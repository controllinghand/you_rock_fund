"""Option tenor for new CSPs: weekly (default) or monthly (the YRVI-CSP-M track).

`option_tenor` in settings.json is "weekly" or "monthly". Monthly is honoured only
together with csp_only_mode. That combination is what the 2017–2026 monthly backtest
validated (greer_project docs/THETADATA_RANKING_POC.md): selling assigned shares at the
next Monday, not wheeling them. Monthly with the wheel would also need monthly covered
calls, which don't exist yet, so with csp_only_mode off the setting is inert and the
box trades weekly.

Monthly cycle: new puts open on the first Monday after each monthly expiry (the third
Friday, or the Thursday before when that Friday is a market holiday) and expire at
the NEXT monthly expiry, ~4–5 weeks later. A one-week catch-up window lets the
following Monday open anything the entry Monday missed (gateway down, a failed fill),
so a catch-up put still has ~2½+ weeks to run (18 days in a 4-week cycle). On the other Mondays the open puts
already fill the slots (monday_runner counts open short puts against num_positions),
so nothing new is sold.
"""
from datetime import date, datetime, timedelta

from market_calendar import is_market_holiday

WEEKLY, MONTHLY = "weekly", "monthly"
CATCH_UP_DAYS = 10          # third Friday + 10 days = the SECOND Monday after it


def active(settings: dict | None = None) -> str:
    """The tenor actually in effect (see the module docstring for the CSP-only rule)."""
    if settings is None:
        from config import get_settings
        settings = get_settings()
    if settings.get("option_tenor") == MONTHLY and settings.get("csp_only_mode"):
        return MONTHLY
    return WEEKLY


def monthly_expiry(year: int, month: int) -> date:
    """Standard monthly option expiry: the third Friday, or the prior trading day
    (Thursday) when that Friday is a market holiday (e.g. Good Friday)."""
    first = date(year, month, 1)
    fri = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
    while is_market_holiday(fri):
        fri -= timedelta(days=1)
    return fri


def _add_month(y: int, m: int, k: int = 1) -> tuple[int, int]:
    m += k
    return y + (m - 1) // 12, (m - 1) % 12 + 1


def cycle(today: date | None = None) -> dict:
    """The monthly cycle `today` falls in.

    prev_expiry  the last monthly expiry on or before today
    next_expiry  the expiry new puts sold today would use
    entry_date   first trading day after prev_expiry (the cycle's entry Monday)
    in_window    True from entry_date through the catch-up Monday
    next_entry   the next date new monthly puts may open
    """
    today = today or date.today()
    y, m = today.year, today.month
    this = monthly_expiry(y, m)
    if today <= this:
        prev = monthly_expiry(*_add_month(y, m, -1))
        nxt = this
    else:
        prev = this
        nxt = monthly_expiry(*_add_month(y, m, 1))
    entry = prev + timedelta(days=1)
    while entry.weekday() >= 5 or is_market_holiday(entry):
        entry += timedelta(days=1)
    in_window = entry <= today <= prev + timedelta(days=CATCH_UP_DAYS)
    nxt_entry = entry if today < entry else nxt + timedelta(days=1)
    while nxt_entry.weekday() >= 5 or is_market_holiday(nxt_entry):
        nxt_entry += timedelta(days=1)
    if in_window:
        nxt_entry = today
    return {"prev_expiry": prev, "next_expiry": nxt, "entry_date": entry,
            "in_window": in_window, "next_entry": nxt_entry,
            "dte": (nxt - today).days}


def screener_expiry_str(d: date) -> str:
    """The screener's expiry string format, which trader.parse_expiry expects."""
    return datetime(d.year, d.month, d.day).strftime("%a, %d %b %Y 00:00:00 GMT")
