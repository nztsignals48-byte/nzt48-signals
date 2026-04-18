"""news_to_intel — pipes LLM-scored news into the engine's intel files.

When llm_news_analyzer emits news.alpha with a sentiment_score and
conviction_delta_pp, this service rewrites
data/intel/sentiment_long_short.json[tickers][TICKER] with the latest
values so strategy sentiment_long_short picks them up on next evaluation.

Without this, LLM news is invisible to the engine's bar-triggered
strategies (only sig2order consumed it). Now:

    IBKR news → llm_news_analyzer (Haiku) → news.alpha NATS
              → news_to_intel                  → data/intel/sentiment_long_short.json
              → sentiment_long_short strategy sees it on next tick
              → StrategyView → conviction_engine → sig2order → IBKR order
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

SENT_PATH = Path("/Users/rr/aegis-v5/data/intel/sentiment_long_short.json")


async def main() -> None:
    import nats  # type: ignore
    nc = await nats.connect(os.environ.get("NATS_URL", "nats://127.0.0.1:4222"),
                            name="aegis-v5-news-to-intel")
    log.info("news_to_intel connected to NATS")

    count = 0

    async def on_alpha(msg):
        nonlocal count
        try:
            a = json.loads(msg.data)
        except Exception:
            return
        t = a.get("ticker")
        if not t:
            return
        sent = float(a.get("sentiment_score") or 0)
        impact = float(a.get("impact_magnitude") or 0)
        delta = float(a.get("conviction_delta_pp") or 0)

        # Load current intel
        try:
            data = json.loads(SENT_PATH.read_text())
        except Exception:
            data = {"schema_version": 1, "tickers": {}}
        data.setdefault("tickers", {})
        cur = data["tickers"].get(t, {})
        # EWMA to avoid whip-saw on every article.
        prev_score = float(cur.get("score") or 0)
        new_score = prev_score * 0.7 + sent * 0.3
        data["tickers"][t] = {
            "score": round(new_score, 4),
            "direction": "long" if new_score > 0.15 else ("short" if new_score < -0.15 else "neutral"),
            "confidence": round(impact, 3),
            "conviction_delta_pp": round(delta, 2),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        SENT_PATH.write_text(json.dumps(data, indent=1))
        count += 1
        if count % 10 == 0:
            log.info("news_to_intel wrote %d entries total; latest: %s score=%.2f",
                     count, t, new_score)

    await nc.subscribe("news.alpha", cb=on_alpha)
    log.info("listening on news.alpha; writing to %s", SENT_PATH)
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
