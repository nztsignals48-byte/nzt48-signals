"""LLM uplift tracker — measures whether LLM agents add alpha net of cost.

Listens to orders.filled; for each fill, compares actual PnL with what would
have happened without LLM council input. Reports daily to llm.uplift.report.

Consumed by Ouroboros nightly to decide whether to demote LLM agents.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from pathlib import Path

try:
    from nats.aio.client import Client as NATS
except ImportError:
    NATS = None


log = logging.getLogger("llm-uplift")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROOT = Path("/Users/rr/aegis-v5")
REPORT_PATH = ROOT / "data/llm_uplift_daily.jsonl"


class UpliftState:
    def __init__(self):
        # Signal decisions: sig_id -> (with_llm_accept, without_llm_accept, conviction)
        self.decisions: dict[str, dict] = {}
        # Realized fills: sig_id -> pnl_bps
        self.fills: dict[str, float] = {}
        # LLM cost per signal (Anthropic usage)
        self.cost_per_signal: dict[str, float] = {}


class LLMUpliftTracker:
    def __init__(self):
        self.state = UpliftState()
        self.nc = None

    async def on_llm_decision(self, msg):
        try:
            d = json.loads(msg.data)
            sig = d.get("signal_id")
            if not sig:
                return
            self.state.decisions[sig] = {
                "ts": d.get("ts", time.time()),
                "llm_accept": d.get("council_accept"),
                "consensus_score": d.get("consensus_score", 0.5),
                "n_accept": d.get("num_accept", 0),
                "n_total": d.get("num_total", 6),
            }
            cost = d.get("cost_usd", 0)
            if cost:
                self.state.cost_per_signal[sig] = float(cost)
        except Exception:
            pass

    async def on_fill(self, msg):
        try:
            d = json.loads(msg.data)
            sig = d.get("signal_id")
            pnl = d.get("realized_pnl_bps")
            if sig and pnl is not None:
                self.state.fills[sig] = float(pnl)
        except Exception:
            pass

    def compute_uplift(self) -> dict:
        """Match decisions with fills, compute uplift."""
        with_llm_pnl = []
        without_llm_pnl = []
        total_cost_usd = 0.0

        for sig, dec in self.state.decisions.items():
            if sig not in self.state.fills:
                continue
            pnl = self.state.fills[sig]
            if dec.get("llm_accept"):
                with_llm_pnl.append(pnl)
            else:
                without_llm_pnl.append(pnl)
            total_cost_usd += self.state.cost_per_signal.get(sig, 0.0)

        def mean(xs):
            return sum(xs) / len(xs) if xs else 0.0

        uplift = mean(with_llm_pnl) - mean(without_llm_pnl)
        # Break-even: uplift in bps must cover cost per trade
        cost_per_trade_bps = 0.0
        if len(with_llm_pnl) > 0:
            # $cost over N trades assuming $1000 avg notional = cost_bps
            avg_notional = 1000
            cost_per_trade_bps = (total_cost_usd / len(with_llm_pnl)) / avg_notional * 10000 if with_llm_pnl else 0

        return {
            "ts": time.time(),
            "with_llm_n": len(with_llm_pnl),
            "without_llm_n": len(without_llm_pnl),
            "with_llm_mean_bps": mean(with_llm_pnl),
            "without_llm_mean_bps": mean(without_llm_pnl),
            "uplift_bps": uplift,
            "total_cost_usd": total_cost_usd,
            "cost_per_trade_bps": cost_per_trade_bps,
            "net_uplift_bps": uplift - cost_per_trade_bps,
            "verdict": "ALPHA_POSITIVE" if (uplift - cost_per_trade_bps) > 0 else "ALPHA_NEGATIVE",
        }

    async def report_loop(self):
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        while True:
            await asyncio.sleep(300)  # every 5 min
            report = self.compute_uplift()
            log.info("uplift: with=%d trades %.2fbps, without=%d trades %.2fbps, net=%.2fbps",
                     report["with_llm_n"], report["with_llm_mean_bps"],
                     report["without_llm_n"], report["without_llm_mean_bps"],
                     report["net_uplift_bps"])
            try:
                await self.nc.publish("llm.uplift.report", json.dumps(report).encode())
                with open(REPORT_PATH, "a") as f:
                    f.write(json.dumps(report) + "\n")
            except Exception as e:
                log.warning("report publish fail: %s", e)


async def main():
    if NATS is None:
        log.error("nats-py required")
        return
    url = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    tracker = LLMUpliftTracker()
    tracker.nc = NATS()
    await tracker.nc.connect(servers=[url])
    await tracker.nc.subscribe("llm.council.decision", cb=tracker.on_llm_decision)
    await tracker.nc.subscribe("orders.filled", cb=tracker.on_fill)
    log.info("subscribed")
    await tracker.report_loop()


if __name__ == "__main__":
    asyncio.run(main())
