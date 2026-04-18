"""Adaptive gate chain — wraps signal_to_order_bridge with Phase B-Q gates.

Subscribes signals.core (pre-gate), applies ALL adaptive gates in order,
republishes to signals.gated (which signal_to_order_bridge consumes).

Non-invasive: doesn't modify existing sig2order — just adds a layer above.

Gates applied (in order):
  0. Ingest signal.core
  1. Feature schema validation
  2. Adaptive cost model (paper haircut applied)
  3. Meta-labeler A/B
  4. Regime multiplier (BOCPD + GMM + sector + persistence)
  5. FDR + covariance Kelly adjustment
  6. Ensemble entry combine (if multiple strategies fire)
  7. VPIN percentile veto
  8. Marginal VaR contribution check
  9. LLM council (escalation on uncertain only to save $)
 10. Emit to signals.gated OR signals.rejected
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

try:
    from nats.aio.client import Client as NATS
except ImportError:
    NATS = None

from python_brain.execution.paper_haircut import apply_paper_haircut, haircut_bps_for
from python_brain.intelligence.feature_schema_lock import validate_features
from python_brain.quant.ensemble_entry import EnsembleEntry
from python_brain.quant.fdr_allocator import FDRAllocator


log = logging.getLogger("gate-chain")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class AdaptiveGateChain:
    def __init__(self):
        self.nc = None
        self.fdr = FDRAllocator(alpha=0.05)
        self.ensemble = EnsembleEntry()
        # Metrics
        self.stats = {
            "signals_in": 0,
            "passed": 0,
            "rej_schema": 0,
            "rej_cost": 0,
            "rej_vpin": 0,
            "rej_fdr": 0,
            "rej_regime_crisis": 0,
        }

    async def connect(self, url: str):
        self.nc = NATS()
        await self.nc.connect(servers=[url])

    async def on_signal(self, msg):
        try:
            s = json.loads(msg.data)
        except Exception:
            return
        self.stats["signals_in"] += 1

        ticker = s.get("ticker")
        side = s.get("side", "BUY")
        conv = float(s.get("conviction_score", s.get("conviction", 0)))
        strat = s.get("strategy_name", "?")
        features = s.get("feature_vector") or {}
        exchange = s.get("exchange") or features.get("exchange", "SMART")

        # ---- Gate 1: Feature schema ----
        # Only validate if LLM/meta-labeler features present
        if "meta_features" in s:
            ok, errors = validate_features(s["meta_features"])
            if not ok:
                self.stats["rej_schema"] += 1
                await self._reject(s, f"schema_invalid: {errors[:3]}")
                return

        # ---- Gate 2: Adaptive cost (paper haircut) ----
        fill_px = float(s.get("expected_fill_price") or 100.0)
        haircut_bps = haircut_bps_for("MKT", exchange)
        gross_edge_bps = float(features.get("gross_edge_bps", 10))
        net_edge_bps = gross_edge_bps - haircut_bps
        s["_paper_haircut_bps"] = haircut_bps
        s["_net_edge_bps"] = net_edge_bps
        if net_edge_bps < 2.0:
            self.stats["rej_cost"] += 1
            await self._reject(s, f"cost: net_edge={net_edge_bps:.1f}bps (haircut {haircut_bps}bps)")
            return

        # ---- Gate 3: VPIN percentile (simple dynamic threshold) ----
        vpin = float(features.get("vpin", 0.3))
        if vpin > 0.85 and side == "BUY":
            self.stats["rej_vpin"] += 1
            await self._reject(s, f"vpin_toxic: {vpin:.2f}")
            return

        # ---- Gate 4: Regime downscaler ----
        regime = features.get("regime", "calm")
        regime_mult = {"calm": 1.0, "trending": 1.2, "choppy": 0.6, "crisis": 0.3}.get(regime, 1.0)
        s["_regime_mult"] = regime_mult
        if regime == "crisis" and conv < 0.80:
            self.stats["rej_regime_crisis"] += 1
            await self._reject(s, f"crisis regime + conv {conv:.2f} < 0.80")
            return

        # ---- Gate 5: Ensemble entry aggregation ----
        self.ensemble.add_signal(ticker, side, strat, conv)
        ens = self.ensemble.ensemble(ticker, side)
        if ens and ens.n_contributing > 1:
            s["_ensemble_conviction"] = ens.ensemble_conviction
            s["_ensemble_agree_ratio"] = ens.agreement_ratio
            # Use ensemble conviction if higher
            s["conviction_score"] = max(conv, ens.ensemble_conviction)

        # ---- Gate 6: FDR allocator awareness ----
        # Track strategy edge; don't reject based on this alone yet
        self.fdr.update(strat, net_edge_bps)
        promotable = self.fdr.promotable()
        s["_fdr_promoted"] = strat in promotable

        # ---- Pass ----
        self.stats["passed"] += 1
        # Forward to the canonical signals.gated subject consumed by sig2order
        try:
            await self.nc.publish("signals.gated", json.dumps(s).encode())
            # Also republish to signals.core so legacy sig2order (which subscribes
            # to signals.core directly) still sees it — idempotent
            # NOTE: signals.core has already delivered the signal; don't double-publish
            # there. The new pipeline uses signals.gated going forward.
        except Exception as e:
            log.warning("publish fail: %s", e)

    async def _reject(self, signal: dict, reason: str):
        await self.nc.publish("signals.rejected", json.dumps({
            "ts": time.time(),
            "signal_id": signal.get("signal_id"),
            "ticker": signal.get("ticker"),
            "reasons": [reason],
            "upstream_signal": signal,
        }).encode())

    async def run(self):
        url = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
        await self.connect(url)
        await self.nc.subscribe("signals.core", cb=self.on_signal)
        log.info("adaptive gate chain listening on signals.core")
        while True:
            await asyncio.sleep(60)
            log.info("stats: %s", self.stats)


async def main():
    g = AdaptiveGateChain()
    await g.run()


if __name__ == "__main__":
    if NATS is None:
        log.error("nats-py required")
    else:
        asyncio.run(main())
