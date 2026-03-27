"""
sonos_play.py — Plays an MP3 URL on a Sonos speaker and restores prior playback.

Accepts a single audio URL argument, snapshots the current Sonos state,
plays the requested file, waits for playback to finish, and optionally
restores the previous state. All events are logged to LOG_FILE.
"""

# =============================================================================
# QA TESTING CHECKLIST
# =============================================================================
# Manually verify the following scenarios before releasing to production.
#
# SCENARIO 1: Speaker is standalone (not in any group)
#   [ ] Bugle call plays on the configured speaker at the configured volume.
#   [ ] If music was playing before, it resumes after the bugle call finishes.
#   [ ] If the speaker was idle and skip_restore_if_idle=true (default), the
#       speaker remains idle after the bugle call (no restore).
#
# SCENARIO 2: Speaker is in a group and music IS playing
#   [ ] The group's music is paused immediately (coordinator.pause() is called).
#   [ ] The target speaker unjoins the group (it becomes a standalone player).
#   [ ] The bugle call plays ONLY on the target speaker (not the whole group).
#   [ ] Other speakers in the group remain silent during the bugle call.
#   [ ] After playback, the target speaker rejoins the original group.
#   [ ] The group's music resumes from where it left off (snapshot restored).
#
# SCENARIO 3: Speaker is in a group and music is NOT playing (idle group)
#   [ ] The target speaker unjoins the group.
#   [ ] The bugle call plays ONLY on the target speaker.
#   [ ] After playback, the target speaker rejoins the original group.
#   [ ] If skip_restore_if_idle=true (default), the group stays idle (no restore).
#   [ ] If skip_restore_if_idle=false, the snapshot is restored anyway.
#
# SCENARIO 4: Speaker IS the coordinator of a group
#   [ ] Same group-aware flow as Scenario 2/3: the coordinator unjoins the group
#       (becoming standalone), plays the bugle call, then rejoins.
#   [ ] After rejoin and restore, the group reforms correctly and playback
#       resumes if it was active before.
#
# SCENARIO 5: Error handling / resilience
#   [ ] If an error occurs during play_uri or sleep, the speaker still attempts
#       to rejoin its original group (try/finally ensures this).
#   [ ] If the rejoin itself fails, the error is logged clearly so the user
#       can diagnose the issue manually.
#   [ ] The outer error handler logs the playback error with full details.
#
# SCENARIO 6: Volume handling
#   [ ] The bugle call plays at the volume specified in config.json.
#   [ ] After restore, the speaker's volume returns to what it was before
#       (volume is part of the snapshot that is restored).
# =============================================================================
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
    state, plays the given audio URL at the configured volume on the target
    speaker only, waits for the track to finish, and restores previous playback
    unless the speaker was idle and skip_restore_if_idle is enabled.

    When the target speaker is part of a group the function:
      1. Pauses the group's music on the coordinator.
      2. Unjoins the target speaker so it becomes a standalone player.
      3. Plays the bugle call on that speaker alone.
      4. Rejoins the speaker to its original group.
      5. Restores the coordinator's snapshot (resumes the song if one was playing).

    When the target speaker is already standalone the original single-speaker
    flow is used (snapshot → stop → play → restore).
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

        group_members = speaker.group.members
        is_grouped = len(group_members) > 1

        if is_grouped:
            log(f"INFO: Speaker {speaker.player_name} is in a group of {len(group_members)} members. Will play solo.")

            if was_playing:
                coordinator.pause()
                log(f"INFO: Paused group playback on {coordinator.player_name}")

            speaker.unjoin()
            log(f"INFO: Unjoined {speaker.player_name} from group")
            time.sleep(1)

            try:
                speaker.volume = volume
                speaker.play_uri(audio_url)
                log(f"SUCCESS: Played {audio_url} on {speaker.player_name}")

                print("  ⏳ Fetching audio duration...")
                duration = get_mp3_duration(audio_url, default_wait)
                log(f"INFO: Waiting {duration} seconds for playback to finish")
                print(f"  ▶️  Playing — waiting ~{duration} seconds for playback to finish...")
                time.sleep(duration)

                try:
                    speaker.stop()
                    log(f"INFO: Stopped playback on {speaker.player_name}")
                except Exception as stop_err:
                    # Sonos raises a generic exception with this message when stop()
                    # is called on a non-coordinator group member.  soco does not
                    # expose a dedicated exception type for this case, so we
                    # identify it by the canonical message fragment "coordinator".
                    if "coordinator" in str(stop_err).lower():
                        # Speaker was re-grouped before stop(); fall back to the
                        # current group coordinator (safe for all group members).
                        try:
                            speaker.group.coordinator.stop()
                            log(
                                f"INFO: Stopped playback via group coordinator "
                                f"(fallback) on {speaker.player_name}"
                            )
                        except Exception as coord_stop_err:
                            log(
                                f"WARNING: Fallback coordinator stop also failed "
                                f"for {speaker.player_name}: {coord_stop_err}"
                            )
                    else:
                        raise
            finally:
                try:
                    speaker.join(coordinator)
                    log(f"INFO: Rejoined {speaker.player_name} to group coordinator {coordinator.player_name}")
                    time.sleep(1)
                except Exception as join_err:
                    log(f"ERROR: Failed to rejoin {speaker.player_name} to group: {join_err}. Manual intervention may be required to rejoin the speaker to its group.")

            if was_playing or not skip_restore_if_idle:
                snapshot.restore()
                log(f"INFO: Restored previous playback on {coordinator.player_name}")
                print("  ✅ Playback complete. State restored.")
            else:
                log("INFO: No prior playback. Skipping restore.")
                print("  ✅ Playback complete. (No prior playback — skipping restore.)")
        else:
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
