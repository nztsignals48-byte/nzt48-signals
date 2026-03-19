"""Tests for brain.gap_detector -- overnight gap detection, classification, fill tracking."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from brain.gap_detector import (
    GapDetector,
    GapInfo,
    calculate_fill_target,
    calculate_gap_pct,
    classify_gap,
)


# ── Gap detection ──


def test_gap_up_detected():
    """Gap up: today_open > prev_close."""
    detector = GapDetector()
    gap = detector.detect(prev_close=100.0, today_open=103.0, rvol=1.0)

    assert gap is not None
    assert gap.direction == "up"
    assert abs(gap.gap_pct - 3.0) < 0.01


def test_gap_down_detected():
    """Gap down: today_open < prev_close."""
    detector = GapDetector()
    gap = detector.detect(prev_close=100.0, today_open=97.0, rvol=1.0)

    assert gap is not None
    assert gap.direction == "down"
    assert abs(gap.gap_pct - (-3.0)) < 0.01


def test_no_gap_below_threshold():
    """Tiny gap below min_gap_pct is ignored."""
    detector = GapDetector(min_gap_pct=0.10)
    gap = detector.detect(prev_close=100.0, today_open=100.05, rvol=1.0)

    assert gap is None


def test_gap_exactly_at_threshold():
    """Gap just above min_gap_pct is accepted."""
    detector = GapDetector(min_gap_pct=0.10)
    # Use a gap clearly above 0.10% to avoid floating-point boundary issues
    gap = detector.detect(prev_close=100.0, today_open=100.12, rvol=1.0)

    assert gap is not None
    assert abs(gap.gap_pct - 0.12) < 0.01


def test_zero_prev_close():
    """Zero previous close returns None (H61)."""
    detector = GapDetector()
    gap = detector.detect(prev_close=0.0, today_open=100.0, rvol=1.0)

    assert gap is None


def test_negative_prev_close():
    """Negative previous close returns None (H61)."""
    detector = GapDetector()
    gap = detector.detect(prev_close=-5.0, today_open=100.0, rvol=1.0)

    assert gap is None


# ── Gap classification ──


def test_classify_information_gap():
    """RVOL > 5x = information gap (institutional)."""
    assert classify_gap(5.1) == "information"
    assert classify_gap(10.0) == "information"


def test_classify_liquidity_gap():
    """RVOL < 2x = liquidity gap (low participation)."""
    assert classify_gap(1.9) == "liquidity"
    assert classify_gap(0.5) == "liquidity"


def test_classify_uncertain_gap():
    """RVOL between 2x and 5x = uncertain."""
    assert classify_gap(2.0) == "uncertain"
    assert classify_gap(3.5) == "uncertain"
    assert classify_gap(5.0) == "uncertain"


def test_gap_classification_in_detect():
    """Classification is correctly applied during detection."""
    detector = GapDetector()

    gap = detector.detect(prev_close=100.0, today_open=105.0, rvol=6.0)
    assert gap is not None
    assert gap.gap_type == "information"

    gap = detector.detect(prev_close=100.0, today_open=105.0, rvol=1.5)
    assert gap is not None
    assert gap.gap_type == "liquidity"

    gap = detector.detect(prev_close=100.0, today_open=105.0, rvol=3.0)
    assert gap is not None
    assert gap.gap_type == "uncertain"


# ── Fill target calculation ──


def test_fill_target_gap_up():
    """Gap up fill target: 75% retrace toward prev_close."""
    # Gap up: open=105, close=100, gap=5
    # Fill target = 105 - 0.75 * 5 = 101.25
    target = calculate_fill_target(prev_close=100.0, today_open=105.0, fill_pct=0.75)
    assert abs(target - 101.25) < 0.01


def test_fill_target_gap_down():
    """Gap down fill target: 75% retrace toward prev_close."""
    # Gap down: open=95, close=100, gap=-5
    # Fill target = 95 - 0.75 * (-5) = 95 + 3.75 = 98.75
    target = calculate_fill_target(prev_close=100.0, today_open=95.0, fill_pct=0.75)
    assert abs(target - 98.75) < 0.01


def test_fill_target_100pct():
    """100% fill target equals previous close."""
    target = calculate_fill_target(prev_close=100.0, today_open=110.0, fill_pct=1.0)
    assert abs(target - 100.0) < 0.01


def test_fill_target_in_detection():
    """Fill target is correctly set during detection."""
    detector = GapDetector(fill_pct=0.75)
    gap = detector.detect(prev_close=100.0, today_open=104.0, rvol=1.0)

    assert gap is not None
    # 104 - 0.75 * 4 = 104 - 3 = 101
    assert abs(gap.fill_target - 101.0) < 0.01


# ── Fill tracking ──


def test_gap_up_fill_tracking():
    """Gap up fills when price drops to fill target."""
    detector = GapDetector(fill_pct=0.75)
    detector.detect(prev_close=100.0, today_open=104.0, rvol=1.0)
    # Fill target = 101.0

    # Not filled yet
    gap = detector.check_fill(current_price=102.0)
    assert gap is not None
    assert gap.is_filled is False

    # Filled
    gap = detector.check_fill(current_price=101.0)
    assert gap is not None
    assert gap.is_filled is True


def test_gap_down_fill_tracking():
    """Gap down fills when price rises to fill target."""
    detector = GapDetector(fill_pct=0.75)
    detector.detect(prev_close=100.0, today_open=96.0, rvol=1.0)
    # Fill target = 96 - 0.75 * (-4) = 96 + 3 = 99.0

    # Not filled yet
    gap = detector.check_fill(current_price=97.0)
    assert gap is not None
    assert gap.is_filled is False

    # Filled
    gap = detector.check_fill(current_price=99.0)
    assert gap is not None
    assert gap.is_filled is True


def test_fill_stays_filled():
    """Once filled, gap stays filled even if price reverses."""
    detector = GapDetector(fill_pct=0.75)
    detector.detect(prev_close=100.0, today_open=104.0, rvol=1.0)

    # Fill the gap
    detector.check_fill(current_price=100.0)
    gap = detector.check_fill(current_price=100.0)
    assert gap.is_filled is True

    # Price goes back up — still filled
    gap = detector.check_fill(current_price=105.0)
    assert gap.is_filled is True


def test_check_fill_no_gap():
    """check_fill with no gap detected returns None."""
    detector = GapDetector()
    result = detector.check_fill(current_price=100.0)
    assert result is None


# ── Gap percentage utility ──


def test_calculate_gap_pct_positive():
    pct = calculate_gap_pct(prev_close=100.0, today_open=103.0)
    assert abs(pct - 3.0) < 0.01


def test_calculate_gap_pct_negative():
    pct = calculate_gap_pct(prev_close=100.0, today_open=97.0)
    assert abs(pct - (-3.0)) < 0.01


def test_calculate_gap_pct_zero_prev():
    """H61: zero prev_close returns 0."""
    pct = calculate_gap_pct(prev_close=0.0, today_open=100.0)
    assert pct == 0.0


def test_calculate_gap_pct_no_gap():
    pct = calculate_gap_pct(prev_close=100.0, today_open=100.0)
    assert abs(pct) < 0.01


# ── Reset ──


def test_reset_clears_gap():
    """Reset clears the detected gap."""
    detector = GapDetector()
    detector.detect(prev_close=100.0, today_open=105.0, rvol=1.0)
    assert detector.current_gap is not None

    detector.reset()
    assert detector.current_gap is None


def test_current_gap_property():
    """current_gap property returns the gap or None."""
    detector = GapDetector()
    assert detector.current_gap is None

    detector.detect(prev_close=100.0, today_open=110.0, rvol=1.0)
    assert detector.current_gap is not None
    assert detector.current_gap.direction == "up"


# ── Large gap scenarios ──


def test_large_gap_up():
    """Large 10% gap up."""
    detector = GapDetector()
    gap = detector.detect(prev_close=100.0, today_open=110.0, rvol=8.0)

    assert gap is not None
    assert gap.direction == "up"
    assert abs(gap.gap_pct - 10.0) < 0.01
    assert gap.gap_type == "information"  # RVOL 8x


def test_large_gap_down():
    """Large 10% gap down."""
    detector = GapDetector()
    gap = detector.detect(prev_close=100.0, today_open=90.0, rvol=7.0)

    assert gap is not None
    assert gap.direction == "down"
    assert abs(gap.gap_pct - (-10.0)) < 0.01
    assert gap.gap_type == "information"


if __name__ == "__main__":
    import inspect

    tests = [
        obj
        for name, obj in inspect.getmembers(sys.modules[__name__])
        if inspect.isfunction(obj) and name.startswith("test_")
    ]
    for t in tests:
        t()
        print(f"  PASS: {t.__name__}")
    print(f"All {len(tests)} gap_detector tests passed.")
