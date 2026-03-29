"""Book 1 — Multi-System Compounding Mathematics.

Implements the Fundamental Law of Active Management and portfolio-level
compounding metrics that the system was missing:

1. IR = IC × √BR (Grinold & Kahn)
   - IC per strategy: correlation between signal confidence and actual P&L
   - BR: number of independent bets per period
   - IR: risk-adjusted excess return

2. Portfolio Sharpe aggregation with √N scaling
   - Combines individual strategy Sharpes using correlation-aware formula
   - SR_P² = Σ SR_i² (uncorrelated case, Treynor-Black)
   - SR_P = SR_avg × √(N / (1 + (N-1)ρ)) (correlated case)

3. Variance drag monitoring
   - variance_drag = σ²/2 (cost of volatility on geometric growth)
   - g = μ - σ²/2 (what actually compounds)
   - Tracks how much volatility is costing in annualized terms

4. Kelly-optimal growth rate
   - g* = SR²/2 per strategy, g*_portfolio = (1/2)Σ SR_i²

Integration:
    - bridge.py: update on every exit (rolling IC, breadth counter)
    - nightly_v6.py: compute daily snapshot → nightly_output.json
    - Persisted to /app/data/fundamental_law.json

Usage:
    from python_brain.metrics.fundamental_law import (
        FundamentalLawTracker, get_tracker,
    )

    tracker = get_tracker()
    tracker.record_signal(strategy, confidence, actual_pnl)
    report = tracker.compute_report()
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("fundamental_law")

DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
STATE_FILE = DATA_DIR / "fundamental_law.json"

# Rolling window sizes
IC_WINDOW = 200       # Last 200 signals per strategy for IC computation
SHARPE_WINDOW = 200   # Last 200 returns per strategy for Sharpe


@dataclass
class StrategyMetrics:
    """Per-strategy IC, Sharpe, and breadth tracking."""
    name: str
    # IC tracking: paired (confidence, actual_return) observations
    confidences: List[float] = field(default_factory=list)
    returns: List[float] = field(default_factory=list)
    # Bet counter
    total_bets: int = 0
    bets_today: int = 0
    last_date: str = ""

    @property
    def ic(self) -> float:
        """Information Coefficient: rank correlation between confidence and P&L.

        Uses Spearman rank correlation (robust to outliers).
        Returns 0.0 if insufficient data.
        """
        if len(self.confidences) < 20:
            return 0.0
        n = len(self.confidences)
        # Rank both series
        conf_ranks = _rank(self.confidences)
        ret_ranks = _rank(self.returns)
        # Spearman ρ = 1 - 6Σd²/(n(n²-1))
        d_sq_sum = sum((c - r) ** 2 for c, r in zip(conf_ranks, ret_ranks))
        rho = 1 - 6 * d_sq_sum / (n * (n * n - 1))
        return rho

    @property
    def sharpe(self) -> float:
        """Annualized Sharpe ratio from recent returns."""
        if len(self.returns) < 10:
            return 0.0
        mean_r = sum(self.returns) / len(self.returns)
        var_r = sum((r - mean_r) ** 2 for r in self.returns) / len(self.returns)
        std_r = var_r ** 0.5
        if std_r < 1e-9:
            return 0.0
        # Annualize assuming ~3 trades/day, 252 days
        daily_sharpe = mean_r / std_r
        return daily_sharpe * (252 ** 0.5)

    @property
    def mean_return(self) -> float:
        if not self.returns:
            return 0.0
        return sum(self.returns) / len(self.returns)

    @property
    def volatility(self) -> float:
        if len(self.returns) < 2:
            return 0.0
        mean_r = self.mean_return
        var_r = sum((r - mean_r) ** 2 for r in self.returns) / len(self.returns)
        return var_r ** 0.5

    def record(self, confidence: float, actual_return: float, date_str: str):
        """Record a signal outcome."""
        self.confidences.append(confidence)
        self.returns.append(actual_return)
        self.total_bets += 1
        if date_str != self.last_date:
            self.bets_today = 0
            self.last_date = date_str
        self.bets_today += 1
        # Trim to window
        if len(self.confidences) > IC_WINDOW:
            self.confidences = self.confidences[-IC_WINDOW:]
            self.returns = self.returns[-IC_WINDOW:]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ic": round(self.ic, 4),
            "sharpe": round(self.sharpe, 3),
            "total_bets": self.total_bets,
            "n_observations": len(self.returns),
            "mean_return": round(self.mean_return, 6),
            "volatility": round(self.volatility, 6),
        }


def _rank(values: List[float]) -> List[float]:
    """Compute ranks (1-based) for Spearman correlation. Handles ties with average rank."""
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        # Find all tied values
        while j < len(indexed) and indexed[j][1] == indexed[i][1]:
            j += 1
        # Average rank for ties
        avg_rank = (i + j + 1) / 2.0  # +1 for 1-based
        for k in range(i, j):
            ranks[indexed[k][0]] = avg_rank
        i = j
    return ranks


@dataclass
class FundamentalLawReport:
    """Snapshot of all Book 1 metrics."""
    timestamp: float = 0.0

    # Fundamental Law: IR = IC × √BR
    avg_ic: float = 0.0               # Average IC across active strategies
    annual_breadth: int = 0            # Estimated annual independent bets
    information_ratio: float = 0.0     # IR = IC × √BR

    # Portfolio Sharpe (√N scaling)
    n_active_strategies: int = 0
    avg_strategy_sharpe: float = 0.0
    portfolio_sharpe_uncorrelated: float = 0.0  # SR × √N (theoretical max)
    portfolio_sharpe_estimated: float = 0.0      # With estimated ρ correction
    avg_pairwise_correlation: float = 0.0

    # Variance drag
    portfolio_mean_return: float = 0.0
    portfolio_volatility: float = 0.0
    variance_drag: float = 0.0         # σ²/2 — what volatility costs
    geometric_growth_rate: float = 0.0 # μ - σ²/2 — what actually compounds

    # Kelly-optimal growth
    kelly_optimal_growth: float = 0.0  # g* = (1/2)Σ SR_i²

    # Per-strategy breakdown
    strategies: Dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "fundamental_law": {
                "avg_ic": round(self.avg_ic, 4),
                "annual_breadth": self.annual_breadth,
                "information_ratio": round(self.information_ratio, 3),
            },
            "portfolio_sharpe": {
                "n_active": self.n_active_strategies,
                "avg_strategy_sharpe": round(self.avg_strategy_sharpe, 3),
                "portfolio_uncorrelated": round(self.portfolio_sharpe_uncorrelated, 3),
                "portfolio_estimated": round(self.portfolio_sharpe_estimated, 3),
                "avg_pairwise_correlation": round(self.avg_pairwise_correlation, 3),
            },
            "compounding": {
                "mean_return_daily": round(self.portfolio_mean_return, 6),
                "volatility_daily": round(self.portfolio_volatility, 6),
                "variance_drag_daily": round(self.variance_drag, 6),
                "variance_drag_annual_pct": round(self.variance_drag * 252 * 100, 2),
                "geometric_growth_daily": round(self.geometric_growth_rate, 6),
                "geometric_growth_annual_pct": round(self.geometric_growth_rate * 252 * 100, 2),
            },
            "kelly_optimal_growth": {
                "daily": round(self.kelly_optimal_growth, 6),
                "annual_pct": round(self.kelly_optimal_growth * 252 * 100, 2),
            },
            "strategies": self.strategies,
        }


class FundamentalLawTracker:
    """Track IR, IC, breadth, portfolio Sharpe, variance drag across all strategies."""

    def __init__(self):
        self._strategies: Dict[str, StrategyMetrics] = {}
        self._portfolio_returns: List[float] = []  # Daily portfolio returns
        self._load()

    def record_signal(self, strategy: str, confidence: float, actual_return: float,
                      date_str: str = ""):
        """Record a signal outcome for IC and breadth tracking.

        Call this on every trade exit with the signal's original confidence
        and the actual realized return.
        """
        if not date_str:
            from datetime import datetime, timezone
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if strategy not in self._strategies:
            self._strategies[strategy] = StrategyMetrics(name=strategy)

        self._strategies[strategy].record(confidence, actual_return, date_str)

    def record_daily_portfolio_return(self, daily_return: float):
        """Record aggregate daily portfolio return for variance drag computation."""
        self._portfolio_returns.append(daily_return)
        if len(self._portfolio_returns) > 252:
            self._portfolio_returns = self._portfolio_returns[-252:]

    def compute_report(self) -> FundamentalLawReport:
        """Compute full Book 1 metrics snapshot."""
        report = FundamentalLawReport(timestamp=time.time())

        active = {k: v for k, v in self._strategies.items() if v.total_bets >= 10}
        if not active:
            return report

        # Per-strategy metrics
        ics = []
        sharpes = []
        for name, sm in active.items():
            report.strategies[name] = sm.to_dict()
            ic = sm.ic
            sr = sm.sharpe
            if len(sm.returns) >= 20:
                ics.append(ic)
            if len(sm.returns) >= 10:
                sharpes.append(sr)

        report.n_active_strategies = len(active)

        # --- Fundamental Law: IR = IC × √BR ---
        if ics:
            report.avg_ic = sum(ics) / len(ics)
        # Breadth: total independent bets/year (annualized from observed rate)
        total_bets = sum(sm.total_bets for sm in active.values())
        # Estimate days of data
        all_dates = set()
        for sm in active.values():
            if sm.last_date:
                all_dates.add(sm.last_date)
        n_trading_days = max(len(all_dates), 1)
        daily_rate = total_bets / n_trading_days
        report.annual_breadth = int(daily_rate * 252)
        if report.annual_breadth > 0 and report.avg_ic != 0:
            report.information_ratio = report.avg_ic * math.sqrt(report.annual_breadth)

        # --- Portfolio Sharpe (√N scaling) ---
        positive_sharpes = [s for s in sharpes if s > 0]
        if positive_sharpes:
            report.avg_strategy_sharpe = sum(positive_sharpes) / len(positive_sharpes)
            N = len(positive_sharpes)

            # Uncorrelated case: SR_P = SR_avg × √N (Treynor-Black)
            report.portfolio_sharpe_uncorrelated = report.avg_strategy_sharpe * math.sqrt(N)

            # Correlated case: SR_P = SR_avg × √(N / (1 + (N-1)ρ))
            # Use average pairwise correlation from the correlation module if available
            try:
                from python_brain.risk.correlation import get_correlation_tracker
                ct = get_correlation_tracker()
                report.avg_pairwise_correlation = ct.avg_correlation
            except (ImportError, AttributeError):
                report.avg_pairwise_correlation = 0.25  # Conservative estimate

            rho = max(0.0, min(0.99, report.avg_pairwise_correlation))
            denom = 1 + (N - 1) * rho
            if denom > 0:
                report.portfolio_sharpe_estimated = (
                    report.avg_strategy_sharpe * math.sqrt(N / denom)
                )

        # --- Variance Drag ---
        if len(self._portfolio_returns) >= 10:
            mu = sum(self._portfolio_returns) / len(self._portfolio_returns)
            var = sum((r - mu) ** 2 for r in self._portfolio_returns) / len(self._portfolio_returns)
            sigma = var ** 0.5
            report.portfolio_mean_return = mu
            report.portfolio_volatility = sigma
            report.variance_drag = var / 2  # σ²/2
            report.geometric_growth_rate = mu - var / 2  # g = μ - σ²/2

        # --- Kelly-Optimal Growth: g* = (1/2)Σ SR_i² ---
        if positive_sharpes:
            # Convert annualized Sharpe to daily for growth rate
            daily_sharpes = [s / math.sqrt(252) for s in positive_sharpes]
            report.kelly_optimal_growth = 0.5 * sum(s * s for s in daily_sharpes)

        return report

    def save(self):
        """Persist state to disk."""
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "strategies": {},
            "portfolio_returns": self._portfolio_returns[-252:],
        }
        for name, sm in self._strategies.items():
            state["strategies"][name] = {
                "name": sm.name,
                "confidences": sm.confidences[-IC_WINDOW:],
                "returns": sm.returns[-IC_WINDOW:],
                "total_bets": sm.total_bets,
                "bets_today": sm.bets_today,
                "last_date": sm.last_date,
            }
        try:
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(state), encoding="utf-8")
            os.rename(str(tmp), str(STATE_FILE))
        except OSError as e:
            log.warning("Failed to save fundamental_law state: %s", e)

    def _load(self):
        """Restore state from disk."""
        if not STATE_FILE.exists():
            return
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            self._portfolio_returns = data.get("portfolio_returns", [])
            for name, d in data.get("strategies", {}).items():
                sm = StrategyMetrics(name=name)
                sm.confidences = d.get("confidences", [])
                sm.returns = d.get("returns", [])
                sm.total_bets = d.get("total_bets", 0)
                sm.bets_today = d.get("bets_today", 0)
                sm.last_date = d.get("last_date", "")
                self._strategies[name] = sm
            log.info("BOOK1: loaded fundamental law state (%d strategies, %d portfolio returns)",
                     len(self._strategies), len(self._portfolio_returns))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load fundamental_law state: %s", e)


# ---------------------------------------------------------------------------
# Singleton + convenience
# ---------------------------------------------------------------------------
_tracker: Optional[FundamentalLawTracker] = None


def get_tracker() -> FundamentalLawTracker:
    global _tracker
    if _tracker is None:
        _tracker = FundamentalLawTracker()
    return _tracker
