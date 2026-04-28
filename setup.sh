#!/bin/bash
# setup.sh — Installs, updates, or removes the Honor Tradition with Tech project.
#
# Usage:
#   wget --no-cache https://raw.githubusercontent.com/agster27/flag/main/setup.sh -O setup.sh
#   chmod +x setup.sh
#   ./setup.sh
set -e
set -o pipefail

SETUP_VERSION="2.4.1"

# Menu option numbers — single source of truth so messages never drift.
readonly MENU_LIST=1
readonly MENU_SUNSET=2
readonly MENU_TEST=3
readonly MENU_LOGS=4
readonly MENU_INSTALL=5
readonly MENU_UPGRADE=6
readonly MENU_RECONFIG=7
readonly MENU_UNINSTALL=8
readonly MENU_EXIT=9

# ---------------------------------------------------------------------------
# Table of contents
# ---------------------------------------------------------------------------
#   Helpers:         maybe_sudo, log, cfg_default, _ensure_install_dir
#   Service writer:  write_service_file
#   Speaker picker:  _pick_speakers_for_test, discover_sonos_speakers
#   Configuration:   configure_setup
#   Sunset:          show_sunset_time, get_sunset_header_line
#   Status:          test_sonos_playback, list_scheduled_plays, view_logs
#   Install state:   detect_install_state, show_install_required_msg,
#                    _require_install, _resolve_speaker_names
#   Menu:            prompt_menu
#   Lifecycle:       install_fresh, upgrade_scripts, uninstall_all
#   CLI parsing:     _print_usage + arg dispatch
#   Main loop:       (bottom of file)
# ---------------------------------------------------------------------------

BASE_URL="https://raw.githubusercontent.com/agster27/flag/main"
INSTALL_DIR="/opt/flag"
AUDIO_DIR="$INSTALL_DIR/audio"
VENV_DIR="$INSTALL_DIR/sonos-env"
LOG_FILE="$INSTALL_DIR/setup.log"
REQUIREMENTS_TXT="$INSTALL_DIR/requirements.txt"
CONFIG_FILE="$INSTALL_DIR/config.json"

# Session-level cache for Sonos speaker name lookups (IP → player_name).
# Populated by _resolve_speaker_names; persists for the lifetime of this
# setup.sh invocation so repeated menu renders don't re-query the network.
# The '2>/dev/null || true' guard makes this a no-op on bash < 4.0 that
# lacks associative array support — the feature degrades to bare IPs.
declare -A _SPEAKER_NAME_CACHE 2>/dev/null || true

# Per-day cache for get_sunset_header_line — avoids a Python cold-start on
# every menu render.  Both vars are set together; an empty _SUNSET_CACHE_DATE
# means the cache is cold.
_SUNSET_CACHE_DATE=""
_SUNSET_CACHE_LINE=""

# ---------------------------------------------------------------------------
# Sudo wrapper: runs a command as root when the current user is not root,
# no-ops (runs directly) when already root so 'sudo' is never invoked
# unnecessarily during privileged CI or container runs.
# ---------------------------------------------------------------------------
function maybe_sudo() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        sudo "$@"
    fi
}

# ---------------------------------------------------------------------------
# Create $INSTALL_DIR if it does not exist and ensure the current user owns it.
# Called only from install_fresh, upgrade_scripts, and the interactive menu
# entry point — never from uninstall_all or the --help / --uninstall CLI paths.
# ---------------------------------------------------------------------------
function _ensure_install_dir() {
    maybe_sudo mkdir -p "$INSTALL_DIR"
    maybe_sudo chown "$(whoami)" "$INSTALL_DIR"
}

# ---------------------------------------------------------------------------
# Logging helper: writes timestamped messages to both $LOG_FILE (appended)
# and stdout via tee.  Falls back to stdout-only when $LOG_FILE's parent
# directory does not exist or is not writable (e.g. before install_fresh has
# created /opt/flag).
# ---------------------------------------------------------------------------
function log() {
    local _msg="[$(date +'%Y-%m-%d %H:%M:%S')] $*"
    if [[ -n "$LOG_FILE" && -d "$(dirname "$LOG_FILE")" && -w "$(dirname "$LOG_FILE")" ]]; then
        echo "$_msg" | tee -a "$LOG_FILE"
    else
        echo "$_msg"
    fi
}

# ---------------------------------------------------------------------------
# Read an existing config value (if config already exists) as default.
# Uses jq dot-notation for the key, e.g. cfg_default "port" "8000"
# or cfg_default "schedules[0].name" "colors".
# ---------------------------------------------------------------------------
function cfg_default() {
    local key="$1" fallback="$2"
    if [ -f "$CONFIG_FILE" ] && command -v jq &>/dev/null; then
        local val; val=$(jq -r ".${key} // empty" "$CONFIG_FILE" 2>/dev/null)
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
# Multi-select speaker picker for test playback.
# Sets SONOS_IPS_JSON to a JSON array of selected IPs, or empty array when
# falling through to manual entry.
# Sets _USE_CONFIG_FILE_DIRECTLY=true if the user picks option 0.
# Requires the Python venv (with soco installed) to already exist.
# ---------------------------------------------------------------------------
function _pick_speakers_for_test() {
    SONOS_IPS_JSON="[]"
    _USE_CONFIG_FILE_DIRECTLY=false

    if [ ! -d "$VENV_DIR" ]; then
        return
    fi
    log "🔍 Scanning network for Sonos speakers..."
    # Use tab as delimiter to avoid conflicts with speaker names that may contain '|'
    DISCOVER_EXIT=0
    DISCOVERED=$("$VENV_DIR/bin/python" - <<'PYEOF' 2>/dev/null
import sys
try:
    from soco.discovery import discover
    devices = sorted(discover(timeout=5) or [], key=lambda d: d.player_name)
    for d in devices:
        print(f"{d.player_name}\t{d.ip_address}")
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
PYEOF
) || DISCOVER_EXIT=$?
    if [ -z "$DISCOVERED" ]; then
        echo ""
        if [ $DISCOVER_EXIT -ne 0 ]; then
            echo "  ⚠️  Sonos discovery encountered an error. You can enter the IP address manually."
        else
            echo "  ⚠️  No Sonos speakers found on the network."
            echo "  You can enter the IP address manually."
        fi
        return
    fi

    echo ""
    echo "  Found Sonos speakers:"
    echo "    0) Use all currently configured speakers from config.json"
    i=1
    declare -a _TEST_IPS
    # _TEST_IPS is 1-indexed: _TEST_IPS[$i] maps to the user's "Select N" input
    while IFS=$'\t' read -r name ip; do
        echo "    $i) $name — $ip"
        _TEST_IPS[$i]="$ip"
        ((i++))
    done <<< "$DISCOVERED"
    echo ""

    COUNT=$((i - 1))
    echo "  Enter the numbers of the speakers to test (comma-separated, e.g. 1,3)."
    echo "  Enter 0 for all configured speakers, press Enter for all discovered, or type IPs manually."
    while true; do
        read -rp "  Selection [0, 1-${COUNT}, comma-separated, or Enter for all discovered]: " SEL
        if [ -z "$SEL" ]; then
            # Select all discovered speakers
            SONOS_IPS_JSON=$(printf '%s\n' "${_TEST_IPS[@]:1:$COUNT}" | jq -R . | jq -s .)
            echo "  ✅ Selected all $COUNT discovered speaker(s)."
            return
        fi
        if [ "$SEL" = "0" ]; then
            _USE_CONFIG_FILE_DIRECTLY=true
            echo "  ✅ Will use all configured speakers from config.json."
            return
        fi
        # Validate and parse comma-separated selection
        local _valid=true
        local _selected_json="[]"
        IFS=',' read -ra _parts <<< "$SEL"
        for _part in "${_parts[@]}"; do
            _n="${_part// /}"  # strip spaces
            if [[ "$_n" =~ ^[0-9]+$ ]] && [ "$_n" -ge 1 ] && [ "$_n" -le "$COUNT" ]; then
                _selected_json=$(echo "$_selected_json" | jq --arg ip "${_TEST_IPS[$_n]}" '. + [$ip]')
            else
                echo "  ⚠️  Invalid selection: '$_n'. Enter 0, or numbers between 1 and $COUNT."
                _valid=false
                break
            fi
        done
        if [ "$_valid" = "true" ]; then
            SONOS_IPS_JSON="$_selected_json"
            echo "  ✅ Selected $(echo "$SONOS_IPS_JSON" | jq 'length') speaker(s)."
            return
        fi
    done
}

# ---------------------------------------------------------------------------
# Discover Sonos speakers and let the user select ONE OR MORE for synchronized
# multi-speaker playback.  Sets SPEAKERS_JSON to a JSON array of speaker objects
# ({ip, name}).  Per-speaker volume is added later in configure_setup after the
# global volume has been established.
# Requires the Python venv (with soco installed) to already exist.
# ---------------------------------------------------------------------------
function discover_sonos_speakers() {
    SPEAKERS_JSON="[]"

    if [ ! -d "$VENV_DIR" ]; then
        return
    fi

    log "🔍 Scanning network for Sonos speakers..."
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

    if [ -z "$DISCOVERED" ]; then
        echo ""
        echo "  ⚠️  No Sonos speakers found on the network."
        return
    fi

    echo ""
    echo "  Found Sonos speakers:"
    i=1
    declare -a DISC_IPS
    declare -a DISC_NAMES
    # Read existing configured IPs for annotation (handle both legacy and new format)
    _existing_cfg_ips=""
    if [ -f "$CONFIG_FILE" ] && command -v jq &>/dev/null; then
        _existing_cfg_ips=$(jq -r '.speakers // [] | map(if type == "string" then . else .ip end) | .[]' "$CONFIG_FILE" 2>/dev/null) || true
    fi
    while IFS=$'\t' read -r name ip; do
        _marker=""
        if [ -n "$_existing_cfg_ips" ] && echo "$_existing_cfg_ips" | grep -qx "$ip" 2>/dev/null; then
            _marker="  [currently configured]"
        fi
        echo "    $i) $name — $ip${_marker}"
        DISC_IPS[$i]="$ip"
        DISC_NAMES[$i]="$name"
        ((i++))
    done <<< "$DISCOVERED"
    COUNT=$((i - 1))
    echo ""

    if [ "$COUNT" -eq 1 ]; then
        echo "  ✅ Only one speaker found. Using: ${DISC_IPS[1]}"
        SPEAKERS_JSON=$(jq -n --arg ip "${DISC_IPS[1]}" --arg name "${DISC_NAMES[1]}" '[{ip: $ip, name: $name}]')
        return
    fi

    echo "  Enter the numbers of the speakers to use (comma-separated, e.g. 1,3)."
    echo "  Press Enter to select all, or type IPs manually instead."
    while true; do
        read -rp "  Selection [1-${COUNT}, comma-separated, or Enter for all]: " SEL
        if [ -z "$SEL" ]; then
            # Select all discovered speakers
            SPEAKERS_JSON=$(
                for (( j=1; j<=COUNT; j++ )); do
                    jq -n --arg ip "${DISC_IPS[$j]}" --arg name "${DISC_NAMES[$j]}" '{ip: $ip, name: $name}'
                done | jq -s .
            )
            echo "  ✅ Selected all $COUNT speaker(s)."
            return
        fi
        # Validate and parse comma-separated selection
        local _valid=true
        local _selected_json="[]"
        IFS=',' read -ra _parts <<< "$SEL"
        for _part in "${_parts[@]}"; do
            _n="${_part// /}"  # strip spaces
            if [[ "$_n" =~ ^[0-9]+$ ]] && [ "$_n" -ge 1 ] && [ "$_n" -le "$COUNT" ]; then
                _selected_json=$(echo "$_selected_json" | jq --arg ip "${DISC_IPS[$_n]}" --arg name "${DISC_NAMES[$_n]}" '. + [{ip: $ip, name: $name}]')
            else
                echo "  ⚠️  Invalid selection: '$_n'. Enter numbers between 1 and $COUNT."
                _valid=false
                break
            fi
        done
        if [ "$_valid" = "true" ]; then
            SPEAKERS_JSON="$_selected_json"
            echo "  ✅ Selected $(echo "$SPEAKERS_JSON" | jq 'length') speaker(s)."
            return
        fi
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

    # Auto-migrate legacy speakers format (array of IP strings → array of objects)
    if [ -f "$CONFIG_FILE" ] && command -v jq &>/dev/null; then
        _is_legacy=$(jq '(.speakers // []) | if length == 0 then false else (.[0] | type == "string") end' "$CONFIG_FILE" 2>/dev/null || echo false)
        if [ "$_is_legacy" = "true" ]; then
            log "🔄 Migrating speakers from legacy IP-string format to object format..."
            jq '.speakers |= map({ip: .})' "$CONFIG_FILE" > "${CONFIG_FILE}.tmp" && mv "${CONFIG_FILE}.tmp" "$CONFIG_FILE"
        fi
    fi

    # Speakers list — try auto-discovery first, fall back to manual entry
    # Read existing speaker IPs from config for use as a default display
    _existing_speakers_display=""
    if [ -f "$CONFIG_FILE" ] && command -v jq &>/dev/null; then
        _existing_speakers_display=$(jq -r '.speakers // [] | map(.ip) | join(", ")' "$CONFIG_FILE" 2>/dev/null || echo "")
    fi

    discover_sonos_speakers
    if [ "$(echo "$SPEAKERS_JSON" | jq 'length')" -eq 0 ]; then
        # Discovery found nothing (or venv not ready) — prompt for manual IPs
        echo ""
        if [ -n "$_existing_speakers_display" ]; then
            read -rp "  Sonos speaker IP(s), comma-separated [${_existing_speakers_display}]: " INPUT
        else
            read -rp "  Sonos speaker IP(s), comma-separated (e.g. 10.0.0.10,10.0.0.11): " INPUT
        fi
        if [ -z "$INPUT" ] && [ -n "$_existing_speakers_display" ]; then
            INPUT="$_existing_speakers_display"
        fi
        # Build JSON array of {ip} objects from comma-separated input,
        # preserving any existing per-speaker volume from the current config.
        SPEAKERS_JSON="[]"
        IFS=',' read -ra _ips <<< "$INPUT"
        for _ip in "${_ips[@]}"; do
            _ip="${_ip// /}"  # strip spaces
            if [ -n "$_ip" ]; then
                # Carry over existing object if available, else create a bare {ip}
                _existing_obj="null"
                if [ -f "$CONFIG_FILE" ] && command -v jq &>/dev/null; then
                    _existing_obj=$(jq --arg ip "$_ip" \
                        '(.speakers // []) | map(select(.ip == $ip)) | first // null' \
                        "$CONFIG_FILE" 2>/dev/null || echo "null")
                fi
                if [ "$_existing_obj" != "null" ] && [ -n "$_existing_obj" ]; then
                    SPEAKERS_JSON=$(echo "$SPEAKERS_JSON" | jq --argjson obj "$_existing_obj" '. + [$obj]')
                else
                    SPEAKERS_JSON=$(echo "$SPEAKERS_JSON" | jq --arg ip "$_ip" '. + [{ip: $ip}]')
                fi
            fi
        done
    fi

    # Derive a single IP from SPEAKERS_JSON for local-IP detection below
    SONOS_IP=$(echo "$SPEAKERS_JSON" | jq -r '.[0].ip // ""')

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
            _first_url=$(jq -r '.schedules[0].audio_url // .colors_url // ""' \
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
        read -rp "  Sonos volume 0–100 (global default) [${default_vol}]: " INPUT
        VOLUME="${INPUT:-$default_vol}"
        if [[ "$VOLUME" =~ ^[0-9]+$ ]] && [ "$VOLUME" -ge 0 ] && [ "$VOLUME" -le 100 ]; then
            break
        fi
        echo "  ⚠️  Please enter a number between 0 and 100."
    done

    # Per-speaker volume overrides
    # Walk through each speaker in SPEAKERS_JSON and optionally prompt for a
    # volume override.  Pressing Enter keeps the speaker at the global default.
    _spk_count=$(echo "$SPEAKERS_JSON" | jq 'length')
    if [ "$_spk_count" -gt 0 ]; then
        echo ""
        echo "  Per-speaker volume overrides (Enter to use global default of $VOLUME):"
        _new_speakers_json="[]"
        for (( _si=0; _si<_spk_count; _si++ )); do
            _spk_obj=$(echo "$SPEAKERS_JSON" | jq ".[$_si]")
            _spk_ip=$(echo "$_spk_obj" | jq -r '.ip // ""')
            _spk_name=$(echo "$_spk_obj" | jq -r '.name // ""')
            # Use existing per-speaker volume as default if present, else global
            _spk_existing_vol=$(echo "$_spk_obj" | jq -r 'if has("volume") then .volume | tostring else "" end')
            if [ -n "$_spk_existing_vol" ]; then
                _spk_default="$_spk_existing_vol"
            else
                _spk_default="$VOLUME"
            fi
            if [ -n "$_spk_name" ]; then
                _spk_label="\"$_spk_name\" ($_spk_ip)"
            else
                _spk_label="$_spk_ip"
            fi
            while true; do
                read -rp "  Volume for ${_spk_label} [${_spk_default}]: " _spk_vol_input
                _spk_vol_input="${_spk_vol_input:-$_spk_default}"
                if [[ "$_spk_vol_input" =~ ^[0-9]+$ ]] && [ "$_spk_vol_input" -ge 0 ] && [ "$_spk_vol_input" -le 100 ]; then
                    break
                fi
                echo "  ⚠️  Please enter a number between 0 and 100."
            done
            if [ "$_spk_vol_input" = "$VOLUME" ]; then
                # Volume matches global default — omit the field so the speaker uses
                # the top-level default implicitly (keeps config clean).
                _new_speakers_json=$(echo "$_new_speakers_json" | jq \
                    --argjson obj "$_spk_obj" \
                    '. + [$obj | del(.volume)]')
            else
                # Volume differs from global — store explicit per-speaker override.
                _new_speakers_json=$(echo "$_new_speakers_json" | jq \
                    --argjson obj "$_spk_obj" \
                    --argjson vol "$_spk_vol_input" \
                    '. + [$obj + {volume: $vol}]')
            fi
        done
        SPEAKERS_JSON="$_new_speakers_json"
    fi

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
        --argjson  speakers        "$SPEAKERS_JSON" \
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
          "speakers":              $speakers,
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

    # Return cached value if it was computed today.
    local _today
    _today=$(date +%Y-%m-%d)
    if [ "$_SUNSET_CACHE_DATE" = "$_today" ]; then
        SUNSET_HEADER_LINE="$_SUNSET_CACHE_LINE"
        return
    fi

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

    # Populate per-day cache.
    _SUNSET_CACHE_DATE="$_today"
    _SUNSET_CACHE_LINE="$SUNSET_HEADER_LINE"
}

# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Interactive speaker picker + one-shot playback test.
# Calls _pick_speakers_for_test to let the user choose discovered or manual
# speakers, then plays the first scheduled audio URL using sonos_play.py.
# Falls back to manual IP entry when Sonos discovery finds nothing.
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

    _pick_speakers_for_test

    # Option 0: run directly against the full config file (no temp-config rewrite)
    if [ "$_USE_CONFIG_FILE_DIRECTLY" = "true" ]; then
        TEST_URL=$(jq -r '(.schedules[0].audio_url // "")' "$CONFIG_FILE")
        _cfg_speakers=$(jq -r '.speakers // [] | map(if type == "string" then . else .ip end) | join(", ")' "$CONFIG_FILE" 2>/dev/null || echo "configured")
        echo ""
        echo "  🔊 Playing test sound on configured speaker(s): $_cfg_speakers ..."
        echo "     URL: $TEST_URL"
        echo ""
        echo "  ⏳ This may take 30–90 seconds. Watch progress:"
        echo "     tail -f $LOG_FILE"
        echo ""
        FLAG_CONFIG="$CONFIG_FILE" "$VENV_DIR/bin/python" "$INSTALL_DIR/sonos_play.py" "$TEST_URL"
        PLAY_EXIT=$?
        if [ $PLAY_EXIT -eq 0 ]; then
            echo "  ✅ Test playback complete."
        else
            echo "  ❌ Test playback failed. Check $LOG_FILE for details."
        fi
        echo "  📋 Full log: $LOG_FILE"
        return
    fi

    # No speakers selected from discovery — fall back to manual entry
    if [ "$(echo "$SONOS_IPS_JSON" | jq 'length')" -eq 0 ]; then
        read -rp "  Enter Sonos speaker IP(s), comma-separated, to test: " _MANUAL_IPS
        if [ -z "$_MANUAL_IPS" ]; then
            echo "  ⚠️  No IP provided. Aborting test."
            return
        fi
        IFS=',' read -ra _ips <<< "$_MANUAL_IPS"
        SONOS_IPS_JSON=$(printf '%s\n' "${_ips[@]}" | sed '/^[[:space:]]*$/d' | tr -d ' ' | jq -R . | jq -s .)
    fi

    if [ "$(echo "$SONOS_IPS_JSON" | jq 'length')" -eq 0 ]; then
        echo "  ⚠️  No IP provided. Aborting test."
        return
    fi

    # Use the first schedule's audio_url
    TEST_URL=$(jq -r '(.schedules[0].audio_url // "")' "$CONFIG_FILE")
    _test_ips_display=$(echo "$SONOS_IPS_JSON" | jq -r 'join(", ")')
    _test_count=$(echo "$SONOS_IPS_JSON" | jq 'length')
    echo ""
    echo "  🔊 Playing test sound on $_test_count speaker(s): $_test_ips_display ..."
    echo "     URL: $TEST_URL"
    echo ""
    echo "  ⏳ This may take 30–90 seconds. Watch progress:"
    echo "     tail -f $LOG_FILE"
    echo ""

    # Build speaker objects for the temp config, preserving per-speaker volumes
    # from the existing config when the selected IP matches a configured speaker.
    _EXISTING_SPEAKERS=$(jq '.speakers // []' "$CONFIG_FILE" 2>/dev/null || echo '[]')
    _SPEAKERS_WITH_VOLS=$(echo "$SONOS_IPS_JSON" | jq \
        --argjson existing "$_EXISTING_SPEAKERS" \
        'map(. as $ip |
            ($existing | map(
                if type == "string" then {ip: .} else . end
            ) | map(select(.ip == $ip)) | first) // {ip: $ip}
        )')

    TMPCONFIG=$(mktemp --suffix=.json) || { echo "  ❌ Failed to create temp file."; return 1; }
    if ! jq --argjson speakers "$_SPEAKERS_WITH_VOLS" '.speakers = $speakers' "$CONFIG_FILE" > "$TMPCONFIG"; then
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
    # Displays a formatted table of all configured schedules (name, audio file,
    # time), the status of active systemd flag-*.timers, and whether the audio
    # HTTP server (flag-audio-http) is running, installed-but-stopped, or absent.
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
    elif systemctl list-unit-files flag-audio-http.service 2>/dev/null | grep -q "flag-audio-http"; then
        echo "  ⛔ flag-audio-http is installed but not running"
    else
        echo "  ℹ️  flag-audio-http service not installed"
    fi
    echo ""
}

function view_logs() {
    # Shows the last 20 lines of both setup.log and sonos_play.log side by side,
    # each prefixed with a section heading so recent activity is easy to scan.
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
    echo "  Please run \"Install\" first (option ${MENU_INSTALL})."
}

# ---------------------------------------------------------------------------
# Returns 0 if the installation is sufficient to run a feature, 1 otherwise.
# When returning 1, prints the install-required message and waits for Enter.
# ---------------------------------------------------------------------------
function _require_install() {
    if [ "$INSTALL_STATE" = "none" ] || [ "$INSTALL_STATE" = "partial_no_venv" ]; then
        show_install_required_msg
        echo ""
        read -rp "  Press Enter to return to menu..." _pause
        return 1
    fi
    return 0
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

# ---------------------------------------------------------------------------
# Attempt to resolve Sonos speaker IPs to player names via soco.
# Results are cached in the _SPEAKER_NAME_CACHE associative array for the
# current setup.sh session so repeated menu renders don't re-query the network.
# Sets _RESOLVED_SPEAKERS_DISPLAY to a formatted display string (e.g.
# "Living Room (10.0.40.32), Kitchen (10.0.40.41)").
# Falls back to bare IPs when lookup fails or the venv is absent.
# ---------------------------------------------------------------------------
function _resolve_speaker_names() {
    local _cfg="$1"
    _RESOLVED_SPEAKERS_DISPLAY=""
    if ! command -v jq &>/dev/null || [ ! -f "$_cfg" ]; then
        return
    fi

    # Extract speaker objects, normalising legacy string entries to {ip} objects
    local _speakers_json
    _speakers_json=$(jq '[.speakers // [] | .[] | if type == "string" then {ip: .} else . end]' "$_cfg" 2>/dev/null) || true
    if [ -z "$_speakers_json" ] || [ "$_speakers_json" = "[]" ]; then
        return
    fi

    local _raw_ips
    _raw_ips=$(echo "$_speakers_json" | jq -r '.[].ip') || true
    if [ -z "$_raw_ips" ]; then
        return
    fi

    # Seed the session cache with names stored in the config (fast, works offline)
    while IFS=$'\t' read -r _cip _cname; do
        if [ -n "$_cname" ] && [[ -z "${_SPEAKER_NAME_CACHE[$_cip]+x}" ]]; then
            _SPEAKER_NAME_CACHE["$_cip"]="$_cname"
        fi
    done < <(echo "$_speakers_json" | jq -r '.[] | select(.name? and .name != "") | [.ip, .name] | @tsv' 2>/dev/null || true)

    # Collect IPs not yet in the session cache for soco batch lookup
    local _needs_lookup=()
    while IFS= read -r _ip; do
        if [[ -z "${_SPEAKER_NAME_CACHE[$_ip]+x}" ]]; then
            _needs_lookup+=("$_ip")
        fi
    done <<< "$_raw_ips"

    # Batch soco lookup with a short timeout so the menu is never blocked
    if [ "${#_needs_lookup[@]}" -gt 0 ] && [ -d "$VENV_DIR" ] && [ -x "$VENV_DIR/bin/python" ]; then
        local _ips_json
        _ips_json=$(printf '%s\n' "${_needs_lookup[@]}" | jq -Rs '[split("\n")[] | select(. != "")]')
        local _lookup_result
        _lookup_result=$(IPS_JSON="$_ips_json" timeout 3 "$VENV_DIR/bin/python" - <<'PYEOF' 2>/dev/null || true
import sys, json, os
try:
    from soco.discovery import discover
    ips = json.loads(os.environ.get('IPS_JSON', '[]'))
    devices = {d.ip_address: d.player_name for d in (discover(timeout=2) or [])}
    for ip in ips:
        name = devices.get(ip, "")
        print(f"{ip}\t{name}")
except Exception:
    pass
PYEOF
)
        if [ -n "$_lookup_result" ]; then
            while IFS=$'\t' read -r _lip _lname; do
                _SPEAKER_NAME_CACHE["$_lip"]="$_lname"
            done <<< "$_lookup_result"
        fi
        # Mark any still-uncached IPs so we don't retry on the next render
        for _ip in "${_needs_lookup[@]}"; do
            if [[ -z "${_SPEAKER_NAME_CACHE[$_ip]+x}" ]]; then
                _SPEAKER_NAME_CACHE["$_ip"]=""
            fi
        done
    fi

    # Build display string from cache + per-speaker volume annotation.
    # Build the IP→volume map once (single jq call) rather than once per speaker.
    local _global_vol
    _global_vol=$(jq -r '.volume // 30' "$_cfg" 2>/dev/null || echo 30)
    declare -A _VOL_MAP
    while IFS=$'\t' read -r _vmip _vmvol; do
        [ -n "$_vmip" ] && _VOL_MAP["$_vmip"]="$_vmvol"
    done < <(echo "$_speakers_json" | jq -r '.[] | [.ip, (if has("volume") then (.volume|tostring) else "" end)] | @tsv' 2>/dev/null || true)

    local _parts=()
    while IFS= read -r _ip; do
        local _name="${_SPEAKER_NAME_CACHE[$_ip]:-}"
        local _spk_vol="${_VOL_MAP[$_ip]:-}"
        local _vol_annotation=""
        if [ -n "$_spk_vol" ]; then
            _vol_annotation=" @${_spk_vol}"
        fi
        if [ -n "$_name" ]; then
            _parts+=("${_name} ${_ip}${_vol_annotation}")
        else
            _parts+=("${_ip}${_vol_annotation}")
        fi
    done <<< "$_raw_ips"

    _RESOLVED_SPEAKERS_DISPLAY=$(printf '%s, ' "${_parts[@]}")
    _RESOLVED_SPEAKERS_DISPLAY="${_RESOLVED_SPEAKERS_DISPLAY%, }"
}

# ---------------------------------------------------------------------------
# Renders the main interactive menu (header, status, config summary, sunset
# line, and numbered option list).  Reads the user's choice into $CHOICE.
# The caller is responsible for detecting install state before calling this.
# ---------------------------------------------------------------------------
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
        _speaker_count=$(jq '.speakers | length' "$CONFIG_FILE" 2>/dev/null || echo 0)
        _resolve_speaker_names "$CONFIG_FILE"
        _speakers_display="$_RESOLVED_SPEAKERS_DISPLAY"
        _cnt=$(jq '.schedules | length' "$CONFIG_FILE" 2>/dev/null || echo 0)
        if [ "$_speaker_count" -eq 0 ]; then
            echo "  Config:  Speakers: (none) | $_cnt schedule(s)"
        elif [ "$_speaker_count" -le 3 ]; then
            echo "  Config:  Speakers ($_speaker_count): $_speakers_display"
            echo "           Schedules: $_cnt"
        else
            echo "  Config:  Speakers: $_speaker_count configured"
            echo "           ($_speakers_display)"
            echo "           Schedules: $_cnt"
        fi
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

# ---------------------------------------------------------------------------
# Complete uninstall — removes all traces of the installation.  Accepts an
# optional --yes / -y flag to skip the confirmation prompt (for scripted
# teardown).  Five numbered phases:
#   1. Systemd units   — disable + stop + remove flag-* and legacy sonos-* units
#   2. Install dir     — rm -rf /opt/flag (sets LOG_FILE="" so log() falls back)
#   3. Legacy dirs     — remove older install locations (/opt/sonos-flag, etc.)
#   4. Cron entries    — purge matching entries from current-user and root crontabs
#   5. setup.sh itself — removes the script if it lives outside INSTALL_DIR
# ---------------------------------------------------------------------------
function uninstall_all() {
    # Optional first argument: "--yes" or "-y" to skip confirmation prompt.
    local _skip_confirm=false
    if [[ "${1:-}" == "--yes" || "${1:-}" == "-y" ]]; then
        _skip_confirm=true
    fi

    if [ "$_skip_confirm" = false ]; then
        echo ""
        read -rp "  ⚠️  This will permanently remove all files and systemd services/timers. Are you sure? [y/N]: " CONFIRM
        if [[ "${CONFIRM,,}" != "y" ]]; then
            echo "  Uninstall cancelled."
            return
        fi
    fi

    log "🚨 Uninstalling Honor Tradition with Tech..."

    local _removed_units=0
    local _removed_dirs=0
    local _removed_cron=0

    # -------------------------------------------------------------------------
    # 1. Systemd units — disable + stop + remove from all known locations.
    # -------------------------------------------------------------------------

    # All directories that may contain flag-related unit files.
    local _unit_dirs=(
        /etc/systemd/system
        /lib/systemd/system
        /usr/lib/systemd/system
    )

    # Legacy unit names that may not match the flag-* glob.
    local _legacy_units=(
        sonos-colors.service
        sonos-colors.timer
        sonos-taps.service
        sonos-taps.timer
    )

    # Disable + stop all flag-*.timer and flag-*.service across all locations.
    for _dir in "${_unit_dirs[@]}"; do
        for _timer_file in "$_dir"/flag-*.timer; do
            [ -f "$_timer_file" ] || continue
            _unit=$(basename "$_timer_file")
            maybe_sudo systemctl disable --now "$_unit" 2>/dev/null || true
        done
        for _svc_file in "$_dir"/flag-*.service; do
            [ -f "$_svc_file" ] || continue
            _unit=$(basename "$_svc_file")
            maybe_sudo systemctl disable --now "$_unit" 2>/dev/null || true
        done
    done

    # Disable + stop legacy unit names if present.
    for _unit in "${_legacy_units[@]}"; do
        maybe_sudo systemctl disable --now "$_unit" 2>/dev/null || true
    done

    # Remove unit files from all locations.
    for _dir in "${_unit_dirs[@]}"; do
        for _f in "$_dir"/flag-*.timer "$_dir"/flag-*.service; do
            [ -f "$_f" ] || continue
            maybe_sudo rm -f "$_f" && (( _removed_units++ )) || true
        done
        for _unit in "${_legacy_units[@]}"; do
            if [ -f "$_dir/$_unit" ]; then
                maybe_sudo rm -f "$_dir/$_unit" && (( _removed_units++ )) || true
            fi
        done
    done

    maybe_sudo systemctl daemon-reload
    maybe_sudo systemctl reset-failed 2>/dev/null || true

    # -------------------------------------------------------------------------
    # 2. Install directory.
    # -------------------------------------------------------------------------
    if [ -d "$INSTALL_DIR" ]; then
        maybe_sudo rm -rf "$INSTALL_DIR"
        LOG_FILE=""
        (( _removed_dirs++ )) || true
        log "🗑️  Removed: $INSTALL_DIR"
    else
        log "⏭️  Skipped (not present): $INSTALL_DIR"
    fi

    # -------------------------------------------------------------------------
    # 3. Legacy install locations from older versions.
    # -------------------------------------------------------------------------
    local _legacy_dirs=(
        "/opt/sonos-flag"
        "/opt/honor-tradition"
        "$HOME/flag"
        "$HOME/sonos-flag"
    )
    for _ldir in "${_legacy_dirs[@]}"; do
        if [ -d "$_ldir" ]; then
            maybe_sudo rm -rf "$_ldir"
            (( _removed_dirs++ )) || true
            log "🗑️  Removed legacy dir: $_ldir"
        else
            log "⏭️  Skipped (not present): $_ldir"
        fi
    done

    # -------------------------------------------------------------------------
    # 4. Cron entries — remove any lines referencing flag-related scripts for
    #    both the current user and root.
    # -------------------------------------------------------------------------
    local _cron_pattern='/opt/flag|flag-(audio|colors|taps|reschedule)|sonos_play\.py|schedule_sonos\.py'

    # Current user crontab.
    if crontab -l 2>/dev/null | grep -qE "$_cron_pattern"; then
        local _filtered
        _filtered=$(crontab -l 2>/dev/null | grep -vE "$_cron_pattern" || true)
        if [ -z "$_filtered" ]; then
            crontab -r 2>/dev/null || true
        else
            echo "$_filtered" | crontab -
        fi
        (( _removed_cron++ )) || true
        log "🗑️  Removed matching cron entries for current user."
    fi

    # Root crontab.
    if sudo crontab -l -u root 2>/dev/null | grep -qE "$_cron_pattern"; then
        local _root_filtered
        _root_filtered=$(sudo crontab -l -u root 2>/dev/null | grep -vE "$_cron_pattern" || true)
        if [ -z "$_root_filtered" ]; then
            sudo crontab -r -u root 2>/dev/null || true
        else
            echo "$_root_filtered" | sudo crontab -u root -
        fi
        (( _removed_cron++ )) || true
        log "🗑️  Removed matching cron entries for root."
    fi

    # -------------------------------------------------------------------------
    # 5. Remove setup.sh itself if it lives outside INSTALL_DIR.
    # -------------------------------------------------------------------------
    SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
    case "$SELF" in
        "$INSTALL_DIR"/*) ;;  # already deleted with INSTALL_DIR
        *) maybe_sudo rm -f "$SELF" ;;
    esac

    # -------------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------------
    echo ""
    echo "✅ Uninstall complete."
    echo "   • Systemd unit files removed: $_removed_units"
    echo "   • Install directories removed: $_removed_dirs"
    echo "   • Crontab user entries cleaned: $_removed_cron"
    exit 0
}

# ---------------------------------------------------------------------------
# Full first-time installation.  Order of operations:
#   1. _ensure_install_dir — create /opt/flag if absent
#   2. System dependencies  — apt-get (python3-venv, ffmpeg, jq, wget)
#   3. Download scripts     — GitHub API listing → wget each file
#   4. Python venv          — python3 -m venv + pip install requirements
#   5. Configuration wizard — configure_setup writes config.json
#   6. Systemd service      — write_service_file + enable flag-audio-http
#   7. Systemd timers       — schedule_sonos.py generates flag-*.timer units
# ---------------------------------------------------------------------------
function install_fresh() {
    _ensure_install_dir
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
    log "Or re-run this script and choose option ${MENU_RECONFIG} (Reconfigure)."
}

# ---------------------------------------------------------------------------
# In-place upgrade — downloads the latest scripts from GitHub while
# preserving config.json.  Order of operations:
#   1. _ensure_install_dir — create /opt/flag if absent
#   2. Download scripts    — GitHub API listing → wget (skip config.json)
#   3. Whitelist cleanup   — remove top-level files not in the API listing
#   4. Remove stale units  — flag-* timers/services no longer in config
#   5. Pip upgrade         — pip install --upgrade -r requirements.txt
#   6. Regenerate timers   — schedule_sonos.py rewrites flag-*.timer units
# ---------------------------------------------------------------------------
function upgrade_scripts() {
    _ensure_install_dir
    log "🚀 Running setup.sh version $SETUP_VERSION — Upgrade"

    if [ ! -d "$VENV_DIR" ]; then
        log "⚠️  Installation not detected (no virtualenv at $VENV_DIR)."
        log "    Please run Install (option ${MENU_INSTALL}) first."
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

    # -------------------------------------------------------------------------
    # Whitelist-based cleanup — remove deprecated top-level files.
    # Derived from the GitHub API listing (FILES) so new repo files are never
    # accidentally deleted. Runtime-generated artifacts are also preserved.
    # Directories audio/ and sonos-env/ (and dotfiles) are always preserved.
    # -------------------------------------------------------------------------
    local _whitelist=()
    for _f in $FILES; do _whitelist+=("$_f"); done
    _whitelist+=(setup.log sonos_play.log)

    local _deprecated_count=0

    log "🧹 Checking for deprecated top-level files..."
    for _f in "$INSTALL_DIR"/*; do
        [ -e "$_f" ] || continue
        _name=$(basename "$_f")

        # Preserve directories (audio/, sonos-env/) and dotfiles.
        if [ -d "$_f" ] || [[ "$_name" == .* ]]; then
            continue
        fi

        # Check if the file is on the whitelist.
        local _keep=false
        for _w in "${_whitelist[@]}"; do
            if [ "$_name" = "$_w" ]; then
                _keep=true
                break
            fi
        done

        if [ "$_keep" = false ]; then
            rm -f "$_f" || true
            (( _deprecated_count++ )) || true
            log "🧹 Removed deprecated file: $_name"
        fi
    done

    # -------------------------------------------------------------------------
    # Remove specific known-deprecated artifacts.
    # -------------------------------------------------------------------------

    # Top-level MP3 stubs (audio files used to live at top level before audio/).
    for _mp3 in "$INSTALL_DIR/colors.mp3" "$INSTALL_DIR/taps.mp3"; do
        if [ -f "$_mp3" ]; then
            rm -f "$_mp3" || true
            (( _deprecated_count++ )) || true
            log "🧹 Removed deprecated file: $(basename "$_mp3")"
        fi
    done

    # Stale config backups.
    for _bak in "$INSTALL_DIR"/*.bak; do
        [ -f "$_bak" ] || continue
        rm -f "$_bak" || true
        (( _deprecated_count++ )) || true
        log "🧹 Removed deprecated file: $(basename "$_bak")"
    done

    # Python bytecode artifacts.
    if [ -d "$INSTALL_DIR/__pycache__" ]; then
        rm -rf "$INSTALL_DIR/__pycache__" || true
        (( _deprecated_count++ )) || true
        log "🧹 Removed: __pycache__/"
    fi
    for _pyc in "$INSTALL_DIR"/*.pyc; do
        [ -f "$_pyc" ] || continue
        rm -f "$_pyc" || true
        (( _deprecated_count++ )) || true
        log "🧹 Removed deprecated file: $(basename "$_pyc")"
    done

    # Old venv directory names alongside the current one.
    for _old_venv in "$INSTALL_DIR/venv" "$INSTALL_DIR/.venv"; do
        if [ -d "$_old_venv" ]; then
            rm -rf "$_old_venv" || true
            (( _deprecated_count++ )) || true
            log "🧹 Removed old venv: $(basename "$_old_venv")"
        fi
    done

    # -------------------------------------------------------------------------
    # Stale systemd units — remove flag-* timer/service units that no longer
    # correspond to a schedule in config.json, and legacy sonos-* units.
    # -------------------------------------------------------------------------
    local _stale_unit_count=0

    # Explicit legacy unit names (from older installs).
    local _legacy_units=(
        sonos-colors.service
        sonos-colors.timer
        sonos-taps.service
        sonos-taps.timer
    )
    for _unit in "${_legacy_units[@]}"; do
        if [ -f "/etc/systemd/system/$_unit" ]; then
            maybe_sudo systemctl disable --now "$_unit" 2>/dev/null || true
            maybe_sudo rm -f "/etc/systemd/system/$_unit" || true
            (( _stale_unit_count++ )) || true
            log "🧹 Removed legacy unit: $_unit"
        fi
    done

    # Build the set of expected flag-<name>.timer / flag-<name>.service units
    # from config.json schedules (requires jq, which is a system dependency).
    if [ -f "$CONFIG_FILE" ] && command -v jq &>/dev/null; then
        local _expected_timers=("flag-reschedule.timer")
        local _expected_services=("flag-audio-http.service" "flag-reschedule.service" "flag-boot-reschedule.service")
        while IFS= read -r _sched_name; do
            [ -n "$_sched_name" ] || continue
            _expected_timers+=("flag-${_sched_name}.timer")
            _expected_services+=("flag-${_sched_name}.service")
        done < <(jq -r '.schedules[]?.name // empty' "$CONFIG_FILE" 2>/dev/null || true)

        # Remove any flag-*.timer not in the expected set.
        for _timer_file in /etc/systemd/system/flag-*.timer; do
            [ -f "$_timer_file" ] || continue
            _unit=$(basename "$_timer_file")
            local _expected=false
            for _e in "${_expected_timers[@]}"; do
                if [ "$_unit" = "$_e" ]; then
                    _expected=true
                    break
                fi
            done
            if [ "$_expected" = false ]; then
                maybe_sudo systemctl disable --now "$_unit" 2>/dev/null || true
                maybe_sudo rm -f "$_timer_file" || true
                (( _stale_unit_count++ )) || true
                log "🧹 Removed stale timer unit: $_unit"
            fi
        done

        # Remove any flag-*.service not in the expected set.
        for _svc_file in /etc/systemd/system/flag-*.service; do
            [ -f "$_svc_file" ] || continue
            _unit=$(basename "$_svc_file")
            local _expected=false
            for _e in "${_expected_services[@]}"; do
                if [ "$_unit" = "$_e" ]; then
                    _expected=true
                    break
                fi
            done
            if [ "$_expected" = false ]; then
                maybe_sudo systemctl disable --now "$_unit" 2>/dev/null || true
                maybe_sudo rm -f "$_svc_file" || true
                (( _stale_unit_count++ )) || true
                log "🧹 Removed stale service unit: $_unit"
            fi
        done
    fi

    maybe_sudo systemctl daemon-reload

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

    log ""
    log "✅ Upgrade complete. Your config.json was not changed."
    log "   🧹 Cleanup summary: $_deprecated_count deprecated file(s) removed, $_stale_unit_count stale unit(s) removed."
}

# ---------------------------------------------------------------------------
# CLI argument parsing — must come after all function definitions so that
# calling uninstall_all here works correctly.
# ---------------------------------------------------------------------------

function _print_usage() {
    cat <<EOF
Usage: $(basename "$0") [COMMAND] [OPTIONS]

Commands:
  uninstall          Completely remove the Flag installation (prompts for confirmation).
  --help, -h         Show this help text and exit.

Options (for uninstall):
  --yes, -y          Skip the confirmation prompt (for scripted / remote teardown).

Aliases for uninstall: --uninstall, -u

Examples:
  ./setup.sh                    # Interactive menu (default)
  ./setup.sh uninstall          # Uninstall with confirmation prompt
  ./setup.sh uninstall --yes    # Uninstall without prompting
  ./setup.sh --help             # Show this help text
EOF
}

if [[ $# -gt 0 ]]; then
    _CMD="${1:-}"
    _YES_FLAG=""

    # Detect --yes / -y anywhere in the argument list.
    for _arg in "$@"; do
        if [[ "$_arg" == "--yes" || "$_arg" == "-y" ]]; then
            _YES_FLAG="--yes"
        fi
    done

    case "$_CMD" in
        uninstall|--uninstall|-u)
            uninstall_all "$_YES_FLAG"
            ;;
        --help|-h)
            _print_usage
            exit 0
            ;;
        *)
            echo "Error: unrecognized argument '$_CMD'" >&2
            echo "" >&2
            _print_usage >&2
            exit 1
            ;;
    esac
fi

# ---------------------------------------------------------------------------

_ensure_install_dir

while true; do
    detect_install_state
    prompt_menu
    case $CHOICE in
        "$MENU_LIST")
            _require_install || continue
            list_scheduled_plays
            echo ""
            read -rp "  Press Enter to return to menu..." _pause
            ;;
        "$MENU_SUNSET")
            _require_install || continue
            show_sunset_time
            echo ""
            read -rp "  Press Enter to return to menu..." _pause
            ;;
        "$MENU_TEST")
            _require_install || continue
            test_sonos_playback
            echo ""
            read -rp "  Press Enter to return to menu..." _pause
            ;;
        "$MENU_LOGS")
            _require_install || continue
            view_logs
            echo ""
            read -rp "  Press Enter to return to menu..." _pause
            ;;
        "$MENU_INSTALL")
            install_fresh
            ;;
        "$MENU_UPGRADE")
            _require_install || continue
            upgrade_scripts
            ;;
        "$MENU_RECONFIG")
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
                    log "⚠️  Python venv not found. Run option ${MENU_INSTALL} (Install) to create systemd timers."
                fi
                log "✅ Reconfiguration complete."
            fi
            ;;
        "$MENU_UNINSTALL")
            uninstall_all
            ;;
        *)
            log "👋 Exiting without making changes."
            exit 0
            ;;
    esac
done
