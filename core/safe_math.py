"""
NZT-48 Core — Safe Math Utilities (W2)
Guards against division-by-zero and out-of-range values.
Feature flag: leverage_once_assertion in settings.yaml
"""

import logging

logger = logging.getLogger("nzt48.core.safe_math")


def safe_divide(numerator: float, denominator: float, fallback: float = 0.0, context: str = "") -> float:
    """Safe division with fallback value on zero denominator.

    Args:
        numerator: The dividend
        denominator: The divisor
        fallback: Value to return if denominator is zero (default 0.0)
        context: Description of where this division occurs (for logging)

    Returns:
        numerator/denominator, or fallback if denominator is zero/near-zero
    """
    if denominator == 0 or abs(denominator) < 1e-10:
        if context:
            logger.warning("DIV0_GUARD: %s — denominator=%.10f, returning fallback=%.4f", context, denominator, fallback)
        return fallback
    return numerator / denominator


def clamp_confidence(value: float, context: str = "") -> float:
    """Clamp confidence to [0, 100] range.

    Args:
        value: Raw confidence value
        context: Description of adjustment source

    Returns:
        Confidence clamped to [0.0, 100.0]
    """
    clamped = max(0.0, min(100.0, value))
    if clamped != value:
        logger.warning("CONFIDENCE_CLAMP: %s — raw=%.2f, clamped=%.2f", context, value, clamped)
    return clamped


def clamp_return_pct(value: float, session_type: str = "intraday", context: str = "") -> float:
    """Clamp return percentage to sane range.

    Thresholds:
        intraday: +-30%
        overnight: +-8%

    Returns:
        Clamped value, or original if within range
    """
    threshold = 30.0 if session_type == "intraday" else 8.0
    if abs(value) > threshold:
        logger.warning("RETURN_CLAMP: %s — raw=%.2f%%, threshold=+-%.1f%%, type=%s", context, value, threshold, session_type)
        return max(-threshold, min(threshold, value))
    return value
