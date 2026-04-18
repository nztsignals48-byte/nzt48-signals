#!/usr/bin/env python3
"""Data-Flow Monitor — publishes Prometheus gauges for every data pipe.

Subscribes to every NATS subject V5 uses and counts messages per 60s window.
Also connects to NATS monitoring HTTP and IBKR heartbeat to count subscribed
tickers, active news providers, L2 subscriptions, etc.

Every data source V5 touches shows up on the v5_data_flow dashboard as a
rate + last-seen timestamp. If something is silent when it shouldn't be, you
see it instantly.
"""
import asyncio
import json
import logging
import os
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from python_brain.core import metrics
from python_brain.core import metrics_http

log = logging.getLogger("data-flow-monitor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

NATS_URL = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
PORT = int(os.environ.get("DATA_FLOW_PORT", "9104"))

# Subjects we expect to see traffic on
SUBJECTS = [
    # IBKR real-time L1 stream
    "ticks.live.*",
    # IBKR delayed streamer
    "ticks.delayed.*",
    # Scanner output (22 scan types)
    "scanner.hits.*",
    # Rotator
    "universe.rotation",
    # Signals
    "signals.core",
    # Orders
    "orders.submit",
    "orders.filled",
    "orders.reject",
    # Fills / close events
    "fills.closed",
    # News pipeline
    "news.raw",
    "news.alpha",
    # Intel
    "intel.updated",
    # Regime
    "regime.amplifier",
    "regime.vpin.*",
    # Indicators
    "indicators.live.*",
    # Reentry
    "reentry.allowed",
    # Portfolio/equity
    "portfolio.equity",
    "portfolio.positions",
    # IBKR status
    "ibkr.status",
    # Account (filled by account_streamer)
    "account.pnl",
    "account.summary",
    "account.positions",
    "account.pnl.single.*",
    # Executions
    "fills.executions",
]


class SubjectRate:
    __slots__ = ("window", "total", "last_ts")

    def __init__(self, window_s: int = 60):
        self.window = deque(maxlen=60)  # ring of per-second counts
        for _ in range(60):
            self.window.append(0)
        self.total = 0
        self.last_ts: float = 0.0

    def tick(self, ts: float) -> None:
        self.last_ts = ts
        # Increment the last second slot
        if self.window:
            self.window[-1] += 1
        self.total += 1

    def per_minute(self) -> int:
        return sum(self.window)

    def seconds_since_last(self, now: float) -> float:
        return (now - self.last_ts) if self.last_ts else -1


async def main():
    import nats  # type: ignore

    nc = await nats.connect(NATS_URL, name="aegis-v5-data-flow-monitor")
    log.info("data-flow monitor connected to NATS")

    # Register gauges per subject
    reg = metrics.REGISTRY
    rates: dict[str, SubjectRate] = {}
    for s in SUBJECTS:
        # Prometheus metric names: replace . and * with _
        safe = s.replace(".", "_").replace("*", "all").replace("-", "_")
        reg.gauge(f"v5_flow_{safe}_per_min", f"Messages/min on {s}")
        reg.gauge(f"v5_flow_{safe}_last_age_s", f"Seconds since last message on {s}")
        reg.counter(f"v5_flow_{safe}_total", f"Total messages ever on {s}")
        rates[s] = SubjectRate()

    # Summary gauges
    reg.gauge("v5_flow_subjects_active", "Count of subjects with traffic in last 60s")
    reg.gauge("v5_flow_subjects_silent", "Count of subjects silent > 60s")
    reg.gauge("v5_flow_nats_connections", "Active NATS client connections")
    reg.gauge("v5_flow_ibkr_up", "1 if IB Gateway reachable on :4002, 0 else")
    reg.gauge("v5_flow_nats_up", "1 if NATS reachable, 0 else")

    # Ticker-count gauges
    reg.gauge("v5_flow_ticks_live_unique_tickers", "Unique tickers in ticks.live.* (last 60s)")
    reg.gauge("v5_flow_ticks_delayed_unique_tickers", "Unique tickers in ticks.delayed.* (last 60s)")
    reg.gauge("v5_flow_scanner_unique_tickers", "Unique tickers in scanner.hits.* (last 60s)")
    reg.gauge("v5_flow_indicators_unique_tickers", "Unique tickers in indicators.live.* (last 60s)")
    reg.gauge("v5_flow_news_unique_tickers", "Unique tickers in news.raw (last 10 min)")

    # Per-subject ticker windows
    ticker_windows: dict[str, set[str]] = defaultdict(set)
    ticker_window_start: dict[str, float] = {}

    # Extended-field coverage tracking on ticks.live.*
    # Counts how many ticks in the last 60s carry a usable (non-default) value.
    # Tells us whether generic ticks 236/293/375/411 are actually flowing, not
    # just being requested.
    EXT_FIELDS = [
        "shortable", "halted", "rt_hist_vol", "trade_rate", "trade_count",
        "volume_rate", "mark_price", "auction_imbalance", "etf_nav_last",
        "opt_implied_vol", "last_size", "bid_size", "ask_size", "avg_volume",
    ]
    for fld in EXT_FIELDS:
        reg.gauge(f"v5_flow_ext_{fld}_coverage_pct",
                  f"% of ticks.live.* in last 60s carrying usable {fld}")
    reg.gauge("v5_flow_ext_total_ticks_60s", "Total live-ticks observed in last 60s")
    ext_window: deque = deque(maxlen=5000)  # ring of recent ticks (full payloads)

    # Metrics http
    metrics_http.start(port=PORT)
    log.info("data-flow exporter serving on :%d/metrics", PORT)

    async def generic_handler(subject_pattern: str):
        sr = rates[subject_pattern]

        async def handler(msg):
            ts = time.time()
            sr.tick(ts)
            try:
                payload = json.loads(msg.data)
                tk = payload.get("ticker") or payload.get("symbol")
                if tk:
                    ticker_windows[subject_pattern].add(str(tk))
                # Extended-field coverage — only on ticks.live.*
                if subject_pattern == "ticks.live.*":
                    ext_window.append((ts, payload))
            except Exception:
                pass

        return handler

    def _is_default(field: str, v) -> bool:
        """Decide whether a field has a usable (non-default/non-NaN) value."""
        if v is None:
            return True
        if field == "halted":
            return v is False  # default is False
        if field == "shortable":
            # Rust sentinel: 1.0 = shortable. Anything ≥ 0 is real; NaN = missing.
            try:
                return not (isinstance(v, (int, float)) and v == v and v >= 0)
            except Exception:
                return True
        # Numeric fields: default is 0 or NaN
        try:
            if isinstance(v, (int, float)):
                return (v == 0) or (v != v)  # 0 or NaN
            return True
        except Exception:
            return True

    # Subscribe to all subjects
    for s in SUBJECTS:
        handler = await generic_handler(s)
        await nc.subscribe(s, cb=handler)
        log.info("subscribed %s", s)

    # Reporter loop — every 5s update gauges; every 60s window reset
    last_window_reset = time.time()
    while True:
        await asyncio.sleep(5)
        now = time.time()

        # Shift all second-counts by 5
        for sr in rates.values():
            for _ in range(5):
                sr.window.append(0)

        # Update gauges per subject
        active = 0
        silent = 0
        for subj, sr in rates.items():
            safe = subj.replace(".", "_").replace("*", "all").replace("-", "_")
            pm = sr.per_minute()
            age = sr.seconds_since_last(now)
            reg.set(f"v5_flow_{subj.replace('.','_').replace('*','all').replace('-','_')}_per_min", pm)
            reg.set(f"v5_flow_{subj.replace('.','_').replace('*','all').replace('-','_')}_last_age_s",
                    age if age >= 0 else -1)
            if pm > 0:
                active += 1
            else:
                silent += 1

        # Unique ticker counts
        tickers_live = len(ticker_windows.get("ticks.live.*", set()))
        tickers_del = len(ticker_windows.get("ticks.delayed.*", set()))
        tickers_scan = len(ticker_windows.get("scanner.hits.*", set()))
        tickers_ind = len(ticker_windows.get("indicators.live.*", set()))
        tickers_news = len(ticker_windows.get("news.raw", set()))
        reg.set("v5_flow_ticks_live_unique_tickers", tickers_live)
        reg.set("v5_flow_ticks_delayed_unique_tickers", tickers_del)
        reg.set("v5_flow_scanner_unique_tickers", tickers_scan)
        reg.set("v5_flow_indicators_unique_tickers", tickers_ind)
        reg.set("v5_flow_news_unique_tickers", tickers_news)

        reg.set("v5_flow_subjects_active", active)
        reg.set("v5_flow_subjects_silent", silent)

        # Extended-field coverage — look at last-60s ticks only
        cutoff = now - 60.0
        recent = [p for (pts, p) in ext_window if pts >= cutoff]
        total = len(recent)
        reg.set("v5_flow_ext_total_ticks_60s", total)
        if total > 0:
            for fld in EXT_FIELDS:
                usable = sum(1 for p in recent if not _is_default(fld, p.get(fld)))
                pct = (usable * 100.0) / total
                reg.set(f"v5_flow_ext_{fld}_coverage_pct", pct)

        # NATS + IBKR health
        try:
            import urllib.request
            with urllib.request.urlopen("http://127.0.0.1:8222/connz", timeout=2) as r:
                d = json.loads(r.read())
                reg.set("v5_flow_nats_connections", int(d.get("num_connections", 0)))
                reg.set("v5_flow_nats_up", 1)
        except Exception:
            reg.set("v5_flow_nats_up", 0)
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.5)
            s.connect(("127.0.0.1", 4002))
            s.close()
            reg.set("v5_flow_ibkr_up", 1)
        except Exception:
            reg.set("v5_flow_ibkr_up", 0)

        # Reset ticker windows every 60s (every 12 loops)
        if now - last_window_reset > 60:
            ticker_windows.clear()
            last_window_reset = now
            # Also drain old per-min counts — the deque naturally rolls
            # because we append 5 zeros each 5s loop (deque maxlen=60 keeps last 60s)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
