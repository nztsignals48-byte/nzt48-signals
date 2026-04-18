"""Multi-timeframe bar builder. Aggregates ticks into 1m / 5m / 15m / 1h bars."""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List

from python_brain.engine.tick_feed import Tick

TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}


@dataclass
class Bar:
    tf: str
    ticker: str
    start_ns: int
    end_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float


@dataclass
class BarBuilder:
    bars: Dict[str, Dict[str, Deque[Bar]]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(lambda: deque(maxlen=500))))
    _partial: Dict[str, Dict[str, dict]] = field(default_factory=lambda: defaultdict(dict))

    def on_tick(self, t: Tick) -> List[Bar]:
        completed: List[Bar] = []
        for tf, secs in TF_SECONDS.items():
            bucket_start = (t.timestamp_ns // 1_000_000_000 // secs) * secs * 1_000_000_000
            key = (tf, t.ticker)
            partial = self._partial.get(tf, {}).get(t.ticker)
            if partial and partial["start_ns"] != bucket_start:
                bar = Bar(tf=tf, ticker=t.ticker,
                          start_ns=partial["start_ns"],
                          end_ns=partial["start_ns"] + secs * 1_000_000_000,
                          open=partial["open"], high=partial["high"],
                          low=partial["low"], close=partial["close"],
                          volume=partial["volume"], vwap=partial["vwap_num"]/max(partial["vwap_den"],1))
                self.bars[tf][t.ticker].append(bar)
                completed.append(bar)
                partial = None
            if partial is None:
                partial = {"start_ns": bucket_start, "open": t.last, "high": t.high,
                           "low": t.low, "close": t.last, "volume": 0,
                           "vwap_num": 0.0, "vwap_den": 0}
                self._partial.setdefault(tf, {})[t.ticker] = partial
            partial["high"] = max(partial["high"], t.high)
            partial["low"] = min(partial["low"], t.low)
            partial["close"] = t.last
            partial["volume"] += t.volume
            partial["vwap_num"] += t.vwap * t.volume
            partial["vwap_den"] += t.volume
        return completed

    def recent(self, tf: str, ticker: str, n: int = 50) -> List[Bar]:
        return list(self.bars[tf][ticker])[-n:]
