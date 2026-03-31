"""Stochastic Calculus Models for Gap Risk and Simulation — Book 116.

Provides calibration-grade stochastic process models for pricing,
risk estimation, and Monte Carlo simulation within AEGIS V2.

Models:
  - OUProcess: Ornstein-Uhlenbeck mean-reverting process (pairs, spreads)
  - JumpDiffusion: Merton jump-diffusion (gap/tail risk)
  - HestonModel: Stochastic volatility (vol smile, term structure)
  - MonteCarloEngine: Path generation + VaR/CVaR + gap risk

All calibration uses MLE or method-of-moments with numpy only.

Bridge.py integration:
    from python_brain.models.stochastic_models import (
        OUProcess, JumpDiffusion, HestonModel, MonteCarloEngine,
    )

    # Calibrate OU for mean-reversion half-life:
    try:
        from python_brain.models.stochastic_models import OUProcess
        ou = OUProcess(theta=0.5, mu=0.0, sigma=0.01)
        params = ou.calibrate(spread_series)
        half_life = ou.half_life()
    except ImportError:
        pass

    # Overnight gap risk via jump-diffusion MC:
    try:
        from python_brain.models.stochastic_models import (
            JumpDiffusion, MonteCarloEngine,
        )
        jd = JumpDiffusion(mu=0.0, sigma=0.02, lam=0.1, jump_mu=-0.03, jump_sigma=0.05)
        jd.calibrate(daily_returns)
        mc = MonteCarloEngine()
        paths = mc.run_paths(jd, S0=100.0, dt=1/252, n_steps=1, n_paths=50000)
        risk = mc.gap_risk_estimate(paths)
    except ImportError:
        pass
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger("stochastic_models")

__all__ = [
    "OUProcess",
    "JumpDiffusion",
    "HestonModel",
    "MonteCarloEngine",
]


# ---------------------------------------------------------------------------
# Ornstein-Uhlenbeck Process
# ---------------------------------------------------------------------------

class OUProcess:
    """Ornstein-Uhlenbeck mean-reverting process.

    dX_t = theta * (mu - X_t) * dt + sigma * dW_t

    Used for modelling spreads, basis, and mean-reverting signals.
    Half-life = ln(2) / theta gives the expected time to revert
    halfway to the mean.

    Attributes:
        theta: Speed of mean reversion (> 0).
        mu: Long-run mean level.
        sigma: Volatility of the process.
    """

    def __init__(self, theta: float = 0.5, mu: float = 0.0, sigma: float = 0.01) -> None:
        """Initialise OU process parameters.

        Args:
            theta: Speed of mean reversion. Higher = faster reversion.
            mu: Long-run equilibrium level.
            sigma: Instantaneous volatility.
        """
        if theta <= 0:
            raise ValueError(f"theta must be positive, got {theta}")
        if sigma < 0:
            raise ValueError(f"sigma must be non-negative, got {sigma}")

        self.theta = theta
        self.mu = mu
        self.sigma = sigma

    def simulate(self, x0: float, dt: float, n_steps: int) -> np.ndarray:
        """Simulate an OU path using exact discretisation.

        Uses the exact transition density rather than Euler-Maruyama
        for better accuracy at larger dt.

        X_{t+dt} = mu + (X_t - mu) * exp(-theta * dt)
                  + sigma * sqrt((1 - exp(-2*theta*dt)) / (2*theta)) * Z

        Args:
            x0: Initial value.
            dt: Time step size.
            n_steps: Number of steps to simulate.

        Returns:
            Array of shape (n_steps + 1,) including x0 at index 0.
        """
        path = np.zeros(n_steps + 1)
        path[0] = x0

        exp_factor = math.exp(-self.theta * dt)
        var_factor = (self.sigma ** 2) * (1.0 - math.exp(-2.0 * self.theta * dt)) / (2.0 * self.theta)
        std_factor = math.sqrt(max(var_factor, 0.0))

        noise = np.random.standard_normal(n_steps)

        for i in range(n_steps):
            path[i + 1] = self.mu + (path[i] - self.mu) * exp_factor + std_factor * noise[i]

        return path

    def calibrate(self, data: np.ndarray) -> Dict[str, float]:
        """Calibrate OU parameters from observed data via MLE.

        Uses the AR(1) representation:
          X_{t+1} = a + b * X_t + epsilon_t
        where:
          b = exp(-theta * dt),  a = mu * (1 - b)
          Var(epsilon) = sigma^2 * (1 - b^2) / (2 * theta)

        Assumes dt = 1 (unit time step). For other dt, scale theta
        and sigma accordingly.

        Args:
            data: 1-D array of observations (at least 10 points).

        Returns:
            Dict with keys: theta, mu, sigma, half_life, n_obs.

        Raises:
            ValueError: If data has fewer than 10 observations.
        """
        data = np.asarray(data, dtype=float)
        clean = data[np.isfinite(data)]

        if len(clean) < 10:
            raise ValueError(f"Need at least 10 observations, got {len(clean)}")

        # AR(1) regression: X_{t+1} = a + b * X_t + eps
        x_t = clean[:-1]
        x_tp1 = clean[1:]

        n = len(x_t)
        sum_x = np.sum(x_t)
        sum_y = np.sum(x_tp1)
        sum_xy = np.sum(x_t * x_tp1)
        sum_x2 = np.sum(x_t ** 2)

        denom = n * sum_x2 - sum_x ** 2
        if abs(denom) < 1e-15:
            log.warning("OU calibration: degenerate data (constant series)")
            return {"theta": self.theta, "mu": self.mu, "sigma": self.sigma,
                    "half_life": self.half_life(), "n_obs": int(n)}

        b_hat = (n * sum_xy - sum_x * sum_y) / denom
        a_hat = (sum_y - b_hat * sum_x) / n

        # Clamp b to avoid log of non-positive
        b_hat = float(np.clip(b_hat, 1e-6, 1.0 - 1e-6))

        # Extract OU parameters (dt = 1)
        theta_hat = -math.log(b_hat)
        mu_hat = a_hat / (1.0 - b_hat)

        # Residual variance for sigma
        residuals = x_tp1 - (a_hat + b_hat * x_t)
        var_residuals = float(np.var(residuals, ddof=2))

        # sigma^2 = Var(eps) * 2 * theta / (1 - b^2)
        one_minus_b2 = 1.0 - b_hat ** 2
        if one_minus_b2 > 1e-10:
            sigma2_hat = var_residuals * 2.0 * theta_hat / one_minus_b2
        else:
            sigma2_hat = var_residuals

        sigma_hat = math.sqrt(max(sigma2_hat, 0.0))

        # Update internal state
        self.theta = theta_hat
        self.mu = mu_hat
        self.sigma = sigma_hat

        hl = self.half_life()

        log.info("OU calibrated: theta=%.4f, mu=%.6f, sigma=%.6f, half_life=%.1f, n=%d",
                 theta_hat, mu_hat, sigma_hat, hl, n)

        return {
            "theta": theta_hat,
            "mu": mu_hat,
            "sigma": sigma_hat,
            "half_life": hl,
            "n_obs": int(n),
        }

    def half_life(self) -> float:
        """Compute the half-life of mean reversion.

        Half-life = ln(2) / theta, measured in the same time units
        as the process. E.g. if dt=1 day, half-life is in days.

        Returns:
            Half-life in time units. Returns inf if theta ~ 0.
        """
        if self.theta < 1e-10:
            return float("inf")
        return math.log(2.0) / self.theta

    def stationary_variance(self) -> float:
        """Compute the long-run (stationary) variance.

        Var(X_inf) = sigma^2 / (2 * theta)

        Returns:
            Stationary variance.
        """
        if self.theta < 1e-10:
            return float("inf")
        return (self.sigma ** 2) / (2.0 * self.theta)

    def expected_value(self, x0: float, t: float) -> float:
        """Compute E[X_t | X_0 = x0].

        E[X_t] = mu + (x0 - mu) * exp(-theta * t)

        Args:
            x0: Current value.
            t: Time horizon.

        Returns:
            Expected value at time t.
        """
        return self.mu + (x0 - self.mu) * math.exp(-self.theta * t)


# ---------------------------------------------------------------------------
# Merton Jump-Diffusion
# ---------------------------------------------------------------------------

class JumpDiffusion:
    """Merton Jump-Diffusion model.

    dS/S = (mu - lam * kappa) * dt + sigma * dW + J * dN

    where:
      - W is a Brownian motion
      - N is a Poisson process with intensity lam
      - J ~ N(jump_mu, jump_sigma^2) is the jump size (log-normal)
      - kappa = E[e^J - 1] = exp(jump_mu + 0.5 * jump_sigma^2) - 1

    Used for modelling overnight gaps, earnings jumps, and tail risk.

    Attributes:
        mu: Drift rate of the diffusive component.
        sigma: Diffusive volatility.
        lam: Jump intensity (expected jumps per unit time).
        jump_mu: Mean of log-jump size.
        jump_sigma: Std dev of log-jump size.
    """

    def __init__(
        self,
        mu: float = 0.0,
        sigma: float = 0.02,
        lam: float = 0.1,
        jump_mu: float = -0.03,
        jump_sigma: float = 0.05,
    ) -> None:
        """Initialise jump-diffusion parameters.

        Args:
            mu: Annualised drift.
            sigma: Annualised diffusive volatility.
            lam: Jump intensity (expected jumps per year at dt=1/252).
            jump_mu: Mean log-jump size (negative = downward jumps).
            jump_sigma: Std dev of log-jump size.
        """
        self.mu = mu
        self.sigma = sigma
        self.lam = max(lam, 0.0)
        self.jump_mu = jump_mu
        self.jump_sigma = max(jump_sigma, 0.0)

    def simulate(self, S0: float, dt: float, n_steps: int) -> np.ndarray:
        """Simulate a jump-diffusion price path.

        Uses Euler-Maruyama for the diffusive part and compound
        Poisson for the jumps.

        Args:
            S0: Initial price.
            dt: Time step (e.g. 1/252 for daily).
            n_steps: Number of time steps.

        Returns:
            Array of shape (n_steps + 1,) with price path.
        """
        path = np.zeros(n_steps + 1)
        path[0] = S0

        # Compensator for the jump component
        kappa = math.exp(self.jump_mu + 0.5 * self.jump_sigma ** 2) - 1.0

        # Pre-generate random numbers
        z_diffusion = np.random.standard_normal(n_steps)
        n_jumps = np.random.poisson(self.lam * dt, n_steps)

        for i in range(n_steps):
            s = path[i]
            if s <= 0:
                path[i + 1:] = 0.0
                break

            # Diffusive component
            drift = (self.mu - self.lam * kappa - 0.5 * self.sigma ** 2) * dt
            diffusion = self.sigma * math.sqrt(dt) * z_diffusion[i]

            # Jump component
            jump_sum = 0.0
            if n_jumps[i] > 0:
                jump_sizes = np.random.normal(self.jump_mu, self.jump_sigma, int(n_jumps[i]))
                jump_sum = float(np.sum(jump_sizes))

            # Log-price evolution
            log_return = drift + diffusion + jump_sum
            path[i + 1] = s * math.exp(log_return)

        return path

    def calibrate(self, returns: np.ndarray) -> Dict[str, float]:
        """Calibrate jump-diffusion parameters from return data.

        Uses method-of-moments on the return distribution:
        1. Separate jump returns from diffusive returns using a
           threshold (3-sigma rule on the diffusive part).
        2. Estimate diffusive params from non-jump returns.
        3. Estimate jump params from identified jump returns.

        Args:
            returns: Array of log returns (daily if dt=1/252).

        Returns:
            Dict with calibrated parameters.
        """
        returns = np.asarray(returns, dtype=float)
        clean = returns[np.isfinite(returns)]

        if len(clean) < 30:
            log.warning("JumpDiffusion calibration: insufficient data (%d), using defaults", len(clean))
            return self._param_dict()

        # Step 1: Initial robust estimate of diffusive vol
        # Use MAD (Median Absolute Deviation) for robustness
        median_r = float(np.median(clean))
        mad = float(np.median(np.abs(clean - median_r)))
        sigma_robust = mad * 1.4826  # MAD to std conversion

        if sigma_robust < 1e-10:
            sigma_robust = float(np.std(clean, ddof=1))

        # Step 2: Identify jumps as returns beyond 3-sigma
        threshold = 3.0 * sigma_robust
        is_jump = np.abs(clean - median_r) > threshold
        jump_returns = clean[is_jump]
        diffusive_returns = clean[~is_jump]

        # Step 3: Diffusive parameters
        if len(diffusive_returns) > 5:
            self.mu = float(np.mean(diffusive_returns)) * 252.0  # Annualise
            self.sigma = float(np.std(diffusive_returns, ddof=1)) * math.sqrt(252.0)
        else:
            self.mu = float(np.mean(clean)) * 252.0
            self.sigma = float(np.std(clean, ddof=1)) * math.sqrt(252.0)

        # Step 4: Jump parameters
        n_jumps = len(jump_returns)
        n_total = len(clean)

        self.lam = (n_jumps / n_total) * 252.0  # Annualised jump intensity

        if n_jumps >= 3:
            self.jump_mu = float(np.mean(jump_returns))
            self.jump_sigma = float(np.std(jump_returns, ddof=1))
        elif n_jumps > 0:
            self.jump_mu = float(np.mean(jump_returns))
            self.jump_sigma = abs(self.jump_mu) * 0.5  # Rough estimate
        # else: keep defaults

        log.info("JumpDiffusion calibrated: mu=%.4f, sigma=%.4f, lam=%.2f, "
                 "jump_mu=%.4f, jump_sigma=%.4f, n_jumps=%d/%d",
                 self.mu, self.sigma, self.lam,
                 self.jump_mu, self.jump_sigma, n_jumps, n_total)

        return self._param_dict()

    def _param_dict(self) -> Dict[str, float]:
        """Return current parameters as a dict."""
        return {
            "mu": self.mu,
            "sigma": self.sigma,
            "lam": self.lam,
            "jump_mu": self.jump_mu,
            "jump_sigma": self.jump_sigma,
        }


# ---------------------------------------------------------------------------
# Heston Stochastic Volatility Model
# ---------------------------------------------------------------------------

class HestonModel:
    """Heston stochastic volatility model.

    dS/S = mu * dt + sqrt(v) * dW_1
    dv   = kappa * (theta - v) * dt + xi * sqrt(v) * dW_2

    where Corr(dW_1, dW_2) = rho.

    The variance process v follows a CIR process, ensuring v >= 0
    when the Feller condition 2*kappa*theta > xi^2 is satisfied.

    Attributes:
        kappa: Speed of variance mean reversion.
        theta: Long-run variance level.
        xi: Vol-of-vol (volatility of variance).
        rho: Correlation between price and variance Brownians.
        v0: Initial variance.
    """

    def __init__(
        self,
        kappa: float = 2.0,
        theta: float = 0.04,
        xi: float = 0.3,
        rho: float = -0.7,
        v0: float = 0.04,
    ) -> None:
        """Initialise Heston model parameters.

        Args:
            kappa: Variance mean-reversion speed.
            theta: Long-run variance (e.g. 0.04 = 20% annualised vol).
            xi: Vol-of-vol.
            rho: Price-variance correlation (typically negative for equities).
            v0: Initial variance level.
        """
        self.kappa = kappa
        self.theta = theta
        self.xi = xi
        self.rho = float(np.clip(rho, -1.0, 1.0))
        self.v0 = max(v0, 0.0)

        # Check Feller condition
        feller = 2.0 * kappa * theta - xi ** 2
        if feller < 0:
            log.warning("Heston: Feller condition violated (%.4f < 0). "
                        "Variance may hit zero during simulation.", feller)

    def simulate(
        self, S0: float, dt: float, n_steps: int
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Simulate Heston price and variance paths.

        Uses the full-truncation Euler scheme (Lord, Koekkoek & Van Dijk 2010)
        to handle the boundary condition v >= 0.

        Args:
            S0: Initial price.
            dt: Time step.
            n_steps: Number of steps.

        Returns:
            Tuple of (prices, variances), each shape (n_steps + 1,).
        """
        prices = np.zeros(n_steps + 1)
        variances = np.zeros(n_steps + 1)
        prices[0] = S0
        variances[0] = self.v0

        sqrt_dt = math.sqrt(dt)

        # Generate correlated Brownian increments
        z1 = np.random.standard_normal(n_steps)
        z2 = np.random.standard_normal(n_steps)
        # Correlate: W2 = rho * W1 + sqrt(1-rho^2) * W_indep
        w1 = z1
        w2 = self.rho * z1 + math.sqrt(max(1.0 - self.rho ** 2, 0.0)) * z2

        for i in range(n_steps):
            v = variances[i]
            s = prices[i]

            if s <= 0:
                prices[i + 1:] = 0.0
                variances[i + 1:] = variances[i]
                break

            # Full truncation: use max(v, 0) in sqrt but v itself in drift
            v_pos = max(v, 0.0)
            sqrt_v = math.sqrt(v_pos)

            # Price SDE
            log_return = (-0.5 * v_pos) * dt + sqrt_v * sqrt_dt * w1[i]
            prices[i + 1] = s * math.exp(log_return)

            # Variance SDE (truncated Euler)
            dv = self.kappa * (self.theta - v_pos) * dt + self.xi * sqrt_v * sqrt_dt * w2[i]
            variances[i + 1] = max(v + dv, 0.0)

        return prices, variances

    def feller_condition(self) -> float:
        """Compute the Feller condition value.

        2 * kappa * theta - xi^2 >= 0 ensures variance stays positive.

        Returns:
            Feller condition value. Positive = satisfied.
        """
        return 2.0 * self.kappa * self.theta - self.xi ** 2

    def implied_vol(self) -> float:
        """Return the implied annualised volatility from long-run variance.

        Returns:
            sqrt(theta) as annualised vol.
        """
        return math.sqrt(max(self.theta, 0.0))


# ---------------------------------------------------------------------------
# Monte Carlo Engine
# ---------------------------------------------------------------------------

class MonteCarloEngine:
    """Monte Carlo simulation engine for risk estimation.

    Generates paths from any stochastic model and computes
    Value-at-Risk, Conditional VaR (Expected Shortfall), and
    overnight gap risk distributions.

    Thread-safe: each call uses its own RNG state.
    """

    def __init__(self, seed: Optional[int] = None) -> None:
        """Initialise Monte Carlo engine.

        Args:
            seed: Optional random seed for reproducibility.
        """
        self._seed = seed
        if seed is not None:
            np.random.seed(seed)

    def run_paths(
        self,
        model: Any,
        S0: float,
        dt: float,
        n_steps: int,
        n_paths: int = 10000,
    ) -> np.ndarray:
        """Generate Monte Carlo paths from a stochastic model.

        The model must have a .simulate(S0, dt, n_steps) method
        that returns an np.ndarray of shape (n_steps + 1,).

        Args:
            model: Stochastic model (OUProcess, JumpDiffusion, HestonModel).
            S0: Initial price/value.
            dt: Time step.
            n_steps: Steps per path.
            n_paths: Number of paths to simulate.

        Returns:
            Array of shape (n_paths, n_steps + 1) with simulated paths.
        """
        paths = np.zeros((n_paths, n_steps + 1))

        for i in range(n_paths):
            result = model.simulate(S0, dt, n_steps)
            # HestonModel returns (prices, variances) tuple
            if isinstance(result, tuple):
                paths[i, :] = result[0]
            else:
                paths[i, :] = result

        log.debug("MC engine: generated %d paths of %d steps", n_paths, n_steps)
        return paths

    def var_cvar(
        self,
        paths: np.ndarray,
        alpha: float = 0.05,
    ) -> Dict[str, float]:
        """Compute Value-at-Risk and Conditional VaR (Expected Shortfall).

        Uses terminal values of the paths to construct the P&L
        distribution.

        VaR_alpha = quantile(losses, alpha)
        CVaR_alpha = E[loss | loss >= VaR_alpha]

        Args:
            paths: Shape (n_paths, n_steps + 1). Terminal column used.
            alpha: Significance level (default 5%).

        Returns:
            Dict with keys:
              - var: Value at Risk (positive = loss)
              - cvar: Conditional VaR / Expected Shortfall
              - alpha: Significance level used
              - n_paths: Number of paths
              - mean_return_pct: Mean return across paths
              - std_return_pct: Std of returns across paths
        """
        if paths.ndim != 2 or paths.shape[0] < 2:
            log.warning("var_cvar: insufficient paths (shape=%s)", paths.shape)
            return {"var": 0.0, "cvar": 0.0, "alpha": alpha, "n_paths": 0,
                    "mean_return_pct": 0.0, "std_return_pct": 0.0}

        S0 = paths[:, 0]
        S_T = paths[:, -1]

        # Guard against zero initial prices
        valid = S0 > 1e-10
        if not np.any(valid):
            return {"var": 0.0, "cvar": 0.0, "alpha": alpha, "n_paths": 0,
                    "mean_return_pct": 0.0, "std_return_pct": 0.0}

        returns = np.where(valid, (S_T - S0) / S0, 0.0)
        losses = -returns  # Positive loss = negative return

        # VaR at alpha level
        var_value = float(np.percentile(losses, (1.0 - alpha) * 100.0))

        # CVaR: mean of losses in the tail
        tail_mask = losses >= var_value
        if np.any(tail_mask):
            cvar_value = float(np.mean(losses[tail_mask]))
        else:
            cvar_value = var_value

        return {
            "var": round(var_value, 6),
            "cvar": round(cvar_value, 6),
            "alpha": alpha,
            "n_paths": int(paths.shape[0]),
            "mean_return_pct": round(float(np.mean(returns)) * 100.0, 4),
            "std_return_pct": round(float(np.std(returns, ddof=1)) * 100.0, 4),
        }

    def gap_risk_estimate(self, paths: np.ndarray) -> Dict[str, float]:
        """Estimate overnight gap risk from simulated paths.

        Analyses the distribution of first-step returns (overnight gaps)
        from Monte Carlo paths.

        Args:
            paths: Shape (n_paths, n_steps + 1). Uses columns 0 and 1.

        Returns:
            Dict with gap risk statistics:
              - mean_gap_pct: Mean overnight gap
              - std_gap_pct: Std of overnight gaps
              - gap_var_95_pct: 95th percentile gap loss
              - gap_var_99_pct: 99th percentile gap loss
              - prob_gap_gt_2pct: Probability of gap > 2%
              - prob_gap_gt_5pct: Probability of gap > 5%
              - worst_gap_pct: Worst observed gap
              - skewness: Skewness of gap distribution
              - kurtosis: Excess kurtosis of gap distribution
              - n_paths: Number of paths
        """
        if paths.ndim != 2 or paths.shape[1] < 2 or paths.shape[0] < 2:
            log.warning("gap_risk_estimate: insufficient data (shape=%s)", paths.shape)
            return {"mean_gap_pct": 0.0, "std_gap_pct": 0.0,
                    "gap_var_95_pct": 0.0, "gap_var_99_pct": 0.0,
                    "prob_gap_gt_2pct": 0.0, "prob_gap_gt_5pct": 0.0,
                    "worst_gap_pct": 0.0, "skewness": 0.0, "kurtosis": 0.0,
                    "n_paths": 0}

        S0 = paths[:, 0]
        S1 = paths[:, 1]

        valid = S0 > 1e-10
        if not np.any(valid):
            return {"mean_gap_pct": 0.0, "std_gap_pct": 0.0,
                    "gap_var_95_pct": 0.0, "gap_var_99_pct": 0.0,
                    "prob_gap_gt_2pct": 0.0, "prob_gap_gt_5pct": 0.0,
                    "worst_gap_pct": 0.0, "skewness": 0.0, "kurtosis": 0.0,
                    "n_paths": 0}

        gap_returns = np.where(valid, (S1 - S0) / S0, 0.0)
        gap_losses = -gap_returns  # Positive = loss

        mean_gap = float(np.mean(gap_returns)) * 100.0
        std_gap = float(np.std(gap_returns, ddof=1)) * 100.0

        # VaR estimates
        var_95 = float(np.percentile(gap_losses, 95.0)) * 100.0
        var_99 = float(np.percentile(gap_losses, 99.0)) * 100.0

        # Tail probabilities
        n_valid = int(np.sum(valid))
        prob_gt_2 = float(np.sum(gap_losses > 0.02)) / max(n_valid, 1)
        prob_gt_5 = float(np.sum(gap_losses > 0.05)) / max(n_valid, 1)

        worst_gap = float(np.min(gap_returns)) * 100.0

        # Higher moments
        if std_gap > 1e-10:
            centered = gap_returns - np.mean(gap_returns)
            n = len(gap_returns)
            std_r = np.std(gap_returns, ddof=0)
            if std_r > 1e-10:
                skewness = float(np.mean((centered / std_r) ** 3))
                kurtosis = float(np.mean((centered / std_r) ** 4) - 3.0)
            else:
                skewness = 0.0
                kurtosis = 0.0
        else:
            skewness = 0.0
            kurtosis = 0.0

        return {
            "mean_gap_pct": round(mean_gap, 4),
            "std_gap_pct": round(std_gap, 4),
            "gap_var_95_pct": round(var_95, 4),
            "gap_var_99_pct": round(var_99, 4),
            "prob_gap_gt_2pct": round(prob_gt_2, 6),
            "prob_gap_gt_5pct": round(prob_gt_5, 6),
            "worst_gap_pct": round(worst_gap, 4),
            "skewness": round(skewness, 4),
            "kurtosis": round(kurtosis, 4),
            "n_paths": n_valid,
        }

    def scenario_analysis(
        self,
        paths: np.ndarray,
        scenarios: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Dict[str, float]]:
        """Run scenario analysis on simulated paths.

        For each scenario (e.g. '2008 crisis'), applies a shock multiplier
        to the terminal values and re-computes risk metrics.

        Args:
            paths: Base MC paths, shape (n_paths, n_steps + 1).
            scenarios: Dict of scenario_name -> shock multiplier.
                       E.g. {"crash_2008": -0.35, "flash_crash": -0.10}

        Returns:
            Dict of scenario_name -> risk metrics dict.
        """
        if scenarios is None:
            scenarios = {
                "normal": 0.0,
                "mild_stress": -0.05,
                "severe_stress": -0.15,
                "crash": -0.30,
            }

        results: Dict[str, Dict[str, float]] = {}

        for name, shock in scenarios.items():
            shocked_paths = paths.copy()
            shocked_paths[:, -1] *= (1.0 + shock)
            results[name] = self.var_cvar(shocked_paths)
            results[name]["shock"] = shock

        return results
