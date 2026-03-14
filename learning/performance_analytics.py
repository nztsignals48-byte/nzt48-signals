"""
NZT-48 Trading System — Institutional Performance Analytics
Computes hedge-fund-grade metrics for paper trading review.

Metrics computed:
- Sharpe Ratio (annualized, risk-free = 4.5%)
- Sortino Ratio (downside deviation only)
- Calmar Ratio (return / max drawdown)
- Profit Factor (gross profit / gross loss)
- Expectancy per trade (avg_win × win_rate - avg_loss × loss_rate)
- Expectancy in R (same, using R-multiples)
- Win/loss streaks (current + max)
- Recovery Factor (net profit / max drawdown)
- Average Winner vs Average Loser
- Payoff Ratio (avg_win / avg_loss)
- Trade frequency analysis (trades/day, trades/week)
- Time-of-day performance breakdown
- Strategy correlation matrix
- Equity curve statistics (CAGR, max drawdown %, drawdown duration)
"""

from __future__ import annotations

import logging
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("nzt48.learning.performance")


@dataclass
class PerformanceReport:
    """Complete performance analytics report."""
    # Period
    period_start: str = ""
    period_end: str = ""
    trading_days: int = 0

    # Core P&L
    total_trades: int = 0
    total_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0

    # Win/Loss
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    win_rate: float = 0.0

    # Ratios
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    ulcer_index: float = 0.0           # Martin & McCann (1989) downside volatility
    ulcer_performance_index: float = 0.0  # UPI = excess return / ulcer index
    recovery_factor: float = 0.0
    payoff_ratio: float = 0.0

    # Expectancy
    expectancy_dollars: float = 0.0
    expectancy_r: float = 0.0
    avg_winner: float = 0.0
    avg_loser: float = 0.0
    avg_winner_r: float = 0.0
    avg_loser_r: float = 0.0
    largest_winner: float = 0.0
    largest_loser: float = 0.0
    largest_winner_r: float = 0.0
    largest_loser_r: float = 0.0

    # Streaks
    current_streak: int = 0  # positive = wins, negative = losses
    max_win_streak: int = 0
    max_loss_streak: int = 0

    # Drawdown
    max_drawdown_pct: float = 0.0
    max_drawdown_dollars: float = 0.0
    max_drawdown_duration_days: int = 0
    current_drawdown_pct: float = 0.0

    # Equity curve
    starting_equity: float = 0.0
    ending_equity: float = 0.0
    total_return_pct: float = 0.0
    cagr: float = 0.0

    # Execution quality
    avg_entry_quality: float = 0.0
    avg_exit_quality: float = 0.0
    avg_slippage: float = 0.0
    total_commissions: float = 0.0
    total_slippage: float = 0.0

    # Time analysis
    avg_hold_minutes: float = 0.0
    avg_winner_hold_minutes: float = 0.0
    avg_loser_hold_minutes: float = 0.0
    trades_per_day: float = 0.0

    # Per-strategy breakdown
    strategy_stats: dict[str, dict] = field(default_factory=dict)

    # Per-exit-reason breakdown
    exit_reason_stats: dict[str, dict] = field(default_factory=dict)

    # Per-regime breakdown
    regime_stats: dict[str, dict] = field(default_factory=dict)

    def to_telegram(self) -> str:
        """Format as Telegram message."""
        lines = [
            "PERFORMANCE REPORT",
            "=" * 30,
            f"Period: {self.period_start} to {self.period_end} ({self.trading_days} days)",
            "",
            f"Trades: {self.total_trades} | W/L: {self.wins}/{self.losses} | WR: {self.win_rate:.1f}%",
            f"P&L: ${self.total_pnl:+,.2f} ({self.total_return_pct:+.2f}%)",
            "",
            "-- RATIOS --",
            f"Sharpe: {self.sharpe_ratio:.2f} | Sortino: {self.sortino_ratio:.2f}",
            f"Calmar: {self.calmar_ratio:.2f} | PF: {self.profit_factor:.2f}",
            f"Ulcer: {self.ulcer_index:.2f} | UPI: {self.ulcer_performance_index:.2f}",
            f"Recovery: {self.recovery_factor:.2f} | Payoff: {self.payoff_ratio:.2f}",
            "",
            "-- EXPECTANCY --",
            f"Per trade: ${self.expectancy_dollars:+.2f} ({self.expectancy_r:+.2f}R)",
            f"Avg Win: ${self.avg_winner:+.2f} ({self.avg_winner_r:+.2f}R)",
            f"Avg Loss: ${self.avg_loser:.2f} ({self.avg_loser_r:.2f}R)",
            f"Best: {self.largest_winner_r:+.1f}R | Worst: {self.largest_loser_r:.1f}R",
            "",
            "-- STREAKS --",
            f"Current: {self.current_streak:+d} | Max Win: {self.max_win_streak} | Max Loss: {self.max_loss_streak}",
            "",
            "-- DRAWDOWN --",
            f"Max DD: {self.max_drawdown_pct:.2f}% (${self.max_drawdown_dollars:,.0f})",
            f"DD Duration: {self.max_drawdown_duration_days} days",
            f"Current DD: {self.current_drawdown_pct:.2f}%",
            "",
            "-- EXECUTION --",
            f"Avg Hold: {self.avg_hold_minutes:.0f}m (W: {self.avg_winner_hold_minutes:.0f}m / L: {self.avg_loser_hold_minutes:.0f}m)",
            f"Slippage: ${self.total_slippage:.2f} | Commission: ${self.total_commissions:.2f}",
            f"Entry Quality: {self.avg_entry_quality:.0f}/100 | Exit Quality: {self.avg_exit_quality:.0f}/100",
        ]

        # Strategy breakdown
        if self.strategy_stats:
            lines.append("")
            lines.append("-- BY STRATEGY --")
            for strat, s in sorted(self.strategy_stats.items(), key=lambda x: x[1].get("net_pnl", 0), reverse=True):
                wr = s.get("win_rate", 0)
                pnl = s.get("net_pnl", 0)
                trades = s.get("trades", 0)
                avg_r = s.get("avg_r", 0)
                lines.append(f"  {strat}: {trades}T WR={wr:.0f}% PnL=${pnl:+.0f} AvgR={avg_r:+.2f}")

        # Exit reason breakdown
        if self.exit_reason_stats:
            lines.append("")
            lines.append("-- BY EXIT REASON --")
            for reason, s in sorted(self.exit_reason_stats.items(), key=lambda x: x[1].get("count", 0), reverse=True):
                count = s.get("count", 0)
                avg_r = s.get("avg_r", 0)
                lines.append(f"  {reason}: {count}x avg={avg_r:+.2f}R")

        return "\n".join(lines)


class PerformanceAnalytics:
    """Computes institutional-grade performance metrics from trade history."""

    RISK_FREE_RATE = 0.045  # 4.5% annualized

    def __init__(self) -> None:
        pass

    def compute(self, conn: sqlite3.Connection, days: int = 30) -> PerformanceReport:
        """Compute full performance report from virtual_trades table."""
        report = PerformanceReport()

        trades = conn.execute(
            """SELECT * FROM virtual_trades
               WHERE exit_time > datetime('now', ?)
               ORDER BY exit_time ASC""",
            (f"-{days} days",)
        ).fetchall()

        if not trades:
            return report

        report.period_start = trades[0]["entry_time"][:10] if trades[0]["entry_time"] else ""
        report.period_end = trades[-1]["exit_time"][:10] if trades[-1]["exit_time"] else ""
        report.total_trades = len(trades)

        # Compute unique trading days
        trade_dates = set()
        for t in trades:
            if t["exit_time"]:
                trade_dates.add(t["exit_time"][:10])
        report.trading_days = max(len(trade_dates), 1)
        report.trades_per_day = report.total_trades / report.trading_days

        # Core P&L
        pnls = []
        r_multiples = []
        daily_pnls = defaultdict(float)

        for t in trades:
            net = t["net_pnl"] or 0.0
            r = t["r_multiple"] or 0.0
            pnls.append(net)
            r_multiples.append(r)

            exit_date = (t["exit_time"] or "")[:10]
            if exit_date:
                daily_pnls[exit_date] += net

            if net > 0:
                report.wins += 1
                report.gross_profit += net
            elif net < 0:
                report.losses += 1
                report.gross_loss += abs(net)
            else:
                report.breakeven += 1

        report.total_pnl = sum(pnls)
        report.win_rate = (report.wins / report.total_trades * 100) if report.total_trades > 0 else 0

        # Profit Factor
        report.profit_factor = (report.gross_profit / report.gross_loss) if report.gross_loss > 0 else float('inf') if report.gross_profit > 0 else 0

        # Average winner/loser
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p < 0]
        winner_rs = [r for r in r_multiples if r > 0]
        loser_rs = [r for r in r_multiples if r < 0]

        report.avg_winner = sum(winners) / len(winners) if winners else 0
        report.avg_loser = sum(losers) / len(losers) if losers else 0
        report.avg_winner_r = sum(winner_rs) / len(winner_rs) if winner_rs else 0
        report.avg_loser_r = sum(loser_rs) / len(loser_rs) if loser_rs else 0
        report.largest_winner = max(pnls) if pnls else 0
        report.largest_loser = min(pnls) if pnls else 0
        report.largest_winner_r = max(r_multiples) if r_multiples else 0
        report.largest_loser_r = min(r_multiples) if r_multiples else 0

        # Payoff Ratio
        report.payoff_ratio = (report.avg_winner / abs(report.avg_loser)) if report.avg_loser != 0 else float('inf') if report.avg_winner > 0 else 0

        # Expectancy
        wr = report.wins / report.total_trades if report.total_trades > 0 else 0
        lr = report.losses / report.total_trades if report.total_trades > 0 else 0
        report.expectancy_dollars = (report.avg_winner * wr) + (report.avg_loser * lr)
        report.expectancy_r = (report.avg_winner_r * wr) + (report.avg_loser_r * lr)

        # Sharpe Ratio (annualized from daily returns)
        daily_returns = list(daily_pnls.values())
        if len(daily_returns) >= 2:
            mean_daily = sum(daily_returns) / len(daily_returns)
            std_daily = math.sqrt(sum((r - mean_daily) ** 2 for r in daily_returns) / (len(daily_returns) - 1))
            rf_daily = self.RISK_FREE_RATE / 252
            if std_daily > 0:
                report.sharpe_ratio = ((mean_daily - rf_daily) / std_daily) * math.sqrt(252)

            # Sortino (downside deviation only)
            downside = [r for r in daily_returns if r < rf_daily]
            if downside:
                downside_dev = math.sqrt(sum((r - rf_daily) ** 2 for r in downside) / len(downside))
                if downside_dev > 0:
                    report.sortino_ratio = ((mean_daily - rf_daily) / downside_dev) * math.sqrt(252)

        # Drawdown calculation
        equity_curve = []
        running_equity = 0.0
        peak_equity = 0.0
        max_dd = 0.0
        max_dd_dollars = 0.0
        dd_start = None
        max_dd_duration = 0
        current_dd_start = None

        for date in sorted(daily_pnls.keys()):
            running_equity += daily_pnls[date]
            equity_curve.append((date, running_equity))

            if running_equity > peak_equity:
                peak_equity = running_equity
                if current_dd_start:
                    # Calculate duration of this drawdown
                    duration = len([d for d in sorted(daily_pnls.keys()) if current_dd_start <= d <= date])
                    max_dd_duration = max(max_dd_duration, duration)
                current_dd_start = None
            else:
                if current_dd_start is None:
                    current_dd_start = date
                dd = peak_equity - running_equity
                if dd > max_dd_dollars:
                    max_dd_dollars = dd

        report.max_drawdown_dollars = max_dd_dollars
        report.max_drawdown_duration_days = max_dd_duration

        # Get equity for drawdown pct
        equity_snap = conn.execute(
            "SELECT starting_equity FROM equity_snapshots ORDER BY date DESC LIMIT 1"
        ).fetchone()
        starting_eq = equity_snap["starting_equity"] if equity_snap else 100000
        report.starting_equity = starting_eq
        report.ending_equity = starting_eq + report.total_pnl
        report.max_drawdown_pct = (max_dd_dollars / starting_eq * 100) if starting_eq > 0 else 0
        report.current_drawdown_pct = ((peak_equity - running_equity) / starting_eq * 100) if starting_eq > 0 else 0
        report.total_return_pct = (report.total_pnl / starting_eq * 100) if starting_eq > 0 else 0

        # Calmar
        if report.max_drawdown_pct > 0:
            annualized_return = report.total_return_pct * (252 / max(report.trading_days, 1))
            report.calmar_ratio = annualized_return / report.max_drawdown_pct

        # Ulcer Index — Martin & McCann (1989)
        # Measures the depth and duration of drawdowns (volatility of the underwater equity).
        # UI = sqrt(mean(drawdown_pct²)) over the equity curve.
        # Lower is better; >10 signals severe drawdown stress.
        if equity_curve and starting_eq > 0:
            peak = starting_eq
            dd_pcts_sq = []
            for _, eq_val in equity_curve:
                total_eq = starting_eq + eq_val
                if total_eq > peak:
                    peak = total_eq
                dd_pct = ((peak - total_eq) / peak * 100) if peak > 0 else 0.0
                dd_pcts_sq.append(dd_pct ** 2)
            if dd_pcts_sq:
                report.ulcer_index = math.sqrt(sum(dd_pcts_sq) / len(dd_pcts_sq))
                # UPI (Ulcer Performance Index) = excess return / ulcer index
                if report.ulcer_index > 0:
                    excess_return = annualized_return - (self.RISK_FREE_RATE * 100) if report.max_drawdown_pct > 0 else report.total_return_pct * (252 / max(report.trading_days, 1)) - (self.RISK_FREE_RATE * 100)
                    report.ulcer_performance_index = excess_return / report.ulcer_index

        # Recovery Factor
        if max_dd_dollars > 0:
            report.recovery_factor = report.total_pnl / max_dd_dollars

        # CAGR
        if report.trading_days > 0 and starting_eq > 0:
            years = report.trading_days / 252
            if years > 0:
                report.cagr = ((report.ending_equity / starting_eq) ** (1 / years) - 1) * 100

        # Streaks
        current_streak = 0
        max_win_streak = 0
        max_loss_streak = 0
        temp_win = 0
        temp_loss = 0
        for p in pnls:
            if p > 0:
                temp_win += 1
                temp_loss = 0
                max_win_streak = max(max_win_streak, temp_win)
            elif p < 0:
                temp_loss += 1
                temp_win = 0
                max_loss_streak = max(max_loss_streak, temp_loss)
            else:
                temp_win = 0
                temp_loss = 0

        # Current streak from the end
        for p in reversed(pnls):
            if p > 0:
                if current_streak < 0:
                    break
                current_streak += 1
            elif p < 0:
                if current_streak > 0:
                    break
                current_streak -= 1
            else:
                break

        report.current_streak = current_streak
        report.max_win_streak = max_win_streak
        report.max_loss_streak = max_loss_streak

        # Execution quality
        entry_quals = [t["entry_quality"] for t in trades if t["entry_quality"]]
        exit_quals = [t["exit_quality"] for t in trades if t["exit_quality"]]
        slippages = [t["slippage"] for t in trades if t["slippage"]]
        commissions = [t["commission"] for t in trades if t["commission"]]
        durations = [t["duration_minutes"] for t in trades if t["duration_minutes"]]

        report.avg_entry_quality = sum(entry_quals) / len(entry_quals) if entry_quals else 0
        report.avg_exit_quality = sum(exit_quals) / len(exit_quals) if exit_quals else 0
        report.avg_slippage = sum(slippages) / len(slippages) if slippages else 0
        report.total_slippage = sum(slippages) if slippages else 0
        report.total_commissions = sum(commissions) if commissions else 0
        report.avg_hold_minutes = sum(durations) / len(durations) if durations else 0

        winner_durations = [t["duration_minutes"] for t in trades if (t["net_pnl"] or 0) > 0 and t["duration_minutes"]]
        loser_durations = [t["duration_minutes"] for t in trades if (t["net_pnl"] or 0) < 0 and t["duration_minutes"]]
        report.avg_winner_hold_minutes = sum(winner_durations) / len(winner_durations) if winner_durations else 0
        report.avg_loser_hold_minutes = sum(loser_durations) / len(loser_durations) if loser_durations else 0

        # Per-strategy breakdown
        strat_trades = defaultdict(list)
        for t in trades:
            strat_trades[t["strategy"] or "UNKNOWN"].append(t)

        for strat, strat_list in strat_trades.items():
            s_pnls = [t["net_pnl"] or 0 for t in strat_list]
            s_rs = [t["r_multiple"] or 0 for t in strat_list]
            s_wins = sum(1 for p in s_pnls if p > 0)
            s_losses = sum(1 for p in s_pnls if p < 0)
            s_gross_profit = sum(p for p in s_pnls if p > 0)
            s_gross_loss = abs(sum(p for p in s_pnls if p < 0))
            report.strategy_stats[strat] = {
                "trades": len(strat_list),
                "wins": s_wins,
                "losses": s_losses,
                "net_pnl": sum(s_pnls),
                "avg_r": sum(s_rs) / len(s_rs) if s_rs else 0,
                "win_rate": (s_wins / len(strat_list) * 100) if strat_list else 0,
                "profit_factor": (s_gross_profit / s_gross_loss) if s_gross_loss > 0 else float('inf') if s_gross_profit > 0 else 0,
                "best_r": max(s_rs) if s_rs else 0,
                "worst_r": min(s_rs) if s_rs else 0,
            }

        # Per-exit-reason breakdown
        exit_trades = defaultdict(list)
        for t in trades:
            exit_trades[t["exit_reason"] or "UNKNOWN"].append(t)

        for reason, reason_list in exit_trades.items():
            r_vals = [t["r_multiple"] or 0 for t in reason_list]
            report.exit_reason_stats[reason] = {
                "count": len(reason_list),
                "avg_r": sum(r_vals) / len(r_vals) if r_vals else 0,
                "avg_pnl": sum(t["net_pnl"] or 0 for t in reason_list) / len(reason_list) if reason_list else 0,
            }

        # Per-regime breakdown
        regime_trades = defaultdict(list)
        for t in trades:
            regime_trades[t["regime_at_entry"] or "UNKNOWN"].append(t)

        for regime, regime_list in regime_trades.items():
            r_pnls = [t["net_pnl"] or 0 for t in regime_list]
            r_rs = [t["r_multiple"] or 0 for t in regime_list]
            r_wins = sum(1 for p in r_pnls if p > 0)
            report.regime_stats[regime] = {
                "trades": len(regime_list),
                "wins": r_wins,
                "win_rate": (r_wins / len(regime_list) * 100) if regime_list else 0,
                "net_pnl": sum(r_pnls),
                "avg_r": sum(r_rs) / len(r_rs) if r_rs else 0,
            }

        return report

    def compute_strategy_daily_stats(self, conn: sqlite3.Connection, date: str = None) -> dict[str, dict]:
        """Compute per-strategy stats for a specific date. Returns dict of strategy -> stats."""
        date_clause = "date(exit_time) = date('now')" if date is None else "date(exit_time) = ?"
        params = [] if date is None else [date]

        trades = conn.execute(
            f"SELECT * FROM virtual_trades WHERE {date_clause} ORDER BY exit_time",
            params
        ).fetchall()

        stats = defaultdict(lambda: {
            "trades": 0, "wins": 0, "losses": 0,
            "gross_pnl": 0.0, "net_pnl": 0.0,
            "r_multiples": [], "durations": [],
        })

        for t in trades:
            strat = t["strategy"] or "UNKNOWN"
            s = stats[strat]
            net = t["net_pnl"] or 0
            r = t["r_multiple"] or 0
            s["trades"] += 1
            s["net_pnl"] += net
            if net > 0:
                s["wins"] += 1
                s["gross_pnl"] += net
            elif net < 0:
                s["losses"] += 1
                s["gross_pnl"] += net  # This is gross, include losses too
            s["r_multiples"].append(r)
            if t["duration_minutes"]:
                s["durations"].append(t["duration_minutes"])

        result = {}
        for strat, s in stats.items():
            rs = s["r_multiples"]
            result[strat] = {
                "date": date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "strategy": strat,
                "trades": s["trades"],
                "wins": s["wins"],
                "losses": s["losses"],
                "gross_pnl": s["gross_pnl"],
                "net_pnl": s["net_pnl"],
                "avg_r": sum(rs) / len(rs) if rs else 0,
                "best_r": max(rs) if rs else 0,
                "worst_r": min(rs) if rs else 0,
                "win_rate": (s["wins"] / s["trades"] * 100) if s["trades"] > 0 else 0,
                "profit_factor": (sum(r for r in rs if r > 0) / abs(sum(r for r in rs if r < 0))) if any(r < 0 for r in rs) else 0,
                "avg_duration_minutes": sum(s["durations"]) / len(s["durations"]) if s["durations"] else 0,
            }

        return result
