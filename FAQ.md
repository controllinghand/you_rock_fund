# YRVI Setup FAQ

Common issues and fixes for You Rock Club members setting up YRVI on a Mac Mini.

---

### Q: Docker fails with "address already in use" on port 5900

**A:** macOS Screen Sharing is binding port 5900 in a way that collides with IB Gateway's VNC port. (Normally they coexist — the gateway binds IPv4 `127.0.0.1:5900` while Screen Sharing answers on the LAN IP / IPv6 — but a boot-order race can occasionally cause this.)

Easiest fix — give the gateway its own port. In `.env.compose` set:
```
IB_GATEWAY_VNC_PORT=5901
```
then restart:
```bash
docker compose --env-file .env.compose down
./setup_docker.sh --paper
```
Now connect your VNC client to `127.0.0.1:5901` instead.

Alternatively, turn Screen Sharing off (**System Settings → General → Sharing → Screen Sharing → OFF**) and keep the gateway on 5900.

> For remote *terminal* access, use SSH: **System Settings → General → Sharing → Remote Login → On**, then `ssh [your-user]@[MAC_MINI_IP]`.

---

### Q: I need to check the IB Gateway screen (e.g. it's stuck on a 2FA or confirmation dialog)

**A:** There's now a **View Gateway** viewer built into the dashboard — no VNC client to download.

1. Open the dashboard (**http://localhost:3000**) → **Help** (left nav).
2. Under **System Diagnostics**, click **View Gateway**. A new browser tab opens.
3. Click **👁 Open viewer (view-only)** to watch the screen, or **⚠️ Enable keyboard / mouse control** if you need to click through a 2FA/confirmation dialog.

The password auto-fills from your `vnc_server_password` secret, so there's nothing to type. You'll see the IB Gateway GUI and can dismiss whatever dialog is blocking it. See [docs/view-gateway.md](docs/view-gateway.md) for details.

> Older versions told you to install RealVNC or TigerVNC and connect to `127.0.0.1:5900`. That's no longer needed — View Gateway is built in. (The raw VNC port is still there at `127.0.0.1:5900` if you ever want an external client as a fallback; on macOS use the literal `127.0.0.1`, never `localhost`.)

---

### Q: setup_docker.sh fails or containers won't start — I forgot to configure secrets

**A:** Since v1.4.0, account credentials are entered via the secrets container UI, not `.env.compose`. Re-run `setup_docker.sh --paper` — it will open `http://localhost:8001` in your browser. Enter at minimum:

| Field | What to enter |
|---|---|
| IBKR Paper Account ID | Your IBKR paper account ID (e.g. `DU1234567`) |
| IBKR Paper Username | Your IBKR paper username |
| IBKR Paper Trading Password | Your IBKR paper password |
| IBKR Live Trading Password | Your IBKR live password |
| Render Screener API Secret | Provided in onboarding |

Optional fields (only needed if you flip the stack to live mode or want a custom VNC password):

| Field | What to enter |
|---|---|
| IBKR Live Account ID | Your IBKR live account ID |
| IBKR Live Username | Your IBKR live username |
| VNC Password | Defaults to `ibgateway123!test` if unset |

If your browser doesn't open, paste `http://localhost:8001` into it directly. If the browser flow times out, the script falls back to terminal prompts.

---

### Q: Discord test notification fails — "webhook not configured"

**A:** The Discord webhook URL is configured through the secrets container UI — it is not set in `.env.compose` or as a file.

1. Open `http://localhost:8001` in your browser
2. Find **Discord Webhook URL** and click **Set**
3. Paste your webhook URL (get it from: **Discord → Edit Channel → Integrations → Webhooks**)
4. Restart the scheduler to pick it up:

```bash
cd ~/you_rock_fund
docker compose --env-file .env.compose restart scheduler
```

---

### Q: Docker doesn't start automatically after a reboot

**A:** Docker Desktop needs to be configured to launch at login:

**Docker Desktop → Settings (⚙️) → General → ✅ Start Docker Desktop when you log in**

Then reboot and confirm YRVI comes back up on its own.

---

### Q: The Mac Mini asks for a password on every reboot instead of auto-logging in

**A:** Automatic Login requires FileVault to be **off**. Check your status:

```bash
fdesetup status
```

If FileVault is on, turn it off:

**System Settings → Privacy & Security → FileVault → Turn Off**

This takes 30–60 minutes to decrypt. After it finishes, enable auto-login:

**System Settings → Users & Groups → Automatically log in as → [your user]**

> Note: Keeping your Apple ID is fine — FileVault is a separate toggle and can be disabled without removing your Apple ID.

---

### Q: Scheduler fails to restart with "no such file or directory: render_secret"

**A:** The Render API secret is missing from the secrets container. Open `http://localhost:8001`, find **Render Screener API Secret**, and click **Set**. Contact the fund operator for the value if you don't have it.

If the secrets container itself is not running, restart the stack first:
```bash
cd ~/you_rock_fund
docker compose --env-file .env.compose up -d
```

---

### Q: I get "couldn't find env file" when running docker compose commands

**A:** You're running the command from the wrong directory. Always run Docker commands from the repo root:

```bash
cd ~/you_rock_fund
docker compose --env-file .env.compose <command>
```

---

### Q: Where do I configure my credentials?

**A:** All credentials are managed through the secrets container web UI at `http://localhost:8001`. Run `setup_docker.sh --paper` and it will open automatically. The required fields are: IBKR Paper Account ID, IBKR Paper Username, IBKR Paper Trading Password, and Render Screener API Secret.

---

### Q: All trades show "Failed Market Data" / "market likely closed" even though the market is open

**A:** The IB Gateway connection is working but your paper account doesn't have options market data subscriptions enabled. The IBKR paper account inherits market data subscriptions from your live account — if options data isn't subscribed on the live account, the paper account won't see it either.

**How to confirm:** Run a manual test with `docker exec yrvi-scheduler-1 python trader.py`. If the log shows `Market data type: DELAYED (type 3)` and every ticker returns `⏰ No market data — market likely closed` during market hours, this is your issue.

**The fix — complete market data setup on your live account:**

1. Open a browser and log into the IBKR **Client Portal** on your **live account** (not paper)
2. Click the **person icon (top right) → Settings → Trading Platform → Market Data Subscriptions**
3. On that page, complete all three items by clicking the ⚙️ gear icon on each:
   - **Market Data API Acknowledgement** — sign the Terms and Conditions (required to enable API market data)
   - **Market Data Subscriber Status** — set yourself as **Non-Professional** (personal account, not redistributing data commercially — keeps fees at $0)
   - **Non-Commercial Form** — confirm personal/non-commercial use
4. Allow overnight for the acknowledgement to fully propagate — changes made today will be active by the next morning
5. Re-run the test the **following morning between 10–10:30 AM ET** — the market needs to have been open at least 15–30 minutes for delayed quotes to be populated

> **Paper vs. live:** On a **paper** account this is all you need — IBKR provides real-time data for free. A **live** account also needs paid data subscriptions (OPRA for options + US stock networks) — see the next question.

> **Important:** You cannot fix this through the IBKR mobile app or TWS — IBKR explicitly disables Market Data management for paper accounts in those interfaces ("When logging in through TWS or Mobile Apps, all Market Data and Trading functionality is disabled"). The web Client Portal on your live account is the only path.

> **Note:** If the Client Portal warns about an existing session (IB Gateway is running), use a different browser or browser profile to avoid disconnecting your Gateway session.

---

### Q: I went live and System Diagnostics shows Options Data ❌ "no bid/ask" — Market Data Subscriptions

**A:** **Paper accounts get real-time market data for free; live accounts do not.** When you switch to live, options (and stock) requests fall back to IBKR's free **delayed** feed — which only populates 15–30 min after the open and isn't reliable — and the diagnostic flags "no bid/ask." You need to subscribe to real-time data on the live account.

**How to confirm:** The gateway is logged in (IBKR ✅, IB Gateway ✅) but Options Data shows ❌. Manual probes show error `10089`/`10091`/`10167` ("requires additional subscription … Displaying delayed market data").

**The fix — subscribe in the live account's Client Portal** → **Settings → Market Data Subscriptions → ⚙️ Configure → Level I (NBBO)**, check all four (all **NP, L1**; $1.50/mo each, **waived once monthly commissions reach $20**):

| Subscription | Covers |
|---|---|
| **OPRA (US Options Exchanges)** | Option bid/ask + greeks — covered calls & CSPs (required) |
| **NYSE (Network A/CTA)** | NYSE-listed stocks |
| **NYSE American, BATS, ARCA, IEX, Regional (Network B)** | ETFs incl. **SPY**, ARCA/BATS/IEX names |
| **NASDAQ (Network C/UTP)** | NASDAQ-listed stocks |

Skip NYMEX (futures), Canadian exchanges, and OTC Markets — YRVI doesn't trade those.

> **Propagation lag — expect this:** After subscribing, data often does **not** start flowing immediately, even though the portal says "confirmed" and the subscription errors disappear. IBKR's entitlement can take from a few minutes up to the **next session / overnight** to activate. Re-run System Diagnostics later; if still empty after ~an hour, restart the IB Gateway (forces a fresh entitlement pull — you'll get a new IB Key 2FA push to approve). If it's still dark the next morning, contact IBKR.

> **2FA after restart:** Restarting the live gateway triggers an **IB Key push to your phone** — approve it within ~3 minutes or login times out and the API never comes up.

---

### Q: All trades show error 10197 "No market data during competing live session"

**A:** Your live IB Gateway (or TWS) is open somewhere — on another machine, the IBKR desktop app, or a second terminal. When a live account session is active, IBKR gives it market data priority and blocks the paper account's quotes entirely. Every ticker returns 10197 and 0 fills result.

**How to confirm:** Run System Diagnostics from the Help page. If SPY Price shows "No price data" and Options Data shows "no bid/ask", this is the cause — not a subscription issue.

**The fix:**

1. Close the live IB Gateway or TWS app on every device
2. Restart the paper IB Gateway from the Help page (or wait ~30 seconds for it to detect the session is clear)
3. Re-run the pipeline using the **Run Now** button on the This Week page

> **Note:** You can't run paper and live gateways simultaneously on the same IBKR account — the live session always wins. If you need both running at the same time, you would need separate IBKR accounts.

---

### Q: Why does applying an update ask for an IB Key 2FA approval? (And is Auto-Update safe?)

**A:** Every update restarts IB Gateway, and any gateway restart that IBKR didn't schedule itself requires a fresh **IB Key push approval on your phone**.

**Why it happens:** IBKR invalidates the weekly authentication token every **Sunday 1:00 AM ET**. IBC only writes a valid "autorestart" token during its *own* nightly auto-restart (the Daily Auto-Restart Time in Settings → IB Gateway). An update rebuilds the gateway image and **recreates the container**, so on startup IBC finds no valid autorestart file and logs:

```
autorestart file not found: full authentication will be required
```

That triggers the Second Factor Authentication prompt. After you approve, the dashboard records the token and daily restarts run unattended until the next Sunday reset. You can see the current state any time under **Settings → IB Gateway → Weekly IB Key Token**.

**Is this a problem?** Not for a **manual** update — you're at the computer when you click Upgrade, so you just approve the push. It *is* a problem with **Auto-Update enabled**: those updates run unattended at **3 AM Wed–Fri**, the 2FA prompt fires with no one to approve it, and the gateway stays logged out — **trading is paused until you approve on your phone**.

**Recommendation:** Leave **Auto-Update off** for live trading (or only enable it if you'll reliably catch and approve the 3 AM push). Apply updates manually while you're at the machine. Settings → Software Updates shows this same warning inline.

> **Reset Installation** also forces a fresh 2FA — it wipes the gateway settings volume, including the weekly token, so the next restart requires full authentication.

---

### Q: Orders never fill — stuck on "failed" or "order unfilled" even when market data works

**A:** IB Gateway is blocking API orders behind a confirmation dialog that no one is clicking. When **Bypass Order Precautions for API Orders** is not enabled, IBKR pops up a warning dialog before submitting each order. Since the system runs headlessly, nothing dismisses it and the order times out.

**The fix:**

- **Docker setup (v1.7.0+):** Handled automatically via `ibc_config.ini` — no action needed.
- **Legacy/manual setup:** In IB Gateway → **Configure → API → Precautions** → check **✅ Bypass Order Precautions for API Orders** → click **Apply** → **OK**

Without this, the scheduler will connect, get market data, size positions, submit orders — and then silently fail every fill.

---

---

### Q: How do I export a Flex XML from IBKR to use with the History Reconciler?

**A:** Use the IBKR Client Portal to run a Flex Query and download the XML. This covers trades placed manually or outside YRVI that need to be added to your premium history.

1. Log into the **IBKR Client Portal** at [https://www.interactivebrokers.com/sso/Login](https://www.interactivebrokers.com/sso/Login) using your **live account** credentials
2. Navigate to **Performance & Reports → Flex Queries**
3. Click **Activity Flex Query** → then the **+** button to create a new query (or edit an existing one)
4. Configure the query:
   - **Query Name:** Give it a name like `YRVI Reconciler`
   - **Sections:** Click **Trades** — then click **Select All** to include all fields, make sure the **Executions** sub-type is checked, then click **Save**
   - **Period:** Choose *Last 365 Calendar Days* (or a custom date range), then click **Continue**
   - **Format:** XML
   - **Date Format:** `yyyyMMdd`
5. Click **Create** to save the query
6. Click **Run** (▶ button next to your query)
6. When the download dialog appears, save the `.xml` file

Then in YRVI:
- Go to **Settings → History Reconciler**
- Select **Paste / Upload XML**
- Click the file picker (or paste the XML content directly)
- Click **Preview** to review the weeks found, then **Commit** to save

> **Note:** Only option *sells* are counted (CSPs and covered calls). Stock share sales are not included in the premium total.

---

### Q: How do I set up automatic reconciliation via the IBKR Flex Web Service?

**A:** This lets YRVI fetch your trade history directly from IBKR on demand — no manual export needed. It requires a one-time setup of a Flex Token and Query ID in your Secrets page.

**Step 1 — Create the Flex Query and get the Query ID**

Follow steps 1–4 from the previous FAQ entry to create an Activity Flex Query with the **Executions** sub-type, XML format. To find your **Query ID**, click the **ℹ️ icon** to the left of your query name on the Flex Queries page — it shows the Query ID in the details popup (e.g. `1529200`).

**Step 2 — Get your Flex Web Service Token**

1. In the IBKR Client Portal, go to **Performance & Reports → Flex Queries**
2. Click **Select Account(s)** at the top and make sure **all your accounts are selected** — the Flex Web Service Configuration panel only appears when multiple accounts are selected
3. Scroll down — you should now see a **Flex Web Service Configuration** panel in the bottom right
4. Click the **gear icon (⚙)** on that panel
5. Make sure **Flex Web Service Status** is checked (enabled)
3. Copy the **Current Token** shown on the page — it's a long alphanumeric string
4. If no token exists yet, click **Generate Token** to create one

**Step 3 — Enter the secrets in YRVI**

1. Go to **Settings → Secrets** (or the Secrets page in the sidebar)
2. Find **IBKR Flex Token** and click **Set** — paste your token
3. Find **IBKR Flex Query ID** and click **Set** — paste the numeric query ID

> **Note:** After enabling the Flex Web Service for the first time, IBKR needs a few minutes to activate the token. If you see "Statement is incomplete at this time" or "Statement could not be generated at this time", wait 5–10 minutes and try again. If the error persists, use the **Paste / Upload XML** tab instead — download the XML manually from IBKR (see the previous FAQ entry) and upload it directly. The fetch-from-IBKR path and the manual upload path produce identical results.

**Step 4 — Use the Reconciler**

1. Go to **Settings → History Reconciler**
2. Select **Fetch from IBKR**
3. Optionally set a date range, then click **Preview**
4. Review the weeks found and click **Commit**

> **Token security:** Treat your Flex Token like a password — it grants read-only access to your account history. It is stored encrypted in the YRVI secrets container.

---

*Have a question not covered here? Post in the You Rock Club Discord and we'll add it.*
