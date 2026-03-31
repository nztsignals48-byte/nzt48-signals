"""Risk-Constrained PPO for Safe RL Trading — Book 213.

TWO COMPONENTS:

1. ConstrainedPPOAgent (original, numpy-based):
   Standard PPO with drawdown-aware risk shaping for discrete action
   selection (HOLD/BUY/SELL). Requires numpy. Used for RL-based trade
   decisions.

2. PPOParamOptimizer (new, pure-stdlib):
   Shadow-mode evolutionary parameter optimizer. Proposes adjustments to
   ~8 key AEGIS parameters (chandelier ATR mult, kelly fraction, confidence
   floor, heat limits, etc.) based on trade outcomes. Uses constrained
   evolutionary strategy with memory — no numpy/pytorch required.

   SHADOW MODE: The optimizer observes but does NOT control live parameters.
   It proposes adjustments that the nightly pipeline can compare against
   actual performance. Once validated over enough cycles, the operator can
   choose to enable its suggestions through the approval_gate.

Architecture (PPOParamOptimizer):
  State: 10-dimensional [regime_code, win_rate_7d, win_rate_30d, pnl_7d,
         pnl_30d, max_drawdown_7d, avg_confidence, trade_count_7d,
         sharpe_7d, volatility_7d]
  Action: proposed deltas for 8 parameters (continuous, bounded)
  Reward: next-day P&L (normalized) with drawdown penalty
  Optimization: (mu+lambda) evolutionary strategy with elitism, adaptive
                mutation, and constraint projection

State persisted to /app/data/ppo_agent_state.json.

Usage:
    from python_brain.ml.constrained_ppo import (
        PPOParamOptimizer, run_nightly_ppo,
    )
    optimizer = PPOParamOptimizer()
    optimizer.load_state()
    proposals = optimizer.propose_adjustments(current_params, recent_metrics)
    optimizer.record_outcome(proposals, actual_pnl=-12.50, drawdown_pct=0.03)
    comparison = optimizer.get_shadow_comparison()
    optimizer.save_state()

    # Nightly integration:
    result = run_nightly_ppo(metrics_dict, recommendations_dict)
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("constrained_ppo")

# ── Constants ──────────────────────────────────────────────────────────

STATE_DIR = Path("/app/data/ppo")
SHADOW_STATE_FILE = Path("/app/data/ppo_agent_state.json")

# PPO RL agent constants (used by ConstrainedPPOAgent)
ENTROPY_COEFF = 0.01
VALUE_LOSS_COEFF = 0.5
MAX_GRAD_NORM = 0.5
PPO_EPOCHS = 4

# Evolutionary strategy constants (used by PPOParamOptimizer)
ES_POPULATION_SIZE = 20       # Lambda: offspring per generation
ES_ELITE_COUNT = 5            # Mu: parents selected per generation
ES_MUTATION_RATE = 0.15       # Base mutation sigma (fraction of range)
ES_MUTATION_DECAY = 0.995     # Sigma decay per generation for convergence
ES_MIN_MUTATION = 0.02        # Minimum mutation sigma
ES_MAX_HISTORY = 200          # Max outcome records to retain
ES_STATE_DIM = 10             # State vector dimension

# ── Parameter Bounds ──────────────────────────────────────────────────
# Matches approval_gate.py HARD_BOUNDS and ouroboros_challenger.py PARAM_BOUNDS.
# These are the ABSOLUTE bounds. Per-cycle max-change is enforced separately.

PARAM_BOUNDS: Dict[str, Dict[str, float]] = {
    "kelly_fraction": {
        "min": 0.10, "max": 0.35,
        "max_delta_pct": 10.0,    # Max 10% change per cycle
        "default": 0.20,
    },
    "chandelier_atr_mult": {
        "min": 1.5, "max": 5.0,
        "max_delta_pct": 15.0,    # Max 15% per cycle
        "default": 3.0,
    },
    "confidence_floor": {
        "min": 50.0, "max": 85.0,
        "max_delta_abs": 10.0,    # Max 10 points per cycle
        "default": 60.0,
    },
    "spread_veto_pct": {
        "min": 0.10, "max": 0.80,
        "max_delta_abs": 0.10,    # Max 0.10 per cycle
        "default": 0.30,
    },
    "system_velocity_max": {
        "min": 5.0, "max": 20.0,
        "max_delta_abs": 5.0,     # Max 5 per cycle
        "default": 10.0,
    },
    "heat_limit_pct": {
        "min": 5.0, "max": 25.0,
        "max_delta_pct": 15.0,
        "default": 15.0,
    },
    "max_positions": {
        "min": 2.0, "max": 10.0,
        "max_delta_abs": 2.0,
        "default": 6.0,
    },
    "drawdown_halt_pct": {
        "min": 3.0, "max": 10.0,
        "max_delta_abs": 1.0,
        "default": 8.0,
    },
}

PARAM_NAMES = list(PARAM_BOUNDS.keys())
N_PARAMS = len(PARAM_NAMES)

# Regime encoding: map regime names to numeric codes
REGIME_CODES: Dict[str, float] = {
    "trending_up": 1.0,
    "trending_down": -1.0,
    "mean_reverting": 0.5,
    "choppy": -0.5,
    "low_vol": 0.3,
    "high_vol": -0.3,
    "unknown": 0.0,
}


# ╔═══════════════════════════════════════════════════════════════════════╗
# ║  PART 1: Pure-stdlib PPO Parameter Optimizer (Book 213 Shadow Mode) ║
# ╚═══════════════════════════════════════════════════════════════════════╝

def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value to [lo, hi]."""
    return max(lo, min(hi, value))


def _project_to_bounds(params: Dict[str, float],
                       current: Dict[str, float]) -> Dict[str, float]:
    """Project proposed params to feasible region (absolute + per-cycle bounds).

    Enforces:
      1. Absolute bounds from PARAM_BOUNDS
      2. Per-cycle max-change (either pct or abs)
    """
    projected: Dict[str, float] = {}
    for name in PARAM_NAMES:
        bounds = PARAM_BOUNDS[name]
        proposed = params.get(name, bounds["default"])
        cur = current.get(name, bounds["default"])

        # Absolute bounds
        proposed = _clamp(proposed, bounds["min"], bounds["max"])

        # Per-cycle change limit
        max_delta_pct = bounds.get("max_delta_pct")
        max_delta_abs = bounds.get("max_delta_abs")

        if max_delta_pct is not None and cur > 0:
            max_change = cur * max_delta_pct / 100.0
            proposed = _clamp(proposed, cur - max_change, cur + max_change)
        elif max_delta_abs is not None:
            proposed = _clamp(proposed, cur - max_delta_abs, cur + max_delta_abs)

        # Re-clamp to absolute bounds after delta constraint
        proposed = _clamp(proposed, bounds["min"], bounds["max"])
        projected[name] = round(proposed, 6)

    return projected


def _normalize_param(name: str, value: float) -> float:
    """Normalize param to [0, 1] range using its bounds."""
    bounds = PARAM_BOUNDS[name]
    span = bounds["max"] - bounds["min"]
    if span <= 0:
        return 0.5
    return (value - bounds["min"]) / span


def _denormalize_param(name: str, norm_value: float) -> float:
    """Denormalize from [0, 1] back to param range."""
    bounds = PARAM_BOUNDS[name]
    return bounds["min"] + norm_value * (bounds["max"] - bounds["min"])


def _build_state_vector(metrics: Dict[str, Any]) -> List[float]:
    """Build 10-dimensional state vector from metrics dict.

    State dimensions:
      0: regime_code       (-1.0 to 1.0)
      1: win_rate_7d       (0.0 to 1.0)
      2: win_rate_30d      (0.0 to 1.0)
      3: pnl_7d            (normalized, clipped to [-3, 3])
      4: pnl_30d           (normalized, clipped to [-3, 3])
      5: max_drawdown_7d   (0.0 to 1.0)
      6: avg_confidence     (0.0 to 1.0, scaled from 0-100)
      7: trade_count_7d    (normalized, /20 clipped to [0, 3])
      8: sharpe_7d         (clipped to [-3, 3])
      9: volatility_7d     (0.0 to 1.0, scaled)
    """
    regime_name = str(metrics.get("regime", "unknown")).lower()
    regime_code = REGIME_CODES.get(regime_name, 0.0)

    wr_7d = float(metrics.get("win_rate_7d", metrics.get("win_rate", 0.5)))
    wr_30d = float(metrics.get("win_rate_30d", metrics.get("win_rate", 0.5)))

    # Normalize P&L: use pnl / max(abs(pnl_30d), 100) to get reasonable scale
    pnl_7d_raw = float(metrics.get("pnl_7d", metrics.get("total_pnl", 0.0)))
    pnl_30d_raw = float(metrics.get("pnl_30d", metrics.get("total_pnl", 0.0)))
    pnl_scale = max(abs(pnl_30d_raw), 100.0)
    pnl_7d = _clamp(pnl_7d_raw / pnl_scale, -3.0, 3.0)
    pnl_30d = _clamp(pnl_30d_raw / pnl_scale, -3.0, 3.0)

    dd_7d = _clamp(float(metrics.get("max_drawdown_7d",
                                      metrics.get("max_drawdown", 0.0))), 0.0, 1.0)
    avg_conf = _clamp(float(metrics.get("avg_confidence", 65.0)) / 100.0, 0.0, 1.0)
    trade_count = _clamp(float(metrics.get("trade_count_7d",
                                           metrics.get("total_trades", 0))) / 20.0,
                         0.0, 3.0)
    sharpe = _clamp(float(metrics.get("sharpe_7d", metrics.get("sharpe", 0.0))),
                    -3.0, 3.0)
    vol = _clamp(float(metrics.get("volatility_7d",
                                   metrics.get("volatility", 0.1))), 0.0, 1.0)

    return [regime_code, wr_7d, wr_30d, pnl_7d, pnl_30d,
            dd_7d, avg_conf, trade_count, sharpe, vol]


def _compute_reward(pnl: float, drawdown_pct: float,
                    pnl_normalizer: float = 100.0) -> float:
    """Compute reward for a single outcome.

    reward = normalized_pnl - drawdown_penalty

    Drawdown penalty is quadratic above 4% threshold (half the 8% ISA limit).
    """
    norm_pnl = pnl / max(abs(pnl_normalizer), 50.0)
    norm_pnl = _clamp(norm_pnl, -5.0, 5.0)

    # Drawdown penalty: quadratic above 4%, extra penalty above 8%
    dd_penalty = 0.0
    if drawdown_pct > 0.04:
        excess = drawdown_pct - 0.04
        dd_penalty = 10.0 * excess * excess
    if drawdown_pct > 0.08:
        extra = drawdown_pct - 0.08
        dd_penalty += 20.0 * extra

    return norm_pnl - dd_penalty


@dataclass
class _Individual:
    """A candidate parameter set in the evolutionary population."""
    params_norm: List[float]  # Normalized [0,1] for each of N_PARAMS
    fitness: float = float("-inf")
    age: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "params_norm": self.params_norm[:],
            "fitness": self.fitness,
            "age": self.age,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "_Individual":
        return cls(
            params_norm=d["params_norm"][:],
            fitness=d.get("fitness", float("-inf")),
            age=d.get("age", 0),
        )


class PPOParamOptimizer:
    """Shadow-mode evolutionary parameter optimizer for AEGIS.

    Uses a (mu+lambda) evolutionary strategy to search the parameter space.
    Operates in shadow mode: proposes but never applies. Tracks proposed vs
    actual outcomes to build a performance comparison over time.

    The name 'PPO' is retained for Book 213 consistency, but the underlying
    algorithm is a constrained evolutionary strategy (gradient-free) so it
    works without numpy/pytorch.

    Lifecycle:
      1. load_state() — restore from /app/data/ppo_agent_state.json
      2. propose_adjustments(current_params, metrics) — generate proposals
      3. record_outcome(proposed, actual_pnl, drawdown) — learn from results
      4. get_shadow_comparison() — compare proposed vs actual over time
      5. save_state() — persist to disk
    """

    def __init__(self, seed: Optional[int] = None):
        """Initialize the PPO parameter optimizer.

        Args:
            seed: Random seed. If None, uses system entropy.
        """
        self._rng = random.Random(seed)
        self._generation: int = 0
        self._mutation_sigma: float = ES_MUTATION_RATE
        self._population: List[_Individual] = []
        self._elite: List[_Individual] = []

        # History of proposals and outcomes for learning + comparison
        self._outcome_history: List[Dict[str, Any]] = []

        # Running stats for shadow comparison
        self._proposed_cumulative_pnl: float = 0.0
        self._actual_cumulative_pnl: float = 0.0
        self._n_comparisons: int = 0

        # Last state vector seen (for state-conditioned mutation)
        self._last_state: Optional[List[float]] = None

        # Best individual ever seen
        self._best_ever: Optional[_Individual] = None
        self._best_ever_reward: float = float("-inf")

        # Initialize population
        self._init_population()

        log.info("PPOParamOptimizer initialized: pop=%d, elite=%d, sigma=%.3f, "
                 "params=%d", ES_POPULATION_SIZE, ES_ELITE_COUNT,
                 self._mutation_sigma, N_PARAMS)

    def _init_population(self) -> None:
        """Initialize population with diverse candidates near defaults."""
        self._population = []
        for i in range(ES_POPULATION_SIZE):
            if i == 0:
                # First individual: use defaults
                params_norm = [
                    _normalize_param(name, PARAM_BOUNDS[name]["default"])
                    for name in PARAM_NAMES
                ]
            else:
                # Random perturbation around defaults
                params_norm = []
                for name in PARAM_NAMES:
                    default_norm = _normalize_param(name, PARAM_BOUNDS[name]["default"])
                    noise = self._rng.gauss(0, 0.15)
                    params_norm.append(_clamp(default_norm + noise, 0.0, 1.0))
            self._population.append(_Individual(params_norm=params_norm))

    def _denormalize_individual(self, ind: _Individual) -> Dict[str, float]:
        """Convert normalized individual to actual parameter dict."""
        result: Dict[str, float] = {}
        for i, name in enumerate(PARAM_NAMES):
            result[name] = round(_denormalize_param(name, ind.params_norm[i]), 6)
        return result

    def _normalize_params(self, params: Dict[str, float]) -> List[float]:
        """Convert actual parameter dict to normalized list."""
        result: List[float] = []
        for name in PARAM_NAMES:
            val = params.get(name, PARAM_BOUNDS[name]["default"])
            result.append(_normalize_param(name, val))
        return result

    def _mutate(self, parent: _Individual,
                state: Optional[List[float]] = None) -> _Individual:
        """Create offspring by mutating a parent.

        Uses Gaussian mutation with adaptive sigma. State-conditioned:
        in high-drawdown states, reduce mutation magnitude (conservative).
        In low-vol states, allow larger exploration.
        """
        sigma = self._mutation_sigma

        # State-conditioned sigma adjustment
        if state is not None and len(state) >= ES_STATE_DIM:
            drawdown = state[5]   # dd_7d in [0, 1]
            volatility = state[9]  # vol_7d in [0, 1]

            # High drawdown -> reduce exploration (be conservative)
            if drawdown > 0.05:
                sigma *= max(0.3, 1.0 - drawdown * 5.0)

            # Low volatility -> allow more exploration
            if volatility < 0.1:
                sigma *= 1.3

        child_norm: List[float] = []
        for i in range(N_PARAMS):
            noise = self._rng.gauss(0, sigma)
            val = _clamp(parent.params_norm[i] + noise, 0.0, 1.0)
            child_norm.append(val)

        return _Individual(params_norm=child_norm, age=0)

    def _crossover(self, parent_a: _Individual,
                   parent_b: _Individual) -> _Individual:
        """Uniform crossover between two parents."""
        child_norm: List[float] = []
        for i in range(N_PARAMS):
            if self._rng.random() < 0.5:
                child_norm.append(parent_a.params_norm[i])
            else:
                child_norm.append(parent_b.params_norm[i])
        return _Individual(params_norm=child_norm, age=0)

    def _evaluate_fitness(self, individual: _Individual,
                          recent_outcomes: List[Dict[str, Any]]) -> float:
        """Estimate fitness from historical outcomes with similar parameters.

        Uses a kernel-weighted average of rewards from past outcomes that
        had similar proposed parameters. More recent outcomes get higher weight.
        """
        if not recent_outcomes:
            return 0.0

        ind_params = individual.params_norm
        total_weight = 0.0
        weighted_reward = 0.0

        for idx, outcome in enumerate(recent_outcomes):
            proposed_norm = outcome.get("proposed_norm", [])
            if len(proposed_norm) != N_PARAMS:
                continue

            # Euclidean distance in normalized space
            dist_sq = sum(
                (a - b) ** 2
                for a, b in zip(ind_params, proposed_norm)
            )
            dist = math.sqrt(dist_sq)

            # Gaussian kernel: closer params get higher weight
            kernel_weight = math.exp(-dist_sq / (2.0 * 0.1 ** 2))

            # Recency weight: newer outcomes are more relevant
            recency = (idx + 1) / len(recent_outcomes)
            weight = kernel_weight * recency

            reward = outcome.get("reward", 0.0)
            weighted_reward += weight * reward
            total_weight += weight

        if total_weight < 1e-10:
            return 0.0

        return weighted_reward / total_weight

    def _evolve_generation(self, state: Optional[List[float]] = None) -> None:
        """Run one generation of (mu+lambda) ES.

        1. Evaluate fitness of all individuals using outcome history
        2. Select top-mu as elite
        3. Generate lambda offspring via mutation + crossover
        4. Merge elite + offspring for next generation
        """
        # Evaluate fitness
        recent = self._outcome_history[-50:] if self._outcome_history else []
        for ind in self._population:
            ind.fitness = self._evaluate_fitness(ind, recent)
            ind.age += 1

        # Sort by fitness (descending)
        self._population.sort(key=lambda x: x.fitness, reverse=True)

        # Select elite
        self._elite = [copy.deepcopy(ind) for ind in
                       self._population[:ES_ELITE_COUNT]]

        # Track best ever
        if self._elite and self._elite[0].fitness > self._best_ever_reward:
            self._best_ever = copy.deepcopy(self._elite[0])
            self._best_ever_reward = self._elite[0].fitness

        # Generate offspring
        offspring: List[_Individual] = []
        while len(offspring) < ES_POPULATION_SIZE - ES_ELITE_COUNT:
            parent_idx = self._rng.randint(0, len(self._elite) - 1)
            parent = self._elite[parent_idx]

            if self._rng.random() < 0.7:
                # Mutation
                child = self._mutate(parent, state)
            else:
                # Crossover + mutation
                other_idx = self._rng.randint(0, len(self._elite) - 1)
                child = self._crossover(parent, self._elite[other_idx])
                child = self._mutate(child, state)

            offspring.append(child)

        # Next generation: elite + offspring
        self._population = list(self._elite) + offspring

        # Decay mutation rate
        self._mutation_sigma = max(
            ES_MIN_MUTATION,
            self._mutation_sigma * ES_MUTATION_DECAY,
        )
        self._generation += 1

        log.debug("ES generation %d: best_fitness=%.4f, sigma=%.4f, "
                  "elite_avg=%.4f",
                  self._generation,
                  self._elite[0].fitness if self._elite else 0.0,
                  self._mutation_sigma,
                  sum(e.fitness for e in self._elite) / max(len(self._elite), 1))

    def propose_adjustments(self, current_params: Dict[str, float],
                            recent_metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Propose parameter adjustments in shadow mode.

        Runs one ES generation, selects the best individual, projects to
        feasible region (absolute + per-cycle bounds), and returns the
        proposed parameter set as deltas from current.

        Args:
            current_params: Current live parameter values dict. Keys should
                match PARAM_NAMES. Missing keys use defaults.
            recent_metrics: Performance metrics dict with keys like
                win_rate, total_pnl, max_drawdown, regime, etc.

        Returns:
            Dict with keys:
              - proposed_params: absolute proposed values
              - deltas: proposed changes from current
              - state_vector: the 10-dim state used
              - generation: current ES generation
              - best_fitness: fitness of the proposal
              - shadow_mode: always True
        """
        # Build state vector
        state = _build_state_vector(recent_metrics)
        self._last_state = state

        # Fill in defaults for missing current params
        current_full: Dict[str, float] = {}
        for name in PARAM_NAMES:
            current_full[name] = float(
                current_params.get(name, PARAM_BOUNDS[name]["default"])
            )

        # Evolve population
        self._evolve_generation(state)

        # Select best individual
        best = self._elite[0] if self._elite else self._population[0]
        raw_proposed = self._denormalize_individual(best)

        # Project to feasible region (absolute + per-cycle)
        proposed = _project_to_bounds(raw_proposed, current_full)

        # Compute deltas
        deltas: Dict[str, float] = {}
        for name in PARAM_NAMES:
            delta = proposed[name] - current_full[name]
            deltas[name] = round(delta, 6)

        result = {
            "proposed_params": proposed,
            "deltas": deltas,
            "current_params": current_full,
            "state_vector": state,
            "generation": self._generation,
            "best_fitness": round(best.fitness, 6),
            "mutation_sigma": round(self._mutation_sigma, 6),
            "shadow_mode": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        log.info("PPO shadow proposal (gen %d): fitness=%.4f, sigma=%.4f, "
                 "deltas=%s",
                 self._generation, best.fitness, self._mutation_sigma,
                 {k: f"{v:+.4f}" for k, v in deltas.items() if abs(v) > 1e-6})

        return result

    def record_outcome(self, proposed_params: Dict[str, float],
                       actual_pnl: float,
                       drawdown_pct: float = 0.0,
                       actual_params: Optional[Dict[str, float]] = None,
                       actual_pnl_with_proposals: Optional[float] = None) -> None:
        """Record the outcome of a day's trading for learning.

        Call this the day AFTER propose_adjustments() with the actual P&L
        that occurred. This builds the reward history used to evaluate
        fitness of future candidates.

        Args:
            proposed_params: The proposed param dict from propose_adjustments().
            actual_pnl: Actual P&L for the day (GBP).
            drawdown_pct: Max drawdown during the day (fraction, e.g. 0.05=5%).
            actual_params: The params that were actually used (live values).
                If None, assumed same as proposed (for fitness estimation).
            actual_pnl_with_proposals: Hypothetical P&L if proposals had been
                used. If None, not available (shadow mode can't know this).
        """
        reward = _compute_reward(actual_pnl, drawdown_pct)

        proposed_norm = self._normalize_params(proposed_params)

        record: Dict[str, Any] = {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "proposed_params": {k: round(v, 6) for k, v in proposed_params.items()},
            "proposed_norm": proposed_norm,
            "actual_pnl": round(actual_pnl, 2),
            "drawdown_pct": round(drawdown_pct, 6),
            "reward": round(reward, 6),
            "generation": self._generation,
            "state": self._last_state[:] if self._last_state else [],
        }

        if actual_params is not None:
            record["actual_params"] = {k: round(v, 6) for k, v in actual_params.items()}

        if actual_pnl_with_proposals is not None:
            record["actual_pnl_with_proposals"] = round(actual_pnl_with_proposals, 2)

        self._outcome_history.append(record)

        # Track cumulative comparison
        self._actual_cumulative_pnl += actual_pnl
        if actual_pnl_with_proposals is not None:
            self._proposed_cumulative_pnl += actual_pnl_with_proposals
        self._n_comparisons += 1

        # Prune old history
        if len(self._outcome_history) > ES_MAX_HISTORY:
            self._outcome_history = self._outcome_history[-ES_MAX_HISTORY:]

        log.info("PPO outcome recorded: pnl=%.2f, dd=%.2f%%, reward=%.4f, "
                 "history_len=%d",
                 actual_pnl, drawdown_pct * 100, reward,
                 len(self._outcome_history))

    def get_shadow_comparison(self) -> Dict[str, Any]:
        """Get shadow mode performance comparison.

        Returns a dict comparing proposed-parameter performance against
        actual (live) performance. This is the key output for evaluating
        whether the PPO optimizer would have improved results.

        Returns:
            Dict with comparison metrics, recent proposals, and fitness stats.
        """
        if not self._outcome_history:
            return {
                "status": "no_data",
                "message": "No outcomes recorded yet. Need at least 1 day of data.",
                "n_comparisons": 0,
                "shadow_mode": True,
            }

        # Recent outcome stats
        recent = self._outcome_history[-30:]
        all_rewards = [r["reward"] for r in recent]
        all_pnl = [r["actual_pnl"] for r in recent]

        avg_reward = sum(all_rewards) / len(all_rewards)
        avg_pnl = sum(all_pnl) / len(all_pnl)

        # Best/worst days
        best_day = max(recent, key=lambda x: x["reward"])
        worst_day = min(recent, key=lambda x: x["reward"])

        # Parameter stability: how much are proposals changing?
        if len(recent) >= 2:
            param_drifts: Dict[str, float] = {}
            for name in PARAM_NAMES:
                vals = [r["proposed_params"].get(name, 0) for r in recent]
                if len(vals) >= 2:
                    mean_val = sum(vals) / len(vals)
                    variance = sum((v - mean_val) ** 2 for v in vals) / len(vals)
                    param_drifts[name] = round(math.sqrt(variance), 6)
            stability_score = 1.0 - min(
                1.0,
                sum(param_drifts.values()) / max(len(param_drifts), 1),
            )
        else:
            param_drifts = {}
            stability_score = 1.0

        # Compute proposed-vs-actual edge (if we have the data)
        proposed_edge = None
        records_with_both = [
            r for r in self._outcome_history
            if "actual_pnl_with_proposals" in r
        ]
        if records_with_both:
            proposed_total = sum(r["actual_pnl_with_proposals"]
                                for r in records_with_both)
            actual_total = sum(r["actual_pnl"] for r in records_with_both)
            proposed_edge = round(proposed_total - actual_total, 2)

        # Last proposal details
        last = self._outcome_history[-1]

        result: Dict[str, Any] = {
            "status": "active",
            "shadow_mode": True,
            "n_comparisons": self._n_comparisons,
            "n_outcomes_total": len(self._outcome_history),
            "generation": self._generation,
            "mutation_sigma": round(self._mutation_sigma, 6),
            "recent_30d": {
                "avg_reward": round(avg_reward, 4),
                "avg_pnl": round(avg_pnl, 2),
                "total_pnl": round(sum(all_pnl), 2),
                "n_days": len(recent),
                "best_day_reward": round(best_day["reward"], 4),
                "worst_day_reward": round(worst_day["reward"], 4),
            },
            "cumulative": {
                "actual_pnl": round(self._actual_cumulative_pnl, 2),
                "proposed_pnl": round(self._proposed_cumulative_pnl, 2)
                    if self._proposed_cumulative_pnl != 0.0 else None,
                "proposed_edge": proposed_edge,
            },
            "stability": {
                "score": round(stability_score, 4),
                "param_stddev": param_drifts,
            },
            "last_proposal": {
                "date": last.get("date"),
                "proposed_params": last.get("proposed_params"),
                "actual_pnl": last.get("actual_pnl"),
                "reward": last.get("reward"),
            },
            "best_ever_fitness": round(self._best_ever_reward, 4)
                if self._best_ever is not None else None,
            "best_ever_params": self._denormalize_individual(self._best_ever)
                if self._best_ever is not None else None,
        }

        return result

    # ── State Persistence ─────────────────────────────────────────────

    def save_state(self, path: Optional[str] = None) -> None:
        """Persist agent state to JSON file.

        Args:
            path: Override path. Defaults to /app/data/ppo_agent_state.json.
        """
        save_path = Path(path) if path else SHADOW_STATE_FILE
        try:
            save_path.parent.mkdir(parents=True, exist_ok=True)

            state: Dict[str, Any] = {
                "version": 2,
                "book": 213,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "generation": self._generation,
                "mutation_sigma": self._mutation_sigma,
                "n_comparisons": self._n_comparisons,
                "proposed_cumulative_pnl": self._proposed_cumulative_pnl,
                "actual_cumulative_pnl": self._actual_cumulative_pnl,
                "last_state": self._last_state,
                "best_ever_reward": self._best_ever_reward,
                "best_ever": self._best_ever.to_dict()
                    if self._best_ever is not None else None,
                "population": [ind.to_dict() for ind in self._population],
                "elite": [ind.to_dict() for ind in self._elite],
                "outcome_history": self._outcome_history[-ES_MAX_HISTORY:],
            }

            # Atomic write: write to temp then rename
            tmp_path = save_path.with_suffix(".tmp")
            with open(tmp_path, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(str(tmp_path), str(save_path))

            log.info("PPOParamOptimizer saved: gen=%d, outcomes=%d, path=%s",
                     self._generation, len(self._outcome_history), save_path)

        except Exception as e:
            log.error("Failed to save PPOParamOptimizer state: %s", e)
            raise

    def load_state(self, path: Optional[str] = None) -> bool:
        """Load agent state from JSON file.

        Args:
            path: Override path. Defaults to /app/data/ppo_agent_state.json.

        Returns:
            True if loaded successfully, False if file not found or error.
        """
        load_path = Path(path) if path else SHADOW_STATE_FILE
        try:
            if not load_path.exists():
                log.info("No saved PPO state at %s — starting fresh", load_path)
                return False

            with open(load_path, "r") as f:
                state = json.load(f)

            version = state.get("version", 1)
            if version < 2:
                log.warning("PPO state version %d too old — starting fresh", version)
                return False

            self._generation = state.get("generation", 0)
            self._mutation_sigma = state.get("mutation_sigma", ES_MUTATION_RATE)
            self._n_comparisons = state.get("n_comparisons", 0)
            self._proposed_cumulative_pnl = state.get("proposed_cumulative_pnl", 0.0)
            self._actual_cumulative_pnl = state.get("actual_cumulative_pnl", 0.0)
            self._last_state = state.get("last_state")
            self._best_ever_reward = state.get("best_ever_reward", float("-inf"))

            best_data = state.get("best_ever")
            if best_data is not None:
                self._best_ever = _Individual.from_dict(best_data)
            else:
                self._best_ever = None

            # Restore population
            pop_data = state.get("population", [])
            if pop_data:
                self._population = [_Individual.from_dict(d) for d in pop_data]
            else:
                self._init_population()

            # Restore elite
            elite_data = state.get("elite", [])
            if elite_data:
                self._elite = [_Individual.from_dict(d) for d in elite_data]

            # Restore outcome history
            self._outcome_history = state.get("outcome_history", [])

            log.info("PPOParamOptimizer loaded: gen=%d, outcomes=%d, "
                     "sigma=%.4f, best_fitness=%.4f, path=%s",
                     self._generation, len(self._outcome_history),
                     self._mutation_sigma, self._best_ever_reward, load_path)
            return True

        except json.JSONDecodeError as e:
            log.error("Corrupt PPO state file %s: %s", load_path, e)
            return False
        except Exception as e:
            log.error("Failed to load PPOParamOptimizer state: %s", e)
            return False

    @property
    def stats(self) -> Dict[str, Any]:
        """Summary statistics for monitoring."""
        return {
            "generation": self._generation,
            "population_size": len(self._population),
            "elite_size": len(self._elite),
            "mutation_sigma": round(self._mutation_sigma, 6),
            "outcome_history_len": len(self._outcome_history),
            "n_comparisons": self._n_comparisons,
            "best_ever_fitness": round(self._best_ever_reward, 4)
                if self._best_ever is not None else None,
            "actual_cumulative_pnl": round(self._actual_cumulative_pnl, 2),
            "proposed_cumulative_pnl": round(self._proposed_cumulative_pnl, 2),
            "shadow_mode": True,
        }


# ── Nightly Integration ───────────────────────────────────────────────

def run_nightly_ppo(metrics: Dict[str, Any],
                    recommendations: Dict[str, Any],
                    state_path: Optional[str] = None) -> Dict[str, Any]:
    """Nightly pipeline integration for PPO shadow optimizer.

    Called by the nightly pipeline (step N) to:
      1. Load or create the PPO optimizer
      2. Extract current params from recommendations
      3. Record yesterday's outcome (if available)
      4. Propose new adjustments
      5. Return shadow comparison + proposal
      6. Save state

    Args:
        metrics: Nightly metrics dict (from nightly_v6 DailyMetrics or similar).
            Expected keys: win_rate, total_pnl, max_drawdown, regime,
            total_trades, sharpe, avg_confidence, etc.
        recommendations: Current nightly recommendations dict.
            Expected keys: kelly_fraction, chandelier_atr_mult, etc.
        state_path: Override state file path (for testing).

    Returns:
        Dict with proposal, comparison, and status.
    """
    try:
        optimizer = PPOParamOptimizer(seed=42)
        loaded = optimizer.load_state(state_path)

        # Extract current params from recommendations
        current_params: Dict[str, float] = {}
        for name in PARAM_NAMES:
            val = recommendations.get(name)
            if val is not None:
                try:
                    current_params[name] = float(val)
                except (TypeError, ValueError):
                    pass

        # Record yesterday's outcome if we have history
        yesterday_pnl = metrics.get("total_pnl", metrics.get("pnl_today"))
        yesterday_dd = metrics.get("max_drawdown",
                                   metrics.get("max_drawdown_7d", 0.0))

        if yesterday_pnl is not None and loaded:
            # Use the last proposal from history (if any) as the proposed params
            if optimizer._outcome_history:
                last_proposed = optimizer._outcome_history[-1].get(
                    "proposed_params", current_params
                )
            else:
                last_proposed = current_params

            try:
                optimizer.record_outcome(
                    proposed_params=last_proposed,
                    actual_pnl=float(yesterday_pnl),
                    drawdown_pct=float(yesterday_dd),
                    actual_params=current_params,
                )
            except Exception as e:
                log.warning("PPO record_outcome failed: %s", e)

        # Propose new adjustments
        proposal = optimizer.propose_adjustments(current_params, metrics)

        # Get shadow comparison
        comparison = optimizer.get_shadow_comparison()

        # Save state
        try:
            optimizer.save_state(state_path)
        except Exception as e:
            log.error("PPO save_state failed: %s", e)

        result: Dict[str, Any] = {
            "status": "ok",
            "book": 213,
            "shadow_mode": True,
            "loaded_existing": loaded,
            "proposal": proposal,
            "comparison": comparison,
            "optimizer_stats": optimizer.stats,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        log.info("run_nightly_ppo complete: gen=%d, n_outcomes=%d, "
                 "shadow=%s",
                 optimizer._generation,
                 len(optimizer._outcome_history),
                 "active" if comparison.get("status") == "active" else "no_data")

        return result

    except Exception as e:
        log.error("run_nightly_ppo failed: %s", e, exc_info=True)
        return {
            "status": "error",
            "book": 213,
            "shadow_mode": True,
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# ╔═══════════════════════════════════════════════════════════════════════╗
# ║  PART 2: Original numpy-based RL PPO Agent (preserved)              ║
# ╚═══════════════════════════════════════════════════════════════════════╝
# The classes below require numpy and implement the original Book 213
# discrete-action PPO for HOLD/BUY/SELL decision-making. They are kept
# intact for backward compatibility but are separate from the shadow-mode
# parameter optimizer above.

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


# ── Config (numpy PPO) ───────────────────────────────────────────────

@dataclass
class PPOConfig:
    """Configuration for risk-constrained PPO (numpy-based RL agent).

    Attributes:
        clip_ratio: PPO clipping parameter (epsilon).
        gamma: Discount factor.
        lam: GAE lambda.
        drawdown_penalty: Multiplier for drawdown penalty.
        max_dd_threshold: Drawdown percentage above which penalty applies.
        lr_policy: Policy network learning rate.
        lr_value: Value network learning rate.
        seed: Random seed.
    """
    clip_ratio: float = 0.2
    gamma: float = 0.99
    lam: float = 0.95
    drawdown_penalty: float = 10.0
    max_dd_threshold: float = 0.08
    lr_policy: float = 3e-4
    lr_value: float = 1e-3
    seed: int = 42


if _HAS_NUMPY:

    # ── MLP Utilities (numpy) ────────────────────────────────────────

    def _he_init(fan_in: int, fan_out: int,
                 rng: "np.random.Generator") -> "np.ndarray":
        """He initialization for weights."""
        std = math.sqrt(2.0 / fan_in)
        return rng.normal(0.0, std, (fan_in, fan_out))

    def _relu(x: "np.ndarray") -> "np.ndarray":
        """ReLU activation."""
        return np.maximum(0.0, x)

    def _softmax(x: "np.ndarray") -> "np.ndarray":
        """Numerically stable softmax."""
        e = np.exp(x - np.max(x, axis=-1, keepdims=True))
        return e / (np.sum(e, axis=-1, keepdims=True) + 1e-10)

    def _tanh(x: "np.ndarray") -> "np.ndarray":
        """Tanh activation."""
        return np.tanh(x)

    def _clip_grad(grad: "np.ndarray",
                   max_norm: float = MAX_GRAD_NORM) -> "np.ndarray":
        """Clip gradient by norm."""
        norm = np.linalg.norm(grad)
        if norm > max_norm:
            return grad * max_norm / norm
        return grad

    # ── Policy Network ───────────────────────────────────────────────

    class PolicyNetwork:
        """MLP policy that outputs action probabilities.

        Architecture: Linear -> Tanh -> Linear -> Tanh -> Linear -> Softmax
        """

        def __init__(self, state_dim: int, action_dim: int,
                     hidden: int = 64, seed: int = 42):
            self.state_dim = state_dim
            self.action_dim = action_dim
            self.hidden = hidden
            self._rng = np.random.default_rng(seed)

            self.W1 = _he_init(state_dim, hidden, self._rng)
            self.b1 = np.zeros(hidden)
            self.W2 = _he_init(hidden, hidden, self._rng)
            self.b2 = np.zeros(hidden)
            self.W3 = self._rng.normal(0, 0.01, (hidden, action_dim))
            self.b3 = np.zeros(action_dim)

            self._adam_m: Dict[str, Any] = {}
            self._adam_v: Dict[str, Any] = {}
            self._adam_t: int = 0
            for name in ["W1", "b1", "W2", "b2", "W3", "b3"]:
                self._adam_m[name] = np.zeros_like(getattr(self, name))
                self._adam_v[name] = np.zeros_like(getattr(self, name))

        def forward(self, state: "np.ndarray") -> "np.ndarray":
            single = state.ndim == 1
            if single:
                state = state.reshape(1, -1)

            self._z1 = state @ self.W1 + self.b1
            self._h1 = _tanh(self._z1)
            self._z2 = self._h1 @ self.W2 + self.b2
            self._h2 = _tanh(self._z2)
            logits = self._h2 @ self.W3 + self.b3
            probs = _softmax(logits)

            self._state = state
            self._logits = logits
            self._probs = probs

            if single:
                return probs.squeeze(0)
            return probs

        def backward(self, grad_logprob: "np.ndarray",
                     actions: "np.ndarray", lr: float) -> None:
            batch_size = self._state.shape[0]
            grad_logits = np.copy(self._probs)
            for i in range(batch_size):
                grad_logits[i, int(actions[i])] -= 1.0
                grad_logits[i] *= -grad_logprob[i]
            grad_logits /= batch_size
            grad_logits = _clip_grad(grad_logits, MAX_GRAD_NORM)

            grad_W3 = self._h2.T @ grad_logits
            grad_b3 = np.sum(grad_logits, axis=0)
            grad_h2 = grad_logits @ self.W3.T
            grad_z2 = grad_h2 * (1.0 - self._h2 ** 2)
            grad_W2 = self._h1.T @ grad_z2
            grad_b2 = np.sum(grad_z2, axis=0)
            grad_h1 = grad_z2 @ self.W2.T
            grad_z1 = grad_h1 * (1.0 - self._h1 ** 2)
            grad_W1 = self._state.T @ grad_z1
            grad_b1 = np.sum(grad_z1, axis=0)

            self._adam_t += 1
            beta1, beta2, eps = 0.9, 0.999, 1e-8
            for name, grad in [("W1", grad_W1), ("b1", grad_b1),
                                ("W2", grad_W2), ("b2", grad_b2),
                                ("W3", grad_W3), ("b3", grad_b3)]:
                grad = _clip_grad(grad, MAX_GRAD_NORM)
                self._adam_m[name] = beta1 * self._adam_m[name] + (1 - beta1) * grad
                self._adam_v[name] = beta2 * self._adam_v[name] + (1 - beta2) * grad ** 2
                m_hat = self._adam_m[name] / (1 - beta1 ** self._adam_t)
                v_hat = self._adam_v[name] / (1 - beta2 ** self._adam_t)
                update = lr * m_hat / (np.sqrt(v_hat) + eps)
                current = getattr(self, name)
                setattr(self, name, current - update)

        def get_params(self) -> Dict[str, "np.ndarray"]:
            return {
                "W1": self.W1.copy(), "b1": self.b1.copy(),
                "W2": self.W2.copy(), "b2": self.b2.copy(),
                "W3": self.W3.copy(), "b3": self.b3.copy(),
            }

        def set_params(self, params: Dict[str, "np.ndarray"]) -> None:
            for name, val in params.items():
                if hasattr(self, name):
                    setattr(self, name, val.copy())

    # ── Value Network ────────────────────────────────────────────────

    class ValueNetwork:
        """MLP critic that estimates state value.

        Architecture: Linear -> Tanh -> Linear -> Tanh -> Linear (scalar)
        """

        def __init__(self, state_dim: int, hidden: int = 64, seed: int = 42):
            self.state_dim = state_dim
            self.hidden = hidden
            self._rng = np.random.default_rng(seed)

            self.W1 = _he_init(state_dim, hidden, self._rng)
            self.b1 = np.zeros(hidden)
            self.W2 = _he_init(hidden, hidden, self._rng)
            self.b2 = np.zeros(hidden)
            self.W3 = self._rng.normal(0, 0.01, (hidden, 1))
            self.b3 = np.zeros(1)

            self._adam_m: Dict[str, Any] = {}
            self._adam_v: Dict[str, Any] = {}
            self._adam_t: int = 0
            for name in ["W1", "b1", "W2", "b2", "W3", "b3"]:
                self._adam_m[name] = np.zeros_like(getattr(self, name))
                self._adam_v[name] = np.zeros_like(getattr(self, name))

        def forward(self, state: "np.ndarray") -> float:
            single = state.ndim == 1
            if single:
                state = state.reshape(1, -1)

            self._state = state
            self._z1 = state @ self.W1 + self.b1
            self._h1 = _tanh(self._z1)
            self._z2 = self._h1 @ self.W2 + self.b2
            self._h2 = _tanh(self._z2)
            value = self._h2 @ self.W3 + self.b3

            if single:
                return float(value.squeeze())
            return value.squeeze()

        def forward_batch(self, states: "np.ndarray") -> "np.ndarray":
            if states.ndim == 1:
                states = states.reshape(1, -1)

            self._state = states
            self._z1 = states @ self.W1 + self.b1
            self._h1 = _tanh(self._z1)
            self._z2 = self._h1 @ self.W2 + self.b2
            self._h2 = _tanh(self._z2)
            values = (self._h2 @ self.W3 + self.b3).squeeze(-1)
            return values

        def backward(self, grad_value: "np.ndarray", lr: float) -> None:
            batch_size = self._state.shape[0]
            grad_out = grad_value.reshape(-1, 1) / batch_size

            grad_W3 = self._h2.T @ grad_out
            grad_b3 = np.sum(grad_out, axis=0)
            grad_h2 = grad_out @ self.W3.T
            grad_z2 = grad_h2 * (1.0 - self._h2 ** 2)
            grad_W2 = self._h1.T @ grad_z2
            grad_b2 = np.sum(grad_z2, axis=0)
            grad_h1 = grad_z2 @ self.W2.T
            grad_z1 = grad_h1 * (1.0 - self._h1 ** 2)
            grad_W1 = self._state.T @ grad_z1
            grad_b1 = np.sum(grad_z1, axis=0)

            self._adam_t += 1
            beta1, beta2, eps = 0.9, 0.999, 1e-8
            for name, grad in [("W1", grad_W1), ("b1", grad_b1),
                                ("W2", grad_W2), ("b2", grad_b2),
                                ("W3", grad_W3), ("b3", grad_b3)]:
                grad = _clip_grad(grad, MAX_GRAD_NORM)
                self._adam_m[name] = beta1 * self._adam_m[name] + (1 - beta1) * grad
                self._adam_v[name] = beta2 * self._adam_v[name] + (1 - beta2) * grad ** 2
                m_hat = self._adam_m[name] / (1 - beta1 ** self._adam_t)
                v_hat = self._adam_v[name] / (1 - beta2 ** self._adam_t)
                update = lr * m_hat / (np.sqrt(v_hat) + eps)
                current = getattr(self, name)
                setattr(self, name, current - update)

        def get_params(self) -> Dict[str, "np.ndarray"]:
            return {
                "W1": self.W1.copy(), "b1": self.b1.copy(),
                "W2": self.W2.copy(), "b2": self.b2.copy(),
                "W3": self.W3.copy(), "b3": self.b3.copy(),
            }

        def set_params(self, params: Dict[str, "np.ndarray"]) -> None:
            for name, val in params.items():
                if hasattr(self, name):
                    setattr(self, name, val.copy())

    # ── Constrained PPO Agent (numpy RL) ─────────────────────────────

    class ConstrainedPPOAgent:
        """Risk-constrained PPO agent for safe trading (numpy-based).

        Combines standard PPO (clipped surrogate objective) with
        drawdown-aware reward shaping. Actions: HOLD, BUY, SELL.
        """

        def __init__(self, state_dim: int, action_dim: int = 3,
                     config: Optional[PPOConfig] = None):
            self._config = config or PPOConfig()
            self.state_dim = state_dim
            self.action_dim = action_dim
            self._rng = np.random.default_rng(self._config.seed)

            self._policy = PolicyNetwork(state_dim, action_dim, hidden=64,
                                          seed=self._config.seed)
            self._value = ValueNetwork(state_dim, hidden=64,
                                        seed=self._config.seed + 1)

            self._total_steps: int = 0
            self._n_updates: int = 0
            self._recent_losses: List[float] = []
            self._max_drawdown_seen: float = 0.0
            self._episode_returns: List[float] = []

            log.info("ConstrainedPPOAgent: state=%d, actions=%d, clip=%.2f, "
                     "gamma=%.3f, lam=%.3f, dd_penalty=%.1f, dd_threshold=%.2f",
                     state_dim, action_dim, self._config.clip_ratio,
                     self._config.gamma, self._config.lam,
                     self._config.drawdown_penalty,
                     self._config.max_dd_threshold)

        def select_action(self, state: "np.ndarray") -> Tuple[int, float]:
            self._total_steps += 1
            probs = self._policy.forward(state)
            probs = np.maximum(probs, 1e-8)
            probs = probs / np.sum(probs)
            action = int(self._rng.choice(self.action_dim, p=probs))
            log_prob = float(np.log(probs[action] + 1e-10))
            return action, log_prob

        def get_value(self, state: "np.ndarray") -> float:
            return self._value.forward(state)

        def compute_gae(self, rewards: "np.ndarray", values: "np.ndarray",
                        dones: "np.ndarray") -> "np.ndarray":
            T = len(rewards)
            advantages = np.zeros(T)
            gae = 0.0
            gamma = self._config.gamma
            lam = self._config.lam

            for t in reversed(range(T)):
                if t == T - 1:
                    next_value = values[T]
                else:
                    next_value = values[t + 1]
                delta = rewards[t] + gamma * next_value * (1 - dones[t]) - values[t]
                gae = delta + gamma * lam * (1 - dones[t]) * gae
                advantages[t] = gae

            return advantages

        def _risk_shaped_reward(self, raw_reward: float,
                                drawdown_pct: float) -> float:
            self._max_drawdown_seen = max(self._max_drawdown_seen, drawdown_pct)
            threshold = self._config.max_dd_threshold
            penalty_coeff = self._config.drawdown_penalty

            if drawdown_pct <= threshold * 0.5:
                return raw_reward
            if drawdown_pct <= threshold:
                excess = drawdown_pct - threshold * 0.5
                fraction = excess / (threshold * 0.5)
                penalty = penalty_coeff * 0.5 * fraction ** 2
                return raw_reward - penalty

            excess = drawdown_pct - threshold
            penalty = penalty_coeff * excess ** 2
            if drawdown_pct > threshold * 1.5:
                penalty *= 2.0

            shaped = raw_reward - penalty
            if drawdown_pct > threshold:
                log.warning("Drawdown penalty: dd=%.2f%% > threshold=%.2f%%, "
                            "penalty=%.4f, shaped_reward=%.4f",
                            drawdown_pct * 100, threshold * 100, penalty, shaped)
            return shaped

        def update(self, trajectories: Dict[str, "np.ndarray"]) -> Dict[str, float]:
            states = np.array(trajectories["states"])
            actions = np.array(trajectories["actions"], dtype=np.int64)
            raw_rewards = np.array(trajectories["rewards"])
            old_log_probs = np.array(trajectories["log_probs"])
            dones = np.array(trajectories["dones"], dtype=np.float64)
            drawdowns = np.array(trajectories.get("drawdowns",
                                                   np.zeros_like(raw_rewards)))

            T = len(states)
            if T < 2:
                return {"status": "insufficient_data", "n_steps": T}

            shaped_rewards = np.array([
                self._risk_shaped_reward(float(raw_rewards[t]), float(drawdowns[t]))
                for t in range(T)
            ])

            values = np.zeros(T + 1)
            for t in range(T):
                values[t] = self._value.forward(states[t])
            if T > 0 and not dones[-1]:
                values[T] = self._value.forward(states[-1])

            advantages = self.compute_gae(shaped_rewards, values, dones)
            returns = advantages + values[:T]

            adv_mean = np.mean(advantages)
            adv_std = np.std(advantages) + 1e-8
            advantages_norm = (advantages - adv_mean) / adv_std

            total_policy_loss = 0.0
            total_value_loss = 0.0
            total_entropy = 0.0
            total_clip_fraction = 0.0

            for epoch in range(PPO_EPOCHS):
                indices = self._rng.permutation(T)
                batch_size = max(T // 4, 16)

                for start in range(0, T, batch_size):
                    end = min(start + batch_size, T)
                    batch_idx = indices[start:end]
                    b_size = len(batch_idx)

                    b_states = states[batch_idx]
                    b_actions = actions[batch_idx]
                    b_old_log_probs = old_log_probs[batch_idx]
                    b_advantages = advantages_norm[batch_idx]
                    b_returns = returns[batch_idx]

                    new_probs = self._policy.forward(b_states)
                    new_log_probs = np.log(
                        new_probs[np.arange(b_size), b_actions] + 1e-10
                    )

                    ratio = np.exp(new_log_probs - b_old_log_probs)
                    surr1 = ratio * b_advantages
                    surr2 = np.clip(ratio,
                                    1.0 - self._config.clip_ratio,
                                    1.0 + self._config.clip_ratio) * b_advantages
                    policy_loss = -np.mean(np.minimum(surr1, surr2))

                    entropy = -np.sum(new_probs * np.log(new_probs + 1e-10), axis=1)
                    entropy_bonus = np.mean(entropy)

                    clip_fraction = float(np.mean(
                        np.abs(ratio - 1.0) > self._config.clip_ratio
                    ))

                    clipped_ratio = np.clip(ratio,
                                            1.0 - self._config.clip_ratio,
                                            1.0 + self._config.clip_ratio)
                    use_clipped = (surr2 < surr1).astype(np.float64)
                    effective_ratio = (use_clipped * clipped_ratio
                                       + (1 - use_clipped) * ratio)
                    grad_per_sample = effective_ratio * b_advantages + ENTROPY_COEFF

                    self._policy.backward(
                        grad_per_sample, b_actions, self._config.lr_policy
                    )

                    b_values = self._value.forward_batch(b_states)
                    value_loss = np.mean((b_values - b_returns) ** 2)
                    grad_value = 2.0 * VALUE_LOSS_COEFF * (b_values - b_returns)
                    self._value.backward(grad_value, self._config.lr_value)

                    total_policy_loss += policy_loss
                    total_value_loss += value_loss
                    total_entropy += entropy_bonus
                    total_clip_fraction += clip_fraction

            n_batches = max(PPO_EPOCHS * max(T // max(T // 4, 16), 1), 1)
            self._n_updates += 1

            avg_policy_loss = total_policy_loss / n_batches
            avg_value_loss = total_value_loss / n_batches

            self._recent_losses.append(avg_policy_loss)
            if len(self._recent_losses) > 100:
                self._recent_losses = self._recent_losses[-100:]

            self._episode_returns.append(float(np.sum(raw_rewards)))
            if len(self._episode_returns) > 100:
                self._episode_returns = self._episode_returns[-100:]

            metrics_out = {
                "policy_loss": round(avg_policy_loss, 6),
                "value_loss": round(avg_value_loss, 6),
                "entropy": round(total_entropy / n_batches, 4),
                "clip_fraction": round(total_clip_fraction / n_batches, 4),
                "n_updates": self._n_updates,
                "total_steps": self._total_steps,
                "trajectory_length": T,
                "mean_advantage": round(float(adv_mean), 4),
                "mean_return": round(float(np.mean(self._episode_returns)), 4),
                "max_drawdown_seen": round(self._max_drawdown_seen, 4),
                "mean_shaped_reward": round(float(np.mean(shaped_rewards)), 4),
                "mean_raw_reward": round(float(np.mean(raw_rewards)), 4),
            }

            log.info("PPO update #%d: policy_loss=%.4f, value_loss=%.4f, "
                     "entropy=%.3f, clip=%.2f, T=%d",
                     self._n_updates, avg_policy_loss, avg_value_loss,
                     total_entropy / n_batches,
                     total_clip_fraction / n_batches, T)

            return metrics_out

        def save(self, path: str = "/app/data/ppo/agent.npz") -> None:
            save_path = Path(path)
            try:
                save_path.parent.mkdir(parents=True, exist_ok=True)
                save_dict: Dict[str, "np.ndarray"] = {}
                for name, arr in self._policy.get_params().items():
                    save_dict[f"policy_{name}"] = arr
                for name, arr in self._value.get_params().items():
                    save_dict[f"value_{name}"] = arr
                save_dict["meta"] = np.array([
                    self.state_dim, self.action_dim,
                    self._total_steps, self._n_updates,
                    self._max_drawdown_seen,
                ])
                np.savez(str(save_path), **save_dict)
                log.info("ConstrainedPPOAgent saved to %s (updates=%d, steps=%d)",
                         path, self._n_updates, self._total_steps)
            except Exception as e:
                log.error("Failed to save PPO agent: %s", e)

        def load(self, path: str = "/app/data/ppo/agent.npz") -> None:
            try:
                data = np.load(path, allow_pickle=False)
                meta = data["meta"]
                saved_state = int(meta[0])
                saved_action = int(meta[1])

                if saved_state != self.state_dim or saved_action != self.action_dim:
                    log.warning("Dimension mismatch: saved=(%d,%d) vs current=(%d,%d)",
                                saved_state, saved_action,
                                self.state_dim, self.action_dim)
                    return

                self._total_steps = int(meta[2])
                self._n_updates = int(meta[3])
                self._max_drawdown_seen = float(meta[4])

                policy_params = {}
                for name in ["W1", "b1", "W2", "b2", "W3", "b3"]:
                    key = f"policy_{name}"
                    if key in data:
                        policy_params[name] = data[key]
                if policy_params:
                    self._policy.set_params(policy_params)

                value_params = {}
                for name in ["W1", "b1", "W2", "b2", "W3", "b3"]:
                    key = f"value_{name}"
                    if key in data:
                        value_params[name] = data[key]
                if value_params:
                    self._value.set_params(value_params)

                log.info("ConstrainedPPOAgent loaded from %s (updates=%d, "
                         "steps=%d, max_dd=%.3f)",
                         path, self._n_updates, self._total_steps,
                         self._max_drawdown_seen)
            except FileNotFoundError:
                log.info("No saved state at %s -- starting fresh", path)
            except Exception as e:
                log.error("Failed to load PPO agent: %s", e)

        @property
        def stats(self) -> Dict[str, Any]:
            return {
                "total_steps": self._total_steps,
                "n_updates": self._n_updates,
                "max_drawdown_seen": round(self._max_drawdown_seen, 4),
                "mean_episode_return": round(
                    float(np.mean(self._episode_returns)), 4
                ) if self._episode_returns else 0.0,
                "avg_policy_loss": round(
                    float(np.mean(self._recent_losses)), 6
                ) if self._recent_losses else 0.0,
                "config": {
                    "clip_ratio": self._config.clip_ratio,
                    "gamma": self._config.gamma,
                    "drawdown_penalty": self._config.drawdown_penalty,
                    "max_dd_threshold": self._config.max_dd_threshold,
                },
            }

else:
    # numpy not available — provide stubs so imports don't break
    class PolicyNetwork:  # type: ignore[no-redef]
        """Stub: numpy not available."""
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError("PolicyNetwork requires numpy")

    class ValueNetwork:  # type: ignore[no-redef]
        """Stub: numpy not available."""
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError("ValueNetwork requires numpy")

    class ConstrainedPPOAgent:  # type: ignore[no-redef]
        """Stub: numpy not available."""
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError("ConstrainedPPOAgent requires numpy")


# ── Module-level exports ─────────────────────────────────────────────

__all__ = [
    # Pure-stdlib shadow optimizer (Book 213 primary)
    "PPOParamOptimizer",
    "run_nightly_ppo",
    "PARAM_BOUNDS",
    "PARAM_NAMES",
    # Numpy-based RL agent (backward compat)
    "PPOConfig",
    "PolicyNetwork",
    "ValueNetwork",
    "ConstrainedPPOAgent",
]
