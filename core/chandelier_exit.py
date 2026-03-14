"""
NZT-48 Chandelier Exit — Mandate 5 (Le Beau 1999)
====================================================
SOLE AUTHORITY for profit ladder and trailing stops (AEGIS C-02).
All profit taking, stop ratcheting, and partial banking flows through here.
VirtualTrader inline ladders (_run_profit_ladder, _run_etp_ladder) are disabled.

Trailing stop at Highest_High - N×ATR. Activates at ≥2% profit.

Leverage-adjusted ATR multiplier:
  λ=5: N=1.0 (tighter — vol drag demands faster exit)
  λ=3: N=1.5 (standard)
  λ=2: N=2.0 (more room for 2x products)
  λ=1: N=2.5 (unleveraged)

Explicit Profit Ladder with Partial Banking (C-04):
  +2%  → move stop to breakeven
  +4%  → lock profit at +2%, bank 15% of position
  +6%  → bank 33% of position, trail at N×ATR
  +8%  → bank 50% of position, trail at 0.75×N×ATR (tighter)
  +10% → trail at 0.5×N×ATR (tightest)
  >10% → trail tightens 0.1×ATR every additional 2%

Key constraint: 2% MINIMUM qualifying threshold stays.
Chandelier activates AFTER +2% — it replaces the ceiling, not the floor.

Redis persistence (Mandate 9): state survives container restarts.
Fallback: in-memory if Redis unavailable (paper mode acceptable).

References:
  - Le Beau (1999) "Chandelier Exits"
  - Bianchi, Drew & Fan (2016) "Tail Risk in Momentum Strategy Returns"
  - MacLean, Thorp & Ziemba (2011) leverage adjustment
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

# WEEK 1: Perfect entry timing integration
import sys
sys.path.insert(0, '/Users/rr/nzt48-signals')
from src.core.adaptive_ladder import AdaptiveLadder
from src.core.stop_ratchet_memory import StopRatchetMemory

logger = logging.getLogger("nzt48.chandelier")

# Leverage → ATR multiplier map
_ATR_MULT_BY_LEVERAGE = {
    5: 1.0,
    3: 1.5,
    2: 2.0,
    1: 2.5,
}

# Leverage map for ISA universe (same as kelly_sizer.py)
_LEVERAGE_MAP = {
    "QQQ3.L": 3, "3LUS.L": 3, "3SEM.L": 3, "GPT3.L": 3,
    "NVD3.L": 3, "TSL3.L": 3, "TSM3.L": 3,
    "MU2.L": 2,
    "QQQS.L": 3, "3USS.L": 3,
    "QQQ5.L": 5, "SP5L.L": 5,
}

# Profit ladder rungs — C-04: partial banking at rungs 1-3
# bank_pct = fraction of ORIGINAL position to close at this rung
LADDER_RUNGS = [
    {"pct": 2.0, "action": "move_stop_to_breakeven", "bank_pct": 0.0},
    {"pct": 4.0, "action": "lock_profit_2pct_bank_15", "bank_pct": 0.15},
    {"pct": 6.0, "action": "bank_33pct_and_trail", "bank_pct": 0.33},
    {"pct": 8.0, "action": "bank_50pct_trail_tighter", "bank_pct": 0.50},
    {"pct": 10.0, "action": "trail_stop_tightest", "bank_pct": 0.0},
]
# F-16/AR-02: Rungs calibrated to ex-ante rolling ATR multiples ONLY.
# MFE data logged for observation but NEVER fed back into rung parameters.


@dataclass
class ChandelierState:
    """Persistent state for one active Chandelier trailing stop."""
    ticker: str = ""
    trade_id: str = ""
    entry_price: float = 0.0
    direction: str = "LONG"
    leverage: int = 3
    atr_at_entry: float = 0.0
    highest_high: float = 0.0
    lowest_low: float = float("inf")
    trailing_stop: float = 0.0
    active: bool = False
    current_rung: int = 0  # index into LADDER_RUNGS
    scale_out_done: bool = False
    last_update_ts: float = 0.0
    # C-04: Track cumulative banked percentage of original position
    cumulative_banked_pct: float = 0.0  # total fraction banked so far
    # Track which rungs have had their banking executed
    banked_rungs: list = field(default_factory=list)


class ChandelierExit:
    """Mandate 5: Chandelier trailing exit with explicit profit ladder.

    Activates at ≥2% unrealised profit. Replaces the hard ceiling on gains.
    The 2% minimum qualifying threshold stays as the floor.

    Redis persistence (Mandate 9): state hydrates from Redis on init,
    persists on every update, deletes on close.
    """

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._states: dict[str, ChandelierState] = {}  # trade_id -> state

        # WEEK 1: Perfect entry timing — adaptive rungs + stop ratchet
        self.adaptive_ladder = AdaptiveLadder()
        self.stop_ratchet = StopRatchetMemory()

        self._hydrate_from_redis()

    def _hydrate_from_redis(self) -> None:
        """Load all chandelier states from Redis on startup."""
        if not self._redis:
            return
        try:
            keys = self._redis.keys("chandelier:*")
            for key in keys:
                raw = self._redis.get(key)
                if raw:
                    data = json.loads(raw)
                    state = ChandelierState(**data)
                    self._states[state.trade_id] = state
            if self._states:
                logger.info("ChandelierExit: hydrated %d states from Redis", len(self._states))
        except Exception as e:
            logger.warning("ChandelierExit: Redis hydration failed (in-memory mode): %s", e)

    def _persist_to_redis(self, state: ChandelierState) -> None:
        """Persist state to Redis with 7-day TTL (survives weekends)."""
        if not self._redis:
            return
        try:
            key = f"chandelier:{state.ticker}:{state.trade_id}"
            self._redis.set(key, json.dumps(asdict(state)), ex=604800)
        except Exception as e:
            logger.debug("ChandelierExit: Redis persist failed: %s", e)

    def _delete_from_redis(self, state: ChandelierState) -> None:
        """Remove state from Redis on trade close."""
        if not self._redis:
            return
        try:
            key = f"chandelier:{state.ticker}:{state.trade_id}"
            self._redis.delete(key)
        except Exception as e:
            logger.debug("ChandelierExit: Redis delete failed: %s", e)

    def register(
        self,
        trade_id: str,
        ticker: str,
        entry_price: float,
        direction: str,
        atr: float,
    ) -> ChandelierState:
        """Register a new trade for Chandelier tracking."""
        leverage = _LEVERAGE_MAP.get(ticker.upper(), 1)
        state = ChandelierState(
            ticker=ticker,
            trade_id=trade_id,
            entry_price=entry_price,
            direction=direction.upper(),
            leverage=leverage,
            atr_at_entry=atr,
            highest_high=entry_price,
            lowest_low=entry_price,
            last_update_ts=time.time(),
        )
        self._states[trade_id] = state
        self._persist_to_redis(state)
        logger.info(
            "CHANDELIER_REGISTER: %s %s entry=%.2f ATR=%.4f λ=%d",
            ticker, direction, entry_price, atr, leverage,
        )
        return state

    def update(
        self,
        trade_id: str,
        current_price: float,
        current_high: float = 0.0,
        current_low: float = 0.0,
        current_atr: float = None,
    ) -> dict:
        """Update Chandelier state with latest price data.

        Args:
            trade_id: The virtual trade ID.
            current_price: Latest price.
            current_high: Current bar's high (defaults to current_price).
            current_low: Current bar's low (defaults to current_price).

        Returns:
            dict with keys: exit (bool), trailing_stop (float),
                           rung (int), action (str), scale_out (bool)
        """
        state = self._states.get(trade_id)
        if not state:
            return {"exit": False, "trailing_stop": None, "rung": 0, "action": None, "scale_out": False}

        if current_high <= 0:
            current_high = current_price
        if current_low <= 0 or current_low == float("inf"):
            current_low = current_price

        entry = state.entry_price
        # Use live ATR if provided, otherwise fall back to frozen entry ATR
        atr = current_atr if current_atr is not None and current_atr > 0 else state.atr_at_entry

        # Guard: ATR must be positive for stop computation
        if atr <= 0:
            logger.warning("CHANDELIER: %s ATR=0 — skipping update (no stop movement)", trade_id)
            return {"exit": False, "trailing_stop": state.trailing_stop, "rung": state.current_rung, "action": None, "scale_out": False}

        # Calculate unrealised % move
        if state.direction == "LONG":
            pct_move = ((current_price - entry) / entry) * 100 if entry > 0 else 0
            state.highest_high = max(state.highest_high, current_high)
        else:
            pct_move = ((entry - current_price) / entry) * 100 if entry > 0 else 0
            state.lowest_low = min(state.lowest_low, current_low)

        # Check if Chandelier should activate (≥2% profit)
        if not state.active:
            if pct_move >= 2.0:
                state.active = True
                logger.info(
                    "CHANDELIER_ACTIVATE: %s +%.1f%% — trailing stop engaged",
                    state.ticker, pct_move,
                )
            else:
                state.last_update_ts = time.time()
                self._persist_to_redis(state)
                return {"exit": False, "trailing_stop": None, "rung": 0, "action": None, "scale_out": False}

        # Get leverage-adjusted ATR multiplier
        base_mult = _ATR_MULT_BY_LEVERAGE.get(state.leverage, 2.0)
        scale_out = False
        action = None
        # C-04: Track partial banking for this update
        bank_pct = 0.0  # fraction of ORIGINAL position to bank this tick

        # Walk the profit ladder
        old_rung = state.current_rung
        for i, rung in enumerate(LADDER_RUNGS):
            if i <= state.current_rung:
                continue
            if pct_move >= rung["pct"]:
                state.current_rung = i
                action = rung["action"]
                # C-04: Check if this rung has a banking action not yet executed
                rung_bank = rung.get("bank_pct", 0.0)
                if rung_bank > 0 and i not in state.banked_rungs:
                    bank_pct += rung_bank
                    state.banked_rungs.append(i)
                    state.cumulative_banked_pct += rung_bank
                    logger.info(
                        "CHANDELIER_BANK: %s rung %d — banking %.0f%% (cumulative %.0f%%)",
                        state.ticker, i, rung_bank * 100, state.cumulative_banked_pct * 100,
                    )

        # Legacy scale_out flag for backward compatibility
        if state.current_rung >= 2 and not state.scale_out_done:
            scale_out = True
            state.scale_out_done = True

        # Compute trailing stop based on current rung
        if state.current_rung == 0:
            # +2%: move stop near breakeven with 0.5 ATR breathing room
            if state.direction == "LONG":
                trailing_stop = entry - (0.5 * atr)
            else:
                trailing_stop = entry + (0.5 * atr)
        elif state.current_rung == 1:
            # +4%: lock profit at +2%
            if state.direction == "LONG":
                trailing_stop = entry * 1.02
            else:
                trailing_stop = entry * 0.98
        elif state.current_rung == 2:
            # +6%: trail at N×ATR from highest high
            if state.direction == "LONG":
                trailing_stop = state.highest_high - (base_mult * atr)
            else:
                trailing_stop = state.lowest_low + (base_mult * atr)
        elif state.current_rung == 3:
            # +8%: trail at 0.75×N×ATR (tighter)
            tighter_mult = base_mult * 0.75
            if state.direction == "LONG":
                trailing_stop = state.highest_high - (tighter_mult * atr)
            else:
                trailing_stop = state.lowest_low + (tighter_mult * atr)
        else:
            # +10%+: trail at 0.5×N×ATR (tightest)
            tightest_mult = base_mult * 0.5
            # Further tighten by 0.1×ATR every additional 2% beyond 10%
            extra_pct = max(0, pct_move - 10.0)
            extra_tighten = (extra_pct / 2.0) * 0.1 * atr
            if state.direction == "LONG":
                trailing_stop = state.highest_high - (tightest_mult * atr) + extra_tighten
                trailing_stop = min(trailing_stop, state.highest_high - (0.3 * atr))  # never above HH - 0.3 ATR
            else:
                trailing_stop = state.lowest_low + (tightest_mult * atr) - extra_tighten
                trailing_stop = max(trailing_stop, state.lowest_low + (0.3 * atr))

        # Ratchet: trailing stop only moves in favourable direction
        if state.direction == "LONG":
            trailing_stop = max(trailing_stop, state.trailing_stop)
        else:
            if state.trailing_stop > 0:
                trailing_stop = min(trailing_stop, state.trailing_stop)

        state.trailing_stop = trailing_stop

        # Check if stop is hit
        exit_triggered = False
        if state.direction == "LONG" and current_price <= trailing_stop:
            exit_triggered = True
        elif state.direction == "SHORT" and current_price >= trailing_stop:
            exit_triggered = True

        state.last_update_ts = time.time()
        self._persist_to_redis(state)

        if state.current_rung != old_rung:
            logger.info(
                "CHANDELIER_RUNG: %s rung %d→%d (+%.1f%%) stop=%.2f action=%s",
                state.ticker, old_rung, state.current_rung, pct_move,
                trailing_stop, action,
            )

        if exit_triggered:
            logger.info(
                "CHANDELIER_EXIT: %s price=%.2f hit trailing_stop=%.2f (rung=%d, +%.1f%%)",
                state.ticker, current_price, trailing_stop, state.current_rung, pct_move,
            )

        return {
            "exit": exit_triggered,
            "trailing_stop": round(trailing_stop, 4),
            "rung": state.current_rung,
            "action": action,
            "scale_out": scale_out,
            "pct_move": round(pct_move, 2),
            # C-04: partial banking — fraction of original position to close
            "bank_pct": round(bank_pct, 4),
            "cumulative_banked_pct": round(state.cumulative_banked_pct, 4),
        }

    def close(self, trade_id: str) -> None:
        """Remove a trade from Chandelier tracking."""
        state = self._states.pop(trade_id, None)
        if state:
            self._delete_from_redis(state)
            logger.info("CHANDELIER_CLOSE: %s %s removed", state.ticker, trade_id)

    # WEEK 1: Perfect entry timing methods
    def calculate_adaptive_rungs(
        self,
        trade_id: str,
        regime: str,
        hawkes_branching_ratio: float,
        atr: float,
        vtd_ratio: float,
    ) -> Optional[dict]:
        """
        Calculate dynamically adjusted rung targets using AdaptiveLadder.

        Args:
            trade_id: The virtual trade ID
            regime: Market regime (COMPRESSION, EXPANSION, etc.)
            hawkes_branching_ratio: Hawkes self-exciting ratio (0-1.0)
            atr: Current ATR in currency
            vtd_ratio: Volume-time decay (0-1.0)

        Returns:
            Dict with adaptive rungs info, or None if trade not found
        """
        state = self._states.get(trade_id)
        if not state:
            logger.warning(f"calculate_adaptive_rungs: trade_id {trade_id} not found")
            return None

        # Use adaptive_ladder to compute dynamically adjusted rungs
        adaptive_result = self.adaptive_ladder.calculate_adaptive_rungs(
            entry_price=state.entry_price,
            leverage=state.leverage,
            regime=regime,
            hawkes_branching_ratio=hawkes_branching_ratio,
            atr=atr,
            vtd_ratio=vtd_ratio,
        )

        logger.info(
            f"CHANDELIER_ADAPTIVE_RUNGS: {state.ticker} regime={regime} "
            f"hawkes={hawkes_branching_ratio:.2f} rungs={adaptive_result.rung_targets}"
        )

        return {
            "rung_targets": adaptive_result.rung_targets,
            "stop_multipliers": adaptive_result.stop_multipliers,
            "combined_multiplier": adaptive_result.regime_multiplier,
        }

    def should_advance_stop_adaptive(
        self,
        trade_id: str,
        current_stop: float,
        candidate_stop: float,
        current_price: float,
        price_momentum_atr_per_min: float,
        regime: str,
        vtd_ratio: float,
        recent_bars: Optional[list] = None,
    ) -> bool:
        """
        Check if stop should advance using StopRatchetMemory (prevents whipsaw).

        Args:
            trade_id: The virtual trade ID (for logging)
            current_stop: Current stop loss price
            candidate_stop: New proposed stop price
            current_price: Current price
            price_momentum_atr_per_min: Price momentum per minute (ATR units)
            regime: Market regime
            vtd_ratio: Volume-time decay (0-1.0)
            recent_bars: List of recent OHLCV bars

        Returns:
            bool: True if stop should advance, False if should hold
        """
        ratchet_decision = self.stop_ratchet.should_advance_stop(
            current_stop=current_stop,
            candidate_stop=candidate_stop,
            current_price=current_price,
            price_momentum_atr_per_min=price_momentum_atr_per_min,
            regime=regime,
            vtd_ratio=vtd_ratio,
            recent_bars=recent_bars,
        )

        if ratchet_decision.should_advance:
            self.stop_ratchet.record_advance(current_stop, candidate_stop, ratchet_decision.reason)
            logger.info(f"CHANDELIER_STOP_ADVANCE: {trade_id} {current_stop:.2f} → {candidate_stop:.2f}")
            return True
        else:
            logger.debug(f"CHANDELIER_STOP_HOLD: {trade_id} reason={ratchet_decision.reason}")
            return False

    def get_status(self) -> dict:
        """Return summary of all active Chandelier states."""
        return {
            "active_count": len(self._states),
            "trades": {
                tid: {
                    "ticker": s.ticker,
                    "direction": s.direction,
                    "active": s.active,
                    "rung": s.current_rung,
                    "trailing_stop": s.trailing_stop,
                    "highest_high": s.highest_high,
                }
                for tid, s in self._states.items()
            },
        }

    # WEEK 1: Perfect entry timing integration — Ghost Stop support
    def enable_ghost_stop_hybrid_mode(self, trade_id: str, use_brownian_jitter: bool = False) -> bool:
        """
        Enable hybrid Chandelier + Ghost stop mode for a trade.

        If use_brownian_jitter=True, use Brownian motion jitter on stop.
        This is BACKUP only — Chandelier is primary exit mechanism.

        Args:
            trade_id: Virtual trade ID
            use_brownian_jitter: Whether to add jitter (should be False for LSE ISA)

        Returns:
            True if enabled, False if trade not found
        """
        state = self._states.get(trade_id)
        if not state:
            return False

        # Log hybrid mode activation
        logger.info(
            f"CHANDELIER_HYBRID_MODE: {state.ticker} {trade_id} "
            f"(jitter={'ON' if use_brownian_jitter else 'OFF'})"
        )

        # In practice: Chandelier is always primary. Ghost stop is NOT implemented for LSE.
        # This stub is for architectural future-proofing only.
        return True
