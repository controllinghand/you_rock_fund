# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

An automated Python algorithmic options trading system for the **You Rock Volatility Income Fund**. It generates weekly income by selling cash-secured puts (CSPs) and, after assignment, selling covered calls (the wheel strategy) through Interactive Brokers.

## Running the System

```bash
source venv/bin/activate

# Start scheduler (production — runs indefinitely)
python scheduler.py

# Run full pipeline once immediately (screener → sizer → trader)
python trader.py

# Manual wheel operations
python wheel_manager.py detect   # run assignment detection now
python wheel_manager.py check    # run wheel check (stop loss + CC) now

# Run daily risk monitor now
python risk_manager.py

# Background daemon
nohup python scheduler.py > nohup.out 2>&1 &
```

**Monitor execution:**
```bash
tail -f trade_log.txt        # CSP execution details
tail -f wheel_log.txt        # Wheel check and assignment logs
tail -f risk_log.txt         # Daily risk monitor logs
tail -f scheduler_log.txt    # Scheduler heartbeat
cat state.json               # Full system state (see schema below)
```

## Weekly Schedule

| Day/Time (PST) | Job | Module |
|---|---|---|
| Saturday 8:00AM | Assignment detection | `wheel_manager.detect_assignments()` |
| Saturday 6:00PM | Screener preview | `screener` + `position_sizer` |
| Monday 9:55AM | Wheel check → CSP pipeline (one chained job): stop loss sells + covered calls, then screen → size → execute | `scheduler.run_pipeline()` → `wheel_manager.run_wheel_check()` + `trader.execute_positions()` |
| Tue–Thu 9:00AM | Daily risk monitor | `risk_manager.run_daily_monitor()` |

## Architecture

```
config.py          → All fund parameters, IBKR credentials, API keys
screener.py        → Fetches CSP candidates from Render API, filters + scores them
position_sizer.py  → Allocates capital across up to 5 positions (accepts budget override)
trader.py          → IBKR CSP execution: qualify → liquidity check (spread + OI-notional floor) → limit/market escalation
wheel_manager.py   → Assignment detection, screener-based exits, ~0.20-delta covered call execution (prefers nearest strike ≥ cost basis; writes below-cost CC instead of force-selling underwater holdings; keeps holdings through earnings by default)
risk_manager.py    → Daily price checks, stop loss alerts, weekly P&L tracking
scheduler.py       → APScheduler orchestration for all 5 jobs
state.json         → Persisted system state (see schema below)
```

### IBKR Client IDs

Each module connects with a distinct client ID to allow concurrent connections:
- `trader.py` → `IBKR_CLIENT_ID` (=1)
- `wheel_manager.py` → `IBKR_CLIENT_ID_WHEEL` (=2)
- `risk_manager.py` → `IBKR_CLIENT_ID_RISK` (=3)

### state.json Schema

```json
{
  "run_date":      "ISO timestamp of last CSP execution",
  "positions":     [...],        // sized positions from position_sizer
  "executions":    [...],        // CSP order results from trader
  "filled_count":  5,
  "total_premium": 4088,

  "wheel_holdings": [            // stock positions being wheeled
    {
      "ticker":             "OKLO",
      "shares":             800,
      "assigned_strike":    60.00,         // TRUE strike-weighted avg cost = Σ(strike×shares)/Σ(shares), NOT IBKR's premium-netted avgCost
      "tranches": [                        // source of truth for assigned_strike; one entry per CSP assignment batch
        {"shares": 800, "strike": 60.00, "date": "2026-04-25"}
      ],
      "assignment_date":    "2026-04-25",
      "current_cc_strike":  62.00,
      "current_cc_expiry":  "20260501",
      "current_cc_premium": 320.0,
      "cc_contracts":       8,             // contracts actually covered (shares open under a short call)
      "cc_contracts_needed": 8,            // shares // 100; when cc_contracts < needed, cc_status = "partial"
      "weeks_held":         1,
      "cc_status":          "open",  // pending|open|partial|failed|sold_dropped_screener|sold_stop_loss|sold_earnings_this_week|sold_no_viable_cc
      "current_price":      62.50,
      "last_checked":       "ISO timestamp"
    }
  ],

  "monday_context": {            // written by wheel_check, read by run_pipeline
    "skip_tickers":    ["OKLO"],
    "freed_capital":   54000.0,
    "cc_premium":      320.0,
    "shares_sold_pnl": -4800.0,
    "wheel_activity":  [         // one entry per holding processed
      {"ticker": "OKLO", "action": "cc_opened", "cc_strike": 62.0,
       "cc_delta": 0.22, "cc_premium": 320.0, "cc_expiry": "20260501"},
      {"ticker": "FOO",  "action": "sold_dropped_screener",
       "shares": 500, "proceeds": 30000.0, "realized_pnl": -2000.0}
    ],
    "updated":         "ISO timestamp"
  },

  "weekly_pnl": {
    "week_start":           "2026-04-27",
    "csp_premium":          4088,
    "cc_premium":           320,
    "shares_sold_pnl":      -4800,
    "total_realized":       -392,
    "unrealized_stock_pnl": 2000,
    "grand_total":          1608
  }
}
```

### Monday Data Flow

`scheduler.run_pipeline()` runs both halves as ONE chained job (the wheel
check's return dict is passed to the CSP pipeline in memory — no state.json
hand-off, so the pipeline can never start before the wheel check finishes).

```
Step A — wheel_check (run_wheel_check):
  → call get_all_candidates() to get screener ticker set
  → Step 1: if ticker not in screener (down to wheel-retention mkt-cap floor) → sell at market (freed_capital += proceeds)
  → Step 2: earnings — KEPT through earnings by default (wheel_cc_ignore_earnings_filter=true);
            only sells pre-earnings if that toggle is off
  → Step 3: query IBKR option chain for calls on nearest Friday; prefer nearest strike >= cost basis, delta <= ~0.20
  → Step 4: write the CC. Underwater + no strike >= cost → write ~0.20-delta CC BELOW cost (keep shares),
            unless wheel_sell_when_cc_below_assigned=true → force-sell at market
  → write monday_context to state.json (for the Discord preview / recovery)
    AND return it: skip_tickers, freed_capital, reserved_capital,
    active_wheel_count, cc_premium, shares_sold_pnl, wheel_activity,
    open_short_put_tickers

Step B — run_csp_pipeline(context=wheel_check_result):
  → filter skip_tickers from screener results
  → target_fills = num_positions − active_wheel_count − already-open CSPs
  → size_all(targets, budget=net_liq − reserved_capital + freed_capital)
  → execute CSPs (merges into existing state.json — wheel_holdings preserved)
  → assemble and write weekly_pnl (csp_premium + cc_premium + shares_sold_pnl)
```

> Earlier versions ran these as two cron jobs 5 min apart and the pipeline
> re-read `monday_context` from disk; if a CC-heavy wheel check overran 5 min
> the pipeline read stale context (lost CC premium + over-filled CSP slots).
> Fixed v3.9.18 by chaining them. The dashboard's Run Now (`run_monday`) always
> used this in-memory sequence.

### Order Execution (shared pattern)

All orders — CSPs, covered calls, stop loss sells — use the same escalation:
- Limit @ mid (120s) → limit @ bid proxy (120s) → market with 60s polling loop
- Each leg orders only the still-unfilled REMAINDER and accumulates prior legs' fills
  (incl. a cancelled limit leg's partial), so the market leg can never re-send the full
  quantity — no over-sell / naked position. Partial fills are recorded honestly (true
  filled count + gross premium `fill × 100`), never dropped.

### Key Rules

- Never buy back covered calls
- Excluded tickers (per-holding Exclude checkbox + Settings "Excluded Tickers") are NEVER traded, adopted, or sold:
  no CSP, no CC, no wheel adoption — enforced in the screener, both adoption paths, the per-holding loop, and risk monitor
- Sell shares Monday 9:55AM if: ticker dropped from screener (below the wheel-retention mkt-cap floor),
  OR stop-loss tripped (if enabled), OR (underwater with no CC ≥ cost AND wheel_sell_when_cc_below_assigned=true)
- DEFAULT for underwater holdings: write a ~0.20-delta CC BELOW cost (keep shares + premium) — do NOT force-sell
- DEFAULT through earnings: keep the holding and write the CC (wheel_cc_ignore_earnings_filter=true);
  earnings filter still applies to NEW CSP entries via the Earnings Window setting
- CSP liquidity gate is spread + OI-NOTIONAL floor (OI × strike × 100 ≥ min_oi_notional, default $1M),
  NOT a flat open-interest count — fairer to high-strike names
- Freed capital from share sales is added to that week's CSP deployment budget
- Sold tickers are skipped in the same week's CSP screener
- Daily monitor (Tue–Thu) alerts if a ticker drops from screener mid-week
- All operational alerts are persisted in-app (/data/alerts.json) AND sent to Discord via the single
  _send_discord_alert chokepoint in api.py; the dashboard bell reads GET /api/alerts (per-box, standalone)

## Key Configuration (config.py)

```python
IBKR_PORT           = 4002    # IB Gateway: 4002 = paper, 4001 = live
TOTAL_FUND_BUDGET   = 250_000
TARGET_PER_POSITION = 50_000
MAX_PER_POSITION    = 70_000
NUM_POSITIONS       = 5
STOP_LOSS_PCT       = 0.10    # 10% below assignment strike
```

**IB Gateway must be running** on `127.0.0.1:4002` (paper) or `:4001` (live). These are IB Gateway ports — TWS uses different ports (7497/7496).

## Dependencies

Python 3.13 + venv. Key packages: `ib_insync`, `apscheduler`, `requests`, `pandas`, `numpy`, `python-dotenv`, `nest_asyncio`.

```bash
pip install ib_insync apscheduler requests pandas numpy python-dotenv python-dateutil tzlocal nest_asyncio
```

## No Test/Lint Framework

Pure operational code. Validate changes via `state.json` and log files after a dry-run execution (`DRY_RUN = True` in `trader.py`).
