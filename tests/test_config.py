"""
tests/test_config.py — Unit tests for config.py validation logic.

Run with:
    python -m pytest tests/
"""
import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


def _minimal_valid_cfg():
    """Return a minimal valid config dict."""
    return {"speakers": ["192.168.1.100"], "volume": 30}


# ---------------------------------------------------------------------------
# Bug 1: timezone validation — zoneinfo unavailable (Python < 3.9)
# ---------------------------------------------------------------------------

class TestValidateConfigTimezone(unittest.TestCase):
    """validate_config handles zoneinfo being unavailable and non-string timezones."""

    def test_validate_config_skips_tz_check_when_zoneinfo_unavailable(self):
        """When config.zoneinfo is None (Python < 3.9), tz check is skipped with no error."""
        cfg = dict(_minimal_valid_cfg(), timezone="America/New_York")
        # Monkeypatch config.zoneinfo to None (simulates Python < 3.9)
        with patch.object(config, "zoneinfo", None):
            # Should not raise, and should not log a warning about the timezone
            with patch.object(config._log, "warning") as mock_warn:
                config.validate_config(cfg)
            # No warning about the timezone should have been issued
            tz_warnings = [
                c for c in mock_warn.call_args_list
                if "timezone" in str(c).lower()
            ]
            self.assertEqual(tz_warnings, [],
                             "No timezone warning when zoneinfo is unavailable")

    def test_validate_config_handles_non_string_timezone(self):
        """validate_config does not raise when 'timezone' is a non-string (e.g. int)."""
        cfg = dict(_minimal_valid_cfg(), timezone=42)
        # Should not raise; should log a warning instead
        try:
            config.validate_config(cfg)
        except Exception as exc:
            self.fail(f"validate_config raised unexpectedly for non-string timezone: {exc}")

    def test_validate_config_valid_timezone_no_warning(self):
        """A valid IANA timezone string produces no warning."""
        cfg = dict(_minimal_valid_cfg(), timezone="America/New_York")
        with patch.object(config._log, "warning") as mock_warn:
            config.validate_config(cfg)
        tz_warnings = [
            c for c in mock_warn.call_args_list
            if "timezone" in str(c).lower()
        ]
        self.assertEqual(tz_warnings, [],
                         "No warning for a valid IANA timezone string")

    def test_validate_config_invalid_timezone_logs_warning(self):
        """An invalid timezone string causes a warning to be logged."""
        cfg = dict(_minimal_valid_cfg(), timezone="Not/AReal/Timezone")
        with patch.object(config._log, "warning") as mock_warn:
            config.validate_config(cfg)
        tz_warnings = [
            c for c in mock_warn.call_args_list
            if "timezone" in str(c).lower()
        ]
        self.assertGreater(len(tz_warnings), 0,
                           "An invalid timezone string should produce a warning")


if __name__ == "__main__":
    unittest.main()
