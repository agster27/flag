#!/bin/bash
# setup.sh — Installs, updates, or removes the Honor Tradition with Tech project.
#
# Usage:
#   wget --no-cache https://raw.githubusercontent.com/agster27/flag/main/setup.sh -O setup.sh
#   chmod +x setup.sh
#   ./setup.sh
set -e
set -o pipefail

SETUP_VERSION="1.5.0"

BASE_URL="https://raw.githubusercontent.com/agster27/flag/main"
INSTALL_DIR="/opt/flag"
AUDIO_DIR="$INSTALL_DIR/audio"
VENV_DIR="$INSTALL_DIR/sonos-env"
LOG_FILE="$INSTALL_DIR/setup.log"
REQUIREMENTS_TXT="$INSTALL_DIR/requirements.txt"
CONFIG_FILE="$INSTALL_DIR/config.json"

function maybe_sudo() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        sudo "$@"
    fi
}

maybe_sudo mkdir -p "$INSTALL_DIR"
maybe_sudo chown "$(whoami)" "$INSTALL_DIR"
touch "$LOG_FILE"

function log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# ---------------------------------------------------------------------------
# Read an existing config value (if config already exists) as default
# ---------------------------------------------------------------------------
function cfg_default() {
    local key="$1" fallback="$2"
    if [ -f "$CONFIG_FILE" ] && command -v jq &>/dev/null; then
        val=$(jq -r ".${key} // empty" "$CONFIG_FILE" 2>/dev/null)
        echo "${val:-$fallback}"
    else
        echo "$fallback"
    fi
}

# ---------------------------------------------------------------------------
# Write (or rewrite) the systemd service file using the current $PORT value.
# ---------------------------------------------------------------------------
function write_service_file() {
    log "⚙️  Writing systemd service for audio HTTP server (port $PORT)..."
    maybe_sudo tee /etc/systemd/system/flag-audio-http.service > /dev/null <<EOF
[Unit]
Description=Flag Audio HTTP Server
After=network.target

[Service]
Type=simple
WorkingDirectory=$AUDIO_DIR
ExecStart=$VENV_DIR/bin/python -m http.server $PORT --directory $AUDIO_DIR --bind 0.0.0.0
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF
    maybe_sudo systemctl daemon-reload
}

# ---------------------------------------------------------------------------
# Auto-discover Sonos speakers on the local network via soco/SSDP.
# Sets SONOS_IP to the chosen/found IP, or empty string if none selected.
# Requires the Python venv (with soco installed) to already exist.
# ---------------------------------------------------------------------------
function discover_sonos_ip() {
    if [ ! -d "$VENV_DIR" ]; then
        SONOS_IP=""
        return
    fi
    log "🔍 Scanning network for Sonos speakers..."
    # Use tab as delimiter to avoid conflicts with speaker names that may contain '|'
    DISCOVERED=$("$VENV_DIR/bin/python" - <<'PYEOF'
import sys
try:
    from soco.discovery import discover
    devices = sorted(discover(timeout=5) or [], key=lambda d: d.player_name)
    for d in devices:
        print(f"{d.player_name}\t{d.ip_address}")
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
PYEOF
)

    DISCOVER_EXIT=$?
    if [ -z "$DISCOVERED" ]; then
        echo ""
        if [ $DISCOVER_EXIT -ne 0 ]; then
            echo "  ⚠️  Sonos discovery encountered an error. You can enter the IP address manually."
        else
            echo "  ⚠️  No Sonos speakers found on the network."
            echo "  You can enter the IP address manually."
        fi
        SONOS_IP=""
        return
    fi

    echo ""
    echo "  Found Sonos speakers:"
    i=1
    declare -a IPS
    # IPS is 1-indexed intentionally: IPS[$i] maps directly to the user's "Select N" input
    while IFS=$'\t' read -r name ip; do
        echo "    $i) $name — $ip"
        IPS[$i]="$ip"
        ((i++))
    done <<< "$DISCOVERED"
    echo ""

    COUNT=$((i - 1))
    if [ "$COUNT" -eq 1 ]; then
        SONOS_IP="${IPS[1]}"
        echo "  ✅ Only one speaker found. Using: $SONOS_IP"
        return
    fi

    while true; do
        read -rp "  Select speaker [1-${COUNT}] or press Enter to enter IP manually: " SEL
        if [ -z "$SEL" ]; then
            SONOS_IP=""
            return
        fi
        if [[ "$SEL" =~ ^[0-9]+$ ]] && [ "$SEL" -ge 1 ] && [ "$SEL" -le "$COUNT" ]; then
            SONOS_IP="${IPS[$SEL]}"
            echo "  ✅ Selected: $SONOS_IP"
            return
        fi
        echo "  ⚠️  Please enter a number between 1 and $COUNT, or press Enter to type manually."
    done
}

# ---------------------------------------------------------------------------
# Interactive configuration wizard
# Writes $CONFIG_FILE with user-supplied (or defaulted) values.
# ---------------------------------------------------------------------------
function configure_setup() {
    echo ""
    echo "============================================"
    echo "  Flag Audio Server — Configuration Wizard  "
    echo "============================================"
    echo "Press Enter to accept the value shown in [brackets]."
    echo ""

    # Sonos IP — try auto-discovery first
    default_ip=$(cfg_default "sonos_ip" "")
    discover_sonos_ip
    # If discovery returned empty (no devices found or user chose manual), fall back
    if [ -z "$SONOS_IP" ]; then
        read -rp "  Sonos speaker IP address [${default_ip}]: " INPUT
        SONOS_IP="${INPUT:-$default_ip}"
    fi

    # HTTP server port
    default_port=$(cfg_default "port" "8000")
    while true; do
        read -rp "  HTTP server port [${default_port}]: " INPUT
        PORT="${INPUT:-$default_port}"
        if [[ "$PORT" =~ ^[0-9]+$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ]; then
            break
        fi
        echo "  ⚠️  Please enter a valid port number (1–65535)."
    done

    # Hostname / IP this machine is reachable at
    # Preferred: find a local IP on the same /24 subnet as the Sonos speaker
    local_ip=""
    if [ -n "$SONOS_IP" ]; then
        sonos_prefix=$(echo "$SONOS_IP" | cut -d. -f1-3)
        while IFS= read -r candidate; do
            if [[ "$candidate" == "${sonos_prefix}."* ]]; then
                local_ip="$candidate"
                break
            fi
        done < <(hostname -I | tr ' ' '\n')

        # Fallback: use the kernel's preferred source IP toward the Sonos speaker
        if [ -z "$local_ip" ]; then
            local_ip=$(ip route get "$SONOS_IP" 2>/dev/null \
                | awk '/src/ {for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' \
                | head -1)
            if [[ ! "$local_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
                local_ip=""
            fi
        fi
    fi
    # Final fallback: first non-loopback IP
    if [ -z "$local_ip" ]; then
        local_ip=$(hostname -I | awk '{print $1}')
    fi
    # Hostname / IP default: prefer detected local_ip, fall back to existing config
    if [ -n "$local_ip" ]; then
        default_host="$local_ip"
    else
        default_host=$(cfg_default "colors_url" "http://localhost:${default_port}/colors.mp3" \
            | sed 's|http://||;s|:.*||')
    fi
    read -rp "  Hostname or IP of THIS machine (for audio URLs) [${default_host}]: " INPUT
    HOST_ADDR="${INPUT:-$default_host}"

    COLORS_URL="http://${HOST_ADDR}:${PORT}/colors.mp3"
    TAPS_URL="http://${HOST_ADDR}:${PORT}/taps.mp3"

    # Volume
    default_vol=$(cfg_default "volume" "30")
    while true; do
        read -rp "  Sonos volume 0–100 [${default_vol}]: " INPUT
        VOLUME="${INPUT:-$default_vol}"
        if [[ "$VOLUME" =~ ^[0-9]+$ ]] && [ "$VOLUME" -ge 0 ] && [ "$VOLUME" -le 100 ]; then
            break
        fi
        echo "  ⚠️  Please enter a number between 0 and 100."
    done

    # Wait seconds
    default_wait=$(cfg_default "default_wait_seconds" "60")
    while true; do
        read -rp "  Default wait seconds between tracks [${default_wait}]: " INPUT
        WAIT_SECS="${INPUT:-$default_wait}"
        if [[ "$WAIT_SECS" =~ ^[0-9]+$ ]]; then
            break
        fi
        echo "  ⚠️  Please enter a non-negative integer."
    done

    # Skip restore if idle — normalise to lowercase true/false
    default_skip=$(cfg_default "skip_restore_if_idle" "true")
    while true; do
        read -rp "  Skip restore if speaker is idle? (true/false) [${default_skip}]: " INPUT
        SKIP_RESTORE="${INPUT:-$default_skip}"
        SKIP_RESTORE="${SKIP_RESTORE,,}"   # lowercase
        if [ "$SKIP_RESTORE" = "true" ] || [ "$SKIP_RESTORE" = "false" ]; then
            break
        fi
        echo "  ⚠️  Please enter 'true' or 'false'."
    done

    # Latitude
    default_lat=$(cfg_default "latitude" "")
    while true; do
        read -rp "  Latitude (decimal, e.g. 42.1) [${default_lat}]: " INPUT
        LATITUDE="${INPUT:-$default_lat}"
        if [[ "$LATITUDE" =~ ^-?[0-9]+(\.[0-9]+)?$ ]]; then
            break
        fi
        echo "  ⚠️  Please enter a decimal number (e.g. 42.1)."
    done

    # Longitude
    default_lon=$(cfg_default "longitude" "")
    while true; do
        read -rp "  Longitude (decimal, e.g. -71.5) [${default_lon}]: " INPUT
        LONGITUDE="${INPUT:-$default_lon}"
        if [[ "$LONGITUDE" =~ ^-?[0-9]+(\.[0-9]+)?$ ]]; then
            break
        fi
        echo "  ⚠️  Please enter a decimal number (e.g. -71.5)."
    done

    # Timezone
    default_tz=$(cfg_default "timezone" "America/New_York")
    read -rp "  Timezone (e.g. America/New_York) [${default_tz}]: " INPUT
    TIMEZONE="${INPUT:-$default_tz}"

    # Sunset offset
    default_offset=$(cfg_default "sunset_offset_minutes" "0")
    read -rp "  Sunset offset minutes [${default_offset}]: " INPUT
    SUNSET_OFFSET="${INPUT:-$default_offset}"

    echo ""
    echo "  Writing config to $CONFIG_FILE ..."
    cat > "$CONFIG_FILE" <<EOF
{
  "sonos_ip": "$SONOS_IP",
  "port": $PORT,
  "volume": $VOLUME,
  "colors_url": "$COLORS_URL",
  "taps_url": "$TAPS_URL",
  "default_wait_seconds": $WAIT_SECS,
  "skip_restore_if_idle": $SKIP_RESTORE,
  "latitude": $LATITUDE,
  "longitude": $LONGITUDE,
  "timezone": "$TIMEZONE",
  "sunset_offset_minutes": $SUNSET_OFFSET
}
EOF
    log "✅ config.json written."
}

# ---------------------------------------------------------------------------

function test_sonos_playback() {
    echo ""
    echo "============================================"
    echo "  Test Sonos Playback                       "
    echo "============================================"

    if [ ! -d "$VENV_DIR" ]; then
        echo "  ⚠️  Python venv not found. Please run Install first (option 1)."
        return
    fi
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "  ⚠️  config.json not found. Please run Install or Reconfigure first."
        return
    fi

    discover_sonos_ip

    if [ -z "$SONOS_IP" ]; then
        read -rp "  Enter Sonos speaker IP to test: " SONOS_IP
    fi
    if [ -z "$SONOS_IP" ]; then
        echo "  ⚠️  No IP provided. Aborting test."
        return
    fi

    TEST_URL=$(jq -r '.colors_url' "$CONFIG_FILE")
    echo ""
    echo "  🔊 Playing test sound on $SONOS_IP ..."
    echo "     URL: $TEST_URL"
    echo ""
    echo "  ⏳ This may take 30–90 seconds. Watch progress:"
    echo "     tail -f $LOG_FILE"
    echo ""

    TMPCONFIG=$(mktemp --suffix=.json) || { echo "  ❌ Failed to create temp file."; return 1; }
    if ! jq --arg ip "$SONOS_IP" '.sonos_ip = $ip' "$CONFIG_FILE" > "$TMPCONFIG"; then
        rm -f "$TMPCONFIG"
        echo "  ❌ Failed to build test config. Check $CONFIG_FILE."
        return 1
    fi

    FLAG_CONFIG="$TMPCONFIG" "$VENV_DIR/bin/python" "$INSTALL_DIR/sonos_play.py" "$TEST_URL"
    PLAY_EXIT=$?
    rm -f "$TMPCONFIG"
    if [ $PLAY_EXIT -eq 0 ]; then
        echo "  ✅ Test playback complete."
    else
        echo "  ❌ Test playback failed. Check $LOG_FILE for details."
    fi
    echo "  📋 Full log: $LOG_FILE"
}

function prompt_menu() {
    echo ""
    echo "What would you like to do?"
    echo "1) Install / update to the latest scripts"
    echo "2) Reconfigure (edit config.json interactively)"
    echo "3) Test Sonos playback"
    echo "4) Uninstall completely"
    echo "5) Exit without doing anything"
    read -rp "Enter your choice [1-5]: " CHOICE
}

function uninstall_all() {
    echo ""
    read -rp "  ⚠️  This will permanently remove all files, cron jobs, and the systemd service. Are you sure? [y/N]: " CONFIRM
    if [[ "${CONFIRM,,}" != "y" ]]; then
        echo "  Uninstall cancelled."
        return
    fi
    log "🚨 Uninstalling Honor Tradition with Tech..."
    TMPCRON=$(mktemp)
    crontab -l 2>/dev/null | grep -v "$INSTALL_DIR" > "$TMPCRON" || true
    crontab "$TMPCRON" || true
    rm -f "$TMPCRON"
    maybe_sudo systemctl disable --now flag-audio-http 2>/dev/null || true
    maybe_sudo rm -f /etc/systemd/system/flag-audio-http.service
    maybe_sudo systemctl daemon-reload
    maybe_sudo rm -rf "$INSTALL_DIR"
    echo "✅ All files and cron jobs removed!"
    exit 0
}

function update_or_install() {
    log "🚀 Running setup.sh version $SETUP_VERSION"
    log "🔧 Setting up Sonos Scheduled Playback Environment..."

    log "📦 Installing system dependencies..."
    maybe_sudo apt update | tee -a "$LOG_FILE"
    maybe_sudo apt install -y python3-full python3-venv ffmpeg jq wget | tee -a "$LOG_FILE"

    log "📁 Creating $AUDIO_DIR..."
    maybe_sudo mkdir -p "$AUDIO_DIR"
    maybe_sudo chown "$(whoami)" "$AUDIO_DIR"
    maybe_sudo mkdir -p "$INSTALL_DIR"
    maybe_sudo chown "$(whoami)" "$INSTALL_DIR"
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
        # Don't overwrite the config.json we just wrote
        if [ "$file" = "config.json" ]; then
            log "Skipping config.json (keeping user config)"
            continue
        fi
        if wget -q "$BASE_URL/$file" -O "$file"; then
            log "Downloaded: $file"
        else
            log "WARNING: $file could not be downloaded!"
        fi
    done

    # Download audio files
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

    log "🔑 Setting execute permissions on Python scripts..."
    find "$INSTALL_DIR" -maxdepth 1 -type f -name "*.py" -exec chmod +x {} \;

    if [ ! -f "$REQUIREMENTS_TXT" ]; then
        log "📝 Creating requirements.txt..."
        cat > "$REQUIREMENTS_TXT" <<EOF
soco
mutagen
astral
pytz
EOF
    fi

    if [ ! -d "$VENV_DIR" ]; then
        log "🐍 Creating Python virtual environment..."
        python3 -m venv "$VENV_DIR"
    fi

    log "📦 Installing/upgrading Python dependencies in virtualenv..."
    source "$VENV_DIR/bin/activate"
    pip install --upgrade pip
    pip install --upgrade -r "$REQUIREMENTS_TXT"
    deactivate
    log "✅ Python dependencies installed/upgraded."

    # --- Configuration wizard (reads/writes config.json) ---
    configure_setup

    # Read port back from the freshly-written config
    PORT=$(jq -r '.port' "$CONFIG_FILE")

    # Systemd audio HTTP server — always (re)write so port changes take effect
    write_service_file
    maybe_sudo systemctl enable flag-audio-http

    log "🔄 Restarting audio HTTP server..."
    maybe_sudo systemctl restart flag-audio-http

    log "🗓️  Running schedule_sonos.py to set up Sonos schedule crontab..."
    source "$VENV_DIR/bin/activate"
    "$VENV_DIR/bin/python" "$INSTALL_DIR/schedule_sonos.py"
    deactivate

    log "🏁 Setup complete."
    log ""
    log "Test your setup:"
    log "  curl -I http://localhost:${PORT}/colors.mp3"
    log ""
    log "To test Sonos playback manually, run:"
    log "  $VENV_DIR/bin/python $INSTALL_DIR/sonos_play.py"
    log ""
    log "Check status of audio server:"
    log "  sudo systemctl status flag-audio-http"
    log ""
    log "Edit your config at any time: $INSTALL_DIR/config.json"
    log "Or re-run this script and choose option 2 (Reconfigure)."
}

# ---------------------------------------------------------------------------

prompt_menu
case $CHOICE in
    1)
        update_or_install
        ;;
    2)
        configure_setup
        # Rewrite service file with new port and hot-reload
        PORT=$(jq -r '.port' "$CONFIG_FILE")
        write_service_file
        maybe_sudo systemctl enable flag-audio-http
        maybe_sudo systemctl restart flag-audio-http 2>/dev/null || true
        log "✅ Reconfiguration complete."
        ;;
    3)
        test_sonos_playback
        ;;
    4)
        uninstall_all
        ;;
    *)
        log "👋 Exiting without making changes."
        exit 0
        ;;
esac
