"""Tests for brain.indicators.volume_analytics"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from brain.indicators.volume_analytics import (
    calculate_rvol,
    calculate_vpin,
    detect_sweep,
    spread_explosion_rate,
    volume_divergence,
)


# ── calculate_rvol ──

def test_rvol_basic():
    # 20-bar MA of 100, current bar 200 => RVOL = 2.0
    volumes = [100.0] * 20 + [200.0]
    assert abs(calculate_rvol(volumes, window=20) - 2.0) < 0.01


def test_rvol_insufficient_data():
    volumes = [100.0] * 10
    assert calculate_rvol(volumes, window=20) == 0.0


def test_rvol_zero_ma():
    volumes = [0.0] * 21
    assert calculate_rvol(volumes, window=20) == 0.0


def test_rvol_normal():
    volumes = [100.0] * 21
    assert abs(calculate_rvol(volumes, window=20) - 1.0) < 0.01


# ── calculate_vpin ──

def test_vpin_balanced():
    # Equal buy/sell => VPIN = 0
    buy = [100.0] * 50
    sell = [100.0] * 50
    assert abs(calculate_vpin(buy, sell, n_buckets=50)) < 0.01


def test_vpin_all_buy():
    # All buy, no sell => VPIN = 1.0
    buy = [100.0] * 50
    sell = [0.0] * 50
    assert abs(calculate_vpin(buy, sell, n_buckets=50) - 1.0) < 0.01


def test_vpin_insufficient_data():
    buy = [100.0] * 10
    sell = [100.0] * 10
    assert calculate_vpin(buy, sell, n_buckets=50) == 0.0


def test_vpin_mixed():
    buy = [80.0] * 50
    sell = [20.0] * 50
    # |80-20|=60, total=100 => VPIN = 60/100 = 0.6
    assert abs(calculate_vpin(buy, sell, n_buckets=50) - 0.6) < 0.01


# ── detect_sweep ──

def test_sweep_detected():
    # MA of 100, current 300 (3x), price jumps 0.5%
    volumes = [100.0] * 20 + [300.0]
    prices = [100.0] * 20 + [100.5]
    assert detect_sweep(prices, volumes, threshold=2.0) is True


def test_sweep_not_detected_low_volume():
    volumes = [100.0] * 20 + [150.0]
    prices = [100.0] * 20 + [100.5]
    assert detect_sweep(prices, volumes, threshold=2.0) is False


def test_sweep_not_detected_small_move():
    volumes = [100.0] * 20 + [300.0]
    prices = [100.0] * 20 + [100.1]  # Only 0.1% move
    assert detect_sweep(prices, volumes, threshold=2.0) is False


def test_sweep_insufficient_data():
    assert detect_sweep([100.0] * 5, [100.0] * 5) is False


# ── spread_explosion_rate ──

def test_spread_explosion_normal():
    spreads = [0.02] * 10 + [0.02]
    assert abs(spread_explosion_rate(spreads, window=10) - 1.0) < 0.01


def test_spread_explosion_double():
    spreads = [0.02] * 10 + [0.04]
    assert abs(spread_explosion_rate(spreads, window=10) - 2.0) < 0.01


def test_spread_explosion_insufficient():
    spreads = [0.02] * 3
    assert spread_explosion_rate(spreads, window=10) == 0.0


# ── volume_divergence ──

def test_divergence_detected():
    # Price rising, volume declining
    prices = [100.0 + i * 0.5 for i in range(10)]
    volumes = [1000.0 - i * 50.0 for i in range(10)]
    assert volume_divergence(prices, volumes, window=10) is True


def test_divergence_not_detected_price_down():
    # Price falling, volume declining (not divergence for our definition)
    prices = [100.0 - i * 0.5 for i in range(10)]
    volumes = [1000.0 - i * 50.0 for i in range(10)]
    assert volume_divergence(prices, volumes, window=10) is False


def test_divergence_not_detected_volume_rising():
    # Price rising, volume also rising => no divergence
    prices = [100.0 + i * 0.5 for i in range(10)]
    volumes = [1000.0 + i * 50.0 for i in range(10)]
    assert volume_divergence(prices, volumes, window=10) is False


def test_divergence_insufficient():
    assert volume_divergence([100.0], [1000.0], window=10) is False


if __name__ == "__main__":
    import inspect
    tests = [
        obj for name, obj in inspect.getmembers(sys.modules[__name__])
        if inspect.isfunction(obj) and name.startswith("test_")
    ]
    for t in tests:
        t()
        print(f"  PASS: {t.__name__}")
    print(f"All {len(tests)} volume_analytics tests passed.")
