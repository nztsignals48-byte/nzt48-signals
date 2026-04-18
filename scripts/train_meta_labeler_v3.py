"""Train meta-labeler from fills.closed.jsonl (nested payload format).

Extracts features from wrapped messages with schema {subject, schema_version,
payload, ts_ns}. Payload contains entry/exit + realized_pnl_bps + mae/mfe.

Writes data/models/meta_labeler.pkl usable by python_brain.quant.meta_labeler.
"""
from __future__ import annotations

import json
import logging
import pickle
import sys
from pathlib import Path

import numpy as np


log = logging.getLogger("meta-train-v3")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROOT = Path("/Users/rr/aegis-v5")
FILLS_BUS = ROOT / "data/bus/fills.closed.jsonl"
MODEL_PATH = ROOT / "data/models/meta_labeler.pkl"


def gather_fills() -> list[dict]:
    fills = []
    if not FILLS_BUS.exists():
        return fills
    with open(FILLS_BUS) as f:
        for line in f:
            try:
                wrapper = json.loads(line)
            except Exception:
                continue
            payload = wrapper.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    continue
            if isinstance(payload, dict):
                fills.append(payload)
    return fills


def build_dataset(fills: list[dict]):
    """Extract features + labels from fills payload."""
    regime_map = {"calm": 0, "steady": 0, "trending": 1, "choppy": 2, "crisis": 3, "stressed": 3}
    X, y = [], []

    for f in fills:
        pnl = f.get("realized_pnl_bps")
        if pnl is None:
            continue

        # Features
        entry_price = f.get("entry_price", 100)
        exit_price = f.get("exit_price", 100)
        size = f.get("size_shares", 1)
        spread = f.get("spread_cost_bps", 5)
        slippage = f.get("slippage_bps_vs_arrival", 0)
        mae = f.get("mae_bps", 0)
        mfe = f.get("mfe_bps", 0)
        duration_ns = f.get("exit_timestamp_ns", 0) - f.get("entry_timestamp_ns", 0)
        duration_s = duration_ns / 1e9 if duration_ns > 0 else 0.0
        regime_entry = f.get("regime_at_entry", "calm")
        if isinstance(regime_entry, list):
            # Probability vector — take argmax key if possible, else default
            regime_entry = "calm"
        if not isinstance(regime_entry, str):
            regime_entry = "calm"
        commission = f.get("commission_abs", 0)

        exit_reason = f.get("exit_reason", "unknown")
        reason_code = {"ProfitTarget": 0, "StopLoss": 1, "ChandelierTrail": 2,
                       "ExhaustionRSI": 3, "VolumeClimax": 4,
                       "FixedDayExpiry": 5, "EventWindow": 6, "unknown": 7}.get(exit_reason, 7)

        X.append([
            np.log1p(abs(size)),
            spread,
            slippage,
            mae,
            mfe,
            mfe + abs(mae),  # range
            duration_s / 60,  # minutes
            regime_map.get(regime_entry, 0),
            commission,
            reason_code,
        ])
        y.append(1 if float(pnl) > 0 else 0)

    return np.array(X), np.array(y)


def train():
    fills = gather_fills()
    log.info("gathered %d fills", len(fills))

    X, y = build_dataset(fills)
    log.info("dataset: X=%s", X.shape)
    if y.size:
        log.info("  y mean=%.3f (wins=%d losses=%d)",
                 float(y.mean()), int(y.sum()), int(len(y) - y.sum()))

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)

    if X.shape[0] < 30 or len(np.unique(y)) < 2:
        log.warning("insufficient data for training — writing fallback passthrough model")
        class PassThrough:
            def predict_proba(self, X):
                return np.tile([0.3, 0.7], (len(X), 1))
            def predict(self, X):
                return np.ones(len(X), dtype=int)
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(PassThrough(), f)
        return {"mode": "fallback", "n_fills": X.shape[0]}

    try:
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import roc_auc_score

        X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
        model = GradientBoostingClassifier(n_estimators=100, max_depth=3, random_state=42)
        model.fit(X_tr, y_tr)

        probs_te = model.predict_proba(X_te)[:, 1]
        auc = float(roc_auc_score(y_te, probs_te))

        with open(MODEL_PATH, "wb") as f:
            pickle.dump(model, f)
        log.info("GBM trained: AUC=%.4f size=%s", auc, MODEL_PATH.stat().st_size)
        return {"mode": "sklearn_gbm", "auc": auc, "n_train": len(y_tr), "n_test": len(y_te)}

    except ImportError as e:
        log.warning("sklearn unavailable: %s — fallback model", e)
        class PassThrough:
            def predict_proba(self, X):
                return np.tile([0.3, 0.7], (len(X), 1))
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(PassThrough(), f)
        return {"mode": "fallback_no_sklearn"}


if __name__ == "__main__":
    result = train()
    print(json.dumps(result, indent=2))
