import math
from config import (
    TARGET_PER_POSITION,
    MAX_PER_POSITION,
    TOTAL_FUND_BUDGET,
    NUM_POSITIONS,
    COMPOUND_ENABLED
)

def size_position(target: dict, available_capital: float, is_last: bool = False, target_capital: float = None, no_max: bool = False) -> dict | None:
    # CC: contracts fixed by shares owned; no new capital consumed
    if target.get("action_type") == "CC":
        contracts = target.get("shares", 0) // 100
        if contracts < 1:
            print(f"  ⚠️  {target['ticker']} CC skipped — fewer than 100 shares")
            return None
        strike        = target["call_20d_strike"]
        premium       = target["call_20d_premium"]
        premium_pct   = target.get("call_20d_premium_pct", 0)
        return {
            "ticker":           target["ticker"],
            "action_type":      "CC",
            "strike":           strike,
            "premium":          premium,
            "expiry":           target["expiry"],
            "contracts":        contracts,
            "capital_used":     0.0,
            "premium_total":    contracts * premium * 100,
            "yield_pct":        premium_pct * 100,
            "delta":            target.get("call_20d_delta", 0),
            "iv_atm":           target.get("iv_atm", 0),
            "sector":           target.get("sector", ""),
            "latest_price":     target["latest_price"],
            "buffer_pct":       0.0,
            "buyzone":          target.get("buyzone_flag", False),
            "days_to_earnings": target.get("days_to_earnings"),
        }

    # CSP
    strike            = target["put_20d_strike"]
    cash_per_contract = strike * 100

    if not COMPOUND_ENABLED and cash_per_contract > MAX_PER_POSITION:
        print(f"  ⚠️  {target['ticker']} skipped — 1 contract = ${cash_per_contract:,.0f} (exceeds ${MAX_PER_POSITION:,.0f} max)")
        return None

    if cash_per_contract > available_capital:
        print(f"  ⚠️  {target['ticker']} skipped — insufficient remaining capital (${available_capital:,.0f})")
        return None

    if is_last:
        budget    = available_capital if no_max else min(available_capital, MAX_PER_POSITION)
        contracts = math.floor(budget / cash_per_contract)
    else:
        per_pos   = target_capital if target_capital is not None else TARGET_PER_POSITION
        contracts = math.floor(per_pos / cash_per_contract)
        contracts = min(contracts, math.floor(available_capital / cash_per_contract))
        if contracts < 1:
            contracts = 1

    capital_used  = contracts * cash_per_contract
    premium_total = contracts * target["put_20d_premium"] * 100
    yield_pct     = target["put_20d_premium_pct"] * 100

    return {
        "ticker":           target["ticker"],
        "action_type":      "CSP",
        "strike":           strike,
        "premium":          target["put_20d_premium"],
        "expiry":           target["expiry"],
        "contracts":        contracts,
        "capital_used":     capital_used,
        "premium_total":    premium_total,
        "yield_pct":        yield_pct,
        "delta":            target["put_20d_delta"],
        "iv_atm":           target["iv_atm"],
        "sector":           target.get("sector", ""),
        "latest_price":     target["latest_price"],
        "buffer_pct":       target["_buffer_pct"] * 100,
        "buyzone":          target.get("buyzone_flag", False),
        "days_to_earnings": target.get("days_to_earnings"),
    }

def size_all(targets: list, budget: float = None, num_positions: int = None,
             cc_targets: list = None) -> list:
    num              = num_positions if num_positions is not None else NUM_POSITIONS
    remaining_budget = budget if budget is not None else TOTAL_FUND_BUDGET
    effective_budget = remaining_budget

    # CC positions: fixed by shares held, consume no new capital
    cc_sized = []
    for t in (cc_targets or []):
        result = size_position(t, 0)
        if result:
            cc_sized.append(result)

    num_csp_slots = num - len(cc_sized)

    if COMPOUND_ENABLED and num_csp_slots > 0:
        equal_target = remaining_budget / num_csp_slots

        # Pass 1: fill slots #2–N at the equal per-slot target (skip index 0 — reserved for #1)
        rest_sized   = []
        used_tickers = set()
        target_index = 1
        while len(rest_sized) < num_csp_slots - 1 and target_index < len(targets):
            t = targets[target_index]
            target_index += 1
            result = size_position(t, remaining_budget, is_last=False, target_capital=equal_target)
            if result:
                rest_sized.append(result)
                remaining_budget -= result["capital_used"]
                used_tickers.add(t["ticker"])

        # Pass 2: slot #1 (top score) gets the remainder — no cap in compound mode
        top_sized = []
        for t in targets:
            if t["ticker"] in used_tickers:
                continue
            result = size_position(t, remaining_budget, is_last=True, no_max=True)
            if result:
                top_sized.append(result)
                remaining_budget -= result["capital_used"]
                used_tickers.add(t["ticker"])
                break
    else:
        # Non-compound: top position gets remainder first, rest get TARGET_PER_POSITION
        top_sized    = []
        used_tickers = set()
        for t in targets:
            result = size_position(t, remaining_budget, is_last=True)
            if result:
                top_sized.append(result)
                remaining_budget -= result["capital_used"]
                used_tickers.add(t["ticker"])
                break

        rest_sized   = []
        target_index = 0
        while len(rest_sized) < num - 1 and target_index < len(targets):
            t = targets[target_index]
            target_index += 1
            if t["ticker"] in used_tickers:
                continue
            result = size_position(t, remaining_budget, is_last=False)
            if result:
                rest_sized.append(result)
                remaining_budget -= result["capital_used"]
                used_tickers.add(t["ticker"])

    sized = cc_sized + top_sized + rest_sized

    print("\n💼 Position Sizing Summary")
    if COMPOUND_ENABLED and num_csp_slots > 0:
        # NOT "Max #1: $X" — in compound mode slot #1 is passed no_max=True and
        # takes the whole remainder by design (best-scoring name, deliberate
        # concentration). Printing MAX_PER_POSITION here claimed a ceiling on the
        # one slot that has none, which is exactly backwards.
        print(f"   Fund Budget: ${effective_budget:,.0f}  |  Equal Target: ${equal_target:,.0f}/pos (#2–{num_csp_slots})  |  #1: uncapped (takes remainder)")
    else:
        print(f"   Fund Budget: ${effective_budget:,.0f}  |  Target: ${TARGET_PER_POSITION:,.0f}/pos (#2–{num})  |  Max #1: ${MAX_PER_POSITION:,.0f}")
    print("=" * 65)

    total_capital = 0
    total_premium = 0

    for i, p in enumerate(sized, 1):
        bz          = "✅" if p["buyzone"] else "❌"
        atype       = p.get("action_type", "CSP")
        is_top_csp  = atype == "CSP" and i == len(cc_sized) + 1
        if is_top_csp and COMPOUND_ENABLED:
            last_tag = " ← remainder"
        elif is_top_csp:
            last_tag = " ← remainder (max $70K)"
        else:
            last_tag = ""
        over        = " ⚡" if p["capital_used"] > TARGET_PER_POSITION else ""
        capital_str = "held (no new capital)" if atype == "CC" else f"${p['capital_used']:,.0f}{over}"
        print(f"\n  #{i} {p['ticker']} [{atype}]  (Buyzone: {bz}){last_tag}")
        print(f"    Strike:      ${p['strike']:.2f}")
        print(f"    Contracts:   {p['contracts']}")
        print(f"    Capital:     {capital_str}")
        print(f"    Premium:     ${p['premium_total']:,.0f}  ({p['yield_pct']:.2f}%)")
        if atype == "CSP":
            print(f"    Buffer:      {p['buffer_pct']:.2f}%")
        total_capital += p["capital_used"]
        total_premium += p["premium_total"]

    leftover = effective_budget - total_capital
    print("\n" + "=" * 65)
    print(f"  Positions Filled:       {len(sized)} / {num}")
    print(f"  Total Capital Deployed: ${total_capital:,.0f}")
    print(f"  Undeployed Cash:        ${leftover:,.0f}")
    print(f"  Total Premium Income:   ${total_premium:,.0f}")
    if total_capital > 0:
        print(f"  Blended Weekly Yield:   {(total_premium/total_capital)*100:.2f}%")
    print("=" * 65)
    print("  ⚠️  Deltas may shift by Monday — system will auto-adjust strikes at execution time")
    print("=" * 65)

    return sized

if __name__ == "__main__":
    from screener import get_top_targets
    targets = get_top_targets(10)
    size_all(targets)
