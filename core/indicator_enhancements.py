"""
Phase Q1 Indicator Enhancements
================================

New indicators and filters to improve entry confidence:
1. MACD Divergence Detection
2. Vol_MA50 (50-bar volume moving average)
3. Price Action Filter (close > open confirmation)
4. Dynamic Bollinger Bands (adaptive width based on regime)

These feed into Type A/C/D entry logic improvements targeting +1.3 Sharpe.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import numpy as np
import pandas as pd
import pandas_ta as ta

logger = logging.getLogger("nzt48.core.indicator_enhancements")


class IndicatorEnhancements:
    """Phase Q1 indicator enhancements for improved entry confidence."""

    def __init__(self):
        """Initialize enhancement module."""
        pass

    # ------------------------------------------------------------------ #
    #  1. MACD Divergence Detection (30 min)                              #
    # ------------------------------------------------------------------ #

    def detect_macd_divergence(
        self,
        df: pd.DataFrame,
        lookback: int = 20,
    ) -> dict:
        """
        Detect divergence between price and MACD histogram.

        Bearish divergence: Price makes higher high, MACD histogram makes lower high
            → Momentum weakening despite price advancing (fade signal)

        Bullish divergence: Price makes lower low, MACD histogram makes higher low
            → Momentum strengthening despite price declining (entry signal)

        Args:
            df: OHLCV DataFrame with at least lookback bars
            lookback: Number of bars to scan for divergence

        Returns:
            {
                "bearish_divergence": bool,  # Fade signal
                "bullish_divergence": bool,  # Entry signal
                "divergence_strength": float,  # 0-100 (higher = stronger)
            }
        """
        result = {
            "bearish_divergence": False,
            "bullish_divergence": False,
            "divergence_strength": 0.0,
        }

        if df.empty or len(df) < lookback + 15:
            return result

        try:
            # Compute MACD
            macd = ta.macd(df["Close"], fast=12, slow=26, signal=9)
            if macd is None or macd.empty:
                return result

            # Extract histogram column
            hist_col = [c for c in macd.columns if "MACD" in c and "h" in c.lower()]
            if not hist_col:
                return result

            recent = df.iloc[-lookback:].copy()
            hist = macd[hist_col[0]].iloc[-lookback:].values
            highs = recent["High"].values
            lows = recent["Low"].values

            # Find pivot highs and lows (5-bar pivots)
            pivot_highs_price = []
            pivot_lows_price = []
            pivot_highs_macd = []
            pivot_lows_macd = []

            for i in range(2, len(highs) - 2):
                # Price pivot high
                if (highs[i] > highs[i-1] and highs[i] > highs[i-2] and
                    highs[i] > highs[i+1] and highs[i] > highs[i+2]):
                    pivot_highs_price.append((i, highs[i]))
                    pivot_highs_macd.append((i, hist[i]))

                # Price pivot low
                if (lows[i] < lows[i-1] and lows[i] < lows[i-2] and
                    lows[i] < lows[i+1] and lows[i] < lows[i+2]):
                    pivot_lows_price.append((i, lows[i]))
                    pivot_lows_macd.append((i, hist[i]))

            # Bearish divergence: price higher high + MACD lower high
            if len(pivot_highs_price) >= 2:
                prev_ph = pivot_highs_price[-2]
                curr_ph = pivot_highs_price[-1]
                prev_macd_h = pivot_highs_macd[-2]
                curr_macd_h = pivot_highs_macd[-1]

                if curr_ph[1] > prev_ph[1] and curr_macd_h[1] < prev_macd_h[1]:
                    result["bearish_divergence"] = True
                    # Strength: % difference in MACD decline
                    macd_decline_pct = abs((curr_macd_h[1] - prev_macd_h[1]) / (prev_macd_h[1] + 0.001)) * 100
                    result["divergence_strength"] = min(100.0, macd_decline_pct * 10)

            # Bullish divergence: price lower low + MACD higher low
            if len(pivot_lows_price) >= 2:
                prev_pl = pivot_lows_price[-2]
                curr_pl = pivot_lows_price[-1]
                prev_macd_l = pivot_lows_macd[-2]
                curr_macd_l = pivot_lows_macd[-1]

                if curr_pl[1] < prev_pl[1] and curr_macd_l[1] > prev_macd_l[1]:
                    result["bullish_divergence"] = True
                    # Strength: % improvement in MACD
                    macd_improve_pct = abs((curr_macd_l[1] - prev_macd_l[1]) / (prev_macd_l[1] - 0.001)) * 100
                    result["divergence_strength"] = min(100.0, macd_improve_pct * 10)

        except Exception as e:
            logger.debug(f"MACD divergence detection failed: {e}", exc_info=True)

        return result

    # ------------------------------------------------------------------ #
    #  2. Vol_MA50 (20 min)                                                #
    # ------------------------------------------------------------------ #

    def calc_vol_ma50(self, df: pd.DataFrame) -> float:
        """
        Compute 50-bar volume moving average.

        Used for longer-term volume trend detection vs. vol_ma20.
        When vol_ma20 > vol_ma50, volume is accelerating (bullish confirmation).

        Args:
            df: OHLCV DataFrame

        Returns:
            50-bar volume MA (0.0 if insufficient data)
        """
        if df.empty or len(df) < 50:
            return 0.0

        try:
            vol_ma50 = float(df["Volume"].iloc[-50:].mean())
            return vol_ma50 if not np.isnan(vol_ma50) else 0.0
        except Exception as e:
            logger.debug(f"Vol_MA50 calculation failed: {e}")
            return 0.0

    # ------------------------------------------------------------------ #
    #  3. Price Action Filter (15 min)                                     #
    # ------------------------------------------------------------------ #

    def check_price_action_confirmation(
        self,
        df: pd.DataFrame,
        require_close_above_open: bool = True,
    ) -> bool:
        """
        Price action filter: confirm recovery bar has close > open.

        For Type A (dip recovery): recovery bar should close above open
        For Type D (support bounce): bounce bar should close above open

        Args:
            df: OHLCV DataFrame (uses last bar)
            require_close_above_open: If True, requires bullish candle

        Returns:
            True if price action confirms signal, False otherwise
        """
        if df.empty:
            return False

        try:
            last_bar = df.iloc[-1]
            close = float(last_bar["Close"])
            open_price = float(last_bar["Open"])

            if require_close_above_open:
                return close > open_price
            else:
                # For bearish confirmation (Type C fade)
                return close < open_price

        except Exception as e:
            logger.debug(f"Price action filter failed: {e}")
            return False

    # ------------------------------------------------------------------ #
    #  4. Dynamic Bollinger Bands (45 min)                                 #
    # ------------------------------------------------------------------ #

    def calc_dynamic_bollinger_bands(
        self,
        df: pd.DataFrame,
        period: int = 20,
        regime: str = "neutral",
    ) -> Tuple[float, float, float]:
        """
        Dynamic Bollinger Bands with adaptive width based on volatility regime.

        Standard BB uses fixed 2.0 std dev. This adjusts based on regime:
        - High vol regime (VIX > 25): 2.5 std (wider bands, avoid false breakouts)
        - Low vol regime (VIX < 15): 1.5 std (tighter bands, catch smaller moves)
        - Neutral: 2.0 std (standard)

        Args:
            df: OHLCV DataFrame
            period: Lookback period (default 20)
            regime: "high_vol", "low_vol", "neutral"

        Returns:
            (bb_upper, bb_middle, bb_lower)
        """
        if df.empty or len(df) < period:
            return (0.0, 0.0, 0.0)

        try:
            # Determine std multiplier based on regime
            if regime == "high_vol":
                std_mult = 2.5
            elif regime == "low_vol":
                std_mult = 1.5
            else:
                std_mult = 2.0

            # Compute Bollinger Bands
            bb = ta.bbands(df["Close"], length=period, std=std_mult)
            if bb is None or bb.empty:
                return (0.0, 0.0, 0.0)

            # Extract columns
            cols = bb.columns.tolist()
            upper_col = [c for c in cols if "BBU" in c or "upper" in c.lower()]
            middle_col = [c for c in cols if "BBM" in c or "mid" in c.lower()]
            lower_col = [c for c in cols if "BBL" in c or "lower" in c.lower()]

            bb_upper = float(bb[upper_col[0]].iloc[-1]) if upper_col else 0.0
            bb_middle = float(bb[middle_col[0]].iloc[-1]) if middle_col else 0.0
            bb_lower = float(bb[lower_col[0]].iloc[-1]) if lower_col else 0.0

            return (bb_upper, bb_middle, bb_lower)

        except Exception as e:
            logger.debug(f"Dynamic Bollinger Bands failed: {e}", exc_info=True)
            return (0.0, 0.0, 0.0)

    # ------------------------------------------------------------------ #
    #  5. Volume MA Comparison (bonus)                                     #
    # ------------------------------------------------------------------ #

    def check_volume_acceleration(
        self,
        vol_ma20: float,
        vol_ma50: float,
    ) -> bool:
        """
        Check if volume is accelerating (vol_ma20 > vol_ma50).

        Used for Type A/D confirmation: rising volume trend on longer timeframe.

        Args:
            vol_ma20: 20-bar volume MA
            vol_ma50: 50-bar volume MA

        Returns:
            True if volume accelerating (bullish), False otherwise
        """
        if vol_ma20 <= 0 or vol_ma50 <= 0:
            return False

        return vol_ma20 > vol_ma50
