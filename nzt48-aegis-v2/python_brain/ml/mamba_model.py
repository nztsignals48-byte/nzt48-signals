"""Simplified Mamba/S4 State Space Model for Sequence Modeling — Book 161.

Numpy-only implementation of Structured State Space (S4) and Mamba-style
selective state space layers for time-series prediction.

State space model: h(t) = A * h(t-1) + B * x(t)
                   y(t) = C * h(t)

The key innovation of S4 is the HiPPO initialization of A and
Zero-Order Hold (ZOH) discretization for long-range dependencies.
Mamba extends this with input-dependent (selective) A, B, C matrices.

For trading: captures long-range dependencies in price sequences
(e.g., 60-day patterns) that RNNs/LSTMs miss due to vanishing gradients,
with O(n) inference (vs O(n^2) for transformers).

State persisted to /app/data/mamba/.

Usage:
    from python_brain.ml.mamba_model import (
        MambaModel, S4Layer, SelectiveStateSpace, S4Config,
    )
    config = S4Config(d_model=64, d_state=16, seq_len=60, n_layers=2)
    model = MambaModel(config)
    output = model.forward(x_seq)
    prediction = model.predict(features)
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("mamba_model")

__all__ = [
    "S4Config",
    "S4Layer",
    "SelectiveStateSpace",
    "MambaModel",
]

# ── Constants ──────────────────────────────────────────────────────────

STATE_DIR = Path("/app/data/mamba")


# ── Config ─────────────────────────────────────────────────────────────

@dataclass
class S4Config:
    """Configuration for S4/Mamba model.

    Attributes:
        d_model: Model dimension (width of each layer).
        d_state: State space hidden dimension (N).
        seq_len: Input sequence length.
        n_layers: Number of stacked S4/Mamba layers.
        dt_min: Minimum discretization step size.
        dt_max: Maximum discretization step size.
        seed: Random seed.
    """
    d_model: int = 64
    d_state: int = 16
    seq_len: int = 60
    n_layers: int = 2
    dt_min: float = 0.001
    dt_max: float = 0.1
    seed: int = 42


# ── HiPPO Initialization ─────────────────────────────────────────────

def _hippo_init(N: int) -> np.ndarray:
    """HiPPO-LegS initialization for the A matrix.

    Produces the (N, N) matrix that optimally compresses continuous
    signals using Legendre polynomial basis.

    Args:
        N: State dimension.

    Returns:
        A matrix of shape (N, N).
    """
    A = np.zeros((N, N))
    for n in range(N):
        for k in range(N):
            if n > k:
                A[n, k] = math.sqrt(2 * n + 1) * math.sqrt(2 * k + 1)
            elif n == k:
                A[n, k] = n + 1
    return -A


# ── S4 Layer ──────────────────────────────────────────────────────────

class S4Layer:
    """Structured State Space Sequence (S4) layer.

    Implements the discretized state space model:
      h(t) = A_d * h(t-1) + B_d * x(t)
      y(t) = C * h(t)

    Where A_d, B_d are obtained via Zero-Order Hold (ZOH) discretization
    of the continuous (A, B) matrices.
    """

    def __init__(self, d_model: int, d_state: int, seed: int = 42):
        """Initialize S4 layer.

        Args:
            d_model: Input/output dimension.
            d_state: State space dimension (N).
            seed: Random seed.
        """
        self.d_model = d_model
        self.d_state = d_state
        self._rng = np.random.default_rng(seed)

        # Continuous-time parameters
        # A: (d_state, d_state) — HiPPO initialization
        self.A = _hippo_init(d_state)

        # B: (d_state, d_model) — random init
        self.B = self._rng.normal(0, 1.0 / math.sqrt(d_state),
                                   (d_state, d_model))

        # C: (d_model, d_state) — random init
        self.C = self._rng.normal(0, 1.0 / math.sqrt(d_state),
                                   (d_model, d_state))

        # D: skip connection (d_model,)
        self.D = np.ones(d_model)

        # Learnable log-step size
        self._log_dt = np.log(np.full(d_model, 0.01))

        # Cache discretized matrices
        self._Ad: Optional[np.ndarray] = None
        self._Bd: Optional[np.ndarray] = None

        log.info("S4Layer: d_model=%d, d_state=%d", d_model, d_state)

    def _discretize(self, dt: float) -> Tuple[np.ndarray, np.ndarray]:
        """Zero-Order Hold (ZOH) discretization of continuous (A, B).

        Converts continuous-time dynamics to discrete-time:
          A_d = exp(A * dt)  ≈ I + A*dt (first-order approximation)
          B_d = (A_d - I) * A^{-1} * B  ≈ B * dt

        Args:
            dt: Discretization step size.

        Returns:
            Tuple (A_d, B_d) discretized matrices.
        """
        N = self.d_state

        # First-order approximation (bilinear/ZOH)
        # For numerical stability with HiPPO A, use Euler approximation
        # A_d = I + A * dt
        Ad = np.eye(N) + self.A * dt

        # B_d = B * dt
        Bd = self.B * dt

        return Ad, Bd

    def forward(self, x: np.ndarray) -> np.ndarray:
        """Forward pass through the S4 layer.

        Processes a sequence by running the discretized state space
        model recurrently.

        Args:
            x: Input sequence, shape (seq_len, d_model).

        Returns:
            Output sequence, shape (seq_len, d_model).
        """
        seq_len, d_model = x.shape
        dt = float(np.exp(np.mean(self._log_dt)))
        dt = max(0.001, min(dt, 0.1))

        Ad, Bd = self._discretize(dt)
        self._Ad = Ad
        self._Bd = Bd

        # Initialize state
        h = np.zeros(self.d_state)

        outputs = np.zeros_like(x)

        for t in range(seq_len):
            # State update: h(t) = A_d * h(t-1) + B_d * x(t)
            h = Ad @ h + Bd @ x[t]

            # Output: y(t) = C * h(t) + D * x(t) (skip connection)
            outputs[t] = self.C @ h + self.D * x[t]

        return outputs

    def forward_batch(self, x: np.ndarray) -> np.ndarray:
        """Forward pass for a batch of sequences.

        Args:
            x: Input batch, shape (batch, seq_len, d_model).

        Returns:
            Output batch, shape (batch, seq_len, d_model).
        """
        batch_size = x.shape[0]
        outputs = np.zeros_like(x)
        for b in range(batch_size):
            outputs[b] = self.forward(x[b])
        return outputs


# ── Selective State Space (Mamba-style) ────────────────────────────────

class SelectiveStateSpace:
    """Mamba-style selective state space layer.

    Unlike standard S4 where A, B, C are fixed, Mamba makes them
    input-dependent: the selection mechanism learns which parts of
    the input to focus on. This is critical for trading where
    relevance of features changes with market regime.

    Selection: A(x), B(x), C(x) are linear projections of the input.
    """

    def __init__(self, d_model: int, d_state: int, seed: int = 42):
        """Initialize selective state space layer.

        Args:
            d_model: Input/output dimension.
            d_state: State dimension.
            seed: Random seed.
        """
        self.d_model = d_model
        self.d_state = d_state
        self._rng = np.random.default_rng(seed)

        # Selection projections: input -> state space params
        std = 1.0 / math.sqrt(d_model)

        # Project input to dt (step size selection)
        self.W_dt = self._rng.normal(0, std, (d_model, d_model))
        self.b_dt = np.full(d_model, -2.0)  # Initialize to small dt

        # Project input to B (input selection)
        self.W_B = self._rng.normal(0, std, (d_model, d_state))

        # Project input to C (output selection)
        self.W_C = self._rng.normal(0, std, (d_model, d_state))

        # Fixed A (diagonal, log-parameterized for stability)
        self._log_A = np.log(np.arange(1, d_state + 1, dtype=np.float64))

        # Skip connection
        self.D = np.ones(d_model)

        log.info("SelectiveStateSpace: d_model=%d, d_state=%d", d_model, d_state)

    def forward(self, x: np.ndarray) -> np.ndarray:
        """Forward pass with input-dependent selection.

        Args:
            x: Input sequence, shape (seq_len, d_model).

        Returns:
            Output sequence, shape (seq_len, d_model).
        """
        seq_len, d_model = x.shape

        # Initialize hidden state
        h = np.zeros(self.d_state)

        outputs = np.zeros_like(x)

        # Diagonal A (negative for stability)
        A_diag = -np.exp(self._log_A)

        for t in range(seq_len):
            x_t = x[t]

            # Input-dependent dt (softplus for positivity)
            dt_raw = x_t @ self.W_dt + self.b_dt
            dt = np.log1p(np.exp(dt_raw))  # Softplus
            dt_scalar = float(np.mean(np.clip(dt, 0.001, 0.1)))

            # Input-dependent B
            B_t = x_t @ self.W_B  # (d_state,)

            # Input-dependent C
            C_t = x_t @ self.W_C  # (d_state,)

            # Discretize A with input-dependent dt
            Ad_diag = np.exp(A_diag * dt_scalar)

            # State update: h(t) = Ad * h(t-1) + Bd * x(t)
            # Bd = B_t * dt_scalar (simplified ZOH)
            h = Ad_diag * h + B_t * dt_scalar

            # Output: y(t) = C(x) * h + D * x(t)
            y_t_state = C_t @ h  # Scalar -> broadcast
            outputs[t] = y_t_state + self.D * x_t

        return outputs

    def forward_batch(self, x: np.ndarray) -> np.ndarray:
        """Forward pass for a batch.

        Args:
            x: Input batch, shape (batch, seq_len, d_model).

        Returns:
            Output batch, shape (batch, seq_len, d_model).
        """
        batch_size = x.shape[0]
        outputs = np.zeros_like(x)
        for b in range(batch_size):
            outputs[b] = self.forward(x[b])
        return outputs


# ── Layer Norm ────────────────────────────────────────────────────────

class _LayerNorm:
    """Simple layer normalization."""

    def __init__(self, d_model: int, eps: float = 1e-6):
        self.gamma = np.ones(d_model)
        self.beta = np.zeros(d_model)
        self.eps = eps

    def forward(self, x: np.ndarray) -> np.ndarray:
        mean = np.mean(x, axis=-1, keepdims=True)
        var = np.var(x, axis=-1, keepdims=True)
        return self.gamma * (x - mean) / np.sqrt(var + self.eps) + self.beta


# ── Mamba Model ───────────────────────────────────────────────────────

class MambaModel:
    """Multi-layer Mamba/S4 model for sequence prediction.

    Stacks S4 or Mamba layers with residual connections and layer
    normalization. Outputs binary directional prediction with confidence.
    """

    def __init__(self, config: Optional[S4Config] = None):
        """Initialize Mamba model.

        Args:
            config: S4Config. Uses defaults if None.
        """
        self._config = config or S4Config()
        self._rng = np.random.default_rng(self._config.seed)

        # Build layers
        self.layers: List[SelectiveStateSpace] = []
        self.norms: List[_LayerNorm] = []

        for i in range(self._config.n_layers):
            layer = SelectiveStateSpace(
                self._config.d_model,
                self._config.d_state,
                seed=self._config.seed + i,
            )
            self.layers.append(layer)
            self.norms.append(_LayerNorm(self._config.d_model))

        # Output projection: d_model -> 1 (binary prediction)
        std = 1.0 / math.sqrt(self._config.d_model)
        self.W_out = self._rng.normal(0, std, (self._config.d_model, 1))
        self.b_out = np.zeros(1)

        # Input projection (for feature dimension != d_model)
        self.W_in: Optional[np.ndarray] = None
        self._input_dim: Optional[int] = None

        log.info("MambaModel: d_model=%d, d_state=%d, seq_len=%d, "
                 "n_layers=%d",
                 self._config.d_model, self._config.d_state,
                 self._config.seq_len, self._config.n_layers)

    def _ensure_input_proj(self, input_dim: int) -> None:
        """Lazily initialize input projection if needed."""
        if input_dim != self._config.d_model:
            if self._input_dim != input_dim:
                std = 1.0 / math.sqrt(input_dim)
                self.W_in = self._rng.normal(0, std,
                                              (input_dim, self._config.d_model))
                self._input_dim = input_dim

    def forward(self, x_seq: np.ndarray) -> np.ndarray:
        """Forward pass through all layers.

        Args:
            x_seq: Input sequence, shape (seq_len, d_in) or
                   (batch, seq_len, d_in).

        Returns:
            Output sequence, shape (seq_len, d_model) or
            (batch, seq_len, d_model).
        """
        is_batch = x_seq.ndim == 3
        if not is_batch:
            x_seq = x_seq[np.newaxis, :]

        batch_size, seq_len, d_in = x_seq.shape

        # Input projection if needed
        self._ensure_input_proj(d_in)
        if self.W_in is not None:
            x = np.zeros((batch_size, seq_len, self._config.d_model))
            for b in range(batch_size):
                x[b] = x_seq[b] @ self.W_in
        else:
            x = x_seq.copy()

        # Pass through layers with residual connections + layer norm
        for layer, norm in zip(self.layers, self.norms):
            residual = x
            x = layer.forward_batch(x)
            x = x + residual  # Residual connection

            # Layer norm per sample
            for b in range(batch_size):
                x[b] = norm.forward(x[b])

        if not is_batch:
            return x[0]
        return x

    def predict(self, features: np.ndarray) -> Dict[str, Any]:
        """Make a binary directional prediction with confidence.

        Takes the final hidden state from forward pass and projects
        to a single logit for binary classification (up/down).

        Args:
            features: Feature sequence, shape (seq_len, d_in) or
                      (batch, seq_len, d_in).

        Returns:
            Dict with direction, probability, confidence, raw_logit.
        """
        is_batch = features.ndim == 3

        # Forward pass
        output = self.forward(features)

        if not is_batch:
            output = output[np.newaxis, :]

        batch_size = output.shape[0]
        results: List[Dict[str, Any]] = []

        for b in range(batch_size):
            # Use last timestep's output
            last_hidden = output[b, -1, :]  # (d_model,)

            # Project to logit
            logit = float(last_hidden @ self.W_out + self.b_out)

            # Sigmoid for probability
            prob_up = 1.0 / (1.0 + math.exp(-np.clip(logit, -20, 20)))

            # Direction
            direction = "LONG" if prob_up > 0.5 else "SHORT"

            # Confidence: distance from 0.5, scaled to 0-100
            confidence = abs(prob_up - 0.5) * 200  # 0-100 scale
            confidence = min(confidence, 95.0)

            results.append({
                "direction": direction,
                "probability_up": round(prob_up, 4),
                "confidence": round(confidence, 1),
                "raw_logit": round(logit, 4),
            })

        if not is_batch:
            return results[0]
        return {"predictions": results}

    def save(self, path: str = "/app/data/mamba/model.npz") -> None:
        """Save model state."""
        save_path = Path(path)
        try:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_dict: Dict[str, np.ndarray] = {
                "W_out": self.W_out,
                "b_out": self.b_out,
            }
            if self.W_in is not None:
                save_dict["W_in"] = self.W_in

            for i, layer in enumerate(self.layers):
                save_dict[f"layer{i}_W_dt"] = layer.W_dt
                save_dict[f"layer{i}_b_dt"] = layer.b_dt
                save_dict[f"layer{i}_W_B"] = layer.W_B
                save_dict[f"layer{i}_W_C"] = layer.W_C
                save_dict[f"layer{i}_log_A"] = layer._log_A
                save_dict[f"layer{i}_D"] = layer.D

            for i, norm in enumerate(self.norms):
                save_dict[f"norm{i}_gamma"] = norm.gamma
                save_dict[f"norm{i}_beta"] = norm.beta

            np.savez(str(save_path), **save_dict)
            log.info("MambaModel saved to %s", path)
        except Exception as e:
            log.error("Failed to save MambaModel: %s", e)

    def load(self, path: str = "/app/data/mamba/model.npz") -> None:
        """Load model state."""
        try:
            data = np.load(path, allow_pickle=False)
            self.W_out = data["W_out"]
            self.b_out = data["b_out"]
            if "W_in" in data:
                self.W_in = data["W_in"]

            for i, layer in enumerate(self.layers):
                prefix = f"layer{i}"
                if f"{prefix}_W_dt" in data:
                    layer.W_dt = data[f"{prefix}_W_dt"]
                    layer.b_dt = data[f"{prefix}_b_dt"]
                    layer.W_B = data[f"{prefix}_W_B"]
                    layer.W_C = data[f"{prefix}_W_C"]
                    layer._log_A = data[f"{prefix}_log_A"]
                    layer.D = data[f"{prefix}_D"]

            for i, norm in enumerate(self.norms):
                prefix = f"norm{i}"
                if f"{prefix}_gamma" in data:
                    norm.gamma = data[f"{prefix}_gamma"]
                    norm.beta = data[f"{prefix}_beta"]

            log.info("MambaModel loaded from %s", path)
        except FileNotFoundError:
            log.info("No saved model at %s", path)
        except Exception as e:
            log.error("Failed to load MambaModel: %s", e)
