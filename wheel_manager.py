"""
Wheel Strategy Manager

detect_assignments() — Friday 4:15PM PST
    Scan IBKR for stock positions created by put assignments.
    Persist to state.json["wheel_holdings"].

run_wheel_check() — Monday 9:55AM PST (runs before CSP pipeline)
    For each held stock, four-step evaluation:
      Step 1  Screener check: if ticker dropped from screener → sell at market
      Step 2  Option chain: find highest call strike >= assigned_strike
              where abs(delta) >= 0.20 (the "20-delta CC")
      Step 3  Decision: sell CC if viable strike found; else sell at market
      Step 4  Persist monday_context + wheel_activity to state.json

    Returns (freed_capital, skip_tickers) for run_pipeline to consume.
"""
import json
import logging
from datetime import datetime, timedelta
from ib_insync import IB, Stock, Option, LimitOrder, MarketOrder

from config import IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID_WHEEL, ACCOUNT
from screener import get_all_candidates
import discord_poster

STATE_FILE       = "state.json"
TRADE_LOG_JSON   = "trade_log.json"
MID_WAIT_SECS    = 120
BID_WAIT_SECS    = 120
MARKET_WAIT_SECS = 60
MARKET_POLL_SECS = 5
CC_DELTA_MIN     = 0.20   # minimum call delta required to sell a covered call
MAX_CC_STRIKES   = 25     # max option strikes to evaluate per holding

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.FileHandler("wheel_log.txt"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


# ── State ──────────────────────────────────────────────────────

def _append_trade_log(record: dict) -> None:
    """Upsert one execution record into trade_log.json, keyed on symbol+expiry+strike+right."""
    try:
        with open(TRADE_LOG_JSON) as f:
            entries = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        entries = []
    key = (record.get("symbol"), record.get("expiry"), record.get("strike"), record.get("right"))
    for i, e in enumerate(entries):
        if (e.get("symbol"), e.get("expiry"), e.get("strike"), e.get("right")) == key:
            entries[i] = record
            break
    else:
        entries.append(record)
    with open(TRADE_LOG_JSON, "w") as f:
        json.dump(entries, f, indent=2)


def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── IBKR ───────────────────────────────────────────────────────

def _connect() -> IB:
    ib = IB()
    ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID_WHEEL)
    ib.reqMarketDataType(3)
    log.info(f"✅ Connected to IBKR (clientId={IBKR_CLIENT_ID_WHEEL})")
    return ib


def _is_nan(val) -> bool:
    try:
        return val != val
    except Exception:
        return True


def _next_friday_expiry() -> str:
    today      = datetime.now().date()
    days_ahead = 4 - today.weekday()   # Monday=0 → days_ahead=4 (this Friday)
    if days_ahead <= 0:
        days_ahead += 7
    return (today + timedelta(days=days_ahead)).strftime("%Y%m%d")


# ── Option chain ───────────────────────────────────────────────

def _find_cc_strike(ib: IB, ticker: str, expiry: str,
                    assigned_strike: float) -> tuple | None:
    """
    Scan the IBKR call option chain for the highest strike >= assigned_strike
    on expiry where abs(delta) >= CC_DELTA_MIN.

    Call delta decreases monotonically as strike increases (more OTM → lower
    delta), so the highest qualifying strike is the closest to CC_DELTA_MIN
    from above — the "20-delta covered call."

    All market data streams are opened simultaneously and read after a single
    sleep, making this O(1) in wall-clock time regardless of how many strikes
    are checked.

    Returns (strike, delta, mid_price) or None if no viable strike found.
    """
    stock = Stock(ticker, "SMART", "USD")
    q_stock = ib.qualifyContracts(stock)
    if not q_stock:
        log.warning(f"  ⚠️  {ticker}: cannot qualify stock for option chain lookup")
        return None

    stock_data = ib.reqMktData(q_stock[0], "", snapshot=True)
    ib.sleep(2)
    current_price = stock_data.last or stock_data.close or 0
    ib.cancelMktData(q_stock[0])

    chains = ib.reqSecDefOptParams(ticker, "", "STK", q_stock[0].conId)
    if not chains:
        log.warning(f"  ⚠️  {ticker}: IBKR returned no option chain data")
        return None

    all_strikes: set[float] = set()
    for chain in chains:
        if expiry in chain.expirations:
            all_strikes.update(chain.strikes)

    if not all_strikes:
        log.warning(f"  ⚠️  {ticker}: expiry {expiry} not listed in option chain")
        return None

    price_floor    = current_price * 0.95 if current_price > 0 else assigned_strike
    effective_floor = max(assigned_strike, price_floor) if assigned_strike > 0 else price_floor
    candidates = sorted(s for s in all_strikes if s >= effective_floor)
    log.info(f"  📍 {ticker}: current=${current_price:.2f}  "
             f"assigned_strike=${assigned_strike:.2f}  "
             f"scan_floor=${effective_floor:.2f}")
    if not candidates:
        log.warning(f"  ⚠️  {ticker}: no strikes >= effective floor ${effective_floor:.2f}")
        return None

    candidates = candidates[:MAX_CC_STRIKES]
    log.info(f"  📊 {ticker}: scanning {len(candidates)} call strike(s) "
             f"[${candidates[0]:.2f}–${candidates[-1]:.2f}] on {expiry}")

    # Qualify all option contracts up front
    q_pairs: list[tuple[float, object]] = []
    for strike in candidates:
        opt = Option(ticker, expiry, strike, "C", "SMART", currency="USD")
        try:
            q = ib.qualifyContracts(opt)
            if q:
                q_pairs.append((strike, q[0]))
        except Exception:
            continue

    if not q_pairs:
        log.warning(f"  ⚠️  {ticker}: no call contracts qualified on {expiry}")
        return None

    # Open all market data streams simultaneously — one sleep covers all
    streams: dict[float, tuple[object, object]] = {}
    for strike, contract in q_pairs:
        data = ib.reqMktData(contract, genericTickList="13", snapshot=False)
        streams[strike] = (contract, data)

    ib.sleep(5)

    # Read delta and mid from each stream, then cancel
    results: list[tuple[float, float, float | None]] = []
    for strike, (contract, data) in streams.items():
        ib.cancelMktData(contract)

        delta = None
        for attr in ("modelGreeks", "lastGreeks"):
            g = getattr(data, attr, None)
            if g is not None:
                d = getattr(g, "delta", None)
                if d is not None and not _is_nan(d):
                    delta = d
                    break

        if delta is None:
            continue

        bid = data.bid
        ask = data.ask
        mid = round((bid + ask) / 2, 2) \
              if (not _is_nan(bid) and not _is_nan(ask) and bid > 0 and ask > 0) \
              else None
        results.append((strike, abs(delta), mid))

    ib.sleep(0.5)

    if not results:
        log.warning(f"  ⚠️  {ticker}: no delta data returned for any call strike")
        return None

    results.sort(key=lambda x: x[0])  # ascending by strike

    log.info(f"  {'Strike':>8}  {'Delta':>7}  {'Mid':>8}")
    for strike, delta, mid in results:
        flag    = "✅" if delta >= CC_DELTA_MIN else "❌"
        mid_str = f"${mid:.2f}" if mid else "?"
        log.info(f"  ${strike:>7.2f}  {delta:>6.3f}  {mid_str:>8}  {flag}")

    # Highest qualifying strike (delta closest to CC_DELTA_MIN from above)
    viable = [(s, d, m) for s, d, m in results if d >= CC_DELTA_MIN]
    if not viable:
        log.info(f"  ❌ No call strike with delta ≥ {CC_DELTA_MIN:.2f} available")
        return None

    return (*viable[-1], current_price)   # (strike, delta, mid, stock_price)


# ── Orders ─────────────────────────────────────────────────────

def _sell_stock_market(ib: IB, ticker: str, shares: int, reason: str,
                       assigned_strike: float = 0.0) -> dict:
    contract  = Stock(ticker, "SMART", "USD")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        log.error(f"  ❌ Cannot qualify {ticker} for sale")
        return {"status": "failed", "proceeds": 0.0, "fill_price": None}

    log.info(f"  📤 SELL {shares} shares {ticker} at market  [{reason}]")
    order = MarketOrder("SELL", shares, account=ACCOUNT, tif="DAY")
    trade = ib.placeOrder(qualified[0], order)

    elapsed = 0
    while elapsed < MARKET_WAIT_SECS:
        ib.sleep(MARKET_POLL_SECS)
        elapsed += MARKET_POLL_SECS
        status    = trade.orderStatus.status
        remaining = trade.orderStatus.remaining
        filled    = trade.orderStatus.filled
        if status == "Filled" or (remaining == 0 and filled > 0):
            fill     = trade.orderStatus.avgFillPrice
            proceeds = round(shares * fill, 2)
            realized = round(proceeds - (assigned_strike * shares), 2) \
                       if assigned_strike > 0 else None
            log.info(f"  ✅ Sold: {shares}x {ticker} @ ${fill:.2f} = ${proceeds:,.0f}")
            discord_poster.post_emergency_share_sale({
                "ticker":       ticker,
                "shares":       shares,
                "fill_price":   fill,
                "proceeds":     proceeds,
                "reason":       reason,
                "realized_pnl": realized,
            })
            return {"status": "filled", "fill_price": fill, "proceeds": proceeds}
        log.info(f"  ⏳ Sell status: {status} after {elapsed}s")

    log.error(f"  ❌ Share sale timed out for {ticker} — MANUAL ACTION REQUIRED")
    return {"status": "failed", "proceeds": 0.0, "fill_price": None}


def _sell_cc_with_escalation(ib: IB, contract, shares: int, ticker: str,
                              strike: float, ref_mid: float) -> dict:
    num_contracts = shares // 100
    if num_contracts < 1:
        log.warning(f"  ⚠️  {ticker}: {shares} shares < 100 — cannot sell CC")
        return {"status": "skipped_insufficient_shares", "premium_collected": 0.0,
                "fill_price": None, "order_type": None}

    result = {
        "ticker": ticker, "option_contracts": num_contracts, "shares": shares,
        "strike": strike, "status": "unfilled", "fill_price": None,
        "order_type": None, "premium_collected": 0.0,
        "timestamp": datetime.now().isoformat()
    }

    def try_limit(price: float, label: str, wait: int) -> bool:
        log.info(f"  📤 {label}: SELL {num_contracts}x {ticker} CALL "
                 f"${strike:.2f} @ ${price:.2f}")
        order = LimitOrder("SELL", num_contracts, price, account=ACCOUNT, tif="DAY")
        trade = ib.placeOrder(contract, order)
        ib.sleep(wait)
        if trade.orderStatus.status == "Filled":
            fill = trade.orderStatus.avgFillPrice
            log.info(f"  ✅ CC filled: {ticker} @ ${fill:.2f}")
            result.update({
                "status": "filled", "fill_price": fill, "order_type": label,
                "premium_collected": round(num_contracts * fill * 100, 2)
            })
            return True
        log.info(f"  ⏳ {label} unfilled — escalating...")
        ib.cancelOrder(trade.order)
        ib.sleep(1)
        return False

    if try_limit(ref_mid, "limit_mid", MID_WAIT_SECS):
        return result
    bid_proxy = round(ref_mid * 0.90, 2)
    if try_limit(bid_proxy, "limit_bid", BID_WAIT_SECS):
        return result

    log.info(f"  📤 Market order: SELL {num_contracts}x {ticker} CALL ${strike:.2f}")
    order = MarketOrder("SELL", num_contracts, account=ACCOUNT, tif="DAY")
    trade = ib.placeOrder(contract, order)

    elapsed = 0
    while elapsed < MARKET_WAIT_SECS:
        ib.sleep(MARKET_POLL_SECS)
        elapsed += MARKET_POLL_SECS
        status    = trade.orderStatus.status
        remaining = trade.orderStatus.remaining
        filled    = trade.orderStatus.filled
        if status == "Filled" or (remaining == 0 and filled > 0):
            fill = trade.orderStatus.avgFillPrice
            log.info(f"  ✅ CC market filled: {ticker} @ ${fill:.2f} in {elapsed}s")
            result.update({
                "status": "filled", "fill_price": fill, "order_type": "market",
                "premium_collected": round(filled * fill * 100, 2)
            })
            return result
        if status == "PartiallyFilled" and filled > 0:
            log.info(f"  ⏳ Partial CC: {filled}/{num_contracts} after {elapsed}s")
        else:
            log.info(f"  ⏳ CC market status: {status} after {elapsed}s")

    final_qty = trade.orderStatus.filled
    if final_qty > 0:
        fill = trade.orderStatus.avgFillPrice
        log.warning(f"  ⚠️  CC partial fill accepted: {final_qty}/{num_contracts} @ ${fill:.2f}")
        result.update({
            "status": "partial_fill", "fill_price": fill, "order_type": "market",
            "premium_collected": round(final_qty * fill * 100, 2)
        })
    else:
        log.error(f"  ❌ CC order failed for {ticker}")
        result["status"] = "failed"
    return result


# ── Public API ─────────────────────────────────────────────────

def detect_assignments():
    """
    Friday 4:15PM PST — scan IBKR for stock positions and reconcile
    against known wheel_holdings. New assignments are added with the
    assigned strike looked up from that week's state.json positions.
    """
    log.info("\n" + "=" * 65)
    log.info(f"🔍 FRIDAY ASSIGNMENT DETECTION — "
             f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 65)

    state             = _load_state()
    existing_holdings = {h["ticker"]: h for h in state.get("wheel_holdings", [])}
    strike_lookup     = {p["ticker"]: p["strike"] for p in state.get("positions", [])}

    ib = _connect()
    try:
        ib.reqPositions()   # populate cache — positions() reads local cache only
        ib.sleep(2)
        ibkr_positions = ib.positions(account=ACCOUNT)
        stock_positions = {
            p.contract.symbol: int(p.position)
            for p in ibkr_positions
            if p.contract.secType == "STK" and int(p.position) > 0
        }
    finally:
        ib.disconnect()

    log.info(f"📊 Found {len(stock_positions)} stock position(s) in IBKR")

    if not stock_positions and existing_holdings:
        log.error(f"❌ IBKR returned 0 stock positions but {len(existing_holdings)} "
                  f"holding(s) already on record — skipping save to avoid data loss")
        return

    updated         = []
    new_assignments = []
    for ticker, shares in stock_positions.items():
        if ticker in existing_holdings:
            h             = existing_holdings[ticker]
            h["shares"]   = shares
            h["last_checked"] = datetime.now().isoformat()
            log.info(f"  ✅ {ticker}: {shares} shares (existing — updated count)")
        else:
            assigned_strike = strike_lookup.get(ticker, 0.0)
            if assigned_strike == 0.0:
                ibkr_avg_cost = next(
                    (p.avgCost for p in ibkr_positions
                     if p.contract.symbol == ticker), 0.0
                )
                assigned_strike = round(ibkr_avg_cost, 2)
                log.warning(f"  ⚠️  {ticker}: strike not in state — "
                            f"using IBKR avgCost ${assigned_strike:.2f} as assigned_strike")
            h = {
                "ticker":             ticker,
                "shares":             shares,
                "assigned_strike":    assigned_strike,
                "assignment_date":    datetime.now().date().isoformat(),
                "current_cc_strike":  None,
                "current_cc_expiry":  None,
                "current_cc_premium": 0.0,
                "weeks_held":         0,
                "cc_status":          "pending",
                "current_price":      None,
                "last_checked":       datetime.now().isoformat(),
            }
            log.info(f"  🆕 NEW ASSIGNMENT: {ticker}  {shares} shares  "
                     f"@ ${assigned_strike:.2f}")
            new_assignments.append(h)
        updated.append(h)

    for ticker in existing_holdings:
        if ticker not in stock_positions:
            log.info(f"  📤 {ticker}: no longer held (called away or sold)")

    state["wheel_holdings"] = updated
    _save_state(state)
    log.info(f"\n💾 Saved {len(updated)} wheel holding(s) to state.json")
    log.info("=" * 65)

    if new_assignments:
        discord_poster.post_assignment_alert(new_assignments)


def run_wheel_check() -> tuple[float, list]:
    """
    Monday 9:55AM PST — five-step evaluation for each held stock:

      Step 1  Screener check — if ticker no longer passes screener filters,
              sell all shares at market and free capital.
      Step 2  Earnings check — if earnings fall within 0–4 days (Mon–Fri
              this week), sell all shares to avoid earnings risk.
      Step 3  Option chain — query IBKR for call strikes >= assigned_strike
              on the nearest Friday; collect delta for each.
      Step 4  Decision — sell the highest-delta (≥ 0.20) call as a covered
              call. If no such strike exists, sell shares at market.
      Step 5  Persist monday_context and wheel_activity to state.json.

    Returns (freed_capital, skip_tickers, reserved_capital) consumed by run_pipeline.
    """
    log.info("\n" + "=" * 65)
    log.info(f"🔄 MONDAY WHEEL CHECK — "
             f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 65)

    state    = _load_state()
    holdings = state.get("wheel_holdings", [])

    freed_capital   = 0.0
    skip_tickers    = []
    cc_premium      = 0.0
    shares_sold_pnl = 0.0
    wheel_activity  = []
    candidate_info  = {}
    expiry          = _next_friday_expiry()

    ib = _connect()

    try:
        # ── Step 0: Sync against live IBKR stock positions ────
        # Catches assignments that detect_assignments() may have missed on Friday.
        ib.reqPositions()
        ib.sleep(2)
        live_pos      = ib.positions(account=ACCOUNT)
        strike_lookup = {p["ticker"]: p["strike"] for p in state.get("positions", [])}
        known_tickers = {h["ticker"] for h in holdings}
        for p in live_pos:
            if p.contract.secType == "STK" and int(p.position) > 0:
                sym = p.contract.symbol
                if sym not in known_tickers:
                    strike = strike_lookup.get(sym, 0.0)
                    if strike == 0.0:
                        strike = round(p.avgCost, 2)
                        log.warning(f"  ⚠️  {sym}: using IBKR avgCost ${strike:.2f} "
                                    f"as assigned_strike fallback")
                    log.warning(f"⚠️  Untracked stock detected: {sym} "
                                f"{int(p.position)} shares @ ${strike:.2f} — "
                                f"adding to wheel_holdings")
                    holdings.append({
                        "ticker":             sym,
                        "shares":             int(p.position),
                        "assigned_strike":    strike,
                        "assignment_date":    datetime.now().date().isoformat(),
                        "current_cc_strike":  None,
                        "current_cc_expiry":  None,
                        "current_cc_premium": 0.0,
                        "weeks_held":         0,
                        "cc_status":          "pending",
                        "current_price":      None,
                        "last_checked":       datetime.now().isoformat(),
                    })
                    known_tickers.add(sym)

        if not holdings:
            log.info("📭 No wheel holdings — nothing to do")
        else:
            # Screener candidates (Steps 1 & 2 prerequisite)
            log.info("\n📡 Fetching screener candidates...")
            candidate_info = get_all_candidates()
            if candidate_info:
                log.info(f"  ✅ {len(candidate_info)} ticker(s) pass screener filters")
            else:
                log.warning("  ⚠️  Screener returned 0 tickers — API may be down")
                log.warning("  Skipping screener/earnings checks; will attempt CCs for all holdings")

        for h in holdings:
            ticker          = h["ticker"]
            shares          = h.get("shares", 0)
            assigned_strike = h.get("assigned_strike", 0.0)
            weeks_held      = h.get("weeks_held", 0) + 1
            h["weeks_held"] = weeks_held
            h["last_checked"] = datetime.now().isoformat()

            log.info(f"\n  ── {ticker}  {shares} shares  "
                     f"@ ${assigned_strike:.2f}  week {weeks_held} ──")

            if shares <= 0:
                log.info(f"  ⏭️  {ticker}: 0 shares — skipping")
                continue

            # ── Step 1: Screener check ────────────────────────
            if candidate_info and ticker not in candidate_info:
                log.warning(f"  🚫 {ticker}: dropped from screener — selling shares")
                result = _sell_stock_market(ib, ticker, shares, "dropped_screener",
                                               assigned_strike=assigned_strike)
                if result["status"] == "filled":
                    proceeds = result["proceeds"]
                    realized = round(proceeds - (assigned_strike * shares), 2)
                    freed_capital   += proceeds
                    shares_sold_pnl += realized
                    skip_tickers.append(ticker)
                    h["shares"]    = 0
                    h["cc_status"] = "sold_dropped_screener"
                    wheel_activity.append({
                        "ticker":       ticker,
                        "action":       "sold_dropped_screener",
                        "shares":       shares,
                        "fill_price":   result["fill_price"],
                        "proceeds":     proceeds,
                        "realized_pnl": realized,
                    })
                    log.info(f"  📊 P&L: ${realized:,.0f}  Freed: ${proceeds:,.0f}")
                else:
                    log.error(f"  ❌ Sale FAILED for {ticker} — MANUAL ACTION REQUIRED")
                continue

            log.info(f"  ✅ {ticker} on screener — checking earnings")

            # ── Step 2: Earnings check ────────────────────────
            ticker_info      = candidate_info.get(ticker, {})
            days_to_earnings = ticker_info.get("days_to_earnings")
            try:
                earnings_this_week = (
                    days_to_earnings is not None
                    and 0 <= int(days_to_earnings) <= 4
                )
            except (TypeError, ValueError):
                earnings_this_week = False

            if earnings_this_week:
                dte_int = int(days_to_earnings)
                log.warning(f"  🚨 {ticker}: earnings in {dte_int} day(s) — "
                             f"selling shares to avoid earnings risk")
                result = _sell_stock_market(ib, ticker, shares, "earnings_this_week",
                                               assigned_strike=assigned_strike)
                if result["status"] == "filled":
                    proceeds = result["proceeds"]
                    realized = round(proceeds - (assigned_strike * shares), 2)
                    freed_capital   += proceeds
                    shares_sold_pnl += realized
                    skip_tickers.append(ticker)
                    h["shares"]    = 0
                    h["cc_status"] = "sold_earnings_this_week"
                    wheel_activity.append({
                        "ticker":           ticker,
                        "action":           "sold_earnings_this_week",
                        "days_to_earnings": dte_int,
                        "shares":           shares,
                        "fill_price":       result["fill_price"],
                        "proceeds":         proceeds,
                        "realized_pnl":     realized,
                    })
                    log.info(f"  📊 P&L: ${realized:,.0f}  Freed: ${proceeds:,.0f}")
                else:
                    log.error(f"  ❌ Sale FAILED for {ticker} — MANUAL ACTION REQUIRED")
                continue

            log.info(f"  ✅ {ticker}: no earnings this week — querying option chain")

            # ── Step 3: Find best CC strike ───────────────────
            cc_info = _find_cc_strike(ib, ticker, expiry, assigned_strike)

            # ── Step 4: Decision ──────────────────────────────
            if cc_info is None:
                log.warning(f"  ❌ {ticker}: no call strike with delta ≥ "
                             f"{CC_DELTA_MIN:.2f} — selling shares")
                result = _sell_stock_market(ib, ticker, shares, "no_viable_cc",
                                               assigned_strike=assigned_strike)
                if result["status"] == "filled":
                    proceeds = result["proceeds"]
                    realized = round(proceeds - (assigned_strike * shares), 2)
                    freed_capital   += proceeds
                    shares_sold_pnl += realized
                    skip_tickers.append(ticker)
                    h["shares"]    = 0
                    h["cc_status"] = "sold_no_viable_cc"
                    wheel_activity.append({
                        "ticker":       ticker,
                        "action":       "sold_no_viable_cc",
                        "shares":       shares,
                        "fill_price":   result["fill_price"],
                        "proceeds":     proceeds,
                        "realized_pnl": realized,
                    })
                    log.info(f"  📊 P&L: ${realized:,.0f}  Freed: ${proceeds:,.0f}")
                else:
                    log.error(f"  ❌ Sale FAILED for {ticker} — MANUAL ACTION REQUIRED")
                continue

            cc_strike, cc_delta, cc_mid, cc_stock_price = cc_info
            mid_display = f"${cc_mid:.2f}" if cc_mid else "?"
            log.info(f"  🎯 Selling CC: ${cc_strike:.2f} strike  "
                     f"delta={cc_delta:.3f}  mid={mid_display}")

            cc_opt = Option(ticker, expiry, cc_strike, "C", "SMART", currency="USD")
            try:
                qualified = ib.qualifyContracts(cc_opt)
            except Exception as e:
                log.error(f"  ❌ Cannot qualify CC for {ticker}: {e}")
                h["cc_status"] = "failed"
                continue

            if not qualified:
                log.warning(f"  ⚠️  {ticker}: CC contract did not qualify")
                h["cc_status"] = "failed"
                continue

            ref_mid      = cc_mid if (cc_mid and cc_mid > 0) else 0.50
            order_result = _sell_cc_with_escalation(
                ib, qualified[0], shares, ticker, cc_strike, ref_mid
            )

            if order_result["status"] in ("filled", "partial_fill"):
                prem = order_result["premium_collected"]
                cc_premium             += prem
                h["current_cc_strike"]  = cc_strike
                h["current_cc_expiry"]  = expiry
                h["current_cc_premium"] = prem
                h["cc_status"]          = "open"
                wheel_activity.append({
                    "ticker":     ticker,
                    "action":     "cc_opened",
                    "cc_strike":  cc_strike,
                    "cc_delta":   round(cc_delta, 3),
                    "cc_premium": prem,
                    "cc_expiry":  expiry,
                })
                log.info(f"  💰 CC premium: ${prem:,.0f}")
                # Capture execution metadata for dashboard enrichment
                fill_price = order_result.get("fill_price")
                buffer_pct = (
                    round(((cc_stock_price - cc_strike) / cc_stock_price) * 100, 2)
                    if cc_stock_price and cc_stock_price > 0 else None
                )
                try:
                    _append_trade_log({
                        "symbol":               ticker,
                        "expiry":               expiry,
                        "strike":               float(cc_strike),
                        "right":                "C",
                        "entry_date":           datetime.now().isoformat(),
                        "delta_at_entry":       round(cc_delta, 4),
                        "buffer_pct_at_entry":  buffer_pct,
                        "premium_per_contract": fill_price,
                        "contracts":            shares // 100,
                        "total_premium":        prem,
                    })
                    log.info(f"  📝 trade_log.json: {ticker} CC recorded")
                except Exception as tl_err:
                    log.warning(f"  ⚠️  trade_log.json write failed: {tl_err}")
            else:
                h["cc_status"] = "failed"
                wheel_activity.append({
                    "ticker": ticker, "action": "cc_failed", "cc_strike": cc_strike
                })
                log.warning(f"  ⚠️  {ticker}: CC order failed — no CC this week")

    finally:
        ib.disconnect()

    # ── Step 5: Persist to state.json ────────────────────────
    reserved_capital   = round(sum(
        h.get("shares", 0) * h.get("assigned_strike", 0.0)
        for h in holdings if h.get("shares", 0) > 0
    ), 2)
    active_wheel_count = sum(1 for h in holdings if h.get("shares", 0) > 0)

    state["wheel_holdings"] = holdings
    state["monday_context"] = {
        "skip_tickers":       skip_tickers,
        "freed_capital":      freed_capital,
        "cc_premium":         cc_premium,
        "shares_sold_pnl":    shares_sold_pnl,
        "wheel_activity":     wheel_activity,
        "reserved_capital":   reserved_capital,
        "active_wheel_count": active_wheel_count,
        "updated":            datetime.now().isoformat()
    }
    _save_state(state)

    exits   = [a for a in wheel_activity if "sold" in a["action"]]
    ccs     = [a for a in wheel_activity if a["action"] == "cc_opened"]
    cc_summ = "  ".join(
        f"{a['ticker']} ${a['cc_strike']:.0f} δ{a['cc_delta']:.2f}" for a in ccs
    )

    earnings_exits = [a for a in wheel_activity if a["action"] == "sold_earnings_this_week"]

    log.info("\n" + "=" * 65)
    log.info("📊 WHEEL CHECK SUMMARY")
    log.info(f"   Screener dropped: "
             f"{[a['ticker'] for a in exits if a['action'] == 'sold_dropped_screener'] or 'none'}")
    log.info(f"   Earnings exits:   "
             f"{[a['ticker'] for a in earnings_exits] or 'none'}")
    log.info(f"   No-delta exits:   "
             f"{[a['ticker'] for a in exits if a['action'] == 'sold_no_viable_cc'] or 'none'}")
    log.info(f"   CCs opened:       {len(ccs)}  {cc_summ or 'none'}")
    log.info(f"   Freed capital:    ${freed_capital:,.0f}")
    log.info(f"   Shares sold P&L:  ${shares_sold_pnl:,.0f}")
    log.info(f"   CC premium:       ${cc_premium:,.0f}")
    log.info(f"   Reserved capital: ${reserved_capital:,.0f}")
    log.info(f"   Active holdings:  {active_wheel_count}")
    log.info("=" * 65)

    return freed_capital, skip_tickers, reserved_capital


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "detect":
        detect_assignments()
    else:
        freed, skip = run_wheel_check()
        print(f"\nFreed: ${freed:,.0f}  Skip: {skip}")
