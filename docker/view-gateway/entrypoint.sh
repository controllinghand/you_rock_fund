#!/bin/bash
# View Gateway entrypoint — bridge the IB Gateway VNC display to the browser.
#
# websockify serves the noVNC web client (from /usr/share/novnc) AND proxies the
# browser's WebSocket to the IB Gateway container's raw VNC port.
#
# For convenience we auto-fill the VNC password: at startup we fetch the same
# `vnc_server_password` secret the IB Gateway uses (from the secrets container) and
# write it into a same-origin `vnc-config.js`, which view.html reads to connect
# without prompting. The password is served only on loopback and lives in an
# ephemeral container file — it is never put in a URL, browser history, or
# websockify's request logs. Falls back to the shipped default and, failing that,
# to a manual prompt in the browser.
set -eu

LISTEN_PORT="${VIEW_GATEWAY_INTERNAL_PORT:-6080}"
VNC_HOST="${GATEWAY_VNC_HOST:-ib_gateway}"
VNC_PORT="${GATEWAY_VNC_PORT:-5900}"
SECRETS_BASE="${SECRETS_BASE:-http://secrets:8001/secret}"
VNC_DEFAULT="ibgateway123!test"      # matches the IB Gateway entrypoint default
WEB_ROOT="/usr/share/novnc"

# Fetch the VNC password from the secrets container (best-effort, short retry).
# Mirrors the IB Gateway entrypoint's parser: pulls "value" out of the JSON body.
fetch_vnc_password() {
    attempt=0
    while [ "$attempt" -lt 5 ]; do
        attempt=$((attempt + 1))
        body=$(curl -sf --max-time 3 "${SECRETS_BASE}/vnc_server_password" 2>/dev/null || true)
        if [ -n "$body" ]; then
            value=$(printf '%s' "$body" | sed -n 's/.*"value"[[:space:]]*:[[:space:]]*"\(.*\)".*/\1/p')
            if [ -n "$value" ]; then printf '%s' "$value"; return 0; fi
        fi
        sleep 2
    done
    return 0
}

vnc_password=$(fetch_vnc_password)
if [ -n "$vnc_password" ]; then
    echo "view-gateway: fetched vnc_server_password from secrets (${#vnc_password} chars)"
else
    vnc_password="$VNC_DEFAULT"
    echo "view-gateway: vnc_server_password not available from secrets — using shipped default"
fi

# Write the same-origin config view.html reads. JS-escape backslashes and quotes.
esc=$(printf '%s' "$vnc_password" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
printf 'window.__VNC_CONFIG__ = { password: "%s" };\n' "$esc" > "${WEB_ROOT}/vnc-config.js"
echo "view-gateway: wrote ${WEB_ROOT}/vnc-config.js (auto-fill enabled)"

echo "view-gateway: serving noVNC on 0.0.0.0:${LISTEN_PORT}, bridging to ${VNC_HOST}:${VNC_PORT} (view-only by default)"

# --web serves the noVNC static client; the positional args are listen -> target.
exec websockify --web="${WEB_ROOT}" "0.0.0.0:${LISTEN_PORT}" "${VNC_HOST}:${VNC_PORT}"
