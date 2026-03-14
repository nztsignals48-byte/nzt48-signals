"""
Telegram Alerting System for NZT-48 Perfect Entry Timing System
================================================================

Sends real-time alerts for:
- Trade entries (with confidence, signals, position size)
- Rung hits (profit scaling, which rung)
- Trade exits (P&L, exit reason)
- Daily summaries (trades, win rate, learnings)
- Errors/Alarms (critical, high priority)

Integrates with:
- orchestrator.py (entry detection)
- chandelier_exit.py (rung advancement)
- daily_optimization.py (end-of-day summary)
- learning systems (alerts on improvements)
"""

import os
import logging
import requests
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class TelegramMessage:
    """Telegram message to send"""
    chat_id: str
    text: str
    parse_mode: str = "HTML"
    retry_count: int = 0


class TelegramAlerter:
    """
    Send alerts via Telegram to user.

    Configuration via environment variables:
    - TELEGRAM_BOT_TOKEN: Bot token from BotFather
    - TELEGRAM_CHAT_ID: Your chat ID (numeric)
    - TELEGRAM_DRY_RUN: If "true", log instead of sending
    """

    def __init__(self, dry_run: bool = None):
        """Initialize Telegram alerter from environment"""
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")

        # Allow override via parameter
        if dry_run is None:
            self.dry_run = os.getenv("TELEGRAM_DRY_RUN", "false").lower() == "true"
        else:
            self.dry_run = dry_run

        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        self.logger = logging.getLogger("nzt48.telegram_alerter")

        # Validate configuration
        if not self.bot_token:
            self.logger.warning("TELEGRAM_BOT_TOKEN not set — alerts disabled")
        if not self.chat_id:
            self.logger.warning("TELEGRAM_CHAT_ID not set — alerts disabled")

        self.alerts_sent = 0
        self.alerts_failed = 0

    def _is_configured(self) -> bool:
        """Check if Telegram is properly configured"""
        return bool(self.bot_token and self.chat_id)

    def _send_raw(self, message: str, retry: int = 0, max_retries: int = 3) -> bool:
        """
        Send raw message via Telegram API.

        Returns True if sent/dry-run, False if failed after retries.
        """
        if not self._is_configured():
            self.logger.debug("Telegram not configured, skipping alert")
            return False

        if self.dry_run:
            self.logger.info(f"[DRY_RUN] Would send Telegram: {message[:100]}...")
            self.alerts_sent += 1
            return True

        try:
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML"
            }

            response = requests.post(self.api_url, json=payload, timeout=5)
            response.raise_for_status()

            self.logger.info(f"Telegram alert sent: {message[:80]}...")
            self.alerts_sent += 1
            return True

        except requests.exceptions.RequestException as e:
            self.alerts_failed += 1
            if retry < max_retries:
                wait_time = 2 ** retry  # Exponential backoff
                self.logger.warning(f"Telegram send failed, retry {retry+1}/{max_retries} in {wait_time}s: {e}")
                time.sleep(wait_time)
                return self._send_raw(message, retry=retry+1, max_retries=max_retries)
            else:
                self.logger.error(f"Telegram send failed after {max_retries} retries: {e}")
                return False

        except Exception as e:
            self.logger.error(f"Unexpected error sending Telegram: {e}")
            self.alerts_failed += 1
            return False

    def send_trade_entry(
        self,
        asset: str,
        direction: str,
        entry_price: float,
        confidence: float,
        signals: Dict[str, Any],
        position_size: float,
        kelly_size: float = None
    ) -> bool:
        """
        Alert on trade entry.

        Args:
            asset: Asset being traded (e.g., "QQQ3.L")
            direction: "BUY" or "SELL"
            entry_price: Entry price
            confidence: Confidence score (0-100)
            signals: Dict of signals that triggered entry
            position_size: Actual position size in currency
            kelly_size: Kelly-recommended size

        Returns:
            True if sent/logged successfully
        """
        signal_list = ", ".join([f"{k}={v}" for k, v in list(signals.items())[:3]])
        kelly_pct = (position_size / kelly_size * 100) if kelly_size and kelly_size > 0 else 0

        message = (
            f"🚀 <b>ENTRY: {direction} {asset}</b>\n"
            f"Price: £{entry_price:.2f}\n"
            f"Confidence: {confidence:.0f}%\n"
            f"Position: £{position_size:.0f} ({kelly_pct:.0f}% Kelly)\n"
            f"Signals: {signal_list}\n"
            f"{datetime.now().strftime('%H:%M:%S')}"
        )

        return self._send_raw(message)

    def send_rung_hit(
        self,
        asset: str,
        rung_num: int,
        hit_price: float,
        profit_pct: float,
        rungs_remaining: int
    ) -> bool:
        """
        Alert when rung target is hit (profit scaling).

        Args:
            asset: Asset
            rung_num: Which rung (1-7)
            hit_price: Price at which rung was hit
            profit_pct: Profit percentage at this rung
            rungs_remaining: How many rungs left

        Returns:
            True if sent/logged successfully
        """
        message = (
            f"📈 <b>RUNG HIT: {asset}</b>\n"
            f"Rung {rung_num} hit at £{hit_price:.2f}\n"
            f"Profit: +{profit_pct:.1f}%\n"
            f"Rungs remaining: {rungs_remaining}\n"
            f"{datetime.now().strftime('%H:%M:%S')}"
        )

        return self._send_raw(message)

    def send_trade_exit(
        self,
        asset: str,
        entry_price: float,
        exit_price: float,
        total_profit_pct: float,
        total_profit_currency: float,
        exit_reason: str,
        rungs_hit: int
    ) -> bool:
        """
        Alert on trade exit.

        Args:
            asset: Asset
            entry_price: Entry price
            exit_price: Exit price
            total_profit_pct: Profit percentage
            total_profit_currency: Profit in currency
            exit_reason: Why we exited ("rung_5", "stopped_out", "time_exit", etc)
            rungs_hit: How many rungs we advanced through

        Returns:
            True if sent/logged successfully
        """
        emoji = "✅" if total_profit_pct > 0 else "⚠️"
        color = "green" if total_profit_pct > 0 else "red"

        message = (
            f"{emoji} <b>EXIT: {asset}</b>\n"
            f"Entry: £{entry_price:.2f} → Exit: £{exit_price:.2f}\n"
            f"P&L: <b>{total_profit_pct:+.2f}%</b> (£{total_profit_currency:+.0f})\n"
            f"Reason: {exit_reason}\n"
            f"Rungs hit: {rungs_hit}\n"
            f"{datetime.now().strftime('%H:%M:%S')}"
        )

        return self._send_raw(message)

    def send_daily_summary(
        self,
        trades_today: int,
        win_rate: float,
        daily_pnl: float,
        daily_pnl_pct: float,
        best_trade: Dict[str, Any],
        worst_trade: Dict[str, Any],
        learning_update: Optional[str] = None
    ) -> bool:
        """
        Daily end-of-day summary.

        Args:
            trades_today: Number of trades
            win_rate: Win rate percentage
            daily_pnl: Daily P&L in currency
            daily_pnl_pct: Daily P&L percentage
            best_trade: Dict with asset, profit_pct
            worst_trade: Dict with asset, loss_pct
            learning_update: Optional learning summary

        Returns:
            True if sent/logged successfully
        """
        emoji = "📊"
        status = "📈" if daily_pnl >= 0 else "📉"

        best_str = f"{best_trade.get('asset', 'N/A')} +{best_trade.get('profit_pct', 0):.1f}%" if best_trade else "N/A"
        worst_str = f"{worst_trade.get('asset', 'N/A')} {worst_trade.get('loss_pct', 0):.1f}%" if worst_trade else "N/A"

        learning_str = f"\n📚 {learning_update}" if learning_update else ""

        message = (
            f"{emoji} <b>DAILY SUMMARY</b> {status}\n"
            f"Trades: {trades_today}\n"
            f"Win Rate: {win_rate:.1f}%\n"
            f"P&L: £{daily_pnl:+.0f} ({daily_pnl_pct:+.2f}%)\n"
            f"Best: {best_str}\n"
            f"Worst: {worst_str}{learning_str}\n"
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        return self._send_raw(message)

    def send_alarm(
        self,
        severity: str,
        title: str,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Send alarm/error alert.

        Args:
            severity: "critical", "high", "medium", "low"
            title: Short alarm title
            message: Alarm message
            details: Optional dict of additional details

        Returns:
            True if sent/logged successfully
        """
        emojis = {
            "critical": "🚨",
            "high": "⚠️",
            "medium": "⚡",
            "low": "ℹ️"
        }
        emoji = emojis.get(severity, "⚠️")

        detail_str = ""
        if details:
            for k, v in list(details.items())[:3]:
                detail_str += f"\n{k}: {v}"

        alert_message = (
            f"{emoji} <b>ALARM: {title}</b>\n"
            f"{message}{detail_str}\n"
            f"{datetime.now().strftime('%H:%M:%S')}"
        )

        return self._send_raw(alert_message)

    def send_heat_cap_warning(self, current_loss_pct: float, heat_cap_pct: float) -> bool:
        """Alert when approaching daily loss limit"""
        remaining = heat_cap_pct - current_loss_pct
        message = (
            f"🔥 <b>HEAT CAP WARNING</b>\n"
            f"Current loss: {current_loss_pct:.2f}%\n"
            f"Heat cap: {heat_cap_pct:.2f}%\n"
            f"Remaining: {remaining:.2f}%\n"
            f"⚠️ Trading will pause at heat cap limit"
        )
        return self._send_raw(message)

    def send_heat_cap_hit(self, total_loss: float, heat_cap: float) -> bool:
        """Alert when heat cap is breached — trading paused"""
        message = (
            f"🛑 <b>HEAT CAP HIT — TRADING PAUSED</b>\n"
            f"Total loss: £{total_loss:.0f}\n"
            f"Heat cap: £{heat_cap:.0f}\n"
            f"Trading will resume tomorrow\n"
            f"Check system status"
        )
        return self._send_raw(message)

    def get_stats(self) -> Dict[str, int]:
        """Return alerting statistics"""
        return {
            "alerts_sent": self.alerts_sent,
            "alerts_failed": self.alerts_failed,
            "total_attempts": self.alerts_sent + self.alerts_failed
        }


# Test mode
if __name__ == "__main__":
    print("=" * 70)
    print("TELEGRAM ALERTER TEST")
    print("=" * 70)

    # Test with DRY_RUN
    alerter = TelegramAlerter(dry_run=True)

    print("\n1. Test Trade Entry Alert")
    print("-" * 70)
    alerter.send_trade_entry(
        asset="QQQ3.L",
        direction="BUY",
        entry_price=145.50,
        confidence=78,
        signals={"OFI": 0.45, "RVOL": 2.1, "Hawkes": 0.68},
        position_size=742.50,
        kelly_size=990
    )

    print("\n2. Test Rung Hit Alert")
    print("-" * 70)
    alerter.send_rung_hit(
        asset="QQQ3.L",
        rung_num=1,
        hit_price=148.20,
        profit_pct=1.85,
        rungs_remaining=6
    )

    print("\n3. Test Trade Exit Alert")
    print("-" * 70)
    alerter.send_trade_exit(
        asset="QQQ3.L",
        entry_price=145.50,
        exit_price=150.10,
        total_profit_pct=3.16,
        total_profit_currency=23.40,
        exit_reason="rung_3_hit",
        rungs_hit=3
    )

    print("\n4. Test Daily Summary")
    print("-" * 70)
    alerter.send_daily_summary(
        trades_today=5,
        win_rate=80.0,
        daily_pnl=156.25,
        daily_pnl_pct=0.47,
        best_trade={"asset": "QQQ3.L", "profit_pct": 3.16},
        worst_trade={"asset": "3USS.L", "loss_pct": -1.20},
        learning_update="Increased confidence threshold from 65% to 68%"
    )

    print("\n5. Test Alarm")
    print("-" * 70)
    alerter.send_alarm(
        severity="medium",
        title="High Volatility Event",
        message="VIX spiked above 20",
        details={"VIX": 21.5, "Time": "14:25", "Impact": "Widened rungs 15%"}
    )

    print("\n" + "=" * 70)
    print(f"STATS: {alerter.get_stats()}")
    print("✅ ALL TESTS COMPLETE (DRY_RUN MODE)")
    print("=" * 70)
