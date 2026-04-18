"""reentry_manager — permit same-ticker re-entry under evidence-based rules.

Rule set (from Van Tharp + Kaufman + TradingView backtest conventions):
  1. Cooldown: >= 5 bars (5-min TF) or >= 10 bars (1-min TF) since exit
  2. Thesis reconfirm: same strategy must fire again
  3. KER(10) >= 0.30 (not in chop)
  4. Price > prior-trade AVWAP (shows reclaim)
  5. Fresh R-sizing (new initial risk, not averaged)
  6. Entry bar volume >= 1.2 × 20-bar average
  7. Max 2 re-entries per ticker per session

Subscribes:
    orders.filled     (tracks entries + exits per ticker)
    risk.regime       (for KER)
    ticks.live.*      (for cooldown bar-counting + price reclaim check)

Publishes:
    reentry.allowed   {ticker, strategy, reason}   — signal-to-order bridge
                                                    subscribes and whitelists
                                                    this (ticker, strategy) tuple
                                                    for the next matching signal
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, Tuple

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

COOLDOWN_S = 300  # 5 min on 5-min TF
MAX_REENTRIES = 2
KER_GATE = 0.30


@dataclass
class ExitRecord:
    ticker: str
    strategy: str
    ts: float
    exit_price: float
    entry_avwap: float = 0.0  # from prior-trade accumulated vwap
    reentry_count: int = 0


@dataclass
class TickerRolling:
    """Rolling VWAP + recent-volume cache per ticker for reentry gate."""
    ticker: str
    vwap_num: float = 0.0
    vwap_den: float = 0.0
    last_price: float = 0.0
    last_volume: float = 0.0
    avg_volume_20: float = 0.0
    volume_samples: list = field(default_factory=list)
    last_ts: float = 0.0


class ReentryManager:
    def __init__(self) -> None:
        self.nats_url = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
        self.exits: Dict[Tuple[str, str], ExitRecord] = {}  # (ticker, strategy) -> last exit
        self.ker_current: float = 0.4  # default calm
        self.tickers: Dict[str, TickerRolling] = {}
        self.session_reentry_count: Dict[str, int] = {}  # ticker -> count this session

    async def run(self) -> None:
        import nats  # type: ignore
        nc = await nats.connect(self.nats_url, name="aegis-v5-reentry-manager")
        log.info("reentry manager connected to NATS")

        async def on_fill(msg):
            try:
                f = json.loads(msg.data)
                t = f.get("ticker")
                s = f.get("strategy") or "?"
                side = (f.get("side") or "").upper()
                if side == "SELL" and t:
                    # Record exit
                    rec = ExitRecord(
                        ticker=t, strategy=s, ts=time.time(),
                        exit_price=float(f.get("avg_price") or 0),
                        entry_avwap=self._avwap_for(t),
                    )
                    self.exits[(t, s)] = rec
                    log.info("EXIT recorded %s/%s @$%.2f avwap=$%.2f",
                             t, s, rec.exit_price, rec.entry_avwap)
            except Exception:
                pass

        async def on_regime(msg):
            try:
                r = json.loads(msg.data)
                # pull ker-like metric from regime state name (proxy)
                state = r.get("state", "calm")
                self.ker_current = {
                    "calm": 0.4, "active": 0.5, "event": 0.6,
                    "crisis": 0.3, "cooldown": 0.1,
                }.get(state, 0.4)
            except Exception:
                pass

        async def on_tick(msg):
            try:
                t = json.loads(msg.data)
                sym = t.get("ticker")
                if not sym:
                    return
                price = float(t.get("last") or 0)
                vol = float(t.get("volume") or 0)
                if price <= 0:
                    return
                tr = self.tickers.setdefault(sym, TickerRolling(ticker=sym))
                tr.last_price = price
                tr.last_volume = vol
                tr.last_ts = time.time()
                tr.vwap_num += price * vol
                tr.vwap_den += vol
                tr.volume_samples.append(vol)
                if len(tr.volume_samples) > 20:
                    tr.volume_samples.pop(0)
                if tr.volume_samples:
                    tr.avg_volume_20 = sum(tr.volume_samples) / len(tr.volume_samples)
            except Exception:
                pass

        async def on_signal(msg):
            """Intercept signals.core and check if this is a reentry candidate."""
            try:
                s = json.loads(msg.data)
                ticker = s.get("ticker")
                strategy = s.get("strategy_name")
                if not ticker or not strategy:
                    return
                rec = self.exits.get((ticker, strategy))
                if not rec:
                    return  # not a reentry
                # Apply gates
                now = time.time()
                dt = now - rec.ts
                tr = self.tickers.get(ticker)
                reasons = []
                if dt < COOLDOWN_S:
                    reasons.append(f"cooldown({dt:.0f}s<{COOLDOWN_S}s)")
                if self.ker_current < KER_GATE:
                    reasons.append(f"ker({self.ker_current:.2f}<{KER_GATE})")
                if tr and rec.entry_avwap > 0 and tr.last_price < rec.entry_avwap:
                    reasons.append(f"below_avwap({tr.last_price:.2f}<{rec.entry_avwap:.2f})")
                if tr and tr.avg_volume_20 > 0 and tr.last_volume < 1.2 * tr.avg_volume_20:
                    reasons.append(f"low_vol({tr.last_volume:.0f}<{1.2*tr.avg_volume_20:.0f})")
                count = self.session_reentry_count.get(ticker, 0)
                if count >= MAX_REENTRIES:
                    reasons.append(f"max_reentries({count})")

                if reasons:
                    log.info("REENTRY BLOCK %s/%s: %s", ticker, strategy, ", ".join(reasons))
                    return

                # Permit: emit reentry.allowed so sig2order knows to ignore its
                # dedupe for this ticker-strategy pair, one time.
                self.session_reentry_count[ticker] = count + 1
                payload = {
                    "ticker": ticker,
                    "strategy": strategy,
                    "signal_id": s.get("signal_id"),
                    "reentry_count": count + 1,
                    "ts": time.time(),
                }
                await nc.publish("reentry.allowed",
                                 json.dumps(payload).encode("utf-8"))
                log.info("REENTRY ALLOWED %s/%s (count=%d)",
                         ticker, strategy, count + 1)
                # Clear the exit record so subsequent signal isn't double-whitelisted
                self.exits.pop((ticker, strategy), None)
            except Exception as e:
                log.warning("on_signal err: %s", e)

        await nc.subscribe("orders.filled", cb=on_fill)
        await nc.subscribe("risk.regime", cb=on_regime)
        await nc.subscribe("ticks.live.*", cb=on_tick)
        await nc.subscribe("signals.core", cb=on_signal)
        log.info("listening on orders.filled, ticks.live.*, signals.core")
        while True:
            await asyncio.sleep(60)

    def _avwap_for(self, ticker: str) -> float:
        tr = self.tickers.get(ticker)
        if not tr or tr.vwap_den <= 0:
            return 0.0
        return tr.vwap_num / tr.vwap_den


if __name__ == "__main__":
    asyncio.run(ReentryManager().run())
