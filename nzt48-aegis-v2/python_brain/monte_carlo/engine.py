"""Book 17: Monte Carlo Simulation Engine — Equity Curve Risk Analysis.

Runs bootstrap resampling and GBM simulations to assess:
- P(Sharpe Ratio > 0)
- P(Kelly fraction > 0)
- P(ruin) — equity drops below 70% of starting capital
- Maximum drawdown distribution
- Terminal equity distribution

Uses actual trade returns from WAL files for bootstrap resampling.
Supports both nightly (10K paths) and weekly (50K paths) analysis.

Usage:
    from python_brain.monte_carlo.engine import run_monte_carlo_nightly

    summary = run_monte_carlo_nightly()
    # Returns dict for pipeline logging

Saves detailed report to DATA_DIR/monte_carlo_report.json
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("monte_carlo")

DATA_DIR = os.environ.get("AEGIS_DATA_DIR", "/app/data")
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", "/app/events"))

STARTING_EQUITY = 10000.0
RUIN_THRESHOLD = 0.70  # 70% of starting equity
TRADING_DAYS_PER_YEAR = 252


@dataclass
class MonteCarloResult:
    """Results from a single Monte Carlo simulation path."""
    terminal_equity: float
    max_drawdown_pct: float
    hit_ruin: bool
    sharpe_ratio: float
    kelly_fraction: float
    avg_return: float
    volatility: float


@dataclass
class MonteCarloReport:
    """Aggregate Monte Carlo analysis report."""
    timestamp: str
    n_paths: int
    n_trades_observed: int
    lookback_days: int

    # Bootstrap resampling results
    bootstrap_p_sharpe_positive: float
    bootstrap_p_kelly_positive: float
    bootstrap_p_ruin: float
    bootstrap_avg_terminal: float
    bootstrap_median_terminal: float
    bootstrap_max_dd_median: float
    bootstrap_max_dd_p95: float

    # GBM model results
    gbm_p_sharpe_positive: float
    gbm_p_kelly_positive: float
    gbm_p_ruin: float
    gbm_avg_terminal: float
    gbm_median_terminal: float
    gbm_max_dd_median: float
    gbm_max_dd_p95: float

    # Model parameters
    estimated_drift: float
    estimated_volatility: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def load_trade_returns(lookback_days: int = 90) -> List[float]:
    """Load realized trade returns from WAL files.

    Returns list of per-trade returns as percentages (e.g., 2.5 = 2.5% gain).
    Only includes closed positions with valid entry_price and pnl.
    """
    returns: List[float] = []
    today = datetime.now(timezone.utc).date()

    for d in range(lookback_days):
        date = today - timedelta(days=d)
        wal_path = WAL_DIR / f"{date.strftime('%Y-%m-%d')}.ndjson"

        if not wal_path.exists():
            continue

        try:
            with open(wal_path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        evt = json.loads(line)
                        if evt.get("event_type") != "PositionClosed":
                            continue

                        entry = evt.get("entry_price", evt.get("avg_entry", 0.0))
                        pnl = evt.get("realized_pnl", evt.get("pnl", 0.0))
                        qty = abs(evt.get("quantity", evt.get("shares", 0)))

                        if entry <= 0 or qty == 0:
                            continue

                        # Per-share PnL to percentage return
                        pnl_per_share = pnl / qty
                        return_pct = (pnl_per_share / entry) * 100

                        returns.append(return_pct)

                    except (json.JSONDecodeError, KeyError, ZeroDivisionError):
                        continue
        except IOError:
            continue

    return returns


def estimate_parameters(returns: List[float]) -> Tuple[float, float]:
    """Estimate drift (mu) and volatility (sigma) from trade returns.

    Returns:
        (drift, volatility) as annualized values
    """
    if len(returns) < 2:
        return 0.0, 0.0

    n = len(returns)
    mean_return = sum(returns) / n

    variance = sum((r - mean_return) ** 2 for r in returns) / (n - 1)
    std_dev = math.sqrt(variance) if variance > 0 else 0.0

    # Annualize (assuming returns are per-trade, not per-day)
    # Use conservative scaling based on observed trade frequency
    drift = mean_return / 100.0  # Convert percentage to decimal
    volatility = std_dev / 100.0

    return drift, volatility


def run_bootstrap_path(returns: List[float], n_trades: int) -> MonteCarloResult:
    """Run single bootstrap resampling path.

    Randomly samples from actual trade returns with replacement.
    """
    equity = STARTING_EQUITY
    peak_equity = equity
    max_dd = 0.0
    hit_ruin = False
    path_returns: List[float] = []

    for _ in range(n_trades):
        trade_return_pct = random.choice(returns)
        trade_return_decimal = trade_return_pct / 100.0

        equity *= (1.0 + trade_return_decimal)
        path_returns.append(trade_return_decimal)

        if equity > peak_equity:
            peak_equity = equity

        dd = (peak_equity - equity) / peak_equity * 100.0
        if dd > max_dd:
            max_dd = dd

        if equity < STARTING_EQUITY * RUIN_THRESHOLD:
            hit_ruin = True

    # Compute Sharpe and Kelly
    avg_ret = sum(path_returns) / len(path_returns) if path_returns else 0.0
    var_ret = sum((r - avg_ret) ** 2 for r in path_returns) / len(path_returns) if path_returns else 0.0
    std_ret = math.sqrt(var_ret) if var_ret > 0 else 0.0

    sharpe = (avg_ret / std_ret * math.sqrt(TRADING_DAYS_PER_YEAR)) if std_ret > 0 else 0.0
    kelly = (avg_ret / var_ret) if var_ret > 0 else 0.0

    return MonteCarloResult(
        terminal_equity=equity,
        max_drawdown_pct=max_dd,
        hit_ruin=hit_ruin,
        sharpe_ratio=sharpe,
        kelly_fraction=kelly,
        avg_return=avg_ret,
        volatility=std_ret,
    )


def run_gbm_path(
    drift: float,
    volatility: float,
    n_steps: int,
    dt: float = 1.0 / TRADING_DAYS_PER_YEAR,
) -> MonteCarloResult:
    """Run single Geometric Brownian Motion path.

    S_t+1 = S_t * exp((mu - sigma^2/2)*dt + sigma*sqrt(dt)*Z)
    where Z ~ N(0,1)
    """
    equity = STARTING_EQUITY
    peak_equity = equity
    max_dd = 0.0
    hit_ruin = False
    path_returns: List[float] = []

    sqrt_dt = math.sqrt(dt)
    drift_term = (drift - 0.5 * volatility ** 2) * dt

    for _ in range(n_steps):
        z = random.gauss(0, 1)
        shock = volatility * sqrt_dt * z

        growth = math.exp(drift_term + shock)
        new_equity = equity * growth

        ret = (new_equity - equity) / equity
        path_returns.append(ret)

        equity = new_equity

        if equity > peak_equity:
            peak_equity = equity

        dd = (peak_equity - equity) / peak_equity * 100.0
        if dd > max_dd:
            max_dd = dd

        if equity < STARTING_EQUITY * RUIN_THRESHOLD:
            hit_ruin = True

    avg_ret = sum(path_returns) / len(path_returns) if path_returns else 0.0
    var_ret = sum((r - avg_ret) ** 2 for r in path_returns) / len(path_returns) if path_returns else 0.0
    std_ret = math.sqrt(var_ret) if var_ret > 0 else 0.0

    sharpe = (avg_ret / std_ret * math.sqrt(TRADING_DAYS_PER_YEAR)) if std_ret > 0 else 0.0
    kelly = (avg_ret / var_ret) if var_ret > 0 else 0.0

    return MonteCarloResult(
        terminal_equity=equity,
        max_drawdown_pct=max_dd,
        hit_ruin=hit_ruin,
        sharpe_ratio=sharpe,
        kelly_fraction=kelly,
        avg_return=avg_ret,
        volatility=std_ret,
    )


def aggregate_results(results: List[MonteCarloResult]) -> Dict[str, float]:
    """Aggregate Monte Carlo results into summary statistics."""
    if not results:
        return {}

    n = len(results)

    sharpe_positive = sum(1 for r in results if r.sharpe_ratio > 0)
    kelly_positive = sum(1 for r in results if r.kelly_fraction > 0)
    ruin_count = sum(1 for r in results if r.hit_ruin)

    terminal_equities = sorted([r.terminal_equity for r in results])
    max_dds = sorted([r.max_drawdown_pct for r in results])

    median_idx = n // 2
    p95_idx = int(n * 0.95)

    return {
        "p_sharpe_positive": sharpe_positive / n,
        "p_kelly_positive": kelly_positive / n,
        "p_ruin": ruin_count / n,
        "avg_terminal": sum(terminal_equities) / n,
        "median_terminal": terminal_equities[median_idx],
        "max_dd_median": max_dds[median_idx],
        "max_dd_p95": max_dds[min(p95_idx, n - 1)],
    }


def run_monte_carlo(
    n_paths: int = 10000,
    lookback_days: int = 90,
) -> MonteCarloReport:
    """Run full Monte Carlo analysis with bootstrap and GBM models.

    Args:
        n_paths: Number of simulation paths to run
        lookback_days: Days of historical trade data to load

    Returns:
        MonteCarloReport with aggregated results
    """
    log.info(f"Loading trade returns (lookback={lookback_days} days)...")
    returns = load_trade_returns(lookback_days)

    if len(returns) < 10:
        log.warning(f"Insufficient trade data: {len(returns)} trades. Need at least 10.")
        return MonteCarloReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            n_paths=0,
            n_trades_observed=len(returns),
            lookback_days=lookback_days,
            bootstrap_p_sharpe_positive=0.0,
            bootstrap_p_kelly_positive=0.0,
            bootstrap_p_ruin=0.0,
            bootstrap_avg_terminal=STARTING_EQUITY,
            bootstrap_median_terminal=STARTING_EQUITY,
            bootstrap_max_dd_median=0.0,
            bootstrap_max_dd_p95=0.0,
            gbm_p_sharpe_positive=0.0,
            gbm_p_kelly_positive=0.0,
            gbm_p_ruin=0.0,
            gbm_avg_terminal=STARTING_EQUITY,
            gbm_median_terminal=STARTING_EQUITY,
            gbm_max_dd_median=0.0,
            gbm_max_dd_p95=0.0,
            estimated_drift=0.0,
            estimated_volatility=0.0,
        )

    log.info(f"Loaded {len(returns)} trade returns")

    drift, volatility = estimate_parameters(returns)
    log.info(f"Estimated drift={drift:.4f}, volatility={volatility:.4f}")

    # Use observed trade count for bootstrap, or project forward
    n_trades_per_path = max(len(returns), 100)

    # Bootstrap resampling
    log.info(f"Running {n_paths} bootstrap paths...")
    bootstrap_results: List[MonteCarloResult] = []
    for i in range(n_paths):
        if i % 1000 == 0 and i > 0:
            log.info(f"  Completed {i}/{n_paths} bootstrap paths")
        result = run_bootstrap_path(returns, n_trades_per_path)
        bootstrap_results.append(result)

    bootstrap_stats = aggregate_results(bootstrap_results)

    # GBM simulation
    log.info(f"Running {n_paths} GBM paths...")
    gbm_results: List[MonteCarloResult] = []
    for i in range(n_paths):
        if i % 1000 == 0 and i > 0:
            log.info(f"  Completed {i}/{n_paths} GBM paths")
        result = run_gbm_path(drift, volatility, n_trades_per_path)
        gbm_results.append(result)

    gbm_stats = aggregate_results(gbm_results)

    report = MonteCarloReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        n_paths=n_paths,
        n_trades_observed=len(returns),
        lookback_days=lookback_days,
        bootstrap_p_sharpe_positive=bootstrap_stats["p_sharpe_positive"],
        bootstrap_p_kelly_positive=bootstrap_stats["p_kelly_positive"],
        bootstrap_p_ruin=bootstrap_stats["p_ruin"],
        bootstrap_avg_terminal=bootstrap_stats["avg_terminal"],
        bootstrap_median_terminal=bootstrap_stats["median_terminal"],
        bootstrap_max_dd_median=bootstrap_stats["max_dd_median"],
        bootstrap_max_dd_p95=bootstrap_stats["max_dd_p95"],
        gbm_p_sharpe_positive=gbm_stats["p_sharpe_positive"],
        gbm_p_kelly_positive=gbm_stats["p_kelly_positive"],
        gbm_p_ruin=gbm_stats["p_ruin"],
        gbm_avg_terminal=gbm_stats["avg_terminal"],
        gbm_median_terminal=gbm_stats["median_terminal"],
        gbm_max_dd_median=gbm_stats["max_dd_median"],
        gbm_max_dd_p95=gbm_stats["max_dd_p95"],
        estimated_drift=drift,
        estimated_volatility=volatility,
    )

    # Save report
    output_path = Path(DATA_DIR) / "monte_carlo_report.json"
    with open(output_path, "w") as f:
        json.dump(report.to_dict(), f, indent=2)

    log.info(f"Saved report to {output_path}")

    return report


def run_monte_carlo_nightly() -> Dict[str, Any]:
    """Nightly Monte Carlo analysis with 10K paths.

    Returns summary dict for pipeline logging.
    """
    log.info("Starting nightly Monte Carlo analysis (10K paths)...")
    report = run_monte_carlo(n_paths=10000, lookback_days=90)

    return {
        "status": "complete" if report.n_trades_observed >= 10 else "insufficient_data",
        "n_paths": report.n_paths,
        "n_trades": report.n_trades_observed,
        "bootstrap_p_ruin": round(report.bootstrap_p_ruin, 4),
        "gbm_p_ruin": round(report.gbm_p_ruin, 4),
        "bootstrap_p_sharpe_positive": round(report.bootstrap_p_sharpe_positive, 4),
        "timestamp": report.timestamp,
    }


def run_monte_carlo_weekly() -> Dict[str, Any]:
    """Weekly Monte Carlo analysis with 50K paths and extended lookback.

    Returns summary dict for pipeline logging.
    """
    log.info("Starting weekly Monte Carlo analysis (50K paths)...")
    report = run_monte_carlo(n_paths=50000, lookback_days=180)

    return {
        "status": "complete" if report.n_trades_observed >= 10 else "insufficient_data",
        "n_paths": report.n_paths,
        "n_trades": report.n_trades_observed,
        "bootstrap_p_ruin": round(report.bootstrap_p_ruin, 4),
        "gbm_p_ruin": round(report.gbm_p_ruin, 4),
        "bootstrap_median_terminal": round(report.bootstrap_median_terminal, 2),
        "gbm_median_terminal": round(report.gbm_median_terminal, 2),
        "max_dd_p95": round(report.bootstrap_max_dd_p95, 2),
        "timestamp": report.timestamp,
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    summary = run_monte_carlo_nightly()

    print(json.dumps(summary, indent=2))

    # Optional Telegram notification
    try:
        from python_brain.ouroboros.claude_helper import send_telegram

        if summary["status"] == "complete":
            msg = (
                f"<b>Monte Carlo Analysis Complete</b>\n\n"
                f"Paths: {summary['n_paths']:,}\n"
                f"Trades analyzed: {summary['n_trades']}\n"
                f"P(ruin) bootstrap: {summary['bootstrap_p_ruin']:.1%}\n"
                f"P(ruin) GBM: {summary['gbm_p_ruin']:.1%}\n"
                f"P(SR>0): {summary['bootstrap_p_sharpe_positive']:.1%}\n"
            )
            send_telegram(msg)
    except ImportError:
        pass
