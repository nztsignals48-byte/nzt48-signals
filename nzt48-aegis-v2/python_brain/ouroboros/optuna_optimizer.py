"""Optuna Hyperparameter Optimizer — nightly parameter search.

Uses Bayesian optimization (TPE) to find optimal strategy parameters:
  - confidence_floor per entry type
  - chandelier_atr_mult
  - kelly_cap
  - hour_weights

Objective: maximize Sharpe ratio (or profit factor) on rolling 30-day window.
Runs as nightly pipeline step, writes optimized params to
data/optuna_recommendations.json for config_writer consumption.

License: Optuna is MIT.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("optuna_optimizer")

try:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    _HAS_OPTUNA = True
except ImportError:
    _HAS_OPTUNA = False

_DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
_OUTPUT_PATH = _DATA_DIR / "optuna_recommendations.json"
_STUDY_DB = _DATA_DIR / "optuna_study.db"


def _load_trade_history() -> List[Dict[str, Any]]:
    """Load recent trade history for backtesting parameter sets."""
    path = _DATA_DIR / "signal_trade_history.json"
    if not path.exists():
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _simulate_params(trades: List[Dict], params: Dict[str, float]) -> float:
    """Simulate PnL under a parameter set. Returns Sharpe ratio."""
    conf_floor = params.get("confidence_floor", 65)
    kelly_cap = params.get("kelly_cap", 0.20)
    chandelier_mult = params.get("chandelier_atr_mult", 2.0)

    pnls = []
    for t in trades:
        if t.get("confidence", 0) < conf_floor:
            continue  # Would have been filtered out
        pnl = t.get("pnl", 0.0)
        k = min(t.get("kelly_fraction", 0.01), kelly_cap)
        # Simulate impact of wider/tighter stops via chandelier multiplier
        stop_factor = chandelier_mult / 2.0  # Normalized to default=1.0
        adjusted_pnl = pnl * k * stop_factor
        pnls.append(adjusted_pnl)

    if len(pnls) < 10:
        return -999.0  # Too few trades — penalize

    mean_pnl = sum(pnls) / len(pnls)
    var_pnl = sum((p - mean_pnl) ** 2 for p in pnls) / max(len(pnls) - 1, 1)
    std_pnl = var_pnl ** 0.5
    if std_pnl < 1e-9:
        return 0.0
    sharpe = (mean_pnl / std_pnl) * (252 ** 0.5)
    return sharpe


def run_optimization(
    n_trials: int = 50,
    output_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Run Optuna hyperparameter optimization.

    Args:
        n_trials: Number of Bayesian optimization trials.
        output_path: Where to save results JSON.

    Returns:
        Dict with best params and study stats, or None on failure.
    """
    if not _HAS_OPTUNA:
        log.warning("Optuna not installed — optimization skipped")
        return None

    trades = _load_trade_history()
    if len(trades) < 30:
        log.info("Insufficient trades for optimization: %d < 30", len(trades))
        return None

    def objective(trial):
        params = {
            "confidence_floor": trial.suggest_int("confidence_floor", 55, 80),
            "kelly_cap": trial.suggest_float("kelly_cap", 0.01, 0.30, step=0.01),
            "chandelier_atr_mult": trial.suggest_float("chandelier_atr_mult", 1.5, 3.5, step=0.1),
        }
        return _simulate_params(trades, params)

    # Create or load persistent study
    storage = f"sqlite:///{_STUDY_DB}"
    study = optuna.create_study(
        study_name="aegis_nightly",
        direction="maximize",
        storage=storage,
        load_if_exists=True,
    )

    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

    best = study.best_params
    best_value = study.best_value

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_trials_total": len(study.trials),
        "n_trials_this_run": n_trials,
        "n_trades": len(trades),
        "best_sharpe": round(best_value, 4),
        "best_params": {k: round(v, 4) if isinstance(v, float) else v for k, v in best.items()},
    }

    if output_path is None:
        output_path = str(_OUTPUT_PATH)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    log.info("Optuna optimization complete: Sharpe=%.3f, params=%s", best_value, best)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [Optuna] %(levelname)s %(message)s")
    result = run_optimization(n_trials=50)
    if result:
        print(json.dumps(result, indent=2))
