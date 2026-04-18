"""Preference logger. Called on every signal AND every close.

Writes a preference pair (strategy_default vs llm_conviction vs realized_pnl)
used by A/B harness and Ouroboros. Metric `preference_logger_calls_total` MUST
increment on every signal — hourly alert if count == 0.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict

PREF_PATH = Path(os.environ.get("AEGIS_V5_DATA", "/Users/rr/aegis-v5/data")) / "preferences.jsonl"

class PreferenceLogger:
    def __init__(self, path: Path = PREF_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.calls: int = 0

    def log_signal(self, signal_id: str, strategy: str, default_conv: float, llm_conv: float, context: Dict[str, Any]) -> None:
        rec = {"ts": time.time(), "kind": "signal", "signal_id": signal_id, "strategy": strategy,
               "default_conv": default_conv, "llm_conv": llm_conv, "context": context}
        self._append(rec)

    def log_close(self, signal_id: str, realized_pnl_bps: float, exit_reason: str) -> None:
        rec = {"ts": time.time(), "kind": "close", "signal_id": signal_id,
               "realized_pnl_bps": realized_pnl_bps, "exit_reason": exit_reason}
        self._append(rec)

    def _append(self, rec: Dict[str, Any]) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        self.calls += 1
