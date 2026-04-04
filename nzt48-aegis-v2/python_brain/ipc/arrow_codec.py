"""Apache Arrow IPC Codec — Phase 6.6 (Session 28).

Zero-copy serialization for Rust↔Python bridge messages using Apache Arrow IPC.
Replaces JSON text serialization with binary Arrow record batches for:
  - 10-50x lower serialization latency (binary vs text parsing)
  - Zero-copy reads via memory-mapped files
  - Batch processing support (N ticks → 1 IPC message)

Architecture:
  Current:  Rust → JSON text → stdin pipe → Python json.loads()
  Arrow:    Rust → Arrow IPC buffer → shared file/pipe → Python pyarrow.ipc.read_message()

This module provides the Python-side codec. Rust-side uses arrow2 or arrow-rs.

Usage:
    from python_brain.ipc.arrow_codec import TickBatchCodec, SignalCodec

    # Decode a batch of ticks from Arrow IPC bytes
    codec = TickBatchCodec()
    ticks_df = codec.decode(ipc_bytes)

    # Encode a signal response as Arrow IPC bytes
    sig_codec = SignalCodec()
    ipc_bytes = sig_codec.encode(signal_dict)

Dependencies: pyarrow (Apache 2.0, already in requirements.txt)
"""

from __future__ import annotations

import io
import logging
from typing import Dict, List, Optional

import pyarrow as pa
import pyarrow.ipc as ipc

log = logging.getLogger("arrow_codec")

# ══════════════════════════════════════════════════════════════
# SCHEMAS — must match Rust-side TickContext + BrainSignal
# ══════════════════════════════════════════════════════════════

TICK_SCHEMA = pa.schema([
    ("ticker_id", pa.uint32()),
    ("last", pa.float64()),
    ("high", pa.float64()),
    ("low", pa.float64()),
    ("bid", pa.float64()),
    ("ask", pa.float64()),
    ("volume", pa.uint64()),
    ("timestamp_ns", pa.uint64()),
    ("win_rate", pa.float64()),
    ("total_trades", pa.uint32()),
    ("avg_win", pa.float64()),
    ("avg_loss", pa.float64()),
    ("leverage", pa.uint32()),
    ("realized_vol", pa.float64()),
    ("correlation", pa.float64()),
    ("drawdown_pct", pa.float64()),
    ("amihud", pa.float64()),
    ("regime", pa.utf8()),
    ("spread_pct", pa.float64()),
    ("time_fraction", pa.float64()),
    ("heat_pct", pa.float64()),
    ("equity", pa.float64()),
    ("vix", pa.float64()),
    ("london_time_secs", pa.uint32()),
    ("gap_pct", pa.float64()),
    ("symbol", pa.utf8()),
    ("open_positions", pa.uint32()),
    ("trades_today", pa.uint32()),
])

SIGNAL_SCHEMA = pa.schema([
    ("type", pa.utf8()),           # "signal", "no_signal", "error"
    ("ticker_id", pa.uint32()),
    ("direction", pa.utf8()),       # "Long", "Short"
    ("confidence", pa.int32()),
    ("kelly_fraction", pa.float64()),
    ("shares", pa.int32()),
    ("strategy", pa.utf8()),
    ("z_score", pa.float64()),
    ("rvol", pa.float64()),
    ("hurst", pa.float64()),
    ("adx", pa.float64()),
    ("rsi", pa.float64()),
    ("ibs", pa.float64()),
    ("structural_score", pa.float64()),
    ("entry_type", pa.utf8()),
    ("vwap_dist_pct", pa.float64()),
    ("vol_slope", pa.float64()),
    ("scanner_score", pa.float64()),
    ("suggested_initial_stop_atr_mult", pa.float64()),
    ("max_hold_hours", pa.float64()),
    ("exit_trail_bias", pa.utf8()),
    ("execution_algo", pa.utf8()),
])

# ══════════════════════════════════════════════════════════════
# TICK BATCH CODEC
# ══════════════════════════════════════════════════════════════


class TickBatchCodec:
    """Encode/decode batches of tick messages using Arrow IPC."""

    def __init__(self):
        self._schema = TICK_SCHEMA

    def encode(self, ticks: List[Dict]) -> bytes:
        """Encode a list of tick dicts into Arrow IPC bytes.

        Args:
            ticks: List of tick message dicts (from JSON parse or direct construction)

        Returns:
            Arrow IPC stream bytes (can be written to file or pipe)
        """
        if not ticks:
            return b""

        arrays = {}
        for field in self._schema:
            name = field.name
            values = [t.get(name, _default_for_type(field.type)) for t in ticks]
            arrays[name] = pa.array(values, type=field.type)

        batch = pa.record_batch(arrays, schema=self._schema)

        sink = io.BytesIO()
        writer = ipc.new_stream(sink, self._schema)
        writer.write_batch(batch)
        writer.close()
        return sink.getvalue()

    def decode(self, data: bytes) -> List[Dict]:
        """Decode Arrow IPC bytes into a list of tick dicts.

        Args:
            data: Arrow IPC stream bytes

        Returns:
            List of tick message dicts
        """
        if not data:
            return []

        reader = ipc.open_stream(io.BytesIO(data))
        table = reader.read_all()
        return table.to_pylist()

    def encode_single(self, tick: Dict) -> bytes:
        """Encode a single tick as Arrow IPC (convenience wrapper)."""
        return self.encode([tick])

    def decode_single(self, data: bytes) -> Optional[Dict]:
        """Decode a single tick from Arrow IPC (convenience wrapper)."""
        ticks = self.decode(data)
        return ticks[0] if ticks else None


# ══════════════════════════════════════════════════════════════
# SIGNAL CODEC
# ══════════════════════════════════════════════════════════════


class SignalCodec:
    """Encode/decode signal responses using Arrow IPC."""

    def __init__(self):
        self._schema = SIGNAL_SCHEMA

    def encode(self, signal: Dict) -> bytes:
        """Encode a signal dict into Arrow IPC bytes."""
        arrays = {}
        for field in self._schema:
            name = field.name
            val = signal.get(name, _default_for_type(field.type))
            arrays[name] = pa.array([val], type=field.type)

        batch = pa.record_batch(arrays, schema=self._schema)

        sink = io.BytesIO()
        writer = ipc.new_stream(sink, self._schema)
        writer.write_batch(batch)
        writer.close()
        return sink.getvalue()

    def decode(self, data: bytes) -> Optional[Dict]:
        """Decode Arrow IPC bytes into a signal dict."""
        if not data:
            return None

        reader = ipc.open_stream(io.BytesIO(data))
        table = reader.read_all()
        rows = table.to_pylist()
        return rows[0] if rows else None

    def encode_batch(self, signals: List[Dict]) -> bytes:
        """Encode multiple signals into a single Arrow IPC message."""
        if not signals:
            return b""

        arrays = {}
        for field in self._schema:
            name = field.name
            values = [s.get(name, _default_for_type(field.type)) for s in signals]
            arrays[name] = pa.array(values, type=field.type)

        batch = pa.record_batch(arrays, schema=self._schema)

        sink = io.BytesIO()
        writer = ipc.new_stream(sink, self._schema)
        writer.write_batch(batch)
        writer.close()
        return sink.getvalue()


# ══════════════════════════════════════════════════════════════
# FILE-BASED IPC (for tmpfs shared memory)
# ══════════════════════════════════════════════════════════════


def write_ipc_file(path: str, table: pa.Table) -> None:
    """Write Arrow table to IPC file (for shared memory / tmpfs exchange)."""
    writer = ipc.new_file(path, table.schema)
    writer.write_table(table)
    writer.close()


def read_ipc_file(path: str) -> pa.Table:
    """Read Arrow table from IPC file (memory-mapped for zero-copy)."""
    return ipc.open_file(pa.memory_map(path, "r")).read_all()


# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════


def _default_for_type(arrow_type: pa.DataType):
    """Return sensible default for an Arrow type."""
    if pa.types.is_integer(arrow_type):
        return 0
    if pa.types.is_floating(arrow_type):
        return 0.0
    if pa.types.is_string(arrow_type):
        return ""
    if pa.types.is_boolean(arrow_type):
        return False
    return None


def benchmark_codec(n_ticks: int = 1000) -> Dict:
    """Benchmark Arrow IPC vs JSON for tick serialization.

    Returns dict with timing comparisons.
    """
    import json
    import time

    # Generate sample ticks
    ticks = []
    for i in range(n_ticks):
        ticks.append({
            "ticker_id": i % 12,
            "last": 10.5 + i * 0.001,
            "high": 10.6 + i * 0.001,
            "low": 10.4 + i * 0.001,
            "bid": 10.49 + i * 0.001,
            "ask": 10.51 + i * 0.001,
            "volume": 1000 + i,
            "timestamp_ns": 1712275200000000000 + i * 1000000,
            "win_rate": 0.55,
            "total_trades": 100,
            "avg_win": 0.025,
            "avg_loss": 0.020,
            "leverage": 3,
            "realized_vol": 0.32,
            "correlation": 0.15,
            "drawdown_pct": 0.80,
            "amihud": 0.001,
            "regime": "normal",
            "spread_pct": 0.10,
            "time_fraction": 0.50,
            "heat_pct": 3.20,
            "equity": 10500.0,
            "vix": 18.5,
            "london_time_secs": 36000,
            "gap_pct": 0.015,
            "symbol": "VUSA.L",
            "open_positions": 5,
            "trades_today": 12,
        })

    # JSON baseline
    t0 = time.monotonic()
    for t in ticks:
        json.dumps(t)
    json_encode_ms = (time.monotonic() - t0) * 1000

    t0 = time.monotonic()
    for t in ticks:
        json.loads(json.dumps(t))
    json_roundtrip_ms = (time.monotonic() - t0) * 1000

    # Arrow IPC
    codec = TickBatchCodec()

    t0 = time.monotonic()
    arrow_bytes = codec.encode(ticks)
    arrow_encode_ms = (time.monotonic() - t0) * 1000

    t0 = time.monotonic()
    codec.decode(arrow_bytes)
    arrow_decode_ms = (time.monotonic() - t0) * 1000

    return {
        "n_ticks": n_ticks,
        "json_encode_ms": round(json_encode_ms, 2),
        "json_roundtrip_ms": round(json_roundtrip_ms, 2),
        "arrow_encode_ms": round(arrow_encode_ms, 2),
        "arrow_decode_ms": round(arrow_decode_ms, 2),
        "arrow_bytes": len(arrow_bytes),
        "json_bytes": sum(len(json.dumps(t).encode()) for t in ticks),
        "speedup_encode": round(json_encode_ms / max(arrow_encode_ms, 0.01), 1),
        "speedup_decode": round(json_roundtrip_ms / max(arrow_decode_ms, 0.01), 1),
    }
