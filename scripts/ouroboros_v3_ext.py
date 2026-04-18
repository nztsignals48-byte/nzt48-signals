"""Ouroboros v3 extension — wires retrain_hooks into the nightly cycle.

ouroboros_v3_nightly.py was built without calling run_all_hooks, so
retrain_meta_labeler + arctic_capabilities never execute nightly.

This script runs ouroboros_v3_nightly.run_all() first, then invokes
run_all_hooks(), merging results into one report.

Scheduled by supervisor daily at 23:30 UTC.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/rr/aegis-v5")
sys.path.insert(0, str(ROOT))

log = logging.getLogger("ouro-v3-ext")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def run_nightly_v3_ext() -> dict:
    """Run extended nightly: v3 base + hooks + idempotent bandit write."""
    report = {"ts": time.time(), "extended": True, "steps": {}}

    # Step 1: v3 base cycle
    try:
        from scripts.ouroboros_v3_nightly import run_all as v3_base
        report["steps"]["v3_base"] = v3_base()
    except Exception as e:
        report["steps"]["v3_base"] = {"error": str(e)}

    # Step 2: run_all_hooks — retrain_meta_labeler, arctic_capabilities
    try:
        from python_brain.ouroboros.retrain_hooks import run_all_hooks
        report["steps"]["hooks"] = run_all_hooks()
    except Exception as e:
        report["steps"]["hooks"] = {"error": str(e)}

    # Step 3: Capital bandit v2 writes — idempotent
    try:
        from python_brain.quant.capital_bandit_v2 import ThompsonCapitalBanditV2
        from python_brain.quant.fdr_allocator import FDRAllocator
        bandit = ThompsonCapitalBanditV2()
        bandit._load()
        # Use FDR promotable set
        fdr = FDRAllocator()
        # promotable returns [] on fresh state — that's OK
        bandit.write_learned_toml(promotable=fdr.promotable())
        report["steps"]["bandit_v2_writeback"] = {
            "n_strategies": len(bandit.state.priors),
            "promotable": fdr.promotable(),
        }
    except Exception as e:
        report["steps"]["bandit_v2_writeback"] = {"error": str(e)}

    # Step 4: Clean learned.toml cruft defensively
    try:
        from python_brain.quant.learned_toml_writer import clean_learned_toml
        removed = clean_learned_toml()
        report["steps"]["learned_toml_cleanup_bytes"] = removed
    except Exception as e:
        report["steps"]["learned_toml_cleanup_bytes"] = {"error": str(e)}

    # Persist
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = ROOT / "data/ouroboros_v3_ext_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{date_str}.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    log.info("ouroboros v3-ext complete -> %s", out)
    return report


if __name__ == "__main__":
    run_nightly_v3_ext()
