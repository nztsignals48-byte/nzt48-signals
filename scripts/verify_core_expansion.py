#!/usr/bin/env python3
"""
NZT-48 Core Expansion Verification
Verify 20 proposed ISA-eligible leveraged funds via yfinance.
"""
import json
import os
import sys
from datetime import datetime, timezone

# Proposed LONG leveraged (3x-5x)
PROPOSED_LONG = [
    ("AMD3.L", "3x", "AMD", "Semiconductor peer"),
    ("ARM3.L", "3x", "ARM Holdings", "AI chip designer"),
    ("AVGO3.L", "3x", "Broadcom", "Networking + AI infra"),
    ("PLTR3.L", "3x", "Palantir", "AI/data analytics"),
    ("META3.L", "3x", "Meta", "Mag7 + AI capex"),
    ("AMZN3.L", "3x", "Amazon", "Cloud + retail"),
    ("MSFT3.L", "3x", "Microsoft", "AI + enterprise"),
    ("AAPL3.L", "3x", "Apple", "Mag7 anchor"),
    ("3LDE.L", "3x", "DAX", "European exposure"),
    ("3LIT.L", "3x", "FTSE MIB", "European diversification"),
]

PROPOSED_SHORT = [
    ("NVDS.L", "-3x", "NVIDIA", "Inverse of NVD3.L"),
    ("TSLS.L", "-3x", "Tesla", "Inverse of TSL3.L"),
    ("AMDS.L", "-3x", "AMD", "Inverse of AMD3.L"),
    ("ARMS.L", "-3x", "ARM Holdings", "Inverse of ARM3.L"),
    ("3SUS.L", "-3x", "S&P 500", "Broader US short"),
    ("SC3S.L", "-3x", "Semiconductors", "Sector inverse"),
    ("MG3S.L", "-3x", "Mag7", "Mag7 inverse"),
    ("GPTS.L", "-3x", "AI Index", "AI inverse"),
    ("3SDE.L", "-3x", "DAX", "European inverse"),
    ("3SIT.L", "-3x", "FTSE MIB", "European inverse"),
]

def verify_ticker(ticker):
    """Verify a single ticker via yfinance."""
    try:
        import yfinance as yf
        data = yf.download(ticker, period="5d", progress=False)
        if data is None or data.empty:
            return {"status": "DELISTED", "data_ok": False, "volume_ok": False, "price_ok": False, "rows": 0}

        rows = len(data)
        last_close = float(data["Close"].iloc[-1]) if "Close" in data.columns else 0
        last_volume = float(data["Volume"].iloc[-1]) if "Volume" in data.columns else 0

        data_ok = rows >= 2 and last_close > 0
        volume_ok = last_volume > 0
        price_ok = last_close > 0.01 and last_close < 50000

        status = "VERIFIED" if (data_ok and volume_ok and price_ok) else "PROPOSED"

        return {
            "status": status,
            "data_ok": data_ok,
            "volume_ok": volume_ok,
            "price_ok": price_ok,
            "rows": rows,
            "last_close": round(last_close, 4),
            "last_volume": int(last_volume),
        }
    except Exception as e:
        return {"status": "ERROR", "data_ok": False, "volume_ok": False, "price_ok": False, "error": str(e)}


def main():
    results = {"long": [], "short": [], "verified_at": datetime.now(timezone.utc).isoformat(), "summary": {}}

    verified = 0
    proposed = 0
    delisted = 0
    errors = 0

    print("=== NZT-48 Core Expansion Verification ===\n")

    for ticker, leverage, underlying, rationale in PROPOSED_LONG + PROPOSED_SHORT:
        direction = "LONG" if (ticker, leverage, underlying, rationale) in PROPOSED_LONG else "SHORT"
        print(f"  Checking {ticker} ({leverage} {underlying})...", end=" ", flush=True)
        result = verify_ticker(ticker)
        result.update({"ticker": ticker, "leverage": leverage, "underlying": underlying, "rationale": rationale, "direction": direction})

        if direction == "LONG":
            results["long"].append(result)
        else:
            results["short"].append(result)

        if result["status"] == "VERIFIED":
            verified += 1
            print(f"✓ VERIFIED (close={result.get('last_close', 'N/A')}, vol={result.get('last_volume', 'N/A')})")
        elif result["status"] == "PROPOSED":
            proposed += 1
            print(f"? PROPOSED (data incomplete)")
        elif result["status"] == "DELISTED":
            delisted += 1
            print(f"✗ DELISTED (no data)")
        else:
            errors += 1
            print(f"! ERROR ({result.get('error', 'unknown')})")

    results["summary"] = {
        "total": len(PROPOSED_LONG) + len(PROPOSED_SHORT),
        "verified": verified,
        "proposed": proposed,
        "delisted": delisted,
        "errors": errors,
    }

    print(f"\n=== Summary: {verified} verified, {proposed} proposed, {delisted} delisted, {errors} errors ===")

    # Save results
    os.makedirs("artifacts/universe", exist_ok=True)
    output_path = "artifacts/universe/core_expansion_verification.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {output_path}")

    return results


if __name__ == "__main__":
    main()
