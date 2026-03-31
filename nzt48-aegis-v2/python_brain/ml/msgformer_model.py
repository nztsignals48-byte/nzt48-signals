"""Multi-Scale Graph-Transformer Hybrid (MSGformer) — Book 107.

Combines graph neural networks (GAT-style message passing) with
temporal transformers across multiple timescales for market prediction.

Key insight: financial instruments form a graph (sector, correlation,
lead-lag edges). Information propagates through this graph at different
speeds across timescales. MSGformer captures both structural (graph)
and temporal (attention) dependencies simultaneously.

Architecture:
  Per timescale (5s, 1m, 5m, 15m, 1h):
    GraphLayer: GAT attention-weighted neighbor aggregation
    TemporalLayer: causal multi-head self-attention
  MultiScaleStack: fuses all timescale outputs
  MSGformerModel: full model with contagion detection

Pure numpy implementation — no PyTorch/DGL dependency.

State: /app/data/models/msgformer_*.npz

Bridge.py integration:
    try:
        from python_brain.ml.msgformer_model import (
            MSGformerModel, GraphLayer, TemporalLayer, MultiScaleStack,
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
    "GraphLayer",
    "TemporalLayer",
    "MultiScaleStack",
    "MSGformerModel",
]

# ── Paths ──────────────────────────────────────────────────────────────
MODEL_DIR = Path("/app/data/models")

# ── Constants ──────────────────────────────────────────────────────────
EPSILON = 1e-8
CLIP_GRAD = 5.0
DEFAULT_SCALES = ["5s", "1m", "5m", "15m", "1h"]


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


def _leaky_relu(x: np.ndarray, alpha: float = 0.2) -> np.ndarray:
    """Leaky ReLU activation (used in GAT)."""
    return np.where(x > 0, x, alpha * x)


def _relu(x: np.ndarray) -> np.ndarray:
    return np.maximum(0.0, x)


def _layer_norm(x: np.ndarray, axis: int = -1) -> np.ndarray:
    """Layer normalization."""
    mean = np.mean(x, axis=axis, keepdims=True)
    var = np.var(x, axis=axis, keepdims=True)
    return (x - mean) / np.sqrt(var + EPSILON)


def _elu(x: np.ndarray, alpha: float = 1.0) -> np.ndarray:
    """ELU activation."""
    return np.where(x > 0, x, alpha * (np.exp(np.clip(x, -10, 0)) - 1))


# ── Graph Attention Layer (GAT-style) ──────────────────────────────────

class GraphLayer:
    """Graph Attention Network layer (Velickovic et al. 2018).

    Performs attention-weighted message passing over a graph structure.
    Each node aggregates information from its neighbors using learned
    attention coefficients.

    For node i and neighbor j:
      e_ij = LeakyReLU(a^T [Wh_i || Wh_j])
      alpha_ij = softmax_j(e_ij)
      h'_i = ELU(sum_j alpha_ij * Wh_j)

    Multi-head attention: K independent attention heads, outputs concatenated.

    Args:
        d_in: input feature dimension per node
        d_out: output feature dimension per node (per head)
        n_heads: number of attention heads
        dropout_rate: attention dropout (training only, simulated)
    """

    def __init__(
        self,
        d_in: int,
        d_out: int,
        n_heads: int = 4,
        dropout_rate: float = 0.1,
    ) -> None:
        self.d_in = d_in
        self.d_out = d_out
        self.n_heads = n_heads
        self.dropout_rate = dropout_rate

        # Per-head parameters
        self.W: List[np.ndarray] = []  # Feature transform: (d_in, d_out) per head
        self.a_src: List[np.ndarray] = []  # Source attention: (d_out, 1) per head
        self.a_dst: List[np.ndarray] = []  # Destination attention: (d_out, 1) per head

        for _ in range(n_heads):
            self.W.append(_xavier_init(d_in, d_out))
            self.a_src.append(_xavier_init(d_out, 1))
            self.a_dst.append(_xavier_init(d_out, 1))

        # Output projection after concatenation
        self.W_out = _xavier_init(d_out * n_heads, d_out)
        self.b_out = np.zeros(d_out)

        # Store attention weights for analysis
        self._last_attention: Optional[np.ndarray] = None

    def forward(
        self,
        X: np.ndarray,
        A: np.ndarray,
        training: bool = False,
    ) -> np.ndarray:
        """Forward pass: attention-weighted neighbor aggregation.

        Args:
            X: node features (n_nodes, d_in)
            A: adjacency matrix (n_nodes, n_nodes). Non-zero entries
               indicate edges. Can be weighted.

        Returns:
            Updated node features (n_nodes, d_out)
        """
        n_nodes = X.shape[0]

        # Mask: only attend to connected nodes
        # A > 0 means there's an edge
        mask = (A != 0).astype(np.float64)

        head_outputs: List[np.ndarray] = []
        all_attentions: List[np.ndarray] = []

        for k in range(self.n_heads):
            # Linear transform
            Wh = X @ self.W[k]  # (n_nodes, d_out)

            # Attention scores
            # e_ij = LeakyReLU(a_src^T Wh_i + a_dst^T Wh_j)
            src_scores = Wh @ self.a_src[k]   # (n_nodes, 1)
            dst_scores = Wh @ self.a_dst[k]   # (n_nodes, 1)

            # Broadcast: e_ij = src_i + dst_j for all pairs
            e = src_scores + dst_scores.T  # (n_nodes, n_nodes)
            e = _leaky_relu(e, alpha=0.2)

            # Mask non-edges with large negative (before softmax)
            e = np.where(mask > 0, e, -1e9)

            # Add self-loops (every node attends to itself)
            self_attn = (src_scores.flatten()[:n_nodes] +
                         dst_scores.flatten()[:n_nodes])
            self_attn = _leaky_relu(self_attn, alpha=0.2)
            np.fill_diagonal(e, self_attn)

            # Self-loop mask
            mask_with_self = mask.copy()
            np.fill_diagonal(mask_with_self, 1.0)
            e = np.where(mask_with_self > 0, e, -1e9)

            # Attention weights via softmax
            alpha = _softmax(e, axis=-1)  # (n_nodes, n_nodes)

            # Optional: attention dropout (training simulation)
            if training and self.dropout_rate > 0:
                drop_mask = (np.random.random(alpha.shape) > self.dropout_rate).astype(np.float64)
                alpha = alpha * drop_mask
                row_sums = alpha.sum(axis=-1, keepdims=True)
                alpha = alpha / (row_sums + EPSILON)

            all_attentions.append(alpha)

            # Aggregate neighbor features
            h_prime = alpha @ Wh  # (n_nodes, d_out)
            h_prime = _elu(h_prime)

            head_outputs.append(h_prime)

        # Concatenate heads
        multi_head = np.concatenate(head_outputs, axis=-1)  # (n_nodes, d_out * n_heads)

        # Output projection
        output = multi_head @ self.W_out + self.b_out  # (n_nodes, d_out)
        output = _layer_norm(output)

        # Store attention for analysis
        self._last_attention = np.stack(all_attentions, axis=0).mean(axis=0)  # Average across heads

        return output

    def get_attention_weights(self) -> Optional[np.ndarray]:
        """Return last computed attention weights (n_nodes, n_nodes)."""
        return self._last_attention


# ── Temporal Self-Attention Layer ──────────────────────────────────────

class TemporalLayer:
    """Causal multi-head self-attention over the time dimension.

    Processes temporal sequences of node embeddings, attending only
    to past positions (causal masking for online prediction).

    Args:
        d_model: feature dimension
        n_heads: number of attention heads
        d_ff: feed-forward hidden dimension
    """

    def __init__(
        self,
        d_model: int,
        n_heads: int = 4,
        d_ff: Optional[int] = None,
    ) -> None:
        self.d_model = d_model
        self.n_heads = n_heads
        self.d_k = d_model // n_heads
        self.d_ff = d_ff or d_model * 4

        # Self-attention projections
        self.W_q = _xavier_init(d_model, d_model)
        self.W_k = _xavier_init(d_model, d_model)
        self.W_v = _xavier_init(d_model, d_model)
        self.W_o = _xavier_init(d_model, d_model)

        # Position-wise FFN
        self.W_ff1 = _xavier_init(d_model, self.d_ff)
        self.b_ff1 = np.zeros(self.d_ff)
        self.W_ff2 = _xavier_init(self.d_ff, d_model)
        self.b_ff2 = np.zeros(d_model)

        # Stored attention for analysis
        self._last_attention: Optional[np.ndarray] = None

    def forward(self, X_seq: np.ndarray) -> np.ndarray:
        """Forward pass with causal multi-head attention.

        Args:
            X_seq: input sequence (seq_len, d_model) or (batch, seq_len, d_model)

        Returns:
            Attended features, same shape as input
        """
        squeeze = False
        if X_seq.ndim == 2:
            X_seq = X_seq[np.newaxis, :, :]
            squeeze = True

        batch_size, seq_len, d = X_seq.shape

        # Q, K, V projections
        Q = X_seq @ self.W_q
        K = X_seq @ self.W_k
        V = X_seq @ self.W_v

        # Reshape for multi-head: (B, T, H, d_k) → (B, H, T, d_k)
        Q = Q.reshape(batch_size, seq_len, self.n_heads, self.d_k).transpose(0, 2, 1, 3)
        K = K.reshape(batch_size, seq_len, self.n_heads, self.d_k).transpose(0, 2, 1, 3)
        V = V.reshape(batch_size, seq_len, self.n_heads, self.d_k).transpose(0, 2, 1, 3)

        # Scaled dot-product attention
        scale = math.sqrt(self.d_k)
        scores = (Q @ K.transpose(0, 1, 3, 2)) / scale  # (B, H, T, T)

        # Causal mask: attend only to past + present
        causal_mask = np.tril(np.ones((seq_len, seq_len)))
        scores = np.where(causal_mask[np.newaxis, np.newaxis, :, :] == 0, -1e9, scores)

        attn_weights = _softmax(scores, axis=-1)  # (B, H, T, T)
        self._last_attention = attn_weights.mean(axis=(0, 1))  # Average over batch and heads

        # Weighted sum
        context = attn_weights @ V  # (B, H, T, d_k)
        context = context.transpose(0, 2, 1, 3).reshape(batch_size, seq_len, self.d_model)

        # Output projection + residual
        attn_out = context @ self.W_o
        hidden = _layer_norm(X_seq + attn_out, axis=-1)

        # Position-wise FFN + residual
        ff_out = _relu(hidden @ self.W_ff1 + self.b_ff1)
        ff_out = ff_out @ self.W_ff2 + self.b_ff2
        output = _layer_norm(hidden + ff_out, axis=-1)

        if squeeze:
            output = output[0]

        return output

    def get_attention_weights(self) -> Optional[np.ndarray]:
        """Return last computed temporal attention weights (seq_len, seq_len)."""
        return self._last_attention


# ── Multi-Scale Stack ──────────────────────────────────────────────────

class MultiScaleStack:
    """Processes multiple timescales with graph + temporal layers per scale.

    Each timescale gets its own GraphLayer and TemporalLayer, then
    outputs are fused via learned weighted sum.

    Timescales: 5s, 1m, 5m, 15m, 1h
    Per scale: GraphLayer → TemporalLayer → output
    Fusion: softmax-weighted sum across scales

    Args:
        d_model: feature dimension
        n_graph_heads: attention heads in graph layer
        n_temporal_heads: attention heads in temporal layer
        scale_names: list of timescale identifiers
    """

    def __init__(
        self,
        d_model: int,
        n_graph_heads: int = 4,
        n_temporal_heads: int = 4,
        scale_names: Optional[List[str]] = None,
    ) -> None:
        self.d_model = d_model
        self.scale_names = scale_names or DEFAULT_SCALES
        self.n_scales = len(self.scale_names)

        # Per-scale layers
        self.graph_layers: Dict[str, GraphLayer] = {}
        self.temporal_layers: Dict[str, TemporalLayer] = {}

        for name in self.scale_names:
            self.graph_layers[name] = GraphLayer(
                d_in=d_model, d_out=d_model, n_heads=n_graph_heads
            )
            self.temporal_layers[name] = TemporalLayer(
                d_model=d_model, n_heads=n_temporal_heads
            )

        # Fusion weights (learnable logits → softmax)
        self.fusion_logits = np.zeros(self.n_scales, dtype=np.float64)

        # Cross-scale interaction: project concatenated coarsest + finest
        self.W_cross = _xavier_init(d_model * 2, d_model)
        self.b_cross = np.zeros(d_model)

    def forward(
        self,
        features_by_scale: Dict[str, np.ndarray],
        adjacency: np.ndarray,
    ) -> np.ndarray:
        """Process all timescales through graph + temporal layers and fuse.

        Args:
            features_by_scale: dict mapping scale name to features.
                Each value: (n_nodes, seq_len, d_model) — per-node temporal features
                OR (n_nodes, d_model) — single-step features
            adjacency: adjacency matrix (n_nodes, n_nodes)

        Returns:
            Fused representation (n_nodes, d_model)
        """
        scale_outputs: List[np.ndarray] = []
        available_indices: List[int] = []
        graph_attentions: Dict[str, Optional[np.ndarray]] = {}

        for i, name in enumerate(self.scale_names):
            if name not in features_by_scale:
                continue

            feat = features_by_scale[name]
            if feat is None or feat.size == 0:
                continue

            n_nodes = feat.shape[0]

            # Step 1: Graph layer (per-node, aggregating spatial info)
            if feat.ndim == 3:
                # (n_nodes, seq_len, d_model) → process last timestep through graph
                graph_input = feat[:, -1, :]  # Most recent features
            else:
                graph_input = feat  # (n_nodes, d_model)

            graph_out = self.graph_layers[name].forward(graph_input, adjacency)
            graph_attentions[name] = self.graph_layers[name].get_attention_weights()

            # Step 2: Temporal layer (per-node temporal sequence)
            if feat.ndim == 3:
                seq_len = feat.shape[1]
                # Replace last timestep with graph-enriched features
                enriched = feat.copy()
                enriched[:, -1, :] = graph_out

                # Process each node's temporal sequence
                # Stack all nodes as a batch: (n_nodes, seq_len, d_model)
                temporal_out = self.temporal_layers[name].forward(enriched)
                # Take last timestep output
                scale_out = temporal_out[:, -1, :]  # (n_nodes, d_model)
            else:
                # No temporal dimension — use graph output directly
                scale_out = graph_out

            scale_outputs.append(scale_out)
            available_indices.append(i)

        if not scale_outputs:
            log.warning("MultiScaleStack: no valid scale data provided")
            return np.zeros((1, self.d_model), dtype=np.float64)

        # Fusion: weighted sum across scales
        fusion_logits = self.fusion_logits[available_indices]
        weights = _softmax(fusion_logits)

        stacked = np.stack(scale_outputs, axis=0)  # (n_avail, n_nodes, d_model)
        fused = np.einsum("s,snd->nd", weights, stacked)  # (n_nodes, d_model)

        # Cross-scale interaction gate
        if len(scale_outputs) >= 2:
            finest = scale_outputs[0]
            coarsest = scale_outputs[-1]
            cross = np.concatenate([finest, coarsest], axis=-1)
            gate = 1.0 / (1.0 + np.exp(-(cross @ self.W_cross + self.b_cross)))
            fused = fused * gate

        fused = _layer_norm(fused, axis=-1)

        return fused

    def get_fusion_weights(self) -> Dict[str, float]:
        """Return current scale fusion weights."""
        weights = _softmax(self.fusion_logits)
        return {name: float(w) for name, w in zip(self.scale_names, weights)}

    def get_graph_attentions(self) -> Dict[str, Optional[np.ndarray]]:
        """Return last graph attention weights per scale."""
        return {
            name: layer.get_attention_weights()
            for name, layer in self.graph_layers.items()
        }


# ── MSGformer Full Model ──────────────────────────────────────────────

class MSGformerModel:
    """Multi-Scale Graph-Transformer for market prediction.

    Combines graph structure (inter-instrument relationships) with
    temporal dynamics (per-instrument time series) across multiple
    timescales for directional prediction.

    Includes contagion detection: identifies abnormal cross-instrument
    attention patterns that may indicate contagion (e.g., a flash crash
    spreading across correlated instruments).

    Args:
        d_model: hidden dimension
        n_graph_heads: attention heads for graph layers
        n_temporal_heads: attention heads for temporal layers
        d_output: output dimension per node
        scale_names: timescale identifiers
    """

    def __init__(
        self,
        d_model: int = 64,
        n_graph_heads: int = 4,
        n_temporal_heads: int = 4,
        d_output: int = 1,
        scale_names: Optional[List[str]] = None,
    ) -> None:
        self.d_model = d_model
        self.n_graph_heads = n_graph_heads
        self.n_temporal_heads = n_temporal_heads
        self.d_output = d_output
        self.scale_names = scale_names or DEFAULT_SCALES

        # Core multi-scale stack
        self.multi_scale = MultiScaleStack(
            d_model=d_model,
            n_graph_heads=n_graph_heads,
            n_temporal_heads=n_temporal_heads,
            scale_names=self.scale_names,
        )

        # Prediction head
        self.W_pred1 = _xavier_init(d_model, d_model)
        self.b_pred1 = np.zeros(d_model)
        self.W_pred2 = _xavier_init(d_model, d_output)
        self.b_pred2 = np.zeros(d_output)

        # Contagion detection head
        self.W_contagion = _xavier_init(d_model, d_model)
        self.contagion_threshold = 2.0  # Std deviations above mean attention

        # Historical attention baselines for anomaly detection
        self._attention_baselines: Dict[str, Dict[str, float]] = {}

        # Training state
        self.n_updates = 0

    def forward(
        self,
        graph_features: Dict[str, np.ndarray],
        temporal_features: Dict[str, np.ndarray],
        adjacency: np.ndarray,
    ) -> Dict[str, Any]:
        """Full forward pass.

        Args:
            graph_features: per-scale node features for graph processing
                {scale: (n_nodes, d_model)}
            temporal_features: per-scale temporal sequences
                {scale: (n_nodes, seq_len, d_model)}
            adjacency: adjacency matrix (n_nodes, n_nodes)

        Returns:
            Dict with:
                - predictions: (n_nodes, d_output)
                - attention_weights: dict of per-scale graph attention
                - temporal_attention: dict of per-scale temporal attention
                - fusion_weights: dict of scale weights
                - node_embeddings: (n_nodes, d_model)
        """
        # Merge graph and temporal features
        # Priority: use temporal if available, fall back to graph
        combined_features: Dict[str, np.ndarray] = {}
        for scale in self.scale_names:
            if scale in temporal_features and temporal_features[scale] is not None:
                combined_features[scale] = temporal_features[scale]
            elif scale in graph_features and graph_features[scale] is not None:
                combined_features[scale] = graph_features[scale]

        if not combined_features:
            n_nodes = adjacency.shape[0] if adjacency.ndim >= 2 else 1
            return {
                "predictions": np.zeros((n_nodes, self.d_output)),
                "attention_weights": {},
                "temporal_attention": {},
                "fusion_weights": self.multi_scale.get_fusion_weights(),
                "node_embeddings": np.zeros((n_nodes, self.d_model)),
            }

        # Multi-scale processing
        node_embeddings = self.multi_scale.forward(combined_features, adjacency)

        # Prediction head
        h = _relu(node_embeddings @ self.W_pred1 + self.b_pred1)
        h = _layer_norm(h, axis=-1)
        predictions = h @ self.W_pred2 + self.b_pred2  # (n_nodes, d_output)

        # Collect attention weights
        graph_attentions = self.multi_scale.get_graph_attentions()
        temporal_attentions = {}
        for name, layer in self.multi_scale.temporal_layers.items():
            temporal_attentions[name] = layer.get_attention_weights()

        return {
            "predictions": predictions,
            "attention_weights": {
                k: v.tolist() if v is not None else None
                for k, v in graph_attentions.items()
            },
            "temporal_attention": {
                k: v.tolist() if v is not None else None
                for k, v in temporal_attentions.items()
            },
            "fusion_weights": self.multi_scale.get_fusion_weights(),
            "node_embeddings": node_embeddings,
        }

    def detect_contagion(
        self,
        attention_weights: Dict[str, Any],
        node_names: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Detect abnormal cross-instrument attention (contagion signals).

        Contagion is detected when attention between instruments
        significantly exceeds historical baseline, especially between
        instruments in different sectors/groups.

        Args:
            attention_weights: graph attention weights per scale
                {scale: (n_nodes, n_nodes) or list}
            node_names: optional instrument names for each node

        Returns:
            List of contagion alerts with source, target, scale, severity
        """
        alerts: List[Dict[str, Any]] = []

        for scale, attn in attention_weights.items():
            if attn is None:
                continue

            # Convert to numpy if needed
            if isinstance(attn, list):
                attn = np.array(attn)
            if attn.ndim != 2:
                continue

            n_nodes = attn.shape[0]

            # Zero out diagonal (self-attention is expected)
            off_diag = attn.copy()
            np.fill_diagonal(off_diag, 0.0)

            # Compute statistics
            mean_attn = float(off_diag[off_diag > 0].mean()) if (off_diag > 0).any() else 0.0
            std_attn = float(off_diag[off_diag > 0].std()) if (off_diag > 0).any() else 1.0

            # Update running baseline
            if scale not in self._attention_baselines:
                self._attention_baselines[scale] = {
                    "mean": mean_attn,
                    "std": max(std_attn, EPSILON),
                    "n_obs": 1,
                }
            else:
                bl = self._attention_baselines[scale]
                n = bl["n_obs"]
                # Exponential moving average
                alpha = 2.0 / (min(n, 100) + 1)
                bl["mean"] = (1 - alpha) * bl["mean"] + alpha * mean_attn
                bl["std"] = (1 - alpha) * bl["std"] + alpha * max(std_attn, EPSILON)
                bl["n_obs"] = n + 1

            baseline = self._attention_baselines[scale]

            # Find abnormally high attention pairs
            threshold = baseline["mean"] + self.contagion_threshold * baseline["std"]

            abnormal_pairs = np.argwhere(off_diag > threshold)

            for src_idx, tgt_idx in abnormal_pairs:
                src_idx = int(src_idx)
                tgt_idx = int(tgt_idx)
                attention_value = float(off_diag[src_idx, tgt_idx])
                z_score = (attention_value - baseline["mean"]) / max(baseline["std"], EPSILON)

                src_name = node_names[src_idx] if node_names and src_idx < len(node_names) else f"node_{src_idx}"
                tgt_name = node_names[tgt_idx] if node_names and tgt_idx < len(node_names) else f"node_{tgt_idx}"

                severity = "LOW"
                if z_score > 4.0:
                    severity = "HIGH"
                elif z_score > 3.0:
                    severity = "MEDIUM"

                alerts.append({
                    "scale": scale,
                    "source": src_name,
                    "target": tgt_name,
                    "source_idx": src_idx,
                    "target_idx": tgt_idx,
                    "attention": round(attention_value, 6),
                    "baseline_mean": round(baseline["mean"], 6),
                    "z_score": round(z_score, 2),
                    "severity": severity,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

        if alerts:
            log.warning("Contagion detected: %d alert(s) across %d scales",
                        len(alerts),
                        len(set(a["scale"] for a in alerts)))

        # Sort by severity (HIGH first) then z-score
        severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        alerts.sort(key=lambda a: (severity_order.get(a["severity"], 3), -a["z_score"]))

        return alerts

    def train_step(
        self,
        graph_features: Dict[str, np.ndarray],
        temporal_features: Dict[str, np.ndarray],
        adjacency: np.ndarray,
        targets: np.ndarray,
        lr: float = 0.001,
    ) -> Dict[str, float]:
        """Single training step with MSE loss.

        Args:
            graph_features: per-scale graph features
            temporal_features: per-scale temporal features
            adjacency: graph adjacency matrix
            targets: target values (n_nodes, d_output) or (n_nodes,)
            lr: learning rate

        Returns:
            Dict with loss metrics
        """
        # Forward
        result = self.forward(graph_features, temporal_features, adjacency)
        predictions = result["predictions"]
        embeddings = result["node_embeddings"]

        # Loss
        if targets.ndim == 1:
            targets = targets[:, np.newaxis]
        error = predictions - targets
        loss = float(np.mean(error ** 2))

        # Backprop through prediction head (simplified)
        n = max(error.shape[0], 1)
        d_pred = 2.0 * error / n

        # W_pred2 gradient
        h = _relu(embeddings @ self.W_pred1 + self.b_pred1)
        h = _layer_norm(h, axis=-1)

        grad_W_pred2 = h.T @ d_pred
        grad_b_pred2 = d_pred.sum(axis=0)

        # W_pred1 gradient
        d_h = d_pred @ self.W_pred2.T
        d_h_pre = d_h * (embeddings @ self.W_pred1 + self.b_pred1 > 0).astype(np.float64)

        grad_W_pred1 = embeddings.T @ d_h_pre
        grad_b_pred1 = d_h_pre.sum(axis=0)

        # Clip and update
        norm_W2 = np.linalg.norm(grad_W_pred2)
        if norm_W2 > CLIP_GRAD:
            grad_W_pred2 *= CLIP_GRAD / norm_W2
        norm_W1 = np.linalg.norm(grad_W_pred1)
        if norm_W1 > CLIP_GRAD:
            grad_W_pred1 *= CLIP_GRAD / norm_W1

        self.W_pred2 -= lr * grad_W_pred2
        self.b_pred2 -= lr * grad_b_pred2
        self.W_pred1 -= lr * grad_W_pred1
        self.b_pred1 -= lr * grad_b_pred1

        # Update fusion logits
        self.multi_scale.fusion_logits -= lr * 0.01 * np.random.randn(self.multi_scale.n_scales)

        self.n_updates += 1

        return {
            "loss": loss,
            "n_updates": self.n_updates,
            "pred_mean": float(predictions.mean()),
            "pred_std": float(predictions.std()),
        }

    def save(self, path: Optional[Path] = None) -> str:
        """Save model to disk."""
        save_path = path or (MODEL_DIR / "msgformer_latest.npz")
        save_path.parent.mkdir(parents=True, exist_ok=True)

        weights: Dict[str, np.ndarray] = {
            "W_pred1": self.W_pred1,
            "b_pred1": self.b_pred1,
            "W_pred2": self.W_pred2,
            "b_pred2": self.b_pred2,
            "W_contagion": self.W_contagion,
            "fusion_logits": self.multi_scale.fusion_logits,
        }

        # Save per-scale graph layer weights
        for name in self.scale_names:
            gl = self.multi_scale.graph_layers[name]
            for k in range(gl.n_heads):
                weights[f"graph_{name}_W_{k}"] = gl.W[k]
                weights[f"graph_{name}_a_src_{k}"] = gl.a_src[k]
                weights[f"graph_{name}_a_dst_{k}"] = gl.a_dst[k]
            weights[f"graph_{name}_W_out"] = gl.W_out
            weights[f"graph_{name}_b_out"] = gl.b_out

        # Save per-scale temporal layer weights
        for name in self.scale_names:
            tl = self.multi_scale.temporal_layers[name]
            weights[f"temp_{name}_W_q"] = tl.W_q
            weights[f"temp_{name}_W_k"] = tl.W_k
            weights[f"temp_{name}_W_v"] = tl.W_v
            weights[f"temp_{name}_W_o"] = tl.W_o
            weights[f"temp_{name}_W_ff1"] = tl.W_ff1
            weights[f"temp_{name}_b_ff1"] = tl.b_ff1
            weights[f"temp_{name}_W_ff2"] = tl.W_ff2
            weights[f"temp_{name}_b_ff2"] = tl.b_ff2

        np.savez(str(save_path), **weights)
        log.info("MSGformerModel saved to %s (%d weight arrays)", save_path, len(weights))
        return str(save_path)

    def load(self, path: Optional[Path] = None) -> bool:
        """Load model from disk."""
        load_path = path or (MODEL_DIR / "msgformer_latest.npz")
        if not load_path.exists():
            log.warning("MSGformerModel: no saved model at %s", load_path)
            return False

        try:
            data = np.load(str(load_path))

            self.W_pred1 = data["W_pred1"]
            self.b_pred1 = data["b_pred1"]
            self.W_pred2 = data["W_pred2"]
            self.b_pred2 = data["b_pred2"]
            self.W_contagion = data["W_contagion"]
            self.multi_scale.fusion_logits = data["fusion_logits"]

            for name in self.scale_names:
                gl = self.multi_scale.graph_layers[name]
                for k in range(gl.n_heads):
                    key_W = f"graph_{name}_W_{k}"
                    key_src = f"graph_{name}_a_src_{k}"
                    key_dst = f"graph_{name}_a_dst_{k}"
                    if key_W in data:
                        gl.W[k] = data[key_W]
                        gl.a_src[k] = data[key_src]
                        gl.a_dst[k] = data[key_dst]
                key_out = f"graph_{name}_W_out"
                if key_out in data:
                    gl.W_out = data[key_out]
                    gl.b_out = data[f"graph_{name}_b_out"]

                tl = self.multi_scale.temporal_layers[name]
                key_q = f"temp_{name}_W_q"
                if key_q in data:
                    tl.W_q = data[key_q]
                    tl.W_k = data[f"temp_{name}_W_k"]
                    tl.W_v = data[f"temp_{name}_W_v"]
                    tl.W_o = data[f"temp_{name}_W_o"]
                    tl.W_ff1 = data[f"temp_{name}_W_ff1"]
                    tl.b_ff1 = data[f"temp_{name}_b_ff1"]
                    tl.W_ff2 = data[f"temp_{name}_W_ff2"]
                    tl.b_ff2 = data[f"temp_{name}_b_ff2"]

            log.info("MSGformerModel loaded from %s", load_path)
            return True
        except Exception as e:
            log.error("MSGformerModel load failed: %s", e)
            return False

    def get_config(self) -> Dict[str, Any]:
        """Return model configuration."""
        total_params = 0
        for name in self.scale_names:
            gl = self.multi_scale.graph_layers[name]
            for k in range(gl.n_heads):
                total_params += gl.W[k].size + gl.a_src[k].size + gl.a_dst[k].size
            total_params += gl.W_out.size + gl.b_out.size

            tl = self.multi_scale.temporal_layers[name]
            total_params += (tl.W_q.size + tl.W_k.size + tl.W_v.size + tl.W_o.size +
                             tl.W_ff1.size + tl.b_ff1.size + tl.W_ff2.size + tl.b_ff2.size)

        total_params += (self.W_pred1.size + self.b_pred1.size +
                         self.W_pred2.size + self.b_pred2.size +
                         self.W_contagion.size +
                         self.multi_scale.fusion_logits.size)

        return {
            "d_model": self.d_model,
            "n_graph_heads": self.n_graph_heads,
            "n_temporal_heads": self.n_temporal_heads,
            "d_output": self.d_output,
            "scale_names": self.scale_names,
            "n_updates": self.n_updates,
            "total_params": total_params,
            "fusion_weights": self.multi_scale.get_fusion_weights(),
            "contagion_threshold": self.contagion_threshold,
        }
