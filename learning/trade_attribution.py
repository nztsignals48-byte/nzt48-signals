"""
learning/trade_attribution.py
==============================
AEGIS 0-10: TradeAttribution Record -- Blood Oath #4.

Cannot judge the 200-Trade Gate without knowing WHY trades won or lost.
This module provides deep per-trade attribution:

1. MFE/MAE in R-multiples (not just %, which hides risk-adjusted truth)
2. Entry timing score: how close to optimal entry within the bar range
3. Exit-also-true ablation: were alternative exit points superior?
4. Shadow markout verdicts: what happened 1m/5m/15m/30m after exit?

Schema-compatible with outcomes.jsonl -- adds fields, never breaks existing.
All new fields live under an "attribution" sub-dict in the outcome record.

Usage:
    from learning.trade_attribution import TradeAttributionEngine, TradeAttribution
    engine = TradeAttributionEngine()
    attr = engine.compute(
        entry_price=20.23, exit_price=19.59,
        stop_price=19.59, target_price=21.66,
        direction="LONG",
        entry_time=datetime(...), exit_time=datetime(...),
        price_series=df,  # DataFrame with OHLCV, DatetimeIndex
    )
    outcome_dict["attribution"] = attr.to_dict()
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    import pandas as pd
except ImportError:
    pd = None  # type: ignore[assignment]

logger = logging.getLogger("nzt48.learning.trade_attribution")


# ---------------------------------------------------------------------------
# Shadow markout intervals (minutes after exit)
# ---------------------------------------------------------------------------
MARKOUT_INTERVALS = [1, 5, 15, 30]


# ---------------------------------------------------------------------------
# Exit ablation: alternative exit strategies to test
# ---------------------------------------------------------------------------
EXIT_ABLATION_VARIANTS = [
    {"label": "trail_1atr", "desc": "1x ATR trailing stop"},
    {"label": "trail_2atr", "desc": "2x ATR trailing stop"},
    {"label": "time_exit_50pct", "desc": "Exit at 50% of holding duration"},
    {"label": "mfe_50pct", "desc": "Exit when 50% of MFE reached"},
    {"label": "mfe_75pct", "desc": "Exit when 75% of MFE reached"},
]


@dataclass
class MarkoutVerdict:
    """What happened to price N minutes after exit."""
    minutes_after: int = 0
    price_at_markout: float = 0.0
    pnl_vs_exit_pct: float = 0.0   # (markout - exit) / exit * 100
    pnl_vs_exit_r: float = 0.0     # In R-multiples from entry risk
    verdict: str = ""               # LEFT_MONEY | GOOD_EXIT | DODGED_REVERSAL

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExitAblationResult:
    """Result of testing an alternative exit strategy."""
    label: str = ""
    description: str = ""
    alt_exit_price: float = 0.0
    alt_pnl_r: float = 0.0
    actual_pnl_r: float = 0.0
    delta_r: float = 0.0           # alt_pnl_r - actual_pnl_r (positive = alt was better)
    verdict: str = ""               # SUPERIOR | INFERIOR | EQUIVALENT

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TradeAttribution:
    """
    Deep per-trade attribution record.
    Schema-compatible with outcomes.jsonl: lives under "attribution" key.
    """
    # -- Identity (links back to outcome) --
    signal_id: str = ""

    # -- MFE / MAE in R-multiples --
    mfe_r: float = 0.0              # Best unrealised P&L during trade, in R
    mae_r: float = 0.0              # Worst unrealised P&L during trade, in R
    mfe_price: float = 0.0          # Price at MFE
    mae_price: float = 0.0          # Price at MAE
    mfe_bar_index: int = 0          # Bar offset where MFE occurred
    mae_bar_index: int = 0          # Bar offset where MAE occurred
    mfe_minutes_in: int = 0         # Minutes from entry to MFE
    mae_minutes_in: int = 0         # Minutes from entry to MAE
    exit_efficiency_pct: float = 0.0  # actual_r / mfe_r * 100 (clamped 0-100)

    # -- MFE/MAE in percent (for backward compat) --
    mfe_pct: float = 0.0
    mae_pct: float = 0.0

    # -- Entry Timing Score --
    # For LONG: (daily_high - entry) / (daily_high - daily_low)
    # Score of 1.0 = entered at the daily low (perfect). 0.0 = entered at the high (worst).
    # For SHORT: (entry - daily_low) / (daily_high - daily_low)
    entry_timing_score: float = 0.0
    daily_high: float = 0.0
    daily_low: float = 0.0

    # -- Exit Ablation --
    # Was the exit signal also active at other points? Would alternatives be better?
    exit_ablation: list = field(default_factory=list)  # list[ExitAblationResult]
    best_alt_exit_label: str = ""     # label of best alternative
    best_alt_exit_delta_r: float = 0.0  # how much better it was

    # -- Shadow Markout Verdicts --
    # What happened to price 1m, 5m, 15m, 30m after exit?
    shadow_markouts: list = field(default_factory=list)  # list[MarkoutVerdict]
    markout_consensus: str = ""  # GOOD_EXIT | LEFT_MONEY | DODGED_REVERSAL | MIXED

    # -- Metadata --
    computed_at: str = ""
    version: str = "1.0"

    def to_dict(self) -> dict:
        d = {}
        for k, v in asdict(self).items():
            d[k] = v
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TradeAttribution":
        known_fields = cls.__dataclass_fields__
        filtered = {}
        for k, v in d.items():
            if k in known_fields:
                filtered[k] = v
        return cls(**filtered)


class TradeAttributionEngine:
    """
    Computes deep TradeAttribution from trade data.

    Core method: compute() takes entry/exit/stop/target prices, direction,
    timestamps, and the price series during the trade. Returns a
    TradeAttribution dataclass ready to embed in the outcome record.
    """

    def compute(
        self,
        entry_price: float,
        exit_price: float,
        stop_price: float,
        target_price: float,
        direction: str,
        entry_time: datetime,
        exit_time: datetime,
        price_series,          # pd.DataFrame with OHLCV, DatetimeIndex
        signal_id: str = "",
        post_exit_series=None,  # Optional: bars AFTER exit for markout analysis
    ) -> TradeAttribution:
        """
        Compute full trade attribution.

        Args:
            entry_price:  Fill price at entry.
            exit_price:   Fill price at exit.
            stop_price:   Stop-loss level.
            target_price: Target1 level.
            direction:    "LONG" or "SHORT".
            entry_time:   Entry timestamp (UTC).
            exit_time:    Exit timestamp (UTC).
            price_series: DataFrame of OHLCV bars during the trade window.
                         Must have DatetimeIndex and columns: High, Low, Close.
            signal_id:    Optional signal ID for linking.
            post_exit_series: Optional DataFrame of bars AFTER exit for markout.

        Returns:
            TradeAttribution with all fields populated.
        """
        attr = TradeAttribution(signal_id=signal_id)
        direction = direction.upper()

        # Risk distance (entry -> stop) in price units -- the "1R" denominator
        risk_distance = abs(entry_price - stop_price)
        if risk_distance < 1e-8:
            # Degenerate: use 1% of entry as fallback
            risk_distance = entry_price * 0.01
            logger.warning(
                "ATTRIBUTION %s: zero risk distance, using 1%% fallback (%.4f)",
                signal_id, risk_distance,
            )

        # 1. MFE / MAE
        self._compute_mfe_mae(
            attr, entry_price, direction, risk_distance, price_series, entry_time,
        )

        # 2. Exit efficiency
        actual_r = self._price_to_r(entry_price, exit_price, direction, risk_distance)
        if attr.mfe_r > 0:
            attr.exit_efficiency_pct = round(
                min(100.0, max(0.0, (actual_r / attr.mfe_r) * 100)), 2,
            )
        else:
            attr.exit_efficiency_pct = 0.0

        # 3. Entry timing score
        self._compute_entry_timing(attr, entry_price, direction, price_series)

        # 4. Exit ablation
        self._compute_exit_ablation(
            attr, entry_price, exit_price, stop_price, direction,
            risk_distance, actual_r, price_series, entry_time,
        )

        # 5. Shadow markout verdicts
        self._compute_shadow_markouts(
            attr, exit_price, direction, risk_distance,
            price_series, exit_time, post_exit_series,
        )

        attr.computed_at = datetime.now(timezone.utc).isoformat()

        logger.info(
            "ATTRIBUTION %s: MFE=%.2fR MAE=%.2fR entry_timing=%.2f "
            "exit_eff=%.0f%% markout=%s best_alt=%s(%.2fR)",
            signal_id, attr.mfe_r, attr.mae_r, attr.entry_timing_score,
            attr.exit_efficiency_pct, attr.markout_consensus,
            attr.best_alt_exit_label, attr.best_alt_exit_delta_r,
        )

        return attr

    # ------------------------------------------------------------------
    # 1. MFE / MAE computation
    # ------------------------------------------------------------------
    def _compute_mfe_mae(
        self,
        attr: TradeAttribution,
        entry_price: float,
        direction: str,
        risk_distance: float,
        price_series,
        entry_time: datetime,
    ) -> None:
        """Compute Maximum Favorable / Adverse Excursion in R-multiples."""
        if pd is None or price_series is None or price_series.empty:
            return

        mfe_price = entry_price
        mae_price = entry_price
        mfe_r = 0.0
        mae_r = 0.0
        mfe_bar_idx = 0
        mae_bar_idx = 0

        for i, (idx, row) in enumerate(price_series.iterrows()):
            high = float(row.get("High", row.get("high", entry_price)))
            low = float(row.get("Low", row.get("low", entry_price)))

            if direction == "LONG":
                # MFE: highest high relative to entry
                bar_best_r = (high - entry_price) / risk_distance
                bar_worst_r = (low - entry_price) / risk_distance
            else:
                # SHORT: MFE when price drops, MAE when price rises
                bar_best_r = (entry_price - low) / risk_distance
                bar_worst_r = (entry_price - high) / risk_distance

            if bar_best_r > mfe_r:
                mfe_r = bar_best_r
                mfe_price = high if direction == "LONG" else low
                mfe_bar_idx = i

            if bar_worst_r < mae_r:
                mae_r = bar_worst_r
                mae_price = low if direction == "LONG" else high
                mae_bar_idx = i

        attr.mfe_r = round(mfe_r, 4)
        attr.mae_r = round(mae_r, 4)
        attr.mfe_price = round(mfe_price, 4)
        attr.mae_price = round(mae_price, 4)
        attr.mfe_bar_index = mfe_bar_idx
        attr.mae_bar_index = mae_bar_idx

        # Percent-based for backward compat with existing outcomes schema
        if entry_price > 0:
            attr.mfe_pct = round((mfe_price - entry_price) / entry_price * 100, 4)
            attr.mae_pct = round((mae_price - entry_price) / entry_price * 100, 4)
            if direction == "SHORT":
                # For shorts, MFE% is negative price move, MAE% is positive price move
                attr.mfe_pct = round((entry_price - mfe_price) / entry_price * 100, 4)
                attr.mae_pct = round((entry_price - mae_price) / entry_price * 100, 4)

        # Time to MFE/MAE
        try:
            entry_ts = price_series.index[0]
            if mfe_bar_idx < len(price_series):
                mfe_ts = price_series.index[mfe_bar_idx]
                attr.mfe_minutes_in = max(0, int((mfe_ts - entry_ts).total_seconds() / 60))
            if mae_bar_idx < len(price_series):
                mae_ts = price_series.index[mae_bar_idx]
                attr.mae_minutes_in = max(0, int((mae_ts - entry_ts).total_seconds() / 60))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 2. Entry timing score
    # ------------------------------------------------------------------
    def _compute_entry_timing(
        self,
        attr: TradeAttribution,
        entry_price: float,
        direction: str,
        price_series,
    ) -> None:
        """
        Entry timing score: how close to optimal entry.

        LONG:  (daily_high - entry) / (daily_high - daily_low)
          => 1.0 if entered at daily low (perfect), 0.0 at daily high (worst)
        SHORT: (entry - daily_low) / (daily_high - daily_low)
          => 1.0 if entered at daily high (perfect), 0.0 at daily low (worst)
        """
        if pd is None or price_series is None or price_series.empty:
            return

        try:
            daily_high = float(price_series["High"].max()) if "High" in price_series.columns else float(price_series["high"].max())
            daily_low = float(price_series["Low"].min()) if "Low" in price_series.columns else float(price_series["low"].min())
        except Exception:
            return

        attr.daily_high = round(daily_high, 4)
        attr.daily_low = round(daily_low, 4)

        range_size = daily_high - daily_low
        if range_size < 1e-8:
            attr.entry_timing_score = 0.5  # No range = neutral
            return

        if direction == "LONG":
            # Lower entry = better for longs
            score = (daily_high - entry_price) / range_size
        else:
            # Higher entry = better for shorts
            score = (entry_price - daily_low) / range_size

        attr.entry_timing_score = round(max(0.0, min(1.0, score)), 4)

    # ------------------------------------------------------------------
    # 3. Exit ablation
    # ------------------------------------------------------------------
    def _compute_exit_ablation(
        self,
        attr: TradeAttribution,
        entry_price: float,
        exit_price: float,
        stop_price: float,
        direction: str,
        risk_distance: float,
        actual_r: float,
        price_series,
        entry_time: datetime,
    ) -> None:
        """
        Test alternative exit strategies against the actual exit.
        Each variant simulates a different exit rule over the same price path.
        """
        if pd is None or price_series is None or price_series.empty:
            attr.exit_ablation = []
            return

        results = []

        # Estimate ATR from the price series for trailing stop variants
        atr = self._estimate_atr(price_series)

        total_bars = len(price_series)
        half_bar = max(1, total_bars // 2)

        for variant in EXIT_ABLATION_VARIANTS:
            label = variant["label"]
            desc = variant["desc"]

            alt_exit_price = None

            if label == "trail_1atr":
                alt_exit_price = self._simulate_trailing_stop(
                    entry_price, stop_price, direction, price_series, atr * 1.0,
                )
            elif label == "trail_2atr":
                alt_exit_price = self._simulate_trailing_stop(
                    entry_price, stop_price, direction, price_series, atr * 2.0,
                )
            elif label == "time_exit_50pct":
                # Exit at the midpoint bar's close
                if half_bar < total_bars:
                    close_col = "Close" if "Close" in price_series.columns else "close"
                    alt_exit_price = float(price_series.iloc[half_bar][close_col])
            elif label == "mfe_50pct":
                alt_exit_price = self._simulate_mfe_target_exit(
                    entry_price, direction, risk_distance, price_series,
                    target_r_frac=0.50, mfe_r=attr.mfe_r,
                )
            elif label == "mfe_75pct":
                alt_exit_price = self._simulate_mfe_target_exit(
                    entry_price, direction, risk_distance, price_series,
                    target_r_frac=0.75, mfe_r=attr.mfe_r,
                )

            if alt_exit_price is None:
                continue

            alt_r = self._price_to_r(entry_price, alt_exit_price, direction, risk_distance)
            delta_r = round(alt_r - actual_r, 4)

            if delta_r > 0.05:
                verdict = "SUPERIOR"
            elif delta_r < -0.05:
                verdict = "INFERIOR"
            else:
                verdict = "EQUIVALENT"

            result = ExitAblationResult(
                label=label,
                description=desc,
                alt_exit_price=round(alt_exit_price, 4),
                alt_pnl_r=round(alt_r, 4),
                actual_pnl_r=round(actual_r, 4),
                delta_r=delta_r,
                verdict=verdict,
            )
            results.append(result)

        attr.exit_ablation = [r.to_dict() for r in results]

        # Find best alternative
        if results:
            best = max(results, key=lambda r: r.delta_r)
            if best.delta_r > 0.05:
                attr.best_alt_exit_label = best.label
                attr.best_alt_exit_delta_r = best.delta_r
            else:
                attr.best_alt_exit_label = "actual_was_best"
                attr.best_alt_exit_delta_r = 0.0

    def _simulate_trailing_stop(
        self,
        entry_price: float,
        initial_stop: float,
        direction: str,
        price_series,
        trail_distance: float,
    ) -> Optional[float]:
        """Simulate a trailing stop exit and return the exit price."""
        if trail_distance <= 0:
            return None

        trailing_stop = initial_stop
        close_col = "Close" if "Close" in price_series.columns else "close"
        high_col = "High" if "High" in price_series.columns else "high"
        low_col = "Low" if "Low" in price_series.columns else "low"

        for _idx, row in price_series.iterrows():
            high = float(row[high_col])
            low = float(row[low_col])
            close = float(row[close_col])

            if direction == "LONG":
                # Update trailing stop upward
                new_stop = high - trail_distance
                if new_stop > trailing_stop:
                    trailing_stop = new_stop
                # Check if stopped
                if low <= trailing_stop:
                    return trailing_stop
            else:
                # SHORT: trailing stop moves down
                new_stop = low + trail_distance
                if new_stop < trailing_stop:
                    trailing_stop = new_stop
                if high >= trailing_stop:
                    return trailing_stop

        # Never stopped: return last close
        return float(price_series.iloc[-1][close_col])

    def _simulate_mfe_target_exit(
        self,
        entry_price: float,
        direction: str,
        risk_distance: float,
        price_series,
        target_r_frac: float,
        mfe_r: float,
    ) -> Optional[float]:
        """
        Simulate exiting when price reaches target_r_frac of the MFE.
        E.g., if MFE was +3R and target_r_frac=0.50, exit at +1.5R.
        """
        if mfe_r <= 0:
            return None

        target_r = mfe_r * target_r_frac
        close_col = "Close" if "Close" in price_series.columns else "close"
        high_col = "High" if "High" in price_series.columns else "high"
        low_col = "Low" if "Low" in price_series.columns else "low"

        if direction == "LONG":
            target_price = entry_price + target_r * risk_distance
            for _idx, row in price_series.iterrows():
                if float(row[high_col]) >= target_price:
                    return target_price
        else:
            target_price = entry_price - target_r * risk_distance
            for _idx, row in price_series.iterrows():
                if float(row[low_col]) <= target_price:
                    return target_price

        # Never reached: return last close
        return float(price_series.iloc[-1][close_col])

    def _estimate_atr(self, price_series, period: int = 14) -> float:
        """Estimate ATR from the price series. Simple true-range average."""
        if pd is None or price_series is None or len(price_series) < 2:
            return 0.0

        high_col = "High" if "High" in price_series.columns else "high"
        low_col = "Low" if "Low" in price_series.columns else "low"
        close_col = "Close" if "Close" in price_series.columns else "close"

        true_ranges = []
        prev_close = None
        for _idx, row in price_series.iterrows():
            h = float(row[high_col])
            l = float(row[low_col])
            c = float(row[close_col])

            if prev_close is not None:
                tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
            else:
                tr = h - l
            true_ranges.append(tr)
            prev_close = c

        if not true_ranges:
            return 0.0

        # Use the last `period` values or all if fewer
        window = true_ranges[-period:]
        return sum(window) / len(window)

    # ------------------------------------------------------------------
    # 4. Shadow markout verdicts
    # ------------------------------------------------------------------
    def _compute_shadow_markouts(
        self,
        attr: TradeAttribution,
        exit_price: float,
        direction: str,
        risk_distance: float,
        price_series,
        exit_time: datetime,
        post_exit_series=None,
    ) -> None:
        """
        Compute what happened to price 1m, 5m, 15m, 30m after exit.

        Uses post_exit_series if provided, otherwise tries to find
        post-exit bars in price_series (if it extends past exit_time).
        """
        if pd is None:
            return

        # Build the post-exit bar set
        post_bars = None
        if post_exit_series is not None and not post_exit_series.empty:
            post_bars = post_exit_series
        elif price_series is not None and not price_series.empty:
            # Try to use bars after exit_time from the main series
            try:
                post_bars = price_series[price_series.index > exit_time]
            except Exception:
                pass

        if post_bars is None or post_bars.empty:
            attr.shadow_markouts = []
            attr.markout_consensus = "NO_DATA"
            return

        close_col = "Close" if "Close" in post_bars.columns else "close"
        verdicts = []
        left_money_count = 0
        dodged_reversal_count = 0

        for minutes in MARKOUT_INTERVALS:
            # Find the bar closest to exit_time + N minutes
            try:
                target_time = exit_time + timedelta(minutes=minutes)
                # Find closest bar at or after target_time
                future_bars = post_bars[post_bars.index >= target_time]
                if future_bars.empty:
                    # Use the last available bar if we have post-exit bars
                    if not post_bars.empty:
                        future_bars = post_bars.tail(1)
                    else:
                        continue

                markout_price = float(future_bars.iloc[0][close_col])
            except Exception:
                continue

            pnl_vs_exit_pct = round((markout_price - exit_price) / exit_price * 100, 4) if exit_price > 0 else 0.0

            # R-multiple: how much more (or less) we'd have made
            if direction == "LONG":
                pnl_vs_exit_r = round((markout_price - exit_price) / risk_distance, 4)
            else:
                pnl_vs_exit_r = round((exit_price - markout_price) / risk_distance, 4)

            # Verdict
            if pnl_vs_exit_r > 0.1:
                verdict_str = "LEFT_MONEY"
                left_money_count += 1
            elif pnl_vs_exit_r < -0.1:
                verdict_str = "DODGED_REVERSAL"
                dodged_reversal_count += 1
            else:
                verdict_str = "GOOD_EXIT"

            mv = MarkoutVerdict(
                minutes_after=minutes,
                price_at_markout=round(markout_price, 4),
                pnl_vs_exit_pct=pnl_vs_exit_pct,
                pnl_vs_exit_r=pnl_vs_exit_r,
                verdict=verdict_str,
            )
            verdicts.append(mv)

        attr.shadow_markouts = [v.to_dict() for v in verdicts]

        # Consensus
        if not verdicts:
            attr.markout_consensus = "NO_DATA"
        elif left_money_count > len(verdicts) / 2:
            attr.markout_consensus = "LEFT_MONEY"
        elif dodged_reversal_count > len(verdicts) / 2:
            attr.markout_consensus = "DODGED_REVERSAL"
        elif left_money_count == 0 and dodged_reversal_count == 0:
            attr.markout_consensus = "GOOD_EXIT"
        else:
            attr.markout_consensus = "MIXED"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _price_to_r(
        entry_price: float,
        price: float,
        direction: str,
        risk_distance: float,
    ) -> float:
        """Convert a price to R-multiple relative to entry."""
        if direction == "LONG":
            return round((price - entry_price) / risk_distance, 4)
        else:
            return round((entry_price - price) / risk_distance, 4)


# ---------------------------------------------------------------------------
# Convenience: compute attribution from an outcome dict
# ---------------------------------------------------------------------------
def compute_attribution_from_outcome(
    outcome: dict,
    price_series=None,
    post_exit_series=None,
) -> dict:
    """
    Convenience function: given an outcome dict (as stored in outcomes.jsonl),
    compute TradeAttribution and return it as a dict ready to merge.

    Usage:
        outcome = json.loads(line)
        attr_dict = compute_attribution_from_outcome(outcome, bars, post_bars)
        outcome["attribution"] = attr_dict
    """
    engine = TradeAttributionEngine()

    entry_time = None
    exit_time = None
    try:
        gen_at = outcome.get("generated_at", "")
        if gen_at:
            entry_time = datetime.fromisoformat(gen_at.replace("Z", "+00:00"))
    except Exception:
        entry_time = datetime.now(timezone.utc)

    try:
        closed_at = outcome.get("closed_at", "")
        if closed_at:
            exit_time = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
    except Exception:
        exit_time = datetime.now(timezone.utc)

    if entry_time is None:
        entry_time = datetime.now(timezone.utc)
    if exit_time is None:
        exit_time = datetime.now(timezone.utc)

    attr = engine.compute(
        entry_price=outcome.get("entry", 0.0),
        exit_price=outcome.get("exit_price", 0.0),
        stop_price=outcome.get("stop", 0.0),
        target_price=outcome.get("target1", 0.0),
        direction=outcome.get("direction", "LONG"),
        entry_time=entry_time,
        exit_time=exit_time,
        price_series=price_series,
        signal_id=outcome.get("signal_id", ""),
        post_exit_series=post_exit_series,
    )

    return attr.to_dict()
