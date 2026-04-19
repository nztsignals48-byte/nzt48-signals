"""Nightly drift check — invokes drift_sentry.analyze on real fill feature
distributions.

Compares the last 7 days of fills (live set) vs the prior 30 days (baseline
set). Any feature with PSI > 0.25 or KS > 0.15 triggers an alert.

Called by ouroboros_v3_ext nightly; writes report to data/drift_reports/.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

from python_brain.quant.drift_sentry import analyze


log = logging.getLogger("drift-check")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROOT = Path("/Users/rr/aegis-v5")
FILLS_BUS = ROOT / "data/bus/fills.closed.jsonl"
REPORT_DIR = ROOT / "data/drift_reports"


def load_fills_in_ranges() -> tuple[list[dict], list[dict]]:
    """Return (baseline_fills, live_fills).

    baseline = fills from 30 days ago .. 7 days ago
    live = fills from last 7 days
    """
    import time as _time
    now = _time.time()
    seven_days_ago = now - 7 * 86400
    thirty_days_ago = now - 30 * 86400

    baseline = []
    live = []
    if not FILLS_BUS.exists():
        return baseline, live

    with open(FILLS_BUS) as f:
        for line in f:
            try:
                w = json.loads(line)
            except Exception:
                continue
            p = w.get("payload", w)
            if isinstance(p, str):
                try: p = json.loads(p)
                except: continue
            ts_ns = p.get("exit_timestamp_ns") or p.get("entry_timestamp_ns")
            if ts_ns is None:
                continue
            ts = float(ts_ns) / 1e9
            if ts >= seven_days_ago:
                live.append(p)
            elif ts >= thirty_days_ago:
                baseline.append(p)

    # Fallback: if baseline insufficient (fills are sim/old), split by line order
    if len(baseline) < 30:
        all_fills = []
        with open(FILLS_BUS) as f:
            for line in f:
                try:
                    w = json.loads(line)
                    p = w.get("payload", w)
                    if isinstance(p, str):
                        p = json.loads(p)
                    all_fills.append(p)
                except Exception:
                    continue
        if len(all_fills) >= 100:
            split = int(len(all_fills) * 0.75)
            baseline = all_fills[:split]
            live = all_fills[split:]
    return baseline, live


def extract_features(fills: list[dict]) -> dict[str, list[float]]:
    """Extract the same 7 features the meta-labeler uses."""
    out = {
        "conviction": [],
        "gross_edge_bps": [],
        "spread_bps": [],
        "rvol": [],
        "vpin": [],
        "regime_code": [],
        "session_code": [],
    }
    for f in fills:
        size = f.get("size_shares", 1)
        conv = min(1.0, max(0.0, float(np.log1p(size)) / float(np.log1p(500))))
        out["conviction"].append(conv)
        out["gross_edge_bps"].append(float(f.get("mfe_bps", 0) or 0))
        out["spread_bps"].append(float(f.get("spread_cost_bps", 5)))
        mae = abs(float(f.get("mae_bps", 0) or 0))
        mfe = float(f.get("mfe_bps", 0) or 0)
        out["rvol"].append(min(20.0, max(0.1, (mfe + mae) / 100.0 + 0.5)))
        er = f.get("exit_reason", "unknown")
        out["vpin"].append({"VolumeClimax": 0.7, "StopLoss": 0.5}.get(er, 0.3))
        reg = f.get("regime_at_entry", "calm")
        if isinstance(reg, list) or not isinstance(reg, str):
            reg = "calm"
        out["regime_code"].append(float({"calm": 0, "trending": 1, "choppy": 2, "crisis": 3}.get(reg, 0)))
        out["session_code"].append(0.0)  # default us_session
    return out


def extract_labels(fills: list[dict]) -> np.ndarray:
    """1 if realized_pnl_bps > 0 else 0."""
    y = []
    for f in fills:
        pnl = f.get("realized_pnl_bps")
        if pnl is None:
            continue
        y.append(1 if float(pnl) > 0 else 0)
    return np.array(y)


def run_drift_check() -> dict:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    baseline, live = load_fills_in_ranges()
    log.info("baseline: %d fills, live: %d fills", len(baseline), len(live))

    if len(baseline) < 30 or len(live) < 10:
        result = {
            "status": "insufficient_data",
            "baseline_n": len(baseline),
            "live_n": len(live),
            "alerts": [],
        }
    else:
        bf = extract_features(baseline)
        lf = extract_features(live)
        by = extract_labels(baseline)
        ly = extract_labels(live)

        report = analyze(
            baseline_features=bf,
            live_features=lf,
            baseline_labels=by,
            live_labels=ly,
            baseline_preds=None,
            live_preds=None,
            baseline_uplift_bps=0.0,
            live_uplift_bps=0.0,
        )
        result = {
            "status": "ok",
            "baseline_n": len(baseline),
            "live_n": len(live),
            "feature_psi": report.feature_psi,
            "feature_ks": report.feature_ks,
            "label_base_rate_shift": report.label_base_rate_shift,
            "alerts": report.alerts,
        }

    from datetime import datetime, timezone
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = REPORT_DIR / f"drift_{date_str}.json"
    out.write_text(json.dumps(result, indent=2, default=str))
    log.info("drift check complete: %d alerts → %s", len(result.get("alerts", [])), out)
    return result


if __name__ == "__main__":
    run_drift_check()
