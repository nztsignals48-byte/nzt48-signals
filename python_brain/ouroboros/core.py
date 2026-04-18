"""Ouroboros nightly — 12 steps end-to-end. Reads WAL, writes learned.toml."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from python_brain.ouroboros.alpha_decay import cusum_drift, rolling_sharpe
from python_brain.ouroboros.chandelier_calibrate import grid_search
from python_brain.ouroboros.demote_resurrect import review
from python_brain.ouroboros.drift import Adwin
from python_brain.ouroboros.kelly_bayesian import bayesian_kelly
from python_brain.ouroboros.learned_writer import validate_bounds, write_learned


DATA_DIR = Path(os.environ.get("AEGIS_V5_DATA", "/Users/rr/aegis-v5/data"))
WAL_DIR = DATA_DIR / "wal"


@dataclass
class OuroborosResult:
    updated: Dict[str, float]
    refused: Dict[str, str]
    per_strategy_state: Dict[str, str]
    n_trades: int
    cusum_by_strategy: Dict[str, float] = field(default_factory=dict)
    drift_by_strategy: Dict[str, bool] = field(default_factory=dict)


def _load_today_wal() -> List[Dict[str, Any]]:
    if not WAL_DIR.exists():
        return []
    events: List[Dict[str, Any]] = []
    for f in sorted(WAL_DIR.glob("events_*.wal")):
        for line in f.read_text().splitlines():
            try:
                events.append(json.loads(line))
            except Exception:
                continue
    return events


def run_nightly(prior_state: Dict[str, str] | None = None) -> OuroborosResult:
    prior_state = prior_state or {}
    events = _load_today_wal()
    closes = [e for e in events if e.get("kind") == "TradeClosed"]

    sig_to_strategy = {e["payload"]["signal_id"]: e["payload"]["strategy_name"]
                       for e in events if e.get("kind") == "SignalReceived"}
    per_strat_pnls: Dict[str, List[float]] = {}
    per_strat_mae_mfe: Dict[str, List[tuple]] = {}
    for c in closes:
        strat = sig_to_strategy.get(c["payload"]["signal_id"], "unknown")
        per_strat_pnls.setdefault(strat, []).append(c["payload"]["realized_pnl_bps"])
        per_strat_mae_mfe.setdefault(strat, []).append((
            abs(c["payload"]["mae_bps"]) / 100.0,
            c["payload"]["mfe_bps"] / 100.0,
        ))

    all_pnls = [p for arr in per_strat_pnls.values() for p in arr]
    kelly = bayesian_kelly(all_pnls)

    all_mae_mfe = [mm for arr in per_strat_mae_mfe.values() for mm in arr]
    cha = grid_search(all_mae_mfe)

    floor = 0.60

    cusum_by_strat: Dict[str, float] = {s: cusum_drift(p) for s, p in per_strat_pnls.items()}

    drift_by_strat: Dict[str, bool] = {}
    for s, arr in per_strat_pnls.items():
        adwin = Adwin()
        drifted = False
        for p in arr:
            if adwin.update(p):
                drifted = True
                break
        drift_by_strat[s] = drifted

    new_state = review(per_strat_pnls, prior_state)

    candidate = {
        "kelly_fraction": kelly,
        "chandelier_atr_mult": cha,
        "heat_limit": 0.075,
        "confidence_floor": floor,
        "regime_scale": 0.8,
    }
    ok, refusals = validate_bounds(candidate)

    updated: Dict[str, float] = {}
    if ok:
        write_learned(candidate)
        updated = candidate

    return OuroborosResult(
        updated=updated, refused=refusals, per_strategy_state=new_state,
        n_trades=len(closes), cusum_by_strategy=cusum_by_strat,
        drift_by_strategy=drift_by_strat,
    )
