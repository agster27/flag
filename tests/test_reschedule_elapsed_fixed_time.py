"""
tests/test_reschedule_elapsed_fixed_time.py — Tests for the elapsed-fixed-time guard.

Verifies that during a reschedule run (all timers already enabled), a fixed-time
timer whose OnCalendar fire time has already elapsed today is NOT restarted.
Restarting an elapsed timer triggers the same systemd misfire bug (PR #43) that
was already fixed for sunset timers — systemd treats the elapsed event as "missed"
and fires the timer immediately even with Persistent=false.

Run with:
    python -m pytest tests/
  or:
    python -m unittest discover tests/
"""
import sys
import os
import unittest
from datetime import time as _time
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_config():
    """Minimal config with one fixed-time timer (colors at 08:00) and one sunset timer."""
    return {
        "speakers": ["192.168.1.100"],
        "volume": 30,
        "city": "TestCity",
        "country": "TC",
        "latitude": 40.7128,
        "longitude": -74.0060,
        "timezone": "America/New_York",
        "schedules": [
            {"name": "colors", "time": "08:00", "audio_url": "http://example.com/colors.mp3"},
            {"name": "taps",   "time": "sunset", "audio_url": "http://example.com/taps.mp3"},
        ],
    }


def _mock_datetime(hour, minute):
    """
    Return a mock that replaces schedule_sonos.datetime so that
    datetime.now(tz).time() returns the given (hour, minute).
    """
    mock_now = MagicMock()
    mock_now.time.return_value = _time(hour, minute)
    mock_dt = MagicMock()
    mock_dt.now.return_value = mock_now
    return mock_dt


def _run_reschedule(mock_dt):
    """
    Run schedule_sonos.main() in reschedule mode (all timers already enabled)
    with the given datetime mock and all unit files considered changed.

    Returns the list of (action, *args) tuples passed to _run_systemctl.
    """
    import schedule_sonos

    with patch("os.getuid", return_value=0), \
         patch("schedule_sonos.load_config", return_value=_base_config()), \
         patch("schedule_sonos._write_unit_file"), \
         patch("schedule_sonos._clean_stale_units", return_value=False), \
         patch("schedule_sonos.get_sunset_local_time", return_value=(19, 39)), \
         patch("schedule_sonos._is_timer_enabled", return_value=True), \
         patch("schedule_sonos._unit_file_content_matches", return_value=False), \
         patch("schedule_sonos.datetime", mock_dt), \
         patch("schedule_sonos._run_systemctl") as mock_ctl:
        schedule_sonos.main()

    return [c.args for c in mock_ctl.call_args_list]


# ---------------------------------------------------------------------------
# Elapsed-fixed-time guard tests
# ---------------------------------------------------------------------------

class TestElapsedFixedTimeGuard(unittest.TestCase):
    """
    Verify that a fixed-time timer whose fire time has already elapsed today
    is NOT restarted during a reschedule run.
    """

    def test_elapsed_fixed_time_timer_not_restarted(self):
        """
        When current time (08:52) is after the fire time (08:00), the fixed-time
        timer must NOT receive 'systemctl restart'.  daemon-reload re-arms the
        active timer for tomorrow automatically.
        """
        # Current time 08:52 — after colors timer fires at 08:00
        calls = _run_reschedule(_mock_datetime(8, 52))
        self.assertNotIn(("restart", "flag-colors.timer"), calls,
                         "Elapsed fixed-time timer must NOT be restarted")

    def test_not_yet_elapsed_fixed_time_timer_is_restarted(self):
        """
        When current time (01:00) is before the fire time (08:00) and the unit
        file changed, the fixed-time timer MUST be restarted.
        """
        # Current time 01:00 — before colors timer fires at 08:00
        calls = _run_reschedule(_mock_datetime(1, 0))
        self.assertIn(("restart", "flag-colors.timer"), calls,
                      "Fixed-time timer must be restarted when fire time is still in future")

    def test_exact_elapsed_time_not_restarted(self):
        """
        When current time equals the fire time exactly (08:00 == 08:00), the
        timer is treated as elapsed and must NOT be restarted.
        """
        calls = _run_reschedule(_mock_datetime(8, 0))
        self.assertNotIn(("restart", "flag-colors.timer"), calls,
                         "Timer at exact fire time must be treated as elapsed, not restarted")

    def test_one_minute_before_fire_time_is_restarted(self):
        """
        When current time (07:59) is one minute before the fire time (08:00),
        the timer is NOT yet elapsed and must be restarted.
        """
        calls = _run_reschedule(_mock_datetime(7, 59))
        self.assertIn(("restart", "flag-colors.timer"), calls,
                      "Timer one minute before fire time must be restarted")

    def test_elapsed_fixed_time_daemon_reload_still_called(self):
        """
        Even when the fixed-time restart is skipped, daemon-reload must still
        be called (to re-arm the active timer with any updated OnCalendar value).
        """
        calls = _run_reschedule(_mock_datetime(8, 52))
        self.assertIn(("daemon-reload",), calls,
                      "daemon-reload must be called even when restart is skipped")


# ---------------------------------------------------------------------------
# Sunset timer behaviour unchanged
# ---------------------------------------------------------------------------

class TestSunsetTimerUnchangedByElapsedGuard(unittest.TestCase):
    """
    Sunset timers must still be left active (no stop/start/restart) regardless
    of whether the current time is before or after their fire time.
    """

    def test_sunset_timer_not_restarted_before_elapsed(self):
        """Sunset timer must not be restarted even when current time is before fire time."""
        calls = _run_reschedule(_mock_datetime(1, 0))  # well before 19:39
        self.assertNotIn(("restart", "flag-taps.timer"), calls,
                         "Sunset timer must not be restarted regardless of current time")

    def test_sunset_timer_not_restarted_after_elapsed(self):
        """Sunset timer must not be restarted even when current time is after fire time."""
        calls = _run_reschedule(_mock_datetime(22, 0))  # after 19:39
        self.assertNotIn(("restart", "flag-taps.timer"), calls,
                         "Sunset timer must not be restarted regardless of current time")

    def test_sunset_timer_not_stopped_after_elapsed(self):
        """Sunset timer must not be stopped even when current time is after fire time."""
        calls = _run_reschedule(_mock_datetime(22, 0))
        self.assertNotIn(("stop", "flag-taps.timer"), calls,
                         "Sunset timer must not be stopped")

    def test_sunset_timer_not_started_after_elapsed(self):
        """Sunset timer must not be started even when current time is after fire time."""
        calls = _run_reschedule(_mock_datetime(22, 0))
        self.assertNotIn(("start", "flag-taps.timer"), calls,
                         "Sunset timer must not be started")


# ---------------------------------------------------------------------------
# Unchanged unit file — no restart regardless of time
# ---------------------------------------------------------------------------

class TestUnchangedUnitNoRestart(unittest.TestCase):
    """
    When the unit file content is unchanged, no restart must happen
    regardless of whether the fire time has elapsed.
    """

    def _run_reschedule_unchanged(self, hour, minute):
        """Run reschedule with unchanged unit files and specified current time."""
        import schedule_sonos

        with patch("os.getuid", return_value=0), \
             patch("schedule_sonos.load_config", return_value=_base_config()), \
             patch("schedule_sonos._write_unit_file"), \
             patch("schedule_sonos._clean_stale_units", return_value=False), \
             patch("schedule_sonos.get_sunset_local_time", return_value=(19, 39)), \
             patch("schedule_sonos._is_timer_enabled", return_value=True), \
             patch("schedule_sonos._unit_file_content_matches", return_value=True), \
             patch("schedule_sonos.datetime", _mock_datetime(hour, minute)), \
             patch("schedule_sonos._run_systemctl") as mock_ctl:
            schedule_sonos.main()

        return [c.args for c in mock_ctl.call_args_list]

    def test_no_restart_when_unchanged_before_fire_time(self):
        """Unchanged unit before fire time — no restart."""
        calls = self._run_reschedule_unchanged(1, 0)
        restart_colors = [c for c in calls if c == ("restart", "flag-colors.timer")]
        self.assertEqual(restart_colors, [],
                         "Unchanged unit must not be restarted even if fire time is in future")

    def test_no_restart_when_unchanged_after_fire_time(self):
        """Unchanged unit after fire time — no restart."""
        calls = self._run_reschedule_unchanged(9, 0)
        restart_colors = [c for c in calls if c == ("restart", "flag-colors.timer")]
        self.assertEqual(restart_colors, [],
                         "Unchanged unit must not be restarted when fire time has elapsed")
