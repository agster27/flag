#!/bin/bash
set -e

BASE_URL="https://raw.githubusercontent.com/agster27/flag/main"
INSTALL_DIR="/opt/flag"
AUDIO_DIR="$INSTALL_DIR/audio"
VENV_DIR="$INSTALL_DIR/sonos-env"
LOG_FILE="$INSTALL_DIR/setup.log"

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
    TMPCRON=$(mktemp)
    crontab -l 2>/dev/null | grep -v "$INSTALL_DIR" > "$TMPCRON" || true
    crontab "$TMPCRON" || true
    rm -f "$TMPCRON"
    log "✅ All files and cron jobs removed!"
    sudo systemctl disable --now flag-audio-http 2>/dev/null || true
    sudo rm -f /etc/systemd/system/flag-audio-http.service
    sudo systemctl daemon-reload
    sudo rm -rf "$INSTALL_DIR"
    exit 0
}

function update_or_install() {
    log "🔧 Setting up Sonos Scheduled Playback Environment..."

    log "📦 Installing dependencies..."
    sudo apt update | tee -a "$LOG_FILE"
    sudo apt install -y python3-full python3-venv ffmpeg jq wget | tee -a "$LOG_FILE"

    log "📁 Creating $AUDIO_DIR..."
    sudo mkdir -p "$AUDIO_DIR"
    sudo chown $(whoami) "$AUDIO_DIR"
    sudo mkdir -p "$INSTALL_DIR"
    sudo chown $(whoami) "$INSTALL_DIR"
    cd "$INSTALL_DIR"

    log "🌐 Fetching file list from GitHub API..."
    FILES=$(wget -qO- https://api.github.com/repos/agster27/flag/contents/ | jq -r '.[] | select(.type == "file") | .name')
    if [ -z "$FILES" ]; then
        log "❌ Could not fetch file list from GitHub. Exiting."
        exit 1
    fi

    log "⬇️  Downloading scripts from GitHub..."
    for file in $FILES; do
        if wget -q "$BASE_URL/$file" -O "$file"; then
            log "Downloaded: $file"
        else
            log "WARNING: $file could not be downloaded!"
        fi
    done

    log "🔐 Making .sh and .py scripts executable..."
    find "$INSTALL_DIR" -maxdepth 1 -type f \( -iname "*.sh" -o -iname "*.py" \) -exec chmod +x {} \;

    log "🐍 Setting up virtual environment..."
    python3 -m venv "$VENV_DIR"
    source "$VENV_DIR/bin/activate"

    log "📦 Installing Python packages..."
    pip install --upgrade pip | tee -a "$LOG_FILE"
    pip install soco astral pytz mutagen | tee -a "$LOG_FILE"

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

    # Step: Call schedule_sonos.py to update crontab
    SCHEDULE_CMD="$VENV_DIR/bin/python $INSTALL_DIR/schedule_sonos.py"
    if [ -x "$INSTALL_DIR/schedule_sonos.py" ]; then
        log "🚀 Running schedule_sonos.py to update crontab..."
        $SCHEDULE_CMD | tee -a "$LOG_FILE"
        log "✅ schedule_sonos.py executed."
    else
        log "⚠️  schedule_sonos.py not found or not executable!"
    fi

    # --- Setup Audio HTTP server as a systemd service ---
    SERVICE_FILE="/etc/systemd/system/flag-audio-http.service"
    log "📝 Creating systemd service at $SERVICE_FILE..."

    sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Flag Audio HTTP Server
After=network.target

[Service]
Type=simple
ExecStart=$VENV_DIR/bin/python -m http.server 8000 --directory $AUDIO_DIR
WorkingDirectory=$AUDIO_DIR
Restart=always
User=root
Group=root

[Install]
WantedBy=multi-user.target
EOF

    log "🔄 Reloading systemd daemon and enabling audio HTTP server..."
    sudo systemctl daemon-reload
    sudo systemctl enable --now flag-audio-http
    log "✅ Audio HTTP server started and enabled! Files served at http://<your-host>:8000/"

    log "✅ Setup complete. See $LOG_FILE for details."
    log "Make sure to:"
    log "- Upload your colors.mp3 and taps.mp3 files to $AUDIO_DIR"
    log "- Your cron jobs are set up. To review, run: crontab -l"
    log "- Audio server is running. Check with: sudo systemctl status flag-audio-http"
}

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
