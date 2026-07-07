#!/bin/bash

LOCKFILE="$HOME/.yrvi_setup.lock"
if [ -f "$LOCKFILE" ]; then
    LOCK_AGE=$(( $(date +%s) - $(date -r "$LOCKFILE" +%s) ))
    if [ "$LOCK_AGE" -lt 600 ]; then
        echo "$(date): Another instance is running (lock age: ${LOCK_AGE}s) — exiting"
        exit 0
    else
        echo "$(date): Stale lock found (${LOCK_AGE}s old) — removing and proceeding"
        rm -f "$LOCKFILE"
    fi
fi
touch "$LOCKFILE"
trap 'rm -f "$LOCKFILE"' EXIT

# ─────────────────────────────────────────────────────────────
#  Docker Setup — You Rock Volatility Income Fund
#  Containerized branch: Docker replaces launchd/IBC
#
#  Usage:
#    bash setup_docker.sh --paper   # paper trading (IBKR paper account)
#    bash setup_docker.sh --live    # live trading  (IBKR live account)
#
#  What it does:
#    1. Checks Docker is running (install Docker Desktop first)
#    2. Configures secrets via the secrets container (browser or CLI)
#    3. Validates .env.compose and config (docker/preflight.sh)
#    4. Builds and starts all 5 containers (ib_gateway, api, scheduler, web, secrets)
#    5. Installs com.yourockfund.docker launchd service so containers
#       start automatically on every login / reboot
#    6. Installs YRVI Startup.app in /Applications
# ─────────────────────────────────────────────────────────────

set -euo pipefail

# ── Flag parsing ───────────────────────────────────────────────

usage() {
    echo ""
    echo "  Usage: setup_docker.sh --paper|--live"
    echo ""
    echo "    --paper          use paper trading secrets (IBKR paper account)"
    echo "    --live           use live trading secrets  (IBKR live account)"
    echo ""
}

LAST_MODE_FILE="$HOME/.yrvi_last_mode"

TRADING_MODE=""
for arg in "$@"; do
    case "$arg" in
        --paper)         TRADING_MODE="paper" ;;
        --live)          TRADING_MODE="live"  ;;
        --help|-h) usage; exit 0 ;;
        *) printf "Unknown flag: %s\n" "$arg" >&2; usage; exit 1 ;;
    esac
done

if [ -z "$TRADING_MODE" ]; then
    if [ -f "$LAST_MODE_FILE" ]; then
        TRADING_MODE=$(cat "$LAST_MODE_FILE")
        printf "  No mode flag given — resuming last mode: %s\n" "$TRADING_MODE"
    else
        usage
        exit 1
    fi
fi

if [ "$TRADING_MODE" != "paper" ] && [ "$TRADING_MODE" != "live" ]; then
    printf "Invalid mode in %s: '%s' — run with --paper or --live to reset\n" "$LAST_MODE_FILE" "$TRADING_MODE" >&2
    exit 1
fi

echo "$TRADING_MODE" > "$LAST_MODE_FILE"

# ── Globals ────────────────────────────────────────────────────

PROJ=$(cd "$(dirname "$0")" && pwd)

# Keep .env.compose in sync with the chosen trading mode
IBKR_PORT_VAL=4004
[ "$TRADING_MODE" = "live" ] && IBKR_PORT_VAL=4003
if [ -f "$PROJ/.env.compose" ]; then
    sed -i.bak "s/^TRADING_MODE=.*/TRADING_MODE=$TRADING_MODE/" "$PROJ/.env.compose"
    sed -i.bak "s/^IBKR_PORT=.*/IBKR_PORT=$IBKR_PORT_VAL/" "$PROJ/.env.compose"
    rm -f "$PROJ/.env.compose.bak"
    printf "  ✅  .env.compose updated: TRADING_MODE=%s  IBKR_PORT=%s\n" "$TRADING_MODE" "$IBKR_PORT_VAL"
fi

# ── Platform detection ─────────────────────────────────────────
OS=$(uname -s)
IS_WINDOWS=false
IS_WSL=false
case "$OS" in MINGW*|MSYS*|CYGWIN*) IS_WINDOWS=true ;; esac
if [ "$OS" = "Linux" ] && grep -qi microsoft /proc/version 2>/dev/null; then IS_WSL=true; fi

DOCKER_PLIST_SRC="$PROJ/com.yourockfund.docker.plist"
DOCKER_PLIST_DEST="$HOME/Library/LaunchAgents/com.yourockfund.docker.plist"
DOCKER_LABEL="com.yourockfund.docker"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

ok()   { printf "  ${GREEN}✅${NC}  %s\n" "$1"; }
fail() { printf "  ${RED}❌${NC}  %s\n" "$1" >&2; exit 1; }
warn() { printf "  ${YELLOW}⚠️${NC}   %s\n" "$1"; }
info() { printf "  ${BLUE}ℹ️${NC}   %s\n" "$1"; }

MODE_LABEL="paper"
[ "$TRADING_MODE" = "live" ] && MODE_LABEL="LIVE"

echo ""
printf "${BOLD}${BLUE}╔══════════════════════════════════════════════════╗\n"
printf "║    Docker Setup — YRVI Fund  (%-17s)  ║\n" "$MODE_LABEL"
printf "╚══════════════════════════════════════════════════╝${NC}\n"
echo ""

# ── Step 1: Check Docker is running ──────────────────────────
printf "${BOLD}Step 1 / 6   Check Docker${NC}\n"
echo "──────────────────────────────────────────────────────"

if ! command -v docker &>/dev/null; then
    fail "docker not found — install Docker Desktop from https://www.docker.com/products/docker-desktop and retry"
fi

if ! docker info &>/dev/null 2>&1; then
    fail "Docker daemon not running — start Docker Desktop and retry"
fi

DOCKER_VER=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "unknown")
ok "Docker running  (server $DOCKER_VER)"

echo ""
warn "Make sure Docker Desktop is set to auto-start:"
echo "       Docker Desktop → Settings → General:"
echo "         ✅  Start Docker Desktop when you sign in"
echo "       This ensures Docker is running before YRVI"
echo "       containers restart after a reboot."
echo ""

# ── Step 2: Configure secrets ────────────────────────────────
echo ""
printf "${BOLD}Step 2 / 6   Configure secrets${NC}\n"
echo "──────────────────────────────────────────────────────"

cd "$PROJ"
mkdir -p docker/secrets

# Empty placeholders for the file-based secrets: block in
# docker-compose.yml — ib_gateway still reads tws_password_paper
# from /run/secrets/. Other services use the secrets container
# HTTP path; their files stay empty.
for _placeholder in \
    docker/secrets/tws_password_paper \
    docker/secrets/tws_password_live \
    docker/secrets/render_secret \
    docker/secrets/anthropic_api_key \
    docker/secrets/discord_webhook_url \
    docker/secrets/discord_webhook_weekly_plan \
    docker/secrets/ibkr_password_live; do
    [ -f "$_placeholder" ] || touch "$_placeholder"
done
unset _placeholder

SECRETS_URL="http://localhost:8001"

info "Starting secrets container..."
docker compose --env-file .env.compose up -d --build secrets >/dev/null 2>&1 \
    || fail "Failed to start secrets container — check: docker compose --env-file .env.compose logs secrets"

WAIT=0
until curl -sf "$SECRETS_URL/health" >/dev/null 2>&1; do
    if [ "$WAIT" -ge 30 ]; then
        fail "Secrets container did not become healthy within 30s — check: docker compose --env-file .env.compose logs secrets"
    fi
    sleep 3
    WAIT=$((WAIT + 3))
done
ok "Secrets container running"

secrets_complete() {
    curl -sf "$SECRETS_URL/secrets/status" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print('true' if d.get('complete') else 'false')" 2>/dev/null \
        || echo "false"
}

if [ "$(secrets_complete)" = "true" ]; then
    ok "Secrets already configured"
else
    info "Opening $SECRETS_URL in your browser..."
    case "$OS" in
        Darwin)               open "$SECRETS_URL" 2>/dev/null || true ;;
        MINGW*|MSYS*|CYGWIN*) cmd.exe /c start "" "$SECRETS_URL" 2>/dev/null || true ;;
        Linux)
            if $IS_WSL; then
                cmd.exe /c start "" "$SECRETS_URL" 2>/dev/null || xdg-open "$SECRETS_URL" 2>/dev/null || true
            else
                xdg-open "$SECRETS_URL" 2>/dev/null || true
            fi ;;
        *)                    info "Open $SECRETS_URL in a browser to enter secrets" ;;
    esac

    printf "  Enter your credentials at ${BLUE}%s${NC} — setup will continue automatically when done.\n" "$SECRETS_URL"
    echo ""
    DOTS=0
    while [ "$(secrets_complete)" != "true" ]; do
        DOTS=$(( (DOTS % 3) + 1 ))
        printf "\r  Waiting for secrets%-3s" "$(printf '%0.s.' $(seq 1 $DOTS))"
        sleep 3
    done
    printf "\r%-40s\r" ""
    ok "Secrets configured"
fi

# ── Step 3: Validate .env.compose and config ─────────────────
echo ""
printf "${BOLD}Step 3 / 6   Validate .env.compose and config${NC}\n"
echo "──────────────────────────────────────────────────────"

if [ ! -f ".env.compose" ]; then
    fail ".env.compose not found — copy .env.compose.example to .env.compose and fill in credentials"
fi

# Account credentials are now managed by the secrets container — preflight
# verifies the required secrets are populated there.
if [ "$TRADING_MODE" = "live" ]; then
    LIVE_ACCT=$(curl -sf "$SECRETS_URL/secret/account_live" 2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('value',''))" 2>/dev/null || echo "")
    LIVE_USER=$(curl -sf "$SECRETS_URL/secret/tws_userid_live" 2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('value',''))" 2>/dev/null || echo "")
    if [ -z "$LIVE_ACCT" ] || [ -z "$LIVE_USER" ]; then
        fail "Live mode requires account_live and tws_userid_live in the secrets UI ($SECRETS_URL)"
    fi
fi

if [ "$TRADING_MODE" = "paper" ]; then
    sh docker/preflight.sh || fail "Preflight check failed — fix the issues above and retry"
fi
ok "Config validated"

# ── Step 4: Build and start containers ───────────────────────
echo ""
printf "${BOLD}Step 4 / 6   Build and start all 5 containers${NC}\n"
echo "──────────────────────────────────────────────────────"

info "Building images and starting api, scheduler, ib_gateway, web (secrets already running)..."

if docker compose --env-file .env.compose up -d --build; then

    sleep 3
    RUNNING=$(docker compose --env-file .env.compose ps 2>/dev/null \
        | grep -cE "Up|running|healthy" || true)
    if [ "$RUNNING" -ge 5 ]; then
        ok "All $RUNNING containers running"
    elif [ "$RUNNING" -gt 0 ]; then
        warn "$RUNNING / 5 containers running — IB Gateway may still be initializing (allow 60 s)"
        info "Monitor: docker compose --env-file .env.compose logs -f ib_gateway"
    else
        warn "Containers started but status unclear — check:"
        info "  docker compose --env-file .env.compose ps"
    fi

    # ── dry_run check ──────────────────────────────────────────
    info "Checking dry_run setting..."
    sleep 5
    DRY_RUN=$(curl -sf http://127.0.0.1:8000/api/settings 2>/dev/null \
        | python3 -c \
          "import sys,json; print(json.load(sys.stdin).get('dry_run','?'))" \
          2>/dev/null \
        || echo "?")
    if [ "$DRY_RUN" = "False" ] || [ "$DRY_RUN" = "false" ]; then
        ok "dry_run=false — paper trading handles safety; orders go to your paper account"
    elif [ "$DRY_RUN" = "True" ] || [ "$DRY_RUN" = "true" ]; then
        info "dry_run=true — orders are simulated (no fills). Toggle off in Settings when ready to trade"
    else
        info "dry_run not checked yet (API still starting — verify at http://localhost:8000/api/settings)"
    fi

else
    echo ""
    printf "  ${RED}❌${NC}  docker compose up failed.\n"
    info "  docker compose --env-file .env.compose ps"
    info "  docker compose --env-file .env.compose logs"
    exit 1
fi

# ── Step 5: Install Docker auto-start on login ────────────────
echo ""
printf "${BOLD}Step 5 / 6   Install Docker auto-start on login${NC}\n"
echo "──────────────────────────────────────────────────────"

if $IS_WINDOWS; then
    TASK_NAME="YRVI_Docker_AutoStart"
    PROJ_WIN=$(cygpath -w "$PROJ")
    LOGFILE="$(cygpath -w "$HOME")\\yrvi-autostart.log"
    BATCH="$PROJ/yrvi-autostart.bat"

    printf '@echo off\r\ncd /d "%s"\r\ndocker compose --env-file .env.compose up -d >> "%s" 2>&1\r\n' \
        "$PROJ_WIN" "$LOGFILE" > "$BATCH"
    BATCH_WIN=$(cygpath -w "$BATCH")

    if schtasks.exe /create /tn "$TASK_NAME" /tr "\"$BATCH_WIN\"" /sc ONLOGON /f 2>/dev/null; then
        ok "Task Scheduler job '$TASK_NAME' registered — containers auto-start on every login"
        info "  Reboot log: $LOGFILE"
    else
        warn "Could not register Task Scheduler job — rerun Git Bash as Administrator to enable auto-start"
        info "  Batch file: $BATCH_WIN"
    fi
elif $IS_WSL; then
    TASK_NAME="YRVI_Docker_AutoStart"
    WIN_HOME_WIN=$(cmd.exe /c "echo %USERPROFILE%" 2>/dev/null | tr -d '\r')
    WIN_HOME=$(wslpath "$WIN_HOME_WIN" 2>/dev/null || echo "$HOME")
    BATCH="$WIN_HOME/yrvi-autostart.bat"
    BATCH_WIN=$(wslpath -w "$BATCH" 2>/dev/null || echo "$BATCH")
    LOGFILE="$HOME/yrvi-autostart.log"

    printf '@echo off\r\nwsl.exe -- bash -c "cd %s && docker compose --env-file .env.compose up -d >> %s 2>&1"\r\n' \
        "$PROJ" "$LOGFILE" > "$BATCH"

    if schtasks.exe /create /tn "$TASK_NAME" /tr "\"$BATCH_WIN\"" /sc ONLOGON /f 2>/dev/null; then
        ok "Task Scheduler job '$TASK_NAME' registered — containers auto-start on every login"
        info "  Reboot log: $LOGFILE"
    else
        warn "Could not register Task Scheduler job — try running from a terminal with admin rights"
        info "  Batch file: $BATCH_WIN"
    fi
else
    mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

    sed -e "s|__PROJ__|$PROJ|g" -e "s|__HOME__|$HOME|g" "$DOCKER_PLIST_SRC" > "$DOCKER_PLIST_DEST"

    if [ -t 0 ]; then
        launchctl bootstrap "gui/$(id -u)" "$DOCKER_PLIST_DEST" 2>/dev/null || \
            launchctl load "$DOCKER_PLIST_DEST" 2>/dev/null || true
        ok "com.yourockfund.docker installed — containers will auto-start on every login"
    else
        ok "com.yourockfund.docker already active (launched by launchd — skipping re-register)"
    fi
    info "  Reboot log: cat ~/Library/Logs/yrvi-autostart.log"
fi

# ── Step 6: Install Desktop app (macOS only) ─────────────────
echo ""
printf "${BOLD}Step 6 / 6   Install Desktop app${NC}\n"
echo "──────────────────────────────────────────────────────"

if [ "$OS" = "Darwin" ]; then
    APP_DEST="/Applications/YRVI Startup.app"
    NEW_INSTALL=false
    [ -e "$APP_DEST" ] || NEW_INSTALL=true

    # Install/update the app UNCONDITIONALLY (idempotent) so it self-heals to the
    # current headless launcher on every login — the LaunchAgent runs this script
    # at login, so an upgrade that pulled a newer app definition takes effect on
    # the next login with no Terminal and no manual step. (The OLD behavior skipped
    # when the app already existed, which is why boxes were stuck on the legacy
    # Terminal-opening app.)
    if YRVI_PROJ="$PROJ" bash "$PROJ/scripts/install-startup-app.sh" >/dev/null 2>&1; then
        ok "YRVI Startup app up to date (headless)"
    else
        warn "YRVI Startup app refresh skipped (non-fatal)"
    fi

    # Add to the Dock only on first install — re-adding every login would pile up
    # duplicate Dock tiles.
    if $NEW_INSTALL; then
        defaults write com.apple.dock persistent-apps -array-add \
            "<dict><key>tile-data</key><dict><key>file-data</key><dict>\
<key>_CFURLString</key><string>/Applications/YRVI Startup.app</string>\
<key>_CFURLStringType</key><integer>0</integer></dict></dict></dict>"
        killall Dock 2>/dev/null || true
    fi

    # Register yrvi:// URL scheme (idempotent — skips if already registered)
    bash "$PROJ/scripts/yrvi-register-url-scheme.sh"
    ok "yrvi:// upgrade URL scheme registered"
else
    info "Desktop app is macOS only"
    info "  Access dashboard at http://localhost:3000"
fi

# ── Summary ───────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════"
printf "${BOLD}${GREEN}  Setup complete.${NC}\n"
echo "══════════════════════════════════════════════════════"
echo ""
echo "  Dashboard:  http://localhost:3000"
echo "  API status: http://localhost:8000/api/status"
echo ""
echo "  Wait for IB Gateway to log in (watch for 'Login has completed'):"
echo "    docker compose --env-file .env.compose logs -f ib_gateway"
echo ""
echo "  VNC (if IBKR shows a dialog at first login):"
echo "    1. Set the VNC password at http://localhost:8001 (or use default)"
echo "    2. docker compose --env-file .env.compose up -d --force-recreate ib_gateway"
echo "    3. Connect a VNC client to localhost:5900"
if $IS_WINDOWS || $IS_WSL; then
echo "    Windows: install RealVNC Viewer (free) → https://www.realvnc.com/en/connect/download/viewer/"
else
echo "    macOS: open vnc://localhost:5900 in Finder → Go → Connect to Server"
fi
echo ""
echo "  Pre-flight check anytime:"
echo "    bash startup.sh"
echo ""
echo "  Re-run setup (skips already-configured secrets):"
echo "    bash setup_docker.sh --$TRADING_MODE"
echo ""
