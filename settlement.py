"""Unsettled-cash tracking — money the broker's cash balance shows but won't let
you spend yet.

Why this exists
---------------
2026-09-21: the Monday cash sweep tried to park $8,298 in QQQ and IBKR rejected it
with Error 201 — "Equity with Loan Value [9368.63 USD] must exceed the new total
Initial Margin of [9500.00 USD]". At that moment the account's TotalCashValue was
$17,905.15 and BuyingPower, AvailableFunds and SettledCash ALL reported $8,405.15
free. Every tag the code can read said the money was there. Only the order-entry
risk check knew otherwise: $8,536.52 of that cash was Friday's proceeds — IREN
300 shares called away at $44.50, plus the prior week's QQQ park sold for
$3,595.68 — still inside T+1 settlement. By the close the same tags read
$17,905.15 and the constraint had evaporated on its own.

So the broker cannot be asked this question. The fund has to remember its own
recent sales, which is what this ledger is.

The rule it encodes
-------------------
On a cash/IRA account IBKR will not let you BUY STOCK with unsettled proceeds
(the good-faith-violation rule). It does NOT apply that restriction to writing
options: the HUT put sold two minutes before the rejection, under the identical
cash picture, filled without complaint, and no option order on this account has
ever been rejected for funds. So callers haircut the STOCK path (the cash sweep)
and leave the CSP path alone.

Settlement is T+1, and the observed behaviour is that it completes DURING the
following session rather than at its open — Friday's proceeds were still blocked
at 10:01 PT Monday and free by 15:27 PT the same day. So an entry counts as
unsettled while `asof <= next_trading_day(trade_date)`, which is deliberately one
session conservative: over-estimating unsettled cash under-parks (costs a few
dollars of yield drift), under-estimating it earns a rejected order and a
manual-check alert.

This module is pure — no config, no ib_insync, no I/O. The caller owns state.json.
"""
from datetime import date, datetime

from market_calendar import next_trading_day

LEDGER_KEY = "unsettled_sales"


def _as_date(value) -> date | None:
    """Accept a date, a datetime, or an ISO string of either."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
    return None


def _is_settled(entry: dict, asof: date) -> bool:
    d = _as_date(entry.get("date"))
    if d is None:
        # An entry we cannot date is treated as settled rather than blocking the
        # sweep forever on a malformed row.
        return True
    return asof > next_trading_day(d)


def record_sale(state: dict, amount: float, source: str, ticker: str = None,
                trade_date=None, entry_id: str = None) -> bool:
    """Log sale proceeds that are now working through settlement.

    `entry_id` makes this idempotent, which matters because the writers re-run:
    detect_assignments books the same call-away Saturday AND again at Monday's
    reconcile. Returns True when a new row was added.
    """
    amount = round(float(amount or 0.0), 2)
    if amount <= 0:
        return False
    d = _as_date(trade_date) or date.today()
    entry_id = entry_id or f"{source}:{ticker or ''}:{d.isoformat()}"

    ledger = state.get(LEDGER_KEY)
    if not isinstance(ledger, list):
        ledger = []
    if any(e.get("id") == entry_id for e in ledger):
        return False

    ledger.append({
        "id":       entry_id,
        "date":     d.isoformat(),
        "amount":   amount,
        "source":   source,
        "ticker":   ticker,
        "recorded": datetime.now().isoformat(),
    })
    state[LEDGER_KEY] = prune(ledger, asof=d)
    return True


def prune(ledger: list, asof: date = None) -> list:
    """Drop rows that have settled. Keeps the ledger a handful of entries long."""
    asof = asof or date.today()
    if not isinstance(ledger, list):
        return []
    return [e for e in ledger if isinstance(e, dict) and not _is_settled(e, asof)]


def unsettled(state: dict, asof: date = None) -> tuple[float, list]:
    """(total still unsettled, the rows making it up) as of `asof` (today)."""
    asof   = asof or date.today()
    ledger = state.get(LEDGER_KEY)
    rows   = [e for e in ledger if isinstance(e, dict) and not _is_settled(e, asof)] \
             if isinstance(ledger, list) else []
    return round(sum(float(e.get("amount") or 0.0) for e in rows), 2), rows


def parse_settled_by_date(raw: str) -> dict:
    """IBKR's `SettledCashByDate` → {date: amount}.

    Raw form is `20260921:17613.1462148;20260922:17905.1462148` — a settlement
    SCHEDULE, and the only figure IBKR publishes with a time dimension. Every other
    cash tag reports one number: on the evening of 2026-09-21 `SettledCash` read
    $17,905.15 flat while this read $17,613.15 for that day and $17,905.15 for the
    next, the $292.00 being the CSP + CC premiums written that morning, settling T+1.

    It lives in `accountValues()`, NOT in `accountSummary()`'s tag list, which is
    why nothing here had ever seen it. Malformed pairs are skipped rather than
    raising — this feeds a log line, not a trading decision.
    """
    out = {}
    for pair in (raw or "").split(";"):
        day, _, amount = pair.partition(":")
        try:
            out[datetime.strptime(day.strip(), "%Y%m%d").date()] = float(amount)
        except (TypeError, ValueError):
            continue
    return out


def settled_as_of(schedule: dict, asof: date = None) -> float | None:
    """The settled-cash figure that applies on `asof` (today).

    Exact match when IBKR lists the day. Otherwise the LOWEST figure in the
    schedule: the amounts rise as money settles, so the smallest is the one that
    cannot over-state what is spendable right now.
    """
    if not schedule:
        return None
    asof = asof or date.today()
    if asof in schedule:
        return schedule[asof]
    past = [d for d in schedule if d <= asof]
    return schedule[max(past)] if past else min(schedule.values())


def describe(rows: list) -> str:
    """One-line human summary for a log line or a Discord alert."""
    if not rows:
        return "none"
    return ", ".join(
        f"{e.get('ticker') or e.get('source')} ${float(e.get('amount') or 0):,.0f}"
        f" ({e.get('date')})"
        for e in rows)
