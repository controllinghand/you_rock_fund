# View Gateway — browser-accessible, view-only window into the IB Gateway GUI.
#
# The IB Gateway container (gnzsnz/ib-gateway) already runs the Gateway GUI inside
# Xvfb with an x11vnc server on port 5900. This image adds nothing to the Gateway —
# it only bridges that existing VNC display to the browser with noVNC + websockify,
# so you can *see* whether the Gateway is logged in, waiting for 2FA, showing an
# error dialog, or otherwise stuck. It never touches trading logic or credentials.
FROM debian:bookworm-slim

# novnc     → static web client, served from /usr/share/novnc
# websockify → TCP(VNC) <-> WebSocket bridge that also serves the noVNC web root
# curl       → container healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
        novnc \
        websockify \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Landing page (index.html) offers view-only by default; control is a deliberate,
# confirm-gated link. view.html is a thin noVNC client that auto-fills the VNC
# password (written to vnc-config.js at startup) so the operator isn't prompted.
COPY index.html /usr/share/novnc/index.html
COPY view.html /usr/share/novnc/view.html
COPY entrypoint.sh /usr/local/bin/view-gateway-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/view-gateway-entrypoint.sh \
    && chmod +x /usr/local/bin/view-gateway-entrypoint.sh

# Internal listen port for websockify (host-mapped to VIEW_GATEWAY_PORT in compose).
ENV VIEW_GATEWAY_INTERNAL_PORT=6080 \
    GATEWAY_VNC_HOST=ib_gateway \
    GATEWAY_VNC_PORT=5900

EXPOSE 6080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS "http://localhost:${VIEW_GATEWAY_INTERNAL_PORT}/vnc.html" >/dev/null || exit 1

ENTRYPOINT ["/usr/local/bin/view-gateway-entrypoint.sh"]
