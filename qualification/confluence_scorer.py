"""
NZT-48 Trading System — Multi-Timeframe Confluence Scorer
Institutional traders never trade on a single timeframe.  This module scores
how many timeframes, volume behaviours, technical indicators, and cross-asset
signals agree with a proposed trade direction.

Confluence Score (0-100+):
  - Timeframe alignment: 5 timeframes x 20 pts each (max 100)
  - Volume confluence:   up to +10 bonus / -10 penalty
  - Indicator confluence: RSI +10, MACD +5, Bollinger +5 (max 20)
  - Cross-asset:         SPY +5, Sector ETF +5, VIX +5 (max 15)

A score of 60+ is the minimum for a standard momentum entry.
Mean-reversion strategies can work at 40+ because they intentionally fade
the prevailing multi-TF trend.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Direction
import config as cfg

logger = logging.getLogger("nzt48.confluence")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIMEFRAMES = ("5min", "15min", "1h", "daily", "weekly")

POINTS_PER_TIMEFRAME = 20

# Strategy -> minimum confluence score required
_STRATEGY_MINIMUMS: dict[str, int] = {
    "S1":  60,   # Regime trend following — needs alignment
    "S2":  60,   # Momentum breakout — needs alignment
    "S3":  40,   # Mean reversion — intentionally counter-trend
    "S4":  50,   # Catalyst / narrative — catalyst can override
    "S5":  50,   # PEAD earnings — fundamental catalyst
    "S6":  60,   # Macro regime
    "S7":  50,   # Sector rotation
    "S8":  40,   # Vol crush — options strategy, less TF-dependent
    "S9":  40,   # Pairs trade — relative, not directional
    "S10": 50,   # AI thematic
    "S11": 60,   # Hot scanner — momentum, needs alignment
    "S12": 50,   # Rebalance flow
    "S13": 60,   # Trend compound
    "S14": 50,   # Gamma squeeze
}

_DEFAULT_MINIMUM = 60


class ConfluenceScorer:
    """Multi-timeframe confluence scoring engine.

    Evaluates agreement across five timeframes (5-min through weekly),
    volume behaviour, technical indicator alignment, and cross-asset
    context to produce a single 0-100+ confluence score.

    The score is *additive* to the existing 5-layer confidence score,
    not a replacement.  It answers: "Is the broader market structure
    supporting or opposing this signal?"
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        """Initialise the scorer.  Stateless — all data passed per call."""
        logger.debug("ConfluenceScorer initialised")

    def score_confluence(
        self,
        signal_direction: str,
        timeframe_data: dict[str, dict[str, Any]],
        indicators: dict[str, Any],
        market_context: dict[str, Any],
    ) -> dict[str, Any]:
        """Compute the full multi-timeframe confluence score.

        Args:
            signal_direction: ``"LONG"`` or ``"SHORT"``.
            timeframe_data: Keyed by timeframe label (``"5min"``, ``"15min"``,
                ``"1h"``, ``"daily"``, ``"weekly"``).  Each value is a dict
                containing at minimum::

                    {
                        "price": float,
                        "ema20": float,
                        "ema50": float,
                        "vwap": float,          # optional on daily/weekly
                        "volume_trend": str,     # "increasing" | "decreasing" | "flat"
                        "rvol": float,           # relative volume (5min only)
                    }

            indicators: Aggregate indicator snapshot::

                    {
                        "rsi14": float,
                        "macd_histogram": float,
                        "bb_upper": float,
                        "bb_lower": float,
                        "bb_middle": float,
                        "price": float,
                    }

            market_context: Cross-asset and volume context::

                    {
                        "spy_trend": str,        # "BULLISH" | "BEARISH" | "NEUTRAL"
                        "sector_etf_trend": str,  # same
                        "vix_trend": str,         # "DECLINING" | "RISING" | "FLAT"
                        "volume_on_pullback": str, # "increasing" | "decreasing" | "flat"
                        "volume_on_breakout": str, # same
                        "rvol_5min": float,        # 5-min relative volume
                    }

        Returns:
            A dict with the full breakdown::

                {
                    "score": int,
                    "timeframe_alignment": int,
                    "volume_confluence": int,
                    "indicator_confluence": int,
                    "cross_asset_confluence": int,
                    "breakdown": {
                        "timeframes": {...},
                        "volume_details": {...},
                        "indicator_details": {...},
                        "cross_asset_details": {...},
                    },
                    "recommendation": str,
                }
        """
        direction = self._normalise_direction(signal_direction)

        # --- 1. Timeframe alignment ---
        tf_score, tf_details = self._score_timeframe_alignment(
            direction, timeframe_data,
        )

        # --- 2. Volume confluence ---
        vol_score, vol_details = self._score_volume_confluence(
            market_context,
        )

        # --- 3. Indicator confluence ---
        ind_score, ind_details = self._score_indicator_confluence(
            direction, indicators,
        )

        # --- 4. Cross-asset confluence ---
        xa_score, xa_details = self._score_cross_asset_confluence(
            direction, market_context,
        )

        # --- Aggregate ---
        total = tf_score + vol_score + ind_score + xa_score
        # Floor at 0 — negative is possible from penalties
        total = max(total, 0)

        recommendation = self._make_recommendation(total, tf_score)

        result = {
            "score": total,
            "timeframe_alignment": tf_score,
            "volume_confluence": vol_score,
            "indicator_confluence": ind_score,
            "cross_asset_confluence": xa_score,
            "breakdown": {
                "timeframes": tf_details,
                "volume_details": vol_details,
                "indicator_details": ind_details,
                "cross_asset_details": xa_details,
            },
            "recommendation": recommendation,
        }

        logger.info(
            "Confluence %s: TF=%d Vol=%d Ind=%d XA=%d -> TOTAL=%d | %s",
            direction, tf_score, vol_score, ind_score, xa_score,
            total, recommendation,
        )

        return result

    def get_minimum_confluence(self, strategy: str) -> int:
        """Return the minimum required confluence score for a strategy.

        Momentum strategies (S1, S2, S11, S13) require 60+ because they
        depend on multi-timeframe trend alignment.  Mean-reversion (S3)
        and relative-value (S8, S9) strategies can work at 40+ because
        they intentionally trade against the prevailing trend.

        Args:
            strategy: Strategy identifier (``"S1"`` through ``"S14"``).

        Returns:
            Minimum confluence score as an integer.
        """
        minimum = _STRATEGY_MINIMUMS.get(strategy, _DEFAULT_MINIMUM)
        logger.debug(
            "Minimum confluence for %s: %d", strategy, minimum,
        )
        return minimum

    def adjust_confidence(
        self,
        base_confidence: int,
        confluence_score: int,
    ) -> int:
        """Adjust a signal's base confidence based on confluence.

        The adjustment is intentionally asymmetric: strong confluence
        provides a moderate boost, but weak confluence applies a larger
        penalty.  This protects against false signals that look good on
        one timeframe but are fighting the broader structure.

        Adjustment table:
            >=80 confluence -> +10 confidence
            >=60 confluence -> +5  confidence
            >=40 confluence ->  0  (no change)
            >=20 confluence -> -10 confidence
            < 20 confluence -> -20 confidence

        The result is clamped to [0, 100].

        Args:
            base_confidence: Original confidence score (0-100).
            confluence_score: Confluence score from ``score_confluence()``.

        Returns:
            Adjusted confidence score (int, 0-100).
        """
        if confluence_score >= 80:
            adjustment = 10
        elif confluence_score >= 60:
            adjustment = 5
        elif confluence_score >= 40:
            adjustment = 0
        elif confluence_score >= 20:
            adjustment = -10
        else:
            adjustment = -20

        adjusted = max(0, min(100, base_confidence + adjustment))

        logger.info(
            "Confidence adjustment: base=%d confluence=%d adj=%+d -> final=%d",
            base_confidence, confluence_score, adjustment, adjusted,
        )

        return adjusted

    # ------------------------------------------------------------------
    # Timeframe alignment (0-100)
    # ------------------------------------------------------------------

    def _score_timeframe_alignment(
        self,
        direction: str,
        timeframe_data: dict[str, dict[str, Any]],
    ) -> tuple[int, dict[str, Any]]:
        """Score how many timeframes agree with the signal direction.

        For each timeframe, determine BULLISH / BEARISH / NEUTRAL by
        checking price vs EMA(20), EMA(50), and VWAP.  Award 20 points
        for each timeframe aligned with ``direction``.

        Args:
            direction: ``"LONG"`` or ``"SHORT"``.
            timeframe_data: Per-timeframe price/indicator data.

        Returns:
            Tuple of (score, detail dict).
        """
        aligned_count = 0
        details: dict[str, dict[str, Any]] = {}

        for tf in TIMEFRAMES:
            tf_data = timeframe_data.get(tf)
            if tf_data is None:
                details[tf] = {
                    "bias": "MISSING",
                    "aligned": False,
                    "reason": "no data",
                }
                continue

            bias = self._determine_timeframe_bias(tf_data, tf)
            is_aligned = self._bias_matches_direction(bias, direction)
            if is_aligned:
                aligned_count += 1

            details[tf] = {
                "bias": bias,
                "aligned": is_aligned,
                "price": tf_data.get("price", 0.0),
                "ema20": tf_data.get("ema20", 0.0),
                "ema50": tf_data.get("ema50", 0.0),
                "vwap": tf_data.get("vwap", 0.0),
            }

        score = aligned_count * POINTS_PER_TIMEFRAME

        details["_summary"] = {
            "aligned_count": aligned_count,
            "total_timeframes": len(TIMEFRAMES),
            "score": score,
        }

        logger.debug(
            "Timeframe alignment: %d/%d aligned (%d pts)",
            aligned_count, len(TIMEFRAMES), score,
        )

        return score, details

    def _determine_timeframe_bias(
        self,
        tf_data: dict[str, Any],
        timeframe: str,
    ) -> str:
        """Determine whether a single timeframe is BULLISH, BEARISH, or NEUTRAL.

        Decision logic (majority vote):
          - Price > EMA(20) -> bullish signal
          - Price > EMA(50) -> bullish signal
          - Price > VWAP    -> bullish signal (intraday TFs only)

        2 or 3 bullish signals = BULLISH.
        2 or 3 bearish signals = BEARISH.
        Otherwise = NEUTRAL.

        Args:
            tf_data: Dict with ``price``, ``ema20``, ``ema50``,
                and optionally ``vwap``.
            timeframe: Timeframe label (used to decide if VWAP applies).

        Returns:
            ``"BULLISH"``, ``"BEARISH"``, or ``"NEUTRAL"``.
        """
        price = tf_data.get("price", 0.0)
        ema20 = tf_data.get("ema20", 0.0)
        ema50 = tf_data.get("ema50", 0.0)
        vwap = tf_data.get("vwap", 0.0)

        if price <= 0:
            return "NEUTRAL"

        bullish_votes = 0
        bearish_votes = 0
        total_votes = 0

        # EMA20 check
        if ema20 > 0:
            total_votes += 1
            if price > ema20:
                bullish_votes += 1
            elif price < ema20:
                bearish_votes += 1

        # EMA50 check
        if ema50 > 0:
            total_votes += 1
            if price > ema50:
                bullish_votes += 1
            elif price < ema50:
                bearish_votes += 1

        # VWAP check — only meaningful for intraday timeframes
        intraday_timeframes = ("5min", "15min", "1h")
        if timeframe in intraday_timeframes and vwap > 0:
            total_votes += 1
            if price > vwap:
                bullish_votes += 1
            elif price < vwap:
                bearish_votes += 1

        if total_votes == 0:
            return "NEUTRAL"

        # Majority vote
        if bullish_votes >= 2:
            return "BULLISH"
        if bearish_votes >= 2:
            return "BEARISH"
        return "NEUTRAL"

    @staticmethod
    def _bias_matches_direction(bias: str, direction: str) -> bool:
        """Check whether a timeframe bias matches the signal direction.

        Args:
            bias: ``"BULLISH"``, ``"BEARISH"``, or ``"NEUTRAL"``.
            direction: ``"LONG"`` or ``"SHORT"``.

        Returns:
            ``True`` if the bias supports the direction.
        """
        if direction == "LONG" and bias == "BULLISH":
            return True
        if direction == "SHORT" and bias == "BEARISH":
            return True
        return False

    # ------------------------------------------------------------------
    # Volume confluence (-10 to +15)
    # ------------------------------------------------------------------

    def _score_volume_confluence(
        self,
        market_context: dict[str, Any],
    ) -> tuple[int, dict[str, str | int | float]]:
        """Score volume behaviour relative to the proposed trade.

        Bonuses:
          - Volume increasing on pullback to support: +10
          - RVOL > 2 on 5-min: +5

        Penalties:
          - Volume decreasing into breakout: -10 (weak breakout)

        Args:
            market_context: Dict with ``volume_on_pullback``,
                ``volume_on_breakout``, and ``rvol_5min`` keys.

        Returns:
            Tuple of (score, detail dict).
        """
        score = 0
        details: dict[str, str | int | float] = {}

        # Volume on pullback to support
        vol_pullback = market_context.get("volume_on_pullback", "flat")
        if vol_pullback == "increasing":
            score += 10
            details["pullback_volume"] = "+10 (increasing on pullback)"
        else:
            details["pullback_volume"] = f"0 ({vol_pullback})"

        # Volume on breakout
        vol_breakout = market_context.get("volume_on_breakout", "flat")
        if vol_breakout == "decreasing":
            score -= 10
            details["breakout_volume"] = "-10 (decreasing into breakout)"
        else:
            details["breakout_volume"] = f"0 ({vol_breakout})"

        # RVOL on 5-min
        rvol = market_context.get("rvol_5min", 0.0)
        if rvol > 2.0:
            score += 5
            details["rvol_5min"] = f"+5 (RVOL={rvol:.1f})"
        else:
            details["rvol_5min"] = f"0 (RVOL={rvol:.1f})"

        details["total"] = score

        logger.debug("Volume confluence: %+d", score)

        return score, details

    # ------------------------------------------------------------------
    # Indicator confluence (0-20)
    # ------------------------------------------------------------------

    def _score_indicator_confluence(
        self,
        direction: str,
        indicators: dict[str, Any],
    ) -> tuple[int, dict[str, str | int]]:
        """Score technical indicator agreement with the signal direction.

        Components:
          - RSI(14) in favourable zone for direction: +10
          - MACD histogram agreeing: +5
          - Bollinger Band position confirming: +5

        Args:
            direction: ``"LONG"`` or ``"SHORT"``.
            indicators: Dict with ``rsi14``, ``macd_histogram``,
                ``bb_upper``, ``bb_lower``, ``bb_middle``, ``price``.

        Returns:
            Tuple of (score, detail dict).
        """
        score = 0
        details: dict[str, str | int] = {}

        # RSI(14) — oversold for longs, overbought for shorts
        rsi = indicators.get("rsi14", 50.0)
        if direction == "LONG" and rsi <= 35:
            score += 10
            details["rsi"] = f"+10 (oversold RSI={rsi:.1f})"
        elif direction == "SHORT" and rsi >= 65:
            score += 10
            details["rsi"] = f"+10 (overbought RSI={rsi:.1f})"
        else:
            details["rsi"] = f"0 (RSI={rsi:.1f}, no edge)"

        # MACD histogram direction
        macd_hist = indicators.get("macd_histogram", 0.0)
        if direction == "LONG" and macd_hist > 0:
            score += 5
            details["macd"] = f"+5 (histogram={macd_hist:+.3f})"
        elif direction == "SHORT" and macd_hist < 0:
            score += 5
            details["macd"] = f"+5 (histogram={macd_hist:+.3f})"
        else:
            details["macd"] = f"0 (histogram={macd_hist:+.3f}, opposing)"

        # Bollinger Band position
        price = indicators.get("price", 0.0)
        bb_upper = indicators.get("bb_upper", 0.0)
        bb_lower = indicators.get("bb_lower", 0.0)
        bb_middle = indicators.get("bb_middle", 0.0)

        if price > 0 and bb_upper > 0 and bb_lower > 0:
            if direction == "LONG" and price <= bb_middle:
                # Price in lower half of BB — room to expand upward
                score += 5
                details["bollinger"] = "+5 (price in lower half, room to expand)"
            elif direction == "SHORT" and price >= bb_middle:
                # Price in upper half of BB — room to contract downward
                score += 5
                details["bollinger"] = "+5 (price in upper half, room to contract)"
            else:
                details["bollinger"] = "0 (BB position not confirming)"
        else:
            details["bollinger"] = "0 (BB data missing)"

        details["total"] = score

        logger.debug("Indicator confluence: %+d", score)

        return score, details

    # ------------------------------------------------------------------
    # Cross-asset confluence (0-15)
    # ------------------------------------------------------------------

    def _score_cross_asset_confluence(
        self,
        direction: str,
        market_context: dict[str, Any],
    ) -> tuple[int, dict[str, str | int]]:
        """Score cross-asset agreement with the signal direction.

        Components:
          - SPY trending same direction: +5
          - Sector ETF trending same direction: +5
          - VIX supporting (declining for longs, rising for shorts): +5

        Args:
            direction: ``"LONG"`` or ``"SHORT"``.
            market_context: Dict with ``spy_trend``, ``sector_etf_trend``,
                ``vix_trend``.

        Returns:
            Tuple of (score, detail dict).
        """
        score = 0
        details: dict[str, str | int] = {}

        # SPY direction
        spy_trend = market_context.get("spy_trend", "NEUTRAL")
        if self._bias_matches_direction(spy_trend, direction):
            score += 5
            details["spy"] = f"+5 (SPY={spy_trend})"
        else:
            details["spy"] = f"0 (SPY={spy_trend}, not aligned)"

        # Sector ETF direction
        sector_trend = market_context.get("sector_etf_trend", "NEUTRAL")
        if self._bias_matches_direction(sector_trend, direction):
            score += 5
            details["sector_etf"] = f"+5 (Sector={sector_trend})"
        else:
            details["sector_etf"] = f"0 (Sector={sector_trend}, not aligned)"

        # VIX behaviour
        vix_trend = market_context.get("vix_trend", "FLAT")
        vix_supports = (
            (direction == "LONG" and vix_trend == "DECLINING")
            or (direction == "SHORT" and vix_trend == "RISING")
        )
        if vix_supports:
            score += 5
            details["vix"] = f"+5 (VIX={vix_trend})"
        else:
            details["vix"] = f"0 (VIX={vix_trend}, not supporting)"

        details["total"] = score

        logger.debug("Cross-asset confluence: %+d", score)

        return score, details

    # ------------------------------------------------------------------
    # Recommendation
    # ------------------------------------------------------------------

    @staticmethod
    def _make_recommendation(total: int, tf_score: int) -> str:
        """Generate a human-readable recommendation from the scores.

        Args:
            total: Total confluence score.
            tf_score: Timeframe alignment sub-score.

        Returns:
            Recommendation string.
        """
        aligned_count = tf_score // POINTS_PER_TIMEFRAME if POINTS_PER_TIMEFRAME else 0

        if total >= 100:
            return (
                f"EXTREME CONFLUENCE ({aligned_count}/5 TF aligned, score {total}). "
                "All timeframes agree — full size, high conviction."
            )
        if total >= 80:
            return (
                f"STRONG CONFLUENCE ({aligned_count}/5 TF aligned, score {total}). "
                "Excellent multi-timeframe agreement — standard size or above."
            )
        if total >= 60:
            return (
                f"ACCEPTABLE CONFLUENCE ({aligned_count}/5 TF aligned, score {total}). "
                "Minimum threshold met for momentum strategies — standard size."
            )
        if total >= 40:
            return (
                f"WEAK CONFLUENCE ({aligned_count}/5 TF aligned, score {total}). "
                "Only suitable for mean-reversion or catalyst plays — reduce size."
            )
        if total >= 20:
            return (
                f"POOR CONFLUENCE ({aligned_count}/5 TF aligned, score {total}). "
                "Counter-trend setup — only take with extreme confidence and reduced size."
            )
        return (
            f"NO CONFLUENCE ({aligned_count}/5 TF aligned, score {total}). "
            "No multi-timeframe support — skip unless extraordinary catalyst."
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_direction(direction: str) -> str:
        """Normalise direction input to ``"LONG"`` or ``"SHORT"``.

        Accepts ``Direction`` enum values, raw strings, and common
        aliases (``"BUY"``/``"SELL"``).

        Args:
            direction: Direction string or ``Direction`` enum.

        Returns:
            ``"LONG"`` or ``"SHORT"``.

        Raises:
            ValueError: If direction cannot be normalised.
        """
        if isinstance(direction, Direction):
            return direction.value

        upper = str(direction).upper().strip()
        if upper in ("LONG", "BUY", "BULLISH"):
            return "LONG"
        if upper in ("SHORT", "SELL", "BEARISH"):
            return "SHORT"

        raise ValueError(
            f"Cannot normalise direction '{direction}'. "
            f"Expected LONG/SHORT/BUY/SELL/BULLISH/BEARISH."
        )
