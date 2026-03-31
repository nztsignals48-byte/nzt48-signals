"""Self-Rewarding Deep RL Engine — Book 143.

Self-Rewarding Deep Reinforcement Learning for adaptive trading.
The agent learns to predict its own reward signal, bootstrapping
from sparse expert rewards to generate dense self-reward feedback.

Architecture:
  RewardNetwork: MLP that learns to predict reward from (state, action)
  SRDRLAgent: Double DQN with self-reward augmentation

Key insight: When expert rewards are sparse (e.g., only at trade exit),
the self-reward network provides dense intermediate feedback, accelerating
learning. The effective reward = max(expert_reward, predicted_reward),
ensuring the agent never ignores real signal.

State persisted to /app/data/srdrl/{agent_name}.npz.

Usage:
    from python_brain.ml.srdrl_engine import SRDRLAgent, RewardNetwork
    agent = SRDRLAgent(state_dim=32, action_dim=3)
    action = agent.select_action(state)
    self_reward = agent.compute_self_reward(state, action, outcome)
    metrics = agent.train_step(batch)
    agent.save("/app/data/srdrl/momentum_agent.npz")
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("srdrl_engine")

__all__ = [
    "RewardNetwork",
    "SRDRLAgent",
]

# ── Constants ──────────────────────────────────────────────────────────

STATE_DIR = Path("/app/data/srdrl")
DEFAULT_HIDDEN = 64
DEFAULT_LR = 1e-3
DEFAULT_GAMMA = 0.99
DEFAULT_TAU = 0.005       # Soft update rate for target network
DEFAULT_EPSILON_START = 1.0
DEFAULT_EPSILON_END = 0.05
DEFAULT_EPSILON_DECAY = 5000
REPLAY_CAPACITY = 10000
MIN_REPLAY_SIZE = 64
BATCH_SIZE = 32


# ── MLP Utilities ──────────────────────────────────────────────────────

def _relu(x: np.ndarray) -> np.ndarray:
    """ReLU activation."""
    return np.maximum(0.0, x)


def _relu_grad(x: np.ndarray) -> np.ndarray:
    """ReLU gradient."""
    return (x > 0).astype(np.float64)


def _softmax(x: np.ndarray) -> np.ndarray:
    """Numerically stable softmax."""
    e = np.exp(x - np.max(x, axis=-1, keepdims=True))
    return e / (np.sum(e, axis=-1, keepdims=True) + 1e-10)


def _init_weights(fan_in: int, fan_out: int, rng: np.random.Generator) -> np.ndarray:
    """He initialization."""
    std = math.sqrt(2.0 / fan_in)
    return rng.normal(0.0, std, (fan_in, fan_out))


# ── MLP Layer ──────────────────────────────────────────────────────────

class _MLPLayer:
    """Single dense layer with optional ReLU."""

    def __init__(self, in_dim: int, out_dim: int, rng: np.random.Generator,
                 activation: bool = True):
        self.W = _init_weights(in_dim, out_dim, rng)
        self.b = np.zeros(out_dim)
        self.activation = activation
        # Cache for backprop
        self._input: Optional[np.ndarray] = None
        self._pre_act: Optional[np.ndarray] = None

    def forward(self, x: np.ndarray) -> np.ndarray:
        """Forward pass."""
        self._input = x
        self._pre_act = x @ self.W + self.b
        if self.activation:
            return _relu(self._pre_act)
        return self._pre_act

    def backward(self, grad_out: np.ndarray, lr: float) -> np.ndarray:
        """Backward pass with weight update. Returns gradient w.r.t. input."""
        if self.activation:
            grad_out = grad_out * _relu_grad(self._pre_act)

        grad_W = self._input.T @ grad_out if self._input.ndim > 1 else np.outer(self._input, grad_out)
        grad_b = np.sum(grad_out, axis=0) if grad_out.ndim > 1 else grad_out

        # Gradient clipping
        grad_W = np.clip(grad_W, -1.0, 1.0)
        grad_b = np.clip(grad_b, -1.0, 1.0)

        self.W -= lr * grad_W
        self.b -= lr * grad_b

        return grad_out @ self.W.T

    def get_params(self) -> Tuple[np.ndarray, np.ndarray]:
        """Return copies of weights and biases."""
        return self.W.copy(), self.b.copy()

    def set_params(self, W: np.ndarray, b: np.ndarray) -> None:
        """Set weights and biases."""
        self.W = W.copy()
        self.b = b.copy()

    def soft_update(self, other: '_MLPLayer', tau: float) -> None:
        """Polyak averaging: self = tau * other + (1 - tau) * self."""
        self.W = tau * other.W + (1 - tau) * self.W
        self.b = tau * other.b + (1 - tau) * self.b


# ── MLP Network ────────────────────────────────────────────────────────

class _MLP:
    """Multi-layer perceptron."""

    def __init__(self, dims: List[int], rng: np.random.Generator):
        self.layers: List[_MLPLayer] = []
        for i in range(len(dims) - 1):
            act = i < len(dims) - 2  # No activation on output layer
            self.layers.append(_MLPLayer(dims[i], dims[i + 1], rng, activation=act))

    def forward(self, x: np.ndarray) -> np.ndarray:
        """Forward pass through all layers."""
        for layer in self.layers:
            x = layer.forward(x)
        return x

    def backward(self, grad: np.ndarray, lr: float) -> None:
        """Backward pass through all layers."""
        for layer in reversed(self.layers):
            grad = layer.backward(grad, lr)

    def get_params(self) -> List[Tuple[np.ndarray, np.ndarray]]:
        """Get all layer parameters."""
        return [l.get_params() for l in self.layers]

    def set_params(self, params: List[Tuple[np.ndarray, np.ndarray]]) -> None:
        """Set all layer parameters."""
        for layer, (W, b) in zip(self.layers, params):
            layer.set_params(W, b)

    def soft_update(self, other: '_MLP', tau: float) -> None:
        """Polyak averaging from another network."""
        for self_layer, other_layer in zip(self.layers, other.layers):
            self_layer.soft_update(other_layer, tau)


# ── Replay Buffer ──────────────────────────────────────────────────────

class _ReplayBuffer:
    """Fixed-size circular replay buffer for experience replay."""

    def __init__(self, capacity: int = REPLAY_CAPACITY):
        self.capacity = capacity
        self.buffer: List[Tuple] = []
        self._idx = 0

    def push(self, state: np.ndarray, action: int, reward: float,
             next_state: np.ndarray, done: bool) -> None:
        """Store a transition."""
        transition = (state.copy(), action, reward, next_state.copy(), done)
        if len(self.buffer) < self.capacity:
            self.buffer.append(transition)
        else:
            self.buffer[self._idx] = transition
        self._idx = (self._idx + 1) % self.capacity

    def sample(self, batch_size: int,
               rng: np.random.Generator) -> Tuple[np.ndarray, ...]:
        """Sample a random batch. Returns (states, actions, rewards, next_states, dones)."""
        indices = rng.choice(len(self.buffer), size=batch_size, replace=False)
        states = np.array([self.buffer[i][0] for i in indices])
        actions = np.array([self.buffer[i][1] for i in indices])
        rewards = np.array([self.buffer[i][2] for i in indices])
        next_states = np.array([self.buffer[i][3] for i in indices])
        dones = np.array([self.buffer[i][4] for i in indices], dtype=np.float64)
        return states, actions, rewards, next_states, dones

    def __len__(self) -> int:
        return len(self.buffer)


# ── Reward Network ─────────────────────────────────────────────────────

class RewardNetwork:
    """Learns to predict its own reward signal from (state, action) pairs.

    The network takes concatenated [state, one_hot(action)] as input
    and predicts a scalar reward. Trained against expert (external) rewards.
    """

    def __init__(self, state_dim: int, action_dim: int,
                 hidden: int = DEFAULT_HIDDEN, lr: float = 1e-3,
                 seed: int = 42):
        """Initialize reward prediction network.

        Args:
            state_dim: Dimension of the state vector.
            action_dim: Number of discrete actions.
            hidden: Hidden layer size.
            lr: Learning rate.
            seed: Random seed for reproducibility.
        """
        self.state_dim = state_dim
        self.action_dim = action_dim
        self.lr = lr
        self._rng = np.random.default_rng(seed)

        input_dim = state_dim + action_dim
        self._net = _MLP([input_dim, hidden, hidden // 2, 1], self._rng)

        self._n_updates: int = 0
        self._running_loss: float = 0.0
        log.info("RewardNetwork initialized: state_dim=%d, action_dim=%d, hidden=%d",
                 state_dim, action_dim, hidden)

    def _encode_input(self, state: np.ndarray, action: int) -> np.ndarray:
        """Concatenate state with one-hot action encoding."""
        one_hot = np.zeros(self.action_dim)
        one_hot[action] = 1.0
        if state.ndim == 1:
            return np.concatenate([state, one_hot])
        # Batch case
        batch_one_hot = np.zeros((state.shape[0], self.action_dim))
        batch_one_hot[np.arange(state.shape[0]), action] = 1.0
        return np.concatenate([state, batch_one_hot], axis=1)

    def forward(self, state: np.ndarray, action: int) -> float:
        """Predict reward for a (state, action) pair.

        Args:
            state: State vector of shape (state_dim,).
            action: Action index.

        Returns:
            Predicted reward scalar.
        """
        x = self._encode_input(state, action)
        if x.ndim == 1:
            x = x.reshape(1, -1)
        out = self._net.forward(x)
        return float(out.squeeze())

    def update(self, state: np.ndarray, action: int,
               expert_reward: float) -> float:
        """Train one step against an expert reward.

        Args:
            state: State vector.
            action: Action taken.
            expert_reward: Ground-truth reward from the environment.

        Returns:
            MSE loss for this update.
        """
        x = self._encode_input(state, action)
        if x.ndim == 1:
            x = x.reshape(1, -1)

        predicted = self._net.forward(x)
        target = np.array([[expert_reward]])

        # MSE loss and gradient
        error = predicted - target
        loss = float(np.mean(error ** 2))

        # Backward pass: d(MSE)/d(pred) = 2 * error / n
        grad = 2.0 * error / error.size
        self._net.backward(grad, self.lr)

        self._n_updates += 1
        self._running_loss = 0.95 * self._running_loss + 0.05 * loss
        return loss

    @property
    def stats(self) -> Dict[str, Any]:
        """Training statistics."""
        return {
            "n_updates": self._n_updates,
            "running_loss": round(self._running_loss, 6),
        }


# ── SRDRL Agent ────────────────────────────────────────────────────────

class SRDRLAgent:
    """Self-Rewarding Deep RL Agent with Double DQN.

    Combines a standard Double DQN architecture with a self-rewarding
    mechanism: the reward used for Q-learning is max(expert, predicted),
    providing dense feedback even when expert rewards are sparse.

    Actions:
        0 = HOLD, 1 = BUY, 2 = SELL (configurable via action_dim)
    """

    def __init__(self, state_dim: int, action_dim: int = 3,
                 hidden: int = DEFAULT_HIDDEN, lr: float = DEFAULT_LR,
                 gamma: float = DEFAULT_GAMMA, tau: float = DEFAULT_TAU,
                 seed: int = 42):
        """Initialize SRDRL agent.

        Args:
            state_dim: Dimension of state space.
            action_dim: Number of discrete actions.
            hidden: Hidden layer width.
            lr: Learning rate.
            gamma: Discount factor.
            tau: Soft update rate for target network.
            seed: Random seed.
        """
        self.state_dim = state_dim
        self.action_dim = action_dim
        self.hidden = hidden
        self.lr = lr
        self.gamma = gamma
        self.tau = tau

        self._rng = np.random.default_rng(seed)

        # Double DQN: online + target networks
        dims = [state_dim, hidden, hidden, action_dim]
        self._online_net = _MLP(dims, self._rng)
        self._target_net = _MLP(dims, self._rng)
        self._target_net.set_params(self._online_net.get_params())

        # Self-reward network
        self._reward_net = RewardNetwork(state_dim, action_dim, hidden,
                                         lr=lr * 0.5, seed=seed + 1)

        # Replay buffer
        self._replay = _ReplayBuffer(REPLAY_CAPACITY)

        # Exploration schedule
        self._epsilon = DEFAULT_EPSILON_START
        self._epsilon_end = DEFAULT_EPSILON_END
        self._epsilon_decay = DEFAULT_EPSILON_DECAY
        self._total_steps: int = 0
        self._train_steps: int = 0

        # Tracking
        self._episode_rewards: List[float] = []
        self._recent_losses: List[float] = []

        log.info("SRDRLAgent initialized: state=%d, actions=%d, hidden=%d, "
                 "gamma=%.3f, tau=%.4f", state_dim, action_dim, hidden,
                 gamma, tau)

    def select_action(self, state: np.ndarray) -> int:
        """Select action using epsilon-greedy policy.

        Args:
            state: Current state vector of shape (state_dim,).

        Returns:
            Selected action index.
        """
        self._total_steps += 1

        # Exponential epsilon decay
        self._epsilon = self._epsilon_end + (
            DEFAULT_EPSILON_START - self._epsilon_end
        ) * math.exp(-self._total_steps / self._epsilon_decay)

        # Epsilon-greedy
        if self._rng.random() < self._epsilon:
            return int(self._rng.integers(0, self.action_dim))

        # Greedy: pick action with highest Q-value
        q_values = self._online_net.forward(state.reshape(1, -1))
        return int(np.argmax(q_values.squeeze()))

    def compute_self_reward(self, state: np.ndarray, action: int,
                            outcome: float) -> float:
        """Compute self-reward as max(expert, predicted).

        The self-reward mechanism provides dense feedback by combining
        the reward network's prediction with the actual expert reward.
        This ensures the agent never ignores real signal while benefiting
        from predicted intermediate rewards.

        Args:
            state: State at time of action.
            action: Action taken.
            outcome: Expert/environmental reward.

        Returns:
            Self-reward = max(expert_reward, predicted_reward).
        """
        predicted = self._reward_net.forward(state, action)

        # Train reward network on expert signal
        if abs(outcome) > 1e-8:
            self._reward_net.update(state, action, outcome)

        # Self-reward: take the max to never suppress real signal
        # For negative outcomes, take the min (most negative) to not
        # suppress punishment
        if outcome < 0:
            self_reward = min(outcome, predicted)
        else:
            self_reward = max(outcome, predicted)

        return float(self_reward)

    def store_transition(self, state: np.ndarray, action: int,
                         reward: float, next_state: np.ndarray,
                         done: bool) -> None:
        """Store a transition in the replay buffer.

        Args:
            state: Current state.
            action: Action taken.
            reward: Reward received (self-reward or expert).
            next_state: Next state after action.
            done: Whether episode terminated.
        """
        self._replay.push(state, action, reward, next_state, done)

    def train_step(self, batch: Optional[Dict] = None) -> Dict[str, float]:
        """Perform one Double DQN update step with self-reward.

        If batch is provided, uses it directly. Otherwise samples from
        the replay buffer.

        Args:
            batch: Optional dict with keys: states, actions, rewards,
                   next_states, dones. If None, samples from replay.

        Returns:
            Dict with training metrics: loss, mean_q, epsilon.
        """
        # Check replay buffer has enough samples
        if batch is None and len(self._replay) < MIN_REPLAY_SIZE:
            return {"loss": 0.0, "mean_q": 0.0, "epsilon": self._epsilon,
                    "status": "insufficient_samples"}

        if batch is not None:
            states = np.array(batch["states"])
            actions = np.array(batch["actions"])
            rewards = np.array(batch["rewards"])
            next_states = np.array(batch["next_states"])
            dones = np.array(batch["dones"], dtype=np.float64)
        else:
            states, actions, rewards, next_states, dones = self._replay.sample(
                BATCH_SIZE, self._rng)

        batch_size = states.shape[0]

        # Augment rewards with self-reward
        augmented_rewards = np.copy(rewards)
        for i in range(batch_size):
            predicted_r = self._reward_net.forward(states[i], int(actions[i]))
            if rewards[i] < 0:
                augmented_rewards[i] = min(rewards[i], predicted_r)
            else:
                augmented_rewards[i] = max(rewards[i], predicted_r)

        # Double DQN: online net selects, target net evaluates
        online_q_next = self._online_net.forward(next_states)
        best_actions = np.argmax(online_q_next, axis=1)

        target_q_next = self._target_net.forward(next_states)
        target_q_values = target_q_next[np.arange(batch_size), best_actions]

        # Bellman target
        targets = augmented_rewards + self.gamma * target_q_values * (1.0 - dones)

        # Current Q-values
        current_q = self._online_net.forward(states)
        current_q_selected = current_q[np.arange(batch_size), actions.astype(int)]

        # TD error
        td_errors = current_q_selected - targets
        loss = float(np.mean(td_errors ** 2))

        # Backprop: gradient of MSE w.r.t. Q-values
        grad = np.zeros_like(current_q)
        grad[np.arange(batch_size), actions.astype(int)] = (
            2.0 * td_errors / batch_size
        )
        self._online_net.backward(grad, self.lr)

        # Soft update target network
        self._target_net.soft_update(self._online_net, self.tau)

        # Train reward network on batch
        for i in range(min(batch_size, 8)):
            self._reward_net.update(states[i], int(actions[i]), float(rewards[i]))

        self._train_steps += 1
        self._recent_losses.append(loss)
        if len(self._recent_losses) > 100:
            self._recent_losses = self._recent_losses[-100:]

        mean_q = float(np.mean(current_q_selected))

        return {
            "loss": round(loss, 6),
            "mean_q": round(mean_q, 4),
            "epsilon": round(self._epsilon, 4),
            "train_steps": self._train_steps,
            "replay_size": len(self._replay),
            "reward_net_loss": round(self._reward_net._running_loss, 6),
            "avg_loss_100": round(float(np.mean(self._recent_losses)), 6),
        }

    def save(self, path: str = "/app/data/srdrl/agent.npz") -> None:
        """Save agent state to disk.

        Args:
            path: File path for the saved state.
        """
        save_path = Path(path)
        try:
            save_path.parent.mkdir(parents=True, exist_ok=True)

            # Collect all parameters
            online_params = self._online_net.get_params()
            target_params = self._target_net.get_params()
            reward_params = self._reward_net._net.get_params()

            save_dict = {
                "state_dim": self.state_dim,
                "action_dim": self.action_dim,
                "hidden": self.hidden,
                "total_steps": self._total_steps,
                "train_steps": self._train_steps,
                "epsilon": self._epsilon,
            }

            # Flatten params for numpy savez
            flat = {}
            for prefix, params_list in [("online", online_params),
                                         ("target", target_params),
                                         ("reward", reward_params)]:
                for i, (W, b_vec) in enumerate(params_list):
                    flat[f"{prefix}_W{i}"] = W
                    flat[f"{prefix}_b{i}"] = b_vec

            flat["meta"] = np.array([
                self.state_dim, self.action_dim, self.hidden,
                self._total_steps, self._train_steps,
            ])
            flat["epsilon_arr"] = np.array([self._epsilon])

            np.savez(str(save_path), **flat)
            log.info("SRDRLAgent saved to %s (steps=%d, train=%d)",
                     path, self._total_steps, self._train_steps)
        except Exception as e:
            log.error("Failed to save SRDRLAgent to %s: %s", path, e)

    def load(self, path: str = "/app/data/srdrl/agent.npz") -> None:
        """Load agent state from disk.

        Args:
            path: File path to load from.
        """
        try:
            data = np.load(path, allow_pickle=False)

            meta = data["meta"]
            saved_state_dim = int(meta[0])
            saved_action_dim = int(meta[1])

            if saved_state_dim != self.state_dim or saved_action_dim != self.action_dim:
                log.warning("Dimension mismatch: saved=(%d,%d), current=(%d,%d) — skipping load",
                            saved_state_dim, saved_action_dim,
                            self.state_dim, self.action_dim)
                return

            self._total_steps = int(meta[3])
            self._train_steps = int(meta[4])
            self._epsilon = float(data["epsilon_arr"][0])

            # Restore network params
            for prefix, net in [("online", self._online_net),
                                ("target", self._target_net),
                                ("reward", self._reward_net._net)]:
                params = []
                i = 0
                while f"{prefix}_W{i}" in data:
                    W = data[f"{prefix}_W{i}"]
                    b_vec = data[f"{prefix}_b{i}"]
                    params.append((W, b_vec))
                    i += 1
                if params:
                    net.set_params(params)

            log.info("SRDRLAgent loaded from %s (steps=%d, train=%d, eps=%.4f)",
                     path, self._total_steps, self._train_steps, self._epsilon)
        except FileNotFoundError:
            log.info("No saved state at %s — starting fresh", path)
        except Exception as e:
            log.error("Failed to load SRDRLAgent from %s: %s", path, e)

    @property
    def stats(self) -> Dict[str, Any]:
        """Agent statistics summary."""
        return {
            "total_steps": self._total_steps,
            "train_steps": self._train_steps,
            "epsilon": round(self._epsilon, 4),
            "replay_size": len(self._replay),
            "avg_loss": round(float(np.mean(self._recent_losses)), 6) if self._recent_losses else 0.0,
            "reward_net": self._reward_net.stats,
        }
