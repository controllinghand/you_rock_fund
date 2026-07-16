# CostAverage_Issue1 — True Strike-Weighted Average Cost for Covered Calls

## Context

When the same stock is assigned via two (or more) CSPs at **different strikes**, the
system needs the **true average cost per share** = Σ(strike × shares) / Σ(shares) for
covered-call decisions. Interactive Brokers (and other brokers) fold the collected CSP
premium into the displayed "Avg Price", lowering it below the real strike-weighted
average. David wants the premium-free average.

**Concrete example (AAOI):** 400 shares assigned @ $170 + 600 shares assigned @ $140.
- True strike-weighted average: (400×170 + 600×140) / 1000 = **$152.00**
- IBKR's displayed Avg Price: **$150.48** (the ~$1.52/share gap = ~$1,520 of collected premium baked in)

**Two problems found in the code:**

1. **No tranche tracking / overwrite bug** — [wheel_manager.py:571-575](wheel_manager.py:571):
   when a ticker already in `wheel_holdings` is assigned again, `detect_assignments()`
   updates only `shares` to the IBKR aggregate total and leaves `assigned_strike` at the
   **original** value. The second tranche's strike is silently lost.
2. **Premium-netted fallback** — [wheel_manager.py:581-589](wheel_manager.py:581) (and the
   adoption path at [wheel_manager.py:741](wheel_manager.py:741)): when the CSP strike is
   missing, it falls back to IBKR `avgCost`, which is exactly the premium-netted number we
   want to avoid.

`assigned_strike` is the single cost-basis reference used everywhere — CC strike selection
([wheel_manager.py:325-351](wheel_manager.py:325)), stop-loss threshold, unrealized P&L
([Dashboard.jsx:488-528](yrvi-app/src/pages/Dashboard.jsx:488), [risk_manager.py:104](risk_manager.py:104)),
and called-away P&L ([wheel_manager.py:546-549](wheel_manager.py:546)).

## Decisions (confirmed with user)

- **Scope:** use the true strike-weighted average **everywhere** (CC selection, stop-loss,
  P&L, dashboard) — one consistent "true cost" number.
- **Seeding existing holdings:** make tranches **editable in the dashboard UI**, so AAOI
  (and any current multi-tranche holding) can be corrected now and stay correct going forward.

## Approach

Keep `assigned_strike` as the **field every existing read already uses**, but make it hold
the strike-weighted average. Add a `tranches` list as the source of truth so the average is
auditable, recomputable, and user-editable. This minimizes churn — existing reads of
`assigned_strike` automatically get the true average with no call-site changes.

### Holding schema addition (`state.json` `wheel_holdings[]`)

```jsonc
"tranches": [                       // source of truth for the weighted average
  {"shares": 400, "strike": 170.0, "date": "2026-06-15"},
  {"shares": 600, "strike": 140.0, "date": "2026-06-22"}
],
"assigned_strike": 152.0            // now = round(Σ(strike×shares)/Σ(shares), 2)
```

### Code changes

**`wheel_manager.py`**
- Add helper `_avg_cost(tranches) -> float`: `round(sum(t["strike"]*t["shares"]) / sum(t["shares"]), 2)`; returns 0.0 for empty.
- Add helper `_ensure_tranches(h)`: migration shim — if a holding lacks `tranches`, synthesize a single tranche `[{shares: h["shares"], strike: h["assigned_strike"], date: h["assignment_date"]}]`. Call it wherever holdings are loaded so legacy state.json keeps working.
- `detect_assignments()` ([wheel_manager.py:570-605](wheel_manager.py:570)):
  - **Existing ticker, share count increased** (new tranche assigned): compute `delta = ibkr_shares - sum(existing tranche shares)`; if `delta > 0` and this week's CSP strike is in `strike_lookup`, append `{shares: delta, strike: strike_lookup[ticker], date: today}`; recompute and set `assigned_strike = _avg_cost(tranches)`. Always update `shares` to the IBKR total. Log the blended average.
  - **New assignment** ([wheel_manager.py:590-602](wheel_manager.py:590)): seed `tranches=[{shares, strike, date}]` and set `assigned_strike` from it.
  - Keep the IBKR `avgCost` fallback **only** when the real CSP strike is unknown, and log a clear warning that the value may be premium-netted.
- Adoption path ([wheel_manager.py:731-760](wheel_manager.py:731)): seed a single tranche the same way.

**`api.py`** — add an edit endpoint mirroring `toggle_excluded` ([api.py:2339-2355](api.py:2339)):
```python
class HoldingTranches(BaseModel):
    ticker: str
    tranches: list[dict]   # [{shares, strike, date?}]

@app.post("/api/holding-tranches")
def set_holding_tranches(body: HoldingTranches):
    # load state, find wheel_holdings[ticker], validate shares/strike > 0,
    # set h["tranches"], h["assigned_strike"] = weighted avg, keep h["shares"]
    # equal to the tranche-share total, save state, return the updated holding.
```
Reuse the existing state load/save helpers used by the positions routes.

**`yrvi-app/src/pages/Dashboard.jsx`** ([Dashboard.jsx:479-555](yrvi-app/src/pages/Dashboard.jsx:479)):
- Relabel the holdings cell to show the true average, e.g. `400+600 shares @ $152.00 avg cost`.
- Add a small edit control (pencil/expand) per holding to view and edit the tranche rows
  (shares @ strike), POSTing to `/api/holding-tranches` — mirror the existing per-holding
  Exclude checkbox wiring. On success, re-fetch `/api/positions`.
- Unrealized P&L / stop-loss display need no formula change — they already read
  `assigned_strike`, which now carries the true average.

### Files touched
- `wheel_manager.py` — helpers + `detect_assignments` + adoption-path tranche logic
- `api.py` — `/api/holding-tranches` endpoint
- `yrvi-app/src/pages/Dashboard.jsx` — display label + tranche editor
- (no schema migration script needed — `_ensure_tranches` backfills lazily on load)

## Verification

1. `python -c "import ast; ast.parse(open('wheel_manager.py').read()); ast.parse(open('api.py').read())"` syntax check.
2. Unit-style check of `_avg_cost`: `[{400,170},{600,140}] → 152.00`; single tranche → that strike; empty → 0.0.
3. Legacy state.json (holding with no `tranches`) still loads — `_ensure_tranches` yields one tranche and `assigned_strike` is unchanged.
4. Simulate a second assignment: holding with 400@$170, then IBKR shows 1000 shares with this-week strike $140 in `state["positions"]` → after `detect_assignments()`, `tranches` has two rows and `assigned_strike == 152.0`.
5. From the dashboard, edit AAOI tranches to 400@$170 + 600@$140 → confirm POST persists and the holding now shows `$152.00 avg cost`; confirm the next CC strike selection uses $152 as the cost-basis floor.
6. Confirm stop-loss (`assigned_strike × 0.90`) and unrealized P&L on the dashboard reflect $152.00.

## Security / input-validation (acceptance criteria for `/api/holding-tranches`)

This endpoint writes `assigned_strike`, which directly feeds money-moving logic — stop-loss
trigger (`assigned_strike × 0.90`, [wheel_manager.py:877](wheel_manager.py:877) /
`risk_manager.py`), the force-sell-when-underwater path (`wheel_sell_when_cc_below_assigned`),
and CC strike selection. The API has no auth (localhost-only, bound to `127.0.0.1:8000`, CORS
locked to `localhost:3000`), so bad/garbage input — not injection — is the real risk. The
endpoint must validate strictly:

1. **Per-tranche validation** — every tranche requires `shares > 0` and `strike > 0`, both
   numeric and within sane bounds (integer shares; reject absurd strikes). Reject on any
   invalid tranche rather than coercing.
2. **Cap list length** — reject payloads with more than ~50 tranches (junk-payload DoS guard).
3. **Ticker must already exist** — normalize (`.strip().upper()`) and require it to be present
   in `wheel_holdings`. Edit only; never create an arbitrary holding.
4. **Share-total sanity check** — `Σ(tranche shares)` must equal the holding's current IBKR
   share count; on mismatch, reject or warn loudly — never silently rewrite the share total.

Rationale: a maliciously/accidentally high or low avg cost could make a healthy holding appear
to breach stop-loss (→ force-sell at market) or flip a "keep + write below-cost CC" into a
liquidation. No new security *exposure* is introduced (same localhost boundary as existing
endpoints, no new secrets/egress/deps; `state.json` is written via `json.dump` and the ticker
is a dict key, not a filesystem path — so no injection/traversal vectors).

## Build addenda (Sean's review — green-lit to build)

1. **Fix adjacent dead code in `detect_assignments()`.** [wheel_manager.py:616-617](wheel_manager.py:616)
   (`if new_assignments: discord_poster.post_assignment_alert(...)`) sit *after*
   `return called_away` at [:614](wheel_manager.py:614) — unreachable, so new-assignment Discord
   alerts never fire. Restructure during the rewrite so the alert actually sends (before the
   return / after the save).
2. **Round-lot-mismatch warning (acceptance criterion).** The Σ-shares sanity check won't catch
   two assignments landing between Saturday scans and collapsing into one tranche at one strike
   (totals still match; only the per-strike split is wrong). Log a loud warning when the inferred
   delta isn't a multiple of 100, and rely on the dashboard editor to correct it.
3. **One-time tranche backfill = explicit post-deploy step.** `_ensure_tranches` only seeds a
   *single* tranche for legacy holdings — wrong for anything already blended (AAOI 400@$170 /
   600@$140 would seed one tranche at the stale `assigned_strike`). After deploy, open the editor
   and type the real tranches for each existing multi-tranche holding. Note this in PR/release notes.

**Process:** David PRs → tests on his box → pings Sean. Sean does a correctness pass (touches cost
basis → drives stop-loss / force-sell on real money) before approving merge to main.

## Workflow constraint
All work targets the **YRVI_dev** dev clone via the fork+PR workflow (origin = dhookom fork,
upstream = controllinghand). Never edit the running `/Users/davidhookom/you_rock_fund` copy.
Bump `VERSION` + `yrvi-app/package.json` and open a PR to `controllinghand/you_rock_fund`
for Sean to review/merge.
