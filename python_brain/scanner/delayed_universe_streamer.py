#!/usr/bin/env python3
"""Delayed-data streamer — rotates through the full qualified universe
(contracts.toml) and publishes snapshot ticks to ticks.delayed.{TICKER}.

Purpose: the IBKR Scanner API only returns results when markets are open
for that region. When a region is closed, scans return 0 hits — the
system is blind to what's happening there. This streamer fills the gap
by directly requesting delayed-data snapshots on every qualified contract,
rotating through them in batches so we stay under IBKR's 100-slot cap.

The rotator can consume ticks.delayed.* to score price-movement across the
whole universe, regardless of whether the scanner is cooperating.
"""
import asyncio
import json
import logging
import os
import random
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("delayed-streamer")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

# Py 3.11+ stdlib; fallback to tomli for 3.9/3.10
try:
    import tomllib as _toml
except ImportError:
    import tomli as _toml  # type: ignore

NATS_URL = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
IBKR_HOST = "127.0.0.1"
IBKR_PORT = 4002
BATCH_SIZE = 80           # stay under 100 cap (leave room for other clients)
DWELL_S = 8               # dwell time per batch to let ticks arrive
CONTRACTS_TOML = Path("/Users/rr/aegis-v5/config/contracts.toml")


def load_contracts() -> list[dict[str, Any]]:
    try:
        with open(CONTRACTS_TOML, "rb") as f:
            data = _toml.load(f)
        return data.get("contracts", [])
    except Exception as e:
        log.error("load contracts.toml: %s", e)
        return []


async def run_streamer():
    import nats
    from ib_insync import IB, Stock, util  # type: ignore

    contracts_data = load_contracts()
    log.info("loaded %d contracts from TOML", len(contracts_data))

    # Build Stock objects — skip any with con_id=0 (never qualified)
    stocks: list[tuple[Any, str, str, str]] = []
    for c in contracts_data:
        sym = str(c.get("symbol", "")).strip()
        exch = c.get("exchange", "SMART")
        ccy = c.get("currency", "USD")
        con_id = int(c.get("con_id", 0))
        if not sym or con_id == 0:
            continue
        s = Stock(symbol=sym, exchange=exch, currency=ccy)
        s.conId = con_id
        stocks.append((s, sym, exch, ccy))
    log.info("%d valid contracts (with con_id) ready to stream", len(stocks))

    # Persistent NATS
    nc = await nats.connect(NATS_URL, name="aegis-v5-delayed-streamer")
    log.info("connected to NATS %s", NATS_URL)

    # Persistent IB client
    ib = IB()
    client_id = random.randint(500, 900)
    try:
        await ib.connectAsync(IBKR_HOST, IBKR_PORT, clientId=client_id, timeout=25)
    except Exception as e:
        log.error("IBKR connectAsync failed clientId=%d: %s", client_id, e)
        await nc.drain()
        return
    log.info("IBKR connected clientId=%d — requesting delayed data", client_id)
    ib.reqMarketDataType(3)  # 3 = delayed

    batch_idx = 0
    cycle_count = 0

    while True:
        if not stocks:
            log.warning("no valid contracts; sleeping 60s")
            await asyncio.sleep(60)
            continue

        start = batch_idx * BATCH_SIZE
        end = start + BATCH_SIZE
        if start >= len(stocks):
            batch_idx = 0
            cycle_count += 1
            log.info("completed universe cycle #%d (covered %d tickers)",
                     cycle_count, len(stocks))
            continue

        batch = stocks[start:end]
        tickers_subscribed: list[tuple[Any, str, str, str]] = []
        for s, sym, exch, ccy in batch:
            try:
                t = ib.reqMktData(s, "", False, False)
                tickers_subscribed.append((t, sym, exch, ccy))
            except Exception as e:
                log.debug("reqMktData fail %s: %s", sym, e)

        # Let prices populate
        await asyncio.sleep(DWELL_S)

        published = 0
        for t, sym, exch, ccy in tickers_subscribed:
            try:
                def _safe(x):
                    try:
                        v = float(x)
                        return v if v == v and v > 0 else 0.0
                    except Exception:
                        return 0.0
                bid = _safe(t.bid)
                ask = _safe(t.ask)
                last = _safe(t.last)
                vol = _safe(t.volume)
                if last <= 0 and bid <= 0:
                    continue
                import time as _t
                payload = {
                    "ticker": sym, "exchange": exch, "currency": ccy,
                    "bid": bid, "ask": ask, "last": last,
                    "volume": vol, "ts_ns": _t.time_ns(),
                    "delayed": True,
                }
                await nc.publish(f"ticks.delayed.{sym}",
                                 json.dumps(payload).encode("utf-8"))
                published += 1
            except Exception:
                pass
            finally:
                try:
                    ib.cancelMktData(t.contract)
                except Exception:
                    pass

        log.info("batch %d [%d-%d of %d] published=%d",
                 batch_idx, start, min(end, len(stocks)), len(stocks), published)
        batch_idx += 1


def main():
    from ib_insync import util  # type: ignore
    util.patchAsyncio()
    try:
        asyncio.run(run_streamer())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
