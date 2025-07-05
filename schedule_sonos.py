#!/bin/bash

CONFIG_FILE="/opt/flag/config.json"
SONOS_ENV="/opt/flag/sonos-env/bin/python"
SONOS_SCRIPT="/opt/flag/sonos_play.py"
SUNSET_TIMER="/opt/flag/sunset_timer.py"
LOG_FILE="/opt/flag/sonos_play.log"

# Extract URLs from config
TAPS_URL=$(jq -r .taps_url "$CONFIG_FILE")
COLORS_URL=$(jq -r .colors_url "$CONFIG_FILE")

# Get sunset time (format: "minute hour")
SUNSET_TIME=$("$SONOS_ENV" "$SUNSET_TIMER")

if ! [[ "$SUNSET_TIME" =~ ^[0-9]{1,2}\ [0-9]{1,2}$ ]]; then
  echo "$(date -Iseconds) - ERROR: Invalid sunset time format from sunset_timer.py: '$SUNSET_TIME'" >> "$LOG_FILE"
  exit 1
fi

# Build cron entries
TAPS_CMD="$SONOS_ENV $SONOS_SCRIPT $TAPS_URL"
TAPS_JOB="$SUNSET_TIME * * * $TAPS_CMD"
COLORS_CMD="$SONOS_ENV $SONOS_SCRIPT $COLORS_URL"
COLORS_JOB="0 8 * * * $COLORS_CMD"

# Read existing crontab
EXISTING_CRON=$(crontab -l 2>/dev/null | grep -v "$SONOS_SCRIPT.*taps.mp3" | grep -v "$SONOS_SCRIPT.*colors.mp3")

# Combine and write new crontab
{
    echo "$EXISTING_CRON"
    echo "$TAPS_JOB"
    echo "$COLORS_JOB"
} | crontab -

# Log changes
echo "$(date -Iseconds) - INFO: Updated sunset (Taps) cronjob to: $TAPS_JOB" >> "$LOG_FILE"
echo "$(date -Iseconds) - INFO: Ensured morning (Colors) cronjob: $COLORS_JOB" >> "$LOG_FILE"
