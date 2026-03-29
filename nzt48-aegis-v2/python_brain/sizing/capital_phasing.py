"""Capital-Aware Strategy Selection — Book 179.

At different equity levels, different strategies are viable.
Small accounts can't run strategies with high per-trade costs.

Phase 1 (£10K): 1-2 strategies, index ETPs only, max 3 positions
Phase 2 (£25K): 3 strategies, add mega-cap single stock
Phase 3 (£50K): 5 strategies, add US equities via PctVol algo
Phase 4 (£100K): 7 strategies, daily-bar trend following
Phase 5 (£500K+): Full ensemble, 80+ instruments

Usage:
    from python_brain.sizing.capital_phasing import (
        get_capital_phase, get_viable_strategies,
    )

    phase = get_capital_phase(equity=10000)
    strategies = get_viable_strategies(phase)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Set

log = logging.getLogger("capital_phasing")


@dataclass(frozen=True)
class CapitalPhase:
    """Configuration for a capital phase."""
    phase: int
    min_equity: float
    max_strategies: int
    max_positions: int
    allowed_strategies: Set[str]
    allowed_instruments: Set[str]  # Instrument types
    min_position_gbp: float
    max_kelly: float


CAPITAL_PHASES: List[CapitalPhase] = [
    CapitalPhase(
        phase=1, min_equity=0, max_strategies=2, max_positions=2,
        allowed_strategies={"TypeF", "S2_Reversion"},
        allowed_instruments={"3x_index"},
        min_position_gbp=2000, max_kelly=0.03,
    ),
    CapitalPhase(
        phase=2, min_equity=15000, max_strategies=3, max_positions=3,
        allowed_strategies={"TypeF", "S2_Reversion", "TypeB", "TypeE"},
        allowed_instruments={"3x_index", "3x_mega_cap"},
        min_position_gbp=2000, max_kelly=0.04,
    ),
    CapitalPhase(
        phase=3, min_equity=25000, max_strategies=5, max_positions=3,
        allowed_strategies={
            "TypeF", "S2_Reversion", "TypeB", "TypeE", "TypeA",
            "VolCompression", "CalendarAnomalies",
        },
        allowed_instruments={"3x_index", "3x_mega_cap", "3x_single"},
        min_position_gbp=2500, max_kelly=0.04,
    ),
    CapitalPhase(
        phase=4, min_equity=50000, max_strategies=7, max_positions=4,
        allowed_strategies={
            "TypeF", "S2_Reversion", "TypeB", "TypeE", "TypeA",
            "S3_MacroTrend", "S5_OvernightCarry", "VolCompression",
            "RebalancingFlow", "AlphaFactory",
        },
        allowed_instruments={"3x_index", "3x_mega_cap", "3x_single", "3x_commodity"},
        min_position_gbp=3000, max_kelly=0.05,
    ),
    CapitalPhase(
        phase=5, min_equity=100000, max_strategies=10, max_positions=5,
        allowed_strategies={
            "TypeF", "S2_Reversion", "TypeB", "TypeE", "TypeA",
            "S3_MacroTrend", "S4_VolPremium", "S5_OvernightCarry",
            "S7_TailHedge", "VolCompression", "RebalancingFlow",
            "AlphaFactory", "Pairs", "LeadLag", "NAVArbitrage",
        },
        allowed_instruments={"3x_index", "3x_mega_cap", "3x_single", "3x_commodity", "vix", "inverse"},
        min_position_gbp=5000, max_kelly=0.05,
    ),
]


def get_capital_phase(equity: float) -> CapitalPhase:
    """Get the appropriate capital phase for current equity."""
    phase = CAPITAL_PHASES[0]
    for p in CAPITAL_PHASES:
        if equity >= p.min_equity:
            phase = p
    return phase


def get_viable_strategies(phase: CapitalPhase) -> Set[str]:
    """Get strategies allowed at this capital phase."""
    return phase.allowed_strategies


def is_strategy_viable(strategy: str, equity: float) -> bool:
    """Check if a strategy is viable at current equity."""
    phase = get_capital_phase(equity)
    return strategy in phase.allowed_strategies


def min_viable_equity(strategy: str) -> float:
    """Get minimum equity needed for a strategy to be viable."""
    for phase in CAPITAL_PHASES:
        if strategy in phase.allowed_strategies:
            return phase.min_equity
    return float("inf")  # Not viable at any phase
