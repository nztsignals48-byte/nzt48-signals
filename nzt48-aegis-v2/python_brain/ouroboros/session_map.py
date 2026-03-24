"""Session Map — 10 named trading sessions across the 22-hour operating day.

Defines the canonical session schedule for AEGIS V2's multi-exchange universe.
Each session specifies which exchanges are eligible, how to allocate IBKR streaming
slots, liquidity minimums, and shadow (pre-market) candidates for the next session.

Sessions cover UTC 23:00 → 21:00 (22 hours). The 21:00-23:00 UTC window is DARK
(no trading, used for maintenance, nightly jobs, and Ouroboros analysis).

Usage:
    from python_brain.ouroboros.session_map import detect_session, SESSION_MAP

    session = detect_session(14, 30)  # 14:30 UTC
    print(session.name)               # "TRANSATLANTIC"
    print(session.eligible_exchanges) # ["LSE", "LSEETF", "XETRA", "EURONEXT", "SMART"]

CLI:
    python3 -m python_brain.ouroboros.session_map --test
    python3 -m python_brain.ouroboros.session_map --current
    python3 -m python_brain.ouroboros.session_map --dump-json
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger("session_map")

# ---------------------------------------------------------------------------
# Session Definition
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SessionDefinition:
    """A named trading session within the 22-hour operating day."""

    name: str
    start_hour: int          # UTC hour (0-23)
    start_minute: int        # UTC minute (0-59)
    end_hour: int            # UTC hour (0-23), wraps at midnight
    end_minute: int          # UTC minute (0-59)
    eligible_exchanges: Tuple[str, ...]
    slot_allocation: Dict[str, int]  # exchange → number of IBKR slots
    shadow_exchanges: Tuple[str, ...]  # upcoming exchanges for pre-market slots
    shadow_slots: int        # slots reserved for shadow/pre-market
    liquidity_minimum_usd: float  # daily notional floor
    spread_ceiling_bps: float     # max spread in basis points
    leveraged_allowed: bool       # whether leveraged ETPs can be ACTIVE
    description: str = ""

    @property
    def total_active_slots(self) -> int:
        return sum(self.slot_allocation.values())

    @property
    def start_utc(self) -> str:
        return f"{self.start_hour:02d}:{self.start_minute:02d}"

    @property
    def end_utc(self) -> str:
        return f"{self.end_hour:02d}:{self.end_minute:02d}"

    def contains_time(self, hour: int, minute: int) -> bool:
        """Check if a UTC time falls within this session.

        Handles midnight wrap (e.g., ASIA_PRE: 23:00-00:00).
        """
        t = hour * 60 + minute
        s = self.start_hour * 60 + self.start_minute
        e = self.end_hour * 60 + self.end_minute

        if s < e:
            # Normal range (no midnight wrap)
            return s <= t < e
        else:
            # Wraps midnight (e.g., 23:00 → 00:00)
            return t >= s or t < e

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "start_utc": self.start_utc,
            "end_utc": self.end_utc,
            "eligible_exchanges": list(self.eligible_exchanges),
            "slot_allocation": dict(self.slot_allocation),
            "shadow_exchanges": list(self.shadow_exchanges),
            "shadow_slots": self.shadow_slots,
            "total_active_slots": self.total_active_slots,
            "liquidity_minimum_usd": self.liquidity_minimum_usd,
            "spread_ceiling_bps": self.spread_ceiling_bps,
            "leveraged_allowed": self.leveraged_allowed,
            "description": self.description,
        }


# ---------------------------------------------------------------------------
# 10 Named Sessions (canonical, covering 22 hours)
# ---------------------------------------------------------------------------
# Slot allocations target 100 total (engine hard cap at line 2682 of engine.rs).
# Shadow slots come from the same 100 pool but are lower-priority pre-market.

SESSION_MAP: Dict[str, SessionDefinition] = {}

def _s(name, sh, sm, eh, em, exchanges, slots, shadow_ex, shadow_n,
       liq, spread, lev, desc=""):
    """Helper to build and register a SessionDefinition."""
    sd = SessionDefinition(
        name=name,
        start_hour=sh, start_minute=sm,
        end_hour=eh, end_minute=em,
        eligible_exchanges=tuple(exchanges),
        slot_allocation=slots,
        shadow_exchanges=tuple(shadow_ex),
        shadow_slots=shadow_n,
        liquidity_minimum_usd=liq,
        spread_ceiling_bps=spread,
        leveraged_allowed=lev,
        description=desc,
    )
    SESSION_MAP[name] = sd
    return sd

# ── ASIA_PRE: 23:00-00:00 UTC ──
# TSE and HKEX pre-market. Thin liquidity, mostly position monitoring.
_s("ASIA_PRE", 23, 0, 0, 0,
   exchanges=["TSE", "HKEX"],
   slots={"TSE": 50, "HKEX": 30},
   shadow_ex=["SGX", "KRX"],
   shadow_n=20,
   liq=100_000, spread=50.0, lev=False,
   desc="Asia pre-market, TSE/HKEX warming up")

# ── ASIA_EARLY: 00:00-02:00 UTC ──
# TSE, HKEX open. ASX early session (10:00 AEST in winter).
_s("ASIA_EARLY", 0, 0, 2, 0,
   exchanges=["TSE", "HKEX", "ASX"],
   slots={"TSE": 40, "HKEX": 30, "ASX": 15},
   shadow_ex=["SGX", "KRX"],
   shadow_n=15,
   liq=150_000, spread=40.0, lev=False,
   desc="TSE/HKEX/ASX early session")

# ── ASIA_CORE: 02:00-06:00 UTC ──
# Full Asia: TSE (09:00-11:30 JST morning), HKEX, SGX, KRX all open.
_s("ASIA_CORE", 2, 0, 6, 0,
   exchanges=["TSE", "HKEX", "SGX", "KRX"],
   slots={"TSE": 35, "HKEX": 30, "SGX": 15, "KRX": 10},
   shadow_ex=["LSE", "XETRA"],
   shadow_n=10,
   liq=200_000, spread=30.0, lev=False,
   desc="Full Asia session — TSE/HKEX/SGX/KRX")

# ── ASIA_LATE: 06:00-08:00 UTC ──
# Asia closing (TSE afternoon 14:00-15:00 JST). Europe pre-market warming.
_s("ASIA_LATE", 6, 0, 8, 0,
   exchanges=["TSE", "HKEX"],
   slots={"TSE": 30, "HKEX": 20},
   shadow_ex=["LSE", "XETRA", "EURONEXT"],
   shadow_n=50,
   liq=150_000, spread=35.0, lev=False,
   desc="Asia closing, Europe pre-market")

# ── EUROPE_OPEN: 08:00-10:00 UTC ──
# LSE, XETRA, Euronext open. High volatility window (morning auction spillover).
_s("EUROPE_OPEN", 8, 0, 10, 0,
   exchanges=["LSE", "LSEETF", "XETRA", "EURONEXT"],
   slots={"LSE": 30, "LSEETF": 10, "XETRA": 20, "EURONEXT": 15},
   shadow_ex=["SMART"],
   shadow_n=25,
   liq=200_000, spread=30.0, lev=True,
   desc="Europe opening — LSE/XETRA/Euronext")

# ── EUROPE_CORE: 10:00-13:00 UTC ──
# Europe full session. Liquid, stable spreads.
_s("EUROPE_CORE", 10, 0, 13, 0,
   exchanges=["LSE", "LSEETF", "XETRA", "EURONEXT"],
   slots={"LSE": 30, "LSEETF": 10, "XETRA": 20, "EURONEXT": 15},
   shadow_ex=["SMART"],
   shadow_n=25,
   liq=200_000, spread=25.0, lev=True,
   desc="Europe core session")

# ── TRANSATLANTIC: 13:00-16:30 UTC ──
# US + Europe overlap. Highest global liquidity window.
_s("TRANSATLANTIC", 13, 0, 16, 30,
   exchanges=["LSE", "LSEETF", "XETRA", "EURONEXT", "SMART"],
   slots={"SMART": 45, "LSE": 20, "LSEETF": 10, "XETRA": 10, "EURONEXT": 5},
   shadow_ex=[],
   shadow_n=10,
   liq=300_000, spread=20.0, lev=True,
   desc="US+Europe overlap — peak global liquidity")

# ── US_CORE: 16:30-19:00 UTC ──
# US only (Europe closed after 16:30). High liquidity.
_s("US_CORE", 16, 30, 19, 0,
   exchanges=["SMART"],
   slots={"SMART": 90},
   shadow_ex=["LSE"],
   shadow_n=10,
   liq=300_000, spread=20.0, lev=True,
   desc="US core session — Europe closed")

# ── US_LATE: 19:00-21:00 UTC ──
# US power hour and close. Volume spikes at close.
_s("US_LATE", 19, 0, 21, 0,
   exchanges=["SMART"],
   slots={"SMART": 90},
   shadow_ex=["TSE", "HKEX"],
   shadow_n=10,
   liq=200_000, spread=25.0, lev=True,
   desc="US late session — power hour and close")

# ── DARK: 21:00-23:00 UTC ──
# No trading. Maintenance, nightly analysis, Ouroboros.
_s("DARK", 21, 0, 23, 0,
   exchanges=[],
   slots={},
   shadow_ex=["TSE", "HKEX"],
   shadow_n=100,
   liq=0, spread=999.0, lev=False,
   desc="Dark window — no trading, maintenance")


# Ordered list for iteration (chronological from 23:00 UTC)
SESSION_ORDER = [
    "ASIA_PRE", "ASIA_EARLY", "ASIA_CORE", "ASIA_LATE",
    "EUROPE_OPEN", "EUROPE_CORE", "TRANSATLANTIC",
    "US_CORE", "US_LATE", "DARK",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_session(hour: int, minute: int = 0) -> SessionDefinition:
    """Detect the current trading session from UTC hour and minute.

    Returns the matching SessionDefinition. If no session matches
    (should not happen with 24h coverage), returns DARK as fallback.
    """
    for name in SESSION_ORDER:
        session = SESSION_MAP[name]
        if session.contains_time(hour, minute):
            return session
    # Fallback (should never happen — sessions cover full 24h)
    return SESSION_MAP["DARK"]


def detect_current_session() -> SessionDefinition:
    """Detect session from the current UTC time."""
    now = datetime.now(timezone.utc)
    return detect_session(now.hour, now.minute)


def get_next_session(current_name: str) -> SessionDefinition:
    """Return the next session after the named one."""
    try:
        idx = SESSION_ORDER.index(current_name)
        next_idx = (idx + 1) % len(SESSION_ORDER)
        return SESSION_MAP[SESSION_ORDER[next_idx]]
    except ValueError:
        return SESSION_MAP["DARK"]


def get_all_exchanges_for_session(session: SessionDefinition) -> Set[str]:
    """Get all exchanges eligible for a session (active + shadow)."""
    return set(session.eligible_exchanges) | set(session.shadow_exchanges)


def get_slot_allocation(session: SessionDefinition, total_slots: int = 100) -> Dict[str, int]:
    """Get the slot allocation for a session, scaled to total_slots.

    The canonical allocations in SESSION_MAP target 100 slots.
    This function scales them if a different total is needed.
    """
    canonical_total = session.total_active_slots + session.shadow_slots
    if canonical_total == 0:
        return {}

    scale = total_slots / max(canonical_total, 1)
    result = {}
    for exch, n in session.slot_allocation.items():
        result[exch] = max(1, int(n * scale))

    # Shadow gets whatever remains
    active_sum = sum(result.values())
    result["_shadow"] = max(0, total_slots - active_sum)
    return result


def session_map_to_dict() -> Dict[str, Any]:
    """Export the full session map as a JSON-serializable dict."""
    now = datetime.now(timezone.utc)
    current = detect_session(now.hour, now.minute)
    return {
        "generated": now.isoformat(),
        "current_session": current.name,
        "current_utc": f"{now.hour:02d}:{now.minute:02d}",
        "total_sessions": len(SESSION_MAP),
        "sessions": {name: SESSION_MAP[name].to_dict() for name in SESSION_ORDER},
    }


# ---------------------------------------------------------------------------
# Exchange → Session reverse lookup
# ---------------------------------------------------------------------------

# Pre-computed: which sessions is each exchange active in?
_EXCHANGE_SESSIONS: Dict[str, List[str]] = {}

def _build_exchange_sessions():
    for name in SESSION_ORDER:
        s = SESSION_MAP[name]
        for exch in s.eligible_exchanges:
            _EXCHANGE_SESSIONS.setdefault(exch, []).append(name)

_build_exchange_sessions()


def get_sessions_for_exchange(exchange: str) -> List[str]:
    """Get all session names where an exchange is eligible."""
    return _EXCHANGE_SESSIONS.get(exchange, [])


# ---------------------------------------------------------------------------
# Exchange name normalization
# ---------------------------------------------------------------------------
# The master universe uses source names (NYSE, NASDAQ, EURONEXT_PA, etc.)
# The session map uses IBKR routing names (SMART, EURONEXT, etc.)
# This mapping bridges the gap.

_EXCHANGE_ALIASES: Dict[str, Set[str]] = {
    "SMART": {"NYSE", "NASDAQ", "AMEX", "ARCA", "SMART"},
    "LSE": {"LSE"},
    "LSEETF": {"LSEETF"},
    "EURONEXT": {"EURONEXT", "EURONEXT_PA", "EURONEXT_AS", "AEB"},
    "XETRA": {"XETRA"},
    "TSE": {"TSE"},
    "HKEX": {"HKEX"},
    "SGX": {"SGX"},
    "KRX": {"KRX"},
    "ASX": {"ASX"},
    "TSX": {"TSX"},
    "SIX": {"SIX"},
    "NZX": {"NZX", "XNZE"},
}

# Reverse lookup: source exchange → session-map exchange(s)
_SOURCE_TO_SESSION_EXCHANGE: Dict[str, List[str]] = {}

def _build_source_lookup():
    for session_exch, aliases in _EXCHANGE_ALIASES.items():
        for alias in aliases:
            _SOURCE_TO_SESSION_EXCHANGE.setdefault(alias, []).append(session_exch)

_build_source_lookup()


def normalize_exchange(source_exchange: str) -> List[str]:
    """Map a source exchange name to session-map exchange name(s).

    Examples:
        normalize_exchange("NYSE") → ["SMART"]
        normalize_exchange("NASDAQ") → ["SMART"]
        normalize_exchange("EURONEXT_PA") → ["EURONEXT"]
        normalize_exchange("LSE") → ["LSE"]
        normalize_exchange("UNKNOWN") → ["UNKNOWN"]
    """
    return _SOURCE_TO_SESSION_EXCHANGE.get(source_exchange, [source_exchange])


def ticker_matches_session(ticker_exchange: str, session: SessionDefinition) -> bool:
    """Check if a ticker's exchange matches the session's eligible/shadow exchanges.

    Handles exchange name aliasing (NYSE→SMART, EURONEXT_PA→EURONEXT, etc.).
    """
    session_exchanges = set(session.eligible_exchanges) | set(session.shadow_exchanges)
    normalized = normalize_exchange(ticker_exchange)
    return bool(set(normalized) & session_exchanges)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _run_test():
    """Verify session map covers 24 hours and detect_session works."""
    print("=== Session Map Test Suite ===\n")

    # Test 1: All 24 hours are covered
    print("Test 1: 24-hour coverage")
    for h in range(24):
        for m in (0, 30):
            s = detect_session(h, m)
            print(f"  {h:02d}:{m:02d} UTC → {s.name:20s} "
                  f"(exchanges={','.join(s.eligible_exchanges) or 'none':30s} "
                  f"slots={s.total_active_slots})")
    print()

    # Test 2: Session transitions
    print("Test 2: Session order and transitions")
    for i, name in enumerate(SESSION_ORDER):
        nxt = get_next_session(name)
        s = SESSION_MAP[name]
        print(f"  {name:20s} {s.start_utc}-{s.end_utc} → next: {nxt.name}")
    print()

    # Test 3: Exchange sessions
    print("Test 3: Exchange → Sessions")
    for exch in sorted(_EXCHANGE_SESSIONS.keys()):
        sessions = get_sessions_for_exchange(exch)
        print(f"  {exch:12s} active in: {', '.join(sessions)}")
    print()

    # Test 4: Slot allocation totals
    print("Test 4: Slot allocations (target 100)")
    for name in SESSION_ORDER:
        s = SESSION_MAP[name]
        total = s.total_active_slots + s.shadow_slots
        print(f"  {name:20s} active={s.total_active_slots:3d} shadow={s.shadow_slots:3d} total={total:3d}")
    print()

    # Test 5: Current session
    current = detect_current_session()
    now = datetime.now(timezone.utc)
    print(f"Test 5: Current session at {now.strftime('%H:%M UTC')}: {current.name}")
    print(f"  Exchanges: {', '.join(current.eligible_exchanges) or 'none'}")
    print(f"  Slots: {current.slot_allocation}")
    print(f"  Shadow: {', '.join(current.shadow_exchanges) or 'none'} ({current.shadow_slots} slots)")
    print()

    print("=== All tests passed ===")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="AEGIS V2 Session Map")
    parser.add_argument("--test", action="store_true", help="Run test suite")
    parser.add_argument("--current", action="store_true", help="Show current session")
    parser.add_argument("--dump-json", action="store_true", help="Dump session map as JSON")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [SessionMap] %(levelname)s %(message)s")

    if args.test:
        _run_test()
    elif args.dump_json:
        print(json.dumps(session_map_to_dict(), indent=2))
    elif args.current:
        s = detect_current_session()
        now = datetime.now(timezone.utc)
        print(f"UTC: {now.strftime('%H:%M')}")
        print(f"Session: {s.name}")
        print(f"Exchanges: {', '.join(s.eligible_exchanges) or 'none'}")
        print(f"Slots: {json.dumps(s.slot_allocation)}")
        print(f"Shadow: {', '.join(s.shadow_exchanges) or 'none'} ({s.shadow_slots})")
        print(f"Liquidity min: ${s.liquidity_minimum_usd:,.0f}")
        print(f"Spread ceiling: {s.spread_ceiling_bps} bps")
        print(f"Leveraged: {'yes' if s.leveraged_allowed else 'no'}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
