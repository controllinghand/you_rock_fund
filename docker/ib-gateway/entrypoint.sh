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
#   3. ReloginAfterSecondFactorAuthenticationTimeout=yes — an unanswered IB Key push
#      must leave the gateway retrying, never parked. On 2026-07-17 a push arrived at
#      10:35 PM, went untapped, and IBC neither retried nor exited: the gateway sat
#      dead for 26 hours, taking out a Saturday assignment-detection run.
#      Written as a literal rather than left to ${RELOGIN_AFTER_TWOFA_TIMEOUT},
#      because every box installed before this fix has RELOGIN_AFTER_TWOFA_TIMEOUT=no
#      pinned in its own .env.compose — a compose default alone would reach only fresh
#      installs and silently skip every existing box, including live and the friends'.
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
    set_kv ReloginAfterSecondFactorAuthenticationTimeout yes
    echo "yrvi-gw-entrypoint: patched IBC template $tmpl (CommandServerPort=7462 loopback, LoginFailed=terminate, relogin-after-2FA-timeout=yes)"
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

# Self-heal the Java runtime pointer for the running TWS version.
#
# IBC locates the JRE by reading install4j's pref_jre.cfg (preferred), falling back to
# inst_jre.cfg, from the version's .install4j dir. Some image bakes ship a version dir
# with NO pref_jre.cfg and an inst_jre.cfg that still points at the install-time temp dir
# (/tmp/setup/ibgateway-<vrsn>-...sh.NNNN.dir/jre) — which does not exist at runtime. IBC
# then dies with "Can't find suitable Java installation" even though the bundled JRE sits
# right beside the jars at <verdir>/jre. This is the JRE-pointer cousin of the jars shadow
# handled by self_heal_tws_version above, and it broke the 10.48.1c upgrade (10.48.1b had a
# valid pref_jre.cfg; 10.48.1c shipped without one and fell back to the dead temp path).
#
# Fix: whenever the current JRE pointer is missing or dead, repoint pref_jre.cfg at the
# in-tree bundled JRE (<verdir>/jre) — which lives in the volume right next to the jars, so
# it is always present and valid. Idempotent: a pointer that already resolves to a working
# java is left untouched.
self_heal_jre_pointer() {
    tws_path="${TWS_PATH:-/home/ibgateway/Jts}"
    vrsn="${TWS_MAJOR_VRSN:-}"

    if [ -z "$vrsn" ]; then
        echo "yrvi-gw-entrypoint: TWS_MAJOR_VRSN unset — skipping JRE-pointer self-heal" >&2
        return 0
    fi

    # Find the version dir that actually holds the jars (handles nested + flat layout).
    verdir=""
    for d in $(find "$tws_path" -maxdepth 4 -type d -path "*/$vrsn/jars" 2>/dev/null); do
        verdir=$(dirname "$d"); break
    done
    if [ -z "$verdir" ]; then
        echo "yrvi-gw-entrypoint: TWS $vrsn install dir not found — skipping JRE-pointer self-heal" >&2
        return 0
    fi

    bundled_jre="$verdir/jre"
    if [ ! -x "$bundled_jre/bin/java" ]; then
        echo "yrvi-gw-entrypoint: no bundled JRE at $bundled_jre — skipping JRE-pointer self-heal" >&2
        return 0
    fi

    i4j="$verdir/.install4j"
    mkdir -p "$i4j"
    pref="$i4j/pref_jre.cfg"

    # If pref_jre.cfg already resolves to a working java, leave it alone.
    if [ -f "$pref" ]; then
        cur=$(tr -d '\n\r' < "$pref" 2>/dev/null || true)
        if [ -n "$cur" ] && [ -x "$cur/bin/java" ]; then
            return 0
        fi
    fi

    printf '%s' "$bundled_jre" > "$pref"
    echo "yrvi-gw-entrypoint: JRE-pointer self-heal — set pref_jre.cfg -> $bundled_jre"
    send_discord_alert "🔄 YRVI: IB Gateway self-healed its Java pointer after upgrade — the new TWS version shipped without a JRE path, so it was repointed to the bundled JRE. Starting normally."
    return 0
}

# Keep x11vnc alive so the built-in View Gateway viewer can reach the display.
#
# The image starts x11vnc exactly once (run.sh start_vnc), immediately after
# waiting for the X socket — but common.sh's wait_x_socket only checks that the
# socket FILE exists, and Xvfb creates that file a moment before it accepts
# connections. An x11vnc that loses the race dies on the spot ("unable to open
# the X DISPLAY: :1") and nothing ever retries it, so port 5900 stays dead for
# the life of the container while the gateway logs in and trades normally. That
# is the confusing case where the dashboard says the gateway is UP and the viewer
# says it is down — both are right. Seen on the paper box 2026-07-15; a restart
# 12 hours earlier won the same race, so it is luck, not configuration.
#
# run.sh is image code we can't patch, so supervise from here instead: relaunch
# x11vnc whenever it is missing while Xvfb is up. The image ships no X probe tool
# (no xdpyinfo/xset), but none is needed — x11vnc exits immediately if the display
# isn't ready, so a relaunch that fails simply retries on the next tick.
#
# Viewer-only, so this deliberately never sends a Discord alert: it has no trading
# impact, and a cosmetic alert on gateway starts would just be noise.
vnc_watcher() {
    vnc_pw="$1"
    gw_pid="$2"
    poll="${VNC_WATCH_INTERVAL:-15}"

    # No password → the image disables VNC entirely; nothing to supervise.
    if [ -z "$vnc_pw" ]; then
        return 0
    fi

    heals=0
    while kill -0 "$gw_pid" 2>/dev/null; do
        sleep "$poll"

        # Xvfb down means the gateway is still starting or is shutting down
        # (stop_ibc kills x11vnc, then Xvfb) — either way, don't touch the display.
        pgrep -x Xvfb   >/dev/null 2>&1 || continue
        # Already serving? Nothing to do.
        pgrep -x x11vnc >/dev/null 2>&1 && continue

        # DISPLAY is exported by run.sh in its own process, not inherited here;
        # :1 is the display run.sh always creates. -rfbport pins 5900 rather than
        # letting x11vnc drift to 5901 on a bind clash, since 5900 is the only
        # port the viewer knows.
        x11vnc -ncache_cr -display :1 -forever -shared -bg -noipv6 \
               -rfbport 5900 -passwd "$vnc_pw" >/dev/null 2>&1 || true
        sleep 1
        if pgrep -x x11vnc >/dev/null 2>&1; then
            heals=$((heals + 1))
            echo "yrvi-gw-entrypoint: VNC self-heal — x11vnc was not running; relaunched it on :1 (heal #${heals})"
        fi
    done
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

# Repoint the JRE if this version shipped without a valid pref_jre.cfg (otherwise IBC
# exits with "Can't find suitable Java installation" — what broke the 10.48.1c upgrade).
self_heal_jre_pointer

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

# run.sh passes --on2fatimeout=$TWOFA_TIMEOUT_ACTION to IBC, and boxes installed
# before the relogin fix still pin 'exit' in their own .env.compose. Leaving that
# would contradict the ini setting patched above, and 'exit' is the worse branch
# regardless: this service is `restart: "no"` (deliberately, so a credential
# failure can't loop into an IBKR lockout), so an exiting gateway never comes back
# on its own. Force the retry branch to agree with the template.
if [ "${TWOFA_TIMEOUT_ACTION:-}" != "restart" ]; then
    echo "yrvi-gw-entrypoint: TWOFA_TIMEOUT_ACTION='${TWOFA_TIMEOUT_ACTION:-unset}' overridden to 'restart' (an exiting gateway cannot self-recover under restart:\"no\")"
    export TWOFA_TIMEOUT_ACTION=restart
fi

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

# Supervise x11vnc for the viewer (see vnc_watcher above). Exits on its own when
# the gateway does; never blocks or fails startup.
vnc_watcher "$vnc_password" "$GW_PID" &
VNC_WATCHER_PID=$!

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

# Tear down the watchers.
kill "$WATCHER_PID" 2>/dev/null || true
wait "$WATCHER_PID" 2>/dev/null || true
kill "$VNC_WATCHER_PID" 2>/dev/null || true
wait "$VNC_WATCHER_PID" 2>/dev/null || true

if [ -f "$LOCKOUT_FLAG" ]; then
    exit 1
fi

exit "$EXIT"
