"""
NZT-48 Adaptive Intelligence Engine — The Brain Above The Brain
Beyond institutional: AI-powered nightly learning cycle that reviews ALL trade data,
generates per-ticker/per-strategy/per-regime parameter adjustments, and feeds them
back into the system for continuous improvement.

Architecture:
1. GATHER — Collect all learning module outputs into a unified intelligence snapshot
2. ANALYSE — Statistical analysis + AI reasoning (Gemini/Claude/any LLM)
3. RECOMMEND — Generate specific parameter adjustments per ticker, per strategy
4. APPLY — Auto-apply conservative adjustments, queue aggressive ones for approval
5. REPORT — Daily intelligence report to Telegram

This is what makes NZT-48 *better* than institutional:
- Institutions have quant teams that review data weekly. We do it EVERY NIGHT.
- Institutions silo indicator research. We cross-correlate everything per ticker.
- Institutions are slow to kill edges. We auto-halt within 20 trades.
- Institutions don't track missed trades. We do, and learn from what we filtered out.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.adaptive_intelligence")


@dataclass
class TickerIntelligence:
    """Per-ticker intelligence profile — the bot's knowledge about one instrument."""
    ticker: str = ""
    trades: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    best_strategy: str = ""
    best_strategy_wr: float = 0.0
    worst_strategy: str = ""
    worst_strategy_wr: float = 0.0
    best_regime: str = ""
    worst_regime: str = ""
    optimal_rvol: float = 1.5
    optimal_stop_mult: float = 1.5
    optimal_confidence_floor: int = 65
    false_breakout_rate: float = 0.0
    avg_hold_minutes: float = 0.0
    best_entry_hour: int = -1
    best_indicators: list[str] = field(default_factory=list)
    worst_indicators: list[str] = field(default_factory=list)
    # AI-generated insights
    ai_notes: str = ""
    recommendations: list[str] = field(default_factory=list)


@dataclass
class StrategyIntelligence:
    """Per-strategy intelligence profile — deep knowledge about one strategy."""
    strategy: str = ""
    trades: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    expectancy: float = 0.0
    best_regime: str = ""
    best_regime_wr: float = 0.0
    worst_regime: str = ""
    worst_regime_wr: float = 0.0
    best_tickers: list[str] = field(default_factory=list)
    worst_tickers: list[str] = field(default_factory=list)
    common_failure_type: str = ""
    failure_pct: float = 0.0
    setup_grade_avg: float = 0.0
    timing_grade_avg: float = 0.0
    management_grade_avg: float = 0.0
    # Auto-adjustments
    confidence_adjustment: int = 0
    recommended_regimes: list[str] = field(default_factory=list)
    avoid_regimes: list[str] = field(default_factory=list)


@dataclass
class DailyIntelligenceReport:
    """The nightly intelligence report — one per day."""
    date: str = ""
    timestamp: str = ""
    system_iq: float = 0.0
    system_iq_change: float = 0.0
    total_trades_today: int = 0
    total_pnl_r: float = 0.0
    win_rate_today: float = 0.0
    # Key findings
    top_finding: str = ""
    ticker_insights: list[TickerIntelligence] = field(default_factory=list)
    strategy_insights: list[StrategyIntelligence] = field(default_factory=list)
    # Parameter adjustments (to apply)
    adjustments: list[dict] = field(default_factory=list)
    # AI analysis (if available)
    ai_analysis: str = ""
    # Filter effectiveness
    filter_helping: bool = True
    filter_analysis: str = ""
    # Missed opportunities
    missed_trades_count: int = 0
    missed_would_have_won: int = 0
    # Recommendations
    action_items: list[str] = field(default_factory=list)

    def to_telegram(self) -> str:
        """Format for Telegram delivery."""
        lines = [
            "DAILY INTELLIGENCE REPORT",
            "=" * 30,
            f"Date: {self.date}",
            f"System IQ: {self.system_iq:.1f} ({self.system_iq_change:+.1f})",
            f"Trades: {self.total_trades_today} | WR: {self.win_rate_today:.0f}% | PnL: {self.total_pnl_r:+.2f}R",
            "",
        ]

        if self.top_finding:
            lines.append(f"KEY FINDING: {self.top_finding}")
            lines.append("")

        if self.adjustments:
            lines.append(f"PARAMETER ADJUSTMENTS ({len(self.adjustments)}):")
            for adj in self.adjustments[:5]:
                status = "AUTO" if adj.get("auto_apply") else "PENDING"
                lines.append(f"  [{status}] {adj.get('description', '')}")
            lines.append("")

        if self.missed_trades_count > 0:
            lines.append(
                f"MISSED TRADES: {self.missed_trades_count} blocked, "
                f"{self.missed_would_have_won} would have won"
            )
            if not self.filter_helping:
                lines.append(f"  ALERT: Filters may be too aggressive!")
            lines.append("")

        if self.action_items:
            lines.append("ACTION ITEMS:")
            for item in self.action_items[:5]:
                lines.append(f"  - {item}")
            lines.append("")

        if self.ai_analysis:
            # Truncate for Telegram
            analysis = self.ai_analysis[:500]
            lines.append(f"AI ANALYSIS: {analysis}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


class AdaptiveIntelligenceEngine:
    """The brain above the brain — nightly AI-powered learning cycle.

    Runs after market close. Gathers all data from all 10 learning modules,
    trade autopsies, missed trade journal, and generates:
    1. Per-ticker intelligence profiles
    2. Per-strategy intelligence profiles
    3. Specific parameter adjustments
    4. AI-powered analysis and recommendations
    5. Daily intelligence report for Telegram
    """

    # Conservative adjustments are auto-applied; aggressive ones need approval
    CONSERVATIVE_THRESHOLD = 5   # Max confidence adjustment auto-applied
    MIN_TRADES_FOR_ADJUSTMENT = 10  # Need N trades before adjusting

    def __init__(self, ai_api_key: str = "", ai_model: str = "gemini-2.5-flash") -> None:
        self._ai_api_key = ai_api_key
        self._ai_model = ai_model
        self._reports: list[DailyIntelligenceReport] = []
        self._ticker_profiles: dict[str, TickerIntelligence] = {}
        self._strategy_profiles: dict[str, StrategyIntelligence] = {}
        self._pending_adjustments: list[dict] = []

    async def run_nightly_cycle(self, conn: sqlite3.Connection,
                                 learning_engine=None) -> DailyIntelligenceReport:
        """Run the complete nightly intelligence cycle.

        This is THE critical function — called once per day after market close.
        It's what makes the system compound its intelligence over time.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        logger.info("=== NIGHTLY INTELLIGENCE CYCLE: %s ===", today)

        report = DailyIntelligenceReport(
            date=today,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # === 1. GATHER: Collect today's trade data ===
        today_trades = self._get_today_trades(conn)
        report.total_trades_today = len(today_trades)
        if today_trades:
            wins = sum(1 for t in today_trades if t["r_multiple"] > 0)
            report.win_rate_today = (wins / len(today_trades)) * 100
            report.total_pnl_r = sum(t["r_multiple"] for t in today_trades)

        # === 2. GATHER: All-time data for deep analysis ===
        all_trades = self._get_all_trades(conn)
        autopsies = self._get_autopsies(conn)
        missed_trades = self._get_missed_trades(conn)

        # === 3. ANALYSE: Build per-ticker intelligence ===
        report.ticker_insights = self._build_ticker_intelligence(all_trades, autopsies, conn)
        self._ticker_profiles = {t.ticker: t for t in report.ticker_insights}

        # === 4. ANALYSE: Build per-strategy intelligence ===
        report.strategy_insights = self._build_strategy_intelligence(all_trades, autopsies, conn)
        self._strategy_profiles = {s.strategy: s for s in report.strategy_insights}

        # === 5. ANALYSE: Filter effectiveness ===
        if missed_trades:
            report.missed_trades_count = len(missed_trades)
            report.missed_would_have_won = sum(
                1 for m in missed_trades if m.get("hypothetical_r", 0) > 0
            )
            total_missed = len(missed_trades)
            if total_missed > 10:
                missed_wr = (report.missed_would_have_won / total_missed) * 100
                taken_wr = report.win_rate_today if report.total_trades_today > 0 else 0
                report.filter_helping = missed_wr < taken_wr
                report.filter_analysis = (
                    f"Missed WR: {missed_wr:.0f}% vs Taken WR: {taken_wr:.0f}% — "
                    f"{'Filters HELPING' if report.filter_helping else 'Filters TOO AGGRESSIVE'}"
                )

        # === 6. GENERATE: Parameter adjustments ===
        report.adjustments = self._generate_adjustments(
            report.ticker_insights, report.strategy_insights, all_trades
        )
        self._pending_adjustments = [a for a in report.adjustments if not a.get("auto_apply")]

        # === 7. AI ANALYSIS (if API key available) ===
        if self._ai_api_key and len(all_trades) >= 10:
            try:
                report.ai_analysis = await self._run_ai_analysis(
                    report, all_trades, autopsies, missed_trades
                )
            except Exception as e:
                logger.error("AI analysis failed (non-critical): %s", e)
                report.ai_analysis = ""

        # === 8. DETERMINE top finding ===
        report.top_finding = self._determine_top_finding(report)

        # === 9. GENERATE action items ===
        report.action_items = self._generate_action_items(report)

        # === 10. GET System IQ ===
        if learning_engine:
            iq_data = learning_engine.system_iq.get_current()
            report.system_iq = iq_data.get("iq", 0)
            if self._reports:
                report.system_iq_change = report.system_iq - self._reports[-1].system_iq

        # === 11. PERSIST report ===
        self._reports.append(report)
        self._persist_report(conn, report)

        # === 12. AUTO-APPLY conservative adjustments ===
        auto_applied = self._auto_apply_adjustments(conn, report.adjustments)
        logger.info(
            "NIGHTLY CYCLE COMPLETE: %d trades, %d adjustments (%d auto-applied), IQ=%.1f",
            report.total_trades_today, len(report.adjustments), auto_applied, report.system_iq,
        )

        return report

    def _get_today_trades(self, conn: sqlite3.Connection) -> list[dict]:
        """Get today's closed trades."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            rows = conn.execute(
                """SELECT id, ticker, strategy, direction, r_multiple, net_pnl,
                          exit_reason, confidence, duration_minutes, regime_at_entry,
                          peak_r, trough_r, entry_time, exit_time, failure_category,
                          bot_instance
                   FROM virtual_trades
                   WHERE date(exit_time) = ?
                   ORDER BY exit_time ASC""",
                (today,),
            ).fetchall()
            return [dict(row) for row in rows] if rows else []
        except Exception as e:
            logger.warning("Failed to get today's trades: %s", e)
            return []

    def _get_all_trades(self, conn: sqlite3.Connection) -> list[dict]:
        """Get all closed trades for deep analysis."""
        try:
            rows = conn.execute(
                """SELECT id, ticker, strategy, direction, r_multiple, net_pnl,
                          exit_reason, confidence, duration_minutes, regime_at_entry,
                          peak_r, trough_r, entry_time, exit_time, failure_category,
                          bot_instance
                   FROM virtual_trades
                   ORDER BY exit_time ASC"""
            ).fetchall()
            return [dict(row) for row in rows] if rows else []
        except Exception as e:
            logger.warning("Failed to get all trades: %s", e)
            return []

    def _get_autopsies(self, conn: sqlite3.Connection) -> list[dict]:
        """Get all trade autopsies."""
        try:
            rows = conn.execute(
                """SELECT * FROM trade_autopsies ORDER BY rowid DESC LIMIT 500"""
            ).fetchall()
            return [dict(row) for row in rows] if rows else []
        except Exception as e:
            logger.debug("No autopsies table yet: %s", e)
            return []

    def _get_missed_trades(self, conn: sqlite3.Connection) -> list[dict]:
        """Get all missed/rejected trades."""
        try:
            rows = conn.execute(
                """SELECT * FROM missed_trades ORDER BY timestamp DESC LIMIT 500"""
            ).fetchall()
            return [dict(row) for row in rows] if rows else []
        except Exception as e:
            logger.debug("No missed_trades table yet: %s", e)
            return []

    def _build_ticker_intelligence(self, trades: list[dict],
                                    autopsies: list[dict],
                                    conn: sqlite3.Connection) -> list[TickerIntelligence]:
        """Build deep intelligence profile for each ticker."""
        if not trades:
            return []

        # Group trades by ticker
        by_ticker: dict[str, list[dict]] = defaultdict(list)
        for t in trades:
            by_ticker[t["ticker"]].append(t)

        # Group autopsies by ticker
        autopsy_by_ticker: dict[str, list[dict]] = defaultdict(list)
        for a in autopsies:
            autopsy_by_ticker[a.get("ticker", "")].append(a)

        profiles = []
        for ticker, ticker_trades in by_ticker.items():
            if len(ticker_trades) < 3:
                continue

            profile = TickerIntelligence(ticker=ticker)
            profile.trades = len(ticker_trades)

            # Win rate
            wins = sum(1 for t in ticker_trades if t["r_multiple"] > 0)
            profile.win_rate = (wins / len(ticker_trades)) * 100

            # Average R
            profile.avg_r = sum(t["r_multiple"] for t in ticker_trades) / len(ticker_trades)

            # Best/worst strategy
            strat_performance = defaultdict(lambda: {"wins": 0, "total": 0, "r_sum": 0})
            for t in ticker_trades:
                s = t["strategy"]
                strat_performance[s]["total"] += 1
                strat_performance[s]["r_sum"] += t["r_multiple"]
                if t["r_multiple"] > 0:
                    strat_performance[s]["wins"] += 1

            best_strat, worst_strat = None, None
            best_wr, worst_wr = -1, 101
            for s, data in strat_performance.items():
                if data["total"] >= 3:
                    wr = (data["wins"] / data["total"]) * 100
                    if wr > best_wr:
                        best_wr = wr
                        best_strat = s
                    if wr < worst_wr:
                        worst_wr = wr
                        worst_strat = s

            profile.best_strategy = best_strat or ""
            profile.best_strategy_wr = best_wr if best_strat else 0
            profile.worst_strategy = worst_strat or ""
            profile.worst_strategy_wr = worst_wr if worst_strat else 0

            # Best/worst regime
            regime_performance = defaultdict(lambda: {"wins": 0, "total": 0})
            for t in ticker_trades:
                r = t.get("regime_at_entry", "")
                if r:
                    regime_performance[r]["total"] += 1
                    if t["r_multiple"] > 0:
                        regime_performance[r]["wins"] += 1

            best_regime, worst_regime = "", ""
            best_regime_wr, worst_regime_wr = -1, 101
            for r, data in regime_performance.items():
                if data["total"] >= 3:
                    wr = (data["wins"] / data["total"]) * 100
                    if wr > best_regime_wr:
                        best_regime_wr = wr
                        best_regime = r
                    if wr < worst_regime_wr:
                        worst_regime_wr = wr
                        worst_regime = r
            profile.best_regime = best_regime
            profile.worst_regime = worst_regime

            # False breakout rate
            false_breakouts = sum(
                1 for t in ticker_trades
                if t["r_multiple"] <= -0.5 and (t.get("duration_minutes") or 999) < 30
            )
            profile.false_breakout_rate = false_breakouts / len(ticker_trades)

            # Average hold time
            durations = [t.get("duration_minutes", 0) for t in ticker_trades if t.get("duration_minutes")]
            profile.avg_hold_minutes = sum(durations) / len(durations) if durations else 0

            # Best entry hour (from winning trades)
            hour_wins = defaultdict(lambda: {"wins": 0, "total": 0})
            for t in ticker_trades:
                try:
                    hour = datetime.fromisoformat(t["entry_time"]).hour
                    hour_wins[hour]["total"] += 1
                    if t["r_multiple"] > 0:
                        hour_wins[hour]["wins"] += 1
                except (ValueError, TypeError, KeyError):
                    pass
            best_hour = -1
            best_hour_wr = -1
            for h, data in hour_wins.items():
                if data["total"] >= 3:
                    wr = data["wins"] / data["total"]
                    if wr > best_hour_wr:
                        best_hour_wr = wr
                        best_hour = h
            profile.best_entry_hour = best_hour

            # Optimal parameters from winning trades
            winning = [t for t in ticker_trades if t["r_multiple"] > 0]
            losing = [t for t in ticker_trades if t["r_multiple"] < 0]

            if winning:
                win_confs = [t.get("confidence", 70) for t in winning if t.get("confidence")]
                if win_confs:
                    profile.optimal_confidence_floor = int(min(win_confs))

            # Autopsy insights
            ticker_autopsies = autopsy_by_ticker.get(ticker, [])
            if ticker_autopsies:
                grades = [a.get("setup_grade", 50) for a in ticker_autopsies]
                if grades:
                    avg_setup = sum(grades) / len(grades)

                # Find best indicators from autopsies
                indicator_verdicts = [
                    a.get("indicator_verdict", "")
                    for a in ticker_autopsies
                    if a.get("indicator_verdict", "") != "No single indicator stood out"
                ]
                if indicator_verdicts:
                    from collections import Counter
                    top_indicators = Counter(indicator_verdicts).most_common(3)
                    profile.best_indicators = [i[0] for i in top_indicators]

            # Generate recommendations
            recommendations = []
            if profile.false_breakout_rate > 0.25:
                recommendations.append(
                    f"High false breakout rate ({profile.false_breakout_rate:.0%}). "
                    f"Raise RVOL minimum or wait for confirmation."
                )
            if profile.worst_strategy and worst_wr < 30 and strat_performance[worst_strat]["total"] >= 5:
                recommendations.append(
                    f"Disable {profile.worst_strategy} for {ticker} (WR={worst_wr:.0f}%)"
                )
            if best_hour >= 0:
                recommendations.append(
                    f"Best entry hour: {best_hour}:00 UTC. Consider restricting entries."
                )
            if profile.avg_hold_minutes > 120 and profile.avg_r < 0.5:
                recommendations.append(
                    f"Avg hold {profile.avg_hold_minutes:.0f}min with low R. "
                    f"Tighten time-decay threshold."
                )
            profile.recommendations = recommendations

            profiles.append(profile)

        return sorted(profiles, key=lambda p: p.avg_r, reverse=True)

    def _build_strategy_intelligence(self, trades: list[dict],
                                      autopsies: list[dict],
                                      conn: sqlite3.Connection) -> list[StrategyIntelligence]:
        """Build deep intelligence profile for each strategy."""
        if not trades:
            return []

        by_strategy: dict[str, list[dict]] = defaultdict(list)
        for t in trades:
            by_strategy[t["strategy"]].append(t)

        autopsy_by_strategy: dict[str, list[dict]] = defaultdict(list)
        for a in autopsies:
            autopsy_by_strategy[a.get("strategy", "")].append(a)

        profiles = []
        for strategy, strat_trades in by_strategy.items():
            if len(strat_trades) < 5:
                continue

            profile = StrategyIntelligence(strategy=strategy)
            profile.trades = len(strat_trades)

            wins = sum(1 for t in strat_trades if t["r_multiple"] > 0)
            profile.win_rate = (wins / len(strat_trades)) * 100
            profile.avg_r = sum(t["r_multiple"] for t in strat_trades) / len(strat_trades)
            profile.expectancy = profile.avg_r  # Simplified

            # Best/worst regime for this strategy
            regime_perf = defaultdict(lambda: {"wins": 0, "total": 0})
            for t in strat_trades:
                r = t.get("regime_at_entry", "")
                if r:
                    regime_perf[r]["total"] += 1
                    if t["r_multiple"] > 0:
                        regime_perf[r]["wins"] += 1

            best_r, worst_r = "", ""
            best_r_wr, worst_r_wr = -1, 101
            for r, data in regime_perf.items():
                if data["total"] >= 3:
                    wr = (data["wins"] / data["total"]) * 100
                    if wr > best_r_wr:
                        best_r_wr = wr
                        best_r = r
                    if wr < worst_r_wr:
                        worst_r_wr = wr
                        worst_r = r
            profile.best_regime = best_r
            profile.best_regime_wr = best_r_wr if best_r else 0
            profile.worst_regime = worst_r
            profile.worst_regime_wr = worst_r_wr if worst_r else 0

            # Recommended/avoid regimes
            profile.recommended_regimes = [
                r for r, data in regime_perf.items()
                if data["total"] >= 5 and (data["wins"] / data["total"]) >= 0.55
            ]
            profile.avoid_regimes = [
                r for r, data in regime_perf.items()
                if data["total"] >= 5 and (data["wins"] / data["total"]) < 0.35
            ]

            # Best/worst tickers
            ticker_perf = defaultdict(lambda: {"wins": 0, "total": 0, "r_sum": 0})
            for t in strat_trades:
                ticker_perf[t["ticker"]]["total"] += 1
                ticker_perf[t["ticker"]]["r_sum"] += t["r_multiple"]
                if t["r_multiple"] > 0:
                    ticker_perf[t["ticker"]]["wins"] += 1

            sorted_tickers = sorted(
                [(tk, d["r_sum"] / d["total"]) for tk, d in ticker_perf.items() if d["total"] >= 3],
                key=lambda x: x[1], reverse=True,
            )
            profile.best_tickers = [t[0] for t in sorted_tickers[:3]]
            profile.worst_tickers = [t[0] for t in sorted_tickers[-3:]] if len(sorted_tickers) >= 3 else []

            # Common failure type
            failures = [t for t in strat_trades if t["r_multiple"] < 0]
            if failures:
                failure_cats = [t.get("failure_category", "UNKNOWN") for t in failures]
                from collections import Counter
                most_common = Counter(failure_cats).most_common(1)
                if most_common:
                    profile.common_failure_type = most_common[0][0]
                    profile.failure_pct = (most_common[0][1] / len(failures)) * 100

            # Autopsy grades
            strat_autopsies = autopsy_by_strategy.get(strategy, [])
            if strat_autopsies:
                setup_grades = [a.get("setup_grade", 50) for a in strat_autopsies]
                timing_grades = [a.get("timing_grade", 50) for a in strat_autopsies]
                mgmt_grades = [a.get("management_grade", 50) for a in strat_autopsies]
                profile.setup_grade_avg = sum(setup_grades) / len(setup_grades)
                profile.timing_grade_avg = sum(timing_grades) / len(timing_grades)
                profile.management_grade_avg = sum(mgmt_grades) / len(mgmt_grades)

            # Confidence adjustment
            if profile.trades >= self.MIN_TRADES_FOR_ADJUSTMENT:
                if profile.win_rate >= 65 and profile.avg_r >= 1.0:
                    profile.confidence_adjustment = 10
                elif profile.win_rate >= 55 and profile.avg_r >= 0.5:
                    profile.confidence_adjustment = 5
                elif profile.win_rate < 40:
                    profile.confidence_adjustment = -10
                elif profile.win_rate < 45:
                    profile.confidence_adjustment = -5

            profiles.append(profile)

        return sorted(profiles, key=lambda p: p.expectancy, reverse=True)

    def _generate_adjustments(self, ticker_insights: list[TickerIntelligence],
                               strategy_insights: list[StrategyIntelligence],
                               all_trades: list[dict]) -> list[dict]:
        """Generate specific parameter adjustments from intelligence."""
        adjustments = []

        # --- Ticker-level adjustments ---
        for ticker in ticker_insights:
            if ticker.trades < self.MIN_TRADES_FOR_ADJUSTMENT:
                continue

            # Disable underperforming strategies for this ticker
            if ticker.worst_strategy and ticker.worst_strategy_wr < 30:
                adjustments.append({
                    "type": "DISABLE_STRATEGY_FOR_TICKER",
                    "ticker": ticker.ticker,
                    "strategy": ticker.worst_strategy,
                    "reason": f"WR={ticker.worst_strategy_wr:.0f}% on {ticker.ticker}",
                    "description": f"Disable {ticker.worst_strategy} for {ticker.ticker} (WR {ticker.worst_strategy_wr:.0f}%)",
                    "auto_apply": True,
                    "value": {"disabled": True},
                })

            # Raise confidence floor for high false-breakout tickers
            if ticker.false_breakout_rate > 0.3:
                new_floor = max(70, ticker.optimal_confidence_floor + 5)
                adjustments.append({
                    "type": "RAISE_CONFIDENCE_FLOOR",
                    "ticker": ticker.ticker,
                    "reason": f"False breakout rate {ticker.false_breakout_rate:.0%}",
                    "description": f"Raise confidence floor for {ticker.ticker} to {new_floor}",
                    "auto_apply": True,
                    "value": {"confidence_floor": new_floor},
                })

            # Entry time restriction
            if ticker.best_entry_hour >= 0 and ticker.trades >= 20:
                adjustments.append({
                    "type": "OPTIMAL_ENTRY_HOUR",
                    "ticker": ticker.ticker,
                    "reason": f"Best entry hour analysis ({ticker.trades} trades)",
                    "description": f"{ticker.ticker} optimal entry: {ticker.best_entry_hour}:00 UTC",
                    "auto_apply": False,  # Informational
                    "value": {"best_hour": ticker.best_entry_hour},
                })

        # --- Strategy-level adjustments ---
        for strat in strategy_insights:
            if strat.trades < self.MIN_TRADES_FOR_ADJUSTMENT:
                continue

            # Disable strategy in bad regimes
            for regime in strat.avoid_regimes:
                adjustments.append({
                    "type": "DISABLE_STRATEGY_IN_REGIME",
                    "strategy": strat.strategy,
                    "regime": regime,
                    "reason": f"{strat.strategy} WR < 35% in {regime}",
                    "description": f"Disable {strat.strategy} in {regime} regime",
                    "auto_apply": True,
                    "value": {"disabled": True},
                })

            # Confidence boost/penalty
            if abs(strat.confidence_adjustment) > 0:
                auto = abs(strat.confidence_adjustment) <= self.CONSERVATIVE_THRESHOLD
                adjustments.append({
                    "type": "STRATEGY_CONFIDENCE_ADJ",
                    "strategy": strat.strategy,
                    "reason": f"WR={strat.win_rate:.0f}% avgR={strat.avg_r:.2f} over {strat.trades} trades",
                    "description": f"Adjust {strat.strategy} confidence by {strat.confidence_adjustment:+d}",
                    "auto_apply": auto,
                    "value": {"confidence_adj": strat.confidence_adjustment},
                })

            # Management improvement needed
            if strat.management_grade_avg > 0 and strat.management_grade_avg < 40:
                adjustments.append({
                    "type": "MANAGEMENT_ALERT",
                    "strategy": strat.strategy,
                    "reason": f"Avg management grade: {strat.management_grade_avg:.0f}/100",
                    "description": f"{strat.strategy} poor trade management (grade {strat.management_grade_avg:.0f})",
                    "auto_apply": False,
                    "value": {"management_grade": strat.management_grade_avg},
                })

        return adjustments

    async def _run_ai_analysis(self, report: DailyIntelligenceReport,
                                trades: list[dict], autopsies: list[dict],
                                missed: list[dict]) -> str:
        """Call AI (Gemini) for THREE types of analysis:

        1. TEXT ANALYSIS — qualitative insights a human quant would provide
        2. STRUCTURED ADJUSTMENTS — JSON parameter changes to auto-apply
        3. WORST TRADE REVIEW — AI diagnosis of the day's worst trades

        This is what makes us BETTER than institutional: AI + statistics + speed.
        """
        context = self._build_ai_context(report, trades, autopsies, missed)

        # === PASS 1: Qualitative analysis ===
        text_analysis = await self._ai_qualitative_analysis(context)

        # === PASS 2: Structured parameter recommendations (JSON) ===
        ai_adjustments = await self._ai_structured_adjustments(context, report)
        if ai_adjustments:
            for adj in ai_adjustments:
                adj["source"] = "AI"
                adj["auto_apply"] = adj.get("confidence_delta", 999) <= self.CONSERVATIVE_THRESHOLD
            report.adjustments.extend(ai_adjustments)

        # === PASS 3: Worst trade diagnosis ===
        worst_trades = sorted(trades, key=lambda t: t.get("r_multiple", 0))[:3]
        if worst_trades and worst_trades[0].get("r_multiple", 0) < -0.5:
            diagnosis = await self._ai_worst_trade_review(worst_trades, autopsies)
            if diagnosis:
                text_analysis += f"\n\nWORST TRADE DIAGNOSIS:\n{diagnosis}"

        # === PASS 4: Per-ticker AI notes for top/bottom performers ===
        await self._ai_ticker_notes(report, trades)

        return text_analysis

    async def _call_gemini(self, prompt: str, max_tokens: int = 500,
                            temperature: float = 0.3) -> str:
        """Shared Gemini API caller with retry."""
        try:
            import httpx
        except ImportError:
            logger.debug("httpx not available — skipping AI call")
            return ""

        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{self._ai_model}:generateContent",
                        params={"key": self._ai_api_key},
                        json={
                            "contents": [{"parts": [{"text": prompt}]}],
                            "generationConfig": {
                                "maxOutputTokens": max_tokens,
                                "temperature": temperature,
                            },
                        },
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            if parts:
                                return parts[0].get("text", "")
                    elif resp.status_code == 429:
                        logger.warning("AI rate limited, attempt %d", attempt + 1)
                        import asyncio
                        await asyncio.sleep(2)
                        continue
                    else:
                        logger.warning("AI API returned %d: %s", resp.status_code, resp.text[:200])
                        break
            except Exception as e:
                logger.warning("AI call error (attempt %d): %s", attempt + 1, e)
        return ""

    async def _ai_qualitative_analysis(self, context: str) -> str:
        """Pass 1: Qualitative analysis — the human quant perspective."""
        prompt = f"""You are an elite quantitative trading analyst reviewing a day's performance
for the NZT-48 automated trading system. The system trades leveraged ETPs on the LSE
and US equities via a UK ISA for tax-free compound growth.

Here is today's intelligence snapshot:

{context}

Provide a concise analysis (max 300 words) covering:
1. The most important pattern you see in the data
2. One specific parameter change that would improve performance
3. Whether the current filters are too aggressive or too loose
4. Which ticker+strategy combination has the most untapped potential
5. One risk you see that the statistical analysis might miss

Be specific with numbers. No general advice — only data-driven insights."""

        return await self._call_gemini(prompt, max_tokens=500, temperature=0.3)

    async def _ai_structured_adjustments(self, context: str,
                                          report: DailyIntelligenceReport) -> list[dict]:
        """Pass 2: Get AI to recommend specific parameter changes as JSON.

        This is the key enhancement — AI doesn't just explain, it PRESCRIBES
        changes the system can auto-apply.
        """
        prompt = f"""You are a quantitative trading system optimizer. Based on the performance data below,
generate SPECIFIC parameter adjustments as a JSON array.

PERFORMANCE DATA:
{context}

CURRENT ADJUSTMENTS ALREADY GENERATED BY STATISTICAL ANALYSIS:
{json.dumps(report.adjustments[:10], indent=2, default=str)}

Generate 1-5 ADDITIONAL adjustments the statistical model may have missed. Each adjustment must be:
{{"type": "confidence_floor|strategy_disable|regime_restrict|size_adjust|stop_adjust",
  "ticker": "NVDA" or "" for all,
  "strategy": "S1" or "" for all,
  "regime": "TRENDING_UP" or "" for all,
  "description": "human-readable explanation",
  "confidence_delta": integer (-15 to +15),
  "reasoning": "why this change will improve performance"}}

Rules:
- Only suggest changes backed by data patterns you can see
- confidence_delta <= 5 means it can be auto-applied safely
- Be conservative — wrong adjustments destroy edge
- Focus on the BIGGEST opportunity for improvement
- If no changes are needed, return an empty array []

Return ONLY valid JSON array, no markdown, no explanation outside the JSON."""

        raw = await self._call_gemini(prompt, max_tokens=800, temperature=0.2)
        if not raw:
            return []

        # Parse JSON from response (handle markdown wrapping)
        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
                cleaned = cleaned.rsplit("```", 1)[0]
            cleaned = cleaned.strip()
            adjustments = json.loads(cleaned)
            if isinstance(adjustments, list):
                logger.info("AI generated %d structured adjustments", len(adjustments))
                return adjustments[:5]
        except (json.JSONDecodeError, ValueError) as e:
            logger.debug("AI structured output parse failed: %s — raw: %s", e, raw[:200])

        return []

    async def _ai_worst_trade_review(self, worst_trades: list[dict],
                                      autopsies: list[dict]) -> str:
        """Pass 3: AI reviews the worst trades and diagnoses errors.

        This is like having a senior trader review your worst trades every night.
        """
        # Build trade summaries
        trade_details = []
        for t in worst_trades:
            detail = (
                f"- {t.get('ticker', '?')} {t.get('strategy', '?')} "
                f"{t.get('direction', '?')} R={t.get('r_multiple', 0):.2f} "
                f"Conf={t.get('confidence', 0)} Regime={t.get('regime_at_entry', '?')} "
                f"Duration={t.get('duration_minutes', 0)}min "
                f"Exit={t.get('exit_reason', '?')} "
                f"PeakR={t.get('peak_r', 0):.2f} TroughR={t.get('trough_r', 0):.2f}"
            )
            trade_details.append(detail)

        # Match autopsies
        autopsy_details = []
        for t in worst_trades:
            tid = t.get("id", "")
            matching = [a for a in autopsies if a.get("trade_id") == tid]
            if matching:
                a = matching[0]
                autopsy_details.append(
                    f"- {t.get('ticker', '?')}: Setup={a.get('setup_grade', '?')}/100 "
                    f"Timing={a.get('timing_grade', '?')}/100 "
                    f"Mgmt={a.get('management_grade', '?')}/100 "
                    f"Lesson: {a.get('primary_lesson', 'N/A')}"
                )

        prompt = f"""Review these worst trades from today's session and diagnose what went wrong.

WORST TRADES:
{chr(10).join(trade_details)}

AUTOPSY DATA:
{chr(10).join(autopsy_details) if autopsy_details else 'No autopsies available'}

For each trade, provide:
1. What specific error was made (entry timing, regime mismatch, size too large, etc)
2. What the system should do differently next time for this exact setup
3. Whether this is a SYSTEM error (needs code fix) or an EDGE case (accept and move on)

Be specific and actionable. Max 200 words total."""

        return await self._call_gemini(prompt, max_tokens=400, temperature=0.3)

    async def _ai_ticker_notes(self, report: DailyIntelligenceReport,
                                trades: list[dict]) -> None:
        """Pass 4: Generate AI notes for the top and bottom tickers.

        Updates the ai_notes field on TickerIntelligence profiles in-place.
        Only processes tickers with 10+ trades to be meaningful.
        """
        # Select tickers worth reviewing
        notable = [t for t in report.ticker_insights if t.trades >= 10]
        if not notable:
            return

        # Sort by potential impact: top 3 and bottom 3
        by_avg_r = sorted(notable, key=lambda t: t.avg_r, reverse=True)
        review_set = by_avg_r[:3] + by_avg_r[-3:]
        # Deduplicate
        seen = set()
        unique = []
        for t in review_set:
            if t.ticker not in seen:
                seen.add(t.ticker)
                unique.append(t)

        ticker_summaries = []
        for t in unique:
            ticker_summaries.append(
                f"{t.ticker}: {t.trades}t WR={t.win_rate:.0f}% avgR={t.avg_r:.2f} "
                f"best_strat={t.best_strategy}({t.best_strategy_wr:.0f}%) "
                f"worst_strat={t.worst_strategy}({t.worst_strategy_wr:.0f}%) "
                f"FBR={t.false_breakout_rate:.0%} best_regime={t.best_regime} "
                f"worst_regime={t.worst_regime}"
            )

        prompt = f"""For each ticker below, write a ONE LINE trading note (max 20 words)
that a trader should keep in mind. Focus on the most actionable insight.

{chr(10).join(ticker_summaries)}

Format: TICKER: note
Example: NVDA: Strong momentum plays only — avoid mean reversion setups in trending regimes"""

        raw = await self._call_gemini(prompt, max_tokens=300, temperature=0.3)
        if not raw:
            return

        # Parse notes and update profiles
        for line in raw.strip().split("\n"):
            line = line.strip()
            if ":" not in line:
                continue
            ticker_part = line.split(":")[0].strip().upper()
            note = ":".join(line.split(":")[1:]).strip()
            if ticker_part in self._ticker_profiles:
                self._ticker_profiles[ticker_part].ai_notes = note

    def _build_ai_context(self, report: DailyIntelligenceReport,
                           trades: list[dict], autopsies: list[dict],
                           missed: list[dict]) -> str:
        """Build concise context string for AI analysis."""
        lines = [
            f"Total trades: {len(trades)}",
            f"Today: {report.total_trades_today} trades, WR={report.win_rate_today:.0f}%, PnL={report.total_pnl_r:+.2f}R",
            f"System IQ: {report.system_iq:.1f}",
            "",
            "TOP 5 TICKERS BY AVG R:",
        ]
        for t in report.ticker_insights[:5]:
            lines.append(
                f"  {t.ticker}: {t.trades}t WR={t.win_rate:.0f}% avgR={t.avg_r:.2f} "
                f"best={t.best_strategy} worst={t.worst_strategy} FBR={t.false_breakout_rate:.0%}"
            )

        lines.append("\nBOTTOM 3 TICKERS:")
        for t in report.ticker_insights[-3:]:
            lines.append(
                f"  {t.ticker}: {t.trades}t WR={t.win_rate:.0f}% avgR={t.avg_r:.2f} "
                f"worst_regime={t.worst_regime}"
            )

        lines.append("\nSTRATEGY PERFORMANCE:")
        for s in report.strategy_insights[:10]:
            lines.append(
                f"  {s.strategy}: {s.trades}t WR={s.win_rate:.0f}% exp={s.expectancy:.3f} "
                f"best_regime={s.best_regime} avoid={','.join(s.avoid_regimes)}"
            )

        if missed:
            lines.append(f"\nMISSED TRADES: {len(missed)} blocked, "
                        f"{report.missed_would_have_won} would have won")

        if report.adjustments:
            lines.append(f"\nPENDING ADJUSTMENTS: {len(report.adjustments)}")
            for a in report.adjustments[:5]:
                lines.append(f"  [{a['type']}] {a['description']}")

        return "\n".join(lines)

    def _determine_top_finding(self, report: DailyIntelligenceReport) -> str:
        """Determine the single most important finding of the day."""
        findings = []

        # Check if filters are hurting us
        if not report.filter_helping and report.missed_trades_count > 5:
            findings.append((
                10,
                f"Filters blocked {report.missed_trades_count} trades, "
                f"{report.missed_would_have_won} would have won. Filters too aggressive."
            ))

        # Check for strategy in decline
        for s in report.strategy_insights:
            if s.win_rate < 35 and s.trades >= 20:
                findings.append((
                    8,
                    f"Strategy {s.strategy} declining: WR={s.win_rate:.0f}% over {s.trades} trades. "
                    f"Consider halting."
                ))

        # Check for ticker opportunities
        for t in report.ticker_insights:
            if t.win_rate > 70 and t.trades >= 10:
                findings.append((
                    7,
                    f"{t.ticker} is performing exceptionally (WR={t.win_rate:.0f}%, "
                    f"avgR={t.avg_r:.2f}). Consider increasing allocation."
                ))

        # Management issues
        for s in report.strategy_insights:
            if s.management_grade_avg > 0 and s.management_grade_avg < 40:
                findings.append((
                    6,
                    f"{s.strategy} trade management poor (grade {s.management_grade_avg:.0f}/100). "
                    f"Profit ladder may need tuning."
                ))

        if findings:
            findings.sort(key=lambda x: x[0], reverse=True)
            return findings[0][1]

        if report.total_trades_today == 0:
            return "No trades today — system may need more aggressive scanning."

        return f"Normal day: {report.total_trades_today} trades, {report.win_rate_today:.0f}% WR."

    def _generate_action_items(self, report: DailyIntelligenceReport) -> list[str]:
        """Generate prioritised action items."""
        items = []

        auto_count = sum(1 for a in report.adjustments if a.get("auto_apply"))
        pending_count = len(report.adjustments) - auto_count
        if auto_count > 0:
            items.append(f"{auto_count} parameter adjustments auto-applied")
        if pending_count > 0:
            items.append(f"{pending_count} adjustments pending approval (/approve_adjustments)")

        if not report.filter_helping:
            items.append("REVIEW: Filters may be too aggressive — check missed trade journal")

        for s in report.strategy_insights:
            if s.avoid_regimes:
                items.append(f"{s.strategy} auto-disabled in: {', '.join(s.avoid_regimes)}")

        # System IQ trend
        if report.system_iq_change < -1.0:
            items.append("WARNING: System IQ declining — review all learning module outputs")
        elif report.system_iq_change > 1.0:
            items.append("System IQ improving — learning engine working well")

        return items

    def _auto_apply_adjustments(self, conn: sqlite3.Connection,
                                 adjustments: list[dict]) -> int:
        """Auto-apply conservative adjustments to the database.

        These are stored in a learning_adjustments table so they're
        picked up by the qualification pipeline on next scan.
        """
        applied = 0
        for adj in adjustments:
            if not adj.get("auto_apply"):
                continue

            try:
                conn.execute(
                    """INSERT OR REPLACE INTO learning_state
                       (module, state_json, updated_at) VALUES (?, ?, ?)""",
                    (
                        f"adj_{adj['type']}_{adj.get('ticker', '')}_{adj.get('strategy', '')}_{adj.get('regime', '')}",
                        json.dumps(adj),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                applied += 1
                logger.info("AUTO-APPLIED: %s", adj["description"])
            except Exception as e:
                logger.error("Failed to apply adjustment: %s — %s", adj["description"], e)

        if applied > 0:
            conn.commit()
        return applied

    def _persist_report(self, conn: sqlite3.Connection,
                         report: DailyIntelligenceReport) -> None:
        """Persist the daily intelligence report."""
        try:
            conn.execute(
                """INSERT OR REPLACE INTO learning_state
                   (module, state_json, updated_at) VALUES (?, ?, ?)""",
                (
                    f"daily_report_{report.date}",
                    json.dumps(report.to_dict(), default=str),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        except Exception as e:
            logger.error("Failed to persist daily report: %s", e)

    def get_ticker_intel(self, ticker: str) -> Optional[TickerIntelligence]:
        """Get cached intelligence for a ticker."""
        return self._ticker_profiles.get(ticker)

    def get_strategy_intel(self, strategy: str) -> Optional[StrategyIntelligence]:
        """Get cached intelligence for a strategy."""
        return self._strategy_profiles.get(strategy)

    def get_pending_adjustments(self) -> list[dict]:
        """Get adjustments pending operator approval."""
        return self._pending_adjustments

    def approve_all_pending(self, conn: sqlite3.Connection) -> int:
        """Approve all pending adjustments."""
        count = self._auto_apply_adjustments(conn, self._pending_adjustments)
        self._pending_adjustments.clear()
        return count

    def get_latest_report(self) -> Optional[DailyIntelligenceReport]:
        """Get the most recent daily report."""
        return self._reports[-1] if self._reports else None

    def save_state(self, conn: sqlite3.Connection) -> None:
        """Persist adaptive intelligence state."""
        state = {
            "ticker_profiles": {
                t: {
                    "ticker": p.ticker, "trades": p.trades, "win_rate": p.win_rate,
                    "avg_r": p.avg_r, "best_strategy": p.best_strategy,
                    "worst_strategy": p.worst_strategy, "best_regime": p.best_regime,
                    "worst_regime": p.worst_regime, "false_breakout_rate": p.false_breakout_rate,
                    "optimal_confidence_floor": p.optimal_confidence_floor,
                    "best_entry_hour": p.best_entry_hour,
                    "recommendations": p.recommendations,
                }
                for t, p in self._ticker_profiles.items()
            },
            "strategy_profiles": {
                s: {
                    "strategy": p.strategy, "trades": p.trades, "win_rate": p.win_rate,
                    "avg_r": p.avg_r, "expectancy": p.expectancy,
                    "best_regime": p.best_regime, "worst_regime": p.worst_regime,
                    "avoid_regimes": p.avoid_regimes,
                    "confidence_adjustment": p.confidence_adjustment,
                }
                for s, p in self._strategy_profiles.items()
            },
            "pending_adjustments": self._pending_adjustments,
            "reports_count": len(self._reports),
        }
        try:
            conn.execute(
                """INSERT OR REPLACE INTO learning_state
                   (module, state_json, updated_at) VALUES (?, ?, ?)""",
                ("adaptive_intelligence", json.dumps(state), datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            logger.info("Adaptive Intelligence state saved")
        except Exception as e:
            logger.error("Failed to save AI state: %s", e)

    def load_state(self, conn: sqlite3.Connection) -> None:
        """Load adaptive intelligence state."""
        try:
            row = conn.execute(
                "SELECT state_json FROM learning_state WHERE module = ?",
                ("adaptive_intelligence",),
            ).fetchone()
        except Exception:
            return
        if not row:
            return
        state = json.loads(row["state_json"] if isinstance(row, sqlite3.Row) else row[0])

        # Restore ticker profiles
        for t, data in state.get("ticker_profiles", {}).items():
            profile = TickerIntelligence(**{k: v for k, v in data.items() if k != "recommendations"})
            profile.recommendations = data.get("recommendations", [])
            self._ticker_profiles[t] = profile

        # Restore strategy profiles
        for s, data in state.get("strategy_profiles", {}).items():
            profile = StrategyIntelligence(**{k: v for k, v in data.items() if k != "avoid_regimes"})
            profile.avoid_regimes = data.get("avoid_regimes", [])
            self._strategy_profiles[s] = profile

        self._pending_adjustments = state.get("pending_adjustments", [])
        logger.info(
            "Adaptive Intelligence loaded: %d tickers, %d strategies, %d pending adjustments",
            len(self._ticker_profiles), len(self._strategy_profiles),
            len(self._pending_adjustments),
        )
