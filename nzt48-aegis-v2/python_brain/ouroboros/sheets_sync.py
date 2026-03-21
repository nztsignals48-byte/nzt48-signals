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
        "Date", "Indicator", "Win_Count", "Win_Mean", "Win_Median", "Win_Std",
        "Loss_Count", "Loss_Mean", "Loss_Median", "Loss_Std",
    ],
    "Regime_Performance": [
        "Date", "Regime", "Trades", "Wins", "Win_Rate", "Avg_PnL", "Total_PnL",
    ],
    "Session_Performance": [
        "Date", "Session", "Trades", "Wins", "Win_Rate", "Avg_PnL",
    ],
    "Learned_Rules": [
        "Date", "Indicator", "Direction", "Threshold", "Trades",
        "Win_Rate", "Lift_Pct", "Avg_PnL",
    ],
    # ---------------------------------------------------------------------------
    # N4a: 12 new tabs (21-tab architecture)
    # ---------------------------------------------------------------------------
    # Tab 3: Full trade lifecycle with N2b enrichment + N1b taxonomy
    "Closed_Trades": [
        "Timestamp", "Symbol", "Entry_Price", "Exit_Price", "Qty",
        "Gross_PnL", "Commission", "Net_PnL", "Trade_Class",
        "Highest_Rung", "MAE", "MFE", "Spread_Entry_Pct", "Spread_Exit_Pct",
        "Hold_Time_Min", "Session_Phase", "VWAP_Dist_Pct", "ATR_Pct",
        "RVOL", "Hurst", "ADX", "Vol_Slope", "VIX",
        "Regime", "Confidence", "Strategy", "Daily_Trade_Num",
    ],
    # Tab 4: Indicator values on winning trades only
    "Win_Indicators": [
        "Timestamp", "Symbol", "Net_PnL", "Confidence", "RVOL", "Hurst",
        "ADX", "ATR_Pct", "Spread_Pct", "Vol_Slope", "Rung", "Hold_Min",
    ],
    # Tab 5: Indicator values on losing trades only
    "Loss_Indicators": [
        "Timestamp", "Symbol", "Net_PnL", "Confidence", "RVOL", "Hurst",
        "ADX", "ATR_Pct", "Spread_Pct", "Vol_Slope", "Rung", "Hold_Min",
    ],
    # Tab 6: Win vs Loss indicator comparison (populated by N4b nightly)
    "Win_Loss_Delta": [
        "Date", "Indicator", "Win_Mean", "Loss_Mean", "Delta",
        "Win_P25", "Loss_P25", "Predictive", "Suggested_Gate",
    ],
    # Tab 7: Every rejected signal with full gate context
    "Rejected_Signals": [
        "Timestamp", "Symbol", "Strategy", "Confidence", "Gate_Name",
        "Gate_Reason", "Price", "Hurst", "ADX", "RVOL", "Vol_Slope",
        "Spread_Pct",
    ],
    # Tab 8: Rejected signals that would have won (nightly MissedWinnerCandidate)
    "Missed_Winners": [
        "Timestamp", "Symbol", "Strategy", "Gate_Name", "Confidence",
        "Price_At_Reject", "Peak_Price_After", "Hypothetical_PnL_Pct",
        "Window_Min",
    ],
    # Tab 9: MAE/MFE per trade for execution quality analysis
    "MAE_MFE": [
        "Timestamp", "Symbol", "MAE", "MFE", "MAE_MFE_Ratio",
        "MFE_At_Exit_Pct", "Left_On_Table_Pct", "Rung", "Net_PnL",
        "Trade_Class", "Hold_Min",
    ],
    # Tab 13: Spread and cost attribution per trade
    "Spread_Execution": [
        "Timestamp", "Symbol", "Spread_Entry_Pct", "Spread_Exit_Pct",
        "Total_Spread_Cost", "Commission", "Gross_PnL", "Net_PnL",
        "Cost_Drag_Pct",
    ],
    # Tab 14: Per-class trade taxonomy statistics (nightly)
    "Trade_Classes": [
        "Date", "Class", "Count", "Wins", "Losses", "Win_Rate",
        "Total_PnL", "Avg_PnL", "Avg_Hold_Min", "Avg_Rung",
    ],
    # Tab 19: Ticker scoreboard promote/hold/demote/kill (nightly)
    "Ticker_Scoreboard": [
        "Date", "Symbol", "Score", "Verdict", "Trades", "Win_Rate",
        "Profit_Factor", "Avg_Rung", "Avg_Spread_Cost",
    ],
    # Tab 16: Ouroboros parameter evolution over time (nightly)
    "Parameter_Evolution": [
        "Date", "Parameter", "Value", "Previous_Value", "Change_Pct",
    ],
    # Tab 21: Configuration diff log (real-time from OuroborosChange + RungAdvanced)
    "Config_Diff_Log": [
        "Timestamp", "Event_Type", "Symbol", "Detail", "Old_Value", "New_Value",
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

def _route_event(event: dict) -> List[tuple]:
    """Map a WAL event to list of (tab_name, row_data) tuples.

    N4a: A single event can now produce rows for multiple tabs.
    E.g. PositionClosed -> Live_Trades + Closed_Trades + MAE_MFE + Spread_Execution + Win/Loss_Indicators.

    WAL event schema (from Rust engine):
      event_id, schema_version, event_time_ns, write_time_ns, checksum, payload
    Payload is a single-key dict: {"RoutedOrder": {...}}, {"PositionClosed": {...}}, etc.
    """
    results: List[tuple] = []
    payload = event.get("payload", {})
    ts = _ns_to_iso(event.get("event_time_ns", 0))

    # --- RoutedOrder (entry) -> Live_Trades ---
    if "RoutedOrder" in payload:
        data = payload["RoutedOrder"]
        side = data.get("side", "")
        if side == "Sell":
            return results  # Exit orders captured by PositionClosed
        symbol = data.get("symbol", _resolve_ticker(data.get("ticker_id", 0)))
        row = [
            ts,
            symbol,
            side,
            int(data.get("qty", data.get("approved_size", 0))),
            "",                                # Entry_Price
            "",                                # Exit_Price
            "",                                # PnL_GBP
            round(data.get("confidence", 0), 2),
            round(data.get("kelly_fraction", 0), 4),
            data.get("strategy", ""),
            data.get("currency", "USD"),
            data.get("regime_at_entry", ""),
            "",                                # Rung
            "",                                # Duration_Min
            "OPEN",
        ]
        results.append(("Live_Trades", row))
        return results

    # --- PositionClosed -> Live_Trades + Closed_Trades + MAE_MFE + Spread_Execution + Win/Loss_Indicators ---
    if "PositionClosed" in payload:
        data = payload["PositionClosed"]
        symbol = data.get("symbol", _resolve_ticker(data.get("ticker_id", 0)))
        entry_ns = data.get("entry_time_ns", 0)
        exit_ns = data.get("exit_time_ns", 0)
        hold_min = data.get("hold_time_mins", 0)
        if not hold_min and entry_ns and exit_ns and exit_ns > entry_ns:
            hold_min = round((exit_ns - entry_ns) / 60_000_000_000, 1)
        final_pnl = round(data.get("final_pnl", 0), 4)
        gross_pnl = round(data.get("gross_pnl", final_pnl), 4)
        commission = round(data.get("total_commission", 0), 4)
        rung = data.get("highest_rung", 0)
        mae = round(data.get("mae", 0), 4)
        mfe = round(data.get("mfe", 0.0001), 4)
        spread_entry = round(data.get("spread_at_entry_pct", 0), 4)
        spread_exit = round(data.get("spread_at_exit_pct", 0), 4)
        confidence = round(data.get("confidence", 0), 2)
        regime = data.get("regime_at_entry", "")
        strategy = data.get("strategy", "")
        session_phase = data.get("entry_session_phase", "")
        vwap_dist = round(data.get("vwap_dist_at_entry_pct", 0), 4)
        atr_pct = round(data.get("atr_pct_at_entry", 0), 4)
        rvol = round(data.get("entry_rvol", 0), 4)
        hurst = round(data.get("entry_hurst", 0), 4)
        adx = round(data.get("entry_adx", 0), 4)
        vol_slope = round(data.get("vol_slope_at_entry", 0), 4)
        vix = round(data.get("vix_at_entry", 0), 2)
        entry_price = round(data.get("entry_price", 0), 4)
        exit_price = round(data.get("exit_price", 0), 4)
        qty = int(data.get("qty", 0))
        daily_trade_num = data.get("daily_trade_number", "")
        trade_class = data.get("trade_class", "")

        # Classify trade if not already classified
        if not trade_class:
            try:
                from python_brain.ouroboros.trade_taxonomy import classify_trade
                trade_class = classify_trade(data)
            except ImportError:
                trade_class = "unclassified"

        # 1. Live_Trades (existing)
        results.append(("Live_Trades", [
            ts, symbol, "Exit", qty, entry_price, exit_price, final_pnl,
            confidence, "", strategy, "", regime, rung, hold_min,
            f"CLOSED ({final_pnl:+.2f})",
        ]))

        # 2. Closed_Trades (full enriched lifecycle — N4a)
        results.append(("Closed_Trades", [
            ts, symbol, entry_price, exit_price, qty,
            gross_pnl, commission, final_pnl, trade_class,
            rung, mae, mfe, spread_entry, spread_exit,
            hold_min, session_phase, vwap_dist, atr_pct,
            rvol, hurst, adx, vol_slope, vix,
            regime, confidence, strategy, daily_trade_num,
        ]))

        # 3. Win_Indicators or Loss_Indicators (N4a)
        indicator_row = [
            ts, symbol, final_pnl, confidence, rvol, hurst,
            adx, atr_pct, spread_entry, vol_slope, rung, hold_min,
        ]
        if final_pnl > 0:
            results.append(("Win_Indicators", indicator_row))
        else:
            results.append(("Loss_Indicators", indicator_row))

        # 4. MAE_MFE (N4a)
        mfe_abs = abs(mfe) if mfe else 0.0001
        mae_abs = abs(mae) if mae else 0
        mae_mfe_ratio = round(mae_abs / mfe_abs, 4) if mfe_abs > 0 else 0
        # MFE at exit: what % of MFE was captured
        mfe_at_exit_pct = round(final_pnl / mfe_abs * 100, 1) if mfe_abs > 0 and final_pnl > 0 else 0
        left_on_table = round(max(0, (mfe_abs - max(final_pnl, 0)) / mfe_abs * 100), 1) if mfe_abs > 0 else 0
        results.append(("MAE_MFE", [
            ts, symbol, mae, mfe, mae_mfe_ratio,
            mfe_at_exit_pct, left_on_table, rung, final_pnl,
            trade_class, hold_min,
        ]))

        # 5. Spread_Execution (N4a)
        position_value = max(entry_price * qty, 1.0)
        total_spread_cost = round((spread_entry + spread_exit) / 100.0 * position_value, 4)
        cost_drag_pct = round((commission + total_spread_cost) / max(abs(gross_pnl), 0.01) * 100, 1)
        results.append(("Spread_Execution", [
            ts, symbol, spread_entry, spread_exit,
            total_spread_cost, commission, gross_pnl, final_pnl,
            cost_drag_pct,
        ]))

        return results

    # --- SignalRejected -> Rejected_Signals (N4a / N2a) ---
    if "SignalRejected" in payload:
        data = payload["SignalRejected"]
        symbol = data.get("symbol", _resolve_ticker(data.get("ticker_id", 0)))
        results.append(("Rejected_Signals", [
            ts,
            symbol,
            data.get("strategy", ""),
            round(data.get("confidence", 0), 2),
            data.get("gate_name", ""),
            data.get("gate_reason", ""),
            round(data.get("price_at_reject", 0), 4),
            round(data.get("hurst", 0), 4),
            round(data.get("adx", 0), 4),
            round(data.get("rvol", 0), 4),
            round(data.get("vol_slope", 0), 4),
            round(data.get("spread_pct", 0), 4),
        ]))
        return results

    # --- MissedWinnerCandidate -> Missed_Winners (N4a / N2c) ---
    if "MissedWinnerCandidate" in payload:
        data = payload["MissedWinnerCandidate"]
        symbol = data.get("symbol", _resolve_ticker(data.get("ticker_id", 0)))
        results.append(("Missed_Winners", [
            ts,
            symbol,
            data.get("strategy", ""),
            data.get("gate_name", ""),
            round(data.get("confidence", 0), 2),
            round(data.get("price_at_reject", 0), 4),
            round(data.get("peak_price_after", 0), 4),
            round(data.get("hypothetical_pnl_pct", 0), 2),
            data.get("window_min", ""),
        ]))
        return results

    # --- RiskStateChange -> System_Health (existing) + Config_Diff_Log (N4a) ---
    if "RiskStateChange" in payload:
        data = payload["RiskStateChange"]
        old_regime = data.get("old_regime", data.get("previous_regime", ""))
        new_regime = data.get("new_regime", data.get("regime", ""))
        results.append(("System_Health", [
            ts, "", "", "", "", new_regime, data.get("trading_mode", ""), "", "",
        ]))
        results.append(("Config_Diff_Log", [
            ts, "RiskStateChange", "", f"Regime: {old_regime} → {new_regime}",
            old_regime, new_regime,
        ]))
        return results

    # --- StateSnapshot -> System_Health (existing) ---
    if "StateSnapshot" in payload:
        data = payload["StateSnapshot"]
        positions_open = 0
        unrealised_pnl = 0.0
        try:
            pj = json.loads(data.get("portfolio_json", "{}"))
            positions_open = pj.get("positions", 0)
            unrealised_pnl = pj.get("unrealized_pnl", 0.0)
            if unrealised_pnl == 0.0:
                unrealised_pnl = pj.get("unrealised_pnl", 0.0)
            if unrealised_pnl == 0.0:
                equity = data.get("equity", 0)
                starting = 10000.0
                if equity > 0:
                    unrealised_pnl = round(equity - starting, 2)
        except (json.JSONDecodeError, TypeError):
            pass
        results.append(("System_Health", [
            ts, "", "", positions_open,
            round(data.get("equity", 0), 2),
            round(unrealised_pnl, 2), "", "simulation", "", "",
        ]))
        return results

    # --- SystemReady -> System_Health heartbeat ---
    if "SystemReady" in payload:
        data = payload["SystemReady"]
        results.append(("System_Health", [
            ts, 0, 0, data.get("positions_reconciled", 0),
            "", 0, "INIT", "simulation", 0, 0,
        ]))
        return results

    # --- OuroborosChange -> Ouroboros_Changes + Config_Diff_Log (N4a) ---
    if "OuroborosChange" in payload:
        data = payload["OuroborosChange"]
        param = data.get("parameter", "")
        old_val = str(data.get("old_value", ""))
        new_val = str(data.get("new_value", ""))
        reason = data.get("reason", "")
        results.append(("Ouroboros_Changes", [ts, param, old_val, new_val, reason]))
        results.append(("Config_Diff_Log", [
            ts, "OuroborosChange", "", f"{param}: {reason}", old_val, new_val,
        ]))
        return results

    # --- RungAdvanced -> Config_Diff_Log (N4a) ---
    if "RungAdvanced" in payload:
        data = payload["RungAdvanced"]
        symbol = data.get("symbol", _resolve_ticker(data.get("ticker_id", 0)))
        old_rung = data.get("old_rung", data.get("previous_rung", ""))
        new_rung = data.get("new_rung", data.get("rung", ""))
        results.append(("Config_Diff_Log", [
            ts, "RungAdvanced", symbol, f"Rung {old_rung} → {new_rung}",
            str(old_rung), str(new_rung),
        ]))
        return results

    return results


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

                    # Route to tab(s) — N4a: one event can produce multiple rows
                    routed = _route_event(event)
                    for tab_name, row in routed:
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

        wrote_any = False
        routed = _route_event(event)
        for tab_name, row in routed:
            if self._sheets.append_rows(tab_name, [row]):
                wrote_any = True

        pos_rows = _route_snapshot_positions(event)
        if pos_rows is not None:
            if self._sheets.clear_and_write("Open_Positions", pos_rows):
                wrote_any = True

        return wrote_any


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

            routed = _route_event(event)
            for tab_name, row in routed:
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

            routed = _route_event(event)
            if routed:
                hashes_to_mark.append(h)
            for tab_name, row in routed:
                batches.setdefault(tab_name, []).append(row)

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


# ---------------------------------------------------------------------------
# N4a: Nightly data push functions (called by nightly_v6.py)
# ---------------------------------------------------------------------------

def push_nightly_trade_classes(class_report: Dict[str, Any], date_str: str = "") -> bool:
    """Push trade class statistics to Trade_Classes tab (nightly).

    Args:
        class_report: Dict from trade_taxonomy.build_class_report(),
                      keyed by trade_class -> TradeClassStats or dict.
        date_str: Date string (YYYY-MM-DD). Defaults to today UTC.
    """
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    rows = []
    for tc, stats in sorted(class_report.items()):
        if hasattr(stats, "count"):
            # TradeClassStats dataclass
            rows.append([
                date_str, tc, stats.count, stats.wins, stats.losses,
                round(stats.win_rate, 4), round(stats.total_pnl, 4),
                round(stats.avg_pnl, 4), round(stats.avg_hold_mins, 1),
                round(stats.avg_rung, 2),
            ])
        else:
            # Dict format
            rows.append([
                date_str, tc, stats.get("count", 0), stats.get("wins", 0),
                stats.get("losses", 0), round(stats.get("win_rate", 0), 4),
                round(stats.get("total_pnl", 0), 4), round(stats.get("avg_pnl", 0), 4),
                round(stats.get("avg_hold_mins", 0), 1), round(stats.get("avg_rung", 0), 2),
            ])

    if not rows:
        return True
    return _push_nightly_rows("Trade_Classes", rows)


def push_nightly_ticker_scoreboard(scoreboard: List[Dict[str, Any]], date_str: str = "") -> bool:
    """Push ticker scoreboard to Ticker_Scoreboard tab (nightly).

    Args:
        scoreboard: List of dicts with: symbol, score, verdict, trades, win_rate, etc.
        date_str: Date string. Defaults to today UTC.
    """
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    rows = []
    for entry in scoreboard:
        rows.append([
            date_str,
            entry.get("symbol", ""),
            round(entry.get("score", 0), 1),
            entry.get("verdict", ""),
            entry.get("trades", 0),
            round(entry.get("win_rate", 0), 4),
            round(entry.get("profit_factor", 0), 2),
            round(entry.get("avg_rung", 0), 2),
            round(entry.get("avg_spread_cost", 0), 4),
        ])

    if not rows:
        return True
    return _push_nightly_rows("Ticker_Scoreboard", rows)


def push_nightly_parameter_evolution(params: List[Dict[str, Any]], date_str: str = "") -> bool:
    """Push parameter evolution to Parameter_Evolution tab (nightly).

    Args:
        params: List of dicts with: parameter, value, previous_value, change_pct.
        date_str: Date string. Defaults to today UTC.
    """
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    rows = []
    for p in params:
        rows.append([
            date_str,
            p.get("parameter", ""),
            p.get("value", ""),
            p.get("previous_value", ""),
            round(p.get("change_pct", 0), 2),
        ])

    if not rows:
        return True
    return _push_nightly_rows("Parameter_Evolution", rows)


def push_indicator_intelligence_to_sheets(intel_result) -> bool:
    """Push indicator intelligence analysis results to multiple Sheets tabs.

    Args:
        intel_result: IndicatorIntelligence instance with to_sheets_rows() method.
    """
    try:
        sheet_rows = intel_result.to_sheets_rows()
    except Exception as e:
        log.error("Failed to convert indicator intelligence to sheets rows: %s", e)
        return False

    success = True
    for tab_name, rows in sheet_rows.items():
        if rows:
            if not _push_nightly_rows(tab_name, rows):
                success = False
    return success


def _push_nightly_rows(tab_name: str, rows: List[List[Any]]) -> bool:
    """Helper: push rows to a Sheets tab using a fresh SheetsClient.

    Used by nightly push functions. Opens a new connection each time
    (nightly jobs are infrequent, no need for persistent connection).
    """
    sa_path = _find_service_account_path()
    if sa_path is None:
        log.warning("Sheets nightly push skipped: no service account found")
        return False

    try:
        sheets = SheetsClient(sa_path)
        ok = sheets.append_rows(tab_name, rows)
        if ok:
            log.info("Pushed %d rows to %s", len(rows), tab_name)
        return ok
    except Exception as e:
        log.error("Failed to push nightly data to %s: %s", tab_name, e)
        return False


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
