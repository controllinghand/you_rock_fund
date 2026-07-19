#!/bin/sh
set -eu

load_secret() {
    var_name="$1"
    secret_path="$2"

    current_value="$(printenv "$var_name" 2>/dev/null || true)"
    if [ -z "$current_value" ] && [ -r "$secret_path" ]; then
        # Strip trailing newlines commonly introduced by `printf` or editors.
        secret_value="$(tr -d '\r\n' < "$secret_path")"
        if [ -n "$secret_value" ]; then
            export "$var_name=$secret_value"
        fi
    fi
}

link_runtime_file() {
    file_name="$1"
    data_dir="${YRVI_DATA_DIR:-/data}"
    target="$data_dir/$file_name"
    source="/app/$file_name"

    mkdir -p "$data_dir"

    if [ -f "$source" ] && [ ! -L "$source" ] && [ ! -e "$target" ]; then
        cp "$source" "$target"
    fi

    if [ ! -L "$source" ]; then
        rm -f "$source"
        ln -s "$target" "$source"
    fi
}

initialize_settings() {
    data_dir="${YRVI_DATA_DIR:-/data}"
    settings_file="$data_dir/settings.json"

    mkdir -p "$data_dir"

    if [ -e "$settings_file" ]; then
        return
    fi

    python - <<'PY'
import json
import os
from pathlib import Path

data_dir = Path(os.environ.get("YRVI_DATA_DIR", "/data"))
settings_file = data_dir / "settings.json"
defaults_file = Path("/app/settings_default.json")

settings = {}
if defaults_file.exists():
    settings = json.loads(defaults_file.read_text())

# Dry Run defaults to OFF (from settings_default.json). Fresh installs are
# paper-mode, so the paper account — not simulation — is the safety net; seeding
# dry_run=true would just create "am I actually trading?" confusion. A user opts
# into Dry Run explicitly via Settings; it's the exception, not the default.
if os.environ.get("IBKR_PORT"):
    settings["ibkr_port"] = int(os.environ["IBKR_PORT"])
if os.environ.get("TRADING_MODE"):
    settings["trading_mode"] = os.environ["TRADING_MODE"]
settings_file.write_text(json.dumps(settings, indent=2) + "\n")
PY
}

# ── Durable trading-mode override ────────────────────────────────
# The active trading mode (live/paper) is persisted to /data/gw_trading_mode by
# the YRVI API so it survives software upgrades, which reset .env.compose back
# to its committed defaults (paper / 4004). Derive TRADING_MODE and IBKR_PORT
# from that file — the same source of truth the IB Gateway entrypoint reads —
# so the scheduler/api and the gateway can never disagree on which account is
# live. (Inside the container network the gateway listens on 4003=live, 4004=paper.)
if [ -f "/data/gw_trading_mode" ]; then
    _tm_override="$(tr -d '\r\n' < /data/gw_trading_mode 2>/dev/null || true)"
    if [ "$_tm_override" = "live" ] || [ "$_tm_override" = "paper" ]; then
        TRADING_MODE="$_tm_override"
        export TRADING_MODE
        echo "yrvi-entrypoint: trading mode from /data/gw_trading_mode -> ${TRADING_MODE}"
    fi
fi

# IBKR_PORT is DERIVED, never configured. It is a pure function of the trading
# mode (inside the container network the gateway listens on 4003=live,
# 4004=paper), so letting .env.compose supply it independently only creates a
# way for the port and the mode to disagree — which is exactly the v3.9.19 bug
# (api kept a stale port and hit Errno 111/4004 against the wrong gateway).
# Deriving it unconditionally also closes the case where /data/gw_trading_mode
# is absent: previously the durable block was skipped entirely and BOTH values
# fell back to .env.compose.
if [ "${TRADING_MODE:-paper}" = "live" ]; then
    IBKR_PORT=4003
else
    IBKR_PORT=4004
fi
export IBKR_PORT
echo "yrvi-entrypoint: IBKR_PORT=${IBKR_PORT} (derived from TRADING_MODE=${TRADING_MODE:-paper})"

# ── Values the app owns; .env.compose must not be able to change them ──
# Post-install, behaviour is owned by the app (settings.json + the durable
# /data/gw_* files) or is a constant that no operator should tune. A stale
# .env.compose — every box carries one, and upgrades reset it to committed
# defaults — must never be able to shadow that. Overriding here rather than
# rewriting the operator's file keeps unreachable standalone boxes safe.
#
# Report anything ignored so this is observable rather than a silent override.
_obsolete=""
for _k in IBKR_PORT IBKR_CLIENT_ID RENDER_URL READ_ONLY_API TWS_ACCEPT_INCOMING \
          ALLOW_BLIND_TRADING EXISTING_SESSION_DETECTED_ACTION \
          TWOFA_TIMEOUT_ACTION RELOGIN_AFTER_TWOFA_TIMEOUT TWOFA_EXIT_INTERVAL
do
    if grep -qE "^[[:space:]]*${_k}=" /host_repo/.env.compose 2>/dev/null; then
        _obsolete="${_obsolete} ${_k}"
    fi
done
if [ -n "$_obsolete" ]; then
    echo "yrvi-entrypoint: ignoring obsolete .env.compose keys (app-owned or baked):${_obsolete}"
    echo "yrvi-entrypoint: .env.compose now affects host ports + image tag ONLY"
fi

# IBKR client ids are already hardcoded in config.py (wheel=2, risk=3,
# preview=4, cash_park=5); 1 was the odd one out, configurable for no reason.
IBKR_CLIENT_ID=1
export IBKR_CLIENT_ID

load_secret RENDER_SECRET /run/secrets/render_secret
load_secret ANTHROPIC_API_KEY /run/secrets/anthropic_api_key
load_secret DISCORD_WEBHOOK_URL /run/secrets/discord_webhook_url
load_secret DISCORD_WEBHOOK_WEEKLY_PLAN /run/secrets/discord_webhook_weekly_plan
load_secret IBKR_PASSWORD_LIVE /run/secrets/ibkr_password_live

initialize_settings

for runtime_file in \
    state.json \
    settings.json \
    ytd_tracker.json \
    trade_log.json \
    earnings_cache.json \
    scheduler_heartbeat.json \
    scheduler_log.txt \
    trade_log.txt \
    wheel_log.txt \
    risk_log.txt \
    scheduler_stdout.log \
    scheduler_stderr.log \
    api_stdout.log \
    api_stderr.log
do
    link_runtime_file "$runtime_file"
done

exec "$@"
