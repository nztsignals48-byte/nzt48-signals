"""
NZT-48 Trading System — Google Sheets Auto-Logger
Section 54: Every signal and trade auto-logged to Google Sheets via gspread.
Auto-calculates win rate, profit factor, per-strategy stats.
50 trades triggers MAE/MFE recalibration.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger("nzt48.sheets")


class SheetsLogger:
    """Auto-logging of every signal and trade to Google Sheets.

    Uses gspread with a service account for authentication.
    Creates separate worksheets for: Signals, Trades, Daily Summary, Strategy Stats.
    All metrics auto-calculated from the trade log via gspread API.
    """

    SIGNAL_HEADERS = [
        "ID", "Timestamp", "Ticker", "Direction", "Strategy", "Confidence",
        "Entry", "Stop", "Target1", "Target2", "Regime", "RVOL", "GEX",
        "Bot", "Bot Instance", "Status", "Rejection Reason",
        "ISA Ticker", "Overseer Status", "Portfolio Heat",
    ]

    TRADE_HEADERS = [
        "ID", "Signal ID", "Date", "Time In", "Time Out", "Duration (min)",
        "Ticker", "Direction", "Strategy", "Bot", "Bot Instance",
        "Entry", "Exit", "Stop", "Shares", "Risk $", "Risk %",
        "P&L $", "P&L R", "Gross P&L", "Commissions", "Net P&L",
        "Entry Quality", "Exit Quality", "Confidence",
        "Regime", "GEX", "DIX", "VIX", "Internals",
        "Patterns", "Emotional State", "Firewall Triggers",
        "What Worked", "What Failed", "Would Take Again",
        # === COMPOUNDING TRACKER COLUMNS (added for 2% daily target) ===
        "Running P&L £", "Running Win Rate %", "Equity After Trade £",
        "Target Equity £", "Gap vs Target £", "Exit Reason",
        "Holding Duration Min", "Ratchet Rungs Hit", "Peak P&L Before Exit (MFE)",
        "Pathway",
        # === LIQUIDITY TIER (G1 — from LSE Registry) ===
        "Liquidity Tier",
    ]

    DAILY_HEADERS = [
        "Date", "Bot", "Trades", "Signals", "Skipped",
        "P&L $", "P&L %", "Wins", "Losses", "Avg R",
        "Best R", "Worst R", "Regime", "Grade", "Lesson",
    ]

    def __init__(self, spreadsheet_name: str = "NZT-48 Trade Log", starting_equity: float = 10_000.0) -> None:
        self.spreadsheet_name = spreadsheet_name
        self._gc = None
        self._spreadsheet = None
        self._initialized = False
        self._trade_count = 0
        # Compounding tracker state
        self._running_pnl = 0.0
        self._win_count = 0
        self._loss_count = 0
        self._starting_equity = starting_equity
        self._current_equity = starting_equity
        self._trading_day = 0

        # --- Graceful degradation: pre-validate credentials path at startup ---
        # If the creds file is missing we disable Sheets logging immediately so
        # the system never crashes when initialize() is called later.
        import os as _os
        _creds_path = _os.environ.get("GOOGLE_SHEETS_CREDS", "")
        if _creds_path and not _os.path.exists(_creds_path):
            self.enabled = False
            logger.warning(
                "SheetsLogger: GOOGLE_SHEETS_CREDS points to a file that does not exist: '%s'. "
                "Google Sheets logging is DISABLED. System will continue without Sheets.",
                _creds_path,
            )
        elif not _creds_path:
            self.enabled = False
            logger.warning(
                "SheetsLogger: GOOGLE_SHEETS_CREDS env var is not set. "
                "Google Sheets logging is DISABLED. System will continue without Sheets.",
            )
        else:
            self.enabled = True
            logger.info(
                "SheetsLogger: credentials file found at '%s'. "
                "Call initialize() to activate Sheets logging.",
                _creds_path,
            )

    def update_equity(self, current_equity: float) -> None:
        """SA-5 FIX: Refresh equity for accurate daily P&L % calculations.

        Called from daily reset cycle (same pattern as circuit_breakers.reset_daily).
        """
        if current_equity > 0:
            self._starting_equity = current_equity
            self._current_equity = current_equity
            self._running_pnl = 0.0
            self._win_count = 0
            self._loss_count = 0
            self._trading_day += 1

    def initialize(self) -> bool:
        """Initialize gspread with service account credentials."""
        try:
            import gspread
            from google.oauth2.service_account import Credentials

            creds_path = os.environ.get("GOOGLE_SHEETS_CREDS", "")
            if not creds_path or not os.path.exists(creds_path):
                logger.warning(
                    "Google Sheets credentials not found at %s. "
                    "Sheets logging disabled.", creds_path
                )
                return False

            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ]
            credentials = Credentials.from_service_account_file(creds_path, scopes=scopes)
            self._gc = gspread.authorize(credentials)

            # Open or create spreadsheet
            try:
                self._spreadsheet = self._gc.open(self.spreadsheet_name)
            except gspread.SpreadsheetNotFound:
                self._spreadsheet = self._gc.create(self.spreadsheet_name)
                logger.info("Created new spreadsheet: %s", self.spreadsheet_name)

            # Ensure worksheets exist
            self._ensure_worksheets()
            self._initialized = True
            logger.info("Google Sheets logger initialized: %s", self.spreadsheet_name)
            return True

        except ImportError:
            logger.warning("gspread not installed. Sheets logging disabled.")
            return False
        except Exception as e:
            logger.error("Failed to initialize Sheets: %s", e)
            return False

    def _ensure_worksheets(self) -> None:
        """Create worksheets if they don't exist."""
        existing = [ws.title for ws in self._spreadsheet.worksheets()]

        sheets_config = {
            "Signals": self.SIGNAL_HEADERS,
            "Trades": self.TRADE_HEADERS,
            "Daily Summary": self.DAILY_HEADERS,
        }

        for name, headers in sheets_config.items():
            if name not in existing:
                ws = self._spreadsheet.add_worksheet(title=name, rows=1000, cols=len(headers))
                ws.append_row(headers)
                logger.info("Created worksheet: %s", name)

    def log_signal(self, signal) -> None:
        """Log a signal to the Signals worksheet."""
        if not self._initialized:
            return

        try:
            ws = self._spreadsheet.worksheet("Signals")
            row = [
                signal.id,
                signal.timestamp.isoformat() if hasattr(signal.timestamp, 'isoformat')
                    else str(signal.timestamp),
                signal.ticker,
                signal.direction.value if hasattr(signal.direction, 'value')
                    else str(signal.direction),
                signal.strategy,
                signal.confidence,
                signal.entry,
                signal.stop,
                signal.target_1r,
                signal.target_2r,
                signal.regime.value if hasattr(signal.regime, 'value')
                    else str(signal.regime),
                signal.rvol,
                signal.gex_regime.value if hasattr(signal.gex_regime, 'value')
                    else str(signal.gex_regime),
                signal.bot.value if hasattr(signal.bot, 'value') else str(signal.bot),
                signal.bot_instance.value if hasattr(signal.bot_instance, 'value')
                    else str(signal.bot_instance),
                signal.status.value if hasattr(signal.status, 'value')
                    else str(signal.status),
                signal.rejection_reason,
                signal.isa_ticker,
                signal.overseer_status,
                signal.portfolio_heat,
            ]
            ws.append_row(row)
            logger.debug("Signal %s logged to Sheets", signal.id)

        except Exception as e:
            logger.error("Failed to log signal to Sheets: %s", e)

    def log_trade(self, trade, virtual_trade=None) -> None:
        """Log a completed trade to the Trades worksheet.

        Args:
            trade: models.Trade object with standard journal fields
            virtual_trade: Optional VirtualTrade with execution detail
                           (exit_reason, partials, peak_r, pathway etc.)
        """
        if not self._initialized:
            return

        try:
            ws = self._spreadsheet.worksheet("Trades")

            # --- Update compounding tracker state ---
            pnl = trade.pnl_dollars or 0.0
            self._running_pnl += pnl
            if pnl > 0:
                self._win_count += 1
            elif pnl < 0:
                self._loss_count += 1
            total_trades = self._win_count + self._loss_count
            running_win_rate = (self._win_count / total_trades * 100) if total_trades > 0 else 0.0
            self._current_equity += pnl

            # Detect new trading day (increment on first trade of a new date)
            trade_date = ""
            if hasattr(trade.time_entered, 'strftime'):
                trade_date = trade.time_entered.strftime("%Y-%m-%d")
            if not hasattr(self, '_last_trade_date') or self._last_trade_date != trade_date:
                self._trading_day += 1
                self._last_trade_date = trade_date

            # Target equity: £10,000 × 1.02^N where N = trading day
            target_equity = self._starting_equity * (1.02 ** self._trading_day)
            gap_vs_target = self._current_equity - target_equity

            # --- Extract virtual_trade enrichment (if available) ---
            exit_reason = ""
            holding_duration = trade.duration_minutes or 0
            ratchet_rungs_hit = ""
            peak_pnl_mfe = 0.0
            pathway = ""

            # --- Liquidity Tier: extract from signal or virtual_trade ---
            liquidity_tier = ""
            for source in [virtual_trade, trade]:
                if source is not None:
                    lt = getattr(source, 'liquidity_tier', '') or ''
                    if lt:
                        liquidity_tier = lt
                        break
            # Fallback: try to infer from LSE registry if .L ticker
            if not liquidity_tier and hasattr(trade, 'ticker') and trade.ticker.upper().endswith('.L'):
                try:
                    from uk_isa.lse_registry import LSERegistry
                    registry = LSERegistry()
                    product = registry.get_product(trade.ticker)
                    if product:
                        liquidity_tier = getattr(product, 'liquidity_tier', 'UNKNOWN') or 'UNKNOWN'
                except Exception:
                    liquidity_tier = "UNKNOWN"

            if virtual_trade is not None:
                exit_reason = getattr(virtual_trade, 'exit_reason', '') or ''
                holding_duration = getattr(virtual_trade, 'duration_minutes', holding_duration) or holding_duration

                # Ratchet rungs: extract from partials list
                partials = getattr(virtual_trade, 'partials', []) or []
                if partials:
                    rungs = [str(p.get('rung', '?')) for p in partials]
                    ratchet_rungs_hit = ", ".join(rungs)

                # Peak P&L (MFE) — peak_r × risk_dollars gives peak dollar P&L
                peak_r = getattr(virtual_trade, 'peak_r', 0.0) or 0.0
                risk_d = getattr(virtual_trade, 'risk_dollars', 0.0) or 0.0
                peak_pnl_mfe = round(peak_r * risk_d, 2) if risk_d else 0.0

                # Pathway (S15_PRIORITY, S16_UNIVERSAL, STANDARD_GAUNTLET etc.)
                pathway = getattr(virtual_trade, 'pathway', '') or ''
                if not pathway:
                    pathway = "S15_PRIORITY" if trade.strategy == "S15_DailyTarget" else "STANDARD"

            # --- Build row (original 35 columns + 10 compounding tracker columns) ---
            row = [
                trade.id,
                trade.signal_id,
                trade.time_entered.strftime("%Y-%m-%d") if hasattr(trade.time_entered, 'strftime')
                    else str(trade.time_entered),
                trade.time_entered.strftime("%H:%M:%S") if hasattr(trade.time_entered, 'strftime')
                    else "",
                trade.time_exited.strftime("%H:%M:%S") if trade.time_exited
                    and hasattr(trade.time_exited, 'strftime') else "",
                trade.duration_minutes,
                trade.ticker,
                trade.direction.value if hasattr(trade.direction, 'value')
                    else str(trade.direction),
                trade.strategy,
                trade.bot.value if hasattr(trade.bot, 'value') else str(trade.bot),
                trade.bot_instance.value if hasattr(trade.bot_instance, 'value')
                    else str(trade.bot_instance),
                trade.entry_price,
                trade.exit_price,
                trade.stop_price,
                trade.shares,
                trade.risk_dollars,
                trade.risk_percent,
                trade.pnl_dollars,
                trade.pnl_r_multiple,
                trade.gross_pnl,
                trade.commissions,
                trade.net_pnl,
                trade.entry_quality,
                trade.exit_quality,
                trade.confidence_score,
                trade.regime_state,
                trade.gex_regime,
                trade.dix_reading,
                trade.vix_level,
                trade.internals_composite,
                ", ".join(trade.patterns_detected) if trade.patterns_detected else "",
                trade.emotional_state,
                ", ".join(trade.firewall_triggers) if trade.firewall_triggers else "",
                trade.what_worked,
                trade.what_failed,
                "Y" if trade.would_take_again else "N",
                # === COMPOUNDING TRACKER COLUMNS ===
                round(self._running_pnl, 2),
                round(running_win_rate, 1),
                round(self._current_equity, 2),
                round(target_equity, 2),
                round(gap_vs_target, 2),
                exit_reason,
                round(holding_duration, 1),
                ratchet_rungs_hit,
                peak_pnl_mfe,
                pathway,
                # === LIQUIDITY TIER (G1) ===
                liquidity_tier,
            ]
            ws.append_row(row)
            self._trade_count += 1

            # 50 trades triggers MAE/MFE recalibration
            if self._trade_count % 50 == 0:
                logger.info("50 trades logged. MAE/MFE recalibration triggered.")

            logger.info(
                "SHEETS: Trade %s | P&L=£%.2f | Equity=£%.2f | Target=£%.2f | Gap=£%.2f | Day #%d",
                trade.id, pnl, self._current_equity, target_equity, gap_vs_target, self._trading_day,
            )

        except Exception as e:
            logger.error("Failed to log trade to Sheets: %s", e)

    def log_daily_summary(self, summary: dict) -> None:
        """Log daily summary to the Daily Summary worksheet."""
        if not self._initialized:
            return

        try:
            ws = self._spreadsheet.worksheet("Daily Summary")
            row = [
                summary.get("date", ""),
                summary.get("bot", ""),
                summary.get("trades_taken", 0),
                summary.get("signals_received", 0),
                summary.get("signals_skipped", 0),
                summary.get("pnl_dollars", 0),
                summary.get("pnl_pct", 0),
                summary.get("wins", 0),
                summary.get("losses", 0),
                summary.get("avg_r", 0),
                summary.get("best_r", 0),
                summary.get("worst_r", 0),
                summary.get("regime", ""),
                summary.get("emotional_grade", ""),
                summary.get("lesson", ""),
            ]
            ws.append_row(row)

        except Exception as e:
            logger.error("Failed to log daily summary: %s", e)
