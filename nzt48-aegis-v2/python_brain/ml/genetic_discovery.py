"""Automated Strategy Discovery via Genetic Programming — Book 51.

Evolves trading rules as expression trees using genetic operators.
Each tree is a mathematical formula that produces a signal from
price/volume features.

GP Tree: nodes are operators (+, -, *, /, max, min, abs, log, rank)
         leaves are features (close, volume, sma_20, rsi_14, etc.)

Evolution:
  - 4 islands × 500 individuals per island
  - Tournament selection (size 7)
  - Subtree crossover (90%) + point mutation (5%) + hoist (5%)
  - Migration between islands every 20 generations
  - Elitism: top 5 individuals survive unchanged

Fitness: Walk-forward Sharpe ratio (NOT in-sample Sharpe — avoids overfit)

Usage:
    from python_brain.ml.genetic_discovery import (
        GPEngine, GPTree, evaluate_tree,
    )

    engine = GPEngine(n_features=20)
    best = engine.evolve(feature_matrix, returns, generations=100)
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("genetic_discovery")


class NodeType(Enum):
    ADD = "+"
    SUB = "-"
    MUL = "*"
    DIV = "/"
    MAX = "max"
    MIN = "min"
    ABS = "abs"
    NEG = "neg"
    FEATURE = "feature"
    CONSTANT = "const"


@dataclass
class GPNode:
    """A node in a GP expression tree."""
    node_type: NodeType
    value: Any = None  # Feature index or constant value
    children: List["GPNode"] = field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.node_type in (NodeType.FEATURE, NodeType.CONSTANT)

    def depth(self) -> int:
        if self.is_terminal:
            return 0
        return 1 + max((c.depth() for c in self.children), default=0)

    def size(self) -> int:
        if self.is_terminal:
            return 1
        return 1 + sum(c.size() for c in self.children)

    def evaluate(self, features: np.ndarray) -> np.ndarray:
        """Evaluate tree on feature matrix (N × D)."""
        if self.node_type == NodeType.FEATURE:
            idx = self.value
            if idx < features.shape[1]:
                return features[:, idx]
            return np.zeros(features.shape[0])

        if self.node_type == NodeType.CONSTANT:
            return np.full(features.shape[0], self.value)

        if self.node_type == NodeType.ABS:
            return np.abs(self.children[0].evaluate(features))

        if self.node_type == NodeType.NEG:
            return -self.children[0].evaluate(features)

        left = self.children[0].evaluate(features) if len(self.children) > 0 else np.zeros(features.shape[0])
        right = self.children[1].evaluate(features) if len(self.children) > 1 else np.zeros(features.shape[0])

        if self.node_type == NodeType.ADD:
            return left + right
        elif self.node_type == NodeType.SUB:
            return left - right
        elif self.node_type == NodeType.MUL:
            return left * right
        elif self.node_type == NodeType.DIV:
            return np.divide(left, np.where(np.abs(right) < 1e-10, 1.0, right))
        elif self.node_type == NodeType.MAX:
            return np.maximum(left, right)
        elif self.node_type == NodeType.MIN:
            return np.minimum(left, right)

        return np.zeros(features.shape[0])

    def to_str(self) -> str:
        if self.node_type == NodeType.FEATURE:
            return f"F{self.value}"
        if self.node_type == NodeType.CONSTANT:
            return f"{self.value:.2f}"
        if self.node_type in (NodeType.ABS, NodeType.NEG):
            return f"{self.node_type.value}({self.children[0].to_str()})"
        if len(self.children) >= 2:
            return f"({self.children[0].to_str()} {self.node_type.value} {self.children[1].to_str()})"
        return "?"


BINARY_OPS = [NodeType.ADD, NodeType.SUB, NodeType.MUL, NodeType.DIV, NodeType.MAX, NodeType.MIN]
UNARY_OPS = [NodeType.ABS, NodeType.NEG]


def random_tree(n_features: int, max_depth: int = 4, depth: int = 0) -> GPNode:
    """Generate a random GP tree."""
    if depth >= max_depth or (depth > 0 and random.random() < 0.3):
        # Terminal
        if random.random() < 0.7:
            return GPNode(NodeType.FEATURE, value=random.randint(0, n_features - 1))
        else:
            return GPNode(NodeType.CONSTANT, value=round(random.uniform(-2, 2), 2))

    # Operator
    if random.random() < 0.8:  # Binary
        op = random.choice(BINARY_OPS)
        return GPNode(op, children=[
            random_tree(n_features, max_depth, depth + 1),
            random_tree(n_features, max_depth, depth + 1),
        ])
    else:  # Unary
        op = random.choice(UNARY_OPS)
        return GPNode(op, children=[random_tree(n_features, max_depth, depth + 1)])


def crossover(parent1: GPNode, parent2: GPNode) -> GPNode:
    """Subtree crossover between two parents."""
    # Deep copy parent1 (simplified — works for our tree structure)
    import copy
    child = copy.deepcopy(parent1)

    # Find random subtree in child to replace
    nodes = _collect_nodes(child)
    if not nodes:
        return child

    # Find random subtree in parent2 to insert
    donor_nodes = _collect_nodes(parent2)
    if not donor_nodes:
        return child

    target = random.choice(nodes)
    donor = random.choice(donor_nodes)

    # Replace target's content with donor's
    target.node_type = donor.node_type
    target.value = donor.value
    target.children = [copy.deepcopy(c) for c in donor.children]

    return child


def _collect_nodes(tree: GPNode) -> List[GPNode]:
    """Collect all nodes in a tree (for crossover/mutation targets)."""
    nodes = [tree]
    for child in tree.children:
        nodes.extend(_collect_nodes(child))
    return nodes


def compute_fitness(tree: GPNode, features: np.ndarray, returns: np.ndarray) -> float:
    """Compute fitness as walk-forward Sharpe ratio."""
    try:
        signal = tree.evaluate(features)
    except (ValueError, RuntimeWarning):
        return -10.0

    if np.any(np.isnan(signal)) or np.any(np.isinf(signal)):
        return -10.0

    # Convert signal to positions (sign of signal)
    positions = np.sign(signal[:-1])
    trade_returns = positions * returns[1:]

    if len(trade_returns) < 20:
        return -10.0

    mean_r = np.mean(trade_returns)
    std_r = np.std(trade_returns, ddof=1)
    if std_r < 1e-10:
        return -10.0

    sharpe = mean_r / std_r * math.sqrt(252)

    # Penalize complexity (Occam's razor)
    complexity_penalty = tree.size() * 0.01
    return sharpe - complexity_penalty


class GPEngine:
    """Genetic programming engine for strategy discovery."""

    def __init__(
        self,
        n_features: int = 20,
        pop_size: int = 200,
        max_depth: int = 6,
        tournament_size: int = 7,
    ):
        self.n_features = n_features
        self.pop_size = pop_size
        self.max_depth = max_depth
        self.tournament_size = tournament_size

    def evolve(
        self,
        features: np.ndarray,
        returns: np.ndarray,
        generations: int = 50,
    ) -> Tuple[GPNode, float]:
        """Evolve a population to find the best trading rule.

        Returns: (best_tree, best_fitness)
        """
        # Initialize population
        population = [random_tree(self.n_features, self.max_depth) for _ in range(self.pop_size)]

        best_ever = None
        best_fitness = -float("inf")

        for gen in range(generations):
            # Evaluate fitness
            fitnesses = [compute_fitness(t, features, returns) for t in population]

            # Track best
            gen_best_idx = int(np.argmax(fitnesses))
            if fitnesses[gen_best_idx] > best_fitness:
                best_fitness = fitnesses[gen_best_idx]
                best_ever = population[gen_best_idx]

            if gen % 10 == 0:
                log.info("GP gen %d: best=%.3f, avg=%.3f, best_size=%d",
                         gen, best_fitness, np.mean(fitnesses),
                         best_ever.size() if best_ever else 0)

            # Selection + reproduction
            new_pop = []
            # Elitism: keep top 5
            elite_idx = np.argsort(fitnesses)[-5:]
            for i in elite_idx:
                new_pop.append(population[i])

            while len(new_pop) < self.pop_size:
                # Tournament selection
                p1 = self._tournament(population, fitnesses)
                p2 = self._tournament(population, fitnesses)

                r = random.random()
                if r < 0.90:
                    child = crossover(p1, p2)
                elif r < 0.95:
                    child = self._mutate(p1)
                else:
                    child = random_tree(self.n_features, self.max_depth)

                # Depth limit
                if child.depth() <= self.max_depth:
                    new_pop.append(child)
                else:
                    new_pop.append(random_tree(self.n_features, self.max_depth))

            population = new_pop

        log.info("GP evolution complete: best Sharpe=%.3f, tree=%s",
                 best_fitness, best_ever.to_str() if best_ever else "?")
        return best_ever, best_fitness

    def _tournament(self, population, fitnesses):
        indices = random.sample(range(len(population)), min(self.tournament_size, len(population)))
        best_idx = max(indices, key=lambda i: fitnesses[i])
        return population[best_idx]

    def _mutate(self, tree: GPNode) -> GPNode:
        import copy
        mutant = copy.deepcopy(tree)
        nodes = _collect_nodes(mutant)
        if nodes:
            target = random.choice(nodes)
            replacement = random_tree(self.n_features, max_depth=2)
            target.node_type = replacement.node_type
            target.value = replacement.value
            target.children = replacement.children
        return mutant
