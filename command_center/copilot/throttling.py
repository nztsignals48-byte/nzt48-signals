"""
command_center/copilot/throttling.py
=====================================
Thread-safe rate limiter for on-demand copilot scans.

Prevents runaway scan requests from overloading the signal engine.
Default cooldown is 60 seconds between scans. Configurable at init.
"""

from __future__ import annotations

import threading
import time


class ScanThrottle:
    """Thread-safe rate limiter for on-demand pipeline scans.

    Usage:
        throttle = ScanThrottle(cooldown_seconds=60)
        allowed, reason = throttle.can_scan()
        if allowed:
            # run the scan
            throttle.record_scan()
        else:
            print(reason)  # "Rate limited — 42s remaining"
    """

    def __init__(self, cooldown_seconds: float = 60.0) -> None:
        """Initialise the throttle.

        Args:
            cooldown_seconds: Minimum interval between scans.
        """
        self._cooldown = cooldown_seconds
        self._last_scan: float = 0.0
        self._lock = threading.Lock()
        self._scan_count: int = 0

    def can_scan(self) -> tuple[bool, str]:
        """Check whether an on-demand scan is allowed right now.

        Returns:
            (allowed, reason) — allowed is True if cooldown has elapsed,
            otherwise False with a human-readable reason string.
        """
        with self._lock:
            if self._last_scan == 0.0:
                return True, "No previous scan — ready"

            elapsed = time.monotonic() - self._last_scan
            remaining = self._cooldown - elapsed

            if remaining <= 0:
                return True, "Cooldown elapsed — ready"
            else:
                return False, (
                    f"Rate limited — {remaining:.0f}s remaining "
                    f"(cooldown={self._cooldown:.0f}s, "
                    f"scans_today={self._scan_count})"
                )

    def record_scan(self) -> None:
        """Record that a scan was just performed. Resets the cooldown timer."""
        with self._lock:
            self._last_scan = time.monotonic()
            self._scan_count += 1

    @property
    def scan_count(self) -> int:
        """Total number of copilot scans recorded this session."""
        with self._lock:
            return self._scan_count

    @property
    def seconds_until_ready(self) -> float:
        """Seconds remaining before next scan is allowed. 0.0 if ready now."""
        with self._lock:
            if self._last_scan == 0.0:
                return 0.0
            elapsed = time.monotonic() - self._last_scan
            remaining = self._cooldown - elapsed
            return max(0.0, remaining)

    def reset(self) -> None:
        """Reset the throttle state. Useful for testing."""
        with self._lock:
            self._last_scan = 0.0
            self._scan_count = 0
