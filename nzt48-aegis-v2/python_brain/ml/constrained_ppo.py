"""Risk-Constrained PPO for Safe RL Trading — Book 213.

Numpy-only Proximal Policy Optimization with drawdown-aware risk
shaping. Standard PPO maximizes cumulative reward but ignores tail
risk — a single 8% drawdown in the ISA can trigger emergency flattening.

This module adds risk constraints:
  1. Drawdown penalty: reward -= penalty * max(dd - threshold, 0)^2
  2. Clipped surrogate objective (standard PPO)
  3. Generalized Advantage Estimation (GAE-lambda)
  4. Value function baseline

The agent learns to trade profitably while staying within drawdown
bounds, matching the AEGIS risk doctrine.

Architecture:
  PolicyNetwork: MLP (state -> action probabilities via softmax)
  ValueNetwork: MLP (state -> scalar value estimate)
  ConstrainedPPOAgent: Orchestrates training with risk shaping

Actions: 0=HOLD, 1=BUY, 2=SELL

State persisted to /app/data/ppo/.

Usage:
    from python_brain.ml.constrained_ppo import (
        ConstrainedPPOAgent, PPOConfig, PolicyNetwork, ValueNetwork,
    )
    config = PPOConfig(drawdown_penalty=10.0, max_dd_threshold=0.08)
    agent = ConstrainedPPOAgent(state_dim=32, action_dim=3, config=config)
    action, log_prob = agent.select_action(state)
    shaped_reward = agent._risk_shaped_reward(raw_reward, drawdown_pct=0.05)
    metrics = agent.update(trajectories)
    agent.save("/app/data/ppo/agent.npz")
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("constrained_ppo")

__all__ = [
    "PPOConfig",
    "PolicyNetwork",
    "ValueNetwork",
    "ConstrainedPPOAgent",
]

# ── Constants ──────────────────────────────────────────────────────────

STATE_DIR = Path("/app/data/ppo")
ENTROPY_COEFF = 0.01           # Entropy bonus for exploration
VALUE_LOSS_COEFF = 0.5         # Weight of value loss
MAX_GRAD_NORM = 0.5            # Gradient clipping threshold
PPO_EPOCHS = 4                 # Number of optimization epochs per update


# ── Config ─────────────────────────────────────────────────────────────

@dataclass
class PPOConfig:
    """Configuration for risk-constrained PPO.

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


# ── MLP Utilities ──────────────────────────────────────────────────────

def _he_init(fan_in: int, fan_out: int,
             rng: np.random.Generator) -> np.ndarray:
    """He initialization for weights."""
    std = math.sqrt(2.0 / fan_in)
    return rng.normal(0.0, std, (fan_in, fan_out))


def _relu(x: np.ndarray) -> np.ndarray:
    """ReLU activation."""
    return np.maximum(0.0, x)


def _softmax(x: np.ndarray) -> np.ndarray:
    """Numerically stable softmax."""
    e = np.exp(x - np.max(x, axis=-1, keepdims=True))
    return e / (np.sum(e, axis=-1, keepdims=True) + 1e-10)


def _tanh(x: np.ndarray) -> np.ndarray:
    """Tanh activation."""
    return np.tanh(x)


def _clip_grad(grad: np.ndarray, max_norm: float = MAX_GRAD_NORM) -> np.ndarray:
    """Clip gradient by norm."""
    norm = np.linalg.norm(grad)
    if norm > max_norm:
        return grad * max_norm / norm
    return grad


# ── Policy Network ────────────────────────────────────────────────────

class PolicyNetwork:
    """MLP policy that outputs action probabilities.

    Architecture: Linear -> Tanh -> Linear -> Tanh -> Linear -> Softmax
    """

    def __init__(self, state_dim: int, action_dim: int,
                 hidden: int = 64, seed: int = 42):
        """Initialize policy network.

        Args:
            state_dim: State space dimension.
            action_dim: Number of discrete actions.
            hidden: Hidden layer width.
            seed: Random seed.
        """
        self.state_dim = state_dim
        self.action_dim = action_dim
        self.hidden = hidden
        self._rng = np.random.default_rng(seed)

        # Layer 1: state_dim -> hidden
        self.W1 = _he_init(state_dim, hidden, self._rng)
        self.b1 = np.zeros(hidden)

        # Layer 2: hidden -> hidden
        self.W2 = _he_init(hidden, hidden, self._rng)
        self.b2 = np.zeros(hidden)

        # Layer 3: hidden -> action_dim (logits)
        self.W3 = self._rng.normal(0, 0.01, (hidden, action_dim))
        self.b3 = np.zeros(action_dim)

        # Adam optimizer state
        self._adam_m: Dict[str, Any] = {}
        self._adam_v: Dict[str, Any] = {}
        self._adam_t: int = 0

        for name in ["W1", "b1", "W2", "b2", "W3", "b3"]:
            self._adam_m[name] = np.zeros_like(getattr(self, name))
            self._adam_v[name] = np.zeros_like(getattr(self, name))

    def forward(self, state: np.ndarray) -> np.ndarray:
        """Compute action probabilities.

        Args:
            state: State vector, shape (state_dim,) or (batch, state_dim).

        Returns:
            Action probabilities, shape (action_dim,) or (batch, action_dim).
        """
        single = state.ndim == 1
        if single:
            state = state.reshape(1, -1)

        # Forward pass
        self._z1 = state @ self.W1 + self.b1
        self._h1 = _tanh(self._z1)

        self._z2 = self._h1 @ self.W2 + self.b2
        self._h2 = _tanh(self._z2)

        logits = self._h2 @ self.W3 + self.b3
        probs = _softmax(logits)

        # Cache for backward
        self._state = state
        self._logits = logits
        self._probs = probs

        if single:
            return probs.squeeze(0)
        return probs

    def backward(self, grad_logprob: np.ndarray, actions: np.ndarray,
                 lr: float) -> None:
        """Backward pass through policy network.

        Args:
            grad_logprob: Gradient of the objective w.r.t. log probabilities,
                          shape (batch,).
            actions: Actions taken, shape (batch,).
            lr: Learning rate.
        """
        batch_size = self._state.shape[0]

        # Gradient of log_prob w.r.t. logits (softmax gradient)
        # d(log p_a) / d(logit_j) = 1(j==a) - p_j
        grad_logits = np.copy(self._probs)  # (batch, action_dim)
        for i in range(batch_size):
            grad_logits[i, int(actions[i])] -= 1.0
            # Multiply by the per-sample gradient
            grad_logits[i] *= -grad_logprob[i]

        grad_logits /= batch_size

        # Clip gradients
        grad_logits = _clip_grad(grad_logits, MAX_GRAD_NORM)

        # Layer 3
        grad_W3 = self._h2.T @ grad_logits
        grad_b3 = np.sum(grad_logits, axis=0)
        grad_h2 = grad_logits @ self.W3.T

        # Tanh backward
        grad_z2 = grad_h2 * (1.0 - self._h2 ** 2)

        # Layer 2
        grad_W2 = self._h1.T @ grad_z2
        grad_b2 = np.sum(grad_z2, axis=0)
        grad_h1 = grad_z2 @ self.W2.T

        # Tanh backward
        grad_z1 = grad_h1 * (1.0 - self._h1 ** 2)

        # Layer 1
        grad_W1 = self._state.T @ grad_z1
        grad_b1 = np.sum(grad_z1, axis=0)

        # Adam update
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

    def get_params(self) -> Dict[str, np.ndarray]:
        """Get all parameters as a dict of arrays."""
        return {
            "W1": self.W1.copy(), "b1": self.b1.copy(),
            "W2": self.W2.copy(), "b2": self.b2.copy(),
            "W3": self.W3.copy(), "b3": self.b3.copy(),
        }

    def set_params(self, params: Dict[str, np.ndarray]) -> None:
        """Set all parameters from a dict."""
        for name, val in params.items():
            if hasattr(self, name):
                setattr(self, name, val.copy())


# ── Value Network ─────────────────────────────────────────────────────

class ValueNetwork:
    """MLP critic that estimates state value.

    Architecture: Linear -> Tanh -> Linear -> Tanh -> Linear (scalar output)
    """

    def __init__(self, state_dim: int, hidden: int = 64, seed: int = 42):
        """Initialize value network.

        Args:
            state_dim: State space dimension.
            hidden: Hidden layer width.
            seed: Random seed.
        """
        self.state_dim = state_dim
        self.hidden = hidden
        self._rng = np.random.default_rng(seed)

        self.W1 = _he_init(state_dim, hidden, self._rng)
        self.b1 = np.zeros(hidden)
        self.W2 = _he_init(hidden, hidden, self._rng)
        self.b2 = np.zeros(hidden)
        self.W3 = self._rng.normal(0, 0.01, (hidden, 1))
        self.b3 = np.zeros(1)

        # Adam state
        self._adam_m: Dict[str, Any] = {}
        self._adam_v: Dict[str, Any] = {}
        self._adam_t: int = 0
        for name in ["W1", "b1", "W2", "b2", "W3", "b3"]:
            self._adam_m[name] = np.zeros_like(getattr(self, name))
            self._adam_v[name] = np.zeros_like(getattr(self, name))

    def forward(self, state: np.ndarray) -> float:
        """Estimate state value.

        Args:
            state: State vector, shape (state_dim,) or (batch, state_dim).

        Returns:
            Value estimate (scalar if single state, array if batch).
        """
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

    def forward_batch(self, states: np.ndarray) -> np.ndarray:
        """Batch forward pass.

        Args:
            states: Batch of states, shape (batch, state_dim).

        Returns:
            Value estimates, shape (batch,).
        """
        if states.ndim == 1:
            states = states.reshape(1, -1)

        self._state = states
        self._z1 = states @ self.W1 + self.b1
        self._h1 = _tanh(self._z1)
        self._z2 = self._h1 @ self.W2 + self.b2
        self._h2 = _tanh(self._z2)
        values = (self._h2 @ self.W3 + self.b3).squeeze(-1)
        return values

    def backward(self, grad_value: np.ndarray, lr: float) -> None:
        """Backward pass for value network.

        Args:
            grad_value: Gradient of value loss, shape (batch,).
            lr: Learning rate.
        """
        batch_size = self._state.shape[0]
        grad_out = grad_value.reshape(-1, 1) / batch_size

        # Layer 3
        grad_W3 = self._h2.T @ grad_out
        grad_b3 = np.sum(grad_out, axis=0)
        grad_h2 = grad_out @ self.W3.T

        # Tanh backward
        grad_z2 = grad_h2 * (1.0 - self._h2 ** 2)

        # Layer 2
        grad_W2 = self._h1.T @ grad_z2
        grad_b2 = np.sum(grad_z2, axis=0)
        grad_h1 = grad_z2 @ self.W2.T

        # Tanh backward
        grad_z1 = grad_h1 * (1.0 - self._h1 ** 2)

        # Layer 1
        grad_W1 = self._state.T @ grad_z1
        grad_b1 = np.sum(grad_z1, axis=0)

        # Adam update
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

    def get_params(self) -> Dict[str, np.ndarray]:
        """Get parameters."""
        return {
            "W1": self.W1.copy(), "b1": self.b1.copy(),
            "W2": self.W2.copy(), "b2": self.b2.copy(),
            "W3": self.W3.copy(), "b3": self.b3.copy(),
        }

    def set_params(self, params: Dict[str, np.ndarray]) -> None:
        """Set parameters."""
        for name, val in params.items():
            if hasattr(self, name):
                setattr(self, name, val.copy())


# ── Constrained PPO Agent ─────────────────────────────────────────────

class ConstrainedPPOAgent:
    """Risk-constrained PPO agent for safe trading.

    Combines standard PPO (clipped surrogate objective) with
    drawdown-aware reward shaping. The agent maximizes risk-adjusted
    returns while keeping maximum drawdown below the ISA safety threshold.

    Training loop:
      1. Collect trajectories using current policy
      2. Compute GAE advantages
      3. Risk-shape rewards (penalize near-threshold drawdowns)
      4. PPO update with clipped objective + entropy bonus
      5. Value function update with MSE loss
    """

    def __init__(self, state_dim: int, action_dim: int = 3,
                 config: Optional[PPOConfig] = None):
        """Initialize constrained PPO agent.

        Args:
            state_dim: State space dimension.
            action_dim: Number of discrete actions (HOLD, BUY, SELL).
            config: PPOConfig. Uses defaults if None.
        """
        self._config = config or PPOConfig()
        self.state_dim = state_dim
        self.action_dim = action_dim

        self._rng = np.random.default_rng(self._config.seed)

        # Networks
        self._policy = PolicyNetwork(state_dim, action_dim, hidden=64,
                                      seed=self._config.seed)
        self._value = ValueNetwork(state_dim, hidden=64,
                                    seed=self._config.seed + 1)

        # Tracking
        self._total_steps: int = 0
        self._n_updates: int = 0
        self._recent_losses: List[float] = []
        self._max_drawdown_seen: float = 0.0
        self._episode_returns: List[float] = []

        log.info("ConstrainedPPOAgent: state=%d, actions=%d, clip=%.2f, "
                 "gamma=%.3f, lam=%.3f, dd_penalty=%.1f, dd_threshold=%.2f",
                 state_dim, action_dim, self._config.clip_ratio,
                 self._config.gamma, self._config.lam,
                 self._config.drawdown_penalty, self._config.max_dd_threshold)

    def select_action(self, state: np.ndarray) -> Tuple[int, float]:
        """Select action from the policy.

        Args:
            state: Current state vector.

        Returns:
            Tuple (action_index, log_probability).
        """
        self._total_steps += 1
        probs = self._policy.forward(state)

        # Sample action from categorical distribution
        probs = np.maximum(probs, 1e-8)
        probs = probs / np.sum(probs)

        action = int(self._rng.choice(self.action_dim, p=probs))
        log_prob = float(np.log(probs[action] + 1e-10))

        return action, log_prob

    def get_value(self, state: np.ndarray) -> float:
        """Get value estimate for a state.

        Args:
            state: State vector.

        Returns:
            Value estimate.
        """
        return self._value.forward(state)

    def compute_gae(self, rewards: np.ndarray, values: np.ndarray,
                    dones: np.ndarray) -> np.ndarray:
        """Compute Generalized Advantage Estimation.

        GAE-lambda provides a bias-variance tradeoff for advantage
        estimation. lambda=0 gives TD(0) (high bias, low variance),
        lambda=1 gives Monte Carlo (low bias, high variance).

        A_t = sum_{l=0}^{T-t} (gamma*lambda)^l * delta_{t+l}
        where delta_t = r_t + gamma * V(s_{t+1}) - V(s_t)

        Args:
            rewards: Rewards, shape (T,).
            values: Value estimates V(s_t), shape (T+1,) (includes bootstrap).
            dones: Done flags, shape (T,).

        Returns:
            Advantages, shape (T,).
        """
        T = len(rewards)
        advantages = np.zeros(T)
        gae = 0.0

        gamma = self._config.gamma
        lam = self._config.lam

        for t in reversed(range(T)):
            if t == T - 1:
                next_value = values[T]  # Bootstrap value
            else:
                next_value = values[t + 1]

            delta = rewards[t] + gamma * next_value * (1 - dones[t]) - values[t]
            gae = delta + gamma * lam * (1 - dones[t]) * gae
            advantages[t] = gae

        return advantages

    def _risk_shaped_reward(self, raw_reward: float,
                            drawdown_pct: float) -> float:
        """Apply drawdown-aware risk shaping to rewards.

        Penalizes the agent when drawdown approaches or exceeds the
        ISA safety threshold. The penalty is quadratic to strongly
        discourage near-threshold states.

        Shaped reward = raw_reward - penalty * max(dd - threshold, 0)^2

        Args:
            raw_reward: Original environment reward.
            drawdown_pct: Current drawdown as a fraction (e.g., 0.05 = 5%).

        Returns:
            Risk-shaped reward.
        """
        self._max_drawdown_seen = max(self._max_drawdown_seen, drawdown_pct)

        threshold = self._config.max_dd_threshold
        penalty_coeff = self._config.drawdown_penalty

        if drawdown_pct <= threshold * 0.5:
            # Safe zone: no penalty
            return raw_reward

        if drawdown_pct <= threshold:
            # Warning zone: linear ramp-up penalty
            excess = drawdown_pct - threshold * 0.5
            fraction = excess / (threshold * 0.5)  # 0 to 1
            penalty = penalty_coeff * 0.5 * fraction ** 2
            return raw_reward - penalty

        # Danger zone: quadratic penalty above threshold
        excess = drawdown_pct - threshold
        penalty = penalty_coeff * excess ** 2

        # Extra penalty for being way over threshold
        if drawdown_pct > threshold * 1.5:
            penalty *= 2.0

        shaped = raw_reward - penalty

        if drawdown_pct > threshold:
            log.warning("Drawdown penalty: dd=%.2f%% > threshold=%.2f%%, "
                        "penalty=%.4f, shaped_reward=%.4f",
                        drawdown_pct * 100, threshold * 100,
                        penalty, shaped)

        return shaped

    def update(self, trajectories: Dict[str, np.ndarray]) -> Dict[str, float]:
        """PPO update with risk-constrained objective.

        Performs multiple epochs of minibatch PPO on collected trajectories.

        Args:
            trajectories: Dict with keys:
                - states: (T, state_dim)
                - actions: (T,) integer actions
                - rewards: (T,) raw rewards
                - log_probs: (T,) log probabilities from old policy
                - dones: (T,) done flags
                - drawdowns: (T,) drawdown percentages (optional)

        Returns:
            Training metrics dict.
        """
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

        # Risk-shape rewards
        shaped_rewards = np.array([
            self._risk_shaped_reward(float(raw_rewards[t]), float(drawdowns[t]))
            for t in range(T)
        ])

        # Compute values for all states + bootstrap
        values = np.zeros(T + 1)
        for t in range(T):
            values[t] = self._value.forward(states[t])
        # Bootstrap value for last state
        if T > 0 and not dones[-1]:
            values[T] = self._value.forward(states[-1])

        # Compute GAE advantages
        advantages = self.compute_gae(shaped_rewards, values, dones)

        # Returns = advantages + values (for value function training)
        returns = advantages + values[:T]

        # Normalize advantages
        adv_mean = np.mean(advantages)
        adv_std = np.std(advantages) + 1e-8
        advantages_norm = (advantages - adv_mean) / adv_std

        # PPO epochs
        total_policy_loss = 0.0
        total_value_loss = 0.0
        total_entropy = 0.0
        total_clip_fraction = 0.0

        for epoch in range(PPO_EPOCHS):
            # Shuffle indices
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

                # New policy forward pass
                new_probs = self._policy.forward(b_states)
                new_log_probs = np.log(
                    new_probs[np.arange(b_size), b_actions] + 1e-10
                )

                # Importance sampling ratio
                ratio = np.exp(new_log_probs - b_old_log_probs)

                # Clipped surrogate objective
                surr1 = ratio * b_advantages
                surr2 = np.clip(ratio,
                                1.0 - self._config.clip_ratio,
                                1.0 + self._config.clip_ratio) * b_advantages
                policy_loss = -np.mean(np.minimum(surr1, surr2))

                # Entropy bonus
                entropy = -np.sum(new_probs * np.log(new_probs + 1e-10), axis=1)
                entropy_bonus = np.mean(entropy)

                # Clip fraction (diagnostic)
                clip_fraction = float(np.mean(
                    np.abs(ratio - 1.0) > self._config.clip_ratio
                ))

                # Policy gradient = -(advantage * grad_log_prob) + entropy
                # For our backward pass, we compute the per-sample gradient weight
                clipped_ratio = np.clip(ratio,
                                        1.0 - self._config.clip_ratio,
                                        1.0 + self._config.clip_ratio)
                use_clipped = (surr2 < surr1).astype(np.float64)
                effective_ratio = use_clipped * clipped_ratio + (1 - use_clipped) * ratio

                # Combined gradient signal per sample
                grad_per_sample = effective_ratio * b_advantages + ENTROPY_COEFF

                self._policy.backward(
                    grad_per_sample, b_actions, self._config.lr_policy
                )

                # Value function update
                b_values = self._value.forward_batch(b_states)
                value_loss = np.mean((b_values - b_returns) ** 2)

                # Value gradient
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

        # Track episode return
        self._episode_returns.append(float(np.sum(raw_rewards)))
        if len(self._episode_returns) > 100:
            self._episode_returns = self._episode_returns[-100:]

        metrics = {
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
                 total_entropy / n_batches, total_clip_fraction / n_batches, T)

        return metrics

    def save(self, path: str = "/app/data/ppo/agent.npz") -> None:
        """Save agent state to disk.

        Args:
            path: File path for saved state.
        """
        save_path = Path(path)
        try:
            save_path.parent.mkdir(parents=True, exist_ok=True)

            save_dict: Dict[str, np.ndarray] = {}

            # Policy params
            for name, arr in self._policy.get_params().items():
                save_dict[f"policy_{name}"] = arr

            # Value params
            for name, arr in self._value.get_params().items():
                save_dict[f"value_{name}"] = arr

            # Meta
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
        """Load agent state from disk.

        Args:
            path: File path to load from.
        """
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

            # Restore policy
            policy_params = {}
            for name in ["W1", "b1", "W2", "b2", "W3", "b3"]:
                key = f"policy_{name}"
                if key in data:
                    policy_params[name] = data[key]
            if policy_params:
                self._policy.set_params(policy_params)

            # Restore value
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
            log.info("No saved state at %s — starting fresh", path)
        except Exception as e:
            log.error("Failed to load PPO agent: %s", e)

    @property
    def stats(self) -> Dict[str, Any]:
        """Agent statistics."""
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
