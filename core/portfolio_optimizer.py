"""
NZT-48 Trading System — Equal Risk Contribution (ERC) Portfolio Optimizer
Wave 2, Item 4: Maillard, Roncalli & Teiletche (2010)

Computes portfolio weights such that each position contributes equally
to total portfolio risk (variance). Unlike equal-weight, ERC accounts
for correlations: low-correlation assets get more weight, high-correlation
clusters get less.

Integration:
    The optimizer runs on the nightly macro refresh cycle and produces
    a weight dict {ticker: weight} that DynamicSizer uses as an
    additional multiplicative scalar on the portfolio heat allocation.

Covariance Source:
    Uses the Ledoit-Wolf (2004) shrinkage covariance matrix from
    uk_isa/correlation_engine.py for regularized estimation.

V7.0 Immutability:
    ERC weights are MULTIPLICATIVE SCALARS on the existing portfolio
    heat budget. They can redistribute capital across tickers but
    CANNOT increase total portfolio risk beyond the 6% heat cap or
    0.75% per-trade constitutional limit. The 6/8 indicator consensus,
    LSE Priority Rule, and Risk Constitution remain untouched.

Reference:
    Maillard, S., Roncalli, T. & Teiletche, J. (2010).
    "The Properties of Equally Weighted Risk Contribution Portfolios."
    Journal of Portfolio Management, 36(4), 60-70.
"""

from __future__ import annotations

import logging
import math
import threading
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np

try:
    import config as cfg
    _HAS_CFG = True
except ImportError:
    _HAS_CFG = False

logger = logging.getLogger("nzt48.core.portfolio_optimizer")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ERC_MAX_ITERATIONS = 500       # Newton-Raphson convergence limit
_ERC_TOLERANCE = 1e-8           # Convergence threshold
_ERC_MIN_WEIGHT = 0.02          # Floor: no ticker below 2% weight
_ERC_DEFAULT_WEIGHT = None      # None = equal-weight fallback


class ERCPortfolioOptimizer:
    """Equal Risk Contribution portfolio optimizer.

    Given N assets and their covariance matrix, solves for weights w
    such that each asset's marginal risk contribution is equal:

        RC_i = w_i * (Sigma @ w)_i = sigma_p^2 / N  for all i

    Where:
        RC_i   = risk contribution of asset i
        w_i    = weight of asset i
        Sigma  = covariance matrix
        sigma_p = portfolio volatility

    The solution is found via the Spinu (2013) convex reformulation:
        minimise  sum_i [ 1/(2N) * log(w_i) - w_i * (Sigma @ w)_i ]
        subject to  sum(w) = 1, w >= 0

    In practice, we use the closed-form Newton-Raphson approach from
    Maillard et al. (2010), Algorithm 1.

    Thread-safe: all mutable state is guarded by ``self._lock``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

        # Cached weights from last optimisation
        self._weights: dict[str, float] = {}
        self._last_optimised_at: datetime | None = None
        self._risk_contributions: dict[str, float] = {}
        self._portfolio_vol: float = 0.0

        logger.info("ERCPortfolioOptimizer initialized")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def optimise(
        self,
        tickers: list[str],
        covariance_matrix: "np.ndarray",
    ) -> dict[str, float]:
        """Compute ERC weights for the given tickers and covariance matrix.

        Args:
            tickers: List of N ticker symbols.
            covariance_matrix: (N, N) positive semi-definite covariance
                matrix. Must be aligned with tickers order.

        Returns:
            Dict mapping ticker → weight (0 to 1), summing to 1.0.
            Returns equal weights on failure.
        """
        if not _HAS_CFG or not cfg.get("v95_erc_allocation_enabled", True):
            return self._equal_weights(tickers)

        n = len(tickers)
        if n == 0:
            return {}
        if n == 1:
            return {tickers[0]: 1.0}

        cov = np.array(covariance_matrix, dtype=np.float64)

        # Validate covariance matrix dimensions
        if cov.shape != (n, n):
            logger.warning(
                "ERC: covariance matrix shape %s does not match %d tickers — "
                "falling back to equal weights",
                cov.shape, n,
            )
            return self._equal_weights(tickers)

        # Ensure symmetry and positive diagonal
        cov = (cov + cov.T) / 2.0
        diag = np.diag(cov)
        if np.any(diag <= 0):
            logger.warning("ERC: non-positive diagonal in covariance — equal weights")
            return self._equal_weights(tickers)

        try:
            weights = self._solve_erc(cov, n)
        except Exception as e:
            logger.warning("ERC: optimisation failed (%s) — equal weights", e)
            return self._equal_weights(tickers)

        # Build result dict
        result = {tickers[i]: round(float(weights[i]), 6) for i in range(n)}

        # Compute risk contributions for diagnostics
        risk_contribs = self._compute_risk_contributions(weights, cov)
        portfolio_vol = float(np.sqrt(weights @ cov @ weights))

        with self._lock:
            self._weights = result
            self._last_optimised_at = datetime.now(timezone.utc)
            self._risk_contributions = {
                tickers[i]: round(float(risk_contribs[i]), 6) for i in range(n)
            }
            self._portfolio_vol = round(portfolio_vol, 6)

        logger.info(
            "ERC: optimised %d assets | weights=%s | "
            "portfolio_vol=%.4f | max_rc_deviation=%.6f",
            n, result, portfolio_vol,
            float(np.max(risk_contribs) - np.min(risk_contribs)),
        )

        return result

    def get_weight(self, ticker: str) -> float:
        """Return the ERC weight for a specific ticker.

        Returns 0.0 if the ticker has no weight assigned, which
        effectively means the DynamicSizer will not scale the
        position (pass-through at the caller's discretion).
        """
        with self._lock:
            return self._weights.get(ticker, 0.0)

    def get_weights(self) -> dict[str, float]:
        """Return the full weights dict."""
        with self._lock:
            return dict(self._weights)

    def get_diagnostics(self) -> dict:
        """Return optimisation diagnostics for dashboard/logging."""
        with self._lock:
            return {
                "weights": dict(self._weights),
                "risk_contributions": dict(self._risk_contributions),
                "portfolio_vol": self._portfolio_vol,
                "last_optimised_at": (
                    self._last_optimised_at.isoformat()
                    if self._last_optimised_at else None
                ),
                "n_assets": len(self._weights),
            }

    # ------------------------------------------------------------------
    # Private: ERC Solver
    # ------------------------------------------------------------------

    @staticmethod
    def _solve_erc(cov: "np.ndarray", n: int) -> "np.ndarray":
        """Solve for ERC weights using iterative bisection.

        Maillard et al. (2010), Algorithm 1:
            1. Start with inverse-vol weights (good initial guess)
            2. Iteratively adjust weights so that marginal risk
               contributions converge to equality
            3. Normalise to sum = 1

        Args:
            cov: (N, N) covariance matrix.
            n: Number of assets.

        Returns:
            (N,) weight vector summing to 1.0.
        """
        # Initial guess: inverse volatility weights
        vols = np.sqrt(np.diag(cov))
        vols = np.maximum(vols, 1e-10)  # prevent division by zero
        w = (1.0 / vols)
        w = w / w.sum()

        for iteration in range(_ERC_MAX_ITERATIONS):
            # Portfolio variance
            sigma_w = cov @ w
            portfolio_var = float(w @ sigma_w)

            if portfolio_var <= 0:
                break

            portfolio_vol = math.sqrt(portfolio_var)

            # Marginal risk contributions: RC_i = w_i * (Sigma @ w)_i
            mrc = w * sigma_w  # element-wise

            # Target: equal contribution = portfolio_var / n
            target_rc = portfolio_var / n

            # Gradient: how much each weight should change
            # Scale each weight by (target_rc / current_rc)
            rc_ratios = np.where(
                mrc > 1e-12,
                target_rc / mrc,
                1.0,
            )

            # Update weights with damping (0.5 for stability)
            w_new = w * np.power(rc_ratios, 0.5)

            # Apply minimum weight floor
            w_new = np.maximum(w_new, _ERC_MIN_WEIGHT)

            # Renormalise
            w_new = w_new / w_new.sum()

            # Check convergence
            delta = float(np.max(np.abs(w_new - w)))
            w = w_new

            if delta < _ERC_TOLERANCE:
                logger.debug(
                    "ERC: converged in %d iterations (delta=%.2e)",
                    iteration + 1, delta,
                )
                break
        else:
            logger.warning(
                "ERC: did not converge after %d iterations (delta=%.2e)",
                _ERC_MAX_ITERATIONS, delta,
            )

        return w

    @staticmethod
    def _compute_risk_contributions(
        w: "np.ndarray",
        cov: "np.ndarray",
    ) -> "np.ndarray":
        """Compute each asset's risk contribution as a fraction.

        RC_i = w_i * (Sigma @ w)_i / sigma_p^2

        Returns array of risk contributions that should sum to ~1.0
        if weights are properly normalised.
        """
        sigma_w = cov @ w
        portfolio_var = float(w @ sigma_w)
        if portfolio_var <= 0:
            return np.ones(len(w)) / len(w)
        mrc = w * sigma_w
        return mrc / portfolio_var

    @staticmethod
    def _equal_weights(tickers: list[str]) -> dict[str, float]:
        """Return equal weights as fallback."""
        n = len(tickers)
        if n == 0:
            return {}
        w = round(1.0 / n, 6)
        return {t: w for t in tickers}
