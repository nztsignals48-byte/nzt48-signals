"""Telegram Alerting & Human-in-the-Loop Escalation — Books 8, 38, 58.

Structured alerts via Telegram for:
1. Trade notifications (entry/exit with cost breakdown)
2. Health alerts (watchdog failures)
3. Drawdown escalation (phase transitions)
4. Overnight risk warnings (positions approaching limits)
5. Strategy lifecycle events (quarantine, kill, promote)
6. Human-in-the-loop confirmation requests

Severity levels:
  INFO:     Trade notifications, daily summary
  WARNING:  Drawdown phase change, strategy quarantine
  CRITICAL: Health failure, ruin probability > 20%, HALT trigger
  EMERGENCY: Sacred limit breach, data loss, credential compromise

Book 58 escalation protocol:
  L1: INFO → Telegram message only
  L2: WARNING → Telegram + sound notification
  L3: CRITICAL → Telegram + repeated every 5 min until acknowledged
  L4: EMERGENCY → Telegram + system HALT + await human restart

Usage:
    from python_brain.alerting.telegram import TelegramAlerter, AlertLevel

    alerter = TelegramAlerter(bot_token="...", chat_id="...")
    alerter.send_trade_alert(trade_data)
    alerter.send_health_alert(health_status)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

log = logging.getLogger("telegram_alerter")


class AlertLevel(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    EMERGENCY = "EMERGENCY"


LEVEL_EMOJI = {
    AlertLevel.INFO: "",
    AlertLevel.WARNING: "[!]",
    AlertLevel.CRITICAL: "[!!]",
    AlertLevel.EMERGENCY: "[!!!]",
}


@dataclass
class Alert:
    """A structured alert for Telegram delivery."""
    level: AlertLevel
    title: str
    body: str
    source: str = ""  # Module that generated the alert
    timestamp: float = 0.0
    requires_ack: bool = False  # Human must acknowledge
    repeat_interval_secs: int = 0  # 0 = no repeat

    def format_message(self) -> str:
        """Format alert as Telegram message (Markdown)."""
        emoji = LEVEL_EMOJI.get(self.level, "")
        lines = [
            f"*{emoji} {self.level.value}: {self.title}*",
            "",
            self.body,
        ]
        if self.source:
            lines.append(f"\n_Source: {self.source}_")
        if self.requires_ack:
            lines.append("\n*ACTION REQUIRED: Reply /ack to acknowledge*")
        return "\n".join(lines)


class TelegramAlerter:
    """Send structured alerts via Telegram Bot API."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
    ):
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        self._pending_acks: Dict[str, Alert] = {}
        self._last_sent: Dict[str, float] = {}  # Dedup: title → timestamp
        self._enabled = bool(self.bot_token and self.chat_id)

        if not self._enabled:
            log.info("Telegram alerter: disabled (no token/chat_id)")

    def send(self, alert: Alert) -> bool:
        """Send an alert via Telegram."""
        if not self._enabled:
            log.info("ALERT [%s]: %s — %s", alert.level.value, alert.title, alert.body[:100])
            return False

        # Dedup: don't send same title within 60 seconds
        now = time.time()
        last = self._last_sent.get(alert.title, 0)
        if now - last < 60 and alert.level != AlertLevel.EMERGENCY:
            return False

        alert.timestamp = now
        message = alert.format_message()

        try:
            import urllib.request
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            data = json.dumps({
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "disable_notification": alert.level == AlertLevel.INFO,
            }).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    self._last_sent[alert.title] = now
                    if alert.requires_ack:
                        self._pending_acks[alert.title] = alert
                    # Book 58: Register WARNING/CRITICAL alerts for escalation tracking
                    if alert.level in (AlertLevel.WARNING, AlertLevel.CRITICAL):
                        try:
                            from python_brain.alerting.escalation_manager import get_manager
                            alert_id = f"{alert.level.value}_{int(now)}_{alert.title[:20]}"
                            get_manager().register_alert(
                                alert_id=alert_id,
                                level=alert.level.value,
                                title=alert.title,
                                body=alert.body[:500],
                                source=alert.source,
                            )
                        except Exception as esc_err:
                            log.debug("Escalation register failed (non-fatal): %s", esc_err)
                    return True
        except Exception as e:
            log.warning("Telegram send failed: %s", e)

        return False

    # -----------------------------------------------------------------------
    # Convenience methods for common alert types
    # -----------------------------------------------------------------------

    def send_trade_alert(self, trade: Dict[str, Any]):
        """Send trade entry/exit notification."""
        ticker = trade.get("ticker", "?")
        direction = trade.get("direction", "Long")
        strategy = trade.get("strategy", "?")
        confidence = trade.get("confidence", 0)
        pnl = trade.get("pnl")

        if pnl is not None:
            # Exit notification
            cost = trade.get("estimated_cost", 0)
            net_pnl = pnl - cost
            body = (
                f"Ticker: {ticker}\n"
                f"Strategy: {strategy}\n"
                f"Gross P&L: {pnl:+.2f} GBP\n"
                f"Costs: -{cost:.2f} GBP\n"
                f"Net P&L: {net_pnl:+.2f} GBP"
            )
            self.send(Alert(AlertLevel.INFO, f"EXIT {ticker}", body, source="trade"))
        else:
            # Entry notification
            kelly = trade.get("kelly_fraction", 0)
            body = (
                f"Ticker: {ticker}\n"
                f"Direction: {direction}\n"
                f"Strategy: {strategy}\n"
                f"Confidence: {confidence}\n"
                f"Kelly: {kelly:.3f}"
            )
            self.send(Alert(AlertLevel.INFO, f"ENTRY {ticker}", body, source="trade"))

    def send_health_alert(self, health: Dict[str, Any]):
        """Send health check failure alert."""
        failed = health.get("failed", [])
        if not failed:
            return

        level = AlertLevel.WARNING
        if health.get("overall_level", 5) <= 2:
            level = AlertLevel.CRITICAL

        body = "Failed checks:\n" + "\n".join(f"  - {f}" for f in failed)
        self.send(Alert(
            level, "HEALTH CHECK FAILURE", body,
            source="watchdog",
            requires_ack=level == AlertLevel.CRITICAL,
            repeat_interval_secs=300 if level == AlertLevel.CRITICAL else 0,
        ))

    def send_drawdown_alert(self, phase: str, dd_pct: float, equity: float):
        """Send drawdown phase transition alert."""
        level = {
            "MONITORING": AlertLevel.WARNING,
            "RECOVERY": AlertLevel.WARNING,
            "CRITICAL": AlertLevel.CRITICAL,
            "HALTED": AlertLevel.EMERGENCY,
        }.get(phase, AlertLevel.INFO)

        body = f"Drawdown: {dd_pct:.1f}%\nEquity: {equity:.0f} GBP\nPhase: {phase}"
        self.send(Alert(
            level, f"DRAWDOWN {phase}", body,
            source="drawdown_recovery",
            requires_ack=phase in ("CRITICAL", "HALTED"),
        ))

    def send_overnight_alert(self, reductions: Dict[str, float]):
        """Send overnight position reduction alert."""
        if not reductions:
            return

        body = "Position reductions for overnight compliance:\n"
        total = 0.0
        for ticker, amount in reductions.items():
            body += f"  {ticker}: reduce {amount:.0f} GBP\n"
            total += amount
        body += f"\nTotal reduction: {total:.0f} GBP"

        self.send(Alert(AlertLevel.WARNING, "OVERNIGHT REDUCTION", body, source="overnight_risk"))

    def send_lifecycle_alert(self, strategy: str, old_state: str, new_state: str, reason: str = ""):
        """Send strategy lifecycle transition alert."""
        level = AlertLevel.INFO
        if new_state in ("RETIRED", "HALTED"):
            level = AlertLevel.WARNING
        elif new_state == "UNDER_REVIEW":
            level = AlertLevel.WARNING

        body = f"Strategy: {strategy}\n{old_state} -> {new_state}"
        if reason:
            body += f"\nReason: {reason}"

        self.send(Alert(level, f"LIFECYCLE: {strategy}", body, source="lifecycle"))

    def send_daily_summary(self, summary: Dict[str, Any]):
        """Send end-of-day summary."""
        trades = summary.get("total_trades", 0)
        net_pnl = summary.get("net_pnl", 0)
        wr = summary.get("win_rate", 0)
        dd = summary.get("drawdown_pct", 0)

        body = (
            f"Trades: {trades}\n"
            f"Net P&L: {net_pnl:+.2f} GBP\n"
            f"Win Rate: {wr:.0f}%\n"
            f"Drawdown: {dd:.1f}%"
        )
        self.send(Alert(AlertLevel.INFO, "DAILY SUMMARY", body, source="nightly"))
