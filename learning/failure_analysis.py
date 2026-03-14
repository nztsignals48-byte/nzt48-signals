"""
NZT-48 Learning Module 5: Failure Analysis
Every losing trade auto-categorised. Weekly failure report.

Categories:
- WRONG_DIRECTION: Signal long, stock went straight down
- BAD_TIMING: Right direction, stopped out first
- STOPPED_THEN_TARGET: Hit stop, then reached target
- REGIME_SHIFT: Regime changed after entry
- SPREAD_SLIPPAGE: Execution cost killed the trade
- NEWS_SHOCK: Unexpected news
- OVERSEER_FORCED: Portfolio risk forced close
"""
from __future__ import annotations
import json
import logging
import sqlite3
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone
from typing import Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.learning.failures")

FAILURE_CATEGORIES = [
    "WRONG_DIRECTION", "BAD_TIMING", "STOPPED_THEN_TARGET",
    "REGIME_SHIFT", "SPREAD_SLIPPAGE", "NEWS_SHOCK", "OVERSEER_FORCED",
]


class FailureAnalysis:
    """Auto-categorises losing trades and identifies systematic weaknesses."""

    def __init__(self):
        self._failures: list[dict] = []
        self._weekly_failures: list[dict] = []

    def record_failure(self, trade_data: dict) -> str:
        """Record and categorise a losing trade.

        trade_data should include: r_multiple, exit_reason, peak_r, trough_r,
        slippage, ticker, strategy, regime_at_entry, regime_at_exit, direction
        """
        category = trade_data.get("failure_category", "")
        if not category:
            category = self._auto_categorise(trade_data)

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ticker": trade_data.get("ticker", ""),
            "strategy": trade_data.get("strategy", ""),
            "direction": trade_data.get("direction", ""),
            "r_multiple": trade_data.get("r_multiple", 0),
            "category": category,
            "regime_entry": trade_data.get("regime_at_entry", ""),
            "regime_exit": trade_data.get("regime_at_exit", ""),
            "peak_r": trade_data.get("peak_r", 0),
            "trough_r": trade_data.get("trough_r", 0),
            "exit_reason": trade_data.get("exit_reason", ""),
        }

        self._failures.append(record)
        self._weekly_failures.append(record)

        logger.info(
            "FAILURE: %s %s %s R=%.2f → %s",
            record["ticker"], record["direction"], record["strategy"],
            record["r_multiple"], category,
        )

        return category

    def _auto_categorise(self, data: dict) -> str:
        """Auto-categorise based on trade data."""
        exit_reason = data.get("exit_reason", "")
        peak_r = data.get("peak_r", 0)
        trough_r = data.get("trough_r", 0)
        slippage = data.get("slippage", 0)
        r_multiple = data.get("r_multiple", 0)
        regime_entry = data.get("regime_at_entry", "")
        regime_exit = data.get("regime_at_exit", "")

        if exit_reason == "OVERSEER_FORCED":
            return "OVERSEER_FORCED"
        if exit_reason == "REGIME_FLIP" or (regime_entry != regime_exit and regime_exit):
            return "REGIME_SHIFT"
        if peak_r >= 1.0 and r_multiple < 0:
            return "STOPPED_THEN_TARGET"
        if slippage > 0 and abs(r_multiple) < 0.3:
            return "SPREAD_SLIPPAGE"
        if trough_r < -0.5 and peak_r < 0.2:
            return "WRONG_DIRECTION"
        return "BAD_TIMING"

    def get_weekly_report(self) -> dict:
        """Generate weekly failure analysis report."""
        if not self._weekly_failures:
            return {"total_losses": 0, "categories": {}, "recommendations": []}

        counts = Counter(f["category"] for f in self._weekly_failures)
        total = len(self._weekly_failures)

        # Find systematic issues
        recommendations = []
        if counts.get("WRONG_DIRECTION", 0) / max(total, 1) > 0.3:
            recommendations.append(
                "30%+ trades are WRONG_DIRECTION. Review regime classification accuracy. "
                "Consider raising confidence floor."
            )
        if counts.get("STOPPED_THEN_TARGET", 0) / max(total, 1) > 0.25:
            recommendations.append(
                "25%+ trades hit stop then reached target. Stops too tight. "
                "Consider widening ATR multiplier by 0.2×."
            )
        if counts.get("BAD_TIMING", 0) / max(total, 1) > 0.3:
            recommendations.append(
                "30%+ trades are BAD_TIMING. Entry too early. "
                "Consider waiting for confirmation bar or tightening time windows."
            )
        if counts.get("REGIME_SHIFT", 0) / max(total, 1) > 0.2:
            recommendations.append(
                "20%+ trades killed by regime shift. Hold durations may be too long. "
                "Consider reducing max hold time or adding regime exit triggers."
            )
        if counts.get("SPREAD_SLIPPAGE", 0) / max(total, 1) > 0.15:
            recommendations.append(
                "15%+ trades lost to slippage. Check bid-ask spreads. "
                "Consider raising minimum dollar volume threshold."
            )

        # Strategy breakdown
        strategy_failures = defaultdict(list)
        for f in self._weekly_failures:
            strategy_failures[f["strategy"]].append(f["category"])

        strategy_breakdown = {}
        for strat, cats in strategy_failures.items():
            strategy_breakdown[strat] = {
                "total_losses": len(cats),
                "top_failure": Counter(cats).most_common(1)[0] if cats else ("N/A", 0),
            }

        report = {
            "total_losses": total,
            "categories": dict(counts),
            "category_pct": {k: round(v / total * 100, 1) for k, v in counts.items()},
            "recommendations": recommendations,
            "strategy_breakdown": strategy_breakdown,
            "worst_loss_r": min(f["r_multiple"] for f in self._weekly_failures),
        }

        return report

    def reset_weekly(self):
        """Reset weekly failures for new period."""
        self._weekly_failures.clear()

    def get_all_failures(self, limit: int = 50) -> list[dict]:
        """Get recent failures."""
        return self._failures[-limit:]

    def save_state(self, conn: sqlite3.Connection) -> None:
        """Persist failure analysis state to SQLite as a JSON blob."""
        conn.execute(
            """CREATE TABLE IF NOT EXISTS learning_state (
                module TEXT PRIMARY KEY,
                state_json TEXT,
                updated_at TEXT
            )"""
        )
        state = {
            "failures": self._failures[-500:],  # Keep last 500
            "weekly_failures": self._weekly_failures,
        }
        conn.execute(
            "INSERT OR REPLACE INTO learning_state (module, state_json, updated_at) VALUES (?, ?, ?)",
            ("failure_analysis", json.dumps(state), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        logger.info("Failure analysis state saved to DB")

    def load_state(self, conn: sqlite3.Connection) -> None:
        """Load failure analysis state from SQLite."""
        try:
            row = conn.execute(
                "SELECT state_json FROM learning_state WHERE module = ?",
                ("failure_analysis",),
            ).fetchone()
        except Exception:
            return
        if not row:
            return
        state = json.loads(row["state_json"] if isinstance(row, sqlite3.Row) else row[0])
        self._failures = state.get("failures", [])
        self._weekly_failures = state.get("weekly_failures", [])
        logger.info("Failure analysis state loaded: %d failures", len(self._failures))
