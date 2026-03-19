"""AEGIS V2 - WAL Watcher for Telegram Notifications + Google Sheets Sync.

Tails the Write-Ahead Log (NDJSON) and sends Telegram alerts for:
- Trade entries (FillEvent)
- Trade exits (PositionClosed)
- Regime changes (RegimeChange)
- System alerts (HaltEvent, CircuitBreaker)

Also pushes relevant events to Redis 'sheets:queue' for Google Sheets
dashboard sync (if sheets_service_account.json is present).

Runs as a background process alongside the engine.

Usage:
    python3 -m python_brain.ouroboros.wal_watcher --wal-dir /app/events
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add project root to path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from python_brain.ouroboros.telegram_notify import (
    send_trade_entry,
    send_trade_exit,
    send_regime_change,
    send_alert,
)
from python_brain.ouroboros.sheets_sync import (
    SheetsSyncClient,
    push_to_sheets_queue,
    REDIS_QUEUE_KEY,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WAL-Watch] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("wal_watcher")

# Ticker ID to symbol mapping (loaded from config)
TICKER_MAP: dict = {}

# Google Sheets sync state (initialised in tail_wal)
_sheets_redis = None  # Redis client for sheets queue
_sheets_client: Optional[SheetsSyncClient] = None
_sheets_enabled: bool = False

# WAL event types that are pushed to the Sheets queue
SHEETS_EVENT_TYPES = frozenset({
    "RoutedOrder",      # Trade entries (filtered to Buy in sheets_sync)
    "PositionClosed",   # Trade exits with P&L
    "RiskStateChange",  # Regime changes
    "StateSnapshot",    # Periodic health + open positions
    "SystemReady",      # Engine boot heartbeat
    "OuroborosChange",  # Nightly config tweaks
})


def load_ticker_map(config_dir: Path) -> dict:
    """Load ticker_id -> symbol mapping from active_watchlist.json."""
    watchlist_path = config_dir / "active_watchlist.json"
    mapping = {}
    if watchlist_path.exists():
        try:
            with open(watchlist_path) as f:
                wl = json.load(f)
            for i, t in enumerate(wl.get("vanguard", [])):
                mapping[i] = t.get("symbol", f"T{i}")
        except Exception as e:
            log.warning("Failed to load ticker map: %s", e)
    return mapping


def resolve_ticker(ticker_id: int) -> str:
    """Resolve ticker_id to symbol name."""
    return TICKER_MAP.get(ticker_id, f"TICKER_{ticker_id}")


def get_current_wal_file(wal_dir: Path) -> Path:
    """Get the active WAL file path.

    The Rust engine always writes to ``current.ndjson`` (rotated on restart).
    We fall back to the legacy date-named file only if ``current.ndjson``
    doesn't exist (shouldn't happen in normal operation).
    """
    current = wal_dir / "current.ndjson"
    if current.exists():
        return current
    # Legacy fallback: date-named WAL
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return wal_dir / f"{today}.ndjson"


def _maybe_push_to_sheets(event: dict) -> None:
    """Push event to Redis sheets queue if it matches a Sheets-relevant type."""
    global _sheets_redis
    if not _sheets_enabled or _sheets_redis is None:
        return
    payload = event.get("payload", {})
    if not payload:
        return
    # Check if any key in the payload matches our event types
    for key in payload:
        if key in SHEETS_EVENT_TYPES:
            push_to_sheets_queue(_sheets_redis, event)
            return


def process_wal_event(event: dict) -> None:
    """Process a single WAL event and send Telegram notification if relevant."""
    payload = event.get("payload", {})
    if not payload:
        return

    # Push to Google Sheets queue (fire-and-forget)
    _maybe_push_to_sheets(event)

    # Determine event type from payload keys
    if "RoutedOrder" in payload:
        data = payload["RoutedOrder"]
        side = data.get("side", "Long")
        # Only notify on entry orders — Sell-side RoutedOrders are exit orders
        # (the real exit notification comes from PositionClosed with actual P&L)
        if side in ("Sell", "SLD"):
            return
        ticker = data.get("symbol") or resolve_ticker(data.get("ticker_id", 0))
        send_trade_entry(
            ticker=ticker,
            direction=side,
            price=0,  # price not in routed order
            confidence=data.get("confidence", 0),
            kelly=data.get("kelly_fraction", 0),
            shares=int(data.get("approved_size", 0)),
            strategy=data.get("strategy", ""),
        )
        log.info("Sent trade entry alert: %s %s", ticker, side)

    elif "FillEvent" in payload:
        data = payload["FillEvent"]
        ticker = resolve_ticker(data.get("ticker_id", 0))
        send_alert(
            f"<b>ORDER FILLED</b>\n"
            f"{ticker} - {data.get('filled_qty', 0)} shares @ {data.get('price', 0):.4f}\n"
            f"Commission: {data.get('commission', 0):.2f}",
            severity="MEDIUM",
        )
        log.info("Sent fill alert: %s", ticker)

    elif "PositionClosed" in payload:
        data = payload["PositionClosed"]
        # Use symbol from event directly (more reliable than ticker_id map)
        ticker = data.get("symbol") or resolve_ticker(data.get("ticker_id", 0))
        pnl = data.get("final_pnl", 0.0)
        send_trade_exit(
            ticker=ticker,
            direction="Long",
            entry_price=0,  # not in WAL event
            exit_price=0,   # not in WAL event
            pnl_pct=0,      # not in WAL event (only absolute GBP)
            pnl_gbp=pnl,
            reason=data.get("strategy", ""),  # use strategy as exit context
            rung_achieved=data.get("highest_rung", 0),
        )
        log.info("Sent trade exit alert: %s (P&L: %.2f)", ticker, pnl)

    elif "RegimeChange" in payload:
        data = payload["RegimeChange"]
        send_regime_change(
            old_regime=data.get("from", "Unknown"),
            new_regime=data.get("to", "Unknown"),
            reason=data.get("reason", ""),
        )
        log.info("Sent regime change: %s -> %s", data.get("from"), data.get("to"))

    elif "HaltEvent" in payload:
        data = payload["HaltEvent"]
        send_alert(
            f"SYSTEM HALT\nReason: {data.get('reason', 'unknown')}",
            severity="CRITICAL",
        )

    elif "Checkpoint" in payload:
        # Silent — don't notify on checkpoints
        pass

    elif "ExitSignal" in payload:
        data = payload["ExitSignal"]
        ticker = resolve_ticker(data.get("ticker_id", 0))
        send_alert(
            f"Exit signal: {ticker}\nReason: {data.get('reason', '')}\n"
            f"Priority: {data.get('priority', '')}",
            severity="LOW",
        )


def _init_sheets_sync(redis_url: str) -> None:
    """Initialise Google Sheets sync (Redis queue + background writer thread).
    Fails silently if service account JSON is missing or Redis is unavailable.
    """
    global _sheets_redis, _sheets_client, _sheets_enabled

    # Try to connect to Redis for the queue
    try:
        from python_brain.ouroboros.sheets_sync import _get_redis_client
        _sheets_redis = _get_redis_client(redis_url)
        if _sheets_redis is None:
            log.info("Sheets sync: Redis unavailable, sheets queue disabled")
            return
    except Exception as e:
        log.info("Sheets sync: Redis init failed (%s), disabled", e)
        return

    # Start the background Sheets writer thread
    try:
        _sheets_client = SheetsSyncClient(redis_url=redis_url)
        started = _sheets_client.start()
        if started:
            _sheets_enabled = True
            log.info("Google Sheets sync enabled (background thread running)")
        else:
            log.info("Google Sheets sync disabled (no service account or init failed)")
            _sheets_redis = None  # no point queuing if writer can't process
    except Exception as e:
        log.info("Google Sheets sync disabled: %s", e)
        _sheets_redis = None


def tail_wal(wal_dir: Path, config_dir: Path) -> None:
    """Continuously tail the WAL file and process new events."""
    global TICKER_MAP
    TICKER_MAP = load_ticker_map(config_dir)
    log.info("Loaded ticker map: %d entries", len(TICKER_MAP))

    # Initialise Google Sheets sync (silent no-op if no service account)
    redis_url = os.environ.get("REDIS_URL", "redis://:nzt48redis@aegis-redis:6379/0")
    _init_sheets_sync(redis_url)

    current_file = get_current_wal_file(wal_dir)
    log.info("Watching WAL: %s", current_file)

    # Start from end of file (don't replay old events)
    file_pos = 0
    if current_file.exists():
        file_pos = current_file.stat().st_size

    events_processed = 0

    while True:
        try:
            # Check if date rolled over
            new_file = get_current_wal_file(wal_dir)
            if new_file != current_file:
                log.info("Date rolled: %s -> %s", current_file.name, new_file.name)
                current_file = new_file
                file_pos = 0
                TICKER_MAP = load_ticker_map(config_dir)

            if not current_file.exists():
                time.sleep(5)
                continue

            current_size = current_file.stat().st_size
            if current_size <= file_pos:
                time.sleep(1)
                continue

            # Read new lines
            with open(current_file) as f:
                f.seek(file_pos)
                new_data = f.read()
                file_pos = f.tell()

            for line in new_data.strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    process_wal_event(event)
                    events_processed += 1
                except json.JSONDecodeError:
                    log.warning("Malformed WAL line: %s", line[:100])

        except KeyboardInterrupt:
            log.info("WAL watcher stopped (processed %d events)", events_processed)
            break
        except Exception as e:
            log.error("WAL watcher error: %s", e, exc_info=True)
            time.sleep(5)


def main():
    parser = argparse.ArgumentParser(description="AEGIS V2 WAL Watcher")
    parser.add_argument("--wal-dir", type=str, default="/app/events",
                        help="WAL directory")
    parser.add_argument("--config-dir", type=str, default="/app/config",
                        help="Config directory")
    args = parser.parse_args()

    wal_dir = Path(args.wal_dir)
    config_dir = Path(args.config_dir)

    log.info("Starting WAL watcher: wal=%s config=%s", wal_dir, config_dir)
    tail_wal(wal_dir, config_dir)


if __name__ == "__main__":
    main()
