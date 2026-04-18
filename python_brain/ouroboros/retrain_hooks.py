"""Ouroboros retrain hooks — explicit references for dead-code sweep.

Imports:
  - retrain_meta_labeler (scripts/retrain_meta_labeler.py) — nightly meta-labeler retrain
  - cpcv_embargo_auto — autocorrelation-aware embargo sizing
  - arcticdb_adapter — high-perf time-series persistence

These are all the modules Ouroboros calls on its nightly run. This file
makes the import chain explicit so dead-code scans find them.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path("/Users/rr/aegis-v5")
sys.path.insert(0, str(ROOT))

# Explicit imports — referenced by retrain_hooks
from scripts.retrain_meta_labeler import retrain_meta_labeler  # noqa: F401
from python_brain.quant.cpcv_embargo_auto import embargo_horizon_aware  # noqa: F401
from python_brain.infra.arcticdb_adapter import ArcticAdapter, capabilities  # noqa: F401


NIGHTLY_HOOKS = {
    "retrain_meta_labeler": retrain_meta_labeler,
    "embargo_horizon_aware": embargo_horizon_aware,
    "arctic_adapter": ArcticAdapter,
}


def run_all_hooks() -> dict:
    """Invoke every nightly hook — called by ouroboros_v3_nightly."""
    import logging
    log = logging.getLogger("ouroboros-hooks")
    results = {}
    # Retrain meta-labeler
    try:
        r = retrain_meta_labeler()
        results["retrain_meta_labeler"] = r
    except Exception as e:
        results["retrain_meta_labeler"] = {"error": str(e)}
    # ArcticDB capabilities check
    try:
        results["arctic_capabilities"] = capabilities()
    except Exception as e:
        results["arctic_capabilities"] = {"error": str(e)}
    # Embargo sizer (smoke) — real usage is per-strategy inside CPCV
    results["embargo_sizer_available"] = callable(embargo_horizon_aware)
    return results


if __name__ == "__main__":
    import json
    print(json.dumps(run_all_hooks(), indent=2, default=str))
