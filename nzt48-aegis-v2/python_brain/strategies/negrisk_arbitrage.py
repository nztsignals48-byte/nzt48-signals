"""NegRisk Multi-Condition Arbitrage for Leveraged ETPs — Book 206.

Adapted from Polymarket NegRisk arbitrage ($29M extracted, 73% of all arb
profits). For ETPs, the analogous structures are:

  1. Bull/Bear pair constraint: if underlying moves +X%, then
     3x Bull moves +3X% and 3x Bear moves -3X%. The pair has a
     mathematical relationship that should hold.

  2. Cross-issuer constraint: two ETPs from different issuers tracking
     the same index should trade at near-identical prices.

  3. Leveraged ETF tracking: L*underlying_return vs actual ETP return.
     Tracking error creates decay arbitrage opportunities.

When any of these relationships deviate beyond transaction costs,
an arbitrage signal is generated.

Data paths:
  - /app/data/negrisk_arb_state.json — basket definitions + recent mispricings

Bridge.py integration:
    try:
        from python_brain.strategies.negrisk_arbitrage import (
            NegRiskDetector, ArbitrageSignalGenerator,
            LeveragedETFArbitrage, BasketDefinition,
        )
    except ImportError:
        pass

Usage:
    basket = BasketDefinition(
        instruments=["3LTS.L", "3STS.L"],
        weights=[1.0, 1.0],
        theoretical_sum=200.0,
        tolerance=2.0,
    )
    detector = NegRiskDetector([basket])
    mispricing = detector.check_basket(basket, {"3LTS.L": 105.0, "3STS.L": 92.0})
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger("negrisk_arbitrage")

__all__ = [
    "BasketDefinition",
    "NegRiskDetector",
    "ArbitrageSignalGenerator",
    "LeveragedETFArbitrage",
]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR = Path("/app/data")
STATE_PATH = DATA_DIR / "negrisk_arb_state.json"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class BasketDefinition:
    """Definition of a basket of instruments with a mathematical constraint.

    For NegRisk-style arbitrage, the weighted sum of instrument prices
    should equal theoretical_sum (within tolerance). Deviations = arb.

    Examples:
      - Bull/Bear pair: weights=[1,1], theoretical_sum = bull_price + bear_price at equilibrium
      - Cross-issuer: weights=[1,-1], theoretical_sum = 0 (prices should be equal)
      - Multi-outcome: weights=[1,1,...,1], theoretical_sum = 1.0

    Attributes:
        instruments: List of ticker symbols in the basket.
        weights: Corresponding weights for each instrument.
        theoretical_sum: Expected weighted sum under no-arb condition.
        tolerance: Minimum deviation (in price units) to consider as mispricing.
        name: Optional basket name for logging.
    """
    instruments: List[str] = field(default_factory=list)
    weights: List[float] = field(default_factory=list)
    theoretical_sum: float = 1.0
    tolerance: float = 0.5
    name: str = ""

    def __post_init__(self) -> None:
        if len(self.instruments) != len(self.weights):
            raise ValueError(
                f"instruments ({len(self.instruments)}) and weights "
                f"({len(self.weights)}) must have same length"
            )
        if not self.name:
            self.name = "+".join(self.instruments[:3])

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to dict."""
        return {
            "instruments": self.instruments,
            "weights": self.weights,
            "theoretical_sum": self.theoretical_sum,
            "tolerance": self.tolerance,
            "name": self.name,
        }


# ---------------------------------------------------------------------------
# NegRisk Detector
# ---------------------------------------------------------------------------
class NegRiskDetector:
    """Detects mispricings in instrument baskets.

    Monitors baskets of correlated instruments and flags when the weighted
    sum deviates from its theoretical value by more than the tolerance.
    """

    def __init__(self, baskets: Optional[List[BasketDefinition]] = None) -> None:
        """Initialise NegRisk detector with basket definitions.

        Args:
            baskets: List of basket definitions to monitor.
        """
        self._baskets = baskets or []
        self._mispricing_history: Deque[Dict[str, Any]] = deque(maxlen=5000)
        self._check_count: int = 0
        log.info(
            "NegRiskDetector: %d baskets registered", len(self._baskets),
        )

    def add_basket(self, basket: BasketDefinition) -> None:
        """Register a new basket to monitor.

        Args:
            basket: Basket definition.
        """
        self._baskets.append(basket)
        log.info("Added basket: %s (%d instruments)", basket.name, len(basket.instruments))

    def check_basket(
        self,
        basket: BasketDefinition,
        current_prices: Dict[str, float],
    ) -> Optional[Dict[str, Any]]:
        """Check a single basket for mispricing.

        Computes the weighted sum of current prices and compares to
        the theoretical value. If deviation exceeds tolerance, returns
        mispricing info.

        Args:
            basket: Basket definition.
            current_prices: Dict mapping ticker -> current price.

        Returns:
            Mispricing dict or None if within tolerance.
        """
        self._check_count += 1

        actual = self._basket_value(basket, current_prices)
        if actual is None:
            return None

        theoretical = basket.theoretical_sum
        deviation = actual - theoretical

        if not self._is_mispriced(actual, theoretical, basket.tolerance):
            return None

        direction = "OVERPRICED" if deviation > 0 else "UNDERPRICED"

        mispricing = {
            "basket": basket.name,
            "instruments": basket.instruments,
            "actual_sum": round(actual, 6),
            "theoretical_sum": round(theoretical, 6),
            "deviation": round(deviation, 6),
            "deviation_pct": round(
                abs(deviation) / theoretical * 100 if theoretical != 0 else 0.0, 4,
            ),
            "direction": direction,
            "tolerance": basket.tolerance,
            "prices": {t: current_prices.get(t, 0.0) for t in basket.instruments},
            "ts": time.time(),
        }

        self._mispricing_history.append(mispricing)
        log.info(
            "Mispricing detected: %s dev=%.4f (%s) [%s]",
            basket.name, deviation, direction,
            ", ".join(f"{t}={current_prices.get(t, 0):.2f}" for t in basket.instruments),
        )
        return mispricing

    def _basket_value(
        self,
        basket: BasketDefinition,
        prices: Dict[str, float],
    ) -> Optional[float]:
        """Compute weighted sum of basket instrument prices.

        Args:
            basket: Basket definition.
            prices: Dict mapping ticker -> current price.

        Returns:
            Weighted sum, or None if any instrument is missing.
        """
        total = 0.0
        for ticker, weight in zip(basket.instruments, basket.weights):
            price = prices.get(ticker)
            if price is None:
                log.debug("Missing price for %s in basket %s", ticker, basket.name)
                return None
            total += weight * price
        return total

    def _is_mispriced(
        self,
        actual: float,
        theoretical: float,
        tolerance: float,
    ) -> bool:
        """Check if deviation exceeds tolerance.

        Args:
            actual: Actual weighted sum.
            theoretical: Theoretical weighted sum.
            tolerance: Minimum deviation in price units.

        Returns:
            True if abs(actual - theoretical) > tolerance.
        """
        return abs(actual - theoretical) > tolerance

    def check_all(self, prices: Dict[str, float]) -> List[Dict[str, Any]]:
        """Check all registered baskets for mispricings.

        Args:
            prices: Dict mapping ticker -> current price.

        Returns:
            List of mispricing dicts (only those that exceeded tolerance).
        """
        results = []
        for basket in self._baskets:
            mispricing = self.check_basket(basket, prices)
            if mispricing is not None:
                results.append(mispricing)
        return results

    def recent_mispricings(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return recent mispricing detections.

        Args:
            limit: Maximum number of recent mispricings to return.

        Returns:
            List of mispricing dicts, newest first.
        """
        items = list(self._mispricing_history)
        items.reverse()
        return items[:limit]


# ---------------------------------------------------------------------------
# Arbitrage Signal Generator
# ---------------------------------------------------------------------------
class ArbitrageSignalGenerator:
    """Converts mispricing detections into executable arbitrage signals.

    Computes expected profit net of costs and only fires if profit
    exceeds minimum threshold.
    """

    def __init__(
        self,
        detector: Optional[NegRiskDetector] = None,
        min_profit: float = 5.0,
        cost_per_leg: float = 2.0,
    ) -> None:
        """Initialise arbitrage signal generator.

        Args:
            detector: NegRiskDetector instance. Creates empty one if None.
            min_profit: Minimum expected profit (GBP) to generate a signal.
            cost_per_leg: Estimated cost per trade leg (commission + spread).
        """
        self._detector = detector or NegRiskDetector()
        self._min_profit = min_profit
        self._cost_per_leg = cost_per_leg
        self._signal_count: int = 0
        log.info(
            "ArbitrageSignalGenerator: min_profit=%.2f cost_per_leg=%.2f",
            min_profit, cost_per_leg,
        )

    @property
    def detector(self) -> NegRiskDetector:
        """Return the underlying NegRisk detector."""
        return self._detector

    def scan_all(self, prices: Dict[str, float]) -> List[Dict[str, Any]]:
        """Scan all baskets and generate executable arbitrage signals.

        Pipeline:
          1. Check all baskets for mispricings
          2. For each mispricing, compute expected profit after costs
          3. If profit > min_profit, generate signal

        Args:
            prices: Dict mapping ticker -> current price.

        Returns:
            List of signal dicts for profitable arbitrage opportunities.
        """
        mispricings = self._detector.check_all(prices)
        signals = []

        for mp in mispricings:
            n_legs = len(mp.get("instruments", []))
            costs = n_legs * self._cost_per_leg

            expected_profit = self.compute_expected_profit(mp, costs)

            if not self.is_executable(expected_profit, self._min_profit):
                log.debug(
                    "Arb not executable: %s profit=%.2f < min=%.2f",
                    mp.get("basket", "?"), expected_profit, self._min_profit,
                )
                continue

            self._signal_count += 1
            signal = {
                "type": "negrisk_arbitrage",
                "basket": mp.get("basket", ""),
                "direction": mp.get("direction", ""),
                "instruments": mp.get("instruments", []),
                "deviation": mp.get("deviation", 0.0),
                "deviation_pct": mp.get("deviation_pct", 0.0),
                "expected_profit": round(expected_profit, 2),
                "costs": round(costs, 2),
                "net_profit": round(expected_profit - costs, 2),
                "prices": mp.get("prices", {}),
                "confidence": self._profit_to_confidence(expected_profit, costs),
                "source": "negrisk_arb",
                "signal_number": self._signal_count,
                "ts": time.time(),
            }
            signals.append(signal)

            log.info(
                "Arb Signal #%d: %s dev=%.4f profit=%.2f net=%.2f",
                self._signal_count, signal["basket"],
                signal["deviation"], expected_profit, signal["net_profit"],
            )

        return signals

    def compute_expected_profit(
        self,
        mispricing: Dict[str, Any],
        costs: float,
    ) -> float:
        """Compute expected profit from a mispricing.

        Expected profit = abs(deviation) * position_value - costs
        This assumes we can capture the full deviation as the basket
        mean-reverts. In practice, we capture a fraction.

        Args:
            mispricing: Mispricing dict from detector.
            costs: Total estimated costs for all legs.

        Returns:
            Expected profit in GBP (before position sizing).
        """
        deviation = abs(mispricing.get("deviation", 0.0))
        # Assume we trade 1 unit of each basket leg
        # Profit = deviation captured minus costs
        expected = deviation - costs
        return expected

    def is_executable(
        self,
        profit: float,
        min_profit: float = 5.0,
    ) -> bool:
        """Check if expected profit exceeds minimum threshold.

        Args:
            profit: Expected profit in GBP.
            min_profit: Minimum acceptable profit.

        Returns:
            True if profit > min_profit.
        """
        return profit > min_profit

    def _profit_to_confidence(self, profit: float, costs: float) -> float:
        """Map profit to confidence score (0-100).

        Higher profit relative to costs = higher confidence.

        Args:
            profit: Expected gross profit.
            costs: Total costs.

        Returns:
            Confidence score in [0, 100].
        """
        if costs <= 0:
            return 80.0
        ratio = profit / costs  # profit-to-cost ratio
        # ratio of 2x costs → ~60, 5x costs → ~80, 10x+ → ~90
        confidence = 50.0 + min(ratio * 5.0, 40.0)
        return round(max(0.0, min(100.0, confidence)), 1)

    @property
    def signal_count(self) -> int:
        """Return total signals generated."""
        return self._signal_count


# ---------------------------------------------------------------------------
# Leveraged ETF Arbitrage
# ---------------------------------------------------------------------------
class LeveragedETFArbitrage:
    """Detects tracking error and volatility decay in leveraged ETPs.

    Leveraged ETPs aim to deliver L * underlying_return daily. In practice,
    they deviate due to:
      - Rebalancing mechanics (daily reset)
      - Volatility drag (compounding penalty)
      - Tracking error (imperfect replication)

    When tracking error or decay exceeds norms, arbitrage signals emerge.
    """

    def __init__(
        self,
        leverage_ratios: Optional[Dict[str, float]] = None,
        tracking_error_threshold: float = 0.5,
        decay_history_len: int = 500,
    ) -> None:
        """Initialise leveraged ETF arbitrage detector.

        Args:
            leverage_ratios: Dict mapping ticker -> leverage factor.
                             E.g., {"3LTS.L": 3.0, "3STS.L": -3.0}.
            tracking_error_threshold: Min tracking error (%) to flag.
            decay_history_len: Length of decay history buffer.
        """
        self._leverage_ratios = leverage_ratios or {}
        self._threshold = tracking_error_threshold
        self._decay_history: Deque[Dict[str, Any]] = deque(maxlen=decay_history_len)
        self._check_count: int = 0
        log.info(
            "LeveragedETFArbitrage: %d instruments, threshold=%.2f%%",
            len(self._leverage_ratios), tracking_error_threshold,
        )

    def add_instrument(self, ticker: str, leverage: float) -> None:
        """Register a leveraged instrument.

        Args:
            ticker: Instrument ticker.
            leverage: Leverage factor (e.g., 3.0 for 3x bull, -3.0 for 3x bear).
        """
        self._leverage_ratios[ticker] = leverage
        log.info("Registered %s with leverage %.1fx", ticker, leverage)

    def check_leverage_ratio(
        self,
        etp_return: float,
        underlying_return: float,
        leverage: float,
        ticker: str = "",
    ) -> Dict[str, Any]:
        """Check if ETP return matches expected leveraged return.

        Expected: etp_return = leverage * underlying_return
        Tracking error = abs(actual - expected)

        Args:
            etp_return: Actual ETP return (fractional, e.g., 0.03 = 3%).
            underlying_return: Underlying index return (fractional).
            leverage: Expected leverage factor.
            ticker: Optional ticker for logging.

        Returns:
            Dict with expected return, tracking error, deviation direction.
        """
        self._check_count += 1
        expected_return = leverage * underlying_return
        tracking_error = etp_return - expected_return
        tracking_error_pct = abs(tracking_error) * 100.0

        result = {
            "ticker": ticker,
            "etp_return": round(etp_return, 6),
            "underlying_return": round(underlying_return, 6),
            "leverage": leverage,
            "expected_return": round(expected_return, 6),
            "tracking_error": round(tracking_error, 6),
            "tracking_error_pct": round(tracking_error_pct, 4),
            "exceeds_threshold": tracking_error_pct > self._threshold,
            "direction": "OUTPERFORM" if tracking_error > 0 else "UNDERPERFORM",
            "ts": time.time(),
        }

        self._decay_history.append(result)

        if tracking_error_pct > self._threshold:
            log.info(
                "Tracking error: %s %.3f%% (%s) expected=%.4f actual=%.4f",
                ticker, tracking_error_pct, result["direction"],
                expected_return, etp_return,
            )

        return result

    def decay_arbitrage_signal(
        self,
        tracking_error: float,
        threshold: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Generate an arbitrage signal from tracking error.

        When an ETP outperforms its expected leverage ratio, it is
        overpriced relative to the underlying (sell signal).
        When it underperforms, it is underpriced (buy signal).

        Args:
            tracking_error: Signed tracking error (actual - expected).
            threshold: Override threshold in percentage points.

        Returns:
            Signal dict or None if within threshold.
        """
        th = threshold if threshold is not None else self._threshold
        error_pct = abs(tracking_error) * 100.0

        if error_pct <= th:
            return None

        if tracking_error > 0:
            # ETP outperformed → overpriced → sell
            direction = "SELL"
            reason = "etp_outperformance_decay_arb"
        else:
            # ETP underperformed → underpriced → buy
            direction = "BUY"
            reason = "etp_underperformance_decay_arb"

        confidence = 50.0 + min((error_pct - th) * 10.0, 35.0)
        confidence = round(max(50.0, min(90.0, confidence)), 1)

        signal = {
            "type": "leveraged_decay_arb",
            "direction": direction,
            "tracking_error": round(tracking_error, 6),
            "tracking_error_pct": round(error_pct, 4),
            "threshold": th,
            "confidence": confidence,
            "reason": reason,
            "source": "negrisk_arb",
            "ts": time.time(),
        }

        log.info(
            "Decay arb signal: %s error=%.4f%% conf=%.1f",
            direction, error_pct, confidence,
        )
        return signal

    def batch_check(
        self,
        etp_returns: Dict[str, float],
        underlying_return: float,
    ) -> List[Dict[str, Any]]:
        """Check all registered instruments against underlying return.

        Args:
            etp_returns: Dict mapping ticker -> actual ETP return.
            underlying_return: Underlying index return.

        Returns:
            List of check results for instruments exceeding threshold.
        """
        results = []
        for ticker, leverage in self._leverage_ratios.items():
            etp_ret = etp_returns.get(ticker)
            if etp_ret is None:
                continue
            check = self.check_leverage_ratio(
                etp_ret, underlying_return, leverage, ticker,
            )
            if check.get("exceeds_threshold", False):
                results.append(check)
        return results

    def decay_stats(self) -> Dict[str, Any]:
        """Return tracking error statistics from history.

        Returns:
            Dict with mean, max, count of tracking errors.
        """
        if not self._decay_history:
            return {"count": 0, "mean_error_pct": 0.0, "max_error_pct": 0.0}

        errors = [
            abs(h.get("tracking_error", 0.0)) * 100.0
            for h in self._decay_history
        ]
        arr = np.array(errors, dtype=np.float64)
        return {
            "count": len(errors),
            "mean_error_pct": round(float(np.mean(arr)), 4),
            "max_error_pct": round(float(np.max(arr)), 4),
            "std_error_pct": round(float(np.std(arr)), 4),
            "exceed_count": sum(1 for e in errors if e > self._threshold),
        }

    def save_state(self) -> None:
        """Persist state to disk."""
        state = {
            "leverage_ratios": self._leverage_ratios,
            "threshold": self._threshold,
            "check_count": self._check_count,
            "decay_stats": self.decay_stats(),
            "ts": time.time(),
        }
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(STATE_PATH, "w") as f:
                json.dump(state, f, indent=2)
            log.info("State saved to %s", STATE_PATH)
        except OSError as exc:
            log.error("Failed to save state: %s", exc)
