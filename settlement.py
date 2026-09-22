"""IBKR's settlement schedule — the one cash figure that carries a date.

`SettledCashByDate` lives in `accountValues()`, not in `accountSummary()`'s tag
list, which is why nothing here had ever read it. Raw form:

    20260921:17613.1462148;20260922:17905.1462148

On the evening of 2026-09-21 that read $17,613.15 for that day and $17,905.15 for
the next, while `SettledCash` reported a flat $17,905.15 — the $292.00 difference
being the CSP + CC premiums written that morning, settling T+1. Every other cash
tag reports a single number and cannot express "not until tomorrow."

Why this module is now only a parser
------------------------------------
v5.2.123 also kept a ledger of the fund's own recent sales here, on the theory
that the 2026-09-21 cash-sweep rejection was caused by proceeds still inside T+1.
That was wrong. On 2026-09-22 the cash was fully settled — this very tag agreed to
the cent, gap $0.00 — and IBKR refused the identical order anyway. The real cause
was margin headroom: stock carries no loan value in that account, so a purchase
lowers equity-with-loan-value without adding any back, and the order was sized at
exactly the available funds. See the buy path in cash_park.py. The ledger was
removed in v5.2.128; this logging is what disproved it, so it stays.

Pure — no config, no ib_insync, no I/O.
"""
from datetime import date, datetime


def parse_settled_by_date(raw: str) -> dict:
    """`SettledCashByDate` → {date: amount}.

    Malformed pairs are skipped rather than raising — this feeds a log line, not a
    trading decision.
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
