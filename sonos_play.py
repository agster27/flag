import soco
import sys
import time
import json
import urllib.request
from datetime import datetime
from mutagen.mp3 import MP3
from soco.snapshot import Snapshot

# Load config
with open("/opt/config.json") as f:
    config = json.load(f)

SONOS_IP = config["sonos_ip"]
VOLUME = config["volume"]
SKIP_RESTORE_IF_IDLE = config.get("skip_restore_if_idle", True)
DEFAULT_WAIT = config.get("default_wait_seconds", 60)
LOG_FILE = "/opt/sonos_play.log"
AUDIO_URL = sys.argv[1]

def log(message):
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.now().isoformat()} - {message}\n")

def get_mp3_duration(url):
    try:
        temp_file = "/tmp/temp_scheduled_song.mp3"
        urllib.request.urlretrieve(url, temp_file)
        audio = MP3(temp_file)
        return int(audio.info.length)
    except Exception as e:
        log(f"WARNING: Could not get duration. Defaulting to {DEFAULT_WAIT} sec. Error: {e}")
        return DEFAULT_WAIT

try:
    speaker = soco.SoCo(SONOS_IP)
    coordinator = speaker.group.coordinator

    state = coordinator.get_current_transport_info()["current_transport_state"]
    was_playing = state == "PLAYING"

    snapshot = Snapshot(coordinator)
    snapshot.snapshot()
    log(f"INFO: Took snapshot of {coordinator.player_name} (was_playing={was_playing})")

    coordinator.stop()
    coordinator.volume = VOLUME
    coordinator.play_uri(AUDIO_URL)
    log(f"SUCCESS: Played {AUDIO_URL} on {coordinator.player_name}")

    duration = get_mp3_duration(AUDIO_URL)
    log(f"INFO: Waiting {duration} seconds for playback to finish")
    time.sleep(duration)

    if was_playing or not SKIP_RESTORE_IF_IDLE:
        snapshot.restore()
        log(f"INFO: Restored previous playback on {coordinator.player_name}")
    else:
        log("INFO: No prior playback. Skipping restore.")

except Exception as e:
    log(f"ERROR: Failed during scheduled play - {e}")
