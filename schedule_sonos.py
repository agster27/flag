#!/usr/bin/env python3
"""
schedule_sonos.py — Calculates today's sunset and writes the Sonos cron schedule.

Reads location and timezone from config.json, computes sunset time using the
astral library, and installs three cron jobs: Colors at a configured local time
(default 08:00), Taps at sunset (with an optional offset), and a daily
self-reschedule at 02:00 to keep the sunset time accurate.

All times are converted from the configured local timezone to UTC before being
written to cron, because cron always fires at the system clock time (UTC on
most servers).
"""
import os
import subprocess
from datetime import datetime, timedelta
from astral import LocationInfo
from astral.sun import sun
import pytz
from config import load_config, INSTALL_DIR

CRON_MARKER = "# [flag_sonos_autogen]"
PYTHON_BIN = os.path.join(INSTALL_DIR, "sonos-env", "bin", "python")
SONOS_PLAY = os.path.join(INSTALL_DIR, "sonos_play.py")

def get_system_timezone():
    """Try to determine the system timezone name."""
    try:
        # Try to read /etc/timezone (Debian/Ubuntu)
        if os.path.exists("/etc/timezone"):
            with open("/etc/timezone") as f:
                return f.read().strip()
        # Try timedatectl (works on many Linux systems)
        tz = subprocess.check_output(["timedatectl", "show", "-p", "Timezone"], text=True).strip()
        if "=" in tz:
            return tz.split("=", 1)[1]
    except Exception:
        pass
    # Fallback to UTC
    return "UTC"

def get_location(config):
    """
    Build an astral LocationInfo object from config values.

    Falls back to sensible defaults (New York City) for any missing keys.
    The timezone defaults to the system timezone if not specified in config.

    Args:
        config (dict): Parsed configuration dictionary.

    Returns:
        LocationInfo: Location object used for sunrise/sunset calculations.
    """
    # Fallbacks for missing config
    city = config.get("city", "MyCity")
    country = config.get("country", "MyCountry")
    latitude = config.get("latitude", 40.7128)  # Default: NYC
    longitude = config.get("longitude", -74.0060)
    timezone = config.get("timezone")
    if not timezone:
        timezone = get_system_timezone()
    return LocationInfo(city, country, timezone, latitude, longitude)

def local_to_utc_hm(hour, minute, tz_name):
    """
    Convert a local wall-clock time (today) to UTC hour and minute.

    Handles DST transitions: non-existent times (spring-forward) use the DST
    interpretation; ambiguous times (fall-back) use standard time.

    Args:
        hour (int): Local hour (0–23).
        minute (int): Local minute (0–59).
        tz_name (str): IANA timezone name (e.g. ``"America/New_York"``).

    Returns:
        tuple[int, int]: (hour, minute) in UTC.
    """
    tz = pytz.timezone(tz_name)
    now = datetime.now(tz)
    naive_dt = datetime(now.year, now.month, now.day, hour, minute)
    try:
        local_dt = tz.localize(naive_dt, is_dst=None)
    except pytz.exceptions.NonExistentTimeError:
        # Clocks spring forward — this wall time doesn't exist; use DST side of the gap.
        local_dt = tz.localize(naive_dt, is_dst=True)
    except pytz.exceptions.AmbiguousTimeError:
        # Clocks fall back — time occurs twice; use standard-time (post-transition) side.
        local_dt = tz.localize(naive_dt, is_dst=False)
    utc_dt = local_dt.astimezone(pytz.utc)
    return utc_dt.hour, utc_dt.minute

def get_sunset_cron_time(config):
    """
    Calculate today's sunset hour and minute (in UTC) for the configured location.

    Applies the optional ``sunset_offset_minutes`` config value as a positive
    or negative offset from actual sunset.

    Args:
        config (dict): Parsed configuration dictionary.

    Returns:
        tuple[int, int]: (hour, minute) in UTC of the (optionally offset) sunset time.
    """
    loc = get_location(config)
    s = sun(loc.observer, date=datetime.now().date(), tzinfo=pytz.timezone(loc.timezone))
    sunset = s["sunset"]
    sunset_time = sunset + timedelta(minutes=config.get("sunset_offset_minutes", 0))
    sunset_utc = sunset_time.astimezone(pytz.utc)
    return sunset_utc.hour, sunset_utc.minute

def get_crontab():
    """
    Read and return the current user crontab as a list of lines.

    Returns:
        list[str]: Lines of the current crontab, or an empty list if none exists.
    """
    try:
        output = subprocess.check_output(['crontab', '-l'], stderr=subprocess.DEVNULL, text=True)
        return output.splitlines()
    except subprocess.CalledProcessError:
        return []

def write_crontab(lines):
    """
    Write the given lines as the current user's crontab.

    Args:
        lines (list[str]): Crontab lines to write.

    Raises:
        RuntimeError: If the ``crontab`` command exits with a non-zero return code.
    """
    cron_text = "\n".join(lines) + "\n"
    result = subprocess.run(
        ['crontab', '-'],
        input=cron_text,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"crontab write failed with return code {result.returncode}: {result.stderr.strip()}"
        )

def build_cron_entry(minute, hour, command):
    """
    Build a single cron entry string tagged with CRON_MARKER.

    Args:
        minute (int): The minute field for the cron schedule (0–59).
        hour (int): The hour field for the cron schedule (0–23).
        command (str): The shell command to run.

    Returns:
        str: A complete cron line in the format ``M H * * * command # marker``.
    """
    return f"{minute} {hour} * * * {command} {CRON_MARKER}"

def main():
    """
    Load config, calculate sunset, and write the three Sonos cron jobs.

    Replaces any existing flag_sonos_autogen cron entries with fresh ones:
    Colors at the configured local time (default 08:00), Taps at today's
    (offset) sunset, and a self-reschedule at 02:00 to recalculate sunset
    the following day.  All times are converted to UTC before being written
    to cron.
    """
    config = load_config()
    colors_url = config["colors_url"]
    taps_url = config["taps_url"]
    tz_name = config.get("timezone") or get_system_timezone()

    # 1. Parse colors play time (local) and convert to UTC
    colors_time_str = config.get("colors_time", "08:00")
    colors_hour, colors_minute = map(int, colors_time_str.split(":"))
    colors_hour_utc, colors_min_utc = local_to_utc_hm(colors_hour, colors_minute, tz_name)

    # 2. Calculate sunset time for taps (returned in UTC)
    sunset_hour, sunset_minute = get_sunset_cron_time(config)

    # 3. Convert reschedule time (02:00 local) to UTC
    reschedule_hour_utc, reschedule_min_utc = local_to_utc_hm(2, 0, tz_name)

    # 4. Compose commands
    colors_cmd = f'{PYTHON_BIN} {SONOS_PLAY} "{colors_url}"'
    taps_cmd = f'{PYTHON_BIN} {SONOS_PLAY} "{taps_url}"'
    schedule_cmd = f'{PYTHON_BIN} {os.path.abspath(__file__)}'

    # 5. Prepare new flag_sonos_autogen crontab entries
    new_cron = [
        build_cron_entry(colors_min_utc, colors_hour_utc, colors_cmd),        # Colors
        build_cron_entry(sunset_minute, sunset_hour, taps_cmd),                # Taps at sunset
        build_cron_entry(reschedule_min_utc, reschedule_hour_utc, schedule_cmd),  # Reschedule
    ]

    # 6. Read current crontab and strip out all old flag_sonos_autogen lines
    cur_cron = get_crontab()
    filtered = [line for line in cur_cron if CRON_MARKER not in line and line.strip() != '']

    # 7. Add new lines
    filtered += new_cron

    # 8. Write updated crontab
    write_crontab(filtered)

    # 9. Print result
    print("Crontab updated with the following jobs:")
    print(f"  Colors at {colors_hour:02d}:{colors_minute:02d} {tz_name} "
          f"({colors_hour_utc:02d}:{colors_min_utc:02d} UTC) "
          f"→ cron: {colors_min_utc} {colors_hour_utc} * * *")
    # Convert sunset UTC back to local time for display
    tz_obj = pytz.timezone(tz_name)
    today = datetime.now(tz_obj).date()
    sunset_utc_dt = pytz.utc.localize(datetime(today.year, today.month, today.day, sunset_hour, sunset_minute))
    sunset_local_dt = sunset_utc_dt.astimezone(tz_obj)
    print(f"  Taps at sunset ({sunset_local_dt.hour:02d}:{sunset_local_dt.minute:02d} {tz_name} / "
          f"{sunset_hour:02d}:{sunset_minute:02d} UTC) "
          f"→ cron: {sunset_minute} {sunset_hour} * * *")
    print(f"  Reschedule at 02:00 {tz_name} "
          f"({reschedule_hour_utc:02d}:{reschedule_min_utc:02d} UTC) "
          f"→ cron: {reschedule_min_utc} {reschedule_hour_utc} * * *")

if __name__ == "__main__":
    main()
