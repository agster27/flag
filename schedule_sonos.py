#!/bin/bash

# Load config values
CONFIG_FILE="/opt/config.json"
SUNSET_URL=$(jq -r .taps_url "$CONFIG_FILE")
SONOS_ENV="/opt/sonos-env/bin/python"
SONOS_SCRIPT="/opt/sonos_play.py"

# Calculate sunset time in 24h format (e.g., "20 24" for 8:24 PM)
SUNSET_HOUR_MINUTE=$(python3 /opt/sunset_timer.py)  # This should print "24 20" for crontab

# Format the cron job
SUNSET_CMD="$SONOS_ENV $SONOS_SCRIPT $SUNSET_URL"
SUNSET_JOB="$SUNSET_HOUR_MINUTE * * * $SUNSET_CMD"

# Remove any previous taps job
(crontab -l 2>/dev/null | grep -v "$SONOS_SCRIPT.*taps.mp3"; echo "$SUNSET_JOB") | crontab -
