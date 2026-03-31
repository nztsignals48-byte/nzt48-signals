"""Game Theory for Algorithmic Markets — Book 170.

Models markets as adversarial games where participants' strategies
interact.  Provides Nash equilibrium solving, predator-prey population
dynamics (Lotka-Volterra), and Stackelberg leader-follower execution
optimisation.

Key insight from Book 170: markets are NOT physics experiments with
fixed laws — they are adversarial games.  Your edge is someone else's
loss.  When enough participants find the same edge, the edge vanishes.

Components:
  - NashEquilibriumSolver:  Mixed-strategy Nash for 2-player games
  - PredatorPreyModel:      Lotka-Volterra market ecosystem dynamics
  - StackelbergExecutor:    Leader-follower optimal execution
  - GameTheoreticSignal:    Top-level crowding + timing signals

Bridge.py integration:
    try:
        from python_brain.ml.game_theory_execution import (
            GameTheoreticSignal, NashEquilibriumSolver,
            PredatorPreyModel, StackelbergExecutor,
        )
        _game_sig = GameTheoreticSignal()
    except ImportError:
        _game_sig = None

Cross-references:
  - Book 35 (Quant Fund Reverse Engineering)
  - Book 168 (Statistical Arbitrage at Scale)
  - Book 103 (Adversarial Robustness)
  - Book 123 (Market Impact Modelling)
  - Book 169 (Lambda Vol Regime Field Theory — crowding term)
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

log = logging.getLogger("game_theory_execution")

__all__ = [
    "NashEquilibriumSolver",
    "PredatorPreyModel",
    "StackelbergExecutor",
    "GameTheoreticSignal",
]

# ---------------------------------------------------------------------------
# Paths (production)
# ---------------------------------------------------------------------------
_DATA_DIR = "/app/data"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_MAX_SUPPORT_SIZE = 10   # limit for support enumeration
_DEFAULT_LV_ALPHA = 1.1  # prey growth rate
_DEFAULT_LV_BETA = 0.4   # predation rate
_DEFAULT_LV_GAMMA = 0.4  # predator death rate
_DEFAULT_LV_DELTA = 0.1  # predator growth from predation


# ---------------------------------------------------------------------------
# NashEquilibriumSolver
# ---------------------------------------------------------------------------
class NashEquilibriumSolver:
    """Solve 2-player normal-form games for mixed-strategy Nash equilibria.

    Uses support enumeration: for each pair of supports (subsets of
    strategies), solve the indifference conditions.  For small games
    (<=10 strategies per player) this is exact.

    Market interpretation:
      - Player A = AEGIS V2 (our system)
      - Player B = market (aggregate of other participants)
      - Payoffs = P&L under different strategy combinations
    """

    def solve_2player(
        self,
        payoff_A: np.ndarray,
        payoff_B: np.ndarray,
    ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """Find a mixed-strategy Nash equilibrium for a 2-player game.

        Args:
            payoff_A: (m, n) payoff matrix for player A.
            payoff_B: (m, n) payoff matrix for player B.

        Returns:
            Tuple of (strategy_A, strategy_B) as probability vectors,
            or (None, None) if no equilibrium found.
        """
        m, n = payoff_A.shape
        if payoff_B.shape != (m, n):
            log.error("solve_2player: payoff matrices must have same shape")
            return (None, None)

        if m > _MAX_SUPPORT_SIZE or n > _MAX_SUPPORT_SIZE:
            log.warning(
                "solve_2player: game too large (%dx%d), capping supports at %d",
                m, n, _MAX_SUPPORT_SIZE,
            )

        results = self._support_enumeration(payoff_A, payoff_B)

        if not results:
            log.warning("solve_2player: no Nash equilibrium found")
            return (None, None)

        # Return the equilibrium with highest total expected payoff
        best = max(results, key=lambda x: x[2])
        log.info(
            "solve_2player | found %d equilibria, best payoff=%.4f",
            len(results), best[2],
        )
        return (best[0], best[1])

    def _support_enumeration(
        self,
        A: np.ndarray,
        B: np.ndarray,
    ) -> List[Tuple[np.ndarray, np.ndarray, float]]:
        """Enumerate supports and solve indifference conditions.

        For each pair of support sizes (k1, k2), enumerate all
        C(m, k1) x C(n, k2) support pairs and check if they yield
        a valid Nash equilibrium.

        Returns:
            List of (p1, p2, expected_payoff) tuples.
        """
        m, n = A.shape
        max_m = min(m, _MAX_SUPPORT_SIZE)
        max_n = min(n, _MAX_SUPPORT_SIZE)
        results: List[Tuple[np.ndarray, np.ndarray, float]] = []

        # Try pure strategy pairs first
        for i in range(max_m):
            for j in range(max_n):
                # Check if (i, j) is a pure-strategy NE
                if A[i, j] >= np.max(A[:, j]) and B[i, j] >= np.max(B[i, :]):
                    p1 = np.zeros(m)
                    p1[i] = 1.0
                    p2 = np.zeros(n)
                    p2[j] = 1.0
                    payoff = float(A[i, j] + B[i, j])
                    results.append((p1, p2, payoff))

        # Try fully mixed (all strategies in support) — most common case
        p1_full, p2_full = self._solve_indifference(
            A, B, list(range(max_m)), list(range(max_n)),
        )
        if p1_full is not None and p2_full is not None:
            ep = float(p1_full @ A @ p2_full + p1_full @ B @ p2_full)
            results.append((p1_full, p2_full, ep))

        # Try support sizes 2 if game is small enough
        if max_m >= 2 and max_n >= 2 and max_m * max_n <= 36:
            for s1 in self._combinations(list(range(max_m)), 2):
                for s2 in self._combinations(list(range(max_n)), 2):
                    p1, p2 = self._solve_indifference(A, B, list(s1), list(s2))
                    if p1 is not None and p2 is not None:
                        ep = float(p1 @ A @ p2 + p1 @ B @ p2)
                        results.append((p1, p2, ep))

        return results

    @staticmethod
    def _solve_indifference(
        A: np.ndarray,
        B: np.ndarray,
        support_1: List[int],
        support_2: List[int],
    ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """Solve the indifference conditions for given supports.

        Player 1's mixed strategy must make Player 2 indifferent among
        their support strategies, and vice versa.
        """
        m, n = A.shape
        k1, k2 = len(support_1), len(support_2)

        # Player 2's indifference: for all j in support_2,
        # sum_i p1[i] * A[i, j] = constant
        # Plus constraint: sum p1 = 1, p1 >= 0
        try:
            # Build system for p2: B_sub^T @ p2 = uniform value
            # Player 1 is indifferent across support_1 when:
            # A[i, :] @ p2 is equal for all i in support_1
            A_sub = A[np.ix_(support_1, support_2)]
            B_sub = B[np.ix_(support_1, support_2)]

            # Solve for p2: make player 1 indifferent
            # A_sub @ p2 = v * ones, sum(p2) = 1
            # Augmented system: [A_sub; ones^T] @ p2 = [v*ones; 1]
            # With v unknown — use difference equations
            if k2 < 2:
                p2_support = np.ones(k2)
            else:
                # A_sub[0,:] @ p2 = A_sub[1,:] @ p2 = ... = A_sub[k1-1,:] @ p2
                # Difference: (A_sub[i,:] - A_sub[0,:]) @ p2 = 0 for i>0
                diff_rows = A_sub[1:, :] - A_sub[0:1, :]
                sum_row = np.ones((1, k2))
                lhs = np.vstack([diff_rows, sum_row])
                rhs = np.zeros(lhs.shape[0])
                rhs[-1] = 1.0
                # Least-squares solve
                p2_support, residuals, rank, _ = np.linalg.lstsq(lhs, rhs, rcond=None)

            if np.any(p2_support < -1e-8):
                return (None, None)
            p2_support = np.maximum(p2_support, 0.0)
            s2_sum = float(np.sum(p2_support))
            if s2_sum < 1e-12:
                return (None, None)
            p2_support /= s2_sum

            # Solve for p1: make player 2 indifferent
            if k1 < 2:
                p1_support = np.ones(k1)
            else:
                diff_cols = B_sub[:, 1:].T - B_sub[:, 0:1].T
                sum_row = np.ones((1, k1))
                lhs = np.vstack([diff_cols, sum_row])
                rhs = np.zeros(lhs.shape[0])
                rhs[-1] = 1.0
                p1_support, _, _, _ = np.linalg.lstsq(lhs, rhs, rcond=None)

            if np.any(p1_support < -1e-8):
                return (None, None)
            p1_support = np.maximum(p1_support, 0.0)
            s1_sum = float(np.sum(p1_support))
            if s1_sum < 1e-12:
                return (None, None)
            p1_support /= s1_sum

            # Map back to full strategy space
            p1 = np.zeros(m)
            p2 = np.zeros(n)
            for idx, s in enumerate(support_1):
                p1[s] = p1_support[idx]
            for idx, s in enumerate(support_2):
                p2[s] = p2_support[idx]

            return (p1, p2)

        except (np.linalg.LinAlgError, ValueError) as exc:
            log.debug("_solve_indifference failed: %s", exc)
            return (None, None)

    @staticmethod
    def _combinations(items: List[int], k: int):
        """Generate k-combinations of items (itertools-free)."""
        n = len(items)
        if k > n:
            return
        indices = list(range(k))
        yield tuple(items[i] for i in indices)
        while True:
            for i in reversed(range(k)):
                if indices[i] != i + n - k:
                    break
            else:
                return
            indices[i] += 1
            for j in range(i + 1, k):
                indices[j] = indices[j - 1] + 1
            yield tuple(items[i] for i in indices)


# ---------------------------------------------------------------------------
# PredatorPreyModel
# ---------------------------------------------------------------------------
class PredatorPreyModel:
    """Lotka-Volterra predator-prey dynamics for market participants.

    Models the interaction between two participant types:
      - Prey (x):     Retail / slow-moving capital (we want to harvest)
      - Predator (y): HFT / institutional (competing for same alpha)

    Equations:
      dx/dt = alpha * x - beta * x * y    (prey grows, predated upon)
      dy/dt = delta * x * y - gamma * y    (predator grows from eating, dies naturally)

    Market interpretation:
      - alpha:  rate at which new retail flow appears
      - beta:   rate at which predators consume retail flow
      - gamma:  rate at which predators exit (alpha decay)
      - delta:  rate at which predators grow from profits
    """

    def __init__(
        self,
        alpha: float = _DEFAULT_LV_ALPHA,
        beta: float = _DEFAULT_LV_BETA,
        gamma: float = _DEFAULT_LV_GAMMA,
        delta: float = _DEFAULT_LV_DELTA,
    ) -> None:
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma
        self.delta = delta
        log.info(
            "PredatorPreyModel init | a=%.2f b=%.2f g=%.2f d=%.2f",
            alpha, beta, gamma, delta,
        )

    def simulate(
        self,
        x0: float,
        y0: float,
        dt: float = 0.01,
        n_steps: int = 1000,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Simulate Lotka-Volterra dynamics with 4th-order Runge-Kutta.

        Args:
            x0:      initial prey population (retail flow volume)
            y0:      initial predator population (institutional activity)
            dt:      time step
            n_steps: number of simulation steps

        Returns:
            Tuple of (prey_history, predator_history) arrays.
        """
        x = np.zeros(n_steps + 1)
        y = np.zeros(n_steps + 1)
        x[0], y[0] = x0, y0

        for i in range(n_steps):
            xi, yi = x[i], y[i]

            # RK4 integration
            k1x, k1y = self._derivatives(xi, yi)
            k2x, k2y = self._derivatives(xi + 0.5 * dt * k1x, yi + 0.5 * dt * k1y)
            k3x, k3y = self._derivatives(xi + 0.5 * dt * k2x, yi + 0.5 * dt * k2y)
            k4x, k4y = self._derivatives(xi + dt * k3x, yi + dt * k3y)

            x[i + 1] = max(0.0, xi + (dt / 6.0) * (k1x + 2 * k2x + 2 * k3x + k4x))
            y[i + 1] = max(0.0, yi + (dt / 6.0) * (k1y + 2 * k2y + 2 * k3y + k4y))

        return (x, y)

    def detect_regime(
        self,
        prey_pop: np.ndarray,
        predator_pop: np.ndarray,
    ) -> str:
        """Detect the current ecosystem regime.

        Args:
            prey_pop:     recent prey population history
            predator_pop: recent predator population history

        Returns:
            One of: PREY_DOMINANT, PREDATOR_DOMINANT, EQUILIBRIUM
        """
        if len(prey_pop) < 2 or len(predator_pop) < 2:
            return "EQUILIBRIUM"

        # Use recent values
        recent = min(20, len(prey_pop))
        x_mean = float(np.mean(prey_pop[-recent:]))
        y_mean = float(np.mean(predator_pop[-recent:]))

        if x_mean < 1e-12 and y_mean < 1e-12:
            return "EQUILIBRIUM"

        ratio = x_mean / max(y_mean, 1e-12)

        # Equilibrium point: x* = gamma/delta, y* = alpha/beta
        if self.delta > 0 and self.beta > 0:
            x_eq = self.gamma / self.delta
            y_eq = self.alpha / self.beta
            eq_ratio = x_eq / max(y_eq, 1e-12)

            # Within 30% of equilibrium ratio
            if 0.7 * eq_ratio < ratio < 1.3 * eq_ratio:
                regime = "EQUILIBRIUM"
            elif ratio > 1.3 * eq_ratio:
                regime = "PREY_DOMINANT"
            else:
                regime = "PREDATOR_DOMINANT"
        else:
            if ratio > 2.0:
                regime = "PREY_DOMINANT"
            elif ratio < 0.5:
                regime = "PREDATOR_DOMINANT"
            else:
                regime = "EQUILIBRIUM"

        log.debug(
            "detect_regime | x_mean=%.3f y_mean=%.3f ratio=%.3f => %s",
            x_mean, y_mean, ratio, regime,
        )
        return regime

    # ---- private ----------------------------------------------------------

    def _derivatives(self, x: float, y: float) -> Tuple[float, float]:
        """Compute Lotka-Volterra derivatives."""
        dx = self.alpha * x - self.beta * x * y
        dy = self.delta * x * y - self.gamma * y
        return (dx, dy)


# ---------------------------------------------------------------------------
# StackelbergExecutor
# ---------------------------------------------------------------------------
class StackelbergExecutor:
    """Leader-follower Stackelberg game for execution optimisation.

    In execution, the leader (larger order) commits first, and the
    follower (smaller participant like AEGIS V2) can observe and
    optimally respond.

    For AEGIS V2, we are almost always the follower:
      - Observe large institutional flow
      - Optimise our execution timing around their impact
    """

    def optimal_leader_strategy(
        self,
        market_impact_fn: Callable[[float], float],
        follower_response_fn: Callable[[float], float],
        n_grid: int = 100,
    ) -> Dict[str, Any]:
        """Find the Stackelberg leader's optimal strategy.

        The leader chooses an execution rate r that maximises their
        payoff, accounting for the follower's best response.

        Args:
            market_impact_fn:   r -> impact(r), maps execution rate to price impact
            follower_response_fn: leader_r -> follower_r, follower's best response

        Returns:
            Dict with leader_rate, follower_rate, leader_payoff, follower_payoff.
        """
        best_rate = 0.0
        best_payoff = -math.inf
        best_follower_rate = 0.0

        for i in range(1, n_grid + 1):
            rate = i / n_grid  # execution rate in (0, 1]
            impact = market_impact_fn(rate)
            follower_rate = follower_response_fn(rate)
            total_impact = market_impact_fn(rate + follower_rate)

            # Leader payoff: want to minimise their own impact
            # Lower impact = higher payoff
            leader_payoff = -total_impact * rate

            if leader_payoff > best_payoff:
                best_payoff = leader_payoff
                best_rate = rate
                best_follower_rate = follower_rate

        result = {
            "leader_rate": best_rate,
            "follower_rate": best_follower_rate,
            "leader_payoff": best_payoff,
            "follower_payoff": -market_impact_fn(best_rate + best_follower_rate) * best_follower_rate,
            "total_impact": market_impact_fn(best_rate + best_follower_rate),
        }

        log.info(
            "optimal_leader_strategy | leader_r=%.3f follower_r=%.3f impact=%.4f",
            best_rate, best_follower_rate, result["total_impact"],
        )
        return result

    def optimal_follower_strategy(
        self,
        leader_rate: float,
        market_impact_fn: Callable[[float], float],
        n_grid: int = 100,
    ) -> Dict[str, Any]:
        """Find the follower's optimal response to the leader's rate.

        AEGIS V2 perspective: given observed institutional flow,
        what is our optimal execution timing?

        Args:
            leader_rate:      observed leader execution rate
            market_impact_fn: r -> impact(r)

        Returns:
            Dict with follower_rate, payoff, strategy.
        """
        best_rate = 0.0
        best_payoff = -math.inf

        for i in range(n_grid + 1):
            rate = i / n_grid
            total = leader_rate + rate
            impact = market_impact_fn(total)
            payoff = -impact * rate  # minimise our own cost
            if rate == 0:
                payoff = 0.0  # doing nothing has zero cost

            if payoff > best_payoff:
                best_payoff = payoff
                best_rate = rate

        # Timing recommendation
        if best_rate < 0.1:
            strategy = "WAIT"
        elif best_rate > 0.7:
            strategy = "EXECUTE_NOW"
        else:
            strategy = "SPLIT"

        result = {
            "follower_rate": best_rate,
            "payoff": best_payoff,
            "strategy": strategy,
            "leader_rate": leader_rate,
        }

        log.debug("optimal_follower | rate=%.3f strategy=%s", best_rate, strategy)
        return result


# ---------------------------------------------------------------------------
# GameTheoreticSignal
# ---------------------------------------------------------------------------
class GameTheoreticSignal:
    """Top-level game-theoretic signal generator.

    Combines crowding detection and timing optimisation.

    Output dict schema:
      - crowding_score:     0-1 where 1 = extreme crowding
      - participant_regime: PREY_DOMINANT, PREDATOR_DOMINANT, EQUILIBRIUM
      - timing:             EXECUTE_NOW, WAIT, SPLIT
      - is_prey:            True if we're likely being hunted
      - confidence:         signal confidence [0, 100]
      - sizing_factor:      Kelly multiplier [0, 1]
    """

    def __init__(self) -> None:
        self.predator_prey = PredatorPreyModel()
        self.nash_solver = NashEquilibriumSolver()
        self.stackelberg = StackelbergExecutor()
        log.info("GameTheoreticSignal init")

    def assess_crowding(
        self,
        volume_profile: np.ndarray,
        order_imbalance: np.ndarray,
    ) -> Dict[str, Any]:
        """Detect crowding and whether we are prey or predator.

        Args:
            volume_profile:  intraday volume by time bucket
            order_imbalance: signed imbalance per time bucket

        Returns:
            Crowding assessment dictionary.
        """
        if len(volume_profile) == 0 or len(order_imbalance) == 0:
            return {
                "crowding_score": 0.5,
                "is_prey": False,
                "volume_concentration": 0.0,
                "imbalance_persistence": 0.0,
            }

        # Volume concentration (HHI of volume profile)
        total_vol = float(np.sum(volume_profile))
        if total_vol > 0:
            shares = volume_profile / total_vol
            hhi = float(np.sum(shares ** 2))
        else:
            hhi = 1.0 / max(len(volume_profile), 1)

        # Normalise HHI: uniform = 1/n, single-bucket = 1
        n_buckets = len(volume_profile)
        min_hhi = 1.0 / max(n_buckets, 1)
        vol_concentration = float(np.clip(
            (hhi - min_hhi) / (1.0 - min_hhi + 1e-12), 0.0, 1.0,
        ))

        # Imbalance persistence: autocorrelation of order imbalance
        if len(order_imbalance) >= 3:
            oi = order_imbalance.astype(float)
            mean_oi = float(np.mean(oi))
            std_oi = float(np.std(oi))
            if std_oi > 1e-12:
                # Lag-1 autocorrelation
                ac1 = float(np.mean(
                    (oi[:-1] - mean_oi) * (oi[1:] - mean_oi)
                )) / (std_oi ** 2)
                imb_persistence = float(np.clip(ac1, 0.0, 1.0))
            else:
                imb_persistence = 0.0
        else:
            imb_persistence = 0.0

        # Crowding score: combination of vol concentration + imbalance persistence
        crowding_score = 0.6 * vol_concentration + 0.4 * imb_persistence

        # Are we prey?  High crowding + our orders would be on the wrong
        # side of the imbalance
        mean_imb = float(np.mean(order_imbalance)) if len(order_imbalance) > 0 else 0.0
        is_prey = crowding_score > 0.5 and abs(mean_imb) > 0.3

        result = {
            "crowding_score": float(np.clip(crowding_score, 0.0, 1.0)),
            "is_prey": is_prey,
            "volume_concentration": vol_concentration,
            "imbalance_persistence": imb_persistence,
        }

        log.debug(
            "assess_crowding | score=%.3f is_prey=%s vol_conc=%.3f imb_pers=%.3f",
            crowding_score, is_prey, vol_concentration, imb_persistence,
        )
        return result

    def optimal_timing(self, crowding_score: float) -> str:
        """Determine optimal execution timing based on crowding.

        Args:
            crowding_score: 0-1 crowding intensity.

        Returns:
            One of: EXECUTE_NOW, WAIT, SPLIT.
        """
        if crowding_score > 0.7:
            timing = "WAIT"
        elif crowding_score < 0.3:
            timing = "EXECUTE_NOW"
        else:
            timing = "SPLIT"

        log.debug("optimal_timing | crowding=%.3f => %s", crowding_score, timing)
        return timing

    def generate(
        self,
        volume_profile: np.ndarray,
        order_imbalance: np.ndarray,
        prey_history: Optional[np.ndarray] = None,
        predator_history: Optional[np.ndarray] = None,
    ) -> Dict[str, Any]:
        """Full game-theoretic signal generation.

        Args:
            volume_profile:   intraday volume by time bucket
            order_imbalance:  signed imbalance per bucket
            prey_history:     optional retail flow history
            predator_history: optional institutional flow history

        Returns:
            Complete signal dictionary.
        """
        crowding = self.assess_crowding(volume_profile, order_imbalance)
        timing = self.optimal_timing(crowding["crowding_score"])

        # Predator-prey regime if histories are provided
        if prey_history is not None and predator_history is not None:
            regime = self.predator_prey.detect_regime(prey_history, predator_history)
        else:
            regime = "EQUILIBRIUM"

        # Confidence: higher when crowding signal is clear
        if crowding["crowding_score"] > 0.7 or crowding["crowding_score"] < 0.2:
            confidence = 70.0
        else:
            confidence = 50.0

        # Sizing: reduce when crowding is high or we're prey
        if crowding["is_prey"]:
            sizing_factor = 0.3
            confidence = min(confidence, 55.0)
        elif crowding["crowding_score"] > 0.6:
            sizing_factor = 0.5
        else:
            sizing_factor = 0.8

        result = {
            "crowding_score": crowding["crowding_score"],
            "participant_regime": regime,
            "timing": timing,
            "is_prey": crowding["is_prey"],
            "confidence": confidence,
            "sizing_factor": sizing_factor,
            "volume_concentration": crowding["volume_concentration"],
            "imbalance_persistence": crowding["imbalance_persistence"],
        }

        log.info(
            "generate | crowding=%.3f timing=%s regime=%s conf=%.0f",
            crowding["crowding_score"], timing, regime, confidence,
        )
        return result
