"""BOOK 32: MICROSTRUCTURE — Order flow patterns & bid-ask bounce"""

import sys
from typing import Dict, List, Optional


def calculate_bid_ask_spread_pct(bid: float, ask: float) -> float:
    """Calculate spread as percentage of mid."""
    if bid <= 0 or ask <= 0:
        return 0.1  # Default 10 bps

    mid = (bid + ask) / 2
    spread_bps = (ask - bid) / mid * 10000 if mid > 0 else 0

    return max(0, min(1, spread_bps / 50))  # Normalize to [0-1]


def calculate_vpin(returns: List[float], volume: List[int], lookback: int = 50) -> float:
    """
    Volume-Synchronized Probability of Informed Trading.
    High VPIN = likely informed buying/selling = opportunity to fade.
    """
    try:
        if len(returns) < lookback or len(volume) < lookback:
            return 0.5

        recent_returns = returns[-lookback:]
        recent_volume = volume[-lookback:]

        # Directional volume: positive returns = buy pressure
        buy_volume = sum(recent_volume[i] for i in range(len(recent_returns)) if recent_returns[i] > 0)
        sell_volume = sum(recent_volume[i] for i in range(len(recent_returns)) if recent_returns[i] <= 0)
        total_volume = sum(recent_volume)

        if total_volume == 0:
            return 0.5

        # VPIN: ratio of directional imbalance
        vpin = abs(buy_volume - sell_volume) / total_volume

        return max(0, min(1, vpin))

    except Exception as e:
        sys.stderr.write(f"VPIN error: {e}\n")
        return 0.5


def estimate_order_imbalance(prices: List[float], volumes: List[int], lookback: int = 10) -> float:
    """
    Order imbalance: More buy orders than sell = bullish.
    Estimated from price moves + volume.
    """
    try:
        if len(prices) < lookback or len(volumes) < lookback:
            return 0.5

        recent_prices = prices[-lookback:]
        recent_volumes = volumes[-lookback:]

        # If price went up on high volume = buy pressure
        up_moves = sum(1 for i in range(1, len(recent_prices)) if recent_prices[i] > recent_prices[i - 1])
        down_moves = len(recent_prices) - 1 - up_moves

        if up_moves + down_moves == 0:
            return 0.5

        # Imbalance: ratio of up-volume to total volume
        imbalance = up_moves / (up_moves + down_moves)

        return imbalance

    except Exception as e:
        sys.stderr.write(f"Order imbalance error: {e}\n")
        return 0.5


def microstructure_signal(ticker: str, msg: Dict, ind: Dict) -> Optional[float]:
    """
    Composite microstructure signal [0-1].
    High = likely short-term reversal (fade the move)
    Low = likely continuation (follow the trend)
    """
    try:
        bid = msg.get("bid", 0)
        ask = msg.get("ask", 0)
        close_history = msg.get("close_history", [])
        volume_history = msg.get("volume_history", [])

        if len(close_history) < 5:
            return None

        # Component 1: Spread
        spread = calculate_bid_ask_spread_pct(bid, ask)

        # Component 2: VPIN
        vpin = calculate_vpin(close_history, volume_history or [1] * len(close_history))

        # Component 3: Order imbalance
        imbalance = estimate_order_imbalance(close_history, volume_history or [1] * len(close_history))

        # Composite: High spread + high VPIN + extreme imbalance = reversal setup
        composite = 0.3 * spread + 0.4 * vpin + 0.3 * abs(imbalance - 0.5)

        return max(0, min(1, composite))

    except Exception as e:
        sys.stderr.write(f"Microstructure signal error: {e}\n")
        return None
