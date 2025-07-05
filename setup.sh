#!/bin/bash

set -e

echo "🔧 Setting up Sonos Scheduled Playback Environment..."

# Step 1: Install system dependencies
echo "📦 Installing dependencies..."
sudo apt update
sudo apt install -y python3-full python3-venv ffmpeg jq git

# Step 2: Create audio directory if it doesn't exist
echo "📁 Ensuring /opt/flag/audio exists..."
sudo mkdir -p /opt/flag/audio
sudo chown $(whoami) /opt/flag/audio

# Step 3: Setup Python virtual environment
echo "🐍 Setting up virtual environment..."
cd /opt/flag
python3 -m venv sonos-env
source sonos-env/bin/activate

echo "📦 Installing Python packages..."
pip install --upgrade pip
pip install soco astral pytz mutagen

# Step 4: Clone GitHub repo and copy Python scripts
echo "📥 Cloning GitHub repository..."
cd /opt
if [ -d "/opt/flag" ]; then
    echo "🔁 Repo already exists, pulling latest changes..."
    cd /opt/flag
    git checkout main 2>/dev/null || git checkout -b main
    git branch --set-upstream-to=origin/main main 2>/dev/null
    git pull
else
    git clone https://github.com/agster27/flag.git /opt/flag
    cd /opt/flag
fi

echo "📄 Copying scripts to /opt/flag..."
cp sonos_play.py sunset_timer.py schedule_sonos.sh audio_check.py /opt/flag/
chmod +x /opt/flag/schedule_sonos.sh

# Step 5: Create default config.json if not present
CONFIG_FILE="/opt/flag/config.json"
if [ ! -f "$CONFIG_FILE" ]; then
    echo "📝 Creating default config.json..."
    cat <<EOF > "$CONFIG_FILE"
{
  "sonos_ip": "192.168.1.50",
  "volume": 30,
  "colors_url": "http://flag.aghy.home:8000/audio/colors.mp3",
  "taps_url": "http://flag.aghy.home:8000/audio/taps.mp3",
  "default_wait_seconds": 60,
  "skip_restore_if_idle": true
}
EOF
else
    echo "✅ config.json already exists. Skipping creation."
fi

# Step 6: Notify user about next steps
echo "✅ Setup complete. Make sure to:"
echo "- Add the cron jobs listed in the README.md"
echo "- Upload your colors.mp3 and taps.mp3 files to /opt/flag/audio"
echo "- Run /opt/flag/schedule_sonos.sh after 2AM to create the sunset cron job"

# Step 7: Set permissions on schedule_sonos.sh
chmod 755 /opt/flag/schedule_sonos.sh

# Step 8: Add 8 AM Colors cronjob if it doesn't exist
CRON_CMD="/opt/flag/sonos-env/bin/python /opt/flag/sonos_play.py \$(jq -r .colors_url /opt/flag/config.json)"
CRON_JOB="0 8 * * * $CRON_CMD"
echo "📅 Checking crontab for 8 AM Colors job..."
if ! crontab -l 2>/dev/null | grep -Fq "$CRON_CMD"; then
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    echo "✅ Added Colors cronjob: $CRON_JOB"
else
    echo "✅ Colors cronjob already exists."
fi
