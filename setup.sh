#!/bin/bash
set -e

BASE_URL="https://raw.githubusercontent.com/agster27/flag/main"
INSTALL_DIR="/opt/flag"
AUDIO_DIR="$INSTALL_DIR/audio"
VENV_DIR="$INSTALL_DIR/sonos-env"

function prompt_menu() {
    echo ""
    echo "What would you like to do?"
    echo "1) Update/install the latest scripts"
    echo "2) Uninstall completely"
    echo "3) Exit without doing anything"
    read -p "Enter your choice [1-3]: " CHOICE
}

function uninstall_all() {
    echo "🚨 Uninstalling Honor Tradition with Tech..."
    sudo rm -rf "$INSTALL_DIR"
    # Remove related crontab entries
    TMPCRON=$(mktemp)
    crontab -l 2>/dev/null | grep -v "$INSTALL_DIR" > "$TMPCRON" || true
    crontab "$TMPCRON" || true
    rm -f "$TMPCRON"
    echo "✅ All files and cron jobs removed!"
    exit 0
}

function update_or_install() {
    echo "🔧 Setting up Sonos Scheduled Playback Environment..."

    # Step 1: Install system dependencies
    echo "📦 Installing dependencies..."
    sudo apt update
    sudo apt install -y python3-full python3-venv ffmpeg jq wget

    # Step 2: Create directory structure
    echo "📁 Creating $AUDIO_DIR..."
    sudo mkdir -p "$AUDIO_DIR"
    sudo chown $(whoami) "$AUDIO_DIR"
    cd "$INSTALL_DIR" || sudo mkdir -p "$INSTALL_DIR" && cd "$INSTALL_DIR"

    # Step 3: Download scripts from GitHub
    echo "⬇️  Downloading scripts from GitHub..."
    wget -q "$BASE_URL/sonos_play.py" -O sonos_play.py
    wget -q "$BASE_URL/sunset_timer.py" -O sunset_timer.py
    wget -q "$BASE_URL/schedule_sonos.sh" -O schedule_sonos.sh
    wget -q "$BASE_URL/audio_check.py" -O audio_check.py

    chmod +x schedule_sonos.sh

    # Step 4: Setup Python virtual environment
    echo "🐍 Setting up virtual environment..."
    python3 -m venv "$VENV_DIR"
    source "$VENV_DIR/bin/activate"

    echo "📦 Installing Python packages..."
    pip install --upgrade pip
    pip install soco astral pytz mutagen

    # Step 5: Create config.json if not present
    CONFIG_FILE="$INSTALL_DIR/config.json"
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
    echo "- Upload your colors.mp3 and taps.mp3 files to $AUDIO_DIR"
    echo "- Run $INSTALL_DIR/schedule_sonos.sh after 2AM to create the sunset cron job"

    # Step 7: Set permissions on schedule_sonos.sh if it exists
    if [ -f "$INSTALL_DIR/schedule_sonos.sh" ]; then
        chmod 755 "$INSTALL_DIR/schedule_sonos.sh"
    else
        echo "⚠️  WARNING: $INSTALL_DIR/schedule_sonos.sh not found!"
    fi

    # Step 8: Add 8 AM Colors cronjob if it doesn't exist
    CRON_CMD="$VENV_DIR/bin/python $INSTALL_DIR/sonos_play.py \$(jq -r .colors_url $CONFIG_FILE)"
    CRON_JOB="0 8 * * * $CRON_CMD"
    echo "📅 Checking crontab for 8 AM Colors job..."
    if ! crontab -l 2>/dev/null | grep -Fq "$CRON_CMD"; then
        (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
        echo "✅ Added Colors cronjob: $CRON_JOB"
    else
        echo "✅ Colors cronjob already exists."
    fi
}

# --- MAIN MENU ---
while true; do
    prompt_menu
    case $CHOICE in
        1)
            update_or_install
            break
            ;;
        2)
            uninstall_all
            break
            ;;
        3)
            echo "👋 Exiting. No changes made."
            exit 0
            ;;
        *)
            echo "❌ Invalid option. Please enter 1, 2, or 3."
            ;;
    esac
done
