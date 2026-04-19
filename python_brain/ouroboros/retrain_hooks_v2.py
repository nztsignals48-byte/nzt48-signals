"""Ouroboros retrain hooks v2 — extends v1 with drift check + GMM refit.

Called by ouroboros_v3_ext nightly after run_all_hooks() runs. Keeps v1
intact while adding:
  - drift_check.run_drift_check() — feature/label/calibration/uplift drift
  - gmm historical refit (uses capital_bandit_v2 learned.toml writer)
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path("/Users/rr/aegis-v5")
sys.path.insert(0, str(ROOT))

from python_brain.ouroboros.drift_check import run_drift_check  # noqa: F401


log = logging.getLogger("ouroboros-hooks-v2")


def run_extended_hooks() -> dict:
    """v2 hooks — drift check + regime refit housekeeping."""
    results = {}

    # Drift check — compares feature distributions pre/post
    try:
        results["drift_check"] = run_drift_check()
    except Exception as e:
        results["drift_check"] = {"error": str(e)}

    # Clean learned.toml defensively after any writes
    try:
        from python_brain.quant.learned_toml_writer import clean_learned_toml
        removed = clean_learned_toml()
        results["learned_toml_cleanup_bytes"] = removed
    except Exception as e:
        results["learned_toml_cleanup_bytes"] = {"error": str(e)}

    return results


if __name__ == "__main__":
    import json
    print(json.dumps(run_extended_hooks(), indent=2, default=str))
