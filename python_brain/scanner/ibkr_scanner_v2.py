"""V2 scanner — config-driven, session-aware.

Reads `/Users/rr/aegis-v5/config/scanner_templates.json` and runs each
scan type every `rescan_interval_s`. Each hit is tagged with its
`session` (asia|eu|us) so the rotator can apply time-of-day weighting.

Replaces ibkr_scanner.py (kept for reference).
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

TEMPLATES_PATH = ROOT / "config" / "scanner_templates.json"


@dataclass
class ScannerWorkerV2:
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 4002
    ibkr_client_id: int = 617  # was 107→207, both thrashed; using fresh random
    nats_url: str = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    rescan_interval_s: int = 60

    async def run(self) -> None:
        import nats  # type: ignore
        from ib_insync import IB, ScannerSubscription  # type: ignore

        templates = json.loads(TEMPLATES_PATH.read_text())
        log.info("loaded %d scan templates", len(templates))

        nc = await nats.connect(self.nats_url, name="aegis-v5-scanner-v2")
        log.info("scanner connected to NATS %s", self.nats_url)

        ib = IB()
        # Retry loop with cid increment — defends against per-cid soft-locks
        # on IBKR's side after rapid reconnect cycles.
        connected = False
        for attempt in range(8):
            cid = self.ibkr_client_id + attempt * 13  # jump around the cid space
            try:
                await ib.connectAsync(
                    self.ibkr_host, self.ibkr_port, clientId=cid,
                    readonly=True, timeout=30,
                )
                log.info("scanner connected cid=%d (attempt %d)", cid, attempt + 1)
                self.ibkr_client_id = cid
                connected = True
                break
            except Exception as e:
                log.warning("scanner connect attempt %d cid=%d failed: %s",
                            attempt + 1, cid, e)
                await asyncio.sleep(8)
        if not connected:
            log.error("scanner could not connect after 8 attempts; exiting")
            return
        log.info("scanner connected to IBKR client_id=%d", self.ibkr_client_id)

        ib.reqMarketDataType(3)  # delayed

        # Probe handler: subscribers publish a test scan request; we run it and
        # publish the result. Lets us test scanner codes without opening new
        # IBKR clients (which frequently hang on apiStart).
        async def on_probe(msg):
            try:
                req = json.loads(msg.data)
                loc = req.get("locationCode")
                scan = req.get("scanCode", "TOP_PERC_GAIN")
                req_id = req.get("req_id", loc)
                reply_to = req.get("reply_to", "scanner.probe.result")
                sub = ScannerSubscription(instrument="STK", locationCode=loc,
                                          scanCode=scan, numberOfRows=5)
                try:
                    r = await asyncio.wait_for(ib.reqScannerDataAsync(sub), timeout=10)
                    out = {
                        "req_id": req_id, "locationCode": loc, "scanCode": scan,
                        "ok": True, "hits": len(r or []),
                        "sample": [x.contractDetails.contract.symbol for x in (r or [])[:3]],
                    }
                except Exception as e:
                    out = {"req_id": req_id, "locationCode": loc, "scanCode": scan,
                           "ok": False, "err": str(e)[:120]}
                await nc.publish(reply_to, json.dumps(out).encode("utf-8"))
                log.info("probe %s -> ok=%s hits=%s",
                         loc, out.get("ok"), out.get("hits", 0))
            except Exception as e:
                log.warning("probe handler error: %s", e)

        await nc.subscribe("scanner.probe", cb=on_probe)

        # Matching-symbols discovery handler. Uses the already-authenticated
        # client; bypasses the apiStart hang that fresh connections hit.
        async def on_match(msg):
            try:
                req = json.loads(msg.data)
                prefix = req.get("prefix", "")
                req_id = req.get("req_id", prefix)
                reply_to = req.get("reply_to", "scanner.match.result")
                try:
                    res = await asyncio.wait_for(
                        ib.reqMatchingSymbolsAsync(prefix), timeout=8)
                    syms = []
                    for m in res or []:
                        c = m.contract
                        if c.secType == "STK" and c.symbol:
                            syms.append({
                                "symbol": c.symbol,
                                "exchange": c.primaryExchange or c.exchange or "SMART",
                                "currency": c.currency or "USD",
                            })
                    out = {"req_id": req_id, "prefix": prefix, "ok": True, "syms": syms}
                except Exception as e:
                    out = {"req_id": req_id, "prefix": prefix, "ok": False,
                           "err": str(e)[:120]}
                await nc.publish(reply_to, json.dumps(out).encode("utf-8"))
            except Exception as e:
                log.warning("match handler error: %s", e)

        # Qualify-contracts handler. Uses same authenticated client.
        async def on_qualify(msg):
            try:
                from ib_insync import Stock  # local import keeps module cost low
                req = json.loads(msg.data)
                items = req.get("items", [])
                req_id = req.get("req_id", "")
                reply_to = req.get("reply_to", "scanner.qualify.result")
                try:
                    stocks = [Stock(symbol=x["symbol"], exchange=x["exchange"],
                                    currency=x.get("currency", "USD"))
                              for x in items if x.get("symbol")]
                    res = await asyncio.wait_for(
                        ib.qualifyContractsAsync(*stocks), timeout=20)
                    out_items = []
                    for c in res:
                        if c and c.conId:
                            out_items.append({
                                "symbol": c.symbol,
                                "exchange": c.primaryExchange or "SMART",
                                "currency": c.currency or "USD",
                                "con_id": c.conId,
                            })
                    out = {"req_id": req_id, "ok": True, "qualified": out_items}
                except Exception as e:
                    out = {"req_id": req_id, "ok": False, "err": str(e)[:160]}
                await nc.publish(reply_to, json.dumps(out).encode("utf-8"))
            except Exception as e:
                log.warning("qualify handler error: %s", e)

        await nc.subscribe("scanner.match", cb=on_match)
        await nc.subscribe("scanner.qualify", cb=on_qualify)

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)

        scan_round = 0
        while not stop.is_set():
            ts = datetime.now(timezone.utc).isoformat()
            round_hits = 0
            per_session: dict[str, int] = {"asia": 0, "eu": 0, "us": 0}
            per_scan_fail = 0

            for t in templates:
                try:
                    sub = ScannerSubscription(
                        instrument=t["instrument"],
                        locationCode=t["locationCode"],
                        scanCode=t["scanCode"],
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
                            "scan_type": t["name"],
                            "session": t.get("session", "us"),
                            "rank": rank,
                            "ts": ts,
                        }
                        await nc.publish(
                            f"scanner.hits.{t['name']}",
                            json.dumps(payload).encode("utf-8"),
                        )
                        round_hits += 1
                        per_session[t.get("session", "us")] = per_session.get(
                            t.get("session", "us"), 0
                        ) + 1
                except Exception as e:
                    per_scan_fail += 1
                    log.warning("scan %s failed: %s", t["name"], e)

            scan_round += 1
            log.info(
                "round %d: %d hits (asia=%d eu=%d us=%d) fail=%d",
                scan_round, round_hits,
                per_session.get("asia", 0), per_session.get("eu", 0), per_session.get("us", 0),
                per_scan_fail,
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.rescan_interval_s)
            except asyncio.TimeoutError:
                pass

        await nc.drain()
        ib.disconnect()
        log.info("scanner v2 stopped")


if __name__ == "__main__":
    asyncio.run(ScannerWorkerV2().run())
