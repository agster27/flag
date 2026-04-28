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


# ---------------------------------------------------------------------------
# P1 optimisation tests: skip no-op restarts when unit files unchanged
# ---------------------------------------------------------------------------

class TestRescheduleOptimization(unittest.TestCase):
    """
    Verify that the reschedule run skips ``systemctl restart`` when the timer
    unit file content has not changed (P1 defence-in-depth fix).
    """

    def _run_reschedule(self, unit_file_matches):
        """
        Run schedule_sonos.main() in reschedule mode.

        Args:
            unit_file_matches (bool): Return value for
                ``_unit_file_content_matches``.  True → files are unchanged →
                no restart.  False → files differ → restart required.

        Returns:
            list of _run_systemctl call arg-tuples.
        """
        import schedule_sonos

        with patch("os.getuid", return_value=0), \
             patch("schedule_sonos.load_config", return_value=_base_config()), \
             patch("schedule_sonos._write_unit_file"), \
             patch("schedule_sonos._clean_stale_units", return_value=False), \
             patch("schedule_sonos.get_sunset_local_time", return_value=(19, 39)), \
             patch("schedule_sonos._is_timer_enabled", return_value=True), \
             patch("schedule_sonos._unit_file_content_matches",
                   return_value=unit_file_matches), \
             patch("schedule_sonos._run_systemctl") as mock_ctl:
            schedule_sonos.main()
        return _systemctl_calls(mock_ctl)

    def test_reschedule_no_restart_when_unchanged(self):
        """
        When the on-disk unit file already matches the newly computed content,
        ``systemctl restart flag-colors.timer`` must NOT be called.

        This is the core guard: the 02:00 reschedule run must not restart a
        Persistent=true timer when nothing changed, to prevent spurious
        catch-up fires.
        """
        calls = self._run_reschedule(unit_file_matches=True)
        restart_colors = [c for c in calls if c == ("restart", "flag-colors.timer")]
        self.assertEqual(restart_colors, [],
                         "Expected NO 'systemctl restart flag-colors.timer' "
                         "when unit file content is unchanged")

    def test_reschedule_restarts_when_changed(self):
        """
        When the unit file content differs from what is on disk (e.g. the
        colors time changed from 08:00 to 09:00), ``systemctl restart
        flag-colors.timer`` must be called exactly once.
        """
        calls = self._run_reschedule(unit_file_matches=False)
        restart_colors = [c for c in calls if c == ("restart", "flag-colors.timer")]
        self.assertEqual(len(restart_colors), 1,
                         "Expected exactly one 'systemctl restart flag-colors.timer' "
                         "when unit file content changed")

    def test_reschedule_never_restarts_reschedule_timer(self):
        """
        Regression guard: ``systemctl restart flag-reschedule.timer`` must
        NEVER be called in any reschedule scenario, regardless of whether unit
        files changed or not.  Self-restarting the parent timer
        can cause systemd to treat the just-elapsed 02:00 event as missed.
        """
        for unit_file_matches in (True, False):
            with self.subTest(unit_file_matches=unit_file_matches):
                calls = self._run_reschedule(unit_file_matches=unit_file_matches)
                reschedule_activations = [
                    c for c in calls
                    if len(c) >= 2 and c[-1] == "flag-reschedule.timer"
                       and c[0] in ("start", "restart", "enable")
                ]
                self.assertEqual(reschedule_activations, [],
                                 "flag-reschedule.timer must not be started/restarted "
                                 "during a reschedule run")


# ---------------------------------------------------------------------------
# Persistent=false regression tests
# ---------------------------------------------------------------------------

class TestPersistentFalse(unittest.TestCase):
    """
    Verify that every generated timer unit contains ``Persistent=false``.

    This is the primary regression guard ensuring that no timer can ever
    replay a missed fire after a reboot or outage.
    """

    def test_schedule_timer_persistent_false(self):
        """Fixed-time schedule timer must contain Persistent=false."""
        import schedule_sonos
        content = schedule_sonos._build_timer_unit("colors", 8, 0)
        self.assertIn("Persistent=false", content,
                      "Fixed-time timer must contain Persistent=false")
        self.assertNotIn("Persistent=true", content,
                         "Fixed-time timer must not contain Persistent=true")

    def test_sunset_timer_persistent_false(self):
        """Sunset-based schedule timer must contain Persistent=false."""
        import schedule_sonos
        content = schedule_sonos._build_timer_unit("taps", 19, 30)
        self.assertIn("Persistent=false", content,
                      "Sunset timer must contain Persistent=false")
        self.assertNotIn("Persistent=true", content,
                         "Sunset timer must not contain Persistent=true")

    def test_reschedule_timer_persistent_false(self):
        """flag-reschedule.timer must contain Persistent=false."""
        import schedule_sonos
        content = schedule_sonos._build_reschedule_timer()
        self.assertIn("Persistent=false", content,
                      "Reschedule timer must contain Persistent=false")
        self.assertNotIn("Persistent=true", content,
                         "Reschedule timer must not contain Persistent=true")

    def test_build_timer_unit_no_persistent_param(self):
        """_build_timer_unit must not accept a 'persistent' keyword argument."""
        import schedule_sonos
        import inspect
        sig = inspect.signature(schedule_sonos._build_timer_unit)
        self.assertNotIn("persistent", sig.parameters,
                         "_build_timer_unit must not expose a 'persistent' parameter")


# ---------------------------------------------------------------------------
# Boot-reschedule service tests
# ---------------------------------------------------------------------------

class TestBootRescheduleService(unittest.TestCase):
    """
    Verify that flag-boot-reschedule.service is generated and written correctly.
    """

    def test_boot_reschedule_service_content(self):
        """Builder returns a valid oneshot unit targeting multi-user.target."""
        import schedule_sonos
        content = schedule_sonos._build_boot_reschedule_service(["colors", "taps"])
        self.assertIn("[Unit]", content)
        self.assertIn("[Service]", content)
        self.assertIn("[Install]", content)
        self.assertIn("Type=oneshot", content)
        self.assertIn("WantedBy=multi-user.target", content)
        self.assertIn("network-online.target", content)
        self.assertIn("Before=flag-colors.timer flag-taps.timer", content)

    def test_boot_reschedule_service_no_before_when_empty(self):
        """Builder omits Before= when schedule_names is empty or None."""
        import schedule_sonos
        for arg in (None, []):
            content = schedule_sonos._build_boot_reschedule_service(arg)
            self.assertNotIn("Before=", content,
                             f"Before= must be absent when schedule_names={arg!r}")

    def test_boot_reschedule_service_written_on_first_install(self):
        """flag-boot-reschedule.service must be written during a first-install run."""
        import schedule_sonos

        with patch("os.getuid", return_value=0), \
             patch("schedule_sonos.load_config", return_value=_base_config()), \
             patch("schedule_sonos._write_unit_file") as mock_write, \
             patch("schedule_sonos._clean_stale_units"), \
             patch("schedule_sonos.get_sunset_local_time", return_value=(19, 39)), \
             patch("schedule_sonos._is_timer_enabled", return_value=False), \
             patch("schedule_sonos._run_systemctl"):
            schedule_sonos.main()

        written_paths = [c.args[0] for c in mock_write.call_args_list]
        boot_reschedule_path = "/etc/systemd/system/flag-boot-reschedule.service"
        self.assertIn(boot_reschedule_path, written_paths,
                      "flag-boot-reschedule.service must be written on first install")

    def test_boot_reschedule_service_written_on_reschedule(self):
        """flag-boot-reschedule.service must be written during a reschedule run."""
        import schedule_sonos

        with patch("os.getuid", return_value=0), \
             patch("schedule_sonos.load_config", return_value=_base_config()), \
             patch("schedule_sonos._write_unit_file") as mock_write, \
             patch("schedule_sonos._clean_stale_units"), \
             patch("schedule_sonos.get_sunset_local_time", return_value=(19, 39)), \
             patch("schedule_sonos._is_timer_enabled", return_value=True), \
             patch("schedule_sonos._run_systemctl"):
            schedule_sonos.main()

        written_paths = [c.args[0] for c in mock_write.call_args_list]
        boot_reschedule_path = "/etc/systemd/system/flag-boot-reschedule.service"
        self.assertIn(boot_reschedule_path, written_paths,
                      "flag-boot-reschedule.service must be written on reschedule run")

    def test_boot_reschedule_service_enabled_no_now_on_first_install(self):
        """
        On first install, flag-boot-reschedule.service must be enabled without
        --now (it is a boot-time oneshot, not something to run immediately).
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

        calls = _systemctl_calls(mock_ctl)
        # Must be enabled without --now
        self.assertIn(("enable", "flag-boot-reschedule.service"), calls,
                      "flag-boot-reschedule.service must be enabled on first install")
        # Must NOT be started with --now
        self.assertNotIn(("enable", "--now", "flag-boot-reschedule.service"), calls,
                         "flag-boot-reschedule.service must not be started immediately")

    def test_boot_reschedule_not_enabled_on_reschedule(self):
        """
        During a reschedule run, flag-boot-reschedule.service must not be
        re-enabled (it is already enabled from the first-install run).
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

        calls = _systemctl_calls(mock_ctl)
        boot_enables = [
            c for c in calls
            if "flag-boot-reschedule.service" in c and c[0] == "enable"
        ]
        self.assertEqual(boot_enables, [],
                         "flag-boot-reschedule.service must not be re-enabled "
                         "during a reschedule run")


# ---------------------------------------------------------------------------
# parse_sunset_offset tests
# ---------------------------------------------------------------------------

class TestParseSunsetOffset(unittest.TestCase):
    """Unit tests for the parse_sunset_offset() helper."""

    def setUp(self):
        import schedule_sonos
        self.parse = schedule_sonos.parse_sunset_offset

    def test_negative_offset(self):
        """'sunset-5min' returns -5."""
        self.assertEqual(self.parse("sunset-5min"), -5)

    def test_positive_offset(self):
        """'sunset+1min' returns 1 (also validates the minimum accepted value)."""
        self.assertEqual(self.parse("sunset+1min"), 1)

    def test_min_negative_offset(self):
        """'sunset-1min' returns -1 (minimum accepted negative offset)."""
        self.assertEqual(self.parse("sunset-1min"), -1)

    def test_max_offset(self):
        """'sunset+720min' returns 720 (upper boundary)."""
        self.assertEqual(self.parse("sunset+720min"), 720)

    def test_plain_sunset_returns_none(self):
        """'sunset' (no offset) returns None."""
        self.assertIsNone(self.parse("sunset"))

    def test_hhmm_returns_none(self):
        """'08:00' (fixed time) returns None."""
        self.assertIsNone(self.parse("08:00"))

    def test_zero_offset_raises(self):
        """'sunset+0min' is rejected with ValueError (use plain 'sunset' instead)."""
        with self.assertRaises(ValueError):
            self.parse("sunset+0min")

    def test_out_of_range_raises(self):
        """'sunset+1000min' is rejected with ValueError (exceeds 720-minute maximum)."""
        with self.assertRaises(ValueError):
            self.parse("sunset+1000min")

    def test_non_numeric_returns_none(self):
        """'sunset-abcmin' does not match the regex and returns None."""
        self.assertIsNone(self.parse("sunset-abcmin"))

    # --- #5 edge-case additions ---

    def test_leading_zero_returns_negative_5(self):
        """'sunset-05min' returns -5 (leading-zero N)."""
        self.assertEqual(self.parse("sunset-05min"), -5)

    def test_multiple_leading_zeros_returns_negative_5(self):
        """'sunset-0005min' returns -5 (multiple leading zeros)."""
        self.assertEqual(self.parse("sunset-0005min"), -5)

    def test_no_digits_plus_returns_none(self):
        """'sunset+min' (no digits) does not match and returns None."""
        self.assertIsNone(self.parse("sunset+min"))

    def test_no_digits_minus_returns_none(self):
        """'sunset-min' (no digits) does not match and returns None."""
        self.assertIsNone(self.parse("sunset-min"))

    def test_empty_string_returns_none(self):
        """'' (empty string) does not match and returns None."""
        self.assertIsNone(self.parse(""))

    # --- #6 case-insensitive additions ---

    def test_mixed_case_negative_offset(self):
        """'Sunset-5min' returns -5 (case-insensitive matching)."""
        self.assertEqual(self.parse("Sunset-5min"), -5)

    def test_all_caps_positive_offset(self):
        """'SUNSET+1MIN' returns 1 (fully uppercase)."""
        self.assertEqual(self.parse("SUNSET+1MIN"), 1)


# ---------------------------------------------------------------------------
# Sunset-offset integration tests
# ---------------------------------------------------------------------------

class TestSunsetOffsetIntegration(unittest.TestCase):
    """
    Verify that sunset-offset schedule entries are handled correctly by main():
    - Treated as sunset-based (no stop/restart on reschedule runs).
    - Resolved using get_sunset_local_time_with_offset.
    """

    def _config_with_sunset_offset(self):
        """Config with one sunset-offset entry and one fixed-time entry."""
        return {
            "speakers": ["192.168.1.100"],
            "volume": 30,
            "city": "TestCity",
            "country": "TC",
            "latitude": 40.7128,
            "longitude": -74.0060,
            "timezone": "America/New_York",
            "schedules": [
                {
                    "name": "evening-first-call",
                    "time": "sunset-5min",
                    "audio_url": "http://example.com/first_call.mp3",
                },
                {
                    "name": "morning-colors",
                    "time": "08:00",
                    "audio_url": "http://example.com/morning_colors.mp3",
                },
            ],
        }

    def test_sunset_offset_timer_not_stopped_on_reschedule(self):
        """sunset-offset timer must not be stopped during a reschedule run."""
        import schedule_sonos

        with patch("os.getuid", return_value=0), \
             patch("schedule_sonos.load_config",
                   return_value=self._config_with_sunset_offset()), \
             patch("schedule_sonos._write_unit_file"), \
             patch("schedule_sonos._clean_stale_units"), \
             patch("schedule_sonos.get_sunset_local_time", return_value=(19, 39)), \
             patch("schedule_sonos.get_sunset_local_time_with_offset",
                   return_value=(19, 34)), \
             patch("schedule_sonos._is_timer_enabled", return_value=True), \
             patch("schedule_sonos._run_systemctl") as mock_ctl:
            schedule_sonos.main()

        calls = [c.args for c in mock_ctl.call_args_list]
        self.assertNotIn(("stop", "flag-evening-first-call.timer"), calls,
                         "Sunset-offset timer must not be stopped during reschedule")

    def test_sunset_offset_timer_not_started_on_reschedule(self):
        """sunset-offset timer must not be started during a reschedule run."""
        import schedule_sonos

        with patch("os.getuid", return_value=0), \
             patch("schedule_sonos.load_config",
                   return_value=self._config_with_sunset_offset()), \
             patch("schedule_sonos._write_unit_file"), \
             patch("schedule_sonos._clean_stale_units"), \
             patch("schedule_sonos.get_sunset_local_time", return_value=(19, 39)), \
             patch("schedule_sonos.get_sunset_local_time_with_offset",
                   return_value=(19, 34)), \
             patch("schedule_sonos._is_timer_enabled", return_value=True), \
             patch("schedule_sonos._run_systemctl") as mock_ctl:
            schedule_sonos.main()

        calls = [c.args for c in mock_ctl.call_args_list]
        self.assertNotIn(("start", "flag-evening-first-call.timer"), calls,
                         "Sunset-offset timer must not be started during reschedule")

    def test_sunset_offset_calls_with_offset_helper(self):
        """main() calls get_sunset_local_time_with_offset for sunset-offset entries."""
        import schedule_sonos

        with patch("os.getuid", return_value=0), \
             patch("schedule_sonos.load_config",
                   return_value=self._config_with_sunset_offset()), \
             patch("schedule_sonos._write_unit_file"), \
             patch("schedule_sonos._clean_stale_units"), \
             patch("schedule_sonos.get_sunset_local_time", return_value=(19, 39)), \
             patch("schedule_sonos.get_sunset_local_time_with_offset",
                   return_value=(19, 34)) as mock_offset, \
             patch("schedule_sonos._is_timer_enabled", return_value=False), \
             patch("schedule_sonos._run_systemctl"):
            schedule_sonos.main()

        mock_offset.assert_called_once()
        _, called_offset = mock_offset.call_args.args
        self.assertEqual(called_offset, -5,
                         "Expected offset of -5 for 'sunset-5min'")

    def _config_with_time(self, time_value):
        """Config with a single entry using the given time value."""
        return {
            "speakers": ["192.168.1.100"],
            "volume": 30,
            "city": "TestCity",
            "country": "TC",
            "latitude": 40.7128,
            "longitude": -74.0060,
            "timezone": "America/New_York",
            "schedules": [
                {
                    "name": "test-entry",
                    "time": time_value,
                    "audio_url": "http://example.com/test.mp3",
                },
            ],
        }

    def test_mixed_case_sunset_goes_through_sunset_branch(self):
        """A config entry with time='Sunset' is treated as sunset-based (#6)."""
        import schedule_sonos

        with patch("os.getuid", return_value=0), \
             patch("schedule_sonos.load_config",
                   return_value=self._config_with_time("Sunset")), \
             patch("schedule_sonos._write_unit_file"), \
             patch("schedule_sonos._clean_stale_units"), \
             patch("schedule_sonos.get_sunset_local_time",
                   return_value=(19, 39)) as mock_sunset, \
             patch("schedule_sonos._is_timer_enabled", return_value=False), \
             patch("schedule_sonos._run_systemctl"):
            schedule_sonos.main()

        mock_sunset.assert_called_once()

    def test_whitespace_sunset_goes_through_sunset_branch(self):
        """A config entry with time=' sunset ' (whitespace) is treated as sunset-based (#7)."""
        import schedule_sonos

        with patch("os.getuid", return_value=0), \
             patch("schedule_sonos.load_config",
                   return_value=self._config_with_time(" sunset ")), \
             patch("schedule_sonos._write_unit_file"), \
             patch("schedule_sonos._clean_stale_units"), \
             patch("schedule_sonos.get_sunset_local_time",
                   return_value=(19, 39)) as mock_sunset, \
             patch("schedule_sonos._is_timer_enabled", return_value=False), \
             patch("schedule_sonos._run_systemctl"):
            schedule_sonos.main()

        mock_sunset.assert_called_once()


# ---------------------------------------------------------------------------
# Tests for stacked-offset decoupling and midnight-wrap guard (Bugs #1 & #2)
# ---------------------------------------------------------------------------

class TestSunsetOffsetDecouplingAndWrap(unittest.TestCase):
    """
    Verify Bug #1 fix: per-entry offset is absolute (config sunset_offset_minutes
    is ignored), and Bug #2 fix: midnight wrap raises ValueError.
    """

    _BASE_CONFIG = {
        "speakers": ["192.168.1.100"],
        "volume": 30,
        "city": "TestCity",
        "country": "TC",
        "latitude": 40.7128,
        "longitude": -74.0060,
        "timezone": "America/New_York",
        "sunset_offset_minutes": 30,
        "schedules": [],
    }

    def _make_sun_return(self, hour, minute, tz_name="America/New_York"):
        """
        Return a dict whose ``"sunset"`` key is a timezone-aware datetime
        for today at the given hour:minute in tz_name.
        """
        import pytz
        from datetime import date, datetime as dt
        tz = pytz.timezone(tz_name)
        today = date.today()
        naive = dt(today.year, today.month, today.day, hour, minute, 0)
        aware = tz.localize(naive, is_dst=False)
        return {"sunset": aware}

    def test_stacked_offset_decoupled(self):
        """
        get_sunset_local_time_with_offset must use ONLY extra_offset_minutes.
        With sunset_offset_minutes=30 and extra_offset_minutes=-5 the result
        must be sunset-5min (18:55 if sunset is 19:00), NOT sunset+25min.
        """
        import schedule_sonos

        sun_data = self._make_sun_return(19, 0)
        with patch("schedule_sonos.sun", return_value=sun_data):
            h, m = schedule_sonos.get_sunset_local_time_with_offset(
                self._BASE_CONFIG, -5
            )
        self.assertEqual((h, m), (18, 55),
                         "Per-entry offset must be absolute, not stacked with config offset")

    def test_forward_wrap_raises(self):
        """
        get_sunset_local_time_with_offset must raise ValueError when offset
        pushes the result past midnight into the next day.
        """
        import schedule_sonos

        # Sunset at 23:50 + 30 minutes crosses midnight
        sun_data = self._make_sun_return(23, 50)
        with patch("schedule_sonos.sun", return_value=sun_data):
            with self.assertRaises(ValueError) as ctx:
                schedule_sonos.get_sunset_local_time_with_offset(
                    self._BASE_CONFIG, 30
                )
        self.assertIn("crosses midnight", str(ctx.exception))

    def test_backward_wrap_raises(self):
        """
        get_sunset_local_time_with_offset must raise ValueError when a large
        negative offset wraps back to the previous day (high-latitude winter).
        """
        import schedule_sonos

        # Sunset at 00:30 − 60 minutes crosses back into the previous day
        sun_data = self._make_sun_return(0, 30)
        with patch("schedule_sonos.sun", return_value=sun_data):
            with self.assertRaises(ValueError) as ctx:
                schedule_sonos.get_sunset_local_time_with_offset(
                    self._BASE_CONFIG, -60
                )
        self.assertIn("crosses midnight", str(ctx.exception))

    def test_plain_sunset_wrap_raises(self):
        """
        get_sunset_local_time must raise ValueError when config
        sunset_offset_minutes causes a midnight wrap.
        """
        import schedule_sonos

        config = dict(self._BASE_CONFIG, sunset_offset_minutes=30)
        # Sunset at 23:50 + 30 min config offset crosses midnight
        sun_data = self._make_sun_return(23, 50)
        with patch("schedule_sonos.sun", return_value=sun_data):
            with self.assertRaises(ValueError) as ctx:
                schedule_sonos.get_sunset_local_time(config)
        self.assertIn("crosses midnight", str(ctx.exception))

    def test_main_skips_wrap_entry_processes_others(self):
        """
        main() must skip a schedule entry whose sunset offset crosses midnight
        (ValueError from get_sunset_local_time_with_offset) while still
        processing other valid entries.
        """
        import schedule_sonos

        config = {
            "speakers": ["192.168.1.100"],
            "volume": 30,
            "city": "TestCity",
            "country": "TC",
            "latitude": 40.7128,
            "longitude": -74.0060,
            "timezone": "America/New_York",
            "schedules": [
                {
                    "name": "wrap-entry",
                    "time": "sunset+700min",
                    "audio_url": "http://example.com/wrap.mp3",
                },
                {
                    "name": "fixed-entry",
                    "time": "08:00",
                    "audio_url": "http://example.com/fixed.mp3",
                },
            ],
        }

        def _offset_raises(cfg, offset):
            raise ValueError("Sunset offset crosses midnight in America/New_York: ...")

        with patch("os.getuid", return_value=0), \
             patch("schedule_sonos.load_config", return_value=config), \
             patch("schedule_sonos._write_unit_file"), \
             patch("schedule_sonos._clean_stale_units"), \
             patch("schedule_sonos.get_sunset_local_time_with_offset",
                   side_effect=_offset_raises), \
             patch("schedule_sonos._is_timer_enabled", return_value=False), \
             patch("schedule_sonos._run_systemctl") as mock_ctl:
            schedule_sonos.main()

        # _run_systemctl("enable", "--now", timer_name) — last arg is the unit name
        enabled_units = [c.args[-1] for c in mock_ctl.call_args_list
                         if c.args[0] == "enable"]
        self.assertNotIn("flag-wrap-entry.timer", enabled_units,
                         "Wrap entry must be skipped (not enabled)")
        self.assertIn("flag-fixed-entry.timer", enabled_units,
                      "Valid fixed-time entry must still be processed")


# ---------------------------------------------------------------------------
# Bug 5: _build_service_unit escapes audio_url with shlex.quote
# ---------------------------------------------------------------------------

class TestBuildServiceUnitEscapesAudioUrl(unittest.TestCase):
    """_build_service_unit uses shlex.quote so special characters in URLs are safe."""

    def test_build_service_unit_escapes_audio_url_with_quote(self):
        """URL containing double-quotes is shell-safe after shlex.quote."""
        import shlex
        import schedule_sonos

        url_with_quotes = 'http://example.com/taps.mp3?a="b"'
        content = schedule_sonos._build_service_unit("taps", url_with_quotes)

        # Extract the ExecStart line
        exec_line = [ln for ln in content.splitlines() if ln.startswith("ExecStart=")][0]
        exec_value = exec_line[len("ExecStart="):]

        # shlex.split must not raise and must include the URL as one token
        tokens = shlex.split(exec_value)
        self.assertIn(url_with_quotes, tokens,
                      "The unescaped URL must appear as a single token after shlex.split")

    def test_build_service_unit_normal_url_still_parseable(self):
        """A normal URL is still correctly round-tripped through shlex.split."""
        import shlex
        import schedule_sonos

        url = "http://example.com/colors.mp3"
        content = schedule_sonos._build_service_unit("colors", url)

        exec_line = [ln for ln in content.splitlines() if ln.startswith("ExecStart=")][0]
        exec_value = exec_line[len("ExecStart="):]
        tokens = shlex.split(exec_value)
        self.assertIn(url, tokens, "Plain URL must appear as a single token")


# ---------------------------------------------------------------------------
# Bug 6: resolve_schedules rejects non-list / null schedules
# ---------------------------------------------------------------------------

class TestResolveSchedulesValidation(unittest.TestCase):
    """resolve_schedules returns [] when 'schedules' is present but not a non-empty list."""

    def _resolve(self, schedules_value):
        import schedule_sonos
        return schedule_sonos.resolve_schedules({"schedules": schedules_value})

    def test_resolve_schedules_rejects_non_list_schedules(self):
        """'schedules' that is a string (not a list) returns []."""
        result = self._resolve("not-a-list")
        self.assertEqual(result, [],
                          "Non-list schedules value must return empty list")

    def test_resolve_schedules_rejects_null_schedules(self):
        """'schedules' that is None (null) returns []."""
        result = self._resolve(None)
        self.assertEqual(result, [],
                          "Null schedules value must return empty list")

    def test_resolve_schedules_rejects_empty_list(self):
        """'schedules' that is an empty list returns []."""
        result = self._resolve([])
        self.assertEqual(result, [],
                          "Empty schedules list must return empty list")

    def test_resolve_schedules_accepts_valid_list(self):
        """'schedules' that is a non-empty list is returned as-is."""
        entry = {"name": "taps", "time": "sunset", "audio_url": "http://example.com/t.mp3"}
        result = self._resolve([entry])
        self.assertEqual(result, [entry],
                          "Valid non-empty schedules list must be returned unchanged")

    def test_resolve_schedules_rejects_dict_schedules(self):
        """'schedules' that is a dict (not a list) returns []."""
        result = self._resolve({"name": "taps"})
        self.assertEqual(result, [],
                          "Dict schedules value must return empty list")
