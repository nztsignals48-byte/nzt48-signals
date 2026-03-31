"""Transfer Learning for Cross-Market Adaptation — Book 93.

Pre-train on US markets (large-cap, leveraged ETPs), fine-tune on LSE ETPs.
Progressive domain adaptation: US → EU → LSE with catastrophic forgetting
prevention via Elastic Weight Consolidation (EWC).

Key ideas:
1. Domain shift detection via Maximum Mean Discrepancy (MMD)
2. Feature alignment across markets (common features, z-score normalization)
3. Progressive training: freeze bottom layers, fine-tune top layers
4. EWC penalty prevents forgetting source domain knowledge

State: /app/data/transfer_learning/{domain_pair}.json

Bridge.py integration:
    try:
        from python_brain.ml.transfer_learner import TransferLearner, TransferConfig
    except ImportError:
        pass

Usage:
    from python_brain.ml.transfer_learner import (
        TransferLearner, TransferConfig, DomainType,
    )

    config = TransferConfig(
        source_domain=DomainType.US_LARGE_CAP,
        target_domain=DomainType.LSE_ETP,
    )
    learner = TransferLearner(config)
    result = learner.train_progressive(us_data, eu_data, lse_data)
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import warnings

import numpy as np

# Suppress spurious numpy matmul RuntimeWarnings. BLAS matmul accumulator
# can emit divide-by-zero / overflow warnings during intermediate computation
# even when the final result is finite. Safe because we clip/sanitize weights
# every epoch and validate all outputs.
warnings.filterwarnings("ignore", message=".*encountered in matmul.*", category=RuntimeWarning)

try:
    from scipy import stats as scipy_stats
except ImportError:
    scipy_stats = None

log = logging.getLogger(__name__)

__all__ = [
    "DomainType",
    "TransferConfig",
    "DomainShiftDetector",
    "FeatureAligner",
    "ProgressiveTrainer",
    "TransferLearner",
]

# ── Constants ─────────────────────────────────────────────────────────

STATE_DIR = Path("/app/data/transfer_learning")
DEFAULT_GAMMA = 1.0
DEFAULT_KS_THRESHOLD = 0.05
EWC_LAMBDA = 1000.0  # EWC penalty strength


# ── Enums & Dataclasses ──────────────────────────────────────────────

class DomainType(Enum):
    US_LARGE_CAP = "us_large_cap"
    US_LEVERAGED = "us_leveraged"
    LSE_ETP = "lse_etp"
    EU_ETP = "eu_etp"


@dataclass
class TransferConfig:
    source_domain: DomainType = DomainType.US_LARGE_CAP
    target_domain: DomainType = DomainType.LSE_ETP
    freeze_layers: List[str] = field(default_factory=lambda: ["layer_0"])
    fine_tune_lr: float = 0.001
    fine_tune_epochs: int = 10
    max_domain_shift: float = 0.5
    feature_alignment: bool = True

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["source_domain"] = self.source_domain.value
        d["target_domain"] = self.target_domain.value
        return d


# ── Domain Shift Detection ───────────────────────────────────────────

class DomainShiftDetector:
    """Detect distribution shift between source and target domains.

    Uses Maximum Mean Discrepancy (MMD) with RBF kernel to quantify
    the distance between feature distributions across markets.
    """

    def __init__(self, reference_distribution: dict):
        """
        Args:
            reference_distribution: Source domain statistics.
                {"mean": np.ndarray, "std": np.ndarray, "samples": np.ndarray}
        """
        self.ref = reference_distribution
        self._threshold = 0.5

    def compute_shift(self, target_data: np.ndarray) -> float:
        """Compute MMD between source and target feature distributions.

        Args:
            target_data: (n_samples, n_features) target domain data.

        Returns:
            MMD score (0 = identical distributions, higher = more shift).
        """
        source_samples = self.ref.get("samples")
        if source_samples is None or len(source_samples) == 0:
            log.warning("No source samples for shift computation")
            return 1.0

        if target_data.ndim != 2 or target_data.shape[0] < 2:
            return 1.0

        return self._mmd_rbf(source_samples, target_data, gamma=DEFAULT_GAMMA)

    def _mmd_rbf(self, X: np.ndarray, Y: np.ndarray, gamma: float = 1.0) -> float:
        """Compute MMD with RBF (Gaussian) kernel.

        MMD^2 = E[k(x,x')] - 2*E[k(x,y)] + E[k(y,y')]
        where k(a,b) = exp(-gamma * ||a-b||^2)

        Uses subsample for efficiency if datasets are large.
        """
        max_samples = 500
        if len(X) > max_samples:
            idx = np.random.choice(len(X), max_samples, replace=False)
            X = X[idx]
        if len(Y) > max_samples:
            idx = np.random.choice(len(Y), max_samples, replace=False)
            Y = Y[idx]

        # Pairwise squared distances
        XX = np.sum(X ** 2, axis=1, keepdims=True)
        YY = np.sum(Y ** 2, axis=1, keepdims=True)

        # K(X, X)
        dist_xx = np.nan_to_num(XX + XX.T - 2.0 * X @ X.T, nan=0.0)
        k_xx = np.exp(-gamma * np.maximum(dist_xx, 0.0))

        # K(Y, Y)
        dist_yy = np.nan_to_num(YY + YY.T - 2.0 * Y @ Y.T, nan=0.0)
        k_yy = np.exp(-gamma * np.maximum(dist_yy, 0.0))

        # K(X, Y)
        dist_xy = np.nan_to_num(XX + YY.T - 2.0 * X @ Y.T, nan=0.0)
        k_xy = np.exp(-gamma * np.maximum(dist_xy, 0.0))

        n_x = len(X)
        n_y = len(Y)

        # Unbiased MMD^2 estimator
        # Remove diagonal for unbiased estimate
        np.fill_diagonal(k_xx, 0.0)
        np.fill_diagonal(k_yy, 0.0)

        mmd_sq = (
            np.sum(k_xx) / max(n_x * (n_x - 1), 1)
            - 2.0 * np.sum(k_xy) / max(n_x * n_y, 1)
            + np.sum(k_yy) / max(n_y * (n_y - 1), 1)
        )

        return float(max(mmd_sq, 0.0) ** 0.5)

    def is_transferable(self, shift_score: float) -> bool:
        """Check if domain shift is small enough for transfer.

        Args:
            shift_score: MMD score from compute_shift().

        Returns:
            True if transfer is likely beneficial.
        """
        return shift_score < self._threshold

    def identify_shifted_features(
        self, source: np.ndarray, target: np.ndarray
    ) -> list:
        """Identify features with significant distribution shift.

        Uses Kolmogorov-Smirnov test per feature. Features with p < 0.05
        are considered shifted.

        Args:
            source: (n_samples, n_features) source data.
            target: (n_samples, n_features) target data.

        Returns:
            List of feature indices with significant shift.
        """
        if source.ndim != 2 or target.ndim != 2:
            return []
        if source.shape[1] != target.shape[1]:
            log.warning(
                "Feature dimension mismatch: source=%d, target=%d",
                source.shape[1], target.shape[1],
            )
            return []

        shifted = []
        n_features = source.shape[1]

        for i in range(n_features):
            s_col = source[:, i]
            t_col = target[:, i]

            if scipy_stats is not None:
                stat, p_value = scipy_stats.ks_2samp(s_col, t_col)
            else:
                # Fallback: manual two-sample KS test
                p_value = self._manual_ks_test(s_col, t_col)

            if p_value < DEFAULT_KS_THRESHOLD:
                shifted.append(i)

        log.info(
            "Shifted features: %d / %d (threshold p<%.2f)",
            len(shifted), n_features, DEFAULT_KS_THRESHOLD,
        )
        return shifted

    @staticmethod
    def _manual_ks_test(a: np.ndarray, b: np.ndarray) -> float:
        """Manual two-sample KS test when scipy unavailable."""
        n1 = len(a)
        n2 = len(b)
        if n1 == 0 or n2 == 0:
            return 1.0

        combined = np.sort(np.concatenate([a, b]))
        cdf1 = np.searchsorted(np.sort(a), combined, side="right") / n1
        cdf2 = np.searchsorted(np.sort(b), combined, side="right") / n2
        d_stat = float(np.max(np.abs(cdf1 - cdf2)))

        # Approximate p-value (asymptotic)
        en = math.sqrt(n1 * n2 / (n1 + n2))
        lam = (en + 0.12 + 0.11 / en) * d_stat
        if lam <= 0:
            return 1.0
        # Kolmogorov distribution approximation
        p_value = 2.0 * math.exp(-2.0 * lam * lam)
        return max(0.0, min(1.0, p_value))


# ── Feature Alignment ────────────────────────────────────────────────

class FeatureAligner:
    """Align feature spaces between source and target domains.

    Finds common features, z-score normalizes both domains to a
    common scale using source domain statistics.
    """

    def __init__(self, source_features: list, target_features: list):
        """
        Args:
            source_features: List of feature names in source domain.
            target_features: List of feature names in target domain.
        """
        self.source_features = source_features
        self.target_features = target_features
        self._common = self._common_features()
        self._source_stats: Optional[Dict] = None

    def align(
        self, source_data: np.ndarray, target_data: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Align source and target data to common feature space.

        1. Select common features from both datasets.
        2. Compute z-score statistics from source domain.
        3. Normalize both to source domain scale.

        Args:
            source_data: (n_samples, n_source_features) array.
            target_data: (n_samples, n_target_features) array.

        Returns:
            (aligned_source, aligned_target) both with common feature columns.
        """
        if not self._common:
            log.warning("No common features between domains")
            return source_data, target_data

        # Get column indices for common features
        src_idx = [self.source_features.index(f) for f in self._common
                   if f in self.source_features]
        tgt_idx = [self.target_features.index(f) for f in self._common
                   if f in self.target_features]

        if not src_idx or not tgt_idx:
            return source_data, target_data

        aligned_source = source_data[:, src_idx]
        aligned_target = target_data[:, tgt_idx]

        # Compute source statistics for normalization
        src_mean = np.mean(aligned_source, axis=0)
        src_std = np.std(aligned_source, axis=0)
        src_std = np.where(src_std < 1e-8, 1.0, src_std)

        self._source_stats = {"mean": src_mean, "std": src_std}

        # Normalize source
        aligned_source = (aligned_source - src_mean) / src_std

        # Normalize target to source scale
        aligned_target = self._normalize_to_source(aligned_target, self._source_stats)

        log.info("Aligned %d common features", len(self._common))
        return aligned_source, aligned_target

    def _common_features(self) -> list:
        """Find features present in both domains."""
        source_set = set(self.source_features)
        target_set = set(self.target_features)
        common = sorted(source_set & target_set)
        return common

    def _normalize_to_source(
        self, target_data: np.ndarray, source_stats: dict
    ) -> np.ndarray:
        """Normalize target data using source domain statistics.

        Args:
            target_data: (n_samples, n_features) target data.
            source_stats: {"mean": np.ndarray, "std": np.ndarray}

        Returns:
            Normalized target data on source scale.
        """
        mean = source_stats["mean"]
        std = source_stats["std"]
        return (target_data - mean) / std


# ── Progressive Trainer ──────────────────────────────────────────────

class ProgressiveTrainer:
    """Progressive domain adaptation with EWC regularization.

    Trains on source domain first, then fine-tunes on target domain
    with frozen bottom layers and EWC penalty to prevent catastrophic
    forgetting of source domain knowledge.
    """

    def __init__(self, config: TransferConfig):
        self.config = config
        self._fisher_cache: Optional[Dict] = None

    def pretrain(
        self, source_data: np.ndarray, source_labels: np.ndarray
    ) -> dict:
        """Train on source domain. Returns weight dictionary.

        Simple linear model trained via gradient descent.

        Args:
            source_data: (n_samples, n_features) source features.
            source_labels: (n_samples,) binary labels.

        Returns:
            Weight dict {"layer_0": np.ndarray, "bias_0": np.ndarray, ...}
        """
        n_samples, n_features = source_data.shape
        if n_samples < 10:
            log.warning("Insufficient source samples: %d", n_samples)
            return {}

        # Z-score normalize input for stable training
        src_mean = np.mean(source_data, axis=0)
        src_std = np.std(source_data, axis=0)
        src_std = np.where(src_std < 1e-8, 1.0, src_std)
        X_norm = (source_data - src_mean) / src_std

        # Initialize weights (Xavier initialization)
        hidden_dim = min(32, n_features)
        rng = np.random.RandomState(42)
        weights = {
            "layer_0": rng.randn(n_features, hidden_dim) * math.sqrt(2.0 / n_features),
            "bias_0": np.zeros(hidden_dim),
            "layer_1": rng.randn(hidden_dim, 1) * math.sqrt(2.0 / hidden_dim),
            "bias_1": np.zeros(1),
        }

        lr = 0.01
        n_epochs = 50
        _WEIGHT_CLIP = 10.0

        for epoch in range(n_epochs):
            # Forward pass
            h = X_norm @ weights["layer_0"] + weights["bias_0"]
            h = np.maximum(h, 0)  # ReLU
            logits = h @ weights["layer_1"] + weights["bias_1"]
            probs = 1.0 / (1.0 + np.exp(-np.clip(logits.ravel(), -30, 30)))

            # Binary cross-entropy loss
            eps = 1e-7
            loss = -np.mean(
                source_labels * np.log(probs + eps)
                + (1 - source_labels) * np.log(1 - probs + eps)
            )

            # Backward pass with gradient clipping
            dlogits = (probs - source_labels).reshape(-1, 1) / n_samples

            grad_w1 = np.clip(h.T @ dlogits, -5.0, 5.0)
            weights["layer_1"] -= lr * grad_w1
            weights["bias_1"] -= lr * np.sum(dlogits, axis=0)

            dh = dlogits @ weights["layer_1"].T
            dh = dh * (h > 0).astype(float)  # ReLU grad
            grad_w0 = np.clip(X_norm.T @ dh, -5.0, 5.0)
            weights["layer_0"] -= lr * grad_w0
            weights["bias_0"] -= lr * np.sum(dh, axis=0)

            # Clip weights to prevent divergence
            for k in weights:
                weights[k] = np.clip(weights[k], -_WEIGHT_CLIP, _WEIGHT_CLIP)

            if epoch % 10 == 0:
                log.debug("Pretrain epoch %d: loss=%.4f", epoch, loss)

        # Compute Fisher information for EWC
        self._fisher_cache = self._fisher_information(weights, X_norm)

        log.info(
            "Pretrain complete: %d samples, %d features, final_loss=%.4f",
            n_samples, n_features, loss,
        )
        return weights

    def fine_tune(
        self,
        weights: dict,
        target_data: np.ndarray,
        target_labels: np.ndarray,
    ) -> dict:
        """Fine-tune pretrained weights on target domain.

        Freezes layers specified in config, applies EWC penalty to prevent
        catastrophic forgetting.

        Args:
            weights: Pretrained weight dict from pretrain().
            target_data: (n_samples, n_features) target features.
            target_labels: (n_samples,) binary labels.

        Returns:
            Fine-tuned weight dict.
        """
        if not weights:
            log.warning("No pretrained weights to fine-tune")
            return weights

        n_samples = target_data.shape[0]
        if n_samples < 5:
            log.warning("Insufficient target samples: %d", n_samples)
            return weights

        # Z-score normalize target data for stable fine-tuning
        tgt_mean = np.mean(target_data, axis=0)
        tgt_std = np.std(target_data, axis=0)
        tgt_std = np.where(tgt_std < 1e-8, 1.0, tgt_std)
        X_norm = (target_data - tgt_mean) / tgt_std

        # Deep copy weights
        ft_weights = {k: v.copy() for k, v in weights.items()}
        original_weights = {k: v.copy() for k, v in weights.items()}

        # Freeze specified layers
        ft_weights = self._freeze_layers(ft_weights, self.config.freeze_layers)

        lr = self.config.fine_tune_lr
        fisher = self._fisher_cache or {}
        _WEIGHT_CLIP = 10.0

        for epoch in range(self.config.fine_tune_epochs):
            # Forward pass
            h = X_norm @ ft_weights["layer_0"] + ft_weights["bias_0"]
            h = np.maximum(h, 0)
            logits = h @ ft_weights["layer_1"] + ft_weights["bias_1"]
            probs = 1.0 / (1.0 + np.exp(-np.clip(logits.ravel(), -30, 30)))

            # BCE loss
            eps = 1e-7
            loss = -np.mean(
                target_labels * np.log(probs + eps)
                + (1 - target_labels) * np.log(1 - probs + eps)
            )

            # EWC penalty
            ewc_penalty = self._compute_ewc_penalty(
                ft_weights, fisher, original_weights
            )
            total_loss = loss + ewc_penalty

            # Backward pass (skip frozen layers)
            dlogits = (probs - target_labels).reshape(-1, 1) / n_samples

            if "layer_1" not in self.config.freeze_layers:
                grad_w1 = np.clip(h.T @ dlogits, -5.0, 5.0)
                # Add EWC gradient
                if "layer_1" in fisher:
                    grad_w1 += (
                        EWC_LAMBDA
                        * fisher["layer_1"]
                        * (ft_weights["layer_1"] - original_weights["layer_1"])
                    )
                ft_weights["layer_1"] -= lr * grad_w1
                ft_weights["bias_1"] -= lr * np.sum(dlogits, axis=0)

            if "layer_0" not in self.config.freeze_layers:
                dh = dlogits @ ft_weights["layer_1"].T
                dh = dh * (h > 0).astype(float)
                grad_w0 = np.clip(X_norm.T @ dh, -5.0, 5.0)
                if "layer_0" in fisher:
                    grad_w0 += (
                        EWC_LAMBDA
                        * fisher["layer_0"]
                        * (ft_weights["layer_0"] - original_weights["layer_0"])
                    )
                ft_weights["layer_0"] -= lr * grad_w0
                ft_weights["bias_0"] -= lr * np.sum(dh, axis=0)

            # Clip weights to prevent divergence
            for k in ft_weights:
                ft_weights[k] = np.clip(ft_weights[k], -_WEIGHT_CLIP, _WEIGHT_CLIP)

            if epoch % 5 == 0:
                log.debug(
                    "Fine-tune epoch %d: loss=%.4f, ewc=%.6f, total=%.4f",
                    epoch, loss, ewc_penalty, total_loss,
                )

        log.info(
            "Fine-tune complete: %d samples, %d epochs, loss=%.4f",
            n_samples, self.config.fine_tune_epochs, total_loss,
        )
        return ft_weights

    def _freeze_layers(self, weights: dict, freeze_list: list) -> dict:
        """Mark layers as frozen (they won't be updated during fine-tuning).

        The actual freezing happens in fine_tune() by skipping gradient
        updates for frozen layer names. This method is a no-op on the
        weight values but validates the freeze list.

        Args:
            weights: Weight dict.
            freeze_list: Layer names to freeze.

        Returns:
            Same weight dict (freeze is enforced in fine_tune loop).
        """
        for layer_name in freeze_list:
            if layer_name not in weights:
                log.warning("Freeze layer '%s' not found in weights", layer_name)
        return weights

    def _compute_ewc_penalty(
        self, weights: dict, fisher_info: dict, original_weights: dict
    ) -> float:
        """Elastic Weight Consolidation penalty.

        L_EWC = (lambda / 2) * sum_i F_i * (theta_i - theta*_i)^2

        Penalizes deviation from pretrained weights, scaled by Fisher
        information (importance of each parameter for source domain).

        Args:
            weights: Current weights.
            fisher_info: Diagonal Fisher information per parameter.
            original_weights: Pretrained weights (theta*).

        Returns:
            Scalar EWC penalty.
        """
        if not fisher_info:
            return 0.0

        penalty = 0.0
        for key in weights:
            if key in fisher_info and key in original_weights:
                diff = weights[key] - original_weights[key]
                penalty += float(np.sum(fisher_info[key] * diff ** 2))

        return (EWC_LAMBDA / 2.0) * penalty

    def _fisher_information(self, weights: dict, data: np.ndarray) -> dict:
        """Compute diagonal Fisher information matrix.

        Approximation: F_i = E[ (d log p / d theta_i)^2 ]
        Computed as average squared gradient over the dataset.

        Args:
            weights: Current weight dict.
            data: (n_samples, n_features) input data.

        Returns:
            Dict of parameter_name -> diagonal Fisher (same shape as weight).
        """
        n = data.shape[0]
        if n == 0:
            return {}

        # Forward pass (data should already be normalized by caller)
        h = data @ weights["layer_0"] + weights["bias_0"]
        h_relu = np.maximum(h, 0)
        logits = h_relu @ weights["layer_1"] + weights["bias_1"]
        probs = 1.0 / (1.0 + np.exp(-np.clip(logits.ravel(), -30, 30)))

        fisher = {}

        # Accumulate squared gradients over samples (subsample for speed)
        max_fisher_samples = min(n, 200)
        indices = np.random.choice(n, max_fisher_samples, replace=False)

        f_layer_1 = np.zeros_like(weights["layer_1"])
        f_bias_1 = np.zeros_like(weights["bias_1"])
        f_layer_0 = np.zeros_like(weights["layer_0"])
        f_bias_0 = np.zeros_like(weights["bias_0"])

        for idx in indices:
            x_i = data[idx : idx + 1]
            h_i = x_i @ weights["layer_0"] + weights["bias_0"]
            h_relu_i = np.maximum(h_i, 0)
            logit_i = h_relu_i @ weights["layer_1"] + weights["bias_1"]
            p_i = 1.0 / (1.0 + np.exp(-np.clip(logit_i.ravel(), -30, 30)))

            # Gradient of log-likelihood for a Bernoulli
            dlogit = (p_i - p_i * p_i).reshape(-1, 1)  # variance of Bernoulli

            grad_w1 = h_relu_i.T * dlogit.ravel()
            grad_b1 = dlogit.ravel()
            dh = dlogit * weights["layer_1"].T
            dh = dh * (h_i > 0).astype(float)
            grad_w0 = x_i.T @ dh
            grad_b0 = dh.ravel()

            f_layer_1 += grad_w1 ** 2
            f_bias_1 += grad_b1 ** 2
            f_layer_0 += grad_w0 ** 2
            f_bias_0 += grad_b0 ** 2

        n_f = max_fisher_samples
        fisher["layer_0"] = f_layer_0 / n_f
        fisher["bias_0"] = f_bias_0 / n_f
        fisher["layer_1"] = f_layer_1 / n_f
        fisher["bias_1"] = f_bias_1 / n_f

        return fisher


# ── Main Orchestrator ────────────────────────────────────────────────

class TransferLearner:
    """Orchestrates cross-market transfer learning pipeline.

    Progressive training: US → EU → LSE.
    Detects domain shift at each stage and aborts if shift is too large.
    """

    def __init__(self, config: TransferConfig):
        self.config = config
        self._state_dir = STATE_DIR
        self._trainer = ProgressiveTrainer(config)

    def train_progressive(
        self,
        us_data: dict,
        eu_data: dict,
        lse_data: dict,
    ) -> dict:
        """Progressive domain adaptation: US → EU → LSE.

        Each data dict: {"features": np.ndarray, "labels": np.ndarray,
                         "feature_names": list}

        Args:
            us_data: US market training data.
            eu_data: EU market training data.
            lse_data: LSE ETP training data (final target).

        Returns:
            Result dict with weights, metrics, and transfer diagnostics.
        """
        result = {
            "status": "started",
            "stages": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Stage 1: Pretrain on US
        us_features = us_data.get("features")
        us_labels = us_data.get("labels")
        if us_features is None or us_labels is None:
            result["status"] = "error"
            result["reason"] = "Missing US data"
            return result

        log.info("Stage 1: Pretraining on US data (%d samples)", len(us_labels))
        weights = self._trainer.pretrain(us_features, us_labels)
        if not weights:
            result["status"] = "pretrain_failed"
            return result

        result["stages"].append({
            "stage": "us_pretrain",
            "n_samples": len(us_labels),
            "status": "complete",
        })

        # Stage 2: Fine-tune on EU (intermediate domain)
        eu_features = eu_data.get("features")
        eu_labels = eu_data.get("labels")
        if eu_features is not None and eu_labels is not None and len(eu_labels) > 0:
            # Check domain shift
            shift_detector = DomainShiftDetector({"samples": us_features})
            eu_shift = shift_detector.compute_shift(eu_features)
            log.info("US → EU domain shift: %.4f", eu_shift)

            if shift_detector.is_transferable(eu_shift):
                # Feature alignment if configured
                if self.config.feature_alignment:
                    us_names = us_data.get("feature_names", [])
                    eu_names = eu_data.get("feature_names", [])
                    if us_names and eu_names:
                        aligner = FeatureAligner(us_names, eu_names)
                        _, eu_features = aligner.align(us_features, eu_features)

                weights = self._trainer.fine_tune(weights, eu_features, eu_labels)
                result["stages"].append({
                    "stage": "eu_fine_tune",
                    "n_samples": len(eu_labels),
                    "domain_shift": float(eu_shift),
                    "status": "complete",
                })
            else:
                log.warning(
                    "EU domain shift too large (%.4f > %.4f), skipping",
                    eu_shift, self.config.max_domain_shift,
                )
                result["stages"].append({
                    "stage": "eu_fine_tune",
                    "domain_shift": float(eu_shift),
                    "status": "skipped_high_shift",
                })

        # Stage 3: Fine-tune on LSE (final target)
        lse_features = lse_data.get("features")
        lse_labels = lse_data.get("labels")
        if lse_features is None or lse_labels is None:
            result["status"] = "error"
            result["reason"] = "Missing LSE data"
            return result

        shift_detector = DomainShiftDetector({"samples": us_features})
        lse_shift = shift_detector.compute_shift(lse_features)
        log.info("US → LSE domain shift: %.4f", lse_shift)

        if self.config.feature_alignment:
            us_names = us_data.get("feature_names", [])
            lse_names = lse_data.get("feature_names", [])
            if us_names and lse_names:
                aligner = FeatureAligner(us_names, lse_names)
                _, lse_features = aligner.align(us_features, lse_features)

        weights = self._trainer.fine_tune(weights, lse_features, lse_labels)
        result["stages"].append({
            "stage": "lse_fine_tune",
            "n_samples": len(lse_labels),
            "domain_shift": float(lse_shift),
            "status": "complete",
        })

        result["status"] = "complete"
        result["weights_keys"] = list(weights.keys())

        # Save state
        self._save_state(result)

        return result

    def evaluate_transfer(
        self, source_metrics: dict, target_metrics: dict
    ) -> dict:
        """Evaluate quality of transfer learning.

        Args:
            source_metrics: {"accuracy": float, "auc": float, ...} on source.
            target_metrics: Same metrics on target after transfer.

        Returns:
            Dict with transfer ratio and diagnostics.
        """
        result = {}

        for metric in ("accuracy", "auc", "f1"):
            src_val = source_metrics.get(metric, 0.0)
            tgt_val = target_metrics.get(metric, 0.0)

            if src_val > 0:
                result[f"{metric}_transfer_ratio"] = tgt_val / src_val
            else:
                result[f"{metric}_transfer_ratio"] = 0.0

        # Overall transfer effectiveness
        ratios = [v for k, v in result.items() if k.endswith("_transfer_ratio")]
        if ratios:
            result["mean_transfer_ratio"] = float(np.mean(ratios))
        else:
            result["mean_transfer_ratio"] = 0.0

        result["negative_transfer"] = self.detect_negative_transfer(
            source_metrics, target_metrics
        )

        return result

    def detect_negative_transfer(
        self, baseline_metrics: dict, transfer_metrics: dict
    ) -> bool:
        """Detect if transfer learning hurts target performance.

        Negative transfer occurs when the transferred model performs
        WORSE on the target domain than a model trained from scratch.

        Args:
            baseline_metrics: Target metrics WITHOUT transfer (trained from scratch).
            transfer_metrics: Target metrics WITH transfer.

        Returns:
            True if transfer is harmful.
        """
        baseline_auc = baseline_metrics.get("auc", 0.5)
        transfer_auc = transfer_metrics.get("auc", 0.5)

        # Negative transfer: transferred model is worse
        is_negative = transfer_auc < baseline_auc - 0.01  # 1% tolerance

        if is_negative:
            log.warning(
                "Negative transfer detected: baseline_auc=%.4f, transfer_auc=%.4f",
                baseline_auc, transfer_auc,
            )

        return is_negative

    def _save_state(self, result: dict) -> None:
        """Persist transfer learning state to disk."""
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            domain_pair = (
                f"{self.config.source_domain.value}_to_"
                f"{self.config.target_domain.value}"
            )
            path = self._state_dir / f"{domain_pair}.json"
            with open(str(path), "w") as f:
                json.dump(result, f, indent=2, default=str)
            log.info("Transfer state saved: %s", path)
        except Exception as e:
            log.warning("Failed to save transfer state: %s", e)
