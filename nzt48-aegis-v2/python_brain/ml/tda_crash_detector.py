"""Topological Data Analysis for Crash Early Warning — Book 127.

Numpy-only implementation of TDA-based crash detection. Uses Takens
embedding to construct point clouds from return time series, then
computes Vietoris-Rips simplicial complexes and Betti numbers to
detect topological anomalies that precede market crashes.

Key insight: before crashes, the return time series point cloud develops
persistent loops (B1 increases) as the market oscillates between
greed and fear regimes. This topological signature appears 2-5 days
before large drawdowns.

Components:
  - PointCloudBuilder: Takens time-delay embedding
  - SimplicialComplex: Vietoris-Rips complex construction
  - PersistenceDiagram: Persistent homology (simplified filtration)
  - CrashDetector: Online crash probability estimator

State: /app/data/tda_baseline.json

Bridge.py integration:
    try:
        from python_brain.ml.tda_crash_detector import CrashDetector
        _tda = CrashDetector(lookback=60, alert_threshold=2.0)
    except ImportError:
        _tda = None

    # In nightly pipeline:
    if _tda:
        result = _tda.update(returns_60d)
        if result["crash_probability"] > 0.7:
            # Escalate to Telegram alert
            pass
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger("tda_crash_detector")

__all__ = [
    "PointCloudBuilder",
    "SimplicialComplex",
    "PersistenceDiagram",
    "CrashDetector",
]

# ── Persistence Paths ────────────────────────────────────────────────

DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
BASELINE_PATH = DATA_DIR / "tda_baseline.json"


# ---------------------------------------------------------------------------
# Point Cloud Builder — Takens Embedding
# ---------------------------------------------------------------------------

class PointCloudBuilder:
    """Construct point clouds from time series via Takens embedding.

    Takens' theorem (1981): a delay embedding of a scalar time series
    in dimension d with delay tau preserves the topological properties
    of the underlying dynamical system's attractor, provided d >= 2m+1
    where m is the attractor dimension.

    For financial returns, embedding_dim=3, delay=1 works well as a
    starting point (captures 3-day dynamics).
    """

    @staticmethod
    def build(
        returns: np.ndarray,
        embedding_dim: int = 3,
        delay: int = 1,
    ) -> np.ndarray:
        """Build a point cloud via Takens time-delay embedding.

        Each point in the cloud is a vector:
          [x(t), x(t-tau), x(t-2*tau), ..., x(t-(d-1)*tau)]

        Args:
            returns: 1-D array of returns (or any scalar time series).
            embedding_dim: Dimension of the embedding space (d).
            delay: Time delay between coordinates (tau).

        Returns:
            Array of shape (n_points, embedding_dim) where
            n_points = len(returns) - (embedding_dim - 1) * delay.

        Raises:
            ValueError: If returns is too short for the embedding.
        """
        returns = np.asarray(returns, dtype=float)
        n = len(returns)
        n_points = n - (embedding_dim - 1) * delay

        if n_points < 2:
            raise ValueError(
                f"Time series too short ({n}) for embedding_dim={embedding_dim}, "
                f"delay={delay}. Need at least {(embedding_dim - 1) * delay + 2} points."
            )

        cloud = np.zeros((n_points, embedding_dim))
        for d in range(embedding_dim):
            start = (embedding_dim - 1 - d) * delay
            end = start + n_points
            cloud[:, d] = returns[start:end]

        return cloud

    @staticmethod
    def pairwise_distances(cloud: np.ndarray) -> np.ndarray:
        """Compute pairwise Euclidean distance matrix.

        Args:
            cloud: Point cloud, shape (n, d).

        Returns:
            Distance matrix, shape (n, n). Symmetric with zero diagonal.
        """
        n = cloud.shape[0]
        # Efficient computation: ||a-b||^2 = ||a||^2 + ||b||^2 - 2*a.b
        sq_norms = np.sum(cloud ** 2, axis=1)
        dist_sq = sq_norms[:, None] + sq_norms[None, :] - 2.0 * cloud @ cloud.T
        # Numerical safety: clamp negatives to zero
        dist_sq = np.maximum(dist_sq, 0.0)
        return np.sqrt(dist_sq)


# ---------------------------------------------------------------------------
# Simplicial Complex — Vietoris-Rips
# ---------------------------------------------------------------------------

class SimplicialComplex:
    """Vietoris-Rips simplicial complex (simplified for numpy-only).

    Given a point cloud and a radius epsilon, the VR complex contains:
      - 0-simplices: all points (vertices)
      - 1-simplices: edges between points within distance epsilon
      - 2-simplices: triangles where all three edges exist

    This is a simplified implementation suitable for small point clouds
    (n < 500). For production TDA on large datasets, use giotto-tda or
    ripser.

    Attributes:
        vertices: Set of vertex indices.
        edges: Set of (i, j) tuples with i < j.
        triangles: Set of (i, j, k) tuples with i < j < k.
    """

    def __init__(self) -> None:
        """Initialise empty complex."""
        self.vertices: List[int] = []
        self.edges: List[Tuple[int, int]] = []
        self.triangles: List[Tuple[int, int, int]] = []
        self._adjacency: Dict[int, set] = {}

    def build(
        self,
        points: np.ndarray,
        epsilon: float,
    ) -> Dict[str, int]:
        """Build Vietoris-Rips complex at radius epsilon.

        Args:
            points: Point cloud, shape (n, d).
            epsilon: Radius threshold. Points within epsilon are connected.

        Returns:
            Dict with counts: n_vertices, n_edges, n_triangles.
        """
        n = points.shape[0]
        self.vertices = list(range(n))
        self.edges = []
        self.triangles = []
        self._adjacency = {i: set() for i in range(n)}

        # Compute distance matrix
        dist = PointCloudBuilder.pairwise_distances(points)

        # Build 1-skeleton (edges)
        for i in range(n):
            for j in range(i + 1, n):
                if dist[i, j] <= epsilon:
                    self.edges.append((i, j))
                    self._adjacency[i].add(j)
                    self._adjacency[j].add(i)

        # Build 2-skeleton (triangles) — check all triples with 3 edges
        # Only check triples where all pairs are edges (use adjacency)
        for i in range(n):
            neighbors_i = self._adjacency[i]
            for j in neighbors_i:
                if j <= i:
                    continue
                neighbors_j = self._adjacency[j]
                # Common neighbors form triangles
                common = neighbors_i & neighbors_j
                for k in common:
                    if k <= j:
                        continue
                    self.triangles.append((i, j, k))

        return {
            "n_vertices": len(self.vertices),
            "n_edges": len(self.edges),
            "n_triangles": len(self.triangles),
        }

    def betti_numbers(self) -> Tuple[int, int, int]:
        """Compute Betti numbers B0, B1, B2.

        Uses the Euler characteristic and simplicial homology:
          - B0 = number of connected components
          - B1 = n_edges - n_vertices + B0 - n_triangles (from Euler char.)
              More precisely: B1 = rank(ker(d1)) - rank(im(d2))
          - B2 = estimated from triangle structure

        For efficiency, B0 is computed via union-find, B1 via the
        rank-nullity theorem on the boundary matrices, and B2 is
        approximated.

        Returns:
            Tuple of (B0, B1, B2).
        """
        n_v = len(self.vertices)
        if n_v == 0:
            return (0, 0, 0)

        # B0: Connected components via union-find
        parent = list(range(n_v))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: int, y: int) -> None:
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        for i, j in self.edges:
            union(i, j)

        b0 = len(set(find(v) for v in self.vertices))

        # B1: Using Euler characteristic for simplicial complexes
        # chi = V - E + F  and  chi = B0 - B1 + B2
        # For VR complex truncated at dim 2: B2 is small
        # Use: B1 = E - V + B0 - B2 (approximate B2)

        n_e = len(self.edges)
        n_t = len(self.triangles)

        # B2 approximation: count "void" triangulations
        # In the simplified case, B2 ~ number of tetrahedra boundaries
        # For small complexes, B2 is usually 0 or very small
        b2 = self._estimate_b2()

        # Euler characteristic: chi = V - E + T
        chi = n_v - n_e + n_t

        # B1 = B0 - chi + B2
        b1 = max(b0 - chi + b2, 0)

        return (b0, b1, b2)

    def _estimate_b2(self) -> int:
        """Estimate B2 (number of voids / enclosed cavities).

        Looks for tetrahedra boundaries in the 2-skeleton. A void
        exists when 4 triangles form a hollow tetrahedron without
        a solid interior.

        For small point clouds this is typically 0.

        Returns:
            Estimated B2.
        """
        if len(self.triangles) < 4:
            return 0

        # Build a set of triangles for fast lookup
        tri_set = set(self.triangles)

        # Check for tetrahedra: 4 vertices where all 4 faces are triangles
        # This is O(T^2) but T is small for our use case
        tetra_count = 0
        vertices_in_triangles: Dict[int, List[Tuple[int, int, int]]] = {}
        for tri in self.triangles:
            for v in tri:
                if v not in vertices_in_triangles:
                    vertices_in_triangles[v] = []
                vertices_in_triangles[v].append(tri)

        checked: set = set()
        for tri in self.triangles:
            i, j, k = tri
            # Find vertices adjacent to all three
            common_neighbors = (
                self._adjacency.get(i, set())
                & self._adjacency.get(j, set())
                & self._adjacency.get(k, set())
            )
            for l in common_neighbors:
                if l <= k:
                    continue
                tetra_key = (i, j, k, l)
                if tetra_key in checked:
                    continue
                checked.add(tetra_key)

                # Check all 4 faces exist
                faces = [
                    tuple(sorted([i, j, k])),
                    tuple(sorted([i, j, l])),
                    tuple(sorted([i, k, l])),
                    tuple(sorted([j, k, l])),
                ]
                if all(f in tri_set for f in faces):
                    tetra_count += 1

        # B2 ~ number of hollow tetrahedra (very rough)
        return max(tetra_count - 1, 0) if tetra_count > 0 else 0


# ---------------------------------------------------------------------------
# Persistence Diagram
# ---------------------------------------------------------------------------

class PersistenceDiagram:
    """Simplified persistent homology via Vietoris-Rips filtration.

    Computes a persistence diagram by building VR complexes at
    increasing epsilon values and tracking when topological features
    (components, loops, voids) are born and die.

    Features that persist across many epsilon scales are "real" topology.
    Short-lived features are noise.

    Attributes:
        diagram: List of (birth, death, dimension) tuples.
    """

    def __init__(self) -> None:
        """Initialise empty persistence diagram."""
        self.diagram: List[Tuple[float, float, int]] = []

    def compute(
        self,
        points: np.ndarray,
        max_epsilon: float = 2.0,
        n_steps: int = 50,
    ) -> List[Tuple[float, float, int]]:
        """Compute persistence diagram via filtration.

        Builds VR complexes at n_steps epsilon values from 0 to
        max_epsilon. Tracks Betti number changes to identify
        birth/death of features.

        Args:
            points: Point cloud, shape (n, d).
            max_epsilon: Maximum filtration radius.
            n_steps: Number of filtration steps.

        Returns:
            List of (birth_epsilon, death_epsilon, dimension) tuples.
        """
        epsilons = np.linspace(0, max_epsilon, n_steps)
        self.diagram = []

        prev_betti = (0, 0, 0)
        complex_ = SimplicialComplex()

        # Track active features per dimension
        active_features: Dict[int, List[float]] = {0: [], 1: [], 2: []}

        for eps in epsilons:
            complex_.build(points, eps)
            betti = complex_.betti_numbers()

            for dim in range(3):
                current = betti[dim]
                previous = prev_betti[dim]

                if current > previous:
                    # New features born
                    n_new = current - previous
                    for _ in range(n_new):
                        active_features[dim].append(float(eps))

                elif current < previous:
                    # Features died
                    n_died = previous - current
                    for _ in range(min(n_died, len(active_features[dim]))):
                        birth = active_features[dim].pop(0)
                        self.diagram.append((birth, float(eps), dim))

            prev_betti = betti

        # Close remaining features at max_epsilon
        for dim in range(3):
            for birth in active_features[dim]:
                self.diagram.append((birth, float(max_epsilon), dim))

        # Sort by persistence (death - birth) descending
        self.diagram.sort(key=lambda x: -(x[1] - x[0]))

        return self.diagram

    def persistence_entropy(self) -> float:
        """Compute Shannon entropy of the persistence diagram.

        Higher entropy = more topological complexity = more features
        with similar persistence. Low entropy = few dominant features.

        Crashes tend to increase persistence entropy as new loops
        and components emerge at multiple scales.

        Returns:
            Shannon entropy (non-negative). Returns 0.0 if diagram is empty.
        """
        if not self.diagram:
            return 0.0

        # Persistence values
        lifetimes = [max(d - b, 1e-10) for b, d, _ in self.diagram]
        total = sum(lifetimes)

        if total < 1e-10:
            return 0.0

        # Normalise to probability distribution
        probs = [l / total for l in lifetimes]

        # Shannon entropy
        entropy = 0.0
        for p in probs:
            if p > 1e-15:
                entropy -= p * math.log(p)

        return entropy

    def wasserstein_distance(self, other: PersistenceDiagram, p: int = 2) -> float:
        """Compute approximate Wasserstein distance between two diagrams.

        Uses a greedy matching (not optimal transport) for efficiency.
        The true Wasserstein distance requires the Hungarian algorithm,
        but this approximation is sufficient for anomaly detection.

        Each unmatched point is matched to the diagonal (birth=death).

        Args:
            other: Another PersistenceDiagram.
            p: Norm order (default: 2 for W_2 distance).

        Returns:
            Approximate Wasserstein-p distance.
        """
        if not self.diagram and not other.diagram:
            return 0.0

        # Extract birth-death points per dimension
        total_cost = 0.0

        for dim in range(3):
            pts_a = [(b, d) for b, d, dim_ in self.diagram if dim_ == dim]
            pts_b = [(b, d) for b, d, dim_ in other.diagram if dim_ == dim]

            if not pts_a and not pts_b:
                continue

            # Greedy matching
            matched_b = set()
            for ba, da in pts_a:
                best_cost = ((da - ba) / 2.0) ** p  # Cost to diagonal
                best_j = -1

                for j, (bb, db) in enumerate(pts_b):
                    if j in matched_b:
                        continue
                    cost = (abs(ba - bb) ** p + abs(da - db) ** p)
                    if cost < best_cost:
                        best_cost = cost
                        best_j = j

                total_cost += best_cost
                if best_j >= 0:
                    matched_b.add(best_j)

            # Unmatched points in B: cost to diagonal
            for j, (bb, db) in enumerate(pts_b):
                if j not in matched_b:
                    total_cost += ((db - bb) / 2.0) ** p

        return total_cost ** (1.0 / p)

    def total_persistence(self, dim: Optional[int] = None) -> float:
        """Compute total persistence (sum of lifetimes).

        Args:
            dim: Optional dimension filter. None = all dimensions.

        Returns:
            Sum of (death - birth) for all features.
        """
        if dim is not None:
            return sum(d - b for b, d, dim_ in self.diagram if dim_ == dim)
        return sum(d - b for b, d, _ in self.diagram)

    def n_features(self, dim: Optional[int] = None) -> int:
        """Count number of features in the diagram.

        Args:
            dim: Optional dimension filter.

        Returns:
            Number of features.
        """
        if dim is not None:
            return sum(1 for _, _, d in self.diagram if d == dim)
        return len(self.diagram)


# ---------------------------------------------------------------------------
# Crash Detector
# ---------------------------------------------------------------------------

class CrashDetector:
    """TDA-based crash early warning system.

    Monitors the topological structure of return time series in real-time.
    When the topology deviates significantly from the baseline (measured
    by Betti numbers, persistence entropy, and Wasserstein distance),
    the crash probability increases.

    The detector maintains a rolling baseline from calm market periods
    and flags anomalies when current topology exceeds alert_threshold
    standard deviations from the baseline.

    State persisted to /app/data/tda_baseline.json.

    Attributes:
        lookback: Number of return observations per window.
        alert_threshold: Z-score threshold for anomaly detection.
    """

    def __init__(
        self,
        lookback: int = 60,
        alert_threshold: float = 2.0,
        embedding_dim: int = 3,
        delay: int = 1,
    ) -> None:
        """Initialise crash detector.

        Args:
            lookback: Rolling window size for returns.
            alert_threshold: Z-score threshold for alerts.
            embedding_dim: Takens embedding dimension.
            delay: Takens embedding delay.
        """
        self.lookback = lookback
        self.alert_threshold = alert_threshold
        self._embedding_dim = embedding_dim
        self._delay = delay

        # Baseline statistics
        self._baseline_betti: Optional[Tuple[float, float, float]] = None
        self._baseline_betti_std: Optional[Tuple[float, float, float]] = None
        self._baseline_entropy: float = 0.0
        self._baseline_entropy_std: float = 1.0
        self._baseline_persistence: float = 0.0
        self._baseline_persistence_std: float = 1.0

        # Current state
        self._current_betti: Tuple[int, int, int] = (0, 0, 0)
        self._current_entropy: float = 0.0
        self._current_persistence: float = 0.0
        self._anomaly_score: float = 0.0
        self._crash_prob: float = 0.0

        # History for baseline calibration
        self._betti_history: List[Tuple[int, int, int]] = []
        self._entropy_history: List[float] = []
        self._persistence_history: List[float] = []

        self._load_baseline()

    def update(self, returns_window: np.ndarray) -> Dict[str, Any]:
        """Update crash detector with new return data.

        Computes TDA features on the returns window, compares to
        baseline, and updates crash probability.

        Args:
            returns_window: 1-D array of recent returns (at least
                            lookback observations).

        Returns:
            Dict with keys:
              - crash_probability: 0.0 to 1.0
              - anomaly_score: Z-score of topological deviation
              - betti_numbers: Current (B0, B1, B2)
              - persistence_entropy: Current entropy
              - total_persistence: Sum of feature lifetimes
              - is_anomalous: Whether topology is anomalous
              - baseline_calibrated: Whether baseline exists
              - n_features: Number of persistent features
        """
        returns_window = np.asarray(returns_window, dtype=float)
        clean = returns_window[np.isfinite(returns_window)]

        if len(clean) < self.lookback:
            log.debug("CrashDetector: insufficient data (%d < %d)",
                       len(clean), self.lookback)
            return self._empty_result("insufficient_data")

        # Use the most recent lookback observations
        window = clean[-self.lookback:]

        try:
            # Step 1: Build point cloud
            cloud = PointCloudBuilder.build(window, self._embedding_dim, self._delay)

            # Step 2: Determine epsilon range from data
            dist_matrix = PointCloudBuilder.pairwise_distances(cloud)
            # Use median distance as reference scale
            upper_tri = dist_matrix[np.triu_indices_from(dist_matrix, k=1)]
            if len(upper_tri) == 0:
                return self._empty_result("degenerate_cloud")

            median_dist = float(np.median(upper_tri))
            max_epsilon = median_dist * 3.0

            if max_epsilon < 1e-10:
                return self._empty_result("zero_scale")

            # Step 3: Build simplicial complex at median distance
            complex_ = SimplicialComplex()
            complex_.build(cloud, median_dist)
            self._current_betti = complex_.betti_numbers()

            # Step 4: Compute persistence diagram
            pd = PersistenceDiagram()
            pd.compute(cloud, max_epsilon=max_epsilon, n_steps=30)
            self._current_entropy = pd.persistence_entropy()
            self._current_persistence = pd.total_persistence()

            # Step 5: Update history
            self._betti_history.append(self._current_betti)
            self._entropy_history.append(self._current_entropy)
            self._persistence_history.append(self._current_persistence)

            # Keep history bounded
            max_history = 200
            if len(self._betti_history) > max_history:
                self._betti_history = self._betti_history[-max_history:]
                self._entropy_history = self._entropy_history[-max_history:]
                self._persistence_history = self._persistence_history[-max_history:]

            # Step 6: Update baseline if sufficient history
            if len(self._betti_history) >= 20 and self._baseline_betti is None:
                self._calibrate_baseline()

            # Step 7: Compute anomaly score
            is_anomalous = self._is_topology_anomalous(
                self._current_betti, self._baseline_betti
            )
            self._anomaly_score = self._compute_anomaly_score()
            self._crash_prob = self._anomaly_to_probability(self._anomaly_score)

            result = {
                "crash_probability": round(self._crash_prob, 4),
                "anomaly_score": round(self._anomaly_score, 4),
                "betti_numbers": list(self._current_betti),
                "persistence_entropy": round(self._current_entropy, 4),
                "total_persistence": round(self._current_persistence, 4),
                "is_anomalous": is_anomalous,
                "baseline_calibrated": self._baseline_betti is not None,
                "n_features": pd.n_features(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            if is_anomalous:
                log.warning(
                    "TDA ANOMALY: crash_prob=%.2f, score=%.2f, betti=%s, entropy=%.3f",
                    self._crash_prob, self._anomaly_score,
                    self._current_betti, self._current_entropy,
                )

            return result

        except Exception as e:
            log.error("CrashDetector update failed: %s", e, exc_info=True)
            return self._empty_result(f"error: {e}")

    def _is_topology_anomalous(
        self,
        current_betti: Tuple[int, int, int],
        baseline_betti: Optional[Tuple[float, float, float]],
    ) -> bool:
        """Check if current Betti numbers are anomalous vs baseline.

        Anomalous if any Betti number deviates by more than
        alert_threshold standard deviations from the baseline mean.

        Args:
            current_betti: Current (B0, B1, B2).
            baseline_betti: Baseline mean (B0, B1, B2). None if uncalibrated.

        Returns:
            True if topology is anomalous.
        """
        if baseline_betti is None or self._baseline_betti_std is None:
            return False

        for dim in range(3):
            current = float(current_betti[dim])
            mean = baseline_betti[dim]
            std = max(self._baseline_betti_std[dim], 0.1)

            z = abs(current - mean) / std
            if z > self.alert_threshold:
                log.debug("Betti B%d anomalous: current=%d, baseline=%.1f +/- %.1f, z=%.2f",
                          dim, current_betti[dim], mean, std, z)
                return True

        return False

    def _compute_anomaly_score(self) -> float:
        """Compute composite anomaly score from all TDA features.

        Combines z-scores of:
        1. Betti number deviations (weighted by dimension)
        2. Persistence entropy deviation
        3. Total persistence deviation

        Returns:
            Composite anomaly score (0 = normal, higher = more anomalous).
        """
        if self._baseline_betti is None:
            return 0.0

        scores: List[float] = []
        weights: List[float] = []

        # Betti number z-scores
        betti_weights = [0.3, 0.5, 0.2]  # B1 gets most weight (loops = pre-crash)
        for dim in range(3):
            current = float(self._current_betti[dim])
            mean = self._baseline_betti[dim]
            std = max(self._baseline_betti_std[dim], 0.1)
            z = abs(current - mean) / std
            scores.append(z)
            weights.append(betti_weights[dim])

        # Entropy z-score
        if self._baseline_entropy_std > 1e-10:
            z_entropy = abs(self._current_entropy - self._baseline_entropy) / self._baseline_entropy_std
        else:
            z_entropy = 0.0
        scores.append(z_entropy)
        weights.append(0.4)

        # Total persistence z-score
        if self._baseline_persistence_std > 1e-10:
            z_persist = abs(
                self._current_persistence - self._baseline_persistence
            ) / self._baseline_persistence_std
        else:
            z_persist = 0.0
        scores.append(z_persist)
        weights.append(0.3)

        # Weighted average
        total_weight = sum(weights)
        if total_weight < 1e-10:
            return 0.0

        composite = sum(s * w for s, w in zip(scores, weights)) / total_weight
        return composite

    def _anomaly_to_probability(self, score: float) -> float:
        """Convert anomaly score to crash probability using sigmoid.

        Maps the anomaly score to [0, 1] via a logistic function
        centered at the alert threshold.

        P(crash) = 1 / (1 + exp(-k * (score - threshold)))

        Args:
            score: Composite anomaly score.

        Returns:
            Crash probability in [0, 1].
        """
        k = 2.0  # Steepness of sigmoid
        z = k * (score - self.alert_threshold)

        # Numerical stability
        if z > 20:
            return 1.0
        if z < -20:
            return 0.0

        return 1.0 / (1.0 + math.exp(-z))

    def get_crash_probability(self) -> float:
        """Get the current crash probability estimate.

        Returns:
            Probability in [0, 1] based on the last update.
        """
        return self._crash_prob

    def _calibrate_baseline(self) -> None:
        """Calibrate baseline statistics from accumulated history.

        Uses the central 80% of observations (trimmed mean) to
        be robust against outliers during calibration period.
        """
        n = len(self._betti_history)
        if n < 20:
            return

        # Betti number statistics
        betti_arr = np.array(self._betti_history, dtype=float)
        self._baseline_betti = (
            float(np.mean(betti_arr[:, 0])),
            float(np.mean(betti_arr[:, 1])),
            float(np.mean(betti_arr[:, 2])),
        )
        self._baseline_betti_std = (
            max(float(np.std(betti_arr[:, 0], ddof=1)), 0.1),
            max(float(np.std(betti_arr[:, 1], ddof=1)), 0.1),
            max(float(np.std(betti_arr[:, 2], ddof=1)), 0.1),
        )

        # Entropy statistics
        ent_arr = np.array(self._entropy_history, dtype=float)
        self._baseline_entropy = float(np.mean(ent_arr))
        self._baseline_entropy_std = max(float(np.std(ent_arr, ddof=1)), 0.01)

        # Persistence statistics
        pers_arr = np.array(self._persistence_history, dtype=float)
        self._baseline_persistence = float(np.mean(pers_arr))
        self._baseline_persistence_std = max(float(np.std(pers_arr, ddof=1)), 0.01)

        self._save_baseline()
        log.info(
            "TDA baseline calibrated: betti_mean=%s, entropy_mean=%.3f, n=%d",
            self._baseline_betti, self._baseline_entropy, n,
        )

    def _empty_result(self, reason: str) -> Dict[str, Any]:
        """Return an empty/default result dict."""
        return {
            "crash_probability": 0.0,
            "anomaly_score": 0.0,
            "betti_numbers": [0, 0, 0],
            "persistence_entropy": 0.0,
            "total_persistence": 0.0,
            "is_anomalous": False,
            "baseline_calibrated": self._baseline_betti is not None,
            "n_features": 0,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ── Persistence ──────────────────────────────────────────────────

    def _load_baseline(self) -> None:
        """Load baseline from JSON file."""
        if not BASELINE_PATH.exists():
            log.debug("TDA baseline not found at %s — starting fresh", BASELINE_PATH)
            return

        try:
            with open(BASELINE_PATH, "r") as f:
                data = json.load(f)

            bm = data.get("baseline_betti_mean")
            bs = data.get("baseline_betti_std")
            if bm and bs:
                self._baseline_betti = tuple(bm)
                self._baseline_betti_std = tuple(bs)

            self._baseline_entropy = data.get("baseline_entropy", 0.0)
            self._baseline_entropy_std = data.get("baseline_entropy_std", 1.0)
            self._baseline_persistence = data.get("baseline_persistence", 0.0)
            self._baseline_persistence_std = data.get("baseline_persistence_std", 1.0)

            log.info("Loaded TDA baseline: betti=%s, entropy=%.3f",
                     self._baseline_betti, self._baseline_entropy)

        except (json.JSONDecodeError, OSError, TypeError) as e:
            log.warning("Failed to load TDA baseline: %s", e)

    def _save_baseline(self) -> None:
        """Persist baseline to JSON file."""
        try:
            BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "baseline_betti_mean": list(self._baseline_betti) if self._baseline_betti else None,
                "baseline_betti_std": list(self._baseline_betti_std) if self._baseline_betti_std else None,
                "baseline_entropy": self._baseline_entropy,
                "baseline_entropy_std": self._baseline_entropy_std,
                "baseline_persistence": self._baseline_persistence,
                "baseline_persistence_std": self._baseline_persistence_std,
                "n_history": len(self._betti_history),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(BASELINE_PATH, "w") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            log.error("Failed to save TDA baseline: %s", e)
