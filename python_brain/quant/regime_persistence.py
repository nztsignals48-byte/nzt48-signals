"""Regime persistence model — how long does current regime last historically?

Uses empirical duration distributions: for each regime, fit a right-censored
survival curve from historical BOCPD outputs. Returns expected remaining
duration given current run-length.

Used by: exit_engine (tighten stops if regime nearing end), sig2order
(reduce sizing when regime persistence low).
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np


HISTORY_PATH = Path("/Users/rr/aegis-v5/data/regime_durations.json")


def record_regime_transition(
    from_regime: str,
    duration_days: float,
    history_path: Path = HISTORY_PATH,
) -> None:
    """Append a completed regime duration to history."""
    history_path.parent.mkdir(parents=True, exist_ok=True)
    if history_path.exists():
        try:
            data = json.loads(history_path.read_text())
        except Exception:
            data = {}
    else:
        data = {}
    data.setdefault(from_regime, []).append(float(duration_days))
    history_path.write_text(json.dumps(data, indent=2))


def expected_remaining_duration(
    current_regime: str,
    current_run_days: float,
    history_path: Path = HISTORY_PATH,
) -> dict:
    """Fit empirical survival function; return E[remaining | survived to now]."""
    if not history_path.exists():
        return {"expected_remaining_days": None, "n_observations": 0}
    try:
        data = json.loads(history_path.read_text())
    except Exception:
        return {"expected_remaining_days": None, "n_observations": 0}

    durations = data.get(current_regime, [])
    if len(durations) < 5:
        return {"expected_remaining_days": None, "n_observations": len(durations)}

    arr = np.array(durations)
    # Conditional expectation: E[T | T > current_run_days]
    survived = arr[arr > current_run_days]
    if len(survived) == 0:
        # Current run is longer than any historical duration → close to exhaustion
        return {
            "expected_remaining_days": 0.5,
            "n_observations": len(durations),
            "percentile_of_current_run": 100,
        }
    expected = float(survived.mean() - current_run_days)
    pct = float((arr < current_run_days).mean() * 100)
    return {
        "expected_remaining_days": max(expected, 0.1),
        "n_observations": len(durations),
        "percentile_of_current_run": pct,
    }


def persistence_multiplier(current_regime: str, current_run_days: float) -> float:
    """Scaler in [0.5, 1.2] — higher when regime likely persists, lower when near end."""
    info = expected_remaining_duration(current_regime, current_run_days)
    remaining = info.get("expected_remaining_days")
    if remaining is None:
        return 1.0
    pct = info.get("percentile_of_current_run", 50)
    if pct > 90:
        return 0.5  # very near end
    if pct > 75:
        return 0.7
    if pct > 50:
        return 0.9
    if pct > 25:
        return 1.1
    return 1.2  # early in regime, likely persists


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        # Seed some synthetic history
        import random
        for _ in range(20):
            record_regime_transition("calm", random.gauss(10, 3))
        for _ in range(15):
            record_regime_transition("crisis", random.gauss(3, 1))

        print(f"Calm, 5 days in: {expected_remaining_duration('calm', 5)}")
        print(f"Calm, 15 days in: {expected_remaining_duration('calm', 15)}")
        print(f"Calm persistence mult 5d: {persistence_multiplier('calm', 5):.2f}")
        print(f"Calm persistence mult 15d: {persistence_multiplier('calm', 15):.2f}")
        print("OK")
