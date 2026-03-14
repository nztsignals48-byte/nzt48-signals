"""
Telegram Alerter for Perfect Entry Timing
===========================================
Sends real-time alerts for:
1. Entry decisions (perfect timing confirmed)
2. Rung achievements (profit ladder progression)
3. Daily summaries (win rate, trades executed)

Fallback: if Telegram unavailable, logs to file.
"""

import os
import logging
from datetime import datetime
from typing import Optional, List
from dataclasses import dataclass

logger = logging.getLogger("nzt48.telegram")


@dataclass
class AlertMessage:
    """A single alert to send"""
    title: str
    details: str
    severity: str  # "info", "success", "warning", "critical"
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class TelegramAlerter:
    """
    Sends alerts via Telegram Bot API.

    Handles:
    - Connection testing on startup
    - Graceful degradation (logs to file if API unavailable)
    - Rate limiting (no spam)
    - Alert queuing
    """

    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        """
        Initialize Telegram alerter.

        Args:
            bot_token: Telegram Bot API token (env var: NZT48_TELEGRAM_BOT_TOKEN)
            chat_id: Telegram chat ID (env var: NZT48_TELEGRAM_CHAT_ID)
        """
        self.bot_token = bot_token or os.getenv("NZT48_TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("NZT48_TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.bot_token and self.chat_id)
        self._alert_queue: List[AlertMessage] = []
        self._last_alert_time = {}  # Track rate limiting per alert type

        # Test connection on startup
        if self.enabled:
            self._test_connection()
        else:
            logger.warning("TelegramAlerter: disabled (no token/chat_id)")

    def _test_connection(self) -> bool:
        """Test Telegram API connectivity"""
        try:
            import requests

            url = f"https://api.telegram.org/bot{self.bot_token}/getMe"
            response = requests.get(url, timeout=5)

            if response.status_code == 200:
                logger.info("TelegramAlerter: ✓ Connection test passed")
                return True
            else:
                logger.warning(
                    f"TelegramAlerter: Connection test failed (status {response.status_code})"
                )
                self.enabled = False
                return False

        except Exception as e:
            logger.warning(f"TelegramAlerter: Connection test failed: {e}")
            self.enabled = False
            return False

    def _should_send(self, alert_type: str, min_interval_sec: int = 60) -> bool:
        """Rate limit check: don't send same alert type too frequently"""
        now = datetime.now().timestamp()
        last = self._last_alert_time.get(alert_type, 0)

        if now - last < min_interval_sec:
            return False

        self._last_alert_time[alert_type] = now
        return True

    def _format_message(self, alert: AlertMessage) -> str:
        """Format alert for Telegram"""
        emoji = {
            "info": "ℹ️",
            "success": "✅",
            "warning": "⚠️",
            "critical": "🚨",
        }.get(alert.severity, "📢")

        timestamp_str = alert.timestamp.strftime("%H:%M:%S")

        return f"""{emoji} {alert.title}
{timestamp_str}

{alert.details}
"""

    def _send_telegram_message(self, text: str) -> bool:
        """Send message via Telegram Bot API"""
        if not self.enabled:
            return False

        try:
            import requests

            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "Markdown",
            }

            response = requests.post(url, json=payload, timeout=10)

            if response.status_code == 200:
                return True
            else:
                logger.warning(
                    f"TelegramAlerter: Send failed (status {response.status_code})"
                )
                return False

        except Exception as e:
            logger.warning(f"TelegramAlerter: Send failed: {e}")
            return False

    def _log_fallback(self, alert: AlertMessage) -> None:
        """Fallback: log to file instead of Telegram"""
        msg = self._format_message(alert)
        if alert.severity == "critical":
            logger.critical(msg)
        elif alert.severity == "warning":
            logger.warning(msg)
        elif alert.severity == "success":
            logger.info(msg)
        else:
            logger.debug(msg)

    def send_entry_alert(
        self,
        symbol: str,
        side: str,
        confidence_pct: float,
        position_size: float,
        leverage: float,
        regime: str,
        early_detection_reason: str = "",
    ) -> None:
        """
        Send alert when trade entry is approved.

        Args:
            symbol: e.g., "QQQ3.L"
            side: "BUY" or "SELL"
            confidence_pct: Early detection confidence (0-100%)
            position_size: Position size in GBP
            leverage: Leverage multiplier (1x, 3x, 5x)
            regime: Market regime
            early_detection_reason: Why entry was approved
        """
        if not self._should_send("entry_alert", min_interval_sec=30):
            return

        details = f"""*Symbol:* {symbol}
*Side:* {side}
*Confidence:* {confidence_pct:.0f}%
*Position:* £{position_size:.0f}
*Leverage:* {leverage:.1f}x
*Regime:* {regime}

_{early_detection_reason}_
"""

        alert = AlertMessage(
            title=f"📈 PERFECT ENTRY: {symbol} {side}",
            details=details,
            severity="success",
        )

        if self.enabled:
            success = self._send_telegram_message(self._format_message(alert))
            if not success:
                self._log_fallback(alert)
        else:
            self._log_fallback(alert)

    def send_rung_alert(
        self,
        symbol: str,
        rung_number: int,
        profit_pct: float,
        action: str,
        bank_pct: float = 0.0,
    ) -> None:
        """
        Send alert when profit rung is hit.

        Args:
            symbol: e.g., "QQQ3.L"
            rung_number: Rung index (0-4+)
            profit_pct: Unrealised profit %
            action: Action at this rung (e.g., "lock_profit_2pct_bank_15")
            bank_pct: Fraction of position banked
        """
        if not self._should_send("rung_alert", min_interval_sec=10):
            return

        details = f"""*Symbol:* {symbol}
*Rung:* {rung_number}
*Profit:* +{profit_pct:.1f}%
*Action:* {action}
*Banking:* {bank_pct*100:.0f}%
"""

        alert = AlertMessage(
            title=f"🎯 RUNG HIT: {symbol} +{profit_pct:.1f}%",
            details=details,
            severity="info",
        )

        if self.enabled:
            success = self._send_telegram_message(self._format_message(alert))
            if not success:
                self._log_fallback(alert)
        else:
            self._log_fallback(alert)

    def send_daily_summary(
        self,
        date: datetime,
        trades_executed: int,
        trades_rejected: int,
        win_rate_pct: float,
        daily_pnl: float,
        total_pnl: float,
    ) -> None:
        """
        Send end-of-day summary.

        Args:
            date: Date of summary
            trades_executed: Count of executed trades
            trades_rejected: Count of rejected trades
            win_rate_pct: Win rate percentage
            daily_pnl: P&L for the day
            total_pnl: Total P&L
        """
        if not self._should_send("daily_summary", min_interval_sec=3600):
            return

        daily_emoji = "📈" if daily_pnl > 0 else "📉"
        total_emoji = "🚀" if total_pnl > 0 else "⚠️"

        details = f"""*Date:* {date.strftime('%Y-%m-%d')}

*Trades:* {trades_executed} executed, {trades_rejected} rejected
*Win Rate:* {win_rate_pct:.1f}%

*Daily P&L:* £{daily_pnl:+.2f} {daily_emoji}
*Total P&L:* £{total_pnl:+.2f} {total_emoji}
"""

        alert = AlertMessage(
            title="📊 DAILY SUMMARY",
            details=details,
            severity="info",
        )

        if self.enabled:
            success = self._send_telegram_message(self._format_message(alert))
            if not success:
                self._log_fallback(alert)
        else:
            self._log_fallback(alert)

    def send_warning(
        self,
        title: str,
        reason: str,
    ) -> None:
        """
        Send warning alert (e.g., ISA audit failure, ISA trading halt).

        Args:
            title: Alert title
            reason: Detailed reason
        """
        if not self._should_send("warning_alert", min_interval_sec=30):
            return

        alert = AlertMessage(
            title=title,
            details=reason,
            severity="warning",
        )

        if self.enabled:
            success = self._send_telegram_message(self._format_message(alert))
            if not success:
                self._log_fallback(alert)
        else:
            self._log_fallback(alert)


if __name__ == "__main__":
    # Quick test
    alerter = TelegramAlerter()

    print("="*60)
    print("TelegramAlerter Initialization")
    print("="*60)
    print(f"Enabled: {alerter.enabled}")
    print(f"Bot token: {'***' + alerter.bot_token[-4:] if alerter.bot_token else 'NOT SET'}")
    print(f"Chat ID: {alerter.chat_id if alerter.chat_id else 'NOT SET'}")

    # Test message formats
    print("\n✅ TelegramAlerter ready")
