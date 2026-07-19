"""Single source of truth for the operator's configured timezone.

Every module that timestamps or displays a local time must resolve it here, so
Dashboard → Settings → Timezone actually means something. Before this existed,
scheduler.py resolved the setting but api.py, monday_runner.py and
discord_poster.py each hardcoded ZoneInfo("America/Los_Angeles"). An operator
outside Pacific therefore got jobs that FIRED on their configured zone while
every timestamp, Discord post and dashboard string was computed in Pacific —
the setting was half-wired rather than merely duplicated.

Precedence, highest first:

  1. settings.json ["timezone"]  — the app's own state, on the yrvi_data volume.
     Written by the dashboard, survives upgrades. This is the source of truth.
  2. TIME_ZONE env               — install-time seed from .env.compose. Kept as
     a fallback only: it is also what sets the container's TZ (log timestamps),
     but it must never beat an explicit operator choice.
  3. America/Los_Angeles         — the fund's home zone.

NOT for market or broker time. Exchange hours are Eastern and IBKR invalidates
the weekly IB Key token at 01:00 America/New_York; both are facts about the
outside world, not operator preferences, and must stay hardcoded where they are
(see api.py's separate ET constant). Only ever use this for "what time is it
where the operator is".

Resolution happens at import, so a timezone change needs a container restart —
same as before, and the dashboard already restarts on a settings change.
"""

import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "America/Los_Angeles"

# /data is the volume in containers; the bare filename covers the symlink the
# images place at /app/settings.json and a plain checkout run from the repo.
_SETTINGS_CANDIDATES = (
    Path("/data/settings.json"),
    Path(__file__).parent / "settings.json",
    Path("settings.json"),
)


def _configured_name() -> str:
    """The configured IANA name, without validating it."""
    for path in _SETTINGS_CANDIDATES:
        try:
            if path.exists():
                name = (json.loads(path.read_text()) or {}).get("timezone")
                if name and str(name).strip():
                    return str(name).strip()
        except Exception:
            # A missing/corrupt settings file must never stop a module importing.
            continue
    return (os.environ.get("TIME_ZONE") or "").strip() or DEFAULT_TIMEZONE


def resolve_timezone() -> ZoneInfo:
    """Resolve the operator's timezone, falling back rather than raising.

    A bad IANA name must not take down the scheduler or the api: an unusable
    timezone is a cosmetic problem, but failing to import is an outage.
    """
    try:
        return ZoneInfo(_configured_name())
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


# Import-time constant. Named LOCAL_TZ because it is the operator's zone and not
# necessarily Pacific; modules alias it to their existing `PST` name so the
# hundreds of `datetime.now(PST)` call sites stay untouched.
LOCAL_TZ = resolve_timezone()
