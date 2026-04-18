"""Adversarial self-test — synthesizes pathological signals to probe V5 gates.

Generates 100 known-bad signals designed to exploit each gate's weakness:
- Toxic VPIN signals that should be vetoed
- Over-concentrated tech BUYs that should trigger correlation guard
- Low-edge signals that should fail cost model
- Stale regime signals that should be size-multiplied down
- LLM-sensitive ambiguous signals

Sends via NATS signals.core; measures what fraction get correctly rejected.
Weekly report generates 'gate effectiveness' score.

Beyond institutional: most risk teams do this MANUALLY in quarterly reviews.
V5 automates it weekly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

try:
    from nats.aio.client import Client as NATS
except ImportError:
    NATS = None


log = logging.getLogger("adversarial")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

REPORT_PATH = Path("/Users/rr/aegis-v5/data/adversarial_reports")


@dataclass
class PathologicalSignal:
    signal_id: str
    ticker: str
    side: str
    conviction: float
    strategy: str
    features: dict
    expected_rejection_reason: str   # which gate SHOULD catch this


def synthesize_signals(n_per_category: int = 20) -> list[PathologicalSignal]:
    signals = []

    # Category 1: Toxic VPIN — should be vetoed by VPIN gate
    for _ in range(n_per_category):
        signals.append(PathologicalSignal(
            signal_id=f"adv_toxic_{uuid.uuid4().hex[:6]}",
            ticker=random.choice(["AAPL", "NVDA", "MSFT"]),
            side="BUY",
            conviction=0.75,
            strategy="ADVERSARIAL_toxic_vpin",
            features={
                "vpin": 0.95,  # extreme toxicity
                "gross_edge_bps": 30,
                "rvol": 1.5,
                "regime": "calm",
                "spread_bps": 2,
            },
            expected_rejection_reason="vpin_veto",
        ))

    # Category 2: Over-concentrated tech — should hit correlation guard
    tech_tickers = ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMD", "INTC", "CRM"]
    for _ in range(n_per_category):
        signals.append(PathologicalSignal(
            signal_id=f"adv_concentrated_{uuid.uuid4().hex[:6]}",
            ticker=random.choice(tech_tickers),
            side="BUY",
            conviction=0.80,
            strategy="ADVERSARIAL_concentration",
            features={
                "vpin": 0.3,
                "gross_edge_bps": 25,
                "rvol": 1.2,
                "regime": "calm",
                "spread_bps": 2,
            },
            expected_rejection_reason="correlation_guard",
        ))

    # Category 3: Tiny edge — should fail cost model
    for _ in range(n_per_category):
        signals.append(PathologicalSignal(
            signal_id=f"adv_lowedge_{uuid.uuid4().hex[:6]}",
            ticker="SPY",
            side="BUY",
            conviction=0.65,
            strategy="ADVERSARIAL_low_edge",
            features={
                "vpin": 0.3,
                "gross_edge_bps": 1.0,   # below min_edge_bps=2.0
                "rvol": 1.0,
                "regime": "calm",
                "spread_bps": 5,
            },
            expected_rejection_reason="cost_model",
        ))

    # Category 4: Crisis regime — should be size-multiplied down
    for _ in range(n_per_category):
        signals.append(PathologicalSignal(
            signal_id=f"adv_crisis_{uuid.uuid4().hex[:6]}",
            ticker=random.choice(["TSLA", "COIN", "NVDA"]),
            side="BUY",
            conviction=0.85,
            strategy="ADVERSARIAL_crisis_regime",
            features={
                "vpin": 0.5,
                "gross_edge_bps": 40,
                "rvol": 3.0,
                "regime": "crisis",  # should reduce size dramatically
                "spread_bps": 10,
            },
            expected_rejection_reason="regime_downsize",
        ))

    # Category 5: Ambiguous — should trigger LLM escalation
    for _ in range(n_per_category):
        signals.append(PathologicalSignal(
            signal_id=f"adv_ambiguous_{uuid.uuid4().hex[:6]}",
            ticker=random.choice(["AAPL", "MSFT", "NVDA"]),
            side=random.choice(["BUY", "SELL"]),
            conviction=random.uniform(0.45, 0.55),
            strategy="ADVERSARIAL_ambiguous",
            features={
                "vpin": 0.4,
                "gross_edge_bps": 5,
                "rvol": 1.1,
                "regime": "choppy",
                "spread_bps": 3,
                "news_sentiment": 0.05,  # ambiguous
            },
            expected_rejection_reason="llm_escalation",
        ))

    return signals


async def run_adversarial_test(n_per_category: int = 20) -> dict:
    if NATS is None:
        log.error("nats-py required")
        return {}

    url = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    nc = NATS()
    await nc.connect(servers=[url])

    signals = synthesize_signals(n_per_category)
    log.info("synthesized %d adversarial signals", len(signals))

    # Track rejections
    rejected: dict[str, dict] = {}

    async def on_rejection(msg):
        try:
            d = json.loads(msg.data)
            sid = d.get("signal_id")
            if sid and sid.startswith("adv_"):
                rejected[sid] = d
        except Exception:
            pass

    async def on_order(msg):
        """If an order comes through, signal was NOT rejected."""
        try:
            d = json.loads(msg.data)
            sid = d.get("signal_id")
            if sid and sid.startswith("adv_"):
                rejected[sid] = {"not_rejected": True}
        except Exception:
            pass

    await nc.subscribe("signals.rejected", cb=on_rejection)
    await nc.subscribe("orders.submit", cb=on_order)

    # Publish all adversarial signals
    for s in signals:
        payload = {
            "signal_id": s.signal_id,
            "ticker": s.ticker,
            "side": s.side,
            "conviction_score": s.conviction,
            "strategy_name": s.strategy,
            "feature_vector": s.features,
            "expected_fill_price": 100.0,
            "adversarial": True,
        }
        await nc.publish("signals.core", json.dumps(payload).encode())

    # Wait for gates to process
    await asyncio.sleep(10)

    # Score
    by_category: dict[str, dict] = {}
    for s in signals:
        cat = s.expected_rejection_reason
        by_category.setdefault(cat, {"total": 0, "caught": 0, "missed": 0})
        by_category[cat]["total"] += 1
        if s.signal_id in rejected:
            info = rejected[s.signal_id]
            if info.get("not_rejected"):
                by_category[cat]["missed"] += 1
            else:
                by_category[cat]["caught"] += 1
        else:
            # No feedback received in window — count as missed for safety
            by_category[cat]["missed"] += 1

    total = len(signals)
    caught = sum(c["caught"] for c in by_category.values())
    report = {
        "ts": time.time(),
        "total_signals": total,
        "caught": caught,
        "missed": total - caught,
        "catch_rate": caught / total if total else 0.0,
        "by_category": by_category,
    }

    REPORT_PATH.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    path = REPORT_PATH / f"adversarial_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
    path.write_text(json.dumps(report, indent=2))

    log.info("catch rate: %.1f%% (%d/%d)", report["catch_rate"] * 100, caught, total)
    for cat, stats in by_category.items():
        log.info("  %s: %d/%d caught", cat, stats["caught"], stats["total"])

    return report


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        # Test just the synthesizer without NATS
        signals = synthesize_signals(n_per_category=5)
        print(f"Generated {len(signals)} signals across categories:")
        by_cat = {}
        for s in signals:
            by_cat.setdefault(s.expected_rejection_reason, 0)
            by_cat[s.expected_rejection_reason] += 1
        for cat, n in by_cat.items():
            print(f"  {cat}: {n}")
        print("OK")
    else:
        asyncio.run(run_adversarial_test())
