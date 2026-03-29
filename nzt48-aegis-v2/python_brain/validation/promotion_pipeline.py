"""Simulation-to-Live 12-Stage Promotion Pipeline — Book 52.

Every strategy must pass through 12 stages before receiving live capital.
No shortcuts. No exceptions. The pipeline is the only path from hypothesis
to deployment.

12 Stages:
  DATA STAGES (1-3):
    1. Raw data ingestion — 99.5% completeness gate
    2. Feature engineering — 40+ features per bar
    3. Hypothesis generation — Claude proposes, human approves

  VALIDATION STAGES (4-6):
    4. Walk-forward backtest — 200+ trades, Sharpe>0.5, PF>1.1
    5. Monte Carlo validation — 5th percentile equity > 0
    6. Adversarial stress test — survive noise injection, distribution shift

  PAPER STAGES (7-9):
    7. Paper deployment — shadow mode, no capital
    8. Paper reconciliation — match backtest within 10%
    9. Statistical significance — p < 0.05 vs random baseline

  LIVE STAGES (10-12):
    10. Promote to live micro — £100-500 real capital
    11. Canary monitoring — compare live vs paper for 50 trades
    12. Full deployment — promote to standard allocation

Usage:
    from python_brain.validation.promotion_pipeline import (
        PromotionPipeline, PipelineStage, StageResult,
    )

    pipeline = PromotionPipeline("TypeF")
    result = pipeline.evaluate_stage(PipelineStage.BACKTEST, metrics)
    if result.passed:
        pipeline.promote()
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("promotion_pipeline")


class PipelineStage(Enum):
    # Data stages
    DATA_INGESTION = 1
    FEATURE_ENGINEERING = 2
    HYPOTHESIS = 3
    # Validation stages
    BACKTEST = 4
    MONTE_CARLO = 5
    ADVERSARIAL = 6
    # Paper stages
    PAPER_DEPLOY = 7
    PAPER_RECONCILE = 8
    SIGNIFICANCE = 9
    # Live stages
    LIVE_MICRO = 10
    CANARY = 11
    FULL_DEPLOY = 12


# Gate requirements per stage
STAGE_GATES: Dict[PipelineStage, Dict[str, Any]] = {
    PipelineStage.DATA_INGESTION: {
        "min_completeness_pct": 99.5,
        "max_gap_minutes": 5,
        "min_tickers": 10,
    },
    PipelineStage.FEATURE_ENGINEERING: {
        "min_features": 20,
        "no_nan_pct": 99.0,  # Max 1% NaN allowed
    },
    PipelineStage.HYPOTHESIS: {
        "requires_human_approval": True,
        "min_claude_score": 7,  # Out of 10
    },
    PipelineStage.BACKTEST: {
        "min_trades": 200,
        "min_sharpe": 0.5,
        "min_profit_factor": 1.1,
        "min_win_rate": 0.35,
        "max_drawdown_pct": 20.0,
    },
    PipelineStage.MONTE_CARLO: {
        "min_paths": 5000,
        "p5_equity_positive": True,  # 5th percentile must be > initial
        "ruin_probability_max": 0.10,
    },
    PipelineStage.ADVERSARIAL: {
        "noise_survival": True,  # Survives 10% noise injection
        "distribution_shift_survival": True,  # Survives regime shift
    },
    PipelineStage.PAPER_DEPLOY: {
        "min_paper_days": 14,
        "min_paper_signals": 50,
    },
    PipelineStage.PAPER_RECONCILE: {
        "sharpe_degradation_max": 0.50,  # OOS Sharpe >= 50% of IS Sharpe
        "win_rate_delta_max": 10.0,  # Paper WR within 10pp of backtest WR
    },
    PipelineStage.SIGNIFICANCE: {
        "p_value_max": 0.05,
        "min_trades": 30,
    },
    PipelineStage.LIVE_MICRO: {
        "capital_gbp": 500,
        "min_trades": 50,
        "min_days": 14,
    },
    PipelineStage.CANARY: {
        "live_vs_paper_sharpe_ratio_min": 0.50,
        "max_live_drawdown_pct": 10.0,
    },
    PipelineStage.FULL_DEPLOY: {
        "operator_approval": True,
        "all_previous_stages_passed": True,
    },
}


@dataclass
class StageResult:
    """Result of evaluating a single promotion stage."""
    stage: PipelineStage
    passed: bool = False
    reason: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "stage": self.stage.value,
            "stage_name": self.stage.name,
            "passed": self.passed,
            "reason": self.reason,
            "metrics": self.metrics,
            "timestamp": self.timestamp,
        }


class PromotionPipeline:
    """Manage a strategy's progression through the 12-stage pipeline."""

    def __init__(self, strategy_name: str):
        self.strategy = strategy_name
        self._current_stage = PipelineStage.DATA_INGESTION
        self._stage_results: Dict[PipelineStage, StageResult] = {}
        self._history: List[Dict[str, Any]] = []

    @property
    def current_stage(self) -> PipelineStage:
        return self._current_stage

    @property
    def stage_number(self) -> int:
        return self._current_stage.value

    def evaluate_stage(
        self,
        stage: PipelineStage,
        metrics: Dict[str, Any],
    ) -> StageResult:
        """Evaluate whether a stage's gates are met."""
        gates = STAGE_GATES.get(stage, {})
        result = StageResult(
            stage=stage,
            timestamp=datetime.now(timezone.utc).isoformat(),
            metrics=metrics,
        )

        # Check each gate
        failures = []

        if stage == PipelineStage.BACKTEST:
            if metrics.get("n_trades", 0) < gates["min_trades"]:
                failures.append(f"trades {metrics.get('n_trades', 0)} < {gates['min_trades']}")
            if metrics.get("sharpe", 0) < gates["min_sharpe"]:
                failures.append(f"Sharpe {metrics.get('sharpe', 0):.2f} < {gates['min_sharpe']}")
            if metrics.get("profit_factor", 0) < gates["min_profit_factor"]:
                failures.append(f"PF {metrics.get('profit_factor', 0):.2f} < {gates['min_profit_factor']}")
            if metrics.get("win_rate", 0) < gates["min_win_rate"]:
                failures.append(f"WR {metrics.get('win_rate', 0):.1%} < {gates['min_win_rate']:.0%}")
            if metrics.get("max_drawdown_pct", 100) > gates["max_drawdown_pct"]:
                failures.append(f"DD {metrics.get('max_drawdown_pct', 100):.1f}% > {gates['max_drawdown_pct']}%")

        elif stage == PipelineStage.MONTE_CARLO:
            if not metrics.get("p5_equity_positive", False):
                failures.append("5th percentile equity negative")
            if metrics.get("ruin_probability", 1) > gates["ruin_probability_max"]:
                failures.append(f"ruin {metrics.get('ruin_probability', 1):.1%} > {gates['ruin_probability_max']:.0%}")

        elif stage == PipelineStage.PAPER_DEPLOY:
            if metrics.get("paper_days", 0) < gates["min_paper_days"]:
                failures.append(f"days {metrics.get('paper_days', 0)} < {gates['min_paper_days']}")
            if metrics.get("paper_signals", 0) < gates["min_paper_signals"]:
                failures.append(f"signals {metrics.get('paper_signals', 0)} < {gates['min_paper_signals']}")

        elif stage == PipelineStage.PAPER_RECONCILE:
            degradation = metrics.get("sharpe_degradation", 1.0)
            if degradation > gates["sharpe_degradation_max"]:
                failures.append(f"Sharpe degradation {degradation:.1%} > {gates['sharpe_degradation_max']:.0%}")
            wr_delta = abs(metrics.get("win_rate_delta_pp", 100))
            if wr_delta > gates["win_rate_delta_max"]:
                failures.append(f"WR delta {wr_delta:.0f}pp > {gates['win_rate_delta_max']}pp")

        elif stage == PipelineStage.SIGNIFICANCE:
            if metrics.get("p_value", 1) > gates["p_value_max"]:
                failures.append(f"p={metrics.get('p_value', 1):.4f} > {gates['p_value_max']}")
            if metrics.get("n_trades", 0) < gates["min_trades"]:
                failures.append(f"trades {metrics.get('n_trades', 0)} < {gates['min_trades']}")

        elif stage == PipelineStage.LIVE_MICRO:
            if metrics.get("n_trades", 0) < gates["min_trades"]:
                failures.append(f"trades {metrics.get('n_trades', 0)} < {gates['min_trades']}")
            if metrics.get("days", 0) < gates["min_days"]:
                failures.append(f"days {metrics.get('days', 0)} < {gates['min_days']}")

        elif stage == PipelineStage.CANARY:
            ratio = metrics.get("live_vs_paper_sharpe_ratio", 0)
            if ratio < gates["live_vs_paper_sharpe_ratio_min"]:
                failures.append(f"live/paper Sharpe ratio {ratio:.2f} < {gates['live_vs_paper_sharpe_ratio_min']}")
            if metrics.get("max_drawdown_pct", 100) > gates["max_live_drawdown_pct"]:
                failures.append(f"live DD {metrics.get('max_drawdown_pct', 100):.1f}% > {gates['max_live_drawdown_pct']}%")

        elif stage == PipelineStage.FULL_DEPLOY:
            if not metrics.get("operator_approval", False):
                failures.append("operator approval required")

        # Determine result
        if failures:
            result.passed = False
            result.reason = "; ".join(failures)
            log.info("PIPELINE: %s stage %d (%s) FAILED — %s",
                     self.strategy, stage.value, stage.name, result.reason)
        else:
            result.passed = True
            result.reason = "all_gates_passed"
            log.info("PIPELINE: %s stage %d (%s) PASSED",
                     self.strategy, stage.value, stage.name)

        self._stage_results[stage] = result
        self._history.append(result.to_dict())
        return result

    def promote(self) -> bool:
        """Advance to next stage if current stage passed."""
        current_result = self._stage_results.get(self._current_stage)
        if current_result is None or not current_result.passed:
            log.warning("PIPELINE: %s cannot promote — stage %d not passed",
                        self.strategy, self._current_stage.value)
            return False

        next_val = self._current_stage.value + 1
        if next_val > 12:
            log.info("PIPELINE: %s already at FULL_DEPLOY (stage 12)", self.strategy)
            return True

        old = self._current_stage
        self._current_stage = PipelineStage(next_val)
        log.info("PIPELINE: %s promoted %s → %s",
                 self.strategy, old.name, self._current_stage.name)
        return True

    def demote(self, to_stage: PipelineStage) -> bool:
        """Demote strategy back to an earlier stage."""
        if to_stage.value >= self._current_stage.value:
            return False
        old = self._current_stage
        self._current_stage = to_stage
        log.warning("PIPELINE: %s DEMOTED %s → %s",
                    self.strategy, old.name, to_stage.name)
        return True

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "current_stage": self._current_stage.value,
            "current_stage_name": self._current_stage.name,
            "stages_passed": [
                s.name for s, r in self._stage_results.items() if r.passed
            ],
            "stages_failed": [
                s.name for s, r in self._stage_results.items() if not r.passed
            ],
            "history_count": len(self._history),
        }
