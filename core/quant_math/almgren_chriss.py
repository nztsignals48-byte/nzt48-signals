"""
Almgren-Chriss Dynamic Slippage Model.
Replaces static bps-per-side with participation-rate and volatility-dependent impact.
Almgren & Chriss (2001), Journal of Risk 3(2).
"""
from __future__ import annotations
import math


def calculate_dynamic_slippage(order_qty: float, adv: float,
                                daily_vol: float, top_of_book_qty: float) -> float:
    """
    Impact = Permanent + Temporary.
    Permanent: gamma * sigma * (order/ADV)
    Temporary: eta * sigma * sqrt(order/top_book)

    Returns impact in price units (multiply by price to get bps).
    """
    gamma = 0.05   # Permanent impact coefficient
    eta = 0.10     # Temporary impact coefficient

    if adv <= 0:
        adv = 500_000
    participation = order_qty / adv
    permanent = gamma * daily_vol * participation

    if top_of_book_qty <= 0:
        return permanent + daily_vol * 0.10  # Conservative fallback

    liquidity_demand = order_qty / top_of_book_qty
    temporary = eta * daily_vol * math.sqrt(max(liquidity_demand, 0))

    return permanent + temporary
