"""regime_council — Phase 7 returns-based classifier. Swap for 3x Sonnet debate in prod.

Pre-registered metric: Kelly-adjusted return at equal risk. Min effect: +3% annualised.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import List

from python_brain.core.ab_harness import AgentABHarness
from python_brain.engine.quant_core import regime_probs

DATA_DIR = Path(os.environ.get("AEGIS_V5_DATA", "/Users/rr/aegis-v5/data"))
OUT_PATH = DATA_DIR / "intel" / "regime_council.json"


class RegimeCouncil:
    def __init__(self) -> None:
        self.ab = AgentABHarness(agent_name="regime_council")

    def run(self, returns_1d_spy: List[float], current_vol: float) -> None:
        probs = regime_probs(returns_1d_spy, vol=current_vol)
        rationale = (
            "crisis"   if probs[2] > 0.35 else
            "trending" if probs[1] > 0.4  else
            "rotation" if probs[3] > 0.35 else
            "steady"
        )
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps({"regime_probs": probs, "rationale": rationale, "generated_at": time.time()}))
