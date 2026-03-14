"""
NZT-48 Report Generator + Monte Carlo Simulator
Auto-generates daily (21:00 GMT), weekly (Sunday 20:00), monthly (1st) reports.
Sends to Telegram. Includes equity curve, System IQ trend, Monte Carlo.
"""
from __future__ import annotations
import logging
import random
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.reports")


class MonteCarloSimulator:
    """10,000-iteration Monte Carlo from actual R-distribution."""

    def __init__(self, iterations: int = 10000):
        self.iterations = iterations

    @staticmethod
    def _kelly_criterion(r_multiples: list[float]) -> float:
        """Calculate optimal Kelly fraction from R-multiple distribution.

        Kelly f* = W - (1 - W) / R
        where W = win rate, R = avg_win / avg_loss ratio
        """
        winners = [r for r in r_multiples if r > 0]
        losers = [r for r in r_multiples if r <= 0]

        if not losers:
            return 0.5  # No losses yet, use conservative estimate

        win_count = len(winners)
        total_count = len(r_multiples)
        win_rate = win_count / total_count

        avg_win = sum(winners) / max(1, len(winners))
        avg_loss = abs(sum(losers) / len(losers))

        if avg_loss == 0:
            return 0.5

        win_loss_ratio = avg_win / avg_loss
        kelly = win_rate - (1 - win_rate) / win_loss_ratio

        return kelly

    def simulate(
        self,
        r_multiples: list[float],
        risk_per_trade: float = 0.0075,
        starting_equity: float = 20000,
        trades_per_day: float = 2.5,
        days: int = 252,
        seed: int | None = None,
    ) -> dict:
        """Run Monte Carlo simulation.

        Args:
            r_multiples: Historical R-multiple distribution
            risk_per_trade: Fraction of equity risked per trade
            starting_equity: Starting balance
            trades_per_day: Average trades per day
            days: Trading days to simulate
            seed: Random seed for reproducibility (None for random)

        Returns:
            Dict with percentiles, probabilities, risk metrics
        """
        if len(r_multiples) < 10:
            return {"error": "Need at least 10 trades for Monte Carlo"}

        rng = random.Random(seed)
        total_trades = int(trades_per_day * days)
        final_equities = []
        max_drawdowns = []
        ruin_count = 0

        for _ in range(self.iterations):
            equity = starting_equity
            peak = equity
            max_dd = 0.0

            for _ in range(total_trades):
                # Sample random R-multiple from actual distribution
                r = r_multiples[rng.randint(0, len(r_multiples) - 1)]
                pnl = equity * risk_per_trade * r
                equity += pnl

                # Track drawdown
                if equity > peak:
                    peak = equity
                dd = (peak - equity) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)

                # Ruin check
                if equity <= starting_equity * 0.5:
                    ruin_count += 1
                    break

            final_equities.append(equity)
            max_drawdowns.append(max_dd)

        final_equities.sort()
        max_drawdowns.sort()
        n = len(final_equities)

        def annualised_return(final_eq):
            """Properly annualise: ((final/start) ^ (252/days) - 1) * 100."""
            if starting_equity <= 0 or final_eq <= 0:
                return 0
            return ((final_eq / starting_equity) ** (252 / days) - 1) * 100

        # Probability of hitting targets
        targets = {
            "20%": sum(1 for e in final_equities if annualised_return(e) >= 20) / n * 100,
            "30%": sum(1 for e in final_equities if annualised_return(e) >= 30) / n * 100,
            "100%": sum(1 for e in final_equities if annualised_return(e) >= 100) / n * 100,
            "200%": sum(1 for e in final_equities if annualised_return(e) >= 200) / n * 100,
        }

        kelly_est = self._kelly_criterion(r_multiples)

        return {
            "iterations": self.iterations,
            "trades_simulated": total_trades,
            "starting_equity": starting_equity,
            "median_final": round(final_equities[n // 2], 2),
            "p10_final": round(final_equities[n // 10], 2),
            "p25_final": round(final_equities[n // 4], 2),
            "p75_final": round(final_equities[3 * n // 4], 2),
            "p90_final": round(final_equities[9 * n // 10], 2),
            "median_return_pct": round(annualised_return(final_equities[n // 2]), 1),
            "p10_return_pct": round(annualised_return(final_equities[n // 10]), 1),
            "p90_return_pct": round(annualised_return(final_equities[9 * n // 10]), 1),
            "target_probabilities": targets,
            "worst_case_dd": round(max_drawdowns[9 * n // 10] * 100, 1),
            "median_dd": round(max_drawdowns[n // 2] * 100, 1),
            "risk_of_ruin_pct": round(ruin_count / self.iterations * 100, 2),
            "optimal_kelly_est": round(kelly_est, 4),
        }


class ReportGenerator:
    """Generates daily, weekly, monthly reports for Telegram delivery."""

    def __init__(self):
        self.monte_carlo = MonteCarloSimulator()

    def daily_report(self, data: dict) -> str:
        """Generate daily report (21:00 GMT).

        data should include:
        - trades_closed: int
        - wins, losses: int
        - net_pnl, net_pnl_pct: float
        - best_trade: dict (ticker, r, strategy)
        - worst_trade: dict
        - regime_breakdown: dict (regime -> % time)
        - signals_generated, signals_qualified, signals_rejected: int
        - rejection_reasons: dict
        - firewall_triggers: list
        - indicator_top5, indicator_bottom5: list
        - system_iq: float
        - equity: float
        - annualised_pace: float
        """
        trades = data.get("trades_closed", 0)
        wins = data.get("wins", 0)
        losses = data.get("losses", 0)
        wr = wins / trades * 100 if trades > 0 else 0
        pnl = data.get("net_pnl", 0)
        pnl_pct = data.get("net_pnl_pct", 0)
        equity = data.get("equity", 20000)
        iq = data.get("system_iq", 0)
        pace = data.get("annualised_pace", 0)

        best = data.get("best_trade", {})
        worst = data.get("worst_trade", {})

        lines = [
            "=== NZT-48 DAILY REPORT ===",
            f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            "",
            f"Trades: {trades} ({wins}W / {losses}L) WR: {wr:.0f}%",
            f"Net P&L: ${pnl:+,.2f} ({pnl_pct:+.2f}%)",
            f"Equity: ${equity:,.2f}",
            "",
        ]

        if best:
            lines.append(f"Best: {best.get('ticker', '')} {best.get('strategy', '')} +{best.get('r', 0):.2f}R")
        if worst:
            lines.append(f"Worst: {worst.get('ticker', '')} {worst.get('strategy', '')} {worst.get('r', 0):.2f}R")

        lines.append("")

        # Regime breakdown
        regime = data.get("regime_breakdown", {})
        if regime:
            lines.append("Regime Distribution:")
            for r, pct in sorted(regime.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"  {r}: {pct:.0f}%")

        # Signals
        lines.extend([
            "",
            f"Signals: {data.get('signals_generated', 0)} generated → {data.get('signals_qualified', 0)} qualified → {data.get('signals_rejected', 0)} rejected",
        ])

        # System IQ
        lines.extend([
            "",
            f"System IQ: {iq:.1f}",
            f"Annualised Pace: {pace:.0f}% (target: 200%+)",
            f"Distance to 200%: {max(0, 200 - pace):.0f}% gap",
        ])

        return "\n".join(lines)

    def weekly_report(self, data: dict) -> str:
        """Generate weekly report (Sunday 20:00 GMT)."""
        lines = [
            "=== NZT-48 WEEKLY REPORT ===",
            f"Week ending: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            "",
            f"Week P&L: ${data.get('week_pnl', 0):+,.2f} ({data.get('week_pnl_pct', 0):+.2f}%)",
            f"Trades: {data.get('total_trades', 0)} ({data.get('wins', 0)}W / {data.get('losses', 0)}L)",
            f"Equity: ${data.get('equity', 20000):,.2f}",
            "",
            "--- Strategy Leaderboard ---",
        ]

        for s in data.get("strategy_leaderboard", [])[:5]:
            lines.append(
                f"  {s['strategy']}: {s.get('expectancy', 0):.3f}R exp, "
                f"{s.get('trades', 0)} trades, {s.get('win_rate', 0):.0f}% WR"
            )

        lines.extend([
            "",
            "--- Ticker Leaderboard ---",
        ])
        for t in data.get("ticker_leaderboard", [])[:5]:
            lines.append(f"  {t['ticker']}: {t.get('pnl', 0):+.2f}R total, {t.get('trades', 0)} trades")

        lines.extend([
            "",
            "--- Bot Comparison ---",
        ])
        for b in data.get("bot_comparison", []):
            lines.append(f"  {b['bot']}: ${b.get('pnl', 0):+,.2f} ({b.get('trades', 0)} trades)")

        # Learning updates
        lines.extend(["", "--- Learning Updates ---"])
        for update in data.get("learning_updates", []):
            lines.append(f"  {update}")

        # Monte Carlo
        mc = data.get("monte_carlo", {})
        if mc:
            lines.extend([
                "",
                "--- Monte Carlo (10k iterations) ---",
                f"  Median Return: {mc.get('median_return_pct', 0):.0f}%",
                f"  P(200%+): {mc.get('target_probabilities', {}).get('200%', 0):.1f}%",
                f"  Risk of Ruin: {mc.get('risk_of_ruin_pct', 0):.2f}%",
                f"  Worst-case DD: {mc.get('worst_case_dd', 0):.1f}%",
            ])

        lines.extend([
            "",
            f"System IQ: {data.get('system_iq', 0):.1f} ({data.get('iq_change_week', 0):+.1f} this week)",
            f"Annualised Pace: {data.get('annualised_pace', 0):.0f}%",
        ])

        return "\n".join(lines)

    def monthly_report(self, data: dict) -> str:
        """Generate monthly report (1st of month)."""
        lines = [
            "=== NZT-48 MONTHLY REPORT ===",
            f"Month: {data.get('month', datetime.now(timezone.utc).strftime('%B %Y'))}",
            "",
            f"Month P&L: ${data.get('month_pnl', 0):+,.2f} ({data.get('month_pnl_pct', 0):+.2f}%)",
            f"Total Equity: ${data.get('equity', 20000):,.2f}",
            f"Total Return: {data.get('total_return_pct', 0):+.1f}%",
            "",
            "--- Go/No-Go Assessment (Section 45) ---",
            f"  Virtual Trades: {data.get('total_trades', 0)} (need 50+)",
            f"  Win Rate: {data.get('win_rate', 0):.0f}% (need >50%)",
            f"  Profit Factor: {data.get('profit_factor', 0):.2f} (need >1.3)",
            f"  Max Drawdown: {data.get('max_drawdown', 0):.1f}% (need <8%)",
        ]

        # Go/No-Go verdict
        go = (
            data.get("total_trades", 0) >= 50 and
            data.get("win_rate", 0) > 50 and
            data.get("profit_factor", 0) > 1.3 and
            abs(data.get("max_drawdown", 0)) < 8
        )
        lines.append(f"  VERDICT: {'GO — Ready for live' if go else 'NO-GO — Continue paper trading'}")

        lines.extend([
            "",
            f"System IQ Trend: {data.get('iq_trend', 'N/A')}",
            f"ISA Compounding: ${data.get('isa_value', 0):,.2f} of 10-year projection",
            f"Path to 200%: {data.get('pace_to_200', 'N/A')}",
        ])

        return "\n".join(lines)

    def pace_report(self, data: dict) -> str:
        """Generate /pace command: current annualised return vs 200% target."""
        equity = data.get("equity", 20000)
        starting = data.get("starting_equity", 20000)
        days_running = max(data.get("days_running", 1), 1)

        total_return = (equity - starting) / starting
        annualised = ((1 + total_return) ** (252 / days_running) - 1) * 100

        lines = [
            "=== NZT-48 PACE REPORT ===",
            f"Days Running: {days_running}",
            f"Starting Equity: ${starting:,.2f}",
            f"Current Equity: ${equity:,.2f}",
            f"Total Return: {total_return*100:+.1f}%",
            f"Annualised Pace: {annualised:.0f}%",
            f"Target: 200%+",
            f"Gap: {max(0, 200 - annualised):.0f}%",
            "",
        ]

        if annualised >= 200:
            lines.append("STATUS: ON TARGET")
        elif annualised >= 100:
            lines.append("STATUS: STRONG — above 100%, approaching target")
        elif annualised >= 50:
            lines.append("STATUS: BUILDING — learning engine compounding")
        else:
            lines.append("STATUS: EARLY STAGE — system still learning")

        return "\n".join(lines)

    def go_no_go(self, data: dict) -> str:
        """Generate /go_no_go command output."""
        criteria = [
            ("50+ virtual trades", data.get("total_trades", 0) >= 50, f"{data.get('total_trades', 0)} trades"),
            (">50% win rate", data.get("win_rate", 0) > 50, f"{data.get('win_rate', 0):.0f}%"),
            (">1.3 profit factor", data.get("profit_factor", 0) > 1.3, f"{data.get('profit_factor', 0):.2f}"),
            ("<8% max drawdown", abs(data.get("max_drawdown", 0)) < 8, f"{data.get('max_drawdown', 0):.1f}%"),
        ]

        all_pass = all(passed for _, passed, _ in criteria)

        lines = ["=== NZT-48 GO/NO-GO ASSESSMENT ===", ""]
        for name, passed, value in criteria:
            status = "PASS" if passed else "FAIL"
            lines.append(f"  [{status}] {name}: {value}")

        lines.extend([
            "",
            f"VERDICT: {'GO — System proven. Build IBKR layer.' if all_pass else 'NO-GO — Continue virtual tracking.'}",
        ])

        return "\n".join(lines)
