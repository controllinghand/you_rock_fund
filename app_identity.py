# app_identity.py
# ----------------------------------------------------------
# Stable, anonymous per-box identity for outbound screener-API calls, so
# the Render service can tell legit boxes apart and count unique installs.
#
#   - install_id:   a random UUID minted ONCE and persisted to the durable
#                   /data volume (survives upgrades). Anonymous — no account
#                   info, just enough to distinguish one box from another.
#   - version:      the repo VERSION file.
#   - trading_mode: the durable /data/gw_trading_mode file (paper|live).
#
# These ride as X-Install-Id / X-App-Version / X-Trading-Mode request headers
# and are recorded by the screener API's access log. Best-effort: every read
# is guarded so a missing file never breaks a screener run.
# ----------------------------------------------------------

import uuid
from pathlib import Path

_BASE = Path(__file__).parent
_DATA_DIR = Path("/data")


def _install_id_path() -> Path:
    # Prefer the durable volume (survives upgrades); fall back to the code dir
    # for local/dev runs that don't mount /data.
    if _DATA_DIR.is_dir():
        return _DATA_DIR / "install_id"
    return _BASE / ".install_id"


def get_install_id() -> str:
    p = _install_id_path()
    try:
        existing = p.read_text().strip()
        if existing:
            return existing
    except Exception:
        pass
    new_id = uuid.uuid4().hex
    try:
        p.write_text(new_id)
    except Exception:
        pass  # ephemeral id for this run if we can't persist — still usable
    return new_id


def get_version() -> str:
    try:
        return (_BASE / "VERSION").read_text().strip() or "unknown"
    except Exception:
        return "unknown"


def get_trading_mode() -> str:
    try:
        mode = (_DATA_DIR / "gw_trading_mode").read_text().strip().lower()
        if mode in ("paper", "live"):
            return mode
    except Exception:
        pass
    return "unknown"


def request_headers() -> dict:
    """Identity headers to attach to outbound screener-API requests."""
    return {
        "X-Install-Id": get_install_id(),
        "X-App-Version": get_version(),
        "X-Trading-Mode": get_trading_mode(),
    }
