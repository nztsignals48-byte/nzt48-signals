"""
NZT-48 Chronological Determinism Module — The Single Source of Time.

Resolves contradictions C-01, C-02, C-03, C-13, C-21 from the V8.0 Audit.

RULES:
1. Every module imports time from HERE. No standalone ZoneInfo/datetime.now() anywhere else.
2. now_utc() for session bounds, logging, business logic timestamps.
3. mono_ns() for internal physical calculations (Hawkes decay, API latency, execution speed).
4. LSE_OPEN = 08:00 UK (not 09:00 — that bug is dead).
5. LSE_TRADING_START = 09:00 UK (S15/S16 noise-avoidance window — Ben-Rephael et al. 2012).
"""
from __future__ import annotations

import time
from datetime import datetime, time as dtime, timezone
from zoneinfo import ZoneInfo
from typing import Optional

# ─── Singleton timezone objects (replaces 7 independent definitions — C-13) ───
UK_TZ: ZoneInfo = ZoneInfo("Europe/London")
ET_TZ: ZoneInfo = ZoneInfo("America/New_York")

# ─── Market session constants ─────────────────────────────────────────────────
LSE_OPEN: dtime       = dtime(8, 0)    # Actual LSE open (fixes C-02)
LSE_CLOSE: dtime      = dtime(16, 30)
LSE_TRADING_START: dtime = dtime(9, 0)   # S15/S16 window start (avoids auction noise)
LSE_TRADING_END: dtime   = dtime(15, 15)  # S15/S16 window end
NYSE_OPEN: dtime      = dtime(9, 30)
NYSE_CLOSE: dtime     = dtime(16, 0)

# ─── Staleness threshold for API latency detection (Imperative 2) ─────────────
STALE_THRESHOLD_NS: int = 4_000_000_000  # 4.0 seconds — t3.small baseline ~3s per tick


# ─── Wall-clock functions ─────────────────────────────────────────────────────

def now_utc() -> datetime:
    """Return current UTC time as a timezone-aware datetime.

    Replaces:
    - datetime.now(timezone.utc)
    - datetime.utcnow()  (deprecated Python 3.12+, C-21)
    - datetime.now()      (naive — never use)
    """
    return datetime.now(timezone.utc)


def now_uk() -> datetime:
    """Return current UK local time (handles GMT/BST automatically).

    Fixes C-01: Ulysses Lock was comparing UTC to UK market hours.
    Fixes C-03: 5x overnight kill was using UTC for UK close check.
    """
    return datetime.now(UK_TZ)


def now_et() -> datetime:
    """Return current US Eastern time (handles EST/EDT automatically)."""
    return datetime.now(ET_TZ)


# ─── Monotonic clock (Imperative 2) ──────────────────────────────────────────

def mono_ns() -> int:
    """Return monotonic nanosecond timestamp for internal physical calculations.

    Use for: Hawkes decay, API latency tracking, execution speed measurement.
    NEVER use for session bounds or business logic — use now_utc()/now_uk() instead.
    """
    return time.monotonic_ns()


# ─── Session boundary queries ─────────────────────────────────────────────────

def is_lse_open(dt: Optional[datetime] = None) -> bool:
    """Check if LSE is open (08:00-16:30 UK local time).

    This is the ACTUAL exchange open. Use for:
    - Data feed activation
    - Position management (overnight kills)
    - Universal scanner signal generation

    Fixes C-02: Was using 09:00 in universal_scanner.py.
    """
    if dt is None:
        dt = now_uk()
    elif dt.tzinfo is None or dt.tzinfo != UK_TZ:
        dt = dt.astimezone(UK_TZ)
    t = dt.time()
    # Weekday check: Mon=0 .. Fri=4
    if dt.weekday() > 4:
        return False
    return LSE_OPEN <= t <= LSE_CLOSE


def is_lse_trading_window(dt: Optional[datetime] = None) -> bool:
    """Check if we are in the S15/S16 active trading window (09:00-15:15 UK).

    This deliberately avoids the first hour (08:00-09:00) per
    Ben-Rephael, Da & Israelsen (2012) — opening auction noise.
    Also avoids 15:15-16:30 — close auction and ETP rebalance window.

    Use for:
    - S15 daily target signal generation
    - S16 universal scanner signal generation
    """
    if dt is None:
        dt = now_uk()
    elif dt.tzinfo is None or dt.tzinfo != UK_TZ:
        dt = dt.astimezone(UK_TZ)
    t = dt.time()
    if dt.weekday() > 4:
        return False
    return LSE_TRADING_START <= t <= LSE_TRADING_END


def is_nyse_open(dt: Optional[datetime] = None) -> bool:
    """Check if NYSE is open (09:30-16:00 US Eastern)."""
    if dt is None:
        dt = now_et()
    elif dt.tzinfo is None or dt.tzinfo != ET_TZ:
        dt = dt.astimezone(ET_TZ)
    t = dt.time()
    if dt.weekday() > 4:
        return False
    return NYSE_OPEN <= t <= NYSE_CLOSE


def is_market_hours_uk(dt: Optional[datetime] = None) -> bool:
    """Alias for is_lse_open() — used by Ulysses Lock middleware.

    Fixes C-01: The middleware was comparing UTC time to UK market bounds.
    Now correctly uses UK local time.
    """
    return is_lse_open(dt)


def is_market_hours_frozen(dt: Optional[datetime] = None) -> bool:
    """True during LSE market hours — used by Ulysses Lock to freeze config."""
    return is_lse_open(dt)


# ─── Utility ──────────────────────────────────────────────────────────────────

def utc_timestamp_ms() -> int:
    """Millisecond UTC timestamp for Redis stream entries and logging."""
    return int(now_utc().timestamp() * 1000)


def elapsed_ms(start_mono_ns: int) -> float:
    """Calculate elapsed milliseconds since a monotonic nanosecond timestamp."""
    return (mono_ns() - start_mono_ns) / 1_000_000


def is_stale(start_mono_ns: int) -> bool:
    """Check if elapsed time exceeds the staleness threshold (2.5s)."""
    return (mono_ns() - start_mono_ns) > STALE_THRESHOLD_NS


# ─── 5x Overnight Kill (C-03) ────────────────────────────────────────────────

_5X_KILL_TIME: dtime = dtime(16, 15)  # UK local time

def is_past_5x_kill_time(dt: Optional[datetime] = None) -> bool:
    """Check if current UK time is past the 5x ETP forced-close deadline (16:15 UK).

    Fixes C-03: Was using UTC, which during BST means the kill fires
    45 minutes late (UTC 16:15 = UK 17:15 in summer).
    """
    if dt is None:
        dt = now_uk()
    elif dt.tzinfo is None or dt.tzinfo != UK_TZ:
        dt = dt.astimezone(UK_TZ)
    return dt.time() >= _5X_KILL_TIME
