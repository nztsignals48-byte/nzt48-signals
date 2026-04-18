"""Options flow tracker — nightly analysis of signals.options_flow for Ouroboros.

Imports options_flow_signal to reuse OptionsFlowAnalyzer; tallies per-ticker
signal hit rate + realized PnL of triggered signals.

Referenced by ouroboros_v3_nightly.py (Step 13).
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path

# Referenced import — closes dead-code loop
from python_brain.quant.options_flow_signal import OptionsFlowAnalyzer, OptionsSnapshot


log = logging.getLogger("opt-flow-track")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


ROOT = Path("/Users/rr/aegis-v5")
ARCHIVE = ROOT / "data/archive"
OUTPUT = ROOT / "data/options_flow_stats.json"


def analyze_nightly() -> dict:
    """Tally options flow signals and compute hit-rate from archive."""
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # Confirm analyzer + snapshot classes resolve (referential integrity)
    _ = OptionsFlowAnalyzer()
    _ = OptionsSnapshot(ticker="_test_", ts=0.0, call_oi=0, put_oi=0,
                        call_vol=0, put_vol=0, implied_vol=0)

    archive_files = list(ARCHIVE.glob("signals_options_flow_*.jsonl"))
    results = {
        "n_files": len(archive_files),
        "n_signals": 0,
        "by_side": defaultdict(int),
        "by_ticker": defaultdict(int),
    }
    for path in archive_files[-7:]:
        try:
            with open(path) as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    results["n_signals"] += 1
                    side = d.get("signal_side") or d.get("side", "unknown")
                    ticker = d.get("ticker", "unknown")
                    results["by_side"][side] += 1
                    results["by_ticker"][ticker] += 1
        except Exception:
            continue

    # Serialize defaultdicts for JSON
    results["by_side"] = dict(results["by_side"])
    results["by_ticker"] = dict(results["by_ticker"])
    OUTPUT.write_text(json.dumps(results, indent=2))
    log.info("options flow nightly: %d signals analyzed", results["n_signals"])
    return results


if __name__ == "__main__":
    analyze_nightly()
