"""Delayed-data scanner. Publishes `watchlist.current` to NATS every cycle.

Phase 8 MVP uses a fixed universe + score dict. Swap for client_id=103 IBKR
delayed feed in production. Engine reads watchlist.current and hot-swaps
subscriptions; held positions are preserved (never unsubscribed).
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import List, Set

from python_brain.core.nats_client import NatsClient
from python_brain.scanner.thompson import ThompsonSampler


DATA_DIR = Path(os.environ.get("AEGIS_V5_DATA", "/Users/rr/aegis-v5/data"))
SCAN_SCORES = DATA_DIR / "scan_scores.json"


async def scan_once(held_positions: Set[str], slots: int = 100) -> List[str]:
    """Build a watchlist of up to `slots` tickers.

    Held positions ALWAYS included. Remainder filled by scores, then Thompson
    dark-horse posteriors for the last 40 slots.
    """
    scores: dict = {}
    if SCAN_SCORES.exists():
        try:
            scores = json.loads(SCAN_SCORES.read_text())
        except Exception:
            scores = {}
    ranked = [t for t, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)]
    watchlist: List[str] = list(held_positions)
    dark_horse_slots = 40 if slots >= 40 else max(1, slots // 3)
    top_slots = slots - len(watchlist) - dark_horse_slots
    for t in ranked:
        if t in watchlist:
            continue
        if len(watchlist) - len(held_positions) >= top_slots:
            break
        watchlist.append(t)
    sampler = ThompsonSampler(slots=dark_horse_slots)
    candidates = [t for t in ranked if t not in watchlist]
    for t in sampler.pick(candidates):
        if len(watchlist) >= slots:
            break
        watchlist.append(t)
    return watchlist


async def publish_watchlist(client: NatsClient, watchlist: List[str]) -> None:
    await client.publish("watchlist.current", {"tickers": watchlist, "count": len(watchlist)})


async def main() -> int:
    client = NatsClient.from_env()
    await client.connect()
    wl = await scan_once(held_positions=set(), slots=100)
    await publish_watchlist(client, wl)
    print(f"scanner: published watchlist of {len(wl)} tickers")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
