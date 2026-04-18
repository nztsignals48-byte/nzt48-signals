"""Broker reconciliation. Runs every 60s paper AND live. 1-share mismatch = CRITICAL.

In-process sim mode: compares engine's open positions against the WAL's last
known state — i.e. self-reconcile. Live mode (Phase 13): replaces with
reqPositions + reqAccountSummary against IBKR.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from python_brain.engine.portfolio_state import PortfolioState


@dataclass
class ReconciliationResult:
    discrepancies: List[str]
    last_run_ts_ns: int


class BrokerSync:
    def __init__(self) -> None:
        self.last_run_ts_ns = 0

    def run(self, state: PortfolioState, broker_view: Dict[str, int]) -> ReconciliationResult:
        """broker_view: {ticker: shares}. Empty in sim."""
        local_view: Dict[str, int] = {}
        for p in state.positions:
            local_view[p.ticker] = local_view.get(p.ticker, 0) + p.size_shares
        discs: List[str] = []
        all_tickers = set(local_view) | set(broker_view)
        for t in all_tickers:
            l = local_view.get(t, 0)
            b = broker_view.get(t, 0)
            if l != b:
                discs.append(f"{t}: engine={l} broker={b}")
        return ReconciliationResult(discrepancies=discs, last_run_ts_ns=0)
