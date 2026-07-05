"""
Optional Discord notification plugin for YRVI.
Only active when DISCORD_WEBHOOK_URL is set in .env — silently no-ops otherwise.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from config import MAX_PER_POSITION, TRADING_MODE, ACCOUNT, MODE_LABEL
from secrets_client import get_secret

load_dotenv()

WEBHOOK_URL = get_secret("discord_webhook_url", "DISCORD_WEBHOOK_URL")
YTD_FILE      = "ytd_tracker.json"
PST           = ZoneInfo("America/Los_Angeles")

_version_file = Path(__file__).parent / "VERSION"
_VERSION = f"v{_version_file.read_text().strip()}" if _version_file.exists() else "unknown"
_FOOTER  = f"You Rock Volatility Income Fund · {_VERSION} · {MODE_LABEL}"

COLOR_GREEN  = 0x2ECC71   # yield ≥ 1%
COLOR_YELLOW = 0xF1C40F   # yield 0.5–1%
COLOR_RED    = 0xE74C3C   # yield < 0.5%
COLOR_BLUE   = 0x3498DB   # preview
COLOR_PURPLE = 0x9B59B6   # assignment alert
COLOR_FIRE   = 0xFF0000   # emergency share sale


def is_enabled() -> bool:
    return bool(WEBHOOK_URL)


def _post(payload: dict) -> bool:
    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"[discord] post failed: {e}")
        return False


def _load_ytd() -> dict:
    try:
        with open(YTD_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"weeks": [], "total_premium": 0.0, "weeks_traded": 0,
                "best_week": None, "worst_week": None}


def _save_ytd(ytd: dict):
    with open(YTD_FILE, "w") as f:
        json.dump(ytd, f, indent=2)


def _update_ytd(week_start: str, premium_collected: float, shares_sold_pnl: float, fund_budget: float) -> dict:
    ytd = _load_ytd()
    entry = {
        "week_start":        week_start,
        "premium_collected": premium_collected,
        "shares_sold_pnl":   shares_sold_pnl,
        "total_realized":    round(premium_collected + shares_sold_pnl, 2),
        "yield_pct":         round(premium_collected / fund_budget * 100, 3) if fund_budget else 0,
    }
    existing_idx = next((i for i, w in enumerate(ytd["weeks"]) if w["week_start"] == week_start), None)
    if existing_idx is not None:
        ytd["weeks"][existing_idx] = entry
    else:
        ytd["weeks"].append(entry)
    ytd["total_premium"] = round(
        sum(w.get("premium_collected", w.get("realized", 0)) for w in ytd["weeks"]), 2
    )
    ytd["weeks_traded"]  = len(ytd["weeks"])
    by_premium        = sorted(ytd["weeks"], key=lambda w: w.get("premium_collected", w.get("realized", 0)))
    ytd["worst_week"]  = by_premium[0]
    ytd["best_week"]   = by_premium[-1]
    _save_ytd(ytd)
    return ytd


def _fmt_strike(v: float) -> str:
    """$41 for whole numbers, $22.50 for decimals."""
    return f"${v:.0f}" if v == int(v) else f"${v:.2f}"


def _build_trades_section(state: dict) -> tuple[str, str]:
    """
    Returns (trades_text, slippage_line) built from state executions + positions.
    Joins on ticker to get strike/screener_premium/buffer from sized positions.
    """
    executions = state.get("executions", [])
    if not executions:
        return "", ""

    pos_by_ticker = {p["ticker"]: p for p in state.get("positions", [])}

    EMOJI = {
        "filled":                "✅",
        "dry_run":               "✅",
        "partial_fill":          "✅",
        "failed":                "❌",
        "failed_qualify":        "❌",
        "failed_market_data":    "❌",
        "failed_permissions":    "🚫",
        "skipped_liquidity":     "⚠️",
        "skipped_contract_size": "⚠️",
        "unfilled":              "❌",
    }
    SKIP_LABEL = {
        "failed":                "failed — order unfilled",
        "failed_qualify":        "skipped — could not qualify contract",
        "failed_market_data":    "skipped — no market data",
        "failed_permissions":    "failed — IBKR options trading not enabled (check permissions in Client Portal)",
        "skipped_liquidity":     "skipped — spread too wide",
        "skipped_contract_size": "skipped — contract too large",
        "unfilled":              "unfilled",
    }

    lines       = []
    slip_deltas = []   # fill_price - screener_premium, per share

    for ex in executions:
        ticker     = ex.get("ticker", "?")
        status     = ex.get("status", "unknown")
        emoji      = EMOJI.get(status, "❓")
        pos        = pos_by_ticker.get(ticker)
        fill_price = ex.get("fill_price")
        contracts  = ex.get("contracts", 0)
        prem_coll  = ex.get("premium_collected", 0)
        order_type = ex.get("order_type") or ""

        strike      = pos["strike"]     if pos else None
        screener_px = pos["premium"]    if pos else None   # per share
        buffer_pct  = pos["buffer_pct"] if pos else None
        plan_delta  = pos["delta"]      if pos else None

        if status in ("filled", "dry_run", "partial_fill") and fill_price is not None:
            strike_str   = f"{_fmt_strike(strike)} strike" if strike else "? strike"
            fill_str     = f"filled @ ${fill_price:.2f}"
            screener_str = f"(screener ${screener_px:.2f})" if screener_px is not None else ""
            otype_str    = f" via {order_type}" if order_type and order_type != "limit_mid" else ""
            prem_str     = f"${prem_coll:,.0f}"
            buf_str      = f"{buffer_pct:.1f}% buffer" if buffer_pct is not None else ""
            exec_delta   = ex.get("delta_at_entry")
            if exec_delta is not None and plan_delta is not None:
                delta_str = f"δ{abs(plan_delta):.2f}→{abs(exec_delta):.2f}"
            elif exec_delta is not None:
                delta_str = f"δ{abs(exec_delta):.2f}"
            else:
                delta_str = ""

            parts = [
                f"{emoji} **{ticker}**",
                f"{strike_str}",
                f"{contracts} contracts",
                f"{fill_str} {screener_str}{otype_str}".strip(),
            ]
            if delta_str:
                parts.append(delta_str)
            parts.append(prem_str)
            if buf_str:
                parts.append(buf_str)
            lines.append("  |  ".join(parts))

            if screener_px is not None:
                slip_deltas.append(fill_price - screener_px)

        else:
            label = SKIP_LABEL.get(status, status)
            if status == "skipped_liquidity":
                reason = ex.get("reason")
                if reason == "oi":
                    oi   = ex.get("open_interest")
                    notl = ex.get("oi_notional")
                    if oi is not None and notl is not None:
                        label = f"skipped — open interest too thin (OI {oi:.0f}, ${notl:,.0f} notional)"
                    elif oi is not None:
                        label = f"skipped — open interest too thin (OI {oi:.0f})"
                    else:
                        label = "skipped — open interest too thin"
                elif ex.get("spread_pct") is not None:
                    if reason == "spread_illiquid":
                        label = f"skipped — spread too wide (illiquid) ({ex['spread_pct']*100:.1f}%)"
                    elif reason == "spread_low_yield":
                        label = f"skipped — spread too wide, low yield ({ex['spread_pct']*100:.1f}%)"
                    elif reason == "spread_low_yield_unfilled":
                        label = f"skipped — spread too wide, limit unfilled ({ex['spread_pct']*100:.1f}%)"
                    else:
                        label = f"skipped — spread too wide ({ex['spread_pct']*100:.1f}%)"
            strike_str = f"{_fmt_strike(strike)} strike  |  " if strike is not None else ""
            lines.append(f"{emoji} **{ticker}**  |  {strike_str}{label}")

    trades_text = "\n".join(lines) if lines else "No executions recorded."

    slippage_line = ""
    if slip_deltas:
        avg  = sum(slip_deltas) / len(slip_deltas)
        sign = "+" if avg >= 0 else "-"
        slippage_line = f"💹 Avg fill vs screener: {sign}${abs(avg):.2f} per contract"

    statuses = {ex.get("status") for ex in executions}
    footnotes = []
    if "skipped_liquidity" in statuses:
        skip_exs = [ex for ex in executions if ex.get("status") == "skipped_liquidity"]
        reasons  = {ex.get("reason") for ex in skip_exs}
        sample   = next((ex for ex in skip_exs if ex.get("max_spread_pct") is not None), {})
        max_spread    = sample.get("max_spread_pct",       0.20)
        min_bid_yield = sample.get("min_bid_yield_pct",    0.01)
        hard_cap      = sample.get("max_spread_hard_cap",  0.50)
        if "spread_illiquid" in reasons:
            footnotes.append(
                f"* Spread too wide (illiquid) = spread > {hard_cap*100:.0f}% of mid price (skipped regardless of yield)"
            )
        if "spread_low_yield" in reasons:
            footnotes.append(
                f"* Spread too wide, low yield = spread > {max_spread*100:.0f}% AND mid yield < {min_bid_yield*100:.2f}% of strike"
            )
        if "spread_low_yield_unfilled" in reasons:
            footnotes.append(
                "* Spread too wide, limit unfilled = mid yield qualified but no fill at limit price"
            )
        if "oi" in reasons:
            sample_oi       = next((ex for ex in skip_exs if ex.get("min_oi_notional") is not None), {})
            min_oi_notional = sample_oi.get("min_oi_notional", 1_000_000)
            footnotes.append(
                f"* Open interest too thin = OI × strike × 100 < ${min_oi_notional:,.0f} notional (price-neutral liquidity floor)"
            )
    if "skipped_contract_size" in statuses:
        footnotes.append(f"* Contract too large = single contract exceeds ${MAX_PER_POSITION:,.0f} max position size")
    footnote_block = "\n" + "\n".join(footnotes) if footnotes else ""

    # Discord field value cap is 1024 chars
    if slippage_line:
        combined = trades_text + "\n" + slippage_line
        if len(combined) > 1024:
            trades_text = trades_text[:1020 - len(slippage_line)] + "…"
        trades_text = trades_text + "\n" + slippage_line

    if footnote_block:
        if len(trades_text) + len(footnote_block) > 1024:
            trades_text = trades_text[:1024 - len(footnote_block) - 1] + "…"
        trades_text = trades_text + footnote_block

    return trades_text, slippage_line


def _yield_color(yield_pct: float) -> int:
    if yield_pct >= 1.0:
        return COLOR_GREEN
    elif yield_pct >= 0.5:
        return COLOR_YELLOW
    return COLOR_RED


def _yield_emoji(yield_pct: float) -> str:
    if yield_pct >= 1.0:
        return "🟢"
    elif yield_pct >= 0.5:
        return "🟡"
    return "🔴"


def wheel_plan_totals(wheel_plan: list) -> tuple:
    """Sum the capital and premium of the newly-written covered calls in a plan.

    CC capital uses each holding's cost basis (assigned_strike × shares) — the same
    base the per-holding income % is computed against — so a combined blended yield
    (CSP + CC) pairs each premium with the capital that actually earned it. Only
    `cc_opened` rows count: already-covered / deferred / sold holdings contribute no
    new premium this week, so including their capital would understate the yield.
    Returns (cc_capital, cc_premium).
    """
    cc_capital = 0.0
    cc_premium = 0.0
    for a in wheel_plan or []:
        if a.get("action") != "cc_opened":
            continue
        cc_premium += a.get("cc_premium", 0.0) or 0.0
        cc_capital += (a.get("assigned_strike") or 0.0) * (a.get("shares", 0) or 0)
    return round(cc_capital, 2), round(cc_premium, 2)


def _wheel_plan_lines(wheel_plan: list) -> list:
    """Render wheel-check decisions as Discord lines, mirroring the This Week
    'Monday Wheel Plan' rows (CC opened / deferred / already-covered / sold)."""
    lines = []
    for a in wheel_plan or []:
        action = a.get("action", "")
        ticker = a.get("ticker", "?")
        if action == "cc_opened":
            prem   = a.get('cc_premium', 0)
            strike = a.get('cc_strike')
            cost   = a.get('assigned_strike')
            shares = a.get('shares', 0)
            yield_str = ""
            if cost and shares:
                yield_str = f" | {prem / (cost * shares) * 100:.2f}% income"
            cost_str = ""
            if a.get("below_assigned") and cost and strike:
                cost_str = f" | ⚠️ below cost ${cost:.2f} ({(strike - cost) / cost * 100:.1f}%)"
            lines.append(
                f"✅ **{ticker}** — Write CC @ {_fmt_strike(strike)} | "
                f"δ{a.get('cc_delta', 0):.2f} | ~${prem:,.0f} | exp {a.get('cc_expiry', '?')}"
                f"{yield_str}{cost_str}"
            )
        elif action == "cc_deferred":
            lines.append(
                f"⏳ **{ticker}** — CC could not be priced (deferred); "
                f"{a.get('shares', '')} sh kept, no sale"
            )
        elif action in ("cc_already_open", "held_covered"):
            exp = a.get("cc_expiry")
            lines.append(f"♻️ **{ticker}** — Already covered by open CC"
                         + (f" (exp {exp})" if exp else "") + " — skip")
        elif action == "cc_failed":
            lines.append(f"⚠️ **{ticker}** — CC could not be priced @ {_fmt_strike(a.get('cc_strike'))}")
        elif isinstance(action, str) and action.startswith("sold"):
            reason = action.replace("sold_", "").replace("_", " ") or "sold"
            lines.append(
                f"📤 **{ticker}** — Sell {a.get('shares', '')} sh ({reason}) | "
                f"~${a.get('proceeds', 0):,.0f} proceeds | P&L ${a.get('realized_pnl', 0):,.0f}"
            )
        else:
            lines.append(f"• **{ticker}** — {action}")
    return lines


def post_weekly_plan(positions: list, wheel_plan: list = None,
                     freed_capital: float = 0.0, cc_premium: float = 0.0):
    """Post Saturday evening weekly trading plan to Discord.

    wheel_plan (optional): wheel-check decisions from run_monday's dry-run, shown
    as a 'Monday Wheel Plan' section so Discord mirrors the This Week dashboard.
    """
    if not WEBHOOK_URL:
        return

    from datetime import timedelta
    now = datetime.now(PST)
    days_to_monday = (7 - now.weekday()) % 7 or 7
    next_monday = (now + timedelta(days=days_to_monday)).strftime("%b %d, %Y")
    acct_suffix = f"···{ACCOUNT[-4:]}" if ACCOUNT and len(ACCOUNT) >= 4 else ACCOUNT
    mode_label = f"🔴 LIVE {acct_suffix}" if TRADING_MODE == "live" else f"📄 Paper {acct_suffix}"

    lines = []
    total_capital = 0.0
    total_premium = 0.0
    for i, p in enumerate(positions[:5], 1):
        atype         = p.get("action_type", "CSP")
        strike        = p["strike"]
        contracts     = p["contracts"]
        capital_used  = p["capital_used"]
        buffer_pct    = p["buffer_pct"]
        premium_total = p["premium_total"]
        yield_pct     = p["yield_pct"]
        dte           = p.get("days_to_earnings")

        earn_str = f" | Earnings: {dte} days" if dte is not None and dte > 0 else ""
        if atype == "CC":
            capital_str  = "held"
            detail_str   = f"　　CC on {contracts * 100} shares | Premium: ${premium_total:,.0f} ({yield_pct:.2f}%){earn_str}"
        else:
            capital_str  = f"${capital_used:,.0f}"
            detail_str   = (f"　　Buffer: {buffer_pct:.1f}% | Premium: ${premium_total:,.0f} "
                            f"({yield_pct:.2f}%){earn_str}")
        lines.append(
            f"✅ **#{i} {p['ticker']}** | {atype} Strike {_fmt_strike(strike)} | "
            f"{contracts} contracts | {capital_str}\n"
            + detail_str
        )
        total_capital += capital_used
        total_premium += premium_total

    # Roll the covered calls into the top-line so the summary is the whole plan
    # (CSP + CC), not CSP-only. Pure-CSP weeks are unchanged (cc_* = 0).
    cc_capital, _ = wheel_plan_totals(wheel_plan)
    combined_capital = total_capital + cc_capital
    combined_premium = total_premium + cc_premium
    combined_yield   = (combined_premium / combined_capital * 100) if combined_capital else 0.0
    run_time = now.strftime("%I:%M %p %Z").lstrip("0")

    fields = [
        {"name": "Capital Deployed", "value": f"${combined_capital:,.0f}", "inline": True},
        {"name": "Est. Premium",     "value": f"${combined_premium:,.0f}", "inline": True},
        {"name": "Blended Yield",    "value": f"{combined_yield:.2f}%",     "inline": True},
    ]

    # Monday Wheel Plan — mirrors the This Week dashboard section so Discord
    # shows the same CC / defer / sell decisions for held positions.
    wheel_lines = _wheel_plan_lines(wheel_plan)
    if wheel_lines:
        body = "\n".join(wheel_lines)
        if len(body) > 1024:                      # Discord field value hard cap
            body = body[:1000].rsplit("\n", 1)[0] + "\n… (truncated)"
        fields.append({
            "name":  f"🗓️ Monday Wheel Plan  ·  CC ${cc_premium:,.0f} · Freed ${freed_capital:,.0f}",
            "value": body,
            "inline": False,
        })

    fields.append({"name": "​", "value": "Results posted Monday after execution ✅",
                   "inline": False})

    _post({"embeds": [{
        "title":       f"📋 YRVI Week of {next_monday} — Trading Plan [{mode_label}]",
        "description": "\n".join(lines) if lines else "No CSP positions sized.",
        "color":       0x0099FF,
        "fields":      fields,
        "footer":    {"text": f"Screener run {run_time} · {_VERSION} · {mode_label}"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]})


def _goal_lines(ytd_total: float, capital: float, goal_pct: float,
                net_liq: float = None, bold: bool = False) -> list:
    """Two-goal status block for the weekly posts:
      • Premium Goal — gross income collected vs the annual target (capital × goal_pct)
      • Account Value — live Net Liq vs (capital + target), with the +/- growth
    The account line is omitted when net_liq is unavailable (e.g. IBKR down).
    `bold` wraps the labels in ** for the rich-embed (results) post.
    """
    b = "**" if bold else ""
    annual_target  = capital * goal_pct
    account_target = capital * (1 + goal_pct)
    prem_pct = (ytd_total / annual_target * 100) if annual_target else 0
    lines = [
        f"{b}Premium Goal:{b} {prem_pct:.1f}% — "
        f"${ytd_total:,.0f} / ${annual_target:,.0f} ({goal_pct * 100:.0f}%/yr)"
    ]
    if net_liq is not None and capital:
        growth     = net_liq - capital
        growth_pct = growth / capital * 100
        sign       = "+" if growth >= 0 else "−"
        emoji      = "🟢" if growth >= 0 else "🔴"
        lines.append(
            f"{b}Account Value:{b} {emoji} ${net_liq:,.0f} / ${account_target:,.0f} "
            f"({sign}${abs(growth):,.0f}, {sign}{abs(growth_pct):.1f}% vs ${capital:,.0f} capital)"
        )
    return lines


def post_weekly_results(state: dict, fund_budget: float = 250_000,
                         capital: float = None, goal_pct: float = 0.24,
                         net_liq: float = None):
    """Post rich embed after Monday CSP execution completes."""
    if not WEBHOOK_URL:
        return

    pnl             = state.get("weekly_pnl", {})
    week_start      = pnl.get("week_start", datetime.now(PST).strftime("%Y-%m-%d"))
    csp_premium     = pnl.get("csp_premium", 0.0)
    cc_premium      = pnl.get("cc_premium", 0.0)
    shares_sold_pnl = pnl.get("shares_sold_pnl", 0.0)
    total_realized  = pnl.get("total_realized", 0.0)

    premium_collected = csp_premium + cc_premium
    # Goal is anchored to contributed capital (static fund_budget setting), not
    # the compound deployment budget — keep the two from drifting apart.
    if capital is None:
        capital = fund_budget
    # Yield denominator = the week's deployment budget. On a zero-CSP-slot week
    # the caller passes fund_budget=0 (no CSPs were sized), which would zero the
    # yield and paint the whole post red — title 🔴, red sidebar, 0.00% — despite
    # CC premium having been collected. Fall back to net_liq, then contributed
    # capital, so the yield reflects reality and matches the dashboard.
    denom = fund_budget or net_liq or capital or 0
    yield_pct = premium_collected / denom * 100 if denom else 0
    ytd       = _update_ytd(week_start, premium_collected, shares_sold_pnl, denom)

    avg_yield    = (ytd["total_premium"] / ytd["weeks_traded"] / denom * 100) \
                   if ytd["weeks_traded"] and denom else 0

    fields = [
        {"name": "CSP Premium",      "value": f"${csp_premium:,.0f}",     "inline": True},
        {"name": "CC Premium",       "value": f"${cc_premium:,.0f}",      "inline": True},
        {"name": "Shares Sold P&L",  "value": f"${shares_sold_pnl:,.0f}", "inline": True},
        {"name": "Week Yield",       "value": f"{yield_pct:.2f}%",        "inline": True},
        {"name": "Total Realized",   "value": f"${total_realized:,.0f}",  "inline": True},
        {"name": "​",           "value": "​",                   "inline": True},
    ]

    trades_text, _ = _build_trades_section(state)
    if trades_text:
        fields.append({"name": "📋 This Week's Trades", "value": trades_text, "inline": False})

    # Wheel activity summary (shares sold + CCs opened this Monday)
    wheel_activity = state.get("monday_context", {}).get("wheel_activity", [])
    if wheel_activity:
        activity_lines = []
        for a in wheel_activity:
            ticker = a.get("ticker", "?")
            action = a.get("action", "")
            if action == "cc_opened":
                prem   = a.get("cc_premium", 0)
                strike = a.get("cc_strike", 0)
                delta  = a.get("cc_delta", 0)
                cost   = a.get("assigned_strike")
                shares = a.get("shares", 0)
                # Income yield = premium / capital tied up in the stock (cost × shares).
                yield_str = ""
                if cost and shares:
                    yld = prem / (cost * shares) * 100
                    yield_str = f"  ({yld:.2f}% income)"
                # Flag below-cost CCs and show the cost basis + gap.
                cost_str = ""
                if a.get("below_assigned") and cost:
                    gap = (strike - cost) / cost * 100
                    cost_str = f"  ⚠️ below cost ${cost:.2f} ({gap:.1f}%)"
                activity_lines.append(
                    f"🔄 **{ticker}** CC @ ${strike:.2f}  δ{delta:.2f}  "
                    f"${prem:,.0f} premium{yield_str}{cost_str}"
                )
            elif action == "sold_earnings_this_week":
                dte     = a.get("days_to_earnings", "?")
                pnl_val = a.get("realized_pnl", 0)
                sign    = "+" if pnl_val >= 0 else ""
                activity_lines.append(
                    f"🚨 **{ticker}** sold — earnings this week ({dte} days)  "
                    f"{sign}${pnl_val:,.0f} P&L"
                )
            elif action == "sold_dropped_screener":
                pnl_val = a.get("realized_pnl", 0)
                sign    = "+" if pnl_val >= 0 else ""
                activity_lines.append(
                    f"📤 **{ticker}** sold (dropped from screener)  "
                    f"{sign}${pnl_val:,.0f} P&L"
                )
            elif action == "sold_no_viable_cc":
                pnl_val = a.get("realized_pnl", 0)
                sign    = "+" if pnl_val >= 0 else ""
                activity_lines.append(
                    f"📤 **{ticker}** sold (no 0.20-delta CC available)  "
                    f"{sign}${pnl_val:,.0f} P&L"
                )
            elif action == "cc_failed":
                activity_lines.append(f"⚠️ **{ticker}** CC order failed — no CC this week")
        if activity_lines:
            fields.append({
                "name":   "🔄 Wheel Activity",
                "value":  "\n".join(activity_lines)[:1024],
                "inline": False
            })

    best  = ytd.get("best_week")
    worst = ytd.get("worst_week")
    ytd_lines = [
        f"**Total Premium:** ${ytd['total_premium']:,.0f}",
        f"**Weeks Traded:** {ytd['weeks_traded']}",
        f"**Avg Yield/Week:** {avg_yield:.2f}%",
        *_goal_lines(ytd["total_premium"], capital, goal_pct, net_liq, bold=True),
    ]
    def _week_yield(week: dict, prem: float) -> float:
        # Legacy week entries predate the yield_pct field — fall back to
        # computing it from the week's premium so the post never crashes.
        if "yield_pct" in week:
            return week["yield_pct"]
        return prem / denom * 100 if denom else 0

    if best:
        best_prem = best.get("premium_collected", best.get("realized", 0))
        ytd_lines.append(f"**Best Week:** ${best_prem:,.0f} ({_week_yield(best, best_prem):.2f}%)")
    if worst and best and worst["week_start"] != best["week_start"]:
        worst_prem = worst.get("premium_collected", worst.get("realized", 0))
        ytd_lines.append(f"**Worst Week:** ${worst_prem:,.0f} ({_week_yield(worst, worst_prem):.2f}%)")
    fields.append({"name": "📊 YTD Stats", "value": "\n".join(ytd_lines), "inline": False})

    holdings = [h for h in state.get("wheel_holdings", []) if h.get("shares", 0) > 0]
    if holdings:
        lines = []
        for h in holdings:
            strike = h.get("assigned_strike", h.get("assignment_strike", 0))
            cc_str = f"CC {h.get('cc_status', '?')}"
            if h.get("current_cc_strike"):
                cc_str = (f"CC @ ${h['current_cc_strike']:.2f}  "
                          f"[{h.get('cc_status', '?')}]")
            lines.append(
                f"• **{h['ticker']}** {h['shares']} shares "
                f"@ ${strike:.2f}  wk {h.get('weeks_held', 0)}  {cc_str}"
            )
        fields.append({"name": "🔄 Wheel Holdings", "value": "\n".join(lines), "inline": False})

    _post({"embeds": [{
        "title":     f"{_yield_emoji(yield_pct)} YRVI Week of {week_start} — "
                     f"${total_realized:,.0f} realized ({yield_pct:.2f}%)",
        "color":     _yield_color(yield_pct),
        "fields":    fields,
        "footer":    {"text": _FOOTER},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]})


def post_preview(positions: list, budget: float):
    """Post 9:50AM Monday pre-execution preview."""
    if not WEBHOOK_URL:
        return

    now       = datetime.now(PST)
    lines     = []
    est_total = 0.0
    for i, p in enumerate(positions[:5], 1):
        prem       = p.get("premium_total", 0)
        est_total += prem
        bz         = " ✅" if p.get("buyzone") else ""
        lines.append(
            f"{i}. **{p['ticker']}** ${p['strike']:.0f} put · "
            f"{p['contracts']}x · exp {p.get('expiry', '?')} · "
            f"~${prem:,.0f} prem ({p.get('yield_pct', 0):.2f}%){bz}"
        )

    _post({"embeds": [{
        "title":       f"📋 YRVI Preview — {now.strftime('%A %b %d')} (executing in ~10 min)",
        "description": "\n".join(lines) if lines else "No positions sized.",
        "color":       COLOR_BLUE,
        "fields": [
            {"name": "Budget",            "value": f"${budget:,.0f}",     "inline": True},
            {"name": "Positions",         "value": str(len(positions)),   "inline": True},
            {"name": "Est. Total Prem.",  "value": f"~${est_total:,.0f}", "inline": True},
        ],
        "footer":    {"text": _FOOTER},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]})


def post_emergency_share_sale(result: dict):
    """🚨 Immediate alert fired every time wheel shares are sold at market."""
    if not WEBHOOK_URL:
        return

    ticker     = result.get("ticker", "?")
    shares     = result.get("shares", 0)
    fill_price = result.get("fill_price") or 0.0
    proceeds   = result.get("proceeds") or 0.0
    reason     = result.get("reason", "unknown")
    realized   = result.get("realized_pnl")

    reason_labels = {
        "dropped_screener":   "Dropped from screener",
        "earnings_this_week": "Earnings this week",
        "no_viable_cc":       "No viable CC (delta < 0.20)",
    }
    reason_str = reason_labels.get(reason, reason)

    if realized is not None:
        sign    = "+" if realized >= 0 else ""
        pnl_str = f"{sign}${realized:,.0f}"
    else:
        pnl_str = "N/A"

    _post({
        "content": f"🚨🚨🚨 **EMERGENCY SHARE SALE — {ticker}** 🚨🚨🚨",
        "embeds": [{
            "title":       f"🚨🚨🚨 EMERGENCY SHARE SALE: {ticker} — {reason_str}",
            "description": f"**{shares:,} shares of {ticker}** sold at market",
            "color":       COLOR_FIRE,
            "fields": [
                {"name": "Shares",       "value": f"{shares:,}",         "inline": True},
                {"name": "Fill Price",   "value": f"${fill_price:.2f}",  "inline": True},
                {"name": "Proceeds",     "value": f"${proceeds:,.0f}",   "inline": True},
                {"name": "Realized P&L", "value": pnl_str,               "inline": True},
                {"name": "Reason",       "value": reason_str,            "inline": True},
            ],
            "footer":    {"text": f"{_FOOTER} — SHARES SOLD"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
    })


def post_weekly_review(state: dict, called_away: list, new_assignments: list,
                        fund_budget: float = 250_000, capital: float = None,
                        goal_pct: float = 0.24, net_liq: float = None):
    """
    Post Friday end-of-week summary — how every position resolved.
    Replaces the individual assignment + called-away alerts with one rich embed.
    """
    if not WEBHOOK_URL:
        return

    pnl            = state.get("weekly_pnl", {})
    week_start     = pnl.get("week_start", datetime.now(PST).strftime("%Y-%m-%d"))
    csp_premium    = pnl.get("csp_premium", 0.0)
    cc_premium     = pnl.get("cc_premium", 0.0)
    shares_sold_pnl = pnl.get("shares_sold_pnl", 0.0)
    total_realized  = pnl.get("total_realized", 0.0)

    # Called-away stock P&L happens Friday — not yet in weekly_pnl
    called_away_stock_pnl = sum(h.get("_stock_pnl", 0.0) for h in called_away)
    grand_total = total_realized + called_away_stock_pnl
    yield_pct   = (csp_premium + cc_premium) / fund_budget * 100 if fund_budget else 0

    # Position lookup for strike info
    pos_lookup = {p["ticker"]: p for p in state.get("positions", [])}

    # Split filled executions into expired-worthless vs assigned
    assigned_tickers = {h["ticker"] for h in new_assignments}
    filled_execs     = [e for e in state.get("executions", [])
                        if e.get("status") in ("filled", "partial_fill")]
    expired_execs    = [e for e in filled_execs if e["ticker"] not in assigned_tickers]

    # ── Outcome lines ──────────────────────────────────────────────
    outcome_lines = []

    for e in expired_execs:
        pos    = pos_lookup.get(e["ticker"], {})
        strike = pos.get("strike", 0.0)
        prem   = e.get("premium_collected", 0.0)
        outcome_lines.append(
            f"✅ **{e['ticker']}** expired worthless  "
            f"{_fmt_strike(strike)} strike  ${prem:,.0f} kept"
        )

    for h in new_assignments:
        exec_rec = next((e for e in filled_execs if e["ticker"] == h["ticker"]), {})
        prem     = exec_rec.get("premium_collected", 0.0)
        outcome_lines.append(
            f"📬 **{h['ticker']}** assigned @ {_fmt_strike(h['assigned_strike'])}  "
            f"{h['shares']} shares  ${prem:,.0f} premium collected"
        )

    for h in called_away:
        cc_strike = h.get("current_cc_strike") or h.get("assigned_strike", 0.0)
        stock_pnl = h.get("_stock_pnl", 0.0)
        cc_prem   = h.get("current_cc_premium", 0.0)
        if stock_pnl > 0:
            shares_str = f"+${stock_pnl:,.0f} on shares"
        elif stock_pnl < 0:
            shares_str = f"-${abs(stock_pnl):,.0f} on shares"
        else:
            shares_str = "break-even on shares"
        outcome_lines.append(
            f"📤 **{h['ticker']}** called away @ {_fmt_strike(cc_strike)}  "
            f"{shares_str}  · ${cc_prem:,.0f} CC already collected"
        )

    # ── Fields ────────────────────────────────────────────────────
    # Discord renders 3 inline fields per row.
    # Row 1: CSP / CC / Total Realized  Row 2: Week Yield + spacers
    fields = [
        {"name": "CSP Premium",    "value": f"${csp_premium:,.0f}",  "inline": True},
        {"name": "CC Premium",     "value": f"${cc_premium:,.0f}",   "inline": True},
        {"name": "Total Realized", "value": f"${grand_total:,.0f}",  "inline": True},
        {"name": "Week Yield",     "value": f"{yield_pct:.2f}%",     "inline": True},
        {"name": "​",         "value": "​",                "inline": True},
        {"name": "​",         "value": "​",                "inline": True},
    ]

    if outcome_lines:
        fields.append({
            "name":   "📋 Week Outcomes",
            "value":  "\n".join(outcome_lines),
            "inline": False,
        })

    # Ongoing holdings (held from before, not assigned or called away this week)
    called_away_tickers = {h["ticker"] for h in called_away}
    ongoing = [h for h in state.get("wheel_holdings", [])
               if h["ticker"] not in assigned_tickers
               and h["ticker"] not in called_away_tickers]
    if ongoing:
        hold_lines = []
        for h in ongoing:
            cc_str = (f"  CC @ {_fmt_strike(h['current_cc_strike'])} wk {h['weeks_held']}"
                      if h.get("current_cc_strike") else "  (pending CC)")
            hold_lines.append(
                f"🔄 **{h['ticker']}** {h['shares']} shares "
                f"@ {_fmt_strike(h['assigned_strike'])}{cc_str}"
            )
        fields.append({
            "name":   "🔄 Ongoing Wheel Holdings",
            "value":  "\n".join(hold_lines),
            "inline": False,
        })

    # YTD — read-only (already updated Monday after CSP execution)
    ytd = _load_ytd()
    if ytd.get("weeks_traded"):
        if capital is None:
            capital = fund_budget
        avg_yield  = ytd["total_premium"] / ytd["weeks_traded"] / fund_budget * 100 if fund_budget else 0
        goal_block = "\n".join(_goal_lines(ytd["total_premium"], capital, goal_pct, net_liq))
        fields.append({
            "name": "📊 YTD Stats",
            "value": (
                f"Total Premium: ${ytd['total_premium']:,.0f}\n"
                f"Weeks Traded: {ytd['weeks_traded']}\n"
                f"Avg Yield/Week: {avg_yield:.2f}%\n"
                f"{goal_block}"
            ),
            "inline": False,
        })

    # Week label e.g. "May 18–22"
    try:
        from datetime import date, timedelta
        ws         = date.fromisoformat(week_start)
        week_label = f"{ws.strftime('%b %-d')}–{(ws + timedelta(days=4)).strftime('%b %-d')}"
    except Exception:
        week_label = week_start

    _post({"embeds": [{
        "title":       f"{_yield_emoji(yield_pct)} YRVI Week of {week_label} — Week in Review",
        "description": f"**${grand_total:,.0f} realized**  ({yield_pct:.2f}% yield)",
        "color":       _yield_color(yield_pct),
        "fields":      fields,
        "footer":      {"text": _FOOTER},
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    }]})


def post_called_away_alert(called_away: list):
    """Post Friday alert when CC-covered shares were called away at expiry."""
    if not WEBHOOK_URL or not called_away:
        return

    lines = []
    for h in called_away:
        ticker          = h["ticker"]
        shares          = h.get("shares", 0)
        assigned_strike = h.get("assigned_strike", 0.0)
        cc_strike       = h.get("current_cc_strike") or assigned_strike
        cc_premium      = h.get("current_cc_premium", 0.0)
        weeks_held      = h.get("weeks_held", 0)
        stock_pnl       = h.get("_stock_pnl", round((cc_strike - assigned_strike) * shares, 2))
        pnl_str         = f"+${stock_pnl:,.0f}" if stock_pnl >= 0 else f"-${abs(stock_pnl):,.0f}"
        lines.append(
            f"• **{ticker}** — {shares} shares called away @ ${cc_strike:.2f}  "
            f"(assigned ${assigned_strike:.2f})  stock P&L {pnl_str}  "
            f"CC premium ${cc_premium:,.0f}  held {weeks_held}w"
        )

    _post({"embeds": [{
        "title":       f"📤 YRVI — {len(called_away)} Position(s) Called Away",
        "description": "\n".join(lines),
        "color":       COLOR_GREEN,
        "fields": [{
            "name":   "Capital Freed",
            "value":  "Capital returns to the CSP pool — new positions sized Monday 10:00 AM",
            "inline": False,
        }],
        "footer":    {"text": _FOOTER},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]})


def post_assignment_alert(new_assignments: list):
    """Post Friday alert for newly detected stock assignments."""
    if not WEBHOOK_URL or not new_assignments:
        return

    lines = [
        f"• **{a['ticker']}** — {a['shares']} shares "
        f"@ ${a.get('assigned_strike', a.get('assignment_strike', 0)):.2f}"
        for a in new_assignments
    ]

    _post({"embeds": [{
        "title":       f"📬 YRVI — {len(new_assignments)} Assignment(s) Detected",
        "description": "\n".join(lines),
        "color":       COLOR_PURPLE,
        "fields": [{
            "name":   "Next Step",
            "value":  "Wheel check runs Monday 9:55AM — screener check + 0.20-delta covered calls",
            "inline": False,
        }],
        "footer":    {"text": _FOOTER},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]})
