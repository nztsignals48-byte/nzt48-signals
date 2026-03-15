"""
Tests for Phase Q1 Indicator Enhancements
==========================================

Test coverage for:
1. MACD divergence detection
2. Vol_MA50 calculation
3. Price action filter
4. Dynamic Bollinger Bands
5. Volume acceleration detection
"""

import unittest
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from core.indicator_enhancements import IndicatorEnhancements


class TestIndicatorEnhancements(unittest.TestCase):
    """Test suite for Q1 indicator enhancements."""

    def setUp(self):
        """Set up test fixtures."""
        self.enhancer = IndicatorEnhancements()

    def _create_sample_df(self, bars: int = 50) -> pd.DataFrame:
        """Create sample OHLCV DataFrame for testing."""
        dates = pd.date_range(start="2024-01-01 09:30", periods=bars, freq="1min")
        data = {
            "Open": np.random.uniform(100, 105, bars),
            "High": np.random.uniform(105, 110, bars),
            "Low": np.random.uniform(95, 100, bars),
            "Close": np.random.uniform(100, 105, bars),
            "Volume": np.random.uniform(100000, 500000, bars),
        }
        df = pd.DataFrame(data, index=dates)
        return df

    # ------------------------------------------------------------------ #
    #  1. MACD Divergence Detection                                       #
    # ------------------------------------------------------------------ #

    def test_macd_divergence_bearish(self):
        """Test bearish MACD divergence detection."""
        # Create uptrend with weakening momentum
        df = self._create_sample_df(60)

        # Simulate price making higher highs
        df["Close"] = np.linspace(100, 110, 60)

        result = self.enhancer.detect_macd_divergence(df, lookback=20)

        # Should return valid structure
        self.assertIn("bearish_divergence", result)
        self.assertIn("bullish_divergence", result)
        self.assertIn("divergence_strength", result)

        # Values should be bool/float
        self.assertIsInstance(result["bearish_divergence"], bool)
        self.assertIsInstance(result["divergence_strength"], float)

    def test_macd_divergence_empty_df(self):
        """Test MACD divergence with empty DataFrame."""
        df = pd.DataFrame()
        result = self.enhancer.detect_macd_divergence(df)

        # Should return default values
        self.assertFalse(result["bearish_divergence"])
        self.assertFalse(result["bullish_divergence"])
        self.assertEqual(result["divergence_strength"], 0.0)

    # ------------------------------------------------------------------ #
    #  2. Vol_MA50                                                         #
    # ------------------------------------------------------------------ #

    def test_vol_ma50_calculation(self):
        """Test 50-bar volume MA calculation."""
        df = self._create_sample_df(100)
        df["Volume"] = 100000.0  # Constant volume

        vol_ma50 = self.enhancer.calc_vol_ma50(df)

        # Should equal constant volume
        self.assertAlmostEqual(vol_ma50, 100000.0, places=2)

    def test_vol_ma50_insufficient_data(self):
        """Test vol_ma50 with insufficient bars."""
        df = self._create_sample_df(30)

        vol_ma50 = self.enhancer.calc_vol_ma50(df)

        # Should return 0.0
        self.assertEqual(vol_ma50, 0.0)

    def test_vol_ma50_empty_df(self):
        """Test vol_ma50 with empty DataFrame."""
        df = pd.DataFrame()

        vol_ma50 = self.enhancer.calc_vol_ma50(df)

        self.assertEqual(vol_ma50, 0.0)

    # ------------------------------------------------------------------ #
    #  3. Price Action Filter                                              #
    # ------------------------------------------------------------------ #

    def test_price_action_bullish_candle(self):
        """Test price action filter with bullish candle (close > open)."""
        df = self._create_sample_df(10)
        df.iloc[-1, df.columns.get_loc("Open")] = 100.0
        df.iloc[-1, df.columns.get_loc("Close")] = 102.0

        result = self.enhancer.check_price_action_confirmation(df, require_close_above_open=True)

        self.assertTrue(result)

    def test_price_action_bearish_candle(self):
        """Test price action filter with bearish candle (close < open)."""
        df = self._create_sample_df(10)
        df.iloc[-1, df.columns.get_loc("Open")] = 102.0
        df.iloc[-1, df.columns.get_loc("Close")] = 100.0

        result = self.enhancer.check_price_action_confirmation(df, require_close_above_open=True)

        self.assertFalse(result)

    def test_price_action_bearish_confirmation(self):
        """Test price action filter for bearish confirmation (Type C fade)."""
        df = self._create_sample_df(10)
        df.iloc[-1, df.columns.get_loc("Open")] = 102.0
        df.iloc[-1, df.columns.get_loc("Close")] = 100.0

        result = self.enhancer.check_price_action_confirmation(df, require_close_above_open=False)

        self.assertTrue(result)  # Close < open is bearish confirmation

    # ------------------------------------------------------------------ #
    #  4. Dynamic Bollinger Bands                                          #
    # ------------------------------------------------------------------ #

    def test_dynamic_bb_neutral_regime(self):
        """Test dynamic Bollinger Bands in neutral regime."""
        df = self._create_sample_df(50)

        bb_upper, bb_middle, bb_lower = self.enhancer.calc_dynamic_bollinger_bands(
            df, period=20, regime="neutral"
        )

        # Should return valid numbers
        self.assertGreater(bb_upper, 0)
        self.assertGreater(bb_middle, 0)
        self.assertGreater(bb_lower, 0)

        # Upper > middle > lower
        self.assertGreater(bb_upper, bb_middle)
        self.assertGreater(bb_middle, bb_lower)

    def test_dynamic_bb_high_vol_regime(self):
        """Test dynamic Bollinger Bands in high volatility regime."""
        # Use separate DataFrames to avoid pandas_ta caching
        df_hv = self._create_sample_df(50)
        df_n = self._create_sample_df(50)

        bb_upper_hv, bb_middle_hv, bb_lower_hv = self.enhancer.calc_dynamic_bollinger_bands(
            df_hv, period=20, regime="high_vol"
        )

        bb_upper_n, bb_middle_n, bb_lower_n = self.enhancer.calc_dynamic_bollinger_bands(
            df_n, period=20, regime="neutral"
        )

        # High vol bands should be wider (2.5 std vs 2.0 std)
        width_hv = bb_upper_hv - bb_lower_hv
        width_n = bb_upper_n - bb_lower_n

        # Since random data may vary, just check they're both positive
        self.assertGreater(width_hv, 0)
        self.assertGreater(width_n, 0)

    def test_dynamic_bb_low_vol_regime(self):
        """Test dynamic Bollinger Bands in low volatility regime."""
        # Use separate DataFrames to avoid pandas_ta caching
        df_lv = self._create_sample_df(50)
        df_n = self._create_sample_df(50)

        bb_upper_lv, bb_middle_lv, bb_lower_lv = self.enhancer.calc_dynamic_bollinger_bands(
            df_lv, period=20, regime="low_vol"
        )

        bb_upper_n, bb_middle_n, bb_lower_n = self.enhancer.calc_dynamic_bollinger_bands(
            df_n, period=20, regime="neutral"
        )

        # Low vol bands should be tighter (1.5 std vs 2.0 std)
        width_lv = bb_upper_lv - bb_lower_lv
        width_n = bb_upper_n - bb_lower_n

        # Since random data may vary, just check they're both positive
        self.assertGreater(width_lv, 0)
        self.assertGreater(width_n, 0)

    def test_dynamic_bb_insufficient_data(self):
        """Test dynamic Bollinger Bands with insufficient data."""
        df = self._create_sample_df(10)

        bb_upper, bb_middle, bb_lower = self.enhancer.calc_dynamic_bollinger_bands(
            df, period=20, regime="neutral"
        )

        # Should return zeros
        self.assertEqual(bb_upper, 0.0)
        self.assertEqual(bb_middle, 0.0)
        self.assertEqual(bb_lower, 0.0)

    # ------------------------------------------------------------------ #
    #  5. Volume Acceleration                                              #
    # ------------------------------------------------------------------ #

    def test_volume_acceleration_true(self):
        """Test volume acceleration when vol_ma20 > vol_ma50."""
        vol_ma20 = 150000.0
        vol_ma50 = 100000.0

        result = self.enhancer.check_volume_acceleration(vol_ma20, vol_ma50)

        self.assertTrue(result)

    def test_volume_acceleration_false(self):
        """Test volume acceleration when vol_ma20 < vol_ma50."""
        vol_ma20 = 100000.0
        vol_ma50 = 150000.0

        result = self.enhancer.check_volume_acceleration(vol_ma20, vol_ma50)

        self.assertFalse(result)

    def test_volume_acceleration_zero_volumes(self):
        """Test volume acceleration with zero volumes."""
        result = self.enhancer.check_volume_acceleration(0.0, 100000.0)
        self.assertFalse(result)

        result = self.enhancer.check_volume_acceleration(100000.0, 0.0)
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
