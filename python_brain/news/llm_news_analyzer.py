"""llm_news_analyzer — Anthropic Claude analyses each news event.

Subscribes: news.raw
Publishes: news.alpha  (with sentiment_score [-1..+1], conviction_delta_pp,
                        rationale_short)

One Haiku call per news event. Cost governor caps total spend.

Model: claude-haiku-4-5-20251001  (cheap + fast, sufficient for 1-2 sentence
headlines). Falls back to rule-based sentiment if API unavailable.
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

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
SYSTEM_PROMPT = """You are a financial news analyst for a paper-trading engine.
For each news headline, output ONLY a JSON object with keys:
  sentiment_score      (-1.0 to +1.0; negative=bearish, positive=bullish)
  impact_magnitude     (0.0 to 1.0; 0=noise, 1=major market event)
  conviction_delta_pp  (-30 to +15; how this shifts a signal's conviction)
  rationale_short      (1 sentence, <= 20 words)
No prose. No markdown. JSON only."""


@dataclass
class LlmNewsAnalyzer:
    nats_url: str = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    daily_cost_cap_usd: float = 15.0
    cost_path: Path = Path("/Users/rr/aegis-v5/data/llm_cost_today.json")
    model: str = ANTHROPIC_MODEL

    async def run(self) -> None:
        import nats  # type: ignore

        nc = await nats.connect(self.nats_url, name="aegis-v5-llm-news")
        log.info("LLM news analyzer connected to NATS %s", self.nats_url)

        if not self.api_key:
            log.warning("ANTHROPIC_API_KEY not set — falling back to rule-based scoring")

        try:
            import anthropic  # type: ignore
            client = anthropic.Anthropic(api_key=self.api_key) if self.api_key else None
        except ImportError:
            log.warning("anthropic SDK not installed; rule-based scoring")
            client = None

        async def on_news(msg):
            try:
                n = json.loads(msg.data)
            except Exception:
                return
            headline = n.get("headline") or ""
            if not headline.strip():
                return

            # Cost check
            cost = self._load_cost()
            if cost >= self.daily_cost_cap_usd:
                log.warning("daily LLM cost cap hit (%.2f >= %.2f); skipping",
                            cost, self.daily_cost_cap_usd)
                score = self._rule_score(headline)
            elif client is None:
                score = self._rule_score(headline)
            else:
                try:
                    resp = await asyncio.to_thread(
                        client.messages.create,
                        model=self.model,
                        max_tokens=150,
                        system=SYSTEM_PROMPT,
                        messages=[{
                            "role": "user",
                            "content": f"Ticker: {n.get('ticker') or 'market'}\nHeadline: {headline}"
                        }],
                    )
                    text = resp.content[0].text.strip()
                    # Strip markdown fencing if model wrapped it.
                    if text.startswith("```"):
                        text = text.strip("`").split("\n", 1)[1].rsplit("\n", 1)[0]
                    score = json.loads(text)
                    # Approximate Haiku cost: $1/MTok input, $5/MTok output.
                    in_tok = resp.usage.input_tokens
                    out_tok = resp.usage.output_tokens
                    call_cost = (in_tok * 1.0 + out_tok * 5.0) / 1_000_000
                    self._add_cost(call_cost)
                except Exception as e:
                    log.warning("LLM call failed (%s) — rule-based fallback", e)
                    score = self._rule_score(headline)

            payload = {
                "ticker": n.get("ticker"),
                "provider": n.get("provider"),
                "article_id": n.get("article_id"),
                "headline": headline[:200],
                "sentiment_score": float(score.get("sentiment_score", 0)),
                "impact_magnitude": float(score.get("impact_magnitude", 0)),
                "conviction_delta_pp": float(score.get("conviction_delta_pp", 0)),
                "rationale_short": (score.get("rationale_short") or "")[:200],
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            await nc.publish("news.alpha", json.dumps(payload).encode("utf-8"))
            log.info(
                "news.alpha %s [%s] sent=%+.2f impact=%.2f delta=%+.1fpp | %s",
                payload["ticker"] or "mkt", payload["provider"],
                payload["sentiment_score"], payload["impact_magnitude"],
                payload["conviction_delta_pp"], headline[:60],
            )

        await nc.subscribe("news.raw", cb=on_news)
        log.info("listening on news.raw (model=%s, cost_cap=$%.2f)",
                 self.model, self.daily_cost_cap_usd)

        while True:
            await asyncio.sleep(60)

    # --- fallback + cost bookkeeping -------------------------------------
    _POS = {"beat", "beats", "surge", "soar", "upgrade", "upgraded", "bullish",
            "raise", "guidance above", "record", "profit", "breakthrough"}
    _NEG = {"miss", "missed", "plunge", "plummet", "downgrade", "downgraded",
            "bearish", "cut", "guidance below", "lawsuit", "probe", "investigation",
            "fraud", "bankruptcy", "warning"}

    def _rule_score(self, headline: str) -> dict:
        h = headline.lower()
        pos = sum(1 for w in self._POS if w in h)
        neg = sum(1 for w in self._NEG if w in h)
        raw = pos - neg
        sent = max(-1.0, min(1.0, raw * 0.4))
        impact = min(1.0, 0.2 + 0.2 * (pos + neg))
        delta = max(-30.0, min(15.0, sent * 12.0))
        return {
            "sentiment_score": sent,
            "impact_magnitude": impact,
            "conviction_delta_pp": delta,
            "rationale_short": f"rule-based: pos={pos} neg={neg}",
        }

    def _load_cost(self) -> float:
        try:
            d = json.loads(self.cost_path.read_text())
            if d.get("date") == datetime.now(timezone.utc).date().isoformat():
                return float(d.get("usd", 0))
        except Exception:
            pass
        return 0.0

    def _add_cost(self, usd: float) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        cur = self._load_cost()
        self.cost_path.parent.mkdir(parents=True, exist_ok=True)
        self.cost_path.write_text(json.dumps({"date": today, "usd": cur + usd}))


if __name__ == "__main__":
    asyncio.run(LlmNewsAnalyzer().run())
