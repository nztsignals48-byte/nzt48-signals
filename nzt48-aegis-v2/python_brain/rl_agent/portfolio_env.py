"""rl_agent/portfolio_env.py — Book 33: RL Portfolio Agent.

Gymnasium environment for PPO-based portfolio allocation.
Shadow mode + circuit breakers + safety hierarchy.

Safety: Risk arbiter > human > rules > RL agent (strict hierarchy).
RL agent NEVER has hard authority over orders.
"""

import math
import logging
from collections import deque
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

N_STRATEGIES = 7
STATE_DIM = 80
MIN_WEIGHT = 0.05
MAX_WEIGHT = 0.30
MAX_INVESTABLE = 0.75
MAX_DAILY_TURNOVER = 0.20
MAX_SINGLE_CHANGE = 0.10

STRATEGY_NAMES = [
    "Reversion", "NightRider", "TrendSurfer", "VolHarvester",
    "EventSniper", "CrisisAlpha", "MetaRotator",
]

# Authority levels for RL blending
AUTHORITY_BLEND = {
    0: 0.0,   # Observe only
    1: 0.0,   # Suggest only (logged, not applied)
    2: 0.10,  # 10% blend
    3: 0.30,  # 30% blend
    4: 0.50,  # 50% blend (max)
}


# ── Action Transformation ──────────────────────────────────────────────

def transform_action(raw_action: np.ndarray) -> np.ndarray:
    """Convert unbounded network output to valid allocation weights.

    raw_action: (N_STRATEGIES,) unbounded floats
    Returns: (N_STRATEGIES,) weights in [MIN_WEIGHT, MAX_WEIGHT], sum <= MAX_INVESTABLE
    """
    # Sigmoid to [0, 1]
    sigmoid = 1.0 / (1.0 + np.exp(-np.clip(raw_action, -10, 10)))
    # Scale to [MIN_WEIGHT, MAX_WEIGHT]
    weights = MIN_WEIGHT + sigmoid * (MAX_WEIGHT - MIN_WEIGHT)

    # Enforce sum constraint
    total = weights.sum()
    if total > MAX_INVESTABLE:
        excess = total - MAX_INVESTABLE
        weights = weights - excess * (weights / total)
        weights = np.clip(weights, MIN_WEIGHT, MAX_WEIGHT)

    return weights


def apply_turnover_limit(current: np.ndarray, target: np.ndarray,
                         max_total: float = MAX_DAILY_TURNOVER,
                         max_single: float = MAX_SINGLE_CHANGE) -> np.ndarray:
    """Limit turnover between current and target allocations."""
    delta = target - current
    # Per-strategy clip
    delta = np.clip(delta, -max_single, max_single)
    # Total turnover clip
    total_turnover = np.abs(delta).sum()
    if total_turnover > max_total:
        delta = delta * (max_total / total_turnover)
    return current + delta


# ── Reward Components ──────────────────────────────────────────────────

class DifferentialSharpeReward:
    """Differential Sharpe Ratio reward (Moody & Saffell).

    Tracks exponential moving moments of returns and computes
    instantaneous contribution to Sharpe.
    """

    def __init__(self, eta: float = 0.01):
        self.eta = eta
        self.A = 0.0  # First moment
        self.B = 0.0  # Second moment

    def compute(self, daily_return: float) -> float:
        delta_A = daily_return - self.A
        delta_B = daily_return ** 2 - self.B

        if self.B - self.A ** 2 > 1e-12:
            dsr = (self.B * delta_A - 0.5 * self.A * delta_B) / \
                  (self.B - self.A ** 2) ** 1.5
        else:
            dsr = 0.0

        self.A += self.eta * delta_A
        self.B += self.eta * delta_B
        return float(dsr)

    def reset(self):
        self.A = 0.0
        self.B = 0.0


def drawdown_penalty(current_dd: float, threshold: float = 0.03,
                     scale: float = 10.0) -> float:
    """Quadratic penalty for drawdowns beyond threshold."""
    if current_dd <= threshold:
        return 0.0
    return -scale * (current_dd - threshold) ** 2


def turnover_penalty(old_w: np.ndarray, new_w: np.ndarray,
                     cost: float = 0.001) -> float:
    """Penalty proportional to allocation change (transaction costs)."""
    return -cost * np.abs(new_w - old_w).sum()


def compute_reward(daily_return: float, current_dd: float,
                   old_w: np.ndarray, new_w: np.ndarray,
                   dsr_tracker: DifferentialSharpeReward) -> float:
    """Combined reward: DSR + drawdown penalty + turnover penalty."""
    base = dsr_tracker.compute(daily_return)
    dd_pen = drawdown_penalty(current_dd)
    to_pen = turnover_penalty(old_w, new_w)
    return float(np.clip(base + dd_pen + to_pen, -10.0, 10.0))


# ── State Preprocessor ─────────────────────────────────────────────────

class StatePreprocessor:
    """Running z-score normalisation for state features."""

    def __init__(self, dim: int = STATE_DIM):
        self.running_mean = np.zeros(dim)
        self.running_var = np.ones(dim)
        self.count = 0

    def normalise(self, state_raw: np.ndarray) -> np.ndarray:
        self.count += 1
        delta = state_raw - self.running_mean
        self.running_mean += delta / self.count
        self.running_var += delta * (state_raw - self.running_mean)

        std = np.sqrt(self.running_var / max(self.count, 1)) + 1e-8
        normalised = (state_raw - self.running_mean) / std
        return np.clip(normalised, -5.0, 5.0).astype(np.float32)

    def reset(self):
        self.running_mean[:] = 0
        self.running_var[:] = 1
        self.count = 0


# ── Time Feature Encoding ─────────────────────────────────────────────

def encode_time_features(day_of_year: int, day_of_week: int,
                         month: int, quarter: int) -> np.ndarray:
    """Encode temporal features as sin/cos pairs (14 features)."""
    features = []
    # Day of year
    features.append(math.sin(2 * math.pi * day_of_year / 365))
    features.append(math.cos(2 * math.pi * day_of_year / 365))
    # Day of week
    features.append(math.sin(2 * math.pi * day_of_week / 5))
    features.append(math.cos(2 * math.pi * day_of_week / 5))
    # Month
    features.append(math.sin(2 * math.pi * month / 12))
    features.append(math.cos(2 * math.pi * month / 12))
    # Quarter
    features.append(math.sin(2 * math.pi * quarter / 4))
    features.append(math.cos(2 * math.pi * quarter / 4))
    # Padding to 14
    features.extend([0.0] * (14 - len(features)))
    return np.array(features[:14], dtype=np.float32)


# ── Gymnasium Environment ──────────────────────────────────────────────

try:
    import gymnasium as gym

    class AegisPortfolioEnv(gym.Env):
        """Portfolio allocation environment for RL training.

        State: 80 dims (48 market + 7 strategy Sharpe + 7 positions + 4 portfolio + 14 time)
        Action: 7 continuous weights in [-3, 3], transformed to [0.05, 0.30]
        Reward: Differential Sharpe + drawdown penalty + turnover penalty
        """

        metadata = {"render_modes": []}

        def __init__(self, market_data: np.ndarray,
                     strategy_returns: np.ndarray,
                     max_dd_terminate: float = 0.15):
            super().__init__()

            self.market_data = market_data       # (T, 48)
            self.strategy_returns = strategy_returns  # (T, 7)
            self.max_dd = max_dd_terminate

            self.observation_space = gym.spaces.Box(
                low=-5.0, high=5.0, shape=(STATE_DIM,), dtype=np.float32)
            self.action_space = gym.spaces.Box(
                low=-3.0, high=3.0, shape=(N_STRATEGIES,), dtype=np.float32)

            self.preprocessor = StatePreprocessor()
            self.dsr_tracker = DifferentialSharpeReward()

            self.current_step = 0
            self.current_weights = np.full(N_STRATEGIES, MAX_INVESTABLE / N_STRATEGIES)
            self.nav = 1.0
            self.peak_nav = 1.0

        def reset(self, seed=None, **kwargs):
            super().reset(seed=seed)
            max_start = max(1, len(self.market_data) - 252)
            self.current_step = self.np_random.integers(0, max_start)
            self.current_weights = np.full(N_STRATEGIES, MAX_INVESTABLE / N_STRATEGIES)
            self.nav = 1.0
            self.peak_nav = 1.0
            self.preprocessor.reset()
            self.dsr_tracker.reset()
            return self._get_state(), {}

        def step(self, raw_action):
            new_weights = transform_action(raw_action)
            new_weights = apply_turnover_limit(self.current_weights, new_weights)

            strat_returns = self.strategy_returns[self.current_step]
            portfolio_return = float(np.dot(new_weights, strat_returns))

            self.nav *= (1.0 + portfolio_return)
            self.peak_nav = max(self.peak_nav, self.nav)
            current_dd = (self.peak_nav - self.nav) / self.peak_nav

            reward = compute_reward(
                portfolio_return, current_dd,
                self.current_weights, new_weights,
                self.dsr_tracker)

            self.current_weights = new_weights
            self.current_step += 1

            terminated = current_dd > self.max_dd
            truncated = self.current_step >= len(self.market_data) - 1

            info = {
                "nav": self.nav,
                "drawdown": current_dd,
                "weights": new_weights.copy(),
                "portfolio_return": portfolio_return,
            }

            return self._get_state(), reward, terminated, truncated, info

        def _get_state(self):
            market = self.market_data[self.current_step]

            # Rolling strategy Sharpe (20-day)
            start = max(0, self.current_step - 20)
            window = self.strategy_returns[start:self.current_step + 1]
            if len(window) > 1:
                strat_sharpe = np.mean(window, axis=0) / (np.std(window, axis=0) + 1e-8) / 3.0
            else:
                strat_sharpe = np.zeros(N_STRATEGIES)

            # Portfolio metrics
            current_dd = (self.peak_nav - self.nav) / self.peak_nav
            cash = 1.0 - self.current_weights.sum()
            port_sharpe = 0.0
            if self.dsr_tracker.B - self.dsr_tracker.A ** 2 > 1e-12:
                port_sharpe = self.dsr_tracker.A / math.sqrt(
                    self.dsr_tracker.B - self.dsr_tracker.A ** 2)
            portfolio_metrics = np.array([
                self.nav / self.peak_nav - 1.0, current_dd, port_sharpe, cash])

            # Time features
            time_feats = encode_time_features(
                self.current_step % 365, self.current_step % 5,
                (self.current_step % 365) // 30 + 1, (self.current_step % 365) // 90 + 1)

            state = np.concatenate([
                market[:48] if len(market) >= 48 else np.pad(market, (0, 48 - len(market))),
                strat_sharpe[:N_STRATEGIES],
                self.current_weights,
                portfolio_metrics,
                time_feats,
            ]).astype(np.float32)

            # Pad/trim to STATE_DIM
            if len(state) < STATE_DIM:
                state = np.pad(state, (0, STATE_DIM - len(state)))
            elif len(state) > STATE_DIM:
                state = state[:STATE_DIM]

            return self.preprocessor.normalise(state)

except ImportError:
    # Gymnasium not installed — provide stub
    class AegisPortfolioEnv:
        def __init__(self, *args, **kwargs):
            raise ImportError("gymnasium is required for AegisPortfolioEnv")


# ── Safety: Risk Arbiter Integration ───────────────────────────────────

def apply_risk_arbiter(rl_weights: np.ndarray,
                       arbiter_state: Dict) -> np.ndarray:
    """Risk arbiter overrides RL suggestions.

    Authority hierarchy: Risk arbiter > human > rules > RL agent.
    """
    safe = rl_weights.copy()

    # FLATTEN regime — zero all
    if arbiter_state.get("regime") == "FLATTEN":
        return np.zeros_like(safe)

    # Daily loss limit — freeze
    if arbiter_state.get("daily_loss_exceeded", False):
        return arbiter_state.get("frozen_weights", safe)

    # Strategy-level blocks
    blocked = arbiter_state.get("blocked_strategies", set())
    for i, name in enumerate(STRATEGY_NAMES):
        if name in blocked:
            safe[i] = 0.0

    # Position limits
    max_alloc = arbiter_state.get("max_allocation", {})
    for i, name in enumerate(STRATEGY_NAMES):
        if name in max_alloc:
            safe[i] = min(safe[i], max_alloc[name])

    # Renormalise
    total = safe.sum()
    if total > MAX_INVESTABLE:
        safe = safe * (MAX_INVESTABLE / total)

    return safe


# ── Circuit Breakers ──────────────────────────────────────────────────

class CircuitBreakers:
    """RL-specific circuit breakers.

    Monitors: performance vs baseline, turnover stability, weight divergence.
    """

    def __init__(self, window: int = 20):
        self.perf_window = deque(maxlen=window)
        self.baseline_window = deque(maxlen=window)
        self.divergence_streak = 0

    def check(self, rl_return: float, baseline_return: float,
              rl_weights: np.ndarray, meta_weights: np.ndarray,
              raw_turnover: float) -> List[str]:
        """Check all circuit breakers. Returns list of alert strings."""
        self.perf_window.append(rl_return)
        self.baseline_window.append(baseline_return)

        alerts = []

        # Performance breaker: RL underperforming by >3% over window
        if len(self.perf_window) == self.perf_window.maxlen:
            rl_cum = float(np.prod([1 + r for r in self.perf_window]) - 1)
            base_cum = float(np.prod([1 + r for r in self.baseline_window]) - 1)
            if rl_cum < base_cum - 0.03:
                alerts.append("PERF_BREAKER: RL underperforming by >3%")

        # Stability breaker: excessive turnover
        if raw_turnover > 0.50:
            alerts.append("STABILITY_BREAKER: turnover >50%")

        # Divergence breaker: RL vs meta weights diverge for 5+ days
        divergence = float(np.abs(rl_weights - meta_weights).sum())
        if divergence > 0.40:
            self.divergence_streak += 1
        else:
            self.divergence_streak = 0

        if self.divergence_streak >= 5:
            alerts.append("DIVERGENCE_BREAKER: 5-day streak")

        return alerts


# ── Shadow Tracker ─────────────────────────────────────────────────────

@dataclass
class ShadowRecord:
    date: str
    rl_weights: List[float]
    actual_weights: List[float]
    rl_return: float
    actual_return: float
    rl_nav: float
    actual_nav: float


class ShadowTracker:
    """Track RL vs actual performance in shadow mode (no real capital)."""

    def __init__(self):
        self.rl_nav = 1.0
        self.actual_nav = 1.0
        self.rl_peak = 1.0
        self.actual_peak = 1.0
        self.records: List[ShadowRecord] = []

    def record(self, date: str, rl_weights: np.ndarray,
               actual_weights: np.ndarray,
               strategy_returns: np.ndarray,
               arbiter_state: Optional[Dict] = None) -> None:
        """Record one day of shadow tracking."""
        safe_rl = apply_risk_arbiter(rl_weights, arbiter_state or {})

        rl_ret = float(np.dot(safe_rl, strategy_returns))
        actual_ret = float(np.dot(actual_weights, strategy_returns))

        self.rl_nav *= (1.0 + rl_ret)
        self.actual_nav *= (1.0 + actual_ret)
        self.rl_peak = max(self.rl_peak, self.rl_nav)
        self.actual_peak = max(self.actual_peak, self.actual_nav)

        self.records.append(ShadowRecord(
            date=date,
            rl_weights=safe_rl.tolist(),
            actual_weights=actual_weights.tolist(),
            rl_return=rl_ret,
            actual_return=actual_ret,
            rl_nav=self.rl_nav,
            actual_nav=self.actual_nav,
        ))

        # Bound history
        if len(self.records) > 500:
            self.records = self.records[-300:]

    def summary(self) -> Dict:
        """Summary statistics for shadow period."""
        if not self.records:
            return {"days_tracked": 0}

        rl_rets = np.array([r.rl_return for r in self.records])
        act_rets = np.array([r.actual_return for r in self.records])

        rl_sharpe = float(np.mean(rl_rets) / (np.std(rl_rets) + 1e-8) * np.sqrt(252))
        act_sharpe = float(np.mean(act_rets) / (np.std(act_rets) + 1e-8) * np.sqrt(252))

        rl_dd = (self.rl_peak - self.rl_nav) / self.rl_peak
        act_dd = (self.actual_peak - self.actual_nav) / self.actual_peak

        return {
            "days_tracked": len(self.records),
            "rl_sharpe": round(rl_sharpe, 3),
            "actual_sharpe": round(act_sharpe, 3),
            "sharpe_diff": round(rl_sharpe - act_sharpe, 3),
            "rl_total_return": round(self.rl_nav - 1.0, 4),
            "actual_total_return": round(self.actual_nav - 1.0, 4),
            "rl_max_dd": round(float(rl_dd), 4),
            "actual_max_dd": round(float(act_dd), 4),
        }


# ── Authority Blending ─────────────────────────────────────────────────

def blend_allocations(meta_weights: np.ndarray,
                      rl_weights: np.ndarray,
                      authority_level: int) -> np.ndarray:
    """Blend meta (current) allocations with RL suggestions."""
    rl_fraction = AUTHORITY_BLEND.get(authority_level, 0.0)
    if rl_fraction <= 0:
        return meta_weights.copy()

    blended = (1.0 - rl_fraction) * meta_weights + rl_fraction * rl_weights
    return apply_turnover_limit(meta_weights, blended)


# ── Promotion Readiness ────────────────────────────────────────────────

def check_promotion_readiness(shadow: ShadowTracker,
                              authority_level: int) -> Dict:
    """Check if RL agent is ready for authority promotion."""
    s = shadow.summary()

    promotion_gates = {
        # Shadow → Suggest (0→1)
        0: {"min_days": 126, "min_sharpe": 0.8, "max_dd": 0.10, "min_outperformance": 0.02},
        # Suggest → 10% blend (1→2)
        1: {"min_days": 63, "min_sharpe": 0.8, "max_dd": 0.10, "min_outperformance": 0.01},
        # 10% → 30% (2→3)
        2: {"min_days": 63, "min_sharpe": 0.8, "max_dd": 0.08, "min_outperformance": 0.01},
        # 30% → 50% (3→4)
        3: {"min_days": 126, "min_sharpe": 1.0, "max_dd": 0.06, "min_outperformance": 0.02},
    }

    gates = promotion_gates.get(authority_level, {})
    if not gates:
        return {"eligible": False, "reason": "Max authority level reached"}

    criteria = {
        "days_met": s.get("days_tracked", 0) >= gates.get("min_days", 999),
        "sharpe_met": s.get("rl_sharpe", 0) >= gates.get("min_sharpe", 999),
        "dd_met": s.get("rl_max_dd", 1.0) <= gates.get("max_dd", 0),
        "outperformance_met": (
            s.get("rl_total_return", 0) - s.get("actual_total_return", 0)
        ) * (252 / max(s.get("days_tracked", 1), 1)) >= gates.get("min_outperformance", 999),
    }

    all_met = all(criteria.values())
    return {
        "eligible": all_met,
        "criteria": criteria,
        "shadow_summary": s,
        "current_authority": authority_level,
        "target_authority": authority_level + 1 if all_met else authority_level,
    }


# ── ONNX Export ────────────────────────────────────────────────────────

def export_policy_to_onnx(model_path: str, output_path: str,
                          state_dim: int = STATE_DIM) -> bool:
    """Export trained PPO policy to ONNX for Rust inference.

    Returns True on success.
    """
    try:
        import torch
        from stable_baselines3 import PPO

        model = PPO.load(model_path)
        policy = model.policy

        class PolicyWrapper(torch.nn.Module):
            def __init__(self, pol):
                super().__init__()
                self.features_extractor = pol.features_extractor
                self.mlp_extractor = pol.mlp_extractor
                self.action_net = pol.action_net

            def forward(self, obs):
                features = self.features_extractor(obs)
                latent_pi, _ = self.mlp_extractor(features)
                return self.action_net(latent_pi)

        wrapper = PolicyWrapper(policy)
        wrapper.eval()

        dummy = torch.randn(1, state_dim)
        torch.onnx.export(
            wrapper, dummy, output_path,
            input_names=["state"], output_names=["raw_action"],
            dynamic_axes={"state": {0: "batch"}, "raw_action": {0: "batch"}},
            opset_version=17)
        log.info("Exported RL policy ONNX: %s", output_path)
        return True
    except ImportError:
        log.info("PyTorch/SB3 not available — cannot export ONNX")
        return False
    except Exception as e:
        log.warning("RL ONNX export failed: %s", e)
        return False
