"""Universe rotator — promotes scanner hits into the Live 100.

Consumes:
    scanner.hits.*     (from ibkr_scanner.py, delayed data tier)
    positions.open     (from engine; never unsubscribe a held ticker)

Produces:
    watchlist.v5.json   — current Live 100 intent
    contracts.toml      — rewritten if the Rust bridge supports SIGHUP reload
    universe.rotation   — NATS signal so the Rust bridge can rotate live

Rules (from V4 Session 31):
    - held_tickers never evicted
    - max 10 changes per rotation (anti-thrash)
    - scanner score = weighted rank * scan_type weight, decay 0.9/min
    - minimum dwell time on live list: 5 minutes (no immediate eviction)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Set

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

ROOT = Path(__file__).resolve().parent.parent.parent
WATCHLIST_PATH = ROOT / "data" / "watchlist.v5.json"

SCAN_TYPE_WEIGHTS = {
    "top_movers_usd":    1.0,
    "bottom_movers_usd": 1.0,
    "top_volume_usd":    0.8,
    "hot_by_price":      0.7,
    "top_trade_count":   0.6,
    "high_opt_iv":       0.9,
    "top_movers_gbp":    0.9,
    "top_volume_gbp":    0.7,
    "top_movers_eur":    0.7,
    "top_movers_jpy":    0.6,
}


@dataclass
class TickerState:
    ticker: str
    exchange: str
    con_id: int
    score: float = 0.0
    first_seen_ts: float = field(default_factory=time.time)
    last_updated_ts: float = field(default_factory=time.time)
    in_live_list: bool = False


@dataclass
class UniverseRotator:
    nats_url: str = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    live_list_size: int = 100
    decay_per_minute: float = 0.9
    min_dwell_seconds: float = 300.0
    rotation_interval_s: int = 30
    max_changes_per_rotation: int = 10

    async def run(self) -> None:
        import nats  # type: ignore
        nc = await nats.connect(self.nats_url, name="aegis-v5-universe-rotator")
        log.info("rotator connected to NATS %s", self.nats_url)

        state: Dict[str, TickerState] = {}
        held_tickers: Set[str] = set()

        async def on_hit(msg):
            try:
                p = json.loads(msg.data)
                ticker = p["ticker"]
                scan_type = msg.subject.split(".")[-1]
                weight = SCAN_TYPE_WEIGHTS.get(scan_type, 0.5)
                rank_score = max(0.0, 1.0 - (p.get("rank", 50) - 1) / 50.0)
                delta = weight * rank_score
                st = state.get(ticker)
                if st is None:
                    st = TickerState(
                        ticker=ticker,
                        exchange=p.get("exchange") or "SMART",
                        con_id=int(p.get("con_id") or 0),
                    )
                    state[ticker] = st
                st.score += delta
                st.last_updated_ts = time.time()
            except Exception as e:
                log.warning("bad scan hit: %s", e)

        async def on_position(msg):
            try:
                p = json.loads(msg.data)
                ticker = p.get("ticker")
                is_open = p.get("is_open", True)
                if ticker:
                    if is_open:
                        held_tickers.add(ticker)
                    else:
                        held_tickers.discard(ticker)
            except Exception:
                pass

        await nc.subscribe("scanner.hits.*", cb=on_hit)
        await nc.subscribe("positions.open",  cb=on_position)
        await nc.subscribe("positions.close", cb=on_position)

        last_rot_ts = 0.0
        while True:
            await asyncio.sleep(5)
            now = time.time()
            # Decay scores per minute.
            decay_factor = self.decay_per_minute ** (5.0 / 60.0)
            for st in state.values():
                st.score *= decay_factor

            if now - last_rot_ts < self.rotation_interval_s:
                continue
            last_rot_ts = now

            # Build candidate live list.
            ranked = sorted(
                [s for s in state.values() if s.con_id > 0],
                key=lambda s: s.score,
                reverse=True,
            )
            desired: Set[str] = set(held_tickers)
            for st in ranked:
                if len(desired) >= self.live_list_size:
                    break
                desired.add(st.ticker)

            current_live = {t for t, s in state.items() if s.in_live_list}
            to_add = (desired - current_live)
            # Only evict those over min_dwell.
            to_evict = [
                t for t in (current_live - desired)
                if t not in held_tickers
                and (now - state[t].first_seen_ts) > self.min_dwell_seconds
            ]

            changes = 0
            added, evicted = [], []
            for t in list(to_add)[: self.max_changes_per_rotation]:
                state[t].in_live_list = True
                added.append(t)
                changes += 1
            for t in to_evict[: max(0, self.max_changes_per_rotation - changes)]:
                state[t].in_live_list = False
                evicted.append(t)
                changes += 1

            if changes:
                # Persist current live list.
                live = [
                    {
                        "ticker": st.ticker,
                        "exchange": st.exchange,
                        "con_id": st.con_id,
                        "score": round(st.score, 3),
                    }
                    for st in state.values()
                    if st.in_live_list
                ]
                live.sort(key=lambda x: -x["score"])
                WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
                WATCHLIST_PATH.write_text(json.dumps(live, indent=2))

                payload = {
                    "ts": now,
                    "added": added,
                    "evicted": evicted,
                    "size": len(live),
                }
                await nc.publish("universe.rotation", json.dumps(payload).encode("utf-8"))
                log.info(
                    "rotation: +%d -%d, live=%d (sample: %s)",
                    len(added), len(evicted), len(live),
                    ", ".join(added[:5]) or "(no adds)",
                )


if __name__ == "__main__":
    asyncio.run(UniverseRotator().run())
