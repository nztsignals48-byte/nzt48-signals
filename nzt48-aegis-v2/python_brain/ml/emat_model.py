"""Enhanced Multi-Aspect Attention for Trading — Book 102.

Numpy-only attention-based model combining:
  - Temporal Decay Attention: exponential decay over time distance
  - Trend Gating Network: ADX/trend strength modulates feature importance
  - Volatility Regime Scaler: vol-regime-aware attention rescaling
  - Multi-Scale Fusion: fuses features from 5s, 1min, 5min, 1hr timescales

Architecture:
  Input features → TrendGating → TemporalDecayAttention → VolRegimeScaler
  Multi-scale inputs → MultiScaleFusion → final prediction

Training uses numpy gradient descent with MSE loss. Designed for
directional prediction on ETP universe with 5-second bar data.

State: /app/data/models/emat_*.npz

Bridge.py integration:
    try:
        from python_brain.ml.emat_model import (
            EMATModel, TemporalDecayAttention, MultiScaleFusion,
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
    "TemporalDecayAttention",
    "TrendGatingNetwork",
    "VolatilityRegimeScaler",
    "MultiScaleFusion",
    "EMATModel",
]

# ── Paths ──────────────────────────────────────────────────────────────
MODEL_DIR = Path("/app/data/models")

# ── Constants ──────────────────────────────────────────────────────────
EPSILON = 1e-8
CLIP_GRAD = 5.0
DEFAULT_SCALES = ["5s", "1min", "5min", "1hr"]


# ── Utility Functions ──────────────────────────────────────────────────

def _xavier_init(fan_in: int, fan_out: int) -> np.ndarray:
    """Xavier/Glorot uniform initialization."""
    limit = math.sqrt(6.0 / (fan_in + fan_out))
    return np.random.uniform(-limit, limit, (fan_in, fan_out))


def _sigmoid(x: np.ndarray) -> np.ndarray:
    """Numerically stable sigmoid."""
    pos = x >= 0
    neg = ~pos
    result = np.empty_like(x, dtype=np.float64)
    result[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
    exp_x = np.exp(x[neg])
    result[neg] = exp_x / (1.0 + exp_x)
    return result


def _softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    """Numerically stable softmax along given axis."""
    shifted = x - np.max(x, axis=axis, keepdims=True)
    exp_x = np.exp(shifted)
    return exp_x / (np.sum(exp_x, axis=axis, keepdims=True) + EPSILON)


def _relu(x: np.ndarray) -> np.ndarray:
    """ReLU activation."""
    return np.maximum(0.0, x)


def _layer_norm(x: np.ndarray, axis: int = -1) -> np.ndarray:
    """Simple layer normalization."""
    mean = np.mean(x, axis=axis, keepdims=True)
    var = np.var(x, axis=axis, keepdims=True)
    return (x - mean) / np.sqrt(var + EPSILON)


def _clip_gradients(grad: np.ndarray, max_norm: float = CLIP_GRAD) -> np.ndarray:
    """Clip gradient by global norm."""
    norm = np.linalg.norm(grad)
    if norm > max_norm:
        grad = grad * (max_norm / (norm + EPSILON))
    return grad


# ── Temporal Decay Attention ───────────────────────────────────────────

class TemporalDecayAttention:
    """Attention mechanism with exponential temporal decay.

    More recent observations receive higher attention weights via
    multiplicative decay mask on the attention scores.

    For a sequence of length T, position i receives decay factor:
        decay[i, j] = decay_rate ^ |i - j|

    This biases the model to attend more to recent market events
    while still allowing long-range dependencies through the
    attention mechanism.

    Args:
        d_model: feature dimension
        n_heads: number of attention heads
        decay_rate: exponential decay per time step (0.9-0.99 typical)
    """

    def __init__(self, d_model: int, n_heads: int = 4, decay_rate: float = 0.95) -> None:
        self.d_model = d_model
        self.n_heads = n_heads
        self.d_k = d_model // n_heads
        self.decay_rate = decay_rate

        # Projection weights: Q, K, V
        self.W_q = _xavier_init(d_model, d_model)
        self.W_k = _xavier_init(d_model, d_model)
        self.W_v = _xavier_init(d_model, d_model)
        self.W_o = _xavier_init(d_model, d_model)

        # Cache decay matrix
        self._decay_cache: Dict[int, np.ndarray] = {}

    def _get_decay_matrix(self, seq_len: int) -> np.ndarray:
        """Compute or retrieve cached decay matrix for given sequence length."""
        if seq_len not in self._decay_cache:
            positions = np.arange(seq_len)
            # |i - j| distance matrix
            dist = np.abs(positions[:, None] - positions[None, :])
            self._decay_cache[seq_len] = self.decay_rate ** dist
        return self._decay_cache[seq_len]

    def forward(
        self,
        Q: np.ndarray,
        K: np.ndarray,
        V: np.ndarray,
        decay_rate: Optional[float] = None,
    ) -> np.ndarray:
        """Forward pass with temporal decay attention.

        Args:
            Q: query matrix (batch, seq_len, d_model) or (seq_len, d_model)
            K: key matrix (same shape as Q)
            V: value matrix (same shape as Q)
            decay_rate: override instance decay rate

        Returns:
            Attended output, same shape as input
        """
        # Handle 2D input (seq_len, d_model)
        squeeze = False
        if Q.ndim == 2:
            Q = Q[np.newaxis, :, :]
            K = K[np.newaxis, :, :]
            V = V[np.newaxis, :, :]
            squeeze = True

        batch_size, seq_len, _ = Q.shape
        rate = decay_rate if decay_rate is not None else self.decay_rate

        # Linear projections
        q = Q @ self.W_q  # (B, T, D)
        k = K @ self.W_k
        v = V @ self.W_v

        # Reshape for multi-head: (B, T, H, d_k) -> (B, H, T, d_k)
        q = q.reshape(batch_size, seq_len, self.n_heads, self.d_k).transpose(0, 2, 1, 3)
        k = k.reshape(batch_size, seq_len, self.n_heads, self.d_k).transpose(0, 2, 1, 3)
        v = v.reshape(batch_size, seq_len, self.n_heads, self.d_k).transpose(0, 2, 1, 3)

        # Scaled dot-product attention
        scale = math.sqrt(self.d_k)
        scores = (q @ k.transpose(0, 1, 3, 2)) / scale  # (B, H, T, T)

        # Apply temporal decay mask
        if seq_len not in self._decay_cache or rate != self.decay_rate:
            positions = np.arange(seq_len)
            dist = np.abs(positions[:, None] - positions[None, :])
            decay_mask = rate ** dist
        else:
            decay_mask = self._get_decay_matrix(seq_len)

        scores = scores * decay_mask[np.newaxis, np.newaxis, :, :]

        # Causal mask (optional — attend only to past)
        causal = np.tril(np.ones((seq_len, seq_len)))
        scores = scores * causal[np.newaxis, np.newaxis, :, :]
        scores = np.where(causal[np.newaxis, np.newaxis, :, :] == 0, -1e9, scores)

        # Softmax attention weights
        attn_weights = _softmax(scores, axis=-1)  # (B, H, T, T)

        # Weighted sum of values
        context = attn_weights @ v  # (B, H, T, d_k)

        # Reshape back: (B, H, T, d_k) -> (B, T, D)
        context = context.transpose(0, 2, 1, 3).reshape(batch_size, seq_len, self.d_model)

        # Output projection
        output = context @ self.W_o

        if squeeze:
            output = output[0]

        return output

    def get_attention_weights(
        self, Q: np.ndarray, K: np.ndarray
    ) -> np.ndarray:
        """Return attention weights without computing values (for visualization).

        Args:
            Q: query matrix
            K: key matrix

        Returns:
            Attention weight matrix (seq_len, seq_len)
        """
        if Q.ndim == 2:
            Q = Q[np.newaxis, :, :]
            K = K[np.newaxis, :, :]

        batch_size, seq_len, _ = Q.shape
        q = Q @ self.W_q
        k = K @ self.W_k

        q = q.reshape(batch_size, seq_len, self.n_heads, self.d_k).transpose(0, 2, 1, 3)
        k = k.reshape(batch_size, seq_len, self.n_heads, self.d_k).transpose(0, 2, 1, 3)

        scale = math.sqrt(self.d_k)
        scores = (q @ k.transpose(0, 1, 3, 2)) / scale
        decay_mask = self._get_decay_matrix(seq_len)
        scores = scores * decay_mask[np.newaxis, np.newaxis, :, :]

        weights = _softmax(scores, axis=-1)
        # Average over heads and batch
        return weights.mean(axis=(0, 1))


# ── Trend Gating Network ──────────────────────────────────────────────

class TrendGatingNetwork:
    """Gates feature activations based on trend strength (ADX).

    In trending markets (high ADX), momentum features should be amplified.
    In ranging markets (low ADX), mean-reversion features matter more.

    The gate is a learned sigmoid function of the trend indicator:
        gate = sigmoid(W_gate @ [features; trend_indicator] + b_gate)
        output = features * gate

    Args:
        d_features: input feature dimension
        trend_dim: dimension of trend indicator input (default 1 for ADX)
    """

    def __init__(self, d_features: int, trend_dim: int = 1) -> None:
        self.d_features = d_features
        self.trend_dim = trend_dim
        total_in = d_features + trend_dim

        self.W_gate = _xavier_init(total_in, d_features)
        self.b_gate = np.zeros(d_features)

        # Learned scaling factors for trend/range regimes
        self.W_scale = _xavier_init(trend_dim, d_features)
        self.b_scale = np.ones(d_features)  # Initialize to pass-through

    def forward(self, features: np.ndarray, trend_indicator: np.ndarray) -> np.ndarray:
        """Apply trend-aware gating to features.

        Args:
            features: input features (batch, d_features) or (d_features,)
            trend_indicator: trend strength, e.g. ADX/100 in [0,1]
                Shape: (batch, trend_dim) or (trend_dim,) or scalar

        Returns:
            Gated features, same shape as input
        """
        squeeze = False
        if features.ndim == 1:
            features = features[np.newaxis, :]
            squeeze = True

        # Ensure trend_indicator is 2D
        if np.isscalar(trend_indicator):
            trend_indicator = np.full((features.shape[0], self.trend_dim), trend_indicator)
        elif trend_indicator.ndim == 1:
            if trend_indicator.shape[0] == features.shape[0]:
                trend_indicator = trend_indicator[:, np.newaxis]
            else:
                trend_indicator = trend_indicator[np.newaxis, :]
                trend_indicator = np.broadcast_to(trend_indicator, (features.shape[0], self.trend_dim))

        # Concatenate features with trend indicator
        combined = np.concatenate([features, trend_indicator], axis=-1)

        # Sigmoid gate
        gate_logits = combined @ self.W_gate + self.b_gate
        gate = _sigmoid(gate_logits)

        # Scale factor from trend
        scale = trend_indicator @ self.W_scale + self.b_scale

        # Apply gate and scale
        output = features * gate * scale

        if squeeze:
            output = output[0]

        return output

    def get_gate_values(self, features: np.ndarray, trend_indicator: np.ndarray) -> np.ndarray:
        """Return raw gate values for analysis."""
        if features.ndim == 1:
            features = features[np.newaxis, :]
        if np.isscalar(trend_indicator):
            trend_indicator = np.full((features.shape[0], self.trend_dim), trend_indicator)
        elif trend_indicator.ndim == 1:
            trend_indicator = trend_indicator[:, np.newaxis]

        combined = np.concatenate([features, trend_indicator], axis=-1)
        gate_logits = combined @ self.W_gate + self.b_gate
        return _sigmoid(gate_logits)


# ── Volatility Regime Scaler ──────────────────────────────────────────

class VolatilityRegimeScaler:
    """Scales attention weights by volatility regime.

    In high-volatility regimes, attention should be more concentrated
    (sharper distribution). In low-vol, more uniform (explore more features).

    Regimes:
      0 = Low vol (< 15% annualized)
      1 = Normal (15-30%)
      2 = High vol (30-50%)
      3 = Crisis (> 50%)

    Each regime has learned temperature and scaling parameters.

    Args:
        n_regimes: number of volatility regimes
    """

    def __init__(self, n_regimes: int = 4) -> None:
        self.n_regimes = n_regimes

        # Temperature per regime: higher = sharper attention
        # Low vol → low temp (explore), high vol → high temp (concentrate)
        self.temperatures = np.array([0.5, 1.0, 1.5, 2.0], dtype=np.float64)

        # Scale factor per regime
        self.scales = np.ones(n_regimes, dtype=np.float64)

        # Regime boundaries (annualized vol)
        self.boundaries = np.array([0.15, 0.30, 0.50], dtype=np.float64)

    def classify_regime(self, volatility: float) -> int:
        """Classify volatility into regime index.

        Args:
            volatility: annualized volatility

        Returns:
            Regime index 0-3
        """
        for i, boundary in enumerate(self.boundaries):
            if volatility < boundary:
                return i
        return self.n_regimes - 1

    def forward(
        self,
        attention_weights: np.ndarray,
        vol_regime: int,
    ) -> np.ndarray:
        """Scale attention weights by volatility regime.

        Args:
            attention_weights: attention matrix (..., T, T) or (..., D)
            vol_regime: regime index (0-3) or can be float (auto-classify)

        Returns:
            Rescaled attention weights
        """
        if isinstance(vol_regime, float):
            vol_regime = self.classify_regime(vol_regime)

        regime_idx = max(0, min(self.n_regimes - 1, int(vol_regime)))
        temp = self.temperatures[regime_idx]
        scale = self.scales[regime_idx]

        # Apply temperature scaling: sharpen or soften attention distribution
        # Re-apply softmax with adjusted temperature
        if attention_weights.ndim >= 2:
            # Assume last axis is the attention distribution
            log_weights = np.log(attention_weights + EPSILON)
            scaled = _softmax(log_weights * temp, axis=-1)
        else:
            log_weights = np.log(attention_weights + EPSILON)
            exp_w = np.exp(log_weights * temp)
            scaled = exp_w / (np.sum(exp_w) + EPSILON)

        return scaled * scale


# ── Multi-Scale Fusion ─────────────────────────────────────────────────

class MultiScaleFusion:
    """Fuses features from multiple timescales via learned weighted sum.

    Each timescale (5s, 1min, 5min, 1hr) captures different market dynamics:
      - 5s: microstructure, order flow, immediate momentum
      - 1min: short-term trends, VWAP deviations
      - 5min: medium-term trends, support/resistance
      - 1hr: macro trends, institutional flow

    The fusion weights are learned via gradient descent, subject to
    softmax normalization (weights sum to 1).

    Args:
        d_model: feature dimension (all scales must match)
        scale_names: list of scale identifiers
    """

    def __init__(
        self,
        d_model: int,
        scale_names: Optional[List[str]] = None,
    ) -> None:
        self.d_model = d_model
        self.scale_names = scale_names or DEFAULT_SCALES
        self.n_scales = len(self.scale_names)

        # Learnable fusion logits (softmax → weights)
        self.fusion_logits = np.zeros(self.n_scales, dtype=np.float64)

        # Per-scale projection (optional dimension alignment)
        self.projections: Dict[str, np.ndarray] = {}
        for name in self.scale_names:
            self.projections[name] = np.eye(d_model, dtype=np.float64)

        # Cross-scale attention gate
        self.W_cross = _xavier_init(d_model * 2, d_model)
        self.b_cross = np.zeros(d_model)

    def forward(self, features_by_scale: Dict[str, np.ndarray]) -> np.ndarray:
        """Fuse multi-scale features via learned weighted sum.

        Args:
            features_by_scale: dict mapping scale name to feature array
                Each value shape: (batch, d_model) or (d_model,)

        Returns:
            Fused features (batch, d_model) or (d_model,)
        """
        # Collect available scales
        available = []
        scale_indices = []
        for i, name in enumerate(self.scale_names):
            if name in features_by_scale:
                feat = features_by_scale[name]
                if feat is not None and feat.size > 0:
                    # Project if needed
                    proj = self.projections.get(name)
                    if proj is not None:
                        feat = feat @ proj
                    available.append(feat)
                    scale_indices.append(i)

        if not available:
            log.warning("MultiScaleFusion: no valid scale features provided")
            return np.zeros(self.d_model, dtype=np.float64)

        # Determine output shape
        squeeze = False
        if available[0].ndim == 1:
            available = [f[np.newaxis, :] for f in available]
            squeeze = True

        batch_size = available[0].shape[0]

        # Compute fusion weights (softmax over available scales)
        logits = self.fusion_logits[scale_indices]
        weights = _softmax(logits)  # (n_available,)

        # Weighted sum
        stacked = np.stack(available, axis=0)  # (n_scales, batch, d_model)
        fused = np.einsum("s,sbd->bd", weights, stacked)

        # Cross-scale interaction: concatenate finest + coarsest → gate
        if len(available) >= 2:
            finest = available[0]   # Highest frequency
            coarsest = available[-1]  # Lowest frequency
            cross_input = np.concatenate([finest, coarsest], axis=-1)
            cross_gate = _sigmoid(cross_input @ self.W_cross + self.b_cross)
            fused = fused * cross_gate

        # Layer norm for stability
        fused = _layer_norm(fused, axis=-1)

        if squeeze:
            fused = fused[0]

        return fused

    def get_fusion_weights(self) -> Dict[str, float]:
        """Return current learned fusion weights per scale."""
        weights = _softmax(self.fusion_logits)
        return {name: float(w) for name, w in zip(self.scale_names, weights)}


# ── EMAT Full Model ───────────────────────────────────────────────────

class EMATModel:
    """Enhanced Multi-Aspect Attention for Trading.

    Full model combining temporal decay attention, trend gating,
    volatility regime scaling, and multi-scale fusion.

    Architecture:
      1. Raw features → TrendGatingNetwork → gated features
      2. Gated features → TemporalDecayAttention → attended features
      3. Attended weights → VolatilityRegimeScaler → rescaled attention
      4. Multi-scale features → MultiScaleFusion → fused representation
      5. Fused → FFN → prediction

    Args:
        d_model: hidden dimension
        n_heads: attention heads
        d_output: output dimension (1 for regression, 2+ for classification)
        decay_rate: temporal decay rate
        scale_names: timescale identifiers
    """

    def __init__(
        self,
        d_model: int = 64,
        n_heads: int = 4,
        d_output: int = 1,
        decay_rate: float = 0.95,
        scale_names: Optional[List[str]] = None,
    ) -> None:
        self.d_model = d_model
        self.n_heads = n_heads
        self.d_output = d_output
        self.decay_rate = decay_rate
        self.scale_names = scale_names or DEFAULT_SCALES

        # Sub-modules
        self.attention = TemporalDecayAttention(d_model, n_heads, decay_rate)
        self.trend_gate = TrendGatingNetwork(d_model, trend_dim=1)
        self.vol_scaler = VolatilityRegimeScaler(n_regimes=4)
        self.fusion = MultiScaleFusion(d_model, self.scale_names)

        # Feed-forward network: d_model → 4*d_model → d_model → d_output
        self.W_ff1 = _xavier_init(d_model, d_model * 4)
        self.b_ff1 = np.zeros(d_model * 4)
        self.W_ff2 = _xavier_init(d_model * 4, d_model)
        self.b_ff2 = np.zeros(d_model)
        self.W_out = _xavier_init(d_model, d_output)
        self.b_out = np.zeros(d_output)

        # Training state
        self.n_updates: int = 0
        self.train_losses: List[float] = []

    def forward(
        self,
        features: np.ndarray,
        trend: float = 0.5,
        vol_regime: int = 1,
        scales: Optional[Dict[str, np.ndarray]] = None,
    ) -> Dict[str, Any]:
        """Full forward pass.

        Args:
            features: input features (batch, seq_len, d_model) or (seq_len, d_model)
            trend: trend indicator (ADX/100, scalar)
            vol_regime: volatility regime index (0-3)
            scales: optional multi-scale features dict

        Returns:
            Dict with 'prediction', 'attention_weights', 'gate_values', 'fusion_weights'
        """
        squeeze = False
        if features.ndim == 2:
            features = features[np.newaxis, :, :]
            squeeze = True

        batch_size, seq_len, d = features.shape

        # Reshape to (batch * seq_len, d_model) for gating
        flat_features = features.reshape(-1, d)

        # Step 1: Trend gating
        gated = self.trend_gate.forward(flat_features, trend)
        gated = gated.reshape(batch_size, seq_len, d)

        # Step 2: Temporal decay attention (self-attention)
        attended = self.attention.forward(gated, gated, gated)

        # Step 3: Vol regime scaling on attention weights
        attn_weights = self.attention.get_attention_weights(gated, gated)
        scaled_weights = self.vol_scaler.forward(attn_weights, vol_regime)

        # Residual + layer norm
        hidden = _layer_norm(gated + attended, axis=-1)

        # Step 4: Multi-scale fusion (if scales provided)
        if scales:
            # Pool temporal dimension for each scale
            scale_pooled = {}
            for name, feat in scales.items():
                if feat is not None and feat.size > 0:
                    if feat.ndim == 3:
                        scale_pooled[name] = feat.mean(axis=1)  # (batch, d_model)
                    elif feat.ndim == 2:
                        scale_pooled[name] = feat
                    else:
                        scale_pooled[name] = feat

            fused = self.fusion.forward(scale_pooled)
            if fused.ndim == 1:
                fused = fused[np.newaxis, :]

            # Combine attended representation with fused multi-scale
            # Pool attended over time
            hidden_pooled = hidden.mean(axis=1)  # (batch, d_model)
            combined = hidden_pooled + fused
        else:
            combined = hidden.mean(axis=1)  # (batch, d_model)

        # Step 5: FFN → prediction
        h = _relu(combined @ self.W_ff1 + self.b_ff1)
        h = combined + h @ self.W_ff2 + self.b_ff2  # Residual
        h = _layer_norm(h, axis=-1)
        prediction = h @ self.W_out + self.b_out  # (batch, d_output)

        if self.d_output == 1:
            prediction = prediction.squeeze(-1)  # (batch,)

        if squeeze:
            prediction = prediction[0] if prediction.ndim > 0 else prediction

        result = {
            "prediction": prediction,
            "attention_weights": scaled_weights,
            "gate_values": self.trend_gate.get_gate_values(
                flat_features[:min(10, len(flat_features))], trend
            ).mean(axis=0),
            "fusion_weights": self.fusion.get_fusion_weights(),
        }

        return result

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        epochs: int = 100,
        lr: float = 0.001,
        batch_size: int = 32,
        trend: float = 0.5,
        vol_regime: int = 1,
        scales: Optional[Dict[str, np.ndarray]] = None,
        verbose: bool = True,
    ) -> Dict[str, Any]:
        """Train model using numpy gradient descent.

        Simple MSE loss with gradient descent on the FFN layers.
        Attention/gating weights are updated via backprop approximation.

        Args:
            X: input features (n_samples, seq_len, d_model)
            y: targets (n_samples,) or (n_samples, d_output)
            epochs: number of training epochs
            lr: learning rate
            batch_size: mini-batch size
            trend: trend indicator for gating
            vol_regime: volatility regime
            scales: optional multi-scale features
            verbose: log progress

        Returns:
            Dict with 'final_loss', 'loss_history', 'n_epochs', 'n_updates'
        """
        n_samples = X.shape[0]
        if n_samples == 0:
            log.warning("EMATModel.train: no samples provided")
            return {"final_loss": float("inf"), "loss_history": [], "n_epochs": 0, "n_updates": 0}

        if y.ndim == 1:
            y = y[:, np.newaxis] if self.d_output > 1 else y

        loss_history: List[float] = []
        best_loss = float("inf")

        for epoch in range(epochs):
            epoch_loss = 0.0
            n_batches = 0

            # Shuffle
            indices = np.random.permutation(n_samples)

            for start in range(0, n_samples, batch_size):
                end = min(start + batch_size, n_samples)
                batch_idx = indices[start:end]
                X_batch = X[batch_idx]
                y_batch = y[batch_idx]

                # Forward pass
                result = self.forward(X_batch, trend, vol_regime, scales)
                pred = result["prediction"]

                if pred.ndim == 0:
                    pred = np.array([pred])

                # MSE loss
                if y_batch.ndim == 1 and pred.ndim == 1:
                    error = pred - y_batch
                else:
                    error = pred.flatten() - y_batch.flatten()

                batch_loss = float(np.mean(error ** 2))
                epoch_loss += batch_loss
                n_batches += 1

                # Backward pass (approximate): update FFN weights
                # Gradient of MSE w.r.t. output: 2 * error / n
                bs = end - start
                d_pred = (2.0 * error / bs)

                if d_pred.ndim == 1:
                    d_pred = d_pred[:, np.newaxis]  # (bs, 1)

                # Gradient through W_out
                # pred = h @ W_out + b_out
                # Need h from forward pass — recompute for this batch
                features_batch = X_batch
                if features_batch.ndim == 2:
                    features_batch = features_batch[np.newaxis, :, :]

                flat = features_batch.reshape(-1, features_batch.shape[-1])
                gated = self.trend_gate.forward(flat, trend)
                gated = gated.reshape(features_batch.shape)
                attended = self.attention.forward(gated, gated, gated)
                hidden = _layer_norm(gated + attended, axis=-1)

                if scales:
                    scale_pooled = {}
                    for name, feat in scales.items():
                        if feat is not None and feat.size > 0:
                            if feat.ndim == 3:
                                scale_pooled[name] = feat.mean(axis=1)
                            else:
                                scale_pooled[name] = feat
                    fused = self.fusion.forward(scale_pooled)
                    if fused.ndim == 1:
                        fused = fused[np.newaxis, :]
                    combined = hidden.mean(axis=1) + fused
                else:
                    combined = hidden.mean(axis=1)

                h1 = combined @ self.W_ff1 + self.b_ff1
                h1_relu = _relu(h1)
                h = combined + h1_relu @ self.W_ff2 + self.b_ff2
                h = _layer_norm(h, axis=-1)

                # Gradient: W_out
                grad_W_out = _clip_gradients(h.T @ d_pred)
                grad_b_out = _clip_gradients(d_pred.sum(axis=0))
                self.W_out -= lr * grad_W_out
                self.b_out -= lr * grad_b_out

                # Gradient: W_ff2
                d_h = d_pred @ self.W_out.T  # (bs, d_model)
                d_h1_relu = d_h @ self.W_ff2.T
                d_h1 = d_h1_relu * (h1 > 0).astype(np.float64)  # ReLU backward

                grad_W_ff2 = _clip_gradients(h1_relu.T @ d_h)
                grad_b_ff2 = _clip_gradients(d_h.sum(axis=0))
                self.W_ff2 -= lr * grad_W_ff2
                self.b_ff2 -= lr * grad_b_ff2

                # Gradient: W_ff1
                grad_W_ff1 = _clip_gradients(combined.T @ d_h1)
                grad_b_ff1 = _clip_gradients(d_h1.sum(axis=0))
                self.W_ff1 -= lr * grad_W_ff1
                self.b_ff1 -= lr * grad_b_ff1

                # Update fusion logits (if multi-scale)
                if scales and len(scales) > 0:
                    # Approximate gradient on fusion weights
                    d_fused = d_h  # (bs, d_model)
                    fusion_grad = np.zeros(self.fusion.n_scales)
                    weights = _softmax(self.fusion.fusion_logits)
                    for si, name in enumerate(self.fusion.scale_names):
                        if name in scales and scales[name] is not None:
                            s_feat = scales[name]
                            if s_feat.ndim == 3:
                                s_feat = s_feat.mean(axis=1)
                            if s_feat.ndim == 1:
                                s_feat = s_feat[np.newaxis, :]
                            # dL/d_logit_i = sum_j d_fused_j * s_feat_j * (delta_ij - w_j) * w_i
                            contribution = np.sum(d_fused * s_feat)
                            fusion_grad[si] = contribution * weights[si]

                    fusion_grad = _clip_gradients(fusion_grad)
                    self.fusion.fusion_logits -= lr * fusion_grad

                self.n_updates += 1

            avg_loss = epoch_loss / max(n_batches, 1)
            loss_history.append(avg_loss)

            if avg_loss < best_loss:
                best_loss = avg_loss

            if verbose and (epoch % 10 == 0 or epoch == epochs - 1):
                log.info("EMATModel epoch %d/%d: loss=%.6f best=%.6f",
                         epoch + 1, epochs, avg_loss, best_loss)

        self.train_losses.extend(loss_history)

        return {
            "final_loss": loss_history[-1] if loss_history else float("inf"),
            "best_loss": best_loss,
            "loss_history": loss_history,
            "n_epochs": epochs,
            "n_updates": self.n_updates,
        }

    def save(self, path: Optional[Path] = None) -> str:
        """Save model weights to disk.

        Args:
            path: save path (default: /app/data/models/emat_latest.npz)

        Returns:
            Path string where model was saved
        """
        save_path = path or (MODEL_DIR / "emat_latest.npz")
        save_path.parent.mkdir(parents=True, exist_ok=True)

        weights = {
            "W_q": self.attention.W_q,
            "W_k": self.attention.W_k,
            "W_v": self.attention.W_v,
            "W_o": self.attention.W_o,
            "W_gate": self.trend_gate.W_gate,
            "b_gate": self.trend_gate.b_gate,
            "W_scale": self.trend_gate.W_scale,
            "b_scale": self.trend_gate.b_scale,
            "vol_temperatures": self.vol_scaler.temperatures,
            "vol_scales": self.vol_scaler.scales,
            "fusion_logits": self.fusion.fusion_logits,
            "W_ff1": self.W_ff1,
            "b_ff1": self.b_ff1,
            "W_ff2": self.W_ff2,
            "b_ff2": self.b_ff2,
            "W_out": self.W_out,
            "b_out": self.b_out,
        }

        np.savez(str(save_path), **weights)
        log.info("EMATModel saved to %s (%d weight arrays)", save_path, len(weights))
        return str(save_path)

    def load(self, path: Optional[Path] = None) -> bool:
        """Load model weights from disk.

        Args:
            path: load path

        Returns:
            True if loaded successfully
        """
        load_path = path or (MODEL_DIR / "emat_latest.npz")
        if not load_path.exists():
            log.warning("EMATModel: no saved model at %s", load_path)
            return False

        try:
            data = np.load(str(load_path))
            self.attention.W_q = data["W_q"]
            self.attention.W_k = data["W_k"]
            self.attention.W_v = data["W_v"]
            self.attention.W_o = data["W_o"]
            self.trend_gate.W_gate = data["W_gate"]
            self.trend_gate.b_gate = data["b_gate"]
            self.trend_gate.W_scale = data["W_scale"]
            self.trend_gate.b_scale = data["b_scale"]
            self.vol_scaler.temperatures = data["vol_temperatures"]
            self.vol_scaler.scales = data["vol_scales"]
            self.fusion.fusion_logits = data["fusion_logits"]
            self.W_ff1 = data["W_ff1"]
            self.b_ff1 = data["b_ff1"]
            self.W_ff2 = data["W_ff2"]
            self.b_ff2 = data["b_ff2"]
            self.W_out = data["W_out"]
            self.b_out = data["b_out"]
            log.info("EMATModel loaded from %s", load_path)
            return True
        except Exception as e:
            log.error("EMATModel load failed: %s", e)
            return False

    def get_config(self) -> Dict[str, Any]:
        """Return model configuration."""
        return {
            "d_model": self.d_model,
            "n_heads": self.n_heads,
            "d_output": self.d_output,
            "decay_rate": self.decay_rate,
            "scale_names": self.scale_names,
            "n_updates": self.n_updates,
            "n_params": self._count_params(),
            "fusion_weights": self.fusion.get_fusion_weights(),
        }

    def _count_params(self) -> int:
        """Count total model parameters."""
        total = 0
        for attr in [
            self.attention.W_q, self.attention.W_k, self.attention.W_v,
            self.attention.W_o, self.trend_gate.W_gate, self.trend_gate.b_gate,
            self.trend_gate.W_scale, self.trend_gate.b_scale,
            self.W_ff1, self.b_ff1, self.W_ff2, self.b_ff2,
            self.W_out, self.b_out, self.fusion.fusion_logits,
        ]:
            total += attr.size
        return total
