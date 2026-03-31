"""Autonomous Learning Loop — Observe, Hypothesise, Test, Adapt — Book 158.

Self-improving learning loop that closes the feedback gap in AEGIS V2.
Every trade becomes a training sample. The loop:
  1. OBSERVE  — compute performance metrics, detect patterns in recent trades
  2. HYPOTHESISE — generate improvement hypotheses from observations
  3. TEST    — walk-forward test each hypothesis
  4. ADAPT   — apply improvements if they exceed a minimum threshold
  5. VALIDATE — confirm improvement persists in post-adaptation trades

State is persisted per strategy to /app/data/autonomous_learning/{strategy}.json.

Components:
  - LearningState: Enum for loop phases
  - Hypothesis: Proposed parameter change with expected improvement
  - AutonomousLearner: Full OHTA(V) learning loop

Bridge.py integration:
    try:
        from python_brain.ml.autonomous_learner import (
            AutonomousLearner, Hypothesis, LearningState,
        )
    except ImportError:
        pass

    # After nightly trade review:
    learner = AutonomousLearner(strategy_name="VanguardSniper", params=current_params)
    cycle = learner.run_cycle(trade_results=recent_trades, backtest_fn=my_backtest)
    if cycle["adapted"]:
        new_params = cycle["new_params"]
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger("autonomous_learner")

__all__ = [
    "LearningState",
    "Hypothesis",
    "AutonomousLearner",
]

DATA_DIR = "/app/data/autonomous_learning"


# ---------------------------------------------------------------------------
# Enums & Dataclasses
# ---------------------------------------------------------------------------

class LearningState(Enum):
    """Phases of the autonomous learning loop."""
    OBSERVE = "observe"
    HYPOTHESIZE = "hypothesize"
    TEST = "test"
    ADAPT = "adapt"
    VALIDATE = "validate"


@dataclass
class Hypothesis:
    """A proposed improvement to strategy parameters.

    Attributes:
        description: Human-readable description of the change.
        parameter_changes: Dict of param_name -> new_value.
        expected_improvement: Estimated improvement in fitness metric.
        confidence: Confidence in the hypothesis (0 to 1).
        tested: Whether this hypothesis has been backtested.
        result: Test result dict (sharpe_before, sharpe_after, etc.).
        source: What observation triggered this hypothesis.
    """
    description: str
    parameter_changes: Dict[str, Any]
    expected_improvement: float = 0.0
    confidence: float = 0.5
    tested: bool = False
    result: Dict[str, Any] = field(default_factory=dict)
    source: str = ""


# ---------------------------------------------------------------------------
# AutonomousLearner
# ---------------------------------------------------------------------------

class AutonomousLearner:
    """Self-improving learning loop for AEGIS V2 strategies.

    Runs the OHTAV cycle (Observe, Hypothesise, Test, Adapt, Validate)
    to continuously improve strategy parameters without human intervention.

    Safety guardrails:
      - Minimum improvement threshold to prevent noise-driven changes
      - Maximum parameter drift cap (prevents runaway tuning)
      - Validation gate: improvement must persist in subsequent trades
      - All changes logged with full attribution

    Args:
        strategy_name: Name of the strategy being tuned.
        params: Current parameter dict for the strategy.
        min_improvement: Minimum Sharpe improvement to accept adaptation.
        max_drift_pct: Maximum total parameter drift from baseline (fraction).
        validation_window: Number of subsequent trades to validate.
    """

    def __init__(
        self,
        strategy_name: str,
        params: Dict[str, Any],
        min_improvement: float = 0.05,
        max_drift_pct: float = 0.50,
        validation_window: int = 20,
    ) -> None:
        self.strategy_name = strategy_name
        self.params = dict(params)
        self.baseline_params = dict(params)
        self.min_improvement = min_improvement
        self.max_drift_pct = max_drift_pct
        self.validation_window = validation_window

        self.state = LearningState.OBSERVE
        self.cycle_count: int = 0
        self.hypotheses: List[Hypothesis] = []
        self.adaptation_history: List[Dict[str, Any]] = []
        self.observation_history: List[Dict[str, Any]] = []

        self._state_file = os.path.join(DATA_DIR, f"{strategy_name}.json")
        self._load_state()

        log.info(
            "AutonomousLearner initialised: strategy=%s, params=%d, "
            "min_improvement=%.3f, max_drift=%.0f%%",
            strategy_name, len(params), min_improvement, max_drift_pct * 100,
        )

    # ------------------------------------------------------------------
    # Phase 1: OBSERVE
    # ------------------------------------------------------------------

    def observe(self, trade_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Compute performance metrics and detect patterns in recent trades.

        Args:
            trade_results: List of trade dicts with at minimum:
                - 'pnl': float (net P&L)
                - 'return_pct': float
                - 'holding_time_seconds': int
                - 'direction': str ('long' or 'short')
                Optional: 'entry_confidence', 'max_adverse_excursion',
                'max_favorable_excursion', 'strategy', 'ticker'

        Returns:
            Dict with performance metrics and detected patterns.
        """
        self.state = LearningState.OBSERVE

        if not trade_results:
            log.warning("No trade results to observe")
            return {"status": "no_data", "n_trades": 0}

        pnls = np.array([t.get("pnl", 0.0) for t in trade_results])
        returns = np.array([t.get("return_pct", 0.0) for t in trade_results])

        n_trades = len(trade_results)
        n_winners = int(np.sum(pnls > 0))
        n_losers = int(np.sum(pnls < 0))
        win_rate = n_winners / n_trades if n_trades > 0 else 0.0

        # Core metrics
        mean_return = float(np.mean(returns))
        std_return = float(np.std(returns)) if n_trades > 1 else 0.0
        sharpe = mean_return / std_return if std_return > 1e-8 else 0.0

        # Risk metrics
        max_drawdown = self._compute_max_drawdown(pnls)
        avg_winner = float(np.mean(pnls[pnls > 0])) if n_winners > 0 else 0.0
        avg_loser = float(np.mean(pnls[pnls < 0])) if n_losers > 0 else 0.0
        profit_factor = abs(avg_winner * n_winners / (avg_loser * n_losers)) if n_losers > 0 and avg_loser != 0 else float("inf")

        # Pattern detection
        patterns = self._detect_patterns(trade_results, returns, pnls)

        # Holding time analysis
        holding_times = [t.get("holding_time_seconds", 0) for t in trade_results]
        avg_hold = float(np.mean(holding_times)) if holding_times else 0.0

        # MAE/MFE analysis
        maes = [abs(t.get("max_adverse_excursion", 0.0)) for t in trade_results]
        mfes = [t.get("max_favorable_excursion", 0.0) for t in trade_results]
        avg_mae = float(np.mean(maes)) if maes else 0.0
        avg_mfe = float(np.mean(mfes)) if mfes else 0.0

        observations = {
            "n_trades": n_trades,
            "n_winners": n_winners,
            "n_losers": n_losers,
            "win_rate": win_rate,
            "mean_return": mean_return,
            "std_return": std_return,
            "sharpe": sharpe,
            "max_drawdown": max_drawdown,
            "avg_winner": avg_winner,
            "avg_loser": avg_loser,
            "profit_factor": profit_factor,
            "avg_holding_seconds": avg_hold,
            "avg_mae": avg_mae,
            "avg_mfe": avg_mfe,
            "patterns": patterns,
            "timestamp": time.time(),
        }

        self.observation_history.append(observations)
        # Keep bounded
        if len(self.observation_history) > 100:
            self.observation_history = self.observation_history[-100:]

        log.info(
            "Observed %d trades: win_rate=%.1f%% sharpe=%.3f patterns=%d",
            n_trades, win_rate * 100, sharpe, len(patterns),
        )

        return observations

    def _detect_patterns(
        self,
        trades: List[Dict[str, Any]],
        returns: np.ndarray,
        pnls: np.ndarray,
    ) -> List[Dict[str, Any]]:
        """Detect actionable patterns in trade results.

        Looks for:
          - Losing streaks (cluster of consecutive losers)
          - Time-of-day effects (certain hours better than others)
          - Direction bias (long vs short win rate difference)
          - Stop-out clustering (many trades hitting stop at similar loss %)
          - Confidence calibration (high confidence trades not outperforming)

        Returns:
            List of pattern dicts with 'type', 'description', 'severity', 'data'.
        """
        patterns: List[Dict[str, Any]] = []

        # 1. Losing streak detection
        max_streak = 0
        streak = 0
        for pnl in pnls:
            if pnl < 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0

        if max_streak >= 5:
            patterns.append({
                "type": "losing_streak",
                "description": f"Max losing streak of {max_streak} trades",
                "severity": min(1.0, max_streak / 10.0),
                "data": {"max_streak": max_streak},
            })

        # 2. Direction bias
        long_trades = [t for t in trades if t.get("direction") == "long"]
        short_trades = [t for t in trades if t.get("direction") == "short"]
        if long_trades and short_trades:
            long_wr = sum(1 for t in long_trades if t.get("pnl", 0) > 0) / len(long_trades)
            short_wr = sum(1 for t in short_trades if t.get("pnl", 0) > 0) / len(short_trades)
            if abs(long_wr - short_wr) > 0.15:
                better = "long" if long_wr > short_wr else "short"
                patterns.append({
                    "type": "direction_bias",
                    "description": f"{better} trades outperform by {abs(long_wr - short_wr):.1%}",
                    "severity": abs(long_wr - short_wr),
                    "data": {"long_wr": long_wr, "short_wr": short_wr},
                })

        # 3. Confidence calibration
        conf_trades = [t for t in trades if "entry_confidence" in t]
        if len(conf_trades) >= 10:
            high_conf = [t for t in conf_trades if t["entry_confidence"] >= 70]
            low_conf = [t for t in conf_trades if t["entry_confidence"] < 50]
            if high_conf and low_conf:
                hc_wr = sum(1 for t in high_conf if t.get("pnl", 0) > 0) / len(high_conf)
                lc_wr = sum(1 for t in low_conf if t.get("pnl", 0) > 0) / len(low_conf)
                if lc_wr >= hc_wr:
                    patterns.append({
                        "type": "confidence_miscalibration",
                        "description": "High confidence trades not outperforming low confidence",
                        "severity": 0.7,
                        "data": {"high_conf_wr": hc_wr, "low_conf_wr": lc_wr},
                    })

        # 4. MAE cluster (many trades stopped out at similar level)
        maes = np.array([abs(t.get("max_adverse_excursion", 0.0)) for t in trades])
        if len(maes) >= 10 and maes.std() > 0:
            # Cluster: many MAEs within a tight band
            median_mae = np.median(maes)
            near_median = np.sum(np.abs(maes - median_mae) < median_mae * 0.2)
            if near_median > len(maes) * 0.5:
                patterns.append({
                    "type": "mae_cluster",
                    "description": f"{near_median}/{len(maes)} trades with MAE near {median_mae:.4f}",
                    "severity": 0.6,
                    "data": {"median_mae": float(median_mae), "cluster_pct": near_median / len(maes)},
                })

        return patterns

    @staticmethod
    def _compute_max_drawdown(pnls: np.ndarray) -> float:
        """Compute maximum drawdown from a P&L series."""
        if len(pnls) == 0:
            return 0.0
        cumulative = np.cumsum(pnls)
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = running_max - cumulative
        return float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    # ------------------------------------------------------------------
    # Phase 2: HYPOTHESIZE
    # ------------------------------------------------------------------

    def hypothesize(self, observations: Dict[str, Any]) -> List[Hypothesis]:
        """Generate improvement hypotheses from observations.

        Each pattern detected in observe() generates one or more hypotheses.
        Hypotheses propose specific parameter changes expected to improve
        the detected weakness.

        Args:
            observations: Output from observe().

        Returns:
            List of Hypothesis objects.
        """
        self.state = LearningState.HYPOTHESIZE
        hypotheses: List[Hypothesis] = []

        patterns = observations.get("patterns", [])
        win_rate = observations.get("win_rate", 0.5)
        sharpe = observations.get("sharpe", 0.0)
        avg_mae = observations.get("avg_mae", 0.0)
        avg_mfe = observations.get("avg_mfe", 0.0)

        for pattern in patterns:
            ptype = pattern["type"]

            if ptype == "losing_streak":
                # Hypothesis: tighten entry confidence threshold
                hypotheses.append(Hypothesis(
                    description="Tighten confidence threshold to reduce losing streaks",
                    parameter_changes={"confidence_floor": self._adjust_param("confidence_floor", 5, "add")},
                    expected_improvement=0.05,
                    confidence=0.6,
                    source=f"losing_streak (max={pattern['data']['max_streak']})",
                ))

            elif ptype == "direction_bias":
                data = pattern["data"]
                worse = "short" if data["long_wr"] > data["short_wr"] else "long"
                hypotheses.append(Hypothesis(
                    description=f"Increase confidence threshold for {worse} trades",
                    parameter_changes={f"{worse}_confidence_penalty": 5},
                    expected_improvement=0.03,
                    confidence=0.5,
                    source=f"direction_bias ({worse} underperforming)",
                ))

            elif ptype == "confidence_miscalibration":
                hypotheses.append(Hypothesis(
                    description="Recalibrate confidence scoring model",
                    parameter_changes={"recalibrate_confidence": True},
                    expected_improvement=0.08,
                    confidence=0.4,
                    source="confidence_miscalibration",
                ))

            elif ptype == "mae_cluster":
                median_mae = pattern["data"]["median_mae"]
                hypotheses.append(Hypothesis(
                    description="Widen initial stop to avoid clustered stop-outs",
                    parameter_changes={"stop_atr_mult": self._adjust_param("stop_atr_mult", 0.3, "add")},
                    expected_improvement=0.04,
                    confidence=0.55,
                    source=f"mae_cluster (median={median_mae:.4f})",
                ))

        # General hypotheses based on metrics
        if win_rate < 0.40 and observations.get("n_trades", 0) >= 20:
            hypotheses.append(Hypothesis(
                description="Increase minimum confidence to filter low-quality signals",
                parameter_changes={"confidence_floor": self._adjust_param("confidence_floor", 8, "add")},
                expected_improvement=0.06,
                confidence=0.65,
                source=f"low_win_rate ({win_rate:.1%})",
            ))

        if avg_mfe > 0 and avg_mae > 0 and avg_mfe / avg_mae < 1.5:
            hypotheses.append(Hypothesis(
                description="Improve risk-reward by widening targets or tightening stops",
                parameter_changes={
                    "take_profit_mult": self._adjust_param("take_profit_mult", 0.2, "add"),
                },
                expected_improvement=0.05,
                confidence=0.5,
                source=f"poor_rr_ratio (MFE/MAE={avg_mfe / avg_mae:.2f})",
            ))

        self.hypotheses = hypotheses
        log.info("Generated %d hypotheses from observations", len(hypotheses))

        return hypotheses

    def _adjust_param(
        self,
        param_name: str,
        delta: float,
        operation: str = "add",
    ) -> Any:
        """Propose a parameter adjustment respecting drift limits.

        Args:
            param_name: Name of the parameter to adjust.
            delta: Amount to change.
            operation: 'add' for additive, 'mult' for multiplicative.

        Returns:
            Proposed new value, or None if drift exceeded.
        """
        current = self.params.get(param_name)
        baseline = self.baseline_params.get(param_name)

        if current is None or not isinstance(current, (int, float)):
            return delta  # new param

        if operation == "add":
            proposed = current + delta
        else:
            proposed = current * delta

        # Check drift from baseline
        if baseline is not None and baseline != 0:
            drift = abs(proposed - baseline) / abs(baseline)
            if drift > self.max_drift_pct:
                log.warning(
                    "Drift limit hit for %s: proposed %.3f (drift %.1f%% > %.1f%%)",
                    param_name, proposed, drift * 100, self.max_drift_pct * 100,
                )
                # Clamp to max drift
                max_val = baseline * (1 + self.max_drift_pct)
                min_val = baseline * (1 - self.max_drift_pct)
                proposed = max(min_val, min(max_val, proposed))

        return proposed

    # ------------------------------------------------------------------
    # Phase 3: TEST
    # ------------------------------------------------------------------

    def test(
        self,
        hypothesis: Hypothesis,
        backtest_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Walk-forward test a hypothesis.

        Runs the backtest with current params (control) and with
        proposed changes (treatment). Compares Sharpe ratios.

        Args:
            hypothesis: The hypothesis to test.
            backtest_fn: Function that takes a params dict and returns
                         a result dict with at minimum 'sharpe', 'win_rate',
                         'max_drawdown', 'n_trades'.

        Returns:
            Dict with 'control', 'treatment', 'improvement', 'significant'.
        """
        self.state = LearningState.TEST

        log.info("Testing hypothesis: %s", hypothesis.description)

        # Control: current params
        try:
            control = backtest_fn(self.params)
        except Exception as e:
            log.error("Control backtest failed: %s", e)
            return {"error": str(e), "significant": False, "improvement": 0.0}

        # Treatment: params with proposed changes
        treatment_params = dict(self.params)
        for k, v in hypothesis.parameter_changes.items():
            treatment_params[k] = v

        try:
            treatment = backtest_fn(treatment_params)
        except Exception as e:
            log.error("Treatment backtest failed: %s", e)
            return {"error": str(e), "significant": False, "improvement": 0.0}

        # Compare
        control_sharpe = control.get("sharpe", 0.0)
        treatment_sharpe = treatment.get("sharpe", 0.0)
        improvement = treatment_sharpe - control_sharpe

        # Simple significance check: improvement must exceed minimum
        # and treatment must not have worse drawdown
        control_dd = control.get("max_drawdown", 0.0)
        treatment_dd = treatment.get("max_drawdown", 0.0)
        significant = (
            improvement >= self.min_improvement
            and treatment_dd <= control_dd * 1.2  # don't accept 20% worse drawdown
            and treatment.get("n_trades", 0) >= 10  # minimum trade count
        )

        result = {
            "control": control,
            "treatment": treatment,
            "improvement": improvement,
            "significant": significant,
            "hypothesis": hypothesis.description,
            "parameter_changes": hypothesis.parameter_changes,
        }

        hypothesis.tested = True
        hypothesis.result = result

        log.info(
            "Test result: sharpe %.3f -> %.3f (improvement=%.3f, significant=%s)",
            control_sharpe, treatment_sharpe, improvement, significant,
        )

        return result

    # ------------------------------------------------------------------
    # Phase 4: ADAPT
    # ------------------------------------------------------------------

    def adapt(self, test_results: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Apply parameter changes if test results show sufficient improvement.

        Only applies changes that:
          1. Show improvement above min_improvement threshold
          2. Don't worsen max drawdown by more than 20%
          3. Stay within max_drift_pct of baseline params

        Args:
            test_results: Output from test().

        Returns:
            New params dict if adapted, None if rejected.
        """
        self.state = LearningState.ADAPT

        if not test_results.get("significant", False):
            log.info("Adaptation rejected: test not significant")
            return None

        param_changes = test_results.get("parameter_changes", {})
        if not param_changes:
            return None

        # Apply changes
        old_params = dict(self.params)
        for k, v in param_changes.items():
            self.params[k] = v

        improvement = test_results.get("improvement", 0.0)

        adaptation = {
            "cycle": self.cycle_count,
            "timestamp": time.time(),
            "hypothesis": test_results.get("hypothesis", ""),
            "old_params": old_params,
            "new_params": dict(self.params),
            "improvement": improvement,
            "control_sharpe": test_results.get("control", {}).get("sharpe", 0.0),
            "treatment_sharpe": test_results.get("treatment", {}).get("sharpe", 0.0),
        }
        self.adaptation_history.append(adaptation)

        log.info(
            "Adapted: %d param changes, improvement=%.3f",
            len(param_changes), improvement,
        )

        self._save_state()
        return dict(self.params)

    # ------------------------------------------------------------------
    # Phase 5: VALIDATE
    # ------------------------------------------------------------------

    def validate(self, post_adapt_results: List[Dict[str, Any]]) -> bool:
        """Confirm that the adaptation's improvement persists in new trades.

        If the post-adaptation trades don't show improvement, the
        adaptation is rolled back.

        Args:
            post_adapt_results: Trade results since adaptation was applied.

        Returns:
            True if improvement persists, False if rolled back.
        """
        self.state = LearningState.VALIDATE

        if len(post_adapt_results) < self.validation_window:
            log.info(
                "Validation deferred: %d/%d trades collected",
                len(post_adapt_results), self.validation_window,
            )
            return True  # Provisionally accepted

        # Compute post-adaptation metrics
        pnls = np.array([t.get("pnl", 0.0) for t in post_adapt_results])
        returns = np.array([t.get("return_pct", 0.0) for t in post_adapt_results])
        mean_ret = float(np.mean(returns))
        std_ret = float(np.std(returns))
        post_sharpe = mean_ret / std_ret if std_ret > 1e-8 else 0.0

        # Compare to the control sharpe from the last adaptation
        if self.adaptation_history:
            control_sharpe = self.adaptation_history[-1].get("control_sharpe", 0.0)
            if post_sharpe < control_sharpe * 0.9:
                log.warning(
                    "Validation FAILED: post_sharpe=%.3f < control=%.3f * 0.9. Rolling back.",
                    post_sharpe, control_sharpe,
                )
                # Rollback
                old_params = self.adaptation_history[-1].get("old_params")
                if old_params:
                    self.params = dict(old_params)
                    self._save_state()
                return False

        log.info("Validation passed: post_sharpe=%.3f", post_sharpe)
        return True

    # ------------------------------------------------------------------
    # Full Cycle
    # ------------------------------------------------------------------

    def run_cycle(
        self,
        trade_results: List[Dict[str, Any]],
        backtest_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Run a complete OHTAV learning cycle.

        Args:
            trade_results: Recent trade results.
            backtest_fn: Walk-forward backtest function.

        Returns:
            Dict with:
              - adapted: bool
              - new_params: dict or None
              - observations: observation summary
              - n_hypotheses: number of hypotheses generated
              - best_hypothesis: the hypothesis that was tested/applied
              - cycle: cycle number
        """
        self.cycle_count += 1
        t0 = time.time()

        log.info("=== Learning Cycle %d for %s ===", self.cycle_count, self.strategy_name)

        # 1. Observe
        observations = self.observe(trade_results)
        if observations.get("status") == "no_data":
            return {
                "adapted": False,
                "new_params": None,
                "observations": observations,
                "n_hypotheses": 0,
                "best_hypothesis": None,
                "cycle": self.cycle_count,
            }

        # 2. Hypothesize
        hypotheses = self.hypothesize(observations)
        if not hypotheses:
            log.info("No hypotheses generated — no changes needed")
            return {
                "adapted": False,
                "new_params": None,
                "observations": observations,
                "n_hypotheses": 0,
                "best_hypothesis": None,
                "cycle": self.cycle_count,
            }

        # 3. Test — pick the highest-confidence hypothesis first
        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        best_hyp = hypotheses[0]
        test_result = self.test(best_hyp, backtest_fn)

        # 4. Adapt
        new_params = self.adapt(test_result)
        adapted = new_params is not None

        elapsed = time.time() - t0
        log.info(
            "Cycle %d complete in %.1fs: adapted=%s",
            self.cycle_count, elapsed, adapted,
        )

        self._save_state()

        return {
            "adapted": adapted,
            "new_params": new_params,
            "observations": observations,
            "n_hypotheses": len(hypotheses),
            "best_hypothesis": {
                "description": best_hyp.description,
                "parameter_changes": best_hyp.parameter_changes,
                "confidence": best_hyp.confidence,
                "test_significant": test_result.get("significant", False),
                "improvement": test_result.get("improvement", 0.0),
            },
            "cycle": self.cycle_count,
            "elapsed_seconds": elapsed,
        }

    # ------------------------------------------------------------------
    # State Persistence
    # ------------------------------------------------------------------

    def _save_state(self) -> None:
        """Persist learner state to JSON."""
        os.makedirs(DATA_DIR, exist_ok=True)
        state = {
            "strategy_name": self.strategy_name,
            "params": self.params,
            "baseline_params": self.baseline_params,
            "cycle_count": self.cycle_count,
            "adaptation_history": self.adaptation_history[-50:],  # keep last 50
            "observation_history": self.observation_history[-20:],  # keep last 20
            "state": self.state.value,
            "timestamp": time.time(),
        }
        try:
            with open(self._state_file, "w") as f:
                json.dump(state, f, indent=2, default=str)
            log.debug("State saved to %s", self._state_file)
        except OSError as e:
            log.error("Failed to save state: %s", e)

    def _load_state(self) -> None:
        """Load persisted state if available."""
        if not os.path.exists(self._state_file):
            return
        try:
            with open(self._state_file, "r") as f:
                state = json.load(f)
            self.cycle_count = state.get("cycle_count", 0)
            self.adaptation_history = state.get("adaptation_history", [])
            self.observation_history = state.get("observation_history", [])
            # Restore params if they match the strategy
            if state.get("strategy_name") == self.strategy_name:
                saved_params = state.get("params", {})
                if saved_params:
                    self.params.update(saved_params)
            log.info(
                "Loaded state from %s: cycle=%d, adaptations=%d",
                self._state_file, self.cycle_count, len(self.adaptation_history),
            )
        except (OSError, json.JSONDecodeError, KeyError) as e:
            log.warning("Failed to load state from %s: %s", self._state_file, e)

    def get_drift_report(self) -> Dict[str, Any]:
        """Report how much each parameter has drifted from baseline.

        Returns:
            Dict with param_name -> drift_pct for all numeric params.
        """
        drift: Dict[str, float] = {}
        for k, baseline in self.baseline_params.items():
            current = self.params.get(k)
            if isinstance(baseline, (int, float)) and isinstance(current, (int, float)):
                if baseline != 0:
                    drift[k] = abs(current - baseline) / abs(baseline)
                else:
                    drift[k] = abs(current)
        return {
            "parameter_drift": drift,
            "max_drift": max(drift.values()) if drift else 0.0,
            "n_params_drifted": sum(1 for v in drift.values() if v > 0.01),
            "cycle_count": self.cycle_count,
        }
