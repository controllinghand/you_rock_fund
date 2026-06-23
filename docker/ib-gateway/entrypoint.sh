#!/bin/bash
set -eu

SECRETS_BASE="http://secrets:8001/secret"
MAX_RETRIES=10
RETRY_DELAY=3
VNC_DEFAULT="ibgateway123!test"

TRADING_MODE="${TRADING_MODE:-paper}"

# Allow the YRVI API to override TRADING_MODE via a file on the shared volume.
if [ -f "/data/gw_trading_mode" ]; then
    _tm_override=$(cat "/data/gw_trading_mode" 2>/dev/null | tr -d '\n\r' || true)
    if [ "$_tm_override" = "live" ] || [ "$_tm_override" = "paper" ]; then
        TRADING_MODE="$_tm_override"
        echo "yrvi-gw-entrypoint: TRADING_MODE overridden from /data: $TRADING_MODE"
    fi
fi

if [ "$TRADING_MODE" = "live" ]; then
    PASSWORD_KEY="tws_password_live"
    USERID_KEY="tws_userid_live"
else
    PASSWORD_KEY="tws_password_paper"
    USERID_KEY="tws_userid_paper"
fi

# ── Helpers ──────────────────────────────────────────────────────

# Fetch a secret value by name. Echoes the value on stdout, empty string on failure.
# Always returns 0 so callers can decide whether empty is fatal.
fetch_secret() {
    name="$1"
    attempt=0
    while [ "$attempt" -lt "$MAX_RETRIES" ]; do
        attempt=$((attempt + 1))
        body=$(curl -sf --max-time 5 "${SECRETS_BASE}/${name}" 2>/dev/null || true)
        if [ -n "$body" ]; then
            value=$(printf '%s' "$body" | sed -n 's/.*"value"[[:space:]]*:[[:space:]]*"\(.*\)".*/\1/p')
            if [ -n "$value" ]; then
                printf '%s' "$value"
                return 0
            fi
        fi
        sleep "$RETRY_DELAY"
    done
    return 0
}

# Fetch a secret without retrying — for optional helpers like the webhook URL.
fetch_secret_once() {
    name="$1"
    body=$(curl -sf --max-time 3 "${SECRETS_BASE}/${name}" 2>/dev/null || true)
    if [ -n "$body" ]; then
        printf '%s' "$body" | sed -n 's/.*"value"[[:space:]]*:[[:space:]]*"\(.*\)".*/\1/p'
    fi
}

# Best-effort Discord notification. Never fails or blocks startup.
send_discord_alert() {
    msg="$1"
    webhook=$(fetch_secret_once "discord_webhook_url" 2>/dev/null || true)
    if [ -z "$webhook" ]; then
        echo "yrvi-gw-entrypoint: discord webhook not configured — skipping alert" >&2
        return 0
    fi
    # Escape backslashes and double quotes for the JSON body.
    escaped=$(printf '%s' "$msg" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
    payload=$(printf '{"content":"%s"}' "$escaped")
    curl -sf --max-time 5 -X POST \
        -H "Content-Type: application/json" \
        -d "$payload" \
        "$webhook" >/dev/null 2>&1 || true
    return 0
}

# Patch the IBC config *template* so our settings survive. The image's common.sh
# regenerates config.ini from this template (envsubst) on every start, so anything
# we write to config.ini directly gets clobbered — the template is the only thing
# that sticks. Applies two settings:
#   1. CommandServerPort=7462 on loopback — lets the YRVI API trigger an on-demand
#      IBC soft restart (same path as AutoRestartTime: relaunches the gateway
#      reusing its authenticated session, so no re-login and no 2FA). This is what
#      the watchdog uses to self-heal a wedged API listener without a human.
#   2. LoginFailed=terminate — IBC exits on a bad login instead of retrying into an
#      account lockout. (Previously patched on config.ini, which was then silently
#      regenerated away — this moves it to the template where it actually takes.)
# Logs a warning and continues if no writable template is found.
patch_ibc_template() {
    tmpl="${IBC_INI_TMPL:-}"
    if [ -z "$tmpl" ] || [ ! -f "$tmpl" ]; then
        for p in \
            /home/ibgateway/ibc/config.ini.tmpl \
            /opt/ibc/config.ini.tmpl \
            /root/ibc/config.ini.tmpl
        do
            if [ -f "$p" ]; then tmpl="$p"; break; fi
        done
    fi
    if [ -z "$tmpl" ] || [ ! -f "$tmpl" ] || [ ! -w "$tmpl" ]; then
        echo "yrvi-gw-entrypoint: WARNING — IBC template not found/writable; command server + LoginFailed=terminate not applied" >&2
        return 0
    fi

    # set_kv KEY VALUE — replace the line if present, else append.
    set_kv() {
        key="$1"; val="$2"
        if grep -q "^${key}=" "$tmpl" 2>/dev/null; then
            sed -i "s|^${key}=.*|${key}=${val}|" "$tmpl"
        else
            printf '%s=%s\n' "$key" "$val" >> "$tmpl"
        fi
    }

    set_kv CommandServerPort 7462
    set_kv ControlFrom 127.0.0.1
    set_kv BindAddress 127.0.0.1
    set_kv LoginFailed terminate
    echo "yrvi-gw-entrypoint: patched IBC template $tmpl (CommandServerPort=7462 loopback, LoginFailed=terminate)"
    return 0
}

# Self-heal a TWS version that the persistent volume is shadowing.
#
# The image installs the TWS/Gateway jars under $TWS_PATH (default /home/ibgateway/Jts),
# but that path is a persistent named volume at runtime. Docker only seeds a named volume
# from the image when the volume is EMPTY — so when the base image bumps the TWS version
# on a rebuild (e.g. dashboard upgrade), the already-populated volume keeps the OLD version
# and shadows the new jars baked into the image. IBC is told (TWS_MAJOR_VRSN) to launch the
# new version, can't find its jars in the volume, and exits 4 with:
#   "Offline TWS/Gateway version <X> is not installed: can't find jars folder"
#
# The Dockerfile stashes a copy of the install at /opt/tws-baked (outside the volume). Here
# we copy the expected version out of that bake into the volume if it's missing — so an
# upgrade self-heals on first start with no human and (on paper) no re-login. Idempotent:
# does nothing once the version is present.
self_heal_tws_version() {
    baked="/opt/tws-baked"
    tws_path="${TWS_PATH:-/home/ibgateway/Jts}"
    vrsn="${TWS_MAJOR_VRSN:-}"

    if [ -z "$vrsn" ]; then
        echo "yrvi-gw-entrypoint: TWS_MAJOR_VRSN unset — skipping TWS version self-heal" >&2
        return 0
    fi
    if [ ! -d "$baked" ]; then
        echo "yrvi-gw-entrypoint: no baked TWS install at $baked — skipping self-heal (older image?)" >&2
        return 0
    fi

    # Already present (with jars) in the volume-backed install tree? Nothing to do.
    if find "$tws_path" -maxdepth 4 -type d -path "*/$vrsn/jars" 2>/dev/null | grep -q .; then
        return 0
    fi

    # Locate the version dir in the bake (must contain jars). Handles both the new
    # nested layout ($baked/ibgateway/<vrsn>) and the old flat one ($baked/<vrsn>).
    baked_verdir=""
    for d in $(find "$baked" -maxdepth 3 -type d -name "$vrsn" 2>/dev/null); do
        if [ -d "$d/jars" ]; then baked_verdir="$d"; break; fi
    done
    if [ -z "$baked_verdir" ]; then
        echo "yrvi-gw-entrypoint: WARNING — TWS $vrsn not found in image bake ($baked); cannot self-heal volume" >&2
        return 0
    fi

    rel="${baked_verdir#"$baked"/}"        # e.g. ibgateway/10.48.1b  (or 10.48.1b)
    target_parent="$tws_path/$(dirname "$rel")"
    echo "yrvi-gw-entrypoint: TWS $vrsn missing from volume — restoring from image bake ($rel)..."
    mkdir -p "$target_parent"
    if cp -a "$baked_verdir" "$target_parent/"; then
        echo "yrvi-gw-entrypoint: restored TWS $vrsn into ${target_parent%/}/$vrsn"
        send_discord_alert "🔄 YRVI: IB Gateway self-healed after upgrade — restored TWS $vrsn into the settings volume (the new version was shadowed by the old one). Starting normally."
    else
        echo "yrvi-gw-entrypoint: WARNING — failed to restore TWS $vrsn into volume" >&2
    fi
    return 0
}

# ── Credentials preflight ────────────────────────────────────────

echo "yrvi-gw-entrypoint: fetching ${PASSWORD_KEY} from secrets container..."
password=$(fetch_secret "$PASSWORD_KEY")
if [ -z "$password" ]; then
    echo "yrvi-gw-entrypoint: FATAL: IBKR credentials not found in secrets container — aborting to prevent account lockout" >&2
    echo "yrvi-gw-entrypoint: missing ${PASSWORD_KEY}" >&2
    send_discord_alert "🔴 YRVI: IB Gateway refused to start — credentials missing in secrets container. Login NOT attempted. Re-run setup_docker.sh to enter credentials."
    exit 1
fi

echo "yrvi-gw-entrypoint: fetching ${USERID_KEY} from secrets container..."
userid=$(fetch_secret "$USERID_KEY")
if [ -z "$userid" ]; then
    echo "yrvi-gw-entrypoint: FATAL: IBKR credentials not found in secrets container — aborting to prevent account lockout" >&2
    echo "yrvi-gw-entrypoint: missing ${USERID_KEY}" >&2
    send_discord_alert "🔴 YRVI: IB Gateway refused to start — credentials missing in secrets container. Login NOT attempted. Re-run setup_docker.sh to enter credentials."
    exit 1
fi

echo "yrvi-gw-entrypoint: fetching vnc_server_password from secrets container (optional)..."
vnc_password=$(fetch_secret_once "vnc_server_password" || true)
if [ -z "$vnc_password" ]; then
    vnc_password="$VNC_DEFAULT"
    echo "yrvi-gw-entrypoint: vnc_server_password not set — using default"
fi

# ── Patch IBC config and prepare env ─────────────────────────────

patch_ibc_template

# Restore the running TWS version into the settings volume if an upgrade left it
# shadowed by an older one (otherwise IBC exits 4 "can't find jars folder").
self_heal_tws_version

# Allow the YRVI API to override AUTO_RESTART_TIME via a file on the shared volume
# without requiring a .env.compose edit + full stack restart.
if [ -f "/data/gw_auto_restart_time" ]; then
    _override=$(cat "/data/gw_auto_restart_time" 2>/dev/null | tr -d '\n\r' || true)
    if [ -n "$_override" ]; then
        export AUTO_RESTART_TIME="$_override"
        echo "yrvi-gw-entrypoint: AUTO_RESTART_TIME overridden from /data: $AUTO_RESTART_TIME"
    fi
fi

# Patch jts.ini to bypass API order precautions confirmation dialog.
# Runs on every startup so the setting survives Reset Installation (which wipes the Jts volume).
GATEWAY_CONF="/home/ibgateway/Jts"
mkdir -p "$GATEWAY_CONF"
jts_ini="$GATEWAY_CONF/jts.ini"
if [ ! -f "$jts_ini" ]; then
    printf "[IBGateway]\nApiOrderPrecautionsIgnored=true\n" > "$jts_ini"
    echo "yrvi-gw-entrypoint: jts.ini created with ApiOrderPrecautionsIgnored=true"
elif grep -q "^ApiOrderPrecautionsIgnored=" "$jts_ini"; then
    sed -i "s/^ApiOrderPrecautionsIgnored=.*/ApiOrderPrecautionsIgnored=true/" "$jts_ini"
    echo "yrvi-gw-entrypoint: jts.ini ApiOrderPrecautionsIgnored updated to true"
elif grep -q "^\[IBGateway\]" "$jts_ini"; then
    sed -i "/^\[IBGateway\]/a ApiOrderPrecautionsIgnored=true" "$jts_ini"
    echo "yrvi-gw-entrypoint: jts.ini ApiOrderPrecautionsIgnored appended under [IBGateway]"
else
    printf "\n[IBGateway]\nApiOrderPrecautionsIgnored=true\n" >> "$jts_ini"
    echo "yrvi-gw-entrypoint: jts.ini [IBGateway] section + ApiOrderPrecautionsIgnored appended"
fi

# The image's common.sh errors if both TWS_PASSWORD and TWS_PASSWORD_FILE are set.
# Pass the password as TWS_PASSWORD and clear the file path.
unset TWS_PASSWORD_FILE
export TWS_PASSWORD="$password"
export TWS_USERID="$userid"
export VNC_SERVER_PASSWORD="$vnc_password"

echo "yrvi-gw-entrypoint: secrets loaded (mode=${TRADING_MODE}, userid set, password ${#password} chars), starting IB Gateway..."

# ── Run IB Gateway with lockout monitoring ───────────────────────
#
# Case-insensitive match against any of:
#   - "locked out"
#   - "excessive number of failed login attempts"
#   - "PASSWORD NOTICE"
#   - "Login failed"
# On match: send Discord alert, kill the gateway, exit 1.

LOG_FILE="/tmp/yrvi-gw.log"
LOCKOUT_FLAG="/tmp/yrvi-gw.lockout"
rm -f "$LOG_FILE" "$LOCKOUT_FLAG"
: > "$LOG_FILE"

# Start IB Gateway in the background; tee its stdout+stderr to the log file
# while continuing to write to container stdout.
"$@" > >(tee -a "$LOG_FILE") 2> >(tee -a "$LOG_FILE" >&2) &
GW_PID=$!

# Watcher: tails the log, matches the lockout patterns case-insensitively,
# and on hit sends an alert and signals the gateway to exit.
(
    tail -n 0 -F "$LOG_FILE" 2>/dev/null | while IFS= read -r line; do
        lower=$(printf '%s' "$line" | tr '[:upper:]' '[:lower:]')
        case "$lower" in
            *"locked out"*|*"excessive number of failed login attempts"*|*"password notice"*|*"login failed"*)
                echo "yrvi-gw-entrypoint: lockout pattern matched — halting gateway" >&2
                touch "$LOCKOUT_FLAG"
                send_discord_alert "🔴 YRVI: IBKR account locked out — too many failed login attempts. Contact IBKR support to unlock: 1-877-442-2757 or ibkr.com/support. Stack has been stopped to prevent further attempts."
                kill -TERM "$GW_PID" 2>/dev/null || true
                sleep 3
                kill -KILL "$GW_PID" 2>/dev/null || true
                exit 0
                ;;
        esac
    done
) &
WATCHER_PID=$!

# Wait for the gateway to exit, capture its exit code.
EXIT=0
wait "$GW_PID" 2>/dev/null || EXIT=$?

# Tear down the watcher.
kill "$WATCHER_PID" 2>/dev/null || true
wait "$WATCHER_PID" 2>/dev/null || true

if [ -f "$LOCKOUT_FLAG" ]; then
    exit 1
fi

exit "$EXIT"
