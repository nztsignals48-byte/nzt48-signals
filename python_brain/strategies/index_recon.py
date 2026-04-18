"""index_recon — V3 Gate-2 PF=2.30. Consumes intel.index_recon calendar events."""
from __future__ import annotations

import time
from typing import Optional
from python_brain.strategies.base import Strategy, StrategyContext, StrategyView


class IndexRecon(Strategy):
    name = "index_recon"
    required_intel = ["index_recon.json"]
    exit_method = "EventWindowExit"

    def evaluate(self, ctx: StrategyContext) -> Optional[StrategyView]:
        events = ctx.intel.get("index_recon.json", {}).get("events", [])
        now = time.time()
        for e in events:
            if e.get("ticker") != ctx.ticker:
                continue
            days_to = (e.get("effective_ts", now) - now) / 86400.0
            if 0.0 < days_to < 30.0:
                conv = 0.7
                edge_bps = 80.0 - 2.0 * days_to  # edge decays as event approaches
                risk_bps = max(ctx.indicators.get("atr", 1.0) * 50.0, 10.0)
                return StrategyView(
                    strategy=self.name,
                    ticker=ctx.ticker,
                    default_conviction=conv,
                    edge_estimate_bps=edge_bps,
                    risk_bps=risk_bps,
                    features={
                        "days_to_event": days_to,
                        "event_type": float(hash(e.get("type", "")) % 1000),
                        "atr": ctx.indicators.get("atr", 0.0),
                    },
                    required_intel=self.required_intel,
                )
        return None
