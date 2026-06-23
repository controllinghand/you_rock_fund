import requests
from datetime import datetime, timezone
from config import RENDER_URL as URL, RENDER_SECRET as SECRET

PARAMS = {
    "secret": SECRET,
    "iv_min_atm": 0.40,
    "market_cap_min": 10000000000,
    "min_star_rating": 0,
    "expiry_days": 7,
    "min_target_premium_pct": 0.01,
    "earnings_days_hide": 7,
    "earnings_recent_hide": 0,
    "hide_red": True
}

# ── Render cold-start warm-up ─────────────────────────────────
# The ledger-sync service runs on Render's free tier, which spins the instance
# down after ~15 min idle. The screener is only hit ~twice a week, so it is
# almost always cold, and a cold boot (30–60s) would otherwise eat into the 60s
# query timeout below and make the real request fail. We poll the cheap /health
# endpoint first so boot time never counts against the data query.
_HEALTH_URL = URL.split("/api/")[0].rstrip("/") + "/health"

def _warm_up(max_wait: int = 90) -> None:
    """Block until the Render instance answers /health (or max_wait elapses).

    Best-effort: a healthy warm instance returns in <1s and this is a no-op;
    a cold instance is woken and we wait for it. Never raises — if warm-up
    fails we still attempt the real query (which has its own timeout)."""
    import time
    poll_timeout = 15
    deadline = time.time() + max_wait
    attempt  = 0
    # Stop starting new polls once the remaining budget can't fit another full
    # request timeout, so max_wait stays an honest ceiling rather than being
    # overshot by a final in-flight poll.
    while time.time() + poll_timeout <= deadline:
        attempt += 1
        try:
            r = requests.get(_HEALTH_URL, timeout=poll_timeout)
            if r.status_code == 200:
                if attempt > 1:
                    print(f"✅ Render instance warm after {attempt} health pings")
                return
        except requests.RequestException:
            pass  # cold/booting — keep polling
        time.sleep(3)
    print(f"⚠️  Render /health not ready after {max_wait}s — querying anyway")


# ── Hard filters ──────────────────────────────────────────────
MAX_DELTA           = 0.21
MIN_BUFFER_PCT      = 0.05
MIN_BUFFER_PRIORITY = 0.10
MIN_DAYS_TO_EXPIRY  = 3   # Mon→Fri = 3 UTC calendar days; 4 fails Monday execution
EARNINGS_SAFE_DAYS  = 7

def _earnings_safe(r: dict) -> tuple[bool, dict]:
    """
    Returns (is_safe, row). Earnings filtering is handled server-side via
    earnings_days_hide param; treat missing/unknown values as safe.
    Past earnings (days < 0) are safe. Within EARNINGS_SAFE_DAYS is not.
    """
    dte_e = r.get("days_to_earnings")
    if dte_e is None or dte_e == "?":
        return True, r
    if 0 <= dte_e < EARNINGS_SAFE_DAYS:
        return False, r
    return True, r


# ── Scoring ───────────────────────────────────────────────────

def score_target(row: dict) -> float:
    premium_pct  = row.get("put_20d_premium_pct", 0)
    buffer_pct   = row["_buffer_pct"]
    iv_atm       = row.get("iv_atm", 0)
    buyzone      = 1.10 if row.get("buyzone_flag") else 1.0

    buffer_bonus = 1.5 if buffer_pct >= MIN_BUFFER_PRIORITY else 1.0

    return (
        (0.50 * buffer_pct * buffer_bonus) +
        (0.35 * premium_pct * buyzone) +
        (0.15 * (iv_atm / 10))
    )

def get_top_targets(n=5, always_include: set = None):
    print(f"\n📡 Fetching CSP targets from Render API...")
    _warm_up()
    response = requests.get(URL, params=PARAMS, timeout=60)
    response.raise_for_status()

    data = response.json()
    rows = data.get("rows", [])
    print(f"✅ {len(rows)} candidates returned")

    # ── Filter 0: user-excluded tickers ───────────────────────
    # Read live (not the import-time config constant) so dashboard edits apply
    # on the next run with no restart. Excluded names never get a new CSP.
    from config import get_settings
    excluded = {t.strip().upper() for t in get_settings().get("excluded_tickers", []) if t and t.strip()}
    if excluded:
        before = len(rows)
        rows = [r for r in rows if r.get("ticker", "").upper() not in excluded]
        print(f"🚫 {len(rows)} after exclude list (removed {before - len(rows)}: {', '.join(sorted(excluded))})")

    # ── Filter 1: wheel-ready ─────────────────────────────────
    rows = [r for r in rows if r.get("wheel_fit") == "Wheel-ready"]
    print(f"🔧 {len(rows)} wheel-ready candidates")

    # ── Filter 2: must expire at least 4 days out ─────────────
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    def days_to_expiry(r):
        try:
            exp = datetime.strptime(r["expiry"], "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
            return (exp - today).days
        except:
            return 99
    before = len(rows)
    rows = [r for r in rows if days_to_expiry(r) >= MIN_DAYS_TO_EXPIRY]
    print(f"📅 {len(rows)} passed expiry filter (removed {before - len(rows)} expiring too soon)")

    # ── Filter 3: delta ≤ 0.21 ────────────────────────────────
    before = len(rows)
    rows = [r for r in rows if abs(r.get("put_20d_delta", -1)) <= MAX_DELTA]
    print(f"📐 {len(rows)} passed delta filter (removed {before - len(rows)})")

    # ── Filter 4: buffer ≥ 5% ─────────────────────────────────
    before = len(rows)
    for r in rows:
        r["_buffer_pct"] = (r["latest_price"] - r["put_20d_strike"]) / r["latest_price"]
    rows = [r for r in rows if r["_buffer_pct"] >= MIN_BUFFER_PCT]
    print(f"🛡️  {len(rows)} passed buffer filter (removed {before - len(rows)})")

    # ── Filter 5: earnings safety (fallback lookup for None/"?") ──
    before = len(rows)
    safe_rows = []
    for r in rows:
        is_safe, r = _earnings_safe(r)
        if is_safe:
            safe_rows.append(r)
        else:
            dte_e = r.get("days_to_earnings")
            print(f"⚠️  {r['ticker']} skipped — earnings unsafe ({dte_e})")
    rows = safe_rows
    if before != len(rows):
        print(f"📆 {len(rows)} passed earnings safety check (removed {before - len(rows)})")

    # ── Score ─────────────────────────────────────────────────
    for r in rows:
        r["_score"] = score_target(r)

    rows.sort(key=lambda r: r["_score"], reverse=True)

    # Always include held tickers (for CC sizing) even if below the top-N cutoff
    if always_include:
        top_tickers = {r["ticker"] for r in rows[:n]}
        extras = [r for r in rows[n:] if r["ticker"] in always_include and r["ticker"] not in top_tickers]
        top = rows[:n] + extras
    else:
        top = rows[:n]

    print(f"\n🎯 Top {n} CSP Targets — {datetime.today().strftime('%Y-%m-%d')}")
    print(f"   Scoring: 50% Buffer (1.5x ≥10%) | 35% Premium (1.1x buyzone) | 15% IV")
    print("=" * 65)

    for i, r in enumerate(top, 1):
        premium_pct = r.get("put_20d_premium_pct", 0) * 100
        buffer_pct  = r["_buffer_pct"] * 100
        score       = r["_score"] * 100
        buf_icon    = "✅" if buffer_pct >= 10 else "⚠️"
        fire        = "🔥" if r["_buffer_pct"] >= MIN_BUFFER_PRIORITY else "  "
        bz          = "✅" if r.get("buyzone_flag") else "❌"
        dte         = days_to_expiry(r)
        print(f"\n#{i} {fire} {r['ticker']} — {r.get('sector', 'N/A')}  [score: {score:.3f}]")
        print(f"   Price:       ${r['latest_price']:.2f}")
        print(f"   Strike:      ${r['put_20d_strike']:.2f}")
        print(f"   Buffer:      {buffer_pct:.2f}%  {buf_icon}")
        print(f"   Premium:     ${r['put_20d_premium']:.2f}  ({premium_pct:.2f}%)")
        print(f"   Delta:       {r['put_20d_delta']:.3f}")
        print(f"   IV ATM:      {r['iv_atm']:.3f}")
        print(f"   Expiry:      {r['expiry']}  ({dte} days)")
        print(f"   Earnings:    {r.get('days_to_earnings', '?')} days away")
        print(f"   Buyzone:     {bz}")

    print("\n" + "=" * 65)
    return top

def get_all_candidates(ignore_earnings_filter=False, market_cap_min=None,
                       retention=False) -> dict[str, dict]:
    """
    Returns a dict of ticker → metadata for tickers that pass all screener filters.
    Metadata keys: days_to_earnings (int|None), earnings_date (str|None).
    No printing — designed for programmatic use by wheel_manager and risk_manager.
    Returns an empty dict on API error so callers handle gracefully.

    ignore_earnings_filter: when True, bypasses the earnings_days_hide API param and
    the client-side _earnings_safe() check. Used by wheel_manager for CC decisions so
    holdings with earnings in 5–7 days aren't mistakenly sold as "dropped screener".
    The wheel_manager's 0–4 day earnings-this-week hard stop still applies.

    market_cap_min: when provided, overrides the entry-level market_cap_min floor.
    Used by wheel_manager for retention decisions: market cap is an *entry* criterion,
    so a held name that slips modestly below the 10B entry floor shouldn't be force-sold.
    A lower retention floor still triggers a sale if the name truly deteriorates.

    retention: when True, skips the entry-only put-delta cap (MAX_DELTA) and buffer
    floor (MIN_BUFFER_PCT). Those describe the *new* put we'd sell to open a position,
    not whether a company we already hold is still wheel-worthy. Applying them to a
    retention check force-sells held names over a borderline 20-delta (e.g. 0.219 vs
    0.21) or buffer miss. Used by wheel_manager/risk_manager for held-position checks;
    mirrors the market_cap_min retention override above.
    """
    try:
        params = dict(PARAMS)
        if ignore_earnings_filter:
            params["earnings_days_hide"] = 0
        if market_cap_min is not None:
            params["market_cap_min"] = market_cap_min
        _warm_up()
        response = requests.get(URL, params=params, timeout=60)
        response.raise_for_status()
        rows = response.json().get("rows", [])
    except Exception as e:
        print(f"⚠️  get_all_candidates: API error — {e}")
        return {}

    rows = [r for r in rows if r.get("wheel_fit") == "Wheel-ready"]

    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    def _dte(r):
        try:
            exp = datetime.strptime(r["expiry"], "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
            return (exp - today).days
        except Exception:
            return 99

    rows = [r for r in rows if _dte(r) >= MIN_DAYS_TO_EXPIRY]

    # Entry-only strike-selection filters: skipped in retention mode (see docstring).
    if not retention:
        rows = [r for r in rows if abs(r.get("put_20d_delta", -1)) <= MAX_DELTA]
        for r in rows:
            r["_buffer_pct"] = (r["latest_price"] - r["put_20d_strike"]) / r["latest_price"]
        rows = [r for r in rows if r["_buffer_pct"] >= MIN_BUFFER_PCT]

    if not ignore_earnings_filter:
        safe_rows = []
        for r in rows:
            is_safe, r = _earnings_safe(r)
            if is_safe:
                safe_rows.append(r)
        rows = safe_rows

    return {
        r["ticker"]: {
            "days_to_earnings": r.get("days_to_earnings"),
            "earnings_date":    r.get("earnings_date"),
        }
        for r in rows
    }


if __name__ == "__main__":
    get_top_targets(5)
