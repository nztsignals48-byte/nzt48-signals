"""Financial ML Under Data Scarcity — Book 183.

Techniques for machine learning with limited data, tailored to the
LSE leveraged ETP universe where we have ~2,000 daily bars (8-10 years)
and ~250,000 5-minute bars at best.

Key insight: the fundamental constraint is NOT compute, NOT model
architecture, NOT feature engineering — it is the number of independent
observations.  Autocorrelation further reduces effective sample size.

Components:
  - BootstrapAugmenter:        Block bootstrap preserving autocorrelation
  - BayesianLinearRegression:   Posterior predictive with uncertainty
  - FewShotClassifier:          Prototypical network / nearest centroid
  - DataScarcityPipeline:       Top-level assessment and recommendation

Bridge.py integration:
    try:
        from python_brain.ml.data_scarcity_toolkit import (
            BootstrapAugmenter, BayesianLinearRegression,
            FewShotClassifier, DataScarcityPipeline,
        )
        _dsp = DataScarcityPipeline()
    except ImportError:
        _dsp = None

Cross-references:
  - Book 29 (TCN Deep Learning)
  - Book 124 (Volatility Regime Clustering)
  - Book 144 (Conformal Prediction)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger("data_scarcity_toolkit")

__all__ = [
    "BootstrapAugmenter",
    "BayesianLinearRegression",
    "FewShotClassifier",
    "DataScarcityPipeline",
]

# ---------------------------------------------------------------------------
# Paths (production)
# ---------------------------------------------------------------------------
_DATA_DIR = "/app/data"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEFAULT_BLOCK_SIZE = 20      # 20-bar blocks to preserve autocorrelation
_DEFAULT_N_AUGMENTED = 5
_DEFAULT_ALPHA_PRIOR = 1.0    # prior precision for Bayesian LR
_DEFAULT_BETA_NOISE = 1.0     # noise precision for Bayesian LR
_DEFAULT_N_SUPPORT = 5        # few-shot support set size
_MIN_SAMPLES_FULL_ML = 500
_MIN_SAMPLES_BAYESIAN = 30
_MIN_SAMPLES_FEW_SHOT = 10


# ---------------------------------------------------------------------------
# BootstrapAugmenter
# ---------------------------------------------------------------------------
class BootstrapAugmenter:
    """Block bootstrap augmentation preserving serial autocorrelation.

    Standard i.i.d. bootstrap destroys temporal dependencies in
    financial time series.  Block bootstrap resamples contiguous blocks,
    preserving within-block autocorrelation structure.

    The block size should roughly match the autocorrelation decay length
    (typically 10-30 bars for daily data).
    """

    def __init__(self, block_size: int = _DEFAULT_BLOCK_SIZE) -> None:
        self.block_size = block_size
        log.info("BootstrapAugmenter init | block_size=%d", block_size)

    def augment(
        self,
        data: np.ndarray,
        n_augmented: int = _DEFAULT_N_AUGMENTED,
        rng: Optional[np.random.RandomState] = None,
    ) -> np.ndarray:
        """Generate augmented datasets via block bootstrap.

        Args:
            data:        (T,) or (T, D) original time series.
            n_augmented: number of augmented copies to generate.
            rng:         optional random state for reproducibility.

        Returns:
            (n_augmented * T, D) or (n_augmented * T,) stacked augmented data.
            Each block of T rows is one augmented copy.
        """
        if rng is None:
            rng = np.random.RandomState()

        T = data.shape[0]
        if T < self.block_size:
            log.warning(
                "augment: data length %d < block_size %d — returning copies",
                T, self.block_size,
            )
            if data.ndim == 1:
                return np.tile(data, n_augmented)
            return np.tile(data, (n_augmented, 1))

        augmented = []
        for _ in range(n_augmented):
            sample = self._block_bootstrap(data, self.block_size, rng)
            augmented.append(sample)

        result = np.concatenate(augmented, axis=0)
        log.debug(
            "augment | T=%d n_aug=%d block=%d -> shape=%s",
            T, n_augmented, self.block_size, result.shape,
        )
        return result

    @staticmethod
    def _block_bootstrap(
        data: np.ndarray,
        block_size: int,
        rng: np.random.RandomState,
    ) -> np.ndarray:
        """Generate one block-bootstrapped sample.

        Randomly selects starting indices and concatenates contiguous
        blocks until the target length is reached.

        Args:
            data:       (T,) or (T, D) original data.
            block_size: size of each resampled block.
            rng:        random state.

        Returns:
            Bootstrapped sample of same length as original.
        """
        T = data.shape[0]
        n_blocks = int(math.ceil(T / block_size))

        # Random block start indices
        max_start = T - block_size
        if max_start < 1:
            max_start = 1
        starts = rng.randint(0, max_start, size=n_blocks)

        blocks = []
        for s in starts:
            end = min(s + block_size, T)
            blocks.append(data[s:end])

        bootstrapped = np.concatenate(blocks, axis=0)

        # Trim to original length
        if data.ndim == 1:
            return bootstrapped[:T]
        return bootstrapped[:T, :]


# ---------------------------------------------------------------------------
# BayesianLinearRegression
# ---------------------------------------------------------------------------
class BayesianLinearRegression:
    """Bayesian Linear Regression with conjugate normal-inverse-gamma prior.

    Provides posterior predictive distributions with proper uncertainty
    quantification.  Critical for small datasets where point estimates
    from OLS are unreliable.

    Prior:
      w ~ N(0, alpha^{-1} I)     (weight prior)
      y | X, w ~ N(Xw, beta^{-1})  (noise model)

    Posterior:
      w | X, y ~ N(m_N, S_N)
      m_N = beta * S_N @ X^T @ y
      S_N = (alpha * I + beta * X^T @ X)^{-1}
    """

    def __init__(
        self,
        alpha: float = _DEFAULT_ALPHA_PRIOR,
        beta: float = _DEFAULT_BETA_NOISE,
    ) -> None:
        """Initialise with prior parameters.

        Args:
            alpha: prior precision (regularisation strength).
                   Higher alpha = stronger prior = more shrinkage.
            beta:  noise precision (1/variance of noise).
                   Higher beta = less noise = more confident in data.
        """
        self.alpha = alpha
        self.beta = beta
        self._m_N: Optional[np.ndarray] = None  # posterior mean
        self._S_N: Optional[np.ndarray] = None   # posterior covariance
        self._fitted = False
        log.info(
            "BayesianLinearRegression init | alpha=%.2f beta=%.2f",
            alpha, beta,
        )

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        """Compute posterior distribution over weights.

        Args:
            X: (N, D) feature matrix.
            y: (N,) target vector.
        """
        N, D = X.shape
        if N == 0 or D == 0:
            log.warning("fit: empty data (N=%d, D=%d)", N, D)
            return

        # Posterior covariance: S_N = (alpha * I + beta * X^T X)^{-1}
        XtX = X.T @ X
        S_N_inv = self.alpha * np.eye(D) + self.beta * XtX

        try:
            self._S_N = np.linalg.inv(S_N_inv)
        except np.linalg.LinAlgError:
            log.warning("fit: singular matrix — adding jitter")
            self._S_N = np.linalg.inv(S_N_inv + 1e-6 * np.eye(D))

        # Posterior mean: m_N = beta * S_N @ X^T @ y
        self._m_N = self.beta * self._S_N @ X.T @ y
        self._fitted = True

        log.debug(
            "fit | N=%d D=%d weight_norm=%.4f",
            N, D, float(np.linalg.norm(self._m_N)),
        )

    def predict(self, X: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Predict with uncertainty (mean and variance).

        Args:
            X: (M, D) test features.

        Returns:
            (mean, variance) where mean is (M,) and variance is (M,).
        """
        if not self._fitted or self._m_N is None or self._S_N is None:
            log.warning("predict: model not fitted")
            M = X.shape[0]
            return (np.zeros(M), np.ones(M))

        # Predictive mean
        mean = X @ self._m_N

        # Predictive variance: 1/beta + x^T S_N x
        # Compute per-sample variance
        # variance_i = 1/beta + X[i, :] @ S_N @ X[i, :].T
        XS = X @ self._S_N
        variance = (1.0 / self.beta) + np.sum(XS * X, axis=1)
        variance = np.maximum(variance, 1e-12)

        log.debug(
            "predict | M=%d mean_pred=%.4f mean_var=%.4f",
            X.shape[0], float(np.mean(mean)), float(np.mean(variance)),
        )
        return (mean, variance)

    def posterior_predictive(self, X: np.ndarray) -> Dict[str, Any]:
        """Full posterior predictive with credible intervals.

        Args:
            X: (M, D) test features.

        Returns:
            Dict with mean, variance, ci_lower, ci_upper (95% CI).
        """
        mean, variance = self.predict(X)
        std = np.sqrt(variance)

        # 95% credible interval (approx Normal)
        z_95 = 1.96
        ci_lower = mean - z_95 * std
        ci_upper = mean + z_95 * std

        return {
            "mean": mean,
            "variance": variance,
            "std": std,
            "ci_lower": ci_lower,
            "ci_upper": ci_upper,
            "ci_width": ci_upper - ci_lower,
        }


# ---------------------------------------------------------------------------
# FewShotClassifier
# ---------------------------------------------------------------------------
class FewShotClassifier:
    """Few-shot classifier using prototypical networks (nearest centroid).

    When we only have a handful of examples per class (e.g. 5 examples
    of each regime type), standard classifiers fail.  Prototypical
    networks compute a centroid per class and classify by nearest
    centroid in embedding space.

    This is the simplest effective few-shot method and requires no
    neural network — just the embedding (which can be hand-crafted
    features for financial data).
    """

    def __init__(self, n_support: int = _DEFAULT_N_SUPPORT) -> None:
        """Initialise the few-shot classifier.

        Args:
            n_support: expected number of support examples per class.
        """
        self.n_support = n_support
        self._prototypes: Optional[np.ndarray] = None
        self._classes: Optional[np.ndarray] = None
        self._fitted = False
        log.info("FewShotClassifier init | n_support=%d", n_support)

    def fit(
        self,
        support_set_X: np.ndarray,
        support_set_y: np.ndarray,
    ) -> None:
        """Compute class prototypes from support set.

        Args:
            support_set_X: (N, D) support set features.
            support_set_y: (N,) support set labels (integer-encoded).
        """
        if len(support_set_X) == 0:
            log.warning("fit: empty support set")
            return

        self._classes = np.unique(support_set_y)
        n_classes = len(self._classes)
        D = support_set_X.shape[1]

        self._prototypes = np.zeros((n_classes, D))
        for i, cls in enumerate(self._classes):
            mask = support_set_y == cls
            if np.sum(mask) == 0:
                continue
            self._prototypes[i, :] = np.mean(support_set_X[mask], axis=0)

        self._fitted = True
        log.debug(
            "fit | n_classes=%d n_support=%d D=%d",
            n_classes, len(support_set_X), D,
        )

    def predict(self, query_X: np.ndarray) -> np.ndarray:
        """Classify query points by nearest prototype.

        Args:
            query_X: (M, D) query features.

        Returns:
            (M,) predicted class labels.
        """
        if not self._fitted or self._prototypes is None or self._classes is None:
            log.warning("predict: model not fitted")
            return np.zeros(query_X.shape[0], dtype=int)

        M = query_X.shape[0]
        n_classes = len(self._classes)

        # Compute Euclidean distance to each prototype
        # distances: (M, n_classes)
        distances = np.zeros((M, n_classes))
        for c in range(n_classes):
            diff = query_X - self._prototypes[c]
            distances[:, c] = np.sqrt(np.sum(diff ** 2, axis=1))

        # Nearest centroid
        nearest = np.argmin(distances, axis=1)
        predictions = self._classes[nearest]

        log.debug(
            "predict | M=%d class_distribution=%s",
            M, {int(c): int(np.sum(predictions == c)) for c in self._classes},
        )
        return predictions

    def predict_proba(self, query_X: np.ndarray) -> np.ndarray:
        """Predict class probabilities via softmax of negative distances.

        Args:
            query_X: (M, D) query features.

        Returns:
            (M, n_classes) class probability matrix.
        """
        if not self._fitted or self._prototypes is None or self._classes is None:
            log.warning("predict_proba: model not fitted")
            n_classes = 1
            return np.ones((query_X.shape[0], n_classes)) / n_classes

        M = query_X.shape[0]
        n_classes = len(self._classes)

        distances = np.zeros((M, n_classes))
        for c in range(n_classes):
            diff = query_X - self._prototypes[c]
            distances[:, c] = np.sum(diff ** 2, axis=1)

        # Softmax over negative squared distances
        neg_dist = -distances
        # Numerical stability: subtract max per row
        neg_dist -= np.max(neg_dist, axis=1, keepdims=True)
        exp_nd = np.exp(neg_dist)
        proba = exp_nd / (np.sum(exp_nd, axis=1, keepdims=True) + 1e-12)

        return proba


# ---------------------------------------------------------------------------
# DataScarcityPipeline
# ---------------------------------------------------------------------------
class DataScarcityPipeline:
    """Top-level pipeline for ML under data scarcity.

    Assesses whether the available data is sufficient for the
    intended model class and recommends the appropriate approach:
      - BAYESIAN:  When N < 100 — use Bayesian methods with proper priors
      - FEW_SHOT:  When N < 30 — use few-shot / nearest centroid
      - AUGMENT:   When N < 500 — augment first, then train
      - FULL_ML:   When N >= 500 — standard ML is feasible
    """

    def __init__(self) -> None:
        self.augmenter = BootstrapAugmenter()
        self.bayesian_lr = BayesianLinearRegression()
        self.few_shot = FewShotClassifier()
        log.info("DataScarcityPipeline init")

    def assess_data_sufficiency(
        self,
        n_samples: int,
        n_features: int,
    ) -> Dict[str, Any]:
        """Assess whether data is sufficient for ML.

        Uses the effective sample ratio (n_samples / n_features) and
        known rules-of-thumb from statistical learning theory.

        Args:
            n_samples:  number of training samples.
            n_features: number of input features.

        Returns:
            Dict with effective_ratio, recommendation, details.
        """
        if n_features <= 0:
            log.warning("assess_data_sufficiency: n_features=%d", n_features)
            return {
                "effective_ratio": 0.0,
                "recommendation": "INSUFFICIENT_DATA",
                "n_samples": n_samples,
                "n_features": n_features,
                "sufficient_for_linear": False,
                "sufficient_for_tree": False,
                "sufficient_for_nn": False,
                "details": "No features specified.",
            }

        ratio = n_samples / n_features

        # Rules of thumb (from statistical learning literature):
        # Linear models:  N/D >= 10
        # Tree ensembles:  N/D >= 20
        # Neural networks: N/D >= 50 (shallow), N/D >= 200 (deep)
        sufficient_linear = ratio >= 10
        sufficient_tree = ratio >= 20
        sufficient_nn = ratio >= 50

        # Recommendation
        approach = self.recommend_approach(n_samples)

        # Estimated effective sample size (rough: assume 30% autocorrelation reduction)
        n_effective = int(n_samples * 0.7)

        details_parts = []
        if not sufficient_linear:
            details_parts.append(
                f"N/D={ratio:.1f} < 10: even linear models may overfit."
            )
        if not sufficient_tree and sufficient_linear:
            details_parts.append(
                f"N/D={ratio:.1f}: linear OK, tree ensembles risky."
            )
        if sufficient_tree and not sufficient_nn:
            details_parts.append(
                f"N/D={ratio:.1f}: trees OK, neural networks risky."
            )
        if sufficient_nn:
            details_parts.append(
                f"N/D={ratio:.1f}: all model classes feasible."
            )

        result = {
            "effective_ratio": ratio,
            "n_effective_est": n_effective,
            "recommendation": approach,
            "n_samples": n_samples,
            "n_features": n_features,
            "sufficient_for_linear": sufficient_linear,
            "sufficient_for_tree": sufficient_tree,
            "sufficient_for_nn": sufficient_nn,
            "details": " ".join(details_parts) if details_parts else "Assessment complete.",
        }

        log.info(
            "assess | N=%d D=%d ratio=%.1f => %s",
            n_samples, n_features, ratio, approach,
        )
        return result

    @staticmethod
    def recommend_approach(n_samples: int) -> str:
        """Recommend the ML approach based on sample count alone.

        Args:
            n_samples: number of training samples.

        Returns:
            One of: FEW_SHOT, BAYESIAN, AUGMENT, FULL_ML.
        """
        if n_samples < _MIN_SAMPLES_FEW_SHOT:
            return "FEW_SHOT"
        elif n_samples < _MIN_SAMPLES_BAYESIAN:
            return "FEW_SHOT"
        elif n_samples < 100:
            return "BAYESIAN"
        elif n_samples < _MIN_SAMPLES_FULL_ML:
            return "AUGMENT"
        else:
            return "FULL_ML"

    def run_pipeline(
        self,
        X: np.ndarray,
        y: np.ndarray,
        X_test: Optional[np.ndarray] = None,
    ) -> Dict[str, Any]:
        """Run the appropriate ML pipeline based on data availability.

        Automatically selects between Bayesian LR, few-shot, or
        augmented training based on sample size.

        Args:
            X:      (N, D) training features.
            y:      (N,) training targets.
            X_test: (M, D) test features (optional).

        Returns:
            Dict with approach, predictions, and uncertainty.
        """
        N, D = X.shape
        assessment = self.assess_data_sufficiency(N, D)
        approach = assessment["recommendation"]

        if X_test is None:
            X_test = X

        if approach == "FEW_SHOT":
            # Discretise targets for classification
            y_cls = self._discretise_targets(y)
            self.few_shot.fit(X, y_cls)
            preds = self.few_shot.predict(X_test)
            proba = self.few_shot.predict_proba(X_test)
            return {
                "approach": "FEW_SHOT",
                "predictions": preds,
                "probabilities": proba,
                "assessment": assessment,
            }

        elif approach == "BAYESIAN":
            self.bayesian_lr.fit(X, y)
            posterior = self.bayesian_lr.posterior_predictive(X_test)
            return {
                "approach": "BAYESIAN",
                "predictions": posterior["mean"],
                "uncertainty": posterior["std"],
                "ci_lower": posterior["ci_lower"],
                "ci_upper": posterior["ci_upper"],
                "assessment": assessment,
            }

        elif approach == "AUGMENT":
            # Augment training data, then fit Bayesian LR
            X_aug = self.augmenter.augment(X, n_augmented=3)
            y_aug = self.augmenter.augment(
                y.reshape(-1, 1) if y.ndim == 1 else y,
                n_augmented=3,
            )
            if y_aug.ndim > 1:
                y_aug = y_aug.ravel()
            # Trim to match (augmenter may round)
            min_len = min(X_aug.shape[0], len(y_aug))
            X_aug = X_aug[:min_len]
            y_aug = y_aug[:min_len]

            self.bayesian_lr.fit(X_aug, y_aug)
            posterior = self.bayesian_lr.posterior_predictive(X_test)
            return {
                "approach": "AUGMENT",
                "predictions": posterior["mean"],
                "uncertainty": posterior["std"],
                "augmented_samples": min_len,
                "assessment": assessment,
            }

        else:  # FULL_ML
            # Default to Bayesian LR even for full ML (safe choice)
            self.bayesian_lr.fit(X, y)
            posterior = self.bayesian_lr.posterior_predictive(X_test)
            return {
                "approach": "FULL_ML",
                "predictions": posterior["mean"],
                "uncertainty": posterior["std"],
                "assessment": assessment,
            }

    @staticmethod
    def _discretise_targets(y: np.ndarray, n_bins: int = 3) -> np.ndarray:
        """Discretise continuous targets into class labels.

        Uses quantile-based binning to create balanced classes.

        Args:
            y:      (N,) continuous targets.
            n_bins: number of discrete classes.

        Returns:
            (N,) integer class labels.
        """
        quantiles = np.linspace(0, 100, n_bins + 1)
        edges = np.percentile(y, quantiles)
        labels = np.digitize(y, edges[1:-1])
        return labels.astype(int)
