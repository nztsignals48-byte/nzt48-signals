"""Particle Swarm Optimization for Portfolio Allocation — Book 137.

Implements PSO for optimising portfolio weights across AEGIS strategies.
PSO is particularly suited to this problem because:
  1. No gradient required (objective is a black-box backtest)
  2. Handles constraints naturally (simplex projection)
  3. Escapes local optima through swarm intelligence
  4. Fast convergence for moderate dimensions (< 50)

Components:
  - Particle: Single solution with position, velocity, personal best
  - PSOConfig: Algorithm hyperparameters
  - ParticleSwarmOptimizer: Generic PSO engine
  - PortfolioPSO: AEGIS-specific portfolio weight optimiser

Bridge.py integration:
    try:
        from python_brain.ml.swarm_optimizer import (
            PortfolioPSO, ParticleSwarmOptimizer, PSOConfig,
        )
    except ImportError:
        pass

    # In nightly pipeline:
    pso = PortfolioPSO(
        n_strategies=8,
        return_history=strategy_returns,
        risk_fn=lambda w: max_drawdown(w, strategy_returns),
    )
    result = pso.optimize_weights()
    new_weights = result["best_position"]
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger("swarm_optimizer")

__all__ = [
    "Particle",
    "PSOConfig",
    "ParticleSwarmOptimizer",
    "PortfolioPSO",
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Particle:
    """A single particle in the swarm.

    Attributes:
        position: Current position in search space.
        velocity: Current velocity vector.
        best_position: Personal best position found so far.
        best_fitness: Fitness at personal best position.
    """
    position: np.ndarray = field(default_factory=lambda: np.array([]))
    velocity: np.ndarray = field(default_factory=lambda: np.array([]))
    best_position: np.ndarray = field(default_factory=lambda: np.array([]))
    best_fitness: float = float("inf")

    def to_dict(self) -> Dict[str, Any]:
        """Serialise particle state."""
        return {
            "position": self.position.tolist() if hasattr(self.position, 'tolist') else [],
            "best_position": self.best_position.tolist() if hasattr(self.best_position, 'tolist') else [],
            "best_fitness": self.best_fitness,
        }


@dataclass
class PSOConfig:
    """Particle Swarm Optimization configuration.

    Attributes:
        n_particles: Number of particles in the swarm. More = better
                     exploration but slower. 30-100 typical.
        n_iterations: Maximum iterations. 50-200 typical.
        w: Inertia weight. Controls exploration/exploitation balance.
           0.4-0.9 typical. Can decay over iterations.
        c1: Cognitive coefficient (personal best attraction).
            1.0-2.0 typical.
        c2: Social coefficient (global best attraction).
            1.0-2.0 typical.
        bounds: (lower, upper) bounds per dimension. If None, unbounded.
        w_decay: If True, linearly decay w from w to 0.4 over iterations.
        v_max_fraction: Maximum velocity as fraction of search range.
    """
    n_particles: int = 50
    n_iterations: int = 100
    w: float = 0.7
    c1: float = 1.5
    c2: float = 1.5
    bounds: Optional[Tuple[np.ndarray, np.ndarray]] = None
    w_decay: bool = True
    v_max_fraction: float = 0.2


# ---------------------------------------------------------------------------
# Particle Swarm Optimizer
# ---------------------------------------------------------------------------

class ParticleSwarmOptimizer:
    """Generic Particle Swarm Optimization engine.

    Minimises an objective function f(x) over a bounded search space.
    For maximisation, negate the objective.

    Velocity update:
      v(t+1) = w*v(t) + c1*r1*(pbest - x) + c2*r2*(gbest - x)

    Position update:
      x(t+1) = x(t) + v(t+1)

    where r1, r2 ~ U(0,1) are random vectors.

    Attributes:
        objective_fn: Function to minimise, f(position) -> float.
        bounds: (lower, upper) bounds arrays.
        config: PSO configuration.
    """

    def __init__(
        self,
        objective_fn: Callable[[np.ndarray], float],
        bounds: Tuple[np.ndarray, np.ndarray],
        config: Optional[PSOConfig] = None,
    ) -> None:
        """Initialise PSO.

        Args:
            objective_fn: Function to minimise. Takes 1-D array, returns float.
            bounds: Tuple of (lower_bounds, upper_bounds), each shape (n_dims,).
            config: PSO configuration.
        """
        self._objective = objective_fn
        self._lower = np.asarray(bounds[0], dtype=float)
        self._upper = np.asarray(bounds[1], dtype=float)
        self._n_dims = len(self._lower)
        self._config = config or PSOConfig()

        self._particles: List[Particle] = []
        self._global_best_position: np.ndarray = np.zeros(self._n_dims)
        self._global_best_fitness: float = float("inf")
        self._history: List[Dict[str, float]] = []

    def optimize(self) -> Dict[str, Any]:
        """Run PSO optimization.

        Returns:
            Dict with keys:
              - best_position: Optimal position found.
              - best_fitness: Fitness at optimal position.
              - n_iterations: Iterations executed.
              - history: Per-iteration statistics.
              - convergence: Whether swarm converged.
              - particles: Final swarm state.
        """
        self._initialize_swarm()

        log.info("PSO: %d particles, %d dims, %d iterations",
                 self._config.n_particles, self._n_dims,
                 self._config.n_iterations)

        # Velocity limits
        search_range = self._upper - self._lower
        v_max = search_range * self._config.v_max_fraction

        converged = False

        for iteration in range(self._config.n_iterations):
            # Inertia weight (optional decay)
            if self._config.w_decay:
                w = self._config.w - (self._config.w - 0.4) * (
                    iteration / max(self._config.n_iterations - 1, 1)
                )
            else:
                w = self._config.w

            fitness_values: List[float] = []

            for particle in self._particles:
                # Evaluate fitness
                fitness = self._evaluate(particle.position)
                fitness_values.append(fitness)

                # Update personal best
                if fitness < particle.best_fitness:
                    particle.best_fitness = fitness
                    particle.best_position = particle.position.copy()

                # Update global best
                if fitness < self._global_best_fitness:
                    self._global_best_fitness = fitness
                    self._global_best_position = particle.position.copy()

            # Update velocities and positions
            for particle in self._particles:
                new_velocity = self._update_velocity(
                    particle, self._global_best_position, w
                )

                # Clamp velocity
                new_velocity = np.clip(new_velocity, -v_max, v_max)
                particle.velocity = new_velocity

                new_position = self._update_position(particle)
                particle.position = new_position

            # Record history
            mean_fitness = float(np.mean(fitness_values))
            std_fitness = float(np.std(fitness_values))
            self._history.append({
                "iteration": iteration,
                "best_fitness": self._global_best_fitness,
                "mean_fitness": mean_fitness,
                "std_fitness": std_fitness,
                "inertia_w": w,
            })

            # Log progress
            if iteration % 20 == 0 or iteration == self._config.n_iterations - 1:
                log.info("PSO iter %d: best=%.6f, mean=%.6f, std=%.6f",
                         iteration, self._global_best_fitness, mean_fitness, std_fitness)

            # Convergence check: if swarm has collapsed
            if std_fitness < 1e-10 and iteration > 10:
                log.info("PSO converged at iteration %d", iteration)
                converged = True
                break

        return {
            "best_position": self._global_best_position.tolist(),
            "best_fitness": self._global_best_fitness,
            "n_iterations": len(self._history),
            "history": self._history,
            "convergence": converged,
            "particles": [p.to_dict() for p in self._particles[:10]],  # Top 10 only
        }

    def _initialize_swarm(self) -> None:
        """Initialise particle positions and velocities."""
        self._particles = []
        search_range = self._upper - self._lower

        for _ in range(self._config.n_particles):
            position = self._lower + np.random.random(self._n_dims) * search_range
            velocity = (np.random.random(self._n_dims) - 0.5) * search_range * 0.1

            particle = Particle(
                position=position,
                velocity=velocity,
                best_position=position.copy(),
                best_fitness=float("inf"),
            )
            self._particles.append(particle)

        self._global_best_fitness = float("inf")
        self._global_best_position = self._particles[0].position.copy()

    def _update_velocity(
        self,
        particle: Particle,
        global_best: np.ndarray,
        w: Optional[float] = None,
    ) -> np.ndarray:
        """Update particle velocity.

        v(t+1) = w*v(t) + c1*r1*(pbest - x) + c2*r2*(gbest - x)

        Args:
            particle: The particle to update.
            global_best: Global best position.
            w: Inertia weight (overrides config if provided).

        Returns:
            New velocity vector.
        """
        if w is None:
            w = self._config.w

        r1 = np.random.random(self._n_dims)
        r2 = np.random.random(self._n_dims)

        cognitive = self._config.c1 * r1 * (particle.best_position - particle.position)
        social = self._config.c2 * r2 * (global_best - particle.position)

        new_velocity = w * particle.velocity + cognitive + social
        return new_velocity

    def _update_position(self, particle: Particle) -> np.ndarray:
        """Update particle position with boundary handling.

        Clips position to bounds. Uses reflection at boundaries
        for better exploration.

        Args:
            particle: Particle to update.

        Returns:
            New position vector (clipped to bounds).
        """
        new_position = particle.position + particle.velocity

        # Reflective boundary handling
        for d in range(self._n_dims):
            if new_position[d] < self._lower[d]:
                new_position[d] = self._lower[d]
                particle.velocity[d] *= -0.5  # Reflect and dampen
            elif new_position[d] > self._upper[d]:
                new_position[d] = self._upper[d]
                particle.velocity[d] *= -0.5

        return new_position

    def _evaluate(self, position: np.ndarray) -> float:
        """Evaluate objective function with error handling.

        Args:
            position: Position to evaluate.

        Returns:
            Fitness value. Returns inf on error.
        """
        try:
            result = self._objective(position)
            if not np.isfinite(result):
                return float("inf")
            return float(result)
        except Exception as e:
            log.debug("PSO evaluation error: %s", e)
            return float("inf")


# ---------------------------------------------------------------------------
# Portfolio PSO — AEGIS-Specific
# ---------------------------------------------------------------------------

class PortfolioPSO:
    """PSO-based portfolio weight optimiser for AEGIS strategies.

    Optimises strategy allocation weights to maximise risk-adjusted
    return (Sharpe ratio) subject to:
      - Weights sum to 1 (simplex constraint)
      - All weights >= 0 (long-only)
      - Maximum single strategy weight (concentration limit)

    The simplex constraint is handled by projection after each
    position update, rather than penalty functions.

    Attributes:
        n_strategies: Number of strategies to allocate across.
        return_history: Historical return matrix (T, n_strategies).
        risk_fn: Optional custom risk function.
    """

    def __init__(
        self,
        n_strategies: int,
        return_history: np.ndarray,
        risk_fn: Optional[Callable[[np.ndarray], float]] = None,
        max_weight: float = 0.40,
        min_weight: float = 0.0,
    ) -> None:
        """Initialise portfolio PSO.

        Args:
            n_strategies: Number of strategy slots.
            return_history: Return matrix, shape (T, n_strategies).
            risk_fn: Custom risk function f(weights) -> risk_value.
                     If None, uses portfolio standard deviation.
            max_weight: Maximum weight for any single strategy.
            min_weight: Minimum weight (0 for long-only).
        """
        self.n_strategies = n_strategies
        self._returns = np.asarray(return_history, dtype=float)
        self._risk_fn = risk_fn
        self._max_weight = max_weight
        self._min_weight = min_weight

        if self._returns.ndim == 1:
            self._returns = self._returns.reshape(-1, 1)

        if self._returns.shape[1] != n_strategies:
            raise ValueError(
                f"return_history has {self._returns.shape[1]} columns "
                f"but n_strategies={n_strategies}"
            )

    def optimize_weights(
        self,
        config: Optional[PSOConfig] = None,
        risk_free_rate: float = 0.04,
    ) -> Dict[str, Any]:
        """Find optimal portfolio weights via PSO.

        Maximises Sharpe ratio (by minimising negative Sharpe).

        Args:
            config: PSO configuration.
            risk_free_rate: Annual risk-free rate for Sharpe calculation.

        Returns:
            Dict with keys:
              - best_position: Optimal weights (sums to 1).
              - best_fitness: Negative Sharpe (lower = better).
              - sharpe_ratio: Actual Sharpe ratio (positive = better).
              - expected_return: Annualised expected return.
              - expected_risk: Annualised portfolio volatility.
              - n_iterations: PSO iterations.
              - weights: Named dict of strategy -> weight.
        """
        if config is None:
            config = PSOConfig(
                n_particles=50,
                n_iterations=100,
                w=0.7,
                c1=1.5,
                c2=1.5,
            )

        # Bounds: each weight in [min_weight, max_weight]
        lower = np.full(self.n_strategies, self._min_weight)
        upper = np.full(self.n_strategies, self._max_weight)

        # Objective: negative Sharpe (PSO minimises)
        rf_daily = risk_free_rate / 252.0

        def objective(weights: np.ndarray) -> float:
            return self._objective(weights, rf_daily)

        # Create PSO with simplex projection wrapper
        pso = _SimplexPSO(
            objective_fn=objective,
            bounds=(lower, upper),
            config=config,
            projection_fn=self._constraint_projection,
        )

        result = pso.optimize()

        # Project final weights to simplex
        best_weights = self._constraint_projection(
            np.array(result["best_position"])
        )

        # Compute final metrics
        port_returns = self._returns @ best_weights
        mean_daily = float(np.mean(port_returns))
        std_daily = float(np.std(port_returns, ddof=1))

        ann_return = mean_daily * 252.0
        ann_risk = std_daily * math.sqrt(252.0)

        if ann_risk > 1e-10:
            sharpe = (ann_return - risk_free_rate) / ann_risk
        else:
            sharpe = 0.0

        # Strategy weight mapping
        weight_dict: Dict[str, float] = {}
        for i in range(self.n_strategies):
            weight_dict[f"strategy_{i}"] = round(float(best_weights[i]), 4)

        log.info("PortfolioPSO: Sharpe=%.3f, return=%.2f%%, risk=%.2f%%, iterations=%d",
                 sharpe, ann_return * 100, ann_risk * 100, result["n_iterations"])

        return {
            "best_position": best_weights.tolist(),
            "best_fitness": result["best_fitness"],
            "sharpe_ratio": round(sharpe, 4),
            "expected_return": round(ann_return, 6),
            "expected_risk": round(ann_risk, 6),
            "n_iterations": result["n_iterations"],
            "weights": weight_dict,
            "convergence": result.get("convergence", False),
        }

    def _objective(self, weights: np.ndarray, rf_daily: float) -> float:
        """Compute negative Sharpe ratio for minimisation.

        Args:
            weights: Portfolio weights (may not sum to 1).
            rf_daily: Daily risk-free rate.

        Returns:
            Negative Sharpe ratio.
        """
        # Project to simplex
        w = self._constraint_projection(weights)

        # Portfolio returns
        port_returns = self._returns @ w

        if len(port_returns) < 10:
            return float("inf")

        mean_r = float(np.mean(port_returns)) - rf_daily
        std_r = float(np.std(port_returns, ddof=1))

        if std_r < 1e-10:
            return float("inf")

        sharpe = mean_r / std_r * math.sqrt(252.0)

        # Optional custom risk penalty
        if self._risk_fn is not None:
            try:
                risk_penalty = self._risk_fn(w)
                sharpe -= risk_penalty * 0.5  # Blend risk penalty
            except Exception:
                pass

        return -sharpe  # Negate for minimisation

    def _constraint_projection(self, weights: np.ndarray) -> np.ndarray:
        """Project weights onto the probability simplex.

        Ensures: sum(weights) = 1, all weights >= 0,
        and each weight <= max_weight.

        Uses the efficient simplex projection algorithm
        (Duchi et al. 2008).

        Args:
            weights: Raw weight vector.

        Returns:
            Projected weight vector on the simplex.
        """
        n = len(weights)
        w = weights.copy()

        # Clip to [0, max_weight]
        w = np.clip(w, 0.0, self._max_weight)

        # Project to simplex: sum = 1
        total = np.sum(w)
        if total < 1e-10:
            # All zeros — equal weights
            return np.full(n, 1.0 / n)

        if abs(total - 1.0) < 1e-10:
            return w

        # Iterative projection (handles both sum and max constraints)
        for _ in range(50):
            w = np.clip(w, 0.0, self._max_weight)
            total = np.sum(w)
            if total < 1e-10:
                return np.full(n, 1.0 / n)
            w /= total

            # Check if projection is valid
            if np.all(w <= self._max_weight + 1e-8):
                break

        # Final normalisation
        w = np.clip(w, 0.0, self._max_weight)
        total = np.sum(w)
        if total > 1e-10:
            w /= total

        return w


class _SimplexPSO(ParticleSwarmOptimizer):
    """PSO variant that projects positions onto a constraint manifold.

    After each position update, applies a projection function
    (e.g. simplex projection for portfolio weights).
    """

    def __init__(
        self,
        objective_fn: Callable[[np.ndarray], float],
        bounds: Tuple[np.ndarray, np.ndarray],
        config: Optional[PSOConfig] = None,
        projection_fn: Optional[Callable[[np.ndarray], np.ndarray]] = None,
    ) -> None:
        """Initialise simplex-constrained PSO.

        Args:
            objective_fn: Function to minimise.
            bounds: Search space bounds.
            config: PSO configuration.
            projection_fn: Constraint projection function.
        """
        super().__init__(objective_fn, bounds, config)
        self._projection = projection_fn

    def _update_position(self, particle: Particle) -> np.ndarray:
        """Update position with constraint projection."""
        new_position = super()._update_position(particle)

        if self._projection is not None:
            new_position = self._projection(new_position)

        return new_position

    def _initialize_swarm(self) -> None:
        """Initialise swarm with projected positions."""
        super()._initialize_swarm()

        if self._projection is not None:
            for particle in self._particles:
                particle.position = self._projection(particle.position)
                particle.best_position = particle.position.copy()
