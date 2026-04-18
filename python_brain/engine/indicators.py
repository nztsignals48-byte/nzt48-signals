"""Indicators: RSI, ATR, IBS, momentum_5d, close_proximity_min, rvol, session_high/low."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from python_brain.engine.bar_builder import Bar


@dataclass
class Indicators:
    rsi: float = 50.0
    atr: float = 1.0
    ibs: float = 0.5
    momentum_5d: float = 0.0
    close_proximity_min: float = 60.0
    rvol: float = 1.0
    session_high: float = 0.0
    session_low: float = 0.0
    vwap_distance_bps: float = 0.0
    ema_fast: float = 0.0
    ema_slow: float = 0.0


def _rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = 0.0, 0.0
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100 - 100 / (1 + rs)


def _atr(bars: List[Bar], period: int = 14) -> float:
    if len(bars) < period + 1:
        return 0.0
    trs: List[float] = []
    for i in range(-period, 0):
        b, prev = bars[i], bars[i - 1]
        trs.append(max(b.high - b.low, abs(b.high - prev.close), abs(b.low - prev.close)))
    return sum(trs) / period if trs else 0.0


def _ibs(last_bar: Bar) -> float:
    rng = last_bar.high - last_bar.low
    if rng <= 0:
        return 0.5
    return (last_bar.close - last_bar.low) / rng


@dataclass
class IndicatorStore:
    by_ticker: Dict[str, Indicators] = field(default_factory=dict)

    def update(self, ticker: str, bars_1m: List[Bar], current_ts_ns: int, close_ts_ns: int) -> Indicators:
        ind = self.by_ticker.get(ticker, Indicators())
        if bars_1m:
            closes = [b.close for b in bars_1m]
            ind.rsi = _rsi(closes)
            ind.atr = _atr(bars_1m)
            ind.ibs = _ibs(bars_1m[-1])
            if len(closes) >= 5 * 60 // 1:   # rough 5-bar proxy for 5-day
                ind.momentum_5d = (closes[-1] - closes[-min(300, len(closes))]) / closes[-min(300, len(closes))]
            ind.session_high = max(b.high for b in bars_1m)
            ind.session_low = min(b.low for b in bars_1m)
            vwap = bars_1m[-1].vwap
            if vwap > 0:
                ind.vwap_distance_bps = (bars_1m[-1].close - vwap) / vwap * 1e4
            if len(closes) >= 10:
                alpha_f, alpha_s = 2 / (12 + 1), 2 / (26 + 1)
                ef = closes[0]; es = closes[0]
                for c in closes:
                    ef = alpha_f * c + (1 - alpha_f) * ef
                    es = alpha_s * c + (1 - alpha_s) * es
                ind.ema_fast, ind.ema_slow = ef, es
        ind.close_proximity_min = max(0.0, (close_ts_ns - current_ts_ns) / 60_000_000_000)
        ind.rvol = 1.0  # Phase 2B fills with avg_volume denominator
        self.by_ticker[ticker] = ind
        return ind
