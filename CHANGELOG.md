## [5.1.0] — 2026-06-24
### Added
- **One-click "Restart Gateway" button** so a non-technical operator can recover a wedged gateway without a terminal. When the watchdog can't self-heal and pages you, the old instruction was `docker compose --env-file .env.compose restart ib_gateway` — useless to anyone who can't (or won't) open a shell, especially on the Windows boxes. There's now a **Restart Gateway** button on the **Help → System Diagnostics** page (next to Reset Installation), and one that appears in the top **StatusBar** the moment the gateway goes unreachable. Both POST to the new `/api/gateway/restart` endpoint, which runs `docker restart ib_gateway` from the api container (via the already-mounted `docker.sock`). A full restart re-runs login — automatic on paper, an IB Key 2FA push on live (the button warns first). It's deliberately exempt from the watchdog's auto-restart cooldown (a human clicking is intentional) but records the restart so the watchdog won't immediately fire another on top of it. The StatusBar button is suppressed when the gateway is `locked`/`failed`, where a restart would only risk a deeper lockout.

### Fixed
- **Watchdog now escalates to a full restart when the IBC command server is unreachable — instead of paging and giving up.** This is exactly the gap that left yrvip wedged: a hung API listener (port open, handshake dead) is normally cleared by an IBC *soft* restart, but the soft restart goes through IBC's command server (port 7462) — and when that command server is *also* down, the watchdog used to immediately page a human and stop, never reaching the `docker restart` that the api container is perfectly capable of running. It now falls straight through to a full restart (gated by the same cross-episode cooldown / lockout guard), so this class of wedge self-heals with no human.
- **Operator-facing gateway alerts now point at the dashboard button** ("open the dashboard → Help → Restart Gateway") instead of a bare docker command, with the docker command kept as a labeled advanced/fallback line. Routed through a single shared hint string so every gateway page stays consistent.

## [5.0.5] — 2026-06-23
### Fixed
- **"Gateway Version" diagnostic row now actually populates.** In 5.0.4 the gateway entrypoint tried to publish `$TWS_MAJOR_VRSN` to `/data/gw_tws_version`, but the gateway container runs as uid 1000 and `/data` is root-owned (the gateway only ever *reads* from it) — so the write failed with "Permission denied" and the row never appeared. Reworked: the API now reads the version straight from the `ib_gateway` container's env via `docker inspect` (it already shells out to `docker` for restarts/logs). Bonus: `docker inspect` works even when the container is **stopped**, so the running TWS version is visible exactly when you need it most — during an exit-4 version-skew crash. Dropped the broken entrypoint write.

## [5.0.4] — 2026-06-23
### Fixed
- **Diagnostics no longer cries "Options Data — need a paid OPRA subscription" on a perfectly healthy live account.** The options-data check probes a near-the-money SPY put to confirm bid/ask/greeks are flowing. To find the contract it called `reqSecDefOptParams`, which returns *multiple* SMART chain entries — including a degenerate single-strike/single-expiry artifact (observed live: one lone `2026-09-11 $672P`). The check grabbed the **first** SMART entry, which was that artifact, so it ended up probing a far-dated, ~9%-OTM strike that legitimately has no resting quote — then reported the empty bid/ask as a missing market-data subscription. It now picks the **richest** chain (most strikes, then expirations), so it probes a liquid ~3%-OTM strike on the next Friday and correctly sees the real-time quote. (Verified on the live account: real-time SPY stock *and* options quotes were flowing the whole time — e.g. `06/25 735P` bid 3.16 / ask 3.18 — the alarm was purely a bad contract pick.)
- **SPY price in diagnostics no longer always says "(delayed)".** The label was hardcoded; it now reflects the ticker's actual `marketDataType` (real-time / frozen / delayed / delayed-frozen), so live accounts with a real-time subscription are labeled correctly.

### Added
- **"Gateway Version" row in System Diagnostics** — shows the running IB Gateway / TWS build (e.g. `10.48.1b`). The gateway entrypoint now publishes `$TWS_MAJOR_VRSN` to `/data/gw_tws_version` on startup (the IBKR API only exposes the protocol `serverVersion`, not the Gateway build), and the diagnostic reads it back. Reflects the version actually running after any v5.0.3 self-heal — so a future version skew is visible at a glance instead of surfacing as a mystery exit-4 crash.

## [5.0.3] — 2026-06-23
### Fixed
- **IB Gateway now self-heals a TWS version that an upgrade left shadowed — no more "can't find jars folder" exit-4 crash after updating.** The gateway image is built `FROM ghcr.io/gnzsnz/ib-gateway:latest`, and the TWS/Gateway jars install under `/home/ibgateway/Jts` — which is also a *persistent named volume*. Docker only seeds a named volume from the image when the volume is empty, so when a dashboard upgrade rebuilds the image and `latest` has bumped the bundled TWS version (e.g. 10.47.1e → 10.48.1b), the already-populated volume keeps the **old** version and shadows the new jars baked into the image. IBC is told (via `TWS_MAJOR_VRSN`) to launch the new version, can't find its jars in the volume, and the container exits 4 with `Error: Offline TWS/Gateway version <X> is not installed: can't find jars folder` — nothing ever binds the API port, so the dashboard just sees a dead gateway. (This looks like a 2FA/login lockout, which also exits 4, but it isn't — no login is ever attempted.) The Dockerfile now stashes a copy of the install at `/opt/tws-baked` (outside the volume), and `entrypoint.sh` copies the expected version out of that bake into the volume on startup if it's missing. The result: an upgrade that bumps TWS now repairs itself on the next gateway start — automatically, with no SSH access to the box and (on paper) no re-login. Idempotent; a one-time `🔄` Discord/in-app alert is posted when a restore happens. Live boxes still need their usual 2FA on the post-upgrade gateway start, but the version skew no longer blocks startup.

## [5.0.2] — 2026-06-23
### Fixed
- **Diagnostics now polls up to 30s for delayed options data** instead of giving up immediately, so the gateway connectivity check no longer reports missing bid/ask on feeds that publish on a delay.

## [5.0.1] — 2026-06-23
### Changed
- **Removed the duplicate top "Run Screener" button on the This Week page.** The header had a "Run Screener" button right next to "Run Now", plus a second "Run Screener Now" button in the empty-state card below — two controls for the same action. Dropped the top one; the empty-state "Run Screener Now" remains, and the green "Run Now" stays in the header. (To preview again after results show, navigate off the page and back — it re-renders the empty state.)
- **Removed the redundant "Prem/Contract" column from the IBKR Holdings table.** Now that Avg Price is shown per-share for options (5.0.0), Prem/Contract showed the same per-share entry premium — and Avg Price is the better source (it comes from IBKR's cost basis, so it's always populated, whereas Prem/Contract was only filled for app-executed trades). Dropping it reclaims horizontal space. Total Premium (the aggregate) is unchanged.

## [5.0.0] — 2026-06-22
### Added
- **Exclude holdings from the wheel** — you can now tell the fund to leave a stock completely alone. A new **Exclude** checkbox next to each stock in the dashboard's IBKR Holdings table (and an **Excluded Tickers** field in Settings → Screener Filters for names you don't yet hold) marks a ticker as off-limits. An excluded ticker is *never traded by the app*: no new cash-secured puts, no covered calls, never sold/liquidated, and — critically — **never adopted into `wheel_holdings`**. This covers the main use case: a stock you bought outside the app as a long-term hold that the wheel would otherwise pick up (via Saturday assignment detection or the Monday Step-0 sync) and start writing covered calls on. Existing shares and premium are left untouched; unchecking restores normal handling. The exclusion is enforced across the CSP screener (`get_top_targets`), both wheel adoption paths (`detect_assignments` + `run_wheel_check` Step 0), the per-holding wheel loop, and the daily risk monitor. The retention/skip screener set (`get_all_candidates`) is deliberately left unfiltered so an excluded held name is never misread as "dropped from screener" and force-sold. New endpoint: `POST /api/excluded-tickers` (single-ticker toggle); `GET /api/positions` now returns `excluded_tickers`.
- **Current price column in the IBKR Holdings table** — the dashboard holdings table now shows the live market **Price** next to **Avg Price**, so entry cost and current price sit side by side. The value (`marketPrice`) was already computed by the API per position; it's now surfaced.
- **Implied volatility at execution ("Entry IV")** — covered calls and cash-secured puts now record the option's implied vol at fill, captured from the same greeks snapshot used for the live delta check (falls back to the screener's ATM IV if IBKR returns no IV). Shown as an **Entry IV** column in the IBKR Holdings table (next to Entry δ) and on the Open Positions cards, matching how Entry δ and Buffer are displayed. Persisted to `trade_log.json` as `iv_at_entry` and returned via `GET /api/positions`; historical/backfilled rows use the screener's `iv_atm`.

## [4.3.0] — 2026-06-22
### Added
- **Min OI Notional slider in the Liquidity Filters settings card** — the open-interest floor (added in 4.2.0) is now tunable from the dashboard ($250K–$5M, default $1M) instead of being a code-only constant. Hot-reloads like the spread thresholds.
### Changed
- **CCs are now written *through* earnings by default** — `wheel_cc_ignore_earnings_filter` now defaults to **true**. Previously a wheeled holding with earnings inside the Mon–Fri window was force-sold at market to dodge the gap; the wheel's intent is to keep the shares and keep collecting premium, so the default now keeps the position and writes the covered call. Set the toggle OFF to restore the old sell-before-earnings behavior. No effect on new CSP entries. NOTE: boxes that previously saved this setting OFF keep their saved value — flip the toggle ON and Save once after upgrading to adopt the new default.
- **Dropped the redundant word "Filter" from settings labels** — the section already groups them as Filters, so "Earnings Filter" → "Earnings Window" and "Ignore Earnings Filter for Wheel CCs" → "Ignore Earnings for Wheel CCs".

## [4.2.0] — 2026-06-22
### Changed
- **Open-interest gate is now notional-based, not a flat contract count** — replaced `MIN_OPEN_INTEREST = 100` with a price-neutral floor: `OI × strike × 100 ≥ min_oi_notional` ($1M default) plus a small absolute `min_oi_floor` (10) to reject totally-dead strikes. A flat contract count penalized high-strike underlyings — the same dollar liquidity shows up as fewer contracts on a $300 name than a $30 one. Live example: BE $302.50 had OI 76 (= $2.30M notional) and was wrongly skipped, while CRDO $265 at OI 115 (= $3.05M) passed only because of its lower strike; BE the underlying is liquid (OI 3,808 on the prior week's expiry — the low count was a fresh-weekly artifact). Thresholds hot-reload from settings.
### Fixed
- **Discord results card mislabeled open-interest skips as "spread too wide"** — an `oi` skip reason fell through to the default skip label and reported "skipped — spread too wide", hiding the real cause (this is what misdiagnosed a BE skip). It now reads "open interest too thin (OI N, $X notional)" with a matching footnote.

## [4.0.0] — 2026-06-21
### Added
- **In-app alert feed — the dashboard is now self-observing (v4 foundation)** — until now every operational alert (gateway down, API wedge, login failed/locked, auto-restarts, weekly results) went *only* to Discord; if the webhook was unconfigured or you weren't in that channel, the app itself told you nothing. v4 makes the dashboard a first-class, standalone record. Every alert that flows through the single `_send_discord_alert` chokepoint (all ~24 call sites, no per-site changes) is now also persisted to a capped ring buffer (`/data/alerts.json`, last 200) and surfaced via a bell in the status bar with an unread badge colour-coded by severity (🚨/❌/🔒 critical, 🔄/⚠️ warning, ✅ resolved, derived from the emoji each alert already carries). The bell dropdown shows recent history newest-first with relative timestamps; opening it marks the feed seen (unread tracked per-browser in localStorage, so the api never writes on a plain view), and a Clear action wipes history. New endpoints: `GET /api/alerts` (newest-first + `latest_id` for the client-side unread count) and `DELETE /api/alerts`. The in-app record happens first and unconditionally, so the feed works even with Discord down or unconfigured — Discord and the in-app feed are complementary (push when away vs. context when in the app), not either/or.
- **Standalone by design** — each box keeps its OWN feed; there is no cross-box aggregation or central management box. Live and the paper boxes run on different IBKR accounts and must not see each other — per-box feeds keep the security and ops boundary clean. File-backed (not in-memory) so the history survives the api restarts that happen on trading-mode switches and upgrades, which is exactly when you'd want it.

## [3.9.22] — 2026-06-21
### Added
- **Watchdog self-heals IBKR's routine token-expiry dialog via a capped full restart** — periodically (often over weekends, account-keyed so every box trips at once) IBKR invalidates the auto-login session token and the gateway parks on *"The security tokens associated with your login credentials have expired … Please manually enter your username and password."* The API port stays refused with the gateway `LOGGED_OUT`. The v3.9.21 IBC soft restart can't clear this — it reuses the dead session token and relaunches into the same dialog — so the watchdog used to just page for a manual restart. The gateway-port-down branch now self-heals: on a persistent outage it fires **one** full `docker restart ib_gateway` (gateway container only, so the api-hosted watchdog stays alive to confirm recovery), which re-runs IBC's login from scratch and clears the dialog. Validated on YRVIP paper 6/21: gateway wedged on this dialog for 2 days → gateway-only full restart → "Login has completed" with no 2FA → API port back. **Paper** re-logs in automatically (no 2FA); **LIVE** advances to the IB Key 2FA prompt and the alert tells you to approve on your phone. The handshake-dead branch also escalates a failed soft restart to one full restart before paging.
- **Lockout guard on auto full-restart** — a repeated restart loop would be a string of fresh login attempts and could lock the IBKR account (worse on live). Two guards make that structurally impossible: at most **one** full restart per outage episode (the episode only resets after the API actually reconnects, i.e. a clean login that resets IBKR's attempt counter), plus a 30-minute cross-episode cooldown. `locked` / `failed` (wrong-password) / exit-4 (version mismatch) / scheduled-restart-window states never auto-restart — they page as before.

## [3.9.20] — 2026-06-18
### Fixed
- **Trading-mode switch wrote `.env.compose` to the wrong (ephemeral) path** — `/api/trading-mode` synced `.env.compose` via `BASE_DIR / ".env.compose"`, but inside the container `BASE_DIR` is `/app`, so it only updated the container-local copy and left the bind-mounted host file (`/host_repo/.env.compose`) stale. After switching the live mini to live, the host `.env.compose` still read `paper`/`4004`. Harmless in normal operation because the shared entrypoint always re-derives the port from `/data/gw_trading_mode` (the real source of truth), but it left the `.env.compose` fallback wrong — so a plain `docker compose up` before the durable file existed would silently land on paper. The sync now targets `/host_repo/.env.compose` when present (matching the upgrade / reset-gateway code paths), falling back to `BASE_DIR` for non-containerized dev.

## [3.9.19] — 2026-06-18
### Fixed
- **Trading-mode switch left the api on the old port (screener "Run Now" → connection refused)** — switching paper↔live via the dashboard wrote `/data/gw_trading_mode`, synced `.env.compose`, and restarted the gateway and scheduler, but never restarted the **api** container. Like the scheduler, the api caches `IBKR_PORT`/`TRADING_MODE` from its env at import (`config.py`), so it kept dialing the previous mode's port. After switching the live Mac mini to live (port 4003), the screener pipeline run from the dashboard still connected to the paper port and failed with `[Errno 111] Connect call failed ('172.18.0.4', 4004)`; the scheduler (correctly restarted) was on 4003 the whole time, so Monday trading was unaffected — only on-demand api actions broke. The `/api/trading-mode` endpoint now also restarts the api via a `BackgroundTask` (`_restart_api_self`), deferred ~1.5 s so the HTTP response reaches the client before the container tears down. The shared entrypoint re-derives the correct port from `/data/gw_trading_mode` on the restart, so the api comes back on the new mode's port. The api briefly goes away (~a few seconds) after a mode switch while it restarts — expected.

## [3.9.18] — 2026-06-15
### Fixed
- **Scheduler race dropped CC premium and over-filled CSP slots** — the Monday wheel check (exec−5 min) and the CSP pipeline (exec time) ran as two independent cron jobs only 5 minutes apart. When the wheel check actually sells covered calls it runs the full order-escalation ladder and can take 6+ minutes; on Mon 6/15 (paper) it started 09:55 and didn't finish until 10:01:27, while the CSP pipeline fired at 10:00:00 and read `monday_context` from `state.json` 87 s before the wheel check finished writing it. The pipeline therefore saw the *stale* context (`cc_premium=0`, `active_wheel_count=0`, `reserved=0`) and (a) wrote `weekly_pnl` with `cc_premium=0`, hiding $3,529 of real CC premium from the week's total ($4,631 reported vs $8,160 actual), and (b) treated 0 positions as already deployed — filling 5 new CSP slots instead of 3 (5-position cap − 2 wheel holdings) and sizing them against full `net_liq` instead of holding back the $92,500 reserved for the IONQ/QBTS stock, leaving the account at 7 positions vs the 5 cap. The two jobs are now a single chained job (`run_pipeline` runs the wheel check inline and feeds its in-memory result straight to the CSP pipeline — the same sequence as `monday_runner.run_monday` / the dashboard's Run Now), so the pipeline can never start before the wheel check's results exist. No state.json hand-off, no race.
- **Weekly results never posted to Discord (`KeyError: 'yield_pct'`)** — `post_weekly_results()` formatted the YTD "Best Week"/"Worst Week" lines with a hard subscript `best['yield_pct']` / `worst['yield_pct']`, but week entries written before the `yield_pct` field was added only carry `premium_collected`/`total_realized`. When the best/worst week landed on one of those legacy entries (best = 2026-05-18, worst = 2026-05-11), the lookup raised `KeyError: 'yield_pct'` and the entire weekly results embed was aborted — logged only as `⚠️ Discord weekly results post failed: 'yield_pct'`. Because both the paper Mac mini and the live MacBook share the code and both carry pre-`yield_pct` weeks, neither posted Mon 6/15 results even though the CSPs filled normally (paper 5/5, CSP $4,631). The premium lookups on the same lines already fell back to the old `realized` key; the yield now uses a matching `_week_yield()` fallback that recomputes from the week's premium when `yield_pct` is absent, so the post never crashes. No trade data was lost — `state.json`/`ytd_tracker.json` recorded the week correctly; only the notification was missing.

## [3.9.17] — 2026-06-15
### Fixed
- **Held names force-sold over a borderline put delta (false "dropped screener")** — the wheel check's retention test (`get_all_candidates()`) applied the full *entry* filter set, including the `MAX_DELTA` 0.21 put-delta cap and the 5% buffer floor. Those are strike-selection criteria for opening a *new* CSP, not signals about whether a company we already hold is still wheel-worthy. On Mon 6/15 (paper) this flagged IONQ as "dropped from screener" and planned to sell 700 sh at a ~$2,142 loss purely because its 20-delta put came in at −0.219 vs the 0.21 cap — even though IONQ was still `Wheel-ready` and passed market-cap, buffer (8.4%), DTE, and earnings. Added a `retention=True` mode that skips the entry-only delta/buffer filters (mirroring the existing `market_cap_min` retention override); `wheel_manager` and `risk_manager` now use it for held-position checks so a held name is only dropped on genuine deterioration, not strike-granularity noise.

## [3.9.16] — 2026-06-15
### Fixed
- **Covered calls silently skipped on holiday-shortened weeks (Juneteenth)** — `wheel_manager._next_friday_expiry()` always returned the nominal Friday (e.g. `20260619`) with no holiday awareness. When that Friday is a market holiday the weekly options roll their expiration back to Thursday, so the chain lookup asked IBKR for a date it never lists, `_find_cc_strike()` returned `CC_NO_DATA`, and the fail-safe deferred the CC — mislabeling the cause as "market closed" even though the market was open. On Mon 6/15 this skipped both live CCs (QBTS, ASTS); they were sold manually after the fix for $522 total premium. The CSP path was unaffected because it uses the screener's actual tradable expiry. The expiry now rolls back to the prior trading day whenever it lands on a market holiday (via existing `market_calendar.is_market_holiday()`). Next affected week without this fix would have been Jul 3 (Independence Day observed).
- **CC deferral no longer falsely blames "market closed"** — the `CC_NO_DATA` deferral path (wheel log + Discord) hard-coded "market closed" as the reason, which masked the real cause above (e.g. an unlisted holiday expiry) and made this look like a live data-feed outage. It now reads "CC could not be priced — see reason above / deferred"; `_find_cc_strike()` already logs the specific cause.

## [3.9.9] — 2026-06-09
### Fixed
- **Scheduler dropped Monday's run when the host briefly slept** — the live MacBook Pro runs lid-closed and kept entering macOS "Maintenance Sleep" even on AC, freezing the Docker VM for stretches of seconds to ~18 min. While suspended the scheduler couldn't run, so the heartbeat went stale (every "stale/resumed" Discord alert) and, on Mon 6/8, the box was asleep across the 07:20–07:45 trade window. With APScheduler's default `misfire_grace_time` of 1s, the Discord preview, wheel check, and CSP execution were all silently skipped — no trades ran. The `BlockingScheduler` now uses `job_defaults={"misfire_grace_time": 1800, "coalesce": True}` so a job whose fire time is missed during a brief host suspend still runs on wake (bounded to 30 min so an overnight/weekend sleep can't fire a trade hours late). Host-side fix (disable sleep) applied separately on the live machine.

## [3.4.6] — 2026-06-04
### Fixed
- **Windows setup: secrets browser never opened** — `setup_docker.sh` Step 2 launched the secrets page with `cmd.exe /c start "$URL"`, but Windows `start` treats the first quoted argument as the window title, so it opened an empty window instead of the browser. With no timeout on the secrets wait loop and no CLI fallback, Windows setup appeared to hang at Step 2. Now uses `start "" "$URL"` (empty title) on both the Git Bash and WSL paths so the browser actually launches. Confirmed live on a fresh GEEKOM A5 (Win 11 Pro).

## [3.4.2] — 2026-06-02
### Fixed
- **Weekly IB Key Token panel hidden for paper accounts** — paper logins don't use IB Key 2FA, so the token status, "No weekly token yet" warning, and Refresh Weekly Token button no longer appear in paper mode. Instead a short note explains it applies to live accounts only. The Auto-Update and Reset Installation 2FA warnings are likewise shown only in live mode, since paper restarts never require approval.

## [3.4.1] — 2026-06-02
### Fixed
- **Weekly token "Next reset" mislabeled "ET"** — the reset time was correctly converted to the viewer's local timezone but the label said "ET". It now renders in plain local time matching the "established" line (e.g. "Sat, Jun 6 at 10:00 PM"), with no timezone suffix.
- **"Gateway restarting — check your phone" notice no longer lingers** — the Refresh Weekly Token success message now auto-clears once the token re-establishes, instead of sitting next to the active status.

### Added
- **Auto-Update 2FA warning** — Settings → Software Updates now warns that every update restarts IB Gateway and requires a fresh IB Key 2FA approval. With Auto-Update on, that prompt fires unattended at 3 AM; if unapproved, the gateway stays logged out and trading pauses. Confirmed live: the dashboard upgrade rebuilds the ib_gateway image and recreates the container, so a 2FA challenge fires on every upgrade.
- **FAQ entry** explaining why updates ask for IB Key 2FA and why Auto-Update is risky for unattended (3 AM) runs.

## [3.4.0] — 2026-06-02
### Added
- **Weekly IB Key 2FA token status** — Settings → IB Gateway now shows whether this week's authentication token is active, when it was established, and when the next reset is due (~Sunday 1 AM ET). The API log monitor watches the IBC `autorestart file found/not found` lines and persists the establishment timestamp to `/data/weekly_token_established`.
- **Refresh Weekly Token button** — manually restart IB Gateway to trigger the IB Key phone approval at a convenient time instead of waiting for the nightly auto-restart. Enabled only while this week's token is not yet active; shows a "waiting for IB Key approval…" state and flips to ✅ once login completes. Backed by `POST /api/gateway/refresh-token`.
- **Reset Installation 2FA warning** — the Reset Installation flow now warns that wiping the settings volume also clears the weekly auth token, so a fresh IB Key approval will be required on the next restart.

## [3.0.0] — 2026-05-30
### Added
- **Live trading support** — YRVI now connects to real IBKR live accounts in addition to paper trading. Switch between modes from the Settings page with a single toggle.
- **Paper ↔ Live toggle** — Settings page toggle with CONFIRM dialog, masked account preview, and Discord alert on every mode switch. Gateway restarts automatically with the correct credentials for the selected mode.
- **Live credentials via secrets container** — `tws_userid_live`, `tws_password_live`, and `account_live` are stored in the encrypted secrets container alongside paper credentials. No `.env` file changes required.
- **Green live mode indicator** — header pill and Settings badge turn green + pulse when live trading is active. "Switch to Live" button remains red as a deliberate warning for the action.
- **Amber live mode warning bar** — Settings page shows "Live mode active — all trades use real money" in high-contrast amber (light and dark mode).
- **Reset Installation button always visible** — moved from diagnostics-only to a permanent button in Settings → IB Gateway section for easy access during recovery.
- **Account-scoped positions** — dashboard IBKR Holdings now filters by the configured account, preventing positions from other accounts under the same IBKR login from appearing.
- **Unrealized/Realized P&L display fix** — zero values now show as `+$0.00` instead of "Live account only."

### Requirements
- **IB Key required for live trading** — SMS 2FA is not supported for unattended automation. Enable IB Key (push notification) via the IBKR Mobile app before switching to live. First live login requires a one-time phone approval via VNC; nightly auto-restarts need fresh approval only once per week (Sunday session reset).

### Fixed
- **Live password read from secrets, not env var** — `IBKR_PASSWORD_LIVE` env var check replaced with `get_secret("tws_password_live")` for consistency with all other credentials.
- **Gateway restart uses docker, not launchctl** — `_restart_ibgateway()` now calls `docker restart ib_gateway` (works inside the API container) instead of `launchctl` (macOS host only).
- **Trading mode written to shared volume** — gateway entrypoint reads `/data/gw_trading_mode` override so the correct account credentials are fetched on restart without rebuilding the image.
- **IBKR port uses socat ports** — API connects on 4003 (live) and 4004 (paper) — the socat listener ports — not the raw Gateway ports 4001/4002.

## [2.2.28] — 2026-05-29
### Fixed
- **Startup script no longer reports a false NO-GO on cold start** — `startup.sh` was counting 4 container-not-found failures before running `setup_docker.sh`, then never subtracting them after setup succeeded. The script now re-checks each container after setup and flips fail→pass, then waits up to 60 s for the API to be ready before the health check runs. Result: a clean cold start now shows 15 passed / 0 failed / GO instead of 5 phantom failures.
- **Settings page "Reset to Defaults" now resets all fields** — several settings fields (e.g. Gateway restart time, suppress window) were not included in the reset payload, leaving stale values after a reset. All configurable settings are now covered.

## [2.2.27] — 2026-05-28
### Changed
- **Auto-update test bump** — version bump used to verify end-to-end auto-update flow.

## [2.2.26] — 2026-05-28
### Changed
- **Auto-update delegates git pull + rebuild to API** — scheduler-triggered auto-update now calls `POST /api/version/upgrade` instead of running git and Docker commands in-process, keeping the upgrade path consistent with the manual upgrade button.

## [2.2.25] — 2026-05-28
### Added
- **Scheduled auto-update (opt-in)** — new Settings toggle to automatically pull and rebuild the latest version on Wednesday–Friday at 3 AM; off by default. `auto_update_enabled` added to `settings_default.json`. Scheduler checks at startup and registers/removes the nightly job accordingly.

## [2.2.24] — 2026-05-28
### Fixed
- **Reset Installation no longer fails with volume-in-use error** — the API now stops dependent containers before removing volumes, then removes the gateway container last, preventing Docker "volume is in use" errors during a reset.

## [2.2.23] — 2026-05-28
### Changed
- **Version number appended to all Discord alerts** — every outgoing Discord notification (watchdog, trade, weekly summary) now includes the running version so it's easy to correlate alerts with releases in the log.

## [2.2.22] — 2026-05-28
### Fixed
- **Smarter watchdog Discord alerts for gateway failures** — watchdog now inspects the gateway container state before alerting; distinguishes between container-stopped vs. IBKR-connection-lost scenarios and sends appropriately worded messages for each.

## [2.2.21] — 2026-05-28
### Added
- **One-click Reset Installation** — new button in the System Diagnostics panel (Help page) that stops all containers, removes the `yrvi_data` volume, and re-runs setup; designed to recover from IB Gateway version-mismatch errors that require a clean reinstall of the gateway image.
- `POST /api/reset-installation` endpoint orchestrates the full teardown and delegates to `setup_docker.sh`.

## [2.2.20] — 2026-05-28
### Fixed
- **Diagnostics log snippet chevron defaults to closed** — the expandable log snippet in the Help → System Diagnostics section now starts collapsed so it doesn't push other content off-screen on load.

## [2.2.19] — 2026-05-28
### Fixed
- **Gateway logs always shown in diagnostics** — the `GET /api/diag` endpoint now always fetches the last 30 lines of the gateway container log, regardless of whether the gateway appears healthy, so login errors and version-mismatch messages are visible in the Help page even when the container is running.

## [2.2.18] — 2026-05-28
### Changed
- **Auto-expand log snippet when present** — if `GET /api/diag` returns a gateway log snippet, the Help page now expands the log accordion automatically so members see the relevant lines immediately without an extra click.

## [2.2.17] — 2026-05-28
### Fixed
- **Detect IBC's actual bad-password dialog string** — gateway entrypoint log scan now matches the exact string IBC emits for a wrong password (`"Invalid password"`) in addition to the previously detected lockout strings, so password errors are caught before a lockout occurs.

## [2.2.16] — 2026-05-28
### Fixed
- **IB Gateway check gracefully handles ib_insync connect failures** — `GET /api/diag` now catches `ConnectionRefusedError` and similar exceptions when probing the IBKR API port and downgrades the result to a warning rather than a 500 error, so diagnostics still return usable status when the gateway is mid-restart.

## [2.2.15] — 2026-05-28
### Added
- **Richer IB Gateway diagnostics** — `GET /api/diag` now returns `gateway_container_state` (running/exited/missing), `gateway_log_snippet` (last 30 log lines), and `ibkr_login_status` (connected/login_failed/no_data). Help page renders these with color-coded badges and an expandable log panel.

## [2.2.14] — 2026-05-27
### Fixed
- **Upgrade endpoint resets tracked files before git pull** — `POST /api/version/upgrade` now runs `git checkout -- .` before pulling so stale local modifications to `VERSION` or other tracked files never cause the pull to abort with "local changes would be overwritten".

## [2.2.13] — 2026-05-27
### Fixed
- Version bump (no functional change — internal release tracking).

## [2.2.12] — 2026-05-27
### Fixed
- Version bump (no functional change — internal release tracking).

## [2.2.11] — 2026-05-27
### Fixed
- Version bump (no functional change — internal release tracking).

## [2.2.10] — 2026-05-27
### Added
- **Windows setup guide** — [WINDOWS_SETUP.md](./WINDOWS_SETUP.md) added for GEEKOM A5 and equivalent Windows Mini PCs; covers Git Bash, Docker Desktop, auto-login, Remote Desktop, and full live trading setup.
- `setup_windows.ps1` deprecated — `setup_docker.sh` is now the single setup entry point for macOS and Windows.
- Windows Mini PC added to the live trading hardware table in README; "Windows paper-only" restriction removed.

## [2.2.9] — 2026-05-26
### Fixed
- **Open position cards now show a true execution-time snapshot** — Price, Buffer, Delta, and Yield all reflect the exact state when the option was sold, not Saturday's screener snapshot.
  - `trader.py` fetches the live underlying stock price from IBKR immediately after a fill via new `_get_stock_price()` and stores it as `stock_price_at_entry` in both `state.json` executions and `trade_log.json`. `buffer_pct_at_entry` is now computed from this live price (was previously using the screener's Saturday price).
  - `api.py` joins `trade_log.json` once and surfaces `stock_price_at_entry` and `buffer_pct_at_entry` into the `/api/positions` response (previously these were only used for the IBKR Holdings table).
  - `PositionCard.jsx` prefers execution-time values (`stock_price_at_entry`, `buffer_pct_at_entry`) over screener values (`latest_price`, `buffer_pct`), with graceful fallback to screener data for positions from before this release.

## [2.2.8] — 2026-05-26
### Fixed
- **Open positions card shows actual fill yield, not screener yield** — "Yield" on the Dashboard position card was displaying the screener's projected yield (e.g., 2.21% for BE) rather than the actual collected yield at fill (e.g., 0.89%). The API now computes `fill_yield_pct = fill_price / strike × 100` from the execution record. The card label changes from "Yield" to "Act. Yield" for filled positions to make the distinction explicit. Unfilled/skipped positions still show screener yield.
- **Position card "Delta" now shows entry-time Greek delta** — the stats grid previously showed the screener's planned delta (`pos["delta"]`). It now prefers `delta_at_entry` (the IBKR-measured delta at execution time) from the enriched execution record, falling back to the screener delta when not available. Column renamed "Entry δ" to match the dashboard IBKR Holdings table.
- **`delta_at_entry` included in API `/api/positions` response** — was previously only written to `trade_log.json`; now also surfaced in the enriched execution data returned by the positions endpoint.

## [2.2.7] — 2026-05-26
### Fixed
- **Strike upward adjustment when stock rallies** — `verify_and_adjust_strike` now handles both directions. Previously it only scanned *downward* when a stock fell and delta exceeded the 0.21 ceiling. If a stock rallied between Saturday and Monday (as with BE this week), the screener strike's delta could drop far below target (e.g., 0.13) and the system would execute there anyway. Now a `MIN_DELTA = 0.15` floor triggers an upward scan: the system walks up the option chain to find the highest strike still within the 0.21 cap, maximising premium while staying within risk limits.

### Added
- **Delta shown in Discord execution results** — each filled trade now displays the screener (planned) delta alongside the execution delta in `δplan→actual` format (e.g., `δ0.20→0.13`). Makes it easy to spot drift between Saturday screener and Monday open. The `delta_at_entry` field is now written into `state.json` executions (it was previously only in `trade_log.json`).

### Changed
- **Dashboard "Delta" column renamed to "Entry δ"** — clarifies that the value shown is the Greek delta of the option at entry time, not a plan-vs-actual price difference.

## [1.23.0] — 2026-05-24
### Changed
- **Apply to Gateway now restarts the container immediately** — writes the new restart time to a shared volume file (`/data/gw_auto_restart_time`), then issues `docker restart ib_gateway`; gateway entrypoint reads the override on every startup so the change persists across future restarts without editing `.env.compose`
- Gateway entrypoint (`docker/ib-gateway/entrypoint.sh`) reads `/data/gw_auto_restart_time` override before starting IBC, exporting it as `AUTO_RESTART_TIME`
- `yrvi_data` volume now mounted into `ib_gateway` container at `/data` so the override file is accessible to the entrypoint
- Apply to Gateway returns immediately (restart runs in background thread, ~30–60s); success message updated to reflect that the gateway is restarting

## [1.22.0] — 2026-05-24
### Added
- **IB Gateway settings UI** — new Settings section with a daily auto-restart time picker (7 PM – 2 AM), a restart window slider (10–60 min), and an "Apply to Gateway" button that patches IBC `config.ini` in the running container via `docker exec`
- `POST /api/gateway/patch-restart-time` — patches `AutoRestartTime` in IBC `config.ini` inside the running gateway container
- `auto_restart_time` and `auto_restart_suppress_mins` added to `settings.json` schema and `SettingsUpdate` model; settings take effect on the next watchdog poll with no container restart
- `AUTO_RESTART_TIME` and `AUTO_RESTART_SUPPRESS_SECS` added to `x-python-env` anchor in `docker-compose.yml` so the api container inherits them alongside the gateway

### Fixed
- **Watchdog alert messages are now context-aware** — failures within the configured restart window say "likely the daily restart — a ✅ recovery message will follow" instead of "manual restart required"; failures outside the window keep the original urgent wording
- Removed "2FA or confirmation dialog" assumption from IBKR connection-failure alerts — paper accounts have no 2FA; message now says "slow to reconnect or stuck on a login dialog"
- **Recovery messages now paired with alerts** — `✅ restored` only fires if a prior `🚨` alert was sent for that episode; eliminates phantom recovery messages when the restart window suppresses the outgoing alert

## [1.18.0] — 2026-05-20
### Fixed
- Upgrade reconnect polling timeout increased from 15s to 120s — Docker image builds take 1-2 minutes before containers restart; 15s was triggering a false "Upgrade problem" error while the build was still running
- Upgrade modal phase labels updated to set clearer expectations ("Building & restarting — this takes 1–2 minutes…")

## [1.17.0] — 2026-05-20
### Fixed
- Version badge click now shows feedback in all cases: "✓ Up to date" (green) when current, "Unable to reach GitHub" (gray) if the check fails — previously silent on both

## [1.16.0] — 2026-05-20
### Added
- Clicking the version badge when already up to date briefly flashes "✓ Up to date" in green for 2 seconds — confirms the check ran

## [1.15.0] — 2026-05-20
### Added
- Version badge in status bar is now clickable — click to immediately check for updates; shows a spinner while checking and reveals the Upgrade button if a new version is found

## [1.14.0] — 2026-05-20
### Fixed
- Feedback sender lookup now uses correct secret key `tws_userid_paper` (was `tws_userid`); falls back to `tws_userid_live` then `account_paper` — eliminates "root" showing up from Docker home directory

## [1.13.0] — 2026-05-20
### Changed
- Feedback Discord post now identifies the sender: IBKR username (`tws_userid`) → live username (`tws_userid_live`) → home directory name → "Unknown"

## [1.12.0] — 2026-05-20
### Changed
- Feedback webhook ships with a hardcoded default (`#yrvi-app-feedback`) — works out of the box with no manual secret setup; overridable via `discord_feedback_webhook_url` secret

## [1.11.0] — 2026-05-20
### Added
- Help page feedback form — Bug Report / Feature Request selector + textarea posts directly to `#yrvi-app-feedback` Discord channel via `/api/feedback`; no Discord account needed, never leaves the dashboard
- `/api/feedback` endpoint reads `discord_feedback_webhook_url` secret and posts formatted message with version, mode, and timestamp
- `FeedbackRequest` Pydantic model

## [1.10.0] — 2026-05-20
### Added
- Help page (sidebar, pinned below a divider) with three sections: System Diagnostics, FAQ & Troubleshooting, Report a Bug / Feature Request
- `/api/diag` endpoint — fast read-only health check (scheduler heartbeat age, IB Gateway TCP reachability, last CSP run, last wheel check, market status today, version); no IBKR API calls
- `_next_execution()` in `api.py` now accounts for market holidays — status bar shows Tuesday when Monday is a holiday

## [1.9.0] — 2026-05-20
### Added
- `market_calendar.py` — NYSE holiday calendar computed dynamically from rules (no external dependencies); covers all 10 federal/NYSE holidays including Saturday/Sunday observation shifts
- Scheduler now skips all Monday trading jobs (wheel check, CSP pipeline, Discord preview) on market holidays and automatically shifts execution to Tuesday — handles Memorial Day, Labor Day, MLK Day, Presidents' Day, etc.
- Friday assignment detection skips on Good Friday; daily risk monitor skips on any market holiday (e.g. Thanksgiving Thursday)
- Startup log updated to reflect Mon/Tue firing window with holiday-shift note

## [1.6.0] — 2026-05-17
### Added
- Wheel holdings with active stock positions now appear as covered call (CC) entries in the weekly plan — screener switches from CSP to CC for held tickers, using `call_20d_strike` / `call_20d_premium` from the Render API
- Render API (`/api/targets/csp`) now returns `call_20d_strike`, `call_20d_premium`, `call_20d_premium_pct`, `call_20d_delta`, `call_20d_iv` for every ticker (data was in `iv_summary` but not selected)
- `get_top_targets()` accepts `always_include` set — held tickers are guaranteed in the screener results even if they score below the top-N cutoff
- This Week page shows INTC-style CC rows with $0 capital, contracts fixed by shares held, and CC strike/premium
### Fixed
- Weekly plan position sizer now sizes the top (remainder) position first, iterating past ineligible tickers like SNDK (too expensive per contract), so the best viable target (e.g. BE) gets the max allocation instead of a normal-sized slot
- CC positions no longer consume CSP slot count — `num_positions` for CSP sizing is no longer reduced by active wheel holding count; budget deduction already handles the capital constraint
- `discord_poster.py` webhook URLs now read via `secrets_client.get_secret()` instead of `/run/secrets/` file mounts, which no longer exist after the compose cleanup in v1.5.0
- `docker-compose.yml` file-based Docker secret declarations removed — all secrets served by the `yrvi-secrets` HTTP service; eliminates bind-mount errors on hosts without local secret files
- `held_map` definition moved before `get_top_targets()` call in `api.py` (was referenced before assignment, causing `NameError` on This Week screener runs)
- Stale `targets` variable reference in `api.py` screener return renamed to `all_targets`

## [1.5.0] — 2026-05-15
### Added
- feat: enrich IBKR Holdings table with execution metadata (delta, buffer %, premium/contract, total premium)
- feat: capture trade_log.json at fill time for CSPs and CCs
- feat: backfill current positions from state.json where recoverable
- feat: /api/positions enriched with trade_log join

## [1.4.5] — 2026-05-11
### Added
- Shutdown button on Settings page with confirmation dialog
- `POST /api/shutdown` stops all YRVI containers (scheduler, web, ib_gateway, secrets, api last)
### Fixed
- Restart Scheduler now works in Docker mode (docker.sock mount)
- `POST /api/restart-scheduler` no longer returns HTTP 501 — runs `docker restart yrvi-scheduler-1`
### Changed
- `Dockerfile.api` installs static Docker CLI from download.docker.com (arch-aware, ~70MB)
- `docker-compose.yml` api service mounts `/var/run/docker.sock:ro` (note: read-only flag doesn't restrict Docker API commands)

## [1.4.4] — 2026-05-11
### Added
- Timezone dropdown on Settings page (6 US timezones)
- POST /api/settings/timezone validates the IANA name and persists to settings.json
- Scheduler reads timezone from settings.json at startup (falls back to TIME_ZONE env, then America/Los_Angeles)
- Closes #3

## [1.4.3] — 2026-05-11
### Changed
- tws_password_live moved to optional — paper-only members no longer blocked at setup

## [1.4.2] — 2026-05-11
### Fixed
- Secrets page now dynamically renders all secrets from API (was hardcoded to 5, now shows all 10)
- New Account Info section showing IBKR account ID and username fields

## [1.4.1] — 2026-05-11
### Fixed
- IB Gateway: stop retrying login on failure to prevent IBKR account lockout (`LoginFailed=terminate` patched into IBC config at startup, searches common paths and logs which one was patched)
- IB Gateway: credential preflight check before IBC starts — exits cleanly with a Discord alert if `tws_userid_*` or `tws_password_*` is missing from the secrets container
- IB Gateway: lockout dialog detection — case-insensitive match on "locked out", "excessive number of failed login attempts", "PASSWORD NOTICE", "Login failed"; sends Discord alert and halts container on any match
- IB Gateway entrypoint wraps the gateway in a monitored runner (instead of `exec`) so log lines can be observed; the gateway's own exit code is still propagated
- Note: docker-compose.yml `restart: "no"` (set in v1.3.x) retained — most defensive against lockout

## [1.4.0] — 2026-05-11
### Added
- Account credentials managed via secrets container UI at `http://localhost:8001` — IBKR account ID, IBKR username, and VNC password no longer require `.env.compose` edits
- New secret keys: `account_paper`, `tws_userid_paper` (required); `account_live`, `tws_userid_live`, `vnc_server_password` (optional)
- `setup.html`: new "Account Info" section above the existing passwords section, with helpful labels and hint text for each field
- `setup_docker.sh` CLI fallback now prompts for the 5 new secrets when the browser flow times out
- IB Gateway `entrypoint.sh` fetches `TWS_USERID`, `TWS_PASSWORD`, and `VNC_SERVER_PASSWORD` from the secrets container at startup; VNC falls back to `ibgateway123!test` when unset
### Changed
- `config.py`: `ACCOUNT` is now resolved via `secrets_client.get_secret` keyed by `TRADING_MODE` (paper vs live), with `ACCOUNT` env var as a fallback
- `api.py` `_live_ready()` checks live credentials via secrets container instead of env vars
- `docker-compose.yml`: removed the `:?` hard-fail injections for `ACCOUNT_PAPER` / `TWS_USERID_PAPER` and the `ACCOUNT_LIVE` / `IBKR_USERNAME_LIVE` env passthroughs for api/scheduler
- `docker/preflight.sh`: validates required secrets via the secrets-container `/secrets/status` endpoint and cross-checks that `account_paper` ≠ `tws_userid_paper`
### Removed
- `.env.compose` no longer carries `ACCOUNT_PAPER`, `TWS_USERID_PAPER`, `ACCOUNT_LIVE`, `TWS_USERID_LIVE`, `VNC_SERVER_PASSWORD`, `IBKR_USERNAME_LIVE`, `IBKR_PASSWORD_LIVE_FILE`
- Closes #1

## [1.3.1] — 2026-05-06
### Fixed
- README fully updated for v1.3.0 secrets container architecture
- Removed all stale macOS Keychain references from setup, security, and script-flow sections (11 references updated)
- docker/preflight.sh orphan secrets_dir variable removed

## [1.3.0] — 2026-05-06
### Added
- secrets container: AES-256-GCM encrypted secrets store (docker/secrets_service/)
- secrets_client.py: 3-tier secret resolution (secrets container → /run/secrets/ file → env var)
- SecretsPage in dashboard: manage all credentials via UI with status indicators and inline editing
- setup_docker.sh: browser-first secrets entry with CLI fallback, removes macOS Keychain dependency
- yrvi-restart.sh: verifies secrets container instead of re-injecting from Keychain
### Changed
- docker-compose.yml: added secrets service, updated depends_on for api/scheduler/ib_gateway
- api.py: /api/secrets/* proxy endpoints, secrets read via secrets_client
- scheduler.py: discord webhook read via secrets_client
### Removed
- macOS Keychain dependency — stack now works on Mac, Windows, and Linux
- --keep-secrets flag from setup_docker.sh and yrvi-restart.sh
- Plaintext secret file injection on restart

## [1.2.6] - May 2026
### Added
- Discord weekly results: footnotes explaining skip reasons appear at the bottom of the trades field when applicable — "Spread too wide" (liquidity check) and "Contract too large" ($70K max per position) only included if that skip occurred that week
- `trader.py` now records `skipped_contract_size` in execution results when a fallback candidate's single contract would exceed the $70,000 max position size
### Fixed
- "Failed Market Data" badge on This Week page — verified already resolved; `ThisWeek.jsx` was rewritten to a table/screener view and no longer makes live IBKR market data requests

## [1.2.5] - May 2026
### Added
- Performance page: "Total Realized" stat card showing combined option premium + forced-sale P&L
- Performance table: separate "Premium" and "Sales P&L" columns; footer shows net effect of forced sales
- Bar chart tooltip shows sales P&L alongside premium when a forced liquidation occurred that week
### Changed
- `ytd_tracker.json` now stores `premium_collected`, `shares_sold_pnl`, and `total_realized` per week (backwards-compatible: old entries with `realized` are normalized on read)
- YTD `total_premium`, weekly `yield_pct`, and annual goal progress track option premium only — forced liquidations no longer inflate these numbers
- Sidebar version label removed — status bar version pill is the single source of truth
### Fixed
- `yrvi-build.sh`: re-queries container ID after `docker compose up --build` so health polling in Step 4 inspects the live container instead of the pre-rebuild (stale) ID

## [1.2.1] - May 2026
### Added
- Discord `🚨🚨🚨 EMERGENCY SHARE SALE` alert fires immediately on every wheel share sale (dropped screener, earnings this week, no viable CC), with ticker, shares, fill price, proceeds, reason, and realized P&L
- `_find_cc_strike()` fetches live IBKR market price before building the strike candidate list; scan floor = max(assigned_strike, current_price × 0.95) to avoid scanning deep-ITM calls when a stock has run up
- `detect_assignments()` falls back to IBKR `avgCost` when `assigned_strike` is missing from state, eliminating 0.00 placeholders
- Same avgCost fallback applied in `run_wheel_check()` Step 0 for untracked stocks detected at Monday open

## [1.2.0] - May 2026
### Added
- scripts/yrvi-restart.sh: re-injects Keychain secrets before docker restart, fixing individual container restarts
- Watchdog auto-restarts ib_gateway after 30 min down outside market hours (never during 9:30–4:00 PM ET)
- auto_restart_gateway setting with dashboard toggle (default true)
### Changed
- Watchdog alerts distinguish auto-restart attempted vs manual intervention required
### Notes
- Drops beta label — first full stable release with live trading resilience features

## [0.1.0-beta] - April 2026
### Added
- Complete wheel strategy automation
- IBC auto-login
- React dashboard with dark/light mode
- Discord integration
- Live IBKR portfolio view
- Settings management
- Paper/Live trading toggle

## [1.1.0-beta] - April 2026
### Added
- macOS Keychain secrets management (replaces manual docker/secrets/ file creation)
- setup_docker.sh --paper / --live mode flags (required, replaces bare invocation)
- Password double-entry confirmation with character count on first run
- Ephemeral secret files — written at launch, deleted after containers start
- Secret rotation via Keychain Access.app (delete entry, re-run script)
- README: Hardware Requirements table, Security section, You Rock Club onboarding note
### Changed
- Steps 5 & 6 idempotent — login item and Desktop app skip if already installed
- Hardware tier policy: Mac Mini required for live trading, Windows scoped to paper only
- Versioning policy documented in README Version History

## [1.0.0-beta] - April 2026
### Added
- Docker containerization (replaces launchd)
- Cross-platform: Mac Intel/ARM + Windows
- Secrets management via macOS Keychain (ephemeral docker/secrets/ files)
- Auto-start via Docker login plist
- nginx serving React dashboard
- Socket-based health checks
- Scheduler heartbeat monitoring
### Changed
- setup_docker.sh replaces setup_ibc.sh
- startup.sh now checks Docker containers
