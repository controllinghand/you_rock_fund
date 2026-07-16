# View Gateway — browser window into the IB Gateway GUI

A small, **local-only, view-only** viewer for the IB Gateway screen. Use it to see
at a glance whether IB Gateway is:

- logged in and connected,
- waiting for a **login** / **2FA** prompt,
- showing an **error dialog** or a "session in use" notice,
- or otherwise **stuck** on some GUI state you'd normally never see in a headless container.

It is **not** a general-purpose remote-desktop tool. There is no device discovery,
no cloud, no remote access — just this one YRVI / IB Gateway setup, on this machine.

---

## How it works

IB Gateway already runs inside the `gnzsnz/ib-gateway` container in a virtual X
display (Xvfb) with an **x11vnc** server on port `5900`. The View Gateway adds one
tiny container, `view-gateway`, that runs **noVNC + websockify** and bridges that
existing VNC display to your browser:

```
browser ──HTTP/WebSocket──▶ view-gateway (noVNC + websockify) ──VNC──▶ ib_gateway:5900
        http://localhost:6080                (Docker network only)      (Xvfb + x11vnc)
```

Nothing about trading, credentials, or the Gateway itself changes. The bridge only
*reads* the screen the Gateway already renders.

**Security defaults:**

- **Local-only** — the host port is bound to `127.0.0.1:6080`, exactly like every
  other YRVI service. It is never exposed on your LAN or the internet.
- **View-only by default** — the default URL opens noVNC with `view_only=true`, so
  your clicks and keystrokes are *not* sent to the Gateway. Enabling control is a
  separate, clearly-labeled, confirm-gated action (see below).
- **The VNC password stays out of the viewer** — the browser prompts you for it on
  connect. The `view-gateway` container never reads the `vnc_server_password`
  secret and it never appears in a URL.
- **Opt-in** — it only runs when you ask for the `viewer` profile, so the normal
  stack is unchanged and there's no extra always-on surface.

---

## Start

**Easiest — from the dashboard:** open **Help → System Diagnostics → View Gateway**.
That calls `POST /api/view-gateway/start`, which brings the viewer up on demand
(building it the first time) and opens it in a new browser tab. Nothing to type.

**From the command line** — bring up (or leave up) the normal stack, then add the viewer:

```bash
cd /path/to/you_rock_fund

# Normal stack (unchanged)
docker compose --env-file .env.compose up -d

# Add the View Gateway (builds it the first time)
docker compose --env-file .env.compose --profile viewer up -d --build view-gateway
```

## Access

Open in a browser on **this machine**:

```
http://localhost:6080
```

You'll see a small landing page with two choices:

- **👁 Open viewer (view-only)** — the default. Watch the Gateway; input is disabled.
- **⚠️ Enable keyboard / mouse control** — deliberate, confirm-gated. Only use this
  to actually click through a login or 2FA prompt. Clicks and typing go to the
  **live** Gateway.

**The VNC password auto-fills** — you won't be prompted. At startup the container
fetches the `vnc_server_password` secret (the same one IB Gateway uses, managed in
the secrets UI at `http://localhost:8001`; default `ibgateway123!test` if unset)
and writes it into a same-origin `vnc-config.js` that the viewer reads. The password
is served only on loopback and never appears in the URL, browser history, or
websockify's request logs. If the stored password is wrong/missing, the viewer
falls back to a manual prompt rather than dead-ending.

> Direct URLs, if you prefer to skip the landing page:
> - View-only: `http://localhost:6080/view.html`
> - Control:   `http://localhost:6080/view.html?control=1`
>
> The stock noVNC UI is still available at `http://localhost:6080/vnc.html` (it will
> prompt for the password), but `view.html` is the auto-filling client.

## Stop

```bash
# Stop just the viewer (leaves the trading stack running)
docker compose --env-file .env.compose --profile viewer stop view-gateway

# Or remove it entirely
docker compose --env-file .env.compose --profile viewer rm -sf view-gateway
```

Because it's behind the `viewer` profile, a plain `docker compose ... up -d` or
`down` for the normal stack won't start it — and a normal `up -d` won't stop an
already-running viewer either.

---

## Verify it's working

1. **Container is healthy:**
   ```bash
   docker compose --env-file .env.compose --profile viewer ps view-gateway
   ```
   Status should show `Up ... (healthy)`.

2. **Web root is served** (this is also the healthcheck):
   ```bash
   curl -fsS http://localhost:6080/vnc.html >/dev/null && echo "noVNC OK"
   ```

3. **The bridge reaches the Gateway VNC port** (from inside the viewer container):
   ```bash
   docker compose --env-file .env.compose --profile viewer exec view-gateway \
     bash -c 'curl -sf --max-time 3 telnet://ib_gateway:5900 >/dev/null; echo exit=$?'
   ```
   (A quick alternative: open `http://localhost:6080`, choose view-only, enter the
   VNC password — you should see the Gateway window.)

4. **Read the screen.** What you're looking for:
   | You see… | Meaning |
   |---|---|
   | The Gateway main window with green "connected" status | Logged in and running |
   | A login form / "Second Factor Authentication" prompt | Waiting for 2FA — use **control** mode to complete it |
   | A red error or "existing session" dialog | Stuck — needs attention |
   | Blank / gray screen | Gateway not up yet, or between restarts |

---

## Troubleshooting

- **noVNC says "Failed to connect" / "Server disconnected":** the Gateway container
  may not be up yet, or x11vnc hasn't started. Check:
  ```bash
  docker compose --env-file .env.compose ps ib_gateway
  docker compose --env-file .env.compose logs --tail=50 ib_gateway
  ```
- **Password rejected / prompted anyway:** the viewer auto-fills the
  `vnc_server_password` secret; if it was changed in the secrets UI after the viewer
  started, restart the viewer so it re-reads it
  (`docker compose --env-file .env.compose --profile viewer up -d --force-recreate view-gateway`).
  The VNC password is *not* your IBKR password. Default is `ibgateway123!test`.
- **Port 6080 in use:** change `VIEW_GATEWAY_PORT` in `.env.compose` and re-run the
  `--profile viewer up -d` command.
- **Can't reach it from another machine:** that's intentional — it's bound to
  `127.0.0.1`. Use an SSH tunnel if you truly need remote access
  (`ssh -L 6080:localhost:6080 user@host`), which keeps it off the network.

---

## Files

| File | Purpose |
|---|---|
| `docker/view-gateway/Dockerfile` | Builds the noVNC + websockify bridge image |
| `docker/view-gateway/entrypoint.sh` | Fetches the VNC secret → `vnc-config.js`, then launches websockify → `ib_gateway:5900` |
| `docker/view-gateway/index.html` | View-only-by-default landing page |
| `docker/view-gateway/view.html` | Thin noVNC client that auto-fills the password (no prompt) |
| `docker-compose.yml` (`view-gateway` service) | Opt-in `viewer` profile, `127.0.0.1:6080` |
| `.env.compose` (`VIEW_GATEWAY_PORT`) | Host port for the viewer |
