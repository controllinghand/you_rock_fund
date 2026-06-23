import json
import logging
import math
import time
from datetime import datetime, timezone
from ib_insync import IB, Option, Stock, LimitOrder, MarketOrder

from config import IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID, ACCOUNT, NUM_POSITIONS, TOTAL_FUND_BUDGET, MAX_PER_POSITION, DRY_RUN, get_settings, ACCOUNT_TYPE, gateway_unreachable_message, probe_port

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.FileHandler("trade_log.txt"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

TRADE_LOG_JSON      = "trade_log.json"
MAX_SPREAD_PCT      = 0.20  # fallback default — settings.json overrides via check_liquidity
MIN_BID_YIELD_PCT   = 0.01  # fallback default — bid yield threshold to proceed despite wide spread
MAX_SPREAD_HARD_CAP = 0.50  # fallback default — spread above this is always skipped
MIN_OI_NOTIONAL     = 1_000_000  # fallback default — min open-interest notional (OI × strike × 100); settings.json overrides
MIN_OI_FLOOR        = 10         # fallback default — absolute min contracts; rejects totally-dead strikes that math past the notional floor
MAX_DELTA           = 0.21  # hard ceiling — never sell a CSP with abs(delta) above this
MIN_DELTA           = 0.15  # floor — if live delta drops below this, scan upward for a better strike
MID_WAIT_SECS       = 120
BID_WAIT_SECS       = 120
MARKET_WAIT_SECS    = 60    # total polling window for market orders
MARKET_POLL_SECS    = 5     # check every N seconds
RECONNECT_WAIT_SECS = 30
MAX_RECONNECTS      = 3


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


def connect() -> IB:
    log.info(f"🔌 Connecting to IB Gateway {IBKR_HOST}:{IBKR_PORT} ({ACCOUNT_TYPE}, clientId={IBKR_CLIENT_ID})")
    for attempt in range(1, 4):
        try:
            ib = IB()
            ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID)
            ib.reqMarketDataType(3)  # 1=live, 2=frozen, 3=delayed, 4=delayed-frozen
            log.info(f"✅ Connected to IBKR — Account: {ib.managedAccounts()}")
            _wait_for_usopt(ib)
            return ib
        except TimeoutError:
            port_open = probe_port(IBKR_HOST, IBKR_PORT)
            log.warning(
                f"⚠️  IBKR connect attempt {attempt}/3 timed out ({ACCOUNT_TYPE}, {IBKR_HOST}:{IBKR_PORT}) — "
                f"TCP port {'OPEN (API handshake hung)' if port_open else 'CLOSED (gateway not listening)'}"
            )
            if attempt < 3:
                time.sleep(10)
    raise TimeoutError(gateway_unreachable_message(IBKR_HOST, IBKR_PORT))


def _wait_for_usopt(ib: IB, timeout: int = 30) -> None:
    """Block until the usopt options data farm reports OK (code 2104), or timeout."""
    ready = False

    def on_error(reqId, errorCode, errorString, contract):
        nonlocal ready
        if errorCode == 2104 and "usopt" in errorString:
            ready = True

    ib.errorEvent += on_error
    deadline = time.time() + timeout
    while not ready and time.time() < deadline:
        ib.sleep(0.5)
    ib.errorEvent -= on_error
    if ready:
        log.info("✅ usopt options data farm ready")
    else:
        log.warning(f"⚠️  usopt not confirmed ready after {timeout}s — proceeding anyway")


def _reconnect(ib: IB) -> IB:
    """Disconnect, wait RECONNECT_WAIT_SECS, and return a fresh IB connection."""
    log.warning(f"⚠️  IBKR disconnected — waiting {RECONNECT_WAIT_SECS}s before reconnecting...")
    try:
        ib.disconnect()
    except Exception:
        pass
    time.sleep(RECONNECT_WAIT_SECS)
    new_ib = connect()
    log.info("✅ Reconnected to IBKR — resuming execution")
    return new_ib


def parse_expiry(expiry_str: str) -> str:
    dt = datetime.strptime(expiry_str, "%a, %d %b %Y %H:%M:%S %Z")
    return dt.strftime("%Y%m%d")


def get_option_contract(ib: IB, ticker: str, strike: float, expiry_str: str):
    expiry   = parse_expiry(expiry_str)
    contract = Option(ticker, expiry, strike, "P", "SMART", currency="USD")
    try:
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            log.warning(f"⚠️  Could not qualify: {ticker} {strike}P {expiry}")
            return None
        log.info(f"✅ Qualified: {ticker} {strike}P {expiry}")
        return qualified[0]
    except Exception as e:
        log.error(f"Error qualifying {ticker}: {e}")
        return None


def is_nan(val) -> bool:
    try:
        return val != val  # nan != nan is True
    except:
        return True


def _get_greeks_for_contract(ib: IB, contract) -> tuple[float | None, float | None]:
    """Request delayed market data and return (put delta, implied vol) from the
    same greeks snapshot. Either value may be None if IBKR has no data."""
    tkr = ib.reqMktData(contract, genericTickList="106", snapshot=False)
    ib.sleep(3)
    ib.cancelMktData(contract)
    ib.sleep(0.5)
    for greeks in (tkr.modelGreeks, tkr.lastGreeks, tkr.bidGreeks, tkr.askGreeks):
        if greeks is not None and not is_nan(greeks.delta):
            iv = getattr(greeks, "impliedVol", None)
            iv = iv if (iv is not None and not is_nan(iv)) else None
            return greeks.delta, iv
    return None, None


def _get_stock_price(ib: IB, ticker: str) -> float | None:
    """Return the current underlying stock price using delayed data. Used to snapshot price at fill."""
    try:
        stk = Stock(ticker, "SMART", "USD")
        stk_q = ib.qualifyContracts(stk)
        if not stk_q:
            return None
        tkr = ib.reqMktData(stk_q[0], snapshot=False)
        ib.sleep(3)
        ib.cancelMktData(stk_q[0])
        ib.sleep(0.5)
        for price in (tkr.last, tkr.close, tkr.bid, tkr.ask):
            if price is not None and not is_nan(price) and price > 0:
                return float(price)
    except Exception as e:
        log.warning(f"  ⚠️  Could not fetch stock price for {ticker}: {e}")
    return None


def verify_and_adjust_strike(
        ib: IB, ticker: str, screener_strike: float,
        expiry_str: str, screener_delta: float,
) -> tuple | None:
    """
    Check live delta for screener_strike at execution time and adjust if needed.

    - If abs(delta) > MAX_DELTA (stock fell since Saturday): scan downward for nearest
      strike with delta ≤ MAX_DELTA.
    - If abs(delta) < MIN_DELTA (stock rose since Saturday): scan upward for highest
      strike still within delta ≤ MAX_DELTA (maximises premium within the safe zone).

    Returns (qualified_contract, final_strike, orig_delta, final_delta, final_iv,
    was_adjusted) or None if qualification fails or no valid strike is found.
    final_iv is the chosen contract's implied vol at execution (may be None).
    """
    expiry = parse_expiry(expiry_str)

    # Qualify and delta-check the screener strike
    c = Option(ticker, expiry, screener_strike, "P", "SMART", currency="USD")
    try:
        qualified = ib.qualifyContracts(c)
        if not qualified:
            log.warning(f"  ⚠️  {ticker} — qualify failed during delta check")
            return None
        c = qualified[0]
    except Exception as e:
        log.error(f"  ❌ {ticker} delta-check qualify error: {e}")
        return None

    live_delta, live_iv = _get_greeks_for_contract(ib, c)

    if live_delta is None:
        log.warning(f"  ⚠️  {ticker} — no live delta from IBKR; "
                    f"trusting screener delta {screener_delta:.3f}")
        live_delta = screener_delta

    orig_delta = live_delta

    if MIN_DELTA <= abs(live_delta) <= MAX_DELTA:
        log.info(f"  ✅ {ticker} delta OK: {live_delta:.3f} at ${screener_strike:.2f}")
        return c, screener_strike, orig_delta, live_delta, live_iv, False

    # Need to scan the chain — fetch once and reuse for both directions
    try:
        stk = Stock(ticker, "SMART", "USD")
        stk_q = ib.qualifyContracts(stk)
        if not stk_q:
            log.error(f"  ❌ {ticker} — can't qualify stock for chain lookup")
            return None
        und_con_id = stk_q[0].conId
    except Exception as e:
        log.error(f"  ❌ {ticker} stock qualify error for chain lookup: {e}")
        return None

    chains = ib.reqSecDefOptParams(ticker, "", "STK", und_con_id)
    ib.sleep(1)

    def _scan_strikes(candidates: list, label: str) -> tuple | None:
        for alt_strike in candidates:
            alt_c = Option(ticker, expiry, alt_strike, "P", "SMART", currency="USD")
            try:
                alt_q = ib.qualifyContracts(alt_c)
                if not alt_q:
                    continue
                alt_c = alt_q[0]
            except Exception:
                continue
            alt_delta, alt_iv = _get_greeks_for_contract(ib, alt_c)
            if alt_delta is None:
                continue
            if abs(alt_delta) <= MAX_DELTA:
                log.warning(f"  {label} {ticker} strike adjusted ${screener_strike:.2f} → "
                            f"${alt_strike:.2f} (delta {orig_delta:.3f} → {alt_delta:.3f})")
                return alt_c, alt_strike, orig_delta, alt_delta, alt_iv, True
        return None

    if abs(live_delta) > MAX_DELTA:
        # Stock fell — scan downward for first strike with delta ≤ MAX_DELTA
        log.warning(f"  ⚠️  {ticker} ${screener_strike:.2f} delta {live_delta:.3f} > {MAX_DELTA} "
                    f"— scanning chain downward")
        below = []
        for ch in chains:
            if expiry in (ch.expirations or []):
                below.extend(s for s in ch.strikes if s < screener_strike)
        if not below:
            log.error(f"  ❌ {ticker} — no lower strikes in chain for {expiry}")
            return None
        result = _scan_strikes(sorted(set(below), reverse=True)[:10], "⬇️ ")
        if result is None:
            log.error(f"  ❌ {ticker} — no valid strike found with delta ≤ {MAX_DELTA} — skipping")
        return result

    # abs(live_delta) < MIN_DELTA — stock rose, delta too low
    # Scan upward: take 15 closest strikes above screener, scan from highest to lowest
    # to find the highest strike still within the delta cap (maximises premium)
    log.warning(f"  ⚠️  {ticker} ${screener_strike:.2f} delta {live_delta:.3f} < {MIN_DELTA} "
                f"— scanning chain upward for better delta")
    above = []
    for ch in chains:
        if expiry in (ch.expirations or []):
            above.extend(s for s in ch.strikes if s > screener_strike)
    if not above:
        log.warning(f"  ⚠️  {ticker} — no higher strikes in chain; using screener strike as-is")
        return c, screener_strike, orig_delta, live_delta, live_iv, False
    # 15 closest above screener, then scan highest-first to find best delta within cap
    closest_above = sorted(sorted(set(above))[:15], reverse=True)
    result = _scan_strikes(closest_above, "⬆️ ")
    if result is None:
        log.warning(f"  ⚠️  {ticker} — no higher strike improves delta; using screener strike as-is")
        return c, screener_strike, orig_delta, live_delta, live_iv, False
    return result


def get_market_data(ib: IB, contract, screener_premium: float) -> dict | None:
    """
    Request delayed market data (type 3 — no subscription needed).
    Falls back to screener premium if market is closed.
    """
    ticker = ib.reqMktData(contract, genericTickList="101", snapshot=False)
    ib.sleep(10)

    bid    = ticker.bid
    ask    = ticker.ask
    oi     = ticker.putOpenInterest or ticker.callOpenInterest or 0
    strike = contract.strike

    ib.cancelMktData(contract)
    ib.sleep(0.5)

    # Market is closed or no delayed data available
    if is_nan(bid) or is_nan(ask) or bid <= 0 or ask <= 0:
        log.warning(f"  ⏰ No market data for {contract.symbol} — market likely closed")
        if DRY_RUN:
            simulated_bid       = round(screener_premium * 0.90, 2)
            simulated_ask       = round(screener_premium * 1.10, 2)
            simulated_mid       = screener_premium
            simulated_bid_yield = simulated_bid / strike if strike > 0 else 0
            simulated_mid_yield = simulated_mid / strike if strike > 0 else 0
            log.info(f"  🧪 Simulating: Bid ${simulated_bid}  Ask ${simulated_ask}  "
                     f"Mid ${simulated_mid}  (from screener)")
            return {
                "bid": simulated_bid,
                "ask": simulated_ask,
                "mid": simulated_mid,
                "spread_pct": 0.20,
                "bid_yield": simulated_bid_yield,
                "mid_yield": simulated_mid_yield,
                "open_interest": 999,
                "strike": strike,
                "simulated": True
            }
        return None

    mid        = round((bid + ask) / 2, 2)
    spread     = ask - bid
    spread_pct = spread / mid if mid > 0 else 999
    bid_yield  = bid / strike if strike > 0 else 0
    mid_yield  = mid / strike if strike > 0 else 0

    log.info(f"  {contract.symbol} — Bid: ${bid:.2f}  Ask: ${ask:.2f}  "
             f"Mid: ${mid:.2f}  Spread: {spread_pct*100:.1f}%  "
             f"Bid yield: {bid_yield*100:.2f}%  Mid yield: {mid_yield*100:.2f}%  OI: {oi}")

    return {
        "bid": bid, "ask": ask, "mid": mid,
        "spread_pct": spread_pct,
        "bid_yield": bid_yield,
        "mid_yield": mid_yield,
        "open_interest": oi,
        "strike": strike,
        "simulated": False
    }


def check_liquidity(mkt: dict, ticker: str) -> dict | None:
    """Returns None if liquidity is OK, else a skip-info dict with reason details.

    Wide-spread handling (spread > max_spread_pct):
      - bid_yield ≥ min_bid_yield_pct → proceed using bid as the limit price
      - else if spread > max_spread_hard_cap → skip as spread_illiquid
      - else if mid_yield ≥ min_bid_yield_pct → try_limit_only (FOK mid → FOK bid,
        no market fallback; see place_order_with_escalation)
      - else → skip as spread_low_yield

    Thresholds hot-reload from settings.json on every call; the module-level
    constants are fallbacks if a setting is missing.
    """
    if mkt.get("simulated"):
        return None

    s              = get_settings()
    max_spread     = s.get("max_spread_pct",       MAX_SPREAD_PCT)
    min_bid_yield  = s.get("min_bid_yield_pct",    MIN_BID_YIELD_PCT)
    hard_cap       = s.get("max_spread_hard_cap",  MAX_SPREAD_HARD_CAP)

    spread_pct = mkt["spread_pct"]
    bid_yield  = mkt.get("bid_yield", 0)
    mid_yield  = mkt.get("mid_yield", 0)

    if spread_pct > max_spread:
        if bid_yield >= min_bid_yield:
            log.info(f"⚠️  {ticker} spread wide ({spread_pct*100:.1f}%) "
                     f"but bid yield {bid_yield*100:.2f}% ≥ {min_bid_yield*100:.2f}% — proceeding")
            # Use bid as limit price downstream — mid likely won't fill on wide spreads
            mkt["use_bid_as_limit"] = True
        elif spread_pct > hard_cap:
            log.warning(f"⚠️  {ticker} spread too wide: {spread_pct*100:.1f}% "
                        f"AND bid yield {bid_yield*100:.2f}% < {min_bid_yield*100:.2f}% — skipping")
            return {"reason": "spread_illiquid",
                    "spread_pct": spread_pct, "bid_yield": bid_yield, "mid_yield": mid_yield,
                    "max_spread_pct": max_spread, "min_bid_yield_pct": min_bid_yield,
                    "max_spread_hard_cap": hard_cap}
        elif mid_yield >= min_bid_yield:
            log.info(f"⚠️  {ticker} bid yield {bid_yield*100:.2f}% < {min_bid_yield*100:.2f}% "
                     f"but mid yield {mid_yield*100:.2f}% qualifies — trying limit only")
            # FOK mid → FOK bid; no market fallback (see place_order_with_escalation)
            mkt["try_limit_only"]       = True
            mkt["max_spread_pct"]       = max_spread
            mkt["min_bid_yield_pct"]    = min_bid_yield
            mkt["max_spread_hard_cap"]  = hard_cap
        else:
            log.warning(f"⚠️  {ticker} spread too wide: {spread_pct*100:.1f}% "
                        f"and mid yield {mid_yield*100:.2f}% < {min_bid_yield*100:.2f}% — skipping")
            return {"reason": "spread_low_yield",
                    "spread_pct": spread_pct, "bid_yield": bid_yield, "mid_yield": mid_yield,
                    "max_spread_pct": max_spread, "min_bid_yield_pct": min_bid_yield,
                    "max_spread_hard_cap": hard_cap}

    # Open-interest gate: use notional (OI × strike × 100), not a flat contract
    # count. A flat count penalises high-strike underlyings — the same dollar
    # liquidity shows fewer contracts on a $300 name than a $30 one. The notional
    # floor is price-neutral; a tiny absolute floor still kills dead strikes.
    oi              = mkt["open_interest"]
    strike          = mkt.get("strike", 0)
    oi_notional     = oi * strike * 100
    min_oi_notional = s.get("min_oi_notional", MIN_OI_NOTIONAL)
    min_oi_floor    = s.get("min_oi_floor",    MIN_OI_FLOOR)
    if oi < min_oi_floor or oi_notional < min_oi_notional:
        log.warning(f"⚠️  {ticker} open interest too thin: OI {oi:.0f} "
                    f"(${oi_notional:,.0f} notional) < ${min_oi_notional:,.0f} floor — skipping")
        return {"reason": "oi",
                "open_interest": oi, "oi_notional": oi_notional,
                "min_oi_notional": min_oi_notional, "min_oi_floor": min_oi_floor}
    return None


def place_order_with_escalation(ib: IB, contract, contracts: int,
                                 mkt: dict, ticker: str) -> dict:
    result = {
        "ticker": ticker, "contracts": contracts,
        "status": "unfilled", "fill_price": None,
        "order_type": None, "premium_collected": 0,
        "simulated": mkt.get("simulated", False),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    if DRY_RUN:
        tag = " (simulated data)" if mkt.get("simulated") else " (live data)"
        log.info(f"  🧪 DRY RUN{tag} — would sell {contracts}x {ticker} "
                 f"put @ mid ${mkt['mid']:.2f}")
        result.update({
            "status": "dry_run",
            "fill_price": mkt["mid"],
            "order_type": "limit_mid",
            "premium_collected": round(contracts * mkt["mid"] * 100, 2),
            "exec_timestamp": datetime.now(timezone.utc).isoformat()
        })
        return result

    def _is_permission_error(trade) -> bool:
        """Return True if IBKR rejected with Error 201 due to missing options permissions.
        Error 201 also fires for insufficient funds — check message to distinguish."""
        if trade.orderStatus.status != "Inactive":
            return False
        for e in trade.log:
            if getattr(e, "errorCode", 0) == 201:
                msg = (getattr(e, "message", "") or "").lower()
                # Insufficient funds messages mention margin/funds/equity — not a permissions issue
                if any(w in msg for w in ("available funds", "margin", "equity", "insufficient")):
                    log.error(f"  ❌ {ticker} — IBKR rejected: insufficient funds (Error 201)")
                    result["status"] = "failed_funds"
                    return False
                return True  # genuine permissions rejection
        return False

    def try_limit(price: float, label: str, wait: int) -> bool:
        log.info(f"  📤 {label}: SELL {contracts}x {ticker} PUT @ ${price:.2f}")
        order = LimitOrder("SELL", contracts, price, account=ACCOUNT, tif="DAY")
        trade = ib.placeOrder(contract, order)
        # Quick early-exit: IBKR permission rejections (Error 201) appear within seconds
        ib.sleep(3)
        if _is_permission_error(trade):
            log.error(f"  ❌ {ticker} — IBKR rejected: no options trading permissions (Error 201)")
            result["status"] = "failed_permissions"
            return False
        ib.sleep(wait - 3)
        if trade.orderStatus.status == "Filled":
            fill = trade.orderStatus.avgFillPrice
            log.info(f"  ✅ Filled {ticker} @ ${fill:.2f}")
            result.update({
                "status": "filled", "fill_price": fill,
                "order_type": label,
                "premium_collected": round(contracts * fill * 100, 2),
                "exec_timestamp": datetime.now(timezone.utc).isoformat()
            })
            return True
        log.info(f"  ⏳ {label} unfilled — escalating...")
        ib.cancelOrder(trade.order)
        ib.sleep(1)
        return False

    def try_limit_fok(price: float, label: str) -> bool:
        """FOK limit attempt — fills the full quantity at the limit price or cancels.
        Used for the try_limit_only path so partial fills are avoided."""
        log.info(f"  📤 {label} (FOK): SELL {contracts}x {ticker} PUT @ ${price:.2f}")
        order = LimitOrder("SELL", contracts, price, account=ACCOUNT, tif="FOK")
        trade = ib.placeOrder(contract, order)
        # FOK resolves immediately at IBKR — poll briefly for the final state
        for _ in range(15):
            ib.sleep(1)
            if trade.orderStatus.status in ("Filled", "Cancelled", "ApiCancelled", "Inactive"):
                break
        final_status = trade.orderStatus.status
        if final_status == "Filled":
            fill = trade.orderStatus.avgFillPrice
            filled_qty = trade.orderStatus.filled
            log.info(f"  ✅ {label} (FOK) filled {ticker} @ ${fill:.2f} — limit-only path succeeded")
            result.update({
                "status": "filled", "fill_price": fill,
                "order_type": f"{label}_fok",
                "premium_collected": round(filled_qty * fill * 100, 2),
                "exec_timestamp": datetime.now(timezone.utc).isoformat()
            })
            return True
        log.info(f"  ⏳ {label} (FOK) did not fill (status: {final_status})")
        return False

    if mkt.get("try_limit_only"):
        # Limit-only path: FOK at mid, then FOK at bid. No market fallback.
        if try_limit_fok(mkt["mid"], "limit_mid"): return result
        if try_limit_fok(mkt["bid"], "limit_bid"): return result
        log.warning(f"  ⚠️  {ticker} — limit-only path failed (mid yield qualified but no fill) — skipping")
        result.update({
            "status":              "skipped_liquidity",
            "reason":              "spread_low_yield_unfilled",
            "spread_pct":          mkt.get("spread_pct"),
            "bid_yield":           mkt.get("bid_yield"),
            "mid_yield":           mkt.get("mid_yield"),
            "max_spread_pct":      mkt.get("max_spread_pct"),
            "min_bid_yield_pct":   mkt.get("min_bid_yield_pct"),
            "max_spread_hard_cap": mkt.get("max_spread_hard_cap"),
        })
        return result

    if not mkt.get("use_bid_as_limit"):
        if try_limit(mkt["mid"], "limit_mid", MID_WAIT_SECS): return result
        if result.get("status") in ("failed_permissions", "failed_funds"): return result
    if try_limit(mkt["bid"], "limit_bid", BID_WAIT_SECS): return result
    if result.get("status") in ("failed_permissions", "failed_funds"): return result

    # Market order with polling loop — options can partially fill across multiple exchanges
    log.info(f"  📤 Market order: SELL {contracts}x {ticker} PUT")
    order = MarketOrder("SELL", contracts, account=ACCOUNT, tif="DAY")
    trade = ib.placeOrder(contract, order)

    elapsed = 0
    while elapsed < MARKET_WAIT_SECS:
        ib.sleep(MARKET_POLL_SECS)
        elapsed += MARKET_POLL_SECS
        status      = trade.orderStatus.status
        filled_qty  = trade.orderStatus.filled
        remaining   = trade.orderStatus.remaining

        if status == "Filled" or (remaining == 0 and filled_qty > 0):
            fill = trade.orderStatus.avgFillPrice
            log.info(f"  ✅ Market order filled {ticker} @ ${fill:.2f} "
                     f"({filled_qty} contracts in {elapsed}s)")
            result.update({
                "status": "filled",
                "fill_price": fill,
                "order_type": "market",
                "premium_collected": round(filled_qty * fill * 100, 2),
                "exec_timestamp": datetime.now(timezone.utc).isoformat()
            })
            return result

        if _is_permission_error(trade):
            log.error(f"  ❌ {ticker} — IBKR rejected: no options trading permissions (Error 201)")
            result["status"] = "failed_permissions"
            return result
        if result.get("status") == "failed_funds":
            return result

        if status == "PartiallyFilled" and filled_qty > 0:
            log.info(f"  ⏳ Partial: {filled_qty}/{contracts} filled after {elapsed}s — waiting...")
        else:
            log.info(f"  ⏳ Market status: {status} after {elapsed}s — waiting...")

    # Accept whatever partial fill arrived before timeout
    final_qty = trade.orderStatus.filled
    if final_qty > 0:
        fill = trade.orderStatus.avgFillPrice
        log.warning(f"  ⚠️  Partial fill accepted: {final_qty}/{contracts} @ ${fill:.2f}")
        result.update({
            "status": "partial_fill",
            "fill_price": fill,
            "order_type": "market",
            "premium_collected": round(final_qty * fill * 100, 2),
            "exec_timestamp": datetime.now(timezone.utc).isoformat()
        })
    else:
        log.error(f"  ❌ Could not fill {ticker} — manual review needed")
        result["status"] = "failed"

    return result


def execute_positions(sized_positions: list, extra_targets: list = None,
                      target_fills: int = None, status_callback=None) -> list:
    """
    Execute up to target_fills fills (defaults to NUM_POSITIONS). If a candidate
    fails qualification, market data, or liquidity, the next-ranked screener target
    is sized and attempted automatically until the fill target is met or candidates
    are exhausted.

    extra_targets: full ranked screener list (raw dicts from screener).
    target_fills: how many CSP fills to seek (caller reduces by active wheel count).
    """
    from position_sizer import size_position

    _target = target_fills if target_fills is not None else NUM_POSITIONS

    log.info("\n" + "=" * 65)
    log.info(f"🚀 YOU ROCK VOLATILITY INCOME FUND — Execution Start")
    log.info(f"   Mode: {'🧪 DRY RUN' if DRY_RUN else '🔴 LIVE'}")
    log.info(f"   Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"   Primary candidates: {len(sized_positions)}  |  "
             f"Fallback pool: {len(extra_targets or [])}  |  "
             f"Target fills: {_target}")
    log.info("=" * 65)

    ib               = connect()

    # At market open, delayed options data needs time to populate after usopt connects
    exec_hour, exec_min = map(int, get_settings().get('execution_time', '10:00').split(':'))
    if exec_hour < 7:  # 6:30 AM PST or earlier = running at/near open
        log.info("⏳ Near market open — waiting 60s for delayed options data to populate...")
        ib.sleep(60)

    def _status(ticker=None, stage=None, result=None):
        if status_callback:
            try:
                status_callback(ticker=ticker, stage=stage, result=result)
            except Exception:
                pass

    results          = []
    filled_count     = 0
    capital_deployed = 0
    attempted        = set()
    ticker_results   = []  # running list of per-ticker outcomes for status
    # Track all sized candidates attempted (primaries + fallbacks) for state.json
    all_sized        = list(sized_positions)

    # Work through pre-sized primaries first, then size extras on demand
    primary    = list(sized_positions)
    extras     = list(extra_targets or [])
    extra_ptr  = 0

    def next_candidate():
        nonlocal extra_ptr
        while primary:
            p = primary.pop(0)
            if p["ticker"] not in attempted:
                return p
        while extra_ptr < len(extras):
            raw = extras[extra_ptr]
            extra_ptr += 1
            if raw["ticker"] in attempted:
                continue
            if raw["put_20d_strike"] * 100 > MAX_PER_POSITION:
                log.info(f"  ⛔ {raw['ticker']} skipped — contract size ${raw['put_20d_strike'] * 100:,.0f} exceeds ${MAX_PER_POSITION:,.0f} max")
                results.append({"ticker": raw["ticker"], "status": "skipped_contract_size"})
                continue
            is_last   = (filled_count == _target - 1)
            remaining = TOTAL_FUND_BUDGET - capital_deployed
            p = size_position(raw, remaining, is_last=is_last)
            if p:
                log.info(f"  🔄 Fallback candidate: {p['ticker']} "
                         f"({p['contracts']}x @ ${p['strike']:.2f})")
                all_sized.append(p)
                return p
        return None

    slot       = 0
    reconnects = 0

    while filled_count < _target:
        pos = next_candidate()
        if pos is None:
            log.warning(f"⚠️  No more candidates — {filled_count}/{_target} positions filled")
            break

        slot      += 1
        ticker     = pos["ticker"]
        attempted.add(ticker)
        strike     = pos["strike"]
        expiry     = pos["expiry"]
        contracts  = pos["contracts"]
        premium    = pos["premium"]

        log.info(f"\n[attempt {slot}  fill {filled_count + 1}/{_target}] "
                 f"{ticker} — {contracts} contracts @ ${strike:.2f} (screener strike)")
        _status(ticker=ticker, stage="qualifying")

        try:
            # Verify delta at execution time — auto-adjust if stock moved since Saturday
            delta_result = verify_and_adjust_strike(
                ib, ticker, strike, expiry, screener_delta=pos.get("delta", 0.0)
            )
            if delta_result is None:
                log.info(f"  🔄 {ticker} — no valid strike with delta ≤ {MAX_DELTA}, trying next")
                results.append({"ticker": ticker, "status": "skipped_delta"})
                continue

            contract, strike, orig_delta, final_delta, final_iv, was_adjusted = delta_result
            # Fall back to the screener's ATM IV if live greeks didn't carry one.
            if final_iv is None:
                final_iv = pos.get("iv_atm")
            if was_adjusted:
                old_capital  = pos["capital_used"]
                contracts    = pos["contracts"]
                new_capital  = round(contracts * strike * 100, 2)
                pos          = {**pos, "strike": strike, "capital_used": new_capital}
                # Persist the executed strike/capital back into the candidate list
                # so state.json (and the dashboard) reflect what we actually filled,
                # not the original screener strike.
                for _i, _sp in enumerate(all_sized):
                    if _sp.get("ticker") == ticker:
                        all_sized[_i] = pos
                        break
                log.info(f"  ⚡ Capital adjusted: ${old_capital:,.0f} → ${new_capital:,.0f}")

            _status(ticker=ticker, stage="fetching market data")
            mkt = get_market_data(ib, contract, screener_premium=premium)
            if not mkt:
                log.info(f"  🔄 {ticker} — no market data, trying next candidate")
                results.append({"ticker": ticker, "status": "failed_market_data"})
                _status(ticker=ticker, stage=None, result={"ticker": ticker, "status": "failed_market_data"})
                continue

            skip_info = check_liquidity(mkt, ticker)
            if skip_info:
                log.info(f"  🔄 {ticker} — failed liquidity, trying next candidate")
                results.append({"ticker": ticker, "status": "skipped_liquidity", **skip_info})
                _status(ticker=ticker, stage=None, result={"ticker": ticker, "status": "skipped_liquidity"})
                continue

            _status(ticker=ticker, stage="placing order — limit mid")
            result = place_order_with_escalation(ib, contract, contracts, mkt, ticker)
        except Exception as e:
            log.error(f"  ❌ {ticker} — IBKR error: {e}")
            results.append({"ticker": ticker, "status": "failed"})
            if reconnects >= MAX_RECONNECTS:
                log.error(f"  ❌ Max reconnects ({MAX_RECONNECTS}) reached — stopping execution")
                break
            try:
                ib = _reconnect(ib)
                reconnects += 1
            except Exception as re:
                log.error(f"  ❌ Reconnect failed: {re} — stopping execution")
                break
            continue

        result["delta_at_entry"] = round(final_delta, 4) if final_delta is not None else None
        result["iv_at_entry"]    = round(final_iv, 4) if final_iv is not None else None
        results.append(result)

        # Report result back to status callback
        _status(ticker=ticker, stage=None, result={
            "ticker": ticker,
            "status": result["status"],
            "fill_price": result.get("fill_price"),
            "premium_collected": result.get("premium_collected"),
            "order_type": result.get("order_type"),
        })

        if result["status"] in ("filled", "dry_run", "partial_fill"):
            filled_count     += 1
            capital_deployed += pos["capital_used"]
            # Snapshot live stock price at fill for accurate buffer/price in the dashboard
            live_price  = _get_stock_price(ib, ticker)
            stock_price = live_price if live_price is not None else pos.get("latest_price")
            result["stock_price_at_entry"] = stock_price
            fill_price  = result.get("fill_price")
            if result["status"] == "partial_fill" and fill_price:
                filled_qty = round(result.get("premium_collected", 0) / fill_price / 100)
            else:
                filled_qty = contracts
            try:
                _append_trade_log({
                    "symbol":               ticker,
                    "expiry":               contract.lastTradeDateOrContractMonth,
                    "strike":               float(strike),
                    "right":                "P",
                    "entry_date":           result.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                    "delta_at_entry":       round(final_delta, 4) if final_delta is not None else None,
                    "iv_at_entry":          round(final_iv, 4) if final_iv is not None else None,
                    "stock_price_at_entry": stock_price,
                    "buffer_pct_at_entry":  round(((stock_price - strike) / stock_price) * 100, 2) if stock_price else None,
                    "premium_per_contract": fill_price,
                    "contracts":            filled_qty,
                    "total_premium":        result.get("premium_collected"),
                })
                log.info(f"  📝 trade_log.json: {ticker} recorded")
            except Exception as tl_err:
                log.warning(f"  ⚠️  trade_log.json write failed: {tl_err}")
        else:
            log.info(f"  🔄 {ticker} — order failed, trying next candidate")

        if filled_count < _target:
            ib.sleep(3)

    ib.disconnect()

    # ── Summary ───────────────────────────────────────────────
    log.info("\n" + "=" * 65)
    log.info("📊 EXECUTION SUMMARY")
    log.info("=" * 65)

    total_premium = 0
    for r in results:
        status  = r.get("status", "unknown")
        fill    = r.get("fill_price")
        prem    = r.get("premium_collected", 0)
        otype   = r.get("order_type", "")
        sim_tag = " [simulated]" if r.get("simulated") else ""
        total_premium += prem
        fill_str = f"@ ${fill:.2f} via {otype} — ${prem:,.0f}{sim_tag}" if fill else ""
        log.info(f"  {r['ticker']:6s}  {status:20s}  {fill_str}")

    log.info(f"\n  Fills: {filled_count}/{_target}  |  "
             f"Total Premium: ${total_premium:,.0f}")
    log.info("=" * 65)

    # Merge with existing state so wheel_holdings and monday_context survive
    try:
        with open("state.json") as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}
    existing.update({
        "run_date":      datetime.now().isoformat(),
        "positions":     all_sized,   # includes any fallback candidates that were attempted
        "executions":    results,
        "filled_count":  filled_count,
        "total_premium": total_premium
    })
    with open("state.json", "w") as f:
        json.dump(existing, f, indent=2)
    log.info("💾 Results saved to state.json")

    return results


if __name__ == "__main__":
    from screener import get_top_targets
    from position_sizer import size_all
    all_targets = get_top_targets(10)
    positions   = size_all(all_targets)
    execute_positions(positions, extra_targets=all_targets)
