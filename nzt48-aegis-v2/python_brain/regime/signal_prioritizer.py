"""Signal Prioritization with Thompson Sampling — Book 50.

Dynamically adjusts strategy priority weights using Thompson sampling
on rolling win-rate posteriors. Balances exploitation (trade proven
strategies more) with exploration (don't starve unproven strategies).

Static priorities from signal_router.py serve as the base. This module
adds a dynamic bonus/penalty based on recent performance, so strategies
that are currently performing well get boosted in conflict resolution.

The Thompson sampler uses a Beta(alpha, beta) posterior per strategy:
  - alpha = wins + prior_alpha
  - beta = losses + prior_beta
  - Sample from Beta(alpha, beta) to get a Thompson score
  - Dynamic priority = static_priority + thompson_bonus

Rolling window: last 50 trades per strategy (configurable).
Prior: Beta(2, 2) — weak uniform prior (requires ~10 trades to dominate).

Integration:
  - bridge.py calls update_outcome() on each exit
  - signal_router calls get_dynamic_priority() during conflict resolution
  - Nightly step logs Thompson state for forensics

Usage:
    from python_brain.regime.signal_prioritizer import (
        SignalPrioritizer, get_prioritizer,
    )

    prioritizer = get_prioritizer()
    prioritizer.update_outcome("S2_Reversion", won=True)
    dyn_priority = prioritizer.get_dynamic_priority("S2_Reversion")
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

log = logging.getLogger("signal_prioritizer")

# Import static priorities as the baseline
try:
    from python_brain.regime.signal_router import STRATEGY_PRIORITY
except ImportError:
    STRATEGY_PRIORITY: Dict[str, int] = {}


@dataclass
class StrategyPosterior:
    """Beta posterior for a single strategy's win rate."""
    name: str
    prior_alpha: float = 2.0   # Prior successes (weak uniform)
    prior_beta: float = 2.0    # Prior failures
    wins: int = 0
    losses: int = 0
    outcomes: Deque = field(default_factory=lambda: deque(maxlen=50))

    @property
    def alpha(self) -> float:
        return self.prior_alpha + self.wins

    @property
    def beta(self) -> float:
        return self.prior_beta + self.losses

    @property
    def n_trades(self) -> int:
        return self.wins + self.losses

    @property
    def mean_wr(self) -> float:
        """Posterior mean win rate."""
        return self.alpha / (self.alpha + self.beta)

    def sample(self) -> float:
        """Draw from Beta(alpha, beta) posterior.

        Returns: Sampled win rate (0-1).
        """
        try:
            return random.betavariate(self.alpha, self.beta)
        except ValueError:
            return 0.5

    def update(self, won: bool):
        """Record a trade outcome, maintaining rolling window."""
        self.outcomes.append(won)
        if won:
            self.wins += 1
        else:
            self.losses += 1

        # If we've exceeded window, remove oldest outcome's effect
        if len(self.outcomes) > self.outcomes.maxlen:
            oldest = self.outcomes[0]  # Already removed by deque
            if oldest:
                self.wins = max(0, self.wins - 1)
            else:
                self.losses = max(0, self.losses - 1)


# Thompson bonus scaling: a sampled WR of 0.6 vs baseline 0.5
# gives +10 priority, WR of 0.7 gives +20, WR of 0.4 gives -10.
THOMPSON_BONUS_SCALE = 100  # Priority points per 1.0 WR difference from 0.5


class SignalPrioritizer:
    """Thompson sampling-based signal prioritization."""

    def __init__(
        self,
        window: int = 50,
        prior_alpha: float = 2.0,
        prior_beta: float = 2.0,
        bonus_scale: float = THOMPSON_BONUS_SCALE,
        min_trades_for_bonus: int = 5,
    ):
        self._posteriors: Dict[str, StrategyPosterior] = {}
        self._window = window
        self._prior_alpha = prior_alpha
        self._prior_beta = prior_beta
        self._bonus_scale = bonus_scale
        self._min_trades = min_trades_for_bonus

    def _get_posterior(self, strategy: str) -> StrategyPosterior:
        if strategy not in self._posteriors:
            self._posteriors[strategy] = StrategyPosterior(
                name=strategy,
                prior_alpha=self._prior_alpha,
                prior_beta=self._prior_beta,
                outcomes=deque(maxlen=self._window),
            )
        return self._posteriors[strategy]

    def update_outcome(self, strategy: str, won: bool):
        """Record a trade outcome for a strategy."""
        post = self._get_posterior(strategy)
        post.update(won)
        log.debug(
            "THOMPSON: %s %s → α=%.1f β=%.1f mean_wr=%.2f (n=%d)",
            strategy, "WIN" if won else "LOSS",
            post.alpha, post.beta, post.mean_wr, post.n_trades,
        )

    def get_dynamic_priority(self, strategy: str) -> int:
        """Get dynamically adjusted priority for a strategy.

        Returns: static_priority + thompson_bonus (clamped to [1, 200])
        """
        static = STRATEGY_PRIORITY.get(strategy, 50)
        post = self._get_posterior(strategy)

        # Don't apply bonus until we have enough data
        if post.n_trades < self._min_trades:
            return static

        # Sample from posterior to get exploration/exploitation balance
        sampled_wr = post.sample()
        bonus = int((sampled_wr - 0.5) * self._bonus_scale)

        # Clamp bonus to [-30, +30] so it can't completely override static order
        bonus = max(-30, min(30, bonus))

        dynamic = max(1, min(200, static + bonus))

        return dynamic

    def get_all_priorities(self) -> Dict[str, Dict]:
        """Get current state of all strategy priorities.

        Returns: {strategy: {static, dynamic, mean_wr, n_trades, alpha, beta}}
        """
        result = {}
        all_strategies = set(STRATEGY_PRIORITY.keys()) | set(self._posteriors.keys())

        for strat in sorted(all_strategies):
            static = STRATEGY_PRIORITY.get(strat, 50)
            post = self._posteriors.get(strat)
            if post and post.n_trades >= self._min_trades:
                dynamic = self.get_dynamic_priority(strat)
                result[strat] = {
                    "static_priority": static,
                    "dynamic_priority": dynamic,
                    "mean_wr": round(post.mean_wr, 3),
                    "n_trades": post.n_trades,
                    "alpha": round(post.alpha, 1),
                    "beta": round(post.beta, 1),
                }
            else:
                result[strat] = {
                    "static_priority": static,
                    "dynamic_priority": static,
                    "mean_wr": 0.5,
                    "n_trades": post.n_trades if post else 0,
                    "alpha": self._prior_alpha,
                    "beta": self._prior_beta,
                }

        return result

    def to_dict(self) -> Dict:
        """Serialize state for nightly report."""
        return {
            "strategies": self.get_all_priorities(),
            "config": {
                "window": self._window,
                "prior_alpha": self._prior_alpha,
                "prior_beta": self._prior_beta,
                "bonus_scale": self._bonus_scale,
                "min_trades": self._min_trades,
            },
        }

    def save(self, path: Optional[Path] = None):
        """Save Thompson state to disk for persistence across restarts."""
        if path is None:
            data_dir = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
            path = data_dir / "thompson_prioritizer.json"
        path.parent.mkdir(parents=True, exist_ok=True)

        state = {}
        for name, post in self._posteriors.items():
            state[name] = {
                "wins": post.wins,
                "losses": post.losses,
                "outcomes": list(post.outcomes),
            }

        with open(path, "w") as f:
            json.dump(state, f, indent=2)
        log.info("Thompson state saved: %d strategies → %s", len(state), path)

    def load(self, path: Optional[Path] = None):
        """Load Thompson state from disk."""
        if path is None:
            data_dir = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
            path = data_dir / "thompson_prioritizer.json"

        if not path.exists():
            log.info("No Thompson state file at %s — starting fresh", path)
            return

        try:
            with open(path) as f:
                state = json.load(f)

            for name, data in state.items():
                post = self._get_posterior(name)
                post.wins = data.get("wins", 0)
                post.losses = data.get("losses", 0)
                outcomes = data.get("outcomes", [])
                post.outcomes = deque(outcomes, maxlen=self._window)

            log.info("Thompson state loaded: %d strategies from %s", len(state), path)
        except Exception as e:
            log.warning("Failed to load Thompson state: %s", e)


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_instance: Optional[SignalPrioritizer] = None


def get_prioritizer() -> SignalPrioritizer:
    """Get or create the global SignalPrioritizer instance."""
    global _instance
    if _instance is None:
        _instance = SignalPrioritizer()
        _instance.load()
    return _instance


# ---------------------------------------------------------------------------
# Nightly entry point
# ---------------------------------------------------------------------------

def run_nightly_thompson() -> Dict:
    """Nightly Thompson sampling report.

    Saves state, logs per-strategy posteriors.

    Returns: Summary dict for nightly_v6 recommendations.
    """
    prioritizer = get_prioritizer()
    prioritizer.save()

    state = prioritizer.to_dict()

    # Log summary
    strategies = state.get("strategies", {})
    for name, info in sorted(strategies.items(), key=lambda x: x[1].get("dynamic_priority", 0), reverse=True):
        if info["n_trades"] > 0:
            log.info(
                "THOMPSON: %s static=%d dynamic=%d wr=%.2f n=%d",
                name, info["static_priority"], info["dynamic_priority"],
                info["mean_wr"], info["n_trades"],
            )

    return {
        "status": "complete",
        "n_strategies": len(strategies),
        "strategies_with_data": sum(1 for s in strategies.values() if s["n_trades"] > 0),
        "top_strategy": max(strategies.items(), key=lambda x: x[1].get("dynamic_priority", 0))[0] if strategies else None,
    }
