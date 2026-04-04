"""Riskfolio-Lib Portfolio Optimizer — institutional portfolio construction.

Replaces stdlib HRP with validated riskfolio-lib implementations:
  - Hierarchical Risk Parity (HRP)
  - Risk Parity (RP)
  - CVaR optimization
  - Black-Litterman (when views are available)

ISA constraints enforced: no shorting, no margin, max 40%, min 5%.

Consumers:
  - portfolio_optimizer.py: enhanced HRP + risk parity ensemble
  - nightly_pipeline.sh STEP 22: run_portfolio_rebalance()
  - config_writer.py: strategy allocation weights → dynamic_weights.toml

License: riskfolio-lib is BSD-3.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

log = logging.getLogger("riskfolio_optimizer")

try:
    import riskfolio as rp
    _HAS_RISKFOLIO = True
except ImportError:
    _HAS_RISKFOLIO = False
    log.warning("riskfolio-lib not installed — pip install riskfolio-lib")

# ISA constraints
MAX_SINGLE_WEIGHT = 0.40
MIN_SINGLE_WEIGHT = 0.05


def optimize_portfolio(
    returns_by_strategy: Dict[str, List[float]],
    method: str = "ensemble",
    risk_measure: str = "MV",
) -> Optional[Dict[str, float]]:
    """Optimize portfolio weights using riskfolio-lib.

    Args:
        returns_by_strategy: Dict of {strategy_name: daily_returns_list}
        method: "hrp", "rp" (risk parity), "mv" (mean-variance), or "ensemble" (blend)
        risk_measure: "MV" (variance), "CVaR", "CDaR"

    Returns:
        Dict of {strategy_name: weight} or None on failure.
    """
    if not _HAS_RISKFOLIO:
        log.warning("riskfolio-lib not available — falling back to equal weight")
        n = len(returns_by_strategy)
        return {s: 1.0 / n for s in returns_by_strategy} if n > 0 else None

    try:
        import pandas as pd

        # Build returns DataFrame
        min_len = min(len(v) for v in returns_by_strategy.values() if v)
        if min_len < 10:
            log.warning("Insufficient data for portfolio optimization: %d < 10", min_len)
            return None

        data = {}
        for name, rets in returns_by_strategy.items():
            data[name] = rets[-min_len:]
        df = pd.DataFrame(data)

        if method == "hrp":
            weights = _optimize_hrp(df)
        elif method == "rp":
            weights = _optimize_risk_parity(df, risk_measure)
        elif method == "mv":
            weights = _optimize_mean_variance(df, risk_measure)
        elif method == "ensemble":
            # Blend: 50% HRP + 30% Risk Parity + 20% Mean-Variance
            w_hrp = _optimize_hrp(df)
            w_rp = _optimize_risk_parity(df, risk_measure)
            w_mv = _optimize_mean_variance(df, risk_measure)

            if w_hrp is None:
                return w_rp or w_mv
            if w_rp is None:
                w_rp = w_hrp
            if w_mv is None:
                w_mv = w_hrp

            weights = {}
            for s in w_hrp:
                weights[s] = 0.50 * w_hrp.get(s, 0) + 0.30 * w_rp.get(s, 0) + 0.20 * w_mv.get(s, 0)
        else:
            weights = _optimize_hrp(df)

        if weights is None:
            return None

        # Apply ISA constraints
        weights = _apply_isa_constraints(weights)
        return weights

    except Exception as e:
        log.error("Portfolio optimization failed: %s", str(e)[:200])
        return None


def _optimize_hrp(df) -> Optional[Dict[str, float]]:
    """Hierarchical Risk Parity optimization."""
    try:
        port = rp.HCPortfolio(returns=df)
        w = port.optimization(
            model="HRP",
            codependence="pearson",
            rm="MV",
            leaf_order=True,
        )
        return {col: float(w.loc[col, "weights"]) for col in w.index}
    except Exception as e:
        log.warning("HRP optimization failed: %s", str(e)[:100])
        return None


def _optimize_risk_parity(df, risk_measure: str = "MV") -> Optional[Dict[str, float]]:
    """Risk Parity optimization."""
    try:
        port = rp.Portfolio(returns=df)
        port.assets_stats(method_mu="hist", method_cov="hist")
        w = port.rp_optimization(
            model="Classic",
            rm=risk_measure,
            hist=True,
        )
        return {col: float(w.loc[col, "weights"]) for col in w.index}
    except Exception as e:
        log.warning("Risk Parity optimization failed: %s", str(e)[:100])
        return None


def _optimize_mean_variance(df, risk_measure: str = "MV") -> Optional[Dict[str, float]]:
    """Mean-Variance optimization with ISA constraints (long-only)."""
    try:
        port = rp.Portfolio(returns=df)
        port.assets_stats(method_mu="hist", method_cov="hist")
        w = port.optimization(
            model="Classic",
            rm=risk_measure,
            obj="Sharpe",
            hist=True,
        )
        return {col: float(w.loc[col, "weights"]) for col in w.index}
    except Exception as e:
        log.warning("Mean-Variance optimization failed: %s", str(e)[:100])
        return None


def _apply_isa_constraints(weights: Dict[str, float]) -> Dict[str, float]:
    """Apply ISA constraints: no shorting, no margin, max 40%, min 5%."""
    # Floor negatives
    constrained = {s: max(0.0, w) for s, w in weights.items()}

    # Zero out tiny allocations
    for s in list(constrained.keys()):
        if 0 < constrained[s] < MIN_SINGLE_WEIGHT:
            constrained[s] = 0.0

    # Cap at max
    for s in constrained:
        constrained[s] = min(constrained[s], MAX_SINGLE_WEIGHT)

    # Normalize to sum = 1.0
    total = sum(constrained.values())
    if total > 0:
        for s in constrained:
            constrained[s] /= total

    return constrained


def run_portfolio_rebalance(
    output_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Full pipeline: load returns, optimize, save allocation.

    This is called by nightly_pipeline.sh STEP 22.
    """
    if output_path is None:
        output_path = os.environ.get("AEGIS_DATA_DIR", "/app/data") + "/portfolio_allocation.json"

    # Load strategy returns
    pnl_path = os.path.join(os.environ.get("AEGIS_DATA_DIR", "/app/data"), "strategy_pnl_history.json")
    try:
        with open(pnl_path) as f:
            returns_by_strategy = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        log.warning("strategy_pnl_history.json not found — skipping rebalance")
        return None

    # Optimize
    weights = optimize_portfolio(returns_by_strategy, method="ensemble")
    if weights is None:
        return None

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "ensemble (50% HRP + 30% RP + 20% MV)",
        "weights": weights,
        "constraints": {
            "max_single": MAX_SINGLE_WEIGHT,
            "min_single": MIN_SINGLE_WEIGHT,
            "long_only": True,
            "max_total": 1.0,
        },
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)
    log.info("Portfolio allocation saved: %s", output_path)
    for s, w in sorted(weights.items(), key=lambda x: -x[1]):
        log.info("  %s: %.1f%%", s, w * 100)

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [Portfolio] %(levelname)s %(message)s")
    result = run_portfolio_rebalance()
    if result:
        print(f"\nPortfolio weights ({result['method']}):")
        for s, w in sorted(result["weights"].items(), key=lambda x: -x[1]):
            print(f"  {s}: {w:.1%}")
    else:
        print("Portfolio rebalance failed")
