"""
Cash sweep — park the week's undeployed remainder in a liquid instrument.

Most brokerages sweep idle cash into a money-market fund so it's always earning.
This does the DIY version for the fund: after the Monday option workflow finishes,
buy the configured instrument (QQQ or SGOV) with the leftover cash, then liquidate
it on the last trading day of the week so the cash is back for Monday.

Two entry points, both driven by settings (feature is OFF by default):

  maybe_buy_park(csp_outcome, context, dry_run, ...)   — Monday, after CSPs execute
  sell_park(dry_run, ...)                               — end of week (Thu/Fri job)

Design guards (see the buy path):
  • Idle-cash basis = IBKR BuyingPower read LIVE at sweep time on a cash/IRA
    account (it is ELV − InitMarginReq, so open put collateral is already out of
    it); the CSP budget remainder − committed collateral on a margin account.
    A preview subtracts the planned CSP capital and adds the sales the wheel check
    only intends to make.
  • Buy amount = min(idle(+premium), spendable cash [, 10% of net-liq]). The
    cash cap means it can NEVER reach into margin. The 10% net-liq cap is a
    SAFETY that only applies when some option slots went unfilled (partial/broken
    run) — when every slot is filled the idle cash is genuinely free, so the FULL
    amount is parked.
  • Spendable cash = min(TotalCashValue, SettledCash, EquityWithLoanValue)
    − open CSP collateral − proceeds still inside T+1 settlement. That last term
    is the fund's own ledger (settlement.py), because no IBKR tag reports it: on
    2026-09-21 every cash tag said $8,405 was free while the order-entry check
    refused the buy. Cash/IRA accounts only — margin accounts can spend unsettled
    proceeds.
  • Fractional via IBKR cashQty (spend the exact dollar amount).
  • Reconciles an already-open position so a failed prior sell isn't double-bought
    or stranded.
  • Honors the Settings "Dry Run" toggle (simulate, place no order) exactly like the
    wheel/CSP paths.
"""
import json
from datetime import datetime

from ib_insync import IB, Stock, Order

import log_setup
import settlement
from config import (
    IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID_CASH_PARK, ACCOUNT, ACCOUNT_TYPE,
    MODE_LABEL, get_settings, connect_with_retry,
)
from market_calendar import is_last_trading_day_of_week
from secrets_client import get_secret

STATE_FILE       = "state.json"
MARKET_WAIT_SECS = 60
MARKET_POLL_SECS = 5
# Technical minimum only (not a user-facing floor — fractional shares mean any
# amount "works"): avoids sending a sub-dollar order IBKR would just reject.
MIN_BUY_USD      = 1.0
# Never park more than this share of net-liquidation, regardless of idle cash.
NET_LIQ_CAP_PCT  = 0.10

log = log_setup.get_logger("cash_park", "cash_park_log.txt")


# ── State ──────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── Discord ────────────────────────────────────────────────────

def _discord_alert(message: str) -> None:
    """Plain-text Discord alert (mirrors scheduler._discord_alert). No-op when no
    webhook is configured or Discord is disabled in settings."""
    try:
        if not get_settings().get("discord_webhook_enabled", True):
            return
        webhook_url = get_secret("discord_webhook_url", "DISCORD_WEBHOOK_URL")
        if not webhook_url:
            return
        import requests
        from pathlib import Path
        _vf  = Path(__file__).parent / "VERSION"
        _v   = f"v{_vf.read_text().strip()}" if _vf.exists() else "?"
        _tag = f"`{_v} · {ACCOUNT}`" if ACCOUNT else f"`{_v}`"
        requests.post(webhook_url,
                      json={"content": f"{message}\n{MODE_LABEL} · {_tag}"}, timeout=5)
    except Exception as e:
        log.warning(f"Discord alert failed: {e}")


# ── IBKR helpers ───────────────────────────────────────────────

def _connect(client_id: int = None) -> IB:
    client_id = client_id if client_id is not None else IBKR_CLIENT_ID_CASH_PARK
    log.info(f"🔌 Connecting to IB Gateway {IBKR_HOST}:{IBKR_PORT} ({ACCOUNT_TYPE}, clientId={client_id})")

    def _attempt() -> IB:
        ib = IB()
        ib.connect(IBKR_HOST, IBKR_PORT, clientId=client_id)
        ib.reqMarketDataType(3)   # delayed-frozen ok — ETFs are penny-wide
        log.info(f"✅ Connected to IBKR (clientId={client_id})")
        return ib

    # Fail fast (legacy 3 attempts): the sweep is opt-in and self-guarded, so a
    # skipped park is a non-event next to blocking the run that just placed trades.
    return connect_with_retry(_attempt, IBKR_HOST, IBKR_PORT, log)


_CASH_TAGS = ("TotalCashValue", "NetLiquidation", "BuyingPower",
              "SettledCash", "EquityWithLoanValue")


def _account_summary(ib: IB) -> tuple:
    """(spendable_cash, net_liquidation, buying_power). All None on error.

    - The no-margin cap takes the MIN of TotalCashValue, SettledCash and
      EquityWithLoanValue. TotalCashValue alone was the cap until v5.2.123; on the
      2026-09-21 rejection all three happened to agree, but SettledCash is the tag
      that MEANS what this cap is for and ELV is the figure IBKR's own order check
      quotes back when it says no, so taking the lowest costs one dict lookup and
      removes a whole class of surprise. max(0, …) still blocks a margin account,
      which reports these negative while borrowing.
    - BuyingPower is returned for the dashboard record only; it is NOT a cap. It
      read $8,405 on the run that got rejected.
    """
    try:
        summary = ib.accountSummary(ACCOUNT)
        by_tag  = {}
        for v in summary:
            if v.tag in _CASH_TAGS:
                # Prefer the base-currency / USD row; accountSummary may repeat tags.
                if v.tag not in by_tag or v.currency in ("USD", "BASE", ""):
                    try:
                        by_tag[v.tag] = float(v.value)
                    except (TypeError, ValueError):
                        continue
        cash_tags = [by_tag[t] for t in ("TotalCashValue", "SettledCash",
                                         "EquityWithLoanValue") if t in by_tag]
        return (min(cash_tags) if cash_tags else None,
                by_tag.get("NetLiquidation"), by_tag.get("BuyingPower"))
    except Exception as e:
        log.warning(f"⚠️  Could not read account summary: {e}")
        return None, None, None


def _settled_by_date(ib: IB) -> tuple:
    """(raw string, {date: amount}) from IBKR's `SettledCashByDate`, or ("", {}).

    OBSERVATION ONLY — deliberately not wired into the cap yet. On 2026-09-21 the
    sweep was refused with an Equity-with-Loan-Value of $9,368.63 while every cash
    tag it could read said $8,405 was free; this is the one figure IBKR publishes
    with a settlement date attached, so it is very likely the signal that would
    have seen the constraint in real time. Nothing sampled it that morning, so that
    is inference, not evidence. Logging it on every sweep is how it gets tested:
    if on a Monday it reads low for TODAY while TotalCashValue reads high, the cap
    can move onto it and the estimate in settlement.py becomes the fallback.

    Best-effort — a log line must never disturb a run that is placing orders.
    """
    try:
        for v in ib.accountValues(ACCOUNT):
            # Not in accountSummary's tag list; only reqAccountUpdates carries it.
            if v.tag == "SettledCashByDate" and v.value:
                return v.value, settlement.parse_settled_by_date(v.value)
    except Exception as e:
        log.warning(f"⚠️  Could not read SettledCashByDate: {e}")
    return "", {}


def _price(ib: IB, ticker: str):
    contract = Stock(ticker, "SMART", "USD")
    q = ib.qualifyContracts(contract)
    if not q:
        return None, None
    data = ib.reqMktData(q[0], snapshot=False)
    ib.sleep(3)
    px = (data.last  if data.last  and data.last  > 0 else
          data.close if data.close and data.close > 0 else
          data.bid   if data.bid   and data.bid   > 0 else None)
    ib.cancelMktData(q[0])
    ib.sleep(0.3)
    return (q[0], round(float(px), 2)) if px else (q[0], None)


def _held_shares(ib: IB, ticker: str) -> float:
    """Actual IBKR position (shares) for `ticker` — the reconciliation source of
    truth for the sell, so we never try to sell more than we really hold."""
    try:
        for p in ib.positions(ACCOUNT):
            c = p.contract
            if c.symbol == ticker and c.secType == "STK":
                return float(p.position)
    except Exception as e:
        log.warning(f"⚠️  Could not read positions: {e}")
    return 0.0


def _open_short_put_capital(ib: IB) -> float:
    """Sum strike × 100 × |contracts| across ALL open short puts — the cash a
    cash-secured-put fund reserves to back them.

    The sweep subtracts this so its remainder reflects capital committed by CSPs
    opened in EARLIER runs this week, not just the current run's. Without it, a
    same-week RE-RUN (where this run deploys 0 new CSPs because the slots are
    already filled) treats the whole budget as idle and over-parks — which drove
    the account onto margin (v5.2.58). Read live from IBKR so it's independent of
    how many runs happened. Best-effort: 0.0 on error (the settled-cash cap still
    applies)."""
    total = 0.0
    try:
        for p in ib.positions(ACCOUNT):
            c = p.contract
            if (c.secType == "OPT" and str(getattr(c, "right", "")).upper().startswith("P")
                    and p.position < 0):
                total += float(c.strike) * 100.0 * abs(float(p.position))
    except Exception as e:
        log.warning(f"⚠️  Could not read open short-put capital: {e}")
    return round(total, 2)


def _poll_fill(ib: IB, trade) -> bool:
    elapsed = 0
    while elapsed < MARKET_WAIT_SECS:
        ib.sleep(MARKET_POLL_SECS)
        elapsed += MARKET_POLL_SECS
        st  = trade.orderStatus.status
        rem = trade.orderStatus.remaining
        fl  = trade.orderStatus.filled
        if st == "Filled" or (rem == 0 and fl and fl > 0):
            return True
        # Terminal rejection/cancel (e.g. Error 10244 cashQty reject) — stop
        # waiting the full window; the caller can escalate immediately.
        if st in ("Cancelled", "ApiCancelled", "Inactive") and not fl:
            log.info(f"  ⛔ order {st} (filled {fl}) — not waiting out the window")
            return False
        log.info(f"  ⏳ order {st}: filled {fl} after {elapsed}s")
    return False


def _reject_reason(trade) -> tuple:
    """(errorCode, message) of the last error on this order, or (None, None).

    A rejected order and an order that genuinely sat unfilled for 60s are the same
    `False` out of _poll_fill, but they are completely different events and the
    2026-09-21 alert ("did not fill — MANUAL CHECK", on an order IBKR refused in
    0.2s) said neither. The reason is right there in the trade log."""
    try:
        for entry in reversed(trade.log or []):
            code = getattr(entry, "errorCode", 0)
            if code:
                msg = (getattr(entry, "message", "") or "").replace("<br>", " ")
                return code, " ".join(msg.split())
    except Exception:
        pass
    return None, None


# ── Buy (Monday, after the option workflow) ────────────────────

def maybe_buy_park(csp_outcome: dict, context: dict, dry_run: bool = False,
                   client_id: int = None, ib: IB = None) -> dict | None:
    """Buy the configured instrument with the account's idle settled cash.

    csp_outcome: return of run_csp_pipeline (total_capital, target_fills, fills,
                 csp_premium, positions).
    context:     wheel-check result (cc_premium).
    dry_run:     True on a preview (Run Screener) — no real fills exist yet, so the
                 sized position count is used as the fill proxy and the planned CSP
                 capital is subtracted from Buying Power to estimate the leftover.

    Returns a small result dict (status + details) or None when the feature is off.
    Never raises into the caller — a sweep failure must not break the Monday run.
    """
    s = get_settings()
    if not s.get("cash_park_enabled", False):
        return None

    instrument   = (s.get("cash_park_instrument") or "QQQ").strip().upper()
    include_prem = bool(s.get("cash_park_include_premiums", False))
    # Simulate (no real order) on a preview OR when the Settings Dry Run toggle is on.
    orders_dry_run = dry_run or bool(s.get("dry_run", False))

    def _finish(result: dict) -> dict:
        """Attach common fields, persist the last decision under a SEPARATE key (so
        an open position block is never clobbered), and return it. This is what makes
        the outcome visible on the dashboard — even a skip.

        Persist on any NON-preview run — i.e. Run Now / the scheduled Monday job —
        even when the Settings 'Dry Run' toggle is on (`orders_dry_run`), so the
        dashboard reflects a simulated Run Now the same way the wheel/CSP paths write
        simulated state. Only the true preview (`dry_run`, Run Screener) stays
        side-effect-free — it surfaces the result in-band via the API response.
        The `dry_run` flag on the result marks a simulated decision."""
        result.setdefault("instrument", instrument)
        result["dry_run"]      = orders_dry_run
        result["evaluated_at"] = datetime.now().isoformat()
        if not dry_run:
            try:
                st = _load_state()
                st["cash_park_last_eval"] = result
                _save_state(st)
            except Exception as e:
                log.warning(f"could not persist cash_park_last_eval: {e}")
        return result

    # Fill status — used ONLY to pick the safety cap below, NOT to skip. When every
    # slot is filled the idle cash is genuinely leftover (park the full amount); if
    # some slots went unfilled (partial/broken run) the 10% net-liq cap kicks in as a
    # safety. On a preview nothing executed, so use the sized count as the proxy.
    fills  = len(csp_outcome.get("positions", [])) if dry_run else csp_outcome.get("fills", 0)
    target = csp_outcome.get("target_fills", 0)
    all_filled = fills >= target

    owns = _connect(client_id) if ib is None else None
    ib   = ib or owns
    try:
        # ── Committed CSP capital = cash reserved to back ALL open short puts
        # (strike×100), read LIVE from IBKR. Subtracting this is what makes a re-run
        # safe: on a re-run this run deploys 0 new CSPs, but the puts opened in earlier
        # runs this week are still consuming the budget. Using only this run's
        # total_capital treated the whole budget as idle and over-parked onto margin
        # (v5.2.58). On a DRY preview the CSPs aren't placed yet, so add the planned
        # (sized) capital; on a LIVE run they're already open and counted here.
        total_cash, net_liq, buying_power = _account_summary(ib)
        committed_csp = _open_short_put_capital(ib)
        planned_csp   = (csp_outcome.get("total_capital", 0.0) or 0.0) if dry_run else 0.0
        committed_csp += planned_csp

        cash_account = bool(get_settings().get("cash_account", False))
        freed        = context.get("freed_capital", 0.0) or 0.0

        # ── Base = idle cash available to park.
        #
        # CASH/IRA account: read it LIVE from BuyingPower at this moment, which is
        # what the docstring has always claimed and what the code threw away until
        # v5.2.124. IBKR's BuyingPower on such an account is ELV − InitMarginReq, so
        # every open put's collateral is ALREADY removed from it. Deriving the
        # remainder from the run's `effective_budget` instead double-subtracted that
        # collateral, because for a cash account the budget IS BuyingPower
        # (monday_runner._compute_effective_budget) and cash_park then subtracted
        # committed_csp on top:
        #
        #   2026-09-21 preview — budget $8,405 (put collateral already out)
        #                        − committed_csp $9,500 → remainder $0.00
        #                        while the account held $8,405.15 of genuinely idle cash.
        #
        # The scheduled 09:55 run escaped it only by ordering luck: its budget is
        # snapshotted BEFORE the puts are sold, so subtracting was right there. Any
        # run with the puts already open — every Run Now, every re-run — parked $0.
        # Reading BuyingPower at sweep time removes the ordering dependence entirely:
        # by then every put this run placed is open and counted exactly once, and it
        # picks up the premium those fills added to cash (worth $107.21 on 9/21).
        #
        # A DRY preview has placed nothing and sold nothing, so it models both:
        # subtract the planned CSP capital, add the proceeds of sales the wheel check
        # only intends to make.
        #
        # MARGIN account: unchanged. There net_liq − reserved does NOT net out put
        # collateral, so the explicit committed_csp subtraction is still what keeps a
        # same-week re-run off margin (v5.2.58).
        if cash_account and buying_power is not None:
            idle = max(0.0, buying_power) + (freed - planned_csp if dry_run else 0.0)
            remainder = max(0.0, idle)
            basis = "live buying_power (cash account)"
        else:
            remainder = max(0.0, (csp_outcome.get("effective_budget", 0.0) or 0.0)
                            - committed_csp)
            basis = "effective_budget − committed CSP"
        base = remainder
        if include_prem:
            base += (csp_outcome.get("csp_premium", 0.0) or 0.0) \
                  + (context.get("cc_premium", 0.0) or 0.0)

        # No-margin cap = real settled cash LESS the cash already reserved to secure the
        # open short puts. TotalCashValue alone is not enough: opening a put on margin
        # doesn't reduce it (premium even raises it), so on a re-run the raw cash cap
        # let the sweep park cash that was already backing the puts → margin (v5.2.58).
        # On a DRY preview add freed proceeds to model POST-sale cash (a stop-loss week
        # is otherwise wrongly zeroed by the pre-sale negative settled cash). The 10%
        # net-liq cap is an additional safety for a partial (unfilled) run.
        settled_eff = (total_cash + freed) if (dry_run and total_cash is not None) else total_cash

        # ── Unsettled-cash haircut (cash/IRA accounts only) ──
        # IBKR will not let a cash account BUY STOCK with proceeds still inside T+1
        # settlement, and no account tag says so: on 2026-09-21 TotalCashValue,
        # SettledCash, BuyingPower and AvailableFunds all reported $8,405 free while
        # the order-entry check refused the buy against an ELV of $9,368.63. So the
        # fund tracks its own recent sales (settlement.py) and subtracts them here.
        # Two sources, because they settle on different days:
        #   • the ledger — Friday's call-away and the prior week's park sale, the
        #     pair that caused the rejection. Both land Monday, mid-run.
        #   • `freed` — shares this very run just sold, which the ledger cannot
        #     know about yet. On a DRY preview `freed` was added above to model
        #     post-sale cash, so adding then subtracting cancels and preview
        #     still matches a live run exactly.
        # Margin accounts are exempt: there the margin loan covers the gap and a
        # haircut would just under-park. Read the toggle LIVE from settings (not an
        # import snapshot) like dry_run.
        state = _load_state()
        if cash_account:
            unsettled, unsettled_rows = settlement.unsettled(state)
            unsettled = round(unsettled + freed, 2)
        else:
            unsettled, unsettled_rows = 0.0, []

        cash_cap    = max(0.0, settled_eff - committed_csp - unsettled) \
                      if settled_eff is not None else base
        netliq_cap  = NET_LIQ_CAP_PCT * net_liq if net_liq and net_liq > 0 else base
        buy_amount  = round(min(base, cash_cap) if all_filled
                            else min(base, cash_cap, netliq_cap), 2)

        log.info(f"  🅿️  Cash sweep: remainder=${remainder:,.2f} [{basis}]  "
                 f"committed_csp=${committed_csp:,.2f}  "
                 f"base=${base:,.2f}  settled(eff)=${cash_cap:,.2f}  10%netliq=${netliq_cap:,.2f}  "
                 f"slots={fills}/{target} "
                 f"{'(all filled → full)' if all_filled else '(partial → 10% cap)'}  "
                 f"→ buy=${buy_amount:,.2f} of {instrument}")
        if unsettled:
            log.info(f"  ⏳ unsettled (T+1, not spendable on a cash account): "
                     f"${unsettled:,.2f} — {settlement.describe(unsettled_rows)}"
                     + (f", plus ${freed:,.0f} freed by this run's sales" if freed else ""))

        # IBKR's own settlement schedule, logged beside the decision this run
        # actually made so the two can be compared later. See _settled_by_date:
        # observation only, it does not move the cap.
        sbd_raw, sbd = _settled_by_date(ib)
        sbd_today    = settlement.settled_as_of(sbd)
        if sbd:
            schedule = "  ".join(f"{d:%Y-%m-%d}=${a:,.2f}" for d, a in sorted(sbd.items()))
            log.info(f"  📅 IBKR SettledCashByDate: {schedule}")
            if sbd_today is not None and total_cash is not None:
                gap = round(total_cash - sbd_today, 2)
                log.info(f"     spendable today ${sbd_today:,.2f} vs cash tags "
                         f"${total_cash:,.2f} → gap ${gap:,.2f}"
                         + ("  ⚠️  the cash tags are over-stating what can buy stock"
                            if gap > 1 else "  (agree)"))

        caps = {"base": round(base, 2), "remainder": round(remainder, 2),
                "remainder_basis": basis,
                "committed_csp": committed_csp,
                "settled_cash": round(total_cash, 2) if total_cash is not None else None,
                "settled_effective": round(cash_cap, 2),
                "unsettled": unsettled,
                "unsettled_detail": settlement.describe(unsettled_rows) if unsettled else None,
                # Persisted so a rejection can be compared against IBKR's own
                # schedule after the fact, which 2026-09-21 had no way to do.
                "settled_by_date": sbd_raw or None,
                "settled_today": round(sbd_today, 2) if sbd_today is not None else None,
                "buying_power": round(buying_power, 2) if buying_power is not None else None,
                "netliq_cap": round(netliq_cap, 2), "all_slots_filled": all_filled,
                "fills": fills, "target": target, "buy_amount": buy_amount}

        if buy_amount < MIN_BUY_USD:
            if remainder < MIN_BUY_USD:
                reason = "no remainder left after this week's CSP deployment"
            elif cash_cap < MIN_BUY_USD and unsettled:
                # The 2026-09-21 case. Worth naming precisely: the cash IS in the
                # account, so "no settled cash" alone reads like something broke.
                reason = (f"${unsettled:,.0f} of the cash is still in T+1 settlement "
                          f"({settlement.describe(unsettled_rows)}) and a cash/IRA account "
                          f"cannot buy stock with unsettled proceeds")
            elif cash_cap < MIN_BUY_USD:
                reason = f"no settled cash (${(settled_eff or 0):,.0f}) — would require margin"
            else:
                reason = (f"remainder ${remainder:,.0f} capped by settled cash ${cash_cap:,.0f}"
                          + ("" if all_filled else f" / 10% net-liq ${netliq_cap:,.0f} (partial run)"))
            log.info(f"  💤 Nothing to park (${buy_amount:,.2f}) — {reason}")
            # Say so in Discord, not just on the dashboard. A silent skip is
            # indistinguishable from the sweep never having run, and the
            # unfilled-slot case in particular is worth explaining: after
            # v5.2.98 a slot can go unfilled DELIBERATELY (an adjusted strike
            # that no longer fits the budget), which is a healthy outcome rather
            # than a broken run — but either way we would rather leave the cash
            # unparked than park it and risk margin.
            slot_note = ""
            if not all_filled:
                unfilled = max(0, target - fills)
                slot_note = (
                    f"\n\n{unfilled} of {target} slot(s) unfilled, so the sweep was held to the "
                    f"10% net-liq cap (${netliq_cap:,.0f}) instead of the full remainder. "
                    f"An unfilled slot is either a deliberate budget skip or a run that went "
                    f"wrong — parking idle cash would mask both, so nothing was parked."
                )
            _discord_alert(
                f"🅿️ **YRVI** Cash sweep — **nothing parked** this week: {reason}.{slot_note}")
            return _finish({"status": "skipped_no_cash", **caps,
                            "message": f"No cash swept — {reason}"})

        # ── Reconcile: don't double-buy if a prior park never sold ──
        existing = state.get("cash_park")
        if existing and existing.get("status") == "open" and existing.get("shares"):
            log.warning(f"  ⚠️  Prior {existing.get('instrument')} park still open — not buying again")
            if not orders_dry_run:
                _discord_alert(f"⚠️ **YRVI** Cash sweep: a prior {existing.get('instrument')} park "
                               f"({existing.get('shares')} sh) is still OPEN — not buying again. "
                               f"It will be sold on the next end-of-week job.")
            return _finish({"status": "skipped_existing_open", **caps,
                            "message": f"{existing.get('instrument')} position from a prior week "
                                       f"still open — not buying again"})

        if orders_dry_run:
            _, px = _price(ib, instrument)
            sh    = round(buy_amount / px, 4) if px else None
            log.info(f"  🟡 [DRY RUN] would BUY ~${buy_amount:,.2f} of {instrument}"
                     + (f" (~{sh} sh @ ${px:.2f})" if px else ""))
            return _finish({"status": "dry_run", **caps, "est_shares": sh, "est_price": px,
                            "message": f"[Preview] Would park ${buy_amount:,.0f} in {instrument}"
                                       + (f" (~{sh} sh @ ${px:.2f})" if px else " (price unavailable — market closed)")})

        # ── Live buy: fractional via cashQty (spend the exact dollars) ──
        contract = Stock(instrument, "SMART", "USD")
        q = ib.qualifyContracts(contract)
        if not q:
            log.error(f"  ❌ Cannot qualify {instrument} — sweep aborted")
            _discord_alert(f"❌ **YRVI** Cash sweep: could not qualify {instrument} — no park this week.")
            return _finish({"status": "failed_qualify", **caps,
                            "message": f"Failed — could not qualify {instrument}"})

        # Tier 1 — fractional via cashQty (spend the exact dollars). Supported on
        # accounts with fractional/monetary-order permission.
        order = Order(action="BUY", orderType="MKT", cashQty=buy_amount,
                      tif="DAY", account=ACCOUNT)
        log.info(f"  📥 BUY ${buy_amount:,.2f} {instrument} at market (cashQty)")
        trade = ib.placeOrder(q[0], order)
        filled_ok = _poll_fill(ib, trade)

        # Tier 2 — whole-share fallback. Some accounts (paper, and any without
        # fractional trading) reject cashQty with Error 10244. If NOTHING filled,
        # retry as a plain whole-share market order for floor($ / price) shares —
        # the same order style the sell side uses. The sub-1-share remainder (well
        # under one QQQ/SGOV share) just stays as cash for the week.
        if not filled_ok and not float(trade.orderStatus.filled or 0.0):
            # Only cancel an order that is still live. IBKR had already killed the
            # 10244 one, and cancelling it anyway logged a misleading Error 10147
            # ("OrderId 6 that needs to be cancelled is not found") next to the
            # real failure.
            if trade.orderStatus.status not in ("Cancelled", "ApiCancelled", "Inactive"):
                ib.cancelOrder(trade.order)
                ib.sleep(1)
            _, px = _price(ib, instrument)
            whole = int(buy_amount // px) if px else 0
            if whole >= 1:
                log.info(f"  🔁 cashQty unfilled — falling back to {whole} whole "
                         f"share(s) of {instrument} @ ${px:.2f}")
                order = Order(action="BUY", orderType="MKT", totalQuantity=whole,
                              tif="DAY", account=ACCOUNT)
                trade = ib.placeOrder(q[0], order)
                filled_ok = _poll_fill(ib, trade)
            else:
                log.error(f"  ❌ {instrument} price unavailable or ${buy_amount:,.0f} "
                          f"< 1 share — cannot fall back")

        if not filled_ok:
            code, reason = _reject_reason(trade)
            # Error 201 = IBKR refused the order on available funds. On a cash/IRA
            # account that is overwhelmingly the unsettled-proceeds rule, not a
            # broken run: the money is in the account, it just isn't spendable
            # until T+1 completes. The haircut above should now catch this before
            # an order is ever sent, so reaching here means the haircut
            # under-counted — say that plainly instead of "did not fill", which
            # sent a MANUAL CHECK alert for an order IBKR refused in 0.2s.
            if code == 201:
                log.error(f"  ⛔ {instrument} buy REJECTED by IBKR (Error 201 — available "
                          f"funds): {reason}")
                log.error(f"     Haircut applied was ${unsettled:,.2f}; nothing parked this week.")
                _discord_alert(
                    f"🅿️ **YRVI** Cash sweep — **nothing parked**: IBKR refused the "
                    f"{instrument} buy (${buy_amount:,.0f}) for insufficient available funds.\n\n"
                    f"On a cash/IRA account, proceeds from a sale cannot buy stock until T+1 "
                    f"settlement completes — a Friday call-away or park sale is still "
                    f"unsettled Monday morning. The cash is safe and idle; no action needed.\n"
                    f"IBKR said: `{reason}`")
                return _finish({"status": "rejected_unsettled_funds", **caps,
                                "ibkr_error": code, "ibkr_reason": reason,
                                "message": f"IBKR refused the {instrument} buy — funds not yet "
                                           f"settled (T+1). Nothing parked."})
            if code:
                log.error(f"  ⛔ {instrument} buy rejected by IBKR (Error {code}): {reason}")
                _discord_alert(f"❌ **YRVI** Cash sweep: {instrument} BUY (${buy_amount:,.0f}) "
                               f"rejected by IBKR — MANUAL CHECK.\n`Error {code}: {reason}`")
                return _finish({"status": "failed_rejected", **caps,
                                "ibkr_error": code, "ibkr_reason": reason,
                                "message": f"Failed — IBKR rejected the {instrument} buy "
                                           f"(Error {code})"})
            log.error(f"  ❌ {instrument} buy did not fill in {MARKET_WAIT_SECS}s")
            _discord_alert(f"❌ **YRVI** Cash sweep: {instrument} BUY (${buy_amount:,.0f}) "
                           f"did not fill — MANUAL CHECK.")
            return _finish({"status": "failed_no_fill", **caps,
                            "message": f"Failed — {instrument} buy did not fill"})

        shares = round(float(trade.orderStatus.filled or 0.0), 4)
        fill   = round(float(trade.orderStatus.avgFillPrice or 0.0), 4)
        cost   = round(shares * fill, 2)
        now    = datetime.now().isoformat()
        state = _load_state()   # reload — the pipeline wrote weekly_pnl meanwhile
        state["cash_park"] = {
            "instrument":   instrument,
            "shares":       shares,
            "buy_price":    fill,
            "cost_basis":   cost,
            "buy_date":     now,
            "status":       "open",
            "sell_price":   None,
            "realized_pnl": None,
            "last_checked": now,
        }
        _save_state(state)
        log.info(f"  ✅ Parked ${cost:,.2f}: {shares} {instrument} @ ${fill:.2f}")
        # When a partial run held the buy down to the 10% net-liq cap, say so —
        # otherwise a deliberately small park looks like the whole remainder.
        cap_note = ""
        if not all_filled and netliq_cap < min(base, cash_cap):
            cap_note = (f"\nHeld to the 10% net-liq cap (${netliq_cap:,.0f}) because "
                        f"{max(0, target - fills)} of {target} slot(s) went unfilled — "
                        f"the remainder was ${base:,.0f}.")
        _discord_alert(f"🅿️ **YRVI** Cash sweep — parked **${cost:,.0f}** in "
                       f"**{instrument}** ({shares} sh @ ${fill:.2f}). Sells end of week."
                       f"{cap_note}")
        return _finish({"status": "bought", **caps, "shares": shares,
                        "buy_price": fill, "cost_basis": cost,
                        "message": f"Parked ${cost:,.0f} in {instrument} ({shares} sh @ ${fill:.2f})"})
    except Exception as e:
        log.error(f"❌ Cash sweep buy error: {e}", exc_info=True)
        _discord_alert(f"❌ **YRVI** Cash sweep buy failed: `{type(e).__name__}: {e}`")
        return _finish({"status": "error", "error": str(e), "message": f"Error: {type(e).__name__}"})
    finally:
        if owns is not None:
            owns.disconnect()


# ── Sell (end of week) ─────────────────────────────────────────

def sell_park(dry_run: bool = False, client_id: int = None, ib: IB = None) -> dict | None:
    """Liquidate the parked position. Runs regardless of the enabled toggle so a
    position is never stranded if the user turns the feature off mid-week. Returns
    None when there's nothing to sell."""
    s = get_settings()
    orders_dry_run = dry_run or bool(s.get("dry_run", False))

    state = _load_state()
    cp = state.get("cash_park")
    if not cp or cp.get("status") != "open" or not cp.get("shares"):
        log.info("  💤 No open cash-park position to sell.")
        return None

    instrument = cp["instrument"]
    want       = round(float(cp["shares"]), 4)
    cost_basis = float(cp.get("cost_basis") or 0.0)

    owns = _connect(client_id) if ib is None else None
    ib   = ib or owns
    try:
        # Reconcile against the real position — never sell more than we hold.
        held   = _held_shares(ib, instrument)
        shares = round(min(want, held), 4) if held > 0 else want
        if held <= 0:
            log.warning(f"  ⚠️  State says {want} {instrument} parked but IBKR shows none — "
                        f"marking closed without an order.")
            cp.update({"status": "sold", "sell_price": None, "realized_pnl": 0.0,
                       "sold_date": datetime.now().isoformat(),
                       "note": "no position at IBKR — reconciled closed"})
            state["cash_park"] = cp
            if not orders_dry_run:
                _save_state(state)
            return {"status": "reconciled_none", "instrument": instrument}

        if orders_dry_run:
            _, px = _price(ib, instrument)
            proceeds = round(shares * px, 2) if px else None
            pnl = round(proceeds - cost_basis, 2) if proceeds is not None else None
            log.info(f"  🟡 [DRY RUN] would SELL {shares} {instrument}"
                     + (f" @ ~${px:.2f} = ${proceeds:,.2f} (P&L ${pnl:+,.2f})" if px else ""))
            return {"status": "dry_run", "instrument": instrument, "shares": shares,
                    "proceeds": proceeds, "realized_pnl": pnl}

        contract = Stock(instrument, "SMART", "USD")
        if not ib.qualifyContracts(contract):
            log.error(f"  ❌ Cannot qualify {instrument} for sale")
            _discord_alert(f"❌ **YRVI** Cash sweep: could not qualify {instrument} to sell — MANUAL CHECK.")
            return {"status": "failed_qualify", "instrument": instrument}

        order = Order(action="SELL", orderType="MKT", totalQuantity=shares,
                      tif="DAY", account=ACCOUNT)
        log.info(f"  📤 SELL {shares} {instrument} at market")
        trade = ib.placeOrder(contract, order)
        if not _poll_fill(ib, trade):
            log.error(f"  ❌ {instrument} sale did not fill in {MARKET_WAIT_SECS}s")
            _discord_alert(f"❌ **YRVI** Cash sweep: {instrument} SELL ({shares} sh) "
                           f"did not fill — MANUAL CHECK, position still open.")
            return {"status": "failed_no_fill", "instrument": instrument}

        filled   = round(float(trade.orderStatus.filled or 0.0), 4)
        fill     = round(float(trade.orderStatus.avgFillPrice or 0.0), 4)
        proceeds = round(filled * fill, 2)
        realized = round(proceeds - cost_basis, 2)
        now      = datetime.now().isoformat()

        state = _load_state()
        cp = state.get("cash_park", cp)
        cp.update({"status": "sold", "sell_price": fill, "shares": filled,
                   "proceeds": proceeds, "realized_pnl": realized,
                   "sold_date": now, "last_checked": now})
        state["cash_park"] = cp
        _record_park_pnl(state, realized)
        # These proceeds are unsettled until T+1. The sweep sells on the week's LAST
        # trading day, so on a cash account they are still settling when Monday's buy
        # wants them — the park was blocking its own next cycle (2026-09-21).
        settlement.record_sale(state, proceeds, source="park_sale", ticker=instrument,
                               entry_id=f"park_sale:{cp.get('buy_date') or now}")
        _save_state(state)

        log.info(f"  ✅ Cash sweep closed: sold {filled} {instrument} @ ${fill:.2f} "
                 f"= ${proceeds:,.2f}  (P&L ${realized:+,.2f})")
        sign = "🟢" if realized >= 0 else "🔴"
        _discord_alert(f"💵 **YRVI** Cash sweep closed — sold {filled} **{instrument}** "
                       f"@ ${fill:.2f} = ${proceeds:,.0f}. {sign} P&L **${realized:+,.0f}**")
        return {"status": "sold", "instrument": instrument, "shares": filled,
                "sell_price": fill, "proceeds": proceeds, "realized_pnl": realized}
    except Exception as e:
        log.error(f"❌ Cash sweep sell error: {e}", exc_info=True)
        _discord_alert(f"❌ **YRVI** Cash sweep sell failed: `{type(e).__name__}: {e}`")
        return {"status": "error", "error": str(e)}
    finally:
        if owns is not None:
            owns.disconnect()


def _record_park_pnl(state: dict, realized: float) -> None:
    """Fold the sweep's realized P&L into this week's weekly_pnl (park_pnl field +
    total_realized). weekly_pnl was written Monday; the sell lands later the same
    week, so we add park_pnl to the existing components."""
    wp = state.get("weekly_pnl")
    if not isinstance(wp, dict):
        return
    wp["park_pnl"] = round(realized, 2)
    # Re-sum EVERY realized component, not just the ones that existed when this
    # was written: this runs days after Monday and overwrites total_realized, so
    # a component missing from this list is silently erased from the week.
    wp["total_realized"] = round(
        (wp.get("csp_premium") or 0.0)
        + (wp.get("cc_premium") or 0.0)
        + (wp.get("shares_sold_pnl") or 0.0)
        + (wp.get("called_away_pnl") or 0.0)
        + realized, 2)
    wp["last_updated"] = datetime.now().isoformat()


if __name__ == "__main__":
    import sys
    log_setup.configure_root()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sell"
    if cmd == "sell":
        print(sell_park(dry_run="--dry" in sys.argv))
    else:
        print("usage: python cash_park.py sell [--dry]")
