"""
Performance Report Generator — Daily, Weekly, Monthly Summaries
==============================================================
Generates comprehensive performance reports at 3 frequencies:
  - DAILY (17:15 UTC / 12:15 PM ET): After-market summary
  - WEEKLY (Sunday 23:15 UTC): 7-day rolling analysis
  - MONTHLY (1st of month): 30-day return, Sharpe, max DD

Section 63: Trade journal + Section 49-52: Learning-driven optimization recommendations.

Reports include:
  - Trade count, win rate, avg profit per winner
  - Entry quality metrics
  - Risk summary: max drawdown, heat level, largest loss
  - Signal effectiveness
  - Tier performance breakdown
  - Optimization recommendations
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nzt48.monitoring.performance_report")

DB_PATH = Path(__file__).parent.parent / "data" / "nzt48.db"
REPORTS_ROOT = Path(__file__).parent.parent / "reports"


@dataclass
class DailySummary:
    """Daily performance summary."""
    date: str
    trades: int
    win_rate: float
    avg_profit_per_winner: float
    avg_loss_per_loser: float
    total_pnl_r: float
    daily_return_pct: float
    entry_quality_pct: float
    entry_in_first_rung_pct: float
    avg_confidence_score: float
    max_drawdown_pct: float
    heat_level: str
    largest_winner_r: float
    largest_loser_r: float
    top_signal: str
    top_signal_wr: float


@dataclass
class WeeklySummary:
    """Weekly performance summary (rolling 7 days)."""
    week_start: str
    week_end: str
    trades: int
    rolling_7d_wr: float
    rolling_7d_avg_r: float
    rolling_7d_sharpe: float
    max_dd_7d: float
    tier_performance: dict = field(default_factory=dict)
    signal_effectiveness: dict = field(default_factory=dict)
    best_signal: str = ""
    best_signal_wr: float = 0.0
    worst_signal: str = ""
    worst_signal_wr: float = 0.0
    regime_performance: dict = field(default_factory=dict)


@dataclass
class MonthlySummary:
    """Monthly performance summary (30 days)."""
    month_start: str
    month_end: str
    trades: int
    win_rate_30d: float
    sharpe_30d: float
    max_dd_30d: float
    total_return_pct: float
    best_day: float
    worst_day: float
    signal_evolution: dict = field(default_factory=dict)
    regime_patterns: dict = field(default_factory=dict)
    recommendations: list = field(default_factory=list)


class GeneratePerformanceReport:
    """Generates performance reports at multiple frequencies."""

    def __init__(self, db_path: str | Path = DB_PATH, reports_root: str | Path = REPORTS_ROOT):
        self.db_path = Path(db_path)
        self.reports_root = Path(reports_root)
        self.reports_root.mkdir(parents=True, exist_ok=True)
        self._ensure_tables()

    def _ensure_tables(self):
        """Create report history tables."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_summary_reports (
                date TEXT PRIMARY KEY,
                trades INTEGER,
                win_rate REAL,
                avg_profit_per_winner REAL,
                avg_loss_per_loser REAL,
                total_pnl_r REAL,
                daily_return_pct REAL,
                entry_quality_pct REAL,
                avg_confidence_score REAL,
                max_drawdown_pct REAL,
                heat_level TEXT,
                largest_winner_r REAL,
                largest_loser_r REAL,
                top_signal TEXT,
                generated_at TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS weekly_summary_reports (
                week_start TEXT PRIMARY KEY,
                week_end TEXT,
                trades INTEGER,
                rolling_7d_wr REAL,
                rolling_7d_avg_r REAL,
                rolling_7d_sharpe REAL,
                max_dd_7d REAL,
                tier_performance TEXT,
                signal_effectiveness TEXT,
                regime_performance TEXT,
                generated_at TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS monthly_summary_reports (
                month_start TEXT PRIMARY KEY,
                month_end TEXT,
                trades INTEGER,
                win_rate_30d REAL,
                sharpe_30d REAL,
                max_dd_30d REAL,
                total_return_pct REAL,
                best_day REAL,
                worst_day REAL,
                generated_at TEXT
            )
        """)

        conn.commit()
        conn.close()

    def daily_summary(self) -> DailySummary:
        """Generate end-of-day summary."""
        logger.info("=== DAILY PERFORMANCE SUMMARY ===")

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        summary = self._compute_daily_summary(today)

        try:
            self._save_daily_summary(summary)
            self._write_daily_report(summary)
        except Exception as exc:
            logger.error(f"Failed to save daily summary: {exc}")

        return summary

    def _compute_daily_summary(self, date: str) -> DailySummary:
        """Compute daily metrics from trade database."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Fetch all trades from today
            cursor.execute(
                """
                SELECT pnl_r_multiple, pnl_dollars, entry_quality, confidence_score,
                       patterns_detected, net_pnl
                FROM trades
                WHERE DATE(time_entered) = ?
                """,
                (date,),
            )
            trades = cursor.fetchall()
            conn.close()

            if not trades:
                return DailySummary(
                    date=date,
                    trades=0,
                    win_rate=0,
                    avg_profit_per_winner=0,
                    avg_loss_per_loser=0,
                    total_pnl_r=0,
                    daily_return_pct=0,
                    entry_quality_pct=0,
                    entry_in_first_rung_pct=0,
                    avg_confidence_score=0,
                    max_drawdown_pct=0,
                    heat_level="FLAT",
                    largest_winner_r=0,
                    largest_loser_r=0,
                    top_signal="NONE",
                    top_signal_wr=0,
                )

            pnl_rs = [t[0] for t in trades]
            pnl_dollars = [t[1] for t in trades]
            entry_qualities = [t[2] for t in trades]
            confidences = [t[3] for t in trades]

            trade_count = len(trades)
            winners = [r for r in pnl_rs if r > 0]
            losers = [r for r in pnl_rs if r <= 0]

            win_rate = len(winners) / trade_count if trade_count > 0 else 0
            avg_winner = sum(winners) / len(winners) if winners else 0
            avg_loser = sum(losers) / len(losers) if losers else 0
            total_pnl_r = sum(pnl_rs)

            # Daily return (assuming $10k starting equity)
            total_pnl_dollars = sum(pnl_dollars)
            daily_return_pct = (total_pnl_dollars / 10000) * 100 if total_pnl_dollars > 0 else 0

            # Entry quality
            avg_entry_quality = sum(entry_qualities) / len(entry_qualities) if entry_qualities else 0
            entry_first_rung = sum(1 for eq in entry_qualities if eq > 80) / len(entry_qualities) if entry_qualities else 0

            # Confidence
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0

            # Drawdown (max loss from peak)
            cumsum = 0
            peak = 0
            max_dd = 0
            for r in pnl_rs:
                cumsum += r
                if cumsum > peak:
                    peak = cumsum
                drawdown = peak - cumsum
                if drawdown > max_dd:
                    max_dd = drawdown

            # Heat level
            if win_rate < 0.40:
                heat_level = "RED"
            elif win_rate < 0.45:
                heat_level = "ORANGE"
            elif win_rate < 0.50:
                heat_level = "YELLOW"
            else:
                heat_level = "GREEN"

            # Find top signal
            top_signal = "MIXED"
            top_signal_wr = 0
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT strategy, COUNT(*) as cnt, SUM(CASE WHEN pnl_r_multiple > 0 THEN 1 ELSE 0 END) as wins
                    FROM trades
                    WHERE DATE(time_entered) = ?
                    GROUP BY strategy
                    ORDER BY wins DESC
                    LIMIT 1
                    """,
                    (date,),
                )
                row = cursor.fetchone()
                if row:
                    top_signal = row[0]
                    top_signal_wr = row[2] / row[1] if row[1] > 0 else 0
                conn.close()
            except Exception:
                pass

            return DailySummary(
                date=date,
                trades=trade_count,
                win_rate=win_rate,
                avg_profit_per_winner=avg_winner,
                avg_loss_per_loser=avg_loser,
                total_pnl_r=total_pnl_r,
                daily_return_pct=daily_return_pct,
                entry_quality_pct=avg_entry_quality,
                entry_in_first_rung_pct=entry_first_rung,
                avg_confidence_score=avg_confidence,
                max_drawdown_pct=max_dd,
                heat_level=heat_level,
                largest_winner_r=max(winners) if winners else 0,
                largest_loser_r=min(losers) if losers else 0,
                top_signal=top_signal,
                top_signal_wr=top_signal_wr,
            )

        except Exception as exc:
            logger.error(f"Failed to compute daily summary: {exc}")
            return DailySummary(date=date, trades=0, win_rate=0, avg_profit_per_winner=0,
                              avg_loss_per_loser=0, total_pnl_r=0, daily_return_pct=0,
                              entry_quality_pct=0, entry_in_first_rung_pct=0,
                              avg_confidence_score=0, max_drawdown_pct=0, heat_level="ERROR",
                              largest_winner_r=0, largest_loser_r=0, top_signal="ERROR", top_signal_wr=0)

    def _save_daily_summary(self, summary: DailySummary):
        """Persist daily summary to database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO daily_summary_reports
            (date, trades, win_rate, avg_profit_per_winner, avg_loss_per_loser, total_pnl_r,
             daily_return_pct, entry_quality_pct, avg_confidence_score, max_drawdown_pct, heat_level,
             largest_winner_r, largest_loser_r, top_signal, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                summary.date,
                summary.trades,
                summary.win_rate,
                summary.avg_profit_per_winner,
                summary.avg_loss_per_loser,
                summary.total_pnl_r,
                summary.daily_return_pct,
                summary.entry_quality_pct,
                summary.avg_confidence_score,
                summary.max_drawdown_pct,
                summary.heat_level,
                summary.largest_winner_r,
                summary.largest_loser_r,
                summary.top_signal,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

        conn.commit()
        conn.close()

    def _write_daily_report(self, summary: DailySummary):
        """Write daily report to disk."""
        date_dir = self.reports_root / summary.date
        date_dir.mkdir(parents=True, exist_ok=True)

        report_content = f"""
NZT-48 DAILY PERFORMANCE REPORT
==============================
Date: {summary.date}
Generated: {datetime.now(timezone.utc).isoformat()}

TRADES
------
Total:        {summary.trades}
Win Rate:     {summary.win_rate:.1%}
Avg Winner:   {summary.avg_profit_per_winner:+.2f}R
Avg Loser:    {summary.avg_loss_per_loser:+.2f}R
Total P&L:    {summary.total_pnl_r:+.2f}R
Daily Return: {summary.daily_return_pct:+.2f}%

ENTRY QUALITY
-------------
Avg Quality:         {summary.entry_quality_pct:.0f}%
In First Rung:       {summary.entry_in_first_rung_pct:.1%}
Avg Confidence:      {summary.avg_confidence_score:.0f}

RISK METRICS
------------
Max Drawdown:   {summary.max_drawdown_pct:+.2f}R
Heat Level:     {summary.heat_level}
Largest Winner: {summary.largest_winner_r:+.2f}R
Largest Loser:  {summary.largest_loser_r:+.2f}R

BEST SIGNAL
-----------
Signal:         {summary.top_signal}
Win Rate:       {summary.top_signal_wr:.1%}
"""

        report_file = date_dir / "daily_summary.txt"
        with open(report_file, "w") as f:
            f.write(report_content)

        logger.info(f"Daily report written: {report_file}")

    def weekly_summary(self) -> WeeklySummary:
        """Generate weekly summary (rolling 7 days)."""
        logger.info("=== WEEKLY PERFORMANCE SUMMARY ===")

        today = datetime.now(timezone.utc)
        week_start = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        week_end = today.strftime("%Y-%m-%d")

        summary = self._compute_weekly_summary(week_start, week_end)

        try:
            self._save_weekly_summary(summary)
            self._write_weekly_report(summary)
        except Exception as exc:
            logger.error(f"Failed to save weekly summary: {exc}")

        return summary

    def _compute_weekly_summary(self, week_start: str, week_end: str) -> WeeklySummary:
        """Compute weekly metrics."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT pnl_r_multiple FROM trades
                WHERE DATE(time_entered) BETWEEN ? AND ?
                """,
                (week_start, week_end),
            )
            rs = [row[0] for row in cursor.fetchall()]

            if not rs:
                return WeeklySummary(
                    week_start=week_start,
                    week_end=week_end,
                    trades=0,
                    rolling_7d_wr=0,
                    rolling_7d_avg_r=0,
                    rolling_7d_sharpe=0,
                    max_dd_7d=0,
                )

            trades = len(rs)
            wins = sum(1 for r in rs if r > 0)
            wr = wins / trades if trades > 0 else 0
            avg_r = sum(rs) / trades if trades > 0 else 0

            # Sharpe
            if trades > 1:
                mean = avg_r
                variance = sum((r - mean) ** 2 for r in rs) / (trades - 1)
                std = variance ** 0.5
                sharpe = mean / std if std > 0 else 0
            else:
                sharpe = 0

            # Max DD
            cumsum = 0
            peak = 0
            max_dd = 0
            for r in rs:
                cumsum += r
                if cumsum > peak:
                    peak = cumsum
                dd = peak - cumsum
                if dd > max_dd:
                    max_dd = dd

            # Tier performance
            cursor.execute(
                """
                SELECT metadata, pnl_r_multiple FROM trades
                WHERE DATE(time_entered) BETWEEN ? AND ?
                """,
                (week_start, week_end),
            )
            trades_data = cursor.fetchall()
            tier_perf = {}
            for meta, r in trades_data:
                try:
                    if meta and isinstance(meta, str):
                        m = json.loads(meta)
                        tier = m.get("tier", "UNKNOWN")
                        if tier not in tier_perf:
                            tier_perf[tier] = {"wins": 0, "total": 0}
                        tier_perf[tier]["total"] += 1
                        if r > 0:
                            tier_perf[tier]["wins"] += 1
                except:
                    pass

            tier_wr = {t: p["wins"] / p["total"] for t, p in tier_perf.items() if p["total"] > 0}

            # Signal effectiveness
            cursor.execute(
                """
                SELECT strategy, pnl_r_multiple FROM trades
                WHERE DATE(time_entered) BETWEEN ? AND ?
                """,
                (week_start, week_end),
            )
            sig_data = cursor.fetchall()
            sig_perf = {}
            for sig, r in sig_data:
                if sig not in sig_perf:
                    sig_perf[sig] = {"wins": 0, "total": 0}
                sig_perf[sig]["total"] += 1
                if r > 0:
                    sig_perf[sig]["wins"] += 1

            sig_wr = {s: p["wins"] / p["total"] for s, p in sig_perf.items() if p["total"] > 0}
            best_sig = max(sig_wr.items(), key=lambda x: x[1]) if sig_wr else ("NONE", 0)
            worst_sig = min(sig_wr.items(), key=lambda x: x[1]) if sig_wr else ("NONE", 0)

            conn.close()

            return WeeklySummary(
                week_start=week_start,
                week_end=week_end,
                trades=trades,
                rolling_7d_wr=wr,
                rolling_7d_avg_r=avg_r,
                rolling_7d_sharpe=sharpe,
                max_dd_7d=max_dd,
                tier_performance=tier_wr,
                signal_effectiveness=sig_wr,
                best_signal=best_sig[0],
                best_signal_wr=best_sig[1],
                worst_signal=worst_sig[0],
                worst_signal_wr=worst_sig[1],
            )

        except Exception as exc:
            logger.error(f"Failed to compute weekly summary: {exc}")
            return WeeklySummary(
                week_start=week_start,
                week_end=week_end,
                trades=0,
                rolling_7d_wr=0,
                rolling_7d_avg_r=0,
                rolling_7d_sharpe=0,
                max_dd_7d=0,
            )

    def _save_weekly_summary(self, summary: WeeklySummary):
        """Persist weekly summary."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO weekly_summary_reports
            (week_start, week_end, trades, rolling_7d_wr, rolling_7d_avg_r, rolling_7d_sharpe, max_dd_7d,
             tier_performance, signal_effectiveness, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                summary.week_start,
                summary.week_end,
                summary.trades,
                summary.rolling_7d_wr,
                summary.rolling_7d_avg_r,
                summary.rolling_7d_sharpe,
                summary.max_dd_7d,
                json.dumps(summary.tier_performance),
                json.dumps(summary.signal_effectiveness),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

        conn.commit()
        conn.close()

    def _write_weekly_report(self, summary: WeeklySummary):
        """Write weekly report to disk."""
        week_dir = self.reports_root / f"week_{summary.week_start}"
        week_dir.mkdir(parents=True, exist_ok=True)

        report_content = f"""
NZT-48 WEEKLY PERFORMANCE REPORT
===============================
Period: {summary.week_start} to {summary.week_end}
Generated: {datetime.now(timezone.utc).isoformat()}

ROLLING 7-DAY METRICS
---------------------
Trades:       {summary.trades}
Win Rate:     {summary.rolling_7d_wr:.1%}
Avg R:        {summary.rolling_7d_avg_r:+.2f}
Sharpe:       {summary.rolling_7d_sharpe:.2f}
Max DD:       {summary.max_dd_7d:+.2f}R

TIER PERFORMANCE
----------------
{chr(10).join(f"{t}: {w:.1%}" for t, w in summary.tier_performance.items())}

SIGNAL EFFECTIVENESS
--------------------
Best:         {summary.best_signal} ({summary.best_signal_wr:.1%})
Worst:        {summary.worst_signal} ({summary.worst_signal_wr:.1%})

TOP SIGNALS
-----------
{chr(10).join(f"{s}: {w:.1%}" for s, w in sorted(summary.signal_effectiveness.items(), key=lambda x: x[1], reverse=True)[:5])}
"""

        report_file = week_dir / "weekly_summary.txt"
        with open(report_file, "w") as f:
            f.write(report_content)

        logger.info(f"Weekly report written: {report_file}")

    def monthly_summary(self) -> MonthlySummary:
        """Generate monthly summary (30 days)."""
        logger.info("=== MONTHLY PERFORMANCE SUMMARY ===")

        today = datetime.now(timezone.utc)
        month_start = (today - timedelta(days=30)).strftime("%Y-%m-%d")
        month_end = today.strftime("%Y-%m-%d")

        summary = self._compute_monthly_summary(month_start, month_end)

        try:
            self._save_monthly_summary(summary)
            self._write_monthly_report(summary)
        except Exception as exc:
            logger.error(f"Failed to save monthly summary: {exc}")

        return summary

    def _compute_monthly_summary(self, month_start: str, month_end: str) -> MonthlySummary:
        """Compute 30-day metrics."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT pnl_r_multiple, pnl_dollars FROM trades
                WHERE DATE(time_entered) BETWEEN ? AND ?
                """,
                (month_start, month_end),
            )
            trades_data = cursor.fetchall()

            if not trades_data:
                return MonthlySummary(
                    month_start=month_start,
                    month_end=month_end,
                    trades=0,
                    win_rate_30d=0,
                    sharpe_30d=0,
                    max_dd_30d=0,
                    total_return_pct=0,
                    best_day=0,
                    worst_day=0,
                )

            rs = [t[0] for t in trades_data]
            pnls = [t[1] for t in trades_data]

            trades = len(rs)
            wins = sum(1 for r in rs if r > 0)
            wr = wins / trades if trades > 0 else 0

            mean_r = sum(rs) / trades
            if trades > 1:
                variance = sum((r - mean_r) ** 2 for r in rs) / (trades - 1)
                std = variance ** 0.5
                sharpe = mean_r / std if std > 0 else 0
            else:
                sharpe = 0

            # Max DD
            cumsum = 0
            peak = 0
            max_dd = 0
            for r in rs:
                cumsum += r
                if cumsum > peak:
                    peak = cumsum
                dd = peak - cumsum
                if dd > max_dd:
                    max_dd = dd

            total_return = (sum(pnls) / 10000) * 100 if pnls else 0
            best_day = max(rs) if rs else 0
            worst_day = min(rs) if rs else 0

            conn.close()

            return MonthlySummary(
                month_start=month_start,
                month_end=month_end,
                trades=trades,
                win_rate_30d=wr,
                sharpe_30d=sharpe,
                max_dd_30d=max_dd,
                total_return_pct=total_return,
                best_day=best_day,
                worst_day=worst_day,
            )

        except Exception as exc:
            logger.error(f"Failed to compute monthly summary: {exc}")
            return MonthlySummary(
                month_start=month_start,
                month_end=month_end,
                trades=0,
                win_rate_30d=0,
                sharpe_30d=0,
                max_dd_30d=0,
                total_return_pct=0,
                best_day=0,
                worst_day=0,
            )

    def _save_monthly_summary(self, summary: MonthlySummary):
        """Persist monthly summary."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO monthly_summary_reports
            (month_start, month_end, trades, win_rate_30d, sharpe_30d, max_dd_30d, total_return_pct, best_day, worst_day, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                summary.month_start,
                summary.month_end,
                summary.trades,
                summary.win_rate_30d,
                summary.sharpe_30d,
                summary.max_dd_30d,
                summary.total_return_pct,
                summary.best_day,
                summary.worst_day,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

        conn.commit()
        conn.close()

    def _write_monthly_report(self, summary: MonthlySummary):
        """Write monthly report to disk."""
        month_dir = self.reports_root / f"month_{summary.month_start[:7]}"
        month_dir.mkdir(parents=True, exist_ok=True)

        report_content = f"""
NZT-48 MONTHLY PERFORMANCE REPORT
=================================
Period: {summary.month_start} to {summary.month_end}
Generated: {datetime.now(timezone.utc).isoformat()}

30-DAY METRICS
--------------
Trades:         {summary.trades}
Win Rate:       {summary.win_rate_30d:.1%}
Sharpe Ratio:   {summary.sharpe_30d:.2f}
Max Drawdown:   {summary.max_dd_30d:+.2f}R
Total Return:   {summary.total_return_pct:+.2f}%

DAILY EXTREMES
--------------
Best Day:       {summary.best_day:+.2f}R
Worst Day:      {summary.worst_day:+.2f}R

NOTES
-----
- Review signal evolution to identify what changed
- Check regime patterns for seasonal/market-specific behavior
- Identify degraded signals for next month optimization
"""

        report_file = month_dir / "monthly_summary.txt"
        with open(report_file, "w") as f:
            f.write(report_content)

        logger.info(f"Monthly report written: {report_file}")
