"""earnings_pattern — V3 Gate-2 PF=1.40. Consumes earnings_whisper."""
from __future__ import annotations

from typing import Optional
from python_brain.strategies.base import Strategy, StrategyContext, StrategyView


class EarningsPattern(Strategy):
    name = "earnings_pattern"
    required_intel = ["earnings_whisper.json"]
    exit_method = "FixedDayExpiry"

    def evaluate(self, ctx: StrategyContext) -> Optional[StrategyView]:
        whispers = ctx.intel.get("earnings_whisper.json", {}).get("whispers", {})
        w = whispers.get(ctx.ticker)
        if not w:
            return None
        surprise_bps = w.get("expected_surprise_bps", 0.0)
        if abs(surprise_bps) < 50.0:
            return None
        conv = min(0.80, 0.5 + abs(surprise_bps) / 500.0)
        edge_bps = abs(surprise_bps) * 0.3
        risk_bps = max(ctx.indicators.get("atr", 1.0) * 50.0, 15.0)
        return StrategyView(
            strategy=self.name,
            ticker=ctx.ticker,
            default_conviction=conv,
            edge_estimate_bps=edge_bps,
            risk_bps=risk_bps,
            features={
                "expected_surprise_bps": surprise_bps,
                "whisper_count": float(w.get("analyst_count", 0)),
                "atr": ctx.indicators.get("atr", 0.0),
            },
            required_intel=self.required_intel,
        )
