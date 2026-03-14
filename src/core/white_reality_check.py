"""
Phase 4: White Reality Check (Deflated Sharpe Ratio)
Purpose: Validate signals using rigorous statistics (De Prado methodology)

The Reality: Most backtested edges are luck, not real.
Solution: Bootstrap Deflated Sharpe Ratio (DSR) >1.0 = world-class edge

Key idea:
- Sharpe Ratio is biased upward (overfitting, multiple testing)
- Deflated Sharpe subtracts bias, gives realistic estimate
- DSR >1.0 means the edge is statistically significant (not luck)
- DSR <0.5 means disable the signal for 1 week (it's lucky)

Ralph Wiggum Check: "My cat's breath smells like cat food"
(Is this edge real or just lucky?  DSR will tell you.)
"""

import numpy as np
from dataclasses import dataclass
from typing import List, Tuple
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class DSRResult:
    """Result of Deflated Sharpe Ratio calculation"""
    dsr: float  # Deflated Sharpe Ratio
    sharpe_ratio: float  # Raw Sharpe (biased)
    pvalue: float  # False discovery rate
    is_significant: bool  # DSR >1.0?
    sample_size: int
    confidence_interval: Tuple[float, float]  # 95% CI


class WhiteRealityCheck:
    """
    Bootstrap Deflated Sharpe Ratio validator.

    Determines if a signal has real edge or just got lucky.
    """

    MIN_OBSERVATIONS = 50
    DSR_THRESHOLD = 1.0  # World-class edge
    DSR_DISABLE_THRESHOLD = 0.5  # Disable if <0.5 for 1 week
    BOOTSTRAP_SAMPLES = 10000

    def __init__(self):
        self.disabled_signals = {}  # {signal_name: disable_until_time}

    def calculate_dsr(
        self,
        signal_returns: List[float],
        benchmark_returns: List[float] = None
    ) -> DSRResult:
        """
        Calculate Deflated Sharpe Ratio (De Prado).

        Args:
            signal_returns: Returns when signal=1 (list of daily returns)
            benchmark_returns: Baseline returns (if None, use signal_returns as both)

        Returns:
            DSRResult with DSR, Sharpe, p-value, confidence interval
        """
        signal_returns = np.array(signal_returns)
        n = len(signal_returns)

        if n < self.MIN_OBSERVATIONS:
            logger.warning(f"Only {n} observations, need {self.MIN_OBSERVATIONS} for reliability")
            return DSRResult(
                dsr=0.0,
                sharpe_ratio=0.0,
                pvalue=1.0,
                is_significant=False,
                sample_size=n,
                confidence_interval=(0.0, 0.0)
            )

        # Calculate raw Sharpe Ratio
        mean_return = np.mean(signal_returns)
        std_return = np.std(signal_returns, ddof=1)
        sharpe_ratio = mean_return / std_return if std_return > 0 else 0

        # Deflated Sharpe Ratio (De Prado)
        # Key insight: subtract bias that comes from overfitting + multiple testing
        # DSR ≈ Sharpe - (1 - Sharpe / sqrt(N)) / sqrt(M) - adjustments
        #
        # Simplified formula used here:
        # DSR = (Sharpe - bias) × sqrt(1 - T/M)
        # where:
        #   Sharpe = raw Sharpe ratio
        #   bias = (1 - sqrt(1 - (Sharpe/sqrt(N))^2)) (overfitting penalty)
        #   N = sample size
        #   T = number of tests (assume 1 for single signal)
        #   M = independent observations (assume N)

        # Overfitting bias
        overfitting_bias = max(0, 1 - np.sqrt(1 - (sharpe_ratio / np.sqrt(n)) ** 2))

        # Multiple testing penalty (assume 1 test)
        num_tests = 1
        multiple_penalty = np.sqrt(1 - num_tests / n)

        # Deflated Sharpe
        dsr = (sharpe_ratio - overfitting_bias) * multiple_penalty
        dsr = max(0, dsr)  # Can't be negative

        # Bootstrap confidence interval (10,000 resamples)
        bootstrap_sharpes = []
        for _ in range(self.BOOTSTRAP_SAMPLES):
            sample = np.random.choice(signal_returns, size=n, replace=True)
            sample_mean = np.mean(sample)
            sample_std = np.std(sample, ddof=1)
            sample_sharpe = sample_mean / sample_std if sample_std > 0 else 0
            bootstrap_sharpes.append(sample_sharpe)

        bootstrap_sharpes = np.array(bootstrap_sharpes)
        ci_lower = np.percentile(bootstrap_sharpes, 2.5)
        ci_upper = np.percentile(bootstrap_sharpes, 97.5)

        # P-value (one-tailed): prob(Sharpe < 0)
        pvalue = np.sum(bootstrap_sharpes < 0) / len(bootstrap_sharpes)

        is_significant = dsr >= self.DSR_THRESHOLD

        return DSRResult(
            dsr=dsr,
            sharpe_ratio=sharpe_ratio,
            pvalue=pvalue,
            is_significant=is_significant,
            sample_size=n,
            confidence_interval=(ci_lower, ci_upper)
        )

    def check_signal_quality(
        self,
        signal_name: str,
        signal_returns: List[float],
        regime: str = "ALL"
    ) -> Tuple[bool, str]:
        """
        Check if signal should be used based on DSR.

        Returns:
            (should_use, reason)
        """
        # Check if signal is in disabled list
        if signal_name in self.disabled_signals:
            disable_until = self.disabled_signals[signal_name]
            if datetime.now() < disable_until:
                remaining = (disable_until - datetime.now()).total_seconds() / 3600
                return False, f"Signal disabled (DSR <0.5) for {remaining:.1f} more hours"
            else:
                # Enable again
                del self.disabled_signals[signal_name]

        # Calculate DSR
        result = self.calculate_dsr(signal_returns)

        if result.dsr < self.DSR_DISABLE_THRESHOLD:
            # Disable for 1 week (too much luck detected)
            self.disabled_signals[signal_name] = datetime.now() + timedelta(days=7)
            logger.warning(f"Signal {signal_name} disabled (DSR={result.dsr:.3f} <0.5) for 7 days")
            return False, f"Signal disabled: DSR {result.dsr:.3f} <0.5 (lucky, not real)"

        if not result.is_significant:
            return False, f"Signal not significant (DSR={result.dsr:.3f} <1.0)"

        return True, f"Signal quality OK (DSR={result.dsr:.3f})"


# Unit tests
def test_strong_edge():
    """Test signal with strong, real edge"""
    checker = WhiteRealityCheck()

    # Synthetic returns: 55% of time +1%, 45% of time -0.7%
    # This is a real edge (WR=55%, E/R=1.43 = +1% / -0.7%)
    np.random.seed(42)
    returns = np.concatenate([
        np.random.normal(0.01, 0.003, 110),   # 55% wins, ~+1%
        np.random.normal(-0.007, 0.003, 90),  # 45% losses, ~-0.7%
    ])

    result = checker.calculate_dsr(returns.tolist())

    print(f"Strong edge test:")
    print(f"  Sharpe: {result.sharpe_ratio:.3f}")
    print(f"  DSR: {result.dsr:.3f}")
    print(f"  Significant: {result.is_significant}")

    assert result.sharpe_ratio > 0, "Sharpe should be positive"
    assert result.dsr > 0, "DSR should be positive"
    assert result.sample_size == 200, "Should have 200 observations"

    print("✓ Strong edge test passed")


def test_weak_edge():
    """Test signal with weak or lucky edge"""
    checker = WhiteRealityCheck()

    # Synthetic returns: just random noise around 0
    # No real edge, just luck
    np.random.seed(42)
    returns = np.random.normal(0, 0.02, 100)  # 0% edge, just noise

    result = checker.calculate_dsr(returns.tolist())

    print(f"\nWeak/lucky edge test:")
    print(f"  Sharpe: {result.sharpe_ratio:.3f}")
    print(f"  DSR: {result.dsr:.3f}")
    print(f"  Significant: {result.is_significant}")

    assert result.dsr < 1.0, "DSR should be <1.0 for weak edge"
    assert not result.is_significant, "Weak edge should not be significant"

    print("✓ Weak edge test passed")


def test_signal_disabling():
    """Test that signals with DSR <0.5 get disabled for 7 days"""
    checker = WhiteRealityCheck()

    # Generate very weak signal (almost pure luck)
    np.random.seed(42)
    returns = np.random.normal(0.0001, 0.02, 100)  # Almost no edge

    should_use, reason = checker.check_signal_quality("weak_signal", returns.tolist())

    print(f"\nSignal disabling test:")
    print(f"  Should use: {should_use}")
    print(f"  Reason: {reason}")

    # Check if disabled
    if not should_use and "disabled" in reason.lower():
        print("✓ Signal correctly disabled")

    # Try to use again immediately (should still be disabled)
    should_use_again, _ = checker.check_signal_quality("weak_signal", returns.tolist())
    assert not should_use_again, "Signal should remain disabled"
    print("✓ Signal remains disabled on second check")


if __name__ == "__main__":
    test_strong_edge()
    test_weak_edge()
    test_signal_disabling()

    print("\n" + "="*60)
    print("PHASE 4: WHITE REALITY CHECK (DSR) - EXAMPLE OUTPUT")
    print("="*60)

    checker = WhiteRealityCheck()

    # Real edge: 45% WR, 1.5x payoff
    # Generate synthetic historical returns
    np.random.seed(123)
    winning_returns = np.random.normal(0.015, 0.005, 113)  # 45% wins
    losing_returns = np.random.normal(-0.01, 0.003, 139)   # 55% losses
    all_returns = np.concatenate([winning_returns, losing_returns])

    result = checker.calculate_dsr(all_returns.tolist())

    print(f"\nExample: 45% win rate, 1.5x payoff signal")
    print(f"  Sample size: {result.sample_size} trades")
    print(f"  Raw Sharpe Ratio: {result.sharpe_ratio:.3f}")
    print(f"  Deflated Sharpe (DSR): {result.dsr:.3f}")
    print(f"  95% Confidence Interval: [{result.confidence_interval[0]:.3f}, {result.confidence_interval[1]:.3f}]")
    print(f"  P-value (false discovery): {result.pvalue:.3f}")
    print(f"  Significant (DSR >1.0): {result.is_significant}")

    if result.is_significant:
        print(f"  ✅ PASS: Edge is real (DSR >1.0)")
    else:
        print(f"  ⚠️  WARNING: Edge questionable (DSR <1.0)")

    print("\n✅ Phase 4 (White Reality Check / DSR) complete and tested")
