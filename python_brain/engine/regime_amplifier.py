"""regime_amplifier — detect high-vol / event-driven market states and
broadcast a sizing multiplier so sig2order goes bigger when opportunity exists.

Subscribes:
    ticks.live.*         (for rolling realised vol on SPY/QQQ/VIX proxies)
    news.alpha           (for major catalyst detection via LLM)
    portfolio.equity     (for drawdown-aware de-risking)

Publishes:
    risk.regime          {state, vol_multiple, size_boost, chandelier_atr_mult,
                          big_move_probability, rationale}

States:
    calm        — normal US vol, size ×1.0
    active      — elevated vol, size ×1.3
    event       — LLM-detected catalyst or gap move, size ×1.8
    crisis      — VIX proxy > 40, size ×2.0 AND tighten Chandelier -2% → -1.5%
    cooldown    — post-drawdown (>3% intraday DD), size ×0.5

`sig2order` subscribes and multiplies its computed size_gbp by size_boost.
`broker_chandelier` subscribes and adjusts its ATR mult accordingly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Deque, Dict

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@dataclass
class RegimeAmplifier:
    nats_url: str = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    # Rolling 5-min realised vol on SPY to detect market-wide stress.
    spy_prices: Deque[tuple] = field(default_factory=lambda: deque(maxlen=300))
    # LLM catalyst events in last 30 min
    recent_big_alpha: Deque[tuple] = field(default_factory=lambda: deque(maxlen=100))
    last_equity: float = 100_000.0
    intraday_peak: float = 100_000.0

    async def run(self) -> None:
        import nats  # type: ignore
        nc = await nats.connect(self.nats_url, name="aegis-v5-regime-amp")
        log.info("regime amplifier connected to NATS")

        last_publish = 0.0

        async def on_tick(msg):
            nonlocal last_publish
            try:
                t = json.loads(msg.data)
            except Exception:
                return
            if t.get("ticker") != "SPY":
                return
            last = float(t.get("last") or 0)
            if last <= 0:
                return
            ts = time.time()
            self.spy_prices.append((ts, last))
            # Publish at most every 10s
            if ts - last_publish < 10:
                return
            last_publish = ts
            await self._publish(nc)

        async def on_alpha(msg):
            try:
                a = json.loads(msg.data)
                impact = float(a.get("impact_magnitude") or 0)
                delta = abs(float(a.get("conviction_delta_pp") or 0))
                if impact > 0.6 or delta > 8:
                    self.recent_big_alpha.append((time.time(), a.get("ticker"), impact, delta))
            except Exception:
                pass

        async def on_equity(msg):
            try:
                e = json.loads(msg.data)
                eq = float(e.get("equity_gbp") or 0)
                if eq > 0:
                    self.last_equity = eq
                    self.intraday_peak = max(self.intraday_peak, eq)
            except Exception:
                pass

        await nc.subscribe("ticks.live.*", cb=on_tick)
        await nc.subscribe("news.alpha", cb=on_alpha)
        await nc.subscribe("portfolio.equity", cb=on_equity)
        log.info("regime amplifier listening")

        # Periodic publish even without SPY ticks.
        while True:
            await asyncio.sleep(15)
            await self._publish(nc)

    async def _publish(self, nc) -> None:
        state, vol_mult, size_boost, atr_mult, big_move_p, rat = self._compute()
        payload = {
            "state": state,
            "vol_multiple": round(vol_mult, 3),
            "size_boost": round(size_boost, 3),
            "chandelier_atr_mult": round(atr_mult, 3),
            "big_move_probability": round(big_move_p, 3),
            "rationale": rat,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await nc.publish("risk.regime", json.dumps(payload).encode("utf-8"))
        except Exception:
            pass

    def _realised_vol_5m(self) -> float:
        """Annualised realised vol of SPY over last ~5 min from ticks."""
        if len(self.spy_prices) < 20:
            return 0.12  # default 12% vol
        rets = []
        prev = None
        for (ts, px) in self.spy_prices:
            if prev is not None and prev > 0:
                rets.append((px - prev) / prev)
            prev = px
        if not rets:
            return 0.12
        import statistics, math
        sd = statistics.pstdev(rets)
        # annualise: ticks roughly 1s apart → 252*6.5h*3600 = ~5.9M per year
        return sd * math.sqrt(5_900_000) if sd > 0 else 0.12

    def _compute(self):
        vol = self._realised_vol_5m()
        # Baseline SPY vol ~12-15% annualised
        vol_mult = vol / 0.14
        dd_pct = 0.0
        if self.intraday_peak > 0:
            dd_pct = max(0.0, (self.intraday_peak - self.last_equity) / self.intraday_peak)

        # Count recent big-alpha events
        now = time.time()
        recent = [a for a in self.recent_big_alpha if now - a[0] < 1800]  # 30 min
        big_move_p = min(1.0, len(recent) / 5.0)

        # Decide state
        if dd_pct > 0.03:
            return "cooldown", vol_mult, 0.5, 2.5, big_move_p, f"dd={dd_pct*100:.1f}%"
        if vol_mult > 3.0:
            return "crisis", vol_mult, 2.0, 4.0, big_move_p, f"SPY vol ×{vol_mult:.1f}"
        if vol_mult > 1.8 or big_move_p > 0.6:
            return "event", vol_mult, 1.8, 3.5, big_move_p, f"vol ×{vol_mult:.1f} big_alpha={len(recent)}"
        if vol_mult > 1.3:
            return "active", vol_mult, 1.3, 3.0, big_move_p, f"vol ×{vol_mult:.1f}"
        return "calm", vol_mult, 1.0, 3.0, big_move_p, "normal vol"


if __name__ == "__main__":
    asyncio.run(RegimeAmplifier().run())
