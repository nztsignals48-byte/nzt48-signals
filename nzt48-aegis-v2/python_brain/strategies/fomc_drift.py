"""Book 24: FOMC/CPI/NFP drift capture — event-specific entry delays and windows.

Two functions:
1. get_event_blackout(ts_ns) — graduated blackout near events (hard/soft zones)
2. get_drift_signal(event_type, mins_since, bias) — post-event drift capture

Consumed by:
- bridge.py _check_quality_gates() → blackout (hard gate)
- bridge.py _generate_signals() → drift signal (new signal generator "EventDrift")

Event calendar loaded from /app/data/event_calendar.json (written by nightly or manual).
"""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional

_CALENDAR_PATH = "/app/data/event_calendar.json"

# Event definitions: (pre_blackout_mins, post_drift_start_mins, post_drift_end_mins, impact_tier)
_EVENT_PROFILES = {
    "FOMC": {"pre_block": 30, "drift_start": 15, "drift_end": 45, "tier": 1, "bias": "drift"},
    "CPI": {"pre_block": 5, "drift_start": 5, "drift_end": 30, "tier": 1, "bias": "drift"},
    "NFP": {"pre_block": 5, "drift_start": 10, "drift_end": 60, "tier": 1, "bias": "drift"},
    "BOE_RATE": {"pre_block": 15, "drift_start": 10, "drift_end": 30, "tier": 1, "bias": "drift"},
    "ECB_RATE": {"pre_block": 15, "drift_start": 10, "drift_end": 30, "tier": 1, "bias": "drift"},
    "GDP": {"pre_block": 3, "drift_start": 5, "drift_end": 20, "tier": 2, "bias": "drift"},
    "RETAIL_SALES": {"pre_block": 3, "drift_start": 5, "drift_end": 15, "tier": 2, "bias": "fade"},
    "PMI": {"pre_block": 2, "drift_start": 3, "drift_end": 15, "tier": 2, "bias": "drift"},
}


@dataclass
class EventBlackout:
    """Blackout context for an upcoming/recent event."""
    event_name: str
    event_type: str
    minutes_to: float  # Negative = past
    hard_block: bool   # True = reject entries
    in_drift_window: bool
    direction_bias: str  # "drift" or "fade"
    sizing_scale: float  # 0.0-1.0, how much to reduce sizing
    tier: int


@dataclass
class DriftSignal:
    """Post-event drift capture signal."""
    confidence: float
    direction: str  # "Long" or "Short"
    event_type: str
    minutes_since: float
    expected_duration_mins: float


_calendar_cache = None
_calendar_mtime = 0


def _load_calendar():
    """Load event calendar from disk."""
    global _calendar_cache, _calendar_mtime
    if not os.path.exists(_CALENDAR_PATH):
        return []
    try:
        mtime = os.path.getmtime(_CALENDAR_PATH)
        if _calendar_cache is not None and mtime == _calendar_mtime:
            return _calendar_cache
        with open(_CALENDAR_PATH) as f:
            data = json.load(f)
        _calendar_cache = data.get("events", [])
        _calendar_mtime = mtime
        return _calendar_cache
    except Exception:
        return []


def get_event_blackout(ts_ns):
    """Check if current time is near a macro event.

    Returns EventBlackout if within blackout/drift zone, None otherwise.
    """
    if ts_ns <= 0:
        return None

    now_s = ts_ns / 1e9
    events = _load_calendar()

    for evt in events:
        evt_ts = evt.get("timestamp_utc", 0)
        evt_type = evt.get("type", "")
        if evt_type not in _EVENT_PROFILES:
            continue

        profile = _EVENT_PROFILES[evt_type]
        mins_to = (evt_ts - now_s) / 60.0  # Positive = future, negative = past

        # Pre-event blackout
        if 0 < mins_to <= profile["pre_block"]:
            return EventBlackout(
                event_name=evt.get("name", evt_type),
                event_type=evt_type,
                minutes_to=mins_to,
                hard_block=True,
                in_drift_window=False,
                direction_bias="",
                sizing_scale=0.0,
                tier=profile["tier"],
            )

        # Soft zone: 30-60 min before tier 1 events
        if profile["tier"] == 1 and profile["pre_block"] < mins_to <= 60:
            return EventBlackout(
                event_name=evt.get("name", evt_type),
                event_type=evt_type,
                minutes_to=mins_to,
                hard_block=False,
                in_drift_window=False,
                direction_bias="",
                sizing_scale=0.5,  # Half sizing near big events
                tier=profile["tier"],
            )

        # Post-event drift window
        if -profile["drift_end"] <= mins_to <= -profile["drift_start"]:
            return EventBlackout(
                event_name=evt.get("name", evt_type),
                event_type=evt_type,
                minutes_to=mins_to,
                hard_block=False,
                in_drift_window=True,
                direction_bias=profile["bias"],
                sizing_scale=1.0,  # Full sizing in drift window
                tier=profile["tier"],
            )

    return None


def get_drift_signal(event_type, minutes_since, direction_bias):
    """Generate a drift capture signal after an event release.

    Returns DriftSignal if conditions met, None otherwise.
    """
    if event_type not in _EVENT_PROFILES:
        return None

    profile = _EVENT_PROFILES[event_type]

    # Must be within drift window
    if minutes_since < profile["drift_start"] or minutes_since > profile["drift_end"]:
        return None

    # Confidence decays linearly from drift_start to drift_end
    progress = (minutes_since - profile["drift_start"]) / (profile["drift_end"] - profile["drift_start"])
    base_conf = 68.0 if profile["tier"] == 1 else 58.0
    conf = base_conf * (1.0 - 0.4 * progress)  # Decays 40% over window

    if conf < 50:
        return None

    direction = "Long"  # ISA: long-only. Drift direction from market reaction.

    return DriftSignal(
        confidence=round(conf, 1),
        direction=direction,
        event_type=event_type,
        minutes_since=round(minutes_since, 1),
        expected_duration_mins=round(profile["drift_end"] - minutes_since, 1),
    )
