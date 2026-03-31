"""Meta-Learning with MAML/Reptile for Fast Regime Adaptation — Book 97.

Trains a meta-learner across multiple market regimes (trending, mean-reverting,
high-vol, low-vol, transition). At inference time, adapts to the current regime
in 1-5 gradient steps using recent data (few-shot adaptation).

Uses Reptile (Nichol et al. 2018) — simpler than full MAML, no second-order
gradients needed. meta_params += meta_lr * mean(adapted - meta).

Key idea: the meta-learned initialization is a point in weight space that
is close to good solutions for ALL regimes. A few gradient steps on recent
data quickly specialise it to the current regime.

State: /app/data/meta_learning/meta_params.json

Bridge.py integration:
    try:
        from python_brain.ml.maml_trainer import (
            MAMLSignalGenerator, ReptileTrainer, FewShotRegimeAdapter,
        )
    except ImportError:
        pass

Usage:
    from python_brain.ml.maml_trainer import (
        MAMLSignalGenerator, ReptileTrainer, TaskDistribution,
    )

    task_dist = TaskDistribution("/app/data/meta_learning")
    tasks = task_dist.create_tasks_from_history(returns, regimes)
    trainer = ReptileTrainer(model)
    trainer.meta_train(tasks)
    trainer.save_meta_params("/app/data/meta_learning/meta_params.json")
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

import numpy as np

log = logging.getLogger(__name__)

__all__ = [
    "TaskType",
    "MetaTask",
    "TaskDistribution",
    "SimpleLinearModel",
    "ReptileTrainer",
    "FewShotRegimeAdapter",
    "MAMLSignalGenerator",
]

# ── Constants ─────────────────────────────────────────────────────────

STATE_DIR = Path("/app/data/meta_learning")
META_PARAMS_FILE = "meta_params.json"
MIN_REGIME_LENGTH = 20
DEFAULT_META_LR = 0.01
DEFAULT_INNER_LR = 0.1
DEFAULT_INNER_STEPS = 5


# ── Enums & Dataclasses ──────────────────────────────────────────────

class TaskType(Enum):
    TRENDING = "trending"
    MEAN_REVERTING = "mean_reverting"
    HIGH_VOL = "high_vol"
    LOW_VOL = "low_vol"
    TRANSITION = "transition"


@dataclass
class MetaTask:
    """A single meta-learning task episode.

    Each task corresponds to a regime period with train/test splits
    for inner-loop adaptation and evaluation.
    """
    task_type: TaskType
    train_data: np.ndarray      # (n_train, n_features)
    train_labels: np.ndarray    # (n_train,)
    test_data: np.ndarray       # (n_test, n_features)
    test_labels: np.ndarray     # (n_test,)
    regime_context: dict = field(default_factory=dict)


# ── Task Distribution ────────────────────────────────────────────────

class TaskDistribution:
    """Sample meta-learning tasks from historical regime data.

    Each task is an episode of (train, test) data from a contiguous
    regime period. The meta-learner trains across many such episodes.
    """

    def __init__(self, data_store_path: str):
        self.data_store_path = Path(data_store_path)
        self._cached_tasks: List[MetaTask] = []

    def sample_task(self, task_type: Optional[TaskType] = None) -> Optional[MetaTask]:
        """Sample a random task episode from cache.

        Args:
            task_type: If specified, only sample tasks of this type.

        Returns:
            A MetaTask, or None if no matching tasks available.
        """
        if not self._cached_tasks:
            log.warning("No cached tasks — call create_tasks_from_history first")
            return None

        candidates = self._cached_tasks
        if task_type is not None:
            candidates = [t for t in candidates if t.task_type == task_type]

        if not candidates:
            log.warning("No tasks of type %s", task_type)
            return None

        idx = np.random.randint(len(candidates))
        return candidates[idx]

    def create_tasks_from_history(
        self,
        returns: np.ndarray,
        regimes: np.ndarray,
        n_train: int = 50,
        n_test: int = 20,
    ) -> List[MetaTask]:
        """Create task episodes from historical regime periods.

        Splits each contiguous regime block into (train, test).

        Args:
            returns: (T, n_features) feature/return matrix.
            regimes: (T,) int or string array of regime labels.
            n_train: Number of train samples per task.
            n_test: Number of test samples per task.

        Returns:
            List of MetaTask episodes.
        """
        if len(returns) != len(regimes):
            log.warning(
                "Returns length (%d) != regimes length (%d)",
                len(returns), len(regimes),
            )
            return []

        # Create binary labels: positive return = 1, negative = 0
        # Use first column as the target return if multi-feature
        if returns.ndim == 2:
            target_returns = returns[:, 0]
            features = returns
        else:
            target_returns = returns
            features = returns.reshape(-1, 1)

        labels = (target_returns > 0).astype(float)

        # Map regime labels to TaskType
        regime_periods = self._identify_regime_periods(regimes)

        tasks = []
        for task_type, periods in regime_periods.items():
            for start, end in periods:
                length = end - start
                required = n_train + n_test
                if length < required:
                    continue

                # Train/test split within regime (respecting time order)
                train_end = start + n_train
                test_end = min(train_end + n_test, end)

                task = MetaTask(
                    task_type=task_type,
                    train_data=features[start:train_end].copy(),
                    train_labels=labels[start:train_end].copy(),
                    test_data=features[train_end:test_end].copy(),
                    test_labels=labels[train_end:test_end].copy(),
                    regime_context={
                        "start": int(start),
                        "end": int(end),
                        "length": int(length),
                        "mean_return": float(np.mean(target_returns[start:end])),
                        "volatility": float(np.std(target_returns[start:end])),
                    },
                )
                tasks.append(task)

        self._cached_tasks = tasks
        log.info(
            "Created %d meta-tasks from %d regime periods",
            len(tasks),
            sum(len(v) for v in regime_periods.values()),
        )
        return tasks

    def _identify_regime_periods(self, regimes: np.ndarray) -> dict:
        """Find contiguous blocks of the same regime label.

        Args:
            regimes: (T,) array of regime labels (int or str).

        Returns:
            Dict of TaskType -> list of (start_idx, end_idx) tuples.
        """
        # Map raw regime labels to TaskType
        label_map = {
            0: TaskType.TRENDING,
            1: TaskType.MEAN_REVERTING,
            2: TaskType.HIGH_VOL,
            3: TaskType.LOW_VOL,
            4: TaskType.TRANSITION,
            "trending": TaskType.TRENDING,
            "mean_reverting": TaskType.MEAN_REVERTING,
            "high_vol": TaskType.HIGH_VOL,
            "low_vol": TaskType.LOW_VOL,
            "transition": TaskType.TRANSITION,
        }

        periods: Dict[TaskType, List[Tuple[int, int]]] = {
            t: [] for t in TaskType
        }

        if len(regimes) == 0:
            return periods

        current_label = regimes[0]
        current_start = 0

        for i in range(1, len(regimes)):
            if regimes[i] != current_label:
                # End of a regime block
                task_type = label_map.get(current_label)
                if task_type is not None and (i - current_start) >= MIN_REGIME_LENGTH:
                    periods[task_type].append((current_start, i))
                current_label = regimes[i]
                current_start = i

        # Final block
        task_type = label_map.get(current_label)
        if task_type is not None and (len(regimes) - current_start) >= MIN_REGIME_LENGTH:
            periods[task_type].append((current_start, len(regimes)))

        return periods


# ── Simple Linear Model ──────────────────────────────────────────────

class SimpleLinearModel:
    """Lightweight linear model for meta-learning.

    Simple enough for fast inner-loop adaptation (1-5 steps).
    Uses analytical gradients — no autograd needed.
    """

    def __init__(self, input_dim: int, output_dim: int = 1):
        self.input_dim = input_dim
        self.output_dim = output_dim

        # Xavier initialization
        scale = math.sqrt(2.0 / (input_dim + output_dim))
        rng = np.random.RandomState(42)
        self._params = {
            "W": rng.randn(input_dim, output_dim) * scale,
            "b": np.zeros(output_dim),
        }

    def forward(self, X: np.ndarray) -> np.ndarray:
        """Linear prediction with sigmoid activation.

        Args:
            X: (n_samples, input_dim) feature matrix.

        Returns:
            (n_samples,) predicted probabilities.
        """
        logits = X @ self._params["W"] + self._params["b"]
        probs = 1.0 / (1.0 + np.exp(-np.clip(logits.ravel(), -30, 30)))
        return probs

    def get_params(self) -> dict:
        """Return a copy of current parameters."""
        return {k: v.copy() for k, v in self._params.items()}

    def set_params(self, params: dict) -> None:
        """Set model parameters from a dict."""
        for k in self._params:
            if k in params:
                self._params[k] = params[k].copy()

    def compute_loss(self, X: np.ndarray, y: np.ndarray) -> float:
        """Binary cross-entropy loss.

        Args:
            X: (n_samples, input_dim) features.
            y: (n_samples,) binary labels.

        Returns:
            Scalar loss.
        """
        probs = self.forward(X)
        eps = 1e-7
        loss = -np.mean(
            y * np.log(probs + eps) + (1 - y) * np.log(1 - probs + eps)
        )
        return float(loss)

    def compute_gradient(self, X: np.ndarray, y: np.ndarray) -> dict:
        """Analytical gradient of BCE loss w.r.t. parameters.

        Args:
            X: (n_samples, input_dim) features.
            y: (n_samples,) binary labels.

        Returns:
            Dict of param_name -> gradient array (same shape as param).
        """
        n = X.shape[0]
        probs = self.forward(X)

        # d(BCE)/d(logits) = (probs - y) / n
        dlogits = (probs - y).reshape(-1, 1) / n

        grad_W = X.T @ dlogits  # (input_dim, output_dim)
        grad_b = np.sum(dlogits, axis=0)  # (output_dim,)

        return {"W": grad_W, "b": grad_b}


# ── Reptile Meta-Learner ────────────────────────────────────────────

class ReptileTrainer:
    """Reptile meta-learner (Nichol et al. 2018).

    Outer loop: meta_params += meta_lr * mean(adapted_params - meta_params)
    Inner loop: K gradient steps on each task's train set.

    Simpler than full MAML (no second-order gradients), works well in
    practice for few-shot adaptation scenarios.
    """

    def __init__(
        self,
        model: SimpleLinearModel,
        meta_lr: float = DEFAULT_META_LR,
        inner_lr: float = DEFAULT_INNER_LR,
        inner_steps: int = DEFAULT_INNER_STEPS,
    ):
        self.model = model
        self.meta_lr = meta_lr
        self.inner_lr = inner_lr
        self.inner_steps = inner_steps

    def meta_train(
        self, tasks: List[MetaTask], n_iterations: int = 1000
    ) -> dict:
        """Reptile outer loop: learn a good initialization across tasks.

        Args:
            tasks: List of MetaTask episodes.
            n_iterations: Number of outer loop iterations.

        Returns:
            Training metrics dict.
        """
        if not tasks:
            log.warning("No tasks for meta-training")
            return {"status": "no_tasks"}

        meta_params = self.model.get_params()
        best_loss = float("inf")
        loss_history = []

        for iteration in range(n_iterations):
            # Sample batch of tasks
            batch_size = min(5, len(tasks))
            task_indices = np.random.choice(len(tasks), batch_size, replace=False)

            adapted_params_list = []
            task_losses = []

            for idx in task_indices:
                task = tasks[idx]

                # Set model to current meta params
                self.model.set_params(meta_params)

                # Inner loop: adapt to this task
                adapted = self._inner_loop(task)
                adapted_params_list.append(adapted)

                # Evaluate on task test set
                self.model.set_params(adapted)
                test_loss = self.model.compute_loss(task.test_data, task.test_labels)
                task_losses.append(test_loss)

            # Outer update: Reptile step
            meta_params = self._outer_update(meta_params, adapted_params_list)

            mean_loss = float(np.mean(task_losses))
            loss_history.append(mean_loss)

            if mean_loss < best_loss:
                best_loss = mean_loss

            if iteration % 100 == 0:
                log.info(
                    "Reptile iter %d/%d: mean_loss=%.4f, best=%.4f",
                    iteration, n_iterations, mean_loss, best_loss,
                )

        # Store final meta params
        self.model.set_params(meta_params)

        result = {
            "status": "complete",
            "n_iterations": n_iterations,
            "n_tasks": len(tasks),
            "final_loss": float(loss_history[-1]) if loss_history else 0.0,
            "best_loss": best_loss,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        log.info(
            "Meta-training complete: %d iters, %d tasks, best_loss=%.4f",
            n_iterations, len(tasks), best_loss,
        )
        return result

    def _inner_loop(self, task: MetaTask) -> dict:
        """K gradient steps on task's train set.

        Args:
            task: MetaTask with train_data and train_labels.

        Returns:
            Adapted parameter dict.
        """
        params = self.model.get_params()

        for step in range(self.inner_steps):
            self.model.set_params(params)
            grads = self.model.compute_gradient(task.train_data, task.train_labels)

            for key in params:
                if key in grads:
                    params[key] = params[key] - self.inner_lr * grads[key]

        return params

    def _outer_update(
        self, meta_params: dict, adapted_params_list: List[dict]
    ) -> dict:
        """Reptile outer update.

        meta_params += meta_lr * mean(adapted_params - meta_params)

        Args:
            meta_params: Current meta parameters.
            adapted_params_list: List of adapted parameter dicts from inner loops.

        Returns:
            Updated meta parameters.
        """
        if not adapted_params_list:
            return meta_params

        updated = {}
        n_tasks = len(adapted_params_list)

        for key in meta_params:
            # Compute mean difference
            diffs = [
                adapted[key] - meta_params[key]
                for adapted in adapted_params_list
                if key in adapted
            ]
            if diffs:
                mean_diff = sum(diffs) / len(diffs)
                updated[key] = meta_params[key] + self.meta_lr * mean_diff
            else:
                updated[key] = meta_params[key].copy()

        return updated

    def save_meta_params(self, path: str) -> None:
        """Save meta-learned parameters to JSON.

        Args:
            path: File path for JSON output.
        """
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)

            params = self.model.get_params()
            serializable = {}
            for k, v in params.items():
                serializable[k] = v.tolist()

            data = {
                "params": serializable,
                "input_dim": self.model.input_dim,
                "output_dim": self.model.output_dim,
                "meta_lr": self.meta_lr,
                "inner_lr": self.inner_lr,
                "inner_steps": self.inner_steps,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            with open(str(p), "w") as f:
                json.dump(data, f, indent=2)
            log.info("Meta params saved: %s", path)
        except Exception as e:
            log.warning("Failed to save meta params: %s", e)

    def load_meta_params(self, path: str) -> None:
        """Load meta-learned parameters from JSON.

        Args:
            path: File path to JSON file.
        """
        try:
            with open(path, "r") as f:
                data = json.load(f)

            params = {}
            for k, v in data.get("params", {}).items():
                params[k] = np.array(v)

            self.model.set_params(params)
            log.info("Meta params loaded: %s", path)
        except FileNotFoundError:
            log.warning("Meta params file not found: %s", path)
        except Exception as e:
            log.warning("Failed to load meta params: %s", e)


# ── Few-Shot Regime Adapter ──────────────────────────────────────────

class FewShotRegimeAdapter:
    """Adapt meta-learned parameters to current regime in 1-5 steps.

    At inference time, uses recent data to quickly specialise the
    meta-learned initialization to the current market regime.
    """

    def __init__(
        self,
        meta_params: dict,
        inner_lr: float = DEFAULT_INNER_LR,
        inner_steps: int = 3,
    ):
        self.meta_params = {k: v.copy() for k, v in meta_params.items()}
        self.inner_lr = inner_lr
        self.inner_steps = inner_steps
        self._adapted_params: Optional[dict] = None
        self._adaptation_loss_before: float = 0.0
        self._adaptation_loss_after: float = 0.0

    def adapt(
        self, recent_data: np.ndarray, recent_labels: np.ndarray
    ) -> dict:
        """Adapt meta-params to current regime using recent data.

        Args:
            recent_data: (n_recent, n_features) recent market data.
            recent_labels: (n_recent,) binary labels.

        Returns:
            Adapted parameter dict.
        """
        if recent_data.shape[0] < 3:
            log.warning("Too few samples for adaptation: %d", recent_data.shape[0])
            self._adapted_params = self.meta_params.copy()
            return self._adapted_params

        # Build a temporary model with meta params
        input_dim = recent_data.shape[1]
        W = self.meta_params.get("W")
        if W is None:
            log.warning("No W in meta_params")
            return self.meta_params

        model = SimpleLinearModel(input_dim, 1)
        model.set_params(self.meta_params)

        # Record loss before adaptation
        self._adaptation_loss_before = model.compute_loss(recent_data, recent_labels)

        # Gradient descent adaptation
        params = model.get_params()
        for step in range(self.inner_steps):
            model.set_params(params)
            grads = model.compute_gradient(recent_data, recent_labels)
            for key in params:
                if key in grads:
                    params[key] = params[key] - self.inner_lr * grads[key]

        # Record loss after adaptation
        model.set_params(params)
        self._adaptation_loss_after = model.compute_loss(recent_data, recent_labels)

        self._adapted_params = params
        log.info(
            "Adapted in %d steps: loss %.4f → %.4f",
            self.inner_steps,
            self._adaptation_loss_before,
            self._adaptation_loss_after,
        )
        return params

    def predict(self, adapted_params: dict, X: np.ndarray) -> np.ndarray:
        """Predict using adapted parameters.

        Args:
            adapted_params: Parameter dict from adapt().
            X: (n_samples, n_features) feature matrix.

        Returns:
            (n_samples,) predicted probabilities.
        """
        W = adapted_params.get("W")
        b = adapted_params.get("b")
        if W is None or b is None:
            return np.full(X.shape[0], 0.5)

        logits = X @ W + b
        probs = 1.0 / (1.0 + np.exp(-np.clip(logits.ravel(), -30, 30)))
        return probs

    def confidence(self, adapted_params: dict, X: np.ndarray) -> float:
        """Measure confidence of the adapted model.

        High confidence = predictions far from 0.5 (decisive).
        Low confidence = predictions clustered around 0.5 (uncertain).

        Args:
            adapted_params: Parameter dict.
            X: (n_samples, n_features) feature matrix.

        Returns:
            Confidence score in [0, 1].
        """
        probs = self.predict(adapted_params, X)
        if len(probs) == 0:
            return 0.0

        # Deviation from maximum entropy (0.5)
        deviations = np.abs(probs - 0.5)
        mean_deviation = float(np.mean(deviations))

        # Normalize: max deviation is 0.5, so scale to [0, 1]
        confidence = min(1.0, mean_deviation * 2.0)
        return confidence


# ── Signal Generator ─────────────────────────────────────────────────

class MAMLSignalGenerator:
    """Generate trading signals using meta-learned regime adaptation.

    Loads meta-learned parameters, adapts to recent market conditions
    in a few gradient steps, and produces directional predictions.
    """

    def __init__(self, meta_params_path: str):
        self.meta_params_path = meta_params_path
        self._meta_params: Optional[dict] = None
        self._adapter: Optional[FewShotRegimeAdapter] = None
        self._last_quality: float = 0.0
        self._load_meta_params()

    def _load_meta_params(self) -> None:
        """Load meta parameters from disk."""
        try:
            with open(self.meta_params_path, "r") as f:
                data = json.load(f)
            params = {}
            for k, v in data.get("params", {}).items():
                params[k] = np.array(v)
            self._meta_params = params
            log.info("Loaded meta params from %s", self.meta_params_path)
        except FileNotFoundError:
            log.warning("Meta params not found: %s", self.meta_params_path)
        except Exception as e:
            log.warning("Failed to load meta params: %s", e)

    def generate_signal(
        self,
        features: np.ndarray,
        recent_history: np.ndarray,
    ) -> dict:
        """Adapt meta-learner to recent data, then predict.

        Args:
            features: (n_samples, n_features) current features to predict on.
            recent_history: (n_recent, n_features+1) recent data with labels
                in the last column (used for adaptation).

        Returns:
            Signal dict with direction, confidence, and adaptation quality.
        """
        if self._meta_params is None:
            return {"direction": "FLAT", "confidence": 0.0, "status": "no_meta_params"}

        # Split recent history into features and labels
        if recent_history.ndim != 2 or recent_history.shape[1] < 2:
            return {"direction": "FLAT", "confidence": 0.0, "status": "bad_history"}

        recent_features = recent_history[:, :-1]
        recent_labels = recent_history[:, -1]

        # Adapt to current regime
        self._adapter = FewShotRegimeAdapter(self._meta_params)
        adapted_params = self._adapter.adapt(recent_features, recent_labels)

        # Predict on current features
        probs = self._adapter.predict(adapted_params, features)
        confidence_score = self._adapter.confidence(adapted_params, features)

        # Adaptation quality
        self._last_quality = self.get_adaptation_quality()

        # Aggregate prediction
        mean_prob = float(np.mean(probs))
        if mean_prob > 0.55:
            direction = "LONG"
        elif mean_prob < 0.45:
            direction = "SHORT"
        else:
            direction = "FLAT"

        result = {
            "direction": direction,
            "confidence": round(confidence_score * 100, 1),
            "bullish_prob": round(mean_prob, 4),
            "adaptation_quality": round(self._last_quality, 4),
            "n_adaptation_samples": len(recent_labels),
            "status": "ok",
        }

        return result

    def get_adaptation_quality(self) -> float:
        """Measure how well the last adaptation worked.

        Quality = relative loss reduction during adaptation.
        1.0 = perfect (loss went to 0), 0.0 = no improvement.

        Returns:
            Quality score in [0, 1].
        """
        if self._adapter is None:
            return 0.0

        before = self._adapter._adaptation_loss_before
        after = self._adapter._adaptation_loss_after

        if before < 1e-8:
            return 1.0  # Already at minimum

        reduction = (before - after) / before
        return float(max(0.0, min(1.0, reduction)))
