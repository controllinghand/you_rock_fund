#!/usr/bin/env python3
"""
diag.py — YRVI market data diagnostic

Tests IBKR connectivity and options market data without placing orders.
Use mid-week to confirm the Market Data API Acknowledgement is active
and delayed options data is flowing — no screener needed.

Usage:
    docker exec yrvi-scheduler-1 python diag.py
"""
import sys
from datetime import datetime, timedelta
from ib_insync import IB, Stock, Option

from config import IBKR_HOST, IBKR_PORT

TEST_TICKER  = "SPY"
DIAG_CLIENT  = 9   # dedicated client ID — does not conflict with scheduler (1/2/3)
PASS         = "✅"
FAIL         = "❌"
WARN         = "⚠️ "


def next_friday() -> str:
    today = datetime.today()
    for days in range(1, 10):
        candidate = today + timedelta(days=days)
        if candidate.weekday() == 4:   # 4 = Friday
            return candidate.strftime("%Y%m%d")


def is_nan(val) -> bool:
    try:
        return val != val
    except Exception:
        return True


def section(n: int, total: int, label: str):
    print(f"\n[{n}/{total}] {label}...")


def main():
    print("\n" + "=" * 60)
    print("  YRVI MARKET DATA DIAGNOSTIC")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S ET')}")
    print("=" * 60)

    steps = 5

    # ── 1. Connect ─────────────────────────────────────────────
    section(1, steps, f"Connecting to IB Gateway ({IBKR_HOST}:{IBKR_PORT})")
    try:
        ib = IB()
        ib.connect(IBKR_HOST, IBKR_PORT, clientId=DIAG_CLIENT)
        print(f"  {PASS} Connected — accounts: {ib.managedAccounts()}")
    except Exception as e:
        print(f"  {FAIL} Connection failed: {e}")
        print("\n  Is IB Gateway running?  docker ps | grep ib")
        sys.exit(1)

    # ── 2. Set delayed market data ─────────────────────────────
    section(2, steps, "Setting market data type → DELAYED (type 3)")
    ib.reqMarketDataType(3)
    ib.sleep(1)
    print(f"  {PASS} Market data type set (delayed — no subscription required)")

    # ── 3. Get SPY stock price ─────────────────────────────────
    section(3, steps, f"Fetching {TEST_TICKER} stock price")
    try:
        stk   = Stock(TEST_TICKER, "SMART", "USD")
        stk_q = ib.qualifyContracts(stk)
        if not stk_q:
            print(f"  {FAIL} Could not qualify {TEST_TICKER}")
            ib.disconnect()
            sys.exit(1)
        stk_ticker = ib.reqMktData(stk_q[0], snapshot=False)
        ib.sleep(3)
        ib.cancelMktData(stk_q[0])
        price = stk_ticker.last or stk_ticker.close
        if not price or is_nan(price):
            print(f"  {WARN} No price for {TEST_TICKER} — market may be closed, using chain fallback")
            price = None
        else:
            print(f"  {PASS} {TEST_TICKER}: ${price:.2f}")
    except Exception as e:
        print(f"  {FAIL} Stock price error: {e}")
        price = None

    # ── 4. Resolve a valid option contract from chain ──────────
    section(4, steps, f"Looking up {TEST_TICKER} option chain")
    expiry = next_friday()
    try:
        chains = ib.reqSecDefOptParams(TEST_TICKER, "", "STK", stk_q[0].conId)
        ib.sleep(1)
        smart_chain = next((c for c in chains if c.exchange == "SMART"), None) or \
                      next((c for c in chains), None)
        if not smart_chain or expiry not in (smart_chain.expirations or []):
            # Fall back to nearest available Friday expiry
            fridays = sorted(
                e for e in (smart_chain.expirations if smart_chain else [])
                if datetime.strptime(e, "%Y%m%d").weekday() == 4 and e >= expiry
            )
            expiry = fridays[0] if fridays else expiry
        strikes = sorted(smart_chain.strikes) if smart_chain else []
    except Exception as e:
        print(f"  {WARN} Chain lookup failed ({e}) — will attempt a fixed strike")
        strikes = []

    # Pick strike ~10% OTM; fall back to a round number near current SPY level
    if price and strikes:
        target = price * 0.90
        strike = min(strikes, key=lambda s: abs(s - target))
    elif strikes:
        strike = strikes[len(strikes) // 2]
    else:
        strike = 500  # rough SPY fallback

    print(f"  {PASS} Using expiry {expiry}  strike ${strike:.0f}")

    try:
        contract  = Option(TEST_TICKER, expiry, strike, "P", "SMART", currency="USD")
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            print(f"  {FAIL} Could not qualify {TEST_TICKER} {expiry} ${strike}P")
            ib.disconnect()
            sys.exit(1)
        contract = qualified[0]
        print(f"  {PASS} Qualified conId={contract.conId}")
    except Exception as e:
        print(f"  {FAIL} Option qualification error: {e}")
        ib.disconnect()
        sys.exit(1)

    # ── 5. Fetch market data + greeks ──────────────────────────
    section(5, steps, f"Fetching bid/ask/delta for {TEST_TICKER} {expiry} ${strike}P")
    try:
        tkr = ib.reqMktData(contract, genericTickList="106", snapshot=False)
        ib.sleep(5)
        ib.cancelMktData(contract)
        ib.sleep(0.5)

        bid   = tkr.bid
        ask   = tkr.ask
        delta = None
        for greeks in (tkr.modelGreeks, tkr.lastGreeks, tkr.bidGreeks, tkr.askGreeks):
            if greeks is not None and not is_nan(greeks.delta):
                delta = greeks.delta
                break

        bid_ok   = not is_nan(bid) and bid is not None and bid > 0
        ask_ok   = not is_nan(ask) and ask is not None and ask > 0
        delta_ok = delta is not None and not is_nan(delta)

        print(f"  {'✅' if bid_ok  else '❌'} Bid:   {'$' + f'{bid:.2f}'  if bid_ok  else 'no data'}")
        print(f"  {'✅' if ask_ok  else '❌'} Ask:   {'$' + f'{ask:.2f}'  if ask_ok  else 'no data'}")
        print(f"  {'✅' if delta_ok else '⚠️ '} Delta: {f'{delta:.4f}' if delta_ok else 'no greeks (normal if market just opened)'}")

        market_data_ok = bid_ok and ask_ok
    except Exception as e:
        print(f"  {FAIL} Market data error: {e}")
        market_data_ok = False

    ib.disconnect()

    # ── Result ─────────────────────────────────────────────────
    print("\n" + "=" * 60)
    if market_data_ok:
        print(f"  {PASS} RESULT: Options market data is flowing — YRVI can trade")
    else:
        print(f"  {FAIL} RESULT: No options market data received")
        print()
        print("  Most likely cause: Market Data API Acknowledgement has not")
        print("  propagated yet. Re-run tomorrow between 10–10:30 AM ET")
        print("  after the market has been open at least 15–30 minutes.")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
