"""
NZT-48 Learning Module 7: Decay Detector
Rolling 20-trade expectancy per strategy and ticker.
If expectancy drops below 0.0R for 20 consecutive trades → AUTO-HALT.
Generate OOS signals for monitoring.
Auto-resume if OOS > 0.5R for 15+ signals.
"""
from __future__ import annotations
import json
import logging
import sqlite3
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.learning.decay")


class DecayDetector:
    """Detects dying edges and auto-halts before they bleed capital."""

    WINDOW = 20
    HALT_THRESHOLD = 0.0      # Halt if expectancy < 0
    RESUME_THRESHOLD = 0.5    # Resume if OOS > 0.5R
    OOS_MIN_SIGNALS = 15

    def __init__(self):
        _w = self.WINDOW
        # strategy -> deque of R-multiples (rolling window)
        self._strategy_window: dict[str, deque] = defaultdict(lambda: deque(maxlen=_w))
        # ticker -> deque of R-multiples
        self._ticker_window: dict[str, deque] = defaultdict(lambda: deque(maxlen=_w))
        # Halted strategies/tickers
        self.halted_strategies: set[str] = set()
        self.halted_tickers: set[str] = set()
        # OOS tracking for halted items
        self._oos_strategy: dict[str, list[float]] = defaultdict(list)
        self._oos_ticker: dict[str, list[float]] = defaultdict(list)
        self._alerts: list[dict] = []

    def record_trade(self, strategy: str, ticker: str, r_multiple: float) -> list[dict]:
        """Record a trade and check for decay."""
        alerts = []

        # Strategy check
        self._strategy_window[strategy].append(r_multiple)
        if len(self._strategy_window[strategy]) >= self.WINDOW:
            avg_r = sum(self._strategy_window[strategy]) / len(self._strategy_window[strategy])
            if avg_r < self.HALT_THRESHOLD and strategy not in self.halted_strategies:
                self.halted_strategies.add(strategy)
                alert = {
                    "type": "STRATEGY_DECAY_HALT",
                    "strategy": strategy,
                    "avg_r_20": round(avg_r, 3),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "message": f"Strategy {strategy} halted: 20-trade avg R = {avg_r:.3f}",
                }
                alerts.append(alert)
                self._alerts.append(alert)
                logger.warning("DECAY HALT: %s avg_R=%.3f", strategy, avg_r)

        # Ticker check
        self._ticker_window[ticker].append(r_multiple)
        if len(self._ticker_window[ticker]) >= self.WINDOW:
            avg_r = sum(self._ticker_window[ticker]) / len(self._ticker_window[ticker])
            if avg_r < self.HALT_THRESHOLD and ticker not in self.halted_tickers:
                self.halted_tickers.add(ticker)
                alert = {
                    "type": "TICKER_DECAY_HALT",
                    "ticker": ticker,
                    "avg_r_20": round(avg_r, 3),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "message": f"Ticker {ticker} halted: 20-trade avg R = {avg_r:.3f}",
                }
                alerts.append(alert)
                self._alerts.append(alert)
                logger.warning("DECAY HALT TICKER: %s avg_R=%.3f", ticker, avg_r)

        return alerts

    def record_oos_signal(self, strategy: str = None, ticker: str = None, predicted_r: float = 0) -> Optional[dict]:
        """Record out-of-sample signal for halted strategy/ticker."""
        if strategy and strategy in self.halted_strategies:
            self._oos_strategy[strategy].append(predicted_r)
            if len(self._oos_strategy[strategy]) >= self.OOS_MIN_SIGNALS:
                avg = sum(self._oos_strategy[strategy]) / len(self._oos_strategy[strategy])
                if avg >= self.RESUME_THRESHOLD:
                    self.halted_strategies.discard(strategy)
                    self._oos_strategy[strategy].clear()
                    alert = {
                        "type": "STRATEGY_DECAY_RESUME",
                        "strategy": strategy,
                        "oos_avg_r": round(avg, 3),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    self._alerts.append(alert)
                    logger.info("DECAY RESUME: %s OOS_avg=%.3f", strategy, avg)
                    return alert

        if ticker and ticker in self.halted_tickers:
            self._oos_ticker[ticker].append(predicted_r)
            if len(self._oos_ticker[ticker]) >= self.OOS_MIN_SIGNALS:
                avg = sum(self._oos_ticker[ticker]) / len(self._oos_ticker[ticker])
                if avg >= self.RESUME_THRESHOLD:
                    self.halted_tickers.discard(ticker)
                    self._oos_ticker[ticker].clear()
                    alert = {
                        "type": "TICKER_DECAY_RESUME",
                        "ticker": ticker,
                        "oos_avg_r": round(avg, 3),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    self._alerts.append(alert)
                    return alert
        return None

    def is_halted(self, strategy: str = None, ticker: str = None) -> bool:
        """Check if strategy or ticker is halted due to decay."""
        if strategy and strategy in self.halted_strategies:
            return True
        if ticker and ticker in self.halted_tickers:
            return True
        return False

    def get_status(self) -> dict:
        """Get decay detector status."""
        return {
            "halted_strategies": list(self.halted_strategies),
            "halted_tickers": list(self.halted_tickers),
            "strategy_windows": {
                s: {"avg_r": round(sum(w) / len(w), 3) if w else 0, "trades": len(w)}
                for s, w in self._strategy_window.items()
            },
            "alerts": self._alerts[-10:],
        }

    def save_state(self, conn: sqlite3.Connection) -> None:
        """Persist decay detector state to SQLite as a JSON blob."""
        conn.execute(
            """CREATE TABLE IF NOT EXISTS learning_state (
                module TEXT PRIMARY KEY,
                state_json TEXT,
                updated_at TEXT
            )"""
        )
        state = {
            "strategy_windows": {s: list(w) for s, w in self._strategy_window.items()},
            "ticker_windows": {t: list(w) for t, w in self._ticker_window.items()},
            "halted_strategies": list(self.halted_strategies),
            "halted_tickers": list(self.halted_tickers),
            "oos_strategy": dict(self._oos_strategy),
            "oos_ticker": dict(self._oos_ticker),
            "alerts": self._alerts[-50:],
        }
        conn.execute(
            "INSERT OR REPLACE INTO learning_state (module, state_json, updated_at) VALUES (?, ?, ?)",
            ("decay_detector", json.dumps(state), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        logger.info("Decay detector state saved to DB")

    def load_state(self, conn: sqlite3.Connection) -> None:
        """Load decay detector state from SQLite."""
        try:
            row = conn.execute(
                "SELECT state_json FROM learning_state WHERE module = ?",
                ("decay_detector",),
            ).fetchone()
        except Exception:
            return
        if not row:
            return
        state = json.loads(row["state_json"] if isinstance(row, sqlite3.Row) else row[0])
        for s, vals in state.get("strategy_windows", {}).items():
            self._strategy_window[s] = deque(vals, maxlen=self.WINDOW)
        for t, vals in state.get("ticker_windows", {}).items():
            self._ticker_window[t] = deque(vals, maxlen=self.WINDOW)
        self.halted_strategies = set(state.get("halted_strategies", []))
        self.halted_tickers = set(state.get("halted_tickers", []))
        for s, vals in state.get("oos_strategy", {}).items():
            self._oos_strategy[s] = vals
        for t, vals in state.get("oos_ticker", {}).items():
            self._oos_ticker[t] = vals
        self._alerts = state.get("alerts", [])
        logger.info("Decay detector state loaded")
