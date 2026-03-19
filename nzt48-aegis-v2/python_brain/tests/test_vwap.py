"""Tests for brain.vwap -- VWAP calculator with bands, slope, and volume profile."""

import sys
import os
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from brain.vwap import VWAPBar, VWAPCalculator, VWAPResult, is_at_sigma_band, is_beyond_sigma_band


# ── Basic VWAP calculation ──


def test_vwap_single_bar():
    """VWAP of a single bar = typical price."""
    calc = VWAPCalculator()
    calc.reset()
    bar = VWAPBar(high=105.0, low=95.0, close=100.0, volume=1000.0)
    result = calc.update(bar)

    assert result is not None
    expected_tp = (105.0 + 95.0 + 100.0) / 3.0  # 100.0
    assert abs(result.vwap - expected_tp) < 1e-9, f"Expected {expected_tp}, got {result.vwap}"


def test_vwap_two_bars_equal_volume():
    """Two bars with equal volume: VWAP = average of typical prices."""
    calc = VWAPCalculator()
    calc.reset()

    bar1 = VWAPBar(high=110.0, low=90.0, close=100.0, volume=500.0)
    bar2 = VWAPBar(high=120.0, low=100.0, close=110.0, volume=500.0)

    calc.update(bar1)
    result = calc.update(bar2)

    tp1 = (110.0 + 90.0 + 100.0) / 3.0  # 100.0
    tp2 = (120.0 + 100.0 + 110.0) / 3.0  # 110.0
    expected_vwap = (tp1 * 500 + tp2 * 500) / (500 + 500)  # 105.0

    assert result is not None
    assert abs(result.vwap - expected_vwap) < 1e-9


def test_vwap_volume_weighted():
    """Volume weighting: high-volume bar dominates."""
    calc = VWAPCalculator()
    calc.reset()

    # Low-volume bar at 100
    bar1 = VWAPBar(high=105.0, low=95.0, close=100.0, volume=100.0)
    # High-volume bar at 200
    bar2 = VWAPBar(high=210.0, low=190.0, close=200.0, volume=10000.0)

    calc.update(bar1)
    result = calc.update(bar2)

    tp1 = (105.0 + 95.0 + 100.0) / 3.0
    tp2 = (210.0 + 190.0 + 200.0) / 3.0
    expected_vwap = (tp1 * 100 + tp2 * 10000) / (100 + 10000)

    assert result is not None
    # VWAP should be very close to tp2 due to volume dominance
    assert abs(result.vwap - expected_vwap) < 1e-9
    assert result.vwap > 195.0  # Dominated by the high-volume bar


def test_vwap_zero_volume():
    """Zero total volume returns None."""
    calc = VWAPCalculator()
    calc.reset()
    bar = VWAPBar(high=100.0, low=90.0, close=95.0, volume=0.0)
    result = calc.update(bar)
    assert result is None


# ── Standard deviation bands ──


def test_bands_symmetry():
    """Bands are symmetric around VWAP."""
    calc = VWAPCalculator()
    calc.reset()

    bars = [
        VWAPBar(high=110.0, low=90.0, close=100.0, volume=1000.0),
        VWAPBar(high=115.0, low=95.0, close=105.0, volume=1000.0),
        VWAPBar(high=108.0, low=88.0, close=98.0, volume=1000.0),
    ]
    for bar in bars:
        result = calc.update(bar)

    assert result is not None
    # Check symmetry
    assert abs((result.upper_1 - result.vwap) - (result.vwap - result.lower_1)) < 1e-9
    assert abs((result.upper_2 - result.vwap) - (result.vwap - result.lower_2)) < 1e-9
    assert abs((result.upper_3 - result.vwap) - (result.vwap - result.lower_3)) < 1e-9


def test_bands_ordering():
    """Band ordering: lower_3 < lower_2 < lower_1 < VWAP < upper_1 < upper_2 < upper_3."""
    calc = VWAPCalculator()
    calc.reset()

    bars = [
        VWAPBar(high=110.0, low=90.0, close=100.0, volume=1000.0),
        VWAPBar(high=120.0, low=95.0, close=108.0, volume=2000.0),
        VWAPBar(high=105.0, low=85.0, close=92.0, volume=1500.0),
    ]
    for bar in bars:
        result = calc.update(bar)

    assert result is not None
    assert result.lower_3 < result.lower_2 < result.lower_1 < result.vwap
    assert result.vwap < result.upper_1 < result.upper_2 < result.upper_3


def test_bands_flat_price_zero_stddev():
    """Flat price bars produce zero-width bands (all equal to VWAP)."""
    calc = VWAPCalculator()
    calc.reset()

    for _ in range(10):
        bar = VWAPBar(high=100.0, low=100.0, close=100.0, volume=500.0)
        result = calc.update(bar)

    assert result is not None
    # All typical prices are identical, so std_dev = 0
    assert abs(result.upper_1 - result.vwap) < 1e-9
    assert abs(result.lower_1 - result.vwap) < 1e-9


def test_custom_sigma_levels():
    """Custom sigma levels produce corresponding bands."""
    calc = VWAPCalculator(sigma_levels=(0.5, 1.5, 2.5))
    calc.reset()

    bars = [
        VWAPBar(high=110.0, low=90.0, close=100.0, volume=1000.0),
        VWAPBar(high=120.0, low=95.0, close=110.0, volume=1000.0),
    ]
    for bar in bars:
        result = calc.update(bar)

    assert result is not None
    # 0.5-sigma band should be narrower than 1.5-sigma, etc.
    width_1 = result.upper_1 - result.lower_1
    width_2 = result.upper_2 - result.lower_2
    width_3 = result.upper_3 - result.lower_3
    assert width_1 < width_2 < width_3


# ── Slope calculation ──


def test_slope_flat_vwap():
    """Flat VWAP (constant price & volume) produces zero slope."""
    calc = VWAPCalculator(slope_window=5)
    calc.reset()

    for _ in range(10):
        bar = VWAPBar(high=100.0, low=100.0, close=100.0, volume=1000.0)
        result = calc.update(bar)

    assert result is not None
    assert abs(result.slope) < 1e-9


def test_slope_rising_vwap():
    """Rising prices produce positive VWAP slope."""
    calc = VWAPCalculator(slope_window=5)
    calc.reset()

    for i in range(10):
        price = 100.0 + i * 2.0
        bar = VWAPBar(high=price + 1.0, low=price - 1.0, close=price, volume=1000.0)
        result = calc.update(bar)

    assert result is not None
    assert result.slope > 0.0, f"Expected positive slope, got {result.slope}"


def test_slope_falling_vwap():
    """Falling prices produce negative VWAP slope."""
    calc = VWAPCalculator(slope_window=5)
    calc.reset()

    for i in range(10):
        price = 200.0 - i * 3.0
        bar = VWAPBar(high=price + 1.0, low=price - 1.0, close=price, volume=1000.0)
        result = calc.update(bar)

    assert result is not None
    assert result.slope < 0.0, f"Expected negative slope, got {result.slope}"


def test_slope_single_bar():
    """Single bar has zero slope (insufficient data for regression)."""
    calc = VWAPCalculator(slope_window=5)
    calc.reset()

    bar = VWAPBar(high=105.0, low=95.0, close=100.0, volume=1000.0)
    result = calc.update(bar)

    assert result is not None
    assert abs(result.slope) < 1e-9


# ── Sigma position ──


def test_sigma_position_at_vwap():
    """Price at VWAP has sigma_position = 0."""
    calc = VWAPCalculator()
    calc.reset()

    # Create bars where close = typical price ≈ VWAP
    bar = VWAPBar(high=100.0, low=100.0, close=100.0, volume=1000.0)
    result = calc.update(bar)

    assert result is not None
    assert abs(result.sigma_position) < 1e-9


def test_sigma_position_above_vwap():
    """Close above VWAP produces positive sigma_position."""
    calc = VWAPCalculator()
    calc.reset()

    # First bar establishes VWAP near 100
    bar1 = VWAPBar(high=105.0, low=95.0, close=100.0, volume=10000.0)
    calc.update(bar1)

    # Second bar with close well above VWAP
    bar2 = VWAPBar(high=130.0, low=110.0, close=120.0, volume=100.0)
    result = calc.update(bar2)

    assert result is not None
    assert result.sigma_position > 0.0


def test_sigma_position_below_vwap():
    """Close below VWAP produces negative sigma_position."""
    calc = VWAPCalculator()
    calc.reset()

    # First bar establishes VWAP near 100
    bar1 = VWAPBar(high=105.0, low=95.0, close=100.0, volume=10000.0)
    calc.update(bar1)

    # Second bar with close well below VWAP
    bar2 = VWAPBar(high=85.0, low=75.0, close=80.0, volume=100.0)
    result = calc.update(bar2)

    assert result is not None
    assert result.sigma_position < 0.0


# ── Volume profile ──


def test_volume_profile_accelerating():
    """Increasing volume classified as accelerating."""
    calc = VWAPCalculator(volume_short_window=5, volume_long_window=20)
    calc.reset()

    # 20 bars of low volume, then 5 bars of high volume
    for i in range(20):
        bar = VWAPBar(high=101.0, low=99.0, close=100.0, volume=100.0)
        calc.update(bar)

    for i in range(5):
        bar = VWAPBar(high=101.0, low=99.0, close=100.0, volume=500.0)
        result = calc.update(bar)

    assert result is not None
    assert result.volume_profile == "accelerating"


def test_volume_profile_declining():
    """Decreasing volume classified as declining."""
    calc = VWAPCalculator(volume_short_window=5, volume_long_window=20)
    calc.reset()

    # 20 bars of high volume, then 5 bars of very low volume
    for i in range(20):
        bar = VWAPBar(high=101.0, low=99.0, close=100.0, volume=1000.0)
        calc.update(bar)

    for i in range(5):
        bar = VWAPBar(high=101.0, low=99.0, close=100.0, volume=100.0)
        result = calc.update(bar)

    assert result is not None
    assert result.volume_profile == "declining"


def test_volume_profile_neutral():
    """Constant volume classified as neutral."""
    calc = VWAPCalculator(volume_short_window=5, volume_long_window=20)
    calc.reset()

    for i in range(25):
        bar = VWAPBar(high=101.0, low=99.0, close=100.0, volume=1000.0)
        result = calc.update(bar)

    assert result is not None
    assert result.volume_profile == "neutral"


def test_volume_profile_insufficient_data():
    """Insufficient data returns neutral."""
    calc = VWAPCalculator(volume_short_window=5, volume_long_window=20)
    calc.reset()

    bar = VWAPBar(high=101.0, low=99.0, close=100.0, volume=1000.0)
    result = calc.update(bar)

    assert result is not None
    assert result.volume_profile == "neutral"


# ── Session reset ──


def test_session_reset():
    """Reset clears all cumulative state."""
    calc = VWAPCalculator()
    calc.reset()

    for i in range(10):
        bar = VWAPBar(high=110.0, low=90.0, close=100.0, volume=1000.0)
        calc.update(bar)

    assert calc._bar_count == 10
    assert calc._cum_vol > 0.0

    calc.reset()

    assert calc._bar_count == 0
    assert calc._cum_vol == 0.0
    assert calc._cum_tp_vol == 0.0
    assert calc._cum_tp2_vol == 0.0
    assert len(calc._vwap_history) == 0
    assert len(calc._volume_history) == 0


def test_reset_produces_fresh_vwap():
    """After reset, VWAP reflects only new session data."""
    calc = VWAPCalculator()
    calc.reset()

    # Session 1: price around 100
    for _ in range(10):
        bar = VWAPBar(high=105.0, low=95.0, close=100.0, volume=1000.0)
        calc.update(bar)

    calc.reset()

    # Session 2: price around 200
    bar = VWAPBar(high=210.0, low=190.0, close=200.0, volume=1000.0)
    result = calc.update(bar)

    assert result is not None
    tp = (210.0 + 190.0 + 200.0) / 3.0
    assert abs(result.vwap - tp) < 1e-9  # Should reflect only session 2


# ── Sigma band detection ──


def test_is_at_sigma_band():
    """Test is_at_sigma_band with tolerance."""
    assert is_at_sigma_band(2.0, 2.0) is True  # Exactly at band
    assert is_at_sigma_band(1.9, 2.0, tolerance=0.15) is True  # Within tolerance
    assert is_at_sigma_band(1.84, 2.0, tolerance=0.15) is False  # Just outside
    assert is_at_sigma_band(-2.0, 2.0) is True  # Negative side
    assert is_at_sigma_band(3.5, 2.0) is True  # Well beyond


def test_is_beyond_sigma_band():
    """Test is_beyond_sigma_band (strict)."""
    assert is_beyond_sigma_band(2.1, 2.0) is True
    assert is_beyond_sigma_band(2.0, 2.0) is False  # Not strictly beyond
    assert is_beyond_sigma_band(1.9, 2.0) is False
    assert is_beyond_sigma_band(-2.1, 2.0) is True  # Negative side


# ── Efficiency: many bars ──


def test_many_bars_no_crash():
    """Process 5000 bars without crashing (efficiency test)."""
    calc = VWAPCalculator()
    calc.reset()

    for i in range(5000):
        price = 100.0 + math.sin(i * 0.1) * 10.0
        vol = 1000.0 + (i % 100) * 10.0
        bar = VWAPBar(high=price + 1.0, low=price - 1.0, close=price, volume=vol)
        result = calc.update(bar)

    assert result is not None
    assert math.isfinite(result.vwap)
    assert math.isfinite(result.slope)
    assert math.isfinite(result.sigma_position)


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
    print(f"All {len(tests)} vwap tests passed.")
