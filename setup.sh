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
# Create or touch the log file
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
    FILES=$(wget -qO- https://api.github.com/repos/agster27/flag/contents/ | jq -r '.[] | select(.type == "file") | .name
