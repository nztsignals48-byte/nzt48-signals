#!/usr/bin/env python3
"""Indicator framer — subscribes to ticks.live.*, computes rolling indicators
per ticker, publishes snapshots to indicators.live.{ticker} at 1Hz.

Indicators produced (matches chandelier_v4.V4InputFrame inputs):
  - ker10:           Kaufman Efficiency Ratio (10-bar net move / total path)
  - avwap_entry:     anchored VWAP from entry (per-position, tracked separately)
  - avwap_session:   session-anchored VWAP
  - bar_volume:      current 1-min bar volume
  - avg_volume_20:   20-bar rolling average volume
  - rv_now:          realised vol (1-min ATR / price, annualised)
  - rv_20d_ema:      20-day EMA of rv_now  (approximation using 20-bar-of-1-min)
  - rsi:             RSI(14)
  - macd_hist:       MACD histogram
  - bar_close_in_lower_third, bar_is_red

The engine and broker_chandelier read indicators.live.{ticker} and plug into
V4InputFrame so all 8 exit layers fire instead of just 3.
"""
from __future__ import annotations
import asyncio
import json
import logging
import math
import os
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional

log = logging.getLogger("indicator-framer")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

NATS_URL = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
BAR_WINDOW_S = 60.0        # 1-min bars
PUBLISH_HZ = 1.0           # publish frame every second per ticker
RING_BARS = 30             # keep 30 bars = 30 minutes of 1-min history


@dataclass
class Bar:
    t_start: float
    o: float = 0.0
    h: float = 0.0
    l: float = 0.0
    c: float = 0.0
    v: float = 0.0  # cumulative volume during this bar
    last_price: float = 0.0


@dataclass
class TickerState:
    ticker: str
    bars: Deque[Bar] = field(default_factory=lambda: deque(maxlen=RING_BARS))
    cur_bar: Optional[Bar] = None
    last_published_ts: float = 0.0
    # Session cumulative for VWAP
    session_px_vol: float = 0.0
    session_vol: float = 0.0
    session_start_ts: float = 0.0
    # RSI state
    rsi_avg_gain: float = 0.0
    rsi_avg_loss: float = 0.0
    rsi_prev_close: float = 0.0
    rsi_bars_seen: int = 0
    # MACD state
    ema12: float = 0.0
    ema26: float = 0.0
    macd_sig: float = 0.0
    macd_prev_hist: Optional[float] = None

    def push_tick(self, price: float, volume_delta: float, ts: float) -> None:
        if price <= 0:
            return
        bar_start = math.floor(ts / BAR_WINDOW_S) * BAR_WINDOW_S
        if self.cur_bar is None or self.cur_bar.t_start != bar_start:
            # Close prior bar
            if self.cur_bar is not None:
                self._close_bar(self.cur_bar)
            self.cur_bar = Bar(t_start=bar_start, o=price, h=price, l=price,
                               c=price, v=max(0.0, volume_delta), last_price=price)
        else:
            b = self.cur_bar
            if price > b.h:
                b.h = price
            if price < b.l:
                b.l = price
            b.c = price
            b.last_price = price
            b.v += max(0.0, volume_delta)
        # Session VWAP accumulation
        if volume_delta > 0:
            self.session_px_vol += price * volume_delta
            self.session_vol += volume_delta

    def _close_bar(self, bar: Bar) -> None:
        self.bars.append(bar)
        # RSI update
        if self.rsi_prev_close > 0:
            chg = bar.c - self.rsi_prev_close
            gain = max(chg, 0.0)
            loss = max(-chg, 0.0)
            if self.rsi_bars_seen < 14:
                self.rsi_avg_gain += gain
                self.rsi_avg_loss += loss
                self.rsi_bars_seen += 1
                if self.rsi_bars_seen == 14:
                    self.rsi_avg_gain /= 14
                    self.rsi_avg_loss /= 14
            else:
                self.rsi_avg_gain = (self.rsi_avg_gain * 13 + gain) / 14
                self.rsi_avg_loss = (self.rsi_avg_loss * 13 + loss) / 14
        self.rsi_prev_close = bar.c
        # MACD EMA update
        if self.ema12 == 0:
            self.ema12 = bar.c
            self.ema26 = bar.c
        else:
            self.ema12 = self.ema12 + (2.0 / 13.0) * (bar.c - self.ema12)
            self.ema26 = self.ema26 + (2.0 / 27.0) * (bar.c - self.ema26)
        macd = self.ema12 - self.ema26
        if self.macd_sig == 0:
            self.macd_sig = macd
        else:
            self.macd_sig = self.macd_sig + (2.0 / 10.0) * (macd - self.macd_sig)
        self.macd_prev_hist = macd - self.macd_sig

    def compute_frame(self) -> dict:
        """Compute indicator values. Safe to call mid-bar."""
        bars = list(self.bars)
        cur = self.cur_bar
        cur_price = cur.last_price if cur else 0.0

        # KER(10) = |net change| / sum of |per-bar changes|
        ker10: Optional[float] = None
        if len(bars) >= 10:
            net = bars[-1].c - bars[-10].o
            path = sum(abs(bars[i].c - bars[i - 1].c)
                       for i in range(-9, 0)) or 1e-9
            ker10 = abs(net) / path

        # Volume
        bar_volume = cur.v if cur else 0.0
        avg_volume_20: Optional[float] = None
        if len(bars) >= 5:
            avg_volume_20 = sum(b.v for b in bars[-20:]) / min(20, len(bars))

        # Realised vol — ATR over last 14 bars / current price, annualised
        atr = 0.0
        rv_now: Optional[float] = None
        if len(bars) >= 14:
            trs = []
            for i in range(-14, 0):
                b = bars[i]
                prev_c = bars[i - 1].c if i > -len(bars) else b.o
                tr = max(b.h - b.l, abs(b.h - prev_c), abs(b.l - prev_c))
                trs.append(tr)
            atr = sum(trs) / len(trs)
            if cur_price > 0:
                # annualise 1-min bar vol: sqrt(1440*252) ≈ 602
                rv_now = (atr / cur_price) * 602.0

        # rv_20d_ema — approximate: EMA of recent rv_now values. For paper run,
        # use the avg of last 20 bars' (H-L)/C which behaves similarly.
        rv_20d_ema: Optional[float] = None
        if len(bars) >= 20:
            ratios = [(b.h - b.l) / b.c for b in bars[-20:] if b.c > 0]
            if ratios:
                rv_20d_ema = (sum(ratios) / len(ratios)) * 602.0

        # Session VWAP
        avwap_session: Optional[float] = None
        if self.session_vol > 0:
            avwap_session = self.session_px_vol / self.session_vol

        # Bar close-in-lower-third, red
        bar_close_in_lower_third = False
        bar_is_red = False
        if cur is not None:
            rng = cur.h - cur.l
            if rng > 0:
                pos_in_bar = (cur.c - cur.l) / rng
                bar_close_in_lower_third = (pos_in_bar <= 0.33)
            bar_is_red = (cur.c < cur.o)

        # RSI
        rsi: Optional[float] = None
        if self.rsi_bars_seen >= 14 and self.rsi_avg_loss > 0:
            rs = self.rsi_avg_gain / self.rsi_avg_loss
            rsi = 100.0 - 100.0 / (1.0 + rs)
        elif self.rsi_bars_seen >= 14:
            rsi = 100.0

        # MACD histogram
        macd_hist: Optional[float] = None
        if self.ema12 > 0 and self.ema26 > 0:
            macd_hist = (self.ema12 - self.ema26) - self.macd_sig

        return {
            "ticker": self.ticker,
            "ts": time.time(),
            "atr": atr,
            "ker10": ker10,
            "bar_volume": bar_volume,
            "avg_volume_20": avg_volume_20,
            "rv_now": rv_now,
            "rv_20d_ema": rv_20d_ema,
            "avwap_session": avwap_session,
            "bar_close_in_lower_third": bar_close_in_lower_third,
            "bar_is_red": bar_is_red,
            "rsi": rsi,
            "macd_hist": macd_hist,
            "macd_hist_prev": self.macd_prev_hist,
            "last_price": cur_price,
            "bars_cached": len(bars),
        }


async def main() -> None:
    import nats  # type: ignore
    nc = await nats.connect(NATS_URL, name="aegis-v5-indicator-framer")
    log.info("indicator framer connected to NATS %s", NATS_URL)

    states: Dict[str, TickerState] = {}
    last_vol: Dict[str, float] = {}

    async def on_tick(msg):
        try:
            payload = json.loads(msg.data.decode())
        except Exception:
            return
        ticker = payload.get("ticker")
        if not ticker:
            return
        price = payload.get("last") or payload.get("mid") or payload.get("bid") or 0.0
        price = float(price) if price else 0.0
        vol_cum = float(payload.get("volume") or 0.0)
        prev = last_vol.get(ticker, vol_cum)
        vol_delta = max(0.0, vol_cum - prev)
        last_vol[ticker] = vol_cum
        ts = float(payload.get("ts") or time.time())

        st = states.get(ticker)
        if st is None:
            st = TickerState(ticker=ticker)
            st.session_start_ts = ts
            states[ticker] = st

        st.push_tick(price, vol_delta, ts)

        # Rate-limit publish to ~1Hz per ticker
        if ts - st.last_published_ts >= 1.0:
            st.last_published_ts = ts
            try:
                frame = st.compute_frame()
                await nc.publish(f"indicators.live.{ticker}",
                                 json.dumps(frame).encode("utf-8"))
            except Exception as e:
                log.warning("publish failed %s: %s", ticker, e)

    await nc.subscribe("ticks.live.*", cb=on_tick)
    log.info("indicator framer listening on ticks.live.*")

    try:
        while True:
            await asyncio.sleep(30)
            log.info("framer state: %d tickers, total bars=%d",
                     len(states), sum(len(s.bars) for s in states.values()))
    finally:
        await nc.drain()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
