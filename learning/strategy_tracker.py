"""
NZT-48 Learning Module 2: Strategy-Context Matrix
Performance matrix: each strategy × each regime state.
Win rate, avg R, expectancy per cell.

After 30+ trades per cell: auto-DISABLE strategies with expectancy <0.2R.
Auto-RE-ENABLE on probation (half size) if OOS expectancy recovers >0.5R.
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

logger = logging.getLogger("nzt48.learning.strategy_matrix")


class StrategyCell:
    """One cell in the strategy×regime matrix."""
    def __init__(self):
        self.trades: int = 0
        self.wins: int = 0
        self.total_r: float = 0.0
        self.win_rate: float = 0.0
        self.avg_r: float = 0.0
        self.expectancy: float = 0.0
        self.disabled: bool = False
        self.on_probation: bool = False
        self.probation_trades: int = 0
        self.oos_signals: list[float] = []  # Out-of-sample R-multiples


class StrategyContextMatrix:
    """Tracks strategy performance per regime and auto-disables underperformers."""

    DISABLE_THRESHOLD = 0.2    # Disable if expectancy < 0.2R after 30 trades
    PROBATION_THRESHOLD = 0.5  # Re-enable on probation if OOS > 0.5R
    MIN_TRADES_DISABLE = 30
    PROBATION_SIZE_MULT = 0.5  # Half position on probation
    PROBATION_LENGTH = 10      # 10 trades on probation

    def __init__(self):
        # matrix[strategy][regime] = StrategyCell
        self.matrix: dict[str, dict[str, StrategyCell]] = defaultdict(
            lambda: defaultdict(StrategyCell)
        )
        self._disabled_alerts: list[dict] = []
        self._enabled_alerts: list[dict] = []

    def record_trade(self, strategy: str, regime: str, r_multiple: float) -> Optional[dict]:
        """Record a trade and check for auto-disable/enable.
        Returns alert dict if state changed."""
        cell = self.matrix[strategy][regime]
        cell.trades += 1
        cell.total_r += r_multiple

        if r_multiple > 0:
            cell.wins += 1

        cell.win_rate = cell.wins / cell.trades if cell.trades > 0 else 0
        cell.avg_r = cell.total_r / cell.trades if cell.trades > 0 else 0

        # Expectancy = (WR × avg_win_R) - ((1-WR) × avg_loss_R)
        # Track running sums for proper computation
        if not hasattr(cell, '_total_win_r'):
            cell._total_win_r = 0.0
            cell._total_loss_r = 0.0
        if r_multiple > 0:
            cell._total_win_r += r_multiple
        else:
            cell._total_loss_r += abs(r_multiple)

        avg_win_r = cell._total_win_r / cell.wins if cell.wins > 0 else 0
        losses_count = cell.trades - cell.wins
        avg_loss_r = cell._total_loss_r / losses_count if losses_count > 0 else 0
        cell.expectancy = (cell.win_rate * avg_win_r) - ((1 - cell.win_rate) * avg_loss_r)

        # Handle probation
        if cell.on_probation:
            cell.probation_trades += 1
            if cell.probation_trades >= self.PROBATION_LENGTH:
                # Evaluate probation
                if cell.expectancy >= self.DISABLE_THRESHOLD:
                    cell.on_probation = False
                    cell.disabled = False
                    alert = {
                        "type": "STRATEGY_ENABLED",
                        "strategy": strategy,
                        "regime": regime,
                        "expectancy": round(cell.expectancy, 3),
                        "trades": cell.trades,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    self._enabled_alerts.append(alert)
                    logger.info("STRATEGY RE-ENABLED: %s in %s (exp=%.3fR)", strategy, regime, cell.expectancy)
                    return alert
                else:
                    cell.disabled = True
                    cell.on_probation = False
                    logger.info("PROBATION FAILED: %s in %s disabled again", strategy, regime)
            return None

        # Check for auto-disable
        if (cell.trades >= self.MIN_TRADES_DISABLE and
            cell.expectancy < self.DISABLE_THRESHOLD and
            not cell.disabled):
            cell.disabled = True
            alert = {
                "type": "STRATEGY_DISABLED",
                "strategy": strategy,
                "regime": regime,
                "expectancy": round(cell.expectancy, 3),
                "win_rate": round(cell.win_rate * 100, 1),
                "trades": cell.trades,
                "reason": f"Expectancy {cell.expectancy:.3f}R < {self.DISABLE_THRESHOLD}R after {cell.trades} trades",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._disabled_alerts.append(alert)
            logger.warning(
                "STRATEGY DISABLED: %s in %s — exp=%.3fR WR=%.0f%% (%d trades)",
                strategy, regime, cell.expectancy, cell.win_rate * 100, cell.trades,
            )
            return alert

        return None

    def record_oos_signal(self, strategy: str, regime: str, predicted_r: float) -> Optional[dict]:
        """Record an out-of-sample signal for disabled strategy.
        If OOS performance recovers, re-enable on probation."""
        cell = self.matrix.get(strategy, {}).get(regime)
        if not cell or not cell.disabled or cell.on_probation:
            return None

        cell.oos_signals.append(predicted_r)

        if len(cell.oos_signals) >= 15:
            oos_avg = sum(cell.oos_signals) / len(cell.oos_signals)
            if oos_avg >= self.PROBATION_THRESHOLD:
                cell.on_probation = True
                cell.probation_trades = 0
                cell.disabled = False
                cell.oos_signals.clear()
                alert = {
                    "type": "STRATEGY_PROBATION",
                    "strategy": strategy,
                    "regime": regime,
                    "oos_expectancy": round(oos_avg, 3),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                logger.info(
                    "STRATEGY ON PROBATION: %s in %s (OOS=%.3fR, half size)",
                    strategy, regime, oos_avg,
                )
                return alert
        return None

    def is_disabled(self, strategy: str, regime: str) -> bool:
        """Check if a strategy is disabled in this regime."""
        cell = self.matrix.get(strategy, {}).get(regime)
        return cell.disabled if cell else False

    def get_size_multiplier(self, strategy: str, regime: str) -> float:
        """Get position size multiplier (0.5 on probation, 1.0 normal)."""
        cell = self.matrix.get(strategy, {}).get(regime)
        if cell and cell.on_probation:
            return self.PROBATION_SIZE_MULT
        return 1.0

    def get_leaderboard(self) -> list[dict]:
        """Get strategy×regime leaderboard sorted by expectancy."""
        rows = []
        for strategy, regimes in self.matrix.items():
            for regime, cell in regimes.items():
                if cell.trades >= 5:
                    rows.append({
                        "strategy": strategy,
                        "regime": regime,
                        "trades": cell.trades,
                        "win_rate": round(cell.win_rate * 100, 1),
                        "avg_r": round(cell.avg_r, 3),
                        "expectancy": round(cell.expectancy, 3),
                        "disabled": cell.disabled,
                        "probation": cell.on_probation,
                    })
        return sorted(rows, key=lambda r: r["expectancy"], reverse=True)

    def get_alerts(self) -> list[dict]:
        """Get all disable/enable alerts."""
        return self._disabled_alerts + self._enabled_alerts

    def save_state(self, conn: sqlite3.Connection) -> None:
        """Persist strategy matrix state to SQLite as a JSON blob."""
        conn.execute(
            """CREATE TABLE IF NOT EXISTS learning_state (
                module TEXT PRIMARY KEY,
                state_json TEXT,
                updated_at TEXT
            )"""
        )
        state = {}
        for strategy, regimes in self.matrix.items():
            for regime, cell in regimes.items():
                key = f"{strategy}|{regime}"
                state[key] = {
                    "trades": cell.trades, "wins": cell.wins,
                    "total_r": cell.total_r, "win_rate": cell.win_rate,
                    "avg_r": cell.avg_r, "expectancy": cell.expectancy,
                    "disabled": cell.disabled, "on_probation": cell.on_probation,
                    "probation_trades": cell.probation_trades,
                    "oos_signals": cell.oos_signals,
                    "total_win_r": getattr(cell, '_total_win_r', 0.0),
                    "total_loss_r": getattr(cell, '_total_loss_r', 0.0),
                }
        conn.execute(
            "INSERT OR REPLACE INTO learning_state (module, state_json, updated_at) VALUES (?, ?, ?)",
            ("strategy_matrix", json.dumps(state), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        logger.info("Strategy matrix state saved to DB")

    def load_state(self, conn: sqlite3.Connection) -> None:
        """Load strategy matrix state from SQLite."""
        try:
            row = conn.execute(
                "SELECT state_json FROM learning_state WHERE module = ?",
                ("strategy_matrix",),
            ).fetchone()
        except Exception:
            return  # Table doesn't exist yet
        if not row:
            return
        state = json.loads(row["state_json"] if isinstance(row, sqlite3.Row) else row[0])
        for key, data in state.items():
            strategy, regime = key.split("|", 1)
            cell = self.matrix[strategy][regime]
            cell.trades = data["trades"]
            cell.wins = data["wins"]
            cell.total_r = data["total_r"]
            cell.win_rate = data["win_rate"]
            cell.avg_r = data["avg_r"]
            cell.expectancy = data["expectancy"]
            cell.disabled = data["disabled"]
            cell.on_probation = data["on_probation"]
            cell.probation_trades = data["probation_trades"]
            cell.oos_signals = data.get("oos_signals", [])
            cell._total_win_r = data.get("total_win_r", 0.0)
            cell._total_loss_r = data.get("total_loss_r", 0.0)
        logger.info("Strategy matrix state loaded: %d cells", len(state))
