"""GMM multi-factor regime classifier — Two-Sigma-style.

Fits Gaussian Mixture Model on 6 macro factors: SPY return, VIX level,
DGS10, DXY return, HYG return, cross-sectional dispersion.

Runs alongside BOCPD — publishes regime.current + regime.gmm_state.
Ouroboros refits nightly on latest 252-day window.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

try:
    from sklearn.mixture import GaussianMixture
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


@dataclass
class GMMRegimeState:
    regime_id: int
    regime_label: str
    posterior_probs: list
    feature_vector: list
    most_similar_regime: int


REGIME_LABELS = {
    0: "steady_bull",
    1: "choppy_neutral",
    2: "stressed_selloff",
    3: "crisis_panic",
}


class GMMRegimeClassifier:
    def __init__(
        self,
        n_regimes: int = 4,
        feature_names: list | None = None,
        random_state: int = 42,
    ):
        self.n_regimes = n_regimes
        self.feature_names = feature_names or [
            "spy_ret", "vix_level", "dgs10_change",
            "dxy_ret", "hyg_ret", "sector_dispersion",
        ]
        self.random_state = random_state
        self.gmm = None
        self.fitted = False
        self._means = None

    def fit(self, X: np.ndarray) -> None:
        """X: (n_days, n_features) historical factor matrix."""
        if not HAS_SKLEARN:
            # Fallback: cluster via simple k-means-like rule on vol
            self._means = np.array([
                [0.001, 15, 0.0, 0.0, 0.001, 0.01],   # steady
                [0.0, 20, 0.0, 0.0, 0.0, 0.015],      # choppy
                [-0.01, 30, 0.0, 0.002, -0.01, 0.03], # stressed
                [-0.03, 50, 0.01, 0.005, -0.03, 0.05],# crisis
            ])[:self.n_regimes]
            self.fitted = True
            return

        n, d = X.shape
        if n < self.n_regimes * 3:
            # Not enough data — use fallback means
            self._means = np.array([
                [0.001, 15, 0.0, 0.0, 0.001, 0.01],
                [0.0, 20, 0.0, 0.0, 0.0, 0.015],
                [-0.01, 30, 0.0, 0.002, -0.01, 0.03],
                [-0.03, 50, 0.01, 0.005, -0.03, 0.05],
            ])[:self.n_regimes, :d]
            self.fitted = True
            return

        self.gmm = GaussianMixture(
            n_components=self.n_regimes,
            covariance_type="full",
            random_state=self.random_state,
            reg_covar=1e-4,
        )
        self.gmm.fit(X)
        # Order regimes by "stress" — ascending mean vol (last feature assumed vol)
        if d >= 2:
            order = np.argsort(self.gmm.means_[:, 1])  # VIX-like column
        else:
            order = np.arange(self.n_regimes)
        self._order = order
        self.fitted = True

    def classify(self, features: list[float]) -> GMMRegimeState:
        if not self.fitted:
            return GMMRegimeState(0, "uninitialized", [1.0], list(features), 0)
        x = np.asarray(features, dtype=float).reshape(1, -1)

        if self.gmm is None:
            # Fallback classifier
            dists = np.linalg.norm(self._means - x, axis=1)
            rid = int(dists.argmin())
            probs = np.exp(-dists)
            probs = probs / probs.sum()
            return GMMRegimeState(
                regime_id=rid,
                regime_label=REGIME_LABELS.get(rid, f"regime_{rid}"),
                posterior_probs=probs.tolist(),
                feature_vector=features,
                most_similar_regime=rid,
            )

        probs = self.gmm.predict_proba(x)[0]
        # Map to ordered regime IDs
        if hasattr(self, "_order"):
            inv_order = {orig: pos for pos, orig in enumerate(self._order)}
            ordered_probs = np.zeros(self.n_regimes)
            for orig_id, p in enumerate(probs):
                ordered_probs[inv_order[orig_id]] = p
            probs = ordered_probs

        rid = int(probs.argmax())
        return GMMRegimeState(
            regime_id=rid,
            regime_label=REGIME_LABELS.get(rid, f"regime_{rid}"),
            posterior_probs=probs.tolist(),
            feature_vector=features,
            most_similar_regime=rid,
        )

    def size_multiplier(self, regime_id: int) -> float:
        """Regime-conditional size mult: same semantics as BOCPD."""
        return {
            0: 1.0,   # steady
            1: 0.6,   # choppy
            2: 0.35,  # stressed
            3: 0.15,  # crisis
        }.get(regime_id, 1.0)


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        rng = np.random.default_rng(42)
        # Synthesize 200 days of factor data with a regime shift midway
        n = 200
        calm = rng.normal(loc=[0.001, 15, 0, 0, 0.001, 0.01], scale=[0.01, 3, 0.02, 0.005, 0.01, 0.005], size=(100, 6))
        crisis = rng.normal(loc=[-0.02, 40, 0.01, 0.003, -0.02, 0.04], scale=[0.03, 10, 0.03, 0.015, 0.03, 0.02], size=(100, 6))
        X = np.vstack([calm, crisis])

        clf = GMMRegimeClassifier(n_regimes=4)
        clf.fit(X)

        test_calm = [0.001, 14, 0.001, 0.0, 0.001, 0.008]
        r = clf.classify(test_calm)
        print(f"Calm test -> regime={r.regime_id} ({r.regime_label}) probs={[f'{p:.2f}' for p in r.posterior_probs]}")

        test_crisis = [-0.035, 55, 0.02, 0.01, -0.04, 0.06]
        r = clf.classify(test_crisis)
        print(f"Crisis test -> regime={r.regime_id} ({r.regime_label}) probs={[f'{p:.2f}' for p in r.posterior_probs]}")
        print(f"Size mult crisis: {clf.size_multiplier(r.regime_id)}")
        print("OK")
