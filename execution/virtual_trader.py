"""
NZT-48 Virtual Execution Engine
Pure self-tracking: generates signals, virtually executes at real prices,
tracks positions with 30-second updates, runs profit ladder, closes trades.

NO BROKER CONNECTION. Proves the system before real money.

Slippage Model:
- Bot B: entry ± random(0.01%, 0.08%) + $0.005/share commission
- Bot A: entry ± random(0.05%, 0.25%) + zero commission
- RVOL > 2.5: double slippage (fast market)
- Stops: 1.5× worse slippage than entries
- Partial fills: >500 shares → +0.01% per 500
"""
from __future__ import annotations

import json
import logging
import math
import random
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import (
    Signal, Direction, Bot, BotInstance, RegimeState,
    Position, Trade, SignalStatus,
)
import config as cfg
from execution.cost_model import (
    round_trip_cost_bps, spread_gate_result, get_spread_bps,
)
from uk_isa.isa_universe import (
    CORRELATION_GROUPS, LEVERAGE_MAP, FIVE_X_TICKERS,
    INVERSE_PAIRS, SECTOR_PROXY, ISA_FACTOR_GROUPS,
    UNDERLYING_MAP,
    get_leverage, get_abs_leverage, get_factor_group,
)
from core.quant_math.microstructure import calculate_micro_price
from core.quant_math.eigen_risk import calculate_portfolio_heat_pca
from core.quant_math.almgren_chriss import calculate_dynamic_slippage
from core.quant_math.nav_basis import nav_basis_gate
from core.tca_engine import TransactionCostAnalyzer

logger = logging.getLogger("nzt48.virtual_trader")

# Phase 9: P&L Kill Switch thresholds
_DAILY_PNL_KILL = -200.0    # -£200 = halt today
_TOTAL_PNL_KILL = -1500.0   # -£1500 = halt permanently

# Phase 21: Closed-loop TCA feedback — auto-corrects EV gate slippage
_tca_engine = TransactionCostAnalyzer(window=20)

# Phase 41: Barbell allocation split
_CORE_ALLOCATION = 0.90
_VOL_ALLOCATION = 0.10


@dataclass
class VirtualPosition:
    """Tracked virtual position with full state."""
    id: str = ""
    signal_id: str = ""
    ticker: str = ""
    bot: str = "B"
    bot_instance: str = "BULL"
    strategy: str = ""
    direction: str = "LONG"
    entry_price: float = 0.0        # Slippage-adjusted fill
    entry_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    shares: int = 0
    risk_dollars: float = 0.0
    initial_stop: float = 0.0
    current_stop: float = 0.0
    ladder_rung: int = 0             # Current rung (0-7)
    remaining_pct: float = 1.0       # % of position remaining
    unrealised_pnl: float = 0.0
    unrealised_r: float = 0.0
    peak_r: float = 0.0              # MFE
    trough_r: float = 0.0            # MAE
    partials: list = field(default_factory=list)
    commission: float = 0.0
    slippage: float = 0.0
    status: str = "OPEN"
    indicator_snapshot: dict = field(default_factory=dict)
    regime_at_entry: str = ""
    confidence: int = 0
    confidence_layers: dict = field(default_factory=dict)
    target_1r: float = 0.0
    target_2r: float = 0.0
    trail_stop: float = 0.0
    atr: float = 0.0                 # ATR(14) at entry, for profit ladder stops
    rvol: float = 0.0                # RVOL at entry, for rung 4 evaluation
    # Market context at entry
    time_window: str = ""                # Which session window (MORNING_MOMENTUM etc)
    portfolio_heat: float = 0.0          # Total risk deployed as % of equity
    concurrent_positions: int = 0        # How many other trades were open
    premarket_alignment: str = ""        # Did pre-market brief support this direction?
    sector_rs: float = 0.0              # Sector relative strength at entry
    vix_at_entry: float = 0.0           # VIX level at entry
    market_direction_spy: float = 0.0    # SPY change% at entry
    days_since_last_signal: int = 0     # Days since last signal on same ticker
    gap_classification: str = ""         # gap_and_go / gap_and_fade / flat
    news_catalyst: str = ""              # earnings / upgrade / news / macro / ""
    earnings_proximity: int = -1         # Days to/from nearest earnings (-1=unknown)
    # Phase 5: Chandelier Exit tracking (highest high / lowest low from peak/trough)
    highest_high: float = 0.0
    lowest_low: float = 0.0
    # Phase 29: Volume-clock time-stop
    entry_underlying_vol: float = 0.0   # Underlying ADV at entry for volume-clock
    # Phase 20: Flash crash detection
    previous_price: float = 0.0         # Last seen price for flash crash detection


@dataclass
class VirtualTrade:
    """Completed virtual trade record."""
    id: str = ""
    position_id: str = ""
    signal_id: str = ""
    bot: str = "B"
    bot_instance: str = "BULL"
    ticker: str = ""
    direction: str = "LONG"
    strategy: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    entry_time: str = ""
    exit_time: str = ""
    shares: int = 0
    risk_dollars: float = 0.0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    commission: float = 0.0
    slippage: float = 0.0
    r_multiple: float = 0.0
    entry_quality: float = 0.0       # 100 × (1 - |MAE| / stop_distance)
    exit_quality: float = 0.0        # 100 × (exit_R / MFE_R)
    exit_reason: str = ""
    duration_minutes: int = 0
    regime_at_entry: str = ""
    regime_at_exit: str = ""
    confidence: int = 0
    indicator_snapshot_entry: dict = field(default_factory=dict)
    indicator_snapshot_exit: dict = field(default_factory=dict)
    peak_r: float = 0.0
    trough_r: float = 0.0
    partials: list = field(default_factory=list)
    failure_category: str = ""
    # Market context at entry
    time_window: str = ""
    portfolio_heat: float = 0.0
    concurrent_positions: int = 0
    premarket_alignment: str = ""
    sector_rs_entry: float = 0.0
    vix_at_entry: float = 0.0
    vix_at_exit: float = 0.0
    market_direction_during: float = 0.0   # SPY change% during trade
    gap_classification: str = ""
    news_catalyst: str = ""
    earnings_proximity: int = -1
    # Execution analysis
    mae_time_minutes: int = 0              # How many minutes to hit MAE
    mfe_time_minutes: int = 0              # How many minutes to hit MFE
    exit_efficiency: float = 0.0           # (actual_exit - entry) / (MFE - entry)
    slippage_model_vs_actual: float = 0.0  # Model slippage vs what happened
    # Missed opportunity tracking
    missed_gain_by_early_exit: float = 0.0   # How much more we could have made if we held to MFE
    missed_loss_by_late_exit: float = 0.0    # How much we lost by not exiting earlier at MFE
    # G-14: Financing drag (overnight cost for leveraged ETPs)
    financing_drag: float = 0.0
    # Firewall state
    firewall_cooldown_active: bool = False
    tournament_rank: int = 0               # Strategy tournament rank at entry


class SlippageModel:
    """Realistic cost-model-based slippage and commission model.

    Phase 10: Replaces fixed random seed with adaptive spread-based slippage
    calibrated from the institutional cost model (execution/cost_model.py).
    """

    def __init__(self):
        self._rng = random.Random()  # No fixed seed — realistic variance
        self.logger = logging.getLogger("nzt48.slippage_model")

    def entry_slippage(self, signal: Signal, shares: int = 0) -> float:
        """Calculate entry slippage using cost-model spread data.

        Uses get_spread_bps() from the institutional cost model for realistic
        half-spread slippage, with noise and RVOL scaling.

        Args:
            signal: The signal being executed.
            shares: Actual computed share count (signal.shares may be 0 at call time).
        """
        spread_bps = get_spread_bps(signal.ticker)
        half_spread = signal.entry * (spread_bps / 2 / 10_000)
        noise = self._rng.uniform(0.8, 1.2)
        slip = half_spread * noise

        # Fast market: RVOL > 2.5 increases slippage by 50%
        rvol = getattr(signal, 'rvol', 1.0) or 1.0
        if rvol > 2.5:
            slip *= 1.5

        # Partial fill penalty: +0.01% per 500 shares (retained from v4)
        effective_shares = shares if shares > 0 else signal.shares
        if effective_shares > 500:
            extra_blocks = effective_shares // 500
            slip += signal.entry * extra_blocks * 0.0001

        # Phase 32: Almgren-Chriss dynamic slippage when book depth is available
        adv = getattr(signal, 'adv', 0) or 0
        top_book = getattr(signal, 'bid_size', 0) or 0
        daily_vol = getattr(signal, 'daily_volatility', 0) or 0
        if adv > 0 and top_book > 0 and daily_vol > 0 and effective_shares > 0:
            dynamic_slip = calculate_dynamic_slippage(effective_shares, adv, daily_vol, top_book)
            if dynamic_slip > abs(slip):
                slip = dynamic_slip  # Absolute value; direction sign applied below
                self.logger.debug("ALMGREN_CHRISS: %s dynamic_slip=%.6f", signal.ticker, dynamic_slip)

        # Direction: longs get worse (higher) fill, shorts get worse (lower) fill
        if signal.direction == Direction.LONG:
            return slip
        else:
            return -slip

    def stop_slippage(self, position: VirtualPosition) -> float:
        """Calculate stop-loss fill slippage (1.5× worse than entry)."""
        price = position.current_stop
        is_bot_a = position.bot == "A"

        if is_bot_a:
            slip_pct = self._rng.uniform(0.0005, 0.0025) * 1.5
        else:
            slip_pct = self._rng.uniform(0.0001, 0.0008) * 1.5

        slip_dollars = price * slip_pct

        # Stops fill worse: longs stopped lower, shorts stopped higher
        if position.direction == "LONG":
            return -slip_dollars
        else:
            return slip_dollars

    @staticmethod
    def commission(signal: Signal, shares: int = 0) -> float:
        """Calculate commission.

        Args:
            signal: The signal being executed.
            shares: Actual computed share count (signal.shares may be 0 at call time).
        """
        if signal.bot == Bot.A:
            return 0.0  # ISA — zero commission
        else:
            effective_shares = shares if shares > 0 else signal.shares
            return effective_shares * 0.005  # $0.005/share for IBKR


class VirtualTrader:
    """Virtual execution engine for NZT-48.

    Manages all virtual positions:
    - Opens positions on qualified signals with slippage
    - Runs 30-second price update loop
    - Executes profit ladder state machine
    - Closes positions (stop hit, target, timeout, regime flip)
    - Records completed trades with full analytics
    """

    def __init__(self):
        self.logger = logging.getLogger("nzt48.virtual_trader")
        self.equity = float(cfg.get("system.starting_equity", 10_000))  # From config, default £10k ISA
        self.open_positions: dict[str, VirtualPosition] = {}
        self.closed_trades: list[VirtualTrade] = []
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        self.total_realised_pnl = 0.0
        self.slippage_model = SlippageModel()
        self._on_trade_close_callbacks: list = []
        self._db = None
        self._lock = threading.RLock()  # Reentrant lock — safe for close_position called under lock
        # Phase 9: P&L kill switch state
        self._trading_halted = False
        self._all_trades: deque = deque(maxlen=2000)  # Bounded: auto-evicts oldest trades
        # Phase 13.2: Price history for PCA eigen risk
        self._price_history: dict[str, list[float]] = {}
        # V9.5 Phase 2: Telemetry buffer (set via set_telemetry_buffer)
        self._telemetry_buffer = None
        # T-12: Circuit breaker state — fail-closed until first check_all() runs
        self._cb_allow_new_entries: bool = False
        self._cb_last_update_ts: float = 0.0  # epoch timestamp of last CB state push
        # C-02/C-03: Chandelier Exit reference — SOLE profit ladder authority
        self._chandelier = None
        # H-06: Broker failure protocol — reference to IBKRGateway for DEGRADED checks
        self._ibkr_gateway = None

    def set_db(self, db_connection):
        """Set database connection for persistence."""
        self._db = db_connection
        # C-15: Reload any surviving open positions from DB on restart
        self._load_open_positions()

    def set_telemetry_buffer(self, telemetry_buffer) -> None:
        """V9.5: Attach telemetry buffer for feature snapshot capture."""
        self._telemetry_buffer = telemetry_buffer
        self.logger.info("V9.5 telemetry buffer attached")

    def update_circuit_breaker_state(self, allow_new_entries: bool) -> None:
        """T-12: Called by engine after each circuit breaker check."""
        self._cb_allow_new_entries = allow_new_entries
        self._cb_last_update_ts = time.time()

    def set_chandelier(self, chandelier) -> None:
        """C-02/C-03: Attach ChandelierExit instance — SOLE profit ladder authority.

        Once attached, all profit ladder logic delegates to Chandelier.
        The inline _run_profit_ladder and _run_etp_ladder become no-ops.
        """
        self._chandelier = chandelier
        self.logger.info("C-02: ChandelierExit attached as SOLE profit ladder authority")

    def set_circuit_breakers(self, cb) -> None:
        """A-09: Attach CircuitBreakerSystem for stop-out cascade detection.

        When attached, close_position() calls cb.record_stopout() on
        STOP_HIT exits so the anti-cascade detector can halt entries
        if 3 stop-outs occur within 15 minutes.
        """
        self._circuit_breakers = cb
        self.logger.info("A-09: CircuitBreakerSystem attached for anti-cascade detection")

    def set_ibkr_gateway(self, gateway) -> None:
        """H-06: Attach IBKRGateway for broker failure protocol awareness.

        When attached, open_position() checks gateway.is_degraded before
        accepting new entries. Existing positions rely on broker-side
        bracket orders (GTC stops) during DEGRADED mode.
        """
        self._ibkr_gateway = gateway
        self.logger.info("H-06: IBKRGateway attached for broker failure protocol")

    def is_broker_degraded(self) -> bool:
        """H-06: Check if broker is in DEGRADED mode (no new entries allowed).

        Returns False if no gateway attached (virtual-only mode).
        """
        if self._ibkr_gateway is None:
            return False
        return self._ibkr_gateway.is_degraded

    def _load_open_positions(self):
        """Restore open positions from virtual_positions table on startup (C-15).

        Without this, a restart loses all in-flight positions.
        Also rehydrates chandelier ladder_rung state so trailing stops
        resume at the correct rung instead of resetting to 0.
        """
        if not self._db:
            return
        try:
            rows = self._db.execute(
                "SELECT * FROM virtual_positions WHERE status = 'OPEN'"
            ).fetchall()
            if not rows:
                return

            col_names = [desc[0] for desc in self._db.execute(
                "SELECT * FROM virtual_positions LIMIT 0"
            ).description]

            restored = 0
            for row in rows:
                data = dict(zip(col_names, row))
                pos = VirtualPosition(
                    id=data.get("id", ""),
                    signal_id=data.get("signal_id", ""),
                    bot=data.get("bot", "B"),
                    bot_instance=data.get("bot_instance", "BULL"),
                    ticker=data.get("ticker", ""),
                    direction=data.get("direction", "LONG"),
                    strategy=data.get("strategy", ""),
                    entry_price=float(data.get("entry_price", 0)),
                    shares=int(data.get("shares", 0)),
                    risk_dollars=float(data.get("risk_dollars", 0)),
                    initial_stop=float(data.get("initial_stop", 0)),
                    current_stop=float(data.get("current_stop", 0)),
                    ladder_rung=int(data.get("ladder_rung", 0)),
                    unrealised_pnl=float(data.get("unrealised_pnl", 0)),
                    unrealised_r=float(data.get("unrealised_r", 0)),
                    peak_r=float(data.get("peak_r", 0)),
                    trough_r=float(data.get("trough_r", 0)),
                    commission=float(data.get("commission", 0)),
                    slippage=float(data.get("slippage", 0)),
                    status="OPEN",
                    regime_at_entry=data.get("regime_at_entry", ""),
                    confidence=int(data.get("confidence", 0)),
                    time_window=data.get("time_window", ""),
                    portfolio_heat=float(data.get("portfolio_heat", 0)),
                    concurrent_positions=int(data.get("concurrent_positions", 0)),
                    premarket_alignment=data.get("premarket_alignment", ""),
                    sector_rs=float(data.get("sector_rs", 0)),
                    vix_at_entry=float(data.get("vix_at_entry", 0)),
                    market_direction_spy=float(data.get("market_direction_spy", 0)),
                    days_since_last_signal=int(data.get("days_since_last_signal", 0)),
                    gap_classification=data.get("gap_classification", ""),
                    news_catalyst=data.get("news_catalyst", ""),
                    earnings_proximity=int(data.get("earnings_proximity", -1)),
                )
                # Parse entry_time from ISO string back to datetime
                entry_time_str = data.get("entry_time", "")
                if entry_time_str:
                    try:
                        pos.entry_time = datetime.fromisoformat(entry_time_str)
                    except (ValueError, TypeError):
                        pos.entry_time = datetime.now(timezone.utc)

                # Parse JSON fields
                for json_field in ("indicator_snapshot", "confidence_layers"):
                    raw = data.get(json_field, "")
                    if raw and isinstance(raw, str):
                        try:
                            setattr(pos, json_field, json.loads(raw))
                        except (json.JSONDecodeError, TypeError):
                            pass

                self.open_positions[pos.id] = pos
                restored += 1

                # C-15: Rehydrate chandelier ladder state from DB for surviving positions
                if pos.ladder_rung > 0:
                    self.logger.info(
                        "CHANDELIER_REHYDRATE: %s at rung %d (R=%.2f, stop=%.4f)",
                        pos.id, pos.ladder_rung, pos.unrealised_r, pos.current_stop,
                    )

            if restored:
                self.logger.info(
                    "STARTUP_RESTORE: loaded %d open positions from DB", restored,
                )
        except Exception as e:
            self.logger.error("Failed to load open positions from DB: %s", e)

    def register_trade_callback(self, callback):
        """Register a callback for when trades close (feeds learning engine)."""
        self._on_trade_close_callbacks.append(callback)

    # ── Phase 9: P&L Kill Switch ────────────────────────────────────────────
    def _check_pnl_kill_switch(self) -> bool:
        """Check daily and total P&L against kill thresholds.

        Returns True if trading is allowed, False if halted.
        """
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_pnl = sum(
            t.net_pnl for t in self.closed_trades
            if hasattr(t, 'net_pnl') and t.exit_time and t.exit_time[:10] == today_str
        )
        total_pnl = sum(t.net_pnl for t in self._all_trades if hasattr(t, 'net_pnl'))
        if today_pnl <= _DAILY_PNL_KILL:
            self.logger.critical("PNL_KILL_DAILY: £%.2f -- HALTING", today_pnl)
            self._trading_halted = True
            return False
        if total_pnl <= _TOTAL_PNL_KILL:
            self.logger.critical("PNL_KILL_TOTAL: £%.2f -- HALTING", total_pnl)
            self._trading_halted = True
            return False
        return True

    # ── Phase 13: Portfolio Heat — correlation veto + beta cap + max positions ──
    def _check_portfolio_heat(self, signal: Signal) -> bool:
        """Check portfolio concentration and risk limits.

        Returns True if the trade is allowed, False if vetoed.
        Rules:
        - Max 3 open positions
        - Max 2 positions per correlation group
        - Total effective beta (sum of abs leverage) must not exceed 6.0
        """
        open_positions = [p for p in self.open_positions.values() if p.status == "OPEN"]

        # Max 3 open positions
        if len(open_positions) >= 3:
            self.logger.warning("PORTFOLIO_HEAT_VETO: max 3 positions reached (%d open)", len(open_positions))
            return False

        # Check same correlation group count (max 2 per group)
        signal_group = None
        for group_name, group_tickers in CORRELATION_GROUPS.items():
            if signal.ticker in group_tickers:
                signal_group = group_name
                break

        if signal_group:
            same_group_count = 0
            for pos in open_positions:
                if pos.ticker in CORRELATION_GROUPS.get(signal_group, []):
                    same_group_count += 1
            if same_group_count >= 2:
                self.logger.warning(
                    "PORTFOLIO_HEAT_VETO: max 2 per correlation group '%s' (%d already open)",
                    signal_group, same_group_count,
                )
                return False

        # Check total effective beta (max 6.0)
        current_beta = sum(get_abs_leverage(pos.ticker) for pos in open_positions)
        new_beta = get_abs_leverage(signal.ticker)
        if current_beta + new_beta > 6.0:
            self.logger.warning(
                "PORTFOLIO_HEAT_VETO: total effective beta %.1f + %.1f = %.1f > 6.0",
                current_beta, new_beta, current_beta + new_beta,
            )
            return False

        # Phase 13.2: Dynamic PCA heat (when sufficient data exists)
        try:
            import numpy as np
            open_tickers = [p.ticker for p in self.open_positions.values() if p.status == "OPEN"]
            if len(open_tickers) >= 2:
                # Build returns matrix from recent price updates
                returns_list = []
                for t in open_tickers:
                    hist = self._price_history.get(t, [])
                    if len(hist) >= 10:
                        arr = np.array(hist[-20:])
                        rets = np.diff(arr) / arr[:-1]
                        returns_list.append(rets)
                if len(returns_list) >= 2:
                    min_len = min(len(r) for r in returns_list)
                    if min_len >= 5:
                        matrix = np.column_stack([r[-min_len:] for r in returns_list])
                        pc1 = calculate_portfolio_heat_pca(matrix)
                        if pc1 > 0.85:
                            self.logger.warning(
                                "EIGEN_VETO: PC1=%.2f — portfolio is one bet, blocking entry",
                                pc1,
                            )
                            return False
        except Exception:
            pass  # PCA is advisory, don't block on errors

        return True

    def _check_correlated_underlying(self, signal: Signal) -> bool:
        """T-13: Block if same underlying already has an open position (different ticker)."""
        new_ul = UNDERLYING_MAP.get(signal.ticker, signal.ticker)
        for pos in self.open_positions.values():
            if pos.status == "OPEN" and pos.ticker != signal.ticker:
                pos_ul = UNDERLYING_MAP.get(pos.ticker, pos.ticker)
                if pos_ul == new_ul:
                    self.logger.warning(
                        "CORRELATED_UNDERLYING_VETO: %s blocked — %s already open (underlying: %s)",
                        signal.ticker, pos.ticker, new_ul,
                    )
                    return False
        return True

    # ── Phase 40: Regime-aware holding period ──────────────────────────────
    def _get_regime_adjusted_time_stop(self, pos: VirtualPosition, regime: str) -> int:
        """Return regime-adjusted max hold time in minutes.

        Trending / bullish / low-vol: extend winners to 120min, losers stay 45min.
        Choppy / high-vol / mean-reverting: cut to 20min.
        Default: 45min.
        """
        if regime in ("TRENDING", "BULLISH", "LOW_VOL",
                       "TRENDING_UP_STRONG", "TRENDING_UP_MOD"):
            pct_move = (pos.unrealised_pnl / (pos.entry_price * pos.shares)) if pos.shares > 0 else 0
            if pos.direction != "LONG":
                pct_move = -pct_move
            if pct_move > 0:
                return 120
            return 45
        if regime in ("CHOPPY", "HIGH_VOL", "MEAN_REVERTING",
                       "RANGING", "VOLATILE"):
            return 20
        return 45

    # ── Phase 41: Barbell allocation ──────────────────────────────────────
    def _get_available_equity_for_signal(self, signal_source: str) -> float:
        """Return available equity based on barbell allocation.

        Core signals get 90% of equity. Volatile/hedge signals get 10%.
        """
        total_equity = self._get_current_equity()
        if signal_source in ("PARASITE_REENTRY", "FLASH_CRASH_HEDGE", "VOL_SPIKE"):
            return total_equity * _VOL_ALLOCATION
        return total_equity * _CORE_ALLOCATION

    def _get_current_equity(self) -> float:
        """Return current equity (base + realised P&L)."""
        return self.equity

    # ── Phase 20: Flash crash hedge (delta-neutral via inverse ETPs) ──────
    def _trigger_flash_crash_hedge(self, pos: VirtualPosition, drop_pct: float, price: float) -> Optional[dict]:
        """If underlying drops > 0.5% in a single price update, buy inverse ETP at 50% of exposure.

        Returns an event dict if hedge triggered, None otherwise.
        """
        inverse_ticker = INVERSE_PAIRS.get(pos.ticker)
        if not inverse_ticker:
            return None

        hedge_exposure = abs(pos.entry_price * pos.shares * pos.remaining_pct) * 0.50
        self.logger.warning(
            "FLASH_CRASH_HEDGE: %s dropped %.2f%% — hedging with %s at £%.2f exposure",
            pos.ticker, drop_pct * 100, inverse_ticker, hedge_exposure,
        )
        return {
            "type": "FLASH_CRASH_HEDGE",
            "position": pos.id,
            "ticker": pos.ticker,
            "inverse_ticker": inverse_ticker,
            "drop_pct": round(drop_pct * 100, 2),
            "hedge_exposure": round(hedge_exposure, 2),
            "hedge_source": "FLASH_CRASH_HEDGE",
        }

    # ── Phase 26: Capacity cap ───────────────────────────────────────────
    def _apply_capacity_cap(self, shares: int, signal: Signal) -> int:
        """Cap shares to 10% of visible bid size.

        Uses the signal's bid_size attribute if available, otherwise no cap.
        """
        bid_size = getattr(signal, 'bid_size', 0) or 0
        if bid_size > 0:
            max_shares = int(bid_size * 0.10)
            if shares > max_shares and max_shares > 0:
                self.logger.warning(
                    "CAPACITY_CAP: %s shares %d > 10%% of bid_size %d — capping to %d",
                    signal.ticker, shares, bid_size, max_shares,
                )
                return max_shares
        return shares

    # ── C-18: LMP Half-Spread Cost Helper ─────────────────────────────────
    def _get_half_spread_bps(self, ticker: str) -> float:
        """Return half the bid-ask spread in basis points for *ticker*.

        Priority:
          1. Live / EWMA spread from execution.cost_model.get_spread_bps()
          2. Conservative static defaults by leverage factor:
             - 5× ETPs: 25 bps (wider spreads, thinner books)
             - 3× ETPs / everything else: 15 bps

        The returned value is *half*-spread — i.e. one side of the round trip.

        Reference: Lehalle & Mounjid (2023), "Limit Order Book dynamics".
        """
        full_spread = get_spread_bps(ticker)
        if full_spread and full_spread > 0:
            return full_spread / 2.0

        # Fallback: conservative defaults by leverage tier
        if ticker in FIVE_X_TICKERS:
            return 25.0
        return 15.0

    # ── C-19: Avellaneda-Stoikov Inventory Skew ────────────────────────────
    def _stoikov_skew(
        self,
        inventory_risk: float,
        gamma: float = 0.1,
        sigma: float = 0.02,
    ) -> float:
        """Avellaneda-Stoikov mid-price adjustment for inventory risk.

        The optimal market-maker shifts the reservation price away from the
        mid by  δ = −γ · σ² · q  where *q* is normalised inventory risk
        in [-1, +1].

        In our context *q* measures how loaded the portfolio is relative to
        its capacity:
          q = (current_open − max_positions / 2) / (max_positions / 2)

        When the portfolio is full (q → +1) the skew penalises new longs
        (entry price adjusted upward → harder EV hurdle).  When it is empty
        (q → −1) the skew is favourable.

        Args:
            inventory_risk: Normalised inventory [-1, +1].
            gamma: Risk-aversion parameter (higher = more conservative).
            sigma: Estimated short-term volatility (decimal, e.g. 0.02 = 2%).

        Returns:
            Price skew in *fractional* terms (multiply by entry_price to get
            the GBP adjustment).

        Reference: Avellaneda & Stoikov (2008), "High-frequency trading in a
        limit order book", *Quantitative Finance*, 8(3), 217-224.
        """
        return -gamma * (sigma ** 2) * inventory_risk

    def _compute_inventory_risk(self) -> float:
        """Normalised inventory risk for the Stoikov skew.

        Returns a value in [-1, +1] where:
          -1 = no positions open (empty book — favourable skew)
          +1 = at capacity (full book — penalising skew)
        """
        max_positions = 3  # Hard cap from _check_portfolio_heat
        current_open = len(
            [p for p in self.open_positions.values() if p.status == "OPEN"]
        )
        half = max_positions / 2.0
        if half == 0:
            return 0.0
        return (current_open - half) / half

    # ── C-20: OFI Toxicity Check ──────────────────────────────────────────
    def _check_ofi_toxicity(self, ticker: str, threshold: float = 0.7) -> bool:
        """Check Order Flow Imbalance toxicity for *ticker*.

        Uses the OFI module (core.quant_math.ofi) when bid/ask depth is
        available.  A strongly one-sided OFI (|normalised_ofi| > *threshold*)
        signals toxic flow — informed traders are sweeping the book and
        passive orders face severe adverse selection.

        In paper mode this is **informational only** (logged but does not
        veto).  The architecture is wired so that when live execution is
        enabled the return value can gate passive-order placement.

        Args:
            ticker: Instrument to check.
            threshold: Absolute normalised OFI above which flow is deemed
                       toxic.  Default 0.7 per Cont, Kukanov & Stoikov (2014).

        Returns:
            True if flow is toxic (passive orders should be avoided).
            False otherwise or when data is unavailable.

        Reference: Cont, R., Kukanov, A. & Stoikov, S. (2014), "The Price
        Impact of Order Book Events", *Journal of Financial Economics*,
        113(3), 402-419.
        """
        try:
            from core.quant_math.ofi import calculate_ofi

            # Pull latest two ticks of book data from the signal cache if
            # available.  The virtual trader doesn't maintain its own book —
            # this relies on signal-level attributes populated by the scanner.
            # If the data isn't there we silently return False (non-toxic).
            _ofi_cache = getattr(self, '_ofi_book_cache', {})
            prev = _ofi_cache.get(ticker)
            if prev is None:
                return False

            bid_t = prev.get('bid_t', 0)
            bid_size_t = prev.get('bid_size_t', 0)
            ask_t = prev.get('ask_t', 0)
            ask_size_t = prev.get('ask_size_t', 0)
            bid_prev = prev.get('bid_prev', 0)
            bid_size_prev = prev.get('bid_size_prev', 0)
            ask_prev = prev.get('ask_prev', 0)
            ask_size_prev = prev.get('ask_size_prev', 0)

            if bid_t <= 0 or ask_t <= 0:
                return False

            ofi_raw = calculate_ofi(
                bid_t, bid_size_t, ask_t, ask_size_t,
                bid_prev, bid_size_prev, ask_prev, ask_size_prev,
            )

            # Normalise by total visible depth
            total_depth = bid_size_t + ask_size_t
            if total_depth <= 0:
                return False
            normalised = abs(ofi_raw) / total_depth

            is_toxic = normalised > threshold
            if is_toxic:
                self.logger.info(
                    "OFI_TOXIC: %s |OFI|/depth=%.3f > %.2f — passive orders ill-advised",
                    ticker, normalised, threshold,
                )
            return is_toxic

        except Exception as exc:
            self.logger.debug("OFI toxicity check skipped for %s: %s", ticker, exc)
            return False

    def _get_recent_returns(self, ticker: str, lookback: int = 252) -> 'Optional[np.ndarray]':
        """Get recent daily returns for a ticker for EVT tail risk analysis.

        Attempts two sources in order:
        1. yfinance cached daily close prices → log returns.
        2. Virtual trade P&L history for this ticker (fallback).

        Returns numpy array of returns or None if insufficient data.

        Parameters
        ----------
        ticker : str
            Instrument ticker (e.g. "QQQ3.L").
        lookback : int
            Number of trading days of history to request. Default 252 (1 year).
        """
        import numpy as np

        # Source 1: yfinance daily closes → log returns
        try:
            import yfinance as yf
            period = f"{lookback + 10}d"  # Request extra days to account for non-trading days
            hist = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=True)
            if hist is not None and len(hist) >= 30:
                closes = hist["Close"].dropna().values.astype(float)
                if len(closes) >= 30:
                    log_returns = np.diff(np.log(closes))
                    return log_returns
        except Exception as yf_err:
            self.logger.debug("_get_recent_returns: yfinance failed for %s: %s", ticker, yf_err)

        # Source 2: Virtual trade P&L for this ticker (R-multiples as proxy)
        try:
            ticker_trades = [
                t.r_multiple for t in self._all_trades
                if hasattr(t, 'r_multiple') and hasattr(t, 'ticker')
                and t.ticker == ticker and t.r_multiple is not None
            ]
            if len(ticker_trades) >= 30:
                return np.array(ticker_trades, dtype=np.float64)
        except Exception as trade_err:
            self.logger.debug("_get_recent_returns: trade history failed for %s: %s", ticker, trade_err)

        return None

    def _stoikov_ev_gate(
        self,
        signal: Signal,
        mid_price: float,
    ) -> tuple[bool, str, dict]:
        """V9.5 Phase 4: Stoikov (2017) expected value gate.

        Pre-entry filter using micro-price imbalance and spread dynamics.
        Prevents entries when liquidity is deteriorating or execution costs are excessive.

        Three gates:
          1. Adverse selection: micro-price vs mid-price divergence (>2bps for direction)
          2. Spread momentum: >20% widening over last 5 observations
          3. Spread width: exceeds asset-class-specific threshold

        Returns (passed, reason, metrics). If gate disabled, always returns True.
        """
        from core.quant_math.microstructure import calculate_micro_price, calculate_spread_momentum

        # Feature flag check
        if not cfg.get("feature_flags.v95_stoikov_ev_gate", True):
            return True, "Stoikov gate disabled", {}

        # Get order book data from signal attributes
        _bid_px = getattr(signal, 'bid_price', 0) or 0
        _ask_px = getattr(signal, 'ask_price', 0) or 0
        _bid_sz = getattr(signal, 'bid_size', 0) or 0
        _ask_sz = getattr(signal, 'ask_size', 0) or 0

        # If no book data, pass through (graceful degradation)
        if _bid_px <= 0 or _ask_px <= 0 or (_bid_sz + _ask_sz) <= 0:
            return True, "Stoikov: no book data — pass-through", {}

        micro_price = calculate_micro_price(_bid_px, _ask_px, _bid_sz, _ask_sz)
        spread_bps = (_ask_px - _bid_px) / mid_price * 10_000 if mid_price > 0 else 0

        # Spread momentum from cost_model's tracker
        spread_history = []
        try:
            from execution.cost_model import get_spread_history
            spread_history = get_spread_history(signal.ticker)
        except (ImportError, Exception):
            pass
        spread_momentum = calculate_spread_momentum(spread_history) if spread_history else 0.0

        metrics = {
            "micro_price": round(micro_price, 6),
            "mid_price": round(mid_price, 6),
            "spread_bps": round(spread_bps, 2),
            "spread_momentum": round(spread_momentum, 4),
        }

        # Determine spread threshold based on asset class
        ticker = signal.ticker
        if ticker.endswith(".L"):
            max_spread_bps = float(cfg.get("feature_flags.v95_stoikov_spread_etp_bps", 80))
        elif hasattr(signal, 'bot') and signal.bot.value == "B":
            # Check A-team vs B-team
            from uk_isa.isa_universe import A_TEAM_US
            if hasattr(signal, 'ticker') and signal.ticker in (A_TEAM_US if 'A_TEAM_US' in dir() else set()):
                max_spread_bps = float(cfg.get("feature_flags.v95_stoikov_spread_us_a_bps", 30))
            else:
                max_spread_bps = float(cfg.get("feature_flags.v95_stoikov_spread_us_b_bps", 50))
        else:
            max_spread_bps = 50.0

        # Gate 1: Adverse selection
        if signal.direction == Direction.LONG:
            if micro_price < mid_price * 0.9998:  # micro > 2bps below mid
                return False, f"Stoikov: LONG but micro<mid by {(mid_price-micro_price)/mid_price*10000:.1f}bps (adverse)", metrics
        else:
            if micro_price > mid_price * 1.0002:
                return False, f"Stoikov: SHORT but micro>mid by {(micro_price-mid_price)/mid_price*10000:.1f}bps (adverse)", metrics

        # Gate 2: Spread momentum
        if spread_momentum > 0.20:
            return False, f"Stoikov: spread widening {spread_momentum:.0%} (liquidity deteriorating)", metrics

        # Gate 3: Spread width
        if spread_bps > max_spread_bps:
            return False, f"Stoikov: spread {spread_bps:.0f}bps > {max_spread_bps:.0f}bps threshold", metrics

        return True, "Stoikov: EV gate passed", metrics

    def open_position(self, signal: Signal, indicators_snapshot: dict = None) -> Optional[VirtualPosition]:
        """Open a virtual position from a qualified signal.

        Entry at signal price + realistic slippage.
        Applies Phase 3 (EV gate), Phase 9 (P&L kill switch), Phase 12 (trade suppression),
        Phase 13 (portfolio heat), Phase 26 (capacity cap).
        """
        if signal.status == SignalStatus.SKIPPED:
            return None

        # Phase 9: P&L kill switch — halt all trading if daily/total losses exceed thresholds
        if self._trading_halted:
            self.logger.warning("TRADING_HALTED: rejecting %s — kill switch active", signal.ticker)
            return None
        if not self._check_pnl_kill_switch():
            return None

        # H-06: Broker failure protocol — reject new entries when broker is DEGRADED
        if self.is_broker_degraded():
            self.logger.warning(
                "H-06 DEGRADED_VETO: %s rejected — broker in DEGRADED mode, no new entries allowed. "
                "Open positions rely on broker-side bracket orders.",
                signal.ticker,
            )
            return None

        # T-12: Circuit breaker re-check at execution time (120s state expiry)
        _cb_age = time.time() - self._cb_last_update_ts
        if _cb_age > 120:
            self.logger.warning("CIRCUIT_BREAKER_VETO at execution: %s blocked — CB state stale (%.0fs old)", signal.ticker, _cb_age)
            return None
        if not self._cb_allow_new_entries:
            self.logger.warning("CIRCUIT_BREAKER_VETO at execution: %s blocked — new entries not allowed", signal.ticker)
            return None

        # T-13: Block same-underlying positions (e.g., QQQ3.L blocks QQQ5.L)
        if not self._check_correlated_underlying(signal):
            return None

        # C-11: Atomic mutual exclusion — block if inverse counterpart is held
        if not self._check_mutual_exclusion(signal.ticker):
            return None

        # C-05: Overnight gap veto — block if gap > 2x ATR/Close%
        _signal_atr = getattr(signal, 'atr', 0) or 0
        if _signal_atr <= 0 and indicators_snapshot:
            _signal_atr = (indicators_snapshot or {}).get('atr14', 0) or 0
        if _signal_atr > 0 and self._check_overnight_gap_veto(signal.ticker, signal.entry, _signal_atr):
            return None

        # Phase 13: Portfolio heat — correlation veto, beta cap, max 3 positions
        if not self._check_portfolio_heat(signal):
            return None

        # Phase 12: Trade suppression — RVOL + sector veto
        # Underlying RVOL check: if underlying RVOL < 0.7, veto
        underlying_rvol = getattr(signal, 'rvol', 1.0) or 1.0
        if underlying_rvol < 0.7:
            self.logger.warning("RVOL_VETO: %s underlying RVOL=%.2f < 0.7 — rejected", signal.ticker, underlying_rvol)
            return None

        # Phase 12: Sector dispersion check — if going LONG but sector ETF is down > 0.5%, veto
        if signal.direction == Direction.LONG:
            sector_etf = SECTOR_PROXY.get(signal.ticker)
            if sector_etf:
                sector_change = getattr(signal, 'sector_change_pct', None)
                if sector_change is not None and sector_change < -0.5:
                    self.logger.warning(
                        "SECTOR_VETO: %s LONG but sector %s down %.2f%% — rejected",
                        signal.ticker, sector_etf, sector_change,
                    )
                    return None

        # V9.5 Phase 4: Stoikov EV gate — pre-entry liquidity and adverse selection filter
        _stoikov_passed, _stoikov_reason, _stoikov_metrics = self._stoikov_ev_gate(
            signal, mid_price=signal.entry,
        )
        if not _stoikov_passed:
            self.logger.warning(
                "STOIKOV_VETO: %s %s — %s (spread=%.1fbps micro=%.4f)",
                signal.ticker, signal.direction.value, _stoikov_reason,
                _stoikov_metrics.get("spread_bps", 0),
                _stoikov_metrics.get("micro_price", 0),
            )
            return None

        # Phase 28: NAV basis gate — front-run AP activity (requires Bloomberg IIV)
        iiv = getattr(signal, 'iiv', None) or getattr(signal, 'nav_iiv', None)
        if iiv and iiv > 0:
            nav_pass, nav_reason = nav_basis_gate(
                signal.ticker, signal.entry, iiv, signal.direction.value,
            )
            if not nav_pass:
                self.logger.warning("NAV_BASIS_VETO: %s", nav_reason)
                return None

        # Pre-compute shares from signal entry (before slippage) for initial sizing
        pre_slip_risk = abs(signal.entry - signal.stop)
        if pre_slip_risk <= 0:
            self.logger.warning(
                "Zero risk distance for %s %s (entry=%.2f, stop=%.2f, strategy=%s) — skipping",
                signal.ticker, signal.direction.value, signal.entry, signal.stop, signal.strategy,
            )
            return None

        risk_budget = self.equity * signal.risk_pct
        estimated_shares = max(1, int(risk_budget / pre_slip_risk))

        # Phase 41: Barbell allocation — limit equity by signal source
        signal_source = getattr(signal, 'source', 'CORE') or 'CORE'
        available_equity = self._get_available_equity_for_signal(signal_source)
        risk_budget = min(risk_budget, available_equity * signal.risk_pct)

        # Calculate slippage-adjusted entry (now with correct share count)
        entry_slip = self.slippage_model.entry_slippage(signal, shares=estimated_shares)
        fill_price = signal.entry + entry_slip

        # ── Phase 19: Stoikov Micro-Price Adjustment ──────────────────────
        # Stoikov (2017) SSRN 2970694: micro-price is a martingale estimator
        # of future mid-price, weighted by order book imbalance.
        # If bid_size > ask_size → buying pressure → micro > mid.
        # Use micro-price for EV gate when bid/ask depth is available,
        # falling back to fill_price (slippage-adjusted mid) when it is not.
        _bid_px = getattr(signal, 'bid_price', 0) or 0
        _ask_px = getattr(signal, 'ask_price', 0) or 0
        _bid_sz = getattr(signal, 'bid_size', 0) or 0
        _ask_sz = getattr(signal, 'ask_size', 0) or 0

        if _bid_px > 0 and _ask_px > 0 and (_bid_sz + _ask_sz) > 0:
            micro_price = calculate_micro_price(_bid_px, _ask_px, _bid_sz, _ask_sz)
            mid_price = (_bid_px + _ask_px) / 2.0
            # Apply micro-price adjustment to fill: shift fill by (micro - mid)
            fill_price += (micro_price - mid_price)
            self.logger.debug(
                "MICRO_PRICE: %s bid=%.4f ask=%.4f bid_sz=%d ask_sz=%d "
                "mid=%.4f micro=%.4f adj_fill=%.4f",
                signal.ticker, _bid_px, _ask_px, _bid_sz, _ask_sz,
                mid_price, micro_price, fill_price,
            )
        # ── End Phase 19 ─────────────────────────────────────────────────

        # ── Phase 42: LMP (Lillo-Mikhail-Pu) Spread-Widening Penalty ────
        # If spread is widening tick-by-tick, apply 20% adverse selection
        # penalty to P(Win). Since no L2 order book, full Avellaneda-Stoikov
        # is descoped — LMP penalises based on spread dynamics only.
        _lmp_penalty = 1.0  # No penalty by default
        _prev_spread_bps = getattr(signal, '_prev_spread_bps', None)
        _current_spread_bps = get_spread_bps(signal.ticker)
        if _prev_spread_bps and _prev_spread_bps > 0:
            if _current_spread_bps > _prev_spread_bps * 1.1:  # Spread widening >10%
                _lmp_penalty = 0.80  # 20% adverse selection penalty
                self.logger.info(
                    "LMP_PENALTY: %s spread widening %.1f→%.1f bps — 20%% P(Win) cut",
                    signal.ticker, _prev_spread_bps, _current_spread_bps,
                )
        # ── End Phase 42 ─────────────────────────────────────────────────

        # ── C-19: Avellaneda-Stoikov Inventory Skew ──────────────────────
        # Adjust virtual fill price by reservation-price skew so that a
        # full portfolio faces a tighter EV hurdle (higher entry for longs,
        # lower entry for shorts).
        # Ref: Avellaneda & Stoikov (2008), Quantitative Finance 8(3).
        _inv_risk = self._compute_inventory_risk()
        _stoikov_delta = self._stoikov_skew(_inv_risk)
        if abs(_stoikov_delta) > 1e-9:
            _price_adj = fill_price * _stoikov_delta
            fill_price += _price_adj
            self.logger.debug(
                "STOIKOV_SKEW: %s inv_risk=%.2f skew=%.6f adj=%.4f new_fill=%.4f",
                signal.ticker, _inv_risk, _stoikov_delta, _price_adj, fill_price,
            )
        # ── End C-19 ────────────────────────────────────────────────────

        # ── C-20: OFI Toxicity Check (informational in paper mode) ──────
        # Log toxic flow detection; architecture ready for live-mode veto.
        # Ref: Cont, Kukanov & Stoikov (2014), J. Financial Economics.
        _ofi_toxic = self._check_ofi_toxicity(signal.ticker)
        if _ofi_toxic:
            self.logger.info(
                "OFI_TOXIC_WARN: %s — toxic flow detected, passive orders ill-advised "
                "(paper mode: proceeding anyway)",
                signal.ticker,
            )
        # ── End C-20 ────────────────────────────────────────────────────

        # ── Phase 3: EV-Based Execution Gate ──────────────────────────────
        # Gate 1: Spread veto
        gate = spread_gate_result(signal.ticker)
        if gate == "VETO":
            self.logger.warning("SPREAD_VETO: %s -- rejected", signal.ticker)
            return None

        # Gate 2: Asymmetric execution costs
        order_value = fill_price * estimated_shares
        rt_cost_bps = round_trip_cost_bps(signal.ticker, order_value=order_value)
        _tca_correction = _tca_engine.get_correction_factor()
        entry_cost = fill_price * (rt_cost_bps / 2 * _tca_correction) / 10_000
        exit_cost_stressed = entry_cost * 2.0  # Perold 1988
        drift_buffer = fill_price * 2 / 10_000  # 2 bps with IBKR

        # ── C-18: LMP Spread-Widening Penalty — half-spread EV discount ──
        # Discount expected fill by the half-spread cost.  This captures the
        # crossing-the-spread penalty that the original EV gate omitted.
        # Ref: Lehalle & Mounjid (2023), "Limit Order Book dynamics".
        half_spread_bps = self._get_half_spread_bps(signal.ticker)
        spread_penalty = fill_price * (half_spread_bps / 10_000.0)
        # ── End C-18 ────────────────────────────────────────────────────

        # Gate 3: EV computation
        p_win = min(0.65, max(0.35, signal.confidence / 100.0)) * _lmp_penalty
        capture = abs(signal.target_1r - fill_price) if hasattr(signal, 'target_1r') else abs(getattr(signal, 'target1', fill_price * 1.02) - fill_price)
        stop_dist = abs(fill_price - signal.stop)

        ev = (p_win * (capture - entry_cost - drift_buffer)) - ((1 - p_win) * (stop_dist + exit_cost_stressed + drift_buffer)) - spread_penalty

        if ev <= 0:
            self.logger.warning("EV_VETO: %s EV=%.4f (spread_penalty=%.4f)", signal.ticker, ev, spread_penalty)
            return None

        # ── Phase 43: GPD Tail Risk Veto (Balkema-de Haan-Pickands) ──────
        # If P(loss > 5σ gap) > 1% based on trade history, reject signal.
        # Requires ≥10 exceedances to activate — no-op during cold start.
        try:
            import numpy as np
            from core.quant_math.evt import gpd_tail_risk
            _r_multiples = [t.r_multiple for t in self._all_trades if hasattr(t, 'r_multiple')]
            if len(_r_multiples) >= 30:
                _evt_result = gpd_tail_risk(np.array(_r_multiples))
                if _evt_result.veto:
                    self.logger.warning(
                        "EVT_GPD_VETO: %s — %s", signal.ticker, _evt_result.reason,
                    )
                    return None
        except Exception as _evt_err:
            self.logger.debug("EVT check skipped: %s", _evt_err)
        # ── End Phase 43 ─────────────────────────────────────────────────

        # ── Phase V8: GPD Tail Risk Veto (Balkema-de Haan-Pickands, C-24) ─
        # Stateful per-ticker EVT veto using core/evt.py TailRiskMonitor.
        # If GPD predicts >1% probability of a 5-sigma gap, reject signal.
        # Complements Phase 43 (R-multiple based) with market-price-based EVT.
        try:
            from core.evt import TailRiskMonitor
            if not hasattr(self, '_tail_monitor'):
                self._tail_monitor = TailRiskMonitor()
            # Get recent returns for this ticker
            recent_returns = self._get_recent_returns(signal.ticker, lookback=252)
            if recent_returns is not None and len(recent_returns) >= 50:
                should_veto, veto_reason = self._tail_monitor.veto_signal(
                    signal.ticker, recent_returns, sigma_threshold=5.0
                )
                if should_veto:
                    self.logger.warning("GPD_TAIL_VETO: %s — %s", signal.ticker, veto_reason)
                    return None
        except Exception as e:
            self.logger.debug("GPD veto check failed (non-fatal): %s", e)
        # ── End Phase V8 ─────────────────────────────────────────────────

        # ── End Phase 3 ──────────────────────────────────────────────────

        # Recalculate shares from slippage-adjusted fill
        per_share_risk = abs(fill_price - signal.stop)
        if per_share_risk <= 0:
            self.logger.warning(
                "Zero risk distance for %s after slippage (fill=%.2f, stop=%.2f, slip=%.4f) — skipping",
                signal.ticker, fill_price, signal.stop, entry_slip,
            )
            return None

        shares = max(1, int(risk_budget / per_share_risk))

        # Phase 26: Capacity cap — don't exceed 10% of visible bid size
        shares = self._apply_capacity_cap(shares, signal)
        if shares < 1:
            self.logger.warning("CAPACITY_CAP: %s reduced to 0 shares — skipping", signal.ticker)
            return None

        # Commission uses actual share count
        commission = self.slippage_model.commission(signal, shares=shares)

        # Create position (under lock for thread safety)
        with self._lock:
            return self._create_position(
                signal, fill_price, shares, commission, entry_slip,
                per_share_risk, indicators_snapshot,
            )

    def _create_position(
        self, signal: Signal, fill_price: float, shares: int,
        commission: float, entry_slip: float, per_share_risk: float,
        indicators_snapshot: dict = None,
    ) -> VirtualPosition:
        """Internal: create and register position (must be called under lock)."""
        pos = VirtualPosition(
            id=f"VP-{str(uuid.uuid4())[:8]}",
            signal_id=signal.id,
            ticker=signal.ticker,
            bot=signal.bot.value,
            bot_instance=signal.bot_instance.value,
            strategy=signal.strategy,
            direction=signal.direction.value,
            entry_price=fill_price,
            entry_time=datetime.now(timezone.utc),
            shares=shares,
            risk_dollars=shares * per_share_risk,
            initial_stop=signal.stop,
            current_stop=signal.stop,
            target_1r=signal.target_1r,
            target_2r=signal.target_2r,
            commission=commission,
            slippage=abs(entry_slip) * shares,
            indicator_snapshot=indicators_snapshot or {},
            atr=(indicators_snapshot or {}).get("atr14", 0.0) or 0.0,
            rvol=signal.rvol if hasattr(signal, "rvol") else 0.0,
            regime_at_entry=signal.regime.value,
            confidence=int(signal.confidence),
            confidence_layers={
                "L1": signal.confidence_breakdown.layer1_price_action,
                "L2": signal.confidence_breakdown.layer2_regime,
                "L3": signal.confidence_breakdown.layer3_sector_flow,
                "L4": signal.confidence_breakdown.layer4_macro,
                "L5": signal.confidence_breakdown.layer5_narrative,
            },
        )

        self.open_positions[pos.id] = pos

        # C-03: Register with Chandelier Exit for trailing stop tracking
        if self._chandelier:
            try:
                self._chandelier.register(
                    trade_id=pos.id,
                    ticker=pos.ticker,
                    entry_price=pos.entry_price,
                    direction=pos.direction,
                    atr=pos.atr,
                )
            except Exception as _ch_err:
                self.logger.warning("Chandelier register failed for %s: %s", pos.ticker, _ch_err)

        # Persist to DB
        if self._db:
            self._persist_position(pos)

        # V9.5 Phase 2: Capture telemetry snapshot at entry (async, fire-and-forget)
        if self._telemetry_buffer and pos.signal_id:
            try:
                import asyncio
                _snap = indicators_snapshot or {}
                _now = datetime.now(timezone.utc)
                coro = self._telemetry_buffer.capture(
                    signal_id=pos.signal_id,
                    ticker=pos.ticker,
                    vpin=float(_snap.get("vpin", 0) or 0),
                    ofi=float(_snap.get("ofi", 0) or 0),
                    micro_price=float(_snap.get("micro_price", 0) or 0),
                    bid_ask_spread_bps=float(_snap.get("spread_bps", 0) or 0),
                    kelly_fraction=float(_snap.get("kelly_fraction", 0) or 0),
                    position_size=pos.shares,
                    hawkes_intensity=float(_snap.get("hawkes_intensity", 0) or 0),
                    regime=pos.regime_at_entry,
                    vix=pos.vix_at_entry,
                    hour_of_day=_now.hour,
                    day_of_week=_now.weekday(),
                    time_window=pos.time_window,
                )
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(coro)
                else:
                    asyncio.run(coro)
            except Exception as _tel_err:
                self.logger.debug("V9.5 telemetry capture failed (non-fatal): %s", _tel_err)

        self.logger.info(
            "VIRTUAL OPEN: %s %s %d shares @ %.2f (slip: %.4f, comm: %.2f)",
            pos.direction, pos.ticker, pos.shares, pos.entry_price,
            entry_slip, pos.commission,
        )

        return pos

    def update_prices(self, price_data: dict[str, float], current_regime: str = "") -> list[dict]:
        """30-second price update cycle.

        Args:
            price_data: Dict of ticker -> current_price
            current_regime: Current regime state string

        Returns:
            List of events (ladder transitions, closes, alerts)
        """
        with self._lock:
            return self._update_prices_locked(price_data, current_regime)

    def _update_prices_locked(self, price_data: dict[str, float], current_regime: str = "") -> list[dict]:
        """Internal price update (must be called under lock)."""
        events = []
        positions_to_close = []

        for pos_id, pos in list(self.open_positions.items()):
            if pos.status != "OPEN":
                continue

            price = price_data.get(pos.ticker)
            if price is None or price <= 0:
                continue

            # Update P&L
            risk = pos.risk_dollars / pos.shares if pos.shares > 0 else 1.0
            remaining_shares = int(pos.shares * pos.remaining_pct)

            if pos.direction == "LONG":
                pos.unrealised_pnl = (price - pos.entry_price) * remaining_shares
                pos.unrealised_r = (price - pos.entry_price) / risk if risk > 0 else 0
            else:
                pos.unrealised_pnl = (pos.entry_price - price) * remaining_shares
                pos.unrealised_r = (pos.entry_price - price) / risk if risk > 0 else 0

            # 5-03: Include financing drag estimate for overnight positions
            if hasattr(pos, 'entry_time') and pos.entry_time:
                now = datetime.now(timezone.utc)
                from config.universe_constants import INVERSE_ETPS_SET
                _is_inv = pos.ticker in INVERSE_ETPS_SET
                notional = pos.entry_price * remaining_shares
                drag = self._calculate_financing_drag(
                    pos.ticker, pos.entry_time, now, notional, is_inverse=_is_inv,
                )
                pos.unrealised_pnl += drag

            # Track MFE/MAE
            pos.peak_r = max(pos.peak_r, pos.unrealised_r)
            pos.trough_r = min(pos.trough_r, pos.unrealised_r)

            # Phase 5: Track highest_high / lowest_low for Chandelier Exit
            if pos.direction == "LONG":
                pos.highest_high = max(pos.highest_high or pos.entry_price, price)
            else:
                pos.lowest_low = min(pos.lowest_low or pos.entry_price, price) if (pos.lowest_low > 0) else min(pos.entry_price, price)

            # Phase 20: Flash crash detection — if underlying drops > 0.5% in a single update
            if pos.previous_price > 0 and pos.direction == "LONG":
                single_update_drop = (pos.previous_price - price) / pos.previous_price
                if single_update_drop > 0.005:  # > 0.5% drop in one tick
                    hedge_event = self._trigger_flash_crash_hedge(pos, single_update_drop, price)
                    if hedge_event:
                        events.append(hedge_event)
            pos.previous_price = price

            # Phase 13.2: Track prices for PCA eigen risk
            hist = self._price_history.setdefault(pos.ticker, [])
            hist.append(price)
            if len(hist) > 50:
                self._price_history[pos.ticker] = hist[-50:]

            # Check stop hit
            if pos.direction == "LONG" and price <= pos.current_stop:
                stop_slip = self.slippage_model.stop_slippage(pos)
                exit_price = pos.current_stop + stop_slip
                positions_to_close.append((pos_id, exit_price, "STOP_HIT"))
                events.append({"type": "STOP_HIT", "position": pos_id, "ticker": pos.ticker})
                continue
            elif pos.direction == "SHORT" and price >= pos.current_stop:
                stop_slip = self.slippage_model.stop_slippage(pos)
                exit_price = pos.current_stop + stop_slip
                positions_to_close.append((pos_id, exit_price, "STOP_HIT"))
                events.append({"type": "STOP_HIT", "position": pos_id, "ticker": pos.ticker})
                continue

            # Time-Decay Pressure (v5 Section 10.3):
            # Positions below +0.5R after 60 min = EXIT at market
            # Prevents dead-money positions tying up capital
            if pos.bot_instance != "SWING":
                hold_minutes = (datetime.now(timezone.utc) - pos.entry_time).total_seconds() / 60
                if hold_minutes >= 60 and pos.unrealised_r < 0.5 and pos.ladder_rung < 2:
                    positions_to_close.append((pos_id, price, "TIME_DECAY_PRESSURE"))
                    events.append({
                        "type": "TIME_DECAY", "position": pos_id, "ticker": pos.ticker,
                        "hold_min": int(hold_minutes), "r": round(pos.unrealised_r, 2),
                    })
                    self.logger.warning(
                        "TIME DECAY: %s held %dmin at %.2fR — forcing exit",
                        pos.ticker, int(hold_minutes), pos.unrealised_r,
                    )
                    continue

            # Phase 6: Information Decay Time-Stop — 45min with < 0.5% move and no rung
            if pos.bot_instance != "SWING":
                hold_minutes = (datetime.now(timezone.utc) - pos.entry_time).total_seconds() / 60
                pct_move = abs(price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
                if hold_minutes >= 45 and pct_move < 0.005 and pos.ladder_rung < 1:
                    positions_to_close.append((pos_id, price, "EDGE_DECAY_45MIN"))
                    events.append({
                        "type": "EDGE_DECAY", "position": pos_id, "ticker": pos.ticker,
                        "hold_min": int(hold_minutes), "pct_move": round(pct_move * 100, 2),
                    })
                    self.logger.warning(
                        "EDGE DECAY: %s held %dmin at %.2f%% move, rung %d — forcing exit",
                        pos.ticker, int(hold_minutes), pct_move * 100, pos.ladder_rung,
                    )
                    continue

            # Phase 40: Regime-aware holding period
            if pos.bot_instance != "SWING" and current_regime:
                hold_minutes = (datetime.now(timezone.utc) - pos.entry_time).total_seconds() / 60
                regime_max_hold = self._get_regime_adjusted_time_stop(pos, current_regime)
                if hold_minutes >= regime_max_hold and pos.ladder_rung < 1:
                    positions_to_close.append((pos_id, price, "REGIME_TIME_STOP"))
                    events.append({
                        "type": "REGIME_TIME_STOP", "position": pos_id, "ticker": pos.ticker,
                        "hold_min": int(hold_minutes), "regime": current_regime,
                        "max_hold": regime_max_hold,
                    })
                    self.logger.warning(
                        "REGIME_TIME_STOP: %s held %dmin in %s regime (max=%dmin) — forcing exit",
                        pos.ticker, int(hold_minutes), current_regime, regime_max_hold,
                    )
                    continue

            # Phase 29: Volume-clock time-stop — if 30% of ADV traded since entry
            if pos.entry_underlying_vol > 0 and pos.bot_instance != "SWING":
                pct_move = abs(price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
                try:
                    from uk_isa.isa_universe import UNDERLYING_INDEX
                    import yfinance as yf
                    underlying = UNDERLYING_INDEX.get(pos.ticker)
                    if underlying:
                        u_data = yf.download(underlying, period="1d", interval="1m", progress=False, timeout=5)
                        if u_data is not None and len(u_data) > 0:
                            cumulative_vol = float(u_data["Volume"].sum())
                            vol_since_entry = max(0, cumulative_vol - pos.entry_underlying_vol)
                            u_daily = yf.download(underlying, period="10d", interval="1d", progress=False, timeout=5)
                            if u_daily is not None and len(u_daily) >= 5:
                                avg_daily_vol = float(u_daily["Volume"].mean())
                                if avg_daily_vol > 0:
                                    vol_pct = vol_since_entry / avg_daily_vol
                                    if vol_pct > 0.30 and pct_move < 0.005 and pos.ladder_rung < 1:
                                        positions_to_close.append((pos_id, price, "VOLUME_CLOCK_STOP"))
                                        events.append({
                                            "type": "VOLUME_CLOCK_STOP", "position": pos_id,
                                            "ticker": pos.ticker, "vol_pct": round(vol_pct * 100, 1),
                                        })
                                        self.logger.warning(
                                            "VOLUME_CLOCK: %s — %.0f%% of ADV traded, price at %.2f%% — scratching",
                                            pos.ticker, vol_pct * 100, pct_move * 100,
                                        )
                                        continue
                except Exception as vol_err:
                    self.logger.debug("VOLUME_CLOCK: %s lookup failed: %s", pos.ticker, vol_err)

            # Run profit ladder
            ladder_events = self._run_profit_ladder(pos, price)
            events.extend(ladder_events)

            # Check if ladder signalled a close (e.g. ETP rung 3 at +5%)
            for le in ladder_events:
                if le.get("close"):
                    exit_price = le.get("exit_price", price)
                    positions_to_close.append((pos_id, exit_price, "ETP_TARGET_5%"))
                    break

            # Check max hold time
            hold_minutes = (datetime.now(timezone.utc) - pos.entry_time).total_seconds() / 60
            max_hold = 480 if pos.bot_instance != "SWING" else 14400  # 8h or 10 days
            if hold_minutes > max_hold:
                positions_to_close.append((pos_id, price, "TIME_EXPIRED"))
                events.append({"type": "TIME_EXPIRED", "position": pos_id, "ticker": pos.ticker})

            # Overnight ETP Protection: Never hold 3x/5x leveraged ETPs overnight
            # unless explicitly in a swing trade. Overnight gap risk on 5x = account-destroying.
            # Close all leveraged ETP positions after 15:55 ET (5 min before close).
            if pos.bot == "A" and pos.bot_instance != "SWING":
                now_utc = datetime.now(timezone.utc)
                # Use proper timezone conversion for accurate ET time
                try:
                    from zoneinfo import ZoneInfo
                    from core.clock import ET_TZ
                    et_now = now_utc.astimezone(ET_TZ)
                    et_hour = et_now.hour
                    et_minute = et_now.minute
                except Exception:
                    # Fallback: rough EST offset (UTC-5)
                    et_hour = now_utc.hour - 5
                    if et_hour < 0:
                        et_hour += 24
                    et_minute = now_utc.minute
                # Close at 15:55 ET or later (5 min before market close)
                if (et_hour == 15 and et_minute >= 55) or et_hour >= 16:
                    positions_to_close.append((pos_id, price, "ETP_OVERNIGHT_PROTECTION"))
                    events.append({
                        "type": "ETP_OVERNIGHT", "position": pos_id, "ticker": pos.ticker,
                        "reason": "Leveraged ETP must not be held overnight",
                    })
                    self.logger.warning(
                        "ETP OVERNIGHT PROTECTION: Closing %s %s at %02d:%02d ET "
                        "— no leveraged overnight holds",
                        pos.ticker, pos.direction, et_hour, et_minute,
                    )

            # Phase 14: Volatility drag defense
            # 5x overnight kill: if ticker is 5x and past 16:15 UK local, force close
            # Fixes C-03: was checking UTC time, now uses UK local via clock.py
            if pos.ticker in FIVE_X_TICKERS and pos.bot_instance != "SWING":
                from core.clock import is_past_5x_kill_time, now_uk
                if is_past_5x_kill_time():
                    if pos_id not in [p[0] for p in positions_to_close]:
                        _now_uk = now_uk()
                        positions_to_close.append((pos_id, price, "5X_OVERNIGHT_KILL"))
                        events.append({
                            "type": "VOL_DRAG_5X", "position": pos_id, "ticker": pos.ticker,
                            "reason": "5x product must close before overnight — volatility drag",
                        })
                        self.logger.warning(
                            "5X_OVERNIGHT_KILL: %s forced close at %02d:%02d UK",
                            pos.ticker, _now_uk.hour, _now_uk.minute,
                        )

            # F-09: Overnight kill ALL leveraged ETPs (2x+) during paper/limited live
            # Ruin math assumes 0.75% max loss, but overnight gap on 3x = 5-15% portfolio loss.
            # Previously only killed 3x+ when VIX > 30; now unconditional for ALL leveraged .L tickers.
            ticker_leverage = get_abs_leverage(pos.ticker, default=1.0)
            if ticker_leverage >= 2.0 and pos.bot_instance != "SWING":
                from core.clock import is_past_5x_kill_time
                past_1615 = is_past_5x_kill_time()  # UK local time check
                if past_1615:
                    if pos_id not in [p[0] for p in positions_to_close]:
                        positions_to_close.append((pos_id, price, "LEVERAGED_OVERNIGHT_KILL"))
                        current_vix = price_data.get("^VIX", pos.vix_at_entry) or 0.0
                        events.append({
                            "type": "LEVERAGED_OVERNIGHT_KILL", "position": pos_id, "ticker": pos.ticker,
                            "vix": current_vix, "leverage": ticker_leverage,
                            "reason": f"{ticker_leverage:.0f}x leveraged ETP — no overnight holds (F-09)",
                        })
                        self.logger.warning(
                            "LEVERAGED_OVERNIGHT_KILL: %s (%.0fx) — forced close at 16:15 UK (F-09)",
                            pos.ticker, ticker_leverage,
                        )

            # Emotional Firewall: Holding Losers (pattern 4)
            # At -1R for > 5 min = FORCE EXIT
            if pos.unrealised_r <= -1.0:
                hold_minutes = (datetime.now(timezone.utc) - pos.entry_time).total_seconds() / 60
                if hold_minutes >= 5 and pos.ladder_rung == 0:
                    positions_to_close.append((pos_id, price, "FIREWALL_HOLDING_LOSER"))
                    events.append({
                        "type": "FIREWALL", "pattern": "HOLDING_LOSERS",
                        "position": pos_id, "ticker": pos.ticker,
                        "r": round(pos.unrealised_r, 2),
                    })
                    try:
                        from delivery.database import insert_firewall_event, transaction
                        with transaction() as fconn:
                            insert_firewall_event(fconn, {
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "pattern": "HOLDING_LOSERS",
                                "signal_ticker": pos.ticker,
                                "signal_direction": pos.direction,
                                "signal_strategy": pos.strategy,
                                "signal_confidence": pos.confidence,
                                "action_taken": "FORCE_EXIT",
                                "reason": f"At {pos.unrealised_r:.2f}R for >{int(hold_minutes)}min",
                            })
                    except Exception:
                        pass
                    continue

            # Emotional Firewall: Refusing Profits (pattern 8)
            # At +2R with no partial taken = AUTO partial 50%
            if pos.unrealised_r >= 2.0 and pos.ladder_rung < 3 and pos.remaining_pct >= 0.95:
                remaining_shares = int(pos.shares * pos.remaining_pct)
                sold_shares = int(remaining_shares * 0.50)
                if sold_shares > 0:
                    if pos.direction == "LONG":
                        pnl = sold_shares * (price - pos.entry_price)
                    else:
                        pnl = sold_shares * (pos.entry_price - price)
                    pos.partials.append({
                        "rung": "FIREWALL", "shares": sold_shares,
                        "price": price, "pnl": pnl,
                    })
                    pos.remaining_pct = max(0.0, (remaining_shares - sold_shares) / pos.shares)
                    pos.current_stop = pos.entry_price  # At least breakeven
                    events.append({
                        "type": "FIREWALL", "pattern": "REFUSING_PROFITS",
                        "position": pos_id, "ticker": pos.ticker,
                        "r": round(pos.unrealised_r, 2), "sold": sold_shares,
                    })
                    self.logger.warning(
                        "FIREWALL REFUSING_PROFITS: %s at +%.1fR with no partials — auto-sold %d shares",
                        pos.ticker, pos.unrealised_r, sold_shares,
                    )
                    try:
                        from delivery.database import insert_firewall_event, transaction
                        with transaction() as fconn:
                            insert_firewall_event(fconn, {
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "pattern": "REFUSING_PROFITS",
                                "signal_ticker": pos.ticker,
                                "signal_direction": pos.direction,
                                "signal_strategy": pos.strategy,
                                "signal_confidence": pos.confidence,
                                "action_taken": "AUTO_PARTIAL_50%",
                                "reason": f"At +{pos.unrealised_r:.1f}R with no partials — sold {sold_shares} shares",
                            })
                    except Exception:
                        pass

            # Check regime flip
            if current_regime and pos.regime_at_entry:
                if self._is_regime_flip(pos.regime_at_entry, current_regime, pos.direction):
                    # Regime flipped against position — close
                    positions_to_close.append((pos_id, price, "REGIME_FLIP"))
                    events.append({"type": "REGIME_FLIP", "position": pos_id, "ticker": pos.ticker})

            # Persist update
            if self._db:
                self._update_position_db(pos)

        # Deduplicate positions_to_close by position_id (first occurrence wins)
        seen = set()
        deduped = []
        for item in positions_to_close:
            if item[0] not in seen:
                seen.add(item[0])
                deduped.append(item)
        positions_to_close = deduped

        # Close positions
        for pos_id, exit_price, reason in positions_to_close:
            trade = self.close_position(pos_id, exit_price, reason, current_regime)
            if trade:
                events.append({
                    "type": "TRADE_CLOSED",
                    "trade_id": trade.id,
                    "ticker": trade.ticker,
                    "r_multiple": trade.r_multiple,
                    "net_pnl": trade.net_pnl,
                    "reason": reason,
                })

        return events

    def _run_profit_ladder(self, pos: VirtualPosition, price: float) -> list[dict]:
        """Run profit ladder — delegates to ChandelierExit (AEGIS C-02).

        When ChandelierExit is attached (C-03), it is the SOLE AUTHORITY for:
        - Trailing stop computation (Le Beau 1999)
        - Profit rung transitions
        - Partial banking signals (C-04)

        The old inline 7-rung ladder and ETP ladder are fully replaced.
        Chandelier handles both Bot A (ETP) and Bot B (stock) positions
        via leverage-adjusted ATR multipliers.
        """
        # C-02: Delegate to Chandelier Exit — sole profit ladder authority
        if self._chandelier:
            return self._run_chandelier_ladder(pos, price)

        # Fallback: no chandelier attached (should not happen in production)
        self.logger.warning(
            "PROFIT_LADDER_FALLBACK: %s — no ChandelierExit attached, skipping ladder",
            pos.ticker,
        )
        return []

    def _run_chandelier_ladder(self, pos: VirtualPosition, price: float) -> list[dict]:
        """Delegate to main chandelier exit if available.

        Chandelier updates are handled by main.py reconciliation loop.
        This stub exists so _run_profit_ladder can call it without AttributeError.
        """
        # Chandelier updates handled by main.py reconciliation loop — no-op here
        return []

    def _run_etp_ladder(self, pos: VirtualPosition, price: float, r_current: float) -> list[dict]:
        """Revolutionary 2% Increment Profit Ratchet for leveraged ETPs.

        Research basis:
        - Trailing stops outperform fixed exits (Nordic stocks study: 73.91% vs 46.44%)
        - Chandelier Exit (Le Beau / Elder): ATR-based trailing from highest high
        - Ratchet method: lock in gains at each 2% threshold, trail below last rung

        CORE TICKERS use the full ratchet:
        | Rung | Move  | Action                | Stop Position         | Remaining |
        |------|-------|-----------------------|-----------------------|-----------|
        | 0    | Entry | Full position          | -1.0% (3x) / -0.75% (5x) | 100% |
        | 1    | +1%   | Move stop to breakeven | Entry price           | 100%      |
        | 2    | +2%   | Sell 25%, lock in      | +1% from entry        | 75%       |
        | 3    | +4%   | Sell 25%, lock in      | +3% from entry        | 50%       |
        | 4    | +6%   | Sell 25%, lock in      | +5% from entry        | 25%       |
        | 5    | +8%   | Evaluate: trail/lock   | +7% from entry        | 25%       |
        | 6    | +10%+ | Runner: 1.5% Chandelier| Price - 1.5%          | 25%       |

        WHALE MODE: RVOL > 2.0 + trending regime → skip partial sells, keep 100% with 2% trail
        """
        events = []
        entry = pos.entry_price

        if pos.direction == "LONG":
            pct_move = (price - entry) / entry
        else:
            pct_move = (entry - price) / entry

        # Use canonical universe from isa_universe (Phase 1: single source of truth)
        from uk_isa.isa_universe import CORE_UNIVERSE
        is_core = pos.ticker in CORE_UNIVERSE

        # Check for WHALE MODE: RVOL > 2.0 + trending regime → hold full position with trail
        rvol_entry = 0.0
        try:
            import json
            snap = pos.indicator_snapshot_entry
            if isinstance(snap, str):
                snap = json.loads(snap)
            if isinstance(snap, dict):
                rvol_entry = snap.get("rvol", 0) or 0
        except Exception:
            pass

        regime_trending = pos.regime_at_entry in ("TRENDING_UP_STRONG", "TRENDING_UP_MOD")
        whale_mode = is_core and rvol_entry >= 2.0 and regime_trending

        # === RUNG 1: +1% → breakeven (same as before) ===
        if pos.ladder_rung < 1 and pct_move >= 0.01:
            pos.current_stop = entry
            pos.ladder_rung = 1
            events.append({"type": "ETP_LADDER", "rung": 1, "action": "BREAKEVEN_AT_1%", "ticker": pos.ticker})
            logger.info("ETP_LADDER rung 1 BREAKEVEN_AT_1%% — %s %.2f%% move", pos.ticker, pct_move * 100)

        # === RUNG 2: +2% → LOCK IN: sell 25% (or hold in whale mode) ===
        if pos.ladder_rung < 2 and pct_move >= 0.02:
            if pos.direction == "LONG":
                pos.current_stop = max(pos.current_stop, entry * 1.01)  # Lock stop at +1%
            else:
                pos.current_stop = min(pos.current_stop, entry * 0.99)

            if not whale_mode:
                remaining_shares = int(pos.shares * pos.remaining_pct)
                sold_shares = max(1, int(pos.shares * 0.25))
                sold_shares = min(sold_shares, remaining_shares)
                if sold_shares > 0:
                    pnl = sold_shares * (price - entry) if pos.direction == "LONG" else sold_shares * (entry - price)
                    pos.partials.append({"rung": 2, "shares": sold_shares, "price": price, "pnl": pnl})
                    pos.remaining_pct = max(0.0, (remaining_shares - sold_shares) / pos.shares)
                events.append({"type": "ETP_LADDER", "rung": 2, "action": "LOCK_IN_25%_AT_2%", "ticker": pos.ticker})
            else:
                events.append({"type": "ETP_LADDER", "rung": 2, "action": "WHALE_MODE_HOLD_100%_AT_2%", "ticker": pos.ticker})

            pos.ladder_rung = 2
            logger.info("ETP_LADDER rung 2 %s — %s %.2f%% whale=%s",
                        "WHALE_HOLD" if whale_mode else "LOCK_IN_25%", pos.ticker, pct_move * 100, whale_mode)

        # === RUNG 3: +4% → LOCK IN: sell 25% (or trail in whale mode) ===
        if pos.ladder_rung < 3 and pct_move >= 0.04:
            if pos.direction == "LONG":
                pos.current_stop = max(pos.current_stop, entry * 1.03)  # Lock stop at +3%
            else:
                pos.current_stop = min(pos.current_stop, entry * 0.97)

            if not whale_mode:
                remaining_shares = int(pos.shares * pos.remaining_pct)
                sold_shares = max(1, int(pos.shares * 0.25))
                sold_shares = min(sold_shares, remaining_shares)
                if sold_shares > 0:
                    pnl = sold_shares * (price - entry) if pos.direction == "LONG" else sold_shares * (entry - price)
                    pos.partials.append({"rung": 3, "shares": sold_shares, "price": price, "pnl": pnl})
                    pos.remaining_pct = max(0.0, (remaining_shares - sold_shares) / pos.shares)
                events.append({"type": "ETP_LADDER", "rung": 3, "action": "LOCK_IN_25%_AT_4%", "ticker": pos.ticker})
            else:
                # Whale mode: update 2% trail
                if pos.direction == "LONG":
                    pos.current_stop = max(pos.current_stop, price * 0.98)
                else:
                    pos.current_stop = min(pos.current_stop, price * 1.02)
                events.append({"type": "ETP_LADDER", "rung": 3, "action": "WHALE_MODE_TRAIL_2%_AT_4%", "ticker": pos.ticker})

            pos.ladder_rung = 3
            logger.info("ETP_LADDER rung 3 %s — %s %.2f%%",
                        "WHALE_TRAIL" if whale_mode else "LOCK_IN_25%_AT_4%", pos.ticker, pct_move * 100)

        # === RUNG 4: +6% → LOCK IN: sell 25% ===
        if pos.ladder_rung < 4 and pct_move >= 0.06:
            if pos.direction == "LONG":
                pos.current_stop = max(pos.current_stop, entry * 1.05)  # Lock stop at +5%
            else:
                pos.current_stop = min(pos.current_stop, entry * 0.95)

            if not whale_mode:
                remaining_shares = int(pos.shares * pos.remaining_pct)
                sold_shares = max(1, int(pos.shares * 0.25))
                sold_shares = min(sold_shares, remaining_shares)
                if sold_shares > 0:
                    pnl = sold_shares * (price - entry) if pos.direction == "LONG" else sold_shares * (entry - price)
                    pos.partials.append({"rung": 4, "shares": sold_shares, "price": price, "pnl": pnl})
                    pos.remaining_pct = max(0.0, (remaining_shares - sold_shares) / pos.shares)
                events.append({"type": "ETP_LADDER", "rung": 4, "action": "LOCK_IN_25%_AT_6%", "ticker": pos.ticker})
            else:
                if pos.direction == "LONG":
                    pos.current_stop = max(pos.current_stop, price * 0.98)
                else:
                    pos.current_stop = min(pos.current_stop, price * 1.02)
                events.append({"type": "ETP_LADDER", "rung": 4, "action": "WHALE_MODE_TRAIL_2%_AT_6%", "ticker": pos.ticker})

            pos.ladder_rung = 4
            logger.info("ETP_LADDER rung 4 — %s %.2f%% remaining=%.0f%%",
                        pos.ticker, pct_move * 100, pos.remaining_pct * 100)

        # === RUNG 5: +8% → Evaluate: trail or lock final 25% ===
        if pos.ladder_rung < 5 and pct_move >= 0.08:
            if pos.direction == "LONG":
                pos.current_stop = max(pos.current_stop, entry * 1.07)  # Lock stop at +7%
            else:
                pos.current_stop = min(pos.current_stop, entry * 0.93)
            pos.ladder_rung = 5
            events.append({"type": "ETP_LADDER", "rung": 5, "action": "RUNNER_ENGAGED_AT_8%", "ticker": pos.ticker})
            logger.info("ETP_LADDER rung 5 RUNNER_ENGAGED — %s %.2f%% 🐋 riding the wave",
                        pos.ticker, pct_move * 100)

        # === RUNG 6: +10%+ → Chandelier trailing stop (1.5% from highest) ===
        if pos.ladder_rung < 6 and pct_move >= 0.10:
            pos.ladder_rung = 6
            events.append({"type": "ETP_LADDER", "rung": 6, "action": "CHANDELIER_TRAIL_1.5%_AT_10%", "ticker": pos.ticker})
            logger.info("ETP_LADDER rung 6 CHANDELIER — %s %.2f%% trailing 1.5%% from peak",
                        pos.ticker, pct_move * 100)

        # === TRAILING STOP UPDATES (every tick for active runners) ===
        if pos.ladder_rung >= 5 and pos.remaining_pct > 0:
            # Rung 5+: 1.5% Chandelier trailing stop from highest price
            trail_pct = 0.015  # 1.5% Chandelier trail (Le Beau / Elder)
            if pos.direction == "LONG":
                new_stop = price * (1 - trail_pct)
                if new_stop > pos.current_stop:
                    pos.current_stop = new_stop
            else:
                new_stop = price * (1 + trail_pct)
                if new_stop < pos.current_stop:
                    pos.current_stop = new_stop
        elif pos.ladder_rung >= 2 and pos.remaining_pct > 0 and whale_mode:
            # Whale mode: 2% trailing stop even on early rungs
            if pos.direction == "LONG":
                new_stop = price * 0.98
                if new_stop > pos.current_stop:
                    pos.current_stop = new_stop
            else:
                new_stop = price * 1.02
                if new_stop < pos.current_stop:
                    pos.current_stop = new_stop

        return events

    def _is_regime_flip(self, entry_regime: str, current_regime: str, direction: str = "LONG") -> bool:
        """Check if regime has flipped against the position.

        Args:
            entry_regime: Regime state when position was opened.
            current_regime: Current regime state.
            direction: Position direction ("LONG" or "SHORT").
        """
        up_regimes = {"TRENDING_UP_STRONG", "TRENDING_UP_MOD"}
        down_regimes = {"TRENDING_DOWN_STRONG", "TRENDING_DOWN_MOD", "RISK_OFF", "SHOCK"}

        if entry_regime in up_regimes and current_regime in down_regimes:
            return True
        if entry_regime in down_regimes and current_regime in up_regimes:
            return True
        # SHOCK only closes LONG positions; SHORT positions benefit from shock
        if current_regime == "SHOCK" and direction == "LONG":
            return True
        return False

    def _calculate_financing_drag(self, ticker: str, entry_time, exit_time,
                                   notional: float, is_inverse: bool = False) -> float:
        """G-14: Calculate financing drag ONLY for overnight holds.

        Leveraged ETPs incur daily financing costs:
        - Long leveraged (3x/5x): ~2 bps/day
        - Inverse ETPs: ~4 bps/day

        Only applied if position crosses the overnight boundary (16:30 UK).
        Intraday trades have zero financing drag.

        Returns:
            Financing cost in currency units (always negative or zero).
        """
        if entry_time is None or exit_time is None:
            return 0.0

        # Check if position crossed overnight
        try:
            from zoneinfo import ZoneInfo
            uk_tz = ZoneInfo("Europe/London")
        except ImportError:
            import pytz
            uk_tz = pytz.timezone("Europe/London")

        entry_uk = entry_time.astimezone(uk_tz) if entry_time.tzinfo else entry_time
        exit_uk = exit_time.astimezone(uk_tz) if exit_time.tzinfo else exit_time

        # Count overnight boundaries crossed
        # Overnight = position held past 16:30 UK
        from datetime import time as dtime
        overnight_cutoff = dtime(16, 30)

        nights = 0
        current = entry_uk.date()
        end_date = exit_uk.date()
        while current < end_date:
            # Check if this was a trading day (weekday)
            if current.weekday() < 5:  # Mon-Fri
                nights += 1
            current += timedelta(days=1)

        # Also check if entry was before 16:30 and exit after 16:30 on same day
        if (entry_uk.date() == exit_uk.date() and
            entry_uk.time() < overnight_cutoff and
            exit_uk.time() > overnight_cutoff):
            nights += 1

        if nights == 0:
            return 0.0  # Intraday trade — no financing drag

        # Apply financing cost
        daily_drag_bps = 4.0 if is_inverse else 2.0  # bps per day
        total_drag = notional * (daily_drag_bps / 10000.0) * nights

        self.logger.info(
            "G-14 FINANCING_DRAG: %s | nights=%d | drag=%.2f | "
            "rate=%.0f bps/day | notional=%.0f | inverse=%s",
            ticker, nights, total_drag, daily_drag_bps, notional, is_inverse,
        )

        return -total_drag  # Always negative (cost)

    def close_position(
        self, position_id: str, exit_price: float, reason: str,
        regime_at_exit: str = "", exit_indicators: dict = None,
    ) -> Optional[VirtualTrade]:
        """Close a virtual position and create a trade record."""
        pos = self.open_positions.get(position_id)
        if not pos or pos.status != "OPEN":
            return None

        pos.status = "CLOSED"
        remaining_shares = int(pos.shares * pos.remaining_pct)

        # Calculate P&L
        if pos.direction == "LONG":
            gross_pnl = (exit_price - pos.entry_price) * remaining_shares
        else:
            gross_pnl = (pos.entry_price - exit_price) * remaining_shares

        # Add partial profits
        partial_pnl = sum(p.get("pnl", 0) for p in pos.partials)
        total_gross = gross_pnl + partial_pnl

        # G-14: Financing drag for overnight holds on leveraged ETPs
        exit_now = datetime.now(timezone.utc)
        notional = pos.entry_price * pos.shares
        from config.universe_constants import INVERSE_ETPS_SET
        _is_inverse = pos.ticker in INVERSE_ETPS_SET
        financing_drag = self._calculate_financing_drag(
            pos.ticker, pos.entry_time, exit_now, notional, is_inverse=_is_inverse,
        )

        net_pnl = total_gross - pos.commission - pos.slippage + financing_drag

        # R-multiple
        r_multiple = net_pnl / pos.risk_dollars if pos.risk_dollars > 0 else 0

        # Missed gain/loss tracking
        # missed_gain_by_early_exit: If peak_r > exit_r, we left money on the table
        # = (peak_r - exit_r) * risk_dollars — potential gain we missed by exiting too early
        exit_r = r_multiple
        missed_gain = max(0, (pos.peak_r - exit_r)) * pos.risk_dollars if pos.risk_dollars > 0 else 0

        # missed_loss_by_late_exit: If peak_r was positive but we exited at a loss or lower R
        # = (peak_r - exit_r) * risk_dollars when exit_r < peak_r and peak_r > 0
        # This captures "we were winning but held too long and gave it back"
        missed_loss = 0.0
        if pos.peak_r > 0 and exit_r < pos.peak_r:
            missed_loss = (pos.peak_r - exit_r) * pos.risk_dollars if pos.risk_dollars > 0 else 0

        # Entry/exit quality
        stop_distance = abs(pos.entry_price - pos.initial_stop)
        entry_quality = 100 * (1 - abs(pos.trough_r) / 1.0) if stop_distance > 0 else 50
        exit_quality = 100 * (pos.unrealised_r / pos.peak_r) if pos.peak_r > 0 else 50
        entry_quality = max(0, min(100, entry_quality))
        exit_quality = max(0, min(100, exit_quality))

        # M-06: Entry Timing Score — measures entry quality relative to position range
        # For LONG: ETS near 0 = entered near bottom = good timing
        # For SHORT: ETS near 0 = entered near top = good timing (inverted)
        _ets_low = pos.lowest_low if pos.lowest_low < float("inf") else pos.entry_price
        _ets_high = pos.highest_high
        _ets_range = max(_ets_high - _ets_low, 1e-6)  # Guard ZeroDivisionError
        _ets_raw = (pos.entry_price - _ets_low) / _ets_range
        # Invert for SHORT: entering near the top is good (raw≈1 → ETS≈0)
        _ets = (1.0 - _ets_raw) if pos.direction == "SHORT" else _ets_raw
        _ets = max(0.0, min(1.0, _ets))  # Clamp [0, 1]
        self.logger.info(
            "ETS: %s %s entry_timing_score=%.3f (entry=%.2f range=[%.2f, %.2f])",
            pos.direction, pos.ticker, _ets, pos.entry_price, _ets_low, _ets_high,
        )

        # Duration
        duration = (datetime.now(timezone.utc) - pos.entry_time).total_seconds() / 60

        # Failure categorisation for losers
        failure = ""
        if r_multiple < 0:
            failure = self._categorise_failure(pos, exit_price, reason)

        # Create trade record
        trade = VirtualTrade(
            id=f"VT-{str(uuid.uuid4())[:8]}",
            position_id=pos.id,
            signal_id=pos.signal_id,
            bot=pos.bot,
            bot_instance=pos.bot_instance,
            ticker=pos.ticker,
            direction=pos.direction,
            strategy=pos.strategy,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            entry_time=pos.entry_time.isoformat(),
            exit_time=datetime.now(timezone.utc).isoformat(),
            shares=pos.shares,
            risk_dollars=pos.risk_dollars,
            gross_pnl=total_gross,
            net_pnl=net_pnl,
            commission=pos.commission,
            slippage=pos.slippage,
            financing_drag=financing_drag,
            r_multiple=r_multiple,
            entry_quality=entry_quality,
            exit_quality=exit_quality,
            exit_reason=reason,
            duration_minutes=int(duration),
            regime_at_entry=pos.regime_at_entry,
            regime_at_exit=regime_at_exit,
            confidence=pos.confidence,
            indicator_snapshot_entry=pos.indicator_snapshot,
            indicator_snapshot_exit=exit_indicators or {},
            peak_r=pos.peak_r,
            trough_r=pos.trough_r,
            partials=pos.partials,
            failure_category=failure,
        )

        # Set missed opportunity tracking
        trade.missed_gain_by_early_exit = missed_gain
        trade.missed_loss_by_late_exit = missed_loss

        # Update running P&L
        self.daily_pnl += net_pnl
        self.total_realised_pnl += net_pnl
        self.equity += net_pnl

        self.closed_trades.append(trade)
        self._all_trades.append(trade)  # Phase 9: track all trades for total P&L kill switch
        if len(self.closed_trades) > 1000:
            self.closed_trades = self.closed_trades[-1000:]

        # Phase 21: TCA feedback — predicted vs actual execution cost
        try:
            order_value = pos.entry_price * pos.shares
            if order_value > 0:
                predicted_bps = round_trip_cost_bps(pos.ticker, order_value=order_value)
                actual_bps = (pos.slippage + pos.commission) / order_value * 10_000
                _tca_engine.record(predicted_bps, actual_bps)
        except Exception:
            pass

        # Persist FIRST — audit trail before state change (C-16)
        # Only delete from dict if DB write succeeds (or no DB configured).
        # If DB insert fails, position stays in dict so data is never lost.
        db_ok = True
        if self._db:
            db_ok = self._persist_trade(trade)

        if db_ok:
            del self.open_positions[position_id]

            # A-09: Notify circuit breaker of stop-out for anti-cascade detection
            if "STOP" in reason.upper() and hasattr(self, '_circuit_breakers') and self._circuit_breakers:
                try:
                    self._circuit_breakers.record_stopout(pos.ticker)
                except Exception as e:
                    self.logger.warning("A-09: record_stopout failed: %s", e)
        else:
            # DB write failed — roll back in-memory state to avoid data loss (C-16).
            pos.status = "OPEN"
            self.daily_pnl -= net_pnl
            self.total_realised_pnl -= net_pnl
            self.equity -= net_pnl
            if trade in self.closed_trades:
                self.closed_trades.remove(trade)
            # _all_trades is a bounded deque — pop the last appended trade on rollback
            if self._all_trades and self._all_trades[-1] is trade:
                self._all_trades.pop()
            self.logger.error(
                "C-16 SAFETY: position %s kept in dict — DB persist failed, will retry on next close",
                position_id,
            )
            return None

        # V9.5 Phase 2: Retrieve telemetry snapshot and attach to trade for learning
        if self._telemetry_buffer and pos.signal_id:
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    _tel_task = loop.create_task(
                        self._telemetry_buffer.retrieve(pos.signal_id)
                    )
                    _tel_task.add_done_callback(
                        lambda t: setattr(trade, '_v95_telemetry', t.result())
                        if not t.cancelled() and t.exception() is None else None
                    )
                # Telemetry is available via trade._v95_telemetry in callbacks
            except Exception as _tel_err:
                self.logger.debug("V9.5 telemetry retrieve failed (non-fatal): %s", _tel_err)

        # Fire callbacks (learning engine, etc.)
        for callback in self._on_trade_close_callbacks:
            try:
                callback(trade)
            except Exception as e:
                self.logger.error(
                    "Trade callback error for %s %s (R=%.2f): %s",
                    trade.ticker, trade.direction, r_multiple, e,
                )

        self.logger.info(
            "VIRTUAL CLOSE: %s %s R=%.2f net=$%.2f reason=%s (duration=%dmin, peak=%.2fR, "
            "missed_gain=$%.2f, missed_loss=$%.2f)",
            trade.direction, trade.ticker, r_multiple, net_pnl,
            reason, int(duration), pos.peak_r,
            trade.missed_gain_by_early_exit, trade.missed_loss_by_late_exit,
        )

        return trade

    def _categorise_failure(self, pos: VirtualPosition, exit_price: float, reason: str) -> str:
        """Auto-categorise losing trades."""
        if reason == "STOP_HIT":
            # Check if price later went to target
            if pos.peak_r >= 1.0:
                return "STOPPED_THEN_TARGET"
            elif pos.trough_r < -0.5 and pos.peak_r < 0.2:
                return "WRONG_DIRECTION"
            else:
                return "BAD_TIMING"
        elif reason == "REGIME_FLIP":
            return "REGIME_SHIFT"
        elif reason == "OVERSEER_FORCED":
            return "OVERSEER_FORCED"
        elif reason == "FIREWALL_HOLDING_LOSER":
            return "HELD_TOO_LONG"
        elif reason == "TIME_EXPIRED":
            return "BAD_TIMING"
        elif reason == "TIME_DECAY_PRESSURE":
            return "DEAD_MONEY"
        elif reason in ("EOD_FORCE_CLOSE", "ETP_OVERNIGHT_PROTECTION"):
            return "EOD_CLOSE"
        elif reason == "CIRCUIT_BREAKER_RED":
            return "CIRCUIT_BREAKER"
        # Phase 6, 29, 40 new exit reasons
        elif reason == "EDGE_DECAY_45MIN":
            return "DEAD_MONEY"
        elif reason == "REGIME_TIME_STOP":
            return "DEAD_MONEY"
        elif reason == "VOLUME_CLOCK_STOP":
            return "DEAD_MONEY"
        # Phase 14: volatility drag exits
        elif reason in ("5X_OVERNIGHT_KILL", "VOL_DRAG_VIX_KILL", "LEVERAGED_OVERNIGHT_KILL"):
            return "EOD_CLOSE"

        # Check if slippage was the difference
        if pos.slippage > abs(pos.unrealised_pnl):
            return "SPREAD_SLIPPAGE"

        return "WRONG_DIRECTION"

    def _persist_position(self, pos: VirtualPosition):
        """Save position to virtual_positions table."""
        try:
            self._db.execute(
                """INSERT OR REPLACE INTO virtual_positions
                (id, signal_id, bot, bot_instance, ticker, direction, strategy,
                 entry_price, entry_time, shares, risk_dollars, initial_stop,
                 current_stop, ladder_rung, unrealised_pnl, unrealised_r,
                 peak_r, trough_r, commission, slippage, status,
                 indicator_snapshot, regime_at_entry, confidence, confidence_layers,
                 time_window, portfolio_heat, concurrent_positions,
                 premarket_alignment, sector_rs, vix_at_entry,
                 market_direction_spy, days_since_last_signal,
                 gap_classification, news_catalyst, earnings_proximity)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pos.id, pos.signal_id, pos.bot, pos.bot_instance, pos.ticker,
                 pos.direction, pos.strategy, pos.entry_price,
                 pos.entry_time.isoformat(), pos.shares, pos.risk_dollars,
                 pos.initial_stop, pos.current_stop, pos.ladder_rung,
                 pos.unrealised_pnl, pos.unrealised_r, pos.peak_r, pos.trough_r,
                 pos.commission, pos.slippage, pos.status,
                 json.dumps(pos.indicator_snapshot), pos.regime_at_entry,
                 pos.confidence, json.dumps(pos.confidence_layers),
                 pos.time_window, pos.portfolio_heat, pos.concurrent_positions,
                 pos.premarket_alignment, pos.sector_rs, pos.vix_at_entry,
                 pos.market_direction_spy, pos.days_since_last_signal,
                 pos.gap_classification, pos.news_catalyst, pos.earnings_proximity),
            )
            self._db.commit()
        except Exception as e:
            self.logger.error(
                "DB persist position error for %s %s (id=%s, strategy=%s): %s",
                pos.ticker, pos.direction, pos.id, pos.strategy, e,
            )

    def _update_position_db(self, pos: VirtualPosition):
        """Update position in database."""
        try:
            self._db.execute(
                """UPDATE virtual_positions SET
                current_stop=?, ladder_rung=?, unrealised_pnl=?, unrealised_r=?,
                peak_r=?, trough_r=?, remaining_pct=?, status=?
                WHERE id=?""",
                (pos.current_stop, pos.ladder_rung, pos.unrealised_pnl,
                 pos.unrealised_r, pos.peak_r, pos.trough_r,
                 pos.remaining_pct, pos.status, pos.id),
            )
            self._db.commit()
        except Exception as e:
            self.logger.error(
                "DB update position error for %s (id=%s, rung=%d, R=%.2f): %s",
                pos.ticker, pos.id, pos.ladder_rung, pos.unrealised_r, e,
            )

    def _persist_trade(self, trade: VirtualTrade) -> bool:
        """Save completed trade to virtual_trades table.

        Returns True if persist succeeded, False on failure (C-16 safety).
        """
        try:
            self._db.execute(
                """INSERT INTO virtual_trades
                (id, position_id, signal_id, bot, bot_instance, ticker, direction,
                 strategy, entry_price, exit_price, entry_time, exit_time, shares,
                 risk_dollars, gross_pnl, net_pnl, commission, slippage, r_multiple,
                 entry_quality, exit_quality, exit_reason, duration_minutes,
                 regime_at_entry, regime_at_exit, confidence,
                 indicator_snapshot_entry, indicator_snapshot_exit,
                 peak_r, trough_r, partials, failure_category,
                 time_window, portfolio_heat, concurrent_positions,
                 premarket_alignment, sector_rs_entry, vix_at_entry,
                 vix_at_exit, market_direction_during, gap_classification,
                 news_catalyst, earnings_proximity,
                 mae_time_minutes, mfe_time_minutes, exit_efficiency,
                 slippage_model_vs_actual, firewall_cooldown_active,
                 tournament_rank,
                 missed_gain_by_early_exit, missed_loss_by_late_exit)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (trade.id, trade.position_id, trade.signal_id, trade.bot,
                 trade.bot_instance, trade.ticker, trade.direction, trade.strategy,
                 trade.entry_price, trade.exit_price, trade.entry_time,
                 trade.exit_time, trade.shares, trade.risk_dollars,
                 trade.gross_pnl, trade.net_pnl, trade.commission, trade.slippage,
                 trade.r_multiple, trade.entry_quality, trade.exit_quality,
                 trade.exit_reason, trade.duration_minutes,
                 trade.regime_at_entry, trade.regime_at_exit, trade.confidence,
                 json.dumps(trade.indicator_snapshot_entry),
                 json.dumps(trade.indicator_snapshot_exit),
                 trade.peak_r, trade.trough_r, json.dumps(trade.partials),
                 trade.failure_category,
                 trade.time_window, trade.portfolio_heat, trade.concurrent_positions,
                 trade.premarket_alignment, trade.sector_rs_entry, trade.vix_at_entry,
                 trade.vix_at_exit, trade.market_direction_during,
                 trade.gap_classification, trade.news_catalyst,
                 trade.earnings_proximity,
                 trade.mae_time_minutes, trade.mfe_time_minutes,
                 trade.exit_efficiency, trade.slippage_model_vs_actual,
                 1 if trade.firewall_cooldown_active else 0,
                 trade.tournament_rank,
                 trade.missed_gain_by_early_exit, trade.missed_loss_by_late_exit),
            )
            self._db.commit()
            return True
        except Exception as e:
            self.logger.error(
                "DB persist trade error for %s %s (id=%s, strategy=%s, R=%.2f, reason=%s): %s",
                trade.ticker, trade.direction, trade.id, trade.strategy,
                trade.r_multiple, trade.exit_reason, e,
            )
            return False

    def get_daily_summary(self) -> dict:
        """Get daily P&L summary."""
        open_unrealised = sum(p.unrealised_pnl for p in self.open_positions.values())
        base_equity = self.equity - self.daily_pnl  # Equity at start of day
        return {
            "equity": self.equity,
            "daily_realised_pnl": self.daily_pnl,
            "unrealised_pnl": open_unrealised,
            "total_pnl": self.daily_pnl + open_unrealised,
            "open_positions": len(self.open_positions),
            "trades_closed_today": len([t for t in self.closed_trades
                                        if t.exit_time and t.exit_time[:10] == datetime.now(timezone.utc).strftime("%Y-%m-%d")]),
            "daily_pnl_pct": self.daily_pnl / base_equity if base_equity > 0 else 0.0,
        }

    def close_all_positions(
        self,
        price_data: dict[str, float],
        reason: str = "EOD",
        regime_at_exit: str = "",
        exclude_swing: bool = True,
    ) -> list[VirtualTrade]:
        """Close all open positions at end of trading day.

        Args:
            price_data: Dict of ticker -> current market price for exit.
            reason: Exit reason label (default "EOD" for end-of-day).
            regime_at_exit: Current regime state string.
            exclude_swing: If True, skip positions with bot_instance == "SWING".

        Returns:
            List of VirtualTrade records for all closed positions.
        """
        closed = []
        with self._lock:
            positions_to_close = []
            for pos_id, pos in list(self.open_positions.items()):
                if pos.status != "OPEN":
                    continue
                if exclude_swing and pos.bot_instance == "SWING":
                    self.logger.info(
                        "EOD SKIP: %s %s — swing trade, holding overnight",
                        pos.ticker, pos.direction,
                    )
                    continue
                exit_price = price_data.get(pos.ticker)
                if exit_price is None or exit_price <= 0:
                    # Fallback to entry price if no market data
                    exit_price = pos.entry_price
                    self.logger.warning(
                        "EOD CLOSE: No price data for %s — using entry price $%.2f",
                        pos.ticker, exit_price,
                    )
                positions_to_close.append((pos_id, exit_price))

            for pos_id, exit_price in positions_to_close:
                trade = self.close_position(
                    pos_id, exit_price, reason, regime_at_exit,
                )
                if trade:
                    closed.append(trade)
                    self.logger.info(
                        "EOD CLOSE: %s %s @ $%.2f — R=%.2f, P&L=$%.2f",
                        trade.ticker, trade.direction, exit_price,
                        trade.r_multiple, trade.net_pnl,
                    )

        if closed:
            total_pnl = sum(t.net_pnl for t in closed)
            self.logger.info(
                "EOD CASH OUT: Closed %d positions, total P&L: $%.2f",
                len(closed), total_pnl,
            )
        else:
            self.logger.info("EOD CASH OUT: No positions to close")

        return closed

    # ── C-05: Overnight Gap Veto ────────────────────────────────────────
    def _check_overnight_gap_veto(self, ticker: str, current_price: float, atr: float) -> bool:
        """C-05: Veto entry if overnight gap exceeds 2x (ATR / Close%).

        Compares the gap between yesterday's close and today's open against
        a threshold of 2 * ATR(14) / yesterday's close.  Large overnight gaps
        on leveraged ETPs indicate information shock and the edge is consumed.

        Args:
            ticker: Instrument ticker.
            current_price: Current / today's open price.
            atr: ATR(14) value at evaluation time.

        Returns:
            True if the entry should be VETOED (gap is too large).
            False if entry is safe to proceed.
        """
        if atr <= 0 or current_price <= 0:
            return False

        try:
            import yfinance as yf
            hist = yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=True)
            if hist is None or len(hist) < 2:
                return False

            prev_close = float(hist["Close"].iloc[-2])
            today_open = float(hist["Open"].iloc[-1])

            if prev_close <= 0:
                return False

            gap_pct = abs(today_open - prev_close) / prev_close
            atr_pct = atr / prev_close
            threshold = 2.0 * atr_pct

            if gap_pct > threshold:
                self.logger.warning(
                    "C-05 OVERNIGHT_GAP_VETO: %s gap=%.2f%% > 2*ATR%%=%.2f%% "
                    "(prev_close=%.4f, open=%.4f, ATR=%.4f) — entry vetoed",
                    ticker, gap_pct * 100, threshold * 100,
                    prev_close, today_open, atr,
                )
                return True

            return False

        except Exception as e:
            self.logger.debug("C-05 overnight gap check failed for %s: %s", ticker, e)
            return False

    # ── C-11: Atomic Mutual Exclusion ────────────────────────────────────
    def _check_mutual_exclusion(self, ticker: str) -> bool:
        """C-11: Veto entry if the inverse counterpart is already held.

        Uses INVERSE_ETPS and LONG_TO_INVERSE from config/universe_constants.py
        to find counterparts.  If entering QQQS.L and QQQ3.L is already open
        (or vice versa), the entry is vetoed — holding both simultaneously
        is a guaranteed loss to spread + costs.

        Args:
            ticker: Ticker being considered for entry.

        Returns:
            True if entry is allowed (no conflict).
            False if entry should be VETOED (counterpart held).
        """
        from config.universe_constants import INVERSE_ETPS, LONG_TO_INVERSE

        open_tickers = {
            p.ticker for p in self.open_positions.values() if p.status == "OPEN"
        }
        if not open_tickers:
            return True

        # Case 1: ticker is an inverse ETP → check if its long counterpart is held
        long_counterpart = INVERSE_ETPS.get(ticker)
        if long_counterpart and long_counterpart in open_tickers:
            self.logger.warning(
                "C-11 MUTUAL_EXCLUSION_VETO: %s blocked — long counterpart %s already held",
                ticker, long_counterpart,
            )
            return False

        # Case 2: ticker is a long ETP → check if any of its inverse counterparts are held
        inverse_counterparts = LONG_TO_INVERSE.get(ticker, [])
        for inv in inverse_counterparts:
            if inv in open_tickers:
                self.logger.warning(
                    "C-11 MUTUAL_EXCLUSION_VETO: %s blocked — inverse counterpart %s already held",
                    ticker, inv,
                )
                return False

        return True

    # ── G-14: Session-End Exit Protocol ──────────────────────────────────
    def _execute_session_end_exit(self) -> list[dict]:
        """G-14: Session-end exit protocol for all open positions.

        Implements a three-phase escalation to ensure all positions are flat
        by market close:

        Phase 1 — 16:25 UK: Submit aggressive LIMIT orders pegged to
                  Ask (for sells) / Bid (for buys), refreshed every 10s.
        Phase 2 — 16:28 UK: If unfilled, convert to MOC (Market-On-Close).
        Phase 3 — 16:31 UK: If still open, trigger P0 alert — manual
                  intervention required.

        Returns:
            List of event dicts describing actions taken.
        """
        from core.clock import now_uk

        events = []
        uk_now = now_uk()
        uk_time = uk_now.hour * 60 + uk_now.minute  # Minutes since midnight

        # Only active from 16:25 UK onwards
        if uk_time < 16 * 60 + 25:
            return events

        open_positions = [
            p for p in self.open_positions.values()
            if p.status == "OPEN" and p.bot_instance != "SWING"
        ]
        if not open_positions:
            return events

        for pos in open_positions:
            if uk_time >= 16 * 60 + 31:
                # Phase 3: 16:31+ UK — P0 ALERT (positions still open after MOC)
                self.logger.critical(
                    "G-14 P0_ALERT: %s %s STILL OPEN at %02d:%02d UK — "
                    "MANUAL INTERVENTION REQUIRED",
                    pos.ticker, pos.direction, uk_now.hour, uk_now.minute,
                )
                events.append({
                    "type": "G14_SESSION_END_P0_ALERT",
                    "position": pos.id,
                    "ticker": pos.ticker,
                    "direction": pos.direction,
                    "phase": 3,
                    "uk_time": f"{uk_now.hour:02d}:{uk_now.minute:02d}",
                    "severity": "P0",
                    "message": f"{pos.ticker} still open at {uk_now.hour:02d}:{uk_now.minute:02d} UK — manual intervention required",
                })

            elif uk_time >= 16 * 60 + 28:
                # Phase 2: 16:28-16:30 UK — MOC order
                self.logger.warning(
                    "G-14 MOC_ORDER: %s %s — submitting Market-On-Close at %02d:%02d UK",
                    pos.ticker, pos.direction, uk_now.hour, uk_now.minute,
                )
                events.append({
                    "type": "G14_SESSION_END_MOC",
                    "position": pos.id,
                    "ticker": pos.ticker,
                    "direction": pos.direction,
                    "phase": 2,
                    "order_type": "MOC",
                    "uk_time": f"{uk_now.hour:02d}:{uk_now.minute:02d}",
                })

            else:
                # Phase 1: 16:25-16:27 UK — aggressive LIMIT pegged to Ask/Bid
                # For closing a LONG: sell at Ask (aggressive)
                # For closing a SHORT: buy at Bid (aggressive)
                peg_side = "ASK" if pos.direction == "LONG" else "BID"
                self.logger.info(
                    "G-14 LIMIT_PEG: %s %s — aggressive LIMIT pegged to %s, "
                    "refresh every 10s (phase 1, %02d:%02d UK)",
                    pos.ticker, pos.direction, peg_side,
                    uk_now.hour, uk_now.minute,
                )
                events.append({
                    "type": "G14_SESSION_END_LIMIT",
                    "position": pos.id,
                    "ticker": pos.ticker,
                    "direction": pos.direction,
                    "phase": 1,
                    "order_type": "LIMIT",
                    "peg_side": peg_side,
                    "refresh_interval_s": 10,
                    "uk_time": f"{uk_now.hour:02d}:{uk_now.minute:02d}",
                })

        return events

    # ── G-15: Broker Bracket Orders ──────────────────────────────────────
    def _place_bracket_order(
        self,
        ticker: str,
        entry_price: float,
        stop_atr: float,
        take_profit_pct: float,
    ) -> dict:
        """G-15: Place OCA (One-Cancels-All) bracket order at broker.

        Creates a bracket with:
        - Stop-loss at entry_price - stop_atr (LONG) or + stop_atr (SHORT)
        - Take-profit at entry_price * (1 + take_profit_pct) (LONG)
          or entry_price * (1 - take_profit_pct) (SHORT)

        In virtual/paper mode this logs the bracket specification and returns
        the order details dict.  When IBKRGateway is attached, the bracket
        is forwarded to the broker for GTC server-side execution.

        Args:
            ticker: Instrument ticker.
            entry_price: Fill price.
            stop_atr: ATR-based stop distance (absolute, positive).
            take_profit_pct: Take-profit distance as a fraction (e.g. 0.05 = 5%).

        Returns:
            Dict with bracket order details (order IDs are virtual in paper mode).
        """
        # Determine direction from open position
        direction = "LONG"
        for pos in self.open_positions.values():
            if pos.ticker == ticker and pos.status == "OPEN":
                direction = pos.direction
                break

        if direction == "LONG":
            stop_price = entry_price - stop_atr
            take_profit_price = entry_price * (1.0 + take_profit_pct)
        else:
            stop_price = entry_price + stop_atr
            take_profit_price = entry_price * (1.0 - take_profit_pct)

        bracket = {
            "type": "G15_BRACKET_ORDER",
            "ticker": ticker,
            "direction": direction,
            "entry_price": round(entry_price, 6),
            "stop_price": round(stop_price, 6),
            "take_profit_price": round(take_profit_price, 6),
            "stop_atr": round(stop_atr, 6),
            "take_profit_pct": round(take_profit_pct, 4),
            "order_type": "OCA",
            "status": "VIRTUAL",
            "parent_order_id": f"BRK-{uuid.uuid4().hex[:8]}",
            "stop_order_id": f"BRK-STP-{uuid.uuid4().hex[:8]}",
            "tp_order_id": f"BRK-TP-{uuid.uuid4().hex[:8]}",
        }

        # Forward to broker if available
        if self._ibkr_gateway is not None:
            try:
                self._ibkr_gateway.place_bracket(
                    ticker=ticker,
                    direction=direction,
                    stop_price=stop_price,
                    take_profit_price=take_profit_price,
                )
                bracket["status"] = "SUBMITTED"
                self.logger.info(
                    "G-15 BRACKET_SUBMITTED: %s %s stop=%.4f tp=%.4f",
                    ticker, direction, stop_price, take_profit_price,
                )
            except Exception as e:
                bracket["status"] = "BROKER_ERROR"
                bracket["error"] = str(e)
                self.logger.error(
                    "G-15 BRACKET_FAILED: %s — %s (falling back to virtual)", ticker, e,
                )
        else:
            self.logger.info(
                "G-15 BRACKET_VIRTUAL: %s %s stop=%.4f tp=%.4f (paper mode)",
                ticker, direction, stop_price, take_profit_price,
            )

        return bracket

    def _update_bracket_on_rung_advance(self, ticker: str, new_stop: float) -> dict:
        """G-15: Update bracket stop-loss when profit ladder advances a rung.

        When the chandelier trailing stop ratchets up, the broker-side bracket
        must be updated to match.  This cancels the old stop and replaces it
        with the new trailing level.

        Args:
            ticker: Instrument ticker.
            new_stop: New stop-loss price after rung advance.

        Returns:
            Dict with update details.
        """
        update = {
            "type": "G15_BRACKET_UPDATE",
            "ticker": ticker,
            "new_stop": round(new_stop, 6),
            "status": "VIRTUAL",
            "update_order_id": f"BRK-UPD-{uuid.uuid4().hex[:8]}",
        }

        if self._ibkr_gateway is not None:
            try:
                self._ibkr_gateway.modify_stop(ticker=ticker, new_stop=new_stop)
                update["status"] = "MODIFIED"
                self.logger.info(
                    "G-15 BRACKET_STOP_UPDATED: %s new_stop=%.4f", ticker, new_stop,
                )
            except Exception as e:
                update["status"] = "BROKER_ERROR"
                update["error"] = str(e)
                self.logger.error(
                    "G-15 BRACKET_UPDATE_FAILED: %s — %s", ticker, e,
                )
        else:
            self.logger.info(
                "G-15 BRACKET_STOP_VIRTUAL: %s new_stop=%.4f (paper mode)",
                ticker, new_stop,
            )

        return update

    def reset_daily(self):
        """Reset daily counters."""
        self.daily_pnl = 0.0
