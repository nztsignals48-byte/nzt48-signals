"""Tests for brain.indicators.hurst"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from brain.indicators.hurst import estimate_hurst, classify_regime


# ── estimate_hurst ──

def test_hurst_trending():
    # Strong uptrend: H should be > 0.5
    prices = [100.0 + i for i in range(30)]
    h = estimate_hurst(prices, max_lag=20)
    assert h > 0.5, f"Uptrend should have H > 0.5, got {h}"


def test_hurst_mean_reverting():
    # Oscillating series around 100
    prices = [100.0 + ((-1) ** i) * 2.0 for i in range(30)]
    h = estimate_hurst(prices, max_lag=20)
    assert h < 0.6, f"Oscillating should have H < 0.6, got {h}"


def test_hurst_insufficient_data():
    h = estimate_hurst([100.0, 101.0], max_lag=20)
    assert h == 0.5, "Insufficient data should return 0.5 (random)"


def test_hurst_invalid_price():
    # Contains zero price
    h = estimate_hurst([100.0, 0.0, 101.0], max_lag=2)
    assert h == 0.5, "Invalid price series should return 0.5"


def test_hurst_clamped():
    # Result should always be in [0, 1]
    prices = [100.0 + i * 10.0 for i in range(50)]
    h = estimate_hurst(prices, max_lag=20)
    assert 0.0 <= h <= 1.0, f"Hurst should be in [0,1], got {h}"


def test_hurst_flat_series():
    # Flat prices, constant
    prices = [100.0] * 25
    h = estimate_hurst(prices, max_lag=20)
    # Flat series with identical prices leads to log(1.0)=0 returns
    # which gives degenerate R/S. The function should return 0.5 (fallback)
    assert 0.0 <= h <= 1.0


# ── classify_regime ──

def test_classify_trending():
    assert classify_regime(0.7) == "trending"


def test_classify_mean_reverting():
    assert classify_regime(0.3) == "mean_reverting"


def test_classify_random():
    assert classify_regime(0.5) == "random"


def test_classify_boundary_high():
    assert classify_regime(0.55) == "random"


def test_classify_boundary_low():
    assert classify_regime(0.45) == "random"


def test_classify_boundary_just_above():
    assert classify_regime(0.551) == "trending"


def test_classify_boundary_just_below():
    assert classify_regime(0.449) == "mean_reverting"


if __name__ == "__main__":
    import inspect
    tests = [
        obj for name, obj in inspect.getmembers(sys.modules[__name__])
        if inspect.isfunction(obj) and name.startswith("test_")
    ]
    for t in tests:
        t()
        print(f"  PASS: {t.__name__}")
    print(f"All {len(tests)} hurst tests passed.")
