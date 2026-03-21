#!/usr/bin/env python3
"""
audio_check.py — Validates and converts audio files for Sonos compatibility.

Checks that MP3 files in the audio directory meet Sonos requirements
(stereo, 44.1 kHz or 48 kHz). Incompatible files are automatically
converted using ffmpeg.
"""

import os
import subprocess
from mutagen.mp3 import MP3
from mutagen import MutagenError
from config import AUDIO_DIR

# Sonos supports 44.1 kHz and 48 kHz sample rates for reliable MP3 playback
VALID_SAMPLE_RATES = [44100, 48000]
# Sonos requires stereo (2-channel) audio for reliable playback; mono files will be converted
VALID_CHANNELS = 2
VALID_EXTENSION = ".mp3"

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

    The converted file replaces the original at the same path.

    Args:
        filepath (str): Path to the file to convert.

    Returns:
        bool: True if conversion succeeded, False otherwise.
    """
    temp_path = filepath + ".converted.mp3"
    try:
        print(f"🔄 Converting {filepath} to a Sonos-compatible MP3...")
        subprocess.run([
            "ffmpeg", "-y", "-i", filepath,
            "-ar", "44100", "-ac", "2", "-codec:a", "libmp3lame", temp_path
        ], check=True)
        os.replace(temp_path, filepath)
        print(f"✅ Successfully converted {filepath}")
        return True
    except subprocess.CalledProcessError:
        print(f"❌ Failed to convert {filepath}")
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
    print("🎧 Checking MP3 files in /opt/flag/audio...")
    for filename in os.listdir(AUDIO_DIR):
        if filename.endswith(VALID_EXTENSION):
            filepath = os.path.join(AUDIO_DIR, filename)
            if is_valid_mp3(filepath):
                print(f"✅ {filename} is Sonos-compatible.")
            else:
                print(f"⚠️  {filename} is not compatible. Attempting conversion...")
                if not convert_to_mp3(filepath):
                    print(f"❌ ERROR: Could not convert {filename}. Please check the file format.")
        else:
            print(f"❌ {filename} is not an MP3 file. Skipping.")

if __name__ == "__main__":
    check_all_audio()
