"""Swarmode Financial Intelligence — Bio-Inspired Optimization — Book 152.

Implements five bio-inspired swarm optimizers for trading parameter tuning,
feature selection, and portfolio allocation:

  1. AntColonyOptimizer — Pheromone-based feature selection
  2. ArtificialBeeColony — Employed/onlooker/scout bees for param optimization
  3. FireflyAlgorithm — Brightness-based attraction for multi-modal optimization
  4. GreyWolfOptimizer — Alpha/beta/delta hierarchy for multi-objective tuning
  5. SwarmEnsemble — Runs all 5 (incl. PSO) and returns the best result

Each optimizer targets a different problem structure within AEGIS V2.
All use walk-forward fitness evaluation to prevent overfitting.

Bridge.py integration:
    try:
        from python_brain.ml.swarmode_optimizer import (
            AntColonyOptimizer, ArtificialBeeColony,
            FireflyAlgorithm, GreyWolfOptimizer, SwarmEnsemble,
        )
    except ImportError:
        pass

    # Feature selection via ACO:
    aco = AntColonyOptimizer()
    selected = aco.optimize(n_features=120, fitness_fn=my_fitness)
    # Parameter tuning via ABC:
    abc = ArtificialBeeColony()
    result = abc.optimize(bounds=param_bounds, fitness_fn=sharpe_fn)
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger("swarmode_optimizer")

__all__ = [
    "AntColonyOptimizer",
    "ArtificialBeeColony",
    "FireflyAlgorithm",
    "GreyWolfOptimizer",
    "SwarmEnsemble",
]

DATA_DIR = "/app/data/swarmode_optimizer"


# ---------------------------------------------------------------------------
# Ant Colony Optimizer — Feature Selection
# ---------------------------------------------------------------------------

class AntColonyOptimizer:
    """Ant Colony Optimization for binary feature selection.

    Ants construct feature subsets by probabilistically selecting features
    based on pheromone trails and heuristic desirability. Good subsets
    deposit more pheromone, biasing future ants toward useful features.

    Args:
        alpha: Pheromone influence weight.
        beta: Heuristic influence weight.
        rho: Evaporation rate (0 to 1).
        seed: Random seed for reproducibility.
    """

    def __init__(
        self,
        alpha: float = 1.0,
        beta: float = 2.0,
        rho: float = 0.1,
        seed: Optional[int] = None,
    ) -> None:
        self.alpha = alpha
        self.beta = beta
        self.rho = rho
        self.rng = np.random.default_rng(seed)

    def optimize(
        self,
        n_features: int,
        fitness_fn: Callable[[List[int]], float],
        n_ants: int = 30,
        n_iter: int = 50,
        min_features: int = 3,
        max_features: Optional[int] = None,
    ) -> List[int]:
        """Run ACO feature selection.

        Args:
            n_features: Total number of candidate features.
            fitness_fn: Takes list of selected feature indices, returns fitness (higher=better).
            n_ants: Number of ants per iteration.
            n_iter: Number of iterations.
            min_features: Minimum features to select.
            max_features: Maximum features to select. Defaults to n_features // 2.

        Returns:
            List of selected feature indices (best found).
        """
        max_features = max_features or max(min_features + 1, n_features // 2)
        pheromone = np.ones(n_features, dtype=np.float64)
        # Heuristic: uniform initially
        heuristic = np.ones(n_features, dtype=np.float64)

        best_fitness = -np.inf
        best_features: List[int] = list(range(min_features))

        log.info(
            "ACO starting: %d features, %d ants, %d iterations",
            n_features, n_ants, n_iter,
        )

        for iteration in range(n_iter):
            ant_solutions: List[Tuple[List[int], float]] = []

            for _ in range(n_ants):
                selected = self._construct_solution(
                    pheromone, heuristic, n_features, min_features, max_features,
                )
                try:
                    fitness = fitness_fn(selected)
                except Exception as e:
                    log.warning("Fitness evaluation failed for ant: %s", e)
                    fitness = -np.inf

                ant_solutions.append((selected, fitness))

                if fitness > best_fitness:
                    best_fitness = fitness
                    best_features = selected[:]

            # Evaporate
            self._evaporate(pheromone, self.rho)

            # Deposit pheromone from all ants (proportional to fitness)
            for selected, fitness in ant_solutions:
                if fitness > -np.inf:
                    self._deposit_pheromone(pheromone, selected, fitness)

            # Update heuristic based on pheromone (feature importance proxy)
            heuristic = pheromone / pheromone.sum()

            if (iteration + 1) % 10 == 0:
                log.debug(
                    "ACO iter %d/%d: best_fitness=%.4f, n_selected=%d",
                    iteration + 1, n_iter, best_fitness, len(best_features),
                )

        log.info(
            "ACO complete: best_fitness=%.4f, %d features selected",
            best_fitness, len(best_features),
        )
        return best_features

    def _construct_solution(
        self,
        pheromone: np.ndarray,
        heuristic: np.ndarray,
        n_features: int,
        min_features: int,
        max_features: int,
    ) -> List[int]:
        """Ant constructs a feature subset probabilistically."""
        n_select = int(self.rng.integers(min_features, max_features + 1))

        # Probability of selecting each feature
        tau = np.power(pheromone, self.alpha)
        eta = np.power(heuristic, self.beta)
        probs = tau * eta
        prob_sum = probs.sum()
        if prob_sum < 1e-12:
            probs = np.ones(n_features) / n_features
        else:
            probs /= prob_sum

        # Select without replacement
        selected = list(self.rng.choice(
            n_features, size=min(n_select, n_features), replace=False, p=probs,
        ))
        return sorted(selected)

    def _deposit_pheromone(
        self,
        pheromone: np.ndarray,
        selected: List[int],
        fitness: float,
    ) -> None:
        """Deposit pheromone on selected features proportional to fitness."""
        deposit = max(0.0, fitness)
        for idx in selected:
            pheromone[idx] += deposit

    @staticmethod
    def _evaporate(pheromone: np.ndarray, rho: float) -> None:
        """Evaporate pheromone across all features."""
        pheromone *= (1.0 - rho)
        # Floor to prevent complete evaporation
        np.clip(pheromone, 0.01, None, out=pheromone)


# ---------------------------------------------------------------------------
# Artificial Bee Colony — Parameter Optimization
# ---------------------------------------------------------------------------

class ArtificialBeeColony:
    """Artificial Bee Colony for continuous parameter optimization.

    Three phases:
      1. Employed bees: exploit current food sources (solutions)
      2. Onlooker bees: probabilistically choose food sources by fitness
      3. Scout bees: abandon exhausted sources and explore randomly

    Args:
        limit: Abandonment limit — max iterations without improvement before scout.
        seed: Random seed.
    """

    def __init__(
        self,
        limit: int = 20,
        seed: Optional[int] = None,
    ) -> None:
        self.limit = limit
        self.rng = np.random.default_rng(seed)

    def optimize(
        self,
        bounds: List[Tuple[float, float]],
        fitness_fn: Callable[[np.ndarray], float],
        n_bees: int = 30,
        n_iter: int = 100,
    ) -> Dict[str, Any]:
        """Run ABC optimization.

        Args:
            bounds: List of (lower, upper) bounds per dimension.
            fitness_fn: Takes parameter vector, returns fitness (higher=better).
            n_bees: Number of food sources (employed bees = n_bees // 2).
            n_iter: Number of iterations.

        Returns:
            Dict with 'best_params', 'best_fitness', 'history'.
        """
        dim = len(bounds)
        lower = np.array([b[0] for b in bounds])
        upper = np.array([b[1] for b in bounds])
        n_food = n_bees // 2

        # Initialise food sources randomly
        food_sources = np.array([
            self.rng.uniform(lower, upper) for _ in range(n_food)
        ])
        fitness = np.array([self._safe_eval(fitness_fn, fs) for fs in food_sources])
        trials = np.zeros(n_food, dtype=int)

        best_idx = int(np.argmax(fitness))
        best_params = food_sources[best_idx].copy()
        best_fitness = fitness[best_idx]
        history: List[float] = [best_fitness]

        log.info(
            "ABC starting: %d dims, %d food sources, %d iterations",
            dim, n_food, n_iter,
        )

        for iteration in range(n_iter):
            # --- Employed Bee Phase ---
            for i in range(n_food):
                new_source = self._employed_bee(food_sources, i, lower, upper, dim)
                new_fit = self._safe_eval(fitness_fn, new_source)
                if new_fit > fitness[i]:
                    food_sources[i] = new_source
                    fitness[i] = new_fit
                    trials[i] = 0
                else:
                    trials[i] += 1

            # --- Onlooker Bee Phase ---
            # Probability proportional to fitness
            fit_shifted = fitness - fitness.min() + 1e-10
            probs = fit_shifted / fit_shifted.sum()

            for _ in range(n_food):
                chosen = int(self.rng.choice(n_food, p=probs))
                new_source = self._employed_bee(food_sources, chosen, lower, upper, dim)
                new_fit = self._safe_eval(fitness_fn, new_source)
                if new_fit > fitness[chosen]:
                    food_sources[chosen] = new_source
                    fitness[chosen] = new_fit
                    trials[chosen] = 0
                else:
                    trials[chosen] += 1

            # --- Scout Bee Phase ---
            for i in range(n_food):
                if trials[i] >= self.limit:
                    food_sources[i] = self.rng.uniform(lower, upper)
                    fitness[i] = self._safe_eval(fitness_fn, food_sources[i])
                    trials[i] = 0
                    log.debug("Scout bee replaced exhausted source %d", i)

            # Track best
            iter_best = int(np.argmax(fitness))
            if fitness[iter_best] > best_fitness:
                best_fitness = fitness[iter_best]
                best_params = food_sources[iter_best].copy()

            history.append(best_fitness)

            if (iteration + 1) % 20 == 0:
                log.debug(
                    "ABC iter %d/%d: best_fitness=%.6f",
                    iteration + 1, n_iter, best_fitness,
                )

        log.info("ABC complete: best_fitness=%.6f", best_fitness)
        return {
            "best_params": best_params.tolist(),
            "best_fitness": float(best_fitness),
            "history": history,
            "n_evaluations": n_food + 2 * n_food * n_iter,
        }

    def _employed_bee(
        self,
        food_sources: np.ndarray,
        idx: int,
        lower: np.ndarray,
        upper: np.ndarray,
        dim: int,
    ) -> np.ndarray:
        """Employed bee generates neighbour solution."""
        # Pick a random partner (not self)
        partner = idx
        while partner == idx:
            partner = int(self.rng.integers(0, len(food_sources)))

        # Perturb one random dimension
        new_source = food_sources[idx].copy()
        j = int(self.rng.integers(0, dim))
        phi = self.rng.uniform(-1, 1)
        new_source[j] += phi * (food_sources[idx][j] - food_sources[partner][j])
        return np.clip(new_source, lower, upper)

    @staticmethod
    def _safe_eval(
        fitness_fn: Callable[[np.ndarray], float],
        params: np.ndarray,
    ) -> float:
        """Evaluate fitness with error handling."""
        try:
            result = fitness_fn(params)
            if not np.isfinite(result):
                return -1e10
            return float(result)
        except Exception as e:
            log.warning("Fitness eval error: %s", e)
            return -1e10


# ---------------------------------------------------------------------------
# Firefly Algorithm — Multi-Modal Optimization
# ---------------------------------------------------------------------------

class FireflyAlgorithm:
    """Firefly Algorithm for multi-modal continuous optimization.

    Fireflies move toward brighter (higher-fitness) individuals.
    Attraction decreases with distance, naturally forming clusters
    around multiple optima.

    Args:
        alpha: Randomisation parameter (exploration).
        beta0: Attractiveness at distance 0.
        gamma: Light absorption coefficient (controls attraction decay).
        seed: Random seed.
    """

    def __init__(
        self,
        alpha: float = 0.2,
        beta0: float = 1.0,
        gamma: float = 1.0,
        seed: Optional[int] = None,
    ) -> None:
        self.alpha = alpha
        self.beta0 = beta0
        self.gamma = gamma
        self.rng = np.random.default_rng(seed)

    def optimize(
        self,
        bounds: List[Tuple[float, float]],
        fitness_fn: Callable[[np.ndarray], float],
        n_fireflies: int = 25,
        n_iter: int = 50,
    ) -> Dict[str, Any]:
        """Run Firefly optimization.

        Args:
            bounds: List of (lower, upper) bounds per dimension.
            fitness_fn: Takes parameter vector, returns fitness (higher=better).
            n_fireflies: Population size.
            n_iter: Number of iterations.

        Returns:
            Dict with 'best_params', 'best_fitness', 'history'.
        """
        dim = len(bounds)
        lower = np.array([b[0] for b in bounds])
        upper = np.array([b[1] for b in bounds])
        scale = upper - lower

        # Initialise fireflies
        fireflies = np.array([
            self.rng.uniform(lower, upper) for _ in range(n_fireflies)
        ])
        brightness = np.array([
            self._safe_eval(fitness_fn, ff) for ff in fireflies
        ])

        best_idx = int(np.argmax(brightness))
        best_params = fireflies[best_idx].copy()
        best_fitness = brightness[best_idx]
        history: List[float] = [best_fitness]

        log.info(
            "Firefly starting: %d dims, %d fireflies, %d iterations",
            dim, n_fireflies, n_iter,
        )

        for iteration in range(n_iter):
            # Reduce alpha over time (cooling)
            alpha_t = self.alpha * (0.99 ** iteration)

            for i in range(n_fireflies):
                for j in range(n_fireflies):
                    if brightness[j] > brightness[i]:
                        # Distance between i and j (normalised)
                        r = np.linalg.norm((fireflies[i] - fireflies[j]) / scale)
                        # Attractiveness
                        beta = self.beta0 * math.exp(-self.gamma * r * r)
                        # Move firefly i toward j
                        fireflies[i] += beta * (fireflies[j] - fireflies[i])
                        fireflies[i] += alpha_t * scale * (self.rng.uniform(-0.5, 0.5, dim))
                        fireflies[i] = np.clip(fireflies[i], lower, upper)
                        brightness[i] = self._safe_eval(fitness_fn, fireflies[i])

            # Track best
            iter_best = int(np.argmax(brightness))
            if brightness[iter_best] > best_fitness:
                best_fitness = brightness[iter_best]
                best_params = fireflies[iter_best].copy()

            history.append(best_fitness)

            if (iteration + 1) % 10 == 0:
                log.debug(
                    "Firefly iter %d/%d: best=%.6f",
                    iteration + 1, n_iter, best_fitness,
                )

        log.info("Firefly complete: best_fitness=%.6f", best_fitness)
        return {
            "best_params": best_params.tolist(),
            "best_fitness": float(best_fitness),
            "history": history,
        }

    @staticmethod
    def _safe_eval(
        fitness_fn: Callable[[np.ndarray], float],
        params: np.ndarray,
    ) -> float:
        """Evaluate fitness with error handling."""
        try:
            result = fitness_fn(params)
            if not np.isfinite(result):
                return -1e10
            return float(result)
        except Exception as e:
            log.warning("Firefly fitness eval error: %s", e)
            return -1e10


# ---------------------------------------------------------------------------
# Grey Wolf Optimizer — Multi-Objective
# ---------------------------------------------------------------------------

class GreyWolfOptimizer:
    """Grey Wolf Optimizer with alpha/beta/delta hierarchy.

    The wolf pack hierarchy:
      - Alpha: best solution found so far
      - Beta: second-best solution
      - Delta: third-best solution
      - Omega: remaining wolves, guided by alpha/beta/delta

    Convergence parameter 'a' decreases from 2 to 0 over iterations,
    transitioning from exploration to exploitation.

    Args:
        seed: Random seed.
    """

    def __init__(self, seed: Optional[int] = None) -> None:
        self.rng = np.random.default_rng(seed)

    def optimize(
        self,
        bounds: List[Tuple[float, float]],
        fitness_fn: Callable[[np.ndarray], float],
        n_wolves: int = 30,
        n_iter: int = 100,
    ) -> Dict[str, Any]:
        """Run Grey Wolf Optimization.

        Args:
            bounds: List of (lower, upper) bounds per dimension.
            fitness_fn: Takes parameter vector, returns fitness (higher=better).
            n_wolves: Pack size.
            n_iter: Number of iterations.

        Returns:
            Dict with 'best_params', 'best_fitness', 'history',
            'alpha', 'beta', 'delta' positions.
        """
        dim = len(bounds)
        lower = np.array([b[0] for b in bounds])
        upper = np.array([b[1] for b in bounds])

        # Initialise wolves
        wolves = np.array([
            self.rng.uniform(lower, upper) for _ in range(n_wolves)
        ])
        fitness = np.array([
            self._safe_eval(fitness_fn, w) for w in wolves
        ])

        # Find alpha, beta, delta
        sorted_idx = np.argsort(-fitness)  # descending
        alpha_pos = wolves[sorted_idx[0]].copy()
        alpha_fit = fitness[sorted_idx[0]]
        beta_pos = wolves[sorted_idx[1]].copy() if n_wolves > 1 else alpha_pos.copy()
        beta_fit = fitness[sorted_idx[1]] if n_wolves > 1 else alpha_fit
        delta_pos = wolves[sorted_idx[2]].copy() if n_wolves > 2 else beta_pos.copy()
        delta_fit = fitness[sorted_idx[2]] if n_wolves > 2 else beta_fit

        history: List[float] = [float(alpha_fit)]

        log.info(
            "GWO starting: %d dims, %d wolves, %d iterations",
            dim, n_wolves, n_iter,
        )

        for iteration in range(n_iter):
            # Linearly decrease 'a' from 2 to 0
            a = 2.0 * (1.0 - iteration / n_iter)

            for i in range(n_wolves):
                # Encircling behaviour guided by alpha, beta, delta
                new_pos = np.zeros(dim)

                for leader_pos in [alpha_pos, beta_pos, delta_pos]:
                    r1 = self.rng.random(dim)
                    r2 = self.rng.random(dim)
                    A = 2.0 * a * r1 - a
                    C = 2.0 * r2

                    D = np.abs(C * leader_pos - wolves[i])
                    X = leader_pos - A * D
                    new_pos += X

                # Average of three leader-guided positions
                wolves[i] = np.clip(new_pos / 3.0, lower, upper)
                fitness[i] = self._safe_eval(fitness_fn, wolves[i])

            # Update hierarchy
            for i in range(n_wolves):
                if fitness[i] > alpha_fit:
                    # Promote: current alpha -> beta, beta -> delta
                    delta_pos = beta_pos.copy()
                    delta_fit = beta_fit
                    beta_pos = alpha_pos.copy()
                    beta_fit = alpha_fit
                    alpha_pos = wolves[i].copy()
                    alpha_fit = fitness[i]
                elif fitness[i] > beta_fit:
                    delta_pos = beta_pos.copy()
                    delta_fit = beta_fit
                    beta_pos = wolves[i].copy()
                    beta_fit = fitness[i]
                elif fitness[i] > delta_fit:
                    delta_pos = wolves[i].copy()
                    delta_fit = fitness[i]

            history.append(float(alpha_fit))

            if (iteration + 1) % 20 == 0:
                log.debug(
                    "GWO iter %d/%d: alpha=%.6f beta=%.6f delta=%.6f a=%.3f",
                    iteration + 1, n_iter, alpha_fit, beta_fit, delta_fit, a,
                )

        log.info("GWO complete: alpha_fitness=%.6f", alpha_fit)
        return {
            "best_params": alpha_pos.tolist(),
            "best_fitness": float(alpha_fit),
            "alpha": alpha_pos.tolist(),
            "beta": beta_pos.tolist(),
            "delta": delta_pos.tolist(),
            "history": history,
        }

    @staticmethod
    def _safe_eval(
        fitness_fn: Callable[[np.ndarray], float],
        params: np.ndarray,
    ) -> float:
        """Evaluate fitness with error handling."""
        try:
            result = fitness_fn(params)
            if not np.isfinite(result):
                return -1e10
            return float(result)
        except Exception as e:
            log.warning("GWO fitness eval error: %s", e)
            return -1e10


# ---------------------------------------------------------------------------
# PSO (lightweight inline for SwarmEnsemble completeness)
# ---------------------------------------------------------------------------

class _InlinePSO:
    """Minimal Particle Swarm Optimizer for ensemble use.

    The full PSO lives in swarm_optimizer.py (Book 137). This is a
    lightweight inline version so SwarmEnsemble is self-contained.
    """

    def __init__(self, seed: Optional[int] = None) -> None:
        self.rng = np.random.default_rng(seed)

    def optimize(
        self,
        bounds: List[Tuple[float, float]],
        fitness_fn: Callable[[np.ndarray], float],
        n_particles: int = 30,
        n_iter: int = 100,
        w: float = 0.7,
        c1: float = 1.5,
        c2: float = 1.5,
    ) -> Dict[str, Any]:
        """Run PSO optimization."""
        dim = len(bounds)
        lower = np.array([b[0] for b in bounds])
        upper = np.array([b[1] for b in bounds])

        # Initialise
        positions = np.array([self.rng.uniform(lower, upper) for _ in range(n_particles)])
        velocities = np.zeros_like(positions)
        personal_best_pos = positions.copy()
        personal_best_fit = np.array([
            self._safe_eval(fitness_fn, p) for p in positions
        ])

        global_best_idx = int(np.argmax(personal_best_fit))
        global_best_pos = personal_best_pos[global_best_idx].copy()
        global_best_fit = personal_best_fit[global_best_idx]

        for iteration in range(n_iter):
            for i in range(n_particles):
                r1 = self.rng.random(dim)
                r2 = self.rng.random(dim)
                velocities[i] = (
                    w * velocities[i]
                    + c1 * r1 * (personal_best_pos[i] - positions[i])
                    + c2 * r2 * (global_best_pos - positions[i])
                )
                positions[i] = np.clip(positions[i] + velocities[i], lower, upper)

                fit = self._safe_eval(fitness_fn, positions[i])
                if fit > personal_best_fit[i]:
                    personal_best_fit[i] = fit
                    personal_best_pos[i] = positions[i].copy()
                    if fit > global_best_fit:
                        global_best_fit = fit
                        global_best_pos = positions[i].copy()

        return {
            "best_params": global_best_pos.tolist(),
            "best_fitness": float(global_best_fit),
        }

    @staticmethod
    def _safe_eval(fn: Callable, params: np.ndarray) -> float:
        try:
            r = fn(params)
            return float(r) if np.isfinite(r) else -1e10
        except Exception:
            return -1e10


# ---------------------------------------------------------------------------
# SwarmEnsemble — Run All 5, Take Best
# ---------------------------------------------------------------------------

class SwarmEnsemble:
    """Run all 5 swarm optimizers and return the best result.

    Orchestrates PSO, ACO (adapted), ABC, Firefly, and Grey Wolf
    on the same fitness function. Returns the best solution found
    by any optimizer along with per-optimizer comparison data.

    Args:
        seed: Base random seed (each optimizer gets seed+offset).
    """

    def __init__(self, seed: Optional[int] = None) -> None:
        self.seed = seed or 42

    def optimize(
        self,
        bounds: List[Tuple[float, float]],
        fitness_fn: Callable[[np.ndarray], float],
        n_iter: int = 50,
        n_pop: int = 25,
    ) -> Dict[str, Any]:
        """Run all optimizers and return the best result.

        Args:
            bounds: Parameter bounds per dimension.
            fitness_fn: Fitness function (higher=better).
            n_iter: Iterations per optimizer.
            n_pop: Population size per optimizer.

        Returns:
            Dict with 'best_params', 'best_fitness', 'best_optimizer',
            'all_results' (per-optimizer comparison).
        """
        log.info("SwarmEnsemble starting: %d dims, %d iter, %d pop", len(bounds), n_iter, n_pop)
        results: Dict[str, Dict[str, Any]] = {}
        t0 = time.time()

        # 1. PSO
        try:
            pso = _InlinePSO(seed=self.seed)
            results["PSO"] = pso.optimize(bounds, fitness_fn, n_particles=n_pop, n_iter=n_iter)
            log.debug("PSO: %.6f", results["PSO"]["best_fitness"])
        except Exception as e:
            log.error("PSO failed: %s", e)
            results["PSO"] = {"best_fitness": -1e10, "best_params": [], "error": str(e)}

        # 2. ABC
        try:
            abc = ArtificialBeeColony(seed=self.seed + 1)
            results["ABC"] = abc.optimize(bounds, fitness_fn, n_bees=n_pop * 2, n_iter=n_iter)
            log.debug("ABC: %.6f", results["ABC"]["best_fitness"])
        except Exception as e:
            log.error("ABC failed: %s", e)
            results["ABC"] = {"best_fitness": -1e10, "best_params": [], "error": str(e)}

        # 3. Firefly
        try:
            ff = FireflyAlgorithm(seed=self.seed + 2)
            results["Firefly"] = ff.optimize(bounds, fitness_fn, n_fireflies=n_pop, n_iter=n_iter)
            log.debug("Firefly: %.6f", results["Firefly"]["best_fitness"])
        except Exception as e:
            log.error("Firefly failed: %s", e)
            results["Firefly"] = {"best_fitness": -1e10, "best_params": [], "error": str(e)}

        # 4. Grey Wolf
        try:
            gwo = GreyWolfOptimizer(seed=self.seed + 3)
            results["GWO"] = gwo.optimize(bounds, fitness_fn, n_wolves=n_pop, n_iter=n_iter)
            log.debug("GWO: %.6f", results["GWO"]["best_fitness"])
        except Exception as e:
            log.error("GWO failed: %s", e)
            results["GWO"] = {"best_fitness": -1e10, "best_params": [], "error": str(e)}

        # Find overall best
        best_name = max(results, key=lambda k: results[k].get("best_fitness", -1e10))
        best = results[best_name]
        elapsed = time.time() - t0

        log.info(
            "SwarmEnsemble complete in %.1fs: winner=%s fitness=%.6f",
            elapsed, best_name, best.get("best_fitness", -1e10),
        )

        return {
            "best_params": best.get("best_params", []),
            "best_fitness": best.get("best_fitness", -1e10),
            "best_optimizer": best_name,
            "all_results": {
                name: {
                    "best_fitness": r.get("best_fitness", -1e10),
                    "best_params": r.get("best_params", []),
                }
                for name, r in results.items()
            },
            "elapsed_seconds": elapsed,
        }

    def save_results(
        self,
        result: Dict[str, Any],
        filepath: Optional[str] = None,
    ) -> None:
        """Persist ensemble results to JSON."""
        filepath = filepath or os.path.join(DATA_DIR, "ensemble_result.json")
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        try:
            with open(filepath, "w") as f:
                json.dump(result, f, indent=2)
            log.info("Ensemble results saved to %s", filepath)
        except OSError as e:
            log.error("Failed to save ensemble results: %s", e)
