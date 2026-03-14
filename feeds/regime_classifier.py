"""
NZT-48 Trading System — 8-State Regime Classifier
Section 7: Layer 2 of the 5-Layer Perception Engine.

The regime classifier determines the CURRENT market state and dictates
which bot instance activates, which strategies fire, position sizing,
and whether 3x/5x ETPs are allowed.

8 States: TRENDING_UP (strong/mod), TRENDING_DOWN (strong/mod),
RANGE_BOUND, HIGH_VOLATILITY, RISK_OFF, SHOCK.

Inputs: QQQ/SPY price vs VWAP, EMA alignment, slope, VIX level.
Output: RegimeState enum + confidence + transition actions.
"""

from __future__ import annotations

import logging
import sys
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from models import RegimeState, TimeWindow

logger = logging.getLogger("nzt48.regime")

# G-09: Flapping detection constants
_FLAP_WINDOW_SECONDS = 600       # 10 minutes
_FLAP_CHANGE_THRESHOLD = 3       # 3+ changes in window = flapping
_FLAP_COOLDOWN_SECONDS = 1800    # 30 min stable before auto-clear
_FLAP_SIZE_MULTIPLIER = 0.50     # Reduce existing positions by 50%

# G-10: Post-recovery ramp-up constants
_RAMP_RISK_OFF_SECONDS = 900     # 15 min at 0.50x after RISK_OFF -> NORMAL
_RAMP_RISK_OFF_MULT = 0.50
_RAMP_SHOCK_SECONDS = 1800       # 30 min at 0.25x after SHOCK -> NORMAL
_RAMP_SHOCK_MULT = 0.25
_RAMP_FLAPPING_SECONDS = 900     # 15 min at 0.50x after FLAPPING -> NORMAL
_RAMP_FLAPPING_MULT = 0.50

# G-11: Stuck detection constants
_STUCK_MARKET_HOURS_SECONDS = 86400  # 24h of market time

# C-07: Regime confirmation buffer — 3-tick before transition
# Prevents noisy oscillations from causing whipsaw.
# Emergency regimes (SHOCK, RISK_OFF) bypass and transition immediately.
_C07_CONFIRMATION_TICKS = 3

# C-07: VIX hysteresis — proportional deadband (15% of trigger level)
# Once a VIX-driven regime is entered, VIX must fall below (trigger - 15%)
# to exit. E.g., HIGH_VOLATILITY at VIX=25 requires VIX < 21.25 to clear.
# This prevents rapid oscillation at VIX 24.9/25.1 boundaries.
_VIX_HYSTERESIS_PCT = 0.15          # 15% deadband
_VIX_HIGH_VOL_TRIGGER = 25.0        # VIX threshold for HIGH_VOLATILITY
_VIX_RISK_OFF_TRIGGER = 35.0        # VIX threshold for RISK_OFF
_VIX_SHOCK_TRIGGER = 45.0           # VIX threshold for SHOCK
_VIX_HIGH_VOL_CLEAR = _VIX_HIGH_VOL_TRIGGER * (1.0 - _VIX_HYSTERESIS_PCT)  # 21.25
_VIX_RISK_OFF_CLEAR = _VIX_RISK_OFF_TRIGGER * (1.0 - _VIX_HYSTERESIS_PCT)  # 29.75


class RegimeClassifier:
    """Classifies the market into one of 8 regime states.

    Uses QQQ and SPY price action relative to VWAP, EMA alignment,
    price slope, and VIX level to determine the current regime.

    The regime determines which bot activates and which strategies fire.
    Transitions trigger immediate protective actions (Section 7).
    """

    def __init__(self) -> None:
        self._current_regime: RegimeState = RegimeState.RANGE_BOUND
        self._previous_regime: RegimeState = RegimeState.RANGE_BOUND
        self._regime_start: datetime = datetime.now(timezone.utc)
        self._regime_duration_bars: int = 0
        self._transition_buffer_sessions: int = 0  # 2-session buffer on change
        self._regime_history: list[dict] = []  # Bounded to prevent memory leak
        self._max_regime_history = 500  # Keep last 500 transitions

        # G-09: Flapping detection — timestamps of recent regime changes
        self._regime_change_times: deque[datetime] = deque(maxlen=20)
        self._is_flapping: bool = False
        self._flap_entered_at: datetime | None = None
        self._last_flap_regime: RegimeState | None = None  # regime before flapping

        # G-10: Post-recovery ramp-up tracking
        self._recovery_start: datetime | None = None
        self._recovery_from: RegimeState | None = None  # what we recovered from
        self._recovery_multiplier: float = 1.0
        self._recovery_duration_seconds: float = 0.0

        # G-11: Stuck detection — cumulative market-hours at same regime
        self._stuck_warned: bool = False  # only warn once per stuck episode

        # C-07: 3-tick confirmation buffer before regime transition
        # Prevents noisy oscillations in regime classification from causing
        # whipsaw in strategy decisions. Emergency regimes (SHOCK, RISK_OFF)
        # bypass the buffer and transition immediately.
        self._proposed_regime: RegimeState | None = None
        self._proposal_count: int = 0

        # C-07: VIX hysteresis — proportional deadband (15% of trigger level)
        # Prevents rapid oscillation at VIX threshold boundaries.
        # E.g., HIGH_VOLATILITY triggers at VIX=25, requires VIX < 21.25 to clear.
        self._vix_elevated: bool = False  # True when VIX has crossed above threshold
        self._vix_risk_off: bool = False  # True when VIX has crossed above RISK_OFF threshold

    @property
    def current_regime(self) -> RegimeState:
        return self._current_regime

    @property
    def previous_regime(self) -> RegimeState:
        return self._previous_regime

    @property
    def in_transition(self) -> bool:
        """True if within the 2-session transition buffer after a regime change."""
        return self._transition_buffer_sessions > 0

    def classify(
        self,
        qqq_price: float,
        qqq_vwap: float,
        spy_price: float,
        spy_vwap: float,
        ema9: float,
        ema20: float,
        ema50: float,
        slope_per_bar: float,
        vix: float,
        spy_change_pct: float = 0.0,
        or_range: float = 0.0,
        normal_range: float = 0.0,
    ) -> RegimeState:
        """Classify the current market regime based on multiple inputs.

        Args:
            qqq_price: Current QQQ price
            qqq_vwap: QQQ VWAP level
            spy_price: Current SPY price
            spy_vwap: SPY VWAP level
            ema9: QQQ EMA(9)
            ema20: QQQ EMA(20)
            ema50: QQQ EMA(50) — used for strong trend confirmation
            slope_per_bar: Price slope as % change per bar (5-min)
            vix: Current VIX level
            spy_change_pct: SPY intraday % change (for RISK_OFF detection)
            or_range: Current day's opening range width
            normal_range: 20-day average range (for HIGH_VOL detection)

        Returns:
            RegimeState enum value
        """
        now = datetime.now(timezone.utc)

        # G-09: If currently flapping, check if cooldown has elapsed
        if self._is_flapping:
            stable_seconds = (now - self._flap_entered_at).total_seconds() if self._flap_entered_at else 0
            # Check underlying regime — but don't actually transition yet
            underlying = self._determine_state(
                qqq_price, qqq_vwap, spy_price, spy_vwap,
                ema9, ema20, ema50, slope_per_bar, vix,
                spy_change_pct, or_range, normal_range
            )
            # Track if underlying keeps changing during flap (reset cooldown timer)
            if hasattr(self, '_flap_underlying') and underlying != self._flap_underlying:
                self._flap_entered_at = now  # reset cooldown
                self._regime_change_times.append(now)
            self._flap_underlying = underlying

            if stable_seconds >= _FLAP_COOLDOWN_SECONDS:
                # Auto-clear: 30 min of stable regime
                logger.warning(
                    "REGIME_FLAPPING CLEARED after %.0fs stable. Resuming: %s",
                    stable_seconds, underlying.value,
                )
                self._is_flapping = False
                # G-10: Enter recovery ramp-up from FLAPPING
                self._recovery_start = now
                self._recovery_from = RegimeState.REGIME_FLAPPING
                self._recovery_multiplier = _RAMP_FLAPPING_MULT
                self._recovery_duration_seconds = _RAMP_FLAPPING_SECONDS
                self._handle_transition(underlying)
            else:
                self._regime_duration_bars += 1
                return RegimeState.REGIME_FLAPPING

        new_regime = self._determine_state(
            qqq_price, qqq_vwap, spy_price, spy_vwap,
            ema9, ema20, ema50, slope_per_bar, vix,
            spy_change_pct, or_range, normal_range
        )

        if new_regime != self._current_regime:
            # C-07: 3-tick confirmation buffer before non-emergency transitions
            # Emergency regimes (SHOCK, RISK_OFF) bypass the buffer entirely
            # and transition immediately — safety always takes priority.
            _EMERGENCY_REGIMES = {RegimeState.SHOCK, RegimeState.RISK_OFF}

            if new_regime in _EMERGENCY_REGIMES:
                # Emergency — transition immediately, reset buffer
                self._proposed_regime = None
                self._proposal_count = 0
                self._handle_transition(new_regime)
            else:
                # Non-emergency — require 3 consecutive same-regime proposals
                if new_regime == self._proposed_regime:
                    self._proposal_count += 1
                else:
                    self._proposed_regime = new_regime
                    self._proposal_count = 1

                if self._proposal_count >= _C07_CONFIRMATION_TICKS:
                    # Confirmed — accept the transition
                    logger.info(
                        "C-07 REGIME CONFIRMED: %s → %s after %d ticks",
                        self._current_regime.value, new_regime.value,
                        _C07_CONFIRMATION_TICKS,
                    )
                    self._proposed_regime = None
                    self._proposal_count = 0
                    self._handle_transition(new_regime)
                else:
                    # Not yet confirmed — log and keep current regime
                    logger.debug(
                        "C-07 REGIME BUFFERING: proposed=%s tick=%d/%d | "
                        "current=%s",
                        new_regime.value, self._proposal_count,
                        _C07_CONFIRMATION_TICKS, self._current_regime.value,
                    )

            # G-09: Check for flapping after transition
            if self._check_flapping(now):
                self._enter_flapping(now)
                return RegimeState.REGIME_FLAPPING
        else:
            # Same regime as current — reset proposal buffer
            self._proposed_regime = None
            self._proposal_count = 0

        # G-11: Stuck detection
        self._check_stuck(now)

        self._regime_duration_bars += 1
        return self._current_regime

    def _determine_state(
        self,
        qqq_price: float,
        qqq_vwap: float,
        spy_price: float,
        spy_vwap: float,
        ema9: float,
        ema20: float,
        ema50: float,
        slope: float,
        vix: float,
        spy_change: float,
        or_range: float,
        normal_range: float,
    ) -> RegimeState:
        """Core classification logic mapping inputs to one of 8 states.

        C-07: VIX thresholds now use hysteresis with a proportional deadband
        (15% of the trigger level). Once a VIX-driven regime is entered, VIX
        must fall below (trigger * 0.85) to exit. This prevents rapid
        oscillation at threshold boundaries (e.g. VIX 24.9/25.1).
        """

        # SHOCK: VIX > 45 or circuit breaker (extreme)
        # No hysteresis — SHOCK always transitions immediately for safety
        if vix > _VIX_SHOCK_TRIGGER:
            logger.critical("SHOCK detected: VIX=%.1f. EMERGENCY FLATTEN.", vix)
            self._vix_elevated = True
            self._vix_risk_off = True
            return RegimeState.SHOCK

        # RISK_OFF: VIX > 35 or SPY falling > -2% intraday
        # C-07: VIX hysteresis — once in RISK_OFF, require VIX < 29.75 to clear
        vix_risk_off = False
        if vix > _VIX_RISK_OFF_TRIGGER:
            vix_risk_off = True
            self._vix_risk_off = True
        elif self._vix_risk_off and vix > _VIX_RISK_OFF_CLEAR:
            # Still in deadband — maintain RISK_OFF
            vix_risk_off = True
        else:
            self._vix_risk_off = False

        if vix_risk_off or spy_change < -2.0:
            logger.warning("RISK_OFF: VIX=%.1f, SPY change=%.2f%%", vix, spy_change)
            self._vix_elevated = True
            return RegimeState.RISK_OFF

        # HIGH_VOLATILITY: VIX > 25 or range > 2x normal
        # C-07: VIX hysteresis — once in HIGH_VOL, require VIX < 21.25 to clear
        vix_high_vol = False
        if vix > _VIX_HIGH_VOL_TRIGGER:
            vix_high_vol = True
            self._vix_elevated = True
        elif self._vix_elevated and vix > _VIX_HIGH_VOL_CLEAR:
            # Still in deadband — maintain HIGH_VOLATILITY
            vix_high_vol = True
        else:
            self._vix_elevated = False

        if vix_high_vol or (normal_range > 0 and or_range > 2 * normal_range):
            logger.info("HIGH_VOLATILITY: VIX=%.1f, range ratio=%.1f",
                        vix, or_range / max(normal_range, 0.001))
            return RegimeState.HIGH_VOLATILITY

        # Check QQQ and SPY vs VWAP
        qqq_above_vwap = qqq_price > qqq_vwap
        spy_above_vwap = spy_price > spy_vwap
        both_above = qqq_above_vwap and spy_above_vwap
        both_below = (not qqq_above_vwap) and (not spy_above_vwap)
        one_above = qqq_above_vwap or spy_above_vwap

        # EMA alignment
        bullish_ema = ema9 > ema20
        bearish_ema = ema9 < ema20

        # TRENDING_UP strong: Both > VWAP, EMA(9) > EMA(20), slope > +0.015%/bar (was 0.02%)
        if both_above and bullish_ema and slope > 0.00015:
            return RegimeState.TRENDING_UP_STRONG

        # TRENDING_UP moderate: 1 of 2 > VWAP, slope +0.003 to +0.015% (was 0.005-0.02%)
        if one_above and slope > 0.00003 and slope <= 0.00015:
            return RegimeState.TRENDING_UP_MOD

        # TRENDING_DOWN strong: Both < VWAP, EMA(9) < EMA(20), slope < -0.015%/bar (was -0.02%)
        if both_below and bearish_ema and slope < -0.00015:
            return RegimeState.TRENDING_DOWN_STRONG

        # TRENDING_DOWN moderate: at least 1 of 2 < VWAP, slope -0.003 to -0.015% (was -0.005 to -0.02%)
        if not one_above and slope < -0.00003 and slope >= -0.00015:
            return RegimeState.TRENDING_DOWN_MOD

        # RANGE_BOUND: +/-0.5% around VWAP (widened from 0.3%), EMAs flat, VIX 15-24
        qqq_vwap_dist = abs(qqq_price - qqq_vwap) / qqq_vwap if qqq_vwap > 0 else 0
        if qqq_vwap_dist < 0.005 and 15 <= vix <= 24:
            return RegimeState.RANGE_BOUND

        # Default: RANGE_BOUND if nothing else matched clearly
        return RegimeState.RANGE_BOUND

    def _handle_transition(self, new_regime: RegimeState) -> None:
        """Process a regime transition: log it, set buffer, trigger actions."""
        now = datetime.now(timezone.utc)
        old = self._current_regime
        self._previous_regime = old
        self._current_regime = new_regime
        self._regime_start = now
        self._regime_duration_bars = 0
        self._transition_buffer_sessions = 1  # Reduced from 2 to 1 for faster regime recognition

        # G-09: Record transition timestamp for flapping detection
        self._regime_change_times.append(now)

        # G-10: Detect recovery transitions and set ramp-up
        self._check_recovery_transition(old, new_regime, now)

        # G-11: Reset stuck warning on any transition
        self._stuck_warned = False

        transition = self._get_transition_action(old, new_regime)
        logger.warning(
            "REGIME TRANSITION: %s → %s | Action: %s",
            old.value, new_regime.value, transition
        )

        self._regime_history.append({
            "timestamp": now.isoformat(),
            "from": old.value,
            "to": new_regime.value,
            "action": transition,
        })
        # Prevent memory leak — trim history to bounded size
        if len(self._regime_history) > self._max_regime_history:
            self._regime_history = self._regime_history[-self._max_regime_history:]

    def _get_transition_action(self, old: RegimeState, new: RegimeState) -> str:
        """Section 7: Immediate actions on regime transitions."""

        # ANY → SHOCK
        if new == RegimeState.SHOCK:
            return "EMERGENCY FLATTEN. Kill switch."

        # ANY → RISK_OFF
        if new == RegimeState.RISK_OFF:
            return "FLATTEN everything. Cash. Wait."

        # TRENDING_UP → RANGE_BOUND
        if old in (RegimeState.TRENDING_UP_STRONG, RegimeState.TRENDING_UP_MOD) \
                and new == RegimeState.RANGE_BOUND:
            return "Tighten all long stops to breakeven. No new longs."

        # TRENDING_UP → TRENDING_DOWN
        if old in (RegimeState.TRENDING_UP_STRONG, RegimeState.TRENDING_UP_MOD) \
                and new in (RegimeState.TRENDING_DOWN_STRONG, RegimeState.TRENDING_DOWN_MOD):
            return "FLATTEN all longs. Switch to short-hunting."

        # TRENDING_DOWN → TRENDING_UP
        if old in (RegimeState.TRENDING_DOWN_STRONG, RegimeState.TRENDING_DOWN_MOD) \
                and new in (RegimeState.TRENDING_UP_STRONG, RegimeState.TRENDING_UP_MOD):
            return "FLATTEN all shorts. Switch to long-hunting."

        # RANGE_BOUND → TRENDING
        if old == RegimeState.RANGE_BOUND \
                and new in (RegimeState.TRENDING_UP_STRONG, RegimeState.TRENDING_UP_MOD,
                            RegimeState.TRENDING_DOWN_STRONG, RegimeState.TRENDING_DOWN_MOD):
            return "Opportunity. Enter trend direction. +10 confidence."

        # G-09: ANY → REGIME_FLAPPING
        if new == RegimeState.REGIME_FLAPPING:
            return "FLAPPING. Reduce positions 50%. No new entries."

        return "Monitor. Standard transition."

    def get_regime_size_multiplier(self) -> float:
        """Section 7: Position size multiplier based on current regime.

        A-02: RISK_OFF returns 0.0 here (LONG default). Direction-aware
        override for INVERSE/SHORT is handled in DynamicSizer.
        G-09: REGIME_FLAPPING returns 0.50 (reduce existing, block new).
        G-10: Post-recovery ramp-up applied on top of base multiplier.
        """
        multipliers = {
            RegimeState.TRENDING_UP_STRONG: 1.0,
            RegimeState.TRENDING_UP_MOD: 0.8,
            RegimeState.TRENDING_DOWN_STRONG: 1.0,
            RegimeState.TRENDING_DOWN_MOD: 0.8,
            RegimeState.RANGE_BOUND: 0.6,
            RegimeState.HIGH_VOLATILITY: 0.5,
            RegimeState.RISK_OFF: 0.0,   # A-02: 0.0 for LONG; INVERSE override in DynamicSizer
            RegimeState.SHOCK: 0.0,
            RegimeState.REGIME_FLAPPING: 0.5,  # G-09: 50% reduction
        }
        base = multipliers.get(self._current_regime, 0.6)

        # G-10: Apply post-recovery ramp-up multiplier if active
        ramp = self.get_recovery_multiplier()
        if ramp < 1.0:
            base *= ramp

        return base

    def can_trade_long(self) -> bool:
        """Whether long trades are permitted in the current regime.
        G-09: REGIME_FLAPPING blocks all new entries.
        """
        blocked = {RegimeState.RISK_OFF, RegimeState.SHOCK, RegimeState.REGIME_FLAPPING}
        return self._current_regime not in blocked

    def can_trade_short(self) -> bool:
        """Whether short trades are permitted in the current regime.
        G-09: REGIME_FLAPPING blocks all new entries.
        """
        blocked = {RegimeState.SHOCK, RegimeState.REGIME_FLAPPING}
        return self._current_regime not in blocked

    def can_use_3x_etps(self) -> bool:
        """Whether 3x leveraged ETPs are allowed in current regime."""
        allowed = {
            RegimeState.TRENDING_UP_STRONG,
            RegimeState.TRENDING_UP_MOD,
            RegimeState.TRENDING_DOWN_STRONG,
            RegimeState.TRENDING_DOWN_MOD,
        }
        return self._current_regime in allowed

    def get_3x_min_confidence(self) -> int:
        """Minimum confidence required for 3x ETP entries."""
        thresholds = {
            RegimeState.TRENDING_UP_STRONG: 80,
            RegimeState.TRENDING_UP_MOD: 75,
            RegimeState.TRENDING_DOWN_STRONG: 80,
            RegimeState.TRENDING_DOWN_MOD: 75,
        }
        return thresholds.get(self._current_regime, 999)  # 999 = effectively blocked

    def get_transition_confidence_bonus(self) -> int:
        """Section 36 Layer 2: Bonus points for favourable transitions."""
        if self._regime_duration_bars < 5:  # Recent transition
            # RANGE → TRENDING = +10
            if self._previous_regime == RegimeState.RANGE_BOUND and \
                    self._current_regime in (RegimeState.TRENDING_UP_STRONG,
                                             RegimeState.TRENDING_UP_MOD,
                                             RegimeState.TRENDING_DOWN_STRONG,
                                             RegimeState.TRENDING_DOWN_MOD):
                return 10
        return 0

    def decrement_transition_buffer(self) -> None:
        """Called at end of each session to count down the 2-session buffer."""
        if self._transition_buffer_sessions > 0:
            self._transition_buffer_sessions -= 1
            logger.info("Transition buffer: %d sessions remaining",
                        self._transition_buffer_sessions)

    # ------------------------------------------------------------------
    # G-09: Regime Flapping Protection
    # ------------------------------------------------------------------

    def _check_flapping(self, now: datetime) -> bool:
        """Check if 3+ regime changes occurred in the last 10 minutes."""
        cutoff = now - timedelta(seconds=_FLAP_WINDOW_SECONDS)
        recent_changes = [t for t in self._regime_change_times if t >= cutoff]
        return len(recent_changes) >= _FLAP_CHANGE_THRESHOLD

    def _enter_flapping(self, now: datetime) -> None:
        """Enter REGIME_FLAPPING state. Reduce positions 50%, block new entries."""
        self._is_flapping = True
        self._flap_entered_at = now
        self._last_flap_regime = self._current_regime
        self._flap_underlying = self._current_regime
        self._previous_regime = self._current_regime
        self._current_regime = RegimeState.REGIME_FLAPPING
        self._regime_start = now
        self._regime_duration_bars = 0
        logger.critical(
            "G-09 REGIME_FLAPPING: 3+ regime changes in 10 min. "
            "Reducing positions 50%%. No new entries. "
            "Auto-clear after %ds stable.",
            _FLAP_COOLDOWN_SECONDS,
        )

    @property
    def is_flapping(self) -> bool:
        """True if regime is currently in flapping state."""
        return self._is_flapping

    # ------------------------------------------------------------------
    # G-10: Post-Recovery Ramp-Up Sizing
    # ------------------------------------------------------------------

    def _check_recovery_transition(
        self, old: RegimeState, new: RegimeState, now: datetime,
    ) -> None:
        """Set ramp-up multiplier when recovering from adverse regimes."""
        normal_regimes = {
            RegimeState.TRENDING_UP_STRONG, RegimeState.TRENDING_UP_MOD,
            RegimeState.TRENDING_DOWN_STRONG, RegimeState.TRENDING_DOWN_MOD,
            RegimeState.RANGE_BOUND,
        }
        if new not in normal_regimes:
            self._recovery_start = None
            self._recovery_from = None
            self._recovery_multiplier = 1.0
            return

        if old == RegimeState.RISK_OFF:
            self._recovery_start = now
            self._recovery_from = old
            self._recovery_multiplier = _RAMP_RISK_OFF_MULT
            self._recovery_duration_seconds = _RAMP_RISK_OFF_SECONDS
            logger.warning(
                "G-10 RAMP-UP: RISK_OFF -> %s | %.2fx for %ds",
                new.value, _RAMP_RISK_OFF_MULT, _RAMP_RISK_OFF_SECONDS,
            )
        elif old == RegimeState.SHOCK:
            self._recovery_start = now
            self._recovery_from = old
            self._recovery_multiplier = _RAMP_SHOCK_MULT
            self._recovery_duration_seconds = _RAMP_SHOCK_SECONDS
            logger.warning(
                "G-10 RAMP-UP: SHOCK -> %s | %.2fx for %ds",
                new.value, _RAMP_SHOCK_MULT, _RAMP_SHOCK_SECONDS,
            )

    def get_recovery_multiplier(self) -> float:
        """G-10: Return the current post-recovery ramp-up multiplier.

        Returns 1.0 if no recovery ramp-up is active. Returns the
        configured multiplier if still within the ramp-up window.
        Auto-expires when the ramp-up duration elapses.
        """
        if self._recovery_start is None:
            return 1.0

        now = datetime.now(timezone.utc)
        elapsed = (now - self._recovery_start).total_seconds()

        if elapsed >= self._recovery_duration_seconds:
            from_regime = self._recovery_from.value if self._recovery_from else "UNKNOWN"
            logger.info(
                "G-10 RAMP-UP COMPLETE: %s recovery finished after %.0fs",
                from_regime, elapsed,
            )
            self._recovery_start = None
            self._recovery_from = None
            self._recovery_multiplier = 1.0
            return 1.0

        return self._recovery_multiplier

    @property
    def recovery_active(self) -> bool:
        """True if a post-recovery ramp-up is currently in effect."""
        return self._recovery_start is not None and self.get_recovery_multiplier() < 1.0

    # ------------------------------------------------------------------
    # G-11: Regime Stuck Detection
    # ------------------------------------------------------------------

    def _check_stuck(self, now: datetime) -> None:
        """Warn if the same regime has been active for >24h of market time."""
        if self._stuck_warned:
            return

        elapsed = (now - self._regime_start).total_seconds()
        if elapsed > _STUCK_MARKET_HOURS_SECONDS:
            logger.warning(
                "G-11 REGIME_STUCK: %s has been active for %.1f hours (>24h). "
                "Manual review recommended -- possible data feed or classifier issue.",
                self._current_regime.value,
                elapsed / 3600,
            )
            self._stuck_warned = True

    # ------------------------------------------------------------------
    # Info / Status
    # ------------------------------------------------------------------

    def get_regime_info(self) -> dict:
        """Get full regime context for logging and display."""
        info = {
            "regime": self._current_regime.value,
            "previous": self._previous_regime.value,
            "duration_bars": self._regime_duration_bars,
            "in_transition": self.in_transition,
            "size_multiplier": self.get_regime_size_multiplier(),
            "can_long": self.can_trade_long(),
            "can_short": self.can_trade_short(),
            "can_3x": self.can_use_3x_etps(),
            "transition_bonus": self.get_transition_confidence_bonus(),
            "is_flapping": self._is_flapping,
            "recovery_active": self.recovery_active,
            "recovery_multiplier": self.get_recovery_multiplier(),
        }
        if self._recovery_from:
            info["recovery_from"] = self._recovery_from.value
        return info

    def compute_slope(self, prices: pd.Series, window: int = 20) -> float:
        """Calculate price slope as % change per bar over a rolling window.

        Used to determine trend strength for regime classification.
        Slope > +0.02%/bar = strong uptrend. < -0.02%/bar = strong downtrend.
        """
        if len(prices) < window:
            return 0.0

        recent = prices.iloc[-window:]
        if recent.iloc[0] == 0:
            return 0.0

        # Linear regression slope normalised by price
        x = np.arange(len(recent), dtype=float)
        y = recent.values.astype(float)
        try:
            slope = np.polyfit(x, y, 1)[0]
            return slope / recent.iloc[0]  # Normalise to percentage
        except (np.linalg.LinAlgError, ValueError):
            return 0.0


class TimeOfDayEngine:
    """Section 10: Time-of-Day Statistical Edges.

    7 windows from Chaos Open to Close Mechanics.
    Each window has specific strategy adjustments.
    The system uses US Eastern Time as the anchor.
    """

    # Window definitions (ET): (start_hour, start_min, end_hour, end_min)
    WINDOWS = {
        TimeWindow.CHAOS_OPEN: (9, 30, 9, 35),
        TimeWindow.MORNING_MOMENTUM: (9, 35, 10, 30),
        TimeWindow.TREND_EXTENSION: (10, 30, 11, 30),
        TimeWindow.LUNCH_CHOP: (11, 30, 14, 0),
        TimeWindow.AFTERNOON_PUSH: (14, 0, 15, 0),
        TimeWindow.POWER_HOUR: (15, 0, 15, 30),
        TimeWindow.CLOSE_MECHANICS: (15, 30, 16, 0),
    }

    def get_current_window(self, et_hour: int, et_minute: int) -> TimeWindow:
        """Determine which time window we're currently in.

        Args:
            et_hour: Current hour in Eastern Time (0-23)
            et_minute: Current minute (0-59)

        Returns:
            TimeWindow enum value
        """
        current_minutes = et_hour * 60 + et_minute

        for window, (sh, sm, eh, em) in self.WINDOWS.items():
            start = sh * 60 + sm
            end = eh * 60 + em
            if start <= current_minutes < end:
                return window

        # Outside market hours
        if current_minutes < 9 * 60 + 30:
            return TimeWindow.CHAOS_OPEN  # Pre-market, treat as cautious
        return TimeWindow.CLOSE_MECHANICS  # After hours

    def is_no_trade_window(self, window: TimeWindow) -> bool:
        """Section 10: Some windows are NO TRADE or restricted."""
        return window == TimeWindow.CHAOS_OPEN

    def is_primary_window(self, window: TimeWindow) -> bool:
        """Morning momentum is the PRIMARY trading window."""
        return window == TimeWindow.MORNING_MOMENTUM

    def get_window_adjustments(self, window: TimeWindow) -> dict:
        """Get strategy adjustments for the current time window.

        Returns dict with: rvol_min_override, confidence_modifier,
        allowed_strategies, notes.
        """
        adjustments = {
            TimeWindow.CHAOS_OPEN: {
                "rvol_min_override": None,
                "confidence_modifier": 0,
                "no_trade": True,
                "notes": "NO TRADES. Observe only. First 5 minutes.",
            },
            TimeWindow.MORNING_MOMENTUM: {
                "rvol_min_override": None,  # Use standard RVOL
                "confidence_modifier": 0,
                "no_trade": False,
                "notes": "PRIMARY WINDOW. Full aggression. ORB.",
            },
            TimeWindow.TREND_EXTENSION: {
                "rvol_min_override": None,
                "confidence_modifier": 0,
                "no_trade": False,
                "notes": "VWAP pullback entries. Good for adds.",
            },
            TimeWindow.LUNCH_CHOP: {
                "rvol_min_override": 1.7,
                "confidence_modifier": -8,  # Midday penalty
                "no_trade": False,
                "notes": "RVOL min 1.7. Most NO_TRADE signals.",
            },
            TimeWindow.AFTERNOON_PUSH: {
                "rvol_min_override": None,
                "confidence_modifier": 0,
                "no_trade": False,
                "notes": "Fresh setups. Confirm with regime.",
            },
            TimeWindow.POWER_HOUR: {
                "rvol_min_override": None,
                "confidence_modifier": 0,
                "no_trade": False,
                "notes": "Only high confidence. Quick exits.",
            },
            TimeWindow.CLOSE_MECHANICS: {
                "rvol_min_override": None,
                "confidence_modifier": 0,
                "no_trade": True,
                "notes": "FLATTEN. No new entries.",
            },
        }
        return adjustments.get(window, adjustments[TimeWindow.LUNCH_CHOP])

    def is_friday_afternoon(self, et_hour: int, day_of_week: int) -> bool:
        """Section 44: Friday anxiety detection.
        Friday (day_of_week=4) after 15:30 ET = flatten 3x, reduce 50%.
        """
        return day_of_week == 4 and et_hour >= 15

    def get_session_quality(self, hour: int, minute: int) -> tuple[str, int]:
        """Smart Session Windows — time-of-day entry quality scoring.

        Returns (session_label, confidence_adjustment) based on the
        current ET time.  Used by the qualification pipeline to BLOCK
        entries during dead zones and BOOST entries during optimal windows.

        Windows (all Eastern Time):
            09:30-10:00  ORB_PRIME          +10  (opening range — highest edge)
            10:00-11:30  MORNING_MOMENTUM   +5   (continuation moves)
            12:00-14:00  DEAD_ZONE          -10  (lunch lull — lowest volume)
            15:00-16:00  POWER_HOUR          +5  (closing power hour)
            everything else  NEUTRAL           0
        """
        current = hour * 60 + minute

        # ORB_PRIME: 09:30 – 10:00 ET
        if 9 * 60 + 30 <= current < 10 * 60:
            return ("ORB_PRIME", 10)

        # MORNING_MOMENTUM: 10:00 – 11:30 ET
        if 10 * 60 <= current < 11 * 60 + 30:
            return ("MORNING_MOMENTUM", 5)

        # DEAD_ZONE: 12:00 – 14:00 ET
        if 12 * 60 <= current < 14 * 60:
            return ("DEAD_ZONE", -10)

        # POWER_HOUR: 15:00 – 16:00 ET
        if 15 * 60 <= current < 16 * 60:
            return ("POWER_HOUR", 5)

        return ("NEUTRAL", 0)

    def is_last_10_minutes(self, et_hour: int, et_minute: int) -> bool:
        """No-Trade Doctrine condition: last 10 minutes of session."""
        current = et_hour * 60 + et_minute
        close = 15 * 60 + 50  # 15:50 ET (10 min before 16:00)
        return current >= close
