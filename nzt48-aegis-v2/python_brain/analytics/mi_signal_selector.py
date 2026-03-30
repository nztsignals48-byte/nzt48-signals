"""Book 119: Information Theory Signal Selection.

Computes mutual information between signal features and trade outcomes
to identify which indicators carry real predictive power (including
nonlinear relationships that Pearson correlation misses).

Runs nightly to produce a feature importance ranking that informs:
  - Which indicators to weight more heavily in signal confidence
  - Which features are redundant (carry same information)
  - Whether new features add incremental value

Key concepts:
  MI(feature; outcome) — total predictive information in bits/nats
  CMI(feature; outcome | existing) — incremental info beyond what we have
  TE(X→Y) — directed information flow (for lead-lag validation)

Usage (nightly):
    from python_brain.analytics.mi_signal_selector import run_mi_analysis
    report = run_mi_analysis("/app/data/trade_log.ndjson")

Usage (bridge.py confidence adjustment):
    from python_brain.analytics.mi_signal_selector import load_feature_weights
    weights = load_feature_weights()  # {feature_name: importance_score}
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("mi_signal_selector")

# ---------------------------------------------------------------------------
# Core MI computation (stdlib only — no sklearn dependency)
# ---------------------------------------------------------------------------

def _digitize(values: List[float], n_bins: int = 10) -> List[int]:
    """Bin continuous values into discrete bins."""
    if not values:
        return []
    vmin, vmax = min(values), max(values)
    if vmax == vmin:
        return [0] * len(values)
    bin_width = (vmax - vmin) / n_bins
    return [min(int((v - vmin) / bin_width), n_bins - 1) for v in values]


def mutual_information(x: List[float], y: List[float], n_bins: int = 10) -> float:
    """Compute mutual information I(X;Y) using histogram estimator.

    Returns MI in nats (natural log). No external dependencies.
    """
    n = min(len(x), len(y))
    if n < 20:
        return 0.0

    x_bins = _digitize(x[:n], n_bins)
    y_bins = _digitize(y[:n], n_bins)

    # Joint and marginal counts
    joint = defaultdict(int)
    x_margin = defaultdict(int)
    y_margin = defaultdict(int)
    for i in range(n):
        joint[(x_bins[i], y_bins[i])] += 1
        x_margin[x_bins[i]] += 1
        y_margin[y_bins[i]] += 1

    # MI = sum p(x,y) * log(p(x,y) / (p(x)*p(y)))
    mi = 0.0
    for (xb, yb), count in joint.items():
        p_xy = count / n
        p_x = x_margin[xb] / n
        p_y = y_margin[yb] / n
        if p_xy > 0 and p_x > 0 and p_y > 0:
            mi += p_xy * math.log(p_xy / (p_x * p_y))

    return max(0.0, mi)  # MI is non-negative


def conditional_mutual_information(
    x: List[float], y: List[float], z: List[float], n_bins: int = 8
) -> float:
    """Compute conditional MI: I(X;Y|Z) using binned estimator.

    Measures info X carries about Y beyond what Z provides.
    """
    n = min(len(x), len(y), len(z))
    if n < 30:
        return 0.0

    x_bins = _digitize(x[:n], n_bins)
    y_bins = _digitize(y[:n], n_bins)
    z_bins = _digitize(z[:n], n_bins)

    # Group by Z bin, compute MI(X;Y) within each Z bin, weight by P(Z)
    z_groups = defaultdict(list)
    for i in range(n):
        z_groups[z_bins[i]].append(i)

    cmi = 0.0
    for z_val, indices in z_groups.items():
        if len(indices) < 10:
            continue
        weight = len(indices) / n
        x_sub = [x[i] for i in indices]
        y_sub = [y[i] for i in indices]
        mi_sub = mutual_information(x_sub, y_sub, min(n_bins, max(3, len(indices) // 5)))
        cmi += weight * mi_sub

    return max(0.0, cmi)


def transfer_entropy(
    x: List[float], y: List[float], lag: int = 1, n_bins: int = 8
) -> float:
    """Compute transfer entropy TE(X→Y) = I(Y_future; X_past | Y_past).

    Measures directed information flow from X to Y.
    """
    n = min(len(x), len(y))
    if n < lag + 30:
        return 0.0

    y_future = y[lag:n]
    x_past = x[:n - lag]
    y_past = y[:n - lag]

    return conditional_mutual_information(x_past, y_future, y_past, n_bins)


# ---------------------------------------------------------------------------
# Feature analysis on trade history
# ---------------------------------------------------------------------------

# Features we track from signal dicts
SIGNAL_FEATURES = [
    "rvol", "hurst", "adx", "vpin", "vol_slope", "structural_score",
    "vwap_dist_pct", "d_vpin", "ibs_entry", "rsi2_entry",
    "leader_move_pct", "follower_lag_pct", "expected_catchup_pct",
    "ensemble_value", "bayes_posterior",
]


def run_mi_analysis(
    trade_log_path: str = "/app/data/trade_log.ndjson",
    output_path: str = "/app/data/feature_importance.json",
) -> dict:
    """Run MI-based feature importance analysis on trade history.

    Reads trade log, extracts features and outcomes (P&L direction),
    computes MI for each feature, then ranks by importance.
    """
    # Load trade data
    trades = []
    try:
        with open(trade_log_path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        trades.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except FileNotFoundError:
        log.warning(f"Trade log not found: {trade_log_path}")
        return {"status": "no_data", "features": {}}

    if len(trades) < 30:
        log.info(f"Insufficient trades ({len(trades)}) for MI analysis (need 30+)")
        return {"status": "insufficient_data", "n_trades": len(trades), "features": {}}

    # Extract features and outcomes
    feature_values = defaultdict(list)
    outcomes = []
    for trade in trades:
        pnl = trade.get("pnl", trade.get("realized_pnl", 0))
        if pnl is None:
            continue
        outcomes.append(1.0 if pnl > 0 else 0.0)

        for feat in SIGNAL_FEATURES:
            val = trade.get(feat)
            if val is not None and isinstance(val, (int, float)) and math.isfinite(val):
                feature_values[feat].append(float(val))
            else:
                feature_values[feat].append(0.0)

    if len(outcomes) < 30:
        return {"status": "insufficient_outcomes", "n_outcomes": len(outcomes), "features": {}}

    # Compute MI for each feature
    results = {}
    for feat in SIGNAL_FEATURES:
        values = feature_values[feat]
        if len(values) == len(outcomes) and len(set(values)) > 2:
            mi = mutual_information(values, outcomes)
            results[feat] = {"mi": round(mi, 6), "n_samples": len(values)}
        else:
            results[feat] = {"mi": 0.0, "n_samples": len(values)}

    # Rank by MI
    ranked = sorted(results.items(), key=lambda kv: kv[1]["mi"], reverse=True)

    # Compute incremental (conditional) MI for top features
    selected = []
    for feat, info in ranked:
        if info["mi"] < 0.001:
            break
        if not selected:
            info["cmi"] = info["mi"]
            info["rank"] = 1
            selected.append(feat)
        else:
            cmi = conditional_mutual_information(
                feature_values[feat], outcomes, feature_values[selected[-1]]
            )
            info["cmi"] = round(cmi, 6)
            if cmi > 0.001:
                info["rank"] = len(selected) + 1
                selected.append(feat)
            else:
                info["rank"] = 0  # Redundant with already-selected features

    # Generate feature weights (normalized MI scores for bridge.py)
    max_mi = max((v["mi"] for v in results.values()), default=1.0)
    weights = {}
    if max_mi > 0:
        for feat, info in results.items():
            weights[feat] = round(info["mi"] / max_mi, 4)

    report = {
        "status": "ok",
        "n_trades": len(trades),
        "n_outcomes": len(outcomes),
        "features": results,
        "selected_order": selected,
        "feature_weights": weights,
        "top_5": [f"{feat} (MI={results[feat]['mi']:.4f})" for feat in selected[:5]],
    }

    # Save
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2)
        log.info(f"MI analysis saved to {output_path}: {len(selected)} features selected")
    except Exception as e:
        log.error(f"Failed to save MI report: {e}")

    return report


def load_feature_weights(path: str = "/app/data/feature_importance.json") -> Dict[str, float]:
    """Load pre-computed feature weights for bridge.py confidence adjustment."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data.get("feature_weights", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    trade_log = sys.argv[1] if len(sys.argv) > 1 else "/app/data/trade_log.ndjson"
    report = run_mi_analysis(trade_log)
    print(json.dumps(report, indent=2))
