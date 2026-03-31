"""Quantum-Inspired Optimization for Trading — Book 159.

Classical implementations of quantum-inspired algorithms for portfolio
allocation, parameter search, and combinatorial optimization. NOT actual
quantum computing — these are classical algorithms that borrow concepts
(superposition, tunnelling, amplitude) to explore solution spaces more
efficiently than traditional methods.

Components:
  - QuantumBit: Probabilistic bit with rotation gates
  - QBEAOptimizer: Quantum-inspired Binary Evolutionary Algorithm
  - QuantumAnnealingSimulator: Simulated annealing with quantum tunnelling
  - QuantumPortfolioOptimizer: QUBO-based portfolio weight optimization

Bridge.py integration:
    try:
        from python_brain.ml.quantum_inspired import (
            QBEAOptimizer, QuantumAnnealingSimulator,
            QuantumPortfolioOptimizer, QuantumBit,
        )
    except ImportError:
        pass

    # Portfolio optimization:
    qpo = QuantumPortfolioOptimizer(seed=42)
    weights = qpo.optimize_weights(returns, cov, n_assets=51)

    # Parameter search via quantum annealing:
    qas = QuantumAnnealingSimulator(seed=42)
    result = qas.optimize(energy_fn=my_fitness, bounds=param_bounds)
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

log = logging.getLogger("quantum_inspired")

__all__ = [
    "QuantumBit",
    "QBEAOptimizer",
    "QuantumAnnealingSimulator",
    "QuantumPortfolioOptimizer",
]

DATA_DIR = "/app/data/quantum_inspired"


# ---------------------------------------------------------------------------
# QuantumBit
# ---------------------------------------------------------------------------

class QuantumBit:
    """Represents a probabilistic quantum bit (qubit) as a pair of amplitudes.

    A qubit is described by two complex amplitudes (alpha, beta) where
    |alpha|^2 + |beta|^2 = 1. Observation collapses the qubit to 0 or 1
    based on these probabilities. Rotation gates adjust the amplitudes.

    For classical simulation we use real-valued amplitudes only.

    Args:
        alpha: Amplitude for state |0>. Default sqrt(0.5) = equal superposition.
        beta: Amplitude for state |1>. Default sqrt(0.5).
    """

    def __init__(
        self,
        alpha: float = math.sqrt(0.5),
        beta: float = math.sqrt(0.5),
    ) -> None:
        self.alpha = alpha
        self.beta = beta
        self._normalise()

    def _normalise(self) -> None:
        """Ensure |alpha|^2 + |beta|^2 = 1."""
        norm = math.sqrt(self.alpha ** 2 + self.beta ** 2)
        if norm < 1e-12:
            self.alpha = math.sqrt(0.5)
            self.beta = math.sqrt(0.5)
        else:
            self.alpha /= norm
            self.beta /= norm

    def observe(self, rng: Optional[np.random.Generator] = None) -> int:
        """Collapse the qubit to 0 or 1 based on probability amplitudes.

        P(0) = |alpha|^2, P(1) = |beta|^2.

        Args:
            rng: Numpy random generator. If None, uses default.

        Returns:
            0 or 1.
        """
        if rng is None:
            rng = np.random.default_rng()
        p_one = self.beta ** 2
        return 1 if rng.random() < p_one else 0

    def rotate(self, angle: float) -> None:
        """Apply a rotation gate to the qubit.

        Rotation matrix:
          [cos(theta)  -sin(theta)]
          [sin(theta)   cos(theta)]

        Args:
            angle: Rotation angle in radians.
        """
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        new_alpha = cos_a * self.alpha - sin_a * self.beta
        new_beta = sin_a * self.alpha + cos_a * self.beta
        self.alpha = new_alpha
        self.beta = new_beta
        self._normalise()

    @property
    def p_zero(self) -> float:
        """Probability of observing 0."""
        return self.alpha ** 2

    @property
    def p_one(self) -> float:
        """Probability of observing 1."""
        return self.beta ** 2

    def __repr__(self) -> str:
        return f"QuantumBit(alpha={self.alpha:.4f}, beta={self.beta:.4f})"


# ---------------------------------------------------------------------------
# QBEAOptimizer — Quantum-Inspired Binary Evolutionary Algorithm
# ---------------------------------------------------------------------------

class QBEAOptimizer:
    """Quantum-inspired Binary Evolutionary Algorithm.

    Each individual in the population is a vector of QuantumBits.
    Solutions are obtained by observing the qubits. The quantum genes
    are rotated toward the best-found solution, enabling superposition-
    like exploration that converges toward good solutions.

    Key advantage over standard GA: the quantum representation
    maintains diversity implicitly — even after many generations, each
    qubit still has some probability of generating either 0 or 1.

    Args:
        n_bits: Number of binary dimensions.
        fitness_fn: Takes a binary array (0/1), returns fitness (higher=better).
        n_pop: Population size.
        n_iter: Number of generations.
        seed: Random seed.
    """

    def __init__(
        self,
        n_bits: int,
        fitness_fn: Callable[[np.ndarray], float],
        n_pop: int = 50,
        n_iter: int = 100,
        seed: Optional[int] = None,
    ) -> None:
        self.n_bits = n_bits
        self.fitness_fn = fitness_fn
        self.n_pop = n_pop
        self.n_iter = n_iter
        self.rng = np.random.default_rng(seed)

        # Population: each individual is a list of QuantumBits
        self.population: List[List[QuantumBit]] = [
            [QuantumBit() for _ in range(n_bits)]
            for _ in range(n_pop)
        ]

        self.best_solution: Optional[np.ndarray] = None
        self.best_fitness: float = -np.inf
        self.history: List[float] = []

    def optimize(self) -> Dict[str, Any]:
        """Run the QBEA optimization.

        Returns:
            Dict with 'best_solution' (binary array), 'best_fitness', 'history'.
        """
        log.info("QBEA starting: %d bits, %d pop, %d iterations", self.n_bits, self.n_pop, self.n_iter)
        t0 = time.time()

        for gen in range(self.n_iter):
            # Observe: collapse each individual's qubits to binary solutions
            solutions = np.zeros((self.n_pop, self.n_bits), dtype=int)
            fitnesses = np.zeros(self.n_pop)

            for i, individual in enumerate(self.population):
                for j, qbit in enumerate(individual):
                    solutions[i, j] = qbit.observe(self.rng)

                try:
                    fitnesses[i] = self.fitness_fn(solutions[i])
                    if not np.isfinite(fitnesses[i]):
                        fitnesses[i] = -1e10
                except Exception as e:
                    log.warning("QBEA fitness eval error at gen %d ind %d: %s", gen, i, e)
                    fitnesses[i] = -1e10

            # Update global best
            gen_best_idx = int(np.argmax(fitnesses))
            if fitnesses[gen_best_idx] > self.best_fitness:
                self.best_fitness = fitnesses[gen_best_idx]
                self.best_solution = solutions[gen_best_idx].copy()

            self.history.append(float(self.best_fitness))

            # Rotate quantum genes toward the best solution
            if self.best_solution is not None:
                for i, individual in enumerate(self.population):
                    self._update_quantum_genes(self.best_solution, individual, solutions[i])

            if (gen + 1) % 20 == 0:
                log.debug(
                    "QBEA gen %d/%d: best_fitness=%.6f",
                    gen + 1, self.n_iter, self.best_fitness,
                )

        elapsed = time.time() - t0
        log.info("QBEA complete in %.1fs: best_fitness=%.6f", elapsed, self.best_fitness)

        return {
            "best_solution": self.best_solution.tolist() if self.best_solution is not None else [],
            "best_fitness": float(self.best_fitness),
            "history": self.history,
            "elapsed_seconds": elapsed,
        }

    def _update_quantum_genes(
        self,
        best: np.ndarray,
        individual: List[QuantumBit],
        observed: np.ndarray,
    ) -> None:
        """Rotate quantum genes toward the best solution.

        The rotation angle is determined by a lookup table based on
        the relationship between the best bit, observed bit, and
        current qubit amplitudes.

        Args:
            best: Best-known binary solution.
            individual: List of QuantumBits for this individual.
            observed: This individual's observed binary solution.
        """
        base_angle = 0.05 * math.pi  # ~9 degrees

        for j, qbit in enumerate(individual):
            if best[j] == observed[j]:
                # Already aligned — small random rotation for exploration
                angle = self.rng.uniform(-0.01, 0.01) * math.pi
            else:
                # Rotate toward the best
                if best[j] == 1:
                    # Want higher P(1) → increase beta
                    if qbit.alpha * qbit.beta > 0:
                        angle = base_angle
                    elif qbit.alpha * qbit.beta < 0:
                        angle = -base_angle
                    elif qbit.alpha == 0:
                        angle = 0.0
                    else:
                        angle = base_angle if self.rng.random() < 0.5 else -base_angle
                else:
                    # Want higher P(0) → increase alpha
                    if qbit.alpha * qbit.beta > 0:
                        angle = -base_angle
                    elif qbit.alpha * qbit.beta < 0:
                        angle = base_angle
                    elif qbit.beta == 0:
                        angle = 0.0
                    else:
                        angle = base_angle if self.rng.random() < 0.5 else -base_angle

            qbit.rotate(angle)


# ---------------------------------------------------------------------------
# QuantumAnnealingSimulator
# ---------------------------------------------------------------------------

class QuantumAnnealingSimulator:
    """Simulated annealing with quantum tunnelling.

    Extends classical simulated annealing by adding a tunnelling
    mechanism that allows the optimizer to pass through energy barriers
    rather than having to climb over them. This is simulated classically
    using path-integral Monte Carlo inspired moves.

    The key difference from classical SA:
      - Classical: only accepts uphill moves probabilistically
      - Quantum: can tunnel through thin barriers regardless of height

    Args:
        n_replicas: Number of Trotter replicas for path-integral simulation.
        initial_temp: Initial temperature.
        final_temp: Final temperature.
        transverse_field: Initial transverse field strength (tunnelling).
        seed: Random seed.
    """

    def __init__(
        self,
        n_replicas: int = 10,
        initial_temp: float = 10.0,
        final_temp: float = 0.01,
        transverse_field: float = 5.0,
        seed: Optional[int] = None,
    ) -> None:
        self.n_replicas = n_replicas
        self.initial_temp = initial_temp
        self.final_temp = final_temp
        self.transverse_field = transverse_field
        self.rng = np.random.default_rng(seed)

    def optimize(
        self,
        energy_fn: Callable[[np.ndarray], float],
        bounds: List[Tuple[float, float]],
        n_iter: int = 1000,
    ) -> Dict[str, Any]:
        """Run quantum-inspired annealing.

        Args:
            energy_fn: Takes parameter vector, returns energy (LOWER=better).
            bounds: List of (lower, upper) bounds per dimension.
            n_iter: Number of annealing steps.

        Returns:
            Dict with 'best_params', 'best_energy', 'history'.
        """
        dim = len(bounds)
        lower = np.array([b[0] for b in bounds])
        upper = np.array([b[1] for b in bounds])
        scale = upper - lower

        log.info(
            "Quantum annealing: %d dims, %d replicas, %d iterations",
            dim, self.n_replicas, n_iter,
        )
        t0 = time.time()

        # Initialise replicas at random positions
        replicas = np.array([
            self.rng.uniform(lower, upper) for _ in range(self.n_replicas)
        ])
        energies = np.array([
            self._safe_energy(energy_fn, r) for r in replicas
        ])

        best_idx = int(np.argmin(energies))
        best_params = replicas[best_idx].copy()
        best_energy = energies[best_idx]
        history: List[float] = [float(best_energy)]

        for step in range(n_iter):
            # Annealing schedule
            progress = step / max(1, n_iter - 1)
            temperature = self.initial_temp * (self.final_temp / self.initial_temp) ** progress
            gamma = self.transverse_field * (1.0 - progress)  # Transverse field decays

            for k in range(self.n_replicas):
                # Classical perturbation
                perturbation = self.rng.normal(0, 1, dim) * scale * 0.05 * (1 - progress)
                candidate = np.clip(replicas[k] + perturbation, lower, upper)
                candidate_energy = self._safe_energy(energy_fn, candidate)

                delta_E = candidate_energy - energies[k]

                # Classical acceptance
                classical_accept = delta_E < 0 or self.rng.random() < math.exp(
                    -delta_E / max(temperature, 1e-10)
                )

                # Quantum tunnelling: additional acceptance probability
                tunnel_prob = self._tunneling_probability(delta_E, temperature, gamma)
                quantum_accept = self.rng.random() < tunnel_prob

                if classical_accept or quantum_accept:
                    replicas[k] = candidate
                    energies[k] = candidate_energy

                # Inter-replica coupling (path-integral interaction)
                if self.n_replicas > 1 and gamma > 0.01:
                    k_prev = (k - 1) % self.n_replicas
                    k_next = (k + 1) % self.n_replicas
                    coupling = -0.5 * gamma * (
                        np.sum((replicas[k] - replicas[k_prev]) ** 2)
                        + np.sum((replicas[k] - replicas[k_next]) ** 2)
                    )
                    # Slight pull toward neighbouring replicas
                    replica_pull = 0.01 * gamma * (
                        (replicas[k_prev] - replicas[k])
                        + (replicas[k_next] - replicas[k])
                    )
                    replicas[k] = np.clip(replicas[k] + replica_pull, lower, upper)
                    energies[k] = self._safe_energy(energy_fn, replicas[k])

            # Track best across all replicas
            step_best_idx = int(np.argmin(energies))
            if energies[step_best_idx] < best_energy:
                best_energy = energies[step_best_idx]
                best_params = replicas[step_best_idx].copy()

            history.append(float(best_energy))

            if (step + 1) % 200 == 0:
                log.debug(
                    "QA step %d/%d: best_energy=%.6f temp=%.4f gamma=%.4f",
                    step + 1, n_iter, best_energy, temperature, gamma,
                )

        elapsed = time.time() - t0
        log.info("Quantum annealing complete in %.1fs: best_energy=%.6f", elapsed, best_energy)

        return {
            "best_params": best_params.tolist(),
            "best_energy": float(best_energy),
            "history": history,
            "elapsed_seconds": elapsed,
        }

    def _tunneling_probability(
        self,
        delta_E: float,
        temperature: float,
        gamma: float,
    ) -> float:
        """Compute quantum tunnelling probability.

        The tunnelling probability depends on the barrier height (delta_E),
        temperature, and transverse field strength (gamma). Higher gamma
        = more tunnelling.

        Args:
            delta_E: Energy difference (positive = uphill).
            temperature: Current temperature.
            gamma: Current transverse field strength.

        Returns:
            Probability of tunnelling (0 to 1).
        """
        if delta_E <= 0:
            return 1.0  # Downhill always accepted

        if gamma < 1e-10:
            return 0.0  # No tunnelling field

        # Quantum tunnelling: probability depends on barrier width (approx proportional
        # to delta_E) and gamma (inversely proportional to barrier)
        # P_tunnel ~ exp(-delta_E / gamma)
        exponent = -delta_E / max(gamma, 1e-10)
        if exponent < -50:
            return 0.0
        return math.exp(exponent)

    @staticmethod
    def _safe_energy(
        energy_fn: Callable[[np.ndarray], float],
        params: np.ndarray,
    ) -> float:
        """Evaluate energy with error handling."""
        try:
            result = energy_fn(params)
            if not np.isfinite(result):
                return 1e10
            return float(result)
        except Exception as e:
            log.warning("Energy eval error: %s", e)
            return 1e10


# ---------------------------------------------------------------------------
# QuantumPortfolioOptimizer
# ---------------------------------------------------------------------------

class QuantumPortfolioOptimizer:
    """Quantum-inspired portfolio weight optimization.

    Formulates the Markowitz mean-variance optimization as a QUBO
    (Quadratic Unconstrained Binary Optimization) problem and solves
    it using quantum-inspired methods on classical hardware.

    The binary encoding discretises weights into K levels per asset.
    E.g., K=8 means weights of 0/8, 1/8, ..., 8/8 (normalised).

    Args:
        risk_aversion: Lambda parameter for risk penalty.
        n_bits_per_asset: Binary precision for weight encoding.
        seed: Random seed.
    """

    def __init__(
        self,
        risk_aversion: float = 1.0,
        n_bits_per_asset: int = 4,
        seed: Optional[int] = None,
    ) -> None:
        self.risk_aversion = risk_aversion
        self.n_bits_per_asset = n_bits_per_asset
        self.seed = seed
        self.rng = np.random.default_rng(seed)

    def optimize_weights(
        self,
        returns: np.ndarray,
        cov: np.ndarray,
        n_assets: int,
        n_iter: int = 200,
        n_pop: int = 40,
    ) -> np.ndarray:
        """Optimize portfolio weights using quantum-inspired binary search.

        Args:
            returns: Expected returns vector, shape (n_assets,).
            cov: Covariance matrix, shape (n_assets, n_assets).
            n_assets: Number of assets.
            n_iter: Optimization iterations.
            n_pop: Population size for QBEA.

        Returns:
            Optimal weight vector, shape (n_assets,), sums to 1.0.
        """
        log.info(
            "Quantum portfolio optimization: %d assets, %d bits/asset, %d iterations",
            n_assets, self.n_bits_per_asset, n_iter,
        )

        n_bits = n_assets * self.n_bits_per_asset

        def fitness_fn(binary_solution: np.ndarray) -> float:
            """Convert binary to weights and evaluate portfolio fitness."""
            weights = self._binary_to_weights(binary_solution, n_assets)
            portfolio_return = float(np.dot(weights, returns))
            portfolio_risk = float(weights @ cov @ weights)

            # Fitness = return - risk_aversion * risk
            fitness = portfolio_return - self.risk_aversion * portfolio_risk

            # Penalty for extreme concentration
            max_weight = float(np.max(weights))
            if max_weight > 0.25:
                fitness -= 10.0 * (max_weight - 0.25) ** 2

            # Penalty for too few assets (diversification)
            n_active = np.sum(weights > 0.01)
            if n_active < min(5, n_assets):
                fitness -= 5.0 * (min(5, n_assets) - n_active) ** 2

            return fitness

        # Run QBEA
        qbea = QBEAOptimizer(
            n_bits=n_bits,
            fitness_fn=fitness_fn,
            n_pop=n_pop,
            n_iter=n_iter,
            seed=self.seed,
        )
        result = qbea.optimize()

        # Convert best solution to weights
        best_binary = np.array(result["best_solution"])
        weights = self._binary_to_weights(best_binary, n_assets)

        # Also try quantum annealing on the continuous relaxation
        qa_weights = self._continuous_refinement(weights, returns, cov, n_assets)

        # Take the better solution
        qbea_fitness = self._portfolio_fitness(weights, returns, cov)
        qa_fitness = self._portfolio_fitness(qa_weights, returns, cov)

        if qa_fitness > qbea_fitness:
            weights = qa_weights
            log.debug("QA refinement improved on QBEA: %.6f > %.6f", qa_fitness, qbea_fitness)

        log.info(
            "Portfolio optimized: %d active assets, max_weight=%.3f, "
            "expected_return=%.4f, expected_risk=%.4f",
            int(np.sum(weights > 0.01)),
            float(np.max(weights)),
            float(np.dot(weights, returns)),
            float(weights @ cov @ weights),
        )

        return weights

    def _binary_to_weights(
        self,
        binary: np.ndarray,
        n_assets: int,
    ) -> np.ndarray:
        """Convert binary solution to portfolio weights.

        Each asset's weight is encoded by n_bits_per_asset binary digits.
        The resulting integer is normalised to sum to 1.

        Args:
            binary: Binary array of length n_assets * n_bits_per_asset.
            n_assets: Number of assets.

        Returns:
            Weight vector summing to 1.0.
        """
        raw_weights = np.zeros(n_assets)
        for i in range(n_assets):
            start = i * self.n_bits_per_asset
            end = start + self.n_bits_per_asset
            bits = binary[start:end]
            # Binary to integer
            value = 0
            for b in range(len(bits)):
                value += int(bits[b]) * (2 ** b)
            raw_weights[i] = value

        # Normalise to simplex
        total = raw_weights.sum()
        if total < 1e-10:
            return np.ones(n_assets) / n_assets
        return raw_weights / total

    def _continuous_refinement(
        self,
        initial_weights: np.ndarray,
        returns: np.ndarray,
        cov: np.ndarray,
        n_assets: int,
    ) -> np.ndarray:
        """Refine weights using quantum annealing on continuous space.

        Starts from the QBEA solution and uses quantum annealing to
        fine-tune in continuous space.

        Args:
            initial_weights: Starting weights from QBEA.
            returns: Expected returns.
            cov: Covariance matrix.
            n_assets: Number of assets.

        Returns:
            Refined weight vector.
        """
        # Energy function: negative fitness (lower = better for annealing)
        def energy_fn(w_raw: np.ndarray) -> float:
            # Project onto simplex
            w = np.maximum(w_raw, 0)
            total = w.sum()
            if total < 1e-10:
                return 1e10
            w = w / total
            return -(float(np.dot(w, returns)) - self.risk_aversion * float(w @ cov @ w))

        bounds = [(0.0, 0.5) for _ in range(n_assets)]

        qa = QuantumAnnealingSimulator(
            n_replicas=5,
            initial_temp=5.0,
            final_temp=0.001,
            transverse_field=2.0,
            seed=self.seed + 100 if self.seed else None,
        )

        result = qa.optimize(
            energy_fn=energy_fn,
            bounds=bounds,
            n_iter=300,
        )

        # Project result onto simplex
        raw_w = np.array(result["best_params"])
        raw_w = np.maximum(raw_w, 0)
        total = raw_w.sum()
        if total < 1e-10:
            return initial_weights
        return raw_w / total

    def _portfolio_fitness(
        self,
        weights: np.ndarray,
        returns: np.ndarray,
        cov: np.ndarray,
    ) -> float:
        """Evaluate portfolio fitness (higher = better)."""
        port_return = float(np.dot(weights, returns))
        port_risk = float(weights @ cov @ weights)
        return port_return - self.risk_aversion * port_risk

    def save_result(
        self,
        weights: np.ndarray,
        metadata: Optional[Dict[str, Any]] = None,
        filepath: Optional[str] = None,
    ) -> None:
        """Persist optimal weights to JSON."""
        filepath = filepath or os.path.join(DATA_DIR, "portfolio_weights.json")
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        result = {
            "weights": weights.tolist(),
            "n_assets": len(weights),
            "n_active": int(np.sum(weights > 0.01)),
            "max_weight": float(np.max(weights)),
            "timestamp": time.time(),
        }
        if metadata:
            result["metadata"] = metadata

        try:
            with open(filepath, "w") as f:
                json.dump(result, f, indent=2)
            log.info("Portfolio weights saved to %s", filepath)
        except OSError as e:
            log.error("Failed to save weights: %s", e)
