#!/bin/bash

set -e

echo "🔧 Setting up Sonos Scheduled Playback Environment..."

# Step 1: Install system dependencies
echo "📦 Installing dependencies..."
sudo apt update
sudo apt install -y python3-full python3-venv ffmpeg jq git

# Step 2: Create audio directory if it doesn't exist
echo "📁 Ensuring /opt/audio exists..."
sudo mkdir -p /opt/audio
sudo chown $(whoami) /opt/audio

# Step 3: Setup Python virtual environment
echo "🐍 Setting up virtual environment..."
cd /opt
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
    git pull
else
    git clone https://github.com/agster27/flag.git
    cd flag
fi

echo "📄 Copying scripts to /opt..."
cp sonos_play.py sunset_timer.py schedule_sonos.sh /opt/
chmod +x /opt/schedule_sonos.sh

# Step 5: Create default config.json if not present
CONFIG_FILE="/opt/config.json"
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
echo "- Upload your colors.mp3 and taps.mp3 files to /opt/audio"
echo "- Run /opt/schedule_sonos.sh after 2AM to create the sunset cron job"

# Step 7: Set permissions on schedule_sonos.sh
chmod 755 /opt/schedule_sonos.sh

# Step 8: Add 8 AM Colors cronjob if it doesn't exist
CRON_CMD="/opt/sonos-env/bin/python /opt/sonos_play.py $(jq -r .colors_url /opt/config.json)"
CRON_JOB="0 8 * * * $CRON_CMD"
echo "📅 Checking crontab for 8 AM Colors job..."
if ! crontab -l 2>/dev/null | grep -Fq "$CRON_CMD"; then
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    echo "✅ Added Colors cronjob: $CRON_JOB"
else
    echo "✅ Colors cronjob already exists."
fi
