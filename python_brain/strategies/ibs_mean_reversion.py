"""ibs_mean_reversion — V2 +14K PnL/sh. IBS = (close-low)/(high-low)."""
from __future__ import annotations

from typing import Optional
from python_brain.strategies.base import Strategy, StrategyContext, StrategyView


class IbsMeanReversion(Strategy):
    name = "ibs_mean_reversion"
    required_intel = []
    exit_method = "FixedDayExpiry"

    def evaluate(self, ctx: StrategyContext) -> Optional[StrategyView]:
        ibs = ctx.indicators.get("ibs")
        if ibs is None:
            return None
        # Oversold: IBS < 0.2. Overbought: IBS > 0.8.
        if 0.2 <= ibs <= 0.8:
            return None
        conv = 0.55 + 0.4 * (abs(ibs - 0.5) - 0.3)   # 0.55..0.75
        edge_bps = 25.0 + 40.0 * abs(ibs - 0.5)
        risk_bps = max(ctx.indicators.get("atr", 1.0) * 50.0, 10.0)
        return StrategyView(
            strategy=self.name,
            ticker=ctx.ticker,
            default_conviction=conv,
            edge_estimate_bps=edge_bps,
            risk_bps=risk_bps,
            features={
                "ibs": ibs,
                "atr": ctx.indicators.get("atr", 0.0),
                "rsi": ctx.indicators.get("rsi", 50.0),
            },
            required_intel=self.required_intel,
        )
