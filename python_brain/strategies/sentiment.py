"""sentiment_long_short — V3 Gate-2 PF=3.68. Consumes intel.news."""
from __future__ import annotations

from typing import Optional
from python_brain.strategies.base import Strategy, StrategyContext, StrategyView


class SentimentLongShort(Strategy):
    name = "sentiment_long_short"
    required_intel = ["news_reactor.json"]
    exit_method = "ChandelierStop"

    def evaluate(self, ctx: StrategyContext) -> Optional[StrategyView]:
        news = ctx.intel.get("news_reactor.json", {})
        events = news.get("events", [])
        # Select events for this ticker.
        relevant = [e for e in events if e.get("ticker") == ctx.ticker]
        if not relevant:
            return None
        score = sum(e.get("score", 0.0) for e in relevant) / len(relevant)
        if abs(score) < 0.3:
            return None
        conv = min(0.9, 0.5 + abs(score) * 0.5)
        edge_bps = abs(score) * 25.0
        risk_bps = max(ctx.indicators.get("atr", 1.0) * 50.0, 10.0)
        return StrategyView(
            strategy=self.name,
            ticker=ctx.ticker,
            default_conviction=conv,
            edge_estimate_bps=edge_bps,
            risk_bps=risk_bps,
            features={
                "sentiment_score": score,
                "events_count": float(len(relevant)),
                "atr": ctx.indicators.get("atr", 0.0),
                "regime_steady": ctx.regime_probs[0] if ctx.regime_probs else 0.0,
            },
            required_intel=self.required_intel,
        )
