"""VIX stress rule for monthly puts (YRVI-CSP-M).

When the VIX is above `vix_stress_level` at the Monday entry, new monthly puts
target `vix_stress_delta` (default 0.10) instead of ~0.20, so strikes sit much
further below the price in a stressed market. The 1% bid-yield floor still
applies to every put; when the VIX is high, monthly 0.10-delta puts usually
clear it because premiums are rich.

Backtest (greer_project feat/ranking-poc, thetadata_vix_pause_test.py,
2017 – Aug 2026, 5 slots): max drawdown −25% → −17%, CAGR +12.7% → +12.6%,
ending capital with 1%/month withdrawn $99k → $105k. Almost all of the
drawdown gain comes from one entry, Mon 2020-02-24, when the VIX was 17 at
Friday's close and ~25 by Monday, so this reads the LIVE VIX on the run day,
never the prior close. Pausing outright instead tested worse: it sits out the
richest premium right after a crash.

Default OFF. Monthly only — on weeklies a 0.10-delta put rarely pays 1% a week,
so the rule mostly leaves slots in cash. That is exactly what YRVI-MT 🚢 wants in a
crash, so the trend filter has its own spike variant below (mt_spike_rule).
If the VIX can't be read, the run trades normally (fail open) and says so.
"""
import logging
import math

import requests

log = logging.getLogger("trader.vix")   # child of trader → lands in trade_log.txt

DEFAULT_LEVEL = 20.0
DEFAULT_DELTA = 0.10
BAND = 0.03          # stressed delta window: target − 0.03 … target + 0.02
_YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?range=1d&interval=1m"


def _from_ibkr(ib) -> float | None:
    from ib_insync import Index
    try:
        c = Index("VIX", "CBOE", "USD")
        if not ib.qualifyContracts(c):
            return None
        t = ib.reqMktData(c, "", snapshot=False)
        ib.sleep(4)
        # last only — never t.close: that is the PRIOR session's close, the reading
        # that misses a Monday gap like 2020-02-24 (Fri 17 → Mon ~25). No last → Yahoo.
        v = t.last
        ib.cancelMktData(c)
        return float(v) if v and not math.isnan(v) and v > 0 else None
    except Exception as e:                      # market-data permission, timeout, …
        log.info(f"  VIX from IBKR unavailable: {e}")
        return None


def _from_yahoo() -> float | None:
    try:
        r = requests.get(_YAHOO, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        v = r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"]
        return float(v) if v and v > 0 else None
    except Exception as e:
        log.info(f"  VIX from Yahoo unavailable: {e}")
        return None


def read_vix(ib) -> tuple[float | None, str]:
    """(level, source). IBKR first, Yahoo as fallback; (None, "unavailable") if both fail."""
    v = _from_ibkr(ib)
    if v is not None:
        return v, "IBKR"
    v = _from_yahoo()
    if v is not None:
        return v, "Yahoo"
    return None, "unavailable"


def stress_rule(ib, settings: dict) -> dict | None:
    """The put-delta window for this run, or None when the rule is off.

    Returns {"vix", "source", "level", "stressed", "target", "min_delta", "max_delta"}.
    min/max are None when not stressed (the trader's normal window applies).
    """
    if not settings.get("vix_stress_enabled"):
        return None
    level = float(settings.get("vix_stress_level", DEFAULT_LEVEL))
    target = float(settings.get("vix_stress_delta", DEFAULT_DELTA))
    vix, source = read_vix(ib)
    stressed = vix is not None and vix > level
    rule = {"vix": vix, "source": source, "level": level, "stressed": stressed, "target": target,
            "min_delta": round(target - BAND, 3) if stressed else None,
            "max_delta": round(target + BAND - 0.01, 3) if stressed else None}
    if vix is None:
        log.warning(f"  🌡️ VIX stress rule ON but the VIX couldn't be read — trading the normal ~0.20 delta")
    elif stressed:
        log.warning(f"  🌡️ VIX {vix:.2f} ({source}) > {level:g} — monthly puts target {target:.2f} delta "
                    f"(window {rule['min_delta']:.2f}–{rule['max_delta']:.2f})")
    else:
        log.info(f"  🌡️ VIX {vix:.2f} ({source}) ≤ {level:g} — normal ~0.20 delta")
    return rule


# ── YRVI-MT 🚢 spike rule (weekly puts) ──────────────────────────
# The website's YRVI-MT fund (greer yrvi_mt_paper.vix_stress) stresses when the live
# VIX is above 1.75x the mean of its 50 prior daily closes: new puts then target
# ~0.10 delta (max 0.12) and must still pay the 1% minimum bid, which on a weekly
# put usually means none that week. A RATIO, not a level: it reacts to how fast the
# VIX jumps (a crash) rather than to a high-but-steady VIX. Fails open.
MT_RATIO, MT_LOOKBACK, MT_TARGET, MT_MAX_DELTA = 1.75, 50, 0.10, 0.12
_YAHOO_HIST = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?range=6mo&interval=1d"


def _prior_closes_ibkr(ib, today) -> list:
    from ib_insync import Index
    try:
        c = Index("VIX", "CBOE", "USD")
        if not ib.qualifyContracts(c):
            return []
        bars = ib.reqHistoricalData(c, endDateTime="", durationStr="6 M", barSizeSetting="1 day",
                                    whatToShow="TRADES", useRTH=True, formatDate=1)
        return [float(b.close) for b in bars if b.date < today and b.close and b.close > 0]
    except Exception as e:
        log.info(f"  VIX history from IBKR unavailable: {e}")
        return []


def _prior_closes_yahoo(today) -> list:
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    try:
        r = requests.get(_YAHOO_HIST, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        ny = ZoneInfo("America/New_York")
        out = []
        for ts, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]):
            d = datetime.fromtimestamp(ts, timezone.utc).astimezone(ny).date()
            if d < today and c and c > 0:
                out.append(float(c))
        return out
    except Exception as e:
        log.info(f"  VIX history from Yahoo unavailable: {e}")
        return []


def mt_spike_rule(ib) -> dict:
    """The put-delta window for a YRVI-MT run: same keys as stress_rule()."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("America/New_York")).date()
    vix, source = read_vix(ib)
    closes = _prior_closes_ibkr(ib, today) or _prior_closes_yahoo(today)
    closes = closes[-MT_LOOKBACK:]
    rule = {"vix": vix, "source": source, "level": None, "stressed": False, "target": MT_TARGET,
            "min_delta": None, "max_delta": None, "mean50": None, "ratio": None}
    if vix is None or len(closes) < MT_LOOKBACK:
        log.warning(f"  🌡️ MT VIX spike rule: "
                    f"{'VIX unreadable' if vix is None else f'only {len(closes)} prior closes'} — "
                    f"trading the normal ~0.20 delta")
        return rule
    mean = sum(closes) / len(closes)
    ratio = vix / mean
    rule.update(mean50=round(mean, 2), ratio=round(ratio, 3), level=round(MT_RATIO * mean, 2),
                stressed=ratio > MT_RATIO)
    if rule["stressed"]:
        rule.update(min_delta=round(MT_TARGET - BAND, 3), max_delta=MT_MAX_DELTA)
        log.warning(f"  🌡️ VIX {vix:.2f} ({source}) is {ratio:.2f}x its 50-day mean {mean:.2f} "
                    f"(> {MT_RATIO}x) — new puts target {MT_TARGET:.2f} delta (max {MT_MAX_DELTA:.2f})")
    else:
        log.info(f"  🌡️ VIX {vix:.2f} ({source}) is {ratio:.2f}x its 50-day mean {mean:.2f} "
                 f"(≤ {MT_RATIO}x) — normal ~0.20 delta")
    return rule
