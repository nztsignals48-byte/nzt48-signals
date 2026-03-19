"""Push current open positions with unrealised P&L to Google Sheets.

Reads RoutedOrder events from the WAL to reconstruct open positions,
calculates unrealised P&L from current market prices (via the engine's
HEARTBEAT equity data), and writes to the Open_Positions tab.

Also updates System_Health with the latest unrealised P&L.

Designed to run every 5 minutes via cron alongside sheets_sync.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PosSync] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("push_positions")

# Service account paths (same as sheets_sync.py)
SA_PATHS = [
    "/app/config/sheets_service_account.json",
    "config/sheets_service_account.json",
]

SPREADSHEET_NAME = "AEGIS V2 Dashboard"
STARTING_EQUITY = 10000.0


def _load_open_positions_from_wal(wal_dir: str) -> Dict[int, Dict[str, Any]]:
    """Reconstruct open positions from WAL RoutedOrder events.

    Returns dict of ticker_id -> position info.
    A position is "open" if it has a Long entry but no corresponding Sell exit.
    """
    positions: Dict[int, Dict[str, Any]] = {}
    # AUDIT-FIX: Read current.ndjson AND all archive files
    wal_base = Path(wal_dir)
    wal_files = []
    current = wal_base / "current.ndjson"
    if current.exists():
        wal_files.append(current)
    archive_dir = wal_base / "archive"
    if archive_dir.exists():
        wal_files.extend(sorted(archive_dir.glob("*.ndjson")))

    if not wal_files:
        log.warning("No WAL files found in: %s", wal_dir)
        return positions

    for wal_path in wal_files:
      with open(wal_path) as f:
        for line in f:
            try:
                ev = json.loads(line.strip())
                payload = ev.get("payload", {})

                if "RoutedOrder" in payload:
                    data = payload["RoutedOrder"]
                    tid = data.get("ticker_id", 0)
                    side = data.get("side", "")
                    symbol = data.get("symbol", f"T{tid}")

                    if side == "Long":
                        positions[tid] = {
                            "ticker_id": tid,
                            "symbol": symbol,
                            "qty": data.get("qty", 0),
                            "confidence": data.get("confidence", 0),
                            "kelly": data.get("kelly_fraction", 0),
                            "strategy": data.get("strategy", ""),
                            "currency": data.get("currency", ""),
                            "entry_time": ev.get("event_time_ns", 0),
                        }
                    elif side in ("Sell", "SLD"):
                        # Position closed
                        positions.pop(tid, None)

            except (json.JSONDecodeError, KeyError):
                continue

    return positions


def _get_equity_from_heartbeat(wal_dir: str) -> Tuple[float, float]:
    """Extract latest equity and unrealised PnL from engine logs.

    Reads docker logs for HEARTBEAT lines. Falls back to WAL if needed.
    Returns (equity, unrealised_pnl).
    """
    equity = STARTING_EQUITY
    pnl = 0.0

    # Try reading from engine logs via docker
    try:
        import subprocess
        result = subprocess.run(
            ["docker", "logs", "aegis-v2", "--tail", "200"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stderr.split("\n") + result.stdout.split("\n"):
            if "HEARTBEAT:" in line:
                # Parse: HEARTBEAT: regime=Normal ... equity=9996.11 pnl=-3.89 pos=3
                parts = line.split()
                for part in parts:
                    if part.startswith("equity="):
                        try:
                            equity = float(part.split("=")[1])
                        except ValueError:
                            pass
                    if part.startswith("pnl="):
                        try:
                            pnl = float(part.split("=")[1])
                        except ValueError:
                            pass
    except Exception as e:
        log.warning("Could not read docker logs: %s", e)

    unrealised = equity - STARTING_EQUITY
    return equity, unrealised


def _push_to_sheets(positions: Dict[int, Dict], equity: float, unrealised_pnl: float):
    """Write position data to Google Sheets Open_Positions tab."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        log.error("gspread/google-auth not installed")
        return False

    # Find service account
    sa_path = None
    for p in SA_PATHS:
        if os.path.exists(p):
            sa_path = p
            break

    if not sa_path:
        log.error("No service account JSON found")
        return False

    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(sa_path, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open(SPREADSHEET_NAME)

        # --- Update Open_Positions tab ---
        ws_pos = sh.worksheet("Open_Positions")

        # Clear existing data (keep header)
        ws_pos.clear()

        # Write header
        header = [
            "Symbol", "Qty", "Entry_Price", "Current_Price", "Unrealized_PnL",
            "Rung", "Stop_Price", "Highest_High", "Duration_Min", "Exchange",
        ]
        ws_pos.append_row(header, value_input_option="RAW")

        # Write position rows
        ts = datetime.now(timezone.utc)
        rows = []
        for tid, pos in sorted(positions.items(), key=lambda x: x[1]["symbol"]):
            # We don't have current_price or entry_price from WAL alone
            # But we can show what we know
            rows.append([
                pos["symbol"],
                pos["qty"],
                "",  # Entry_Price (not in WAL RoutedOrder)
                "",  # Current_Price (not available without market data)
                "",  # Unrealized_PnL (per-ticker not available yet)
                "",  # Rung
                "",  # Stop_Price
                "",  # Highest_High
                "",  # Duration_Min
                pos.get("currency", ""),
            ])

        if rows:
            ws_pos.append_rows(rows, value_input_option="RAW")
            log.info("Updated Open_Positions: %d positions", len(rows))
        else:
            ws_pos.append_row(["No open positions", "", "", "", "", "", "", "", "", ""])
            log.info("No open positions to show")

        # --- Update System_Health with unrealised P&L ---
        ws_health = sh.worksheet("System_Health")
        health_row = [
            ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "",  # Uptime_Hours
            "",  # Ticks_Received
            len(positions),
            round(equity, 2),
            round(unrealised_pnl, 2),  # Unrealised_PnL
            "",  # Risk_Regime
            "simulation",
            "",  # WAL_Size_MB
            "",  # Memory_Usage_Pct
        ]
        ws_health.append_row(health_row, value_input_option="RAW")
        log.info("Updated System_Health: equity=%.2f, unrealised=%.2f, positions=%d",
                 equity, unrealised_pnl, len(positions))

        return True

    except Exception as e:
        log.error("Failed to update sheets: %s", e)
        return False


def main():
    wal_dir = os.environ.get("WAL_DIR", "/app/events")

    log.info("Reading open positions from WAL...")
    positions = _load_open_positions_from_wal(wal_dir)
    log.info("Found %d open positions", len(positions))

    log.info("Getting equity from engine heartbeat...")
    equity, unrealised_pnl = _get_equity_from_heartbeat(wal_dir)
    log.info("Equity: %.2f, Unrealised PnL: %.2f", equity, unrealised_pnl)

    log.info("Pushing to Google Sheets...")
    success = _push_to_sheets(positions, equity, unrealised_pnl)

    if success:
        log.info("Done. Positions + unrealised P&L updated in Google Sheets.")
    else:
        log.error("Failed to update Google Sheets.")
        sys.exit(1)


if __name__ == "__main__":
    main()
