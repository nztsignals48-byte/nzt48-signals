"""BOOK 168: STATISTICAL ARBITRAGE — Pairs trading with cointegration"""

import sys
from typing import Dict, List, Optional


def test_cointegration(price_a: List[float], price_b: List[float], lookback: int = 252) -> float:
    """
    Test if two prices are cointegrated (move together long-term).

    Returns: Cointegration score [0-1]
    0 = not cointegrated (independent)
    1 = strongly cointegrated (move together)
    """
    try:
        if len(price_a) < lookback or len(price_b) < lookback:
            return 0.5

        recent_a = price_a[-lookback:]
        recent_b = price_b[-lookback:]

        # Simplified: compute correlation coefficient
        mean_a = sum(recent_a) / len(recent_a)
        mean_b = sum(recent_b) / len(recent_b)

        covariance = sum((recent_a[i] - mean_a) * (recent_b[i] - mean_b) for i in range(len(recent_a))) / len(recent_a)
        var_a = sum((x - mean_a) ** 2 for x in recent_a) / len(recent_a)
        var_b = sum((x - mean_b) ** 2 for x in recent_b) / len(recent_b)

        if var_a == 0 or var_b == 0:
            return 0.5

        correlation = covariance / (var_a * var_b) ** 0.5

        return max(0, min(1, correlation))

    except Exception as e:
        sys.stderr.write(f"Cointegration test error: {e}\n")
        return 0.5


def calculate_hedge_ratio(price_a: List[float], price_b: List[float]) -> float:
    """
    Calculate optimal hedge ratio via OLS regression.
    Ratio = beta = cov(A,B) / var(B)
    """
    try:
        if len(price_a) < 20 or len(price_b) < 20:
            return 1.0

        recent_a = price_a[-100:]
        recent_b = price_b[-100:]

        mean_a = sum(recent_a) / len(recent_a)
        mean_b = sum(recent_b) / len(recent_b)

        numerator = sum((recent_a[i] - mean_a) * (recent_b[i] - mean_b) for i in range(len(recent_a)))
        denominator = sum((recent_b[i] - mean_b) ** 2 for i in range(len(recent_b)))

        if denominator == 0:
            return 1.0

        hedge_ratio = numerator / denominator

        return max(0.5, min(2.0, hedge_ratio))  # Cap between 0.5-2x

    except Exception as e:
        sys.stderr.write(f"Hedge ratio error: {e}\n")
        return 1.0


def generate_spread_signal(ticker_a: str, ticker_b: str, price_a: float, price_b: float, hedge_ratio: float) -> Optional[str]:
    """
    Generate pair signal based on spread Z-score.

    Returns: "BUY", "SELL", or None
    """
    try:
        if price_a <= 0 or price_b <= 0:
            return None

        # Spread = price_a - hedge_ratio * price_b
        spread = price_a - hedge_ratio * price_b

        # Simple: if spread > 0 and pair is cointegrated, short A / long B
        # if spread < 0, long A / short B
        if abs(spread) > 0.05 * max(price_a, price_b):
            return "SELL" if spread > 0 else "BUY"

        return None

    except Exception as e:
        sys.stderr.write(f"Spread signal error: {e}\n")
        return None


def pairs_trading_signal(
    ticker_id: str, msg: Dict, ind: Dict, conf_floor: int, kelly_fn, common_fields: Dict
) -> Optional[Dict]:
    """
    Generate pairs trading signal if cointegrated pair exists.

    Pairs to monitor: sector stocks, indices, ETFs
    """
    try:
        # Known cointegrated pairs (in production: discover dynamically)
        known_pairs = {
            "UPRO": ("SPY", 3),  # 3x SPY
            "VTI": ("VOO", 1),  # Total market vs S&P 500
        }

        if ticker_id not in known_pairs:
            return None

        paired_ticker, ratio = known_pairs[ticker_id]

        # Get current price
        ltp = msg.get("ltp", 0)
        if ltp <= 0:
            return None

        # In production: fetch paired_ticker's price from market data
        # For now: simulate
        paired_price = ltp / ratio

        # Test cointegration
        close_history = msg.get("close_history", [])
        if len(close_history) < 50:
            return None

        # Simulate paired history
        paired_history = [p / ratio for p in close_history]

        coint_score = test_cointegration(close_history, paired_history)

        if coint_score < 0.6:
            return None  # Not cointegrated

        # Calculate hedge ratio
        hedge = calculate_hedge_ratio(close_history, paired_history)

        # Generate signal
        signal_dir = generate_spread_signal(ticker_id, paired_ticker, ltp, paired_price, hedge)

        if not signal_dir:
            return None

        confidence = int(50 + 40 * coint_score)

        if confidence < conf_floor:
            return None

        signal = {
            **common_fields,
            "strategy": "PAIRS",
            "ticker": ticker_id,
            "direction": signal_dir,
            "confidence": confidence,
            "kelly_fraction": kelly_fn("PAIRS", {"edge_bps": 20}),
            "max_hold_hours": 4,
            "_pairs_pair": paired_ticker,
            "_hedge_ratio": hedge,
            "_cointegration": coint_score,
        }

        sys.stderr.write(f"PAIRS signal: {ticker_id}/{paired_ticker} coint={coint_score:.2f} conf={confidence}\n")
        sys.stderr.flush()

        return signal

    except Exception as e:
        sys.stderr.write(f"PAIRS error: {e}\n")
        return None
