#!/usr/bin/env bash
# tests/test_menu_render.sh — Smoke test for setup.sh menu structure.
#
# Static checks that catch the regression class behind PR #67:
#   - The main menu has exactly 11 numbered options, in order 1..11.
#   - The MENU_* constants are sequential 1..11 and dispatched in the case loop.
#   - No cron-backend identifiers leaked back into setup.sh.
#   - setup.sh parses with `bash -n`.
#
# Pure bash + grep/awk/seq. Does not invoke setup.sh or require systemd.
# Runnable on any system with bash (including git-bash on Windows).
#
# Usage:
#   bash tests/test_menu_render.sh
#
# Exits 0 on success, non-zero with a list of failed assertions otherwise.
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP_SH="$SCRIPT_DIR/../setup.sh"

if [[ ! -f "$SETUP_SH" ]]; then
    echo "❌ setup.sh not found at $SETUP_SH"
    exit 2
fi

FAIL=0
pass() { echo "  ✅ $1"; }
fail() { echo "  ❌ $1"; FAIL=$((FAIL + 1)); }

echo "Verifying setup.sh menu structure..."

# Extract the prompt_menu function body so other numbered prompts (sub-menus,
# speaker picker) don't pollute the count.
menu_body=$(awk '/^function prompt_menu\(\) \{$/,/^\}$/' "$SETUP_SH")
if [[ -z "$menu_body" ]]; then
    fail "Could not extract prompt_menu function from setup.sh"
    echo "FATAL: bailing early."
    exit 1
fi

# 1. Exactly 11 numbered options inside prompt_menu
option_count=$(printf '%s\n' "$menu_body" \
    | grep -cE '^[[:space:]]+echo[[:space:]]+"[[:space:]]+[0-9]+\)' || true)
if [[ "$option_count" == "11" ]]; then
    pass "Menu has 11 numbered options"
else
    fail "Menu has $option_count numbered options (expected 11)"
fi

# 2. Numbers are 1..11 in order
nums=$(printf '%s\n' "$menu_body" \
    | grep -oE '"[[:space:]]+[0-9]+\)' \
    | grep -oE '[0-9]+')
expected_nums=$(seq 1 11)
if [[ "$nums" == "$expected_nums" ]]; then
    pass "Options numbered 1..11 in order"
else
    fail "Numbering wrong: got '$(echo "$nums" | tr '\n' ' ')', expected 1..11"
fi

# 3. Final read prompt advertises [1-11]
if grep -qE 'Enter your choice \[1-11\]' "$SETUP_SH"; then
    pass "Final read prompt is [1-11]"
else
    fail "Final read prompt does not say [1-11]"
fi

# 4. MENU_* constants: sequential 1..11, no MENU_BACKEND
constants=$(grep -E '^readonly MENU_[A-Z]+=[0-9]+$' "$SETUP_SH")
const_count=$(printf '%s\n' "$constants" | wc -l | tr -d ' ')
if [[ "$const_count" == "11" ]]; then
    pass "11 MENU_* constants defined"
else
    fail "$const_count MENU_* constants defined (expected 11)"
fi

const_values=$(printf '%s\n' "$constants" | grep -oE '=[0-9]+$' | tr -d '=' | sort -n)
expected_values=$(seq 1 11)
if [[ "$const_values" == "$expected_values" ]]; then
    pass "MENU_* values are 1..11"
else
    fail "MENU_* values not 1..11: got '$(echo "$const_values" | tr '\n' ' ')'"
fi

if grep -qE '^readonly MENU_BACKEND=' "$SETUP_SH"; then
    fail "MENU_BACKEND constant must not be present"
else
    pass "MENU_BACKEND constant absent"
fi

# 5. Every MENU_* constant (except EXIT) has a dispatch case
for const in $(printf '%s\n' "$constants" | awk '{print $2}' | cut -d= -f1); do
    if [[ "$const" == "MENU_EXIT" ]]; then
        # MENU_EXIT is intentionally handled by the wildcard `*)` case.
        continue
    fi
    if grep -qE "\"\\\$$const\"\\)" "$SETUP_SH"; then
        pass "$const has dispatch case"
    else
        fail "$const has no dispatch case"
    fi
done

# 6. No cron-backend leakage
forbidden=(
    "Switch scheduling backend"
    "switch_scheduling_backend"
    "_detect_scheduling_backend"
    "_backend_activate_systemd"
    "_backend_activate_cron"
    "_SCHEDULING_BACKEND"
    "/etc/cron.d/flag"
)
for pat in "${forbidden[@]}"; do
    if grep -qF -- "$pat" "$SETUP_SH"; then
        fail "Forbidden cron-backend reference present: '$pat'"
    else
        pass "Absent: '$pat'"
    fi
done

# 7. setup.sh passes bash -n
if bash -n "$SETUP_SH" 2>/dev/null; then
    pass "setup.sh syntactically valid (bash -n)"
else
    fail "bash -n setup.sh failed"
fi

echo ""
if [[ $FAIL -eq 0 ]]; then
    echo "✅ All menu smoke tests passed."
    exit 0
else
    echo "❌ $FAIL assertion(s) failed."
    exit 1
fi
