#!/bin/bash

LOCKFILE="$HOME/.yrvi_build.lock"
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
#  Container Build — You Rock Volatility Income Fund
#
#  Usage:
#    ./scripts/yrvi-build.sh <container> --paper|--live [--dry-run]
#
#  Valid containers: ib_gateway  api  scheduler  web  all
#
#  When to use each script:
#    yrvi-restart.sh  → restart existing container (same image, no rebuild)
#    yrvi-build.sh    → rebuild image and restart (use after code changes)
#
#  What it does:
#    1. Verifies the docker compose stack is running
#    2. Re-injects secrets from macOS Keychain → docker/secrets/
#    3. Rebuilds and restarts the named container (docker compose up -d --build)
#    4. Polls health status every 3s until healthy or timeout
#    5. Wipes secret files
# ─────────────────────────────────────────────────────────────

set -euo pipefail

# ── Flag parsing ───────────────────────────────────────────────

VALID_CONTAINERS="ib_gateway api scheduler web all"

usage() {
    echo ""
    echo "  Usage: yrvi-build.sh <container> --paper|--live [--dry-run]"
    echo ""
    echo "    <container>      one of: ib_gateway  api  scheduler  web  all"
    echo "    --paper          paper trading mode (IBKR paper account)"
    echo "    --live           live trading mode  (IBKR live account)"
    echo "    --dry-run        print what would happen; make no changes"
    echo ""
    echo "  Use yrvi-restart.sh to restart without rebuilding the image."
    echo ""
}

CONTAINER=""
TRADING_MODE=""
DRY_RUN=false

for arg in "$@"; do
    case "$arg" in
        --paper)   TRADING_MODE="paper" ;;
        --live)    TRADING_MODE="live"  ;;
        --dry-run) DRY_RUN=true         ;;
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
printf "║    Container Build — YRVI    (%-17s)  ║\n" "$CONTAINER"
echo "╚══════════════════════════════════════════════════╝"
printf "${NC}"
echo ""
info "Mode: $MODE_LABEL$DRY_LABEL"
echo ""

# ── Safety checks ──────────────────────────────────────────────

cd "$PROJ"
if [ ! -f ".env.compose" ]; then
    fail "Must be run from repo root — .env.compose not found in $PROJ"
fi

if [ "$TRADING_MODE" = "live" ] && [ "${YRVI_ENV:-}" != "live" ]; then
    printf "  ${RED}❌${NC}  --live requires YRVI_ENV=live in your environment\n" >&2
    printf "  ${BLUE}ℹ️${NC}   Set it and retry:\n" >&2
    printf "         export YRVI_ENV=live\n" >&2
    printf "         ./scripts/yrvi-build.sh %s --live\n" "$CONTAINER" >&2
    exit 1
fi

if [ "$DRY_RUN" = true ]; then
    info "DRY RUN — no changes will be made"
    echo ""
fi

# ── Step 1: Verify stack is running ────────────────────────────
printf "${BOLD}Step 1 / 4   Verify stack is running${NC}\n"
echo "──────────────────────────────────────────────────────"

RUNNING=$(docker compose --env-file .env.compose ps --status running 2>/dev/null \
    | grep -cE "running|Up" || true)

if [ "$RUNNING" -eq 0 ]; then
    printf "  ${RED}❌${NC}  Docker compose stack is not running\n" >&2
    info "Start it first:  bash setup_docker.sh --${TRADING_MODE}"
    exit 1
fi

CONTAINER_ID=""
if [ "$CONTAINER" = "all" ]; then
    ok "$RUNNING container(s) running — rebuilding full stack"
else
    CONTAINER_ID=$(docker compose --env-file .env.compose ps -q "$CONTAINER" 2>/dev/null | head -1 || true)
    if [ -z "$CONTAINER_ID" ]; then
        fail "'$CONTAINER' is not in the running stack ($RUNNING other container(s) running)"
    fi
    ok "$RUNNING container(s) running — $CONTAINER found (id: ${CONTAINER_ID:0:12})"
fi

# ── Step 2: Inject secrets ─────────────────────────────────────
echo ""
printf "${BOLD}Step 2 / 4   Inject secrets${NC}\n"
echo "──────────────────────────────────────────────────────"

WRITTEN_SECRET_FILES=()

# Check if the secrets container is running — if so, credentials are managed
# there (stored in the yrvi_data Docker volume) and no Keychain injection needed.
SECRETS_RUNNING=$(docker ps --filter "name=yrvi-secrets-1" --filter "status=running" \
    --format "{{.Names}}" 2>/dev/null | grep -c "yrvi-secrets-1" || true)

if [ "$SECRETS_RUNNING" -gt 0 ]; then
    ok "Secrets container is running — credentials managed via secrets UI (port 8001)"
    info "Skipping Keychain injection"
else
    # Secrets container not running — fall back to macOS Keychain injection
    info "Secrets container not detected — injecting from macOS Keychain"
    echo ""

    mkdir -p docker/secrets

    # Create empty placeholder files for optional secrets (only if absent — never overwrite)
    for _placeholder in \
        docker/secrets/discord_webhook_url \
        docker/secrets/discord_webhook_weekly_plan \
        docker/secrets/anthropic_api_key \
        docker/secrets/ibkr_password_live; do
        if [ ! -f "$_placeholder" ]; then
            if [ "$DRY_RUN" = true ]; then
                info "Would create placeholder: $_placeholder"
            else
                touch "$_placeholder"
            fi
        fi
    done
    unset _placeholder

    # Keychain service names (fixed — do not change without updating Keychain entries)
    KC_RENDER="YRVI_RENDER"
    if [ "$TRADING_MODE" = "paper" ]; then
        KC_TWS="YRVI_TWS_PAPER"
        TWS_SECRET_FILE="docker/secrets/tws_password_paper"
        TWS_LABEL="IBKR paper trading password"
    else
        KC_TWS="YRVI_TWS_LIVE"
        TWS_SECRET_FILE="docker/secrets/tws_password_live"
        TWS_LABEL="IBKR live trading password"
    fi

    # fetch_secret SERVICE FILE LABEL
    #   Retrieves secret from Keychain only — exits if missing, never prompts.
    fetch_secret() {
        local service="$1"
        local file="$2"
        local label="$3"

        local value
        value=$(security find-generic-password -s "$service" -w 2>/dev/null || true)

        if [ -z "$value" ]; then
            printf "  ${RED}❌${NC}  '%s' not found in Keychain (service: %s)\n" "$label" "$service" >&2
            printf "  ${BLUE}ℹ️${NC}   Run setup_docker.sh first to store secrets:\n" >&2
            printf "         bash setup_docker.sh --%s\n" "$TRADING_MODE" >&2
            exit 1
        fi

        if [ "$DRY_RUN" = true ]; then
            ok "[dry run] Would write '$label' → $file"
            return
        fi

        printf '%s' "$value" > "$file"
        chmod 600 "$file"
        WRITTEN_SECRET_FILES+=("$file")
        ok "Retrieved '$label' from Keychain"
    }

    info "macOS may prompt you to allow Keychain access — click Allow."
    echo ""

    fetch_secret "$KC_TWS"    "$TWS_SECRET_FILE"            "$TWS_LABEL"
    fetch_secret "$KC_RENDER" "docker/secrets/render_secret" "Render screener API secret"

    [ "$DRY_RUN" = false ] && ok "Secret files written to docker/secrets/"
fi

# ── Step 3: Build and restart ──────────────────────────────────
echo ""
printf "${BOLD}Step 3 / 4   Build and restart${NC}\n"
echo "──────────────────────────────────────────────────────"

if [ "$DRY_RUN" = true ]; then
    if [ "$CONTAINER" = "all" ]; then
        info "Would run: docker compose --env-file .env.compose up -d --build"
    else
        info "Would run: docker compose --env-file .env.compose up -d --build $CONTAINER"
    fi
else
    info "Building and restarting ${CONTAINER}..."
    if [ "$CONTAINER" = "all" ]; then
        docker compose --env-file .env.compose up -d --build
    else
        docker compose --env-file .env.compose up -d --build "$CONTAINER"
        # Re-query after rebuild — container ID changes after docker compose replaces the container
        CONTAINER_ID=$(docker compose --env-file .env.compose ps -q "$CONTAINER" 2>/dev/null | head -1 || true)
    fi
    ok "${CONTAINER} built and started"
fi

# ── Step 4: Wait for healthy ───────────────────────────────────
echo ""
printf "${BOLD}Step 4 / 4   Wait for container to become healthy${NC}\n"
echo "──────────────────────────────────────────────────────"

# ib_gateway (and full stack) needs extra time; others are faster
case "$CONTAINER" in
    ib_gateway|all) TIMEOUT=180 ;;
    *)              TIMEOUT=60  ;;
esac

if [ "$DRY_RUN" = true ]; then
    info "Would poll docker inspect health every 3s (timeout ${TIMEOUT}s)"
else
    ELAPSED=0
    FINAL_STATUS=""

    if [ "$CONTAINER" = "all" ]; then
        # For full stack: poll until all 4 services report running/healthy
        while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
            UP=$(docker compose --env-file .env.compose ps 2>/dev/null \
                | grep -cE "Up|running|healthy" || true)
            if [ "$UP" -ge 4 ]; then
                FINAL_STATUS="all running ($UP / 4)"; break
            fi
            ELAPSED=$(( ELAPSED + 3 ))
            printf "  waiting... %ds / %ds  (%s / 4 containers up)\r" \
                "$ELAPSED" "$TIMEOUT" "$UP"
            sleep 3
        done
        echo ""

        if [ -z "$FINAL_STATUS" ]; then
            UP=$(docker compose --env-file .env.compose ps 2>/dev/null \
                | grep -cE "Up|running|healthy" || true)
            if [ "$UP" -gt 0 ]; then
                # Partial start — ib_gateway may still be authenticating; warn but don't fail
                warn "$UP / 4 containers running after ${TIMEOUT}s — ib_gateway may still be initializing"
                info "Monitor: docker compose --env-file .env.compose logs -f ib_gateway"
                FINAL_STATUS="partial ($UP / 4)"
            else
                printf "  ${RED}❌${NC}  No containers running after %ds\n" "$TIMEOUT" >&2
                info "Check logs: docker compose --env-file .env.compose logs"
                if [ ${#WRITTEN_SECRET_FILES[@]} -gt 0 ]; then
                    for f in "${WRITTEN_SECRET_FILES[@]}"; do rm -f "$f"; done
                fi
                exit 1
            fi
        fi
    else
        # For a single container: inspect health directly (same logic as yrvi-restart.sh)
        while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
            HEALTH=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER_ID" 2>/dev/null || echo "error")
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
            info "Check logs: docker compose --env-file .env.compose logs --tail=50 $CONTAINER"
            if [ ${#WRITTEN_SECRET_FILES[@]} -gt 0 ]; then
                for f in "${WRITTEN_SECRET_FILES[@]}"; do rm -f "$f"; done
            fi
            exit 1
        elif [ "$FINAL_STATUS" = "unhealthy" ] || \
             [ "$FINAL_STATUS" = "exited" ]    || \
             [ "$FINAL_STATUS" = "dead" ]; then
            printf "  ${RED}❌${NC}  %s status is '%s'\n" "$CONTAINER" "$FINAL_STATUS" >&2
            info "Check logs: docker compose --env-file .env.compose logs --tail=50 $CONTAINER"
            if [ ${#WRITTEN_SECRET_FILES[@]} -gt 0 ]; then
                for f in "${WRITTEN_SECRET_FILES[@]}"; do rm -f "$f"; done
            fi
            exit 1
        fi
    fi

    ok "$CONTAINER is $FINAL_STATUS"

    # ── Wipe plaintext secret files ─────────────────────────────
    if [ ${#WRITTEN_SECRET_FILES[@]} -gt 0 ]; then
        for f in "${WRITTEN_SECRET_FILES[@]}"; do rm -f "$f"; done
    fi
    ok "Secret files wiped — passwords remain safely in macOS Keychain"
fi

# ── Summary ────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════"
if [ "$DRY_RUN" = true ]; then
    printf "${BOLD}${YELLOW}  Dry run complete — no changes made.${NC}\n"
else
    printf "${BOLD}${GREEN}  $CONTAINER built and restarted successfully.${NC}\n"
fi
echo "══════════════════════════════════════════════════════"
echo ""
if [ "$DRY_RUN" = false ]; then
    if [ "$CONTAINER" = "all" ]; then
        info "Logs:   docker compose --env-file .env.compose logs -f"
    else
        info "Logs:   docker compose --env-file .env.compose logs --tail=50 $CONTAINER"
    fi
    info "Status: docker compose --env-file .env.compose ps"
    echo ""
fi
