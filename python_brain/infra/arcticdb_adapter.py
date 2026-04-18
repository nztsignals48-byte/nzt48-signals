"""ArcticDB + Arrow IPC adapter — high-performance time-series persistence.

Wraps ArcticDB (Man Group's C++ DataFrame database) for V5's tick + fill
storage. Falls back to JSONL WAL when arcticdb not installed.

Consumed by: arcticdb_ingester.py (scripts/v5_supervisor.py line 215 references it).
Referenced by: ouroboros nightly for fast Sharpe/PF/DSR queries.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

try:
    import arcticdb as adb
    HAS_ARCTICDB = True
except ImportError:
    adb = None
    HAS_ARCTICDB = False

try:
    import pyarrow as pa
    import pyarrow.ipc as ipc
    HAS_ARROW = True
except ImportError:
    pa = None
    ipc = None
    HAS_ARROW = False


log = logging.getLogger("arctic-adapter")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROOT = Path("/Users/rr/aegis-v5")
ARCTIC_URI = f"lmdb://{ROOT / 'data/arctic'}"
ARROW_IPC_DIR = ROOT / "data/arrow_ipc"


class ArcticAdapter:
    """High-performance tick/fill store with ArcticDB or Arrow IPC fallback."""

    def __init__(self, library: str = "v5_ticks"):
        self.library = library
        self._ac = None
        self._lib = None
        if HAS_ARCTICDB:
            try:
                self._ac = adb.Arctic(ARCTIC_URI)
                if library not in self._ac.list_libraries():
                    self._ac.create_library(library)
                self._lib = self._ac[library]
                log.info("ArcticDB connected at %s library=%s", ARCTIC_URI, library)
            except Exception as e:
                log.warning("ArcticDB init failed: %s (using fallback)", e)
                self._ac = None

    def write_frame(self, symbol: str, df):
        """Write DataFrame to ArcticDB or JSONL fallback."""
        if self._lib is not None:
            self._lib.write(symbol, df)
            return True
        # Fallback: JSONL
        path = ROOT / f"data/archive/arctic_fallback_{symbol}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            if hasattr(df, "to_dict"):
                for _, row in df.iterrows():
                    f.write(json.dumps(row.to_dict(), default=str) + "\n")
        return False

    def read_frame(self, symbol: str, date_range=None):
        """Read DataFrame from ArcticDB."""
        if self._lib is None:
            return None
        try:
            return self._lib.read(symbol).data
        except Exception:
            return None

    def write_arrow_ipc(self, name: str, records: list[dict]):
        """Write records as Arrow IPC stream (for cross-process zero-copy)."""
        if not HAS_ARROW or not records:
            return False
        ARROW_IPC_DIR.mkdir(parents=True, exist_ok=True)
        path = ARROW_IPC_DIR / f"{name}.arrow"
        table = pa.Table.from_pylist(records)
        with ipc.new_file(path, table.schema) as writer:
            writer.write_table(table)
        return True

    def read_arrow_ipc(self, name: str):
        """Read Arrow IPC stream."""
        if not HAS_ARROW:
            return None
        path = ARROW_IPC_DIR / f"{name}.arrow"
        if not path.exists():
            return None
        with ipc.open_file(path) as reader:
            return reader.read_all()


def capabilities() -> dict:
    return {
        "arcticdb": HAS_ARCTICDB,
        "arrow_ipc": HAS_ARROW,
        "arctic_uri": ARCTIC_URI if HAS_ARCTICDB else None,
    }


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        print("Capabilities:", capabilities())
        adapter = ArcticAdapter()
        ok = adapter.write_arrow_ipc("test_smoke", [
            {"ticker": "AAPL", "price": 100.0, "ts": 1700000000},
            {"ticker": "AAPL", "price": 101.0, "ts": 1700000001},
        ])
        print(f"Arrow IPC write: {ok}")
        if ok:
            table = adapter.read_arrow_ipc("test_smoke")
            print(f"Arrow IPC read: {len(table) if table else 0} rows")
        print("OK")
