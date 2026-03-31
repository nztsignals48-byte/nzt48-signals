"""Market Impact Models for Execution Quality — Book 123.

Implements three market impact models plus a pre-trade estimator:

1. KyleLambda: Permanent price impact (Kyle 1985)
2. AlmgrenChrissImpact: Temporary + permanent impact with optimal execution
3. SquareRootImpact: Barra/Tower model for quick estimates
4. PreTradeImpactEstimator: Combines models for order routing decisions

All costs are expressed in basis points (bps) for consistency with
the AEGIS cost model and TCA analyzer.

Bridge.py integration:
    try:
        from python_brain.execution.market_impact import (
            PreTradeImpactEstimator, AlmgrenChrissImpact,
        )
        _impact_est = PreTradeImpactEstimator()
    except ImportError:
        _impact_est = None

    # Before sending order:
    if _impact_est:
        impact = _impact_est.estimate(
            order_size_gbp=2000, symbol="QQQ3.L",
            adv=500000, sigma=0.025,
        )
        if impact["total_bps"] > 50:
            log.warning("High impact: %s", impact)
        if _impact_est.should_split(order_size=2000, adv=500000):
            # Use TWAP instead of market order
            pass
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger("market_impact")

__all__ = [
    "KyleLambda",
    "AlmgrenChrissImpact",
    "SquareRootImpact",
    "PreTradeImpactEstimator",
]


# ---------------------------------------------------------------------------
# Kyle's Lambda — Permanent Impact
# ---------------------------------------------------------------------------

class KyleLambda:
    """Kyle's Lambda permanent price impact model (Kyle 1985).

    lambda = Cov(return, signed_volume) / Var(signed_volume)

    Permanent impact represents the information content of order flow.
    Higher lambda indicates a more information-rich (and costly) market.

    For AEGIS ISA-sized orders on liquid ETPs, Kyle's lambda is
    typically 1e-8 to 1e-6 (negligible), but useful for monitoring
    deteriorating liquidity.
    """

    @staticmethod
    def estimate(volume: np.ndarray, returns: np.ndarray) -> float:
        """Estimate Kyle's lambda from paired volume/return data.

        Uses OLS regression: r_t = alpha + lambda * v_t + eps_t
        where v_t is signed volume (positive = buy, negative = sell).

        Args:
            volume: Signed volume array (positive = net buying).
            returns: Corresponding return array (same length).

        Returns:
            Estimated lambda. Returns 0.0 if estimation fails.
        """
        volume = np.asarray(volume, dtype=float)
        returns = np.asarray(returns, dtype=float)

        if len(volume) != len(returns):
            log.warning("KyleLambda: volume/returns length mismatch (%d vs %d)",
                        len(volume), len(returns))
            return 0.0

        # Remove NaN/Inf
        mask = np.isfinite(volume) & np.isfinite(returns)
        v = volume[mask]
        r = returns[mask]

        if len(v) < 20:
            log.debug("KyleLambda: insufficient data (%d points)", len(v))
            return 0.0

        var_v = float(np.var(v, ddof=1))
        if var_v < 1e-15:
            return 0.0

        cov_rv = float(np.cov(r, v, ddof=1)[0, 1])
        lambda_est = cov_rv / var_v

        log.debug("KyleLambda: lambda=%.2e (n=%d)", lambda_est, len(v))
        return lambda_est

    @staticmethod
    def permanent_impact(order_size: float, lambda_: float) -> float:
        """Compute permanent price impact from Kyle's lambda.

        Impact = lambda * order_size (in return space).

        Args:
            order_size: Signed order size (shares or notional).
            lambda_: Kyle's lambda coefficient.

        Returns:
            Expected permanent price impact (same units as returns).
        """
        return lambda_ * order_size


# ---------------------------------------------------------------------------
# Almgren-Chriss Impact Model
# ---------------------------------------------------------------------------

class AlmgrenChrissImpact:
    """Almgren-Chriss optimal execution and impact model (2001).

    Decomposes market impact into:
      - Temporary impact: eta * (execution rate) — vanishes after trade
      - Permanent impact: gamma * (execution rate) — persists

    The optimal trajectory minimises E[cost] + lambda * Var[cost]
    (mean-variance trade-off between urgency and impact).

    Attributes:
        sigma: Daily price volatility (as fraction, e.g. 0.02).
        eta: Temporary impact coefficient.
        gamma: Permanent impact coefficient.
        tau: Time interval between trades (in days).
    """

    def __init__(
        self,
        sigma: float = 0.02,
        eta: float = 0.01,
        gamma: float = 0.001,
        tau: float = 1.0 / 78.0,
    ) -> None:
        """Initialise Almgren-Chriss parameters.

        Args:
            sigma: Daily volatility (fraction of price).
            eta: Temporary impact coefficient. Typical: 0.01 for liquid ETPs.
            gamma: Permanent impact coefficient. Typical: 0.001.
            tau: Time between execution slices (default: 5-min = 1/78 day).
        """
        self.sigma = max(sigma, 1e-10)
        self.eta = max(eta, 0.0)
        self.gamma = max(gamma, 0.0)
        self.tau = max(tau, 1e-10)

    def temporary_impact(self, rate: float) -> float:
        """Compute temporary market impact.

        Temporary impact = eta * |rate| (decays immediately after slice).

        Args:
            rate: Execution rate (shares per time interval).

        Returns:
            Temporary impact in price units (fraction of price).
        """
        return self.eta * abs(rate)

    def permanent_impact(self, rate: float) -> float:
        """Compute permanent market impact.

        Permanent impact = gamma * rate (persists after trade).

        Args:
            rate: Execution rate (shares per time interval).

        Returns:
            Permanent impact in price units (fraction of price).
        """
        return self.gamma * abs(rate)

    def optimal_trajectory(
        self,
        X: float,
        T: float,
        n_slices: int,
        risk_aversion: float = 1e-6,
    ) -> np.ndarray:
        """Compute the optimal execution trajectory.

        Minimises E[cost] + risk_aversion * Var[cost] via the closed-form
        Almgren-Chriss solution.

        The trajectory describes the remaining position at each time slice.

        x_j = X * sinh(kappa * (T - t_j)) / sinh(kappa * T)

        where kappa = sqrt(risk_aversion * sigma^2 / (eta * (tau + epsilon)))

        Args:
            X: Total shares to execute (positive = buy).
            T: Total execution time (in days).
            n_slices: Number of execution slices.
            risk_aversion: Risk aversion parameter (lambda in the paper).

        Returns:
            Array of shape (n_slices + 1,) with remaining position at each
            time point. Starts at X, ends at 0.
        """
        if n_slices < 1:
            return np.array([X, 0.0])

        tau = T / n_slices

        # Compute kappa (urgency parameter)
        # kappa^2 = risk_aversion * sigma^2 / (eta / tau + 0.5 * gamma)
        eta_over_tau = self.eta / max(tau, 1e-10)
        denominator = eta_over_tau + 0.5 * self.gamma
        if denominator < 1e-15:
            # Degenerate case: linear trajectory
            return np.linspace(X, 0.0, n_slices + 1)

        kappa_sq = risk_aversion * self.sigma ** 2 / denominator
        kappa = math.sqrt(max(kappa_sq, 0.0))

        if kappa < 1e-10 or T < 1e-10:
            return np.linspace(X, 0.0, n_slices + 1)

        # Time points
        times = np.linspace(0, T, n_slices + 1)

        # Compute trajectory
        sinh_kT = math.sinh(kappa * T)
        if abs(sinh_kT) < 1e-15:
            return np.linspace(X, 0.0, n_slices + 1)

        trajectory = np.zeros(n_slices + 1)
        for j in range(n_slices + 1):
            t_j = times[j]
            trajectory[j] = X * math.sinh(kappa * (T - t_j)) / sinh_kT

        # Enforce boundary conditions
        trajectory[0] = X
        trajectory[-1] = 0.0

        return trajectory

    def expected_cost(
        self,
        trajectory: np.ndarray,
        X: float,
    ) -> float:
        """Compute expected execution cost for a given trajectory.

        Cost = sum over slices of:
          permanent_impact(n_j) * remaining_j + temporary_impact(rate_j) * n_j

        where n_j = x_{j-1} - x_j is the trade size in slice j.

        Args:
            trajectory: Remaining position at each time point (from
                        optimal_trajectory). Shape (n_slices + 1,).
            X: Total initial position.

        Returns:
            Total expected cost as fraction of notional (in bps divide by 1e-4).
        """
        if len(trajectory) < 2:
            return 0.0

        total_cost = 0.0
        n_slices = len(trajectory) - 1

        for j in range(1, n_slices + 1):
            n_j = trajectory[j - 1] - trajectory[j]  # Trade in this slice
            rate_j = abs(n_j) / max(self.tau, 1e-10)

            # Permanent cost applies to remaining position
            remaining = trajectory[j]
            perm_cost = self.gamma * abs(n_j)

            # Temporary cost applies to this trade
            temp_cost = self.eta * abs(rate_j) * abs(n_j)

            total_cost += perm_cost + temp_cost

        # Normalise by total position to get fraction
        if abs(X) > 1e-10:
            return total_cost / abs(X)
        return 0.0

    def total_impact_bps(self, order_size: float, daily_volume: float) -> float:
        """Quick estimate of total impact in basis points.

        Combines temporary and permanent impact at the average
        execution rate assuming uniform execution over 30 minutes.

        Args:
            order_size: Order size in shares.
            daily_volume: Average daily volume in shares.

        Returns:
            Total expected impact in basis points.
        """
        if daily_volume <= 0 or order_size <= 0:
            return 0.0

        participation = order_size / daily_volume
        # Assume 30-min execution = 1/13 of trading day
        rate = order_size * 13.0

        temp = self.eta * rate
        perm = self.gamma * rate
        total_frac = (temp + perm) * participation
        return total_frac * 10000.0  # Convert to bps


# ---------------------------------------------------------------------------
# Square Root Impact (Barra / Tower Model)
# ---------------------------------------------------------------------------

class SquareRootImpact:
    """Square-root market impact model (Barra/Tower).

    impact = sigma * sqrt(volume_pct / daily_vol)

    Empirically validated on large institutional datasets. The square-root
    law is one of the most robust empirical regularities in market
    microstructure.

    For AEGIS ISA-sized orders (typically < 0.1% of ADV), impact
    is usually < 1 bps.
    """

    @staticmethod
    def impact(
        volume_pct: float,
        daily_vol: float,
        sigma: float,
    ) -> float:
        """Compute square-root market impact.

        Args:
            volume_pct: Order size as fraction of ADV (e.g. 0.01 = 1%).
            daily_vol: Average daily volume (shares or notional).
            sigma: Daily volatility (as fraction, e.g. 0.02 = 2%).

        Returns:
            Expected impact as fraction of price (multiply by 10000 for bps).
        """
        if daily_vol <= 0 or volume_pct <= 0:
            return 0.0

        return sigma * math.sqrt(abs(volume_pct))

    @staticmethod
    def impact_bps(
        order_size: float,
        adv: float,
        sigma: float,
    ) -> float:
        """Compute square-root impact directly in basis points.

        Convenience method that handles the participation rate calculation.

        Args:
            order_size: Order size (shares or GBP notional).
            adv: Average daily volume (same units as order_size).
            sigma: Daily volatility (as fraction).

        Returns:
            Impact in basis points.
        """
        if adv <= 0 or order_size <= 0:
            return 0.0

        participation = order_size / adv
        impact_frac = sigma * math.sqrt(participation)
        return impact_frac * 10000.0

    @staticmethod
    def inverse_impact(
        max_impact_bps: float,
        adv: float,
        sigma: float,
    ) -> float:
        """Compute maximum order size for a given impact budget.

        Inverts the square-root formula to find the largest order
        that stays within the impact budget.

        Args:
            max_impact_bps: Maximum acceptable impact in bps.
            adv: Average daily volume.
            sigma: Daily volatility.

        Returns:
            Maximum order size (same units as adv).
        """
        if sigma <= 0 or adv <= 0 or max_impact_bps <= 0:
            return 0.0

        max_impact_frac = max_impact_bps / 10000.0
        # impact = sigma * sqrt(size/adv)
        # size/adv = (impact/sigma)^2
        max_participation = (max_impact_frac / sigma) ** 2
        return max_participation * adv


# ---------------------------------------------------------------------------
# Pre-Trade Impact Estimator
# ---------------------------------------------------------------------------

class PreTradeImpactEstimator:
    """Pre-trade impact estimator combining multiple models.

    Produces a composite impact estimate using:
    1. Square-root model (primary, most robust)
    2. Almgren-Chriss temporary/permanent decomposition
    3. Confidence interval based on model agreement

    Used by the execution layer to decide:
    - Market order vs. TWAP/VWAP
    - Order splitting strategy
    - Timing of execution
    """

    # Tier-specific calibration constants
    IMPACT_TIERS: Dict[str, Dict[str, float]] = {
        "MEGA_CAP": {"eta": 0.005, "gamma": 0.0005, "sigma_mult": 0.8},
        "LARGE_CAP": {"eta": 0.008, "gamma": 0.0008, "sigma_mult": 0.9},
        "LSE_ETP_LIQUID": {"eta": 0.012, "gamma": 0.001, "sigma_mult": 1.0},
        "LSE_ETP_ILLIQUID": {"eta": 0.025, "gamma": 0.002, "sigma_mult": 1.3},
        "DEFAULT": {"eta": 0.015, "gamma": 0.0012, "sigma_mult": 1.1},
    }

    def __init__(self, tier: str = "LSE_ETP_LIQUID") -> None:
        """Initialise with instrument tier.

        Args:
            tier: Instrument liquidity tier for calibration.
        """
        self._tier = tier
        self._tier_params = self.IMPACT_TIERS.get(tier, self.IMPACT_TIERS["DEFAULT"])

    def estimate(
        self,
        order_size_gbp: float,
        symbol: str,
        adv: float,
        sigma: float,
    ) -> Dict[str, float]:
        """Produce a composite pre-trade impact estimate.

        Combines square-root and Almgren-Chriss models, returning
        a breakdown of permanent, temporary, and total impact in bps.

        Args:
            order_size_gbp: Order notional in GBP.
            symbol: Instrument ticker (for logging).
            adv: Average daily volume in GBP.
            sigma: Daily volatility (as fraction).

        Returns:
            Dict with keys:
              - permanent_bps: Estimated permanent impact
              - temporary_bps: Estimated temporary impact
              - total_bps: Total expected impact
              - confidence: Estimate confidence (0.0 - 1.0)
              - participation_pct: Order as % of ADV
              - model_agreement: Agreement between models (0-1)
              - symbol: Instrument ticker
        """
        if adv <= 0 or order_size_gbp <= 0 or sigma <= 0:
            return {
                "permanent_bps": 0.0,
                "temporary_bps": 0.0,
                "total_bps": 0.0,
                "confidence": 0.0,
                "participation_pct": 0.0,
                "model_agreement": 0.0,
                "symbol": symbol,
            }

        participation = order_size_gbp / adv
        sigma_adj = sigma * self._tier_params["sigma_mult"]

        # Model 1: Square-root
        sqrt_impact_bps = SquareRootImpact.impact_bps(order_size_gbp, adv, sigma_adj)

        # Model 2: Almgren-Chriss
        ac = AlmgrenChrissImpact(
            sigma=sigma_adj,
            eta=self._tier_params["eta"],
            gamma=self._tier_params["gamma"],
        )
        ac_impact_bps = ac.total_impact_bps(order_size_gbp, adv)

        # Composite: weighted average (square-root gets more weight as more robust)
        w_sqrt = 0.6
        w_ac = 0.4
        total_bps = w_sqrt * sqrt_impact_bps + w_ac * ac_impact_bps

        # Decompose into permanent/temporary (using AC ratios)
        if ac_impact_bps > 1e-10:
            perm_ratio = self._tier_params["gamma"] / (
                self._tier_params["eta"] + self._tier_params["gamma"]
            )
        else:
            perm_ratio = 0.3  # Default split

        permanent_bps = total_bps * perm_ratio
        temporary_bps = total_bps * (1.0 - perm_ratio)

        # Model agreement: how close are the two estimates?
        if max(sqrt_impact_bps, ac_impact_bps) > 1e-10:
            agreement = 1.0 - abs(sqrt_impact_bps - ac_impact_bps) / max(
                sqrt_impact_bps, ac_impact_bps
            )
        else:
            agreement = 1.0

        # Confidence: based on participation rate and model agreement
        # Low participation = high confidence, high agreement = high confidence
        participation_confidence = 1.0 - min(participation * 10.0, 0.5)
        confidence = 0.5 * participation_confidence + 0.5 * max(agreement, 0.0)

        log.debug(
            "PreTrade %s: total=%.1f bps (perm=%.1f, temp=%.1f), "
            "participation=%.3f%%, confidence=%.2f",
            symbol, total_bps, permanent_bps, temporary_bps,
            participation * 100.0, confidence,
        )

        return {
            "permanent_bps": round(permanent_bps, 2),
            "temporary_bps": round(temporary_bps, 2),
            "total_bps": round(total_bps, 2),
            "confidence": round(float(np.clip(confidence, 0.0, 1.0)), 3),
            "participation_pct": round(participation * 100.0, 4),
            "model_agreement": round(max(agreement, 0.0), 3),
            "symbol": symbol,
        }

    def should_split(
        self,
        order_size: float,
        adv: float,
        max_participation: float = 0.05,
    ) -> bool:
        """Determine whether an order should be split (TWAP/VWAP).

        An order should be split when it represents a significant
        fraction of daily volume. For AEGIS ISA, the threshold is
        5% of ADV (very conservative).

        Args:
            order_size: Order size (shares or GBP).
            adv: Average daily volume (same units).
            max_participation: Maximum single-order participation rate.

        Returns:
            True if order should be split into slices.
        """
        if adv <= 0:
            return True  # No volume data = split for safety

        participation = order_size / adv
        should = participation > max_participation

        if should:
            log.info("Order split recommended: %.2f%% of ADV (threshold=%.2f%%)",
                     participation * 100.0, max_participation * 100.0)

        return should

    def max_order_size(
        self,
        adv: float,
        sigma: float,
        max_impact_bps: float = 10.0,
    ) -> float:
        """Compute the maximum order size for a given impact budget.

        Uses the square-root model inverse to find the largest order
        that stays within the impact budget.

        Args:
            adv: Average daily volume in GBP.
            sigma: Daily volatility.
            max_impact_bps: Impact budget in basis points.

        Returns:
            Maximum order size in GBP.
        """
        sigma_adj = sigma * self._tier_params["sigma_mult"]
        return SquareRootImpact.inverse_impact(max_impact_bps, adv, sigma_adj)
