"""Knowledge Distillation: Teacher→Student Model Compression — Book 106.

Compresses large teacher models into small student models suitable for
4GB edge deployment (Raspberry Pi, cheap VPS, etc.).

Technique: Hinton et al. (2015) knowledge distillation with:
  - Soft targets from teacher (temperature-scaled softmax)
  - Combined loss: alpha * KL(soft_teacher, soft_student) + (1-alpha) * CE(hard, student)
  - INT8 quantization simulation for further compression

Architecture:
  TeacherModel: 256-hidden, ~65K params (loads from /app/data/models/)
  StudentModel: 64-hidden, ~4K params (1/10th size)
  Compression target: <500KB student model + INT8 weights

State: /app/data/models/student_*.npz

Bridge.py integration:
    try:
        from python_brain.ml.knowledge_distiller import (
            KnowledgeDistiller, StudentModel, DistillationConfig,
        )
    except ImportError:
        pass
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger(__name__)

__all__ = [
    "DistillationConfig",
    "TeacherModel",
    "StudentModel",
    "KnowledgeDistiller",
]

# ── Paths ──────────────────────────────────────────────────────────────
MODEL_DIR = Path("/app/data/models")

# ── Constants ──────────────────────────────────────────────────────────
EPSILON = 1e-8
CLIP_GRAD = 5.0


# ── Configuration ──────────────────────────────────────────────────────

@dataclass
class DistillationConfig:
    """Configuration for knowledge distillation.

    Attributes:
        temperature: softmax temperature for soft targets (higher = softer)
        alpha: weight for distillation loss vs hard label loss
            0.0 = only hard labels, 1.0 = only soft targets
        student_hidden: hidden dimension of student model
        teacher_hidden: hidden dimension of teacher model
        learning_rate: student training learning rate
        epochs: number of distillation epochs
        batch_size: mini-batch size
        patience: early stopping patience (epochs without improvement)
    """
    temperature: float = 3.0
    alpha: float = 0.7
    student_hidden: int = 64
    teacher_hidden: int = 256
    learning_rate: float = 0.001
    epochs: int = 200
    batch_size: int = 64
    patience: int = 20

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Utility Functions ──────────────────────────────────────────────────

def _xavier_init(fan_in: int, fan_out: int) -> np.ndarray:
    """Xavier/Glorot uniform initialization."""
    limit = math.sqrt(6.0 / (fan_in + fan_out))
    return np.random.uniform(-limit, limit, (fan_in, fan_out))


def _softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    """Numerically stable softmax."""
    shifted = x - np.max(x, axis=axis, keepdims=True)
    exp_x = np.exp(shifted)
    return exp_x / (np.sum(exp_x, axis=axis, keepdims=True) + EPSILON)


def _relu(x: np.ndarray) -> np.ndarray:
    return np.maximum(0.0, x)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    """Numerically stable sigmoid."""
    pos = x >= 0
    neg = ~pos
    result = np.empty_like(x, dtype=np.float64)
    result[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
    exp_x = np.exp(x[neg])
    result[neg] = exp_x / (1.0 + exp_x)
    return result


def _clip_grad(grad: np.ndarray, max_norm: float = CLIP_GRAD) -> np.ndarray:
    """Clip gradient by global norm."""
    norm = np.linalg.norm(grad)
    if norm > max_norm:
        grad = grad * (max_norm / (norm + EPSILON))
    return grad


def _layer_norm(x: np.ndarray, axis: int = -1) -> np.ndarray:
    """Layer normalization."""
    mean = np.mean(x, axis=axis, keepdims=True)
    var = np.var(x, axis=axis, keepdims=True)
    return (x - mean) / np.sqrt(var + EPSILON)


# ── Teacher Model ──────────────────────────────────────────────────────

class TeacherModel:
    """Large teacher model wrapper.

    Loads a pre-trained model from /app/data/models/ and provides
    forward pass for generating soft targets.

    Architecture: Input → Dense(teacher_hidden) → ReLU → Dense(teacher_hidden)
                  → ReLU → Dense(n_classes)

    Args:
        d_input: input feature dimension
        d_hidden: hidden dimension (default 256)
        n_classes: output classes (default 2 for binary)
    """

    def __init__(
        self,
        d_input: int,
        d_hidden: int = 256,
        n_classes: int = 2,
    ) -> None:
        self.d_input = d_input
        self.d_hidden = d_hidden
        self.n_classes = n_classes

        # 3-layer network
        self.W1 = _xavier_init(d_input, d_hidden)
        self.b1 = np.zeros(d_hidden)
        self.W2 = _xavier_init(d_hidden, d_hidden)
        self.b2 = np.zeros(d_hidden)
        self.W3 = _xavier_init(d_hidden, n_classes)
        self.b3 = np.zeros(n_classes)

    def forward(self, X: np.ndarray) -> np.ndarray:
        """Forward pass returning logits (pre-softmax).

        Args:
            X: input features (batch, d_input)

        Returns:
            Logits (batch, n_classes)
        """
        h1 = _relu(X @ self.W1 + self.b1)
        h2 = _relu(h1 @ self.W2 + self.b2)
        logits = h2 @ self.W3 + self.b3
        return logits

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Forward pass returning class probabilities.

        Args:
            X: input features

        Returns:
            Probabilities (batch, n_classes)
        """
        logits = self.forward(X)
        return _softmax(logits, axis=-1)

    def get_params(self) -> Dict[str, np.ndarray]:
        """Return all model parameters."""
        return {
            "W1": self.W1, "b1": self.b1,
            "W2": self.W2, "b2": self.b2,
            "W3": self.W3, "b3": self.b3,
        }

    def set_params(self, params: Dict[str, np.ndarray]) -> None:
        """Set model parameters from dict."""
        self.W1 = params["W1"]
        self.b1 = params["b1"]
        self.W2 = params["W2"]
        self.b2 = params["b2"]
        self.W3 = params["W3"]
        self.b3 = params["b3"]

    def count_params(self) -> int:
        """Count total parameters."""
        total = 0
        for p in self.get_params().values():
            total += p.size
        return total

    def load(self, path: Optional[Path] = None) -> bool:
        """Load teacher weights from disk.

        Searches /app/data/models/ for teacher_*.npz files.
        """
        load_path = path or (MODEL_DIR / "teacher_latest.npz")
        if not load_path.exists():
            log.warning("TeacherModel: no saved model at %s", load_path)
            return False
        try:
            data = np.load(str(load_path))
            self.set_params({k: data[k] for k in data.files})
            log.info("TeacherModel loaded from %s (%d params)",
                     load_path, self.count_params())
            return True
        except Exception as e:
            log.error("TeacherModel load failed: %s", e)
            return False

    def save(self, path: Optional[Path] = None) -> str:
        """Save teacher weights to disk."""
        save_path = path or (MODEL_DIR / "teacher_latest.npz")
        save_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(str(save_path), **self.get_params())
        log.info("TeacherModel saved to %s", save_path)
        return str(save_path)


# ── Student Model ──────────────────────────────────────────────────────

class StudentModel:
    """Small student model for edge deployment.

    ~1/10th the parameters of the teacher. Same input/output interface
    but narrower hidden layers.

    Architecture: Input → Dense(student_hidden) → ReLU → LN
                  → Dense(student_hidden // 2) → ReLU → LN
                  → Dense(n_classes)

    Args:
        d_input: input feature dimension
        d_hidden: hidden dimension (default 64)
        n_classes: output classes (default 2)
    """

    def __init__(
        self,
        d_input: int,
        d_hidden: int = 64,
        n_classes: int = 2,
    ) -> None:
        self.d_input = d_input
        self.d_hidden = d_hidden
        self.n_classes = n_classes

        d_mid = max(d_hidden // 2, 8)

        self.W1 = _xavier_init(d_input, d_hidden)
        self.b1 = np.zeros(d_hidden)
        self.W2 = _xavier_init(d_hidden, d_mid)
        self.b2 = np.zeros(d_mid)
        self.W3 = _xavier_init(d_mid, n_classes)
        self.b3 = np.zeros(n_classes)

        # Layer norm parameters
        self.ln1_gamma = np.ones(d_hidden)
        self.ln1_beta = np.zeros(d_hidden)
        self.ln2_gamma = np.ones(d_mid)
        self.ln2_beta = np.zeros(d_mid)

    def forward(self, X: np.ndarray) -> np.ndarray:
        """Forward pass returning logits.

        Args:
            X: input features (batch, d_input)

        Returns:
            Logits (batch, n_classes)
        """
        h1 = _relu(X @ self.W1 + self.b1)
        h1 = _layer_norm(h1) * self.ln1_gamma + self.ln1_beta

        h2 = _relu(h1 @ self.W2 + self.b2)
        h2 = _layer_norm(h2) * self.ln2_gamma + self.ln2_beta

        logits = h2 @ self.W3 + self.b3
        return logits

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Forward pass returning probabilities."""
        return _softmax(self.forward(X), axis=-1)

    def get_params(self) -> Dict[str, np.ndarray]:
        """Return all model parameters."""
        return {
            "W1": self.W1, "b1": self.b1,
            "W2": self.W2, "b2": self.b2,
            "W3": self.W3, "b3": self.b3,
            "ln1_gamma": self.ln1_gamma, "ln1_beta": self.ln1_beta,
            "ln2_gamma": self.ln2_gamma, "ln2_beta": self.ln2_beta,
        }

    def set_params(self, params: Dict[str, np.ndarray]) -> None:
        """Set model parameters from dict."""
        self.W1 = params["W1"]
        self.b1 = params["b1"]
        self.W2 = params["W2"]
        self.b2 = params["b2"]
        self.W3 = params["W3"]
        self.b3 = params["b3"]
        if "ln1_gamma" in params:
            self.ln1_gamma = params["ln1_gamma"]
            self.ln1_beta = params["ln1_beta"]
            self.ln2_gamma = params["ln2_gamma"]
            self.ln2_beta = params["ln2_beta"]

    def count_params(self) -> int:
        """Count total parameters."""
        total = 0
        for p in self.get_params().values():
            total += p.size
        return total

    def save(self, path: Optional[Path] = None) -> str:
        """Save student weights to disk."""
        save_path = path or (MODEL_DIR / "student_latest.npz")
        save_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(str(save_path), **self.get_params())
        log.info("StudentModel saved to %s (%d params)", save_path, self.count_params())
        return str(save_path)

    def load(self, path: Optional[Path] = None) -> bool:
        """Load student weights from disk."""
        load_path = path or (MODEL_DIR / "student_latest.npz")
        if not load_path.exists():
            log.warning("StudentModel: no saved model at %s", load_path)
            return False
        try:
            data = np.load(str(load_path))
            self.set_params({k: data[k] for k in data.files})
            log.info("StudentModel loaded from %s (%d params)",
                     load_path, self.count_params())
            return True
        except Exception as e:
            log.error("StudentModel load failed: %s", e)
            return False


# ── Knowledge Distiller ────────────────────────────────────────────────

class KnowledgeDistiller:
    """Distills knowledge from a large teacher into a compact student.

    Combines two loss signals:
      1. Distillation loss (KL divergence): student mimics teacher's
         soft probability distribution at high temperature
      2. Hard label loss (cross-entropy): student learns ground truth

    Combined: L = alpha * T^2 * KL(soft_teacher || soft_student) + (1-alpha) * CE(y, student)

    The T^2 factor compensates for the magnitude reduction of gradients
    from the soft targets at high temperature (Hinton et al. 2015).

    Args:
        config: DistillationConfig with hyperparameters
    """

    def __init__(self, config: Optional[DistillationConfig] = None) -> None:
        self.config = config or DistillationConfig()
        self.distillation_history: List[Dict[str, float]] = []

    @staticmethod
    def _soft_targets(logits: np.ndarray, temperature: float) -> np.ndarray:
        """Compute softmax with temperature scaling.

        Higher temperature → softer (more uniform) distribution.
        This reveals the teacher's "dark knowledge" about inter-class
        relationships that hard labels discard.

        Args:
            logits: raw logits (batch, n_classes)
            temperature: softmax temperature (> 1.0 for softer)

        Returns:
            Temperature-scaled probabilities (batch, n_classes)
        """
        scaled = logits / max(temperature, EPSILON)
        return _softmax(scaled, axis=-1)

    @staticmethod
    def _kl_divergence(p: np.ndarray, q: np.ndarray) -> float:
        """KL(p || q) — how much q diverges from p.

        Args:
            p: target distribution (teacher soft targets)
            q: approximate distribution (student soft targets)

        Returns:
            Mean KL divergence across batch
        """
        # Clip for numerical stability
        p_safe = np.clip(p, EPSILON, 1.0)
        q_safe = np.clip(q, EPSILON, 1.0)
        kl = np.sum(p_safe * np.log(p_safe / q_safe), axis=-1)
        return float(np.mean(kl))

    @staticmethod
    def _cross_entropy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
        """Cross-entropy loss.

        Args:
            y_true: one-hot labels (batch, n_classes) or class indices (batch,)
            y_pred: predicted probabilities (batch, n_classes)

        Returns:
            Mean cross-entropy loss
        """
        y_pred_safe = np.clip(y_pred, EPSILON, 1.0 - EPSILON)

        if y_true.ndim == 1:
            # Class indices → gather correct class probs
            batch_size = len(y_true)
            ce = -np.log(y_pred_safe[np.arange(batch_size), y_true.astype(int)])
        else:
            # One-hot
            ce = -np.sum(y_true * np.log(y_pred_safe), axis=-1)

        return float(np.mean(ce))

    def _distillation_loss(
        self,
        student_logits: np.ndarray,
        soft_targets: np.ndarray,
        hard_labels: np.ndarray,
        alpha: float,
        temperature: float,
    ) -> float:
        """Combined distillation + hard label loss.

        Args:
            student_logits: student raw logits (batch, n_classes)
            soft_targets: teacher soft probabilities at temperature T
            hard_labels: ground truth labels
            alpha: weight for distillation vs hard label
            temperature: softmax temperature used

        Returns:
            Combined loss value
        """
        # Soft student predictions at same temperature
        student_soft = self._soft_targets(student_logits, temperature)

        # Hard student predictions at T=1
        student_hard = _softmax(student_logits, axis=-1)

        # KL divergence loss (scaled by T^2 per Hinton)
        kl_loss = self._kl_divergence(soft_targets, student_soft)
        kl_loss *= temperature ** 2

        # Hard label cross-entropy loss
        ce_loss = self._cross_entropy(hard_labels, student_hard)

        # Combined
        total = alpha * kl_loss + (1.0 - alpha) * ce_loss
        return total

    def distill(
        self,
        teacher: TeacherModel,
        student: StudentModel,
        X_train: np.ndarray,
        y_train: np.ndarray,
        config: Optional[DistillationConfig] = None,
        X_val: Optional[np.ndarray] = None,
        y_val: Optional[np.ndarray] = None,
    ) -> Dict[str, Any]:
        """Run knowledge distillation training.

        Args:
            teacher: pre-trained teacher model (frozen)
            student: student model to train
            X_train: training features (n_samples, d_input)
            y_train: training labels (n_samples,) class indices
            config: override distillation config
            X_val: optional validation features
            y_val: optional validation labels

        Returns:
            Dict with training history and final metrics
        """
        cfg = config or self.config
        n_samples = X_train.shape[0]
        n_classes = teacher.n_classes

        if n_samples == 0:
            log.warning("KnowledgeDistiller: no training data")
            return {"status": "no_data", "final_loss": float("inf")}

        log.info("Distillation starting: teacher=%d params, student=%d params, "
                 "T=%.1f, alpha=%.2f, samples=%d",
                 teacher.count_params(), student.count_params(),
                 cfg.temperature, cfg.alpha, n_samples)

        # Pre-compute teacher soft targets (teacher is frozen)
        teacher_logits = teacher.forward(X_train)
        teacher_soft = self._soft_targets(teacher_logits, cfg.temperature)

        # Convert labels to one-hot if needed for CE loss
        if y_train.ndim == 1:
            y_onehot = np.zeros((n_samples, n_classes), dtype=np.float64)
            y_onehot[np.arange(n_samples), y_train.astype(int)] = 1.0
        else:
            y_onehot = y_train

        loss_history: List[float] = []
        val_history: List[float] = []
        best_loss = float("inf")
        best_params = student.get_params()
        patience_counter = 0

        for epoch in range(cfg.epochs):
            epoch_loss = 0.0
            n_batches = 0
            indices = np.random.permutation(n_samples)

            for start in range(0, n_samples, cfg.batch_size):
                end = min(start + cfg.batch_size, n_samples)
                idx = indices[start:end]
                bs = end - start

                X_b = X_train[idx]
                y_b = y_onehot[idx]
                teacher_soft_b = teacher_soft[idx]

                # Student forward
                student_logits = student.forward(X_b)

                # Compute loss
                loss = self._distillation_loss(
                    student_logits, teacher_soft_b, y_b,
                    cfg.alpha, cfg.temperature,
                )
                epoch_loss += loss
                n_batches += 1

                # Backward pass through student network
                # Gradient of combined loss w.r.t. student logits
                student_soft = self._soft_targets(student_logits, cfg.temperature)
                student_hard_probs = _softmax(student_logits, axis=-1)

                # d_KL/d_logits = (1/T) * (student_soft - teacher_soft_b)
                d_kl = (student_soft - teacher_soft_b) / max(cfg.temperature, EPSILON)
                d_kl *= cfg.temperature ** 2  # T^2 scaling

                # d_CE/d_logits = student_hard - y_onehot
                d_ce = student_hard_probs - y_b

                # Combined gradient on logits
                d_logits = (cfg.alpha * d_kl + (1.0 - cfg.alpha) * d_ce) / bs

                # Backprop through student layers
                # Layer 3: logits = h2 @ W3 + b3
                # Recompute hidden states
                h1 = _relu(X_b @ student.W1 + student.b1)
                h1 = _layer_norm(h1) * student.ln1_gamma + student.ln1_beta
                d_mid = max(student.d_hidden // 2, 8)
                h2 = _relu(h1 @ student.W2 + student.b2)
                h2 = _layer_norm(h2) * student.ln2_gamma + student.ln2_beta

                grad_W3 = _clip_grad(h2.T @ d_logits)
                grad_b3 = _clip_grad(d_logits.sum(axis=0))

                # Layer 2: h2_pre = h1 @ W2 + b2, h2 = relu(h2_pre)
                d_h2 = d_logits @ student.W3.T
                # Through layer norm (approximate: ignore LN gradient for stability)
                d_h2_pre = d_h2 * (_relu(h1 @ student.W2 + student.b2) > 0).astype(np.float64)

                grad_W2 = _clip_grad(h1.T @ d_h2_pre)
                grad_b2 = _clip_grad(d_h2_pre.sum(axis=0))

                # Layer 1: h1_pre = X @ W1 + b1, h1 = relu(h1_pre)
                d_h1 = d_h2_pre @ student.W2.T
                d_h1_pre = d_h1 * (_relu(X_b @ student.W1 + student.b1) > 0).astype(np.float64)

                grad_W1 = _clip_grad(X_b.T @ d_h1_pre)
                grad_b1 = _clip_grad(d_h1_pre.sum(axis=0))

                # SGD update
                student.W3 -= cfg.learning_rate * grad_W3
                student.b3 -= cfg.learning_rate * grad_b3
                student.W2 -= cfg.learning_rate * grad_W2
                student.b2 -= cfg.learning_rate * grad_b2
                student.W1 -= cfg.learning_rate * grad_W1
                student.b1 -= cfg.learning_rate * grad_b1

            avg_loss = epoch_loss / max(n_batches, 1)
            loss_history.append(avg_loss)

            # Validation
            val_loss = None
            if X_val is not None and y_val is not None:
                v_logits = student.forward(X_val)
                if y_val.ndim == 1:
                    v_onehot = np.zeros((len(y_val), n_classes), dtype=np.float64)
                    v_onehot[np.arange(len(y_val)), y_val.astype(int)] = 1.0
                else:
                    v_onehot = y_val
                v_teacher_logits = teacher.forward(X_val)
                v_teacher_soft = self._soft_targets(v_teacher_logits, cfg.temperature)
                val_loss = self._distillation_loss(
                    v_logits, v_teacher_soft, v_onehot,
                    cfg.alpha, cfg.temperature,
                )
                val_history.append(val_loss)
                monitor_loss = val_loss
            else:
                monitor_loss = avg_loss

            # Early stopping
            if monitor_loss < best_loss:
                best_loss = monitor_loss
                best_params = {k: v.copy() for k, v in student.get_params().items()}
                patience_counter = 0
            else:
                patience_counter += 1

            if patience_counter >= cfg.patience:
                log.info("Distillation early stopped at epoch %d (patience=%d)",
                         epoch + 1, cfg.patience)
                break

            if epoch % 20 == 0 or epoch == cfg.epochs - 1:
                val_str = f" val={val_loss:.6f}" if val_loss is not None else ""
                log.info("Distill epoch %d/%d: loss=%.6f%s best=%.6f",
                         epoch + 1, cfg.epochs, avg_loss, val_str, best_loss)

        # Restore best weights
        student.set_params(best_params)

        # Final metrics
        compression = self.measure_compression_ratio(teacher, student)
        student_acc = self._accuracy(student, X_train, y_train)

        result = {
            "status": "completed",
            "final_loss": loss_history[-1] if loss_history else float("inf"),
            "best_loss": best_loss,
            "loss_history": loss_history,
            "val_history": val_history,
            "n_epochs": len(loss_history),
            "student_accuracy": student_acc,
            "compression": compression,
            "config": cfg.to_dict(),
        }

        self.distillation_history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "best_loss": best_loss,
            "n_epochs": len(loss_history),
            "student_accuracy": student_acc,
            "compression_ratio": compression.get("param_ratio", 0.0),
        })

        log.info("Distillation complete: loss=%.6f, accuracy=%.4f, "
                 "compression=%.1fx, student=%d params",
                 best_loss, student_acc,
                 compression.get("param_ratio", 0.0),
                 student.count_params())

        return result

    def _accuracy(
        self,
        model: StudentModel,
        X: np.ndarray,
        y: np.ndarray,
    ) -> float:
        """Compute classification accuracy.

        Args:
            model: model to evaluate
            X: features
            y: true labels (class indices or one-hot)

        Returns:
            Accuracy in [0, 1]
        """
        probs = model.predict_proba(X)
        preds = np.argmax(probs, axis=-1)
        if y.ndim > 1:
            y_flat = np.argmax(y, axis=-1)
        else:
            y_flat = y.astype(int)
        return float(np.mean(preds == y_flat))

    @staticmethod
    def quantize_int8(weights: Dict[str, np.ndarray]) -> Dict[str, Any]:
        """Simulate INT8 quantization of model weights.

        Maps float64/float32 weights to int8 range [-127, 127] with
        per-tensor scale factors. This simulates what TFLite/ONNX
        quantization would produce for edge deployment.

        Args:
            weights: dict of weight arrays (float)

        Returns:
            Dict with quantized weights (int8), scales, and size info
        """
        quantized: Dict[str, Any] = {}
        total_float_bytes = 0
        total_int8_bytes = 0

        for name, w in weights.items():
            float_bytes = w.nbytes
            total_float_bytes += float_bytes

            # Per-tensor asymmetric quantization
            w_min = float(w.min())
            w_max = float(w.max())

            if abs(w_max - w_min) < EPSILON:
                # Constant tensor
                q = np.zeros_like(w, dtype=np.int8)
                scale = 1.0
                zero_point = 0
            else:
                # Map [w_min, w_max] → [-127, 127]
                scale = (w_max - w_min) / 254.0
                zero_point = int(round(-w_min / scale - 127))
                q = np.clip(np.round(w / scale + zero_point - 127), -127, 127).astype(np.int8)

            int8_bytes = q.nbytes
            total_int8_bytes += int8_bytes

            quantized[name] = {
                "data": q,
                "scale": scale,
                "zero_point": zero_point,
                "shape": list(w.shape),
                "float_bytes": float_bytes,
                "int8_bytes": int8_bytes,
            }

        # Add scale storage overhead (float32 per tensor)
        scale_overhead = len(weights) * 8  # scale + zero_point per tensor
        total_int8_bytes += scale_overhead

        quantized["_meta"] = {
            "total_float_bytes": total_float_bytes,
            "total_int8_bytes": total_int8_bytes,
            "compression_ratio": total_float_bytes / max(total_int8_bytes, 1),
            "size_reduction_pct": round(
                (1.0 - total_int8_bytes / max(total_float_bytes, 1)) * 100, 1
            ),
        }

        log.info("INT8 quantization: %.1f KB → %.1f KB (%.1fx compression, %.1f%% reduction)",
                 total_float_bytes / 1024,
                 total_int8_bytes / 1024,
                 quantized["_meta"]["compression_ratio"],
                 quantized["_meta"]["size_reduction_pct"])

        return quantized

    @staticmethod
    def dequantize_int8(quantized: Dict[str, Any]) -> Dict[str, np.ndarray]:
        """Dequantize INT8 weights back to float for inference verification.

        Args:
            quantized: output from quantize_int8

        Returns:
            Dict of float weight arrays
        """
        result: Dict[str, np.ndarray] = {}
        for name, info in quantized.items():
            if name.startswith("_"):
                continue
            q = info["data"]
            scale = info["scale"]
            zero_point = info["zero_point"]
            result[name] = (q.astype(np.float64) - zero_point + 127) * scale
        return result

    @staticmethod
    def measure_compression_ratio(
        teacher: TeacherModel,
        student: StudentModel,
    ) -> Dict[str, Any]:
        """Measure compression achieved by distillation.

        Args:
            teacher: original large model
            student: compressed small model

        Returns:
            Dict with param counts, ratio, estimated sizes
        """
        t_params = teacher.count_params()
        s_params = student.count_params()

        # Estimated sizes (float64 = 8 bytes, int8 = 1 byte)
        t_size_f64 = t_params * 8
        s_size_f64 = s_params * 8
        s_size_int8 = s_params * 1 + 16  # +overhead for scales

        return {
            "teacher_params": t_params,
            "student_params": s_params,
            "param_ratio": round(t_params / max(s_params, 1), 2),
            "teacher_size_kb": round(t_size_f64 / 1024, 1),
            "student_size_f64_kb": round(s_size_f64 / 1024, 1),
            "student_size_int8_kb": round(s_size_int8 / 1024, 1),
            "total_compression": round(t_size_f64 / max(s_size_int8, 1), 1),
            "fits_4gb": s_size_int8 < 4 * 1024 * 1024 * 1024,
        }
