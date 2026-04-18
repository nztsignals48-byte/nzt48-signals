"""Options flow signal — consumes opt_call_oi / opt_put_oi / opt_implied_vol
from ticks.live.* and publishes directional signals to signals.options_flow.

Key patterns:
- Sudden put/call ratio spike > 2.5σ → bearish signal
- Implied vol surge > 30% same-day → vol-expansion signal
- Call OI buildup (multi-bar) → bullish positioning signal

Consumed by: strategy ensemble (ensemble_entry), Ouroboros (feeds features
into meta-labeler retrain).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass

import numpy as np

try:
    from nats.aio.client import Client as NATS
except ImportError:
    NATS = None


log = logging.getLogger("opt-flow")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@dataclass
class OptionsSnapshot:
    ticker: str
    ts: float
    call_oi: float
    put_oi: float
    call_vol: float
    put_vol: float
    implied_vol: float

    @property
    def put_call_oi_ratio(self) -> float:
        return self.put_oi / max(self.call_oi, 1)

    @property
    def put_call_vol_ratio(self) -> float:
        return self.put_vol / max(self.call_vol, 1)


class OptionsFlowAnalyzer:
    def __init__(self, window_size: int = 60):
        self.window = window_size
        self.history: dict[str, deque[OptionsSnapshot]] = defaultdict(lambda: deque(maxlen=window_size))

    def add(self, snap: OptionsSnapshot):
        self.history[snap.ticker].append(snap)

    def analyze(self, ticker: str) -> dict | None:
        h = list(self.history.get(ticker, []))
        if len(h) < 10:
            return None
        latest = h[-1]
        pc_ratios = [s.put_call_oi_ratio for s in h]
        ivs = [s.implied_vol for s in h if s.implied_vol > 0]

        mean_pc = np.mean(pc_ratios)
        std_pc = np.std(pc_ratios)
        pc_z = (latest.put_call_oi_ratio - mean_pc) / max(std_pc, 1e-6)

        iv_change = 0.0
        if len(ivs) >= 10:
            old_iv = np.mean(ivs[:5])
            new_iv = np.mean(ivs[-5:])
            iv_change = (new_iv - old_iv) / max(old_iv, 1e-6)

        # Signal logic
        signal_side = None
        conviction = 0.0
        rationale = ""

        if pc_z > 2.5:
            signal_side = "SELL"  # put buildup → bearish
            conviction = min(0.5 + pc_z * 0.1, 0.9)
            rationale = f"put/call OI z={pc_z:.2f}"
        elif pc_z < -2.5:
            signal_side = "BUY"  # call buildup → bullish
            conviction = min(0.5 + abs(pc_z) * 0.1, 0.9)
            rationale = f"call/put OI z={pc_z:.2f}"
        elif iv_change > 0.30:
            signal_side = "SELL"  # vol expansion often precedes drop
            conviction = 0.6
            rationale = f"IV surge {iv_change:.1%}"

        return {
            "ticker": ticker,
            "ts": latest.ts,
            "put_call_oi_ratio": latest.put_call_oi_ratio,
            "put_call_oi_z": pc_z,
            "iv_change_pct": iv_change,
            "signal_side": signal_side,
            "conviction": conviction,
            "rationale": rationale,
        }


async def main():
    if NATS is None:
        return
    analyzer = OptionsFlowAnalyzer()
    url = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    nc = NATS()
    await nc.connect(servers=[url])

    async def on_tick(msg):
        try:
            d = json.loads(msg.data)
            ticker = d.get("ticker") or d.get("symbol")
            if not ticker:
                return
            call_oi = d.get("opt_call_oi", 0) or 0
            put_oi = d.get("opt_put_oi", 0) or 0
            if call_oi + put_oi <= 0:
                return  # no options data
            snap = OptionsSnapshot(
                ticker=ticker,
                ts=d.get("ts") or time.time(),
                call_oi=float(call_oi),
                put_oi=float(put_oi),
                call_vol=float(d.get("opt_call_vol", 0) or 0),
                put_vol=float(d.get("opt_put_vol", 0) or 0),
                implied_vol=float(d.get("opt_implied_vol", 0) or 0),
            )
            analyzer.add(snap)
            result = analyzer.analyze(ticker)
            if result and result["signal_side"]:
                await nc.publish("signals.options_flow", json.dumps(result).encode())
                log.info("options flow: %s %s conv=%.2f (%s)",
                        result["signal_side"], ticker, result["conviction"],
                        result["rationale"])
        except Exception as e:
            log.warning("tick fail: %s", e)

    await nc.subscribe("ticks.live.*", cb=on_tick)
    log.info("options flow analyzer listening")
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        a = OptionsFlowAnalyzer()
        # Synthesize history with put surge
        import time as t
        for i in range(20):
            a.add(OptionsSnapshot(
                ticker="AAPL", ts=t.time() + i,
                call_oi=1000, put_oi=500 + i * 5,
                call_vol=100, put_vol=50, implied_vol=0.25,
            ))
        # Put surge
        a.add(OptionsSnapshot(
            ticker="AAPL", ts=t.time() + 21,
            call_oi=1000, put_oi=3000,
            call_vol=100, put_vol=500, implied_vol=0.35,
        ))
        r = a.analyze("AAPL")
        print(f"Signal: {r}")
        print("OK")
    else:
        asyncio.run(main())
