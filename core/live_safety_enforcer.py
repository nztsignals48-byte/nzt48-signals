"""Live Safety Enforcer - Prevent dangerous trades"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SafetyLimits:
    daily_heat_cap_pct: float = -4.0  # -4% daily loss limit
    per_trade_stop_loss_pct: float = 2.0  # 2% max loss per trade
    max_position_pct: float = 5.0  # 5% of account per trade
    max_leverage: float = 5.0  # 5x max
    max_consecutive_losses: int = 3  # Pause after 3 losses
    max_daily_trades: int = 25  # Circuit breaker


class LiveSafetyEnforcer:
    """Enforces risk limits before every trade"""

    def __init__(self, account_balance: float = 10000.0):
        self.logger = logging.getLogger("nzt48.live_safety_enforcer")
        self.account_balance = account_balance
        self.limits = SafetyLimits()
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.daily_trades = 0

    def can_trade(self, position_size: float, leverage: float = 1.0) -> tuple:
        """Check if trade is allowed"""
        # Check position size
        max_pos = self.account_balance * (self.limits.max_position_pct / 100)
        if position_size > max_pos:
            return False, f"Position £{position_size} exceeds max £{max_pos}"

        # Check leverage
        if leverage > self.limits.max_leverage:
            return False, f"Leverage {leverage}x exceeds max {self.limits.max_leverage}x"

        # Check daily heat cap
        max_loss = self.account_balance * (self.limits.daily_heat_cap_pct / 100)
        if self.daily_pnl < max_loss:
            return False, f"Daily loss £{abs(self.daily_pnl)} exceeds cap £{abs(max_loss)}"

        # Check consecutive losses
        if self.consecutive_losses >= self.limits.max_consecutive_losses:
            return False, f"{self.consecutive_losses} consecutive losses - pause trading"

        # Check daily trade limit
        if self.daily_trades >= self.limits.max_daily_trades:
            return False, f"Exceeded max daily trades ({self.daily_trades})"

        return True, "all checks pass"

    def record_trade_exit(self, pnl: float):
        """Record trade exit P&L"""
        self.daily_pnl += pnl
        self.daily_trades += 1
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0


if __name__ == "__main__":
    enforcer = LiveSafetyEnforcer(account_balance=10000.0)
    can_trade, reason = enforcer.can_trade(position_size=500.0, leverage=2.0)
    print(f"✅ Safety check: {can_trade} ({reason})")
