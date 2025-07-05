from astral.sun import sun
from astral import LocationInfo
from datetime import datetime
import pytz

LOG_FILE = "/opt/sonos_play.log"

def log(message):
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.now().isoformat()} - {message}\n")

try:
    city = LocationInfo("Milford", "USA", "US/Eastern", 42.1398, -71.5162)
    s = sun(city.observer, date=datetime.now().date(), tzinfo=pytz.timezone(city.timezone))
    sunset_time = s['sunset'].strftime('%H:%M')
    log(f"INFO: Calculated sunset time: {sunset_time}")
    print(sunset_time)
except Exception as e:
    log(f"ERROR: Failed to calculate sunset time - {e}")
    print("18:00")  # fallback sunset time
