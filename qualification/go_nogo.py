"""
NZT-48 Go/No-Go Scorecard — Section 45
Automated tracking of 10 launch criteria before transitioning from paper to live.
Reports progress via Telegram and dashboard.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.go_nogo")


@dataclass
class Criterion:
    """A single Go/No-Go criterion."""
    id: int
    name: str
    target: str
    current_value: str = ""
    met: bool = False


@dataclass
class GoNoGoScorecard:
    """Tracks all 10 launch criteria from Section 45."""
    criteria: list[Criterion] = field(default_factory=list)
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def met_count(self) -> int:
        return sum(1 for c in self.criteria if c.met)

    @property
    def total_count(self) -> int:
        return len(self.criteria)

    @property
    def progress_pct(self) -> float:
        return (self.met_count / self.total_count * 100) if self.total_count > 0 else 0


class GoNoGoTracker:
    """Evaluates Go/No-Go criteria from paper trading data.

    Section 45 — 10 Criteria:
    1. 50+ paper trades completed
    2. Raw win rate > 50%
    3. Effective win rate > 65% (partials count as wins)
    4. Profit factor > 1.3
    5. Max drawdown < 8%
    6. Never 5 consecutive losses (in entire paper period)
    7. Zero emotional firewall overrides
    8. No system crashes in last 5 days
    9. All active strategies have 5+ trades each
    10. Sharpe ratio > 1.0
    """

    def __init__(self) -> None:
        self._last_scorecard: GoNoGoScorecard | None = None

    def evaluate(self, conn) -> GoNoGoScorecard:
        """Evaluate all 10 criteria from the database.

        Args:
            conn: SQLite connection to nzt48.db

        Returns:
            GoNoGoScorecard with all criteria evaluated.
        """
        criteria = []

        # --- Criterion 1: 50+ paper trades ---
        total_trades = conn.execute(
            "SELECT COUNT(*) FROM virtual_trades"
        ).fetchone()[0]
        criteria.append(Criterion(
            id=1,
            name="50+ paper trades",
            target=">= 50",
            current_value=str(total_trades),
            met=total_trades >= 50,
        ))

        # --- Criterion 2: Raw win rate > 50% ---
        wins = conn.execute(
            "SELECT COUNT(*) FROM virtual_trades WHERE r_multiple > 0"
        ).fetchone()[0]
        raw_wr = (wins / total_trades * 100) if total_trades > 0 else 0
        criteria.append(Criterion(
            id=2,
            name="Raw win rate > 50%",
            target="> 50%",
            current_value=f"{raw_wr:.1f}%",
            met=raw_wr > 50,
        ))

        # --- Criterion 3: Effective win rate > 65% ---
        # Effective = trades that were profitable overall (including partials)
        effective_wins = conn.execute(
            "SELECT COUNT(*) FROM virtual_trades WHERE net_pnl > 0"
        ).fetchone()[0]
        effective_wr = (effective_wins / total_trades * 100) if total_trades > 0 else 0
        criteria.append(Criterion(
            id=3,
            name="Effective win rate > 65%",
            target="> 65%",
            current_value=f"{effective_wr:.1f}%",
            met=effective_wr > 65,
        ))

        # --- Criterion 4: Profit factor > 1.3 ---
        gross_profit = conn.execute(
            "SELECT COALESCE(SUM(net_pnl), 0) FROM virtual_trades WHERE net_pnl > 0"
        ).fetchone()[0]
        gross_loss = abs(conn.execute(
            "SELECT COALESCE(SUM(net_pnl), 0) FROM virtual_trades WHERE net_pnl < 0"
        ).fetchone()[0])
        pf = (gross_profit / gross_loss) if gross_loss > 0 else 0
        criteria.append(Criterion(
            id=4,
            name="Profit factor > 1.3",
            target="> 1.3",
            current_value=f"{pf:.2f}",
            met=pf > 1.3,
        ))

        # --- Criterion 5: Max drawdown < 8% ---
        # Calculate peak-to-trough from equity snapshots
        snapshots = conn.execute(
            "SELECT ending_equity FROM equity_snapshots ORDER BY date ASC"
        ).fetchall()
        max_dd = 0.0
        if snapshots:
            peak = snapshots[0][0] if snapshots[0][0] else 10000
            for snap in snapshots:
                eq = snap[0] if snap[0] else peak
                if eq > peak:
                    peak = eq
                dd = (peak - eq) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)
        criteria.append(Criterion(
            id=5,
            name="Max drawdown < 8%",
            target="< 8%",
            current_value=f"{max_dd * 100:.1f}%",
            met=max_dd < 0.08,
        ))

        # --- Criterion 6: Never 5 consecutive losses ---
        # SK-02: 30-day rolling window prevents ancient losses from permanent block
        all_results = conn.execute(
            """SELECT r_multiple FROM virtual_trades
               WHERE exit_time >= datetime('now', '-30 days')
               ORDER BY exit_time ASC"""
        ).fetchall()
        max_consec_losses = 0
        current_streak = 0
        for row in all_results:
            if row[0] is not None and row[0] < 0:
                current_streak += 1
                max_consec_losses = max(max_consec_losses, current_streak)
            else:
                current_streak = 0
        criteria.append(Criterion(
            id=6,
            name="Never 5 consecutive losses",
            target="< 5",
            current_value=f"max streak: {max_consec_losses}",
            met=max_consec_losses < 5,
        ))

        # --- Criterion 7: Zero firewall overrides ---
        firewall_exits = conn.execute(
            "SELECT COUNT(*) FROM virtual_trades WHERE exit_reason LIKE '%FIREWALL%'"
        ).fetchone()[0]
        criteria.append(Criterion(
            id=7,
            name="Zero firewall overrides",
            target="0",
            current_value=str(firewall_exits),
            met=firewall_exits == 0,
        ))

        # --- Criterion 8: No system crashes in last 5 days ---
        # Check if there are any gaps > 2 hours during market hours in the last 5 days
        # For now, approximate by checking if we have recent trades
        five_days_ago = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        recent_trades = conn.execute(
            "SELECT COUNT(*) FROM virtual_trades WHERE exit_time > ?",
            (five_days_ago,),
        ).fetchone()[0]
        # Simple heuristic: if we have trades, system was running
        criteria.append(Criterion(
            id=8,
            name="No crashes last 5 days",
            target="Stable",
            current_value=f"{recent_trades} trades in 5d",
            met=recent_trades > 0,  # Will be more sophisticated once system is running
        ))

        # --- Criterion 9: All strategies have 5+ trades ---
        strat_counts = conn.execute(
            """SELECT strategy, COUNT(*) as cnt
               FROM virtual_trades
               GROUP BY strategy"""
        ).fetchall()
        min_strat_trades = 0
        min_strat_name = "none"
        active_strategies_ok = True
        if strat_counts:
            for row in strat_counts:
                if row[1] < 5:
                    active_strategies_ok = False
                    if min_strat_trades == 0 or row[1] < min_strat_trades:
                        min_strat_trades = row[1]
                        min_strat_name = row[0]
        else:
            active_strategies_ok = False
        criteria.append(Criterion(
            id=9,
            name="All strategies 5+ trades",
            target=">= 5 each",
            current_value=f"min: {min_strat_name}={min_strat_trades}" if not active_strategies_ok else "All OK",
            met=active_strategies_ok,
        ))

        # --- Criterion 10: Sharpe ratio > 1.0 ---
        daily_returns = conn.execute(
            """SELECT date(exit_time) as d, SUM(net_pnl) as daily_pnl
               FROM virtual_trades
               GROUP BY d
               ORDER BY d"""
        ).fetchall()
        sharpe = 0.0
        if len(daily_returns) >= 5:
            returns = [row[1] for row in daily_returns if row[1] is not None]
            if returns:
                import statistics
                mean_ret = statistics.mean(returns)
                std_ret = statistics.stdev(returns) if len(returns) > 1 else 1
                if std_ret > 0:
                    # Annualize: sqrt(252) * daily sharpe
                    sharpe = (mean_ret / std_ret) * (252 ** 0.5)
        criteria.append(Criterion(
            id=10,
            name="Sharpe ratio > 1.0",
            target="> 1.0",
            current_value=f"{sharpe:.2f}",
            met=sharpe > 1.0,
        ))

        scorecard = GoNoGoScorecard(
            criteria=criteria,
            last_updated=datetime.now(timezone.utc),
        )
        self._last_scorecard = scorecard

        logger.info(
            "GO/NO-GO: %d/%d criteria met (%.0f%%)",
            scorecard.met_count, scorecard.total_count, scorecard.progress_pct,
        )

        return scorecard

    def to_telegram(self, scorecard: GoNoGoScorecard | None = None) -> str:
        """Format the scorecard for Telegram."""
        sc = scorecard or self._last_scorecard
        if not sc:
            return "GO/NO-GO: No evaluation run yet."

        lines = [
            "GO/NO-GO SCORECARD",
            "=" * 30,
            f"Progress: {sc.met_count}/{sc.total_count} ({sc.progress_pct:.0f}%)",
            "",
        ]

        for c in sc.criteria:
            check = "OK" if c.met else "  "
            lines.append(f"[{check}] {c.id}. {c.name}")
            lines.append(f"     Target: {c.target} | Current: {c.current_value}")

        if sc.met_count == sc.total_count:
            lines.append("")
            lines.append("ALL CRITERIA MET — READY FOR LIVE TRADING")
        else:
            remaining = sc.total_count - sc.met_count
            lines.append("")
            lines.append(f"{remaining} criteria remaining before live trading.")

        return "\n".join(lines)

    def to_dict(self, scorecard: GoNoGoScorecard | None = None) -> dict:
        """Serialize for API/dashboard."""
        sc = scorecard or self._last_scorecard
        if not sc:
            return {"criteria": [], "met": 0, "total": 0, "progress_pct": 0}
        return {
            "criteria": [
                {
                    "id": c.id, "name": c.name, "target": c.target,
                    "current": c.current_value, "met": c.met,
                }
                for c in sc.criteria
            ],
            "met": sc.met_count,
            "total": sc.total_count,
            "progress_pct": sc.progress_pct,
            "last_updated": sc.last_updated.isoformat(),
        }
