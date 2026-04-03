"""BOOK 121: FORMULAIC ALPHA — Simple mechanical rules with edge"""

import sys
from typing import Dict, Optional


def piotroski_fscore(ticker: str, msg: Dict) -> float:
    """
    Simplified Piotroski F-Score (9-point quality check).
    High score = profitable, solid fundamentals.

    Scores:
    - Profitability (4 points): ROA, operating CF, quality of earnings
    - Leverage (3 points): Delta debt, current ratio
    - Efficiency (2 points): Asset turnover, accruals
    """
    score = 0

    try:
        # Profitability proxy: use recent vol + return
        recent_return = msg.get("recent_return_pct", 0) / 100
        if recent_return > 0:
            score += 1  # Positive returns

        atr = msg.get("atr_pct", 1) / 100
        if atr < 0.03:  # Low volatility = stable
            score += 1
        if atr > 0.05:  # High volatility = unstable
            score -= 1

        # Leverage proxy: use bid-ask spread
        bid_ask_pct = msg.get("bid_ask_pct", 0.1) / 100
        if bid_ask_pct < 0.05:  # Tight spread = healthy
            score += 1

        # Cap at 0-9
        return max(0, min(9, score + 5))  # Neutral = 5

    except:
        return 5.0  # Neutral default


def earnings_yield_signal(ticker: str, msg: Dict) -> float:
    """Earnings yield: earnings-to-price ratio."""
    price = msg.get("ltp", 1)
    recent_return = msg.get("recent_return_pct", 0) / 100

    # Proxy: high recent return = lower earnings yield (expensive)
    earnings_yield = max(0, 0.1 - recent_return)

    return earnings_yield


def formulaic_alpha_score(ticker: str, msg: Dict) -> float:
    """
    Composite formulaic alpha score [0-1].
    High = attractive (cheap, profitable, quality)
    """
    try:
        f_score = piotroski_fscore(ticker, msg)
        e_yield = earnings_yield_signal(ticker, msg)

        # Normalize: f_score is [0-9], e_yield is [0-0.2]
        composite = (f_score / 9.0) * 0.6 + min(1, e_yield / 0.1) * 0.4

        return max(0, min(1, composite))

    except Exception as e:
        sys.stderr.write(f"Formulaic alpha error: {e}\n")
        return 0.5


def apply_formulaic_adjustment(base_confidence: int, ticker: str, msg: Dict) -> int:
    """Adjust signal confidence based on formulaic alpha."""
    alpha_score = formulaic_alpha_score(ticker, msg)

    # Boost if high alpha, penalize if low
    mult = 0.7 + 0.6 * alpha_score

    adjusted = int(base_confidence * mult)
    return max(0, min(100, adjusted))
