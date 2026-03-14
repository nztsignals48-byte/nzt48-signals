"""
NZT-48 Learning Module 4: Pattern Recognition Feedback
All 12 detected patterns scored post-trade.
Rolling accuracy per pattern × regime × ticker.

<40% accuracy after 20 samples → -5 confidence penalty
>70% accuracy → +5 bonus
"""
from __future__ import annotations
import json
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.learning.patterns")

PATTERNS = [
    "coiled_spring", "volume_climax", "failed_breakout", "trend_acceleration",
    "momentum_exhaustion", "vwap_magnet", "gap_and_go", "gap_and_fade",
    "earnings_momentum", "dead_cat_bounce", "absorption", "abcd_pattern",
]


class PatternTracker:
    """Tracks accuracy of detected patterns and adjusts confidence."""

    def __init__(self):
        # pattern -> regime -> ticker -> [bool]  (True = pattern led to win)
        self._results: dict[str, dict[str, dict[str, list]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(list))
        )
        self._window = 50  # Rolling window

    def record_pattern_outcome(
        self, patterns: list[str], regime: str, ticker: str, was_win: bool,
    ) -> None:
        """Record outcome for each pattern detected on a trade."""
        for pattern in patterns:
            if pattern not in PATTERNS:
                continue
            results = self._results[pattern][regime][ticker]
            results.append(was_win)
            # Trim to window
            if len(results) > self._window:
                results.pop(0)

    def get_accuracy(self, pattern: str, regime: str = None, ticker: str = None) -> Optional[float]:
        """Get accuracy for a pattern, optionally filtered by regime/ticker."""
        results = []
        for r, tickers in self._results.get(pattern, {}).items():
            if regime and r != regime:
                continue
            for t, outcomes in tickers.items():
                if ticker and t != ticker:
                    continue
                results.extend(outcomes)

        if len(results) < 10:
            return None
        return sum(results) / len(results)

    def get_confidence_adjustment(self, patterns: list[str], regime: str, ticker: str = "") -> int:
        """Get aggregate confidence adjustment from detected patterns."""
        total_adj = 0
        for pattern in patterns:
            acc = self.get_accuracy(pattern, regime, ticker)
            if acc is None:
                continue
            if acc >= 0.70:
                total_adj += 5
            elif acc < 0.40:
                total_adj -= 5
        return max(-10, min(10, total_adj))  # Cap aggregate

    def get_pattern_leaderboard(self) -> list[dict]:
        """Get all patterns ranked by accuracy."""
        rows = []
        for pattern in PATTERNS:
            all_results = []
            for regime, tickers in self._results.get(pattern, {}).items():
                for ticker, outcomes in tickers.items():
                    all_results.extend(outcomes)

            if len(all_results) >= 10:
                accuracy = sum(all_results) / len(all_results) * 100
                rows.append({
                    "pattern": pattern,
                    "accuracy": round(accuracy, 1),
                    "samples": len(all_results),
                    "adjustment": 5 if accuracy >= 70 else (-5 if accuracy < 40 else 0),
                })

        return sorted(rows, key=lambda r: r["accuracy"], reverse=True)

    def save_state(self, conn: sqlite3.Connection) -> None:
        """Persist pattern tracker state to SQLite as a JSON blob."""
        conn.execute(
            """CREATE TABLE IF NOT EXISTS learning_state (
                module TEXT PRIMARY KEY,
                state_json TEXT,
                updated_at TEXT
            )"""
        )
        # Serialize nested defaultdicts to plain dicts
        state = {}
        for pattern, regimes in self._results.items():
            for regime, tickers in regimes.items():
                for ticker, outcomes in tickers.items():
                    key = f"{pattern}|{regime}|{ticker}"
                    state[key] = outcomes
        conn.execute(
            "INSERT OR REPLACE INTO learning_state (module, state_json, updated_at) VALUES (?, ?, ?)",
            ("pattern_tracker", json.dumps(state), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        logger.info("Pattern tracker state saved to DB")

    def load_state(self, conn: sqlite3.Connection) -> None:
        """Load pattern tracker state from SQLite."""
        try:
            row = conn.execute(
                "SELECT state_json FROM learning_state WHERE module = ?",
                ("pattern_tracker",),
            ).fetchone()
        except Exception:
            return
        if not row:
            return
        state = json.loads(row["state_json"] if isinstance(row, sqlite3.Row) else row[0])
        for key, outcomes in state.items():
            parts = key.split("|", 2)
            if len(parts) != 3:
                continue
            pattern, regime, ticker = parts
            self._results[pattern][regime][ticker] = outcomes
        logger.info("Pattern tracker state loaded: %d entries", len(state))
