"""IBKR universe scanner — client_id=103, delayed-data tier.

Runs IBKR's scanner subscription with multiple filter types and publishes
hits on NATS. The universe rotator consumes these to promote tickers into
the Live 100 that the Rust bridge (client_id=105) streams in real-time.

NATS subjects:
    scanner.hits.top_movers_usd
    scanner.hits.top_volume_usd
    scanner.hits.top_movers_gbp    (LSE)
    scanner.hits.top_movers_eur    (XETRA + Euronext)
    scanner.hits.top_movers_asia   (TSE/HKEX/SGX)
    scanner.hits.high_opt_iv
    scanner.hits.hot_by_price
    scanner.hits.top_trade_count

Payload shape:
    {
        "ticker": "AAPL",
        "exchange": "SMART",
        "currency": "USD",
        "con_id": 265598,
        "scan_type": "top_movers_usd",
        "rank": 1,
        "last": 197.75,
        "ts": "2026-04-17T04:12:00+00:00"
    }
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


# ScannerSubscription templates covering the 30k+ universe across exchanges.
# Each maps to an IBKR built-in scan type.
# Reference: https://interactivebrokers.github.io/tws-api/market_scanners.html
SCAN_TEMPLATES = [
    # US equities
    ("top_movers_usd",  {"instrument": "STK", "locationCode": "STK.US.MAJOR", "scanCode": "TOP_PERC_GAIN"}),
    ("bottom_movers_usd",{"instrument": "STK", "locationCode": "STK.US.MAJOR", "scanCode": "TOP_PERC_LOSE"}),
    ("top_volume_usd",  {"instrument": "STK", "locationCode": "STK.US.MAJOR", "scanCode": "MOST_ACTIVE"}),
    ("hot_by_price",    {"instrument": "STK", "locationCode": "STK.US.MAJOR", "scanCode": "HOT_BY_PRICE"}),
    ("top_trade_count", {"instrument": "STK", "locationCode": "STK.US.MAJOR", "scanCode": "TOP_TRADE_COUNT"}),
    # Options IV
    ("high_opt_iv",     {"instrument": "STK", "locationCode": "STK.US.MAJOR", "scanCode": "HIGH_OPT_IMP_VOLAT"}),
    # UK LSE
    ("top_movers_gbp",  {"instrument": "STK", "locationCode": "STK.EU.LSE", "scanCode": "TOP_PERC_GAIN"}),
    ("top_volume_gbp",  {"instrument": "STK", "locationCode": "STK.EU.LSE", "scanCode": "MOST_ACTIVE"}),
    # XETRA
    ("top_movers_eur",  {"instrument": "STK", "locationCode": "STK.EU.IBIS", "scanCode": "TOP_PERC_GAIN"}),
    # Asia — these run 24/7-ish for our purposes (TSE opens 1am UK)
    ("top_movers_jpy",  {"instrument": "STK", "locationCode": "STK.HK.SEHK", "scanCode": "TOP_PERC_GAIN"}),
]


@dataclass
class ScannerWorker:
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 4002
    ibkr_client_id: int = 107
    nats_url: str = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    rescan_interval_s: int = 60

    async def run(self) -> None:
        import nats  # type: ignore
        from ib_insync import IB, ScannerSubscription  # type: ignore

        nc = await nats.connect(self.nats_url, name="aegis-v5-scanner")
        log.info("scanner connected to NATS %s", self.nats_url)

        ib = IB()
        await ib.connectAsync(
            self.ibkr_host, self.ibkr_port, clientId=self.ibkr_client_id,
            readonly=True,
        )
        log.info("scanner connected to IBKR client_id=%d", self.ibkr_client_id)

        # Request delayed data so we don't consume paid L1 slots.
        # marketDataType 3 = delayed, 4 = delayed-frozen
        ib.reqMarketDataType(3)

        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)

        scan_count = 0
        while not stop_event.is_set():
            ts = datetime.now(timezone.utc).isoformat()
            total_hits = 0
            for name, params in SCAN_TEMPLATES:
                try:
                    sub = ScannerSubscription(
                        instrument=params["instrument"],
                        locationCode=params["locationCode"],
                        scanCode=params["scanCode"],
                        numberOfRows=50,
                    )
                    results = await ib.reqScannerDataAsync(sub)
                    for rank, row in enumerate(results or [], start=1):
                        c = row.contractDetails.contract
                        payload = {
                            "ticker": c.symbol,
                            "exchange": c.primaryExchange or c.exchange,
                            "currency": c.currency,
                            "con_id": c.conId,
                            "scan_type": name,
                            "rank": rank,
                            "ts": ts,
                        }
                        await nc.publish(
                            f"scanner.hits.{name}",
                            json.dumps(payload).encode("utf-8"),
                        )
                        total_hits += 1
                except Exception as e:
                    log.warning("scan %s failed: %s", name, e)

            scan_count += 1
            log.info(
                "scan %d complete: %d hits across %d scans",
                scan_count, total_hits, len(SCAN_TEMPLATES),
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.rescan_interval_s)
            except asyncio.TimeoutError:
                pass

        await nc.drain()
        ib.disconnect()
        log.info("scanner stopped")


if __name__ == "__main__":
    asyncio.run(ScannerWorker().run())
