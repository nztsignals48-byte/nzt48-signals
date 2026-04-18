"""Risk arbiter (Python mirror of rust_core/src/risk_arbiter.rs).

Every one of the 16 checks emits a continuous `confidence_delta` in [-50, +5].
NO hard gates (no `return None`). Only sacred halt: 8-consecutive-losses.
Confidence floor (from config) is the sole arbiter of whether a signal executes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from python_brain.engine.portfolio_state import PortfolioState


@dataclass
class RiskEvaluation:
    deltas: Dict[str, float]
    final_confidence: float
    halt: bool


class RiskArbiter:
    def __init__(self, confidence_floor: float = 0.55) -> None:
        self.confidence_floor = confidence_floor

    def evaluate(self, strat_default_conv: float, features: Dict[str, float], state: PortfolioState) -> RiskEvaluation:
        deltas: Dict[str, float] = {}

        spread_bps = features.get("spread_bps", 5.0)
        deltas["spread"] = -min(30.0, max(0.0, (spread_bps - 10.0) * 0.5))

        avg_vol = features.get("avg_volume", 500_000.0)
        deltas["liquidity"] = 0.0 if avg_vol >= 200_000 else -10.0

        corr = features.get("correlation_spy", 0.5)
        deltas["correlation"] = -max(0.0, (abs(corr) - 0.7) * 40.0)

        dd = state.drawdown_pct
        if dd < 0.03:    deltas["drawdown"] = 0.0
        elif dd < 0.05:  deltas["drawdown"] = -10.0
        elif dd < 0.08:  deltas["drawdown"] = -20.0
        else:            deltas["drawdown"] = -30.0

        vol = features.get("rt_hist_vol", 0.2)
        deltas["vol_regime"] = -max(0.0, (vol - 0.40) * 30.0)

        heat_pct = sum(state.per_ticker_gbp.values()) / max(state.equity_gbp, 1.0)
        deltas["heat"] = -max(0.0, (heat_pct - 0.75) * 40.0)

        ticker_pct = state.per_ticker_gbp.get(features.get("ticker", ""), 0.0) / max(state.equity_gbp, 1.0)
        deltas["concentration"] = -max(0.0, (ticker_pct - 0.10) * 50.0)

        overnight_exposure_pct = features.get("overnight_exposure_pct", 0.0)
        deltas["overnight"] = -max(0.0, (overnight_exposure_pct - 0.30) * 40.0)

        deltas["shortable"] = 0.0 if features.get("shortable", True) else -15.0
        deltas["halted"]    = -50.0 if features.get("halted", False) else 0.0

        est_cost_bps = features.get("est_cost_bps", 3.0)
        edge_bps = features.get("edge_bps", 20.0)
        deltas["cost_alpha"] = -max(0.0, (est_cost_bps - edge_bps * 0.25) * 0.4)

        book_pressure = features.get("book_pressure", 0.0)
        deltas["book_pressure"] = -max(0.0, (abs(book_pressure) - 0.5) * 15.0)

        vwap_dist = features.get("vwap_distance_bps", 0.0)
        deltas["vwap_chase"] = -max(0.0, (abs(vwap_dist) - 20.0) * 0.25)

        imb = features.get("book_imbalance", 0.0)
        deltas["imbalance"] = -max(0.0, (abs(imb) - 0.7) * 20.0)

        vol_surge = features.get("vol_surge_z", 0.0)
        deltas["vol_surge"] = -max(0.0, (vol_surge - 4.0) * 10.0)

        kalman_z = features.get("kalman_z", 0.0)
        deltas["kalman_spike"] = -max(0.0, (abs(kalman_z) - 3.0) * 10.0)

        # Final confidence = default + sum(deltas in pp) / 100
        delta_sum_pp = sum(deltas.values())
        final = max(0.0, min(1.0, strat_default_conv + delta_sum_pp / 100.0))

        halt = state.consecutive_losses >= 8
        return RiskEvaluation(deltas=deltas, final_confidence=final, halt=halt)
