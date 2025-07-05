#!/bin/bash
set -e

echo "🔧 Setting up Sonos Scheduled Playback Environment..."

# Step 1: Install system dependencies
echo "📦 Installing dependencies..."
sudo apt update
sudo apt install -y python3-full python3-venv ffmpeg jq

# Step 2: Create /opt/flag directory structure
echo "📁 Creating /opt/flag/audio..."
sudo mkdir -p /opt/flag/audio
sudo chown $(whoami) /opt/flag/audio

# Step 3: Download scripts from GitHub
echo "⬇️  Downloading scripts from GitHub..."
BASE_URL="https://raw.githubusercontent.com/agster27/flag/main"
cd /opt/flag

wget -q $BASE_URL/sonos_play.py -O sonos_play.py
wget -q $BASE_URL/sunset_timer.py -O sunset_timer.py
wget -q $BASE_URL/schedule_sonos.sh -O schedule_sonos.sh
wget -q $BASE_URL/audio_check.py -O audio_check.py

chmod +x schedule_sonos.sh

# Step 4: Setup Python virtual environment
echo "🐍 Setting up virtual environment..."
python3 -m venv sonos-env
source sonos-env/bin/activate

echo "📦 Installing Python packages..."
pip install --upgrade pip
pip install soco astral pytz mutagen

# Step 5: Create config.json if not present
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

echo "✅ Setup complete. See README for next steps."
