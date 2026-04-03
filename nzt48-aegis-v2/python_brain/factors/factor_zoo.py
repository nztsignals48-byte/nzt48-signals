"""
BOOK 18: FACTOR ZOO TAXONOMY — Systematic Factor Classification & Reweighting

Classify all trading factors into families:
- Value (P/B, P/E, dividend yield)
- Momentum (price, earnings, quality)
- Quality (profitability, payout, accruals)
- Volatility (idiosyncratic, realized, implied)
- Growth (earnings growth, revenue growth)

Reweight signals based on factor exposure & current regime.

Key Insight: Different factors work in different regimes.
- Value: works in low-growth regimes
- Momentum: works in trending regimes
- Quality: works always (market prefers quality)
- Volatility: works in mean-reverting environments

Implementation:
1. Compute factor scores for each stock
2. Composite score per factor family
3. Reweight signals by regime-factor fit
4. Boost/penalize based on factor performance
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional

log = logging.getLogger("factor_zoo")


@dataclass
class FactorScores:
    """All factor scores for a ticker."""
    value_score: float  # [0-1] Value attractiveness
    momentum_score: float  # [0-1] Momentum strength
    quality_score: float  # [0-1] Quality/profitability
    volatility_score: float  # [0-1] Volatility attractiveness
    growth_score: float  # [0-1] Growth potential
    composite_score: float  # [0-1] Weighted composite


def compute_value_factors(ticker: str, msg: Dict) -> Optional[float]:
    """
    Compute value factor score [0-1].
    Low = expensive (high P/B, high P/E)
    High = cheap (low P/B, low P/E)
    """
    try:
        price = msg.get("ltp", 0)
        if price <= 0:
            return None

        # Simplified: use recent price movement as proxy for valuation
        # (In prod: use P/B, P/E from fundamental data)
        recent_return = msg.get("recent_return_pct", 0) / 100
        volatility = msg.get("atr_pct", 1) / 100

        # High recent return + low vol = expensive
        # Low recent return + high vol = cheap
        value_score = max(0, min(1, 0.5 - 0.3 * recent_return + 0.2 * volatility))

        return value_score

    except Exception as e:
        sys.stderr.write(f"Value factor error: {e}\n")
        return None


def compute_momentum_factors(ticker: str, msg: Dict, lookback_bars: int = 252) -> Optional[float]:
    """
    Compute momentum factor score [0-1].
    High = strong momentum (trending up)
    Low = weak momentum (trending down)
    """
    try:
        # Use price history if available
        close_history = msg.get("close_history", [])
        if len(close_history) < lookback_bars // 4:  # Need at least 25% of lookback
            return None

        # Simple momentum: compare recent vs older prices
        if len(close_history) >= lookback_bars:
            old_price = close_history[-lookback_bars]
            new_price = close_history[-1]
        else:
            old_price = close_history[0]
            new_price = close_history[-1]

        if old_price <= 0:
            return None

        momentum_pct = (new_price - old_price) / old_price
        momentum_score = max(0, min(1, 0.5 + 0.5 * momentum_pct / 0.5))  # Normalize to ±50%

        return momentum_score

    except Exception as e:
        sys.stderr.write(f"Momentum factor error: {e}\n")
        return None


def compute_quality_factors(ticker: str, msg: Dict) -> Optional[float]:
    """
    Compute quality factor score [0-1].
    High = high quality (profitable, stable, good payout)
    Low = low quality (unprofitable, risky, poor payout)
    """
    try:
        # Proxy: use volume/volatility as quality signal
        # (In prod: use ROE, debt/equity, accruals quality)
        volume = msg.get("volume", 0)
        volatility = msg.get("atr_pct", 10) / 100

        # High volume + low vol = quality
        # Low volume + high vol = junk
        quality_score = max(0, min(1, 0.5 + 0.3 * (volume > 0) - 0.2 * volatility))

        return quality_score

    except Exception as e:
        sys.stderr.write(f"Quality factor error: {e}\n")
        return None


def compute_volatility_factors(ticker: str, msg: Dict) -> Optional[float]:
    """
    Compute volatility factor score [0-1].
    High = high volatility (mean-reversion opportunity)
    Low = low volatility (stable, boring)
    """
    try:
        atr_pct = msg.get("atr_pct", 1) / 100
        vix = msg.get("vix", 20)

        # Volatility score: higher vol = higher opportunity (for mean reversion)
        # Normalize to [0-1]
        vol_score = max(0, min(1, atr_pct / 0.05))  # 5% ATR = max volatility

        return vol_score

    except Exception as e:
        sys.stderr.write(f"Volatility factor error: {e}\n")
        return None


def compute_growth_factors(ticker: str, msg: Dict) -> Optional[float]:
    """
    Compute growth factor score [0-1].
    High = high growth (earnings accelerating)
    Low = low growth (earnings decelerating)
    """
    try:
        # Proxy: use recent momentum as growth signal
        # (In prod: use earnings growth rate, revenue growth rate)
        recent_return = msg.get("recent_return_pct", 0) / 100

        # Growth score: positive return = growth
        growth_score = max(0, min(1, 0.5 + 0.5 * recent_return / 0.1))  # Normalize to ±10%

        return growth_score

    except Exception as e:
        sys.stderr.write(f"Growth factor error: {e}\n")
        return None


def compute_composite_factor_score(ticker: str, msg: Dict, regime: str = "trending") -> FactorScores:
    """
    Compute all factor scores and composite weighting.

    Args:
        ticker: Stock ticker
        msg: Market message with price/volume/volatility data
        regime: Current market regime (trending/mean-reverting/crisis)

    Returns:
        FactorScores object with all scores [0-1]
    """
    # Compute individual factors
    value = compute_value_factors(ticker, msg) or 0.5
    momentum = compute_momentum_factors(ticker, msg) or 0.5
    quality = compute_quality_factors(ticker, msg) or 0.5
    volatility = compute_volatility_factors(ticker, msg) or 0.5
    growth = compute_growth_factors(ticker, msg) or 0.5

    # Regime-dependent composite weighting
    if regime == "trending":
        # Momentum + growth matter most
        composite = 0.1 * value + 0.4 * momentum + 0.1 * quality + 0.1 * volatility + 0.3 * growth
    elif regime == "mean-reverting":
        # Quality + volatility matter most
        composite = 0.2 * value + 0.1 * momentum + 0.4 * quality + 0.3 * volatility + 0.0 * growth
    elif regime == "crisis":
        # Quality + low vol matter most
        composite = 0.05 * value + 0.05 * momentum + 0.7 * quality + 0.2 * volatility + 0.0 * growth
    else:
        # Balanced default
        composite = 0.2 * (value + momentum + quality + volatility + growth)

    return FactorScores(
        value_score=value,
        momentum_score=momentum,
        quality_score=quality,
        volatility_score=volatility,
        growth_score=growth,
        composite_score=composite,
    )


def apply_factor_adjustment(base_confidence: int, factor_scores: FactorScores, regime: str) -> int:
    """
    Adjust signal confidence based on factor scores.

    Args:
        base_confidence: Original signal confidence [0-100]
        factor_scores: FactorScores object
        regime: Market regime

    Returns:
        Adjusted confidence [0-100]
    """
    # Regime-dependent adjustment multiplier
    if regime == "trending":
        # Boost momentum-heavy signals
        mult = 0.7 + 0.6 * factor_scores.momentum_score
    elif regime == "mean-reverting":
        # Boost mean-reversion setup (high vol, low quality sometimes works)
        mult = 0.7 + 0.4 * factor_scores.volatility_score
    elif regime == "crisis":
        # Boost quality signals, penalize everything else
        mult = 0.3 + 1.4 * factor_scores.quality_score
    else:
        # Default: use composite
        mult = 0.5 + factor_scores.composite_score

    adjusted = int(base_confidence * mult)
    return max(0, min(100, adjusted))
