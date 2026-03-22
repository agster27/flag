"""
sonos_play.py — Plays an MP3 URL on a Sonos speaker and restores prior playback.

Accepts a single audio URL argument, snapshots the current Sonos state,
plays the requested file, waits for playback to finish, and optionally
restores the previous state. All events are logged to LOG_FILE.
"""
import argparse
import os
import sys
import soco
import tempfile
import time
import urllib.request
from datetime import datetime
from mutagen.mp3 import MP3
from soco.snapshot import Snapshot
from config import load_config, LOG_FILE


def log(message):
    """
    Append a timestamped message to the log file.

    Args:
        message (str): The message to log.
    """
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.now().isoformat()} - {message}\n")


def get_mp3_duration(url, default_wait):
    """
    Download an MP3 from *url* and return its duration in whole seconds.

    If the file cannot be downloaded or its duration cannot be read, the
    function logs a warning and returns *default_wait* as the fallback.

    Args:
        url (str): URL of the MP3 file.
        default_wait (int): Fallback duration in seconds.

    Returns:
        int: Duration of the MP3 in seconds, or *default_wait* on failure.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    temp_file = tmp.name
    tmp.close()
    try:
        urllib.request.urlretrieve(url, temp_file)
        audio = MP3(temp_file)
        duration = int(audio.info.length)
        log(f"INFO: MP3 duration is {duration} seconds")
        return duration
    except Exception as e:
        log(f"WARNING: Could not get duration. Defaulting to {default_wait} sec. Error: {e}")
        return default_wait
    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)


def main():
    """
    Entry point: parse arguments, play the requested audio on Sonos, and restore state.

    Reads configuration from config.json, snapshots the current Sonos playback
    state, plays the given audio URL at the configured volume, waits for the
    track to finish, and restores previous playback unless the speaker was idle
    and skip_restore_if_idle is enabled.
    """
    parser = argparse.ArgumentParser(description="Play an audio URL on a Sonos speaker.")
    parser.add_argument("audio_url", help="URL of the MP3 file to play")
    args = parser.parse_args()

    config = load_config()

    # --- Validate required config keys (Issue 1) ---
    sonos_ip = config.get("sonos_ip")
    if sonos_ip is None or not isinstance(sonos_ip, str) or not sonos_ip.strip():
        log("ERROR: 'sonos_ip' is missing or invalid in config.json. Aborting.")
        sys.exit("❌ 'sonos_ip' is missing or invalid in config.json. Please run setup.sh to reconfigure.")

    volume = config.get("volume")
    if volume is None or not isinstance(volume, (int, float)):
        log("ERROR: 'volume' is missing or not a number in config.json. Aborting.")
        sys.exit("❌ 'volume' is missing or not a number in config.json. Please run setup.sh to reconfigure.")

    # --- Validate volume range (Issue 3) ---
    volume = int(volume)
    if not (0 <= volume <= 100):
        clamped = max(0, min(100, volume))
        log(f"WARNING: Volume {volume} is outside 0–100; clamping to {clamped}.")
        print(f"  ⚠️  Volume {volume} is outside valid range 0–100; clamping to {clamped}.", file=sys.stderr)
        volume = clamped

    skip_restore_if_idle = config.get("skip_restore_if_idle", True)
    default_wait = config.get("default_wait_seconds", 60)

    # --- Validate audio_url argument (Issue 2) ---
    audio_url = args.audio_url
    if not audio_url.startswith("http://") and not audio_url.startswith("https://"):
        log(f"ERROR: audio_url '{audio_url}' is not a valid HTTP URL. Aborting.")
        sys.exit(f"❌ audio_url must start with http:// or https://. Got: {audio_url!r}")

    try:
        speaker = soco.SoCo(sonos_ip)
        coordinator = speaker.group.coordinator

        state = coordinator.get_current_transport_info()["current_transport_state"]
        was_playing = state == "PLAYING"

        snapshot = Snapshot(coordinator)
        snapshot.snapshot()
        log(f"INFO: Took snapshot of {coordinator.player_name} (was_playing={was_playing})")
        print(f"  ⏳ Connected to {coordinator.player_name}.")

        coordinator.stop()
        coordinator.volume = volume

        coordinator.play_uri(audio_url)
        log(f"SUCCESS: Played {audio_url} on {coordinator.player_name}")

        print("  ⏳ Fetching audio duration...")
        duration = get_mp3_duration(audio_url, default_wait)
        log(f"INFO: Waiting {duration} seconds for playback to finish")
        print(f"  ▶️  Playing — waiting ~{duration} seconds for playback to finish...")
        time.sleep(duration)

        if was_playing or not skip_restore_if_idle:
            snapshot.restore()
            log(f"INFO: Restored previous playback on {coordinator.player_name}")
            print("  ✅ Playback complete. State restored.")
        else:
            log("INFO: No prior playback. Skipping restore.")
            print("  ✅ Playback complete. (No prior playback — skipping restore.)")

    except Exception as e:
        log(f"ERROR: Failed during scheduled play - {e}")
        print(f"  ❌ Error during playback: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
