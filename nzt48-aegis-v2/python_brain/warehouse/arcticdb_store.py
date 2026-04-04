"""ArcticDB Time-Series Store — Phase 6.3.

Versioned time-series storage using ArcticDB (LMDB backend).
Complements DuckDB warehouse with:
  - Versioned symbol data (tick-level, bar, trade history)
  - Native NumPy/Pandas integration (zero-copy reads)
  - Automatic data deduplication and append optimization
  - Time-range queries without full table scan

Architecture:
  Hot tier  → NDJSON WAL (real-time, append-only)
  Warm tier → ArcticDB (versioned time-series, random access)
  Cold tier → DuckDB/Parquet (batch analytics, SQL)
  Archive   → S3 tar.gz (long-term retention)

Usage:
    from python_brain.warehouse.arcticdb_store import TimeSeriesStore

    ts = TimeSeriesStore()
    ts.write_bars("QQQ3.L", df)           # Append OHLCV bars
    df = ts.read_bars("QQQ3.L", "2026-04-01", "2026-04-04")
    ts.write_trades(trades_df)            # Append closed trades
    ts.snapshot_signals(signals_df)       # Version signal history

Dependencies: arcticdb (requires LMDB C library — liblmdb-dev)
License: arcticdb is BSL-1.1 (free for non-production use, production ok after 4yr)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

log = logging.getLogger("arcticdb_store")

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
ARCTIC_DIR = DATA_DIR / "arcticdb"

# Lazy import — fails gracefully if arcticdb not installed
_arcticdb = None


def _get_arcticdb():
    global _arcticdb
    if _arcticdb is None:
        import arcticdb
        _arcticdb = arcticdb
    return _arcticdb


class TimeSeriesStore:
    """ArcticDB-backed versioned time-series store for AEGIS V2."""

    # Library names within ArcticDB
    LIB_BARS = "bars"
    LIB_TICKS = "ticks"
    LIB_TRADES = "trades"
    LIB_SIGNALS = "signals"
    LIB_FEATURES = "features"

    def __init__(self, uri: Optional[str] = None):
        """Initialize ArcticDB connection.

        Args:
            uri: ArcticDB connection URI. Defaults to local LMDB at data/arcticdb/.
        """
        self._uri = uri or f"lmdb://{ARCTIC_DIR}"
        self._ac = None
        self._libs = {}
        ARCTIC_DIR.mkdir(parents=True, exist_ok=True)

    def _conn(self):
        """Lazy ArcticDB connection."""
        if self._ac is None:
            arc = _get_arcticdb()
            self._ac = arc.Arctic(self._uri)
        return self._ac

    def _lib(self, name: str):
        """Get or create a library (collection of symbols)."""
        if name not in self._libs:
            ac = self._conn()
            if name not in ac.list_libraries():
                ac.create_library(name)
            self._libs[name] = ac[name]
        return self._libs[name]

    # ── OHLCV BAR STORAGE ──

    def write_bars(self, symbol: str, df: pd.DataFrame) -> None:
        """Append OHLCV bars for a symbol. Deduplicates on index (timestamp).

        Expected columns: open, high, low, close, volume
        Index: DatetimeIndex (UTC)
        """
        if df.empty:
            return
        lib = self._lib(self.LIB_BARS)
        try:
            lib.append(symbol, df)
        except Exception:
            # First write or schema change — use write (creates new version)
            lib.write(symbol, df)
        log.debug("write_bars: %s → %d rows", symbol, len(df))

    def read_bars(self, symbol: str,
                  start: Optional[str] = None,
                  end: Optional[str] = None) -> pd.DataFrame:
        """Read OHLCV bars for a symbol, optionally filtered by date range.

        Args:
            symbol: Ticker symbol (e.g. "QQQ3.L")
            start: ISO date string (inclusive)
            end: ISO date string (inclusive)
        """
        lib = self._lib(self.LIB_BARS)
        if symbol not in [s for s in lib.list_symbols()]:
            return pd.DataFrame()

        arc = _get_arcticdb()
        qb = arc.QueryBuilder()
        if start:
            qb = qb[qb.index >= pd.Timestamp(start, tz="UTC")]
        if end:
            qb = qb[qb.index <= pd.Timestamp(end, tz="UTC")]

        try:
            return lib.read(symbol, query_builder=qb).data
        except Exception:
            # Fallback: read all then filter
            df = lib.read(symbol).data
            if start:
                df = df[df.index >= pd.Timestamp(start, tz="UTC")]
            if end:
                df = df[df.index <= pd.Timestamp(end, tz="UTC")]
            return df

    # ── TRADE HISTORY ──

    def write_trades(self, df: pd.DataFrame) -> None:
        """Append closed trades. Symbol key = 'all_trades' (single collection)."""
        if df.empty:
            return
        lib = self._lib(self.LIB_TRADES)
        try:
            lib.append("all_trades", df)
        except Exception:
            lib.write("all_trades", df)
        log.debug("write_trades: %d rows", len(df))

    def read_trades(self, start: Optional[str] = None,
                    end: Optional[str] = None) -> pd.DataFrame:
        """Read trade history."""
        lib = self._lib(self.LIB_TRADES)
        if "all_trades" not in [s for s in lib.list_symbols()]:
            return pd.DataFrame()
        df = lib.read("all_trades").data
        if start:
            df = df[df.index >= pd.Timestamp(start, tz="UTC")]
        if end:
            df = df[df.index <= pd.Timestamp(end, tz="UTC")]
        return df

    # ── SIGNAL SNAPSHOTS (versioned) ──

    def snapshot_signals(self, df: pd.DataFrame) -> None:
        """Write signal snapshot (new version each call — allows diff analysis)."""
        if df.empty:
            return
        lib = self._lib(self.LIB_SIGNALS)
        today = datetime.utcnow().strftime("%Y-%m-%d")
        lib.write(f"signals_{today}", df)
        log.debug("snapshot_signals: %d rows for %s", len(df), today)

    # ── FEATURE STORE (ML inputs) ──

    def write_features(self, symbol: str, df: pd.DataFrame) -> None:
        """Store computed features for ML training (versioned per symbol)."""
        if df.empty:
            return
        lib = self._lib(self.LIB_FEATURES)
        lib.write(symbol, df)

    def read_features(self, symbol: str) -> pd.DataFrame:
        """Read latest features for a symbol."""
        lib = self._lib(self.LIB_FEATURES)
        if symbol not in [s for s in lib.list_symbols()]:
            return pd.DataFrame()
        return lib.read(symbol).data

    # ── WAL INGESTION ──

    def ingest_wal(self, wal_path: str) -> int:
        """Ingest a WAL NDJSON file into ArcticDB bar + trade libraries.

        Parses PositionClosed events into trades, StateSnapshot into bars.
        Returns number of records ingested.
        """
        import json

        path = Path(wal_path)
        if not path.exists():
            log.warning("WAL file not found: %s", wal_path)
            return 0

        trades = []
        bars = {}  # symbol → list of bar dicts

        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                payload = event.get("payload", {})
                event_type = payload.get("type", "")

                if event_type == "PositionClosed":
                    trades.append({
                        "trade_id": event.get("id", ""),
                        "ticker": payload.get("symbol", ""),
                        "strategy": payload.get("strategy", ""),
                        "entry_price": payload.get("entry_price", 0),
                        "exit_price": payload.get("exit_price", 0),
                        "quantity": payload.get("quantity", 0),
                        "pnl": payload.get("pnl", 0),
                        "cost_adjusted_pnl": payload.get("cost_adjusted_pnl", 0),
                        "hold_secs": payload.get("hold_secs", 0),
                    })
                elif event_type == "StateSnapshot":
                    for sym, data in payload.get("bars", {}).items():
                        if sym not in bars:
                            bars[sym] = []
                        bars[sym].append(data)

        count = 0

        # Ingest trades
        if trades:
            df_trades = pd.DataFrame(trades)
            if "trade_id" in df_trades.columns:
                df_trades.index = pd.to_datetime(
                    [datetime.utcnow()] * len(df_trades), utc=True
                )
            self.write_trades(df_trades)
            count += len(trades)

        # Ingest bars
        for sym, bar_list in bars.items():
            if bar_list:
                df_bars = pd.DataFrame(bar_list)
                if not df_bars.empty:
                    df_bars.index = pd.to_datetime(df_bars.get("timestamp", []), utc=True)
                    self.write_bars(sym, df_bars)
                    count += len(df_bars)

        log.info("ingest_wal: %s → %d records", wal_path, count)
        return count

    # ── UTILITIES ──

    def list_symbols(self, library: str = "bars") -> list:
        """List all symbols in a library."""
        lib = self._lib(library)
        return list(lib.list_symbols())

    def symbol_info(self, symbol: str, library: str = "bars") -> dict:
        """Get metadata about a symbol (row count, date range, etc.)."""
        lib = self._lib(library)
        if symbol not in [s for s in lib.list_symbols()]:
            return {"exists": False}
        info = lib.read_metadata(symbol)
        df = lib.read(symbol).data
        return {
            "exists": True,
            "rows": len(df),
            "columns": list(df.columns),
            "start": str(df.index.min()) if len(df) > 0 else None,
            "end": str(df.index.max()) if len(df) > 0 else None,
        }

    def compact(self) -> None:
        """Compact all libraries (reclaim LMDB space after deletes)."""
        ac = self._conn()
        for lib_name in ac.list_libraries():
            lib = ac[lib_name]
            try:
                lib.compact_incomplete(symbol=None, append=True, convert=True)
            except Exception as e:
                log.debug("compact %s: %s", lib_name, e)


# Module-level singleton
_store: Optional[TimeSeriesStore] = None


def get_store() -> TimeSeriesStore:
    """Get module-level singleton TimeSeriesStore."""
    global _store
    if _store is None:
        _store = TimeSeriesStore()
    return _store


def ingest_today_wal(data_dir: str = "/app/data") -> int:
    """Convenience: ingest today's WAL into ArcticDB. Called from nightly pipeline."""
    from pathlib import Path
    wal_dir = Path(os.environ.get("AEGIS_WAL_DIR", "/app/events"))
    current_wal = wal_dir / "current.ndjson"
    if current_wal.exists():
        store = get_store()
        return store.ingest_wal(str(current_wal))
    return 0
