#!/usr/bin/env python3
"""Probe IBKR for actual allowed scanner location codes + scan codes for THIS account."""
import asyncio
import random
import xml.etree.ElementTree as ET
from ib_insync import IB, util


async def probe():
    ib = IB()
    cid = random.randint(500, 900)
    await ib.connectAsync("127.0.0.1", 4002, clientId=cid, timeout=30)
    print(f"Connected clientId={cid}", flush=True)
    xml = await ib.reqScannerParametersAsync()
    if not xml:
        print("No XML returned")
        return
    # Save full XML
    with open("/tmp/scanner_params.xml", "w") as f:
        f.write(xml)
    print(f"Wrote {len(xml)} bytes of XML to /tmp/scanner_params.xml", flush=True)

    root = ET.fromstring(xml)
    # Grab all LocationCode values for STK
    locs = set()
    for loc in root.iter("LocationCode"):
        if loc.text and loc.text.startswith("STK"):
            locs.add(loc.text)
    print(f"\nSTK LocationCodes ({len(locs)}):")
    for l in sorted(locs):
        print(f"  {l}")

    # Grab all ScanCode values
    scans = set()
    for sc in root.iter("ScanCode"):
        if sc.text:
            scans.add(sc.text)
    print(f"\nScanCodes ({len(scans)}):")
    for s in sorted(scans)[:30]:
        print(f"  {s}")

    ib.disconnect()


if __name__ == "__main__":
    util.patchAsyncio()
    asyncio.run(probe())
