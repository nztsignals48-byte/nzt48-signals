"""
AEGIS K-18: 5 Chaos Drills.

Systematic chaos engineering for the trading system. Each drill
simulates a specific failure mode and verifies the system handles
it correctly (halts, degrades gracefully, or recovers).

Drills:
    CD-01 PandasFatFinger   — Corrupted price data (NaN, negative, 1000x spike)
    CD-02 ToxicTsunami      — 50 toxic fills in 60 seconds
    CD-03 PhantomFill       — Fill reported but no position change
    CD-04 AdverseSelectionSniper — Every fill is adversely selected (instant -1R)
    CD-05 RedisLobotomy     — Redis connection lost mid-operation

Reference:
    Netflix Chaos Monkey (2011). Principles of Chaos Engineering.
    Basiri, A. et al. (2016). "Chaos Engineering." IEEE Software.

SKELETON IMPLEMENTATION — Phase K.

Usage:
    python testing/chaos_drills.py --drill CD-01
    python testing/chaos_drills.py --all
"""
from __future__ import annotations

import logging
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.chaos_drills")


@dataclass
class DrillResult:
    """Result of a chaos drill execution."""
    drill_id: str
    drill_name: str
    passed: bool
    start_time: datetime
    end_time: datetime
    elapsed_seconds: float
    checks_total: int = 0
    checks_passed: int = 0
    checks_failed: int = 0
    failure_details: list[str] = field(default_factory=list)
    notes: str = ""


class ChaosDrill(ABC):
    """Base class for all chaos drills."""

    @property
    @abstractmethod
    def drill_id(self) -> str:
        """Drill identifier (e.g. 'CD-01')."""
        ...

    @property
    @abstractmethod
    def drill_name(self) -> str:
        """Human-readable drill name."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Description of what the drill tests."""
        ...

    @abstractmethod
    def run(self, **kwargs) -> DrillResult:
        """Execute the chaos drill.

        Returns:
            DrillResult with pass/fail and details.
        """
        ...

    @abstractmethod
    def verify(self, result: DrillResult, **kwargs) -> DrillResult:
        """Verify system state after the drill.

        Checks that the system responded correctly to the chaos event.

        Args:
            result: The DrillResult from run().

        Returns:
            Updated DrillResult with verification checks.
        """
        ...

    def _make_result(self, passed: bool, checks_total: int,
                     checks_passed: int, failures: list[str],
                     start: datetime, notes: str = "") -> DrillResult:
        """Helper to construct a DrillResult."""
        end = datetime.now(timezone.utc)
        return DrillResult(
            drill_id=self.drill_id,
            drill_name=self.drill_name,
            passed=passed,
            start_time=start,
            end_time=end,
            elapsed_seconds=(end - start).total_seconds(),
            checks_total=checks_total,
            checks_passed=checks_passed,
            checks_failed=checks_total - checks_passed,
            failure_details=failures,
            notes=notes,
        )


class CD01_PandasFatFinger(ChaosDrill):
    """CD-01: Corrupted price data injection.

    Injects NaN, negative, and 1000x spike values into the price DataFrame
    and verifies the indicator engine and risk sizer handle them gracefully
    without crashing or producing nonsensical signals.

    Expected behaviour:
        - IndicatorEngine.compute_all() returns valid IndicatorSnapshot
          (with defaults/zeros for corrupted fields, not NaN/Inf)
        - RiskSizer rejects any signal derived from corrupted data
        - No unhandled exceptions propagate

    TODO (Phase Q2):
        - Import IndicatorEngine and run compute_all with corrupted df
        - Import RiskSizer and verify signal rejection
        - Test with real ISA fund data + injected corruption
    """

    @property
    def drill_id(self) -> str:
        return "CD-01"

    @property
    def drill_name(self) -> str:
        return "PandasFatFinger"

    @property
    def description(self) -> str:
        return "Inject NaN, negative, and 1000x spike values into price data"

    def run(self, **kwargs) -> DrillResult:
        start = datetime.now(timezone.utc)
        checks = 0
        passed = 0
        failures = []

        try:
            import numpy as np
            import pandas as pd

            # --- Create clean test DataFrame ---
            n_bars = 100
            clean_df = pd.DataFrame({
                "Open": np.random.uniform(100, 105, n_bars),
                "High": np.random.uniform(105, 110, n_bars),
                "Low": np.random.uniform(95, 100, n_bars),
                "Close": np.random.uniform(100, 105, n_bars),
                "Volume": np.random.randint(1000, 50000, n_bars),
            })

            # --- Test 1: NaN injection ---
            checks += 1
            nan_df = clean_df.copy()
            nan_df.loc[50, "Close"] = np.nan
            nan_df.loc[51, "High"] = np.nan
            nan_df.loc[52, "Volume"] = np.nan
            # Verify no crash on basic operations
            try:
                _ = nan_df["Close"].mean()
                passed += 1
            except Exception as e:
                failures.append(f"NaN injection caused crash: {e}")

            # --- Test 2: Negative price injection ---
            checks += 1
            neg_df = clean_df.copy()
            neg_df.loc[50, "Close"] = -100.0
            neg_df.loc[51, "Open"] = -50.0
            try:
                mean_close = neg_df["Close"].mean()
                if np.isfinite(mean_close):
                    passed += 1
                else:
                    failures.append("Negative prices produced non-finite mean")
            except Exception as e:
                failures.append(f"Negative price injection caused crash: {e}")

            # --- Test 3: 1000x spike injection ---
            checks += 1
            spike_df = clean_df.copy()
            spike_df.loc[50, "Close"] = 100000.0  # 1000x normal
            spike_df.loc[50, "High"] = 100000.0
            try:
                _ = spike_df["Close"].pct_change()
                passed += 1
            except Exception as e:
                failures.append(f"1000x spike caused crash: {e}")

            # --- Test 4: Zero volume ---
            checks += 1
            zero_vol_df = clean_df.copy()
            zero_vol_df["Volume"] = 0
            try:
                vol_mean = zero_vol_df["Volume"].mean()
                if vol_mean == 0:
                    passed += 1
                else:
                    failures.append("Zero volume not handled correctly")
            except Exception as e:
                failures.append(f"Zero volume caused crash: {e}")

            # --- Test 5: Empty DataFrame ---
            checks += 1
            empty_df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
            try:
                _ = empty_df["Close"].mean()  # Should return NaN, not crash
                passed += 1
            except Exception as e:
                failures.append(f"Empty DataFrame caused crash: {e}")

        except ImportError as e:
            failures.append(f"Required package not available: {e}")

        return self._make_result(
            passed=len(failures) == 0,
            checks_total=checks,
            checks_passed=passed,
            failures=failures,
            start=start,
            notes="SKELETON: Tests basic DataFrame corruption handling. "
                  "Phase Q2 will test IndicatorEngine and RiskSizer integration.",
        )

    def verify(self, result: DrillResult, **kwargs) -> DrillResult:
        """Verify no state corruption after fat finger drill."""
        # SKELETON: In Phase Q2, verify IndicatorEngine state is clean
        result.notes += " | VERIFY: System state check (skeleton — assumed clean)"
        return result


class CD02_ToxicTsunami(ChaosDrill):
    """CD-02: 50 toxic fills in 60 seconds.

    Simulates a scenario where every fill is toxic (adversely selected)
    at high frequency, overwhelming the risk system.

    Expected behaviour:
        - Circuit breakers trip at appropriate thresholds
        - System enters HALT/DEGRADED mode before catastrophic loss
        - Fill toxicity metrics are correctly tracked
        - No fills accepted after circuit breaker trips

    TODO (Phase Q2):
        - Import CircuitBreakerSystem and simulate fill events
        - Verify L1/L2/L3 cascade triggers at correct PnL levels
        - Test concurrent fill processing under load
    """

    @property
    def drill_id(self) -> str:
        return "CD-02"

    @property
    def drill_name(self) -> str:
        return "ToxicTsunami"

    @property
    def description(self) -> str:
        return "Simulate 50 toxic fills in 60 seconds to test circuit breakers"

    def run(self, **kwargs) -> DrillResult:
        start = datetime.now(timezone.utc)
        checks = 0
        passed = 0
        failures = []

        # --- Simulate 50 toxic fills ---
        checks += 1
        toxic_fills = []
        for i in range(50):
            fill = {
                "fill_id": i,
                "ticker": "QQQ3.L",
                "direction": "LONG",
                "fill_price": 100.0 + i * 0.1,  # Progressively worse
                "slippage_bps": 15 + i * 2,       # Increasing slippage
                "latency_ms": 5 + i,               # Increasing latency
                "pnl_r": -0.3 - (i * 0.05),       # Increasingly negative
                "timestamp_ms": time.time() * 1000 + i * 1200,  # ~1.2s apart
            }
            toxic_fills.append(fill)

        # Check: all 50 fills generated
        if len(toxic_fills) == 50:
            passed += 1
        else:
            failures.append(f"Expected 50 toxic fills, got {len(toxic_fills)}")

        # --- Verify toxic detection thresholds ---
        checks += 1
        fills_over_10bps = [f for f in toxic_fills if f["slippage_bps"] > 10]
        if len(fills_over_10bps) == 50:  # All should be toxic
            passed += 1
        else:
            failures.append(f"Expected all fills toxic, got {len(fills_over_10bps)}")

        # --- Verify cumulative loss triggers halt ---
        checks += 1
        cumulative_r = sum(f["pnl_r"] for f in toxic_fills)
        if cumulative_r < -5.0:  # Should easily exceed halt threshold
            passed += 1
            logger.info("CD-02: Cumulative loss = %.1fR (should trigger halt)", cumulative_r)
        else:
            failures.append(f"Cumulative loss {cumulative_r:.1f}R insufficient to trigger halt")

        return self._make_result(
            passed=len(failures) == 0,
            checks_total=checks,
            checks_passed=passed,
            failures=failures,
            start=start,
            notes="SKELETON: Tests fill generation and threshold math. "
                  "Phase Q2 will integrate with CircuitBreakerSystem.",
        )

    def verify(self, result: DrillResult, **kwargs) -> DrillResult:
        """Verify circuit breakers are in correct state after tsunami."""
        result.notes += " | VERIFY: Circuit breaker state check (skeleton)"
        return result


class CD03_PhantomFill(ChaosDrill):
    """CD-03: Phantom Fill — fill reported but no position change.

    Simulates a fill acknowledgement from the broker where the position
    doesn't actually change (phantom fill). This can happen during
    exchange glitches or network partitions.

    Expected behaviour:
        - Position reconciler detects mismatch between expected and actual
        - System enters DEGRADED mode until reconciliation completes
        - Alert generated for manual review
        - No further orders placed until position reconciled

    TODO (Phase Q2):
        - Import IBKRGateway and simulate fill callback without position update
        - Verify position reconciler catches the phantom
        - Test with multiple simultaneous phantom fills
    """

    @property
    def drill_id(self) -> str:
        return "CD-03"

    @property
    def drill_name(self) -> str:
        return "PhantomFill"

    @property
    def description(self) -> str:
        return "Simulate fill reported with no actual position change"

    def run(self, **kwargs) -> DrillResult:
        start = datetime.now(timezone.utc)
        checks = 0
        passed = 0
        failures = []

        # --- Test 1: Phantom fill detection logic ---
        checks += 1
        expected_position = {"ticker": "QQQ3.L", "shares": 100, "direction": "LONG"}
        actual_position = {"ticker": "QQQ3.L", "shares": 0, "direction": None}  # Phantom

        mismatch = (expected_position["shares"] != actual_position["shares"])
        if mismatch:
            passed += 1
            logger.info("CD-03: Phantom fill correctly detected (expected %d, actual %d)",
                        expected_position["shares"], actual_position["shares"])
        else:
            failures.append("Phantom fill not detected")

        # --- Test 2: Partial phantom (got 50 of 100 shares) ---
        checks += 1
        partial_position = {"ticker": "QQQ3.L", "shares": 50, "direction": "LONG"}
        partial_mismatch = (expected_position["shares"] != partial_position["shares"])
        if partial_mismatch:
            passed += 1
        else:
            failures.append("Partial phantom fill not detected")

        # --- Test 3: Duplicate fill detection ---
        checks += 1
        fill_ids_seen = set()
        fills = [
            {"fill_id": "F001", "shares": 100},
            {"fill_id": "F001", "shares": 100},  # Duplicate!
            {"fill_id": "F002", "shares": 50},
        ]
        duplicates = 0
        for fill in fills:
            if fill["fill_id"] in fill_ids_seen:
                duplicates += 1
            fill_ids_seen.add(fill["fill_id"])

        if duplicates == 1:
            passed += 1
        else:
            failures.append(f"Expected 1 duplicate fill, detected {duplicates}")

        return self._make_result(
            passed=len(failures) == 0,
            checks_total=checks,
            checks_passed=passed,
            failures=failures,
            start=start,
            notes="SKELETON: Tests mismatch detection logic. "
                  "Phase Q2 will integrate with position reconciler.",
        )

    def verify(self, result: DrillResult, **kwargs) -> DrillResult:
        """Verify system state after phantom fill drill."""
        result.notes += " | VERIFY: Position reconciliation state (skeleton)"
        return result


class CD04_AdverseSelectionSniper(ChaosDrill):
    """CD-04: Adverse Selection Sniper.

    Every fill is adversely selected — price moves against the position
    immediately after fill. Simulates trading against informed flow.

    Expected behaviour:
        - Fill toxicity detector flags all fills as toxic
        - After N consecutive toxic fills, system pauses execution
        - Toxic flow metrics update correctly
        - Risk sizer reduces position size or bans the ticker

    TODO (Phase Q2):
        - Import fill toxicity tracker and simulate adverse fills
        - Verify toxic fill counter increments correctly
        - Test ticker-level banning after sustained adverse selection
    """

    @property
    def drill_id(self) -> str:
        return "CD-04"

    @property
    def drill_name(self) -> str:
        return "AdverseSelectionSniper"

    @property
    def description(self) -> str:
        return "Every fill is adversely selected (instant move against position)"

    def run(self, **kwargs) -> DrillResult:
        start = datetime.now(timezone.utc)
        checks = 0
        passed = 0
        failures = []

        # --- Simulate 20 adversely selected fills ---
        checks += 1
        adverse_fills = []
        for i in range(20):
            fill_price = 100.0
            # Price immediately moves against (LONG fill, price drops)
            post_fill_price = fill_price - (0.05 * (i + 1))  # Worse each time
            adverse_pnl = post_fill_price - fill_price
            adverse_fills.append({
                "fill_id": i,
                "fill_price": fill_price,
                "post_fill_price_1s": post_fill_price,
                "adverse_pnl": adverse_pnl,
                "adverse_bps": abs(adverse_pnl / fill_price) * 10000,
            })

        # All fills should be adversely selected
        all_adverse = all(f["adverse_pnl"] < 0 for f in adverse_fills)
        if all_adverse:
            passed += 1
        else:
            failures.append("Not all fills were adversely selected")

        # --- Check: average adverse selection increasing ---
        checks += 1
        first_half_avg = sum(f["adverse_bps"] for f in adverse_fills[:10]) / 10
        second_half_avg = sum(f["adverse_bps"] for f in adverse_fills[10:]) / 10
        if second_half_avg > first_half_avg:
            passed += 1
            logger.info("CD-04: Adverse selection increasing (%.1f -> %.1f bps)",
                        first_half_avg, second_half_avg)
        else:
            failures.append("Adverse selection not increasing as expected")

        # --- Check: system should detect pattern by fill 10 ---
        checks += 1
        consecutive_adverse = 0
        detection_threshold = 5
        detected_at = None
        for i, fill in enumerate(adverse_fills):
            if fill["adverse_pnl"] < 0:
                consecutive_adverse += 1
            else:
                consecutive_adverse = 0
            if consecutive_adverse >= detection_threshold and detected_at is None:
                detected_at = i
                break

        if detected_at is not None and detected_at < 10:
            passed += 1
            logger.info("CD-04: Adverse selection pattern detected at fill %d", detected_at)
        else:
            failures.append(f"Adverse selection detection delayed (at fill {detected_at})")

        return self._make_result(
            passed=len(failures) == 0,
            checks_total=checks,
            checks_passed=passed,
            failures=failures,
            start=start,
            notes="SKELETON: Tests adverse selection math and detection logic. "
                  "Phase Q2 will integrate with fill toxicity tracker.",
        )

    def verify(self, result: DrillResult, **kwargs) -> DrillResult:
        """Verify system paused after sustained adverse selection."""
        result.notes += " | VERIFY: Execution pause state (skeleton)"
        return result


class CD05_RedisLobotomy(ChaosDrill):
    """CD-05: Redis Lobotomy — Redis connection lost mid-operation.

    Simulates Redis going offline while the system is mid-operation
    (e.g. reading position state, writing chandelier exit trail).

    Expected behaviour:
        - System detects Redis failure within 5 seconds
        - Falls back to in-memory state (degraded but functional)
        - No orders placed while Redis is offline
        - State reconciled when Redis reconnects
        - No data loss or corruption

    TODO (Phase Q2):
        - Mock Redis client with connection failure injection
        - Test chandelier exit persistence during Redis outage
        - Verify position state recovery after reconnection
        - Test concurrent reads/writes during failure window
    """

    @property
    def drill_id(self) -> str:
        return "CD-05"

    @property
    def drill_name(self) -> str:
        return "RedisLobotomy"

    @property
    def description(self) -> str:
        return "Simulate Redis connection loss during mid-operation"

    def run(self, **kwargs) -> DrillResult:
        start = datetime.now(timezone.utc)
        checks = 0
        passed = 0
        failures = []

        # --- Test 1: Simulate Redis ConnectionError ---
        checks += 1
        try:
            # Simulate what happens when Redis raises ConnectionError
            class MockRedis:
                def __init__(self, fail: bool = False):
                    self._fail = fail

                def get(self, key: str) -> Optional[str]:
                    if self._fail:
                        raise ConnectionError("Redis connection lost")
                    return '{"trailing_stop": 98.5}'

                def set(self, key: str, value: str) -> bool:
                    if self._fail:
                        raise ConnectionError("Redis connection lost")
                    return True

            # Normal operation
            healthy_redis = MockRedis(fail=False)
            val = healthy_redis.get("nzt48:chandelier:QQQ3.L")
            assert val is not None

            # Failed operation — should raise, caller should catch
            failed_redis = MockRedis(fail=True)
            caught_error = False
            try:
                failed_redis.get("nzt48:chandelier:QQQ3.L")
            except ConnectionError:
                caught_error = True

            if caught_error:
                passed += 1
            else:
                failures.append("Redis ConnectionError not raised/caught")

        except Exception as e:
            failures.append(f"Redis failure simulation error: {e}")

        # --- Test 2: Fallback to in-memory state ---
        checks += 1
        in_memory_fallback = {}
        try:
            # Simulate writing to Redis, falling back to memory
            try:
                failed_redis = MockRedis(fail=True)
                failed_redis.set("key", "value")
            except ConnectionError:
                in_memory_fallback["key"] = "value"  # Fallback

            if in_memory_fallback.get("key") == "value":
                passed += 1
            else:
                failures.append("In-memory fallback failed")
        except Exception as e:
            failures.append(f"Fallback test error: {e}")

        # --- Test 3: Reconnection detection ---
        checks += 1
        try:
            reconnected_redis = MockRedis(fail=False)
            val = reconnected_redis.get("test_key")
            # Should succeed after reconnection
            passed += 1
        except Exception as e:
            failures.append(f"Reconnection test failed: {e}")

        return self._make_result(
            passed=len(failures) == 0,
            checks_total=checks,
            checks_passed=passed,
            failures=failures,
            start=start,
            notes="SKELETON: Tests Redis failure/fallback logic with mocks. "
                  "Phase Q2 will test with real Redis and chandelier exit state.",
        )

    def verify(self, result: DrillResult, **kwargs) -> DrillResult:
        """Verify no state corruption after Redis lobotomy."""
        result.notes += " | VERIFY: State consistency check (skeleton)"
        return result


# --- Drill Registry ---
ALL_DRILLS: dict[str, type[ChaosDrill]] = {
    "CD-01": CD01_PandasFatFinger,
    "CD-02": CD02_ToxicTsunami,
    "CD-03": CD03_PhantomFill,
    "CD-04": CD04_AdverseSelectionSniper,
    "CD-05": CD05_RedisLobotomy,
}


def run_drill(drill_id: str, **kwargs) -> DrillResult:
    """Run a single chaos drill by ID.

    Args:
        drill_id: Drill identifier (e.g. "CD-01").

    Returns:
        DrillResult with pass/fail and details.
    """
    drill_cls = ALL_DRILLS.get(drill_id)
    if drill_cls is None:
        raise ValueError(f"Unknown drill: {drill_id}. Available: {list(ALL_DRILLS.keys())}")

    drill = drill_cls()
    logger.info("=== CHAOS DRILL %s: %s ===", drill.drill_id, drill.drill_name)
    logger.info("Description: %s", drill.description)

    result = drill.run(**kwargs)
    result = drill.verify(result, **kwargs)

    status = "PASS" if result.passed else "FAIL"
    logger.info(
        "=== %s %s: %s (%d/%d checks) | %.1fs ===",
        drill.drill_id, drill.drill_name, status,
        result.checks_passed, result.checks_total,
        result.elapsed_seconds,
    )

    if not result.passed:
        for failure in result.failure_details:
            logger.error("  FAILURE: %s", failure)

    return result


def run_all_drills(**kwargs) -> list[DrillResult]:
    """Run all 5 chaos drills sequentially.

    Returns:
        List of DrillResult, one per drill.
    """
    results = []
    for drill_id in sorted(ALL_DRILLS.keys()):
        try:
            result = run_drill(drill_id, **kwargs)
            results.append(result)
        except Exception as e:
            logger.error("Drill %s crashed: %s", drill_id, e)
            results.append(DrillResult(
                drill_id=drill_id,
                drill_name=ALL_DRILLS[drill_id]().drill_name,
                passed=False,
                start_time=datetime.now(timezone.utc),
                end_time=datetime.now(timezone.utc),
                elapsed_seconds=0,
                failure_details=[f"Drill crashed: {e}"],
            ))

    # Summary
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    logger.info(
        "\n=== CHAOS DRILL SUMMARY: %d/%d PASSED ===",
        passed, total,
    )
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        logger.info("  %s %s: %s (%d/%d checks)",
                     r.drill_id, r.drill_name, status,
                     r.checks_passed, r.checks_total)

    return results


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="NZT-48 Chaos Drills (K-18)")
    parser.add_argument("--drill", type=str, default=None,
                        help="Run specific drill (e.g. CD-01). Default: run all.")
    parser.add_argument("--all", action="store_true",
                        help="Run all 5 drills.")
    args = parser.parse_args()

    if args.drill:
        run_drill(args.drill)
    else:
        run_all_drills()
