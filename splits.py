"""
Stock splits on wheeled shares — detected from IBKR's own numbers (v5.2.160).

IBKR adjusts a split for us: the position goes from 100 to 800 shares and avgCost
from $760 to $95, and short options are converted by OCC. What it can't adjust is
state.json's strike-weighted `tranches` / `assigned_strike` — the P&L basis that
only this app knows. Before this module the Saturday reconcile read an 8-for-1 split
as "+700 shares assigned this week": the extra shares were blended in as a new
tranche at this week's CSP strike (or left at the pre-split strike), assigned_strike
stayed at ~8x the market, the covered-call floor ("nearest strike >= cost") found
nothing, realized P&L would book a huge fake loss, and a box with
wheel_sell_when_cc_below_assigned on would force-sell the shares at market.
(Found 2026-11-20 after SNDK's 8:1 split hit the website's paper funds.)

The signature of a split is unambiguous: the share count changes by a factor r while
IBKR's average cost changes by 1/r — the position's TOTAL cost is unchanged. A real
assignment adds shares at a new cost, so the total cost moves. No outside data needed.
"""
from __future__ import annotations

from datetime import date

# Total cost must agree within this fraction (IBKR rounds avgCost; commissions are inside it).
COST_TOLERANCE = 0.03
# A new tranche whose strike is this far from IBKR's implied per-share cost is suspicious.
SUSPICIOUS_STRIKE_FACTOR = 1.6


def detect_split(prior_shares: float, new_shares: float,
                 prior_avg_cost: float, new_avg_cost: float,
                 tol: float = COST_TOLERANCE) -> float | None:
    """Return the split ratio (new shares per old share) when the broker's numbers show a
    split, else None. Needs the previous run's IBKR avgCost (`net_cost` in state)."""
    try:
        ps, ns, pc, nc = float(prior_shares), float(new_shares), float(prior_avg_cost), float(new_avg_cost)
    except (TypeError, ValueError):
        return None
    if ps <= 0 or ns <= 0 or pc <= 0 or nc <= 0 or ns == ps:
        return None
    ratio = ns / ps
    before, after = pc * ps, nc * ns
    if abs(after - before) / before > tol:
        return None                      # total cost moved -> shares were bought/assigned, not split
    if abs(nc * ratio - pc) / pc > tol:
        return None
    return ratio


def apply_split(holding: dict, ratio: float, today: str | None = None) -> dict:
    """Scale a wheel holding in place for a split: tranche shares x ratio, strikes / ratio
    (same dollars), the open covered call's strike / ratio and contract count x ratio (OCC
    converts whole-number splits into more contracts). Returns a summary for logs/alerts."""
    today = today or date.today().isoformat()
    before_shares = sum(int(t.get("shares", 0)) for t in holding.get("tranches") or []) or int(holding.get("shares", 0))
    before_strike = float(holding.get("assigned_strike") or 0.0)
    for t in holding.get("tranches") or []:
        t["shares"] = int(round(int(t.get("shares", 0)) * ratio))
        t["strike"] = round(float(t.get("strike", 0.0)) / ratio, 4)
    tranches = holding.get("tranches") or []
    total = sum(t["shares"] for t in tranches)
    if total > 0:
        holding["assigned_strike"] = round(sum(t["strike"] * t["shares"] for t in tranches) / total, 2)
    elif before_strike:
        holding["assigned_strike"] = round(before_strike / ratio, 2)
    if holding.get("current_cc_strike"):
        holding["current_cc_strike"] = round(float(holding["current_cc_strike"]) / ratio, 4)
    whole = abs(ratio - round(ratio)) < 1e-9 and ratio >= 1
    if holding.get("cc_contracts") and whole:
        holding["cc_contracts"] = int(holding["cc_contracts"]) * int(round(ratio))
    if holding.get("net_cost"):
        holding["net_cost"] = round(float(holding["net_cost"]) / ratio, 2)
    summary = {
        "ticker": holding.get("ticker"),
        "ratio": ratio,
        "date": today,
        "shares_before": before_shares,
        "shares_after": total or int(round(before_shares * ratio)),
        "assigned_before": round(before_strike, 2),
        "assigned_after": holding.get("assigned_strike"),
    }
    holding.setdefault("splits", []).append(summary)
    return summary


def suspicious_tranche_strike(strike: float, implied_cost: float,
                              factor: float = SUSPICIOUS_STRIKE_FACTOR) -> bool:
    """True when a new tranche's CSP strike is far from what IBKR says the shares cost —
    e.g. a put that was open across a split is assigned at its PRE-split strike in state."""
    try:
        s, c = float(strike), float(implied_cost)
    except (TypeError, ValueError):
        return False
    if s <= 0 or c <= 0:
        return False
    return s / c > factor or c / s > factor
