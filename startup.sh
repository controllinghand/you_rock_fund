#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  YRVI Trading System — Docker Startup & Pre-flight Check
#  Containerized branch: Docker manages all services.
#  Docker replaces launchd — do not use setup_ibc.sh on this branch.
#
#  Run after any reboot, or to verify the stack is healthy.
# ─────────────────────────────────────────────────────────────

PROJ=$(cd "$(dirname "$0")" && pwd)

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

PASS=0; FAIL=0; WARN=0

pass() { printf "  ${GREEN}✅${NC}  %s\n" "$1"; ((PASS++)) || true; }
fail() { printf "  ${RED}❌${NC}  %s\n" "$1"; ((FAIL++)) || true; }
warn() { printf "  ${YELLOW}⚠️${NC}   %s\n" "$1"; ((WARN++)) || true; }

section() {
    echo ""
    printf "${BOLD}${BLUE}%s${NC}\n" "$1"
    printf '%0.s─' {1..52}; echo ""
}

# ── Banner ────────────────────────────────────────────────────
echo ""
printf "${BOLD}"
echo "╔══════════════════════════════════════════════════╗"
echo "║    YOU ROCK VOLATILITY INCOME FUND               ║"
echo "║    System Startup & Pre-flight Check             ║"
echo "║    (Containerized — Docker manages services)     ║"
echo "╚══════════════════════════════════════════════════╝"
printf "${NC}"
echo "  $(date '+%A %Y-%m-%d  %H:%M:%S %Z')"

cd "$PROJ"

# .env.compose is install-time only (host ports + image tag) and is OPTIONAL:
# every value it can set has a compose default, so an operator may delete it.
# `docker compose --env-file <missing>` is a hard error, so only pass the flag
# when the file is actually there. Written as if/fi rather than
# `[ -f x ] && VAR=...` because under `set -e` that idiom exits the script when
# the file is absent. Expanded unquoted on purpose: word-splitting is what turns
# this into two argv entries (the path has no spaces).
ENV_FILE_ARGS=""
if [ -f .env.compose ]; then
    ENV_FILE_ARGS="--env-file .env.compose"
fi

# ═════════════════════════════════════════════════════════════
section "1 / 4   Docker Engine"
# ═════════════════════════════════════════════════════════════

if ! command -v docker &>/dev/null; then
    fail "docker not found — install Rancher Desktop from https://rancherdesktop.io"
elif ! docker info &>/dev/null 2>&1; then
    fail "Docker daemon not running — start Rancher Desktop"
else
    DOCKER_VER=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "unknown")
    pass "Docker running  (server $DOCKER_VER)"
fi

# ═════════════════════════════════════════════════════════════
section "2 / 4   Container Health  (docker compose ps)"
# ═════════════════════════════════════════════════════════════

if [ ! -f ".env.compose" ]; then
    fail ".env.compose not found — run setup_docker.sh first"
else
    PS_OUT=$(docker compose $ENV_FILE_ARGS ps 2>/dev/null || true)
    ALL_UP=true
    # Remember each service's first-pass verdict so the post-setup re-check only
    # reverses the counter it actually incremented. (Blanket ((FAIL--)) per
    # now-up container drove FAIL negative when a container was up/warned — not
    # failed — at first check, yielding a bogus "-3 failed / NO-GO".)
    FAILED_SVCS=" "; WARNED_SVCS=" "

    for svc in ib_gateway api scheduler web; do
        SVC_LINE=$(echo "$PS_OUT" | grep -i "$svc" | head -1 || true)
        if echo "$SVC_LINE" | grep -qiE "Up|running|healthy"; then
            STATUS=$(echo "$SVC_LINE" | grep -oiE "Up [^[:space:]].*|running|healthy" | head -1 || echo "Up")
            pass "Container $svc — $STATUS"
        elif [ -z "$SVC_LINE" ]; then
            fail "Container $svc — not found"
            FAILED_SVCS="${FAILED_SVCS}${svc} "
            ALL_UP=false
        else
            warn "Container $svc — $(echo "$SVC_LINE" | awk '{print $NF}')"
            WARNED_SVCS="${WARNED_SVCS}${svc} "
            ALL_UP=false
        fi
    done

    if [ "$ALL_UP" = "false" ]; then
        echo ""
        # Determine trading mode: prefer saved mode file, fall back to settings.json, then default paper
        _MODE_FLAG="--paper"
        if [ -f "$HOME/.yrvi_last_mode" ]; then
            _SAVED=$(cat "$HOME/.yrvi_last_mode" | tr -d '[:space:]')
            [ "$_SAVED" = "live" ] && _MODE_FLAG="--live"
        elif [ -f "$PROJ/data/settings.json" ]; then
            _SAVED=$(python3 -c "import json; d=json.load(open('$PROJ/data/settings.json')); print(d.get('trading_mode','paper'))" 2>/dev/null || echo "paper")
            [ "$_SAVED" = "live" ] && _MODE_FLAG="--live"
        fi
        printf "  ${YELLOW}ℹ️${NC}   Running setup_docker.sh to pull secrets and restart containers...\n"
        bash "$PROJ/setup_docker.sh" "$_MODE_FLAG"
        echo ""

        # Re-check containers after setup and correct the counters. Only reverse
        # the counter this service actually contributed at first check: a service
        # that was already Up stays a plain pass (no double-count); one that was
        # FAILED or WARNED flips to pass by decrementing that exact counter.
        PS_OUT=$(docker compose $ENV_FILE_ARGS ps 2>/dev/null || true)
        for svc in ib_gateway api scheduler web; do
            SVC_LINE=$(echo "$PS_OUT" | grep -i "$svc" | head -1 || true)
            if echo "$SVC_LINE" | grep -qiE "Up|running|healthy"; then
                STATUS=$(echo "$SVC_LINE" | grep -oiE "Up [^[:space:]].*|running|healthy" | head -1 || echo "Up")
                case "$FAILED_SVCS" in
                    *" $svc "*)
                        ((FAIL--)) || true; ((PASS++)) || true
                        pass "Container $svc — $STATUS  (started by setup)" ;;
                    *)
                        case "$WARNED_SVCS" in
                            *" $svc "*)
                                ((WARN--)) || true; ((PASS++)) || true
                                pass "Container $svc — $STATUS  (started by setup)" ;;
                            # already Up at first check — leave counters as-is
                        esac ;;
                esac
            fi
        done

        printf "  ${YELLOW}ℹ️${NC}   Waiting for API to be ready...\n"
        for i in $(seq 1 12); do
            sleep 5
            if curl -sf --max-time 3 http://localhost:8000/api/status &>/dev/null; then
                break
            fi
        done
    fi
fi

# ═════════════════════════════════════════════════════════════
section "3 / 4   API Health  (http://localhost:8000/api/status)"
# ═════════════════════════════════════════════════════════════

API_RESP=$(curl -sf --max-time 8 http://localhost:8000/api/status 2>/dev/null || true)

if [ -z "$API_RESP" ]; then
    fail "API not responding on port 8000 — check:"
    warn "  docker compose $ENV_FILE_ARGS logs api"
else
    GW_RUNNING=$(echo "$API_RESP" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d.get('gateway_running','?'))" 2>/dev/null || echo "?")
    IBKR_CONN=$(echo "$API_RESP" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d.get('ibkr_connected','?'))" 2>/dev/null || echo "?")
    ACCOUNT=$(echo "$API_RESP" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d.get('account','unknown'))" 2>/dev/null || echo "unknown")
    TRADING_MODE=$(echo "$API_RESP" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d.get('trading_mode','?'))" 2>/dev/null || echo "?")

    pass "API responding  (mode: $TRADING_MODE)"

    if [ "$GW_RUNNING" = "True" ] || [ "$GW_RUNNING" = "true" ]; then
        pass "IB Gateway container: running"
    else
        warn "IB Gateway not yet running — allow 60 s, then:"
        warn "  docker compose $ENV_FILE_ARGS logs -f ib_gateway"
    fi

    if [ "$IBKR_CONN" = "True" ] || [ "$IBKR_CONN" = "true" ]; then
        pass "IBKR connected  (account: $ACCOUNT)"
    else
        warn "IBKR not connected — Gateway may still be logging in"
    fi
fi

# ═════════════════════════════════════════════════════════════
section "4 / 4   Pre-flight Checks"
# ═════════════════════════════════════════════════════════════

# DRY_RUN from running API settings endpoint
DRY_RESP=$(curl -sf --max-time 5 http://localhost:8000/api/settings 2>/dev/null || true)
if [ -n "$DRY_RESP" ]; then
    DRY=$(echo "$DRY_RESP" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d.get('dry_run','?'))" 2>/dev/null || echo "?")
    if [ "$DRY" = "False" ] || [ "$DRY" = "false" ]; then
        pass "DRY_RUN = false  (orders will be submitted to IBKR)"
    elif [ "$DRY" = "True" ] || [ "$DRY" = "true" ]; then
        pass "DRY_RUN = true  (orders simulated — toggle off in Settings when ready to trade)"
    else
        warn "Could not determine DRY_RUN state from API"
    fi
else
    warn "Could not reach API settings endpoint"
fi

# MIN_DAYS_TO_EXPIRY
DTE=$(grep "^MIN_DAYS_TO_EXPIRY" screener.py | grep -o '[0-9]*' | head -1)
if [ "$DTE" = "3" ]; then
    pass "MIN_DAYS_TO_EXPIRY = 3  (correct for Monday execution)"
else
    warn "MIN_DAYS_TO_EXPIRY = ${DTE:-unknown}  (expected 3 — Mon→Fri = 3 DTE)"
fi

# Scheduler container health
SCHED_LINE=$(docker compose $ENV_FILE_ARGS ps 2>/dev/null \
    | grep -i "scheduler" | head -1 || true)
if echo "$SCHED_LINE" | grep -qiE "Up|running|healthy"; then
    pass "Scheduler container healthy"
else
    warn "Scheduler container status unclear — check:"
    warn "  docker compose $ENV_FILE_ARGS logs scheduler"
fi

# ═════════════════════════════════════════════════════════════
# GO / NO-GO
# ═════════════════════════════════════════════════════════════

echo ""
echo "══════════════════════════════════════════════════════"
printf "  Checks: ${GREEN}%d passed${NC}  " "$PASS"
printf "${YELLOW}%d warning(s)${NC}  "     "$WARN"
printf "${RED}%d failed${NC}\n"             "$FAIL"
echo "══════════════════════════════════════════════════════"

DOW_FINAL=$(date +%u)
case "$DOW_FINAL" in
    1|2) DAY_CTX="Monday trading is imminent" ;;
    3)   DAY_CTX="next trade Monday 10:00 AM PST" ;;
    4)   DAY_CTX="next trade Monday 10:00 AM PST" ;;
    5)   DAY_CTX="next trade Monday 10:00 AM PST" ;;
    6)   DAY_CTX="screener preview runs tonight 6:00 PM PST" ;;
    7)   DAY_CTX="screener ran yesterday — targets ready for Monday" ;;
    *)   DAY_CTX="next trade Monday 10:00 AM PST" ;;
esac

if   [ "$FAIL" -eq 0 ] && [ "$WARN" -eq 0 ]; then
    echo ""
    printf "  ${BOLD}${GREEN}🟢  GO — All systems ready  (%s)${NC}\n" "$DAY_CTX"
    echo ""
    printf "  ${BOLD}${BLUE}🌐  YRVI Dashboard: http://localhost:3000${NC}\n"
    echo ""
    if [ "${YRVI_NO_OPEN:-}" = "1" ]; then
        :   # invoked by the desktop launcher — it opens the browser itself, so
            # skip here to avoid opening a duplicate tab.
    elif [ "$(uname -s)" = "Darwin" ]; then
        open http://localhost:3000
    else
        echo "  Open http://localhost:3000"
    fi
elif [ "$FAIL" -eq 0 ]; then
    echo ""
    printf "  ${BOLD}${YELLOW}🟡  GO with warnings — review items above  (%s)${NC}\n" "$DAY_CTX"
    echo ""
    printf "  ${BOLD}${BLUE}🌐  YRVI Dashboard: http://localhost:3000${NC}\n"
    echo ""
else
    echo ""
    printf "  ${BOLD}${RED}🔴  NO-GO — resolve %d critical issue(s)  (%s)${NC}\n" "$FAIL" "$DAY_CTX"
    echo ""
    printf "  ${BOLD}${BLUE}🌐  YRVI Dashboard: http://localhost:3000${NC}\n"
    echo ""
fi
