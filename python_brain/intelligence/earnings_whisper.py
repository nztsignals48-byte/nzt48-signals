"""earnings_whisper — Phase 7 rule-based. Swap for Finnhub /recommendation + WSH in prod.

Pre-registered metric: earnings_pattern PF. Min effect: +0.10 absolute.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, List

from python_brain.core.ab_harness import AgentABHarness

DATA_DIR = Path(os.environ.get("AEGIS_V5_DATA", "/Users/rr/aegis-v5/data"))
OUT_PATH = DATA_DIR / "intel" / "earnings_whisper.json"


class EarningsWhisper:
    def __init__(self) -> None:
        self.ab = AgentABHarness(agent_name="earnings_whisper")

    def run(self, tickers: List[str], analyst_consensus: Dict[str, float]) -> None:
        whispers: Dict[str, Dict[str, float]] = {}
        for t in tickers:
            cons = analyst_consensus.get(t, 0.0)
            whispers[t] = {"expected_surprise_bps": cons * 100, "analyst_count": 20}
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps({"whispers": whispers, "generated_at": time.time()}))
