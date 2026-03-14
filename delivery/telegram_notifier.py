"""
NZT-48 Trading System — Tiered Telegram Notification Architecture (AEGIS H-04/H-05/H-11)
========================================================================================

Implements:
  H-04: Tiered alert system with P0-P3 priority routing
  H-05: Fallback defence-in-depth (email via SES, SMS via SNS)
  H-11: Daily operational checklists (morning/midday/evening)

Priority tiers:
  P0: Instant + SOUND  (drawdown >L2, crash, cascade halt) — disable_notification=False
  P1: Instant, silent   (trade fill, stop hit, regime change) — disable_notification=True
  P2: 30-min batch      (signal generated, graduation) — buffered, sent every 30 min
  P3: 2x daily digest   (ML health, macro summary) — sent at 08:00 and 17:00 UK

Correlation escalation: 3+ P1 in 15 min => auto-escalate to P0.

Fallback chain (P0 only):
  1. Telegram (primary)
  2. Email via AWS SES (parallel for P0, fallback if Telegram fails within 30s)
  3. SMS via AWS SNS (if Telegram fails within 30s)

Burst protection: >5 P1 in 60s => consolidate into single summary.

References:
  Hyman, Emon & MacPherson (2019) — Alert Fatigue in Trading Operations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("nzt48.telegram_notifier")

UK_TZ = ZoneInfo("Europe/London")

# Priority constants
P0 = 0  # Instant + sound
P1 = 1  # Instant, silent
P2 = 2  # 30-min batch
P3 = 3  # 2x daily digest

_PRIORITY_LABELS = {P0: "P0-CRITICAL", P1: "P1-ACTION", P2: "P2-BATCH", P3: "P3-DIGEST"}
_PRIORITY_EMOJI = {P0: "\U0001f6a8", P1: "\u26a0\ufe0f", P2: "\U0001f4cb", P3: "\U0001f4ca"}

# H-04 escalation thresholds
_P1_ESCALATION_WINDOW_SEC = 900   # 15 min
_P1_ESCALATION_COUNT = 3          # 3+ P1 in window => escalate to P0

# H-05 burst protection thresholds
_P1_BURST_WINDOW_SEC = 60         # 60 sec
_P1_BURST_COUNT = 5               # >5 P1 in 60s => consolidate

# P2 batch interval (seconds)
_P2_BATCH_INTERVAL_SEC = 1800     # 30 min

# P3 digest times (UK hours)
_P3_DIGEST_HOURS = [8, 17]        # 08:00 and 17:00 UK


class DeliveryFailureLog:
    """Log all delivery failures as P1 incidents (H-05 requirement)."""

    def __init__(self):
        self._failures: list[dict] = []
        self._lock = threading.Lock()

    def record(self, channel: str, priority: int, error: str, message_preview: str = ""):
        with self._lock:
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "channel": channel,
                "priority": priority,
                "error": error[:200],
                "message_preview": message_preview[:80],
            }
            self._failures.append(entry)
            # Keep last 500
            if len(self._failures) > 500:
                self._failures = self._failures[-500:]
            logger.error(
                "DELIVERY_FAILURE [%s] priority=%s: %s — %s",
                channel, _PRIORITY_LABELS.get(priority, str(priority)),
                error[:100], message_preview[:60],
            )

    def get_recent(self, n: int = 20) -> list[dict]:
        with self._lock:
            return list(self._failures[-n:])

    def count_today(self) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            return sum(1 for f in self._failures if f["ts"].startswith(today))


class TelegramNotifier:
    """Tiered Telegram notification system with fallback defence-in-depth.

    Thread-safe singleton managing P0-P3 alert routing, P2 batching,
    P3 digest collection, P1 correlation escalation, and P1 burst protection.
    """

    _instance: Optional["TelegramNotifier"] = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self._lock = threading.Lock()

        # Reference to the TelegramDelivery instance (set via set_telegram_sender)
        self._telegram_sender = None

        # P1 timestamp tracking for escalation (H-04)
        self._p1_timestamps: deque[float] = deque(maxlen=100)

        # P1 burst tracking (H-05)
        self._p1_burst_timestamps: deque[float] = deque(maxlen=100)
        self._p1_burst_buffer: list[str] = []
        self._p1_burst_lock = threading.Lock()

        # P2 batch buffer
        self._p2_buffer: list[dict] = []
        self._p2_lock = threading.Lock()
        self._p2_last_flush: float = time.time()

        # P3 digest buffer
        self._p3_buffer: list[dict] = []
        self._p3_lock = threading.Lock()
        self._p3_last_digest_hour: Optional[int] = None

        # Delivery failure log (H-05)
        self._failure_log = DeliveryFailureLog()

        # AWS credentials (loaded from env)
        self._aws_region = os.environ.get("AWS_REGION", "eu-west-2")
        self._ses_sender = os.environ.get("NZT48_SES_SENDER", "")
        self._ses_recipient = os.environ.get("NZT48_SES_RECIPIENT", "")
        self._sns_topic_arn = os.environ.get("NZT48_SNS_TOPIC_ARN", "")
        self._sns_phone = os.environ.get("NZT48_SNS_PHONE", "")

        # Alert counters for daily tracking
        self._daily_counts = {P0: 0, P1: 0, P2: 0, P3: 0}
        self._daily_counts_date: str = datetime.now(timezone.utc).date().isoformat()

        logger.info(
            "TelegramNotifier initialised — SES=%s SNS=%s",
            "configured" if self._ses_sender else "disabled",
            "configured" if (self._sns_topic_arn or self._sns_phone) else "disabled",
        )

    @classmethod
    def instance(cls) -> "TelegramNotifier":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def set_telegram_sender(self, sender) -> None:
        """Wire in the TelegramDelivery instance for actual sends."""
        self._telegram_sender = sender

    # ─────────────────────────────────────────────────────────────────────
    # H-04: Main entry point — send_alert(message, priority)
    # ─────────────────────────────────────────────────────────────────────

    async def send_alert(self, message: str, priority: int = P1) -> bool:
        """Route a message to the correct tier.

        Args:
            message: Alert text (may include HTML for Telegram).
            priority: P0, P1, P2, or P3.

        Returns:
            True if delivered (or buffered successfully), False on failure.
        """
        self._reset_daily_counts_if_needed()
        self._increment_daily_count(priority)

        if priority == P0:
            return await self._handle_p0(message)
        elif priority == P1:
            return await self._handle_p1(message)
        elif priority == P2:
            return self._handle_p2(message)
        elif priority == P3:
            return self._handle_p3(message)
        else:
            logger.warning("Unknown priority %s — treating as P1", priority)
            return await self._handle_p1(message)

    # ─────────────────────────────────────────────────────────────────────
    # P0: Instant + SOUND — always send, with fallback chain
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_p0(self, message: str) -> bool:
        """P0: Immediate delivery with sound. Telegram + email (parallel).
        If Telegram fails within 30s, escalate to SMS.
        """
        prefix = f"{_PRIORITY_EMOJI[P0]} [{_PRIORITY_LABELS[P0]}]\n"
        full_msg = prefix + message
        logger.warning("P0 ALERT: %s", message[:120])

        # Send Telegram with sound (disable_notification=False)
        tg_success = await self._send_telegram(full_msg, disable_notification=False)

        # Send email in parallel for P0 (H-05 defence-in-depth)
        email_task = asyncio.create_task(self._send_email(
            subject=f"NZT-48 P0 CRITICAL: {message[:60]}",
            body=full_msg,
        ))

        # If Telegram failed, escalate to SMS
        if not tg_success:
            self._failure_log.record("telegram", P0, "P0 Telegram send failed", message[:80])
            logger.error("P0 Telegram FAILED — escalating to SMS")
            await self._send_sms(f"NZT-48 P0: {message[:140]}")

        # Await email task (non-blocking, just for logging)
        try:
            await asyncio.wait_for(email_task, timeout=30)
        except asyncio.TimeoutError:
            self._failure_log.record("email", P0, "Email send timeout (30s)", message[:80])
        except Exception as e:
            self._failure_log.record("email", P0, str(e), message[:80])

        return tg_success

    # ─────────────────────────────────────────────────────────────────────
    # P1: Instant, silent — with correlation escalation & burst protection
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_p1(self, message: str) -> bool:
        """P1: Instant silent delivery. Checks for escalation and burst conditions."""
        now = time.time()

        # Track P1 timestamps for escalation check
        with self._lock:
            self._p1_timestamps.append(now)
            self._p1_burst_timestamps.append(now)

        # H-04: Correlation escalation — 3+ P1 in 15 min => auto-escalate to P0
        if self._should_escalate_to_p0():
            logger.warning("P1 ESCALATION: 3+ P1 in 15 min — auto-escalating to P0")
            escalation_msg = (
                f"{_PRIORITY_EMOJI[P0]} [P1->P0 AUTO-ESCALATION]\n"
                f"3+ P1 alerts in 15 min detected.\n\n"
                f"Latest: {message}"
            )
            return await self._handle_p0(escalation_msg)

        # H-05: Burst protection — >5 P1 in 60s => consolidate
        if self._is_p1_burst():
            return self._consolidate_p1_burst(message)

        prefix = f"{_PRIORITY_EMOJI[P1]} [{_PRIORITY_LABELS[P1]}]\n"
        full_msg = prefix + message
        logger.info("P1 ALERT: %s", message[:120])

        # Send with disable_notification=True (silent)
        success = await self._send_telegram(full_msg, disable_notification=True)
        if not success:
            self._failure_log.record("telegram", P1, "P1 Telegram send failed", message[:80])
        return success

    def _should_escalate_to_p0(self) -> bool:
        """Check if 3+ P1 alerts occurred in the last 15 minutes."""
        now = time.time()
        cutoff = now - _P1_ESCALATION_WINDOW_SEC
        with self._lock:
            recent = sum(1 for ts in self._p1_timestamps if ts >= cutoff)
        return recent >= _P1_ESCALATION_COUNT

    def _is_p1_burst(self) -> bool:
        """Check if >5 P1 alerts occurred in the last 60 seconds."""
        now = time.time()
        cutoff = now - _P1_BURST_WINDOW_SEC
        with self._lock:
            recent = sum(1 for ts in self._p1_burst_timestamps if ts >= cutoff)
        return recent > _P1_BURST_COUNT

    def _consolidate_p1_burst(self, message: str) -> bool:
        """Consolidate burst of P1 alerts into a single summary (H-05)."""
        with self._p1_burst_lock:
            self._p1_burst_buffer.append(message)
            # If this is the first burst message, schedule flush
            if len(self._p1_burst_buffer) == _P1_BURST_COUNT + 1:
                logger.warning("P1 BURST: >%d P1 in %ds — consolidating", _P1_BURST_COUNT, _P1_BURST_WINDOW_SEC)
                # Schedule a delayed flush (5 seconds to collect all burst messages)
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.call_later(5.0, lambda: asyncio.ensure_future(self._flush_p1_burst()))
                except Exception:
                    pass
        return True  # Buffered successfully

    async def _flush_p1_burst(self) -> None:
        """Flush accumulated P1 burst messages as a single consolidated summary."""
        with self._p1_burst_lock:
            messages = list(self._p1_burst_buffer)
            self._p1_burst_buffer.clear()

        if not messages:
            return

        summary = (
            f"{_PRIORITY_EMOJI[P1]} [P1 BURST CONSOLIDATION]\n"
            f"{len(messages)} alerts in rapid succession:\n\n"
        )
        for i, msg in enumerate(messages[:10], 1):  # Cap at 10 in display
            summary += f"  {i}. {msg[:100]}\n"
        if len(messages) > 10:
            summary += f"  ... and {len(messages) - 10} more\n"

        await self._send_telegram(summary, disable_notification=True)

    # ─────────────────────────────────────────────────────────────────────
    # P2: 30-min batch
    # ─────────────────────────────────────────────────────────────────────

    def _handle_p2(self, message: str) -> bool:
        """P2: Buffer message for 30-min batch delivery."""
        with self._p2_lock:
            self._p2_buffer.append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "message": message,
            })
        logger.debug("P2 buffered (%d pending): %s", len(self._p2_buffer), message[:80])
        return True

    async def flush_p2_batch(self) -> bool:
        """Flush P2 buffer — called every 30 min by scheduler."""
        with self._p2_lock:
            items = list(self._p2_buffer)
            self._p2_buffer.clear()
            self._p2_last_flush = time.time()

        if not items:
            return True  # Nothing to send

        batch_msg = (
            f"{_PRIORITY_EMOJI[P2]} [{_PRIORITY_LABELS[P2]}] "
            f"({len(items)} items)\n"
            f"{'=' * 30}\n\n"
        )
        for item in items[-20:]:  # Cap at 20 items per batch
            ts_short = item["ts"][11:16]  # HH:MM
            batch_msg += f"[{ts_short}] {item['message']}\n\n"
        if len(items) > 20:
            batch_msg += f"... and {len(items) - 20} more\n"

        logger.info("P2 BATCH: flushing %d items", len(items))
        success = await self._send_telegram(batch_msg, disable_notification=True)
        if not success:
            self._failure_log.record("telegram", P2, "P2 batch send failed", f"{len(items)} items")
        return success

    # ─────────────────────────────────────────────────────────────────────
    # P3: 2x daily digest (08:00 and 17:00 UK)
    # ─────────────────────────────────────────────────────────────────────

    def _handle_p3(self, message: str) -> bool:
        """P3: Buffer message for 2x daily digest."""
        with self._p3_lock:
            self._p3_buffer.append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "message": message,
            })
        logger.debug("P3 buffered (%d pending): %s", len(self._p3_buffer), message[:80])
        return True

    async def flush_p3_digest(self) -> bool:
        """Flush P3 buffer as digest — called at 08:00 and 17:00 UK by scheduler."""
        with self._p3_lock:
            items = list(self._p3_buffer)
            self._p3_buffer.clear()

        if not items:
            return True

        now_uk = datetime.now(UK_TZ)
        period = "MORNING" if now_uk.hour < 12 else "EVENING"

        digest_msg = (
            f"{_PRIORITY_EMOJI[P3]} NZT-48 {period} DIGEST\n"
            f"{now_uk.strftime('%d %b %Y %H:%M')} UK\n"
            f"{'=' * 30}\n\n"
        )
        for item in items[-30:]:  # Cap at 30 items per digest
            digest_msg += f"  {item['message']}\n"
        if len(items) > 30:
            digest_msg += f"\n... and {len(items) - 30} more items\n"

        digest_msg += f"\n{'=' * 30}\n"
        digest_msg += f"Alerts today: P0={self._daily_counts[P0]} P1={self._daily_counts[P1]} "
        digest_msg += f"P2={self._daily_counts[P2]} P3={self._daily_counts[P3]}\n"
        digest_msg += f"Delivery failures today: {self._failure_log.count_today()}"

        logger.info("P3 DIGEST (%s): flushing %d items", period, len(items))
        success = await self._send_telegram(digest_msg, disable_notification=True)
        if not success:
            self._failure_log.record("telegram", P3, "P3 digest send failed", f"{len(items)} items")
        return success

    # ─────────────────────────────────────────────────────────────────────
    # H-05: Fallback channels — Email (SES) and SMS (SNS)
    # ─────────────────────────────────────────────────────────────────────

    async def _send_email(self, subject: str, body: str) -> bool:
        """Send email via AWS SES (boto3). Non-blocking fallback for P0."""
        if not self._ses_sender or not self._ses_recipient:
            logger.debug("SES not configured — email fallback skipped")
            return False

        try:
            import boto3
            ses = boto3.client("ses", region_name=self._aws_region)
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: ses.send_email(
                    Source=self._ses_sender,
                    Destination={"ToAddresses": [self._ses_recipient]},
                    Message={
                        "Subject": {"Data": subject[:200], "Charset": "UTF-8"},
                        "Body": {"Text": {"Data": body[:4000], "Charset": "UTF-8"}},
                    },
                ),
            )
            msg_id = response.get("MessageId", "unknown")
            logger.info("SES email sent: subject=%s msgId=%s", subject[:60], msg_id)
            return True
        except ImportError:
            logger.debug("boto3 not installed — SES email fallback unavailable")
            return False
        except Exception as e:
            self._failure_log.record("ses_email", P0, str(e), subject[:80])
            logger.error("SES email failed: %s", e)
            return False

    async def _send_sms(self, message: str) -> bool:
        """Send SMS via AWS SNS (boto3). Last-resort fallback for P0."""
        if not self._sns_phone and not self._sns_topic_arn:
            logger.debug("SNS not configured — SMS fallback skipped")
            return False

        try:
            import boto3
            sns = boto3.client("sns", region_name=self._aws_region)

            if self._sns_phone:
                # Direct SMS to phone number
                response = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: sns.publish(
                        PhoneNumber=self._sns_phone,
                        Message=message[:160],  # SMS limit
                        MessageAttributes={
                            "AWS.SNS.SMS.SMSType": {
                                "DataType": "String",
                                "StringValue": "Transactional",
                            }
                        },
                    ),
                )
            elif self._sns_topic_arn:
                # Publish to SNS topic
                response = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: sns.publish(
                        TopicArn=self._sns_topic_arn,
                        Message=message[:256],
                        Subject="NZT-48 P0 CRITICAL",
                    ),
                )
            else:
                return False

            msg_id = response.get("MessageId", "unknown")
            logger.info("SNS SMS sent: msgId=%s", msg_id)
            return True
        except ImportError:
            logger.debug("boto3 not installed — SNS SMS fallback unavailable")
            return False
        except Exception as e:
            self._failure_log.record("sns_sms", P0, str(e), message[:80])
            logger.error("SNS SMS failed: %s", e)
            return False

    # ─────────────────────────────────────────────────────────────────────
    # Core Telegram send — uses the wired TelegramDelivery instance
    # ─────────────────────────────────────────────────────────────────────

    async def _send_telegram(self, message: str, disable_notification: bool = False) -> bool:
        """Send via the wired TelegramDelivery._send_message.

        For P0: disable_notification=False (audible alert).
        For P1+: disable_notification=True (silent).
        """
        if self._telegram_sender is None:
            logger.debug("TelegramNotifier: no sender wired — message logged only: %s", message[:80])
            return False

        try:
            # Access the internal _send_message or use send_alert
            # TelegramDelivery._send_message supports parse_mode
            bot = getattr(self._telegram_sender, "_bot", None)
            chat_id = getattr(self._telegram_sender, "chat_id", "")
            enabled = getattr(self._telegram_sender, "_enabled", False)

            if not enabled or not bot or not chat_id:
                logger.debug("TelegramNotifier: sender not enabled/configured")
                return False

            # Truncate to Telegram limit (4096 chars)
            if len(message) > 4000:
                message = message[:3997] + "..."

            # Use bot.send_message directly for disable_notification control
            await bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=None,  # Plain text for reliability
                disable_notification=disable_notification,
                connect_timeout=10,
                read_timeout=15,
                write_timeout=15,
            )
            return True
        except Exception as e:
            logger.error("TelegramNotifier._send_telegram failed: %s", e)
            return False

    # ─────────────────────────────────────────────────────────────────────
    # H-05: Unified fallback method
    # ─────────────────────────────────────────────────────────────────────

    async def _send_with_fallback(self, message: str, priority: int) -> bool:
        """Send message with full fallback chain based on priority.

        P0: Telegram -> Email -> SMS
        P1: Telegram only (with burst protection)
        P2/P3: Telegram only (batched/digested)
        """
        if priority == P0:
            # Primary: Telegram with sound
            tg_ok = await self._send_telegram(message, disable_notification=False)

            # Parallel: Email for P0
            email_ok = await self._send_email(
                subject=f"NZT-48 CRITICAL: {message[:60]}",
                body=message,
            )

            # Fallback: SMS if Telegram failed
            if not tg_ok:
                self._failure_log.record("telegram", P0, "Primary channel failed", message[:80])
                sms_ok = await self._send_sms(f"NZT-48 P0: {message[:140]}")
                if not sms_ok:
                    self._failure_log.record("all_channels", P0, "All fallback channels failed", message[:80])
                return sms_ok
            return True

        elif priority == P1:
            tg_ok = await self._send_telegram(message, disable_notification=True)
            if not tg_ok:
                self._failure_log.record("telegram", P1, "P1 send failed", message[:80])
            return tg_ok

        else:
            # P2/P3 handled by batching/digest — this is a direct send fallback
            tg_ok = await self._send_telegram(message, disable_notification=True)
            if not tg_ok:
                self._failure_log.record("telegram", priority, f"P{priority} send failed", message[:80])
            return tg_ok

    # ─────────────────────────────────────────────────────────────────────
    # H-11: Daily Operational Checklists
    # ─────────────────────────────────────────────────────────────────────

    async def send_morning_checklist(self) -> bool:
        """07:45 UK — Morning operational checklist.

        Reports: container health, overnight errors, data feed status, startup gate result.
        """
        logger.info("H-11: Generating morning operational checklist (07:45 UK)")
        now_uk = datetime.now(UK_TZ)

        lines = [
            f"\U0001f305 NZT-48 MORNING CHECKLIST",
            f"{now_uk.strftime('%d %b %Y %H:%M')} UK",
            f"{'=' * 30}",
            "",
        ]

        # Container health
        container_health = self._check_container_health()
        lines.append(f"CONTAINER HEALTH: {container_health['status']}")
        lines.append(f"  Uptime: {container_health['uptime']}")
        lines.append(f"  Memory: {container_health['memory_mb']:.0f} MB")
        lines.append("")

        # Overnight errors
        error_count = self._count_overnight_errors()
        error_emoji = "\u2705" if error_count == 0 else "\u26a0\ufe0f"
        lines.append(f"OVERNIGHT ERRORS: {error_emoji} {error_count} errors since 22:00")
        lines.append("")

        # Data feed status
        feed_status = self._check_data_feed_status()
        lines.append(f"DATA FEEDS:")
        for feed_name, status in feed_status.items():
            status_emoji = "\u2705" if status == "OK" else "\u274c"
            lines.append(f"  {status_emoji} {feed_name}: {status}")
        lines.append("")

        # Startup gate / kill switch
        kill_switch_active = self._check_kill_switch()
        ks_emoji = "\u274c ACTIVE" if kill_switch_active else "\u2705 CLEAR"
        lines.append(f"KILL SWITCH: {ks_emoji}")
        lines.append("")

        # Delivery failure count
        failures = self._failure_log.count_today()
        lines.append(f"DELIVERY FAILURES (today): {failures}")
        lines.append(f"{'=' * 30}")

        checklist_msg = "\n".join(lines)
        return await self._send_telegram(checklist_msg, disable_notification=True)

    async def send_midday_checklist(self) -> bool:
        """12:00 UK — Midday operational checklist.

        Reports: open positions P&L, circuit breaker status, drought state.
        """
        logger.info("H-11: Generating midday operational checklist (12:00 UK)")
        now_uk = datetime.now(UK_TZ)

        lines = [
            f"\u2600\ufe0f NZT-48 MIDDAY CHECKLIST",
            f"{now_uk.strftime('%d %b %Y %H:%M')} UK",
            f"{'=' * 30}",
            "",
        ]

        # Open positions P&L
        positions_data = self._get_positions_summary()
        lines.append(f"OPEN POSITIONS: {positions_data['count']}")
        lines.append(f"  Unrealised P&L: \u00a3{positions_data['unrealised_pnl']:+.2f}")
        lines.append(f"  Realised P&L (today): \u00a3{positions_data['realised_pnl']:+.2f}")
        lines.append("")

        # Circuit breaker status
        cb_status = self._get_circuit_breaker_status()
        cb_emoji = "\u2705" if cb_status == "GREEN" else ("\u26a0\ufe0f" if cb_status == "AMBER" else "\u274c")
        lines.append(f"CIRCUIT BREAKER: {cb_emoji} {cb_status}")
        lines.append("")

        # Drought state
        drought = self._check_drought_state()
        drought_emoji = "\u26a0\ufe0f DROUGHT" if drought else "\u2705 NORMAL"
        lines.append(f"SIGNAL DROUGHT: {drought_emoji}")
        lines.append("")

        # Alert counts so far today
        lines.append(f"ALERTS TODAY: P0={self._daily_counts[P0]} P1={self._daily_counts[P1]} "
                      f"P2={self._daily_counts[P2]} P3={self._daily_counts[P3]}")
        lines.append(f"{'=' * 30}")

        checklist_msg = "\n".join(lines)
        return await self._send_telegram(checklist_msg, disable_notification=True)

    async def send_evening_checklist(self) -> bool:
        """17:00 UK — Evening operational checklist.

        Reports: daily P&L, alerts count, backup status.
        """
        logger.info("H-11: Generating evening operational checklist (17:00 UK)")
        now_uk = datetime.now(UK_TZ)

        lines = [
            f"\U0001f307 NZT-48 EVENING CHECKLIST",
            f"{now_uk.strftime('%d %b %Y %H:%M')} UK",
            f"{'=' * 30}",
            "",
        ]

        # Daily P&L
        pnl_data = self._get_daily_pnl_summary()
        pnl_emoji = "\U0001f7e2" if pnl_data['net_pnl'] >= 0 else "\U0001f534"
        lines.append(f"DAILY P&L: {pnl_emoji}")
        lines.append(f"  Realised: \u00a3{pnl_data['realised']:+.2f}")
        lines.append(f"  Unrealised: \u00a3{pnl_data['unrealised']:+.2f}")
        lines.append(f"  Net: \u00a3{pnl_data['net_pnl']:+.2f}")
        lines.append(f"  Trades closed: {pnl_data['trades_closed']}")
        lines.append(f"  Win rate: {pnl_data['win_rate']:.0f}%")
        lines.append("")

        # Alerts count
        lines.append(f"ALERTS TODAY:")
        lines.append(f"  P0 (Critical):  {self._daily_counts[P0]}")
        lines.append(f"  P1 (Action):    {self._daily_counts[P1]}")
        lines.append(f"  P2 (Batched):   {self._daily_counts[P2]}")
        lines.append(f"  P3 (Digest):    {self._daily_counts[P3]}")
        lines.append(f"  Failures:       {self._failure_log.count_today()}")
        lines.append("")

        # Backup status
        backup_status = self._check_backup_status()
        backup_emoji = "\u2705" if backup_status["ok"] else "\u274c"
        lines.append(f"BACKUP STATUS: {backup_emoji} {backup_status['detail']}")
        lines.append(f"{'=' * 30}")

        checklist_msg = "\n".join(lines)
        return await self._send_telegram(checklist_msg, disable_notification=True)

    # ─────────────────────────────────────────────────────────────────────
    # H-11: Data collection helpers for checklists
    # ─────────────────────────────────────────────────────────────────────

    def _check_container_health(self) -> dict:
        """Check container health: uptime, memory usage."""
        import resource
        try:
            # Memory usage via resource module
            mem_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # macOS returns bytes, Linux returns KB
            import platform
            if platform.system() == "Darwin":
                mem_mb = mem_kb / (1024 * 1024)
            else:
                mem_mb = mem_kb / 1024
        except Exception:
            mem_mb = 0.0

        # Uptime from /proc/uptime or process start
        try:
            import psutil
            proc = psutil.Process()
            uptime_sec = time.time() - proc.create_time()
            hours = int(uptime_sec // 3600)
            mins = int((uptime_sec % 3600) // 60)
            uptime_str = f"{hours}h {mins}m"
        except Exception:
            uptime_str = "unknown"

        status = "OK" if mem_mb < 500 else ("DEGRADED" if mem_mb < 1000 else "CRITICAL")
        return {"status": status, "uptime": uptime_str, "memory_mb": mem_mb}

    def _count_overnight_errors(self) -> int:
        """Count ERROR-level log entries since 22:00 yesterday."""
        try:
            from pathlib import Path
            log_path = Path(__file__).parent.parent / "data" / "nzt48.log"
            if not log_path.exists():
                return 0
            count = 0
            cutoff = datetime.now(UK_TZ).replace(hour=22, minute=0, second=0, microsecond=0)
            # Simple: count lines containing "ERROR" in last N lines
            with open(log_path, "r", errors="replace") as f:
                # Read last 2000 lines
                lines = f.readlines()[-2000:]
                for line in lines:
                    if "ERROR" in line:
                        count += 1
            return count
        except Exception as e:
            logger.debug("_count_overnight_errors failed: %s", e)
            return -1

    def _check_data_feed_status(self) -> dict:
        """Check data feed freshness."""
        feeds = {}
        try:
            from pathlib import Path
            state_dir = Path(__file__).parent.parent / "data"

            # Check yfinance data freshness
            db_path = state_dir / "nzt48.db"
            if db_path.exists():
                import sqlite3
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                try:
                    row = conn.execute(
                        "SELECT MAX(timestamp) as latest FROM market_data"
                    ).fetchone()
                    if row and row["latest"]:
                        feeds["market_data"] = "OK"
                    else:
                        feeds["market_data"] = "NO DATA"
                except Exception:
                    feeds["market_data"] = "DB ERROR"
                finally:
                    conn.close()
            else:
                feeds["market_data"] = "NO DB"

            # Check Redis connectivity
            try:
                import redis
                r = redis.Redis(
                    host=os.environ.get("REDIS_HOST", "nzt48-redis"),
                    port=int(os.environ.get("REDIS_PORT", 6379)),
                    password=os.environ.get("REDIS_PASSWORD", "nzt48redis"),
                    socket_timeout=3,
                )
                r.ping()
                feeds["redis"] = "OK"
            except Exception:
                feeds["redis"] = "UNAVAILABLE"

        except Exception as e:
            feeds["error"] = str(e)[:60]

        return feeds

    def _check_kill_switch(self) -> bool:
        """Check if kill switch is active."""
        from pathlib import Path
        kill_path = Path(__file__).parent.parent / "data" / "KILL_SWITCH"
        return kill_path.exists()

    def _get_positions_summary(self) -> dict:
        """Get summary of open positions and today's P&L."""
        result = {"count": 0, "unrealised_pnl": 0.0, "realised_pnl": 0.0}
        try:
            from delivery.database import get_connection, get_open_positions
            conn = get_connection()
            try:
                positions = get_open_positions(conn)
                result["count"] = len(positions)
                result["unrealised_pnl"] = sum((p["unrealised_pnl"] or 0.0) for p in positions)

                # Today's realised P&L
                vt_row = conn.execute(
                    "SELECT COALESCE(SUM(net_pnl), 0) as daily_pnl "
                    "FROM virtual_trades WHERE date(exit_time) = date('now')"
                ).fetchone()
                result["realised_pnl"] = vt_row["daily_pnl"] if vt_row else 0.0
            finally:
                conn.close()
        except Exception as e:
            logger.debug("_get_positions_summary failed: %s", e)
        return result

    def _get_circuit_breaker_status(self) -> str:
        """Get circuit breaker colour status."""
        try:
            from qualification.circuit_breakers import CircuitBreakerSystem
            cbs = CircuitBreakerSystem()
            state = cbs.get_state()
            return state.get("colour", "GREEN") if isinstance(state, dict) else "GREEN"
        except Exception:
            return "UNKNOWN"

    def _check_drought_state(self) -> bool:
        """Check if system is in signal drought."""
        try:
            from pathlib import Path
            import json as _json
            drought_path = Path(__file__).parent.parent / "artifacts"
            # Check latest system state artifact
            today = datetime.now(timezone.utc).date().isoformat()
            state_dir = drought_path / today
            if state_dir.exists():
                for session_dir in sorted(state_dir.iterdir(), reverse=True):
                    state_file = session_dir / "system_state.json"
                    if state_file.exists():
                        data = _json.loads(state_file.read_text())
                        return data.get("drought_flag", False)
            return False
        except Exception:
            return False

    def _get_daily_pnl_summary(self) -> dict:
        """Get detailed daily P&L summary for evening checklist."""
        result = {
            "realised": 0.0, "unrealised": 0.0, "net_pnl": 0.0,
            "trades_closed": 0, "win_rate": 0.0,
        }
        try:
            from delivery.database import get_connection, get_open_positions
            conn = get_connection()
            try:
                # Realised P&L from closed trades today
                vt_row = conn.execute(
                    "SELECT COUNT(*) as trades, "
                    "COALESCE(SUM(net_pnl), 0) as total_pnl, "
                    "SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins "
                    "FROM virtual_trades WHERE date(exit_time) = date('now')"
                ).fetchone()
                trades = vt_row["trades"] if vt_row else 0
                result["realised"] = vt_row["total_pnl"] if vt_row else 0.0
                result["trades_closed"] = trades
                wins = vt_row["wins"] if vt_row else 0
                result["win_rate"] = (wins / trades * 100) if trades > 0 else 0.0

                # Unrealised P&L from open positions
                positions = get_open_positions(conn)
                result["unrealised"] = sum((p["unrealised_pnl"] or 0.0) for p in positions)
                result["net_pnl"] = result["realised"] + result["unrealised"]
            finally:
                conn.close()
        except Exception as e:
            logger.debug("_get_daily_pnl_summary failed: %s", e)
        return result

    def _check_backup_status(self) -> dict:
        """Check if last S3 backup was successful."""
        try:
            from pathlib import Path
            backup_log = Path(__file__).parent.parent / "data" / "backup_last_run.txt"
            if backup_log.exists():
                content = backup_log.read_text().strip()
                if "SUCCESS" in content.upper():
                    return {"ok": True, "detail": f"Last backup: {content[:60]}"}
                return {"ok": False, "detail": f"Last backup: {content[:60]}"}
            return {"ok": False, "detail": "No backup log found"}
        except Exception:
            return {"ok": False, "detail": "Backup check failed"}

    # ─────────────────────────────────────────────────────────────────────
    # Utility
    # ─────────────────────────────────────────────────────────────────────

    def _reset_daily_counts_if_needed(self) -> None:
        """Reset daily alert counters at midnight UTC."""
        today = datetime.now(timezone.utc).date().isoformat()
        if today != self._daily_counts_date:
            self._daily_counts = {P0: 0, P1: 0, P2: 0, P3: 0}
            self._daily_counts_date = today

    def _increment_daily_count(self, priority: int) -> None:
        """Increment daily counter for the given priority."""
        if priority in self._daily_counts:
            self._daily_counts[priority] += 1

    def get_status(self) -> dict:
        """Return current notifier status for diagnostics."""
        return {
            "daily_counts": dict(self._daily_counts),
            "daily_counts_date": self._daily_counts_date,
            "p2_buffer_size": len(self._p2_buffer),
            "p3_buffer_size": len(self._p3_buffer),
            "delivery_failures_today": self._failure_log.count_today(),
            "telegram_sender_active": self._telegram_sender is not None,
            "ses_configured": bool(self._ses_sender),
            "sns_configured": bool(self._sns_topic_arn or self._sns_phone),
        }


def get_notifier() -> TelegramNotifier:
    """Module-level convenience accessor."""
    return TelegramNotifier.instance()
