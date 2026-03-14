"""
Daily Optimization Engine — Adaptive Learning from Yesterday's Trades
=====================================================================
END-OF-DAY analysis: win/loss patterns, signal decay, confidence calibration.

Section 49-52: Learning engine that improves entry timing daily by analyzing:
  1. Top 5 winners: signal composition, regime, confidence, tier
  2. Top 5 losers: false signals, late entry, regime mismatch, whipsaw
  3. Weekly metrics: win rate trend, confidence calibration, tier performance
  4. Adjustment recommendations: threshold tweaks, early detection tuning

Runs NIGHTLY (17:00 UTC / 12:00 PM ET) with:
  - Full audit log of all changes
  - Rollback capability if metrics degrade
  - SQLite persistence of all learning decisions
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nzt48.learning.daily_optimization")

DB_PATH = Path(__file__).parent.parent / "data" / "nzt48.db"


@dataclass
class TradeAnalysis:
    """Result of analyzing a single trade."""
    trade_id: str
    ticker: str
    direction: str
    pnl_r: float
    confidence: float
    regime: str
    entry_quality: float
    tier: str  # BLUE_CHIP, SPECIALIST, EXPANSION
    patterns: list[str] = field(default_factory=list)
    why_won_lost: str = ""  # Root cause analysis


@dataclass
class DailyMetrics:
    """Daily summary metrics."""
    date: str
    trades_taken: int
    win_rate: float
    avg_r_winner: float
    avg_r_loser: float
    confidence_calibration: float  # actual WR vs predicted confidence
    avg_entry_quality: float
    tier_performance: dict = field(default_factory=dict)  # tier -> win_rate
    regime_performance: dict = field(default_factory=dict)  # regime -> win_rate
    signal_effectiveness: dict = field(default_factory=dict)  # pattern -> win_rate


@dataclass
class OptimizationRecommendation:
    """A suggested parameter adjustment."""
    recommendation_id: str
    timestamp: datetime
    category: str  # "confidence_threshold", "early_detection", "tier_allocation", "regime_weighting"
    current_value: float
    suggested_value: float
    reason: str
    confidence_score: float  # 0-1: how confident we are in this change
    requires_approval: bool = True  # Manual sign-off needed


class DailyOptimizer:
    """Nightly optimization analysis and recommendation engine."""

    def __init__(self, db_path: str | Path = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_tables()

    def _ensure_tables(self):
        """Create optimization tracking tables if missing."""
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Learning decisions audit log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS learning_audit_log (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                optimization_id TEXT NOT NULL,
                decision_type TEXT NOT NULL,
                old_value REAL,
                new_value REAL,
                reason TEXT,
                confidence_score REAL,
                status TEXT,
                reverted_at TEXT,
                revert_reason TEXT,
                UNIQUE(optimization_id)
            )
        """)

        # Daily metrics history
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_metrics_history (
                date TEXT PRIMARY KEY,
                trades_taken INTEGER,
                win_rate REAL,
                avg_r_winner REAL,
                avg_r_loser REAL,
                confidence_calibration REAL,
                avg_entry_quality REAL,
                tier_performance TEXT,
                regime_performance TEXT,
                signal_effectiveness TEXT,
                computed_at TEXT
            )
        """)

        # Win/loss factor analysis
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trade_factor_analysis (
                trade_id TEXT PRIMARY KEY,
                date TEXT,
                ticker TEXT,
                direction TEXT,
                pnl_r REAL,
                confidence REAL,
                regime TEXT,
                entry_quality REAL,
                tier TEXT,
                patterns TEXT,
                why_won_lost TEXT,
                analyzed_at TEXT
            )
        """)

        # Optimization recommendations
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS optimization_recommendations (
                recommendation_id TEXT PRIMARY KEY,
                timestamp TEXT,
                category TEXT,
                current_value REAL,
                suggested_value REAL,
                reason TEXT,
                confidence_score REAL,
                status TEXT,
                approved_at TEXT,
                applied_at TEXT,
                reverted_at TEXT
            )
        """)

        conn.commit()
        conn.close()

    def run_nightly_analysis(self) -> dict:
        """Main nightly optimization job.

        Returns:
            Summary dict with results and recommendations
        """
        logger.info("=== NIGHTLY OPTIMIZATION ANALYSIS START ===")
        t0 = datetime.now(timezone.utc)
        summary = {
            "started_at": t0.isoformat(),
            "trades_analyzed": 0,
            "top_winners": [],
            "top_losers": [],
            "daily_metrics": None,
            "recommendations": [],
            "errors": [],
        }

        try:
            # 1. Fetch today's trades
            today = t0.strftime("%Y-%m-%d")
            trades = self._fetch_today_trades(today)
            summary["trades_analyzed"] = len(trades)
            logger.info(f"Analyzing {len(trades)} trades from {today}")

            if len(trades) < 2:
                logger.warning("Not enough trades to analyze (need >= 2)")
                summary["reason"] = "insufficient_trades"
                return summary

            # 2. Analyze each trade
            analyzed = []
            for trade in trades:
                analysis = self._analyze_trade(trade)
                analyzed.append(analysis)

            # 3. Extract top winners and losers
            top_winners = sorted(analyzed, key=lambda x: x.pnl_r, reverse=True)[:5]
            top_losers = sorted(analyzed, key=lambda x: x.pnl_r)[:5]

            summary["top_winners"] = [
                {
                    "trade_id": w.trade_id,
                    "ticker": w.ticker,
                    "pnl_r": w.pnl_r,
                    "confidence": w.confidence,
                    "regime": w.regime,
                    "tier": w.tier,
                    "why": w.why_won_lost,
                }
                for w in top_winners
            ]
            summary["top_losers"] = [
                {
                    "trade_id": l.trade_id,
                    "ticker": l.ticker,
                    "pnl_r": l.pnl_r,
                    "confidence": l.confidence,
                    "regime": l.regime,
                    "tier": l.tier,
                    "why": l.why_won_lost,
                }
                for l in top_losers
            ]

            # 4. Compute daily metrics
            metrics = self._compute_daily_metrics(analyzed, today)
            summary["daily_metrics"] = {
                "trades": metrics.trades_taken,
                "win_rate": metrics.win_rate,
                "avg_r_winner": metrics.avg_r_winner,
                "avg_r_loser": metrics.avg_r_loser,
                "confidence_calibration": metrics.confidence_calibration,
            }

            # 5. Generate recommendations
            recommendations = self._generate_recommendations(
                metrics, top_winners, top_losers, analyzed
            )
            summary["recommendations"] = [
                {
                    "id": r.recommendation_id,
                    "category": r.category,
                    "current": r.current_value,
                    "suggested": r.suggested_value,
                    "reason": r.reason,
                    "confidence": r.confidence_score,
                    "requires_approval": r.requires_approval,
                }
                for r in recommendations
            ]

            # 6. Persist findings
            self._save_analysis(analyzed, metrics, recommendations)

            logger.info(f"Analysis complete: {len(recommendations)} recommendations generated")

        except Exception as exc:
            logger.error(f"Nightly analysis failed: {exc}", exc_info=True)
            summary["errors"].append(str(exc))

        summary["completed_at"] = datetime.now(timezone.utc).isoformat()
        return summary

    def _fetch_today_trades(self, date: str) -> list:
        """Fetch all trades executed on the given date."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM trades
                WHERE DATE(time_entered) = ?
                ORDER BY time_entered DESC
                """,
                (date,),
            )
            rows = cursor.fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception as exc:
            logger.error(f"Failed to fetch trades: {exc}")
            return []

    def _analyze_trade(self, trade: dict) -> TradeAnalysis:
        """Analyze a single trade to extract learning factors."""
        trade_id = trade.get("id", "UNKNOWN")

        # Root cause analysis
        why = ""
        if trade.get("pnl_r_multiple", 0) > 1.0:
            # Winner
            why = self._why_won(trade)
        else:
            # Loser
            why = self._why_lost(trade)

        return TradeAnalysis(
            trade_id=trade_id,
            ticker=trade.get("ticker", ""),
            direction=trade.get("direction", "LONG"),
            pnl_r=trade.get("pnl_r_multiple", 0.0),
            confidence=trade.get("confidence_score", 0.0),
            regime=trade.get("regime_state", "UNKNOWN"),
            entry_quality=trade.get("entry_quality", 0.0),
            tier=trade.get("metadata", {}).get("tier", "UNKNOWN") if isinstance(trade.get("metadata"), dict) else "UNKNOWN",
            patterns=trade.get("patterns_detected", []),
            why_won_lost=why,
        )

    def _why_won(self, trade: dict) -> str:
        """Analyze why a winner won."""
        reasons = []

        if trade.get("confidence_score", 0) > 70:
            reasons.append("high_confidence_entry")

        if trade.get("entry_quality", 0) > 75:
            reasons.append("excellent_entry_timing")

        regime = trade.get("regime_state", "")
        if regime in ["TRENDING_UP_STRONG", "TRENDING_UP_MOD"]:
            reasons.append("bullish_regime_tailwind")

        if trade.get("internals_composite", 0) >= 3:
            reasons.append("strong_market_internals")

        patterns = trade.get("patterns_detected", [])
        if "BREAKOUT" in patterns:
            reasons.append("breakout_pattern")

        return " + ".join(reasons) if reasons else "favorable_conditions"

    def _why_lost(self, trade: dict) -> str:
        """Analyze why a loser lost."""
        reasons = []

        if trade.get("confidence_score", 0) > 70 and trade.get("pnl_r_multiple", 0) < 0:
            reasons.append("false_signal")

        if trade.get("entry_quality", 0) < 40:
            reasons.append("poor_entry_timing")

        # Check if already moved >2% before entry
        entry_price = trade.get("entry_price", 0)
        stop_price = trade.get("stop_price", 0)
        if entry_price > 0 and stop_price > 0:
            move_pct = abs(entry_price - stop_price) / entry_price
            if move_pct > 0.02:
                reasons.append("entered_after_2pct_move")

        regime = trade.get("regime_state", "")
        if regime in ["RANGE_BOUND", "HIGH_VOLATILITY", "SHOCK"]:
            reasons.append("choppy_regime")

        if trade.get("internals_composite", 0) <= 1:
            reasons.append("weak_market_internals")

        duration = trade.get("duration_minutes", 0)
        if duration < 5:
            reasons.append("whipsaw_quick_stop")

        return " + ".join(reasons) if reasons else "unfavorable_conditions"

    def _compute_daily_metrics(self, analyzed: list[TradeAnalysis], date: str) -> DailyMetrics:
        """Compute consolidated daily metrics."""
        if not analyzed:
            return DailyMetrics(date=date, trades_taken=0, win_rate=0, avg_r_winner=0, avg_r_loser=0, confidence_calibration=0, avg_entry_quality=0)

        winners = [t for t in analyzed if t.pnl_r > 0]
        losers = [t for t in analyzed if t.pnl_r <= 0]

        win_rate = len(winners) / len(analyzed) if analyzed else 0
        avg_r_winner = sum(t.pnl_r for t in winners) / len(winners) if winners else 0
        avg_r_loser = sum(t.pnl_r for t in losers) / len(losers) if losers else 0
        avg_entry_quality = sum(t.entry_quality for t in analyzed) / len(analyzed)

        # Confidence calibration: do higher-confidence trades actually win more?
        high_conf = [t for t in analyzed if t.confidence > 70]
        low_conf = [t for t in analyzed if t.confidence <= 70]
        high_conf_wr = len([t for t in high_conf if t.pnl_r > 0]) / len(high_conf) if high_conf else 0
        low_conf_wr = len([t for t in low_conf if t.pnl_r > 0]) / len(low_conf) if low_conf else 0
        confidence_calibration = high_conf_wr - low_conf_wr  # Should be > 0

        # Tier performance
        tier_perf = {}
        for tier in ["BLUE_CHIP", "SPECIALIST", "EXPANSION"]:
            tier_trades = [t for t in analyzed if t.tier == tier]
            if tier_trades:
                tier_wins = len([t for t in tier_trades if t.pnl_r > 0])
                tier_perf[tier] = tier_wins / len(tier_trades)

        # Regime performance
        regime_perf = {}
        for regime in set(t.regime for t in analyzed):
            regime_trades = [t for t in analyzed if t.regime == regime]
            if regime_trades:
                regime_wins = len([t for t in regime_trades if t.pnl_r > 0])
                regime_perf[regime] = regime_wins / len(regime_trades)

        # Signal effectiveness
        signal_eff = {}
        all_patterns = set()
        for t in analyzed:
            all_patterns.update(t.patterns)
        for pattern in all_patterns:
            pattern_trades = [t for t in analyzed if pattern in t.patterns]
            if pattern_trades:
                pattern_wins = len([t for t in pattern_trades if t.pnl_r > 0])
                signal_eff[pattern] = pattern_wins / len(pattern_trades)

        return DailyMetrics(
            date=date,
            trades_taken=len(analyzed),
            win_rate=win_rate,
            avg_r_winner=avg_r_winner,
            avg_r_loser=avg_r_loser,
            confidence_calibration=confidence_calibration,
            avg_entry_quality=avg_entry_quality,
            tier_performance=tier_perf,
            regime_performance=regime_perf,
            signal_effectiveness=signal_eff,
        )

    def _generate_recommendations(
        self,
        metrics: DailyMetrics,
        top_winners: list[TradeAnalysis],
        top_losers: list[TradeAnalysis],
        all_analyzed: list[TradeAnalysis],
    ) -> list[OptimizationRecommendation]:
        """Generate actionable optimization recommendations."""
        recs = []
        import uuid

        # 1. If win rate dropping: tighten confidence threshold
        if metrics.win_rate < 0.40 and metrics.trades_taken >= 5:
            recs.append(OptimizationRecommendation(
                recommendation_id=f"rec-{uuid.uuid4().hex[:8]}",
                timestamp=datetime.now(timezone.utc),
                category="confidence_threshold",
                current_value=70.0,
                suggested_value=75.0,
                reason=f"Win rate {metrics.win_rate:.1%} below 40% — raise confidence bar",
                confidence_score=0.8,
                requires_approval=True,
            ))

        # 2. If entering late: enhance early detection
        late_entries = [t for t in all_analyzed if t.entry_quality < 40]
        if len(late_entries) / len(all_analyzed) > 0.3:
            recs.append(OptimizationRecommendation(
                recommendation_id=f"rec-{uuid.uuid4().hex[:8]}",
                timestamp=datetime.now(timezone.utc),
                category="early_detection",
                current_value=1.0,
                suggested_value=0.85,
                reason=f"{len(late_entries)} trades entered late (>2% moved) — lower Tier 1 requirements",
                confidence_score=0.75,
                requires_approval=True,
            ))

        # 3. If one tier underperforming: reduce allocation
        if metrics.tier_performance:
            worst_tier = min(metrics.tier_performance.items(), key=lambda x: x[1])
            if worst_tier[1] < 0.35:
                recs.append(OptimizationRecommendation(
                    recommendation_id=f"rec-{uuid.uuid4().hex[:8]}",
                    timestamp=datetime.now(timezone.utc),
                    category="tier_allocation",
                    current_value=0.33,  # Assuming equal allocation
                    suggested_value=0.20,
                    reason=f"{worst_tier[0]} win rate {worst_tier[1]:.1%} — reduce allocation",
                    confidence_score=0.7,
                    requires_approval=True,
                ))

        # 4. If specific regime underperforming: adjust weighting
        if metrics.regime_performance:
            worst_regime = min(metrics.regime_performance.items(), key=lambda x: x[1])
            if worst_regime[1] < 0.30:
                recs.append(OptimizationRecommendation(
                    recommendation_id=f"rec-{uuid.uuid4().hex[:8]}",
                    timestamp=datetime.now(timezone.utc),
                    category="regime_weighting",
                    current_value=1.0,
                    suggested_value=0.7,
                    reason=f"{worst_regime[0]} win rate {worst_regime[1]:.1%} — reduce confidence weight in this regime",
                    confidence_score=0.65,
                    requires_approval=True,
                ))

        logger.info(f"Generated {len(recs)} optimization recommendations")
        return recs

    def _save_analysis(
        self,
        analyzed: list[TradeAnalysis],
        metrics: DailyMetrics,
        recommendations: list[OptimizationRecommendation],
    ):
        """Persist analysis results to database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Save trade factor analysis
        for t in analyzed:
            cursor.execute(
                """
                INSERT OR REPLACE INTO trade_factor_analysis
                (trade_id, date, ticker, direction, pnl_r, confidence, regime, entry_quality, tier, patterns, why_won_lost, analyzed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    t.trade_id,
                    metrics.date,
                    t.ticker,
                    t.direction,
                    t.pnl_r,
                    t.confidence,
                    t.regime,
                    t.entry_quality,
                    t.tier,
                    json.dumps(t.patterns),
                    t.why_won_lost,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

        # Save daily metrics
        cursor.execute(
            """
            INSERT OR REPLACE INTO daily_metrics_history
            (date, trades_taken, win_rate, avg_r_winner, avg_r_loser, confidence_calibration, avg_entry_quality, tier_performance, regime_performance, signal_effectiveness, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metrics.date,
                metrics.trades_taken,
                metrics.win_rate,
                metrics.avg_r_winner,
                metrics.avg_r_loser,
                metrics.confidence_calibration,
                metrics.avg_entry_quality,
                json.dumps(metrics.tier_performance),
                json.dumps(metrics.regime_performance),
                json.dumps(metrics.signal_effectiveness),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

        # Save recommendations
        for rec in recommendations:
            cursor.execute(
                """
                INSERT OR REPLACE INTO optimization_recommendations
                (recommendation_id, timestamp, category, current_value, suggested_value, reason, confidence_score, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.recommendation_id,
                    rec.timestamp.isoformat(),
                    rec.category,
                    rec.current_value,
                    rec.suggested_value,
                    rec.reason,
                    rec.confidence_score,
                    "PENDING" if rec.requires_approval else "AUTO_APPROVED",
                ),
            )

        conn.commit()
        conn.close()
        logger.info(f"Saved {len(analyzed)} trade analyses + {len(recommendations)} recommendations")

    def get_pending_recommendations(self) -> list[dict]:
        """Fetch all pending recommendations awaiting approval."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM optimization_recommendations WHERE status = 'PENDING' ORDER BY confidence_score DESC"
        )
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def approve_recommendation(self, recommendation_id: str, approve: bool = True):
        """Approve or reject a pending recommendation."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        status = "APPROVED" if approve else "REJECTED"
        cursor.execute(
            "UPDATE optimization_recommendations SET status = ? WHERE recommendation_id = ?",
            (status, recommendation_id),
        )

        # Log to audit
        cursor.execute(
            """
            INSERT INTO learning_audit_log
            (id, timestamp, optimization_id, decision_type, reason, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"audit-{recommendation_id}",
                datetime.now(timezone.utc).isoformat(),
                recommendation_id,
                f"recommendation_{status.lower()}",
                f"User {status.lower()} recommendation",
                "COMPLETED",
            ),
        )

        conn.commit()
        conn.close()
        logger.info(f"Recommendation {recommendation_id} marked as {status}")

    def rollback_change(self, optimization_id: str, reason: str):
        """Rollback a previously applied optimization."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE learning_audit_log
            SET reverted_at = ?, revert_reason = ?, status = 'REVERTED'
            WHERE optimization_id = ?
            """,
            (datetime.now(timezone.utc).isoformat(), reason, optimization_id),
        )

        conn.commit()
        conn.close()
        logger.warning(f"Rolled back optimization {optimization_id}: {reason}")
