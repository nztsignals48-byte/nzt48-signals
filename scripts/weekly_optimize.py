"""
AEGIS K-17: Weekly Genetic Optimization.

Scheduled for Sunday 22:00 UK time.
Downloads the week's tick data, runs walk-forward genetic optimization
on indicator parameters, and pushes optimised params to Redis.

Constraint: Max drift 10% per epoch — prevents parameter instability
from overfitting to a single week's noise.

Reference:
    Pardo, R. (2008). "The Evaluation and Optimization of Trading
    Strategies." Wiley.
    Koza, J.R. (1992). "Genetic Programming." MIT Press.

SKELETON IMPLEMENTATION — Phase K.

Usage:
    # Scheduled via APScheduler in main.py:
    # scheduler.add_job(run_weekly_optimization, 'cron',
    #                   day_of_week='sun', hour=22, minute=0,
    #                   timezone='Europe/London')

    python scripts/weekly_optimize.py  # Manual run
"""
from __future__ import annotations

import json
import logging
import random
import sys
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.weekly_optimize")

# --- Configuration ---
POPULATION_SIZE = 50            # Number of parameter sets per generation
GENERATIONS = 20                # Number of evolutionary generations
MUTATION_RATE = 0.15            # Probability of mutating each gene
CROSSOVER_RATE = 0.7            # Probability of crossover vs clone
ELITE_COUNT = 5                 # Top N individuals preserved unchanged
MAX_DRIFT_PCT = 0.10            # 10% max parameter drift per epoch
TOURNAMENT_SIZE = 3             # Tournament selection size
WALK_FORWARD_SPLITS = 4         # Number of walk-forward splits

# Redis key for optimised parameters
REDIS_PARAMS_KEY = "nzt48:optimized_params"
REDIS_PARAMS_HISTORY_KEY = "nzt48:optimized_params_history"


@dataclass
class Individual:
    """A single parameter set (chromosome) in the genetic population."""
    genes: dict[str, float] = field(default_factory=dict)
    fitness: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    generation: int = 0


@dataclass
class OptimizationResult:
    """Result of a weekly optimization run."""
    timestamp: datetime
    best_individual: Individual
    generations_run: int
    population_size: int
    walk_forward_sharpe: float
    params_drifted: dict[str, tuple[float, float]]  # param -> (old, new)
    drift_clamped: bool  # True if any param was clamped to 10% drift
    elapsed_seconds: float


# --- Default parameter ranges for genetic optimization ---
# Each entry: (min_value, max_value, current_default)
PARAMETER_SPACE: dict[str, tuple[float, float, float]] = {
    # Indicator parameters
    "ema_fast": (5, 15, 9),
    "ema_slow": (15, 30, 20),
    "ema_trend": (40, 65, 50),
    "rsi_period": (10, 20, 14),
    "rsi_oversold": (20, 35, 30),
    "rsi_overbought": (65, 80, 70),
    "atr_period": (10, 20, 14),
    "atr_stop_mult": (1.5, 3.5, 2.0),
    "bb_period": (15, 25, 20),
    "bb_std": (1.5, 2.5, 2.0),
    # Entry/exit thresholds
    "rvol_min": (1.0, 3.0, 1.5),
    "adx_trend_threshold": (20, 35, 25),
    "confidence_floor": (60, 75, 65),
    # Position sizing
    "risk_per_trade_pct": (0.005, 0.01, 0.0075),
    "profit_target_r": (1.5, 3.0, 2.0),
}


def create_random_individual(generation: int = 0) -> Individual:
    """Create a random individual within the parameter space."""
    genes = {}
    for param, (min_val, max_val, _default) in PARAMETER_SPACE.items():
        genes[param] = random.uniform(min_val, max_val)
    return Individual(genes=genes, generation=generation)


def create_seeded_individual(generation: int = 0) -> Individual:
    """Create an individual seeded with current default parameters."""
    genes = {}
    for param, (_min, _max, default) in PARAMETER_SPACE.items():
        genes[param] = default
    return Individual(genes=genes, generation=generation)


def mutate(individual: Individual, generation: int) -> Individual:
    """Mutate an individual's genes with Gaussian perturbation.

    Mutation magnitude decreases over generations (simulated annealing).
    """
    mutant = Individual(
        genes=deepcopy(individual.genes),
        generation=generation,
    )

    # Annealing: reduce mutation magnitude over generations
    temp = max(0.1, 1.0 - (generation / GENERATIONS) * 0.8)

    for param in mutant.genes:
        if random.random() < MUTATION_RATE:
            min_val, max_val, _ = PARAMETER_SPACE[param]
            range_size = max_val - min_val
            perturbation = random.gauss(0, range_size * 0.1 * temp)
            mutant.genes[param] = max(min_val, min(max_val,
                                      mutant.genes[param] + perturbation))

    return mutant


def crossover(parent_a: Individual, parent_b: Individual, generation: int) -> Individual:
    """Uniform crossover between two parents."""
    child_genes = {}
    for param in parent_a.genes:
        if random.random() < 0.5:
            child_genes[param] = parent_a.genes[param]
        else:
            child_genes[param] = parent_b.genes[param]
    return Individual(genes=child_genes, generation=generation)


def tournament_select(population: list[Individual]) -> Individual:
    """Tournament selection: pick best from random subset."""
    tournament = random.sample(population, min(TOURNAMENT_SIZE, len(population)))
    return max(tournament, key=lambda ind: ind.fitness)


def evaluate_fitness(individual: Individual, tick_data: Any) -> float:
    """Evaluate fitness of a parameter set using walk-forward backtesting.

    SKELETON: Returns placeholder fitness. Real implementation requires:
    1. Split tick_data into WALK_FORWARD_SPLITS in-sample/out-of-sample pairs
    2. For each split, run strategy with individual's parameters on in-sample
    3. Validate on out-of-sample
    4. Aggregate out-of-sample Sharpe ratios

    Fitness = mean out-of-sample Sharpe across all walk-forward splits.

    TODO (Phase Q2):
        - Integrate with strategy backtest engine
        - Use vectorized pandas backtest for speed
        - Add overfitting penalty (in-sample vs out-of-sample divergence)
        - Penalize parameter sets that produce < 30 trades (insufficient sample)
    """
    # SKELETON: placeholder fitness based on parameter reasonableness
    fitness = 0.0

    # Slight preference for parameters near defaults (stability bonus)
    for param, (min_val, max_val, default) in PARAMETER_SPACE.items():
        gene_val = individual.genes.get(param, default)
        range_size = max_val - min_val
        if range_size > 0:
            distance = abs(gene_val - default) / range_size
            fitness += (1.0 - distance) * 0.1  # small bonus for being near default

    individual.fitness = fitness
    return fitness


def clamp_drift(
    new_params: dict[str, float],
    old_params: dict[str, float],
    max_drift_pct: float = MAX_DRIFT_PCT,
) -> tuple[dict[str, float], bool]:
    """Clamp parameter drift to max_drift_pct per epoch.

    Prevents wild parameter swings from one week to the next.
    Each parameter can change by at most 10% of its current value.

    Args:
        new_params: Proposed new parameter values.
        old_params: Current parameter values.
        max_drift_pct: Maximum allowed drift as fraction (0.10 = 10%).

    Returns:
        (clamped_params, was_clamped) tuple.
    """
    clamped = {}
    was_clamped = False

    for param, new_val in new_params.items():
        old_val = old_params.get(param)

        if old_val is None or old_val == 0:
            clamped[param] = new_val
            continue

        max_change = abs(old_val) * max_drift_pct
        actual_change = new_val - old_val

        if abs(actual_change) > max_change:
            # Clamp to max drift
            direction = 1.0 if actual_change > 0 else -1.0
            clamped[param] = old_val + direction * max_change
            was_clamped = True
            logger.info(
                "K-17 DRIFT_CLAMPED: %s %.4f -> %.4f (wanted %.4f, max_change=%.4f)",
                param, old_val, clamped[param], new_val, max_change,
            )
        else:
            clamped[param] = new_val

    return clamped, was_clamped


def push_params_to_redis(
    params: dict[str, float],
    result: OptimizationResult,
    redis_client: Any = None,
) -> bool:
    """Push optimised parameters to Redis for live engine consumption.

    SKELETON: Logs the params. Real implementation writes to Redis.

    Args:
        params: Optimised parameter dictionary.
        result: Full optimization result for audit trail.
        redis_client: Redis connection (None = dry run).

    Returns:
        True if successfully pushed.

    TODO (Phase Q2):
        - Write to REDIS_PARAMS_KEY as JSON
        - Append to REDIS_PARAMS_HISTORY_KEY (list, max 52 weeks)
        - Add Redis pub/sub notification for live engine to reload
    """
    params_json = json.dumps(params, indent=2)
    logger.info("K-17 PARAMS_READY (skeleton — not pushing to Redis):\n%s", params_json)

    if redis_client is not None:
        try:
            redis_client.set(REDIS_PARAMS_KEY, params_json)
            redis_client.lpush(
                REDIS_PARAMS_HISTORY_KEY,
                json.dumps({
                    "timestamp": result.timestamp.isoformat(),
                    "params": params,
                    "sharpe": result.walk_forward_sharpe,
                    "drift_clamped": result.drift_clamped,
                }),
            )
            redis_client.ltrim(REDIS_PARAMS_HISTORY_KEY, 0, 51)  # Keep 52 weeks
            logger.info("K-17: Pushed optimised params to Redis key=%s", REDIS_PARAMS_KEY)
            return True
        except Exception as e:
            logger.error("K-17: Failed to push params to Redis: %s", e)
            return False

    return True


def run_weekly_optimization(
    tick_data: Any = None,
    redis_client: Any = None,
    current_params: Optional[dict[str, float]] = None,
) -> OptimizationResult:
    """Main entry point: run the weekly genetic optimization.

    Called by APScheduler every Sunday 22:00 UK.

    Args:
        tick_data: Week's tick data (SKELETON: not yet used).
        redis_client: Redis connection for param push.
        current_params: Current live parameters (for drift clamping).

    Returns:
        OptimizationResult with best parameters and metadata.
    """
    start_time = time.monotonic()
    logger.info("K-17: Starting weekly genetic optimization (pop=%d, gen=%d)",
                POPULATION_SIZE, GENERATIONS)

    # --- Initialize population ---
    population: list[Individual] = []

    # Seed with current defaults
    population.append(create_seeded_individual(generation=0))

    # Fill rest with random individuals
    for _ in range(POPULATION_SIZE - 1):
        population.append(create_random_individual(generation=0))

    # --- Evaluate initial population ---
    for ind in population:
        evaluate_fitness(ind, tick_data)

    # --- Evolution loop ---
    for gen in range(1, GENERATIONS + 1):
        # Sort by fitness (descending)
        population.sort(key=lambda x: x.fitness, reverse=True)

        # Elite preservation
        next_gen = population[:ELITE_COUNT]

        # Fill rest via selection + crossover/mutation
        while len(next_gen) < POPULATION_SIZE:
            parent_a = tournament_select(population)

            if random.random() < CROSSOVER_RATE:
                parent_b = tournament_select(population)
                child = crossover(parent_a, parent_b, gen)
            else:
                child = Individual(genes=deepcopy(parent_a.genes), generation=gen)

            child = mutate(child, gen)
            evaluate_fitness(child, tick_data)
            next_gen.append(child)

        population = next_gen

        # Log progress every 5 generations
        if gen % 5 == 0:
            best = max(population, key=lambda x: x.fitness)
            logger.info(
                "K-17: Gen %d/%d | best_fitness=%.4f",
                gen, GENERATIONS, best.fitness,
            )

    # --- Final result ---
    population.sort(key=lambda x: x.fitness, reverse=True)
    best = population[0]

    # --- Drift clamping ---
    if current_params is None:
        current_params = {p: default for p, (_, _, default) in PARAMETER_SPACE.items()}

    clamped_params, drift_clamped = clamp_drift(best.genes, current_params)

    # Track which params drifted
    params_drifted = {}
    for param in clamped_params:
        old_val = current_params.get(param, 0)
        new_val = clamped_params[param]
        if abs(new_val - old_val) > 1e-8:
            params_drifted[param] = (old_val, new_val)

    elapsed = time.monotonic() - start_time

    result = OptimizationResult(
        timestamp=datetime.now(timezone.utc),
        best_individual=best,
        generations_run=GENERATIONS,
        population_size=POPULATION_SIZE,
        walk_forward_sharpe=best.fitness,  # SKELETON: fitness as Sharpe proxy
        params_drifted=params_drifted,
        drift_clamped=drift_clamped,
        elapsed_seconds=round(elapsed, 2),
    )

    logger.info(
        "K-17: Optimization complete in %.1fs | best_fitness=%.4f | "
        "drift_clamped=%s | params_changed=%d",
        elapsed, best.fitness, drift_clamped, len(params_drifted),
    )

    # --- Push to Redis ---
    push_params_to_redis(clamped_params, result, redis_client)

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logger.info("K-17: Manual weekly optimization run")
    result = run_weekly_optimization()
    logger.info("K-17: Done. Best fitness=%.4f, elapsed=%.1fs",
                result.walk_forward_sharpe, result.elapsed_seconds)
