"""
Monday runner — the single source of truth for "what happens on Monday".

The exact same code path is used by:
  • scheduler.py        — Mon wheel check (exec−5) + CSP pipeline (configured exec time) (live)
  • Run Now    (api)    — execute the full Monday sequence on demand (live)
  • Run Screener (api)  — preview the full Monday sequence (dry_run, no side effects)

run_monday(dry_run) ties the two halves together:

    run_wheel_check(dry_run)                       # keep / sell / write-CC decisions
        │  → freed_capital, skip_tickers, cc_premium, shares_sold_pnl, ...
        ▼
    run_csp_pipeline(context, dry_run)             # size + (optionally) execute CSPs

When dry_run is True nothing is placed, persisted, or posted — the call returns the
plan so the dashboard can show exactly what Monday will do.
"""
import json
import logging
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from config import (
    IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID, IBKR_CLIENT_ID_PREVIEW, ACCOUNT,
    NUM_POSITIONS, TOTAL_FUND_BUDGET, get_settings,
)

log = logging.getLogger(__name__)
PST = ZoneInfo("America/Los_Angeles")
STATE_FILE = "state.json"
TRADE_LOG_FILE = "trade_log.json"


# ── Helpers ────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _week_premium_from_trade_log(week_monday: str):
    """(csp_premium, cc_premium) gross, summed from trade_log.json for the week
    (Mon..Sun of `week_monday`), split by right (P→CSP, C→CC). Durable source of
    truth so a pipeline RE-RUN can't overwrite the week's real premium with 0 —
    the accumulator-only path zeroed it on any recovery/Run-Now re-run (#71).
    Returns (None, None) if the log can't be read/parsed."""
    try:
        with open(TRADE_LOG_FILE) as f:
            entries = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None, None
    try:
        wk_start = date.fromisoformat(week_monday)
    except (TypeError, ValueError):
        return None, None
    wk_end = wk_start + timedelta(days=6)
    csp = cc = 0.0
    for e in entries:
        try:
            ed = date.fromisoformat(str(e.get("entry_date", ""))[:10])
        except (TypeError, ValueError):
            continue
        if wk_start <= ed <= wk_end:
            prem = e.get("total_premium") or 0.0
            if e.get("right") == "P":
                csp += prem
            elif e.get("right") == "C":
                cc += prem
    return round(csp, 2), round(cc, 2)


def _write_weekly_pnl(csp_premium: float, context: dict, fund_budget: float = 0,
                      net_liq: float = None) -> float:
    """Assemble weekly_pnl from CSP premium + wheel-check context, persist it, and
    post Discord results. Returns total_realized. Shared by the normal and the
    'no remaining CSP slots' code paths so P&L is consistent either way."""
    cc_premium      = context.get("cc_premium", 0.0)
    shares_sold_pnl = context.get("shares_sold_pnl", 0.0)

    now   = datetime.now(PST)
    # Key the week by its Monday — same convention as reconciler._week_monday —
    # so an off-Monday run (holiday, misfire recovery, manual Run Now) overwrites
    # rather than spawning a second ytd_tracker row the Flex merge then preserves.
    week_monday = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")

    # Prefer the durable trade_log for premium so a RE-RUN (Run Now / recovery /
    # off-Monday) — where nothing new is collected — can't zero the week (#71).
    tl_csp, tl_cc = _week_premium_from_trade_log(week_monday)
    if tl_csp is not None:
        csp_premium, cc_premium = tl_csp, tl_cc

    state = _load_state()
    prev  = state.get("weekly_pnl", {})
    # Likewise don't let a re-run with no new share sales zero a realized stock
    # P&L already recorded for this same week.
    if not shares_sold_pnl and prev.get("week_start") == week_monday:
        shares_sold_pnl = prev.get("shares_sold_pnl", 0.0)

    total_realized  = round(csp_premium + cc_premium + shares_sold_pnl, 2)
    state["weekly_pnl"] = {
        "week_start":      week_monday,
        "csp_premium":     round(csp_premium, 2),
        "cc_premium":      round(cc_premium, 2),
        "shares_sold_pnl": round(shares_sold_pnl, 2),
        "total_realized":  total_realized,
        "last_updated":    datetime.now().isoformat(),
    }
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

    try:
        from discord_poster import is_enabled, post_weekly_results
        if is_enabled():
            settings = get_settings()
            capital  = settings.get("fund_budget", TOTAL_FUND_BUDGET)
            goal_pct = settings.get("goal_pct", 0.24)
            nl       = net_liq
            if nl is None:
                try:
                    _, nl = _fetch_account_summary(capital)
                except Exception:
                    nl = None
            post_weekly_results(_load_state(), fund_budget=fund_budget,
                                capital=capital, goal_pct=goal_pct, net_liq=nl)
    except Exception as e:
        log.warning(f"  ⚠️  Discord weekly results post failed: {e}")

    return total_realized


def _fetch_account_summary(fallback: float) -> tuple[float, float]:
    """
    Fetch IBKR BuyingPower and NetLiquidation (read-only). Returns
    (effective_budget, net_liq); falls back to (fallback, fallback) on any error.
    Mirrors scheduler._fetch_account_summary so live budgeting is identical.
    """
    try:
        from ib_insync import IB
        ib = IB()
        ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID, readonly=True)
        summary = ib.accountSummary(ACCOUNT)
        ib.disconnect()
        by_tag  = {v.tag: float(v.value) for v in summary
                   if v.tag in ("BuyingPower", "NetLiquidation")}
        bp      = by_tag.get("BuyingPower")
        net_liq = by_tag.get("NetLiquidation")
        if bp and bp > 0 and net_liq and net_liq > 0:
            effective = min(bp, net_liq)
            log.info(f"  💹 Compound mode: net_liq=${net_liq:,.0f}  "
                     f"buying_power=${bp:,.0f}  using=${effective:,.0f}")
            return effective, net_liq
        log.warning("  ⚠️  Account summary incomplete — using fund budget fallback")
    except Exception as e:
        log.warning(f"  ⚠️  Could not fetch account summary ({e}) — using fund budget fallback")
    return fallback, fallback


# ── CSP pipeline (Monday 10:00 half) ───────────────────────────

def run_csp_pipeline(context: dict, dry_run: bool = False,
                     progress_callback=None, account_summary: tuple = None) -> dict:
    """
    Screen → size → (execute) CSPs, applying the wheel check's results.

    context provides the outputs of run_wheel_check (or a persisted monday_context):
      skip_tickers, freed_capital, reserved_capital, active_wheel_count,
      cc_premium, shares_sold_pnl

    dry_run: size only, place no orders, write no state, post no Discord.
    account_summary: optional (buying_power, net_liq) to avoid an extra IBKR
      connection during a preview; when None and compounding, it is fetched live.

    Note: module (re)loading is the caller's responsibility — the API endpoints
    reload config/screener/etc. before calling so dashboard changes take effect;
    the long-running scheduler deliberately does not reload mid-process.
    """
    from screener import get_top_targets
    from position_sizer import size_all

    # Recovery dedup: tickers that already have an open short put (from the wheel
    # check's live IBKR snapshot). We skip re-selling them and count them toward
    # the fill target so a re-run only fills the *remaining* slots.
    already_open_puts  = list(context.get("open_short_put_tickers", []))
    skip_tickers       = set(context.get("skip_tickers", [])) | set(already_open_puts)
    freed_capital      = context.get("freed_capital", 0.0)
    reserved_capital   = context.get("reserved_capital", 0.0)
    active_wheel_count = context.get("active_wheel_count", 0)
    open_csp_count     = len(already_open_puts)

    mode = "DRY RUN" if dry_run else "LIVE"
    log.info(f"⏰ CSP PIPELINE [{mode}]")
    if context.get("skip_tickers"):
        log.info(f"  🚫 Skipping wheel-exit tickers: {set(context.get('skip_tickers', []))}")
    if already_open_puts:
        log.info(f"  ♻️  CSP already open (recovery): {already_open_puts}")
    if freed_capital:
        log.info(f"  💰 Freed capital added to pool: ${freed_capital:,.0f}")

    # Read sizing settings LIVE so the long-running scheduler matches the
    # module-reloading preview/Run-Now paths — Run Screener, Run Now and the
    # live 9:55 run all size against the same num_positions / fund_budget.
    settings         = get_settings()
    compound_enabled = settings.get("compound_enabled", True)
    num_positions    = settings.get("num_positions", NUM_POSITIONS)
    fund_budget      = settings.get("fund_budget", TOTAL_FUND_BUDGET)

    # Remaining CSP slots = total − wheel holdings − already-open CSPs
    target_fills = max(0, num_positions - active_wheel_count - open_csp_count)

    if target_fills <= 0:
        log.info(f"  ✅ No remaining CSP slots to fill "
                 f"({active_wheel_count} wheel + {open_csp_count} open CSP = {num_positions} cap)")
        result = {
            "positions": [], "raw_targets": [], "filtered_count": 0,
            "effective_budget": 0, "target_fills": 0, "total_premium": 0,
            "total_capital": 0, "compound_enabled": compound_enabled,
            "already_open_put_tickers": already_open_puts,
            "executed": not dry_run,
        }
        if not dry_run:
            # No CSPs ran this week, so trader.execute_positions never executed and
            # never refreshed the CSP-run fields — they'd keep the PRIOR week's run.
            # Clear them here so the Discord weekly post and the Trade-History
            # "Current Week" view don't resurface last week's executions/positions.
            # Must happen BEFORE _write_weekly_pnl, which re-loads state to build the
            # Discord "This Week's Trades" section from state["executions"].
            state = _load_state()
            state["run_date"]      = datetime.now().isoformat()
            state["positions"]     = []
            state["executions"]    = []
            state["filled_count"]  = 0
            state["total_premium"] = 0
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
            _write_weekly_pnl(0.0, context)
        return result

    all_targets = get_top_targets(10)
    filtered_targets = [t for t in all_targets if t["ticker"] not in skip_tickers]

    net_liq = None  # captured below for the weekly account-value goal post
    if compound_enabled:
        if account_summary is not None:
            buying_power, net_liq = account_summary
        else:
            buying_power, net_liq = _fetch_account_summary(fund_budget)
        # Exclude capital already tied up in wheel holdings. buying_power is
        # min(BuyingPower, NetLiq): for cash/Roth accounts BuyingPower already
        # excludes wheel stock, but on margin/paper accounts it resolves to
        # NetLiq, which INCLUDES the wheel stock — so without this cap we'd
        # secure new CSPs with capital that's already deployed. Capping at
        # net_liq − reserved removes that double-count; min() keeps whichever
        # basis is tighter, and it's a no-op when there are no wheel holdings.
        capped_budget    = min(buying_power, net_liq - reserved_capital)
        effective_budget = capped_budget + freed_capital
        log.info(f"  📊 Budget: buying_power=${buying_power:,.0f}  net_liq=${net_liq:,.0f}  "
                 f"reserved=${reserved_capital:,.0f}  freed=${freed_capital:,.0f}  "
                 f"effective=${effective_budget:,.0f}  (compounding ON)")
    else:
        effective_budget = fund_budget + freed_capital - reserved_capital
        log.info(f"  📊 Budget: base=${fund_budget:,.0f}  freed=${freed_capital:,.0f}  "
                 f"reserved=${reserved_capital:,.0f}  effective=${effective_budget:,.0f}  (compounding OFF)")

    log.info(f"  🔢 Filling {target_fills} CSP slot(s)  "
             f"({active_wheel_count} wheel + {open_csp_count} open CSP already deployed)")
    positions    = size_all(filtered_targets, budget=effective_budget,
                            num_positions=target_fills)

    total_premium = sum(p.get("premium_total", 0) for p in positions)
    total_capital = sum(p.get("capital_used", 0) for p in positions)

    base = {
        "positions":               positions,
        "raw_targets":             all_targets,
        "filtered_count":          len(all_targets) - len(filtered_targets),
        "effective_budget":        effective_budget,
        "target_fills":            target_fills,
        "total_premium":           total_premium,
        "total_capital":           total_capital,
        "compound_enabled":        compound_enabled,
        "already_open_put_tickers": already_open_puts,
    }

    if dry_run:
        log.info(f"  🟡 [DRY RUN] sized {len(positions)} CSP(s) — no orders placed")
        base["executed"] = False
        return base

    # ── Live execution ────────────────────────────────────────
    from trader import execute_positions
    results = execute_positions(positions, extra_targets=filtered_targets,
                                target_fills=target_fills, status_callback=progress_callback)

    filled      = [r for r in results if r.get("status") in ("filled", "dry_run", "partial_fill")]
    csp_premium = sum(r.get("premium_collected", 0) for r in results)
    total_realized = _write_weekly_pnl(csp_premium, context, fund_budget=effective_budget,
                                        net_liq=net_liq)

    log.info(f"✅ CSP pipeline done — {len(filled)}/{target_fills} fills  "
             f"CSP ${csp_premium:,.0f}  CC ${context.get('cc_premium', 0.0):,.0f}  total ${total_realized:,.0f}")

    base.update({
        "executed":      True,
        "results":       results,
        "fills":         len(filled),
        "csp_premium":   round(csp_premium, 2),
        "total_realized": total_realized,
    })
    return base


# ── Full Monday sequence (wheel check → CSP pipeline) ──────────

def run_monday(dry_run: bool = False, progress_callback=None,
               account_summary: tuple = None) -> dict:
    """
    Run the complete Monday sequence in one call: wheel check then CSP pipeline,
    using the wheel check's in-memory results to drive the pipeline.

    dry_run=True  → faithful preview, zero side effects (Run Screener)
    dry_run=False → live execution: sells shares, writes CCs, opens CSPs (Run Now)
    """
    from wheel_manager import run_wheel_check

    mode = "DRY RUN" if dry_run else "LIVE"
    log.info("\n" + "=" * 65)
    log.info(f"🗓️  MONDAY RUNNER [{mode}] — {datetime.now(PST).strftime('%Y-%m-%d %H:%M %Z')}")
    log.info("=" * 65)

    wheel = run_wheel_check(dry_run=dry_run, client_id=IBKR_CLIENT_ID_PREVIEW,
                            progress_callback=progress_callback)
    csp   = run_csp_pipeline(wheel, dry_run=dry_run,
                             progress_callback=progress_callback,
                             account_summary=account_summary)

    return {"dry_run": dry_run, "wheel": wheel, "csp": csp}
