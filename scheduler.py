import json
import logging
import asyncio
import os
import socket
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from config import NUM_POSITIONS, TOTAL_FUND_BUDGET, IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID, ACCOUNT, get_settings, MODE_LABEL
from secrets_client import get_secret
from market_calendar import is_first_trading_day_of_week, is_market_holiday, is_last_trading_day_of_week

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.FileHandler("scheduler_log.txt"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

STATE_FILE      = "state.json"
SETTINGS_FILE   = "settings.json"
HEARTBEAT_FILE  = "scheduler_heartbeat.json"

DEFAULT_TIMEZONE = "America/Los_Angeles"


def _resolve_timezone() -> ZoneInfo:
    try:
        with open(SETTINGS_FILE) as f:
            tz_name = (json.load(f) or {}).get("timezone")
    except (FileNotFoundError, json.JSONDecodeError):
        tz_name = None
    tz_name = tz_name or os.environ.get("TIME_ZONE") or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


PST = _resolve_timezone()  # variable name kept for compatibility; reflects configured timezone


def _write_heartbeat():
    try:
        with open(HEARTBEAT_FILE, "w") as f:
            json.dump({"timestamp": datetime.now(PST).isoformat()}, f)
    except Exception:
        pass


def _discord_alert(message: str) -> None:
    """Send a plain-text Discord alert. No-ops when webhook is not configured."""
    try:
        webhook_url = get_secret("discord_webhook_url", "DISCORD_WEBHOOK_URL")
        if not webhook_url:
            return
        import requests
        from pathlib import Path
        _vf = Path(__file__).parent / "VERSION"
        _v  = f"v{_vf.read_text().strip()}" if _vf.exists() else "?"
        _tag = f"`{_v} · {ACCOUNT}`" if ACCOUNT else f"`{_v}`"
        requests.post(webhook_url, json={"content": f"{message}\n{MODE_LABEL} · {_tag}"}, timeout=5)
    except Exception as e:
        log.warning(f"Discord alert failed: {e}")


def _fetch_account_summary(fallback: float) -> tuple[float, float]:
    """
    Fetch IBKR BuyingPower and NetLiquidation.
    Returns (buying_power, net_liq); falls back to (fallback, fallback) on any failure.
    BuyingPower already reflects all open positions (including manual trades outside the app),
    so it is the correct base budget for new CSPs.
    """
    try:
        from ib_insync import IB
        ib = IB()
        ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID, readonly=True)
        summary = ib.accountSummary(ACCOUNT)
        ib.disconnect()
        by_tag  = {v.tag: float(v.value) for v in summary if v.tag in ("BuyingPower", "NetLiquidation")}
        bp      = by_tag.get("BuyingPower")
        net_liq = by_tag.get("NetLiquidation")
        if bp and bp > 0 and net_liq and net_liq > 0:
            # Use the lesser of the two: for cash/Roth accounts BuyingPower < NetLiq (correct);
            # for margin/paper accounts BuyingPower is inflated (4x+), so NetLiq wins.
            effective = min(bp, net_liq)
            log.info(f"  💹 Compound mode: net_liq=${net_liq:,.0f}  buying_power=${bp:,.0f}  "
                     f"using=${effective:,.0f}")
            return effective, net_liq
        log.warning("  ⚠️  Account summary incomplete — using fund budget fallback")
    except Exception as e:
        log.warning(f"  ⚠️  Could not fetch account summary from IBKR ({e}) — using fund budget fallback")
    return fallback, fallback


def _fetch_net_liq(fallback: float) -> float:
    """Legacy wrapper — returns NetLiquidation only."""
    _, net_liq = _fetch_account_summary(fallback)
    return net_liq
    return fallback


def _ibkr_reachable() -> bool:
    """Quick TCP probe: returns True if the IB Gateway API port is accepting connections."""
    host = os.environ.get("IBKR_HOST", "127.0.0.1")
    port = int(os.environ.get("IBKR_PORT", "4004"))
    try:
        with socket.create_connection((host, port), timeout=5):
            return True
    except OSError:
        return False


def _new_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


def _load_settings() -> dict:
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

# Earliest allowed Monday execution. The wheel check runs 5 min before execution
# and MUST be both after the 6:30 AM PST open (to price CCs) and before the CSP
# pipeline (to free capital first). 7:00 puts the wheel check at 6:55 — ~25 min
# after open — which is safe on live AND on paper's 15-min-delayed feed. Anything
# earlier would run the wheel check at/pre-open with no greeks (no CCs written).
_MIN_EXEC_HOUR = 7
_MIN_EXEC_MIN  = 0

def _parse_exec_time(settings: dict) -> tuple:
    """Return (hour, minute) PST for configured Monday execution time, floored to
    the earliest safe time so the wheel check never lands at/before market open."""
    try:
        h, m = map(int, settings.get("execution_time", "10:00").split(":"))
    except Exception:
        return 10, 0
    if (h, m) < (_MIN_EXEC_HOUR, _MIN_EXEC_MIN):
        log.warning(f"⚠️  Configured execution_time {h:02d}:{m:02d} is earlier than the "
                    f"{_MIN_EXEC_HOUR:02d}:{_MIN_EXEC_MIN:02d} floor — the wheel check would run "
                    f"at/before market open with no option greeks. Using "
                    f"{_MIN_EXEC_HOUR:02d}:{_MIN_EXEC_MIN:02d} instead.")
        return _MIN_EXEC_HOUR, _MIN_EXEC_MIN
    return h, m

def _offset_time(hour: int, minute: int, delta_minutes: int) -> tuple:
    """Subtract delta_minutes from (hour, minute), return new (hour, minute)."""
    total = hour * 60 + minute - delta_minutes
    return total // 60, total % 60


def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


# ── Saturday 6PM — screener preview ───────────────────────────

def run_screener_preview():
    loop = _new_loop()
    now  = datetime.now(PST)
    log.info("\n" + "=" * 65)
    log.info(f"📋 SATURDAY PREVIEW — {now.strftime('%A %Y-%m-%d %H:%M %Z')}")
    log.info("=" * 65)
    try:
        # Delegate to the shared Monday runner (dry_run) so the Saturday Discord
        # preview is the EXACT same plan as the dashboard's Run Screener / This
        # Week and Monday's live run — same CSP sizing and the same wheel-check
        # decisions (CC / defer / sell). A user checking Discord sees what they'd
        # see on the dashboard, even when away from their system.
        from monday_runner import run_monday

        settings         = _load_settings()
        compound_enabled = settings.get("compound_enabled", True)
        fund_budget      = settings.get("fund_budget", TOTAL_FUND_BUDGET)

        # Pre-fetch the account summary so the dry preview reuses a single
        # read-only connection for budgeting (mirrors the dashboard path and
        # avoids a second IBKR connect inside the CSP pipeline).
        account_summary = _fetch_account_summary(fund_budget) if compound_enabled else None

        outcome   = run_monday(dry_run=True, account_summary=account_summary)
        wheel     = outcome.get("wheel", {})
        csp       = outcome.get("csp", {})
        positions = csp.get("positions", [])

        exec_time = settings.get("execution_time", "10:00")
        log.info(f"\n📋 {len(positions)} CSP(s) queued for Monday {exec_time} PST  "
                 f"(budget=${csp.get('effective_budget', 0):,.0f}"
                 f"{'  compounding ON' if compound_enabled else ''})")

        from discord_poster import is_enabled, post_weekly_plan
        if is_enabled():
            post_weekly_plan(
                positions,
                wheel_plan=wheel.get("wheel_activity", []),
                freed_capital=wheel.get("freed_capital", 0.0),
                cc_premium=wheel.get("cc_premium", 0.0),
            )
            log.info("✅ Weekly plan posted to Discord")
    except Exception as e:
        log.error(f"❌ Preview error: {e}", exc_info=True)
    finally:
        loop.close()


# ── Saturday 8AM — assignment detection ───────────────────────
# Runs Saturday morning (not Friday afternoon) so IBKR has posted the
# prior day's option assignments/expirations overnight. Detecting on
# Friday 4:15PM misreported assigned puts as "expired worthless".

def run_assignment_detection():
    loop = _new_loop()
    now  = datetime.now(PST)
    if is_market_holiday(now.date()):
        log.info(f"⏭️  SATURDAY ASSIGNMENT DETECTION skipped — market holiday ({now.strftime('%Y-%m-%d')})")
        loop.close()
        return
    log.info("\n" + "=" * 65)
    log.info(f"🔍 SATURDAY ASSIGNMENT DETECTION — {now.strftime('%A %Y-%m-%d %H:%M %Z')}")
    log.info("=" * 65)
    try:
        from wheel_manager import detect_assignments
        state_before  = _load_state()
        known_tickers = {h["ticker"] for h in state_before.get("wheel_holdings", [])}

        called_away = detect_assignments()

        from discord_poster import is_enabled, post_weekly_review
        if is_enabled():
            state_after  = _load_state()
            today        = now.date().isoformat()
            new_ones     = [h for h in state_after.get("wheel_holdings", [])
                            if h["ticker"] not in known_tickers
                            and h.get("assignment_date") == today]
            settings      = _load_settings()
            fund_budget   = settings.get("fund_budget", TOTAL_FUND_BUDGET)
            goal_pct      = settings.get("goal_pct", 0.24)
            try:
                _, net_liq = _fetch_account_summary(fund_budget)
            except Exception:
                net_liq = None
            post_weekly_review(state_after, called_away or [], new_ones,
                                fund_budget=fund_budget, capital=fund_budget,
                                goal_pct=goal_pct, net_liq=net_liq)
    except Exception as e:
        log.error(f"❌ Assignment detection error: {e}", exc_info=True)
        _discord_alert(f"🚨 **YRVI** Saturday assignment detection failed: `{type(e).__name__}: {e}`")
    finally:
        loop.close()


# ── Monday 9:50AM — Discord pre-execution preview ─────────────

def run_discord_preview():
    now = datetime.now(PST)
    if not is_first_trading_day_of_week(now.date()):
        log.info(f"⏭️  Discord preview skipped — not the first trading day of the week ({now.strftime('%A %Y-%m-%d')})")
        return
    from discord_poster import is_enabled, post_preview
    if not is_enabled():
        return
    loop = _new_loop()
    log.info("📋 Discord preview — sizing positions...")
    try:
        from screener import get_top_targets
        from position_sizer import size_all
        state        = _load_state()
        context      = state.get("monday_context", {})
        skip_tickers = set(context.get("skip_tickers", []))
        freed        = context.get("freed_capital", 0.0)
        # wheel_check hasn't run yet — estimate from current holdings
        holdings     = state.get("wheel_holdings", [])
        reserved     = round(sum(
            h.get("shares", 0) * h.get("assigned_strike", 0.0)
            for h in holdings if h.get("shares", 0) > 0
        ), 2)
        active_count = sum(1 for h in holdings if h.get("shares", 0) > 0)
        targets      = get_top_targets(10)
        filtered     = [t for t in targets if t["ticker"] not in skip_tickers]
        budget       = TOTAL_FUND_BUDGET + freed - reserved
        target_n     = max(1, NUM_POSITIONS - active_count)
        positions    = size_all(filtered, budget=budget, num_positions=target_n)
        post_preview(positions, budget)
        log.info("✅ Discord preview posted")
    except Exception as e:
        log.error(f"❌ Discord preview error: {e}", exc_info=True)
    finally:
        loop.close()


# ── Monday — wheel check → CSP pipeline (one chained job) ─────

def run_pipeline():
    """Run the wheel check and the CSP pipeline back-to-back in one job.

    The wheel check's results are handed to the CSP pipeline IN MEMORY rather
    than via state.json. The two used to be separate cron jobs 5 min apart, but
    when the wheel check actually sells covered calls it runs the order-escalation
    ladder and can take 6+ min — overrunning the pipeline's start, which then read
    a stale monday_context (cc_premium=0, active_wheel_count=0, reserved=0). That
    dropped CC premium from the weekly total and made the pipeline over-fill CSP
    slots against capital already tied up in wheel stock. Chaining them (the same
    sequence as monday_runner.run_monday / the dashboard's Run Now) removes the race.
    """
    loop = _new_loop()
    now  = datetime.now(PST)
    if not is_first_trading_day_of_week(now.date()):
        log.info(f"⏭️  MONDAY RUN skipped — not the first trading day of the week ({now.strftime('%A %Y-%m-%d')})")
        loop.close()
        return
    log.info("\n" + "=" * 65)
    log.info(f"🗓️  MONDAY RUN — {now.strftime('%A %Y-%m-%d %H:%M %Z')}")
    log.info("=" * 65)
    if not _ibkr_reachable():
        msg = "IB Gateway unreachable before Monday run — trades will not execute"
        log.error(f"❌ {msg}")
        _discord_alert(f"🚨 **YRVI** {msg}. Check gateway login / VNC port 5900.")
    # ── Live progress feed ───────────────────────────────────────────
    # Written from the very first second so the dashboard can swap the
    # Next-Execution countdown for live "Working on X" status for the WHOLE
    # workflow — both the wheel-check (CC) phase AND the CSP phase. Earlier this
    # only covered the CSP phase, so the multi-minute CC-selling phase showed
    # nothing. /api/run-status serves this file. Best-effort — never breaks the run.
    import json as _json
    _progress_file  = "/data/run_progress.json"
    _ticker_results = []
    _phase          = {"name": "wheel check"}

    def _sched_progress(ticker=None, stage=None, result=None):
        if result:
            _ticker_results.append(result)
        try:
            _json.dump({
                "executing":      True,
                "current_phase":  _phase["name"],
                "current_ticker": ticker,
                "current_stage":  stage,
                "ticker_results": list(_ticker_results),
            }, open(_progress_file, "w"))
        except Exception:
            pass

    def _clear_progress():
        try:
            _json.dump({"executing": False, "current_phase": None,
                        "current_ticker": None, "current_stage": None,
                        "ticker_results": _ticker_results}, open(_progress_file, "w"))
        except Exception:
            pass

    # Flip the feed to "executing" before any IBKR work so the dashboard hides
    # the countdown immediately, not only once the CSP phase starts.
    _sched_progress(ticker=None, stage="starting wheel check")

    try:
        # ── Step 1: wheel check (stop-loss sells + covered calls) ──
        # Its return dict IS the pipeline context (skip_tickers, freed_capital,
        # reserved_capital, active_wheel_count, cc_premium, shares_sold_pnl, …).
        # progress_callback streams per-ticker CC/sell activity to the feed.
        from wheel_manager import run_wheel_check
        context = run_wheel_check(progress_callback=_sched_progress)
        log.info(f"✅ Wheel check done — freed ${context['freed_capital']:,.0f}  "
                 f"reserved ${context['reserved_capital']:,.0f}  skip {context['skip_tickers'] or 'none'}")

        # ── Step 2: CSP pipeline, driven by the wheel check's live results ──
        _phase["name"] = "CSP pipeline"
        _sched_progress(ticker=None, stage="screening candidates")
        from monday_runner import run_csp_pipeline
        outcome = run_csp_pipeline(context, dry_run=False, progress_callback=_sched_progress)

        # Clear progress file now that execution is done
        _clear_progress()

        # ── Systemic market data failure alert ────────────────
        results    = outcome.get("results", [])
        actionable = [r for r in results if r.get("status") not in
                      ("skipped_contract_size", "skipped_delta")]
        if actionable and all(r.get("status") == "failed_market_data" for r in actionable):
            _discord_alert(
                "⚠️ **YRVI** All candidates failed market data — no trades placed.\n"
                "Check IB Gateway → data farm connections and paper account market data subscriptions."
            )

        log.info(f"\n✅ Done — {outcome.get('fills', 0)}/{outcome.get('target_fills', 0)} CSP fills  |  "
                 f"CSP ${outcome.get('csp_premium', 0):,.0f}  "
                 f"Total realized ${outcome.get('total_realized', 0):,.0f}")

        # ── Step 3: cash sweep — park the week's undeployed remainder ──
        # No-op unless enabled in Settings. Self-guarded (all slots filled, 10%
        # net-liq cap, no margin) and never raises into the run.
        try:
            from cash_park import maybe_buy_park
            maybe_buy_park(outcome, context, dry_run=False)
        except Exception as e:
            log.error(f"❌ Cash sweep buy error (non-fatal): {e}", exc_info=True)

    except Exception as e:
        log.error(f"❌ Monday run error: {e}", exc_info=True)
        _discord_alert(f"🚨 **YRVI** Monday run (wheel check / CSP pipeline) failed: `{type(e).__name__}: {e}`")
        # Don't leave the feed stuck on "executing" — restore the countdown.
        _clear_progress()
    finally:
        loop.close()


# ── Tuesday–Friday 3AM — auto-update check ────────────────────

_GITHUB_VERSION_URL = (
    "https://raw.githubusercontent.com/controllinghand/"
    "you_rock_fund/main/VERSION"
)


def run_auto_update():
    settings = _load_settings()
    if not settings.get("auto_update_enabled"):
        return

    from pathlib import Path
    import requests as req

    now = datetime.now(PST)
    log.info("\n" + "=" * 65)
    log.info(f"🔄 AUTO-UPDATE CHECK — {now.strftime('%A %Y-%m-%d %H:%M %Z')}")
    log.info("=" * 65)
    try:
        version_file = Path(__file__).parent / "VERSION"
        current = version_file.read_text().strip() if version_file.exists() else "unknown"

        r = req.get(_GITHUB_VERSION_URL, params={"_": int(now.timestamp())},
                    headers={"Cache-Control": "no-cache"}, timeout=10)
        r.raise_for_status()
        latest = r.text.strip()

        def _parse(v): return [int(x) for x in v.lstrip("v").split(".")]
        if _parse(current) >= _parse(latest):
            log.info(f"✅ Already up to date ({current})")
            return

        log.info(f"⬆️  Update available: {current} → {latest} — delegating to API")

        # The api container has the /host_repo bind-mount and upgrade logic.
        # Calling it keeps all git/build logic in one place.
        res = req.post("http://api:8000/api/version/upgrade", timeout=90)
        data = res.json()
        if data.get("success"):
            log.info("🚀 Upgrade launched — containers will restart momentarily")
            _discord_alert(f"⬆️ **YRVI** Auto-updating {current} → {latest} — rebuilding now…")
        else:
            log.error(f"❌ Upgrade failed: {data.get('output', '')[:300]}")
            _discord_alert(f"❌ **YRVI** Auto-update failed: `{data.get('output', '')[:200]}`")

    except Exception as e:
        log.error(f"❌ Auto-update error: {e}", exc_info=True)
        _discord_alert(f"❌ **YRVI** Auto-update check failed: `{type(e).__name__}: {e}`")


# ── Tuesday–Thursday 9AM — daily risk monitor ─────────────────

def run_risk_monitor():
    loop = _new_loop()
    now  = datetime.now(PST)
    if is_market_holiday(now.date()):
        log.info(f"⏭️  DAILY RISK MONITOR skipped — market holiday ({now.strftime('%Y-%m-%d')})")
        loop.close()
        return
    log.info("\n" + "=" * 65)
    log.info(f"📊 DAILY RISK MONITOR — {now.strftime('%A %Y-%m-%d %H:%M %Z')}")
    log.info("=" * 65)
    try:
        from risk_manager import run_daily_monitor
        run_daily_monitor()
    except Exception as e:
        log.error(f"❌ Risk monitor error: {e}", exc_info=True)
    finally:
        loop.close()


# ── Thu/Fri 12:30PM — cash-sweep end-of-week sell ─────────────
# Scheduled on BOTH Thursday and Friday; the job only actually sells on whichever
# is the week's last trading day (Friday normally, Thursday when Friday is a market
# holiday such as Good Friday). sell_park() itself no-ops when there's nothing
# parked, so a Thursday firing in a normal week is a cheap check.

def run_cash_park_sell():
    loop = _new_loop()
    now  = datetime.now(PST)
    if not is_last_trading_day_of_week(now.date()):
        log.info(f"⏭️  CASH SWEEP SELL skipped — {now.strftime('%A %Y-%m-%d')} is not "
                 f"the last trading day of the week")
        loop.close()
        return
    log.info("\n" + "=" * 65)
    log.info(f"💵 CASH SWEEP SELL — {now.strftime('%A %Y-%m-%d %H:%M %Z')}")
    log.info("=" * 65)
    try:
        from cash_park import sell_park
        sell_park(dry_run=False)
    except Exception as e:
        log.error(f"❌ Cash sweep sell error: {e}", exc_info=True)
        _discord_alert(f"🚨 **YRVI** Cash sweep sell job failed: `{type(e).__name__}: {e}`")
    finally:
        loop.close()


# ── Scheduler main ─────────────────────────────────────────────

def main():
    settings = _load_settings()
    exec_h, exec_m = _parse_exec_time(settings)
    prev_h, prev_m = _offset_time(exec_h, exec_m, 10)   # Discord preview: exec − 10 min
    wheel_h, wheel_m = _offset_time(exec_h, exec_m, 5)  # Wheel check:     exec − 5 min

    def fmt(h, m):
        ampm = "AM" if h < 12 else "PM"
        h12  = h % 12 or 12
        return f"{h12}:{m:02d} {ampm} PST"

    # job_defaults: survive brief host suspends (e.g. laptop maintenance sleep).
    # If the host freezes across a job's fire time, run it on wake as long as we
    # woke within 30 min — bounded so an overnight/weekend sleep can't fire a
    # trade hours late. coalesce collapses a backlog into a single run.
    scheduler = BlockingScheduler(
        timezone=PST,
        job_defaults={"misfire_grace_time": 1800, "coalesce": True},
    )

    scheduler.add_job(
        run_screener_preview,
        trigger="cron", day_of_week="sat", hour=18, minute=0,
        id="saturday_preview", name="Saturday Screener Preview"
    )
    scheduler.add_job(
        run_assignment_detection,
        trigger="cron", day_of_week="sat", hour=8, minute=0,
        id="saturday_assignment", name="Saturday Assignment Detection"
    )
    scheduler.add_job(
        run_discord_preview,
        trigger="cron", day_of_week="mon,tue", hour=prev_h, minute=prev_m,
        id="monday_discord_preview", name="Weekly Discord Preview"
    )
    # Wheel check → CSP pipeline run as ONE chained job (no state.json hand-off
    # race). It fires at the wheel-check time so CCs are still priced near the
    # open; the CSP pipeline then runs immediately after the wheel check returns.
    scheduler.add_job(
        run_pipeline,
        trigger="cron", day_of_week="mon,tue", hour=wheel_h, minute=wheel_m,
        id="monday_execution", name="Weekly Wheel Check + CSP Execution"
    )
    scheduler.add_job(
        run_risk_monitor,
        trigger="cron", day_of_week="tue,wed,thu", hour=9, minute=0,
        id="daily_risk_monitor", name="Daily Risk Monitor"
    )
    # Cash-sweep sell — fires Thu+Fri; run_cash_park_sell only acts on the week's
    # last trading day (Friday, or Thursday when Friday is a holiday). 12:30 PM PST
    # leaves ample liquidity and clears the position well before the close.
    scheduler.add_job(
        run_cash_park_sell,
        trigger="cron", day_of_week="thu,fri", hour=12, minute=30,
        id="cash_park_sell", name="Cash Sweep End-of-Week Sell"
    )
    scheduler.add_job(
        run_auto_update,
        trigger="cron", day_of_week="wed,thu,fri", hour=3, minute=0,
        id="auto_update", name="Auto-Update Check"
    )
    scheduler.add_job(
        _write_heartbeat,
        trigger="interval", seconds=60,
        id="heartbeat", name="Scheduler Heartbeat"
    )

    _write_heartbeat()
    log.info("\n" + "=" * 65)
    log.info("🗓️  YOU ROCK FUND SCHEDULER — Running")
    log.info(f"   Current time : {datetime.now(PST).strftime('%A %Y-%m-%d %H:%M %Z')}")
    log.info("   • Friday     4:15 PM PST  — assignment detection (skipped on Good Friday)")
    log.info("   • Saturday   6:00 PM PST  — screener preview")
    log.info(f"   • Mon/Tue*  {fmt(prev_h, prev_m):>11}  — Discord preview (if webhook set)")
    log.info(f"   • Mon/Tue*  {fmt(wheel_h, wheel_m):>11}  — wheel check (stop loss + CCs) → CSP execution  ← configured {fmt(exec_h, exec_m)}")
    log.info("   • Tue–Thu    9:00 AM PST  — daily risk monitor (skipped on holidays)")
    log.info("   • Thu/Fri   12:30 PM PST  — cash-sweep sell (last trading day only; if enabled)")
    log.info("   • Wed–Fri    3:00 AM PST  — auto-update check (if enabled in settings)")
    log.info("   * Shifts to Tuesday when Monday is a market holiday")
    log.info("   Press Ctrl+C to stop")
    log.info("=" * 65 + "\n")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        log.info("\n⛔ Scheduler stopped")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
