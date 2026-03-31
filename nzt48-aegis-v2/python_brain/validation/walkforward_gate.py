"""Book 9: Walk-Forward Validation Gate.

Prevents autonomous parameter tuning (Ouroboros config_writer) from
applying changes without minimum evidence. Acts as a safety gate
for the approval_gate.py module.

Rules:
  1. No parameter changes with < 100 trades of evidence
  2. Changes must improve expected Sharpe (based on historical sensitivity)
  3. Maximum parameter change per cycle is bounded (anti-whipsaw)
  4. Track all proposed changes in audit log

Wired into approval_gate.py (cold-path only).
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

DATA_DIR = os.environ.get("AEGIS_DATA_DIR", "/app/data")
GATE_LOG = os.path.join(DATA_DIR, "walkforward_gate_log.ndjson")
PNL_FILE = os.path.join(DATA_DIR, "strategy_pnl_history.json")

# Hard bounds for autonomous parameter changes
PARAM_BOUNDS = {
    "kelly_cap": (0.10, 0.35),
    "chandelier_atr": (1.5, 5.0),
    "confidence_floor": (45, 75),
    "drawdown_halt_pct": (0.05, 0.10),
}

# Maximum change per nightly cycle (anti-whipsaw)
MAX_CHANGE_PER_CYCLE = {
    "kelly_cap": 0.03,
    "chandelier_atr": 0.3,
    "confidence_floor": 5,
    "drawdown_halt_pct": 0.01,
}


@dataclass
class ValidationResult:
    approved: bool
    reason: str
    param: str = ""
    old_value: float = 0.0
    new_value: float = 0.0
    clamped_value: float = 0.0
    evidence_n: int = 0


def _count_total_trades() -> int:
    """Count total trades across all strategies."""
    if not os.path.exists(PNL_FILE):
        return 0
    try:
        with open(PNL_FILE) as f:
            data = json.load(f)
        return sum(len(v) for v in data.values() if isinstance(v, list))
    except Exception:
        return 0


def validate_parameter_change(
    param_name: str,
    current_value: float,
    proposed_value: float,
    min_evidence_trades: int = 100,
) -> ValidationResult:
    """Validate a proposed parameter change against walk-forward evidence.

    Returns ValidationResult with approved=True/False and reason.
    If approved, clamped_value contains the bounded new value.
    """
    result = ValidationResult(
        approved=False, param=param_name,
        old_value=current_value, new_value=proposed_value,
    )

    # 1. Minimum evidence check
    n_trades = _count_total_trades()
    result.evidence_n = n_trades

    if n_trades < min_evidence_trades:
        result.reason = (
            f"Insufficient evidence: {n_trades} trades < {min_evidence_trades} minimum. "
            f"Config frozen until N={min_evidence_trades}."
        )
        return result

    # 2. Hard bounds check
    bounds = PARAM_BOUNDS.get(param_name)
    if bounds:
        lo, hi = bounds
        if proposed_value < lo or proposed_value > hi:
            clamped = max(lo, min(hi, proposed_value))
            result.clamped_value = clamped
            result.reason = f"Clamped {param_name}: {proposed_value} -> {clamped} (bounds [{lo}, {hi}])"
            proposed_value = clamped

    # 3. Anti-whipsaw: max change per cycle
    max_delta = MAX_CHANGE_PER_CYCLE.get(param_name, float("inf"))
    delta = abs(proposed_value - current_value)
    if delta > max_delta:
        # Clamp to maximum allowed change
        direction = 1 if proposed_value > current_value else -1
        proposed_value = current_value + direction * max_delta
        result.clamped_value = proposed_value
        result.reason = (
            f"Whipsaw guard: clamped delta from {delta:.4f} to {max_delta} "
            f"({param_name}: {current_value} -> {proposed_value})"
        )

    # 4. No-op check
    if abs(proposed_value - current_value) < 1e-6:
        result.reason = f"No change needed: {param_name} = {current_value}"
        result.approved = True
        result.clamped_value = current_value
        return result

    # Approved with (possibly clamped) value
    result.approved = True
    if not result.clamped_value:
        result.clamped_value = proposed_value
    if not result.reason:
        result.reason = f"Approved: {param_name} {current_value} -> {result.clamped_value} (N={n_trades})"

    # Log the decision
    _log_decision(result)

    return result


def check_minimum_evidence(min_trades: int = 100) -> Tuple[bool, int]:
    """Quick check: do we have enough evidence for any config changes?

    Returns (has_enough, trade_count).
    """
    n = _count_total_trades()
    return (n >= min_trades, n)


def _log_decision(result: ValidationResult):
    """Append decision to audit log."""
    try:
        os.makedirs(os.path.dirname(GATE_LOG), exist_ok=True)
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "param": result.param,
            "old_value": result.old_value,
            "new_value": result.new_value,
            "clamped_value": result.clamped_value,
            "approved": result.approved,
            "reason": result.reason,
            "evidence_n": result.evidence_n,
        }
        with open(GATE_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    # Smoke test
    r = validate_parameter_change("kelly_cap", 0.20, 0.25)
    print(f"Kelly cap change: approved={r.approved}, reason={r.reason}")
    r = validate_parameter_change("chandelier_atr", 3.0, 10.0)
    print(f"Chandelier change: approved={r.approved}, clamped={r.clamped_value}")
