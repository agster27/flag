#!/usr/bin/env bash
# tests/verify_runtime.sh — Runtime health check for a deployed flag installation.
#
# Run on the host where flag is installed (Pi / LXC / VM with systemd).
# Verifies that the static-sunset-timer architecture and play guard from
# PR #66 are deployed and that no cron backend (PR #67 removed) is active.
#
# Checks:
#   1. Each sunset schedule's timer has OnCalendar=*-*-* 03:00:00 (static)
#   2. Each sunset service is Type=simple and uses --sleep-until-schedule
#   3. check_play_guard() is defined in sonos_play.py
#   4. setup.sh has no cron-backend identifiers and /etc/cron.d/flag is absent
#   5. flag-reschedule.timer has fired recently and the timers are armed
#   6. No sunset service Started in the 01:55–02:05 window today (the
#      misfire window).  Skipped with a warning if run before 02:05.
#
# Usage (read-only — does not need root):
#   bash tests/verify_runtime.sh
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed
#   2 — fatal precondition not met (jq/systemctl missing, config not found)
set -eu

INSTALL_DIR="${INSTALL_DIR:-/opt/flag}"
CONFIG_FILE="${CONFIG_FILE:-$INSTALL_DIR/config.json}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
SETUP_SH="$INSTALL_DIR/setup.sh"
PLAY_PY="$INSTALL_DIR/sonos_play.py"

FAIL=0
WARN=0
pass() { echo "  ✅ $1"; }
fail() { echo "  ❌ $1"; FAIL=$((FAIL + 1)); }
warn() { echo "  ⚠️  $1"; WARN=$((WARN + 1)); }

for cmd in jq systemctl journalctl; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "❌ FATAL: '$cmd' is required but not installed."
        exit 2
    fi
done
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "❌ FATAL: $CONFIG_FILE not found. Is flag installed?"
    exit 2
fi

echo "=== flag runtime verification ==="
echo "  Install dir: $INSTALL_DIR"
echo "  Config:      $CONFIG_FILE"
echo "  Now:         $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo ""

# ---------------------------------------------------------------------------
# Discover sunset schedule names from config.json
# ---------------------------------------------------------------------------
sunset_names=$(jq -r '.schedules[] | select(.time | test("^[[:space:]]*sunset"; "i")) | .name' "$CONFIG_FILE")
if [[ -z "$sunset_names" ]]; then
    warn "No sunset schedules in config.json — sunset-specific checks skipped"
fi

# ---------------------------------------------------------------------------
# 1. Sunset timer files are STATIC (OnCalendar=*-*-* 03:00:00)
# ---------------------------------------------------------------------------
echo "== 1. Static sunset timer files =="
while IFS= read -r name; do
    [[ -z "$name" ]] && continue
    timer="$SYSTEMD_DIR/flag-${name}.timer"
    if [[ ! -f "$timer" ]]; then
        fail "Timer file missing: $timer"
        continue
    fi
    if grep -qE '^OnCalendar=\*-\*-\* 03:00:00[[:space:]]*$' "$timer"; then
        pass "flag-${name}.timer: OnCalendar=*-*-* 03:00:00 (static)"
    else
        actual=$(grep -E '^OnCalendar=' "$timer" | head -1)
        fail "flag-${name}.timer is NOT static (got: $actual)"
    fi
done <<< "$sunset_names"

# ---------------------------------------------------------------------------
# 2. Sunset service files use Type=simple + --sleep-until-schedule
# ---------------------------------------------------------------------------
echo ""
echo "== 2. Sleep-until-schedule sunset services =="
while IFS= read -r name; do
    [[ -z "$name" ]] && continue
    svc="$SYSTEMD_DIR/flag-${name}.service"
    if [[ ! -f "$svc" ]]; then
        fail "Service file missing: $svc"
        continue
    fi
    if grep -qE '^Type=simple[[:space:]]*$' "$svc"; then
        pass "flag-${name}.service: Type=simple"
    else
        fail "flag-${name}.service is NOT Type=simple"
    fi
    if grep -qE -- "--sleep-until-schedule[[:space:]]+${name}([[:space:]]|$)" "$svc"; then
        pass "flag-${name}.service: --sleep-until-schedule ${name}"
    else
        fail "flag-${name}.service does NOT use --sleep-until-schedule ${name}"
    fi
done <<< "$sunset_names"

# ---------------------------------------------------------------------------
# 3. Play guard present in sonos_play.py
# ---------------------------------------------------------------------------
echo ""
echo "== 3. Play guard =="
if [[ ! -f "$PLAY_PY" ]]; then
    fail "$PLAY_PY not found"
elif grep -qE '^def check_play_guard\(' "$PLAY_PY"; then
    pass "check_play_guard() defined in sonos_play.py"
else
    fail "check_play_guard() NOT defined in sonos_play.py — play guard missing"
fi

# ---------------------------------------------------------------------------
# 4. No cron backend (setup.sh leakage + /etc/cron.d/flag)
# ---------------------------------------------------------------------------
echo ""
echo "== 4. Cron backend absent =="
if [[ -f "$SETUP_SH" ]]; then
    leak=0
    for pat in "Switch scheduling backend" "switch_scheduling_backend" "_backend_activate_cron"; do
        if grep -qF -- "$pat" "$SETUP_SH"; then
            fail "Forbidden cron-backend reference in setup.sh: '$pat'"
            leak=$((leak + 1))
        fi
    done
    [[ $leak -eq 0 ]] && pass "No cron-backend identifiers in setup.sh"
else
    warn "$SETUP_SH not found — skipped setup.sh leakage check"
fi
if [[ -f /etc/cron.d/flag ]]; then
    fail "/etc/cron.d/flag exists. Remove with: sudo rm /etc/cron.d/flag"
else
    pass "/etc/cron.d/flag absent"
fi

# ---------------------------------------------------------------------------
# 5. flag-reschedule.timer is enabled and has fired (or will soon)
# ---------------------------------------------------------------------------
echo ""
echo "== 5. flag-reschedule.timer status =="
state=$(systemctl is-enabled flag-reschedule.timer 2>/dev/null || true)
if [[ "$state" == "enabled" ]]; then
    pass "flag-reschedule.timer is enabled"
else
    fail "flag-reschedule.timer is not enabled (is-enabled='$state')"
fi
last=$(systemctl show flag-reschedule.timer -p LastTriggerUSec | cut -d= -f2- | tr -d '\r')
next=$(systemctl show flag-reschedule.timer -p NextElapseUSecRealtime | cut -d= -f2- | tr -d '\r')
if [[ -n "$last" && "$last" != "n/a" && "$last" != "0" ]]; then
    pass "flag-reschedule.timer last fired: $last"
else
    warn "flag-reschedule.timer has no LastTriggerUSec yet (likely first day)"
fi
if [[ -n "$next" && "$next" != "n/a" && "$next" != "0" ]]; then
    pass "flag-reschedule.timer next fires: $next"
else
    fail "flag-reschedule.timer has no NextElapseUSecRealtime — timer not armed"
fi

# ---------------------------------------------------------------------------
# 6. No sunset service Started in today's 01:55–02:05 window (misfire window)
# ---------------------------------------------------------------------------
echo ""
echo "== 6. Misfire-window check (01:55–02:05 today) =="
now_min=$(date '+%H%M')
if [[ "$now_min" -lt "0205" ]]; then
    warn "It is currently $(date '+%H:%M'); misfire window not yet passed today — re-run after 02:05"
else
    misfire=0
    while IFS= read -r name; do
        [[ -z "$name" ]] && continue
        svc="flag-${name}.service"
        n=$(journalctl -u "$svc" --since "01:55" --until "02:05" --no-pager 2>/dev/null \
            | grep -c "Started " || true)
        if [[ "$n" -gt 0 ]]; then
            fail "$svc fired $n time(s) between 01:55 and 02:05 today — misfire detected"
            misfire=$((misfire + 1))
        fi
    done <<< "$sunset_names"
    if [[ $misfire -eq 0 && -n "$sunset_names" ]]; then
        pass "No sunset service Started in 01:55–02:05 today"
    fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=== Summary ==="
if [[ $FAIL -eq 0 ]]; then
    if [[ $WARN -gt 0 ]]; then
        echo "✅ All checks passed ($WARN warning(s) — informational only)"
    else
        echo "✅ All checks passed"
    fi
    exit 0
else
    echo "❌ $FAIL check(s) failed, $WARN warning(s)"
    exit 1
fi
