#!/usr/bin/env python3
"""intelligence_runner — cycles through the Phase 7 intelligence agents
(thesis_monitor, regime_council, earnings_whisper) every 5 minutes so their
output JSONs stay fresh.

Previously these were library classes that nothing ever called — the intel
JSONs (thesis_monitor.json, regime_council.json, earnings_whisper.json)
stayed tiny / stale forever.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

log = logging.getLogger("intel-runner")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

NATS_URL = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
INTERVAL_S = 300  # 5 min

DATA_DIR = Path("/Users/rr/aegis-v5/data")
INTEL_DIR = DATA_DIR / "intel"


def _load_positions() -> list[dict]:
    """Best-effort: read latest account.positions archive or intel."""
    # Try archive (nats_archiver writes it)
    import glob
    files = sorted(glob.glob(str(DATA_DIR / "archive" / "account_positions_*.jsonl")))
    if not files:
        return []
    try:
        with open(files[-1]) as f:
            lines = f.readlines()
        if not lines:
            return []
        last = json.loads(lines[-1])
        # nats_archiver wraps as {"subject":..., "payload":{...}}
        payload = last.get("payload", last)
        return payload.get("positions", []) or []
    except Exception as e:
        log.debug("load positions: %s", e)
        return []


def _load_intel(name: str) -> dict:
    p = INTEL_DIR / name
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except Exception:
        return {}


async def run():
    import nats  # type: ignore
    nc = await nats.connect(NATS_URL, name="aegis-v5-intelligence-runner")
    log.info("intelligence runner connected to NATS")

    # Import the agents lazily
    try:
        from python_brain.intelligence.thesis_monitor import ThesisMonitor
    except Exception as e:
        log.warning("thesis_monitor not importable: %s", e)
        ThesisMonitor = None  # type: ignore

    try:
        from python_brain.intelligence.regime_council import RegimeCouncil
    except Exception as e:
        log.warning("regime_council not importable: %s", e)
        RegimeCouncil = None  # type: ignore

    try:
        from python_brain.intelligence.earnings_whisper import EarningsWhisper
    except Exception as e:
        log.warning("earnings_whisper not importable: %s", e)
        EarningsWhisper = None  # type: ignore

    thesis = ThesisMonitor() if ThesisMonitor else None
    regime = RegimeCouncil() if RegimeCouncil else None
    whisper = EarningsWhisper() if EarningsWhisper else None

    while True:
        start = time.time()
        positions = _load_positions()
        sentiment_intel = _load_intel("sentiment_long_short.json")
        macro = _load_intel("macro.json")
        fundamentals = _load_intel("fundamentals.json")

        context = {
            "sentiment": sentiment_intel,
            "macro": macro,
            "fundamentals": fundamentals,
            "ts": start,
        }

        # Thesis monitor
        if thesis is not None:
            try:
                invalidations = thesis.run(positions, context)
                INTEL_DIR.mkdir(parents=True, exist_ok=True)
                (INTEL_DIR / "thesis_monitor.json").write_text(json.dumps({
                    "ts": start,
                    "invalidations": invalidations,
                }, indent=2))
                if invalidations:
                    try:
                        await nc.publish("intel.thesis.invalidated",
                                         json.dumps({"ts": start, "invalidations": invalidations}).encode())
                    except Exception:
                        pass
                log.info("thesis_monitor: %d invalidations", len(invalidations))
            except Exception as e:
                log.warning("thesis_monitor run: %s", e)

        # Regime council — signature: run(returns_1d_spy, current_vol)
        if regime is not None:
            try:
                returns = [0.0] * 20  # placeholder; proper SPY returns come from delayed streamer later
                current_vol = 0.15
                try:
                    vix = float(macro.get("VIXCLS", 15.0) or 15.0)
                    current_vol = vix / 100.0
                except Exception:
                    pass
                regime.run(returns, current_vol)
                log.info("regime_council: vix-implied vol=%.3f", current_vol)
            except Exception as e:
                log.warning("regime_council run: %s", e)

        # Earnings whisper — signature: run(tickers, analyst_consensus)
        if whisper is not None:
            try:
                tickers = []
                wpath = DATA_DIR / "watchlist.v5.json"
                if wpath.exists():
                    try:
                        w = json.loads(wpath.read_text())
                        tickers = [x["ticker"] for x in w.get("tickers", [])[:50]
                                   if "ticker" in x]
                    except Exception:
                        pass
                analyst = {t: 0.0 for t in tickers}  # placeholder consensus
                whisper.run(tickers, analyst)
                log.info("earnings_whisper: %d tickers processed", len(tickers))
            except Exception as e:
                log.warning("earnings_whisper run: %s", e)

        elapsed = time.time() - start
        log.info("cycle done in %.1fs; sleeping %ds", elapsed, INTERVAL_S)
        await asyncio.sleep(max(5, INTERVAL_S - int(elapsed)))


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
