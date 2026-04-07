"""Find ALL GraniteShares/LevShares products available on IBKR."""
import ib_insync
import time
import json
import sys

ib = ib_insync.IB()
ib.connect("ib-gateway", 4003, clientId=104)
time.sleep(1)

# We already have 49 LSEETF tickers. Let's find what we're missing.
# Strategy: search reqMatchingSymbols for known product names,
# and also do direct lookups for plausible symbol patterns.

found = {}

# Method 1: reqMatchingSymbols with product-related terms
search_terms = [
    "GraniteShares", "Leverage Shares", "3x Long", "3x Short",
    "5x Long", "5x Short", "ETP", "Leveraged",
    "Coinbase", "MicroStrategy", "Bitcoin", "Ethereum",
    "NVIDIA 3x", "Tesla 3x", "Apple 3x", "Microsoft 3x",
    "Semiconductor", "Palantir", "CrowdStrike",
]

for term in search_terms:
    try:
        matches = ib.reqMatchingSymbols(term)
        time.sleep(0.4)
        if matches:
            for m in matches:
                c = m.contract
                exch = c.exchange or c.primaryExchange or ""
                if "LSE" in exch or c.currency in ("GBP", "GBX"):
                    key = c.conId
                    if key not in found:
                        found[key] = {
                            "symbol": c.symbol,
                            "con_id": c.conId,
                            "exchange": exch,
                            "currency": c.currency,
                        }
                        print(f"MATCH: {c.symbol} id={c.conId} exch={exch} cur={c.currency}", flush=True)
    except Exception as e:
        print(f"Search error [{term}]: {e}", file=sys.stderr)

# Method 2: Direct contract lookup for plausible GraniteShares symbols
# GraniteShares uses patterns like: 3LXX, 3SXX, 5LXX, 5SXX, XXX3, XXXS
candidates = []

# Generate candidate symbols for crypto/missing products
bases = [
    # Crypto-related
    "COIN", "MSTR", "BITC", "BCHN", "BTIC", "EBIT", "XBTC", "GBTC",
    "3BIT", "3ETH", "BTCW", "ETHW",
    # Single-stock leveraged we might be missing
    "3LPT", "3SPT",   # Palantir
    "3LCF", "3SCF",   # Cloudflare
    "3LUB", "3SUB",   # Uber
    "3LCW", "3SCW",   # CrowdStrike
    "3LDD", "3SDD",   # DDOG
    "3LSQ", "3SSQ",   # Block/Square
    "3LSP", "3SSP",   # Shopify
    "3LNF", "3SNF",   # Netflix
    "3LCN", "3SCN",   # Coinbase
    "3LMR", "3SMR",   # MicroStrategy
    # Indices/Sectors we might be missing
    "3LFT", "3SFT",   # FTSE
    "3LDX", "3SDX",   # DAX
    "FTSE", "DAX3",
    "BANK", "SEMI", "TECH",
    "3LBK", "3SBK",   # Banks
    "3LGD", "3SGD",   # Gold
    "3LOI", "3SOI",   # Oil
    # Other patterns
    "NAS3", "SP53", "RUS3",
    "3CRD", "3CRS",
    "CRUD", "OILW",
    "GDX3", "SLV3",
]

for sym in candidates + bases:
    for cur in ["GBP", "USD"]:
        try:
            contract = ib_insync.Stock(sym, "LSEETF", cur)
            details = ib.reqContractDetails(contract)
            time.sleep(0.12)
            if details:
                d = details[0]
                key = d.contract.conId
                if key not in found:
                    name = d.longName[:60] if d.longName else "?"
                    found[key] = {
                        "symbol": d.contract.symbol,
                        "con_id": d.contract.conId,
                        "exchange": d.contract.exchange,
                        "currency": d.contract.currency,
                        "long_name": name,
                    }
                    print(f"DIRECT: {d.contract.symbol} id={d.contract.conId} name={name}", flush=True)
        except Exception:
            pass

# Method 3: Search for products on LSE (not LSEETF) that might be leveraged
for sym in bases:
    for cur in ["GBP", "USD"]:
        try:
            contract = ib_insync.Stock(sym, "LSE", cur)
            details = ib.reqContractDetails(contract)
            time.sleep(0.12)
            if details:
                d = details[0]
                key = d.contract.conId
                if key not in found:
                    name = d.longName[:60] if d.longName else "?"
                    found[key] = {
                        "symbol": d.contract.symbol,
                        "con_id": d.contract.conId,
                        "exchange": d.contract.exchange,
                        "currency": d.contract.currency,
                        "long_name": name,
                    }
                    print(f"LSE: {d.contract.symbol} id={d.contract.conId} name={name}", flush=True)
        except Exception:
            pass

ib.disconnect()

print(f"\n=== Total unique products found: {len(found)} ===")
# Print sorted by symbol
for key in sorted(found.keys(), key=lambda k: found[k]["symbol"]):
    v = found[key]
    name = v.get("long_name", "")
    print(f"  {v['symbol']:12s} id={v['con_id']:>12d}  exch={v['exchange']:8s}  cur={v['currency']:4s}  {name}")

# Output as JSON for processing
print("\n---JSON---")
print(json.dumps(list(found.values()), indent=2))
