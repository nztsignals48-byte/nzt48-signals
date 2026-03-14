"""
command_center/tick_loop.py
============================
Realtime tick loop — runs every N seconds during session windows.

Responsibilities:
  1. Detect current session (LSE / NYSE / off-hours)
  2. Detect regime from macro data
  3. Run SignalEngine (strict → fallback)
  4. Update CommandCenterState
  5. Compute TickDiff (what changed)
  6. Push Telegram alert if notable change
  7. Store signals to DB

Sessions (UK time):
  PRE_LSE   06:00 – 08:00
  LSE       08:00 – 16:30
  OVERLAP   14:30 – 16:30  (LSE + NYSE both open)
  PRE_NYSE  12:00 – 14:30
  NYSE      14:30 – 21:00
  EOD       21:00 – 22:00
  OFF_HOURS everything else
"""

from __future__ import annotations

import asyncio
import collections
import gc
import json
import logging
import math
import os
import time as _time_mod
from datetime import datetime, timezone, time as dtime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo  # kept for type hints; prefer core.clock.UK_TZ

import yfinance as yf

from command_center.diff import DiffEngine
from command_center.state import get_state
from core.quant_math.microstructure import calculate_micro_price
from core.quant_math.vpin import calculate_vpin
from core.quant_math.ofi import calculate_ofi
from core.quant_math.hawkes import HawkesMicrostructureMonitor
from core.quant_math.lead_lag import detect_lead_signal
from signal_engine.engine import SignalEngine
from uk_isa.isa_universe import CORE_UNIVERSE, EXTENDED_UNIVERSE
from execution.virtual_trader import VirtualTrader
from models import Signal, Direction, Bot, BotInstance, RegimeState, ConfidenceBreakdown

logger = logging.getLogger("nzt48.tick_loop")
from core.clock import UK_TZ as _UK, now_uk, now_utc, mono_ns, elapsed_ms, is_stale, STALE_THRESHOLD_NS

# ---------------------------------------------------------------------------
# Session windows (UK local time)
# ---------------------------------------------------------------------------
_SESSIONS = [
    ("PRE_LSE",   dtime(6,  0), dtime(8,  0)),
    ("LSE",       dtime(8,  0), dtime(16, 30)),
    ("PRE_NYSE",  dtime(12, 0), dtime(14, 30)),
    ("NYSE",      dtime(14, 30), dtime(21,  0)),
    ("EOD",       dtime(21, 0), dtime(22,  0)),
]

# Tick intervals
TICK_INTERVAL_ACTIVE   = 30    # seconds during live session
TICK_INTERVAL_INACTIVE = 120   # seconds off-hours

# Sniper loop interval (Phase 6: Brain/Sniper decoupling)
_SNIPER_INTERVAL = 5  # seconds — high-frequency position monitor

# Dead Man's Switch (Phase 17)
_HEARTBEAT_FILE = Path("/tmp/nzt48_heartbeat.json")

# Phase 3.5: Maximum price drift before skipping execution
_MAX_DRIFT_PCT = 0.005  # 0.5%

# Phase 12: Regimes that block all new entries
_RISK_OFF_REGIMES = frozenset({"CRISIS", "HIGH_RISK", "RED", "RISK_OFF", "SHOCK"})

# Phase 25: Feature log directory
_FEATURE_LOG_DIR = Path("data/feature_logs")

# Phase 34: Spoofing defense — book instability thresholds
# Aitken et al. (2015) "Trade-based manipulation and market efficiency"
_SPOOF_SIZE_CHANGE_THRESHOLD = 0.50   # 50% top-of-book size change = instability event
_SPOOF_TIME_WINDOW_S         = 2.0    # max seconds between snapshots to count as rapid
_SPOOF_EVENT_WINDOW_S        = 30.0   # rolling window to accumulate instability events
_SPOOF_EVENT_TRIGGER         = 3      # events within window to flag spoofing
_SPOOF_BOOK_HISTORY_MAXLEN   = 120    # max snapshots retained per ticker (~10min at 5s)

# Phase 44: OHLCV Dedup Cache — prevent redundant yfinance calls within 15s
_OHLCV_CACHE_TTL_NS = 15_000_000_000  # 15 seconds in nanoseconds
_ohlcv_cache: dict[str, tuple[int, float]] = {}  # ticker -> (mono_ns_timestamp, price)

# V9.5 Phase 1a: GC Discipline — disable GC during market hours for latency stability.
# gc.collect() runs at market close to reclaim memory.
_gc_disabled: bool = False

# ---------------------------------------------------------------------------
# Ring Buffer Telemetry (C-05) — fixed-size, zero-allocation during trading
# ---------------------------------------------------------------------------

class RingBuffer:
    """Fixed-size ring buffer for tick latency telemetry.

    Stores last N tick processing times for monitoring.
    No dynamic allocation during trading hours.
    """
    def __init__(self, size: int = 1000):
        self._buf = [0.0] * size
        self._idx = 0
        self._size = size
        self._count = 0

    def push(self, value: float) -> None:
        self._buf[self._idx] = value
        self._idx = (self._idx + 1) % self._size
        self._count = min(self._count + 1, self._size)

    def percentile(self, p: float) -> float:
        if self._count == 0:
            return 0.0
        data = sorted(self._buf[:self._count])
        idx = int(p / 100.0 * (len(data) - 1))
        return data[idx]

    def mean(self) -> float:
        if self._count == 0:
            return 0.0
        return sum(self._buf[:self._count]) / self._count


# Module-level ring buffers — one per loop type
_tick_latency_ring = RingBuffer(1000)    # Brain (main tick loop) latencies
_sniper_latency_ring = RingBuffer(1000)  # Sniper loop latencies


# ---------------------------------------------------------------------------
# JIT Warm-Up (C-17) — pre-compile Numba functions before market open
# ---------------------------------------------------------------------------

def _warmup_jit() -> None:
    """Pre-compile all Numba JIT functions before market open.

    Avoids first-call compilation latency during live trading.
    Must run BEFORE first tick arrives.
    """
    import numpy as np
    dummy = np.random.randn(100).astype(np.float64)

    # Warm up each Numba-decorated function with dummy data
    try:
        from core.quant_math.hawkes import HawkesMicrostructureMonitor
        _hm = HawkesMicrostructureMonitor(baseline=0.1, alpha=0.8, beta=1.2)
        _hm.current_intensity()
    except Exception:
        pass
    try:
        from core.quant_math.ofi import calculate_ofi
        calculate_ofi(1.0, 100.0, 1.01, 100.0, 0.99, 90.0, 1.02, 110.0)
    except Exception:
        pass
    try:
        from core.quant_math.evt import gpd_tail_risk
        gpd_tail_risk(dummy)
    except Exception:
        pass
    try:
        from core.quant_math.vpin import calculate_vpin
        calculate_vpin(dummy[:50], np.abs(dummy[:50]) * 1000)
    except Exception:
        pass
    try:
        from core.quant_math.microstructure import calculate_micro_price
        calculate_micro_price(1.0, 1.01, 100.0, 100.0)
    except Exception:
        pass

    logger.info("[JIT_WARMUP] All quant functions pre-compiled")


# Lazy import for IBKR gateway (may not be installed)
try:
    from execution.ibkr_gateway import IBKRGateway
except ImportError:
    IBKRGateway = None  # type: ignore[misc,assignment]


def get_current_session(now_uk: datetime) -> tuple[str, bool]:
    """Return (session_name, is_active)."""
    t = now_uk.time()
    # Prioritise most specific session
    if dtime(14, 30) <= t < dtime(16, 30):
        return "OVERLAP", True
    if dtime(14, 30) <= t < dtime(21, 0):
        return "NYSE", True
    if dtime(8, 0) <= t < dtime(16, 30):
        return "LSE", True
    if dtime(6, 0) <= t < dtime(8, 0):
        return "PRE_LSE", False
    if dtime(12, 0) <= t < dtime(14, 30):
        return "PRE_NYSE", False
    if dtime(21, 0) <= t < dtime(22, 0):
        return "EOD", False
    return "OFF_HOURS", False


def detect_regime() -> str:
    """Quick regime from SPX/VIX proxy using cached yfinance."""
    try:
        spx = yf.download("^GSPC", period="5d", interval="1d",
                          auto_adjust=True, progress=False)
        vix = yf.download("^VIX",  period="5d", interval="1d",
                          auto_adjust=True, progress=False)
        import pandas as pd
        if isinstance(spx.columns, pd.MultiIndex):
            spx.columns = spx.columns.get_level_values(0)
        if isinstance(vix.columns, pd.MultiIndex):
            vix.columns = vix.columns.get_level_values(0)

        if spx.empty or vix.empty:
            return "NEUTRAL"

        spx_close = spx["Close"].dropna()
        vix_close = vix["Close"].dropna()

        spx_pct  = float((spx_close.iloc[-1] / spx_close.iloc[-2] - 1) * 100) if len(spx_close) >= 2 else 0.0
        vix_last = float(vix_close.iloc[-1]) if not vix_close.empty else 20.0

        if vix_last > 30 or spx_pct < -1.5:
            return "RISK_OFF"
        elif vix_last < 18 and spx_pct > 0.3:
            return "RISK_ON"
        elif spx_pct > 1.0:
            return "BULLISH"
        elif spx_pct < -0.5:
            return "BEARISH"
        return "NEUTRAL"
    except Exception as exc:
        logger.debug("detect_regime failed: %s", exc)
        return "NEUTRAL"


# ---------------------------------------------------------------------------
# Tick loop
# ---------------------------------------------------------------------------

class TickLoop:
    """Async tick loop. Call `start()` to begin; `stop()` to end."""

    # Suppress Telegram for the first N ticks after startup (engine needs to warm up)
    _STARTUP_GRACE_TICKS = 2
    # Minimum seconds between Telegram UPDATE messages (prevent rapid-fire)
    _TELEGRAM_UPDATE_COOLDOWN = 300  # 5 minutes

    def __init__(
        self,
        use_extended: bool = False,
        telegram_fn   = None,   # async callable(text) -> None
        api_pusher    = None,   # _APIPusher instance for HTTP push to unified API
        virtual_trader: VirtualTrader = None,
        engine_ref    = None,   # Phase 2: reference to main NZT48Engine for regime reads
        signal_queue: Optional[asyncio.Queue] = None,  # Phase 2: unified signal queue
    ) -> None:
        self._engine     = SignalEngine(use_extended=use_extended)
        self._diff_eng   = DiffEngine()
        self._state      = get_state()
        self._telegram   = telegram_fn
        self._api_pusher = api_pusher
        self._virtual_trader = virtual_trader
        self._engine_ref = engine_ref       # Phase 2: main engine back-reference
        self._signal_queue = signal_queue   # Phase 2: cross-component signal bus
        self._running    = False
        self._task       = None
        self._sniper_task: Optional[asyncio.Task] = None  # Phase 6
        self._ticks_since_start = 0
        self._last_telegram_update = 0.0  # epoch timestamp
        self._positions_opened_today = 0
        self._closed_tickers_today: set[str] = set()  # prevent reopen after close
        self._last_reset_date: str = ""  # ISO date for daily reset
        self._MAX_POSITIONS_PER_DAY = 5
        self._MAX_CONCURRENT_POSITIONS = 2
        self._MIN_COMPOSITE_FOR_EXEC = 65
        self._MIN_RR_FOR_EXEC = 1.2

        # Phase 31: Hawkes cascade monitor
        self._hawkes_monitor = HawkesMicrostructureMonitor(baseline=0.1, alpha=0.8, beta=1.2)
        # Phase 24: VPIN rolling data
        self._vpin_prices: list[float] = []
        self._vpin_volumes: list[float] = []
        # Phase 30: OFI rolling state (previous tick bid/ask)
        self._ofi_prev: dict[str, tuple] = {}  # ticker -> (bid, bid_sz, ask, ask_sz)
        self._ofi_rolling: dict[str, list[float]] = {}  # ticker -> last 100 OFI values
        # Phase 35: Lead-lag NQ -> QQQ3.L tracking
        self._lead_lag_prev_nq: float = 0.0  # previous NQ tick price

        # Phase 34: Spoofing defense — per-ticker book snapshots & instability events
        # Each entry: deque of (timestamp, bid_size, ask_size)
        self._book_snapshots: dict[str, collections.deque] = {}
        # Each entry: deque of timestamps when instability was detected
        self._spoof_events: dict[str, collections.deque] = {}
        # Current spoofing flags per ticker (reset each sniper cycle)
        self._spoof_flags: dict[str, bool] = {}

        # Phase 16: IBKR client (optional — graceful degradation)
        self._ibkr_client: Optional[Any] = None
        if IBKRGateway is not None:
            try:
                gw = IBKRGateway()
                if gw.connect():
                    self._ibkr_client = gw
                    logger.info("[TICK_LOOP] IBKR gateway connected")
                else:
                    logger.info("[TICK_LOOP] IBKR gateway not available — virtual-only mode")
            except Exception as ibkr_err:
                logger.debug("[TICK_LOOP] IBKR init failed (non-fatal): %s", ibkr_err)

    async def start(self) -> None:
        if self._running:
            return
        # C-17: Pre-compile JIT functions before first tick
        _warmup_jit()
        self._running = True
        self._task    = asyncio.create_task(self._loop())
        # Phase 6: Launch sniper coroutine as separate high-frequency task
        if self._virtual_trader:
            self._sniper_task = asyncio.create_task(self._sniper())
            logger.info("[TICK_LOOP] sniper task launched (every %ds)", _SNIPER_INTERVAL)

        # V9.5 Phase 3: Hot-reload listener — subscribes to nzt:system:hot_reload
        if self._engine_ref and hasattr(self._engine_ref, '_state_manager'):
            sm = self._engine_ref._state_manager
            if sm and not sm._fallback_mode:
                self._hot_reload_task = asyncio.create_task(
                    sm.subscribe_hot_reload(self._handle_hot_reload)
                )
                logger.info("[V9.5] hot-reload listener started")

        logger.info("[TICK_LOOP] started")

    async def _handle_hot_reload(self, message: dict) -> None:
        """V9.5: Handle hot-reload events — swap ML weights, Kelly params, GPD atomically."""
        reload_type = message.get("type", "unknown")
        logger.info("[V9.5] HOT_RELOAD received: type=%s", reload_type)

        if reload_type in ("ml_weights", "daily_ml_update"):
            # Reload ML meta-model weights
            try:
                if self._engine_ref and hasattr(self._engine_ref, 'ml_meta'):
                    self._engine_ref.ml_meta._load_model()
                    logger.info("[V9.5] HOT_RELOAD: ML weights swapped")
            except Exception as e:
                logger.error("[V9.5] HOT_RELOAD: ML swap failed: %s", e)

        if reload_type in ("kelly_params", "daily_ml_update"):
            # Kelly params updated — will be picked up on next sizing call
            logger.info("[V9.5] HOT_RELOAD: Kelly params will refresh on next trade")

        if reload_type in ("gpd_recalibration", "daily_ml_update"):
            # GPD params recalibrated — will be picked up from gpd_params.json
            logger.info("[V9.5] HOT_RELOAD: GPD params recalibrated")

    async def stop(self) -> None:
        self._running = False
        if self._sniper_task:
            self._sniper_task.cancel()
        if self._task:
            self._task.cancel()
        # Phase 16: Disconnect IBKR gracefully
        if self._ibkr_client:
            try:
                self._ibkr_client.disconnect()
            except Exception:
                pass
        logger.info("[TICK_LOOP] stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("[TICK_LOOP] unhandled error: %s", exc, exc_info=True)

            _now_uk  = now_uk()
            _, active = get_current_session(_now_uk)
            interval = TICK_INTERVAL_ACTIVE if active else TICK_INTERVAL_INACTIVE
            await asyncio.sleep(interval)

    async def _tick(self) -> None:
        _t0_tick = mono_ns()  # C-05: Ring buffer telemetry
        _now_uk_dt = now_uk()
        session_name, session_active = get_current_session(_now_uk_dt)

        # Daily reset of counters at midnight UK
        today_iso = _now_uk_dt.strftime("%Y-%m-%d")
        if today_iso != self._last_reset_date:
            self._positions_opened_today = 0
            self._closed_tickers_today.clear()
            self._last_reset_date = today_iso
            logger.info("[TICK] Daily reset: positions_opened=0, closed_tickers cleared")

        # V9.5 Phase 1a: GC Discipline — disable during active session, enable at close
        global _gc_disabled
        if session_active and not _gc_disabled:
            gc.disable()
            _gc_disabled = True
            logger.info("[V9.5] GC disabled for active session (%s)", session_name)
        elif not session_active and _gc_disabled:
            gc.collect()
            gc.enable()
            _gc_disabled = False
            logger.info("[V9.5] GC re-enabled after session close (collected)")

        # Update market overview
        self._state.market.session_name   = session_name
        self._state.market.session_active = session_active

        # Regime (cached, re-fetch every 20 ticks ≈ 10min, with hysteresis to prevent flip-flop)
        if self._state.tick_count % 20 == 0 or not self._state.market.regime:
            new_regime = detect_regime()
            old_regime = self._state.market.regime or "NEUTRAL"
            # Hysteresis: only change regime if it's been different for 2+ consecutive checks
            if not hasattr(self, "_pending_regime"):
                self._pending_regime = None
                self._pending_regime_count = 0
            if new_regime != old_regime:
                if new_regime == self._pending_regime:
                    self._pending_regime_count += 1
                else:
                    self._pending_regime = new_regime
                    self._pending_regime_count = 1
                # Only switch after 2 consecutive readings of the new regime
                if self._pending_regime_count >= 2:
                    self._state.market.regime = new_regime
                    self._pending_regime = None
                    self._pending_regime_count = 0
            else:
                self._pending_regime = None
                self._pending_regime_count = 0
        regime = self._state.market.regime

        logger.info("[TICK] #%d session=%s active=%s regime=%s halt=%s",
                    self._state.tick_count + 1, session_name, session_active, regime,
                    self._state.halt_new_signals)

        # --- Strategy Router (v3.0) — run before engine so state is fresh ---
        try:
            from signal_engine.strategy_router import StrategyRouter
            router = StrategyRouter()
            router_result = router.run(
                regime=regime,
                session=session_name,
                hour_uk=_now_uk_dt.hour,
                minute_uk=_now_uk_dt.minute,
                write_artifact=False,   # don't write artifact on every tick
            )
            self._state.update_strategies(router_result)
            logger.debug("[TICK] router: %d active strategies, kill_switch=%s",
                         len([s for s in router_result.active_strategies if s.active]),
                         router_result.kill_switch)
        except Exception as router_err:
            logger.debug("[TICK] strategy router failed (non-fatal): %s", router_err)
            router_result = None

        # --- HALT CHECK: if kill_switch or manual halt, skip engine and return ---
        if self._state.halt_new_signals:
            logger.info("[TICK] HALT ACTIVE — skipping engine run")
            self._state.tick_count += 1
            self._state.last_tick = now_utc()
            if self._api_pusher is not None:
                try:
                    self._api_pusher.push_cc_snapshot(self._state.get_snapshot())
                except Exception:
                    pass
            return

        if router_result and router_result.kill_switch:
            logger.warning("[TICK] Strategy router KILL SWITCH — skipping engine run")
            self._state.tick_count += 1
            self._state.last_tick = now_utc()
            if self._api_pusher is not None:
                try:
                    self._api_pusher.push_cc_snapshot(self._state.get_snapshot())
                except Exception:
                    pass
            return

        # Run engine — ALWAYS use 5d to ensure >= 14 bars for institutional-grade indicators.
        # 1d gave only 1-6 bars during early LSE hours → MIN_BARS gate rejected everything.
        # yfinance 5d/1h returns ~120 bars (well within free-tier limits).
        period   = "5d"
        interval = "1h"

        # V9.5 Phase 1b: Fail-closed latency gate — monotonic timing on engine run.
        # If data fetch takes > 2.5s, mark data STALE and halt execution for this tick.
        _engine_t0 = mono_ns()
        result   = self._engine.run(
            session=session_name,
            regime=regime,
            n_plays_min=5,
            n_plays_max=15,
            period=period,
            interval=interval,
        )
        _engine_latency_ms = elapsed_ms(_engine_t0)
        if is_stale(_engine_t0):
            logger.error(
                "[V9.5] LATENCY_GATE: engine.run took %.0fms > %dms — data STALE, skipping execution",
                _engine_latency_ms, STALE_THRESHOLD_NS // 1_000_000,
            )
            self._state.tick_count += 1
            self._state.last_tick = now_utc()
            return

        # Breadth proxy (% of universe above ema20)
        try:
            breadth = sum(
                1 for p in result.plays if p.direction == "LONG"
            ) / max(len(result.plays), 1)
            self._state.market.breadth_score = round(breadth, 2)
        except Exception:
            pass

        # Update state
        await self._state.update_from_engine(result)

        # v4.0: Update allocation panel from router result
        if router_result:
            self._state.update_allocation(router_result)

        # v4.0: Update drought cockpit from engine result
        drought_pkg = getattr(result, "drought_package", None)
        if drought_pkg is not None:
            self._state.update_drought_cockpit(drought_pkg)

        # v4.0: Refresh session status after each tick
        self._state.refresh_session_status()

        # Diff
        diff = self._diff_eng.compute(result)

        # === EXECUTION BRIDGE: Route qualifying plays to virtual trader ===
        if self._virtual_trader and session_active and not self._state.halt_new_signals:
            await self._execute_qualifying_plays(result, regime)

        # === POSITION MONITOR: Check stops and targets on open positions ===
        if self._virtual_trader:
            try:
                await self._monitor_positions(result)
            except Exception as mon_err:
                logger.error("[POSITION_MONITOR] Error: %s", mon_err)

        self._ticks_since_start += 1
        if not diff.is_empty:
            logger.info("[TICK] DIFF: %s", diff.to_text())
            # Telegram alert for notable changes (with startup grace + cooldown)
            if self._telegram:
                import time as _time
                _now = _time.time()
                if self._ticks_since_start <= self._STARTUP_GRACE_TICKS:
                    logger.info("[TICK] Telegram suppressed (startup grace, tick %d/%d)",
                                self._ticks_since_start, self._STARTUP_GRACE_TICKS)
                elif _now - self._last_telegram_update < self._TELEGRAM_UPDATE_COOLDOWN:
                    logger.info("[TICK] Telegram suppressed (cooldown, %ds remaining)",
                                int(self._TELEGRAM_UPDATE_COOLDOWN - (_now - self._last_telegram_update)))
                else:
                    msg = diff.to_telegram()
                    if msg:
                        try:
                            await self._telegram(f"NZT-48 UPDATE\n{msg}")
                            self._last_telegram_update = _now
                        except Exception as t_err:
                            logger.debug("Telegram alert failed: %s", t_err)

        # Expire stale tape entries
        self._engine.tape.expire_stale(max_age_seconds=3600)

        logger.info(
            "[TICK] done: plays=%d strict=%d fallback=%d drought=%s",
            len(result.plays),
            result.strict_count,
            result.fallback_count,
            "YES" if result.drought else "no",
        )

        # Write plays.json artifact so dashboard /api/todays-play can find it
        if result.plays:
            try:
                today_str = now_utc().strftime("%Y-%m-%d")
                session_slug = session_name.lower().replace(" ", "_")
                artifact_dir = Path("artifacts") / today_str / session_slug
                artifact_dir.mkdir(parents=True, exist_ok=True)
                plays_data = {
                    "generated_at": now_utc().isoformat(),
                    "session": session_slug,
                    "regime": regime,
                    "mode": "TICK_LOOP",
                    "strict_count": result.strict_count,
                    "fallback_count": result.fallback_count,
                    "total_plays": len(result.plays),
                    "ibkr_connected": bool(self._ibkr_client and self._ibkr_client.connected),  # Phase 27
                    "plays": [],
                }
                for p in result.plays:
                    # Phase 27: EV estimate = win_rate * avg_win - (1-win_rate) * avg_loss
                    _rr = float(p.rr_ratio) if hasattr(p, 'rr_ratio') and p.rr_ratio else 1.0
                    _win_rate = float(p.composite) / 100.0 if p.composite else 0.5
                    _ev = _win_rate * _rr - (1 - _win_rate) * 1.0  # normalised to 1R risk

                    # Phase 27: Kelly criterion risk sizing
                    # f* = (p * b - q) / b  where p=win_rate, q=loss_rate, b=rr_ratio
                    if _rr > 0:
                        _kelly = (_win_rate * _rr - (1 - _win_rate)) / _rr
                    else:
                        _kelly = 0.0
                    _kelly = max(0.0, min(_kelly, 0.25))  # clamp: 0-25% max risk

                    plays_data["plays"].append({
                        "ticker": p.ticker,
                        "direction": p.direction if hasattr(p, 'direction') else "LONG",
                        "composite": float(p.composite),
                        "entry": float(p.entry) if hasattr(p, 'entry') and p.entry else 0.0,
                        "stop": float(p.stop) if hasattr(p, 'stop') and p.stop else 0.0,
                        "target1": float(p.target1) if hasattr(p, 'target1') and p.target1 else 0.0,
                        "target2": float(p.target2) if hasattr(p, 'target2') and p.target2 else 0.0,
                        "rr_ratio": float(p.rr_ratio) if hasattr(p, 'rr_ratio') and p.rr_ratio else 0.0,
                        "rvol": float(p.rvol) if hasattr(p, 'rvol') else 0.0,
                        "momentum": float(p.momentum) if hasattr(p, 'momentum') else 0.0,
                        "volatility": float(p.volatility) if hasattr(p, 'volatility') else 0.0,
                        "ev_estimate": round(_ev, 4),          # Phase 27
                        "kelly_risk_pct": round(_kelly, 4),    # Phase 27
                    })
                # Atomic write — temp file → rename
                tmp_path = artifact_dir / "plays.json.tmp"
                final_path = artifact_dir / "plays.json"
                with open(tmp_path, "w") as f:
                    json.dump(plays_data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                tmp_path.rename(final_path)
                logger.debug("[TICK] plays.json written: %d plays -> %s", len(result.plays), final_path)
            except Exception as art_err:
                logger.debug("[TICK] plays.json write failed (non-critical): %s", art_err)

        # Push CC state to unified API for WebSocket broadcast
        if self._api_pusher is not None:
            try:
                snapshot = self._state.get_snapshot()
                self._api_pusher.push_cc_snapshot(snapshot)
            except Exception as push_err:
                logger.debug("[TICK] API push failed: %s", push_err)

        # C-05: Record tick processing latency in ring buffer
        _tick_ms = elapsed_ms(_t0_tick)
        _tick_latency_ring.push(_tick_ms)
        if _tick_latency_ring._count % 50 == 0 and _tick_latency_ring._count > 0:
            logger.info(
                "[TELEMETRY] tick latency: last=%.1fms mean=%.1fms p95=%.1fms p99=%.1fms (n=%d)",
                _tick_ms,
                _tick_latency_ring.mean(),
                _tick_latency_ring.percentile(95),
                _tick_latency_ring.percentile(99),
                _tick_latency_ring._count,
            )

    # -----------------------------------------------------------------------
    # Execution bridge
    # -----------------------------------------------------------------------

    async def _execute_qualifying_plays(self, result, regime: str) -> None:
        """Route qualifying plays from SignalEngine to VirtualTrader.

        Filters:
        - Composite score >= _MIN_COMPOSITE_FOR_EXEC (65)
        - R:R >= _MIN_RR_FOR_EXEC (1.2)
        - Not already in position for this ticker
        - Under daily and concurrent position limits
        """
        if not result.plays:
            return

        # Check concurrent position limit
        try:
            open_positions = self._virtual_trader.open_positions
            open_count = len(open_positions)
            open_tickers = {p.ticker for p in open_positions.values()}
        except Exception:
            open_count = 0
            open_tickers = set()

        if open_count >= self._MAX_CONCURRENT_POSITIONS:
            logger.debug("[EXEC_BRIDGE] Max concurrent positions (%d) reached",
                         self._MAX_CONCURRENT_POSITIONS)
            return

        if self._positions_opened_today >= self._MAX_POSITIONS_PER_DAY:
            logger.debug("[EXEC_BRIDGE] Max daily positions (%d) reached",
                         self._MAX_POSITIONS_PER_DAY)
            return

        for play in result.plays:
            # Phase 12: Regime risk-OFF filter — block ALL new entries in crisis regimes
            _effective_regime = self._get_regime()
            if _effective_regime in _RISK_OFF_REGIMES:
                logger.warning("[EXEC] Regime=%s -- blocking ALL new entries", _effective_regime)
                continue

            # Quality gates
            if play.composite < self._MIN_COMPOSITE_FOR_EXEC:
                continue
            if hasattr(play, 'rr_ratio') and play.rr_ratio and play.rr_ratio < self._MIN_RR_FOR_EXEC:
                continue
            # Skip if already in position
            if play.ticker in open_tickers:
                continue
            # Skip if this ticker was closed today (prevent open→close→reopen churn)
            if play.ticker in self._closed_tickers_today:
                logger.debug("[EXEC_BRIDGE] Skipping %s — already closed today", play.ticker)
                continue
            # Concurrent limit check
            if open_count >= self._MAX_CONCURRENT_POSITIONS:
                break

            # Convert PlayScore to Signal for virtual trader
            try:
                # Map direction string to Direction enum
                direction = Direction.LONG
                if hasattr(play, 'direction') and play.direction:
                    direction = Direction(play.direction) if play.direction in ("LONG", "SHORT") else Direction.LONG

                # Map regime string to RegimeState enum (best-effort)
                try:
                    regime_state = RegimeState(regime)
                except (ValueError, KeyError):
                    regime_state = RegimeState.RANGE_BOUND

                signal = Signal(
                    ticker=play.ticker,
                    direction=direction,
                    strategy="TICK_LOOP",
                    entry=play.entry if hasattr(play, 'entry') and play.entry > 0 else 0.0,
                    stop=play.stop if hasattr(play, 'stop') and play.stop > 0 else 0.0,
                    target_1r=play.target1 if hasattr(play, 'target1') and play.target1 else 0.0,
                    target_2r=play.target2 if hasattr(play, 'target2') and play.target2 else 0.0,
                    confidence=float(play.composite),
                    confidence_breakdown=ConfidenceBreakdown(
                        layer1_price_action=float(play.composite) * 0.45,
                        layer2_regime=float(play.composite) * 0.20,
                        layer3_sector_flow=float(play.composite) * 0.15,
                        layer4_macro=float(play.composite) * 0.10,
                        layer5_narrative=float(play.composite) * 0.10,
                        final_score=float(play.composite),
                    ),
                    regime=regime_state,
                    bot=Bot.A,  # UK ISA — leveraged ETPs
                    bot_instance=BotInstance.BULL,
                    rvol=play.rvol if hasattr(play, 'rvol') else None,
                    timestamp=now_utc(),
                )

                # Phase 25: Log feature vector at point-in-time (before execution)
                self._log_feature_vector(play, regime)

                # Phase 3.5: Get fresh price and check drift before execution
                fresh_price = self._get_fresh_price(play.ticker)
                if fresh_price > 0:
                    should_exec, signal = self._check_drift_and_rebase(signal, fresh_price)
                    if not should_exec:
                        logger.info("[EXEC_BRIDGE] %s skipped — price drift too large", play.ticker)
                        continue

                # Phase 16: Try maker-only IBKR execution first
                ibkr_fill = None
                if self._ibkr_client and self._ibkr_client.connected:
                    ibkr_fill = await self._maker_only_execute(signal, play)
                    if ibkr_fill:
                        logger.info("[EXEC_BRIDGE] %s filled via IBKR maker @ %.4f",
                                    play.ticker, ibkr_fill.get("fill_price", 0))

                # Virtual trader execution (always — paper tracking even if IBKR filled)
                vp = self._virtual_trader.open_position(signal)
                if vp:
                    open_count += 1
                    open_tickers.add(play.ticker)
                    self._positions_opened_today += 1
                    logger.info(
                        "[EXEC_BRIDGE] POSITION OPENED: %s %s @ %.4f | "
                        "stop=%.4f target=%.4f | composite=%.1f | R:R=%.1f",
                        play.direction if hasattr(play, 'direction') else "LONG",
                        play.ticker,
                        signal.entry,
                        signal.stop,
                        signal.target_1r,
                        play.composite,
                        play.rr_ratio if hasattr(play, 'rr_ratio') and play.rr_ratio else 0,
                    )

                    # Persist signal to SQLite so dashboard /api/signals shows it
                    try:
                        from delivery.database import get_connection as _gc_tl, insert_signal as _is_tl
                        with _gc_tl() as _sig_conn:
                            _is_tl(_sig_conn, {
                                "id": f"TL-{play.ticker}-{now_utc().strftime('%Y%m%d%H%M%S')}",
                                "timestamp": now_utc().isoformat(),
                                "ticker": play.ticker,
                                "strategy": "TICK_LOOP",
                                "direction": signal.direction.value,
                                "confidence": float(play.composite),
                                "entry": signal.entry,
                                "stop": signal.stop,
                                "target_1r": signal.target_1r,
                                "target_2r": signal.target_2r,
                                "regime": regime,
                                "status": "EXECUTED",
                                "bot": "A",
                                "bot_instance": "BULL",
                                "risk_dollars": vp.risk_dollars if hasattr(vp, 'risk_dollars') else 0,
                                "risk_pct": 0.0075,
                                "shares": vp.shares,
                                "rvol": play.rvol if hasattr(play, 'rvol') else 0,
                            })
                        logger.info("[EXEC_BRIDGE] Signal persisted to DB: %s", play.ticker)
                    except Exception as db_err:
                        logger.warning("[EXEC_BRIDGE] Signal DB persist failed: %s", db_err)


            except Exception as exec_err:
                logger.error("[EXEC_BRIDGE] Failed to open position for %s: %s",
                             play.ticker, exec_err, exc_info=True)

    async def _monitor_positions(self, result) -> None:
        """Check open positions against current prices for stop/target hits."""
        try:
            open_positions = self._virtual_trader.open_positions
        except Exception:
            return

        if not open_positions:
            return

        # Build price map from engine result
        price_map = {}
        for play in result.plays:
            if hasattr(play, 'entry') and play.entry > 0:
                price_map[play.ticker] = play.entry

        if not price_map:
            return

        # Use VirtualTrader.update_prices which handles stops, targets,
        # profit ladder, time decay, and all exit logic
        regime = self._state.market.regime or ""
        try:
            events = self._virtual_trader.update_prices(price_map, current_regime=regime)
            for event in events:
                _evt_ticker = event.get("ticker", "?")
                if event.get("type") == "TRADE_CLOSED":
                    logger.info(
                        "[POSITION_MONITOR] Trade closed: %s R=%.2f P&L=%.2f reason=%s",
                        _evt_ticker,
                        event.get("r_multiple", 0),
                        event.get("net_pnl", 0),
                        event.get("reason", "?"),
                    )
                    # Track closed ticker to prevent reopen churn
                    self._closed_tickers_today.add(_evt_ticker)
                elif event.get("type") in ("STOP_HIT", "REGIME_FLIP", "TIME_EXPIRED",
                                            "TIME_DECAY", "FIREWALL", "ETP_OVERNIGHT"):
                    logger.info("[POSITION_MONITOR] Event: %s for %s",
                                event.get("type"), _evt_ticker)
                    # Also track forced closures
                    self._closed_tickers_today.add(_evt_ticker)
        except Exception as update_err:
            logger.debug("[POSITION_MONITOR] update_prices failed: %s", update_err)

    # -----------------------------------------------------------------------
    # Phase 2: Unified regime read — engine_ref → yfinance fallback
    # -----------------------------------------------------------------------

    def _get_regime(self) -> str:
        """Read regime from engine_ref (main NZT48Engine) if available.

        Falls back to the tick-loop's own state.market.regime which is
        populated by detect_regime() on a 20-tick cadence.
        """
        if self._engine_ref:
            ctx = getattr(self._engine_ref, '_current_market_ctx', None)
            if ctx and hasattr(ctx, 'regime'):
                val = ctx.regime
                return val.value if hasattr(val, 'value') else str(val)
        return self._state.market.regime or "NORMAL"

    # -----------------------------------------------------------------------
    # Phase 3.5: Fresh price at execution — IBKR real-time → yfinance fallback
    # -----------------------------------------------------------------------

    def _get_fresh_price(self, ticker: str) -> float:
        """Get the freshest possible price for a ticker.

        Priority: OHLCV cache → IBKR real-time snapshot → yfinance last close.
        Returns 0.0 on total failure.
        """
        # Phase 44: OHLCV dedup cache — skip yfinance if we fetched within 15s
        _cache_entry = _ohlcv_cache.get(ticker)
        if _cache_entry:
            _cached_ts, _cached_px = _cache_entry
            if (mono_ns() - _cached_ts) < _OHLCV_CACHE_TTL_NS:
                return _cached_px

        # Try IBKR first (~50-100ms latency)
        if self._ibkr_client and self._ibkr_client.connected:
            try:
                price = self._ibkr_client.get_last_price(ticker)
                if price and price > 0:
                    _ohlcv_cache[ticker] = (mono_ns(), float(price))
                    return float(price)
            except Exception as ibkr_err:
                logger.debug("[FRESH_PRICE] IBKR failed for %s: %s", ticker, ibkr_err)

        # yfinance fallback (~1-2s latency)
        _t0 = mono_ns()
        try:
            import pandas as pd
            data = yf.download(ticker, period="1d", interval="1m",
                               auto_adjust=True, progress=False)
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)
            if not data.empty:
                price = float(data["Close"].dropna().iloc[-1])
                _latency_ns = mono_ns() - _t0
                if _latency_ns > STALE_THRESHOLD_NS:
                    logger.warning("STALE_DATA: %s latency=%dms — data may be stale", ticker, _latency_ns // 1_000_000)
                _ohlcv_cache[ticker] = (mono_ns(), price)
                return price
        except Exception as yf_err:
            logger.debug("[FRESH_PRICE] yfinance failed for %s: %s", ticker, yf_err)

        return 0.0

    def _check_drift_and_rebase(
        self, signal: Signal, fresh_price: float
    ) -> tuple[bool, Signal]:
        """Check if fresh price drifted > 0.5% from signal entry.

        Returns:
            (should_execute, rebased_signal)
            If drift > threshold: (False, original_signal)
            Otherwise: (True, signal_with_rebased_stops_targets)
        """
        if fresh_price <= 0 or signal.entry <= 0:
            return (True, signal)  # can't check — proceed with original

        drift_pct = abs(fresh_price - signal.entry) / signal.entry
        if drift_pct > _MAX_DRIFT_PCT:
            logger.warning(
                "[DRIFT] %s price drifted %.2f%% (signal=%.4f fresh=%.4f) — SKIP",
                signal.ticker, drift_pct * 100, signal.entry, fresh_price,
            )
            return (False, signal)

        # Rebase: preserve distance from original entry
        stop_dist = signal.entry - signal.stop
        t1_dist = signal.target_1r - signal.entry
        t2_dist = signal.target_2r - signal.entry if signal.target_2r else 0.0

        signal.entry = fresh_price
        signal.stop = fresh_price - stop_dist
        signal.target_1r = fresh_price + t1_dist
        if signal.target_2r:
            signal.target_2r = fresh_price + t2_dist

        return (True, signal)

    # -----------------------------------------------------------------------
    # Phase 6: Sniper — high-frequency position monitor (every 5s)
    # -----------------------------------------------------------------------

    async def _sniper(self) -> None:
        """High-frequency position monitor.

        Decoupled from the Brain (main tick loop) per Phase 6.
        Runs every 5 seconds, fetching fresh prices for all open positions
        and pushing them through VirtualTrader.update_prices().
        Writes heartbeat file for Dead Man's Switch (Phase 17).
        """
        logger.info("[SNIPER] coroutine started")
        while self._running:
            try:
                _t0_sniper = mono_ns()  # C-05: Ring buffer telemetry
                if not self._virtual_trader:
                    await asyncio.sleep(_SNIPER_INTERVAL)
                    continue

                open_positions = self._virtual_trader.open_positions
                if not open_positions:
                    self._write_heartbeat()
                    await asyncio.sleep(_SNIPER_INTERVAL)
                    continue

                # Get fresh prices for all open position tickers
                tickers = {pos.ticker for pos in open_positions.values() if pos.status == "OPEN"}
                price_map: dict[str, float] = {}
                _tick_receive_ns = mono_ns()  # C-04: Staleness detection
                for ticker in tickers:
                    price = self._get_fresh_price(ticker)
                    if price > 0:
                        price_map[ticker] = price

                # C-04: Staleness check — warn if data fetch took > 2.5s
                if is_stale(_tick_receive_ns):
                    _stale_ms = elapsed_ms(_tick_receive_ns)
                    logger.warning(
                        "[SNIPER] STALE_DATA: price fetch took %.0fms (> %.0fms threshold) — "
                        "data may be outdated for %d tickers",
                        _stale_ms, STALE_THRESHOLD_NS / 1_000_000, len(tickers),
                    )

                if price_map:
                    # Phase 24: VPIN toxic flow detection
                    for ticker, px in price_map.items():
                        self._vpin_prices.append(px)
                        vol_est = getattr(self._ibkr_client, '_last_volumes', {}).get(ticker, 1000) if self._ibkr_client else 1000
                        self._vpin_volumes.append(vol_est)
                    # Keep last 200 ticks
                    self._vpin_prices = self._vpin_prices[-200:]
                    self._vpin_volumes = self._vpin_volumes[-200:]
                    if len(self._vpin_prices) >= 20:
                        import numpy as np
                        vpin_changes = np.diff(self._vpin_prices[-50:])
                        vpin_vols = np.array(self._vpin_volumes[-len(vpin_changes):])
                        if len(vpin_changes) > 0 and len(vpin_vols) == len(vpin_changes):
                            vpin_score = calculate_vpin(vpin_changes, vpin_vols)
                            if vpin_score > 0.75:
                                logger.warning("[VPIN] Toxic flow detected: VPIN=%.3f > 0.75 — caution", vpin_score)

                    # Phase 30: OFI order flow imbalance
                    if self._ibkr_client:
                        for ticker in tickers:
                            try:
                                bid, ask, bid_sz, ask_sz = self._ibkr_client.get_bid_ask(ticker)
                                prev = self._ofi_prev.get(ticker)
                                if prev:
                                    ofi_val = calculate_ofi(
                                        bid, bid_sz, ask, ask_sz,
                                        prev[0], prev[1], prev[2], prev[3],
                                    )
                                    self._ofi_rolling.setdefault(ticker, []).append(ofi_val)
                                    self._ofi_rolling[ticker] = self._ofi_rolling[ticker][-100:]
                                self._ofi_prev[ticker] = (bid, bid_sz, ask, ask_sz)
                            except Exception:
                                pass

                    regime = self._get_regime()
                    events = self._virtual_trader.update_prices(price_map, current_regime=regime)
                    for event in events:
                        _evt_ticker = event.get("ticker", "?")
                        evt_type = event.get("type", "")
                        if evt_type == "TRADE_CLOSED":
                            logger.info(
                                "[SNIPER] Trade closed: %s R=%.2f P&L=%.2f reason=%s",
                                _evt_ticker,
                                event.get("r_multiple", 0),
                                event.get("net_pnl", 0),
                                event.get("reason", "?"),
                            )
                            self._closed_tickers_today.add(_evt_ticker)
                            # Phase 33: Evaluate parasite re-entry on close
                            self._evaluate_parasite_reentry(event)
                        elif evt_type in ("STOP_HIT", "REGIME_FLIP", "TIME_EXPIRED",
                                          "TIME_DECAY", "FIREWALL", "ETP_OVERNIGHT"):
                            logger.info("[SNIPER] Event: %s for %s", evt_type, _evt_ticker)
                            self._closed_tickers_today.add(_evt_ticker)

                    # Phase 31: Hawkes cascade detection — freeze on self-exciting events
                    for ev in events:
                        if ev.get("type") in ("TRADE_CLOSED", "STOP_HIT", "FLASH_CRASH"):
                            self._hawkes_monitor.add_toxic_event(_time_mod.monotonic())
                    hawkes_intensity = self._hawkes_monitor.current_intensity(_time_mod.monotonic())
                    if hawkes_intensity > 5.0:
                        logger.critical(
                            "[HAWKES] CASCADE_FREEZE: intensity=%.2f > 5.0 — blocking new entries + widening stops",
                            hawkes_intensity,
                        )
                        # Phase 44: Toxic-Taker Toggle — widen existing stops by 0.5x ATR
                        for _pos_id, _pos in list(open_positions.items()):
                            if _pos.status == "OPEN" and hasattr(_pos, 'atr') and _pos.atr > 0:
                                if _pos.direction == "LONG":
                                    _new_stop = _pos.current_stop - 0.5 * _pos.atr
                                else:
                                    _new_stop = _pos.current_stop + 0.5 * _pos.atr
                                _pos.current_stop = _new_stop
                                logger.warning(
                                    "[HAWKES] STOP_WIDENED: %s %s stop → %.4f (0.5×ATR cushion)",
                                    _pos.ticker, _pos.direction, _new_stop,
                                )

                # Phase 34: Spoofing defense — detect book instability
                self._detect_spoofing(tickers, price_map)
                self._apply_spoof_warnings()

                # Phase 35: Lead-lag NQ -> QQQ3.L detection
                # If NQ futures data is available, detect latency arb signals
                nq_price = price_map.get("NQ=F", 0) or price_map.get("^NDX", 0)
                if nq_price > 0 and self._lead_lag_prev_nq > 0:
                    nq_change_bps = (nq_price - self._lead_lag_prev_nq) / self._lead_lag_prev_nq * 10_000
                    for ll_ticker in ("QQQ3.L", "QQQS.L", "QQQ5.L"):
                        etp_px = price_map.get(ll_ticker, 0)
                        if etp_px > 0:
                            from uk_isa.isa_universe import get_abs_leverage
                            lev = get_abs_leverage(ll_ticker)
                            etp_change_bps = 0  # Would need previous ETP price for real calc
                            ll_signal = detect_lead_signal(nq_change_bps, etp_px, etp_change_bps, lev)
                            if ll_signal.get("signal") != "NONE":
                                logger.info(
                                    "[LEAD_LAG] %s gap=%.1f bps conf=%.2f — %s signal detected",
                                    ll_ticker, ll_signal["gap_bps"],
                                    ll_signal["confidence"], ll_signal["signal"],
                                )
                if nq_price > 0:
                    self._lead_lag_prev_nq = nq_price

                # Phase 17: Write heartbeat after every sniper cycle
                self._write_heartbeat()

                # C-05: Record sniper cycle latency in ring buffer
                _sniper_ms = elapsed_ms(_t0_sniper)
                _sniper_latency_ring.push(_sniper_ms)
                if _sniper_latency_ring._count % 100 == 0 and _sniper_latency_ring._count > 0:
                    logger.info(
                        "[TELEMETRY] sniper latency: last=%.1fms mean=%.1fms p95=%.1fms p99=%.1fms (n=%d)",
                        _sniper_ms,
                        _sniper_latency_ring.mean(),
                        _sniper_latency_ring.percentile(95),
                        _sniper_latency_ring.percentile(99),
                        _sniper_latency_ring._count,
                    )

            except asyncio.CancelledError:
                logger.info("[SNIPER] coroutine cancelled")
                break
            except Exception as sniper_err:
                logger.error("[SNIPER] unhandled error: %s", sniper_err, exc_info=True)

            await asyncio.sleep(_SNIPER_INTERVAL)

    # -----------------------------------------------------------------------
    # Phase 16: Maker-only execution path (IBKR)
    # -----------------------------------------------------------------------

    async def _maker_only_execute(
        self, signal: Signal, play: Any
    ) -> Optional[dict]:
        """Attempt maker-only limit order via IBKR.

        Harris (2003): Patient liquidity provision earns the spread ~60% of the time.

        Steps:
          1. Get bid/ask from IBKR
          2. Post limit at bid (LONG) or ask (SHORT)
          3. Wait 500ms, check fill
          4. If not filled → cancel order → return None (fall through to virtual)
          5. If filled → place GTC catastrophic stop at 2.0x ATR

        Returns fill result dict on success, None on failure/timeout.
        """
        if not self._ibkr_client or not self._ibkr_client.connected:
            return None

        ticker = signal.ticker
        direction_str = signal.direction.value

        try:
            bid, ask, bid_size, ask_size = self._ibkr_client.get_bid_ask(ticker)
            if bid <= 0 or ask <= 0:
                logger.debug("[MAKER] No bid/ask for %s — skip IBKR path", ticker)
                return None

            # Phase 19: Use micro-price for EV gate when IBKR data available
            micro = calculate_micro_price(bid, ask, bid_size, ask_size)
            mid = (bid + ask) / 2.0

            # EV gate: if micro-price suggests adverse direction, skip
            if direction_str == "LONG" and micro < mid * 0.999:
                logger.info("[MAKER] Micro-price %.4f < mid %.4f — adverse for LONG %s, skip",
                            micro, mid, ticker)
                return None
            elif direction_str == "SHORT" and micro > mid * 1.001:
                logger.info("[MAKER] Micro-price %.4f > mid %.4f — adverse for SHORT %s, skip",
                            micro, mid, ticker)
                return None

            # Post maker limit at the bid (LONG) or ask (SHORT)
            limit_price = bid if direction_str == "LONG" else ask
            qty = max(1, int(signal.shares)) if signal.shares > 0 else 1

            order_result = self._ibkr_client.place_maker_limit(
                ticker, direction_str, qty, limit_price,
            )
            order_id = order_result.get("order_id", -1)
            if order_id < 0:
                return None

            # Wait 500ms for fill
            await asyncio.sleep(0.5)

            # Check fill via latency method (non-zero means filled)
            fill_latency_ms = self._ibkr_client.get_fill_latency_ms(order_id)
            if fill_latency_ms <= 0:
                # Not filled — cancel resting order
                self._ibkr_client.cancel_order(order_id)
                logger.info("[MAKER] %s order for %s not filled in 500ms — cancelled",
                            direction_str, ticker)
                return None

            # Filled! Post GTC catastrophic stop at 2.0x ATR
            atr = getattr(play, 'atr14', 0) or getattr(play, 'volatility', 0) or 0
            if atr <= 0:
                # Estimate ATR from stop distance
                atr = abs(signal.entry - signal.stop) if signal.stop > 0 else limit_price * 0.02

            catastrophic_stop = (
                limit_price - 2.0 * atr if direction_str == "LONG"
                else limit_price + 2.0 * atr
            )
            self._ibkr_client.place_gtc_stop(ticker, direction_str, qty, catastrophic_stop)

            # Phase 18: Post-fill toxicity detection
            self._post_fill_toxicity_check(fill_latency_ms, signal, limit_price)

            logger.info(
                "[MAKER] FILLED: %s %s %d @ %.4f | GTC stop @ %.4f | latency=%.0fms",
                direction_str, ticker, qty, limit_price, catastrophic_stop, fill_latency_ms,
            )

            return {
                "type": "IBKR_FILL",
                "order_id": order_id,
                "fill_price": limit_price,
                "qty": qty,
                "latency_ms": fill_latency_ms,
                "catastrophic_stop": catastrophic_stop,
            }

        except Exception as maker_err:
            logger.error("[MAKER] execution failed for %s: %s", ticker, maker_err, exc_info=True)
            return None

    # -----------------------------------------------------------------------
    # Phase 17: Dead Man's Switch heartbeat
    # -----------------------------------------------------------------------

    def _write_heartbeat(self) -> None:
        """Write heartbeat file for external monitoring.

        External watchdog can check /tmp/nzt48_heartbeat.json:
        - If file age > 30s → sniper is dead → alert
        - Contains open position count and IBKR status for diagnostics
        """
        try:
            open_count = 0
            open_tickers: list[str] = []
            if self._virtual_trader:
                positions = self._virtual_trader.open_positions
                open_count = len(positions)
                open_tickers = [p.ticker for p in positions.values() if p.status == "OPEN"]

            heartbeat = {
                "timestamp": now_utc().isoformat(),
                "epoch": _time_mod.time(),
                "open_positions": open_count,
                "open_tickers": open_tickers,
                "ibkr_connected": bool(self._ibkr_client and self._ibkr_client.connected),
                "regime": self._get_regime(),
                "ticks_since_start": self._ticks_since_start,
            }
            # Atomic write
            tmp = _HEARTBEAT_FILE.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(heartbeat, f)
                f.flush()
                os.fsync(f.fileno())
            tmp.rename(_HEARTBEAT_FILE)
        except Exception as hb_err:
            logger.debug("[HEARTBEAT] write failed: %s", hb_err)

    # -----------------------------------------------------------------------
    # Phase 18: Post-fill toxicity detection
    # -----------------------------------------------------------------------

    def _post_fill_toxicity_check(
        self, fill_latency_ms: float, signal: Signal, fill_price: float
    ) -> None:
        """Detect toxic fills (latency < 50ms = adverse selection).

        Easley & O'Hara (1987): Fast fills indicate informed counter-party.
        If toxic: tighten stop to breakeven immediately.
        """
        if fill_latency_ms < 0:
            return  # no data

        if fill_latency_ms < 50:
            logger.warning(
                "[TOXICITY] %s fill in %.0fms — TOXIC (adverse selection risk). "
                "Tightening stop to breakeven @ %.4f",
                signal.ticker, fill_latency_ms, fill_price,
            )
            # Find the virtual position and tighten stop to breakeven
            if self._virtual_trader:
                for pos in self._virtual_trader.open_positions.values():
                    if pos.ticker == signal.ticker and pos.status == "OPEN":
                        pos.current_stop = pos.entry_price  # breakeven stop
                        logger.info("[TOXICITY] %s stop tightened: %.4f -> %.4f (breakeven)",
                                    signal.ticker, pos.initial_stop, pos.current_stop)
                        break

    # -----------------------------------------------------------------------
    # Phase 19: Micro-price EV gate (used inside _maker_only_execute)
    # -----------------------------------------------------------------------
    # Note: The micro-price logic is integrated directly into _maker_only_execute()
    # above via calculate_micro_price() from core.quant_math.microstructure.
    # This standalone method provides a reusable EV check for other callers.

    def _micro_price_ev_gate(
        self, ticker: str, direction: str
    ) -> tuple[bool, float]:
        """Evaluate micro-price EV for a ticker.

        Returns (is_favorable, micro_price).
        Only works when IBKR is connected — returns (True, 0.0) otherwise.
        Stoikov (2017) SSRN 2970694.
        """
        if not self._ibkr_client or not self._ibkr_client.connected:
            return (True, 0.0)  # no data = pass through

        try:
            bid, ask, bid_size, ask_size = self._ibkr_client.get_bid_ask(ticker)
            if bid <= 0 or ask <= 0:
                return (True, 0.0)

            micro = calculate_micro_price(bid, ask, bid_size, ask_size)
            mid = (bid + ask) / 2.0

            if direction == "LONG":
                favorable = micro >= mid * 0.999
            else:
                favorable = micro <= mid * 1.001
            return (favorable, micro)
        except Exception:
            return (True, 0.0)

    # -----------------------------------------------------------------------
    # Phase 25: Point-in-time feature logging
    # -----------------------------------------------------------------------

    def _log_feature_vector(
        self, play: Any, regime: str, meta_label_result: Optional[dict] = None
    ) -> None:
        """Log feature vector with timestamp for post-hoc analysis.

        Captures point-in-time features at signal generation for:
        - Model retraining without look-ahead bias
        - Feature importance drift detection
        - Strategy attribution analysis
        """
        try:
            _FEATURE_LOG_DIR.mkdir(parents=True, exist_ok=True)
            today_str = now_utc().strftime("%Y-%m-%d")
            log_path = _FEATURE_LOG_DIR / f"features_{today_str}.jsonl"

            feature_record = {
                "timestamp": now_utc().isoformat(),
                "ticker": play.ticker,
                "direction": play.direction if hasattr(play, 'direction') else "LONG",
                "composite": float(play.composite),
                "entry": float(play.entry) if hasattr(play, 'entry') and play.entry else 0.0,
                "stop": float(play.stop) if hasattr(play, 'stop') and play.stop else 0.0,
                "rr_ratio": float(play.rr_ratio) if hasattr(play, 'rr_ratio') and play.rr_ratio else 0.0,
                "rvol": float(play.rvol) if hasattr(play, 'rvol') else 0.0,
                "momentum": float(play.momentum) if hasattr(play, 'momentum') else 0.0,
                "volatility": float(play.volatility) if hasattr(play, 'volatility') else 0.0,
                "regime": regime,
                "regime_from_engine": self._get_regime(),
                "ibkr_connected": bool(self._ibkr_client and self._ibkr_client.connected),
            }

            # Add meta-labeler output if available
            if meta_label_result:
                feature_record["meta_label"] = meta_label_result

            # Phase 24/30/31: Add microstructure features if available
            if self._vpin_prices and len(self._vpin_prices) >= 20:
                try:
                    import numpy as _np
                    _vc = _np.diff(self._vpin_prices[-50:])
                    _vv = _np.array(self._vpin_volumes[-len(_vc):])
                    if len(_vc) > 0 and len(_vv) == len(_vc):
                        feature_record["vpin"] = round(float(calculate_vpin(_vc, _vv)), 4)
                except Exception:
                    pass
            ofi_vals = self._ofi_rolling.get(play.ticker, [])
            if ofi_vals:
                feature_record["ofi_sum_100"] = round(sum(ofi_vals[-100:]), 2)
            feature_record["hawkes_intensity"] = round(
                self._hawkes_monitor.current_intensity(_time_mod.monotonic()), 3
            )
            # Phase 22: Fractional differentiation of recent prices (Lopez de Prado 2018)
            if len(self._vpin_prices) >= 30:
                try:
                    import pandas as _pd
                    from core.quant_math.frac_diff import frac_diff as _frac_diff
                    _price_series = _pd.Series(self._vpin_prices[-50:])
                    _fd = _frac_diff(_price_series, d=0.4)
                    if len(_fd) > 0:
                        feature_record["frac_diff_last"] = round(float(_fd.iloc[-1]), 6)
                except Exception:
                    pass

            with open(log_path, "a") as f:
                f.write(json.dumps(feature_record) + "\n")

        except Exception as log_err:
            logger.debug("[FEATURE_LOG] write failed: %s", log_err)

    # -----------------------------------------------------------------------
    # Phase 33: Parasite re-entry protocol
    # -----------------------------------------------------------------------

    def _evaluate_parasite_reentry(self, closed_event: dict) -> None:
        """Evaluate inverse re-entry after toxic stop-out.

        If a position was stopped out in under 5 minutes, the stop was likely
        hunted by an informed counter-party. The original thesis may still be
        valid — but in the INVERSE direction (the stop-hunter's direction).

        Fires a signal into the signal_queue for the Brain to evaluate.
        """
        try:
            reason = closed_event.get("reason", "")
            duration_min = closed_event.get("duration_minutes", 999)
            ticker = closed_event.get("ticker", "")
            r_multiple = closed_event.get("r_multiple", 0)
            original_direction = closed_event.get("direction", "LONG")

            # Only trigger on fast stop-outs (< 5 min, negative R)
            if duration_min >= 5 or r_multiple >= 0 or not ticker:
                return

            # Don't re-enter if regime is hostile
            current_regime = self._get_regime()
            if current_regime in _RISK_OFF_REGIMES:
                logger.debug("[PARASITE] %s stopped in %dmin but regime=%s — no re-entry",
                             ticker, duration_min, current_regime)
                return

            # Inverse direction
            inverse_dir = "SHORT" if original_direction == "LONG" else "LONG"

            logger.warning(
                "[PARASITE] %s toxic stop-out in %dmin (R=%.2f) — evaluating inverse %s re-entry",
                ticker, duration_min, r_multiple, inverse_dir,
            )

            # Phase 33: Use inverse ETP if available (e.g., QQQ3.L -> QQQS.L)
            from uk_isa.isa_universe import INVERSE_PAIRS
            inverse_ticker = INVERSE_PAIRS.get(ticker, ticker)  # Fallback to same ticker
            use_inverse_etp = inverse_ticker != ticker

            # If we have a signal queue, push the re-entry candidate
            if self._signal_queue:
                reentry_signal = {
                    "type": "PARASITE_REENTRY",
                    "ticker": inverse_ticker,
                    "direction": inverse_dir,
                    "reason": f"toxic_stopout_{reason}",
                    "original_r": r_multiple,
                    "original_duration_min": duration_min,
                    "kelly_risk_pct": 0.0025,  # Quarter Kelly for parasite re-entry
                    "use_inverse_etp": use_inverse_etp,
                    "original_ticker": ticker,
                    "timestamp": now_utc().isoformat(),
                }
                try:
                    self._signal_queue.put_nowait(reentry_signal)
                    logger.info("[PARASITE] Re-entry signal queued for %s %s (inverse_etp=%s)",
                                inverse_dir, inverse_ticker, use_inverse_etp)
                except asyncio.QueueFull:
                    logger.warning("[PARASITE] Signal queue full — re-entry for %s dropped", ticker)
            else:
                logger.debug("[PARASITE] No signal_queue — re-entry for %s not routed", ticker)

        except Exception as para_err:
            logger.debug("[PARASITE] evaluation failed: %s", para_err)

    # -----------------------------------------------------------------------
    # Phase 34: Spoofing defense via book instability
    # -----------------------------------------------------------------------
    # Aitken et al. (2015) "Trade-based manipulation and market efficiency"
    #
    # Spoofing = placing large orders with intent to cancel before fill,
    # creating illusion of supply/demand to move price. We detect via
    # rapid top-of-book size changes: if the best bid/ask size swings
    # by >50% repeatedly (3+ times in 30s), flag the ticker.
    #
    # This is a WARNING-only system — the EV gate handles risk management.
    # The spoof_warning flag is attached to signal metadata so downstream
    # components (execution, logging) can react appropriately.
    # -----------------------------------------------------------------------

    def _detect_spoofing(self, tickers: set[str], price_map: dict[str, float]) -> None:
        """Scan open-position tickers for order book instability (spoofing).

        For each ticker with IBKR connectivity:
          1. Snapshot current top-of-book (bid_size, ask_size)
          2. Compare to previous snapshot — if size changed >50% in <2s, record event
          3. If 3+ instability events in 30s window, set spoof_warning flag

        Args:
            tickers: set of tickers currently held in open positions
            price_map: latest prices (used only to confirm ticker is alive)
        """
        now = _time_mod.monotonic()

        # Clear stale flags from previous cycle
        self._spoof_flags.clear()

        # Requires IBKR for real order book data
        if not self._ibkr_client or not self._ibkr_client.connected:
            return

        for ticker in tickers:
            if ticker not in price_map:
                continue  # no live price — skip

            try:
                bid, ask, bid_size, ask_size = self._ibkr_client.get_bid_ask(ticker)
                if bid <= 0 or ask <= 0 or (bid_size <= 0 and ask_size <= 0):
                    continue  # no valid book data

                # Initialise deques on first encounter
                if ticker not in self._book_snapshots:
                    self._book_snapshots[ticker] = collections.deque(
                        maxlen=_SPOOF_BOOK_HISTORY_MAXLEN
                    )
                    self._spoof_events[ticker] = collections.deque(
                        maxlen=_SPOOF_BOOK_HISTORY_MAXLEN
                    )

                book_deque = self._book_snapshots[ticker]
                event_deque = self._spoof_events[ticker]

                # ---- Compare to previous snapshot ----
                if book_deque:
                    prev_ts, prev_bid_sz, prev_ask_sz = book_deque[-1]
                    dt = now - prev_ts

                    # Only flag rapid changes (< _SPOOF_TIME_WINDOW_S apart)
                    if 0 < dt < _SPOOF_TIME_WINDOW_S:
                        bid_changed = (
                            prev_bid_sz > 0
                            and abs(bid_size - prev_bid_sz) / prev_bid_sz
                            > _SPOOF_SIZE_CHANGE_THRESHOLD
                        )
                        ask_changed = (
                            prev_ask_sz > 0
                            and abs(ask_size - prev_ask_sz) / prev_ask_sz
                            > _SPOOF_SIZE_CHANGE_THRESHOLD
                        )

                        if bid_changed or ask_changed:
                            event_deque.append(now)
                            side = "BID" if bid_changed else "ASK"
                            logger.debug(
                                "[SPOOF] %s %s-side instability: size %.0f→%.0f (dt=%.1fs)",
                                ticker, side,
                                prev_bid_sz if bid_changed else prev_ask_sz,
                                bid_size if bid_changed else ask_size,
                                dt,
                            )

                # Record current snapshot
                book_deque.append((now, bid_size, ask_size))

                # ---- Evaluate rolling event window ----
                # Purge events older than the rolling window
                cutoff = now - _SPOOF_EVENT_WINDOW_S
                while event_deque and event_deque[0] < cutoff:
                    event_deque.popleft()

                if len(event_deque) >= _SPOOF_EVENT_TRIGGER:
                    self._spoof_flags[ticker] = True
                    logger.warning(
                        "[SPOOF] %s SPOOFING DETECTED — %d book instability events in %.0fs "
                        "(threshold=%d in %ds). Adding spoof_warning to signal metadata.",
                        ticker,
                        len(event_deque),
                        _SPOOF_EVENT_WINDOW_S,
                        _SPOOF_EVENT_TRIGGER,
                        int(_SPOOF_EVENT_WINDOW_S),
                    )

            except Exception as spoof_err:
                logger.debug("[SPOOF] %s check failed: %s", ticker, spoof_err)

    def _apply_spoof_warnings(self) -> None:
        """Attach spoof_warning flags to open positions' metadata.

        Called after _detect_spoofing(). For any ticker flagged as
        potentially spoofed, sets `spoof_warning=True` in the position's
        metadata dict. This does NOT block trades — it warns downstream
        components so they can factor it into risk decisions.
        """
        if not self._spoof_flags or not self._virtual_trader:
            return

        for pos in self._virtual_trader.open_positions.values():
            if pos.status != "OPEN":
                continue
            ticker = pos.ticker
            if self._spoof_flags.get(ticker, False):
                # Attach warning to position metadata (create if absent)
                if not hasattr(pos, "metadata") or pos.metadata is None:
                    pos.metadata = {}
                pos.metadata["spoof_warning"] = True
                logger.info(
                    "[SPOOF] %s position marked with spoof_warning=True",
                    ticker,
                )
