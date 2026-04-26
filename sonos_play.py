"""
sonos_play.py — Plays an MP3 URL on one or more Sonos speakers in synchronized playback.

Accepts a single audio URL argument, temporarily groups the configured speakers,
plays the requested file, waits for playback to finish, then dissolves the
temporary group and restores each speaker to its prior state (group membership,
transport state, and volume). All events are logged to LOG_FILE.

When only one speaker is configured, the same 7-phase flow applies — the
group formation steps (join/unjoin) are simply no-ops because there are no
other speakers to coordinate with.
"""

# =============================================================================
# QA TESTING CHECKLIST
# =============================================================================
# Manually verify the following scenarios before releasing to production.
#
# SCENARIO 1: All standalone, all idle
#   [ ] N speakers, none grouped, none playing.
#   [ ] All play in sync at the configured volume.
#   [ ] After playback, all speakers return to idle (no restore with
#       skip_restore_if_idle=true).
#
# SCENARIO 2: All standalone, one playing
#   [ ] Speaker A playing music; B & C idle.
#   [ ] All three play in sync; A resumes its music after the bugle call.
#   [ ] B & C stay idle (skip_restore_if_idle=true).
#
# SCENARIO 3: Pre-existing group with a non-target
#   [ ] A & D grouped (D not a target), A playing music.
#   [ ] A unjoins from D; all targets play taps in sync.
#   [ ] After playback, A rejoins D and music resumes on the A+D group.
#   [ ] D is unaffected throughout (only target speakers are touched).
#
# SCENARIO 4: All targets in the same pre-existing group
#   [ ] A, B, C already grouped and playing.
#   [ ] Only one snapshot is taken (coordinator, not three individual ones).
#   [ ] All unjoin, play in sync, regroup with original coordinator.
#   [ ] Music resumes after playback.
#
# SCENARIO 5: One target offline at fire time
#   [ ] B is unreachable — a warning is logged for B, no crash.
#   [ ] A & C play taps in sync; exit 0.
#
# SCENARIO 6: All targets offline
#   [ ] ERROR logged; process exits non-zero so systemd marks the unit failed.
#
# SCENARIO 7: Bad audio URL
#   [ ] Error is logged when play_uri fails.
#   [ ] Every speaker still rejoins its original group correctly (try/finally
#       ensures Phase 5–7 always execute).
#
# SCENARIO 8: Volume correctness
#   [ ] Bugle plays at configured volume on every speaker.
#   [ ] Each speaker's original volume is restored after playback.
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

try:
    from soco.exceptions import SoCoSlaveException  # may not exist in older soco
except ImportError:
    SoCoSlaveException = None


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
    Entry point: play the requested audio on all configured Sonos speakers in sync.

    Phases:
      0. Discovery — connect to each configured speaker IP; skip unreachable ones.
      1. Snapshot  — capture pre-existing group state and volumes (one snapshot
                     per pre-existing group coordinator, not per speaker).
      2. Tear down — pause and unjoin all target speakers from their groups.
      3. Bugle group — join all reachable speakers under one temporary coordinator.
      4. Play      — play_uri on the coordinator; sleep for duration + 1 s.
      5. Tear down — stop the coordinator; unjoin all bugle group members.
      6. Restore   — rejoin original groups; call snapshot.restore() where needed.
      7. Volumes   — restore each speaker's pre-bugle volume.

    A try/finally ensures that Phases 5–7 always execute, even when Phase 4 raises.
    """
    parser = argparse.ArgumentParser(description="Play an audio URL on Sonos speakers.")
    parser.add_argument("audio_url", help="URL of the MP3 file to play")
    args = parser.parse_args()

    config = load_config()

    # --- Validate speakers list ---
    speakers_cfg = config.get("speakers")
    if not isinstance(speakers_cfg, list) or not speakers_cfg:
        log("ERROR: 'speakers' must be a non-empty list in config.json. Aborting.")
        sys.exit(
            "❌ 'speakers' is missing or invalid in config.json. "
            "Please run setup.sh to reconfigure."
        )

    volume = config.get("volume")
    if volume is None or not isinstance(volume, (int, float)):
        log("ERROR: 'volume' is missing or not a number in config.json. Aborting.")
        sys.exit(
            "❌ 'volume' is missing or not a number in config.json. "
            "Please run setup.sh to reconfigure."
        )

    # --- Validate volume range ---
    volume = int(volume)
    if not (0 <= volume <= 100):
        clamped = max(0, min(100, volume))
        log(f"WARNING: Volume {volume} is outside 0–100; clamping to {clamped}.")
        print(f"  ⚠️  Volume {volume} is outside valid range 0–100; clamping to {clamped}.", file=sys.stderr)
        volume = clamped

    skip_restore_if_idle = config.get("skip_restore_if_idle", True)
    default_wait = config.get("default_wait_seconds", 60)

    # --- Validate audio_url argument ---
    audio_url = args.audio_url
    if not audio_url.startswith("http://") and not audio_url.startswith("https://"):
        log(f"ERROR: audio_url '{audio_url}' is not a valid HTTP URL. Aborting.")
        sys.exit(f"❌ audio_url must start with http:// or https://. Got: {audio_url!r}")

    # =========================================================================
    # Phase 0: Discovery & validation
    # =========================================================================
    reachable = []
    for ip in speakers_cfg:
        try:
            sp = soco.SoCo(ip)
            sp.get_speaker_info(refresh=True)  # forces a network round-trip to the device; raises if the speaker is unreachable
            reachable.append(sp)
            log(f"INFO: Connected to speaker at {ip} ({sp.player_name})")
        except Exception as e:
            log(f"WARNING: Speaker at {ip} is unreachable: {e}. Skipping.")
            print(f"  ⚠️  Speaker at {ip} is unreachable — skipping.", file=sys.stderr)

    if not reachable:
        log("ERROR: All configured speakers are unreachable. Aborting.")
        print("  ❌ All configured speakers are unreachable.", file=sys.stderr)
        sys.exit(1)

    bugle_coordinator = reachable[0]
    log(f"INFO: {len(reachable)} speaker(s) reachable. Bugle coordinator: {bugle_coordinator.player_name}")
    print(f"  ⏳ Connected to {len(reachable)} speaker(s). Coordinator: {bugle_coordinator.player_name}")

    # =========================================================================
    # Phase 1: Snapshot (per pre-existing group, not per speaker)
    # =========================================================================
    # pre_existing_groups: coordinator_uid -> {snapshot, was_playing, member_uids, member_speakers, coordinator_speaker}
    pre_existing_groups = {}
    pre_bugle_volumes = {}  # speaker uid -> volume before we change it

    for sp in reachable:
        try:
            pre_bugle_volumes[sp.uid] = sp.volume
            group_coord = sp.group.coordinator
            uid = group_coord.uid
            if uid not in pre_existing_groups:
                state = group_coord.get_current_transport_info()["current_transport_state"]
                was_playing = state == "PLAYING"
                snap = Snapshot(group_coord)
                snap.snapshot()
                member_uids = {m.uid for m in sp.group.members}
                # Exclude the coordinator itself; only non-coordinator members need to rejoin.
                member_speakers = [m for m in sp.group.members if m.uid != group_coord.uid]
                pre_existing_groups[uid] = {
                    "snapshot": snap,
                    "was_playing": was_playing,
                    "member_uids": member_uids,             # keep for any existing references
                    "member_speakers": member_speakers,     # full SoCo objects of non-coordinator members
                    "coordinator_speaker": group_coord,
                }
                log(f"INFO: Snapshot taken on {group_coord.player_name} (was_playing={was_playing})")
        except Exception as e:
            log(f"WARNING: Could not snapshot speaker {sp.player_name}: {e}")

    log(f"INFO: Snapshot summary — {len(pre_existing_groups)} pre-existing group(s)")

    try:
        # =====================================================================
        # Phase 2: Tear down pre-existing groups
        # =====================================================================
        for uid, info in pre_existing_groups.items():
            if info["was_playing"]:
                try:
                    info["coordinator_speaker"].pause()
                    log(f"INFO: Paused {info['coordinator_speaker'].player_name}")
                except Exception as e:
                    log(f"WARNING: Could not pause {info['coordinator_speaker'].player_name}: {e}")

        for sp in reachable:
            try:
                if len(sp.group.members) > 1:
                    sp.unjoin()
                    log(f"INFO: Unjoined {sp.player_name} from pre-existing group")
            except Exception as e:
                log(f"WARNING: Could not unjoin {sp.player_name}: {e}")

        time.sleep(1)

        # =====================================================================
        # Phase 3: Form temporary bugle group
        # =====================================================================
        bugle_coordinator.volume = volume
        for sp in reachable[1:]:
            try:
                sp.join(bugle_coordinator)
                sp.volume = volume
                log(f"INFO: {sp.player_name} joined bugle group")
            except Exception as e:
                log(f"WARNING: Could not add {sp.player_name} to bugle group: {e}")

        time.sleep(1)

        # =====================================================================
        # Phase 4: Play
        # =====================================================================
        bugle_coordinator.play_uri(audio_url)
        log(f"SUCCESS: Playing {audio_url} on {bugle_coordinator.player_name} (and group members)")

        print("  ⏳ Fetching audio duration...")
        duration = get_mp3_duration(audio_url, default_wait)
        wait_secs = duration + 1
        log(f"INFO: Waiting {wait_secs} seconds for playback to finish")
        print(f"  ▶️  Playing — waiting ~{wait_secs} seconds for playback to finish...")
        time.sleep(wait_secs)

    except Exception as play_err:
        log(f"ERROR: Playback failed — {play_err}")
        print(f"  ❌ Error during playback: {play_err}", file=sys.stderr)

    finally:
        # =====================================================================
        # Phase 5: Tear down bugle group
        # =====================================================================
        try:
            bugle_coordinator.stop()
            log(f"INFO: Stopped playback on {bugle_coordinator.player_name}")
        except Exception as stop_err:
            # Sonos raises an error when stop() is called on a non-coordinator group
            # member.  We detect this via SoCoSlaveException (if available in the
            # installed soco version) or by matching the canonical message fragment
            # "coordinator" as a fallback for older soco versions.
            is_slave_error = (
                SoCoSlaveException is not None and isinstance(stop_err, SoCoSlaveException)
            ) or "coordinator" in str(stop_err).lower()
            if is_slave_error:
                try:
                    bugle_coordinator.group.coordinator.stop()
                    log(
                        f"INFO: Stopped playback via group coordinator fallback "
                        f"on {bugle_coordinator.player_name}"
                    )
                except Exception as coord_stop_err:
                    log(
                        f"WARNING: Fallback coordinator stop also failed "
                        f"for {bugle_coordinator.player_name}: {coord_stop_err}"
                    )
            else:
                log(f"WARNING: stop() failed on {bugle_coordinator.player_name}: {stop_err}")

        for sp in reachable[1:]:
            try:
                sp.unjoin()
                log(f"INFO: Unjoined {sp.player_name} from bugle group")
            except Exception as e:
                log(f"WARNING: Could not unjoin {sp.player_name} from bugle group: {e}")

        # =====================================================================
        # Phase 6: Restore pre-existing groups
        # =====================================================================
        # Identity of speakers is compared by Sonos UID rather than Python object
        # identity: the same physical speaker can be represented by different SoCo
        # instances (one created in Phase 0 for `reachable`, another captured here
        # from `sp.group.coordinator` in Phase 1).

        # First pass: rejoin all pre-existing group members (including non-targets
        # captured in Phase 1 as full SoCo objects via `member_speakers`).
        for uid, info in pre_existing_groups.items():
            group_coord = info["coordinator_speaker"]
            for member in info["member_speakers"]:
                try:
                    member.join(group_coord)
                except Exception as e:
                    log(f"WARNING: Could not rejoin {member.player_name} to original group: {e}")

        # Sleep once after all rejoins (not once per group) to allow Sonos devices
        # to settle after the group topology changes before restoring transport state.
        time.sleep(1)

        # Second pass: restore transport state for each pre-existing group.
        for uid, info in pre_existing_groups.items():
            try:
                group_coord = info["coordinator_speaker"]
                if info["was_playing"] or not skip_restore_if_idle:
                    info["snapshot"].restore()
                    log(
                        f"INFO: Restored {group_coord.player_name} "
                        f"(was_playing={info['was_playing']})"
                    )
                else:
                    log(
                        f"INFO: Skipping restore for {group_coord.player_name} "
                        f"(was idle and skip_restore_if_idle=True)"
                    )
            except Exception as e:
                log(f"ERROR: Failed to restore group for coordinator uid={uid}: {e}")

        # =====================================================================
        # Phase 7: Restore per-speaker volumes
        # =====================================================================
        for sp in reachable:
            try:
                if sp.uid in pre_bugle_volumes:
                    sp.volume = pre_bugle_volumes[sp.uid]
                    log(f"INFO: Restored volume on {sp.player_name}")
            except Exception as e:
                log(f"WARNING: Could not restore volume for {sp.player_name}: {e}")

        print("  ✅ Playback complete.")


if __name__ == "__main__":
    main()
