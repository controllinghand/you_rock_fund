"""
Wheel Strategy Manager

detect_assignments() — Friday 4:15PM PST
    Scan IBKR for stock positions created by put assignments.
    Persist to state.json["wheel_holdings"].

run_wheel_check() — Monday, 5 min before the configured execution time (runs before CSP pipeline)
    For each held stock, four-step evaluation:
      Step 1  Screener check: if ticker dropped from screener → sell at market
      Step 2  Option chain: prefer assigned_strike call if delta >= 0.20;
              otherwise fall back to highest strike with delta >= 0.20
      Step 3  Decision: sell CC if viable strike found; else sell at market
      Step 4  Persist monday_context + wheel_activity to state.json

    Returns (freed_capital, skip_tickers) for run_pipeline to consume.
"""
import json
import logging
import time
from datetime import datetime, timedelta
from ib_insync import IB, Stock, Option, LimitOrder, MarketOrder

from config import IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID_WHEEL, ACCOUNT, get_settings, ACCOUNT_TYPE, gateway_unreachable_message, probe_port
from screener import get_all_candidates
from market_calendar import is_market_holiday
import discord_poster

STATE_FILE       = "state.json"
TRADE_LOG_JSON   = "trade_log.json"
MID_WAIT_SECS    = 120
BID_WAIT_SECS    = 120
MARKET_WAIT_SECS = 60
MARKET_POLL_SECS = 5
CC_DELTA_MIN     = 0.20   # minimum call delta required to sell a covered call
MAX_CC_STRIKES   = 25     # max option strikes to evaluate per holding

# Covered-call chain scan waits for option greeks to stream in. A fixed sleep
# sometimes fired before every strike's greeks had arrived, so the picker saw a
# PARTIAL chain and chose the wrong strike (or fell back to the $0.50 mid
# placeholder) — producing different CC results run-to-run off identical frozen
# data. Instead we poll until all qualified strikes have greeks (or the count
# settles / a cap is hit), which makes the scan deterministic.
CC_SCAN_MIN_WAIT      = 2.0   # let the first batch of greeks land before polling
CC_SCAN_MAX_WAIT      = 12.0  # hard cap — some deep-OTM strikes never populate
CC_SCAN_POLL_INTERVAL = 0.5   # re-check cadence
CC_SCAN_STABLE_ROUNDS = 4     # stop once the ready-count is flat this many polls

# Sentinel distinguishing "IBKR returned no greeks at all" (can't price the CC —
# e.g. market closed during a weekend preview) from a genuine "greeks came back
# but none reached CC_DELTA_MIN" (None). Only the latter should sell shares.
CC_NO_DATA       = "NO_DATA"

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


def _logged_cc_premium(symbol: str, expiry: str, strike: float):
    """Gross premium (commission-free: price × 100 × contracts) recorded for an
    open short call in trade_log.json, or None if not logged. Preferred over IBKR
    `avgCost`, which bakes in commissions — #73."""
    try:
        with open(TRADE_LOG_JSON) as f:
            entries = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    for e in entries:
        try:
            if (e.get("symbol") == symbol and str(e.get("expiry")) == str(expiry)
                    and e.get("right") == "C"
                    and abs(float(e.get("strike", -1)) - float(strike)) < 1e-6):
                p = e.get("total_premium")
                return float(p) if p is not None else None
        except (TypeError, ValueError):
            continue
    return None


def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── Two cost bases: strike (P&L) vs net cost (decisions) ───────
# A wheeled holding carries TWO cost numbers because they answer different
# questions (closed issue #68 for the full rationale):
#
#   • `assigned_strike` — the true strike-weighted price we were assigned at,
#     Σ(strike×shares)/Σ(shares) from `tranches`. This is what we PAID for the
#     shares, so it is the basis for all $ P&L, capital, and "assigned @" display.
#     Premium is booked separately (csp_premium/cc_premium), so P&L must use the
#     pure strike or it double-counts the premium.
#
#   • `net_cost` — IBKR's premium-netted avgCost (≈ strike − premium collected),
#     refreshed live from the broker every detection/wheel check. This is our
#     economic breakeven, so it drives DECISIONS: covered-call strike floor and
#     stop-loss. Sourcing it from the broker means these decisions survive a
#     lost/stale state.json (IBKR always has avgCost), and multiple assignments
#     simply blend into it.
#
# `tranches` remains the strike-weighted source for `assigned_strike` (and a
# display/audit breadcrumb of what strikes we were assigned at).

def _avg_cost(tranches: list) -> float:
    """Strike-weighted average price per share across assignment tranches —
    Σ(strike×shares)/Σ(shares), rounded to 2dp. This is `assigned_strike` (the
    P&L basis), NOT the premium-netted net cost. Returns 0.0 for empty/zero."""
    total_shares = sum(t.get("shares", 0) for t in tranches)
    if total_shares <= 0:
        return 0.0
    return round(
        sum(t.get("strike", 0.0) * t.get("shares", 0) for t in tranches) / total_shares,
        2,
    )


def _ensure_tranches(h: dict) -> dict:
    """Lazy migration: legacy holdings predate `tranches`. Synthesize a single
    tranche from the existing assigned_strike/shares so the holding keeps working.
    `tranches` is the strike-weighted source for assigned_strike (the P&L basis)."""
    if not h.get("tranches"):
        shares = h.get("shares", 0)
        if shares > 0:
            h["tranches"] = [{
                "shares": shares,
                "strike": h.get("assigned_strike", 0.0),
                "date":   h.get("assignment_date") or datetime.now().date().isoformat(),
            }]
        else:
            h["tranches"] = []
    return h


# ── IBKR ───────────────────────────────────────────────────────

def _connect(client_id: int = None) -> IB:
    client_id = client_id if client_id is not None else IBKR_CLIENT_ID_WHEEL
    log.info(f"🔌 Connecting to IB Gateway {IBKR_HOST}:{IBKR_PORT} ({ACCOUNT_TYPE}, clientId={client_id})")
    for attempt in range(1, 4):
        try:
            ib = IB()
            ib.connect(IBKR_HOST, IBKR_PORT, clientId=client_id)
            ib.reqMarketDataType(3)
            log.info(f"✅ Connected to IBKR (clientId={client_id})")
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


def _is_nan(val) -> bool:
    try:
        return val != val
    except Exception:
        return True


def _get_stock_price(ib: IB, ticker: str) -> float | None:
    contract  = Stock(ticker, "SMART", "USD")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        return None
    data  = ib.reqMktData(qualified[0], snapshot=False)
    ib.sleep(3)
    price = data.last  if data.last  and data.last  > 0 else \
            data.close if data.close and data.close > 0 else \
            data.bid   if data.bid   and data.bid   > 0 else None
    ib.cancelMktData(qualified[0])
    ib.sleep(0.5)
    return round(float(price), 2) if price else None


def _next_friday_expiry() -> str:
    today      = datetime.now().date()
    days_ahead = 4 - today.weekday()   # Monday=0 → days_ahead=4 (this Friday)
    if days_ahead <= 0:
        days_ahead += 7
    expiry = today + timedelta(days=days_ahead)
    # When that Friday is a market holiday (e.g. Juneteenth), weekly options
    # roll their expiration back to the prior trading day — usually Thursday.
    # Without this the chain lookup asks for a date IBKR never lists and the CC
    # is silently deferred. The CSP path avoids this because it uses the
    # screener's actual tradable expiry.
    while is_market_holiday(expiry):
        expiry -= timedelta(days=1)
    return expiry.strftime("%Y%m%d")


# ── Option chain ───────────────────────────────────────────────

def _find_cc_strike(ib: IB, ticker: str, expiry: str,
                    assigned_strike: float,
                    allow_below_assigned: bool = False) -> tuple | None:
    """
    Find the best call strike to sell as a covered call.

    Priority 1: cost basis — the lowest viable strike (delta >= CC_DELTA_MIN)
                at or above assigned_strike. Higher premium, smaller buffer, but
                getting called away at/above cost basis is acceptable. Matched by
                nearest-strike-above, not exact equality, so a netted cost basis
                that isn't a tradeable strike (e.g. avgCost fallback) still maps
                to the right strike.
    Priority 2: Highest strike with delta >= CC_DELTA_MIN (20-delta CC). Reached
                when no viable strike sits at/above assigned_strike — the stock
                is underwater (and below-cost CCs are allowed) or assigned_strike
                is unknown.

    The assigned_strike is always included in the scan even if below the
    effective price floor, so its delta can be evaluated.

    allow_below_assigned: when True, drop the assigned_strike floor and scan
        from current_price * 0.95 (the standard 20-delta floor) instead. For an
        underwater holding this lets us write a CC BELOW cost basis rather than
        force-selling the shares — collect premium and keep the shares, at the
        cost of being called away below cost if the stock rebounds past the
        strike. This is the DEFAULT (callers pass True); set
        wheel_sell_when_cc_below_assigned=true to force False and force-sell an
        underwater holding instead.

    Returns (strike, delta, mid_price, implied_vol, stock_price), or:
      • CC_NO_DATA — IBKR returned no greeks/chain at all (can't price; e.g. a
        weekend preview with the market closed). Caller should NOT sell shares.
      • None       — greeks came back but no strike reached CC_DELTA_MIN
        (a genuine no-viable-CC outcome). Caller may sell shares.
    """
    stock = Stock(ticker, "SMART", "USD")
    q_stock = ib.qualifyContracts(stock)
    if not q_stock:
        log.warning(f"  ⚠️  {ticker}: cannot qualify stock for option chain lookup")
        return CC_NO_DATA

    stock_data = ib.reqMktData(q_stock[0], "", snapshot=True)
    ib.sleep(2)
    current_price = stock_data.last or stock_data.close or 0
    ib.cancelMktData(q_stock[0])

    # No valid stock price means we can't place the scan floor (and the feed is
    # almost certainly dead — e.g. a weekend preview on an account without
    # delayed-data entitlement, where reqMktData returns NaN). Treat it as
    # "can't price", not "no viable CC": a NaN price is truthy in Python, so it
    # slips past the `current_price > 0` guard below and collapses the scan floor
    # back to assigned_strike — scanning only deep-OTM strikes that can falsely
    # come back with delta < CC_DELTA_MIN, which would wrongly sell the shares.
    # Defer the CC and KEEP the shares instead.
    if _is_nan(current_price) or current_price <= 0:
        log.warning(f"  ⚠️  {ticker}: no valid stock price (got {current_price}) — "
                    f"cannot price CC (feed likely closed/unentitled)")
        return CC_NO_DATA

    chains = ib.reqSecDefOptParams(ticker, "", "STK", q_stock[0].conId)
    if not chains:
        log.warning(f"  ⚠️  {ticker}: IBKR returned no option chain data")
        return CC_NO_DATA

    all_strikes: set[float] = set()
    for chain in chains:
        if expiry in chain.expirations:
            all_strikes.update(chain.strikes)

    if not all_strikes:
        log.warning(f"  ⚠️  {ticker}: expiry {expiry} not listed in option chain")
        return CC_NO_DATA

    price_floor     = current_price * 0.95 if current_price > 0 else assigned_strike
    if allow_below_assigned:
        # Below-cost CCs permitted: scan from the standard 20-delta floor and
        # ignore the assigned_strike floor (it may sit far above current price).
        effective_floor = price_floor
    else:
        effective_floor = max(assigned_strike, price_floor) if assigned_strike > 0 else price_floor
    candidates_set  = {s for s in all_strikes if s >= effective_floor}
    # Always include assigned_strike so we can check its delta regardless of
    # where the stock is trading now.
    if assigned_strike > 0 and assigned_strike in all_strikes:
        candidates_set.add(assigned_strike)
    candidates = sorted(candidates_set)
    log.info(f"  📍 {ticker}: current=${current_price:.2f}  "
             f"assigned_strike=${assigned_strike:.2f}  "
             f"scan_floor=${effective_floor:.2f}"
             f"{'  (below-cost CCs allowed)' if allow_below_assigned else ''}")
    if not candidates:
        log.warning(f"  ⚠️  {ticker}: no strikes >= effective floor ${effective_floor:.2f}")
        return None

    candidates = candidates[:MAX_CC_STRIKES]
    log.info(f"  📊 {ticker}: scanning {len(candidates)} call strike(s) "
             f"[${candidates[0]:.2f}–${candidates[-1]:.2f}] on {expiry}")

    # Qualify all option contracts in a single batched round-trip. ib_insync's
    # qualifyContracts(*contracts) pipelines the underlying reqContractDetails
    # calls concurrently and returns only those it could resolve — far faster
    # than awaiting one call per strike (the dominant cost of the chain scan,
    # ~0.3-0.5s × up to MAX_CC_STRIKES). Behavior is unchanged: same strikes are
    # qualified, and unlisted strikes are simply absent from the result.
    opts = [Option(ticker, expiry, s, "C", "SMART", currency="USD")
            for s in candidates]
    try:
        qualified_opts = ib.qualifyContracts(*opts)
    except Exception as e:
        log.warning(f"  ⚠️  {ticker}: batch contract qualification failed — {e}")
        qualified_opts = []
    # Map each qualified contract back to its strike (set in-place by qualify).
    q_pairs: list[tuple[float, object]] = [
        (o.strike, o) for o in qualified_opts if getattr(o, "conId", 0)
    ]

    if not q_pairs:
        log.warning(f"  ⚠️  {ticker}: no call contracts qualified on {expiry}")
        return CC_NO_DATA

    # Open all market data streams simultaneously — one poll covers all
    streams: dict[float, tuple[object, object]] = {}
    for strike, contract in q_pairs:
        data = ib.reqMktData(contract, genericTickList="", snapshot=False)
        streams[strike] = (contract, data)

    # Poll until every strike's greeks have arrived (or the ready-count settles
    # / the cap is hit), rather than a single fixed sleep that could read a
    # partial chain. This is what makes the scan deterministic — see the
    # CC_SCAN_* constants above.
    def _has_greeks(data) -> bool:
        for attr in ("modelGreeks", "lastGreeks"):
            g = getattr(data, attr, None)
            if g is not None:
                d = getattr(g, "delta", None)
                if d is not None and not _is_nan(d):
                    return True
        return False

    total   = len(streams)
    ready   = -1
    stable  = 0
    elapsed = 0.0
    ib.sleep(CC_SCAN_MIN_WAIT)
    elapsed += CC_SCAN_MIN_WAIT
    while elapsed < CC_SCAN_MAX_WAIT:
        now_ready = sum(1 for (_c, d) in streams.values() if _has_greeks(d))
        if now_ready >= total:
            ready = now_ready
            break
        stable = stable + 1 if now_ready == ready else 0
        ready  = now_ready
        if stable >= CC_SCAN_STABLE_ROUNDS:   # no new arrivals — data has settled
            break
        ib.sleep(CC_SCAN_POLL_INTERVAL)
        elapsed += CC_SCAN_POLL_INTERVAL
    log.info(f"  ⏱️  {ticker}: greeks ready for {ready}/{total} strike(s) "
             f"after {elapsed:.1f}s")

    # Read delta, implied vol and mid from each stream, then cancel
    results: list[tuple[float, float, float | None, float | None]] = []
    for strike, (contract, data) in streams.items():
        ib.cancelMktData(contract)

        delta = None
        iv    = None
        for attr in ("modelGreeks", "lastGreeks"):
            g = getattr(data, attr, None)
            if g is not None:
                d = getattr(g, "delta", None)
                if d is not None and not _is_nan(d):
                    delta = d
                    v = getattr(g, "impliedVol", None)
                    iv = v if (v is not None and not _is_nan(v)) else None
                    break

        if delta is None:
            continue

        bid = data.bid
        ask = data.ask
        mid = round((bid + ask) / 2, 2) \
              if (not _is_nan(bid) and not _is_nan(ask) and bid > 0 and ask > 0) \
              else None
        results.append((strike, abs(delta), mid, iv))

    ib.sleep(0.5)

    if not results:
        log.warning(f"  ⚠️  {ticker}: no delta data returned for any call strike "
                    f"(market likely closed — cannot price CC right now)")
        return CC_NO_DATA

    results.sort(key=lambda x: x[0])  # ascending by strike

    log.info(f"  {'Strike':>8}  {'Delta':>7}  {'Mid':>8}")
    for strike, delta, mid, _iv in results:
        flag    = "✅" if delta >= CC_DELTA_MIN else "❌"
        mid_str = f"${mid:.2f}" if mid else "?"
        log.info(f"  ${strike:>7.2f}  {delta:>6.3f}  {mid_str:>8}  {flag}")

    viable = [(s, d, m, iv) for s, d, m, iv in results if d >= CC_DELTA_MIN]
    if not viable:
        # No strike reached CC_DELTA_MIN. When below-cost CCs are allowed (the
        # DEFAULT), the scan starts near/below the money — where calls have high
        # delta — so an empty viable set almost always means the near-money greeks
        # never streamed in (a partial / feed-contended read), NOT a genuine
        # no-viable-CC. Returning None here makes the caller force-SELL the holding
        # at market — locking a real loss on bad data, which contradicts the wheel
        # default ("never force-sell an underwater holding — write a below-cost CC").
        # So defer and KEEP the shares. Only the explicit force-sell path
        # (allow_below_assigned=False, scanning strikes >= cost basis that CAN be
        # legitimately far OTM) still returns None so the caller may sell.
        if allow_below_assigned:
            log.warning(f"  ⚠️  {ticker}: {len(results)}/{len(q_pairs)} strikes returned "
                        f"greeks but none ≥ {CC_DELTA_MIN:.2f} delta — incomplete read; "
                        f"deferring and KEEPING shares (NOT selling)")
            return CC_NO_DATA
        log.info(f"  ❌ No call strike with delta ≥ {CC_DELTA_MIN:.2f} available "
                 f"(force-sell path — {len(results)}/{len(q_pairs)} strikes read)")
        return None

    # Priority 1 — sell at cost basis: the LOWEST viable strike at or above
    # assigned_strike (viable is sorted ascending). Selling there captures higher
    # premium and is a clean exit if called away. We match the nearest strike
    # >= assigned_strike rather than an exact equality, because assigned_strike is
    # sometimes a net cost basis rather than a tradeable strike — e.g. the IBKR
    # avgCost fallback records strike-minus-premium (AAOI's $164.28 for a $165
    # put), which never equals a real strike. An exact `==` match silently skips
    # this path and always falls through to the 20-delta strike below.
    at_or_above = (
        [(s, d, m, iv) for s, d, m, iv in viable if s >= assigned_strike]
        if assigned_strike > 0 else []
    )
    if at_or_above:
        s, d, m, iv = at_or_above[0]
        log.info(f"  🎯 Cost-basis strike ${s:.2f} (≥ assigned ${assigned_strike:.2f}) "
                 f"delta={d:.3f} — selling CC there")
        return (s, d, m, iv, current_price)

    # Priority 2 — no viable strike at/above cost basis (stock is underwater, or
    # assigned_strike unknown). Use the highest qualifying strike (~20-delta CC).
    # With below-cost CCs enabled this writes below assigned_strike; otherwise the
    # assigned_strike scan floor means this is only reached when nothing qualifies
    # and the caller force-sells.
    s, d, m, iv = viable[-1]
    log.info(f"  🎯 No viable strike at/above assigned ${assigned_strike:.2f} — "
             f"using ${s:.2f} (delta={d:.3f})")
    return (s, d, m, iv, current_price)


# ── Orders ─────────────────────────────────────────────────────

def _sell_stock_market(ib: IB, ticker: str, shares: int, reason: str,
                       assigned_strike: float = 0.0, dry_run: bool = False) -> dict:
    if dry_run:
        # Preview only: simulate a market fill at the current price. No order,
        # no Discord alert, no trade-log write.
        price = _get_stock_price(ib, ticker)
        if price is None:
            log.warning(f"  🟡 [DRY RUN] {ticker}: price unavailable — cannot simulate sale")
            return {"status": "failed", "proceeds": 0.0, "fill_price": None, "dry_run": True}
        proceeds = round(shares * price, 2)
        log.info(f"  🟡 [DRY RUN] would SELL {shares} {ticker} @ ~${price:.2f} "
                 f"= ${proceeds:,.0f}  [{reason}]")
        return {"status": "filled", "fill_price": price, "proceeds": proceeds, "dry_run": True}

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
                              strike: float, ref_mid: float, dry_run: bool = False) -> dict:
    """Sell `shares // 100` covered-call contracts via limit-mid → limit-bid →
    market escalation.

    Every leg orders ONLY the still-unfilled remainder and accumulates whatever
    filled on prior legs (including a cancelled limit leg's partial fill), so:
      • the market leg can never re-send the full quantity → no over-sell / naked
        call (was #72 Bug B),
      • partial fills are never dropped from the books (was #72 Bug A).
    `premium_collected` is gross (Σ leg_filled × leg_fill × 100); `fill_price` is
    the share-weighted average across legs; `filled_contracts` is the true total.
    """
    num_contracts = shares // 100
    if num_contracts < 1:
        log.warning(f"  ⚠️  {ticker}: {shares} shares < 100 — cannot sell CC")
        return {"ticker": ticker, "status": "skipped_insufficient_shares",
                "premium_collected": 0.0, "fill_price": None, "order_type": None,
                "filled_contracts": 0}

    if dry_run:
        # Preview only: simulate a fill at the mid price. No order placed.
        premium = round(num_contracts * ref_mid * 100, 2)
        log.info(f"  🟡 [DRY RUN] would SELL {num_contracts}x {ticker} CALL "
                 f"${strike:.2f} @ ~${ref_mid:.2f}  = ${premium:,.0f}")
        return {"ticker": ticker, "option_contracts": num_contracts, "shares": shares,
                "strike": strike, "status": "filled", "fill_price": ref_mid,
                "order_type": "dry_run", "premium_collected": premium,
                "filled_contracts": num_contracts,
                "timestamp": datetime.now().isoformat(), "dry_run": True}

    filled_total  = 0      # contracts filled across all legs
    premium_total = 0.0    # gross premium collected across all legs

    def remaining() -> int:
        return num_contracts - filled_total

    def _record_leg(trade, label: str) -> None:
        """Fold whatever filled on this leg (partial or full) into the totals."""
        nonlocal filled_total, premium_total
        leg_filled = int(trade.orderStatus.filled or 0)
        if leg_filled > 0:
            fill = trade.orderStatus.avgFillPrice or 0.0
            premium_total += leg_filled * fill * 100
            filled_total  += leg_filled
            log.info(f"  ✅ {label}: filled {leg_filled}x {ticker} CALL ${strike:.2f} "
                     f"@ ${fill:.2f}  ({filled_total}/{num_contracts} total)")

    def _result(status: str) -> dict:
        avg_fill = round(premium_total / filled_total / 100, 2) if filled_total else None
        return {"ticker": ticker, "option_contracts": num_contracts, "shares": shares,
                "strike": strike, "status": status, "fill_price": avg_fill,
                "order_type": "escalation", "premium_collected": round(premium_total, 2),
                "filled_contracts": filled_total, "timestamp": datetime.now().isoformat()}

    def try_limit(price: float, label: str, wait: int) -> bool:
        qty = remaining()
        if qty < 1:
            return True
        log.info(f"  📤 {label}: SELL {qty}x {ticker} CALL ${strike:.2f} @ ${price:.2f}")
        order = LimitOrder("SELL", qty, price, account=ACCOUNT, tif="DAY")
        trade = ib.placeOrder(contract, order)
        ib.sleep(wait)
        if trade.orderStatus.status != "Filled":
            # Not fully filled — cancel the remainder, then record whatever DID
            # fill (a cancelled limit can still have a partial fill).
            log.info(f"  ⏳ {label} not fully filled — cancelling remainder, escalating...")
            ib.cancelOrder(trade.order)
            ib.sleep(1)
        _record_leg(trade, label)
        return remaining() < 1

    if try_limit(ref_mid, "limit_mid", MID_WAIT_SECS):
        return _result("filled")
    if try_limit(round(ref_mid * 0.90, 2), "limit_bid", BID_WAIT_SECS):
        return _result("filled")

    # Market order for the REMAINING contracts only (never the full quantity).
    qty = remaining()
    if qty >= 1:
        log.info(f"  📤 Market order: SELL {qty}x {ticker} CALL ${strike:.2f}")
        trade = ib.placeOrder(contract, MarketOrder("SELL", qty, account=ACCOUNT, tif="DAY"))
        elapsed = 0
        while elapsed < MARKET_WAIT_SECS:
            ib.sleep(MARKET_POLL_SECS)
            elapsed += MARKET_POLL_SECS
            st  = trade.orderStatus.status
            rem = trade.orderStatus.remaining
            fl  = trade.orderStatus.filled
            if st == "Filled" or (rem == 0 and fl > 0):
                break
            log.info(f"  ⏳ market {st}: {int(fl or 0)}/{qty} after {elapsed}s")
        _record_leg(trade, "market")

    if filled_total >= num_contracts:
        return _result("filled")
    if filled_total > 0:
        log.warning(f"  ⚠️  {ticker}: partial CC fill {filled_total}/{num_contracts} "
                    f"across legs — {remaining()} contracts uncovered")
        return _result("partial_fill")
    log.error(f"  ❌ CC order failed for {ticker} (0 of {num_contracts} filled)")
    return _result("failed")


def _get_option_mid(ib: IB, ticker: str, expiry: str, strike: float):
    """Quote a single known call strike (used to price a coverage top-up at the
    SAME strike as the existing open CC). Returns (qualified_contract, mid) — mid
    is None if no live bid/ask (e.g. market closed); the caller falls back to the
    existing position's average fill price."""
    opt = Option(ticker, expiry, strike, "C", "SMART", currency="USD")
    try:
        q = ib.qualifyContracts(opt)
    except Exception as e:
        log.warning(f"  ⚠️  {ticker}: cannot qualify top-up call ${strike:.2f} {expiry}: {e}")
        return None, None
    if not q:
        return None, None
    data = ib.reqMktData(q[0], genericTickList="", snapshot=False)
    ib.sleep(2)
    bid, ask = data.bid, data.ask
    mid = round((bid + ask) / 2, 2) \
          if (not _is_nan(bid) and not _is_nan(ask) and bid > 0 and ask > 0) else None
    ib.cancelMktData(q[0])
    ib.sleep(0.3)
    return q[0], mid


def _set_cc_coverage(h: dict, *, strike, expiry, premium, covered: int, needed: int) -> str:
    """Record the REAL open-CC state on a holding and return the coverage status.

    Coverage is tracked by contract count, not as a binary flag:
      - "open"    when covered >= needed (fully covered)
      - "partial" when 0 < covered < needed (shares still uncovered)
    Storing cc_contracts / cc_contracts_needed lets the dashboard show "N/needed"
    and keeps current_cc_* in sync with IBKR instead of going stale.
    """
    status = "open" if covered >= needed else "partial"
    if strike is not None:
        h["current_cc_strike"] = strike
    if expiry is not None:
        h["current_cc_expiry"] = expiry
    if premium is not None:
        h["current_cc_premium"] = round(premium, 2)
    h["cc_contracts"]        = covered
    h["cc_contracts_needed"] = needed
    h["cc_status"]           = status
    return status


# ── Public API ─────────────────────────────────────────────────

def detect_assignments():
    """
    Saturday 8AM PST — scan IBKR for stock positions and reconcile
    against known wheel_holdings. Runs Saturday morning (not Friday
    afternoon) so IBKR has posted the prior day's option assignments
    and expirations overnight. New assignments are added with the
    assigned strike looked up from that week's state.json positions.
    Holdings whose CC has expired and are no longer in IBKR are
    recognized as called away and removed from wheel_holdings.

    Returns list of called-away holding dicts (may be empty).
    """
    log.info("\n" + "=" * 65)
    log.info(f"🔍 SATURDAY ASSIGNMENT DETECTION — "
             f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 65)

    state             = _load_state()
    existing_holdings = {h["ticker"]: h for h in state.get("wheel_holdings", [])}
    strike_lookup     = {p["ticker"]: p["strike"] for p in state.get("positions", [])}
    # Tickers the user excluded from the wheel — never adopt as a new holding.
    excluded          = {t.strip().upper() for t in get_settings().get("excluded_tickers", []) if t and t.strip()}

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
        # IBKR's premium-netted avgCost is the source of truth for cost basis
        # (see the cost-basis note below). Snapshot it per symbol so a lost/stale
        # state.json is always reconstructable from the broker on the next run.
        avg_cost_lookup = {
            p.contract.symbol: round(p.avgCost, 2)
            for p in ibkr_positions
            if p.contract.secType == "STK" and int(p.position) > 0
        }
    finally:
        ib.disconnect()

    log.info(f"📊 Found {len(stock_positions)} stock position(s) in IBKR")

    # Identify holdings whose CC expired and are no longer in IBKR — called away.
    # CC expiry is stored as "YYYYMMDD"; compare against today in the same format.
    today_str     = datetime.now().strftime("%Y%m%d")
    called_away   = []
    for ticker, h in existing_holdings.items():
        cc_expiry = h.get("current_cc_expiry")
        if (h.get("cc_status") in ("open", "partial")
                and cc_expiry
                and cc_expiry <= today_str
                and ticker not in stock_positions):
            cc_strike       = h.get("current_cc_strike") or h.get("assigned_strike", 0.0)
            assigned_strike = h.get("assigned_strike", 0.0)
            shares          = h.get("shares", 0)
            stock_pnl       = round((cc_strike - assigned_strike) * shares, 2)
            cc_premium      = h.get("current_cc_premium", 0.0)
            log.info(f"  📤 {ticker}: CC expired {cc_expiry}, no longer in IBKR — "
                     f"called away  stock P&L ${stock_pnl:+,.0f}  "
                     f"CC premium ${cc_premium:,.0f}")
            called_away.append({**h, "_stock_pnl": stock_pnl})

    called_away_tickers = {h["ticker"] for h in called_away}

    # Safety guard: bail only if IBKR shows 0 positions AND there are holdings
    # that are NOT explained by an expired CC (i.e., data may be unreliable).
    unexplained = [t for t in existing_holdings
                   if t not in stock_positions and t not in called_away_tickers]
    if not stock_positions and unexplained:
        log.error(f"❌ IBKR returned 0 stock positions but {len(unexplained)} "
                  f"holding(s) have no expired CC to explain their absence — "
                  f"skipping save to avoid data loss: {unexplained}")
        return []

    updated         = []
    new_assignments = []
    for ticker, shares in stock_positions.items():
        if ticker in existing_holdings:
            h            = _ensure_tranches(existing_holdings[ticker])
            prior_shares = sum(t.get("shares", 0) for t in h["tranches"])
            delta        = shares - prior_shares
            if delta > 0:
                # New tranche assigned at this week's CSP strike — blend it into
                # the strike-weighted assigned_strike (the P&L basis).
                new_strike = strike_lookup.get(ticker, 0.0)
                if new_strike > 0:
                    if delta % 100 != 0:
                        log.warning(
                            f"  ⚠️  {ticker}: assigned share delta {delta} is not a "
                            f"round lot — two assignments may have collapsed into a "
                            f"single tranche at ${new_strike:.2f}; verify in state.json")
                    h["tranches"].append({
                        "shares": delta, "strike": new_strike,
                        "date":   datetime.now().date().isoformat(),
                    })
                    h["assigned_strike"] = _avg_cost(h["tranches"])
                    log.info(f"  ➕ {ticker}: +{delta} shares @ ${new_strike:.2f} "
                             f"(new tranche) — assigned_strike now "
                             f"${h['assigned_strike']:.2f} across {shares} shares")
                else:
                    log.warning(
                        f"  ⚠️  {ticker}: +{delta} shares but no CSP strike in state — "
                        f"cannot blend tranche; assigned_strike left at "
                        f"${h.get('assigned_strike', 0.0):.2f}")
            elif delta < 0:
                log.info(f"  ✅ {ticker}: {shares} shares (down {-delta} — partial "
                         f"call-away/sale; tranches & assigned_strike unchanged)")
            else:
                log.info(f"  ✅ {ticker}: {shares} shares (existing — unchanged)")
            h["shares"]       = shares
            # net_cost = IBKR avgCost (premium-netted), refreshed every detection —
            # drives CC-floor / stop-loss decisions. Broker-sourced so it survives
            # a lost/stale state.json.
            net_cost = avg_cost_lookup.get(ticker, 0.0)
            if net_cost > 0:
                h["net_cost"] = net_cost
                log.info(f"  💰 {ticker}: net cost ${net_cost:.2f} (IBKR avgCost) "
                         f"vs assigned ${h.get('assigned_strike', 0.0):.2f}")
            h["last_checked"] = datetime.now().isoformat()
        else:
            if ticker.upper() in excluded:
                log.info(f"  🚫 {ticker}: excluded from the wheel — not adopting "
                         f"as a new assignment (left as a plain hold)")
                continue
            # assigned_strike (P&L basis) = the CSP strike we were assigned at.
            # Fall back to IBKR avgCost only if the strike isn't in state.
            assigned_strike = strike_lookup.get(ticker, 0.0)
            if assigned_strike == 0.0:
                assigned_strike = avg_cost_lookup.get(ticker, 0.0)
                log.warning(f"  ⚠️  {ticker}: strike not in state — using IBKR "
                            f"avgCost ${assigned_strike:.2f} as assigned_strike")
            h = {
                "ticker":             ticker,
                "shares":             shares,
                "assigned_strike":    assigned_strike,
                "net_cost":           avg_cost_lookup.get(ticker, 0.0) or assigned_strike,
                "tranches":           [{
                    "shares": shares,
                    "strike": assigned_strike,
                    "date":   datetime.now().date().isoformat(),
                }],
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
        # called-away holdings are intentionally excluded from updated → removed from state

    state["wheel_holdings"] = updated
    _save_state(state)
    log.info(f"\n💾 Saved {len(updated)} wheel holding(s) to state.json "
             f"({len(called_away)} called away, {len(new_assignments)} new assignment(s))")
    log.info("=" * 65)
    # Discord alert for new assignments. (Previously dead code: it sat after the
    # return below and never fired — fixed alongside the tranche rewrite.)
    if new_assignments:
        discord_poster.post_assignment_alert(new_assignments)
    return called_away


def run_wheel_check(dry_run: bool = False, client_id: int = None,
                    progress_callback=None) -> dict:
    """
    Monday, 5 min before the configured execution time (PST) — five-step
    evaluation for each held stock:

      Step 1  Screener check — if ticker no longer passes screener filters,
              sell all shares at market and free capital.
      Step 2  Earnings check — if earnings fall within 0–4 days (Mon–Fri
              this week), sell all shares to avoid earnings risk.
      Step 3  Option chain — query IBKR for call strikes >= assigned_strike
              on the nearest Friday; collect delta for each.
      Step 4  Decision — sell CC at assigned_strike if its delta ≥ 0.20;
              else sell the highest-delta (≥ 0.20) strike. If none, sell shares.
      Step 5  Persist monday_context and wheel_activity to state.json.

    dry_run: when True, computes the exact same keep/sell/CC decisions and
    queries IBKR for chains + prices, but places NO orders, writes NO state,
    posts NO Discord alerts, and makes NO trade-log entries. Used by the
    dashboard "Run Screener" preview so it mirrors Monday without side effects.

    client_id: IBKR client id to connect with (defaults to the wheel id). The
    API-driven runner passes a distinct id to avoid colliding with the scheduled wheel job.

    Returns a dict: freed_capital, skip_tickers, reserved_capital, cc_premium,
    shares_sold_pnl, active_wheel_count, wheel_activity, dry_run.
    """
    mode = "DRY RUN" if dry_run else "LIVE"
    log.info("\n" + "=" * 65)
    log.info(f"🔄 MONDAY WHEEL CHECK [{mode}] — "
             f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 65)

    def _progress(ticker=None, stage=None, result=None):
        """Push live wheel-check progress to the dashboard run-status feed, so the
        CC phase is as visible as the CSP phase. Best-effort — never breaks the run."""
        if progress_callback:
            try:
                progress_callback(ticker=ticker, stage=stage, result=result)
            except Exception:
                pass

    state    = _load_state()
    holdings = [_ensure_tranches(h) for h in state.get("wheel_holdings", [])]

    # Read wheel settings LIVE at execution time (not the import-time config
    # constants). The API preview/Run-Now paths reload config before calling, but
    # the long-running scheduler does not — reading get_settings() here makes the
    # live Monday job honor the current Settings toggles too, so Run Screener,
    # Run Now and the live 9:55 run all act on the same values.
    _s                        = get_settings()
    cc_ignore_earnings        = _s.get("wheel_cc_ignore_earnings_filter", True)
    # Default: write a 20-delta CC below cost rather than force-sell an underwater
    # holding. Opt in to force-selling (the old behavior) via this setting.
    sell_when_cc_below        = _s.get("wheel_sell_when_cc_below_assigned", False)
    allow_cc_below_assigned   = not sell_when_cc_below
    retention_market_cap_min  = _s.get("wheel_retention_market_cap_min", 5_000_000_000)
    stop_loss_enabled         = _s.get("wheel_stop_loss_enabled", False)
    stop_loss_pct             = _s.get("stop_loss_pct", 0.10)
    # Default ON: when a holding is only partially covered, write the shortfall so
    # every owned share carries a covered call (topped up at the existing CC's
    # strike/expiry). Turn off to leave partial holdings as-is.
    cover_all_shares          = _s.get("wheel_cover_all_shares", True)
    # Tickers the user excluded from the wheel — never adopt, never CC, never sell.
    excluded                  = {t.strip().upper() for t in _s.get("excluded_tickers", []) if t and t.strip()}

    # Order-placement gate. Simulate CC/stock-sale orders when EITHER this is a
    # preview run (pipeline dry_run) OR the Settings "Dry Run" toggle is ON — read
    # LIVE here, so the long-running scheduler honors a UI toggle without a restart
    # (the same stale-snapshot fix applied to CSP execution in trader.py). Kept
    # separate from the pipeline `dry_run` so a live-market run with Dry Run ON still
    # writes monday_context/Discord and still prices CCs on real greeks (type-4 switch
    # below stays on `dry_run`) — it just simulates the fills instead of placing them.
    orders_dry_run            = dry_run or _s.get("dry_run", False)
    if orders_dry_run and not dry_run:
        log.info("  🧪 Settings Dry Run is ON — wheel orders will be simulated (no real CC/stock orders)")

    freed_capital   = 0.0
    skip_tickers    = []
    cc_premium      = 0.0
    shares_sold_pnl = 0.0
    wheel_activity  = []
    candidate_info  = {}
    expiry          = _next_friday_expiry()
    # Recovery reconciliation — what is ALREADY open in IBKR (so a re-run never
    # duplicates). Source of truth is the live account, not state.json.
    open_short_calls       = {}     # (symbol, expiry YYYYMMDD) -> contracts short
    open_short_call_meta   = {}     # (symbol, expiry YYYYMMDD) -> {contracts, strike, premium}
    open_short_put_tickers = set()  # symbols with an open short put (CSP)
    tickers_with_open_call = set()  # symbols with any open short call (covered)

    ib = _connect(client_id)

    # In a preview (dry_run) the market is usually closed (weekend / pre-open),
    # so the default delayed feed (type 3) returns no option greeks and every
    # holding would look like "no viable CC". Switch to delayed-frozen (type 4),
    # which serves the last snapshot (Friday's close) and needs no OPRA
    # entitlement on paper or live, so we can still price CCs for the preview.
    # The live 9:55 run keeps type 3 — the market is open then.
    if dry_run:
        ib.reqMarketDataType(4)
        log.info("  🧊 Preview: using delayed-frozen market data (last/Friday close)")

    try:
        # ── Step 0: Sync against live IBKR stock positions ────
        # Catches assignments that detect_assignments() may have missed on Friday.
        ib.reqPositions()
        ib.sleep(2)
        live_pos      = ib.positions(account=ACCOUNT)
        strike_lookup = {p["ticker"]: p["strike"] for p in state.get("positions", [])}
        known_tickers = {h["ticker"] for h in holdings}
        # IBKR avgCost is the authoritative cost basis (see the cost-basis note
        # above). Snapshot it live so Monday's CC/stop-loss decisions use the
        # broker's number, self-correcting any stale/lost state.json value.
        avg_cost_lookup = {
            p.contract.symbol: round(p.avgCost, 2)
            for p in live_pos
            if p.contract.secType == "STK" and int(p.position) > 0
        }

        # Snapshot open option positions for recovery dedup
        for p in live_pos:
            c   = p.contract
            pos = int(p.position)
            if c.secType == "OPT" and pos < 0:
                if c.right == "C":
                    key = (c.symbol, c.lastTradeDateOrContractMonth)
                    n   = abs(pos)
                    open_short_calls[key] = open_short_calls.get(key, 0) + n
                    # Capture strike + premium so a recovery re-run can record the
                    # ACTUAL open CC on the holding (not leave stale current_cc_*).
                    # IBKR option avgCost is per-contract total (price × 100).
                    m = open_short_call_meta.setdefault(
                        key, {"contracts": 0, "premium": 0.0, "strike": c.strike})
                    m["contracts"] += n
                    m["premium"]   += (p.avgCost or 0.0) * n
                    m["strike"]     = c.strike
                elif c.right == "P":
                    open_short_put_tickers.add(c.symbol)
        # Prefer commission-free gross premium from trade_log over IBKR avgCost
        # (which includes commissions) for the recorded current_cc_premium — #73.
        for (sym, exp), m in open_short_call_meta.items():
            gross = _logged_cc_premium(sym, exp, m.get("strike"))
            m["premium"] = gross if gross is not None else round(m["premium"], 2)
        tickers_with_open_call = {sym for (sym, _exp) in open_short_calls}
        if open_short_calls or open_short_put_tickers:
            calls_str = ", ".join(f"{k[0]} {k[1]}×{v}" for k, v in open_short_calls.items()) or "none"
            log.info(f"  🔎 Reconcile — open short calls: {calls_str}  |  "
                     f"open short puts: {sorted(open_short_put_tickers) or 'none'}")
        for p in live_pos:
            if p.contract.secType == "STK" and int(p.position) > 0:
                sym = p.contract.symbol
                if sym.upper() in excluded:
                    log.info(f"  🚫 {sym}: excluded from the wheel — not adopting "
                             f"into wheel_holdings (left as a plain hold)")
                    continue
                if sym not in known_tickers:
                    # No state for this holding — assigned_strike (P&L basis) falls
                    # back to the CSP strike if known, else IBKR avgCost. net_cost
                    # (decisions) is always the broker's avgCost.
                    avg = round(p.avgCost, 2)
                    strike = strike_lookup.get(sym, 0.0) or avg
                    if strike_lookup.get(sym, 0.0) == 0.0:
                        log.warning(f"  ⚠️  {sym}: strike not in state — using IBKR "
                                    f"avgCost ${avg:.2f} as assigned_strike")
                    log.warning(f"⚠️  Untracked stock detected: {sym} "
                                f"{int(p.position)} shares @ ${strike:.2f} — "
                                f"adding to wheel_holdings")
                    holdings.append({
                        "ticker":             sym,
                        "shares":             int(p.position),
                        "assigned_strike":    strike,
                        "net_cost":           avg or strike,
                        "tranches":           [{
                            "shares": int(p.position),
                            "strike": strike,
                            "date":   datetime.now().date().isoformat(),
                        }],
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
            if cc_ignore_earnings:
                log.info("  ⚠️  wheel_cc_ignore_earnings_filter=true — earnings filter bypassed for CC decisions")
            if sell_when_cc_below:
                log.info("  ⚠️  wheel_sell_when_cc_below_assigned=true — underwater names with no CC ≥ cost are force-sold (not written below cost)")
            else:
                log.info("  ℹ️  Below-cost CCs enabled (default) — underwater names write a 20-delta CC below cost instead of force-selling")
            log.info(f"  📉 Retention market-cap floor: ${retention_market_cap_min/1e9:.1f}B "
                     f"(vs entry floor — held names below entry floor are kept if above this)")
            candidate_info = get_all_candidates(
                ignore_earnings_filter=cc_ignore_earnings,
                market_cap_min=retention_market_cap_min,
                retention=True,
            )
            if candidate_info:
                log.info(f"  ✅ {len(candidate_info)} ticker(s) pass screener filters")
            else:
                log.warning("  ⚠️  Screener returned 0 tickers — API may be down")
                log.warning("  Skipping screener/earnings checks; will attempt CCs for all holdings")

        for h in holdings:
            ticker          = h["ticker"]
            shares          = h.get("shares", 0)
            # assigned_strike (strike-weighted) is the P&L basis — leave it as the
            # stored value. net_cost is the premium-netted breakeven that drives
            # CC-floor / stop-loss decisions: refresh it live from the broker
            # (source of truth) and cache back, so those decisions self-correct any
            # stale/lost state.json value.
            assigned_strike = h.get("assigned_strike", 0.0)
            net_cost        = avg_cost_lookup.get(ticker) or h.get("net_cost") or assigned_strike
            h["net_cost"]   = net_cost

            # ── Excluded from the wheel: leave it alone entirely ──
            # No CC, no sell, no weeks_held bump. The user has opted this name out
            # (e.g. a long-term hold) — the app must not trade it.
            if ticker.upper() in excluded:
                log.info(f"  🚫 {ticker}: excluded from the wheel — no CC, no sell (left as-is)")
                h["last_checked"] = datetime.now().isoformat()
                wheel_activity.append({"ticker": ticker, "action": "skipped_excluded"})
                _progress(ticker=ticker, stage="excluded",
                          result={"ticker": ticker, "status": "skipped_excluded"})
                continue

            # ── Recovery guard: already covered by a live short call ──
            # If a CC is already open on this ticker, the shares are covered.
            # On a re-run we must NOT sell them (selling would leave a naked call,
            # and we never buy back CCs) and must NOT write a second CC. Skip the
            # whole holding before touching weeks_held so the re-run is idempotent.
            if shares > 0 and ticker in tickers_with_open_call:
                exps     = sorted({e for (s, e) in open_short_calls if s == ticker})
                needed   = shares // 100
                covered  = sum(v for (s, e), v in open_short_calls.items() if s == ticker)
                # Record the ACTUAL open CC on the holding (fixes stale current_cc_*):
                # prefer this week's target expiry, else the expiry with the most
                # contracts. Premium = sum of collected premium across the ticker's
                # open calls (IBKR avgCost is per-contract total).
                metas = {e: open_short_call_meta.get((ticker, e), {}) for e in exps}
                rec_expiry = expiry if (ticker, expiry) in open_short_calls else (
                    max(metas, key=lambda e: metas[e].get("contracts", 0)) if metas else None)
                rec = metas.get(rec_expiry, {})
                rec_premium = sum(m.get("premium", 0.0) for m in metas.values())
                status = _set_cc_coverage(
                    h, strike=rec.get("strike"), expiry=rec_expiry,
                    premium=rec_premium, covered=covered, needed=needed)
                h["last_checked"] = datetime.now().isoformat()
                topup_strike = rec.get("strike")
                if status == "open":
                    log.info(f"  ♻️  {ticker}: already covered by open CC "
                             f"({covered}/{needed}, exp {', '.join(exps)}) — leaving as-is (recovery-safe)")
                    action = "cc_already_open"
                else:
                    # Partially covered. Default ON: write the shortfall at the
                    # EXISTING strike/expiry so it stays one uniform position
                    # (Phase 1 — no schema change). Never sell covered shares.
                    # Honors the Settings Dry Run toggle via orders_dry_run.
                    shortfall = needed - covered
                    dropped   = bool(candidate_info) and ticker not in candidate_info
                    if not cover_all_shares:
                        log.warning(f"  ⚠️  {ticker}: only {covered}/{needed} covered "
                                    f"({shortfall} short) — wheel_cover_all_shares off, leaving as-is")
                        action = "cc_partial"
                    elif dropped:
                        log.warning(f"  ⚠️  {ticker}: {covered}/{needed} covered but dropped from "
                                    f"screener — not topping up (slated for exit)")
                        action = "cc_partial"
                    elif not (topup_strike and rec_expiry):
                        log.warning(f"  ⚠️  {ticker}: {covered}/{needed} covered — cannot resolve "
                                    f"existing strike/expiry, leaving as-is")
                        action = "cc_partial"
                    else:
                        log.info(f"  ➕ {ticker}: {covered}/{needed} covered — writing {shortfall} "
                                 f"more @ ${topup_strike:.2f} {rec_expiry} (cover-all)")
                        _progress(ticker=ticker, stage=f"covering shortfall {shortfall}x ${topup_strike:.0f}")
                        contract, mid = _get_option_mid(ib, ticker, rec_expiry, topup_strike)
                        avg_fill = (rec_premium / covered / 100) if covered else 0.0
                        ref_mid  = mid if mid else (round(avg_fill, 2) or 0.50)
                        if not contract:
                            log.warning(f"  ⚠️  {ticker}: could not qualify top-up strike — "
                                        f"leaving {covered}/{needed}")
                            action = "cc_partial"
                        else:
                            order_result = _sell_cc_with_escalation(
                                ib, contract, shortfall * 100, ticker,
                                topup_strike, ref_mid, dry_run=orders_dry_run)
                            if order_result["status"] in ("filled", "partial_fill"):
                                filled   = int(order_result.get("filled_contracts") or 0)
                                add_prem = order_result["premium_collected"]
                                cc_premium += add_prem
                                covered   = covered + filled
                                new_stat  = _set_cc_coverage(
                                    h, strike=topup_strike, expiry=rec_expiry,
                                    premium=rec_premium + add_prem, covered=covered, needed=needed)
                                log.info(f"  ➕ {ticker}: topped up {filled}/{shortfall} @ "
                                         f"${topup_strike:.2f} — now {covered}/{needed} ({new_stat}); "
                                         f"premium +${add_prem:,.0f}")
                                action = "cc_topped_up" if new_stat == "open" else "cc_partial"
                                # Gate on orders_dry_run (not the pipeline dry_run):
                                # a live run with the Settings "Dry Run" toggle ON
                                # only SIMULATES the fill, so it must not be recorded
                                # in trade_log.json as a real trade.
                                if not orders_dry_run and filled > 0:
                                    try:
                                        _append_trade_log({
                                            "symbol":               ticker,
                                            "expiry":               rec_expiry,
                                            "strike":               float(topup_strike),
                                            "right":                "C",
                                            "entry_date":           datetime.now().isoformat(),
                                            "premium_per_contract": order_result.get("fill_price"),
                                            "contracts":            filled,
                                            "total_premium":        add_prem,
                                        })
                                    except Exception as tl_err:
                                        log.warning(f"  ⚠️  trade_log.json write failed: {tl_err}")
                            else:
                                log.warning(f"  ⚠️  {ticker}: top-up order failed — "
                                            f"leaving {covered}/{needed}")
                                action = "cc_partial"
                wheel_activity.append({
                    "ticker":    ticker,
                    "action":    action,
                    "cc_expiry": rec_expiry,
                    "contracts": covered,
                    "needed":    needed,
                })
                _progress(ticker=ticker, stage=f"covered {covered}/{needed}",
                          result={"ticker": ticker, "status": action,
                                  "contracts": covered, "needed": needed})
                continue

            weeks_held      = h.get("weeks_held", 0) + 1
            h["weeks_held"] = weeks_held
            h["last_checked"] = datetime.now().isoformat()

            log.info(f"\n  ── {ticker}  {shares} shares  "
                     f"@ ${assigned_strike:.2f}  week {weeks_held} ──")
            _progress(ticker=ticker, stage="checking covered call")

            if shares <= 0:
                log.info(f"  ⏭️  {ticker}: 0 shares — skipping")
                continue

            # ── Step 1: Screener check ────────────────────────
            if candidate_info and ticker not in candidate_info:
                log.warning(f"  🚫 {ticker}: dropped from screener — selling shares")
                _progress(ticker=ticker, stage="dropped screener — selling")
                result = _sell_stock_market(ib, ticker, shares, "dropped_screener",
                                               assigned_strike=assigned_strike, dry_run=orders_dry_run)
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
                    _progress(ticker=ticker, stage="sold (dropped screener)",
                              result={"ticker": ticker, "status": "sold_dropped_screener",
                                      "shares": shares, "proceeds": proceeds})
                else:
                    log.error(f"  ❌ Sale FAILED for {ticker} — MANUAL ACTION REQUIRED")
                    _progress(ticker=ticker, stage="sale FAILED",
                              result={"ticker": ticker, "status": "sell_failed"})
                continue

            log.info(f"  ✅ {ticker} on screener — checking stop loss")

            # ── Step 1b: Stop-loss check ──────────────────────
            # Measured vs net_cost (premium-netted breakeven), not the assignment
            # strike — this is a decision about our economic position.
            if stop_loss_enabled and net_cost > 0:
                current_price = _get_stock_price(ib, ticker)
                if current_price is None:
                    log.warning(f"  ⚠️  {ticker}: price unavailable — skipping stop-loss check")
                else:
                    stop_threshold = round(net_cost * (1 - stop_loss_pct), 2)
                    loss_pct       = round((1 - current_price / net_cost) * 100, 1)
                    if current_price < stop_threshold:
                        log.warning(
                            f"  🛑 {ticker}: price ${current_price:.2f} is {loss_pct:.1f}% below "
                            f"net cost ${net_cost:.2f} "
                            f"(threshold {stop_loss_pct*100:.0f}%) — selling shares"
                        )
                        result = _sell_stock_market(ib, ticker, shares, "stop_loss",
                                                    assigned_strike=assigned_strike, dry_run=orders_dry_run)
                        if result["status"] == "filled":
                            proceeds = result["proceeds"]
                            realized = round(proceeds - (assigned_strike * shares), 2)
                            freed_capital   += proceeds
                            shares_sold_pnl += realized
                            skip_tickers.append(ticker)
                            h["shares"]    = 0
                            h["cc_status"] = "sold_stop_loss"
                            wheel_activity.append({
                                "ticker":       ticker,
                                "action":       "sold_stop_loss",
                                "loss_pct":     loss_pct,
                                "shares":       shares,
                                "fill_price":   result["fill_price"],
                                "proceeds":     proceeds,
                                "realized_pnl": realized,
                            })
                            log.info(f"  📊 P&L: ${realized:,.0f}  Freed: ${proceeds:,.0f}")
                            _progress(ticker=ticker, stage="sold (stop loss)",
                                      result={"ticker": ticker, "status": "sold_stop_loss",
                                              "shares": shares, "proceeds": proceeds})
                        else:
                            log.error(f"  ❌ Sale FAILED for {ticker} — MANUAL ACTION REQUIRED")
                            _progress(ticker=ticker, stage="sale FAILED",
                                      result={"ticker": ticker, "status": "sell_failed"})
                        continue
                    else:
                        log.info(
                            f"  ✅ {ticker}: price ${current_price:.2f}  "
                            f"({loss_pct:.1f}% below strike — within {stop_loss_pct*100:.0f}% threshold)"
                            if current_price < assigned_strike else
                            f"  ✅ {ticker}: price ${current_price:.2f} (above assigned strike)"
                        )

            log.info(f"  ✅ {ticker}: stop-loss check passed — checking earnings")

            # ── Step 2: Earnings check ────────────────────────
            # Skipped entirely when wheel_cc_ignore_earnings_filter is True.
            if not cc_ignore_earnings:
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
                                                   assigned_strike=assigned_strike, dry_run=orders_dry_run)
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
            else:
                ticker_info      = candidate_info.get(ticker, {})
                days_to_earnings = ticker_info.get("days_to_earnings")
                if days_to_earnings is not None:
                    log.info(f"  ⚠️  {ticker}: earnings in {days_to_earnings} day(s) — "
                             f"ignored (wheel_cc_ignore_earnings_filter=true)")

            log.info(f"  ✅ {ticker}: earnings check passed — querying option chain")
            _progress(ticker=ticker, stage="scanning call strikes")

            # ── Step 3: Find best CC strike ───────────────────
            # Floor at net_cost (premium-netted breakeven) — a CC at/above our net
            # cost can't lock an economic loss even if called away.
            cc_info = _find_cc_strike(ib, ticker, expiry, net_cost,
                                      allow_below_assigned=allow_cc_below_assigned)

            # ── Step 4: Decision ──────────────────────────────
            # CC_NO_DATA means _find_cc_strike couldn't price any call — the
            # specific reason (market closed during a weekend preview, an
            # unlisted/holiday expiry, no chain, or a live data gap) is already
            # logged above by _find_cc_strike. Don't sell shares on missing
            # data: defer the CC decision and keep the shares either way.
            if cc_info == CC_NO_DATA:
                log.info(f"  ⏳ {ticker}: CC could not be priced "
                         f"(see reason above) — deferring, keeping shares")
                wheel_activity.append({
                    "ticker": ticker,
                    "action": "cc_deferred",
                    "shares": shares,
                })
                h["cc_status"] = "pending"
                _progress(ticker=ticker, stage="CC deferred (no data)",
                          result={"ticker": ticker, "status": "cc_deferred"})
                continue

            if cc_info is None:
                log.warning(f"  ❌ {ticker}: no call strike with delta ≥ "
                             f"{CC_DELTA_MIN:.2f} — selling shares")
                result = _sell_stock_market(ib, ticker, shares, "no_viable_cc",
                                               assigned_strike=assigned_strike, dry_run=orders_dry_run)
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
                    _progress(ticker=ticker, stage="sold (no viable CC)",
                              result={"ticker": ticker, "status": "sold_no_viable_cc",
                                      "shares": shares, "proceeds": proceeds})
                else:
                    log.error(f"  ❌ Sale FAILED for {ticker} — MANUAL ACTION REQUIRED")
                    _progress(ticker=ticker, stage="sale FAILED",
                              result={"ticker": ticker, "status": "sell_failed"})
                continue

            cc_strike, cc_delta, cc_mid, cc_iv, cc_stock_price = cc_info
            below_assigned = assigned_strike > 0 and cc_strike < assigned_strike
            mid_display = f"${cc_mid:.2f}" if cc_mid else "?"
            log.info(f"  🎯 Selling CC: ${cc_strike:.2f} strike  "
                     f"delta={cc_delta:.3f}  mid={mid_display}")
            if below_assigned:
                locked_loss = round((cc_strike - assigned_strike) * shares, 2)
                log.warning(f"  ⚠️  {ticker}: CC strike ${cc_strike:.2f} is BELOW "
                            f"assigned ${assigned_strike:.2f} — if called away, "
                            f"locks ${locked_loss:,.0f} (kept shares + premium instead of force-sell)")

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
            _progress(ticker=ticker, stage=f"selling CC ${cc_strike:.0f} (δ{cc_delta:.2f})")
            order_result = _sell_cc_with_escalation(
                ib, qualified[0], shares, ticker, cc_strike, ref_mid, dry_run=orders_dry_run
            )

            if order_result["status"] in ("filled", "partial_fill"):
                prem = order_result["premium_collected"]
                needed = shares // 100
                filled = int(order_result.get("filled_contracts") or needed)
                cc_premium             += prem
                # Record real coverage: a partial fill leaves shares uncovered, so
                # mark "partial" (not "open") and store the true filled count.
                status = _set_cc_coverage(
                    h, strike=cc_strike, expiry=expiry, premium=prem,
                    covered=filled, needed=needed)
                partial = status == "partial"
                wheel_activity.append({
                    "ticker":          ticker,
                    "action":          "cc_partial" if partial else "cc_opened",
                    "cc_strike":       cc_strike,
                    "cc_delta":        round(cc_delta, 3),
                    "cc_premium":      prem,
                    "cc_expiry":       expiry,
                    "contracts":       filled,
                    "needed":          needed,
                    "below_assigned":  below_assigned,
                    "assigned_strike": round(assigned_strike, 2) if assigned_strike else None,
                    "shares":          shares,
                })
                _progress(ticker=ticker, stage=f"CC {'partial ' if partial else ''}filled {filled}/{needed}",
                          result={"ticker": ticker,
                                  "status": "cc_partial" if partial else "cc_opened",
                                  "cc_strike": cc_strike, "cc_premium": prem,
                                  "contracts": filled, "needed": needed,
                                  "cc_delta": round(cc_delta, 3), "cc_expiry": expiry})
                if partial:
                    log.warning(f"  ⚠️  {ticker}: CC partial fill {filled}/{needed} — "
                                f"{needed - filled} contracts still uncovered")
                log.info(f"  💰 CC premium: ${prem:,.0f}")
                # Capture execution metadata for dashboard enrichment
                fill_price = order_result.get("fill_price")
                buffer_pct = (
                    round(((cc_stock_price - cc_strike) / cc_stock_price) * 100, 2)
                    if cc_stock_price and cc_stock_price > 0 else None
                )
                # Gate on orders_dry_run (not the pipeline dry_run) so a live run
                # with the Settings "Dry Run" toggle ON — which only simulates the
                # order — does not record a phantom fill in trade_log.json.
                if not orders_dry_run:
                    try:
                        _append_trade_log({
                            "symbol":               ticker,
                            "expiry":               expiry,
                            "strike":               float(cc_strike),
                            "right":                "C",
                            "entry_date":           datetime.now().isoformat(),
                            "delta_at_entry":       round(cc_delta, 4),
                            "iv_at_entry":          round(cc_iv, 4) if cc_iv is not None else None,
                            "buffer_pct_at_entry":  buffer_pct,
                            "premium_per_contract": fill_price,
                            "contracts":            filled,
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
                _progress(ticker=ticker, stage="CC order failed",
                          result={"ticker": ticker, "status": "cc_failed", "cc_strike": cc_strike})

    finally:
        ib.disconnect()

    # ── Step 5: Persist to state.json ────────────────────────
    reserved_capital   = round(sum(
        h.get("shares", 0) * h.get("assigned_strike", 0.0)
        for h in holdings if h.get("shares", 0) > 0
    ), 2)
    active_wheel_count = sum(1 for h in holdings if h.get("shares", 0) > 0)
    # Tickers we already hold shares of — so the CSP pipeline can optionally avoid
    # opening a SECOND position (a new tranche) on the same name (#82).
    held_tickers       = sorted({h["ticker"] for h in holdings if h.get("shares", 0) > 0})

    monday_context = {
        "skip_tickers":           skip_tickers,
        "freed_capital":          freed_capital,
        "cc_premium":             cc_premium,
        "shares_sold_pnl":        shares_sold_pnl,
        "wheel_activity":         wheel_activity,
        "reserved_capital":       reserved_capital,
        "active_wheel_count":     active_wheel_count,
        "held_tickers":           held_tickers,
        "open_short_put_tickers": sorted(open_short_put_tickers),
        "updated":                datetime.now().isoformat()
    }
    if not dry_run:
        state["wheel_holdings"] = holdings
        state["monday_context"] = monday_context
        _save_state(state)
    else:
        log.info("  🟡 [DRY RUN] state.json NOT written — preview only")

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

    return {
        "dry_run":                dry_run,
        "freed_capital":          freed_capital,
        "skip_tickers":           skip_tickers,
        "reserved_capital":       reserved_capital,
        "cc_premium":             cc_premium,
        "shares_sold_pnl":        shares_sold_pnl,
        "active_wheel_count":     active_wheel_count,
        "wheel_activity":         wheel_activity,
        "held_tickers":           held_tickers,
        "open_short_put_tickers": sorted(open_short_put_tickers),
    }


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "detect":
        detect_assignments()
    else:
        dry = "--dry-run" in sys.argv or "dry" in sys.argv[2:]
        r = run_wheel_check(dry_run=dry)
        print(f"\n[{'DRY RUN' if dry else 'LIVE'}]  Freed: ${r['freed_capital']:,.0f}  "
              f"Skip: {r['skip_tickers']}  Reserved: ${r['reserved_capital']:,.0f}  "
              f"CC premium: ${r['cc_premium']:,.0f}")
