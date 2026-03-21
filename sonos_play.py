import argparse
import os
import soco
import tempfile
import time
import urllib.request
from datetime import datetime
from mutagen.mp3 import MP3
from soco.snapshot import Snapshot
from config import load_config, LOG_FILE


def log(message):
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.now().isoformat()} - {message}\n")


def get_mp3_duration(url, default_wait):
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    temp_file = tmp.name
    tmp.close()
    try:
        urllib.request.urlretrieve(url, temp_file)
        audio = MP3(temp_file)
        return int(audio.info.length)
    except Exception as e:
        log(f"WARNING: Could not get duration. Defaulting to {default_wait} sec. Error: {e}")
        return default_wait
    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)


def main():
    parser = argparse.ArgumentParser(description="Play an audio URL on a Sonos speaker.")
    parser.add_argument("audio_url", help="URL of the MP3 file to play")
    args = parser.parse_args()

    config = load_config()
    sonos_ip = config["sonos_ip"]
    volume = config["volume"]
    skip_restore_if_idle = config.get("skip_restore_if_idle", True)
    default_wait = config.get("default_wait_seconds", 60)
    audio_url = args.audio_url

    try:
        speaker = soco.SoCo(sonos_ip)
        coordinator = speaker.group.coordinator

        state = coordinator.get_current_transport_info()["current_transport_state"]
        was_playing = state == "PLAYING"

        snapshot = Snapshot(coordinator)
        snapshot.snapshot()
        log(f"INFO: Took snapshot of {coordinator.player_name} (was_playing={was_playing})")

        coordinator.stop()
        coordinator.volume = volume

        coordinator.play_uri(audio_url)
        log(f"SUCCESS: Played {audio_url} on {coordinator.player_name}")

        duration = get_mp3_duration(audio_url, default_wait)
        log(f"INFO: Waiting {duration} seconds for playback to finish")
        time.sleep(duration)

        if was_playing or not skip_restore_if_idle:
            snapshot.restore()
            log(f"INFO: Restored previous playback on {coordinator.player_name}")
        else:
            log("INFO: No prior playback. Skipping restore.")

    except Exception as e:
        log(f"ERROR: Failed during scheduled play - {e}")


if __name__ == "__main__":
    main()
