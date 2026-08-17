"""Deployed-capital math — how much of the fund is actually committed right now.

Why this exists
---------------
On 2026-08-17 a CSP (BE 215P x2) filled after the run had already written it off,
so state.json showed 4 positions worth $240,500 of collateral while IBKR held 5
worth $283,500 — against $258,481 net liquidation. The account was ~$25k onto
margin and nothing surfaced it, because the v5.2.92 net-liq cap sized against the
same picture that was missing the position.

The lesson is that a check sharing a data source with the thing it checks is not
a check. So every caller here MUST pass rows derived from **live IBKR positions**,
never from state.json. This module is deliberately pure — no config import, no
ib_insync, no I/O — so api.py (which does not import config) can use it and so the
arithmetic is unit-testable without a broker.

What the numbers mean
---------------------
csp_collateral  Σ strike × 100 × |contracts| over open SHORT PUTS. The cash a
                cash-secured-put fund must hold against them. Exact — derived
                from strikes, so it never depends on market data being alive.

stock_value     Σ shares × avg_cost over long stock: the cash actually SPENT
                acquiring it. Cost basis rather than market value is deliberate.
                Margin is a question about cash outlay, not current valuation,
                and cost basis cannot go stale or blank when the market-data farm
                drops (which on these boxes it does).

total_deployed  csp_collateral + stock_value.

on_margin       csp_collateral > cash. This is the PRECISE test: stock is already
                paid for, so the live question is whether the remaining cash can
                secure the puts. total_deployed/net_liq is the headline gauge, but
                this boolean is what should drive an alert.
"""


CONCENTRATION_WARN_PCT = 50.0   # one name past half the deployment is worth a look


def compute_deployed(rows: list, cash: float = None, net_liq: float = None,
                     park_symbol: str = None) -> dict:
    """Summarise committed capital from normalized broker position rows.

    rows: dicts with symbol, sec_type ("OPT"/"STK"), right ("P"/"C"/None),
          strike, position (signed), avg_cost (per share).
    cash / net_liq: from the broker's account summary; optional, and the derived
          ratio/flag are simply omitted when they are not supplied.
    park_symbol: the cash-sweep instrument (QQQ/SGOV) when a park is open. Its
          shares are real deployed capital but they are NOT wheel stock backing a
          covered call — they are idle cash parked until Friday. Bucketed
          separately so the CC-backed figure means what it says.

    `positions` breaks the total down per slot, largest first, each with its share
    of the deployment, so a single name quietly becoming half the fund is visible.
    """
    park_symbol    = (park_symbol or "").upper() or None
    csp_collateral = 0.0
    stock_value    = 0.0
    park_value     = 0.0
    csp_count = cc_count = stock_count = 0
    slots: list = []

    for r in rows or []:
        sec    = (r.get("sec_type") or "").upper()
        right  = (r.get("right") or "").upper()
        qty    = float(r.get("position") or 0)
        symbol = (r.get("symbol") or "?").upper()
        if sec == "OPT" and qty < 0:
            if right.startswith("P"):
                strike = float(r.get("strike") or 0)
                cap    = strike * 100.0 * abs(qty)
                csp_collateral += cap
                csp_count += 1
                slots.append({"symbol": symbol, "kind": "CSP", "capital": round(cap, 2),
                              "contracts": int(abs(qty)), "strike": strike})
            elif right.startswith("C"):
                # A covered call ties up no additional capital — the shares back
                # it — but the count is worth surfacing next to the stock value.
                cc_count += 1
        elif sec == "STK" and qty > 0:
            cap = qty * float(r.get("avg_cost") or 0)
            if park_symbol and symbol == park_symbol:
                park_value += cap
                slots.append({"symbol": symbol, "kind": "PARK", "capital": round(cap, 2),
                              "shares": qty})
            else:
                stock_value += cap
                stock_count += 1
                slots.append({"symbol": symbol, "kind": "STK", "capital": round(cap, 2),
                              "shares": qty})

    total = csp_collateral + stock_value + park_value
    slots.sort(key=lambda s: s["capital"], reverse=True)
    for s in slots:
        s["pct_of_total"] = round(s["capital"] / total * 100, 1) if total else 0.0

    top_pct = slots[0]["pct_of_total"] if slots else 0.0
    out = {
        "csp_collateral": round(csp_collateral, 2),
        "stock_value":    round(stock_value, 2),
        "park_value":     round(park_value, 2),
        "total_deployed": round(total, 2),
        "csp_count":      csp_count,
        "cc_count":       cc_count,
        "stock_count":    stock_count,
        "positions":      slots,
        "top_symbol":       slots[0]["symbol"] if slots else None,
        "top_pct":          top_pct,
        "concentrated":     top_pct > CONCENTRATION_WARN_PCT,
        "concentration_warn_pct": CONCENTRATION_WARN_PCT,
    }
    if cash is not None:
        out["cash"] = round(float(cash), 2)
        # The precise margin test — stock is already paid for, so what matters is
        # whether the remaining cash covers the puts.
        out["on_margin"]     = csp_collateral > float(cash)
        out["cash_shortfall"] = round(max(0.0, csp_collateral - float(cash)), 2)
    if net_liq:
        out["net_liq"] = round(float(net_liq), 2)
        out["deployed_pct"] = round(total / float(net_liq) * 100, 1)
    return out


def from_ib_positions(positions) -> list:
    """Adapt ib_insync Position objects (ib.positions()) to compute_deployed rows."""
    rows = []
    for p in positions or []:
        c = getattr(p, "contract", None)
        if c is None:
            continue
        rows.append({
            "symbol":   getattr(c, "symbol", "") or "",
            "sec_type": getattr(c, "secType", ""),
            "right":    getattr(c, "right", "") or "",
            "strike":   getattr(c, "strike", 0) or 0,
            "position": getattr(p, "position", 0) or 0,
            "avg_cost": getattr(p, "avgCost", 0) or 0,
        })
    return rows


def from_api_portfolio(portfolio: list) -> list:
    """Adapt api.py's portfolio dicts to compute_deployed rows.

    Note api.py already divides option avgCost by the multiplier to make it
    per-share; stock avgCost is per-share either way, and only stock avg_cost is
    consumed here, so that normalisation is harmless.
    """
    rows = []
    for p in portfolio or []:
        rows.append({
            "symbol":   p.get("symbol", ""),
            "sec_type": p.get("secType", ""),
            "right":    p.get("right") or "",
            "strike":   p.get("strike") or 0,
            "position": p.get("position") or 0,
            "avg_cost": p.get("avgCost") or 0,
        })
    return rows
