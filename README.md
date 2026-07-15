# You Rock Volatility Income Fund (YRVI)

![Version](https://img.shields.io/badge/version-5.0.2-blue)

An automated Python algorithmic options trading system that generates weekly income through the complete wheel strategy — selling cash-secured puts (CSPs), managing assignments with covered calls (CCs), and enforcing automatic stop losses — all running 24/7 on a Mac Mini with zero manual intervention.

## How It Works

**Full weekly schedule:**

| Day | Time (PST) | Action |
|-----|-----------|--------|
| Saturday | 6:00 PM | Screener preview — logs top 5 targets, no trades |
| Monday* | 9:50 AM | Discord preview — posts this week's sized positions |
| Monday* | 9:55 AM | Wheel check — stop losses + sell covered calls on assigned stocks |
| Monday* | 10:00 AM | CSP execution — screen → size → execute 5 positions |
| Tuesday–Thursday | 9:00 AM | Daily risk monitor — checks stop loss thresholds, logs P&L |
| Saturday | 8:00 AM | Assignment detection — checks for newly assigned positions (after IBKR posts overnight) |
| Wednesday–Friday | 3:00 AM | Auto-update check — pulls latest release if enabled in Settings |

*Shifts to Tuesday when Monday is a market holiday.

**Pipeline:**

1. **Screener** — fetches candidates from the Render API, applies hard filters (delta ≤ 0.21, buffer ≥ 5%, DTE ≥ 3), and scores survivors by buffer + premium + IV

2. **Position Sizer** — allocates $250K across up to 5 positions (~$50K each, last position gets remainder up to $70K max)

3. **Wheel Manager** — Monday 9:55 AM (per holding):
   - **Step 1 — Screener check:** if ticker no longer passes screener filters (down to the wheel-retention market-cap floor) → sell shares at market and free capital
   - **Step 2 — Earnings:** by default the holding is **kept through earnings** and a covered call is written (`Ignore Earnings for Wheel CCs` is on by default). Turn the toggle off to sell shares before earnings instead
   - **Step 3 — Option chain:** query IBKR for CALL options on the nearest Friday; prefer the nearest strike **≥ cost basis** with delta ≤ ~0.20
   - **Step 4 — Decision:** write the covered call. If the holding is underwater and no acceptable strike sits at/above cost, it writes a ~0.20-delta call **below cost** (keeps the shares + premium) rather than force-selling — unless `Sell Shares Instead of Below-Cost CC` is on
   - **Step 5 — Accounting:** freed capital (from any sales) returns to the CSP pool; sold tickers are skipped in that week's screener

4. **Trader** — connects to IBKR, qualifies contracts, checks liquidity (spread within limits + open-interest notional floor), executes with limit-mid → limit-bid → market escalation; automatically replaces failed positions with the next ranked ticker

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
| Liquidity check | Spread within limits + open-interest **notional** ≥ $1M (price-neutral, replaces the old flat OI ≥ 100 count) |
| Screener exit | Sell shares if ticker drops from screener filters (down to the wheel-retention floor) |
| Underwater holdings | Keep the shares and write a below-cost covered call by default, rather than force-selling (toggle to restore force-sell) |
| Earnings protection | New CSP entries skip tickers with earnings within 7 days; held wheel positions are kept through earnings by default (covered call still written) |
| Exclude from wheel | Per-holding Exclude checkbox (and a Settings field) — an excluded ticker is never traded, adopted, or sold: no CSP, no CC, no wheel adoption |
| Auto-replacement | Failed position → automatically try next ranked ticker |

## Getting Started

New to IBKR? See the **[IBKR Account Setup Guide](IBKR_SETUP_GUIDE.md)** for a complete walkthrough — from creating your account to paper trading your first week.

> 📖 **You Rock Club Mac Mini Setup Guide:** See [MAC_MINI_SETUP.md](./MAC_MINI_SETUP.md) for a complete step-by-step walkthrough from unboxing to first trade.

> 📖 **You Rock Club Windows Setup Guide:** See [WINDOWS_SETUP.md](./WINDOWS_SETUP.md) for setup on a Windows Mini PC (GEEKOM A5 or equivalent).

> ❓ **Troubleshooting:** See [FAQ.md](./FAQ.md) for common setup issues and fixes.

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Mac and Windows)
- [Git for Windows](https://git-scm.com/download/win) (Windows only — provides Git Bash to run `setup_docker.sh`)
- Access to the You Rock Club screener API (Render)

### Hardware Requirements

| Mode | Hardware | OS | Secrets Method |
|------|----------|----|----------------|
| Paper trading | Any Mac (Intel or Apple Silicon) | macOS | Encrypted secrets container (AES-256-GCM) |
| Paper trading | Any Windows 10/11 PC | Windows | Encrypted secrets container (AES-256-GCM) |
| Live trading | Mac Mini (recommended) | macOS | Encrypted secrets container (AES-256-GCM) |
| Live trading | Windows Mini PC (e.g. GEEKOM A5) | Windows | Encrypted secrets container (AES-256-GCM) |

> **Cross-platform**: `setup_docker.sh` runs on macOS and Windows (via Git Bash) and detects the platform automatically.

### IB Gateway port reference

| Mode | Port (inside Docker network) |
|---|---|
| Paper trading | 4004 |
| Live trading | 4003 |

These are the ports the Python containers use to reach IB Gateway inside the Compose network. They differ from the standalone IB Gateway defaults (4002/4001) used in the legacy launchd setup.

### ⚠️ Live Trading: IB Key Required

YRVI requires **IB Key** (IBKR Mobile push notification) for two-factor authentication when running live. **SMS 2FA is not supported** — it requires manual code entry on every Gateway restart, which breaks unattended automation.

**Before switching to live:**
1. Install the **IBKR Mobile** app and enable **IB Key** in your account security settings
2. Add live credentials (account ID, username, password) in the YRVI dashboard → Secrets page
3. Switch to live from Settings → Trading Mode → Switch to Live
4. Approve the IB Key push notification on first login (watch VNC)
5. After that, only one approval per week is needed (Sunday session reset)

See the [IBKR Setup Guide](IBKR_SETUP_GUIDE.md#-required-enable-ib-key-before-going-live) for the full walkthrough.

## Installation

```bash
git clone https://github.com/controllinghand/you_rock_fund.git you_rock_fund
cd you_rock_fund
```

#### Configure `.env.compose`

```bash
cp .env.compose.example .env.compose
```

`.env.compose` only contains non-secret settings (ports, trading mode, timezone) — no editing required for a default paper-trading setup. Leave `TRADING_MODE=paper` — the paper account is the safety net for a new setup, so orders route to your paper account (not real money). Dry Run defaults to **off**; enable it in Settings only if you want to simulate without any account fills.

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

- **Required:** IBKR paper password, Render screener API secret
- **Optional:** IBKR live password (only needed for live trading), Discord webhook URL, Discord weekly-plan webhook URL

Setup polls silently until the browser form is submitted, then auto-proceeds — the browser UI shows a completion banner when all required secrets are saved. You can update any secret later by visiting `http://localhost:8001` directly or via the **Secrets** page in the dashboard.

> **Note:** The `docker/secrets/` directory holds empty placeholder files for the file-based fallback path; it's git-ignored and the real values never live there.

#### Remote access — the dashboard on your phone (optional)

Every YRVI service binds to `127.0.0.1`, so the dashboard is reachable only from the box itself. That loopback binding is deliberate and load-bearing: it's why the dashboard needs no login. **Never rebind these services to `0.0.0.0` or forward a port to them.**

If you want the dashboard on your phone, put your box and your phone on a **private overlay network** (Tailscale) and publish just the dashboard port to it — no inbound ports, no public exposure, no code changes, about 15 minutes. See **[docs/remote-access.md](docs/remote-access.md)** for the walkthrough and its non-obvious traps (uncheck Funnel; run the daemon as root; disable key expiry; HTTPS only).

Your own account, your own network, your own devices — and self-supported. The isolation that keeps YRVI deployments independent also means nobody else can debug your box. For why there's no password on the dashboard, see [docs/dashboard-auth.md](docs/dashboard-auth.md).

#### Viewing the IB Gateway screen (built-in View Gateway)

To check the Gateway's screen — a login, 2FA prompt, error dialog, or just its status — use the **View Gateway** viewer built into the dashboard. No VNC client to download: open **Help → System Diagnostics → View Gateway**, then **👁 Open viewer (view-only)** (the password auto-fills). See [docs/view-gateway.md](docs/view-gateway.md).

> Earlier versions told you to install RealVNC/TigerVNC and connect to `127.0.0.1:5900`. That's no longer needed. The raw VNC port still exists on `127.0.0.1:5900` (IPv4) as an optional external-client fallback; on macOS always use the literal `127.0.0.1:5900`, **never `localhost:5900`** (`localhost` resolves to IPv6 `::1`, which macOS Screen Sharing answers → "authentication failed" against the wrong server).

> If `docker compose up` ever fails with *"address already in use"* on 5900, turn Screen Sharing off (**System Settings → General → Sharing → Screen Sharing → OFF**) or set `IB_GATEWAY_VNC_PORT` to a free port in `.env.compose`. For remote terminal access, use SSH (**Remote Login → On**).

#### macOS Setup (Paper)

```bash
./setup_docker.sh --paper
```

On first run, the script opens `http://localhost:8001` in your browser where you'll enter:
- Your IBKR paper account password
- Your Render screener API secret
- Discord webhooks (optional)
- Your IBKR live password (optional — only needed if you plan to trade live)

Setup polls silently until the form is submitted; the browser shows a completion banner when done, then the script auto-proceeds. On subsequent runs, the script detects existing secrets and skips this step.

#### macOS Setup (Live)

```bash
./setup_docker.sh --live
```

Requires always-on hardware — a Mac Mini or a Windows Mini PC (e.g. GEEKOM A5). Live and paper credentials are stored separately in the secrets container under `tws_password_live` and `tws_password_paper` — they're never shared between modes.

#### Windows Setup (Paper)

**Prerequisite:** Install [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/) — enable the WSL2 backend during install; in Settings → General check **Start Docker Desktop when you sign in to your computer**.

`setup_docker.sh` detects the platform automatically and works from either Git Bash or WSL2 (Ubuntu). Choose whichever you prefer:

---

**Option A — Git Bash (simplest, recommended for most users)**

Install [Git for Windows](https://git-scm.com/download/win) — during install select **Git from the command line and also from 3rd-party software**. Then open **Git Bash** (search "Git Bash" in the Start menu) and run:

```bash
cd ~
git clone https://github.com/controllinghand/you_rock_fund.git
cd you_rock_fund
bash setup_docker.sh --paper
```

> **Why `cd ~` first?** Git Bash opens at `/` by default, which maps to the Git install folder. `cd ~` takes you to `C:\Users\<you>` first. Substitute any other path (e.g. `cd /c/trading`) to install elsewhere.

> **Always use Git Bash, not PowerShell or cmd.exe** — the script uses Unix path handling.

---

**Option B — WSL2 / Ubuntu (cleaner Linux environment)**

Docker Desktop already installs WSL2. Open **Ubuntu** from the Start menu (or run `wsl` in a terminal) and run:

```bash
sudo apt-get install -y git   # if git isn't already installed
cd ~
git clone https://github.com/controllinghand/you_rock_fund.git
cd you_rock_fund
bash setup_docker.sh --paper
```

Docker Desktop's WSL2 integration makes the `docker` CLI available inside Ubuntu automatically — no extra steps needed.

---

> **Don't move the folder after setup.** The Task Scheduler auto-start job hardcodes the repo path at setup time. If you move the folder, re-run `bash setup_docker.sh --paper` from the new location to re-register it.

> **Auto-start after reboot:** The script registers a Windows Task Scheduler job so containers restart automatically on every login. If the registration fails, rerun your terminal as Administrator and run `bash setup_docker.sh --paper` again.

> **Viewing the Gateway for 2FA:** If IBKR requires 2FA on first login, use the built-in **View Gateway** viewer — **Help → System Diagnostics → View Gateway** in the dashboard (no VNC client to install). Most first-time logins complete automatically without needing it. See [docs/view-gateway.md](docs/view-gateway.md).

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
- **These placeholder files stay empty (0 bytes) — the IBKR password is never written to disk in cleartext.** Verified on both a dashboard upgrade and a full stop/start: the files never change size. (`setup_docker.sh` only `touch`es them; the legacy Keychain→file injection path in `yrvi-build.sh` is a bootstrap fallback that the secrets-container setup never triggers.)
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

> **⭐ Real-time market data:** Paper accounts get real-time data for free, but a **live account needs paid subscriptions** — without them YRVI falls back to delayed data and System Diagnostics shows Options Data ❌ "no bid/ask". In Client Portal → Market Data Subscriptions → Level I (NBBO), enable **OPRA (US Options Exchanges)** for options, plus **NYSE (Network A)**, **Network B (ARCA/BATS/IEX — covers SPY)**, and **NASDAQ (Network C)** for real-time stock quotes (strike selection + stop-loss). All NP/L1, $1.50/mo each, waived at $20 monthly commissions. See [Step 3 of the live-trading section in IBKR_SETUP_GUIDE.md](IBKR_SETUP_GUIDE.md#preparing-for-live-trading).

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

## Settings Reference

All settings are managed from the dashboard **Settings** page and hot-reload on every API call — no restart required unless noted. They are stored in `settings.json` and fall back to `settings_default.json` for any missing keys.

### Fund Settings

| Setting | Default | Range | Description |
|---------|---------|-------|-------------|
| Initial Fund Budget | $250,000 | $10K – $2M | Starting capital for CSP deployment. When Compound Weekly is off, this is always the deployment base. |
| # Positions | 5 | 1 – 10 | Target number of CSP positions to fill each Monday. |
| Min Position | $10,000 | $5K – $100K | Minimum capital allocated to any single CSP position. |
| Max Position | $70,000 | $10K – $200K | Maximum capital for any single position. The last position absorbs remaining budget up to this cap. |
| Compound Weekly | On | On / Off | When on, uses IBKR net liquidation as the Monday deployment budget so the fund grows as premiums accumulate. Falls back to Initial Fund Budget if IBKR is unreachable. |

### Screener Filters

| Setting | Default | Range | Description |
|---------|---------|-------|-------------|
| Max Delta | 0.21 | 0.10 – 0.30 | Maximum absolute delta for CSPs sold. Higher = more aggressive strikes and more premium, but more assignment risk. |
| Min Buffer % | 5% | 3% – 20% | The strike must be at least this far below the current stock price. Higher = more downside cushion. |
| Earnings Window | 7 days | 0 – 30 days | Skip new CSP entries on tickers with earnings within this many days. Protects against earnings-driven gap moves. |
| Ignore Earnings for Wheel CCs | **On** | On / Off | On (default): held positions are kept through earnings and the covered call is still written. Off: shares are sold before earnings to dodge the gap. No effect on new CSP entries. |
| Wheel Retention Mkt Cap | $5.0B | $1.0B – $10.0B | Keep wheeling a held name down to this market cap, even if below the 10B entry floor — sell only if it falls further. New CSP entries still use the 10B entry floor. |
| Sell Shares Instead of Below-Cost CC | Off | On / Off | Off (default): an underwater holding with no covered call at/above cost writes a ~20-delta call below cost (keeps shares + premium). On: force-sell those shares at market instead (the old behavior). |
| Stop Loss on Wheel Holdings | Off | On / Off | When on, a holding is sold on Monday if its price has fallen more than the Stop Loss % below its assigned strike. The screener exit is the primary exit — this is an optional additional layer. |
| Stop Loss % | 10% | 0% – 50% | How far below the assigned strike triggers a stop loss sale. Only active when Stop Loss on Wheel Holdings is enabled. |

### Liquidity Filters

| Setting | Default | Range | Description |
|---------|---------|-------|-------------|
| Max Spread % | 20% | 5% – 50% | Skip a CSP if the bid/ask spread exceeds this percentage of the mid price. Protects against poor fills on illiquid options. |
| Min Bid Yield % | 1% | 0.5% – 3% | Override the spread filter if the bid yield meets this threshold — useful when wide spreads are justified by high premium. |
| Max Spread Hard Cap % | 50% | 25% – 100% | Always skip regardless of yield if spread exceeds this. An absolute ceiling that cannot be overridden by bid yield. |
| Min OI Notional | $1.0M | $0.25M – $5.0M | Skip if the option's open-interest notional (open interest × strike × 100) is below this. A price-neutral liquidity floor that replaced the old flat "open interest ≥ 100" count, which unfairly penalized high-strike names (the same dollar liquidity shows fewer contracts on a $300 stock than a $30 one). A small absolute floor (10 contracts) still rejects totally-dead strikes. |

### Execution

| Setting | Default | Options | Description |
|---------|---------|---------|-------------|
| Monday Execution Time | 10:00 AM PST | Any time | When the CSP pipeline fires each Monday. 10:00 AM PST (1:00 PM ET) is recommended for best liquidity and tighter spreads. **Requires a scheduler restart to take effect.** |
| Dry Run | Off | On / Off | Simulate all orders without placing real trades. Fills are logged as `dry_run`. Useful for testing a new configuration. When in live trading, enable this for extra protection before committing real money. |

### IB Gateway

| Setting | Default | Range | Description |
|---------|---------|-------|-------------|
| Auto-restart Gateway | On | On / Off | Automatically restart the IB Gateway container if it goes offline outside market hours (9:30 AM – 4:00 PM ET). One attempt per failure episode; hourly alerts resume if the restart fails. |
| Nightly Restart Time | 11:59 PM | Any time | Scheduled time for the nightly gateway container restart. Use **Apply to Gateway** to push a new time to the running container immediately. |
| Suppress Restart Window | 30 min | 0 – 120 min | How long before the scheduled restart time to suppress other auto-restart attempts. Prevents a double-restart if the gateway drops just before the nightly cycle. |

## Order Execution

Each position escalates through three stages (120 seconds each):
1. **Limit @ mid** — tries for best price
2. **Limit @ bid** — accepts bid to ensure fill
3. **Market order** — last resort

Liquidity checks: spread within the configured limits, and open-interest notional (OI × strike × 100) ≥ $1M (price-neutral floor; replaces the old flat OI ≥ 100 count).

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

## 🔔 In-App Alert Feed

Since v4.0.0 the dashboard is **self-observing** — every operational alert (gateway down/restarting/back, login failed or locked, auto-restarts, weekly results) is recorded in-app, not just sent to Discord. A **bell** in the status bar shows an unread badge color-coded by severity (🚨/❌/🔒 critical, 🔄/⚠️ warning, ✅ resolved), and its dropdown lists recent history newest-first; **Clear** wipes it.

- The in-app record happens **first and unconditionally**, so the feed works even with Discord unconfigured or down — Discord (push when away) and the bell (context when in the app) are complementary, not either/or.
- **Standalone by design:** each box keeps its own feed. There is no cross-box aggregation — live and paper boxes run on different IBKR accounts and must not see each other.
- History is file-backed (`/data/alerts.json`, last 200) so it survives the api restarts that happen on trading-mode switches and upgrades. Endpoints: `GET /api/alerts`, `DELETE /api/alerts`.

## 🔔 Optional: Discord Notifications

YRVI can post trade results to a Discord channel automatically. This is entirely optional — if no webhook is configured, the system runs silently as normal.

### What gets posted

| Event | When | Content |
|-------|------|---------|
| Pre-execution preview | Monday 9:50AM | Sized positions with strikes, contracts, estimated premium |
| Weekly results | Monday ~10:30AM | CSP/CC/stop-loss P&L, week yield %, YTD stats |
| Assignment alert | Saturday 8:00AM | Newly assigned stocks with stop-loss prices |
| Called-away alert | Saturday 8:00AM | CC-covered shares called away at expiry |
| End-of-week summary | Friday ~4:20PM | Called-away recap, open wheel positions, weekly P&L |
| Gateway watchdog alert | As needed | Gateway down / restarting / back online notifications |

Results are color-coded: 🟢 green (≥1% yield), 🟡 yellow (0.5–1%), 🔴 red (<0.5%).

YTD stats track total premium collected, weeks traded, avg weekly yield, best/worst week, and progress toward the $100K annual target. Stored locally in `ytd_tracker.json`.

### Setup

1. In Discord, go to your channel → **Edit Channel → Integrations → Webhooks → New Webhook**
2. Copy the webhook URL
3. Open `http://localhost:8001` (or visit **Secrets** in the dashboard) and paste it into the **Discord Webhook URL** field
4. Restart the scheduler — Discord posts will begin automatically

No code changes needed. If no webhook is configured, all Discord calls are silently skipped.

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
  Use referral link for signup: https://ibkr.com/referral/sean5376

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

### v5.0.2 (June 2026)
- **Diagnostics wait for the delayed options feed** — the Help → System Diagnostics options probe now polls up to ~30s for SPY bid/ask/delta instead of a flat 5s wait. On a fresh connection the delayed options farm (usopt) is slow to populate, so healthy data was being false-flagged as "no bid/ask — needs OPRA"; the real trader already waits 60s for exactly this. Stops the spurious red Options-Data error during market hours.

### v5.0.1 (June 2026)
- **Dashboard cleanups** — dropped the redundant Prem/Contract column from IBKR Holdings (option Avg Price is now per-share) and removed the duplicate top "Run Screener" button on This Week.

### v5.0.0 (June 2026)
- **Exclude holdings from the wheel** — a per-holding Exclude checkbox (IBKR Holdings table) plus a Settings "Excluded Tickers" field. An excluded name is never traded, adopted, or sold: no CSP, no covered call, never force-sold, and never adopted into `wheel_holdings`. Enforced across the screener, both wheel-adoption paths, the per-holding loop, and the risk monitor.
- **Current Price column + per-share option Avg Price** in the holdings table.
- **Entry IV at execution** — implied vol captured from the live greeks at fill time on both the CSP and CC paths, written to the trade log and shown as an Entry IV column / on PositionCards.
- **Narrow-screen horizontal scroll** for the holdings table, and **live covered-call progress reporting** during the Monday run (per-holding status events surfaced in This Week).

### v4.3.0 (June 2026)
- **Min OI Notional slider** — the open-interest liquidity floor (from v4.2.0) is now tunable from Settings → Liquidity Filters ($0.25M–$5M, default $1M) instead of being a code constant; hot-reloads like the spread thresholds.
- **CCs written *through* earnings by default** — `Ignore Earnings for Wheel CCs` now defaults **on**: a held position with earnings in the Monday–Friday window is kept and a covered call is written, rather than force-selling to dodge the gap. Turn it off to restore sell-before-earnings. No effect on new CSP entries. (Boxes that previously saved this off keep their saved value — toggle on + save once after upgrading.)
- **Settings labels cleaned up** — dropped the redundant word "Filter": *Earnings Filter* → *Earnings Window*, *Ignore Earnings Filter for Wheel CCs* → *Ignore Earnings for Wheel CCs*.

### v4.2.0 (June 2026)
- **Notional-based open-interest floor** — replaced the flat `open interest ≥ 100` count with a price-neutral floor: `OI × strike × 100 ≥ $1M` plus a small absolute floor (10 contracts). A flat contract count penalized high-strike underlyings — the same dollar liquidity shows fewer contracts on a $300 name than a $30 one (e.g. BE $302.50 at OI 76 = $2.3M notional was wrongly skipped while a lower-strike name with similar dollars passed). Thresholds hot-reload from settings.
- **Fix:** the Discord results card mislabeled open-interest skips as "spread too wide" (an `oi` skip fell through to the default label). It now reads "open interest too thin (OI N, $X notional)".

### v4.1.0 / v4.1.1 (June 2026)
- **Below-cost covered call instead of force-selling underwater holdings** — when a wheeled holding is underwater and no covered call sits at/above cost basis, the wheel now writes a ~0.20-delta call **below cost** (keeping the shares + premium) rather than dumping the shares at a loss. New `Sell Shares Instead of Below-Cost CC` toggle (default off) restores the old force-sell behavior.
- **CC strike selection** prefers the nearest strike **≥ cost basis**, not the exact assigned strike.

### v4.0.0 / v4.0.1 (June 2026)
- **In-app alert feed — the dashboard is now self-observing** — every operational alert that flows through the single Discord chokepoint is also persisted to a capped ring buffer (`/data/alerts.json`, last 200) and surfaced via a status-bar bell with an unread badge color-coded by severity. The in-app record happens first and unconditionally, so the feed works even with Discord down or unconfigured. Standalone by design — each box keeps its own feed, no cross-box aggregation. New endpoints `GET /api/alerts` and `DELETE /api/alerts`.
- **Fix (v4.0.1):** Restart Scheduler now saves settings first and stays visible until applied.

### v3.7.0 (June 2026)
- **Run Now is now a recovery-safe tool** — its purpose is to complete a Monday that failed or partially filled (system down, partial fills, etc.) using whatever cash/securities are actually available. Re-running is now **idempotent**: it reconciles against the live IBKR account and only does what's *missing*, never duplicating what already succeeded.
- **Covered-call dedup** — the wheel check snapshots open short calls from IBKR. If a holding is already covered, it is left as-is: no second (naked) call is written, and the shares are **not** sold even if the screener flipped (selling covered shares would strand a naked call, and the fund never buys back CCs). Matches ticker + this-Friday expiry.
- **CSP dedup** — tickers that already have an open short put are skipped, counted toward the position cap, and only the *remaining* slots are filled with available cash (`NUM_POSITIONS − wheel holdings − open CSPs`).
- The **Run Screener** preview reflects all of this: shows "already covered — skip" rows in the Monday Wheel Plan and a recovery banner listing CSPs already open. Source of truth is the live account, not `state.json`, so it's robust even if a crash skipped a state write.

### v3.6.0 (June 2026)
- **"This Week" now mirrors Monday exactly** — a new `monday_runner.py` orchestrator is the single source of truth for the full Monday sequence (wheel check → CSP pipeline). The scheduler, **Run Now**, and **Run Screener** all call the same code, so what you preview is what executes.
- **Run Screener = faithful dry-run preview** — previously the preview used the 10B entry screener and showed held names below the entry floor (e.g. QBTS) as *dropped*, contradicting Monday. It now runs the real wheel check in dry-run mode (queries live IBKR option chains for covered-call decisions, places no orders, writes no state) and shows a **Monday Wheel Plan**: which holdings get a CC (strike/delta/premium), which get sold, and freed capital — then sizes CSPs against that plan. Takes ~20–40s and requires the gateway up.
- **Run Now = execute Monday on demand** — now runs the complete live sequence (sell shares, write covered calls, open CSPs), not just CSPs. A context-aware confirmation lists exactly what will execute and warns about double-execution if clicked inside the Monday 9:55/10:00 AM PT scheduled window.
- `run_wheel_check(dry_run=...)` added with full side-effect gating (no orders, state, Discord, or trade-log writes in dry-run); returns a structured result dict. New `IBKR_CLIENT_ID_PREVIEW` (4) keeps API-driven runs from colliding with the scheduler's 9:55 wheel job.

### v3.5.0 (June 2026)
- **Separate wheel retention market-cap floor** — market cap is an *entry* criterion (10B), but a held name that slips modestly below it shouldn't be force-sold. A new `wheel_retention_market_cap_min` setting (default $5.0B) lets the Monday wheel check keep writing covered calls on assigned positions down to a lower floor; a name is only sold if it falls below the retention floor or fails the genuine risk gates (stop loss, no viable 0.20-delta CC, earnings this week).
- New **"Wheel Retention Mkt Cap"** slider on Settings → Screener Filters (1.0B–10.0B, 0.5B steps). New CSP entries still use the 10B entry floor.
- `screener.get_all_candidates()` gains a `market_cap_min` override; `wheel_manager` retention check queries the screener with the retention floor instead of the entry floor.

### v3.0.0 (May 2026) 🚀
- **Live trading support** — full paper ↔ live toggle from the Settings page. IB Gateway restarts automatically with the correct account credentials. No config file edits required.
- **IB Key required for live** — SMS 2FA not supported for unattended automation. Enable IB Key (IBKR Mobile push notification) before going live. First login requires a one-time VNC approval; nightly restarts need re-auth once per week.
- **Secure live credentials** — `tws_userid_live`, `tws_password_live`, and `account_live` stored in the encrypted secrets container alongside paper credentials
- **Green live mode indicator** — header pill and Settings badge turn green + pulse when live is active; amber warning bar shows "Live mode active — all trades use real money"
- **Reset Installation button** — always accessible in Settings → IB Gateway (previously only visible on specific error conditions)
- **Account-scoped positions** — dashboard filters IBKR Holdings to the configured account only
- **Unrealized/Realized P&L** — now displays correctly on the live account dashboard

### v2.2.10 (May 2026)
- **Windows setup guide** — [WINDOWS_SETUP.md](./WINDOWS_SETUP.md) added for GEEKOM A5 and equivalent Windows Mini PCs; covers Git Bash, Docker Desktop, auto-login, Remote Desktop, and full live trading setup
- `setup_windows.ps1` deprecated — `setup_docker.sh` is now the single setup entry point for macOS and Windows
- README updated: Windows Mini PC added to the live trading hardware table; "Windows paper-only" restriction removed

### v2.2.9 (May 2026)
- Fix: open position cards now show a true execution-time snapshot — Price, Buffer, Delta, and Yield all reflect the exact state when the option was sold, not Saturday's screener snapshot
- `trader.py` fetches the live underlying price from IBKR immediately after a fill and stores it as `stock_price_at_entry`; `buffer_pct_at_entry` computed from this live price

### v2.2.8 (May 2026)
- Fix: position card "Yield" now shows actual collected yield at fill (`fill_price / strike × 100`), not screener projected yield; label changes to "Act. Yield" for filled positions
- Fix: position card "Delta" now shows `delta_at_entry` (IBKR-measured at execution), falling back to screener delta; column renamed "Entry δ"

### v2.2.7 (May 2026)
- Fix: strike upward adjustment when stock rallies between Saturday and Monday — `verify_and_adjust_strike` now scans up the chain when delta drops below a `MIN_DELTA = 0.15` floor, maximising premium while staying within the 0.21 cap
- Discord execution results now include `δplan→actual` format (e.g. `δ0.20→0.13`) showing screener vs execution delta for every filled trade
- Dashboard "Delta" column renamed to "Entry δ"

### v2.0.0 (May 2026)
- **Cross-platform Windows support** — `setup_docker.sh` now detects macOS, Windows (Git Bash / MINGW), and WSL2 automatically; runs the full 6-step setup flow on all platforms
- Windows: setup opens the secrets browser UI (`http://localhost:8001`) via `cmd.exe /c start`; Step 5 registers a Task Scheduler job (`YRVI_Docker_AutoStart`) for container auto-start on login
- Windows live trading now fully supported — Windows Mini PC (e.g. GEEKOM A5) added as a valid always-on live trading host alongside Mac Mini
- `setup_windows.ps1` (PowerShell, file-based secrets, Rancher Desktop) superseded by the unified bash script

### v1.24.0 (May 2026)
- **Shutdown progress overlay** — after confirming shutdown, the dashboard shows a full-screen overlay while containers stop, then displays "YRVI is offline / restart from Desktop" once the API goes silent; prevents interaction with the dead UI during the shutdown window
- Setup now polls silently until the browser secrets form is submitted and auto-proceeds — no CLI fallback, no manual Enter prompt; the browser shows a completion banner when done
- Fix: `OverallBadge` contrast improved in light mode
- Fix: setup script step headers now use `printf` instead of `echo` for correct ANSI color rendering across all shells

### v1.23.2 (May 2026)
- Discord trade notifications now include the fund version number and IBKR account identifier

### v1.23.1 (May 2026)
- Fix: holiday countdown displayed the next Monday instead of next Tuesday when the trading day shifts due to a market holiday

### v1.23.0 (May 2026)
- **Apply to Gateway** — new button on the IB Gateway settings panel that schedules a container restart with a persistent restart-time override, so watchdog timing changes take effect without manual intervention

### v1.22.0 (May 2026)
- **Context-aware gateway watchdog alerts** — Discord alerts now include richer context about gateway state; new IB Gateway section in the Settings UI lets members configure watchdog restart timing directly from the dashboard

### v1.21.1 (May 2026)
- **Friday end-of-week summary** — Discord post sent automatically after the Friday assignment detection run, summarizing called-away shares, open wheel positions, and weekly P&L
- Fix: Friday summary field layout and called-away wording

### v1.21.0 (May 2026)
- **Called-away detection** — system detects when CC-covered shares are called away at Friday expiry and sends a Discord alert

### v1.20.1 (May 2026)
- Fix: non-compound CSP contract count now capped to available capital (was possible to over-allocate in edge cases)
- Fix: clarified UI note that Max Position is ignored in compound mode

### v1.20.0 (May 2026)
- **Compound equal-weight position sizing** — in compound mode, capital is divided equally across all active positions rather than being weighted by screener score
- Max Position cap is skipped in compound mode (equal weighting makes it redundant)
- **Wheel CC earnings filter toggle** — new Settings toggle to bypass the earnings proximity check when selling covered calls

### v1.19.1 (May 2026)
- Fix: execution day label showed the wrong day when Monday falls on a market holiday

### v1.19.0 (May 2026)
- **Compound Weekly** — when enabled (default on), the Monday pipeline uses IBKR net liquidation as the deployment budget so the fund grows automatically as premiums accumulate; falls back to Initial Fund Budget if IBKR is unreachable
- **Stop Loss on Wheel Holdings** — optional toggle to sell a holding on Monday if price falls more than a configurable % below its assigned strike (0–50%, default 10%); off by default since the screener exit is the primary exit
- **Dry Run default changed to off** — paper trading is already the safety layer; Dry Run on first install no longer adds a redundant extra step. Live trading mode shows an amber callout in Settings so users know the option exists
- **Settings Reference** — collapsible reference section added to the Help page (grouped by section, with default and range for every setting); matching table added to README
- Version files reconciled: `VERSION`, `package.json`, and README badge now all track the same number

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
