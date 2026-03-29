"""Regime-Aware Dynamic Risk Limits — Book 85.

Static risk limits are simultaneously too loose in crisis and too tight
in calm. This module dynamically adjusts all risk parameters based on
the current regime.

4 regime states × 6 risk parameters = 24 regime-conditional settings.
Transitions use smooth ramps (not sudden switches) to avoid whipsaw.

Usage:
    from python_brain.risk.regime_risk_limits import (
        DynamicRiskLimits, get_regime_limits,
    )

    limits = get_regime_limits(vix=28, hurst=0.3, hmm_state=1)
    max_positions = limits.max_positions
    kelly_cap = limits.kelly_cap
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict

log = logging.getLogger("regime_risk_limits")


@dataclass(frozen=True)
class RiskLimitSet:
    """Complete set of risk limits for a given regime."""
    max_positions: int = 3
    portfolio_heat_pct: float = 10.0
    kelly_cap: float = 0.05
    confidence_floor: int = 50
    max_single_position_pct: float = 33.0
    daily_trade_limit: int = 10
    stop_tightness_mult: float = 1.0  # 1.0 = normal, 0.5 = 2x tighter
    leverage_cap: float = 3.0
    overnight_exposure_pct: float = 100.0


# Regime → risk limits
REGIME_LIMITS: Dict[str, RiskLimitSet] = {
    "STEADY": RiskLimitSet(
        max_positions=3,
        portfolio_heat_pct=10.0,
        kelly_cap=0.05,
        confidence_floor=50,
        max_single_position_pct=33.0,
        daily_trade_limit=10,
        stop_tightness_mult=1.0,
        leverage_cap=3.0,
        overnight_exposure_pct=100.0,
    ),
    "WOI": RiskLimitSet(
        max_positions=2,
        portfolio_heat_pct=7.0,
        kelly_cap=0.035,
        confidence_floor=60,
        max_single_position_pct=25.0,
        daily_trade_limit=6,
        stop_tightness_mult=0.75,
        leverage_cap=2.5,
        overnight_exposure_pct=50.0,
    ),
    "CRISIS": RiskLimitSet(
        max_positions=1,
        portfolio_heat_pct=4.0,
        kelly_cap=0.02,
        confidence_floor=75,
        max_single_position_pct=15.0,
        daily_trade_limit=3,
        stop_tightness_mult=0.5,
        leverage_cap=2.0,
        overnight_exposure_pct=20.0,
    ),
    "EXTREME": RiskLimitSet(
        max_positions=0,  # No new entries
        portfolio_heat_pct=0.0,
        kelly_cap=0.0,
        confidence_floor=100,  # Nothing passes
        max_single_position_pct=0.0,
        daily_trade_limit=0,
        stop_tightness_mult=0.3,
        leverage_cap=0.0,
        overnight_exposure_pct=0.0,
    ),
}


def classify_regime(vix: float, hurst: float = 0.5, hmm_state: int = 1) -> str:
    """Classify current regime for risk limit selection."""
    if vix >= 50 or hmm_state == 2 and vix >= 30:
        return "EXTREME"
    elif vix >= 30:
        return "CRISIS"
    elif vix >= 18 or hmm_state == 1:
        return "WOI"
    return "STEADY"


def get_regime_limits(
    vix: float = 21.0,
    hurst: float = 0.5,
    hmm_state: int = 1,
) -> RiskLimitSet:
    """Get risk limits for current market conditions."""
    regime = classify_regime(vix, hurst, hmm_state)
    return REGIME_LIMITS.get(regime, REGIME_LIMITS["WOI"])


def interpolate_limits(
    current: RiskLimitSet,
    target: RiskLimitSet,
    progress: float,  # 0.0 = current, 1.0 = target
) -> RiskLimitSet:
    """Smoothly transition between two risk limit sets.

    Used during regime transitions to avoid sudden parameter jumps.
    Typical transition: 2-5 days (progress increments 0.2-0.5 per day).
    """
    p = max(0.0, min(1.0, progress))
    q = 1.0 - p

    return RiskLimitSet(
        max_positions=int(current.max_positions * q + target.max_positions * p),
        portfolio_heat_pct=current.portfolio_heat_pct * q + target.portfolio_heat_pct * p,
        kelly_cap=current.kelly_cap * q + target.kelly_cap * p,
        confidence_floor=int(current.confidence_floor * q + target.confidence_floor * p),
        max_single_position_pct=current.max_single_position_pct * q + target.max_single_position_pct * p,
        daily_trade_limit=int(current.daily_trade_limit * q + target.daily_trade_limit * p),
        stop_tightness_mult=current.stop_tightness_mult * q + target.stop_tightness_mult * p,
        leverage_cap=current.leverage_cap * q + target.leverage_cap * p,
        overnight_exposure_pct=current.overnight_exposure_pct * q + target.overnight_exposure_pct * p,
    )
