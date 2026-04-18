"""ConvictionEngine — WIRED into server.py. This was V4's defining dead-code bug.

Every LLM output is clipped to [-30, +15] pp per LLM forbidden zones rule.
Ranking is by conviction * edge_estimate / risk; top-N emitted to portfolio_constructor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional


@dataclass
class StrategyView:
    signal_id: str
    strategy: str
    ticker: str
    default_conviction: float      # in [0, 1]
    edge_estimate_bps: float
    risk_bps: float
    features: dict

@dataclass
class RankedSignal(StrategyView):
    final_conviction: float = 0.0
    llm_delta_pp: float = 0.0
    score: float = 0.0
    rank: int = -1


class ConvictionEngine:
    def __init__(self, max_per_batch: int = 5, min_composite_score: float = 3.0) -> None:
        self.max_per_batch = max_per_batch
        self.min_composite_score = min_composite_score

    def rank_signals(self, views: Iterable[StrategyView], llm_deltas: Optional[dict[str, float]] = None) -> List[RankedSignal]:
        llm_deltas = llm_deltas or {}
        out: List[RankedSignal] = []
        for v in views:
            d = llm_deltas.get(v.signal_id, 0.0)
            # LLM forbidden zones: clip to [-30, +15] pp.
            d = max(-30.0, min(15.0, d))
            final = max(0.0, min(1.0, v.default_conviction + d / 100.0))
            score = final * v.edge_estimate_bps / max(v.risk_bps, 1.0)
            out.append(RankedSignal(
                signal_id=v.signal_id, strategy=v.strategy, ticker=v.ticker,
                default_conviction=v.default_conviction, edge_estimate_bps=v.edge_estimate_bps,
                risk_bps=v.risk_bps, features=v.features,
                final_conviction=final, llm_delta_pp=d, score=score,
            ))
        out = [s for s in out if s.score >= self.min_composite_score]
        out.sort(key=lambda s: s.score, reverse=True)
        for i, s in enumerate(out[: self.max_per_batch]):
            s.rank = i
        return out[: self.max_per_batch]
