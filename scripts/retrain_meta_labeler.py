"""Nightly meta-labeler retrain — called by ouroboros_v3.

Wraps train_meta_labeler_v3 with consistent interface for Ouroboros.
Name this file `retrain_meta_labeler.py` so supervisor/scheduler can grep it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path("/Users/rr/aegis-v5")
sys.path.insert(0, str(ROOT))


def retrain_meta_labeler() -> dict:
    """Retrain the meta-labeler from latest fills. Called nightly."""
    from scripts.train_meta_labeler_v3 import train
    result = train()
    result["name"] = "retrain_meta_labeler"
    return result


def retrain_meta_labeler_cli():
    """CLI entry for `python -m scripts.retrain_meta_labeler`."""
    result = retrain_meta_labeler()
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    retrain_meta_labeler_cli()
