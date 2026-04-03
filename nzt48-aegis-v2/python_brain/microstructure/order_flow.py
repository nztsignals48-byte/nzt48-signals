"""BOOK 32: MICROSTRUCTURE — Order flow patterns & bid-ask bounce

PRODUCTION VERSION (Session 19):
- True VPIN algorithm with volume bars (not time bars)
- Tick-direction buy/sell classification
- Volume bar normalization by notional volume
- Informed trading detection (VPIN > 0.60)
"""

import sys
from typing import Dict, List, Optional

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    np = None


def calculate_bid_ask_spread_pct(bid: float, ask: float) -> float:
    """Calculate spread as percentage of mid."""
    if bid <= 0 or ask <= 0:
        return 0.1  # Default 10 bps

    mid = (bid + ask) / 2
    spread_bps = (ask - bid) / mid * 10000 if mid > 0 else 0

    return max(0, min(1, spread_bps / 50))  # Normalize to [0-1]


def calculate_vpin_production(
    tick_prices: List[float],
    tick_volumes: List[int],
    volume_bar_size: int = 1_000_000  # $1M notional volume per bar
) -> float:
    """
    True VPIN (Volume-Synchronized Probability of Informed Trading).

    PRODUCTION FIX (Session 19):
    1. Create volume bars (partition by notional volume, not time)
    2. For each bar: determine buy vs sell (tick direction)
    3. Calculate VPIN = abs(buy_vol - sell_vol) / total_vol
    4. High VPIN (>0.60) = informed trading detected

    Algorithm:
    - Tick direction: price increase = buy, price decrease = sell
    - Volume bar: accumulate ticks until reaching notional threshold
    - VPIN: average buy/sell imbalance over recent 50 bars

    Returns:
        VPIN value 0.0-1.0 (higher = more informed trading)
    """
    try:
        if not HAS_NUMPY or len(tick_prices) < 100:
            return 0.5  # Insufficient data

        # STEP 1: Create volume bars (partition by notional volume)
        volume_bars = []
        current_volume = 0  # $ notional volume
        current_bar = {"buy_vol": 0, "sell_vol": 0}

        for i in range(len(tick_prices)):
            price = float(tick_prices[i])
            volume = int(tick_volumes[i])
            notional_volume = price * volume

            current_volume += notional_volume

            # STEP 2: Determine if tick is buy or sell (tick direction rule)
            if i == 0:
                direction = "buy"  # Assume buy for first tick
            else:
                prev_price = float(tick_prices[i - 1])
                if price > prev_price:
                    direction = "buy"  # Uptick
                elif price < prev_price:
                    direction = "sell"  # Downtick
                else:
                    # Price unchanged: use volume signature
                    # High volume on flat = buyer-initiated, low volume = seller-initiated
                    median_vol = np.median(tick_volumes[-20:]) if i >= 20 else np.median(tick_volumes)
                    direction = "buy" if volume > median_vol else "sell"

            # STEP 3: Accumulate to volume bar
            if direction == "buy":
                current_bar["buy_vol"] += volume
            else:
                current_bar["sell_vol"] += volume

            # STEP 4: Close bar when reaching notional threshold
            if current_volume >= volume_bar_size:
                volume_bars.append(current_bar)
                current_volume = 0
                current_bar = {"buy_vol": 0, "sell_vol": 0}

        # STEP 5: Calculate VPIN over recent 50 bars
        recent_bars = volume_bars[-50:] if len(volume_bars) >= 50 else volume_bars

        if len(recent_bars) == 0:
            return 0.5

        # VPIN = sum(|buy_vol - sell_vol|) / sum(total_vol) over recent period
        total_imbalance = 0
        total_volume = 0

        for bar in recent_bars:
            buy_vol = bar["buy_vol"]
            sell_vol = bar["sell_vol"]
            bar_total = buy_vol + sell_vol

            total_imbalance += abs(buy_vol - sell_vol)
            total_volume += bar_total

        # Final VPIN
        vpin = total_imbalance / total_volume if total_volume > 0 else 0.5

        return float(max(0.0, min(1.0, vpin)))

    except Exception as e:
        sys.stderr.write(f"VPIN calc error: {e}\n")
        return 0.5


def estimate_order_imbalance(tick_prices: List[float], tick_volumes: List[int], lookback: int = 100) -> float:
    """
    Order imbalance: ratio of buy-initiated to total volume.
    High imbalance = buying pressure, Low = selling pressure.
    """
    try:
        if len(tick_prices) < lookback or len(tick_volumes) < lookback:
            return 0.5

        recent_prices = tick_prices[-lookback:]
        recent_volumes = tick_volumes[-lookback:]

        # Count buy vs sell ticks (tick direction)
        buy_count = 0
        for i in range(1, len(recent_prices)):
            if recent_prices[i] > recent_prices[i - 1]:
                buy_count += 1

        buy_ratio = buy_count / (len(recent_prices) - 1) if len(recent_prices) > 1 else 0.5

        return float(buy_ratio)

    except Exception as e:
        sys.stderr.write(f"Order imbalance error: {e}\n")
        return 0.5


def microstructure_signal(ticker: str, msg: Dict, ind: Dict) -> Optional[float]:
    """
    Composite microstructure signal [0-1].

    PRODUCTION VERSION (Session 19):
    - VPIN calculated from tick data with volume bars
    - Order imbalance from tick direction
    - High signal = informed trading detected
    """
    try:
        bid = msg.get("bid", 0)
        ask = msg.get("ask", 0)

        # Get tick data (recent ticks for VPIN calculation)
        tick_prices = msg.get("tick_prices_recent", msg.get("close_history", []))
        tick_volumes = msg.get("tick_volumes_recent", msg.get("volume_history", []))

        if len(tick_prices) < 100:
            return None

        # Component 1: Spread
        spread = calculate_bid_ask_spread_pct(bid, ask)

        # Component 2: VPIN (Session 19: true VPIN with volume bars)
        vpin = calculate_vpin_production(tick_prices, tick_volumes or [1] * len(tick_prices))

        # Component 3: Order imbalance
        imbalance = estimate_order_imbalance(tick_prices, tick_volumes or [1] * len(tick_prices))

        # Composite: High spread + high VPIN + extreme imbalance = informed trading
        composite = 0.2 * spread + 0.5 * vpin + 0.3 * abs(imbalance - 0.5)

        return max(0, min(1, composite))

    except ImportError:
        pass  # NumPy not available
    except Exception as e:
        sys.stderr.write(f"Microstructure signal error (non-fatal): {e}\n")
        return None
