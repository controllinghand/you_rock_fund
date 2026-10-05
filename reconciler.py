"""Reconcile ytd_tracker.json from IBKR Flex XML fills.

Two entry points:
  reconcile_from_xml()           — parse XML already in memory
  reconcile_from_flex_service()  — fetch from IBKR Flex Web Service
"""
import json
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).parent
YTD_FILE = BASE_DIR / "ytd_tracker.json"

FLEX_SEND_URL = (
    "https://gdcdyn.interactivebrokers.com"
    "/Universal/servlet/FlexStatementService.SendRequest"
)
FLEX_GET_URL = (
    "https://gdcdyn.interactivebrokers.com"
    "/Universal/servlet/FlexStatementService.GetStatement"
)


def _week_monday(d: date) -> str:
    return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")


def _parse_fills(
    xml_str: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> list[dict]:
    """Parse IBKR Flex XML; return list of option-sell fill dicts.

    date_from / date_to: YYYY-MM-DD filter strings (inclusive, optional).
    Each returned dict has: symbol, putCall, strike, expiry, tradeDate,
    week_start, contracts, premium.
    """
    root = ET.fromstring(xml_str)

    d_from = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else None
    d_to   = datetime.strptime(date_to,   "%Y-%m-%d").date() if date_to   else None

    fills = []
    for elem in root.iter():
        if elem.tag not in ("Trade", "Execution"):
            continue

        if elem.get("assetCategory", "") != "OPT":
            continue

        buy_sell = elem.get("buySell", "")
        if "SELL" not in buy_sell or "Ca." in buy_sell:
            continue

        # tradeDate is YYYYMMDD; dateTime prefix fallback
        trade_date_str = elem.get("tradeDate") or elem.get("dateTime", "")[:8]
        if not trade_date_str or len(trade_date_str) < 8:
            continue

        try:
            trade_date = datetime.strptime(trade_date_str, "%Y%m%d").date()
        except ValueError:
            continue

        if d_from and trade_date < d_from:
            continue
        if d_to and trade_date > d_to:
            continue

        try:
            proceeds = float(elem.get("proceeds", 0))
        except (TypeError, ValueError):
            proceeds = 0.0

        if proceeds <= 0:
            continue

        try:
            contracts = abs(int(float(elem.get("quantity", 1))))
        except (TypeError, ValueError):
            contracts = 1

        fills.append({
            "symbol":     elem.get("symbol", ""),
            "putCall":    elem.get("putCall", ""),
            "strike":     elem.get("strike", ""),
            "expiry":     elem.get("expiry", ""),
            "tradeDate":  trade_date_str,
            "week_start": _week_monday(trade_date),
            "contracts":  contracts,
            "premium":    round(proceeds, 2),
        })

    return fills


def _rebuild_ytd(fills: list[dict]) -> dict:
    """Group fills by week and build a ytd_tracker-compatible dict."""
    weeks_map: dict[str, float] = {}
    for f in fills:
        ws = f["week_start"]
        weeks_map[ws] = round(weeks_map.get(ws, 0.0) + f["premium"], 2)

    weeks = [
        {
            "week_start":        ws,
            "premium_collected": prem,
            "total_realized":    prem,
        }
        for ws, prem in sorted(weeks_map.items())
    ]

    total = round(sum(w["premium_collected"] for w in weeks), 2)
    best  = max(weeks, key=lambda w: w["premium_collected"]) if weeks else None
    worst = min(weeks, key=lambda w: w["premium_collected"]) if weeks else None

    return {
        "weeks":         weeks,
        "total_premium": total,
        "weeks_traded":  len(weeks),
        "best_week":     best,
        "worst_week":    worst,
    }


def _load_existing_weeks() -> dict[str, dict]:
    """Return existing ytd_tracker weeks keyed by week_start."""
    if not YTD_FILE.exists():
        return {}
    try:
        data = json.loads(YTD_FILE.read_text())
        return {w["week_start"]: w for w in data.get("weeks", []) if "week_start" in w}
    except Exception:
        return {}


def _finalize_ytd(weeks_map: dict[str, dict]) -> dict:
    """Build a complete ytd_tracker dict from a week_start → week dict map."""
    weeks = sorted(weeks_map.values(), key=lambda w: w["week_start"])
    total = round(sum(w.get("premium_collected", w.get("realized", 0)) for w in weeks), 2)
    # Hold weeks ($0 by design while monthly puts carry) don't rank — same rule
    # as discord_poster.ytd_best_worst. All-hold falls back to every week.
    ranked = [w for w in weeks if not w.get("hold")] or weeks
    best  = max(ranked, key=lambda w: w.get("premium_collected", w.get("realized", 0))) if ranked else None
    worst = min(ranked, key=lambda w: w.get("premium_collected", w.get("realized", 0))) if ranked else None
    return {
        "weeks":         weeks,
        "total_premium": total,
        "weeks_traded":  len(weeks),
        "best_week":     best,
        "worst_week":    worst,
    }


def reconcile_from_xml(
    xml_str: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """Parse Flex XML and merge into ytd_tracker.json.

    XML weeks take precedence over existing data for the same week_start.
    Existing weeks not present in the XML are preserved.
    """
    fills = _parse_fills(xml_str, date_from, date_to)
    xml_ytd = _rebuild_ytd(fills)

    merged = _load_existing_weeks()
    for w in xml_ytd["weeks"]:
        merged[w["week_start"]] = w
    ytd = _finalize_ytd(merged)

    result = {
        "fills_found":   len(fills),
        "weeks_found":   len(xml_ytd["weeks"]),
        "total_premium": ytd["total_premium"],
        "weeks":         ytd["weeks"],
        "committed":     False,
    }

    if not dry_run:
        YTD_FILE.write_text(json.dumps(ytd, indent=2))
        result["committed"] = True

    return result


def _fetch_flex_xml(token: str, query_id: str) -> str:
    """Fetch statement XML from IBKR Flex Web Service."""
    import requests  # local import keeps startup fast when unused

    r1 = requests.get(
        FLEX_SEND_URL,
        params={"t": token, "q": query_id, "v": "3"},
        timeout=30,
    )
    r1.raise_for_status()

    root1 = ET.fromstring(r1.text)
    status = root1.findtext("Status") or ""
    if status != "Success":
        msg = (
            root1.findtext("ErrorMessage")
            or root1.findtext("ErrorCode")
            or "unknown error"
        )
        raise RuntimeError(f"Flex SendRequest failed: {msg}")

    ref_code = root1.findtext("ReferenceCode") or ""
    if not ref_code:
        raise RuntimeError("No ReferenceCode returned by Flex SendRequest")

    for attempt in range(15):
        time.sleep(2)
        r2 = requests.get(
            FLEX_GET_URL,
            params={"q": ref_code, "t": token, "v": "3"},
            timeout=30,
        )
        r2.raise_for_status()

        if "<FlexStatements" in r2.text:
            return r2.text

        # Still processing — IBKR returns a status XML
        try:
            root2 = ET.fromstring(r2.text)
            st2 = root2.findtext("Status") or ""
            if st2 in ("Statement generation in progress", "Processing"):
                continue
            err2 = root2.findtext("ErrorMessage") or st2
            raise RuntimeError(f"Flex GetStatement error: {err2}")
        except ET.ParseError:
            pass

    raise RuntimeError("Flex statement not ready after 30 s — try again in a moment")


def reconcile_from_flex_service(
    token: str,
    query_id: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """Fetch XML from IBKR Flex Web Service and reconcile ytd_tracker.json."""
    xml_str = _fetch_flex_xml(token, query_id)
    return reconcile_from_xml(xml_str, date_from, date_to, dry_run)
