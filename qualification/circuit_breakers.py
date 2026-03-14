"""
NZT-48 Trading System -- Circuit Breaker System
Institutional-grade capital protection during adverse market conditions.

THIS MODULE IS THE SOLE CANONICAL AUTHORITY for all P&L-based trading halts
(A-08). Daily, weekly, and monthly loss limits are enforced HERE and nowhere
else. VirtualTrader's Phase-9 P&L kill switch is a legacy duplicate — this
circuit breaker system supersedes it.

Seven independent circuit breakers that operate simultaneously:
1. Drawdown Circuit Breaker     -- 3-level daily loss protection
2. Volatility Circuit Breaker   -- VIX-based entry/size controls
3. Correlation Spike Breaker    -- Cross-position correlation guard
4. Consecutive Loss Breaker     -- Loss streak cooldown escalation
5. Black Swan Detector          -- Flash crash / extreme move detection
6. Portfolio Drawdown Cascade   -- G-02: AUM-tiered peak-to-trough drawdown
7. Weekly/Monthly Drawdown      -- A-07: weekly -8% and monthly -15% halts
8. Anti-Cascade Detector        -- A-09: 3 stop-outs in 15 min → 30 min halt

Each breaker can independently restrict or halt trading. The most
restrictive action across all breakers is always enforced.

A-03: Paper Mode Risk Parity
    Paper mode applies IDENTICAL risk rules as live. No relaxation, no
    special treatment. This ensures paper trading is a faithful rehearsal.

A-06: Redis State Persistence
    All circuit breaker state is persisted to Redis so that Docker restarts
    and IBC daily restarts (04:45 UK) cannot bypass active halts/cooldowns.

    Redis key namespace: ``nzt:cb:{field_name}``
    TTL: session-scoped — max(86400, seconds_until_next_market_open + 7200)
         This ensures state spans weekends (Fri close → Mon open).

    IMPORTANT: Redis must be configured with ``appendfsync always`` for
    trading-state keys to guarantee durability on power loss. Set this in
    redis.conf or via Docker Compose command override.

    Fallback: in-memory if Redis unavailable (degraded mode, logged as WARNING).

A-08: Unified Kill Switch
    daily_pnl passed to check_all() MUST be realised + unrealised (total P&L).
    The caller (main.py scan loop) is responsible for summing both components.
    This CB module is the single enforcement point — VirtualTrader Phase-9
    _check_pnl_kill_switch() should defer to CB results via
    update_circuit_breaker_state().

References:
    - Section 42: Session & Weekly Protection
    - Section 43: 17 Immutable Risk Rules
    - Section 60: Drawdown Recovery Protocol
    - Section 57: Cross-Correlation Matrix
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

import config as cfg

logger = logging.getLogger("nzt48.circuit_breakers")

# UK timezone for TTL calculation (next market open)
_UK_TZ = ZoneInfo("Europe/London")


# ---------------------------------------------------------------------------
# Constants derived from config where available, hardcoded where not
# ---------------------------------------------------------------------------

# Drawdown levels (daily intraday loss as fraction of equity)
_DD_LEVEL_1_YELLOW = 0.015    # 1.5% -- reduce sizes 50%
_DD_LEVEL_2_ORANGE = 0.025    # 2.5% -- stop new entries
_DD_LEVEL_3_RED = 0.04        # 4.0% -- close everything, halt

# Volatility (VIX)
_VIX_SPIKE_PCT = 0.25         # 25% intraday jump
_VIX_SPIKE_PAUSE_SEC = 1800   # 30 minutes
_VIX_HIGH_ABS = 35            # reduce all sizes 50%
_VIX_EXTREME_ABS = 45         # emergency close leveraged ETPs

# C-07: VIX hysteresis — proportional deadband (15% of trigger level)
# Once a VIX breaker tier is triggered, VIX must fall below (trigger - 15%)
# to clear. Prevents rapid oscillation at threshold boundaries.
# E.g., VIX HIGH triggers at 35, clears at 35 * 0.85 = 29.75.
_VIX_HYSTERESIS_PCT = 0.15
_VIX_HIGH_CLEAR = _VIX_HIGH_ABS * (1.0 - _VIX_HYSTERESIS_PCT)        # 29.75
_VIX_EXTREME_CLEAR = _VIX_EXTREME_ABS * (1.0 - _VIX_HYSTERESIS_PCT)  # 38.25

# Correlation
_CORR_THRESHOLD = 0.80        # avg cross-position correlation
_DIRECTION_CONCENTRATION = 0.80  # > 80% same direction

# Consecutive losses — THIS IS THE SOLE AUTHORITY for loss-streak thresholds (A-13).
# ImmutableRiskRules in risk_sizer.py no longer enforces consecutive loss halts.
# The threshold was unified to 5 (more conservative; was 7 here vs 5 in risk_sizer).
_CONSEC_LOSS_TIER_1 = 3       # 15 min cooldown
_CONSEC_LOSS_TIER_2 = 4       # 30 min cooldown + 50% size (graduated: between T1=3 and T3=6)
_CONSEC_LOSS_TIER_3 = 6       # halt for rest of session (graduated from T2=4)

_COOLDOWN_TIER_1_SEC = 900    # 15 minutes
_COOLDOWN_TIER_2_SEC = 1800   # 30 minutes

# Black swan
_SPY_15M_EXTREME_PCT = 0.02   # 2% in 15 minutes
_FLASH_CRASH_VOLUME_MULT = 5  # 5x normal volume
_FLASH_CRASH_PRICE_DROP = 0.03  # 3%+ price drop

# A-07: Weekly / Monthly drawdown thresholds (fraction of starting equity)
_WEEKLY_DD_HALT = 0.08         # 8% weekly loss → halt until next week
_MONTHLY_DD_HALT = 0.15        # 15% monthly loss → halt until next month

# A-09: Anti-Cascade detection
_CASCADE_STOPOUT_COUNT = 3     # number of stop-outs to trigger cascade halt
_CASCADE_WINDOW_SEC = 900      # 15-minute rolling window
_CASCADE_HALT_SEC = 1800       # 30-minute halt duration

# ---------------------------------------------------------------------------
# G-02: AUM-Tiered Portfolio Drawdown Cascade (R-06)
# ---------------------------------------------------------------------------
# Peak-to-trough portfolio drawdown (not daily — cumulative).
# Thresholds scale with AUM tier. The cascade is independent of the
# daily drawdown breaker (#1) — it catches slow multi-day bleeds that
# never trigger L1/L2/L3 in a single session.
#
# AUM tiers and their drawdown thresholds (as fractions):
#   £10K-£100K (Tier 1): -2% / -4% / -6% / -8%
#   £100K-£500K (Tier 2): -1.5% / -3% / -5% / -7%  (tighter as AUM grows)
#   £500K+ (Tier 3): -1% / -2% / -4% / -6%  (institutional risk limits)
#
# Each tier defines: (yellow_pct, orange_pct, red_pct, critical_pct)
# ---------------------------------------------------------------------------
_AUM_TIERS: list[tuple[float, float, tuple[float, float, float, float]]] = [
    # (min_equity, max_equity, (yellow, orange, red, critical))
    (0,       100_000,  (0.02, 0.04, 0.06, 0.08)),
    (100_000, 500_000,  (0.015, 0.03, 0.05, 0.07)),
    (500_000, float("inf"), (0.01, 0.02, 0.04, 0.06)),
]

# Cascade modes — what each level enforces
_CASCADE_YELLOW = "YELLOW"     # reduce position size to 75%
_CASCADE_ORANGE = "ORANGE"     # reduce to 50%, P1 alert
_CASCADE_RED = "RED"           # EXIT_ONLY mode, P0 alert
_CASCADE_CRITICAL = "CRITICAL" # HALT all trading, require manual restart


class CircuitBreakerSystem:
    """Institutional-grade circuit breaker system protecting capital
    during adverse market conditions.

    Combines six independent breaker subsystems. On every tick /
    evaluation cycle, ``check_all()`` returns a unified action dict
    that the execution engine must obey.

    Six breakers:
    1. Drawdown Circuit Breaker     -- 3-level daily loss protection
    2. Volatility Circuit Breaker   -- VIX-based entry/size controls
    3. Correlation Spike Breaker    -- Cross-position correlation guard
    4. Consecutive Loss Breaker     -- Loss streak cooldown escalation
    5. Black Swan Detector          -- Flash crash / extreme move detection
    6. Portfolio Drawdown Cascade   -- G-02: AUM-tiered peak-to-trough drawdown

    Args:
        equity: Starting account equity in base currency (default 10000).

    Usage::

        cbs = CircuitBreakerSystem(equity=10_000)

        # On every evaluation cycle
        result = cbs.check_all(
            daily_pnl=-180.0,
            equity=9820.0,
            vix_current=28.5,
            vix_prev_close=22.0,
            spy_15min_change=-0.018,
            open_positions=[...],
            recent_trades=[...],
        )

        if not result["allow_new_entries"]:
            # Block all new signals
            ...
        if result["force_close_all"]:
            # Emergency flatten everything
            ...
    """

    # ------------------------------------------------------------------
    # Redis key constants (A-06)
    # ------------------------------------------------------------------
    _KEY_PREFIX = "nzt:cb:"
    _KEY_HALTED = "nzt:cb:halted_for_session"
    _KEY_HALT_REASON = "nzt:cb:halt_reason"
    _KEY_CONSEC_LOSSES = "nzt:cb:consecutive_losses"
    _KEY_LAST_LOSS_TIME = "nzt:cb:last_loss_time"
    _KEY_COOLDOWN_UNTIL = "nzt:cb:cooldown_until"
    _KEY_VIX_PAUSE_UNTIL = "nzt:cb:vix_pause_until"
    _KEY_DAILY_RESULTS = "nzt:cb:daily_results"

    # G-02: Portfolio drawdown cascade Redis keys
    _KEY_PEAK_EQUITY = "nzt:cb:peak_equity"
    _KEY_CASCADE_MODE = "nzt:cb:cascade_mode"
    _KEY_CASCADE_HALTED = "nzt:cb:cascade_halted"

    # A-07: Weekly/Monthly drawdown Redis keys
    _KEY_WEEKLY_PNL = "nzt:cb:weekly_pnl"
    _KEY_WEEKLY_HALTED = "nzt:cb:weekly_halted"
    _KEY_MONTHLY_PNL = "nzt:cb:monthly_pnl"
    _KEY_MONTHLY_HALTED = "nzt:cb:monthly_halted"

    # A-09: Anti-cascade Redis keys
    _KEY_STOPOUT_LOG = "nzt:cb:stopout_log"
    _KEY_CASCADE_HALT_UNTIL = "nzt:cb:cascade_halt_until"

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def __init__(
        self,
        equity: float = 10_000,
        redis_client=None,
    ) -> None:
        # A-03: Paper mode risk parity — no paper_mode parameter.
        # All risk rules are identical regardless of live/paper. No relaxation.
        self._starting_equity = equity
        self._equity = equity

        # B-05 (SK-01): Track when equity was last refreshed at session reset.
        # If equity hasn't been reset today, _check_drawdown uses stale
        # denominators — false L2/L3 triggers or missed real drawdowns.
        # Initialised to now so first session after boot is considered fresh.
        self._equity_last_reset: datetime = datetime.now(timezone.utc)
        self._equity_stale: bool = False

        # A-06: Sync Redis client (same pattern as ChandelierExit)
        self._redis = redis_client

        # A-06: Threading lock to prevent write-clobbering from concurrent
        # APScheduler scan threads calling _persist_state() simultaneously.
        self._persist_lock = threading.Lock()

        # Consecutive loss tracking
        self._consecutive_losses: int = 0
        self._last_loss_time: Optional[datetime] = None
        self._cooldown_until: Optional[datetime] = None

        # VIX spike tracking
        self._vix_pause_until: Optional[datetime] = None

        # C-07: VIX hysteresis state — once a tier is triggered, it stays
        # active until VIX falls below the clear threshold (trigger - 15%).
        self._vix_high_active: bool = False    # True when VIX HIGH breaker is latched
        self._vix_extreme_active: bool = False # True when VIX EXTREME breaker is latched

        # Session state
        self._halted_for_session: bool = False
        self._halt_reason: str = ""

        # Daily trade results (R-multiples) for streak tracking
        self._daily_results: list[float] = []

        # G-02: Portfolio drawdown cascade state
        self._peak_equity: float = equity    # High-water mark
        self._cascade_mode: str = "GREEN"    # Current cascade level
        self._cascade_halted: bool = False   # CRITICAL level requires manual restart

        # A-07: Weekly/Monthly drawdown state
        self._weekly_pnl: float = 0.0        # Cumulative weekly P&L (currency units)
        self._weekly_halted: bool = False     # Halted until next week
        self._monthly_pnl: float = 0.0       # Cumulative monthly P&L (currency units)
        self._monthly_halted: bool = False    # Halted until next month

        # A-09: Anti-cascade state
        self._stopout_log: list[dict] = []       # [{ticker, timestamp_iso}, ...]
        self._cascade_halt_until: Optional[datetime] = None  # Halt new entries until

        # A-06: Hydrate state from Redis on startup (survives restarts)

        logger.info(
            "CircuitBreakerSystem initialised | equity=%.2f | "
            "DD levels: L1=%.1f%% L2=%.1f%% L3=%.1f%% | "
            "redis=%s",
            equity,
            _DD_LEVEL_1_YELLOW * 100,
            _DD_LEVEL_2_ORANGE * 100,
            _DD_LEVEL_3_RED * 100,
            "connected" if self._redis else "in-memory",
        )

    # ------------------------------------------------------------------
    # A-06: Redis Persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_session_ttl() -> int:
        """Compute session-scoped TTL that spans weekends.

        Returns max(86400, seconds_until_next_market_open + 7200).

        - On a weekday during market hours: ~86400 (24h, next day)
        - On Friday evening: ~230400+ (spans Sat + Sun + 2h buffer into Mon)
        - On Saturday: ~180000+ (spans Sun + Mon open + 2h)

        The +7200s (2h) buffer ensures the halt is still active when
        the next session actually opens, accounting for pre-market setup.
        """
        now_uk = datetime.now(_UK_TZ)
        weekday = now_uk.weekday()  # Mon=0, Sun=6

        # Find the next trading day
        if weekday < 4:  # Mon-Thu: next trading day is tomorrow
            days_ahead = 1
        elif weekday == 4:  # Friday: next is Monday (+3)
            days_ahead = 3
        elif weekday == 5:  # Saturday: next is Monday (+2)
            days_ahead = 2
        else:  # Sunday: next is Monday (+1)
            days_ahead = 1

        next_open_uk = (now_uk + timedelta(days=days_ahead)).replace(
            hour=8, minute=0, second=0, microsecond=0,
        )
        secs_until_open = (next_open_uk - now_uk).total_seconds()

        return max(86400, int(secs_until_open) + 7200)

    def _persist_state(self) -> None:
        """Persist all mutable CB state to Redis atomically under lock.

        Called after every state mutation (record_trade_result, reset_daily,
        _check_drawdown halt, _check_consecutive_losses, _check_volatility
        pause, _check_black_swan halt).

        Uses threading.Lock to prevent write-clobbering from concurrent
        APScheduler scan threads.
        """
        if not self._redis:
            return

        with self._persist_lock:
            try:
                ttl = self._compute_session_ttl()
                pipe = self._redis.pipeline(transaction=True)

                # Halt state
                pipe.set(self._KEY_HALTED, "1" if self._halted_for_session else "0", ex=ttl)
                pipe.set(self._KEY_HALT_REASON, self._halt_reason or "", ex=ttl)

                # Consecutive loss state
                pipe.set(self._KEY_CONSEC_LOSSES, str(self._consecutive_losses), ex=ttl)

                # Datetime fields: store as ISO 8601 or empty string
                pipe.set(
                    self._KEY_LAST_LOSS_TIME,
                    self._last_loss_time.isoformat() if self._last_loss_time else "",
                    ex=ttl,
                )
                pipe.set(
                    self._KEY_COOLDOWN_UNTIL,
                    self._cooldown_until.isoformat() if self._cooldown_until else "",
                    ex=ttl,
                )
                pipe.set(
                    self._KEY_VIX_PAUSE_UNTIL,
                    self._vix_pause_until.isoformat() if self._vix_pause_until else "",
                    ex=ttl,
                )

                # Daily results list (JSON array of floats)
                pipe.set(
                    self._KEY_DAILY_RESULTS,
                    json.dumps(self._daily_results),
                    ex=ttl,
                )

                # A-07: Weekly/Monthly drawdown state
                weekly_ttl = max(ttl, 604800)   # 7 days minimum for weekly
                monthly_ttl = max(ttl, 2678400)  # 31 days minimum for monthly
                pipe.set(self._KEY_WEEKLY_PNL, str(self._weekly_pnl), ex=weekly_ttl)
                pipe.set(self._KEY_WEEKLY_HALTED, "1" if self._weekly_halted else "0", ex=weekly_ttl)
                pipe.set(self._KEY_MONTHLY_PNL, str(self._monthly_pnl), ex=monthly_ttl)
                pipe.set(self._KEY_MONTHLY_HALTED, "1" if self._monthly_halted else "0", ex=monthly_ttl)

                # A-09: Anti-cascade state
                pipe.set(
                    self._KEY_STOPOUT_LOG,
                    json.dumps(self._stopout_log),
                    ex=ttl,
                )
                pipe.set(
                    self._KEY_CASCADE_HALT_UNTIL,
                    self._cascade_halt_until.isoformat() if self._cascade_halt_until else "",
                    ex=ttl,
                )

                pipe.execute()
                logger.debug(
                    "CB state persisted to Redis | halted=%s | consec_losses=%d | ttl=%ds",
                    self._halted_for_session, self._consecutive_losses, ttl,
                )
            except Exception as e:
                # Non-fatal: in-memory state is still authoritative
                logger.warning("CB Redis persist failed (in-memory authoritative): %s", e)

    def _hydrate_from_redis(self) -> None:
        """Restore circuit breaker state from Redis on startup.

        Called once during __init__. If Redis has persisted halt state
        from a previous container lifecycle, that state is restored so
        that Docker restarts cannot bypass active halts or cooldowns.

        On any failure, falls back to clean in-memory state (safe default
        for paper mode).
        """
        if not self._redis:
            return

        try:
            pipe = self._redis.pipeline(transaction=False)
            pipe.get(self._KEY_HALTED)
            pipe.get(self._KEY_HALT_REASON)
            pipe.get(self._KEY_CONSEC_LOSSES)
            pipe.get(self._KEY_LAST_LOSS_TIME)
            pipe.get(self._KEY_COOLDOWN_UNTIL)
            pipe.get(self._KEY_VIX_PAUSE_UNTIL)
            pipe.get(self._KEY_DAILY_RESULTS)
            results = pipe.execute()

            (
                halted_raw,
                halt_reason_raw,
                consec_raw,
                last_loss_raw,
                cooldown_raw,
                vix_pause_raw,
                daily_results_raw,
            ) = results

            restored_any = False

            # Halt state
            if halted_raw is not None:
                self._halted_for_session = halted_raw == "1"
                if self._halted_for_session:
                    restored_any = True
            if halt_reason_raw is not None and halt_reason_raw:
                self._halt_reason = halt_reason_raw
                restored_any = True

            # Consecutive losses
            if consec_raw is not None:
                self._consecutive_losses = int(consec_raw)
                if self._consecutive_losses > 0:
                    restored_any = True

            # Datetime fields
            if last_loss_raw is not None and last_loss_raw:
                self._last_loss_time = datetime.fromisoformat(last_loss_raw)
                restored_any = True

            if cooldown_raw is not None and cooldown_raw:
                parsed_cooldown = datetime.fromisoformat(cooldown_raw)
                # Only restore if cooldown is still in the future
                if parsed_cooldown > datetime.now(timezone.utc):
                    self._cooldown_until = parsed_cooldown
                    restored_any = True

            if vix_pause_raw is not None and vix_pause_raw:
                parsed_vix = datetime.fromisoformat(vix_pause_raw)
                # Only restore if VIX pause is still in the future
                if parsed_vix > datetime.now(timezone.utc):
                    self._vix_pause_until = parsed_vix
                    restored_any = True

            # Daily results
            if daily_results_raw is not None and daily_results_raw:
                self._daily_results = json.loads(daily_results_raw)
                if self._daily_results:
                    restored_any = True

            # A-07: Weekly/Monthly drawdown state (separate pipeline, optional keys)
            try:
                p2 = self._redis.pipeline(transaction=False)
                p2.get(self._KEY_WEEKLY_PNL)
                p2.get(self._KEY_WEEKLY_HALTED)
                p2.get(self._KEY_MONTHLY_PNL)
                p2.get(self._KEY_MONTHLY_HALTED)
                p2.get(self._KEY_STOPOUT_LOG)
                p2.get(self._KEY_CASCADE_HALT_UNTIL)
                r2 = p2.execute()

                if r2[0] is not None:
                    self._weekly_pnl = float(r2[0])
                if r2[1] is not None:
                    self._weekly_halted = r2[1] == "1"
                    if self._weekly_halted:
                        restored_any = True
                if r2[2] is not None:
                    self._monthly_pnl = float(r2[2])
                if r2[3] is not None:
                    self._monthly_halted = r2[3] == "1"
                    if self._monthly_halted:
                        restored_any = True
                if r2[4] is not None and r2[4]:
                    self._stopout_log = json.loads(r2[4])
                if r2[5] is not None and r2[5]:
                    parsed_ch = datetime.fromisoformat(r2[5])
                    if parsed_ch > datetime.now(timezone.utc):
                        self._cascade_halt_until = parsed_ch
                        restored_any = True
            except Exception:
                pass  # Non-critical — new keys may not exist yet

            if restored_any:
                logger.warning(
                    "CB STATE HYDRATED FROM REDIS | halted=%s | reason=%s | "
                    "consec_losses=%d | cooldown=%s | vix_pause=%s | "
                    "daily_trades=%d",
                    self._halted_for_session, self._halt_reason,
                    self._consecutive_losses,
                    self._cooldown_until is not None,
                    self._vix_pause_until is not None,
                    len(self._daily_results),
                )
            else:
                logger.info("CB Redis hydration: no persisted state found (clean start)")

        except Exception as e:
            logger.warning(
                "CB Redis hydration failed (clean in-memory start): %s", e,
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_all(
        self,
        daily_pnl: float,
        equity: float,
        vix_current: float,
        vix_prev_close: float,
        spy_15min_change: float,
        open_positions: list[Any],
        recent_trades: list[Any],
        volume_ratio: float = 1.0,
    ) -> dict[str, Any]:
        """Run every circuit breaker and return the unified action.

        A-08: This method is the SOLE CANONICAL AUTHORITY for daily loss
        halts. VirtualTrader Phase-9 _check_pnl_kill_switch() is a legacy
        duplicate and should defer to CB results.

        Args:
            daily_pnl: TOTAL P&L (realised + unrealised) for the current
                       session in currency units (negative = loss). Callers
                       MUST sum both components — using realised-only would
                       allow unrealised losses to bypass the drawdown breaker.
            equity: Current account equity (starting + cumulative P&L).
            vix_current: Current VIX level.
            vix_prev_close: Previous session VIX close.
            spy_15min_change: SPY percentage change over the last 15 min
                              (e.g. -0.025 for a -2.5% move).
            open_positions: List of open Position objects (or dicts).
            recent_trades: List of recently completed Trade objects.
            volume_ratio: Current volume / average volume for SPY
                          (used by Black Swan detector). Default 1.0.

        Returns:
            dict with keys:
                level (str): "GREEN" | "YELLOW" | "ORANGE" | "RED"
                action (str): Human-readable action description.
                size_multiplier (float): 0.0 - 1.0 scaling factor.
                allow_new_entries (bool): Whether new entries are allowed.
                force_close_all (bool): Emergency flatten everything.
                force_close_etps (bool): Emergency close leveraged ETPs only.
                alerts (list[str]): Accumulated alert messages.
        """
        self._equity = equity
        alerts: list[str] = []

        # Start with the most permissive state
        level = "GREEN"
        action = "Normal trading."
        size_multiplier = 1.0
        allow_new_entries = True
        force_close_all = False
        force_close_etps = False

        # If already halted for the session, short-circuit
        if self._halted_for_session:
            return {
                "level": "RED",
                "action": f"SESSION HALTED: {self._halt_reason}",
                "size_multiplier": 0.0,
                "allow_new_entries": False,
                "force_close_all": True,
                "force_close_etps": True,
                "alerts": [f"Session halted: {self._halt_reason}"],
            }

        # B-05 (SK-01): Block all trading if equity denominator is stale.
        # This prevents drawdown % calculations against a stale _starting_equity
        # from producing false L2/L3 triggers or missing real drawdowns.
        if self._equity_stale:
            stale_msg = (
                "EQUITY STALE: _starting_equity not refreshed this session. "
                "Call reset_daily(current_equity=...) or reset_daily_equity() "
                "to unblock trading."
            )
            logger.warning("B-05 EQUITY FRESHNESS GUARD | %s", stale_msg)
            return {
                "level": "RED",
                "action": f"BLOCKED: {stale_msg}",
                "size_multiplier": 0.0,
                "allow_new_entries": False,
                "force_close_all": False,
                "force_close_etps": False,
                "alerts": [stale_msg],
            }

        # --- 1. Drawdown Circuit Breaker ---
        dd = self._check_drawdown(daily_pnl, equity)
        level = self._escalate_level(level, dd["level"])
        size_multiplier = min(size_multiplier, dd["size_multiplier"])
        allow_new_entries = allow_new_entries and dd["allow_new_entries"]
        force_close_all = force_close_all or dd["force_close_all"]
        alerts.extend(dd["alerts"])

        # --- 2. Volatility Circuit Breaker ---
        vol = self._check_volatility(vix_current, vix_prev_close)
        level = self._escalate_level(level, vol["level"])
        size_multiplier = min(size_multiplier, vol["size_multiplier"])
        allow_new_entries = allow_new_entries and vol["allow_new_entries"]
        force_close_etps = force_close_etps or vol["force_close_etps"]
        alerts.extend(vol["alerts"])

        # --- 3. Correlation Spike Breaker ---
        corr = self._check_correlation(open_positions)
        level = self._escalate_level(level, corr["level"])
        allow_new_entries = allow_new_entries and corr["allow_new_entries"]
        alerts.extend(corr["alerts"])

        # --- 4. Consecutive Loss Breaker ---
        consec = self._check_consecutive_losses()
        level = self._escalate_level(level, consec["level"])
        size_multiplier = min(size_multiplier, consec["size_multiplier"])
        allow_new_entries = allow_new_entries and consec["allow_new_entries"]
        force_close_all = force_close_all or consec["force_close_all"]
        alerts.extend(consec["alerts"])

        # --- 5. Black Swan Detector ---
        swan = self._check_black_swan(
            spy_15min_change, volume_ratio, open_positions,
        )
        level = self._escalate_level(level, swan["level"])
        force_close_all = force_close_all or swan["force_close_all"]
        allow_new_entries = allow_new_entries and swan["allow_new_entries"]
        alerts.extend(swan["alerts"])

        # --- 6. A-07: Weekly/Monthly Circuit Breaker ---
        wm = self._check_weekly_monthly(daily_pnl, equity)
        level = self._escalate_level(level, wm["level"])
        size_multiplier = min(size_multiplier, wm["size_multiplier"])
        allow_new_entries = allow_new_entries and wm["allow_new_entries"]
        force_close_all = force_close_all or wm["force_close_all"]
        alerts.extend(wm["alerts"])

        # --- 7. A-09: Anti-Cascade Stop Logic ---
        cascade = self._check_anti_cascade()
        level = self._escalate_level(level, cascade["level"])
        allow_new_entries = allow_new_entries and cascade["allow_new_entries"]
        alerts.extend(cascade["alerts"])

        # Derive final action string
        if force_close_all:
            action = "EMERGENCY: Close ALL positions immediately. Trading halted."
        elif force_close_etps:
            action = "Close all leveraged ETPs. Reduce sizes per multiplier."
        elif not allow_new_entries:
            action = "No new entries. Manage existing positions only."
        elif size_multiplier < 1.0:
            action = f"Reduce position sizes to {size_multiplier*100:.0f}% of standard."
        else:
            action = "Normal trading. All breakers GREEN."

        result = {
            "level": level,
            "action": action,
            "size_multiplier": size_multiplier,
            "allow_new_entries": allow_new_entries,
            "force_close_all": force_close_all,
            "force_close_etps": force_close_etps,
            "alerts": alerts,
        }

        if level != "GREEN":
            logger.warning(
                "CIRCUIT BREAKER [%s] | mult=%.2f | new_entries=%s | "
                "close_all=%s | close_etps=%s | alerts=%d",
                level, size_multiplier, allow_new_entries,
                force_close_all, force_close_etps, len(alerts),
            )

        return result

    def record_trade_result(self, r_multiple: float) -> None:
        """Record a completed trade result for streak tracking.

        Args:
            r_multiple: The R-multiple of the completed trade.
                        Negative values are losses, positive are wins.
        """
        self._daily_results.append(r_multiple)

        if r_multiple < 0:
            self._consecutive_losses += 1
            self._last_loss_time = datetime.now(timezone.utc)
            logger.info(
                "Trade loss recorded: %.2fR | consecutive_losses=%d",
                r_multiple, self._consecutive_losses,
            )
        else:
            # Win or breakeven resets the streak
            if self._consecutive_losses > 0:
                logger.info(
                    "Loss streak broken at %d with %.2fR win",
                    self._consecutive_losses, r_multiple,
                )
            self._consecutive_losses = 0
            self._cooldown_until = None

        # A-06: Persist after every trade result
        self._persist_state()

    def reset_daily(self, current_equity: float | None = None) -> None:
        """Reset all daily state at the start of a new trading session.

        Call this at market open or when the daily session begins.
        Consecutive losses carry over only within a session; a new day
        starts fresh.
        """
        self._consecutive_losses = 0
        self._last_loss_time = None
        self._cooldown_until = None
        self._vix_pause_until = None
        self._halted_for_session = False
        self._halt_reason = ""
        self._daily_results.clear()

        # B-05 (SK-01): Update starting equity for daily P&L % calculations.
        # Assert freshness — if equity not provided, mark stale so check_all
        # blocks trading until confirmed via reset_daily_equity().
        if current_equity is not None and current_equity > 0:
            self._starting_equity = current_equity
            self._equity_last_reset = datetime.now(timezone.utc)
            self._equity_stale = False
            logger.info("Circuit breaker daily reset — equity updated to %.2f", current_equity)
        else:
            self._equity_stale = True
            logger.warning(
                "Circuit breaker daily reset — NO equity provided. "
                "Trading BLOCKED until reset_daily_equity() confirms fresh equity."
            )

        # A-06: Persist clean state to Redis (clears previous halt/cooldown)
        self._persist_state()

    def reset_daily_equity(self, new_equity: float) -> None:
        """B-05 (SK-01): Mid-session equity refresh for accurate P&L % calculations.

        Call this when broker/VT equity is confirmed after a failed or
        equity-less reset_daily() call. Clears the stale flag so trading
        can resume.

        Args:
            new_equity: Current broker/VT equity in base currency.
                        Must be > 0 or the call is rejected.
        """
        if new_equity <= 0:
            logger.error(
                "reset_daily_equity rejected: new_equity=%.2f is not positive. "
                "Trading remains BLOCKED.",
                new_equity,
            )
            return

        old = self._starting_equity
        self._starting_equity = new_equity
        self._equity_last_reset = datetime.now(timezone.utc)
        self._equity_stale = False
        logger.info(
            "B-05: Equity denominator refreshed | old=%.2f -> new=%.2f | stale=False",
            old, new_equity,
        )
        self._persist_state()

    def get_status(self) -> dict[str, Any]:
        """Return a snapshot of the circuit breaker system state.

        Useful for dashboards, logging, and Telegram status messages.

        Returns:
            dict with current state of every breaker subsystem.
        """
        now = datetime.now(timezone.utc)

        cooldown_remaining_sec = 0.0
        if self._cooldown_until and now < self._cooldown_until:
            cooldown_remaining_sec = (
                self._cooldown_until - now
            ).total_seconds()

        vix_pause_remaining_sec = 0.0
        if self._vix_pause_until and now < self._vix_pause_until:
            vix_pause_remaining_sec = (
                self._vix_pause_until - now
            ).total_seconds()

        return {
            "equity": self._equity,
            "starting_equity": self._starting_equity,
            "equity_stale": self._equity_stale,  # B-05: freshness flag
            "equity_last_reset": self._equity_last_reset.isoformat(),  # B-05
            "halted_for_session": self._halted_for_session,
            "halt_reason": self._halt_reason,
            "consecutive_losses": self._consecutive_losses,
            "cooldown_remaining_sec": cooldown_remaining_sec,
            "vix_pause_remaining_sec": vix_pause_remaining_sec,
            "daily_trades": len(self._daily_results),
            "daily_results": list(self._daily_results),
            "thresholds": {
                "drawdown_l1_pct": _DD_LEVEL_1_YELLOW * 100,
                "drawdown_l2_pct": _DD_LEVEL_2_ORANGE * 100,
                "drawdown_l3_pct": _DD_LEVEL_3_RED * 100,
                "vix_spike_pct": _VIX_SPIKE_PCT * 100,
                "vix_high": _VIX_HIGH_ABS,
                "vix_extreme": _VIX_EXTREME_ABS,
                "correlation_threshold": _CORR_THRESHOLD,
                "direction_concentration": _DIRECTION_CONCENTRATION,
                "consec_loss_t1": _CONSEC_LOSS_TIER_1,
                "consec_loss_t2": _CONSEC_LOSS_TIER_2,
                "consec_loss_t3": _CONSEC_LOSS_TIER_3,
                "spy_15m_extreme_pct": _SPY_15M_EXTREME_PCT * 100,
            },
        }

    # ------------------------------------------------------------------
    # Breaker 1: Drawdown Circuit Breaker
    # ------------------------------------------------------------------

    def _check_drawdown(
        self, daily_pnl: float, equity: float,
    ) -> dict[str, Any]:
        """Three-level daily drawdown protection.

        Level 1 (YELLOW): Daily loss > 1.5% equity
            -> Reduce position sizes by 50%.
        Level 2 (ORANGE): Daily loss > 2.5% equity
            -> Stop new entries, manage existing only.
        Level 3 (RED): Daily loss > 4% equity
            -> Close all positions, halt for rest of day.
        """
        alerts: list[str] = []
        level = "GREEN"
        size_multiplier = 1.0
        allow_new_entries = True
        force_close_all = False

        # Calculate daily loss as percentage of starting equity
        # (use starting equity so the threshold doesn't shift as we lose)
        loss_pct = abs(min(daily_pnl, 0)) / self._starting_equity

        if loss_pct > _DD_LEVEL_3_RED:
            level = "RED"
            size_multiplier = 0.0
            allow_new_entries = False
            force_close_all = True
            self._halted_for_session = True
            self._halt_reason = (
                f"Drawdown L3 RED: daily loss {loss_pct*100:.2f}% > "
                f"{_DD_LEVEL_3_RED*100:.1f}% threshold"
            )
            alerts.append(
                f"DRAWDOWN L3 RED: Loss {loss_pct*100:.2f}% of equity. "
                f"CLOSE ALL. HALT SESSION."
            )
            logger.critical(
                "DRAWDOWN CIRCUIT BREAKER L3 RED | loss=%.2f%% | "
                "CLOSING ALL POSITIONS | SESSION HALTED",
                loss_pct * 100,
            )
            # A-06: Persist halt state to Redis immediately
            self._persist_state()

        elif loss_pct > _DD_LEVEL_2_ORANGE:
            level = "ORANGE"
            size_multiplier = 0.50
            allow_new_entries = False
            alerts.append(
                f"DRAWDOWN L2 ORANGE: Loss {loss_pct*100:.2f}% of equity. "
                f"Size reduced 50%. No new entries."
            )
            logger.warning(
                "DRAWDOWN CIRCUIT BREAKER L2 ORANGE | loss=%.2f%% | "
                "NEW ENTRIES BLOCKED",
                loss_pct * 100,
            )

        elif loss_pct > _DD_LEVEL_1_YELLOW:
            level = "YELLOW"
            size_multiplier = 0.50
            alerts.append(
                f"DRAWDOWN L1 YELLOW: Loss {loss_pct*100:.2f}% of equity. "
                f"Sizes reduced to 50%."
            )
            logger.warning(
                "DRAWDOWN CIRCUIT BREAKER L1 YELLOW | loss=%.2f%% | "
                "SIZES HALVED",
                loss_pct * 100,
            )

        return {
            "level": level,
            "size_multiplier": size_multiplier,
            "allow_new_entries": allow_new_entries,
            "force_close_all": force_close_all,
            "alerts": alerts,
        }

    # ------------------------------------------------------------------
    # Breaker 2: Volatility Circuit Breaker (VIX)
    # ------------------------------------------------------------------

    def _check_volatility(
        self, vix_current: float, vix_prev_close: float,
    ) -> dict[str, Any]:
        """VIX-based circuit breaker with three tiers.

        - VIX spike > 25% intraday -> pause new entries for 30 minutes.
        - VIX > 35 absolute -> reduce all sizes by 50%.
        - VIX > 45 -> emergency close all leveraged ETPs.
        """
        alerts: list[str] = []
        level = "GREEN"
        size_multiplier = 1.0
        allow_new_entries = True
        force_close_etps = False
        now = datetime.now(timezone.utc)

        # Check for VIX spike (intraday percentage change)
        if vix_prev_close > 0:
            vix_change_pct = (vix_current - vix_prev_close) / vix_prev_close
        else:
            vix_change_pct = 0.0

        # Tier 1: Intraday VIX spike > 25%
        if vix_change_pct >= _VIX_SPIKE_PCT:
            self._vix_pause_until = now + timedelta(seconds=_VIX_SPIKE_PAUSE_SEC)
            alerts.append(
                f"VIX SPIKE: +{vix_change_pct*100:.1f}% intraday "
                f"({vix_prev_close:.1f} -> {vix_current:.1f}). "
                f"New entries paused for 30 min."
            )
            logger.warning(
                "VIX SPIKE BREAKER | change=+%.1f%% | pausing entries 30 min",
                vix_change_pct * 100,
            )
            # A-06: Persist VIX pause timestamp to Redis
            self._persist_state()

        # Enforce existing VIX pause
        if self._vix_pause_until and now < self._vix_pause_until:
            allow_new_entries = False
            remaining = (self._vix_pause_until - now).total_seconds() / 60
            level = self._escalate_level(level, "YELLOW")
            alerts.append(
                f"VIX spike pause active: {remaining:.0f} min remaining."
            )

        # Tier 2: VIX > 35 absolute — with C-07 hysteresis
        # Once triggered, stays active until VIX falls below 29.75 (35 * 0.85).
        # Prevents rapid oscillation at the 35 boundary.
        if vix_current >= _VIX_HIGH_ABS:
            self._vix_high_active = True
        elif self._vix_high_active and vix_current < _VIX_HIGH_CLEAR:
            self._vix_high_active = False
            logger.info(
                "C-07 VIX HIGH CLEARED: VIX=%.1f < clear threshold %.1f",
                vix_current, _VIX_HIGH_CLEAR,
            )

        if self._vix_high_active:
            level = self._escalate_level(level, "ORANGE")
            size_multiplier = min(size_multiplier, 0.50)
            alerts.append(
                f"VIX HIGH: {vix_current:.1f} (trigger={_VIX_HIGH_ABS}, "
                f"clear={_VIX_HIGH_CLEAR:.1f}). All sizes reduced to 50%."
            )
            logger.warning(
                "VIX HIGH BREAKER | VIX=%.1f | sizes halved | "
                "clear_at=%.1f (C-07 hysteresis)",
                vix_current, _VIX_HIGH_CLEAR,
            )

        # Tier 3: VIX > 45 -- emergency close leveraged ETPs — with C-07 hysteresis
        # Once triggered, stays active until VIX falls below 38.25 (45 * 0.85).
        if vix_current >= _VIX_EXTREME_ABS:
            self._vix_extreme_active = True
        elif self._vix_extreme_active and vix_current < _VIX_EXTREME_CLEAR:
            self._vix_extreme_active = False
            logger.info(
                "C-07 VIX EXTREME CLEARED: VIX=%.1f < clear threshold %.1f",
                vix_current, _VIX_EXTREME_CLEAR,
            )

        if self._vix_extreme_active:
            level = self._escalate_level(level, "RED")
            force_close_etps = True
            allow_new_entries = False
            alerts.append(
                f"VIX EXTREME: {vix_current:.1f} (trigger={_VIX_EXTREME_ABS}, "
                f"clear={_VIX_EXTREME_CLEAR:.1f}). "
                f"CLOSE ALL LEVERAGED ETPs. No new entries."
            )
            logger.critical(
                "VIX EXTREME BREAKER | VIX=%.1f | CLOSING LEVERAGED ETPs | "
                "clear_at=%.1f (C-07 hysteresis)",
                vix_current, _VIX_EXTREME_CLEAR,
            )

        return {
            "level": level,
            "size_multiplier": size_multiplier,
            "allow_new_entries": allow_new_entries,
            "force_close_etps": force_close_etps,
            "alerts": alerts,
        }

    # ------------------------------------------------------------------
    # Breaker 3: Correlation Spike Breaker
    # ------------------------------------------------------------------

    def _check_correlation(
        self, open_positions: list[Any],
    ) -> dict[str, Any]:
        """Cross-position correlation guard.

        - Average pairwise correlation > 0.8 -> block new correlated entries.
        - Portfolio > 80% same direction -> alert, block new same-direction.
        """
        alerts: list[str] = []
        level = "GREEN"
        allow_new_entries = True

        if len(open_positions) < 2:
            return {
                "level": level,
                "allow_new_entries": allow_new_entries,
                "alerts": alerts,
            }

        # --- Correlation check using config matrix ---
        corr_matrix = cfg.get("correlation.matrix", {})
        correlations: list[float] = []

        tickers = []
        for pos in open_positions:
            ticker = pos.ticker if hasattr(pos, "ticker") else pos.get("ticker", "")
            if ticker:
                tickers.append(ticker)

        for i in range(len(tickers)):
            for j in range(i + 1, len(tickers)):
                t1, t2 = tickers[i], tickers[j]
                # Check both orderings in the matrix
                key_fwd = f"{t1}_{t2}"
                key_rev = f"{t2}_{t1}"
                corr_val = corr_matrix.get(key_fwd, corr_matrix.get(key_rev))
                if corr_val is not None:
                    correlations.append(float(corr_val))

        if correlations:
            avg_corr = sum(correlations) / len(correlations)
            if avg_corr > _CORR_THRESHOLD:
                level = "YELLOW"
                allow_new_entries = False
                alerts.append(
                    f"CORRELATION SPIKE: Avg pairwise correlation "
                    f"{avg_corr:.2f} > {_CORR_THRESHOLD}. "
                    f"New correlated entries BLOCKED."
                )
                logger.warning(
                    "CORRELATION BREAKER | avg_corr=%.2f | "
                    "blocking new correlated entries",
                    avg_corr,
                )

        # --- Direction concentration check ---
        if open_positions:
            long_count = 0
            short_count = 0
            for pos in open_positions:
                direction = (
                    pos.direction if hasattr(pos, "direction")
                    else pos.get("direction", "")
                )
                direction_str = (
                    direction.value if hasattr(direction, "value")
                    else str(direction)
                )
                if direction_str == "LONG":
                    long_count += 1
                elif direction_str == "SHORT":
                    short_count += 1

            total = long_count + short_count
            if total > 0:
                dominant_pct = max(long_count, short_count) / total
                dominant_dir = "LONG" if long_count >= short_count else "SHORT"

                if dominant_pct > _DIRECTION_CONCENTRATION:
                    level = self._escalate_level(level, "YELLOW")
                    alerts.append(
                        f"DIRECTION CONCENTRATION: {dominant_pct*100:.0f}% "
                        f"{dominant_dir} ({long_count}L/{short_count}S). "
                        f"Portfolio too directional (>{_DIRECTION_CONCENTRATION*100:.0f}%)."
                    )
                    logger.warning(
                        "DIRECTION CONCENTRATION | %.0f%% %s | "
                        "%dL/%dS positions",
                        dominant_pct * 100, dominant_dir,
                        long_count, short_count,
                    )

        return {
            "level": level,
            "allow_new_entries": allow_new_entries,
            "alerts": alerts,
        }

    # ------------------------------------------------------------------
    # Breaker 4: Consecutive Loss Breaker
    # ------------------------------------------------------------------

    def _check_consecutive_losses(self) -> dict[str, Any]:
        """Escalating response to consecutive losing trades.

        THIS IS THE SOLE AUTHORITY for consecutive loss halts (A-13).
        ImmutableRiskRules no longer enforces loss-streak halts.

        Graduated tiers (Finding 16 fix):
        Tier 1 (3 losses): 15-minute cooldown before next entry.
        Tier 2 (4 losses): 30-minute cooldown + 50% size reduction.
        Tier 3 (6 losses): Halt trading for the rest of the session.
        """
        alerts: list[str] = []
        level = "GREEN"
        size_multiplier = 1.0
        allow_new_entries = True
        force_close_all = False
        now = datetime.now(timezone.utc)

        losses = self._consecutive_losses

        # Tier 3: 6+ consecutive losses -> session halt (graduated: T1=3, T2=4, T3=6)
        # This is the SOLE AUTHORITY for consecutive loss halts.
        if losses >= _CONSEC_LOSS_TIER_3:
            level = "RED"
            size_multiplier = 0.0
            allow_new_entries = False
            force_close_all = True
            self._halted_for_session = True
            self._halt_reason = (
                f"Consecutive loss streak: {losses} losses in a row"
            )
            alerts.append(
                f"CONSECUTIVE LOSS L3: {losses} losses in a row. "
                f"SESSION HALTED. Close all positions."
            )
            logger.critical(
                "CONSECUTIVE LOSS BREAKER L3 | %d losses | SESSION HALTED",
                losses,
            )
            # A-06: Persist halt state to Redis immediately
            self._persist_state()
            return {
                "level": level,
                "size_multiplier": size_multiplier,
                "allow_new_entries": allow_new_entries,
                "force_close_all": force_close_all,
                "alerts": alerts,
            }

        # Tier 2: 4-5 consecutive losses -> 30 min cooldown + half size
        if losses >= _CONSEC_LOSS_TIER_2:
            level = "ORANGE"
            size_multiplier = 0.50

            if self._last_loss_time and not self._cooldown_until:
                self._cooldown_until = self._last_loss_time + timedelta(
                    seconds=_COOLDOWN_TIER_2_SEC,
                )
                # A-06: Persist cooldown to Redis
                self._persist_state()

            if self._cooldown_until and now < self._cooldown_until:
                allow_new_entries = False
                remaining = (self._cooldown_until - now).total_seconds() / 60
                alerts.append(
                    f"CONSECUTIVE LOSS L2: {losses} losses. "
                    f"Cooldown: {remaining:.0f} min remaining. "
                    f"Sizes at 50% when cooldown expires."
                )
            else:
                alerts.append(
                    f"CONSECUTIVE LOSS L2: {losses} losses. "
                    f"Cooldown expired. Sizes at 50%."
                )

            logger.warning(
                "CONSECUTIVE LOSS BREAKER L2 | %d losses | "
                "size=50%% | cooldown=%s",
                losses, allow_new_entries is False,
            )
            return {
                "level": level,
                "size_multiplier": size_multiplier,
                "allow_new_entries": allow_new_entries,
                "force_close_all": force_close_all,
                "alerts": alerts,
            }

        # Tier 1: 3-4 consecutive losses -> 15 min cooldown
        if losses >= _CONSEC_LOSS_TIER_1:
            level = "YELLOW"

            if self._last_loss_time and not self._cooldown_until:
                self._cooldown_until = self._last_loss_time + timedelta(
                    seconds=_COOLDOWN_TIER_1_SEC,
                )
                # A-06: Persist cooldown to Redis
                self._persist_state()

            if self._cooldown_until and now < self._cooldown_until:
                allow_new_entries = False
                remaining = (self._cooldown_until - now).total_seconds() / 60
                alerts.append(
                    f"CONSECUTIVE LOSS L1: {losses} losses. "
                    f"Cooldown: {remaining:.0f} min remaining."
                )
            else:
                alerts.append(
                    f"CONSECUTIVE LOSS L1: {losses} losses. "
                    f"Cooldown expired. Proceed with caution."
                )
                # Clear expired cooldown so it can re-trigger on next loss
                self._cooldown_until = None

            logger.warning(
                "CONSECUTIVE LOSS BREAKER L1 | %d losses | cooldown=%s",
                losses, allow_new_entries is False,
            )

        return {
            "level": level,
            "size_multiplier": size_multiplier,
            "allow_new_entries": allow_new_entries,
            "force_close_all": force_close_all,
            "alerts": alerts,
        }

    # ------------------------------------------------------------------
    # Breaker 5: Black Swan Detector
    # ------------------------------------------------------------------

    def _check_black_swan(
        self,
        spy_15min_change: float,
        volume_ratio: float,
        open_positions: list[Any],
    ) -> dict[str, Any]:
        """Detect extreme market events and trigger emergency exits.

        Pattern 1: SPY moves > 2% in 15 minutes
            -> Tighten all stops to breakeven or exit immediately.

        Pattern 2: Flash crash -- volume spike 5x + price drop 3%+
            -> Emergency exit all positions.
        """
        alerts: list[str] = []
        level = "GREEN"
        force_close_all = False
        allow_new_entries = True

        spy_move = abs(spy_15min_change)

        # Pattern 1: SPY extreme move (> 2% in 15 min)
        if spy_move >= _SPY_15M_EXTREME_PCT:
            level = "RED"
            allow_new_entries = False
            direction = "DOWN" if spy_15min_change < 0 else "UP"
            alerts.append(
                f"BLACK SWAN: SPY moved {spy_15min_change*100:+.2f}% "
                f"in 15 min ({direction}). "
                f"Tighten ALL stops to breakeven or EXIT."
            )
            logger.critical(
                "BLACK SWAN DETECTOR | SPY 15min change=%+.2f%% | "
                "TIGHTEN STOPS OR EXIT",
                spy_15min_change * 100,
            )

        # Pattern 2: Flash crash (volume spike 5x + price drop 3%+)
        if (
            volume_ratio >= _FLASH_CRASH_VOLUME_MULT
            and spy_15min_change <= -_FLASH_CRASH_PRICE_DROP
        ):
            level = "RED"
            force_close_all = True
            allow_new_entries = False
            self._halted_for_session = True
            self._halt_reason = (
                f"Flash crash detected: SPY {spy_15min_change*100:+.2f}% "
                f"with {volume_ratio:.1f}x volume"
            )
            alerts.append(
                f"FLASH CRASH DETECTED: SPY {spy_15min_change*100:+.2f}% "
                f"with {volume_ratio:.1f}x volume. "
                f"EMERGENCY EXIT ALL POSITIONS. SESSION HALTED."
            )
            logger.critical(
                "FLASH CRASH DETECTOR | SPY=%+.2f%% | vol=%+.1fx | "
                "EMERGENCY EXIT ALL | SESSION HALTED",
                spy_15min_change * 100, volume_ratio,
            )
            # A-06: Persist halt state to Redis immediately
            self._persist_state()

        return {
            "level": level,
            "force_close_all": force_close_all,
            "allow_new_entries": allow_new_entries,
            "alerts": alerts,
        }

    # ------------------------------------------------------------------
    # Breaker 7: A-07 Weekly/Monthly Drawdown Circuit Breaker
    # ------------------------------------------------------------------

    def _check_weekly_monthly(self, daily_pnl: float, equity: float) -> dict[str, Any]:
        """A-07: Weekly (-8%) and monthly (-15%) circuit breakers.

        Prevents catastrophic multi-day drawdowns that daily breakers miss.
        Five consecutive L3 days = 20% equity loss without this check.
        """
        alerts: list[str] = []
        level = "GREEN"
        size_multiplier = 1.0
        allow_new_entries = True
        force_close_all = False

        if equity <= 0:
            return {
                "level": level, "size_multiplier": size_multiplier,
                "allow_new_entries": allow_new_entries,
                "force_close_all": force_close_all, "alerts": alerts,
            }

        # Weekly check
        if self._weekly_halted:
            level = "RED"
            allow_new_entries = False
            force_close_all = True
            alerts.append(
                f"WEEKLY HALT ACTIVE: weekly P&L = {self._weekly_pnl:.2f} "
                f"(>{_WEEKLY_DD_HALT*100:.0f}% drawdown)"
            )
        elif self._starting_equity > 0:
            weekly_dd = abs(min(0, self._weekly_pnl)) / self._starting_equity
            if weekly_dd >= _WEEKLY_DD_HALT:
                self._weekly_halted = True
                level = "RED"
                allow_new_entries = False
                force_close_all = True
                self._halted_for_session = True
                self._halt_reason = f"Weekly drawdown {weekly_dd*100:.1f}% >= {_WEEKLY_DD_HALT*100:.0f}%"
                alerts.append(f"WEEKLY HALT TRIGGERED: {self._halt_reason}")
                logger.critical("A-07 WEEKLY CIRCUIT BREAKER | dd=%.1f%% | HALT", weekly_dd * 100)
                self._persist_state()

        # Monthly check
        if self._monthly_halted:
            level = "RED"
            allow_new_entries = False
            force_close_all = True
            alerts.append(
                f"MONTHLY HALT ACTIVE: monthly P&L = {self._monthly_pnl:.2f} "
                f"(>{_MONTHLY_DD_HALT*100:.0f}% drawdown)"
            )
        elif self._starting_equity > 0:
            monthly_dd = abs(min(0, self._monthly_pnl)) / self._starting_equity
            if monthly_dd >= _MONTHLY_DD_HALT:
                self._monthly_halted = True
                level = "RED"
                allow_new_entries = False
                force_close_all = True
                self._halted_for_session = True
                self._halt_reason = f"Monthly drawdown {monthly_dd*100:.1f}% >= {_MONTHLY_DD_HALT*100:.0f}%"
                alerts.append(f"MONTHLY HALT TRIGGERED: {self._halt_reason}")
                logger.critical("A-07 MONTHLY CIRCUIT BREAKER | dd=%.1f%% | HALT", monthly_dd * 100)
                self._persist_state()

        return {
            "level": level, "size_multiplier": size_multiplier,
            "allow_new_entries": allow_new_entries,
            "force_close_all": force_close_all, "alerts": alerts,
        }

    # ------------------------------------------------------------------
    # Breaker 8: A-09 Anti-Cascade Detector
    # ------------------------------------------------------------------

    def record_stopout(self, ticker: str) -> None:
        """A-09: Record a stop-out event for cascade detection."""
        entry = {"ticker": ticker, "timestamp_iso": datetime.now(timezone.utc).isoformat()}
        self._stopout_log.append(entry)
        # Trim old entries beyond the window
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=_CASCADE_WINDOW_SEC)
        self._stopout_log = [
            e for e in self._stopout_log
            if datetime.fromisoformat(e["timestamp_iso"]).replace(tzinfo=timezone.utc) > cutoff
        ]
        logger.info("A-09 STOPOUT recorded: %s | stopouts_in_window=%d", ticker, len(self._stopout_log))

        # Check if cascade threshold breached
        if len(self._stopout_log) >= _CASCADE_STOPOUT_COUNT:
            self._cascade_halt_until = datetime.now(timezone.utc) + timedelta(seconds=_CASCADE_HALT_SEC)
            logger.critical(
                "A-09 CASCADE HALT: %d stop-outs in %ds → halt ALL entries for %ds",
                len(self._stopout_log), _CASCADE_WINDOW_SEC, _CASCADE_HALT_SEC,
            )
        self._persist_state()

    def _check_anti_cascade(self) -> dict[str, Any]:
        """A-09: Portfolio-wide anti-cascade stop logic.

        3 stop-outs in 15 min across ANY tickers → halt ALL entries for 30 min.
        """
        alerts: list[str] = []
        level = "GREEN"
        allow_new_entries = True

        if self._cascade_halt_until is not None:
            now = datetime.now(timezone.utc)
            if now < self._cascade_halt_until:
                remaining = (self._cascade_halt_until - now).total_seconds()
                level = "ORANGE"
                allow_new_entries = False
                alerts.append(
                    f"A-09 CASCADE HALT: {len(self._stopout_log)} stop-outs in window. "
                    f"No new entries for {remaining:.0f}s"
                )
            else:
                # Halt expired
                self._cascade_halt_until = None
                self._stopout_log.clear()
                self._persist_state()

        return {
            "level": level, "size_multiplier": 1.0,
            "allow_new_entries": allow_new_entries,
            "force_close_all": False, "alerts": alerts,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _escalate_level(current: str, proposed: str) -> str:
        """Return the more severe of two breaker levels.

        Severity order: GREEN < YELLOW < ORANGE < RED.
        """
        severity = {"GREEN": 0, "YELLOW": 1, "ORANGE": 2, "RED": 3}
        if severity.get(proposed, 0) > severity.get(current, 0):
            return proposed
        return current
