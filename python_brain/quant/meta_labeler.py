"""
Meta-Labeling Layer (López de Prado AFML Ch.3)

Binary classifier that filters signals: "given this primary signal, would it
actually be profitable after all costs?" Answers "yes" with calibrated probability.

Goal: reduce false positives by ~30-50% of low-quality signals before they
become orders. Model retrained nightly off actual fills in data/bus/fills.closed.jsonl.
"""
from __future__ import annotations

import os
import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class MetaLabelFeatures:
    strategy: str
    conviction: float
    gross_edge_bps: float
    spread_bps: float
    rvol: float
    vpin: float
    regime: str           # "calm" / "trending" / "choppy" / "crisis"
    session: str          # "us_session" / "lse_session" / "overnight"


class MetaLabeler:
    """Wraps a trained sklearn classifier for online use in sig2order."""

    MODEL_PATH = Path("/Users/rr/aegis-v5/data/models/meta_labeler.pkl")
    FEATURES_PATH = Path("/Users/rr/aegis-v5/data/models/meta_labeler_features.pkl")

    def __init__(self, accept_threshold: float = 0.40):
        self.accept_threshold = accept_threshold
        self._model = None
        self._features_schema = None
        self._loaded_mtime = 0
        self.load_model()

    def load_model(self):
        """Lazy-load model; reload if file changed."""
        try:
            if self.MODEL_PATH.exists():
                mtime = self.MODEL_PATH.stat().st_mtime
                if mtime > self._loaded_mtime:
                    with open(self.MODEL_PATH, "rb") as f:
                        self._model = pickle.load(f)
                    self._loaded_mtime = mtime
                    if self.FEATURES_PATH.exists():
                        with open(self.FEATURES_PATH, "rb") as f:
                            self._features_schema = pickle.load(f)
        except Exception:
            self._model = None

    def _features_to_vector(self, f: MetaLabelFeatures) -> np.ndarray:
        """Convert features to numeric vector."""
        regime_map = {"calm": 0, "trending": 1, "choppy": 2, "crisis": 3}
        session_map = {"us_session": 0, "lse_session": 1, "overnight": 2, "after_hours": 3}
        return np.array([
            float(f.conviction),
            float(f.gross_edge_bps),
            float(f.spread_bps),
            float(f.rvol),
            float(f.vpin),
            float(regime_map.get(f.regime, 0)),
            float(session_map.get(f.session, 0)),
        ]).reshape(1, -1)

    def predict_proba(self, features: MetaLabelFeatures) -> float:
        """Returns P(accept). If no model loaded, returns 0.5 (neutral)."""
        self.load_model()
        if self._model is None:
            return 0.5

        try:
            X = self._features_to_vector(features)
            if hasattr(self._model, "predict_proba"):
                proba = self._model.predict_proba(X)
                if proba.shape[1] >= 2:
                    return float(proba[0, 1])
                return float(proba[0, 0])
            return 0.5
        except Exception:
            return 0.5

    def should_accept(self, features: MetaLabelFeatures) -> tuple[bool, float]:
        """Returns (accept, probability)."""
        prob = self.predict_proba(features)
        return prob >= self.accept_threshold, prob


def apply_meta_label(model: MetaLabeler, features: MetaLabelFeatures) -> tuple[bool, float]:
    """Convenience wrapper."""
    return model.should_accept(features)


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        ml = MetaLabeler()
        feat = MetaLabelFeatures(
            strategy="test", conviction=0.75, gross_edge_bps=25,
            spread_bps=2, rvol=1.5, vpin=0.3, regime="calm", session="us_session",
        )
        accept, prob = ml.should_accept(feat)
        print(f"Accept: {accept}, Prob: {prob:.4f}")
        print(f"Model loaded: {ml._model is not None}")
        print("OK")
