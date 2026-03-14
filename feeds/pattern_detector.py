"""
NZT-48 Trading System -- 12-Pattern Detection Engine
=====================================================
Section 6 of the Master Spec.

Detects 12 intraday price/volume patterns from OHLCV bar data and the
current IndicatorSnapshot.  Each detected pattern carries a confidence
modifier and a recommended action that downstream modules (confidence
engine, trade manager) consume directly.

Patterns
--------
 1. Coiled Spring          7. Gap and Go
 2. Volume Climax          8. Gap and Fade
 3. Failed Breakout        9. Earnings Momentum
 4. Trend Acceleration    10. Dead Cat Bounce
 5. Momentum Exhaustion   11. Absorption
 6. VWAP Magnet           12. ABCD Pattern
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from models import IndicatorSnapshot

logger = logging.getLogger("nzt48.pattern_detector")

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
SwingType = Literal["high", "low"]


@dataclass(frozen=True)
class SwingPoint:
    """A single swing high or low in a price series."""
    index: int
    price: float
    kind: SwingType


# ---------------------------------------------------------------------------
# PatternDetector
# ---------------------------------------------------------------------------

class PatternDetector:
    """Detect the 12 Section-6 patterns from intraday bar data.

    The main entry point is :meth:`detect_patterns`, which returns a list
    of pattern dicts.  Callers that find no patterns simply receive an
    empty list -- the engine never raises on missing data; it logs a
    warning and returns gracefully.

    Parameters
    ----------
    min_bars : int
        Minimum number of bars required in the DataFrame before any
        detection logic runs.  Default ``10``.
    """

    def __init__(self, min_bars: int = 10) -> None:
        self._min_bars = min_bars

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def detect_patterns(
        self,
        df: pd.DataFrame,
        indicators: IndicatorSnapshot,
        or_high_5m: float,
        or_low_5m: float,
        daily_bars: pd.DataFrame | None = None,
        prev_close: float | None = None,
        is_post_earnings: bool = False,
        market_open_index: int | None = None,
    ) -> list[dict]:
        """Run all 12 pattern detectors and return matches.

        Parameters
        ----------
        df : pd.DataFrame
            Intraday OHLCV bars (typically 1-min).  Must contain columns
            ``Open, High, Low, Close, Volume``.
        indicators : IndicatorSnapshot
            Current indicator state for this ticker.
        or_high_5m : float
            5-minute opening range high.
        or_low_5m : float
            5-minute opening range low.
        daily_bars : pd.DataFrame | None
            Daily OHLCV bars (20+ days) used for Coiled Spring OR-width
            averaging.  Optional -- if ``None``, the Coiled Spring
            detector is skipped.
        prev_close : float | None
            Previous session close.  Required for gap-based patterns
            (7, 8, 9).  If ``None`` those patterns are skipped.
        is_post_earnings : bool
            Whether the current session is the first session after an
            earnings release.  Enables the Earnings Momentum detector.
        market_open_index : int | None
            Row index in *df* corresponding to the market open bar
            (09:30 ET).  Used by gap-hold checks.  If ``None``, the
            detector infers it as the first bar.

        Returns
        -------
        list[dict]
            Each dict contains:
            ``{name, confidence_modifier, action, details}``
        """
        if df is None or df.empty or len(df) < self._min_bars:
            logger.debug(
                "Not enough bars for pattern detection (%s)",
                0 if df is None else len(df),
            )
            return []

        # Ensure required columns exist
        required = {"Open", "High", "Low", "Close", "Volume"}
        if not required.issubset(df.columns):
            logger.warning(
                "DataFrame missing columns: %s", required - set(df.columns)
            )
            return []

        patterns: list[dict] = []

        # 1. Coiled Spring
        if daily_bars is not None and not daily_bars.empty:
            result = self._detect_coiled_spring(
                df, indicators, or_high_5m, or_low_5m, daily_bars
            )
            if result:
                patterns.append(result)

        # 2. Volume Climax
        result = self._detect_volume_climax(df, indicators)
        if result:
            patterns.append(result)

        # 3. Failed Breakout
        result = self._detect_failed_breakout(df, or_high_5m, or_low_5m)
        if result:
            patterns.append(result)

        # 4. Trend Acceleration
        result = self._detect_trend_acceleration(df)
        if result:
            patterns.append(result)

        # 5. Momentum Exhaustion
        result = self._detect_momentum_exhaustion(df, indicators)
        if result:
            patterns.append(result)

        # 6. VWAP Magnet
        result = self._detect_vwap_magnet(indicators)
        if result:
            patterns.append(result)

        # 7. Gap and Go
        if prev_close is not None and prev_close > 0:
            result = self._detect_gap_and_go(
                df, indicators, prev_close, market_open_index
            )
            if result:
                patterns.append(result)

        # 8. Gap and Fade
        if prev_close is not None and prev_close > 0:
            result = self._detect_gap_and_fade(
                df, indicators, prev_close, market_open_index
            )
            if result:
                patterns.append(result)

        # 9. Earnings Momentum
        if is_post_earnings and prev_close is not None and prev_close > 0:
            result = self._detect_earnings_momentum(
                df, indicators, prev_close, market_open_index
            )
            if result:
                patterns.append(result)

        # 10. Dead Cat Bounce
        result = self._detect_dead_cat_bounce(df, indicators)
        if result:
            patterns.append(result)

        # 11. Absorption
        result = self._detect_absorption(df)
        if result:
            patterns.append(result)

        # 12. ABCD Pattern
        result = self._detect_abcd_pattern(df)
        if result:
            patterns.append(result)

        if patterns:
            logger.info(
                "Detected %d pattern(s) for %s: %s",
                len(patterns),
                indicators.ticker,
                [p["name"] for p in patterns],
            )

        return patterns

    # ==================================================================
    # Individual pattern detectors
    # ==================================================================

    # ------------------------------------------------------------------
    # 1. Coiled Spring
    # ------------------------------------------------------------------

    def _detect_coiled_spring(
        self,
        df: pd.DataFrame,
        indicators: IndicatorSnapshot,
        or_high_5m: float,
        or_low_5m: float,
        daily_bars: pd.DataFrame,
    ) -> dict | None:
        """OR width < 50% of 20-day average OR width, volume building.

        Action: +15 confidence when it breaks.
        """
        current_or_width = or_high_5m - or_low_5m
        if current_or_width <= 0:
            return None

        avg_or_width = self._calc_or_width_20d_avg(daily_bars)
        if avg_or_width <= 0:
            return None

        ratio = current_or_width / avg_or_width
        if ratio >= 0.50:
            return None

        # Check for volume building: last 5 bars should show increasing
        # volume trend (simple: mean of last 5 > mean of prior 5)
        volume_building = False
        if len(df) >= 10:
            recent_vol = df["Volume"].iloc[-5:].mean()
            prior_vol = df["Volume"].iloc[-10:-5].mean()
            volume_building = prior_vol > 0 and recent_vol > prior_vol

        if not volume_building:
            return None

        return {
            "name": "Coiled Spring",
            "confidence_modifier": 15,
            "action": "+15 confidence when it breaks",
            "details": {
                "current_or_width": round(current_or_width, 4),
                "avg_or_width_20d": round(avg_or_width, 4),
                "compression_ratio": round(ratio, 3),
                "volume_building": volume_building,
            },
        }

    # ------------------------------------------------------------------
    # 2. Volume Climax
    # ------------------------------------------------------------------

    def _detect_volume_climax(
        self,
        df: pd.DataFrame,
        indicators: IndicatorSnapshot,
    ) -> dict | None:
        """Highest 1-min volume + reversal candle + RVOL > 3.0.

        Action: Counter-trend if regime supports.
        """
        if indicators.rvol < 3.0:
            return None

        if len(df) < 3:
            return None

        # Find the bar with the highest volume
        max_vol_idx = df["Volume"].idxmax()
        max_vol_pos = df.index.get_loc(max_vol_idx)

        # The spike must be among the recent bars (last 10)
        if max_vol_pos < len(df) - 10:
            return None

        max_vol_bar = df.loc[max_vol_idx]

        # Volume must be at least 3x the session average
        session_avg_vol = df["Volume"].mean()
        if session_avg_vol <= 0 or max_vol_bar["Volume"] < 3.0 * session_avg_vol:
            return None

        # Check for reversal candle
        if not self._detect_reversal_candle(max_vol_bar):
            return None

        return {
            "name": "Volume Climax",
            "confidence_modifier": 0,
            "action": "Counter-trend if regime supports",
            "details": {
                "climax_volume": int(max_vol_bar["Volume"]),
                "session_avg_volume": int(session_avg_vol),
                "volume_multiple": round(
                    max_vol_bar["Volume"] / session_avg_vol, 2
                ),
                "rvol": round(indicators.rvol, 2),
                "reversal_candle": True,
            },
        }

    # ------------------------------------------------------------------
    # 3. Failed Breakout
    # ------------------------------------------------------------------

    def _detect_failed_breakout(
        self,
        df: pd.DataFrame,
        or_high_5m: float,
        or_low_5m: float,
    ) -> dict | None:
        """Breaks OR, closes back inside within 3 bars, volume fading.

        Action: Reverse signal if confidence >= 70.
        """
        if len(df) < 5:
            return None

        or_width = or_high_5m - or_low_5m
        if or_width <= 0:
            return None

        # Look at the last 10 bars for a breakout-then-failure sequence
        window = df.iloc[-10:] if len(df) >= 10 else df

        # Check for high-side breakout failure
        high_breakout = self._check_failed_breakout_side(
            window, or_high_5m, side="high"
        )
        # Check for low-side breakout failure
        low_breakout = self._check_failed_breakout_side(
            window, or_low_5m, side="low"
        )

        if high_breakout:
            return {
                "name": "Failed Breakout",
                "confidence_modifier": 0,
                "action": "Reverse signal if confidence >= 70",
                "details": {
                    "side": "high",
                    "or_high": round(or_high_5m, 4),
                    "or_low": round(or_low_5m, 4),
                    **high_breakout,
                },
            }

        if low_breakout:
            return {
                "name": "Failed Breakout",
                "confidence_modifier": 0,
                "action": "Reverse signal if confidence >= 70",
                "details": {
                    "side": "low",
                    "or_high": round(or_high_5m, 4),
                    "or_low": round(or_low_5m, 4),
                    **low_breakout,
                },
            }

        return None

    def _check_failed_breakout_side(
        self,
        window: pd.DataFrame,
        level: float,
        side: Literal["high", "low"],
    ) -> dict | None:
        """Check if price broke a level and came back within 3 bars."""
        broke_out = False
        breakout_bar_pos: int | None = None

        for i in range(len(window)):
            bar = window.iloc[i]
            if side == "high" and bar["High"] > level:
                broke_out = True
                breakout_bar_pos = i
            elif side == "low" and bar["Low"] < level:
                broke_out = True
                breakout_bar_pos = i

            if broke_out and breakout_bar_pos is not None:
                # Look at the next 1-3 bars for close back inside
                for j in range(breakout_bar_pos + 1, min(breakout_bar_pos + 4, len(window))):
                    check_bar = window.iloc[j]
                    closed_back_inside = (
                        (side == "high" and check_bar["Close"] < level)
                        or (side == "low" and check_bar["Close"] > level)
                    )
                    if closed_back_inside:
                        # Check volume fading from breakout bar onward
                        bo_vol = window.iloc[breakout_bar_pos]["Volume"]
                        fade_vol = window.iloc[j]["Volume"]
                        if bo_vol > 0 and fade_vol < bo_vol:
                            return {
                                "breakout_bar": breakout_bar_pos,
                                "return_bar": j,
                                "bars_to_return": j - breakout_bar_pos,
                                "volume_fading": True,
                            }

                # Reset -- this breakout held, check subsequent bars
                broke_out = False
                breakout_bar_pos = None

        return None

    # ------------------------------------------------------------------
    # 4. Trend Acceleration
    # ------------------------------------------------------------------

    def _detect_trend_acceleration(self, df: pd.DataFrame) -> dict | None:
        """3+ consecutive bars with higher volume AND each bar larger range.

        Action: +10 confidence.  Widen trail to 1.5x ATR.
        """
        if len(df) < 4:
            return None

        # Check the last 5 bars (need at least 3 consecutive qualifying)
        lookback = min(len(df), 8)
        window = df.iloc[-lookback:]

        ranges = (window["High"] - window["Low"]).values
        volumes = window["Volume"].values

        # Find the longest run of increasing range AND increasing volume
        best_run = 0
        current_run = 0

        for i in range(1, len(ranges)):
            if ranges[i] > ranges[i - 1] and volumes[i] > volumes[i - 1]:
                current_run += 1
            else:
                best_run = max(best_run, current_run)
                current_run = 0

        best_run = max(best_run, current_run)

        # Need 3+ consecutive qualifying bars (which means 2+ transitions)
        if best_run < 2:
            return None

        consecutive_bars = best_run + 1  # transitions + 1 = bars

        return {
            "name": "Trend Acceleration",
            "confidence_modifier": 10,
            "action": "+10 confidence. Widen trail to 1.5x ATR",
            "details": {
                "consecutive_bars": consecutive_bars,
                "last_range": round(float(ranges[-1]), 4),
                "last_volume": int(volumes[-1]),
            },
        }

    # ------------------------------------------------------------------
    # 5. Momentum Exhaustion
    # ------------------------------------------------------------------

    def _detect_momentum_exhaustion(
        self,
        df: pd.DataFrame,
        indicators: IndicatorSnapshot,
    ) -> dict | None:
        """MACD peaked 5+ bars ago, RSI divergence.

        Action: Tighten trail to 0.5x ATR.

        Detection: MACD histogram declining for 5+ bars while price still
        advancing (bearish divergence) OR MACD histogram rising for 5+
        bars while price still declining (bullish divergence).
        """
        if len(df) < 7:
            return None

        # We rely on the indicator snapshot for current MACD state.
        # For the historical histogram trajectory we approximate from
        # recent bar closes relative to indicator EMAs.
        # Primary check: MACD histogram has been declining for 5+ bars.

        # Use a simple proxy: if current histogram < 0 and decreasing
        # (i.e. histogram itself is negative and getting more negative),
        # while price is higher than 5 bars ago => bearish divergence.
        histogram_val = indicators.macd_histogram
        rsi_val = indicators.rsi14

        recent_close = df["Close"].iloc[-1]
        close_5_bars_ago = df["Close"].iloc[-6] if len(df) >= 6 else df["Close"].iloc[0]

        price_advancing = recent_close > close_5_bars_ago
        price_declining = recent_close < close_5_bars_ago

        # Check histogram trajectory: approximate from last N closes
        # Simple heuristic: build a mini-histogram-like series from
        # close differences and check for divergence.
        closes = df["Close"].iloc[-8:].values if len(df) >= 8 else df["Close"].values

        # Approximate momentum via rate-of-change over 3 bars
        if len(closes) >= 6:
            roc_recent = closes[-1] - closes[-3]
            roc_earlier = closes[-3] - closes[-6] if len(closes) >= 6 else 0

            # Bearish exhaustion: price still up but momentum fading
            bearish_exhaustion = (
                price_advancing
                and roc_recent < roc_earlier
                and histogram_val < 0
                and rsi_val > 60
            )

            # Bullish exhaustion: price still down but momentum fading
            bullish_exhaustion = (
                price_declining
                and roc_recent > roc_earlier
                and histogram_val > 0
                and rsi_val < 40
            )

            if bearish_exhaustion or bullish_exhaustion:
                divergence_type = "bearish" if bearish_exhaustion else "bullish"
                return {
                    "name": "Momentum Exhaustion",
                    "confidence_modifier": 0,
                    "action": "Tighten trail to 0.5x ATR",
                    "details": {
                        "divergence_type": divergence_type,
                        "macd_histogram": round(histogram_val, 4),
                        "rsi": round(rsi_val, 2),
                        "price_5_bars_ago": round(float(close_5_bars_ago), 4),
                        "price_current": round(float(recent_close), 4),
                    },
                }

        return None

    # ------------------------------------------------------------------
    # 6. VWAP Magnet
    # ------------------------------------------------------------------

    def _detect_vwap_magnet(
        self,
        indicators: IndicatorSnapshot,
    ) -> dict | None:
        """Extended > 2 standard deviations from VWAP, RVOL declining.

        Action: Prepare for mean reversion.
        """
        price = indicators.price

        if price <= 0 or indicators.vwap <= 0:
            return None

        above_upper_2s = price > indicators.vwap_upper_2s and indicators.vwap_upper_2s > 0
        below_lower_2s = price < indicators.vwap_lower_2s and indicators.vwap_lower_2s > 0

        if not (above_upper_2s or below_lower_2s):
            return None

        # RVOL declining: use rvol < 1.5 as a proxy for fading relative
        # volume (i.e. the move is losing volume support)
        if indicators.rvol > 1.5:
            return None

        side = "above" if above_upper_2s else "below"
        deviation = abs(price - indicators.vwap)

        return {
            "name": "VWAP Magnet",
            "confidence_modifier": 0,
            "action": "Prepare for mean reversion",
            "details": {
                "side": side,
                "price": round(price, 4),
                "vwap": round(indicators.vwap, 4),
                "vwap_upper_2s": round(indicators.vwap_upper_2s, 4),
                "vwap_lower_2s": round(indicators.vwap_lower_2s, 4),
                "deviation": round(deviation, 4),
                "rvol": round(indicators.rvol, 2),
            },
        }

    # ------------------------------------------------------------------
    # 7. Gap and Go
    # ------------------------------------------------------------------

    def _detect_gap_and_go(
        self,
        df: pd.DataFrame,
        indicators: IndicatorSnapshot,
        prev_close: float,
        market_open_index: int | None,
    ) -> dict | None:
        """Gap > 1.5%, holds 5 bars, RVOL > 2.0.

        Action: ORB entry on pullback.  High confidence.
        """
        if len(df) < 6:
            return None

        open_bar_pos = market_open_index if market_open_index is not None else 0
        if open_bar_pos >= len(df):
            return None

        open_price = df["Open"].iloc[open_bar_pos]
        gap_pct = (open_price - prev_close) / prev_close * 100.0

        if abs(gap_pct) < 1.5:
            return None

        gap_direction = "up" if gap_pct > 0 else "down"
        gap_level = prev_close  # the level price gapped away from

        # Check if price held the gap for at least 5 bars
        hold_bars = min(open_bar_pos + 6, len(df))
        held = True
        for i in range(open_bar_pos, hold_bars):
            bar = df.iloc[i]
            if gap_direction == "up" and bar["Low"] < prev_close:
                held = False
                break
            elif gap_direction == "down" and bar["High"] > prev_close:
                held = False
                break

        if not held:
            return None

        if indicators.rvol < 2.0:
            return None

        return {
            "name": "Gap and Go",
            "confidence_modifier": 20,
            "action": "ORB entry on pullback. High confidence.",
            "details": {
                "gap_pct": round(gap_pct, 3),
                "gap_direction": gap_direction,
                "prev_close": round(prev_close, 4),
                "open_price": round(float(open_price), 4),
                "held_bars": hold_bars - open_bar_pos,
                "rvol": round(indicators.rvol, 2),
            },
        }

    # ------------------------------------------------------------------
    # 8. Gap and Fade
    # ------------------------------------------------------------------

    def _detect_gap_and_fade(
        self,
        df: pd.DataFrame,
        indicators: IndicatorSnapshot,
        prev_close: float,
        market_open_index: int | None,
    ) -> dict | None:
        """Gap > 1.5%, can't hold, volume declining.

        Action: SHORT on break below OR low.
        """
        if len(df) < 6:
            return None

        open_bar_pos = market_open_index if market_open_index is not None else 0
        if open_bar_pos >= len(df):
            return None

        open_price = df["Open"].iloc[open_bar_pos]
        gap_pct = (open_price - prev_close) / prev_close * 100.0

        if abs(gap_pct) < 1.5:
            return None

        gap_direction = "up" if gap_pct > 0 else "down"

        # Check that the gap is NOT holding -- price is moving back
        # toward prev_close
        recent_close = df["Close"].iloc[-1]

        if gap_direction == "up":
            # Fading: price dropping back toward prev_close
            gap_fill_pct = (open_price - recent_close) / (open_price - prev_close)
            fading = gap_fill_pct > 0.3  # filled at least 30% of the gap
        else:
            # Fading: price rising back toward prev_close
            gap_fill_pct = (recent_close - open_price) / (prev_close - open_price)
            fading = gap_fill_pct > 0.3

        if not fading:
            return None

        # Volume declining: compare last 3 bars avg to first 3 bars avg
        if len(df) >= 6:
            early_vol = df["Volume"].iloc[open_bar_pos:open_bar_pos + 3].mean()
            late_vol = df["Volume"].iloc[-3:].mean()
            volume_declining = early_vol > 0 and late_vol < early_vol
        else:
            volume_declining = False

        if not volume_declining:
            return None

        action = (
            "SHORT on break below OR low"
            if gap_direction == "up"
            else "LONG on break above OR high"
        )

        return {
            "name": "Gap and Fade",
            "confidence_modifier": 0,
            "action": action,
            "details": {
                "gap_pct": round(gap_pct, 3),
                "gap_direction": gap_direction,
                "prev_close": round(prev_close, 4),
                "open_price": round(float(open_price), 4),
                "current_close": round(float(recent_close), 4),
                "gap_fill_pct": round(float(gap_fill_pct) * 100, 1),
                "volume_declining": True,
            },
        }

    # ------------------------------------------------------------------
    # 9. Earnings Momentum
    # ------------------------------------------------------------------

    def _detect_earnings_momentum(
        self,
        df: pd.DataFrame,
        indicators: IndicatorSnapshot,
        prev_close: float,
        market_open_index: int | None,
    ) -> dict | None:
        """Post-gap after earnings, holds 2+ hours (120 1-min bars), RVOL > 2.0.

        Action: Trade gap direction, wider stop.
        """
        open_bar_pos = market_open_index if market_open_index is not None else 0
        if open_bar_pos >= len(df):
            return None

        open_price = df["Open"].iloc[open_bar_pos]
        gap_pct = (open_price - prev_close) / prev_close * 100.0

        # Need a meaningful gap (> 2% for earnings is typical)
        if abs(gap_pct) < 1.5:
            return None

        gap_direction = "up" if gap_pct > 0 else "down"

        # Check if held for 2+ hours = 120 1-minute bars
        bars_since_open = len(df) - open_bar_pos
        if bars_since_open < 120:
            return None

        # Verify the gap held: price didn't cross back through prev_close
        held = True
        for i in range(open_bar_pos, len(df)):
            bar = df.iloc[i]
            if gap_direction == "up" and bar["Close"] < prev_close:
                held = False
                break
            elif gap_direction == "down" and bar["Close"] > prev_close:
                held = False
                break

        if not held:
            return None

        if indicators.rvol < 2.0:
            return None

        return {
            "name": "Earnings Momentum",
            "confidence_modifier": 15,
            "action": "Trade gap direction, wider stop",
            "details": {
                "gap_pct": round(gap_pct, 3),
                "gap_direction": gap_direction,
                "bars_held": bars_since_open,
                "hours_held": round(bars_since_open / 60, 2),
                "rvol": round(indicators.rvol, 2),
            },
        }

    # ------------------------------------------------------------------
    # 10. Dead Cat Bounce
    # ------------------------------------------------------------------

    def _detect_dead_cat_bounce(
        self,
        df: pd.DataFrame,
        indicators: IndicatorSnapshot,
    ) -> dict | None:
        """EMA9 < EMA20 < EMA50, low volume bounce.

        Action: SHORT when stalls near EMA(20).
        """
        # Check bearish EMA alignment
        if not (indicators.ema9 < indicators.ema20 < indicators.ema50):
            return None

        # All EMAs must be populated (non-zero)
        if indicators.ema9 <= 0 or indicators.ema20 <= 0 or indicators.ema50 <= 0:
            return None

        price = indicators.price
        if price <= 0:
            return None

        # Price should be bouncing UP toward EMA20 (i.e. approaching from
        # below or near it)
        ema20 = indicators.ema20
        distance_to_ema20_pct = (ema20 - price) / ema20 * 100.0

        # Price within 1% of EMA20 or between EMA9 and EMA20
        near_ema20 = distance_to_ema20_pct < 1.0 and distance_to_ema20_pct > -0.5

        if not near_ema20:
            return None

        # Low volume bounce: RVOL < 1.0 signals weak conviction
        if indicators.rvol >= 1.0:
            return None

        # Confirm bounce: recent bars should show upward movement
        if len(df) >= 5:
            recent_low = df["Low"].iloc[-5:].min()
            current_close = df["Close"].iloc[-1]
            bouncing = current_close > recent_low
        else:
            bouncing = True

        if not bouncing:
            return None

        return {
            "name": "Dead Cat Bounce",
            "confidence_modifier": 0,
            "action": "SHORT when stalls near EMA(20)",
            "details": {
                "ema9": round(indicators.ema9, 4),
                "ema20": round(indicators.ema20, 4),
                "ema50": round(indicators.ema50, 4),
                "price": round(price, 4),
                "distance_to_ema20_pct": round(distance_to_ema20_pct, 3),
                "rvol": round(indicators.rvol, 2),
            },
        }

    # ------------------------------------------------------------------
    # 11. Absorption
    # ------------------------------------------------------------------

    def _detect_absorption(self, df: pd.DataFrame) -> dict | None:
        """Large volume, small range, then continuation.

        Action: +10 confidence for continuation.
        """
        if len(df) < 5:
            return None

        # Calculate average range and average volume
        ranges = (df["High"] - df["Low"]).values
        avg_range = float(np.mean(ranges)) if len(ranges) > 0 else 0
        avg_volume = float(df["Volume"].mean())

        if avg_range <= 0 or avg_volume <= 0:
            return None

        # Look for absorption bars in the recent window (last 10 bars,
        # excluding the very last 2 which are the "continuation")
        window_end = len(df) - 2
        window_start = max(0, window_end - 10)

        absorption_bar_idx: int | None = None

        for i in range(window_start, window_end):
            bar_range = ranges[i]
            bar_volume = df["Volume"].iloc[i]

            # High volume (> 1.5x average) with small range (body < 30%
            # of average range)
            body = abs(df["Close"].iloc[i] - df["Open"].iloc[i])
            if bar_volume > 1.5 * avg_volume and body < 0.30 * avg_range:
                absorption_bar_idx = i
                break  # Take the first one found

        if absorption_bar_idx is None:
            return None

        # Check for continuation: the bars after the absorption bar
        # should be directional (close moving consistently in one
        # direction)
        post_bars = df.iloc[absorption_bar_idx + 1:]
        if len(post_bars) < 2:
            return None

        first_post_close = post_bars["Close"].iloc[0]
        last_post_close = post_bars["Close"].iloc[-1]
        continuation_direction = "up" if last_post_close > first_post_close else "down"

        # Verify the move is meaningful (> 30% of average range)
        move = abs(last_post_close - first_post_close)
        if move < 0.30 * avg_range:
            return None

        return {
            "name": "Absorption",
            "confidence_modifier": 10,
            "action": "+10 confidence for continuation",
            "details": {
                "absorption_bar_index": absorption_bar_idx,
                "absorption_volume": int(df["Volume"].iloc[absorption_bar_idx]),
                "absorption_body": round(
                    abs(
                        df["Close"].iloc[absorption_bar_idx]
                        - df["Open"].iloc[absorption_bar_idx]
                    ),
                    4,
                ),
                "avg_range": round(avg_range, 4),
                "continuation_direction": continuation_direction,
                "continuation_move": round(float(move), 4),
            },
        }

    # ------------------------------------------------------------------
    # 12. ABCD Pattern
    # ------------------------------------------------------------------

    def _detect_abcd_pattern(self, df: pd.DataFrame) -> dict | None:
        """Measured move: AB = CD with Fibonacci ratios.

        Action: Enter at D, target at CD projection.
        """
        if len(df) < 20:
            return None

        swings = self._find_swing_points(df, lookback=5)

        if len(swings) < 4:
            return None

        # Try the most recent 4 swing points as an ABCD candidate
        # We need alternating high-low-high-low or low-high-low-high
        for start_idx in range(len(swings) - 3):
            candidate = swings[start_idx : start_idx + 4]
            a, b, c, d = candidate

            # Must alternate: high-low-high-low or low-high-low-high
            types = [s.kind for s in candidate]
            alternating = all(
                types[i] != types[i + 1] for i in range(len(types) - 1)
            )
            if not alternating:
                continue

            ab_range = abs(b.price - a.price)
            if ab_range <= 0:
                continue

            bc_retrace = abs(c.price - b.price)
            cd_range = abs(d.price - c.price)

            # Check BC retracement: 38.2% - 78.6% of AB
            if not self._check_fibonacci_ratio(bc_retrace, ab_range):
                continue

            # Check CD ~= AB (within 20% tolerance)
            if ab_range > 0:
                cd_ab_ratio = cd_range / ab_range
                if not (0.80 <= cd_ab_ratio <= 1.20):
                    continue
            else:
                continue

            # D should be near the current price (last 5 bars)
            d_recency = len(df) - 1 - d.index
            if d_recency > 5:
                continue

            # Determine direction based on pattern shape
            if a.kind == "low":
                # Bullish ABCD: A(low) B(high) C(low) D(high) -- but
                # D is the completion, so we expect a reversal down
                direction = "short"
                target = d.price - cd_range
            else:
                # Bearish ABCD: A(high) B(low) C(high) D(low) -- expect
                # reversal up
                direction = "long"
                target = d.price + cd_range

            return {
                "name": "ABCD Pattern",
                "confidence_modifier": 0,
                "action": "Enter at D, target at CD projection",
                "details": {
                    "A": {"index": a.index, "price": round(a.price, 4), "type": a.kind},
                    "B": {"index": b.index, "price": round(b.price, 4), "type": b.kind},
                    "C": {"index": c.index, "price": round(c.price, 4), "type": c.kind},
                    "D": {"index": d.index, "price": round(d.price, 4), "type": d.kind},
                    "ab_range": round(ab_range, 4),
                    "bc_retrace_pct": round(bc_retrace / ab_range * 100, 1),
                    "cd_ab_ratio": round(cd_ab_ratio, 3),
                    "direction": direction,
                    "target": round(target, 4),
                },
            }

        return None

    # ==================================================================
    # Helper methods
    # ==================================================================

    @staticmethod
    def _calc_or_width_20d_avg(daily_bars: pd.DataFrame) -> float:
        """Average opening range width over the last 20 trading days.

        The opening range is approximated from daily bars as:
            ``High - Low`` of the first portion of each day (proxy).

        Since daily bars don't contain intraday granularity, we use the
        daily range as a proxy that scales proportionally with intraday
        OR width.  In production this should be replaced with a cached
        table of actual 5-min OR widths per day.

        Parameters
        ----------
        daily_bars : pd.DataFrame
            Daily OHLCV data.  Must have at least 1 row.

        Returns
        -------
        float
            Average OR width.  Returns ``0.0`` if insufficient data.
        """
        if daily_bars is None or daily_bars.empty:
            return 0.0

        if "High" not in daily_bars.columns or "Low" not in daily_bars.columns:
            return 0.0

        # Use last 20 days (or however many are available)
        bars = daily_bars.iloc[-20:]
        daily_ranges = bars["High"] - bars["Low"]

        # OR is typically ~40-60% of the full daily range; use 0.5 as
        # a reasonable multiplier for approximation
        avg_daily_range = float(daily_ranges.mean())
        or_width_approx = avg_daily_range * 0.5

        return or_width_approx if or_width_approx > 0 else 0.0

    @staticmethod
    def _detect_reversal_candle(bar: pd.Series) -> bool:
        """Determine if a bar is a reversal candle.

        A reversal candle has its close opposite to the direction of the
        move implied by the bar's range.  Specifically:
        - If the bar made a higher high (bullish push), the close should
          be near the low (bearish reversal).
        - If the bar made a lower low (bearish push), the close should
          be near the high (bullish reversal).

        We use the wick ratio: the close is in the opposite half of the
        bar's range from the extreme.

        Parameters
        ----------
        bar : pd.Series
            A single OHLCV bar with Open, High, Low, Close.

        Returns
        -------
        bool
        """
        high = float(bar["High"])
        low = float(bar["Low"])
        open_price = float(bar["Open"])
        close = float(bar["Close"])

        bar_range = high - low
        if bar_range <= 0:
            return False

        midpoint = low + bar_range / 2.0

        # Bar pushed up (high > open) but closed below midpoint
        pushed_up = high > open_price and close < midpoint
        # Bar pushed down (low < open) but closed above midpoint
        pushed_down = low < open_price and close > midpoint

        return pushed_up or pushed_down

    @staticmethod
    def _find_swing_points(
        df: pd.DataFrame,
        lookback: int = 5,
    ) -> list[SwingPoint]:
        """Identify swing highs and swing lows in a price series.

        A swing high at bar ``i`` means ``High[i]`` is the highest high
        in the window ``[i - lookback, i + lookback]``.  Similarly for
        swing lows using the ``Low`` column.

        Parameters
        ----------
        df : pd.DataFrame
            OHLCV data.
        lookback : int
            Number of bars on each side to confirm the swing.

        Returns
        -------
        list[SwingPoint]
            Sorted by index (chronological).
        """
        swings: list[SwingPoint] = []
        n = len(df)

        if n < 2 * lookback + 1:
            return swings

        highs = df["High"].values
        lows = df["Low"].values

        for i in range(lookback, n - lookback):
            # Swing High: highest high in the window
            window_highs = highs[i - lookback : i + lookback + 1]
            if highs[i] == window_highs.max() and np.sum(window_highs == highs[i]) == 1:
                swings.append(SwingPoint(index=i, price=float(highs[i]), kind="high"))

            # Swing Low: lowest low in the window
            window_lows = lows[i - lookback : i + lookback + 1]
            if lows[i] == window_lows.min() and np.sum(window_lows == lows[i]) == 1:
                swings.append(SwingPoint(index=i, price=float(lows[i]), kind="low"))

        # Sort by index (should already be, but enforce)
        swings.sort(key=lambda s: s.index)

        # Remove consecutive same-type swings (keep the more extreme one)
        filtered: list[SwingPoint] = []
        for sp in swings:
            if filtered and filtered[-1].kind == sp.kind:
                # Same type consecutive -- keep the more extreme
                prev = filtered[-1]
                if sp.kind == "high" and sp.price > prev.price:
                    filtered[-1] = sp
                elif sp.kind == "low" and sp.price < prev.price:
                    filtered[-1] = sp
            else:
                filtered.append(sp)

        return filtered

    @staticmethod
    def _check_fibonacci_ratio(bc_retrace: float, ab_range: float) -> bool:
        """Check if the BC retracement falls within the 38.2%-78.6% Fibonacci zone.

        Parameters
        ----------
        bc_retrace : float
            Absolute price distance of the BC leg.
        ab_range : float
            Absolute price distance of the AB leg.

        Returns
        -------
        bool
            ``True`` if ``bc_retrace / ab_range`` is between 0.382 and 0.786.
        """
        if ab_range <= 0:
            return False

        ratio = bc_retrace / ab_range
        return 0.382 <= ratio <= 0.786
