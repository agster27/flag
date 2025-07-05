#!/bin/bash

# Load config values
CONFIG_FILE="/opt/config.json"
SONOS_ENV="/opt/sonos-env/bin/python"
SONOS_SCRIPT="/opt/sonos_play.py"
SUNSET_TIMER="/opt/sunset_timer.py"

# Extract taps_url from config
TAPS_URL=$(jq -r .taps_url "$CONFIG_FILE")

# Use virtualenv Python to run sunset_timer
SUNSET_TIME=$("$SONOS_ENV" "$SUNSET_TIMER")  # Expected format: "20 24"

# Validate format
if ! [[ "$SUNSET_TIME" =~ ^[0-9]{1,2}\ [0-9]{1,2}$ ]]; then
  echo "❌ Invalid time format received from sunset_timer.py: '$SUNSET_TIME'"
  exit 1
fi

# Build the cron command
CRON_CMD="$SONOS_ENV $SONOS_SCRIPT $TAPS_URL"
CRON_JOB="$SUNSET_TIME * * * $CRON_CMD"

# Clean up previous taps cron entries and apply updated one
echo "🧹 Cleaning up old Taps cron jobs..."
(crontab -l 2>/dev/null | grep -v "$SONOS_SCRIPT.*taps.mp3"; echo "$CRON_JOB") | crontab -

echo "✅ Sunset schedule updated to: $CRON_JOB"
