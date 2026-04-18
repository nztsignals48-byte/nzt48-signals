"""overnight_return — Lou et al. t-stat 17. Mega-caps only (tight spread)."""
from __future__ import annotations

from typing import Optional
from python_brain.strategies.base import Strategy, StrategyContext, StrategyView


class OvernightReturn(Strategy):
    name = "overnight_return"
    required_intel = []
    exit_method = "NextOpen"

    def evaluate(self, ctx: StrategyContext) -> Optional[StrategyView]:
        # Only fire in the last 15 minutes of US session.
        close_proximity = ctx.indicators.get("close_proximity_min", 99.0)
        if close_proximity > 15.0:
            return None
        # Preference long overnight on positive-drift large-caps.
        momentum_5d = ctx.indicators.get("momentum_5d", 0.0)
        if abs(momentum_5d) < 0.005:
            return None
        conv = 0.6 if momentum_5d > 0 else 0.55
        edge_bps = 12.0 + abs(momentum_5d) * 500.0
        risk_bps = max(ctx.indicators.get("atr", 1.0) * 40.0, 8.0)
        return StrategyView(
            strategy=self.name,
            ticker=ctx.ticker,
            default_conviction=conv,
            edge_estimate_bps=edge_bps,
            risk_bps=risk_bps,
            features={
                "close_proximity_min": close_proximity,
                "momentum_5d": momentum_5d,
                "atr": ctx.indicators.get("atr", 0.0),
            },
            required_intel=self.required_intel,
        )
