"""
Parallel Universe Scanner
==========================

Q2-4: Parallel ticker scanning with ThreadPoolExecutor for 4x speedup.

Problem: Sequential scanning of 40-50 tickers takes 40-50 seconds
Solution: Parallel scanning with thread pool (4 workers → 10-12 seconds)

Design:
- ThreadPoolExecutor with 4-8 worker threads
- Each ticker scanned independently (no shared state)
- Thread-safe quote cache integration
- Graceful degradation on thread failures

Expected speedup:
- Sequential: 40 tickers × 1s = 40s
- Parallel (4 workers): 40 tickers / 4 = 10s (4x speedup)
- Parallel (8 workers): 40 tickers / 8 = 5s (8x speedup, but diminishing returns)

Optimal worker count = CPU cores × 2 (for I/O-bound tasks)
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Callable
from zoneinfo import ZoneInfo

logger = logging.getLogger("nzt48.core.universe_scanner")

UTC = ZoneInfo("UTC")


@dataclass
class ScanResult:
    """Result from scanning a single ticker."""
    ticker: str
    success: bool
    data: Optional[dict]  # Ticker data if successful
    error: Optional[str]  # Error message if failed
    scan_time_seconds: float
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(UTC)


@dataclass
class UniverseScanSummary:
    """Summary of parallel universe scan."""
    total_tickers: int
    successful_scans: int
    failed_scans: int
    total_time_seconds: float
    avg_time_per_ticker: float
    speedup_factor: float  # vs sequential baseline
    worker_count: int
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(UTC)


class ParallelUniverseScanner:
    """
    Q2-4: Parallel ticker scanner with ThreadPoolExecutor.

    Scans universe of tickers in parallel for 4x speedup.

    Usage:
        scanner = ParallelUniverseScanner(max_workers=4)

        # Define scan function (must be thread-safe)
        def scan_ticker(ticker: str) -> dict:
            # Fetch data, compute indicators, etc.
            return {"price": 100.0, "volume": 1000000}

        # Scan universe
        results = scanner.scan_universe(
            tickers=["AAPL", "GOOGL", "TSLA"],
            scan_function=scan_ticker,
        )

        # Get summary
        summary = scanner.get_scan_summary(results)
    """

    def __init__(self, max_workers: int = 4, timeout_seconds: float = 10.0):
        """
        Initialize parallel scanner.

        Args:
            max_workers: Number of worker threads (default 4)
            timeout_seconds: Timeout per ticker scan (default 10s)
        """
        self.max_workers = max_workers
        self.timeout_seconds = timeout_seconds

        # Stats
        self._scan_history: List[UniverseScanSummary] = []

        logger.info(
            f"Q2-4 PARALLEL_SCANNER: Initialized (workers={max_workers}, timeout={timeout_seconds}s)"
        )

    def scan_universe(
        self,
        tickers: List[str],
        scan_function: Callable[[str], dict],
    ) -> List[ScanResult]:
        """
        Scan universe of tickers in parallel.

        Args:
            tickers: List of tickers to scan
            scan_function: Function to scan each ticker (must be thread-safe)
                          Should accept ticker (str) and return dict or raise exception

        Returns:
            List of ScanResult objects (one per ticker)
        """
        if not tickers:
            logger.warning("Q2-4 PARALLEL_SCANNER: No tickers to scan")
            return []

        start_time = time.time()
        results: List[ScanResult] = []

        logger.info(
            f"Q2-4 PARALLEL_SCANNER: Starting scan of {len(tickers)} tickers "
            f"with {self.max_workers} workers"
        )

        # Create thread pool and submit tasks
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all ticker scans
            future_to_ticker = {
                executor.submit(self._scan_ticker_safe, ticker, scan_function): ticker
                for ticker in tickers
            }

            # Collect results as they complete
            for future in as_completed(future_to_ticker, timeout=self.timeout_seconds * len(tickers)):
                ticker = future_to_ticker[future]
                try:
                    result = future.result(timeout=self.timeout_seconds)
                    results.append(result)

                    if result.success:
                        logger.debug(
                            f"Q2-4 SCAN_SUCCESS: {ticker} ({result.scan_time_seconds:.2f}s)"
                        )
                    else:
                        logger.warning(
                            f"Q2-4 SCAN_FAILED: {ticker} — {result.error}"
                        )

                except Exception as e:
                    # Future raised exception
                    logger.error(f"Q2-4 SCAN_EXCEPTION: {ticker} — {e}")
                    results.append(
                        ScanResult(
                            ticker=ticker,
                            success=False,
                            data=None,
                            error=str(e),
                            scan_time_seconds=0.0,
                        )
                    )

        total_time = time.time() - start_time

        # Calculate summary
        summary = self._create_summary(results, total_time)
        self._scan_history.append(summary)

        logger.info(
            f"Q2-4 PARALLEL_SCANNER: Completed {summary.successful_scans}/{summary.total_tickers} "
            f"in {summary.total_time_seconds:.2f}s "
            f"(avg={summary.avg_time_per_ticker:.2f}s/ticker, "
            f"speedup={summary.speedup_factor:.1f}x vs sequential)"
        )

        return results

    def _scan_ticker_safe(
        self,
        ticker: str,
        scan_function: Callable[[str], dict],
    ) -> ScanResult:
        """
        Thread-safe wrapper for scanning a single ticker.

        Args:
            ticker: Stock ticker
            scan_function: Function to scan ticker

        Returns:
            ScanResult
        """
        start_time = time.time()

        try:
            # Call user-provided scan function
            data = scan_function(ticker)

            scan_time = time.time() - start_time

            return ScanResult(
                ticker=ticker,
                success=True,
                data=data,
                error=None,
                scan_time_seconds=scan_time,
            )

        except Exception as e:
            scan_time = time.time() - start_time

            logger.error(f"Q2-4 SCAN_ERROR: {ticker} — {e}")

            return ScanResult(
                ticker=ticker,
                success=False,
                data=None,
                error=str(e),
                scan_time_seconds=scan_time,
            )

    def _create_summary(
        self,
        results: List[ScanResult],
        total_time: float,
    ) -> UniverseScanSummary:
        """
        Create scan summary from results.

        Args:
            results: List of ScanResult
            total_time: Total scan time in seconds

        Returns:
            UniverseScanSummary
        """
        total_tickers = len(results)
        successful_scans = sum(1 for r in results if r.success)
        failed_scans = total_tickers - successful_scans

        avg_time_per_ticker = total_time / total_tickers if total_tickers > 0 else 0.0

        # Calculate speedup factor (vs sequential baseline of 1s per ticker)
        sequential_baseline = total_tickers * 1.0  # Assume 1s per ticker sequential
        speedup_factor = sequential_baseline / total_time if total_time > 0 else 1.0

        return UniverseScanSummary(
            total_tickers=total_tickers,
            successful_scans=successful_scans,
            failed_scans=failed_scans,
            total_time_seconds=total_time,
            avg_time_per_ticker=avg_time_per_ticker,
            speedup_factor=speedup_factor,
            worker_count=self.max_workers,
        )

    def get_scan_summary(self, results: List[ScanResult]) -> UniverseScanSummary:
        """
        Get summary of scan results.

        Args:
            results: List of ScanResult from scan_universe()

        Returns:
            UniverseScanSummary
        """
        if not self._scan_history:
            return None

        return self._scan_history[-1]

    def get_stats(self) -> dict:
        """
        Get scanner statistics.

        Returns:
            Dict with keys: total_scans, avg_speedup, avg_success_rate, worker_count
        """
        if not self._scan_history:
            return {
                "total_scans": 0,
                "avg_speedup": 0.0,
                "avg_success_rate": 0.0,
                "worker_count": self.max_workers,
            }

        total_scans = len(self._scan_history)
        avg_speedup = sum(s.speedup_factor for s in self._scan_history) / total_scans

        total_tickers = sum(s.total_tickers for s in self._scan_history)
        total_successful = sum(s.successful_scans for s in self._scan_history)
        avg_success_rate = total_successful / total_tickers if total_tickers > 0 else 0.0

        return {
            "total_scans": total_scans,
            "avg_speedup": avg_speedup,
            "avg_success_rate": avg_success_rate,
            "worker_count": self.max_workers,
        }

    def log_stats(self) -> None:
        """Log scanner statistics."""
        stats = self.get_stats()
        logger.info(
            f"Q2-4 SCANNER_STATS: scans={stats['total_scans']} "
            f"avg_speedup={stats['avg_speedup']:.1f}x "
            f"success_rate={stats['avg_success_rate']:.1%} "
            f"workers={stats['worker_count']}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Integration Helper: Convert existing sequential scan to parallel
# ─────────────────────────────────────────────────────────────────────────────

def parallel_scan_wrapper(
    tickers: List[str],
    data_fetcher,  # Object with get_intraday_bars(), get_daily_bars() methods
    indicator_calculator,  # Function to calculate indicators from bars
    max_workers: int = 4,
) -> Dict[str, dict]:
    """
    Q2-4: Helper to convert sequential scan to parallel.

    Wraps existing data fetching and indicator calculation in parallel executor.

    Args:
        tickers: List of tickers to scan
        data_fetcher: Object with get_intraday_bars(ticker) method
        indicator_calculator: Function(ticker, df_intraday, df_daily) -> dict
        max_workers: Number of worker threads

    Returns:
        Dict of ticker → indicator data

    Usage:
        results = parallel_scan_wrapper(
            tickers=["AAPL", "GOOGL"],
            data_fetcher=data_feeds,
            indicator_calculator=lambda t, df_intra, df_daily: {
                "rsi": calculate_rsi(df_intra),
                "rvol": calculate_rvol(df_intra),
            },
            max_workers=4,
        )
    """

    def scan_ticker(ticker: str) -> dict:
        """Scan a single ticker (called in parallel)."""
        try:
            # Fetch data
            df_intraday = data_fetcher.get_intraday_bars(ticker)
            df_daily = data_fetcher.get_daily_bars(ticker)

            if df_intraday is None or df_intraday.empty:
                raise ValueError(f"No intraday data for {ticker}")

            # Calculate indicators
            indicators = indicator_calculator(ticker, df_intraday, df_daily)

            return indicators

        except Exception as e:
            logger.error(f"Error scanning {ticker}: {e}")
            raise

    # Create scanner
    scanner = ParallelUniverseScanner(max_workers=max_workers)

    # Scan universe
    results = scanner.scan_universe(tickers=tickers, scan_function=scan_ticker)

    # Convert to dict: ticker → data
    output = {}
    for result in results:
        if result.success:
            output[result.ticker] = result.data

    logger.info(
        f"Q2-4 PARALLEL_WRAPPER: Scanned {len(output)}/{len(tickers)} tickers successfully"
    )

    return output
