"""Nightly meta-labeler retrain — uses v4 trainer (7-feature schema).

IMPORTANT: v4 trains with the SAME 7 features that live inference
(python_brain/quant/meta_labeler.py) produces. v3 used 10 features which
caused silent 0.5 fallback at inference. Keep this pinned to v4.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path("/Users/rr/aegis-v5")
sys.path.insert(0, str(ROOT))


def retrain_meta_labeler() -> dict:
    """Retrain the meta-labeler from latest fills. Called nightly."""
    from scripts.train_meta_labeler_v4 import train
    result = train()
    result["name"] = "retrain_meta_labeler"
    result["trainer_version"] = "v4"
    return result


def retrain_meta_labeler_cli():
    result = retrain_meta_labeler()
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    retrain_meta_labeler_cli()
