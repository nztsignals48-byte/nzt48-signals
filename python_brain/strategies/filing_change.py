"""filing_change_detect — V3 Gate-2 PF=2.49. Consumes intel.filings."""
from __future__ import annotations

from typing import Optional
from python_brain.strategies.base import Strategy, StrategyContext, StrategyView


class FilingChangeDetect(Strategy):
    name = "filing_change_detect"
    required_intel = ["sec_scanner.json"]
    exit_method = "FixedDayExpiry"

    def evaluate(self, ctx: StrategyContext) -> Optional[StrategyView]:
        filings = ctx.intel.get("sec_scanner.json", {}).get("filings", [])
        relevant = [f for f in filings if f.get("ticker") == ctx.ticker]
        if not relevant:
            return None
        change_score = max((f.get("change_score", 0.0) for f in relevant), default=0.0)
        if change_score < 0.25:
            return None
        conv = min(0.85, 0.5 + change_score * 0.7)
        edge_bps = change_score * 40.0
        risk_bps = max(ctx.indicators.get("atr", 1.0) * 50.0, 10.0)
        return StrategyView(
            strategy=self.name,
            ticker=ctx.ticker,
            default_conviction=conv,
            edge_estimate_bps=edge_bps,
            risk_bps=risk_bps,
            features={
                "change_score": change_score,
                "filings_count": float(len(relevant)),
                "atr": ctx.indicators.get("atr", 0.0),
            },
            required_intel=self.required_intel,
        )
