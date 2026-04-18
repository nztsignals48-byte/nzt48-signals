"""Alpha-decay: CUSUM + rolling 60-trade Sharpe per strategy."""
from __future__ import annotations

from math import sqrt
from typing import List


def rolling_sharpe(pnls_bps: List[float]) -> float:
    if len(pnls_bps) < 10:
        return 0.0
    mean = sum(pnls_bps) / len(pnls_bps)
    var = sum((p - mean) ** 2 for p in pnls_bps) / (len(pnls_bps) - 1)
    sd = sqrt(var) if var > 0 else 1.0
    return mean / sd


def cusum_drift(pnls_bps: List[float], k: float = 5.0, h: float = 50.0) -> float:
    s_hi = 0.0
    s_lo = 0.0
    peak = 0.0
    for p in pnls_bps:
        s_hi = max(0.0, s_hi + p - k)
        s_lo = min(0.0, s_lo + p + k)
        peak = max(peak, abs(s_hi), abs(s_lo))
    return peak / h
