#!/usr/bin/env python3
"""Send scanner probe requests to the running scanner via NATS.

Uses the scanner's already-authenticated IBKR client, dodging the apiStart
handshake problem entirely.
"""
import asyncio
import json
import os
import sys
import time
import uuid

NATS_URL = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")

CANDIDATES = [
    # Format: region, locationCode
    ("US",  "STK.US.MAJOR"),
    ("US",  "STK.NASDAQ.NMS"),
    ("US",  "STK.NYSE"),

    # UK
    ("UK",  "STK.EU.LSE"),
    ("UK",  "STK.UK.LSE"),
    ("UK",  "STK.LSE"),

    # Germany
    ("DE",  "STK.EU.IBIS"),
    ("DE",  "STK.DE.IBIS"),
    ("DE",  "STK.IBIS"),

    # France
    ("FR",  "STK.EU.SBF"),
    ("FR",  "STK.FR.SBF"),
    ("FR",  "STK.SBF"),

    # Italy
    ("IT",  "STK.EU.BVME"),
    ("IT",  "STK.IT.BVME"),

    # Netherlands
    ("NL",  "STK.EU.AEB"),
    ("NL",  "STK.NL.AEB"),

    # Spain
    ("ES",  "STK.EU.BM"),
    ("ES",  "STK.ES.BM"),

    # Switzerland
    ("CH",  "STK.EU.EBS"),
    ("CH",  "STK.CH.EBS"),

    # Belgium
    ("BE",  "STK.EU.ENEXT.BE"),
    ("BE",  "STK.BE.ENEXT"),

    # Hong Kong
    ("HK",  "STK.HK.SEHK"),
    ("HK",  "STK.HK"),

    # Japan — variations
    ("JP",  "STK.JP.TSE.JPN"),
    ("JP",  "STK.JP.TSE"),
    ("JP",  "STK.JP"),

    # Singapore
    ("SG",  "STK.SG.SGX"),
    ("SG",  "STK.SG"),

    # Australia
    ("AU",  "STK.AU.ASX"),
    ("AU",  "STK.AU"),

    # Taiwan
    ("TW",  "STK.TW.TWSE"),
    ("TW",  "STK.TW"),

    # Korea
    ("KR",  "STK.KR.KSE"),
    ("KR",  "STK.KR"),

    # Canada
    ("CA",  "STK.NA.CANADA"),
    ("CA",  "STK.CA"),
]


async def main():
    import nats  # type: ignore
    nc = await nats.connect(NATS_URL, name="aegis-v5-probe-caller")
    reply_subject = f"scanner.probe.result.{uuid.uuid4().hex[:8]}"
    results: list[dict] = []

    async def on_reply(msg):
        try:
            d = json.loads(msg.data)
            results.append(d)
        except Exception:
            pass

    await nc.subscribe(reply_subject, cb=on_reply)

    # Send all probes
    for region, code in CANDIDATES:
        req = {
            "locationCode": code,
            "scanCode": "TOP_PERC_GAIN",
            "reply_to": reply_subject,
            "req_id": f"{region}::{code}",
        }
        await nc.publish("scanner.probe", json.dumps(req).encode("utf-8"))
        await asyncio.sleep(0.3)  # polite pacing

    # Wait for responses
    deadline = time.time() + 45
    while time.time() < deadline and len(results) < len(CANDIDATES):
        await asyncio.sleep(1)

    print(f"\nReceived {len(results)}/{len(CANDIDATES)} responses\n")
    print(f"{'STATUS':8s} {'REGION':6s} {'LOCATION':30s} HITS  ERR")
    for r in sorted(results, key=lambda x: x.get("req_id", "")):
        ok = r.get("ok")
        hits = r.get("hits", 0)
        err = r.get("err", "")[:60]
        status = "✅ WORK" if (ok and hits > 0) else ("⚠️ EMPTY" if ok else "❌ FAIL")
        region = r["req_id"].split("::")[0] if "::" in r.get("req_id", "") else "??"
        loc = r.get("locationCode", "")
        print(f"{status:8s} {region:6s} {loc:30s} {hits:3d}   {err}")

    # Summary: first working code per region
    working: dict[str, list[str]] = {}
    for r in results:
        if r.get("ok") and r.get("hits", 0) > 0:
            region = r["req_id"].split("::")[0]
            working.setdefault(region, []).append(r["locationCode"])

    print("\n=== WORKING CODES BY REGION ===")
    for region in sorted(working):
        print(f"  {region}: {working[region]}")

    await nc.drain()


if __name__ == "__main__":
    asyncio.run(main())
