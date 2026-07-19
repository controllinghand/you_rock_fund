#!/bin/sh
set -eu

env_file="${1:-.env.compose}"
failed=0

fail() {
    printf 'ERROR: %s\n' "$1" >&2
    failed=1
}

value_for() {
    key="$1"
    if [ ! -f "$env_file" ]; then
        return
    fi
    awk -F= -v key="$key" '
        $0 !~ /^[[:space:]]*#/ && $1 == key {
            sub(/^[^=]*=/, "")
            print
            exit
        }
    ' "$env_file"
}

is_placeholder() {
    value="$1"
    case "$value" in
        ""|your_*|YOUR_*|replace-with*|get_from*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

# A missing $env_file is NOT an error. It is install-time only (host ports +
# image tag) and every value it can set has a compose default, so an operator
# may delete it once installed; the trading mode then comes from the durable
# /data/gw_trading_mode file like it does on every other start. Only validate
# the seed value when the file is actually present.
if [ ! -f "$env_file" ]; then
    printf 'Note: %s not present — using compose defaults (host ports + image tag).\n' "$env_file"
else
    mode="$(value_for TRADING_MODE)"
    if [ "$mode" = "live" ]; then
        fail "This Compose stack is currently wired for paper Gateway login. Keep TRADING_MODE=paper until live Compose wiring is intentionally enabled."
    fi
fi

status_body="$(curl -sf http://localhost:8001/secrets/status 2>/dev/null || true)"

if [ -z "$status_body" ]; then
    fail "Secrets container is not running — run setup_docker.sh first"
else
    complete="$(printf '%s' "$status_body" | python3 -c "import sys,json; d=json.load(sys.stdin); print('true' if d.get('complete') else 'false')" 2>/dev/null || echo "false")"
    if [ "$complete" != "true" ]; then
        fail "Required secrets not configured — open http://localhost:8001 to enter missing secrets"
    fi

    # Sanity check: account and username must not be identical (common copy/paste mistake)
    paper_acct="$(curl -sf http://localhost:8001/secret/account_paper 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('value',''))" 2>/dev/null || true)"
    paper_user="$(curl -sf http://localhost:8001/secret/tws_userid_paper 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('value',''))" 2>/dev/null || true)"
    if [ -n "$paper_acct" ] && [ "$paper_acct" = "$paper_user" ]; then
        fail "tws_userid_paper must be the paper login username, not the paper account id (open http://localhost:8001 to fix)."
    fi
fi

if [ "$failed" -ne 0 ]; then
    exit 1
fi

printf 'Container preflight OK: required config and secrets are populated.\n'
