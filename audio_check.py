#!/usr/bin/env python3
"""
audio_check.py — Validates and converts audio files for Sonos compatibility.

Checks that MP3 files in the audio directory meet Sonos requirements
(stereo, 44.1 kHz or 48 kHz). Incompatible files are automatically
converted using ffmpeg.

.. warning::
    Conversion is performed **in place** — the original file is replaced with
    no backup.  Ensure you have a copy elsewhere before running this script if
    the originals are irreplaceable.
"""

import logging
import os
import shutil
import subprocess
from mutagen.mp3 import MP3
from mutagen import MutagenError
from config import AUDIO_DIR, LOG_FILE

_log = logging.getLogger(__name__)

# Sonos supports 44.1 kHz and 48 kHz sample rates for reliable MP3 playback
VALID_SAMPLE_RATES = [44100, 48000]
# Sonos requires stereo (2-channel) audio for reliable playback; mono files will be converted
VALID_CHANNELS = 2
VALID_EXTENSION = ".mp3"

def _output(msg):
    """Print a message and also write it to the log file."""
    print(msg)
    _log.info(msg)

def is_valid_mp3(filepath):
    """
    Check whether an MP3 file meets Sonos compatibility requirements.

    Args:
        filepath (str): Path to the MP3 file to check.

    Returns:
        bool: True if the file has a valid sample rate and stereo channels, False otherwise.
    """
    try:
        audio = MP3(filepath)
        return audio.info.sample_rate in VALID_SAMPLE_RATES and audio.info.channels == VALID_CHANNELS
    except MutagenError:
        return False

def convert_to_mp3(filepath):
    """
    Convert an audio file to a Sonos-compatible stereo MP3 at 44.1 kHz.

    **The original file is replaced in place** — there is no backup.  The
    converted output is written to a temporary path first and then atomically
    renamed over the original.

    Args:
        filepath (str): Path to the file to convert.

    Returns:
        bool: True if conversion succeeded, False otherwise.
    """
    # Issue 8: Check that ffmpeg is available before attempting conversion
    if shutil.which("ffmpeg") is None:
        msg = "❌ ffmpeg is not installed or not on PATH. Cannot convert audio files."
        print(msg)
        _log.error(msg)
        return False

    temp_path = filepath + ".converted.mp3"
    try:
        _output(f"🔄 Converting {filepath} to a Sonos-compatible MP3...")
        subprocess.run([
            "ffmpeg", "-y", "-i", filepath,
            "-ar", "44100", "-ac", "2", "-codec:a", "libmp3lame", temp_path
        ], check=True)
        try:
            os.replace(temp_path, filepath)
        except OSError as replace_err:
            msg = f"❌ Failed to replace {filepath} after conversion: {replace_err}"
            print(msg)
            _log.error(msg)
            return False
        finally:
            # On success, temp_path was atomically renamed so exists() returns False.
            # On failure (OSError above), temp_path still exists and must be cleaned up.
            if os.path.exists(temp_path):
                os.remove(temp_path)
        _output(f"✅ Successfully converted {filepath}")
        return True
    except subprocess.CalledProcessError:
        msg = f"❌ Failed to convert {filepath}"
        print(msg)
        _log.error(msg)
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return False

def check_all_audio():
    """
    Validate all MP3 files in the audio directory and convert incompatible ones.

    Iterates over every file in AUDIO_DIR. Files with a `.mp3` extension are
    checked for Sonos compatibility; incompatible files are converted in-place
    using ffmpeg. Non-MP3 files are skipped with a warning.
    """
    # Issue 7: Check that AUDIO_DIR exists before listing its contents
    if not os.path.isdir(AUDIO_DIR):
        msg = f"❌ Audio directory not found: {AUDIO_DIR}"
        print(msg)
        _log.error(msg)
        return

    _output(f"🎧 Checking MP3 files in {AUDIO_DIR}...")
    for filename in os.listdir(AUDIO_DIR):
        if filename.endswith(VALID_EXTENSION):
            filepath = os.path.join(AUDIO_DIR, filename)
            if is_valid_mp3(filepath):
                _output(f"✅ {filename} is Sonos-compatible.")
            else:
                _output(f"⚠️  {filename} is not compatible. Attempting conversion...")
                if not convert_to_mp3(filepath):
                    msg = f"❌ ERROR: Could not convert {filename}. Please check the file format."
                    print(msg)
                    _log.error(msg)
        else:
            _output(f"❌ {filename} is not an MP3 file. Skipping.")

if __name__ == "__main__":
    check_all_audio()
