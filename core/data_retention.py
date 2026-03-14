"""
Data Retention Manager — NZT-48 V8.0
Self-learning data safety: ring buffer, outcomes rotation, model backup, WAL mode.

Principles:
- outcomes.jsonl capped at 2000 hot lines (rolling); older trades archived
- ml_model.pkl backed up before each retrain (7 daily + weekly backups for 3 months)
- Global ring buffer (deque maxsize=500) for in-memory learning ops — thread-safe
- SQLite WAL mode ensures crash-safe writes with minimal read blocking
"""

import collections
import gzip
import json
import logging
import os
import shutil
import sqlite3
import threading
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────
HOT_OUTCOMES_MAX = 2000      # Lines kept in live outcomes.jsonl
RING_BUFFER_MAXSIZE = 500    # In-memory trade deque (global, thread-safe)
MODEL_DAILY_BACKUPS = 7      # Daily model backups to keep
MODEL_WEEKLY_BACKUPS = 12    # Weekly model backups to keep (3 months)

DATA_DIR = Path("data")
ARCHIVE_DIR = DATA_DIR / "archive"
OUTCOMES_FILE = DATA_DIR / "outcomes.jsonl"
MODEL_FILE = DATA_DIR / "ml_model.pkl"
MODEL_STATE_FILE = DATA_DIR / "ml_meta_model_state.json"
DB_FILE = DATA_DIR / "nzt48.db"


class DataRetentionManager:
    """
    Memory-first recycling system for all NZT-48 self-learning data.

    Responsibilities:
    1. outcomes.jsonl rotation — keep last 2000 hot, archive remainder (gzip)
    2. ml_model.pkl daily backup — 7 daily + 12 weekly retained
    3. Global ring buffer — shared deque(maxsize=500) for in-memory ops
    4. SQLite WAL mode — ensure crash-safe writes
    """

    # Class-level ring buffer — shared across all learning modules
    _ring_buffer: collections.deque = collections.deque(maxlen=RING_BUFFER_MAXSIZE)
    _ring_lock: threading.Lock = threading.Lock()

    def __init__(self):
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        self._ensure_wal_mode()

    # ─────────────────────────────────────────────────────────
    # Ring buffer (global, thread-safe)
    # ─────────────────────────────────────────────────────────

    @classmethod
    def get_ring_buffer(cls) -> collections.deque:
        """Returns the global ring buffer deque (maxsize=500)."""
        return cls._ring_buffer

    @classmethod
    def push_to_ring(cls, outcome: dict) -> None:
        """Thread-safe push of a trade outcome to the ring buffer."""
        with cls._ring_lock:
            cls._ring_buffer.append(outcome)

    @classmethod
    def get_ring_snapshot(cls, n: Optional[int] = None) -> list:
        """Thread-safe snapshot of ring buffer. Returns last n items or all."""
        with cls._ring_lock:
            items = list(cls._ring_buffer)
        if n:
            return items[-n:]
        return items

    @classmethod
    def ring_size(cls) -> int:
        """Current number of items in the ring buffer."""
        return len(cls._ring_buffer)

    @classmethod
    def ring_utilization_pct(cls) -> float:
        """Ring buffer utilization as percentage of maxsize."""
        return (len(cls._ring_buffer) / RING_BUFFER_MAXSIZE) * 100

    # ─────────────────────────────────────────────────────────
    # outcomes.jsonl rotation
    # ─────────────────────────────────────────────────────────

    def rotate_outcomes(self, max_hot_lines: int = HOT_OUTCOMES_MAX) -> dict:
        """
        Rotates outcomes.jsonl — keeps last `max_hot_lines` hot, archives the rest.

        Archive file: data/archive/outcomes_YYYY.jsonl.gz (append-only, compressed)
        Returns: {rotated: bool, archived_lines: int, hot_lines: int}
        """
        if not OUTCOMES_FILE.exists():
            return {"rotated": False, "archived_lines": 0, "hot_lines": 0}

        try:
            lines = OUTCOMES_FILE.read_text(encoding="utf-8").splitlines()
            total = len(lines)

            if total <= max_hot_lines:
                return {"rotated": False, "archived_lines": 0, "hot_lines": total}

            hot_lines = lines[-max_hot_lines:]
            archive_lines = lines[:-max_hot_lines]

            # Append to this year's archive (gzipped)
            year = date.today().year
            archive_file = ARCHIVE_DIR / f"outcomes_{year}.jsonl.gz"
            with gzip.open(archive_file, "at", encoding="utf-8") as f:
                for line in archive_lines:
                    f.write(line + "\n")

            # Rewrite hot file
            OUTCOMES_FILE.write_text("\n".join(hot_lines) + "\n", encoding="utf-8")

            logger.info(
                "Outcomes rotated: %d archived → %s, %d hot lines remain",
                len(archive_lines), archive_file.name, len(hot_lines),
            )
            return {
                "rotated": True,
                "archived_lines": len(archive_lines),
                "hot_lines": len(hot_lines),
            }

        except Exception as e:
            logger.warning("Outcomes rotation failed: %s", e)
            return {"rotated": False, "archived_lines": 0, "hot_lines": 0, "error": str(e)}

    def get_outcomes_line_count(self) -> int:
        """Current line count of outcomes.jsonl."""
        if not OUTCOMES_FILE.exists():
            return 0
        try:
            return sum(1 for _ in OUTCOMES_FILE.open(encoding="utf-8"))
        except Exception:
            return 0

    # ─────────────────────────────────────────────────────────
    # ml_model.pkl backup
    # ─────────────────────────────────────────────────────────

    def backup_model(self) -> dict:
        """
        Backs up ml_model.pkl (or ml_meta_model_state.json if pkl not yet created).
        Retains: last 7 daily backups + last 12 weekly (Sunday) backups.
        Returns: {backed_up: bool, backup_path: str}
        """
        # Find actual model file — pkl or state json
        source = None
        if MODEL_FILE.exists():
            source = MODEL_FILE
        elif MODEL_STATE_FILE.exists():
            source = MODEL_STATE_FILE

        if not source:
            logger.debug("backup_model: no model file found yet")
            return {"backed_up": False, "backup_path": None}

        try:
            today = date.today()
            suffix = source.suffix
            backup_name = f"ml_model_{today.strftime('%Y%m%d')}{suffix}"
            backup_path = ARCHIVE_DIR / backup_name

            shutil.copy2(source, backup_path)
            logger.info("Model backed up: %s → %s", source.name, backup_path.name)

            # Prune old daily backups (keep last MODEL_DAILY_BACKUPS)
            self._prune_model_backups()

            return {"backed_up": True, "backup_path": str(backup_path)}

        except Exception as e:
            logger.warning("Model backup failed: %s", e)
            return {"backed_up": False, "backup_path": None, "error": str(e)}

    def _prune_model_backups(self) -> None:
        """Retain 7 daily + 12 weekly (Sunday) backups; delete older ones."""
        try:
            # Find all model backups
            backups = sorted(ARCHIVE_DIR.glob("ml_model_*"))
            if len(backups) <= MODEL_DAILY_BACKUPS:
                return

            # Parse dates from filenames
            dated = []
            for p in backups:
                try:
                    name = p.stem  # e.g. ml_model_20250301
                    parts = name.split("_")
                    d = date(int(parts[2][:4]), int(parts[2][4:6]), int(parts[2][6:8]))
                    dated.append((d, p))
                except Exception:
                    continue

            dated.sort(reverse=True)  # Most recent first

            keep = set()
            # Keep last 7 daily
            for d, p in dated[:MODEL_DAILY_BACKUPS]:
                keep.add(p)

            # Keep last 12 weekly (Sunday backups)
            weekly_kept = 0
            for d, p in dated:
                if d.weekday() == 6 and p not in keep:  # Sunday
                    keep.add(p)
                    weekly_kept += 1
                    if weekly_kept >= MODEL_WEEKLY_BACKUPS:
                        break

            # Delete anything not in keep set
            for d, p in dated:
                if p not in keep:
                    p.unlink()
                    logger.debug("Pruned old model backup: %s", p.name)

        except Exception as e:
            logger.debug("Model backup pruning failed: %s", e)

    # ─────────────────────────────────────────────────────────
    # SQLite WAL mode
    # ─────────────────────────────────────────────────────────

    def _ensure_wal_mode(self) -> None:
        """Enable WAL mode on nzt48.db if it exists."""
        if not DB_FILE.exists():
            return
        try:
            conn = sqlite3.connect(str(DB_FILE))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA wal_autocheckpoint=1000")
            conn.execute("PRAGMA synchronous=NORMAL")  # Faster than FULL, still crash-safe with WAL
            conn.commit()
            conn.close()
            logger.info("SQLite WAL mode confirmed: %s", DB_FILE.name)
        except Exception as e:
            logger.warning("SQLite WAL mode setup failed: %s", e)

    def ensure_wal_mode(self, db_path: Optional[str] = None) -> bool:
        """Public method to enable WAL mode on a SQLite DB. Returns True on success."""
        path = Path(db_path) if db_path else DB_FILE
        if not path.exists():
            return False
        try:
            conn = sqlite3.connect(str(path))
            result = conn.execute("PRAGMA journal_mode=WAL").fetchone()
            conn.execute("PRAGMA wal_autocheckpoint=1000")
            conn.commit()
            conn.close()
            return result and result[0].lower() == "wal"
        except Exception as e:
            logger.warning("WAL mode failed for %s: %s", path, e)
            return False

    # ─────────────────────────────────────────────────────────
    # Status / dashboard
    # ─────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Returns current data retention status for war room / monitoring."""
        outcomes_lines = self.get_outcomes_line_count()
        model_exists = MODEL_FILE.exists() or MODEL_STATE_FILE.exists()
        archive_count = len(list(ARCHIVE_DIR.glob("ml_model_*"))) if ARCHIVE_DIR.exists() else 0

        return {
            "outcomes_lines": outcomes_lines,
            "outcomes_rotation_needed": outcomes_lines > HOT_OUTCOMES_MAX,
            "ring_buffer_size": self.ring_size(),
            "ring_buffer_utilization_pct": round(self.ring_utilization_pct(), 1),
            "model_exists": model_exists,
            "model_backups": archive_count,
            "archive_dir": str(ARCHIVE_DIR),
            "wal_mode_enabled": self._get_wal_status(),
        }

    def _get_wal_status(self) -> bool:
        """Check if WAL mode is currently active."""
        if not DB_FILE.exists():
            return False
        try:
            conn = sqlite3.connect(str(DB_FILE))
            result = conn.execute("PRAGMA journal_mode").fetchone()
            conn.close()
            return result and result[0].lower() == "wal"
        except Exception:
            return False

    def get_telegram_summary(self) -> str:
        """One-liner for nightly Telegram intelligence report."""
        status = self.get_status()
        lines = int(status["outcomes_lines"])
        ring_pct = status["ring_buffer_utilization_pct"]
        rotation_warn = " ⚠️ ROTATION NEEDED" if status["outcomes_rotation_needed"] else ""
        return (
            f"💾 Data: outcomes={lines} lines{rotation_warn} | "
            f"ring={ring_pct:.0f}% | "
            f"model_backups={status['model_backups']} | "
            f"WAL={'✅' if status['wal_mode_enabled'] else '❌'}"
        )
