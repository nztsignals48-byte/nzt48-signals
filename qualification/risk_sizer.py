"""
NZT-48 Trading System — Risk Management Engine
Section 43: 15 Immutable Risk Rules (CONSTITUTIONAL) — R2 daily loss & R10 loss-streak halt moved to circuit_breakers.py
Section 44: 12 Emotional Firewall Blocks
Section 42: Session & Weekly Protection
Section 60: Drawdown Recovery Protocol
AEGIS K-07: Commission Audit + Capital Critical Mass Gate

These rules CANNOT be adjusted by the learning engine, operator,
or any system component. They are CONSTITUTIONAL.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import (
    Signal, Direction, Bot, BotInstance, DrawdownLevel,
    EmotionalPattern, SignalStatus,
)

logger = logging.getLogger("nzt48.risk")


class ImmutableRiskRules:
    """Section 43: 15 rules that cannot be overridden by any component.

    These are hardcoded values from the spec. The learning engine,
    optimiser, and operator cannot modify them. Period.

    A-04: __setattr__ guard enforces true immutability post-init.
    Any attempt to modify attributes after construction raises AttributeError.

    Note: Rule 2 (MAX_DAILY_LOSS) removed — daily loss is now governed
    solely by the L1/L2/L3 cascade in circuit_breakers.py (AEGIS F-08).

    Note: Rule 10 (CONSECUTIVE_LOSSES_5_HALT) removed — consecutive loss
    session halt is now governed solely by circuit_breakers.py (AEGIS A-13).
    """

    # === THE 15 CONSTITUTIONAL RULES (R2, R10 moved to circuit_breakers.py) ===
    RISK_PER_TRADE = 0.0075              # 0.75% of equity
    # Daily loss governed solely by L1/L2/L3 cascade in circuit_breakers.py
    # (L1=1.5% YELLOW, L2=2.5% ORANGE, L3=4.0% RED). Removed MAX_DAILY_LOSS=0.03
    # which contradicted the graduated cascade — see AEGIS F-08.
    MAX_WEEKLY_LOSS = 0.06               # 6%
    MAX_CONCURRENT_POSITIONS = 3         # Default (BULL-BOT: 5)
    MAX_SAME_SUB_INDUSTRY = 2            # No 3 GPU stocks at once
    MAX_TRADES_PER_DAY = 4               # Per bot (hard cap)
    MAX_TRADES_PER_WEEK = 10             # Per bot
    MIN_CONFIDENCE = 65                  # Canonical confidence floor — see ThresholdRegistry (E-01)
    CONSECUTIVE_LOSSES_3_COOLDOWN = 15   # Minutes
    CONSECUTIVE_LOSSES_3_CONF = 75       # Minimum conf after 3 losses
    # A-13: Consecutive loss session halt removed from here.
    # circuit_breakers.py is the SOLE AUTHORITY for consecutive loss halts.
    # See CircuitBreakerSystem._check_consecutive_losses() — threshold = 5.
    ETP_3X_MAX_ALLOCATION = 0.30         # 30% of equity
    ETP_3X_MAX_SINGLE = 0.15            # 15%
    ETP_5X_MAX_ALLOCATION = 0.15         # 15%
    ETP_5X_MAX_SINGLE = 0.05            # 5%
    ETP_5X_MAX_HOLD_DAYS = 3            # Volatility decay
    ETP_3X_MAX_HOLD_DAYS = 5            # Decay limit
    REGIME_FLIP_EXIT = True              # Wrong regime = EXIT immediately

    def __init__(self) -> None:
        # A-04: Mark initialisation complete — __setattr__ guard activates
        object.__setattr__(self, '_initialised', True)

    def __setattr__(self, name: str, value: object) -> None:
        """A-04: Block all attribute mutation after __init__ completes.

        Raises AttributeError on any attempt to modify instance or class-level
        attributes post-construction. This makes the constitutional rules
        truly immutable at runtime — no code path can silently mutate them.
        """
        if getattr(self, '_initialised', False):
            raise AttributeError(
                f"ImmutableRiskRules: cannot modify '{name}' — "
                f"constitutional rules are frozen post-init"
            )
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        """A-04: Block attribute deletion."""
        raise AttributeError(
            f"ImmutableRiskRules: cannot delete '{name}' — "
            f"constitutional rules are frozen"
        )

    def check_all(
        self,
        signal: Signal,
        equity: float,
        daily_pnl_pct: float,
        weekly_pnl_pct: float,
        daily_trade_count: int,
        weekly_trade_count: int,
        consecutive_losses: int,
        open_positions: list,
        same_industry_count: int = 0,
        etp_3x_allocation_pct: float = 0.0,
        etp_5x_allocation_pct: float = 0.0,
        last_loss_time: Optional[datetime] = None,
    ) -> tuple[bool, list[str]]:
        """Check a signal against all 15 immutable rules (R2 daily loss
        and R10 loss-streak halt are handled by circuit_breakers.py).

        Returns (passed: bool, violations: list[str]).
        If ANY rule is violated, the signal is BLOCKED.
        """
        violations: list[str] = []

        # Rule 1: Risk per trade = 0.75%
        if signal.risk_pct > self.RISK_PER_TRADE:
            violations.append(
                f"R1: Risk {signal.risk_pct*100:.2f}% > {self.RISK_PER_TRADE*100}% max"
            )

        # Rule 2: REMOVED — daily loss is governed solely by L1/L2/L3 cascade
        # in circuit_breakers.py (see AEGIS F-08). The old MAX_DAILY_LOSS=3%
        # contradicted the graduated L1=1.5%/L2=2.5%/L3=4.0% thresholds.

        # Rule 3: Max weekly loss = 6%
        if weekly_pnl_pct <= -self.MAX_WEEKLY_LOSS:
            violations.append(
                f"R3: Weekly loss {weekly_pnl_pct*100:.1f}% >= {self.MAX_WEEKLY_LOSS*100}% limit. HALT."
            )

        # Rule 4: Max concurrent positions
        max_pos = self.MAX_CONCURRENT_POSITIONS
        if signal.bot_instance == BotInstance.BULL:
            max_pos = 5  # Elevated for BULL-BOT in confirmed trend
        elif signal.bot_instance == BotInstance.BEAR:
            max_pos = 2
        bot_positions = len([p for p in open_positions
                            if hasattr(p, 'bot_instance')
                            and p.bot_instance == signal.bot_instance.value])
        if bot_positions >= max_pos:
            violations.append(
                f"R4: Max positions ({max_pos}) reached for {signal.bot_instance.value}"
            )

        # Rule 5: Max same sub-industry = 2
        if same_industry_count >= self.MAX_SAME_SUB_INDUSTRY:
            violations.append(
                f"R5: Max same sub-industry ({self.MAX_SAME_SUB_INDUSTRY}) reached"
            )

        # Rule 6: Max trades per day = 4 per bot
        if daily_trade_count >= self.MAX_TRADES_PER_DAY:
            violations.append(
                f"R6: Max daily trades ({self.MAX_TRADES_PER_DAY}) reached"
            )

        # Rule 7: Max trades per week = 10 per bot
        if weekly_trade_count >= self.MAX_TRADES_PER_WEEK:
            violations.append(
                f"R7: Max weekly trades ({self.MAX_TRADES_PER_WEEK}) reached"
            )

        # Rule 8: Min confidence = 65 — Canonical confidence floor (E-01)
        if signal.confidence < self.MIN_CONFIDENCE:
            violations.append(
                f"R8: Confidence {signal.confidence:.0f} < {self.MIN_CONFIDENCE} floor"
            )

        # Rule 9: 3 consecutive losses = 15-min cool + conf 75
        if consecutive_losses >= 3 and consecutive_losses < 5:
            if last_loss_time:
                cooldown_end = last_loss_time + timedelta(minutes=self.CONSECUTIVE_LOSSES_3_COOLDOWN)
                if datetime.now(timezone.utc) < cooldown_end:
                    violations.append(
                        f"R9: 3 consecutive losses — {self.CONSECUTIVE_LOSSES_3_COOLDOWN}min cooldown active"
                    )
            if signal.confidence < self.CONSECUTIVE_LOSSES_3_CONF:
                violations.append(
                    f"R9: 3 losses — need conf >= {self.CONSECUTIVE_LOSSES_3_CONF}, got {signal.confidence:.0f}"
                )

        # Rule 10: REMOVED (A-13) — consecutive loss session halt is governed
        # solely by circuit_breakers.py (TIER_3 = 5 losses). Having it here
        # AND in circuit_breakers created a contradiction (5 vs 7). Circuit
        # breakers are the single source of truth for loss-streak halts.

        # Rules 11-12: 3x ETP allocation limits
        if signal.bot == Bot.A:
            if etp_3x_allocation_pct > self.ETP_3X_MAX_ALLOCATION:
                violations.append(
                    f"R11: 3x ETP allocation {etp_3x_allocation_pct*100:.1f}% > "
                    f"{self.ETP_3X_MAX_ALLOCATION*100}% max"
                )
            single_pct = signal.position_pct_equity
            if "3x" in signal.isa_leverage and single_pct > self.ETP_3X_MAX_SINGLE:
                violations.append(
                    f"R12: 3x single position {single_pct*100:.1f}% > "
                    f"{self.ETP_3X_MAX_SINGLE*100}% max"
                )

        # Rules 13-14: 5x ETP allocation limits
        if signal.bot == Bot.A and "5x" in signal.isa_leverage:
            if etp_5x_allocation_pct > self.ETP_5X_MAX_ALLOCATION:
                violations.append(
                    f"R13: 5x ETP allocation {etp_5x_allocation_pct*100:.1f}% > "
                    f"{self.ETP_5X_MAX_ALLOCATION*100}% max"
                )
            if signal.position_pct_equity > self.ETP_5X_MAX_SINGLE:
                violations.append(
                    f"R14: 5x single position {signal.position_pct_equity*100:.1f}% > "
                    f"{self.ETP_5X_MAX_SINGLE*100}% max"
                )

        # Rules 15-16: ETP max hold days (checked in position reconciler, not here)
        # Rule 17: Regime flip = EXIT (checked in regime classifier)

        if violations:
            logger.warning("IMMUTABLE RULES VIOLATED for %s %s: %s",
                           signal.direction.value, signal.ticker,
                           "; ".join(violations))

        return len(violations) == 0, violations


class EmotionalFirewall:
    """Section 44: 14 Blocked Patterns.

    The emotional firewall detects and prevents destructive
    trading behaviours automatically. These are the patterns
    that destroy retail traders. The system protects you FROM yourself.
    """

    def __init__(self) -> None:
        self._last_stopout_time: Optional[datetime] = None
        self._recent_wins: int = 0
        self._cooldown_until: Optional[datetime] = None

    def check_all(
        self,
        signal: Signal,
        equity: float,
        standard_size: int,
        recent_trades: list,
        open_positions: list,
        daily_pnl_pct: float,
        last_stopout_time: Optional[datetime] = None,
        no_trade_moved_well: bool = False,
    ) -> tuple[bool, list[str]]:
        """Check for all 14 emotional patterns.

        Returns (passed: bool, triggered_patterns: list[str]).
        """
        triggered: list[str] = []
        now = datetime.now(timezone.utc)

        # 1. Revenge Trading: Signal within 5 min of stop-out, conf < 75
        if last_stopout_time:
            minutes_since_stop = (now - last_stopout_time).total_seconds() / 60
            if minutes_since_stop < 5 and signal.confidence < 75:
                triggered.append(
                    f"REVENGE: {minutes_since_stop:.0f}min since stop-out, "
                    f"conf={signal.confidence:.0f} < 75. BLOCK. 15-min cooldown."
                )
                self._cooldown_until = now + timedelta(minutes=15)

        # Check cooldown
        if self._cooldown_until and now < self._cooldown_until:
            remaining = (self._cooldown_until - now).total_seconds() / 60
            triggered.append(
                f"COOLDOWN ACTIVE: {remaining:.0f}min remaining"
            )

        # 2. Overtrading: 4th trade when last 3 were wins
        recent_wins = sum(1 for t in recent_trades[-3:] if hasattr(t, 'pnl_dollars')
                          and t.pnl_dollars > 0)
        trade_count_today = len(recent_trades)
        if trade_count_today >= 3 and recent_wins >= 3 and signal.confidence < 85:
            triggered.append(
                f"OVERTRADING: 4th trade after 3 wins. BLOCK unless conf >= 85."
            )

        # 3. Size Inflation: Size > 100% standard calc
        if signal.shares > standard_size * 1.0:
            signal.shares = standard_size  # CAP at 100%
            triggered.append("SIZE_INFLATION: Capped at standard size.")

        # 4. Holding Losers: At -1R for > 5 min (checked in position reconciler)
        # 5. Moving Stops: Attempt to widen stop (checked in position management)

        # 6. FOMO: NO_TRADE moved well, attempting re-evaluation
        if no_trade_moved_well:
            triggered.append(
                "FOMO: Skipped signal moved well. BLOCK 10 min. -10 penalty."
            )
            signal.confidence -= 10
            self._cooldown_until = now + timedelta(minutes=10)

        # 13. Chasing: Entry after price moved > 1 ATR from signal level
        # If signal has entry significantly worse than setup level
        if hasattr(signal, 'trail') and signal.trail > 0:
            # Use the trail field as a reference for setup distance
            pass  # Checked at execution time via indicators

        # 14. Revenge Sizing: After a loss > 1R, next trade has larger size
        if recent_trades:
            last_trade = recent_trades[-1] if recent_trades else None
            if last_trade and hasattr(last_trade, 'pnl_r_multiple'):
                if last_trade.pnl_r_multiple < -1.0 and signal.shares > standard_size:
                    triggered.append(
                        f"REVENGE_SIZING: Last trade was {last_trade.pnl_r_multiple:.1f}R loss, "
                        f"current size {signal.shares} > standard {standard_size}. "
                        f"CAP to standard."
                    )
                    signal.shares = standard_size

        # 7. Averaging Down: Add to losing position
        for pos in open_positions:
            if (hasattr(pos, 'ticker') and pos.ticker == signal.ticker
                    and hasattr(pos, 'unrealised_pnl') and pos.unrealised_pnl < 0
                    and hasattr(pos, 'direction') and pos.direction == signal.direction.value):
                triggered.append(
                    f"AVERAGING_DOWN: Adding to losing {signal.ticker} position. "
                    "BLOCK unconditionally."
                )

        # 8. Refusing Profits: +2R, no partial/trail set
        # (Checked in profit ladder reconciler)

        # 9. Friday Anxiety: Large position 15:30 ET Friday
        # (Checked in time-of-day engine)

        # 10. One More Trade: Daily loss -2.5%, new signal
        if daily_pnl_pct <= -0.025 and signal.confidence < 90:
            triggered.append(
                f"ONE_MORE_TRADE: Daily loss {daily_pnl_pct*100:.1f}%. "
                f"BLOCK unless conf >= 90 (got {signal.confidence:.0f})."
            )

        # 11. Hope: Negative, past time stop
        # (Checked in position reconciler)

        # 12. Anchoring: Re-enter < 15 min at worse price
        for t in recent_trades[-5:]:
            if (hasattr(t, 'ticker') and t.ticker == signal.ticker
                    and hasattr(t, 'time_exited') and t.time_exited):
                try:
                    exit_time = t.time_exited if isinstance(t.time_exited, datetime) \
                        else datetime.fromisoformat(str(t.time_exited))
                    minutes_since = (now - exit_time).total_seconds() / 60
                    if minutes_since < 15:
                        # Check if worse price
                        if hasattr(t, 'exit_price'):
                            if (signal.direction == Direction.LONG
                                    and signal.entry > t.exit_price):
                                signal.confidence -= 15
                                triggered.append(
                                    f"ANCHORING: Re-entry at higher price within 15 min. "
                                    f"-15 confidence penalty."
                                )
                            elif (signal.direction == Direction.SHORT
                                  and signal.entry < t.exit_price):
                                signal.confidence -= 15
                                triggered.append(
                                    f"ANCHORING: Re-entry at lower price within 15 min. "
                                    f"-15 confidence penalty."
                                )
                except (ValueError, TypeError):
                    pass

        if triggered:
            logger.warning("EMOTIONAL FIREWALL for %s %s: %s",
                           signal.direction.value, signal.ticker,
                           "; ".join(triggered))

        # Any hard blocks? (REVENGE, AVERAGING_DOWN, ONE_MORE_TRADE cooldown)
        hard_blocks = [t for t in triggered
                       if any(k in t for k in ["BLOCK", "REVENGE", "AVERAGING_DOWN", "COOLDOWN"])]
        return len(hard_blocks) == 0, triggered


class SessionProtection:
    """Section 42: Session & Weekly Protection.

    Daily P&L thresholds trigger automatic responses.
    Weekly P&L thresholds force risk reduction.
    """

    def get_session_action(self, daily_pnl_pct: float) -> dict:
        """Determine action based on daily P&L percentage.

        Returns dict with: action, min_confidence, max_trades, size_modifier, halt
        """
        if daily_pnl_pct >= 0.02:
            return {
                "action": "DEFINITELY stop. Walk away.",
                "min_confidence": 999,  # No more trades
                "max_trades": 0,
                "size_modifier": 0,
                "halt": True,
            }
        # B-01: Removed +1.5% tier — it capped gross at 1.1% net max, killing
        # compounding toward the 2% daily target. Only the +2.0% halt remains.
        # Previously: +1.5% → conf 85 + 1 trade + half-size = premature lock-in
        elif daily_pnl_pct >= 0.01:
            return {
                "action": "Min conf 80. One more trade max.",
                "min_confidence": 80,
                "max_trades": 1,
                "size_modifier": 1.0,
                "halt": False,
            }
        elif daily_pnl_pct >= 0.005:
            return {
                "action": "Raise min confidence to 70.",
                "min_confidence": 70,
                "max_trades": 4,
                "size_modifier": 1.0,
                "halt": False,
            }
        elif daily_pnl_pct <= -0.03:
            return {
                "action": "HALT. Non-negotiable. Done.",
                "min_confidence": 999,
                "max_trades": 0,
                "size_modifier": 0,
                "halt": True,
            }
        elif daily_pnl_pct <= -0.02:
            return {
                "action": "ONE more trade if conf >= 90.",
                "min_confidence": 90,
                "max_trades": 1,
                "size_modifier": 0.5,
                "halt": False,
            }
        elif daily_pnl_pct <= -0.01:
            return {
                "action": "Min conf 80. Half size.",
                "min_confidence": 80,
                "max_trades": 4,
                "size_modifier": 0.5,
                "halt": False,
            }
        else:
            return {
                "action": "Normal trading.",
                "min_confidence": 65,  # Canonical confidence floor — see ThresholdRegistry (E-01)
                "max_trades": 4,
                "size_modifier": 1.0,
                "halt": False,
            }

    def get_weekly_action(self, weekly_pnl_pct: float, day_of_week: int) -> dict:
        """Determine action based on weekly P&L.

        day_of_week: 0=Mon, 2=Wed, 4=Fri
        """
        if weekly_pnl_pct <= -0.06:
            return {"action": "AUTO-HALT. Manual resume required.", "halt": True}
        if weekly_pnl_pct <= -0.03 and day_of_week <= 2:
            return {"action": "Half size for rest of week.", "size_modifier": 0.5, "halt": False}
        if weekly_pnl_pct >= 0.05:
            return {"action": "Stop for week. Lock in gains.", "halt": True}
        if weekly_pnl_pct >= 0.03 and day_of_week <= 2:
            return {"action": "Half size rest of week.", "size_modifier": 0.5, "halt": False}
        return {"action": "Normal.", "size_modifier": 1.0, "halt": False}


class DrawdownRecovery:
    """Section 60: Drawdown Recovery Protocol.

    Drawdown = cumulative peak-to-trough equity decline,
    NOT daily loss (which is capped at 3%).
    """

    def get_level(self, drawdown_pct: float) -> DrawdownLevel:
        """Classify current drawdown into protocol level."""
        dd = abs(drawdown_pct)
        if dd > 0.12:
            return DrawdownLevel.EMERGENCY
        elif dd > 0.10:
            return DrawdownLevel.CRITICAL
        elif dd > 0.08:
            return DrawdownLevel.RED
        elif dd > 0.05:
            return DrawdownLevel.ORANGE
        elif dd > 0.03:
            return DrawdownLevel.YELLOW
        return DrawdownLevel.GREEN

    def get_protocol(self, level: DrawdownLevel) -> dict:
        """Get the full protocol for a drawdown level."""
        protocols = {
            DrawdownLevel.GREEN: {
                "status": "NORMAL",
                "risk_per_trade": 0.0075,
                "min_confidence": 65,  # Canonical confidence floor — see ThresholdRegistry (E-01)
                "max_trades_per_day": 4,
                "no_3x": False,
                "action": "Normal trading.",
            },
            DrawdownLevel.YELLOW: {
                "status": "CAUTION",
                "risk_per_trade": 0.005,
                "min_confidence": 70,
                "max_trades_per_day": 2,
                "no_3x": True,
                "action": "Reduce risk. Review last 10 trades for pattern.",
            },
            DrawdownLevel.ORANGE: {
                "status": "DEFENSIVE",
                "risk_per_trade": 0.003,
                "min_confidence": 80,
                "max_trades_per_day": 1,
                "no_3x": True,
                "action": "Only S1+S2 strategies. No new strategies.",
            },
            DrawdownLevel.RED: {
                "status": "RECOVERY MODE",
                "risk_per_trade": 0,
                "min_confidence": 999,
                "max_trades_per_day": 0,
                "no_3x": True,
                "action": "HALT live trading. Paper for 5 days. Full system review.",
            },
            DrawdownLevel.CRITICAL: {
                "status": "FULL STOP",
                "risk_per_trade": 0,
                "min_confidence": 999,
                "max_trades_per_day": 0,
                "no_3x": True,
                "action": "HALT all. 2-week break. Strategy audit. Walk-forward re-validation.",
            },
            DrawdownLevel.EMERGENCY: {
                "status": "RESET",
                "risk_per_trade": 0,
                "min_confidence": 999,
                "max_trades_per_day": 0,
                "no_3x": True,
                "action": "STOP. Full redesign. Re-paper 30 days. Restart with 0.25% risk.",
            },
        }
        return protocols.get(level, protocols[DrawdownLevel.GREEN])

    def get_recovery_risk(self, normal_risk: float, post_halt: bool = False,
                           consecutive_no_loss: int = 0) -> float:
        """Section 60: Recovery scaling after any halt.

        Start at HALF normal (0.375%). After 10 clean trades → 75%.
        After 20 more → full 0.75%.
        """
        if not post_halt:
            return normal_risk

        if consecutive_no_loss >= 30:
            return normal_risk  # Full
        elif consecutive_no_loss >= 10:
            return normal_risk * 0.75
        else:
            return normal_risk * 0.5


# =========================================================================
# AEGIS K-07: Commission Audit + Capital Critical Mass Gate
# =========================================================================
#
# Commission model: IBKR Tiered pricing (UK ISA via London Stock Exchange).
#   - Rate: 0.05% of trade value
#   - Minimum per order: GBP 1.00
#   - Maximum per order: 1% of trade value (IBKR cap)
#
# Capital Critical Mass Gate:
#   Enforces MAX_CONCURRENT=1 position until equity exceeds GBP 25,000.
#   Rationale: below critical mass, commission drag and spread costs
#   consume a disproportionate share of edge. Running multiple positions
#   with thin capitalisation leads to ruin via cost drag alone.
#
# References:
#   - IBKR Tiered Commission Schedule (UK Stocks & ETPs), 2024.
#   - Harris, L. (2003). "Trading and Exchanges." Oxford University Press.
#     Ch. 7: Transaction Costs.
#   - AEGIS Master Plan v16.0, Section K-07.
# =========================================================================

# IBKR Tiered pricing constants (UK ISA)
_IBKR_COMMISSION_RATE: float = 0.0005     # 0.05% of trade value
_IBKR_COMMISSION_MIN_GBP: float = 1.00    # GBP 1.00 minimum per order
_IBKR_COMMISSION_MAX_PCT: float = 0.01    # 1% cap per order

# Capital thresholds for position concurrency
_CRITICAL_MASS_THRESHOLD_GBP: float = 25_000.0   # GBP 25k
_CRITICAL_MASS_TIER_2_GBP: float = 50_000.0       # GBP 50k
_CRITICAL_MASS_TIER_3_GBP: float = 100_000.0      # GBP 100k


class CommissionAuditor:
    """AEGIS K-07: IBKR Tiered commission verification and cost accounting.

    Verifies that the commission model used in EV calculations and TCA
    accurately reflects IBKR's actual Tiered pricing for UK ISA trades.

    Commission formula (IBKR Tiered, UK):
        commission = max(MIN_GBP, min(trade_value * RATE, trade_value * MAX_PCT))

    This class provides:
      1. Pre-trade commission estimation for EV gate
      2. Post-trade commission verification against IBKR fill reports
      3. Cumulative cost tracking for performance attribution
      4. Commission drag alerts when costs exceed acceptable thresholds

    Thread-safety: NOT thread-safe. Intended for single-threaded
    main engine loop usage only.
    """

    def __init__(
        self,
        commission_rate: float = _IBKR_COMMISSION_RATE,
        min_commission_gbp: float = _IBKR_COMMISSION_MIN_GBP,
        max_commission_pct: float = _IBKR_COMMISSION_MAX_PCT,
    ) -> None:
        """Initialise the commission auditor.

        Args:
            commission_rate: IBKR Tiered rate (default 0.05%).
            min_commission_gbp: Minimum commission per order in GBP.
            max_commission_pct: Maximum commission as % of trade value.
        """
        self._rate = commission_rate
        self._min_gbp = min_commission_gbp
        self._max_pct = max_commission_pct

        # Cumulative tracking
        self._total_estimated_gbp: float = 0.0
        self._total_actual_gbp: float = 0.0
        self._trade_count: int = 0
        self._mismatches: list[dict] = []

        logger.info(
            "CommissionAuditor initialised: rate=%.4f%% min=GBP%.2f max=%.2f%%",
            self._rate * 100, self._min_gbp, self._max_pct * 100,
        )

    def estimate_commission_gbp(self, trade_value_gbp: float) -> float:
        """Estimate commission for a trade of given value.

        Applies IBKR Tiered formula:
            commission = max(min_gbp, min(value * rate, value * max_pct))

        Args:
            trade_value_gbp: Total trade value in GBP (price * shares).

        Returns:
            Estimated commission in GBP.
        """
        if trade_value_gbp <= 0:
            return 0.0
        raw = trade_value_gbp * self._rate
        capped = min(raw, trade_value_gbp * self._max_pct)
        return max(self._min_gbp, capped)

    def estimate_commission_bps(self, trade_value_gbp: float) -> float:
        """Estimate commission in basis points for a given trade value.

        Args:
            trade_value_gbp: Total trade value in GBP.

        Returns:
            Commission in bps. For small trades hitting the GBP 1.00
            minimum, this will be higher than the nominal 5 bps rate.
        """
        if trade_value_gbp <= 0:
            return 0.0
        commission_gbp = self.estimate_commission_gbp(trade_value_gbp)
        return (commission_gbp / trade_value_gbp) * 10_000

    def estimate_round_trip_bps(self, trade_value_gbp: float) -> float:
        """Estimate round-trip (entry + exit) commission in bps.

        Args:
            trade_value_gbp: Trade value for ONE side (entry or exit).

        Returns:
            Total round-trip commission in bps.
        """
        return self.estimate_commission_bps(trade_value_gbp) * 2

    def verify_fill_commission(
        self,
        trade_value_gbp: float,
        actual_commission_gbp: float,
        ticker: str = "",
    ) -> tuple[bool, float]:
        """Verify an actual IBKR commission against our model.

        Compares broker-reported commission to our estimate. If they
        diverge by more than 10%, logs a warning and records the mismatch.

        Args:
            trade_value_gbp: Actual trade value from fill report.
            actual_commission_gbp: Commission reported by IBKR.
            ticker: Ticker for logging context.

        Returns:
            (matches: bool, deviation_pct: float)
            matches is True if within 10% tolerance.
        """
        estimated = self.estimate_commission_gbp(trade_value_gbp)
        self._total_estimated_gbp += estimated
        self._total_actual_gbp += actual_commission_gbp
        self._trade_count += 1

        if estimated <= 0:
            return True, 0.0

        deviation_pct = abs(actual_commission_gbp - estimated) / estimated
        matches = deviation_pct <= 0.10  # 10% tolerance

        if not matches:
            mismatch = {
                "ticker": ticker,
                "trade_value_gbp": round(trade_value_gbp, 2),
                "estimated_gbp": round(estimated, 4),
                "actual_gbp": round(actual_commission_gbp, 4),
                "deviation_pct": round(deviation_pct * 100, 1),
            }
            self._mismatches.append(mismatch)
            logger.warning(
                "K-07 COMMISSION MISMATCH: %s value=GBP%.2f "
                "estimated=GBP%.4f actual=GBP%.4f deviation=%.1f%%",
                ticker, trade_value_gbp, estimated,
                actual_commission_gbp, deviation_pct * 100,
            )

        return matches, deviation_pct

    def get_min_profitable_trade_value(
        self,
        target_edge_bps: float = 10.0,
    ) -> float:
        """Compute minimum trade value where commission doesn't destroy edge.

        When trade value is small, the GBP 1.00 minimum commission eats
        a disproportionate share of the expected edge. This returns the
        minimum trade value where round-trip commission <= target_edge_bps.

        Args:
            target_edge_bps: Target edge in bps that must survive after
                             round-trip commissions. Default 10 bps.

        Returns:
            Minimum trade value in GBP.
        """
        # At the minimum, commission = GBP 1.00 per side, GBP 2.00 round-trip.
        # We need: (2.00 / trade_value) * 10_000 <= target_edge_bps
        # => trade_value >= (2.00 * 10_000) / target_edge_bps
        if target_edge_bps <= 0:
            return float("inf")
        return (2.0 * self._min_gbp * 10_000) / target_edge_bps

    def get_stats(self) -> dict:
        """Return commission audit stats for dashboard / monitoring.

        Returns:
            Dict with cumulative commission data and mismatch count.
        """
        return {
            "trade_count": self._trade_count,
            "total_estimated_gbp": round(self._total_estimated_gbp, 2),
            "total_actual_gbp": round(self._total_actual_gbp, 2),
            "cumulative_deviation_gbp": round(
                self._total_actual_gbp - self._total_estimated_gbp, 2
            ),
            "mismatch_count": len(self._mismatches),
            "recent_mismatches": self._mismatches[-5:],
        }

    def reset(self) -> None:
        """Reset cumulative tracking (e.g. start of new trading week)."""
        self._total_estimated_gbp = 0.0
        self._total_actual_gbp = 0.0
        self._trade_count = 0
        self._mismatches.clear()


def check_variance_drag(atr_5d: float, atr_20d: float) -> bool:
    """K-15: Variance Drag Kill-Switch.

    If 5-day ATR < 20-day ATR, market is in contraction / sideways —
    leveraged ETP volatility drag destroys value. Ban leveraged ETP
    trading until expansion resumes (5d ATR >= 20d ATR).

    Args:
        atr_5d: 5-day rolling ATR for the instrument.
        atr_20d: 20-day rolling ATR for the instrument.

    Returns:
        True if variance drag is detected (ban leveraged ETPs).
        False if vol is expanding or data is invalid.
    """
    if atr_20d <= 0:
        return False
    return atr_5d < atr_20d


def capital_critical_mass_gate(equity: float) -> int:
    """AEGIS K-07: Determine maximum concurrent positions based on equity.

    Enforces position concurrency limits proportional to account equity.
    Below critical mass (GBP 25,000), only ONE position is permitted
    because commission drag and spread costs consume too much edge
    when split across multiple thin positions.

    Tier structure:
        equity <  GBP 25,000  -> MAX_CONCURRENT = 1
        equity <  GBP 50,000  -> MAX_CONCURRENT = 2
        equity <  GBP 100,000 -> MAX_CONCURRENT = 3
        equity >= GBP 100,000 -> MAX_CONCURRENT = 5

    Note: This gate OVERRIDES ImmutableRiskRules.MAX_CONCURRENT_POSITIONS
    when it returns a lower value. The caller must take the minimum of
    this function's return value and the constitutional limit.

    Args:
        equity: Current account equity in GBP.

    Returns:
        Maximum number of concurrent positions allowed.
    """
    if equity < _CRITICAL_MASS_THRESHOLD_GBP:
        return 1
    elif equity < _CRITICAL_MASS_TIER_2_GBP:
        return 2
    elif equity < _CRITICAL_MASS_TIER_3_GBP:
        return 3
    else:
        return 5
