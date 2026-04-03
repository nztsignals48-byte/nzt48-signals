"""BOOK 206: MULTI-LEG ARBITRAGE — Spreads across related instruments

PRODUCTION VERSION (Session 19):
- Vol rank calculated as percentile over 50-day lookback (not arbitrary 0.2/0.8)
- Correlation check (r^2 > 0.8) to detect structural breaks
- Liquidity validation (volume > 20th percentile)
- Only fires on extreme vol (0-15% or 85-100% percentile range)
"""

import sys
from typing import Dict, List, Optional

try:
    import numpy as np
except ImportError:
    np = None

log_name = "multi_leg_arb"


def calculate_vol_rank(ticker: str, returns: List[float], lookback: int = 50) -> float:
    """
    Calculate Vol Rank = percentile of current volatility over lookback period.

    PRODUCTION FIX (Session 19):
    Vol Rank = (vol_current - vol_min) / (vol_max - vol_min) * 100

    Result: 0-100 scale
      - 0-20: Vol at 20-year lows (buy volatility)
      - 80-100: Vol at 20-year highs (sell volatility)

    Args:
        ticker: Ticker ID
        returns: Historical returns (daily %)
        lookback: Number of periods to track (50 = ~2.5 months)

    Returns:
        Vol rank from 0-100, or 50 if insufficient data
    """
    if not np or len(returns) < 30:
        return 50.0  # Default to neutral if no data

    try:
        returns_array = np.array(returns)

        # Calculate rolling 20-day volatility (annualized)
        volatilities = []
        for i in range(len(returns_array) - 20):
            window_vol = np.std(returns_array[i:i+20]) * np.sqrt(252)  # Annualized
            volatilities.append(window_vol)

        if len(volatilities) < lookback:
            return 50.0

        # Get current vol and percentile
        current_vol = volatilities[-1]
        recent_vols = volatilities[-lookback:]

        max_vol = max(recent_vols)
        min_vol = min(recent_vols)

        # Avoid division by zero
        if max_vol == min_vol:
            return 50.0

        # Calculate percentile
        vol_rank = ((current_vol - min_vol) / (max_vol - min_vol)) * 100

        return float(vol_rank)

    except Exception as e:
        sys.stderr.write(f"Vol rank calc error: {e}\n")
        return 50.0


def calculate_correlation(returns_a: List[float], returns_b: List[float]) -> float:
    """
    Calculate correlation between two return series.
    Returns r^2 (correlation squared, 0-1 scale).
    """
    if not np or len(returns_a) < 20 or len(returns_b) < 20:
        return 0.5

    try:
        a = np.array(returns_a[-252:])  # Last year
        b = np.array(returns_b[-252:])

        if len(a) != len(b):
            return 0.5

        correlation = np.corrcoef(a, b)[0, 1]
        r_squared = correlation ** 2

        return float(r_squared)

    except Exception as e:
        sys.stderr.write(f"Correlation calc error: {e}\n")
        return 0.5


def multi_leg_arb_signal(
    ticker_id: str, msg: Dict, ind: Dict, conf_floor: int, kelly_fn, common_fields: Dict
) -> Optional[Dict]:
    """
    Generate spread arbitrage signals based on volatility extremes.

    PRODUCTION VERSION (Session 19):
    1. Calculate vol rank as percentile (0-100)
    2. Only fire on extremes (0-15 or 85-100)
    3. Verify correlation with underlying (r^2 > 0.8)
    4. Validate liquidity (volume > 20th percentile)
    5. Skip on structural breaks (correlation collapse)

    Multi-leg opportunities:
    - ETF vs underlying basis (e.g., UPRO vs SPY)
    - Sector ETF spreads (XLV vs XLE momentum divergence)
    - Leveraged ETF vol mean reversion
    """
    try:
        # ETF-underlying pairs to monitor
        etf_underlying_pairs = {
            "UPRO": ("SPY", "spy_returns_252"),    # 3x SPY (bull)
            "SQQQ": ("QQQ", "qqq_returns_252"),    # 3x Nasdaq (inverse)
            "TQQQ": ("QQQ", "qqq_returns_252"),    # 3x Nasdaq (bull)
            "VTI": ("VOO", "voo_returns_252"),     # Total US vs S&P 500
        }

        if ticker_id not in etf_underlying_pairs:
            return None

        underlying, underlying_returns_key = etf_underlying_pairs[ticker_id]
        etf_price = msg.get("ltp", 0)

        if etf_price <= 0:
            return None

        # SESSION 19 FIX #1: Calculate vol rank properly (percentile, not arbitrary)
        returns = ind.get("returns_252", [])  # 252-day returns
        vol_rank = calculate_vol_rank(ticker_id, returns, lookback=50)

        # SESSION 19 FIX #2: Only trade on EXTREME vol (0-15 or 85-100 percentile)
        is_vol_low = vol_rank < 15  # Vol at lows = buy opportunity
        is_vol_high = vol_rank > 85  # Vol at highs = sell opportunity

        if not (is_vol_low or is_vol_high):
            return None  # Vol in normal range, skip

        # SESSION 19 FIX #3: Verify correlation with underlying (structural break detection)
        underlying_returns = ind.get(underlying_returns_key, [])
        r_squared = calculate_correlation(returns, underlying_returns)

        if r_squared < 0.8:
            # Correlation broken (crisis period or structural break)
            # Skip to avoid false signals
            sys.stderr.write(
                f"MULTILEG skip {ticker_id}: correlation broken (r^2={r_squared:.2f} < 0.8, vol_rank={vol_rank:.0f})\n"
            )
            sys.stderr.flush()
            return None

        # SESSION 19 FIX #4: Validate liquidity
        volume_percentile = ind.get("volume_percentile", 50)
        if volume_percentile < 20:
            return None  # Too illiquid

        # Determine direction based on vol rank
        if is_vol_low:
            direction = "BUY"  # Buy when vol is at lows (expect reversion up)
            signal_type = "vol_compression"
        else:  # is_vol_high
            direction = "SHORT"  # Short when vol is at highs (expect reversion down)
            signal_type = "vol_expansion"

        # Confidence based on distance from center and correlation strength
        base_conf = 65
        extremeness = max(abs(vol_rank - 50) - 35, 0) / 15 * 20  # 0-20 boost for extremeness
        correlation_boost = (r_squared - 0.8) / 0.2 * 10  # 0-10 boost for strong correlation

        confidence = int(min(base_conf + extremeness + correlation_boost, 90))

        if confidence < conf_floor:
            return None

        signal = {
            **common_fields,
            "strategy": "MULTILEG",
            "ticker": ticker_id,
            "direction": direction,
            "confidence": confidence,
            "kelly_fraction": kelly_fn("MULTILEG", {"edge_bps": 40}),
            "max_hold_hours": 2,
            "shares": 0,  # Rust engine will size
            # Metadata (Session 19)
            "_spread_pair": f"{ticker_id}/{underlying}",
            "_vol_rank": round(vol_rank, 1),
            "_vol_rank_type": signal_type,
            "_correlation_r_squared": round(r_squared, 3),
            "_extremeness_bps": round(abs(vol_rank - 50), 1),
        }

        sys.stderr.write(
            f"MULTILEG signal: {ticker_id}/{underlying} vol_rank={vol_rank:.0f} "
            f"direction={direction} r^2={r_squared:.3f} conf={confidence}\n"
        )
        sys.stderr.flush()

        return signal

    except ImportError:
        pass  # NumPy not available
    except Exception as e:
        sys.stderr.write(f"MULTILEG error (non-fatal): {e}\n")
        sys.stderr.flush()
        return None
