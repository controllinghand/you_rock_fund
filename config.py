import json
import os
import socket
from pathlib import Path
from dotenv import load_dotenv

from secrets_client import get_secret

load_dotenv()

# ── You Rock Volatility Income Fund ──────────────────────────
_DURABLE_MODE_FILE = Path("/data/gw_trading_mode")


def _resolve_trading_mode() -> str:
    """live|paper. Durable file first — it is the source of truth every container
    reads (the entrypoints, the gateway, and now this)."""
    try:
        if _DURABLE_MODE_FILE.exists():
            mode = _DURABLE_MODE_FILE.read_text().strip().lower()
            if mode in ("live", "paper"):
                return mode
    except Exception:
        pass
    return (os.environ.get("TRADING_MODE") or "").strip().lower()


def _resolve_ibkr_port(mode: str) -> int:
    """IBKR_PORT is DERIVED from the trading mode, never independently configured
    — that is what stops the port and the account disagreeing (the v3.9.19 bug).

    The entrypoint exports it for the long-running process, but `docker exec`
    does NOT inherit an entrypoint's exports: it gets the container's Config.Env.
    So every documented manual command —
        docker compose exec scheduler python wheel_manager.py detect
    — must be able to derive this itself. Re-deriving here rather than reading a
    port from the environment keeps ONE rule, used by both paths.

    Raises rather than defaulting when the mode is unknown: silently assuming
    'paper' on a live box would point this process at the wrong account, and a
    loud failure is the only safe option for a trading system.
    """
    env_port = (os.environ.get("IBKR_PORT") or "").strip()
    if env_port:
        return int(env_port)
    if mode == "live":
        return 4003
    if mode == "paper":
        return 4004
    raise RuntimeError(
        "Cannot determine IBKR_PORT: no IBKR_PORT in the environment and the "
        "trading mode is unknown (no /data/gw_trading_mode and no TRADING_MODE). "
        "Refusing to guess — the wrong port means the wrong IBKR account."
    )


IBKR_HOST      = os.environ.get("IBKR_HOST", "ib_gateway")
_TRADING_MODE  = _resolve_trading_mode()
IBKR_PORT      = _resolve_ibkr_port(_TRADING_MODE)   # 4003=live, 4004=paper
# Client ids 2-5 are hardcoded below; 1 is the default for trader/one-off runs.
IBKR_CLIENT_ID = int((os.environ.get("IBKR_CLIENT_ID") or "1").strip())


# ── IB Gateway connection helpers (single source of truth) ───
# Keep this list aligned with api.py's port→mode mapping. Paper ports never
# require 2FA; live ports do. The durable-mode file (/data/gw_trading_mode)
# uses 4003=live / 4004=paper; legacy .env used 4001/4002.
_PAPER_PORTS = (4002, 4004)
_LIVE_PORTS  = (4001, 4003)


def account_type_for_port(port: int) -> str:
    """Map an IB Gateway port → 'paper' or 'live'. Single source of truth so
    diagnostics never mislabel paper (4004) as live and ask for a 2FA that
    paper accounts don't use."""
    return "paper" if int(port) in _PAPER_PORTS else "live"


ACCOUNT_TYPE = account_type_for_port(IBKR_PORT)
# Human-readable badge for Discord alerts so every message says which account
# it came from (live MacBook vs paper mini) at a glance.
MODE_LABEL   = "🔴 LIVE" if ACCOUNT_TYPE == "live" else "📄 PAPER"


def probe_port(host: str, port: int, timeout: float = 3.0) -> bool:
    """True if a TCP connection to host:port succeeds — i.e. the gateway is
    listening. Distinguishes 'port open but API handshake hung' (login/2FA
    dialog or reconnect wedge) from 'port closed' (container down, still
    booting, or host asleep)."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def gateway_unreachable_message(host: str, port: int) -> str:
    """Build a precise 'where + why' message for a failed IBKR connect, so the
    Discord alert points at the actual failure instead of a generic timeout."""
    acct = account_type_for_port(port)
    if probe_port(host, port):
        where = (f"port {port} is OPEN but the IBKR API handshake timed out — "
                 f"gateway is up but stuck (login/2FA dialog or reconnect wedge); "
                 f"a `restart ib_gateway` usually clears it")
    else:
        where = (f"port {port} is CLOSED — gateway not listening "
                 f"(container down, still booting, or host asleep)")
    twofa = "No 2FA needed for paper." if acct == "paper" else "Check 2FA login."
    return f"IB Gateway unreachable at {host}:{port} ({acct} account) — {where}. {twofa}"

# Resolved above from the durable /data/gw_trading_mode first, env second — the
# same rule the entrypoints use. Reading env alone here would let `docker exec`
# pick a different mode (and therefore a different ACCOUNT) than the running
# scheduler, on the same box, in the same minute.
TRADING_MODE   = _TRADING_MODE or "paper"
_ACCOUNT_KEY   = "account_live" if TRADING_MODE == "live" else "account_paper"
ACCOUNT        = get_secret(_ACCOUNT_KEY, "ACCOUNT")
if not ACCOUNT:
    raise RuntimeError(
        f"ACCOUNT not set — configure '{_ACCOUNT_KEY}' in the secrets container "
        f"(http://localhost:8001) or set ACCOUNT in the environment."
    )

# IBKR client IDs — each module gets its own to allow concurrent connections
IBKR_CLIENT_ID_WHEEL = 2        # wheel_manager.py (scheduler Monday 9:55 job)
IBKR_CLIENT_ID_RISK  = 3        # risk_manager.py
IBKR_CLIENT_ID_PREVIEW = 4      # API-driven Monday runner (Run Screener / Run Now) —
                                # distinct from the scheduler's wheel id so a manual
                                # run from the dashboard never collides with the 9:55 job
IBKR_CLIENT_ID_CASH_PARK = 5    # cash_park.py — Monday sweep buy + end-of-week sell

# Execution
EXECUTE_HOUR_PST = 10            # 10AM PST Monday
EXECUTE_MINUTE   = 0

# Screener API. The endpoint is the same for every deployment, so it is baked
# rather than required from the environment — a hard os.environ[] here meant a
# missing or mistyped .env.compose key took down every module that imports
# config. The SECRET stays in the secrets container: never default a credential.
RENDER_URL    = os.environ.get(
    "RENDER_URL", "https://yourockclub-ledger-sync.onrender.com/api/targets/csp"
)
RENDER_SECRET = get_secret("render_secret", "RENDER_SECRET")

# ── Fund parameters (settings.json is source of truth) ───────

_BASE = Path(__file__).parent

def get_settings() -> dict:
    """Hot-reload fund settings from settings.json on every call."""
    defaults: dict = {}
    defaults_file = _BASE / "settings_default.json"
    settings_file = _BASE / "settings.json"
    if defaults_file.exists():
        try:
            defaults = json.loads(defaults_file.read_text())
        except Exception:
            pass
    if settings_file.exists():
        try:
            return {**defaults, **json.loads(settings_file.read_text())}
        except Exception:
            pass
    return defaults

_s = get_settings()

TOTAL_FUND_BUDGET   = _s.get("fund_budget",      250_000)
NUM_POSITIONS       = _s.get("num_positions",     5)
TARGET_PER_POSITION = int(TOTAL_FUND_BUDGET // NUM_POSITIONS)
MAX_PER_POSITION    = _s.get("max_position_size", 70_000)
WEEKLY_INCOME_GOAL  = 0.01       # 1% per week
DRY_RUN                          = _s.get("dry_run",                          False)
WHEEL_CC_IGNORE_EARNINGS_FILTER  = _s.get("wheel_cc_ignore_earnings_filter",  True)
WHEEL_RETENTION_MARKET_CAP_MIN   = _s.get("wheel_retention_market_cap_min",    5_000_000_000)
WHEEL_SELL_WHEN_CC_BELOW_ASSIGNED = _s.get("wheel_sell_when_cc_below_assigned", False)
WHEEL_STOP_LOSS_ENABLED          = _s.get("wheel_stop_loss_enabled",          False)
STOP_LOSS_PCT                    = _s.get("stop_loss_pct",                    0.10)
COMPOUND_ENABLED                 = _s.get("compound_enabled",                 True)
# Cash (non-margin) account: IBKR BuyingPower is already real settled cash and
# already excludes capital converted to wheel stock, so the net_liq − reserved
# cap is a double-count that goes falsely negative when holdings are underwater.
# When True, deploy BuyingPower directly. Default False = unchanged margin logic.
CASH_ACCOUNT                     = _s.get("cash_account",                     False)
# Tickers the user has excluded from the wheel entirely — no new CSPs, no covered
# calls, never sold, never adopted into wheel_holdings. Normalized to uppercase.
EXCLUDED_TICKERS                 = sorted({t.strip().upper() for t in _s.get("excluded_tickers", []) if t and t.strip()})
