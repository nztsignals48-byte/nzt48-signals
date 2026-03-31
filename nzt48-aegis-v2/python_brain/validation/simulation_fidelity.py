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
from typing import Any, Dict, Optional

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
        report.cost_modeling = self._score_costs(config, metrics)

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
        """Fill realism: does slippage model match real conditions?

        Compares simulated fill prices against actual fill prices (if available)
        and computes mean absolute error in basis points.
        """
        score = 50.0  # Baseline

        # Slippage model exists?
        slippage = config.get("risk", {}).get("slippage_assumption_pct", 0)
        if slippage > 0:
            score += 10  # Has slippage model
        else:
            return score  # Can't score further without model

        # Market impact model?
        if config.get("paper_broker", {}).get("market_impact_enabled", False):
            score += 10

        # Partial fills simulated?
        if metrics.get("partial_fills_simulated", False):
            score += 10

        # Compare simulated vs actual fill prices (core fidelity metric)
        sim_fills = metrics.get("simulated_fill_prices", [])
        actual_fills = metrics.get("actual_fill_prices", [])
        if sim_fills and actual_fills and len(sim_fills) == len(actual_fills):
            errors_bps = []
            for sim_p, act_p in zip(sim_fills, actual_fills):
                if act_p > 0:
                    error_bps = abs(sim_p - act_p) / act_p * 10_000
                    errors_bps.append(error_bps)
            if errors_bps:
                mae_bps = sum(errors_bps) / len(errors_bps)
                # Score: 0 bps error = 30 points, 10+ bps = 0 points
                fill_accuracy_score = max(0.0, 30.0 * (1.0 - mae_bps / 10.0))
                score += fill_accuracy_score
                if mae_bps > 5:
                    log.warning("Fill MAE = %.1f bps — simulation may be unrealistic", mae_bps)
        else:
            # No actual data to compare — partial credit for having a model
            score += 10

        return min(100, score)

    def _score_costs(self, config: Dict, metrics: Optional[Dict] = None) -> float:
        """Cost modeling accuracy.

        Compares modeled commission + spread against actual costs when available.
        """
        score = 0.0

        costs = config.get("costs", {})
        if costs.get("ibkr_commission_gbp", 0) > 0:
            score += 20  # Commission modeled
        if costs.get("round_trip_fee_pct", 0) > 0:
            score += 15  # Round-trip fee
        if costs.get("fx_conversion_pct", 0) > 0:
            score += 10  # FX costs

        # Spread cost in fills?
        risk = config.get("risk", {})
        if risk.get("slippage_assumption_pct", 0) > 0:
            score += 15  # Spread/slippage

        # Stamp duty / FTT
        if costs.get("stamp_duty_pct", -1) >= 0:
            score += 10

        # Compare modeled vs actual costs (if live data available)
        m = metrics or {}
        modeled_costs = m.get("modeled_costs_gbp", [])
        actual_costs = m.get("actual_costs_gbp", [])
        if modeled_costs and actual_costs and len(modeled_costs) == len(actual_costs):
            cost_errors = []
            for mod_c, act_c in zip(modeled_costs, actual_costs):
                if act_c > 0:
                    pct_error = abs(mod_c - act_c) / act_c * 100
                    cost_errors.append(pct_error)
            if cost_errors:
                mean_error = sum(cost_errors) / len(cost_errors)
                # Score: 0% error = 30 points, 50%+ error = 0 points
                cost_accuracy = max(0.0, 30.0 * (1.0 - mean_error / 50.0))
                score += cost_accuracy
        else:
            score += 10  # Partial credit when no actual data

        return min(100, score)

    def _score_timing(self, metrics: Dict) -> float:
        """Execution timing realism.

        Analyzes signal-to-fill latency distribution if available.
        """
        score = 60.0  # Reasonable baseline

        # Time-of-day effects modeled?
        if metrics.get("time_of_day_modeled", False):
            score += 10

        # Order latency simulated?
        if metrics.get("order_latency_ms", 0) > 0:
            score += 10

        # Analyze signal-to-fill latency distribution
        latencies_ms = metrics.get("signal_to_fill_latencies_ms", [])
        if latencies_ms and len(latencies_ms) >= 5:
            import math
            sorted_lat = sorted(latencies_ms)
            n = len(sorted_lat)
            median_lat = sorted_lat[n // 2]
            p95_lat = sorted_lat[int(n * 0.95)]
            mean_lat = sum(sorted_lat) / n

            # Realistic latency range: 50-2000ms for paper trading
            # Too low (<10ms) = unrealistic instant fills
            # Too high (>5000ms) = something is broken
            if 50 <= median_lat <= 2000:
                score += 10  # Realistic median
            elif median_lat < 10:
                log.warning("Median latency %.0fms is unrealistically low", median_lat)

            # Latency should have some variance (not constant)
            if n > 1:
                variance = sum((x - mean_lat) ** 2 for x in sorted_lat) / (n - 1)
                cv = math.sqrt(variance) / mean_lat if mean_lat > 0 else 0
                if 0.1 < cv < 3.0:
                    score += 10  # Realistic variance (not fixed delay)

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

    def _score_regime_fidelity(self, metrics: Dict) -> float:
        """Regime fidelity: Jensen-Shannon divergence between paper and live regime distributions.

        Lower JSD = paper and live spend similar time in each regime = higher fidelity.
        JSD is bounded [0, ln(2)] ~ [0, 0.693]. Score maps JSD to 0-100.
        """
        import math

        paper_dist = metrics.get("paper_regime_distribution", {})
        live_dist = metrics.get("live_regime_distribution", {})

        if not paper_dist or not live_dist:
            return 50.0  # No data — neutral score

        # Unify keys
        all_regimes = set(paper_dist.keys()) | set(live_dist.keys())
        if not all_regimes:
            return 50.0

        # Normalize to probability distributions
        p_total = sum(paper_dist.values()) or 1
        l_total = sum(live_dist.values()) or 1
        p = {r: paper_dist.get(r, 0) / p_total for r in all_regimes}
        q = {r: live_dist.get(r, 0) / l_total for r in all_regimes}

        # Compute JSD = 0.5 * KL(P||M) + 0.5 * KL(Q||M) where M = (P+Q)/2
        m = {r: (p[r] + q[r]) / 2.0 for r in all_regimes}

        def _kl(dist_a: dict, dist_b: dict) -> float:
            kl = 0.0
            for r in all_regimes:
                a_val = dist_a[r]
                b_val = dist_b[r]
                if a_val > 1e-12 and b_val > 1e-12:
                    kl += a_val * math.log(a_val / b_val)
            return kl

        jsd = 0.5 * _kl(p, m) + 0.5 * _kl(q, m)

        # Map JSD to score: JSD=0 → 100, JSD>=0.5 → 0
        score = max(0.0, 100.0 * (1.0 - jsd / 0.5))
        return score


# ---------------------------------------------------------------------------
# Fidelity Comparison — Paper vs Live gap analysis
# ---------------------------------------------------------------------------

@dataclass
class FidelityComparison:
    """Side-by-side comparison of live vs paper trading fidelity."""
    live_metrics: Dict[str, Any]
    paper_metrics: Dict[str, Any]
    gaps: Dict[str, float]  # dimension → gap (live_score - paper_score)
    recommendation: str     # "RELIABLE" | "NEEDS_CALIBRATION" | "UNRELIABLE"

    def to_dict(self) -> dict:
        return {
            "gaps": self.gaps,
            "recommendation": self.recommendation,
            "live_summary": {k: v for k, v in self.live_metrics.items()
                           if isinstance(v, (int, float, str, bool))},
            "paper_summary": {k: v for k, v in self.paper_metrics.items()
                             if isinstance(v, (int, float, str, bool))},
        }


def compare_fidelity(
    scorer: FidelityScorer,
    live_metrics: Dict[str, Any],
    paper_metrics: Dict[str, Any],
    config: Dict[str, Any],
) -> FidelityComparison:
    """Compare paper simulation fidelity against live trading data.

    Produces gap analysis and actionable recommendation.
    """
    live_report = scorer.score(live_metrics, config)
    paper_report = scorer.score(paper_metrics, config)

    gaps = {
        "fill_realism": live_report.fill_realism - paper_report.fill_realism,
        "cost_modeling": live_report.cost_modeling - paper_report.cost_modeling,
        "timing_realism": live_report.timing_realism - paper_report.timing_realism,
        "data_quality": live_report.data_quality - paper_report.data_quality,
        "risk_parity": live_report.risk_parity - paper_report.risk_parity,
        "composite": live_report.composite - paper_report.composite,
    }

    # Recommendation based on composite gap
    abs_gap = abs(gaps["composite"])
    if abs_gap < 10:
        recommendation = "RELIABLE"
    elif abs_gap < 25:
        recommendation = "NEEDS_CALIBRATION"
    else:
        recommendation = "UNRELIABLE"

    return FidelityComparison(
        live_metrics=live_metrics,
        paper_metrics=paper_metrics,
        gaps=gaps,
        recommendation=recommendation,
    )
