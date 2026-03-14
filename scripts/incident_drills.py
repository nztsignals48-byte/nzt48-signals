#!/usr/bin/env python3
"""
NZT-48 Incident Drill Scripts
Simulate feed outage, latency spike, and Telegram spam to verify graceful degradation.
Run: python scripts/incident_drills.py --drill feed_outage|latency_spike|telegram_spam
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

# Ensure project root is on the path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("nzt48.incident_drills")


# ---------------------------------------------------------------------------
# DrillResult
# ---------------------------------------------------------------------------

@dataclass
class DrillResult:
    """Outcome of a single incident drill."""

    drill_name: str
    passed: bool
    checks: list  # list of (check_name: str, passed: bool, evidence: str)
    duration_seconds: float
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Telegram-specific extras (populated only by telegram drill)
    messages_sent: int = 0
    messages_suppressed: int = 0
    spam_kill_activated: bool = False

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"{status} {self.drill_name} ({self.duration_seconds:.1f}s)"


# ---------------------------------------------------------------------------
# DrillRunner
# ---------------------------------------------------------------------------

class DrillRunner:
    """Executes incident drills against the NZT-48 system components."""

    def __init__(self):
        self._hub_cls = None
        self._engine_cls = None
        self._dedupe_cls = None
        self._limiter_cls = None
        self._load_imports()

    def _load_imports(self):
        """Lazily import project modules; handle missing gracefully."""
        try:
            from data_hub.hub import DataHub
            self._hub_cls = DataHub
        except ImportError:
            logger.warning("Could not import DataHub; feed_outage and latency drills unavailable")

        try:
            from signal_engine.engine import SignalEngine
            self._engine_cls = SignalEngine
        except ImportError:
            logger.warning("Could not import SignalEngine; engine drills unavailable")

        try:
            from delivery.telegram_bot import TelegramDedupe, TelegramRateLimiter
            self._dedupe_cls = TelegramDedupe
            self._limiter_cls = TelegramRateLimiter
        except ImportError:
            logger.warning("Could not import Telegram classes; telegram_spam drill unavailable")

    # ------------------------------------------------------------------
    # Drill 1: Feed Outage
    # ------------------------------------------------------------------

    def run_feed_outage_drill(self, duration_seconds: int = 60) -> DrillResult:
        """Simulate a complete data-feed outage and verify graceful degradation.

        Steps:
            1. Patch DataHub.get_bars to return empty DataFrames for all tickers.
            2. Run SignalEngine and verify it produces a drought / no signals.
            3. Remove the patch and run the engine again.
            4. Verify recovery (engine attempts to produce signals normally).

        Args:
            duration_seconds: How long the mock outage lasts (used for logging;
                              the drill itself runs synchronously).

        Returns:
            DrillResult with pass/fail and per-check evidence.
        """
        checks = []
        t0 = time.monotonic()

        if self._hub_cls is None or self._engine_cls is None:
            return DrillResult(
                drill_name="feed_outage",
                passed=False,
                checks=[("imports_available", False, "DataHub or SignalEngine not importable")],
                duration_seconds=time.monotonic() - t0,
            )

        import pandas as pd
        from data_hub.hub import BarResult

        try:
            from data_hub.models import DataReliabilityScore
        except ImportError:
            DataReliabilityScore = MagicMock

        # --- Phase 1: outage (mocked) ---
        def _empty_bars(self_hub, ticker, period="5d", interval="1h"):
            """Mock get_bars that always returns an empty DataFrame."""
            return BarResult(
                ticker=ticker,
                df=pd.DataFrame(),
                source="mock_outage",
                reliability=DataReliabilityScore() if callable(DataReliabilityScore) else MagicMock(),
                pence_adjusted=False,
            )

        with patch.object(self._hub_cls, "get_bars", _empty_bars):
            engine = self._engine_cls(universe=["QQQ3.L", "3LUS.L"])
            try:
                result = engine.run(session="DRILL_OUTAGE", write_artifacts=False)
            except Exception as exc:
                result = None
                checks.append(("engine_runs_under_outage", False, f"Exception: {exc}"))

        if result is not None:
            has_drought = result.drought is not None
            no_signals = len(result.plays) == 0

            checks.append((
                "drought_detected",
                has_drought,
                f"drought={result.drought is not None}, plays={len(result.plays)}",
            ))
            checks.append((
                "no_signals_during_outage",
                no_signals,
                f"plays_count={len(result.plays)}",
            ))
        else:
            # Engine raised -- still check that it didn't silently emit signals
            checks.append(("drought_detected", True, "Engine raised exception (acceptable degradation)"))
            checks.append(("no_signals_during_outage", True, "No signals (engine raised)"))

        # --- Phase 2: recovery (real DataHub, but still safe) ---
        try:
            engine2 = self._engine_cls(universe=["QQQ3.L"])
            result2 = engine2.run(session="DRILL_RECOVERY", write_artifacts=False)
            # Recovery means the engine ran without error -- signals may or may not exist
            # depending on live market data, so we only check it didn't crash.
            engine_recovered = True
            evidence = f"plays={len(result2.plays)}, drought={result2.drought is not None}"
        except Exception as exc:
            engine_recovered = False
            evidence = f"Recovery failed: {exc}"

        checks.append(("engine_recovers_after_outage", engine_recovered, evidence))

        elapsed = time.monotonic() - t0
        all_passed = all(c[1] for c in checks)

        return DrillResult(
            drill_name="feed_outage",
            passed=all_passed,
            checks=checks,
            duration_seconds=elapsed,
        )

    # ------------------------------------------------------------------
    # Drill 2: Latency Spike
    # ------------------------------------------------------------------

    def run_latency_spike_drill(self, delay_seconds: float = 30) -> DrillResult:
        """Inject artificial latency into DataHub.get_bars and verify the engine
        still completes within a reasonable timeout.

        Steps:
            1. Monkey-patch DataHub.get_bars to sleep for *delay_seconds* before
               delegating to the real implementation.
            2. Run SignalEngine for a small universe.
            3. Verify: scan completes (possibly with timeout), no duplicate signals.
            4. Remove the patch.

        Args:
            delay_seconds: Artificial delay injected per ticker. For drill
                           purposes this is capped at a sensible maximum so the
                           drill itself finishes in reasonable time.

        Returns:
            DrillResult with timing evidence.
        """
        checks = []
        t0 = time.monotonic()

        if self._hub_cls is None or self._engine_cls is None:
            return DrillResult(
                drill_name="latency_spike",
                passed=False,
                checks=[("imports_available", False, "DataHub or SignalEngine not importable")],
                duration_seconds=time.monotonic() - t0,
            )

        # Cap the delay so the drill doesn't block forever.
        effective_delay = min(delay_seconds, 5.0)

        original_get_bars = self._hub_cls.get_bars

        def _slow_get_bars(self_hub, ticker, period="5d", interval="1h"):
            """Wrapper that injects a sleep before the real fetch."""
            time.sleep(effective_delay)
            return original_get_bars(self_hub, ticker, period, interval)

        # Run with latency
        scan_start = time.monotonic()
        with patch.object(self._hub_cls, "get_bars", _slow_get_bars):
            engine = self._engine_cls(universe=["QQQ3.L"])
            try:
                result = engine.run(session="DRILL_LATENCY", write_artifacts=False)
                scan_completed = True
                scan_duration = time.monotonic() - scan_start
                evidence = f"completed in {scan_duration:.1f}s (delay={effective_delay}s/ticker)"
            except Exception as exc:
                scan_completed = False
                scan_duration = time.monotonic() - scan_start
                evidence = f"Exception after {scan_duration:.1f}s: {exc}"
                result = None

        checks.append(("scan_completes_under_latency", scan_completed, evidence))

        # Check extended duration is visible
        checks.append((
            "scan_duration_extended",
            scan_duration >= effective_delay,
            f"scan_duration={scan_duration:.1f}s >= injected_delay={effective_delay}s",
        ))

        # Check no duplicate signals
        if result is not None and result.plays:
            tickers_seen = [p.ticker for p in result.plays]
            # Duplicates = same ticker appearing more than once with identical direction
            seen_keys = set()
            duplicates = 0
            for p in result.plays:
                key = (p.ticker, getattr(p, "direction", "UNKNOWN"))
                if key in seen_keys:
                    duplicates += 1
                seen_keys.add(key)
            checks.append((
                "no_duplicate_signals",
                duplicates == 0,
                f"duplicates={duplicates}, total_plays={len(result.plays)}",
            ))
        else:
            checks.append(("no_duplicate_signals", True, "No plays to check (acceptable)"))

        elapsed = time.monotonic() - t0
        all_passed = all(c[1] for c in checks)

        return DrillResult(
            drill_name="latency_spike",
            passed=all_passed,
            checks=checks,
            duration_seconds=elapsed,
        )

    # ------------------------------------------------------------------
    # Drill 3: Telegram Spam
    # ------------------------------------------------------------------

    def run_telegram_spam_drill(self, message_count: int = 50) -> DrillResult:
        """Send a burst of messages through TelegramRateLimiter and TelegramDedupe
        to verify protective thresholds engage correctly.

        Verifies:
            - RateLimiter blocks after MAX_PER_MINUTE (5).
            - SPAM_KILL activates after threshold (10 in a minute).
            - Dedupe suppresses identical messages.

        Args:
            message_count: Total messages to attempt.

        Returns:
            DrillResult with messages_sent, messages_suppressed, spam_kill_activated.
        """
        checks = []
        t0 = time.monotonic()

        if self._limiter_cls is None or self._dedupe_cls is None:
            return DrillResult(
                drill_name="telegram_spam",
                passed=False,
                checks=[("imports_available", False, "TelegramDedupe or TelegramRateLimiter not importable")],
                duration_seconds=time.monotonic() - t0,
            )

        limiter = self._limiter_cls()
        dedupe = self._dedupe_cls(window_seconds=300)

        sent_count = 0
        suppressed_by_rate = 0
        suppressed_by_dedupe = 0
        spam_kill_fired = False
        rate_limited_at = None

        for i in range(message_count):
            content = f"DRILL signal #{i} QQQ3.L LONG conf=85"
            content_hash = hashlib.md5(content.encode()).hexdigest()

            # Check dedupe first
            if not dedupe.should_send(content_hash):
                suppressed_by_dedupe += 1
                continue

            # Check rate limiter
            allowed, reason = limiter.can_send()
            if not allowed:
                if reason and "SPAM_KILL" in reason:
                    spam_kill_fired = True
                if rate_limited_at is None:
                    rate_limited_at = i
                suppressed_by_rate += 1
                continue

            # Simulate successful send
            limiter.record_send()
            sent_count += 1

        total_suppressed = suppressed_by_rate + suppressed_by_dedupe

        # Check 1: rate limiter fires after MAX_PER_MINUTE
        max_per_min = self._limiter_cls.MAX_PER_MINUTE
        checks.append((
            "rate_limiter_triggers",
            rate_limited_at is not None and rate_limited_at <= max_per_min + 1,
            f"rate_limited_at_message={rate_limited_at}, MAX_PER_MINUTE={max_per_min}",
        ))

        # Check 2: SPAM_KILL fires after threshold
        spam_threshold = self._limiter_cls.SPAM_KILL_THRESHOLD
        checks.append((
            "spam_kill_activates",
            spam_kill_fired,
            f"spam_kill_fired={spam_kill_fired}, threshold={spam_threshold}",
        ))

        # Check 3: dedupe suppresses identical messages
        # All 50 messages have unique content ("#0", "#1" etc.) so dedupe
        # won't trigger here. Send duplicate messages to test dedupe separately.
        dedupe2 = self._dedupe_cls(window_seconds=300)
        dup_hash = hashlib.md5(b"DUPLICATE_MESSAGE").hexdigest()
        first_ok = dedupe2.should_send(dup_hash)
        second_ok = dedupe2.should_send(dup_hash)
        dedupe_works = first_ok and not second_ok
        checks.append((
            "dedupe_suppresses_identical",
            dedupe_works,
            f"first_send={first_ok}, second_send={second_ok}",
        ))

        elapsed = time.monotonic() - t0
        all_passed = all(c[1] for c in checks)

        return DrillResult(
            drill_name="telegram_spam",
            passed=all_passed,
            checks=checks,
            duration_seconds=elapsed,
            messages_sent=sent_count,
            messages_suppressed=total_suppressed,
            spam_kill_activated=spam_kill_fired,
        )

    # ------------------------------------------------------------------
    # Run all drills
    # ------------------------------------------------------------------

    def run_all(self) -> list[DrillResult]:
        """Execute all three incident drills sequentially."""
        results = []
        for drill_fn in [
            self.run_feed_outage_drill,
            self.run_latency_spike_drill,
            self.run_telegram_spam_drill,
        ]:
            try:
                results.append(drill_fn())
            except Exception as exc:
                results.append(DrillResult(
                    drill_name=drill_fn.__name__.replace("run_", "").replace("_drill", ""),
                    passed=False,
                    checks=[("unhandled_exception", False, str(exc))],
                    duration_seconds=0.0,
                ))
        return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _print_results(results: list[DrillResult]) -> None:
    """Pretty-print drill results to stdout."""
    print("\n" + "=" * 60)
    print("  NZT-48 INCIDENT DRILL REPORT")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 60 + "\n")

    all_passed = True
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        marker = "[+]" if r.passed else "[X]"
        print(f"{marker} {status} {r.drill_name} ({r.duration_seconds:.1f}s)")
        for check_name, passed, evidence in r.checks:
            icon = "  ok" if passed else "  FAIL"
            print(f"  {icon} {check_name}: {evidence}")

        # Telegram extras
        if r.drill_name == "telegram_spam":
            print(f"  --- messages_sent={r.messages_sent}, "
                  f"suppressed={r.messages_suppressed}, "
                  f"spam_kill={r.spam_kill_activated}")

        if not r.passed:
            all_passed = False
        print()

    print("-" * 60)
    overall = "ALL DRILLS PASSED" if all_passed else "SOME DRILLS FAILED"
    print(f"  {overall}")
    print("-" * 60 + "\n")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="NZT-48 Incident Drills -- test system resilience",
    )
    parser.add_argument(
        "--drill",
        choices=["feed_outage", "latency_spike", "telegram_spam", "all"],
        required=True,
        help="Which drill to run (or 'all' for the full suite)",
    )
    args = parser.parse_args()

    runner = DrillRunner()

    if args.drill == "all":
        results = runner.run_all()
    elif args.drill == "feed_outage":
        results = [runner.run_feed_outage_drill()]
    elif args.drill == "latency_spike":
        results = [runner.run_latency_spike_drill()]
    elif args.drill == "telegram_spam":
        results = [runner.run_telegram_spam_drill()]
    else:
        print(f"Unknown drill: {args.drill}", file=sys.stderr)
        sys.exit(1)

    _print_results(results)

    # Exit code: 0 if all passed, 1 if any failed
    sys.exit(0 if all(r.passed for r in results) else 1)
