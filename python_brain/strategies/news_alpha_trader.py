#!/usr/bin/env python3
"""news_alpha_trader — consumes news.alpha (LLM sentiment/impact/delta)
and publishes signals.core when a ticker has strong enough LLM conviction.

Closes the loop: before this, LLM spend was wasted on tickers that no
strategy ever traded. Now any ticker the LLM scores with |delta| > 8pp and
impact > 0.5 becomes a tradeable signal, provided the ticker is in our
contract universe.

Listens:   news.alpha
Publishes: signals.core
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

log = logging.getLogger("news-alpha-trader")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

NATS_URL = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
MIN_DELTA_PP = 8.0
MIN_IMPACT = 0.5
COOLDOWN_S = 900  # one signal per ticker per 15 min


def _load_contract_map() -> dict[str, dict]:
    """Parse contracts.toml into a ticker→{con_id, exchange, currency} map."""
    path = Path("/Users/rr/aegis-v5/config/contracts.toml")
    if not path.exists():
        return {}
    text = path.read_text()
    blocks = re.split(r"\[\[contracts\]\]", text)
    out: dict[str, dict] = {}
    for b in blocks[1:]:
        data: dict = {}
        for line in b.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'(\w+)\s*=\s*"?([^"]+)"?\s*(#.*)?$', line)
            if not m:
                continue
            k, v = m.group(1), m.group(2).strip()
            if k == "con_id":
                try:
                    v = int(v)
                except Exception:
                    continue
            data[k] = v
        sym = data.get("symbol")
        if sym and data.get("con_id"):
            out[sym.upper()] = data
    return out


async def run():
    import nats
    nc = await nats.connect(NATS_URL, name="aegis-v5-news-alpha-trader")
    log.info("news-alpha-trader connected to NATS")

    contract_map = _load_contract_map()
    log.info("loaded %d tradable contracts", len(contract_map))

    last_signal_ts: dict[str, float] = {}
    n_received = 0
    n_signaled = 0

    async def on_alpha(msg):
        nonlocal n_received, n_signaled
        try:
            d = json.loads(msg.data)
        except Exception:
            return
        n_received += 1

        ticker = (d.get("ticker") or "").upper()
        if not ticker:
            return
        contract = contract_map.get(ticker)
        if not contract:
            return  # not tradable

        # news.alpha payload uses: conviction_delta_pp, impact_magnitude, sentiment_score
        delta_pp = float(d.get("conviction_delta_pp")
                         or d.get("delta_pp")
                         or d.get("conviction_delta") or 0)
        impact = float(d.get("impact_magnitude") or d.get("impact") or 0)
        sent = float(d.get("sentiment_score") or d.get("sentiment") or 0)

        if abs(delta_pp) < MIN_DELTA_PP or impact < MIN_IMPACT:
            return  # not strong enough

        now = time.time()
        last_t = last_signal_ts.get(ticker, 0)
        if now - last_t < COOLDOWN_S:
            return  # cooldown

        # Map sentiment to side
        side = "BUY" if delta_pp > 0 else "SELL"

        # Use delta_pp as conviction strength: 8pp → 0.60, 20pp → 1.00
        conviction = max(0.60, min(1.00, 0.55 + abs(delta_pp) / 40.0))

        signal = {
            "signal_id": f"newsalpha-{uuid.uuid4().hex[:12]}",
            "ticker": ticker,
            "exchange": contract.get("exchange", "SMART"),
            "currency": contract.get("currency", "USD"),
            "con_id": int(contract.get("con_id")),
            "side": side,
            "strategy_name": "news_alpha",
            "conviction_score": conviction,
            "expected_fill_price": 1.0,  # sig2order will look up real price
            "features": {
                "news_delta_pp": delta_pp,
                "news_impact": impact,
                "news_sentiment": sent,
            },
        }
        try:
            await nc.publish("signals.core", json.dumps(signal).encode())
            last_signal_ts[ticker] = now
            n_signaled += 1
            log.info("signal %s %s conv=%.2f delta=%+.1fpp impact=%.2f",
                     side, ticker, conviction, delta_pp, impact)
        except Exception as e:
            log.warning("publish failed: %s", e)

    await nc.subscribe("news.alpha", cb=on_alpha)
    log.info("listening on news.alpha (min_delta=%.1fpp min_impact=%.2f cooldown=%ds)",
             MIN_DELTA_PP, MIN_IMPACT, COOLDOWN_S)

    while True:
        await asyncio.sleep(60)
        log.info("stats: %d alpha msgs seen, %d signals fired", n_received, n_signaled)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
