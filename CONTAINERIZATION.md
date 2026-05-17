# Containerized YRVI — DEPRECATED

> **This document is from the old `containerized` branch and is no longer accurate.**
>
> It references manual file-based secrets (`docker/secrets/`), the macOS Keychain setup, and an older `.env.compose` format — all of which were replaced by the **secrets container** in v1.3.0.
>
> For current setup instructions, see **README.md** and **MAC_MINI_SETUP.md**. Credentials are now configured through the web UI at `http://localhost:8001` — run `setup_docker.sh --paper` to get started.

---

## Quickstart — New Install (5 Steps) — DEPRECATED

**Step 1 — Install Rancher Desktop**
Download from [rancherdesktop.io](https://rancherdesktop.io) and start it. Enable dockerd (moby) in Preferences → Container Engine. Wait for the status indicator to show "Running."

> ⚠️ **IMPORTANT: Configure Rancher Desktop to auto-start**
>
> Without this, Docker won't be running when the YRVI containers try to start after a reboot.
>
> 1. Open Rancher Desktop
> 2. Click **Preferences**
> 3. Click **Application** in the left sidebar
> 4. Click the **Behavior** tab
> 5. Under **Startup**: check ✅ **Automatically start at login**
> 6. Under **Background**: check ✅ **Start in the background**
> 7. Click **Apply**
>
> **Leave "Quit when closing application window" unchecked** — closing the Rancher Desktop window should not stop Docker.

**Step 2 — Clone the repo**
```bash
git clone https://github.com/controllinghand/you_rock_fund.git you_rock_fund
cd you_rock_fund
git switch containerized
```

**Step 3 — Set up secrets and config**
```bash
cp .env.compose.example .env.compose
mkdir -p docker/secrets
cp docker/secrets.example/* docker/secrets/
chmod 600 docker/secrets/*
```

Edit `.env.compose` and fill in your IBKR paper credentials:
```env
ACCOUNT_PAPER=DUP_YOUR_PAPER_ACCOUNT_ID
TWS_USERID_PAPER=your_paper_login_username
```

Write your passwords into the secret files (one value per file, no quotes):
```
docker/secrets/tws_password_paper
docker/secrets/render_secret
```

**Step 4 — Run setup**
```bash
bash setup_docker.sh
```

This validates your config, builds all 4 containers, starts the stack, and installs a Mac login item so containers restart automatically after a reboot.

**Step 5 — Open the dashboard**
```
http://localhost:3000
```

Wait for IB Gateway to finish logging in (takes 30–90 s). Watch the log:
```bash
docker compose --env-file .env.compose logs -f ib_gateway
```

Look for `Login has completed` in the output. The dashboard API status at `http://localhost:8000/api/status` will show `"ibkr_connected": true` when ready.

> **Note:** `setup_ibc.sh` and the launchd plist files in this repo are for the non-containerized branch only. Docker replaces launchd on this branch — do not run `setup_ibc.sh`.

---

## Windows Installation

### Prerequisites

Install both tools before cloning or running anything.

**1. Rancher Desktop**

Download from [rancherdesktop.io](https://rancherdesktop.io) and run the installer.

After installation:

1. Open Rancher Desktop → **Preferences → Container Engine**.
2. Select **dockerd (moby)** — not containerd. The `docker compose` V2 plugin is only bundled with the moby engine.
3. Click **Apply** and wait for the status indicator to show **Running**.
4. Configure Rancher Desktop to auto-start so containers come back up automatically after a reboot:
   1. Open Rancher Desktop
   2. Click **Preferences**
   3. Click **Application** in the left sidebar
   4. Click the **Behavior** tab
   5. Under **Startup**: check ✅ **Automatically start at login**
   6. Under **Background**: check ✅ **Start in the background**
   7. Click **Apply**

   **Leave "Quit when closing application window" unchecked** — closing the Rancher Desktop window should not stop Docker.

   Without **Automatically start at login** and **Start in the background** both checked, Docker will not be running when Windows starts and the YRVI containers will not come up after a reboot.

**2. Git for Windows**

Download from [git-scm.com/download/win](https://git-scm.com/download/win) and run the installer.

When the installer asks about **Adjusting your PATH environment**, select:

> **Git from the command line and also from 3rd-party software**

This is the critical choice — without it, `git` will not be available in PowerShell. The other installer defaults are fine.

After installation, close and reopen PowerShell before continuing.

### Verify Before You Start

Open PowerShell and run both checks. Both must return a version number before proceeding — if either fails, refer back to the prerequisites above.

```powershell
git --version
docker --version
```

Expected output:

```
git version 2.x.x.windows.x
Docker version 27.x.x, build xxxxxxx
```

### Clone the Repo

> ⚠️ Clone into the Windows filesystem (`C:\Users\...`), not a WSL2 path (`\\wsl$\...`). Docker bind-mounts from WSL2 paths silently fail.

```powershell
cd C:\Users\$env:USERNAME
git clone https://github.com/controllinghand/you_rock_fund.git you_rock_fund
cd you_rock_fund
```

### Run the Setup Script

Open **PowerShell** (not WSL2 bash) in the repo root and run:

```powershell
.\setup_windows.ps1
```

The script runs five steps:

| Step | What it does |
|---|---|
| 1 — Preflight | Verifies PowerShell, docker, docker compose V2, repo root, and warns if running from a WSL2 path |
| 2 — Env file | Copies `.env.compose.example` → `.env.compose` if not present, then opens it in Notepad for you to fill in `ACCOUNT_PAPER` and `TWS_USERID_PAPER` |
| 3 — Secrets | Creates `docker\secrets\` and prompts securely for `tws_password_paper` and `render_secret` |
| 4 — Bind-mount | Optionally copies `docker-compose.override.yml.example` and creates `docker\data\` so runtime files are visible on the Windows filesystem |
| 5 — Containers | Runs `docker compose --env-file .env.compose up -d --build`, shows live output, prints a GO/NO-GO status table, then checks the `dry_run` safety default |

Add `-DryRun` to walk through all steps without running any docker commands:

```powershell
.\setup_windows.ps1 -DryRun
```

### After Setup

Open the dashboard at `http://localhost:3000`. IB Gateway takes 30–90 seconds to log in — watch for `Login has completed` in the log:

```powershell
docker compose --env-file .env.compose logs -f ib_gateway
```

### Runtime Files and Logs

By default, runtime logs and data (state.json, trade_log.txt, scheduler_log.txt, etc.) live inside a Docker named volume and are accessible via `docker compose logs` or `docker compose exec`. The setup script offers to enable a **bind-mount override** that makes those same files visible under `docker\data\` on your Windows filesystem, where you can open them directly in Explorer, Notepad, or VS Code.

To enable it manually (if you skipped the prompt during setup):

```powershell
copy docker-compose.override.yml.example docker-compose.override.yml
mkdir docker\data
docker compose --env-file .env.compose up -d
```

### Safety Default: dry_run

On a fresh data volume, `settings.json` is initialized from `settings_default.json` with `dry_run = true`. This prevents YRVI from submitting any orders to IBKR until you consciously enable trading. The setup script checks this automatically, but you can verify it any time:

```powershell
curl http://127.0.0.1:8000/api/settings
```

Expected output for a safe new install:

```json
{ "dry_run": true, "trading_mode": "paper", "ibkr_port": 4004 }
```

When you are ready to place paper trades — after confirming `ibkr_connected: true` in `/api/status` — disable dry-run:

```powershell
curl -X POST http://127.0.0.1:3000/api/settings `
     -H "Content-Type: application/json" `
     -d '{"dry_run":false}'
```

Do not disable `dry_run` until you have confirmed `trading_mode: paper` and that IB Gateway is connected to the paper account, not a live account.

### VNC on Windows (informational — most users won't need this)

Most first-time IB Gateway logins complete automatically without any VNC interaction. VNC is only needed if IBKR presents a 2FA challenge, a device-authorization dialog, or a credential error during the initial login.

Windows does not have a built-in VNC viewer. If you do need VNC:

1. Install [RealVNC Viewer](https://www.realvnc.com/en/connect/download/viewer/) (free).
2. Set `VNC_SERVER_PASSWORD` in `.env.compose`.
3. Recreate the gateway container:

   ```powershell
   docker compose --env-file .env.compose up -d --force-recreate ib_gateway
   ```

4. Connect RealVNC Viewer to `localhost:5900` using the password from step 2.
5. Complete the IBKR dialog in the VNC session.

The VNC port is bound to `127.0.0.1` only.

### Windows Filesystem Reminder

Always run PowerShell from `C:\Users\...`, not from a WSL2 path. You can verify your location with:

```powershell
(Get-Location).Path
```

It should start with `C:\` (or another Windows drive letter), not `\\wsl$\`.

---

This setup runs YRVI locally with Rancher Desktop or Docker-compatible tooling:

- `ib_gateway`: Interactive Brokers Gateway + IBC from `ghcr.io/gnzsnz/ib-gateway:latest`
- `api`: FastAPI dashboard backend
- `scheduler`: APScheduler trading worker
- `web`: built React dashboard served by nginx

The Python containers connect to IB Gateway over the Compose network. Paper mode uses `ib_gateway:4004`; live mode uses `ib_gateway:4003`.

## Container Roles

The container stack separates the original YRVI processes from the new infrastructure needed to run them under Rancher Desktop or Docker Compose.

| Container | What it runs | Role in the system | Relationship to upstream |
|---|---|---|---|
| `ib_gateway` | `ghcr.io/gnzsnz/ib-gateway:latest`, which packages Interactive Brokers Gateway with IBC | Provides the IBKR API endpoint that YRVI uses for account data and order placement. It logs in to the paper account, exposes paper trading inside the Compose network on `4004`, and optionally exposes VNC on `localhost:5900` for 2FA or manual dialogs. | New containerization infrastructure. The upstream project expected IB Gateway/IBC to run on macOS through launchd; this replaces that host dependency with a container. |
| `api` | `api.py` via `uvicorn` | Serves the dashboard API on `localhost:8000`. It reads state, settings, and performance files, checks IBKR status through `ib_gateway`, runs the screener preview endpoint, and serves dashboard actions that are safe in container mode. | Wraps an upstream component. `api.py` already existed; the container adds environment wiring, Docker secrets, shared data volume access, and container-aware health checks. |
| `scheduler` | `scheduler.py` | Runs the APScheduler jobs for the trading system: Saturday preview, Monday Discord preview, Monday wheel check, Monday CSP execution, Tue-Thu risk monitor, and Friday assignment detection. It writes a heartbeat file so the API can show scheduler health across containers. | Wraps an upstream component. `scheduler.py` already existed; the heartbeat and shared volume behavior are new to support container health reporting. |
| `web` | Built `yrvi-app` React assets served by nginx | Serves the dashboard at `localhost:3000` and proxies `/api/...` requests to the `api` container. The nginx config uses Docker DNS so it can recover when the API container is recreated. | Wraps an upstream component with new infrastructure. `yrvi-app` already existed; nginx static serving and proxying replace local Vite preview for production-like container use. |

Shared infrastructure:

- `yrvi_data` volume: stores runtime files that upstream code writes in the repo directory, such as `state.json`, `settings.json`, `ytd_tracker.json`, log files, and `scheduler_heartbeat.json`.
- `ib_gateway_settings` volume: persists IB Gateway/JTS settings across container restarts.
- `docker/entrypoint-secrets.sh`: new container helper that loads Docker secret files into environment variables and links runtime files into `yrvi_data`.
- `docker/preflight.sh`: new local helper that catches missing placeholders and prevents confusing `ACCOUNT_PAPER` with `TWS_USERID_PAPER`.

## Manual Setup Checklist

You must enter a few values manually before the stack can log in or run the screener.

Use these names carefully:

- `ACCOUNT_PAPER`: the IBKR paper account id, for example `DUP...`.
- `TWS_USERID_PAPER`: the IBKR paper login username. This is not the account id.
- `docker/secrets/tws_password_paper`: the password for `TWS_USERID_PAPER`.
- `docker/secrets/render_secret`: the You Rock Club Render screener API secret.

The stack is currently wired for paper trading only. Live credential fields are present for later, but they are not used while `TRADING_MODE=paper`.

## One-Time Setup

Copy the non-secret Compose config:

```bash
cp .env.compose.example .env.compose
```

Create local secret files. These files are gitignored and must not be committed:

```bash
mkdir -p docker/secrets
cp docker/secrets.example/* docker/secrets/
chmod 600 docker/secrets/*
```

Edit `.env.compose` and set the paper trading values:

```env
TRADING_MODE=paper
ACCOUNT_PAPER=DUP_YOUR_PAPER_ACCOUNT_ID
TWS_USERID_PAPER=your_paper_login_username
IBKR_PORT=4004
YRVI_INIT_DRY_RUN=true
```

If you want to reserve live credentials for later, fill these too. They are not used by the current paper Gateway:

```env
ACCOUNT_LIVE=YOUR_LIVE_IBKR_ACCOUNT_ID
TWS_USERID_LIVE=your_live_ibkr_username
IBKR_USERNAME_LIVE=your_live_ibkr_username
```

Edit the required secret files manually:

- `docker/secrets/tws_password_paper`: put only the IBKR paper password on one line.
- `docker/secrets/render_secret`: put only the Render screener API secret on one line.

Leave these optional files blank unless you need them:

- `docker/secrets/anthropic_api_key`
- `docker/secrets/discord_webhook_url`
- `docker/secrets/discord_webhook_weekly_plan`
- `docker/secrets/tws_password_live`
- `docker/secrets/ibkr_password_live`

Run the preflight check:

```bash
sh docker/preflight.sh
```

The preflight fails if required fields are blank, still placeholders, or if `TWS_USERID_PAPER` accidentally equals `ACCOUNT_PAPER`.

## Run Locally

With Docker Compose:

```bash
sh docker/preflight.sh
docker compose --env-file .env.compose up -d --build
docker compose --env-file .env.compose logs -f ib_gateway
```

With Rancher Desktop using `nerdctl`:

```bash
sh docker/preflight.sh
nerdctl compose --env-file .env.compose up -d --build
nerdctl compose --env-file .env.compose logs -f ib_gateway
```

Wait for the IB Gateway log to show login completion. Useful successful lines include:

```text
Login has completed
Configuration tasks completed
```

Open the dashboard at:

```text
http://localhost:3000
```

The API is available on localhost only:

```text
http://localhost:8000/api/status
```

The status response should eventually show:

```json
{
  "gateway_running": true,
  "scheduler_pid": 1,
  "ibkr_connected": true,
  "account": "YOUR_PAPER_ACCOUNT_ID",
  "trading_mode": "paper"
}
```

## IB Gateway And 2FA

The Compose service is intentionally named `ib_gateway`:

```yaml
image: ghcr.io/gnzsnz/ib-gateway:${IB_GATEWAY_TAG:-latest}
container_name: ib_gateway
```

For first login or 2FA recovery, set a strong temporary `VNC_SERVER_PASSWORD` in `.env.compose`, recreate `ib_gateway`, and connect to `localhost:5900` with a VNC client:
[NOTE: VNC is built into MacOS (Screen Share), most Linux (Desktop-Sharing), but not Windows (try RealVNC or other).]

```bash
docker compose --env-file .env.compose up -d --force-recreate ib_gateway
docker compose --env-file .env.compose logs -f ib_gateway
```

In the VNC session, verify the login username is `TWS_USERID_PAPER`, not `ACCOUNT_PAPER`. Complete any IBKR 2FA prompt or warning dialog.

After recovery, remove `VNC_SERVER_PASSWORD` and recreate the container if you do not need VNC. The VNC port is bound to `127.0.0.1` only.

## Verifying The Dashboard

Check the API directly:

```bash
curl http://127.0.0.1:8000/api/status
```

Check the dashboard proxy:

```bash
curl http://127.0.0.1:3000/api/status
```

Run the screener through the dashboard proxy:

```bash
curl http://127.0.0.1:3000/api/screener
```

If direct API works but `localhost:3000/api/...` returns `502`, restart the web proxy:

```bash
docker compose --env-file .env.compose restart web
```

## Runtime Data

`api` and `scheduler` share the `yrvi_data` volume. The entrypoint symlinks these app files into that volume:

- `state.json`
- `settings.json`
- `ytd_tracker.json`
- `earnings_cache.json`
- `scheduler_heartbeat.json`
- `scheduler_log.txt`
- `trade_log.txt`
- `wheel_log.txt`
- `risk_log.txt`
- service stdout and stderr logs

To use visible local files instead of a named volume, copy:

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
mkdir -p docker/data
```

Then restart:

```bash
docker compose --env-file .env.compose up -d
```

`docker/data/` and `docker/secrets/` are ignored by git.

On a fresh data volume, the entrypoint initializes `settings.json` from `settings_default.json` with `dry_run` forced to `true`. This is a safety default for first container startup. While `dry_run` is `true`, the app can show simulated YRVI positions in `state.json`, but it does not submit orders to IBKR, so Interactive Brokers will not show trade history for those simulated positions.

After validating that the stack is connected to the paper account, disable dry-run if you want IBKR paper orders to be submitted:

```bash
curl -sS -X POST http://127.0.0.1:3000/api/settings \
  -H 'Content-Type: application/json' \
  -d '{"dry_run":false}'
```

Verify the runtime setting:

```bash
curl -sS http://127.0.0.1:3000/api/settings
```

This updates the persisted `settings.json` in `yrvi_data`; it does not change a tracked Git file, so `git status` will not show it. Normal container restarts keep this setting. Deleting the `yrvi_data` volume creates a fresh `settings.json` and resets `dry_run` to `true` unless you change it again.

To regenerate dashboard positions without waiting for the next scheduled Monday run, run the scheduler pipeline manually. With `dry_run=true`, this only writes simulated positions into `state.json`. With `dry_run=false`, it can submit paper orders through IBKR:

```bash
docker compose --env-file .env.compose exec scheduler python - <<'PY'
import scheduler
scheduler.run_pipeline()
PY
```

## Restoring Existing Trade Data

The Docker volume is fresh on a new install and contains no trade history. To carry over an existing `state.json` and `ytd_tracker.json` from the main branch, copy them into the running scheduler container after the stack is up:

```bash
docker compose --env-file .env.compose cp state.json scheduler:/data/
docker compose --env-file .env.compose cp ytd_tracker.json scheduler:/data/
```

The entrypoint symlinks those files from `/data/` into the app directory, so the copy takes effect immediately without a container restart. Verify the data was loaded:

```bash
docker compose --env-file .env.compose exec api python - <<'PY'
import json
from pathlib import Path
s = json.loads(Path("/data/state.json").read_text())
print("positions:", len(s.get("positions", [])))
print("wheel_holdings:", len(s.get("wheel_holdings", [])))
PY
```

## Sync With Upstream

Keep two remotes:

- `upstream`: the original author repo, `https://github.com/controllinghand/you_rock_fund.git`
- `origin`: your fork, for example `https://github.com/dhookom/you_rock_fund.git`

Check remotes:

```bash
git remote -v
```

If needed, add the author repo as `upstream`:

```bash
git remote add upstream https://github.com/controllinghand/you_rock_fund.git
```

Keep containerization changes on this branch and rebase over the author's updates:

```bash
git switch containerized-rancher-desktop
git fetch upstream
git rebase upstream/main
```

If the rebase reports conflicts, resolve the listed files, then continue:

```bash
git add <resolved-files>
git rebase --continue
```

After the rebase succeeds, rebuild and recreate the YRVI app containers so both Python code and dashboard assets are refreshed:

```bash
docker compose --env-file .env.compose build --pull api scheduler web
docker compose --env-file .env.compose up -d --force-recreate api scheduler web
docker compose --env-file .env.compose ps
```

The `ib_gateway` container usually does not need to be recreated for an upstream YRVI code sync unless you changed Gateway credentials, Gateway environment variables, or the IB Gateway image tag.

Validate that the branch is based on the current author repo:

```bash
git log --oneline --decorate --max-count=12
git diff --name-status upstream/main...HEAD
```

Expected result: the container branch is ahead because of Docker/docs/container health changes, but upstream dashboard files under `yrvi-app/src/` should not appear in the diff unless you intentionally changed them on this branch.

Validate the running backend and dashboard proxy:

```bash
curl -sS http://127.0.0.1:8000/api/status
curl -sS http://127.0.0.1:3000/api/status
curl -sS http://127.0.0.1:3000/api/settings
curl -sS http://127.0.0.1:3000/api/positions
```

Validate that the rebuilt frontend is being served by nginx:

```bash
curl -sS http://127.0.0.1:3000/ | sed -n '1,30p'
docker compose --env-file .env.compose exec web ls -l /usr/share/nginx/html/assets
```

The HTML should point at a built `/assets/index-*.js` file, and the asset timestamps should match the rebuild time. If the dashboard layout still appears old, compare the served code check above with the runtime API data. The frontend code can be current while cards and tables still reflect persisted `state.json` from before the sync, or no positions if `state.json` was reset.

Re-check safety-critical runtime settings after every data-volume reset or fresh setup:

```bash
curl -sS http://127.0.0.1:3000/api/settings
```

For easier reading, pretty-print the settings:

```bash
curl -sS http://127.0.0.1:3000/api/settings | python3 -m json.tool
```

If you intend to submit paper trades, verify these exact fields in the response:

```json
{
  "dry_run": false,
  "trading_mode": "paper",
  "ibkr_port": 4004
}
```

Meaning:

- `dry_run: false`: YRVI is allowed to submit orders instead of only writing simulated local results.
- `trading_mode: paper`: the app is configured for paper trading behavior.
- `ibkr_port: 4004`: the Python services connect to the paper IB Gateway port inside the Compose network.

Also confirm the API is connected to the expected paper account:

```bash
curl -sS http://127.0.0.1:3000/api/status | python3 -m json.tool
```

Look for:

```json
{
  "ibkr_connected": true,
  "account": "YOUR_PAPER_ACCOUNT_ID",
  "trading_mode": "paper"
}
```

If `dry_run` is still `true` and you are ready to place paper trades, change only that runtime setting:

```bash
curl -sS -X POST http://127.0.0.1:3000/api/settings \
  -H 'Content-Type: application/json' \
  -d '{"dry_run":false}' | python3 -m json.tool
```

If the data volume was recreated, `dry_run` may be initialized back to `true` for safety.

After a successful rebase, update your fork branch:

```bash
git push --force-with-lease origin containerized-rancher-desktop
```

Do not commit `.env.compose`, `.env`, or `docker/secrets/`; those are local-only files.

If `api` is recreated and the dashboard starts returning `502`, the current nginx config should recover via Docker DNS. If it does not, restart `web`:

```bash
docker compose --env-file .env.compose restart web
```

If you use Rancher Desktop without Docker compatibility:

```bash
git fetch upstream
git rebase upstream/main
nerdctl compose --env-file .env.compose build --pull api scheduler web
nerdctl compose --env-file .env.compose up -d --force-recreate api scheduler web
nerdctl compose --env-file .env.compose ps
```

Because the container layer is mostly additive, upstream conflicts should usually be limited to Docker/docs files or dependency changes.

## Switching To Live Trading

Validate everything in paper mode first.

In containerized mode, switch trading mode from Compose, not from the dashboard button. The dashboard API rejects in-container trading-mode changes so it cannot leave the app and IB Gateway configured for different modes.

The file names for live mode are reserved now, but the Compose stack intentionally keeps `ib_gateway` wired to paper credentials until live trading is enabled deliberately. For a future live cutover:

1. Put the live account in `.env.compose`:

   ```env
   TRADING_MODE=live
   IBKR_PORT=4003
   ACCOUNT_LIVE=YOUR_LIVE_ACCOUNT
   TWS_USERID_LIVE=your_live_username
   IBKR_USERNAME_LIVE=your_live_username
   ```

2. Put the live Gateway password in `docker/secrets/tws_password_live`.
3. Put the API compatibility live password in `docker/secrets/ibkr_password_live` if using the non-container launchd flow.
4. Update the Compose Gateway environment from the paper `TWS_USERID_PAPER` / `tws_password_paper` pair to the live `TWS_USERID_LIVE` / `tws_password_live` pair, then restart the stack:

   ```bash
   docker compose --env-file .env.compose up -d
   ```

## Security Notes

- Keep the IB Gateway, API, dashboard, and VNC port bindings on `127.0.0.1`.
- Do not expose `/api` outside localhost without adding real authentication. The API can change settings, restart local services in the launchd setup, and switch trading mode.
- Keep `READ_ONLY_API=no` only because this app places orders. Use paper trading before live trading.
- Keep `ALLOW_BLIND_TRADING=no` unless you have explicitly reviewed and accepted the image's behavior.
- Pin `IB_GATEWAY_TAG` to a tested tag or digest after paper validation if you want deterministic upgrades.
- Prefer a password manager or SOPS for producing files in `docker/secrets/`; do not commit decrypted secrets.

## Useful Commands

```bash
docker compose --env-file .env.compose ps
docker compose --env-file .env.compose logs -f ib_gateway
docker compose --env-file .env.compose logs -f api
docker compose --env-file .env.compose logs -f scheduler
docker compose --env-file .env.compose restart scheduler
docker compose --env-file .env.compose restart web
docker compose --env-file .env.compose down
```

The dashboard's scheduler restart endpoint is disabled in containerized mode. Use `docker compose restart scheduler` instead.

## Troubleshooting In Containers

Troubleshooting the containerized stack is different from the original macOS launchd setup in a few ways:

- Processes run in separate containers instead of one host environment.
- The dashboard talks to `api` through nginx in the `web` container.
- Python services talk to IB Gateway over the Docker network using `ib_gateway:4004` for paper mode.
- Runtime files are persisted through Docker volumes instead of being written directly into the working tree.
- Anything changed manually inside a running container is temporary unless it is written to a mounted volume.

Start with service status:

```bash
docker compose --env-file .env.compose ps
```

Read logs from the container that owns the failing behavior:

```bash
docker compose --env-file .env.compose logs -f ib_gateway
docker compose --env-file .env.compose logs -f api
docker compose --env-file .env.compose logs -f scheduler
docker compose --env-file .env.compose logs -f web
```

Check the API directly, bypassing the dashboard proxy:

```bash
curl http://127.0.0.1:8000/api/status
```

Check the same API through the dashboard proxy:

```bash
curl http://127.0.0.1:3000/api/status
```

If `localhost:8000/api/status` works but `localhost:3000/api/status` fails, the issue is likely in the `web` nginx proxy. Restart it:

```bash
docker compose --env-file .env.compose restart web
```

If `ibkr_connected` is false, check `ib_gateway` logs and use VNC if needed:
[NOTE: VNC is built into MacOS (Screen Share), most Linux (Desktop-Sharing), but not Windows (try RealVNC or other).]

```bash
docker compose --env-file .env.compose logs -f ib_gateway
```

Set `VNC_SERVER_PASSWORD` in `.env.compose`, recreate `ib_gateway`, and connect to `localhost:5900` if IBKR needs 2FA, a warning confirmation, or credential correction.

If IB Gateway shows a growing number of API client tabs, check the API logs:

```bash
docker compose --env-file .env.compose logs --since=10m api
```

The dashboard API should use a stable dashboard read client, `clientId=101`, and cache IBKR reads for about 30 seconds. A few clients from trading jobs are expected, but the dashboard should not create a new random client ID on every poll. If the client list keeps growing, restart the Python services first:

```bash
docker compose --env-file .env.compose restart api scheduler
```

Avoid restarting `ib_gateway` unless the Gateway itself is unhealthy, because that can trigger a new IBKR login or 2FA prompt.

If dashboard positions disappear after resetting data, check whether `state.json` exists in the shared runtime volume:

```bash
docker compose --env-file .env.compose exec api ls -l /data
docker compose --env-file .env.compose exec api python - <<'PY'
import json
from pathlib import Path
p = Path("/data/state.json")
print("state.json exists:", p.exists())
if p.exists():
    state = json.loads(p.read_text())
    print("positions:", len(state.get("positions", [])))
    print("executions:", len(state.get("executions", [])))
PY
```

The dashboard's CSP position cards come from `state.json`. The live IBKR holdings table comes from the IBKR API. If `state.json` is missing, regenerate it with the manual scheduler pipeline shown in the Runtime Data section.

If Interactive Brokers shows no trade history, first check the runtime settings:

```bash
curl -sS http://127.0.0.1:3000/api/settings
```

When `dry_run` is `true`, YRVI records simulated execution results locally but does not place IBKR orders. Broker-visible paper trades require `dry_run=false`, `trading_mode=paper`, `IBKR_PORT=4004`, a connected paper Gateway, and either the scheduled Monday execution or a manual pipeline run.

If the dashboard "looks old" after syncing upstream, distinguish code from data:

```bash
curl -sS http://127.0.0.1:3000/ | sed -n '1,30p'
curl -sS http://127.0.0.1:3000/api/positions
```

The first command shows which built dashboard asset is being served. The second shows the runtime data driving the cards and tables. Rebuilt frontend code can be current while persisted `state.json` still contains older results, or while `state.json` is empty after a volume reset.

### Persisted Data

Persistence is handled with Docker volumes and startup symlinks:

- `yrvi_data`: shared by `api` and `scheduler`.
- `ib_gateway_settings`: used by `ib_gateway` to keep IB Gateway/JTS settings.
- `docker/entrypoint-secrets.sh`: links runtime files from `/app` into `/data` so upstream code can keep using the same relative filenames.

These files are persisted in `yrvi_data`:

- `state.json`
- `settings.json`
- `ytd_tracker.json`
- `earnings_cache.json`
- `scheduler_heartbeat.json`
- `scheduler_log.txt`
- `trade_log.txt`
- `wheel_log.txt`
- `risk_log.txt`
- `scheduler_stdout.log`
- `scheduler_stderr.log`
- `api_stdout.log`
- `api_stderr.log`

To make persisted files visible on the Mac filesystem, enable the optional bind-mount override:

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
mkdir -p docker/data
docker compose --env-file .env.compose up -d
```

After that, runtime state and logs are visible under `docker/data/`.

### Ephemeral Concerns

The following are ephemeral and can disappear on rebuilds or container removal:

- Files written inside containers outside `/data` or `/home/ibgateway/Jts`.
- Manual edits made by shelling into a container.
- Some `docker compose logs` history after containers are removed and recreated.
- Python package installs done interactively inside a container.
- Code changes inside a container image after it was built.

Make code changes in the repo, then rebuild the affected containers:

```bash
docker compose --env-file .env.compose up -d --build api scheduler web
```

Do not debug by editing files inside containers unless the change is intentionally temporary. Put durable changes in the repository or in the persisted `docker/data/` / Docker volume paths.
