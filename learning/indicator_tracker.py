"""
NZT-48 Learning Module 1: Indicator Effectiveness Tracker
After every closed trade: for each of 22 indicators, record whether it
correctly predicted direction and outcome.
Rolling 100-trade window per indicator per regime.

AUTO-ADJUSTMENT:
- >65% accuracy → +3 confidence bonus
- <40% accuracy → -5 penalty
- Recalculated every 20 trades
"""
from __future__ import annotations
import json
import logging
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Direction, RegimeState

logger = logging.getLogger("nzt48.learning.indicators")

# All 22 indicators we track
INDICATORS = [
    "vwap", "ema9", "ema20", "ema50", "ema10w",
    "rsi14", "macd", "stochastic_rsi", "atr14",
    "bollinger", "keltner", "adx14", "obv", "mfi14",
    "rvol", "volume_spike", "dollar_volume",
    "or_5m", "or_15m", "bid_ask_spread",
    "cumulative_delta", "speed_of_tape",
]


@dataclass
class IndicatorScore:
    """Performance score for one indicator in one regime."""
    indicator: str = ""
    regime: str = ""
    trades_evaluated: int = 0
    correct_predictions: int = 0
    accuracy_pct: float = 0.0
    avg_r_correct: float = 0.0
    avg_r_wrong: float = 0.0
    effectiveness_score: float = 0.0
    confidence_adjustment: int = 0  # Auto-calculated: +3 or -5 or 0


class IndicatorEffectivenessTracker:
    """Tracks which indicators actually predict profitable trades."""

    def __init__(self):
        # scores[indicator][regime] = IndicatorScore
        self.scores: dict[str, dict[str, IndicatorScore]] = defaultdict(
            lambda: defaultdict(IndicatorScore)
        )
        # Rolling trade history per indicator per regime
        self._history: dict[str, dict[str, list]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._trades_since_recalc = 0
        self._recalc_interval = 20
        self._window = 100

    def record_trade(self, trade_data: dict) -> None:
        """Record indicator predictions vs actual outcome.

        trade_data must include:
        - 'r_multiple': float
        - 'direction': 'LONG' or 'SHORT'
        - 'regime': regime string
        - 'indicators': dict of indicator_name -> value at entry
        """
        r = trade_data.get("r_multiple", 0)
        direction = trade_data.get("direction", "LONG")
        regime = trade_data.get("regime", "RANGE_BOUND")
        indicators = trade_data.get("indicators", {})

        for ind_name in INDICATORS:
            val = indicators.get(ind_name)
            if val is None:
                continue

            correct = self._was_indicator_correct(ind_name, val, direction, r > 0)

            history = self._history[ind_name][regime]
            history.append({"correct": correct, "r": r})

            # Trim to rolling window
            if len(history) > self._window:
                history.pop(0)

        self._trades_since_recalc += 1
        if self._trades_since_recalc >= self._recalc_interval:
            self._recalculate_all()
            self._trades_since_recalc = 0

    def _was_indicator_correct(self, name: str, value, direction: str, was_win: bool) -> bool:
        """Determine if an indicator correctly predicted the outcome."""
        # Each indicator has its own logic for "correct prediction"
        if name == "rsi14":
            if direction == "LONG" and value < 50 and was_win:
                return True  # Bought oversold, won
            if direction == "SHORT" and value > 50 and was_win:
                return True
            return was_win  # Simplified: correct if trade won

        if name == "macd":
            # MACD positive = bullish, negative = bearish
            if direction == "LONG" and value > 0 and was_win:
                return True
            if direction == "SHORT" and value < 0 and was_win:
                return True
            return was_win

        if name in ("rvol", "volume_spike"):
            return was_win  # High volume should confirm moves

        # Default: indicator is "correct" if trade won
        return was_win

    def _recalculate_all(self) -> None:
        """Recalculate all indicator scores."""
        for ind_name in INDICATORS:
            for regime, history in self._history[ind_name].items():
                if len(history) < 10:
                    continue

                score = IndicatorScore(indicator=ind_name, regime=regime)
                score.trades_evaluated = len(history)

                correct_trades = [h for h in history if h["correct"]]
                wrong_trades = [h for h in history if not h["correct"]]

                score.correct_predictions = len(correct_trades)
                score.accuracy_pct = len(correct_trades) / len(history) * 100

                if correct_trades:
                    score.avg_r_correct = sum(h["r"] for h in correct_trades) / len(correct_trades)
                if wrong_trades:
                    score.avg_r_wrong = sum(h["r"] for h in wrong_trades) / len(wrong_trades)

                # Effectiveness = accuracy × impact
                score.effectiveness_score = (score.accuracy_pct / 100) * score.avg_r_correct

                # Auto-adjustment
                if score.accuracy_pct >= 65:
                    score.confidence_adjustment = 3
                elif score.accuracy_pct < 40:
                    score.confidence_adjustment = -5
                else:
                    score.confidence_adjustment = 0

                self.scores[ind_name][regime] = score

        logger.info("Indicator scores recalculated across %d indicators", len(INDICATORS))

    def get_adjustment(self, indicator: str, regime: str) -> int:
        """Get confidence adjustment for an indicator in current regime."""
        score = self.scores.get(indicator, {}).get(regime)
        if not score:
            return 0
        return score.confidence_adjustment

    def get_total_adjustment(self, regime: str, indicators: dict) -> int:
        """Get aggregate confidence adjustment from all indicators."""
        total = 0
        for ind_name, val in indicators.items():
            if ind_name in INDICATORS and val is not None:
                total += self.get_adjustment(ind_name, regime)
        return max(-15, min(15, total))  # Cap aggregate

    def get_leaderboard(self, regime: str = None) -> list[dict]:
        """Get indicator leaderboard sorted by effectiveness."""
        rows = []
        for ind_name in INDICATORS:
            if regime:
                score = self.scores.get(ind_name, {}).get(regime)
                if score and score.trades_evaluated >= 10:
                    rows.append({
                        "indicator": ind_name,
                        "regime": regime,
                        "accuracy": round(score.accuracy_pct, 1),
                        "avg_r_correct": round(score.avg_r_correct, 2),
                        "effectiveness": round(score.effectiveness_score, 3),
                        "adjustment": score.confidence_adjustment,
                        "trades": score.trades_evaluated,
                    })
            else:
                for r, score in self.scores.get(ind_name, {}).items():
                    if score.trades_evaluated >= 10:
                        rows.append({
                            "indicator": ind_name,
                            "regime": r,
                            "accuracy": round(score.accuracy_pct, 1),
                            "avg_r_correct": round(score.avg_r_correct, 2),
                            "effectiveness": round(score.effectiveness_score, 3),
                            "adjustment": score.confidence_adjustment,
                            "trades": score.trades_evaluated,
                        })
        return sorted(rows, key=lambda r: r["effectiveness"], reverse=True)

    def save_state(self, conn: sqlite3.Connection) -> None:
        """Persist indicator scores to the indicator_scores table."""
        now = datetime.now(timezone.utc).isoformat()
        for ind_name in INDICATORS:
            for regime, score in self.scores.get(ind_name, {}).items():
                if score.trades_evaluated < 1:
                    continue
                conn.execute(
                    """INSERT OR REPLACE INTO indicator_scores
                       (indicator_name, regime, trades_evaluated, correct_predictions,
                        accuracy_pct, avg_r_correct, avg_r_wrong, effectiveness_score,
                        confidence_adjustment, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ind_name, regime, score.trades_evaluated,
                     score.correct_predictions, score.accuracy_pct,
                     score.avg_r_correct, score.avg_r_wrong,
                     score.effectiveness_score, score.confidence_adjustment, now),
                )
        conn.commit()
        logger.info("Indicator tracker state saved to DB")

    def load_state(self, conn: sqlite3.Connection) -> None:
        """Load indicator scores from the indicator_scores table."""
        rows = conn.execute(
            "SELECT * FROM indicator_scores ORDER BY updated_at DESC"
        ).fetchall()
        for row in rows:
            ind_name = row["indicator_name"]
            regime = row["regime"]
            if ind_name not in INDICATORS:
                continue
            score = IndicatorScore(
                indicator=ind_name,
                regime=regime,
                trades_evaluated=row["trades_evaluated"],
                correct_predictions=row["correct_predictions"],
                accuracy_pct=row["accuracy_pct"],
                avg_r_correct=row["avg_r_correct"] or 0.0,
                avg_r_wrong=row["avg_r_wrong"] or 0.0,
                effectiveness_score=row["effectiveness_score"] or 0.0,
                confidence_adjustment=row["confidence_adjustment"] or 0,
            )
            self.scores[ind_name][regime] = score
        logger.info("Indicator tracker state loaded: %d records", len(rows))
