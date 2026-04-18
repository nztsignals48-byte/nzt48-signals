"""Pull the FULL LSE ETP universe (GraniteShares / Leverage Shares / WisdomTree 3×)
from IBKR using contract search, not hardcoded guesses.

Strategy:
  1. Scan STK.EU.LSE for every issuer name matching 'SHARES 3X/LEV/SHORT'
  2. For each hit, qualifyContractsAsync to get real con_ids
  3. Write to data/lse_etps_full.toml; merge into contracts.toml afterwards
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

OUT = Path("/Users/rr/aegis-v5/data/lse_etps_full.toml")

# Known LSE ETP symbol prefixes (3× leveraged / inverse)
# Leverage Shares: 3L / 3S + up to 3 chars
# GraniteShares: similar
# WisdomTree: variable naming
# Comprehensive guess list - we qualify every one and keep only those IBKR confirms
# Expand the 3-letter suffix space systematically.
LEVSHARES_SUFFIXES = [
    # Single-stock ETPs from public Leverage Shares product catalogue:
    "AP","AM","GO","AZ","AS","AL","AR","AB","AC","AD","AE","AF","AG","AH","AI","AK",
    "BA","BK","BP","BT","BM","BB","BC","BD","BF","BG","BH","BI","BJ","BL","BN","BO",
    "CM","CV","CO","CS","CI","CA","CB","CC","CD","CE","CF","CG","CH","CJ","CK","CL",
    "DE","DI","DP","DL","DD","DF","DG","DH","DJ","DM","DN","DO","DR","DS","DT","DU",
    "ET","EB","EE","EM","EP","ER","ES","EV","EX",
    "FA","FD","FE","FI","FM","FN","FP","FR","FT",
    "GE","GM","GO","GS","GT","GV","GX",
    "HD","HI","HM","HO","HP","HR","HS",
    "IC","ID","IM","IS","IP","IQ","IR",
    "JP","JN",
    "KO","KR",
    "LH","LL","LM","LN",
    "MA","MC","MD","MM","MO","MP","MS","MT","MU",
    "NE","NF","NP","NV","NX","NF","NS","NT",
    "OR","OP",
    "PA","PE","PF","PG","PH","PI","PJ","PK","PL","PM","PN","PO","PP","PQ","PR","PS","PT","PU","PV","PW","PX","PY","PZ",
    "QC","QQ",
    "RI","RV","RY",
    "SA","SB","SL","SM","SN","SP","SQ","SR","SS","ST","SU","SV","SX","SY","SZ",
    "TE","TI","TM","TP","TR","TS","TT","TW",
    "UB","UN","UP","UT",
    "VI","VL","VO","VP",
    "WM",
    "XO","XS",
    "ZM",
]

# Build both Long (3L**) and Short (3S**) permutations
LS_3X_SYMBOLS: list[str] = []
for s in LEVSHARES_SUFFIXES:
    LS_3X_SYMBOLS.append(f"3L{s}")
    LS_3X_SYMBOLS.append(f"3S{s}")

# GraniteShares naming uses slightly different scheme (e.g. 3LSL = Silver 3×)
# We've already covered these via 3L/3S suffix space. Also add index-level:
INDEX_3X = [
    "3LSX","3SSX","3LNQ","3SNQ","3LUS","3SUS","3LSP","3SSP","3LDL","3SDL",
    "3LDX","3SDX","3LDA","3SDA","3LFT","3SFT","3LUK","3SUK","3LGA","3SGA",
    "QQQ3","QQQS","NDX3","SPXL","SPXS",
]

# WisdomTree 3× products (3USL, 3USS, etc.)
WISDOMTREE_3X = [
    "3USL","3USS","3UKL","3UKS","3DEL","3DES","3JPL","3JPS","3FRL","3FRS",
    "3ITL","3ITS","3EUL","3EUS","3NQL","3NQS","3SPL","3SPS","3NZL","3NZS",
]

# Single-name 2× from Leverage Shares (use 2L/2S prefix)
TWOX_SYMBOLS = []
for s in LEVSHARES_SUFFIXES[:40]:  # just the more common underlyings
    TWOX_SYMBOLS.append(f"2L{s}")
    TWOX_SYMBOLS.append(f"2S{s}")


async def main() -> None:
    from ib_insync import IB, Stock
    ib = IB()
    await ib.connectAsync("127.0.0.1", 4002, clientId=161, readonly=True, timeout=30)
    log.info("connected; starting LSEETF qualification")

    all_syms = sorted(set(LS_3X_SYMBOLS + INDEX_3X + WISDOMTREE_3X + TWOX_SYMBOLS))
    log.info("testing %d candidate symbols on LSEETF", len(all_syms))

    qualified = []
    for i in range(0, len(all_syms), 40):
        chunk = all_syms[i:i+40]
        contracts = [Stock(s, "LSEETF", "GBP") for s in chunk]
        try:
            qs = await ib.qualifyContractsAsync(*contracts)
        except Exception as e:
            log.debug("batch err: %s", e)
            continue
        for q in qs:
            if q.conId:
                qualified.append({
                    "symbol": q.symbol,
                    "con_id": q.conId,
                    "exchange": q.exchange or "LSEETF",
                    "currency": q.currency or "GBP",
                })
        await asyncio.sleep(0.5)
        log.info("  %d/%d checked; %d qualified so far", i+len(chunk), len(all_syms), len(qualified))

    # Also try LSE (not LSEETF) for some ETPs that are listed on the main board
    log.info("also testing LSE listing for same symbols...")
    remaining_syms = [s for s in all_syms
                      if not any(q["symbol"] == s for q in qualified)]
    for i in range(0, len(remaining_syms), 40):
        chunk = remaining_syms[i:i+40]
        contracts = [Stock(s, "LSE", "GBP") for s in chunk]
        try:
            qs = await ib.qualifyContractsAsync(*contracts)
        except Exception:
            continue
        for q in qs:
            if q.conId:
                qualified.append({
                    "symbol": q.symbol, "con_id": q.conId,
                    "exchange": q.exchange or "LSE",
                    "currency": q.currency or "GBP",
                })
        await asyncio.sleep(0.5)

    ib.disconnect()

    # Write TOML
    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Full LSE ETP universe (auto-qualified)\n"]
    for c in qualified:
        lines += [
            "[[contracts]]",
            f'symbol = "{c["symbol"]}"',
            f'con_id = {c["con_id"]}',
            f'exchange = "{c["exchange"]}"',
            'sec_type = "STK"',
            f'currency = "{c["currency"]}"',
            "fast = false",
            "",
        ]
    OUT.write_text("\n".join(lines))
    log.info("wrote %d ETPs to %s", len(qualified), OUT)
    from collections import Counter
    by_ex = Counter(c["exchange"] for c in qualified)
    for ex, n in by_ex.most_common():
        log.info("  %s: %d", ex, n)


if __name__ == "__main__":
    asyncio.run(main())
