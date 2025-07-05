import soco
import sys
import time
from datetime import datetime
from mutagen.mp3 import MP3
import urllib.request
from soco.snapshot import Snapshot

LOG_FILE = "/opt/sonos_play.log"
SONOS_IP = "10.0.40.86"  # Replace with your Sonos IP
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
        log(f"WARNING: Could not get duration from {url}, defaulting to 60 seconds. Error: {e}")
        return 60

try:
    speaker = soco.SoCo(SONOS_IP)
    coordinator = speaker.group.coordinator

    # Check current playback state
    state = coordinator.get_current_transport_info()["current_transport_state"]
    was_playing = state == "PLAYING"

    # Take a snapshot
    snap = Snapshot(coordinator)
    snap.snapshot()
    log(f"INFO: Took snapshot of {coordinator.player_name} (was_playing={was_playing})")

    # Stop and play the scheduled song
    coordinator.stop()
    coordinator.volume = 30
    coordinator.play_uri(AUDIO_URL)
    log(f"SUCCESS: Played {AUDIO_URL} on {coordinator.player_name}")

    # Wait for song to finish
    duration = get_mp3_duration(AUDIO_URL)
    log(f"INFO: Waiting {duration} seconds for playback to finish")
    time.sleep(duration)

    # Only restore if something was playing before
    if was_playing:
        snap.restore()
        log(f"INFO: Restored previous playback on {coordinator.player_name}")
    else:
        log(f"INFO: No prior playback. Skipping restore.")

except Exception as e:
    log(f"ERROR: Failed during scheduled play - {e}")
