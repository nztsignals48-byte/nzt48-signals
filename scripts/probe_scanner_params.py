"""Probe IBKR's actual reqScannerParameters XML to discover valid location codes.

Writes the full XML to /Users/rr/aegis-v5/data/scanner_parameters.xml and
prints the list of VALID STK locationCodes for each region.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path


async def main() -> None:
    from ib_insync import IB  # type: ignore
    ib = IB()
    await ib.connectAsync("127.0.0.1", 4002, clientId=119, readonly=True)
    xml = await ib.reqScannerParametersAsync()
    out = Path("/Users/rr/aegis-v5/data/scanner_parameters.xml")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(xml)
    print(f"wrote {out} ({len(xml):,} chars)")
    locs = sorted(set(re.findall(r"<locationCode>(STK\.[A-Z\.]+)</locationCode>", xml)))
    print(f"\n=== {len(locs)} STK locationCodes ===")
    for l in locs:
        print(f"  {l}")
    ib.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
