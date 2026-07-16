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

if [ ! -f "$env_file" ]; then
    fail "$env_file is missing. Copy .env.compose.example to .env.compose first."
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
    if printf '%s' "$status_body" | grep -q '"complete"[[:space:]]*:[[:space:]]*true'; then
        complete="true"
    else
        complete="false"
    fi
    if [ "$complete" != "true" ]; then
        fail "Required secrets not configured — open http://localhost:8001 to enter missing secrets"
    fi

    # Sanity check: account and username must not be identical (common copy/paste mistake)
    paper_acct="$(curl -sf http://localhost:8001/secret/account_paper 2>/dev/null | sed -n 's/.*"value"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
    paper_user="$(curl -sf http://localhost:8001/secret/tws_userid_paper 2>/dev/null | sed -n 's/.*"value"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
    if [ -n "$paper_acct" ] && [ "$paper_acct" = "$paper_user" ]; then
        fail "tws_userid_paper must be the paper login username, not the paper account id (open http://localhost:8001 to fix)."
    fi
fi

if [ "$failed" -ne 0 ]; then
    exit 1
fi

printf 'Container preflight OK: required config and secrets are populated.\n'
