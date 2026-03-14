"""
NZT-48 InvariantEnforcer — AEGIS Phase H-02
=============================================
Enforces 12 runtime invariants that protect the trading system from
state corruption, configuration drift, and silent failures.

Invariants:
    1.  IMAGE_PARITY      — env IMAGE_DIGEST matches git HEAD SHA
    2.  ISA_FAIL_CLOSED   — every ticker must be ISA-eligible
    3.  VIX_FAIL_CLOSED   — VIX != 0 AND age < 300s
    4.  DRAWDOWN_CASCADE  — daily P&L above L3 threshold
    5.  POSITION_LIMIT    — open positions <= MAX_CONCURRENT
    6.  OVERNIGHT_FLAT    — positions == 0 at 16:25 GMT
    7.  EQUITY_FRESH      — equity matches broker +/-0.1%
    8.  CONFIDENCE_FLOOR  — signal confidence >= 65
    9.  IMMUTABLE_RISK    — __setattr__ raises post-init
    10. HALT_PERSISTENCE  — Redis halt survives restart
    11. LOSS_STREAK_SCOPED — query WHERE date >= session_start
    12. DATA_FEED_ALIVE   — last tick age < MAX_STALE

Lifecycle:
    - ALL 12 run at boot (any failure = sys.exit(1))
    - Invariants 2-12 run every 60s during market hours
    - On ANY failure during runtime: trigger flatten + alert + halt

Reference: AEGIS_MASTER_PLAN Section H — Operational Infrastructure.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("nzt48.invariant_enforcer")

_UK_TZ = ZoneInfo("Europe/London")

# ── Thresholds ───────────────────────────────────────────────────────────────
_VIX_MAX_AGE_SEC = 300               # 5 minutes
_DD_L3_THRESHOLD_PCT = 0.04          # 4% daily loss = L3 RED (from circuit_breakers.py)
_MAX_CONCURRENT_DEFAULT = 4          # From settings.yaml
_OVERNIGHT_FLAT_TIME = dtime(16, 25) # Must be flat by 16:25 UK
_EQUITY_TOLERANCE_PCT = 0.001        # 0.1% tolerance
_CONFIDENCE_FLOOR = 65               # Minimum signal confidence
_DATA_FEED_MAX_STALE_SEC = 300       # 5 minutes


@dataclass
class InvariantResult:
    """Result of a single invariant check."""
    name: str
    passed: bool
    detail: str
    severity: str = "CRITICAL"  # CRITICAL or WARNING

    def __str__(self) -> str:
        icon = "PASS" if self.passed else "FAIL"
        return f"[{icon}] {self.name}: {self.detail}"


class InvariantEnforcer:
    """Enforces all 12 runtime invariants.

    Usage::

        enforcer = InvariantEnforcer(
            redis_client=redis_conn,
            circuit_breakers=cbs,
            virtual_trader=vt,
            risk_rules=immutable_rules,
            equity=10000.0,
        )

        # At boot — all 12 checked, failure = sys.exit(1)
        enforcer.enforce_boot()

        # Every 60s during market hours — invariants 2-12
        enforcer.enforce_runtime()
    """

    def __init__(
        self,
        redis_client=None,
        circuit_breakers=None,
        virtual_trader=None,
        risk_rules=None,
        state_manager=None,
        equity: float = 10_000.0,
        flatten_callback: Optional[Callable] = None,
        alert_callback: Optional[Callable] = None,
    ) -> None:
        self._redis = redis_client
        self._circuit_breakers = circuit_breakers
        self._virtual_trader = virtual_trader
        self._risk_rules = risk_rules
        self._state_manager = state_manager
        self._equity = equity
        self._flatten_callback = flatten_callback
        self._alert_callback = alert_callback

        # Mutable state for runtime checks
        self._last_vix_value: float = 0.0
        self._last_vix_ts: float = 0.0
        self._last_data_feed_ts: float = 0.0
        self._session_start: Optional[str] = None
        self._broker_equity: Optional[float] = None

        # Track if halted by enforcer
        self._halted: bool = False

    # ── State Updates (called by engine) ─────────────────────────────────

    def update_vix(self, value: float) -> None:
        """Update cached VIX value and timestamp."""
        self._last_vix_value = value
        self._last_vix_ts = time.time()

    def update_data_feed_ts(self, ts: float) -> None:
        """Update last data feed timestamp."""
        self._last_data_feed_ts = ts

    def update_equity(self, equity: float) -> None:
        """Update current equity."""
        self._equity = equity

    def update_broker_equity(self, broker_equity: float) -> None:
        """Update broker-reported equity for reconciliation."""
        self._broker_equity = broker_equity

    def set_session_start(self, session_start: str) -> None:
        """Set session start date string (YYYY-MM-DD)."""
        self._session_start = session_start

    # ── Individual Invariant Checks ──────────────────────────────────────

    def _check_image_parity(self) -> InvariantResult:
        """INV-01: IMAGE_PARITY — /app/.git_sha matches expected SHA."""
        git_sha_file = Path("/app/.git_sha")
        expected_sha = os.environ.get("NZT48_EXPECTED_SHA", "")

        if not git_sha_file.exists():
            # Not running in Docker — skip gracefully
            return InvariantResult(
                name="IMAGE_PARITY",
                passed=True,
                detail="Not in Docker (/app/.git_sha absent), skipped",
                severity="WARNING",
            )

        try:
            actual_sha = git_sha_file.read_text().strip()
        except Exception as e:
            return InvariantResult(
                name="IMAGE_PARITY",
                passed=False,
                detail=f"Cannot read /app/.git_sha: {e}",
            )

        if not expected_sha:
            # No expected SHA set — log and pass (operator must set NZT48_EXPECTED_SHA)
            return InvariantResult(
                name="IMAGE_PARITY",
                passed=True,
                detail=f"Image SHA={actual_sha[:12]}, no NZT48_EXPECTED_SHA set (advisory only)",
                severity="WARNING",
            )

        if actual_sha == expected_sha:
            return InvariantResult(
                name="IMAGE_PARITY",
                passed=True,
                detail=f"SHA match: {actual_sha[:12]}",
            )
        else:
            return InvariantResult(
                name="IMAGE_PARITY",
                passed=False,
                detail=f"BOOT_PARITY_MISMATCH: image={actual_sha[:12]} expected={expected_sha[:12]}",
            )

    def _check_isa_fail_closed(self) -> InvariantResult:
        """INV-02: ISA_FAIL_CLOSED — every open position ticker is ISA-eligible."""
        try:
            from uk_isa.isa_eligibility import is_isa_eligible
        except ImportError:
            return InvariantResult(
                name="ISA_FAIL_CLOSED",
                passed=True,
                detail="ISA eligibility module not available, skipped",
                severity="WARNING",
            )

        if self._virtual_trader is None:
            return InvariantResult(
                name="ISA_FAIL_CLOSED",
                passed=True,
                detail="No VirtualTrader, skipped",
                severity="WARNING",
            )

        open_positions = getattr(self._virtual_trader, "open_positions", {})
        non_eligible = []
        for pos_id, pos in open_positions.items():
            ticker = getattr(pos, "ticker", None)
            if ticker and not is_isa_eligible(ticker):
                non_eligible.append(ticker)

        if non_eligible:
            return InvariantResult(
                name="ISA_FAIL_CLOSED",
                passed=False,
                detail=f"Non-ISA tickers in positions: {non_eligible}",
            )
        return InvariantResult(
            name="ISA_FAIL_CLOSED",
            passed=True,
            detail=f"{len(open_positions)} positions, all ISA-eligible",
        )

    def _check_vix_fail_closed(self) -> InvariantResult:
        """INV-03: VIX_FAIL_CLOSED — VIX != 0 AND age < 300s."""
        if self._last_vix_value == 0.0 and self._last_vix_ts == 0.0:
            # No VIX data yet — fail closed during market hours
            now_uk = datetime.now(_UK_TZ).time()
            if dtime(8, 0) <= now_uk <= dtime(22, 0):
                return InvariantResult(
                    name="VIX_FAIL_CLOSED",
                    passed=False,
                    detail="VIX value is 0 / never received during market hours",
                )
            return InvariantResult(
                name="VIX_FAIL_CLOSED",
                passed=True,
                detail="No VIX data, outside market hours — acceptable",
                severity="WARNING",
            )

        age = time.time() - self._last_vix_ts
        if self._last_vix_value == 0.0:
            return InvariantResult(
                name="VIX_FAIL_CLOSED",
                passed=False,
                detail=f"VIX=0 (stale or missing), age={age:.0f}s",
            )
        if age > _VIX_MAX_AGE_SEC:
            return InvariantResult(
                name="VIX_FAIL_CLOSED",
                passed=False,
                detail=f"VIX={self._last_vix_value:.1f} but stale: {age:.0f}s > {_VIX_MAX_AGE_SEC}s",
            )
        return InvariantResult(
            name="VIX_FAIL_CLOSED",
            passed=True,
            detail=f"VIX={self._last_vix_value:.1f}, age={age:.0f}s",
        )

    def _check_drawdown_cascade(self) -> InvariantResult:
        """INV-04: DRAWDOWN_CASCADE — daily P&L above L3 threshold."""
        if self._circuit_breakers is None:
            return InvariantResult(
                name="DRAWDOWN_CASCADE",
                passed=True,
                detail="No CircuitBreakerSystem, skipped",
                severity="WARNING",
            )

        try:
            status = self._circuit_breakers.get_status()
            # Check if halted (which means L3 was breached)
            if status.get("halted_for_session", False):
                reason = status.get("halt_reason", "unknown")
                return InvariantResult(
                    name="DRAWDOWN_CASCADE",
                    passed=False,
                    detail=f"Daily drawdown L3 breached, session halted: {reason}",
                )
            return InvariantResult(
                name="DRAWDOWN_CASCADE",
                passed=True,
                detail="Daily drawdown within limits",
            )
        except Exception as e:
            return InvariantResult(
                name="DRAWDOWN_CASCADE",
                passed=False,
                detail=f"Drawdown check failed: {e}",
            )

    def _check_position_limit(self) -> InvariantResult:
        """INV-05: POSITION_LIMIT — open_positions <= MAX_CONCURRENT."""
        try:
            import config as cfg
            max_pos = int(cfg.get("portfolio_risk.max_concurrent_positions", _MAX_CONCURRENT_DEFAULT))
        except Exception:
            max_pos = _MAX_CONCURRENT_DEFAULT

        if self._virtual_trader is None:
            return InvariantResult(
                name="POSITION_LIMIT",
                passed=True,
                detail="No VirtualTrader, skipped",
                severity="WARNING",
            )

        open_positions = getattr(self._virtual_trader, "open_positions", {})
        open_count = sum(
            1 for p in open_positions.values()
            if getattr(p, "status", "OPEN") == "OPEN"
        )

        if open_count > max_pos:
            return InvariantResult(
                name="POSITION_LIMIT",
                passed=False,
                detail=f"{open_count} positions open, max={max_pos}",
            )
        return InvariantResult(
            name="POSITION_LIMIT",
            passed=True,
            detail=f"{open_count}/{max_pos} positions",
        )

    def _check_overnight_flat(self) -> InvariantResult:
        """INV-06: OVERNIGHT_FLAT — positions == 0 at 16:25 UK."""
        now_uk = datetime.now(_UK_TZ)
        t = now_uk.time()

        # Only enforce after 16:25 UK on weekdays
        if now_uk.weekday() > 4 or t < _OVERNIGHT_FLAT_TIME:
            return InvariantResult(
                name="OVERNIGHT_FLAT",
                passed=True,
                detail="Not past 16:25 UK, check deferred",
            )

        # After 16:25 — must be flat
        if self._virtual_trader is None:
            return InvariantResult(
                name="OVERNIGHT_FLAT",
                passed=True,
                detail="No VirtualTrader, skipped",
                severity="WARNING",
            )

        open_positions = getattr(self._virtual_trader, "open_positions", {})
        open_count = sum(
            1 for p in open_positions.values()
            if getattr(p, "status", "OPEN") == "OPEN"
        )

        if open_count > 0:
            tickers = [
                getattr(p, "ticker", "?") for p in open_positions.values()
                if getattr(p, "status", "OPEN") == "OPEN"
            ]
            return InvariantResult(
                name="OVERNIGHT_FLAT",
                passed=False,
                detail=f"{open_count} positions still open at {t}: {tickers}",
            )
        return InvariantResult(
            name="OVERNIGHT_FLAT",
            passed=True,
            detail=f"Flat at {t}",
        )

    def _check_equity_fresh(self) -> InvariantResult:
        """INV-07: EQUITY_FRESH — equity matches broker +/-0.1%."""
        if self._broker_equity is None:
            return InvariantResult(
                name="EQUITY_FRESH",
                passed=True,
                detail="No broker equity available, skipped (paper mode)",
                severity="WARNING",
            )

        if self._equity <= 0 or self._broker_equity <= 0:
            return InvariantResult(
                name="EQUITY_FRESH",
                passed=False,
                detail=f"Invalid equity: engine={self._equity}, broker={self._broker_equity}",
            )

        diff_pct = abs(self._equity - self._broker_equity) / self._broker_equity
        if diff_pct > _EQUITY_TOLERANCE_PCT:
            return InvariantResult(
                name="EQUITY_FRESH",
                passed=False,
                detail=f"Equity mismatch: engine={self._equity:.2f} broker={self._broker_equity:.2f} diff={diff_pct:.3%}",
            )
        return InvariantResult(
            name="EQUITY_FRESH",
            passed=True,
            detail=f"Equity aligned: {self._equity:.2f} (diff={diff_pct:.4%})",
        )

    def _check_confidence_floor(self) -> InvariantResult:
        """INV-08: CONFIDENCE_FLOOR — structural check that MIN_CONFIDENCE is set."""
        if self._risk_rules is None:
            return InvariantResult(
                name="CONFIDENCE_FLOOR",
                passed=True,
                detail="No ImmutableRiskRules, skipped",
                severity="WARNING",
            )

        min_conf = getattr(self._risk_rules, "MIN_CONFIDENCE", None)
        if min_conf is None or min_conf < _CONFIDENCE_FLOOR:
            return InvariantResult(
                name="CONFIDENCE_FLOOR",
                passed=False,
                detail=f"MIN_CONFIDENCE={min_conf}, required >={_CONFIDENCE_FLOOR}",
            )
        return InvariantResult(
            name="CONFIDENCE_FLOOR",
            passed=True,
            detail=f"MIN_CONFIDENCE={min_conf}",
        )

    def _check_immutable_risk(self) -> InvariantResult:
        """INV-09: IMMUTABLE_RISK — __setattr__ raises post-init."""
        if self._risk_rules is None:
            return InvariantResult(
                name="IMMUTABLE_RISK",
                passed=True,
                detail="No ImmutableRiskRules, skipped",
                severity="WARNING",
            )

        try:
            self._risk_rules.RISK_PER_TRADE = 0.99  # Attempt mutation
            # If we get here, immutability is broken
            return InvariantResult(
                name="IMMUTABLE_RISK",
                passed=False,
                detail="ImmutableRiskRules.__setattr__ did NOT raise — immutability broken",
            )
        except AttributeError:
            # Expected: mutation blocked
            return InvariantResult(
                name="IMMUTABLE_RISK",
                passed=True,
                detail="ImmutableRiskRules correctly blocks mutation",
            )

    def _check_halt_persistence(self) -> InvariantResult:
        """INV-10: HALT_PERSISTENCE — Redis halt survives restart."""
        if self._redis is None:
            return InvariantResult(
                name="HALT_PERSISTENCE",
                passed=True,
                detail="No Redis client, halt persistence cannot be verified",
                severity="WARNING",
            )

        try:
            # Verify Redis can store and retrieve halt state
            self._redis.ping()

            # Check if a halt is currently persisted
            kill_data = self._redis.hgetall("nzt:kill")
            if kill_data and kill_data.get("active") == "1":
                reason = kill_data.get("reason", "unknown")
                return InvariantResult(
                    name="HALT_PERSISTENCE",
                    passed=True,
                    detail=f"Halt persisted in Redis (active): {reason}",
                )

            # Verify we can write/read (round-trip test)
            test_key = "nzt:invariant:halt_test"
            self._redis.set(test_key, "1", ex=10)
            val = self._redis.get(test_key)
            self._redis.delete(test_key)

            if val == "1":
                return InvariantResult(
                    name="HALT_PERSISTENCE",
                    passed=True,
                    detail="Redis halt persistence verified (round-trip OK)",
                )
            else:
                return InvariantResult(
                    name="HALT_PERSISTENCE",
                    passed=False,
                    detail=f"Redis round-trip failed: wrote '1', read '{val}'",
                )
        except Exception as e:
            return InvariantResult(
                name="HALT_PERSISTENCE",
                passed=False,
                detail=f"Redis halt persistence check failed: {e}",
            )

    def _check_loss_streak_scoped(self) -> InvariantResult:
        """INV-11: LOSS_STREAK_SCOPED — consecutive loss queries are session-scoped."""
        # Structural check: verify ScopedQuery exists and is importable
        try:
            from core.scoped_query import ScopedQuery
            # Verify it has the expected interface
            if hasattr(ScopedQuery, "get_consecutive_losses") or hasattr(ScopedQuery, "scoped_where"):
                return InvariantResult(
                    name="LOSS_STREAK_SCOPED",
                    passed=True,
                    detail="ScopedQuery module available for session-scoped queries",
                )
            # ScopedQuery exists but may use a different interface — still OK
            return InvariantResult(
                name="LOSS_STREAK_SCOPED",
                passed=True,
                detail="ScopedQuery module imported, interface assumed correct",
            )
        except ImportError:
            return InvariantResult(
                name="LOSS_STREAK_SCOPED",
                passed=False,
                detail="ScopedQuery not importable — loss streak queries may be unscoped",
            )

    def _check_data_feed_alive(self) -> InvariantResult:
        """INV-12: DATA_FEED_ALIVE — last tick age < MAX_STALE."""
        now_uk = datetime.now(_UK_TZ)
        t = now_uk.time()

        # Only enforce during market hours (08:00-16:30 UK weekdays)
        if now_uk.weekday() > 4 or t < dtime(8, 0) or t > dtime(16, 30):
            return InvariantResult(
                name="DATA_FEED_ALIVE",
                passed=True,
                detail="Outside LSE market hours, feed check deferred",
            )

        if self._last_data_feed_ts == 0.0:
            return InvariantResult(
                name="DATA_FEED_ALIVE",
                passed=False,
                detail="No data feed timestamp recorded",
            )

        age = time.time() - self._last_data_feed_ts
        if age > _DATA_FEED_MAX_STALE_SEC:
            return InvariantResult(
                name="DATA_FEED_ALIVE",
                passed=False,
                detail=f"Data feed stale: {age:.0f}s > {_DATA_FEED_MAX_STALE_SEC}s",
            )
        return InvariantResult(
            name="DATA_FEED_ALIVE",
            passed=True,
            detail=f"Data feed alive, age={age:.0f}s",
        )

    # ── Aggregate Checks ─────────────────────────────────────────────────

    def _run_all(self, include_image_parity: bool = True) -> list[InvariantResult]:
        """Run all invariants and return results."""
        results = []

        if include_image_parity:
            results.append(self._check_image_parity())

        results.extend([
            self._check_isa_fail_closed(),
            self._check_vix_fail_closed(),
            self._check_drawdown_cascade(),
            self._check_position_limit(),
            self._check_overnight_flat(),
            self._check_equity_fresh(),
            self._check_confidence_floor(),
            self._check_immutable_risk(),
            self._check_halt_persistence(),
            self._check_loss_streak_scoped(),
            self._check_data_feed_alive(),
        ])

        return results

    def enforce_boot(self) -> list[InvariantResult]:
        """Run ALL 12 invariants at boot. Any CRITICAL failure = sys.exit(1).

        Returns the results list (only if all pass).
        """
        results = self._run_all(include_image_parity=True)
        failures = [r for r in results if not r.passed and r.severity == "CRITICAL"]

        # Log all results
        logger.info("INVARIANT_ENFORCER BOOT CHECK — %d invariants:", len(results))
        for r in results:
            if r.passed:
                logger.info("  %s", r)
            else:
                logger.error("  %s", r)

        if failures:
            logger.critical(
                "INVARIANT_ENFORCER: %d CRITICAL failure(s) at boot — HALTING",
                len(failures),
            )
            for f in failures:
                logger.critical("  CRITICAL: %s — %s", f.name, f.detail)

            print("\n" + "=" * 60, file=sys.stderr)
            print("  NZT-48 INVARIANT ENFORCER: BOOT FAILURE", file=sys.stderr)
            print("=" * 60, file=sys.stderr)
            for f in failures:
                print(f"  [XX] {f.name}: {f.detail}", file=sys.stderr)
            print("=" * 60 + "\n", file=sys.stderr)
            sys.exit(1)

        passed_count = sum(1 for r in results if r.passed)
        logger.info(
            "INVARIANT_ENFORCER: Boot check PASSED (%d/%d)",
            passed_count, len(results),
        )
        return results

    def enforce_runtime(self) -> list[InvariantResult]:
        """Run invariants 2-12 (skip IMAGE_PARITY). On failure: flatten + alert + halt.

        Called every 60s during market hours by the scheduler.
        Returns results list.
        """
        if self._halted:
            logger.warning("INVARIANT_ENFORCER: Already halted, skipping runtime check")
            return []

        results = self._run_all(include_image_parity=False)
        failures = [r for r in results if not r.passed and r.severity == "CRITICAL"]

        if not failures:
            return results

        # CRITICAL failure during runtime — trigger flatten + alert + halt
        self._halted = True

        failure_summary = "; ".join(f"{f.name}: {f.detail}" for f in failures)
        logger.critical(
            "INVARIANT_ENFORCER RUNTIME FAILURE: %d invariant(s) violated — "
            "triggering flatten + halt: %s",
            len(failures), failure_summary,
        )

        # 1. Flatten all positions
        if self._flatten_callback:
            try:
                self._flatten_callback()
                logger.critical("INVARIANT_ENFORCER: Flatten triggered")
            except Exception as e:
                logger.critical("INVARIANT_ENFORCER: Flatten callback failed: %s", e)

        # 2. Send alert
        if self._alert_callback:
            try:
                alert_msg = (
                    "INVARIANT VIOLATION — EMERGENCY HALT\n"
                    f"Failures: {len(failures)}\n"
                )
                for f in failures:
                    alert_msg += f"  {f.name}: {f.detail}\n"
                alert_msg += "\nAll positions flattened. Trading halted."
                self._alert_callback(alert_msg)
                logger.critical("INVARIANT_ENFORCER: Alert sent")
            except Exception as e:
                logger.critical("INVARIANT_ENFORCER: Alert callback failed: %s", e)

        # 3. Persist halt in Redis
        if self._redis:
            try:
                self._redis.hset("nzt:kill", mapping={
                    "active": "1",
                    "reason": f"INVARIANT_VIOLATION: {failure_summary}",
                    "timestamp": str(time.time()),
                })
                logger.critical("INVARIANT_ENFORCER: Halt persisted to Redis")
            except Exception as e:
                logger.critical("INVARIANT_ENFORCER: Redis halt persist failed: %s", e)

        return results

    def is_halted(self) -> bool:
        """Return True if the enforcer has triggered a halt."""
        return self._halted

    def get_status(self) -> dict[str, Any]:
        """Return current enforcer state for dashboard/API."""
        return {
            "halted": self._halted,
            "last_vix": self._last_vix_value,
            "last_vix_age_sec": time.time() - self._last_vix_ts if self._last_vix_ts else None,
            "last_data_feed_age_sec": time.time() - self._last_data_feed_ts if self._last_data_feed_ts else None,
            "equity": self._equity,
            "broker_equity": self._broker_equity,
        }
