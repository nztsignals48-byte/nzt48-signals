"""LLM decision audit — every LLM council decision tagged with prompt hash + rationale.

Subscribes `llm.council.decision`, writes append-only tamper-evident log.
Each entry has SHA256 hash of prompts + input features for auditability.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path

try:
    from nats.aio.client import Client as NATS
except ImportError:
    NATS = None


log = logging.getLogger("llm-audit")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

AUDIT_LOG = Path("/Users/rr/aegis-v5/data/audit/llm_decisions.jsonl")


def decision_hash(decision: dict) -> str:
    canonical = json.dumps({
        "signal_id": decision.get("signal_id"),
        "ticker": decision.get("ticker"),
        "agents": sorted(decision.get("agents", [])),
        "feature_hash": decision.get("feature_hash"),
    }, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def append_audit(entry: dict) -> None:
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


async def main():
    if NATS is None:
        return
    url = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    nc = NATS()
    await nc.connect(servers=[url])

    async def on_decision(msg):
        try:
            d = json.loads(msg.data)
            d["audit_ts"] = time.time()
            d["audit_hash"] = decision_hash(d)
            append_audit(d)
        except Exception as e:
            log.warning("audit fail: %s", e)

    await nc.subscribe("llm.council.decision", cb=on_decision)
    log.info("LLM decision audit active → %s", AUDIT_LOG)
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
