"""
NZT-48 Core — Sanity Gates (W1)
Fail-closed gates that prevent impossible data from reaching output channels.
Feature flag: sanity_gate_v2 in settings.yaml
"""

import logging

logger = logging.getLogger("nzt48.core.sanity_gates")


def gate_return_magnitude(return_pct: float, session_type: str = "intraday") -> tuple[bool, str]:
    """Block impossible return magnitudes.

    Thresholds:
        intraday: ±30% (even 3x leveraged LSE ETPs rarely exceed 25%)
        overnight: ±8% (futures-implied moves)

    Returns:
        (passed, tag) — tag is empty string if passed, failure code if blocked
    """
    if session_type == "intraday":
        threshold = 30.0
    elif session_type == "overnight":
        threshold = 8.0
    else:
        threshold = 30.0

    if abs(return_pct) > threshold:
        tag = f"SANITY_FAIL_MAGNITUDE: {return_pct:+.2f}% exceeds ±{threshold}% ({session_type})"
        logger.warning(tag)
        return False, tag
    return True, ""


def gate_score_floor(score: float, minimum: float = 10.0) -> tuple[bool, str]:
    """Block signals with composite score below minimum.

    Returns:
        (passed, tag)
    """
    if score is None or (isinstance(score, (int, float)) and score < minimum):
        tag = f"SANITY_FAIL_SCORE_FLOOR: score={score} < minimum={minimum}"
        logger.warning(tag)
        return False, tag
    return True, ""


def gate_confidence_range(confidence: float) -> tuple[bool, str]:
    """Block confidence values outside [0, 100].

    Returns:
        (passed, tag)
    """
    if confidence is None or not isinstance(confidence, (int, float)):
        return False, "SANITY_FAIL_CONFIDENCE: None or non-numeric"
    if confidence < 0 or confidence > 100:
        tag = f"SANITY_FAIL_CONFIDENCE_RANGE: {confidence} outside [0, 100]"
        logger.warning(tag)
        return False, tag
    return True, ""


def gate_price_positive(price: float, ticker: str = "") -> tuple[bool, str]:
    """Block zero or negative prices.

    Returns:
        (passed, tag)
    """
    if price is None or not isinstance(price, (int, float)) or price <= 0:
        tag = f"SANITY_FAIL_PRICE: {ticker} price={price} (must be > 0)"
        logger.warning(tag)
        return False, tag
    return True, ""


def gate_stop_below_entry(entry: float, stop: float, direction: str = "LONG") -> tuple[bool, str]:
    """Block signals where stop is on wrong side of entry.

    For LONG: stop must be below entry
    For SHORT: stop must be above entry

    Returns:
        (passed, tag)
    """
    if direction == "LONG" and stop >= entry:
        tag = f"SANITY_FAIL_STOP: LONG but stop={stop} >= entry={entry}"
        logger.warning(tag)
        return False, tag
    if direction == "SHORT" and stop <= entry:
        tag = f"SANITY_FAIL_STOP: SHORT but stop={stop} <= entry={entry}"
        logger.warning(tag)
        return False, tag
    return True, ""


def run_signal_sanity_gates(signal_dict: dict) -> tuple[bool, list[str]]:
    """Run all sanity gates on a signal dict. Returns (all_passed, list_of_failure_tags)."""
    failures = []

    # Score floor
    score = signal_dict.get("composite_score") or signal_dict.get("score") or signal_dict.get("confidence", 0)
    passed, tag = gate_score_floor(score)
    if not passed:
        failures.append(tag)

    # Confidence range
    conf = signal_dict.get("confidence", 0)
    if conf is not None:
        passed, tag = gate_confidence_range(conf)
        if not passed:
            failures.append(tag)

    # Price positive
    entry = signal_dict.get("entry", 0)
    ticker = signal_dict.get("ticker", "")
    passed, tag = gate_price_positive(entry, ticker)
    if not passed:
        failures.append(tag)

    # Stop sanity
    stop = signal_dict.get("stop", 0)
    direction = signal_dict.get("direction", "LONG")
    if entry > 0 and stop > 0:
        passed, tag = gate_stop_below_entry(entry, stop, direction)
        if not passed:
            failures.append(tag)

    return len(failures) == 0, failures
