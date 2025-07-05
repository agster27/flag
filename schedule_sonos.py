#!/usr/bin/env python3
import os
import sys
import json
import subprocess
from datetime import datetime, timedelta
from astral import LocationInfo
from astral.sun import sun
import pytz

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
CRON_MARKER = "# [flag_sonos_autogen]"
PYTHON_BIN = os.path.join(os.path.dirname(__file__), "sonos-env", "bin", "python")
SONOS_PLAY = os.path.join(os.path.dirname(__file__), "sonos_play.py")

def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

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
    loc = get_location(config)
    s = sun(loc.observer, date=datetime.now().date(), tzinfo=pytz.timezone(loc.timezone))
    sunset = s["sunset"]
    sunset_time = sunset + timedelta(minutes=config.get("sunset_offset_minutes", 0))
    return sunset_time.hour, sunset_time.minute

def get_crontab():
    try:
        output = subprocess.check_output(['crontab', '-l'], stderr=subprocess.DEVNULL, text=True)
        return output.splitlines()
    except subprocess.CalledProcessError:
        return []

def write_crontab(lines):
    cron_text = "\n".join(lines) + "\n"
    proc = subprocess.Popen(['crontab', '-'], stdin=subprocess.PIPE, text=True)
    proc.communicate(input=cron_text)

def build_cron_entry(minute, hour, command):
    return f"{minute} {hour} * * * {command} {CRON_MARKER}"

def main():
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
        build_cron_entry(f"{sunset_minute:02}", f"{sunset_hour:02}", taps_cmd),  # Taps at sunset
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
