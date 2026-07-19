#!/bin/bash

LOCKFILE="$HOME/.yrvi_restart.lock"
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
#  Container Restart — You Rock Volatility Income Fund
#
#  Usage:
#    ./scripts/yrvi-restart.sh <container> --paper|--live [--dry-run]
#
#  Valid containers: ib_gateway  api  scheduler  web
#
#  What it does:
#    1. Verifies the docker compose stack is running
#    2. Verifies the secrets container is reachable and configured
#    3. Restarts the named container
#    4. Polls health status every 3s until healthy or 60s timeout
# ─────────────────────────────────────────────────────────────

set -euo pipefail

# ── Flag parsing ───────────────────────────────────────────────

VALID_CONTAINERS="ib_gateway api scheduler web"

usage() {
    echo ""
    echo "  Usage: yrvi-restart.sh <container> --paper|--live [--dry-run]"
    echo ""
    echo "    <container>      one of: ib_gateway  api  scheduler  web"
    echo "    --paper          paper trading mode (IBKR paper account)"
    echo "    --live           live trading mode  (IBKR live account)"
    echo "    --dry-run        print what would happen; make no changes"
    echo ""
}

CONTAINER=""
TRADING_MODE=""
DRY_RUN=false

for arg in "$@"; do
    case "$arg" in
        --paper)        TRADING_MODE="paper" ;;
        --live)         TRADING_MODE="live"  ;;
        --dry-run)      DRY_RUN=true         ;;
        --help|-h) usage; exit 0 ;;
        --*) printf "Unknown flag: %s\n" "$arg" >&2; usage; exit 1 ;;
        *)
            if [ -z "$CONTAINER" ]; then
                CONTAINER="$arg"
            else
                printf "Unexpected argument: %s\n" "$arg" >&2; usage; exit 1
            fi
            ;;
    esac
done

if [ -z "$CONTAINER" ]; then
    printf "Error: container name is required\n" >&2; usage; exit 1
fi

VALID=false
for c in $VALID_CONTAINERS; do
    [ "$c" = "$CONTAINER" ] && VALID=true && break
done
if [ "$VALID" = false ]; then
    printf "Error: '%s' is not a valid container\nValid names: %s\n" "$CONTAINER" "$VALID_CONTAINERS" >&2
    usage; exit 1
fi

if [ -z "$TRADING_MODE" ]; then
    printf "Error: --paper or --live is required\n" >&2; usage; exit 1
fi

# ── Globals ────────────────────────────────────────────────────

PROJ=$(cd "$(dirname "$0")/.." && pwd)

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

ok()   { printf "  ${GREEN}✅${NC}  %s\n" "$1"; }
fail() { printf "  ${RED}❌${NC}  %s\n" "$1" >&2; exit 1; }
warn() { printf "  ${YELLOW}⚠️${NC}   %s\n" "$1"; }
info() { printf "  ${BLUE}ℹ️${NC}   %s\n" "$1"; }

MODE_LABEL="paper"
[ "$TRADING_MODE" = "live" ] && MODE_LABEL="LIVE"
DRY_LABEL=""
[ "$DRY_RUN" = true ] && DRY_LABEL=" [dry run]"

echo ""
printf "${BOLD}${BLUE}"
echo "╔══════════════════════════════════════════════════╗"
printf "║    Container Restart — YRVI  (%-17s)  ║\n" "$CONTAINER"
echo "╚══════════════════════════════════════════════════╝"
printf "${NC}"
echo ""
info "Mode: $MODE_LABEL$DRY_LABEL"
echo ""

# ── Safety checks ──────────────────────────────────────────────

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
if [ ! -f ".env.compose" ]; then
    fail "Must be run from repo root — .env.compose not found in $PROJ"
fi

if [ "$TRADING_MODE" = "live" ] && [ "${YRVI_ENV:-}" != "live" ]; then
    printf "  ${RED}❌${NC}  --live requires YRVI_ENV=live in your environment\n" >&2
    printf "  ${BLUE}ℹ️${NC}   Set it and retry:\n" >&2
    printf "         export YRVI_ENV=live\n" >&2
    printf "         ./scripts/yrvi-restart.sh %s --live\n" "$CONTAINER" >&2
    exit 1
fi

if [ "$DRY_RUN" = true ]; then
    info "DRY RUN — no changes will be made"
    echo ""
fi

# ── Step 1: Verify stack is running ────────────────────────────
printf "${BOLD}Step 1 / 4   Verify stack is running${NC}\n"
echo "──────────────────────────────────────────────────────"

RUNNING=$(docker compose $ENV_FILE_ARGS ps --status running 2>/dev/null \
    | grep -cE "running|Up" || true)

if [ "$RUNNING" -eq 0 ]; then
    printf "  ${RED}❌${NC}  Docker compose stack is not running\n" >&2
    info "Start it first:  bash setup_docker.sh --${TRADING_MODE}"
    exit 1
fi

CONTAINER_ID=$(docker compose $ENV_FILE_ARGS ps -q "$CONTAINER" 2>/dev/null | head -1 || true)
if [ -z "$CONTAINER_ID" ]; then
    fail "'$CONTAINER' is not in the running stack ($RUNNING other container(s) running)"
fi

ok "$RUNNING container(s) running — $CONTAINER found (id: ${CONTAINER_ID:0:12})"

# ── Step 2: Verify secrets container ───────────────────────────
echo ""
printf "${BOLD}Step 2 / 4   Verify secrets container${NC}\n"
echo "──────────────────────────────────────────────────────"

SECRETS_URL="http://localhost:8001"

STATUS_BODY=$(curl -sf "$SECRETS_URL/secrets/status" 2>/dev/null || true)

if [ -z "$STATUS_BODY" ]; then
    printf "  ${RED}❌${NC}  Secrets container is not running.\n" >&2
    info "Start it first:"
    info "  docker compose $ENV_FILE_ARGS up -d secrets"
    exit 1
fi

COMPLETE=$(printf '%s' "$STATUS_BODY" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('true' if d.get('complete') else 'false')" 2>/dev/null \
    || echo "false")

if [ "$COMPLETE" != "true" ]; then
    printf "  ${RED}❌${NC}  Secrets not fully configured.\n" >&2
    info "Open $SECRETS_URL to enter missing secrets, then retry."
    exit 1
fi

ok "Secrets container ready"

# ── Step 3: Restart container ──────────────────────────────────
echo ""
printf "${BOLD}Step 3 / 4   Restart container${NC}\n"
echo "──────────────────────────────────────────────────────"

if [ "$DRY_RUN" = true ]; then
    info "Would run: docker restart $CONTAINER_ID  (service: $CONTAINER)"
else
    info "Restarting $CONTAINER..."
    docker restart "$CONTAINER_ID" >/dev/null
    ok "$CONTAINER restarted"
fi

# ── Step 4: Wait for healthy ───────────────────────────────────
echo ""
printf "${BOLD}Step 4 / 4   Wait for container to become healthy${NC}\n"
echo "──────────────────────────────────────────────────────"

# ib_gateway needs extra time to authenticate with IBKR after restart
case "$CONTAINER" in
    ib_gateway) TIMEOUT=180 ;;
    *)          TIMEOUT=60  ;;
esac

if [ "$DRY_RUN" = true ]; then
    info "Would poll docker inspect health every 3s (timeout ${TIMEOUT}s)"
else
    ELAPSED=0
    FINAL_STATUS=""

    while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
        # Null-safe: containers without a HEALTHCHECK (web, ib_gateway) have a nil
        # .State.Health; the {{if .State.Health}} guard yields an empty string
        # instead of erroring, so the "no healthcheck" branch treats it as success.
        HEALTH=$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$CONTAINER_ID" 2>/dev/null || echo "error")
        STATUS=$(docker inspect --format='{{.State.Status}}'        "$CONTAINER_ID" 2>/dev/null || echo "error")

        if [ "$STATUS" = "exited" ] || [ "$STATUS" = "dead" ]; then
            FINAL_STATUS="$STATUS"; break
        fi

        if [ "$HEALTH" = "healthy" ]; then
            FINAL_STATUS="healthy"; break
        elif [ "$HEALTH" = "unhealthy" ]; then
            FINAL_STATUS="unhealthy"; break
        elif [ -z "$HEALTH" ] || [ "$HEALTH" = "<no value>" ]; then
            # No HEALTHCHECK defined — docker inspect returns empty or "<no value>"
            if [ "$STATUS" = "running" ]; then
                FINAL_STATUS="running (no healthcheck)"; break
            fi
        fi

        ELAPSED=$(( ELAPSED + 3 ))
        printf "  waiting... %ds / %ds  (health: %s, status: %s)\r" \
            "$ELAPSED" "$TIMEOUT" "${HEALTH:-none}" "$STATUS"
        sleep 3
    done
    echo ""

    if [ -z "$FINAL_STATUS" ]; then
        printf "  ${RED}❌${NC}  %s did not become healthy within %ds\n" "$CONTAINER" "$TIMEOUT" >&2
        info "Check logs: docker compose $ENV_FILE_ARGS logs --tail=50 $CONTAINER"
        exit 1
    elif [ "$FINAL_STATUS" = "unhealthy" ] || \
         [ "$FINAL_STATUS" = "exited" ]    || \
         [ "$FINAL_STATUS" = "dead" ]; then
        printf "  ${RED}❌${NC}  %s status is '%s'\n" "$CONTAINER" "$FINAL_STATUS" >&2
        info "Check logs: docker compose $ENV_FILE_ARGS logs --tail=50 $CONTAINER"
        exit 1
    else
        ok "$CONTAINER is $FINAL_STATUS"
    fi
fi

# ── Summary ────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════"
if [ "$DRY_RUN" = true ]; then
    printf "${BOLD}${YELLOW}  Dry run complete — no changes made.${NC}\n"
else
    printf "${BOLD}${GREEN}  $CONTAINER restarted successfully.${NC}\n"
fi
echo "══════════════════════════════════════════════════════"
echo ""
if [ "$DRY_RUN" = false ]; then
    info "Logs:   docker compose $ENV_FILE_ARGS logs --tail=50 $CONTAINER"
    info "Status: docker compose $ENV_FILE_ARGS ps"
    echo ""
fi
