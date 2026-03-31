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


# ---------------------------------------------------------------------------
# Parquet + Batch Ingest Utilities — Book 63 extensions
# ---------------------------------------------------------------------------


def create_parquet_partitions(conn, table: str, partition_col: str, output_dir: str) -> None:
    """Export a DuckDB table to partitioned Parquet files.

    Args:
        conn: DuckDB connection
        table: table name to export
        partition_col: column to partition by (e.g. 'date', 'strategy')
        output_dir: directory to write partitioned Parquet files
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    conn.execute(f"""
        COPY {table} TO '{output_dir}'
        (FORMAT PARQUET, PARTITION_BY ({partition_col}), OVERWRITE_OR_IGNORE)
    """)
    log.info("Parquet partitions written: %s → %s (partition_by=%s)", table, output_dir, partition_col)


def create_indices(conn, table: str, columns: List[str]) -> None:
    """Create indices on specified columns for query optimization.

    Args:
        conn: DuckDB connection
        table: table name
        columns: list of column names to index
    """
    for col in columns:
        idx_name = f"idx_{table}_{col}"
        try:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({col})")
            log.info("Index created: %s on %s(%s)", idx_name, table, col)
        except Exception as e:
            log.warning("Index creation failed for %s(%s): %s", table, col, e)


def batch_ingest_wal(
    conn,
    wal_dir: str,
    start_date: str,
    end_date: str,
    batch_size: int = 1000,
) -> Dict[str, int]:
    """Batch ingest WAL files from a date range.

    Scans wal_dir for NDJSON files matching YYYY-MM-DD.ndjson within
    [start_date, end_date] inclusive, then bulk inserts into DuckDB.

    Args:
        conn: DuckDB connection (unused directly — delegates to DataWarehouse)
        wal_dir: directory containing WAL NDJSON files
        start_date: inclusive start date (YYYY-MM-DD)
        end_date: inclusive end date (YYYY-MM-DD)
        batch_size: records per INSERT batch (for memory control)

    Returns:
        {"files_processed": N, "total_trades": M, "total_signals": S}
    """
    wal_path = Path(wal_dir)
    if not wal_path.exists():
        log.warning("WAL dir does not exist: %s", wal_dir)
        return {"files_processed": 0, "total_trades": 0, "total_signals": 0}

    files_processed = 0
    total_trades = 0
    total_signals = 0

    # Collect and sort WAL files in date range
    wal_files = sorted(wal_path.glob("*.ndjson"))

    for wal_file in wal_files:
        date_str = wal_file.stem
        # Filter to date range
        if date_str < start_date or date_str > end_date:
            continue

        trades_batch = []
        signals_batch = []

        with open(wal_file) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    evt = json.loads(line)
                    et = evt.get("event_type", "")

                    if et == "PositionClosed":
                        trades_batch.append((
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
                        ))

                    elif et in ("RoutedOrder", "SignalRejected"):
                        signals_batch.append((
                            evt.get("event_id", ""),
                            evt.get("ticker", evt.get("symbol", "")),
                            evt.get("strategy", ""),
                            evt.get("side", "Long"),
                            evt.get("confidence", 0),
                            evt.get("kelly_fraction", 0),
                            et == "RoutedOrder",
                            evt.get("veto_reason", ""),
                            date_str,
                        ))

                    # Flush in batches
                    if len(trades_batch) >= batch_size:
                        _flush_trades(conn, trades_batch)
                        total_trades += len(trades_batch)
                        trades_batch = []

                    if len(signals_batch) >= batch_size:
                        _flush_signals(conn, signals_batch)
                        total_signals += len(signals_batch)
                        signals_batch = []

                except (json.JSONDecodeError, KeyError):
                    continue

        # Flush remaining
        if trades_batch:
            _flush_trades(conn, trades_batch)
            total_trades += len(trades_batch)
        if signals_batch:
            _flush_signals(conn, signals_batch)
            total_signals += len(signals_batch)

        files_processed += 1

    log.info("Batch ingest complete: %d files, %d trades, %d signals (%s to %s)",
             files_processed, total_trades, total_signals, start_date, end_date)
    return {"files_processed": files_processed, "total_trades": total_trades, "total_signals": total_signals}


def _flush_trades(conn, batch: list) -> None:
    """Bulk insert a batch of trade tuples."""
    conn.executemany("""
        INSERT INTO trades (trade_id, ticker, strategy, entry_price, exit_price,
            quantity, pnl, hold_time_mins, entry_confidence, exit_rung, regime, date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, batch)


def _flush_signals(conn, batch: list) -> None:
    """Bulk insert a batch of signal tuples."""
    conn.executemany("""
        INSERT INTO signals (signal_id, ticker, strategy, direction,
            confidence, kelly_fraction, accepted, veto_reason, date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, batch)


def export_parquet(
    conn,
    table: str,
    output_dir: str,
    date_from: str,
    date_to: str,
) -> None:
    """Streaming export of a table slice to Parquet.

    Args:
        conn: DuckDB connection
        table: table name
        output_dir: output directory
        date_from: start date (YYYY-MM-DD)
        date_to: end date (YYYY-MM-DD)
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_file = Path(output_dir) / f"{table}_{date_from}_{date_to}.parquet"
    conn.execute(f"""
        COPY (
            SELECT * FROM {table}
            WHERE date >= '{date_from}' AND date <= '{date_to}'
        ) TO '{output_file}'
        (FORMAT PARQUET, COMPRESSION 'SNAPPY')
    """)
    log.info("Parquet export: %s → %s (%s to %s)", table, output_file, date_from, date_to)


def compute_rolling_feature(
    conn,
    table: str,
    expr: str,
    window: int,
    symbol: str,
) -> list:
    """Compute a rolling window function over a table for a specific symbol.

    Args:
        conn: DuckDB connection
        table: table name (e.g. 'daily_bars')
        expr: SQL expression to compute (e.g. 'AVG(close)', 'STDDEV(close)')
        window: number of rows in the rolling window
        symbol: ticker symbol to filter on

    Returns:
        List of dicts with date and computed rolling value.
    """
    result = conn.execute(f"""
        SELECT
            date,
            {expr} OVER (
                ORDER BY date
                ROWS BETWEEN {window - 1} PRECEDING AND CURRENT ROW
            ) AS rolling_value
        FROM {table}
        WHERE ticker = ?
        ORDER BY date
    """, [symbol]).fetchall()
    columns = [desc[0] for desc in conn.description]
    return [dict(zip(columns, row)) for row in result]
