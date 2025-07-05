#!/bin/bash

CONFIG_FILE="/opt/config.json"
PYTHON_BIN="/opt/sonos-env/bin/python"
SONOS_PLAY="/opt/sonos_play.py"
SUNSET_TIMER="/opt/sunset_timer.py"
CRON_TEMP="/tmp/cron_sunset"
CURRENT_CRON=$(mktemp)

# Extract taps_url from config.json
TAPS_URL=$(jq -r '.taps_url' "$CONFIG_FILE")

# Get sunset time
SUNSET_TIME=$($PYTHON_BIN $SUNSET_TIMER)

# Format HH:MM
SUNSET_HOUR=$(echo "$SUNSET_TIME" | cut -d: -f1)
SUNSET_MIN=$(echo "$SUNSET_TIME" | cut -d: -f2)

# Log
echo "$(date --iso-8601=seconds) - INFO: Calculated sunset time: $SUNSET_TIME for taps.mp3" >> /opt/sonos_play.log

# Prepare cron line
CRON_CMD="$SUNSET_MIN $SUNSET_HOUR * * * $PYTHON_BIN $SONOS_PLAY $TAPS_URL"

# Remove existing line for sonos_play.py from crontab and add new one
crontab -l 2>/dev/null | grep -v "$SONOS_PLAY" > "$CURRENT_CRON"
echo "$CRON_CMD" >> "$CURRENT_CRON"
crontab "$CURRENT_CRON"
rm "$CURRENT_CRON"

echo "$(date --iso-8601=seconds) - INFO: Sunset schedule updated to $SUNSET_TIME for taps.mp3" >> /opt/sonos_play.log
