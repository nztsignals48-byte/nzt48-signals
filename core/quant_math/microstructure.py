"""
Market microstructure utilities.
Stoikov (2017) micro-price + Amihud (2002) illiquidity measure.
"""
from __future__ import annotations


def calculate_micro_price(bid_price: float, ask_price: float,
                          bid_size: float, ask_size: float) -> float:
    """
    Stoikov (2017): Micro-Price is a martingale estimator of future mid-price.
    If Ask size >> Bid size, true price is LOWER than mid-price.
    SSRN 2970694.
    """
    total_size = bid_size + ask_size
    if total_size == 0:
        return (bid_price + ask_price) / 2.0
    return (bid_price * ask_size + ask_price * bid_size) / total_size


def calculate_spread_momentum(spread_history: list[float], window: int = 5) -> float:
    """Calculate spread momentum over last N observations.

    Stoikov (2017): spread widening signals liquidity deterioration.
    Positive = spread widening (bad). Negative = spread tightening (good).

    Args:
        spread_history: List of bid-ask spread values in bps (most recent last).
        window: Lookback window (default 5).

    Returns:
        Fractional change: (current - avg_prior) / avg_prior. E.g., 0.20 = 20% widening.
    """
    if len(spread_history) < window:
        return 0.0

    recent = spread_history[-window:]
    avg_prior = sum(recent[:-1]) / max(len(recent) - 1, 1)
    current = recent[-1]

    if avg_prior <= 0:
        return 0.0

    return (current - avg_prior) / avg_prior


def calculate_amihud_illiquidity(abs_returns: list[float], volumes: list[float]) -> float:
    """
    Amihud (2002): ILLIQ = (1/D) * sum(|r_d| / V_d)
    Higher = less liquid = wider implicit spread.
    """
    if not abs_returns or not volumes or len(abs_returns) != len(volumes):
        return 0.0
    total = 0.0
    count = 0
    for r, v in zip(abs_returns, volumes):
        if v > 0:
            total += abs(r) / v
            count += 1
    return total / count if count > 0 else 0.0
