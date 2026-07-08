# 📋 IBKR Account Setup Guide for YRVI

A complete walkthrough for setting up your Interactive Brokers account to run the You Rock Volatility Income Fund system.

---

## Overview

**Interactive Brokers (IBKR)** is the brokerage we use to execute trades. It offers:
- Direct market access with very low commissions (~$0.65/contract for options)
- A robust API (`ib_insync`) that YRVI uses to place orders automatically
- Both paper (simulated) and live trading accounts

**What to expect:**
- Account approval takes **2–3 business days**
- Start with a **free paper trading account** — no deposit required
- Paper accounts come with $1M virtual cash (we'll reset to $250K)
- You can run the full YRVI system on paper trading while you wait for live approval

---

## Step 1 — Create Your Account

1. Go to **https://ibkr.com/referral/sean5376** (referral link — same signup, no extra steps)
2. Click **"Open Account"** in the top right
3. Select **"Individual Account"**
4. Enter your email address and create a strong password
5. Check your inbox and click the verification link

> **Tip:** Use the same email you want associated with this account long-term — it's hard to change later.

---

## ⚠️ CRITICAL: Choose IBKR Pro (NOT Lite)

During signup IBKR will ask you to choose between two account tiers. **This is the most important choice in the entire setup.**

| | IBKR Lite | IBKR Pro |
|---|-----------|----------|
| Commissions | $0 (free trades) | ~$0.65/contract |
| API access | ❌ **None** | ✅ **Full access** |
| YRVI compatible | ❌ **Will not work** | ✅ **Required** |

### **You MUST select IBKR Pro.**

YRVI is fully automated — it connects to IBKR via API to place every order. IBKR Lite has no API access, which means **the automation cannot place any trades.**

**The commission cost is negligible:**

| | Amount |
|---|--------|
| Options commission | ~$0.65 per contract |
| Typical week (50 contracts sold) | ~$32 in commissions |
| Typical weekly premium collected | $3,500+ |
| Commission as % of income | **< 1%** ✅ |

**If you accidentally selected IBKR Lite:**
1. Log into the IBKR portal → **Account → Account Type**
2. Request an upgrade to **IBKR Pro**
3. Upgrade takes 1–2 business days to process

---

## Step 2 — Personal Information

Fill in your details as prompted. Here's what to expect:

| Field | What to enter |
|-------|--------------|
| Legal name | Exactly as it appears on your government ID |
| Address | Your current residential address |
| Phone number | A number you can receive calls/SMS on for 2FA |
| Date of birth | Your actual DOB |
| SSN | Full SSN for US citizens (required for tax reporting) |
| Country of citizenship | United States |

**Employment status** — choose the option that best describes you:
- Employed, Self-employed, Retired, or Student are all fine
- If employed, you'll be asked your employer's name and your job title

**Financial information** — IBKR uses these to assess suitability. Recommended ranges for YRVI:

| Field | Recommended range |
|-------|------------------|
| Annual income | $100,000 – $200,000 |
| Net worth (total) | $500,000 – $1,000,000 |
| Liquid net worth | $250,000 – $500,000 |

> These should reflect your actual financial situation. IBKR uses them to determine account eligibility, not to verify income for a loan.

---

## Step 3 — Trading Experience ⭐ CRITICAL

**This section determines whether you get options trading approval.** Answer conservatively and you'll be denied. Be accurate about what you know.

Recommended answers for the YRVI strategy:

| Asset class | Experience | Knowledge level |
|-------------|-----------|----------------|
| Stocks / ETFs | 3+ years | Good |
| Options | 3+ years | Good |
| Bonds | 1–2 years | Limited (fine to leave low) |

**Investment objective:** Income

**Trading frequency:** Weekly

**Average trade size:** $25,000 – $100,000

> **Why this matters:** IBKR uses your stated experience to decide your options approval level. Level 3 is what we need (see Step 5). Stating "no experience" with options will result in denial.

---

## Step 4 — Account Configuration ⭐ CRITICAL

> **Reminder:** Make sure you selected **IBKR Pro** during signup. IBKR Lite has no API access and YRVI will not work with it. See the [IBKR Pro warning above](#️-critical-choose-ibkr-pro-not-lite) if you're unsure.

**Account type: CASH**

YRVI sells **cash-secured puts** — meaning the full capital to buy the shares is held in your account as collateral. You do **not** need margin for this strategy, and using a cash account avoids margin calls and interest charges.

| Setting | Value | Why |
|---------|-------|-----|
| Account type | **CASH** | CSPs require cash collateral, not margin |
| Base currency | USD | All positions are in US equities |

**Trading permissions to request:**

| Permission | Required? |
|-----------|-----------|
| ✅ US Stocks & ETFs | Yes — needed for stock assignments |
| ✅ US Options (Level 3) | Yes — required for selling CSPs and CCs |
| ❌ Margin | No — cash account only |
| ❌ Futures | No |
| ❌ Forex | No |

> If you accidentally select a margin account during signup, contact IBKR support to convert it to a cash account before depositing funds.

---

## Step 5 — Options Level 3 Approval

**Level 3 options** is required to run YRVI. Despite being a conservative cash-secured strategy, IBKR classifies "Short Put" under Level 3 (not Level 2) because it involves a short obligation. Level 2 will result in Error 201 rejections on every order.

| Strategy | Required Level |
|----------|---------------|
| ✅ Cash-secured puts (CSPs) | Level 3 — core YRVI strategy |
| ✅ Covered calls (CCs) | Level 3 — wheel strategy after assignment |
| ❌ Naked puts/calls | Not applicable — cash account prevents this |
| ❌ Multi-leg spreads | Not applicable |

> **Note:** Level 2 at IBKR includes "Covered Put" which sounds similar but is actually a married put (buying a put to protect a long position) — not the same as selling a cash-secured put. Don't be fooled by the name.

**If your Level 3 request is denied:**

1. Update your **Financial Profile** first — IBKR requires sufficient liquid net worth and options trading experience to approve Level 3
2. Make sure your options experience is set to **6-10+ years** and knowledge to **Extensive**
3. Liquid net worth should accurately reflect your real assets
4. Re-apply after the profile update is approved (usually 24-48 hours)
5. Or call IBKR client services: **1-877-442-2757** (US) and tell them: *"I want to sell cash-secured puts and covered calls only. The full cash collateral is held in the account and there is no naked exposure."*

---

## Step 6 — Identity Verification

IBKR is required by law to verify your identity. Have these ready:

**Required documents:**
- **Government-issued photo ID** — driver's license or passport
- **Proof of address** — utility bill, bank statement, or government letter dated within 90 days

**Upload tips:**
- Take photos in good lighting with no glare
- Make sure all four corners of the document are visible
- The name and address must be clearly legible
- Blurry or cropped photos are the #1 cause of approval delays

> Documents are reviewed within 1–2 business days. You'll receive an email when your account is approved.

---

### Step 7 — Fund Your Account (Optional for Paper Trading)

**Paper Trading — $0 required** ✅
You can start paper trading immediately with NO real money.
IBKR gives you $1M in virtual cash to practice with.
This is how we recommend everyone starts with YRVI.

**Live Trading — when you're ready**
When you're confident in the system and want to use real money:
- Minimum: any amount (no IBKR minimum for cash accounts)
- YRVI is designed for $250,000 deployed capital
- You can start smaller and scale up as you get comfortable
- Transfer options:
  * ACH transfer (free, 3-5 days)
  * Wire transfer (faster, small fee)
  * ACATS transfer from another broker

💡 Pro Tip: Run paper trading for at least 4-8 weeks before
using real money. The system needs to prove itself first!

---

## Step 8 — Enable Paper Trading

While waiting for live account approval, set up paper trading to test the full YRVI system:

1. Log into **https://www.interactivebrokers.com/portal**
2. Go to **Account → Settings → Paper Trading Account**
3. Click **"Create Paper Trading Account"**
4. Your paper account is created with **$1,000,000** virtual cash

**Reset paper account to $250,000 for accurate YRVI testing:**
1. Log into your IBKR account in a browser — make sure you select **Paper Trading** at login (you'll see a pink banner: *"This is a Paper Trading account for Simulated Trading"*)
2. Go to **Account Settings** → click the **gear icon** next to **Paper Trading Account Reset**
3. Select **$250,000** from the "Select Reset Amount" dropdown and click **Continue**

> Note: Only the cash balance is reset — close any open positions first for a full reset. Reset requests submitted before 4:00 PM ET are processed the next day.

> Paper trading uses real market data and real order routing logic — the only difference is no real money changes hands. Run YRVI in paper mode for at least 4 weeks before going live.

---

## Step 9 — Configure Market Data Subscriptions ⭐ CRITICAL

Without this step, YRVI will connect to IBKR successfully but fail to retrieve option prices — every trade will show **"Failed Market Data"** even when the market is open.

1. Log into the IBKR **Client Portal** on your **live account** in a browser
2. Click the **person icon (top right) → Settings → Trading Platform → Market Data Subscriptions**
3. Complete all three items by clicking the ⚙️ gear icon on each:

   | Item | What to do |
   |------|-----------|
   | **Market Data API Acknowledgement** | Sign the Terms and Conditions — required to enable API market data access |
   | **Market Data Subscriber Status** | Select **Non-Professional** — you're trading your own account, not redistributing data commercially. Keeps fees at $0. |
   | **Non-Commercial Form** | Confirm personal/non-commercial use |

> **Paper vs. live — this is the part that trips people up:**
> - **Paper trading:** IBKR provides **real-time** option data for free. No OPRA subscription needed — the API Acknowledgement above is the only unlock. This is why YRVI "just works" in paper.
> - **Live trading:** the live account needs a **paid OPRA subscription** for real-time option data. Without it, requests fall back to IBKR's free **delayed** data (type 3), which only populates ~15–30 minutes after the open and is not reliable for the dashboard diagnostic or earlier-time execution. See [Preparing for Live Trading](#preparing-for-live-trading) for the exact subscription.

> **Propagation time:** Changes made today may take a few minutes to overnight to fully activate. On delayed data, run your first test the following morning between 10–10:30 AM ET (market open ≥15–30 min). With a live OPRA subscription, real-time quotes flow as soon as the market opens.

> **Why the live account?** IBKR paper accounts inherit market data subscriptions from your live account. The IBKR mobile app and TWS explicitly disable Market Data management for paper accounts — the web Client Portal on your live account is the only place to configure this.

> **Tip:** If the portal warns about an existing session (IB Gateway is already running), open the portal in a different browser or browser profile to avoid disconnecting your Gateway.

---

## Step 11 — Find Your Credentials for YRVI

Before running the setup script, have these three things ready. You'll start with your **paper trading credentials** — live account info can be added later from the dashboard.

**Your IBKR Paper Username**
- Auto-generated by IBKR when your paper account is created
- Find it by logging into the IBKR portal → **Account → Settings → Paper Trading Account**
- It will be a short username (different from your main IBKR login)

**Your IBKR Paper Password**
- Your paper account has its own separate password from your main IBKR login
- If you haven't set one yet, you can set or reset it in the IBKR portal → **Account → Settings → Paper Trading Account**
- > 🔒 **Security tip:** Use a unique password for your paper account — it will be stored in your system's secret store and used by the automation to log in.

**Your Paper Account Number**
- Log into the IBKR portal → **Account → Account Summary**
- Switch to your paper account view
- Your paper account number starts with **`DU`** (e.g., `DU12345678`)

> 💡 The setup script will prompt you for these values and store them securely in your system's secret store. They are never written to a config file or committed to git. When you're ready to go live, add your live credentials from the Secrets page in the YRVI dashboard.

---

## Step 12 — Configure IB Gateway API Precautions ⭐ CRITICAL

Without this, YRVI will connect, get market data, size positions — and then silently fail every order. IB Gateway pops up a confirmation dialog before submitting API orders, and since the system runs headlessly, nothing dismisses it and every trade times out.

**Docker setup:** This is handled automatically via `ibc_config.ini` (`BypassOrderPrecautions=yes`) — no manual step needed.

**Legacy/manual setup:** You must set this once in the IB Gateway GUI:
1. Open the Gateway screen with the built-in **View Gateway** viewer — dashboard **Help → System Diagnostics → View Gateway**, then **⚠️ Enable keyboard / mouse control** (you need input to change this setting). No VNC client required. (Fallback: an external VNC client to `127.0.0.1:5900` — literal IPv4, not `localhost`.)
2. Click **Configure → API → Precautions**
3. Check **✅ Bypass Order Precautions for API Orders**
4. Click **Apply → OK**

---

## Step 13 — Install and Configure YRVI

> **Docker (recommended):** `setup_docker.sh` handles everything — IB Gateway runs inside a container automatically. Manual IB Gateway installation is not needed.

### Docker Setup (Recommended)

1. Install **[Rancher Desktop](https://rancherdesktop.io)** and enable the dockerd (moby) engine in Preferences → Container Engine.

2. ⚠️ **Configure Rancher Desktop to auto-start** — this is required so Docker is running before YRVI containers start after a reboot:
   - Open Rancher Desktop → **Preferences → Application**
   - Check ✅ **Automatically start at login**
   - Check ✅ **Start in background**
   - Click **Apply**

3. Run the one-command setup:
   ```bash
   git clone https://github.com/controllinghand/you_rock_fund.git you_rock_fund
   cd you_rock_fund
   bash setup_docker.sh
   ```

See [CONTAINERIZATION.md](CONTAINERIZATION.md) for the full guide including credentials, 2FA, and troubleshooting.

### Manual / Legacy Setup (macOS only)

If you're using the original launchd-based setup instead of Docker:

1. Go to: **https://www.interactivebrokers.com/en/trading/ibgateway-stable.php**
2. Click **"Download IB Gateway Stable"** — choose the correct macOS version:
   - **Apple Silicon (M4/M3/M2/M1):** `arm64` / Apple Silicon build
   - **Intel Mac:** `x64` / Intel build
3. Run the `.dmg` installer — the default install location is fine
4. Run `bash setup_ibc.sh` — this configures IB Gateway to launch automatically via launchd

> **IB Gateway vs TWS:** YRVI uses IB Gateway, not Trader Workstation (TWS). They serve the same API but IB Gateway is lightweight and headless — better for automated trading. Use IB Gateway ports: **4002** (paper) and **4001** (live).

---

---

## Preparing for Live Trading

Once you've run paper trading for at least 4 weeks and are ready to use real money, follow these steps to switch YRVI to live trading via the dashboard.

### ⚠️ Required: Enable IB Key Before Going Live

YRVI requires **IB Key** (push notification via the IBKR Mobile app) for two-factor authentication. **SMS 2FA is not supported** — it requires someone to be physically present to enter a code every time IB Gateway restarts, which breaks the unattended automation.

**Before switching to live, set up IB Key:**

1. Download the **IBKR Mobile** app on your phone (iOS or Android)
2. Log into the app with your live account credentials
3. Go to **More → Security → Secure Login System**
4. Enable **IB Key** and follow the activation steps
5. In the IBKR Client Portal → **Settings → Security → Secure Login System**, confirm IB Key is your active 2FA method

> If you are currently using SMS 2FA, switch to IB Key before going live. SMS requires manual code entry on every restart and cannot be automated.

**How 2FA works with YRVI:**
- On **first live login**, IB Gateway will show a 2FA prompt in VNC — approve the IB Key push notification on your phone
- After that, IB Gateway **auto-restarts nightly** and only requires a fresh 2FA approval **once per week** (Sunday evening when IBKR resets the weekly session token)
- The weekly approval takes seconds — you'll get a push notification, tap approve, done

### Step 1 — Open a Live IBKR Account

If you've only been using a paper trading account, you'll need a funded live account:

1. Log into **https://www.interactivebrokers.com/portal**
2. Go to **Account → Open Additional Account** (or apply during your original signup)
3. Deposit funds via ACH (free, 3–5 days) or wire transfer
4. Confirm you have **Level 3 options approval** on the live account (same as paper)

> Your live account number starts with **`U`** (e.g., `U12345678`). Paper accounts start with `DU`.

### Step 2 — Add Live Credentials in the Secrets Page

Open the YRVI dashboard → **Secrets** and add the three live credentials:

| Secret | Value |
|--------|-------|
| IBKR Live Account ID | Your live account number (e.g. `U12345678`) |
| IBKR Live Username | Your live IBKR username |
| IBKR Live Password | Your live IBKR password |

These are stored in the same encrypted secrets container as your paper credentials — no `.env` file editing required.

### Step 3 — Subscribe to Real-Time Options Data (OPRA) ⭐ CRITICAL

Paper trading gets real-time data for free, but a **live account does not** — it needs a paid subscription, or YRVI silently falls back to delayed data (Options Data shows ❌ "no bid/ask" in System Diagnostics even with the market open).

1. Log into the IBKR **Client Portal** on your **live account** (close IB Gateway first — a running Gateway and a Client Portal login conflict over the same session)
2. **Settings → Market Data Subscriptions → ⚙️ Configure → Level I (NBBO)**
3. Check these four (all **NP, L1** — Non-Professional, top-of-book, which is all YRVI uses):

   | Subscription | Covers | Needed for |
   |---|---|---|
   | **OPRA (US Options Exchanges)** | Options on NYSE, CBOE, BOX, Nasdaq, MIAX, MEMX | Covered calls + CSPs (bid/ask + greeks) |
   | **NYSE (Network A/CTA)** | NYSE-listed stocks | Strike selection + stop-loss price |
   | **NYSE American, BATS, ARCA, IEX, Regional (Network B)** | ETFs incl. **SPY**, ARCA/BATS/IEX names | Strike selection + stop-loss price |
   | **NASDAQ (Network C/UTP)** | NASDAQ-listed stocks | Strike selection + stop-loss price |

   - Each is **USD 1.50/month, waived once monthly commissions reach USD 20** — effectively free at YRVI's trading volume.
   - OPRA is the only one strictly required for *options*; the three network feeds give real-time *stock* quotes for any ticker the screener surfaces and accurate stop-loss triggers. The free "US Real-Time Non Consolidated Streaming Quotes" does **not** satisfy the API's consolidated request (you'll see `error 10089 — SPY ARCA/TOP`).
   - **Skip:** NYMEX (futures), NEO/Canadian exchanges, OTC Markets — not traded by YRVI.
4. Restart IB Gateway afterward (the dashboard does this on mode switch) so it pulls the new entitlements. Activation is usually minutes but can take until the next session.

> **Why this matters:** delayed data only populates 15–30 min after the open, so the system can't screen, price, or run at earlier times. Real-time OPRA removes that constraint.

### Step 4 — Switch to Live in the Dashboard

1. Open the YRVI dashboard → **Settings**
2. Scroll to **Trading Mode** and click **"Switch to Live"**
3. The dashboard checks that all three live credentials are configured
4. If any are missing, a warning shows exactly which secrets to add in the Secrets page
5. If all credentials are present, a confirmation modal shows your live account number (masked) — type `CONFIRM` to proceed

When you confirm, YRVI automatically:
- Writes the trading mode to the shared volume
- Restarts IB Gateway with your live credentials
- Posts a Discord alert (if webhook is enabled)

**Watch VNC during the first live login** — IB Gateway will show the 2FA screen. Approve the IB Key push notification on your phone. After that, the nightly auto-restart handles everything without intervention (except the weekly Sunday re-auth).

> You do **not** need to manually edit any config files or restart IB Gateway — the dashboard handles everything.

---

## Common Issues & Solutions

> ❓ For YRVI-specific setup issues (Docker, secrets, Screen Sharing), see [FAQ.md](./FAQ.md).

| Problem | Solution |
|---------|----------|
| Options trading denied | Call IBKR (1-877-442-2757), explain CSP/CC strategy — they often approve manually |
| Account stuck "In Review" | Upload clearer ID photos; check spam folder for document requests |
| Can't find account number | Portal → Account → Account Summary (top of page) |
| Paper account not showing | Wait 24 hours after live account approval; may need to enable via portal |
| 2FA / authentication issues | Enable **IB Key** in IBKR Mobile — SMS 2FA is not supported for unattended automation |
| Wire transfer not credited | Call IBKR with your wire confirmation number — usually credited same day |
| Options order rejected | Verify your account has Level 3 options (not Level 2); check available cash collateral |

---

## ⚠️ Important Notes

- **Never share your password.** YRVI stores credentials in an encrypted secrets container on your machine — never in a config file or uploaded to GitHub.
- **Start with paper trading** for at least 4 weeks to validate the system before risking real capital.
- **Only use money you can afford to have tied up.** CSPs tie up collateral for the duration of the contract (typically 1 week). Stop losses are set at 10% below strike — this is the maximum per-position loss in a bad week.
- **Keep IB Gateway running.** YRVI connects to IB Gateway to place orders. If IB Gateway is closed, trades won't execute. With Docker, `setup_docker.sh` handles this automatically. With the legacy launchd setup, `setup_ibc.sh` configures it to start automatically on login.
- **Keep Rancher Desktop set to auto-start** (Docker setup only). If Rancher Desktop is not configured to start at login, Docker won't be available when YRVI containers try to restart after a reboot. Set this in Preferences → Application → ✅ Automatically start at login + ✅ Start in background.

---

## Timeline

| Day | Action |
|-----|--------|
| Day 1 | Submit IBKR application, upload ID documents |
| Day 2–3 | IBKR reviews and approves account |
| Day 3 | Install Rancher Desktop; enable auto-start (Preferences → Application) |
| Day 3 | Run `bash setup_docker.sh` — builds containers and installs login item |
| Day 3 | Paper trading begins via IB Gateway container |
| Week 1–4 | Run full YRVI system in paper mode, review results each Monday |
| Week 4–8 | Fund live account, switch `IBKR_PORT=4001`, go live |
