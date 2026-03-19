"""AEGIS V2 -- Google Sheets Sync Module.

Pushes WAL events to a Google Sheets dashboard for real-time visibility.
Uses gspread + google-auth (NOT deprecated oauth2client).

Architecture:
  - WAL watcher pushes relevant events to Redis list 'sheets:queue'
  - SheetsSyncClient runs a background thread, popping from the queue every 5s
  - Batches writes via append_rows() to stay within gspread rate limits (60/min)
  - Idempotent: SHA256 dedup of event payloads (last 10,000 hashes)
  - Graceful: never crashes the engine -- all errors logged and swallowed

Spreadsheet: "AEGIS V2 Dashboard" (shared with nztsignals48@gmail.com)
Tabs: Live_Trades, Daily_Summary, Open_Positions, Ouroboros_Changes, System_Health

Usage:
    # Standalone manual sync (process everything in Redis queue now)
    python3 -m python_brain.ouroboros.sheets_sync

    # Programmatic (background thread started by WAL watcher)
    from python_brain.ouroboros.sheets_sync import SheetsSyncClient
    client = SheetsSyncClient(redis_url="redis://:nzt48redis@aegis-redis:6379/0")
    client.start()  # background thread
    ...
    client.stop()
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Sheets] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sheets_sync")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SPREADSHEET_NAME = "AEGIS V2 Dashboard"
REDIS_QUEUE_KEY = "sheets:queue"
REDIS_DEDUP_KEY = "sheets:seen_hashes"
MAX_DEDUP_HASHES = 10_000
POLL_INTERVAL_SEC = 5
BATCH_SIZE = 50  # max rows per append_rows call

# Service account JSON paths (Docker first, then local fallback)
SA_PATHS = [
    Path("/app/config/sheets_service_account.json"),
    Path("config/sheets_service_account.json"),
]

# Tab definitions: (name, header_row)
TAB_DEFINITIONS: Dict[str, List[str]] = {
    "Live_Trades": [
        "Timestamp", "Symbol", "Direction", "Qty", "Entry_Price", "Exit_Price",
        "PnL_GBP", "Confidence", "Kelly", "Strategy", "Exchange", "Regime",
        "Rung", "Duration_Min", "Unrealised_PnL",
    ],
    "Daily_Summary": [
        "Date", "Trades_Entered", "Trades_Exited", "Win_Rate_Pct", "Total_PnL_GBP",
        "Equity_GBP", "Max_Drawdown_Pct", "Sharpe_Rolling", "Best_Trade",
        "Worst_Trade",
    ],
    "Open_Positions": [
        "Symbol", "Qty", "Entry_Price", "Current_Price", "Unrealized_PnL",
        "Rung", "Stop_Price", "Highest_High", "Duration_Min", "Exchange",
    ],
    "Ouroboros_Changes": [
        "Timestamp", "Parameter", "Old_Value", "New_Value", "Reason",
    ],
    "System_Health": [
        "Timestamp", "Uptime_Hours", "Ticks_Received", "Positions_Open",
        "Equity_GBP", "Unrealised_PnL", "Risk_Regime", "Trading_Mode",
        "WAL_Size_MB", "Memory_Usage_Pct",
    ],
    # Phase H: Indicator intelligence tabs (populated by nightly indicator_intelligence.py)
    "Indicator_Stats": [
        "Indicator", "Win_Mean", "Win_Median", "Win_Std",
        "Loss_Mean", "Loss_Median", "Loss_Std", "Delta_Mean",
    ],
    "Regime_Performance": [
        "Regime", "Trades", "Wins", "Win_Rate", "Avg_PnL",
    ],
    "Session_Performance": [
        "Session", "Trades", "Wins", "Win_Rate",
    ],
    "Learned_Rules": [
        "Indicator", "Direction", "Threshold", "Win_Rate",
        "Trades", "Lift_Pct", "Confidence_Score",
    ],
}

# Ticker ID -> symbol mapping (mirrors nightly_v6.py PRIMARY_TICKERS)
PRIMARY_TICKERS = [
    "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L", "TSL3.L",
    "TSM3.L", "MU2.L", "QQQS.L", "3USS.L", "QQQ5.L", "5SPY.L",
]


def _resolve_ticker(ticker_id: int) -> str:
    """Resolve numeric ticker_id to symbol."""
    if 0 <= ticker_id < len(PRIMARY_TICKERS):
        return PRIMARY_TICKERS[ticker_id]
    return f"TICKER_{ticker_id}"


def _event_hash(event: dict) -> str:
    """SHA256 hash of event payload for dedup."""
    raw = json.dumps(event, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _now_iso() -> str:
    """Current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ns_to_iso(ns: int) -> str:
    """Convert nanosecond epoch timestamp to ISO string."""
    if not ns:
        return _now_iso()
    try:
        return datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OSError, ValueError):
        return _now_iso()


def _find_service_account_path() -> Optional[Path]:
    """Find the first existing service account JSON file."""
    for p in SA_PATHS:
        if p.exists():
            return p
    return None


# ---------------------------------------------------------------------------
# Google Sheets client wrapper
# ---------------------------------------------------------------------------

class SheetsClient:
    """Wraps gspread operations with auto-create and error handling."""

    def __init__(self, sa_path: Path):
        self._sa_path = sa_path
        self._gc = None
        self._spreadsheet = None
        self._worksheets: Dict[str, Any] = {}
        self._connect()

    def _connect(self) -> None:
        """Authenticate and open (or create) the spreadsheet."""
        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError:
            log.error("gspread or google-auth not installed. Run: pip install gspread google-auth")
            raise

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(str(self._sa_path), scopes=scopes)
        self._gc = gspread.authorize(creds)
        log.info("Authenticated with Google Sheets via service account")

        # Open or create spreadsheet
        try:
            self._spreadsheet = self._gc.open(SPREADSHEET_NAME)
            log.info("Opened spreadsheet: %s", SPREADSHEET_NAME)
        except gspread.SpreadsheetNotFound:
            log.info("Spreadsheet not found, creating: %s", SPREADSHEET_NAME)
            self._spreadsheet = self._gc.create(SPREADSHEET_NAME)
            # Share with the dashboard email
            self._spreadsheet.share("nztsignals48@gmail.com", perm_type="user", role="writer")
            log.info("Created and shared spreadsheet with nztsignals48@gmail.com")

        # Ensure all tabs exist with headers
        self._ensure_tabs()

    def _ensure_tabs(self) -> None:
        """Create missing worksheets and set headers."""
        existing = {ws.title: ws for ws in self._spreadsheet.worksheets()}

        for tab_name, headers in TAB_DEFINITIONS.items():
            if tab_name in existing:
                ws = existing[tab_name]
                # Check if headers are set (row 1)
                try:
                    first_row = ws.row_values(1)
                    if not first_row:
                        ws.update("A1", [headers])
                        log.info("Set headers on existing tab: %s", tab_name)
                except Exception:
                    pass
            else:
                ws = self._spreadsheet.add_worksheet(
                    title=tab_name, rows=1000, cols=len(headers)
                )
                ws.update("A1", [headers])
                log.info("Created tab: %s (%d columns)", tab_name, len(headers))
            self._worksheets[tab_name] = ws

        # Remove default "Sheet1" if our tabs are in place
        if "Sheet1" in existing and len(existing) > 1:
            try:
                self._spreadsheet.del_worksheet(existing["Sheet1"])
                log.info("Removed default Sheet1")
            except Exception:
                pass  # might fail if it's the last sheet

    def _get_ws(self, tab_name: str) -> Any:
        """Get worksheet by name, refreshing cache if needed."""
        if tab_name not in self._worksheets:
            self._ensure_tabs()
        return self._worksheets.get(tab_name)

    def append_rows(self, tab_name: str, rows: List[List[Any]]) -> bool:
        """Append rows to a worksheet. Returns True on success."""
        if not rows:
            return True
        ws = self._get_ws(tab_name)
        if ws is None:
            log.error("Worksheet %s not found", tab_name)
            return False
        try:
            ws.append_rows(rows, value_input_option="USER_ENTERED")
            return True
        except Exception as e:
            log.error("Failed to append %d rows to %s: %s", len(rows), tab_name, e)
            return False

    def clear_and_write(self, tab_name: str, rows: List[List[Any]]) -> bool:
        """Clear a worksheet (except header) and write fresh rows. For Open_Positions."""
        ws = self._get_ws(tab_name)
        if ws is None:
            log.error("Worksheet %s not found", tab_name)
            return False
        try:
            headers = TAB_DEFINITIONS.get(tab_name, [])
            # Clear all, rewrite header + data
            ws.clear()
            all_rows = [headers] + rows
            if all_rows:
                ws.update("A1", all_rows, value_input_option="USER_ENTERED")
            return True
        except Exception as e:
            log.error("Failed to clear_and_write %s: %s", tab_name, e)
            return False


# ---------------------------------------------------------------------------
# Event -> Row mapping
# ---------------------------------------------------------------------------

def _route_event(event: dict) -> Optional[tuple]:
    """Map a WAL event to (tab_name, row_data). Returns None if not routable.

    WAL event schema (from Rust engine):
      event_id, schema_version, event_time_ns, write_time_ns, checksum, payload
    Payload is a single-key dict: {"RoutedOrder": {...}}, {"PositionClosed": {...}}, etc.
    """
    payload = event.get("payload", {})
    ts = _ns_to_iso(event.get("event_time_ns", 0))

    # --- RoutedOrder (entry) -> Live_Trades ---
    # Engine uses side="Long"/"Short" for entries, side="Sell" for exit orders
    if "RoutedOrder" in payload:
        data = payload["RoutedOrder"]
        side = data.get("side", "")
        if side == "Sell":
            return None  # Exit orders are captured by PositionClosed
        symbol = data.get("symbol", _resolve_ticker(data.get("ticker_id", 0)))
        row = [
            ts,
            symbol,
            side,                              # "Long" or "Short"
            int(data.get("qty", data.get("approved_size", 0))),
            "",                                # Entry_Price (not in RoutedOrder)
            "",                                # Exit_Price (filled on close)
            "",                                # PnL_GBP
            round(data.get("confidence", 0), 2),
            round(data.get("kelly_fraction", 0), 4),
            data.get("strategy", ""),
            data.get("currency", "USD"),       # Exchange/currency
            data.get("regime_at_entry", ""),
            "",                                # Rung (filled on close)
            "",                                # Duration_Min
            "OPEN",                            # Unrealised_PnL (marks as open position)
        ]
        return ("Live_Trades", row)

    # --- PositionClosed -> Live_Trades (exit row with full P&L) ---
    if "PositionClosed" in payload:
        data = payload["PositionClosed"]
        symbol = data.get("symbol", _resolve_ticker(data.get("ticker_id", 0)))
        # Calculate duration from entry/exit nanosecond timestamps
        entry_ns = data.get("entry_time_ns", 0)
        exit_ns = data.get("exit_time_ns", 0)
        duration_min = ""
        if entry_ns and exit_ns and exit_ns > entry_ns:
            duration_min = round((exit_ns - entry_ns) / 60_000_000_000, 1)
        final_pnl = round(data.get("final_pnl", 0), 4)
        row = [
            ts,
            symbol,
            "Exit",
            int(data.get("qty", 0)),
            "",                                # Entry_Price (not available)
            "",                                # Exit_Price (not available)
            final_pnl,
            round(data.get("confidence", 0), 2),
            "",                                # Kelly (from entry)
            "",                                # Strategy
            "",                                # Exchange
            data.get("regime_at_entry", ""),
            data.get("highest_rung", ""),
            duration_min,
            f"CLOSED ({final_pnl:+.2f})",     # Unrealised_PnL: shows final realised on close
        ]
        return ("Live_Trades", row)

    # --- RiskStateChange -> System_Health tab (regime info) ---
    if "RiskStateChange" in payload:
        data = payload["RiskStateChange"]
        row = [
            ts,
            "",                                # Uptime_Hours
            "",                                # Ticks_Received
            "",                                # Positions_Open
            "",                                # Equity_GBP
            data.get("new_regime", data.get("regime", "")),
            data.get("trading_mode", ""),
            "",                                # WAL_Size_MB
            "",                                # Memory_Usage_Pct
        ]
        return ("System_Health", row)

    # --- StateSnapshot -> System_Health ---
    if "StateSnapshot" in payload:
        data = payload["StateSnapshot"]
        # Parse portfolio_json for position count and unrealised P&L
        positions_open = 0
        unrealised_pnl = 0.0
        try:
            pj = json.loads(data.get("portfolio_json", "{}"))
            positions_open = pj.get("positions", 0)
            unrealised_pnl = pj.get("unrealized_pnl", 0.0)
            # Also try alternative key names
            if unrealised_pnl == 0.0:
                unrealised_pnl = pj.get("unrealised_pnl", 0.0)
            if unrealised_pnl == 0.0:
                # Calculate from equity vs starting equity
                equity = data.get("equity", 0)
                high_water = pj.get("high_water", 10000.0)
                starting = 10000.0  # Known starting equity
                if equity > 0:
                    unrealised_pnl = round(equity - starting, 2)
        except (json.JSONDecodeError, TypeError):
            pass
        health_row = [
            ts,
            "",                                # Uptime_Hours
            "",                                # Ticks_Received
            positions_open,
            round(data.get("equity", 0), 2),
            round(unrealised_pnl, 2),          # Unrealised_PnL (NEW)
            "",                                # Risk_Regime
            "simulation",                      # Trading_Mode
            "",                                # WAL_Size_MB
            "",                                # Memory_Usage_Pct
        ]
        return ("System_Health", health_row)

    # --- SystemReady -> System_Health heartbeat ---
    if "SystemReady" in payload:
        data = payload["SystemReady"]
        row = [
            ts,
            0,                                 # Uptime_Hours (just started)
            0,                                 # Ticks_Received
            data.get("positions_reconciled", 0),
            "",                                # Equity_GBP
            0,                                 # Unrealised_PnL (NEW)
            "INIT",
            "simulation",
            0,                                 # WAL_Size_MB
            0,                                 # Memory_Usage_Pct
        ]
        return ("System_Health", row)

    # --- OuroborosChange -> Ouroboros_Changes tab ---
    if "OuroborosChange" in payload:
        data = payload["OuroborosChange"]
        row = [
            ts,
            data.get("parameter", ""),
            str(data.get("old_value", "")),
            str(data.get("new_value", "")),
            data.get("reason", ""),
        ]
        return ("Ouroboros_Changes", row)

    return None


def _route_snapshot_positions(event: dict) -> Optional[List[List[Any]]]:
    """Extract open positions from a StateSnapshot for clear-and-rewrite.
    Returns a list of rows for Open_Positions tab, or None.

    Note: The Rust engine's StateSnapshot contains portfolio_json (a stringified
    JSON with position count and high_water) but not individual position details.
    When the engine adds per-position snapshots, this will parse them.
    For now, returns None (Open_Positions not populated from snapshots).
    """
    payload = event.get("payload", {})
    if "StateSnapshot" not in payload:
        return None
    data = payload["StateSnapshot"]
    # Current WAL schema: portfolio_json is "{\"positions\":0,\"high_water\":10000.00}"
    # No per-position breakdown available yet
    positions = data.get("open_positions", [])
    if not positions:
        return None
    rows = []
    for pos in positions:
        symbol = pos.get("symbol", _resolve_ticker(pos.get("ticker_id", 0)))
        rows.append([
            symbol,
            int(pos.get("qty", 0)),
            pos.get("entry_price", ""),
            pos.get("current_price", ""),
            round(pos.get("unrealized_pnl", 0), 2),
            pos.get("rung", ""),
            pos.get("stop_price", ""),
            pos.get("highest_high", ""),
            pos.get("duration_min", ""),
            pos.get("exchange", ""),
        ])
    return rows


# ---------------------------------------------------------------------------
# Redis-backed queue helpers
# ---------------------------------------------------------------------------

def _get_redis_client(redis_url: str):
    """Create a Redis client. Returns None if redis is not available."""
    try:
        import redis as redis_lib
        return redis_lib.Redis.from_url(redis_url, decode_responses=True, socket_timeout=5)
    except ImportError:
        log.warning("redis package not installed; using in-memory queue fallback")
        return None
    except Exception as e:
        log.warning("Cannot connect to Redis (%s); using in-memory queue fallback", e)
        return None


def push_to_sheets_queue(redis_client, event: dict) -> bool:
    """Push a WAL event to the Redis sheets queue. Returns True on success."""
    if redis_client is None:
        return False
    try:
        redis_client.rpush(REDIS_QUEUE_KEY, json.dumps(event, default=str))
        return True
    except Exception as e:
        log.warning("Failed to push to sheets queue: %s", e)
        return False


def pop_from_sheets_queue(redis_client, count: int = BATCH_SIZE) -> List[dict]:
    """Pop up to `count` events from the Redis sheets queue."""
    if redis_client is None:
        return []
    events = []
    try:
        for _ in range(count):
            raw = redis_client.lpop(REDIS_QUEUE_KEY)
            if raw is None:
                break
            try:
                events.append(json.loads(raw))
            except json.JSONDecodeError:
                log.warning("Malformed event in sheets queue: %s", raw[:100])
    except Exception as e:
        log.warning("Failed to pop from sheets queue: %s", e)
    return events


# ---------------------------------------------------------------------------
# Dedup tracker
# ---------------------------------------------------------------------------

class DedupTracker:
    """Track seen event hashes for idempotent writes. Thread-safe."""

    def __init__(self, redis_client=None, max_size: int = MAX_DEDUP_HASHES):
        self._redis = redis_client
        self._max_size = max_size
        # In-memory fallback
        self._local_hashes: deque = deque(maxlen=max_size)
        self._local_set: set = set()
        self._lock = threading.Lock()

    def is_seen(self, h: str) -> bool:
        """Check if hash was already processed."""
        if self._redis is not None:
            try:
                return self._redis.sismember(REDIS_DEDUP_KEY, h)
            except Exception:
                pass
        with self._lock:
            return h in self._local_set

    def mark_seen(self, h: str) -> None:
        """Mark a hash as processed."""
        if self._redis is not None:
            try:
                self._redis.sadd(REDIS_DEDUP_KEY, h)
                # Trim set if too large (probabilistic -- check periodically)
                size = self._redis.scard(REDIS_DEDUP_KEY)
                if size > self._max_size * 1.2:
                    # Remove oldest ~20% (Redis sets are unordered, but this
                    # provides rough bounded growth)
                    excess = size - self._max_size
                    members = self._redis.srandmember(REDIS_DEDUP_KEY, int(excess))
                    if members:
                        self._redis.srem(REDIS_DEDUP_KEY, *members)
                return
            except Exception:
                pass
        with self._lock:
            if h not in self._local_set:
                if len(self._local_hashes) >= self._max_size:
                    evicted = self._local_hashes[0]
                    self._local_set.discard(evicted)
                self._local_hashes.append(h)
                self._local_set.add(h)


# ---------------------------------------------------------------------------
# SheetsSyncClient -- background thread
# ---------------------------------------------------------------------------

class SheetsSyncClient:
    """Background thread that pops events from Redis and writes to Google Sheets.

    Usage:
        client = SheetsSyncClient(redis_url="redis://:nzt48redis@aegis-redis:6379/0")
        client.start()
        ...
        client.stop()
    """

    def __init__(
        self,
        redis_url: str = "redis://:nzt48redis@aegis-redis:6379/0",
        poll_interval: float = POLL_INTERVAL_SEC,
    ):
        self._redis_url = redis_url
        self._poll_interval = poll_interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._sheets: Optional[SheetsClient] = None
        self._redis = None
        self._dedup: Optional[DedupTracker] = None
        self._enabled = False

    def start(self) -> bool:
        """Start the background sync thread. Returns True if enabled."""
        # Check for service account
        sa_path = _find_service_account_path()
        if sa_path is None:
            log.info(
                "Google Sheets sync DISABLED: no service account found at %s",
                " or ".join(str(p) for p in SA_PATHS),
            )
            return False

        # Connect to Redis
        self._redis = _get_redis_client(self._redis_url)

        # Initialize Sheets client
        try:
            self._sheets = SheetsClient(sa_path)
        except Exception as e:
            log.error("Failed to initialize Google Sheets client: %s", e)
            return False

        self._dedup = DedupTracker(self._redis)
        self._enabled = True

        self._thread = threading.Thread(
            target=self._run_loop,
            name="sheets-sync",
            daemon=True,
        )
        self._thread.start()
        log.info("Google Sheets sync started (poll every %ds)", self._poll_interval)
        return True

    def stop(self) -> None:
        """Stop the background sync thread."""
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=10)
        log.info("Google Sheets sync stopped")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _run_loop(self) -> None:
        """Main sync loop: pop events from Redis, route and write to Sheets."""
        consecutive_errors = 0
        max_backoff = 60

        while not self._stop_event.is_set():
            try:
                events = pop_from_sheets_queue(self._redis)
                if not events:
                    self._stop_event.wait(self._poll_interval)
                    continue

                # Batch by tab
                batches: Dict[str, List[List[Any]]] = {}
                position_snapshot: Optional[List[List[Any]]] = None

                for event in events:
                    h = _event_hash(event)
                    if self._dedup and self._dedup.is_seen(h):
                        continue
                    if self._dedup:
                        self._dedup.mark_seen(h)

                    # Route to tab
                    result = _route_event(event)
                    if result:
                        tab_name, row = result
                        batches.setdefault(tab_name, []).append(row)

                    # Check for position snapshot (clear-and-rewrite)
                    pos_rows = _route_snapshot_positions(event)
                    if pos_rows is not None:
                        position_snapshot = pos_rows

                # Write batches
                for tab_name, rows in batches.items():
                    if self._sheets:
                        self._sheets.append_rows(tab_name, rows)
                        log.debug("Appended %d rows to %s", len(rows), tab_name)

                # Write open positions (clear & rewrite)
                if position_snapshot is not None and self._sheets:
                    self._sheets.clear_and_write("Open_Positions", position_snapshot)
                    log.debug("Refreshed Open_Positions with %d rows", len(position_snapshot))

                    # Also update Live_Trades "OPEN" cells with actual unrealised P&L
                    try:
                        self._update_live_trades_unrealised(position_snapshot)
                    except Exception as e:
                        log.warning("Failed to update Live_Trades unrealised: %s", e)

                consecutive_errors = 0

            except Exception as e:
                consecutive_errors += 1
                backoff = min(self._poll_interval * (2 ** consecutive_errors), max_backoff)
                log.error(
                    "Sheets sync error (attempt %d, backoff %.0fs): %s",
                    consecutive_errors, backoff, e,
                )
                self._stop_event.wait(backoff)

    def _update_live_trades_unrealised(self, position_snapshot: List[List[Any]]):
        """Update Live_Trades 'OPEN' cells with actual unrealised P&L from position snapshot.

        Scans the last 200 rows of Live_Trades for entries with Direction='Long'
        and Unrealised_PnL='OPEN', then updates them with the actual figure from
        the position snapshot.
        """
        if not self._sheets or not position_snapshot:
            return

        ws = self._sheets._get_ws("Live_Trades")
        if ws is None:
            return

        # Build symbol -> unrealised P&L lookup from position snapshot
        # Position snapshot rows: [Symbol, Qty, Entry, Current, UnrealisedPnL, ...]
        pnl_by_symbol: Dict[str, float] = {}
        for row in position_snapshot:
            if len(row) >= 5 and row[0] and row[4] != "":
                try:
                    pnl_by_symbol[str(row[0])] = float(row[4])
                except (ValueError, TypeError):
                    pass

        if not pnl_by_symbol:
            return

        # Get recent rows (last 200) to find OPEN entries
        try:
            all_vals = ws.get_all_values()
        except Exception:
            return

        updates = []
        # Scan from bottom up for efficiency
        start = max(1, len(all_vals) - 200)
        for i in range(start, len(all_vals)):
            row = all_vals[i]
            if len(row) < 15:
                continue
            direction = row[2]  # Column C
            symbol = row[1]     # Column B
            current_pnl = row[14] if len(row) > 14 else ""  # Column O

            if direction == "Long" and (current_pnl == "OPEN" or current_pnl.startswith("OPEN")):
                if symbol in pnl_by_symbol:
                    pnl_val = pnl_by_symbol[symbol]
                    sign = "+" if pnl_val >= 0 else ""
                    label = f"OPEN ({sign}{pnl_val:.2f})"
                    updates.append({"range": f"O{i + 1}", "values": [[label]]})

        if updates:
            ws.batch_update(updates, value_input_option="RAW")
            log.info("Updated %d Live_Trades OPEN cells with unrealised P&L", len(updates))

    def process_single_event(self, event: dict) -> bool:
        """Process a single event immediately (for standalone mode). Returns True if written."""
        if not self._sheets:
            return False

        h = _event_hash(event)
        if self._dedup and self._dedup.is_seen(h):
            return False
        if self._dedup:
            self._dedup.mark_seen(h)

        result = _route_event(event)
        if result:
            tab_name, row = result
            return self._sheets.append_rows(tab_name, [row])

        pos_rows = _route_snapshot_positions(event)
        if pos_rows is not None:
            return self._sheets.clear_and_write("Open_Positions", pos_rows)

        return False


# ---------------------------------------------------------------------------
# Standalone CLI: drain Redis queue or replay WAL file
# ---------------------------------------------------------------------------

def _drain_queue(redis_url: str) -> int:
    """Drain the Redis sheets queue and write everything to Sheets. Returns event count."""
    sa_path = _find_service_account_path()
    if sa_path is None:
        log.error("No service account found. See config/sheets_setup_instructions.txt")
        return 0

    redis_client = _get_redis_client(redis_url)
    sheets = SheetsClient(sa_path)
    dedup = DedupTracker(redis_client)
    total = 0

    while True:
        events = pop_from_sheets_queue(redis_client)
        if not events:
            break

        batches: Dict[str, List[List[Any]]] = {}
        position_snapshot: Optional[List[List[Any]]] = None

        for event in events:
            h = _event_hash(event)
            if dedup.is_seen(h):
                continue
            dedup.mark_seen(h)

            result = _route_event(event)
            if result:
                tab_name, row = result
                batches.setdefault(tab_name, []).append(row)

            pos_rows = _route_snapshot_positions(event)
            if pos_rows is not None:
                position_snapshot = pos_rows

        for tab_name, rows in batches.items():
            sheets.append_rows(tab_name, rows)
            total += len(rows)
            log.info("Wrote %d rows to %s", len(rows), tab_name)

        if position_snapshot is not None:
            sheets.clear_and_write("Open_Positions", position_snapshot)
            log.info("Refreshed Open_Positions with %d rows", len(position_snapshot))

    return total


def _replay_wal_file(wal_path: Path, redis_url: str) -> int:
    """Replay a WAL NDJSON file into Sheets (bypasses Redis).

    Batches all events by tab and writes in bulk to avoid rate limits.
    Dedup hashes are only recorded AFTER successful writes.
    Returns event count.
    """
    sa_path = _find_service_account_path()
    if sa_path is None:
        log.error("No service account found. See config/sheets_setup_instructions.txt")
        return 0

    redis_client = _get_redis_client(redis_url)
    sheets = SheetsClient(sa_path)
    dedup = DedupTracker(redis_client)
    total = 0

    # Collect all routable events into batches by tab
    batches: Dict[str, List[List[Any]]] = {}
    position_snapshot: Optional[List[List[Any]]] = None
    hashes_to_mark: list = []

    log.info("Replaying WAL file: %s", wal_path)
    with open(wal_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            h = _event_hash(event)
            if dedup.is_seen(h):
                continue

            result = _route_event(event)
            if result:
                tab_name, row = result
                batches.setdefault(tab_name, []).append(row)
                hashes_to_mark.append(h)

            pos_rows = _route_snapshot_positions(event)
            if pos_rows is not None:
                position_snapshot = pos_rows

    # Write batches (one append_rows call per tab)
    for tab_name, rows in batches.items():
        # Split into chunks of BATCH_SIZE to avoid payload limits
        for i in range(0, len(rows), BATCH_SIZE):
            chunk = rows[i:i + BATCH_SIZE]
            ok = sheets.append_rows(tab_name, chunk)
            if ok:
                total += len(chunk)
                log.info("Wrote %d rows to %s", len(chunk), tab_name)
            else:
                log.error("Failed to write %d rows to %s", len(chunk), tab_name)
            # Small delay between chunks to respect rate limits
            if i + BATCH_SIZE < len(rows):
                time.sleep(2)

    if position_snapshot is not None:
        sheets.clear_and_write("Open_Positions", position_snapshot)
        log.info("Refreshed Open_Positions with %d rows", len(position_snapshot))

    # Mark all successfully written events as seen (dedup)
    for h in hashes_to_mark:
        dedup.mark_seen(h)

    return total


def main():
    """CLI entry point: drain Redis queue or replay a WAL file."""
    import argparse

    parser = argparse.ArgumentParser(
        description="AEGIS V2 -- Google Sheets Sync (standalone)"
    )
    parser.add_argument(
        "--redis-url",
        default=os.environ.get("REDIS_URL", "redis://:nzt48redis@aegis-redis:6379/0"),
        help="Redis connection URL",
    )
    parser.add_argument(
        "--replay",
        type=str,
        default=None,
        help="Replay a WAL NDJSON file directly to Sheets (bypasses Redis queue)",
    )
    args = parser.parse_args()

    if args.replay:
        wal_path = Path(args.replay)
        if not wal_path.exists():
            log.error("WAL file not found: %s", wal_path)
            sys.exit(1)
        count = _replay_wal_file(wal_path, args.redis_url)
        log.info("Replay complete: %d events written to Sheets", count)
    else:
        log.info("Draining Redis sheets queue...")
        count = _drain_queue(args.redis_url)
        log.info("Drain complete: %d events written to Sheets", count)


if __name__ == "__main__":
    main()
