"""Trend filter for the YRVI-MT 🚢 track: the website's frozen weekly snapshot.

The YRVI-MT rules (greer_project yrvi_mt*.py, run by yr-scheduler for the
website fund) decide each week on FRIDAY'S CLOSE and freeze that decision into a
snapshot served by /api/targets/csp?strategy=YRVI-MT:

  market.csp_gate          True when SPY is at or below its 50-day average → new
                           puts may open this week (only on names whose own close
                           is also at or below their 50-day average)
  market.qqq_parking_gate  True when SPY and QQQ are above their 50-day averages
                           and QQQ is above its 200-day → spare cash holds QQQ.
                           The two gates are mutually exclusive.
  rows                     eligible candidates, ranked by put bid / strike

Monday re-checks execution conditions only. It never recomputes the trend
decision from IBKR history, so a box and the website fund can't disagree about
which way the week went.

No valid snapshot means NO MT TRADES that week — no covered calls, no puts, no
QQQ change — exactly what the website fund does. That only happens when the
website's Friday capture failed, so the run says so loudly instead of falling
back to a different strategy.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests

from app_identity import request_headers
from config import RENDER_SECRET, RENDER_URL

NY = ZoneInfo("America/New_York")
STRATEGY = "YRVI-MT"


def active(settings: dict) -> bool:
    """The trend filter runs only on the wheel. CSP-only mode overrides it, the same
    way it overrides every other wheel rule."""
    return bool(settings.get("trend_filter_enabled")) and not settings.get("csp_only_mode")


def _today_ny() -> date:
    return datetime.now(NY).date()


def _unavailable(status: str, reasons: list, payload: dict | None = None) -> dict:
    p = payload or {}
    return {"ok": False, "status": status, "reasons": reasons,
            "observation_date": p.get("observation_date"), "entry_date": p.get("entry_date"),
            "expiry": p.get("expiry"), "csp_gate": None, "park_gate": None, "rows": [],
            "snapshot": None}


def validate(payload: dict, preview: bool, today: date | None = None) -> dict:
    """Check a snapshot payload is complete and belongs to the week being traded.

    preview=False (a live run): the snapshot's entry day must be TODAY — the website
    trades only on the entry day, and a mid-week Run Now must not act on a stale
    decision. preview=True (Run Screener / the weekend preview): the upcoming entry
    day, today or up to 3 days ahead, is accepted so the plan can be shown.
    """
    today = today or _today_ny()
    if not isinstance(payload, dict) or payload.get("strategy") != STRATEGY:
        return _unavailable("invalid_snapshot", ["not a YRVI-MT snapshot"])
    if payload.get("status") != "ready":
        return _unavailable(payload.get("status") or "not_ready",
                            payload.get("reasons") or ["snapshot not ready"], payload)
    market = payload.get("market") or {}
    csp, park = market.get("csp_gate"), market.get("qqq_parking_gate")
    rows = payload.get("rows")
    if type(csp) is not bool or type(park) is not bool or (csp and park):
        return _unavailable("invalid_snapshot", ["market gates missing or not exclusive"], payload)
    if not isinstance(rows, list) or payload.get("count") != len(rows):
        return _unavailable("invalid_snapshot", ["row count mismatch"], payload)
    try:
        entry = date.fromisoformat(payload["entry_date"])
    except (KeyError, TypeError, ValueError):
        return _unavailable("invalid_snapshot", ["missing entry date"], payload)
    if preview:
        if not 0 <= (entry - today).days <= 3:
            return _unavailable("stale_snapshot", [f"snapshot is for {entry}, not this week"], payload)
    elif entry != today:
        return _unavailable("not_entry_day",
                            [f"the MT week enters on {entry}; today is {today}"], payload)
    return {"ok": True, "status": "ready", "reasons": [],
            "observation_date": payload.get("observation_date"), "entry_date": str(entry),
            "expiry": payload.get("expiry"), "csp_gate": csp, "park_gate": park,
            "rows": rows, "snapshot": payload}


def fetch_snapshot(preview: bool = False) -> dict:
    """GET this week's frozen snapshot. Never raises: any failure is an unavailable
    snapshot, which the Monday run treats as a no-trade week."""
    try:
        r = requests.get(RENDER_URL, params={"secret": RENDER_SECRET, "strategy": STRATEGY},
                         headers=request_headers(), timeout=90)
        try:
            payload = r.json()
        except ValueError:
            return _unavailable(f"http_{r.status_code}", ["non-JSON response"])
        if r.status_code not in (200, 503):
            return _unavailable(f"http_{r.status_code}", [str(payload.get("error", ""))[:200]])
        return validate(payload, preview)
    except requests.RequestException as e:
        return _unavailable("request_failed", [type(e).__name__])


def describe(mt: dict) -> str:
    """One line for logs and Discord."""
    if not mt.get("ok"):
        return f"no valid MT snapshot ({mt.get('status')}: {', '.join(mt.get('reasons') or [])})"
    gate = ("puts OPEN (SPY at/below its 50-day)" if mt["csp_gate"]
            else "QQQ park ON (uptrend)" if mt["park_gate"] else "both gates closed — cash")
    return (f"week of {mt['entry_date']} (Friday {mt['observation_date']} close): {gate}, "
            f"{len(mt['rows'])} ranked candidate(s), expiry {mt['expiry']}")
