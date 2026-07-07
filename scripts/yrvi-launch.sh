#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  YRVI Headless Launcher
#
#  Brings the whole stack up with NO terminal window. Invoked by the
#  "Start YRVI" desktop app (scripts/make-desktop-app.sh), or run directly
#  for testing:  bash scripts/yrvi-launch.sh
#
#  What it does, in order:
#    1. Make sure the Docker engine is running (launch Docker Desktop /
#       Rancher Desktop and wait for it if it isn't).
#    2. Bring the containers up via the proven startup.sh (which also opens
#       the dashboard in the browser).
#    3. Confirm the API answered; notify the user via native notifications.
#
#  All progress goes to macOS Notification Center + a log file — there is no
#  terminal, so the browser (dashboard) is where the operator sees results.
# ─────────────────────────────────────────────────────────────

# macOS launches .app scripts via `do shell script`, which uses a minimal PATH
# (/usr/bin:/bin:/usr/sbin:/sbin) that does NOT include where the `docker` CLI
# lives (Docker Desktop → /usr/local/bin, Homebrew ARM → /opt/homebrew/bin,
# Rancher → ~/.rd/bin). Without this, `docker` isn't found and the launcher
# wrongly concludes the engine is down. Prepend the common locations up front so
# every `docker` call here (and in startup.sh, which we invoke) resolves.
export PATH="/usr/local/bin:/opt/homebrew/bin:$HOME/.docker/bin:$HOME/.rd/bin:$PATH"

PROJ="${YRVI_PROJ:-$HOME/you_rock_fund}"
LOG="$HOME/Library/Logs/yrvi-launch.log"
mkdir -p "$(dirname "$LOG")" 2>/dev/null

notify() {  # notify "<subtitle>" "<message>"
    /usr/bin/osascript -e "display notification \"$2\" with title \"YRVI\" subtitle \"$1\"" >/dev/null 2>&1
}
fail() {    # fail "<message>" — show a blocking dialog so a non-technical operator sees it
    /usr/bin/osascript -e "display dialog \"YRVI could not start:

$1\" buttons {\"OK\"} default button 1 with icon stop with title \"YRVI\"" >/dev/null 2>&1
    echo "FAIL: $1"
    exit 1
}

# Everything below is logged (append) so a failed launch can be diagnosed.
exec >>"$LOG" 2>&1
echo ""
echo "===== $(date '+%Y-%m-%d %H:%M:%S')  yrvi-launch start ====="

[ -d "$PROJ" ] || fail "Project folder not found at $PROJ"

notify "Starting up" "Launching the trading system… (about a minute)"

# ── 1. Ensure the Docker engine is running ────────────────────
if ! docker info >/dev/null 2>&1; then
    echo "Docker engine not running — launching it…"
    if [ -d "/Applications/Docker.app" ]; then
        open -a "Docker"
    elif [ -d "/Applications/Rancher Desktop.app" ]; then
        open -a "Rancher Desktop"
    else
        fail "Docker Desktop is not installed. Install it from docker.com, then try again."
    fi
    notify "Starting up" "Waiting for Docker to come online…"
    # Wait up to ~120s for the daemon to accept commands.
    for _ in $(seq 1 60); do
        docker info >/dev/null 2>&1 && break
        sleep 2
    done
    docker info >/dev/null 2>&1 || \
        fail "Docker did not finish starting in time. Open Docker Desktop, wait until it says 'running', then start YRVI again."
    echo "Docker engine is up."
fi

# ── 2. Bring the stack up (startup.sh handles down→up + opens the browser) ──
cd "$PROJ" || fail "Cannot open project folder at $PROJ"
echo "Running startup.sh…"
# YRVI_NO_OPEN=1 → startup.sh does NOT open the browser; the launcher owns that
# (single open below) so we never end up with two dashboard tabs.
YRVI_NO_OPEN=1 bash "$PROJ/startup.sh" || true   # non-fatal: we verify the API ourselves below

# ── 3. Confirm the API answered ───────────────────────────────
echo "Waiting for the API…"
API_UP=false
for _ in $(seq 1 24); do
    if curl -sf --max-time 3 http://localhost:8000/api/status >/dev/null 2>&1; then
        API_UP=true
        break
    fi
    sleep 5
done

if [ "$API_UP" = true ]; then
    # startup.sh already opened the dashboard; open again is a harmless no-op
    # focus in case it didn't (e.g. it hit a NO-GO branch but the API is fine).
    open "http://localhost:3000" >/dev/null 2>&1 || true
    notify "Ready" "Dashboard is up — opening it in your browser."
    echo "===== ready ====="
else
    fail "The dashboard did not come up within ~2 minutes. Open Docker Desktop to check the containers, then start YRVI again."
fi
