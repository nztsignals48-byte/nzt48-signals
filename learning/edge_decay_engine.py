"""
NZT-48 Learning Module: Intraday Edge Decay Engine
The institutional-grade alpha curve tracker.

Core insight: trading edge is NOT constant throughout the day.
Morning has the most alpha, midday is a dead zone, and the close has a
second (weaker) window. This engine tracks and learns the actual alpha
curve per strategy, per regime, and detects session structure in real-time.

Capabilities:
1. Time Window Alpha Tracking — 30-min bucket performance (win rate, avg R, expectancy)
2. Dynamic Time Confidence Adjustment — boost/penalize confidence by time of day
3. Session Structure Detection — AM trend, reversal day, choppy range
4. Optimal Entry Windows — statistically best 30-min windows per strategy
5. Fatigue Detection — quality degradation after N trades per session
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Optional

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config as cfg

logger = logging.getLogger("nzt48.learning.edge_decay")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# US regular-hours 30-minute buckets: 9:30, 10:00, ..., 15:30 (13 buckets)
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)

BUCKET_LABELS: list[str] = [
    "09:30-10:00", "10:00-10:30", "10:30-11:00", "11:00-11:30",
    "11:30-12:00", "12:00-12:30", "12:30-13:00", "13:00-13:30",
    "13:30-14:00", "14:00-14:30", "14:30-15:00", "15:00-15:30",
    "15:30-16:00",
]

# Fatigue defaults
DEFAULT_FATIGUE_THRESHOLD = 8
FATIGUE_QUALITY_CURVE = {
    # trades_today -> quality multiplier (1.0 = no degradation)
    0: 1.00, 1: 1.00, 2: 1.00, 3: 1.00,
    4: 1.00, 5: 0.98, 6: 0.95, 7: 0.90,
    8: 0.85, 9: 0.78, 10: 0.70, 11: 0.60,
    12: 0.50,
}

# Session classification thresholds
SESSION_TREND_THRESHOLD = 0.005      # 0.5% cumulative move to consider trending
SESSION_REVERSAL_MIN_SWING = 0.003   # 0.3% swing required for reversal detection
SESSION_CHOP_MAX_RANGE = 0.008       # 0.8% total range for choppy classification


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class BucketStats:
    """Accumulated statistics for one 30-minute time bucket."""
    wins: int = 0
    losses: int = 0
    total_r: float = 0.0
    r_values: list[float] = field(default_factory=list)

    @property
    def trades(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> float:
        return (self.wins / self.trades * 100) if self.trades > 0 else 0.0

    @property
    def avg_r(self) -> float:
        return (self.total_r / self.trades) if self.trades > 0 else 0.0

    @property
    def expectancy(self) -> float:
        """Expectancy = (WR * avg_win_R) - ((1 - WR) * avg_loss_R)."""
        if self.trades < 1:
            return 0.0
        winners = [r for r in self.r_values if r > 0]
        losers = [r for r in self.r_values if r <= 0]
        avg_win = (sum(winners) / len(winners)) if winners else 0.0
        avg_loss = (abs(sum(losers)) / len(losers)) if losers else 0.0
        wr = self.wins / self.trades
        return (wr * avg_win) - ((1 - wr) * avg_loss)

    def to_dict(self) -> dict:
        return {
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 1),
            "avg_r": round(self.avg_r, 3),
            "expectancy": round(self.expectancy, 4),
        }


# ---------------------------------------------------------------------------
# Edge Decay Engine
# ---------------------------------------------------------------------------

class EdgeDecayEngine:
    """Tracks how alpha decays (and resurges) across the trading day.

    Three-dimensional bucketing:
        time_bucket  x  strategy  x  regime  ->  BucketStats

    Provides real-time confidence adjustments based on historical
    alpha per bucket, session structure detection, optimal entry
    windows, and trader fatigue modelling.
    """

    # Minimum trades in a bucket before it influences confidence
    MIN_BUCKET_TRADES = 5
    # Maximum absolute confidence adjustment from time-of-day
    MAX_TIME_ADJ = 15

    def __init__(self) -> None:
        # Primary store: bucket_key -> BucketStats
        # bucket_key = "BUCKET|STRATEGY|REGIME" or "BUCKET|_all_|_all_"
        self._buckets: dict[str, BucketStats] = defaultdict(BucketStats)

        # Aggregate by bucket only (across all strategies/regimes)
        self._global_buckets: dict[str, BucketStats] = defaultdict(BucketStats)

        # Per-session tracking for fatigue and structure
        self._today_trade_count: int = 0
        self._today_date: Optional[str] = None

        # Intraday momentum state (set at 10:00 ET each day)
        self._first_hour_return: Optional[float] = None
        self._first_hour_return_date: Optional[str] = None

        # Fatigue configuration
        self.fatigue_threshold: int = DEFAULT_FATIGUE_THRESHOLD

        # History for session classification training
        self._session_history: list[dict] = []

        logger.info("EdgeDecayEngine initialised: %d time buckets", len(BUCKET_LABELS))

    # ------------------------------------------------------------------
    # Bucket helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _time_to_bucket(t: time) -> Optional[str]:
        """Map a wall-clock time to the corresponding 30-min bucket label.

        Returns None if outside regular trading hours.
        """
        if t < MARKET_OPEN or t >= MARKET_CLOSE:
            return None

        # Minutes elapsed since market open
        minutes = (t.hour * 60 + t.minute) - (MARKET_OPEN.hour * 60 + MARKET_OPEN.minute)
        bucket_idx = minutes // 30

        if 0 <= bucket_idx < len(BUCKET_LABELS):
            return BUCKET_LABELS[bucket_idx]
        return None

    @staticmethod
    def _parse_entry_time(entry_time: str | datetime) -> Optional[time]:
        """Parse an entry_time string or datetime into a time object."""
        try:
            if isinstance(entry_time, datetime):
                return entry_time.time()
            dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
            return dt.time()
        except (ValueError, TypeError, AttributeError):
            return None

    def _bucket_key(self, bucket: str, strategy: str, regime: str) -> str:
        """Composite key for the 3D bucket store."""
        return f"{bucket}|{strategy}|{regime}"

    # ------------------------------------------------------------------
    # Trade recording
    # ------------------------------------------------------------------

    def record_trade(
        self,
        strategy: str,
        regime: str,
        entry_time: str | datetime,
        r_multiple: float,
    ) -> None:
        """Record a completed trade into the time-bucketed alpha tracker.

        Args:
            strategy: Strategy name (e.g. "S1_ORB", "S4_VWAP").
            regime: Market regime at entry (e.g. "TRENDING_UP", "CHOPPY").
            entry_time: ISO-format timestamp or datetime of trade entry.
            r_multiple: Realised R-multiple of the trade.
        """
        t = self._parse_entry_time(entry_time)
        if t is None:
            logger.debug("Could not parse entry_time: %s", entry_time)
            return

        bucket = self._time_to_bucket(t)
        if bucket is None:
            logger.debug("Entry time %s outside regular hours", t)
            return

        is_win = r_multiple > 0

        # --- Update strategy x regime bucket ---
        key = self._bucket_key(bucket, strategy, regime)
        stats = self._buckets[key]
        if is_win:
            stats.wins += 1
        else:
            stats.losses += 1
        stats.total_r += r_multiple
        stats.r_values.append(r_multiple)

        # --- Update global (all strategies, all regimes) bucket ---
        gstats = self._global_buckets[bucket]
        if is_win:
            gstats.wins += 1
        else:
            gstats.losses += 1
        gstats.total_r += r_multiple
        gstats.r_values.append(r_multiple)

        # --- Fatigue tracking (reset on new day) ---
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._today_date != today:
            self._today_date = today
            self._today_trade_count = 0
        self._today_trade_count += 1

        logger.debug(
            "EDGE_DECAY record: bucket=%s strategy=%s regime=%s R=%.2f "
            "(bucket_trades=%d, today_count=%d)",
            bucket, strategy, regime, r_multiple,
            self._global_buckets[bucket].trades,
            self._today_trade_count,
        )

    # ------------------------------------------------------------------
    # Dynamic Time Confidence Adjustment
    # ------------------------------------------------------------------

    def get_time_adjustment(
        self,
        strategy: str,
        regime: str,
        current_time: datetime | time | str | None = None,
    ) -> int:
        """Compute a confidence adjustment based on time-of-day alpha.

        Uses historical expectancy for the current 30-min bucket to
        boost (positive) or penalise (negative) signal confidence.

        Lookup order:
            1. Strategy + regime specific bucket  (most precise)
            2. Strategy-only bucket  (all regimes)
            3. Global bucket  (all strategies, all regimes)

        Args:
            strategy: Strategy name.
            regime: Current regime.
            current_time: The time to evaluate. Defaults to now (UTC).

        Returns:
            Integer in [-15, +15]. Positive = historically strong bucket,
            negative = historically weak / dead zone.
        """
        # Resolve current time
        if current_time is None:
            t = datetime.now(timezone.utc).time()
        elif isinstance(current_time, str):
            parsed = self._parse_entry_time(current_time)
            t = parsed if parsed else datetime.now(timezone.utc).time()
        elif isinstance(current_time, datetime):
            t = current_time.time()
        else:
            t = current_time

        bucket = self._time_to_bucket(t)
        if bucket is None:
            return 0  # Outside market hours, no adjustment

        # Try strategy + regime specific data
        key = self._bucket_key(bucket, strategy, regime)
        stats = self._buckets.get(key)
        if stats and stats.trades >= self.MIN_BUCKET_TRADES:
            return self._expectancy_to_adjustment(stats.expectancy, stats.trades)

        # Fallback: strategy across all regimes
        strat_stats = self._aggregate_bucket_for_strategy(bucket, strategy)
        if strat_stats and strat_stats.trades >= self.MIN_BUCKET_TRADES:
            return self._expectancy_to_adjustment(strat_stats.expectancy, strat_stats.trades)

        # Fallback: global bucket
        gstats = self._global_buckets.get(bucket)
        if gstats and gstats.trades >= self.MIN_BUCKET_TRADES:
            return self._expectancy_to_adjustment(gstats.expectancy, gstats.trades)

        return 0  # Insufficient data

    def _aggregate_bucket_for_strategy(
        self, bucket: str, strategy: str
    ) -> Optional[BucketStats]:
        """Aggregate a bucket across all regimes for a given strategy."""
        agg = BucketStats()
        prefix = f"{bucket}|{strategy}|"
        for key, stats in self._buckets.items():
            if key.startswith(prefix):
                agg.wins += stats.wins
                agg.losses += stats.losses
                agg.total_r += stats.total_r
                agg.r_values.extend(stats.r_values)
        return agg if agg.trades > 0 else None

    def _expectancy_to_adjustment(self, expectancy: float, trades: int) -> int:
        """Convert a bucket's expectancy to a confidence adjustment.

        Scale:
            expectancy >= 0.8R  and  trades >= 20  ->  +15
            expectancy >= 0.5R  and  trades >= 15  ->  +10
            expectancy >= 0.2R  and  trades >= 10  ->  +5
            expectancy  ~ 0.0R                      ->   0
            expectancy <= -0.1R and  trades >= 10  ->  -5
            expectancy <= -0.3R and  trades >= 15  ->  -10
            expectancy <= -0.5R and  trades >= 20  ->  -15
        """
        # Confidence scales with both magnitude and sample size
        confidence_factor = min(1.0, trades / 20)

        if expectancy >= 0.8:
            raw = 15
        elif expectancy >= 0.5:
            raw = 10
        elif expectancy >= 0.2:
            raw = 5
        elif expectancy >= -0.1:
            raw = 0
        elif expectancy >= -0.3:
            raw = -5
        elif expectancy >= -0.5:
            raw = -10
        else:
            raw = -15

        adjusted = int(raw * confidence_factor)
        return max(-self.MAX_TIME_ADJ, min(self.MAX_TIME_ADJ, adjusted))

    # ------------------------------------------------------------------
    # Session Structure Detection
    # ------------------------------------------------------------------

    def classify_session(self, price_changes_30min: list[float]) -> str:
        """Classify the current session's structure from 30-min price changes.

        Takes a list of fractional price changes for each completed 30-min
        bar so far today. E.g. [0.003, 0.001, -0.002, ...] where 0.003 = +0.3%.

        Session types:
            "am_trend"      - Trend established by 10:30 (first 2 bars), holds all day.
                              Strong directional conviction in the AM persists.
            "reversal_day"  - AM trend (first 3 bars) reverses after 11:00.
                              The AM move is a trap.
            "choppy_range"  - No sustained directional move. Total range is small.
                              Dead zone day, minimal edge.
            "pm_trend"      - Flat AM, trend emerges after 13:00 (bar 7+).
                              Late-day momentum.
            "unknown"       - Not enough data or doesn't fit a clean pattern.

        Args:
            price_changes_30min: List of fractional price changes per 30-min bar.
                                 Index 0 = 9:30-10:00, index 1 = 10:00-10:30, etc.

        Returns:
            Session type string.
        """
        if not price_changes_30min or len(price_changes_30min) < 2:
            return "unknown"

        n = len(price_changes_30min)
        cumulative = []
        running = 0.0
        for change in price_changes_30min:
            running += change
            cumulative.append(running)

        total_move = cumulative[-1]
        abs_total = abs(total_move)

        # Range: max cumulative - min cumulative
        peak = max(cumulative)
        trough = min(cumulative)
        total_range = peak - trough

        # AM move: cumulative through first 2 bars (9:30-10:30)
        am_move = cumulative[min(1, n - 1)]
        abs_am = abs(am_move)

        # Mid-session reversal check: did the direction flip after bar 3?
        am_direction = 1 if am_move > 0 else -1 if am_move < 0 else 0

        # --- Choppy Range ---
        if total_range < SESSION_CHOP_MAX_RANGE and abs_total < SESSION_TREND_THRESHOLD:
            return "choppy_range"

        # --- AM Trend ---
        # Strong AM move that persists: AM move and final move same sign,
        # AM contributes > 40% of final move, final move is significant
        if (abs_am >= SESSION_TREND_THRESHOLD
                and abs_total >= SESSION_TREND_THRESHOLD
                and am_direction != 0
                and (total_move * am_move > 0)  # Same direction
                and abs_am >= abs_total * 0.4):
            # Check for reversal: did we retrace significantly after bar 3?
            if n >= 5:
                post_am_low = min(cumulative[2:]) if am_direction > 0 else 0
                post_am_high = max(cumulative[2:]) if am_direction < 0 else 0
                # If we retraced > 60% of the AM move but then recovered, still AM trend
                if am_direction > 0 and (am_move - post_am_low) > abs_am * 0.6:
                    # Check if it recovered
                    if total_move > am_move * 0.4:
                        return "am_trend"
                    else:
                        return "reversal_day"
                elif am_direction < 0 and (post_am_high - am_move) > abs_am * 0.6:
                    if total_move < am_move * 0.4:
                        return "am_trend"
                    else:
                        return "reversal_day"
            return "am_trend"

        # --- Reversal Day ---
        # AM showed a clear direction but final move is opposite or flat
        if (abs_am >= SESSION_REVERSAL_MIN_SWING
                and n >= 4
                and am_direction != 0
                and total_move * am_move <= 0):  # Opposite or zero
            return "reversal_day"

        # --- PM Trend ---
        # Flat AM (small move through bar 4), then significant move after
        if n >= 7:
            am_flat = abs(cumulative[min(3, n - 1)]) < SESSION_TREND_THRESHOLD
            pm_move = abs(cumulative[-1] - cumulative[min(6, n - 1)])
            if am_flat and pm_move >= SESSION_TREND_THRESHOLD:
                return "pm_trend"

        # --- Fallback: if the total move is large but doesn't fit AM/reversal ---
        if abs_total >= SESSION_TREND_THRESHOLD:
            return "am_trend"  # Default to trend if there is a clear move

        return "unknown"

    # ------------------------------------------------------------------
    # Optimal Entry Windows
    # ------------------------------------------------------------------

    def get_optimal_windows(self, strategy: str | None = None) -> list[dict]:
        """Compute the statistically optimal 30-min entry windows.

        For each bucket, returns expectancy and trade count.
        Results are sorted by expectancy descending.

        Args:
            strategy: If provided, filter to this strategy only.
                      If None, returns global (all-strategy) windows.

        Returns:
            List of dicts:
                {"window": "9:30-10:00", "expectancy": 0.45, "avg_r": 0.32,
                 "win_rate": 62.5, "trades": 50}
        """
        results = []

        for bucket_label in BUCKET_LABELS:
            if strategy:
                # Aggregate across all regimes for this strategy
                stats = self._aggregate_bucket_for_strategy(bucket_label, strategy)
            else:
                stats = self._global_buckets.get(bucket_label)

            if stats is None or stats.trades < 1:
                results.append({
                    "window": bucket_label,
                    "expectancy": 0.0,
                    "avg_r": 0.0,
                    "win_rate": 0.0,
                    "trades": 0,
                })
                continue

            results.append({
                "window": bucket_label,
                "expectancy": round(stats.expectancy, 4),
                "avg_r": round(stats.avg_r, 3),
                "win_rate": round(stats.win_rate, 1),
                "trades": stats.trades,
            })

        # Sort by expectancy (best first)
        results.sort(key=lambda x: x["expectancy"], reverse=True)
        return results

    # ------------------------------------------------------------------
    # Fatigue Detection
    # ------------------------------------------------------------------

    def check_fatigue(self, trades_today: int) -> dict:
        """Assess whether trade quality is degraded due to volume fatigue.

        Research consistently shows that after a threshold number of trades,
        decision quality drops. This models a smooth degradation curve.

        Args:
            trades_today: Number of trades taken so far today.

        Returns:
            Dict with:
                fatigued (bool): Whether the threshold has been crossed.
                quality_penalty (float): 0.0 to 1.0 reduction factor.
                    0.0 = no penalty, 0.5 = half quality, 1.0 = total degradation.
                trades_today (int): Echo back for context.
                trades_remaining (int): Estimated quality trades left before
                    severe degradation (quality < 0.7).
                message (str): Human-readable assessment.
        """
        fatigued = trades_today >= self.fatigue_threshold

        # Look up quality from curve, extrapolate for high counts
        if trades_today in FATIGUE_QUALITY_CURVE:
            quality = FATIGUE_QUALITY_CURVE[trades_today]
        elif trades_today > max(FATIGUE_QUALITY_CURVE.keys()):
            # Aggressive degradation beyond the curve
            quality = max(0.1, 0.50 - (trades_today - 12) * 0.05)
        else:
            quality = 1.0

        quality_penalty = round(1.0 - quality, 3)

        # Trades remaining until quality drops below 0.7
        trades_remaining = 0
        if not fatigued:
            for n in range(trades_today, max(FATIGUE_QUALITY_CURVE.keys()) + 5):
                q = FATIGUE_QUALITY_CURVE.get(n, max(0.1, 0.50 - (n - 12) * 0.05))
                if q < 0.70:
                    trades_remaining = n - trades_today
                    break
            else:
                trades_remaining = 0

        # Message
        if quality >= 0.95:
            message = f"Fresh: {trades_today} trades taken, full quality."
        elif quality >= 0.85:
            message = f"Warming up degradation: {trades_today} trades, quality at {quality:.0%}."
        elif quality >= 0.70:
            message = (
                f"Fatigue setting in: {trades_today} trades, quality at {quality:.0%}. "
                f"Tighten filters or reduce size."
            )
        else:
            message = (
                f"SEVERE FATIGUE: {trades_today} trades, quality at {quality:.0%}. "
                f"Consider stopping for the day."
            )

        return {
            "fatigued": fatigued,
            "quality_penalty": quality_penalty,
            "quality_multiplier": round(quality, 3),
            "trades_today": trades_today,
            "trades_remaining_quality": trades_remaining,
            "threshold": self.fatigue_threshold,
            "message": message,
        }

    # ------------------------------------------------------------------
    # Intraday Momentum Signal
    # ------------------------------------------------------------------

    @staticmethod
    def compute_intraday_momentum(first_hour_return: float) -> float:
        """Predict the last-hour directional bias from the first-hour return.

        Research (Gao, Han, Li, Zhou 2018) shows a positive autocorrelation
        (~0.10-0.15) between the first 30-60 minute return and the last
        30-minute return within the same trading day. The effect is strongest
        when the first-hour move is large (>0.5%).

        Args:
            first_hour_return: The percentage return of the first hour of
                trading (9:30-10:30 ET). E.g. 0.8 means +0.8%.

        Returns:
            Predicted directional bias for the last hour. Positive = up,
            negative = down. Magnitude reflects confidence (larger first-hour
            moves produce stronger predictions).
        """
        # Correlation coefficient from research: ~0.12
        correlation = 0.12
        # Predicted return direction (same sign, attenuated by correlation)
        predicted_bias = first_hour_return * correlation
        return predicted_bias

    def set_first_hour_return(self, first_hour_return: float) -> None:
        """Store the first-hour return. Called at ~10:00 ET each day.

        Args:
            first_hour_return: Percentage return from 9:30 to 10:00/10:30 ET.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._first_hour_return = first_hour_return
        self._first_hour_return_date = today
        logger.info(
            "Intraday momentum: first-hour return set to %.3f%% for %s",
            first_hour_return, today,
        )

    def get_intraday_momentum_bias(self) -> float:
        """Return a confidence adjustment based on intraday momentum.

        Logic:
        - Only active during the 15:30-16:00 window (last 30 minutes).
        - If the first-hour return aligns with the current signal direction,
          add +5 to +10 confidence points.
        - If counter to the signal direction, subtract -5 points.
        - Returns 0 outside the power-close window or if no first-hour data.

        Returns:
            Float confidence adjustment: +5 to +10 for aligned, -5 for counter,
            0 if inactive or no data.
        """
        # Check if we have today's first-hour return
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if (self._first_hour_return is None
                or self._first_hour_return_date != today):
            return 0.0

        # Check if we're in the 15:30-16:00 window
        now_et = datetime.now(timezone.utc).time()
        # Note: in production the time should be converted to ET.
        # Here we check against the bucket label approach.
        current_bucket = self._time_to_bucket(now_et)
        if current_bucket != "15:30-16:00":
            return 0.0

        fhr = self._first_hour_return
        predicted = self.compute_intraday_momentum(fhr)

        # Strong first-hour move (>0.5%) gets larger adjustment
        abs_fhr = abs(fhr)
        if abs_fhr > 0.5:
            # Strong alignment: +10
            if predicted > 0:
                return 10.0
            else:
                return -5.0
        elif abs_fhr > 0.2:
            # Moderate alignment: +5
            if predicted > 0:
                return 5.0
            else:
                return -5.0
        else:
            # Weak first-hour move: no meaningful signal
            return 0.0

    def get_time_of_day_scalar(
        self,
        current_time: datetime | time | str | None = None,
    ) -> float:
        """Return a time-of-day confidence scalar, with intraday momentum override.

        Base scalars per 30-min bucket (empirical alpha curve):
            09:30-10:00  1.00  (opening momentum — highest alpha)
            10:00-10:30  0.95
            10:30-11:00  0.85
            11:00-11:30  0.75
            11:30-12:00  0.60  (lunch chop begins)
            12:00-12:30  0.50
            12:30-13:00  0.50
            13:00-13:30  0.55
            13:30-14:00  0.60
            14:00-14:30  0.70
            14:30-15:00  0.75
            15:00-15:30  0.65
            15:30-16:00  0.50  (normally low, but overridden if intraday momentum)

        Override: If first-hour return > 0.5%, the 15:30-16:00 scalar is
        boosted from 0.50 to 0.80 (research shows strong intraday momentum
        persists into the close on big-move days).

        Args:
            current_time: The time to evaluate. Defaults to now (UTC).

        Returns:
            Float scalar in [0.0, 1.0] for confidence modulation.
        """
        # Default scalar map
        scalar_map = {
            "09:30-10:00": 1.00,
            "10:00-10:30": 0.95,
            "10:30-11:00": 0.85,
            "11:00-11:30": 0.75,
            "11:30-12:00": 0.60,
            "12:00-12:30": 0.50,
            "12:30-13:00": 0.50,
            "13:00-13:30": 0.55,
            "13:30-14:00": 0.60,
            "14:00-14:30": 0.70,
            "14:30-15:00": 0.75,
            "15:00-15:30": 0.65,
            "15:30-16:00": 0.50,
        }

        # Resolve current time
        if current_time is None:
            t = datetime.now(timezone.utc).time()
        elif isinstance(current_time, str):
            parsed = self._parse_entry_time(current_time)
            t = parsed if parsed else datetime.now(timezone.utc).time()
        elif isinstance(current_time, datetime):
            t = current_time.time()
        else:
            t = current_time

        bucket = self._time_to_bucket(t)
        if bucket is None:
            return 0.0  # Outside market hours

        scalar = scalar_map.get(bucket, 0.50)

        # Override for the last 30 minutes when intraday momentum is strong
        if bucket == "15:30-16:00":
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if (self._first_hour_return is not None
                    and self._first_hour_return_date == today
                    and abs(self._first_hour_return) > 0.5):
                scalar = 0.80
                logger.debug(
                    "Intraday momentum override: 15:30-16:00 scalar boosted "
                    "from 0.50 to 0.80 (first-hour return: %.3f%%)",
                    self._first_hour_return,
                )

        return scalar

    # ------------------------------------------------------------------
    # Full status / dashboard
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Get comprehensive status for the dashboard.

        Returns:
            Dict with alpha curve, session stats, fatigue state, and
            top/bottom time windows.
        """
        # Alpha curve: expectancy per bucket (global)
        alpha_curve = {}
        for bucket_label in BUCKET_LABELS:
            stats = self._global_buckets.get(bucket_label)
            if stats and stats.trades > 0:
                alpha_curve[bucket_label] = stats.to_dict()
            else:
                alpha_curve[bucket_label] = {
                    "trades": 0, "wins": 0, "losses": 0,
                    "win_rate": 0.0, "avg_r": 0.0, "expectancy": 0.0,
                }

        # Best and worst windows (with enough data)
        windows_with_data = [
            (label, self._global_buckets[label])
            for label in BUCKET_LABELS
            if self._global_buckets.get(label) and self._global_buckets[label].trades >= self.MIN_BUCKET_TRADES
        ]
        windows_with_data.sort(key=lambda x: x[1].expectancy, reverse=True)

        best_windows = [
            {"window": label, **stats.to_dict()}
            for label, stats in windows_with_data[:3]
        ]
        worst_windows = [
            {"window": label, **stats.to_dict()}
            for label, stats in windows_with_data[-3:]
        ] if len(windows_with_data) >= 3 else []

        # Dead zones: buckets with negative expectancy and enough data
        dead_zones = [
            {"window": label, **stats.to_dict()}
            for label, stats in windows_with_data
            if stats.expectancy < 0
        ]

        # Total trades across all buckets
        total_trades = sum(
            s.trades for s in self._global_buckets.values()
        )

        # Per-strategy breakdown: which strategies have data in which buckets
        strategy_set = set()
        for key in self._buckets:
            parts = key.split("|")
            if len(parts) >= 2:
                strategy_set.add(parts[1])

        fatigue = self.check_fatigue(self._today_trade_count)

        return {
            "total_trades_tracked": total_trades,
            "buckets": len(BUCKET_LABELS),
            "alpha_curve": alpha_curve,
            "best_windows": best_windows,
            "worst_windows": worst_windows,
            "dead_zones": dead_zones,
            "strategies_tracked": sorted(strategy_set),
            "today_trade_count": self._today_trade_count,
            "fatigue": fatigue,
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_state(self, conn: sqlite3.Connection) -> None:
        """Persist edge decay engine state to SQLite.

        Note: Relies on learning_state table created by database.py init_db().
        """

        # Serialise bucket stats
        buckets_data = {}
        for key, stats in self._buckets.items():
            buckets_data[key] = {
                "wins": stats.wins,
                "losses": stats.losses,
                "total_r": stats.total_r,
                "r_values": stats.r_values[-200:],  # Keep last 200 per bucket
            }

        global_data = {}
        for label, stats in self._global_buckets.items():
            global_data[label] = {
                "wins": stats.wins,
                "losses": stats.losses,
                "total_r": stats.total_r,
                "r_values": stats.r_values[-500:],  # Larger window for global
            }

        state = {
            "buckets": buckets_data,
            "global_buckets": global_data,
            "fatigue_threshold": self.fatigue_threshold,
            "session_history": self._session_history[-100:],
            "first_hour_return": self._first_hour_return,
            "first_hour_return_date": self._first_hour_return_date,
        }

        conn.execute(
            "INSERT OR REPLACE INTO learning_state (module, state_json, updated_at) VALUES (?, ?, ?)",
            ("edge_decay_engine", json.dumps(state), datetime.now(timezone.utc).isoformat()),
        )
        logger.info(
            "EdgeDecayEngine state saved: %d strategy buckets, %d global buckets",
            len(buckets_data), len(global_data),
        )

    def load_state(self, conn: sqlite3.Connection) -> None:
        """Load edge decay engine state from SQLite."""
        try:
            row = conn.execute(
                "SELECT state_json FROM learning_state WHERE module = ?",
                ("edge_decay_engine",),
            ).fetchone()
        except Exception:
            return  # Table may not exist yet

        if not row:
            return

        state = json.loads(row["state_json"] if isinstance(row, sqlite3.Row) else row[0])

        # Restore strategy x regime buckets
        for key, data in state.get("buckets", {}).items():
            stats = BucketStats()
            stats.wins = data["wins"]
            stats.losses = data["losses"]
            stats.total_r = data["total_r"]
            stats.r_values = data.get("r_values", [])
            self._buckets[key] = stats

        # Restore global buckets
        for label, data in state.get("global_buckets", {}).items():
            stats = BucketStats()
            stats.wins = data["wins"]
            stats.losses = data["losses"]
            stats.total_r = data["total_r"]
            stats.r_values = data.get("r_values", [])
            self._global_buckets[label] = stats

        self.fatigue_threshold = state.get("fatigue_threshold", DEFAULT_FATIGUE_THRESHOLD)
        self._session_history = state.get("session_history", [])
        self._first_hour_return = state.get("first_hour_return")
        self._first_hour_return_date = state.get("first_hour_return_date")

        total = sum(s.trades for s in self._global_buckets.values())
        logger.info(
            "EdgeDecayEngine state loaded: %d total trades across %d buckets",
            total, len(self._global_buckets),
        )
