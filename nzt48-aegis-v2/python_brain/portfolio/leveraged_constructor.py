"""Portfolio Construction for Leveraged Instrument Portfolios — Book 180.

Specialized portfolio optimizer for ISA-constrained leveraged ETP
portfolios. Handles unique challenges of leveraged instruments:
  - Volatility decay (leveraged ETPs lose value in choppy markets)
  - Amplified correlation risk
  - ISA constraints (no shorting, max 40% single position, sum <= 100%)
  - Covariance estimation with Ledoit-Wolf shrinkage

The optimizer constructs minimum-variance portfolios subject to a
target volatility constraint and ISA rules, with returns adjusted
for the volatility drag inherent in leveraged products.

Rebalancing uses a cost-aware schedule that minimizes turnover.

State persisted to /app/data/portfolio/.

Usage:
    from python_brain.portfolio.leveraged_constructor import (
        LeveragedPortfolioOptimizer, LeveragedInstrument,
        CovarianceEstimator,
    )
    instruments = [
        LeveragedInstrument("QQQ3.L", 3, "QQQ", "tech", 0.08),
        LeveragedInstrument("3USL.L", 3, "SPY", "index", 0.05),
    ]
    optimizer = LeveragedPortfolioOptimizer(instruments, target_vol=0.15)
    result = optimizer.optimize(returns, cov)
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("leveraged_constructor")

__all__ = [
    "LeveragedInstrument",
    "CovarianceEstimator",
    "LeveragedPortfolioOptimizer",
]

# ── Constants ──────────────────────────────────────────────────────────

STATE_DIR = Path("/app/data/portfolio")
MAX_SINGLE_POSITION = 0.40   # ISA: max 40% in single instrument
MIN_WEIGHT = 0.0             # ISA: no shorting
MAX_TOTAL_WEIGHT = 1.0       # ISA: sum of weights <= 100%
DEFAULT_TARGET_VOL = 0.15    # 15% annualized target
MIN_REBALANCE_THRESHOLD = 0.02  # 2% drift before rebalancing


# ── Data Classes ──────────────────────────────────────────────────────

@dataclass
class LeveragedInstrument:
    """A leveraged ETP instrument.

    Attributes:
        symbol: Ticker symbol (e.g., 'QQQ3.L').
        leverage: Leverage multiplier (e.g., 3 for 3x).
        underlying: Underlying index/stock.
        sector: Sector classification.
        decay_rate_annual: Annual volatility decay rate (fraction).
    """
    symbol: str
    leverage: int = 3
    underlying: str = ""
    sector: str = ""
    decay_rate_annual: float = 0.05

    def vol_drag(self, realized_vol: float) -> float:
        """Compute annualized volatility drag.

        Vol drag = -0.5 * L * (L - 1) * sigma^2
        For a 3x ETP with 20% vol: -0.5 * 3 * 2 * 0.04 = -12%/year

        Args:
            realized_vol: Annualized realized volatility.

        Returns:
            Volatility drag as negative fraction (e.g., -0.12).
        """
        L = abs(self.leverage)
        return -0.5 * L * (L - 1) * realized_vol ** 2


# ── Covariance Estimator ──────────────────────────────────────────────

class CovarianceEstimator:
    """Robust covariance estimation with Ledoit-Wolf shrinkage.

    Shrinks the sample covariance toward a structured target
    (identity scaled by average variance) to reduce estimation error
    in high-dimensional settings.
    """

    def __init__(self):
        """Initialize covariance estimator."""
        log.info("CovarianceEstimator initialized")

    def shrunk_covariance(self, returns: np.ndarray,
                          shrinkage: float = 0.5) -> np.ndarray:
        """Compute Ledoit-Wolf shrunk covariance matrix.

        Shrinkage target: diagonal matrix of sample variances.
        C_shrunk = (1 - alpha) * C_sample + alpha * F_target

        Args:
            returns: Return matrix, shape (n_obs, n_assets).
            shrinkage: Shrinkage intensity [0, 1].
                       0 = pure sample, 1 = pure target.

        Returns:
            Shrunk covariance matrix, shape (n_assets, n_assets).
        """
        returns = np.asarray(returns, dtype=np.float64)
        if returns.ndim == 1:
            returns = returns.reshape(-1, 1)

        n_obs, n_assets = returns.shape

        if n_obs < 2:
            log.warning("Insufficient observations for covariance: %d", n_obs)
            return np.eye(n_assets)

        # Sample covariance
        demeaned = returns - np.mean(returns, axis=0)
        sample_cov = (demeaned.T @ demeaned) / (n_obs - 1)

        # Shrinkage target: scaled identity (average variance on diagonal)
        avg_var = np.mean(np.diag(sample_cov))
        target = np.eye(n_assets) * avg_var

        # If shrinkage not specified, compute optimal Ledoit-Wolf shrinkage
        if shrinkage < 0:
            shrinkage = self._optimal_shrinkage(demeaned, sample_cov, target)

        shrinkage = float(np.clip(shrinkage, 0.0, 1.0))

        shrunk = (1.0 - shrinkage) * sample_cov + shrinkage * target

        log.info("Covariance shrinkage: alpha=%.3f, n_assets=%d, n_obs=%d",
                 shrinkage, n_assets, n_obs)
        return shrunk

    def effective_covariance(self, instruments: List[LeveragedInstrument],
                             raw_cov: np.ndarray) -> np.ndarray:
        """Adjust covariance for leverage effects.

        Leveraged instruments amplify both variance and correlation.
        C_eff[i,j] = L_i * L_j * C_raw[i,j]

        Args:
            instruments: List of leveraged instruments.
            raw_cov: Raw (underlying) covariance matrix.

        Returns:
            Leverage-adjusted covariance matrix.
        """
        n = len(instruments)
        if raw_cov.shape != (n, n):
            log.warning("Covariance shape mismatch: %s vs %d instruments",
                        raw_cov.shape, n)
            return raw_cov

        leverage_vec = np.array([abs(inst.leverage) for inst in instruments],
                                dtype=np.float64)

        # Outer product of leverages
        leverage_matrix = np.outer(leverage_vec, leverage_vec)
        effective_cov = raw_cov * leverage_matrix

        return effective_cov

    def _optimal_shrinkage(self, demeaned: np.ndarray,
                           sample_cov: np.ndarray,
                           target: np.ndarray) -> float:
        """Compute optimal Ledoit-Wolf shrinkage intensity.

        Based on the Oracle Approximating Shrinkage (OAS) estimator.
        """
        n_obs, n_assets = demeaned.shape
        if n_obs < 3 or n_assets < 2:
            return 0.5

        # Frobenius norms
        diff = sample_cov - target
        rho_num = np.sum(diff ** 2)

        # Approximate optimal shrinkage
        if rho_num < 1e-10:
            return 0.0

        # Simplified: use trace-based estimator
        trace_s2 = np.trace(sample_cov @ sample_cov)
        trace_s = np.trace(sample_cov)

        rho = ((n_obs - 2) / n_obs * trace_s2 + trace_s ** 2) / \
              ((n_obs + 2) * (trace_s2 - trace_s ** 2 / n_assets))

        return float(np.clip(rho, 0.0, 1.0))


# ── Leveraged Portfolio Optimizer ─────────────────────────────────────

class LeveragedPortfolioOptimizer:
    """Portfolio optimizer for leveraged ETP portfolios.

    Finds minimum-variance portfolio weights subject to:
      - Target annualized volatility
      - ISA constraints (no shorting, max 40% single, sum <= 100%)
      - Volatility drag adjustment

    Uses iterative projected gradient descent for constrained optimization.
    """

    def __init__(self, instruments: List[LeveragedInstrument],
                 target_vol: float = DEFAULT_TARGET_VOL,
                 max_position: float = MAX_SINGLE_POSITION):
        """Initialize optimizer.

        Args:
            instruments: List of leveraged instruments.
            target_vol: Target annualized portfolio volatility.
            max_position: Max weight for any single instrument.
        """
        self._instruments = instruments
        self._target_vol = target_vol
        self._max_position = max_position
        self._n_assets = len(instruments)
        self._cov_estimator = CovarianceEstimator()

        log.info("LeveragedPortfolioOptimizer: %d instruments, "
                 "target_vol=%.2f, max_pos=%.2f",
                 self._n_assets, target_vol, max_position)

    def optimize(self, returns: np.ndarray,
                 cov: Optional[np.ndarray] = None) -> Dict[str, Any]:
        """Optimize portfolio weights.

        Args:
            returns: Historical returns, shape (n_obs, n_assets).
            cov: Precomputed covariance. Estimated from returns if None.

        Returns:
            Dict with optimal weights, expected return, portfolio vol,
            and per-instrument details.
        """
        returns = np.asarray(returns, dtype=np.float64)
        if returns.ndim == 1:
            returns = returns.reshape(-1, 1)

        n_obs, n_assets = returns.shape

        if n_assets != self._n_assets:
            log.warning("Return columns (%d) != instruments (%d)",
                        n_assets, self._n_assets)
            n_assets = min(n_assets, self._n_assets)
            returns = returns[:, :n_assets]

        # Estimate covariance if not provided
        if cov is None:
            raw_cov = self._cov_estimator.shrunk_covariance(returns, shrinkage=-1)
        else:
            raw_cov = cov[:n_assets, :n_assets]

        # Adjust for leverage
        eff_cov = self._cov_estimator.effective_covariance(
            self._instruments[:n_assets], raw_cov
        )

        # Adjust returns for volatility drag
        adj_returns = self.decay_adjusted_returns(returns, self._instruments[:n_assets])
        expected_returns = np.mean(adj_returns, axis=0) * 252  # Annualize

        # Optimize: projected gradient descent
        weights = self._optimize_weights(expected_returns, eff_cov)

        # Portfolio statistics
        port_vol = math.sqrt(float(weights @ eff_cov @ weights) * 252)
        port_ret = float(weights @ expected_returns)

        # Scale to target vol if needed
        if port_vol > 0 and port_vol > self._target_vol * 1.05:
            scale = self._target_vol / port_vol
            weights *= scale
            # Re-project to satisfy constraints
            weights = self._project_constraints(weights)
            port_vol = math.sqrt(float(weights @ eff_cov @ weights) * 252)
            port_ret = float(weights @ expected_returns)

        # Build result
        instrument_details = []
        for i in range(n_assets):
            inst = self._instruments[i]
            inst_vol = math.sqrt(float(eff_cov[i, i]) * 252)
            instrument_details.append({
                "symbol": inst.symbol,
                "weight": round(float(weights[i]), 4),
                "leverage": inst.leverage,
                "vol_drag_annual": round(inst.vol_drag(inst_vol / abs(inst.leverage)), 4),
                "effective_vol": round(inst_vol, 4),
            })

        result = {
            "weights": {self._instruments[i].symbol: round(float(weights[i]), 4)
                        for i in range(n_assets)},
            "portfolio_vol": round(port_vol, 4),
            "portfolio_return": round(port_ret, 4),
            "sharpe_estimate": round(port_ret / max(port_vol, 0.01), 2),
            "total_weight": round(float(np.sum(weights)), 4),
            "n_active": int(np.sum(weights > 0.001)),
            "hhi": round(float(np.sum(weights ** 2)), 4),
            "instruments": instrument_details,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        log.info("Optimization result: vol=%.3f, ret=%.3f, n_active=%d, "
                 "total_weight=%.3f",
                 port_vol, port_ret, result["n_active"], result["total_weight"])

        return result

    def _optimize_weights(self, expected_returns: np.ndarray,
                          cov: np.ndarray,
                          n_iter: int = 500,
                          lr: float = 0.01) -> np.ndarray:
        """Projected gradient descent for constrained optimization.

        Minimizes portfolio variance subject to ISA constraints.

        Args:
            expected_returns: Expected annualized returns per asset.
            cov: Covariance matrix (annualized by the caller if needed).
            n_iter: Number of optimization iterations.
            lr: Learning rate.

        Returns:
            Optimal weight vector.
        """
        n = len(expected_returns)

        # Initialize with equal weights (respecting max position)
        w = np.full(n, min(1.0 / n, self._max_position))
        w = self._project_constraints(w)

        # Risk-return tradeoff parameter
        risk_aversion = 2.0

        for iteration in range(n_iter):
            # Gradient of mean-variance objective:
            # min w'Cw - lambda * w'mu
            # grad = 2*Cw - lambda*mu
            grad_risk = 2.0 * cov @ w
            grad_return = -risk_aversion * expected_returns

            grad = grad_risk + grad_return

            # Gradient step
            w_new = w - lr * grad

            # Project onto constraint set
            w_new = self._project_constraints(w_new)

            # Check convergence
            if np.max(np.abs(w_new - w)) < 1e-8:
                break

            w = w_new

        return w

    def _project_constraints(self, w: np.ndarray) -> np.ndarray:
        """Project weights onto the ISA-feasible set.

        Constraints:
          - w_i >= 0 (no shorting)
          - w_i <= max_position (max 40% single)
          - sum(w) <= 1.0 (total <= 100%)

        Args:
            w: Weight vector.

        Returns:
            Projected weight vector satisfying all constraints.
        """
        # Clip negative weights
        w = np.maximum(w, MIN_WEIGHT)

        # Clip individual positions
        w = np.minimum(w, self._max_position)

        # Scale down if total > 1
        total = np.sum(w)
        if total > MAX_TOTAL_WEIGHT:
            w = w * MAX_TOTAL_WEIGHT / total

        return w

    def _isa_constraints(self, weights: np.ndarray) -> bool:
        """Check if weights satisfy all ISA constraints.

        Args:
            weights: Portfolio weight vector.

        Returns:
            True if all constraints are satisfied.
        """
        # No shorting
        if np.any(weights < -1e-8):
            return False

        # Max single position
        if np.any(weights > self._max_position + 1e-8):
            return False

        # Total <= 100%
        if np.sum(weights) > MAX_TOTAL_WEIGHT + 1e-8:
            return False

        return True

    def decay_adjusted_returns(self, raw_returns: np.ndarray,
                               instruments: List[LeveragedInstrument]) -> np.ndarray:
        """Subtract volatility drag from raw returns.

        Leveraged ETPs suffer volatility decay: the daily rebalancing
        creates a drag proportional to L*(L-1)*sigma^2/2.

        Args:
            raw_returns: Raw daily returns, shape (n_obs, n_assets).
            instruments: Corresponding instruments.

        Returns:
            Drag-adjusted returns, shape (n_obs, n_assets).
        """
        adjusted = raw_returns.copy()
        n_assets = min(raw_returns.shape[1], len(instruments))

        for i in range(n_assets):
            inst = instruments[i]
            # Realized vol of this instrument
            realized_vol = float(np.std(raw_returns[:, i]) * math.sqrt(252))
            underlying_vol = realized_vol / max(abs(inst.leverage), 1)

            # Daily drag
            annual_drag = inst.vol_drag(underlying_vol)
            daily_drag = annual_drag / 252

            # Also subtract the fixed decay rate
            daily_fixed_decay = inst.decay_rate_annual / 252

            adjusted[:, i] = raw_returns[:, i] + daily_drag - daily_fixed_decay

        return adjusted

    def rebalancing_schedule(self, current_weights: np.ndarray,
                             target_weights: np.ndarray,
                             cost_model: Optional[Dict[str, float]] = None
                             ) -> List[Dict[str, Any]]:
        """Generate cost-aware rebalancing schedule.

        Determines which trades to execute to move from current to
        target weights, minimizing transaction costs and turnover.

        Args:
            current_weights: Current portfolio weights.
            target_weights: Target optimal weights.
            cost_model: Per-instrument transaction cost (fraction).
                       Defaults to 0.15% per leg.

        Returns:
            List of rebalancing trades sorted by priority.
        """
        n = min(len(current_weights), len(target_weights), self._n_assets)
        diffs = target_weights[:n] - current_weights[:n]

        trades: List[Dict[str, Any]] = []

        for i in range(n):
            diff = float(diffs[i])
            if abs(diff) < MIN_REBALANCE_THRESHOLD:
                continue

            inst = self._instruments[i]
            cost_per_leg = 0.0015  # Default 0.15%
            if cost_model and inst.symbol in cost_model:
                cost_per_leg = cost_model[inst.symbol]

            trade_cost = abs(diff) * cost_per_leg * 2  # Round trip

            # Net benefit = reduction in risk minus cost
            benefit = abs(diff) * 0.01 - trade_cost  # Simplified

            trades.append({
                "symbol": inst.symbol,
                "current_weight": round(float(current_weights[i]), 4),
                "target_weight": round(float(target_weights[i]), 4),
                "delta": round(diff, 4),
                "direction": "buy" if diff > 0 else "sell",
                "estimated_cost": round(trade_cost, 6),
                "priority": round(abs(diff) - trade_cost, 4),
            })

        # Sort by priority (largest beneficial trades first)
        trades.sort(key=lambda t: t["priority"], reverse=True)

        log.info("Rebalancing schedule: %d trades (of %d instruments)",
                 len(trades), n)
        return trades
