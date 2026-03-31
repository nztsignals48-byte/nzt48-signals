"""Graph Neural Network for Market Structure — Book 96.

Models market structure as a graph: instruments are nodes, relationships
(sector, correlation, ETP pairing, lead-lag) are edges. GCN and GAT
layers propagate information through the graph to produce node-level
predictions (bullish/bearish probabilities per instrument).

Pure numpy implementation — no PyTorch/DGL/PyG dependency.

Architecture:
  - MarketGraphBuilder: constructs multi-relational market graph
  - GCNLayer: Graph Convolution (Kipf & Welling 2017)
  - GATLayer: Graph Attention (Velickovic et al. 2018)
  - MarketGNN: 2-layer GCN/GAT, returns node embeddings + predictions
  - GNNSignalGenerator: build graph, run GNN, return signals

State: /app/data/gnn_graphs/ (daily graph snapshots)

Bridge.py integration:
    try:
        from python_brain.ml.gnn_market_structure import (
            GNNSignalGenerator, MarketGraphBuilder,
        )
    except ImportError:
        pass

Usage:
    from python_brain.ml.gnn_market_structure import (
        GNNSignalGenerator, MarketGraphBuilder, MarketGNN,
    )

    builder = MarketGraphBuilder(instruments, sector_map)
    graph = builder.build_combined_graph(returns, etp_pairs)
    gnn = MarketGNN(n_features=graph.node_features.shape[1])
    predictions = gnn.predict(graph)
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)

__all__ = [
    "EdgeType",
    "MarketGraph",
    "MarketGraphBuilder",
    "GCNLayer",
    "GATLayer",
    "MarketGNN",
    "GNNSignalGenerator",
]

# ── Constants ─────────────────────────────────────────────────────────

STATE_DIR = Path("/app/data/gnn_graphs")
DEFAULT_CORRELATION_THRESHOLD = 0.5
DEFAULT_CORRELATION_WINDOW = 20
DEFAULT_MAX_LAG = 5


# ── Enums & Dataclasses ──────────────────────────────────────────────

class EdgeType(Enum):
    SECTOR = "sector"
    SUPPLY_CHAIN = "supply_chain"
    ETF_HOLDING = "etf_holding"
    CORRELATION = "correlation"
    LEVERAGED_PAIR = "leveraged_pair"
    INVERSE_PAIR = "inverse_pair"
    LEAD_LAG = "lead_lag"


@dataclass
class MarketGraph:
    """Multi-relational market structure graph."""
    nodes: list                     # Instrument names/tickers
    adjacency: np.ndarray           # (N, N) weighted adjacency matrix
    edge_types: np.ndarray          # (N, N) int-encoded edge types
    node_features: np.ndarray       # (N, F) feature matrix per node


# ── Graph Builder ────────────────────────────────────────────────────

class MarketGraphBuilder:
    """Build market structure graphs from instrument relationships.

    Constructs multi-relational adjacency matrices from sector membership,
    return correlations, ETP pair mappings, and lead-lag relationships.
    """

    def __init__(self, instruments: list, sector_map: dict):
        """
        Args:
            instruments: List of instrument tickers.
            sector_map: ticker -> sector_name mapping.
        """
        self.instruments = instruments
        self.sector_map = sector_map
        self.n = len(instruments)
        self._idx = {ticker: i for i, ticker in enumerate(instruments)}

    def build_sector_graph(self) -> np.ndarray:
        """Build adjacency from same-sector relationships.

        Instruments in the same sector get edges with weight 1.0,
        then degree-normalized (D^{-1} @ A) for stable GCN propagation.

        Returns:
            (N, N) degree-normalized adjacency matrix.
        """
        A = np.zeros((self.n, self.n))

        for i, t1 in enumerate(self.instruments):
            s1 = self.sector_map.get(t1)
            if s1 is None:
                continue
            for j, t2 in enumerate(self.instruments):
                if i == j:
                    continue
                s2 = self.sector_map.get(t2)
                if s1 == s2 and s2 is not None:
                    A[i, j] = 1.0

        # Degree normalization
        degree = np.sum(A, axis=1, keepdims=True)
        degree = np.where(degree < 1e-8, 1.0, degree)
        A = A / degree

        return A

    def build_correlation_graph(
        self,
        returns: np.ndarray,
        threshold: float = DEFAULT_CORRELATION_THRESHOLD,
        window: int = DEFAULT_CORRELATION_WINDOW,
    ) -> np.ndarray:
        """Build adjacency from rolling return correlations.

        Only correlations above the threshold become edges.

        Args:
            returns: (T, N) return matrix (T time steps, N instruments).
            threshold: Minimum absolute correlation for an edge.
            window: Rolling window for correlation computation.

        Returns:
            (N, N) correlation-based adjacency matrix.
        """
        T, N = returns.shape
        if N != self.n:
            log.warning("Returns columns (%d) != instruments (%d)", N, self.n)
            return np.zeros((self.n, self.n))

        if T < window:
            log.warning("Not enough data for correlation window: %d < %d", T, window)
            return np.zeros((self.n, self.n))

        # Use most recent window for rolling correlation
        recent = returns[-window:]
        # Correlation matrix
        stds = np.std(recent, axis=0, keepdims=True)
        stds = np.where(stds < 1e-8, 1.0, stds)
        normalized = (recent - np.mean(recent, axis=0, keepdims=True)) / stds
        corr = (normalized.T @ normalized) / window

        # Threshold: only strong correlations become edges
        A = np.where(np.abs(corr) >= threshold, np.abs(corr), 0.0)
        np.fill_diagonal(A, 0.0)

        return A

    def build_etp_graph(self, etp_pairs: dict) -> np.ndarray:
        """Build adjacency from ETP pair relationships.

        Args:
            etp_pairs: Dict of pair relationships:
                {"leveraged": [("3USL", "SPY"), ...],
                 "inverse": [("3USS", "SPY"), ...]}

        Returns:
            (N, N) ETP relationship adjacency matrix.
        """
        A = np.zeros((self.n, self.n))

        for pair_type in ("leveraged", "inverse"):
            pairs = etp_pairs.get(pair_type, [])
            for t1, t2 in pairs:
                i = self._idx.get(t1)
                j = self._idx.get(t2)
                if i is not None and j is not None:
                    # Bidirectional edge
                    A[i, j] = 1.0
                    A[j, i] = 1.0

        return A

    def build_lead_lag_graph(
        self, returns: np.ndarray, max_lag: int = DEFAULT_MAX_LAG
    ) -> np.ndarray:
        """Build adjacency from cross-correlation lead-lag relationships.

        For each pair (i, j), compute cross-correlation at lags 1..max_lag.
        If j leads i (max cross-corr at positive lag), add directed edge j→i.

        Args:
            returns: (T, N) return matrix.
            max_lag: Maximum lag to test.

        Returns:
            (N, N) directed lead-lag adjacency matrix.
        """
        T, N = returns.shape
        if N != self.n or T < max_lag + 10:
            return np.zeros((self.n, self.n))

        A = np.zeros((self.n, self.n))

        for i in range(N):
            for j in range(N):
                if i == j:
                    continue

                best_corr = 0.0
                best_lag = 0

                for lag in range(1, max_lag + 1):
                    if lag >= T:
                        break
                    # Does j at time t predict i at time t+lag?
                    x = returns[:-lag, j]
                    y = returns[lag:, i]
                    n_obs = min(len(x), len(y))
                    if n_obs < 10:
                        continue
                    x = x[:n_obs]
                    y = y[:n_obs]

                    x_std = np.std(x)
                    y_std = np.std(y)
                    if x_std < 1e-8 or y_std < 1e-8:
                        continue

                    corr = float(
                        np.mean((x - np.mean(x)) * (y - np.mean(y)))
                        / (x_std * y_std)
                    )

                    if abs(corr) > abs(best_corr):
                        best_corr = corr
                        best_lag = lag

                # Significant lead-lag relationship
                if abs(best_corr) > 0.3 and best_lag > 0:
                    A[j, i] = abs(best_corr)  # j leads i

        return A

    def build_combined_graph(
        self, returns: np.ndarray, etp_pairs: dict
    ) -> MarketGraph:
        """Merge all edge types into a single multi-relational graph.

        Args:
            returns: (T, N) return matrix.
            etp_pairs: ETP pair definitions.

        Returns:
            MarketGraph with combined adjacency and edge type labels.
        """
        A_sector = self.build_sector_graph()
        A_corr = self.build_correlation_graph(returns)
        A_etp = self.build_etp_graph(etp_pairs)
        A_lead_lag = self.build_lead_lag_graph(returns)

        # Combined adjacency: weighted sum
        A_combined = (
            0.3 * A_sector
            + 0.3 * A_corr
            + 0.2 * A_etp
            + 0.2 * A_lead_lag
        )

        # Edge type matrix (dominant type per edge)
        edge_types = np.zeros((self.n, self.n), dtype=int)
        for i in range(self.n):
            for j in range(self.n):
                if A_combined[i, j] < 1e-6:
                    continue
                # Pick strongest signal
                scores = {
                    EdgeType.SECTOR.value: A_sector[i, j],
                    EdgeType.CORRELATION.value: A_corr[i, j],
                    EdgeType.LEVERAGED_PAIR.value: A_etp[i, j],
                    EdgeType.LEAD_LAG.value: A_lead_lag[i, j],
                }
                best_type = max(scores, key=scores.get)
                # Encode edge type as int
                type_map = {t.value: idx for idx, t in enumerate(EdgeType)}
                edge_types[i, j] = type_map.get(best_type, 0)

        # Node features: recent return statistics
        node_features = self._compute_node_features(returns)

        graph = MarketGraph(
            nodes=self.instruments,
            adjacency=A_combined,
            edge_types=edge_types,
            node_features=node_features,
        )

        log.info(
            "Built combined graph: %d nodes, %d edges",
            self.n, int(np.sum(A_combined > 1e-6)),
        )
        return graph

    def _compute_node_features(self, returns: np.ndarray) -> np.ndarray:
        """Compute basic statistical features per node.

        Features: mean return, std, skew, kurtosis, max drawdown,
        recent momentum (5-bar, 20-bar).
        """
        T, N = returns.shape
        features = np.zeros((N, 7))

        for i in range(N):
            r = returns[:, i]
            features[i, 0] = np.mean(r)
            features[i, 1] = np.std(r)
            # Skewness
            std = features[i, 1]
            if std > 1e-8:
                features[i, 2] = float(np.mean(((r - np.mean(r)) / std) ** 3))
                features[i, 3] = float(np.mean(((r - np.mean(r)) / std) ** 4) - 3.0)
            # Max drawdown
            cum = np.cumsum(r)
            running_max = np.maximum.accumulate(cum)
            drawdowns = running_max - cum
            features[i, 4] = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0
            # Momentum
            features[i, 5] = float(np.sum(r[-5:])) if T >= 5 else 0.0
            features[i, 6] = float(np.sum(r[-20:])) if T >= 20 else 0.0

        return features


# ── GCN Layer ────────────────────────────────────────────────────────

class GCNLayer:
    """Graph Convolutional layer (Kipf & Welling 2017).

    X' = ReLU(A_hat @ X @ W)
    where A_hat = D^{-1/2} @ (A + I) @ D^{-1/2}
    """

    def __init__(self, in_features: int, out_features: int):
        self.in_features = in_features
        self.out_features = out_features
        # Xavier initialization
        scale = math.sqrt(2.0 / (in_features + out_features))
        rng = np.random.RandomState(42)
        self.W = rng.randn(in_features, out_features) * scale
        self.bias = np.zeros(out_features)
        self._last_output: Optional[np.ndarray] = None

    def forward(self, X: np.ndarray, A: np.ndarray) -> np.ndarray:
        """Forward pass: graph convolution.

        Args:
            X: (N, in_features) node feature matrix.
            A: (N, N) adjacency matrix.

        Returns:
            (N, out_features) updated node features.
        """
        A_hat = self._add_self_loops(A)
        # Message passing + linear transform
        out = A_hat @ X @ self.W + self.bias
        # ReLU activation
        out = np.maximum(out, 0)
        self._last_output = out
        return out

    def _add_self_loops(self, A: np.ndarray) -> np.ndarray:
        """Compute normalized adjacency: D^{-1/2} @ (A + I) @ D^{-1/2}.

        Args:
            A: (N, N) adjacency matrix.

        Returns:
            (N, N) symmetrically normalized adjacency with self-loops.
        """
        N = A.shape[0]
        A_tilde = A + np.eye(N)

        # Degree matrix
        D = np.sum(A_tilde, axis=1)
        D_inv_sqrt = np.where(D > 1e-8, 1.0 / np.sqrt(D), 0.0)
        D_inv_sqrt_mat = np.diag(D_inv_sqrt)

        # Symmetric normalization
        A_hat = D_inv_sqrt_mat @ A_tilde @ D_inv_sqrt_mat
        return A_hat


# ── GAT Layer ────────────────────────────────────────────────────────

class GATLayer:
    """Graph Attention layer (Velickovic et al. 2018).

    Multi-head attention with learned edge weights.
    Numpy-only implementation.
    """

    def __init__(
        self,
        in_features: int,
        out_features: int,
        n_heads: int = 4,
    ):
        self.in_features = in_features
        self.out_features = out_features
        self.n_heads = n_heads
        self.head_dim = out_features // n_heads

        rng = np.random.RandomState(42)
        scale = math.sqrt(2.0 / (in_features + self.head_dim))

        # Per-head weight matrices
        self.W = rng.randn(n_heads, in_features, self.head_dim) * scale
        # Attention parameters: a = [a_left || a_right] per head
        self.a_left = rng.randn(n_heads, self.head_dim) * 0.1
        self.a_right = rng.randn(n_heads, self.head_dim) * 0.1

        self._attention_weights: Optional[np.ndarray] = None

    def forward(self, X: np.ndarray, A: np.ndarray) -> np.ndarray:
        """Multi-head graph attention forward pass.

        Args:
            X: (N, in_features) node feature matrix.
            A: (N, N) adjacency matrix (used as attention mask).

        Returns:
            (N, out_features) updated node features (concatenated heads).
        """
        N = X.shape[0]
        head_outputs = []

        attention_all = np.zeros((N, N))

        for h in range(self.n_heads):
            # Linear transformation for this head
            Xh = X @ self.W[h]  # (N, head_dim)

            # Attention coefficients
            alpha = self._attention_coefficients(Xh, A, h)  # (N, N)
            attention_all += alpha / self.n_heads

            # Weighted aggregation
            out_h = alpha @ Xh  # (N, head_dim)
            head_outputs.append(out_h)

        self._attention_weights = attention_all

        # Concatenate heads
        out = np.concatenate(head_outputs, axis=1)  # (N, out_features)
        return out

    def _attention_coefficients(
        self, Xh: np.ndarray, A: np.ndarray, head_idx: int
    ) -> np.ndarray:
        """Compute attention coefficients for one head.

        e_ij = LeakyReLU(a_left^T @ h_i + a_right^T @ h_j)
        alpha_ij = softmax_j(e_ij) * mask(A_ij > 0)

        Args:
            Xh: (N, head_dim) transformed node features.
            A: (N, N) adjacency (attention mask).
            head_idx: Which attention head.

        Returns:
            (N, N) attention coefficient matrix.
        """
        N = Xh.shape[0]

        # Compute attention logits
        e_left = Xh @ self.a_left[head_idx]    # (N,)
        e_right = Xh @ self.a_right[head_idx]  # (N,)

        # Pairwise attention: e_ij = e_left_i + e_right_j
        e = e_left.reshape(-1, 1) + e_right.reshape(1, -1)  # (N, N)

        # LeakyReLU (negative slope = 0.2)
        e = np.where(e > 0, e, 0.2 * e)

        # Mask: only attend to neighbors + self
        mask = (A > 1e-8) | np.eye(N, dtype=bool)
        e = np.where(mask, e, -1e9)

        # Softmax over neighbors
        e_max = np.max(e, axis=1, keepdims=True)
        e_exp = np.exp(e - e_max)
        e_exp = np.where(mask, e_exp, 0.0)
        alpha = e_exp / (np.sum(e_exp, axis=1, keepdims=True) + 1e-8)

        return alpha


# ── Market GNN ───────────────────────────────────────────────────────

class MarketGNN:
    """Two-layer GCN/GAT for market structure analysis.

    Produces node embeddings and per-instrument bullish/bearish predictions.
    """

    def __init__(
        self,
        n_features: int,
        hidden_dim: int = 64,
        n_layers: int = 2,
        n_heads: int = 4,
        dropout: float = 0.1,
    ):
        self.n_features = n_features
        self.hidden_dim = hidden_dim
        self.n_layers = n_layers
        self.dropout = dropout

        # Layer 1: GCN for broad message passing
        self.gcn1 = GCNLayer(n_features, hidden_dim)
        # Layer 2: GAT for attention-weighted refinement
        self.gat1 = GATLayer(hidden_dim, hidden_dim, n_heads=n_heads)

        # Output projection: node embedding → bullish probability
        rng = np.random.RandomState(42)
        self.out_W = rng.randn(hidden_dim, 1) * math.sqrt(2.0 / hidden_dim)
        self.out_bias = np.zeros(1)

        self._last_embeddings: Optional[np.ndarray] = None

    def forward(self, graph: MarketGraph) -> np.ndarray:
        """Forward pass: 2-layer GCN/GAT, returns node embeddings.

        Args:
            graph: MarketGraph with node_features and adjacency.

        Returns:
            (N, hidden_dim) node embeddings.
        """
        X = graph.node_features
        A = graph.adjacency

        # Layer 1: GCN
        H = self.gcn1.forward(X, A)

        # Dropout (at training time — for inference, skip)
        if self.dropout > 0:
            mask = (np.random.rand(*H.shape) > self.dropout).astype(float)
            H = H * mask / (1.0 - self.dropout)

        # Layer 2: GAT
        H = self.gat1.forward(H, A)

        # ReLU on final embeddings
        H = np.maximum(H, 0)
        self._last_embeddings = H

        return H

    def predict(self, graph: MarketGraph) -> dict:
        """Node-level prediction: bullish/bearish probability per instrument.

        Args:
            graph: MarketGraph.

        Returns:
            Dict of ticker -> {"bullish_prob": float, "bearish_prob": float}
        """
        embeddings = self.forward(graph)

        # Linear projection + sigmoid
        logits = embeddings @ self.out_W + self.out_bias  # (N, 1)
        probs = 1.0 / (1.0 + np.exp(-np.clip(logits.ravel(), -30, 30)))

        predictions = {}
        for i, ticker in enumerate(graph.nodes):
            predictions[ticker] = {
                "bullish_prob": float(probs[i]),
                "bearish_prob": float(1.0 - probs[i]),
            }

        return predictions

    def get_attention_weights(self) -> np.ndarray:
        """Return attention weights from the GAT layer.

        Shows which edges (instrument relationships) the model
        considers most important.

        Returns:
            (N, N) attention weight matrix.
        """
        if self.gat1._attention_weights is not None:
            return self.gat1._attention_weights
        return np.array([])


# ── Signal Generator ─────────────────────────────────────────────────

class GNNSignalGenerator:
    """Generate trading signals from GNN market structure analysis.

    Builds a market graph from current data, runs GNN forward pass,
    and converts node predictions into directional signals.
    """

    def __init__(
        self,
        instruments: list,
        sector_map: dict,
        etp_pairs: dict,
    ):
        self.instruments = instruments
        self.sector_map = sector_map
        self.etp_pairs = etp_pairs
        self.builder = MarketGraphBuilder(instruments, sector_map)
        self._gnn: Optional[MarketGNN] = None
        self._previous_graph: Optional[MarketGraph] = None
        self._state_dir = STATE_DIR

    def generate_signals(
        self, returns: np.ndarray, features: np.ndarray
    ) -> dict:
        """Build graph, run GNN, return signals per instrument.

        Args:
            returns: (T, N) return matrix.
            features: (N, F) per-instrument feature matrix.

        Returns:
            Dict of ticker -> {"direction": str, "confidence": float,
                               "graph_context": dict}
        """
        # Build graph
        graph = self.builder.build_combined_graph(returns, self.etp_pairs)

        # Override node features if external features provided
        if features is not None and features.shape[0] == len(self.instruments):
            graph.node_features = features

        # Lazy-init GNN with correct feature dimension
        n_feat = graph.node_features.shape[1]
        if self._gnn is None or self._gnn.n_features != n_feat:
            self._gnn = MarketGNN(n_features=n_feat)

        # Run GNN
        predictions = self._gnn.predict(graph)

        # Convert to signals
        signals = {}
        for ticker, pred in predictions.items():
            bp = pred["bullish_prob"]
            if bp > 0.55:
                direction = "LONG"
                confidence = min(100.0, bp * 100)
            elif bp < 0.45:
                direction = "SHORT"
                confidence = min(100.0, (1.0 - bp) * 100)
            else:
                direction = "FLAT"
                confidence = 50.0

            # Graph context: attention from neighbors
            attn = self._gnn.get_attention_weights()
            idx = self.instruments.index(ticker) if ticker in self.instruments else -1
            top_neighbors = []
            if idx >= 0 and attn.size > 0:
                neighbor_weights = attn[idx]
                top_idx = np.argsort(neighbor_weights)[-3:][::-1]
                for ni in top_idx:
                    if neighbor_weights[ni] > 0.01:
                        top_neighbors.append({
                            "ticker": self.instruments[ni],
                            "attention": float(neighbor_weights[ni]),
                        })

            signals[ticker] = {
                "direction": direction,
                "confidence": float(confidence),
                "bullish_prob": float(bp),
                "graph_context": {
                    "n_edges": int(np.sum(graph.adjacency[idx] > 1e-6)) if idx >= 0 else 0,
                    "top_neighbors": top_neighbors,
                },
            }

        # Detect structural shifts
        if self._previous_graph is not None:
            shift = self._detect_structural_shift(graph, self._previous_graph)
            for ticker in signals:
                signals[ticker]["structural_shift"] = shift

        # Save snapshot
        self._previous_graph = graph
        self._save_graph_snapshot(graph)

        return signals

    def _detect_structural_shift(
        self, current_graph: MarketGraph, previous_graph: MarketGraph
    ) -> dict:
        """Detect topology changes between consecutive graphs.

        Identifies new edges, broken edges, and significant weight shifts.

        Args:
            current_graph: Today's market graph.
            previous_graph: Yesterday's market graph.

        Returns:
            Dict with shift diagnostics.
        """
        A_curr = current_graph.adjacency
        A_prev = previous_graph.adjacency

        if A_curr.shape != A_prev.shape:
            return {"status": "shape_mismatch"}

        # Edge presence changes
        curr_edges = A_curr > 1e-6
        prev_edges = A_prev > 1e-6

        new_edges = int(np.sum(curr_edges & ~prev_edges))
        broken_edges = int(np.sum(~curr_edges & prev_edges))

        # Weight shifts (on edges present in both)
        both_present = curr_edges & prev_edges
        if np.any(both_present):
            weight_diff = np.abs(A_curr - A_prev)
            mean_shift = float(np.mean(weight_diff[both_present]))
            max_shift = float(np.max(weight_diff[both_present]))
        else:
            mean_shift = 0.0
            max_shift = 0.0

        # Overall topology distance (Frobenius norm of adjacency difference)
        topo_distance = float(np.linalg.norm(A_curr - A_prev))

        shift = {
            "new_edges": new_edges,
            "broken_edges": broken_edges,
            "mean_weight_shift": mean_shift,
            "max_weight_shift": max_shift,
            "topology_distance": topo_distance,
            "significant": topo_distance > 1.0,
        }

        if shift["significant"]:
            log.warning(
                "Structural shift detected: %d new, %d broken, dist=%.3f",
                new_edges, broken_edges, topo_distance,
            )

        return shift

    def _save_graph_snapshot(self, graph: MarketGraph) -> None:
        """Save daily graph snapshot for analysis."""
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
            path = self._state_dir / f"graph_{date_str}.json"

            snapshot = {
                "date": date_str,
                "nodes": graph.nodes,
                "n_edges": int(np.sum(graph.adjacency > 1e-6)),
                "adjacency_density": float(
                    np.sum(graph.adjacency > 1e-6)
                    / max(graph.adjacency.size - len(graph.nodes), 1)
                ),
                "mean_edge_weight": float(
                    np.mean(graph.adjacency[graph.adjacency > 1e-6])
                ) if np.any(graph.adjacency > 1e-6) else 0.0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            with open(str(path), "w") as f:
                json.dump(snapshot, f, indent=2)
            log.info("Graph snapshot saved: %s", path)
        except Exception as e:
            log.warning("Failed to save graph snapshot: %s", e)
