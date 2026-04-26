"""
tests/test_schedule_sonos.py — Unit tests for schedule_sonos.py timer activation logic.

Run with:
    python -m pytest tests/
  or:
    python -m unittest discover tests/

These tests verify the systemctl call sequence during reschedule and first-install
runs without touching the filesystem or requiring root.
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch, call

# Ensure the repo root is on the path so schedule_sonos can be imported.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_config():
    """Minimal config.json content sufficient for schedule_sonos.main()."""
    return {
        "sonos_ip": "192.168.1.100",
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


# Patches applied to every test to avoid root checks and filesystem side-effects.
_COMMON_PATCHES = {
    "os.getuid": lambda: 0,                             # pretend we're root
    "schedule_sonos._write_unit_file": MagicMock(),     # don't touch /etc/systemd
    "schedule_sonos._clean_stale_units": MagicMock(),   # don't glob the filesystem
    "schedule_sonos.get_sunset_local_time": MagicMock(return_value=(19, 39)),  # sunset at 19:39
}


def _systemctl_calls(mock_run_systemctl):
    """Return the list of (action, unit) tuples from _run_systemctl mock calls."""
    result = []
    for c in mock_run_systemctl.call_args_list:
        args = c.args  # positional args passed to _run_systemctl
        result.append(args)
    return result


# ---------------------------------------------------------------------------
# Reschedule-run tests
# ---------------------------------------------------------------------------

class TestRescheduleRun(unittest.TestCase):
    """
    Verify systemctl call sequence when all timers are already enabled
    (nightly reschedule run).
    """

    def _run_main_reschedule(self):
        """
        Run schedule_sonos.main() with all timers already enabled.
        Returns the list of _run_systemctl call arg-tuples.
        """
        import schedule_sonos

        with patch("os.getuid", return_value=0), \
             patch("schedule_sonos.load_config", return_value=_base_config()), \
             patch("schedule_sonos._write_unit_file"), \
             patch("schedule_sonos._clean_stale_units"), \
             patch("schedule_sonos.get_sunset_local_time", return_value=(19, 39)), \
             patch("schedule_sonos._is_timer_enabled", return_value=True), \
             patch("schedule_sonos._run_systemctl") as mock_ctl:
            schedule_sonos.main()
        return _systemctl_calls(mock_ctl)

    def test_sunset_timer_not_stopped(self):
        """
        During a reschedule run the sunset timer must NOT be stopped — leaving
        it active allows daemon-reload to re-arm it with the new OnCalendar value
        without any spurious immediate fire.
        """
        calls = self._run_main_reschedule()
        self.assertNotIn(("stop", "flag-taps.timer"), calls,
                         "Sunset timer must not be stopped during a reschedule run")

    def test_sunset_timer_not_started(self):
        """
        During a reschedule run the sunset timer must NOT be explicitly started —
        systemctl start on a freshly-reloaded OnCalendar timer can fire immediately
        even with Persistent=false.  daemon-reload re-arms the already-active timer.
        """
        calls = self._run_main_reschedule()
        self.assertNotIn(("start", "flag-taps.timer"), calls,
                         "Sunset timer must not be started during a reschedule run")

    def test_sunset_timer_not_restarted(self):
        """
        During a reschedule run the sunset timer must not receive any restart
        command — the already-active timer is re-armed by daemon-reload alone.
        """
        calls = self._run_main_reschedule()
        self.assertNotIn(("restart", "flag-taps.timer"), calls,
                         "Sunset timer must not be restarted during a reschedule run")

    def test_fixed_time_timer_is_restarted(self):
        """
        Fixed-time timers (flag-colors.timer) must be *restarted* during a
        reschedule run.
        """
        calls = self._run_main_reschedule()
        self.assertIn(("restart", "flag-colors.timer"), calls,
                      "Expected 'systemctl restart flag-colors.timer' during reschedule run")

    def test_reschedule_timer_not_restarted(self):
        """
        flag-reschedule.timer must not be restarted or started during a
        reschedule run (to avoid self-referential catch-up fires).
        """
        calls = self._run_main_reschedule()
        reschedule_activations = [
            c for c in calls
            if len(c) >= 2 and c[-1] == "flag-reschedule.timer"
               and c[0] in ("start", "restart", "enable")
        ]
        self.assertEqual(reschedule_activations, [],
                         "flag-reschedule.timer must not be started/restarted during reschedule run")

    def test_daemon_reload_called(self):
        """daemon-reload must be called during a reschedule run."""
        calls = self._run_main_reschedule()
        self.assertIn(("daemon-reload",), calls,
                      "Expected 'systemctl daemon-reload'")


# ---------------------------------------------------------------------------
# First-install-run tests
# ---------------------------------------------------------------------------

class TestFirstInstallRun(unittest.TestCase):
    """
    Verify systemctl call sequence on first install (timers not yet enabled).
    """

    def _run_main_first_install(self):
        """
        Run schedule_sonos.main() with no timers yet enabled.
        Returns the list of _run_systemctl call arg-tuples.
        """
        import schedule_sonos

        with patch("os.getuid", return_value=0), \
             patch("schedule_sonos.load_config", return_value=_base_config()), \
             patch("schedule_sonos._write_unit_file"), \
             patch("schedule_sonos._clean_stale_units"), \
             patch("schedule_sonos.get_sunset_local_time", return_value=(19, 39)), \
             patch("schedule_sonos._is_timer_enabled", return_value=False), \
             patch("schedule_sonos._run_systemctl") as mock_ctl:
            schedule_sonos.main()
        return _systemctl_calls(mock_ctl)

    def test_all_timers_enabled_with_now(self):
        """
        On first install, every schedule timer and flag-reschedule.timer must
        receive 'systemctl enable --now'.
        """
        calls = self._run_main_first_install()
        self.assertIn(("enable", "--now", "flag-colors.timer"), calls,
                      "Expected 'enable --now flag-colors.timer' on first install")
        self.assertIn(("enable", "--now", "flag-taps.timer"), calls,
                      "Expected 'enable --now flag-taps.timer' on first install")
        self.assertIn(("enable", "--now", "flag-reschedule.timer"), calls,
                      "Expected 'enable --now flag-reschedule.timer' on first install")

    def test_no_start_without_enable_on_first_install(self):
        """
        On first install, bare 'start' calls should not be issued — only
        'enable --now' (which both enables and starts atomically).
        """
        calls = self._run_main_first_install()
        bare_starts = [c for c in calls if c[0] == "start"]
        self.assertEqual(bare_starts, [],
                         "First-install run must not issue bare 'start' calls")

    def test_daemon_reload_called(self):
        """daemon-reload must be called during a first-install run."""
        calls = self._run_main_first_install()
        self.assertIn(("daemon-reload",), calls,
                      "Expected 'systemctl daemon-reload'")


if __name__ == "__main__":
    unittest.main()
