#!/usr/bin/env python3
"""Grow contracts.toml to 8000 by asking the running scanner (via NATS) to
perform reqMatchingSymbols + qualifyContracts using its already-authenticated
IBKR client. Avoids the apiStart hang that fresh clients are hitting.
"""
import asyncio
import json
import logging
import os
import random
import sys
import time
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger("build-8k-nats")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

try:
    import tomllib as _toml
except ImportError:
    import tomli as _toml  # type: ignore

NATS_URL = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
CONTRACTS_TOML = Path("/Users/rr/aegis-v5/config/contracts.toml")
TARGET = int(os.environ.get("TARGET_SIZE", "8000"))


def load_existing() -> set[tuple[str, str]]:
    out = set()
    try:
        with open(CONTRACTS_TOML, "rb") as f:
            data = _toml.load(f)
        for c in data.get("contracts", []):
            out.add((c.get("symbol", "").upper(), c.get("exchange", "")))
    except Exception as e:
        log.warning("load existing: %s", e)
    return out


def append_contracts(new_contracts: list[dict[str, Any]]) -> int:
    if not new_contracts:
        return 0
    chunk = []
    for c in new_contracts:
        chunk.append("[[contracts]]\n")
        chunk.append(f'symbol = "{c["symbol"]}"\n')
        chunk.append(f'exchange = "{c["exchange"]}"\n')
        chunk.append(f'currency = "{c["currency"]}"\n')
        chunk.append(f'con_id = {c["con_id"]}\n')
        chunk.append("\n")
    tmp = CONTRACTS_TOML.with_suffix(".tmp")
    with open(CONTRACTS_TOML, "r") as f:
        existing_text = f.read()
    with open(tmp, "w") as f:
        f.write(existing_text)
        if not existing_text.endswith("\n\n"):
            f.write("\n")
        f.writelines(chunk)
    os.replace(tmp, CONTRACTS_TOML)
    return len(new_contracts)


async def main():
    import nats  # type: ignore
    existing = load_existing()
    log.info("start with %d existing, target %d", len(existing), TARGET)
    if len(existing) >= TARGET:
        log.info("already at target")
        return

    nc = await nats.connect(NATS_URL, name="aegis-v5-builder-8k")
    log.info("connected to NATS")

    match_reply = f"scanner.match.result.{uuid.uuid4().hex[:8]}"
    qualify_reply = f"scanner.qualify.result.{uuid.uuid4().hex[:8]}"
    match_buf: dict[str, list] = {}
    qualify_buf: dict[str, list] = {}

    async def on_match(msg):
        d = json.loads(msg.data)
        rid = d.get("req_id", "")
        if d.get("ok"):
            match_buf[rid] = d.get("syms", [])
        else:
            match_buf[rid] = []

    async def on_qualify(msg):
        d = json.loads(msg.data)
        rid = d.get("req_id", "")
        if d.get("ok"):
            qualify_buf[rid] = d.get("qualified", [])
        else:
            qualify_buf[rid] = []

    await nc.subscribe(match_reply, cb=on_match)
    await nc.subscribe(qualify_reply, cb=on_qualify)

    # Step 1: discover candidates via reqMatchingSymbols (alphabet sweep)
    prefixes = []
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
        prefixes.append(c)
    for a in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        for b in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            prefixes.append(a + b)
    random.shuffle(prefixes)

    log.info("discovery: %d prefixes to query via NATS", len(prefixes))
    discovered: dict[str, dict] = {}   # symbol -> {sym, exchange, currency}

    # Fire match requests in parallel batches of 8
    PAR = 8
    for i in range(0, len(prefixes), PAR):
        batch = prefixes[i:i + PAR]
        rids = []
        for p in batch:
            rid = f"m-{i}-{p}"
            rids.append((rid, p))
            await nc.publish("scanner.match", json.dumps({
                "prefix": p,
                "req_id": rid,
                "reply_to": match_reply,
            }).encode())

        # Wait for responses
        deadline = time.time() + 12
        while time.time() < deadline and not all(rid in match_buf for rid, _ in rids):
            await asyncio.sleep(0.3)

        for rid, _ in rids:
            for s in match_buf.pop(rid, []):
                sym = s["symbol"].upper()
                # Prefer US (SMART) — if symbol not already discovered, take it
                if sym not in discovered:
                    discovered[sym] = s

        if (i // PAR) % 10 == 0:
            log.info("  progress: %d prefixes done, %d unique discovered so far",
                     i + len(batch), len(discovered))

    log.info("discovery complete: %d unique symbols candidates", len(discovered))

    # Drop symbols already qualified (under any exchange)
    already_syms = {s for s, _ in existing}
    fresh = [(s, d) for s, d in discovered.items() if s not in already_syms]
    log.info("candidates new to us: %d", len(fresh))

    # Step 2: qualify in batches via NATS
    qualified_count = len(existing)
    BATCH = 40
    append_buf: list[dict] = []
    FLUSH_EVERY = 200

    for i in range(0, len(fresh), BATCH):
        if qualified_count >= TARGET:
            break
        batch = fresh[i:i + BATCH]
        items = [
            {"symbol": sym, "exchange": "SMART", "currency": "USD"}
            for sym, _ in batch
        ]
        rid = f"q-{i}"
        await nc.publish("scanner.qualify", json.dumps({
            "items": items, "req_id": rid, "reply_to": qualify_reply,
        }).encode())

        # Wait for reply
        deadline = time.time() + 25
        while time.time() < deadline and rid not in qualify_buf:
            await asyncio.sleep(0.5)
        qualified = qualify_buf.pop(rid, [])

        for c in qualified:
            key = (c["symbol"].upper(), c["exchange"])
            if key not in existing:
                existing.add(key)
                append_buf.append(c)
                qualified_count += 1

        if (i // BATCH) % 5 == 0:
            log.info("qualify round %d: total %d / %d  (buffer=%d)",
                     i // BATCH, qualified_count, TARGET, len(append_buf))

        if len(append_buf) >= FLUSH_EVERY:
            written = append_contracts(append_buf)
            log.info(">>> wrote %d contracts (total now: %d)", written, qualified_count)
            append_buf = []

        await asyncio.sleep(0.4)

    if append_buf:
        written = append_contracts(append_buf)
        log.info(">>> final flush: wrote %d (total now: %d)", written, qualified_count)

    log.info("DONE. Qualified: %d", qualified_count)
    await nc.drain()


if __name__ == "__main__":
    asyncio.run(main())
