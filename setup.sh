#!/bin/bash
set -e

BASE_URL="https://raw.githubusercontent.com/agster27/flag/main"
INSTALL_DIR="/opt/flag"
AUDIO_DIR="$INSTALL_DIR/audio"
VENV_DIR="$INSTALL_DIR/sonos-env"
LOG_FILE="$INSTALL_DIR/setup.log"

# Ensure /opt/flag exists before any logging
sudo mkdir -p "$INSTALL_DIR"
sudo chown $(whoami) "$INSTALL_DIR"
touch "$LOG_FILE"

function log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

function prompt_menu() {
    echo ""
    echo "What would you like to do?"
    echo "1) Update/install the latest scripts"
    echo "2) Uninstall completely"
    echo "3) Exit without doing anything"
    read -p "Enter your choice [1-3]: " CHOICE
}

function uninstall_all() {
    log "🚨 Uninstalling Honor Tradition with Tech..."
    sudo rm -rf "$INSTALL_DIR"
    # Remove related crontab entries
    TMPCRON=$(mktemp)
    crontab -l 2>/dev/null | grep -v "$INSTALL_DIR" > "$TMPCRON" || true
    crontab "$TMPCRON" || true
    rm -f "$TMPCRON"
    log "✅ All files and cron jobs removed!"
    exit 0
}

function update_or_install() {
    log "🔧 Setting up Sonos Scheduled Playback Environment..."

    # Step 1: Install system dependencies
    log "📦 Installing dependencies..."
    sudo apt update | tee -a "$LOG_FILE"
    sudo apt install -y python3-full python3-venv ffmpeg jq wget | tee -a "$LOG_FILE"

    # Step 2: Create directory structure
    log "📁 Creating $AUDIO_DIR..."
    sudo mkdir -p "$AUDIO_DIR"
    sudo chown $(whoami) "$AUDIO_DIR"
    sudo mkdir -p "$INSTALL_DIR"
    sudo chown $(whoami) "$INSTALL_DIR"
    cd "$INSTALL_DIR"

    # Step 3: Download list of all files in the repo
    log "🌐 Fetching file list from GitHub API..."
    FILES=$(wget -qO- https://api.github.com/repos/agster27/flag/contents/ | jq -r '.[] | select(.type == "file") | .name')
    if [ -z "$FILES" ]; then
        log "❌ Could not fetch file list from GitHub. Exiting."
        exit 1
    fi

    # Step 4: Download each file if it exists in the repo
    log "⬇️  Downloading scripts from GitHub..."
    for file in $FILES; do
        if wget -q "$BASE_URL/$file" -O "$file"; then
            log "Downloaded: $file"
        else
            log "WARNING: $file could not be downloaded!"
        fi
    done

    # Make all .sh and .py scripts executable if any
    log "🔐 Making .sh and .py scripts executable..."
    find "$INSTALL_DIR" -maxdepth 1 -type f \( -iname "*.sh" -o -iname "*.py" \) -exec chmod +x {} \;

    # Step 5: Setup Python virtual environment
    log "🐍 Setting up virtual environment..."
    python3 -m venv "$VENV_DIR"
    source "$VENV_DIR/bin/activate"

    log "📦 Installing Python packages..."
    pip install --upgrade pip | tee -a "$LOG_FILE"
    pip install soco astral pytz mutagen | tee -a "$LOG_FILE"

    # Step 6: Create config.json if not present
    CONFIG_FILE="$INSTALL_DIR/config.json"
    if [ ! -f "$CONFIG_FILE" ]; then
        log "📝 Creating default config.json..."
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
        log "✅ config.json already exists. Skipping creation."
    fi

    # Step 7: Add 8 AM Colors cronjob if it doesn't exist
    CRON_CMD="$VENV_DIR/bin/python $INSTALL_DIR/sonos_play.py \$(jq -r .colors_url $CONFIG_FILE)"
    CRON_JOB="0 8 * * * $CRON_CMD"
    log "📅 Checking crontab for 8 AM Colors job..."
    if ! crontab -l 2>/dev/null | grep -Fq "$CRON_CMD"; then
        (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
        log "✅ Added Colors cronjob: $CRON_JOB"
    else
        log "✅ Colors cronjob already exists."
    fi

    # Step 8: Add 2 AM schedule_sonos.sh cronjob if not present
    SCHEDULE_CMD="$INSTALL_DIR/schedule_sonos.sh"
    SCHEDULE_JOB="0 2 * * * $SCHEDULE_CMD"
    log "📅 Checking crontab for 2 AM schedule_sonos.sh job..."
    if ! crontab -l 2>/dev/null | grep -Fq "$SCHEDULE_CMD"; then
        (crontab -l 2>/dev/null; echo "$SCHEDULE_JOB") | crontab -
        log "✅ Added schedule_sonos.sh cronjob: $SCHEDULE_JOB"
    else
        log "✅ schedule_sonos.sh cronjob already exists."
    fi

    # Step 9: Run schedule_sonos.sh to update sunset cron
    if [ -x "$SCHEDULE_CMD" ]; then
        log "🚀 Running schedule_sonos.sh to update sunset cron job..."
        "$SCHEDULE_CMD" | tee -a "$LOG_FILE"
        log "✅ schedule_sonos.sh executed."
    else
        log "⚠️  schedule_sonos.sh not found or not executable!"
    fi

    # Step 10: Notify user about next steps
    log "✅ Setup complete. See $LOG_FILE for details."
    log "Make sure to:"
    log "- Upload your colors.mp3 and taps.mp3 files to $AUDIO_DIR"
    log "- Your cron jobs are set up. To review, run: crontab -l"
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
            log "👋 Exiting. No changes made."
            exit 0
            ;;
        *)
            echo "❌ Invalid option. Please enter 1, 2, or 3."
            ;;
    esac
done
