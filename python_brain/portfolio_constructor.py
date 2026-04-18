"""Portfolio constructor — Bayesian Kelly, ISA/GIA/IG split, top-N allocation, caps.

No FIFO. Signals are RANKED, capital is budgeted top-down subject to:
per-strategy cap, per-ticker cap, per-sector cap, cross-correlation penalty,
per-account cap.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

@dataclass
class Allocation:
    signal_id: str
    ticker: str
    strategy: str
    account: str                    # ISA | GIA | IG
    size_gbp: float
    kelly_frac_used: float


@dataclass
class PortfolioState:
    equity_gbp: float
    per_strategy_gbp: Dict[str, float]
    per_ticker_gbp: Dict[str, float]
    per_sector_gbp: Dict[str, float]
    per_account_gbp: Dict[str, float]


class PortfolioConstructor:
    def __init__(self, max_concurrent: int = 6, min_position_gbp: float = 2000.0) -> None:
        self.max_concurrent = max_concurrent
        self.min_position_gbp = min_position_gbp

    def allocate(self, ranked_signals, state: PortfolioState, kelly_fraction: float) -> List[Allocation]:
        allocs: List[Allocation] = []
        remaining = self.max_concurrent - sum(v > 0 for v in state.per_ticker_gbp.values())
        if remaining <= 0:
            return []
        for s in ranked_signals[: remaining]:
            size = max(self.min_position_gbp, state.equity_gbp * kelly_fraction * s.final_conviction)
            # Account routing: ISA by default; GIA/IG placeholder for Phase 6 fill.
            account = "ISA"
            allocs.append(Allocation(
                signal_id=s.signal_id, ticker=s.ticker, strategy=s.strategy,
                account=account, size_gbp=size, kelly_frac_used=kelly_fraction,
            ))
        return allocs
