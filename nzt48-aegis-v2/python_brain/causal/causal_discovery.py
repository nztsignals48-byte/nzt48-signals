"""DAG Learning via PC Algorithm for Causal Structure Discovery — Book 134.

Implements the PC algorithm (Peter-Clark, Spirtes et al. 2000) for
learning causal DAG structures from observational data. Uses partial
correlation tests for conditional independence.

Purpose in AEGIS V2: Distinguish genuine causal alpha sources from
spurious correlations. Signals driven by confounders (e.g. both
correlated with VIX) are filtered out before they reach the arbiter.

Components:
  - ConditionalIndependenceTest: Partial correlation CI test
  - PCAlgorithm: Full PC algorithm (skeleton + orientation)
  - CausalAlphaFilter: Filter signals using learned causal structure

Bridge.py integration:
    try:
        from python_brain.causal.causal_discovery import (
            PCAlgorithm, CausalAlphaFilter,
        )
    except ImportError:
        pass

    # In nightly pipeline:
    pc = PCAlgorithm(alpha=0.05, max_cond_set=3)
    dag = pc.fit(feature_matrix, var_names=feature_names)
    causal_filter = CausalAlphaFilter(dag)
    clean_signals = causal_filter.filter_spurious(candidate_signals)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger("causal_discovery")

__all__ = [
    "ConditionalIndependenceTest",
    "PCAlgorithm",
    "CausalAlphaFilter",
]


# ---------------------------------------------------------------------------
# Conditional Independence Test
# ---------------------------------------------------------------------------

class ConditionalIndependenceTest:
    """Conditional independence test via partial correlation.

    Tests X _||_ Y | Z (X independent of Y given Z) using the
    partial correlation coefficient and Fisher's z-transform for
    the p-value.

    The partial correlation removes the linear effect of Z from
    both X and Y, then tests whether the residual correlation
    is significantly different from zero.
    """

    @staticmethod
    def test(
        X: int,
        Y: int,
        Z: List[int],
        data: np.ndarray,
        alpha: float = 0.05,
    ) -> Tuple[bool, float]:
        """Test conditional independence X _||_ Y | Z.

        Args:
            X: Column index of first variable.
            Y: Column index of second variable.
            Z: Column indices of conditioning set.
            data: Data matrix, shape (n_samples, n_variables).
            alpha: Significance level.

        Returns:
            Tuple of (is_independent, p_value).
            is_independent is True if p_value >= alpha.
        """
        n = data.shape[0]

        if len(Z) == 0:
            # Marginal correlation
            r = ConditionalIndependenceTest._marginal_correlation(X, Y, data)
        else:
            r = ConditionalIndependenceTest._partial_correlation(X, Y, Z, data)

        # Fisher z-transform for p-value
        # z = 0.5 * ln((1+r)/(1-r)) is approximately N(0, 1/sqrt(n-|Z|-3))
        abs_r = min(abs(r), 0.9999)  # Clamp to avoid log singularity

        dof = n - len(Z) - 3
        if dof < 1:
            # Insufficient degrees of freedom
            return True, 1.0

        z_stat = math.sqrt(dof) * 0.5 * math.log((1.0 + abs_r) / (1.0 - abs_r))

        # Two-sided p-value from standard normal
        p_value = ConditionalIndependenceTest._normal_sf(abs(z_stat)) * 2.0

        is_independent = p_value >= alpha

        return is_independent, float(p_value)

    @staticmethod
    def _partial_correlation(
        X: int,
        Y: int,
        Z: List[int],
        data: np.ndarray,
    ) -> float:
        """Compute partial correlation of X and Y given Z.

        Uses the recursive formula or the precision matrix approach:
        rho_{XY|Z} = -P_{XY} / sqrt(P_{XX} * P_{YY})
        where P is the precision matrix (inverse covariance) of {X, Y, Z}.

        Args:
            X: Column index of X.
            Y: Column index of Y.
            Z: Column indices of conditioning variables.
            data: Data matrix, shape (n, p).

        Returns:
            Partial correlation coefficient in [-1, 1].
        """
        indices = [X, Y] + list(Z)
        sub_data = data[:, indices]

        # Remove NaN rows
        mask = np.all(np.isfinite(sub_data), axis=1)
        sub_data = sub_data[mask]

        if sub_data.shape[0] < len(indices) + 2:
            return 0.0

        # Covariance matrix of the subset
        cov = np.cov(sub_data, rowvar=False)

        if cov.ndim == 0:
            return 0.0

        # Precision matrix (inverse covariance)
        try:
            precision = np.linalg.inv(cov)
        except np.linalg.LinAlgError:
            # Singular covariance — use pseudo-inverse
            precision = np.linalg.pinv(cov)

        # Partial correlation: rho_{XY|Z} = -P[0,1] / sqrt(P[0,0] * P[1,1])
        p_xx = precision[0, 0]
        p_yy = precision[1, 1]
        p_xy = precision[0, 1]

        denom = math.sqrt(abs(p_xx * p_yy))
        if denom < 1e-15:
            return 0.0

        partial_corr = -p_xy / denom
        return float(np.clip(partial_corr, -1.0, 1.0))

    @staticmethod
    def _marginal_correlation(X: int, Y: int, data: np.ndarray) -> float:
        """Compute marginal (unconditional) correlation between X and Y.

        Args:
            X: Column index.
            Y: Column index.
            data: Data matrix.

        Returns:
            Pearson correlation coefficient.
        """
        x = data[:, X]
        y = data[:, Y]

        mask = np.isfinite(x) & np.isfinite(y)
        x = x[mask]
        y = y[mask]

        if len(x) < 3:
            return 0.0

        corr_matrix = np.corrcoef(x, y)
        r = float(corr_matrix[0, 1])

        if not np.isfinite(r):
            return 0.0

        return r

    @staticmethod
    def _normal_sf(z: float) -> float:
        """Survival function (1 - CDF) of standard normal at z.

        Uses the complementary error function approximation.
        Avoids scipy dependency.

        Args:
            z: Z-score (non-negative for proper usage).

        Returns:
            P(Z >= z) for Z ~ N(0, 1).
        """
        # Abramowitz & Stegun approximation (7.1.26)
        # Accurate to ~1e-7
        t = 1.0 / (1.0 + 0.2316419 * abs(z))
        d = 0.3989422804014327  # 1 / sqrt(2 * pi)
        p = d * math.exp(-z * z / 2.0) * (
            t * (0.319381530
                 + t * (-0.356563782
                        + t * (1.781477937
                               + t * (-1.821255978
                                      + t * 1.330274429))))
        )

        if z >= 0:
            return p
        else:
            return 1.0 - p


# ---------------------------------------------------------------------------
# PC Algorithm
# ---------------------------------------------------------------------------

class PCAlgorithm:
    """PC algorithm for causal DAG learning from observational data.

    Two phases:
    1. Skeleton phase: Start with complete undirected graph, remove
       edges where conditional independence is found.
    2. Orientation phase: Orient edges using v-structures (colliders)
       and Meek's orientation rules.

    Assumptions:
      - Causal Markov condition
      - Faithfulness
      - No hidden confounders (can be relaxed with FCI variant)

    Attributes:
        alpha: Significance level for CI tests.
        max_cond_set: Maximum conditioning set size.
    """

    def __init__(
        self,
        alpha: float = 0.05,
        max_cond_set: int = 3,
    ) -> None:
        """Initialise PC algorithm.

        Args:
            alpha: Significance threshold for conditional independence.
                   Lower = more conservative (fewer edges removed).
            max_cond_set: Maximum size of conditioning set to search.
                          Limits computation but may miss some CI relations.
        """
        self.alpha = alpha
        self.max_cond_set = max_cond_set

        self._adjacency: Optional[np.ndarray] = None
        self._directed: Optional[np.ndarray] = None
        self._var_names: List[str] = []
        self._sep_sets: Dict[Tuple[int, int], FrozenSet[int]] = {}

    def fit(
        self,
        data: np.ndarray,
        var_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Learn causal DAG from data.

        Args:
            data: Data matrix, shape (n_samples, n_variables).
            var_names: Optional variable names. Defaults to V0, V1, ...

        Returns:
            Dict with keys:
              - adjacency: Directed adjacency matrix (n_vars, n_vars).
                           adjacency[i,j] = 1 means i -> j.
              - skeleton: Undirected adjacency matrix.
              - var_names: Variable names.
              - n_edges: Number of directed edges.
              - n_vars: Number of variables.
              - sep_sets: Separation sets for removed edges.
              - v_structures: List of (X, Y, Z) colliders X -> Y <- Z.
        """
        n_vars = data.shape[1]

        if var_names is None:
            var_names = [f"V{i}" for i in range(n_vars)]

        if len(var_names) != n_vars:
            raise ValueError(
                f"var_names length ({len(var_names)}) != data columns ({n_vars})"
            )

        self._var_names = var_names

        log.info("PC algorithm: %d variables, %d samples, alpha=%.3f",
                 n_vars, data.shape[0], self.alpha)

        # Phase 1: Learn skeleton
        skeleton, sep_sets = self._skeleton_phase(data)
        self._adjacency = skeleton.copy()
        self._sep_sets = sep_sets

        n_undirected_edges = int(np.sum(skeleton) // 2)
        log.info("Skeleton: %d undirected edges", n_undirected_edges)

        # Phase 2: Orient edges
        directed, v_structures = self._orient_edges(skeleton, sep_sets)
        self._directed = directed

        n_directed = int(np.sum(directed))
        log.info("Oriented DAG: %d directed edges, %d v-structures",
                 n_directed, len(v_structures))

        return {
            "adjacency": directed.tolist(),
            "skeleton": skeleton.tolist(),
            "var_names": var_names,
            "n_edges": n_directed,
            "n_vars": n_vars,
            "sep_sets": {
                f"{var_names[k[0]]}-{var_names[k[1]]}": list(v)
                for k, v in sep_sets.items()
            },
            "v_structures": [
                (var_names[x], var_names[y], var_names[z])
                for x, y, z in v_structures
            ],
        }

    def _skeleton_phase(
        self,
        data: np.ndarray,
    ) -> Tuple[np.ndarray, Dict[Tuple[int, int], FrozenSet[int]]]:
        """Phase 1: Learn the undirected skeleton.

        Start with complete graph. For each edge (X, Y), test if
        X _||_ Y | Z for conditioning sets Z of increasing size.
        Remove edge if CI is found.

        Args:
            data: Data matrix, shape (n, p).

        Returns:
            Tuple of (skeleton adjacency matrix, separation sets dict).
        """
        n_vars = data.shape[1]

        # Start with complete undirected graph
        skeleton = np.ones((n_vars, n_vars), dtype=int)
        np.fill_diagonal(skeleton, 0)

        sep_sets: Dict[Tuple[int, int], FrozenSet[int]] = {}
        ci_test = ConditionalIndependenceTest()

        for cond_size in range(self.max_cond_set + 1):
            edges_to_remove: List[Tuple[int, int, FrozenSet[int]]] = []

            for i in range(n_vars):
                for j in range(i + 1, n_vars):
                    if skeleton[i, j] == 0:
                        continue

                    # Neighbours of i (excluding j) as candidate conditioning sets
                    neighbors_i = [
                        k for k in range(n_vars)
                        if k != i and k != j and skeleton[i, k] == 1
                    ]

                    if len(neighbors_i) < cond_size:
                        continue

                    # Test all conditioning sets of size cond_size
                    found_independent = False
                    for Z in combinations(neighbors_i, cond_size):
                        Z_list = list(Z)
                        independent, p_val = ci_test.test(i, j, Z_list, data, self.alpha)

                        if independent:
                            edges_to_remove.append((i, j, frozenset(Z)))
                            found_independent = True
                            break

                    if found_independent:
                        continue

                    # Also check neighbours of j
                    neighbors_j = [
                        k for k in range(n_vars)
                        if k != i and k != j and skeleton[j, k] == 1
                    ]

                    if len(neighbors_j) < cond_size:
                        continue

                    for Z in combinations(neighbors_j, cond_size):
                        Z_list = list(Z)
                        independent, p_val = ci_test.test(i, j, Z_list, data, self.alpha)

                        if independent:
                            edges_to_remove.append((i, j, frozenset(Z)))
                            break

            # Remove edges found in this round
            for i, j, Z in edges_to_remove:
                skeleton[i, j] = 0
                skeleton[j, i] = 0
                sep_sets[(i, j)] = Z
                sep_sets[(j, i)] = Z

            if edges_to_remove:
                log.debug("Skeleton: removed %d edges at cond_size=%d",
                          len(edges_to_remove), cond_size)

        return skeleton, sep_sets

    def _orient_edges(
        self,
        skeleton: np.ndarray,
        sep_sets: Dict[Tuple[int, int], FrozenSet[int]],
    ) -> Tuple[np.ndarray, List[Tuple[int, int, int]]]:
        """Phase 2: Orient edges to form a DAG.

        Step 1: Identify v-structures (colliders).
          For non-adjacent X, Z with common neighbor Y:
          If Y not in Sep(X, Z), orient as X -> Y <- Z.

        Step 2: Apply Meek's orientation rules iteratively:
          R1: X -> Y — Z  =>  X -> Y -> Z  (if X and Z not adjacent)
          R2: X -> Z -> Y  and X — Y  =>  X -> Y
          R3: X — Y, X — Z, X — W, Z -> Y, W -> Y  =>  X -> Y

        Args:
            skeleton: Undirected adjacency matrix.
            sep_sets: Separation sets from skeleton phase.

        Returns:
            Tuple of (directed adjacency matrix, list of v-structures).
        """
        n_vars = skeleton.shape[0]
        directed = np.zeros((n_vars, n_vars), dtype=int)
        oriented = np.zeros((n_vars, n_vars), dtype=bool)
        v_structures: List[Tuple[int, int, int]] = []

        # Step 1: Find v-structures
        for y in range(n_vars):
            # Find pairs of non-adjacent parents of y
            parents = [i for i in range(n_vars) if skeleton[i, y] == 1 and i != y]

            for idx_a in range(len(parents)):
                for idx_b in range(idx_a + 1, len(parents)):
                    x = parents[idx_a]
                    z = parents[idx_b]

                    # X and Z must not be adjacent
                    if skeleton[x, z] == 1:
                        continue

                    # Y must not be in Sep(X, Z)
                    sep = sep_sets.get((x, z), sep_sets.get((z, x), frozenset()))
                    if y not in sep:
                        # Orient X -> Y <- Z
                        directed[x, y] = 1
                        directed[z, y] = 1
                        oriented[x, y] = True
                        oriented[z, y] = True
                        v_structures.append((x, y, z))
                        log.debug("V-structure: %s -> %s <- %s",
                                  self._var_names[x], self._var_names[y],
                                  self._var_names[z])

        # Step 2: Apply Meek's rules iteratively
        changed = True
        max_iterations = n_vars * n_vars
        iteration = 0

        while changed and iteration < max_iterations:
            changed = False
            iteration += 1

            for i in range(n_vars):
                for j in range(n_vars):
                    if i == j:
                        continue
                    if skeleton[i, j] == 0:
                        continue
                    if oriented[i, j] or oriented[j, i]:
                        continue

                    # Rule 1: X -> Y — Z, X and Z not adjacent => Y -> Z
                    for k in range(n_vars):
                        if k == i or k == j:
                            continue
                        if directed[k, i] == 1 and skeleton[k, j] == 0:
                            # k -> i — j, k and j not adjacent => i -> j
                            directed[i, j] = 1
                            oriented[i, j] = True
                            changed = True
                            break

                    if oriented[i, j]:
                        continue

                    # Rule 2: i -> k -> j and i — j => i -> j
                    for k in range(n_vars):
                        if k == i or k == j:
                            continue
                        if directed[i, k] == 1 and directed[k, j] == 1:
                            directed[i, j] = 1
                            oriented[i, j] = True
                            changed = True
                            break

        # Fill remaining undirected edges (arbitrary but acyclic)
        for i in range(n_vars):
            for j in range(i + 1, n_vars):
                if skeleton[i, j] == 1 and not oriented[i, j] and not oriented[j, i]:
                    # Orient arbitrarily: lower index -> higher index
                    directed[i, j] = 1

        return directed, v_structures

    def get_parents(self, variable: str) -> List[str]:
        """Get direct causal parents of a variable.

        Args:
            variable: Variable name.

        Returns:
            List of parent variable names.
        """
        if self._directed is None:
            return []

        if variable not in self._var_names:
            log.warning("Variable '%s' not in DAG", variable)
            return []

        j = self._var_names.index(variable)
        parents = []
        for i in range(self._directed.shape[0]):
            if self._directed[i, j] == 1:
                parents.append(self._var_names[i])

        return parents

    def get_children(self, variable: str) -> List[str]:
        """Get direct causal children of a variable.

        Args:
            variable: Variable name.

        Returns:
            List of child variable names.
        """
        if self._directed is None:
            return []

        if variable not in self._var_names:
            log.warning("Variable '%s' not in DAG", variable)
            return []

        i = self._var_names.index(variable)
        children = []
        for j in range(self._directed.shape[1]):
            if self._directed[i, j] == 1:
                children.append(self._var_names[j])

        return children

    def get_adjacency_matrix(self) -> Optional[np.ndarray]:
        """Return the directed adjacency matrix.

        Returns:
            np.ndarray of shape (n_vars, n_vars) or None if not fitted.
        """
        return self._directed


# ---------------------------------------------------------------------------
# Causal Alpha Filter
# ---------------------------------------------------------------------------

class CausalAlphaFilter:
    """Filter signals using learned causal structure.

    Uses the DAG from PCAlgorithm to distinguish genuine causal
    alpha from spurious correlations driven by confounders.

    A signal is "causal alpha" if there is a directed path from
    the predictor to the outcome that is not blocked by confounders.

    Signals driven by common causes (confounders) are removed.

    Attributes:
        dag: Causal DAG (as dict from PCAlgorithm.fit()).
    """

    def __init__(self, dag: Dict[str, Any]) -> None:
        """Initialise with a learned DAG.

        Args:
            dag: Output of PCAlgorithm.fit(). Must contain 'adjacency'
                 and 'var_names'.
        """
        self._adjacency = np.array(dag.get("adjacency", []))
        self._var_names = dag.get("var_names", [])
        self._n_vars = len(self._var_names)
        self._name_to_idx: Dict[str, int] = {
            name: i for i, name in enumerate(self._var_names)
        }

    def is_causal_alpha(self, predictor: str, outcome: str) -> bool:
        """Test if predictor has a causal path to outcome.

        Uses d-separation: predictor causally affects outcome if there
        is a directed path from predictor to outcome in the DAG.

        Args:
            predictor: Predictor variable name.
            outcome: Outcome variable name.

        Returns:
            True if there is a directed causal path predictor -> ... -> outcome.
        """
        if predictor not in self._name_to_idx or outcome not in self._name_to_idx:
            log.debug("Variable not in DAG: predictor=%s, outcome=%s", predictor, outcome)
            return False

        start = self._name_to_idx[predictor]
        end = self._name_to_idx[outcome]

        # BFS for directed path
        visited: Set[int] = set()
        queue = [start]

        while queue:
            current = queue.pop(0)
            if current == end:
                return True
            if current in visited:
                continue
            visited.add(current)

            # Follow directed edges
            for j in range(self._n_vars):
                if self._adjacency[current, j] == 1 and j not in visited:
                    queue.append(j)

        return False

    def confounded_pairs(self) -> List[Tuple[str, str, List[str]]]:
        """Find pairs of variables that share common causes.

        Two variables X, Y are confounded if there exists a variable Z
        such that Z -> X and Z -> Y (Z is a common parent).

        Returns:
            List of (X, Y, [confounders]) tuples.
        """
        pairs: List[Tuple[str, str, List[str]]] = []

        for z in range(self._n_vars):
            # Find all children of z
            children = [
                j for j in range(self._n_vars)
                if self._adjacency[z, j] == 1
            ]

            # All pairs of children share z as a confounder
            for a_idx in range(len(children)):
                for b_idx in range(a_idx + 1, len(children)):
                    x = children[a_idx]
                    y = children[b_idx]
                    pairs.append((
                        self._var_names[x],
                        self._var_names[y],
                        [self._var_names[z]],
                    ))

        # Merge confounders for the same pair
        merged: Dict[Tuple[str, str], List[str]] = {}
        for x, y, confounders in pairs:
            key = (min(x, y), max(x, y))
            if key not in merged:
                merged[key] = []
            for c in confounders:
                if c not in merged[key]:
                    merged[key].append(c)

        return [(k[0], k[1], v) for k, v in merged.items()]

    def filter_spurious(
        self,
        signals: List[Dict[str, Any]],
        predictor_key: str = "predictor",
        outcome_key: str = "outcome",
    ) -> List[Dict[str, Any]]:
        """Remove signals driven by confounders rather than causal paths.

        For each signal, checks if the predictor has a causal path
        to the outcome. Signals without a causal path are filtered out.

        Args:
            signals: List of signal dicts. Each must have predictor_key
                     and outcome_key fields.
            predictor_key: Key for the predictor variable name.
            outcome_key: Key for the outcome variable name.

        Returns:
            Filtered list of signals (only those with causal paths).
        """
        if self._n_vars == 0:
            log.warning("CausalAlphaFilter: empty DAG — returning all signals")
            return signals

        filtered = []
        n_removed = 0

        for signal in signals:
            predictor = signal.get(predictor_key, "")
            outcome = signal.get(outcome_key, "")

            if not predictor or not outcome:
                # No causal info — keep by default
                filtered.append(signal)
                continue

            if predictor not in self._name_to_idx or outcome not in self._name_to_idx:
                # Unknown variables — keep by default
                filtered.append(signal)
                continue

            if self.is_causal_alpha(predictor, outcome):
                filtered.append(signal)
            else:
                n_removed += 1
                log.debug("Filtered spurious signal: %s -> %s (no causal path)",
                          predictor, outcome)

        if n_removed > 0:
            log.info("CausalAlphaFilter: removed %d/%d spurious signals",
                     n_removed, len(signals))

        return filtered

    def causal_strength(self, predictor: str, outcome: str) -> float:
        """Estimate causal strength via path counting.

        Counts the number of directed paths and their lengths.
        More paths and shorter paths = stronger causal relationship.

        Args:
            predictor: Predictor variable name.
            outcome: Outcome variable name.

        Returns:
            Causal strength score (0.0 = no path, higher = stronger).
        """
        if predictor not in self._name_to_idx or outcome not in self._name_to_idx:
            return 0.0

        start = self._name_to_idx[predictor]
        end = self._name_to_idx[outcome]

        # BFS to find all paths and their lengths
        paths: List[int] = []  # List of path lengths
        queue: List[Tuple[int, int]] = [(start, 0)]  # (node, depth)
        visited_at_depth: Dict[int, int] = {}
        max_depth = self._n_vars

        while queue:
            current, depth = queue.pop(0)

            if depth > max_depth:
                continue

            if current == end and depth > 0:
                paths.append(depth)
                continue

            if current in visited_at_depth and visited_at_depth[current] <= depth:
                continue
            visited_at_depth[current] = depth

            for j in range(self._n_vars):
                if self._adjacency[current, j] == 1:
                    queue.append((j, depth + 1))

        if not paths:
            return 0.0

        # Score: sum of 1/length for each path (shorter paths contribute more)
        score = sum(1.0 / length for length in paths)
        return round(score, 4)
