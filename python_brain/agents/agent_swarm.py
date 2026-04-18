"""LLM agent swarm — 4 agents × Haiku 4.5, each with narrow responsibility.

Agents:
    earnings_whisper   subscribes news.raw  (earnings-related) -> agents.earnings
    smart_money        subscribes news.raw + scanner.hits.high_opt_iv_usd -> agents.smart_money
    filing_reactor     subscribes news.raw  (8-K/10-Q/material) -> agents.filings
    regime_narrator    subscribes portfolio.equity (slow)       -> agents.regime

Each agent emits a conviction delta that sig2order consumes. Cost is
aggregated into v5_llm_cost_usd. Cap shared across all agents via the
cost ledger at /Users/rr/aegis-v5/data/llm_cost_today.json.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
COST_PATH = Path("/Users/rr/aegis-v5/data/llm_cost_today.json")
DAILY_CAP_USD = 15.0


SYSTEM_PROMPTS = {
    "earnings_whisper": (
        "You analyse earnings-related headlines. Output ONLY JSON with keys: "
        "is_earnings (bool), surprise_direction (-1|0|+1), conviction_delta_pp (-20..+15), "
        "rationale_short (<=15 words)."
    ),
    "smart_money": (
        "You analyse congressional trades, insider buys, whale options flow, 13F "
        "announcements. Output ONLY JSON: smart_money_signal (-1..+1), "
        "conviction_delta_pp (-10..+12), tickers_mentioned (list), rationale_short."
    ),
    "filing_reactor": (
        "You analyse SEC filings (8-K, 10-Q, 10-K, DEF 14A). Output ONLY JSON: "
        "filing_type (str), materiality (0..1), sentiment (-1..+1), "
        "conviction_delta_pp (-20..+12), rationale_short."
    ),
    "regime_narrator": (
        "You classify the current market regime from portfolio P&L + broad indicators. "
        "Output ONLY JSON: regime (steady|trending|crisis|rotation), confidence (0..1), "
        "kelly_multiplier_suggestion (0.3..1.0), rationale_short."
    ),
}

# Which subjects each agent subscribes to.
SUBSCRIPTIONS = {
    "earnings_whisper":  ["news.raw"],
    "smart_money":       ["news.raw", "scanner.hits.high_opt_iv_usd"],
    "filing_reactor":    ["news.raw"],
    "regime_narrator":   ["portfolio.equity"],
}


def _load_cost() -> float:
    try:
        d = json.loads(COST_PATH.read_text())
        if d.get("date") == datetime.now(timezone.utc).date().isoformat():
            return float(d.get("usd", 0))
    except Exception:
        pass
    return 0.0


def _add_cost(usd: float) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    cur = _load_cost()
    COST_PATH.parent.mkdir(parents=True, exist_ok=True)
    COST_PATH.write_text(json.dumps({"date": today, "usd": cur + usd}))


async def run_agent(agent_name: str, client, nc) -> None:
    sys_prompt = SYSTEM_PROMPTS[agent_name]
    out_subject = f"agents.{agent_name.split('_')[0]}"

    async def handler(msg):
        try:
            p = json.loads(msg.data)
        except Exception:
            return
        if _load_cost() >= DAILY_CAP_USD:
            return
        content = p.get("headline") or p.get("summary") or json.dumps(p)[:300]
        if not content.strip():
            return
        try:
            resp = await asyncio.to_thread(
                client.messages.create,
                model=ANTHROPIC_MODEL,
                max_tokens=140,
                system=sys_prompt,
                messages=[{"role": "user", "content": content[:500]}],
            )
            text = resp.content[0].text.strip()
            if text.startswith("```"):
                text = text.strip("`").split("\n", 1)[1].rsplit("\n", 1)[0]
            data = json.loads(text)
        except Exception as e:
            log.warning("%s LLM failed: %s", agent_name, e)
            return
        # Cost book-keep (Haiku: $1/MTok in, $5/MTok out).
        try:
            in_tok = resp.usage.input_tokens
            out_tok = resp.usage.output_tokens
            _add_cost((in_tok * 1 + out_tok * 5) / 1_000_000)
        except Exception:
            pass

        out = {
            "agent": agent_name,
            "ticker": p.get("ticker"),
            "delta_pp": float(data.get("conviction_delta_pp", 0)),
            "data": data,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        await nc.publish(out_subject, json.dumps(out).encode("utf-8"))
        log.info("%s → delta=%+.1fpp  %s",
                 agent_name, out["delta_pp"], str(data)[:90])

    for s in SUBSCRIPTIONS[agent_name]:
        await nc.subscribe(s, cb=handler)
    log.info("agent %s listening on %s", agent_name, SUBSCRIPTIONS[agent_name])


async def main() -> None:
    import nats  # type: ignore
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY missing — agents can't start")
        return
    try:
        import anthropic  # type: ignore
    except ImportError:
        log.error("anthropic SDK missing")
        return
    client = anthropic.Anthropic(api_key=api_key)

    nc = await nats.connect("nats://127.0.0.1:4222", name="aegis-v5-agent-swarm")
    log.info("agent swarm connected to NATS; daily cap $%.2f", DAILY_CAP_USD)

    for agent in SUBSCRIPTIONS:
        await run_agent(agent, client, nc)

    while True:
        await asyncio.sleep(60)
        log.info("agent swarm alive (cost today=$%.3f)", _load_cost())


if __name__ == "__main__":
    asyncio.run(main())
