"""
config/change_log.py
=====================
Tracks configuration changes for audit trail.

On startup, compares current settings.yaml hash with last known hash.
Logs any changes to data/config_change_log.jsonl.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nzt48.config.change_log")

_DATA_DIR = Path(__file__).parent.parent / "data"
_CONFIG_FILE = Path(__file__).parent / "settings.yaml"
_CHANGE_LOG = _DATA_DIR / "config_change_log.jsonl"
_HASH_FILE = _DATA_DIR / "config_last_hash.txt"


class ChangeLogger:
    """Tracks and logs configuration changes."""

    def __init__(self) -> None:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)

    def check_for_changes(self) -> Optional[dict]:
        """Compare current config hash with last known hash.

        Returns change record if config has changed, None otherwise.
        """
        if not _CONFIG_FILE.exists():
            logger.warning("Config file not found: %s", _CONFIG_FILE)
            return None

        current_hash = self._compute_hash(_CONFIG_FILE)
        last_hash = self._load_last_hash()

        if current_hash == last_hash:
            logger.debug("Config unchanged (hash=%s)", current_hash[:8])
            return None

        # Config has changed
        change = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "CONFIG_CHANGED",
            "old_hash": last_hash or "INITIAL",
            "new_hash": current_hash,
            "file": str(_CONFIG_FILE),
        }

        # Log the change
        self._append_change(change)
        self._save_hash(current_hash)

        if last_hash:
            logger.warning("CONFIG CHANGED: hash %s → %s (see config_change_log.jsonl)",
                          last_hash[:8], current_hash[:8])
        else:
            logger.info("CONFIG: initial hash recorded (%s)", current_hash[:8])

        return change

    def log_manual_change(self, key: str, old_value: str, new_value: str, reason: str = "") -> None:
        """Log a manual configuration change."""
        change = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "MANUAL_CHANGE",
            "key": key,
            "old_value": old_value,
            "new_value": new_value,
            "reason": reason,
        }
        self._append_change(change)
        logger.info("CONFIG MANUAL CHANGE: %s = %s → %s (%s)", key, old_value, new_value, reason)

    @staticmethod
    def _compute_hash(filepath: Path) -> str:
        """Compute SHA-256 hash of a file."""
        return hashlib.sha256(filepath.read_bytes()).hexdigest()

    @staticmethod
    def _load_last_hash() -> Optional[str]:
        if _HASH_FILE.exists():
            return _HASH_FILE.read_text().strip()
        return None

    @staticmethod
    def _save_hash(hash_value: str) -> None:
        _HASH_FILE.write_text(hash_value)

    @staticmethod
    def _append_change(change: dict) -> None:
        with open(_CHANGE_LOG, "a") as f:
            f.write(json.dumps(change) + "\n")
