#!/usr/bin/env python3

import os
import subprocess
import sys
from mutagen.mp3 import MP3
from mutagen import MutagenError

AUDIO_DIR = "/opt/flag/audio"
VALID_SAMPLE_RATES = [44100, 48000]
VALID_CHANNELS = 2
VALID_EXTENSION = ".mp3"

def is_valid_mp3(filepath):
    try:
        audio = MP3(filepath)
        if audio.info.sample_rate in VALID_SAMPLE_RATES and audio.info.channels == VALID_CHANNELS:
            return True
        return False
    except MutagenError:
        return False

def convert_to_mp3(filepath):
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
