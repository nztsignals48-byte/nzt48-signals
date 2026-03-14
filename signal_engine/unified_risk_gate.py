"""
signal_engine/unified_risk_gate.py
====================================
Shared risk budget tracker across both signal pipelines (main.py + engine.py).

Ensures:
  1. A signal vetoed in Pathway A cannot appear as TRADE in Pathway B
  2. Total portfolio heat doesn't exceed budget when both pathways emit
  3. Factor group concentration is enforced globally
  4. Max concurrent signals per ticker is enforced

Singleton pattern — both pathways consult the same instance.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("nzt48.unified_risk_gate")

# Budget limits
MAX_CONCURRENT_POSITIONS = 3
MAX_PORTFOLIO_HEAT_PCT = 6.0  # Total risk budget (% of equity)
MAX_FACTOR_GROUP_POSITIONS = 2
MAX_PER_TICKER = 1  # Only 1 position per ticker across all pathways


class UnifiedRiskGate:
    """Cross-pipeline risk enforcer. Both main.py and engine.py
    must call check() before emitting a signal as TRADE."""

    def __init__(self) -> None:
        self._active_tickers: dict[str, str] = {}  # ticker -> pathway
        self._active_groups: dict[str, int] = defaultdict(int)  # factor_group -> count
        self._vetoed: dict[str, str] = {}  # ticker -> veto_reason
        self._total_heat_pct: float = 0.0
        self._last_reset: str = ""

    def check(
        self,
        ticker: str,
        direction: str,
        factor_group: str,
        risk_pct: float,
        pathway: str = "UNKNOWN",
    ) -> tuple[bool, str]:
        """Check if a new signal is allowed under unified risk rules.

        Returns (allowed: bool, reason: str).
        """
        # 1. Vetoed ticker
        if ticker in self._vetoed:
            return False, f"VETOED: {self._vetoed[ticker]}"

        # 2. Already have a position in this ticker
        if ticker in self._active_tickers:
            existing_pathway = self._active_tickers[ticker]
            return False, f"DUPLICATE: {ticker} already active via {existing_pathway}"

        # 3. Max concurrent positions
        if len(self._active_tickers) >= MAX_CONCURRENT_POSITIONS:
            return False, f"MAX_POSITIONS: {len(self._active_tickers)}/{MAX_CONCURRENT_POSITIONS}"

        # 4. Factor group concentration
        if self._active_groups.get(factor_group, 0) >= MAX_FACTOR_GROUP_POSITIONS:
            return False, f"FACTOR_GROUP: {factor_group} at {self._active_groups[factor_group]}/{MAX_FACTOR_GROUP_POSITIONS}"

        # 5. Portfolio heat budget
        if self._total_heat_pct + risk_pct > MAX_PORTFOLIO_HEAT_PCT:
            return False, f"HEAT_BUDGET: {self._total_heat_pct:.1f}% + {risk_pct:.1f}% > {MAX_PORTFOLIO_HEAT_PCT}%"

        return True, "APPROVED"

    def register(
        self,
        ticker: str,
        factor_group: str,
        risk_pct: float,
        pathway: str = "UNKNOWN",
    ) -> None:
        """Register an approved signal as active."""
        self._active_tickers[ticker] = pathway
        self._active_groups[factor_group] += 1
        self._total_heat_pct += risk_pct
        logger.info(
            "RISK_GATE: registered %s via %s (heat=%.1f%%, positions=%d)",
            ticker, pathway, self._total_heat_pct, len(self._active_tickers),
        )

    def release(self, ticker: str, factor_group: str, risk_pct: float) -> None:
        """Release a closed position."""
        self._active_tickers.pop(ticker, None)
        if factor_group in self._active_groups:
            self._active_groups[factor_group] = max(0, self._active_groups[factor_group] - 1)
        self._total_heat_pct = max(0.0, self._total_heat_pct - risk_pct)

    def veto(self, ticker: str, reason: str) -> None:
        """Veto a ticker — prevents both pathways from trading it."""
        self._vetoed[ticker] = reason
        logger.warning("RISK_GATE: vetoed %s — %s", ticker, reason)

    def clear_veto(self, ticker: str) -> None:
        """Clear a veto (e.g., after daily reset)."""
        self._vetoed.pop(ticker, None)

    def daily_reset(self) -> None:
        """Clear vetoes and stale entries at start of new trading day."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._last_reset:
            self._vetoed.clear()
            self._last_reset = today
            logger.info("RISK_GATE: daily reset — vetoes cleared")

    @property
    def status(self) -> dict:
        return {
            "active_positions": len(self._active_tickers),
            "active_tickers": list(self._active_tickers.keys()),
            "total_heat_pct": round(self._total_heat_pct, 2),
            "vetoed_tickers": list(self._vetoed.keys()),
            "factor_groups": dict(self._active_groups),
        }


# Module singleton
_instance: Optional[UnifiedRiskGate] = None


def get_unified_risk_gate() -> UnifiedRiskGate:
    global _instance
    if _instance is None:
        _instance = UnifiedRiskGate()
    return _instance
