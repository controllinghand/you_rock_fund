#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  IBC + IB Gateway Auto-Login — One-Time Setup
#  You Rock Volatility Income Fund — Mac Mini
#
#  Run once after a fresh install or credential change:
#    bash setup_ibc.sh
#
#  What it does:
#    0. Checks and installs prerequisites (Homebrew, Python, git, Node.js)
#    1. Loads .env credentials
#    2. Locates IB Gateway — downloads and installs it automatically if missing
#    3. Downloads IBC (Interactive Brokers Controller) if absent
#    4. Generates ~/IBC/config.ini from .env credentials
#    5. Configures ~/IBC/StartGateway.sh with correct paths
#    6. Installs and loads the launchd plist so IB Gateway
#       starts automatically on every login / reboot
#    7. Installs YRVI Startup.app into /Applications (scripts/install-startup-app.sh)
# ─────────────────────────────────────────────────────────────

set -euo pipefail

PROJ=$(cd "$(dirname "$0")" && pwd)
IBC_DIR="$HOME/IBC"
IBC_LOG_DIR="$IBC_DIR/Logs"
IBC_VERSION="3.23.0"
IBC_ZIP_URL="https://github.com/IbcAlpha/IBC/releases/download/${IBC_VERSION}/IBCMacos-${IBC_VERSION}.zip"
IBC_ZIP_URL_FALLBACK="https://github.com/IbcAlpha/IBC/releases/latest/download/IBCMacos.zip"
IBC_ZIP="/tmp/IBCMacos-${IBC_VERSION}.zip"

PLIST_SRC="$PROJ/com.yourockfund.ibgateway.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.yourockfund.ibgateway.plist"
GATEWAY_LABEL="com.yourockfund.ibgateway"
API_PLIST_SRC="$PROJ/com.yourockfund.api.plist"
API_PLIST_DEST="$HOME/Library/LaunchAgents/com.yourockfund.api.plist"
API_LABEL="com.yourockfund.api"
DASH_PLIST_SRC="$PROJ/com.yourockfund.dashboard.plist"
DASH_PLIST_DEST="$HOME/Library/LaunchAgents/com.yourockfund.dashboard.plist"
DASH_LABEL="com.yourockfund.dashboard"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

ok()   { printf "  ${GREEN}✅${NC}  %s\n" "$1"; }
fail() { printf "  ${RED}❌${NC}  %s\n" "$1"; exit 1; }
warn() { printf "  ${YELLOW}⚠️${NC}   %s\n" "$1"; }
info() { printf "  ${BLUE}ℹ️${NC}   %s\n" "$1"; }

echo ""
printf "${BOLD}${BLUE}"
echo "╔══════════════════════════════════════════════════╗"
echo "║    IBC + IB Gateway Setup — YRVI Fund            ║"
echo "╚══════════════════════════════════════════════════╝"
printf "${NC}"
echo ""

# ── Step 0: Prerequisites ─────────────────────────────────────
echo "${BOLD}Step 0 / 6   Check and install prerequisites${NC}"
echo "──────────────────────────────────────────────────────"

# Detect CPU architecture
ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ]; then
    ok "Apple Silicon (M4) detected"
else
    ok "Intel (x86_64) detected"
fi

# Ensure Homebrew is in PATH regardless of whether it's already installed
if [ -f /opt/homebrew/bin/brew ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
elif [ -f /usr/local/bin/brew ]; then
    eval "$(/usr/local/bin/brew shellenv)"
fi

# Homebrew
if ! command -v brew &>/dev/null; then
    info "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    if [ -f /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [ -f /usr/local/bin/brew ]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
    ok "Homebrew installed"
else
    ok "Homebrew already installed"
fi

# Python 3.13
if ! command -v python3 &>/dev/null || ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null; then
    info "Installing Python 3.13 via Homebrew..."
    brew install python@3.13
    ok "Python 3.13 installed"
else
    ok "Python $(python3 --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+') already installed"
fi

# git
if ! command -v git &>/dev/null; then
    info "Installing git via Homebrew..."
    brew install git
    ok "git installed"
else
    ok "git $(git --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+') already installed"
fi

# Node.js (needed for Claude Code)
if ! command -v node &>/dev/null; then
    info "Installing Node.js via Homebrew..."
    brew install node
    ok "Node.js installed"
else
    ok "Node.js $(node --version) already installed"
fi

# Python venv + requirements
VENV_DIR="$PROJ/venv"
if [ ! -f "$VENV_DIR/bin/python3" ]; then
    info "Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
    ok "venv created at $VENV_DIR"
else
    ok "venv already exists at $VENV_DIR"
fi

info "Installing Python requirements..."
"$VENV_DIR/bin/pip" install -q -r "$PROJ/requirements.txt"
ok "Python packages installed"

# React dashboard — install npm packages and build production bundle
APP_DIR="$PROJ/yrvi-app"
if [ -d "$APP_DIR" ] && [ -f "$APP_DIR/package.json" ]; then
    if [ ! -d "$APP_DIR/node_modules" ]; then
        info "Installing React app dependencies (npm install)..."
        npm --prefix "$APP_DIR" install --silent
        ok "npm packages installed"
    else
        ok "npm packages already installed"
    fi
    info "Building React dashboard for production (npm run build)..."
    npm --prefix "$APP_DIR" run build --silent
    ok "React dashboard built → yrvi-app/dist/"
else
    warn "yrvi-app/ not found — skipping dashboard build"
fi

# ── Step 1: Load .env ─────────────────────────────────────────
echo ""
echo "${BOLD}Step 1 / 6   Load credentials from .env${NC}"
echo "──────────────────────────────────────────────────────"

ENV_FILE="$PROJ/.env"
[ -f "$ENV_FILE" ] || fail ".env not found at $ENV_FILE"

# Parse .env (skip comments and blanks)
get_env() {
    grep "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'"
}

IBKR_USERNAME=$(get_env "IBKR_USERNAME")
IBKR_PASSWORD=$(get_env "IBKR_PASSWORD")
IBKR_PORT=$(get_env "IBKR_PORT")

if [ -z "$IBKR_USERNAME" ] || [ -z "$IBKR_PASSWORD" ]; then
    echo ""
    warn "IBKR_USERNAME or IBKR_PASSWORD not found in .env"
    warn "Add these two lines to your .env file:"
    echo ""
    echo "    IBKR_USERNAME=your_ibkr_login_id"
    echo "    IBKR_PASSWORD=your_ibkr_password"
    echo ""
    fail "Re-run setup_ibc.sh after updating .env"
fi

[ -z "$IBKR_PORT" ] && IBKR_PORT="4002"
TRADING_MODE="paper"
[ "$IBKR_PORT" = "4001" ] && TRADING_MODE="live"
ok "Credentials loaded  (port=$IBKR_PORT  mode=$TRADING_MODE)"

# ── Step 2: Verify IB Gateway installation ────────────────────
echo ""
echo "${BOLD}Step 2 / 6   Locate IB Gateway${NC}"
echo "──────────────────────────────────────────────────────"

# Search common install locations
GATEWAY_APP=""
GATEWAY_VERSION=""

find_gateway() {
    local hit

    # Helper: filter out hidden-dir paths (/.something) and uninstaller apps.
    # IBKR installs auxiliary .apps in .install4j/ and an "Uninstaller.app"
    # alongside the real "IB Gateway 10.37.app" — we want only the latter.
    gw_filter() {
        grep -i "gateway" | grep -v "/\." | grep -iv "uninstall" | sort -V | tail -1
    }

    # ~/Applications — handles versioned dirs like "IB Gateway 10.37/"
    hit=$(find "$HOME/Applications" -maxdepth 3 -name "*.app" 2>/dev/null | gw_filter)
    [ -n "$hit" ] && echo "$hit" && return 0

    # /Applications — system-wide install
    hit=$(find "/Applications" -maxdepth 3 -name "*.app" 2>/dev/null | gw_filter)
    [ -n "$hit" ] && echo "$hit" && return 0

    # IBKR-branded installs that don't include the word "gateway" in the app name
    hit=$(find "$HOME/Applications" -maxdepth 3 -name "*.app" 2>/dev/null \
          | grep -i "ibkr" | grep -v "/\." | grep -iv "uninstall" | sort -V | tail -1)
    [ -n "$hit" ] && echo "$hit" && return 0

    # Offline installer layout: ~/Jts/ibgateway/<version>/
    hit=$(find "$HOME/Jts/ibgateway" -maxdepth 3 -name "*.app" 2>/dev/null \
          | grep -v "/\." | sort -V | tail -1)
    [ -n "$hit" ] && echo "$hit" && return 0

    return 1
}

GATEWAY_APP=$(find_gateway 2>/dev/null) || true

if [ -z "$GATEWAY_APP" ]; then
    info "IB Gateway not found — downloading automatically..."
    if [ "$ARCH" = "arm64" ]; then
        GW_DMG_URL="https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-macos-arm64.dmg"
    else
        GW_DMG_URL="https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-macos-x64.dmg"
    fi
    GW_DMG="/tmp/ibgateway-installer.dmg"

    info "Downloading IB Gateway stable installer (~200 MB)..."
    curl -fL --progress-bar "$GW_DMG_URL" -o "$GW_DMG" \
        || fail "Download failed — check your internet connection"

    info "Mounting installer DMG..."
    MOUNT_POINT=$(hdiutil attach "$GW_DMG" -nobrowse -noautoopen 2>/dev/null \
        | tail -1 | sed 's/.*\(\/Volumes\/.*\)/\1/' | sed 's/[[:space:]]*$//')
    [ -n "$MOUNT_POINT" ] || fail "Failed to mount IB Gateway DMG"
    info "Mounted at: $MOUNT_POINT"

    # install4j apps have a binary inside Contents/MacOS — run with -q for silent install
    INSTALLER_APP=$(find "$MOUNT_POINT" -maxdepth 2 -name "*.app" 2>/dev/null \
        | grep -iv "uninstall" | sort | head -1)

    if [ -n "$INSTALLER_APP" ]; then
        INSTALLER_BIN=$(find "$INSTALLER_APP/Contents/MacOS" -maxdepth 1 \
            -type f ! -name "*.dylib" 2>/dev/null | head -1)
        [ -n "$INSTALLER_BIN" ] || fail "No executable found inside $INSTALLER_APP"
        info "Running installer silently — this may take a minute..."
        "$INSTALLER_BIN" -q 2>/dev/null || true
    else
        PKG=$(find "$MOUNT_POINT" -maxdepth 2 -name "*.pkg" 2>/dev/null | head -1)
        [ -n "$PKG" ] || fail "No installer found inside mounted DMG at $MOUNT_POINT"
        info "Running pkg installer..."
        sudo installer -pkg "$PKG" -target / || fail "pkg installer failed"
    fi

    info "Unmounting DMG..."
    hdiutil detach "$MOUNT_POINT" -quiet 2>/dev/null || true
    rm -f "$GW_DMG"

    # Re-scan after install
    GATEWAY_APP=$(find_gateway 2>/dev/null) || true
    [ -n "$GATEWAY_APP" ] || fail "Installer finished but IB Gateway app not found — try running setup_ibc.sh again"
    ok "IB Gateway installed automatically"
fi

info "  Found: $GATEWAY_APP"   # debug — confirms which path was matched
ok "IB Gateway: $(basename "$GATEWAY_APP")"

# Extract version from the parent directory name.
# Handles:
#   "IB Gateway 10.37"  → dot=10.37  num=1037
#   "IB Gateway"        → fallback   num=1028
#   "1028"              → Jts layout, convert 1028 → 10.28
GATEWAY_DIR=$(dirname "$GATEWAY_APP")
GATEWAY_PARENT=$(basename "$GATEWAY_DIR")

if echo "$GATEWAY_PARENT" | grep -qE '[0-9]+\.[0-9]+'; then
    GATEWAY_VERSION_DOT=$(echo "$GATEWAY_PARENT" | grep -oE '[0-9]+\.[0-9]+' | tail -1)
elif echo "$GATEWAY_PARENT" | grep -qE '^[0-9]{4}$'; then
    # Pure 4-digit from Jts dir (e.g. "1037") → "10.37"
    GATEWAY_VERSION_DOT=$(echo "$GATEWAY_PARENT" | sed 's/\([0-9][0-9]\)\([0-9][0-9]\)/\1.\2/')
else
    GATEWAY_VERSION_DOT="10.28"   # safe fallback
fi

# Numeric form without dot — used as the Jts config path (e.g. 1037)
GATEWAY_VERSION_NUM=$(echo "$GATEWAY_VERSION_DOT" | tr -d '.')

ok "Version: $GATEWAY_VERSION_DOT  (IBC TWS_MAJOR_VRSN=$GATEWAY_VERSION_DOT  Jts dir=$GATEWAY_VERSION_NUM)"

# Config path where IB Gateway writes its per-user settings
GATEWAY_CONF="$HOME/Jts/ibgateway/$GATEWAY_VERSION_NUM"

# ── Step 3: Install IBC ───────────────────────────────────────
echo ""
echo "${BOLD}Step 3 / 6   Install IBC $IBC_VERSION${NC}"
echo "──────────────────────────────────────────────────────"

if [ -f "$IBC_DIR/IBC.jar" ] || [ -f "$IBC_DIR/gatewaystartmacos.sh" ]; then
    ok "IBC already installed at $IBC_DIR"
else
    info "Downloading IBC $IBC_VERSION..."
    if ! curl -fsSL "$IBC_ZIP_URL" -o "$IBC_ZIP" 2>/dev/null; then
        warn "Version-specific URL failed — trying /latest redirect..."
        curl -fsSL "$IBC_ZIP_URL_FALLBACK" -o "$IBC_ZIP" || \
            fail "Both download URLs failed. Check https://github.com/IbcAlpha/IBC/releases and update IBC_VERSION in setup_ibc.sh"
    fi
    mkdir -p "$IBC_DIR"
    unzip -q "$IBC_ZIP" -d "$IBC_DIR"
    rm -f "$IBC_ZIP"
    chmod +x "$IBC_DIR"/*.sh 2>/dev/null || true
    ok "IBC $IBC_VERSION installed to $IBC_DIR"
fi
mkdir -p "$IBC_LOG_DIR"

# Always ensure all IBC scripts are executable (catches pre-existing installs)
chmod +x "$IBC_DIR"/*.sh 2>/dev/null || true
chmod +x "$IBC_DIR"/scripts/* 2>/dev/null || true
ok "IBC scripts chmod +x"

# ── Step 4: Generate config.ini ───────────────────────────────
echo ""
echo "${BOLD}Step 4 / 6   Generate ~/IBC/config.ini${NC}"
echo "──────────────────────────────────────────────────────"

CONFIG_DEST="$IBC_DIR/config.ini"

# Start from repo template, substitute placeholders
sed \
    -e "s/PLACEHOLDER_USERNAME/$IBKR_USERNAME/" \
    -e "s/PLACEHOLDER_PASSWORD/$IBKR_PASSWORD/" \
    -e "s/PLACEHOLDER_TRADING_MODE/$TRADING_MODE/" \
    -e "s/PLACEHOLDER_PORT/$IBKR_PORT/" \
    "$PROJ/ibc_config.ini" > "$CONFIG_DEST"

chmod 600 "$CONFIG_DEST"   # credentials file — owner-read only
ok "config.ini written (mode 600)"

# ── Configure gatewaystartmacos.sh ───────────────────────────
# IBC 3.23.0 ships gatewaystartmacos.sh directly (no .sample copy needed).
STARTGW="$IBC_DIR/gatewaystartmacos.sh"
[ -f "$STARTGW" ] || fail "IBC installation missing gatewaystartmacos.sh — re-run setup_ibc.sh"

# Patch key variables. Variable names changed in IBC 3.x vs older releases:
#   IBC_INI          — path to config.ini (new; replaces inline credential vars)
#   TWS_PATH         — parent of versioned install dirs, e.g. ~/Applications
#   TWS_SETTINGS_PATH — where Gateway stores per-user settings (was TWS_CONFIG_PATH)
patch_var() {
    local var="$1" val="$2" file="$3"
    if grep -qE "^${var}=" "$file"; then
        sed -i '' "s|^${var}=.*|${var}=\"${val}\"|" "$file"
    else
        echo "${var}=\"${val}\"" >> "$file"
    fi
}

patch_var "TWS_MAJOR_VRSN"    "$GATEWAY_VERSION_DOT"  "$STARTGW"
patch_var "IBC_INI"            "$IBC_DIR/config.ini"   "$STARTGW"
patch_var "TRADING_MODE"       "$TRADING_MODE"         "$STARTGW"
patch_var "IBC_PATH"           "$IBC_DIR"              "$STARTGW"
patch_var "TWS_PATH"           "$HOME/Applications"    "$STARTGW"
patch_var "TWS_SETTINGS_PATH"  "$GATEWAY_CONF"         "$STARTGW"
patch_var "LOG_PATH"           "$IBC_LOG_DIR"          "$STARTGW"
patch_var "JAVA_PATH"          ""                      "$STARTGW"

chmod +x "$STARTGW"
ok "gatewaystartmacos.sh configured"

# ── Patch jts.ini: bypass API order precautions ───────────────
# IB Gateway stores per-user GUI settings in jts.ini. The "Bypass Order
# Precautions for API Orders" checkbox must be set or Gateway will block
# automated orders with an interactive confirmation dialog the scheduler
# cannot respond to.
patch_jts_ini() {
    local jts_ini="$GATEWAY_CONF/jts.ini"
    mkdir -p "$GATEWAY_CONF"
    if [ ! -f "$jts_ini" ]; then
        # Create minimal jts.ini if Gateway hasn't written one yet
        printf "[IBGateway]\nApiOrderPrecautionsIgnored=true\n" > "$jts_ini"
        ok "jts.ini created with ApiOrderPrecautionsIgnored=true"
        return
    fi
    if grep -q "^ApiOrderPrecautionsIgnored=" "$jts_ini"; then
        sed -i '' "s/^ApiOrderPrecautionsIgnored=.*/ApiOrderPrecautionsIgnored=true/" "$jts_ini"
        ok "jts.ini: ApiOrderPrecautionsIgnored set to true (updated existing)"
    else
        # Append inside [IBGateway] section if present, else append at end
        if grep -q "^\[IBGateway\]" "$jts_ini"; then
            sed -i '' "/^\[IBGateway\]/a\\
ApiOrderPrecautionsIgnored=true" "$jts_ini"
        else
            printf "\n[IBGateway]\nApiOrderPrecautionsIgnored=true\n" >> "$jts_ini"
        fi
        ok "jts.ini: ApiOrderPrecautionsIgnored=true appended"
    fi
}
patch_jts_ini

# ── Step 5: Install and load launchd plist ────────────────────
echo ""
echo "${BOLD}Step 5 / 7   Install launchd service${NC}"
echo "──────────────────────────────────────────────────────"

# Unload existing service if present (ignore errors)
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl bootout "gui/$(id -u)/$GATEWAY_LABEL" 2>/dev/null || true

# Substitute __HOME__ placeholder so plist works for any user
sed -e "s|__HOME__|$HOME|g" "$PLIST_SRC" > "$PLIST_DEST"
launchctl bootstrap "gui/$(id -u)" "$PLIST_DEST" 2>/dev/null || \
    launchctl load "$PLIST_DEST" 2>/dev/null || true

sleep 3
GW_PID=$(launchctl list "$GATEWAY_LABEL" 2>/dev/null | grep '"PID"' | grep -o '[0-9]*' || true)
if [ -n "$GW_PID" ]; then
    ok "launchd service loaded and IB Gateway starting  (PID $GW_PID)"
else
    warn "Service registered but IB Gateway not yet running (may still be loading)"
    info "Check: launchctl list $GATEWAY_LABEL"
    info "Logs:  tail -f $IBC_LOG_DIR/ibgateway_stderr.log"
fi

# Install FastAPI backend plist
NPM_BIN="$(command -v npm 2>/dev/null)"
launchctl bootout "gui/$(id -u)/$API_LABEL" 2>/dev/null || true
sed -e "s|__PROJ__|$PROJ|g" -e "s|__HOME__|$HOME|g" \
    "$API_PLIST_SRC" > "$API_PLIST_DEST"
launchctl bootstrap "gui/$(id -u)" "$API_PLIST_DEST" 2>/dev/null || \
    launchctl load "$API_PLIST_DEST" 2>/dev/null || true
sleep 2
API_PID=$(launchctl list "$API_LABEL" 2>/dev/null | grep '"PID"' | grep -o '[0-9]*' || true)
[ -n "$API_PID" ] && ok "FastAPI backend launchd service loaded  (PID $API_PID)" || \
    warn "FastAPI backend registered — will start on next login (or run startup.sh)"

# Install React dashboard plist (requires dist/ from npm run build above)
if [ -n "$NPM_BIN" ] && [ -d "$PROJ/yrvi-app/dist" ]; then
    launchctl bootout "gui/$(id -u)/$DASH_LABEL" 2>/dev/null || true
    sed -e "s|__PROJ__|$PROJ|g" \
        -e "s|__HOME__|$HOME|g" \
        -e "s|__NPM__|$NPM_BIN|g" \
        "$DASH_PLIST_SRC" > "$DASH_PLIST_DEST"
    launchctl bootstrap "gui/$(id -u)" "$DASH_PLIST_DEST" 2>/dev/null || \
        launchctl load "$DASH_PLIST_DEST" 2>/dev/null || true
    sleep 2
    DASH_PID=$(launchctl list "$DASH_LABEL" 2>/dev/null | grep '"PID"' | grep -o '[0-9]*' || true)
    [ -n "$DASH_PID" ] && ok "React dashboard launchd service loaded  (PID $DASH_PID, port 3000)" || \
        warn "React dashboard registered — will start on next login (or run startup.sh)"
else
    warn "Skipping dashboard plist — npm not found or dist/ missing"
fi

# ── Step 6: YRVI Startup app bundle ───────────────────────────
echo ""
echo "${BOLD}Step 6 / 7   Create YRVI Startup app${NC}"
echo "──────────────────────────────────────────────────────"

# Delegate to the ONE launcher installer (also used by setup_docker.sh and, on
# every login, by scripts/yrvi-launch.sh). This script used to build the bundle
# itself from assets/app_template/, whose executable opened a Terminal and ran
# "$PROJ/yrvi_startup.command" — a file DELETED in v5.2.29 when the launcher went
# headless. The template was never updated, so every fresh install since then
# produced a launcher that failed on its first click ("No such file or directory")
# and could never self-heal: the repair lives in yrvi-launch.sh, which the broken
# bundle never called. Two install paths that disagreed; now there is one.
if YRVI_PROJ="$PROJ" bash "$PROJ/scripts/install-startup-app.sh" >/dev/null 2>&1; then
    ok "YRVI Startup.app installed in /Applications — double-click to bring the stack up"
else
    warn "YRVI Startup app install failed (non-fatal) — run: bash scripts/install-startup-app.sh"
fi

# Clean up artifacts from the legacy Desktop install so nobody double-clicks the
# dead one. The installer's app lives in /Applications; these are the old paths.
rm -rf "$HOME/Desktop/YRVI Startup.app"
rm -f  "$HOME/Desktop/YRVI Startup.command"

# ── Summary ───────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════"
printf "${BOLD}${GREEN}  Setup complete.${NC}\n"
echo "══════════════════════════════════════════════════════"
echo ""
echo "  All services now auto-start on every login:"
echo "    • IB Gateway   (IBC / launchd)"
echo "    • Scheduler    (com.yourockfund.scheduler)"
echo "    • FastAPI API  (com.yourockfund.api  → port 8000)"
echo "    • Dashboard    (com.yourockfund.dashboard → port 3000)"
echo ""
echo "  Dashboard:  http://localhost:3000"
echo ""
echo "  Monitor logs:"
echo "    tail -f $IBC_LOG_DIR/ibgateway_stderr.log"
echo "    tail -f $PROJ/api_stderr.log"
echo "    tail -f $PROJ/yrvi-app/preview_stderr.log"
echo ""
echo "  Test / restart everything:"
echo "    Double-click  YRVI Startup  in /Applications"
echo "    — or —  bash startup.sh"
echo ""
echo "  Manual service restart:"
echo "    launchctl kickstart -k gui/\$(id -u)/com.yourockfund.api"
echo "    launchctl kickstart -k gui/\$(id -u)/com.yourockfund.dashboard"
echo ""
