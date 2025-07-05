#!/bin/bash
set -e

SETUP_VERSION="1.2.1"

BASE_URL="https://raw.githubusercontent.com/agster27/flag/main"
INSTALL_DIR="/opt/flag"
AUDIO_DIR="$INSTALL_DIR/audio"
VENV_DIR="$INSTALL_DIR/sonos-env"
LOG_FILE="$INSTALL_DIR/setup.log"
REQUIREMENTS_TXT="$INSTALL_DIR/requirements.txt"

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
    log "🚀 Running setup.sh version $SETUP_VERSION"
    log "🔧 Setting up Sonos Scheduled Playback Environment..."

    log "📦 Installing system dependencies..."
    sudo apt update | tee -a "$LOG_FILE"
    sudo apt install -y python3-full python3-venv ffmpeg jq wget | tee -a "$LOG_FILE"

    log "📁 Creating $AUDIO_DIR..."
    sudo mkdir -p "$AUDIO_DIR"
    sudo chown $(whoami) "$AUDIO_DIR"
    sudo mkdir -p "$INSTALL_DIR"
    sudo chown $(whoami) "$INSTALL_DIR"
    cd "$INSTALL_DIR"

    # Download root files
    log "🌐 Fetching file list from GitHub API (root)..."
    FILES=$(wget -qO- https://api.github.com/repos/agster27/flag/contents/ | jq -r '.[] | select(.type == "file") | .name')
    if [ -z "$FILES" ]; then
        log "❌ Could not fetch file list from GitHub. Exiting."
        exit 1
    fi

    log "⬇️  Downloading scripts from GitHub (root)..."
    for file in $FILES; do
        if wget -q "$BASE_URL/$file" -O "$file"; then
            log "Downloaded: $file"
        else
            log "WARNING: $file could not be downloaded!"
        fi
    done

    # Download all files in the audio directory from GitHub
    log "🌐 Fetching audio file list from GitHub API (audio/)..."
    AUDIO_FILES=$(wget -qO- https://api.github.com/repos/agster27/flag/contents/audio | jq -r '.[] | select(.type == "file") | .name')

    log "⬇️  Downloading audio files from GitHub (audio/)..."
    for audio_file in $AUDIO_FILES; do
        if wget -q "$BASE_URL/audio/$audio_file" -O "$AUDIO_DIR/$audio_file"; then
            log "Downloaded: audio/$audio_file"
        else
            log "WARNING: audio/$audio_file could not be downloaded!"
        fi
    done

    # Make all .py scripts in /opt/flag executable
    log "🔑 Setting execute permissions on Python scripts..."
    find "$INSTALL_DIR" -maxdepth 1 -type f -name "*.py" -exec chmod +x {} \;

    # Create requirements.txt if not present
    if [ ! -f "$REQUIREMENTS_TXT" ]; then
        log "📝 Creating requirements.txt..."
        cat > "$REQUIREMENTS_TXT" <<EOF
soco
mutagen
astral
pytz
EOF
    fi

    # Create virtual environment if it doesn't exist
    if [ ! -d "$VENV_DIR" ]; then
        log "🐍 Creating Python virtual environment..."
        python3 -m venv "$VENV_DIR"
    fi

    log "📦 Installing/updating Python dependencies in virtualenv..."
    source "$VENV_DIR/bin/activate"
    pip install --upgrade pip
    pip install -r "$REQUIREMENTS_TXT"
    deactivate

    log "✅ Python dependencies installed."

    # Systemd audio HTTP server setup
    if [ ! -f /etc/systemd/system/flag-audio-http.service ]; then
        log "⚙️  Installing systemd service for audio HTTP server..."
        sudo tee /etc/systemd/system/flag-audio-http.service > /dev/null <<EOF
[Unit]
Description=Flag Audio HTTP Server
After=network.target

[Service]
Type=simple
WorkingDirectory=$AUDIO_DIR
ExecStart=$VENV_DIR/bin/python -m http.server 8000 --directory $AUDIO_DIR --bind 0.0.0.0
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF
        sudo systemctl daemon-reload
        sudo systemctl enable flag-audio-http
    fi

    log "🔄 Restarting audio HTTP server..."
    sudo systemctl restart flag-audio-http

    # Informational output
    log "🏁 Setup complete."
    log ""
    log "Test your setup:"
    log "  curl -I http://localhost:8000/colors.mp3"
    log ""
    log "To test Sonos playback manually, run:"
    log "  $VENV_DIR/bin/python $INSTALL_DIR/sonos_play.py http://flag.aghy.home:8000/colors.mp3"
    log ""
    log "Check status of audio server:"
    log "  sudo systemctl status flag-audio-http"
    log ""
    log "Edit your config in: $INSTALL_DIR/config.json"
}

prompt_menu
case $CHOICE in
    1)
        update_or_install
        ;;
    2)
        uninstall_all
        ;;
    *)
        log "👋 Exiting without making changes."
        exit 0
        ;;
esac
