"""WP-4: Clean exit handler for Ouroboros Python processes.

Ensures all log handlers are flushed, file handles are closed, and exit
codes communicate intent to the Rust supervisor:

    sys.exit(0)   = success, flush complete
    sys.exit(255) = unrecoverable error, Rust should NOT retry
    sys.exit(1)   = recoverable error, Rust CAN retry

Usage:
    from python_brain.ouroboros.exit_handler import clean_exit, register_handler
    register_handler()  # Call at module/entry-point start
    # ... do work ...
    clean_exit(0)  # Success
    clean_exit(1, "Transient network error")  # Rust can retry
    clean_exit(255, "Schema version mismatch — manual fix required")  # Fatal

Signal handling:
    SIGTERM/SIGINT are caught and translated to clean_exit(0), giving
    atexit handlers a chance to flush. This is critical in Docker where
    `docker stop` sends SIGTERM with a 10s grace period.

Thread safety:
    _flush_all() is safe to call from any thread. The atexit handler
    runs in the main thread during interpreter shutdown.
"""

from __future__ import annotations

import atexit
import logging
import signal
import sys
import os
import threading

# Guard against double-flush during interpreter shutdown
_flush_lock = threading.Lock()
_flushed = False
_registered = False

log = logging.getLogger("exit_handler")


def register_handler() -> None:
    """Register atexit + signal handlers for clean shutdown.

    Safe to call multiple times (idempotent). Only the first call
    has effect; subsequent calls are no-ops.
    """
    global _registered
    if _registered:
        return
    _registered = True

    atexit.register(_flush_all)

    # Only register signal handlers in the main thread (Python restriction)
    if threading.current_thread() is threading.main_thread():
        try:
            signal.signal(signal.SIGTERM, _sigterm_handler)
            signal.signal(signal.SIGINT, _sigterm_handler)
        except (OSError, ValueError):
            # Can fail in certain embedded environments or when not main thread
            pass

    log.debug("WP-4: Exit handler registered (pid=%d)", os.getpid())


def _flush_all() -> None:
    """Flush all log handlers, stdout, and stderr.

    Guarded by a lock to prevent double-flush during concurrent
    shutdown paths (atexit + signal handler racing).
    """
    global _flushed
    with _flush_lock:
        if _flushed:
            return
        _flushed = True

    # Flush all logging handlers
    try:
        for handler in logging.root.handlers[:]:
            try:
                handler.flush()
            except Exception:
                pass
        logging.shutdown()
    except Exception:
        pass

    # Flush standard streams
    try:
        sys.stdout.flush()
    except Exception:
        pass
    try:
        sys.stderr.flush()
    except Exception:
        pass


def _sigterm_handler(signum: int, frame) -> None:
    """Handle SIGTERM/SIGINT gracefully.

    Logs the signal, flushes everything, and exits with code 0
    (clean shutdown requested by supervisor).
    """
    sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else str(signum)
    try:
        log.info("WP-4: Received %s (signal %d), flushing and exiting cleanly",
                 sig_name, signum)
    except Exception:
        pass
    _flush_all()
    sys.exit(0)


def clean_exit(code: int = 0, message: str = "") -> None:
    """Exit with clean flush and structured exit code.

    Args:
        code: Exit code. 0=success, 1=recoverable (retry), 255=fatal (no retry).
        message: Optional human-readable reason for the exit.

    Exit code semantics (consumed by Rust engine supervisor):
        0   — Step completed successfully. All outputs written.
        1   — Recoverable error (network timeout, transient lock conflict).
              Rust supervisor may retry after backoff.
        255 — Unrecoverable error (schema mismatch, missing config, data corruption).
              Rust supervisor should NOT retry; requires human intervention.
    """
    if message:
        try:
            log_fn = log.error if code != 0 else log.info
            log_fn("WP-4: Exit(%d): %s", code, message)
        except Exception:
            pass
    _flush_all()
    sys.exit(code)
