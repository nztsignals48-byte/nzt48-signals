"""
Almgren-Chriss Optimal Execution

Solves the optimal trading schedule that minimizes expected cost + risk penalty
for liquidating/accumulating a large position.

Reference:
- Almgren & Chriss (2000) "Optimal Execution of Portfolio Transactions"
- Almgren (2003) extensions to nonlinear impact

Inputs:
  X: total shares to trade
  T: time horizon (seconds)
  sigma: volatility (per sqrt time)
  eta: temporary impact coefficient (per share traded per second)
  gamma: permanent impact coefficient (per share)
  lambda_risk: risk aversion (0 = risk-neutral, higher = more urgent)

Output:
  x_k: shares remaining at each time step (schedule)
  t_k: time step boundaries
  n_k: shares to trade in each slice

Key equation:
  x(t) = X * sinh(kappa * (T-t)) / sinh(kappa * T)
  where kappa = sqrt(lambda_risk * sigma^2 / eta)
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class ExecutionSchedule:
    slice_times_s: list[float]       # absolute time (seconds from start) of each slice
    slice_sizes: list[int]            # shares in each slice (signed: + = buy, - = sell)
    remaining_schedule: list[int]     # shares remaining after each slice
    expected_cost_bps: float
    expected_variance_bps2: float
    kappa: float                      # urgency parameter


@dataclass
class MarketParams:
    """Execution market parameters."""
    sigma_per_sqrt_s: float           # volatility per sqrt second (e.g. 1% / sqrt(86400))
    eta_per_share_per_s: float        # temporary impact per share per second
    gamma_per_share: float             # permanent impact per share
    spread_bps: float                  # half-spread in bps

    @classmethod
    def from_ticker_stats(
        cls,
        daily_vol_bps: float,
        adv_shares: float,            # average daily volume in shares
        spread_bps: float,
    ) -> "MarketParams":
        """Estimate parameters from ticker statistics."""
        seconds_per_day = 6.5 * 3600  # US market hours
        sigma = (daily_vol_bps / 10000) / math.sqrt(seconds_per_day)

        # Eta: Almgren et al (2005) suggests eta ~ sigma / (0.01 * ADV) for linear impact
        eta = sigma / (0.01 * max(adv_shares, 1))

        # Gamma: permanent impact ~ 10% of temporary for typical equities
        gamma = 0.1 * eta

        return cls(
            sigma_per_sqrt_s=sigma,
            eta_per_share_per_s=eta,
            gamma_per_share=gamma,
            spread_bps=spread_bps,
        )


def solve_almgren_chriss(
    total_shares: int,
    horizon_s: float,
    num_slices: int,
    params: MarketParams,
    risk_aversion: float = 1e-6,
) -> ExecutionSchedule:
    """
    Solve Almgren-Chriss optimal liquidation.

    Args:
        total_shares: signed total to trade (+ = buy, - = sell)
        horizon_s: time horizon in seconds
        num_slices: number of trading slices
        params: market params (sigma, eta, gamma, spread)
        risk_aversion: lambda (0 = VWAP-like, 1e-4 = aggressive)

    Returns:
        ExecutionSchedule with slice times + sizes
    """
    if total_shares == 0 or horizon_s <= 0 or num_slices < 1:
        return ExecutionSchedule([], [], [], 0.0, 0.0, 0.0)

    abs_shares = abs(total_shares)
    sign = 1 if total_shares > 0 else -1

    # Urgency parameter
    kappa_sq = risk_aversion * params.sigma_per_sqrt_s ** 2 / max(params.eta_per_share_per_s, 1e-12)
    kappa = math.sqrt(max(kappa_sq, 1e-12))

    # Time grid
    dt = horizon_s / num_slices
    t_grid = [k * dt for k in range(num_slices + 1)]

    # Optimal trajectory: x(t) = X * sinh(kappa*(T-t)) / sinh(kappa*T)
    # For very aggressive urgency, kappa*T can be huge. In that limit, the
    # schedule collapses to "trade most upfront". Use numerically stable form.
    kT = kappa * horizon_s

    x_remaining = []
    for t in t_grid:
        if kT < 1e-6:
            # Near risk-neutral: linear (VWAP-like)
            x = abs_shares * (1 - t / horizon_s)
        elif kT > 50:
            # Extreme urgency: almost all traded in first fraction
            # sinh(k(T-t))/sinh(kT) ≈ exp(-kt) for large kT
            x = abs_shares * math.exp(-kappa * t)
        else:
            sinh_kT = math.sinh(kT)
            sinh_term = math.sinh(kappa * (horizon_s - t))
            x = abs_shares * sinh_term / sinh_kT
        x_remaining.append(int(round(x)))

    # Slice sizes (differences)
    slice_sizes = []
    slice_times = []
    for k in range(num_slices):
        size = x_remaining[k] - x_remaining[k + 1]
        if size > 0:
            slice_sizes.append(sign * size)
            slice_times.append(t_grid[k])

    # Expected cost (bps) — Almgren-Chriss closed form
    # E[cost] = gamma * X/2 + spread * X + eta * sum(n_k^2 / tau_k)
    permanent_cost = params.gamma_per_share * abs_shares / 2
    spread_cost = params.spread_bps / 10000 * abs_shares
    temporary_cost = sum(
        params.eta_per_share_per_s * (abs(s) ** 2) / dt
        for s in slice_sizes
    )
    total_cost = permanent_cost + spread_cost + temporary_cost
    expected_cost_bps = total_cost / abs_shares * 10000 if abs_shares > 0 else 0

    # Expected variance
    # Var = sigma^2 * sum(x_k^2 * tau_k)
    variance = sum(
        params.sigma_per_sqrt_s ** 2 * (x ** 2) * dt
        for x in x_remaining[:-1]
    )
    expected_variance_bps2 = variance / (abs_shares ** 2) * 1e8 if abs_shares > 0 else 0

    return ExecutionSchedule(
        slice_times_s=slice_times,
        slice_sizes=slice_sizes,
        remaining_schedule=x_remaining,
        expected_cost_bps=expected_cost_bps,
        expected_variance_bps2=expected_variance_bps2,
        kappa=kappa,
    )


def adaptive_schedule(
    total_shares: int,
    horizon_s: float,
    params: MarketParams,
    urgency: str = "normal",
) -> ExecutionSchedule:
    """
    Build schedule with urgency preset.

    urgency:
      "passive"  = VWAP-like (risk-neutral, spread across horizon)
      "normal"   = balanced
      "aggressive" = front-load
      "immediate" = single slice
    """
    presets = {
        "passive": (20, 0.0),          # 20 slices, no risk penalty
        "normal": (10, 1e-6),
        "aggressive": (5, 1e-4),
        "immediate": (1, 1e-2),
    }
    num_slices, risk_aversion = presets.get(urgency, presets["normal"])
    return solve_almgren_chriss(total_shares, horizon_s, num_slices, params, risk_aversion)


def estimate_impact_cost_bps(
    shares: int,
    adv_shares: float,
    daily_vol_bps: float,
    spread_bps: float = 5.0,
) -> float:
    """
    Quick impact cost estimate for sizing decisions.

    Uses square-root formula (Almgren et al 2005):
      impact_bps = 0.314 * vol * sqrt(shares / ADV) * 10000
    """
    if shares <= 0 or adv_shares <= 0:
        return 0.0

    participation = min(abs(shares) / adv_shares, 1.0)
    daily_vol_frac = daily_vol_bps / 10000

    # Square-root law
    impact = 0.314 * daily_vol_frac * math.sqrt(participation)
    return impact * 10000 + spread_bps


if __name__ == "__main__":
    # Smoke test
    import sys
    if "--smoke" in sys.argv:
        # Trade 10000 shares of AAPL over 60 minutes
        params = MarketParams.from_ticker_stats(
            daily_vol_bps=150.0,      # 1.5% daily vol
            adv_shares=50_000_000,     # 50M ADV
            spread_bps=1.0,
        )
        print(f"Params: sigma={params.sigma_per_sqrt_s:.2e}, eta={params.eta_per_share_per_s:.2e}, gamma={params.gamma_per_share:.2e}")

        schedule = adaptive_schedule(
            total_shares=10000,
            horizon_s=3600,
            params=params,
            urgency="normal",
        )
        print(f"Num slices: {len(schedule.slice_sizes)}")
        print(f"Slice sizes: {schedule.slice_sizes[:5]}...")
        print(f"Expected cost: {schedule.expected_cost_bps:.2f} bps")
        print(f"Expected variance: {schedule.expected_variance_bps2:.2f} bps^2")
        print(f"Kappa: {schedule.kappa:.4f}")

        # Quick impact estimate
        cost = estimate_impact_cost_bps(10000, 50_000_000, 150.0)
        print(f"Quick impact estimate: {cost:.2f} bps")
        print("OK")
