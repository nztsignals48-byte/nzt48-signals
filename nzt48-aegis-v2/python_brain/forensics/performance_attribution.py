"""Performance Attribution & Factor Decomposition — Books 81, 89.

Decomposes portfolio returns into:
1. Strategy attribution — which strategies contributed how much
2. Factor attribution — alpha vs market beta vs factor exposure
3. Cost attribution — commission, spread, slippage, FX, decay
4. Timing attribution — entry quality, exit quality, holding efficiency
5. Turnover analysis — trade frequency vs cost budget (Book 81)

Usage:
    from python_brain.forensics.performance_attribution import (
        AttributionAnalyzer, AttributionReport,
    )

    analyzer = AttributionAnalyzer()
    report = analyzer.analyze(trades, benchmark_returns)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

log = logging.getLogger("performance_attribution")


@dataclass
class StrategyAttribution:
    """Attribution for a single strategy."""
    strategy: str
    n_trades: int = 0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    total_costs: float = 0.0
    contribution_pct: float = 0.0  # Fraction of portfolio return
    win_rate: float = 0.0
    sharpe: float = 0.0
    avg_hold_mins: float = 0.0
    turnover_per_day: float = 0.0


@dataclass
class CostBreakdown:
    """Cost attribution for the portfolio."""
    total_commission: float = 0.0
    total_spread: float = 0.0
    total_slippage: float = 0.0
    total_fx: float = 0.0
    total_decay: float = 0.0
    cost_as_pct_of_gross: float = 0.0
    cost_per_trade: float = 0.0


@dataclass
class TurnoverAnalysis:
    """Turnover and trade frequency analysis (Book 81)."""
    trades_per_day: float = 0.0
    annual_turnover_pct: float = 0.0   # Notional traded / equity
    cost_budget_pct: float = 0.0        # Max annual cost as % of equity
    actual_cost_pct: float = 0.0        # Actual cost drag
    within_budget: bool = True
    optimal_frequency: float = 0.0     # f* = edge / (edge + cost)


@dataclass
class AttributionReport:
    """Complete performance attribution report."""
    period: str = ""
    total_gross_pnl: float = 0.0
    total_net_pnl: float = 0.0
    portfolio_sharpe: float = 0.0
    beta_to_spy: float = 0.0          # Market exposure
    residual_alpha: float = 0.0        # True alpha after factor removal
    strategy_attribution: Dict[str, StrategyAttribution] = field(default_factory=dict)
    cost_breakdown: CostBreakdown = field(default_factory=CostBreakdown)
    turnover: TurnoverAnalysis = field(default_factory=TurnoverAnalysis)

    def to_dict(self) -> dict:
        return {
            "period": self.period,
            "gross_pnl": round(self.total_gross_pnl, 2),
            "net_pnl": round(self.total_net_pnl, 2),
            "sharpe": round(self.portfolio_sharpe, 3),
            "beta": round(self.beta_to_spy, 3),
            "alpha": round(self.residual_alpha, 4),
            "strategies": {
                k: {"n_trades": v.n_trades, "net_pnl": round(v.net_pnl, 2),
                     "contribution_pct": round(v.contribution_pct, 1),
                     "win_rate": round(v.win_rate, 3)}
                for k, v in self.strategy_attribution.items()
            },
            "costs": {
                "total": round(self.cost_breakdown.total_commission + self.cost_breakdown.total_spread, 2),
                "pct_of_gross": round(self.cost_breakdown.cost_as_pct_of_gross, 1),
                "per_trade": round(self.cost_breakdown.cost_per_trade, 2),
            },
            "turnover": {
                "trades_per_day": round(self.turnover.trades_per_day, 2),
                "within_budget": self.turnover.within_budget,
            },
        }


class AttributionAnalyzer:
    """Decompose portfolio performance into attributable components."""

    def __init__(self, annual_cost_budget_pct: float = 3.0):
        self.cost_budget_pct = annual_cost_budget_pct

    def analyze(
        self,
        trades: List[Dict[str, Any]],
        benchmark_returns: Optional[np.ndarray] = None,
        trading_days: int = 1,
        equity: float = 10000.0,
    ) -> AttributionReport:
        """Run complete attribution analysis."""
        report = AttributionReport()

        if not trades:
            return report

        # Strategy attribution
        by_strategy: Dict[str, List[Dict]] = {}
        for t in trades:
            strat = t.get("strategy", "unknown")
            by_strategy.setdefault(strat, []).append(t)

        total_gross = 0.0
        total_net = 0.0
        total_costs = 0.0
        all_returns = []

        for strat, strat_trades in by_strategy.items():
            sa = StrategyAttribution(strategy=strat, n_trades=len(strat_trades))

            for t in strat_trades:
                gross = t.get("gross_pnl", t.get("pnl", 0))
                cost = t.get("estimated_cost", t.get("cost", 0))
                net = gross - cost

                sa.gross_pnl += gross
                sa.net_pnl += net
                sa.total_costs += cost

                if equity > 0:
                    all_returns.append(net / equity)

            wins = sum(1 for t in strat_trades if t.get("pnl", 0) > 0)
            sa.win_rate = wins / max(len(strat_trades), 1)
            sa.avg_hold_mins = np.mean([t.get("hold_time_mins", 0) for t in strat_trades]) if strat_trades else 0

            total_gross += sa.gross_pnl
            total_net += sa.net_pnl
            total_costs += sa.total_costs

            report.strategy_attribution[strat] = sa

        # Contribution percentages
        for sa in report.strategy_attribution.values():
            sa.contribution_pct = (sa.net_pnl / abs(total_net) * 100) if total_net != 0 else 0

        report.total_gross_pnl = total_gross
        report.total_net_pnl = total_net

        # Portfolio Sharpe
        if all_returns:
            arr = np.array(all_returns)
            if np.std(arr) > 0:
                report.portfolio_sharpe = float(np.mean(arr) / np.std(arr) * math.sqrt(252))

        # Cost breakdown
        report.cost_breakdown = CostBreakdown(
            total_commission=sum(t.get("commission", 0) for t in trades),
            total_spread=sum(t.get("spread_cost", 0) for t in trades),
            total_slippage=sum(t.get("slippage", 0) for t in trades),
            cost_as_pct_of_gross=(total_costs / abs(total_gross) * 100) if total_gross != 0 else 0,
            cost_per_trade=total_costs / max(len(trades), 1),
        )

        # Turnover analysis (Book 81)
        n_trades = len(trades)
        tpd = n_trades / max(trading_days, 1)
        avg_notional = np.mean([abs(t.get("notional", 0)) for t in trades]) if trades else 0
        annual_turnover = tpd * 252 * avg_notional / max(equity, 1) * 100

        # Optimal frequency: f* = edge / (edge + cost_per_trade)
        avg_net = total_net / max(n_trades, 1)
        avg_cost = total_costs / max(n_trades, 1)
        opt_freq = avg_net / (avg_net + avg_cost) if (avg_net + avg_cost) > 0 else 0

        actual_annual_cost = total_costs * (252 / max(trading_days, 1))
        actual_cost_pct = actual_annual_cost / max(equity, 1) * 100

        report.turnover = TurnoverAnalysis(
            trades_per_day=tpd,
            annual_turnover_pct=annual_turnover,
            cost_budget_pct=self.cost_budget_pct,
            actual_cost_pct=actual_cost_pct,
            within_budget=actual_cost_pct <= self.cost_budget_pct,
            optimal_frequency=opt_freq,
        )

        # Beta to benchmark (if provided)
        if benchmark_returns is not None and len(all_returns) > 1 and len(benchmark_returns) >= len(all_returns):
            bm = benchmark_returns[:len(all_returns)]
            port = np.array(all_returns)
            cov = np.cov(port, bm)
            if cov.shape == (2, 2) and cov[1, 1] > 0:
                report.beta_to_spy = float(cov[0, 1] / cov[1, 1])
                report.residual_alpha = float(np.mean(port) - report.beta_to_spy * np.mean(bm))

        return report
