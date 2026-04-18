"""IbkrTickFeed — consumes live IBKR ticks from the NATS bus.

V5 architecture: the Rust bridge (aegis-engine binary) publishes each
MarketTick to ``ticks.live.{ticker}``. This class subscribes and translates
the Rust MarketTick JSON into the V5 Python ``Tick`` dataclass that the
engine hot loop already understands.

Drop-in replacement for ``SimTickFeed``. Async iterator semantics match.

Requires the `nats-py` package. Install via:
    pip install nats-py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from python_brain.engine.tick_feed import Tick

log = logging.getLogger(__name__)


@dataclass
class IbkrTickFeed:
    nats_url: str = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    subject_pattern: str = "ticks.live.*"
    queue_maxsize: int = 10_000
    max_messages: Optional[int] = None  # None = forever

    async def __aiter__(self) -> AsyncIterator[Tick]:
        try:
            import nats  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "nats-py not installed. `pip install nats-py` to use IbkrTickFeed."
            ) from e

        nc = await nats.connect(self.nats_url, name="aegis-v5-ibkr-tick-feed")
        log.info("IbkrTickFeed connected to %s", self.nats_url)
        queue: asyncio.Queue[Optional[Tick]] = asyncio.Queue(maxsize=self.queue_maxsize)

        async def _handler(msg: "nats.aio.msg.Msg") -> None:
            try:
                payload = json.loads(msg.data)
                tick = _to_v5_tick(payload)
                try:
                    queue.put_nowait(tick)
                except asyncio.QueueFull:
                    log.warning("tick queue full — dropping tick %s", tick.ticker)
            except Exception as e:
                log.warning("decode failed: %s", e)

        sub = await nc.subscribe(self.subject_pattern, cb=_handler)
        try:
            consumed = 0
            while self.max_messages is None or consumed < self.max_messages:
                tick = await queue.get()
                if tick is None:
                    break
                consumed += 1
                yield tick
        finally:
            await sub.unsubscribe()
            await nc.drain()
            log.info("IbkrTickFeed closed (consumed=%d)", consumed)

    def __iter__(self):
        # SimTickFeed is sync-iterable; IbkrTickFeed is async-only.
        raise NotImplementedError(
            "IbkrTickFeed is async. Use: `async for t in feed:` inside asyncio.run(...)."
        )


# ---------------------------------------------------------------------------
# Rust MarketTick → V5 Python Tick
# ---------------------------------------------------------------------------

def _to_v5_tick(p: dict) -> Tick:
    """Translate Rust MarketTick JSON → V5 Python Tick.

    The Rust `MarketTick` has 40+ fields and uses microsecond timestamps;
    the V5 Python `Tick` dataclass uses nanoseconds and a subset of fields.
    Missing fields default to safe values. NaN/null → sentinel.
    """

    def f(k: str, default: float = float("nan")) -> float:
        v = p.get(k)
        if v is None:
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def i(k: str, default: int = 0) -> int:
        v = p.get(k)
        if v is None:
            return default
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def b(k: str, default: bool = False) -> bool:
        v = p.get(k)
        return bool(v) if v is not None else default

    ts_us = p.get("timestamp_us") or 0
    ts_ns = int(ts_us) * 1_000  # Rust µs → Python ns

    last = f("last")
    bid = f("bid")
    ask = f("ask")
    if not _finite(last):
        last = bid if _finite(bid) else ask
    spread = (ask - bid) if (_finite(ask) and _finite(bid)) else 0.05
    high = f("high", last if _finite(last) else 0.0)
    low = f("low", last if _finite(last) else 0.0)
    open_ = f("open", last if _finite(last) else 0.0)
    close = f("close", last if _finite(last) else 0.0)
    vwap = f("vwap", (high + low + last) / 3 if _finite(last) else 0.0)

    return Tick(
        ticker=p.get("ticker", ""),
        exchange=p.get("exchange", "SMART"),
        timestamp_ns=ts_ns,
        bid=bid if _finite(bid) else (last - spread / 2 if _finite(last) else 0.0),
        ask=ask if _finite(ask) else (last + spread / 2 if _finite(last) else 0.0),
        last=last if _finite(last) else 0.0,
        volume=i("volume"),
        high=high,
        low=low,
        open=open_,
        close=close,
        vwap=vwap,
        bid_size=i("bid_size"),
        ask_size=i("ask_size"),
        avg_volume=int(f("avg_volume", 500_000.0)),
        shortable=b("shortable", True),
        halted=b("halted", False),
        rt_hist_vol=f("rt_hist_vol", 0.2),
    )


def _finite(x: float) -> bool:
    return isinstance(x, float) and x == x and x not in (float("inf"), float("-inf"))
