"""Vol-Targeting Position Sizing — Books 10, 80, 118.

Replaces fixed Kelly with volatility-adjusted sizing.
Key insight: size inversely to realized volatility so dollar risk
per trade stays constant regardless of market conditions.

Three sizing layers (applied in order):
1. Vol-target: size = target_vol / realized_vol × base_kelly
2. Kelly ratchet: blend 50/100/200 trade windows for stable Kelly
3. Student-t correction: reduce Kelly for fat-tailed ETP returns

Usage:
    from python_brain.sizing.vol_targeting import (
        vol_adjusted_kelly, kelly_ratchet, student_t_correction,
    )

    kelly = vol_adjusted_kelly(base_kelly=0.05, realized_vol=0.03, target_vol=0.02)
"""

from __future__ import annotations

import math
import logging
from typing import List, Optional

import numpy as np

log = logging.getLogger("vol_targeting")


# ---------------------------------------------------------------------------
# Vol-Targeting (Book 80)
# ---------------------------------------------------------------------------
def vol_adjusted_kelly(
    base_kelly: float,
    realized_vol: float,
    target_vol: float = 0.02,
    floor: float = 0.2,
    cap: float = 2.0,
) -> float:
    """Scale Kelly fraction inversely to realized volatility.

    When vol is high, size smaller. When vol is low, size larger.
    Moreira & Muir (2017): this dominates buy-and-hold for leveraged products.

    Args:
        base_kelly: Theoretical Kelly fraction from win rate / payoff
        realized_vol: Current realized volatility (annualized decimal)
        target_vol: Target volatility (annualized decimal, default 2%)
        floor: Minimum scaling factor (never go below 20% of base)
        cap: Maximum scaling factor (never exceed 2x base)

    Returns:
        Volatility-adjusted Kelly fraction
    """
    if realized_vol <= 0:
        return base_kelly

    vol_ratio = target_vol / realized_vol
    scale = max(floor, min(cap, vol_ratio))

    return base_kelly * scale


# ---------------------------------------------------------------------------
# Dynamic Kelly Ratchet (Book 10)
# ---------------------------------------------------------------------------
def kelly_ratchet(
    trade_returns: List[float],
    windows: tuple = (50, 100, 200),
    weights: tuple = (0.5, 0.3, 0.2),
) -> float:
    """Blended Kelly from multiple lookback windows for stability.

    Short window (50) responds fast to regime changes.
    Long window (200) provides stable base.
    Blend prevents whipsawing.

    Returns: Blended Kelly fraction (0.0 - 0.20)
    """
    if not trade_returns or len(trade_returns) < windows[0]:
        return 0.0

    kellys = []
    for w in windows:
        recent = trade_returns[-w:] if len(trade_returns) >= w else trade_returns
        k = _compute_kelly(recent)
        kellys.append(k)

    # Weighted blend
    total_weight = sum(weights[:len(kellys)])
    if total_weight <= 0:
        return 0.0

    blended = sum(k * w for k, w in zip(kellys, weights)) / total_weight
    return max(0.0, min(0.20, blended))


def _compute_kelly(returns: List[float]) -> float:
    """Compute Kelly fraction from trade returns.

    f* = (p * b - q) / b
    where p = win rate, q = 1-p, b = avg_win / avg_loss
    """
    if not returns:
        return 0.0

    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]

    if not wins or not losses:
        return 0.0

    p = len(wins) / len(returns)
    q = 1 - p
    avg_win = sum(wins) / len(wins)
    avg_loss = abs(sum(losses) / len(losses))

    if avg_loss <= 0:
        return 0.0

    b = avg_win / avg_loss
    kelly = (p * b - q) / b

    # Half-Kelly for safety
    return max(0.0, kelly * 0.5)


# ---------------------------------------------------------------------------
# Student-t Fat Tail Correction (Book 118)
# ---------------------------------------------------------------------------
def student_t_correction(
    kelly: float,
    nu: float = 5.0,
    leverage: int = 3,
) -> float:
    """Adjust Kelly for fat-tailed returns (Student-t distribution).

    ETP returns have nu ≈ 4-6 (fatter tails than Gaussian).
    Gaussian Kelly overestimates optimal fraction when tails are fat.

    Correction factor: 1 / (1 + (leverage-1)/nu)
    For 3x ETP with nu=5: factor = 1/(1+2/5) = 0.714

    Args:
        kelly: Base Kelly fraction
        nu: Degrees of freedom (lower = fatter tails)
        leverage: ETP leverage factor

    Returns:
        Fat-tail adjusted Kelly
    """
    if nu <= 1 or leverage < 1:
        return kelly * 0.5  # Very conservative for extreme tails

    correction = 1.0 / (1.0 + (leverage - 1) / nu)
    return kelly * correction


# ---------------------------------------------------------------------------
# Shannon's Demon Rebalancing Bonus (Book 10)
# ---------------------------------------------------------------------------
def shannons_demon_bonus(
    n_strategies: int,
    avg_strategy_vol: float,
    avg_correlation: float = 0.3,
) -> float:
    """Estimate the diversification bonus from multi-strategy rebalancing.

    Shannon's Demon: rebalancing a portfolio of uncorrelated volatile assets
    captures a geometric growth bonus proportional to:
    bonus ≈ (n-1)/n × σ²/2 × (1 - ρ)

    For 7 strategies with 15% vol and 0.3 correlation:
    bonus ≈ 6/7 × 0.0225/2 × 0.7 ≈ 0.67% annually

    Returns: Estimated annual bonus (decimal, e.g., 0.0067 for 0.67%)
    """
    if n_strategies < 2 or avg_strategy_vol <= 0:
        return 0.0

    diversification = (n_strategies - 1) / n_strategies
    vol_squared = avg_strategy_vol ** 2
    decorrelation = 1 - avg_correlation

    return diversification * vol_squared / 2 * decorrelation


# ---------------------------------------------------------------------------
# Composite Sizing Function
# ---------------------------------------------------------------------------
def compute_position_size(
    base_kelly: float,
    trade_returns: List[float],
    realized_vol: float,
    target_vol: float = 0.02,
    leverage: int = 3,
    nu: float = 5.0,
    drawdown_scale: float = 1.0,
    equity: float = 10000.0,
) -> float:
    """Compute final position size incorporating all sizing layers.

    Returns: Position size in GBP.
    """
    # Layer 1: Vol-targeting
    vol_kelly = vol_adjusted_kelly(base_kelly, realized_vol, target_vol)

    # Layer 2: Kelly ratchet (if enough history)
    if len(trade_returns) >= 50:
        ratcheted = kelly_ratchet(trade_returns)
        # Blend: 60% vol-targeted, 40% ratcheted
        blended = vol_kelly * 0.6 + ratcheted * 0.4
    else:
        blended = vol_kelly

    # Layer 3: Student-t correction
    corrected = student_t_correction(blended, nu, leverage)

    # Layer 4: Drawdown scaling
    final_kelly = corrected * drawdown_scale

    # Clamp to [0.5%, 5%]
    final_kelly = max(0.005, min(0.05, final_kelly))

    return final_kelly * equity
