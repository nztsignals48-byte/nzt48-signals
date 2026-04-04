"""SHAP Model Interpretability — explain why signals fire.

Provides Shapley value explanations for the LightGBM meta-model:
  - Which features contributed most to a signal's score
  - Nightly summary of feature importance trends
  - Per-trade attribution for forensic review

Output feeds into:
  - Nightly report (top feature importances)
  - Claude forensic review (individual signal explanations)
  - Grafana dashboard (feature importance heatmap)

License: SHAP is MIT.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("shap_explainer")

try:
    import shap
    import numpy as np
    _HAS_SHAP = True
except ImportError:
    _HAS_SHAP = False

_DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
_OUTPUT_PATH = _DATA_DIR / "shap_report.json"


def explain_signal(
    model,
    signal_features: Dict[str, float],
    feature_names: List[str],
) -> Optional[Dict[str, float]]:
    """Explain a single signal prediction using SHAP.

    Args:
        model: Trained LightGBM model (lgb.Booster).
        signal_features: Dict of feature values for this signal.
        feature_names: Ordered list of feature column names.

    Returns:
        Dict of {feature_name: shap_value} or None.
    """
    if not _HAS_SHAP:
        return None

    try:
        X = np.array([[signal_features.get(f, 0.0) for f in feature_names]])
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X)

        # shap_values shape: (1, n_features) for binary classification
        if isinstance(shap_values, list):
            shap_values = shap_values[1]  # Class 1 (profitable)

        explanation = {
            feature_names[i]: round(float(shap_values[0][i]), 6)
            for i in range(len(feature_names))
        }

        # Sort by absolute contribution
        explanation = dict(sorted(explanation.items(), key=lambda x: abs(x[1]), reverse=True))
        return explanation

    except Exception as e:
        log.warning("SHAP explanation failed: %s", str(e)[:100])
        return None


def explain_batch(
    model,
    signals: List[Dict[str, float]],
    feature_names: List[str],
) -> List[Optional[Dict[str, float]]]:
    """Explain multiple signals efficiently."""
    if not _HAS_SHAP or not signals:
        return [None] * len(signals)

    try:
        X = np.array([
            [s.get(f, 0.0) for f in feature_names]
            for s in signals
        ])
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X)

        if isinstance(shap_values, list):
            shap_values = shap_values[1]

        results = []
        for i in range(len(signals)):
            explanation = {
                feature_names[j]: round(float(shap_values[i][j]), 6)
                for j in range(len(feature_names))
            }
            results.append(dict(sorted(explanation.items(), key=lambda x: abs(x[1]), reverse=True)))

        return results

    except Exception as e:
        log.warning("SHAP batch explanation failed: %s", str(e)[:100])
        return [None] * len(signals)


def generate_importance_report(
    model,
    signals: List[Dict[str, float]],
    feature_names: List[str],
    output_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Generate nightly SHAP importance report.

    Summarizes average absolute SHAP values across all signals.
    Called by nightly pipeline.
    """
    if not _HAS_SHAP:
        log.warning("SHAP not installed — importance report skipped")
        return None

    if len(signals) < 10:
        log.info("Insufficient signals for SHAP report: %d < 10", len(signals))
        return None

    try:
        X = np.array([
            [s.get(f, 0.0) for f in feature_names]
            for s in signals
        ])
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X)

        if isinstance(shap_values, list):
            shap_values = shap_values[1]

        # Average absolute SHAP values per feature
        mean_abs_shap = np.abs(shap_values).mean(axis=0)
        importance = {
            feature_names[i]: round(float(mean_abs_shap[i]), 6)
            for i in range(len(feature_names))
        }
        importance = dict(sorted(importance.items(), key=lambda x: x[1], reverse=True))

        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "n_signals": len(signals),
            "feature_importance": importance,
            "top_3_features": list(importance.keys())[:3],
        }

        if output_path is None:
            output_path = str(_OUTPUT_PATH)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)

        log.info("SHAP report: top features = %s", list(importance.keys())[:5])
        return result

    except Exception as e:
        log.warning("SHAP report generation failed: %s", str(e)[:100])
        return None
