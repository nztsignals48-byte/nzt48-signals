"""Bayesian Kelly — EWMA of last 60 trade outcomes."""
from __future__ import annotations

from typing import List


def bayesian_kelly(trade_pnls_bps: List[float], ewma_lambda: float = 0.05) -> float:
    if not trade_pnls_bps:
        return 0.15
    w = 1.0
    num = 0.0
    den = 0.0
    for p in reversed(trade_pnls_bps[-60:]):
        num += w * p
        den += w
        w *= (1 - ewma_lambda)
    mean_bps = num / den if den else 0.0
    # Very conservative Kelly fraction from mean edge.
    frac = max(0.02, min(0.30, 0.15 + mean_bps / 5000.0))
    return frac
