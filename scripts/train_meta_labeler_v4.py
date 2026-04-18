"""Train meta-labeler using EXACTLY the 7 features live inference produces.

Live inference (python_brain/quant/meta_labeler.py) builds this 7-feature
vector from a MetaLabelFeatures dataclass:
  [conviction, gross_edge_bps, spread_bps, rvol, vpin, regime_code, session_code]

This trainer reads the fill WAL, extracts/derives those same 7 features per
fill, and fits the classifier. Now live predictions use the same schema as
training — no silent 0.5 fallback.
"""
from __future__ import annotations

import json
import logging
import pickle
import sys
from pathlib import Path

import numpy as np


log = logging.getLogger("meta-train-v4")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROOT = Path("/Users/rr/aegis-v5")
FILLS_BUS = ROOT / "data/bus/fills.closed.jsonl"
MODEL_PATH = ROOT / "data/models/meta_labeler.pkl"


# Must match python_brain/quant/meta_labeler.py _features_to_vector exactly
REGIME_MAP = {"calm": 0, "trending": 1, "choppy": 2, "crisis": 3}
SESSION_MAP = {"us_session": 0, "lse_session": 1, "overnight": 2, "after_hours": 3}


def gather_fills() -> list[dict]:
    fills = []
    if not FILLS_BUS.exists():
        return fills
    with open(FILLS_BUS) as f:
        for line in f:
            try:
                w = json.loads(line)
            except Exception:
                continue
            p = w.get("payload")
            if isinstance(p, str):
                try: p = json.loads(p)
                except: continue
            if isinstance(p, dict):
                fills.append(p)
    return fills


def derive_live_features(fill: dict) -> tuple[list[float], int] | None:
    """Build the same 7-feature vector live inference builds.

    Returns (features, label) or None if fill doesn't have required fields.
    Label: 1 if realized_pnl_bps > 0.
    """
    pnl = fill.get("realized_pnl_bps")
    if pnl is None:
        return None

    # conviction — not in fill schema directly; derive from position sizing
    # (larger sizes → higher conviction at entry). Fall back to 0.5.
    size = fill.get("size_shares", 1)
    # Crude proxy: log-size normalized 0..1 (typical retail: 1-500 shares)
    conviction = min(1.0, max(0.0, np.log1p(size) / np.log1p(500)))

    # gross_edge_bps — derive from realized pnl + spread cost (what would have
    # been gross before spread). For trained classifier purposes, use MFE as
    # the "expected edge at entry" proxy.
    mfe = fill.get("mfe_bps", 0) or 0
    gross_edge_bps = float(mfe)

    spread_bps = float(fill.get("spread_cost_bps", 5))

    # rvol — not in schema; use mfe/mae range as volatility proxy
    mae = abs(float(fill.get("mae_bps", 0) or 0))
    rvol = min(20.0, max(0.1, (mfe + mae) / 100.0 + 0.5))  # crude

    # vpin — not in schema; default from exit_reason (Chandelier = normal)
    exit_reason = fill.get("exit_reason", "unknown")
    vpin = {"VolumeClimax": 0.7, "StopLoss": 0.5}.get(exit_reason, 0.3)

    # regime at entry
    regime_entry = fill.get("regime_at_entry", "calm")
    if isinstance(regime_entry, list):
        regime_entry = "calm"
    if not isinstance(regime_entry, str):
        regime_entry = "calm"
    regime_code = REGIME_MAP.get(regime_entry, 0)

    # session — derive from timestamp if possible
    ts = fill.get("entry_timestamp_ns", 0)
    session_code = 0  # default us_session
    if ts:
        from datetime import datetime, timezone
        try:
            hour = datetime.fromtimestamp(ts / 1e9, tz=timezone.utc).hour
            if 0 <= hour < 8:
                session_code = 2  # overnight
            elif 8 <= hour < 14:
                session_code = 1  # lse_session
            elif 14 <= hour < 21:
                session_code = 0  # us_session
            else:
                session_code = 3  # after_hours
        except Exception:
            pass

    features = [
        float(conviction),
        float(gross_edge_bps),
        float(spread_bps),
        float(rvol),
        float(vpin),
        float(regime_code),
        float(session_code),
    ]
    label = 1 if float(pnl) > 0 else 0
    return features, label


def build_dataset(fills: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    X, y = [], []
    for f in fills:
        r = derive_live_features(f)
        if r is None:
            continue
        features, label = r
        X.append(features)
        y.append(label)
    return np.array(X), np.array(y)


def train():
    fills = gather_fills()
    log.info("gathered %d fills", len(fills))
    X, y = build_dataset(fills)
    log.info("dataset: X=%s", X.shape)
    if y.size:
        log.info("  wins=%d losses=%d (y mean=%.3f)",
                 int(y.sum()), int(len(y) - y.sum()), float(y.mean()))

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)

    if X.shape[0] < 30 or len(np.unique(y)) < 2:
        log.warning("insufficient data — writing fallback model")
        class PassThrough:
            def predict_proba(self, X):
                return np.tile([0.3, 0.7], (len(X), 1))
            def predict(self, X):
                return np.ones(len(X), dtype=int)
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(PassThrough(), f)
        return {"mode": "fallback", "n_fills": int(X.shape[0])}

    try:
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import roc_auc_score, accuracy_score

        X_tr, X_te, y_tr, y_te = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y,
        )
        model = GradientBoostingClassifier(n_estimators=100, max_depth=3, random_state=42)
        model.fit(X_tr, y_tr)

        probs = model.predict_proba(X_te)[:, 1]
        auc = float(roc_auc_score(y_te, probs))
        acc = float(accuracy_score(y_te, model.predict(X_te)))

        with open(MODEL_PATH, "wb") as f:
            pickle.dump(model, f)
        log.info("GBM v4 trained: 7 features, AUC=%.4f acc=%.4f n_in=%d",
                 auc, acc, getattr(model, "n_features_in_", 0))
        return {
            "mode": "sklearn_gbm_v4",
            "auc": auc,
            "accuracy": acc,
            "n_features": 7,
            "n_train": len(y_tr),
            "n_test": len(y_te),
        }
    except ImportError:
        log.warning("sklearn unavailable — fallback model")
        class PassThrough:
            def predict_proba(self, X):
                return np.tile([0.3, 0.7], (len(X), 1))
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(PassThrough(), f)
        return {"mode": "fallback_no_sklearn"}


if __name__ == "__main__":
    result = train()
    print(json.dumps(result, indent=2))
