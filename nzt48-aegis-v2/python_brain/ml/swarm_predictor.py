"""Mirofish Swarm Simulation for Market Prediction — Book 151.

Multi-agent swarm simulation where heterogeneous agent archetypes
(momentum, mean-reversion, noise, fundamental, contrarian) interact
to produce emergent price forecasts. Wealth-weighted consensus
aggregation ensures agents with better track records carry more
influence, while diversity across agent types reduces collective
error per the Diversity Prediction Theorem.

Components:
  - AgentType: Enum of 5 trader archetypes
  - SwarmAgent: Individual agent with position, wealth, signal, confidence
  - SwarmSimulator: Multi-agent simulation engine
    - step() runs one simulation tick with market data
    - get_prediction() returns direction, magnitude, confidence

Bridge.py integration:
    try:
        from python_brain.ml.swarm_predictor import (
            SwarmSimulator, SwarmAgent, AgentType,
        )
    except ImportError:
        pass

    # In nightly pipeline:
    sim = SwarmSimulator(n_agents=100)
    for bar in recent_bars:
        sim.step(bar["close"], bar["volume"])
    pred = sim.get_prediction()
    direction = pred["direction"]   # 'bullish' / 'bearish' / 'neutral'
    confidence = pred["confidence"] # 0.0 - 1.0
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger("swarm_predictor")

__all__ = [
    "AgentType",
    "SwarmAgent",
    "SwarmSimulator",
]

DATA_DIR = "/app/data/swarm_predictor"


# ---------------------------------------------------------------------------
# Enums & Dataclasses
# ---------------------------------------------------------------------------

class AgentType(Enum):
    """The 5 canonical trader archetypes used in the swarm."""
    MOMENTUM = "momentum"
    MEAN_REVERSION = "mean_reversion"
    NOISE = "noise"
    FUNDAMENTAL = "fundamental"
    CONTRARIAN = "contrarian"


@dataclass
class SwarmAgent:
    """Single agent in the swarm simulation.

    Attributes:
        agent_id: Unique identifier.
        agent_type: One of the 5 archetypes.
        position: Current position (-1.0 short to +1.0 long).
        wealth: Cumulative virtual wealth (tracks prediction accuracy).
        signal: Agent's latest directional signal (-1 to +1).
        confidence: Agent's confidence in its own signal (0 to 1).
        lookback: Number of bars the agent considers.
        sensitivity: Reactivity to new information (0 to 1).
        memory: Rolling record of recent decisions and outcomes.
    """
    agent_id: int
    agent_type: AgentType
    position: float = 0.0
    wealth: float = 1000.0
    signal: float = 0.0
    confidence: float = 0.5
    lookback: int = 20
    sensitivity: float = 0.5
    memory: List[Dict[str, Any]] = field(default_factory=list)

    def record_outcome(self, actual_return: float) -> None:
        """Update wealth based on how well the agent's signal matched reality."""
        pnl = self.signal * actual_return * self.sensitivity
        self.wealth = max(1.0, self.wealth + pnl * self.wealth * 0.01)
        self.memory.append({
            "signal": self.signal,
            "actual": actual_return,
            "pnl": pnl,
        })
        # Keep memory bounded
        if len(self.memory) > 200:
            self.memory = self.memory[-200:]


# ---------------------------------------------------------------------------
# SwarmSimulator
# ---------------------------------------------------------------------------

class SwarmSimulator:
    """Multi-agent swarm simulation engine for market prediction.

    Creates a population of heterogeneous trading agents that independently
    react to price/volume data. The aggregate prediction uses wealth-weighted
    consensus — agents that have been right earn more influence.

    Args:
        n_agents: Total number of agents in the swarm.
        agent_mix: Dict mapping AgentType to fraction (must sum to 1.0).
                   Defaults to 30% momentum, 25% MR, 20% noise,
                   15% fundamental, 10% contrarian.
        seed: Random seed for reproducibility.
    """

    DEFAULT_MIX: Dict[AgentType, float] = {
        AgentType.MOMENTUM: 0.30,
        AgentType.MEAN_REVERSION: 0.25,
        AgentType.NOISE: 0.20,
        AgentType.FUNDAMENTAL: 0.15,
        AgentType.CONTRARIAN: 0.10,
    }

    def __init__(
        self,
        n_agents: int = 100,
        agent_mix: Optional[Dict[AgentType, float]] = None,
        seed: Optional[int] = None,
    ) -> None:
        self.n_agents = n_agents
        self.agent_mix = agent_mix or self.DEFAULT_MIX
        self.rng = np.random.default_rng(seed)
        self.agents: List[SwarmAgent] = []
        self.price_history: List[float] = []
        self.volume_history: List[float] = []
        self.prediction_history: List[Dict[str, Any]] = []
        self.step_count: int = 0

        self._initialise_agents()
        log.info(
            "SwarmSimulator initialised: %d agents, mix=%s",
            n_agents,
            {k.value: v for k, v in self.agent_mix.items()},
        )

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _initialise_agents(self) -> None:
        """Create the heterogeneous agent population."""
        agent_id = 0
        for agent_type, fraction in self.agent_mix.items():
            count = max(1, int(self.n_agents * fraction))
            for _ in range(count):
                lookback = self._random_lookback(agent_type)
                sensitivity = self._random_sensitivity(agent_type)
                agent = SwarmAgent(
                    agent_id=agent_id,
                    agent_type=agent_type,
                    lookback=lookback,
                    sensitivity=sensitivity,
                    wealth=1000.0,
                    confidence=0.5,
                )
                self.agents.append(agent)
                agent_id += 1
        log.debug("Created %d agents across %d types", len(self.agents), len(self.agent_mix))

    def _random_lookback(self, agent_type: AgentType) -> int:
        """Assign a type-appropriate lookback period."""
        ranges = {
            AgentType.MOMENTUM: (5, 40),
            AgentType.MEAN_REVERSION: (10, 60),
            AgentType.NOISE: (1, 5),
            AgentType.FUNDAMENTAL: (20, 100),
            AgentType.CONTRARIAN: (5, 30),
        }
        lo, hi = ranges[agent_type]
        return int(self.rng.integers(lo, hi + 1))

    def _random_sensitivity(self, agent_type: AgentType) -> float:
        """Assign a type-appropriate sensitivity."""
        ranges = {
            AgentType.MOMENTUM: (0.4, 0.9),
            AgentType.MEAN_REVERSION: (0.3, 0.7),
            AgentType.NOISE: (0.1, 0.5),
            AgentType.FUNDAMENTAL: (0.2, 0.6),
            AgentType.CONTRARIAN: (0.5, 1.0),
        }
        lo, hi = ranges[agent_type]
        return float(self.rng.uniform(lo, hi))

    # ------------------------------------------------------------------
    # Core Step
    # ------------------------------------------------------------------

    def step(self, market_price: float, volume: float) -> Dict[str, Any]:
        """Run one simulation tick.

        Each agent independently generates a signal based on its archetype
        and the recent price/volume history. Agents are then scored on prior
        predictions and the aggregate prediction is computed.

        Args:
            market_price: Current bar's closing price.
            volume: Current bar's volume.

        Returns:
            Dict with 'direction', 'magnitude', 'confidence', 'dispersion',
            'agent_signals' breakdown by type.
        """
        self.price_history.append(market_price)
        self.volume_history.append(volume)
        self.step_count += 1

        # Score agents on prior prediction if we have enough history
        if len(self.price_history) >= 3:
            prev_return = (
                (self.price_history[-1] - self.price_history[-2])
                / self.price_history[-2]
            )
            for agent in self.agents:
                agent.record_outcome(prev_return)

        # Each agent decides
        prices = np.array(self.price_history, dtype=np.float64)
        volumes = np.array(self.volume_history, dtype=np.float64)

        for agent in self.agents:
            signal = self._agent_decision(agent, prices, volumes)
            agent.signal = np.clip(signal, -1.0, 1.0)
            agent.confidence = self._agent_confidence(agent, prices)

        # Aggregate
        prediction = self._aggregate_prediction(self.agents)
        self.prediction_history.append(prediction)

        if self.step_count % 50 == 0:
            log.debug(
                "Step %d: direction=%s magnitude=%.4f confidence=%.3f",
                self.step_count,
                prediction["direction"],
                prediction["magnitude"],
                prediction["confidence"],
            )

        return prediction

    # ------------------------------------------------------------------
    # Agent Decision Logic
    # ------------------------------------------------------------------

    def _agent_decision(
        self,
        agent: SwarmAgent,
        prices: np.ndarray,
        volumes: np.ndarray,
    ) -> float:
        """Route to the appropriate decision function by agent type."""
        if len(prices) < 3:
            return 0.0

        dispatch = {
            AgentType.MOMENTUM: self._momentum_decision,
            AgentType.MEAN_REVERSION: self._mean_reversion_decision,
            AgentType.NOISE: self._noise_decision,
            AgentType.FUNDAMENTAL: self._fundamental_decision,
            AgentType.CONTRARIAN: self._contrarian_decision,
        }
        fn = dispatch[agent.agent_type]
        return fn(agent, prices, volumes)

    def _momentum_decision(
        self,
        agent: SwarmAgent,
        prices: np.ndarray,
        volumes: np.ndarray,
    ) -> float:
        """Momentum agents chase price trends.

        Uses exponential-weighted return over the agent's lookback window.
        Higher volume amplifies the momentum signal.
        """
        lb = min(agent.lookback, len(prices))
        window = prices[-lb:]
        if len(window) < 2:
            return 0.0

        # Exponential weighting — recent bars matter more
        weights = np.exp(np.linspace(-1, 0, len(window)))
        weights /= weights.sum()

        returns = np.diff(window) / window[:-1]
        if len(returns) == 0:
            return 0.0

        # Pad weights to match returns length
        w = weights[1:]  # one fewer element than prices
        if len(w) != len(returns):
            w = w[: len(returns)]

        weighted_return = np.dot(w / w.sum(), returns)

        # Volume confirmation: scale by relative volume
        vol_window = volumes[-lb:]
        if len(vol_window) > 1 and vol_window.mean() > 0:
            vol_ratio = vol_window[-1] / vol_window.mean()
            vol_factor = min(1.5, max(0.5, vol_ratio))
        else:
            vol_factor = 1.0

        signal = weighted_return * vol_factor * agent.sensitivity * 100.0
        return np.clip(signal, -1.0, 1.0)

    def _mean_reversion_decision(
        self,
        agent: SwarmAgent,
        prices: np.ndarray,
        volumes: np.ndarray,
    ) -> float:
        """Mean-reversion agents bet on return to the moving average.

        Computes z-score of current price vs rolling mean and sells
        when price is above, buys when below.
        """
        lb = min(agent.lookback, len(prices))
        window = prices[-lb:]
        if len(window) < 5:
            return 0.0

        mean_price = window.mean()
        std_price = window.std()
        if std_price < 1e-12:
            return 0.0

        z_score = (prices[-1] - mean_price) / std_price

        # Negative signal when above mean (sell), positive when below (buy)
        signal = -z_score * agent.sensitivity * 0.3
        return np.clip(signal, -1.0, 1.0)

    def _noise_decision(
        self,
        agent: SwarmAgent,
        prices: np.ndarray,
        volumes: np.ndarray,
    ) -> float:
        """Noise agents trade randomly with slight mean-reversion bias.

        Provides liquidity and realistic volume distribution without
        meaningful directional content.
        """
        noise = float(self.rng.normal(0, 0.3))
        # Slight mean-reversion bias to prevent price divergence
        if len(prices) >= 10:
            short_ma = prices[-5:].mean()
            long_ma = prices[-10:].mean()
            if long_ma > 0:
                bias = -(short_ma - long_ma) / long_ma * 0.1
            else:
                bias = 0.0
        else:
            bias = 0.0

        return np.clip(noise + bias, -1.0, 1.0)

    def _fundamental_decision(
        self,
        agent: SwarmAgent,
        prices: np.ndarray,
        volumes: np.ndarray,
    ) -> float:
        """Fundamental agents estimate fair value from long-term price trend.

        Uses a simple linear regression over the lookback window as a
        proxy for fair-value trajectory. Signal is deviation from the
        regression line.
        """
        lb = min(agent.lookback, len(prices))
        window = prices[-lb:]
        if len(window) < 10:
            return 0.0

        # Linear regression for fair value estimate
        x = np.arange(len(window), dtype=np.float64)
        x_mean = x.mean()
        y_mean = window.mean()
        ss_xy = np.dot(x - x_mean, window - y_mean)
        ss_xx = np.dot(x - x_mean, x - x_mean)
        if ss_xx < 1e-12:
            return 0.0

        slope = ss_xy / ss_xx
        intercept = y_mean - slope * x_mean
        fair_value = slope * len(window) + intercept

        if fair_value <= 0:
            return 0.0

        deviation = (fair_value - prices[-1]) / fair_value
        signal = deviation * agent.sensitivity * 2.0
        return np.clip(signal, -1.0, 1.0)

    def _contrarian_decision(
        self,
        agent: SwarmAgent,
        prices: np.ndarray,
        volumes: np.ndarray,
    ) -> float:
        """Contrarian agents trade against recent crowd behaviour.

        Looks at recent price momentum and volume spikes to detect
        potential reversals. Trades opposite to the crowd when
        conviction is high.
        """
        lb = min(agent.lookback, len(prices))
        window = prices[-lb:]
        if len(window) < 5:
            return 0.0

        # Recent momentum
        recent_return = (window[-1] - window[0]) / window[0] if window[0] > 0 else 0.0

        # Volume spike detection (contrarians fade volume spikes)
        vol_window = volumes[-lb:]
        if len(vol_window) > 2 and vol_window.mean() > 0:
            vol_spike = vol_window[-1] / vol_window.mean()
        else:
            vol_spike = 1.0

        # Stronger contrarian signal when momentum is extreme + volume high
        extremity = abs(recent_return) * vol_spike
        direction = -np.sign(recent_return)

        signal = direction * min(1.0, extremity * agent.sensitivity * 5.0)
        return np.clip(signal, -1.0, 1.0)

    # ------------------------------------------------------------------
    # Confidence Estimation
    # ------------------------------------------------------------------

    def _agent_confidence(self, agent: SwarmAgent, prices: np.ndarray) -> float:
        """Estimate agent's confidence based on recent prediction accuracy.

        Agents that have been consistently right earn higher confidence.
        Uses exponential decay over the last 20 decisions.
        """
        if len(agent.memory) < 3:
            return 0.5

        recent = agent.memory[-20:]
        correct = 0
        total = 0
        for entry in recent:
            if abs(entry["actual"]) > 1e-8:
                total += 1
                if entry["signal"] * entry["actual"] > 0:
                    correct += 1

        if total == 0:
            return 0.5

        accuracy = correct / total
        # Map accuracy to confidence: 50% accuracy = 0.3, 80% = 0.8
        confidence = 0.1 + 0.9 * accuracy
        return np.clip(confidence, 0.1, 0.95)

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def _aggregate_prediction(self, agents: List[SwarmAgent]) -> Dict[str, Any]:
        """Wealth-weighted consensus across all agents.

        Agents with higher accumulated wealth (better historical accuracy)
        carry proportionally more weight in the final prediction.

        Returns:
            Dict with:
              - direction: 'bullish', 'bearish', or 'neutral'
              - magnitude: float 0-1 (strength of prediction)
              - confidence: float 0-1 (agreement level among agents)
              - dispersion: float (disagreement measure)
              - agent_signals: breakdown by agent type
        """
        if not agents:
            return self._neutral_prediction()

        # Wealth-weighted signal
        total_wealth = sum(a.wealth for a in agents)
        if total_wealth <= 0:
            return self._neutral_prediction()

        weights = np.array([a.wealth / total_wealth for a in agents])
        signals = np.array([a.signal for a in agents])
        confidences = np.array([a.confidence for a in agents])

        # Wealth * confidence weighted consensus
        combined_weights = weights * confidences
        w_sum = combined_weights.sum()
        if w_sum < 1e-12:
            return self._neutral_prediction()

        combined_weights /= w_sum
        consensus = float(np.dot(combined_weights, signals))

        # Dispersion: wealth-weighted variance of signals (disagreement)
        dispersion = float(np.dot(combined_weights, (signals - consensus) ** 2))

        # Per-type breakdown
        agent_signals: Dict[str, Dict[str, float]] = {}
        for atype in AgentType:
            type_agents = [a for a in agents if a.agent_type == atype]
            if type_agents:
                type_signals = [a.signal for a in type_agents]
                type_wealth = sum(a.wealth for a in type_agents)
                agent_signals[atype.value] = {
                    "mean_signal": float(np.mean(type_signals)),
                    "std_signal": float(np.std(type_signals)),
                    "total_wealth": type_wealth,
                    "count": len(type_agents),
                }

        # Direction classification
        if consensus > 0.05:
            direction = "bullish"
        elif consensus < -0.05:
            direction = "bearish"
        else:
            direction = "neutral"

        # Confidence based on agreement (low dispersion = high confidence)
        agreement_confidence = max(0.0, 1.0 - dispersion * 3.0)
        magnitude = min(1.0, abs(consensus))

        return {
            "direction": direction,
            "magnitude": magnitude,
            "confidence": float(np.clip(agreement_confidence, 0.0, 1.0)),
            "consensus": consensus,
            "dispersion": dispersion,
            "agent_signals": agent_signals,
            "n_agents": len(agents),
            "total_wealth": total_wealth,
            "step": self.step_count,
        }

    @staticmethod
    def _neutral_prediction() -> Dict[str, Any]:
        """Return a neutral / no-signal prediction."""
        return {
            "direction": "neutral",
            "magnitude": 0.0,
            "confidence": 0.0,
            "consensus": 0.0,
            "dispersion": 0.0,
            "agent_signals": {},
            "n_agents": 0,
            "total_wealth": 0.0,
            "step": 0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_prediction(self) -> Dict[str, Any]:
        """Return the most recent aggregate prediction.

        Returns:
            Dict with direction, magnitude, confidence, and full breakdown.
            Returns neutral prediction if no steps have been run.
        """
        if not self.prediction_history:
            return self._neutral_prediction()
        return self.prediction_history[-1]

    def get_agent_stats(self) -> Dict[str, Any]:
        """Summary statistics of agent population.

        Returns:
            Dict with per-type stats: count, mean_wealth, mean_signal, mean_confidence.
        """
        stats: Dict[str, Any] = {}
        for atype in AgentType:
            type_agents = [a for a in self.agents if a.agent_type == atype]
            if type_agents:
                stats[atype.value] = {
                    "count": len(type_agents),
                    "mean_wealth": float(np.mean([a.wealth for a in type_agents])),
                    "mean_signal": float(np.mean([a.signal for a in type_agents])),
                    "mean_confidence": float(np.mean([a.confidence for a in type_agents])),
                    "wealth_std": float(np.std([a.wealth for a in type_agents])),
                }
        return stats

    def save_state(self, filepath: Optional[str] = None) -> None:
        """Persist simulator state to JSON.

        Args:
            filepath: Output path. Defaults to /app/data/swarm_predictor/state.json.
        """
        filepath = filepath or os.path.join(DATA_DIR, "state.json")
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        state = {
            "step_count": self.step_count,
            "n_agents": len(self.agents),
            "agent_stats": self.get_agent_stats(),
            "last_prediction": self.get_prediction(),
            "price_history_len": len(self.price_history),
            "timestamp": time.time(),
        }

        try:
            with open(filepath, "w") as f:
                json.dump(state, f, indent=2)
            log.info("Swarm state saved to %s", filepath)
        except OSError as e:
            log.error("Failed to save swarm state: %s", e)

    def reset(self) -> None:
        """Reset the simulator to initial state, preserving configuration."""
        self.agents.clear()
        self.price_history.clear()
        self.volume_history.clear()
        self.prediction_history.clear()
        self.step_count = 0
        self._initialise_agents()
        log.info("SwarmSimulator reset")
