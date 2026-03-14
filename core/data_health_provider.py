"""
NZT-48 Trading System -- DataHealthProvider
=============================================
Single source of truth for data quality status across the ticker universe.

Delegates the actual OHLCV checks to uk_isa/data_health.py (DataHealthGate),
then wraps results into the canonical DataHealthReport schema consumed by
all delivery surfaces.

Results are cached for the current tick cycle and invalidated on the next
check_all() call. Thread-safe.

Usage:
    from core.data_health_provider import DataHealthProvider
    provider = DataHealthProvider()
    report = provider.check_all(
        tickers=["QQQ3.L", "TSM3.L"],
        bars_dict={"QQQ3.L": qqq3_df, "TSM3.L": tsm3_df},
    )
    ticker_health = provider.get_ticker_health("QQQ3.L")
    summary = provider.get_summary()
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from core.schemas import DataHealthReport

logger = logging.getLogger("nzt48.core.data_health_provider")

# Lazy import to avoid circular dependency at module load
_provenance_registry = None


def _get_provenance_registry():
    """Lazy-load the provenance registry singleton."""
    global _provenance_registry
    if _provenance_registry is None:
        try:
            from core.provenance import get_registry
            _provenance_registry = get_registry()
        except Exception as e:
            logger.debug("Provenance registry not available: %s", e)
    return _provenance_registry

# Attempt to import the existing DataHealthGate
try:
    from uk_isa.data_health import DataHealthGate, get_gate as _get_gate
    _HAS_GATE = True
except ImportError:
    _HAS_GATE = False
    logger.warning(
        "uk_isa.data_health not available -- DataHealthProvider "
        "will operate in fallback mode (basic NaN/shape checks only)"
    )


class DataHealthProvider:
    """Single data health source for the entire system.

    Wraps the DataHealthGate from uk_isa/ and produces canonical
    DataHealthReport objects consumed by PDFs, Telegram, dashboard.

    Thread-safe: all state access is guarded by a threading.Lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._gate: Optional[object] = None
        self._cached_report: Optional[DataHealthReport] = None
        self._cached_per_ticker: dict[str, dict] = {}
        self._last_check_ts: float = 0.0
        self._tick_id: int = 0

        if _HAS_GATE:
            self._gate = _get_gate()
            logger.info("DataHealthProvider initialised with DataHealthGate")
        else:
            logger.warning("DataHealthProvider initialised WITHOUT DataHealthGate (fallback mode)")

    # ------------------------------------------------------------------
    # Primary API
    # ------------------------------------------------------------------

    def check_all(
        self,
        tickers: list[str],
        bars_dict: Optional[dict] = None,
    ) -> DataHealthReport:
        """Run data health checks on all tickers in the universe.

        If bars_dict is provided (ticker -> DataFrame), validates the
        provided data directly. Otherwise delegates to DataHealthGate
        which fetches fresh data via yfinance.

        Args:
            tickers:    List of ticker symbols to validate.
            bars_dict:  Optional dict mapping ticker -> pandas DataFrame
                        of OHLCV bars. If None, the gate fetches its own.

        Returns:
            DataHealthReport with per-ticker status and aggregate summary.
        """
        with self._lock:
            self._tick_id += 1
            now = time.time()

            per_ticker: dict[str, dict] = {}
            pass_count = 0
            fail_count = 0

            if bars_dict is not None and self._gate is not None and _HAS_GATE:
                # Validate provided DataFrames via the gate
                per_ticker, pass_count, fail_count = self._validate_with_gate(
                    tickers, bars_dict
                )
            elif self._gate is not None and _HAS_GATE:
                # Let the gate fetch and validate
                per_ticker, pass_count, fail_count = self._validate_batch_via_gate(tickers)
            else:
                # Fallback: basic shape/NaN checks
                per_ticker, pass_count, fail_count = self._validate_fallback(
                    tickers, bars_dict or {}
                )

            tickers_checked = len(tickers)
            tickers_passed = pass_count
            tickers_failed = fail_count

            # Determine overall status
            if tickers_checked == 0:
                status = "UNKNOWN"
            elif tickers_failed > tickers_checked * 0.3:
                status = "FAIL"
            elif tickers_failed > 0:
                status = "WARN"
            else:
                status = "PASS"

            staleness = now - self._last_check_ts if self._last_check_ts > 0 else 0.0
            self._last_check_ts = now

            report = DataHealthReport(
                status=status,
                tickers_checked=tickers_checked,
                tickers_passed=tickers_passed,
                tickers_failed=tickers_failed,
                per_ticker=per_ticker,
                provider="yfinance" if _HAS_GATE else "fallback",
                data_as_of=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                staleness_seconds=round(staleness, 1),
            )

            self._cached_report = report
            self._cached_per_ticker = per_ticker

            # W3: Register provenance for data health results
            registry = _get_provenance_registry()
            if registry is not None:
                registry.register(
                    "data_health",
                    value=report,
                    provider=report.provider,
                    as_of_epoch=now,
                )
                # Register per-ticker provenance
                for ticker, health in per_ticker.items():
                    registry.register(
                        f"data_health.{ticker}",
                        value=health,
                        provider=report.provider,
                        as_of_epoch=now,
                    )

            logger.debug(
                "DataHealth check complete (tick #%d): %s -- %d/%d passed",
                self._tick_id, status, tickers_passed, tickers_checked,
            )
            return report

    def get_ticker_health(self, ticker: str) -> dict:
        """Return cached health status for a single ticker.

        Args:
            ticker: The ticker symbol to query.

        Returns:
            dict with keys "status" and "reasons", or a default
            "UNKNOWN" entry if the ticker has not been checked.
        """
        with self._lock:
            if ticker in self._cached_per_ticker:
                return self._cached_per_ticker[ticker]
            return {"status": "UNKNOWN", "reasons": ["Ticker not yet checked"]}

    def get_summary(self) -> DataHealthReport:
        """Return the most recent aggregated DataHealthReport.

        If check_all() has never been called, returns a default UNKNOWN report.
        """
        with self._lock:
            if self._cached_report is not None:
                return self._cached_report
            return DataHealthReport(
                status="UNKNOWN",
                tickers_checked=0,
                tickers_passed=0,
                tickers_failed=0,
                per_ticker={},
                provider="none",
                data_as_of="",
                staleness_seconds=0.0,
            )

    def clear_cache(self) -> None:
        """Clear the internal result cache (forces re-validation on next check)."""
        with self._lock:
            self._cached_report = None
            self._cached_per_ticker = {}
            if self._gate is not None and hasattr(self._gate, "clear_cache"):
                self._gate.clear_cache()

    # ------------------------------------------------------------------
    # Internal: validation strategies
    # ------------------------------------------------------------------

    def _validate_with_gate(
        self,
        tickers: list[str],
        bars_dict: dict,
    ) -> tuple[dict, int, int]:
        """Validate provided DataFrames using the DataHealthGate.

        Returns:
            (per_ticker_dict, pass_count, fail_count)
        """
        per_ticker: dict[str, dict] = {}
        pass_count = 0
        fail_count = 0

        for ticker in tickers:
            df = bars_dict.get(ticker)
            try:
                result = self._gate.validate(ticker, df)
                reasons = list(result.exceptions) + list(result.warnings)
                status = result.status  # "PASS", "WARN", "FAIL"
                per_ticker[ticker] = {
                    "status": status,
                    "reasons": reasons,
                    "rows_checked": result.rows_checked,
                    "close_last": round(result.close_last, 4),
                    "rvol": round(result.rvol, 2),
                }
                if status == "FAIL":
                    fail_count += 1
                else:
                    pass_count += 1
            except Exception as e:
                logger.warning("DataHealthGate.validate() failed for %s: %s", ticker, e)
                per_ticker[ticker] = {
                    "status": "FAIL",
                    "reasons": [f"Validation exception: {e}"],
                }
                fail_count += 1

        return per_ticker, pass_count, fail_count

    def _validate_batch_via_gate(
        self,
        tickers: list[str],
    ) -> tuple[dict, int, int]:
        """Let the DataHealthGate fetch and validate all tickers.

        Returns:
            (per_ticker_dict, pass_count, fail_count)
        """
        per_ticker: dict[str, dict] = {}
        pass_count = 0
        fail_count = 0

        try:
            summary = self._gate.batch_validate(tickers, use_cache=False)
            for ticker, result in summary.results.items():
                reasons = list(result.exceptions) + list(result.warnings)
                status = result.status
                per_ticker[ticker] = {
                    "status": status,
                    "reasons": reasons,
                    "rows_checked": result.rows_checked,
                    "close_last": round(result.close_last, 4),
                    "rvol": round(result.rvol, 2),
                }
                if status == "FAIL":
                    fail_count += 1
                else:
                    pass_count += 1
        except Exception as e:
            logger.error("DataHealthGate.batch_validate() failed: %s", e)
            for ticker in tickers:
                per_ticker[ticker] = {
                    "status": "FAIL",
                    "reasons": [f"Batch validation exception: {e}"],
                }
                fail_count += 1

        return per_ticker, pass_count, fail_count

    def _validate_fallback(
        self,
        tickers: list[str],
        bars_dict: dict,
    ) -> tuple[dict, int, int]:
        """Fallback validation when DataHealthGate is not available.

        Performs basic checks: DataFrame exists, has rows, has OHLC columns,
        no all-NaN columns.

        Returns:
            (per_ticker_dict, pass_count, fail_count)
        """
        per_ticker: dict[str, dict] = {}
        pass_count = 0
        fail_count = 0

        for ticker in tickers:
            reasons = []
            df = bars_dict.get(ticker)

            if df is None:
                reasons.append("No DataFrame provided")
                per_ticker[ticker] = {"status": "FAIL", "reasons": reasons}
                fail_count += 1
                continue

            try:
                import pandas as pd
                if not isinstance(df, pd.DataFrame):
                    reasons.append(f"Expected DataFrame, got {type(df).__name__}")
                    per_ticker[ticker] = {"status": "FAIL", "reasons": reasons}
                    fail_count += 1
                    continue

                if df.empty:
                    reasons.append("DataFrame is empty")
                    per_ticker[ticker] = {"status": "FAIL", "reasons": reasons}
                    fail_count += 1
                    continue

                if len(df) < 2:
                    reasons.append(f"Insufficient rows: {len(df)} (need >= 2)")
                    per_ticker[ticker] = {"status": "FAIL", "reasons": reasons}
                    fail_count += 1
                    continue

                # Check for required columns (case-insensitive)
                cols_lower = [c.lower() if isinstance(c, str) else str(c).lower()
                              for c in df.columns]
                required = ["open", "high", "low", "close"]
                missing = [c for c in required if c not in cols_lower]
                if missing:
                    reasons.append(f"Missing columns: {missing}")

                # Check for all-NaN columns
                for col in required:
                    if col in cols_lower:
                        idx = cols_lower.index(col)
                        real_col = df.columns[idx]
                        if df[real_col].isna().all():
                            reasons.append(f"Column '{col}' is all NaN")

                if reasons:
                    per_ticker[ticker] = {"status": "WARN", "reasons": reasons}
                    pass_count += 1  # WARN still counts as usable
                else:
                    per_ticker[ticker] = {"status": "PASS", "reasons": []}
                    pass_count += 1

            except Exception as e:
                reasons.append(f"Fallback validation error: {e}")
                per_ticker[ticker] = {"status": "FAIL", "reasons": reasons}
                fail_count += 1

        return per_ticker, pass_count, fail_count
