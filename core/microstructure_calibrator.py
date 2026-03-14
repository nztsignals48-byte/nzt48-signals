"""
NZT-48 Trading System -- AEGIS K-04: MicrostructureCalibrator
=============================================================
Walk-forward calibration of execution parameters using Implementation
Shortfall (Perold 1988), NOT Information Coefficient.

Walk-forward windows:
  - Training: 20 trading days
  - Testing:  5 trading days
  - Purge:    2 trading days (prevent look-ahead bias; Lopez de Prado 2018)

The calibrator is regime-conditioned: separate parameter sets for each
HMM regime state (trending vs choppy) because microstructure behaviour
differs drastically across regimes (Easley & O'Hara 2010).

Implementation Shortfall (IS) = Decision_Price - Execution_Price
  adjusted for direction. Positive IS = unfavourable slippage.

Calibrated parameters fed to:
  - SpreadTracker (execution/cost_model.py)
  - AdaptiveTWAP slice sizing
  - SpreadCircuitBreaker thresholds

References:
  - Perold, A. (1988). "The Implementation Shortfall: Paper Versus Reality."
    Journal of Portfolio Management, 14(3), 4-9.
  - Lopez de Prado, M. (2018). "Advances in Financial Machine Learning."
    Wiley. Ch. 7: Cross-Validation in Finance.
  - Easley, D. & O'Hara, M. (2010). "Microstructure and Ambiguity."
    Journal of Finance, 65(5), 1817-1846.

AEGIS Phase: Q2 (skeleton only -- full implementation deferred)
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger("nzt48.core.microstructure_calibrator")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class CalibrationRegime(str, Enum):
    """Regime states for conditional calibration.

    Microstructure parameters (spread, impact, fill rate) behave
    differently in trending vs choppy markets. We maintain separate
    calibration sets per regime.
    """
    TRENDING = "TRENDING"
    CHOPPY = "CHOPPY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class TradeRecord:
    """Single trade record for IS computation.

    Attributes:
        ticker: Instrument identifier (e.g. 'QQQ3.L').
        direction: 'LONG' or 'SHORT'.
        decision_price: Mid-price at signal generation time.
        execution_price: Actual fill price (VWAP of all child fills).
        shares: Number of shares executed.
        decision_ts: Timestamp when signal was generated.
        execution_ts: Timestamp when order was fully filled.
        regime: Market regime at time of trade.
        spread_bps_at_decision: Bid-ask spread in bps at decision time.
        order_value_gbp: Total order value in GBP.
    """
    ticker: str
    direction: str
    decision_price: float
    execution_price: float
    shares: int
    decision_ts: datetime
    execution_ts: datetime
    regime: CalibrationRegime = CalibrationRegime.UNKNOWN
    spread_bps_at_decision: float = 0.0
    order_value_gbp: float = 0.0


@dataclass
class CalibrationResult:
    """Output of a single walk-forward calibration fold.

    Attributes:
        fold_id: Sequential fold number (0-indexed).
        train_start: First day of training window.
        train_end: Last day of training window.
        test_start: First day of test window.
        test_end: Last day of test window.
        regime: Regime this calibration applies to.
        mean_is_bps: Mean Implementation Shortfall in bps (training set).
        median_is_bps: Median IS in bps.
        p95_is_bps: 95th percentile IS in bps.
        optimal_spread_threshold_bps: Calibrated spread gate threshold.
        optimal_impact_coefficient: Calibrated market impact coefficient.
        oos_mean_is_bps: Out-of-sample (test) mean IS in bps.
        n_trades_train: Number of trades in training window.
        n_trades_test: Number of trades in test window.
        timestamp: When this calibration was produced.
    """
    fold_id: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    regime: CalibrationRegime
    mean_is_bps: float = 0.0
    median_is_bps: float = 0.0
    p95_is_bps: float = 0.0
    optimal_spread_threshold_bps: float = 0.0
    optimal_impact_coefficient: float = 15.0  # default from cost_model.py
    oos_mean_is_bps: float = 0.0
    n_trades_train: int = 0
    n_trades_test: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Walk-forward calibration constants
# ---------------------------------------------------------------------------

_TRAIN_DAYS: int = 20     # training window
_TEST_DAYS: int = 5       # test (out-of-sample) window
_PURGE_DAYS: int = 2      # gap between train and test (anti-leakage)
_MIN_TRADES_PER_FOLD: int = 10   # minimum trades required to calibrate
_MAX_HISTORY_TRADES: int = 5000  # cap on retained trade history


class MicrostructureCalibrator:
    """Walk-forward calibration engine for execution parameters.

    Uses Implementation Shortfall (IS) as the sole calibration metric.
    IS = (execution_price - decision_price) / decision_price * 10_000
    (sign-adjusted for direction: positive = unfavourable).

    Walk-forward protocol:
        1. Slide a (20-day train, 2-day purge, 5-day test) window
        2. For each fold, compute IS statistics on training data
        3. Derive optimal spread gate and impact coefficient
        4. Validate on test data (out-of-sample IS)
        5. Store regime-conditioned parameter set

    All calibration is performed lazily on demand (not streaming).

    Thread-safety: NOT thread-safe. Intended to be called from the
    main engine loop during the daily recalibration window only.
    """

    def __init__(
        self,
        train_days: int = _TRAIN_DAYS,
        test_days: int = _TEST_DAYS,
        purge_days: int = _PURGE_DAYS,
        min_trades_per_fold: int = _MIN_TRADES_PER_FOLD,
    ) -> None:
        """Initialise the calibrator.

        Args:
            train_days: Number of trading days in training window.
            test_days: Number of trading days in test window.
            purge_days: Number of purge days between train and test
                        to prevent look-ahead bias.
            min_trades_per_fold: Minimum trades required to produce
                                 a valid calibration fold.
        """
        self._train_days = train_days
        self._test_days = test_days
        self._purge_days = purge_days
        self._min_trades = min_trades_per_fold

        # Trade history buffer: regime -> deque of TradeRecords
        self._history: dict[CalibrationRegime, deque[TradeRecord]] = defaultdict(
            lambda: deque(maxlen=_MAX_HISTORY_TRADES)
        )

        # Latest calibration results per regime
        self._latest_calibration: dict[CalibrationRegime, CalibrationResult] = {}

        # All historical calibration folds (for audit trail)
        self._calibration_history: list[CalibrationResult] = []

        logger.info(
            "MicrostructureCalibrator initialised: train=%d test=%d purge=%d min_trades=%d",
            self._train_days, self._test_days, self._purge_days, self._min_trades,
        )

    # ------------------------------------------------------------------
    # Public: ingest trade data
    # ------------------------------------------------------------------

    def record_trade(self, trade: TradeRecord) -> None:
        """Record a completed trade for future calibration.

        Should be called by the execution layer after every fill
        confirmation with accurate decision and execution prices.

        Args:
            trade: Fully populated TradeRecord with IS-relevant fields.
        """
        self._history[trade.regime].append(trade)
        logger.debug(
            "Recorded trade: %s %s IS=%.1f bps (regime=%s)",
            trade.direction,
            trade.ticker,
            self._compute_is_bps(trade),
            trade.regime.value,
        )

    # ------------------------------------------------------------------
    # Public: run calibration
    # ------------------------------------------------------------------

    def calibrate(
        self,
        regime: CalibrationRegime = CalibrationRegime.UNKNOWN,
        as_of: Optional[datetime] = None,
    ) -> Optional[CalibrationResult]:
        """Run walk-forward calibration for the specified regime.

        Performs a single walk-forward fold using the most recent data.
        Call this daily during the pre-market calibration window.

        Args:
            regime: Which regime to calibrate parameters for.
            as_of: Reference datetime for window boundaries.
                   Defaults to now (UTC).

        Returns:
            CalibrationResult if sufficient data, else None.
            None means "keep using previous calibration or defaults."
        """
        # TODO [Q2]: Implement walk-forward window slicing
        #   1. Sort trades by decision_ts
        #   2. Determine train/purge/test boundaries using as_of
        #   3. Filter trades into train/test sets
        #   4. Require >= min_trades in train set
        #   5. Compute IS distribution on training set
        #   6. Derive optimal parameters via _optimise_parameters()
        #   7. Validate on test set
        #   8. Store result in _latest_calibration and _calibration_history
        logger.warning(
            "MicrostructureCalibrator.calibrate() called — SKELETON, returning None. "
            "Full implementation deferred to Q2."
        )
        return None

    def calibrate_all_regimes(
        self,
        as_of: Optional[datetime] = None,
    ) -> dict[CalibrationRegime, Optional[CalibrationResult]]:
        """Run calibration for all regime states.

        Convenience method that calls calibrate() for each regime.

        Args:
            as_of: Reference datetime. Defaults to now (UTC).

        Returns:
            Dict mapping regime -> CalibrationResult (or None).
        """
        results: dict[CalibrationRegime, Optional[CalibrationResult]] = {}
        for regime in CalibrationRegime:
            results[regime] = self.calibrate(regime=regime, as_of=as_of)
        return results

    # ------------------------------------------------------------------
    # Public: retrieve calibrated parameters
    # ------------------------------------------------------------------

    def get_spread_threshold_bps(
        self,
        regime: CalibrationRegime = CalibrationRegime.UNKNOWN,
    ) -> float:
        """Get the calibrated spread gate threshold for the given regime.

        Falls back to a conservative default (22 bps from cost_model.py)
        when no calibration exists.

        Args:
            regime: Current market regime.

        Returns:
            Spread threshold in bps. Orders should use passive limits
            when live spread exceeds this value.
        """
        cal = self._latest_calibration.get(regime)
        if cal is not None and cal.optimal_spread_threshold_bps > 0:
            return cal.optimal_spread_threshold_bps
        # TODO [Q2]: Fall back to cross-regime average if available
        return 22.0  # Conservative default from cost_model.py

    def get_impact_coefficient(
        self,
        regime: CalibrationRegime = CalibrationRegime.UNKNOWN,
    ) -> float:
        """Get the calibrated market impact coefficient.

        Used in the Almgren & Chriss (2001) square-root model:
        impact_bps = coeff * sqrt(order_value / ADV)

        Args:
            regime: Current market regime.

        Returns:
            Impact coefficient (bps per unit participation).
        """
        cal = self._latest_calibration.get(regime)
        if cal is not None and cal.optimal_impact_coefficient > 0:
            return cal.optimal_impact_coefficient
        # TODO [Q2]: Fall back to cross-regime average if available
        return 15.0  # Conservative default from cost_model.py

    def get_latest_calibration(
        self,
        regime: CalibrationRegime = CalibrationRegime.UNKNOWN,
    ) -> Optional[CalibrationResult]:
        """Get the most recent calibration result for a regime.

        Args:
            regime: Regime to query.

        Returns:
            CalibrationResult or None if never calibrated.
        """
        return self._latest_calibration.get(regime)

    def get_calibration_age_hours(
        self,
        regime: CalibrationRegime = CalibrationRegime.UNKNOWN,
    ) -> float:
        """Hours since last successful calibration for the given regime.

        Returns:
            Hours since last calibration. Returns float('inf') if
            never calibrated.
        """
        cal = self._latest_calibration.get(regime)
        if cal is None:
            return float("inf")
        delta = datetime.now(timezone.utc) - cal.timestamp
        return delta.total_seconds() / 3600.0

    def get_stats(self) -> dict:
        """Return calibrator state for dashboard / monitoring.

        Returns:
            Dict with trade counts, calibration ages, and latest
            parameter values per regime.
        """
        stats: dict = {
            "total_trades": sum(len(dq) for dq in self._history.values()),
            "total_calibration_folds": len(self._calibration_history),
            "regimes": {},
        }
        for regime in CalibrationRegime:
            regime_stats: dict = {
                "trade_count": len(self._history.get(regime, [])),
                "calibration_age_hours": round(
                    self.get_calibration_age_hours(regime), 1
                ),
                "spread_threshold_bps": self.get_spread_threshold_bps(regime),
                "impact_coefficient": self.get_impact_coefficient(regime),
            }
            cal = self._latest_calibration.get(regime)
            if cal is not None:
                regime_stats["last_fold_id"] = cal.fold_id
                regime_stats["oos_mean_is_bps"] = round(cal.oos_mean_is_bps, 1)
            stats["regimes"][regime.value] = regime_stats
        return stats

    # ------------------------------------------------------------------
    # Internal: IS computation
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_is_bps(trade: TradeRecord) -> float:
        """Compute Implementation Shortfall in basis points.

        IS = (exec_price - decision_price) / decision_price * 10_000
        Sign-adjusted: positive = unfavourable execution.

        For LONG: exec > decision = unfavourable (positive IS).
        For SHORT: exec < decision = unfavourable (positive IS).

        Args:
            trade: Trade record with decision and execution prices.

        Returns:
            Implementation Shortfall in bps (positive = bad).
        """
        if trade.decision_price <= 0:
            return 0.0
        raw_is = (
            (trade.execution_price - trade.decision_price)
            / trade.decision_price
            * 10_000
        )
        if trade.direction == "SHORT":
            raw_is = -raw_is  # Invert for shorts
        return raw_is

    # ------------------------------------------------------------------
    # Internal: parameter optimisation (Q2 implementation)
    # ------------------------------------------------------------------

    def _optimise_parameters(
        self,
        trades: list[TradeRecord],
        regime: CalibrationRegime,
    ) -> tuple[float, float]:
        """Derive optimal spread threshold and impact coefficient from IS distribution.

        Algorithm (Q2):
          1. Compute IS for each trade in training set.
          2. Spread threshold = P75(spread_bps where IS > median_IS)
             i.e. avoid trades where the spread predicted bad IS.
          3. Impact coefficient = regression of IS ~ sqrt(order_value/ADV)
             using OLS (Almgren & Chriss 2001).
          4. Clamp both to sane ranges.

        Args:
            trades: Training set trade records.
            regime: Regime context.

        Returns:
            (optimal_spread_threshold_bps, optimal_impact_coefficient)
        """
        # TODO [Q2]: Implement optimisation logic
        #   - Compute IS distribution (mean, median, P75, P95)
        #   - Regress IS against sqrt(participation_rate)
        #   - Derive spread threshold from conditional IS analysis
        #   - Apply regime-specific adjustments
        #   - Clamp to [5.0, 80.0] bps for spread, [5.0, 50.0] for impact
        logger.debug(
            "_optimise_parameters() — SKELETON. trades=%d regime=%s",
            len(trades), regime.value,
        )
        return 22.0, 15.0  # Defaults until Q2

    def _validate_oos(
        self,
        trades: list[TradeRecord],
        spread_threshold_bps: float,
        impact_coefficient: float,
    ) -> float:
        """Validate calibrated parameters on out-of-sample test set.

        Computes mean IS on test trades, filtering by the calibrated
        spread threshold. If OOS IS > train IS * 1.5, the calibration
        is flagged as potentially overfit.

        Args:
            trades: Test set trade records.
            spread_threshold_bps: Calibrated spread threshold to validate.
            impact_coefficient: Calibrated impact coefficient.

        Returns:
            Mean IS in bps on the test set.
        """
        # TODO [Q2]: Implement out-of-sample validation
        #   - Compute IS on test trades
        #   - Compare to training IS distribution
        #   - Log WARNING if OOS IS >> training IS (overfit signal)
        #   - Return OOS mean IS
        logger.debug(
            "_validate_oos() — SKELETON. trades=%d threshold=%.1f coeff=%.1f",
            len(trades), spread_threshold_bps, impact_coefficient,
        )
        return 0.0

    # ------------------------------------------------------------------
    # Persistence (Q2)
    # ------------------------------------------------------------------

    def save_state(self, path: str = "data/microstructure_calibration.json") -> None:
        """Persist calibration results to disk.

        Args:
            path: File path for JSON serialisation.
        """
        # TODO [Q2]: Serialise _latest_calibration and _calibration_history
        #   to JSON for survival across Docker restarts.
        logger.debug("save_state() — SKELETON, no-op. path=%s", path)

    def load_state(self, path: str = "data/microstructure_calibration.json") -> None:
        """Load calibration results from disk.

        Args:
            path: File path for JSON deserialisation.
        """
        # TODO [Q2]: Deserialise from JSON and populate
        #   _latest_calibration and _calibration_history.
        logger.debug("load_state() — SKELETON, no-op. path=%s", path)
