"""
NZT-48 Learning Module 6: Correlation Discovery
Rolling 20-day correlation between all 12 tickers.
Alert on >0.3 correlation change.
Feed into Overseer sector concentration limits.
"""
from __future__ import annotations
import json
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config as cfg

logger = logging.getLogger("nzt48.learning.correlation")

BOT_B_TICKERS = cfg.get("bot_b_universe.tickers", [
    "NVDA", "TSLA", "MU", "SNDK", "AMD", "AVGO",
    "MRVL", "ARM", "TSM", "ASML", "SMCI", "VRT",
])


class CorrelationTracker:
    """Tracks rolling correlations between all ticker pairs."""

    def __init__(self, window: int = 20):
        self.window = window
        # ticker -> list of daily returns
        self._returns: dict[str, list[float]] = defaultdict(list)
        # Previous correlation matrix for change detection
        self._prev_corr: dict[tuple[str, str], float] = {}
        self._current_corr: dict[tuple[str, str], float] = {}
        self._alerts: list[dict] = []

    def update_returns(self, daily_returns: dict[str, float]) -> None:
        """Add daily returns for all tickers.

        Args:
            daily_returns: Dict of ticker -> daily return (fraction, e.g., 0.02 = 2%)
        """
        for ticker in BOT_B_TICKERS:
            ret = daily_returns.get(ticker, 0.0)
            self._returns[ticker].append(ret)
            # Trim to window
            if len(self._returns[ticker]) > self.window:
                self._returns[ticker].pop(0)

        # Recalculate correlations
        self._recalculate()

    def _recalculate(self) -> None:
        """Recalculate all pairwise correlations."""
        self._prev_corr = dict(self._current_corr)
        self._current_corr.clear()
        self._alerts.clear()

        for i, t1 in enumerate(BOT_B_TICKERS):
            for t2 in BOT_B_TICKERS[i + 1:]:
                r1 = self._returns.get(t1, [])
                r2 = self._returns.get(t2, [])

                if len(r1) < 5 or len(r2) < 5:
                    continue

                # Use min length
                n = min(len(r1), len(r2))
                x, y = r1[-n:], r2[-n:]

                corr = self._pearson(x, y)
                self._current_corr[(t1, t2)] = corr

                # Check for significant change
                prev = self._prev_corr.get((t1, t2))
                if prev is not None:
                    change = abs(corr - prev)
                    if change > 0.3:
                        alert = {
                            "pair": f"{t1}-{t2}",
                            "prev_corr": round(prev, 3),
                            "current_corr": round(corr, 3),
                            "change": round(change, 3),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                        self._alerts.append(alert)
                        logger.warning(
                            "CORRELATION ALERT: %s-%s changed %.3f → %.3f (Δ=%.3f)",
                            t1, t2, prev, corr, change,
                        )

    @staticmethod
    def _pearson(x: list[float], y: list[float]) -> float:
        """Calculate Pearson correlation coefficient."""
        n = len(x)
        if n < 2:
            return 0.0

        mean_x = sum(x) / n
        mean_y = sum(y) / n

        cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
        var_x = sum((xi - mean_x) ** 2 for xi in x)
        var_y = sum((yi - mean_y) ** 2 for yi in y)

        denom = (var_x * var_y) ** 0.5
        if denom == 0:
            return 0.0
        return cov / denom

    def get_high_correlations(self, threshold: float = 0.80) -> list[dict]:
        """Get all pairs with correlation above threshold."""
        results = []
        for (t1, t2), corr in self._current_corr.items():
            if abs(corr) >= threshold:
                results.append({
                    "pair": f"{t1}-{t2}",
                    "correlation": round(corr, 3),
                })
        return sorted(results, key=lambda r: abs(r["correlation"]), reverse=True)

    def get_alerts(self) -> list[dict]:
        """Get recent correlation change alerts."""
        return self._alerts

    def get_correlation_matrix(self) -> dict:
        """Get full correlation matrix for display."""
        matrix = {}
        for (t1, t2), corr in self._current_corr.items():
            matrix[f"{t1}-{t2}"] = round(corr, 3)
        return matrix

    def get_emerging_pairs(self) -> list[dict]:
        """Identify emerging pair trade relationships (high negative correlation)."""
        pairs = []
        for (t1, t2), corr in self._current_corr.items():
            if corr < -0.5:
                pairs.append({
                    "long_ticker": t1 if corr < 0 else t2,
                    "short_ticker": t2 if corr < 0 else t1,
                    "correlation": round(corr, 3),
                    "type": "negative_correlation_pair",
                })
        return pairs

    def save_state(self, conn: sqlite3.Connection) -> None:
        """Persist correlation tracker state to SQLite as a JSON blob."""
        conn.execute(
            """CREATE TABLE IF NOT EXISTS learning_state (
                module TEXT PRIMARY KEY,
                state_json TEXT,
                updated_at TEXT
            )"""
        )
        state = {
            "returns": dict(self._returns),
            "current_corr": {f"{k[0]}|{k[1]}": v for k, v in self._current_corr.items()},
            "prev_corr": {f"{k[0]}|{k[1]}": v for k, v in self._prev_corr.items()},
        }
        conn.execute(
            "INSERT OR REPLACE INTO learning_state (module, state_json, updated_at) VALUES (?, ?, ?)",
            ("correlation_tracker", json.dumps(state), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        logger.info("Correlation tracker state saved to DB")

    def load_state(self, conn: sqlite3.Connection) -> None:
        """Load correlation tracker state from SQLite."""
        try:
            row = conn.execute(
                "SELECT state_json FROM learning_state WHERE module = ?",
                ("correlation_tracker",),
            ).fetchone()
        except Exception:
            return
        if not row:
            return
        state = json.loads(row["state_json"] if isinstance(row, sqlite3.Row) else row[0])
        for ticker, returns in state.get("returns", {}).items():
            self._returns[ticker] = returns
        for key, corr in state.get("current_corr", {}).items():
            t1, t2 = key.split("|", 1)
            self._current_corr[(t1, t2)] = corr
        for key, corr in state.get("prev_corr", {}).items():
            t1, t2 = key.split("|", 1)
            self._prev_corr[(t1, t2)] = corr
        logger.info("Correlation tracker state loaded")
