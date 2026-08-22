"""Named strategy tracks — the wheel modes the fund can run.

Each box in the You Rock comparison runs a DIFFERENT track on purpose (dev =
CSP-only, YRVIP = ride-the-wave, Johnny = stop-loss), and the whole point of
the comparison is knowing which arm produced which numbers. That makes one
rule non-negotiable:

    THE ACTIVE TRACK IS DERIVED FROM THE LIVE SETTINGS, NEVER STORED.

A stored `track` field would be a cache, and a cache nothing re-validates
eventually disagrees with reality — flip one toggle six weeks from now and the
dashboard would still claim "YRVI-SL" while the box runs something else. Here,
`resolve_track()` is a pure function of settings.json, so changing any pinned
setting drops the badge to Custom on its own. No migration, no sync, no drift.

Selecting a track in the UI is just a bulk write of that track's `pins`; the
badge is recomputed from what actually landed on disk.
"""

# `pins` are the settings that DEFINE the track. A key that isn't pinned is
# explicitly "don't care" — see YRVI-CSP below, where that distinction is
# load-bearing rather than cosmetic.
TRACKS = [
    {
        "id":    "YRVI-26",
        "name":  "Ride the Wave",
        "emoji": "🌊",
        "short": "Wheel, hold through drawdowns",
        "description": (
            "The original wheel. After an assignment the fund writes covered "
            "calls and keeps the shares through a drawdown, exiting only on "
            "call-away or a screener drop. Highest return in a recovering "
            "market, deepest drawdowns."
        ),
        "pins": {
            "csp_only_mode":           False,
            "wheel_stop_loss_enabled": False,
        },
    },
    {
        "id":    "YRVI-SL",
        "name":  "Stop Loss",
        "emoji": "🛑",
        "short": "Wheel + 10% stop",
        "description": (
            "The wheel, plus a 10% stop loss on wheel holdings checked at the "
            "weekly Monday run. Lower drawdown and volatility than Ride the "
            "Wave; gives up the snap-back when a sold-out name recovers."
        ),
        # 10% is part of this track's IDENTITY, not a free parameter: a 15%
        # stop was tested and rejected (it held into deeper losses without
        # buying back the recovery edge), and the tracked YRVI-SL fund is
        # specifically the 10% arm. A box at any other percentage is genuinely
        # not running this track and its numbers aren't comparable — so it
        # reads Custom, which is the honest answer.
        "pins": {
            "csp_only_mode":           False,
            "wheel_stop_loss_enabled": True,
            "stop_loss_pct":           0.10,
        },
    },
    {
        "id":    "YRVI-CSP",
        "name":  "CSP Only",
        "emoji": "🎯",
        "short": "No wheel — dump assignments Monday",
        "description": (
            "Cash-secured puts only. Assigned shares are sold at market at the "
            "Monday wheel check and the proceeds go straight back into that "
            "morning's CSP budget — no covered calls. Never holds equity "
            "through a decline, so the shallowest drawdowns; misses the "
            "recovery when an assigned name snaps back."
        ),
        # Deliberately does NOT pin the stop loss. `csp_only_mode` short-
        # circuits at Step 0b of run_wheel_check and skips every remaining
        # step, so wheel_stop_loss_enabled is unreachable rather than wrong —
        # its value cannot affect behavior. Pinning it would make a real
        # CSP-only box read "Custom" purely because a dead toggle was left on
        # (exactly the state of the dev box on 2026-08-22).
        "pins": {
            "csp_only_mode": True,
        },
    },
]

CUSTOM_TRACK = {
    "id":    "YRVI-Custom",
    "name":  "Custom",
    "emoji": "🔧",
    "short": "Your own combination",
    "description": (
        "The current settings don't match any named track. Nothing is wrong — "
        "results just aren't comparable to the tracked funds."
    ),
    "pins": {},
}

# stop_loss_pct is a float off a 0.01-step slider; compare with a tolerance so
# a value that is 0.1 for every human purpose never reads as Custom.
_FLOAT_TOL = 1e-9


def _matches(settings: dict, key: str, want) -> bool:
    # A MISSING key never matches. Absence is not evidence: if settings failed
    # to load, every boolean pin would read falsy and an empty dict would
    # resolve confidently to Ride the Wave. Requiring the key to be present
    # means a broken read degrades to Custom, which is the honest answer.
    if key not in settings:
        return False
    have = settings.get(key)
    if isinstance(want, bool):
        return bool(have) is want
    if isinstance(want, float):
        try:
            return abs(float(have) - want) < _FLOAT_TOL
        except (TypeError, ValueError):
            return False
    return have == want


def resolve_track(settings: dict) -> dict:
    """Return the track the given settings are actually running.

    Falls back to CUSTOM_TRACK when no track matches. The three named tracks
    are mutually exclusive by construction — YRVI-26 and YRVI-SL disagree on
    wheel_stop_loss_enabled, and both require csp_only_mode False where
    YRVI-CSP requires True — so at most one can ever match and the iteration
    order carries no hidden meaning.
    """
    settings = settings or {}
    for track in TRACKS:
        if all(_matches(settings, k, v) for k, v in track["pins"].items()):
            return track
    return CUSTOM_TRACK


def track_by_id(track_id: str):
    """Look up a named track by id. Returns None for unknown ids (including
    'YRVI-Custom', which is a RESULT of resolution, never something to apply —
    there are no settings that 'being custom' would write)."""
    for track in TRACKS:
        if track["id"] == track_id:
            return track
    return None


def all_tracks() -> list:
    """Every selectable track plus Custom, for the settings UI. Serving these
    from Python keeps ONE definition — duplicating them in JSX is how the
    badge would start lying again."""
    return TRACKS + [CUSTOM_TRACK]
