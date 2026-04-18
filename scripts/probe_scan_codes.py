#!/usr/bin/env python3
"""Probe different scan codes per region to find what works when markets closed."""
import asyncio
import json
import os
import sys
import time
import uuid

NATS_URL = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")

# Test 3 well-known scanCodes at each location to see what actually returns
TEST_MATRIX = [
    # (region, locationCode)
    ("UK", "STK.EU.LSE"),
    ("DE", "STK.EU.IBIS"),
    ("FR", "STK.EU.SBF"),
    ("IT", "STK.EU.BVME"),
    ("JP", "STK.JP.TSE.JPN"),
    ("HK", "STK.HK.SEHK"),
    ("AU", "STK.AU.ASX"),
    ("SG", "STK.SG.SGX"),
    ("US", "STK.US.MAJOR"),
]

SCAN_CODES = [
    "TOP_PERC_GAIN",
    "TOP_PERC_LOSE",
    "MOST_ACTIVE",
    "HIGH_OPT_IMP_VOLAT",
    "HIGH_SYNTH_BID_REV_NAT_YIELD",
    "TOP_TRADE_COUNT",
]


async def main():
    import nats
    nc = await nats.connect(NATS_URL, name="aegis-v5-scancode-probe")
    reply = f"scanner.probe.result.{uuid.uuid4().hex[:8]}"
    results: list[dict] = []

    async def on_reply(msg):
        results.append(json.loads(msg.data))

    await nc.subscribe(reply, cb=on_reply)

    for region, loc in TEST_MATRIX:
        for scan in SCAN_CODES:
            req = {
                "locationCode": loc,
                "scanCode": scan,
                "reply_to": reply,
                "req_id": f"{region}::{loc}::{scan}",
            }
            await nc.publish("scanner.probe", json.dumps(req).encode())
            await asyncio.sleep(0.25)

    deadline = time.time() + 60
    expected = len(TEST_MATRIX) * len(SCAN_CODES)
    while time.time() < deadline and len(results) < expected:
        await asyncio.sleep(1)

    print(f"\n{len(results)}/{expected} replies\n")
    print(f"{'REGION':6s} {'LOCATION':22s} {'SCANCODE':30s}  HITS  ERR")
    for r in sorted(results, key=lambda x: x.get("req_id", "")):
        parts = r["req_id"].split("::")
        region, loc, scan = parts[0], parts[1], parts[2]
        hits = r.get("hits", 0)
        err = (r.get("err") or "")[:40]
        marker = "✅" if (r.get("ok") and hits > 0) else (" " if r.get("ok") else "❌")
        print(f"{marker} {region:4s} {loc:22s} {scan:30s} {hits:4d}  {err}")

    # Summary of working (region, scanCode) combos
    working: dict[tuple, int] = {}
    for r in results:
        if r.get("ok") and r.get("hits", 0) > 0:
            parts = r["req_id"].split("::")
            key = (parts[0], parts[1])
            working[key] = working.get(key, 0) + 1

    print("\n=== Regions with ANY working scan ===")
    for k, n in sorted(working.items()):
        print(f"  {k[0]}  {k[1]}: {n} scan codes returned hits")

    print("\n=== Regions with ZERO hits on any scan code ===")
    all_regions = {(r, l) for r, l in TEST_MATRIX}
    silent = all_regions - set(working.keys())
    for r, l in sorted(silent):
        print(f"  {r}  {l}")

    await nc.drain()


if __name__ == "__main__":
    asyncio.run(main())
