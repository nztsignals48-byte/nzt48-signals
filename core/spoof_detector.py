"""
AEGIS K-14: Spoof Detection Radar.

Monitors order book for spoofing patterns — large orders that appear
and disappear within milliseconds to manipulate other participants.

Detection heuristic:
    If an order > 5x the average book size appears and disappears
    within < 500ms, tag as SPOOFED and halt execution for 3 seconds
    to avoid adverse selection.

Reference:
    SEC Rule 10b-5: Market manipulation prohibition.
    Dodd-Frank Act Section 747: Anti-spoofing provision.
    Tao, Z. et al. (2022). "Detecting Spoofing in High-Frequency
    Trading." Journal of Financial Markets.

SKELETON IMPLEMENTATION — Phase K.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("nzt48.spoof_detector")

# --- Configuration ---
SPOOF_SIZE_MULTIPLIER = 5.0     # Order must be > 5x avg book size
SPOOF_LIFETIME_MS = 500         # Must appear and disappear within 500ms
EXECUTION_HALT_SECONDS = 3.0    # Halt execution for 3s after spoof detection
AVG_BOOK_WINDOW = 100           # Rolling window for average book size calculation


@dataclass
class OrderBookSnapshot:
    """Single snapshot of the order book at a point in time."""
    timestamp_ms: float              # epoch milliseconds
    bid_price: float = 0.0
    ask_price: float = 0.0
    bid_size: float = 0.0
    ask_size: float = 0.0
    total_bid_depth: float = 0.0     # sum of all bid levels
    total_ask_depth: float = 0.0     # sum of all ask levels


@dataclass
class SpoofEvent:
    """Record of a detected spoofing event."""
    timestamp: datetime
    ticker: str
    side: str                        # "BID" or "ASK"
    spoofed_size: float              # size of the spoofed order
    avg_book_size: float             # average book size at time of detection
    size_ratio: float                # spoofed_size / avg_book_size
    lifetime_ms: float               # how long the order existed
    action_taken: str = "HALT_3S"    # what action was taken


class SpoofDetector:
    """K-14: Real-time spoof detection engine.

    Tracks order book snapshots and detects spoofing patterns by
    monitoring for large orders that appear and disappear rapidly.

    Usage:
        detector = SpoofDetector()
        detector.on_book_update(ticker, snapshot)
        if detector.is_execution_halted(ticker):
            # Do not send orders — spoof detected
            pass

    TODO (Phase Q2):
        - Integrate with IBKR L2 data feed (reqMktDepth)
        - Add machine learning classifier for spoof vs legitimate large orders
        - Track repeat offender patterns (same size, same time-of-day)
        - Feed spoof events into risk_officer for pattern analysis
        - Add Prometheus metrics for spoof detection rate
    """

    def __init__(self) -> None:
        # Rolling book size history per ticker: deque of (timestamp_ms, total_size)
        self._book_history: dict[str, deque] = {}

        # Previous snapshot per ticker (for detecting appearance/disappearance)
        self._prev_snapshot: dict[str, OrderBookSnapshot] = {}

        # Execution halt state: ticker -> halt_until (epoch seconds)
        self._halt_until: dict[str, float] = {}

        # Spoof event log (last 1000 events)
        self._spoof_events: deque[SpoofEvent] = deque(maxlen=1000)

        # Large order tracking: ticker -> list of (appeared_at_ms, size, side)
        self._large_orders: dict[str, list[tuple[float, float, str]]] = {}

    def on_book_update(self, ticker: str, snapshot: OrderBookSnapshot) -> Optional[SpoofEvent]:
        """Process an order book update and check for spoofing.

        Called on every L2 data update. Compares current snapshot to
        previous to detect large orders appearing/disappearing.

        Args:
            ticker: Instrument symbol.
            snapshot: Current order book snapshot.

        Returns:
            SpoofEvent if spoofing detected, None otherwise.
        """
        # --- Maintain rolling average book size ---
        if ticker not in self._book_history:
            self._book_history[ticker] = deque(maxlen=AVG_BOOK_WINDOW)
            self._large_orders[ticker] = []

        total_size = snapshot.total_bid_depth + snapshot.total_ask_depth
        self._book_history[ticker].append((snapshot.timestamp_ms, total_size))

        avg_book_size = self._get_avg_book_size(ticker)

        prev = self._prev_snapshot.get(ticker)
        spoof_event = None

        if prev is not None and avg_book_size > 0:
            # --- Check BID side: large order appeared then disappeared ---
            spoof_event = self._check_side_spoof(
                ticker=ticker,
                side="BID",
                prev_size=prev.bid_size,
                curr_size=snapshot.bid_size,
                avg_book_size=avg_book_size,
                prev_ts=prev.timestamp_ms,
                curr_ts=snapshot.timestamp_ms,
            )

            # --- Check ASK side ---
            if spoof_event is None:
                spoof_event = self._check_side_spoof(
                    ticker=ticker,
                    side="ASK",
                    prev_size=prev.ask_size,
                    curr_size=snapshot.ask_size,
                    avg_book_size=avg_book_size,
                    prev_ts=prev.timestamp_ms,
                    curr_ts=snapshot.timestamp_ms,
                )

        # --- Track large orders for disappearance detection ---
        self._track_large_orders(ticker, snapshot, avg_book_size)

        # --- Update previous snapshot ---
        self._prev_snapshot[ticker] = snapshot

        if spoof_event:
            self._on_spoof_detected(spoof_event)

        return spoof_event

    def _check_side_spoof(
        self,
        ticker: str,
        side: str,
        prev_size: float,
        curr_size: float,
        avg_book_size: float,
        prev_ts: float,
        curr_ts: float,
    ) -> Optional[SpoofEvent]:
        """Check if a large order appeared and disappeared on one side.

        Pattern: prev_size was large (>5x avg), curr_size dropped significantly,
        and the elapsed time is < 500ms.
        """
        if avg_book_size <= 0:
            return None

        size_ratio = prev_size / avg_book_size
        elapsed_ms = curr_ts - prev_ts

        # Was prev a large order that disappeared?
        if (size_ratio > SPOOF_SIZE_MULTIPLIER
                and curr_size < prev_size * 0.5  # >50% disappeared
                and elapsed_ms < SPOOF_LIFETIME_MS):

            return SpoofEvent(
                timestamp=datetime.now(timezone.utc),
                ticker=ticker,
                side=side,
                spoofed_size=prev_size,
                avg_book_size=avg_book_size,
                size_ratio=round(size_ratio, 1),
                lifetime_ms=round(elapsed_ms, 1),
                action_taken="HALT_3S",
            )

        return None

    def _track_large_orders(
        self,
        ticker: str,
        snapshot: OrderBookSnapshot,
        avg_book_size: float,
    ) -> None:
        """Track appearance of large orders for multi-update disappearance detection.

        TODO (Phase Q2): Implement multi-update tracking where a large order
        persists for 2-3 updates then vanishes — still spoofing but harder
        to detect with single-update comparison.
        """
        pass  # Skeleton — single-update detection in _check_side_spoof is sufficient for K-14

    def _get_avg_book_size(self, ticker: str) -> float:
        """Calculate rolling average total book size for a ticker."""
        history = self._book_history.get(ticker)
        if not history or len(history) < 5:
            return 0.0

        total = sum(size for _, size in history)
        return total / len(history)

    def _on_spoof_detected(self, event: SpoofEvent) -> None:
        """Handle a detected spoof event: log it and halt execution."""
        self._spoof_events.append(event)

        # Set execution halt for 3 seconds
        self._halt_until[event.ticker] = time.monotonic() + EXECUTION_HALT_SECONDS

        logger.critical(
            "K-14 SPOOF_DETECTED: %s %s side | size=%.0f (%.1fx avg) | "
            "lifetime=%.0fms | HALTING EXECUTION %ds",
            event.ticker, event.side, event.spoofed_size,
            event.size_ratio, event.lifetime_ms,
            EXECUTION_HALT_SECONDS,
        )

    def is_execution_halted(self, ticker: str) -> bool:
        """Check if execution is halted for a ticker due to spoof detection.

        Args:
            ticker: Instrument symbol.

        Returns:
            True if execution should be halted (within 3s of spoof detection).
        """
        halt_until = self._halt_until.get(ticker)
        if halt_until is None:
            return False

        if time.monotonic() < halt_until:
            return True

        # Halt expired — clean up
        del self._halt_until[ticker]
        return False

    def get_spoof_events(self, ticker: Optional[str] = None, limit: int = 50) -> list[SpoofEvent]:
        """Return recent spoof events, optionally filtered by ticker.

        Args:
            ticker: Filter to specific ticker (None = all).
            limit: Max events to return.

        Returns:
            List of SpoofEvent, most recent first.
        """
        events = list(self._spoof_events)
        if ticker:
            events = [e for e in events if e.ticker == ticker]
        return events[-limit:][::-1]

    def get_spoof_rate(self, ticker: str, window_minutes: int = 60) -> float:
        """Calculate spoofing rate (events per hour) for a ticker.

        Args:
            ticker: Instrument symbol.
            window_minutes: Lookback window in minutes.

        Returns:
            Spoof events per hour within the window.
        """
        now = datetime.now(timezone.utc)
        count = 0
        for event in self._spoof_events:
            if event.ticker != ticker:
                continue
            elapsed = (now - event.timestamp).total_seconds() / 60
            if elapsed <= window_minutes:
                count += 1

        # Normalize to events per hour
        if window_minutes > 0:
            return count * (60.0 / window_minutes)
        return 0.0
