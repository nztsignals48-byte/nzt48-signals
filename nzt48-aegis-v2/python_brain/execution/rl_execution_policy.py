"""RL-based Execution Scheduling — Book 101.

Combines Almgren-Chriss optimal execution with a simple policy gradient
(REINFORCE) to learn adaptive execution strategies from fill outcomes.

Architecture:
  - AlmgrenChrissModel: analytical optimal trajectory (risk-averse TWAP/VWAP hybrid)
  - ExecutionPolicy: tabular policy mapping discretized states to action probabilities
  - ExecutionScheduler: orchestrator that produces time-sliced order plans

The policy learns from implementation shortfall rewards: how much better/worse
was our execution vs. the arrival-price benchmark. Over time it adapts to
market microstructure (wider spreads → more passive, high urgency → aggressive).

State: /app/data/execution_policy.json

Bridge.py integration:
    try:
        from python_brain.execution.rl_execution_policy import (
            ExecutionScheduler, ExecutionPolicy, AlmgrenChrissModel,
        )
    except ImportError:
        pass
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger(__name__)

__all__ = [
    "ExecutionState",
    "ExecutionAction",
    "AlmgrenChrissModel",
    "ExecutionPolicy",
    "ExecutionScheduler",
]

# ── Paths ──────────────────────────────────────────────────────────────
POLICY_PATH = Path("/app/data/execution_policy.json")

# ── Constants ──────────────────────────────────────────────────────────
N_STATE_BUCKETS = 5           # Discretization buckets per dimension
LEARNING_RATE = 0.01
GAMMA = 0.99                  # Discount factor
ENTROPY_BONUS = 0.01          # Encourage exploration
MIN_SLICE_SHARES = 1          # Minimum shares per slice
DEFAULT_URGENCY = 0.5         # Mid urgency
TEMPERATURE = 1.0             # Softmax temperature for action selection


# ── Data Structures ────────────────────────────────────────────────────

@dataclass
class ExecutionState:
    """Observable state for the execution policy.

    Attributes:
        remaining_shares: shares left to execute
        time_remaining: fraction of execution window remaining (0-1)
        spread: current bid-ask spread as fraction of mid price
        volume: recent volume relative to ADV (0-inf)
        volatility: recent realized vol (annualized)
        urgency: how urgently we need to complete (0=patient, 1=immediate)
    """
    remaining_shares: float
    time_remaining: float
    spread: float
    volume: float
    volatility: float
    urgency: float = DEFAULT_URGENCY

    def to_bucket_key(self) -> Tuple[int, ...]:
        """Discretize state into bucket indices for tabular policy."""
        def _bucket(val: float, lo: float, hi: float) -> int:
            clamped = max(lo, min(hi, val))
            normalized = (clamped - lo) / (hi - lo + 1e-12)
            return min(int(normalized * N_STATE_BUCKETS), N_STATE_BUCKETS - 1)

        return (
            _bucket(self.remaining_shares, 0.0, 1.0),   # normalized to [0,1]
            _bucket(self.time_remaining, 0.0, 1.0),
            _bucket(self.spread, 0.0, 0.02),             # 0-2% spread
            _bucket(self.volume, 0.0, 3.0),              # 0-3x ADV
            _bucket(self.urgency, 0.0, 1.0),
        )

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)


class ExecutionAction(Enum):
    """Discrete execution actions."""
    AGGRESSIVE = "aggressive"       # Market order — immediate fill, pay spread
    PASSIVE = "passive"             # Limit order at mid/near — may not fill
    WAIT = "wait"                   # Do nothing this slice, wait for better conditions
    SLICE_SMALL = "slice_small"     # Small limit order (25% of remaining per-slice)
    SLICE_LARGE = "slice_large"     # Large limit order (75% of remaining per-slice)


ACTION_LIST = list(ExecutionAction)
N_ACTIONS = len(ACTION_LIST)


# ── Almgren-Chriss Optimal Trajectory ──────────────────────────────────

class AlmgrenChrissModel:
    """Almgren-Chriss (2001) optimal execution trajectory.

    Minimizes E[cost] + lambda * Var[cost] for a risk-averse trader
    liquidating `total_shares` over time horizon T.

    The optimal trajectory balances:
      - Temporary impact (eta): cost per unit of trading rate
      - Permanent impact (gamma_perm): cost per share traded total
      - Risk aversion (lambda_risk): penalty on execution risk (variance)
      - Volatility (sigma): drives the variance of remaining inventory

    Parameters:
        gamma_perm: permanent impact coefficient (default 0.01)
        eta: temporary impact coefficient (default 0.05)
    """

    def __init__(self, gamma_perm: float = 0.01, eta: float = 0.05) -> None:
        self.gamma_perm = gamma_perm
        self.eta = eta

    def optimal_trajectory(
        self,
        total_shares: float,
        T: float,
        sigma: float,
        eta: Optional[float] = None,
        lambda_risk: float = 1e-4,
        n_steps: int = 20,
    ) -> List[Dict[str, float]]:
        """Compute optimal execution trajectory.

        Args:
            total_shares: total number of shares to execute
            T: time horizon in minutes
            sigma: annualized volatility
            eta: temporary impact coefficient (overrides instance default)
            lambda_risk: risk aversion parameter
            n_steps: number of time slices

        Returns:
            List of dicts with keys: time, shares, cumulative, rate
        """
        if total_shares <= 0 or T <= 0:
            return []

        eta_eff = eta if eta is not None else self.eta

        # Convert sigma to per-minute
        sigma_min = sigma / math.sqrt(252 * 390)  # 390 trading minutes/day

        # Almgren-Chriss kappa parameter
        # kappa = sqrt(lambda_risk * sigma^2 / eta)
        denom = max(eta_eff, 1e-12)
        kappa_sq = lambda_risk * (sigma_min ** 2) / denom
        kappa = math.sqrt(max(kappa_sq, 1e-12))

        dt = T / n_steps
        schedule: List[Dict[str, float]] = []
        cumulative = 0.0

        # Sinh-based trajectory from Almgren-Chriss
        sinh_kT = math.sinh(kappa * T)
        if abs(sinh_kT) < 1e-12:
            # Degenerate case: linear (TWAP)
            shares_per_slice = total_shares / n_steps
            for i in range(n_steps):
                t = (i + 0.5) * dt
                cumulative += shares_per_slice
                schedule.append({
                    "time": round(t, 4),
                    "shares": round(shares_per_slice, 4),
                    "cumulative": round(cumulative, 4),
                    "rate": round(shares_per_slice / dt, 4),
                })
            return schedule

        for i in range(n_steps):
            t_start = i * dt
            t_end = (i + 1) * dt

            # Remaining inventory at t: X(t) = X0 * sinh(kappa*(T-t)) / sinh(kappa*T)
            remaining_start = total_shares * math.sinh(kappa * (T - t_start)) / sinh_kT
            remaining_end = total_shares * math.sinh(kappa * (T - t_end)) / sinh_kT

            shares_this_slice = remaining_start - remaining_end
            shares_this_slice = max(shares_this_slice, 0.0)
            cumulative += shares_this_slice

            schedule.append({
                "time": round((t_start + t_end) / 2, 4),
                "shares": round(shares_this_slice, 4),
                "cumulative": round(min(cumulative, total_shares), 4),
                "rate": round(shares_this_slice / dt, 4) if dt > 0 else 0.0,
            })

        # Ensure total shares sum is correct (numerical rounding)
        total_scheduled = sum(s["shares"] for s in schedule)
        if total_scheduled > 0 and abs(total_scheduled - total_shares) > 0.01:
            ratio = total_shares / total_scheduled
            cum = 0.0
            for s in schedule:
                s["shares"] = round(s["shares"] * ratio, 4)
                cum += s["shares"]
                s["cumulative"] = round(cum, 4)

        return schedule

    def expected_cost(
        self,
        total_shares: float,
        T: float,
        sigma: float,
        lambda_risk: float = 1e-4,
    ) -> Dict[str, float]:
        """Estimate total expected execution cost.

        Returns:
            Dict with temporary_cost, permanent_cost, risk_cost, total
        """
        if total_shares <= 0 or T <= 0:
            return {"temporary_cost": 0.0, "permanent_cost": 0.0,
                    "risk_cost": 0.0, "total": 0.0}

        sigma_min = sigma / math.sqrt(252 * 390)

        # Permanent impact
        perm_cost = 0.5 * self.gamma_perm * total_shares ** 2

        # Temporary impact ~ eta * X0^2 / T (simplified)
        temp_cost = self.eta * (total_shares ** 2) / max(T, 1e-12)

        # Risk cost ~ lambda * sigma^2 * X0^2 * T / 3 (for TWAP)
        risk_cost = lambda_risk * (sigma_min ** 2) * (total_shares ** 2) * T / 3.0

        return {
            "temporary_cost": round(perm_cost, 6),
            "permanent_cost": round(temp_cost, 6),
            "risk_cost": round(risk_cost, 6),
            "total": round(perm_cost + temp_cost + risk_cost, 6),
        }


# ── Tabular Execution Policy (REINFORCE) ──────────────────────────────

class ExecutionPolicy:
    """Tabular policy mapping discretized execution states to action probabilities.

    Uses REINFORCE (Williams 1992) policy gradient to update action
    preferences based on implementation shortfall rewards.

    The policy table maps state bucket keys to log-preference vectors.
    Action probabilities computed via softmax.
    """

    def __init__(self, learning_rate: float = LEARNING_RATE) -> None:
        self.lr = learning_rate
        # policy_table: {str(bucket_key): np.ndarray of shape (N_ACTIONS,)}
        self.policy_table: Dict[str, Any] = {}
        self.episode_log: List[Dict[str, Any]] = []  # (state, action_idx, reward)
        self.total_updates: int = 0
        self.cumulative_reward: float = 0.0
        self._load()

    def _get_preferences(self, key: str) -> np.ndarray:
        """Get log-preferences for a state, initializing uniformly if needed."""
        if key not in self.policy_table:
            self.policy_table[key] = np.zeros(N_ACTIONS, dtype=np.float64)
        return self.policy_table[key]

    def _softmax(self, logits: np.ndarray) -> np.ndarray:
        """Numerically stable softmax."""
        shifted = logits - np.max(logits)
        exp_vals = np.exp(shifted / max(TEMPERATURE, 1e-8))
        probs = exp_vals / (np.sum(exp_vals) + 1e-12)
        return probs

    def action_probabilities(self, state: ExecutionState) -> np.ndarray:
        """Get action probabilities for a given state.

        Args:
            state: current execution state

        Returns:
            Array of probabilities, one per ExecutionAction
        """
        key = str(state.to_bucket_key())
        prefs = self._get_preferences(key)
        return self._softmax(prefs)

    def select_action(self, state: ExecutionState) -> ExecutionAction:
        """Sample an action from the policy distribution.

        Args:
            state: current execution state

        Returns:
            Selected ExecutionAction
        """
        probs = self.action_probabilities(state)
        action_idx = int(np.random.choice(N_ACTIONS, p=probs))
        action = ACTION_LIST[action_idx]

        # Log for policy gradient update
        self.episode_log.append({
            "state_key": str(state.to_bucket_key()),
            "action_idx": action_idx,
            "probs": probs.copy(),
        })

        log.debug("ExecPolicy: state=%s action=%s probs=%s",
                  state.to_bucket_key(), action.value, probs.round(3))
        return action

    def update(self, state: ExecutionState, action: ExecutionAction, reward: float) -> None:
        """Single-step REINFORCE update.

        Updates log-preferences: theta += lr * reward * (indicator - pi(a|s))

        Args:
            state: the state in which action was taken
            action: the action taken
            reward: implementation shortfall reward (higher = better execution)
        """
        key = str(state.to_bucket_key())
        prefs = self._get_preferences(key)
        probs = self._softmax(prefs)
        action_idx = ACTION_LIST.index(action)

        # Policy gradient: d log pi(a|s) / d theta = (I[a] - pi(a|s))
        grad = -probs.copy()
        grad[action_idx] += 1.0

        # REINFORCE update with entropy bonus
        entropy = -np.sum(probs * np.log(probs + 1e-12))
        entropy_grad = -(np.log(probs + 1e-12) + 1.0)

        prefs += self.lr * (reward * grad + ENTROPY_BONUS * entropy_grad)

        # Clip preferences to prevent overflow
        np.clip(prefs, -10.0, 10.0, out=prefs)

        self.policy_table[key] = prefs
        self.total_updates += 1
        self.cumulative_reward += reward

        log.debug("ExecPolicy update: key=%s action=%s reward=%.4f entropy=%.4f",
                  key, action.value, reward, entropy)

    def update_episode(self, rewards: List[float]) -> None:
        """Batch REINFORCE update for a full execution episode.

        Applies discounted returns to each logged (state, action) pair.

        Args:
            rewards: list of rewards, one per step in episode_log
        """
        if not self.episode_log or not rewards:
            return

        n = min(len(self.episode_log), len(rewards))

        # Compute discounted returns
        returns = np.zeros(n, dtype=np.float64)
        running = 0.0
        for t in range(n - 1, -1, -1):
            running = rewards[t] + GAMMA * running
            returns[t] = running

        # Normalize returns for stability
        if n > 1:
            ret_std = returns.std()
            if ret_std > 1e-8:
                returns = (returns - returns.mean()) / ret_std

        # Apply gradient updates
        for t in range(n):
            entry = self.episode_log[t]
            key = entry["state_key"]
            action_idx = entry["action_idx"]
            prefs = self._get_preferences(key)
            probs = self._softmax(prefs)

            grad = -probs.copy()
            grad[action_idx] += 1.0

            prefs += self.lr * returns[t] * grad
            np.clip(prefs, -10.0, 10.0, out=prefs)
            self.policy_table[key] = prefs

        self.total_updates += n
        self.cumulative_reward += sum(rewards[:n])

        log.info("ExecPolicy episode update: %d steps, mean_return=%.4f",
                 n, returns.mean())

        self.episode_log.clear()
        self._save()

    @staticmethod
    def compute_reward(
        execution_cost: float,
        benchmark_cost: float,
        max_reward: float = 2.0,
    ) -> float:
        """Compute implementation shortfall reward.

        Positive reward = we did better than benchmark (lower cost).
        Negative reward = we did worse.

        Args:
            execution_cost: actual execution cost (bps or fraction)
            benchmark_cost: benchmark cost (e.g., arrival price cost)
            max_reward: clip reward magnitude

        Returns:
            Reward value, clipped to [-max_reward, max_reward]
        """
        if abs(benchmark_cost) < 1e-12:
            shortfall = -execution_cost
        else:
            # Positive when execution_cost < benchmark_cost
            shortfall = (benchmark_cost - execution_cost) / abs(benchmark_cost)

        reward = max(-max_reward, min(max_reward, shortfall))
        return reward

    def _save(self) -> None:
        """Persist policy table to disk."""
        try:
            POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "policy_table": {
                    k: v.tolist() for k, v in self.policy_table.items()
                },
                "total_updates": self.total_updates,
                "cumulative_reward": self.cumulative_reward,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
            tmp = POLICY_PATH.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            tmp.replace(POLICY_PATH)
            log.debug("ExecPolicy saved: %d states, %d updates",
                      len(self.policy_table), self.total_updates)
        except Exception as e:
            log.error("ExecPolicy save failed: %s", e)

    def _load(self) -> None:
        """Load policy table from disk if available."""
        if not POLICY_PATH.exists():
            log.info("ExecPolicy: no saved policy at %s, starting fresh", POLICY_PATH)
            return
        try:
            with open(POLICY_PATH) as f:
                data = json.load(f)
            table = data.get("policy_table", {})
            self.policy_table = {
                k: np.array(v, dtype=np.float64) for k, v in table.items()
            }
            self.total_updates = data.get("total_updates", 0)
            self.cumulative_reward = data.get("cumulative_reward", 0.0)
            log.info("ExecPolicy loaded: %d states, %d updates",
                     len(self.policy_table), self.total_updates)
        except Exception as e:
            log.error("ExecPolicy load failed: %s — starting fresh", e)
            self.policy_table = {}

    def get_stats(self) -> Dict[str, Any]:
        """Return policy statistics."""
        return {
            "n_states_visited": len(self.policy_table),
            "total_updates": self.total_updates,
            "cumulative_reward": round(self.cumulative_reward, 4),
            "avg_reward": round(self.cumulative_reward / max(self.total_updates, 1), 4),
        }


# ── Execution Scheduler (Orchestrator) ─────────────────────────────────

class ExecutionScheduler:
    """Orchestrator combining Almgren-Chriss trajectory with RL policy.

    Workflow:
      1. AC model generates baseline optimal trajectory
      2. RL policy adapts each slice (aggressive/passive/wait/size)
      3. On fill, compute reward and update policy

    Usage:
        scheduler = ExecutionScheduler()
        plan = scheduler.schedule_order(
            total_shares=100, urgency=0.7,
            market_state={"spread": 0.001, "volume_ratio": 1.2, "volatility": 0.25}
        )
        # Execute plan slices, then report fills:
        scheduler.on_fill(fill_data)
    """

    def __init__(
        self,
        ac_model: Optional[AlmgrenChrissModel] = None,
        policy: Optional[ExecutionPolicy] = None,
    ) -> None:
        self.ac_model = ac_model or AlmgrenChrissModel()
        self.policy = policy or ExecutionPolicy()
        self.active_orders: Dict[str, Dict[str, Any]] = {}
        self._order_counter = 0

    def schedule_order(
        self,
        total_shares: float,
        urgency: float = DEFAULT_URGENCY,
        market_state: Optional[Dict[str, float]] = None,
        time_horizon_minutes: float = 30.0,
        sigma: float = 0.25,
    ) -> List[Dict[str, Any]]:
        """Generate a time-sliced execution plan.

        Args:
            total_shares: total shares to execute
            urgency: 0=patient, 1=immediate
            market_state: dict with 'spread', 'volume_ratio', 'volatility'
            time_horizon_minutes: execution window in minutes
            sigma: annualized volatility

        Returns:
            List of order slices with time, shares, action, order_type
        """
        if total_shares <= 0:
            return []

        ms = market_state or {}
        spread = ms.get("spread", 0.001)
        volume_ratio = ms.get("volume_ratio", 1.0)
        volatility = ms.get("volatility", sigma)

        # Adjust time horizon by urgency (higher urgency → shorter window)
        effective_horizon = time_horizon_minutes * max(0.1, 1.0 - 0.8 * urgency)

        # Step 1: Almgren-Chriss baseline trajectory
        n_slices = max(3, min(50, int(effective_horizon / 1.5)))  # ~1.5 min per slice
        ac_schedule = self.ac_model.optimal_trajectory(
            total_shares=total_shares,
            T=effective_horizon,
            sigma=volatility,
            lambda_risk=1e-4 * (1 + urgency),  # More risk-averse when urgent
            n_steps=n_slices,
        )

        if not ac_schedule:
            # Fallback: single aggressive slice
            return [{
                "slice_idx": 0,
                "time_offset_min": 0.0,
                "shares": total_shares,
                "action": ExecutionAction.AGGRESSIVE.value,
                "order_type": "market",
            }]

        # Step 2: RL policy adapts each slice
        plan: List[Dict[str, Any]] = []
        remaining = total_shares

        for i, ac_slice in enumerate(ac_schedule):
            if remaining <= 0:
                break

            time_frac = 1.0 - (i / max(len(ac_schedule), 1))
            remaining_frac = remaining / max(total_shares, 1e-12)

            state = ExecutionState(
                remaining_shares=remaining_frac,
                time_remaining=time_frac,
                spread=spread,
                volume=volume_ratio,
                volatility=volatility,
                urgency=urgency,
            )

            action = self.policy.select_action(state)
            base_shares = ac_slice["shares"]

            # Map RL action to actual order parameters
            order_shares, order_type = self._action_to_order(
                action, base_shares, remaining
            )

            if order_shares > 0:
                plan.append({
                    "slice_idx": i,
                    "time_offset_min": ac_slice["time"],
                    "shares": round(order_shares, 4),
                    "action": action.value,
                    "order_type": order_type,
                    "state": state.to_dict(),
                })
                remaining -= order_shares

        # Track active order
        self._order_counter += 1
        order_id = f"exec_{self._order_counter}_{int(time.time())}"
        self.active_orders[order_id] = {
            "total_shares": total_shares,
            "plan": plan,
            "filled_shares": 0.0,
            "total_cost": 0.0,
            "arrival_price": ms.get("arrival_price", 0.0),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        log.info("ExecutionScheduler: created plan %s — %d slices for %.0f shares, "
                 "urgency=%.2f, horizon=%.1fmin",
                 order_id, len(plan), total_shares, urgency, effective_horizon)

        return plan

    def on_fill(self, fill_data: Dict[str, Any]) -> Optional[Dict[str, float]]:
        """Process a fill event and update RL policy.

        Args:
            fill_data: dict with keys:
                - order_id: str
                - filled_shares: float
                - fill_price: float
                - arrival_price: float (benchmark)
                - spread_cost: float (bps)
                - slice_idx: int

        Returns:
            Dict with reward info, or None if order_id not found
        """
        order_id = fill_data.get("order_id", "")
        if order_id not in self.active_orders:
            log.warning("ExecutionScheduler: unknown order_id %s", order_id)
            return None

        order = self.active_orders[order_id]
        filled = fill_data.get("filled_shares", 0.0)
        fill_price = fill_data.get("fill_price", 0.0)
        arrival_price = fill_data.get("arrival_price", order.get("arrival_price", 0.0))

        order["filled_shares"] += filled

        # Compute implementation shortfall for this fill
        if arrival_price > 0 and fill_price > 0:
            execution_cost = abs(fill_price - arrival_price) / arrival_price
        else:
            execution_cost = fill_data.get("spread_cost", 0.0) / 10000.0

        # Benchmark: what would TWAP cost?
        benchmark_cost = fill_data.get("benchmark_cost", execution_cost * 1.2)

        reward = ExecutionPolicy.compute_reward(execution_cost, benchmark_cost)

        # Find the state/action for this slice and update
        slice_idx = fill_data.get("slice_idx", 0)
        plan = order.get("plan", [])
        if slice_idx < len(plan):
            slice_info = plan[slice_idx]
            state_dict = slice_info.get("state", {})
            state = ExecutionState(**state_dict)
            action = ExecutionAction(slice_info.get("action", "aggressive"))
            self.policy.update(state, action, reward)

        # Check if order is complete
        if order["filled_shares"] >= order["total_shares"] * 0.99:
            log.info("ExecutionScheduler: order %s completed. Total filled=%.0f",
                     order_id, order["filled_shares"])
            del self.active_orders[order_id]
            self.policy._save()

        return {
            "reward": reward,
            "execution_cost_bps": round(execution_cost * 10000, 2),
            "benchmark_cost_bps": round(benchmark_cost * 10000, 2),
            "shortfall_bps": round((execution_cost - benchmark_cost) * 10000, 2),
        }

    def _action_to_order(
        self,
        action: ExecutionAction,
        base_shares: float,
        remaining: float,
    ) -> Tuple[float, str]:
        """Convert RL action to concrete order parameters.

        Args:
            action: the selected execution action
            base_shares: Almgren-Chriss suggested shares for this slice
            remaining: total shares still to execute

        Returns:
            (shares_to_trade, order_type)
        """
        if action == ExecutionAction.AGGRESSIVE:
            shares = min(base_shares * 1.5, remaining)
            return (max(shares, MIN_SLICE_SHARES), "market")

        elif action == ExecutionAction.PASSIVE:
            shares = min(base_shares, remaining)
            return (max(shares, MIN_SLICE_SHARES), "limit")

        elif action == ExecutionAction.WAIT:
            return (0.0, "none")

        elif action == ExecutionAction.SLICE_SMALL:
            shares = min(base_shares * 0.25, remaining)
            return (max(shares, MIN_SLICE_SHARES) if shares > 0.5 else 0.0, "limit")

        elif action == ExecutionAction.SLICE_LARGE:
            shares = min(base_shares * 0.75, remaining)
            return (max(shares, MIN_SLICE_SHARES), "limit")

        else:
            return (min(base_shares, remaining), "limit")

    def get_status(self) -> Dict[str, Any]:
        """Return scheduler status including active orders and policy stats."""
        return {
            "active_orders": len(self.active_orders),
            "policy_stats": self.policy.get_stats(),
            "orders": {
                oid: {
                    "total_shares": o["total_shares"],
                    "filled_shares": o["filled_shares"],
                    "n_slices": len(o.get("plan", [])),
                    "created_at": o.get("created_at", ""),
                }
                for oid, o in self.active_orders.items()
            },
        }
