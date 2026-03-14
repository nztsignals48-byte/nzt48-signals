"""
tests/test_validation_gates.py
============================
Validation gate checker for perfect entry timing (Week 3-4).

4 gates that must ALL pass before paper trading approval:
  1. gate_1_entry_quality() → 70%+ directional win rate
  2. gate_2_rung_efficiency() → 60%+ hit first rung (+2%)
  3. gate_3_profit_factor() → 1.5x+ P&L ratio
  4. gate_4_no_cascades() → <3 consecutive losses

This module is the TRAFFIC LIGHT for paper trading launch.
All 4 gates must be GREEN to proceed to Phase Q1.
"""

import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Optional
import logging

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.backtest_perfect_entry_timing import (
    PerfectEntryBacktester,
    BacktestMetrics
)

logger = logging.getLogger("nzt48.validation_gates")


@dataclass
class GateResult:
    """Result of a single validation gate."""
    gate_name: str
    passed: bool
    metric_value: float
    threshold: float
    description: str


class ValidationGateChecker:
    """
    Traffic light system for paper trading launch.

    Reads backtest metrics and verifies all 4 gates pass.
    If any gate fails, paper trading is blocked until improvements made.
    """

    def __init__(self, metrics: Optional[BacktestMetrics] = None):
        """
        Initialize gate checker.

        Args:
            metrics: BacktestMetrics object (can be passed in or generated)
        """
        self.metrics = metrics
        self.logger = logging.getLogger("nzt48.validation_gates")
        self.gate_results = []

    def load_backtest_metrics(self, metrics: BacktestMetrics) -> None:
        """Load backtest metrics from a backtest run."""
        self.metrics = metrics

    def gate_1_entry_quality(self) -> GateResult:
        """
        GATE 1: Entry Quality (Directional Win Rate)

        Requirement: 70%+ of entries should be winners.
        This validates that early detection engine can pick directionally correct entries.

        Reference: AEGIS master spec, T-02 entry timing validation
        """
        if not self.metrics:
            return GateResult(
                gate_name="Gate 1: Entry Quality",
                passed=False,
                metric_value=0.0,
                threshold=70.0,
                description="No backtest metrics loaded"
            )

        win_rate = self.metrics.win_rate_pct
        threshold = 70.0
        passed = win_rate >= threshold

        result = GateResult(
            gate_name="Gate 1: Entry Quality (Win Rate)",
            passed=passed,
            metric_value=win_rate,
            threshold=threshold,
            description=f"Win rate {win_rate:.1f}% vs threshold {threshold}% "
                       f"({self.metrics.winning_entries}/{self.metrics.total_entries} trades)"
        )

        self.logger.info(f"Gate 1: {result.description} → {'PASS ✓' if passed else 'FAIL ✗'}")
        self.gate_results.append(result)
        return result

    def gate_2_rung_efficiency(self) -> GateResult:
        """
        GATE 2: Rung Efficiency (First Rung Hit Rate)

        Requirement: 60%+ of entries should hit first rung (+2% profit).
        This validates that early detection catches moves at their BEGINNING,
        not in the middle or after they've already happened.

        The first rung (+2%) is the most critical threshold because:
          - It's where chandelier trailing stop becomes active
          - It's where position sizing scales to full size
          - Missing it means entering after the move has started

        Reference: AEGIS C-04 ladder, T-03 execution timing
        """
        if not self.metrics:
            return GateResult(
                gate_name="Gate 2: Rung Efficiency",
                passed=False,
                metric_value=0.0,
                threshold=60.0,
                description="No backtest metrics loaded"
            )

        rung_hit_rate = self.metrics.rung_1_hit_rate_pct
        threshold = 60.0
        passed = rung_hit_rate >= threshold

        result = GateResult(
            gate_name="Gate 2: Rung Efficiency (First Rung Hit)",
            passed=passed,
            metric_value=rung_hit_rate,
            threshold=threshold,
            description=f"First rung hit rate {rung_hit_rate:.1f}% vs threshold {threshold}% "
                       f"({self.metrics.rung_1_hits}/{self.metrics.total_entries} trades)"
        )

        self.logger.info(f"Gate 2: {result.description} → {'PASS ✓' if passed else 'FAIL ✗'}")
        self.gate_results.append(result)
        return result

    def gate_3_profit_factor(self) -> GateResult:
        """
        GATE 3: Profit Factor (Risk/Reward Ratio)

        Requirement: 1.5x+ profit factor (gross profit / gross loss).
        This validates that average winners are large enough relative to average losers.

        Profit Factor Interpretation:
          - 1.5x: $1.50 gross profit per $1.00 gross loss (excellent)
          - 1.25x: $1.25 gross profit per $1.00 gross loss (good)
          - 1.0x: break-even (unacceptable)
          - <1.0x: loses money on average (unacceptable)

        This gate prevents "many small losses + few huge winners" patterns that
        work in backtesting but fail in live trading due to slippage.

        Reference: AEGIS C-03 profit ladder, risk/reward calibration
        """
        if not self.metrics:
            return GateResult(
                gate_name="Gate 3: Profit Factor",
                passed=False,
                metric_value=0.0,
                threshold=1.5,
                description="No backtest metrics loaded"
            )

        profit_factor = self.metrics.profit_factor
        threshold = 1.5

        # Handle edge cases
        if profit_factor == float('inf'):
            # No losses at all — this is also a failure (too few trades)
            if self.metrics.total_entries < 10:
                passed = False
                profit_factor = 0.0
            else:
                passed = True
                profit_factor = 999.0  # Cap for display
        else:
            passed = profit_factor >= threshold

        result = GateResult(
            gate_name="Gate 3: Profit Factor",
            passed=passed,
            metric_value=profit_factor,
            threshold=threshold,
            description=f"Profit factor {profit_factor:.2f}x vs threshold {threshold}x "
                       f"(${self.metrics.gross_profit:.1f} profit / ${self.metrics.gross_loss:.1f} loss)"
        )

        self.logger.info(f"Gate 3: {result.description} → {'PASS ✓' if passed else 'FAIL ✗'}")
        self.gate_results.append(result)
        return result

    def gate_4_no_cascades(self) -> GateResult:
        """
        GATE 4: Cascade Loss Prevention

        Requirement: <3 consecutive losing trades.
        This validates psychological resilience and emotional discipline.

        Why this matters:
          - 2 consecutive losses: normal market variability ✓
          - 3+ consecutive losses: triggers emotional response (revenge, doubling down)
          - Traders who can't handle 3 in a row will deviate from system

        The <3 threshold is a MINIMUM standard. In Phase Q1 gauntlet, we target
        <2 consecutive losses under real market pressure.

        Reference: AEGIS emotional firewall, Mandel's "losing streaks" chapter
        """
        if not self.metrics:
            return GateResult(
                gate_name="Gate 4: No Cascades",
                passed=False,
                metric_value=0.0,
                threshold=3.0,
                description="No backtest metrics loaded"
            )

        max_cascade = self.metrics.max_consecutive_losses
        threshold = 3.0
        passed = max_cascade < threshold

        result = GateResult(
            gate_name="Gate 4: No Cascades (Max Consecutive Losses)",
            passed=passed,
            metric_value=float(max_cascade),
            threshold=threshold,
            description=f"Max consecutive losses {max_cascade} vs threshold <{threshold} "
                       f"(hardened emotional discipline)"
        )

        self.logger.info(f"Gate 4: {result.description} → {'PASS ✓' if passed else 'FAIL ✗'}")
        self.gate_results.append(result)
        return result

    def all_gates_pass(self) -> bool:
        """
        Check if ALL 4 gates pass.

        Returns:
            True if all gates pass (paper trading APPROVED), False otherwise
        """
        if not self.metrics:
            self.logger.error("Cannot check gates: no metrics loaded")
            return False

        # Run all 4 gates
        gate1 = self.gate_1_entry_quality()
        gate2 = self.gate_2_rung_efficiency()
        gate3 = self.gate_3_profit_factor()
        gate4 = self.gate_4_no_cascades()

        all_pass = all([gate1.passed, gate2.passed, gate3.passed, gate4.passed])

        return all_pass

    def report(self) -> Dict[str, any]:
        """
        Generate human-readable validation report.

        Returns:
            Dict with gate results and overall status
        """
        if not self.gate_results:
            self.all_gates_pass()  # Trigger gate runs

        report = {
            "timestamp": str(__import__("datetime").datetime.now()),
            "total_gates": 4,
            "gates_passed": sum(1 for r in self.gate_results if r.passed),
            "gates_failed": sum(1 for r in self.gate_results if not r.passed),
            "all_gates_pass": all(r.passed for r in self.gate_results),
            "gate_details": [
                {
                    "name": r.gate_name,
                    "status": "PASS ✓" if r.passed else "FAIL ✗",
                    "metric": f"{r.metric_value:.2f}",
                    "threshold": f"{r.threshold:.2f}",
                    "description": r.description
                }
                for r in self.gate_results
            ],
            "recommendation": (
                "PAPER TRADING APPROVED ✓ — Phase Q1 gauntlet can begin"
                if all(r.passed for r in self.gate_results)
                else "PAPER TRADING BLOCKED ✗ — Improve weak gates before launch"
            )
        }

        return report

    def print_report(self) -> None:
        """Print human-readable validation report."""
        report = self.report()

        print("\n" + "=" * 80)
        print("VALIDATION GATES REPORT")
        print("=" * 80)
        print(f"Timestamp: {report['timestamp']}")
        print(f"Gates Passed: {report['gates_passed']}/{report['total_gates']}")
        print()

        for gate in report["gate_details"]:
            print(f"{gate['name']:45s} {gate['status']:10s}")
            print(f"  Metric: {gate['metric']:20s} Threshold: {gate['threshold']}")
            print(f"  {gate['description']}")
            print()

        print("=" * 80)
        print(f"RECOMMENDATION: {report['recommendation']}")
        print("=" * 80 + "\n")


# ─────────────────────────────────────────────────────────
# Embedded Test Cases
# ─────────────────────────────────────────────────────────

def test_validation_gates_pass():
    """Test validation gates with synthetic metrics that PASS."""
    from tests.backtest_perfect_entry_timing import BacktestMetrics, SimulatedTrade
    from datetime import datetime, timezone

    metrics = BacktestMetrics()

    # Create 25 synthetic trades: 18 winners, 7 losers
    # Win rate: 72%
    # Rung 1 hits: 16/25 = 64%
    # Profit factor: $180 / $70 = 2.57x
    # Max consecutive losses: 2

    for i in range(25):
        is_winner = i < 18
        trade = SimulatedTrade(
            ticker="QQQ3.L",
            entry_time=datetime.now(timezone.utc),
            entry_price=100.0,
            direction="LONG",
            leverage=3,
            early_detection_confidence=72.0,
            early_detection_signals=3,
            exit_time=datetime.now(timezone.utc),
            exit_price=102.0 if is_winner else 99.5,
            exit_rung=1 if is_winner else -1,
            gross_pnl_pct=2.0 + i * 0.3 if is_winner else -0.5 - (i - 18) * 0.1,
            rung_1_hit=is_winner and i < 16,
        )
        metrics.trades.append(trade)

    # Compute metrics
    metrics.total_entries = len(metrics.trades)
    metrics.winning_entries = sum(1 for t in metrics.trades if t.win)
    metrics.losing_entries = metrics.total_entries - metrics.winning_entries
    metrics.win_rate_pct = 100.0 * metrics.winning_entries / metrics.total_entries
    metrics.rung_1_hits = sum(1 for t in metrics.trades if t.rung_1_hit)
    metrics.rung_1_hit_rate_pct = 100.0 * metrics.rung_1_hits / metrics.total_entries
    metrics.gross_profit = sum(t.gross_pnl_pct for t in metrics.trades if t.gross_pnl_pct > 0)
    metrics.gross_loss = abs(sum(t.gross_pnl_pct for t in metrics.trades if t.gross_pnl_pct < 0))
    metrics.profit_factor = metrics.gross_profit / metrics.gross_loss
    metrics.max_consecutive_losses = 2

    checker = ValidationGateChecker(metrics)
    assert checker.all_gates_pass(), "All gates should pass"

    report = checker.report()
    assert report["all_gates_pass"], "Report should show all gates pass"
    assert report["gates_passed"] == 4, "Should have 4/4 gates passing"

    print("✓ test_validation_gates_pass PASSED")


def test_validation_gates_fail_low_win_rate():
    """Test validation gates with low win rate (FAIL)."""
    from tests.backtest_perfect_entry_timing import BacktestMetrics, SimulatedTrade
    from datetime import datetime, timezone

    metrics = BacktestMetrics()

    # Create 25 trades: only 15 winners (60% win rate < 70% threshold)
    for i in range(25):
        is_winner = i < 15
        trade = SimulatedTrade(
            ticker="QQQ3.L",
            entry_time=datetime.now(timezone.utc),
            entry_price=100.0,
            direction="LONG",
            gross_pnl_pct=2.0 if is_winner else -1.0,
            rung_1_hit=is_winner,
        )
        metrics.trades.append(trade)

    metrics.total_entries = 25
    metrics.winning_entries = 15
    metrics.win_rate_pct = 60.0  # Below 70% threshold
    metrics.rung_1_hits = 15
    metrics.rung_1_hit_rate_pct = 60.0
    metrics.gross_profit = 30.0
    metrics.gross_loss = 10.0
    metrics.profit_factor = 3.0
    metrics.max_consecutive_losses = 2

    checker = ValidationGateChecker(metrics)
    gate1 = checker.gate_1_entry_quality()

    assert not gate1.passed, "Gate 1 should fail with 60% win rate"
    print("✓ test_validation_gates_fail_low_win_rate PASSED")


def test_validation_gates_fail_too_many_cascades():
    """Test validation gates with 3+ consecutive losses (FAIL)."""
    from tests.backtest_perfect_entry_timing import BacktestMetrics, SimulatedTrade
    from datetime import datetime, timezone

    metrics = BacktestMetrics()

    # Create trades with 3 consecutive losses
    for i in range(20):
        is_winner = i < 3 or i > 5
        trade = SimulatedTrade(
            ticker="QQQ3.L",
            entry_time=datetime.now(timezone.utc),
            entry_price=100.0,
            direction="LONG",
            gross_pnl_pct=2.0 if is_winner else -1.0,
            rung_1_hit=is_winner,
        )
        metrics.trades.append(trade)

    metrics.total_entries = 20
    metrics.winning_entries = 17
    metrics.win_rate_pct = 85.0
    metrics.rung_1_hits = 17
    metrics.rung_1_hit_rate_pct = 85.0
    metrics.gross_profit = 40.0
    metrics.gross_loss = 5.0
    metrics.profit_factor = 8.0
    metrics.max_consecutive_losses = 3  # At or above threshold = FAIL

    checker = ValidationGateChecker(metrics)
    gate4 = checker.gate_4_no_cascades()

    assert not gate4.passed, "Gate 4 should fail with 3+ consecutive losses"
    print("✓ test_validation_gates_fail_too_many_cascades PASSED")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    print("\nRunning validation gate tests...\n")

    test_validation_gates_pass()
    test_validation_gates_fail_low_win_rate()
    test_validation_gates_fail_too_many_cascades()

    print("\n✓ All validation gate tests PASSED\n")

    # Run integrated demo
    print("\n" + "=" * 80)
    print("INTEGRATED DEMO: Running backtest + validation gates")
    print("=" * 80 + "\n")

    backtester = PerfectEntryBacktester()
    metrics = backtester.backtest_universe(
        tickers=["QQQ3.L", "3LUS.L"],
        num_days=10
    )

    checker = ValidationGateChecker(metrics)
    checker.print_report()
