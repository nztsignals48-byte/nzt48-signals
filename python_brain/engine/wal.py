"""Write-Ahead Log. Hash-chained, daily-rotated JSONL, schema-versioned.

Every SignalReceived and TradeClosed is validated against the dataset contract
(the REQUIRED_* sets in tests/dataset_contract_test.py) before write.
Invalid events raise DatasetContractViolation — never silently dropped.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

DATA_DIR = Path(os.environ.get("AEGIS_V5_DATA", "/Users/rr/aegis-v5/data"))
WAL_DIR = DATA_DIR / "wal"

REQUIRED_SIGNAL_FIELDS = {
    "schema_version", "signal_id", "strategy_name", "strategy_version",
    "ticker", "exchange", "account", "timestamp_ns",
    "feature_vector", "conviction_score", "portfolio_rank",
    "account_route_chosen", "expected_fill_price",
    "risk_deltas", "risk_final_confidence",
}
REQUIRED_CLOSE_FIELDS = {
    "schema_version", "signal_id", "entry_timestamp_ns", "exit_timestamp_ns",
    "entry_price", "exit_price", "size_shares",
    "spread_cost_bps", "commission_abs", "stamp_duty_abs", "financing_cost_abs",
    "slippage_bps_vs_arrival",
    "realized_pnl_abs", "realized_pnl_bps", "mae_bps", "mfe_bps",
    "regime_at_entry", "regime_at_exit", "exit_reason",
}


class DatasetContractViolation(ValueError):
    pass


class WAL:
    def __init__(self, dir_: Path = WAL_DIR) -> None:
        self.dir = dir_
        self.dir.mkdir(parents=True, exist_ok=True)
        self.prev_hash = "0" * 64
        self._lock = threading.Lock()

    def _path(self) -> Path:
        d = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.dir / f"events_{d}.wal"

    def _validate(self, kind: str, payload: Dict[str, Any]) -> None:
        if kind == "SignalReceived":
            missing = REQUIRED_SIGNAL_FIELDS - set(payload.keys())
            if missing:
                raise DatasetContractViolation(f"SignalReceived missing fields: {missing}")
        elif kind == "TradeClosed":
            missing = REQUIRED_CLOSE_FIELDS - set(payload.keys())
            if missing:
                raise DatasetContractViolation(f"TradeClosed missing fields: {missing}")

    def append(self, kind: str, payload: Dict[str, Any]) -> Dict[str, str]:
        self._validate(kind, payload)
        with self._lock:
            line_body = json.dumps({"kind": kind, "payload": payload, "prev": self.prev_hash}, sort_keys=True)
            h = hashlib.sha256((self.prev_hash + line_body).encode()).hexdigest()
            line = json.dumps({"schema_version": 1, "kind": kind,
                               "prev": self.prev_hash, "hash": h,
                               "payload": payload}) + "\n"
            with self._path().open("a") as f:
                f.write(line)
            self.prev_hash = h
            return {"hash": h, "prev": line_body}

    def read_today(self) -> List[Dict[str, Any]]:
        p = self._path()
        if not p.exists():
            return []
        out: List[Dict[str, Any]] = []
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out
