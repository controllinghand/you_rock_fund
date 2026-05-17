# You Rock Volatility Income Fund (YRVI)

![Version](https://img.shields.io/badge/version-1.5.0-blue)

An automated Python algorithmic options trading system that generates weekly income through the complete wheel strategy — selling cash-secured puts (CSPs), managing assignments with covered calls (CCs), and enforcing automatic stop losses — all running 24/7 on a Mac Mini with zero manual intervention.

## How It Works

**Full weekly schedule:**

| Day | Time (PST) | Action |
|-----|-----------|--------|
| Saturday | 6:00 PM | Screener preview — logs top 5 targets, no trades |
| Monday | 9:50 AM | Discord preview — posts this week's sized positions |
| Monday | 9:55 AM | Wheel check — stop losses + sell covered calls on assigned stocks |
| Monday | 10:00 AM | CSP execution — screen → size → execute 5 positions |
| Tuesday–Thursday | 9:00 AM | Daily risk monitor — checks stop loss thresholds, logs P&L |
| Friday | 4:15 PM | Assignment detection — checks for newly assigned positions |

**Pipeline:**

1. **Screener** — fetches candidates from the Render API, applies hard filters (delta ≤ 0.21, buffer ≥ 5%, DTE ≥ 3), and scores survivors by buffer + premium + IV

2. **Position Sizer** — allocates $250K across up to 5 positions (~$50K each, last position gets remainder up to $70K max)

3. **Wheel Manager** — Monday 9:55 AM (four-step logic per holding):
   - **Step 1 — Screener check:** if ticker no longer passes screener filters → sell shares at market and free capital
   - **Step 2 — Option chain:** query IBKR for CALL options on the nearest Friday, strike ≥ assigned strike; collect delta for each candidate
   - **Step 3 — Decision:** sell the highest-delta (≥ 0.20) covered call found. If no strike with delta ≥ 0.20 exists → sell shares at market
   - **Step 4 — Accounting:** freed capital (from any sales) returns to the CSP pool; sold tickers are skipped in that week's screener

4. **Trader** — connects to IBKR, qualifies contracts, checks liquidity (spread ≤ 20%, OI ≥ 100), executes with limit-mid → limit-bid → market escalation; automatically replaces failed positions with the next ranked ticker

5. **Risk Manager** — daily Tue–Thu monitor checks all wheel holdings against the 10% stop loss threshold, logs unrealized P&L, and sends alerts

6. **Discord Poster** — (optional) posts weekly results, YTD stats, Monday previews, and Friday assignment alerts to a Discord channel automatically

## Strategy Overview

### The Wheel Strategy

```
Week 1:  Sell CSP on TICKER at $50 strike → collect $500 premium
           ↓ expires worthless → keep $500, repeat
           ↓ or assigned 100 shares at $50
Week 2:  Sell CC on 100 shares at $50 strike → collect $300 premium
           ↓ expires worthless → keep $300, repeat
           ↓ or shares called away at $50 → back to selling CSPs
```

Each cycle generates income whether the option expires or gets exercised.

### Risk Management

| Control | Rule |
|---------|------|
| Delta filter | Only sell puts with delta ≤ 0.21 (~20Δ) |
| Buffer requirement | Strike must be ≥ 5% below current price |
| Liquidity check | Spread ≤ 20%, Open Interest ≥ 100 |
| Screener exit | Sell shares if ticker drops from screener filters |
| Delta exit | Sell shares if no CC strike with delta ≥ 0.20 available |
| Earnings protection | Skip tickers with earnings within 7 days |
| Auto-replacement | Failed position → automatically try next ranked ticker |

## Getting Started

New to IBKR? See the **[IBKR Account Setup Guide](IBKR_SETUP_GUIDE.md)** for a complete walkthrough — from creating your account to paper trading your first week.

> **You Rock Club members:** If you intend to trade live on IBKR, you will need a Mac Mini (or any always-on Mac). Paper trading can be done on any Mac. Windows is supported for paper trading only.

> 📖 **You Rock Club Mac Mini Setup Guide:** See [MAC_MINI_SETUP.md](./MAC_MINI_SETUP.md) for a complete step-by-step walkthrough from unboxing to first trade.

> ❓ **Troubleshooting:** See [FAQ.md](./FAQ.md) for common setup issues and fixes.

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (recommended for Mac) or [Rancher Desktop](https://rancherdesktop.io) (alternative)
- Access to the You Rock Club screener API (Render)

### Hardware Requirements

| Mode | Hardware | OS Required | Secrets Method |
|------|----------|-------------|----------------|
| Paper trading | Any Mac (Intel or Apple Silicon) | macOS | Encrypted secrets container (AES-256-GCM) |
| Live trading | Mac Mini (recommended) | macOS only | Encrypted secrets container (AES-256-GCM) |

> **Cross-platform**: v1.3.0 removed the macOS-only requirement that came with Keychain integration. The stack now runs on macOS, Linux, and Windows. Mac Mini is recommended for live trading purely for the always-on hardware profile and IB Gateway stability — see `setup_windows.ps1` for Windows-specific notes.

### IB Gateway port reference

| Mode | Port (inside Docker network) |
|---|---|
| Paper trading | 4004 |
| Live trading | 4003 |

These are the ports the Python containers use to reach IB Gateway inside the Compose network. They differ from the standalone IB Gateway defaults (4002/4001) used in the legacy launchd setup.

## Installation

```bash
git clone https://github.com/controllinghand/you_rock_fund.git you_rock_fund
cd you_rock_fund
```

#### Configure `.env.compose`

```bash
cp .env.compose.example .env.compose
```

`.env.compose` only contains non-secret settings (ports, trading mode, timezone) — no editing required for a default paper-trading setup. Leave `TRADING_MODE=paper` and `YRVI_INIT_DRY_RUN=true` for the safe defaults.

#### About Secrets

Account credentials and passwords are stored encrypted (AES-256-GCM) in a persistent volume managed by the `secrets` container. When you run `setup_docker.sh`, it starts the secrets container and opens `http://localhost:8001` in your browser to collect:

**Account Info (required for paper, optional fields for live)**

| Field | What to enter |
|---|---|
| IBKR Paper Account ID | Your IBKR paper account ID (e.g. `DU1234567`) |
| IBKR Paper Username | Your IBKR paper username |
| IBKR Live Account ID | (optional) Your IBKR live account ID — only needed for live trading |
| IBKR Live Username | (optional) Your IBKR live username |
| VNC Password | (optional) defaults to `ibgateway123!test` if not set |

**Passwords & API keys**

- **Required:** IBKR paper password, IBKR live password, Render screener API secret
- **Optional:** Discord webhook URL, Discord weekly-plan webhook URL

If the browser flow times out (5 minutes), the script falls back to terminal prompts. You can update any secret later by visiting `http://localhost:8001` directly or via the **Secrets** page in the dashboard.

> **Note:** The `docker/secrets/` directory holds empty placeholder files for the file-based fallback path; it's git-ignored and the real values never live there.

#### Disable macOS Screen Sharing

IB Gateway uses port 5900 for VNC (required for 2FA). macOS Screen Sharing also uses port 5900 and will cause `docker compose up` to fail with a "address already in use" error.

Before running setup, go to **System Settings → General → Sharing → Screen Sharing → toggle OFF**.

> **Note:** Use SSH for remote terminal access to the Mac Mini instead — Screen Sharing cannot run alongside YRVI.

#### macOS Setup (Paper)

```bash
./setup_docker.sh --paper
```

On first run, the script opens `http://localhost:8001` in your browser where you'll enter:
- Your IBKR paper account password
- Your IBKR live account password (re-use the paper password if you don't trade live)
- Your Render screener API secret
- Discord webhooks (optional)

If the browser flow times out, you'll be prompted in the terminal. On subsequent runs, the script detects existing secrets and skips this step.

#### macOS Setup (Live)

```bash
./setup_docker.sh --live
```

Requires a Mac Mini or equivalent always-on hardware. Live and paper credentials are stored separately in the secrets container under `tws_password_live` and `tws_password_paper` — they're never shared between modes.

#### Verifying secrets

Open `http://localhost:8001` in your browser, or visit the **Secrets** page in the dashboard at `http://localhost:3000/secrets`. Each secret shows ✅ Configured or ⚠️ Missing.

#### Rotating / updating a secret

To change a stored secret (e.g. after rotating your IBKR password):
1. Open `http://localhost:8001` (or visit **Secrets** in the dashboard)
2. Click **Update** next to the secret
3. Enter the new value and click **Save**

The change takes effect immediately for new connections; restart `ib_gateway` to apply a new IBKR password.

`setup_docker.sh` validates your config, builds all five containers (`secrets`, `ib_gateway`, `api`, `scheduler`, `web`), starts the stack, and installs a login item so containers restart automatically after a reboot. It also registers the `yrvi://` URL scheme so the **Upgrade** button in the dashboard can open Terminal and rebuild automatically.

> **Existing installs (pre-v1.6.0):** If you ran setup before v1.6.0, register the upgrade URL scheme once manually:
> ```bash
> bash scripts/yrvi-register-url-scheme.sh
> ```

See **[CONTAINERIZATION.md](CONTAINERIZATION.md)** for the full setup guide — credentials, 2FA recovery, and troubleshooting.

## Security

### Secret Security

- Secrets are stored AES-256-GCM-encrypted in a persistent Docker volume managed by the `secrets` container. The encryption key is generated on first run and stored at `/data/secrets.key` inside the volume (chmod 600).
- The secrets API (`http://localhost:8001`) is bound to `localhost` on the host and reachable inside the Docker network as `http://secrets:8001` — never exposed externally.
- `docker/secrets/` files are empty placeholders kept for the `secrets_client` 3-tier fallback (HTTP → file → env). The real values live only in the encrypted volume.
- `docker/secrets/` is in `.gitignore` and must never be committed.

### What the Encryption Does and Doesn't Protect

The AES-256-GCM encryption protects against **data-at-rest leaks** — if a volume backup or snapshot were exposed, the credentials inside would be unreadable without the key.

**It does not protect against host-level access.** Because the encryption key lives in the same Docker volume as the encrypted data, anyone who can run `docker exec` on the host — or read the volume files directly — can access both the key and the secrets. The real security perimeter is:

1. **Who can log into the Mac Mini** (physically or via SSH)
2. **The API being localhost-only** — `http://localhost:8001` is never reachable from outside the machine

### If You Go Live (Real Money)

Before switching to a live IBKR account, review these risks:

| Risk | Mitigation |
|---|---|
| Unauthorized SSH access → full secret exposure | Disable password SSH login; use key-based auth only (`/etc/ssh/sshd_config`: `PasswordAuthentication no`) |
| Mac Mini stolen or physically accessed | Enable FileVault full-disk encryption — note this requires entering a password on every reboot (disables auto-login) |
| IBKR API credentials compromised | Use a dedicated IBKR sub-account for the fund with withdrawal restrictions enabled in IBKR Client Portal |
| Runaway trades if system malfunctions | Keep IBKR's built-in daily loss limit set in Client Portal → Settings → Account Settings |

> **Auto-login vs. FileVault:** The default setup uses auto-login for hands-free reboots. FileVault disables auto-login. For live trading, the recommended posture is: enable FileVault, accept that a reboot requires a manual login to restart the stack, and treat that as a feature (no unattended access).

## Mac Startup (after any reboot)

**Double-click `YRVI Startup` on your Desktop** to run the pre-flight check. It verifies the Docker containers are running and prints a full GO/NO-GO status table.

Or from the terminal:
```bash
bash ~/you_rock_fund/startup.sh
```

**Container management:**
```bash
# Status
docker compose --env-file .env.compose ps

# Logs
docker compose --env-file .env.compose logs -f scheduler
docker compose --env-file .env.compose logs -f ib_gateway

# Restart scheduler
docker compose --env-file .env.compose restart scheduler
```

### Restarting and Rebuilding Containers

Both scripts verify the secrets container is reachable and configured before operating. They no longer write secrets to disk — that's handled by the `secrets` container.

| Script | When to use |
|--------|-------------|
| `yrvi-restart.sh` | Restart existing container — same image, no rebuild (fast) |
| `yrvi-build.sh` | Rebuild image then restart — use after code changes |

**yrvi-restart.sh** — restart without rebuilding:
```bash
./scripts/yrvi-restart.sh ib_gateway --paper
./scripts/yrvi-restart.sh api        --paper
./scripts/yrvi-restart.sh scheduler  --paper
./scripts/yrvi-restart.sh ib_gateway --live   # requires YRVI_ENV=live in environment
```

Flags: `--dry-run`

**yrvi-build.sh** — rebuild image and restart:
```bash
./scripts/yrvi-build.sh api       --paper   # after editing api.py
./scripts/yrvi-build.sh scheduler --paper   # after editing scheduler.py
./scripts/yrvi-build.sh all       --paper   # rebuild full stack
```

Flags: `--dry-run`

**What both scripts do:**
1. Verify the `secrets` container is reachable at `http://localhost:8001` (fail with a helpful message if not)
2. Verify all required secrets are configured (fail with a link to enter them if not)
3. `yrvi-restart.sh`: runs `docker restart <container>` — `yrvi-build.sh`: runs `docker compose up -d --build [container]`
4. Poll health status every 3s (`ib_gateway` timeout: 180s; others: 60s)

## Running Manually

**Run full pipeline once immediately** (screener → size → execute):
```bash
docker compose --env-file .env.compose exec scheduler python - <<'PY'
import scheduler
scheduler.run_pipeline()
PY
```

**Manual wheel operations:**
```bash
docker compose --env-file .env.compose exec scheduler python wheel_manager.py detect
docker compose --env-file .env.compose exec scheduler python wheel_manager.py check
docker compose --env-file .env.compose exec scheduler python risk_manager.py
```

**Dry run** — set `dry_run: true` via the dashboard settings or the API:
```bash
curl -sS -X POST http://127.0.0.1:3000/api/settings \
  -H 'Content-Type: application/json' \
  -d '{"dry_run":true}'
```

## Monitoring

**Live container logs:**
```bash
docker compose --env-file .env.compose logs -f scheduler   # execution, wheel, risk jobs
docker compose --env-file .env.compose logs -f api         # dashboard API requests
docker compose --env-file .env.compose logs -f ib_gateway  # IB Gateway login status
```

**Persisted log files** (enable bind-mount override in [CONTAINERIZATION.md](CONTAINERIZATION.md) for local file access):
```bash
tail -f docker/data/trade_log.txt    # CSP execution details and order fills
tail -f docker/data/wheel_log.txt    # Wheel check: stop loss exits, covered calls
tail -f docker/data/risk_log.txt     # Daily risk monitor and P&L snapshots
cat docker/data/state.json           # Full system state: positions, wheel holdings, P&L
```

## Screener Scoring

```
Score = 0.50 × buffer_pct × (1.5 if buffer ≥ 10%)
      + 0.35 × premium_pct × (1.1 if in buyzone)
      + 0.15 × (iv_atm / 10)
```

Hard filters applied before scoring:
- `wheel_fit == "Wheel-ready"`
- Delta ≤ 0.21
- Buffer ≥ 5%
- Days to expiry ≥ 3 (Mon→Fri = 3 UTC calendar days)

## Capital Allocation

| Parameter | Default |
|-----------|---------|
| Total fund budget | $250,000 |
| Target per position | $50,000 |
| Max per position | $70,000 |
| Max positions | 5 |

The last position absorbs remaining capital up to `MAX_PER_POSITION`.

## Order Execution

Each position escalates through three stages (120 seconds each):
1. **Limit @ mid** — tries for best price
2. **Limit @ bid** — accepts bid to ensure fill
3. **Market order** — last resort

Liquidity checks: spread ≤ 20%, open interest ≥ 100.

## File Structure

```
config.py                          — Fund parameters and env var loading
screener.py                        — Render API fetch, filters, scoring
position_sizer.py                  — Capital allocation logic
trader.py                          — IBKR CSP execution engine
wheel_manager.py                   — Assignment detection, stop loss, covered calls
risk_manager.py                    — Daily price monitoring and P&L tracking
scheduler.py                       — APScheduler orchestration (5 jobs)
api.py                             — FastAPI dashboard backend
startup.sh                         — Startup & pre-flight check (Docker-aware)
setup_docker.sh                    — One-command Docker setup
docker-compose.yml                 — Container stack definition
docker/                            — Dockerfiles, entrypoint, secrets, preflight
CONTAINERIZATION.md                — Full Docker setup and operations guide
.env.compose.example               — Compose environment variable template
```

## 🔔 Optional: Discord Notifications

YRVI can post trade results to a Discord channel automatically. This is entirely optional — if no webhook is configured, the system runs silently as normal.

### What gets posted

| Event | When | Content |
|-------|------|---------|
| Pre-execution preview | Monday 9:50AM | Sized positions with strikes, contracts, estimated premium |
| Weekly results | Monday ~10:30AM | CSP/CC/stop-loss P&L, week yield %, YTD stats |
| Assignment alert | Friday 4:15PM | Newly assigned stocks with stop-loss prices |

Results are color-coded: 🟢 green (≥1% yield), 🟡 yellow (0.5–1%), 🔴 red (<0.5%).

YTD stats track total premium collected, weeks traded, avg weekly yield, best/worst week, and progress toward the $100K annual target. Stored locally in `ytd_tracker.json`.

### Setup

1. In Discord, go to your channel → **Edit Channel → Integrations → Webhooks → New Webhook**
2. Copy the webhook URL
3. Add it to your `.env` file:
   ```env
   DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/xxx/yyy
   ```
4. Restart the scheduler — Discord posts will begin automatically

No code changes needed. If `DISCORD_WEBHOOK_URL` is absent or blank, all Discord calls are silently skipped.

---

## 🛒 Hardware & Shopping List

A dedicated Mac Mini is the recommended setup for set-and-forget automated trading.

**Supported hardware:**
- ✅ Supported: Mac Mini M4 (Apple Silicon/ARM)
- ✅ Supported: Intel Mac (x86_64)
- ✅ Supported: Any Mac running macOS Sequoia or later

### Minimum Requirements
| Component | Spec | Notes |
|-----------|------|-------|
| Computer | Mac Mini M4 | M5 coming ~mid 2026 |
| RAM | 16GB | Base config is fine |
| Storage | 256GB SSD | Base config is fine |
| OS | macOS (Intel/ARM), Windows | Docker-based; macOS Sequoia for native launchd (legacy) |
| Network | Ethernet (recommended) | More reliable than WiFi |

### Shopping List
- **Mac Mini M4 (16GB/256GB)** — $599 retail, often $469-499 on sale
  - Amazon: https://www.amazon.com/dp/B0DLBTPDCS
  - Apple Store: https://www.apple.com/shop/buy-mac/mac-mini
  - Costco (sometimes cheaper): search "Mac Mini M4" on costco.com
  - MicroCenter: ~$399 in store (best price if one is nearby)
- **Ethernet cable** — ~$10 (if needed)
- **IBKR Account** — Free (paper trading available)
  https://www.interactivebrokers.com

> 💡 **Pro Tip:** Check Amazon weekly — the M4 Mac Mini regularly goes on sale for $469-499. Also check MicroCenter if you have one nearby — they often have it for $399!

> **Note:** M5 Mac Mini expected ~mid 2026 (WWDC June) at the same $599 price — worth waiting if you can!

### Optional but recommended
- **UPS Battery Backup** — ~$50-100 (protects against power outages)
- **Monitor** (only needed for initial setup, can SSH after)

### Why Mac Mini?
- Runs 24/7 silently (~6W power draw)
- Auto-restarts after power outage
- IB Gateway + YRVI use <1GB RAM total
- Pays for itself in first week of trading ($3,500+ weekly target)

### Total Setup Cost
| Item | Cost |
|------|------|
| Mac Mini M4 | ~$499 |
| UPS backup | ~$75 |
| Ethernet cable | ~$10 |
| **Total** | **~$584 one time** |

vs $3,500+/week potential income = ROI in first week! 💰

---

---

## Legacy / Manual Setup (macOS launchd)

> **DEPRECATED — for historical reference only.** This pre-Docker launchd setup is no longer supported. Use the Docker setup above. Credentials in this section reference the old `.env` / Keychain approach, which was replaced by the secrets container in v1.3.0.

**Prerequisites (legacy):** Python 3.13+, macOS Sequoia, IB Gateway.

**One-time setup:**
```bash
cp .env.template .env     # fill in IBKR credentials and RENDER_SECRET
bash setup_ibc.sh
```

`setup_ibc.sh` installs Homebrew, Python 3.13, IB Gateway, IBC, and the launchd scheduler service.

**IB Gateway service management:**
```bash
launchctl list com.yourockfund.ibgateway
launchctl kickstart -k gui/$(id -u) com.yourockfund.ibgateway
tail -f ~/IBC/Logs/ibgateway_stdout.log
```

**Scheduler management:**
```bash
launchctl list com.yourockfund.scheduler
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.yourockfund.scheduler.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.yourockfund.scheduler.plist
tail -f ~/you_rock_fund/scheduler_stdout.log
```

**Monitoring (legacy):**
```bash
tail -f trade_log.txt        # CSP execution details
tail -f wheel_log.txt        # Wheel check and covered calls
tail -f risk_log.txt         # Daily risk monitor
cat state.json               # Full system state
```

---

## Version History

### v1.6.0 (May 2026)
- Version update notification — dashboard checks GitHub for a newer release and displays a banner with a one-click `yrvi://` upgrade link
- `yrvi://` URL scheme registered by `setup_docker.sh` so upgrade links open Terminal and run the upgrade script automatically
- Wheel manager prefers the assigned-strike CC when the highest available delta is ≥ 0.20, rather than always picking the strike closest to 0.20
- This Week page shows a screener estimates disclaimer so members understand data is pre-market and may change
- Bug fixes: generic tick list for option greeks, CC positions no longer consume CSP slots, held tickers always included in screener targets, secrets client used for Discord webhook URLs

### v1.5.0 (May 2026)
- Dashboard IBKR Holdings table now shows Delta, Buffer %, Prem/Contract, and Total Premium for each option position
- `trade_log.json` written to the `yrvi_data` volume at fill time for every CSP and CC order, capturing delta, buffer %, and premium at entry
- `/api/positions` joins live IBKR portfolio items with `trade_log.json` on `(symbol, expiry, strike, right)`; missing fields return null and display as `—`
- One-time backfill reconstructs current-week CSP records from `state.json` on first run (only where fill price is confirmed — no guessing)

### v1.4.5 (May 2026)
- Shutdown button on Settings page with confirmation dialog — stops all YRVI containers (scheduler, web, ib_gateway, secrets, api) in order, api last
- `POST /api/shutdown` accepts `{"confirm": "shutdown"}` and runs `docker stop` against each container
- `POST /api/restart-scheduler` now works in Docker mode (was returning HTTP 501) — runs `docker restart yrvi-scheduler-1`
- `Dockerfile.api` installs the static Docker CLI (~70MB) from download.docker.com (arch-aware: x86_64 / aarch64)
- `docker-compose.yml` mounts `/var/run/docker.sock` into the api container with `:ro` (note: read-only does not restrict Docker API commands — the api effectively has root on the host)

### v1.4.4 (May 2026)
- Timezone dropdown on Settings page (6 US timezones — Pacific, Mountain, Central, Eastern, Alaska, Hawaii)
- `POST /api/settings/timezone` validates the IANA name via `zoneinfo.ZoneInfo` and persists to `settings.json`
- Scheduler reads `timezone` from `settings.json` at startup (falls back to `TIME_ZONE` env var, then `America/Los_Angeles`)
- Closes #3

### v1.4.3 (May 2026)
- `tws_password_live` moved to optional — paper-only members no longer blocked at setup
- Secrets page `Required` section now lists 4 keys (was 5); `tws_password_live` moves to `Optional`

### v1.4.2 (May 2026)
- Secrets page now dynamically renders all secrets from the API (was hardcoded to 5, now shows all 10)
- New Account Info section showing IBKR account ID and username fields
- Unknown future secrets surfaced from the backend automatically render with their raw key as the label (no frontend change needed)

### v1.4.1 (May 2026)
- IB Gateway: stop retrying login on failure to prevent IBKR account lockout (`LoginFailed=terminate` patched into IBC config at startup)
- IB Gateway: credential preflight check before IBC starts — exits cleanly with Discord alert if `tws_userid_*` or `tws_password_*` is missing from the secrets container
- IB Gateway: lockout dialog detection (case-insensitive match on "locked out", "excessive number of failed login attempts", "PASSWORD NOTICE", "Login failed") sends a Discord alert and halts the container
- IB Gateway entrypoint now wraps the gateway process in a monitored runner instead of `exec` so log patterns can be observed

### v1.4.0 (May 2026)
- Account credentials moved into the secrets container UI — IBKR account ID, IBKR username, and VNC password are now entered once at `http://localhost:8001` instead of by editing `.env.compose`
- `account_paper`, `tws_userid_paper`, `account_live`, `tws_userid_live`, `vnc_server_password` added to the secrets store (AES-256-GCM)
- `setup.html` reorganized: new "Account Info" section above the existing passwords section
- IB Gateway entrypoint fetches `TWS_USERID` + `VNC_SERVER_PASSWORD` from the secrets container at startup (falls back to `ibgateway123!test` for VNC when unset)
- `config.py` reads `ACCOUNT` via `secrets_client.get_secret`, keyed by `TRADING_MODE`
- Removed `ACCOUNT_PAPER`, `TWS_USERID_PAPER`, `ACCOUNT_LIVE`, `TWS_USERID_LIVE`, `VNC_SERVER_PASSWORD`, `IBKR_USERNAME_LIVE`, `IBKR_PASSWORD_LIVE_FILE` from `.env.compose`
- Closes #1

### v1.3.1 (May 2026)
- README fully updated for v1.3.0 secrets container architecture (removed all stale macOS Keychain references from setup, security, and script-flow sections)
- `docker/preflight.sh` orphan `secrets_dir` variable removed

### v1.3.0 (May 2026)
- Secrets container — encrypted secrets manager, removes macOS Keychain dependency, cross-platform support

### v1.2.0 (May 2026)
- Watchdog auto-restart: after 30 min down outside market hours, watchdog restarts ib_gateway via `yrvi-restart.sh` and sends Discord alerts before/after
- New `auto_restart_gateway` setting (default true) — toggleable from dashboard
- Market-hours guard: no auto-restart 9:30 AM – 4:00 PM ET; alert-only during market hours
- One restart attempt per failure episode; resumes hourly alerts if restart fails

### v1.1.0-beta (April 2026)
- macOS Keychain secrets management (replaces manual docker/secrets/ file creation)
- setup_docker.sh --paper / --live mode flags (required, replaces bare invocation)
- Password double-entry confirmation with character count on first run
- Ephemeral secret files — written at launch, deleted after containers start
- Secret rotation via Keychain Access.app (delete entry, re-run script)
- Steps 5 & 6 idempotent — login item and Desktop app skip if already installed
- Hardware tier policy: Mac Mini required for live trading, Windows scoped to paper only
- README: Hardware Requirements table, Security section, You Rock Club onboarding note
- Versioning policy: minor bump for new capabilities, patch for field fixes, beta drops when first live Mac Mini member completes a full week unassisted

### v1.0.0-beta (April 2026)
- Docker containerization (replaces launchd)
- Cross-platform: Mac Intel/ARM + Windows
- Secrets management via macOS Keychain (ephemeral docker/secrets/ files)
- Auto-start via Docker login plist
- nginx serving React dashboard
- Socket-based health checks
- Scheduler heartbeat monitoring

### v0.1.0-beta (April 2026)
- Initial automated trading system
- Wheel strategy (CSP + CC)
- IB Gateway auto-login via IBC
- Zero-touch Mac reboot via launchd
- Discord auto-posting
- Web dashboard (React + FastAPI)
- YRVI logo and desktop app
- Friend install blueprint (Mac Mini)
