"""P23: Ouroboros Offline Calibration Tool.

Standalone calibration tool that reads WAL archive and produces DynamicWeights
WITHOUT running live. Enables:
  - Parameter sensitivity analysis
  - A/B testing: calibrate alternate weights, compare backtest performance
  - Historical replay of any date range

Usage:
    python -m ouroboros.offline_calibration --archive-dir events/archive/ --days 30
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .bayesian import BayesianResult, DSRResult, bayesian_win_rate, deflated_sharpe_ratio
from .config import CHANDELIER_ATR_MULT_DEFAULT, KELLY_FLOOR
from .exit_calibration import ExitCalibrationResult, calibrate_exit_multiplier
from .kelly_accelerator import KellyUpdate, compute_kelly_updates
from .regime_hunting import RegimeHuntResult, hunt_regimes
from .wal_reader import ClosedTrade, read_day_journal


@dataclass
class CalibrationResult:
    """Result of offline calibration run."""
    days_processed: int
    total_trades: int
    bayesian: Optional[BayesianResult] = None
    dsr: Optional[DSRResult] = None
    kelly_updates: Optional[Dict[int, KellyUpdate]] = None
    exit_cal: Optional[ExitCalibrationResult] = None
    regime: Optional[RegimeHuntResult] = None
    equity_curve: List[float] = field(default_factory=list)
    daily_returns: List[float] = field(default_factory=list)


@dataclass
class SensitivityResult:
    """Result of parameter sensitivity sweep."""
    parameter_name: str
    parameter_values: List[float]
    metrics: Dict[str, List[float]]


def run_offline_calibration(
    wal_paths: List[Path],
    prior_kellys: Optional[Dict[int, float]] = None,
    prior_chandelier_mult: float = CHANDELIER_ATR_MULT_DEFAULT,
) -> CalibrationResult:
    """Run offline calibration on a list of WAL files (one per day).

    Processes WAL files in order, accumulating trades and running
    the full analytics pipeline at the end.
    """
    all_trades: List[ClosedTrade] = []
    equity = 100_000.0
    equity_curve = [equity]
    daily_returns: List[float] = []

    for wal_path in wal_paths:
        journal = read_day_journal(wal_path)
        if journal is None or journal.total_events == 0:
            continue

        day_trades = journal.closed_trades
        all_trades.extend(day_trades)

        # Compute daily equity change.
        day_pnl = sum(t.final_pnl for t in day_trades)
        daily_ret = day_pnl / equity if equity > 0 else 0.0
        equity += day_pnl
        equity_curve.append(equity)
        daily_returns.append(daily_ret)

    if not all_trades:
        return CalibrationResult(
            days_processed=len(wal_paths),
            total_trades=0,
            equity_curve=equity_curve,
        )

    # Run full analytics on accumulated trades.
    pnls = [t.final_pnl for t in all_trades]
    bwr = bayesian_win_rate(pnls)

    returns = []
    for t in all_trades:
        if t.entry_price > 0 and t.qty > 0:
            notional = t.entry_price * t.qty
            returns.append(t.final_pnl / notional)
        elif t.final_pnl != 0:
            returns.append(0.01 if t.final_pnl > 0 else -0.01)

    dsr = deflated_sharpe_ratio(returns)
    kelly = compute_kelly_updates(all_trades, prior_kellys or {})
    exit_cal = calibrate_exit_multiplier(all_trades, prior_chandelier_mult)
    regime = hunt_regimes(all_trades)

    return CalibrationResult(
        days_processed=len(wal_paths),
        total_trades=len(all_trades),
        bayesian=bwr,
        dsr=dsr,
        kelly_updates=kelly,
        exit_cal=exit_cal,
        regime=regime,
        equity_curve=equity_curve,
        daily_returns=daily_returns,
    )


def sensitivity_sweep(
    wal_paths: List[Path],
    parameter_name: str,
    values: List[float],
) -> SensitivityResult:
    """Sweep a parameter and measure impact on key metrics.

    Currently supports: chandelier_atr_mult sweep.
    """
    metrics: Dict[str, List[float]] = {
        "sharpe": [],
        "win_rate": [],
        "chandelier_mult": [],
        "final_equity": [],
    }

    for val in values:
        if parameter_name == "chandelier_atr_mult":
            result = run_offline_calibration(
                wal_paths, prior_chandelier_mult=val,
            )
        else:
            result = run_offline_calibration(wal_paths)

        metrics["sharpe"].append(result.dsr.sharpe_ratio if result.dsr else 0.0)
        metrics["win_rate"].append(
            result.bayesian.bayesian_win_rate if result.bayesian else 0.5
        )
        metrics["chandelier_mult"].append(
            result.exit_cal.new_multiplier if result.exit_cal else val
        )
        metrics["final_equity"].append(
            result.equity_curve[-1] if result.equity_curve else 10000.0
        )

    return SensitivityResult(
        parameter_name=parameter_name,
        parameter_values=values,
        metrics=metrics,
    )
