#!/usr/bin/env python3
"""Grow contracts.toml to 8,000+ qualified contracts.

Approach:
1. Load existing contracts.toml (current qualified set).
2. Fetch IBKR scanner's top-N from every template (active movers) — guaranteed-liquid.
3. Walk the alphabet A..Z × 0..9 via reqMatchingSymbolsAsync (max ~16 per query),
   yielding 1,000-10,000 symbol candidates.
4. Pull NASDAQ/NYSE/LSE/XETRA/HK/TSE/ASX/SGX tickers from static index-constituent
   lists as fallback seeds.
5. De-dupe against already-qualified set.
6. Qualify in batches of 50, filter to those with valid con_ids, append to
   contracts.toml atomically.

Runs until 8,000 reached OR all candidates exhausted. Rate-limited ~2 qualify
calls/sec to respect IBKR throttling.
"""
import asyncio
import json
import logging
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("build-8k")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

try:
    import tomllib as _toml
except ImportError:
    import tomli as _toml  # type: ignore

from ib_insync import IB, Stock, util  # type: ignore

CONTRACTS_TOML = Path("/Users/rr/aegis-v5/config/contracts.toml")
TARGET = int(os.environ.get("TARGET_SIZE", "8000"))
BATCH = 40
PAUSE_S = 0.4


# Exchange-currency map for seeded exchanges
EX_CCY = {
    "SMART": "USD", "NYSE": "USD", "NASDAQ": "USD", "ARCA": "USD", "AMEX": "USD",
    "LSE": "GBP", "LSEETF": "GBP",
    "IBIS": "EUR", "SBF": "EUR", "AEB": "EUR", "BVME": "EUR", "BM": "EUR", "EBS": "CHF",
    "SEHK": "HKD", "TSEJ": "JPY", "SGX": "SGD", "ASX": "AUD", "KSE": "KRW", "TWSE": "TWD",
}


def load_existing() -> tuple[set[str], list[str]]:
    """Return (set of (symbol,exchange) keys, raw lines of file)."""
    existing = set()
    lines: list[str] = []
    try:
        with open(CONTRACTS_TOML, "r") as f:
            lines = f.readlines()
        with open(CONTRACTS_TOML, "rb") as f:
            data = _toml.load(f)
        for c in data.get("contracts", []):
            key = (c.get("symbol", "").upper(), c.get("exchange", ""))
            existing.add(key)
    except Exception as e:
        log.warning("load existing: %s", e)
    return existing, lines


def append_contracts(new_contracts: list[dict[str, Any]]) -> int:
    """Atomically append to contracts.toml. Returns count written."""
    if not new_contracts:
        return 0
    chunk_lines: list[str] = []
    for c in new_contracts:
        chunk_lines.append("[[contracts]]\n")
        chunk_lines.append(f'symbol = "{c["symbol"]}"\n')
        chunk_lines.append(f'exchange = "{c["exchange"]}"\n')
        chunk_lines.append(f'currency = "{c["currency"]}"\n')
        chunk_lines.append(f'con_id = {c["con_id"]}\n')
        chunk_lines.append("\n")
    tmp = CONTRACTS_TOML.with_suffix(".tmp")
    with open(CONTRACTS_TOML, "r") as f:
        existing_text = f.read()
    with open(tmp, "w") as f:
        f.write(existing_text)
        if not existing_text.endswith("\n\n"):
            f.write("\n")
        f.writelines(chunk_lines)
    os.replace(tmp, CONTRACTS_TOML)
    return len(new_contracts)


async def expand_via_matching(ib: IB, seen: set[str],
                              max_rounds: int = 500) -> list[str]:
    """Use reqMatchingSymbolsAsync with alphabet + digit prefixes to discover
    many symbols. Returns list of candidate symbols."""
    discovered: set[str] = set()
    prefixes = []
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
        prefixes.append(c)
    for a in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        for b in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            prefixes.append(a + b)
    random.shuffle(prefixes)

    log.info("discovery via matching symbols: %d prefixes to try", len(prefixes))
    for i, p in enumerate(prefixes[:max_rounds]):
        try:
            res = await asyncio.wait_for(
                ib.reqMatchingSymbolsAsync(p), timeout=5)
            for m in res or []:
                c = m.contract
                if c.secType == "STK" and c.symbol:
                    discovered.add(c.symbol.upper())
        except Exception:
            pass
        if (i + 1) % 50 == 0:
            log.info("  prefix %d/%d; discovered so far: %d",
                     i + 1, len(prefixes), len(discovered))
        await asyncio.sleep(0.1)
    log.info("matching-symbols discovery complete: %d unique symbols", len(discovered))
    return list(discovered)


async def qualify_batch(ib: IB, symbols: list[str], exchange: str,
                        currency: str) -> list[dict[str, Any]]:
    """Try to qualify a list of symbols on (exchange, currency)."""
    stocks = [Stock(symbol=s, exchange=exchange, currency=currency)
              for s in symbols]
    try:
        q = await asyncio.wait_for(ib.qualifyContractsAsync(*stocks), timeout=20)
    except Exception as e:
        log.debug("qualify batch error on %s/%s: %s", exchange, currency, e)
        return []
    out = []
    for c in q:
        if c and c.conId:
            out.append({
                "symbol": c.symbol,
                "exchange": c.primaryExchange or exchange,
                "currency": c.currency or currency,
                "con_id": c.conId,
            })
    return out


async def main():
    existing, _ = load_existing()
    log.info("starting with %d contracts already qualified, target %d",
             len(existing), TARGET)
    if len(existing) >= TARGET:
        log.info("already at target")
        return

    ib = IB()
    client_id = random.randint(500, 900)
    try:
        await ib.connectAsync("127.0.0.1", 4002, clientId=client_id, timeout=30)
    except Exception as e:
        log.error("IBKR connect failed clientId=%d: %s", client_id, e)
        return
    log.info("IBKR connected clientId=%d", client_id)

    # Step 1: discover candidate symbols
    candidates = await expand_via_matching(ib, existing, max_rounds=500)
    # Filter out already-qualified US symbols (mostly duplicates)
    candidates = [
        s for s in candidates
        if (s, "SMART") not in existing and (s, "NASDAQ") not in existing
        and (s, "NYSE") not in existing
    ]
    log.info("candidates to qualify: %d", len(candidates))

    qualified_count = len(existing)
    buffer: list[dict[str, Any]] = []
    flush_every = 200

    # Step 2: qualify candidates in batches
    for i in range(0, len(candidates), BATCH):
        if qualified_count >= TARGET:
            break
        batch = candidates[i:i + BATCH]

        # Try each candidate on SMART (US default)
        results = await qualify_batch(ib, batch, "SMART", "USD")
        for c in results:
            key = (c["symbol"].upper(), c["exchange"])
            if key not in existing:
                existing.add(key)
                buffer.append(c)
                qualified_count += 1

        if (i // BATCH) % 5 == 0:
            log.info("round %d: qualified %d / target %d (buffer=%d)",
                     i // BATCH, qualified_count, TARGET, len(buffer))

        if len(buffer) >= flush_every:
            written = append_contracts(buffer)
            log.info(">>> wrote %d new contracts to contracts.toml (total now: %d)",
                     written, qualified_count)
            buffer = []

        await asyncio.sleep(PAUSE_S)

    # Final flush
    if buffer:
        written = append_contracts(buffer)
        log.info(">>> final flush: wrote %d new contracts (total now: %d)",
                 written, qualified_count)

    log.info("DONE. Total qualified: %d", qualified_count)
    ib.disconnect()


if __name__ == "__main__":
    util.patchAsyncio()
    asyncio.run(main())
