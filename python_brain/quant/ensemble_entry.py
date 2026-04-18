"""Ensemble entry — aggregates multi-strategy signals on same ticker into one conviction.

When multiple strategies fire on the same ticker within a window, ensemble
combines their convictions (weighted by historical per-strategy PF) into a
single meta-conviction. Reduces redundant orders + improves signal-to-noise.

Consumed by signal_to_order_bridge.py — called before dedupe gate.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class StrategyContribution:
    strategy: str
    conviction: float
    weight: float
    age_s: float


@dataclass
class EnsembleSignal:
    ticker: str
    side: str
    ensemble_conviction: float
    n_contributing: int
    contributions: list
    is_agreeing: bool
    agreement_ratio: float


class EnsembleEntry:
    """Aggregates signals per (ticker, side) within a time window."""

    def __init__(
        self,
        window_s: float = 120.0,
        strategy_weights: dict[str, float] | None = None,
    ):
        self.window_s = window_s
        # Default weights; Ouroboros updates these nightly from PF per strategy
        self.weights = strategy_weights or {
            "SentimentLongShort": 1.0,
            "FilingChangeDetect": 1.2,
            "IndexRecon": 1.1,
            "EarningsPattern": 0.8,
            "OvernightReturn": 0.6,
            "IbsMeanReversion": 0.7,
            "MomentumBurst": 0.7,
        }
        # per (ticker, side) -> list of (strategy, conv, ts)
        self._buffer: dict[tuple[str, str], list[tuple[str, float, float]]] = defaultdict(list)

    def set_weights(self, weights: dict[str, float]) -> None:
        self.weights.update(weights)

    def add_signal(self, ticker: str, side: str, strategy: str, conviction: float) -> None:
        now = time.time()
        key = (ticker, side.upper())
        # Expire old entries
        self._buffer[key] = [
            (s, c, t) for s, c, t in self._buffer[key]
            if now - t <= self.window_s
        ]
        self._buffer[key].append((strategy, float(conviction), now))

    def ensemble(self, ticker: str, side: str) -> EnsembleSignal | None:
        key = (ticker, side.upper())
        now = time.time()
        recent = [
            (s, c, t) for s, c, t in self._buffer.get(key, [])
            if now - t <= self.window_s
        ]
        if not recent:
            return None

        contribs = []
        total_weight = 0.0
        weighted_sum = 0.0
        for strat, conv, ts in recent:
            w = self.weights.get(strat, 0.5)
            contribs.append(StrategyContribution(strat, conv, w, now - ts))
            total_weight += w
            weighted_sum += w * conv

        ensemble_conv = weighted_sum / max(total_weight, 1e-9)
        # Agreement: what fraction of contributing strategies have conv >= 0.5?
        agreeing = sum(1 for _, c, _ in recent if c >= 0.5)
        agreement_ratio = agreeing / len(recent)

        return EnsembleSignal(
            ticker=ticker,
            side=side.upper(),
            ensemble_conviction=ensemble_conv,
            n_contributing=len(recent),
            contributions=contribs,
            is_agreeing=agreement_ratio >= 0.66,
            agreement_ratio=agreement_ratio,
        )

    def purge_stale(self) -> None:
        now = time.time()
        to_del = []
        for key, vals in self._buffer.items():
            kept = [(s, c, t) for s, c, t in vals if now - t <= self.window_s]
            if not kept:
                to_del.append(key)
            else:
                self._buffer[key] = kept
        for k in to_del:
            del self._buffer[k]


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        ee = EnsembleEntry(window_s=300)
        ee.add_signal("AAPL", "BUY", "SentimentLongShort", 0.7)
        ee.add_signal("AAPL", "BUY", "FilingChangeDetect", 0.8)
        ee.add_signal("AAPL", "BUY", "MomentumBurst", 0.6)

        result = ee.ensemble("AAPL", "BUY")
        print(f"AAPL BUY: conv={result.ensemble_conviction:.3f} n={result.n_contributing} "
              f"agree={result.agreement_ratio:.2f}")
        print("OK")
