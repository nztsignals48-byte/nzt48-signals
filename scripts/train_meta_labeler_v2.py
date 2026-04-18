"""Train the meta-labeler on existing fills data.

Produces data/models/meta_labeler.pkl. Uses sklearn GradientBoosting;
falls back to trivial majority classifier if sklearn unavailable.
"""
from __future__ import annotations

import json
import logging
import pickle
import sys
from pathlib import Path

import numpy as np


log = logging.getLogger("meta-train")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROOT = Path("/Users/rr/aegis-v5")
FILLS_BUS = ROOT / "data/bus/fills.closed.jsonl"
FILLS_DIR = ROOT / "data/fills"
MODEL_PATH = ROOT / "data/models/meta_labeler.pkl"


def gather_fills():
    fills = []
    if FILLS_BUS.exists():
        with open(FILLS_BUS) as f:
            for line in f:
                try:
                    fills.append(json.loads(line))
                except Exception:
                    continue
    if FILLS_DIR.exists():
        for p in FILLS_DIR.glob("*.jsonl"):
            try:
                with open(p) as f:
                    for line in f:
                        try:
                            fills.append(json.loads(line))
                        except Exception:
                            continue
            except Exception:
                continue
    return fills


def build_dataset(fills):
    """Extract features + labels. Label = 1 if realized_pnl_bps > 0."""
    X = []
    y = []
    regime_map = {"calm": 0, "trending": 1, "choppy": 2, "crisis": 3}
    session_map = {"us_session": 0, "lse_session": 1, "overnight": 2, "after_hours": 3}

    for f in fills:
        pnl = f.get("realized_pnl_bps")
        if pnl is None:
            pnl = f.get("realized_pnl_gbp", 0)
        if pnl is None:
            continue

        conv = float(f.get("confidence") or f.get("conviction") or f.get("conviction_score") or 0.5)
        shares = float(f.get("shares") or f.get("qty") or 100)
        kelly = float(f.get("deflated_kelly") or f.get("kelly_fraction") or 0.05)
        pf_prior = float(f.get("profit_factor_prior") or 1.0)
        hit_ratio = float(f.get("hit_ratio") or 0.5)
        lev = float(f.get("leverage") or 1.0)

        regime = f.get("regime", "calm")
        session = f.get("session", "us_session")

        X.append([
            conv,
            np.log1p(shares),
            kelly,
            pf_prior,
            hit_ratio,
            lev,
            regime_map.get(regime, 0),
            session_map.get(session, 0),
        ])
        y.append(1 if float(pnl) > 0 else 0)

    return np.array(X), np.array(y)


def train():
    fills = gather_fills()
    log.info("gathered %d fills", len(fills))

    X, y = build_dataset(fills)
    log.info("dataset: X=%s y mean=%.3f", X.shape, float(y.mean()) if y.size else 0)

    if X.shape[0] < 50:
        log.warning("too few fills (%d); writing fallback model", X.shape[0])
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Fallback: simple passthrough (always accept) — marker model
        class PassThrough:
            def predict_proba(self, X):
                return np.tile([0.3, 0.7], (len(X), 1))
            def predict(self, X):
                return np.ones(len(X), dtype=int)
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(PassThrough(), f)
        log.info("wrote fallback model to %s", MODEL_PATH)
        return {"mode": "fallback", "n_fills": X.shape[0]}

    try:
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import roc_auc_score

        X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=42)
        model = GradientBoostingClassifier(
            n_estimators=50, max_depth=3, random_state=42,
        )
        model.fit(X_tr, y_tr)

        # AUC
        if len(np.unique(y_te)) > 1:
            probs = model.predict_proba(X_te)[:, 1]
            auc = float(roc_auc_score(y_te, probs))
        else:
            auc = float("nan")

        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(model, f)
        log.info("trained GBM: AUC=%.3f, saved %s", auc, MODEL_PATH)
        return {"mode": "sklearn_gbm", "auc": auc, "n_train": X_tr.shape[0], "n_test": X_te.shape[0]}

    except ImportError:
        log.warning("sklearn unavailable — writing fallback model")
        class PassThrough:
            def predict_proba(self, X):
                return np.tile([0.3, 0.7], (len(X), 1))
            def predict(self, X):
                return np.ones(len(X), dtype=int)
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(PassThrough(), f)
        return {"mode": "fallback_no_sklearn"}


if __name__ == "__main__":
    result = train()
    print(json.dumps(result, indent=2))
