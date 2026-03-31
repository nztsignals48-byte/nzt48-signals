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


# ---------------------------------------------------------------------------
# AEGIS Error Catalog — Book 58 extensions
# ---------------------------------------------------------------------------

import re


class ErrorCategory(Enum):
    """Error categories for AEGIS error classification."""
    A_DATA = "A_DATA"
    B_CONNECTION = "B_CONNECTION"
    C_ORDER = "C_ORDER"
    D_RISK = "D_RISK"
    E_SYSTEM = "E_SYSTEM"
    F_LOGIC = "F_LOGIC"
    G_EXTERNAL = "G_EXTERNAL"


class SeverityLevel(Enum):
    """6-level severity scale for AEGIS alerts."""
    S1_TRACE = 1
    S2_DEBUG = 2
    S3_INFO = 3
    S4_WARNING = 4
    S5_CRITICAL = 5
    S6_EMERGENCY = 6


# ~50 key error codes → (category, severity, description, remediation)
AEGIS_ERROR_CATALOG: Dict[str, tuple] = {
    # A: Data errors (A001–A010)
    "A001": (ErrorCategory.A_DATA, SeverityLevel.S4_WARNING, "Stale tick data (>60s old)", "Check IBKR data feed connection"),
    "A002": (ErrorCategory.A_DATA, SeverityLevel.S5_CRITICAL, "WAL write failure", "Check disk space and WAL directory permissions"),
    "A003": (ErrorCategory.A_DATA, SeverityLevel.S4_WARNING, "Data gap >5 minutes detected", "Verify market hours; check IBKR subscription"),
    "A004": (ErrorCategory.A_DATA, SeverityLevel.S3_INFO, "Missing indicator value (NaN)", "Indicator will use fallback; check input data"),
    "A005": (ErrorCategory.A_DATA, SeverityLevel.S5_CRITICAL, "WAL corruption detected", "Stop engine, repair WAL from backup, restart"),
    "A006": (ErrorCategory.A_DATA, SeverityLevel.S4_WARNING, "Tick timestamp out of order", "Check clock sync; data feed may be replaying"),
    "A007": (ErrorCategory.A_DATA, SeverityLevel.S3_INFO, "Contract definition missing", "Add to contracts.toml and reload"),
    "A008": (ErrorCategory.A_DATA, SeverityLevel.S4_WARNING, "Price spike >10 ATR detected", "Likely bad tick; will be filtered by Rust"),
    "A009": (ErrorCategory.A_DATA, SeverityLevel.S5_CRITICAL, "No ticks received for any symbol >5min", "IBKR connection likely dead; restart gateway"),
    "A010": (ErrorCategory.A_DATA, SeverityLevel.S4_WARNING, "RVOL data unavailable", "Volume analytics degraded; using fallback RVOL=1.0"),

    # B: Connection errors (B001–B010)
    "B001": (ErrorCategory.B_CONNECTION, SeverityLevel.S5_CRITICAL, "IBKR gateway disconnected", "Check IB Gateway process; restart if needed"),
    "B002": (ErrorCategory.B_CONNECTION, SeverityLevel.S4_WARNING, "IBKR reconnect attempt", "Auto-reconnecting; monitor for B001 escalation"),
    "B003": (ErrorCategory.B_CONNECTION, SeverityLevel.S4_WARNING, "Telegram API unreachable", "Alerts will queue; check network connectivity"),
    "B004": (ErrorCategory.B_CONNECTION, SeverityLevel.S3_INFO, "Claude CLI timeout (>30s)", "Curation fallback active (10% haircut)"),
    "B005": (ErrorCategory.B_CONNECTION, SeverityLevel.S5_CRITICAL, "Bridge→Rust IPC failure", "Signals cannot reach engine; restart bridge.py"),
    "B006": (ErrorCategory.B_CONNECTION, SeverityLevel.S4_WARNING, "DuckDB connection lost", "Warehouse queries degraded; will auto-reconnect"),
    "B007": (ErrorCategory.B_CONNECTION, SeverityLevel.S3_INFO, "Gemini API rate limited", "Morning brief may be delayed; using cached data"),
    "B008": (ErrorCategory.B_CONNECTION, SeverityLevel.S5_CRITICAL, "SSH tunnel to EC2 dropped", "Re-establish tunnel; check EC2 instance health"),
    "B009": (ErrorCategory.B_CONNECTION, SeverityLevel.S4_WARNING, "WebSocket heartbeat missed", "Reconnecting data stream; may miss 1-2 ticks"),
    "B010": (ErrorCategory.B_CONNECTION, SeverityLevel.S3_INFO, "RevenueCat webhook timeout", "Non-critical for trading; retry queued"),

    # C: Order errors (C001–C010)
    "C001": (ErrorCategory.C_ORDER, SeverityLevel.S5_CRITICAL, "Order rejected by broker", "Check order params, buying power, and position limits"),
    "C002": (ErrorCategory.C_ORDER, SeverityLevel.S4_WARNING, "Partial fill on entry", "Position sized incorrectly; monitor for completion"),
    "C003": (ErrorCategory.C_ORDER, SeverityLevel.S5_CRITICAL, "Order stuck in SUBMITTED >5min", "Cancel and resubmit; check IBKR TWS for errors"),
    "C004": (ErrorCategory.C_ORDER, SeverityLevel.S4_WARNING, "Slippage exceeds 2x expected", "Liquidity may be thin; consider reducing size"),
    "C005": (ErrorCategory.C_ORDER, SeverityLevel.S5_CRITICAL, "Duplicate order detected", "Kill switch check; may need manual cancellation"),
    "C006": (ErrorCategory.C_ORDER, SeverityLevel.S4_WARNING, "Exit order rejected — position still open", "Retry exit; if persistent, manual close needed"),
    "C007": (ErrorCategory.C_ORDER, SeverityLevel.S3_INFO, "Order filled at better price than limit", "Positive slippage — no action needed"),
    "C008": (ErrorCategory.C_ORDER, SeverityLevel.S4_WARNING, "Chandelier stop triggered during halt", "Price may gap on resume; monitor closely"),
    "C009": (ErrorCategory.C_ORDER, SeverityLevel.S5_CRITICAL, "Position size exceeds ISA limit", "Risk gate should have caught this; investigate"),
    "C010": (ErrorCategory.C_ORDER, SeverityLevel.S4_WARNING, "Market order used instead of limit", "Check order type logic; limit preferred for cost control"),

    # D: Risk errors (D001–D010)
    "D001": (ErrorCategory.D_RISK, SeverityLevel.S5_CRITICAL, "Drawdown exceeds sacred limit", "Auto-halt triggered; review before restart"),
    "D002": (ErrorCategory.D_RISK, SeverityLevel.S4_WARNING, "Portfolio heat approaching limit", "Reduce new entries; let existing trades resolve"),
    "D003": (ErrorCategory.D_RISK, SeverityLevel.S5_CRITICAL, "Correlation breach — positions too correlated", "Close most correlated position; diversify"),
    "D004": (ErrorCategory.D_RISK, SeverityLevel.S4_WARNING, "Overnight exposure exceeds threshold", "Review overnight hold rules; may need to flatten"),
    "D005": (ErrorCategory.D_RISK, SeverityLevel.S6_EMERGENCY, "Multiple risk gates failing simultaneously", "Likely systemic event; flatten all and halt"),
    "D006": (ErrorCategory.D_RISK, SeverityLevel.S4_WARNING, "Kelly fraction at upper bound", "Sizing capped; review if sustained"),
    "D007": (ErrorCategory.D_RISK, SeverityLevel.S5_CRITICAL, "Monte Carlo ruin probability >10%", "Strategy may need retirement; run full analysis"),
    "D008": (ErrorCategory.D_RISK, SeverityLevel.S4_WARNING, "Regime shift detected — strategy mismatch", "Monitor; lifecycle may auto-suspend"),
    "D009": (ErrorCategory.D_RISK, SeverityLevel.S3_INFO, "VaR limit 80% utilized", "Approaching risk capacity; reduce new signals"),
    "D010": (ErrorCategory.D_RISK, SeverityLevel.S4_WARNING, "Spread drag exceeding 50% of edge", "Cost erosion high; consider ticker blacklist"),

    # E: System errors (E001–E005)
    "E001": (ErrorCategory.E_SYSTEM, SeverityLevel.S6_EMERGENCY, "Rust engine panic/crash", "Check crash log; restart engine; investigate core dump"),
    "E002": (ErrorCategory.E_SYSTEM, SeverityLevel.S5_CRITICAL, "Disk space <5% free", "Purge old WAL archives; expand volume if on EC2"),
    "E003": (ErrorCategory.E_SYSTEM, SeverityLevel.S5_CRITICAL, "Memory usage >90%", "Check for leaks; restart bridge.py; increase swap"),
    "E004": (ErrorCategory.E_SYSTEM, SeverityLevel.S4_WARNING, "Docker container restart detected", "Check logs for OOM or crash; may lose state"),
    "E005": (ErrorCategory.E_SYSTEM, SeverityLevel.S3_INFO, "Config reload (SIGHUP) processed", "Normal operation after gate_tuning auto-apply"),

    # F: Logic errors (F001–F005)
    "F001": (ErrorCategory.F_LOGIC, SeverityLevel.S5_CRITICAL, "Signal confidence outside [0,100]", "Clamp and log; investigate signal generator"),
    "F002": (ErrorCategory.F_LOGIC, SeverityLevel.S4_WARNING, "Bayesian prior update divergence", "Prior may be stale; reset calibration"),
    "F003": (ErrorCategory.F_LOGIC, SeverityLevel.S5_CRITICAL, "Strategy generated NaN/Inf signal", "Schema validation should catch; check upstream"),
    "F004": (ErrorCategory.F_LOGIC, SeverityLevel.S4_WARNING, "Thompson sampling exploration spike", "Expected occasionally; monitor for persistence"),
    "F005": (ErrorCategory.F_LOGIC, SeverityLevel.S3_INFO, "Gate tuning recommendation clipped to bounds", "Normal operation — hard bounds enforced"),

    # G: External errors (G001–G005)
    "G001": (ErrorCategory.G_EXTERNAL, SeverityLevel.S4_WARNING, "Market halt detected", "Pause signal generation; resume on unhalt"),
    "G002": (ErrorCategory.G_EXTERNAL, SeverityLevel.S3_INFO, "Exchange early close schedule", "Adjust session timing; reduce late-day entries"),
    "G003": (ErrorCategory.G_EXTERNAL, SeverityLevel.S5_CRITICAL, "Broker margin call received", "Flatten to meet margin; investigate sizing"),
    "G004": (ErrorCategory.G_EXTERNAL, SeverityLevel.S4_WARNING, "FOMC/CPI event within 30 minutes", "Reduce new entries; widen stops on existing"),
    "G005": (ErrorCategory.G_EXTERNAL, SeverityLevel.S3_INFO, "Market session open/close", "Normal event; session timing adjusted"),
}

# Regex patterns for auto-classifying error messages to AEGIS codes
_ERROR_PATTERNS: List[tuple] = [
    # A: Data
    (r"stale.*tick|tick.*stale|data.*stale", "A001"),
    (r"wal.*write.*fail|write.*wal.*fail|failed.*write.*wal", "A002"),
    (r"data.*gap|gap.*data|no.*tick.*\d+\s*min", "A003"),
    (r"nan|NaN|indicator.*missing|missing.*indicator", "A004"),
    (r"wal.*corrupt|corrupt.*wal", "A005"),
    (r"timestamp.*order|out.*of.*order.*tick", "A006"),
    (r"contract.*missing|missing.*contract", "A007"),
    (r"price.*spike|spike.*price|outlier.*tick", "A008"),
    (r"no.*ticks.*received|zero.*ticks", "A009"),
    (r"rvol.*unavail|volume.*unavail", "A010"),
    # B: Connection
    (r"ibkr.*disconnect|gateway.*disconnect|ib.*disconnect", "B001"),
    (r"ibkr.*reconnect|reconnect.*ibkr", "B002"),
    (r"telegram.*unreachable|telegram.*fail|telegram.*timeout", "B003"),
    (r"claude.*timeout|cli.*timeout|curator.*timeout", "B004"),
    (r"bridge.*ipc.*fail|ipc.*fail|rust.*ipc", "B005"),
    (r"duckdb.*connection|duckdb.*lost", "B006"),
    (r"gemini.*rate.*limit|gemini.*429", "B007"),
    (r"ssh.*tunnel.*drop|ec2.*unreachable", "B008"),
    (r"websocket.*heartbeat|heartbeat.*miss", "B009"),
    # C: Order
    (r"order.*reject|rejected.*order|broker.*reject", "C001"),
    (r"partial.*fill|fill.*partial", "C002"),
    (r"order.*stuck|stuck.*submitted", "C003"),
    (r"slippage.*exceed|high.*slippage", "C004"),
    (r"duplicate.*order|order.*duplicate", "C005"),
    (r"exit.*reject|reject.*exit.*order", "C006"),
    (r"position.*size.*exceed|exceed.*isa.*limit", "C009"),
    # D: Risk
    (r"drawdown.*exceed|sacred.*limit|drawdown.*halt", "D001"),
    (r"portfolio.*heat|heat.*limit", "D002"),
    (r"correlation.*breach|too.*correlated", "D003"),
    (r"overnight.*exposure|overnight.*exceed", "D004"),
    (r"multiple.*risk.*gate|risk.*gate.*fail", "D005"),
    (r"kelly.*bound|kelly.*cap", "D006"),
    (r"ruin.*prob|monte.*carlo.*ruin", "D007"),
    (r"regime.*shift|regime.*mismatch", "D008"),
    (r"spread.*drag|cost.*erosion", "D010"),
    # E: System
    (r"rust.*panic|engine.*crash|engine.*panic", "E001"),
    (r"disk.*space|disk.*full|no.*space.*left", "E002"),
    (r"memory.*usage|oom|out.*of.*memory", "E003"),
    (r"docker.*restart|container.*restart", "E004"),
    (r"sighup|config.*reload", "E005"),
    # F: Logic
    (r"confidence.*outside|confidence.*invalid|confidence.*range", "F001"),
    (r"bayesian.*diverge|prior.*diverge", "F002"),
    (r"nan.*signal|inf.*signal|signal.*nan|signal.*inf", "F003"),
    (r"thompson.*spike|exploration.*spike", "F004"),
    (r"clipped.*bounds|recommendation.*clipped", "F005"),
    # G: External
    (r"market.*halt|trading.*halt|halt.*detected", "G001"),
    (r"early.*close|exchange.*close.*early", "G002"),
    (r"margin.*call|margin.*requirement", "G003"),
    (r"fomc|cpi.*event|fed.*meeting", "G004"),
]


def classify_error(error_msg: str) -> tuple:
    """Classify an error message to an AEGIS error code via regex pattern matching.

    Args:
        error_msg: raw error message string

    Returns:
        (code, category, severity, description, remediation) or
        ("UNKNOWN", ErrorCategory.E_SYSTEM, SeverityLevel.S4_WARNING, error_msg, "Investigate manually")
    """
    msg_lower = error_msg.lower()
    for pattern, code in _ERROR_PATTERNS:
        if re.search(pattern, msg_lower):
            if code in AEGIS_ERROR_CATALOG:
                cat, sev, desc, remed = AEGIS_ERROR_CATALOG[code]
                return (code, cat, sev, desc, remed)
    return ("UNKNOWN", ErrorCategory.E_SYSTEM, SeverityLevel.S4_WARNING, error_msg, "Investigate manually")


# Known cascade sequences: initial error → downstream errors
_CASCADE_MAP: Dict[str, List[str]] = {
    "B001": ["A009", "A003", "C003"],          # IBKR disconnect → no ticks → data gap → stuck orders
    "E002": ["A002", "A005"],                   # Disk full → WAL write fail → WAL corruption
    "E001": ["B005", "C003", "D005"],           # Rust panic → IPC fail → stuck orders → multi-gate fail
    "A009": ["A003", "D008"],                   # No ticks → data gap → regime mismatch (stale data)
    "B005": ["C003", "C006"],                   # IPC fail → stuck orders → exit rejection
    "D001": ["D005"],                           # Drawdown breach → multi-gate fail (sacred halt)
    "G003": ["D001", "D005"],                   # Margin call → drawdown → multi-gate fail
    "E003": ["E001", "B005"],                   # OOM → engine crash → IPC fail
}


def cascade_chain(initial_code: str) -> List[str]:
    """Return known cascade sequence for an initial error code.

    Args:
        initial_code: AEGIS error code (e.g. "B001")

    Returns:
        List of downstream error codes that commonly follow, or empty list.
    """
    return _CASCADE_MAP.get(initial_code, [])
