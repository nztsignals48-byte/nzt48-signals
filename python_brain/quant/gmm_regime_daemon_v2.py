"""GMM regime daemon v2 — fixes intraday-vs-daily return semantics.

v1 bug: rolled _prev_close on every 60-second poll, so "daily returns"
collapsed to 60-second noise. GMM trained on noise, not regime.

v2 fix: uses MacroFeatureBufferV2 which:
  - Records today's open once
  - Computes intraday return relative to today's open for live classification
  - Only rolls prev_close at UTC day boundary
  - Historical matrix only accumulates one vector per calendar day

Consumed by supervisor. Same NATS output subject: regime.gmm.
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

from python_brain.quant.gmm_regime import GMMRegimeClassifier, REGIME_LABELS
from python_brain.quant.macro_feature_buffer_v2 import MacroFeatureBufferV2


log = logging.getLogger("gmm-daemon-v2")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

PUBLISH_INTERVAL_S = 60


class GMMRegimeDaemonV2:
    def __init__(self):
        self.buffer = MacroFeatureBufferV2()
        self.classifier = GMMRegimeClassifier(n_regimes=4)
        self._last_fitted_day: str | None = None

    async def run(self):
        if NATS is None:
            log.error("nats-py required")
            return
        url = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
        nc = NATS()
        await nc.connect(servers=[url])

        async def on_tick(msg):
            try:
                d = json.loads(msg.data)
                t = d.get("ticker") or d.get("symbol")
                last = d.get("last") or d.get("mid") or d.get("bid")
                if t and last:
                    self.buffer.on_tick(t, float(last))
            except Exception:
                pass

        async def on_macro(msg):
            try:
                d = json.loads(msg.data)
                if "VIXCLS" in d:
                    self.buffer.on_macro("vix", float(d["VIXCLS"]))
                if "DGS10" in d:
                    self.buffer.on_macro("dgs10", float(d["DGS10"]))
            except Exception:
                pass

        await nc.subscribe("ticks.live.*", cb=on_tick)
        await nc.subscribe("ticks.delayed.*", cb=on_tick)
        await nc.subscribe("intel.macro", cb=on_macro)
        log.info("GMM regime daemon v2 listening")

        while True:
            await asyncio.sleep(PUBLISH_INTERVAL_S)
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            today = now.strftime("%Y-%m-%d")

            # Refit GMM once per UTC day when ≥30 days of real history
            if today != self._last_fitted_day and len(self.buffer.history) >= 30:
                try:
                    X = self.buffer.as_matrix()
                    self.classifier.fit(X)
                    self._last_fitted_day = today
                    log.info("refit GMM on %d historical days", len(X))
                except Exception as e:
                    log.warning("refit fail: %s", e)

            # Live intraday classification
            feat = self.buffer.intraday_feature_vector()
            if feat is None:
                continue
            try:
                state = self.classifier.classify(feat)
                payload = {
                    "ts": time.time(),
                    "regime_id": state.regime_id,
                    "regime_label": state.regime_label,
                    "posterior_probs": state.posterior_probs,
                    "size_multiplier": self.classifier.size_multiplier(state.regime_id),
                    "feature_vector": feat,
                    "n_history_days": len(self.buffer.history),
                    "version": "v2",
                }
                await nc.publish("regime.gmm", json.dumps(payload).encode())
                log.info("regime.gmm: %s mult=%.2f (intraday features)",
                         state.regime_label,
                         self.classifier.size_multiplier(state.regime_id))
            except Exception as e:
                log.warning("classify fail: %s", e)


async def main():
    d = GMMRegimeDaemonV2()
    await d.run()


if __name__ == "__main__":
    asyncio.run(main())
