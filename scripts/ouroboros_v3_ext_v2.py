"""Ouroboros v3-ext v2 — wraps v3-ext and adds drift check + options_flow tracker.

This is the canonical nightly entry point. Supervisor should schedule THIS
(not v3_nightly or v3_ext) at 23:30 UTC.

Pipeline:
  1. v3_ext (base v3 + retrain_hooks + bandit v2 writeback + learned.toml cleanup)
  2. retrain_hooks_v2 (drift check + extra cleanup)
  3. options_flow_tracker (options flow nightly stats)
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

log = logging.getLogger("ouro-v3-ext-v2")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def run_nightly_complete() -> dict:
    report = {"ts": time.time(), "pipeline": "v3_ext_v2", "steps": {}}

    # Step 1: v3_ext base (inherits v3_nightly + retrain_hooks v1 + bandit v2)
    try:
        from scripts.ouroboros_v3_ext import run_nightly_v3_ext
        report["steps"]["v3_ext"] = run_nightly_v3_ext()
    except Exception as e:
        report["steps"]["v3_ext"] = {"error": str(e)}

    # Step 2: Extended hooks — drift check + cleanup
    try:
        from python_brain.ouroboros.retrain_hooks_v2 import run_extended_hooks
        report["steps"]["extended_hooks"] = run_extended_hooks()
    except Exception as e:
        report["steps"]["extended_hooks"] = {"error": str(e)}

    # Step 3: Options flow nightly stats
    try:
        from python_brain.ouroboros.options_flow_tracker import analyze_nightly
        report["steps"]["options_flow"] = analyze_nightly()
    except Exception as e:
        report["steps"]["options_flow"] = {"error": str(e)}

    # Persist
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = ROOT / "data/ouroboros_v3_ext_v2_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{date_str}.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    log.info("ouroboros v3-ext-v2 complete -> %s", out)
    return report


if __name__ == "__main__":
    run_nightly_complete()
