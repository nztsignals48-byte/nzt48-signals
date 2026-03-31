"""Book 49/90: Per-order ADV participation limits.

Prevents being the market. At small account sizes this rarely triggers,
but it's the safety net for catastrophic illiquidity situations.

Consumed by: bridge.py _check_quality_gates() as hard gate.
"""

import time
from collections import defaultdict

# Daily cumulative tracking (resets at midnight UTC)
_daily_cumulative = defaultdict(float)  # symbol -> cumulative GBP traded today
_daily_date = ""


def _reset_if_new_day():
    """Reset daily counters at midnight UTC."""
    global _daily_date, _daily_cumulative
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today != _daily_date:
        _daily_cumulative.clear()
        _daily_date = today


def check_participation(symbol, order_size_gbp, adv_20d_gbp):
    """Check if order respects ADV participation limits.

    Returns (allowed: bool, scale_factor: float).
    - allowed=False: reject order entirely
    - scale_factor<1.0: reduce order size to stay within limits
    """
    if adv_20d_gbp <= 0:
        return True, 1.0  # No ADV data → fail-open

    _reset_if_new_day()

    # Per-order limit: 2% of ADV
    per_order_limit = adv_20d_gbp * 0.02
    if order_size_gbp > per_order_limit:
        # Can we scale down?
        scale = per_order_limit / order_size_gbp
        if scale < 0.3:
            return False, 0.0  # Would need >70% reduction — not viable
        return True, scale

    # Daily cumulative limit: 5% of ADV
    daily_limit = adv_20d_gbp * 0.05
    cumulative = _daily_cumulative.get(symbol, 0)
    if cumulative + order_size_gbp > daily_limit:
        remaining = daily_limit - cumulative
        if remaining <= 0:
            return False, 0.0  # Daily limit exhausted
        scale = remaining / order_size_gbp
        if scale < 0.3:
            return False, 0.0
        return True, scale

    return True, 1.0


def record_fill(symbol, filled_gbp):
    """Record a filled order for daily cumulative tracking."""
    _reset_if_new_day()
    _daily_cumulative[symbol] += filled_gbp
