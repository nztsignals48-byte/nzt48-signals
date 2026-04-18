"""4-layer drift sentry — monitors feature/label/calibration/uplift drift.

Alerts only (no gating). Published to drift.alerts NATS subject; Grafana
dashboard consumes for visualization.

Consumed by Ouroboros nightly for model-freshness decisions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass
class DriftReport:
    feature_psi: dict = field(default_factory=dict)   # per-feature PSI
    feature_ks: dict = field(default_factory=dict)    # per-feature KS
    label_base_rate_shift: float = 0.0
    calibration_brier_change: float = 0.0
    uplift_drift_bps: float = 0.0
    alerts: list = field(default_factory=list)


def population_stability_index(
    baseline: np.ndarray,
    live: np.ndarray,
    n_bins: int = 10,
) -> float:
    """PSI — classic population stability metric.
    PSI < 0.1 = stable, 0.1-0.25 = moderate drift, > 0.25 = significant drift.
    """
    if len(baseline) < 10 or len(live) < 10:
        return 0.0
    _, edges = np.histogram(baseline, bins=n_bins)
    b_hist, _ = np.histogram(baseline, bins=edges)
    l_hist, _ = np.histogram(live, bins=edges)
    b_pct = b_hist / max(b_hist.sum(), 1)
    l_pct = l_hist / max(l_hist.sum(), 1)
    # Smooth zero bins
    b_pct = np.where(b_pct == 0, 1e-6, b_pct)
    l_pct = np.where(l_pct == 0, 1e-6, l_pct)
    return float(np.sum((l_pct - b_pct) * np.log(l_pct / b_pct)))


def ks_statistic(baseline: np.ndarray, live: np.ndarray) -> float:
    """Kolmogorov-Smirnov two-sample distance."""
    if len(baseline) < 5 or len(live) < 5:
        return 0.0
    b_sorted = np.sort(baseline)
    l_sorted = np.sort(live)
    all_x = np.concatenate([b_sorted, l_sorted])
    b_cdf = np.searchsorted(b_sorted, all_x, side="right") / len(b_sorted)
    l_cdf = np.searchsorted(l_sorted, all_x, side="right") / len(l_sorted)
    return float(np.max(np.abs(b_cdf - l_cdf)))


def brier_score(probs: np.ndarray, labels: np.ndarray) -> float:
    """Mean squared error of probabilistic predictions."""
    if len(probs) == 0:
        return 0.0
    return float(np.mean((probs - labels) ** 2))


def analyze(
    baseline_features: dict,
    live_features: dict,
    baseline_labels: np.ndarray | None = None,
    live_labels: np.ndarray | None = None,
    baseline_preds: np.ndarray | None = None,
    live_preds: np.ndarray | None = None,
    baseline_uplift_bps: float = 0.0,
    live_uplift_bps: float = 0.0,
    thresholds: dict | None = None,
) -> DriftReport:
    """Full 4-layer drift analysis."""
    thr = thresholds or {
        "psi_alert": 0.25,
        "ks_alert": 0.15,
        "label_shift_alert": 0.10,
        "brier_alert": 0.05,
        "uplift_drift_alert_bps": 5.0,
    }
    report = DriftReport()

    # Layer 1: feature PSI + KS
    for name in baseline_features:
        if name not in live_features:
            continue
        b = np.asarray(baseline_features[name], dtype=float)
        l = np.asarray(live_features[name], dtype=float)
        psi = population_stability_index(b, l)
        ks = ks_statistic(b, l)
        report.feature_psi[name] = psi
        report.feature_ks[name] = ks
        if psi > thr["psi_alert"]:
            report.alerts.append(f"PSI alert feature={name}: {psi:.3f}")
        if ks > thr["ks_alert"]:
            report.alerts.append(f"KS alert feature={name}: {ks:.3f}")

    # Layer 2: label base rate
    if baseline_labels is not None and live_labels is not None \
            and len(baseline_labels) > 0 and len(live_labels) > 0:
        b_rate = float(np.mean(baseline_labels))
        l_rate = float(np.mean(live_labels))
        shift = abs(l_rate - b_rate)
        report.label_base_rate_shift = shift
        if shift > thr["label_shift_alert"]:
            report.alerts.append(f"Label base-rate shift: {b_rate:.3f} -> {l_rate:.3f}")

    # Layer 3: calibration (Brier)
    if all(x is not None and len(x) > 0 for x in [baseline_preds, live_preds, baseline_labels, live_labels]):
        b_brier = brier_score(baseline_preds, baseline_labels)
        l_brier = brier_score(live_preds, live_labels)
        change = l_brier - b_brier
        report.calibration_brier_change = change
        if change > thr["brier_alert"]:
            report.alerts.append(f"Calibration drift: Brier {b_brier:.3f} -> {l_brier:.3f}")

    # Layer 4: uplift drift (realized vs expected)
    uplift_change = live_uplift_bps - baseline_uplift_bps
    report.uplift_drift_bps = uplift_change
    if abs(uplift_change) > thr["uplift_drift_alert_bps"]:
        report.alerts.append(f"Uplift drift: {baseline_uplift_bps:.2f} -> {live_uplift_bps:.2f} bps")

    return report


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        rng = np.random.default_rng(42)
        baseline = {"conviction": rng.normal(0.6, 0.15, 500).tolist()}
        live = {"conviction": rng.normal(0.7, 0.20, 100).tolist()}  # drifted
        b_labels = (rng.random(500) > 0.5).astype(int)
        l_labels = (rng.random(100) > 0.4).astype(int)
        b_preds = rng.random(500)
        l_preds = rng.random(100)

        r = analyze(
            baseline, live, b_labels, l_labels, b_preds, l_preds,
            baseline_uplift_bps=2.0, live_uplift_bps=0.5,
        )
        print(f"PSI: {r.feature_psi}")
        print(f"KS: {r.feature_ks}")
        print(f"Label shift: {r.label_base_rate_shift:.3f}")
        print(f"Brier change: {r.calibration_brier_change:.3f}")
        print(f"Uplift drift: {r.uplift_drift_bps:.2f}")
        print(f"Alerts: {r.alerts}")
        print("OK")
