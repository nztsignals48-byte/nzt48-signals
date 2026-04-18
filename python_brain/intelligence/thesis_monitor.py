"""thesis_monitor — Phase 7 invalidation detector.

Checks each open position vs its strategy's invalidation rules:
- sentiment_long_short: sentiment score flipped sign vs entry
- earnings_pattern: earnings date moved
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

from python_brain.core.ab_harness import AgentABHarness

DATA_DIR = Path(os.environ.get("AEGIS_V5_DATA", "/Users/rr/aegis-v5/data"))
OUT_PATH = DATA_DIR / "intel" / "thesis_monitor.json"


class ThesisMonitor:
    def __init__(self) -> None:
        self.ab = AgentABHarness(agent_name="thesis_monitor")

    def run(self, open_positions: List[Dict[str, Any]], current_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        invalidations: List[Dict[str, Any]] = []
        for p in open_positions:
            strat = p.get("strategy")
            if strat == "sentiment_long_short":
                entry = p.get("features", {}).get("sentiment_score", 0)
                now = current_context.get("sentiment", {}).get(p["ticker"], entry)
                if entry * now < 0 and abs(now) > 0.2:
                    invalidations.append({"signal_id": p["signal_id"], "reason": "sentiment_flip"})
            elif strat == "earnings_pattern":
                if current_context.get("earnings_moved", {}).get(p["ticker"]):
                    invalidations.append({"signal_id": p["signal_id"], "reason": "earnings_moved"})
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps({"invalidations": invalidations, "generated_at": time.time()}))
        return invalidations
