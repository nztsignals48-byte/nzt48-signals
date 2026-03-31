"""NSGA-III Multi-Objective Pareto Optimizer — Book 79.

Implements the NSGA-III algorithm (Deb & Jain 2014) for optimising
6 competing objectives simultaneously:
  1. Sharpe ratio (maximise)
  2. Max drawdown (minimise)
  3. Sortino ratio (maximise)
  4. Calmar ratio (maximise)
  5. Trade count (target [50, 200])
  6. Portfolio correlation (minimise)

Uses Das-Dennis reference points for diversity preservation and
reference-point-based niching for selection.

ISA constraint: any individual with max_drawdown > 8% is infeasible.

Bridge.py integration:
    from python_brain.ml.nsga3_optimizer import (
        NSGA3Optimizer, AEGISObjectives, ParetoAnalyzer,
        OptimizationConfig,
    )
    objectives = AEGISObjectives(returns_data, drawdown_data)
    optimizer = NSGA3Optimizer(
        parameter_bounds=bounds,
        objective_functions=[objectives.sharpe, objectives.max_drawdown, ...],
        objective_directions=["max", "min", "max", "max", "min", "min"],
    )
    result = optimizer.optimize()
    knee = ParetoAnalyzer.find_knee_point(result["pareto_front"])

Usage:
    from python_brain.ml.nsga3_optimizer import (
        NSGA3Optimizer, AEGISObjectives, ParetoAnalyzer,
        OptimizationConfig, Individual,
    )
"""

from __future__ import annotations

import json
import logging
import math
import copy
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("nsga3_optimizer")

__all__ = [
    "Individual",
    "OptimizationConfig",
    "NSGA3Optimizer",
    "AEGISObjectives",
    "ParetoAnalyzer",
]

# ── Constants ──────────────────────────────────────────────────────────

ISA_MAX_DRAWDOWN = 0.08   # 8% max drawdown for ISA safety
TRADE_COUNT_MIN = 50
TRADE_COUNT_MAX = 200


# ── Dataclasses ────────────────────────────────────────────────────────

@dataclass
class Individual:
    """A single solution in the population."""
    parameters: Dict[str, float] = field(default_factory=dict)
    objectives: Dict[str, float] = field(default_factory=dict)
    rank: int = 0
    crowding_distance: float = 0.0
    reference_point_idx: int = -1
    feasible: bool = True

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class OptimizationConfig:
    """NSGA-III configuration."""
    population_size: int = 100
    n_generations: int = 50
    crossover_prob: float = 0.9
    mutation_prob: float = 0.1
    mutation_scale: float = 0.05
    tournament_size: int = 3
    sbx_eta: float = 20.0        # SBX distribution index
    pm_eta: float = 20.0         # Polynomial mutation distribution index

    def to_dict(self) -> Dict:
        return asdict(self)


# ── NSGA-III Optimizer ─────────────────────────────────────────────────

class NSGA3Optimizer:
    """NSGA-III multi-objective optimizer with reference-point niching.

    Maintains a population of solutions, evolves them via tournament
    selection, SBX crossover, and polynomial mutation, then selects
    the next generation using non-dominated sorting and reference-point
    based niching (Das-Dennis).
    """

    def __init__(
        self,
        parameter_bounds: Dict[str, Tuple[float, float]],
        objective_functions: List[Callable[[Dict[str, float]], float]],
        objective_directions: List[str],
        constraints: Optional[List[Callable[[Dict[str, float]], bool]]] = None,
        config: Optional[OptimizationConfig] = None,
    ):
        """
        Args:
            parameter_bounds: param_name -> (lower, upper).
            objective_functions: Callables that take params dict -> float.
            objective_directions: "max" or "min" per objective.
            constraints: Optional callables that return True if feasible.
            config: Optimization configuration.
        """
        self._bounds = parameter_bounds
        self._obj_fns = objective_functions
        self._obj_dirs = objective_directions
        self._constraints = constraints or []
        self._config = config or OptimizationConfig()

        self._n_obj = len(objective_functions)
        self._param_names = list(parameter_bounds.keys())
        self._ref_points = self._generate_reference_points(self._n_obj)

        self._history: List[Dict] = []

    def optimize(self) -> Dict[str, Any]:
        """Run full NSGA-III optimization.

        Returns:
            Dict with pareto_front (list of Individual dicts),
            history (per-generation stats), config, and metadata.
        """
        log.info("Starting NSGA-III: %d params, %d objectives, %d generations",
                 len(self._param_names), self._n_obj, self._config.n_generations)

        population = self._initialize_population()
        population = self._evaluate_population(population)

        for gen in range(self._config.n_generations):
            # Create offspring via selection + crossover + mutation
            offspring = self._create_offspring(population)
            offspring = self._evaluate_population(offspring)

            # Combine parent + offspring
            combined = population + offspring

            # Non-dominated sort
            fronts = self._non_dominated_sort(combined)

            # Select next generation
            population = self._select_next_generation(fronts)

            # Track history
            front_0 = [ind for ind in population if ind.rank == 0]
            feasible_front = [ind for ind in front_0 if ind.feasible]
            gen_stats = {
                "generation": gen,
                "pop_size": len(population),
                "front_0_size": len(front_0),
                "feasible_front_0": len(feasible_front),
                "n_fronts": len(fronts),
            }
            self._history.append(gen_stats)

            if gen % 10 == 0:
                log.info("NSGA-III gen %d: front_0=%d, feasible=%d",
                         gen, len(front_0), len(feasible_front))

        # Extract final Pareto front
        fronts = self._non_dominated_sort(population)
        pareto_front = fronts[0] if fronts else []

        log.info("NSGA-III complete: %d solutions on Pareto front", len(pareto_front))

        return {
            "pareto_front": [ind.to_dict() for ind in pareto_front],
            "history": self._history,
            "config": self._config.to_dict(),
            "n_reference_points": len(self._ref_points),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ── Reference Points ───────────────────────────────────────────────

    def _generate_reference_points(self, n_objectives: int,
                                   n_divisions: int = 12) -> np.ndarray:
        """Generate Das-Dennis reference points on the unit simplex.

        Uses a recursive approach to create uniformly distributed
        points on the (n_objectives - 1)-simplex.

        Args:
            n_objectives: Number of objectives.
            n_divisions: Number of divisions per axis.

        Returns:
            Array of shape (n_points, n_objectives).
        """
        points = []
        self._das_dennis_recurse(n_objectives, n_divisions,
                                 [0.0] * n_objectives, 0, 0, n_divisions, points)

        if not points:
            # Fallback: at least the vertices
            eye = np.eye(n_objectives)
            return eye

        return np.array(points)

    def _das_dennis_recurse(self, n_obj: int, n_div: int,
                            point: List[float], dim: int,
                            used: int, remaining: int,
                            result: List[List[float]]) -> None:
        """Recursive helper for Das-Dennis reference point generation."""
        if dim == n_obj - 1:
            point[dim] = remaining / n_div
            result.append(list(point))
            return

        for i in range(remaining + 1):
            point[dim] = i / n_div
            self._das_dennis_recurse(n_obj, n_div, point, dim + 1,
                                     used + i, remaining - i, result)

    # ── Population Management ──────────────────────────────────────────

    def _initialize_population(self) -> List[Individual]:
        """Create initial random population within bounds."""
        population = []
        for _ in range(self._config.population_size):
            params = {}
            for name, (lo, hi) in self._bounds.items():
                params[name] = np.random.uniform(lo, hi)
            population.append(Individual(parameters=params))
        return population

    def _evaluate_population(self, pop: List[Individual]) -> List[Individual]:
        """Evaluate all objectives and constraints for each individual."""
        for ind in pop:
            # Evaluate objectives
            for i, fn in enumerate(self._obj_fns):
                try:
                    val = fn(ind.parameters)
                    if math.isnan(val) or math.isinf(val):
                        val = 1e6 if self._obj_dirs[i] == "min" else -1e6
                except Exception:
                    val = 1e6 if self._obj_dirs[i] == "min" else -1e6
                ind.objectives[f"obj_{i}"] = val

            # Check constraints
            ind.feasible = True
            for constraint_fn in self._constraints:
                try:
                    if not constraint_fn(ind.parameters):
                        ind.feasible = False
                        break
                except Exception:
                    ind.feasible = False

            # ISA hard constraint on drawdown
            dd_val = ind.objectives.get("obj_1", 0.0)  # max_drawdown is obj_1
            if abs(dd_val) > ISA_MAX_DRAWDOWN:
                ind.feasible = False

        return pop

    # ── Non-Dominated Sorting ──────────────────────────────────────────

    def _non_dominated_sort(self, combined: List[Individual]) -> List[List[Individual]]:
        """Fast non-dominated sorting (O(MN^2)).

        Feasible individuals always dominate infeasible ones.

        Args:
            combined: All individuals to sort.

        Returns:
            List of fronts, where fronts[0] is the Pareto front.
        """
        n = len(combined)
        if n == 0:
            return []

        domination_count = [0] * n
        dominated_set: List[List[int]] = [[] for _ in range(n)]
        fronts: List[List[int]] = [[]]

        for i in range(n):
            for j in range(i + 1, n):
                if self._dominates(combined[i], combined[j]):
                    dominated_set[i].append(j)
                    domination_count[j] += 1
                elif self._dominates(combined[j], combined[i]):
                    dominated_set[j].append(i)
                    domination_count[i] += 1

            if domination_count[i] == 0:
                combined[i].rank = 0
                fronts[0].append(i)

        current_front = 0
        while fronts[current_front]:
            next_front = []
            for i in fronts[current_front]:
                for j in dominated_set[i]:
                    domination_count[j] -= 1
                    if domination_count[j] == 0:
                        combined[j].rank = current_front + 1
                        next_front.append(j)
            current_front += 1
            fronts.append(next_front)

        # Convert index fronts to Individual fronts
        result = []
        for front_indices in fronts:
            if not front_indices:
                break
            result.append([combined[i] for i in front_indices])

        return result

    def _dominates(self, a: Individual, b: Individual) -> bool:
        """Check Pareto dominance: a dominates b.

        Feasible always dominates infeasible.

        Args:
            a, b: Individuals to compare.

        Returns:
            True if a dominates b.
        """
        # Feasibility takes priority
        if a.feasible and not b.feasible:
            return True
        if not a.feasible and b.feasible:
            return False
        if not a.feasible and not b.feasible:
            # Both infeasible: compare constraint violation
            return False

        # Both feasible: Pareto dominance check
        at_least_one_better = False
        for i in range(self._n_obj):
            key = f"obj_{i}"
            a_val = a.objectives.get(key, 0.0)
            b_val = b.objectives.get(key, 0.0)

            if self._obj_dirs[i] == "max":
                if a_val < b_val:
                    return False
                if a_val > b_val:
                    at_least_one_better = True
            else:  # min
                if a_val > b_val:
                    return False
                if a_val < b_val:
                    at_least_one_better = True

        return at_least_one_better

    # ── Selection with Reference-Point Niching ─────────────────────────

    def _select_next_generation(self, fronts: List[List[Individual]]) -> List[Individual]:
        """Select next generation using reference-point based niching.

        Fills from front 0, then front 1, etc. When the last front
        doesn't fit entirely, use reference-point niching to pick
        the most diverse subset.
        """
        target = self._config.population_size
        selected: List[Individual] = []

        for front in fronts:
            if len(selected) + len(front) <= target:
                selected.extend(front)
            else:
                # Need to select a subset from this front
                remaining = target - len(selected)
                if remaining <= 0:
                    break

                # Normalize and associate with reference points
                all_so_far = selected + front
                normalized = self._normalize_objectives(all_so_far)

                # Associate only the current front individuals
                front_start = len(selected)
                front_normalized = normalized[front_start:]
                associations = self._associate_reference_points(
                    front_normalized, self._ref_points
                )

                # Apply associations to front individuals
                for idx, ind in enumerate(front):
                    ind.reference_point_idx = associations[idx]

                # Niche-based selection
                niche_counts = self._niche_count(selected, self._ref_points)
                chosen = self._niching_selection(front, niche_counts, remaining)
                selected.extend(chosen)
                break

        return selected

    def _normalize_objectives(self, pop: List[Individual]) -> List[np.ndarray]:
        """Normalize objectives to [0, 1] range per objective.

        Args:
            pop: Population to normalize.

        Returns:
            List of normalised objective vectors.
        """
        if not pop:
            return []

        n_obj = self._n_obj
        obj_matrix = np.zeros((len(pop), n_obj))

        for i, ind in enumerate(pop):
            for j in range(n_obj):
                val = ind.objectives.get(f"obj_{j}", 0.0)
                # Flip maximisation objectives so all are "minimise"
                if self._obj_dirs[j] == "max":
                    val = -val
                obj_matrix[i, j] = val

        # Normalise to [0, 1]
        mins = obj_matrix.min(axis=0)
        maxs = obj_matrix.max(axis=0)
        ranges = maxs - mins
        ranges[ranges < 1e-10] = 1.0

        normalized = (obj_matrix - mins) / ranges
        return [normalized[i] for i in range(len(pop))]

    def _associate_reference_points(self, normalized_pop: List[np.ndarray],
                                    ref_points: np.ndarray) -> List[int]:
        """Associate each individual with its nearest reference point.

        Args:
            normalized_pop: Normalised objective vectors.
            ref_points: Reference point array.

        Returns:
            List of reference point indices (one per individual).
        """
        associations = []
        for obj_vec in normalized_pop:
            # Perpendicular distance to each reference line
            min_dist = float("inf")
            min_idx = 0
            for j, ref in enumerate(ref_points):
                ref_norm = np.linalg.norm(ref)
                if ref_norm < 1e-10:
                    dist = np.linalg.norm(obj_vec)
                else:
                    # Project obj_vec onto reference line
                    proj = np.dot(obj_vec, ref) / (ref_norm ** 2) * ref
                    dist = float(np.linalg.norm(obj_vec - proj))

                if dist < min_dist:
                    min_dist = dist
                    min_idx = j

            associations.append(min_idx)
        return associations

    def _niche_count(self, pop: List[Individual],
                     ref_points: np.ndarray) -> Dict[int, int]:
        """Count individuals per reference point.

        Args:
            pop: Current population.
            ref_points: Reference point array.

        Returns:
            Dict of reference_point_idx -> count.
        """
        counts: Dict[int, int] = {}
        for ind in pop:
            idx = ind.reference_point_idx
            if idx >= 0:
                counts[idx] = counts.get(idx, 0) + 1
        return counts

    def _niching_selection(self, front: List[Individual],
                           niche_counts: Dict[int, int],
                           n_select: int) -> List[Individual]:
        """Select individuals from a front using reference-point niching.

        Prefer individuals associated with under-represented reference points.
        """
        selected = []
        remaining = list(front)
        counts = dict(niche_counts)

        while len(selected) < n_select and remaining:
            # Find the reference point with minimum niche count
            ref_counts_in_remaining: Dict[int, List[Individual]] = {}
            for ind in remaining:
                idx = ind.reference_point_idx
                if idx not in ref_counts_in_remaining:
                    ref_counts_in_remaining[idx] = []
                ref_counts_in_remaining[idx].append(ind)

            if not ref_counts_in_remaining:
                break

            # Pick reference point with smallest niche count
            min_count = float("inf")
            min_ref = -1
            for ref_idx in ref_counts_in_remaining:
                c = counts.get(ref_idx, 0)
                if c < min_count:
                    min_count = c
                    min_ref = ref_idx

            if min_ref < 0:
                break

            # Pick one individual from this reference point
            candidates = ref_counts_in_remaining[min_ref]
            chosen = candidates[0]  # Could randomise, but deterministic is fine
            selected.append(chosen)
            remaining.remove(chosen)
            counts[min_ref] = counts.get(min_ref, 0) + 1

        return selected

    # ── Genetic Operators ──────────────────────────────────────────────

    def _create_offspring(self, population: List[Individual]) -> List[Individual]:
        """Create offspring population via tournament, crossover, mutation."""
        offspring = []
        n = self._config.population_size

        while len(offspring) < n:
            # Tournament selection
            p1 = self._tournament_select(population)
            p2 = self._tournament_select(population)

            # SBX crossover
            if np.random.random() < self._config.crossover_prob:
                c1, c2 = self._sbx_crossover(p1, p2)
            else:
                c1 = Individual(parameters=dict(p1.parameters))
                c2 = Individual(parameters=dict(p2.parameters))

            # Polynomial mutation
            if np.random.random() < self._config.mutation_prob:
                c1 = self._polynomial_mutation(c1)
            if np.random.random() < self._config.mutation_prob:
                c2 = self._polynomial_mutation(c2)

            offspring.append(c1)
            if len(offspring) < n:
                offspring.append(c2)

        return offspring[:n]

    def _tournament_select(self, population: List[Individual]) -> Individual:
        """Binary tournament selection based on rank then crowding distance."""
        indices = np.random.choice(len(population),
                                   size=min(self._config.tournament_size, len(population)),
                                   replace=False)
        candidates = [population[i] for i in indices]

        # Sort by rank (ascending), then crowding distance (descending)
        candidates.sort(key=lambda x: (x.rank, -x.crowding_distance))
        return candidates[0]

    def _sbx_crossover(self, p1: Individual,
                       p2: Individual) -> Tuple[Individual, Individual]:
        """Simulated Binary Crossover (SBX).

        Args:
            p1, p2: Parent individuals.

        Returns:
            Two offspring individuals.
        """
        eta = self._config.sbx_eta
        c1_params = {}
        c2_params = {}

        for name in self._param_names:
            lo, hi = self._bounds[name]
            x1 = p1.parameters.get(name, (lo + hi) / 2)
            x2 = p2.parameters.get(name, (lo + hi) / 2)

            if abs(x1 - x2) < 1e-14:
                c1_params[name] = x1
                c2_params[name] = x2
                continue

            u = np.random.random()
            if u <= 0.5:
                beta = (2.0 * u) ** (1.0 / (eta + 1.0))
            else:
                beta = (1.0 / (2.0 * (1.0 - u))) ** (1.0 / (eta + 1.0))

            child1 = 0.5 * ((1 + beta) * x1 + (1 - beta) * x2)
            child2 = 0.5 * ((1 - beta) * x1 + (1 + beta) * x2)

            c1_params[name] = float(np.clip(child1, lo, hi))
            c2_params[name] = float(np.clip(child2, lo, hi))

        return (Individual(parameters=c1_params),
                Individual(parameters=c2_params))

    def _polynomial_mutation(self, individual: Individual) -> Individual:
        """Polynomial mutation operator.

        Args:
            individual: Individual to mutate.

        Returns:
            Mutated individual (new object).
        """
        eta = self._config.pm_eta
        params = dict(individual.parameters)

        for name in self._param_names:
            if np.random.random() > self._config.mutation_prob:
                continue

            lo, hi = self._bounds[name]
            x = params.get(name, (lo + hi) / 2)

            delta_max = hi - lo
            if delta_max < 1e-14:
                continue

            r = np.random.random()
            if r < 0.5:
                delta = (2.0 * r) ** (1.0 / (eta + 1.0)) - 1.0
            else:
                delta = 1.0 - (2.0 * (1.0 - r)) ** (1.0 / (eta + 1.0))

            x_new = x + delta * delta_max * self._config.mutation_scale
            params[name] = float(np.clip(x_new, lo, hi))

        return Individual(parameters=params)


# ── AEGIS Objective Functions ──────────────────────────────────────────

class AEGISObjectives:
    """Defines the 6 AEGIS optimisation objectives.

    All functions accept a parameter dict and return a float.
    Designed to be evaluated via backtesting or simulation.
    """

    def __init__(self, returns: Optional[np.ndarray] = None,
                 prices: Optional[np.ndarray] = None):
        """
        Args:
            returns: Historical return series for backtest evaluation.
            prices: Historical price series for drawdown calculation.
        """
        self._returns = returns if returns is not None else np.array([])
        self._prices = prices if prices is not None else np.array([])

    def sharpe(self, params: Dict[str, float]) -> float:
        """Annualised Sharpe ratio.

        Maximise. Uses returns * signal as proxy for strategy returns.
        """
        r = self._strategy_returns(params)
        if len(r) < 20:
            return -10.0
        mean_r = np.mean(r)
        std_r = np.std(r, ddof=1)
        if std_r < 1e-10:
            return 0.0
        return float(mean_r / std_r * math.sqrt(252))

    def max_drawdown(self, params: Dict[str, float]) -> float:
        """Maximum drawdown (as positive fraction).

        Minimise. ISA constraint: must be < 8%.
        """
        r = self._strategy_returns(params)
        if len(r) < 2:
            return 1.0

        cumulative = np.cumprod(1.0 + r)
        peak = np.maximum.accumulate(cumulative)
        drawdowns = (peak - cumulative) / np.where(peak > 1e-10, peak, 1.0)
        return float(np.max(drawdowns))

    def sortino(self, params: Dict[str, float]) -> float:
        """Annualised Sortino ratio (downside deviation only).

        Maximise.
        """
        r = self._strategy_returns(params)
        if len(r) < 20:
            return -10.0
        mean_r = np.mean(r)
        downside = r[r < 0]
        if len(downside) < 2:
            return 10.0  # No downside = great
        downside_std = np.std(downside, ddof=1)
        if downside_std < 1e-10:
            return 10.0
        return float(mean_r / downside_std * math.sqrt(252))

    def calmar(self, params: Dict[str, float]) -> float:
        """Calmar ratio = annualised return / max drawdown.

        Maximise.
        """
        r = self._strategy_returns(params)
        if len(r) < 20:
            return -10.0
        ann_return = float(np.mean(r) * 252)
        dd = self.max_drawdown(params)
        if dd < 1e-10:
            return 10.0
        return ann_return / dd

    def trade_count(self, params: Dict[str, float]) -> float:
        """Penalised trade count metric.

        Minimise. Returns penalty based on distance from [50, 200] target range.
        """
        # Estimate trade count from signal threshold param
        threshold = params.get("signal_threshold", 0.5)
        if len(self._returns) < 2:
            return 100.0

        # Simulate: trades occur when signal crosses threshold
        signal = self._generate_signal(params)
        n_trades = int(np.sum(np.abs(np.diff(np.sign(signal - threshold))) > 0))

        if TRADE_COUNT_MIN <= n_trades <= TRADE_COUNT_MAX:
            return 0.0
        elif n_trades < TRADE_COUNT_MIN:
            return float(TRADE_COUNT_MIN - n_trades)
        else:
            return float(n_trades - TRADE_COUNT_MAX)

    def portfolio_correlation(self, params: Dict[str, float]) -> float:
        """Average correlation with other strategies.

        Minimise. Lower correlation = better diversification.
        Returns a proxy based on parameter similarity to baseline.
        """
        # Proxy: correlation of strategy returns with buy-and-hold
        r = self._strategy_returns(params)
        if len(r) < 20 or len(self._returns) < 20:
            return 1.0
        bh = self._returns[:len(r)]
        if len(bh) != len(r):
            return 1.0
        corr_matrix = np.corrcoef(r, bh)
        return float(abs(corr_matrix[0, 1]))

    # ── Private Helpers ────────────────────────────────────────────────

    def _strategy_returns(self, params: Dict[str, float]) -> np.ndarray:
        """Simulate strategy returns based on parameters."""
        if len(self._returns) < 2:
            return np.array([])

        signal = self._generate_signal(params)
        positions = np.sign(signal[:-1])
        return positions * self._returns[1:len(signal)]

    def _generate_signal(self, params: Dict[str, float]) -> np.ndarray:
        """Generate a synthetic signal from parameters and returns.

        Uses a simple momentum/mean-reversion model parameterised by
        lookback and threshold.
        """
        n = len(self._returns)
        if n < 2:
            return np.array([0.0])

        lookback = max(2, int(params.get("lookback", 20)))
        momentum_weight = params.get("momentum_weight", 0.5)

        # Rolling mean return as momentum signal
        signal = np.zeros(n)
        for i in range(lookback, n):
            window = self._returns[i - lookback:i]
            signal[i] = np.mean(window) * momentum_weight

        return signal


# ── Pareto Analysis ────────────────────────────────────────────────────

class ParetoAnalyzer:
    """Utilities for analysing Pareto fronts."""

    @staticmethod
    def find_knee_point(front: List[Individual]) -> Optional[Individual]:
        """Find the knee point using the L-method.

        The knee point is where the trade-off between objectives
        changes most rapidly (maximum distance from the line
        connecting the extreme points).

        Args:
            front: List of Pareto-optimal individuals.

        Returns:
            Knee-point Individual, or None if front is empty.
        """
        if not front:
            return None
        if len(front) <= 2:
            return front[0]

        # Extract objective values
        obj_keys = sorted(front[0].objectives.keys())
        n_obj = len(obj_keys)

        if n_obj < 2:
            return front[0]

        # Use first two objectives for knee detection
        points = np.array([
            [ind.objectives.get(obj_keys[0], 0.0),
             ind.objectives.get(obj_keys[1], 0.0)]
            for ind in front
        ])

        # Line from first to last point (sorted by first objective)
        sort_idx = np.argsort(points[:, 0])
        points_sorted = points[sort_idx]
        front_sorted = [front[i] for i in sort_idx]

        p1 = points_sorted[0]
        p2 = points_sorted[-1]
        line_vec = p2 - p1
        line_len = np.linalg.norm(line_vec)

        if line_len < 1e-10:
            return front_sorted[len(front_sorted) // 2]

        # Distance from each point to the line
        max_dist = -1.0
        knee_idx = 0
        for i, pt in enumerate(points_sorted):
            # Perpendicular distance to line p1-p2
            v = pt - p1
            proj = np.dot(v, line_vec) / (line_len ** 2)
            closest = p1 + proj * line_vec
            dist = float(np.linalg.norm(pt - closest))

            if dist > max_dist:
                max_dist = dist
                knee_idx = i

        return front_sorted[knee_idx]

    @staticmethod
    def find_isa_optimal(front: List[Individual]) -> Optional[Individual]:
        """Find the best solution where max_drawdown < 8%.

        Prioritises Sharpe ratio among ISA-feasible solutions.

        Args:
            front: Pareto front individuals.

        Returns:
            Best ISA-compliant Individual, or None.
        """
        feasible = [ind for ind in front if ind.feasible]
        if not feasible:
            # Fall back to lowest drawdown
            if not front:
                return None
            return min(front, key=lambda x: abs(x.objectives.get("obj_1", 1.0)))

        # Among feasible, maximise Sharpe (obj_0)
        return max(feasible, key=lambda x: x.objectives.get("obj_0", -999.0))

    @staticmethod
    def hypervolume(front: List[Individual],
                    reference_point: np.ndarray) -> float:
        """Compute hypervolume indicator for a Pareto front.

        Uses a simple inclusion-exclusion approach for low dimensions.
        For high dimensions, this is an approximation via Monte Carlo.

        Args:
            front: List of Pareto-optimal individuals.
            reference_point: Reference (anti-ideal) point.

        Returns:
            Hypervolume value (higher = better front).
        """
        if not front:
            return 0.0

        obj_keys = sorted(front[0].objectives.keys())
        n_obj = len(obj_keys)

        if n_obj == 0:
            return 0.0

        # Extract objective matrix
        points = np.array([
            [ind.objectives.get(k, 0.0) for k in obj_keys]
            for ind in front
        ])

        if n_obj <= 2:
            return ParetoAnalyzer._hv_2d(points, reference_point[:n_obj])
        else:
            return ParetoAnalyzer._hv_monte_carlo(points, reference_point[:n_obj])

    @staticmethod
    def _hv_2d(points: np.ndarray, ref: np.ndarray) -> float:
        """Exact 2D hypervolume computation."""
        if len(points) == 0:
            return 0.0

        # Sort by first objective (ascending)
        sorted_idx = np.argsort(points[:, 0])
        sorted_points = points[sorted_idx]

        hv = 0.0
        prev_y = ref[1] if len(ref) > 1 else 0.0

        for pt in sorted_points:
            x_contrib = ref[0] - pt[0] if ref[0] > pt[0] else 0.0
            y_val = pt[1] if len(pt) > 1 else 0.0

            if y_val < prev_y:
                y_contrib = prev_y - y_val
                hv += x_contrib * y_contrib
                prev_y = y_val

        return max(hv, 0.0)

    @staticmethod
    def _hv_monte_carlo(points: np.ndarray, ref: np.ndarray,
                        n_samples: int = 10000) -> float:
        """Monte Carlo hypervolume approximation for 3+ objectives."""
        n_obj = points.shape[1]

        # Find ideal point (minimum per objective)
        ideal = points.min(axis=0)

        # Sample random points in the hyperbox [ideal, ref]
        ranges = ref - ideal
        if np.any(ranges <= 0):
            return 0.0

        volume = float(np.prod(ranges))
        dominated_count = 0

        for _ in range(n_samples):
            sample = ideal + np.random.random(n_obj) * ranges
            # Check if sample is dominated by any point in front
            for pt in points:
                if np.all(pt <= sample):
                    dominated_count += 1
                    break

        return volume * dominated_count / n_samples

    @staticmethod
    def save_front(front: List[Individual], path: str) -> None:
        """Persist Pareto front to JSON.

        Args:
            front: List of Pareto-optimal individuals.
            path: Output file path.
        """
        try:
            out_path = Path(path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "pareto_front": [ind.to_dict() for ind in front],
                "n_solutions": len(front),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            with open(str(out_path), "w") as f:
                json.dump(data, f, indent=2, default=str)
            log.info("Saved Pareto front (%d solutions) to %s", len(front), path)
        except Exception as e:
            log.warning("Failed to save Pareto front: %s", e)
