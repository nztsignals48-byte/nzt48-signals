"""
NZT-48 Trading Discipline Engine
================================
"Today's excellence is tomorrow's average"

Core principle: NO trade is better than a BAD trade.
The system must NEVER be forced into trading. If conditions
aren't right, we sit on our hands. Capital preservation is
the first rule of compounding.

Research basis:
- Taleb (2007) "The Black Swan": The cost of NOT trading is zero.
  The cost of a bad trade is real capital destruction.
- Tharp (2006) "Trade Your Way to Financial Freedom":
  Position sizing and selectivity are the primary edge.
- Buffett: "Rule 1: Never lose money. Rule 2: Never forget Rule 1."
- Ed Seykota: "Win or lose, everybody gets what they want out of
  the market. Some people seem to like to lose, so they win by
  losing money."

Academic:
- Odean (1999): Overtrading reduces returns by 3.7% annually
- Barber & Odean (2000): The most active traders underperform by 6.5%
- Samuelson (1963): The fallacy of large numbers — each trade must
  stand on its own merit
"""

from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone

logger = logging.getLogger("nzt48.discipline")

# ═══════════════════════════════════════════════════════════════
# Constants: The Discipline Framework
# ═══════════════════════════════════════════════════════════════

# Minimum setup quality to enter ANY trade (0-100)
# Below this, we DO NOT TRADE. Period.
MIN_SETUP_QUALITY = 65

# Minimum edge expectancy per trade (in R-multiples)
# If the adaptive engine says expected R < this, skip.
MIN_EDGE_EXPECTANCY = 0.10  # +0.10R minimum

# Maximum consecutive losing trades before forced cooldown.
# Statistical calibration (Tharp 2006): at 55% WR, P(3 consecutive losses) = 0.45^3 = 9.1%
# — too high, triggers false cooldowns every ~8 trade sequences.
# P(4 consecutive losses) = 0.45^4 = 4.1% — meaningful signal at 5% significance.
# Raised from 3 to 4 to reduce false positives while maintaining risk control.
MAX_CONSECUTIVE_LOSSES = 4

# Cooldown period after max consecutive losses (minutes)
LOSS_COOLDOWN_MINUTES = 120  # 2 hours

# Daily loss governed solely by L1/L2/L3 cascade in circuit_breakers.py
# (L1=1.5% YELLOW, L2=2.5% ORANGE, L3=4.0% RED). Removed MAX_DAILY_LOSS_PCT=3.0
# which contradicted the graduated cascade — see AEGIS F-08.

# Maximum trades per day (prevents overtrading)
MAX_TRADES_PER_DAY = 4

# "No Trade" streak patience — system doesn't panic if no trades for N days
MAX_NO_TRADE_DAYS_BEFORE_REVIEW = 5

# Quality decay: how much worse does a setup need to be to trigger anyway
# after N days of no trades (prevents indefinite drought)
QUALITY_DECAY_PER_DAY = 2  # Lower threshold by 2 points per drought day
MIN_QUALITY_FLOOR = 50  # But never below this

# Continuous improvement: tracks rolling performance metrics
ROLLING_WINDOW_TRADES = 50  # Assess performance over last 50 trades
EXCELLENCE_WIN_RATE = 0.55  # Current excellence standard
IMPROVEMENT_RATE = 0.01  # Raise the bar by 1% when hit


@dataclass
class DisciplineState:
    """Tracks the system's discipline metrics in real-time."""
    trades_today: int = 0
    daily_pnl_pct: float = 0.0
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    last_trade_time: float = 0.0
    cooldown_until: float = 0.0
    no_trade_days: int = 0
    last_trade_date: str = ""

    # Rolling performance
    rolling_wins: int = 0
    rolling_losses: int = 0
    rolling_total_r: float = 0.0

    # Excellence tracking
    current_excellence_bar: float = EXCELLENCE_WIN_RATE
    times_bar_raised: int = 0


class TradingDisciplineEngine:
    """
    The discipline engine sits BEFORE any trade execution.
    It has absolute veto power over every trade.

    Philosophy: "I'd rather not trade for a day than force a
    trade and lose." — The system owner's golden rule.

    The engine tracks:
    1. Daily loss limits (hard stop at -3%)
    2. Trade frequency (max 4/day, prevent overtrading)
    3. Consecutive loss streaks (cooldown after 3 losses)
    4. Setup quality minimums (never enter below threshold)
    5. Edge expectancy (only trade when math is positive)
    6. Rolling excellence (continuously raise the bar)
    """

    def __init__(self) -> None:
        self.state = DisciplineState()
        self._lock_file = "data/discipline_state.json"
        self._load_state()
        logger.info(
            "TradingDisciplineEngine initialized | "
            "Excellence bar: %.1f%% | Consecutive losses: %d | "
            "Trades today: %d",
            self.state.current_excellence_bar * 100,
            self.state.consecutive_losses,
            self.state.trades_today,
        )

    # ═══════════════════════════════════════════════════════════
    # THE GATE: Should we trade at all right now?
    # ═══════════════════════════════════════════════════════════

    def should_trade(
        self,
        setup_quality: float,
        expected_r: float = 0.0,
        regime: str = "NEUTRAL",
        vix: float = 0.0,
    ) -> tuple[bool, str]:
        """
        THE critical gate. Returns (allowed, reason).

        If this returns False, the trade MUST NOT be taken.
        No override. No exception. No "but this one looks good."

        "The best trade you ever make is the one you don't take
        when conditions aren't right." — Mark Douglas
        """
        now = time.time()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Reset daily counters if new day
        if today != self.state.last_trade_date:
            self._reset_daily()
            self.state.last_trade_date = today

        # ── Gate 1: REMOVED (AEGIS F-08) — daily loss is governed solely by
        # L1/L2/L3 cascade in circuit_breakers.py. Having a separate 3% gate
        # here contradicted the graduated 1.5%/2.5%/4.0% thresholds.

        # ── Gate 2: Cooldown after consecutive losses ──
        if now < self.state.cooldown_until:
            remaining = int((self.state.cooldown_until - now) / 60)
            return False, (
                f"COOLDOWN ACTIVE: {remaining} minutes remaining after "
                f"{MAX_CONSECUTIVE_LOSSES} consecutive losses. "
                "Stepping back to avoid tilt. Patience is profitable."
            )

        # ── Gate 3: Max trades per day ──
        if self.state.trades_today >= MAX_TRADES_PER_DAY:
            return False, (
                f"MAX TRADES: Already took {self.state.trades_today} trades today "
                f"(limit: {MAX_TRADES_PER_DAY}). Quality > quantity. "
                "Overtrading is the #1 retail killer (Barber & Odean, 2000)."
            )

        # ── Gate 4: Setup quality minimum ──
        quality_threshold = self._get_quality_threshold()
        if setup_quality < quality_threshold:
            return False, (
                f"QUALITY GATE: Setup quality {setup_quality:.0f} < "
                f"threshold {quality_threshold:.0f}. "
                "No trade is better than a bad trade. Waiting for excellence."
            )

        # ── Gate 5: Edge expectancy ──
        if expected_r > 0 and expected_r < MIN_EDGE_EXPECTANCY:
            return False, (
                f"EDGE GATE: Expected R={expected_r:.2f} < "
                f"minimum {MIN_EDGE_EXPECTANCY:.2f}. "
                "Only trade when the math is in our favor."
            )

        # ── Gate 6: SHOCK regime absolute block ──
        if regime == "SHOCK":
            return False, (
                "REGIME BLOCK: SHOCK regime active. "
                "Sit on hands. Cash is a position. "
                "Taleb (2007): The cost of not trading is zero."
            )

        # ── Gate 7: VIX extreme caution ──
        if vix > 35:
            return False, (
                f"VIX EXTREME: VIX={vix:.1f} > 35. "
                "Market in panic. Step aside entirely. "
                "Preservation mode active."
            )

        # All gates passed
        return True, "CLEARED: All discipline gates passed. Trade authorized."

    # ═══════════════════════════════════════════════════════════
    # Trade outcome recording
    # ═══════════════════════════════════════════════════════════

    def record_trade(self, r_multiple: float, pnl_pct: float) -> dict:
        """
        Record a completed trade and update all discipline metrics.

        Returns insights dict with any triggered events.
        """
        self.state.trades_today += 1
        self.state.daily_pnl_pct += pnl_pct
        self.state.last_trade_time = time.time()

        insights = {"events": []}

        if r_multiple > 0:
            self.state.consecutive_wins += 1
            self.state.consecutive_losses = 0
            self.state.rolling_wins += 1
            insights["events"].append(
                f"WIN #{self.state.consecutive_wins}: +{r_multiple:.1f}R"
            )
        else:
            self.state.consecutive_losses += 1
            self.state.consecutive_wins = 0
            self.state.rolling_losses += 1
            insights["events"].append(
                f"LOSS #{self.state.consecutive_losses}: {r_multiple:.1f}R"
            )

            # Trigger cooldown if streak hits max
            if self.state.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
                self.state.cooldown_until = time.time() + (LOSS_COOLDOWN_MINUTES * 60)
                insights["events"].append(
                    f"COOLDOWN TRIGGERED: {LOSS_COOLDOWN_MINUTES}min break "
                    f"after {MAX_CONSECUTIVE_LOSSES} consecutive losses. "
                    "This is discipline, not defeat."
                )

        self.state.rolling_total_r += r_multiple

        # Check if we've raised the excellence bar
        self._check_excellence_improvement(insights)

        # Save state
        self._save_state()

        return insights

    def record_no_trade_day(self) -> dict:
        """
        Called at EOD when no trades were taken.
        This is NOT a failure — it's discipline.
        """
        self.state.no_trade_days += 1

        insight = {
            "no_trade_days": self.state.no_trade_days,
            "message": (
                f"Day {self.state.no_trade_days} with no trades. "
                "This is discipline, not inactivity. "
                "The market owes us nothing."
            ),
        }

        if self.state.no_trade_days >= MAX_NO_TRADE_DAYS_BEFORE_REVIEW:
            insight["review_needed"] = True
            insight["message"] += (
                f" ({MAX_NO_TRADE_DAYS_BEFORE_REVIEW} days reached — "
                "review quality thresholds, but do NOT lower standards "
                "just to trade.)"
            )

        self._save_state()
        return insight

    # ═══════════════════════════════════════════════════════════
    # Excellence tracking: "Today's excellence is tomorrow's average"
    # ═══════════════════════════════════════════════════════════

    def _check_excellence_improvement(self, insights: dict) -> None:
        """
        The continuous improvement engine.

        When rolling win rate exceeds the excellence bar,
        RAISE THE BAR. Yesterday's excellence becomes today's average.

        This ensures the system never gets complacent.
        """
        total = self.state.rolling_wins + self.state.rolling_losses
        if total < ROLLING_WINDOW_TRADES:
            return  # Not enough data yet

        rolling_wr = self.state.rolling_wins / total

        if rolling_wr > self.state.current_excellence_bar:
            old_bar = self.state.current_excellence_bar
            self.state.current_excellence_bar += IMPROVEMENT_RATE
            self.state.times_bar_raised += 1

            insights["events"].append(
                f"EXCELLENCE BAR RAISED: {old_bar*100:.0f}% -> "
                f"{self.state.current_excellence_bar*100:.0f}% "
                f"(raised {self.state.times_bar_raised} times). "
                "Today's excellence is tomorrow's average."
            )

            # Reset rolling counters for next window
            self.state.rolling_wins = 0
            self.state.rolling_losses = 0
            self.state.rolling_total_r = 0.0

            logger.info(
                "Excellence bar raised to %.1f%% (raised %d times)",
                self.state.current_excellence_bar * 100,
                self.state.times_bar_raised,
            )

    def get_excellence_report(self) -> dict:
        """Get current excellence metrics for dashboard/Telegram."""
        total = self.state.rolling_wins + self.state.rolling_losses
        rolling_wr = (
            self.state.rolling_wins / total if total > 0 else 0.0
        )
        avg_r = (
            self.state.rolling_total_r / total if total > 0 else 0.0
        )

        return {
            "excellence_bar": self.state.current_excellence_bar,
            "rolling_win_rate": rolling_wr,
            "rolling_avg_r": avg_r,
            "rolling_trades": total,
            "target_trades": ROLLING_WINDOW_TRADES,
            "times_bar_raised": self.state.times_bar_raised,
            "consecutive_losses": self.state.consecutive_losses,
            "consecutive_wins": self.state.consecutive_wins,
            "trades_today": self.state.trades_today,
            "daily_pnl_pct": self.state.daily_pnl_pct,
            "no_trade_days": self.state.no_trade_days,
            "in_cooldown": time.time() < self.state.cooldown_until,
            "motto": "Today's excellence is tomorrow's average",
        }

    # ═══════════════════════════════════════════════════════════
    # Internal helpers
    # ═══════════════════════════════════════════════════════════

    def _get_quality_threshold(self) -> float:
        """
        Dynamic quality threshold that adjusts during drought.

        After MAX_NO_TRADE_DAYS_BEFORE_REVIEW days of no trades,
        the threshold SLIGHTLY decays (by 2 points/day) to prevent
        the system from being permanently locked out.

        BUT it NEVER goes below MIN_QUALITY_FLOOR (50).
        This prevents desperation trades.
        """
        base = MIN_SETUP_QUALITY
        if self.state.no_trade_days > MAX_NO_TRADE_DAYS_BEFORE_REVIEW:
            decay_days = self.state.no_trade_days - MAX_NO_TRADE_DAYS_BEFORE_REVIEW
            decay = decay_days * QUALITY_DECAY_PER_DAY
            return max(MIN_QUALITY_FLOOR, base - decay)
        return base

    def _reset_daily(self) -> None:
        """Reset daily counters at start of new trading day."""
        if self.state.trades_today == 0:
            self.state.no_trade_days += 1
        else:
            self.state.no_trade_days = 0

        self.state.trades_today = 0
        self.state.daily_pnl_pct = 0.0

    def _save_state(self) -> None:
        """Persist discipline state to disk."""
        import json
        from pathlib import Path
        try:
            path = Path(self._lock_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            state_dict = {
                "trades_today": self.state.trades_today,
                "daily_pnl_pct": self.state.daily_pnl_pct,
                "consecutive_losses": self.state.consecutive_losses,
                "consecutive_wins": self.state.consecutive_wins,
                "last_trade_time": self.state.last_trade_time,
                "cooldown_until": self.state.cooldown_until,
                "no_trade_days": self.state.no_trade_days,
                "last_trade_date": self.state.last_trade_date,
                "rolling_wins": self.state.rolling_wins,
                "rolling_losses": self.state.rolling_losses,
                "rolling_total_r": self.state.rolling_total_r,
                "current_excellence_bar": self.state.current_excellence_bar,
                "times_bar_raised": self.state.times_bar_raised,
            }
            path.write_text(json.dumps(state_dict, indent=2))
        except Exception as e:
            logger.warning("Failed to save discipline state: %s", e)

    def _load_state(self) -> None:
        """Load discipline state from disk."""
        import json
        from pathlib import Path
        try:
            path = Path(self._lock_file)
            if path.exists():
                data = json.loads(path.read_text())
                for key, value in data.items():
                    if hasattr(self.state, key):
                        setattr(self.state, key, value)
                logger.info("Loaded discipline state from disk")
        except Exception as e:
            logger.warning("Failed to load discipline state: %s", e)
