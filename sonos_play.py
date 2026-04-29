"""
sonos_play.py — Plays an MP3 URL on one or more Sonos speakers in synchronized playback.

Accepts a single audio URL argument, temporarily groups the configured speakers,
plays the requested file, waits for playback to finish, then dissolves the
temporary group and restores each speaker to its prior state (group membership,
transport state, and volume). All events are logged to LOG_FILE.

When only one speaker is configured, the same 7-phase flow applies — the
group formation steps (join/unjoin) are simply no-ops because there are no
other speakers to coordinate with.

Play guard
----------
Before any speaker discovery or playback, a time-of-day guard verifies that the
current local time is within ``play_guard_tolerance_minutes`` (default: 2) of at
least one scheduled fire time from ``config.json``.  If the check fails, the
script logs a clear ERROR and exits non-zero without touching any speaker.

This guard prevents spurious 2 AM plays caused by systemd daemon-reload races
(see schedule_sonos.py for the full explanation).  The guard is bypassed by:

* ``--ignore-guard`` CLI flag — for manual tests and the sunset sleep-wrapper path
* ``play_guard_enabled: false`` in config.json — permanent per-install override
* ``allow_quiet_hours_play: true`` in config.json — legacy bypass key

Sunset sleep-wrapper
--------------------
When ``--sleep-until-schedule SCHEDULE_NAME`` is passed, the script reads the
schedule's ``time`` field from ``config.json``, computes today's actual fire time
(handling ``sunset`` / ``sunset±Nmin``), sleeps until that time, and then proceeds
with normal playback.  This flag is used by the static 03:00 sunset timer service
units so that the timer ``OnCalendar`` value never changes, eliminating the
daemon-reload race entirely.
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
import fcntl
import logging
import os
import shutil
import socket
import sys
import soco
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from mutagen.mp3 import MP3
from soco.snapshot import Snapshot
from config import load_config, LOG_FILE

try:
    from soco.exceptions import SoCoSlaveException  # may not exist in older soco
except ImportError:
    SoCoSlaveException = None

_log = logging.getLogger(__name__)

# Path to the shared advisory lock file used to prevent concurrent plays.
_PLAY_LOCK_FILE = "/run/flag.lock"


def log(message):
    """
    Append a timestamped message to the log file.

    This is a thin compatibility shim; new code should use ``_log`` directly.

    Args:
        message (str): The message to log.
    """
    _log.info(message)


def check_play_guard(config, now=None):
    """
    Return True if playback is permitted at the current local time.

    Reads the ``schedules`` list from *config*, computes today's local fire time
    for every entry (including sunset-based ones), and returns ``True`` if *now*
    falls within ``±play_guard_tolerance_minutes`` of at least one fire time.

    Returns ``True`` immediately (bypassing all checks) when:

    * ``play_guard_enabled`` is ``False`` in config  (explicit opt-out)
    * ``allow_quiet_hours_play`` is ``True`` in config  (legacy bypass)

    Args:
        config (dict): Parsed configuration dictionary from config.json.
        now (datetime | None): The local time to test against.  A naive datetime
            is assumed to be in the configured timezone.  Defaults to
            ``datetime.now()`` in the configured timezone.

    Returns:
        bool: ``True`` if playback is permitted, ``False`` if the time-of-day
        check fails (likely a systemd misfire).
    """
    # Respect explicit opt-out keys.
    if not config.get("play_guard_enabled", True):
        return True
    if config.get("allow_quiet_hours_play", False):
        return True

    tolerance_mins = int(config.get("play_guard_tolerance_minutes", 2))
    tz_name = config.get("timezone", "UTC")

    # Attempt timezone-aware comparison; fall back to naive local time.
    try:
        import pytz as _pytz
        tz_obj = _pytz.timezone(tz_name)
        if now is None:
            now_aware = datetime.now(tz_obj)
        elif getattr(now, "tzinfo", None) is None:
            now_aware = tz_obj.localize(now)
        else:
            now_aware = now
    except Exception:
        # pytz unavailable or invalid timezone — fall back to naive datetime.
        now_aware = now if now is not None else datetime.now()

    schedules = config.get("schedules") or []
    if not schedules:
        # No schedules → cannot determine expected fire time → allow.
        return True

    # Lazy import of sunset helpers to avoid a hard dependency at module load.
    try:
        from schedule_sonos import (
            parse_sunset_offset,
            get_sunset_local_time,
            get_sunset_local_time_with_offset,
        )
        _sunset_helpers_available = True
    except ImportError:
        _sunset_helpers_available = False

    tolerance_secs = tolerance_mins * 60

    for entry in schedules:
        time_str = entry.get("time", "")
        if not isinstance(time_str, str) or not time_str.strip():
            continue
        time_str_norm = time_str.strip().lower()

        try:
            if _sunset_helpers_available:
                offset = parse_sunset_offset(time_str_norm)
            else:
                offset = None

            if time_str_norm == "sunset":
                if not _sunset_helpers_available:
                    continue
                hour, minute = get_sunset_local_time(config)
            elif offset is not None:
                if not _sunset_helpers_available:
                    continue
                hour, minute = get_sunset_local_time_with_offset(config, offset)
            else:
                parts = time_str_norm.split(":")
                if len(parts) != 2:
                    continue
                hour, minute = int(parts[0]), int(parts[1])
                if not (0 <= hour <= 23 and 0 <= minute <= 59):
                    continue
        except Exception:
            continue

        try:
            fire_dt = now_aware.replace(hour=hour, minute=minute,
                                        second=0, microsecond=0)
            delta_secs = abs((now_aware - fire_dt).total_seconds())
            if delta_secs <= tolerance_secs:
                return True
        except Exception:
            continue

    return False


def _sleep_until_schedule(config, schedule_name):
    """
    Compute today's fire time for *schedule_name* and sleep until it.

    Called when ``--sleep-until-schedule`` is passed on the command line.  Used
    by the static 03:00 sunset timer service units so the timer ``OnCalendar``
    value never needs to change.

    If the computed fire time has already passed today (e.g., the service was
    started after a reboot past sunset), exits with code 0 immediately without
    playing — missed plays are intentionally skipped.

    After waking, acquires the shared advisory play lock (``/run/flag.lock``)
    non-exclusively so that concurrent plays are still prevented (same semantics
    as the ``flock -n`` wrapper used by fixed-time services).

    Args:
        config (dict): Parsed configuration from config.json.
        schedule_name (str): Name of the schedule entry whose time to use.

    Returns:
        Never returns normally; either sleeps-then-returns-to-caller OR sys.exit.
    """
    # Locate the schedule entry.
    schedules = config.get("schedules") or []
    entry = next((s for s in schedules if s.get("name") == schedule_name), None)
    if entry is None:
        _log.error(
            "--sleep-until-schedule: schedule '%s' not found in config.json; aborting.",
            schedule_name,
        )
        sys.exit(1)

    time_str = entry.get("time", "")
    time_str_norm = (time_str.strip().lower() if isinstance(time_str, str)
                     else "")

    tz_name = config.get("timezone", "UTC")
    try:
        import pytz as _pytz
        tz_obj = _pytz.timezone(tz_name)
        now = datetime.now(tz_obj)
    except Exception:
        now = datetime.now()

    try:
        from schedule_sonos import (
            parse_sunset_offset,
            get_sunset_local_time,
            get_sunset_local_time_with_offset,
        )
        offset = parse_sunset_offset(time_str_norm)
        if time_str_norm == "sunset":
            hour, minute = get_sunset_local_time(config)
        elif offset is not None:
            hour, minute = get_sunset_local_time_with_offset(config, offset)
        else:
            parts = time_str_norm.split(":")
            if len(parts) != 2:
                raise ValueError(f"Unexpected time format: {time_str!r}")
            hour, minute = int(parts[0]), int(parts[1])
    except Exception as exc:
        _log.error(
            "--sleep-until-schedule: cannot compute fire time for '%s': %s; aborting.",
            schedule_name, exc,
        )
        sys.exit(1)

    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    sleep_secs = (target - now).total_seconds()

    if sleep_secs <= 0:
        _log.info(
            "sleep_until_schedule: fire time %02d:%02d for '%s' has already passed "
            "today; skipping play.",
            hour, minute, schedule_name,
        )
        sys.exit(0)

    _log.info(
        "sleep_until_schedule: sleeping %.0f s until %02d:%02d for schedule '%s'",
        sleep_secs, hour, minute, schedule_name,
    )
    time.sleep(sleep_secs)
    _log.info(
        "sleep_until_schedule: woke up at %02d:%02d; proceeding with playback for '%s'",
        hour, minute, schedule_name,
    )

    # Acquire the shared advisory lock (non-blocking) so that concurrent plays
    # are prevented, matching the ``flock -n /run/flag.lock`` semantics used by
    # fixed-time services.
    try:
        lock_fd = open(_PLAY_LOCK_FILE, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Keep lock_fd alive for the duration of the process (closed on exit).
        # Assign to a module-level variable so it is not garbage-collected.
        _sleep_until_schedule._lock_fd = lock_fd  # type: ignore[attr-defined]
    except (OSError, IOError):
        _log.error(
            "sleep_until_schedule: another play is in progress; skipping '%s'.",
            schedule_name,
        )
        sys.exit(0)

    # Caller (main()) will proceed with the normal playback flow after we return.


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
        try:
            with urllib.request.urlopen(url, timeout=15) as response:
                with open(temp_file, "wb") as f:
                    shutil.copyfileobj(response, f)
        except (socket.timeout, urllib.error.URLError) as e:
            _log.warning("Could not download audio for duration check. Defaulting to %d sec. Error: %s", default_wait, e)
            return default_wait
        audio = MP3(temp_file)
        duration = int(audio.info.length)
        _log.info("MP3 duration is %d seconds", duration)
        return duration
    except Exception as e:
        _log.warning("Could not get duration. Defaulting to %d sec. Error: %s", default_wait, e)
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
    parser.add_argument(
        "--ignore-guard",
        action="store_true",
        help="Skip the time-of-day play guard (for manual tests via setup.sh).",
    )
    parser.add_argument(
        "--sleep-until-schedule",
        metavar="SCHEDULE_NAME",
        help=(
            "Compute today's fire time for SCHEDULE_NAME from config.json and sleep "
            "until that time before playing.  Used by static 03:00 sunset timer "
            "service units to avoid daemon-reload races."
        ),
    )
    args = parser.parse_args()

    config = load_config()

    # ------------------------------------------------------------------
    # Sunset sleep-wrapper: sleep until the schedule's computed fire time,
    # then fall through to normal playback (guard bypassed since timing is
    # handled by the wrapper itself).
    # ------------------------------------------------------------------
    if args.sleep_until_schedule is not None:
        _sleep_until_schedule(config, args.sleep_until_schedule)
        # After _sleep_until_schedule returns we are at the correct time;
        # proceed with playback without the guard.
        args.ignore_guard = True

    # ------------------------------------------------------------------
    # Play guard: refuse to play if now is not near any scheduled fire time.
    # This is the primary defense against spurious systemd misfires.
    # ------------------------------------------------------------------
    if not args.ignore_guard:
        if not check_play_guard(config):
            tolerance = config.get("play_guard_tolerance_minutes", 2)
            _log.error(
                "play_guard refused to play %s at %s — no scheduled fire time "
                "within ±%d min.  This is likely a systemd misfire; aborting.",
                args.audio_url,
                datetime.now().strftime("%H:%M:%S"),
                tolerance,
            )
            sys.exit(1)

    # --- Validate speakers list ---
    speakers_cfg = config.get("speakers")
    if not isinstance(speakers_cfg, list) or not speakers_cfg:
        log("ERROR: 'speakers' must be a non-empty list in config.json. Aborting.")
        sys.exit(
            "❌ 'speakers' is missing or invalid in config.json. "
            "Please run setup.sh to reconfigure."
        )

    # --- Global (default) volume ---
    # May be absent when every speaker has its own volume; default to 30.
    default_vol = config.get("volume", 30)
    if not isinstance(default_vol, (int, float)):
        log(f"WARNING: 'volume' value {default_vol!r} is not a number; using 30.")
        default_vol = 30
    default_vol = int(default_vol)
    if not (0 <= default_vol <= 100):
        clamped = max(0, min(100, default_vol))
        log(f"WARNING: Volume {default_vol} is outside 0–100; clamping to {clamped}.")
        print(f"  ⚠️  Volume {default_vol} is outside valid range 0–100; clamping to {clamped}.", file=sys.stderr)
        default_vol = clamped

    # --- Build per-speaker (ip, configured_volume) list ---
    # Supports both legacy string format and new object format.
    speaker_entries = []
    for spk in speakers_cfg:
        if isinstance(spk, dict):
            ip = spk.get("ip", "")
            raw_vol = spk.get("volume", default_vol)
            try:
                vol = max(0, min(100, int(raw_vol)))
            except (TypeError, ValueError):
                vol = default_vol
        else:
            ip = spk
            vol = default_vol
        if ip:
            speaker_entries.append((str(ip), vol))

    if not speaker_entries:
        log("ERROR: 'speakers' contains no valid IP entries. Aborting.")
        sys.exit(
            "❌ 'speakers' contains no valid IP entries in config.json. "
            "Please run setup.sh to reconfigure."
        )

    skip_restore_if_idle = config.get("skip_restore_if_idle", True)
    try:
        default_wait = int(config.get("default_wait_seconds", 60))
        if not (0 < default_wait <= 3600):
            log(f"WARNING: 'default_wait_seconds' {default_wait!r} is out of range (must be 1–3600); using 60.")
            default_wait = 60
    except (TypeError, ValueError):
        log(f"WARNING: 'default_wait_seconds' {config.get('default_wait_seconds')!r} is not a valid integer; using 60.")
        default_wait = 60

    # --- Validate audio_url argument ---
    audio_url = args.audio_url
    if not audio_url.startswith("http://") and not audio_url.startswith("https://"):
        log(f"ERROR: audio_url '{audio_url}' is not a valid HTTP URL. Aborting.")
        sys.exit(f"❌ audio_url must start with http:// or https://. Got: {audio_url!r}")

    # =========================================================================
    # Phase 0: Discovery & validation
    # =========================================================================
    # spk_vol_map: speaker IP address -> configured playback volume
    spk_vol_map = {}
    reachable = []
    for ip, vol in speaker_entries:
        try:
            sp = soco.SoCo(ip)
            sp.get_speaker_info(refresh=True)  # forces a network round-trip to the device; raises if the speaker is unreachable
            reachable.append(sp)
            spk_vol_map[ip] = vol
            log(f"INFO: Connected to speaker at {ip} ({sp.player_name}) volume={vol}")
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
            # Snapshot the *coordinator only* of each pre-existing group — restoring the coordinator restores the whole group.
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
        bugle_coordinator.volume = spk_vol_map.get(bugle_coordinator.ip_address, default_vol)
        for sp in reachable[1:]:
            try:
                sp.join(bugle_coordinator)
                sp.volume = spk_vol_map.get(sp.ip_address, default_vol)
                log(f"INFO: {sp.player_name} joined bugle group")
            except Exception as e:
                log(f"WARNING: Could not add {sp.player_name} to bugle group: {e}")

        time.sleep(1)

        # =====================================================================
        # Phase 4: Play
        # =====================================================================
        # Fetch duration *before* play_uri so we don't touch the bugle group
        # if the URL is unreachable, and avoid a redundant HTTP round-trip.
        print("  ⏳ Fetching audio duration...")
        duration = get_mp3_duration(audio_url, default_wait)
        wait_secs = duration + 1

        bugle_coordinator.play_uri(audio_url)
        log(f"SUCCESS: Playing {audio_url} on {bugle_coordinator.player_name} (and group members)")

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
