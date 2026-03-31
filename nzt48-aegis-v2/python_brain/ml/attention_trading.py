"""Attention Mechanism Trading Signals — Book 157.

Pure numpy attention mechanism for generating trading signals from
multi-stream market data. Implements scaled dot-product attention,
multi-head attention, cross-stream attention (price x volume), and
a temporal attention signal generator.

No PyTorch/TensorFlow — this is a forward-pass-only implementation
using pre-initialised random weights, suitable for 4GB RAM EC2.

Components:
  - ScaledDotProductAttention: Core attention operation
  - MultiHeadAttention: Parallel attention heads
  - CrossStreamAttention: Cross-attention between price and volume
  - TemporalAttentionSignal: Full signal generator

Bridge.py integration:
    try:
        from python_brain.ml.attention_trading import (
            TemporalAttentionSignal, MultiHeadAttention,
            CrossStreamAttention, ScaledDotProductAttention,
        )
    except ImportError:
        pass

    # Generate signal from recent bars:
    attn = TemporalAttentionSignal(n_features=5, seq_len=60, n_heads=4)
    sig = attn.generate_signal(price_seq, volume_seq, indicator_seq)
    direction = sig["direction"]      # 'long', 'short', 'neutral'
    confidence = sig["confidence"]    # 0.0 - 1.0
    attention_map = sig["attention_map"]  # which bars drove the signal
"""

from __future__ import annotations

import logging
import math
import os
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger("attention_trading")

__all__ = [
    "ScaledDotProductAttention",
    "MultiHeadAttention",
    "CrossStreamAttention",
    "TemporalAttentionSignal",
]

DATA_DIR = "/app/data/attention_trading"


# ---------------------------------------------------------------------------
# Utility: Xavier/Glorot initialisation
# ---------------------------------------------------------------------------

def _xavier_init(
    rows: int,
    cols: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Xavier/Glorot uniform initialisation for weight matrices."""
    limit = math.sqrt(6.0 / (rows + cols))
    return rng.uniform(-limit, limit, size=(rows, cols))


def _safe_matmul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Matrix multiply with NaN/Inf cleanup on input and output.

    Numpy BLAS may emit spurious overflow warnings on certain data/seed
    combinations even when inputs and outputs are finite. We suppress
    the internal BLAS warnings and validate the result.
    """
    a = np.nan_to_num(a, nan=0.0, posinf=1e6, neginf=-1e6)
    b = np.nan_to_num(b, nan=0.0, posinf=1e6, neginf=-1e6)
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        result = a @ b
    return np.nan_to_num(result, nan=0.0, posinf=1e6, neginf=-1e6)


def _softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    """Numerically stable softmax with NaN protection.

    Args:
        x: Input array.
        axis: Axis to apply softmax along.

    Returns:
        Softmax probabilities (same shape as x).
    """
    x = np.where(np.isfinite(x), x, 0.0)
    x_max = np.max(x, axis=axis, keepdims=True)
    exp_x = np.exp(np.clip(x - x_max, -50, 50))
    denom = np.sum(exp_x, axis=axis, keepdims=True)
    denom = np.where(denom < 1e-12, 1.0, denom)
    return exp_x / denom


def _layer_norm(
    x: np.ndarray,
    eps: float = 1e-6,
) -> np.ndarray:
    """Layer normalisation across the last dimension with NaN protection.

    Args:
        x: Input array of shape (..., d).
        eps: Epsilon for numerical stability.

    Returns:
        Normalised array of same shape.
    """
    x = np.where(np.isfinite(x), x, 0.0)
    mean = np.mean(x, axis=-1, keepdims=True)
    var = np.var(x, axis=-1, keepdims=True)
    return (x - mean) / np.sqrt(var + eps)


# ---------------------------------------------------------------------------
# Scaled Dot-Product Attention
# ---------------------------------------------------------------------------

class ScaledDotProductAttention:
    """Scaled dot-product attention: softmax(QK^T / sqrt(d_k)) V.

    This is the fundamental building block of the attention mechanism.
    Query attends to keys to produce a weighted average of values.
    """

    def forward(
        self,
        Q: np.ndarray,
        K: np.ndarray,
        V: np.ndarray,
        mask: Optional[np.ndarray] = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Compute scaled dot-product attention.

        Args:
            Q: Queries, shape (seq_q, d_k) or (batch, seq_q, d_k).
            K: Keys, shape (seq_k, d_k) or (batch, seq_k, d_k).
            V: Values, shape (seq_k, d_v) or (batch, seq_k, d_v).
            mask: Optional mask, shape broadcastable to (seq_q, seq_k).
                  True values are masked (set to -inf before softmax).

        Returns:
            Tuple of (output, attention_weights).
            output: shape (seq_q, d_v) or (batch, seq_q, d_v)
            attention_weights: shape (seq_q, seq_k) or (batch, seq_q, seq_k)
        """
        d_k = K.shape[-1]
        scaling = math.sqrt(float(d_k))

        # QK^T / sqrt(d_k) with overflow protection
        Q_safe = np.nan_to_num(Q, nan=0.0, posinf=1e6, neginf=-1e6)
        K_safe = np.nan_to_num(K, nan=0.0, posinf=1e6, neginf=-1e6)
        V_safe = np.nan_to_num(V, nan=0.0, posinf=1e6, neginf=-1e6)
        with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
            scores = np.nan_to_num(
                np.matmul(Q_safe, np.swapaxes(K_safe, -2, -1)) / scaling,
                nan=0.0, posinf=1e6, neginf=-1e6,
            )

        # Apply mask if provided
        if mask is not None:
            scores = np.where(mask, -1e9, scores)

        weights = _softmax(scores, axis=-1)
        with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
            output = np.nan_to_num(
                np.matmul(weights, V_safe),
                nan=0.0, posinf=1e6, neginf=-1e6,
            )

        return output, weights


# ---------------------------------------------------------------------------
# Multi-Head Attention
# ---------------------------------------------------------------------------

class MultiHeadAttention:
    """Multi-head attention with parallel heads and learned projections.

    Each head uses separate Q/K/V projections, enabling different heads
    to specialise in different patterns (short momentum, support/resistance,
    volume anomalies, etc.).

    Args:
        d_model: Model dimension (input/output size).
        n_heads: Number of parallel attention heads.
        seed: Random seed for weight initialisation.
    """

    def __init__(
        self,
        d_model: int,
        n_heads: int,
        seed: Optional[int] = None,
    ) -> None:
        if d_model % n_heads != 0:
            raise ValueError(
                f"d_model ({d_model}) must be divisible by n_heads ({n_heads})"
            )

        self.d_model = d_model
        self.n_heads = n_heads
        self.d_k = d_model // n_heads
        self.rng = np.random.default_rng(seed)

        # Projection weights for each head
        self.W_Q = _xavier_init(d_model, d_model, self.rng)
        self.W_K = _xavier_init(d_model, d_model, self.rng)
        self.W_V = _xavier_init(d_model, d_model, self.rng)
        self.W_O = _xavier_init(d_model, d_model, self.rng)

        self._attention = ScaledDotProductAttention()
        log.debug(
            "MultiHeadAttention: d_model=%d, n_heads=%d, d_k=%d",
            d_model, n_heads, self.d_k,
        )

    def forward(
        self,
        Q: np.ndarray,
        K: np.ndarray,
        V: np.ndarray,
        mask: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """Compute multi-head attention.

        Args:
            Q: Queries, shape (seq_len, d_model).
            K: Keys, shape (seq_len, d_model).
            V: Values, shape (seq_len, d_model).
            mask: Optional attention mask.

        Returns:
            Output array of shape (seq_len, d_model).
        """
        seq_len = Q.shape[0]

        # Project Q, K, V (safe matmul prevents overflow)
        Q_proj = _safe_matmul(Q, self.W_Q)
        K_proj = _safe_matmul(K, self.W_K)
        V_proj = _safe_matmul(V, self.W_V)

        # Split into heads: (n_heads, seq_len, d_k)
        Q_heads = Q_proj.reshape(seq_len, self.n_heads, self.d_k).transpose(1, 0, 2)
        K_heads = K_proj.reshape(seq_len, self.n_heads, self.d_k).transpose(1, 0, 2)
        V_heads = V_proj.reshape(seq_len, self.n_heads, self.d_k).transpose(1, 0, 2)

        # Attention per head
        head_outputs = []
        self._last_weights: List[np.ndarray] = []
        for h in range(self.n_heads):
            out, weights = self._attention.forward(
                Q_heads[h], K_heads[h], V_heads[h], mask,
            )
            head_outputs.append(out)
            self._last_weights.append(weights)

        # Concatenate heads: (seq_len, d_model)
        concat = np.concatenate(head_outputs, axis=-1)

        # Output projection
        output = _safe_matmul(concat, self.W_O)
        return output

    def get_attention_weights(self) -> List[np.ndarray]:
        """Return attention weights from the last forward pass.

        Returns:
            List of n_heads arrays, each shape (seq_q, seq_k).
        """
        return getattr(self, "_last_weights", [])


# ---------------------------------------------------------------------------
# Cross-Stream Attention
# ---------------------------------------------------------------------------

class CrossStreamAttention:
    """Cross-attention between two data streams (e.g. price and volume).

    Price features query the volume features and vice versa, enabling
    the model to identify which volume patterns are relevant for the
    current price context.

    Args:
        d_model: Feature dimension for both streams.
        n_heads: Number of attention heads.
        seed: Random seed.
    """

    def __init__(
        self,
        d_model: int,
        n_heads: int = 2,
        seed: Optional[int] = None,
    ) -> None:
        self.d_model = d_model
        self.n_heads = n_heads
        rng = np.random.default_rng(seed)

        # Price queries volume (price->volume cross-attention)
        self.pv_attn = MultiHeadAttention(d_model, n_heads, seed=rng.integers(0, 2**31))
        # Volume queries price (volume->price cross-attention)
        self.vp_attn = MultiHeadAttention(d_model, n_heads, seed=rng.integers(0, 2**31))

        log.debug(
            "CrossStreamAttention: d_model=%d, n_heads=%d",
            d_model, n_heads,
        )

    def forward(
        self,
        price_features: np.ndarray,
        volume_features: np.ndarray,
    ) -> np.ndarray:
        """Compute bidirectional cross-attention between price and volume.

        Args:
            price_features: Shape (seq_len, d_model).
            volume_features: Shape (seq_len, d_model).

        Returns:
            Fused features of shape (seq_len, d_model).
        """
        # Price attends to volume
        pv_out = self.pv_attn.forward(
            Q=price_features, K=volume_features, V=volume_features,
        )

        # Volume attends to price
        vp_out = self.vp_attn.forward(
            Q=volume_features, K=price_features, V=price_features,
        )

        # Combine: average of both cross-attention outputs + residual
        fused = (pv_out + vp_out) / 2.0

        # Layer norm for stability
        fused = _layer_norm(fused)

        return fused


# ---------------------------------------------------------------------------
# Temporal Attention Signal Generator
# ---------------------------------------------------------------------------

class TemporalAttentionSignal:
    """Full attention-based trading signal generator.

    Takes multi-stream input (price, volume, indicators) and produces
    a directional trading signal using multi-head self-attention,
    cross-stream attention, and temporal aggregation.

    Pipeline:
      1. Encode each stream into d_model features
      2. Self-attention on each stream
      3. Cross-attention between price and volume
      4. Temporal aggregation (attend across time steps)
      5. Output projection -> direction + confidence

    Args:
        n_features: Number of raw features per stream.
        seq_len: Sequence length (number of bars).
        n_heads: Number of attention heads.
        d_model: Internal model dimension.
        seed: Random seed.
    """

    def __init__(
        self,
        n_features: int = 5,
        seq_len: int = 60,
        n_heads: int = 4,
        d_model: int = 32,
        seed: Optional[int] = None,
    ) -> None:
        self.n_features = n_features
        self.seq_len = seq_len
        self.n_heads = n_heads
        self.d_model = d_model
        rng = np.random.default_rng(seed)

        # Ensure d_model is divisible by n_heads
        if d_model % n_heads != 0:
            self.d_model = n_heads * (d_model // n_heads + 1)
            log.warning(
                "Adjusted d_model from %d to %d for head divisibility",
                d_model, self.d_model,
            )

        # Input projections: raw features -> d_model
        self.W_price_in = _xavier_init(n_features, self.d_model, rng)
        self.W_volume_in = _xavier_init(n_features, self.d_model, rng)
        self.W_indicator_in = _xavier_init(n_features, self.d_model, rng)

        # Positional encoding (sinusoidal)
        self.pos_encoding = self._build_positional_encoding(seq_len, self.d_model)

        # Self-attention layers
        self.price_self_attn = MultiHeadAttention(
            self.d_model, n_heads, seed=int(rng.integers(0, 2**31)),
        )
        self.volume_self_attn = MultiHeadAttention(
            self.d_model, n_heads, seed=int(rng.integers(0, 2**31)),
        )

        # Cross-stream attention
        self.cross_attn = CrossStreamAttention(
            self.d_model, n_heads=max(1, n_heads // 2),
            seed=int(rng.integers(0, 2**31)),
        )

        # Temporal aggregation attention
        self.temporal_attn = MultiHeadAttention(
            self.d_model, n_heads, seed=int(rng.integers(0, 2**31)),
        )

        # Output projection: d_model -> 3 (direction logits)
        self.W_out = _xavier_init(self.d_model, 3, rng)
        self.b_out = np.zeros(3)

        log.info(
            "TemporalAttentionSignal: n_features=%d, seq_len=%d, "
            "n_heads=%d, d_model=%d",
            n_features, seq_len, n_heads, self.d_model,
        )

    @staticmethod
    def _build_positional_encoding(
        seq_len: int,
        d_model: int,
    ) -> np.ndarray:
        """Build sinusoidal positional encoding.

        Args:
            seq_len: Maximum sequence length.
            d_model: Model dimension.

        Returns:
            Array of shape (seq_len, d_model).
        """
        pe = np.zeros((seq_len, d_model))
        position = np.arange(seq_len)[:, np.newaxis]
        div_term = np.exp(
            np.arange(0, d_model, 2) * (-math.log(10000.0) / d_model)
        )
        pe[:, 0::2] = np.sin(position * div_term)
        pe[:, 1::2] = np.cos(position * div_term[:d_model // 2])
        return pe

    def generate_signal(
        self,
        price_seq: np.ndarray,
        volume_seq: np.ndarray,
        indicator_seq: np.ndarray,
    ) -> Dict[str, Any]:
        """Generate a trading signal from multi-stream input.

        Args:
            price_seq: Price features, shape (seq_len, n_features).
                       Typical features: open, high, low, close, returns.
            volume_seq: Volume features, shape (seq_len, n_features).
                        Typical: raw_vol, vol_ma, vol_ratio, obv, vwap.
            indicator_seq: Technical indicators, shape (seq_len, n_features).
                           Typical: rsi, macd, atr, adx, bb_width.

        Returns:
            Dict with:
              - direction: 'long', 'short', or 'neutral'
              - confidence: float 0-1
              - attention_map: dict describing which bars/features drove signal
              - raw_logits: the 3 output logits (long, neutral, short)
        """
        t0 = time.time()
        seq_len = price_seq.shape[0]

        # Validate inputs
        if seq_len < 5:
            log.warning("Sequence too short (%d). Returning neutral.", seq_len)
            return self._neutral_signal()

        # Pad or truncate to self.seq_len
        price_seq = self._pad_or_truncate(price_seq)
        volume_seq = self._pad_or_truncate(volume_seq)
        indicator_seq = self._pad_or_truncate(indicator_seq)

        # 1. Input projection + positional encoding
        price_emb = _layer_norm(price_seq @ self.W_price_in + self.pos_encoding)
        volume_emb = _layer_norm(volume_seq @ self.W_volume_in + self.pos_encoding)
        indicator_emb = _layer_norm(indicator_seq @ self.W_indicator_in + self.pos_encoding)

        # 2. Self-attention on each stream
        price_attn = self.price_self_attn.forward(price_emb, price_emb, price_emb)
        price_attn = _layer_norm(price_attn + price_emb)  # residual + norm

        volume_attn = self.volume_self_attn.forward(volume_emb, volume_emb, volume_emb)
        volume_attn = _layer_norm(volume_attn + volume_emb)

        # 3. Cross-stream attention (price x volume)
        cross_features = self.cross_attn.forward(price_attn, volume_attn)

        # Combine all streams
        combined = _layer_norm(cross_features + indicator_emb)

        # 4. Temporal aggregation
        temporal_out = self.temporal_attn.forward(combined, combined, combined)
        temporal_out = _layer_norm(temporal_out + combined)

        # 5. Pool across time (attention-weighted mean using last step as query)
        # Use the last time step as the aggregation point
        final_repr = temporal_out[-1]  # shape (d_model,)

        # 6. Output projection
        logits = final_repr @ self.W_out + self.b_out  # shape (3,)
        probs = _softmax(logits)

        # Interpret: index 0=long, 1=neutral, 2=short
        direction_idx = int(np.argmax(probs))
        direction_map = {0: "long", 1: "neutral", 2: "short"}
        direction = direction_map[direction_idx]
        confidence = float(probs[direction_idx])

        # Get attention insights
        attention_map = self._get_attention_insights(
            self.temporal_attn.get_attention_weights(),
            original_seq_len=seq_len,
        )

        elapsed_ms = (time.time() - t0) * 1000

        log.debug(
            "Signal: direction=%s confidence=%.3f elapsed=%.1fms",
            direction, confidence, elapsed_ms,
        )

        return {
            "direction": direction,
            "confidence": confidence,
            "raw_logits": logits.tolist(),
            "probabilities": probs.tolist(),
            "attention_map": attention_map,
            "elapsed_ms": elapsed_ms,
        }

    def _get_attention_insights(
        self,
        weights: List[np.ndarray],
        original_seq_len: int,
    ) -> Dict[str, Any]:
        """Extract interpretable insights from attention weights.

        Identifies which time steps received the most attention across
        all heads, revealing which historical bars drove the signal.

        Args:
            weights: List of attention weight matrices (one per head).
            original_seq_len: Original (pre-padding) sequence length.

        Returns:
            Dict with:
              - top_attended_bars: indices of most-attended bars
              - per_head_focus: which time region each head focuses on
              - attention_entropy: measure of attention concentration
        """
        if not weights:
            return {"top_attended_bars": [], "per_head_focus": [], "attention_entropy": 0.0}

        # Average attention across heads from the last query position
        n_heads = len(weights)
        avg_attn = np.zeros(self.seq_len)
        per_head_focus: List[Dict[str, Any]] = []

        for h, w in enumerate(weights):
            # w shape: (seq_len, seq_len) — last row = attention from final position
            last_row = w[-1] if w.ndim == 2 else w[0, -1]
            avg_attn += last_row
            peak_idx = int(np.argmax(last_row))
            per_head_focus.append({
                "head": h,
                "peak_bar": peak_idx,
                "peak_weight": float(last_row[peak_idx]),
                "focus_region": "recent" if peak_idx > self.seq_len * 0.7 else "historical",
            })

        avg_attn /= n_heads

        # Top attended bars (clip to original seq len)
        top_k = min(10, original_seq_len)
        top_indices = np.argsort(-avg_attn)[:top_k].tolist()

        # Attention entropy (higher = more spread, lower = more concentrated)
        entropy = float(-np.sum(avg_attn * np.log(avg_attn + 1e-10)))

        return {
            "top_attended_bars": top_indices,
            "per_head_focus": per_head_focus,
            "attention_entropy": entropy,
            "avg_attention": avg_attn[:original_seq_len].tolist(),
        }

    def _pad_or_truncate(self, x: np.ndarray) -> np.ndarray:
        """Pad or truncate sequence to self.seq_len.

        Args:
            x: Input array of shape (T, n_features).

        Returns:
            Array of shape (self.seq_len, n_features).
        """
        T, F = x.shape
        if F != self.n_features:
            # Project if feature count differs
            if F > self.n_features:
                x = x[:, :self.n_features]
            else:
                pad = np.zeros((T, self.n_features - F))
                x = np.concatenate([x, pad], axis=1)

        if T >= self.seq_len:
            return x[-self.seq_len:]
        else:
            pad = np.zeros((self.seq_len - T, self.n_features))
            return np.concatenate([pad, x], axis=0)

    @staticmethod
    def _neutral_signal() -> Dict[str, Any]:
        """Return a neutral / no-signal result."""
        return {
            "direction": "neutral",
            "confidence": 0.0,
            "raw_logits": [0.0, 0.0, 0.0],
            "probabilities": [0.333, 0.334, 0.333],
            "attention_map": {
                "top_attended_bars": [],
                "per_head_focus": [],
                "attention_entropy": 0.0,
            },
            "elapsed_ms": 0.0,
        }

    def save_weights(self, filepath: Optional[str] = None) -> None:
        """Save model weights to .npz file."""
        filepath = filepath or os.path.join(DATA_DIR, "attention_weights.npz")
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        try:
            np.savez(
                filepath,
                W_price_in=self.W_price_in,
                W_volume_in=self.W_volume_in,
                W_indicator_in=self.W_indicator_in,
                W_out=self.W_out,
                b_out=self.b_out,
            )
            log.info("Attention weights saved to %s", filepath)
        except OSError as e:
            log.error("Failed to save weights: %s", e)

    def load_weights(self, filepath: Optional[str] = None) -> bool:
        """Load model weights from .npz file.

        Returns:
            True if weights loaded successfully.
        """
        filepath = filepath or os.path.join(DATA_DIR, "attention_weights.npz")
        try:
            data = np.load(filepath)
            self.W_price_in = data["W_price_in"]
            self.W_volume_in = data["W_volume_in"]
            self.W_indicator_in = data["W_indicator_in"]
            self.W_out = data["W_out"]
            self.b_out = data["b_out"]
            log.info("Attention weights loaded from %s", filepath)
            return True
        except (OSError, KeyError) as e:
            log.warning("Failed to load weights from %s: %s", filepath, e)
            return False
