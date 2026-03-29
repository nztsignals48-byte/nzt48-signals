"""Strategy-Regime Matrix — Book 15 Section 8, Book 113.

Maps which strategies activate in which regime, with per-cell parameters
(size multiplier, instrument selection, exit rules). This is the single
source of truth for regime-conditional strategy activation.

4 Regimes × 7+ Strategies = 28+ cells, each with:
  - enabled: bool (is this strategy active in this regime?)
  - size_mult: float (Kelly multiplier 0.0-1.5)
  - instruments: str (which instrument tiers are eligible)
  - exit_tightness: float (Chandelier ATR multiplier adjustment)

Usage:
    from python_brain.regime.strategy_regime_matrix import (
        get_strategy_regime_params, should_strategy_fire, RegimeState,
    )

    regime = RegimeState.from_indicators(vix=25, hurst=0.35, ...)
    params = get_strategy_regime_params("S2_Reversion", regime)
    if not params.enabled:
        skip_signal()
    else:
        kelly *= params.size_mult
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple

log = logging.getLogger("strategy_regime_matrix")


# ---------------------------------------------------------------------------
# Regime States (Book 15)
# ---------------------------------------------------------------------------
class Regime(Enum):
    CRISIS = "CRISIS"       # VIX > 30, or HMM state 2 (HighVol)
    STEADY = "STEADY"       # VIX < 18, low vol, trending
    WOI = "WOI"             # Walking on Ice: VIX 18-30, uncertain
    INFLATION = "INFLATION"  # Yield curve inverted + inflation > 3%


@dataclass(frozen=True)
class RegimeState:
    """Current regime classification with confidence."""
    regime: Regime
    confidence: float  # 0.0-1.0
    vix: float
    hurst: float
    hmm_state: int  # 0=LowVol, 1=Normal, 2=HighVol

    @classmethod
    def from_indicators(
        cls,
        vix: float = 21.0,
        hurst: float = 0.50,
        hmm_state: int = 1,
        yield_curve_inverted: bool = False,
        inflation_pct: float = 2.0,
    ) -> "RegimeState":
        """Classify regime from market indicators."""
        # Priority: CRISIS > INFLATION > WOI > STEADY
        if vix > 30 or hmm_state == 2:
            conf = min(1.0, (vix - 25) / 15) if vix > 25 else 0.6
            return cls(Regime.CRISIS, conf, vix, hurst, hmm_state)

        if yield_curve_inverted and inflation_pct > 3.0:
            return cls(Regime.INFLATION, 0.7, vix, hurst, hmm_state)

        if vix >= 18 or hmm_state == 1:
            conf = min(1.0, (vix - 15) / 10) if vix >= 15 else 0.5
            return cls(Regime.WOI, conf, vix, hurst, hmm_state)

        return cls(Regime.STEADY, 0.8, vix, hurst, hmm_state)


# ---------------------------------------------------------------------------
# Per-Cell Parameters
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StrategyRegimeParams:
    """Parameters for a specific strategy in a specific regime."""
    enabled: bool = True
    size_mult: float = 1.0       # Kelly multiplier (0.0-1.5)
    confidence_adj: int = 0      # Confidence bonus/penalty (-20 to +20)
    exit_tightness: float = 1.0  # ATR multiplier for Chandelier (0.5=tight, 1.5=loose)
    max_positions: int = 3       # Max simultaneous positions for this strategy
    instruments: str = "all"     # "index_only", "mega_cap", "all", "inverse_only"

    @staticmethod
    def disabled() -> "StrategyRegimeParams":
        return StrategyRegimeParams(enabled=False, size_mult=0.0)


# ---------------------------------------------------------------------------
# The Matrix (7 strategies × 4 regimes)
# ---------------------------------------------------------------------------
# Book 15 Section 8: Each cell defines whether and how a strategy operates
# in each regime.

STRATEGY_REGIME_MATRIX: Dict[Tuple[str, Regime], StrategyRegimeParams] = {
    # ===== TypeF (OBV Divergence) — volume-price divergence, strongest signal =====
    ("TypeF", Regime.STEADY): StrategyRegimeParams(size_mult=1.2, confidence_adj=5),
    ("TypeF", Regime.WOI): StrategyRegimeParams(size_mult=0.8, confidence_adj=-5),
    ("TypeF", Regime.CRISIS): StrategyRegimeParams(size_mult=0.5, confidence_adj=-10, exit_tightness=0.7),
    ("TypeF", Regime.INFLATION): StrategyRegimeParams(size_mult=0.7),

    # ===== TypeB (EarlyRunner) — RVOL rising + RSI momentum =====
    ("TypeB", Regime.STEADY): StrategyRegimeParams(size_mult=1.0),
    ("TypeB", Regime.WOI): StrategyRegimeParams(size_mult=0.7, confidence_adj=-5),
    ("TypeB", Regime.CRISIS): StrategyRegimeParams.disabled(),  # Momentum fails in crisis
    ("TypeB", Regime.INFLATION): StrategyRegimeParams(size_mult=0.6),

    # ===== S2_Reversion — BB z-score + RSI(2) mean reversion =====
    ("S2_Reversion", Regime.STEADY): StrategyRegimeParams(size_mult=0.8),  # Less mean reversion in trends
    ("S2_Reversion", Regime.WOI): StrategyRegimeParams(size_mult=1.2, confidence_adj=5),  # Best in choppy
    ("S2_Reversion", Regime.CRISIS): StrategyRegimeParams(size_mult=0.5, exit_tightness=0.6),
    ("S2_Reversion", Regime.INFLATION): StrategyRegimeParams(size_mult=1.0),

    # ===== S3_MacroTrend — SMA crossover, trend following =====
    ("S3_MacroTrend", Regime.STEADY): StrategyRegimeParams(size_mult=1.3, confidence_adj=8),  # Best regime
    ("S3_MacroTrend", Regime.WOI): StrategyRegimeParams(size_mult=0.5, confidence_adj=-10),
    ("S3_MacroTrend", Regime.CRISIS): StrategyRegimeParams.disabled(),  # Trend breaks in crisis
    ("S3_MacroTrend", Regime.INFLATION): StrategyRegimeParams(size_mult=0.7),

    # ===== S4_VolPremium — short vol via inverse ETPs =====
    ("S4_VolPremium", Regime.STEADY): StrategyRegimeParams(size_mult=1.0, instruments="inverse_only"),
    ("S4_VolPremium", Regime.WOI): StrategyRegimeParams.disabled(),  # Vol selling in uncertain regime = suicide
    ("S4_VolPremium", Regime.CRISIS): StrategyRegimeParams.disabled(),
    ("S4_VolPremium", Regime.INFLATION): StrategyRegimeParams(size_mult=0.5),

    # ===== S5_OvernightCarry — buy at close, sell at open =====
    ("S5_OvernightCarry", Regime.STEADY): StrategyRegimeParams(size_mult=1.0),
    ("S5_OvernightCarry", Regime.WOI): StrategyRegimeParams(size_mult=0.5, confidence_adj=-10),
    ("S5_OvernightCarry", Regime.CRISIS): StrategyRegimeParams.disabled(),  # No overnight in crisis
    ("S5_OvernightCarry", Regime.INFLATION): StrategyRegimeParams(size_mult=0.3),

    # ===== S7_TailHedge — long inverse during VIX spikes =====
    ("S7_TailHedge", Regime.STEADY): StrategyRegimeParams.disabled(),  # No need for tail hedge in calm
    ("S7_TailHedge", Regime.WOI): StrategyRegimeParams(size_mult=0.5, instruments="inverse_only"),
    ("S7_TailHedge", Regime.CRISIS): StrategyRegimeParams(size_mult=1.5, confidence_adj=15, instruments="inverse_only"),
    ("S7_TailHedge", Regime.INFLATION): StrategyRegimeParams(size_mult=0.7, instruments="inverse_only"),

    # ===== TypeA (DipRecovery) — RSI<30 + vol spike =====
    ("TypeA", Regime.STEADY): StrategyRegimeParams(size_mult=0.8),
    ("TypeA", Regime.WOI): StrategyRegimeParams(size_mult=1.0, confidence_adj=3),
    ("TypeA", Regime.CRISIS): StrategyRegimeParams(size_mult=0.4, exit_tightness=0.5),  # Dips in crisis are traps
    ("TypeA", Regime.INFLATION): StrategyRegimeParams(size_mult=0.7),

    # ===== TypeE (IBS Mean Reversion) =====
    ("TypeE", Regime.STEADY): StrategyRegimeParams(size_mult=0.9),
    ("TypeE", Regime.WOI): StrategyRegimeParams(size_mult=1.1, confidence_adj=5),
    ("TypeE", Regime.CRISIS): StrategyRegimeParams(size_mult=0.5, exit_tightness=0.6),
    ("TypeE", Regime.INFLATION): StrategyRegimeParams(size_mult=1.0),

    # ===== VanguardSniper — momentum =====
    ("VanguardSniper", Regime.STEADY): StrategyRegimeParams(size_mult=1.2),
    ("VanguardSniper", Regime.WOI): StrategyRegimeParams(size_mult=0.6, confidence_adj=-10),
    ("VanguardSniper", Regime.CRISIS): StrategyRegimeParams.disabled(),
    ("VanguardSniper", Regime.INFLATION): StrategyRegimeParams(size_mult=0.5),
}

# Default for strategies not in matrix
_DEFAULT_PARAMS = StrategyRegimeParams(size_mult=0.7)


# ---------------------------------------------------------------------------
# Lookup Functions
# ---------------------------------------------------------------------------
def get_strategy_regime_params(
    strategy: str,
    regime_state: RegimeState,
) -> StrategyRegimeParams:
    """Look up strategy parameters for current regime."""
    key = (strategy, regime_state.regime)
    return STRATEGY_REGIME_MATRIX.get(key, _DEFAULT_PARAMS)


def should_strategy_fire(
    strategy: str,
    regime_state: RegimeState,
) -> bool:
    """Quick check: is this strategy enabled in current regime?"""
    return get_strategy_regime_params(strategy, regime_state).enabled


def apply_regime_adjustments(
    strategy: str,
    confidence: int,
    kelly_fraction: float,
    regime_state: RegimeState,
) -> Tuple[int, float]:
    """Apply regime-based adjustments to signal confidence and Kelly.

    Returns (adjusted_confidence, adjusted_kelly).
    """
    params = get_strategy_regime_params(strategy, regime_state)

    if not params.enabled:
        return 0, 0.0

    adj_conf = max(0, min(100, confidence + params.confidence_adj))
    adj_kelly = kelly_fraction * params.size_mult

    return adj_conf, adj_kelly
