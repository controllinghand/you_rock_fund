"""
Cash sweep — park the week's undeployed remainder in a liquid instrument.

Most brokerages sweep idle cash into a money-market fund so it's always earning.
This does the DIY version for the fund: after the Monday option workflow finishes,
buy the configured instrument (QQQ or SGOV) with the leftover cash, then liquidate
it on the last trading day of the week so the cash is back for Monday.

Two entry points, both driven by settings (feature is OFF by default):

  maybe_buy_park(csp_outcome, context, dry_run, ...)   — Monday, after CSPs execute
  sell_park(dry_run, ...)                               — end of week (Thu/Fri job)

Design guards (see the buy path):
  • Idle-cash basis = IBKR Buying Power (real settled cash on a cash account; a live
    run reads it AFTER the CSPs execute, a preview subtracts the planned CSP capital).
  • Buy amount = min(idle(+premium), settled cash [, 10% of net-liq]). The
    settled-cash cap means it can NEVER reach into margin. The 10% net-liq cap is a
    SAFETY that only applies when some option slots went unfilled (partial/broken
    run) — when every slot is filled the idle cash is genuinely free, so the FULL
    amount is parked.
  • Fractional via IBKR cashQty (spend the exact dollar amount).
  • Reconciles an already-open position so a failed prior sell isn't double-bought
    or stranded.
  • Honors the Settings "Dry Run" toggle (simulate, place no order) exactly like the
    wheel/CSP paths.
"""
import json
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from ib_insync import IB, Stock, Order

from config import (
    IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID_CASH_PARK, ACCOUNT, ACCOUNT_TYPE,
    MODE_LABEL, get_settings, gateway_unreachable_message, probe_port,
)
from market_calendar import is_last_trading_day_of_week
from secrets_client import get_secret

STATE_FILE       = "state.json"
MARKET_WAIT_SECS = 60
MARKET_POLL_SECS = 5
# Technical minimum only (not a user-facing floor — fractional shares mean any
# amount "works"): avoids sending a sub-dollar order IBKR would just reject.
MIN_BUY_USD      = 1.0
# Never park more than this share of net-liquidation, regardless of idle cash.
NET_LIQ_CAP_PCT  = 0.10

PST = ZoneInfo("America/Los_Angeles")

log = logging.getLogger(__name__)
if not any(getattr(h, "_cash_park", False) for h in log.handlers):
    _fh = logging.FileHandler("cash_park_log.txt")
    _fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)s  %(message)s"))
    _fh._cash_park = True
    log.addHandler(_fh)
    log.setLevel(logging.INFO)


# ── State ──────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── Discord ────────────────────────────────────────────────────

def _discord_alert(message: str) -> None:
    """Plain-text Discord alert (mirrors scheduler._discord_alert). No-op when no
    webhook is configured or Discord is disabled in settings."""
    try:
        if not get_settings().get("discord_webhook_enabled", True):
            return
        webhook_url = get_secret("discord_webhook_url", "DISCORD_WEBHOOK_URL")
        if not webhook_url:
            return
        import requests
        from pathlib import Path
        _vf  = Path(__file__).parent / "VERSION"
        _v   = f"v{_vf.read_text().strip()}" if _vf.exists() else "?"
        _tag = f"`{_v} · {ACCOUNT}`" if ACCOUNT else f"`{_v}`"
        requests.post(webhook_url,
                      json={"content": f"{message}\n{MODE_LABEL} · {_tag}"}, timeout=5)
    except Exception as e:
        log.warning(f"Discord alert failed: {e}")


# ── IBKR helpers ───────────────────────────────────────────────

def _connect(client_id: int = None) -> IB:
    client_id = client_id if client_id is not None else IBKR_CLIENT_ID_CASH_PARK
    log.info(f"🔌 Connecting to IB Gateway {IBKR_HOST}:{IBKR_PORT} ({ACCOUNT_TYPE}, clientId={client_id})")
    for attempt in range(1, 4):
        try:
            ib = IB()
            ib.connect(IBKR_HOST, IBKR_PORT, clientId=client_id)
            ib.reqMarketDataType(3)   # delayed-frozen ok — ETFs are penny-wide
            log.info(f"✅ Connected to IBKR (clientId={client_id})")
            return ib
        except TimeoutError:
            port_open = probe_port(IBKR_HOST, IBKR_PORT)
            log.warning(f"⚠️  IBKR connect attempt {attempt}/3 timed out — "
                        f"TCP port {'OPEN (handshake hung)' if port_open else 'CLOSED'}")
            if attempt < 3:
                time.sleep(10)
    raise TimeoutError(gateway_unreachable_message(IBKR_HOST, IBKR_PORT))


def _account_summary(ib: IB) -> tuple:
    """(total_settled_cash, net_liquidation, buying_power). All None on error.

    - BuyingPower drives the idle-cash basis: on a cash account it's real settled
      cash and already excludes capital tied up in wheel stock + reserved collateral
      (same figure the 'Cash Account' mode deploys as the CSP budget).
    - TotalCashValue is the no-margin cap: max(0, it) blocks any margin account (it
      goes negative when the account is borrowing), so the sweep never uses leverage."""
    try:
        summary = ib.accountSummary(ACCOUNT)
        by_tag  = {}
        for v in summary:
            if v.tag in ("TotalCashValue", "NetLiquidation", "BuyingPower"):
                # Prefer the base-currency / USD row; accountSummary may repeat tags.
                if v.tag not in by_tag or v.currency in ("USD", "BASE", ""):
                    by_tag[v.tag] = float(v.value)
        return (by_tag.get("TotalCashValue"), by_tag.get("NetLiquidation"),
                by_tag.get("BuyingPower"))
    except Exception as e:
        log.warning(f"⚠️  Could not read account summary: {e}")
        return None, None, None


def _price(ib: IB, ticker: str):
    contract = Stock(ticker, "SMART", "USD")
    q = ib.qualifyContracts(contract)
    if not q:
        return None, None
    data = ib.reqMktData(q[0], snapshot=False)
    ib.sleep(3)
    px = (data.last  if data.last  and data.last  > 0 else
          data.close if data.close and data.close > 0 else
          data.bid   if data.bid   and data.bid   > 0 else None)
    ib.cancelMktData(q[0])
    ib.sleep(0.3)
    return (q[0], round(float(px), 2)) if px else (q[0], None)


def _held_shares(ib: IB, ticker: str) -> float:
    """Actual IBKR position (shares) for `ticker` — the reconciliation source of
    truth for the sell, so we never try to sell more than we really hold."""
    try:
        for p in ib.positions(ACCOUNT):
            c = p.contract
            if c.symbol == ticker and c.secType == "STK":
                return float(p.position)
    except Exception as e:
        log.warning(f"⚠️  Could not read positions: {e}")
    return 0.0


def _poll_fill(ib: IB, trade) -> bool:
    elapsed = 0
    while elapsed < MARKET_WAIT_SECS:
        ib.sleep(MARKET_POLL_SECS)
        elapsed += MARKET_POLL_SECS
        st  = trade.orderStatus.status
        rem = trade.orderStatus.remaining
        fl  = trade.orderStatus.filled
        if st == "Filled" or (rem == 0 and fl and fl > 0):
            return True
        # Terminal rejection/cancel (e.g. Error 10244 cashQty reject) — stop
        # waiting the full window; the caller can escalate immediately.
        if st in ("Cancelled", "ApiCancelled", "Inactive") and not fl:
            log.info(f"  ⛔ order {st} (filled {fl}) — not waiting out the window")
            return False
        log.info(f"  ⏳ order {st}: filled {fl} after {elapsed}s")
    return False


# ── Buy (Monday, after the option workflow) ────────────────────

def maybe_buy_park(csp_outcome: dict, context: dict, dry_run: bool = False,
                   client_id: int = None, ib: IB = None) -> dict | None:
    """Buy the configured instrument with the account's idle settled cash.

    csp_outcome: return of run_csp_pipeline (total_capital, target_fills, fills,
                 csp_premium, positions).
    context:     wheel-check result (cc_premium).
    dry_run:     True on a preview (Run Screener) — no real fills exist yet, so the
                 sized position count is used as the fill proxy and the planned CSP
                 capital is subtracted from Buying Power to estimate the leftover.

    Returns a small result dict (status + details) or None when the feature is off.
    Never raises into the caller — a sweep failure must not break the Monday run.
    """
    s = get_settings()
    if not s.get("cash_park_enabled", False):
        return None

    instrument   = (s.get("cash_park_instrument") or "QQQ").strip().upper()
    include_prem = bool(s.get("cash_park_include_premiums", False))
    # Simulate (no real order) on a preview OR when the Settings Dry Run toggle is on.
    orders_dry_run = dry_run or bool(s.get("dry_run", False))

    def _finish(result: dict) -> dict:
        """Attach common fields, persist the last decision under a SEPARATE key (so
        an open position block is never clobbered), and return it. This is what makes
        the outcome visible on the dashboard — even a skip.

        Persist on any NON-preview run — i.e. Run Now / the scheduled Monday job —
        even when the Settings 'Dry Run' toggle is on (`orders_dry_run`), so the
        dashboard reflects a simulated Run Now the same way the wheel/CSP paths write
        simulated state. Only the true preview (`dry_run`, Run Screener) stays
        side-effect-free — it surfaces the result in-band via the API response.
        The `dry_run` flag on the result marks a simulated decision."""
        result.setdefault("instrument", instrument)
        result["dry_run"]      = orders_dry_run
        result["evaluated_at"] = datetime.now().isoformat()
        if not dry_run:
            try:
                st = _load_state()
                st["cash_park_last_eval"] = result
                _save_state(st)
            except Exception as e:
                log.warning(f"could not persist cash_park_last_eval: {e}")
        return result

    # Fill status — used ONLY to pick the safety cap below, NOT to skip. When every
    # slot is filled the idle cash is genuinely leftover (park the full amount); if
    # some slots went unfilled (partial/broken run) the 10% net-liq cap kicks in as a
    # safety. On a preview nothing executed, so use the sized count as the proxy.
    fills  = len(csp_outcome.get("positions", [])) if dry_run else csp_outcome.get("fills", 0)
    target = csp_outcome.get("target_fills", 0)
    all_filled = fills >= target

    owns = _connect(client_id) if ib is None else None
    ib   = ib or owns
    try:
        # ── Base = the CSP budget remainder (the intuitive "leftover after this
        # week's selections"): effective_budget − CSP capital deployed. Bounded by the
        # budget, so it can't over-park from inflated margin buying power; and because
        # the budget respects the account type (buying_power for cash, net_liq−reserved
        # for margin) and net_liq is stable across the stock→cash conversion, the
        # preview number matches what a live Monday run parks.
        total_cash, net_liq, buying_power = _account_summary(ib)
        remainder = max(0.0, (csp_outcome.get("effective_budget", 0.0) or 0.0)
                        - (csp_outcome.get("total_capital", 0.0) or 0.0))
        base = remainder
        if include_prem:
            base += (csp_outcome.get("csp_premium", 0.0) or 0.0) \
                  + (context.get("cc_premium", 0.0) or 0.0)

        # No-margin cap = real settled cash. On a DRY preview the stop-loss/screener
        # sales haven't executed yet, so add the freed proceeds to model the POST-sale
        # cash — otherwise a stop-loss week is wrongly zeroed by the CURRENT (pre-sale)
        # negative settled cash. A live run reads settled cash AFTER the sales, so no
        # adjustment. The 10% net-liq cap is ONLY a safety for a partial (unfilled) run.
        freed       = context.get("freed_capital", 0.0) or 0.0
        settled_eff = (total_cash + freed) if (dry_run and total_cash is not None) else total_cash
        cash_cap    = max(0.0, settled_eff) if settled_eff is not None else base
        netliq_cap  = NET_LIQ_CAP_PCT * net_liq if net_liq and net_liq > 0 else base
        buy_amount  = round(min(base, cash_cap) if all_filled
                            else min(base, cash_cap, netliq_cap), 2)

        log.info(f"  🅿️  Cash sweep: remainder=${remainder:,.2f}  base=${base:,.2f}  "
                 f"settled(eff)=${cash_cap:,.2f}  10%netliq=${netliq_cap:,.2f}  slots={fills}/{target} "
                 f"{'(all filled → full)' if all_filled else '(partial → 10% cap)'}  "
                 f"→ buy=${buy_amount:,.2f} of {instrument}")

        caps = {"base": round(base, 2), "remainder": round(remainder, 2),
                "settled_cash": round(total_cash, 2) if total_cash is not None else None,
                "settled_effective": round(cash_cap, 2),
                "buying_power": round(buying_power, 2) if buying_power is not None else None,
                "netliq_cap": round(netliq_cap, 2), "all_slots_filled": all_filled,
                "fills": fills, "target": target, "buy_amount": buy_amount}

        if buy_amount < MIN_BUY_USD:
            if remainder < MIN_BUY_USD:
                reason = "no remainder left after this week's CSP deployment"
            elif cash_cap < MIN_BUY_USD:
                reason = f"no settled cash (${(settled_eff or 0):,.0f}) — would require margin"
            else:
                reason = (f"remainder ${remainder:,.0f} capped by settled cash ${cash_cap:,.0f}"
                          + ("" if all_filled else f" / 10% net-liq ${netliq_cap:,.0f} (partial run)"))
            log.info(f"  💤 Nothing to park (${buy_amount:,.2f}) — {reason}")
            return _finish({"status": "skipped_no_cash", **caps,
                            "message": f"No cash swept — {reason}"})

        # ── Reconcile: don't double-buy if a prior park never sold ──
        state = _load_state()
        existing = state.get("cash_park")
        if existing and existing.get("status") == "open" and existing.get("shares"):
            log.warning(f"  ⚠️  Prior {existing.get('instrument')} park still open — not buying again")
            if not orders_dry_run:
                _discord_alert(f"⚠️ **YRVI** Cash sweep: a prior {existing.get('instrument')} park "
                               f"({existing.get('shares')} sh) is still OPEN — not buying again. "
                               f"It will be sold on the next end-of-week job.")
            return _finish({"status": "skipped_existing_open", **caps,
                            "message": f"{existing.get('instrument')} position from a prior week "
                                       f"still open — not buying again"})

        if orders_dry_run:
            _, px = _price(ib, instrument)
            sh    = round(buy_amount / px, 4) if px else None
            log.info(f"  🟡 [DRY RUN] would BUY ~${buy_amount:,.2f} of {instrument}"
                     + (f" (~{sh} sh @ ${px:.2f})" if px else ""))
            return _finish({"status": "dry_run", **caps, "est_shares": sh, "est_price": px,
                            "message": f"[Preview] Would park ${buy_amount:,.0f} in {instrument}"
                                       + (f" (~{sh} sh @ ${px:.2f})" if px else " (price unavailable — market closed)")})

        # ── Live buy: fractional via cashQty (spend the exact dollars) ──
        contract = Stock(instrument, "SMART", "USD")
        q = ib.qualifyContracts(contract)
        if not q:
            log.error(f"  ❌ Cannot qualify {instrument} — sweep aborted")
            _discord_alert(f"❌ **YRVI** Cash sweep: could not qualify {instrument} — no park this week.")
            return _finish({"status": "failed_qualify", **caps,
                            "message": f"Failed — could not qualify {instrument}"})

        # Tier 1 — fractional via cashQty (spend the exact dollars). Supported on
        # accounts with fractional/monetary-order permission.
        order = Order(action="BUY", orderType="MKT", cashQty=buy_amount,
                      tif="DAY", account=ACCOUNT)
        log.info(f"  📥 BUY ${buy_amount:,.2f} {instrument} at market (cashQty)")
        trade = ib.placeOrder(q[0], order)
        filled_ok = _poll_fill(ib, trade)

        # Tier 2 — whole-share fallback. Some accounts (paper, and any without
        # fractional trading) reject cashQty with Error 10244. If NOTHING filled,
        # retry as a plain whole-share market order for floor($ / price) shares —
        # the same order style the sell side uses. The sub-1-share remainder (well
        # under one QQQ/SGOV share) just stays as cash for the week.
        if not filled_ok and not float(trade.orderStatus.filled or 0.0):
            ib.cancelOrder(trade.order)
            ib.sleep(1)
            _, px = _price(ib, instrument)
            whole = int(buy_amount // px) if px else 0
            if whole >= 1:
                log.info(f"  🔁 cashQty unfilled — falling back to {whole} whole "
                         f"share(s) of {instrument} @ ${px:.2f}")
                order = Order(action="BUY", orderType="MKT", totalQuantity=whole,
                              tif="DAY", account=ACCOUNT)
                trade = ib.placeOrder(q[0], order)
                filled_ok = _poll_fill(ib, trade)
            else:
                log.error(f"  ❌ {instrument} price unavailable or ${buy_amount:,.0f} "
                          f"< 1 share — cannot fall back")

        if not filled_ok:
            log.error(f"  ❌ {instrument} buy did not fill in {MARKET_WAIT_SECS}s")
            _discord_alert(f"❌ **YRVI** Cash sweep: {instrument} BUY (${buy_amount:,.0f}) "
                           f"did not fill — MANUAL CHECK.")
            return _finish({"status": "failed_no_fill", **caps,
                            "message": f"Failed — {instrument} buy did not fill"})

        shares = round(float(trade.orderStatus.filled or 0.0), 4)
        fill   = round(float(trade.orderStatus.avgFillPrice or 0.0), 4)
        cost   = round(shares * fill, 2)
        now    = datetime.now().isoformat()
        state = _load_state()   # reload — the pipeline wrote weekly_pnl meanwhile
        state["cash_park"] = {
            "instrument":   instrument,
            "shares":       shares,
            "buy_price":    fill,
            "cost_basis":   cost,
            "buy_date":     now,
            "status":       "open",
            "sell_price":   None,
            "realized_pnl": None,
            "last_checked": now,
        }
        _save_state(state)
        log.info(f"  ✅ Parked ${cost:,.2f}: {shares} {instrument} @ ${fill:.2f}")
        _discord_alert(f"🅿️ **YRVI** Cash sweep — parked **${cost:,.0f}** in "
                       f"**{instrument}** ({shares} sh @ ${fill:.2f}). Sells end of week.")
        return _finish({"status": "bought", **caps, "shares": shares,
                        "buy_price": fill, "cost_basis": cost,
                        "message": f"Parked ${cost:,.0f} in {instrument} ({shares} sh @ ${fill:.2f})"})
    except Exception as e:
        log.error(f"❌ Cash sweep buy error: {e}", exc_info=True)
        _discord_alert(f"❌ **YRVI** Cash sweep buy failed: `{type(e).__name__}: {e}`")
        return _finish({"status": "error", "error": str(e), "message": f"Error: {type(e).__name__}"})
    finally:
        if owns is not None:
            owns.disconnect()


# ── Sell (end of week) ─────────────────────────────────────────

def sell_park(dry_run: bool = False, client_id: int = None, ib: IB = None) -> dict | None:
    """Liquidate the parked position. Runs regardless of the enabled toggle so a
    position is never stranded if the user turns the feature off mid-week. Returns
    None when there's nothing to sell."""
    s = get_settings()
    orders_dry_run = dry_run or bool(s.get("dry_run", False))

    state = _load_state()
    cp = state.get("cash_park")
    if not cp or cp.get("status") != "open" or not cp.get("shares"):
        log.info("  💤 No open cash-park position to sell.")
        return None

    instrument = cp["instrument"]
    want       = round(float(cp["shares"]), 4)
    cost_basis = float(cp.get("cost_basis") or 0.0)

    owns = _connect(client_id) if ib is None else None
    ib   = ib or owns
    try:
        # Reconcile against the real position — never sell more than we hold.
        held   = _held_shares(ib, instrument)
        shares = round(min(want, held), 4) if held > 0 else want
        if held <= 0:
            log.warning(f"  ⚠️  State says {want} {instrument} parked but IBKR shows none — "
                        f"marking closed without an order.")
            cp.update({"status": "sold", "sell_price": None, "realized_pnl": 0.0,
                       "sold_date": datetime.now().isoformat(),
                       "note": "no position at IBKR — reconciled closed"})
            state["cash_park"] = cp
            if not orders_dry_run:
                _save_state(state)
            return {"status": "reconciled_none", "instrument": instrument}

        if orders_dry_run:
            _, px = _price(ib, instrument)
            proceeds = round(shares * px, 2) if px else None
            pnl = round(proceeds - cost_basis, 2) if proceeds is not None else None
            log.info(f"  🟡 [DRY RUN] would SELL {shares} {instrument}"
                     + (f" @ ~${px:.2f} = ${proceeds:,.2f} (P&L ${pnl:+,.2f})" if px else ""))
            return {"status": "dry_run", "instrument": instrument, "shares": shares,
                    "proceeds": proceeds, "realized_pnl": pnl}

        contract = Stock(instrument, "SMART", "USD")
        if not ib.qualifyContracts(contract):
            log.error(f"  ❌ Cannot qualify {instrument} for sale")
            _discord_alert(f"❌ **YRVI** Cash sweep: could not qualify {instrument} to sell — MANUAL CHECK.")
            return {"status": "failed_qualify", "instrument": instrument}

        order = Order(action="SELL", orderType="MKT", totalQuantity=shares,
                      tif="DAY", account=ACCOUNT)
        log.info(f"  📤 SELL {shares} {instrument} at market")
        trade = ib.placeOrder(contract, order)
        if not _poll_fill(ib, trade):
            log.error(f"  ❌ {instrument} sale did not fill in {MARKET_WAIT_SECS}s")
            _discord_alert(f"❌ **YRVI** Cash sweep: {instrument} SELL ({shares} sh) "
                           f"did not fill — MANUAL CHECK, position still open.")
            return {"status": "failed_no_fill", "instrument": instrument}

        filled   = round(float(trade.orderStatus.filled or 0.0), 4)
        fill     = round(float(trade.orderStatus.avgFillPrice or 0.0), 4)
        proceeds = round(filled * fill, 2)
        realized = round(proceeds - cost_basis, 2)
        now      = datetime.now().isoformat()

        state = _load_state()
        cp = state.get("cash_park", cp)
        cp.update({"status": "sold", "sell_price": fill, "shares": filled,
                   "proceeds": proceeds, "realized_pnl": realized,
                   "sold_date": now, "last_checked": now})
        state["cash_park"] = cp
        _record_park_pnl(state, realized)
        _save_state(state)

        log.info(f"  ✅ Cash sweep closed: sold {filled} {instrument} @ ${fill:.2f} "
                 f"= ${proceeds:,.2f}  (P&L ${realized:+,.2f})")
        sign = "🟢" if realized >= 0 else "🔴"
        _discord_alert(f"💵 **YRVI** Cash sweep closed — sold {filled} **{instrument}** "
                       f"@ ${fill:.2f} = ${proceeds:,.0f}. {sign} P&L **${realized:+,.0f}**")
        return {"status": "sold", "instrument": instrument, "shares": filled,
                "sell_price": fill, "proceeds": proceeds, "realized_pnl": realized}
    except Exception as e:
        log.error(f"❌ Cash sweep sell error: {e}", exc_info=True)
        _discord_alert(f"❌ **YRVI** Cash sweep sell failed: `{type(e).__name__}: {e}`")
        return {"status": "error", "error": str(e)}
    finally:
        if owns is not None:
            owns.disconnect()


def _record_park_pnl(state: dict, realized: float) -> None:
    """Fold the sweep's realized P&L into this week's weekly_pnl (park_pnl field +
    total_realized). weekly_pnl was written Monday; the sell lands later the same
    week, so we add park_pnl to the existing components."""
    wp = state.get("weekly_pnl")
    if not isinstance(wp, dict):
        return
    wp["park_pnl"] = round(realized, 2)
    wp["total_realized"] = round(
        (wp.get("csp_premium") or 0.0)
        + (wp.get("cc_premium") or 0.0)
        + (wp.get("shares_sold_pnl") or 0.0)
        + realized, 2)
    wp["last_updated"] = datetime.now().isoformat()


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)s  %(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sell"
    if cmd == "sell":
        print(sell_park(dry_run="--dry" in sys.argv))
    else:
        print("usage: python cash_park.py sell [--dry]")
