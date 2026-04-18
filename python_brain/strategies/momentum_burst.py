"""Momentum burst — pure price-signal strategy.

Zero intel dependencies. Fires on:
  - Price broke above 5-bar high AND
  - RSI(14) in 55..75 (momentum zone, not overbought) AND
  - ATR > 0 (liquid) AND
  - (optional) Kalman residual positive (price above filter)

Designed for rotator-discovered tickers. Lets the 30k universe actually trade.

Conviction scales with:
  - distance above breakout
  - RSI strength (55 → 0.6, 75 → 0.8 conv)
"""
from __future__ import annotations

from typing import Optional

from python_brain.strategies.base import Strategy, StrategyContext, StrategyView


class MomentumBurst(Strategy):
    name = "momentum_burst"
    required_intel = []  # none!
    exit_method = "ChandelierStop"  # routed to v2 via runner

    def evaluate(self, ctx: StrategyContext) -> Optional[StrategyView]:
        bars = ctx.bars.get("1m", [])
        if len(bars) < 6:
            return None
        closes = [b.get("close", 0.0) for b in bars[-6:]]
        if not all(c > 0 for c in closes):
            return None
        prior_high = max(closes[:-1])
        last = closes[-1]
        if last <= prior_high:
            return None

        rsi = ctx.indicators.get("rsi") or 0.0
        atr = ctx.indicators.get("atr") or 0.0
        kalman_z = ctx.quant.get("kalman_z") or 0.0

        # Filter: don't chase parabolic, don't fire in low vol.
        if not (55 <= rsi <= 75):
            return None
        if atr <= 0:
            return None
        if kalman_z < -0.5:  # price way below Kalman filter → fading, not breakout
            return None

        # Breakout strength (in multiples of ATR).
        breakout_atr = (last - prior_high) / max(atr, last * 0.0005)
        if breakout_atr < 0.25 or breakout_atr > 3.0:
            # too weak (noise) or too extended (chase)
            return None

        # Conviction: RSI 55 → 0.60, 65 → 0.70, 75 → 0.80, capped.
        rsi_conv = 0.55 + (rsi - 55) * 0.015
        breakout_conv_boost = min(0.10, breakout_atr * 0.04)
        conv = max(0.55, min(0.85, rsi_conv + breakout_conv_boost))

        # Edge estimate: 1.5× ATR move targeted; risk 1× ATR.
        edge_bps = (atr / last) * 1.5 * 1e4 if last > 0 else 50.0
        risk_bps = (atr / last) * 1.0 * 1e4 if last > 0 else 30.0

        return StrategyView(
            strategy=self.name,
            ticker=ctx.ticker,
            default_conviction=conv,
            edge_estimate_bps=float(edge_bps),
            risk_bps=float(risk_bps),
            features={
                "rsi": rsi,
                "atr": atr,
                "kalman_z": kalman_z,
                "breakout_atr_mult": breakout_atr,
                "prior_5bar_high": prior_high,
            },
            required_intel=[],
        )
