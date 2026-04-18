"""
CVaR-Aware Stop Placement

Places stop-loss orders such that expected-shortfall (CVaR) of the worst alpha%
of outcomes equals a specified budget.

Reference:
- Rockafellar & Uryasev (2000)
- Alexander et al. (2006) "CVaR-Optimal Trading"
- Bertsimas & Lauprete (2002) "Shortfall as a risk measure"

Difference vs ATR stop:
- ATR stop: stops out at N * ATR regardless of distribution
- CVaR stop: stops out at the quantile such that E[loss | loss > stop] = budget

Critical when tail risk matters (earnings, macro events, low-liquidity assets).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class CVaRStopResult:
    stop_price: float
    stop_distance_bps: float
    var_bps: float                         # Value-at-Risk
    cvar_bps: float                         # Expected shortfall at stop
    coverage: float                         # probability of stop triggering
    method: str


def historical_cvar_stop(
    entry_price: float,
    side: str,
    historical_returns_bps: np.ndarray,   # adverse moves from entry, in bps (+ = against us)
    cvar_budget_bps: float = 50.0,         # max acceptable expected shortfall
    alpha: float = 0.05,                   # 5% tail
) -> CVaRStopResult:
    """
    Place stop such that expected shortfall equals budget.

    Algorithm:
    1. Compute alpha-quantile VaR
    2. If CVaR > budget, tighten stop
    3. If CVaR < budget, loosen stop
    4. Binary search on stop distance
    """
    if len(historical_returns_bps) < 20:
        # Fallback: 2 sigma stop
        sigma = np.std(historical_returns_bps) if len(historical_returns_bps) > 0 else 100.0
        stop_distance_bps = 2 * sigma
    else:
        # Sort adverse moves descending
        sorted_moves = np.sort(historical_returns_bps)[::-1]

        # Initial VaR
        var_idx = int(alpha * len(sorted_moves))
        var_bps = sorted_moves[var_idx] if var_idx < len(sorted_moves) else sorted_moves[-1]

        # CVaR at VaR
        tail = sorted_moves[:var_idx + 1]
        cvar_bps = tail.mean() if len(tail) > 0 else var_bps

        # If CVaR exceeds budget, we need tighter stop (higher up in tail)
        if cvar_bps > cvar_budget_bps:
            # Binary search: find smaller index where CVaR meets budget
            lo, hi = 0, var_idx
            while lo < hi:
                mid = (lo + hi) // 2
                if mid >= len(sorted_moves):
                    break
                tail_mid = sorted_moves[:mid + 1]
                cvar_mid = tail_mid.mean() if len(tail_mid) > 0 else sorted_moves[mid]
                if cvar_mid > cvar_budget_bps:
                    hi = mid
                else:
                    lo = mid + 1
            var_idx = max(0, lo)
            var_bps = sorted_moves[var_idx] if var_idx < len(sorted_moves) else sorted_moves[-1]

        stop_distance_bps = var_bps

    # Cap within reasonable bounds (20-500 bps)
    stop_distance_bps = max(20.0, min(stop_distance_bps, 500.0))

    # Compute stop price
    if side == "BUY":
        stop_price = entry_price * (1 - stop_distance_bps / 10000)
    else:
        stop_price = entry_price * (1 + stop_distance_bps / 10000)

    # Recompute final CVaR at this stop
    if len(historical_returns_bps) > 0:
        above_stop = historical_returns_bps[historical_returns_bps > stop_distance_bps]
        final_cvar = above_stop.mean() if len(above_stop) > 0 else stop_distance_bps
        coverage = len(above_stop) / len(historical_returns_bps)
    else:
        final_cvar = stop_distance_bps
        coverage = alpha

    return CVaRStopResult(
        stop_price=stop_price,
        stop_distance_bps=stop_distance_bps,
        var_bps=stop_distance_bps,
        cvar_bps=final_cvar,
        coverage=coverage,
        method="historical",
    )


def parametric_cvar_stop(
    entry_price: float,
    side: str,
    volatility_bps: float,
    cvar_budget_bps: float = 50.0,
    alpha: float = 0.05,
) -> CVaRStopResult:
    """
    Gaussian CVaR stop (fast approximation).

    For N(0, sigma): CVaR_alpha = sigma * phi(z_alpha) / alpha
    """
    import math
    # Inverse normal CDF at (1 - alpha)
    z = 1.6449 if alpha == 0.05 else (2.3263 if alpha == 0.01 else 1.2816)
    var_bps = z * volatility_bps
    pdf_z = math.exp(-z * z / 2) / math.sqrt(2 * math.pi)
    cvar_bps = volatility_bps * pdf_z / alpha

    # If CVaR exceeds budget, scale down
    if cvar_bps > cvar_budget_bps:
        scale = cvar_budget_bps / cvar_bps
        var_bps *= scale
        cvar_bps = cvar_budget_bps

    var_bps = max(20.0, min(var_bps, 500.0))

    if side == "BUY":
        stop_price = entry_price * (1 - var_bps / 10000)
    else:
        stop_price = entry_price * (1 + var_bps / 10000)

    return CVaRStopResult(
        stop_price=stop_price,
        stop_distance_bps=var_bps,
        var_bps=var_bps,
        cvar_bps=cvar_bps,
        coverage=alpha,
        method="parametric",
    )


def dynamic_cvar_stop(
    entry_price: float,
    side: str,
    current_price: float,
    time_elapsed_s: float,
    historical_returns_bps: np.ndarray,
    cvar_budget_bps: float = 50.0,
    alpha: float = 0.05,
    trailing: bool = True,
) -> CVaRStopResult:
    """
    Dynamic CVaR stop that trails favorable moves.

    If price moves favorably, ratchet stop up (for BUY).
    """
    # Base CVaR stop from entry
    base_stop = historical_cvar_stop(
        entry_price, side, historical_returns_bps, cvar_budget_bps, alpha
    )

    if not trailing:
        return base_stop

    # If in profit, trail the stop
    if side == "BUY" and current_price > entry_price:
        # Ratchet: stop_price = max(base, current * (1 - stop_distance / 10000))
        trailed = current_price * (1 - base_stop.stop_distance_bps / 10000)
        if trailed > base_stop.stop_price:
            base_stop.stop_price = trailed
    elif side == "SELL" and current_price < entry_price:
        trailed = current_price * (1 + base_stop.stop_distance_bps / 10000)
        if trailed < base_stop.stop_price:
            base_stop.stop_price = trailed

    return base_stop


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        rng = np.random.default_rng(42)

        # Simulate 500 historical adverse moves (bps)
        # Mix: normal noise + occasional fat tail
        normal_moves = np.abs(rng.normal(0, 30, 450))
        tail_moves = np.abs(rng.normal(0, 120, 50))
        adverse = np.concatenate([normal_moves, tail_moves])

        # Historical CVaR stop
        stop = historical_cvar_stop(
            entry_price=100.0,
            side="BUY",
            historical_returns_bps=adverse,
            cvar_budget_bps=80,
            alpha=0.05,
        )
        print(f"Historical CVaR stop:")
        print(f"  Stop price: {stop.stop_price:.4f}")
        print(f"  Distance: {stop.stop_distance_bps:.2f} bps")
        print(f"  VaR: {stop.var_bps:.2f} bps")
        print(f"  CVaR: {stop.cvar_bps:.2f} bps (budget 80)")
        print(f"  Coverage: {stop.coverage:.2%}")

        # Parametric (Gaussian)
        stop_p = parametric_cvar_stop(
            entry_price=100.0,
            side="BUY",
            volatility_bps=30,
            cvar_budget_bps=80,
        )
        print(f"\nParametric CVaR stop:")
        print(f"  Stop price: {stop_p.stop_price:.4f}")
        print(f"  Distance: {stop_p.stop_distance_bps:.2f} bps")
        print(f"  CVaR: {stop_p.cvar_bps:.2f} bps")

        # Dynamic trailing
        stop_d = dynamic_cvar_stop(
            entry_price=100.0,
            side="BUY",
            current_price=102.0,
            time_elapsed_s=300,
            historical_returns_bps=adverse,
            cvar_budget_bps=80,
        )
        print(f"\nDynamic trailing CVaR stop (price at 102.0):")
        print(f"  Stop price: {stop_d.stop_price:.4f} (trailed)")
        print(f"  Distance from current: {(102.0 - stop_d.stop_price) * 100:.2f} bps")
        print("OK")
