"""Book 58 — Escalation Timeout Manager.

Monitors unacknowledged Telegram alerts and auto-escalates:

  WARNING (Tier 2):
    - Sent with sound notification
    - If unacknowledged after 15 min → escalate to CRITICAL (Tier 3)

  CRITICAL (Tier 3):
    - Repeated every 5 min until acknowledged
    - If unacknowledged after 60 min → EMERGENCY: flatten all positions

  EMERGENCY (Tier 4):
    - System sends flatten command to Rust engine
    - Requires human /restart to resume trading

Acknowledgement:
    User replies "/ack" or "/ack <alert_id>" in Telegram chat.
    The Telegram bot webhook (or polling loop) calls `acknowledge(alert_id)`.

Watchdog integration:
    Run `escalation_tick()` every 60 seconds from the bridge watchdog or
    a dedicated cron. It checks all pending alerts and escalates as needed.

Files:
    State persisted to /app/data/escalation_state.json (survives restarts).
    Flatten command sent via /app/data/commands/flatten.json (Rust reads).

Usage:
    from python_brain.alerting.escalation_manager import (
        EscalationManager, escalation_tick,
    )

    mgr = EscalationManager()
    mgr.register_alert(alert)           # Called when TelegramAlerter sends WARNING/CRITICAL
    mgr.acknowledge("alert_id")         # Called when user sends /ack
    escalation_tick()                    # Called every 60s from watchdog
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("escalation_manager")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
STATE_FILE = DATA_DIR / "escalation_state.json"
FLATTEN_CMD_FILE = DATA_DIR / "commands" / "flatten.json"

# Timeouts (seconds)
WARNING_ESCALATE_SECS = 15 * 60    # 15 min: WARNING → CRITICAL
CRITICAL_FLATTEN_SECS = 60 * 60    # 60 min: CRITICAL → EMERGENCY (flatten)
CRITICAL_REPEAT_SECS = 5 * 60      # 5 min: re-send CRITICAL alerts


class EscalationLevel(Enum):
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    EMERGENCY = "EMERGENCY"


@dataclass
class PendingAlert:
    """A tracked alert awaiting acknowledgement."""
    alert_id: str
    level: str                  # WARNING / CRITICAL / EMERGENCY
    title: str
    body: str
    source: str = ""
    created_at: float = 0.0    # Unix timestamp when first registered
    escalated_at: float = 0.0  # Unix timestamp when last escalated
    last_repeat_at: float = 0.0  # For CRITICAL repeat every 5 min
    acknowledged: bool = False
    ack_at: float = 0.0
    flatten_sent: bool = False

    def age_secs(self) -> float:
        return time.time() - self.created_at if self.created_at > 0 else 0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> PendingAlert:
        return PendingAlert(**{k: v for k, v in d.items() if k in PendingAlert.__dataclass_fields__})


class EscalationManager:
    """Manages alert escalation lifecycle."""

    def __init__(self):
        self._pending: Dict[str, PendingAlert] = {}
        self._load_state()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------
    def register_alert(self, alert_id: str, level: str, title: str,
                       body: str, source: str = "") -> PendingAlert:
        """Register a new alert for escalation tracking.

        Only WARNING and CRITICAL alerts are tracked (INFO is fire-and-forget,
        EMERGENCY is already terminal).
        """
        if level not in ("WARNING", "CRITICAL"):
            return PendingAlert(alert_id=alert_id, level=level, title=title,
                                body=body, source=source, created_at=time.time())

        now = time.time()
        pa = PendingAlert(
            alert_id=alert_id,
            level=level,
            title=title,
            body=body,
            source=source,
            created_at=now,
            last_repeat_at=now,
        )
        self._pending[alert_id] = pa
        self._save_state()
        log.info("ESCALATION: registered %s [%s] %s", alert_id, level, title)
        return pa

    def acknowledge(self, alert_id: str) -> bool:
        """Acknowledge an alert by ID. Returns True if found."""
        pa = self._pending.get(alert_id)
        if pa is None:
            # Try partial match (user might send /ack without full ID)
            for aid, p in self._pending.items():
                if not p.acknowledged:
                    pa = p
                    alert_id = aid
                    break
            if pa is None:
                log.warning("ESCALATION: /ack for unknown alert_id=%s", alert_id)
                return False

        pa.acknowledged = True
        pa.ack_at = time.time()
        self._save_state()
        age_min = pa.age_secs() / 60
        log.info("ESCALATION: ACK %s [%s] after %.1f min", alert_id, pa.level, age_min)
        return True

    def acknowledge_all(self) -> int:
        """Acknowledge all pending alerts. Returns count acknowledged."""
        count = 0
        now = time.time()
        for pa in self._pending.values():
            if not pa.acknowledged:
                pa.acknowledged = True
                pa.ack_at = now
                count += 1
        if count:
            self._save_state()
            log.info("ESCALATION: ACK_ALL %d alerts", count)
        return count

    def tick(self) -> List[Dict[str, Any]]:
        """Check all pending alerts and escalate as needed.

        Returns list of actions taken (for logging / Telegram notification).
        Call this every 60 seconds.
        """
        now = time.time()
        actions: List[Dict[str, Any]] = []

        for alert_id, pa in list(self._pending.items()):
            if pa.acknowledged:
                continue

            age = now - pa.created_at

            # WARNING → CRITICAL after 15 min
            if pa.level == "WARNING" and age >= WARNING_ESCALATE_SECS:
                pa.level = "CRITICAL"
                pa.escalated_at = now
                pa.last_repeat_at = now
                self._save_state()
                actions.append({
                    "action": "ESCALATE",
                    "alert_id": alert_id,
                    "from": "WARNING",
                    "to": "CRITICAL",
                    "age_min": round(age / 60, 1),
                    "title": pa.title,
                })
                log.warning("ESCALATION: %s WARNING→CRITICAL after %.0f min: %s",
                            alert_id, age / 60, pa.title)
                # Notify operator of escalation via Telegram
                self._send_escalation_notification(alert_id, pa)
                continue  # Don't also check flatten on same tick — give operator a chance

            # CRITICAL → EMERGENCY (flatten) after 60 min from CREATION
            # (not from escalation — a WARNING that sat 15 min then CRITICAL 45 min = 60 min total)
            if pa.level == "CRITICAL" and age >= CRITICAL_FLATTEN_SECS and not pa.flatten_sent:
                pa.level = "EMERGENCY"
                pa.escalated_at = now
                pa.flatten_sent = True
                self._save_state()
                self._send_flatten_command(alert_id, pa.title)
                actions.append({
                    "action": "FLATTEN",
                    "alert_id": alert_id,
                    "age_min": round(age / 60, 1),
                    "title": pa.title,
                })
                log.critical("ESCALATION: FLATTEN triggered for %s after %.0f min: %s",
                             alert_id, age / 60, pa.title)
                continue

            # CRITICAL: repeat every 5 min (with countdown to flatten)
            if pa.level == "CRITICAL" and (now - pa.last_repeat_at) >= CRITICAL_REPEAT_SECS:
                pa.last_repeat_at = now
                remaining_min = max(0, (CRITICAL_FLATTEN_SECS - age) / 60)
                actions.append({
                    "action": "REPEAT",
                    "alert_id": alert_id,
                    "level": "CRITICAL",
                    "age_min": round(age / 60, 1),
                    "remaining_min": round(remaining_min, 1),
                    "title": pa.title,
                })
                log.warning("ESCALATION: REPEAT CRITICAL %s (%.0f min, %.0f min to flatten): %s",
                            alert_id, age / 60, remaining_min, pa.title)
                # Re-send Telegram reminder with countdown
                self._send_repeat_notification(alert_id, pa, remaining_min)

        # Prune acknowledged alerts older than 24 hours
        cutoff = now - 86400
        stale = [k for k, v in self._pending.items() if v.acknowledged and v.ack_at < cutoff]
        for k in stale:
            del self._pending[k]
        if stale:
            self._save_state()

        return actions

    def pending_count(self) -> int:
        """Count of unacknowledged alerts."""
        return sum(1 for pa in self._pending.values() if not pa.acknowledged)

    def pending_summary(self) -> List[Dict[str, Any]]:
        """Summary of all unacknowledged alerts."""
        return [
            {"id": k, "level": v.level, "title": v.title,
             "age_min": round(v.age_secs() / 60, 1)}
            for k, v in self._pending.items() if not v.acknowledged
        ]

    # -----------------------------------------------------------------------
    # Telegram notifications for escalation/repeat
    # -----------------------------------------------------------------------
    def _send_escalation_notification(self, alert_id: str, pa: PendingAlert):
        """Notify operator that a WARNING has been escalated to CRITICAL."""
        remaining_min = max(0, (CRITICAL_FLATTEN_SECS - pa.age_secs()) / 60)
        try:
            from python_brain.ouroboros.claude_helper import send_telegram
            send_telegram(
                f"[!!] ESCALATED TO CRITICAL\n\n"
                f"Alert: {pa.title}\n"
                f"Age: {pa.age_secs() / 60:.0f} min (was WARNING)\n"
                f"Auto-flatten in: {remaining_min:.0f} min\n\n"
                f"Reply /ack to acknowledge"
            )
        except Exception as e:
            log.warning("Escalation notification failed: %s", e)

    def _send_repeat_notification(self, alert_id: str, pa: PendingAlert, remaining_min: float):
        """Re-send CRITICAL alert with countdown to flatten."""
        try:
            from python_brain.ouroboros.claude_helper import send_telegram
            send_telegram(
                f"[!!] CRITICAL REMINDER\n\n"
                f"Alert: {pa.title}\n"
                f"Unacknowledged for: {pa.age_secs() / 60:.0f} min\n"
                f"AUTO-FLATTEN IN: {remaining_min:.0f} MIN\n\n"
                f"Reply /ack to acknowledge"
            )
        except Exception as e:
            log.warning("Repeat notification failed: %s", e)

    # -----------------------------------------------------------------------
    # Flatten command
    # -----------------------------------------------------------------------
    def _send_flatten_command(self, alert_id: str, title: str):
        """Trigger graceful shutdown via N10a kill switch.

        The Rust engine checks /app/data/KILL every second. When detected,
        it flattens all open positions via market sell, then shuts down.
        This is the proven, tested mechanism — no need for custom command files.

        Also writes /app/data/commands/flatten.json for audit trail (Rust
        doesn't read this — it's for post-mortem analysis).
        """
        # N10a: Write the KILL file — Rust engine picks this up within 1 second
        kill_file = DATA_DIR / "KILL"
        kill_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            kill_file.write_text(
                f"escalation_timeout: {title}\nalert_id: {alert_id}\n"
                f"timestamp: {time.time()}\n",
                encoding="utf-8",
            )
            log.critical("FLATTEN: wrote %s — Rust engine will flatten + shutdown", kill_file)
        except OSError as e:
            log.error("FLATTEN: failed to write KILL file: %s", e)

        # Audit trail: also write flatten.json for post-mortem analysis
        FLATTEN_CMD_FILE.parent.mkdir(parents=True, exist_ok=True)
        cmd = {
            "command": "flatten_all",
            "reason": f"escalation_timeout: {title}",
            "alert_id": alert_id,
            "timestamp": time.time(),
        }
        try:
            FLATTEN_CMD_FILE.write_text(json.dumps(cmd), encoding="utf-8")
        except OSError:
            pass  # Audit trail is non-critical

        # Telegram emergency notification
        try:
            from python_brain.ouroboros.claude_helper import send_telegram
            send_telegram(
                f"[!!!] EMERGENCY FLATTEN\n\n"
                f"Alert: {title}\n"
                f"ID: {alert_id}\n"
                f"Reason: Unacknowledged CRITICAL alert for 60+ minutes\n\n"
                f"ALL POSITIONS BEING CLOSED\n"
                f"Engine shutting down — restart manually after review"
            )
        except Exception as e:
            log.error("FLATTEN: Telegram notification failed: %s", e)

    # -----------------------------------------------------------------------
    # State persistence
    # -----------------------------------------------------------------------
    def _save_state(self):
        """Persist pending alerts to disk (survives restarts)."""
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        state = {k: v.to_dict() for k, v in self._pending.items()}
        try:
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, default=str), encoding="utf-8")
            os.rename(str(tmp), str(STATE_FILE))
        except OSError as e:
            log.warning("Failed to save escalation state: %s", e)

    def _load_state(self):
        """Restore pending alerts from disk."""
        if not STATE_FILE.exists():
            return
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            for alert_id, d in data.items():
                self._pending[alert_id] = PendingAlert.from_dict(d)
            unacked = sum(1 for v in self._pending.values() if not v.acknowledged)
            if unacked:
                log.info("ESCALATION: restored %d pending alerts (%d unacked)",
                         len(self._pending), unacked)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load escalation state: %s", e)


# ---------------------------------------------------------------------------
# Module-level singleton + convenience function
# ---------------------------------------------------------------------------
_manager: Optional[EscalationManager] = None


def get_manager() -> EscalationManager:
    """Get or create the singleton EscalationManager."""
    global _manager
    if _manager is None:
        _manager = EscalationManager()
    return _manager


def escalation_tick() -> List[Dict[str, Any]]:
    """Run one tick of the escalation manager. Call every 60s."""
    return get_manager().tick()


# ---------------------------------------------------------------------------
# CLI: standalone watchdog mode
# ---------------------------------------------------------------------------
def main():
    """Run as a standalone escalation watchdog (every 60s)."""
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [EscalationMgr] %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="Book 58: Escalation Timeout Watchdog")
    parser.add_argument("--once", action="store_true", help="Run one tick and exit")
    parser.add_argument("--interval", type=int, default=60, help="Check interval (seconds)")
    parser.add_argument("--status", action="store_true", help="Print pending alerts and exit")
    parser.add_argument("--ack", type=str, help="Acknowledge an alert by ID")
    parser.add_argument("--ack-all", action="store_true", help="Acknowledge all pending alerts")
    args = parser.parse_args()

    mgr = get_manager()

    if args.ack:
        ok = mgr.acknowledge(args.ack)
        print(f"ACK {'OK' if ok else 'NOT FOUND'}: {args.ack}")
        return

    if args.ack_all:
        n = mgr.acknowledge_all()
        print(f"ACK_ALL: {n} alerts acknowledged")
        return

    if args.status:
        pending = mgr.pending_summary()
        if not pending:
            print("No pending alerts.")
        else:
            print(f"{len(pending)} pending alert(s):")
            for p in pending:
                print(f"  [{p['level']}] {p['id']}: {p['title']} ({p['age_min']:.0f} min)")
        return

    if args.once:
        actions = escalation_tick()
        for a in actions:
            print(f"  {a['action']}: {a.get('title', '?')} ({a.get('age_min', 0):.0f} min)")
        if not actions:
            print("No escalations needed.")
        return

    # Continuous watchdog mode
    log.info("Escalation watchdog starting (interval=%ds)", args.interval)
    while True:
        try:
            actions = escalation_tick()
            for a in actions:
                log.info("ACTION: %s", json.dumps(a, default=str))
        except Exception as e:
            log.error("Escalation tick failed: %s", e)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
