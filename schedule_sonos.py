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

Schedule ``time`` field accepted values
----------------------------------------
- ``"HH:MM"`` — fixed 24-hour local time (hour 0–23, minute 0–59).
- ``"sunset"`` — today's sunset in local time, offset by ``sunset_offset_minutes``
  from config.json.
- ``"sunset±Nmin"`` — sunset plus or minus N minutes (e.g. ``"sunset-5min"``,
  ``"sunset+1min"``).  N must be a positive integer in the range 1–720.
  Sunset-offset timers are treated the same as plain ``"sunset"`` timers for
  reschedule purposes: they are re-armed daily at 02:00 by ``flag-reschedule.timer``
  and on every boot by ``flag-boot-reschedule.service`` without a stop/start cycle.

Multi-speaker note
------------------
This script only generates the unit files that *invoke* ``sonos_play.py``.  The
actual multi-speaker playback logic (speaker discovery, temporary group formation,
synchronized play, and state restoration) lives entirely in ``sonos_play.py``.
The ``speakers`` list from config.json is passed through unchanged to each service
unit so that ``sonos_play.py`` receives it at runtime.
"""

import glob as _glob
import logging
import os
import re
import shlex
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
_RESERVED_NAMES = {"audio-http", "reschedule", "boot-reschedule"}

# Regex for sunset-offset time strings like "sunset-5min" or "sunset+1min".
# re.IGNORECASE allows "Sunset-5min", "SUNSET+1MIN", etc.
_SUNSET_OFFSET_RE = re.compile(r"^sunset([+-])(\d+)min$", re.IGNORECASE)


def parse_sunset_offset(time_str: str):
    """Return signed offset in minutes for 'sunset+Nmin' / 'sunset-Nmin', or None.

    Args:
        time_str (str): The raw time string from a schedule entry.

    Returns:
        int | None: Signed offset in minutes, or ``None`` if *time_str* does
        not match the ``sunset±Nmin`` pattern.

    Raises:
        ValueError: If *time_str* matches the pattern but N is out of range
            (must be 1–720; use plain ``'sunset'`` for zero offset).
    """
    m = _SUNSET_OFFSET_RE.match(time_str.strip())
    if not m:
        return None
    sign, mins = m.group(1), int(m.group(2))
    if mins < 1 or mins > 720:
        raise ValueError(
            f"Sunset offset out of range: '{time_str}' (must be 1-720 minutes)"
        )
    return -mins if sign == "-" else mins


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


def get_sunset_local_time(config):
    """
    Calculate today's sunset hour and minute in the configured local timezone.

    Applies the optional ``sunset_offset_minutes`` config value as a positive
    or negative offset from the actual sunset time.  This offset is used only
    when the schedule entry's ``time`` is the plain ``"sunset"`` string; it is
    **not** applied when ``"sunset±Nmin"`` entries are used (see
    :func:`get_sunset_local_time_with_offset`).

    Args:
        config (dict): Parsed configuration dictionary.

    Returns:
        tuple[int, int]: ``(hour, minute)`` in local time of the (optionally
        offset) sunset.

    Raises:
        ValueError: If the sun never sets at this location today (polar day/
            night), which ``astral`` signals by raising ``ValueError``.
        ValueError: If the offset pushes the resulting time to a different
            calendar day than today's sunset (midnight wrap-around).
    """
    loc = get_location(config)
    tz_obj = pytz.timezone(loc.timezone)
    # astral raises ValueError if the sun never sets (polar day/night)
    s = sun(loc.observer, date=datetime.now().date(), tzinfo=tz_obj)
    sunset = s["sunset"]
    sunset_time = sunset + timedelta(minutes=config.get("sunset_offset_minutes", 0))
    sunset_local = sunset_time.astimezone(tz_obj)
    sunset_unadjusted_local = sunset.astimezone(tz_obj)
    if sunset_local.date() != sunset_unadjusted_local.date():
        raise ValueError(
            f"Sunset offset crosses midnight in {tz_obj.zone}: "
            f"sunset is {sunset_unadjusted_local:%Y-%m-%d %H:%M} but "
            f"adjusted time is {sunset_local:%Y-%m-%d %H:%M}"
        )
    return sunset_local.hour, sunset_local.minute


def get_sunset_local_time_with_offset(config, extra_offset_minutes: int):
    """
    Calculate today's sunset hour and minute with a per-entry signed offset.

    This function is used for schedule entries whose ``time`` field uses the
    ``"sunset±Nmin"`` syntax (e.g. ``"sunset-5min"`` or ``"sunset+1min"``).
    It applies **only** ``extra_offset_minutes`` to the true sunset time;
    the top-level ``sunset_offset_minutes`` config value is intentionally
    **ignored** so that the N in ``"sunset±Nmin"`` is always an absolute
    offset from the actual sunset, regardless of any global config offset.

    The top-level ``sunset_offset_minutes`` applies exclusively to plain
    ``"sunset"`` entries (handled by :func:`get_sunset_local_time`).

    Args:
        config (dict): Parsed configuration dictionary.
        extra_offset_minutes (int): Signed offset in minutes from true sunset
            (negative = before sunset, positive = after sunset).

    Returns:
        tuple[int, int]: ``(hour, minute)`` in local time of the adjusted sunset.

    Raises:
        ValueError: If the sun never sets at this location today (polar day/
            night), which ``astral`` signals by raising ``ValueError``.
        ValueError: If the offset pushes the resulting time to a different
            calendar day than today's sunset (midnight wrap-around).
    """
    loc = get_location(config)
    tz_obj = pytz.timezone(loc.timezone)
    s = sun(loc.observer, date=datetime.now().date(), tzinfo=tz_obj)
    sunset = s["sunset"]
    sunset_time = sunset + timedelta(minutes=extra_offset_minutes)
    sunset_local = sunset_time.astimezone(tz_obj)
    sunset_unadjusted_local = sunset.astimezone(tz_obj)
    if sunset_local.date() != sunset_unadjusted_local.date():
        raise ValueError(
            f"Sunset offset crosses midnight in {tz_obj.zone}: "
            f"sunset is {sunset_unadjusted_local:%Y-%m-%d %H:%M} but "
            f"adjusted time is {sunset_local:%Y-%m-%d %H:%M}"
        )
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
        schedules = config["schedules"]
        if not isinstance(schedules, list) or not schedules:
            _log.error(
                "Config 'schedules' must be a non-empty list; got %r. Nothing to schedule.",
                schedules,
            )
            print("❌ Config 'schedules' must be a non-empty list. Nothing to schedule.")
            return []
        return schedules

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

def _unit_file_content_matches(path: str, new_content: str) -> bool:
    """
    Return True if the file at *path* exists and its contents exactly equal *new_content*.

    Used as a guard before calling :func:`_write_unit_file` to avoid unnecessary
    writes (and downstream ``systemctl restart`` calls) when the unit file content
    has not changed.

    Args:
        path (str): Absolute path to the unit file.
        new_content (str): The new content to compare against.

    Returns:
        bool: ``True`` if the file exists and its contents are identical to
        *new_content*; ``False`` if the file is absent or differs in any way.
    """
    try:
        with open(path) as f:
            return f.read() == new_content
    except OSError:
        return False


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
        f"ExecStart=/usr/bin/flock -n /run/flag.lock {PYTHON_BIN} {SONOS_PLAY} {shlex.quote(audio_url)}\n"
        "User=root\n"
    )


def _build_timer_unit(name, hour, minute):
    """
    Return the content of a systemd ``.timer`` unit that fires at a given local time.

    ``Persistent=false`` ensures that missed firings are never replayed — a missed
    scheduled play is simply skipped, never replayed late.  ``WantedBy=timers.target``
    is required so that ``systemctl enable`` works correctly.

    systemd interprets ``OnCalendar=`` times in the system's local timezone
    (configured via ``/etc/localtime`` or ``TZ``), so no UTC conversion is needed
    as long as the system timezone is correctly set (which ``setup.sh`` ensures).

    Args:
        name (str): Sanitised schedule name (used only in ``Description``).
        hour (int): Local hour to fire (0–23).
        minute (int): Local minute to fire (0–59).

    Returns:
        str: Unit file content ready to be written to disk.
    """
    return (
        "[Unit]\n"
        f"Description=Flag Audio Timer — {name}\n"
        "\n"
        "[Timer]\n"
        f"OnCalendar=*-*-* {hour:02d}:{minute:02d}:00\n"
        "Persistent=false\n"
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
    ``Persistent=false`` ensures a missed 02:00 recalculation is not replayed
    later — boot-time recovery is handled by ``flag-boot-reschedule.service``
    instead.

    Returns:
        str: Unit file content ready to be written to disk.
    """
    return (
        "[Unit]\n"
        "Description=Flag Audio Timer — daily reschedule at 02:00\n"
        "\n"
        "[Timer]\n"
        "OnCalendar=*-*-* 02:00:00\n"
        "Persistent=false\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
def _build_boot_reschedule_service(schedule_names=None):
    """
    Return the content of the ``flag-boot-reschedule.service`` unit.

    This oneshot service runs once on every boot (after the network is up) to
    recompute today's sunset time and rewrite any sunset-based timer unit files.
    Combined with ``Persistent=false`` on all timers, this ensures that after any
    outage or reboot, schedules resume at their next correct fire time and no
    missed play is ever replayed late.

    Args:
        schedule_names (list[str] | None): Sanitised schedule names used to
            generate the ``Before=`` ordering constraint so that this service
            completes before the named schedule timers can fire.  Both ``None``
            and an empty list result in the ``Before=`` line being omitted.

    Returns:
        str: Unit file content ready to be written to disk.
    """
    before_line = ""
    if schedule_names:
        before_units = " ".join(f"flag-{n}.timer" for n in schedule_names)
        before_line = f"Before={before_units}\n"
    return (
        "[Unit]\n"
        "Description=Flag Audio — recompute sunset timers on boot\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        + before_line +
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={PYTHON_BIN} {SCHEDULE_SCRIPT}\n"
        "RemainAfterExit=no\n"
        "User=root\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


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

    Returns:
        bool: ``True`` if at least one stale unit file was removed.
    """
    removed_any = False
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
                    removed_any = True
                    print(f"  🗑️  Removed stale unit: {unit_file}")
                except OSError as exc:
                    print(f"  ⚠️  Could not remove {unit_path}: {exc}")
    return removed_any


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
    7. Write the ``flag-boot-reschedule.service`` oneshot unit (runs on boot).
    8. Remove any stale ``flag-*.timer`` / ``flag-*.service`` unit files that
       are no longer in the current schedule list.
    9. Run ``systemctl daemon-reload``; print a clear error and exit on failure.
    10. Activate timers:

       - **Reschedule run**: restart fixed-time timers normally.  Sunset timers
         are left active — ``daemon-reload`` re-arms an already-active timer
         with the new ``OnCalendar`` value automatically, so no ``stop``/``start``
         is required.  (The previous PR #43 approach of stop→write→reload→start
         caused the timer to fire immediately after ``systemctl start``, even with
         ``Persistent=false``, due to systemd's next-elapse recalculation after a
         stop+reload cycle.)  ``flag-reschedule.timer`` is also skipped — it is
         always hardcoded to 02:00 and restarting your own parent timer at the
         exact ``OnCalendar`` time can cause systemd to treat the just-elapsed
         event as "missed" and fire again.
       - **First-install run**: run ``systemctl enable --now`` for all timers
         including ``flag-reschedule.timer``, and ``systemctl enable`` (no
         ``--now``) for ``flag-boot-reschedule.service``.

    11. Print a summary of installed timers.

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
        if not isinstance(audio_url, str) or not (
            audio_url.startswith("http://") or audio_url.startswith("https://")
        ):
            _log.warning("Skipping '%s': audio_url %r is not a valid http/https URL.", name, audio_url)
            print(f"  ⚠️  Skipping '{name}': audio_url must start with http:// or https://.")
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
    # changed_units: schedule names whose unit files actually differed from disk.
    # Only these timers will be restarted in the reschedule branch.
    changed_units: set[str] = set()
    # any_file_written: True if any unit file was actually written this run.
    # Used to decide whether daemon-reload is needed.
    any_file_written: bool = False
    # Maps schedule name → (hour, minute) for sunset-based timers so that the
    # activation step can compare against the current time.
    sunset_times: dict[str, tuple[int, int]] = {}
    for entry in processed:
        name = entry["name"]
        audio_url = entry["audio_url"]
        time_str = entry["time"]

        # Normalise once for all comparisons and parsing.  The original
        # time_str is kept intact for user-facing display output so that
        # the casing/spacing the user wrote in config.json is echoed back.
        time_str_normalized = time_str.strip().lower() if isinstance(time_str, str) else time_str

        # Resolve the fire time to (hour, minute) in local time.
        # Pre-compute sunset offset (None if not a sunset-offset string) so we
        # don't run the regex twice and can propagate ValueError cleanly.
        try:
            _sunset_offset = parse_sunset_offset(time_str_normalized)
        except ValueError as exc:
            print(f"  ⚠️  Skipping '{name}': {exc}")
            _log.warning("Skipping '%s': %s", name, exc)
            continue
        except AttributeError:
            print(
                f"  ⚠️  Skipping '{name}': invalid time value '{time_str}' (expected a string)"
            )
            _log.warning("Skipping '%s': time value is not a string: %r", name, time_str)
            continue
        is_sunset_based = (time_str_normalized == "sunset") or (_sunset_offset is not None)
        if time_str_normalized == "sunset":
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
        elif _sunset_offset is not None:
            try:
                hour, minute = get_sunset_local_time_with_offset(config, _sunset_offset)
            except ValueError as exc:
                print(
                    f"  ⚠️  Skipping '{name}': cannot compute sunset for today — {exc}"
                )
                _log.warning("Skipping '%s': cannot compute sunset for today — %s", name, exc)
                continue
            _log.info(
                "Schedule '%s': %s → %02d:%02d %s "
                "(OnCalendar=*-*-* %02d:%02d:00, Persistent=false)",
                name, time_str, hour, minute, tz_name, hour, minute,
            )
        else:
            try:
                parts = time_str_normalized.split(":")
                if len(parts) != 2:
                    raise ValueError("Expected HH:MM format")
                hour, minute = int(parts[0]), int(parts[1])
                if not (0 <= hour <= 23 and 0 <= minute <= 59):
                    raise ValueError(f"Time out of range: {time_str}")
            except (ValueError, AttributeError):
                print(
                    f"  ⚠️  Skipping '{name}': invalid time format '{time_str}' "
                    "(expected HH:MM with hour 0–23 and minute 0–59, 'sunset', or "
                    "'sunset±Nmin' (e.g. 'sunset-5min'))"
                )
                _log.warning(
                    "Skipping '%s': invalid time format '%s'", name, time_str,
                )
                continue
            _log.info(
                "Schedule '%s': fixed time %s %s "
                "(OnCalendar=*-*-* %02d:%02d:00, Persistent=false)",
                name, time_str, tz_name, hour, minute,
            )

        service_path = os.path.join(SYSTEMD_DIR, f"flag-{name}.service")
        timer_path = os.path.join(SYSTEMD_DIR, f"flag-{name}.timer")

        service_content = _build_service_unit(name, audio_url)
        timer_content = _build_timer_unit(name, hour, minute)

        # Only write unit files when content has actually changed to avoid
        # unnecessary daemon-reload cycles.
        unit_changed = False
        if not _unit_file_content_matches(service_path, service_content):
            _write_unit_file(service_path, service_content)
            unit_changed = True
            any_file_written = True
        if not _unit_file_content_matches(timer_path, timer_content):
            _write_unit_file(timer_path, timer_content)
            _log.debug("Wrote timer unit %s:\n%s", timer_path, timer_content.rstrip())
            unit_changed = True
            any_file_written = True
        if unit_changed:
            changed_units.add(name)

        written_names.add(name)
        if is_sunset_based:
            sunset_times[name] = (hour, minute)
        time_display = (
            f"{time_str} → {hour:02d}:{minute:02d} {tz_name}" if is_sunset_based
            else f"{time_str} {tz_name}"
        )
        print(
            f"  ✅ {name}: scheduled at {time_display} "
            f"(flag-{name}.timer → flag-{name}.service)"
        )

    # --- Write the daily reschedule service/timer pair ---
    reschedule_svc_content = _build_reschedule_service()
    reschedule_timer_content = _build_reschedule_timer()
    reschedule_svc_path = os.path.join(SYSTEMD_DIR, "flag-reschedule.service")
    reschedule_timer_path = os.path.join(SYSTEMD_DIR, "flag-reschedule.timer")
    if not _unit_file_content_matches(reschedule_svc_path, reschedule_svc_content):
        _write_unit_file(reschedule_svc_path, reschedule_svc_content)
        any_file_written = True
    if not _unit_file_content_matches(reschedule_timer_path, reschedule_timer_content):
        _write_unit_file(reschedule_timer_path, reschedule_timer_content)
        any_file_written = True
    print(
        "  ✅ Reschedule: daily timer at 02:00 local time "
        "(flag-reschedule.timer → flag-reschedule.service)"
    )

    # --- Write the boot-time reschedule service ---
    boot_reschedule_svc_content = _build_boot_reschedule_service(sorted(written_names))
    boot_reschedule_svc_path = os.path.join(SYSTEMD_DIR, "flag-boot-reschedule.service")
    if not _unit_file_content_matches(boot_reschedule_svc_path, boot_reschedule_svc_content):
        _write_unit_file(boot_reschedule_svc_path, boot_reschedule_svc_content)
        any_file_written = True
    print(
        "  ✅ Boot-reschedule: recompute sunset on boot "
        "(flag-boot-reschedule.service)"
    )

    # --- Remove stale unit files from previous runs ---
    stale_removed = _clean_stale_units(written_names)

    # --- Reload systemd daemon so it picks up the new/changed unit files ---
    # Only reload when at least one unit file was actually written or a stale
    # unit was removed; skipping daemon-reload when nothing changed prevents
    # systemd from unnecessarily clearing timer timestamps.
    if any_file_written or stale_removed:
        _log.info("Running daemon-reload")
        try:
            _run_systemctl("daemon-reload")
        except RuntimeError as exc:
            print(f"\n  ❌ daemon-reload failed — check unit file syntax:\n  {exc}")
            _log.error("daemon-reload failed: %s", exc)
            sys.exit(1)
    else:
        _log.info("Skipping daemon-reload: no unit files changed and no stale units removed")

    # --- Activate timers ---
    if is_reschedule:
        # Reschedule run: restart fixed-time timers only when their unit file
        # actually changed.  Skipping the restart when content is unchanged
        # avoids an unnecessary timer reset — if the stamp is still valid there
        # is no reason to reset it with a restart.
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
                if name in changed_units:
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
                    _log.info("  %s: unchanged, no action needed", timer_name)
                    print(f"  ✅ {timer_name}: unchanged, no action needed")
            else:
                # Fixed-time timer: safe to restart when OnCalendar (e.g. 08:00)
                # has not yet elapsed at the 02:00 reschedule time, BUT only
                # restart if the unit file actually changed.  An unchanged unit
                # file means the stamp is still valid — restarting would clear it.
                if name in changed_units:
                    _log.info("Restarting %s (fixed-time timer)", timer_name)
                    try:
                        _run_systemctl("restart", timer_name)
                        print(f"  ✅ Restarted (already enabled): {timer_name}")
                        _log.info("Restarted %s successfully", timer_name)
                    except RuntimeError as exc:
                        print(f"  ⚠️  Could not restart {timer_name}: {exc}")
                        _log.error("Could not restart %s: %s", timer_name, exc)
                else:
                    _log.info("%s: unit file unchanged, skipping restart", timer_name)
                    print(f"  ✅ {timer_name}: unit file unchanged, skipping restart")

        # Skip flag-reschedule.timer — it is hardcoded to 02:00 and never
        # changes, so there is nothing to update.  Restarting your own parent
        # timer at the exact OnCalendar time causes systemd to clear the
        # last-trigger timestamp and may fire the timer again immediately.
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

        # Enable (but do not start) the boot-reschedule service — it is a
        # oneshot that runs on the next boot, not right now.
        _log.info("Enabling flag-boot-reschedule.service (first install)")
        try:
            _run_systemctl("enable", "flag-boot-reschedule.service")
            print("  ✅ Enabled: flag-boot-reschedule.service")
            _log.info("Enabled flag-boot-reschedule.service successfully")
        except RuntimeError as exc:
            print(f"  ⚠️  Could not enable flag-boot-reschedule.service: {exc}")
            _log.error("Could not enable flag-boot-reschedule.service: %s", exc)

    # --- Summary ---
    print("")
    print("Installed systemd timers:")
    for name in sorted(written_names):
        print(f"  flag-{name}.timer  →  flag-{name}.service")
    print("  flag-reschedule.timer  →  flag-reschedule.service")
    print("  flag-boot-reschedule.service  (oneshot on boot)")
    print("")
    print("To verify:   systemctl list-timers --all | grep flag")
    if written_names:
        first_name = sorted(written_names)[0]
        print(f"To inspect:  journalctl -u flag-{first_name} -n 50")
    else:
        print("To inspect:  journalctl -u 'flag-*' -n 50")
    _log.info("schedule_sonos.py completed successfully")


if __name__ == "__main__":
    main()
