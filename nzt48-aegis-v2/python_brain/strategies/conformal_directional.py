"""Book 144 upgrade: Conformal interval directional gate.

Hard gate: BUY only when ENTIRE prediction interval > +0.2%.
SELL only when ENTIRE interval < -0.2%. NO_TRADE when straddles zero.
Reject if interval width > 5%.

Consumed by:
- bridge.py _generate_signals() → flag NO_TRADE or enable with fraction
- bridge.py _apply_adjustments() → sizing modifier from fraction
"""


def check_directional_gate(interval_low, interval_high, min_return=0.002):
    """Check conformal prediction interval for directional clarity.

    Args:
        interval_low: Lower bound of prediction interval
        interval_high: Upper bound of prediction interval
        min_return: Minimum return threshold (default 0.2%)

    Returns:
        (direction, fraction) tuple:
        - direction: "BUY", "SELL", "NO_TRADE", or "REJECT"
        - fraction: position sizing fraction 0.0-1.0
    """
    if interval_low is None or interval_high is None:
        return "NO_TRADE", 0.0

    width = interval_high - interval_low

    # Reject if too uncertain
    if width > 0.05:  # 5% width
        return "REJECT", 0.0

    # Width = 0 means point estimate — no interval information
    if width <= 0:
        return "NO_TRADE", 0.0

    # BUY: entire interval above min_return
    if interval_low > min_return:
        fraction = min(1.0, interval_low / (width * 2)) if width > 0 else 0.5
        return "BUY", round(max(0.1, fraction), 3)

    # SELL: entire interval below -min_return (ISA: can't short, but flag for inverse ETPs)
    if interval_high < -min_return:
        fraction = min(1.0, abs(interval_high) / (width * 2)) if width > 0 else 0.5
        return "SELL", round(max(0.1, fraction), 3)

    # Straddles zero — no directional clarity
    return "NO_TRADE", 0.0
