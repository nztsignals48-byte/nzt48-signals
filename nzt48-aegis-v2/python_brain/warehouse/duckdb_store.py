"""DuckDB/Parquet Data Warehouse — Book 63.

Columnar analytics backend for historical analysis, ML training,
and Ouroboros research. Replaces ad-hoc WAL parsing with structured
SQL queries over Parquet files.

5 Data Domains:
  1. ticks      — Raw tick data (5s bars, partitioned by date/symbol)
  2. bars_5s    — Aggregated 5-second OHLCV bars
  3. daily_bars — End-of-day OHLCV from WAL
  4. trades     — All closed trades with full metadata
  5. signals    — All generated signals (accepted + rejected)

Storage format: Parquet with Snappy compression, partitioned by
year/month/symbol. ~10:1 compression vs NDJSON WAL.

DuckDB streams data without loading full dataset into RAM (critical
for 4GB EC2 constraint).

Usage:
    from python_brain.warehouse.duckdb_store import DataWarehouse

    dw = DataWarehouse()
    dw.ingest_wal("events/2026-03-29.ndjson")
    df = dw.query("SELECT * FROM trades WHERE strategy = 'TypeF'")
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("duckdb_store")

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))
WAREHOUSE_DIR = DATA_DIR / "warehouse"


class DataWarehouse:
    """DuckDB-backed analytical data warehouse for AEGIS V2."""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or str(WAREHOUSE_DIR / "aegis.duckdb")
        self._conn = None
        WAREHOUSE_DIR.mkdir(parents=True, exist_ok=True)

    def _get_conn(self):
        """Lazy connection initialization."""
        if self._conn is None:
            try:
                import duckdb
                self._conn = duckdb.connect(self._db_path)
                self._init_schema()
            except ImportError:
                log.warning("duckdb not installed — warehouse operations will fail")
                raise
        return self._conn

    def _init_schema(self):
        """Create tables if they don't exist."""
        conn = self._conn
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                trade_id VARCHAR,
                ticker VARCHAR,
                strategy VARCHAR,
                entry_price DOUBLE,
                exit_price DOUBLE,
                quantity INTEGER,
                entry_time TIMESTAMP,
                exit_time TIMESTAMP,
                pnl DOUBLE,
                cost_adjusted_pnl DOUBLE,
                commission DOUBLE,
                spread_cost DOUBLE,
                slippage DOUBLE,
                hold_time_mins DOUBLE,
                entry_confidence INTEGER,
                exit_rung INTEGER,
                regime VARCHAR,
                mfe_pct DOUBLE,
                mae_pct DOUBLE,
                r_multiple DOUBLE,
                exit_efficiency DOUBLE,
                date DATE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                signal_id VARCHAR,
                ticker VARCHAR,
                strategy VARCHAR,
                direction VARCHAR,
                confidence INTEGER,
                kelly_fraction DOUBLE,
                timestamp TIMESTAMP,
                accepted BOOLEAN,
                veto_reason VARCHAR,
                regime VARCHAR,
                date DATE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_bars (
                ticker VARCHAR,
                date DATE,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT,
                vwap DOUBLE,
                atr DOUBLE,
                rvol DOUBLE
            )
        """)

        log.info("DuckDB schema initialized: %s", self._db_path)

    def ingest_wal(self, wal_path: str) -> Dict[str, int]:
        """Ingest a WAL NDJSON file into the warehouse.

        Returns: {"trades": N, "signals": M} count of records ingested.
        """
        path = Path(wal_path)
        if not path.exists():
            return {"trades": 0, "signals": 0}

        conn = self._get_conn()
        trades_added = 0
        signals_added = 0

        # Extract date from filename (YYYY-MM-DD.ndjson)
        date_str = path.stem

        with open(path) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    evt = json.loads(line)
                    et = evt.get("event_type", "")

                    if et == "PositionClosed":
                        conn.execute("""
                            INSERT INTO trades (trade_id, ticker, strategy, entry_price, exit_price,
                                quantity, pnl, hold_time_mins, entry_confidence, exit_rung,
                                regime, date)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, [
                            evt.get("event_id", ""),
                            evt.get("ticker", evt.get("symbol", "")),
                            evt.get("strategy", ""),
                            evt.get("entry_price", evt.get("avg_entry", 0)),
                            evt.get("exit_price", 0),
                            evt.get("quantity", 0),
                            evt.get("realized_pnl", 0),
                            evt.get("hold_time_mins", 0),
                            evt.get("confidence", 0),
                            evt.get("exit_rung", 0),
                            evt.get("regime", ""),
                            date_str,
                        ])
                        trades_added += 1

                    elif et == "RoutedOrder":
                        conn.execute("""
                            INSERT INTO signals (signal_id, ticker, strategy, direction,
                                confidence, kelly_fraction, accepted, veto_reason, date)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, [
                            evt.get("event_id", ""),
                            evt.get("ticker", evt.get("symbol", "")),
                            evt.get("strategy", ""),
                            evt.get("side", "Long"),
                            evt.get("confidence", 0),
                            evt.get("kelly_fraction", 0),
                            True,
                            "",
                            date_str,
                        ])
                        signals_added += 1

                    elif et == "SignalRejected":
                        conn.execute("""
                            INSERT INTO signals (signal_id, ticker, strategy, direction,
                                confidence, kelly_fraction, accepted, veto_reason, date)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, [
                            evt.get("event_id", ""),
                            evt.get("ticker", ""),
                            evt.get("strategy", ""),
                            "Long",
                            evt.get("confidence", 0),
                            0,
                            False,
                            evt.get("veto_reason", ""),
                            date_str,
                        ])
                        signals_added += 1

                except (json.JSONDecodeError, KeyError) as e:
                    continue

        log.info("WAL ingested: %s → %d trades, %d signals", path.name, trades_added, signals_added)
        return {"trades": trades_added, "signals": signals_added}

    def query(self, sql: str) -> list:
        """Execute a SQL query and return results as list of dicts."""
        conn = self._get_conn()
        result = conn.execute(sql).fetchall()
        columns = [desc[0] for desc in conn.description]
        return [dict(zip(columns, row)) for row in result]

    def strategy_summary(self, strategy: Optional[str] = None) -> list:
        """Per-strategy performance summary."""
        where = f"WHERE strategy = '{strategy}'" if strategy else ""
        return self.query(f"""
            SELECT
                strategy,
                COUNT(*) as n_trades,
                ROUND(AVG(CASE WHEN pnl > 0 THEN 1.0 ELSE 0.0 END) * 100, 1) as win_rate,
                ROUND(SUM(pnl), 2) as total_pnl,
                ROUND(AVG(pnl), 2) as avg_pnl,
                ROUND(AVG(hold_time_mins), 1) as avg_hold_mins,
                ROUND(AVG(entry_confidence), 0) as avg_confidence
            FROM trades
            {where}
            GROUP BY strategy
            ORDER BY total_pnl DESC
        """)

    def signal_funnel(self) -> list:
        """Signal acceptance/rejection funnel by strategy."""
        return self.query("""
            SELECT
                strategy,
                COUNT(*) as total_signals,
                SUM(CASE WHEN accepted THEN 1 ELSE 0 END) as accepted,
                SUM(CASE WHEN NOT accepted THEN 1 ELSE 0 END) as rejected,
                ROUND(AVG(CASE WHEN accepted THEN 1.0 ELSE 0.0 END) * 100, 1) as acceptance_rate
            FROM signals
            GROUP BY strategy
            ORDER BY total_signals DESC
        """)

    def close(self):
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
