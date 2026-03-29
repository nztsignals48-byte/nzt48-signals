"""Audit Trail & Regulatory Compliance — Books 88, 185.

Immutable audit trail for all trading decisions, required by:
  - ISA wrapper rules (HMRC)
  - MAR (Market Abuse Regulation)
  - MiFID II algorithmic trading requirements

Every trade, signal, parameter change, and system event is logged
with a cryptographic hash chain for tamper detection.

Key MiFID II requirements for algorithmic trading:
  1. Record keeping: all orders, modifications, cancellations
  2. Best execution: document execution quality
  3. Risk controls: evidence that pre-trade risk checks fire
  4. Kill switches: documented ability to halt all trading

Usage:
    from python_brain.forensics.audit_trail import (
        AuditTrail, AuditEvent, AuditCategory,
    )

    trail = AuditTrail()
    trail.log(AuditCategory.TRADE, "Entry submitted", details={...})
    trail.log(AuditCategory.RISK, "CHECK 35 triggered", details={...})
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("audit_trail")

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
AUDIT_DIR = DATA_DIR / "audit"


class AuditCategory(Enum):
    TRADE = "TRADE"           # Order submission, fill, cancellation
    RISK = "RISK"             # Risk check triggers, regime changes
    PARAM = "PARAM"           # Parameter changes
    SYSTEM = "SYSTEM"         # Startup, shutdown, health events
    STRATEGY = "STRATEGY"     # Strategy lifecycle events
    COMPLIANCE = "COMPLIANCE" # Regulatory events (ISA limits, MAR)
    DATA = "DATA"             # Data quality events
    OPERATOR = "OPERATOR"     # Human interventions


@dataclass
class AuditEvent:
    """A single audit trail entry."""
    timestamp: str
    category: str
    action: str
    details: Dict[str, Any]
    hash: str = ""  # SHA-256 hash including previous event's hash
    sequence: int = 0

    def compute_hash(self, prev_hash: str = "") -> str:
        """Compute SHA-256 hash for tamper detection."""
        payload = f"{self.sequence}|{self.timestamp}|{self.category}|{self.action}|{json.dumps(self.details, sort_keys=True)}|{prev_hash}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


class AuditTrail:
    """Append-only audit trail with hash chain integrity."""

    def __init__(self, audit_dir: Optional[Path] = None):
        self._dir = audit_dir or AUDIT_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._sequence = 0
        self._prev_hash = "genesis"
        self._today_path: Optional[Path] = None
        self._today_str = ""

    def _get_file(self) -> Path:
        """Get today's audit file."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._today_str:
            self._today_str = today
            self._today_path = self._dir / f"audit_{today}.ndjson"
            # Read last hash from existing file for chain continuity
            if self._today_path.exists():
                try:
                    with open(self._today_path) as f:
                        for line in f:
                            pass  # Read to last line
                    last = json.loads(line.strip())
                    self._prev_hash = last.get("hash", "genesis")
                    self._sequence = last.get("sequence", 0) + 1
                except (json.JSONDecodeError, UnboundLocalError):
                    pass
        return self._today_path

    def log_event(
        self,
        category: AuditCategory,
        action: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> AuditEvent:
        """Log an audit event."""
        event = AuditEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            category=category.value,
            action=action,
            details=details or {},
            sequence=self._sequence,
        )
        event.hash = event.compute_hash(self._prev_hash)
        self._prev_hash = event.hash
        self._sequence += 1

        # Append to file
        path = self._get_file()
        try:
            with open(path, "a") as f:
                f.write(event.to_json() + "\n")
        except IOError as e:
            log.error("Audit write failed: %s", e)

        return event

    def log_trade(self, action: str, **details):
        return self.log_event(AuditCategory.TRADE, action, details)

    def log_risk(self, action: str, **details):
        return self.log_event(AuditCategory.RISK, action, details)

    def log_param(self, action: str, **details):
        return self.log_event(AuditCategory.PARAM, action, details)

    def log_system(self, action: str, **details):
        return self.log_event(AuditCategory.SYSTEM, action, details)

    def verify_chain(self, audit_file: Path) -> bool:
        """Verify hash chain integrity of an audit file.

        Returns True if all hashes are valid (no tampering).
        """
        prev_hash = "genesis"
        with open(audit_file) as f:
            for line_num, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    event = AuditEvent(**data)
                    expected = event.compute_hash(prev_hash)
                    if event.hash != expected:
                        log.error("AUDIT INTEGRITY FAILURE at line %d: expected %s, got %s",
                                 line_num, expected, event.hash)
                        return False
                    prev_hash = event.hash
                except (json.JSONDecodeError, TypeError) as e:
                    log.error("AUDIT PARSE ERROR at line %d: %s", line_num, e)
                    return False

        log.info("Audit chain verified: %s (%d events)", audit_file.name, line_num)
        return True

    def event_count(self) -> int:
        return self._sequence

    def to_dict(self) -> dict:
        return {
            "audit_dir": str(self._dir),
            "sequence": self._sequence,
            "latest_hash": self._prev_hash,
            "today_file": str(self._today_path) if self._today_path else "",
        }
