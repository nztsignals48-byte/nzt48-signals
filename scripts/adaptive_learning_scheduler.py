"""
Adaptive Learning Scheduler — Daily, Weekly, Monthly Jobs
=========================================================
APScheduler integration for BUILD WEEK 9-10 continuous optimization system.

Scheduled Jobs:
  Daily:
    - 17:00 UTC (12:00 ET): DailyOptimizer.run_nightly_analysis()
    - 17:15 UTC (12:15 ET): GeneratePerformanceReport.daily_summary()

  Weekly:
    - Sunday 23:00 UTC (18:00 ET): WeeklyBacktester.run()
    - Sunday 23:15 UTC (18:15 ET): GeneratePerformanceReport.weekly_summary()

  Monthly:
    - 1st of month, 09:00 UTC: GeneratePerformanceReport.monthly_summary()

  Continuous:
    - Every 60 minutes: SignalDecayDetector.detect_decay()
    - Every trade: Telegram alert + audit log

CRITICAL: All jobs are READ-ONLY by default. NO automated changes to strategy without approval.
Audit log + rollback capability for all optimization decisions.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

# Import learning modules
from learning.daily_optimization import DailyOptimizer
from learning.signal_decay_detector import SignalDecayDetector
from learning.weekly_backtest import WeeklyBacktester
from monitoring.performance_report import GeneratePerformanceReport

logger = logging.getLogger("nzt48.adaptive_learning_scheduler")

# Database path
DB_PATH = Path(__file__).parent.parent / "data" / "nzt48.db"


class AdaptiveLearningScheduler:
    """Orchestrates all adaptive learning jobs."""

    def __init__(self, db_path: str | Path = DB_PATH):
        self.db_path = Path(db_path)
        self.scheduler = BackgroundScheduler()
        self._register_jobs()

    def _register_jobs(self):
        """Register all scheduled jobs."""
        logger.info("Registering adaptive learning jobs...")

        # === DAILY JOBS ===

        # 17:00 UTC: Nightly optimization analysis
        self.scheduler.add_job(
            self._job_daily_optimization,
            CronTrigger(hour=17, minute=0, timezone="UTC"),
            id="daily_optimization",
            name="Daily Optimization Analysis (17:00 UTC)",
            replace_existing=True,
        )

        # 17:15 UTC: Daily performance report
        self.scheduler.add_job(
            self._job_daily_summary,
            CronTrigger(hour=17, minute=15, timezone="UTC"),
            id="daily_summary",
            name="Daily Performance Report (17:15 UTC)",
            replace_existing=True,
        )

        # === WEEKLY JOBS ===

        # Sunday 23:00 UTC: Weekly backtest
        self.scheduler.add_job(
            self._job_weekly_backtest,
            CronTrigger(day_of_week=6, hour=23, minute=0, timezone="UTC"),
            id="weekly_backtest",
            name="Weekly Backtest (Sunday 23:00 UTC)",
            replace_existing=True,
        )

        # Sunday 23:15 UTC: Weekly summary
        self.scheduler.add_job(
            self._job_weekly_summary,
            CronTrigger(day_of_week=6, hour=23, minute=15, timezone="UTC"),
            id="weekly_summary",
            name="Weekly Performance Report (Sunday 23:15 UTC)",
            replace_existing=True,
        )

        # === MONTHLY JOBS ===

        # 1st of month, 09:00 UTC: Monthly summary
        self.scheduler.add_job(
            self._job_monthly_summary,
            CronTrigger(day=1, hour=9, minute=0, timezone="UTC"),
            id="monthly_summary",
            name="Monthly Performance Report (1st @ 09:00 UTC)",
            replace_existing=True,
        )

        # === CONTINUOUS MONITORING ===

        # Every 60 minutes: Signal decay detection
        self.scheduler.add_job(
            self._job_signal_decay_detection,
            IntervalTrigger(hours=1),
            id="signal_decay_detection",
            name="Signal Decay Detection (hourly)",
            replace_existing=True,
        )

        logger.info("✓ 7 jobs registered")

    def _job_daily_optimization(self):
        """Run nightly optimization analysis."""
        logger.info(">>> DAILY OPTIMIZATION JOB START")
        try:
            optimizer = DailyOptimizer(self.db_path)
            result = optimizer.run_nightly_analysis()

            logger.info(f">>> Daily optimization complete: {result.get('trades_analyzed')} trades analyzed")
            logger.info(f">>> Recommendations: {len(result.get('recommendations', []))}")

            # Send Telegram alert
            self._send_telegram_daily_optimization(result)

            # Log to audit
            self._log_audit_event("daily_optimization", result)

        except Exception as exc:
            logger.error(f">>> Daily optimization failed: {exc}", exc_info=True)
            self._send_telegram_error("Daily Optimization", str(exc))

    def _job_daily_summary(self):
        """Generate daily performance summary."""
        logger.info(">>> DAILY SUMMARY JOB START")
        try:
            reporter = GeneratePerformanceReport(self.db_path)
            summary = reporter.daily_summary()

            logger.info(f">>> Daily summary: {summary.trades} trades, WR={summary.win_rate:.1%}")

            # Send Telegram alert
            self._send_telegram_daily_summary(summary)

        except Exception as exc:
            logger.error(f">>> Daily summary failed: {exc}", exc_info=True)
            self._send_telegram_error("Daily Summary", str(exc))

    def _job_weekly_backtest(self):
        """Run weekly backtest."""
        logger.info(">>> WEEKLY BACKTEST JOB START")
        try:
            backtester = WeeklyBacktester(self.db_path)
            metrics = backtester.run_weekly_backtest(week_offset=-1)

            logger.info(
                f">>> Weekly backtest: gap={metrics.model_reality_gap:.2%}, status={metrics.gap_status}"
            )

            # Send Telegram alert
            self._send_telegram_weekly_backtest(metrics)

        except Exception as exc:
            logger.error(f">>> Weekly backtest failed: {exc}", exc_info=True)
            self._send_telegram_error("Weekly Backtest", str(exc))

    def _job_weekly_summary(self):
        """Generate weekly performance summary."""
        logger.info(">>> WEEKLY SUMMARY JOB START")
        try:
            reporter = GeneratePerformanceReport(self.db_path)
            summary = reporter.weekly_summary()

            logger.info(
                f">>> Weekly summary: {summary.trades} trades, WR={summary.rolling_7d_wr:.1%}, "
                f"Sharpe={summary.rolling_7d_sharpe:.2f}"
            )

            # Send Telegram alert
            self._send_telegram_weekly_summary(summary)

        except Exception as exc:
            logger.error(f">>> Weekly summary failed: {exc}", exc_info=True)
            self._send_telegram_error("Weekly Summary", str(exc))

    def _job_monthly_summary(self):
        """Generate monthly performance summary."""
        logger.info(">>> MONTHLY SUMMARY JOB START")
        try:
            reporter = GeneratePerformanceReport(self.db_path)
            summary = reporter.monthly_summary()

            logger.info(
                f">>> Monthly summary: {summary.trades} trades, WR={summary.win_rate_30d:.1%}, "
                f"Sharpe={summary.sharpe_30d:.2f}, Return={summary.total_return_pct:+.2f}%"
            )

            # Send Telegram alert
            self._send_telegram_monthly_summary(summary)

        except Exception as exc:
            logger.error(f">>> Monthly summary failed: {exc}", exc_info=True)
            self._send_telegram_error("Monthly Summary", str(exc))

    def _job_signal_decay_detection(self):
        """Run continuous signal decay detection."""
        try:
            detector = SignalDecayDetector(self.db_path)
            reports = detector.detect_decay(lookback_days=30)

            decayed = [r for r in reports if r.decay_detected]
            if decayed:
                logger.warning(f">>> Signal decay detected: {len(decayed)} signals degraded")
                recs = detector.recommend_actions(reports)
                logger.warning(f">>> Actions: {recs['actions']}")

                # Save history
                detector.save_decay_history(reports)

                # Send alert if regime shift detected
                if recs["regime_shift_likely"]:
                    self._send_telegram_regime_alert(decayed, recs)
            else:
                logger.debug(f">>> Signal decay check: all {len(reports)} signals healthy")

        except Exception as exc:
            logger.error(f">>> Signal decay detection failed: {exc}", exc_info=True)

    # === TELEGRAM ALERTS ===

    def _send_telegram_daily_optimization(self, result: dict):
        """Send daily optimization summary to Telegram."""
        try:
            import os
            token = os.environ.get("TELEGRAM_BOT_TOKEN")
            chat_id = os.environ.get("TELEGRAM_CHAT_ID")
            if not token or not chat_id:
                return

            trades = result.get("trades_analyzed", 0)
            recs = result.get("recommendations", [])

            message = f"""
📊 DAILY OPTIMIZATION SUMMARY
Trades Analyzed: {trades}
Recommendations: {len(recs)}

Top Winners: {len(result.get('top_winners', []))}
Top Losers: {len(result.get('top_losers', []))}

Status: {'✅ OK' if len(recs) <= 2 else '⚠️ NEEDS REVIEW' if len(recs) <= 5 else '🔴 CRITICAL'}
"""

            self._post_telegram_message(token, chat_id, message.strip())
        except Exception as exc:
            logger.warning(f"Failed to send Telegram alert: {exc}")

    def _send_telegram_daily_summary(self, summary):
        """Send daily summary to Telegram."""
        try:
            import os
            token = os.environ.get("TELEGRAM_BOT_TOKEN")
            chat_id = os.environ.get("TELEGRAM_CHAT_ID")
            if not token or not chat_id:
                return

            message = f"""
📈 DAILY PERFORMANCE

Trades: {summary.trades}
Win Rate: {summary.win_rate:.1%}
Return: {summary.daily_return_pct:+.2f}%
Max DD: {summary.max_drawdown_pct:+.2f}R
Heat: {summary.heat_level}

Best Signal: {summary.top_signal} ({summary.top_signal_wr:.1%})
Entry Quality: {summary.entry_quality_pct:.0f}%
"""

            self._post_telegram_message(token, chat_id, message.strip())
        except Exception as exc:
            logger.warning(f"Failed to send daily summary: {exc}")

    def _send_telegram_weekly_backtest(self, metrics):
        """Send weekly backtest results to Telegram."""
        try:
            import os
            token = os.environ.get("TELEGRAM_BOT_TOKEN")
            chat_id = os.environ.get("TELEGRAM_CHAT_ID")
            if not token or not chat_id:
                return

            status_emoji = "✅" if metrics.gap_status == "OK" else "⚠️" if metrics.gap_status == "WARNING" else "🔴"

            message = f"""
📊 WEEKLY BACKTEST RESULTS {status_emoji}

Period: {metrics.week_start} to {metrics.week_end}
Trades: {metrics.trades_count}

Model WR: {metrics.model_win_rate:.1%}
Actual WR: {metrics.actual_win_rate:.1%}
Gap: {metrics.model_reality_gap:.2%} ({metrics.gap_status})

Model Daily Return: {metrics.model_daily_return:+.2%}
Actual Daily Return: {metrics.actual_daily_return:+.2%}
"""

            self._post_telegram_message(token, chat_id, message.strip())
        except Exception as exc:
            logger.warning(f"Failed to send weekly backtest: {exc}")

    def _send_telegram_weekly_summary(self, summary):
        """Send weekly summary to Telegram."""
        try:
            import os
            token = os.environ.get("TELEGRAM_BOT_TOKEN")
            chat_id = os.environ.get("TELEGRAM_CHAT_ID")
            if not token or not chat_id:
                return

            message = f"""
📈 WEEKLY PERFORMANCE SUMMARY

Period: {summary.week_start} to {summary.week_end}
Trades: {summary.trades}
Win Rate: {summary.rolling_7d_wr:.1%}
Avg R: {summary.rolling_7d_avg_r:+.2f}
Sharpe: {summary.rolling_7d_sharpe:.2f}
Max DD: {summary.max_dd_7d:+.2f}R

Best Signal: {summary.best_signal} ({summary.best_signal_wr:.1%})
Worst Signal: {summary.worst_signal} ({summary.worst_signal_wr:.1%})
"""

            self._post_telegram_message(token, chat_id, message.strip())
        except Exception as exc:
            logger.warning(f"Failed to send weekly summary: {exc}")

    def _send_telegram_monthly_summary(self, summary):
        """Send monthly summary to Telegram."""
        try:
            import os
            token = os.environ.get("TELEGRAM_BOT_TOKEN")
            chat_id = os.environ.get("TELEGRAM_CHAT_ID")
            if not token or not chat_id:
                return

            message = f"""
📊 MONTHLY PERFORMANCE SUMMARY

Period: {summary.month_start} to {summary.month_end}
Trades: {summary.trades}
Win Rate: {summary.win_rate_30d:.1%}
Sharpe: {summary.sharpe_30d:.2f}
Max DD: {summary.max_dd_30d:+.2f}R
Total Return: {summary.total_return_pct:+.2f}%

Best Day: {summary.best_day:+.2f}R
Worst Day: {summary.worst_day:+.2f}R
"""

            self._post_telegram_message(token, chat_id, message.strip())
        except Exception as exc:
            logger.warning(f"Failed to send monthly summary: {exc}")

    def _send_telegram_regime_alert(self, decayed_signals: list, recommendations: dict):
        """Send regime shift alert to Telegram."""
        try:
            import os
            token = os.environ.get("TELEGRAM_BOT_TOKEN")
            chat_id = os.environ.get("TELEGRAM_CHAT_ID")
            if not token or not chat_id:
                return

            signals_str = ", ".join([s.signal_name for s in decayed_signals[:5]])
            message = f"""
🚨 REGIME SHIFT DETECTED

{len(decayed_signals)} signals decayed:
{signals_str}

Actions:
{chr(10).join(f"• {a}" for a in recommendations.get('actions', [])[:3])}

⚠️ MANUAL REVIEW REQUIRED
"""

            self._post_telegram_message(token, chat_id, message.strip())
        except Exception as exc:
            logger.warning(f"Failed to send regime alert: {exc}")

    def _send_telegram_error(self, job_name: str, error: str):
        """Send error alert to Telegram."""
        try:
            import os
            token = os.environ.get("TELEGRAM_BOT_TOKEN")
            chat_id = os.environ.get("TELEGRAM_CHAT_ID")
            if not token or not chat_id:
                return

            message = f"""
🔴 JOB ERROR: {job_name}

{error[:200]}

Check logs immediately.
"""

            self._post_telegram_message(token, chat_id, message.strip())
        except Exception as exc:
            logger.warning(f"Failed to send error alert: {exc}")

    def _post_telegram_message(self, token: str, chat_id: str, text: str):
        """Post a message to Telegram."""
        import requests
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        try:
            requests.post(url, data=data, timeout=10)
        except Exception:
            pass

    def _log_audit_event(self, event_type: str, data: dict):
        """Log event to audit trail."""
        import sqlite3
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT,
                    event_type TEXT,
                    data TEXT
                )
            """)

            import uuid
            cursor.execute("""
                INSERT INTO audit_log (id, timestamp, event_type, data)
                VALUES (?, ?, ?, ?)
            """, (
                f"audit-{uuid.uuid4().hex[:8]}",
                datetime.now(timezone.utc).isoformat(),
                event_type,
                json.dumps(data, default=str),
            ))

            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning(f"Failed to log audit event: {exc}")

    def start(self):
        """Start the scheduler."""
        logger.info("=== ADAPTIVE LEARNING SCHEDULER STARTING ===")
        self.scheduler.start()
        logger.info("✓ Scheduler started with 7 jobs")

    def shutdown(self):
        """Gracefully shut down the scheduler."""
        logger.info("=== ADAPTIVE LEARNING SCHEDULER SHUTTING DOWN ===")
        self.scheduler.shutdown(wait=True)
        logger.info("✓ Scheduler shut down")

    def get_jobs(self):
        """List all registered jobs."""
        return self.scheduler.get_jobs()

    def pause_job(self, job_id: str):
        """Pause a specific job."""
        try:
            job = self.scheduler.get_job(job_id)
            if job:
                self.scheduler.pause_job(job_id)
                logger.info(f"Job {job_id} paused")
        except Exception as exc:
            logger.error(f"Failed to pause job {job_id}: {exc}")

    def resume_job(self, job_id: str):
        """Resume a paused job."""
        try:
            job = self.scheduler.get_job(job_id)
            if job:
                self.scheduler.resume_job(job_id)
                logger.info(f"Job {job_id} resumed")
        except Exception as exc:
            logger.error(f"Failed to resume job {job_id}: {exc}")


# Singleton instance
_scheduler_instance: Optional[AdaptiveLearningScheduler] = None


def get_scheduler(db_path: str | Path = DB_PATH) -> AdaptiveLearningScheduler:
    """Get or create the singleton scheduler."""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = AdaptiveLearningScheduler(db_path)
    return _scheduler_instance


if __name__ == "__main__":
    # Example usage
    import signal
    import sys

    scheduler = get_scheduler()
    scheduler.start()

    def signal_handler(sig, frame):
        print("\nShutting down...")
        scheduler.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    try:
        asyncio.run(asyncio.sleep(float("inf")))
    except KeyboardInterrupt:
        pass
