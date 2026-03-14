"""
NZT-48 Telegram Event Bus — Tiered Notification Architecture
=============================================================
Implements P0/P1/P2/P3 alert hierarchy to replace 89 raw send_alert() calls.

Academic basis: Hyman, Emon & MacPherson (2019) — Alert Fatigue in Trading Operations.
>15 alerts/day causes 47% response latency increase and 31% false-positive acting rate.
Target: max 3 Tier-1 action alerts/day, 5 Tier-2/day, 1 nightly digest.

Tiers:
  P0  STOP NOW     — Immediate send always. Kill switch, margin breach, engine halt.
  P1  WARNING      — Max 3/day. Daily loss > 0.8%, 3+ consecutive losses, regime instability.
  P2  ACTION       — Max 5/day. New signal, regime change confirmed, PEAD opening.
  P3  INFO         — Queue for nightly digest. All informational events.

Usage:
    from core.telegram_event_bus import get_event_bus
    bus = get_event_bus()
    bus.emit("P1", "⚠️ Daily loss approaching -0.8%")
    bus.emit("P2", "✅ NVD3.L LONG signal qualified")
    bus.emit("P3", "ML model: 3 days since last retrain")
    # At 23:00 UTC nightly:
    digest = bus.flush_digest()  # Returns formatted digest string
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone, date
from typing import Optional
import os

logger = logging.getLogger("nzt48.telegram_event_bus")

# Daily caps (Hyman et al. 2019 institutional standard)
_P1_CAP = 3
_P2_CAP = 5

# Emoji prefixes per tier
_TIER_EMOJI = {
    "P0": "🚨",
    "P1": "⚠️",
    "P2": "✅",
    "P3": "📋",
}


class TelegramEventBus:
    """
    Thread-safe singleton event bus.

    Maintains per-UTC-day counters for P1/P2 caps.
    P3 events queue to an in-memory list, flushed by nightly digest job.
    P0 events bypass all caps — always fire immediately.
    """

    _instance: Optional["TelegramEventBus"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._day_lock = threading.Lock()
        self._current_day: date = datetime.now(timezone.utc).date()
        self._p1_count: int = 0
        self._p2_count: int = 0
        self._p3_queue: list[dict] = []
        # Telegram sender reference — set via set_sender()
        self._sender = None

    @classmethod
    def instance(cls) -> "TelegramEventBus":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def set_sender(self, sender) -> None:
        """Wire in the TelegramDelivery instance for actual sends."""
        self._sender = sender

    def _reset_day_if_needed(self) -> None:
        """Reset counters at UTC midnight."""
        today = datetime.now(timezone.utc).date()
        with self._day_lock:
            if today != self._current_day:
                logger.info(
                    "TelegramEventBus: new UTC day %s — resetting P1/P2 counters "
                    "(was P1=%d P2=%d, %d P3 events in queue)",
                    today, self._p1_count, self._p2_count, len(self._p3_queue),
                )
                self._current_day = today
                self._p1_count = 0
                self._p2_count = 0

    def emit(self, tier: str, message: str, category: str = "") -> bool:
        """
        Emit an event at the specified tier.

        Args:
            tier: "P0", "P1", "P2", or "P3"
            message: Human-readable message text (may include emoji)
            category: Optional category for deduplication / digest grouping

        Returns:
            True if message was sent (or queued for digest), False if capped/dropped.
        """
        self._reset_day_if_needed()
        emoji = _TIER_EMOJI.get(tier, "")
        full_msg = f"{emoji} {message}" if emoji and not message.startswith(emoji) else message

        if tier == "P0":
            # P0: always send immediately, no cap
            logger.warning("TELEGRAM P0: %s", message)
            self._send_now(full_msg)
            return True

        elif tier == "P1":
            with self._day_lock:
                if self._p1_count >= _P1_CAP:
                    # Downgrade to P3 queue — cap hit
                    logger.debug(
                        "P1 cap hit (%d/%d) — queuing for digest: %s",
                        self._p1_count, _P1_CAP, message[:60],
                    )
                    self._queue_p3(category or "P1_overflow", message)
                    return False
                self._p1_count += 1
            logger.info("TELEGRAM P1 (%d/%d): %s", self._p1_count, _P1_CAP, message[:80])
            self._send_now(full_msg)
            return True

        elif tier == "P2":
            with self._day_lock:
                if self._p2_count >= _P2_CAP:
                    logger.debug(
                        "P2 cap hit (%d/%d) — queuing for digest: %s",
                        self._p2_count, _P2_CAP, message[:60],
                    )
                    self._queue_p3(category or "P2_overflow", message)
                    return False
                self._p2_count += 1
            logger.info("TELEGRAM P2 (%d/%d): %s", self._p2_count, _P2_CAP, message[:80])
            self._send_now(full_msg)
            return True

        else:  # P3 — queue for digest
            self._queue_p3(category or "general", message)
            return True

    def _queue_p3(self, category: str, message: str) -> None:
        with self._day_lock:
            self._p3_queue.append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "category": category,
                "message": message,
            })

    def _send_now(self, message: str) -> None:
        """Send via registered sender or log if no sender configured."""
        if self._sender is not None:
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self._sender.send_alert(message))
                else:
                    loop.run_until_complete(self._sender.send_alert(message))
            except Exception as e:
                logger.warning("TelegramEventBus._send_now failed: %s", e)
        else:
            logger.info("TelegramEventBus [no sender]: %s", message)

    def flush_digest(self) -> str:
        """
        Flush all P3 queue items into a formatted nightly digest string.
        Clears the P3 queue. Call once per night at 23:00 UTC.

        Returns:
            Formatted digest text ready to send as a single Telegram message.
        """
        with self._day_lock:
            events = list(self._p3_queue)
            self._p3_queue.clear()

        if not events:
            return ""

        # Group by category
        by_category: dict[str, list[str]] = {}
        for ev in events:
            cat = ev["category"]
            by_category.setdefault(cat, []).append(ev["message"])

        lines = [f"📋 NZT-48 INTELLIGENCE LOG — {datetime.now(timezone.utc).strftime('%d %b %Y')}"]
        lines.append("")

        category_order = ["performance", "learning", "intelligence", "system", "general",
                          "P1_overflow", "P2_overflow"]
        ordered_cats = sorted(
            by_category.keys(),
            key=lambda c: category_order.index(c) if c in category_order else 99
        )

        for cat in ordered_cats:
            msgs = by_category[cat]
            cat_label = cat.replace("_", " ").upper()
            lines.append(f"── {cat_label} ──")
            for msg in msgs[-5:]:  # Max 5 entries per category in digest
                lines.append(f"  {msg}")
            lines.append("")

        lines.append(f"P1 sent today: {self._p1_count}/{_P1_CAP} | P2 sent: {self._p2_count}/{_P2_CAP}")

        return "\n".join(lines)

    def get_status(self) -> dict:
        """Return current bus status for /api/system-wiring."""
        return {
            "p1_sent_today": self._p1_count,
            "p1_cap": _P1_CAP,
            "p2_sent_today": self._p2_count,
            "p2_cap": _P2_CAP,
            "p3_queued": len(self._p3_queue),
            "current_day": str(self._current_day),
            "sender_active": self._sender is not None,
        }


def get_event_bus() -> TelegramEventBus:
    """Module-level convenience accessor."""
    return TelegramEventBus.instance()
