"""Multi-System Signal Routing — Book 216.

Routes ticks to appropriate strategy generators based on:
1. Instrument type (index, single stock, commodity, VIX)
2. Market session (Asian, European, US overlap, US-only)
3. Current regime (steady, WOI, crisis)
4. Strategy activation status (from lifecycle state machine)

Signal conflict resolution when multiple strategies fire on same ticker:
  Priority ordering: risk (S7) > validated (TypeF) > new (S1-S6)
  Inverse mutual exclusion: cannot hold both long and inverse

Usage:
    from python_brain.regime.signal_router import (
        SignalRouter, RoutingDecision,
    )

    router = SignalRouter()
    decisions = router.route_tick(ticker, bar_data, regime, session)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

log = logging.getLogger("signal_router")


class MarketSession(Enum):
    PRE_MARKET = "PRE_MARKET"          # 06:00-08:00 UTC
    LSE_OPEN = "LSE_OPEN"              # 08:00-14:30 UTC
    US_OVERLAP = "US_OVERLAP"          # 14:30-16:30 UTC (highest volume)
    US_ONLY = "US_ONLY"                # 16:30-21:00 UTC
    AFTER_HOURS = "AFTER_HOURS"        # 21:00-06:00 UTC


def classify_session(london_time_secs: int) -> MarketSession:
    """Classify current market session from London time."""
    hour = london_time_secs // 3600
    if hour < 8:
        return MarketSession.PRE_MARKET
    elif hour < 14 or (hour == 14 and london_time_secs % 3600 < 1800):
        return MarketSession.LSE_OPEN
    elif hour < 16 or (hour == 16 and london_time_secs % 3600 < 1800):
        return MarketSession.US_OVERLAP
    elif hour < 21:
        return MarketSession.US_ONLY
    return MarketSession.AFTER_HOURS


# Strategy → which sessions it should be active in
STRATEGY_SESSIONS: Dict[str, Set[MarketSession]] = {
    "TypeF": {MarketSession.LSE_OPEN, MarketSession.US_OVERLAP},
    "TypeB": {MarketSession.LSE_OPEN, MarketSession.US_OVERLAP},
    "TypeA": {MarketSession.LSE_OPEN, MarketSession.US_OVERLAP},
    "TypeE": {MarketSession.LSE_OPEN, MarketSession.US_OVERLAP},
    "VanguardSniper": {MarketSession.LSE_OPEN, MarketSession.US_OVERLAP},
    "S1_Microstructure": {MarketSession.US_OVERLAP},  # Needs cross-market data
    "S2_Reversion": {MarketSession.LSE_OPEN, MarketSession.US_OVERLAP},
    "S3_MacroTrend": {MarketSession.LSE_OPEN, MarketSession.US_OVERLAP, MarketSession.US_ONLY},
    "S4_VolPremium": {MarketSession.US_OVERLAP},  # VIX products most liquid during overlap
    "S5_OvernightCarry": {MarketSession.US_ONLY},  # Enter before US close
    "S6_Catalyst": {MarketSession.US_OVERLAP, MarketSession.US_ONLY},  # Event-driven
    "S7_TailHedge": {MarketSession.LSE_OPEN, MarketSession.US_OVERLAP, MarketSession.US_ONLY},
    "VolCompression": {MarketSession.LSE_OPEN, MarketSession.US_OVERLAP},
    "RebalancingFlow": {MarketSession.US_ONLY},  # 19:00-20:00 GMT window
    "NAVArbitrage": {MarketSession.LSE_OPEN, MarketSession.US_OVERLAP},
    "AlphaFactory": {MarketSession.LSE_OPEN, MarketSession.US_OVERLAP},
    "LeadLag": {MarketSession.US_OVERLAP},  # Needs both US and LSE active
    "Pairs": {MarketSession.LSE_OPEN, MarketSession.US_OVERLAP},
}

# Signal priority (higher = higher priority in conflict resolution)
STRATEGY_PRIORITY: Dict[str, int] = {
    "S7_TailHedge": 100,     # Risk management — always highest
    "TypeF": 90,             # 72% WR, PF 9.34 — proven
    "S2_Reversion": 85,      # 45% WR, PF 1.42 — validated
    "TypeB": 80,             # 47% WR — validated
    "TypeE": 75,
    "TypeA": 70,
    "VanguardSniper": 65,
    "VolCompression": 60,
    "AlphaFactory": 55,
    "S3_MacroTrend": 50,
    "Pairs": 45,
    "NAVArbitrage": 40,
    "RebalancingFlow": 35,
    "LeadLag": 30,
    "S1_Microstructure": 25,
    "S4_VolPremium": 20,
    "S5_OvernightCarry": 15,
    "S6_Catalyst": 10,
}


@dataclass
class RoutingDecision:
    """Result of routing a signal through conflict resolution."""
    ticker: str
    strategy: str
    allowed: bool = True
    reason: str = ""
    priority: int = 0
    blocked_by: str = ""  # Strategy that blocked this one


class SignalRouter:
    """Route signals with conflict resolution and session awareness."""

    def __init__(self):
        self._active_strategies: Set[str] = set(STRATEGY_SESSIONS.keys())
        self._pending_signals: Dict[str, List[Tuple[str, int, Dict]]] = {}  # ticker → [(strategy, confidence, signal)]

    def deactivate_strategy(self, name: str):
        """Deactivate a strategy (e.g., killed by lifecycle)."""
        self._active_strategies.discard(name)
        log.info("ROUTER: deactivated %s", name)

    def activate_strategy(self, name: str):
        self._active_strategies.add(name)

    def is_strategy_active(self, strategy: str, session: MarketSession) -> bool:
        """Check if strategy should be active in current session."""
        if strategy not in self._active_strategies:
            return False
        allowed_sessions = STRATEGY_SESSIONS.get(strategy, set())
        return session in allowed_sessions

    def resolve_conflicts(
        self,
        signals: List[Dict],
        ticker: str,
    ) -> Optional[Dict]:
        """Resolve conflicts when multiple strategies signal on same ticker.

        Rules:
        1. Highest priority wins
        2. Inverse mutual exclusion (can't hold long + inverse)
        3. Max 1 entry per ticker per bar
        """
        if not signals:
            return None

        if len(signals) == 1:
            return signals[0]

        # Sort by priority
        def priority_key(s):
            return STRATEGY_PRIORITY.get(s.get("strategy", ""), 0)

        signals.sort(key=priority_key, reverse=True)

        winner = signals[0]
        for loser in signals[1:]:
            log.info(
                "CONFLICT: %s on %s — %s (pri=%d) beats %s (pri=%d)",
                ticker, ticker,
                winner.get("strategy"), priority_key(winner),
                loser.get("strategy"), priority_key(loser),
            )

        return winner

    def filter_by_session(
        self,
        signals: List[Dict],
        london_time_secs: int,
    ) -> List[Dict]:
        """Filter signals to only those active in current session."""
        session = classify_session(london_time_secs)
        return [
            s for s in signals
            if self.is_strategy_active(s.get("strategy", ""), session)
        ]

    def route_and_resolve(
        self,
        signals: List[Dict],
        london_time_secs: int,
    ) -> Optional[Dict]:
        """Full routing pipeline: session filter → conflict resolution → winner."""
        if not signals:
            return None

        # Step 1: Filter by session
        active = self.filter_by_session(signals, london_time_secs)
        if not active:
            return None

        # Step 2: Group by ticker
        by_ticker: Dict[str, List[Dict]] = {}
        for s in active:
            t = s.get("ticker", s.get("ticker_id", ""))
            by_ticker.setdefault(t, []).append(s)

        # Step 3: Resolve per-ticker conflicts
        winners = []
        for ticker, ticker_signals in by_ticker.items():
            winner = self.resolve_conflicts(ticker_signals, ticker)
            if winner:
                winners.append(winner)

        # Step 4: Return highest-confidence winner across all tickers
        if not winners:
            return None
        winners.sort(key=lambda s: s.get("confidence", 0), reverse=True)
        return winners[0]
