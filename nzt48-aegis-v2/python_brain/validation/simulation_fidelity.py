"""Simulation Fidelity Scoring — Book 69.

Measures how realistic the paper trading simulation is.
The closer paper results match what live trading would produce,
the more confidence we can place in paper validation.

Fidelity dimensions (each scored 0-100):
  1. Fill realism — slippage model accuracy
  2. Cost modeling — commission + spread accuracy
  3. Timing realism — execution delay simulation
  4. Data quality — tick coverage and freshness
  5. Risk parity — same checks in paper as live

Composite score: weighted average of all dimensions.
Score < 60 = paper results are unreliable for promotion decisions.

Usage:
    from python_brain.validation.simulation_fidelity import (
        FidelityScorer, FidelityReport,
    )

    scorer = FidelityScorer()
    report = scorer.score(paper_metrics, config)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict

log = logging.getLogger("simulation_fidelity")


@dataclass
class FidelityReport:
    """Simulation fidelity assessment."""
    fill_realism: float = 0.0      # 0-100
    cost_modeling: float = 0.0     # 0-100
    timing_realism: float = 0.0   # 0-100
    data_quality: float = 0.0     # 0-100
    risk_parity: float = 0.0      # 0-100
    composite: float = 0.0        # Weighted average
    is_reliable: bool = False     # composite >= 60
    issues: list = None

    def __post_init__(self):
        if self.issues is None:
            self.issues = []

    def to_dict(self) -> dict:
        return {
            "fill_realism": round(self.fill_realism, 1),
            "cost_modeling": round(self.cost_modeling, 1),
            "timing_realism": round(self.timing_realism, 1),
            "data_quality": round(self.data_quality, 1),
            "risk_parity": round(self.risk_parity, 1),
            "composite": round(self.composite, 1),
            "is_reliable": self.is_reliable,
            "issues": self.issues,
        }


class FidelityScorer:
    """Score simulation fidelity across 5 dimensions."""

    def score(self, metrics: Dict[str, Any], config: Dict[str, Any]) -> FidelityReport:
        """Score the current simulation setup."""
        report = FidelityReport()

        # 1. Fill realism (30%)
        report.fill_realism = self._score_fills(metrics, config)

        # 2. Cost modeling (25%)
        report.cost_modeling = self._score_costs(config)

        # 3. Timing realism (15%)
        report.timing_realism = self._score_timing(metrics)

        # 4. Data quality (15%)
        report.data_quality = self._score_data(metrics)

        # 5. Risk parity (15%)
        report.risk_parity = self._score_risk_parity(config)

        # Composite
        report.composite = (
            report.fill_realism * 0.30
            + report.cost_modeling * 0.25
            + report.timing_realism * 0.15
            + report.data_quality * 0.15
            + report.risk_parity * 0.15
        )
        report.is_reliable = report.composite >= 60

        return report

    def _score_fills(self, metrics: Dict, config: Dict) -> float:
        """Fill realism: does slippage model match real conditions?"""
        score = 50.0  # Baseline
        issues = []

        # Slippage model exists?
        slippage = config.get("risk", {}).get("slippage_assumption_pct", 0)
        if slippage > 0:
            score += 20  # Has slippage model
        else:
            issues.append("No slippage model (fills at exact limit)")

        # Market impact model?
        if config.get("paper_broker", {}).get("market_impact_enabled", False):
            score += 15
        else:
            issues.append("No market impact model")

        # Partial fills simulated?
        if metrics.get("partial_fills_simulated", False):
            score += 15
        else:
            issues.append("No partial fill simulation")

        return min(100, score)

    def _score_costs(self, config: Dict) -> float:
        """Cost modeling accuracy."""
        score = 0.0

        costs = config.get("costs", {})
        if costs.get("ibkr_commission_gbp", 0) > 0:
            score += 30  # Commission modeled
        if costs.get("round_trip_fee_pct", 0) > 0:
            score += 20  # Round-trip fee
        if costs.get("fx_conversion_pct", 0) > 0:
            score += 15  # FX costs

        # Spread cost in fills?
        risk = config.get("risk", {})
        if risk.get("slippage_assumption_pct", 0) > 0:
            score += 20  # Spread/slippage

        # Stamp duty / FTT
        if costs.get("stamp_duty_pct", -1) >= 0:
            score += 15

        return min(100, score)

    def _score_timing(self, metrics: Dict) -> float:
        """Execution timing realism."""
        score = 60.0  # Reasonable baseline

        # Time-of-day effects modeled?
        if metrics.get("time_of_day_modeled", False):
            score += 20

        # Order latency simulated?
        if metrics.get("order_latency_ms", 0) > 0:
            score += 20

        return min(100, score)

    def _score_data(self, metrics: Dict) -> float:
        """Data quality and coverage."""
        coverage = metrics.get("data_coverage_pct", 80)
        return min(100, coverage)

    def _score_risk_parity(self, config: Dict) -> float:
        """Paper mode uses same risk checks as live?"""
        score = 0.0

        if config.get("risk", {}).get("paper_uses_live_gates", False):
            score += 60  # Critical: same gates
        if config.get("position", {}).get("max_simultaneous_positions", 999) < 10:
            score += 20  # Reasonable position limits
        if config.get("risk", {}).get("portfolio_heat_limit_pct", 100) < 20:
            score += 20  # Heat limit enforced

        return min(100, score)
