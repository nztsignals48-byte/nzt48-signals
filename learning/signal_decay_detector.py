"""
Signal Decay Detector — Deflated Sharpe Ratio Monitoring
========================================================
Detects when trading signals degrade in effectiveness using Deflated Sharpe Ratio (DSR).

Section 49-52: Continuous monitoring of signal quality with automatic disabling of weak signals.
- DSR < 0.5: Signal quality degraded, reduce confidence weight
- 3+ signals decayed: Regime shift likely, recalibrate adaptive ladder
- Automatic 7-day disable if DSR stays < 0.5

References:
  - De Prado, M. L. (2018). Advances in Financial Machine Learning.
  - Deflated Sharpe Ratio: adjusts for multiple testing bias and estimation risk
"""

from __future__ import annotations

import json
import logging
import sqlite3
import math
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nzt48.learning.signal_decay_detector")

DB_PATH = Path(__file__).parent.parent / "data" / "nzt48.db"


@dataclass
class SignalDecayReport:
    """Report on signal decay status."""
    signal_name: str
    trades_count: int
    win_rate: float
    sharpe_ratio: float
    deflated_sharpe_ratio: float
    decay_detected: bool
    status: str  # ACTIVE, DEGRADED, DISABLED
    disabled_until: Optional[datetime] = None
    reason: str = ""


class SignalDecayDetector:
    """Monitors individual signal effectiveness and detects decay."""

    def __init__(self, db_path: str | Path = DB_PATH):
        self.db_path = Path(db_path)
        self._ensure_tables()

    def _ensure_tables(self):
        """Create signal monitoring tables."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS signal_decay_history (
                signal_name TEXT,
                date TEXT,
                trades_count INTEGER,
                win_rate REAL,
                sharpe_ratio REAL,
                deflated_sharpe_ratio REAL,
                decay_detected INTEGER,
                status TEXT,
                recorded_at TEXT,
                PRIMARY KEY (signal_name, date)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS signal_disabled_log (
                signal_name TEXT PRIMARY KEY,
                disabled_at TEXT NOT NULL,
                disabled_until TEXT NOT NULL,
                reason TEXT,
                re_enabled_at TEXT
            )
        """)

        conn.commit()
        conn.close()

    def detect_decay(self, lookback_days: int = 30) -> list[SignalDecayReport]:
        """Scan all signals for decay over past N days.

        Args:
            lookback_days: Number of days to analyze

        Returns:
            List of decay reports
        """
        logger.info(f"=== SIGNAL DECAY DETECTION (lookback={lookback_days}d) ===")

        reports = []
        try:
            # Get all unique signals from recent trades
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cutoff_date = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
            cursor.execute(
                """
                SELECT DISTINCT strategy FROM trades
                WHERE DATE(time_entered) > ?
                ORDER BY strategy
                """,
                (cutoff_date,),
            )
            signals = [row[0] for row in cursor.fetchall()]
            conn.close()

            logger.info(f"Analyzing {len(signals)} signals")

            for signal_name in signals:
                report = self._analyze_signal(signal_name, lookback_days)
                reports.append(report)

                # Check status and recommend actions
                if report.decay_detected:
                    logger.warning(
                        f"{signal_name}: DSR={report.deflated_sharpe_ratio:.2f} (DEGRADED)"
                    )

            # Check if 3+ signals decayed
            decayed_count = sum(1 for r in reports if r.decay_detected)
            if decayed_count >= 3:
                logger.error(f"!!! {decayed_count} signals decayed — REGIME SHIFT likely !!!")

        except Exception as exc:
            logger.error(f"Decay detection failed: {exc}", exc_info=True)

        return reports

    def _analyze_signal(self, signal_name: str, lookback_days: int) -> SignalDecayReport:
        """Analyze a single signal for decay."""
        cutoff_date = (
            datetime.now(timezone.utc) - timedelta(days=lookback_days)
        ).strftime("%Y-%m-%d")

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Fetch all trades for this signal in the window
        cursor.execute(
            """
            SELECT pnl_r_multiple, pnl_dollars
            FROM trades
            WHERE strategy = ? AND DATE(time_entered) > ?
            ORDER BY time_entered
            """,
            (signal_name, cutoff_date),
        )
        results = cursor.fetchall()
        conn.close()

        if not results:
            return SignalDecayReport(
                signal_name=signal_name,
                trades_count=0,
                win_rate=0,
                sharpe_ratio=0,
                deflated_sharpe_ratio=0,
                decay_detected=False,
                status="UNKNOWN",
                reason="No trades in lookback window",
            )

        rs = [r[0] for r in results]  # R multiples
        trades_count = len(rs)
        win_count = sum(1 for r in rs if r > 0)
        win_rate = win_count / trades_count if trades_count > 0 else 0

        # Compute Sharpe Ratio
        mean_r = sum(rs) / trades_count
        if trades_count > 1:
            variance = sum((r - mean_r) ** 2 for r in rs) / (trades_count - 1)
            std_r = math.sqrt(variance)
        else:
            std_r = 0

        sharpe_ratio = (mean_r / std_r) if std_r > 0 else 0

        # Compute Deflated Sharpe Ratio (DSR)
        # DSR = SR * sqrt(1 - (k-1)/(N-1))
        # where k = number of tested parameters (assume 5), N = trades
        k = 5  # Conservative estimate
        dsr = sharpe_ratio * math.sqrt(max(0, 1 - (k - 1) / (trades_count - 1))) if trades_count > k else sharpe_ratio * 0.5

        # Decay detection: DSR < 0.5
        decay_detected = dsr < 0.5

        # Check if already disabled
        is_disabled = self._is_signal_disabled(signal_name)

        if decay_detected:
            status = "DISABLED" if is_disabled else "DEGRADED"
            reason = f"DSR {dsr:.2f} < 0.5 (degraded)"

            # Auto-disable if not already
            if not is_disabled:
                self._disable_signal(signal_name, lookback_days)
        else:
            # Re-enable if was disabled
            if is_disabled:
                self._reenable_signal(signal_name)
            status = "ACTIVE"
            reason = "Signal healthy"

        return SignalDecayReport(
            signal_name=signal_name,
            trades_count=trades_count,
            win_rate=win_rate,
            sharpe_ratio=sharpe_ratio,
            deflated_sharpe_ratio=dsr,
            decay_detected=decay_detected,
            status=status,
            disabled_until=(
                datetime.now(timezone.utc) + timedelta(days=7)
                if decay_detected and not is_disabled
                else None
            ),
            reason=reason,
        )

    def _is_signal_disabled(self, signal_name: str) -> bool:
        """Check if a signal is currently disabled."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT disabled_until FROM signal_disabled_log
                WHERE signal_name = ? AND re_enabled_at IS NULL
                """,
                (signal_name,),
            )
            row = cursor.fetchone()
            conn.close()

            if row and row[0]:
                disabled_until = datetime.fromisoformat(row[0])
                return disabled_until > datetime.now(timezone.utc)
            return False
        except Exception as exc:
            logger.error(f"Failed to check disable status: {exc}")
            return False

    def _disable_signal(self, signal_name: str, days: int = 7):
        """Disable a signal for N days."""
        now = datetime.now(timezone.utc)
        disabled_until = now + timedelta(days=days)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO signal_disabled_log
            (signal_name, disabled_at, disabled_until, reason)
            VALUES (?, ?, ?, ?)
            """,
            (
                signal_name,
                now.isoformat(),
                disabled_until.isoformat(),
                "Auto-disabled: DSR < 0.5",
            ),
        )

        conn.commit()
        conn.close()
        logger.warning(f"Signal {signal_name} DISABLED until {disabled_until.isoformat()}")

    def _reenable_signal(self, signal_name: str):
        """Re-enable a previously disabled signal."""
        now = datetime.now(timezone.utc)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE signal_disabled_log
            SET re_enabled_at = ?
            WHERE signal_name = ?
            """,
            (now.isoformat(), signal_name),
        )

        conn.commit()
        conn.close()
        logger.info(f"Signal {signal_name} RE-ENABLED")

    def recommend_actions(self, reports: list[SignalDecayReport]) -> dict:
        """Generate recommendations from decay reports."""
        recommendations = {
            "decayed_signals": [],
            "regime_shift_likely": False,
            "actions": [],
        }

        decayed = [r for r in reports if r.decay_detected]
        recommendations["decayed_signals"] = [r.signal_name for r in decayed]

        if len(decayed) >= 3:
            recommendations["regime_shift_likely"] = True
            recommendations["actions"].append(
                f"REGIME SHIFT DETECTED: {len(decayed)} signals decayed. Recalibrate adaptive ladder immediately."
            )

        for report in decayed:
            recommendations["actions"].append(
                f"Reduce confidence weight for {report.signal_name} (DSR={report.deflated_sharpe_ratio:.2f})"
            )

        if len(decayed) == 0:
            recommendations["actions"].append("All signals healthy — no action required")

        return recommendations

    def save_decay_history(self, reports: list[SignalDecayReport]):
        """Persist decay analysis to history."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        for report in reports:
            cursor.execute(
                """
                INSERT OR REPLACE INTO signal_decay_history
                (signal_name, date, trades_count, win_rate, sharpe_ratio, deflated_sharpe_ratio, decay_detected, status, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.signal_name,
                    today,
                    report.trades_count,
                    report.win_rate,
                    report.sharpe_ratio,
                    report.deflated_sharpe_ratio,
                    1 if report.decay_detected else 0,
                    report.status,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

        conn.commit()
        conn.close()
        logger.info(f"Saved decay history for {len(reports)} signals")

    def get_disabled_signals(self) -> list[dict]:
        """Fetch all currently disabled signals."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM signal_disabled_log
                WHERE re_enabled_at IS NULL AND disabled_until > datetime('now')
                ORDER BY disabled_until
                """
            )
            rows = cursor.fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception as exc:
            logger.error(f"Failed to fetch disabled signals: {exc}")
            return []

    def detect_decay_hourly(self, lookback_hours: int = 4, min_trades_per_hour: int = 3) -> list[SignalDecayReport]:
        """
        EXPERIMENTAL: Hourly DSR check (disabled by default).

        WARNING: Hourly windows have VERY SMALL sample size (typically 1-3 trades/hour).
        DSR is unreliable on N<20. This is for research only, not production.

        Args:
            lookback_hours: How many hours to analyze
            min_trades_per_hour: Minimum trades per signal to calculate DSR

        Returns:
            List of decay reports (empty if insufficient data)
        """
        logger.warning(
            f"=== EXPERIMENTAL HOURLY DECAY CHECK (N_MIN={min_trades_per_hour}) ==="
        )
        logger.warning("WARNING: Sample size is too small for statistical reliability.")
        logger.warning("Use only for research. Production should use daily decay checking.")

        reports = []
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
            cursor.execute(
                """
                SELECT strategy, COUNT(*) as cnt
                FROM trades
                WHERE time_entered > ?
                GROUP BY strategy
                HAVING COUNT(*) >= ?
                """,
                (cutoff_time, min_trades_per_hour),
            )
            signals = cursor.fetchall()
            conn.close()

            if not signals:
                logger.info("No signals with sufficient hourly trade count")
                return reports

            for signal_name, trade_count in signals:
                if trade_count < min_trades_per_hour:
                    continue
                report = self._analyze_signal(signal_name, lookback_days=0)  # 0 = last 24h only
                reports.append(report)

        except Exception as exc:
            logger.error(f"Hourly decay detection failed: {exc}", exc_info=True)

        return reports
