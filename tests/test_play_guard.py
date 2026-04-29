"""
tests/test_play_guard.py — Regression tests for the play-guard in sonos_play.py.

These tests verify that ``check_play_guard`` correctly refuses playback at
unexpected times (e.g. 02:00 AM due to a systemd daemon-reload race) and
permits playback at the configured schedule times.

The 02:00 AM incident (2026-04-29) that prompted these tests:
    - flag-reschedule.timer fired at 02:00, ran schedule_sonos.py.
    - schedule_sonos.py rewrote the sunset timer unit file (19:41 → 19:42)
      and called ``systemctl daemon-reload``.
    - systemd computed NextElapseUSec against the mutated OnCalendar value,
      decided the event was "missed" since LastTriggerUSec, and fired
      flag-evening_colors.service immediately at 02:00.
    - sonos_play.py had no time-of-day guard and played at full volume.

The play guard (``check_play_guard``) prevents this by refusing any play
invocation that is not within ±play_guard_tolerance_minutes of a configured
schedule fire time.

Run with:
    python -m pytest tests/
  or:
    python -m unittest discover tests/
"""
import sys
import os
import unittest
from datetime import datetime
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sonos_play  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _six_schedule_config(sunset_hour=19, sunset_minute=42):
    """
    Return a config dict mirroring the six default schedules.

    The evening_colors schedule uses 'sunset' time; get_sunset_local_time is
    expected to be mocked to return (sunset_hour, sunset_minute).
    """
    return {
        "speakers": [{"ip": "10.0.40.32"}],
        "timezone": "America/New_York",
        "latitude": 40.0,
        "longitude": -74.0,
        "play_guard_enabled": True,
        "play_guard_tolerance_minutes": 2,
        "schedules": [
            {
                "name": "first_call",
                "audio_url": "http://10.0.40.233:8000/first_call.mp3",
                "time": "07:55",
            },
            {
                "name": "morning_colors",
                "audio_url": "http://10.0.40.233:8000/morning_colors.mp3",
                "time": "08:00",
            },
            {
                "name": "carry_on",
                "audio_url": "http://10.0.40.233:8000/carry_on.mp3",
                "time": "08:01",
            },
            {
                "name": "evening-first-call",
                "audio_url": "http://10.0.40.233:8000/first_call.mp3",
                "time": "sunset-5min",
            },
            {
                "name": "evening_colors",
                "audio_url": "http://10.0.40.233:8000/evening_colors.mp3",
                "time": "sunset",
            },
            {
                "name": "taps",
                "audio_url": "http://10.0.40.233:8000/taps.mp3",
                "time": "22:00",
            },
        ],
    }


def _naive_dt(hour, minute, second=26):
    """Return a naive datetime for today at the given time (no tzinfo)."""
    return datetime(2026, 4, 29, hour, minute, second)


# ---------------------------------------------------------------------------
# Core refusal test — the 2026-04-29 02:00 AM incident
# ---------------------------------------------------------------------------

class TestPlayGuardRefusesAtWrongTime(unittest.TestCase):
    """
    Guard must refuse playback at 02:00 AM — the exact time of the incident.

    The config has evening_colors at sunset (19:42) and taps at 22:00.
    None of the schedules fire anywhere near 02:00, so the guard must return
    False.
    """

    def _guard(self, now):
        config = _six_schedule_config(sunset_hour=19, sunset_minute=42)
        with patch("schedule_sonos.get_sunset_local_time",
                   return_value=(19, 42)), \
             patch("schedule_sonos.get_sunset_local_time_with_offset",
                   side_effect=lambda cfg, off: (19, 42 + off)):
            return sonos_play.check_play_guard(config, now=now)

    def test_refuses_at_02_00_26(self):
        """Guard returns False at the exact incident time (02:00:26)."""
        result = self._guard(_naive_dt(2, 0, 26))
        self.assertFalse(
            result,
            "play_guard must refuse playback at 02:00:26 — no schedule fires near then"
        )

    def test_refuses_at_03_00_00(self):
        """Guard returns False at 03:00 (the new static timer fire time)."""
        result = self._guard(_naive_dt(3, 0, 0))
        self.assertFalse(
            result,
            "play_guard must refuse playback at 03:00:00 — no schedule fires there"
        )

    def test_refuses_at_midnight(self):
        """Guard returns False at midnight."""
        result = self._guard(_naive_dt(0, 0, 0))
        self.assertFalse(result, "Guard must refuse at midnight")

    def test_refuses_at_14_00(self):
        """Guard returns False at 14:00 — no schedule fires in the early afternoon."""
        result = self._guard(_naive_dt(14, 0, 0))
        self.assertFalse(result, "Guard must refuse at 14:00")


# ---------------------------------------------------------------------------
# Positive case — exact fire time
# ---------------------------------------------------------------------------

class TestPlayGuardPermitsAtCorrectTime(unittest.TestCase):
    """Guard must permit playback at (or very near) a configured schedule time."""

    def _guard(self, now, sunset_hour=19, sunset_minute=42):
        config = _six_schedule_config(sunset_hour=sunset_hour,
                                      sunset_minute=sunset_minute)
        with patch("schedule_sonos.get_sunset_local_time",
                   return_value=(sunset_hour, sunset_minute)), \
             patch("schedule_sonos.get_sunset_local_time_with_offset",
                   side_effect=lambda cfg, off: (19, sunset_minute + off)):
            return sonos_play.check_play_guard(config, now=now)

    def test_permits_at_sunset_19_42(self):
        """Guard returns True when now == sunset (19:42)."""
        result = self._guard(_naive_dt(19, 42, 0), sunset_minute=42)
        self.assertTrue(result, "Guard must permit play at the exact sunset time")

    def test_permits_at_07_55(self):
        """Guard returns True when now == first_call (07:55)."""
        result = self._guard(_naive_dt(7, 55, 0))
        self.assertTrue(result, "Guard must permit play at 07:55 (first_call)")

    def test_permits_at_08_00(self):
        """Guard returns True when now == morning_colors (08:00)."""
        result = self._guard(_naive_dt(8, 0, 0))
        self.assertTrue(result, "Guard must permit play at 08:00 (morning_colors)")

    def test_permits_at_22_00(self):
        """Guard returns True when now == taps (22:00)."""
        result = self._guard(_naive_dt(22, 0, 0))
        self.assertTrue(result, "Guard must permit play at 22:00 (taps)")


# ---------------------------------------------------------------------------
# Boundary tests — ±tolerance_minutes
# ---------------------------------------------------------------------------

class TestPlayGuardBoundary(unittest.TestCase):
    """
    Guard boundary tests using the default tolerance of ±2 minutes.

    At exactly ±2 min from a scheduled fire time the guard must permit.
    At ±3 min (just outside the tolerance) the guard must refuse.
    """

    def _guard(self, now, tolerance_minutes=2):
        config = _six_schedule_config(sunset_hour=19, sunset_minute=42)
        config["play_guard_tolerance_minutes"] = tolerance_minutes
        with patch("schedule_sonos.get_sunset_local_time",
                   return_value=(19, 42)), \
             patch("schedule_sonos.get_sunset_local_time_with_offset",
                   side_effect=lambda cfg, off: (19, 42 + off)):
            return sonos_play.check_play_guard(config, now=now)

    # -- Fixed-time schedule (taps @ 22:00) --

    def test_permits_exactly_at_tolerance_before(self):
        """now == 21:58 (exactly -2 min from taps 22:00) → permitted."""
        self.assertTrue(
            self._guard(_naive_dt(21, 58, 0)),
            "Guard must permit at exactly -2 min boundary"
        )

    def test_permits_exactly_at_tolerance_after(self):
        """now == 22:02 (exactly +2 min from taps 22:00) → permitted."""
        self.assertTrue(
            self._guard(_naive_dt(22, 2, 0)),
            "Guard must permit at exactly +2 min boundary"
        )

    def test_refuses_just_outside_tolerance_before(self):
        """now == 21:57:59 (just past -2 min from taps 22:00) → refused."""
        # 21:57:59 is 2 min 1 sec before 22:00, outside the ±2 min window
        from datetime import datetime as _dt
        now = _dt(2026, 4, 29, 21, 57, 59)
        self.assertFalse(
            self._guard(now),
            "Guard must refuse at 2 min 1 sec before the scheduled time"
        )

    def test_refuses_just_outside_tolerance_after(self):
        """now == 22:02:01 (just past +2 min from taps 22:00) → refused."""
        from datetime import datetime as _dt
        now = _dt(2026, 4, 29, 22, 2, 1)
        self.assertFalse(
            self._guard(now),
            "Guard must refuse at 2 min 1 sec after the scheduled time"
        )

    # -- Sunset schedule (evening_colors @ 19:42) --

    def test_permits_within_tolerance_of_sunset(self):
        """now == 19:40 (2 min before sunset 19:42) → permitted."""
        self.assertTrue(
            self._guard(_naive_dt(19, 40, 0)),
            "Guard must permit 2 min before the sunset fire time"
        )

    def test_permits_within_tolerance_of_sunset_after(self):
        """now == 19:44 (2 min after sunset 19:42) → permitted."""
        self.assertTrue(
            self._guard(_naive_dt(19, 44, 0)),
            "Guard must permit 2 min after the sunset fire time"
        )

    def test_refuses_outside_tolerance_of_sunset(self):
        """now == 19:45:01 (3 min 1 sec after sunset 19:42) → refused."""
        from datetime import datetime as _dt
        now = _dt(2026, 4, 29, 19, 45, 1)
        self.assertFalse(
            self._guard(now),
            "Guard must refuse more than 2 min after the sunset fire time"
        )

    def test_custom_tolerance_5_min(self):
        """A custom tolerance of 5 min is respected."""
        # 21:54 is 6 min before taps 22:00 — outside 5-min window
        self.assertFalse(
            self._guard(_naive_dt(21, 54, 0), tolerance_minutes=5),
            "6 min before schedule should be refused with 5-min tolerance"
        )
        # 21:55 is 5 min before taps 22:00 — exactly at the boundary
        self.assertTrue(
            self._guard(_naive_dt(21, 55, 0), tolerance_minutes=5),
            "5 min before schedule should be permitted with 5-min tolerance"
        )


# ---------------------------------------------------------------------------
# Opt-out / bypass tests
# ---------------------------------------------------------------------------

class TestPlayGuardOptOut(unittest.TestCase):
    """Guard must be completely bypassed when opted out via config keys."""

    def _guard_at_midnight(self, extra_config):
        config = _six_schedule_config()
        config.update(extra_config)
        with patch("schedule_sonos.get_sunset_local_time", return_value=(19, 42)):
            return sonos_play.check_play_guard(config, now=_naive_dt(0, 0, 0))

    def test_play_guard_enabled_false_bypasses_check(self):
        """play_guard_enabled=false → guard always returns True."""
        result = self._guard_at_midnight({"play_guard_enabled": False})
        self.assertTrue(
            result,
            "play_guard_enabled=false must bypass the guard entirely"
        )

    def test_allow_quiet_hours_play_true_bypasses_check(self):
        """allow_quiet_hours_play=true (legacy) → guard always returns True."""
        result = self._guard_at_midnight({"allow_quiet_hours_play": True})
        self.assertTrue(
            result,
            "allow_quiet_hours_play=true must bypass the guard (legacy compat)"
        )

    def test_empty_schedules_bypasses_check(self):
        """No schedules configured → guard allows (cannot determine expected time)."""
        config = {
            "timezone": "America/New_York",
            "play_guard_enabled": True,
            "schedules": [],
        }
        result = sonos_play.check_play_guard(config, now=_naive_dt(2, 0, 26))
        self.assertTrue(
            result,
            "Guard must allow when no schedules are configured (no reference time)"
        )

    def test_missing_play_guard_enabled_defaults_to_enabled(self):
        """When play_guard_enabled is absent, guard defaults to enabled (True)."""
        config = _six_schedule_config()
        del config["play_guard_enabled"]
        # At 02:00 with guard enabled (default), guard must refuse
        with patch("schedule_sonos.get_sunset_local_time", return_value=(19, 42)), \
             patch("schedule_sonos.get_sunset_local_time_with_offset",
                   side_effect=lambda cfg, off: (19, 42 + off)):
            result = sonos_play.check_play_guard(config, now=_naive_dt(2, 0, 26))
        self.assertFalse(
            result,
            "Guard must be enabled by default when play_guard_enabled is absent"
        )


# ---------------------------------------------------------------------------
# main() integration — guard causes sys.exit(1) on misfire
# ---------------------------------------------------------------------------

class TestMainExitsOnGuardFailure(unittest.TestCase):
    """
    When check_play_guard returns False and --ignore-guard is NOT passed,
    main() must log an error and exit non-zero without connecting to any speaker.
    """

    def test_main_exits_nonzero_on_misfire(self):
        """main() calls sys.exit(1) when guard refuses play."""
        config = _six_schedule_config()
        with patch("sonos_play.load_config", return_value=config), \
             patch("sonos_play.check_play_guard", return_value=False), \
             patch("sonos_play.log"), \
             patch("sys.argv", ["sonos_play.py",
                                "http://10.0.40.233:8000/evening_colors.mp3"]):
            with self.assertRaises(SystemExit) as ctx:
                sonos_play.main()
        self.assertEqual(ctx.exception.code, 1,
                         "main() must exit with code 1 when guard refuses")

    def test_main_skips_guard_with_ignore_guard_flag(self):
        """--ignore-guard bypasses the guard check in main()."""
        config = {
            "speakers": [{"ip": "10.0.40.32"}],
            "volume": 30,
            "skip_restore_if_idle": True,
            "default_wait_seconds": 60,
            "play_guard_enabled": True,
            "schedules": [],
            "timezone": "America/New_York",
        }
        # Even if check_play_guard would return False, --ignore-guard skips it.
        # We patch soco.SoCo to exit early (no speakers) to keep the test short.
        with patch("sonos_play.load_config", return_value=config), \
             patch("sonos_play.soco.SoCo", side_effect=Exception("no speaker")), \
             patch("sonos_play.log"), \
             patch("sys.argv", ["sonos_play.py", "--ignore-guard",
                                "http://10.0.40.233:8000/evening_colors.mp3"]):
            # Will exit non-zero because no speakers are reachable, but NOT
            # because of the guard.  We just need to confirm guard is not called.
            with patch("sonos_play.check_play_guard") as mock_guard:
                try:
                    sonos_play.main()
                except SystemExit:
                    pass
            mock_guard.assert_not_called()


if __name__ == "__main__":
    unittest.main()
