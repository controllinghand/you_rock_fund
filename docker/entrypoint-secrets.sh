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

    if [ -e "$settings_file" ] || [ "${YRVI_INIT_DRY_RUN:-true}" = "false" ]; then
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

settings["dry_run"] = True
if os.environ.get("IBKR_PORT"):
    settings["ibkr_port"] = int(os.environ["IBKR_PORT"])
if os.environ.get("TRADING_MODE"):
    settings["trading_mode"] = os.environ["TRADING_MODE"]
settings_file.write_text(json.dumps(settings, indent=2) + "\n")
PY
}

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
