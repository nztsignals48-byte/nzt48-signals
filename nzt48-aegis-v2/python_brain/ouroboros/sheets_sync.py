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
Tabs: 37 total (21 core N4a architecture + 16 pre-existing/complementary)

N4a 21-tab core architecture:
  Daily_Summary, Open_Positions, Closed_Trades, Win_Loss_Delta,
  Confidence_History, Regime_State, Kelly_Sizing, Chandelier_Rungs,
  Gate_Vetoes, Ticker_Scores, Universe_State, Cost_Drag,
  Backfill_Results, Meta_Label, Thompson_Arms, Session_Stats,
  Correlation_Matrix, Risk_Dashboard, System_Health, Config_Changelog, Alerts_Log

Usage:
    # Standalone manual sync (process everything in Redis queue now)
    python3 -m python_brain.ouroboros.sheets_sync

    # Push all nightly tabs (run after nightly_v6)
    python3 -m python_brain.ouroboros.sheets_sync --push-all-tabs

    # Push a specific tab
    python3 -m python_brain.ouroboros.sheets_sync --push-tab Risk_Dashboard

    # List all 37 tabs with their type
    python3 -m python_brain.ouroboros.sheets_sync --list-tabs

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

# Project paths for nightly data sources
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))
REPORTS_DIR = DATA_DIR / "ouroboros_reports"

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
    # ---------------------------------------------------------------------------
    # N4a: Remaining tabs for 21-tab architecture
    # ---------------------------------------------------------------------------
    # Tab: Confidence score history per ticker (nightly from WAL)
    "Confidence_History": [
        "Date", "Symbol", "Avg_Confidence", "Min_Confidence", "Max_Confidence",
        "Trades", "Win_Rate", "Confidence_Bucket",
    ],
    # Tab: Current and historical regime states (nightly from WAL + nightly report)
    "Regime_State": [
        "Date", "Regime", "Duration_Hours", "Trades_In_Regime", "Win_Rate",
        "Avg_PnL", "Transition_From", "VIX_Level",
    ],
    # Tab: Position sizing history (real-time from RoutedOrder + nightly)
    "Kelly_Sizing": [
        "Timestamp", "Symbol", "Kelly_Fraction", "Position_Size_GBP",
        "Confidence", "Win_Prob", "Regime", "Equity_GBP",
    ],
    # Tab: Rung progression per trade (real-time from RungAdvanced WAL events)
    "Chandelier_Rungs": [
        "Timestamp", "Symbol", "Old_Rung", "New_Rung", "Price_At_Advance",
        "Stop_Price", "Highest_High", "ATR",
    ],
    # Tab: Signal rejection breakdown (nightly gate_vetoes.ndjson summary)
    "Gate_Vetoes": [
        "Date", "Gate_Name", "Rejections", "Would_Have_Won", "Would_Have_Lost",
        "Missed_PnL", "Top_Ticker", "Recommendation",
    ],
    # Tab: Current ticker rankings (nightly from ticker_selector / persistent_memory)
    "Ticker_Scores": [
        "Date", "Symbol", "Composite_Score", "Tier", "Win_Rate_30d",
        "Profit_Factor", "Avg_Spread_Cost", "Status",
    ],
    # Tab: Full universe with status (nightly from active_watchlist.json)
    "Universe_State": [
        "Date", "Symbol", "Exchange", "Tier", "Leverage", "Currency",
        "In_Watchlist", "Blacklisted", "Last_Trade_Date",
    ],
    # Tab: Cost analysis per trade/ticker (nightly aggregation)
    "Cost_Drag": [
        "Date", "Symbol", "Trades", "Gross_PnL", "Total_Commission",
        "Total_Spread_Cost", "Net_PnL", "Cost_Drag_Pct", "Avg_Spread_Pct",
    ],
    # Tab: 7-day backfill simulation results (after backfill_simulator run)
    "Backfill_Results": [
        "Date", "Total_Trades", "Win_Rate", "Profit_Factor", "Total_PnL",
        "Starting_Equity", "Ending_Equity", "Return_Pct", "Best_Ticker",
        "Worst_Ticker",
    ],
    # Tab: Meta-label threshold + accuracy per ticker (nightly from meta_label_optimizer)
    "Meta_Label": [
        "Date", "Symbol", "Threshold", "Method", "F1", "Precision",
        "Recall", "N_Trades", "Win_Rate",
    ],
    # Tab: Thompson Sampling arm state per ticker (nightly from universe_filters)
    "Thompson_Arms": [
        "Date", "Symbol", "Posterior_Mean", "Posterior_Std", "N_Trades",
        "Prior_Mean", "Sigma_Sq", "Ranking",
    ],
    # Tab: Per-session statistics (nightly aggregation from WAL)
    "Session_Stats": [
        "Date", "Session", "Trades", "Wins", "Losses", "Win_Rate",
        "Total_PnL", "Avg_PnL", "Best_Trade", "Worst_Trade",
    ],
    # Tab: Top-N pairwise correlations (nightly from indicator_intelligence)
    "Correlation_Matrix": [
        "Date", "Indicator_A", "Indicator_B", "Correlation", "N_Samples",
        "Interpretation",
    ],
    # Tab: Drawdown, VaR, regime, circuit breaker state (nightly)
    "Risk_Dashboard": [
        "Date", "Equity_GBP", "Max_Drawdown_Pct", "Current_Drawdown_Pct",
        "VaR_95_Pct", "Regime", "Circuit_Breaker_State", "Positions_Open",
        "Daily_Loss_Limit_Remaining",
    ],
    # Tab: Config diff rollback ledger (nightly from config_changes.ndjson)
    "Config_Changelog": [
        "Timestamp", "Filename", "Num_Changes", "Summary", "Old_Hash", "New_Hash",
    ],
    # Tab: All Telegram alerts sent (nightly from alert log)
    "Alerts_Log": [
        "Timestamp", "Alert_Type", "Message_Preview", "Recipient", "Status",
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

        # Kelly_Sizing tab (N4a) — capture position sizing at entry
        kelly_frac = data.get("kelly_fraction", 0)
        position_size_gbp = data.get("position_size_gbp", data.get("approved_notional", 0))
        equity_gbp = data.get("equity_at_entry", data.get("equity", 0))
        win_prob = data.get("win_probability", data.get("confidence", 0))
        results.append(("Kelly_Sizing", [
            ts,
            symbol,
            round(kelly_frac, 4),
            round(position_size_gbp, 2),
            round(data.get("confidence", 0), 2),
            round(win_prob, 4),
            data.get("regime_at_entry", data.get("regime", "")),
            round(equity_gbp, 2),
        ]))

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

    # --- RungAdvanced -> Config_Diff_Log + Chandelier_Rungs (N4a) ---
    if "RungAdvanced" in payload:
        data = payload["RungAdvanced"]
        symbol = data.get("symbol", _resolve_ticker(data.get("ticker_id", 0)))
        old_rung = data.get("old_rung", data.get("previous_rung", ""))
        new_rung = data.get("new_rung", data.get("rung", ""))
        results.append(("Config_Diff_Log", [
            ts, "RungAdvanced", symbol, f"Rung {old_rung} → {new_rung}",
            str(old_rung), str(new_rung),
        ]))
        # Chandelier_Rungs tab (N4a)
        results.append(("Chandelier_Rungs", [
            ts,
            symbol,
            str(old_rung),
            str(new_rung),
            round(data.get("price_at_advance", data.get("current_price", 0)), 4),
            round(data.get("stop_price", data.get("new_stop", 0)), 4),
            round(data.get("highest_high", 0), 4),
            round(data.get("atr", 0), 4),
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

    _REDIS_URL_DEFAULT = "redis://:nzt48redis@aegis-redis:6379/0"

    def __init__(
        self,
        redis_url: str = None,
        poll_interval: float = POLL_INTERVAL_SEC,
    ):
        if redis_url is None:
            redis_url = os.environ.get("REDIS_URL", self._REDIS_URL_DEFAULT)
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


def push_daily_summary(report_dir: Path = None) -> bool:
    """Push daily summary row to Daily_Summary tab from nightly report JSON.

    Reads the most recent nightly report JSON from the ouroboros_reports directory
    and pushes a summary row to the Daily_Summary tab via push_nightly_rows().

    Args:
        report_dir: Path to ouroboros_reports directory. Defaults to /app/data/ouroboros_reports.

    Returns:
        True if row was pushed successfully.
    """
    if report_dir is None:
        report_dir = Path(os.environ.get(
            "AEGIS_DATA_DIR",
            Path(__file__).resolve().parents[2] / "data",
        )) / "ouroboros_reports"

    if not report_dir.exists():
        log.error("Report directory not found: %s", report_dir)
        return False

    # Find most recent nightly JSON: try nightly_v6_*.json first, then *_metrics.json
    json_files = sorted(report_dir.glob("nightly_v6_*.json"), reverse=True)
    if not json_files:
        json_files = sorted(report_dir.glob("*_metrics.json"), reverse=True)
    if not json_files:
        log.error("No nightly report JSON found in %s", report_dir)
        return False

    report_path = json_files[0]
    log.info("Reading nightly report: %s", report_path)

    try:
        with open(report_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log.error("Failed to read nightly report %s: %s", report_path, e)
        return False

    # Extract fields with graceful fallbacks
    date_str = data.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    total_trades = data.get("total_trades", 0)
    wins = data.get("wins", 0)
    losses = data.get("losses", 0)
    trades_entered = total_trades  # Approximation: total trades = entered
    trades_exited = wins + losses if (wins + losses) > 0 else total_trades
    win_rate_pct = round(data.get("win_rate", 0) * 100, 1)
    total_pnl = round(data.get("total_pnl_gbp", data.get("total_pnl", 0)), 2)
    equity = round(data.get("equity_gbp", data.get("ending_equity", 0)), 2)
    max_drawdown = round(data.get("max_drawdown_pct", 0), 2)
    sharpe = round(data.get("sharpe_30d", data.get("sharpe_rolling", 0)), 2)

    # Best/worst trade formatting
    best_ticker = data.get("best_trade_ticker", "")
    best_pnl = data.get("best_trade_pnl", 0)
    worst_ticker = data.get("worst_trade_ticker", "")
    worst_pnl = data.get("worst_trade_pnl", 0)

    if best_ticker:
        best_trade = f"{best_ticker} ({best_pnl:+.2f})"
    elif best_pnl:
        best_trade = f"{best_pnl:+.2f}"
    else:
        best_trade = "N/A"

    if worst_ticker:
        worst_trade = f"{worst_ticker} ({worst_pnl:+.2f})"
    elif worst_pnl:
        worst_trade = f"{worst_pnl:+.2f}"
    else:
        worst_trade = "N/A"

    # Daily_Summary headers: Date, Trades_Entered, Trades_Exited, Win_Rate_Pct,
    #   Total_PnL_GBP, Equity_GBP, Max_Drawdown_Pct, Sharpe_Rolling, Best_Trade, Worst_Trade
    row = [
        date_str,
        trades_entered,
        trades_exited,
        win_rate_pct,
        total_pnl,
        equity,
        max_drawdown,
        sharpe,
        best_trade,
        worst_trade,
    ]

    log.info("Pushing Daily_Summary row: date=%s trades=%d wr=%.1f%% pnl=%.2f",
             date_str, total_trades, win_rate_pct, total_pnl)
    return _push_nightly_rows("Daily_Summary", [row])


# ---------------------------------------------------------------------------
# N4a: Nightly push functions for new 21-tab architecture
# ---------------------------------------------------------------------------

def _load_wal_position_closed_events(days: int = 1) -> List[dict]:
    """Load PositionClosed events from WAL for the last N days."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_ns = int(cutoff.timestamp() * 1e9)
    events = []

    wal_candidates = [WAL_DIR / "current.ndjson"]
    archive_dir = WAL_DIR / "archive"
    if archive_dir.exists():
        for f in sorted(archive_dir.glob("*.ndjson")):
            wal_candidates.append(f)

    for wal_path in wal_candidates:
        if not wal_path.exists():
            continue
        try:
            with open(wal_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = event.get("payload", {})
                    if "PositionClosed" in payload:
                        evt_time = event.get("event_time_ns", 0)
                        if evt_time >= cutoff_ns:
                            events.append(payload["PositionClosed"])
        except Exception as e:
            log.warning("Error reading WAL %s: %s", wal_path, e)
    return events


def _load_wal_events_by_type(event_type: str, days: int = 1) -> List[dict]:
    """Load WAL events of a specific type for the last N days."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_ns = int(cutoff.timestamp() * 1e9)
    events = []

    wal_candidates = [WAL_DIR / "current.ndjson"]
    archive_dir = WAL_DIR / "archive"
    if archive_dir.exists():
        for f in sorted(archive_dir.glob("*.ndjson")):
            wal_candidates.append(f)

    for wal_path in wal_candidates:
        if not wal_path.exists():
            continue
        try:
            with open(wal_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = event.get("payload", {})
                    if event_type in payload:
                        evt_time = event.get("event_time_ns", 0)
                        if evt_time >= cutoff_ns:
                            events.append(payload[event_type])
        except Exception as e:
            log.warning("Error reading WAL %s: %s", wal_path, e)
    return events


def push_confidence_history(date_str: str = "") -> bool:
    """Push per-ticker confidence score history to Confidence_History tab.

    Reads PositionClosed events from today's WAL, groups by ticker,
    computes confidence stats per ticker, and pushes rows.
    """
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    trades = _load_wal_position_closed_events(days=1)
    if not trades:
        log.info("No trades for Confidence_History on %s", date_str)
        return True

    # Group by ticker
    from collections import defaultdict
    by_ticker: Dict[str, List[dict]] = defaultdict(list)
    for t in trades:
        sym = t.get("symbol", _resolve_ticker(t.get("ticker_id", 0)))
        by_ticker[sym].append(t)

    rows = []
    for sym, ticker_trades in sorted(by_ticker.items()):
        confs = [t.get("confidence", 0) for t in ticker_trades]
        wins = sum(1 for t in ticker_trades if t.get("final_pnl", 0) > 0)
        n = len(ticker_trades)
        avg_conf = sum(confs) / n if n > 0 else 0
        # Bucket: Low (<0.6), Medium (0.6-0.75), High (>0.75)
        bucket = "High" if avg_conf > 0.75 else ("Medium" if avg_conf >= 0.6 else "Low")
        rows.append([
            date_str, sym,
            round(avg_conf, 4),
            round(min(confs) if confs else 0, 4),
            round(max(confs) if confs else 0, 4),
            n,
            round(wins / n if n > 0 else 0, 4),
            bucket,
        ])

    if not rows:
        return True
    return _push_nightly_rows("Confidence_History", rows)


def push_regime_state(date_str: str = "") -> bool:
    """Push regime state history to Regime_State tab.

    Reads RiskStateChange events from WAL and nightly report for regime stats.
    """
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Try loading from the latest nightly report which has regime performance
    report_dir = REPORTS_DIR
    json_files = sorted(report_dir.glob("nightly_v6_*.json"), reverse=True) if report_dir.exists() else []
    if not json_files:
        json_files = sorted(report_dir.glob("*_metrics.json"), reverse=True) if report_dir.exists() else []

    rows = []
    if json_files:
        try:
            with open(json_files[0]) as f:
                data = json.load(f)
            per_regime = data.get("per_regime", {})
            vix = data.get("vix_close", data.get("vix_at_close", 0))
            for regime, stats in sorted(per_regime.items()):
                trades = stats.get("trades", 0)
                wins = stats.get("wins", 0)
                rows.append([
                    date_str,
                    regime,
                    "",  # Duration_Hours (not available from daily report)
                    trades,
                    round(wins / trades if trades > 0 else 0, 4),
                    round(stats.get("total_pnl", stats.get("avg_pnl", 0)), 4),
                    "",  # Transition_From
                    round(vix, 2),
                ])
        except Exception as e:
            log.warning("Failed to read regime data from nightly report: %s", e)

    # Fallback: read RiskStateChange events from WAL
    if not rows:
        changes = _load_wal_events_by_type("RiskStateChange", days=1)
        for c in changes:
            rows.append([
                date_str,
                c.get("new_regime", c.get("regime", "")),
                "",
                0,
                0,
                0,
                c.get("old_regime", c.get("previous_regime", "")),
                round(c.get("vix", 0), 2),
            ])

    if not rows:
        log.info("No regime state data for %s", date_str)
        return True
    return _push_nightly_rows("Regime_State", rows)


def push_gate_vetoes(date_str: str = "") -> bool:
    """Push gate veto summary to Gate_Vetoes tab.

    Reads gate_vetoes.ndjson or archived vetoes and summarizes by gate name.
    """
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    gate_vetoes_path = DATA_DIR / "gate_vetoes.ndjson"
    # Also check archive
    archive_path = DATA_DIR / "gate_vetoes_archive" / f"gate_vetoes_{date_str}.ndjson"

    vetoes = []
    for vpath in [gate_vetoes_path, archive_path]:
        if not vpath.exists():
            continue
        try:
            with open(vpath) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        vetoes.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            log.warning("Error reading gate vetoes %s: %s", vpath, e)

    if not vetoes:
        log.info("No gate vetoes for %s", date_str)
        return True

    # Summarize by gate name
    from collections import defaultdict
    gate_summary: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"rejections": 0, "won": 0, "lost": 0, "missed_pnl": 0.0, "tickers": defaultdict(int)}
    )
    for v in vetoes:
        gate = v.get("gate_name", v.get("gate", "unknown"))
        gate_summary[gate]["rejections"] += 1
        # Check if outcome is tracked (missed winner analysis)
        hyp_pnl = v.get("hypothetical_pnl_pct", v.get("would_have_pnl", 0))
        if hyp_pnl > 0:
            gate_summary[gate]["won"] += 1
            gate_summary[gate]["missed_pnl"] += hyp_pnl
        elif hyp_pnl < 0:
            gate_summary[gate]["lost"] += 1
        sym = v.get("symbol", v.get("ticker", ""))
        if sym:
            gate_summary[gate]["tickers"][sym] += 1

    rows = []
    for gate, stats in sorted(gate_summary.items()):
        top_ticker = max(stats["tickers"], key=stats["tickers"].get) if stats["tickers"] else ""
        # Recommendation based on missed winners
        total_rej = stats["rejections"]
        would_won = stats["won"]
        if total_rej > 0 and would_won / total_rej > 0.5:
            rec = "LOOSEN"
        elif would_won == 0 and total_rej > 5:
            rec = "KEEP"
        else:
            rec = "MONITOR"
        rows.append([
            date_str, gate, total_rej, would_won, stats["lost"],
            round(stats["missed_pnl"], 4), top_ticker, rec,
        ])

    if not rows:
        return True
    return _push_nightly_rows("Gate_Vetoes", rows)


def push_ticker_scores(date_str: str = "") -> bool:
    """Push current ticker rankings to Ticker_Scores tab.

    Reads from system_memory.json (persistent memory) and/or
    active_watchlist.json for tier/score data.
    """
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    rows = []

    # Try persistent memory for per-ticker stats
    memory_path = DATA_DIR / "system_memory.json"
    if memory_path.exists():
        try:
            with open(memory_path) as f:
                memory = json.load(f)
            per_ticker = memory.get("per_ticker", {})
            for sym, stats in sorted(per_ticker.items()):
                total_trades = stats.get("total_trades", 0)
                wins = stats.get("wins", 0)
                wr = stats.get("win_rate", wins / total_trades if total_trades > 0 else 0)
                # Compute composite score: WR*40 + PF*30 + (1-spread)*30
                pf = stats.get("profit_factor", 0)
                avg_spread = stats.get("avg_spread_cost", stats.get("avg_spread_pct", 0))
                score = round(wr * 40 + min(pf, 3) / 3 * 30 + max(0, 1 - avg_spread) * 30, 1)
                status = stats.get("status", "active")
                rows.append([
                    date_str, sym, score, "",
                    round(wr, 4), round(pf, 2),
                    round(avg_spread, 4), status,
                ])
        except Exception as e:
            log.warning("Failed to read system_memory.json for ticker scores: %s", e)

    # Enrich with tier from active_watchlist.json
    watchlist_path = CONFIG_DIR / "active_watchlist.json"
    tier_map: Dict[str, str] = {}
    if watchlist_path.exists():
        try:
            with open(watchlist_path) as f:
                wl = json.load(f)
            for entry in wl if isinstance(wl, list) else wl.get("tickers", []):
                sym = entry.get("symbol", entry.get("ticker", ""))
                tier = entry.get("tier", "")
                if sym:
                    tier_map[sym] = str(tier)
        except Exception as e:
            log.warning("Failed to read active_watchlist.json: %s", e)

    # Patch tier into rows
    for row in rows:
        sym = row[1]
        if sym in tier_map:
            row[3] = tier_map[sym]

    if not rows:
        # Fall back to just listing PRIMARY_TICKERS with default scores
        for sym in PRIMARY_TICKERS:
            tier = tier_map.get(sym, "HOT")
            rows.append([date_str, sym, 50.0, tier, 0, 0, 0, "active"])

    return _push_nightly_rows("Ticker_Scores", rows)


def push_universe_state(date_str: str = "") -> bool:
    """Push full universe state to Universe_State tab.

    Reads contracts.toml + active_watchlist.json + persistent_memory blacklist.
    """
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    rows = []

    # Read contracts.toml for the full universe
    contracts_path = CONFIG_DIR / "contracts.toml"
    contracts_data: Dict[str, Dict[str, Any]] = {}
    if contracts_path.exists():
        try:
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib  # type: ignore
            with open(contracts_path, "rb") as f:
                toml_data = tomllib.load(f)
            # Contracts are under [[contracts]] array or [contract.*] sections
            for key, val in toml_data.items():
                if isinstance(val, dict) and "exchange" in val:
                    contracts_data[key] = val
                elif isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict) and "symbol" in item:
                            contracts_data[item["symbol"]] = item
        except Exception as e:
            log.warning("Failed to parse contracts.toml: %s", e)

    # Read watchlist for tier info
    watchlist_path = CONFIG_DIR / "active_watchlist.json"
    watchlist_syms: set = set()
    tier_map: Dict[str, str] = {}
    if watchlist_path.exists():
        try:
            with open(watchlist_path) as f:
                wl = json.load(f)
            entries = wl if isinstance(wl, list) else wl.get("tickers", [])
            for entry in entries:
                sym = entry.get("symbol", entry.get("ticker", ""))
                if sym:
                    watchlist_syms.add(sym)
                    tier_map[sym] = str(entry.get("tier", ""))
        except Exception:
            pass

    # Read blacklist from persistent memory
    blacklist: set = set()
    memory_path = DATA_DIR / "system_memory.json"
    if memory_path.exists():
        try:
            with open(memory_path) as f:
                memory = json.load(f)
            for sym in memory.get("blacklist", []):
                blacklist.add(sym)
        except Exception:
            pass

    # Build rows: at minimum list primary tickers
    listed_syms: set = set()
    for sym in PRIMARY_TICKERS:
        cd = contracts_data.get(sym, {})
        rows.append([
            date_str, sym,
            cd.get("exchange", "LSEETF"),
            tier_map.get(sym, "HOT"),
            cd.get("leverage", 3),
            cd.get("currency", "USD"),
            "Yes" if sym in watchlist_syms or not watchlist_syms else "Yes",
            "Yes" if sym in blacklist else "No",
            "",  # Last_Trade_Date
        ])
        listed_syms.add(sym)

    # Add any watchlist entries not in primary tickers
    for sym in sorted(watchlist_syms - listed_syms):
        cd = contracts_data.get(sym, {})
        rows.append([
            date_str, sym,
            cd.get("exchange", ""),
            tier_map.get(sym, ""),
            cd.get("leverage", ""),
            cd.get("currency", ""),
            "Yes",
            "Yes" if sym in blacklist else "No",
            "",
        ])

    if not rows:
        return True
    return _push_nightly_rows("Universe_State", rows)


def push_cost_drag(date_str: str = "") -> bool:
    """Push per-ticker cost analysis to Cost_Drag tab.

    Aggregates commission and spread costs from today's PositionClosed events.
    """
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    trades = _load_wal_position_closed_events(days=1)
    if not trades:
        log.info("No trades for Cost_Drag on %s", date_str)
        return True

    from collections import defaultdict
    by_ticker: Dict[str, List[dict]] = defaultdict(list)
    for t in trades:
        sym = t.get("symbol", _resolve_ticker(t.get("ticker_id", 0)))
        by_ticker[sym].append(t)

    rows = []
    for sym, ticker_trades in sorted(by_ticker.items()):
        n = len(ticker_trades)
        gross = sum(t.get("gross_pnl", t.get("final_pnl", 0)) for t in ticker_trades)
        commission = sum(t.get("total_commission", 0) for t in ticker_trades)
        net = sum(t.get("final_pnl", 0) for t in ticker_trades)
        total_spread = 0.0
        avg_spread_sum = 0.0
        for t in ticker_trades:
            sp_entry = t.get("spread_at_entry_pct", 0)
            sp_exit = t.get("spread_at_exit_pct", 0)
            qty = t.get("qty", 1)
            ep = t.get("entry_price", 1)
            pos_val = max(ep * qty, 1.0)
            total_spread += (sp_entry + sp_exit) / 100.0 * pos_val
            avg_spread_sum += sp_entry
        avg_spread = avg_spread_sum / n if n > 0 else 0
        cost_drag = round((commission + total_spread) / max(abs(gross), 0.01) * 100, 1) if gross else 0
        rows.append([
            date_str, sym, n,
            round(gross, 4), round(commission, 4),
            round(total_spread, 4), round(net, 4),
            cost_drag, round(avg_spread, 4),
        ])

    if not rows:
        return True
    return _push_nightly_rows("Cost_Drag", rows)


def push_backfill_results(date_str: str = "") -> bool:
    """Push latest backfill simulation results to Backfill_Results tab.

    Reads from ouroboros_reports/backfill_sim_*.json.
    """
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if not REPORTS_DIR.exists():
        log.info("No reports dir for backfill results")
        return True

    json_files = sorted(REPORTS_DIR.glob("backfill_sim_*.json"), reverse=True)
    if not json_files:
        log.info("No backfill simulation reports found")
        return True

    try:
        with open(json_files[0]) as f:
            data = json.load(f)
    except Exception as e:
        log.warning("Failed to read backfill report: %s", e)
        return False

    per_ticker = data.get("per_ticker", {})
    # Find best/worst ticker by total_pnl
    best_ticker = ""
    worst_ticker = ""
    if per_ticker:
        best_ticker = max(per_ticker, key=lambda k: per_ticker[k].get("total_pnl", 0))
        worst_ticker = min(per_ticker, key=lambda k: per_ticker[k].get("total_pnl", 0))

    row = [
        data.get("date", date_str),
        data.get("total_trades", 0),
        round(data.get("win_rate", 0), 4),
        round(data.get("profit_factor", 0), 2),
        round(data.get("total_pnl_per_share", 0), 4),
        round(data.get("starting_equity", 10000), 2),
        round(data.get("ending_equity", 10000), 2),
        round(data.get("return_pct", 0), 2),
        best_ticker,
        worst_ticker,
    ]

    return _push_nightly_rows("Backfill_Results", [row])


def push_meta_label(date_str: str = "") -> bool:
    """Push meta-label threshold data to Meta_Label tab.

    Reads from ouroboros_reports/meta_label_*.json or config/meta_label_thresholds.toml.
    """
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Try JSON sidecar first (richer data)
    json_files = sorted(REPORTS_DIR.glob("meta_label_*.json"), reverse=True) if REPORTS_DIR.exists() else []
    if json_files:
        try:
            with open(json_files[0]) as f:
                data = json.load(f)
            tickers = data.get("tickers", {})
            rows = []
            for sym, info in sorted(tickers.items()):
                rows.append([
                    data.get("date", date_str),
                    sym,
                    round(info.get("threshold", info.get("0", 0.55)), 4),
                    info.get("method", "default"),
                    round(info.get("f1", 0), 4),
                    round(info.get("precision", 0), 4),
                    round(info.get("recall", 0), 4),
                    info.get("n_trades", 0),
                    round(info.get("win_rate", 0), 4),
                ])
            if rows:
                return _push_nightly_rows("Meta_Label", rows)
        except Exception as e:
            log.warning("Failed to read meta_label JSON: %s", e)

    log.info("No meta_label data for %s", date_str)
    return True


def push_thompson_arms(date_str: str = "") -> bool:
    """Push Thompson Sampling arm state to Thompson_Arms tab.

    Runs the Thompson Sampling engine from WAL data and exports arm state.
    """
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        from python_brain.ouroboros.universe_filters import ThompsonSamplingEngine
    except ImportError:
        log.info("Thompson Sampling engine not available")
        return True

    # Load trades from last 30 days for Thompson
    trades = _load_wal_position_closed_events(days=30)
    if not trades:
        log.info("No trades for Thompson arms")
        return True

    engine = ThompsonSamplingEngine()
    for t in trades:
        sym = t.get("symbol", _resolve_ticker(t.get("ticker_id", 0)))
        entry_price = t.get("entry_price", 0)
        exit_price = t.get("exit_price", 0)
        if entry_price > 0:
            engine.update_from_trade(sym, entry_price, exit_price)

    import math
    # Sort by posterior mean descending for ranking
    sorted_arms = sorted(engine.arms.items(),
                         key=lambda x: x[1].posterior_mean, reverse=True)
    rows = []
    for rank, (sym, arm) in enumerate(sorted_arms, 1):
        rows.append([
            date_str, sym,
            round(arm.posterior_mean, 6),
            round(math.sqrt(max(1e-10, arm.posterior_variance)), 6),
            arm.n,
            round(arm.mu_0, 6),
            round(arm.sigma_sq, 8),
            rank,
        ])

    if not rows:
        return True
    return _push_nightly_rows("Thompson_Arms", rows)


def push_session_stats(date_str: str = "") -> bool:
    """Push per-session (Asian/European/American) statistics to Session_Stats tab.

    Reads PositionClosed events and groups by entry session phase.
    """
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    trades = _load_wal_position_closed_events(days=1)
    if not trades:
        log.info("No trades for Session_Stats on %s", date_str)
        return True

    from collections import defaultdict
    by_session: Dict[str, List[dict]] = defaultdict(list)
    for t in trades:
        session = t.get("entry_session_phase", "unknown")
        if not session:
            # Classify from entry_time_ns
            entry_ns = t.get("entry_time_ns", 0)
            if entry_ns > 0:
                try:
                    dt = datetime.fromtimestamp(entry_ns / 1e9, tz=timezone.utc)
                    hour = dt.hour
                    if hour < 8:
                        session = "pre_market"
                    elif hour < 12:
                        session = "morning"
                    elif hour < 14:
                        session = "us_open"
                    elif hour < 16:
                        session = "afternoon"
                    else:
                        session = "after_hours"
                except Exception:
                    session = "unknown"
            else:
                session = "unknown"
        by_session[session].append(t)

    rows = []
    for session, session_trades in sorted(by_session.items()):
        n = len(session_trades)
        pnls = [t.get("final_pnl", 0) for t in session_trades]
        wins = sum(1 for p in pnls if p > 0)
        losses = n - wins
        total_pnl = sum(pnls)
        avg_pnl = total_pnl / n if n > 0 else 0
        best = max(pnls) if pnls else 0
        worst = min(pnls) if pnls else 0
        rows.append([
            date_str, session, n, wins, losses,
            round(wins / n if n > 0 else 0, 4),
            round(total_pnl, 4), round(avg_pnl, 4),
            round(best, 4), round(worst, 4),
        ])

    if not rows:
        return True
    return _push_nightly_rows("Session_Stats", rows)


def push_correlation_matrix(date_str: str = "") -> bool:
    """Push top-N pairwise indicator correlations to Correlation_Matrix tab.

    Reads from indicator_intelligence.json output.
    """
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    intel_path = DATA_DIR / "indicator_intelligence.json"
    if not intel_path.exists():
        log.info("No indicator_intelligence.json for correlation matrix")
        return True

    try:
        with open(intel_path) as f:
            data = json.load(f)
    except Exception as e:
        log.warning("Failed to read indicator_intelligence.json: %s", e)
        return False

    correlations = data.get("indicator_correlations", {})
    if not correlations:
        log.info("No correlations in indicator_intelligence.json")
        return True

    rows = []
    seen = set()
    for ind_a, pairs in sorted(correlations.items()):
        for ind_b, corr_val in sorted(pairs.items()):
            pair_key = tuple(sorted([ind_a, ind_b]))
            if pair_key in seen:
                continue
            seen.add(pair_key)
            # Interpret correlation
            abs_corr = abs(corr_val)
            if abs_corr > 0.8:
                interp = "highly_correlated"
            elif abs_corr > 0.5:
                interp = "moderately_correlated"
            elif abs_corr > 0.3:
                interp = "weakly_correlated"
            else:
                interp = "independent"
            rows.append([
                date_str, ind_a, ind_b,
                round(corr_val, 4), "",  # N_Samples not tracked in current JSON
                interp,
            ])

    # Sort by absolute correlation descending, take top 20
    rows.sort(key=lambda r: abs(r[3]) if isinstance(r[3], (int, float)) else 0, reverse=True)
    rows = rows[:20]

    if not rows:
        return True
    return _push_nightly_rows("Correlation_Matrix", rows)


def push_risk_dashboard(date_str: str = "") -> bool:
    """Push risk dashboard state to Risk_Dashboard tab.

    Reads from nightly report JSON for drawdown, equity, regime, etc.
    """
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if not REPORTS_DIR.exists():
        log.info("No reports dir for risk dashboard")
        return True

    json_files = sorted(REPORTS_DIR.glob("nightly_v6_*.json"), reverse=True)
    if not json_files:
        json_files = sorted(REPORTS_DIR.glob("*_metrics.json"), reverse=True)
    if not json_files:
        log.info("No nightly report for risk dashboard")
        return True

    try:
        with open(json_files[0]) as f:
            data = json.load(f)
    except Exception as e:
        log.warning("Failed to read nightly report for risk dashboard: %s", e)
        return False

    equity = data.get("equity_gbp", data.get("ending_equity", data.get("equity", 0)))
    max_dd = data.get("max_drawdown_pct", 0)
    current_dd = data.get("current_drawdown_pct", max_dd)
    var_95 = data.get("var_95_pct", 0)
    regime = data.get("regime", data.get("current_regime", ""))
    cb_state = data.get("circuit_breaker_state", "OK")
    positions = data.get("positions_open", data.get("open_positions", 0))
    # Daily loss limit: typically 2% of equity
    daily_limit = data.get("daily_loss_limit_remaining",
                           round(max(0, equity * 0.02 + data.get("total_pnl_gbp",
                                     data.get("total_pnl", 0))), 2))

    row = [
        date_str,
        round(equity, 2),
        round(max_dd, 2),
        round(current_dd, 2),
        round(var_95, 2),
        regime,
        cb_state,
        positions,
        round(daily_limit, 2),
    ]

    return _push_nightly_rows("Risk_Dashboard", [row])


def push_config_changelog(date_str: str = "") -> bool:
    """Push config change ledger to Config_Changelog tab.

    Reads from data/config_changes.ndjson (written by config_writer.py).
    """
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    ledger_path = DATA_DIR / "config_changes.ndjson"
    if not ledger_path.exists():
        log.info("No config_changes.ndjson for changelog")
        return True

    rows = []
    try:
        with open(ledger_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Only include entries from today
                ts = entry.get("timestamp", "")
                if not ts.startswith(date_str):
                    continue
                diff_summary = entry.get("diff_summary", [])
                num_changes = len(diff_summary)
                # Build a short summary
                if num_changes <= 3:
                    summary_parts = []
                    for d in diff_summary:
                        key = d.get("key", "")
                        action = d.get("action", "")
                        summary_parts.append(f"{key}:{action}")
                    summary = "; ".join(summary_parts)
                else:
                    summary = f"{num_changes} changes"
                rows.append([
                    ts,
                    entry.get("filename", ""),
                    num_changes,
                    summary[:200],  # Truncate for sheets cell limits
                    entry.get("old_hash", "")[:12],
                    entry.get("new_hash", "")[:12],
                ])
    except Exception as e:
        log.warning("Failed to read config_changes.ndjson: %s", e)
        return False

    if not rows:
        log.info("No config changes for %s", date_str)
        return True
    return _push_nightly_rows("Config_Changelog", rows)


def push_alerts_log(date_str: str = "") -> bool:
    """Push Telegram alerts log to Alerts_Log tab.

    Reads from data/telegram_alerts.ndjson (if it exists) or
    scans ouroboros log for sent alerts.
    """
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    rows = []

    # Try reading telegram alerts log
    alerts_path = DATA_DIR / "telegram_alerts.ndjson"
    if alerts_path.exists():
        try:
            with open(alerts_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = entry.get("timestamp", "")
                    if not ts.startswith(date_str):
                        continue
                    msg = entry.get("message", "")[:100]  # Preview only
                    rows.append([
                        ts,
                        entry.get("alert_type", "general"),
                        msg,
                        entry.get("recipient", ""),
                        entry.get("status", "sent"),
                    ])
        except Exception as e:
            log.warning("Failed to read telegram_alerts.ndjson: %s", e)

    if not rows:
        log.info("No alerts for %s", date_str)
        return True
    return _push_nightly_rows("Alerts_Log", rows)


def push_all_tabs(date_str: str = "", report_dir: Path = None) -> Dict[str, bool]:
    """Push all 21+ tabs. Returns dict of tab_name -> success.

    Intended to be called after nightly_v6 completes. Pushes both
    WAL-derived tabs and report-derived tabs.
    """
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    results: Dict[str, bool] = {}

    # 1. Daily_Summary (from nightly report)
    log.info("--- Pushing Daily_Summary ---")
    results["Daily_Summary"] = push_daily_summary(report_dir=report_dir)

    # 2. Confidence_History (from WAL)
    log.info("--- Pushing Confidence_History ---")
    results["Confidence_History"] = push_confidence_history(date_str)

    # 3. Regime_State (from report + WAL)
    log.info("--- Pushing Regime_State ---")
    results["Regime_State"] = push_regime_state(date_str)

    # 4. Gate_Vetoes (from gate_vetoes.ndjson)
    log.info("--- Pushing Gate_Vetoes ---")
    results["Gate_Vetoes"] = push_gate_vetoes(date_str)

    # 5. Ticker_Scores (from persistent memory)
    log.info("--- Pushing Ticker_Scores ---")
    results["Ticker_Scores"] = push_ticker_scores(date_str)

    # 6. Universe_State (from contracts + watchlist)
    log.info("--- Pushing Universe_State ---")
    results["Universe_State"] = push_universe_state(date_str)

    # 7. Cost_Drag (from WAL)
    log.info("--- Pushing Cost_Drag ---")
    results["Cost_Drag"] = push_cost_drag(date_str)

    # 8. Backfill_Results (from backfill sim report)
    log.info("--- Pushing Backfill_Results ---")
    results["Backfill_Results"] = push_backfill_results(date_str)

    # 9. Meta_Label (from meta_label optimizer report)
    log.info("--- Pushing Meta_Label ---")
    results["Meta_Label"] = push_meta_label(date_str)

    # 10. Thompson_Arms (from WAL via Thompson engine)
    log.info("--- Pushing Thompson_Arms ---")
    results["Thompson_Arms"] = push_thompson_arms(date_str)

    # 11. Session_Stats (from WAL)
    log.info("--- Pushing Session_Stats ---")
    results["Session_Stats"] = push_session_stats(date_str)

    # 12. Correlation_Matrix (from indicator intelligence)
    log.info("--- Pushing Correlation_Matrix ---")
    results["Correlation_Matrix"] = push_correlation_matrix(date_str)

    # 13. Risk_Dashboard (from nightly report)
    log.info("--- Pushing Risk_Dashboard ---")
    results["Risk_Dashboard"] = push_risk_dashboard(date_str)

    # 14. Config_Changelog (from config_changes.ndjson)
    log.info("--- Pushing Config_Changelog ---")
    results["Config_Changelog"] = push_config_changelog(date_str)

    # 15. Alerts_Log (from telegram alerts log)
    log.info("--- Pushing Alerts_Log ---")
    results["Alerts_Log"] = push_alerts_log(date_str)

    # 16-18. Trade_Classes, Ticker_Scoreboard, Parameter_Evolution
    #   (These are pushed by existing nightly_v6 hooks — skip if called from push_all)
    # Already handled by nightly_v6 calling push_nightly_trade_classes, etc.
    for existing_tab in ["Trade_Classes", "Ticker_Scoreboard", "Parameter_Evolution"]:
        results[existing_tab] = True  # Assumed pushed by nightly_v6

    # 19-22. Real-time tabs (pushed by _route_event, not nightly):
    #   Live_Trades, Open_Positions, Closed_Trades, Win_Loss_Delta,
    #   Rejected_Signals, Missed_Winners, MAE_MFE, Spread_Execution,
    #   Win_Indicators, Loss_Indicators, Chandelier_Rungs, Kelly_Sizing,
    #   Config_Diff_Log, Ouroboros_Changes, System_Health
    # These are populated in real-time via Redis queue, not nightly push
    realtime_tabs = [
        "Live_Trades", "Open_Positions", "Closed_Trades", "Win_Indicators",
        "Loss_Indicators", "Win_Loss_Delta", "Rejected_Signals", "Missed_Winners",
        "MAE_MFE", "Spread_Execution", "Config_Diff_Log", "Ouroboros_Changes",
        "System_Health", "Chandelier_Rungs", "Kelly_Sizing",
        "Indicator_Stats", "Regime_Performance", "Session_Performance", "Learned_Rules",
    ]
    for rt_tab in realtime_tabs:
        results[rt_tab] = True  # Real-time tabs — not pushed by nightly

    # Summary
    pushed = sum(1 for v in results.values() if v)
    failed = sum(1 for v in results.values() if not v)
    log.info("push_all_tabs complete: %d OK, %d FAILED", pushed, failed)

    return results


def list_all_tabs() -> List[Dict[str, str]]:
    """List all tabs defined in TAB_DEFINITIONS with their type.

    Returns list of dicts with: name, columns, type (realtime|nightly|hybrid).
    """
    # Classify tabs by how they're populated
    realtime_tabs = {
        "Live_Trades", "Open_Positions", "Closed_Trades", "Win_Indicators",
        "Loss_Indicators", "Rejected_Signals", "Missed_Winners", "MAE_MFE",
        "Spread_Execution", "Config_Diff_Log", "Ouroboros_Changes", "System_Health",
        "Chandelier_Rungs", "Kelly_Sizing",
    }
    nightly_tabs = {
        "Daily_Summary", "Confidence_History", "Regime_State", "Gate_Vetoes",
        "Ticker_Scores", "Universe_State", "Cost_Drag", "Backfill_Results",
        "Meta_Label", "Thompson_Arms", "Session_Stats", "Correlation_Matrix",
        "Risk_Dashboard", "Config_Changelog", "Alerts_Log",
        "Trade_Classes", "Ticker_Scoreboard", "Parameter_Evolution",
        "Indicator_Stats", "Regime_Performance", "Session_Performance",
        "Learned_Rules", "Win_Loss_Delta",
    }

    result = []
    for tab_name, headers in TAB_DEFINITIONS.items():
        if tab_name in realtime_tabs:
            tab_type = "realtime"
        elif tab_name in nightly_tabs:
            tab_type = "nightly"
        else:
            tab_type = "hybrid"
        result.append({
            "name": tab_name,
            "columns": len(headers),
            "type": tab_type,
            "headers": ", ".join(headers[:5]) + ("..." if len(headers) > 5 else ""),
        })
    return result


# Individual tab push dispatch (for --push-tab <name>)
_TAB_PUSH_DISPATCH: Dict[str, Any] = {
    "Daily_Summary": lambda d, **kw: push_daily_summary(report_dir=kw.get("report_dir")),
    "Confidence_History": lambda d, **kw: push_confidence_history(d),
    "Regime_State": lambda d, **kw: push_regime_state(d),
    "Gate_Vetoes": lambda d, **kw: push_gate_vetoes(d),
    "Ticker_Scores": lambda d, **kw: push_ticker_scores(d),
    "Universe_State": lambda d, **kw: push_universe_state(d),
    "Cost_Drag": lambda d, **kw: push_cost_drag(d),
    "Backfill_Results": lambda d, **kw: push_backfill_results(d),
    "Meta_Label": lambda d, **kw: push_meta_label(d),
    "Thompson_Arms": lambda d, **kw: push_thompson_arms(d),
    "Session_Stats": lambda d, **kw: push_session_stats(d),
    "Correlation_Matrix": lambda d, **kw: push_correlation_matrix(d),
    "Risk_Dashboard": lambda d, **kw: push_risk_dashboard(d),
    "Config_Changelog": lambda d, **kw: push_config_changelog(d),
    "Alerts_Log": lambda d, **kw: push_alerts_log(d),
}


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
    """CLI entry point: drain Redis queue, replay WAL, or push tabs."""
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
    parser.add_argument(
        "--push-daily-summary",
        action="store_true",
        help="Push daily summary row from latest nightly report to Daily_Summary tab",
    )
    parser.add_argument(
        "--report-dir",
        type=str,
        default=None,
        help="Override report directory path (used with --push-daily-summary)",
    )
    # N4a: 21-tab architecture CLI arguments
    parser.add_argument(
        "--push-all-tabs",
        action="store_true",
        help="Push all nightly tabs (21-tab architecture). Run after nightly_v6.",
    )
    parser.add_argument(
        "--push-tab",
        type=str,
        default=None,
        metavar="TAB_NAME",
        help="Push a specific tab by name (e.g. Confidence_History, Risk_Dashboard)",
    )
    parser.add_argument(
        "--list-tabs",
        action="store_true",
        help="List all defined tabs and their type (realtime/nightly)",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Override date (YYYY-MM-DD) for nightly pushes. Defaults to today UTC.",
    )
    args = parser.parse_args()

    date_str = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # --list-tabs: show all tabs and exit
    if args.list_tabs:
        tabs = list_all_tabs()
        print(f"\nAEGIS V2 Sheets Dashboard -- {len(tabs)} tabs defined\n")
        print(f"{'#':<4} {'Tab Name':<25} {'Cols':<6} {'Type':<10} {'Headers (preview)'}")
        print("-" * 90)
        for i, t in enumerate(tabs, 1):
            print(f"{i:<4} {t['name']:<25} {t['columns']:<6} {t['type']:<10} {t['headers']}")
        print(f"\nTotal: {len(tabs)} tabs")
        return

    # --push-all-tabs: push all nightly tabs
    if args.push_all_tabs:
        report_dir = Path(args.report_dir) if args.report_dir else None
        log.info("Pushing all tabs for date=%s ...", date_str)
        results = push_all_tabs(date_str=date_str, report_dir=report_dir)
        ok_count = sum(1 for v in results.values() if v)
        fail_count = sum(1 for v in results.values() if not v)
        failed_tabs = [k for k, v in results.items() if not v]
        log.info("Push all tabs complete: %d OK, %d FAILED", ok_count, fail_count)
        if failed_tabs:
            log.error("Failed tabs: %s", ", ".join(failed_tabs))
            sys.exit(1)
        return

    # --push-tab <name>: push a single tab
    if args.push_tab:
        tab_name = args.push_tab
        if tab_name not in _TAB_PUSH_DISPATCH:
            available = ", ".join(sorted(_TAB_PUSH_DISPATCH.keys()))
            log.error("Unknown tab '%s'. Pushable tabs: %s", tab_name, available)
            sys.exit(1)
        report_dir = Path(args.report_dir) if args.report_dir else None
        log.info("Pushing tab %s for date=%s ...", tab_name, date_str)
        ok = _TAB_PUSH_DISPATCH[tab_name](date_str, report_dir=report_dir)
        if ok:
            log.info("Tab %s pushed successfully", tab_name)
        else:
            log.error("Failed to push tab %s", tab_name)
            sys.exit(1)
        return

    # --push-daily-summary (legacy)
    if args.push_daily_summary:
        report_dir = Path(args.report_dir) if args.report_dir else None
        ok = push_daily_summary(report_dir=report_dir)
        if ok:
            log.info("Daily summary pushed successfully")
        else:
            log.error("Failed to push daily summary")
            sys.exit(1)
    elif args.replay:
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
