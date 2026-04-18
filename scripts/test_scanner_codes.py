#!/usr/bin/env python3
"""Try each scanner location code and report which ones accept + return hits."""
import asyncio
import random
from ib_insync import IB, ScannerSubscription, util


# Code variants to try
US_CODES = ["STK.US.MAJOR", "STK.US", "STK.NASDAQ.NMS", "STK.NYSE"]
UK_CODES = ["STK.UK.LSE", "STK.EU.LSE", "STK.LSE"]
DE_CODES = ["STK.DE.IBIS", "STK.EU.IBIS", "STK.DE.XETRA"]
FR_CODES = ["STK.FR.SBF", "STK.EU.SBF", "STK.EU.EURONEXT"]
IT_CODES = ["STK.IT.BVME", "STK.EU.BVME"]
NL_CODES = ["STK.NL.AEB", "STK.EU.AEB"]
HK_CODES = ["STK.HK.SEHK", "STK.HK"]
JP_CODES = ["STK.JP.TSE", "STK.JP.TSEJ", "STK.JP"]
SG_CODES = ["STK.SG.SGX", "STK.SG"]
AU_CODES = ["STK.AU.ASX", "STK.AU"]
TW_CODES = ["STK.TW.TWSE", "STK.HK.TWSE", "STK.TW"]


async def test_code(ib, loc):
    """Try one location code. Return (success, hit_count, err)."""
    sub = ScannerSubscription(
        instrument="STK", locationCode=loc, scanCode="TOP_PERC_GAIN",
        numberOfRows=5,
    )
    try:
        res = await asyncio.wait_for(ib.reqScannerDataAsync(sub), timeout=8)
        return (True, len(res or []), "")
    except Exception as e:
        return (False, 0, str(e)[:80])


async def run():
    ib = IB()
    # Try 3 times for handshake
    connected = False
    for attempt in range(3):
        cid = random.randint(500, 900)
        try:
            await ib.connectAsync("127.0.0.1", 4002, clientId=cid, timeout=25)
            connected = True
            print(f"Connected clientId={cid}", flush=True)
            break
        except Exception as e:
            print(f"Attempt {attempt+1} clientId={cid} failed: {e}", flush=True)
            await asyncio.sleep(2)
    if not connected:
        print("Failed after 3 attempts — Gateway saturated")
        return

    all_codes = {
        "US": US_CODES, "UK": UK_CODES, "DE": DE_CODES, "FR": FR_CODES,
        "IT": IT_CODES, "NL": NL_CODES, "HK": HK_CODES, "JP": JP_CODES,
        "SG": SG_CODES, "AU": AU_CODES, "TW": TW_CODES,
    }
    results = {}
    for region, codes in all_codes.items():
        for code in codes:
            ok, hits, err = await test_code(ib, code)
            mark = "✅" if ok and hits > 0 else ("⚠️" if ok else "❌")
            print(f"{mark} {region:3s} {code:25s} hits={hits} {err}", flush=True)
            if ok and hits > 0 and region not in results:
                results[region] = code

    print("\n=== Working codes per region ===")
    for region, code in results.items():
        print(f"  {region}: {code}")

    ib.disconnect()


if __name__ == "__main__":
    util.patchAsyncio()
    asyncio.run(run())
