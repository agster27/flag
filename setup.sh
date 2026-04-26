#!/bin/bash
# setup.sh — Installs, updates, or removes the Honor Tradition with Tech project.
#
# Usage:
#   wget --no-cache https://raw.githubusercontent.com/agster27/flag/main/setup.sh -O setup.sh
#   chmod +x setup.sh
#   ./setup.sh
set -e
set -o pipefail

SETUP_VERSION="2.1.1"

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
# Read an existing config value (if config already exists) as default.
# Uses jq dot-notation for the key, e.g. cfg_default "port" "8000"
# or cfg_default "schedules[0].name" "colors".
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
# Write (or rewrite) the systemd service file for the audio HTTP server.
# Uses the current $PORT value.
# ---------------------------------------------------------------------------
function write_service_file() {
    if [ -z "$PORT" ]; then
        log "❌ PORT is not set. Cannot write audio HTTP service file."
        exit 1
    fi
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
# Interactive configuration wizard.
# Writes $CONFIG_FILE with user-supplied (or defaulted) values.
# ---------------------------------------------------------------------------
function configure_setup() {
    echo ""
    echo "============================================"
    echo "  Flag Audio Server — Configuration Wizard  "
    echo "============================================"
    echo "Press Enter to accept the value shown in [brackets]."
    echo ""

    # Ensure jq is available — needed to read/write config and build JSON
    if ! command -v jq &>/dev/null; then
        echo "  ⚠️  'jq' not found. Installing..."
        maybe_sudo apt-get install -y jq
    fi

    # Validate existing config JSON before reading it
    if [ -f "$CONFIG_FILE" ] && command -v jq &>/dev/null; then
        if ! jq empty "$CONFIG_FILE" &>/dev/null; then
            echo "  ⚠️  WARNING: $CONFIG_FILE contains invalid JSON and cannot be read."
            echo "  Your existing config will be ignored and defaults will be used."
            echo "  The broken file has been moved to ${CONFIG_FILE}.bak for inspection."
            cp "$CONFIG_FILE" "${CONFIG_FILE}.bak"
            rm -f "$CONFIG_FILE"
        fi
    fi

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

    # Hostname / IP this machine is reachable at.
    # Preferred: find a local IP on the same /24 subnet as the Sonos speaker.
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

    # Hostname / IP default: prefer detected local_ip, then fall back to existing config
    if [ -n "$local_ip" ]; then
        default_host="$local_ip"
    else
        # Try first schedule's audio_url (new format) or legacy colors_url
        if [ -f "$CONFIG_FILE" ] && command -v jq &>/dev/null; then
            _first_url=$(jq -r '(.schedules[0].audio_url // .colors_url // "") | select(. != "null")' \
                "$CONFIG_FILE" 2>/dev/null || echo "")
            if [ -n "$_first_url" ] && [ "$_first_url" != "null" ]; then
                default_host=$(echo "$_first_url" | sed 's|http://||;s|:.*||')
            else
                default_host="localhost"
            fi
        else
            default_host="localhost"
        fi
    fi
    read -rp "  Hostname or IP of THIS machine (for audio URLs) [${default_host}]: " INPUT
    HOST_ADDR="${INPUT:-$default_host}"

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

    # Wait seconds (fallback only — not exposed in wizard)
    WAIT_SECS=$(cfg_default "default_wait_seconds" "60")

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

    # Offer to sync the system clock timezone to match the configured timezone
    current_sys_tz=$(timedatectl show -p Timezone --value 2>/dev/null \
        || cat /etc/timezone 2>/dev/null \
        || echo "unknown")
    echo ""
    echo "  Current system timezone: $current_sys_tz"
    if [ "$current_sys_tz" != "$TIMEZONE" ]; then
        read -rp "  Set system timezone to '$TIMEZONE' so systemd timers fire at the right local time? [Y/n]: " SYS_TZ_INPUT
        SYS_TZ_INPUT="${SYS_TZ_INPUT:-y}"
        if [[ "${SYS_TZ_INPUT,,}" == "y" ]]; then
            maybe_sudo timedatectl set-timezone "$TIMEZONE" \
                && echo "  ✅ System timezone set to $TIMEZONE." \
                || echo "  ⚠️  Could not set system timezone automatically. Run: sudo timedatectl set-timezone $TIMEZONE"
        fi
    else
        echo "  ✅ System timezone already matches ($TIMEZONE)."
    fi
    echo ""

    # Sunset offset
    default_offset=$(cfg_default "sunset_offset_minutes" "0")
    while true; do
        read -rp "  Sunset offset minutes (negative = before sunset, e.g. -10) [${default_offset}]: " INPUT
        SUNSET_OFFSET="${INPUT:-$default_offset}"
        if [[ "$SUNSET_OFFSET" =~ ^-?[0-9]+$ ]]; then
            break
        fi
        echo "  ⚠️  Please enter an integer (e.g. 0, -15, 30)."
    done

    # ---- Scheduled Audio Plays ----
    echo ""
    echo "  === Scheduled Audio Plays ==="
    echo "  Configure which audio files to play and when."
    echo "  Time: HH:MM (24-hour local time) or 'sunset'."
    echo ""

    # Load existing schedules into parallel arrays.
    # If upgrading from the old flat-key format, pre-populate from those keys.
    declare -a _snames _sfiles _stimes
    _scount=0

    if [ -f "$CONFIG_FILE" ] && command -v jq &>/dev/null; then
        _new_count=$(jq '.schedules | length // 0' "$CONFIG_FILE" 2>/dev/null || echo "0")
        if [ "${_new_count:-0}" -gt 0 ]; then
            echo "  Found ${_new_count} existing schedule(s):"
            for (( _i=0; _i<_new_count; _i++ )); do
                _snames[$_scount]=$(jq -r ".schedules[${_i}].name" "$CONFIG_FILE")
                # Extract just the filename portion of the audio_url
                _sfiles[$_scount]=$(jq -r ".schedules[${_i}].audio_url" "$CONFIG_FILE" | sed 's|.*/||')
                _stimes[$_scount]=$(jq -r ".schedules[${_i}].time" "$CONFIG_FILE")
                echo "    $(( _scount + 1 )). name='${_snames[$_scount]}'  file='${_sfiles[$_scount]}'  time='${_stimes[$_scount]}'"
                _scount=$(( _scount + 1 ))
            done
            echo ""
            read -rp "  Keep all existing schedules? [Y/n]: " _keep
            _keep="${_keep:-y}"
            if [[ "${_keep,,}" == "n" ]]; then
                # Start fresh with defaults
                _snames=(); _sfiles=(); _stimes=(); _scount=0
                echo "  Starting fresh with default schedules (colors + taps)."
                _snames[0]="colors"; _sfiles[0]="colors.mp3"; _stimes[0]="08:00"
                _snames[1]="taps";   _sfiles[1]="taps.mp3";   _stimes[1]="sunset"
                _scount=2
            fi
        elif jq -e '.colors_url' "$CONFIG_FILE" &>/dev/null 2>&1; then
            # Old flat config — auto-migrate to the new schedules format
            echo "  ⚠️  Upgrading from legacy config format (colors_url / taps_url)."
            echo "  Pre-populating schedules from your existing settings."
            _snames[0]="colors"
            _sfiles[0]=$(jq -r '.colors_url // ""' "$CONFIG_FILE" | sed 's|.*/||')
            _stimes[0]=$(jq -r '.colors_time // "08:00"' "$CONFIG_FILE")
            _snames[1]="taps"
            _sfiles[1]=$(jq -r '.taps_url // ""' "$CONFIG_FILE" | sed 's|.*/||')
            _stimes[1]="sunset"
            _scount=2
            echo "  Pre-populated: colors (${_stimes[0]}), taps (sunset)"
        else
            # No existing schedules — use defaults
            _snames[0]="colors"; _sfiles[0]="colors.mp3"; _stimes[0]="08:00"
            _snames[1]="taps";   _sfiles[1]="taps.mp3";   _stimes[1]="sunset"
            _scount=2
        fi
    else
        # No config file yet — use defaults
        _snames[0]="colors"; _sfiles[0]="colors.mp3"; _stimes[0]="08:00"
        _snames[1]="taps";   _sfiles[1]="taps.mp3";   _stimes[1]="sunset"
        _scount=2
    fi

    # Show the user what's been pre-populated before asking for more
    if [ "$_scount" -gt 0 ]; then
        echo ""
        echo "  Default scheduled plays:"
        for (( _i=0; _i<_scount; _i++ )); do
            echo "    $(( _i + 1 )). name='${_snames[$_i]}'  file='${_sfiles[$_i]}'  time='${_stimes[$_i]}'"
        done
        echo ""
        echo "  ℹ️  To add more plays later, use option 1) List scheduled plays from the main menu."
        echo ""
        read -rp "  Continue? [Y/n]: " _continue
        _continue="${_continue:-y}"
        if [[ "${_continue,,}" == "n" ]]; then
            echo "  ⚠️  Configuration cancelled."
            return
        fi
    fi

    # Safety net: if no schedules ended up configured, restore defaults
    if [ "$_scount" -eq 0 ]; then
        echo "  ⚠️  No schedules configured. Falling back to defaults (colors + taps)."
        _snames[0]="colors"; _sfiles[0]="colors.mp3"; _stimes[0]="08:00"
        _snames[1]="taps";   _sfiles[1]="taps.mp3";   _stimes[1]="sunset"
        _scount=2
    fi

    # Build the schedules JSON array using jq for proper encoding
    SCHEDULES_JSON="[]"
    for (( i=0; i<_scount; i++ )); do
        _audio_url="http://${HOST_ADDR}:${PORT}/${_sfiles[$i]}"
        SCHEDULES_JSON=$(printf '%s' "$SCHEDULES_JSON" | jq \
            --arg name  "${_snames[$i]}" \
            --arg url   "$_audio_url" \
            --arg time  "${_stimes[$i]}" \
            '. + [{"name": $name, "audio_url": $url, "time": $time}]')
    done

    # Write the complete config.json using jq for correct JSON encoding
    echo ""
    echo "  Writing config to $CONFIG_FILE ..."
    jq -n \
        --arg      sonos_ip        "$SONOS_IP" \
        --argjson  port            "$PORT" \
        --argjson  volume          "$VOLUME" \
        --argjson  wait            "$WAIT_SECS" \
        --argjson  skip_restore    "$SKIP_RESTORE" \
        --argjson  lat             "$LATITUDE" \
        --argjson  lon             "$LONGITUDE" \
        --arg      tz              "$TIMEZONE" \
        --argjson  offset          "$SUNSET_OFFSET" \
        --argjson  schedules       "$SCHEDULES_JSON" \
        '{
          "sonos_ip":              $sonos_ip,
          "port":                  $port,
          "volume":                $volume,
          "default_wait_seconds":  $wait,
          "skip_restore_if_idle":  $skip_restore,
          "latitude":              $lat,
          "longitude":             $lon,
          "timezone":              $tz,
          "sunset_offset_minutes": $offset,
          "schedules":             $schedules
        }' > "$CONFIG_FILE"
    log "✅ config.json written."
}

# ---------------------------------------------------------------------------

function show_sunset_time() {
    echo ""
    echo "============================================"
    echo "  Today's Sunset Time"
    echo "============================================"

    if [ ! -d "$VENV_DIR" ]; then
        echo "  ⚠️  Python venv not found. Please run Install first."
        return
    fi
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "  ⚠️  config.json not found. Please run Install or Reconfigure first."
        return
    fi

    local _sunset_output
    _sunset_output=$(cd "$INSTALL_DIR" && "$VENV_DIR/bin/python" - <<'PYEOF' 2>/tmp/sunset_stderr
import sys
try:
    from config import load_config
    from schedule_sonos import get_sunset_local_time
    config = load_config()
    hour, minute = get_sunset_local_time(config)
    offset = config.get("sunset_offset_minutes", 0)
    tz = config.get("timezone", "America/New_York")
    print(f"{hour:02d}:{minute:02d}")
    print(tz)
    print(offset)
except ValueError as e:
    print(f"Polar day/night: sun does not set at this location today.", file=sys.stderr)
    sys.exit(2)
PYEOF
    )
    local _py_exit=$?

    if [ $_py_exit -eq 2 ]; then
        echo "  ⚠️  Sun does not set at this location today (polar day/night)."
        return
    elif [ $_py_exit -ne 0 ]; then
        echo "  ❌ Failed to calculate sunset time."
        if [ -s /tmp/sunset_stderr ]; then
            sed 's/^/  /' /tmp/sunset_stderr
        fi
        return
    fi

    local _time _tz _offset
    _time=$(echo "$_sunset_output" | sed -n '1p')
    _tz=$(echo "$_sunset_output" | sed -n '2p')
    _offset=$(echo "$_sunset_output" | sed -n '3p')

    echo "  🌅 Sunset today: $_time ($_tz)"

    if [ "$_offset" = "0" ] || [ -z "$_offset" ]; then
        echo "  ⏱️  No offset configured — Taps will play at sunset."
    else
        # Compute adjusted time, handling day wrap-around
        local _h _m _total _ah _am
        _h=$(echo "$_time" | cut -d: -f1)
        _m=$(echo "$_time" | cut -d: -f2)
        _total=$(( 10#$_h * 60 + 10#$_m + _offset ))
        # Wrap into [0, 1440) to handle negative offsets or overflow past midnight
        _total=$(( ((_total % 1440) + 1440) % 1440 ))
        _ah=$(( _total / 60 ))
        _am=$(( _total % 60 ))
        printf "  ⏱️  Offset: %d minutes → Taps will play at %02d:%02d\n" "$_offset" "$_ah" "$_am"
    fi
}

# ---------------------------------------------------------------------------
# Populate SUNSET_HEADER_LINE with a one-line sunset summary for the menu
# header, or leave it empty if sunset cannot be determined.
# ---------------------------------------------------------------------------

function get_sunset_header_line() {
    SUNSET_HEADER_LINE=""

    [ -d "$VENV_DIR" ] || return
    [ -f "$CONFIG_FILE" ] || return

    local _sunset_output
    _sunset_output=$(cd "$INSTALL_DIR" && "$VENV_DIR/bin/python" - <<'PYEOF' 2>/dev/null
import sys
try:
    from config import load_config
    from schedule_sonos import get_sunset_local_time
    config = load_config()
    hour, minute = get_sunset_local_time(config)
    offset = config.get("sunset_offset_minutes", 0)
    tz = config.get("timezone", "America/New_York")
    print(f"{hour:02d}:{minute:02d}")
    print(tz)
    print(offset)
except Exception:
    sys.exit(1)
PYEOF
    ) || return

    local _time _tz _offset
    _time=$(echo "$_sunset_output" | sed -n '1p')
    _tz=$(echo "$_sunset_output" | sed -n '2p')
    _offset=$(echo "$_sunset_output" | sed -n '3p')

    [ -n "$_time" ] || return

    if [ "$_offset" = "0" ] || [ -z "$_offset" ]; then
        SUNSET_HEADER_LINE="  Sunset:  🌅 $_time ($_tz)"
    else
        # Compute adjusted time, handling day wrap-around
        local _h _m _total _ah _am
        _h=$(echo "$_time" | cut -d: -f1)
        _m=$(echo "$_time" | cut -d: -f2)
        _total=$(( 10#$_h * 60 + 10#$_m + _offset ))
        _total=$(( ((_total % 1440) + 1440) % 1440 ))
        _ah=$(( _total / 60 ))
        _am=$(( _total % 60 ))
        SUNSET_HEADER_LINE=$(printf "  Sunset:  🌅 %s → Taps at %02d:%02d (offset: %d min)" "$_time" "$_ah" "$_am" "$_offset")
    fi
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

    # Use the first schedule's audio_url (new format) or fall back to legacy colors_url
    TEST_URL=$(jq -r '(.schedules[0].audio_url // .colors_url // "")' "$CONFIG_FILE")
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

function list_scheduled_plays() {
    echo ""
    echo "============================================"
    echo "  Scheduled Plays                           "
    echo "============================================"

    if [ ! -f "$CONFIG_FILE" ]; then
        echo "  ⚠️  config.json not found. Run Install first."
        return
    fi
    if ! command -v jq &>/dev/null; then
        echo "  ⚠️  'jq' not found. Cannot parse config.json."
        return
    fi

    SCHEDULE_COUNT=$(jq '.schedules | length' "$CONFIG_FILE" 2>/dev/null || echo 0)
    if [ "$SCHEDULE_COUNT" -eq 0 ]; then
        echo "  ⚠️  No schedules configured in config.json."
    else
        printf "  %-20s %-35s %-10s\n" "NAME" "AUDIO FILE" "TIME"
        printf "  %-20s %-35s %-10s\n" "--------------------" "-----------------------------------" "----------"
        for i in $(seq 0 $((SCHEDULE_COUNT - 1))); do
            _name=$(jq -r ".schedules[$i].name // \"(unnamed)\"" "$CONFIG_FILE")
            _url=$(jq -r ".schedules[$i].audio_url // \"(none)\"" "$CONFIG_FILE")
            _time=$(jq -r ".schedules[$i].time // \"(none)\"" "$CONFIG_FILE")
            _file=$(basename "$_url")
            printf "  %-20s %-35s %-10s\n" "$_name" "$_file" "$_time"
        done
    fi

    echo ""
    echo "  --- Systemd Timer Status ---"
    local _timer_output
    _timer_output=$(systemctl list-timers --all 2>/dev/null || true)
    if echo "$_timer_output" | grep -q "flag"; then
        echo "$_timer_output" | grep -E "(NEXT|flag)" | sed 's/^/  /'
    else
        echo "  (no flag timers found — run Install or Reconfigure to create them)"
    fi

    echo ""
    echo "  --- Audio HTTP Server ---"
    if systemctl is-active flag-audio-http &>/dev/null; then
        echo "  ✅ flag-audio-http is running"
    elif systemctl list-unit-files flag-audio-http.service &>/dev/null 2>&1 | grep -q "flag-audio-http"; then
        echo "  ⛔ flag-audio-http is installed but not running"
    else
        echo "  ℹ️  flag-audio-http service not installed"
    fi
    echo ""
}

function view_logs() {
    echo ""
    echo "============================================"
    echo "  Recent Logs                               "
    echo "============================================"

    SONOS_LOG="$INSTALL_DIR/sonos_play.log"

    echo ""
    echo "  --- Setup Log (last 20 lines) ---"
    if [ -f "$LOG_FILE" ]; then
        tail -n 20 "$LOG_FILE" | sed 's/^/  /'
    else
        echo "  (no setup log found at $LOG_FILE)"
    fi

    echo ""
    echo "  --- Playback Log (last 20 lines) ---"
    if [ -f "$SONOS_LOG" ]; then
        tail -n 20 "$SONOS_LOG" | sed 's/^/  /'
    else
        echo "  (no playback log found at $SONOS_LOG)"
    fi
    echo ""
}

# ---------------------------------------------------------------------------
# Detect the current installation state and set INSTALL_STATE + INSTALL_STATE_MSG.
# Must be called after variables are defined but before prompt_menu is displayed.
# ---------------------------------------------------------------------------
function show_install_required_msg() {
    echo ""
    echo "  ⚠️  This option requires a completed installation."
    echo "  Please run \"Install\" first (option 5)."
}

function detect_install_state() {
    local has_venv=false has_config=false has_timers=false

    [ -d "$VENV_DIR" ] && has_venv=true || true
    [ -f "$CONFIG_FILE" ] && has_config=true || true
    local _timer_files
    _timer_files=$(ls /etc/systemd/system/flag-*.timer 2>/dev/null || true)
    [ -n "$_timer_files" ] && has_timers=true || true

    if ! $has_venv && ! $has_config; then
        INSTALL_STATE="none"
        INSTALL_STATE_MSG="⚠️  No installation detected. Please select \"Install\" to get started."
    elif ! $has_venv && $has_config; then
        INSTALL_STATE="partial_no_venv"
        INSTALL_STATE_MSG="⚠️  config.json found but Python environment is missing. Please select \"Install\" to complete setup."
    elif $has_venv && ! $has_config; then
        INSTALL_STATE="partial_no_config"
        INSTALL_STATE_MSG="⚠️  Python environment found but config.json is missing. Please select \"Reconfigure\" to set up your config."
    elif $has_venv && $has_config && ! $has_timers; then
        INSTALL_STATE="partial_no_timers"
        INSTALL_STATE_MSG="⚠️  Installation found but no systemd timers detected. Select \"Install\" or \"Reconfigure\" to generate timers."
    else
        INSTALL_STATE="installed"
        INSTALL_STATE_MSG=""
    fi
}

function prompt_menu() {
    echo ""
    echo "============================================"
    echo "  Honor Tradition with Tech — Setup"
    echo "============================================"
    echo "  Version: $SETUP_VERSION"
    if [ -d "$VENV_DIR" ]; then
        echo "  Status:  ✅ Installed"
    else
        echo "  Status:  ⚙️  Not installed"
    fi

    if [ -f "$CONFIG_FILE" ] && command -v jq &>/dev/null; then
        _ip=$(jq -r '.sonos_ip // "not set"' "$CONFIG_FILE" 2>/dev/null)
        _cnt=$(jq '.schedules | length' "$CONFIG_FILE" 2>/dev/null || echo 0)
        echo "  Config:  Sonos IP: $_ip | $_cnt schedule(s)"
    fi

    get_sunset_header_line || true
    [ -n "$SUNSET_HEADER_LINE" ] && echo "$SUNSET_HEADER_LINE" || true

    if [ "$INSTALL_STATE" != "installed" ]; then
        echo ""
        echo "  ============================================"
        echo "  $INSTALL_STATE_MSG"
        echo "  ============================================"
    fi

    # Determine per-option annotations
    local _install_label="Install (first-time setup)"
    local _list_label="List scheduled plays"
    local _sunset_label="Show sunset time"
    local _test_label="Test Sonos playback"
    local _logs_label="View logs"
    local _upgrade_label="Upgrade (update scripts, keep config)"
    local _reconfig_label="Reconfigure (edit config.json interactively)"

    if [ "$INSTALL_STATE" = "none" ] || [ "$INSTALL_STATE" = "partial_no_venv" ]; then
        _install_label="Install (first-time setup)  ← start here"
        _list_label="List scheduled plays  (requires install)"
        _sunset_label="Show sunset time  (requires install)"
        _test_label="Test Sonos playback  (requires install)"
        _logs_label="View logs  (requires install)"
        _upgrade_label="Upgrade (update scripts, keep config)  (requires install)"
    fi

    if [ "$INSTALL_STATE" = "none" ]; then
        _reconfig_label="Reconfigure (edit config.json interactively)  (requires install)"
    fi

    echo ""
    echo "  ── Read-only ──────────────────────────"
    echo "  1) $_list_label"
    echo "  2) $_sunset_label"
    echo "  3) $_test_label"
    echo "  4) $_logs_label"
    echo ""
    echo "  ── Configuration ──────────────────────"
    echo "  5) $_install_label"
    echo "  6) $_upgrade_label"
    echo "  7) $_reconfig_label"
    echo ""
    echo "  ── Danger zone ────────────────────────"
    echo "  8) Uninstall completely"
    echo ""
    echo "  9) Exit without doing anything"
    echo ""
    read -rp "Enter your choice [1-9]: " CHOICE
}

function uninstall_all() {
    echo ""
    read -rp "  ⚠️  This will permanently remove all files and systemd services/timers. Are you sure? [y/N]: " CONFIRM
    if [[ "${CONFIRM,,}" != "y" ]]; then
        echo "  Uninstall cancelled."
        return
    fi
    log "🚨 Uninstalling Honor Tradition with Tech..."

    # Disable and stop all flag-related timers first.
    # Iterate over found files individually rather than relying on shell glob
    # expansion in systemctl arguments, so the loop is safe when no files exist.
    for timer_file in /etc/systemd/system/flag-*.timer; do
        [ -f "$timer_file" ] || continue
        timer_unit=$(basename "$timer_file")
        maybe_sudo systemctl disable --now "$timer_unit" 2>/dev/null || true
    done

    # Disable and stop all flag-related services
    for service_file in /etc/systemd/system/flag-*.service; do
        [ -f "$service_file" ] || continue
        service_unit=$(basename "$service_file")
        maybe_sudo systemctl disable --now "$service_unit" 2>/dev/null || true
    done

    # Remove all flag unit files and reload systemd
    maybe_sudo rm -f /etc/systemd/system/flag-*.timer
    maybe_sudo rm -f /etc/systemd/system/flag-*.service
    maybe_sudo systemctl daemon-reload

    maybe_sudo rm -rf "$INSTALL_DIR"

    # Remove setup.sh itself if it lives outside INSTALL_DIR
    SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
    case "$SELF" in
        "$INSTALL_DIR"/*) ;;  # already deleted with INSTALL_DIR
        *) maybe_sudo rm -f "$SELF" ;;
    esac

    echo "✅ All files, systemd timers, and services removed!"
    exit 0
}

function install_fresh() {
    log "🚀 Running setup.sh version $SETUP_VERSION"
    log "🔧 Setting up Sonos Scheduled Playback Environment..."

    log "📦 Installing system dependencies..."
    maybe_sudo apt-get update | tee -a "$LOG_FILE" || { log "❌ apt-get update failed. Check your network connection."; exit 1; }
    maybe_sudo apt-get install -y python3-full python3-venv ffmpeg jq wget | tee -a "$LOG_FILE" || { log "❌ apt-get install failed. Check the log above for details."; exit 1; }

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

    # Generate systemd timer units for all schedules.
    # All unit files are written first, then daemon-reload, then enable — as
    # required to avoid set -e failures from enabling before files exist.
    log "🗓️  Running schedule_sonos.py to install systemd timers..."
    maybe_sudo "$VENV_DIR/bin/python" "$INSTALL_DIR/schedule_sonos.py"

    log "🏁 Setup complete."
    log ""
    log "Test your setup:"
    log "  curl -I http://localhost:${PORT}/colors.mp3"
    log ""
    log "To test Sonos playback manually, run:"
    log "  $VENV_DIR/bin/python $INSTALL_DIR/sonos_play.py <audio_url>"
    log ""
    log "Check status of the audio server:"
    log "  sudo systemctl status flag-audio-http"
    log ""
    log "Check active timers:"
    log "  systemctl list-timers --all | grep flag"
    log ""
    log "View playback logs:"
    log "  journalctl -u flag-colors -n 50"
    log "  journalctl -u flag-taps -n 50"
    log ""
    log "Edit your config at any time: $INSTALL_DIR/config.json"
    log "Or re-run this script and choose option 6 (Reconfigure)."
}

function upgrade_scripts() {
    log "🚀 Running setup.sh version $SETUP_VERSION — Upgrade"

    if [ ! -d "$VENV_DIR" ]; then
        log "⚠️  Installation not detected (no virtualenv at $VENV_DIR)."
        log "    Please run Install (option 4) first."
        return
    fi

    log "🔧 Upgrading scripts and dependencies (config.json will be preserved)..."

    maybe_sudo mkdir -p "$AUDIO_DIR"
    maybe_sudo chown "$(whoami)" "$AUDIO_DIR"
    cd "$INSTALL_DIR"

    # Download latest root files, skipping config.json
    log "🌐 Fetching file list from GitHub API (root)..."
    FILES=$(wget -qO- https://api.github.com/repos/agster27/flag/contents/ | jq -r '.[] | select(.type == "file") | .name')
    if [ -z "$FILES" ]; then
        log "❌ Could not fetch file list from GitHub. Exiting."
        exit 1
    fi

    log "⬇️  Downloading latest scripts from GitHub (root)..."
    for file in $FILES; do
        if [ "$file" = "config.json" ]; then
            log "Skipping config.json (preserving user config)"
            continue
        fi
        if wget -q "$BASE_URL/$file" -O "$file"; then
            log "Downloaded: $file"
        else
            log "WARNING: $file could not be downloaded!"
        fi
    done

    # Download latest audio files
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

    log "📦 Upgrading Python dependencies in virtualenv..."
    source "$VENV_DIR/bin/activate"
    pip install --upgrade pip
    pip install --upgrade -r "$REQUIREMENTS_TXT"
    deactivate
    log "✅ Python dependencies upgraded."

    # Regenerate systemd timer units with the existing config
    log "🗓️  Regenerating systemd timer units..."
    maybe_sudo "$VENV_DIR/bin/python" "$INSTALL_DIR/schedule_sonos.py"

    log "✅ Upgrade complete. Your config.json was not changed."
}

# ---------------------------------------------------------------------------

while true; do
    detect_install_state
    prompt_menu
    case $CHOICE in
        1)
            if [ "$INSTALL_STATE" = "none" ] || [ "$INSTALL_STATE" = "partial_no_venv" ]; then
                show_install_required_msg
                echo ""
                read -rp "  Press Enter to return to menu..." _pause
            else
                list_scheduled_plays
                echo ""
                read -rp "  Press Enter to return to menu..." _pause
            fi
            ;;
        2)
            if [ "$INSTALL_STATE" = "none" ] || [ "$INSTALL_STATE" = "partial_no_venv" ]; then
                show_install_required_msg
                echo ""
                read -rp "  Press Enter to return to menu..." _pause
            else
                show_sunset_time
                echo ""
                read -rp "  Press Enter to return to menu..." _pause
            fi
            ;;
        3)
            if [ "$INSTALL_STATE" = "none" ] || [ "$INSTALL_STATE" = "partial_no_venv" ]; then
                show_install_required_msg
                echo ""
                read -rp "  Press Enter to return to menu..." _pause
            else
                test_sonos_playback
                echo ""
                read -rp "  Press Enter to return to menu..." _pause
            fi
            ;;
        4)
            if [ "$INSTALL_STATE" = "none" ] || [ "$INSTALL_STATE" = "partial_no_venv" ]; then
                show_install_required_msg
                echo ""
                read -rp "  Press Enter to return to menu..." _pause
            else
                view_logs
                echo ""
                read -rp "  Press Enter to return to menu..." _pause
            fi
            ;;
        5)
            install_fresh
            ;;
        6)
            if [ "$INSTALL_STATE" = "none" ] || [ "$INSTALL_STATE" = "partial_no_venv" ]; then
                show_install_required_msg
                echo ""
                read -rp "  Press Enter to return to menu..." _pause
            else
                upgrade_scripts
            fi
            ;;
        7)
            if [ "$INSTALL_STATE" = "none" ]; then
                show_install_required_msg
            else
                configure_setup
                # Rewrite service file with new port and hot-reload
                PORT=$(jq -r '.port' "$CONFIG_FILE")
                write_service_file
                maybe_sudo systemctl enable flag-audio-http
                maybe_sudo systemctl restart flag-audio-http 2>/dev/null || true
                # Regenerate systemd timer units with the updated config
                if [ -d "$VENV_DIR" ]; then
                    log "🗓️  Regenerating systemd timer units..."
                    maybe_sudo "$VENV_DIR/bin/python" "$INSTALL_DIR/schedule_sonos.py"
                else
                    log "⚠️  Python venv not found. Run option 5 (Install) to create systemd timers."
                fi
                log "✅ Reconfiguration complete."
            fi
            ;;
        8)
            uninstall_all
            ;;
        *)
            log "👋 Exiting without making changes."
            exit 0
            ;;
    esac
done
