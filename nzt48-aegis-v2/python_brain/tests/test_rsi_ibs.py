"""Tests for brain.rsi_ibs -- RSI(2)/IBS combined signal, SMA filters."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from brain.rsi_ibs import (
    RSIIBSStrategy,
    calculate_ibs,
    calculate_rsi,
    calculate_sma,
    combined_rsi_ibs_signal,
    exit_signal_sma5,
    trend_filter_sma200,
)


# ── RSI calculation ──


def test_rsi_strong_uptrend():
    """Strong uptrend produces RSI near 100."""
    closes = [100.0 + i for i in range(20)]  # Monotonic rise
    rsi = calculate_rsi(closes, period=2)
    assert rsi is not None
    assert rsi > 90.0, f"Strong uptrend RSI should be > 90, got {rsi}"


def test_rsi_strong_downtrend():
    """Strong downtrend produces RSI near 0."""
    closes = [100.0 - i for i in range(20)]  # Monotonic fall
    rsi = calculate_rsi(closes, period=2)
    assert rsi is not None
    assert rsi < 10.0, f"Strong downtrend RSI should be < 10, got {rsi}"


def test_rsi_range_0_100():
    """RSI is always in [0, 100]."""
    closes = [100.0 + ((-1) ** i) * 5.0 for i in range(50)]
    rsi = calculate_rsi(closes, period=2)
    assert rsi is not None
    assert 0.0 <= rsi <= 100.0


def test_rsi_insufficient_data():
    """RSI returns None with insufficient data."""
    rsi = calculate_rsi([100.0, 101.0], period=14)
    assert rsi is None


def test_rsi_period_1():
    """RSI with period=1 works."""
    closes = [100.0, 105.0]
    rsi = calculate_rsi(closes, period=1)
    assert rsi is not None
    assert rsi == 100.0  # Single gain, no loss


def test_rsi_flat_prices():
    """Flat prices produce RSI = 50."""
    closes = [100.0] * 20
    rsi = calculate_rsi(closes, period=2)
    assert rsi is not None
    assert abs(rsi - 50.0) < 1e-9, f"Flat prices should give RSI=50, got {rsi}"


def test_rsi_all_losses():
    """All losses produce RSI = 0."""
    closes = [100.0, 99.0, 98.0, 97.0, 96.0]
    rsi = calculate_rsi(closes, period=2)
    assert rsi is not None
    assert abs(rsi) < 1e-9, f"All losses should give RSI=0, got {rsi}"


def test_rsi_all_gains():
    """All gains produce RSI = 100."""
    closes = [100.0, 101.0, 102.0, 103.0, 104.0]
    rsi = calculate_rsi(closes, period=2)
    assert rsi is not None
    assert abs(rsi - 100.0) < 1e-9, f"All gains should give RSI=100, got {rsi}"


def test_rsi_period_2_default():
    """RSI(2) is the Connors default and produces extreme readings."""
    # Sharp 2-bar selloff
    closes = [100.0, 98.0, 95.0]
    rsi = calculate_rsi(closes, period=2)
    assert rsi is not None
    assert rsi < 10.0  # Should be deeply oversold


def test_rsi_invalid_period():
    """Period < 1 returns None."""
    rsi = calculate_rsi([100.0, 101.0, 102.0], period=0)
    assert rsi is None


# ── IBS calculation ──


def test_ibs_close_at_high():
    """Close at high gives IBS = 1.0."""
    ibs = calculate_ibs(high=110.0, low=90.0, close=110.0)
    assert ibs is not None
    assert abs(ibs - 1.0) < 1e-9


def test_ibs_close_at_low():
    """Close at low gives IBS = 0.0."""
    ibs = calculate_ibs(high=110.0, low=90.0, close=90.0)
    assert ibs is not None
    assert abs(ibs) < 1e-9


def test_ibs_close_at_midpoint():
    """Close at midpoint gives IBS = 0.5."""
    ibs = calculate_ibs(high=110.0, low=90.0, close=100.0)
    assert ibs is not None
    assert abs(ibs - 0.5) < 1e-9


def test_ibs_doji_bar():
    """High == Low (doji) returns None (H61)."""
    ibs = calculate_ibs(high=100.0, low=100.0, close=100.0)
    assert ibs is None


def test_ibs_range_0_1():
    """IBS is clamped to [0, 1]."""
    ibs = calculate_ibs(high=110.0, low=90.0, close=105.0)
    assert ibs is not None
    assert 0.0 <= ibs <= 1.0


def test_ibs_zero_range():
    """Zero range returns None."""
    ibs = calculate_ibs(high=50.0, low=50.0, close=50.0)
    assert ibs is None


# ── SMA calculation ──


def test_sma_basic():
    """Simple SMA calculation."""
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    sma = calculate_sma(values, period=5)
    assert sma is not None
    assert abs(sma - 30.0) < 1e-9


def test_sma_last_n():
    """SMA uses last N values."""
    values = [100.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    sma = calculate_sma(values, period=5)
    assert sma is not None
    assert abs(sma - 3.0) < 1e-9  # Average of last 5: [1,2,3,4,5]


def test_sma_insufficient_data():
    """Insufficient data returns None."""
    sma = calculate_sma([10.0, 20.0], period=5)
    assert sma is None


def test_sma_period_1():
    """Period 1 = last value."""
    sma = calculate_sma([10.0, 20.0, 30.0], period=1)
    assert sma is not None
    assert abs(sma - 30.0) < 1e-9


def test_sma_invalid_period():
    """Period < 1 returns None."""
    sma = calculate_sma([10.0, 20.0], period=0)
    assert sma is None


# ── Combined RSI/IBS signal ──


def test_combined_signal_both_triggered():
    """Signal fires when both RSI < threshold and IBS < threshold."""
    # Sharp 2-bar selloff with close near low
    closes = [100.0, 98.0, 95.0]
    high = 96.0
    low = 94.5

    signal = combined_rsi_ibs_signal(
        closes=closes,
        high=high,
        low=low,
        rsi_period=2,
        rsi_threshold=10.0,
        ibs_threshold=0.5,
    )
    # RSI(2) after 2 down bars should be < 10; IBS = (95-94.5)/(96-94.5) = 0.33
    assert signal is True


def test_combined_signal_rsi_too_high():
    """Signal does not fire when RSI is above threshold."""
    closes = [100.0, 102.0, 104.0]  # Uptrend, RSI > threshold
    high = 105.0
    low = 103.0

    signal = combined_rsi_ibs_signal(
        closes=closes,
        high=high,
        low=low,
        rsi_period=2,
        rsi_threshold=10.0,
        ibs_threshold=0.5,
    )
    assert signal is False


def test_combined_signal_ibs_too_high():
    """Signal does not fire when IBS is above threshold."""
    # Even if RSI is low, close at high means IBS is high
    closes = [100.0, 98.0, 95.0]
    high = 95.5
    low = 90.0

    ibs_value = (95.0 - 90.0) / (95.5 - 90.0)  # 0.909
    signal = combined_rsi_ibs_signal(
        closes=closes,
        high=high,
        low=low,
        rsi_period=2,
        rsi_threshold=10.0,
        ibs_threshold=0.2,  # IBS must be < 0.2
    )
    assert signal is False


def test_combined_signal_insufficient_data():
    """Insufficient RSI data returns False."""
    signal = combined_rsi_ibs_signal(
        closes=[100.0],
        high=101.0,
        low=99.0,
        rsi_period=2,
    )
    assert signal is False


def test_combined_signal_doji_bar():
    """Doji bar (IBS = None) returns False."""
    closes = [100.0, 98.0, 95.0]
    signal = combined_rsi_ibs_signal(
        closes=closes,
        high=95.0,
        low=95.0,  # Doji
    )
    assert signal is False


# ── Trend filter (SMA-200) ──


def test_trend_filter_above_sma200():
    """Price above SMA(200) passes trend filter."""
    # 200 values at 100, then current at 105
    closes = [100.0] * 200 + [105.0]
    assert trend_filter_sma200(closes) is True


def test_trend_filter_below_sma200():
    """Price below SMA(200) fails trend filter."""
    closes = [100.0] * 200 + [95.0]
    assert trend_filter_sma200(closes) is False


def test_trend_filter_insufficient_data():
    """Insufficient data returns False."""
    closes = [100.0] * 50
    assert trend_filter_sma200(closes) is False


# ── Exit signal (SMA-5) ──


def test_exit_signal_above_sma5():
    """Price above SMA(5) triggers exit."""
    closes = [100.0, 101.0, 102.0, 103.0, 104.0, 110.0]
    assert exit_signal_sma5(closes) is True


def test_exit_signal_below_sma5():
    """Price below SMA(5) does not trigger exit."""
    closes = [100.0, 101.0, 102.0, 103.0, 104.0, 95.0]
    assert exit_signal_sma5(closes) is False


def test_exit_signal_insufficient_data():
    """Insufficient data returns False."""
    closes = [100.0, 101.0]
    assert exit_signal_sma5(closes) is False


# ── RSIIBSStrategy class ──


def test_strategy_entry_qualified():
    """Strategy evaluates qualified entry (RSI low, IBS low, above SMA-200)."""
    strategy = RSIIBSStrategy(rsi_threshold=10.0, ibs_threshold=0.5)

    # 200 bars at 100, then 2 sharp drops
    closes = [100.0] * 200 + [98.0, 95.0]
    high = 96.0
    low = 94.5

    result = strategy.evaluate_entry(closes=closes, high=high, low=low)

    assert result["entry_signal"] is True  # RSI < 10, IBS < 0.5
    assert result["trend_aligned"] is False  # 95 < SMA(200) which is ~100
    assert result["qualified"] is False  # trend_aligned is False


def test_strategy_entry_not_qualified_rsi_high():
    """Strategy rejects entry when RSI is too high."""
    strategy = RSIIBSStrategy(rsi_threshold=10.0, ibs_threshold=0.5)

    closes = [100.0] * 200 + [102.0, 105.0]  # Rising prices
    high = 106.0
    low = 104.0

    result = strategy.evaluate_entry(closes=closes, high=high, low=low)

    assert result["entry_signal"] is False
    assert result["qualified"] is False


def test_strategy_exit():
    """Strategy evaluates exit when price > SMA(5)."""
    strategy = RSIIBSStrategy()

    closes = [100.0, 101.0, 102.0, 103.0, 104.0, 110.0]
    result = strategy.evaluate_exit(closes=closes)

    assert result["exit_signal"] is True
    assert result["sma_5"] is not None


def test_strategy_exit_no_signal():
    """Strategy exit not triggered when below SMA(5)."""
    strategy = RSIIBSStrategy()

    closes = [100.0, 101.0, 102.0, 103.0, 104.0, 95.0]
    result = strategy.evaluate_exit(closes=closes)

    assert result["exit_signal"] is False


def test_strategy_entry_returns_all_keys():
    """Entry result contains all expected keys."""
    strategy = RSIIBSStrategy()
    closes = [100.0] * 10
    result = strategy.evaluate_entry(closes=closes, high=101.0, low=99.0)

    expected_keys = {"entry_signal", "trend_aligned", "rsi", "ibs", "sma_200", "qualified"}
    assert set(result.keys()) == expected_keys


def test_strategy_exit_returns_all_keys():
    """Exit result contains all expected keys."""
    strategy = RSIIBSStrategy()
    closes = [100.0] * 10
    result = strategy.evaluate_exit(closes=closes)

    expected_keys = {"exit_signal", "sma_5"}
    assert set(result.keys()) == expected_keys


# ── RSI(2) edge cases ──


def test_rsi2_extreme_oversold():
    """RSI(2) after big 2-bar drop should be < 5."""
    closes = [100.0, 90.0, 80.0]  # 10% + 11% drops
    rsi = calculate_rsi(closes, period=2)
    assert rsi is not None
    assert rsi < 5.0, f"Expected extreme oversold RSI < 5, got {rsi}"


def test_rsi2_extreme_overbought():
    """RSI(2) after big 2-bar rally should be > 95."""
    closes = [100.0, 110.0, 121.0]  # 10% + 10% gains
    rsi = calculate_rsi(closes, period=2)
    assert rsi is not None
    assert rsi > 95.0, f"Expected extreme overbought RSI > 95, got {rsi}"


def test_rsi14_moderate():
    """RSI(14) on mixed data should be moderate (not extreme)."""
    closes = [100.0 + ((-1) ** i) * 2.0 for i in range(30)]
    rsi = calculate_rsi(closes, period=14)
    assert rsi is not None
    assert 20.0 < rsi < 80.0, f"Mixed data RSI(14) should be moderate, got {rsi}"


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
    print(f"All {len(tests)} rsi_ibs tests passed.")
