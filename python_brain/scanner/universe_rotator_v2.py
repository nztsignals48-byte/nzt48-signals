"""Universe rotator v2 — session-aware time-of-day weighting.

Rules (UK local time):
    00:00 - 08:00   Asia session        asia×1.0, eu×0.2, us×0.3 (after-hours)
    08:00 - 13:30   Europe early+Asia   asia×0.4 (fading), eu×1.0, us×0.2
    13:30 - 16:30   EU + US overlap     eu×1.0, us×1.0, asia×0.0
    16:30 - 21:00   US core             us×1.0, eu×0.2 (closed but post), asia×0.0
    21:00 - 24:00   US post + Asia warm us×0.6, asia×0.4, eu×0.0

Each scanner hit carries a `session` tag (from scanner_templates.json).
The rotator multiplies the base score by the session weight for the
current UK clock hour. This biases the Live 100 toward whichever market
is trading hottest right now.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Set

try:
    from zoneinfo import ZoneInfo
    UK_TZ = ZoneInfo("Europe/London")
except Exception:
    UK_TZ = timezone.utc  # fallback

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

ROOT = Path(__file__).resolve().parent.parent.parent
WATCHLIST_PATH = ROOT / "data" / "watchlist.v5.json"


def session_weights_now() -> Dict[str, float]:
    """UK-clock-hour → session weights."""
    h = datetime.now(tz=UK_TZ).hour
    if 0 <= h < 8:
        return {"asia": 1.0, "eu": 0.2, "us": 0.3}
    if 8 <= h < 13:
        return {"asia": 0.4, "eu": 1.0, "us": 0.2}
    if 13 <= h < 17:
        return {"asia": 0.0, "eu": 1.0, "us": 1.0}
    if 17 <= h < 21:
        return {"asia": 0.0, "eu": 0.2, "us": 1.0}
    return {"asia": 0.4, "eu": 0.0, "us": 0.6}


SCAN_TYPE_BASE_WEIGHTS = {
    "top_movers_usd":      1.0,
    "bottom_movers_usd":   1.0,
    "top_volume_usd":      0.8,
    "hot_by_price_usd":    0.7,
    "top_trade_count_usd": 0.6,
    "high_opt_iv_usd":     0.9,
    "top_movers_gbp":      0.9,
    "top_volume_gbp":      0.7,
    "top_movers_eur":      0.8,
    "top_volume_eur":      0.6,
    "top_movers_hk":       0.9,
    "top_volume_hk":       0.7,
    "top_movers_jp":       0.9,
    "top_volume_jp":       0.7,
    "top_movers_sg":       0.7,
}


@dataclass
class TickerState:
    ticker: str
    exchange: str
    currency: str
    con_id: int
    session: str
    score: float = 0.0
    first_seen_ts: float = field(default_factory=time.time)
    last_updated_ts: float = field(default_factory=time.time)
    in_live_list: bool = False


@dataclass
class UniverseRotatorV2:
    nats_url: str = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    live_list_size: int = 100
    decay_per_minute: float = 0.9
    min_dwell_seconds: float = 180.0
    rotation_interval_s: int = 5  # v5: 30s → 5s for fast ticker churn
    max_changes_per_rotation: int = 20  # more aggressive than v1

    async def run(self) -> None:
        import nats  # type: ignore
        nc = await nats.connect(self.nats_url, name="aegis-v5-rotator-v2")
        log.info("rotator v2 connected to NATS %s", self.nats_url)

        state: Dict[str, TickerState] = {}
        held: Set[str] = set()

        async def on_hit(msg):
            try:
                p = json.loads(msg.data)
                ticker = p["ticker"]
                scan_type = msg.subject.split(".")[-1]
                session = p.get("session", "us")
                session_w = session_weights_now().get(session, 0.5)
                base_w = SCAN_TYPE_BASE_WEIGHTS.get(scan_type, 0.5)
                rank_score = max(0.0, 1.0 - (p.get("rank", 50) - 1) / 50.0)
                delta = base_w * session_w * rank_score
                if delta <= 0:
                    return  # current session has this region weighted to zero
                st = state.get(ticker)
                if st is None:
                    st = TickerState(
                        ticker=ticker,
                        exchange=p.get("exchange") or "SMART",
                        currency=p.get("currency") or "USD",
                        con_id=int(p.get("con_id") or 0),
                        session=session,
                    )
                    state[ticker] = st
                st.score += delta
                st.last_updated_ts = time.time()
            except Exception as e:
                log.warning("bad scan hit: %s", e)

        async def on_filled(msg):
            try:
                p = json.loads(msg.data)
                t = p.get("ticker")
                if t:
                    held.add(t)
            except Exception:
                pass

        async def on_position(msg):
            """positions.open → pin; positions.close → release."""
            try:
                p = json.loads(msg.data)
                t = p.get("ticker")
                if not t:
                    return
                subj = msg.subject
                if subj.endswith(".open"):
                    held.add(t)
                elif subj.endswith(".close"):
                    held.discard(t)
            except Exception:
                pass

        # Track per-ticker last-seen price for delayed-universe movement scoring
        delayed_last: Dict[str, tuple[float, float]] = {}  # ticker -> (ts, price)

        async def on_delayed(msg):
            """Score tickers based on price movement from IBKR delayed snapshots.
            Boosts tickers that move relative to a few minutes ago, so the
            rotator can promote them into the live-sub watchlist."""
            try:
                p = json.loads(msg.data)
                t = p.get("ticker")
                if not t:
                    return
                price = p.get("last") or p.get("bid") or p.get("ask") or 0.0
                try:
                    price = float(price)
                except Exception:
                    return
                if price <= 0:
                    return
                now_ts = time.time()
                prev = delayed_last.get(t)
                delayed_last[t] = (now_ts, price)
                if prev is None:
                    return
                prev_ts, prev_px = prev
                if prev_px <= 0 or (now_ts - prev_ts) < 60:
                    return
                # Percent move since last snapshot, scaled by session weighting
                pct_move = abs((price - prev_px) / prev_px)
                if pct_move < 0.005:  # < 0.5% not interesting
                    return
                session = "us"  # delayed streamer doesn't tag session yet
                session_w = session_weights_now().get(session, 0.5)
                # Cap contribution: 5% move = weight 1.0, scale linearly
                movement_score = min(pct_move / 0.05, 1.0) * session_w * 0.8
                st = state.get(t)
                if st is None:
                    st = TickerState(
                        ticker=t,
                        exchange=p.get("exchange") or "SMART",
                        currency=p.get("currency") or "USD",
                        con_id=int(p.get("con_id") or 0),
                        session=session,
                    )
                    state[t] = st
                st.score += movement_score
                st.last_updated_ts = now_ts
            except Exception:
                pass

        await nc.subscribe("scanner.hits.*", cb=on_hit)
        await nc.subscribe("orders.filled", cb=on_filled)
        await nc.subscribe("positions.open", cb=on_position)
        await nc.subscribe("positions.close", cb=on_position)
        await nc.subscribe("ticks.delayed.*", cb=on_delayed)

        last_rot = 0.0
        while True:
            await asyncio.sleep(5)
            now = time.time()
            decay = self.decay_per_minute ** (5.0 / 60.0)
            for st in state.values():
                st.score *= decay

            if now - last_rot < self.rotation_interval_s:
                continue
            last_rot = now

            ranked = sorted(
                [s for s in state.values() if s.con_id > 0],
                key=lambda s: s.score,
                reverse=True,
            )
            desired: Dict[str, TickerState] = {}
            for t in held:
                if t in state:
                    desired[t] = state[t]
            for s in ranked:
                if len(desired) >= self.live_list_size:
                    break
                desired.setdefault(s.ticker, s)

            current_live = {t for t, s in state.items() if s.in_live_list}
            to_add = [t for t in desired if t not in current_live]
            to_evict = [
                t for t in (current_live - set(desired))
                if t not in held and (now - state[t].first_seen_ts) > self.min_dwell_seconds
            ]

            changes = 0
            added, evicted = [], []
            for t in to_add[: self.max_changes_per_rotation]:
                state[t].in_live_list = True
                added.append(t); changes += 1
            for t in to_evict[: max(0, self.max_changes_per_rotation - changes)]:
                state[t].in_live_list = False
                evicted.append(t); changes += 1

            if changes or not WATCHLIST_PATH.exists():
                live = [
                    {
                        "ticker": st.ticker,
                        "exchange": st.exchange,
                        "currency": st.currency,
                        "con_id": st.con_id,
                        "session": st.session,
                        "score": round(st.score, 3),
                    }
                    for st in state.values()
                    if st.in_live_list
                ]
                live.sort(key=lambda x: -x["score"])
                WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
                WATCHLIST_PATH.write_text(json.dumps(live, indent=2))
                weights = session_weights_now()
                payload = {
                    "ts": now,
                    "uk_hour": datetime.now(tz=UK_TZ).hour,
                    "weights": weights,
                    "added": added,
                    "evicted": evicted,
                    "size": len(live),
                    "by_session": {
                        "asia": sum(1 for x in live if x["session"] == "asia"),
                        "eu":   sum(1 for x in live if x["session"] == "eu"),
                        "us":   sum(1 for x in live if x["session"] == "us"),
                    },
                }
                await nc.publish("universe.rotation", json.dumps(payload).encode("utf-8"))
                log.info(
                    "rotation: +%d -%d live=%d | asia=%d eu=%d us=%d | weights=%s",
                    len(added), len(evicted), len(live),
                    payload["by_session"]["asia"], payload["by_session"]["eu"], payload["by_session"]["us"],
                    weights,
                )


if __name__ == "__main__":
    asyncio.run(UniverseRotatorV2().run())
