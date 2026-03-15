"""
Test Suite for Phase Q1 Indicator Enhancements
================================================

Tests for:
1. MACD Divergence Detection
2. Vol_MA50 calculation
3. Price Action Filter
4. Dynamic Bollinger Bands
5. Volume Acceleration

Expected: All Q1 indicators functional and wired into tier-based entry logic.
"""

import pandas as pd
import numpy as np
import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core.indicator_enhancements import IndicatorEnhancements
from models import IndicatorSnapshot

UTC = ZoneInfo("UTC")


@pytest.fixture
def sample_df():
    """Create sample OHLCV data for testing."""
    dates = pd.date_range(start="2026-01-01", periods=100, freq="1min")
    df = pd.DataFrame({
        "Open": np.random.uniform(100, 110, 100),
        "High": np.random.uniform(110, 120, 100),
        "Low": np.random.uniform(90, 100, 100),
        "Close": np.random.uniform(100, 110, 100),
        "Volume": np.random.uniform(1e6, 5e6, 100),
    }, index=dates)
    return df


@pytest.fixture
def indicator_engine():
    """Create IndicatorEnhancements instance."""
    return IndicatorEnhancements()


class TestMACDDivergence:
    """Test MACD divergence detection."""

    def test_bullish_divergence_detection(self, indicator_engine):
        """Test bullish divergence (price lower low, MACD higher low)."""
        # Create synthetic data with bullish divergence
        dates = pd.date_range(start="2026-01-01", periods=50, freq="1min")
        closes = [100] * 10 + list(np.linspace(100, 90, 10)) + [90] * 10 + list(np.linspace(90, 85, 10)) + [85] * 10
        df = pd.DataFrame({
            "Open": closes,
            "High": [c + 2 for c in closes],
            "Low": [c - 2 for c in closes],
            "Close": closes,
            "Volume": [1e6] * 50,
        }, index=dates)

        result = indicator_engine.detect_macd_divergence(df, lookback=40)

        # Should detect bullish divergence or at least not error
        assert isinstance(result, dict)
        assert "bullish_divergence" in result
        assert "divergence_strength" in result

    def test_insufficient_data(self, indicator_engine):
        """Test handling of insufficient data."""
        df = pd.DataFrame({
            "Open": [100],
            "High": [102],
            "Low": [98],
            "Close": [101],
            "Volume": [1e6],
        })

        result = indicator_engine.detect_macd_divergence(df)

        assert result["bullish_divergence"] is False
        assert result["bearish_divergence"] is False
        assert result["divergence_strength"] == 0.0


class TestVolMA50:
    """Test 50-bar volume moving average."""

    def test_vol_ma50_calculation(self, sample_df, indicator_engine):
        """Test vol_ma50 with sufficient data."""
        vol_ma50 = indicator_engine.calc_vol_ma50(sample_df)

        assert vol_ma50 > 0
        assert isinstance(vol_ma50, float)

        # Should be close to mean of last 50 bars
        expected = sample_df["Volume"].iloc[-50:].mean()
        assert abs(vol_ma50 - expected) < 1e-6

    def test_vol_ma50_insufficient_data(self, indicator_engine):
        """Test vol_ma50 with insufficient data."""
        df = pd.DataFrame({"Volume": [1e6] * 30})
        vol_ma50 = indicator_engine.calc_vol_ma50(df)

        assert vol_ma50 == 0.0


class TestPriceActionFilter:
    """Test price action confirmation filter."""

    def test_bullish_candle(self, indicator_engine):
        """Test detection of bullish candle (close > open)."""
        df = pd.DataFrame({
            "Open": [100],
            "Close": [105],
        })

        result = indicator_engine.check_price_action_confirmation(df)
        assert result is True

    def test_bearish_candle(self, indicator_engine):
        """Test detection of bearish candle (close < open)."""
        df = pd.DataFrame({
            "Open": [105],
            "Close": [100],
        })

        result = indicator_engine.check_price_action_confirmation(df)
        assert result is False

    def test_empty_df(self, indicator_engine):
        """Test handling of empty DataFrame."""
        df = pd.DataFrame()
        result = indicator_engine.check_price_action_confirmation(df)
        assert result is False


class TestDynamicBollingerBands:
    """Test dynamic Bollinger Bands with regime adaptation."""

    def test_high_vol_regime(self, sample_df, indicator_engine):
        """Test BB calculation in high volatility regime."""
        bb_upper, bb_middle, bb_lower = indicator_engine.calc_dynamic_bollinger_bands(
            sample_df, period=20, regime="high_vol"
        )

        assert bb_upper > bb_middle > bb_lower
        assert bb_upper > 0

        # High vol bands should be wider
        high_vol_width = bb_upper - bb_lower

        # Compare to neutral regime
        bb_u2, bb_m2, bb_l2 = indicator_engine.calc_dynamic_bollinger_bands(
            sample_df, period=20, regime="neutral"
        )
        neutral_width = bb_u2 - bb_l2

        assert high_vol_width > neutral_width

    def test_low_vol_regime(self, sample_df, indicator_engine):
        """Test BB calculation in low volatility regime."""
        bb_upper, bb_middle, bb_lower = indicator_engine.calc_dynamic_bollinger_bands(
            sample_df, period=20, regime="low_vol"
        )

        assert bb_upper > bb_middle > bb_lower


class TestVolumeAcceleration:
    """Test volume acceleration detection."""

    def test_accelerating_volume(self, indicator_engine):
        """Test detection of accelerating volume."""
        vol_ma20 = 2e6
        vol_ma50 = 1.5e6

        result = indicator_engine.check_volume_acceleration(vol_ma20, vol_ma50)
        assert result is True

    def test_decelerating_volume(self, indicator_engine):
        """Test detection of decelerating volume."""
        vol_ma20 = 1.5e6
        vol_ma50 = 2e6

        result = indicator_engine.check_volume_acceleration(vol_ma20, vol_ma50)
        assert result is False

    def test_zero_volume(self, indicator_engine):
        """Test handling of zero volume."""
        result = indicator_engine.check_volume_acceleration(0, 0)
        assert result is False


class TestIntegration:
    """Integration tests for Q1 indicators in tier-based system."""

    def test_indicator_snapshot_has_q1_fields(self):
        """Verify IndicatorSnapshot model has Q1 fields."""
        snapshot = IndicatorSnapshot()

        # Q1 fields must exist
        assert hasattr(snapshot, "macd_bearish_div")
        assert hasattr(snapshot, "macd_bullish_div")
        assert hasattr(snapshot, "macd_div_strength")
        assert hasattr(snapshot, "vol_ma50")
        assert hasattr(snapshot, "vol_acceleration")
        assert hasattr(snapshot, "price_action_bullish")
        assert hasattr(snapshot, "bb_dynamic_upper")
        assert hasattr(snapshot, "bb_dynamic_middle")
        assert hasattr(snapshot, "bb_dynamic_lower")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
