import json
import logging
import math
import os
import shutil
import time
from datetime import datetime, timezone, timedelta
from typing import NamedTuple
from ib_insync import IB, Option, Stock, LimitOrder, MarketOrder, ExecutionFilter

from config import IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID, ACCOUNT, NUM_POSITIONS, TOTAL_FUND_BUDGET, MAX_PER_POSITION, DRY_RUN, get_settings, ACCOUNT_TYPE, connect_with_retry, connect_deadline_sec

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
CANCEL_CONFIRM_SECS = 15    # how long to wait for a cancel to reach a terminal state
CANCEL_POLL_SECS    = 0.5

# An order in one of these states can no longer fill, so its filled quantity is
# final and it is safe to place the next escalation leg.
_TERMINAL_ORDER_STATES = ("Filled", "Cancelled", "ApiCancelled", "Inactive")

# Statuses that must stop the escalation ladder rather than fall through to the
# next leg.
_ABORT_ESCALATION_STATUSES = ("failed_permissions", "failed_funds", "cancel_unconfirmed")


class GatewayUnavailable(Exception):
    """The IBKR connection died — this is infrastructure, not a strategy decision.

    Raised instead of returning None so a dead socket can never be reported as a
    trading outcome. On 2026-08-17 the gateway wedged mid-pipeline and every
    remaining candidate came back as "skipped_delta" — nine tickers logged as if
    their deltas were out of range, in the same millisecond, while the real cause
    was `Socket disconnect`. Worse, swallowing the exception ALSO bypassed the
    reconnect handler in execute_positions, so the run marched on against a dead
    socket instead of reconnecting or stopping.
    """


# Substrings that identify a transport failure rather than a bad contract.
# ib_insync surfaces these as plain Exceptions, so matching text is the only
# option; the isConnected() check below is the primary signal.
_CONN_ERROR_MARKERS = (
    "not connected",
    "socket disconnect",
    "connection reset",
    "connection refused",
    "broken pipe",
    "peer closed connection",
    "timeouterror",
)


def _is_connection_error(ib: IB, exc: Exception) -> bool:
    """True when exc means 'the gateway went away', not 'this contract is bad'."""
    if not ib.isConnected():
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(m in text for m in _CONN_ERROR_MARKERS)


def _discord_alert(message: str) -> None:
    """Plain-text Discord alert (mirrors monday_runner._discord_alert). Defined
    locally rather than imported to keep trader.py free of a monday_runner cycle.
    No-op when Discord is disabled or unconfigured; never raises."""
    try:
        if not get_settings().get("discord_webhook_enabled", True):
            return
        from secrets_client import get_secret
        webhook = get_secret("discord_webhook_url", "DISCORD_WEBHOOK_URL")
        if not webhook:
            return
        import requests
        requests.post(webhook, json={"content": message}, timeout=5)
    except Exception as e:
        log.warning(f"  ⚠️  Discord alert failed: {e}")


def _broker_fill_for(ib: IB, symbol: str, expiry: str, strike: float) -> tuple[int, float] | None:
    """Return (contracts_sold, avg_price) for a short put from IBKR's execution
    record, or None if no matching SLD executions exist."""
    try:
        fills = ib.reqExecutions(ExecutionFilter())
    except Exception as e:
        log.warning(f"  ⚠️  could not fetch executions for {symbol}: {e}")
        return None
    qty, notional = 0.0, 0.0
    for f in fills:
        c, ex = f.contract, f.execution
        if (c.symbol == symbol and c.right == "P"
                and c.lastTradeDateOrContractMonth == expiry
                and float(c.strike) == float(strike)
                and ex.side == "SLD"):
            qty      += float(ex.shares)
            notional += float(ex.shares) * float(ex.price)
    if qty <= 0:
        return None
    return int(qty), round(notional / qty, 4)


def _reconcile_results_against_broker(ib: IB, results: list, attempted: dict) -> list:
    """Check this run's recorded outcomes against what IBKR actually has.

    Why this exists: on 2026-08-17 the gateway died mid-escalation on BE. The
    cancel of the working limit order was issued but never confirmed, so the
    order stayed live at IBKR and filled six minutes later — while the run had
    already recorded "failed — order unfilled". The result was an untracked short
    put: $570 of premium missing from the week and $43,000 of collateral invisible
    to the next sizing pass, which then over-deployed past the net-liq cap.

    Two checks, both read-only — this NEVER places, cancels or modifies an order:
      1. Working orders still live at IBKR for a ticker we did not record as
         filled. This is the early-warning case: it catches BE's order while it
         is still open, before it fills.
      2. Short put positions matching a contract this run attempted but recorded
         as anything other than filled. That is a fill we missed; we pull the real
         price from the execution record and correct the result in place.

    Only contracts THIS run attempted are considered, matched on
    symbol+expiry+strike, so open CSPs from previous weeks are never touched.

    Discrepancies are alerted, not just logged — a silent auto-repair would hide
    that the gateway is dying mid-run. Never raises: a reconcile that can abort a
    run after orders are placed would be worse than the drift it detects.
    """
    discrepancies: list = []
    own_connection = False   # True when WE reconnected, so we must close it here:
                             # the caller still holds the dead handle and its
                             # disconnect() would leave this client id open.
    try:
        if not ib.isConnected():
            log.warning("  ⚠️  Broker reconcile — not connected; attempting one reconnect")
            try:
                ib = _reconnect(ib)
                own_connection = True
            except Exception as e:
                msg = (f"⚠️ **YRVI** Could not verify this run against IBKR — the gateway "
                       f"is unreachable ({e}). Orders may have filled after the run gave up. "
                       f"CHECK OPEN ORDERS AND POSITIONS MANUALLY.")
                log.error(f"  ❌ {msg}")
                _discord_alert(msg)
                return [{"kind": "unverified", "detail": str(e)}]

        recorded_filled = {
            r["ticker"] for r in results
            if r.get("status") in ("filled", "partial_fill", "dry_run")
        }

        # ── 1. Working orders we don't think we have ──────────────────
        try:
            live_states = {"PendingSubmit", "PreSubmitted", "Submitted", "PendingCancel", "ApiPending"}
            for t in ib.reqAllOpenOrders():
                c = t.contract
                if c.symbol in attempted and t.orderStatus.status in live_states:
                    detail = (f"{c.symbol} {getattr(c, 'right', '')}{getattr(c, 'strike', '')} "
                              f"x{t.order.totalQuantity} {t.order.action} — "
                              f"status {t.orderStatus.status}")
                    log.error(f"  ❗ ORDER STILL LIVE AT IBKR: {detail}")
                    discrepancies.append({"kind": "order_still_live", "ticker": c.symbol,
                                          "detail": detail})
        except Exception as e:
            log.warning(f"  ⚠️  open-order check failed: {e}")

        # ── 2. Positions we hold but recorded as not filled ───────────
        try:
            for p in ib.positions():
                c = p.contract
                if c.secType != "OPT" or getattr(c, "right", "") != "P" or p.position >= 0:
                    continue
                info = attempted.get(c.symbol)
                if not info:
                    continue
                if (c.lastTradeDateOrContractMonth != info["expiry"]
                        or float(c.strike) != float(info["strike"])):
                    continue
                if c.symbol in recorded_filled:
                    continue

                qty = int(abs(p.position))
                fill = _broker_fill_for(ib, c.symbol, info["expiry"], info["strike"])
                price = fill[1] if fill else None
                premium = round(price * qty * 100, 2) if price else 0.0
                detail = (f"{c.symbol} ${info['strike']:.2f}P x{qty}"
                          + (f" @ ${price:.2f} — ${premium:,.0f}" if price else " (price unknown)"))
                log.error(f"  ❗ UNRECORDED FILL AT IBKR: {detail}")

                for r in results:
                    if r.get("ticker") == c.symbol:
                        r.update({
                            "status":             "filled",
                            "contracts":          qty,
                            "filled_contracts":   qty,
                            "fill_price":         price,
                            "premium_collected":  premium,
                            "order_type":         "recovered_from_broker",
                            "simulated":          False,
                            "delta_at_entry":     info.get("delta_at_entry"),
                            "iv_at_entry":        info.get("iv_at_entry"),
                            "exec_timestamp":     datetime.now(timezone.utc).isoformat(),
                            "recovered_from_broker": True,
                            "recovery_note": ("recorded as not filled by the run; found "
                                              "open at IBKR during post-run reconcile"),
                        })
                        break
                discrepancies.append({"kind": "unrecorded_fill", "ticker": c.symbol,
                                      "contracts": qty, "fill_price": price,
                                      "premium_collected": premium, "detail": detail})
        except Exception as e:
            log.warning(f"  ⚠️  position check failed: {e}")

    except Exception as e:                       # belt and braces — never fatal
        log.warning(f"  ⚠️  broker reconcile failed: {e}")
        return discrepancies
    finally:
        if own_connection:
            try:
                ib.disconnect()
            except Exception:
                pass

    if discrepancies:
        lines = "\n".join(f"• {d.get('detail', d.get('kind'))}" for d in discrepancies)
        _discord_alert(
            f"⚠️ **YRVI** Post-run broker reconcile found {len(discrepancies)} "
            f"discrepancy(ies) between the run and IBKR:\n{lines}\n"
            f"Unrecorded fills have been corrected in state.json. "
            f"Any order still live at IBKR needs a manual decision."
        )
    else:
        log.info("  ✅ Broker reconcile — run matches IBKR")
    return discrepancies


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


def _same_week(iso_ts: str | None) -> bool:
    """True if `iso_ts` (naive-local isoformat, as stored in state.run_date) falls
    in the current Monday-anchored week. Used to decide whether a prior state's
    executions belong to THIS week and should be consolidated into a re-run."""
    if not iso_ts:
        return False
    try:
        d = datetime.fromisoformat(iso_ts)
    except (TypeError, ValueError):
        return False
    now = datetime.now(d.tzinfo) if d.tzinfo else datetime.now()
    monday = lambda x: (x - timedelta(days=x.weekday())).date()
    return monday(d) == monday(now)


def _merge_week_executions(existing: dict, new_results: list, new_positions: list):
    """Consolidate a same-week re-run. A live Run Now overwrote state.executions
    with only the latest run's results, so after a re-run the dashboard week view,
    filled_count, total_premium, and the Discord 'This Week's Trades' section
    showed just the new fills and dropped the ones opened earlier this week — even
    though weekly_pnl (summed from the durable trade_log) stayed correct. Here we
    UNION this run's results with the still-open fills already recorded this week.

    Safe against double-counting: the pipeline folds open short puts into
    skip_tickers (open_short_put_tickers), so a carried fill is never also
    re-executed. A new FILL for a ticker supersedes a prior one; a new non-fill
    (skip/fail) never displaces a prior fill for the same ticker. Prior-WEEK state
    is dropped (fresh week starts clean).

    Returns (executions, positions, filled_count, total_premium).
    """
    _FILLED = ("filled", "partial_fill", "dry_run")

    by_ticker: dict = {}
    order: list = []

    def _put(e):
        t = e.get("ticker")
        if t not in by_ticker:
            order.append(t)
        by_ticker[t] = e

    if _same_week(existing.get("run_date")):
        for e in existing.get("executions", []):
            if e.get("status") in _FILLED:
                _put(e)   # carry this week's already-open fills first

    for e in new_results:
        t = e.get("ticker")
        # A real new fill overrides; a skip/fail only fills an empty slot so it
        # can't wipe out a fill that this same ticker already produced this week.
        if e.get("status") in _FILLED or t not in by_ticker:
            _put(e)

    merged_execs = [by_ticker[t] for t in order]

    new_pos_tickers = {p.get("ticker") for p in new_positions}
    carried_pos = [p for p in existing.get("positions", [])
                   if p.get("ticker") in by_ticker and p.get("ticker") not in new_pos_tickers]
    merged_positions = list(new_positions) + carried_pos

    filled = [e for e in merged_execs if e.get("status") in _FILLED]
    total_premium = round(sum(e.get("premium_collected", 0) or 0 for e in filled), 2)
    return merged_execs, merged_positions, len(filled), total_premium


def connect() -> IB:
    log.info(f"🔌 Connecting to IB Gateway {IBKR_HOST}:{IBKR_PORT} ({ACCOUNT_TYPE}, clientId={IBKR_CLIENT_ID})")

    def _attempt() -> IB:
        ib = IB()
        ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID)
        ib.reqMarketDataType(3)  # 1=live, 2=frozen, 3=delayed, 4=delayed-frozen
        log.info(f"✅ Connected to IBKR — Account: {ib.managedAccounts()}")
        _wait_for_usopt(ib)
        return ib

    # Step B of the Monday run — same reasoning as the wheel check: the week's CSPs
    # are worth waiting out a wedge for, as long as the market is still open.
    return connect_with_retry(_attempt, IBKR_HOST, IBKR_PORT, log,
                              deadline_sec=connect_deadline_sec())


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


class StrikeProbe(NamedTuple):
    """One strike's execution-relevant snapshot. Any field may be None if IBKR
    had no data for it within the request window."""
    delta: float | None
    iv:    float | None
    oi:    float | None
    bid:   float | None


def _probe_strike(ib: IB, contract) -> StrikeProbe:
    """Delta, implied vol, open interest and bid for one strike, from a SINGLE
    market-data request.

    Open interest (tick 101) and the bid ride along with the greeks at no extra
    cost — measured live, both populate at ~1s while delta is the slowest tick,
    so the existing 3s window already covers them. Carrying them here is what
    lets the chain scan judge a strike on what actually decides the trade
    (liquidity and yield) at the moment it picks, rather than one gate later.
    """
    tkr = ib.reqMktData(contract, genericTickList="106,101", snapshot=False)
    ib.sleep(3)
    ib.cancelMktData(contract)
    ib.sleep(0.5)

    def _clean(v):
        return float(v) if (v is not None and not is_nan(v) and v > 0) else None

    oi, bid = _clean(tkr.putOpenInterest), _clean(tkr.bid)
    for greeks in (tkr.modelGreeks, tkr.lastGreeks, tkr.bidGreeks, tkr.askGreeks):
        if greeks is not None and not is_nan(greeks.delta):
            iv = getattr(greeks, "impliedVol", None)
            iv = iv if (iv is not None and not is_nan(iv)) else None
            return StrikeProbe(greeks.delta, iv, oi, bid)
    return StrikeProbe(None, None, oi, bid)


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


def _fetch_available_cash(ib: IB) -> float | None:
    """Live settled cash available to secure new cash-secured puts, straight from
    IBKR (the source of truth). Returns None on any failure so the caller leaves
    the funds gate OFF and falls back to IBKR's own order rejection — a bad read
    must never block trading. BuyingPower on a cash/Roth account is real settled
    cash already net of what existing positions reserve, which is exactly the
    ceiling for new puts; AvailableFunds / TotalCashValue are fallbacks."""
    try:
        summary = ib.accountSummary(ACCOUNT)
        by_tag  = {v.tag: v.value for v in summary}
        for tag in ("BuyingPower", "AvailableFunds", "TotalCashValue"):
            val = by_tag.get(tag)
            if val not in (None, ""):
                return float(val)
    except Exception as e:
        log.warning(f"  ⚠️  Funds gate: could not read available cash ({e}) — gate disabled this run")
    return None


def _strike_shortfall(oi: float | None, bid: float | None, strike: float) -> str | None:
    """Is this strike worth writing? Returns None if yes, else a short reason.

    Two tests, both mirroring thresholds the rest of the system already uses:
      - OI notional (OI × strike × 100) against min_oi_notional / min_oi_floor,
        the same gate check_liquidity applies at the end of the funnel
      - bid yield (bid / strike) against min_bid_yield_pct — the fund's 1% bar

    The yield test matters because the scan walks DOWNWARD: every step further
    OTM buys a lower delta with a smaller premium. Without it, an OI-aware scan
    happily trades away yield to find a liquid strike and writes a put well
    under the 1% bar. On 2026-08-24 IONQ would have been written at 0.66%.

    Unknown OI or bid (IBKR returned no tick) is treated as PASS — a missing
    tick must never push the scan past an otherwise good strike. Those fall
    through to check_liquidity, where a real skip belongs.

    Thresholds hot-reload from settings.json on every call.
    """
    s = get_settings()
    if oi is not None:
        if oi < s.get("min_oi_floor", MIN_OI_FLOOR):
            return f"OI {oi:.0f} below floor"
        notional = oi * strike * 100
        if notional < s.get("min_oi_notional", MIN_OI_NOTIONAL):
            return f"OI {oi:.0f} = ${notional:,.0f} notional"
    if bid is not None:
        yld = bid / strike if strike > 0 else 0
        if yld < s.get("min_bid_yield_pct", MIN_BID_YIELD_PCT):
            return f"bid ${bid:.2f} = {yld * 100:.2f}% yield"
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
        if _is_connection_error(ib, e):
            raise GatewayUnavailable(f"{ticker} delta-check qualify: {e}") from e
        log.error(f"  ❌ {ticker} delta-check qualify error: {e}")
        return None

    probe = _probe_strike(ib, c)
    live_delta, live_iv = probe.delta, probe.iv

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
        if _is_connection_error(ib, e):
            raise GatewayUnavailable(f"{ticker} stock qualify for chain lookup: {e}") from e
        log.error(f"  ❌ {ticker} stock qualify error for chain lookup: {e}")
        return None

    chains = ib.reqSecDefOptParams(ticker, "", "STK", und_con_id)
    ib.sleep(1)

    def _scan_strikes(candidates: list, label: str) -> tuple | None:
        """Return the best strike among `candidates`, preferring one that is
        actually worth writing.

        Two tiers, in order:
          1. delta within cap AND clears the OI floor AND pays the 1% bid yield
          2. otherwise: the first strike within the delta cap — byte-for-byte
             the old behaviour, left to check_liquidity to accept or skip

        Candidates arrive highest-strike-first in both scan directions, so the
        tier-1 winner is also the most premium among strikes worth writing.

        Why tier 1 exists: selecting on delta alone kept landing on dead
        off-round strikes one increment from a deep one — on 2026-08-24 MRNA
        took $124 (OI 42) with $120 (OI 3698) four dollars below, and the name
        was skipped for $0 rather than written slightly deeper.

        Why tier 2 is a plain delta match and NOT a relaxed tier-1: anything
        smarter changes which strike gets picked when tier 1 finds nothing, and
        every such "improvement" trades yield for fillability. Falling back to
        exactly what the old scan chose means this can only ever turn a skip
        into a fill, never a good fill into a worse one.
        """
        delta_only: tuple | None = None
        for alt_strike in candidates:
            alt_c = Option(ticker, expiry, alt_strike, "P", "SMART", currency="USD")
            try:
                alt_q = ib.qualifyContracts(alt_c)
                if not alt_q:
                    continue
                alt_c = alt_q[0]
            except Exception as e:
                # A dead socket would otherwise fail all 15 strikes in a row and
                # look like "no strike has an acceptable delta".
                if _is_connection_error(ib, e):
                    raise GatewayUnavailable(f"{ticker} chain scan at ${alt_strike:.2f}: {e}") from e
                continue
            alt = _probe_strike(ib, alt_c)
            if alt.delta is None or abs(alt.delta) > MAX_DELTA:
                continue
            candidate = (alt_c, alt_strike, orig_delta, alt.delta, alt.iv, True)
            shortfall = _strike_shortfall(alt.oi, alt.bid, alt_strike)
            if shortfall is None:
                log.warning(f"  {label} {ticker} strike adjusted ${screener_strike:.2f} → "
                            f"${alt_strike:.2f} (delta {orig_delta:.3f} → {alt.delta:.3f}"
                            f"{f', OI {alt.oi:.0f}' if alt.oi is not None else ''}"
                            f"{f', {alt.bid / alt_strike * 100:.2f}% yield' if alt.bid else ''})")
                return candidate
            if delta_only is None:
                delta_only = candidate      # preserve the old pick as the fallback
            log.info(f"  ↷ {ticker} ${alt_strike:.2f} delta {alt.delta:.3f} OK but "
                     f"{shortfall} — looking deeper")
        if delta_only is not None:
            log.warning(f"  {label} {ticker} no strike clears delta, open interest AND yield "
                        f"— falling back to ${delta_only[1]:.2f} (delta {delta_only[3]:.3f}); "
                        f"liquidity gate decides")
        return delta_only

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


def get_market_data(ib: IB, contract, screener_premium: float,
                    dry_run: bool = False) -> dict | None:
    """
    Request delayed market data (type 3 — no subscription needed).
    Falls back to screener premium if market is closed.

    dry_run: caller passes the effective Dry Run state so a closed-market
    simulation only happens when we're actually simulating orders.
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
        if dry_run:
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
                                 mkt: dict, ticker: str,
                                 dry_run: bool = False) -> dict:
    result = {
        "ticker": ticker, "contracts": contracts,
        "status": "unfilled", "fill_price": None,
        "order_type": None, "premium_collected": 0,
        "simulated": mkt.get("simulated", False),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    if dry_run:
        tag = " (simulated data)" if mkt.get("simulated") else " (live data)"
        log.info(f"  🧪 DRY RUN{tag} — would sell {contracts}x {ticker} "
                 f"put @ mid ${mkt['mid']:.2f}")
        result.update({
            "status": "dry_run",
            "fill_price": mkt["mid"],
            "order_type": "limit_mid",
            "premium_collected": round(contracts * mkt["mid"] * 100, 2),
            "filled_contracts": contracts,
            "exec_timestamp": datetime.now(timezone.utc).isoformat()
        })
        return result

    # Cumulative fill accounting across escalation legs, so each leg orders only
    # the still-unfilled remainder and no partial fill is lost — prevents the
    # market leg from re-sending the full quantity (over-sell) — #72.
    filled_total  = 0
    premium_total = 0.0

    def _remaining() -> int:
        return contracts - filled_total

    def _record_leg(trade, label: str) -> None:
        nonlocal filled_total, premium_total
        leg_filled = int(trade.orderStatus.filled or 0)
        if leg_filled > 0:
            fill = trade.orderStatus.avgFillPrice or 0.0
            premium_total += leg_filled * fill * 100
            filled_total  += leg_filled
            log.info(f"  ✅ {label}: filled {leg_filled}x {ticker} PUT @ ${fill:.2f} "
                     f"({filled_total}/{contracts} total)")

    def _finalize(status: str) -> dict:
        avg = round(premium_total / filled_total / 100, 2) if filled_total else None
        result.update({
            "status": status, "fill_price": avg, "order_type": "escalation",
            "premium_collected": round(premium_total, 2),
            "filled_contracts": filled_total,
            "exec_timestamp": datetime.now(timezone.utc).isoformat(),
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

    def _is_terminal(trade) -> bool:
        """True when the order can no longer fill, so its quantity is final."""
        if trade.orderStatus.status in _TERMINAL_ORDER_STATES:
            return True
        # A fully-filled order sometimes reports remaining==0 before the status
        # flips to Filled.
        return (trade.orderStatus.remaining or 0) == 0 and (trade.orderStatus.filled or 0) > 0

    def _await_terminal(trade, timeout: float) -> bool:
        """Poll until the order can no longer fill. True if it settled in time.

        Returning False means the order may STILL BE WORKING at IBKR, so its
        filled quantity is not final and placing another leg for the 'remaining'
        contracts could sell the same position twice.
        """
        waited = 0.0
        while True:
            if _is_terminal(trade):
                return True
            if waited >= timeout:
                return False
            if not ib.isConnected():
                # No point polling a dead socket — this is exactly the BE case:
                # the cancel was sent, the gateway died, the order stayed live.
                return False
            ib.sleep(CANCEL_POLL_SECS)
            waited += CANCEL_POLL_SECS

    def _abort_unconfirmed(trade, label: str) -> None:
        """Record what filled and stop the ladder — the cancel never confirmed."""
        _record_leg(trade, label)
        st = trade.orderStatus.status
        log.error(
            f"  ❗ {ticker} — {label} CANCEL NOT CONFIRMED after {CANCEL_CONFIRM_SECS}s "
            f"(status {st}, filled {int(trade.orderStatus.filled or 0)}/{contracts}). "
            f"The order may still be working at IBKR — NOT escalating, because "
            f"selling the remainder now could double the position."
        )
        _finalize("cancel_unconfirmed")
        result["cancel_unconfirmed"] = {
            "leg": label,
            "order_status": st,
            "order_id": getattr(trade.order, "orderId", None),
            "perm_id": getattr(trade.order, "permId", None),
        }
        _discord_alert(
            f"❗ **YRVI** {ticker} — the {label} cancel was not confirmed (status `{st}`). "
            f"Escalation was stopped so the position can't be sold twice. "
            f"The order may still be working at IBKR — **check open orders**. "
            f"Filled so far: {filled_total}/{contracts}."
        )

    def try_limit(price: float, label: str, wait: int) -> bool:
        qty = _remaining()
        if qty < 1:
            return True
        log.info(f"  📤 {label}: SELL {qty}x {ticker} PUT @ ${price:.2f}")
        order = LimitOrder("SELL", qty, price, account=ACCOUNT, tif="DAY")
        trade = ib.placeOrder(contract, order)
        # Quick early-exit: IBKR permission rejections (Error 201) appear within seconds
        ib.sleep(3)
        if _is_permission_error(trade):
            log.error(f"  ❌ {ticker} — IBKR rejected: no options trading permissions (Error 201)")
            result["status"] = "failed_permissions"
            return False
        ib.sleep(wait - 3)
        if trade.orderStatus.status != "Filled":
            # Not fully filled — cancel the remainder, then record whatever DID fill.
            #
            # The cancel must be CONFIRMED before the next leg goes out. This used
            # to be `cancelOrder(...)` followed by a flat `ib.sleep(1)`, which is a
            # race: on 2026-08-17 the BE cancel was sent at 10:04:20, the gateway
            # died before IBKR acknowledged it, the order stayed live and filled at
            # 10:10:17 — six minutes after the run had written it off as unfilled.
            # We got lucky that the gateway also killed the market leg; had it died
            # a moment later we would have sold the position twice.
            #
            # A cancel normally confirms in well under a second (PendingCancel →
            # Cancelled took ~650ms on that same run), so the 15s ceiling only
            # trips when something is genuinely wrong.
            log.info(f"  ⏳ {label} not fully filled — cancelling remainder, escalating...")
            try:
                ib.cancelOrder(trade.order)
            except Exception as e:
                if _is_connection_error(ib, e):
                    _abort_unconfirmed(trade, label)
                    return False
                raise
            if not _await_terminal(trade, CANCEL_CONFIRM_SECS):
                # Under-filling is recoverable — the slot is simply not taken and
                # the next run can use it. Double-selling is not. Stop here.
                _abort_unconfirmed(trade, label)
                return False
        _record_leg(trade, label)
        return _remaining() < 1

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
        if try_limit(mkt["mid"], "limit_mid", MID_WAIT_SECS): return _finalize("filled")
        if result.get("status") in _ABORT_ESCALATION_STATUSES: return result
    if try_limit(mkt["bid"], "limit_bid", BID_WAIT_SECS): return _finalize("filled")
    if result.get("status") in _ABORT_ESCALATION_STATUSES: return result

    # Market order for the REMAINING contracts only (never the full quantity) so a
    # partial fill on a cancelled limit leg can't lead to an over-sell — #72.
    qty = _remaining()
    if qty >= 1:
        log.info(f"  📤 Market order: SELL {qty}x {ticker} PUT")
        trade = ib.placeOrder(contract, MarketOrder("SELL", qty, account=ACCOUNT, tif="DAY"))
        elapsed = 0
        while elapsed < MARKET_WAIT_SECS:
            ib.sleep(MARKET_POLL_SECS)
            elapsed += MARKET_POLL_SECS
            status      = trade.orderStatus.status
            filled_qty  = trade.orderStatus.filled
            remaining   = trade.orderStatus.remaining
            if status == "Filled" or (remaining == 0 and filled_qty > 0):
                break
            if _is_permission_error(trade):
                log.error(f"  ❌ {ticker} — IBKR rejected: no options trading permissions (Error 201)")
                _record_leg(trade, "market")
                result["status"] = "failed_permissions"
                return result
            if result.get("status") == "failed_funds":
                _record_leg(trade, "market")
                return result
            if status == "PartiallyFilled" and filled_qty > 0:
                log.info(f"  ⏳ Partial: {int(filled_qty)}/{qty} filled after {elapsed}s — waiting...")
            else:
                log.info(f"  ⏳ Market status: {status} after {elapsed}s — waiting...")
        # Nothing is placed after the market leg, so an unsettled order here can't
        # cause an over-sell — but it CAN still fill after we record it, which is
        # how BE's premium went missing. Leave a breadcrumb so the post-run broker
        # reconcile and the Discord card both flag it instead of reporting a clean
        # miss.
        if not _is_terminal(trade):
            log.warning(f"  ⚠️  {ticker} — market order still {trade.orderStatus.status} after "
                        f"{MARKET_WAIT_SECS}s; recorded fill may be incomplete and the order "
                        f"can still fill at IBKR")
            result["market_order_unconfirmed"] = {
                "order_status": trade.orderStatus.status,
                "order_id": getattr(trade.order, "orderId", None),
            }
        _record_leg(trade, "market")

    if filled_total >= contracts:
        return _finalize("filled")
    if filled_total > 0:
        log.warning(f"  ⚠️  {ticker}: partial CSP fill {filled_total}/{contracts} "
                    f"across legs — accepted")
        return _finalize("partial_fill")
    log.error(f"  ❌ Could not fill {ticker} — manual review needed")
    return _finalize("failed")


def execute_positions(sized_positions: list, extra_targets: list = None,
                      target_fills: int = None, status_callback=None,
                      dry_run: bool = None, budget: float = None) -> list:
    """
    Execute up to target_fills fills (defaults to NUM_POSITIONS). If a candidate
    fails qualification, market data, or liquidity, the next-ranked screener target
    is sized and attempted automatically until the fill target is met or candidates
    are exhausted.

    extra_targets: full ranked screener list (raw dicts from screener).
    target_fills: how many CSP fills to seek (caller reduces by active wheel count).
    dry_run: simulate orders instead of placing them. Defaults to None → read the
      Dry Run toggle FRESH from settings.json at call time. The module-level DRY_RUN
      is only an import-time snapshot and goes stale in the long-lived scheduler
      after a UI toggle (it kept simulating fills while Settings said OFF — the
      Trade History "Dry Run" mismatch). Read live, like check_liquidity does for its
      thresholds. Callers may pass an explicit bool to force it (e.g. /api/test-run).
    """
    from position_sizer import size_position

    _target = target_fills if target_fills is not None else NUM_POSITIONS
    dry_run = get_settings().get("dry_run", DRY_RUN) if dry_run is None else dry_run

    log.info("\n" + "=" * 65)
    log.info(f"🚀 YOU ROCK VOLATILITY INCOME FUND — Execution Start")
    log.info(f"   Mode: {'🧪 DRY RUN' if dry_run else '🔴 LIVE'}")
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

    # Cash-secured funds gate: on a cash/Roth account, never even ATTEMPT a put we
    # can't fully secure. Seed from live IBKR cash and decrement locally as each
    # put fills, so later slots are skipped cleanly instead of firing orders IBKR
    # rejects with Error 201 (the "failed funds" cascade that also burns the whole
    # fallback pool). Cash accounts only — on margin IBKR reserves far less than
    # full notional, so a full-notional gate would wrongly skip affordable puts.
    # Read the toggle LIVE from settings (not an import snapshot) like dry_run.
    # None ⇒ gate off (fetch failed, or not a cash account): we fall back to
    # today's behaviour and let IBKR reject — a bad read must not block trading.
    cash_gate_on   = get_settings().get("cash_account", False)
    available_cash = _fetch_available_cash(ib) if cash_gate_on else None
    if available_cash is not None:
        log.info(f"  💵 Funds gate ON — ${available_cash:,.0f} cash available to secure new CSPs")

    results          = []
    filled_count     = 0
    capital_deployed = 0
    # The budget the sizer actually planned against — net liq minus reserved
    # wheel capital when compounding is on, NOT the static TOTAL_FUND_BUDGET.
    # Without this, execution had no idea what the plan's ceiling was: on
    # 2026-08-17 the sizer planned $224,300 against a $233,229 net-liq budget and
    # execution deployed $240,250, ~$7k past net liq and onto margin, because
    # upward strike adjustments were never re-checked against anything.
    # Defaults to TOTAL_FUND_BUDGET so the older callers keep working.
    _budget = float(budget) if budget else float(TOTAL_FUND_BUDGET)
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
            remaining = _budget - capital_deployed
            p = size_position(raw, remaining, is_last=is_last)
            if p:
                log.info(f"  🔄 Fallback candidate: {p['ticker']} "
                         f"({p['contracts']}x @ ${p['strike']:.2f})")
                all_sized.append(p)
                return p
        return None

    slot       = 0
    reconnects = 0
    attempted_contracts: dict = {}   # ticker → final contract worked this run

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

                # An UPWARD adjustment (stock rose since Saturday's screen) costs
                # more collateral per contract than the sizer planned for, and
                # nothing used to re-check it. On 2026-08-17 three adjustments —
                # AAOI +$7,700, CBRS +$6,000, CRDO +$2,250 — turned a compliant
                # $224,300 plan into $240,250 deployed, ~$7k past net liq and onto
                # margin. Trim the lot to what the remaining budget covers instead
                # of silently deploying more.
                #
                # This is a TOTAL-budget check, not a per-position cap: slot #1 is
                # deliberately uncapped in compound mode (it is the best-scoring
                # name and is meant to take the remainder), so this only stops the
                # account from committing more than it actually has.
                room = _budget - capital_deployed
                if new_capital > room:
                    # Named distinctly from the cash gate's `per_contract` /
                    # `orig_contracts` further down, which are separate locals.
                    adj_per_contract = strike * 100
                    max_fit = int(room // adj_per_contract) if adj_per_contract > 0 else 0
                    if max_fit < 1:
                        log.warning(
                            f"  ⛔ {ticker} skipped — adjusted strike ${strike:.2f} needs "
                            f"${adj_per_contract:,.0f}/contract but only ${room:,.0f} of the "
                            f"${_budget:,.0f} budget is left")
                        results.append({
                            "ticker": ticker, "status": "skipped_budget",
                            "reason": "adjusted_strike_over_budget",
                            "adjusted_strike": strike,
                            "budget_room": round(room, 2),
                            "needed": round(adj_per_contract, 2),
                        })
                        _status(ticker=ticker, stage=None,
                                result={"ticker": ticker, "status": "skipped_budget"})
                        continue
                    log.warning(
                        f"  ✂️  {ticker} trimmed {contracts} → {max_fit} contracts — "
                        f"adjusted strike ${strike:.2f} would need ${new_capital:,.0f} but "
                        f"only ${room:,.0f} of the ${_budget:,.0f} budget is left")
                    pre_trim_contracts = contracts
                    contracts   = max_fit
                    new_capital = round(contracts * adj_per_contract, 2)
                    pos = {**pos, "budget_trimmed_from": pre_trim_contracts}

                pos = {**pos, "strike": strike, "capital_used": new_capital,
                       "contracts": contracts}
                # Persist the executed strike/capital back into the candidate list
                # so state.json (and the dashboard) reflect what we actually filled,
                # not the original screener strike.
                for _i, _sp in enumerate(all_sized):
                    if _sp.get("ticker") == ticker:
                        all_sized[_i] = pos
                        break
                log.info(f"  ⚡ Capital adjusted: ${old_capital:,.0f} → ${new_capital:,.0f}")

            # ── Cash-secured funds gate ──────────────────────────────────
            # Never attempt a put we can't fully secure. Checked here (after the
            # delta adjustment settles the FINAL strike, before the market-data
            # round trip). Rather than drop the whole name, TRIM the contract count
            # to what the remaining cash covers (⌊cash / (strike×100)⌋) and place
            # the smaller position — a 4-lot that needs $29.6k becomes a 2-lot at
            # $14.8k instead of a skip. Only skip when not even one contract fits.
            if available_cash is not None:
                per_contract  = strike * 100
                max_affordable = int(available_cash // per_contract)
                if max_affordable < 1:
                    log.info(f"  ⛔ {ticker} skipped — one contract needs ${per_contract:,.0f}, "
                             f"only ${available_cash:,.0f} cash available")
                    skip_res = {"ticker": ticker, "status": "skipped_insufficient_cash",
                                "required_cash": round(per_contract, 2),
                                "available_cash": round(available_cash, 2)}
                    results.append(skip_res)
                    _status(ticker=ticker, stage=None, result=skip_res)
                    continue
                if max_affordable < contracts:
                    orig_contracts = contracts
                    contracts      = max_affordable
                    new_capital    = round(contracts * per_contract, 2)
                    log.info(f"  ✂️  {ticker} trimmed {orig_contracts}→{contracts} contracts to fit "
                             f"${available_cash:,.0f} cash (needs ${orig_contracts * per_contract:,.0f} at full size)")
                    pos = {**pos, "contracts": contracts, "capital_used": new_capital,
                           "cash_trimmed_from": orig_contracts}
                    for _i, _sp in enumerate(all_sized):
                        if _sp.get("ticker") == ticker:
                            all_sized[_i] = pos
                            break

            _status(ticker=ticker, stage="fetching market data")
            mkt = get_market_data(ib, contract, screener_premium=premium, dry_run=dry_run)
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
            # Record what we are about to work, so the post-run reconcile can match
            # broker positions against THIS run's contracts and never touch an open
            # CSP carried over from a previous week.
            attempted_contracts[ticker] = {
                "expiry":         contract.lastTradeDateOrContractMonth,
                "strike":         float(strike),
                "contracts":      contracts,
                "delta_at_entry": round(final_delta, 4) if final_delta is not None else None,
                "iv_at_entry":    round(final_iv, 4) if final_iv is not None else None,
            }
            result = place_order_with_escalation(ib, contract, contracts, mkt, ticker, dry_run=dry_run)
        except GatewayUnavailable as e:
            # Infrastructure, not strategy. Reported under its own status so the
            # Discord card can never imply the delta was out of range, and routed
            # through the same reconnect/abort path as any other IBKR error.
            log.error(f"  ❌ {ticker} — GATEWAY CONNECTION LOST: {e}")
            results.append({"ticker": ticker, "status": "failed_no_connection",
                            "reason": str(e)})
            _status(ticker=ticker, stage=None,
                    result={"ticker": ticker, "status": "failed_no_connection"})
            if reconnects >= MAX_RECONNECTS:
                log.error(f"  ❌ Max reconnects ({MAX_RECONNECTS}) reached — stopping execution")
                break
            try:
                ib = _reconnect(ib)
                reconnects += 1
                log.info(f"  ✅ Reconnected after gateway loss ({reconnects}/{MAX_RECONNECTS})")
            except Exception as re_err:
                log.error(f"  ❌ Reconnect failed: {re_err} — stopping execution")
                break
            continue
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
        # Breadcrumb when the funds gate trimmed the lot size to fit cash, so the
        # dashboard/Discord can show "filled 2 of 4 (cash-capped)".
        if pos.get("cash_trimmed_from"):
            result["cash_trimmed_from"] = pos["cash_trimmed_from"]
        if pos.get("budget_trimmed_from"):
            result["budget_trimmed_from"] = pos["budget_trimmed_from"]
        results.append(result)

        # Report result back to status callback
        _status(ticker=ticker, stage=None, result={
            "ticker": ticker,
            "status": result["status"],
            "fill_price": result.get("fill_price"),
            "premium_collected": result.get("premium_collected"),
            "order_type": result.get("order_type"),
            "cash_trimmed_from": pos.get("cash_trimmed_from"),
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
            # Funds gate: drop the cash this fill just secured so the next slot's
            # check sees the true remainder (exact for cash-secured puts = strike×100×qty).
            if available_cash is not None:
                available_cash = max(0.0, available_cash - strike * 100 * filled_qty)
                log.info(f"  💵 Funds gate — ${available_cash:,.0f} cash remaining after {ticker}")
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

    # Verify against the broker BEFORE dropping the connection. Runs on every
    # execution, not only after a visible wedge: it is two read-only calls, and
    # the run that most needs checking is the one that does not know it failed.
    if not dry_run:
        _status(ticker=None, stage="verifying against IBKR")
        broker_discrepancies = _reconcile_results_against_broker(
            ib, results, attempted_contracts
        )
    else:
        broker_discrepancies = []

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

    # A fill recovered by the broker reconcile never incremented the in-loop
    # counter, so re-derive it from the corrected results.
    recovered = [d for d in broker_discrepancies if d.get("kind") == "unrecorded_fill"]
    if recovered:
        filled_count = sum(
            1 for r in results
            if r.get("status") in ("filled", "partial_fill", "dry_run")
        )
        log.warning(f"  ⚠️  {len(recovered)} fill(s) recovered from IBKR after the run "
                    f"reported them as failed — filled_count corrected to {filled_count}")

    log.info(f"\n  Fills: {filled_count}/{_target}  |  "
             f"Total Premium: ${total_premium:,.0f}")
    if broker_discrepancies:
        log.warning(f"  ⚠️  Broker reconcile found {len(broker_discrepancies)} discrepancy(ies) "
                    f"— see alerts above")
    log.info("=" * 65)

    # Merge with existing state so wheel_holdings and monday_context survive.
    # A MISSING file is a legitimate fresh start ({}). A file that EXISTS but
    # won't parse is corruption (e.g. a prior crash mid-write): overwriting it
    # here would silently drop wheel_holdings — real capital-at-risk positions —
    # which IBKR can't reconstruct as lots. So we make a forensic copy and SKIP
    # the write rather than clobber. We deliberately do NOT rename state.json:
    # it's a symlink into the durable /data volume, and moving it would break the
    # link for every other process. The CSP orders are already placed at IBKR and
    # Saturday's detection re-adopts holdings from the broker, so the only loss is
    # this run's execution record — recoverable, unlike wheel_holdings.
    try:
        with open("state.json") as f:
            existing = json.load(f)
    except FileNotFoundError:
        existing = {}
    except json.JSONDecodeError as e:
        real = os.path.realpath("state.json")
        backup = f"{real}.corrupt-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        try:
            shutil.copy2(real, backup)   # copy the real file; leave the symlink intact
        except OSError:
            backup = "(forensic copy failed)"
        log.error(f"❌ state.json is corrupt ({e}); copied to {backup} and SKIPPING "
                  f"the results write to avoid clobbering wheel_holdings. CSP orders "
                  f"were placed; reconcile via Saturday detection, then repair state.json.")
        return results
    # Consolidate a same-week re-run: union this run's results with the fills
    # already opened earlier this week so the dashboard week view + filled_count +
    # total_premium + the Discord trades section reflect the whole week, not just
    # the last run. Dry runs keep the plain overwrite (no week to consolidate).
    execs, positions_out, fcount, tprem = (
        _merge_week_executions(existing, results, all_sized) if not dry_run
        else (results, all_sized, filled_count, total_premium)
    )
    existing.update({
        "run_date":      datetime.now().isoformat(),
        "positions":     positions_out,   # includes any fallback candidates that were attempted
        "executions":    execs,
        "filled_count":  fcount,
        "total_premium": tprem
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
