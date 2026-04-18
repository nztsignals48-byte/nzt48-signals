"""Tick feed. Two modes:
  - ibkr  (Phase 2A+): wraps Rust engine -> NATS ticks.* stream.
  - sim:  deterministic synthetic generator for tests and the paper-restart trigger
          before real IBKR is attached.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Iterator, List


@dataclass
class Tick:
    ticker: str
    exchange: str
    timestamp_ns: int
    bid: float
    ask: float
    last: float
    volume: int
    high: float
    low: float
    open: float
    close: float
    vwap: float
    bid_size: int = 0
    ask_size: int = 0
    avg_volume: float = 0.0
    shortable: bool = True
    halted: bool = False
    rt_hist_vol: float = 0.2


@dataclass
class SimTickFeed:
    tickers: List[str] = field(default_factory=lambda: ["AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ"])
    seed: int = 42
    steps: int = 500
    start_price: float = 100.0
    _prices: dict = field(default_factory=dict)

    def __post_init__(self):
        random.seed(self.seed)
        self._prices = {t: self.start_price + random.uniform(-20, 20) for t in self.tickers}

    def __iter__(self) -> Iterator[Tick]:
        for step in range(self.steps):
            t_ns = 1_700_000_000_000_000_000 + step * 250_000_000
            for ticker in self.tickers:
                p = self._prices[ticker]
                # Brownian step with small mean-reversion drift.
                drift = (self.start_price - p) * 0.0005
                shock = random.gauss(0, p * 0.001)
                p_new = max(1.0, p + drift + shock)
                self._prices[ticker] = p_new
                spread = max(0.01, p_new * 0.0005)
                bid = p_new - spread / 2
                ask = p_new + spread / 2
                high = max(p, p_new) + spread
                low = min(p, p_new) - spread
                vol = random.randint(100, 1000)
                yield Tick(
                    ticker=ticker, exchange="NASDAQ",
                    timestamp_ns=t_ns, bid=bid, ask=ask, last=p_new,
                    volume=vol, high=high, low=low, open=p, close=p_new,
                    vwap=(high + low + p_new) / 3,
                    bid_size=vol // 2, ask_size=vol // 2,
                    avg_volume=500_000, shortable=True, halted=False,
                    rt_hist_vol=0.2,
                )
