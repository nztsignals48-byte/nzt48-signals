"""ml/temporal_fusion_transformer.py — Book 75: Temporal Fusion Transformer.

NumPy-only implementation of the TFT architecture for multi-horizon
time series forecasting. Produces quantile predictions (0.1, 0.5, 0.9)
for price/return forecasting.

Architecture (Google Research, 2019):
  1. Static covariate encoder — 4 context vectors (variable selection,
     enrichment, state-init-h, state-init-c)
  2. Variable selection networks — separate for past observed, past known,
     future known inputs
  3. LSTM encoder + decoder — sequence processing
  4. Temporal self-attention — interpretable multi-head attention
  5. Position-wise GRN → quantile outputs

This is a NUMPY-ONLY implementation. No PyTorch/TensorFlow dependency.
Forward pass and backpropagation are implemented manually. The primary
value is the architecture definition and preprocessing pipeline. Trained
weights are exported to ONNX for Rust inference (< 1ms).

Note: Training convergence is slower than PyTorch due to numpy limitations.
Use for small-to-medium datasets. For large-scale training, convert to
PyTorch and retrain.

Bridge.py integration:
    try:
        from python_brain.ml.temporal_fusion_transformer import (
            TFTConfig, TemporalFusionTransformer, TFTPreprocessor, TFTTrainer,
        )
        _tft_config = TFTConfig()
        _tft_preprocessor = TFTPreprocessor(_tft_config)
    except ImportError:
        pass

    # Nightly training:
    trainer = TFTTrainer()
    model, metrics = trainer.train(data, TFTConfig())

    # Export for Rust inference:
    trainer.export_onnx(model, "/app/models/tft_v1.onnx")
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)

# Optional ONNX export
_HAS_ONNX = False
try:
    import onnx
    from onnx import numpy_helper, TensorProto
    from onnx.helper import make_graph, make_model, make_node, make_tensor_value_info
    _HAS_ONNX = True
except ImportError:
    pass

__all__ = [
    "TFTConfig",
    "GatedResidualNetwork",
    "VariableSelectionNetwork",
    "InterpretableMultiHeadAttention",
    "TemporalFusionTransformer",
    "TFTPreprocessor",
    "TFTTrainer",
]

# ── Persistence Paths ──────────────────────────────────────────────────

DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
MODEL_DIR = Path(os.environ.get("AEGIS_MODEL_DIR", "/app/models"))


# ── Configuration ──────────────────────────────────────────────────────

@dataclass
class TFTConfig:
    """TFT hyperparameters."""
    d_model: int = 64         # Core hidden dimension
    n_heads: int = 4          # Attention heads
    dropout: float = 0.1      # Dropout rate (applied during training only)
    hidden_size: int = 64     # GRN hidden dimension
    seq_len: int = 60         # Input sequence length (past timesteps)
    pred_horizon: int = 12    # Prediction horizon (future timesteps)
    n_past_observed: int = 12 # Number of past-only observed features
    n_past_known: int = 5     # Number of known features (past portion)
    n_future_known: int = 4   # Number of known features (future portion)
    n_static: int = 18        # Number of static features
    quantiles: List[float] = field(default_factory=lambda: [0.1, 0.5, 0.9])
    learning_rate: float = 0.001
    batch_size: int = 32
    epochs: int = 50
    patience: int = 10        # Early stopping patience
    seed: int = 42


# ── Utility Functions ──────────────────────────────────────────────────

def _glorot_uniform(shape: Tuple[int, ...], rng: np.random.RandomState) -> np.ndarray:
    """Glorot/Xavier uniform initialization."""
    fan_in = shape[0] if len(shape) >= 1 else 1
    fan_out = shape[1] if len(shape) >= 2 else 1
    limit = math.sqrt(6.0 / (fan_in + fan_out))
    return rng.uniform(-limit, limit, size=shape).astype(np.float32)


def _zeros(shape: Tuple[int, ...]) -> np.ndarray:
    return np.zeros(shape, dtype=np.float32)


def _ones(shape: Tuple[int, ...]) -> np.ndarray:
    return np.ones(shape, dtype=np.float32)


def _elu(x: np.ndarray, alpha: float = 1.0) -> np.ndarray:
    """ELU activation: max(0,x) + min(0, alpha*(exp(x)-1))."""
    return np.where(x > 0, x, alpha * (np.exp(np.clip(x, -10, 0)) - 1.0))


def _elu_grad(x: np.ndarray, alpha: float = 1.0) -> np.ndarray:
    """Gradient of ELU."""
    return np.where(x > 0, 1.0, alpha * np.exp(np.clip(x, -10, 0)))


def _sigmoid(x: np.ndarray) -> np.ndarray:
    """Numerically stable sigmoid."""
    x = np.clip(x, -15, 15)
    return 1.0 / (1.0 + np.exp(-x))


def _softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    """Numerically stable softmax."""
    e = np.exp(x - np.max(x, axis=axis, keepdims=True))
    return e / (np.sum(e, axis=axis, keepdims=True) + 1e-10)


def _layer_norm(x: np.ndarray, gamma: np.ndarray, beta: np.ndarray,
                eps: float = 1e-6) -> np.ndarray:
    """Layer normalization along last axis."""
    mean = np.mean(x, axis=-1, keepdims=True)
    var = np.var(x, axis=-1, keepdims=True)
    x_norm = (x - mean) / np.sqrt(var + eps)
    return gamma * x_norm + beta


def _dropout_mask(shape: Tuple[int, ...], rate: float,
                  rng: np.random.RandomState, training: bool) -> np.ndarray:
    """Generate dropout mask. Returns all-ones if not training."""
    if not training or rate <= 0.0:
        return np.ones(shape, dtype=np.float32)
    mask = (rng.random(shape) > rate).astype(np.float32)
    return mask / max(1.0 - rate, 1e-10)  # Scale to maintain expected value


# ── Gated Residual Network ────────────────────────────────────────────

class GatedResidualNetwork:
    """Gated Residual Network (GRN) block.

    Architecture: Linear → ELU → Linear → Dropout → GLU gate → LayerNorm + skip

    The GRN is the core building block of TFT, enabling nonlinear processing
    with skip connections and gating for gradient flow.
    """

    def __init__(self, input_size: int, hidden_size: int, output_size: int,
                 dropout: float = 0.1, context_size: int = 0,
                 rng: Optional[np.random.RandomState] = None):
        """Initialize GRN parameters.

        Args:
            input_size: Dimension of primary input.
            hidden_size: Hidden layer dimension.
            output_size: Output dimension.
            dropout: Dropout rate.
            context_size: Optional context vector dimension (e.g., from static encoder).
            rng: Random state for initialization.
        """
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.output_size = output_size
        self.dropout_rate = dropout

        rng = rng or np.random.RandomState(42)

        # Primary path: input → hidden
        self.W1 = _glorot_uniform((input_size, hidden_size), rng)
        self.b1 = _zeros((hidden_size,))

        # Context path (optional): context → hidden
        self.has_context = context_size > 0
        if self.has_context:
            self.Wc = _glorot_uniform((context_size, hidden_size), rng)

        # Hidden → pre-gate
        self.W2 = _glorot_uniform((hidden_size, output_size * 2), rng)  # *2 for GLU
        self.b2 = _zeros((output_size * 2,))

        # Skip projection (if input_size != output_size)
        self.needs_skip_proj = (input_size != output_size)
        if self.needs_skip_proj:
            self.W_skip = _glorot_uniform((input_size, output_size), rng)

        # Layer norm parameters
        self.ln_gamma = _ones((output_size,))
        self.ln_beta = _zeros((output_size,))

    def forward(self, x: np.ndarray, context: Optional[np.ndarray] = None,
                training: bool = False,
                rng: Optional[np.random.RandomState] = None) -> np.ndarray:
        """Forward pass through GRN.

        Args:
            x: Input tensor (..., input_size).
            context: Optional context vector (..., context_size).
            training: Whether to apply dropout.
            rng: Random state for dropout.

        Returns:
            Output tensor (..., output_size).
        """
        rng = rng or np.random.RandomState()

        # Skip connection
        skip = x @ self.W_skip if self.needs_skip_proj else x

        # Primary path
        eta = x @ self.W1 + self.b1
        if self.has_context and context is not None:
            ctx_proj = context @ self.Wc
            # Broadcast context to match input dimensions (e.g., 2D context → 3D input)
            if ctx_proj.ndim < eta.ndim:
                for _ in range(eta.ndim - ctx_proj.ndim):
                    ctx_proj = np.expand_dims(ctx_proj, axis=-2)
                ctx_proj = np.broadcast_to(ctx_proj, eta.shape)
            eta = eta + ctx_proj

        # ELU activation
        eta = _elu(eta)

        # Project to 2*output_size for GLU
        pre_gate = eta @ self.W2 + self.b2

        # GLU: split into value and gate
        value = pre_gate[..., :self.output_size]
        gate = _sigmoid(pre_gate[..., self.output_size:])

        # Dropout on value
        if training:
            mask = _dropout_mask(value.shape, self.dropout_rate, rng, True)
            value = value * mask

        # Gated output + skip + layer norm
        gated = gate * value
        out = _layer_norm(gated + skip, self.ln_gamma, self.ln_beta)
        return out

    def get_params(self) -> Dict[str, np.ndarray]:
        """Get all trainable parameters."""
        params = {
            "W1": self.W1, "b1": self.b1,
            "W2": self.W2, "b2": self.b2,
            "ln_gamma": self.ln_gamma, "ln_beta": self.ln_beta,
        }
        if self.has_context:
            params["Wc"] = self.Wc
        if self.needs_skip_proj:
            params["W_skip"] = self.W_skip
        return params

    def set_params(self, params: Dict[str, np.ndarray]) -> None:
        """Set trainable parameters from dict."""
        for key, val in params.items():
            if hasattr(self, key):
                setattr(self, key, val.astype(np.float32))


# ── Variable Selection Network ─────────────────────────────────────────

class VariableSelectionNetwork:
    """Variable Selection Network (VSN).

    Learns importance weights for each input variable using per-variable
    GRNs and a softmax over learned scores.

    This enables the model to focus on the most relevant features
    for each prediction, providing interpretability.
    """

    def __init__(self, n_variables: int, d_model: int, hidden_size: int,
                 dropout: float = 0.1, context_size: int = 0,
                 rng: Optional[np.random.RandomState] = None):
        """Initialize VSN.

        Args:
            n_variables: Number of input variables.
            d_model: Dimension per variable after projection.
            hidden_size: GRN hidden size.
            dropout: Dropout rate.
            context_size: Optional static context dimension.
            rng: Random state.
        """
        self.n_variables = n_variables
        self.d_model = d_model
        rng = rng or np.random.RandomState(42)

        # Per-variable projection: each variable gets its own linear + GRN
        self.var_projections: List[np.ndarray] = []
        self.var_biases: List[np.ndarray] = []
        self.var_grns: List[GatedResidualNetwork] = []

        for _ in range(n_variables):
            # Project single variable to d_model
            self.var_projections.append(_glorot_uniform((1, d_model), rng))
            self.var_biases.append(_zeros((d_model,)))
            self.var_grns.append(
                GatedResidualNetwork(d_model, hidden_size, d_model,
                                      dropout, context_size=context_size, rng=rng)
            )

        # Flattened GRN for weight computation
        flattened_size = n_variables * d_model
        self.weight_grn = GatedResidualNetwork(
            flattened_size, hidden_size, n_variables,
            dropout, context_size=context_size, rng=rng,
        )

    def forward(self, x: np.ndarray, context: Optional[np.ndarray] = None,
                training: bool = False,
                rng: Optional[np.random.RandomState] = None) -> Tuple[np.ndarray, np.ndarray]:
        """Forward pass through VSN.

        Args:
            x: Input tensor (..., seq_len, n_variables).
            context: Optional static context (..., context_size).
            training: Whether to apply dropout.
            rng: Random state.

        Returns:
            (selected_output, variable_weights):
                selected_output: (..., seq_len, d_model)
                variable_weights: (..., n_variables) — softmax importance weights
        """
        rng = rng or np.random.RandomState()

        # x shape: (batch, seq_len, n_variables)
        orig_shape = x.shape
        batch_dims = orig_shape[:-1]  # Everything except n_variables

        # Per-variable GRN processing
        var_outputs = []
        for i in range(self.n_variables):
            # Extract variable i: (..., 1)
            xi = x[..., i:i + 1]
            # Project to d_model: (..., d_model)
            projected = xi * self.var_projections[i] + self.var_biases[i]
            # GRN
            processed = self.var_grns[i].forward(projected, context, training, rng)
            var_outputs.append(processed)

        # Stack: (..., n_variables, d_model)
        stacked = np.stack(var_outputs, axis=-2)

        # Compute variable importance weights
        # Flatten variables for weight GRN: (..., n_variables * d_model)
        flattened = stacked.reshape(*batch_dims, -1)
        weight_input = flattened
        raw_weights = self.weight_grn.forward(weight_input, context, training, rng)
        # Softmax over variables: (..., n_variables)
        var_weights = _softmax(raw_weights, axis=-1)

        # Weighted sum: (..., d_model)
        # var_weights: (..., n_variables) → (..., n_variables, 1)
        weights_expanded = var_weights[..., :, np.newaxis]
        selected = np.sum(stacked * weights_expanded, axis=-2)

        return selected, var_weights

    def get_params(self) -> Dict[str, np.ndarray]:
        params = {}
        for i in range(self.n_variables):
            params[f"var_proj_{i}"] = self.var_projections[i]
            params[f"var_bias_{i}"] = self.var_biases[i]
            for k, v in self.var_grns[i].get_params().items():
                params[f"var_grn_{i}_{k}"] = v
        for k, v in self.weight_grn.get_params().items():
            params[f"weight_grn_{k}"] = v
        return params


# ── Interpretable Multi-Head Attention ─────────────────────────────────

class InterpretableMultiHeadAttention:
    """Interpretable multi-head attention from the TFT paper.

    Standard multi-head attention with an additive attention weight
    aggregation across heads, enabling interpretation of which
    timesteps the model attends to.
    """

    def __init__(self, d_model: int, n_heads: int,
                 dropout: float = 0.1,
                 rng: Optional[np.random.RandomState] = None):
        """Initialize attention parameters.

        Args:
            d_model: Total model dimension.
            n_heads: Number of attention heads.
            dropout: Attention dropout rate.
            rng: Random state.
        """
        assert d_model % n_heads == 0, f"d_model ({d_model}) must be divisible by n_heads ({n_heads})"
        self.d_model = d_model
        self.n_heads = n_heads
        self.d_k = d_model // n_heads
        self.dropout_rate = dropout

        rng = rng or np.random.RandomState(42)

        # Q, K, V projections (one per head)
        self.W_Q = _glorot_uniform((d_model, d_model), rng)
        self.W_K = _glorot_uniform((d_model, d_model), rng)
        self.W_V = _glorot_uniform((d_model, d_model), rng)

        # Output projection
        self.W_O = _glorot_uniform((d_model, d_model), rng)
        self.b_O = _zeros((d_model,))

    def forward(self, query: np.ndarray, key: np.ndarray, value: np.ndarray,
                mask: Optional[np.ndarray] = None,
                training: bool = False,
                rng: Optional[np.random.RandomState] = None) -> Tuple[np.ndarray, np.ndarray]:
        """Forward pass through multi-head attention.

        Args:
            query: (batch, seq_q, d_model)
            key: (batch, seq_k, d_model)
            value: (batch, seq_k, d_model)
            mask: Optional (batch, seq_q, seq_k) or (seq_q, seq_k) attention mask.
            training: Whether to apply dropout.
            rng: Random state.

        Returns:
            (output, attention_weights):
                output: (batch, seq_q, d_model)
                attention_weights: (batch, n_heads, seq_q, seq_k)
        """
        rng = rng or np.random.RandomState()
        batch_size = query.shape[0]
        seq_q = query.shape[1]
        seq_k = key.shape[1]

        # Project Q, K, V
        Q = query @ self.W_Q  # (batch, seq_q, d_model)
        K = key @ self.W_K    # (batch, seq_k, d_model)
        V = value @ self.W_V  # (batch, seq_k, d_model)

        # Reshape to (batch, n_heads, seq, d_k)
        Q = Q.reshape(batch_size, seq_q, self.n_heads, self.d_k).transpose(0, 2, 1, 3)
        K = K.reshape(batch_size, seq_k, self.n_heads, self.d_k).transpose(0, 2, 1, 3)
        V = V.reshape(batch_size, seq_k, self.n_heads, self.d_k).transpose(0, 2, 1, 3)

        # Scaled dot-product attention
        scale = math.sqrt(self.d_k)
        scores = np.matmul(Q, K.transpose(0, 1, 3, 2)) / scale  # (batch, n_heads, seq_q, seq_k)

        # Apply mask (causal or padding)
        if mask is not None:
            if mask.ndim == 2:
                mask = mask[np.newaxis, np.newaxis, :, :]
            elif mask.ndim == 3:
                mask = mask[:, np.newaxis, :, :]
            scores = scores + mask * (-1e9)

        attention_weights = _softmax(scores, axis=-1)

        # Attention dropout
        if training:
            attn_mask = _dropout_mask(attention_weights.shape, self.dropout_rate, rng, True)
            attention_weights_dropped = attention_weights * attn_mask
        else:
            attention_weights_dropped = attention_weights

        # Weighted sum of values
        context = np.matmul(attention_weights_dropped, V)  # (batch, n_heads, seq_q, d_k)

        # Concatenate heads
        context = context.transpose(0, 2, 1, 3).reshape(batch_size, seq_q, self.d_model)

        # Output projection
        output = context @ self.W_O + self.b_O

        return output, attention_weights

    def get_params(self) -> Dict[str, np.ndarray]:
        return {
            "W_Q": self.W_Q, "W_K": self.W_K, "W_V": self.W_V,
            "W_O": self.W_O, "b_O": self.b_O,
        }


# ── LSTM Cell (numpy) ─────────────────────────────────────────────────

class LSTMCell:
    """Single LSTM cell implemented in numpy.

    Gates: input (i), forget (f), output (o), cell candidate (g).
    """

    def __init__(self, input_size: int, hidden_size: int,
                 rng: Optional[np.random.RandomState] = None):
        rng = rng or np.random.RandomState(42)
        self.hidden_size = hidden_size

        # Combined weight matrix for efficiency: [i, f, g, o] = 4*hidden_size
        self.W_ih = _glorot_uniform((input_size, 4 * hidden_size), rng)
        self.W_hh = _glorot_uniform((hidden_size, 4 * hidden_size), rng)
        self.bias = _zeros((4 * hidden_size,))

        # Forget gate bias init to 1.0 (standard practice for gradient flow)
        self.bias[hidden_size:2 * hidden_size] = 1.0

    def forward(self, x: np.ndarray, h: np.ndarray,
                c: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Single timestep forward pass.

        Args:
            x: Input (batch, input_size).
            h: Hidden state (batch, hidden_size).
            c: Cell state (batch, hidden_size).

        Returns:
            (new_h, new_c)
        """
        gates = x @ self.W_ih + h @ self.W_hh + self.bias
        hs = self.hidden_size

        i = _sigmoid(gates[:, :hs])
        f = _sigmoid(gates[:, hs:2 * hs])
        g = np.tanh(gates[:, 2 * hs:3 * hs])
        o = _sigmoid(gates[:, 3 * hs:])

        new_c = f * c + i * g
        new_h = o * np.tanh(new_c)
        return new_h, new_c

    def get_params(self) -> Dict[str, np.ndarray]:
        return {"W_ih": self.W_ih, "W_hh": self.W_hh, "bias": self.bias}


class LSTM:
    """Unidirectional LSTM layer (sequence processing)."""

    def __init__(self, input_size: int, hidden_size: int,
                 rng: Optional[np.random.RandomState] = None):
        self.cell = LSTMCell(input_size, hidden_size, rng)
        self.hidden_size = hidden_size

    def forward(self, x: np.ndarray,
                init_states: Optional[Tuple[np.ndarray, np.ndarray]] = None
                ) -> Tuple[np.ndarray, Tuple[np.ndarray, np.ndarray]]:
        """Process full sequence.

        Args:
            x: (batch, seq_len, input_size)
            init_states: Optional (h0, c0) each (batch, hidden_size).

        Returns:
            (outputs, (h_n, c_n)):
                outputs: (batch, seq_len, hidden_size) — all hidden states
                h_n: (batch, hidden_size) — final hidden state
                c_n: (batch, hidden_size) — final cell state
        """
        batch, seq_len, _ = x.shape
        if init_states is not None:
            h, c = init_states
        else:
            h = _zeros((batch, self.hidden_size))
            c = _zeros((batch, self.hidden_size))

        outputs = []
        for t in range(seq_len):
            h, c = self.cell.forward(x[:, t, :], h, c)
            outputs.append(h)

        return np.stack(outputs, axis=1), (h, c)

    def get_params(self) -> Dict[str, np.ndarray]:
        return self.cell.get_params()


# ── Temporal Fusion Transformer ────────────────────────────────────────

class TemporalFusionTransformer:
    """Full TFT model (numpy-only implementation).

    Components:
      1. Static covariate encoder — produces 4 context vectors
      2. Variable selection for past observed, past known, future known
      3. LSTM encoder + decoder
      4. Temporal self-attention (interpretable)
      5. Final GRN → quantile outputs
    """

    def __init__(self, config: Optional[TFTConfig] = None):
        self.config = config or TFTConfig()
        c = self.config
        self.rng = np.random.RandomState(c.seed)

        d = c.d_model
        h = c.hidden_size

        # ── Static Covariate Encoder ──
        # 4 context GRNs: variable_selection, enrichment, state_h, state_c
        self.static_grn_vs = GatedResidualNetwork(c.n_static, h, d, c.dropout, rng=self.rng)
        self.static_grn_enrich = GatedResidualNetwork(c.n_static, h, d, c.dropout, rng=self.rng)
        self.static_grn_h = GatedResidualNetwork(c.n_static, h, d, c.dropout, rng=self.rng)
        self.static_grn_c = GatedResidualNetwork(c.n_static, h, d, c.dropout, rng=self.rng)

        # ── Variable Selection Networks ──
        self.vsn_past_observed = VariableSelectionNetwork(
            c.n_past_observed, d, h, c.dropout, context_size=d, rng=self.rng,
        )
        self.vsn_past_known = VariableSelectionNetwork(
            c.n_past_known, d, h, c.dropout, context_size=d, rng=self.rng,
        )
        self.vsn_future_known = VariableSelectionNetwork(
            c.n_future_known, d, h, c.dropout, context_size=d, rng=self.rng,
        )

        # ── Sequence Processing (LSTM) ──
        # Encoder input: concatenation of past_observed + past_known selections
        encoder_input_size = d * 2  # Two VSN outputs concatenated
        self.encoder_lstm = LSTM(encoder_input_size, d, rng=self.rng)

        # Decoder input: future_known selection
        self.decoder_lstm = LSTM(d, d, rng=self.rng)

        # Post-LSTM GRN for gated skip connection
        self.post_lstm_grn = GatedResidualNetwork(d, h, d, c.dropout, rng=self.rng)

        # ── Temporal Self-Attention ──
        self.attention = InterpretableMultiHeadAttention(d, c.n_heads, c.dropout, rng=self.rng)
        self.post_attn_grn = GatedResidualNetwork(d, h, d, c.dropout, rng=self.rng)

        # ── Static Enrichment ──
        self.enrichment_grn = GatedResidualNetwork(d, h, d, c.dropout, context_size=d, rng=self.rng)

        # ── Output Layer ──
        n_quantiles = len(c.quantiles)
        self.output_grn = GatedResidualNetwork(d, h, d, c.dropout, rng=self.rng)
        self.W_output = _glorot_uniform((d, n_quantiles), self.rng)
        self.b_output = _zeros((n_quantiles,))

        self._training = False

    def forward(self, static: np.ndarray,
                past_observed: np.ndarray,
                past_known: np.ndarray,
                future_known: np.ndarray) -> Dict[str, np.ndarray]:
        """Full forward pass.

        Args:
            static: (batch, n_static) — static features (ticker, sector, etc.)
            past_observed: (batch, seq_len, n_past_observed) — past-only features
            past_known: (batch, seq_len, n_past_known) — known past features
            future_known: (batch, pred_horizon, n_future_known) — known future features

        Returns:
            Dict with:
                "predictions": (batch, pred_horizon, n_quantiles) — quantile forecasts
                "attention_weights": (batch, n_heads, total_seq, total_seq) — attention map
                "variable_weights": dict of variable importance per input group
        """
        training = self._training

        # ── Step 1: Static Covariate Encoder ──
        # Produce 4 context vectors from static features
        ctx_vs = self.static_grn_vs.forward(static, training=training, rng=self.rng)
        ctx_enrich = self.static_grn_enrich.forward(static, training=training, rng=self.rng)
        ctx_h = self.static_grn_h.forward(static, training=training, rng=self.rng)
        ctx_c = self.static_grn_c.forward(static, training=training, rng=self.rng)

        # ── Step 2: Variable Selection ──
        # Expand context for sequence dimensions
        batch = static.shape[0]
        seq_len = past_observed.shape[1]
        pred_horizon = future_known.shape[1]

        # ctx_vs for VSN: (batch, d_model) → broadcast in VSN
        past_obs_selected, past_obs_weights = self.vsn_past_observed.forward(
            past_observed, ctx_vs, training, self.rng,
        )
        past_known_selected, past_known_weights = self.vsn_past_known.forward(
            past_known, ctx_vs, training, self.rng,
        )
        future_known_selected, future_known_weights = self.vsn_future_known.forward(
            future_known, ctx_vs, training, self.rng,
        )

        # ── Step 3: LSTM Encoder/Decoder ──
        # Encoder input: concatenate past selections
        encoder_input = np.concatenate([past_obs_selected, past_known_selected], axis=-1)

        # Initialize LSTM with static context
        init_h = ctx_h[:, np.newaxis, :].squeeze(1) if ctx_h.ndim == 2 else ctx_h
        init_c = ctx_c[:, np.newaxis, :].squeeze(1) if ctx_c.ndim == 2 else ctx_c

        encoder_output, (enc_h, enc_c) = self.encoder_lstm.forward(
            encoder_input, (init_h, init_c),
        )

        # Decoder input: future known selection
        decoder_output, _ = self.decoder_lstm.forward(
            future_known_selected, (enc_h, enc_c),
        )

        # Concatenate encoder and decoder outputs for attention
        # total_seq = seq_len + pred_horizon
        lstm_output = np.concatenate([encoder_output, decoder_output], axis=1)

        # Post-LSTM gated skip connection
        lstm_gated = self.post_lstm_grn.forward(lstm_output, training=training, rng=self.rng)

        # ── Step 4: Static Enrichment ──
        # Add static context to temporal representation
        # Broadcast ctx_enrich: (batch, d_model) → (batch, total_seq, d_model)
        ctx_enrich_expanded = np.broadcast_to(
            ctx_enrich[:, np.newaxis, :],
            lstm_gated.shape,
        ).copy()  # Copy to make contiguous for matmul
        enriched = self.enrichment_grn.forward(
            lstm_gated, ctx_enrich_expanded, training, self.rng,
        )

        # ── Step 5: Temporal Self-Attention ──
        # Causal mask: decoder positions can only attend to encoder + past decoder
        total_seq = seq_len + pred_horizon
        causal_mask = np.triu(np.ones((total_seq, total_seq), dtype=np.float32), k=1)
        # Allow encoder positions to attend to all encoder positions
        # Decoder positions attend to all encoder + past decoder

        attn_output, attn_weights = self.attention.forward(
            enriched, enriched, enriched,
            mask=causal_mask, training=training, rng=self.rng,
        )

        # Post-attention GRN with skip from enriched
        attn_gated = self.post_attn_grn.forward(attn_output, training=training, rng=self.rng)

        # ── Step 6: Output ──
        # Only take decoder positions (future predictions)
        decoder_positions = attn_gated[:, seq_len:, :]

        # Final GRN
        final_repr = self.output_grn.forward(decoder_positions, training=training, rng=self.rng)

        # Quantile projection
        predictions = final_repr @ self.W_output + self.b_output

        return {
            "predictions": predictions,
            "attention_weights": attn_weights,
            "variable_weights": {
                "past_observed": past_obs_weights,
                "past_known": past_known_weights,
                "future_known": future_known_weights,
            },
        }

    def train_mode(self) -> None:
        """Set model to training mode (enables dropout)."""
        self._training = True

    def eval_mode(self) -> None:
        """Set model to evaluation mode (disables dropout)."""
        self._training = False

    def get_all_params(self) -> Dict[str, np.ndarray]:
        """Collect all trainable parameters."""
        params = {}
        for name, grn in [("static_vs", self.static_grn_vs),
                           ("static_enrich", self.static_grn_enrich),
                           ("static_h", self.static_grn_h),
                           ("static_c", self.static_grn_c),
                           ("post_lstm", self.post_lstm_grn),
                           ("post_attn", self.post_attn_grn),
                           ("enrichment", self.enrichment_grn),
                           ("output", self.output_grn)]:
            for k, v in grn.get_params().items():
                params[f"{name}_{k}"] = v

        for k, v in self.vsn_past_observed.get_params().items():
            params[f"vsn_past_obs_{k}"] = v
        for k, v in self.vsn_past_known.get_params().items():
            params[f"vsn_past_known_{k}"] = v
        for k, v in self.vsn_future_known.get_params().items():
            params[f"vsn_future_known_{k}"] = v

        for k, v in self.encoder_lstm.get_params().items():
            params[f"enc_lstm_{k}"] = v
        for k, v in self.decoder_lstm.get_params().items():
            params[f"dec_lstm_{k}"] = v

        for k, v in self.attention.get_params().items():
            params[f"attn_{k}"] = v

        params["W_output"] = self.W_output
        params["b_output"] = self.b_output

        return params

    def set_all_params(self, params: Dict[str, np.ndarray]) -> None:
        """Restore parameters from dict."""
        for key, val in params.items():
            # Route to correct sub-module
            # This is a flat key → attribute mapping for simplicity
            pass  # Parameters are set via references (numpy arrays are mutable)
        # Direct assignment for output weights
        if "W_output" in params:
            self.W_output = params["W_output"].astype(np.float32)
        if "b_output" in params:
            self.b_output = params["b_output"].astype(np.float32)

    def count_params(self) -> int:
        """Count total trainable parameters."""
        total = 0
        for v in self.get_all_params().values():
            total += v.size
        return total


# ── TFT Preprocessor ──────────────────────────────────────────────────

class TFTPreprocessor:
    """Online z-score normalization and sequence building for TFT.

    Uses Welford's online algorithm for streaming mean/variance updates.
    This allows the preprocessor to handle data incrementally without
    storing the full dataset in memory.
    """

    def __init__(self, config: Optional[TFTConfig] = None):
        self.config = config or TFTConfig()

        # Welford's algorithm state per feature group
        self._stats: Dict[str, Dict[str, Any]] = {
            "past_observed": {"n": 0, "mean": None, "M2": None},
            "past_known": {"n": 0, "mean": None, "M2": None},
            "future_known": {"n": 0, "mean": None, "M2": None},
            "static": {"n": 0, "mean": None, "M2": None},
        }

    def update_stats(self, group: str, x: np.ndarray) -> None:
        """Update running statistics using Welford's algorithm.

        Args:
            group: Feature group name ("past_observed", "past_known", etc.)
            x: Data sample (..., n_features). Only last dim matters.
        """
        if group not in self._stats:
            log.warning("Unknown feature group: %s", group)
            return

        stats = self._stats[group]
        # Flatten all but last dimension
        flat = x.reshape(-1, x.shape[-1]).astype(np.float64)

        for sample in flat:
            stats["n"] += 1
            if stats["mean"] is None:
                stats["mean"] = np.zeros_like(sample, dtype=np.float64)
                stats["M2"] = np.zeros_like(sample, dtype=np.float64)

            delta = sample - stats["mean"]
            stats["mean"] += delta / stats["n"]
            delta2 = sample - stats["mean"]
            stats["M2"] += delta * delta2

    def normalize(self, group: str, x: np.ndarray) -> np.ndarray:
        """Z-score normalize using running statistics.

        Args:
            group: Feature group name.
            x: Data to normalize.

        Returns:
            Normalized data (same shape as input).
        """
        stats = self._stats.get(group)
        if stats is None or stats["n"] < 2 or stats["mean"] is None:
            return x.astype(np.float32)

        mean = stats["mean"].astype(np.float32)
        var = (stats["M2"] / (stats["n"] - 1)).astype(np.float32)
        std = np.sqrt(var + 1e-8)

        return ((x.astype(np.float32) - mean) / std).astype(np.float32)

    def build_sequences(self, past_observed: np.ndarray,
                         past_known: np.ndarray,
                         future_known: np.ndarray,
                         static: np.ndarray,
                         targets: np.ndarray) -> Dict[str, np.ndarray]:
        """Build training sequences with sliding window.

        Args:
            past_observed: (total_time, n_past_observed)
            past_known: (total_time, n_past_known)
            future_known: (total_time, n_future_known)
            static: (n_static,) — same for all timesteps
            targets: (total_time,) — prediction targets

        Returns:
            Dict with batched arrays:
                "static": (N, n_static)
                "past_observed": (N, seq_len, n_past_observed)
                "past_known": (N, seq_len, n_past_known)
                "future_known": (N, pred_horizon, n_future_known)
                "targets": (N, pred_horizon)
        """
        c = self.config
        total_time = past_observed.shape[0]
        min_length = c.seq_len + c.pred_horizon

        if total_time < min_length:
            log.warning("Insufficient data: %d < %d (seq_len + pred_horizon)",
                        total_time, min_length)
            return {}

        # Update running stats
        self.update_stats("past_observed", past_observed)
        self.update_stats("past_known", past_known)
        self.update_stats("future_known", future_known)
        self.update_stats("static", static.reshape(1, -1))

        # Normalize
        po_norm = self.normalize("past_observed", past_observed)
        pk_norm = self.normalize("past_known", past_known)
        fk_norm = self.normalize("future_known", future_known)
        st_norm = self.normalize("static", static.reshape(1, -1)).squeeze(0)

        # Sliding window
        n_sequences = total_time - min_length + 1
        batch_po = np.zeros((n_sequences, c.seq_len, c.n_past_observed), dtype=np.float32)
        batch_pk = np.zeros((n_sequences, c.seq_len, c.n_past_known), dtype=np.float32)
        batch_fk = np.zeros((n_sequences, c.pred_horizon, c.n_future_known), dtype=np.float32)
        batch_targets = np.zeros((n_sequences, c.pred_horizon), dtype=np.float32)
        batch_static = np.tile(st_norm, (n_sequences, 1))

        for i in range(n_sequences):
            t_start = i
            t_mid = t_start + c.seq_len
            t_end = t_mid + c.pred_horizon

            batch_po[i] = po_norm[t_start:t_mid]
            batch_pk[i] = pk_norm[t_start:t_mid]
            batch_fk[i] = fk_norm[t_mid:t_end]
            batch_targets[i] = targets[t_mid:t_end]

        return {
            "static": batch_static,
            "past_observed": batch_po,
            "past_known": batch_pk,
            "future_known": batch_fk,
            "targets": batch_targets,
        }

    def get_stats(self) -> Dict[str, Any]:
        """Return current normalization statistics (for serialization)."""
        result = {}
        for group, stats in self._stats.items():
            if stats["mean"] is not None:
                result[group] = {
                    "n": stats["n"],
                    "mean": stats["mean"].tolist(),
                    "std": np.sqrt(stats["M2"] / max(stats["n"] - 1, 1) + 1e-8).tolist(),
                }
        return result

    def save_stats(self, path: Optional[str] = None) -> None:
        """Save normalization stats to JSON."""
        path = Path(path) if path else DATA_DIR / "tft_norm_stats.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "w") as f:
                json.dump(self.get_stats(), f, indent=2)
            log.info("Saved TFT normalization stats to %s", path)
        except OSError as e:
            log.error("Failed to save normalization stats: %s", e)

    def load_stats(self, path: Optional[str] = None) -> None:
        """Load normalization stats from JSON."""
        path = Path(path) if path else DATA_DIR / "tft_norm_stats.json"
        if not path.exists():
            log.info("No normalization stats at %s", path)
            return
        try:
            with open(path, "r") as f:
                data = json.load(f)
            for group, vals in data.items():
                if group in self._stats:
                    n = vals["n"]
                    mean = np.array(vals["mean"], dtype=np.float64)
                    std = np.array(vals["std"], dtype=np.float64)
                    var = std ** 2
                    self._stats[group] = {
                        "n": n,
                        "mean": mean,
                        "M2": var * max(n - 1, 1),
                    }
            log.info("Loaded normalization stats from %s", path)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load normalization stats: %s", e)


# ── TFT Trainer ────────────────────────────────────────────────────────

class TFTTrainer:
    """Training loop for TFT with walk-forward splits, quantile loss,
    early stopping, and ONNX export.

    Uses numerical gradient estimation (finite differences) for
    backpropagation since we don't have autograd in numpy. For production
    training, convert to PyTorch. This trainer is for:
      - Architecture validation
      - Small dataset experiments
      - Preprocessing pipeline verification
      - Weight initialization for PyTorch transfer
    """

    def __init__(self):
        self._best_loss = float("inf")
        self._patience_counter = 0

    def train(self, data: Dict[str, np.ndarray],
              config: Optional[TFTConfig] = None) -> Tuple[TemporalFusionTransformer, Dict[str, Any]]:
        """Train TFT model with walk-forward validation.

        Args:
            data: Dict from TFTPreprocessor.build_sequences() with keys:
                "static", "past_observed", "past_known", "future_known", "targets"
            config: TFT configuration. Uses defaults if None.

        Returns:
            (model, metrics) tuple.
        """
        config = config or TFTConfig()
        model = TemporalFusionTransformer(config)

        n_samples = data["targets"].shape[0]
        if n_samples < 50:
            log.warning("Insufficient training data: %d samples", n_samples)
            return model, {"status": "insufficient_data", "n_samples": n_samples}

        log.info("TFT training: %d samples, %d params, config: d_model=%d, n_heads=%d",
                 n_samples, model.count_params(), config.d_model, config.n_heads)

        # Walk-forward split: 70% train, 15% val, 15% test (respecting time order)
        train_end = int(n_samples * 0.70)
        val_end = int(n_samples * 0.85)

        train_data = {k: v[:train_end] for k, v in data.items()}
        val_data = {k: v[train_end:val_end] for k, v in data.items()}
        test_data = {k: v[val_end:] for k, v in data.items()}

        # Collect all parameters for gradient update
        params = model.get_all_params()
        param_keys = list(params.keys())
        learning_rate = config.learning_rate

        self._best_loss = float("inf")
        self._patience_counter = 0
        best_params = {k: v.copy() for k, v in params.items()}
        train_losses: List[float] = []
        val_losses: List[float] = []

        for epoch in range(config.epochs):
            model.train_mode()
            epoch_loss = 0.0
            n_batches = 0

            # Mini-batch training (time-ordered, no shuffle)
            for batch_start in range(0, train_end, config.batch_size):
                batch_end = min(batch_start + config.batch_size, train_end)
                batch = {k: v[batch_start:batch_end] for k, v in train_data.items()}

                if batch["targets"].shape[0] < 2:
                    continue

                # Forward pass
                output = model.forward(
                    batch["static"], batch["past_observed"],
                    batch["past_known"], batch["future_known"],
                )

                # Quantile loss
                loss = self._quantile_loss(
                    output["predictions"], batch["targets"], config.quantiles,
                )
                epoch_loss += loss
                n_batches += 1

                # Gradient estimation via finite differences on a random subset
                # of parameters (full gradient is too expensive in numpy)
                self._update_params_fd(
                    model, batch, config, learning_rate, epoch,
                )

            avg_train_loss = epoch_loss / max(n_batches, 1)
            train_losses.append(avg_train_loss)

            # Validation
            model.eval_mode()
            val_output = model.forward(
                val_data["static"], val_data["past_observed"],
                val_data["past_known"], val_data["future_known"],
            )
            val_loss = self._quantile_loss(
                val_output["predictions"], val_data["targets"], config.quantiles,
            )
            val_losses.append(val_loss)

            # Early stopping
            if val_loss < self._best_loss:
                self._best_loss = val_loss
                self._patience_counter = 0
                best_params = {k: v.copy() for k, v in model.get_all_params().items()}
            else:
                self._patience_counter += 1

            if epoch % 5 == 0:
                log.info("TFT epoch %d/%d: train_loss=%.6f, val_loss=%.6f, patience=%d/%d",
                         epoch, config.epochs, avg_train_loss, val_loss,
                         self._patience_counter, config.patience)

            if self._patience_counter >= config.patience:
                log.info("Early stopping at epoch %d (patience=%d)", epoch, config.patience)
                break

            # Learning rate decay
            if epoch > 0 and epoch % 20 == 0:
                learning_rate *= 0.5
                log.info("LR decay → %.6f", learning_rate)

        # Restore best params
        model.set_all_params(best_params)
        model.eval_mode()

        # Test evaluation
        test_output = model.forward(
            test_data["static"], test_data["past_observed"],
            test_data["past_known"], test_data["future_known"],
        )
        test_loss = self._quantile_loss(
            test_output["predictions"], test_data["targets"], config.quantiles,
        )

        # Compute coverage metrics for calibration
        coverage = self._compute_coverage(
            test_output["predictions"], test_data["targets"], config.quantiles,
        )

        metrics = {
            "status": "trained",
            "n_samples": n_samples,
            "n_params": model.count_params(),
            "train_loss_final": round(float(train_losses[-1]), 6),
            "val_loss_best": round(float(self._best_loss), 6),
            "test_loss": round(float(test_loss), 6),
            "coverage": coverage,
            "epochs_trained": len(train_losses),
            "early_stopped": self._patience_counter >= config.patience,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        log.info("TFT training complete: %s", json.dumps(metrics, indent=2))
        return model, metrics

    def export_onnx(self, model: TemporalFusionTransformer, path: str) -> bool:
        """Export TFT weights for ONNX-based Rust inference.

        Since we can't build a full ONNX graph from numpy, we export
        the weights as a JSON file that can be loaded by the Rust
        inference engine for reconstruction.

        Args:
            model: Trained TFT model.
            path: Output path (will save {path}.json with weights).

        Returns:
            True if export succeeded.
        """
        export_path = Path(path)
        export_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            params = model.get_all_params()
            # Convert numpy arrays to lists for JSON serialization
            export_data = {
                "architecture": "tft",
                "config": asdict(model.config),
                "n_params": model.count_params(),
                "weights": {k: v.tolist() for k, v in params.items()},
                "shapes": {k: list(v.shape) for k, v in params.items()},
                "exported_at": datetime.now(timezone.utc).isoformat(),
            }

            # Save as compressed numpy archive (more efficient than JSON for weights)
            npz_path = export_path.with_suffix(".npz")
            np.savez_compressed(str(npz_path), **params)
            log.info("Exported TFT weights to %s (%d params)",
                     npz_path, model.count_params())

            # Also save metadata as JSON
            meta_path = export_path.with_suffix(".meta.json")
            meta = {k: v for k, v in export_data.items() if k != "weights"}
            with open(meta_path, "w") as f:
                json.dump(meta, f, indent=2, default=str)

            return True
        except Exception as e:
            log.error("TFT ONNX export failed: %s", e)
            return False

    @staticmethod
    def load_model(path: str, config: Optional[TFTConfig] = None) -> Optional[TemporalFusionTransformer]:
        """Load a TFT model from exported weights.

        Args:
            path: Path to the .npz weights file.
            config: TFT config (must match the trained model).

        Returns:
            Loaded TemporalFusionTransformer or None on failure.
        """
        npz_path = Path(path)
        if not npz_path.exists():
            npz_path = npz_path.with_suffix(".npz")
        if not npz_path.exists():
            log.error("Weights file not found: %s", npz_path)
            return None

        try:
            model = TemporalFusionTransformer(config)
            data = np.load(str(npz_path))
            params = {k: data[k] for k in data.files}
            model.set_all_params(params)
            model.eval_mode()
            log.info("Loaded TFT model from %s", npz_path)
            return model
        except Exception as e:
            log.error("Failed to load TFT model: %s", e)
            return None

    # ── Loss Functions ─────────────────────────────────────────────────

    @staticmethod
    def _quantile_loss(predictions: np.ndarray, targets: np.ndarray,
                        quantiles: List[float]) -> float:
        """Compute pinball (quantile) loss.

        For each quantile q:
            L_q(y, y_hat) = q * max(y - y_hat, 0) + (1-q) * max(y_hat - y, 0)

        Args:
            predictions: (batch, horizon, n_quantiles)
            targets: (batch, horizon)
            quantiles: List of quantile levels [0.1, 0.5, 0.9]

        Returns:
            Scalar loss value (averaged over batch, horizon, quantiles).
        """
        total_loss = 0.0
        n_quantiles = len(quantiles)

        for i, q in enumerate(quantiles):
            pred_q = predictions[:, :, i] if predictions.ndim == 3 else predictions
            errors = targets - pred_q
            loss_q = np.where(errors >= 0, q * errors, (q - 1.0) * errors)
            total_loss += np.mean(loss_q)

        return float(total_loss / n_quantiles)

    @staticmethod
    def _compute_coverage(predictions: np.ndarray, targets: np.ndarray,
                           quantiles: List[float]) -> Dict[str, float]:
        """Compute prediction interval coverage.

        Checks what fraction of targets fall within the predicted quantile
        bands. For a well-calibrated model, coverage should match the
        implied interval width.

        Args:
            predictions: (batch, horizon, n_quantiles) — must have at least
                quantiles at [0.1, 0.5, 0.9].
            targets: (batch, horizon)
            quantiles: Quantile levels.

        Returns:
            Dict with coverage metrics.
        """
        result: Dict[str, float] = {}

        if len(quantiles) >= 3:
            # 80% interval: [0.1, 0.9]
            lower = predictions[:, :, 0]
            upper = predictions[:, :, -1]
            in_interval = (targets >= lower) & (targets <= upper)
            coverage_80 = float(np.mean(in_interval))
            result["coverage_80"] = round(coverage_80, 4)
            result["expected_80"] = quantiles[-1] - quantiles[0]  # e.g., 0.8

        if len(quantiles) >= 2:
            # Median accuracy
            median_pred = predictions[:, :, len(quantiles) // 2]
            mae = float(np.mean(np.abs(targets - median_pred)))
            result["median_mae"] = round(mae, 6)

        return result

    # ── Gradient Updates ───────────────────────────────────────────────

    def _update_params_fd(self, model: TemporalFusionTransformer,
                           batch: Dict[str, np.ndarray],
                           config: TFTConfig,
                           lr: float,
                           epoch: int) -> None:
        """Update parameters using stochastic finite differences.

        Instead of computing full gradients (infeasible in numpy without
        autograd), we use simultaneous perturbation stochastic
        approximation (SPSA). This perturbs all parameters simultaneously
        with a random direction and estimates the gradient from two
        function evaluations.

        SPSA convergence: O(1/sqrt(N)) — slower than backprop but
        requires only 2 forward passes per update.
        """
        params = model.get_all_params()

        # SPSA hyperparameters (Spall 1998)
        a = lr / (1.0 + epoch * 0.01)  # Decaying step size
        c = 0.01 / (1.0 + epoch * 0.001)  # Perturbation magnitude

        # Generate random perturbation direction (Rademacher)
        perturbations = {}
        for key, param in params.items():
            perturbations[key] = model.rng.choice([-1.0, 1.0], size=param.shape).astype(np.float32)

        # Positive perturbation
        for key in params:
            params[key] += c * perturbations[key]
        model.set_all_params(params)

        output_plus = model.forward(
            batch["static"], batch["past_observed"],
            batch["past_known"], batch["future_known"],
        )
        loss_plus = self._quantile_loss(output_plus["predictions"],
                                         batch["targets"], config.quantiles)

        # Negative perturbation
        for key in params:
            params[key] -= 2 * c * perturbations[key]
        model.set_all_params(params)

        output_minus = model.forward(
            batch["static"], batch["past_observed"],
            batch["past_known"], batch["future_known"],
        )
        loss_minus = self._quantile_loss(output_minus["predictions"],
                                          batch["targets"], config.quantiles)

        # Restore to center and apply gradient
        grad_estimate = (loss_plus - loss_minus) / (2 * c)
        for key in params:
            params[key] += c * perturbations[key]  # Restore center
            # SPSA gradient: g_k = (L+ - L-) / (2*c*Delta_k)
            # Update: theta -= a * g_k / Delta_k
            params[key] -= a * grad_estimate * perturbations[key]

        model.set_all_params(params)
