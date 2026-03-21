#!/usr/bin/env python3
"""
schedule_sonos.py — Calculates today's sunset and writes the Sonos cron schedule.

Reads location and timezone from config.json, computes sunset time using the
astral library, and installs three cron jobs: Colors at 0800, Taps at sunset
(with an optional offset), and a daily self-reschedule at 0200 to keep the
sunset time accurate.
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

def get_sunset_cron_time(config):
    """
    Calculate today's sunset hour and minute for the configured location.

    Applies the optional ``sunset_offset_minutes`` config value as a positive
    or negative offset from actual sunset.

    Args:
        config (dict): Parsed configuration dictionary.

    Returns:
        tuple[int, int]: (hour, minute) of the (optionally offset) sunset time.
    """
    loc = get_location(config)
    s = sun(loc.observer, date=datetime.now().date(), tzinfo=pytz.timezone(loc.timezone))
    sunset = s["sunset"]
    sunset_time = sunset + timedelta(minutes=config.get("sunset_offset_minutes", 0))
    return sunset_time.hour, sunset_time.minute

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
    Colors at 0800, Taps at today's (offset) sunset, and a self-reschedule
    at 0200 to recalculate sunset the following day.
    """
    config = load_config()
    colors_url = config["colors_url"]
    taps_url = config["taps_url"]

    # 1. Calculate sunset time for taps
    sunset_hour, sunset_minute = get_sunset_cron_time(config)

    # 2. Compose commands
    colors_cmd = f'{PYTHON_BIN} {SONOS_PLAY} "{colors_url}"'
    taps_cmd = f'{PYTHON_BIN} {SONOS_PLAY} "{taps_url}"'
    schedule_cmd = f'{PYTHON_BIN} {os.path.abspath(__file__)}'

    # 3. Prepare new flag_sonos_autogen crontab entries
    new_cron = [
        build_cron_entry(0, 8, colors_cmd),  # Colors at 8:00 AM
        build_cron_entry(sunset_minute, sunset_hour, taps_cmd),  # Taps at sunset
        build_cron_entry(0, 2, schedule_cmd),  # Recalculate sunset at 2AM
    ]

    # 4. Read current crontab and strip out all old flag_sonos_autogen lines
    cur_cron = get_crontab()
    filtered = [line for line in cur_cron if CRON_MARKER not in line and line.strip() != '']

    # 5. Add new lines
    filtered += new_cron

    # 6. Write updated crontab
    write_crontab(filtered)

    # 7. Print result
    print("Crontab updated with the following jobs:")
    for line in new_cron:
        print(line)

if __name__ == "__main__":
    main()
