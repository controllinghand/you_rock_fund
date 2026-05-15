"""YRVI Management Dashboard — FastAPI backend."""
import asyncio
import json
import logging
import os
import random
import re
import socket
import subprocess
import threading
import time
import traceback

logger = logging.getLogger(__name__)
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    import nest_asyncio
    nest_asyncio.apply()
except (ValueError, ImportError):
    # uvloop doesn't support nest_asyncio; the per-thread loop setup below handles ib_insync instead
    pass

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from secrets_client import get_secret

load_dotenv()

def _read_secret_or_env(secret_name: str, env_name: str) -> str:
    return get_secret(secret_name, env_name)

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "state.json"
YTD_FILE = BASE_DIR / "ytd_tracker.json"
SETTINGS_FILE = BASE_DIR / "settings.json"
SETTINGS_DEFAULT_FILE = BASE_DIR / "settings_default.json"
IBC_CONFIG_FILE = BASE_DIR / "ibc_config.ini"
TRADE_LOG_FILE = BASE_DIR / "trade_log.json"

LIVE_PLACEHOLDERS = {
    "IBKR_PASSWORD_LIVE": "your_live_ibkr_password",
}
LIVE_REQUIRED_SECRETS = {
    "account_live":    "your_live_account_number (starts with U)",
    "tws_userid_live": "your_live_ibkr_username",
}

PST = ZoneInfo("America/Los_Angeles")
ET  = ZoneInfo("America/New_York")
ANNUAL_TARGET = 100_000
CONTAINERIZED = os.environ.get("YRVI_CONTAINERIZED", "0") == "1"
HEARTBEAT_FILE = BASE_DIR / "scheduler_heartbeat.json"
SECRETS_SERVICE_URL = "http://secrets:8001"
# clientId 100-999 used at runtime (random per call) — never conflicts with trader(1) wheel(2) risk(3)

# ── Watchdog ───────────────────────────────────────────────────
# Tracks how long each subsystem has been in a failed state so we
# can alert only after a persistent outage (not a transient hiccup).
_watchdog_state: dict = {
    "gateway_down_since":   None,
    "ibkr_down_since":      None,
    "scheduler_down_since": None,
    "last_gateway_alert":   None,
    "last_ibkr_alert":      None,
    "last_scheduler_alert": None,
}
_gateway_login_status: str = "unknown"

WATCHDOG_INTERVAL = 300   # seconds between checks
ALERT_THRESHOLD   = 600   # seconds a failure must persist before we alert

app = FastAPI(title="YRVI Dashboard API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── File helpers ──────────────────────────────────────────────

def load_settings() -> dict:
    defaults: dict = {}
    if SETTINGS_DEFAULT_FILE.exists():
        try:
            defaults = json.loads(SETTINGS_DEFAULT_FILE.read_text())
        except Exception:
            pass
    if SETTINGS_FILE.exists():
        try:
            user = json.loads(SETTINGS_FILE.read_text())
            return {**defaults, **user}
        except Exception:
            pass
    return defaults

def save_settings(data: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(data, indent=2))

def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}

def load_ytd() -> dict:
    try:
        return json.loads(YTD_FILE.read_text())
    except Exception:
        return {"weeks": [], "total_premium": 0.0, "weeks_traded": 0,
                "best_week": None, "worst_week": None}

def load_trade_log() -> list:
    try:
        return json.loads(TRADE_LOG_FILE.read_text())
    except Exception:
        return []

def _backfill_trade_log() -> None:
    """One-time backfill of trade_log.json from state.json on first run.

    Matches positions to executions by ticker, reconstructs records only when
    fill_price is known. Skips any entry that can't be fully recovered.
    """
    if TRADE_LOG_FILE.exists() and TRADE_LOG_FILE.stat().st_size > 2:
        return  # already populated

    state = load_state()
    positions  = state.get("positions", [])
    executions = state.get("executions", [])
    if not positions or not executions:
        return

    exec_map = {e.get("ticker"): e for e in executions if e.get("ticker")}
    entries = []

    for pos in positions:
        ticker = pos.get("ticker")
        if not ticker:
            continue
        ex = exec_map.get(ticker, {})
        if ex.get("status") not in ("filled", "dry_run", "partial_fill"):
            continue
        fill_price = ex.get("fill_price")
        if fill_price is None:
            continue

        expiry_raw  = pos.get("expiry", "")
        expiry_fmt  = None
        try:
            from datetime import datetime as _dt
            expiry_fmt = _dt.strptime(expiry_raw, "%a, %d %b %Y %H:%M:%S %Z").strftime("%Y%m%d")
        except Exception:
            continue  # unparseable expiry — skip

        strike      = pos.get("strike")
        delta       = pos.get("delta")
        stock_price = pos.get("latest_price")
        contracts   = pos.get("contracts")
        premium_col = ex.get("premium_collected")

        entries.append({
            "symbol":               ticker,
            "expiry":               expiry_fmt,
            "strike":               float(strike) if strike is not None else None,
            "right":                "P",
            "entry_date":           ex.get("timestamp"),
            "delta_at_entry":       round(delta, 4) if delta is not None else None,
            "buffer_pct_at_entry":  round(((stock_price - strike) / stock_price) * 100, 2)
                                    if stock_price and strike else None,
            "premium_per_contract": fill_price,
            "contracts":            contracts,
            "total_premium":        premium_col,
        })

    if entries:
        try:
            TRADE_LOG_FILE.write_text(json.dumps(entries, indent=2))
            logger.info(f"trade_log.json backfilled with {len(entries)} record(s) from state.json")
        except Exception as e:
            logger.warning(f"trade_log.json backfill write failed: {e}")

# ── IBKR helpers ──────────────────────────────────────────────

_ibkr_cache: dict = {"data": None, "ts": 0.0}
IBKR_CACHE_TTL = 30.0

_ACCT_TAGS = (
    "NetLiquidation,SettledCash,UnrealizedPnL,"
    "RealizedPnL,MaintenanceMargin,ExcessLiquidity,BuyingPower"
)
_TAG_KEY = {
    "NetLiquidation":   "account_value",
    "BuyingPower":      "buying_power",
    "SettledCash":      "settled_cash",
    "UnrealizedPnL":    "unrealized_pnl",
    "RealizedPnL":      "realized_pnl",
    "MaintenanceMargin":"maintenance_margin",
    "ExcessLiquidity":  "excess_liquidity",
}

def _safe_float(val, ndigits: int = 2):
    """Convert IBKR values to float, returning None for NaN / sentinel values."""
    try:
        f = float(val)
        if f != f or abs(f) > 1e15:   # NaN or IBKR's 1e308 "unavailable" sentinel
            return None
        return round(f, ndigits)
    except (TypeError, ValueError):
        return None

def _live_ready() -> dict:
    missing = []
    for var, placeholder in LIVE_PLACEHOLDERS.items():
        val = os.environ.get(var, "")
        if not val or val == placeholder:
            missing.append(var)
    for secret_name, placeholder in LIVE_REQUIRED_SECRETS.items():
        val = get_secret(secret_name)
        if not val or val == placeholder:
            missing.append(secret_name)
    account_live = get_secret("account_live")
    placeholder_account = LIVE_REQUIRED_SECRETS["account_live"]
    masked = (account_live[0] + "****") if (account_live and account_live != placeholder_account) else ""
    return {"ready": len(missing) == 0, "missing": missing, "account_masked": masked}

def _update_ibc_config(username: str, password: str, mode: str, port: int) -> None:
    if not IBC_CONFIG_FILE.exists():
        return
    content = IBC_CONFIG_FILE.read_text()
    content = re.sub(r'^IbLoginId=.*$', f'IbLoginId={username}', content, flags=re.MULTILINE)
    content = re.sub(r'^IbPassword=.*$', f'IbPassword={password}', content, flags=re.MULTILINE)
    content = re.sub(r'^TradingMode=.*$', f'TradingMode={mode}', content, flags=re.MULTILINE)
    content = re.sub(r'^ForceTwsApiPort=.*$', f'ForceTwsApiPort={port}', content, flags=re.MULTILINE)
    IBC_CONFIG_FILE.write_text(content)

def _restart_ibgateway() -> None:
    uid = os.getuid()
    subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{uid}/com.yourockfund.ibgateway"],
        capture_output=True, text=True, timeout=10,
    )

# ── Watchdog helpers ───────────────────────────────────────────

def _send_discord_alert(message: str) -> None:
    """Post a plain-text message to the main Discord webhook. No-ops if not configured."""
    try:
        webhook_url = _read_secret_or_env("discord_webhook_url", "DISCORD_WEBHOOK_URL")
        if not webhook_url:
            return
        import requests as req
        req.post(webhook_url, json={"content": message}, timeout=5)
    except Exception as e:
        print(f"[api/watchdog] Discord alert failed: {e}")


def _watchdog_check() -> None:
    """Check gateway and scheduler health; send Discord alerts on persistent failures."""
    now = datetime.now(PST)
    settings = load_settings()
    port = settings.get("ibkr_port", 4002)

    # ── IB Gateway port reachability ─────────────────────────────
    gw_up = _gateway_running(port)
    if not gw_up:
        if _watchdog_state["gateway_down_since"] is None:
            _watchdog_state["gateway_down_since"] = now
        down_sec = (now - _watchdog_state["gateway_down_since"]).total_seconds()
        if (down_sec >= ALERT_THRESHOLD
                and _watchdog_state["last_gateway_alert"] is None):
            _watchdog_state["last_gateway_alert"] = now
            host = os.environ.get("IBKR_HOST", "ib_gateway")
            _send_discord_alert(
                f"🚨 **YRVI** IB Gateway API port unreachable for {int(down_sec / 60)} min "
                f"(`{host}:{port}`). Gateway may not have logged in or is stuck on a dialog. "
                f"VNC available on host port 5900.\n"
                f"🔴 Manual restart required: "
                f"`docker compose --env-file .env.compose restart ib_gateway`"
            )
    else:
        if _watchdog_state["gateway_down_since"] is not None:
            down_sec = (now - _watchdog_state["gateway_down_since"]).total_seconds()
            _send_discord_alert(
                f"✅ **YRVI** IB Gateway port is reachable again "
                f"(was down {int(down_sec / 60)} min)."
            )
        _watchdog_state["gateway_down_since"] = None
        _watchdog_state["last_gateway_alert"] = None

    # ── IBKR API connection (only when gateway port is up) ────────
    # Port open but ib_insync failing → gateway logged in but API broken (Scenario 3)
    # or stuck on an unexpected dialog that kept the port open (Scenario 2 edge case).
    if gw_up:
        ibkr = _get_ibkr_data(settings)
        if not ibkr["connected"]:
            if _watchdog_state["ibkr_down_since"] is None:
                _watchdog_state["ibkr_down_since"] = now
            down_sec = (now - _watchdog_state["ibkr_down_since"]).total_seconds()
            if (down_sec >= ALERT_THRESHOLD
                    and _watchdog_state["last_ibkr_alert"] is None):
                _watchdog_state["last_ibkr_alert"] = now
                err = ibkr.get("error") or "unknown error"
                _send_discord_alert(
                    f"🚨 **YRVI** IB Gateway port is open but IBKR API connection failed "
                    f"for {int(down_sec / 60)} min. Error: `{err}`. "
                    f"Gateway may be frozen on a 2FA or confirmation dialog. "
                    f"VNC available on host port 5900.\n"
                    f"🔴 Manual restart required: "
                    f"`docker compose --env-file .env.compose restart ib_gateway`"
                )
        else:
            if _watchdog_state["ibkr_down_since"] is not None:
                down_sec = (now - _watchdog_state["ibkr_down_since"]).total_seconds()
                _send_discord_alert(
                    f"✅ **YRVI** IBKR API connection restored "
                    f"(was failing for {int(down_sec / 60)} min)."
                )
            _watchdog_state["ibkr_down_since"] = None
            _watchdog_state["last_ibkr_alert"] = None
    else:
        # Gateway port is down — clear IBKR state; its episode timer resets when port returns
        _watchdog_state["ibkr_down_since"] = None
        _watchdog_state["last_ibkr_alert"] = None

    # ── Scheduler heartbeat ───────────────────────────────────────
    sched_ok = _scheduler_pid() is not None
    if not sched_ok:
        if _watchdog_state["scheduler_down_since"] is None:
            _watchdog_state["scheduler_down_since"] = now
        down_sec = (now - _watchdog_state["scheduler_down_since"]).total_seconds()
        if (down_sec >= ALERT_THRESHOLD
                and _watchdog_state["last_scheduler_alert"] is None):
            _watchdog_state["last_scheduler_alert"] = now
            _send_discord_alert(
                f"🚨 **YRVI** Scheduler heartbeat stale for {int(down_sec / 60)} min.\n"
                f"🔴 Manual restart required: "
                f"`docker compose --env-file .env.compose restart scheduler`"
            )
    else:
        if _watchdog_state["scheduler_down_since"] is not None:
            down_sec = (now - _watchdog_state["scheduler_down_since"]).total_seconds()
            _send_discord_alert(
                f"✅ **YRVI** Scheduler heartbeat resumed "
                f"(was stale for {int(down_sec / 60)} min)."
            )
        _watchdog_state["scheduler_down_since"] = None
        _watchdog_state["last_scheduler_alert"] = None


def _run_watchdog() -> None:
    """Background daemon thread: poll gateway + scheduler health every 5 minutes."""
    time.sleep(90)  # let containers finish starting before the first check
    while True:
        try:
            _watchdog_check()
        except Exception as e:
            print(f"[api/watchdog] Unhandled error: {e}")
        time.sleep(WATCHDOG_INTERVAL)


def _run_gateway_log_monitor() -> None:
    """Tail ib_gateway logs via Docker SDK and alert on login failures or lockouts."""
    global _gateway_login_status
    import docker as docker_sdk
    time.sleep(60)  # let the gateway container finish starting

    while True:
        _gateway_login_status = "unknown"
        terminal = False
        container = None
        started_at = ""

        try:
            client = docker_sdk.from_env()
            container = client.containers.get("ib_gateway")
            container.reload()
            started_at = container.attrs["State"]["StartedAt"]

            login_attempts = 0

            for chunk in container.logs(stream=True, follow=True, tail=100):
                line = chunk.decode("utf-8").strip()
                ll = line.lower()

                if "locked out" in ll:
                    _gateway_login_status = "locked"
                    _send_discord_alert(
                        "🔒 IBKR account locked out — too many failed login attempts. "
                        "Stop the gateway and reset your password."
                    )
                    terminal = True
                    break

                if "login failed" in ll or "authentication failed" in ll:
                    _gateway_login_status = "failed"
                    _send_discord_alert(
                        "❌ IB Gateway login failed — check your IBKR credentials."
                    )
                    terminal = True
                    break

                if "login attempt" in ll:
                    login_attempts += 1
                    if login_attempts > 3:
                        _gateway_login_status = "failed"
                        _send_discord_alert(
                            "⚠️ IB Gateway repeated login failures — possible wrong password."
                        )
                        terminal = True
                        break

                if "login has completed" in ll or "logged in" in ll:
                    login_attempts = 0
                    _gateway_login_status = "ok"

        except Exception as e:
            print(f"[api/gateway-log-monitor] error: {e}")

        if terminal:
            # Hold the terminal status until the container is actually restarted.
            print(f"[api/gateway-log-monitor] terminal state ({_gateway_login_status}), "
                  "pausing until container restarts")
            while True:
                time.sleep(60)
                try:
                    container.reload()
                    new_started_at = container.attrs["State"]["StartedAt"]
                    if new_started_at != started_at:
                        print("[api/gateway-log-monitor] container restarted, resuming")
                        break
                except Exception:
                    pass
        else:
            time.sleep(15)  # brief pause before reconnecting after a non-terminal exit


@app.on_event("startup")
async def _startup() -> None:
    t = threading.Thread(target=_run_watchdog, daemon=True, name="yrvi-watchdog")
    t.start()
    print("[api] Health watchdog started")
    if CONTAINERIZED:
        t2 = threading.Thread(target=_run_gateway_log_monitor, daemon=True, name="yrvi-gateway-log-monitor")
        t2.start()
        print("[api] Gateway log monitor started")


@app.post("/api/restart-scheduler")
def restart_scheduler():
    if CONTAINERIZED:
        try:
            r = subprocess.run(
                ["docker", "restart", "yrvi-scheduler-1"],
                capture_output=True, text=True, timeout=30,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=503, detail="docker CLI not installed in api container")
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="docker restart timed out after 30s")
        if r.returncode != 0:
            msg = (r.stderr or r.stdout or "").strip() or "unknown docker error"
            raise HTTPException(status_code=500, detail=f"docker restart failed: {msg}")
        return {"success": True, "container": "yrvi-scheduler-1"}

    uid = os.getuid()
    service = "com.yourockfund.scheduler"
    errors: list[str] = []

    # 1. Try kickstart -k (kills running instance then relaunches)
    r = subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{uid}/{service}"],
        capture_output=True, text=True, timeout=10,
    )
    print(f"[api] kickstart stdout: {r.stdout!r}  stderr: {r.stderr!r}  rc={r.returncode}")
    if r.returncode != 0:
        errors.append(f"kickstart rc={r.returncode}: {r.stderr.strip() or r.stdout.strip()}")

        # 2. Fallback: stop then start
        r2 = subprocess.run(["launchctl", "stop", service], capture_output=True, text=True, timeout=10)
        print(f"[api] stop rc={r2.returncode} stderr={r2.stderr!r}")
        time.sleep(1)
        r3 = subprocess.run(["launchctl", "start", service], capture_output=True, text=True, timeout=10)
        print(f"[api] start rc={r3.returncode} stderr={r3.stderr!r}")
        if r3.returncode != 0:
            errors.append(f"stop/start rc={r3.returncode}: {r3.stderr.strip() or r3.stdout.strip()}")

    time.sleep(2)
    pid = _scheduler_pid()
    if pid is None:
        detail = "Scheduler did not start. " + " | ".join(errors) if errors else "Scheduler did not start — check scheduler_log.txt"
        raise HTTPException(status_code=500, detail=detail)
    return {"success": True, "pid": pid, "errors": errors}


class ShutdownRequest(BaseModel):
    confirm: str


# Stop order: api is last so the HTTP response can return before this
# container kills itself.
SHUTDOWN_CONTAINERS = [
    "yrvi-scheduler-1",
    "yrvi-web-1",
    "ib_gateway",
    "yrvi-secrets-1",
    "yrvi-api-1",
]


@app.post("/api/shutdown")
def shutdown_stack(body: ShutdownRequest):
    if body.confirm != "shutdown":
        raise HTTPException(status_code=400, detail='Confirmation token required: send {"confirm":"shutdown"}')
    if not CONTAINERIZED:
        raise HTTPException(status_code=501, detail="Shutdown is only available in Docker mode")

    def do_shutdown():
        time.sleep(1)  # let the HTTP response flush before we start stopping containers
        for name in SHUTDOWN_CONTAINERS:
            try:
                subprocess.run(
                    ["docker", "stop", name],
                    capture_output=True, text=True, timeout=30,
                )
            except Exception:
                # best-effort — keep going so api (last) still gets stopped
                pass

    threading.Thread(target=do_shutdown, daemon=True).start()
    return {"success": True, "message": "Shutdown initiated"}


_IBKR_EMPTY: dict = {
    "connected": False, "account_value": None, "buying_power": None,
    "settled_cash": None, "unrealized_pnl": None, "realized_pnl": None,
    "maintenance_margin": None, "excess_liquidity": None,
    "account": None, "account_summary": None, "portfolio": [], "error": None,
}

def _get_ibkr_data(settings: dict) -> dict:
    now = time.time()
    if _ibkr_cache["data"] and (now - _ibkr_cache["ts"]) < IBKR_CACHE_TTL:
        return _ibkr_cache["data"]

    result      = dict(_IBKR_EMPTY)
    port        = settings.get("ibkr_port", 4002)
    host        = os.environ.get("IBKR_HOST", "127.0.0.1")
    account_env = settings.get("account") or os.environ.get("ACCOUNT", "")

    # ib_insync's sync API requires an event loop on the calling thread.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    client_id = random.randint(100, 999)
    print(f"[api] IBKR connect → {host}:{port} clientId={client_id}")
    from ib_insync import IB
    ib = IB()
    try:
        ib.connect(host, port, clientId=client_id, timeout=10, readonly=False)
        accts = ib.managedAccounts()
        acct  = account_env or (accts[0] if accts else "")
        print(f"[api] IBKR connected — accounts: {accts}")
        if acct:
            result["account"] = acct

            # ── Account summary
            summary_dict = {item.tag: item.value for item in ib.accountSummary(acct)}
            print(f"[api] accountSummary tags: {list(summary_dict.keys())}")
            result["account_value"]      = _safe_float(summary_dict.get("NetLiquidation", 0))
            result["buying_power"]       = _safe_float(summary_dict.get("BuyingPower",    0))
            result["settled_cash"]       = _safe_float(summary_dict.get("TotalCashValue", 0))
            result["unrealized_pnl"]     = _safe_float(summary_dict.get("UnrealizedPnL",  0))
            result["realized_pnl"]       = _safe_float(summary_dict.get("RealizedPnL",    0))
            result["maintenance_margin"] = _safe_float(summary_dict.get("MaintMarginReq", 0))
            result["excess_liquidity"]   = _safe_float(summary_dict.get("AvailableFunds", 0))
            result["account_summary"] = {
                "net_liquidation":    result["account_value"],
                "settled_cash":       result["settled_cash"],
                "unrealized_pnl":     result["unrealized_pnl"],
                "realized_pnl":       result["realized_pnl"],
                "maintenance_margin": result["maintenance_margin"],
                "excess_liquidity":   result["excess_liquidity"],
                "buying_power":       result["buying_power"],
            }
            result["connected"] = True

            # ── Positions via reqPositions (no subscription, no hang)
            try:
                ib.reqPositions()
                ib.sleep(2)
                raw_positions = ib.positions()
                print(f"[api] reqPositions returned {len(raw_positions)} items")

                # ── Debug: check if portfolio() has data after reqPositions alone
                portfolio_lookup: dict = {}
                portfolio_items = ib.portfolio()
                logger.info(f"portfolio() without subscription: {len(portfolio_items)} items")
                for item in portfolio_items:
                    logger.info(f"  {item.contract.symbol}: mktVal={item.marketValue} pnl={item.unrealizedPNL}")
                for item in portfolio_items:
                    portfolio_lookup[item.contract.conId] = {
                        "marketPrice":   _safe_float(item.marketPrice),
                        "marketValue":   _safe_float(item.marketValue),
                        "unrealizedPNL": _safe_float(item.unrealizedPNL),
                    }

                portfolio = []
                for pos in raw_positions:
                    c        = pos.contract
                    is_opt   = c.secType == "OPT"
                    pnl_data = portfolio_lookup.get(c.conId, {})
                    portfolio.append({
                        "symbol":        c.symbol,
                        "secType":       c.secType,
                        "right":         c.right if is_opt else None,
                        "strike":        _safe_float(c.strike, 4) if is_opt else None,
                        "expiry":        c.lastTradeDateOrContractMonth if is_opt else None,
                        "position":      _safe_float(pos.position, 0),
                        "avgCost":       _safe_float(pos.avgCost, 4),
                        "marketPrice":   pnl_data.get("marketPrice"),
                        "marketValue":   pnl_data.get("marketValue"),
                        "unrealizedPNL": pnl_data.get("unrealizedPNL"),
                    })
                portfolio.sort(key=lambda x: (0 if x["secType"] == "STK" else 1, x["symbol"]))
                result["portfolio"] = portfolio
            except Exception as pe:
                print(f"[api] Positions fetch failed (account_summary preserved): {pe}")

        print(f"[api] net_liq={result['account_value']}  "
              f"unrealized={result['unrealized_pnl']}  "
              f"positions={len(result['portfolio'])}")
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"[api] IBKR connection failed — {msg}")
        traceback.print_exc()
        result["error"] = msg
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass

    _ibkr_cache["data"] = result
    _ibkr_cache["ts"]   = now
    return result

def _scheduler_pid() -> Optional[int]:
    if CONTAINERIZED:
        try:
            hb = json.loads(HEARTBEAT_FILE.read_text())
            ts = datetime.fromisoformat(hb["timestamp"])
            if datetime.now(PST) - ts < timedelta(minutes=3):
                return 1
        except Exception:
            pass
        return None
    try:
        r = subprocess.run(["pgrep", "-f", "python.*scheduler.py"],
                           capture_output=True, text=True)
        pids = [p.strip() for p in r.stdout.strip().split("\n") if p.strip()]
        return int(pids[0]) if pids else None
    except Exception:
        return None

def _gateway_running(port: int) -> bool:
    if CONTAINERIZED:
        host = os.environ.get("IBKR_HOST", "127.0.0.1")
        try:
            with socket.create_connection((host, port), timeout=3):
                return True
        except OSError:
            return False
    try:
        r = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True)
        return bool(r.stdout.strip())
    except Exception:
        return False

def _parse_exec_time(settings: dict) -> tuple:
    try:
        h, m = map(int, settings.get("execution_time", "10:00").split(":"))
        return h, m
    except Exception:
        return 10, 0

def _next_execution() -> str:
    settings = load_settings()
    exec_h, exec_m = _parse_exec_time(settings)
    now = datetime.now(PST)
    days = (7 - now.weekday()) % 7  # Monday = 0
    if days == 0 and (now.hour > exec_h or (now.hour == exec_h and now.minute >= exec_m)):
        days = 7
    target = (now + timedelta(days=days)).replace(hour=exec_h, minute=exec_m, second=0, microsecond=0)
    return target.isoformat()

# ── Endpoints ─────────────────────────────────────────────────

@app.get("/api/status")
def get_status():
    settings = load_settings()
    ibkr = _get_ibkr_data(settings)
    port = settings.get("ibkr_port", 4002)
    state = load_state()
    wheel_count = sum(
        1 for h in state.get("wheel_holdings", []) if h.get("shares", 0) > 0
    )
    return {
        "gateway_running":    _gateway_running(port),
        "scheduler_pid":      _scheduler_pid(),
        "ibkr_connected":     ibkr["connected"],
        "ibkr_error":         ibkr.get("error"),
        "account_value":      ibkr["account_value"],
        "buying_power":       ibkr["buying_power"],
        "unrealized_pnl":     ibkr.get("unrealized_pnl"),
        "net_liquidation":    ibkr.get("account_value"),
        "account":            ibkr["account"],
        "next_execution":       _next_execution(),
        "trading_mode":         settings.get("trading_mode", "paper"),
        "execution_time":       settings.get("execution_time", "10:00"),
        "wheel_count":          wheel_count,
        "gateway_login_status": _gateway_login_status,
    }

@app.get("/api/positions")
def get_positions():
    state = load_state()
    positions = state.get("positions", [])
    executions = state.get("executions", [])
    exec_map = {e.get("ticker"): e for e in executions if "ticker" in e}

    enriched = []
    for p in positions:
        ex = exec_map.get(p["ticker"], {})
        enriched.append({
            **p,
            "status":            ex.get("status", "unknown"),
            "fill_price":        ex.get("fill_price"),
            "order_type":        ex.get("order_type"),
            "premium_collected": ex.get("premium_collected", 0),
            "simulated":         ex.get("simulated", False),
            "exec_timestamp":    ex.get("timestamp"),
        })

    settings = load_settings()
    ibkr = _get_ibkr_data(settings)

    # Enrich live portfolio items with trade_log metadata
    _backfill_trade_log()
    trade_log = load_trade_log()
    tl_index: dict = {}
    for rec in trade_log:
        k = (rec.get("symbol"), rec.get("expiry"), rec.get("strike"), rec.get("right"))
        tl_index[k] = rec

    portfolio = ibkr.get("portfolio", [])
    enriched_portfolio = []
    for item in portfolio:
        tl_key = (item.get("symbol"), item.get("expiry"), item.get("strike"), item.get("right"))
        tl = tl_index.get(tl_key, {})
        enriched_portfolio.append({
            **item,
            "delta_at_entry":       tl.get("delta_at_entry"),
            "buffer_pct_at_entry":  tl.get("buffer_pct_at_entry"),
            "premium_per_contract": tl.get("premium_per_contract"),
            "total_premium":        tl.get("total_premium"),
        })

    return {
        "positions":       enriched,
        "csp_positions":   enriched,
        "wheel_holdings":  state.get("wheel_holdings", []),
        "weekly_pnl":      state.get("weekly_pnl", {}),
        "run_date":        state.get("run_date"),
        "monday_context":  state.get("monday_context", {}),
        "portfolio":       enriched_portfolio,
        "account_summary": ibkr.get("account_summary"),  # None when IBKR disconnected
    }

@app.get("/api/performance")
def get_performance():
    settings = load_settings()
    ytd = load_ytd()
    budget = settings.get("fund_budget", 250_000)

    raw_weeks = ytd.get("weeks", [])
    # Normalize: ensure every week has premium_collected (backwards-compat with old realized field)
    weeks = [
        {**w, "premium_collected": w.get("premium_collected", w.get("realized", 0)),
               "shares_sold_pnl":  w.get("shares_sold_pnl", 0),
               "total_realized":   w.get("total_realized", w.get("realized", 0))}
        for w in raw_weeks
    ]
    total = ytd.get("total_premium", 0.0)
    total_realized = round(sum(w["total_realized"] for w in weeks), 2)
    weeks_traded = ytd.get("weeks_traded", 0)
    avg_yield = (total / weeks_traded / budget * 100) if weeks_traded and budget else 0.0
    progress_pct = (total / ANNUAL_TARGET * 100) if ANNUAL_TARGET else 0.0

    return {
        "weeks":          weeks,
        "total_premium":  total,
        "total_realized": total_realized,
        "weeks_traded":   weeks_traded,
        "avg_yield_pct":  round(avg_yield, 3),
        "best_week":      ytd.get("best_week"),
        "worst_week":     ytd.get("worst_week"),
        "annual_target":  ANNUAL_TARGET,
        "progress_pct":   round(progress_pct, 1),
    }

@app.get("/api/screener")
def run_screener():
    """Run screener + position sizer. Takes ~10 seconds."""
    settings = load_settings()
    try:
        import importlib
        import sys
        for mod_name in ["config", "screener", "position_sizer"]:
            if mod_name in sys.modules:
                importlib.reload(sys.modules[mod_name])

        from screener import get_top_targets
        from position_sizer import size_all

        n      = settings.get("num_positions", 5)
        budget = settings.get("fund_budget", 250_000)

        # Deduct capital reserved for active wheel holdings
        state          = load_state()
        wheel_holdings = state.get("wheel_holdings", [])
        active_holdings = [h for h in wheel_holdings if h.get("shares", 0) > 0]
        reserved_capital   = round(sum(
            h["shares"] * h.get("assigned_strike", 0.0) for h in active_holdings
        ), 2)
        active_wheel_count = len(active_holdings)
        adjusted_budget    = budget - reserved_capital
        target_fills       = max(1, n - active_wheel_count)

        targets   = get_top_targets(n * 2)
        positions = size_all(targets, budget=adjusted_budget, num_positions=target_fills)

        total_premium = sum(p.get("premium_total", 0) for p in positions)
        total_capital = sum(p.get("capital_used", 0) for p in positions)

        return {
            "positions":          positions,
            "raw_targets":        targets,
            "total_premium":      total_premium,
            "total_capital":      total_capital,
            "blended_yield":      round(total_premium / total_capital * 100 if total_capital else 0, 3),
            "budget":             adjusted_budget,
            "total_budget":       budget,
            "reserved_capital":   reserved_capital,
            "active_wheel_count": active_wheel_count,
            "wheel_holdings":     wheel_holdings,
            "run_at":             datetime.now(PST).isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/live-ready")
def get_live_ready():
    return _live_ready()

@app.get("/api/settings")
def get_settings_endpoint():
    return load_settings()

class SettingsUpdate(BaseModel):
    fund_budget:              Optional[float] = None
    num_positions:            Optional[int]   = None
    min_position_size:        Optional[float] = None
    max_position_size:        Optional[float] = None
    max_delta:                Optional[float] = None
    min_buffer_pct:           Optional[float] = None
    earnings_filter_days:     Optional[int]   = None
    max_spread_pct:           Optional[float] = None
    min_bid_yield_pct:        Optional[float] = None
    max_spread_hard_cap:      Optional[float] = None
    dry_run:                  Optional[bool]  = None
    ibkr_port:                Optional[int]   = None
    discord_webhook_enabled:  Optional[bool]  = None
    trading_mode:             Optional[str]   = None
    execution_time:           Optional[str]   = None

@app.post("/api/settings")
def update_settings(body: SettingsUpdate):
    current = load_settings()
    updates = {k: v for k, v in body.dict().items() if v is not None}
    current.update(updates)
    save_settings(current)
    return current

@app.get("/api/settings/timezone")
def get_timezone():
    return {"timezone": load_settings().get("timezone") or "America/Los_Angeles"}

class TimezoneUpdate(BaseModel):
    timezone: str

@app.post("/api/settings/timezone")
def set_timezone(body: TimezoneUpdate):
    tz = (body.timezone or "").strip()
    if not tz:
        raise HTTPException(status_code=400, detail="timezone is required")
    try:
        ZoneInfo(tz)
    except ZoneInfoNotFoundError:
        raise HTTPException(status_code=400, detail=f"Invalid IANA timezone: {tz}")
    current = load_settings()
    current["timezone"] = tz
    save_settings(current)
    return {"timezone": tz}

class SecretValueRequest(BaseModel):
    value: str

@app.get("/api/secrets/status")
def secrets_status():
    import requests as req
    try:
        r = req.get(f"{SECRETS_SERVICE_URL}/secrets/status", timeout=3)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {"complete": False, "error": "secrets container unreachable", "secrets": {}}

@app.get("/api/secrets/{name}")
def get_secret_endpoint(name: str):
    import requests as req
    try:
        r = req.get(f"{SECRETS_SERVICE_URL}/secret/{name}", timeout=3)
    except Exception:
        raise HTTPException(status_code=503, detail="secrets container unreachable")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="secret not found")
    r.raise_for_status()
    return r.json()

@app.post("/api/secrets/{name}")
def set_secret_endpoint(name: str, body: SecretValueRequest):
    import requests as req
    try:
        r = req.post(f"{SECRETS_SERVICE_URL}/secret/{name}", json={"value": body.value}, timeout=3)
    except Exception:
        raise HTTPException(status_code=503, detail="secrets container unreachable")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="unknown secret")
    r.raise_for_status()
    return {"success": True}

class TradingModeRequest(BaseModel):
    mode: str
    confirmation: str

@app.post("/api/trading-mode")
def set_trading_mode(body: TradingModeRequest):
    if body.confirmation != "CONFIRM":
        raise HTTPException(status_code=400, detail="confirmation must be exactly 'CONFIRM'")
    if body.mode not in ("paper", "live"):
        raise HTTPException(status_code=400, detail="mode must be 'paper' or 'live'")

    if body.mode == "live":
        ready = _live_ready()
        if not ready["ready"]:
            missing_str = ", ".join(ready["missing"])
            raise HTTPException(
                status_code=400,
                detail=f"Live credentials not configured. Add these to your .env file and restart YRVI: {missing_str}",
            )

    current = load_settings()
    current["trading_mode"] = body.mode
    current["ibkr_port"]    = 4001 if body.mode == "live" else 4002

    if body.mode == "live":
        current["account"] = get_secret("account_live")
        _update_ibc_config(
            username=get_secret("tws_userid_live"),
            password=os.environ.get("IBKR_PASSWORD_LIVE", ""),
            mode="live",
            port=4001,
        )
        _restart_ibgateway()

    save_settings(current)

    # Bust cache so next /api/status re-checks IBKR
    _ibkr_cache["data"] = None
    _ibkr_cache["ts"]   = 0.0

    try:
        webhook_url = _read_secret_or_env("discord_webhook_url", "DISCORD_WEBHOOK_URL")
        if webhook_url and current.get("discord_webhook_enabled", True):
            import requests as req
            req.post(webhook_url, json={
                "content": f"⚠️ YRVI trading mode switched to **{body.mode.upper()}** via web dashboard"
            }, timeout=5)
    except Exception:
        pass

    return {"success": True, "trading_mode": body.mode, "ibkr_port": current["ibkr_port"]}

@app.get("/api/trade-history")
def get_trade_history():
    state = load_state()
    ytd = load_ytd()

    positions = state.get("positions", [])
    executions = state.get("executions", [])
    pos_map = {p["ticker"]: p for p in positions}

    enriched = []
    for ex in executions:
        t = ex.get("ticker", "")
        pos = pos_map.get(t, {})
        enriched.append({
            **ex,
            "screener_premium": pos.get("premium"),
            "strike":           pos.get("strike"),
            "buffer_pct":       pos.get("buffer_pct"),
            "delta":            pos.get("delta"),
            "capital_used":     pos.get("capital_used"),
        })

    return {
        "current_week": {
            "run_date":   state.get("run_date"),
            "executions": enriched,
            "weekly_pnl": state.get("weekly_pnl", {}),
        },
        "weekly_summaries": ytd.get("weeks", []),
        "total_premium":    ytd.get("total_premium", 0),
    }

@app.get("/api/version")
def get_version():
    version_file = BASE_DIR / "VERSION"
    version = version_file.read_text().strip() if version_file.exists() else "unknown"
    return {"version": version, "branch": "main"}

_GITHUB_VERSION_URL = (
    "https://raw.githubusercontent.com/controllinghand/"
    "you_rock_fund/main/VERSION"
)

@app.get("/api/version/check")
def version_check():
    version_file = BASE_DIR / "VERSION"
    current = version_file.read_text().strip() if version_file.exists() else "unknown"
    try:
        import requests as req
        r = req.get(_GITHUB_VERSION_URL, timeout=5)
        r.raise_for_status()
        latest = r.text.strip()
        return {"current": current, "latest": latest, "up_to_date": current == latest}
    except Exception:
        return {"current": current, "latest": None, "up_to_date": None, "error": "unavailable"}


@app.post("/api/version/upgrade")
def version_upgrade():
    version_file = BASE_DIR / "VERSION"
    current = version_file.read_text().strip() if version_file.exists() else "unknown"

    # Confirm there is actually an update to apply
    try:
        import requests as req
        r = req.get(_GITHUB_VERSION_URL, timeout=5)
        r.raise_for_status()
        latest = r.text.strip()
    except Exception:
        return {"success": False,
                "output": "Could not fetch latest version from GitHub — upgrade aborted"}

    if current == latest:
        return {"success": False,
                "output": f"Already up to date ({current}) — no upgrade needed"}

    output_parts: list[str] = []

    # ── Step 1: git pull ──────────────────────────────────────
    try:
        pull = subprocess.run(
            ["git", "pull", "origin", "main"],
            capture_output=True, text=True, timeout=60,
            cwd=str(BASE_DIR),
        )
        output_parts.append(
            f"$ git pull origin main\n{(pull.stdout + pull.stderr).strip()}"
        )
        if pull.returncode != 0:
            return {"success": False, "output": "\n\n".join(output_parts)}
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "git pull timed out after 60s — upgrade aborted"}
    except Exception as e:
        return {"success": False, "output": f"git pull failed: {e}"}

    # ── Step 2: yrvi-build.sh all --paper ────────────────────
    # Launched via Popen (non-blocking) so this response returns before
    # yrvi-build.sh rebuilds and restarts the containers (including this one).
    build_script = BASE_DIR / "scripts" / "yrvi-build.sh"
    if not build_script.exists():
        output_parts.append(
            "scripts/yrvi-build.sh not found — run manually from terminal"
        )
        return {"success": False, "output": "\n\n".join(output_parts)}

    try:
        subprocess.Popen(
            ["bash", str(build_script), "all", "--paper"],
            cwd=str(BASE_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        output_parts.append(
            "$ bash scripts/yrvi-build.sh all --paper\n"
            "(launched — containers rebuilding and restarting, polling /health to detect restart)"
        )
        return {"success": True, "output": "\n\n".join(output_parts)}
    except Exception as e:
        output_parts.append(
            f"Failed to launch yrvi-build.sh: {e}\nRun manually from terminal."
        )
        return {"success": False, "output": "\n\n".join(output_parts)}


@app.get("/api/health")
def health_check():
    """Liveness probe used by Docker healthcheck — always 200 while the process is alive."""
    return {"status": "ok"}

@app.post("/api/discord-test")
def test_discord():
    webhook_url = _read_secret_or_env("discord_webhook_url", "DISCORD_WEBHOOK_URL")
    if not webhook_url:
        raise HTTPException(status_code=400, detail="Discord webhook not configured — add URL to docker/secrets/discord_webhook_url")
    try:
        import requests as req
        r = req.post(webhook_url, json={"content": "🔔 YRVI Dashboard — test notification"}, timeout=5)
        r.raise_for_status()
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
