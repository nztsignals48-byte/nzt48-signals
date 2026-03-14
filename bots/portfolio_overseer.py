"""
NZT-48 Portfolio Overseer
Phase 2, Section 67: Meta-level risk manager sitting above all bots.
Runs as a separate 30-second loop, monitoring cross-bot risk.

8 Core Functions:
1. Aggregate Exposure Control — max 150% net exposure
2. Cross-Bot Correlation — prevent correlated positions across bots
3. Sector Stacking Prevention — max 50% in one sector
4. Drawdown Cascade Detection — detect multi-bot drawdown spiral
5. Direction Concentration — max 85% same direction
6. Daily Loss Aggregation — halt all if combined > -1.5%
7. Capital Allocation Drift — detect and flag rebalancing needs
8. Regime Disagreement — resolve conflicting regime signals
"""
from __future__ import annotations
import logging
import threading
from datetime import datetime, timezone
from typing import Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import (
    Signal, Direction, Bot, BotInstance, RegimeState,
    Position, Restriction, RestrictionType, DrawdownLevel,
)
import config as cfg


# Sector mapping for the 12 Bot B tickers
TICKER_SECTOR = {
    "NVDA": "semiconductors",
    "AMD": "semiconductors",
    "MU": "memory",
    "SNDK": "memory",
    "AVGO": "semiconductors",
    "MRVL": "semiconductors",
    "ARM": "semiconductors",
    "TSM": "semiconductors",
    "ASML": "semiconductor_equip",
    "SMCI": "ai_infrastructure",
    "VRT": "ai_infrastructure",
    "TSLA": "ev_auto",
}

# Correlation groups — tickers that tend to move together
CORRELATION_GROUPS = [
    {"NVDA", "AMD", "AVGO", "MRVL", "ARM"},  # Fabless semis
    {"MU", "SNDK"},                            # Memory
    {"SMCI", "VRT"},                           # AI infrastructure
    {"TSM", "ASML"},                           # Foundry/equipment
]


class OverseerAction:
    """An action the Overseer wants to take."""
    def __init__(self, function_name: str, severity: str,
                 description: str, affected_bots: list[str],
                 restrictions: list[Restriction] | None = None):
        self.function_name = function_name
        self.severity = severity  # INFO, WARNING, CRITICAL
        self.description = description
        self.affected_bots = affected_bots
        self.restrictions = restrictions or []
        self.timestamp = datetime.now(timezone.utc)


class PortfolioOverseer:
    """Meta-level risk manager that monitors all bots.

    Runs every 30 seconds. Checks 8 risk functions.
    Can halt bots, force-close positions, and create restrictions.
    """

    def __init__(self):
        self.logger = logging.getLogger("nzt48.overseer")

        # Load config
        ov_cfg = cfg.get("overseer", {})
        funcs = ov_cfg.get("functions", {})

        self.check_interval = ov_cfg.get("check_interval_seconds", 30)
        self.max_net_exposure = funcs.get("aggregate_exposure", {}).get("max_net_exposure", 1.50)
        self.correlation_threshold = funcs.get("cross_bot_correlation", {}).get("threshold", 0.80)
        self.max_sector_pct = funcs.get("sector_stacking", {}).get("max_sector_pct", 0.50)
        self.max_same_direction = funcs.get("direction_concentration", {}).get("max_same_direction", 0.85)
        self.max_combined_loss = funcs.get("daily_loss_aggregation", {}).get("max_combined_loss", -0.015)
        self.max_allocation_drift = funcs.get("capital_allocation_drift", {}).get("max_drift", 0.15)

        # State
        self._lock = threading.Lock()
        self.active_restrictions: list[Restriction] = []
        self.last_check: Optional[datetime] = None
        self._session_halted = False

    def run_all_checks(
        self,
        positions: list[dict],
        bot_statuses: list[dict],
        equity: float,
        bot_daily_pnls: dict[str, float],
    ) -> list[OverseerAction]:
        """Run all 8 overseer functions and return any actions needed.

        Args:
            positions: List of position dicts with keys:
                ticker, direction, bot_instance, entry, shares, current_price, sector
            bot_statuses: List of bot status dicts from each bot's get_status()
            equity: Total portfolio equity
            bot_daily_pnls: Dict of bot_instance -> daily P&L as fraction
        """
        self.last_check = datetime.now(timezone.utc)
        actions = []

        # 1. Aggregate Exposure
        action = self._check_aggregate_exposure(positions, equity)
        if action:
            actions.append(action)

        # 2. Cross-Bot Correlation
        action = self._check_cross_bot_correlation(positions)
        if action:
            actions.append(action)

        # 3. Sector Stacking
        action = self._check_sector_stacking(positions, equity)
        if action:
            actions.append(action)

        # 4. Drawdown Cascade
        action = self._check_drawdown_cascade(bot_statuses, bot_daily_pnls)
        if action:
            actions.append(action)

        # 5. Direction Concentration
        action = self._check_direction_concentration(positions, equity)
        if action:
            actions.append(action)

        # 6. Daily Loss Aggregation
        action = self._check_daily_loss_aggregation(bot_daily_pnls)
        if action:
            actions.append(action)

        # 7. Capital Allocation Drift
        action = self._check_allocation_drift(positions, bot_statuses, equity)
        if action:
            actions.append(action)

        # 8. Regime Disagreement
        action = self._check_regime_disagreement(bot_statuses)
        if action:
            actions.append(action)

        if actions:
            self.logger.warning(
                "OVERSEER: %d actions generated — %s",
                len(actions),
                ", ".join(a.function_name for a in actions),
            )

        return actions

    def _check_aggregate_exposure(
        self, positions: list[dict], equity: float,
    ) -> Optional[OverseerAction]:
        """Function 1: Max 150% net exposure across all bots."""
        if equity <= 0:
            return None

        total_long = 0.0
        total_short = 0.0
        bot_exposure: dict[str, float] = {}

        for pos in positions:
            value = pos.get("shares", 0) * pos.get("current_price", 0)
            bot = pos.get("bot_instance", "")
            bot_exposure[bot] = bot_exposure.get(bot, 0) + value

            if pos.get("direction") == "LONG":
                total_long += value
            else:
                total_short += value

        net_exposure = (total_long - total_short) / equity if equity > 0 else 0

        if net_exposure > self.max_net_exposure:
            # Find bot with largest exposure
            largest_bot = max(bot_exposure, key=bot_exposure.get) if bot_exposure else ""
            return OverseerAction(
                function_name="aggregate_exposure",
                severity="WARNING",
                description=(
                    f"Net exposure {net_exposure:.0%} > {self.max_net_exposure:.0%}. "
                    f"Block new entries on {largest_bot} until < 120%."
                ),
                affected_bots=[largest_bot],
                restrictions=[Restriction(
                    bot_instance=largest_bot,
                    restriction_type=RestrictionType.HALT,
                    value="new_entries_blocked",
                    reason=f"Exposure {net_exposure:.0%} > limit",
                )],
            )
        return None

    def _check_cross_bot_correlation(
        self, positions: list[dict],
    ) -> Optional[OverseerAction]:
        """Function 2: Detect correlated positions across bots.

        Computes overlap ratio: (positions in correlated group / total positions)
        compared against the correlation_threshold config (default 0.80).
        When a large fraction of positions are concentrated in one correlation
        group across multiple bots, the effective diversification is low.
        """
        if not positions:
            return None

        # Group positions by bot
        bot_tickers: dict[str, set] = {}
        for pos in positions:
            bot = pos.get("bot_instance", "")
            ticker = pos.get("ticker", "")
            if bot not in bot_tickers:
                bot_tickers[bot] = set()
            bot_tickers[bot].add(ticker)

        total_positions = len(positions)

        # Check each correlation group
        for group in CORRELATION_GROUPS:
            bots_with_group_positions = set()
            group_position_count = 0
            for pos in positions:
                if pos.get("ticker", "") in group:
                    group_position_count += 1
                    bots_with_group_positions.add(pos.get("bot_instance", ""))

            if group_position_count == 0:
                continue

            # Correlation ratio: what fraction of total positions are in this group
            correlation_ratio = group_position_count / total_positions

            if (correlation_ratio >= self.correlation_threshold
                    and len(bots_with_group_positions) >= 2):
                return OverseerAction(
                    function_name="cross_bot_correlation",
                    severity="WARNING",
                    description=(
                        f"Correlation alert: {group_position_count}/{total_positions} positions "
                        f"({correlation_ratio:.0%}) in {group} across "
                        f"{len(bots_with_group_positions)} bots (threshold {self.correlation_threshold:.0%}). "
                        f"Force-close smallest."
                    ),
                    affected_bots=list(bots_with_group_positions),
                )
        return None

    def _check_sector_stacking(
        self, positions: list[dict], equity: float,
    ) -> Optional[OverseerAction]:
        """Function 3: Max 50% of equity in one sector."""
        if equity <= 0:
            return None

        sector_exposure: dict[str, float] = {}
        for pos in positions:
            ticker = pos.get("ticker", "")
            sector = TICKER_SECTOR.get(ticker, "other")
            value = pos.get("shares", 0) * pos.get("current_price", 0)
            # Short positions reduce sector exposure; long positions increase it
            if pos.get("direction") == "SHORT":
                sector_exposure[sector] = sector_exposure.get(sector, 0) - value
            else:
                sector_exposure[sector] = sector_exposure.get(sector, 0) + value

        for sector, value in sector_exposure.items():
            pct = abs(value) / equity
            if pct > self.max_sector_pct:
                return OverseerAction(
                    function_name="sector_stacking",
                    severity="WARNING",
                    description=(
                        f"Sector stacking: {sector} = {pct:.0%} > {self.max_sector_pct:.0%}. "
                        f"Block new same-sector entries until < 40%."
                    ),
                    affected_bots=[],  # Affects all bots
                    restrictions=[Restriction(
                        restriction_type=RestrictionType.SECTOR,
                        value=sector,
                        reason=f"Sector {sector} at {pct:.0%}",
                    )],
                )
        return None

    def _check_drawdown_cascade(
        self, bot_statuses: list[dict], bot_daily_pnls: dict[str, float],
    ) -> Optional[OverseerAction]:
        """Function 4: Detect multi-bot drawdown cascade.
        Trigger: 2+ bots in YELLOW drawdown AND combined > -5%."""
        bots_in_yellow = []
        combined_pnl = sum(bot_daily_pnls.values())

        for status in bot_statuses:
            if status.get("drawdown_level") in ["YELLOW", "ORANGE", "RED", "CRITICAL"]:
                bots_in_yellow.append(status.get("instance", ""))

        if len(bots_in_yellow) >= 2 and combined_pnl < -0.05:
            return OverseerAction(
                function_name="drawdown_cascade",
                severity="CRITICAL",
                description=(
                    f"DRAWDOWN CASCADE: {len(bots_in_yellow)} bots in drawdown, "
                    f"combined P&L = {combined_pnl:.1%}. ALL bots to ORANGE parameters."
                ),
                affected_bots=bots_in_yellow,
            )
        return None

    def _check_direction_concentration(
        self, positions: list[dict], equity: float,
    ) -> Optional[OverseerAction]:
        """Function 5: Max 85% of exposure in same direction (equity-based)."""
        if not positions or equity <= 0:
            return None

        total_long = 0.0
        total_short = 0.0
        for pos in positions:
            value = pos.get("shares", 0) * pos.get("current_price", 0)
            if pos.get("direction") == "LONG":
                total_long += value
            else:
                total_short += value

        # Use equity as the denominator for direction concentration
        long_pct = total_long / equity
        short_pct = total_short / equity

        if long_pct > self.max_same_direction:
            return OverseerAction(
                function_name="direction_concentration",
                severity="WARNING",
                description=(
                    f"Direction concentration: {long_pct:.0%} LONG > {self.max_same_direction:.0%}. "
                    f"Need opposing hedge or flatten smallest bot."
                ),
                affected_bots=[],
                restrictions=[Restriction(
                    restriction_type=RestrictionType.DIRECTION,
                    value="LONG",
                    reason=f"Long concentration {long_pct:.0%}",
                )],
            )
        elif short_pct > self.max_same_direction:
            return OverseerAction(
                function_name="direction_concentration",
                severity="WARNING",
                description=(
                    f"Direction concentration: {short_pct:.0%} SHORT > {self.max_same_direction:.0%}. "
                    f"Need opposing hedge or flatten smallest bot."
                ),
                affected_bots=[],
                restrictions=[Restriction(
                    restriction_type=RestrictionType.DIRECTION,
                    value="SHORT",
                    reason=f"Short concentration {short_pct:.0%}",
                )],
            )
        return None

    def _check_daily_loss_aggregation(
        self, bot_daily_pnls: dict[str, float],
    ) -> Optional[OverseerAction]:
        """Function 6: HALT all bots if combined daily loss > -1.5%."""
        combined = sum(bot_daily_pnls.values())

        if combined < self.max_combined_loss:
            self._session_halted = True
            return OverseerAction(
                function_name="daily_loss_aggregation",
                severity="CRITICAL",
                description=(
                    f"DAILY LOSS LIMIT: Combined P&L = {combined:.2%} < "
                    f"{self.max_combined_loss:.2%}. HALT ALL BOTS."
                ),
                affected_bots=list(bot_daily_pnls.keys()),
            )
        return None

    def _check_allocation_drift(
        self, positions: list[dict], bot_statuses: list[dict], equity: float,
    ) -> Optional[OverseerAction]:
        """Function 7: Detect capital allocation drift > 15% from target."""
        if equity <= 0:
            return None

        # Calculate actual allocation per bot
        bot_capital: dict[str, float] = {}
        for pos in positions:
            bot = pos.get("bot_instance", "")
            value = pos.get("shares", 0) * pos.get("current_price", 0)
            bot_capital[bot] = bot_capital.get(bot, 0) + value

        # Compare to targets
        drift_bots = []
        for status in bot_statuses:
            bot = status.get("instance", "")
            target = status.get("capital_allocation", 0.33)
            actual = bot_capital.get(bot, 0) / equity if equity > 0 else 0
            drift = abs(actual - target)

            if drift > self.max_allocation_drift:
                drift_bots.append(f"{bot}: target={target:.0%}, actual={actual:.0%}")

        if drift_bots:
            return OverseerAction(
                function_name="capital_allocation_drift",
                severity="INFO",
                description=(
                    f"Allocation drift detected: {'; '.join(drift_bots)}. "
                    f"Rebalance at next weekly review."
                ),
                affected_bots=[b.split(":")[0] for b in drift_bots],
            )
        return None

    def _check_regime_disagreement(
        self, bot_statuses: list[dict],
    ) -> Optional[OverseerAction]:
        """Function 8: Detect conflicting regime assumptions across bots."""
        active_bots = [s for s in bot_statuses if s.get("active")]

        if len(active_bots) < 2:
            return None

        # Check if bull and bear are both active (regime disagreement)
        active_instances = {s.get("instance") for s in active_bots}

        if "BULL" in active_instances and "BEAR" in active_instances:
            return OverseerAction(
                function_name="regime_disagreement",
                severity="CRITICAL",
                description=(
                    "REGIME DISAGREEMENT: BULL-BOT and BEAR-BOT both active. "
                    "HALT both until consensus. Use longest timeframe as tiebreaker."
                ),
                affected_bots=["BULL", "BEAR"],
            )
        return None

    def evaluate_signal(
        self, signal: Signal, positions: list[dict], equity: float,
    ) -> tuple[bool, str, float]:
        """Evaluate a signal against overseer constraints.

        Returns: (approved, status_string, portfolio_heat)
        """
        if self._session_halted:
            return False, "SESSION_HALTED", 1.0

        # Check restrictions (thread-safe read)
        with self._lock:
            restrictions_snapshot = list(self.active_restrictions)

        for restriction in restrictions_snapshot:
            if restriction.restriction_type == RestrictionType.HALT:
                if restriction.bot_instance == signal.bot_instance.value:
                    return False, f"BOT_HALTED: {restriction.reason}", 0.0

            elif restriction.restriction_type == RestrictionType.SECTOR:
                ticker_sector = TICKER_SECTOR.get(signal.ticker, "other")
                if ticker_sector == restriction.value:
                    return False, f"SECTOR_BLOCKED: {restriction.reason}", 0.0

            elif restriction.restriction_type == RestrictionType.DIRECTION:
                if signal.direction.value == restriction.value:
                    return False, f"DIRECTION_BLOCKED: {restriction.reason}", 0.0

            elif restriction.restriction_type == RestrictionType.TICKER:
                if signal.ticker == restriction.value:
                    return False, f"TICKER_BLOCKED: {restriction.reason}", 0.0

        # Calculate portfolio heat (0-1)
        total_risk = sum(
            pos.get("shares", 0) * abs(pos.get("current_price", 0) - pos.get("stop", 0))
            for pos in positions
        )
        portfolio_heat = total_risk / equity if equity > 0 else 0

        return True, "CLEAR", portfolio_heat

    def apply_actions(self, actions: list[OverseerAction], bot_router) -> None:
        """Apply overseer actions to the bot router.

        Handles all 8 action types with bot-level enforcement:
        1. aggregate_exposure  — halt largest-exposure bot, block new entries
        2. cross_bot_correlation — halt affected bots
        3. sector_stacking — add sector restriction (handled via restrictions)
        4. drawdown_cascade — set all bots to ORANGE parameters
        5. direction_concentration — add direction restriction
        6. daily_loss_aggregation — halt ALL bots
        7. capital_allocation_drift — informational, log only
        8. regime_disagreement — halt conflicting bots
        """
        all_bots = [bot_router.bull, bot_router.range, bot_router.bear]

        for action in actions:
            self.logger.warning("OVERSEER ACTION: [%s] %s", action.severity, action.description)

            if action.function_name == "aggregate_exposure":
                # Halt the bot with the largest exposure, block new entries
                for bot_name in action.affected_bots:
                    for bot in all_bots:
                        if bot.instance.value == bot_name:
                            bot.halt(f"Aggregate exposure limit — {action.description}")

            elif action.function_name == "cross_bot_correlation":
                # Halt affected bots until correlation is reduced
                for bot_name in action.affected_bots:
                    for bot in all_bots:
                        if bot.instance.value == bot_name:
                            bot.halt(f"Cross-bot correlation — {action.description}")

            elif action.function_name == "sector_stacking":
                # Restrictions are applied below; no additional bot-level action needed
                pass

            elif action.function_name == "drawdown_cascade":
                # Set all bots to ORANGE parameters
                for bot in all_bots:
                    bot.drawdown_level = DrawdownLevel.ORANGE
                    bot.min_confidence = max(bot.min_confidence, 80)
                    bot.max_positions = min(bot.max_positions, 2)

            elif action.function_name == "direction_concentration":
                # Restrictions are applied below; no additional bot-level action needed
                pass

            elif action.function_name == "daily_loss_aggregation":
                bot_router.halt_all(action.description)

            elif action.function_name == "capital_allocation_drift":
                # Informational only — log for weekly review, no enforcement
                self.logger.info("ALLOCATION DRIFT (info): %s", action.description)

            elif action.function_name == "regime_disagreement":
                for bot_name in action.affected_bots:
                    for bot in all_bots:
                        if bot.instance.value == bot_name:
                            bot.halt("Regime disagreement — awaiting consensus")

            # Add any restrictions (thread-safe)
            with self._lock:
                for restriction in action.restrictions:
                    self.active_restrictions.append(restriction)

    def get_status(self) -> dict:
        """Get overseer status for Telegram /overseer command."""
        return {
            "last_check": self.last_check.isoformat() if self.last_check else "Never",
            "session_halted": self._session_halted,
            "active_restrictions": len(self.active_restrictions),
            "restrictions": [
                {
                    "type": r.restriction_type.value,
                    "value": r.value,
                    "reason": r.reason,
                    "bot": r.bot_instance,
                }
                for r in self.active_restrictions
            ],
        }

    def reset_daily(self) -> None:
        """Reset for new session."""
        with self._lock:
            self.active_restrictions.clear()
        self._session_halted = False
        self.last_check = None
