"""
Risk Manager — daily monitor (Tuesday–Thursday 9AM PST)

run_daily_monitor():
  - Fetches current price for each wheel holding from IBKR
  - Checks whether each ticker still passes screener filters mid-week
  - Reports CC strike, expiry days remaining, and unrealized P&L per holding
  - Flags ⚠️ any holding whose ticker dropped from the screener
    (expect those shares to be sold at next Monday's wheel check)
  - Updates state.json with current prices and weekly P&L snapshot
"""
import json
import logging
from datetime import datetime, date, timedelta

from ib_insync import IB, Stock

from config import IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID_RISK, ACCOUNT, ACCOUNT_TYPE, gateway_unreachable_message, probe_port, get_settings
from screener import get_all_candidates

STATE_FILE = "state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.FileHandler("risk_log.txt"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _connect() -> IB:
    log.info(f"🔌 Connecting to IB Gateway {IBKR_HOST}:{IBKR_PORT} ({ACCOUNT_TYPE}, clientId={IBKR_CLIENT_ID_RISK})")
    ib = IB()
    try:
        ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID_RISK)
    except TimeoutError:
        port_open = probe_port(IBKR_HOST, IBKR_PORT)
        log.warning(
            f"⚠️  IBKR connect timed out ({ACCOUNT_TYPE}, {IBKR_HOST}:{IBKR_PORT}) — "
            f"TCP port {'OPEN (API handshake hung)' if port_open else 'CLOSED (gateway not listening)'}"
        )
        raise TimeoutError(gateway_unreachable_message(IBKR_HOST, IBKR_PORT))
    ib.reqMarketDataType(3)
    log.info(f"✅ Connected to IBKR (clientId={IBKR_CLIENT_ID_RISK})")
    return ib


def _is_nan(val) -> bool:
    try:
        return val != val
    except Exception:
        return True


def _get_stock_price(ib: IB, ticker: str) -> float | None:
    contract  = Stock(ticker, "SMART", "USD")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        log.warning(f"  ⚠️  Could not qualify stock: {ticker}")
        return None
    data  = ib.reqMktData(qualified[0], snapshot=False)
    ib.sleep(4)
    price = data.last  if not _is_nan(data.last)  and data.last  > 0 else \
            data.close if not _is_nan(data.close) and data.close > 0 else \
            data.bid   if not _is_nan(data.bid)   and data.bid   > 0 else None
    ib.cancelMktData(qualified[0])
    ib.sleep(0.5)
    return round(float(price), 2) if price else None


def _build_weekly_pnl(state: dict) -> dict:
    executions = state.get("executions", [])
    holdings   = state.get("wheel_holdings", [])
    context    = state.get("monday_context", {})

    csp_premium = sum(
        e.get("premium_collected", 0) for e in executions
        if e.get("status") in ("filled", "partial_fill", "dry_run")
    )
    cc_premium      = context.get("cc_premium", 0.0)
    shares_sold_pnl = context.get("shares_sold_pnl", 0.0)

    unrealized = 0.0
    for h in holdings:
        if h.get("shares", 0) > 0 and h.get("current_price") and h.get("assigned_strike"):
            unrealized += (h["current_price"] - h["assigned_strike"]) * h["shares"]
    unrealized = round(unrealized, 2)

    total_realized = round(csp_premium + cc_premium + shares_sold_pnl, 2)

    # Floor run_date to its Monday so this matches reconciler / monday_runner keying.
    _rd = state.get("run_date", "")[:10]
    if _rd:
        _d = date.fromisoformat(_rd)
        week_start = (_d - timedelta(days=_d.weekday())).strftime("%Y-%m-%d")
    else:
        week_start = ""

    return {
        "week_start":           week_start,
        "csp_premium":          round(csp_premium, 2),
        "cc_premium":           round(cc_premium, 2),
        "shares_sold_pnl":      round(shares_sold_pnl, 2),
        "total_realized":       total_realized,
        "unrealized_stock_pnl": unrealized,
        "grand_total":          round(total_realized + unrealized, 2),
        "last_updated":         datetime.now().isoformat()
    }


# ── Public API ─────────────────────────────────────────────────

def run_daily_monitor():
    """
    Tuesday–Thursday 9AM PST.
    Checks each wheel holding: current price, CC status, unrealized P&L,
    and screener eligibility. Flags positions at risk of Monday sale.
    """
    now = datetime.now()
    log.info("\n" + "=" * 65)
    log.info(f"📊 DAILY RISK MONITOR — {now.strftime('%A %Y-%m-%d %H:%M:%S')}")
    log.info("=" * 65)

    state    = _load_state()
    holdings = state.get("wheel_holdings", [])

    # Fetch screener tickers once — used for all holdings
    log.info("\n📡 Checking screener eligibility...")
    # Held-position membership check: use retention mode so a held name isn't
    # flagged "dropped from screener" over an entry-only delta/buffer miss.
    screener_tickers = get_all_candidates(retention=True)
    if screener_tickers:
        log.info(f"  {len(screener_tickers)} ticker(s) currently pass screener filters")
    else:
        log.warning("  ⚠️  Screener returned 0 results — screener check unavailable")

    if not holdings:
        log.info("📭 No wheel holdings to monitor")
        pnl = _build_weekly_pnl(state)
        state["weekly_pnl"] = pnl
        _save_state(state)
        _log_pnl_summary(pnl)
        return

    ib = _connect()
    screener_dropped = []

    try:
        log.info(f"\n  Monitoring {len(holdings)} wheel holding(s):\n")

        excluded = {t.strip().upper() for t in get_settings().get("excluded_tickers", []) if t and t.strip()}

        for h in holdings:
            ticker          = h["ticker"]
            if ticker.upper() in excluded:
                log.info(f"  [{ticker}]  🚫 excluded from the wheel — skipping monitor")
                continue
            shares          = h.get("shares", 0)
            assigned_strike = h.get("assigned_strike", 0.0)
            cc_strike       = h.get("current_cc_strike")
            cc_expiry_str   = h.get("current_cc_expiry")   # "YYYYMMDD" or None
            cc_status       = h.get("cc_status", "unknown")
            weeks_held      = h.get("weeks_held", 0)

            if shares <= 0:
                log.info(f"  [{ticker}]  (exited position)")
                continue

            log.info(f"  ── {ticker}  {shares} shares  "
                     f"assigned @ ${assigned_strike:.2f}  week {weeks_held} ──")

            current_price = _get_stock_price(ib, ticker)
            if current_price is None:
                log.warning(f"    ⚠️  Price unavailable — skipping")
                continue

            h["current_price"] = current_price
            h["last_checked"]  = now.isoformat()

            unrealized = round((current_price - assigned_strike) * shares, 2)
            pnl_icon   = "🟢" if unrealized >= 0 else "🔴"

            # CC expiry countdown
            days_left = None
            if cc_expiry_str:
                try:
                    exp_date  = datetime.strptime(cc_expiry_str, "%Y%m%d").date()
                    days_left = (exp_date - now.date()).days
                except ValueError:
                    pass

            if cc_strike and days_left is not None:
                cc_line = (f"CC @ ${cc_strike:.2f}  exp {cc_expiry_str}"
                           f"  ({days_left}d remaining)  [{cc_status}]")
            else:
                cc_line = f"CC: {cc_status}"

            # Screener status + earnings check
            # screener_tickers is dict[str, dict] from get_all_candidates()
            if screener_tickers:
                on_screener = ticker in screener_tickers
                if not on_screener:
                    screener_dropped.append(ticker)
                screener_line = ("✅ on screener"
                                 if on_screener
                                 else "⚠️  DROPPED FROM SCREENER — expect sale Monday")

                # Earnings flag
                dte = screener_tickers.get(ticker, {}).get("days_to_earnings")
                try:
                    earnings_soon = dte is not None and 0 <= int(dte) <= 4
                except (TypeError, ValueError):
                    earnings_soon = False
                earnings_line = (f"⚠️  EARNINGS THIS WEEK ({int(dte)} days) — "
                                 f"expect sale Monday"
                                 if earnings_soon else None)
            else:
                screener_line = "? (screener API unavailable)"
                earnings_line = None

            log.info(f"    Price:      ${current_price:.2f}  "
                     f"(assigned @ ${assigned_strike:.2f})")
            log.info(f"    Unrealized: {pnl_icon} ${unrealized:,.0f}")
            log.info(f"    {cc_line}")
            log.info(f"    Screener:   {screener_line}")
            if earnings_line:
                log.warning(f"    Earnings:   {earnings_line}")

    finally:
        ib.disconnect()

    # Summary alerts
    if screener_dropped:
        log.warning("\n" + "!" * 65)
        log.warning(f"  ⚠️  SCREENER DROP: {screener_dropped}")
        log.warning(f"  These tickers no longer pass screener filters.")
        log.warning(f"  They will be sold next Monday 9:55AM PST.")
        log.warning("!" * 65)
    else:
        active = [h for h in holdings if h.get("shares", 0) > 0]
        if active:
            log.info(f"\n  ✅ All {len(active)} holding(s) still pass screener")

    state["wheel_holdings"] = holdings
    pnl = _build_weekly_pnl(state)
    state["weekly_pnl"] = pnl
    _save_state(state)
    _log_pnl_summary(pnl)


def _log_pnl_summary(pnl: dict):
    log.info("\n" + "=" * 65)
    log.info("💰 WEEKLY P&L SNAPSHOT")
    log.info(f"   Week of:              {pnl.get('week_start', 'N/A')}")
    log.info(f"   CSP premium:         ${pnl.get('csp_premium', 0):>10,.0f}")
    log.info(f"   CC premium:          ${pnl.get('cc_premium', 0):>10,.0f}")
    log.info(f"   Shares sold P&L:     ${pnl.get('shares_sold_pnl', 0):>10,.0f}")
    log.info(f"   ─────────────────────────────")
    log.info(f"   Total realized:      ${pnl.get('total_realized', 0):>10,.0f}")
    log.info(f"   Unrealized stock:    ${pnl.get('unrealized_stock_pnl', 0):>10,.0f}")
    log.info(f"   Grand total:         ${pnl.get('grand_total', 0):>10,.0f}")
    log.info("=" * 65)


if __name__ == "__main__":
    run_daily_monitor()
