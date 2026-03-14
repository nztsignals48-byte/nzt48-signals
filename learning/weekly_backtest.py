"""
Weekly Backtest Engine — Model-Reality Gap Detection
===================================================
Every Sunday 23:00 UTC: backtests past week's data and compares vs actual paper trades.

If model backtest vs actual execution differ by >5%:
  - Investigate execution quality issues
  - Check for regime shifts
  - Verify data integrity

Section 49-52: Continuous validation of strategy performance with weekly reconciliation.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nzt48.learning.weekly_backtest")

DB_PATH = Path(__file__).parent.parent / "data" / "nzt48.db"


@dataclass
class BacktestMetrics:
    """Results from a single week's backtest."""
    week_start: str
    week_end: str
    trades_count: int
    model_win_rate: float
    model_avg_r: float
    model_total_r: float
    model_daily_return: float
    actual_win_rate: float
    actual_avg_r: float
    actual_total_r: float
    actual_daily_return: float
    model_reality_gap: float  # Absolute % difference in daily return
    gap_status: str  # OK, WARNING, CRITICAL


class WeeklyBacktester:
    """Runs weekly backtests and compares against paper execution."""

    def __init__(self, db_path: str | Path = DB_PATH):
        self.db_path = Path(db_path)
        self._ensure_tables()

    def _ensure_tables(self):
        """Create backtest result tables."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS weekly_backtest_results (
                week_start TEXT PRIMARY KEY,
                week_end TEXT,
                trades_count INTEGER,
                model_win_rate REAL,
                model_avg_r REAL,
                model_total_r REAL,
                model_daily_return REAL,
                actual_win_rate REAL,
                actual_avg_r REAL,
                actual_total_r REAL,
                actual_daily_return REAL,
                model_reality_gap REAL,
                gap_status TEXT,
                backtest_date TEXT,
                notes TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS weekly_performance_reports (
                week_start TEXT PRIMARY KEY,
                what_worked TEXT,
                what_degraded TEXT,
                vol_forecast TEXT,
                regime_predicted TEXT,
                generated_at TEXT
            )
        """)

        conn.commit()
        conn.close()

    def run_weekly_backtest(self, week_offset: int = -1) -> BacktestMetrics:
        """Run backtest for past week (or offset weeks).

        Args:
            week_offset: -1 = last week, 0 = this week, etc.

        Returns:
            BacktestMetrics comparing model vs actual
        """
        logger.info(f"=== WEEKLY BACKTEST (offset={week_offset}) ===")

        # Calculate week boundaries
        today = datetime.now(timezone.utc)
        days_since_monday = today.weekday()
        week_start = (today - timedelta(days=days_since_monday + 7 * abs(week_offset))).strftime("%Y-%m-%d")
        week_end = (datetime.strptime(week_start, "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d")

        logger.info(f"Analyzing week: {week_start} to {week_end}")

        try:
            # 1. Fetch actual paper trades for the week
            actual = self._get_actual_metrics(week_start, week_end)
            logger.info(
                f"Actual: {actual['trades']} trades, WR={actual['win_rate']:.1%}, Avg R={actual['avg_r']:.2f}"
            )

            # 2. Run model backtest on same period
            model = self._run_model_backtest(week_start, week_end)
            logger.info(
                f"Model:  {model['trades']} trades, WR={model['win_rate']:.1%}, Avg R={model['avg_r']:.2f}"
            )

            # 3. Compute gap and status
            gap = abs(model["daily_return"] - actual["daily_return"])
            if gap > 0.05:  # 5% absolute difference
                gap_status = "CRITICAL" if gap > 0.10 else "WARNING"
                logger.warning(
                    f"!!! Model-Reality Gap: {gap:.2%} — {gap_status} !!!"
                )
            else:
                gap_status = "OK"

            # 4. Create metrics object
            metrics = BacktestMetrics(
                week_start=week_start,
                week_end=week_end,
                trades_count=actual["trades"],
                model_win_rate=model["win_rate"],
                model_avg_r=model["avg_r"],
                model_total_r=model["total_r"],
                model_daily_return=model["daily_return"],
                actual_win_rate=actual["win_rate"],
                actual_avg_r=actual["avg_r"],
                actual_total_r=actual["total_r"],
                actual_daily_return=actual["daily_return"],
                model_reality_gap=gap,
                gap_status=gap_status,
            )

            # 5. Investigate if gap > 5%
            if gap > 0.05:
                self._investigate_gap(week_start, week_end, metrics)

            # 6. Save results
            self._save_backtest_results(metrics)

            return metrics

        except Exception as exc:
            logger.error(f"Weekly backtest failed: {exc}", exc_info=True)
            return BacktestMetrics(
                week_start=week_start,
                week_end=week_end,
                trades_count=0,
                model_win_rate=0,
                model_avg_r=0,
                model_total_r=0,
                model_daily_return=0,
                actual_win_rate=0,
                actual_avg_r=0,
                actual_total_r=0,
                actual_daily_return=0,
                model_reality_gap=0,
                gap_status="ERROR",
            )

    def _get_actual_metrics(self, week_start: str, week_end: str) -> dict:
        """Get actual paper trading metrics for a date range."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT pnl_r_multiple, pnl_dollars
                FROM trades
                WHERE DATE(time_entered) BETWEEN ? AND ?
                """,
                (week_start, week_end),
            )
            results = cursor.fetchall()
            conn.close()

            if not results:
                return {
                    "trades": 0,
                    "win_rate": 0,
                    "avg_r": 0,
                    "total_r": 0,
                    "daily_return": 0,
                }

            rs = [r[0] for r in results]
            trades = len(rs)
            wins = sum(1 for r in rs if r > 0)
            win_rate = wins / trades if trades > 0 else 0
            avg_r = sum(rs) / trades if trades > 0 else 0
            total_r = sum(rs)

            # Daily return (assuming 5 trading days per week, $10k starting equity)
            daily_return = total_r / 5 / 100 if total_r > 0 else 0

            return {
                "trades": trades,
                "win_rate": win_rate,
                "avg_r": avg_r,
                "total_r": total_r,
                "daily_return": daily_return,
            }
        except Exception as exc:
            logger.error(f"Failed to get actual metrics: {exc}")
            return {
                "trades": 0,
                "win_rate": 0,
                "avg_r": 0,
                "total_r": 0,
                "daily_return": 0,
            }

    def _run_model_backtest(self, week_start: str, week_end: str) -> dict:
        """Run strategy backtest on past week's data.

        For now: simplified mock. In production, would:
          - Load OHLCV data for week
          - Run S15 strategy logic on daily bars
          - Compute hypothetical trades
        """
        logger.info(f"Running model backtest for {week_start}..{week_end}")

        # TODO: Integrate with actual backtest runner
        # For now, return realistic mock data
        return {
            "trades": 5,
            "win_rate": 0.60,
            "avg_r": 0.75,
            "total_r": 2.25,
            "daily_return": 0.0045,  # 0.45% daily
        }

    def _investigate_gap(
        self,
        week_start: str,
        week_end: str,
        metrics: BacktestMetrics,
    ):
        """Investigate why model and reality diverged by >5%."""
        logger.warning(f"Investigating {metrics.model_reality_gap:.2%} model-reality gap...")

        checks = []

        # 1. Check execution quality
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT AVG(fill_quality), AVG(entry_quality), AVG(exit_quality)
            FROM trades
            WHERE DATE(time_entered) BETWEEN ? AND ?
            """,
            (week_start, week_end),
        )
        row = cursor.fetchone()
        if row and row[0] is not None:
            fill_quality = row[0]
            if fill_quality < 0.90:
                checks.append(f"Poor fill quality: {fill_quality:.1%} (expect >90%)")

        # 2. Check regime consistency
        cursor.execute(
            """
            SELECT COUNT(DISTINCT regime_state), COUNT(*)
            FROM trades
            WHERE DATE(time_entered) BETWEEN ? AND ?
            """,
            (week_start, week_end),
        )
        row = cursor.fetchone()
        if row and row[0] > 3:
            checks.append(f"Regime flapping: {row[0]} different regimes in {row[1]} trades")

        # 3. Check for data gaps
        cursor.execute(
            """
            SELECT COUNT(*) FROM signals
            WHERE DATE(timestamp) BETWEEN ? AND ? AND status = 'SKIPPED'
            """,
            (week_start, week_end),
        )
        skipped = cursor.fetchone()[0] if cursor.fetchone() else 0
        if skipped > 10:
            checks.append(f"High signal rejection: {skipped} signals skipped")

        conn.close()

        if checks:
            logger.warning("Investigation results:")
            for check in checks:
                logger.warning(f"  - {check}")
        else:
            logger.warning("No obvious issues found — check model assumptions")

    def _save_backtest_results(self, metrics: BacktestMetrics):
        """Persist backtest results."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO weekly_backtest_results
            (week_start, week_end, trades_count, model_win_rate, model_avg_r, model_total_r, model_daily_return,
             actual_win_rate, actual_avg_r, actual_total_r, actual_daily_return, model_reality_gap, gap_status, backtest_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metrics.week_start,
                metrics.week_end,
                metrics.trades_count,
                metrics.model_win_rate,
                metrics.model_avg_r,
                metrics.model_total_r,
                metrics.model_daily_return,
                metrics.actual_win_rate,
                metrics.actual_avg_r,
                metrics.actual_total_r,
                metrics.actual_daily_return,
                metrics.model_reality_gap,
                metrics.gap_status,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

        conn.commit()
        conn.close()
        logger.info(f"Saved weekly backtest results for {metrics.week_start}")

    def generate_weekly_report(self, week_start: str) -> dict:
        """Generate comprehensive weekly performance report."""
        logger.info(f"Generating weekly report for {week_start}")

        report = {
            "week_start": week_start,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "what_worked": [],
            "what_degraded": [],
            "vol_forecast": "",
            "regime_predicted": "",
        }

        try:
            # Fetch backtest results
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                "SELECT * FROM weekly_backtest_results WHERE week_start = ?",
                (week_start,),
            )
            row = cursor.fetchone()

            if not row:
                report["status"] = "NO_BACKTEST"
                return report

            metrics = BacktestMetrics(
                week_start=row[0],
                week_end=row[1],
                trades_count=row[2],
                model_win_rate=row[3],
                model_avg_r=row[4],
                model_total_r=row[5],
                model_daily_return=row[6],
                actual_win_rate=row[7],
                actual_avg_r=row[8],
                actual_total_r=row[9],
                actual_daily_return=row[10],
                model_reality_gap=row[11],
                gap_status=row[12],
            )

            # What worked?
            if metrics.actual_win_rate > 0.55:
                report["what_worked"].append(
                    f"High win rate: {metrics.actual_win_rate:.1%}"
                )
            if metrics.actual_avg_r > 0.5:
                report["what_worked"].append(
                    f"Strong avg R: {metrics.actual_avg_r:.2f}R per trade"
                )

            # What degraded?
            if metrics.actual_win_rate < 0.45:
                report["what_degraded"].append(
                    f"Low win rate: {metrics.actual_win_rate:.1%}"
                )
            if metrics.model_reality_gap > 0.05:
                report["what_degraded"].append(
                    f"Model-reality gap: {metrics.model_reality_gap:.2%}"
                )

            # Vol and regime forecasts
            report["vol_forecast"] = "Expected stable volatility — no major catalysts"
            report["regime_predicted"] = "Trend continuation expected"

            # Save to database
            cursor.execute(
                """
                INSERT OR REPLACE INTO weekly_performance_reports
                (week_start, what_worked, what_degraded, vol_forecast, regime_predicted, generated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    week_start,
                    json.dumps(report["what_worked"]),
                    json.dumps(report["what_degraded"]),
                    report["vol_forecast"],
                    report["regime_predicted"],
                    report["generated_at"],
                ),
            )

            conn.commit()
            conn.close()

            logger.info(f"Weekly report generated for {week_start}")
            return report

        except Exception as exc:
            logger.error(f"Failed to generate weekly report: {exc}")
            report["status"] = "ERROR"
            return report

    def get_backtest_history(self, num_weeks: int = 12) -> list[dict]:
        """Fetch N weeks of backtest history."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM weekly_backtest_results
                ORDER BY week_start DESC
                LIMIT ?
                """,
                (num_weeks,),
            )
            rows = cursor.fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception as exc:
            logger.error(f"Failed to fetch backtest history: {exc}")
            return []
