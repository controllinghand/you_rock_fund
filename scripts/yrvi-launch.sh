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

# ── Single-instance guard ─────────────────────────────────────
# A double-click — or an impatient re-click while nothing yet looks like it's
# happening — would otherwise spawn a second launcher that races the first:
# interleaved logs, setup_docker.sh lock contention, and a scary transient
# NO-GO. Take an atomic mkdir lock. If another launch is already in flight, just
# bring its splash forward and exit instead of starting a duplicate run.
SPLASH="$PROJ/assets/startup-splash.html"
LOCKDIR="${TMPDIR:-/tmp}/yrvi-launch.lock"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
    # Lost the race for the lock. Give the winner a beat to record its PID
    # (it writes the pid file immediately after mkdir), then decide.
    sleep 1
    OTHER_PID=$(cat "$LOCKDIR/pid" 2>/dev/null || true)
    if [ -n "$OTHER_PID" ] && kill -0 "$OTHER_PID" 2>/dev/null; then
        echo "Another launch (pid $OTHER_PID) is already in progress — not starting a duplicate."
        # Do NOT open a browser tab here. The winning launch already opened the
        # splash; a duplicate open would just pile on extra tabs (a triple-click
        # would spawn three). A quiet notification is enough.
        notify "Already starting" "YRVI is already starting up…"
        exit 0
    fi
    # Lock is stale (previous launcher died without cleanup) — reclaim it.
    echo "Reclaiming stale launch lock (owner pid ${OTHER_PID:-unknown} not running)."
    rm -rf "$LOCKDIR"
    mkdir "$LOCKDIR" 2>/dev/null || true
fi
echo "$$" > "$LOCKDIR/pid"
trap 'rm -rf "$LOCKDIR"' EXIT

notify "Starting up" "Launching the trading system… (about a minute)"

# ── Open the progress splash in the browser RIGHT NOW ─────────
# Headless launch = no terminal, and Notification Center banners auto-dismiss,
# so without this the operator stares at nothing for 1–2 min. The splash shows a
# live spinner + checklist + elapsed timer and, once the API answers, redirects
# itself to the dashboard (:3000). It owns that redirect, so we do NOT open the
# dashboard again at the end — that avoids a duplicate tab.
#
# NOTE: this opens exactly ONE URL. If the operator's browser was FULLY QUIT,
# macOS/browser window-restoration reopens the browser's previous windows (an
# old localhost:3000 tab, or a New Tab page) ALONGSIDE our splash — so a
# cold-quit browser can briefly show two windows. That's browser/OS session
# restore, not this script double-opening, and it happens whenever any app
# opens a URL in a not-running browser. Leaving the browser open (the normal
# operator case) gives a single clean tab. To minimize it on a given box, set
# the browser's "On startup → Open the New Tab page" and macOS "Close windows
# when quitting an application".
SPLASH_SHOWN=false
if [ -f "$SPLASH" ]; then
    open "file://$SPLASH" >/dev/null 2>&1 && SPLASH_SHOWN=true
fi

# ── 1. Ensure the Docker engine is running ────────────────────
if ! docker info >/dev/null 2>&1; then
    echo "Docker engine not running — launching it…"
    # Open by FULL PATH, not by name. `open -a "Docker"` resolves the app via
    # LaunchServices, which can point at a stale registration — e.g. a leftover
    # /Volumes/Docker/Docker.app from a past installer DMG mount — so it silently
    # fails to launch the real /Applications/Docker.app and the daemon never
    # comes up. The full path is unambiguous and matches the -d check above.
    if [ -d "/Applications/Docker.app" ]; then
        DOCKER_APP="/Applications/Docker.app"
    elif [ -d "/Applications/Rancher Desktop.app" ]; then
        DOCKER_APP="/Applications/Rancher Desktop.app"
    else
        fail "Docker Desktop is not installed. Install it from docker.com, then try again."
    fi
    open -a "$DOCKER_APP"
    notify "Starting up" "Waiting for Docker to come online…"
    # Wait up to ~120s for the daemon to accept commands, RE-ISSUING the open
    # every ~12s while it's still down. A single open can silently no-op if it
    # lands while Docker Desktop is still mid-shutdown (the operator quit it and
    # immediately launched YRVI) — macOS sees the app as "running" and skips the
    # relaunch, so without the retry the launcher just waits out the full timeout
    # and fails. Re-opening an already-starting app is a harmless focus no-op.
    for i in $(seq 1 60); do
        docker info >/dev/null 2>&1 && break
        [ $((i % 6)) -eq 0 ] && open -a "$DOCKER_APP" >/dev/null 2>&1 || true
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
    # The splash page redirects itself to the dashboard once the API answers, so
    # we don't open :3000 here (that would spawn a second tab). If the splash
    # never showed (missing file, or a headless box with no default browser),
    # fall back to opening the dashboard directly.
    if [ "$SPLASH_SHOWN" != true ]; then
        open "http://localhost:3000" >/dev/null 2>&1 || true
    fi
    notify "Ready" "Dashboard is ready at http://localhost:3000"
    echo "===== ready ====="
else
    fail "The dashboard did not come up within ~2 minutes. Open Docker Desktop to check the containers, then start YRVI again."
fi
