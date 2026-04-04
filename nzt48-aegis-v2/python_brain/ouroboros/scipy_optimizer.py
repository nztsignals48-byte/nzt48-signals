"""SciPy Parameter Optimizer — constrained optimization for AEGIS config parameters.

Replaces heuristic parameter tuning in config_writer.py with gradient-based
constrained optimization using L-BFGS-B.

Optimizes:
  - confidence_floor: [0.55, 0.80]
  - chandelier_atr_mult: [1.5, 3.0]
  - heat_limit: [5%, 10%]
  - kelly_cap: [0.01, 0.05]
  - spread_veto_pct: [0.1%, 0.5%]

Objective: maximize risk-adjusted returns (Sharpe ratio) from historical trades.

License: SciPy is BSD-3.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("scipy_optimizer")


@dataclass
class OptimizationResult:
    """Result of parameter optimization."""
    optimal_params: Dict[str, float]
    objective_value: float  # Negative Sharpe (since we minimize)
    converged: bool
    iterations: int
    previous_params: Dict[str, float]
    improvement_pct: float

    def to_dict(self) -> dict:
        return {
            "optimal_params": self.optimal_params,
            "objective_value": self.objective_value,
            "converged": self.converged,
            "iterations": self.iterations,
            "previous_params": self.previous_params,
            "improvement_pct": self.improvement_pct,
        }


# Parameter bounds (MUST match self-reflection bounds in MEMORY.md)
PARAM_BOUNDS = {
    "confidence_floor": (0.55, 0.80),
    "chandelier_atr_mult": (1.5, 3.0),
    "heat_limit_pct": (5.0, 10.0),
    "kelly_cap": (0.01, 0.05),
    "spread_veto_pct": (0.10, 0.50),
}

# Parameter names in optimization vector order
PARAM_NAMES = list(PARAM_BOUNDS.keys())


def _load_trade_outcomes(data_dir: Optional[str] = None) -> Optional[np.ndarray]:
    """Load historical trade P&L outcomes from WAL or strategy_pnl_history."""
    if data_dir is None:
        data_dir = os.environ.get("AEGIS_DATA_DIR", "/app/data")

    # Try strategy_pnl_history.json first (aggregated)
    pnl_path = os.path.join(data_dir, "strategy_pnl_history.json")
    try:
        with open(pnl_path) as f:
            data = json.load(f)
        # Flatten all strategy returns into one series
        all_returns = []
        for returns in data.values():
            if isinstance(returns, list):
                all_returns.extend(returns)
        if all_returns:
            return np.array(all_returns, dtype=float)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # Try nightly recommendations (has trade_outcomes)
    recs_path = os.path.join(data_dir, "ouroboros_recommendations.json")
    try:
        with open(recs_path) as f:
            data = json.load(f)
        outcomes = data.get("trade_outcomes", [])
        if outcomes:
            return np.array([t.get("pnl", 0) for t in outcomes], dtype=float)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    return None


def _objective_fn(
    params_vec: np.ndarray,
    trade_outcomes: np.ndarray,
) -> float:
    """Objective function: negative Sharpe ratio (to minimize).

    Simulates the effect of parameter choices on trade selection and sizing,
    then computes the resulting Sharpe ratio.

    This is a simplified model — real backtesting uses the full engine.
    """
    confidence_floor = params_vec[0]
    chandelier_mult = params_vec[1]
    heat_limit = params_vec[2] / 100.0  # Convert from % to fraction
    kelly_cap = params_vec[3]
    spread_veto = params_vec[4] / 100.0

    # Simulate parameter effects on returns:
    # Higher confidence floor → fewer trades, hopefully better quality
    # Higher chandelier mult → wider stops → fewer stop-outs but larger losses
    # Lower heat limit → less exposure → lower returns but lower DD
    # Kelly cap → position sizing

    n = len(trade_outcomes)
    if n < 10:
        return 0.0  # Not enough data

    # Model: confidence floor filters out bottom trades
    # Sort by absolute PnL as proxy for signal confidence
    sorted_abs = np.argsort(np.abs(trade_outcomes))
    # Higher floor = keep only top fraction of trades
    keep_fraction = 1.0 - (confidence_floor - 0.55) / (0.80 - 0.55) * 0.5
    keep_n = max(5, int(n * keep_fraction))
    filtered_indices = sorted_abs[-keep_n:]
    filtered = trade_outcomes[filtered_indices]

    # Apply Kelly cap scaling
    scaled = filtered * kelly_cap / 0.03  # Normalize around 3% baseline

    # Apply heat limit as a vol scaling factor
    vol_scale = heat_limit / 0.075  # Normalize around 7.5% baseline
    scaled *= vol_scale

    # Chandelier effect: tighter stops reduce both big wins and big losses
    # Wider stops (higher mult) keep more of the tails
    tail_keep = chandelier_mult / 2.25  # Normalize around 2.25 baseline
    scaled = np.where(
        np.abs(scaled) > np.percentile(np.abs(scaled), 90),
        scaled * tail_keep,
        scaled,
    )

    # Compute Sharpe
    if scaled.std() == 0:
        return 0.0
    sharpe = scaled.mean() / scaled.std() * np.sqrt(252)
    return -sharpe  # Minimize negative Sharpe = maximize Sharpe


def optimize_parameters(
    data_dir: Optional[str] = None,
    current_params: Optional[Dict[str, float]] = None,
) -> Optional[OptimizationResult]:
    """Run constrained parameter optimization.

    Args:
        data_dir: Path to data directory with trade history
        current_params: Current parameter values (for comparison)

    Returns:
        OptimizationResult with optimal parameters, or None on failure.
    """
    try:
        from scipy.optimize import minimize
    except ImportError:
        log.warning("scipy not installed — pip install scipy")
        return None

    trade_outcomes = _load_trade_outcomes(data_dir)
    if trade_outcomes is None or len(trade_outcomes) < 20:
        log.warning("Insufficient trade data for optimization: %s",
                     len(trade_outcomes) if trade_outcomes is not None else "None")
        return None

    # Current params as starting point
    if current_params is None:
        current_params = {
            "confidence_floor": 0.65,
            "chandelier_atr_mult": 2.0,
            "heat_limit_pct": 7.5,
            "kelly_cap": 0.03,
            "spread_veto_pct": 0.30,
        }

    x0 = np.array([current_params.get(name, (PARAM_BOUNDS[name][0] + PARAM_BOUNDS[name][1]) / 2)
                    for name in PARAM_NAMES])
    bounds = [PARAM_BOUNDS[name] for name in PARAM_NAMES]

    try:
        result = minimize(
            _objective_fn,
            x0,
            args=(trade_outcomes,),
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 100, "ftol": 1e-8},
        )

        optimal = {name: float(result.x[i]) for i, name in enumerate(PARAM_NAMES)}

        # Compute improvement
        baseline_sharpe = -_objective_fn(x0, trade_outcomes)
        optimal_sharpe = -result.fun
        improvement = ((optimal_sharpe - baseline_sharpe) / max(abs(baseline_sharpe), 1e-6)) * 100

        opt_result = OptimizationResult(
            optimal_params=optimal,
            objective_value=float(result.fun),
            converged=result.success,
            iterations=result.nit,
            previous_params=current_params,
            improvement_pct=improvement,
        )

        log.info("Optimization %s: Sharpe %.3f → %.3f (+%.1f%%), %d iterations",
                 "converged" if result.success else "FAILED",
                 baseline_sharpe, optimal_sharpe, improvement, result.nit)
        for name, val in optimal.items():
            log.info("  %s: %.4f (was %.4f)", name, val, current_params.get(name, 0))

        return opt_result

    except Exception as e:
        log.error("Optimization failed: %s", str(e)[:200])
        return None


def run_and_save(output_path: Optional[str] = None) -> Optional[OptimizationResult]:
    """Run optimization and save results to JSON."""
    if output_path is None:
        output_path = os.environ.get("AEGIS_DATA_DIR", "/app/data") + "/optimization_result.json"

    result = optimize_parameters()
    if result is None:
        return None

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            **result.to_dict(),
        }, f, indent=2)
    log.info("Optimization results saved: %s", output_path)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [Optimizer] %(levelname)s %(message)s")
    result = run_and_save()
    if result:
        print(f"\nOptimal parameters:")
        for k, v in result.optimal_params.items():
            print(f"  {k}: {v:.4f}")
        print(f"\nImprovement: {result.improvement_pct:+.1f}%")
    else:
        print("Optimization failed (insufficient data or missing dependencies)")
