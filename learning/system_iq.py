"""
NZT-48 Learning Module 10: System IQ Composite
The single number that proves the system is getting smarter.

system_iq = (
    overall_win_rate × 20 + profit_factor × 15 + avg_indicator_accuracy × 20 +
    strategy_regime_match_rate × 15 + pattern_accuracy × 10 +
    entry_quality_avg × 10 + exit_quality_avg × 10
) / 100

Plotted over time. MUST trend upward.
Daily Telegram: "NZT-48 System IQ: 67.3 (+0.4 from yesterday)"
"""
from __future__ import annotations
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.learning.system_iq")


class SystemIQ:
    """Composite intelligence score for NZT-48."""

    def __init__(self):
        self._history: list[dict] = []  # {date, iq, components}
        self._last_iq: float = 0.0
        self._last_components: dict = {}

    def calculate(
        self,
        win_rate: float,           # 0-1
        profit_factor: float,       # > 1 is good
        avg_indicator_accuracy: float,  # 0-100
        strategy_regime_match: float,   # 0-100
        pattern_accuracy: float,        # 0-100
        entry_quality_avg: float,       # 0-100
        exit_quality_avg: float,        # 0-100
    ) -> float:
        """Calculate System IQ composite score.

        Returns 0-100 score.
        """
        # Normalize inputs
        wr_score = min(win_rate * 100, 100)
        pf_score = min(profit_factor * 20, 100)  # PF of 5 = 100
        ind_score = min(avg_indicator_accuracy, 100)
        strat_score = min(strategy_regime_match, 100)
        pattern_score = min(pattern_accuracy, 100)
        entry_score = min(entry_quality_avg, 100)
        exit_score = min(exit_quality_avg, 100)

        # Weighted composite
        iq = (
            wr_score * 0.20 +
            pf_score * 0.15 +
            ind_score * 0.20 +
            strat_score * 0.15 +
            pattern_score * 0.10 +
            entry_score * 0.10 +
            exit_score * 0.10
        )

        self._last_components = {
            "win_rate": round(wr_score, 1),
            "profit_factor": round(pf_score, 1),
            "indicator_accuracy": round(ind_score, 1),
            "strategy_regime_match": round(strat_score, 1),
            "pattern_accuracy": round(pattern_score, 1),
            "entry_quality": round(entry_score, 1),
            "exit_quality": round(exit_score, 1),
        }
        self._last_iq = round(iq, 1)

        return self._last_iq

    def record_daily(self) -> dict:
        """Record daily IQ score and return trend data."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Get previous day's IQ
        prev_iq = self._history[-1]["iq"] if self._history else 0
        week_ago_iq = self._history[-7]["iq"] if len(self._history) >= 7 else 0

        record = {
            "date": today,
            "iq": self._last_iq,
            "components": dict(self._last_components),
            "change_from_yesterday": round(self._last_iq - prev_iq, 1),
            "change_from_week_ago": round(self._last_iq - week_ago_iq, 1),
        }

        self._history.append(record)

        # Determine learning velocity
        if record["change_from_week_ago"] > 1.0:
            velocity = "POSITIVE"
        elif record["change_from_week_ago"] > 0:
            velocity = "STABLE"
        elif record["change_from_week_ago"] > -1.0:
            velocity = "PLATEAU"
        else:
            velocity = "DECLINING"

        record["velocity"] = velocity

        logger.info(
            "SYSTEM IQ: %.1f (%+.1f from yesterday, %+.1f from last week). Velocity: %s",
            self._last_iq, record["change_from_yesterday"],
            record["change_from_week_ago"], velocity,
        )

        return record

    def get_telegram_message(self) -> str:
        """Generate daily System IQ Telegram message."""
        if not self._history:
            return "NZT-48 System IQ: Not yet calculated (need more trades)"

        latest = self._history[-1]
        return (
            f"NZT-48 System IQ: {latest['iq']:.1f} "
            f"({latest['change_from_yesterday']:+.1f} from yesterday, "
            f"{latest['change_from_week_ago']:+.1f} from last week). "
            f"Learning velocity: {latest.get('velocity', 'N/A')}."
        )

    def is_declining(self) -> bool:
        """Check if System IQ is in decline (alert condition)."""
        if len(self._history) < 7:
            return False
        return self._history[-1].get("velocity") == "DECLINING"

    def get_trend(self, days: int = 30) -> list[dict]:
        """Get IQ trend for plotting."""
        return self._history[-days:]

    def get_current(self) -> dict:
        """Get current IQ and components."""
        return {
            "iq": self._last_iq,
            "components": self._last_components,
            "history_length": len(self._history),
        }

    def save_state(self, conn: sqlite3.Connection) -> None:
        """Persist System IQ history to SQLite as a JSON blob."""
        conn.execute(
            """CREATE TABLE IF NOT EXISTS learning_state (
                module TEXT PRIMARY KEY,
                state_json TEXT,
                updated_at TEXT
            )"""
        )
        state = {
            "history": self._history[-365:],  # Keep last year
            "last_iq": self._last_iq,
            "last_components": self._last_components,
        }
        conn.execute(
            "INSERT OR REPLACE INTO learning_state (module, state_json, updated_at) VALUES (?, ?, ?)",
            ("system_iq", json.dumps(state), datetime.now(timezone.utc).isoformat()),
        )
        # Also update system_iq in equity_snapshots if today's record exists
        if self._history:
            latest = self._history[-1]
            conn.execute(
                "UPDATE equity_snapshots SET system_iq = ? WHERE date = ?",
                (latest["iq"], latest["date"]),
            )
        conn.commit()
        logger.info("System IQ state saved to DB")

    def load_state(self, conn: sqlite3.Connection) -> None:
        """Load System IQ history from SQLite."""
        try:
            row = conn.execute(
                "SELECT state_json FROM learning_state WHERE module = ?",
                ("system_iq",),
            ).fetchone()
        except Exception:
            return
        if not row:
            return
        state = json.loads(row["state_json"] if isinstance(row, sqlite3.Row) else row[0])
        self._history = state.get("history", [])
        self._last_iq = state.get("last_iq", 0.0)
        self._last_components = state.get("last_components", {})
        logger.info("System IQ state loaded: %d history entries", len(self._history))
