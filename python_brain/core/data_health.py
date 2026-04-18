"""Data-health contract. Startup-blocking at Phase 1 gate.

Every intel file has a declared spec: primary_key, max_age_hours, consumers[].
At startup: compute FED / EMPTY / STALE / MISSING per row and expose:
- log_summary()
- is_startup_ok()
- starved_strategies()
- to_report() -> dict for zero_trade_day_autodiag
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

DATA_DIR = Path(os.environ.get("AEGIS_V5_DATA", "/Users/rr/aegis-v5/data"))
INTEL_DIR = DATA_DIR / "intel"


@dataclass
class IntelSpec:
    name: str
    primary_key: str
    max_age_hours: float
    consumers: List[str] = field(default_factory=list)
    required: bool = True


SPECS: List[IntelSpec] = [
    IntelSpec("news_reactor.json",    "events",        6,    ["sentiment_long_short"],   required=True),
    IntelSpec("earnings_whisper.json","whispers",      24,   ["earnings_pattern"],       required=True),
    IntelSpec("sec_scanner.json",     "filings",       24,   ["filing_change_detect"],   required=True),
    IntelSpec("regime_council.json",  "regime_probs",  168,  ["conviction_engine", "portfolio_constructor"], required=True),
    IntelSpec("thesis_monitor.json",  "invalidations", 24,   ["exit_engine"],            required=False),
    IntelSpec("index_recon.json",     "events",        168,  ["index_recon"],            required=False),
]


@dataclass
class IntelStatus:
    name: str
    status: str             # FED | EMPTY | STALE | MISSING
    size_bytes: int
    age_hours: float
    consumers_starved: List[str] = field(default_factory=list)
    required: bool = True


class DataHealthMonitor:
    def check(self) -> Dict[str, IntelStatus]:
        out: Dict[str, IntelStatus] = {}
        now = time.time()
        for spec in SPECS:
            p = INTEL_DIR / spec.name
            if not p.exists():
                out[spec.name] = IntelStatus(spec.name, "MISSING", 0, 0.0,
                                             consumers_starved=spec.consumers, required=spec.required)
                continue
            size = p.stat().st_size
            age_h = (now - p.stat().st_mtime) / 3600.0
            if size < 10:
                out[spec.name] = IntelStatus(spec.name, "EMPTY", size, age_h,
                                             consumers_starved=spec.consumers, required=spec.required)
                continue
            if age_h > spec.max_age_hours:
                out[spec.name] = IntelStatus(spec.name, "STALE", size, age_h,
                                             consumers_starved=spec.consumers, required=spec.required)
                continue
            try:
                with open(p) as f:
                    d = json.load(f)
                key = spec.primary_key
                populated = bool(d.get(key))
                if populated:
                    out[spec.name] = IntelStatus(spec.name, "FED", size, age_h, required=spec.required)
                else:
                    out[spec.name] = IntelStatus(spec.name, "EMPTY", size, age_h,
                                                 consumers_starved=spec.consumers, required=spec.required)
            except Exception:
                out[spec.name] = IntelStatus(spec.name, "EMPTY", size, age_h,
                                             consumers_starved=spec.consumers, required=spec.required)
        return out

    def log_summary(self) -> None:
        for name, s in self.check().items():
            flag = "R" if s.required else "."
            print(f"[data_health] {s.status:7} {flag} {name} size={s.size_bytes:>8} age_h={s.age_hours:6.2f}")

    def is_startup_ok(self) -> bool:
        statuses = self.check()
        return all(s.status == "FED" for s in statuses.values() if s.required)

    def starved_strategies(self) -> List[str]:
        starved: List[str] = []
        for s in self.check().values():
            if s.status != "FED":
                starved.extend(s.consumers_starved)
        return sorted(set(starved))

    def to_report(self) -> Dict[str, dict]:
        return {name: s.__dict__ for name, s in self.check().items()}
