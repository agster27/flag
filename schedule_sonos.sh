#!/bin/bash

LOG_FILE="/opt/sonos_play.log"
SONOS_ENV="/opt/sonos-env/bin/python"
PLAY_SCRIPT="/opt/sonos_play.py"
AUDIO_URL="http://flag.aghy.home:8000/taps.mp3"

log() {
    echo "$(date --iso-8601=seconds) - $1" >> "$LOG_FILE"
}

# Get today's sunset time
SUNSET_TIME=$($SONOS_ENV /opt/sunset_timer.py)
SUNSET_HOUR=$(date -d "$SUNSET_TIME" '+%H')
SUNSET_MIN=$(date -d "$SUNSET_TIME" '+%M')

# Remove any existing crontab lines that call sonos_play.py with taps.mp3
crontab -l | grep -v "$AUDIO_URL" > /tmp/current_cron

# Add the updated sunset job
echo "$SUNSET_MIN $SUNSET_HOUR * * * $SONOS_ENV $PLAY_SCRIPT $AUDIO_URL" >> /tmp/current_cron

# Install the updated crontab
crontab /tmp/current_cron
rm /tmp/current_cron

log "INFO: Sunset schedule updated to $SUNSET_HOUR:$SUNSET_MIN for taps.mp3"
