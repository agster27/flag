"""
tests/test_audio_check.py — Unit tests for audio_check.py.

Run with:
    python -m pytest tests/
"""
import sys
import os
import unittest
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Bug 8: convert_to_mp3 cleans up temp file even when os.replace fails
# ---------------------------------------------------------------------------

class TestConvertToMp3TempCleanup(unittest.TestCase):
    """convert_to_mp3 removes the temp file even if os.replace raises."""

    def test_convert_to_mp3_cleans_up_temp_on_replace_failure(self):
        """If os.replace raises, the temp file is still removed via finally."""
        import audio_check

        filepath = "/fake/audio/taps.mp3"
        temp_path = filepath + ".converted.mp3"

        removed_paths = []

        def fake_remove(path):
            removed_paths.append(path)

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run"), \
             patch("os.replace", side_effect=OSError("read-only filesystem")), \
             patch("os.path.exists", return_value=True), \
             patch("os.remove", side_effect=fake_remove):
            result = audio_check.convert_to_mp3(filepath)

        # The function should return False (os.replace failed so conversion is incomplete)
        # but more importantly the temp file must have been cleaned up
        self.assertIn(temp_path, removed_paths,
                      "Temp file must be removed even when os.replace raises")

    def test_convert_to_mp3_succeeds_normally(self):
        """Successful conversion replaces the file and returns True."""
        import audio_check

        filepath = "/fake/audio/taps.mp3"
        temp_path = filepath + ".converted.mp3"

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run"), \
             patch("os.replace"), \
             patch("os.path.exists", return_value=False), \
             patch("os.remove"):
            result = audio_check.convert_to_mp3(filepath)

        self.assertTrue(result, "Successful conversion should return True")

    def test_convert_to_mp3_no_ffmpeg_returns_false(self):
        """Missing ffmpeg returns False without attempting conversion."""
        import audio_check

        with patch("shutil.which", return_value=None):
            result = audio_check.convert_to_mp3("/fake/taps.mp3")

        self.assertFalse(result, "Missing ffmpeg should return False")

    def test_convert_to_mp3_subprocess_failure_cleans_up(self):
        """Temp file is removed on subprocess.CalledProcessError."""
        import subprocess
        import audio_check

        filepath = "/fake/audio/taps.mp3"
        temp_path = filepath + ".converted.mp3"

        removed_paths = []

        def fake_remove(path):
            removed_paths.append(path)

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run",
                   side_effect=subprocess.CalledProcessError(1, "ffmpeg")), \
             patch("os.path.exists", return_value=True), \
             patch("os.remove", side_effect=fake_remove):
            result = audio_check.convert_to_mp3(filepath)

        self.assertFalse(result, "CalledProcessError should return False")
        self.assertIn(temp_path, removed_paths,
                      "Temp file must be removed on CalledProcessError")


if __name__ == "__main__":
    unittest.main()
