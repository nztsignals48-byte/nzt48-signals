"""Execution Quality Engineering — Books 19, 49, 90, 101, 123.

Measures and improves execution quality through:
1. Implementation shortfall tracking (Perold framework)
2. Liquidity scoring per instrument (ADV + spread + depth)
3. IBKR algo order selection (Adaptive vs MidPrice vs TWAP)
4. Capacity monitoring (ADV participation rate)
5. Market impact estimation (Almgren-Chriss square root model)

Implementation shortfall = decision_price - execution_price
  Decomposed into: delay cost + market impact + timing cost

Usage:
    from python_brain.execution.quality import (
        ExecutionAnalyzer, LiquidityScorer, AlgoSelector,
    )

    analyzer = ExecutionAnalyzer()
    shortfall = analyzer.compute_shortfall(
        decision_price=15.42, execution_price=15.45, side="buy",
    )
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("execution_quality")


# ---------------------------------------------------------------------------
# Implementation Shortfall (Book 101)
# ---------------------------------------------------------------------------
@dataclass
class ExecutionShortfall:
    """Decomposed implementation shortfall for a single trade."""
    ticker: str = ""
    side: str = ""  # "buy" or "sell"
    decision_price: float = 0.0  # Price when signal fired
    arrival_price: float = 0.0    # Price when order submitted
    execution_price: float = 0.0  # Actual fill price
    # Decomposition
    delay_cost_bps: float = 0.0    # (arrival - decision) / decision
    market_impact_bps: float = 0.0  # (execution - arrival) / arrival
    total_shortfall_bps: float = 0.0  # (execution - decision) / decision
    # Quality grade
    grade: str = "C"  # A (< 5bps), B (5-15bps), C (15-30bps), D (> 30bps)

    def compute(self):
        """Compute shortfall components."""
        if self.decision_price <= 0:
            return

        sign = 1.0 if self.side == "buy" else -1.0

        self.delay_cost_bps = sign * (self.arrival_price - self.decision_price) / self.decision_price * 10000
        self.market_impact_bps = sign * (self.execution_price - self.arrival_price) / self.arrival_price * 10000
        self.total_shortfall_bps = sign * (self.execution_price - self.decision_price) / self.decision_price * 10000

        # Grade
        abs_sf = abs(self.total_shortfall_bps)
        if abs_sf < 5:
            self.grade = "A"
        elif abs_sf < 15:
            self.grade = "B"
        elif abs_sf < 30:
            self.grade = "C"
        else:
            self.grade = "D"


class ExecutionAnalyzer:
    """Track and analyze execution quality across all trades."""

    def __init__(self):
        self._history: List[ExecutionShortfall] = []

    def record(self, shortfall: ExecutionShortfall):
        self._history.append(shortfall)

    def compute_shortfall(
        self,
        ticker: str,
        decision_price: float,
        arrival_price: float,
        execution_price: float,
        side: str = "buy",
    ) -> ExecutionShortfall:
        sf = ExecutionShortfall(
            ticker=ticker, side=side,
            decision_price=decision_price,
            arrival_price=arrival_price,
            execution_price=execution_price,
        )
        sf.compute()
        self.record(sf)
        return sf

    def summary(self) -> Dict[str, float]:
        if not self._history:
            return {}
        n = len(self._history)
        avg_sf = sum(h.total_shortfall_bps for h in self._history) / n
        avg_delay = sum(h.delay_cost_bps for h in self._history) / n
        avg_impact = sum(h.market_impact_bps for h in self._history) / n
        grades = {g: sum(1 for h in self._history if h.grade == g) for g in "ABCD"}
        return {
            "n_trades": n,
            "avg_shortfall_bps": round(avg_sf, 2),
            "avg_delay_bps": round(avg_delay, 2),
            "avg_impact_bps": round(avg_impact, 2),
            "grade_distribution": grades,
        }


# ---------------------------------------------------------------------------
# Liquidity Scoring (Book 90)
# ---------------------------------------------------------------------------
@dataclass
class LiquidityScore:
    """Composite liquidity score for an instrument (0-100)."""
    ticker: str = ""
    adv_score: float = 0.0     # Average daily volume score (40%)
    spread_score: float = 0.0  # Bid-ask spread score (30%)
    depth_score: float = 0.0   # Order book depth score (20%)
    consistency_score: float = 0.0  # Volume consistency score (10%)
    composite: float = 0.0     # Weighted composite

    @property
    def is_tradeable(self) -> bool:
        return self.composite >= 30.0  # Score < 30 = do not trade

    @property
    def tier(self) -> str:
        if self.composite >= 70:
            return "high"
        elif self.composite >= 40:
            return "medium"
        return "low"


class LiquidityScorer:
    """Score instrument liquidity for position sizing and order routing."""

    def __init__(self, adv_data: Optional[Dict[str, float]] = None):
        self._adv: Dict[str, float] = adv_data or {}
        self._spread_history: Dict[str, List[float]] = {}

    def update_adv(self, ticker: str, adv_gbp: float):
        self._adv[ticker] = adv_gbp

    def update_spread(self, ticker: str, spread_bps: float):
        self._spread_history.setdefault(ticker, []).append(spread_bps)
        # Keep last 100
        if len(self._spread_history[ticker]) > 100:
            self._spread_history[ticker] = self._spread_history[ticker][-100:]

    def score(self, ticker: str) -> LiquidityScore:
        ls = LiquidityScore(ticker=ticker)

        # ADV score (40%): 0 at ADV<10K, 100 at ADV>500K
        adv = self._adv.get(ticker, 0)
        ls.adv_score = min(100, max(0, (adv - 10000) / 4900))

        # Spread score (30%): 100 at spread<5bps, 0 at spread>50bps
        spreads = self._spread_history.get(ticker, [])
        if spreads:
            avg_spread = sum(spreads) / len(spreads)
            ls.spread_score = min(100, max(0, (50 - avg_spread) / 0.45))
        else:
            ls.spread_score = 50  # Default neutral

        # Depth and consistency (placeholder — need L2 data)
        ls.depth_score = 50
        ls.consistency_score = 50

        # Composite
        ls.composite = (
            ls.adv_score * 0.4
            + ls.spread_score * 0.3
            + ls.depth_score * 0.2
            + ls.consistency_score * 0.1
        )

        return ls

    def position_size_adjustment(self, ticker: str) -> float:
        """Reduce position size for illiquid instruments."""
        ls = self.score(ticker)
        if not ls.is_tradeable:
            return 0.0
        return min(1.0, ls.composite / 70.0)


# ---------------------------------------------------------------------------
# IBKR Algo Order Selection (Book 19)
# ---------------------------------------------------------------------------
class AlgoType(Enum):
    LIMIT = "LMT"           # Simple limit order
    ADAPTIVE = "Adaptive"    # IBKR Adaptive algo
    MIDPRICE = "MidPrice"    # IBKR MidPrice algo
    TWAP = "TWAP"           # Time-weighted average
    VWAP = "VWAP"           # Volume-weighted average
    PCTVOL = "PctVol"       # Percentage of volume


class AlgoSelector:
    """Select appropriate IBKR algo based on order characteristics."""

    def select(
        self,
        notional_gbp: float,
        adv_gbp: float,
        spread_bps: float,
        urgency: str = "normal",  # "low", "normal", "high", "emergency"
    ) -> AlgoType:
        """Select optimal execution algorithm.

        Decision matrix:
        - Emergency (exits, risk): Limit at market
        - High urgency: Adaptive (patient→urgent)
        - Large order (>5% ADV): PctVol or TWAP
        - Wide spread (>20bps): MidPrice
        - Normal: Adaptive (patient)
        - Small order: Simple limit
        """
        if urgency == "emergency":
            return AlgoType.LIMIT

        participation = notional_gbp / max(adv_gbp, 1) if adv_gbp > 0 else 1.0

        # Large orders: split across time
        if participation > 0.05:
            return AlgoType.PCTVOL if urgency == "normal" else AlgoType.TWAP

        # Wide spread: use MidPrice to improve execution
        if spread_bps > 20:
            return AlgoType.MIDPRICE

        # Default: Adaptive
        return AlgoType.ADAPTIVE


# ---------------------------------------------------------------------------
# Market Impact Estimation (Book 123)
# ---------------------------------------------------------------------------
def estimate_market_impact(
    notional_gbp: float,
    adv_gbp: float,
    daily_vol: float = 0.02,
    impact_constant: float = 0.1,
) -> float:
    """Almgren-Chriss square root market impact model.

    impact_bps = σ × sqrt(Q/V) × C

    where σ = daily vol, Q = order size, V = ADV, C = impact constant

    Returns: Estimated market impact in basis points.
    """
    if adv_gbp <= 0 or notional_gbp <= 0:
        return 0.0

    participation = notional_gbp / adv_gbp
    impact = daily_vol * math.sqrt(participation) * impact_constant * 10000  # Convert to bps

    return round(impact, 2)
