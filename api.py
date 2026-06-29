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
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from secrets_client import get_secret
from market_calendar import is_market_holiday, is_first_trading_day_of_week

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

LIVE_REQUIRED_SECRETS = {
    "account_live":      "your_live_account_number (starts with U)",
    "tws_userid_live":   "your_live_ibkr_username",
    "tws_password_live": "your_live_ibkr_password",
}

PST = ZoneInfo("America/Los_Angeles")
ET  = ZoneInfo("America/New_York")
CONTAINERIZED = os.environ.get("YRVI_CONTAINERIZED", "0") == "1"
HEARTBEAT_FILE = BASE_DIR / "scheduler_heartbeat.json"
GATEWAY_STATUS_FILE = (
    Path("/data/gateway_status.json") if CONTAINERIZED
    else BASE_DIR / "gateway_status.json"
)
# Weekly IB Key 2FA token: IBKR invalidates it every Sunday 1:00 AM ET. The first
# gateway restart after that needs a manual IB Key phone approval; subsequent daily
# restarts run unattended until the next Sunday. We record when the token became
# active (IBC log: "autorestart file found … authentication will not be required")
# and clear it when 2FA is required again ("autorestart file not found …").
WEEKLY_TOKEN_FILE = (
    Path("/data/weekly_token_established") if CONTAINERIZED
    else BASE_DIR / "weekly_token_established"
)
# In-app alert feed (v4): every alert that goes to Discord is also persisted here so
# the dashboard has a first-class, standalone record — no dependency on Discord being
# configured or reachable. File-backed (not in-memory) because the api container
# restarts on trading-mode switches and upgrades, exactly when the history matters.
# Capped ring buffer; each box keeps its OWN feed (no cross-box aggregation by design).
ALERTS_FILE = (
    Path("/data/alerts.json") if CONTAINERIZED
    else BASE_DIR / "alerts.json"
)
ALERTS_MAX = 200
_alerts_lock = threading.Lock()
SECRETS_SERVICE_URL = "http://secrets:8001"
# Feedback webhook — defaults to the shared You Rock Club feedback channel so every
# box works out of the box; a box can override it via the discord_feedback_webhook_url secret.
_FEEDBACK_WEBHOOK_DEFAULT = "https://discord.com/api/webhooks/1506828497757147167/364xR_1wKCz1LPREGwqpE9mmvQkwkC-EiSirRqWua69eCs-rma5Hc4j7RGIwqBas0jyE"
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
    "ibkr_soft_restart_at": None,   # when we fired an auto soft restart this outage
    "gateway_full_restart_at": None,  # one-shot full restart this gateway-port-down episode
    "ibkr_full_restart_at":    None,  # one-shot full restart this handshake-dead episode
    "last_full_restart_at":    None,  # cross-episode cooldown (lockout guard)
}
_gateway_login_status: str = "unknown"
_gateway_last_event:   str = ""
_gateway_recent_lines: list = []   # rolling buffer of relevant log lines

WATCHDOG_INTERVAL = 300   # seconds between checks
ALERT_THRESHOLD   = 600   # seconds a failure must persist before we alert
SOFT_RESTART_GRACE = 240  # seconds to let an auto soft restart recover before paging
FULL_RESTART_GRACE = 360  # seconds to let an auto full restart recover before paging
                          # (longer than soft: a full restart re-runs login, and on
                          #  LIVE it waits on the human approving the IB Key 2FA push)
FULL_RESTART_COOLDOWN = 1800  # min seconds between auto full restarts of the gateway.
                              # Lockout guard: combined with the one-shot-per-episode
                              # cap, the watchdog can never loop fresh logins into an
                              # IBKR account lockout.

# Ports that mean a LIVE IBKR session (vs paper: 4002/4004). A full restart forces a
# fresh login, which on live triggers an IB Key 2FA push the human must approve.
_LIVE_IBKR_PORTS = (4001, 4003)

# Operator-facing recovery hint appended to gateway pages. The dashboard button
# works for a non-technical operator on any OS (one click → POST /api/gateway/restart,
# which the api container runs via docker.sock). The docker command stays as a
# fallback for advanced users or when the dashboard itself is unreachable.
_MANUAL_RESTART_HINT = (
    "🔧 **Fix:** open the dashboard → **Help** → **Restart Gateway** (one click; "
    "live re-login needs the IB Key 2FA push). VNC also available on host port 5900.\n"
    "🔴 Advanced/fallback: `docker compose --env-file .env.compose restart ib_gateway`"
)

# Auto-restart suppression: read the gateway's configured restart time and
# suppress gateway/IBKR alerts for this many seconds after it fires.
# Avoids false alarms on slower machines that take longer to log back in.
# Env vars are fallbacks; settings.json values take precedence and update live.
_AUTO_RESTART_TIME_ENV     = os.environ.get("AUTO_RESTART_TIME", "11:59 PM").strip()
_AUTO_RESTART_SUPPRESS_ENV = int(os.environ.get("AUTO_RESTART_SUPPRESS_SECS", "1800"))


def _in_auto_restart_window(now: datetime) -> bool:
    """Return True if now falls within the post-restart suppression window."""
    try:
        cfg = load_settings()
        time_str     = (cfg.get("auto_restart_time") or _AUTO_RESTART_TIME_ENV).strip()
        suppress_sec = int(cfg.get("auto_restart_suppress_mins",
                                   _AUTO_RESTART_SUPPRESS_ENV // 60)) * 60
        t = datetime.strptime(time_str, "%I:%M %p").time()
        for delta_days in (0, -1):
            restart_dt = now.replace(
                hour=t.hour, minute=t.minute, second=0, microsecond=0
            ) + timedelta(days=delta_days)
            elapsed = (now - restart_dt).total_seconds()
            if -120 <= elapsed <= suppress_sec:
                return True
        return False
    except Exception:
        return False

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
        iv          = pos.get("iv_atm")
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
            "iv_at_entry":          round(iv, 4) if iv is not None else None,
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
    subprocess.run(
        ["docker", "restart", "ib_gateway"],
        capture_output=True, text=True, timeout=60,
    )


# Port the IBC command server listens on inside the gateway container (enabled on
# loopback by the gateway entrypoint). Sending "RESTART" to it triggers the same
# soft restart as the nightly AutoRestartTime: the gateway relaunches reusing its
# authenticated session token, so there is NO re-login and NO 2FA. That's what lets
# the watchdog self-heal a wedged API listener (port open, handshake dead) without
# paging a human or risking a 2FA lockout on the live account — unlike a full
# `docker restart`, which forces a fresh login.
IBC_COMMAND_PORT = int(os.environ.get("IBC_COMMAND_PORT", "7462"))


def _get_gateway_tws_version():
    """Read the gateway's TWS/Gateway build version (e.g. '10.48.1b') from the
    ib_gateway container's env (TWS_MAJOR_VRSN) via docker inspect.

    The IBKR API only exposes the protocol serverVersion, not the Gateway build,
    so we read it from the container image's env. Using `docker inspect` rather
    than `docker exec` means it works even when the container is stopped — which
    is exactly when a version skew (exit-4) would have you wanting to see it.
    Best-effort: returns None if docker is unavailable or the var isn't set.
    """
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f",
             "{{range .Config.Env}}{{println .}}{{end}}", "ib_gateway"],
            capture_output=True, text=True, timeout=10,
        )
        for line in (result.stdout or "").splitlines():
            if line.startswith("TWS_MAJOR_VRSN="):
                return line.split("=", 1)[1].strip() or None
    except Exception as e:
        print(f"[api/diag] could not read gateway TWS version: {e}")
    return None


def _soft_restart_ibgateway() -> bool:
    """Ask IBC for an on-demand soft restart via its command server.

    Returns True only if the command server accepted the RESTART (replies
    'OK Restarting at ...'). Returns False if the command server is unreachable
    (e.g. an older gateway image without it enabled), so the caller can fall back
    to paging a human.
    """
    try:
        result = subprocess.run(
            ["docker", "exec", "ib_gateway", "sh", "-c",
             f"printf 'RESTART\\n' | socat -T5 - "
             f"TCP:127.0.0.1:{IBC_COMMAND_PORT},connect-timeout=5"],
            capture_output=True, text=True, timeout=20,
        )
        out = ((result.stdout or "") + (result.stderr or "")).strip()
        accepted = "Restarting" in out
        print(f"[api/watchdog] IBC soft restart → {out!r} (accepted={accepted})")
        return accepted
    except Exception as e:
        print(f"[api/watchdog] IBC soft restart failed: {e}")
        return False


def _full_restart_ibgateway() -> bool:
    """Full restart of the gateway container — re-runs IBC's login from scratch.

    Unlike the soft restart (which reuses the authenticated session token), this
    forces a FRESH login by relaunching the container, which is the only thing that
    clears IBKR's routine "security tokens … have expired … please manually enter
    your username and password" dialog. That dialog leaves the API port refused and
    the gateway LOGGED_OUT; the dead session token can't be reused, so a soft restart
    just relaunches into the same dialog. A fresh login completes automatically on
    PAPER (no 2FA) and waits on an IB Key 2FA approval on LIVE.

    Scope is the gateway container only — never the whole stack — because the watchdog
    runs inside the api container and must stay alive to confirm recovery or escalate.
    Returns True if docker accepted the restart.
    """
    try:
        result = subprocess.run(
            ["docker", "restart", "ib_gateway"],
            capture_output=True, text=True, timeout=90,
        )
        ok = result.returncode == 0
        print(f"[api/watchdog] full gateway restart → rc={result.returncode} "
              f"{(result.stderr or '').strip()!r} (ok={ok})")
        return ok
    except Exception as e:
        print(f"[api/watchdog] full gateway restart failed: {e}")
        return False


def _full_restart_in_cooldown(now: datetime) -> bool:
    """True if a full restart fired too recently (cross-episode lockout guard)."""
    last = _watchdog_state.get("last_full_restart_at")
    return last is not None and (now - last).total_seconds() < FULL_RESTART_COOLDOWN


def _restart_scheduler() -> None:
    # Plain restart re-runs the shared entrypoint, which re-reads the durable
    # /data/gw_trading_mode file and re-derives IBKR_PORT. Mirrors the gateway:
    # both containers take the trading mode from the same persisted file rather
    # than from .env.compose (which upgrades reset to paper/4004).
    subprocess.run(
        ["docker", "restart", "yrvi-scheduler-1"],
        capture_output=True, text=True, timeout=60,
    )


def _restart_api_self() -> None:
    # The api process reads IBKR_PORT/TRADING_MODE once at import (config.py),
    # so a trading-mode switch only takes effect after the api restarts and the
    # shared entrypoint re-derives them from /data/gw_trading_mode. The api can't
    # restart itself synchronously (the docker restart kills this process before
    # the HTTP response is sent), so this runs as a BackgroundTask: a brief sleep
    # lets the response flush to the client, then we ask the daemon to restart us.
    # Once the daemon receives the request it tears down and recreates the
    # container independently, even though this process dies mid-call.
    import time
    time.sleep(1.5)
    subprocess.run(
        ["docker", "restart", "yrvi-api-1"],
        capture_output=True, text=True, timeout=60,
    )

# ── Watchdog helpers ───────────────────────────────────────────

def _alert_severity(message: str) -> str:
    """Derive a severity from the leading emoji every alert already carries.

    Keeps the in-app feed colour/priority in sync with the Discord convention
    without threading a separate severity arg through all ~24 call sites.
    """
    msg = message.lstrip()
    if msg.startswith("✅"):
        return "resolved"
    if msg[:1] in ("🚨", "❌", "🔒"):
        return "critical"
    if msg[:1] in ("🔄", "⚠"):
        return "warning"
    return "info"


def _record_alert(message: str) -> None:
    """Append an alert to the persisted in-app feed (capped ring buffer).

    Thread-safe: called from the watchdog background thread and (via
    _send_discord_alert) from request handlers. Best-effort — a feed write must
    never break the alert path, so all errors are swallowed with a log line.
    """
    try:
        with _alerts_lock:
            alerts: list = []
            if ALERTS_FILE.exists():
                try:
                    alerts = json.loads(ALERTS_FILE.read_text())
                except Exception:
                    alerts = []
            next_id = (alerts[-1].get("id", 0) + 1) if alerts else 1
            alerts.append({
                "id":       next_id,
                "ts":       datetime.now(PST).isoformat(),
                "severity": _alert_severity(message),
                "message":  message,
            })
            if len(alerts) > ALERTS_MAX:
                alerts = alerts[-ALERTS_MAX:]
            # Atomic write so a concurrent reader never sees a half-written file.
            tmp = ALERTS_FILE.with_name(ALERTS_FILE.name + ".tmp")
            tmp.write_text(json.dumps(alerts))
            tmp.replace(ALERTS_FILE)
    except Exception as e:
        print(f"[api/alerts] failed to record alert: {e}")


def _send_discord_alert(message: str) -> None:
    """Post an alert to the in-app feed AND the main Discord webhook.

    The in-app record happens first and unconditionally, so the dashboard feed
    works even when Discord isn't configured or is unreachable. The Discord post
    no-ops if no webhook is set.
    """
    _record_alert(message)
    try:
        webhook_url = _read_secret_or_env("discord_webhook_url", "DISCORD_WEBHOOK_URL")
        if not webhook_url:
            return
        version_file = BASE_DIR / "VERSION"
        version = version_file.read_text().strip() if version_file.exists() else "unknown"
        full_message = f"{message}\n-# v{version}"
        import requests as req
        req.post(webhook_url, json={"content": full_message}, timeout=5)
    except Exception as e:
        print(f"[api/watchdog] Discord alert failed: {e}")


def _watchdog_check() -> None:
    """Check gateway and scheduler health; send Discord alerts on persistent failures."""
    now = datetime.now(PST)
    settings = load_settings()
    port = settings.get("ibkr_port", 4004)

    # ── IB Gateway port reachability ─────────────────────────────
    gw_up = _gateway_running(port)
    if not gw_up:
        if _watchdog_state["gateway_down_since"] is None:
            _watchdog_state["gateway_down_since"] = now
        down_sec  = (now - _watchdog_state["gateway_down_since"]).total_seconds()
        host      = os.environ.get("IBKR_HOST", "ib_gateway")
        is_live   = port in _LIVE_IBKR_PORTS
        mins      = int(down_sec / 60)
        full_at   = _watchdog_state["gateway_full_restart_at"]

        # A refused API port with the gateway LOGGED_OUT is the signature of IBKR's
        # routine token-expiry dialog ("security tokens … have expired … manually
        # enter your username and password"). The soft restart can't clear it (the
        # session token is dead); only a FULL restart re-runs login. So self-heal
        # with one full restart, then page if it doesn't take. But some port-down
        # causes must NEVER auto-restart:
        #   • exit 4   → image/version mismatch, needs a Reset Installation
        #   • locked   → account locked; restarting risks a deeper lockout
        #   • failed   → wrong password; restarting just loops failed logins → lockout
        #   • in the auto-restart window → gateway is already cycling on its own
        docker_st = _get_docker_container_state()
        c_exit    = docker_st["exit_code"]
        login_st  = _gateway_login_status
        no_restart = (c_exit == 4 or login_st in ("locked", "failed")
                      or _in_auto_restart_window(now))

        if (down_sec >= ALERT_THRESHOLD
                and _watchdog_state["last_gateway_alert"] is None
                and full_at is None):
            if not no_restart:
                # Restartable cause: fire ONE full restart this episode, gated by the
                # cross-episode cooldown (lockout guard). Don't set last_gateway_alert
                # yet — leave the escalation path open in case it doesn't recover.
                if _full_restart_in_cooldown(now):
                    _watchdog_state["last_gateway_alert"] = now
                    _send_discord_alert(
                        f"🚨 **YRVI** IB Gateway API port unreachable for {mins} min "
                        f"(`{host}:{port}`) and not logged in. Auto full-restart "
                        f"suppressed — one fired < {FULL_RESTART_COOLDOWN // 60} min ago "
                        f"(lockout guard).\n{_MANUAL_RESTART_HINT}"
                    )
                elif _full_restart_ibgateway():
                    _watchdog_state["gateway_full_restart_at"] = now
                    _watchdog_state["last_full_restart_at"]    = now
                    if is_live:
                        _send_discord_alert(
                            f"🔄 **YRVI** LIVE IB Gateway unreachable for {mins} min "
                            f"(`{host}:{port}`) — likely IBKR's routine token-expiry "
                            f"dialog. Auto-recovery: sent a full gateway restart to "
                            f"re-run login. ⚠️ **LIVE re-login needs IB Key 2FA — check "
                            f"your phone and approve.** Confirming recovery or escalating "
                            f"in ~{FULL_RESTART_GRACE // 60} min…"
                        )
                    else:
                        _send_discord_alert(
                            f"🔄 **YRVI** IB Gateway unreachable for {mins} min "
                            f"(`{host}:{port}`) — likely IBKR's routine token-expiry "
                            f"dialog. Auto-recovery: sent a full gateway restart to "
                            f"re-run login (paper logs in automatically — no 2FA). "
                            f"Confirming recovery or escalating in ~{FULL_RESTART_GRACE // 60} min…"
                        )
                else:
                    _watchdog_state["last_gateway_alert"] = now
                    _send_discord_alert(
                        f"🚨 **YRVI** IB Gateway API port unreachable for {mins} min "
                        f"(`{host}:{port}`) and not logged in. Auto full-restart could "
                        f"not be sent (docker error).\n{_MANUAL_RESTART_HINT}"
                    )
            else:
                # Non-restartable cause → targeted page, no auto-restart.
                _watchdog_state["last_gateway_alert"] = now
                if c_exit == 4:
                    _send_discord_alert(
                        f"🚨 **YRVI** IB Gateway version mismatch — installed Gateway version not found "
                        f"(exit code 4). The Docker image updated to a newer Gateway version.\n"
                        f"🔧 **Fix:** Open the dashboard → Help → Run Diagnostics → click **Reset Installation**. "
                        f"No CLI needed."
                    )
                elif login_st == "locked":
                    _send_discord_alert(
                        f"🔒 **YRVI** IB Gateway account locked out — too many failed login attempts. "
                        f"Reset your IBKR password in Client Portal, then restart the gateway.\n"
                        f"🔴 `docker compose --env-file .env.compose restart ib_gateway`"
                    )
                elif login_st == "failed":
                    _send_discord_alert(
                        f"❌ **YRVI** IB Gateway login failed — wrong IBKR username or password. "
                        f"Update credentials in the dashboard Settings page, then restart the gateway.\n"
                        f"🔴 `docker compose --env-file .env.compose restart ib_gateway`"
                    )
                else:  # auto-restart window
                    _send_discord_alert(
                        f"🚨 **YRVI** IB Gateway API port unreachable for {mins} min "
                        f"(`{host}:{port}`). This is likely the scheduled daily restart — "
                        f"a ✅ recovery message will follow once it's back up. "
                        f"If it doesn't recover, VNC is available on host port 5900.\n"
                        f"🔴 Manual restart: "
                        f"`docker compose --env-file .env.compose restart ib_gateway`"
                    )
        elif (full_at is not None
                and _watchdog_state["last_gateway_alert"] is None
                and (now - full_at).total_seconds() >= FULL_RESTART_GRACE):
            # Full restart fired but the gateway is still down past the grace window.
            _watchdog_state["last_gateway_alert"] = now
            twofa = (" — did you approve the IB Key 2FA push on your phone?"
                     if is_live else "")
            _send_discord_alert(
                f"🚨 **YRVI** IB Gateway still unreachable {mins} min in — the auto "
                f"full-restart did not recover it{twofa}. Manual intervention needed.\n"
                f"{_MANUAL_RESTART_HINT}"
            )
    else:
        if (_watchdog_state["gateway_down_since"] is not None
                and (_watchdog_state["last_gateway_alert"] is not None
                     or _watchdog_state["gateway_full_restart_at"] is not None)):
            down_sec = (now - _watchdog_state["gateway_down_since"]).total_seconds()
            # Healed by the auto full-restart = we fired one and never had to page.
            healed = (_watchdog_state["gateway_full_restart_at"] is not None
                      and _watchdog_state["last_gateway_alert"] is None)
            _send_discord_alert(
                f"✅ **YRVI** IB Gateway port is reachable again "
                f"(was down {int(down_sec / 60)} min"
                f"{' — auto full-restart recovered it' if healed else ''})."
            )
        _watchdog_state["gateway_down_since"]      = None
        _watchdog_state["last_gateway_alert"]      = None
        _watchdog_state["gateway_full_restart_at"] = None

    # ── IBKR API connection (only when gateway port is up) ────────
    # Port open but ib_insync failing → gateway logged in but API broken (Scenario 3)
    # or stuck on an unexpected dialog that kept the port open (Scenario 2 edge case).
    if gw_up:
        ibkr = _get_ibkr_data(settings)
        if not ibkr["connected"]:
            if _watchdog_state["ibkr_down_since"] is None:
                _watchdog_state["ibkr_down_since"] = now
            down_sec = (now - _watchdog_state["ibkr_down_since"]).total_seconds()
            err = ibkr.get("error") or "unknown error"
            soft_at = _watchdog_state["ibkr_soft_restart_at"]

            # "Port open but handshake dead" is the wedge signature — a hung API
            # listener that an IBC soft restart clears by relaunching the gateway
            # process (no 2FA, no container bounce). So self-heal FIRST and only
            # page a human if that fails. State machine, all gated on a persistent
            # outage (>= ALERT_THRESHOLD) and firing each step once per outage:
            #   1. in the auto-restart window → don't self-heal (gateway is already
            #      cycling); just inform, matching the old behavior.
            #   2. first response → send a soft restart. If the command server
            #      accepts it, note the time and wait. If it doesn't (older image
            #      without the command server), page a human immediately.
            #   3. soft restart didn't recover within the grace window → escalate.
            if down_sec >= ALERT_THRESHOLD and _watchdog_state["last_ibkr_alert"] is None:
                if _in_auto_restart_window(now):
                    _watchdog_state["last_ibkr_alert"] = now
                    _send_discord_alert(
                        f"🚨 **YRVI** IB Gateway port is open but IBKR API connection failed "
                        f"for {int(down_sec / 60)} min (`{err}`). "
                        f"This is likely the scheduled daily restart — "
                        f"a ✅ recovery message will follow once it's back up. "
                        f"If it doesn't recover, VNC is available on host port 5900.\n"
                        f"🔴 Manual restart: "
                        f"`docker compose --env-file .env.compose restart ib_gateway`"
                    )
                elif soft_at is None:
                    if _soft_restart_ibgateway():
                        _watchdog_state["ibkr_soft_restart_at"] = now
                        _send_discord_alert(
                            f"🔄 **YRVI** IBKR API unreachable for {int(down_sec / 60)} min "
                            f"(`{err}`). Auto-recovery: sent an IB Gateway soft restart "
                            f"(reuses the session — no login, no 2FA). Confirming recovery "
                            f"or escalating in ~{SOFT_RESTART_GRACE // 60} min…"
                        )
                    else:
                        # The IBC command server is unreachable, so we can't soft-restart.
                        # Don't page-and-give-up: escalate straight to a full `docker restart`
                        # (the api container does this via docker.sock), gated by the
                        # cross-episode cooldown lockout guard. This is exactly the wedge
                        # the soft path was meant to clear; the full restart re-runs login.
                        is_live = port in _LIVE_IBKR_PORTS
                        if _full_restart_in_cooldown(now):
                            _watchdog_state["last_ibkr_alert"] = now
                            _send_discord_alert(
                                f"🚨 **YRVI** IB Gateway API failing {int(down_sec / 60)} min in — "
                                f"soft restart unavailable (command server unreachable) and a "
                                f"full-restart escalation is suppressed (one fired < "
                                f"{FULL_RESTART_COOLDOWN // 60} min ago — lockout guard).\n"
                                f"{_MANUAL_RESTART_HINT}"
                            )
                        elif _full_restart_ibgateway():
                            # Mark BOTH soft and full as fired so the state machine's
                            # next pass advances to the full-restart grace-wait branch
                            # (it dispatches on soft_at) instead of re-entering the soft
                            # branch and paging once the cooldown kicks in.
                            _watchdog_state["ibkr_soft_restart_at"] = now
                            _watchdog_state["ibkr_full_restart_at"] = now
                            _watchdog_state["last_full_restart_at"] = now
                            twofa = ("; ⚠️ **LIVE re-login needs IB Key 2FA — check your phone**"
                                     if is_live else " (paper — no 2FA)")
                            _send_discord_alert(
                                f"🔄 **YRVI** IBKR API unreachable for {int(down_sec / 60)} min "
                                f"(`{err}`); the soft restart was unavailable (command server "
                                f"unreachable), so escalated straight to a full gateway restart "
                                f"that re-runs login{twofa}. Confirming recovery or escalating in "
                                f"~{FULL_RESTART_GRACE // 60} min…"
                            )
                        else:
                            _watchdog_state["last_ibkr_alert"] = now
                            _send_discord_alert(
                                f"🚨 **YRVI** IB Gateway port is open but IBKR API connection failed "
                                f"for {int(down_sec / 60)} min. Error: `{err}`. Auto soft-restart "
                                f"unavailable (command server not reachable) and the full-restart "
                                f"escalation could not be sent (docker error).\n"
                                f"{_MANUAL_RESTART_HINT}"
                            )
                elif (_watchdog_state["ibkr_full_restart_at"] is None
                        and (now - soft_at).total_seconds() >= SOFT_RESTART_GRACE):
                    # Soft restart (session reuse) didn't clear it within grace →
                    # escalate to ONE full restart (re-runs login; live = 2FA),
                    # gated by the cross-episode cooldown (lockout guard).
                    is_live = port in _LIVE_IBKR_PORTS
                    if _full_restart_in_cooldown(now):
                        _watchdog_state["last_ibkr_alert"] = now
                        _send_discord_alert(
                            f"🚨 **YRVI** IB Gateway API still failing {int(down_sec / 60)} min in — "
                            f"the soft restart did not recover it (`{err}`) and a full-restart "
                            f"escalation is suppressed (one fired < {FULL_RESTART_COOLDOWN // 60} min "
                            f"ago — lockout guard).\n{_MANUAL_RESTART_HINT}"
                        )
                    elif _full_restart_ibgateway():
                        _watchdog_state["ibkr_full_restart_at"] = now
                        _watchdog_state["last_full_restart_at"] = now
                        twofa = ("; ⚠️ **LIVE re-login needs IB Key 2FA — check your phone**"
                                 if is_live else " (paper — no 2FA)")
                        _send_discord_alert(
                            f"🔄 **YRVI** IBKR API still failing {int(down_sec / 60)} min in — the "
                            f"soft restart didn't clear it, so escalated to a full gateway restart "
                            f"that re-runs login{twofa}. Confirming recovery or escalating in "
                            f"~{FULL_RESTART_GRACE // 60} min…"
                        )
                    else:
                        _watchdog_state["last_ibkr_alert"] = now
                        _send_discord_alert(
                            f"🚨 **YRVI** IB Gateway API still failing {int(down_sec / 60)} min in — "
                            f"the soft restart did not recover it (`{err}`) and the full-restart "
                            f"escalation could not be sent (docker error).\n{_MANUAL_RESTART_HINT}"
                        )
                elif (_watchdog_state["ibkr_full_restart_at"] is not None
                        and (now - _watchdog_state["ibkr_full_restart_at"]).total_seconds()
                            >= FULL_RESTART_GRACE):
                    _watchdog_state["last_ibkr_alert"] = now
                    twofa = (" — did you approve the IB Key 2FA push on your phone?"
                             if port in _LIVE_IBKR_PORTS else "")
                    _send_discord_alert(
                        f"🚨 **YRVI** IB Gateway API still failing {int(down_sec / 60)} min in — "
                        f"neither the soft nor the full auto-restart recovered it (`{err}`){twofa}. "
                        f"Manual intervention needed.\n{_MANUAL_RESTART_HINT}"
                    )
        else:
            if (_watchdog_state["ibkr_down_since"] is not None
                    and (_watchdog_state["last_ibkr_alert"] is not None
                         or _watchdog_state["ibkr_soft_restart_at"] is not None
                         or _watchdog_state["ibkr_full_restart_at"] is not None)):
                down_sec = (now - _watchdog_state["ibkr_down_since"]).total_seconds()
                # Healed by an auto-restart = we fired one (soft or full) and never paged.
                healed = ((_watchdog_state["ibkr_soft_restart_at"] is not None
                           or _watchdog_state["ibkr_full_restart_at"] is not None)
                          and _watchdog_state["last_ibkr_alert"] is None)
                which = ("full" if _watchdog_state["ibkr_full_restart_at"] is not None
                         else "soft")
                _send_discord_alert(
                    f"✅ **YRVI** IBKR API connection restored "
                    f"(was failing for {int(down_sec / 60)} min"
                    f"{f' — auto {which}-restart recovered it' if healed else ''})."
                )
            _watchdog_state["ibkr_down_since"] = None
            _watchdog_state["last_ibkr_alert"] = None
            _watchdog_state["ibkr_soft_restart_at"] = None
            _watchdog_state["ibkr_full_restart_at"] = None
    else:
        # Gateway port is down — clear IBKR state; its episode timer resets when port returns
        _watchdog_state["ibkr_down_since"] = None
        _watchdog_state["last_ibkr_alert"] = None
        _watchdog_state["ibkr_soft_restart_at"] = None
        _watchdog_state["ibkr_full_restart_at"] = None

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


_LOG_RELEVANT_KEYWORDS = (
    "login", "failed", "failure", "error", "exception", "warn",
    "locked", "password", "connect", "disconnect", "starting", "started",
    "ready", "authenticated", "authentication", "2fa", "challenge",
    "exit", "crash", "timeout", "refused", "unrecognized", "autorestart",
)


def _write_gateway_status(status: str, event: str, lines: list) -> None:
    """Persist gateway login status + recent log lines to disk (survives API restarts)."""
    try:
        GATEWAY_STATUS_FILE.write_text(json.dumps({
            "status":       status,
            "last_event":   event,
            "updated":      datetime.now(PST).isoformat(),
            "recent_lines": lines[-8:],
        }))
    except Exception as e:
        print(f"[api/gateway-status] could not write status file: {e}")


def _read_gateway_status() -> dict:
    """Read the persisted gateway status file; returns empty dict on any error."""
    try:
        return json.loads(GATEWAY_STATUS_FILE.read_text())
    except Exception:
        return {}


# ── Weekly IB Key 2FA token tracking ───────────────────────────

def _last_weekly_token_reset(now: datetime) -> datetime:
    """Most recent Sunday 01:00 ET — when IBKR invalidates the weekly IB Key token."""
    et_now = now.astimezone(ET)
    days_since_sun = (et_now.weekday() - 6) % 7   # Mon=0 … Sun=6
    boundary = et_now.replace(hour=1, minute=0, second=0, microsecond=0) \
        - timedelta(days=days_since_sun)
    if boundary > et_now:          # early Sunday, before 1 AM → use last week's
        boundary -= timedelta(days=7)
    return boundary


def _next_weekly_token_reset(now: datetime) -> datetime:
    """Upcoming Sunday 01:00 ET — the next weekly token invalidation."""
    return _last_weekly_token_reset(now) + timedelta(days=7)


def _read_weekly_token() -> Optional[str]:
    """ISO timestamp of when the weekly token was established, or None."""
    try:
        ts = WEEKLY_TOKEN_FILE.read_text().strip()
        return ts or None
    except Exception:
        return None


def _set_weekly_token() -> None:
    """Record the token as established. Preserves a current-week timestamp
    across the week's daily auto-restarts, but overwrites a stale (pre-reset)
    one — otherwise a missed Sunday "autorestart file not found" line would
    freeze the displayed date at last week's value."""
    existing = _read_weekly_token()
    if existing:
        try:
            if (datetime.fromisoformat(existing).astimezone(ET)
                    >= _last_weekly_token_reset(datetime.now(PST))):
                return                     # already current this week — keep original time
        except Exception:
            pass                           # unparseable → fall through and rewrite
    try:
        WEEKLY_TOKEN_FILE.write_text(datetime.now(PST).isoformat())
        print("[api/weekly-token] token established — timestamp recorded")
    except Exception as e:
        print(f"[api/weekly-token] could not write token file: {e}")


def _clear_weekly_token() -> None:
    """Clear the established timestamp — 2FA is required again."""
    try:
        if WEEKLY_TOKEN_FILE.exists():
            WEEKLY_TOKEN_FILE.unlink()
            print("[api/weekly-token] token cleared — 2FA required")
    except Exception as e:
        print(f"[api/weekly-token] could not clear token file: {e}")


def _weekly_token_status() -> dict:
    """Computed weekly-token state for /api/status and the dashboard."""
    now         = datetime.now(PST)
    established  = _read_weekly_token()
    last_reset   = _last_weekly_token_reset(now)
    active = False
    if established:
        try:
            active = datetime.fromisoformat(established).astimezone(ET) >= last_reset
        except Exception:
            active = False
    return {
        # Only surface the timestamp while it's still valid for the current week.
        "weekly_token_established":     established if active else None,
        "weekly_token_active":          active,
        "weekly_token_next_reset":      _next_weekly_token_reset(now).isoformat(),
        # Enabled whenever this week's token isn't active yet (the last Sunday 1 AM
        # boundary is always in the past, so no separate time gate is needed).
        "weekly_token_refresh_enabled": not active,
    }


def _get_docker_container_state() -> dict:
    """
    Return Docker container info for ib_gateway.
    Keys: state (running|exited|restarting|not_found|unknown), exit_code
    Only meaningful when CONTAINERIZED=True.
    """
    if not CONTAINERIZED:
        return {"state": "unknown", "exit_code": None}
    try:
        import docker as docker_sdk
        client = docker_sdk.from_env()
        container = client.containers.get("ib_gateway")
        container.reload()
        s = container.attrs.get("State", {})
        return {
            "state":     s.get("Status", "unknown"),  # running/exited/restarting/paused/dead
            "exit_code": s.get("ExitCode"),
        }
    except Exception as e:
        err = str(e).lower()
        if "404" in err or "no such container" in err or "not found" in err:
            return {"state": "not_found", "exit_code": None}
        return {"state": "unknown", "exit_code": None}


def _fetch_recent_gateway_logs(tail: int = 15) -> list:
    """
    Pull the last `tail` lines directly from the ib_gateway container.
    Returns [] on any error (container stopped, Docker unavailable, etc.).
    """
    if not CONTAINERIZED:
        return []
    try:
        import docker as docker_sdk
        client    = docker_sdk.from_env()
        container = client.containers.get("ib_gateway")
        raw       = container.logs(tail=tail).decode("utf-8", errors="replace")
        return [l for l in raw.splitlines() if l.strip()][-tail:]
    except Exception:
        return []


def _get_gateway_detail(port: int) -> dict:
    """
    Aggregate all available gateway diagnostic info.
    Reads persisted file first, falls back to in-memory globals.
    """
    persisted  = _read_gateway_status()
    login_st   = persisted.get("status") or _gateway_login_status
    last_event = persisted.get("last_event") or _gateway_last_event
    lines      = persisted.get("recent_lines") or list(_gateway_recent_lines)
    docker_st  = _get_docker_container_state()
    return {
        "login_status":    login_st,
        "last_event":      last_event,
        "recent_lines":    lines,
        "container_state": docker_st["state"],
        "exit_code":       docker_st["exit_code"],
    }


def _run_gateway_log_monitor() -> None:
    """Tail ib_gateway logs via Docker SDK and alert on login failures or lockouts."""
    global _gateway_login_status, _gateway_last_event, _gateway_recent_lines
    import docker as docker_sdk
    time.sleep(60)  # let the gateway container finish starting

    while True:
        _gateway_login_status = "unknown"
        terminal = False
        container = None
        started_at = ""
        _recent: list = []   # relevant lines collected this session

        def _add_line(line: str) -> None:
            """Buffer a relevant log line (keep last 20)."""
            ll = line.lower()
            if any(kw in ll for kw in _LOG_RELEVANT_KEYWORDS):
                _recent.append(line[:200])   # cap line length
                if len(_recent) > 20:
                    _recent.pop(0)
                _gateway_recent_lines[:] = _recent

        def _set_status(status: str, event: str) -> None:
            global _gateway_login_status, _gateway_last_event
            _gateway_login_status = status
            _gateway_last_event   = event
            _write_gateway_status(status, event, _recent)

        try:
            client = docker_sdk.from_env()
            container = client.containers.get("ib_gateway")
            container.reload()
            started_at = container.attrs["State"]["StartedAt"]

            login_attempts = 0

            for chunk in container.logs(stream=True, follow=True, tail=100):
                line = chunk.decode("utf-8").strip()
                ll = line.lower()
                _add_line(line)

                # Weekly IB Key 2FA token state (IBC logs one of these on every restart).
                if "autorestart file not found" in ll:
                    _clear_weekly_token()          # token reset → 2FA required this boot
                elif "autorestart file found" in ll and "will not be required" in ll:
                    _set_weekly_token()            # token active → no 2FA needed

                if "locked out" in ll:
                    _set_status("locked", line)
                    _send_discord_alert(
                        "🔒 IBKR account locked out — too many failed login attempts. "
                        "Stop the gateway and reset your password."
                    )
                    terminal = True
                    break

                if ("login failed" in ll or "authentication failed" in ll
                        or "unrecognized username or password" in ll):
                    _set_status("failed", line)
                    _send_discord_alert(
                        "❌ IB Gateway login failed — check your IBKR credentials."
                    )
                    terminal = True
                    break

                if "login attempt" in ll:
                    login_attempts += 1
                    if login_attempts > 3:
                        _set_status("failed", line)
                        _send_discord_alert(
                            "⚠️ IB Gateway repeated login failures — possible wrong password."
                        )
                        terminal = True
                        break

                if "login has completed" in ll or "logged in" in ll:
                    login_attempts = 0
                    _set_status("ok", line)
                    # A successful login confirms the token is established. After a
                    # 2FA approval the "autorestart file found" line only appears on
                    # the next restart, so this is the timely establishment signal.
                    # No-op if already recorded earlier this week.
                    _set_weekly_token()

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


@app.post("/api/gateway/reset-installation")
def reset_gateway_installation():
    """
    Wipe the ib_gateway_settings volume and restart the container so IBC
    reinstalls the correct Gateway version from scratch.  Only needed when
    the Docker image updates to a new Gateway version that isn't in the volume
    (IBC exits with code 4: "Offline TWS/Gateway version X is not installed").
    Credentials (ibc_config.ini / .env) are host-mounted and are NOT affected.
    """
    if not CONTAINERIZED:
        raise HTTPException(status_code=400, detail="Reset is only available in containerized mode")
    try:
        import docker as docker_sdk
        client = docker_sdk.from_env()

        # ── 1. Locate the settings volume from the container's mount list ──
        try:
            container = client.containers.get("ib_gateway")
        except Exception:
            raise HTTPException(status_code=404, detail="ib_gateway container not found")

        settings_volume = None
        for mount in container.attrs.get("Mounts", []):
            if (mount.get("Type") == "volume"
                    and mount.get("Destination") == "/home/ibgateway/Jts"):
                settings_volume = mount.get("Name")
                break

        if not settings_volume:
            raise HTTPException(status_code=500,
                                detail="Could not locate ib_gateway_settings volume in container mounts")

        # ── 2. Stop the container ──────────────────────────────────────────
        try:
            container.stop(timeout=15)
        except Exception:
            pass   # already stopped — that's fine

        # ── 3. Remove the stopped container to release its volume reference ─
        # Docker won't remove a volume while any container (even stopped) still
        # references it, so we must remove the container first.
        try:
            container.remove(force=True)
        except Exception as e:
            raise HTTPException(status_code=500,
                                detail=f"Could not remove container: {e}")

        # ── 4. Remove the stale volume ────────────────────────────────────
        try:
            vol = client.volumes.get(settings_volume)
            vol.remove()
        except Exception as e:
            raise HTTPException(status_code=500,
                                detail=f"Could not remove volume {settings_volume}: {e}")

        # ── 5. Recreate container + fresh volume via docker compose ───────
        # container.start() won't work after remove(); docker compose up
        # recreates the container with the correct config and mounts, and
        # Docker auto-creates the named volume fresh on first mount.
        result = subprocess.run(
            ["docker", "compose", "--env-file", "/host_repo/.env.compose",
             "up", "-d", "ib_gateway"],
            cwd="/host_repo", capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            raise HTTPException(status_code=500,
                                detail=f"docker compose up failed: {result.stderr[:300]}")

        print(f"[api/reset-gateway] wiped {settings_volume} and recreated ib_gateway")
        return {"success": True,
                "message": "Gateway installation reset — IBC is reinstalling (~2 min). "
                           "Run diagnostics again once the gateway comes back up."}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/gateway/refresh-token")
def refresh_weekly_token():
    """
    Restart ib_gateway to trigger the weekly IB Key 2FA push notification.
    Used on Sunday (after the 1 AM ET token invalidation) so the user can get
    their phone prompt at a convenient time instead of waiting for the nightly
    auto-restart. The log monitor records the new token once login completes.
    """
    if not CONTAINERIZED:
        raise HTTPException(status_code=400, detail="Only available in containerized mode")
    try:
        _restart_ibgateway()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gateway restart failed: {e}")
    return {"success": True,
            "message": "Gateway restarting — check your phone for the IB Key approval."}


@app.post("/api/gateway/restart")
def restart_gateway():
    """Operator-triggered full restart of the ib_gateway container — the one-click
    unwedge for the dashboard.

    This does the same `docker restart ib_gateway` the Discord pages used to ask the
    operator to run in a terminal, so a non-technical operator on any OS can recover
    a wedged gateway (port open but API handshake dead, or port refused) without a
    shell. A full restart re-runs IBC login: automatic on PAPER, an IB Key 2FA push
    on LIVE (the operator must approve it on their phone).

    A human deliberately clicking this is intentional, so it is NOT gated by the
    watchdog's auto-restart cooldown — but we record it as the last full restart so
    the watchdog's lockout guard won't immediately fire another on top of it.
    """
    if not CONTAINERIZED:
        raise HTTPException(status_code=400, detail="Only available in containerized mode")
    settings = load_settings()
    is_live = settings.get("ibkr_port", 4004) in _LIVE_IBKR_PORTS
    if not _full_restart_ibgateway():
        raise HTTPException(
            status_code=500,
            detail="docker restart ib_gateway failed — check the api container logs",
        )
    # Record the restart and clear in-flight outage flags so the watchdog evaluates
    # the freshly-restarted gateway cleanly on its next cycle instead of double-firing.
    now = datetime.now(PST)
    _watchdog_state["last_full_restart_at"] = now
    _watchdog_state["last_gateway_alert"]   = None
    _watchdog_state["last_ibkr_alert"]      = None
    print(f"[api/gateway-restart] operator-triggered full restart (live={is_live})")
    msg = ("Gateway restarting — re-running login. "
           + ("⚠️ LIVE: approve the IB Key 2FA push on your phone now. "
              if is_live else "Paper logs in automatically (no 2FA). ")
           + "Give it ~1–2 min, then re-run diagnostics to confirm it's back.")
    return {"success": True, "is_live": is_live, "message": msg}


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

class ReconcileUploadRequest(BaseModel):
    xml: str
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    dry_run: bool = True

class ReconcileFlexRequest(BaseModel):
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    dry_run: bool = True

class YtdWeekRequest(BaseModel):
    week_start: str        # YYYY-MM-DD
    premium_collected: float

class ReconcileCommitRequest(BaseModel):
    weeks: list  # list of week dicts from a previous preview

class FeedbackRequest(BaseModel):
    type: str    # "bug" | "feature"
    message: str


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
    port        = settings.get("ibkr_port", 4004)
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

            # Start the account-level P&L stream early so it has time to populate
            # while accountSummary round-trips below. The IB Gateway's
            # accountSummary does NOT expose UnrealizedPnL/RealizedPnL tags, so
            # reqPnL is the reliable source for the dashboard summary cards.
            acct_pnl = None
            try:
                acct_pnl = ib.reqPnL(acct, "")
            except Exception as pe:
                print(f"[api] reqPnL failed: {pe}")

            # ── Account summary
            summary_dict = {item.tag: item.value for item in ib.accountSummary(acct)}
            print(f"[api] accountSummary tags: {list(summary_dict.keys())}")
            result["account_value"]      = _safe_float(summary_dict.get("NetLiquidation", 0))
            result["buying_power"]       = _safe_float(summary_dict.get("BuyingPower",    0))
            result["settled_cash"]       = _safe_float(summary_dict.get("TotalCashValue", 0))
            # Account-level P&L from reqPnL (accountSummary lacks these tags);
            # fall back to summary tags if the stream hasn't populated. Wait
            # briefly for the first update so a cold call isn't cached at 0.
            if acct_pnl is not None:
                for _ in range(10):
                    if _safe_float(acct_pnl.unrealizedPnL) is not None:
                        break
                    ib.sleep(0.3)
            pnl_unrl = _safe_float(acct_pnl.unrealizedPnL) if acct_pnl else None
            pnl_real = _safe_float(acct_pnl.realizedPnL)   if acct_pnl else None
            result["unrealized_pnl"]     = (pnl_unrl if pnl_unrl is not None
                                            else _safe_float(summary_dict.get("UnrealizedPnL", 0)))
            result["realized_pnl"]       = (pnl_real if pnl_real is not None
                                            else _safe_float(summary_dict.get("RealizedPnL", 0)))
            try:
                ib.cancelPnL(acct, "")
            except Exception:
                pass
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

                # ── Per-position market value + unrealized P&L via reqPnLSingle.
                # ib.portfolio() only populates after a reqAccountUpdates stream,
                # which we never start; reqPnLSingle gives IBKR-computed value and
                # unrealizedPnL per conId (correct cost basis/sign), and needs the
                # account's market-data entitlement (OPRA for options).
                acct_positions = [
                    pos for pos in raw_positions
                    if not (account_env and pos.account != account_env)
                ]
                pnl_lookup: dict = {}
                pnl_reqs: list = []
                for pos in acct_positions:
                    try:
                        pnl_reqs.append((pos.contract.conId,
                                         ib.reqPnLSingle(acct, "", pos.contract.conId)))
                    except Exception as se:
                        print(f"[api] reqPnLSingle failed for {pos.contract.symbol}: {se}")
                ib.sleep(3)  # let PnLSingle streams populate
                for con_id, single in pnl_reqs:
                    pnl_lookup[con_id] = single
                    try:
                        ib.cancelPnLSingle(acct, "", con_id)
                    except Exception:
                        pass

                portfolio = []
                for pos in acct_positions:
                    c        = pos.contract
                    is_opt   = c.secType == "OPT"
                    single   = pnl_lookup.get(c.conId)
                    mult     = _safe_float(c.multiplier, 0) or (100 if is_opt else 1)
                    mkt_val  = _safe_float(single.value)         if single else None
                    unrl     = _safe_float(single.unrealizedPnL) if single else None
                    # Derive per-share price from total value: value / (position * multiplier)
                    denom    = (pos.position or 0) * mult
                    mkt_px   = round(mkt_val / denom, 4) if (mkt_val is not None and denom) else None
                    # IBKR avgCost for options is the per-contract dollar cost (premium ×
                    # multiplier); divide by the multiplier so Avg Price is per-share and
                    # directly comparable to marketPrice (Price). Stocks are unaffected.
                    avg_cost = _safe_float(pos.avgCost, 4)
                    if is_opt and avg_cost is not None and mult:
                        avg_cost = round(avg_cost / mult, 4)
                    portfolio.append({
                        "symbol":        c.symbol,
                        "secType":       c.secType,
                        "right":         c.right if is_opt else None,
                        "strike":        _safe_float(c.strike, 4) if is_opt else None,
                        "expiry":        c.lastTradeDateOrContractMonth if is_opt else None,
                        "position":      _safe_float(pos.position, 0),
                        "avgCost":       avg_cost,
                        "marketPrice":   mkt_px,
                        "marketValue":   mkt_val,
                        "unrealizedPNL": unrl,
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

    def _exec_day_for_monday(monday_dt: datetime) -> datetime:
        """Return the execution datetime for the week whose Monday is monday_dt."""
        t = monday_dt.replace(hour=exec_h, minute=exec_m, second=0, microsecond=0)
        if is_market_holiday(t.date()):
            t = t + timedelta(days=1)   # shift to Tuesday
        return t

    # This week's Monday (go back to Monday regardless of current weekday)
    this_monday = now - timedelta(days=now.weekday())
    target = _exec_day_for_monday(this_monday)

    # If we've already passed this week's execution, advance to next week
    if now >= target:
        next_monday = this_monday + timedelta(days=7)
        target = _exec_day_for_monday(next_monday)

    return target.isoformat()


def _build_diag() -> dict:
    """Fast system health check — file reads + TCP probe only, no IBKR API calls."""
    checks = []
    overall = "ok"

    def check(name, status, detail, log_snippet=None, reset_available=False):
        nonlocal overall
        entry = {"name": name, "status": status, "detail": detail}
        if log_snippet is not None:
            entry["log_snippet"] = log_snippet
        if reset_available:
            entry["reset_available"] = True
        checks.append(entry)
        if status == "error" and overall != "error":
            overall = "error"
        elif status == "warn" and overall == "ok":
            overall = "warn"

    settings = load_settings()
    port = settings.get("ibkr_port", 4004)
    now = datetime.now(PST)

    # ── 1. Scheduler heartbeat ─────────────────────────────────
    try:
        hb = json.loads(HEARTBEAT_FILE.read_text())
        ts = datetime.fromisoformat(hb["timestamp"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=PST)
        age_sec = (now - ts).total_seconds()
        if age_sec < 180:
            check("Scheduler", "ok", f"Running — heartbeat {int(age_sec)}s ago")
        elif age_sec < 600:
            check("Scheduler", "warn", f"Heartbeat stale — {int(age_sec / 60)}m ago (may be restarting)")
        else:
            check("Scheduler", "error", f"Heartbeat stale — {int(age_sec / 60)}m ago (scheduler may be down)")
    except FileNotFoundError:
        check("Scheduler", "error", "No heartbeat file found — scheduler has never run")
    except Exception as e:
        check("Scheduler", "error", f"Could not read heartbeat: {e}")

    # ── 2. IB Gateway — port probe + container state + login status ───
    gw_up    = _gateway_running(port)
    gw_info  = _get_gateway_detail(port)
    login_st = gw_info["login_status"]
    c_state  = gw_info["container_state"]
    c_exit   = gw_info["exit_code"]
    # Always fetch live logs so the snippet is available regardless of gateway state.
    # Fall back to cached lines from gateway_status.json if the container isn't running.
    live_logs = _fetch_recent_gateway_logs(15)
    snippet   = live_logs if live_logs else (gw_info["recent_lines"] or [])

    if gw_up:
        if login_st == "locked":
            check("IB Gateway", "error",
                  f"Port {port} open but account locked out — reset IBKR password, then restart gateway",
                  snippet)
        elif login_st == "failed":
            check("IB Gateway", "error",
                  f"Port {port} open but login failed — check IBKR credentials in Settings",
                  snippet)
        else:
            mode = "live" if port in (4001, 4003) else "paper"
            check("IB Gateway", "ok", f"Reachable on port {port} ({mode})", snippet)
    else:
        # Port not reachable — give the most specific reason we have
        if c_state == "not_found":
            check("IB Gateway", "error",
                  "Container not found — run: docker compose --env-file .env.compose up -d",
                  snippet)
        elif c_state == "exited":
            code_str = f" (exit code {c_exit})" if c_exit is not None else ""
            if c_exit == 4:
                check("IB Gateway", "error",
                      f"Gateway version mismatch{code_str} — installed version not found. "
                      "Use Reset Installation below to reinstall.",
                      snippet, reset_available=True)
            else:
                check("IB Gateway", "error",
                      f"Container stopped{code_str} — run: docker compose --env-file .env.compose restart ib_gateway",
                      snippet)
        elif c_state == "restarting":
            check("IB Gateway", "warn",
                  "Container is restarting — wait 60–90 s then run diagnostics again",
                  snippet)
        elif login_st == "locked":
            check("IB Gateway", "error",
                  "Account locked out — reset IBKR password in Client Portal, then restart gateway",
                  snippet)
        elif login_st == "failed":
            check("IB Gateway", "error",
                  "Login failed — check IBKR username / password in Settings",
                  snippet)
        elif login_st == "ok":
            # Was logged in, now port gone — probably mid-restart
            check("IB Gateway", "warn",
                  f"Port {port} not reachable — gateway may be restarting (was logged in previously)",
                  snippet)
        else:
            check("IB Gateway", "error",
                  f"Not reachable on port {port} — check that IB Gateway is running",
                  snippet)

    # Remember where the IB Gateway entry sits so we can downgrade it later
    # if ib_insync fails to connect (port open but login not complete).
    gw_check_idx = len(checks) - 1

    # ── 2b. Scheduler port vs settings port mismatch ───────────
    # Read PID 1's environ — that's the scheduler.py process the entrypoint
    # exec'd, carrying the IBKR_PORT the entrypoint derived from
    # /data/gw_trading_mode. A plain `echo $IBKR_PORT` via docker exec would
    # spawn a fresh shell with the stale container-level env (compose default)
    # and report a false mismatch even when the scheduler is on the right port.
    try:
        import subprocess as _sp
        _sched_port_raw = _sp.run(
            ["docker", "exec", "yrvi-scheduler-1", "sh", "-c",
             "tr '\\0' '\\n' < /proc/1/environ | sed -n 's/^IBKR_PORT=//p'"],
            capture_output=True, text=True, timeout=5
        ).stdout.strip()
        if _sched_port_raw:
            _sched_port = int(_sched_port_raw)
            if _sched_port != port:
                _sched_mode = "live" if _sched_port in (4001, 4003) else "paper"
                _settings_mode = "live" if port in (4001, 4003) else "paper"
                check("Port Mismatch", "error",
                      f"Scheduler is using port {_sched_port} ({_sched_mode}) but settings say port {port} ({_settings_mode}) — "
                      f"switch trading mode in Settings (or restart the scheduler) to re-sync")
            else:
                check("Port Config", "ok", f"Scheduler and settings both on port {port}")
    except Exception:
        pass  # non-fatal — skip if docker CLI unavailable

    # ── 3. Last CSP execution ──────────────────────────────────
    try:
        state = load_state()
        run_date = state.get("run_date")
        if run_date:
            ts = datetime.fromisoformat(run_date)
            filled = state.get("filled_count", 0)
            premium = state.get("total_premium", 0)
            age_days = (now.replace(tzinfo=None) - ts.replace(tzinfo=None)).days
            detail = f"{ts.strftime('%a %b %-d')} — {filled} fill(s), ${premium:,.0f} premium"
            check("Last CSP Run", "ok" if age_days <= 14 else "warn", detail)
        else:
            check("Last CSP Run", "ok", "Not yet run — will execute automatically on the first Monday at 10:00 AM")
    except Exception as e:
        check("Last CSP Run", "warn", f"Could not read state: {e}")

    # ── 4. Last wheel check ────────────────────────────────────
    try:
        ctx = load_state().get("monday_context", {})
        updated = ctx.get("updated")
        if updated:
            ts = datetime.fromisoformat(updated)
            check("Last Wheel Check", "ok", ts.strftime("%a %b %-d at %-I:%M %p"))
        else:
            check("Last Wheel Check", "ok", "Not yet run — will execute automatically on the first Monday at 9:55 AM")
    except Exception as e:
        check("Last Wheel Check", "warn", f"Could not read state: {e}")

    # ── 5. Market status today ─────────────────────────────────
    today = now.date()
    if is_market_holiday(today):
        from market_calendar import nyse_holidays
        from datetime import timedelta as td
        next_open = today + td(days=1)
        while is_market_holiday(next_open) or next_open.weekday() >= 5:
            next_open += td(days=1)
        check("Market Today", "warn",
              f"Holiday — market closed. Next open: {next_open.strftime('%a %b %-d')}")
    elif today.weekday() >= 5:
        check("Market Today", "ok", "Weekend — market closed")
    else:
        check("Market Today", "ok", "Open — regular trading day")

    # ── 6. Version ─────────────────────────────────────────────
    version_file = BASE_DIR / "VERSION"
    version = version_file.read_text().strip() if version_file.exists() else "unknown"
    check("Version", "ok", f"v{version}")

    # IB Gateway / TWS build version — read from the ib_gateway container env
    # via docker inspect (the IBKR API only exposes the protocol serverVersion,
    # not the Gateway build). Surfacing it here makes a future version skew
    # visible at a glance instead of a mystery exit-4 crash.
    gw_ver = _get_gateway_tws_version()
    if gw_ver:
        check("Gateway Version", "ok", gw_ver)

    # ── 7 & 8. Live market data (SPY stock + options) ──────────
    # Only runs when gateway is reachable; adds ~10s to total diag time.
    if _gateway_running(port):
        host = os.environ.get("IBKR_HOST", "127.0.0.1")
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

        from ib_insync import IB, Stock, Option

        def _diag_is_nan(v):
            try:
                return v != v
            except Exception:
                return True

        def _next_friday_str():
            d = datetime.now().date()
            for i in range(1, 10):
                c = d + timedelta(days=i)
                if c.weekday() == 4:
                    return c.strftime("%Y%m%d")

        ib = IB()
        spy_price = None
        stk_q     = None
        connected = False
        try:
            ib.connect(host, port, clientId=random.randint(810, 839), timeout=10)
            ib.reqMarketDataType(3)
            connected = True
        except Exception as e:
            err_str = str(e).strip() or "connection refused"
            # Port was open but ib_insync couldn't connect — gateway is up but
            # not logged in yet (bad password, mid-startup, stuck dialog, etc.)
            # Downgrade the IB Gateway row with the most specific reason we have.
            cur_login = _gateway_login_status   # re-read; log monitor may have caught up
            if cur_login == "locked":
                gw_msg = (f"Port {port} open but account locked out — "
                          "reset IBKR password in Client Portal, then restart gateway")
            elif cur_login == "failed":
                gw_msg = (f"Port {port} open but login failed — "
                          "check IBKR credentials in Settings")
            else:
                gw_msg = (f"Port {port} open but API connection failed — "
                          "gateway may still be logging in or password is wrong")
            checks[gw_check_idx]["status"] = "error"
            checks[gw_check_idx]["detail"] = gw_msg
            if snippet:
                checks[gw_check_idx]["log_snippet"] = snippet
            overall = "error"
            check("SPY Price",    "error", f"IBKR connect failed: {err_str[:80]}")
            check("Options Data", "error", "Skipped — IBKR connection failed")

        if connected:
            # ── SPY stock price ────────────────────────────────
            try:
                stk   = Stock("SPY", "SMART", "USD")
                stk_q = ib.qualifyContracts(stk)
                tkr   = ib.reqMktData(stk_q[0], snapshot=False)
                ib.sleep(3)
                ib.cancelMktData(stk_q[0])
                price = tkr.last or tkr.close
                if price and not _diag_is_nan(price):
                    spy_price = price
                    # Label the feed by its actual type rather than assuming
                    # delayed — live accounts with a real-time subscription
                    # report marketDataType 1 and were being mislabeled.
                    _mdt = {1: "real-time", 2: "frozen",
                            3: "delayed", 4: "delayed (frozen)"}
                    mdt_label = _mdt.get(getattr(tkr, "marketDataType", None), "")
                    suffix = f" ({mdt_label})" if mdt_label else ""
                    check("SPY Price", "ok", f"${price:.2f}{suffix}")
                else:
                    check("SPY Price", "warn", "No price data — market may be closed")
            except Exception as e:
                check("SPY Price", "error", str(e)[:100])

            # ── SPY options bid/ask/delta ──────────────────────
            try:
                expiry = _next_friday_str()
                strikes = []
                if stk_q:
                    try:
                        chains = ib.reqSecDefOptParams("SPY", "", "STK", stk_q[0].conId)
                        ib.sleep(1)
                        # reqSecDefOptParams can return several SMART entries —
                        # including a degenerate single-strike/single-expiry
                        # artifact. Picking the first SMART match lands the probe
                        # on an illiquid far-dated strike with no quote, which
                        # false-flags healthy real-time data as "no bid/ask".
                        # Choose the richest chain (most strikes, then expiries).
                        smart = [c for c in chains if c.exchange == "SMART"]
                        chain = max(smart or chains,
                                    key=lambda c: (len(c.strikes), len(c.expirations)),
                                    default=None)
                        if chain:
                            strikes = sorted(chain.strikes)
                            fridays = sorted(e for e in chain.expirations
                                            if datetime.strptime(e, "%Y%m%d").weekday() == 4
                                            and e >= expiry)
                            if fridays and expiry not in chain.expirations:
                                expiry = fridays[0]
                    except Exception:
                        pass

                # Pick a near-the-money put (~3% OTM) — it has a liquid,
                # two-sided market. A deep-OTM strike (e.g. 10% OTM on low-vol
                # SPY with a short expiry) is near-worthless with no bid
                # (bid = -1), which would false-flag perfectly healthy data as
                # "no bid/ask". Compute the target directly and only trust the
                # chain's strike list if it has one genuinely close to it.
                if spy_price:
                    target = spy_price * 0.97
                    strike = round(target / 5) * 5
                    if strikes:
                        nearest = min(strikes, key=lambda s: abs(s - target))
                        if abs(nearest - target) <= 0.06 * spy_price:
                            strike = nearest
                elif strikes:
                    strike = strikes[len(strikes) // 2]
                else:
                    strike = 750

                contract  = Option("SPY", expiry, strike, "P", "SMART", currency="USD")
                qualified = ib.qualifyContracts(contract)
                if not qualified:
                    raise ValueError(f"Could not qualify SPY {expiry} ${strike:.0f}P")
                contract = qualified[0]

                # The delayed options farm (usopt) takes time to wake up on a
                # fresh connection — a flat 5s wait routinely misses quotes that
                # are perfectly available, false-flagging healthy data as
                # "no bid/ask". The real trader waits 60s for exactly this
                # (see trader.py near market open). Poll up to ~30s here and
                # stop as soon as we have a usable two-sided quote.
                otkr = ib.reqMktData(contract, genericTickList="106", snapshot=False)

                def _greek_delta(t):
                    for g in (t.modelGreeks, t.lastGreeks, t.bidGreeks, t.askGreeks):
                        if g is not None and not _diag_is_nan(g.delta):
                            return g.delta
                    return None

                bid = ask = delta = None
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    ib.sleep(2)
                    bid   = otkr.bid
                    ask   = otkr.ask
                    delta = _greek_delta(otkr)
                    ask_seen = not _diag_is_nan(ask) and ask is not None and ask > 0
                    bid_seen = not _diag_is_nan(bid) and bid is not None and bid > 0
                    # Stop early once the feed is clearly flowing (ask plus a
                    # bid or greeks) — matches the success test below.
                    if ask_seen and (bid_seen or delta is not None):
                        break
                ib.cancelMktData(contract)
                ib.sleep(0.5)

                bid_ok   = not _diag_is_nan(bid)   and bid   is not None and bid   > 0
                ask_ok   = not _diag_is_nan(ask)    and ask   is not None and ask   > 0
                delta_ok = delta is not None and not _diag_is_nan(delta)

                exp_fmt  = f"{expiry[4:6]}/{expiry[6:]}"
                label    = f"SPY {exp_fmt} ${strike:.0f}P"
                delta_str = f" / Δ {delta:.3f}" if delta_ok else ""

                # Data is flowing if we got an ask plus either a bid or greeks.
                # (A valid option always quotes an ask; bid can legitimately be
                # 0/-1 on a very cheap strike, so don't require bid alone.)
                if ask_ok and (bid_ok or delta_ok):
                    bid_str = f"${bid:.2f}" if bid_ok else "—"
                    check("Options Data", "ok",
                          f"{label} — Bid {bid_str} / Ask ${ask:.2f}{delta_str}")
                else:
                    now_et = now.astimezone(ET)
                    outside_hours = (
                        now_et.hour < 9
                        or (now_et.hour == 9 and now_et.minute < 30)
                        or now_et.hour >= 16
                    )
                    market_closed = today.weekday() >= 5 or is_market_holiday(today) or outside_hours
                    if market_closed:
                        check("Options Data", "warn",
                              f"{label} — no bid/ask (market closed — normal outside trading hours)")
                    else:
                        check("Options Data", "error",
                              f"{label} — no bid/ask. Live accounts need a paid OPRA + US stock "
                              f"data subscription (paper is free). See FAQ → \"Market Data Subscriptions\".")
            except Exception as e:
                check("Options Data", "error", str(e)[:120])

            try:
                ib.disconnect()
            except Exception:
                pass
    else:
        check("SPY Price",    "warn", "Skipped — IB Gateway not reachable")
        check("Options Data", "warn", "Skipped — IB Gateway not reachable")

    return {"checks": checks, "overall": overall, "timestamp": now.isoformat()}


# ── Endpoints ─────────────────────────────────────────────────

@app.get("/api/alerts")
def get_alerts(limit: int = 100):
    """Return the in-app alert feed for this box, newest first.

    `latest_id` lets the client compute its own unread count against a
    locally-stored 'last seen' id — no server-side read state, so the feed stays
    per-browser and the api never needs to write on a plain view.
    """
    with _alerts_lock:
        alerts: list = []
        if ALERTS_FILE.exists():
            try:
                alerts = json.loads(ALERTS_FILE.read_text())
            except Exception:
                alerts = []
    recent = alerts[-max(1, min(limit, ALERTS_MAX)):][::-1]
    return {
        "alerts":    recent,
        "latest_id": alerts[-1]["id"] if alerts else 0,
        "count":     len(alerts),
    }


@app.delete("/api/alerts")
def clear_alerts():
    """Clear the in-app alert feed (history only — does not touch Discord)."""
    with _alerts_lock:
        try:
            tmp = ALERTS_FILE.with_name(ALERTS_FILE.name + ".tmp")
            tmp.write_text(json.dumps([]))
            tmp.replace(ALERTS_FILE)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Could not clear alerts: {e}")
    return {"success": True}


@app.get("/api/status")
def get_status():
    settings = load_settings()
    ibkr = _get_ibkr_data(settings)
    port = settings.get("ibkr_port", 4004)
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
        **_weekly_token_status(),
    }

@app.get("/api/diag")
def get_diag():
    return _build_diag()


@app.get("/api/positions")
def get_positions():
    state = load_state()
    positions = state.get("positions", [])
    executions = state.get("executions", [])
    exec_map = {e.get("ticker"): e for e in executions if "ticker" in e}

    # Load trade_log once — used for both positions and portfolio enrichment
    _backfill_trade_log()
    trade_log = load_trade_log()
    tl_by_ticker: dict = {}
    tl_index: dict = {}
    for rec in trade_log:
        tl_by_ticker[rec.get("symbol")] = rec
        k = (rec.get("symbol"), rec.get("expiry"), rec.get("strike"), rec.get("right"))
        tl_index[k] = rec

    enriched = []
    for p in positions:
        ex  = exec_map.get(p["ticker"], {})
        tl  = tl_by_ticker.get(p["ticker"], {})
        fill_price       = ex.get("fill_price")
        strike           = p.get("strike")
        fill_yield_pct   = round(fill_price / strike * 100, 4) if (fill_price and strike) else None
        stock_at_entry   = tl.get("stock_price_at_entry") or ex.get("stock_price_at_entry")
        buffer_at_entry  = tl.get("buffer_pct_at_entry")
        enriched.append({
            **p,
            "status":                ex.get("status", "unknown"),
            "fill_price":            fill_price,
            "fill_yield_pct":        fill_yield_pct,
            "order_type":            ex.get("order_type"),
            "premium_collected":     ex.get("premium_collected", 0),
            "simulated":             ex.get("simulated", False),
            "exec_timestamp":        ex.get("exec_timestamp") or ex.get("timestamp"),
            "delta_at_entry":        tl.get("delta_at_entry") or ex.get("delta_at_entry"),
            "iv_at_entry":           tl.get("iv_at_entry") or ex.get("iv_at_entry"),
            "stock_price_at_entry":  stock_at_entry,
            "buffer_pct_at_entry":   buffer_at_entry,
        })

    settings = load_settings()
    ibkr = _get_ibkr_data(settings)

    portfolio = ibkr.get("portfolio", [])
    enriched_portfolio = []
    for item in portfolio:
        tl_key = (item.get("symbol"), item.get("expiry"), item.get("strike"), item.get("right"))
        tl = tl_index.get(tl_key, {})
        enriched_portfolio.append({
            **item,
            "delta_at_entry":       tl.get("delta_at_entry"),
            "iv_at_entry":          tl.get("iv_at_entry"),
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
        "excluded_tickers": load_settings().get("excluded_tickers", []),
    }

@app.get("/api/performance")
def get_performance():
    settings = load_settings()
    ytd = load_ytd()
    initial_fund_budget = settings.get("fund_budget", 250_000)
    compound_enabled    = settings.get("compound_enabled", True)
    goal_pct            = settings.get("goal_pct", 0.24)

    # Net Liq drives both the compound yield denominator AND the account-value
    # growth bar, so fetch it once regardless of compound mode.
    cached  = _ibkr_cache.get("data")
    net_liq = cached.get("account_value") if cached else None

    if compound_enabled:
        # Use net_liq for yield display — buying_power reflects only undeployed cash
        # and is misleading as a fund-size denominator when capital is tied up in CSPs.
        budget = net_liq or initial_fund_budget
    else:
        budget = initial_fund_budget

    # The GOAL is anchored to contributed capital (fund_budget), NOT net_liq —
    # the target must not chase the account around as it grows or shrinks.
    capital        = initial_fund_budget
    annual_target  = round(capital * goal_pct)          # premium income goal ($)
    account_target = round(capital * (1 + goal_pct))    # total account-value target
    monthly_target = round(annual_target / 12)          # 2%/month at the 24% default
    net_growth     = round(net_liq - capital, 2) if net_liq is not None else None
    net_growth_pct = (round(net_growth / capital * 100, 2)
                      if net_liq is not None and capital else None)

    raw_weeks = ytd.get("weeks", [])
    # Normalize and recompute yield_pct against current budget so stale stored
    # values (computed with old/default fund_budget) are always corrected.
    weeks = [
        {**w,
         "premium_collected": w.get("premium_collected", w.get("realized", 0)),
         "shares_sold_pnl":   w.get("shares_sold_pnl", 0),
         "total_realized":    w.get("total_realized", w.get("realized", 0)),
         "yield_pct":         round(
             w.get("premium_collected", w.get("realized", 0)) / budget * 100, 3
         ) if budget else w.get("yield_pct", 0)}
        for w in raw_weeks
    ]
    total = ytd.get("total_premium", 0.0)
    total_realized = round(sum(w["total_realized"] for w in weeks), 2)
    weeks_traded = ytd.get("weeks_traded", 0)
    avg_yield = (total / weeks_traded / budget * 100) if weeks_traded and budget else 0.0
    progress_pct = (total / annual_target * 100) if annual_target else 0.0

    def _fix_week_yield(w):
        if not w:
            return w
        prem = w.get("premium_collected", w.get("realized", 0))
        return {**w, "yield_pct": round(prem / budget * 100, 3) if budget else w.get("yield_pct", 0)}

    return {
        "weeks":          weeks,
        "total_premium":  total,
        "total_realized": total_realized,
        "weeks_traded":   weeks_traded,
        "avg_yield_pct":  round(avg_yield, 3),
        "best_week":      _fix_week_yield(ytd.get("best_week")),
        "worst_week":     _fix_week_yield(ytd.get("worst_week")),
        "annual_target":  annual_target,
        "progress_pct":   round(progress_pct, 1),
        "capital":        capital,
        "goal_pct":       goal_pct,
        "account_target": account_target,
        "monthly_target": monthly_target,
        "net_liq":        net_liq,
        "net_growth":     net_growth,
        "net_growth_pct": net_growth_pct,
    }

@app.get("/api/screener")
def run_screener():
    """
    Preview the FULL Monday sequence (wheel check + CSP pipeline) with zero side
    effects — a dry run of exactly what the scheduler / Run Now will execute.
    Connects to IBKR to query option chains for the covered-call decisions, so it
    takes ~20–40s. Places no orders, writes no state, posts no Discord.
    """
    settings = load_settings()
    try:
        import importlib, sys
        for mod_name in ["config", "screener", "position_sizer", "trader",
                         "wheel_manager", "monday_runner"]:
            if mod_name in sys.modules:
                importlib.reload(sys.modules[mod_name])
        from monday_runner import run_monday

        initial_fund_budget = settings.get("fund_budget", 250_000)
        compound_enabled    = settings.get("compound_enabled", True)

        # Pass cached account summary so the dry preview needs no extra IBKR
        # connection for budgeting (min(bp, net_liq) — see scheduler for rationale).
        account_summary = None
        cached       = _ibkr_cache.get("data")
        buying_power = cached.get("buying_power") if cached else None
        net_liq      = cached.get("account_value") if cached else None
        if compound_enabled and buying_power and net_liq:
            account_summary = (min(buying_power, net_liq), net_liq)

        outcome = run_monday(dry_run=True, account_summary=account_summary)
        wheel   = outcome.get("wheel", {})
        csp     = outcome.get("csp", {})

        positions     = csp.get("positions", [])
        total_premium = csp.get("total_premium", 0)
        total_capital = csp.get("total_capital", 0)

        # Current holdings (post-plan view comes from wheel_activity below)
        state           = load_state()
        wheel_holdings  = state.get("wheel_holdings", [])

        return {
            "positions":          positions,
            "raw_targets":        csp.get("raw_targets", []),
            "total_premium":      total_premium,
            "total_capital":      total_capital,
            "blended_yield":      round(total_premium / total_capital * 100 if total_capital else 0, 3),
            "budget":               csp.get("effective_budget", 0),
            # Display top-line for the Capital Allocation waterfall. Use the real
            # net liq (account_summary[1]) — NOT account_summary[0], which is
            # min(BuyingPower, NetLiq) and on cash/Roth accounts resolves to
            # buying power, breaking the "net liq − reserved = available" math.
            "total_budget":         (account_summary[1] if account_summary else initial_fund_budget),
            "initial_fund_budget":  initial_fund_budget,
            "compound_enabled":     csp.get("compound_enabled", compound_enabled),
            "reserved_capital":     wheel.get("reserved_capital", 0.0),
            "active_wheel_count":   wheel.get("active_wheel_count", 0),
            "wheel_holdings":       wheel_holdings,
            # Preview of the Monday wheel decisions (the part that used to be invisible):
            "wheel_plan":           wheel.get("wheel_activity", []),
            "wheel_freed_capital":  wheel.get("freed_capital", 0.0),
            "wheel_cc_premium":     wheel.get("cc_premium", 0.0),
            "wheel_shares_sold_pnl": wheel.get("shares_sold_pnl", 0.0),
            # Recovery reconciliation — positions already open in IBKR that a re-run skips:
            "already_open_put_tickers": csp.get("already_open_put_tickers", []),
            "target_fills":         csp.get("target_fills", 0),
            "dry_run":              True,
            "run_at":               datetime.now(PST).isoformat(),
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
    goal_pct:                 Optional[float] = None
    num_positions:            Optional[int]   = None
    min_position_size:        Optional[float] = None
    max_position_size:        Optional[float] = None
    max_delta:                Optional[float] = None
    min_buffer_pct:           Optional[float] = None
    earnings_filter_days:              Optional[int]   = None
    wheel_cc_ignore_earnings_filter:   Optional[bool]  = None
    wheel_retention_market_cap_min:    Optional[float] = None
    wheel_sell_when_cc_below_assigned: Optional[bool]  = None
    wheel_stop_loss_enabled:           Optional[bool]  = None
    stop_loss_pct:                     Optional[float] = None
    excluded_tickers:                  Optional[list[str]] = None
    compound_enabled:                  Optional[bool]  = None
    max_spread_pct:           Optional[float] = None
    min_bid_yield_pct:        Optional[float] = None
    max_spread_hard_cap:      Optional[float] = None
    min_oi_notional:          Optional[float] = None
    min_oi_floor:             Optional[int]   = None
    dry_run:                  Optional[bool]  = None
    ibkr_port:                Optional[int]   = None
    discord_webhook_enabled:       Optional[bool]  = None
    trading_mode:                  Optional[str]   = None
    execution_time:                Optional[str]   = None
    auto_restart_time:             Optional[str]   = None
    auto_restart_suppress_mins:    Optional[int]   = None
    auto_update_enabled:           Optional[bool]  = None

@app.post("/api/settings")
def update_settings(body: SettingsUpdate):
    current = load_settings()
    updates = {k: v for k, v in body.dict().items() if v is not None}
    if "excluded_tickers" in updates:
        updates["excluded_tickers"] = sorted({
            t.strip().upper() for t in updates["excluded_tickers"] if t and t.strip()
        })
    current.update(updates)
    save_settings(current)
    return current


class ExcludeToggle(BaseModel):
    ticker:   str
    excluded: bool

@app.post("/api/excluded-tickers")
def toggle_excluded(body: ExcludeToggle):
    """Add/remove a single ticker from the wheel-exclusion list — backs the
    per-holding checkbox on the dashboard. Excluded tickers get no new CSPs, no
    covered calls, are never sold, and are never adopted into wheel_holdings."""
    s   = load_settings()
    cur = {t.strip().upper() for t in s.get("excluded_tickers", []) if t and t.strip()}
    sym = body.ticker.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="ticker is required")
    if body.excluded:
        cur.add(sym)
    else:
        cur.discard(sym)
    s["excluded_tickers"] = sorted(cur)
    save_settings(s)
    return {"excluded_tickers": s["excluded_tickers"]}

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

class GatewayRestartTimeBody(BaseModel):
    auto_restart_time: str

def _restart_gateway_background(time_str: str) -> None:
    """Restart ib_gateway in a background thread — takes 30-60s."""
    try:
        subprocess.run(
            ["docker", "restart", "ib_gateway"],
            capture_output=True, text=True, timeout=120,
        )
        print(f"[api/gateway-restart] restarted with AUTO_RESTART_TIME={time_str}")
    except Exception as e:
        print(f"[api/gateway-restart] error: {e}")


@app.post("/api/gateway/patch-restart-time")
def patch_gateway_restart_time(body: GatewayRestartTimeBody):
    """Write the new restart time to the shared volume and restart ib_gateway.
    The entrypoint reads /data/gw_auto_restart_time and exports it as
    AUTO_RESTART_TIME before the base image starts, so the change is permanent
    across container restarts without editing .env.compose."""
    time_str = body.auto_restart_time.strip()

    # Write override file to shared volume — entrypoint reads this on every startup
    override_path = Path("/data/gw_auto_restart_time")
    try:
        override_path.write_text(time_str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write override file: {e}")

    # Restart gateway in background — returns immediately, restart takes ~30-60s
    threading.Thread(target=_restart_gateway_background, args=(time_str,), daemon=True).start()

    return {"restarting": True, "auto_restart_time": time_str}


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
def set_trading_mode(body: TradingModeRequest, background_tasks: BackgroundTasks):
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
                detail=f"Live credentials not configured. Add these in the Secrets page: {missing_str}",
            )

    current = load_settings()
    current["trading_mode"] = body.mode
    ibkr_port = 4003 if body.mode == "live" else 4004
    current["ibkr_port"]    = ibkr_port

    # Write trading mode to shared volume so ib_gateway entrypoint picks it up on restart.
    gw_mode_file = Path("/data/gw_trading_mode")
    try:
        gw_mode_file.write_text(body.mode)
    except Exception as e:
        print(f"[api/trading-mode] failed to write gw_trading_mode: {e}")

    # Keep .env.compose in sync so a plain `docker compose up` (no entrypoint
    # re-derivation, e.g. before /data/gw_trading_mode exists) still lands on the
    # right port. When containerized this must target the bind-mounted host file
    # at /host_repo/.env.compose — BASE_DIR is /app inside the container, so
    # writing there only touches the ephemeral copy and the host file goes stale.
    host_env_file = Path("/host_repo/.env.compose")
    env_file = host_env_file if host_env_file.exists() else BASE_DIR / ".env.compose"
    try:
        if env_file.exists():
            lines = env_file.read_text().splitlines()
            updated = []
            for line in lines:
                if line.startswith("TRADING_MODE="):
                    updated.append(f"TRADING_MODE={body.mode}")
                elif line.startswith("IBKR_PORT="):
                    updated.append(f"IBKR_PORT={ibkr_port}")
                else:
                    updated.append(line)
            env_file.write_text("\n".join(updated) + "\n")
            print(f"[api/trading-mode] updated .env.compose: TRADING_MODE={body.mode} IBKR_PORT={ibkr_port}")
    except Exception as e:
        print(f"[api/trading-mode] failed to update .env.compose: {e}")

    if body.mode == "live":
        current["account"] = get_secret("account_live")
    else:
        current["account"] = get_secret("account_paper")

    _restart_ibgateway()

    # Restart scheduler too so it re-reads /data/gw_trading_mode and re-derives
    # IBKR_PORT — otherwise it keeps trading on the previous mode's port.
    _restart_scheduler()

    # Restart the api as well: like the scheduler it caches IBKR_PORT from its
    # env at import (config.py), so without this it keeps dialing the previous
    # mode's port (e.g. screener "Run Now" hitting 4004 after switching to live).
    # Deferred to a BackgroundTask so the response below reaches the client first.
    background_tasks.add_task(_restart_api_self)

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
    settings = load_settings()

    initial_fund_budget = settings.get("fund_budget", 250_000)
    compound_enabled    = settings.get("compound_enabled", True)
    if compound_enabled:
        cached  = _ibkr_cache.get("data")
        net_liq = cached.get("account_value") if cached else None
        budget  = net_liq or initial_fund_budget
    else:
        budget = initial_fund_budget

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

    weekly_summaries = [
        {**w,
         "premium_collected": w.get("premium_collected", w.get("realized", 0)),
         "yield_pct": round(
             w.get("premium_collected", w.get("realized", 0)) / budget * 100, 3
         ) if budget else w.get("yield_pct", 0)}
        for w in ytd.get("weeks", [])
    ]

    return {
        "current_week": {
            "run_date":       state.get("run_date"),
            "executions":     enriched,
            "weekly_pnl":     state.get("weekly_pnl", {}),
            "wheel_activity": state.get("monday_context", {}).get("wheel_activity", []),
        },
        "weekly_summaries": weekly_summaries,
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
        import requests as req, time as _time
        r = req.get(_GITHUB_VERSION_URL, params={"_": int(_time.time())},
                    headers={"Cache-Control": "no-cache"}, timeout=5)
        r.raise_for_status()
        latest = r.text.strip()
        def parse(v): return [int(x) for x in v.lstrip('v').split('.')]
        up_to_date = parse(current) >= parse(latest)
        return {"current": current, "latest": latest, "up_to_date": up_to_date}
    except Exception:
        return {"current": current, "latest": None, "up_to_date": None, "error": "unavailable"}


@app.post("/api/version/upgrade")
def version_upgrade():
    version_file = BASE_DIR / "VERSION"
    current = version_file.read_text().strip() if version_file.exists() else "unknown"

    # Confirm there is actually an update to apply
    try:
        import requests as req, time as _time
        r = req.get(_GITHUB_VERSION_URL, params={"_": int(_time.time())},
                    headers={"Cache-Control": "no-cache"}, timeout=5)
        r.raise_for_status()
        latest = r.text.strip()
    except Exception:
        return {"success": False,
                "output": "Could not fetch latest version from GitHub — upgrade aborted"}

    if current == latest:
        return {"success": False,
                "output": f"Already up to date ({current}) — no upgrade needed"}

    output_parts: list[str] = []

    # /host_repo is the live host filesystem (bind-mounted in docker-compose.yml).
    # git pull must run there — the container's /app is a baked snapshot with no .git.
    host_repo = Path("/host_repo")
    if not (host_repo / ".git").exists():
        return {"success": False,
                "output": (
                    "Upgrade requires the host repo to be mounted at /host_repo.\n"
                    "Run manually from a terminal:\n"
                    "  git pull origin main\n"
                    "  bash scripts/yrvi-build.sh all --paper"
                )}

    # ── Step 1: git pull ──────────────────────────────────────
    # Use the HTTPS URL directly — the container has no SSH keys or agent,
    # so pulling via "origin" (which may be an SSH remote) would fail.
    _GIT_HTTPS = "https://github.com/controllinghand/you_rock_fund.git"

    # git 2.35.2+ refuses to operate on a repo whose files are owned by a
    # different user than the one running git ("detected dubious ownership").
    # /host_repo is bind-mounted and owned by the host user, not the container's
    # git user, so mark it safe. safe.directory is only honored from global/system
    # config (git ignores it from -c / the command line), so it must be written
    # to the global gitconfig before any git command touches the repo.
    _git_env = {**os.environ, "HOME": os.environ.get("HOME", "/root")}
    subprocess.run(
        ["git", "config", "--global", "--replace-all", "safe.directory", str(host_repo)],
        capture_output=True, env=_git_env,
    )

    # Discard any local modifications to tracked files (e.g. VERSION) so the
    # pull never aborts with "your local changes would be overwritten".
    subprocess.run(
        ["git", "checkout", "--", "."],
        capture_output=True, cwd=str(host_repo), env=_git_env,
    )

    try:
        pull = subprocess.run(
            ["git", "pull", _GIT_HTTPS, "main"],
            capture_output=True, text=True, timeout=60,
            cwd=str(host_repo), env=_git_env,
        )
        output_parts.append(
            f"$ git pull {_GIT_HTTPS} main\n{(pull.stdout + pull.stderr).strip()}"
        )
        if pull.returncode != 0:
            return {"success": False, "output": "\n\n".join(output_parts)}
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "git pull timed out after 60s — upgrade aborted"}
    except Exception as e:
        return {"success": False, "output": f"git pull failed: {e}"}

    # ── Step 2: yrvi-build.sh all --paper ────────────────────
    # Run from /host_repo so docker compose sends updated host files as the
    # build context. Launched via Popen (non-blocking) so this response returns
    # before yrvi-build.sh rebuilds and restarts the containers (including this one).
    build_script = host_repo / "scripts" / "yrvi-build.sh"
    if not build_script.exists():
        output_parts.append(
            "scripts/yrvi-build.sh not found — run manually from terminal"
        )
        return {"success": False, "output": "\n\n".join(output_parts)}

    upgrade_log = Path("/data/upgrade.log")
    try:
        upgrade_log.write_text("")  # clear any previous run
        log_fh = open(upgrade_log, "w")
        _mode_flag = "--live" if load_settings().get("trading_mode") == "live" else "--paper"
        _env = os.environ.copy()
        if _mode_flag == "--live":
            _env["YRVI_ENV"] = "live"
        subprocess.Popen(
            ["bash", str(build_script), "all", _mode_flag],
            cwd=str(host_repo),
            stdout=log_fh,
            stderr=log_fh,
            start_new_session=True,
            env=_env,
        )
        log_fh.close()  # parent closes; child retains its own fd copy
        output_parts.append(f"$ bash scripts/yrvi-build.sh all {_mode_flag}\n(launched)")
        return {"success": True, "output": "\n\n".join(output_parts)}
    except Exception as e:
        output_parts.append(
            f"Failed to launch yrvi-build.sh: {e}\nRun manually from terminal."
        )
        return {"success": False, "output": "\n\n".join(output_parts)}


@app.get("/api/upgrade/log")
def upgrade_log_read():
    import re
    log = Path("/data/upgrade.log")
    if not log.exists():
        return {"content": ""}
    raw = log.read_text(errors="replace")
    clean = re.sub(r'\x1b\[[0-9;]*[mGKHFABCDJsur]', '', raw)
    return {"content": clean}


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


# In-memory run status — shared between manual_run thread and /api/run-status
_run_status: dict = {
    "executing":      False,
    "started_at":     None,
    "result":         None,
    "error":          None,
    "current_ticker": None,
    "current_stage":  None,
    "ticker_results": [],
}


@app.get("/api/run-status")
def get_run_status():
    """Poll this to check if a manual or scheduled run is in progress or just completed."""
    # If manual run is active, it owns the status
    if _run_status.get("executing"):
        return _run_status
    # Check if scheduler wrote a progress file (scheduled run)
    progress_file = Path("/data/run_progress.json")
    try:
        if progress_file.exists():
            sched = json.loads(progress_file.read_text())
            if sched.get("executing"):
                return {**_run_status, **sched, "source": "scheduler"}
    except Exception:
        pass
    return _run_status


@app.post("/api/manual-run")
def manual_run():
    """Trigger a CSP pipeline run immediately, outside the normal schedule."""
    import threading

    if _run_status["executing"]:
        raise HTTPException(status_code=409, detail="A run is already in progress")

    def _run():
        _run_status.update({"executing": True, "started_at": datetime.now().isoformat(),
                            "result": None, "error": None, "ticker_results": [],
                            "current_ticker": None, "current_stage": None})
        try:
            import importlib, sys
            for mod in ["config", "screener", "position_sizer", "trader",
                        "wheel_manager", "monday_runner"]:
                if mod in sys.modules:
                    importlib.reload(sys.modules[mod])
            from monday_runner import run_monday

            _ticker_results = []

            def _progress(ticker=None, stage=None, result=None):
                if result:
                    _ticker_results.append(result)
                _run_status["current_ticker"] = ticker
                _run_status["current_stage"]  = stage
                _run_status["ticker_results"] = list(_ticker_results)

            # Full Monday sequence, live: wheel check (sell shares / write CCs) then CSPs.
            outcome = run_monday(dry_run=False, progress_callback=_progress)
            _run_status["current_ticker"] = None
            _run_status["current_stage"]  = None

            wheel = outcome.get("wheel", {})
            csp   = outcome.get("csp", {})
            _run_status.update({
                "executing": False,
                "result": {
                    "fills":         csp.get("fills", 0),
                    "premium":       csp.get("csp_premium", 0),
                    "cc_premium":    wheel.get("cc_premium", 0),
                    "freed_capital": wheel.get("freed_capital", 0),
                    "completed":     datetime.now().isoformat(),
                }
            })
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Manual run failed: {e}", exc_info=True)
            _run_status.update({"executing": False, "error": str(e), "result": None})

    threading.Thread(target=_run, daemon=True).start()
    return {"success": True, "message": "Monday sequence started (wheel check + CSP pipeline)"}


@app.post("/api/test-run")
def test_run():
    """Trigger a DRY RUN of the CSP pipeline — no real orders placed. For testing status UI."""
    import threading, os

    if _run_status["executing"]:
        raise HTTPException(status_code=409, detail="A run is already in progress")

    def _run():
        _run_status.update({"executing": True, "started_at": datetime.now().isoformat(),
                            "result": None, "error": None, "ticker_results": [],
                            "current_ticker": None, "current_stage": None})
        # Temporarily force DRY_RUN on
        os.environ["DRY_RUN"] = "true"
        try:
            import importlib, sys
            for mod in ["config", "screener", "position_sizer", "trader"]:
                if mod in sys.modules:
                    importlib.reload(sys.modules[mod])
            from screener import get_top_targets
            from position_sizer import size_all
            from trader import execute_positions

            settings    = load_settings()
            n           = settings.get("num_positions", 5)
            all_targets = get_top_targets(n * 2)
            positions   = size_all(all_targets[:n])
            _ticker_results = []

            def _progress(ticker=None, stage=None, result=None):
                if result:
                    _ticker_results.append(result)
                _run_status["current_ticker"] = ticker
                _run_status["current_stage"]  = stage
                _run_status["ticker_results"] = list(_ticker_results)

            execute_positions(positions, extra_targets=all_targets, status_callback=_progress)
            _run_status["current_ticker"] = None
            _run_status["current_stage"]  = None

            filled = [r for r in _ticker_results if r.get("status") in ("filled", "partial_fill", "dry_run")]
            _run_status.update({"executing": False,
                                "result": {"fills": len(filled), "premium": 0, "completed": datetime.now().isoformat(), "dry_run": True}})
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Test run failed: {e}", exc_info=True)
            _run_status.update({"executing": False, "error": str(e), "result": None})
        finally:
            os.environ.pop("DRY_RUN", None)

    threading.Thread(target=_run, daemon=True).start()
    return {"success": True, "message": "Dry run started — no real orders will be placed"}


@app.post("/api/feedback")
def submit_feedback(body: FeedbackRequest):
    webhook_url = _read_secret_or_env("discord_feedback_webhook_url", "DISCORD_FEEDBACK_WEBHOOK_URL") or _FEEDBACK_WEBHOOK_DEFAULT
    if not webhook_url:
        raise HTTPException(
            status_code=503,
            detail="Feedback webhook not configured — get the URL from #yrvi_secrets in the You Rock Club Discord and add it in Secrets."
        )
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    settings = load_settings()
    version_file = BASE_DIR / "VERSION"
    version = version_file.read_text().strip() if version_file.exists() else "unknown"
    mode = settings.get("trading_mode", "paper").capitalize()
    now_str = datetime.now(PST).strftime("%Y-%m-%d %-I:%M %p %Z")

    # Identify sender: paper username → live username → paper account ID → "Unknown"
    sender = (
        _read_secret_or_env("tws_userid_paper", "IBKR_USERNAME_PAPER")
        or _read_secret_or_env("tws_userid_live", "IBKR_USERNAME_LIVE")
        or _read_secret_or_env("account_paper", "IBKR_ACCOUNT_PAPER")
        or "Unknown"
    )

    emoji = "🐛" if body.type == "bug" else "💡"
    label = "Bug Report" if body.type == "bug" else "Feature Request"

    content = (
        f"{emoji} **{label}** from **{sender}**\n"
        f"```\n{body.message.strip()}\n```\n"
        f"v{version} · {mode} mode · {now_str}"
    )

    try:
        import requests as req
        r = req.post(webhook_url, json={"content": content}, timeout=5)
        r.raise_for_status()
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Discord post failed: {e}")


@app.post("/api/ytd/weeks")
def upsert_ytd_week(body: YtdWeekRequest):
    """Add or update a single week in ytd_tracker.json."""
    from reconciler import _load_existing_weeks, _finalize_ytd
    import re
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", body.week_start):
        raise HTTPException(status_code=400, detail="week_start must be YYYY-MM-DD")
    weeks = _load_existing_weeks()
    weeks[body.week_start] = {
        "week_start":        body.week_start,
        "premium_collected": round(body.premium_collected, 2),
        "total_realized":    round(body.premium_collected, 2),
    }
    ytd = _finalize_ytd(weeks)
    YTD_FILE.write_text(json.dumps(ytd, indent=2))
    return {"committed": True, "weeks_total": ytd["weeks_traded"], "total_premium": ytd["total_premium"]}

@app.delete("/api/ytd/weeks/{week_start}")
def delete_ytd_week(week_start: str):
    """Remove a week from ytd_tracker.json by week_start (YYYY-MM-DD)."""
    from reconciler import _load_existing_weeks, _finalize_ytd
    weeks = _load_existing_weeks()
    if week_start not in weeks:
        raise HTTPException(status_code=404, detail="week not found")
    del weeks[week_start]
    ytd = _finalize_ytd(weeks)
    YTD_FILE.write_text(json.dumps(ytd, indent=2))
    return {"committed": True, "weeks_total": ytd["weeks_traded"], "total_premium": ytd["total_premium"]}

@app.post("/api/reconcile/commit")
def reconcile_commit(body: ReconcileCommitRequest):
    """Write a previously previewed weeks list into ytd_tracker.json without re-fetching."""
    from reconciler import _load_existing_weeks, _finalize_ytd
    if not body.weeks:
        raise HTTPException(status_code=400, detail="weeks list is empty")
    merged = _load_existing_weeks()
    for w in body.weeks:
        ws = w.get("week_start")
        if not ws:
            continue
        merged[ws] = {
            "week_start":        ws,
            "premium_collected": round(w.get("premium_collected", w.get("realized", 0)), 2),
            "total_realized":    round(w.get("total_realized", w.get("premium_collected", w.get("realized", 0))), 2),
        }
    ytd = _finalize_ytd(merged)
    YTD_FILE.write_text(json.dumps(ytd, indent=2))
    return {"committed": True, "weeks_found": len(body.weeks), "total_premium": ytd["total_premium"], "weeks": ytd["weeks"]}

@app.post("/api/reconcile/upload")
def reconcile_upload(body: ReconcileUploadRequest):
    """Parse a Flex XML string and preview or commit the ytd_tracker rebuild."""
    from reconciler import reconcile_from_xml
    if not body.xml or not body.xml.strip():
        raise HTTPException(status_code=400, detail="xml is required")
    try:
        result = reconcile_from_xml(
            body.xml,
            date_from=body.date_from,
            date_to=body.date_to,
            dry_run=body.dry_run,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@app.post("/api/reconcile/flex")
def reconcile_flex(body: ReconcileFlexRequest):
    """Fetch Flex XML from IBKR and preview or commit the ytd_tracker rebuild."""
    from reconciler import reconcile_from_flex_service
    token    = _read_secret_or_env("flex_token",    "IBKR_FLEX_TOKEN")
    query_id = _read_secret_or_env("flex_query_id", "IBKR_FLEX_QUERY_ID")
    if not token or not query_id:
        raise HTTPException(
            status_code=400,
            detail="flex_token and flex_query_id secrets must be set before using this feature",
        )
    try:
        result = reconcile_from_flex_service(
            token,
            query_id,
            date_from=body.date_from,
            date_to=body.date_to,
            dry_run=body.dry_run,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return result
