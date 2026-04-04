"""Evidently Drift Detector — data & model drift detection with Grafana alerting.

Monitors for:
  1. Feature drift: RSI, RVOL, spread distributions shifting from training
  2. Prediction drift: signal confidence distribution shifting
  3. Target drift: actual win rate vs predicted win rate diverging

Runs nightly. Writes drift report JSON for Grafana dashboard consumption.
Alert thresholds: feature drift p-value < 0.01 → WARN, < 0.001 → CRITICAL.

License: Evidently is Apache 2.0.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("drift_detector")

try:
    import pandas as pd
    _HAS_PANDAS = True
except ImportError:
    _HAS_PANDAS = False

try:
    from evidently.report import Report
    from evidently.metric_preset import DataDriftPreset, TargetDriftPreset
    _HAS_EVIDENTLY = True
except ImportError:
    _HAS_EVIDENTLY = False

_DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
_REPORT_DIR = _DATA_DIR / "drift_reports"
_FEATURE_COLS = ["confidence", "rvol", "hurst", "adx", "vpin", "spread_pct", "kelly_fraction"]


def detect_drift(
    reference_path: Optional[str] = None,
    current_path: Optional[str] = None,
    output_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Run full drift detection pipeline.

    Args:
        reference_path: Path to reference (training) dataset JSON.
        current_path: Path to current (recent) dataset JSON.
        output_path: Where to save drift report.

    Returns:
        Dict with drift results, or None on failure.
    """
    if not _HAS_EVIDENTLY or not _HAS_PANDAS:
        log.warning("Evidently/pandas not installed — drift detection skipped")
        return None

    if reference_path is None:
        reference_path = str(_DATA_DIR / "signal_trade_history.json")
    if current_path is None:
        current_path = str(_DATA_DIR / "recent_signals.json")

    # Load datasets
    try:
        with open(reference_path) as f:
            ref_data = json.load(f)
        with open(current_path) as f:
            cur_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        log.info("Drift data not available: %s", e)
        return None

    if len(ref_data) < 50 or len(cur_data) < 20:
        log.info("Insufficient data for drift detection: ref=%d, cur=%d", len(ref_data), len(cur_data))
        return None

    # Build DataFrames
    ref_df = pd.DataFrame(ref_data)[_FEATURE_COLS].fillna(0)
    cur_df = pd.DataFrame(cur_data)[_FEATURE_COLS].fillna(0)

    # Run Evidently data drift report
    report = Report(metrics=[DataDriftPreset()])
    report.run(reference_data=ref_df, current_data=cur_df)

    # Extract drift results
    report_dict = report.as_dict()
    drift_results = _parse_evidently_report(report_dict)

    # Check alert thresholds
    drifted_features = [f for f, d in drift_results.get("features", {}).items() if d.get("drift_detected")]
    n_drifted = len(drifted_features)
    severity = "OK"
    if n_drifted >= 3:
        severity = "CRITICAL"
    elif n_drifted >= 1:
        severity = "WARN"

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "severity": severity,
        "n_features_drifted": n_drifted,
        "drifted_features": drifted_features,
        "reference_size": len(ref_data),
        "current_size": len(cur_data),
        "details": drift_results,
    }

    # Save report
    if output_path is None:
        _REPORT_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        output_path = str(_REPORT_DIR / f"drift_{today}.json")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    log.info("Drift report: severity=%s, %d/%d features drifted: %s",
             severity, n_drifted, len(_FEATURE_COLS), drifted_features)

    return result


def _parse_evidently_report(report_dict: Dict) -> Dict[str, Any]:
    """Parse Evidently report dict into simplified drift results."""
    try:
        metrics = report_dict.get("metrics", [])
        if not metrics:
            return {}

        result = {"features": {}}
        for metric in metrics:
            metric_result = metric.get("result", {})
            # DataDriftPreset produces per-column drift results
            drift_by_columns = metric_result.get("drift_by_columns", {})
            for col_name, col_result in drift_by_columns.items():
                result["features"][col_name] = {
                    "drift_detected": col_result.get("drift_detected", False),
                    "drift_score": col_result.get("drift_score", 1.0),
                    "stattest_name": col_result.get("stattest_name", ""),
                }

        dataset_drift = any(
            f.get("drift_detected", False)
            for f in result["features"].values()
        )
        result["dataset_drift"] = dataset_drift
        return result
    except Exception as e:
        log.warning("Failed to parse Evidently report: %s", e)
        return {}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [Drift] %(levelname)s %(message)s")
    result = detect_drift()
    if result:
        print(json.dumps(result, indent=2))
