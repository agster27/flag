#!/usr/bin/env python3
"""
schedule_sonos.py — Generates systemd timer and service unit files for Sonos audio scheduling.

Reads configuration from config.json, computes sunset time (using the astral library
when needed), and writes systemd .service and .timer unit files for each scheduled
audio play. Also generates a daily ``flag-reschedule`` timer that re-runs this script
at 02:00 local time to keep sunset-based timers accurate.

Unit files are written to /etc/systemd/system/ and therefore require root privileges.

Supports an extensible ``schedules`` array in config.json, with backward compatibility
for the legacy flat ``colors_url`` / ``taps_url`` / ``colors_time`` keys. If those old
keys are present but ``schedules`` is absent, a deprecation warning is printed and a
synthetic schedule list is built automatically.
"""

import glob as _glob
import logging
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

from astral import LocationInfo
from astral.sun import sun
import pytz

from config import load_config, INSTALL_DIR, LOG_FILE  # noqa: F401 (LOG_FILE triggers basicConfig)

_log = logging.getLogger("schedule_sonos")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYSTEMD_DIR = "/etc/systemd/system"
PYTHON_BIN = os.path.join(INSTALL_DIR, "sonos-env", "bin", "python")
SONOS_PLAY = os.path.join(INSTALL_DIR, "sonos_play.py")
SCHEDULE_SCRIPT = os.path.abspath(__file__)

# Name suffixes (after "flag-") of units that must never be removed by stale cleanup
_RESERVED_NAMES = {"audio-http", "reschedule"}


# ---------------------------------------------------------------------------
# Timezone / Location helpers
# ---------------------------------------------------------------------------

def get_system_timezone():
    """
    Determine the current system timezone name.

    Tries ``/etc/timezone`` (Debian/Ubuntu), then ``timedatectl``, falling
    back to ``"UTC"`` on any error.

    Returns:
        str: IANA timezone name (e.g. ``"America/New_York"``).
    """
    try:
        # Debian/Ubuntu provide a plain text timezone file
        if os.path.exists("/etc/timezone"):
            with open("/etc/timezone") as f:
                return f.read().strip()
        # timedatectl is available on most modern systemd systems
        tz = subprocess.check_output(
            ["timedatectl", "show", "-p", "Timezone"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if "=" in tz:
            return tz.split("=", 1)[1]
        if tz:
            return tz
    except Exception:
        pass
    return "UTC"


def get_location(config):
    """
    Build an astral ``LocationInfo`` object from config values.

    Falls back to sensible defaults (New York City coordinates) for any
    missing keys. The timezone defaults to the system timezone when not
    specified in config.

    Args:
        config (dict): Parsed configuration dictionary.

    Returns:
        LocationInfo: Location object used for sunrise/sunset calculations.
    """
    city = config.get("city", "MyCity")
    country = config.get("country", "MyCountry")
    latitude = config.get("latitude", 40.7128)   # Default: NYC
    longitude = config.get("longitude", -74.0060)
    timezone = config.get("timezone") or get_system_timezone()
    return LocationInfo(city, country, timezone, latitude, longitude)


def local_to_utc_hm(hour, minute, tz_name):
    """
    Convert a local wall-clock time (today) to UTC hour and minute.

    Handles DST transitions: non-existent times (spring-forward) use the DST
    interpretation; ambiguous times (fall-back) use standard time.

    .. note::
        This function is retained for reference but is **no longer used** in
        the main scheduling flow. systemd ``OnCalendar=`` natively interprets
        times in the system's local timezone, so no UTC conversion is needed.

    Args:
        hour (int): Local hour (0–23).
        minute (int): Local minute (0–59).
        tz_name (str): IANA timezone name (e.g. ``"America/New_York"``).

    Returns:
        tuple[int, int]: ``(hour, minute)`` in UTC.
    """
    tz = pytz.timezone(tz_name)
    now = datetime.now(tz)
    naive_dt = datetime(now.year, now.month, now.day, hour, minute)
    try:
        local_dt = tz.localize(naive_dt, is_dst=None)
    except pytz.exceptions.NonExistentTimeError:
        # Clocks spring forward — this wall time doesn't exist; use DST side.
        local_dt = tz.localize(naive_dt, is_dst=True)
    except pytz.exceptions.AmbiguousTimeError:
        # Clocks fall back — time occurs twice; use standard-time side.
        local_dt = tz.localize(naive_dt, is_dst=False)
    utc_dt = local_dt.astimezone(pytz.utc)
    return utc_dt.hour, utc_dt.minute


def get_sunset_local_time(config):
    """
    Calculate today's sunset hour and minute in the configured local timezone.

    Applies the optional ``sunset_offset_minutes`` config value as a positive
    or negative offset from the actual sunset time.

    Args:
        config (dict): Parsed configuration dictionary.

    Returns:
        tuple[int, int]: ``(hour, minute)`` in local time of the (optionally
        offset) sunset.

    Raises:
        ValueError: If the sun never sets at this location today (polar day/
            night), which ``astral`` signals by raising ``ValueError``.
    """
    loc = get_location(config)
    tz_obj = pytz.timezone(loc.timezone)
    # astral raises ValueError if the sun never sets (polar day/night)
    s = sun(loc.observer, date=datetime.now().date(), tzinfo=tz_obj)
    sunset = s["sunset"]
    sunset_time = sunset + timedelta(minutes=config.get("sunset_offset_minutes", 0))
    sunset_local = sunset_time.astimezone(tz_obj)
    return sunset_local.hour, sunset_local.minute


# ---------------------------------------------------------------------------
# Config / schedule helpers
# ---------------------------------------------------------------------------

def sanitise_name(name):
    """
    Validate and sanitise a schedule name for safe use in systemd unit filenames.

    Replaces any character that is not alphanumeric, a hyphen, or an underscore
    with a hyphen, then strips leading/trailing hyphens.

    Args:
        name (str): Raw schedule name from config.

    Returns:
        str: Sanitised name safe for use as a systemd unit name suffix.

    Raises:
        ValueError: If *name* is empty or becomes empty after sanitisation.
    """
    if not name:
        raise ValueError("Schedule name must not be empty.")
    sanitised = re.sub(r"[^a-zA-Z0-9_-]", "-", str(name)).strip("-")
    if not sanitised:
        raise ValueError(
            f"Schedule name {name!r} is empty or invalid after sanitisation "
            "(only alphanumeric characters, hyphens, and underscores are allowed)."
        )
    return sanitised


def resolve_schedules(config):
    """
    Return the list of schedule entries from config, with backward compatibility.

    If ``schedules`` is absent but the legacy flat keys (``colors_url``,
    ``taps_url``, ``colors_time``) are present, they are synthesised into a
    ``schedules`` list and a deprecation warning is printed to ``stdout``.

    Args:
        config (dict): Parsed configuration dictionary.

    Returns:
        list[dict]: List of schedule entries, each containing ``"name"``,
        ``"audio_url"``, and ``"time"`` keys.
    """
    if "schedules" in config:
        return config["schedules"]

    # Backward compatibility: synthesise from legacy flat keys
    if "colors_url" in config or "taps_url" in config:
        print(
            "⚠️  DEPRECATION WARNING: 'colors_url', 'taps_url', and 'colors_time' are deprecated.\n"
            "   Please migrate to the 'schedules' array format in config.json\n"
            "   (re-run setup.sh → option 2 Reconfigure to auto-migrate)."
        )
        schedules = []
        if "colors_url" in config:
            schedules.append({
                "name": "colors",
                "audio_url": config["colors_url"],
                "time": config.get("colors_time", "08:00"),
            })
        if "taps_url" in config:
            schedules.append({
                "name": "taps",
                "audio_url": config["taps_url"],
                "time": "sunset",
            })
        return schedules

    return []


# ---------------------------------------------------------------------------
# Systemd unit file builders
# ---------------------------------------------------------------------------

def _write_unit_file(path, content):
    """
    Atomically write *content* to a systemd unit file at *path*.

    Writes to a temporary file in the same directory first, then uses
    ``os.replace()`` for an atomic rename. This ensures that a power cut or
    crash can never leave a half-written unit file that confuses systemd.

    Args:
        path (str): Destination path for the unit file.
        content (str): Text content to write.

    Raises:
        OSError: If the write or rename fails (e.g. permission denied).
    """
    dir_path = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        # Clean up the temp file on failure so it doesn't litter /etc/systemd/system/
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _build_service_unit(name, audio_url):
    """
    Return the content of a systemd ``.service`` unit that plays one audio file.

    The unit uses ``Type=oneshot`` (correct for a short-lived script that exits
    after playing), declares a dependency on network connectivity, and waits for
    ``flag-audio-http.service`` (the HTTP audio server) to be up before starting.

    A non-blocking ``flock`` on ``/run/flag.lock`` is used as a single-instance
    guard: if another scheduled play is already running, the second invocation
    exits immediately without playing (preventing overlapping group/stop errors).

    Args:
        name (str): Sanitised schedule name (used only in ``Description``).
        audio_url (str): Full HTTP URL of the MP3 to play.

    Returns:
        str: Unit file content ready to be written to disk.
    """
    return (
        "[Unit]\n"
        f"Description=Flag Audio — play {name}\n"
        "After=network-online.target flag-audio-http.service\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f'ExecStart=/usr/bin/flock -n /run/flag.lock {PYTHON_BIN} {SONOS_PLAY} "{audio_url}"\n'
        "User=root\n"
    )


def _build_timer_unit(name, hour, minute, persistent=True):
    """
    Return the content of a systemd ``.timer`` unit that fires at a given local time.

    ``Persistent=true`` ensures that a missed firing (e.g. because the Raspberry
    Pi was off) is executed on the next boot.  Set ``persistent=False`` for
    sunset-based schedules so that a missed sunset is **not** replayed later
    (e.g. at 02:00 after a reboot).  ``WantedBy=timers.target`` is required so
    that ``systemctl enable`` works correctly.

    systemd interprets ``OnCalendar=`` times in the system's local timezone
    (configured via ``/etc/localtime`` or ``TZ``), so no UTC conversion is needed
    as long as the system timezone is correctly set (which ``setup.sh`` ensures).

    Args:
        name (str): Sanitised schedule name (used only in ``Description``).
        hour (int): Local hour to fire (0–23).
        minute (int): Local minute to fire (0–59).
        persistent (bool): Whether to set ``Persistent=true`` in the timer unit.
            Defaults to ``True``.  Pass ``False`` for sunset-based schedules to
            prevent catch-up fires after a missed sunset.

    Returns:
        str: Unit file content ready to be written to disk.
    """
    persistent_val = "true" if persistent else "false"
    return (
        "[Unit]\n"
        f"Description=Flag Audio Timer — {name}\n"
        "\n"
        "[Timer]\n"
        f"OnCalendar=*-*-* {hour:02d}:{minute:02d}:00\n"
        f"Persistent={persistent_val}\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def _build_reschedule_service():
    """
    Return the content of the ``flag-reschedule.service`` unit.

    This service re-runs ``schedule_sonos.py`` daily to recalculate the
    sunset time and rewrite any sunset-based timer files.

    Returns:
        str: Unit file content ready to be written to disk.
    """
    return (
        "[Unit]\n"
        "Description=Flag Audio — daily reschedule (recalculate sunset timers)\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={PYTHON_BIN} {SCHEDULE_SCRIPT}\n"
        "User=root\n"
    )


def _build_reschedule_timer():
    """
    Return the content of the ``flag-reschedule.timer`` unit.

    Fires daily at 02:00 local time to recalculate sunset-based timers.
    ``Persistent=true`` ensures recalculation happens after a Raspberry Pi
    that was off at 02:00 boots up later in the day.

    Returns:
        str: Unit file content ready to be written to disk.
    """
    return (
        "[Unit]\n"
        "Description=Flag Audio Timer — daily reschedule at 02:00\n"
        "\n"
        "[Timer]\n"
        "OnCalendar=*-*-* 02:00:00\n"
        "Persistent=true\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


# ---------------------------------------------------------------------------
# Systemd management helpers
# ---------------------------------------------------------------------------

def _run_systemctl(*args):
    """
    Run a ``systemctl`` command and raise a clear error on failure.

    Args:
        *args (str): Arguments to pass to ``systemctl``
            (e.g. ``"daemon-reload"`` or ``"enable", "--now", "flag-colors.timer"``).

    Raises:
        RuntimeError: If ``systemctl`` exits with a non-zero return code,
            with the command, exit code, and captured stderr included in the
            error message.
    """
    cmd = ["systemctl"] + list(args)
    _log.debug("Running: systemctl %s", " ".join(args))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        _log.debug(
            "systemctl %s failed (exit %d): %s",
            " ".join(args), result.returncode, result.stderr.strip(),
        )
        raise RuntimeError(
            f"systemctl {' '.join(args)} failed (exit {result.returncode}):\n"
            f"{result.stderr.strip()}"
        )
    _log.debug("systemctl %s: OK", " ".join(args))


def _is_timer_enabled(timer):
    """
    Return ``True`` if *timer* is already enabled in systemd.

    Uses ``systemctl is-enabled`` which exits with code 0 for ``"enabled"``
    and non-zero for ``"disabled"``, ``"static"``, etc.

    Args:
        timer (str): Unit name, e.g. ``"flag-taps.timer"``.

    Returns:
        bool: ``True`` if the timer is enabled, ``False`` otherwise.
    """
    result = subprocess.run(
        ["systemctl", "is-enabled", timer],
        capture_output=True,
        text=True,
    )
    enabled = result.returncode == 0
    _log.debug("Timer %s is %s", timer, "enabled" if enabled else "not enabled")
    return enabled


def _is_reschedule_run(schedule_names):
    """
    Return ``True`` if all schedule timers are already enabled (nightly reschedule run).

    When all ``flag-{name}.timer`` units are already enabled, this script is being
    invoked by the nightly ``flag-reschedule.timer`` to recalculate sunset times.
    In this mode the write→daemon-reload sequence is used for sunset timers: the new
    unit file is written atomically and then ``daemon-reload`` is called.  The
    already-active sunset timer is left alone — systemd re-reads the updated unit
    file on ``daemon-reload`` and re-arms the *active* timer with the new
    ``OnCalendar`` value automatically.  No ``stop``/``start`` is required.

    NOTE: the previous approach (PR #43) used stop→write→reload→start for sunset
    timers.  That caused the timer to fire immediately after ``systemctl start``,
    even with ``Persistent=false``, because systemd's internal next-elapse
    calculation can treat today's ``OnCalendar`` event as missed immediately after
    a stop+reload cycle.  Leaving the timer active and relying on ``daemon-reload``
    re-arming is the correct fix.

    Fixed-time timers are restarted normally (their ``OnCalendar``, e.g. 08:00, has
    not elapsed at the 02:00 reschedule time, so a restart is safe).
    ``flag-reschedule.timer`` is never restarted (to avoid the self-referential
    restart that triggers a catch-up fire at 02:00).

    When one or more schedule timers are *not* yet enabled, this is a first-install
    run; use ``enable --now`` for all timers including ``flag-reschedule.timer``.

    Args:
        schedule_names (list[str]): Sanitised schedule names to check.

    Returns:
        bool: ``True`` if all ``flag-{name}.timer`` units are enabled.
    """
    if not schedule_names:
        return False
    return all(_is_timer_enabled(f"flag-{name}.timer") for name in schedule_names)


def _clean_stale_units(current_names):
    """
    Disable and remove any ``flag-*.timer`` / ``flag-*.service`` unit files
    that are no longer represented in the active schedule.

    This handles the case where a schedule entry is removed from config.json —
    the corresponding unit files are disabled, stopped, and deleted so they
    do not continue to fire.

    Reserved units (``flag-audio-http``, ``flag-reschedule``) are always
    skipped.

    Args:
        current_names (set[str]): Set of sanitised schedule names that should
            remain active (e.g. ``{"colors", "taps"}``).
    """
    for suffix in (".timer", ".service"):
        pattern = os.path.join(SYSTEMD_DIR, f"flag-*{suffix}")
        for unit_path in _glob.glob(pattern):
            unit_file = os.path.basename(unit_path)
            # Extract the inner name: "flag-colors.timer" → "colors"
            inner_name = unit_file[len("flag-"):-len(suffix)]
            if inner_name in _RESERVED_NAMES:
                # Never touch reserved units (audio-http, reschedule)
                continue
            if inner_name not in current_names:
                # Best-effort disable/stop before removal
                subprocess.run(
                    ["systemctl", "disable", "--now", unit_file],
                    capture_output=True,
                    text=True,
                )
                try:
                    os.remove(unit_path)
                    print(f"  🗑️  Removed stale unit: {unit_file}")
                except OSError as exc:
                    print(f"  ⚠️  Could not remove {unit_path}: {exc}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    """
    Load config, generate systemd unit files for all schedules, and enable timers.

    Steps:

    1. Verify the process is running as root (required to write
       ``/etc/systemd/system/``).
    2. Load ``config.json`` and resolve the ``schedules`` list (with
       backward-compatibility for the old flat-key format).
    3. Detect whether this is a reschedule run (all timers already enabled)
       or a first-install run.
    4. For reschedule runs, write new sunset timer unit files and run
       ``daemon-reload`` — the already-active sunset timer is re-armed
       automatically.  No ``stop``/``start`` is needed or safe to use.
    5. For each schedule, compute the fire time (fixed HH:MM or today's
       sunset in local time) and write a ``.service`` + ``.timer`` pair
       atomically.
    6. Write the ``flag-reschedule`` service/timer pair (daily at 02:00).
    7. Remove any stale ``flag-*.timer`` / ``flag-*.service`` unit files that
       are no longer in the current schedule list.
    8. Run ``systemctl daemon-reload``; print a clear error and exit on failure.
    9. Activate timers:

       - **Reschedule run**: restart fixed-time timers normally.  Sunset timers
         are left active — ``daemon-reload`` re-arms an already-active timer
         with the new ``OnCalendar`` value automatically, so no ``stop``/``start``
         is required.  (The previous PR #43 approach of stop→write→reload→start
         caused the timer to fire immediately after ``systemctl start``, even with
         ``Persistent=false``, due to systemd's next-elapse recalculation after a
         stop+reload cycle.)  ``flag-reschedule.timer`` is also skipped — it is
         always hardcoded to 02:00 and restarting your own parent timer with
         ``Persistent=true`` at the exact ``OnCalendar`` time can cause systemd to
         treat the just-elapsed event as "missed" and fire again.
       - **First-install run**: run ``systemctl enable --now`` for all timers
         including ``flag-reschedule.timer``.

    10. Print a summary of installed timers.

    Raises:
        SystemExit: On ``daemon-reload`` failure, duplicate schedule names,
            or if the process is not running as root.
    """
    # Require root so we can write to /etc/systemd/system/
    if os.getuid() != 0:
        sys.exit(
            f"❌ schedule_sonos.py must be run as root (needs write access to {SYSTEMD_DIR}).\n"
            f"   Try: sudo {sys.executable} {os.path.abspath(__file__)}"
        )

    config = load_config()

    # Apply debug log level before anything else so early messages are captured
    if config.get("debug", False):
        logging.getLogger().setLevel(logging.DEBUG)
        _log.debug("Debug logging enabled via config.json 'debug': true")

    # Ensure timezone is set in config so all downstream functions use the same value
    if not config.get("timezone"):
        config["timezone"] = get_system_timezone()
    tz_name = config["timezone"]

    _log.info("schedule_sonos.py started (timezone=%s)", tz_name)

    schedules = resolve_schedules(config)
    if not schedules:
        _log.warning("No schedules found in config.json. Nothing to schedule.")
        print("⚠️  No schedules found in config.json. Nothing to schedule.")
        return

    # --- Validate names and check for duplicates ---
    seen_names: set[str] = set()
    processed = []
    for entry in schedules:
        raw_name = entry.get("name", "")
        name = sanitise_name(raw_name)   # raises ValueError if invalid
        if name in seen_names:
            sys.exit(
                f"❌ Duplicate schedule name '{name}'. "
                "Each schedule entry must have a unique name."
            )
        seen_names.add(name)
        audio_url = entry.get("audio_url")
        time_val = entry.get("time")
        if not audio_url:
            print(f"  ⚠️  Skipping '{name}': missing required 'audio_url' field in schedule entry.")
            continue
        if not time_val:
            print(f"  ⚠️  Skipping '{name}': missing required 'time' field in schedule entry.")
            continue
        processed.append({
            "name": name,
            "audio_url": audio_url,
            "time": time_val,
        })

    # --- Detect reschedule vs first-install mode ---
    # If all schedule timers are already enabled this is a nightly reschedule
    # run (invoked by flag-reschedule.timer).  Otherwise it is a first install.
    processed_names = [entry["name"] for entry in processed]
    is_reschedule = _is_reschedule_run(processed_names)
    if is_reschedule:
        _log.info(
            "Run reason: reschedule — all %d schedule timer(s) already enabled; "
            "will write updated unit files and daemon-reload (already-active sunset "
            "timer re-armed by daemon-reload), skip flag-reschedule.timer restart",
            len(processed_names),
        )
    else:
        _log.info(
            "Run reason: first install — one or more schedule timers not yet "
            "enabled; will use 'enable --now' for all timers including "
            "flag-reschedule.timer"
        )

    print("Writing systemd unit files...")

    # --- Generate a service + timer pair for each schedule entry ---
    written_names: set[str] = set()
    # Maps schedule name → (hour, minute) for sunset-based timers so that the
    # activation step can compare against the current time.
    sunset_times: dict[str, tuple[int, int]] = {}
    for entry in processed:
        name = entry["name"]
        audio_url = entry["audio_url"]
        time_str = entry["time"]

        # Resolve the fire time to (hour, minute) in local time
        if time_str == "sunset":
            try:
                hour, minute = get_sunset_local_time(config)
            except ValueError as exc:
                # Polar day/night: the sun never sets — skip this timer
                print(
                    f"  ⚠️  Skipping '{name}': cannot compute sunset for today — {exc}"
                )
                _log.warning("Skipping '%s': cannot compute sunset for today — %s", name, exc)
                continue
            _log.info(
                "Schedule '%s': sunset at %02d:%02d %s "
                "(OnCalendar=*-*-* %02d:%02d:00, Persistent=false)",
                name, hour, minute, tz_name, hour, minute,
            )
        else:
            try:
                parts = time_str.split(":")
                if len(parts) != 2:
                    raise ValueError("Expected HH:MM format")
                hour, minute = int(parts[0]), int(parts[1])
                if not (0 <= hour <= 23 and 0 <= minute <= 59):
                    raise ValueError(f"Time out of range: {time_str}")
            except (ValueError, AttributeError):
                print(
                    f"  ⚠️  Skipping '{name}': invalid time format '{time_str}' "
                    "(expected HH:MM with hour 0–23 and minute 0–59, or 'sunset')"
                )
                _log.warning(
                    "Skipping '%s': invalid time format '%s'", name, time_str,
                )
                continue
            _log.info(
                "Schedule '%s': fixed time %s %s "
                "(OnCalendar=*-*-* %02d:%02d:00, Persistent=true)",
                name, time_str, tz_name, hour, minute,
            )

        service_path = os.path.join(SYSTEMD_DIR, f"flag-{name}.service")
        timer_path = os.path.join(SYSTEMD_DIR, f"flag-{name}.timer")

        timer_content = _build_timer_unit(name, hour, minute, persistent=(time_str != "sunset"))

        # Atomic writes — if either raises, the exception propagates to the caller
        _write_unit_file(service_path, _build_service_unit(name, audio_url))
        _write_unit_file(timer_path, timer_content)
        _log.debug("Wrote timer unit %s:\n%s", timer_path, timer_content.rstrip())

        written_names.add(name)
        if time_str == "sunset":
            sunset_times[name] = (hour, minute)
        time_display = (
            f"{hour:02d}:{minute:02d} {tz_name}" if time_str == "sunset"
            else f"{time_str} {tz_name}"
        )
        print(
            f"  ✅ {name}: scheduled at {time_display} "
            f"(flag-{name}.timer → flag-{name}.service)"
        )

    # --- Write the daily reschedule service/timer pair ---
    _write_unit_file(
        os.path.join(SYSTEMD_DIR, "flag-reschedule.service"),
        _build_reschedule_service(),
    )
    _write_unit_file(
        os.path.join(SYSTEMD_DIR, "flag-reschedule.timer"),
        _build_reschedule_timer(),
    )
    print(
        "  ✅ Reschedule: daily timer at 02:00 local time "
        "(flag-reschedule.timer → flag-reschedule.service)"
    )

    # --- Remove stale unit files from previous runs ---
    _clean_stale_units(written_names)

    # --- Reload systemd daemon so it picks up the new/changed unit files ---
    # All unit files must be written before daemon-reload and enable steps.
    _log.info("Running daemon-reload")
    try:
        _run_systemctl("daemon-reload")
    except RuntimeError as exc:
        print(f"\n  ❌ daemon-reload failed — check unit file syntax:\n  {exc}")
        _log.error("daemon-reload failed: %s", exc)
        sys.exit(1)

    # --- Activate timers ---
    if is_reschedule:
        # Reschedule run: restart fixed-time timers normally and skip sunset
        # timers entirely.
        #
        # Sunset timers are left active across the unit-file rewrite.  systemd
        # re-reads the updated unit file on daemon-reload and re-arms an
        # *active* timer with the new OnCalendar value automatically — no
        # stop/start is required or safe to use.
        #
        # The previous PR #43 approach (stop→write→reload→start) caused the
        # timer to fire immediately after "systemctl start", even with
        # Persistent=false, because systemd's internal next-elapse calculation
        # can treat today's OnCalendar event as missed immediately after a
        # stop+reload cycle.  Leaving the timer active and relying on
        # daemon-reload re-arming is the correct fix.
        for name in sorted(written_names):
            timer_name = f"flag-{name}.timer"
            if name in sunset_times:
                # Sunset timer: already-active timer re-armed by daemon-reload.
                # Do NOT stop/start — that would cause spurious immediate fire.
                sun_hour, sun_minute = sunset_times[name]
                _log.info(
                    "  ✅ %s: unit updated (OnCalendar=%02d:%02d); "
                    "already-active timer re-armed by daemon-reload",
                    timer_name, sun_hour, sun_minute,
                )
                print(
                    f"  ✅ {timer_name}: unit updated (OnCalendar={sun_hour:02d}:{sun_minute:02d}); "
                    f"already-active timer re-armed by daemon-reload"
                )
            else:
                # Fixed-time timer: safe to restart because OnCalendar (e.g.
                # 08:00) has not yet elapsed at the 02:00 reschedule time.
                _log.info("Restarting %s (fixed-time timer)", timer_name)
                try:
                    _run_systemctl("restart", timer_name)
                    print(f"  ✅ Restarted (already enabled): {timer_name}")
                    _log.info("Restarted %s successfully", timer_name)
                except RuntimeError as exc:
                    print(f"  ⚠️  Could not restart {timer_name}: {exc}")
                    _log.error("Could not restart %s: %s", timer_name, exc)

        # Skip flag-reschedule.timer — it is hardcoded to 02:00 and never
        # changes, so there is nothing to update.  More importantly, restarting
        # your own parent timer with Persistent=true at the exact OnCalendar
        # time causes systemd to clear the last-trigger timestamp and may fire
        # the timer again immediately as a catch-up.
        _log.info(
            "Skipping flag-reschedule.timer restart (reschedule run — "
            "hardcoded 02:00 schedule never changes)"
        )
        print("  ✅ flag-reschedule.timer: no restart needed (reschedule run)")
    else:
        # First-install run: enable and start every timer, including the
        # reschedule timer so the nightly 02:00 recalculation is registered.
        timers_to_activate = [f"flag-{name}.timer" for name in sorted(written_names)]
        timers_to_activate.append("flag-reschedule.timer")
        for timer in timers_to_activate:
            _log.info("Enabling %s (first install)", timer)
            try:
                _run_systemctl("enable", "--now", timer)
                print(f"  ✅ Enabled and started: {timer}")
                _log.info("Enabled %s successfully", timer)
            except RuntimeError as exc:
                print(f"  ⚠️  Could not activate {timer}: {exc}")
                _log.error("Could not enable %s: %s", timer, exc)

    # --- Summary ---
    print("")
    print("Installed systemd timers:")
    for name in sorted(written_names):
        print(f"  flag-{name}.timer  →  flag-{name}.service")
    print("  flag-reschedule.timer  →  flag-reschedule.service")
    print("")
    print("To verify:   systemctl list-timers --all | grep flag")
    first_name = sorted(written_names)[0] if written_names else "colors"
    print(f"To inspect:  journalctl -u flag-{first_name} -n 50")
    _log.info("schedule_sonos.py completed successfully")


if __name__ == "__main__":
    main()
