#!/usr/bin/env python3

from datetime import datetime
from astral import LocationInfo
from astral.sun import sun
import pytz
import json

# Load config
with open("/opt/config.json") as f:
    config = json.load(f)

latitude = config.get("latitude", 42.1)
longitude = config.get("longitude", -71.5)
timezone = config.get("timezone", "America/New_York")

city = LocationInfo("Custom", "Home", timezone, latitude, longitude)
s = sun(city.observer, date=datetime.now(pytz.timezone(timezone)), tzinfo=pytz.timezone(timezone))
sunset = s["sunset"]

# Output crontab-compatible time: minute hour
print(f"{sunset.minute} {sunset.hour}")
