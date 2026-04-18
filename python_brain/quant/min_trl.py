"""Minimum Track Record Length (MinTRL) — Bailey & López de Prado.

MinTRL is the number of observations required for a Sharpe ratio to be
statistically distinguishable from a benchmark Sharpe at a given confidence.

Paired with DSR so we never report a Sharpe without sample-size context.
Consumed by ouroboros_v2_nightly.py and sig2order capital bandit.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class MinTRLResult:
    sharpe: float
    mintrl: float           # observations needed
    observations: int       # observations we have
    satisfied: bool         # observations >= mintrl
    skew: float
    kurt: float


def min_track_record_length(
    sharpe: float,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    benchmark_sharpe: float = 0.0,
    alpha: float = 0.05,
) -> float:
    """Bailey-López de Prado MinTRL formula."""
    if sharpe <= benchmark_sharpe:
        return float("inf")
    # z_alpha — one-sided
    z_alpha = 1.6449 if alpha == 0.05 else (2.3263 if alpha == 0.01 else 1.2816)
    denom = (sharpe - benchmark_sharpe) ** 2
    if denom == 0:
        return float("inf")
    non_normal = 1 - skew * sharpe + ((kurtosis - 1) / 4) * sharpe ** 2
    non_normal = max(non_normal, 0.01)
    return 1.0 + non_normal / denom * z_alpha ** 2


def evaluate(
    sharpe: float,
    n_observations: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    benchmark_sharpe: float = 0.0,
    alpha: float = 0.05,
) -> MinTRLResult:
    """Compute MinTRL + whether we have enough data."""
    trl = min_track_record_length(sharpe, skew, kurtosis, benchmark_sharpe, alpha)
    return MinTRLResult(
        sharpe=sharpe,
        mintrl=trl,
        observations=n_observations,
        satisfied=(n_observations >= trl) if trl != float("inf") else False,
        skew=skew,
        kurt=kurtosis,
    )


def promotion_ready(
    sharpe: float,
    n_observations: int,
    n_trials: int = 1,
    skew: float = 0.0,
    kurt: float = 3.0,
) -> dict:
    """Combined DSR + MinTRL gate — used by capital bandit before promotion."""
    from python_brain.quant.deflated_sharpe import deflated_sharpe_ratio
    result = evaluate(sharpe, n_observations, skew, kurt)
    dsr = deflated_sharpe_ratio(sharpe, n_observations, n_trials, skew, kurt)
    return {
        "dsr": dsr,
        "mintrl": result.mintrl,
        "observations": n_observations,
        "mintrl_satisfied": result.satisfied,
        "dsr_passes": dsr > 0.95,
        "promotion_ready": (dsr > 0.95) and result.satisfied,
    }


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        r = evaluate(sharpe=1.5, n_observations=200)
        print(f"Sharpe 1.5, 200 obs: mintrl={r.mintrl:.0f}, satisfied={r.satisfied}")
        r = evaluate(sharpe=1.5, n_observations=50)
        print(f"Sharpe 1.5, 50 obs: mintrl={r.mintrl:.0f}, satisfied={r.satisfied}")
        r = promotion_ready(sharpe=1.8, n_observations=500, n_trials=10)
        print(f"Promotion: {r}")
        print("OK")
