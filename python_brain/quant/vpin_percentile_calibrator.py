"""VPIN percentile calibrator — replaces static 0.6/0.8 threshold.

Maintains rolling 30-day VPIN distribution per (symbol, session) and sets
veto threshold at 95th percentile. Writes results to learned.toml for
sig2order consumption.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path

import numpy as np


log = logging.getLogger("vpin-calib")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROOT = Path("/Users/rr/aegis-v5")
VPIN_ARCHIVE = ROOT / "data/archive"
OUTPUT = ROOT / "data/vpin_thresholds.json"


WINDOW_DAYS = 30
PERCENTILE = 95


def session_of(hour_utc: int) -> str:
    # Asia 00-08, EU 08-14, US 14-21, Overnight 21-24
    if 0 <= hour_utc < 8:
        return "asia"
    if 8 <= hour_utc < 14:
        return "europe"
    if 14 <= hour_utc < 21:
        return "us"
    return "overnight"


def run_calibration() -> dict:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    result: dict = {"thresholds": {}}

    files = sorted(VPIN_ARCHIVE.glob("regime_vpin_*.jsonl"))
    if not files:
        log.info("no VPIN archives yet")
        OUTPUT.write_text(json.dumps(result, indent=2))
        return result

    # Collect per (symbol, session)
    collected: dict[tuple[str, str], list[float]] = defaultdict(list)
    for path in files[-WINDOW_DAYS:]:
        try:
            with open(path) as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    ticker = d.get("ticker") or d.get("symbol")
                    vpin = d.get("vpin")
                    ts = d.get("ts")
                    if not ticker or vpin is None:
                        continue
                    from datetime import datetime, timezone
                    try:
                        hour = datetime.fromtimestamp(float(ts), tz=timezone.utc).hour
                    except Exception:
                        hour = 12
                    collected[(ticker, session_of(hour))].append(float(vpin))
        except Exception as e:
            log.warning("skipped %s: %s", path, e)

    for (sym, sess), values in collected.items():
        if len(values) < 50:
            continue
        thr = float(np.percentile(values, PERCENTILE))
        result["thresholds"][f"{sym}::{sess}"] = {
            "threshold": thr,
            "n_samples": len(values),
            "percentile": PERCENTILE,
            "mean": float(np.mean(values)),
        }

    OUTPUT.write_text(json.dumps(result, indent=2))
    log.info("calibrated %d (symbol, session) pairs", len(result["thresholds"]))
    return result


def get_threshold_for(symbol: str, session: str) -> float:
    """Read calibrated threshold, default to 0.75 if unknown."""
    if not OUTPUT.exists():
        return 0.75
    try:
        data = json.loads(OUTPUT.read_text())
    except Exception:
        return 0.75
    key = f"{symbol}::{session}"
    info = data.get("thresholds", {}).get(key)
    return float(info["threshold"]) if info else 0.75


if __name__ == "__main__":
    run_calibration()
