"""Python Risk Arbiter — mirrors all 33 CHECKs from Rust risk_arbiter.rs.

Loads thresholds from config/config.toml. Evaluates a signal dict + market
context dict and returns (approved, vetoes). Each veto includes the CHECK
number and a human-readable reason string.

Designed to be imported by production_backtest.py for post-signal filtering:

    from python_brain.ouroboros.risk_arbiter_py import RiskArbiterPy
    arbiter = RiskArbiterPy.from_config_toml("config/config.toml")
    approved, vetoes = arbiter.evaluate(signal, market_ctx, portfolio)
"""

from __future__ import annotations

import math
import os
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Config loader — reads all thresholds from config.toml once.
# ---------------------------------------------------------------------------

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]


def _load_config(path: str | Path) -> dict:
    """Load config.toml and return the raw dict."""
    with open(path, "rb") as f:
        return tomllib.load(f)


# ---------------------------------------------------------------------------
# Enums — mirrors Rust types.
# ---------------------------------------------------------------------------

class RiskRegime(IntEnum):
    """HALT > FLATTEN > REDUCE > NORMAL. Higher = more restrictive."""
    Normal = 0
    Reduce = 1
    Flatten = 2
    Halt = 3


class MacroRegimeSignal(IntEnum):
    """Macro regime classification (mirrors cross_asset_macro.rs)."""
    Normal = 0
    Caution = 1
    Stress = 2
    Crisis = 3


class Direction(IntEnum):
    Long = 0
    Short = 1


# ---------------------------------------------------------------------------
# Data containers — typed dicts replaced by dataclasses for clarity.
# ---------------------------------------------------------------------------

@dataclass
class MacroIndicator:
    """Snapshot of macro-level indicators (mirrors Rust MacroIndicator)."""
    vix: float = 15.0
    dxy: float = 100.0
    credit_spread_bps: float = 100.0
    fear_greed: float = 50.0
    last_update_ns: int = 0


@dataclass
class EvalContext:
    """Per-evaluation context (mirrors Rust EvalContext).

    Callers populate this from live tick data / bridge output.
    Sentinel defaults match the Rust side: they will trigger rejection
    if not overridden (fail-closed design).
    """
    time_secs: int = 10 * 3600
    last_tick_age_secs: int = 999  # SENTINEL: triggers CHECK 7
    bid: float = 10.0
    ask: float = 10.02
    broker_connected: bool = True
    wal_available: bool = True
    now_ns: int = 1_000_000_000
    volatilities: Dict[str, float] = field(default_factory=dict)
    ticker_halted: bool = False
    garch_sigma: float = -1.0  # SENTINEL
    leverage_factor: int = 1
    scanner_score: float = -1.0  # SENTINEL
    kelly_fraction_raw: float = 0.0
    macro_indicator: MacroIndicator = field(default_factory=MacroIndicator)
    macro_stale_threshold_ns: int = 300_000_000_000  # 300 seconds
    ticker_ic: float = 0.0
    ticker_trade_count: int = 0
    ticker_locked: bool = False
    ticker_position_count: int = 0
    evt_cvar: float = 0.0
    kalman_divergence: float = 0.0
    native_spread_bps: float = 0.0
    structural_score: float = 50.0


@dataclass
class PortfolioState:
    """Simplified portfolio state for risk evaluation.

    In production, populate from WAL events or live portfolio tracker.
    """
    equity: float = 10000.0
    initial_equity: float = 10000.0
    cash: float = 10000.0
    filled_positions: int = 0
    pending_positions: int = 0
    daily_drawdown_pct_val: float = 0.0
    weekly_drawdown_pct_val: float = 0.0
    peak_drawdown_pct_val: float = 0.0
    portfolio_heat_pct_val: float = 0.0
    daily_trade_count: int = 0
    consecutive_stop_losses: int = 0
    isa_year_invested: float = 0.0
    # Per-sector exposure: sector_name -> exposure % of equity.
    sector_exposures: Dict[str, float] = field(default_factory=dict)
    # Tickers with open positions (for inverse blocking and sector lookup).
    open_positions: Dict[str, int] = field(default_factory=dict)  # ticker -> count
    # Weekly high-water mark for weekly drawdown.
    weekly_high_water_mark: float = 0.0

    def total_position_count(self) -> int:
        return self.filled_positions + self.pending_positions

    def cash_buffer_pct(self) -> float:
        if self.equity <= 0:
            return 0.0
        return (self.cash / self.equity) * 100.0

    def daily_drawdown_pct(self) -> float:
        return self.daily_drawdown_pct_val

    def weekly_drawdown_pct(self) -> float:
        return self.weekly_drawdown_pct_val

    def peak_drawdown_pct(self) -> float:
        return self.peak_drawdown_pct_val

    def portfolio_heat_pct(self) -> float:
        return self.portfolio_heat_pct_val

    def sector_heat_pct(self, ticker: str, sector_map: Dict[str, str] | None = None) -> float:
        """Return sector exposure % for the ticker's sector."""
        if sector_map is None:
            return 0.0
        sector = sector_map.get(ticker, "Unknown")
        return self.sector_exposures.get(sector, 0.0)

    def cvar_heat_pct(self, volatilities: Dict[str, float]) -> float:
        """Approximate CVaR heat. In production, use full position-level calc."""
        if not volatilities or self.equity <= 0:
            return 0.0
        total_risk = sum(v * 2.33 for v in volatilities.values())  # 99% VaR approx
        return (total_risk / self.equity) * 100.0

    def inverse_blocker(self, ticker: str, inverse_pairs: Dict[str, str] | None = None) -> Optional[str]:
        """Return blocking ticker if inverse mutual exclusion is violated."""
        if inverse_pairs is None:
            return None
        inverse = inverse_pairs.get(ticker)
        if inverse and inverse in self.open_positions and self.open_positions[inverse] > 0:
            return inverse
        return None


# ---------------------------------------------------------------------------
# RiskConfig — all thresholds loaded from config.toml.
# ---------------------------------------------------------------------------

@dataclass
class RiskConfig:
    """All configurable risk parameters. Loaded from config.toml sections."""

    # [position]
    max_positions: int = 6
    portfolio_heat_limit_pct: float = 15.0
    sector_heat_cap_pct: float = 33.0
    cash_buffer_pct: float = 10.0
    isa_annual_limit_gbp: float = 20000.0

    # [signal]
    confidence_floor: float = 65.0
    velocity_check_window_secs: int = 1
    velocity_check_max_intents: int = 5

    # [timing]
    stale_data_threshold_secs: int = 120
    entry_cutoff_secs: int = 15 * 3600 + 45 * 60  # 15:45 London

    # [risk]
    daily_drawdown_pct: float = 4.0
    weekly_drawdown_pct: float = 7.0
    peak_drawdown_halt_pct: float = 15.0
    equity_floor_pct: float = 70.0
    spread_veto_pct: float = 0.3
    consecutive_loss_halt: int = 8
    daily_trade_limit: int = 20
    min_gross_edge_pct: float = 0.10

    # [hardening]
    system_velocity_max: int = 10
    kelly_ramp_target: int = 250
    kelly_ramp_clamp_min: float = 0.1
    kelly_ramp_clamp_max: float = 1.0
    vix_high_enter: float = 25.0
    vix_high_exit: float = 22.0
    vix_extreme_enter: float = 35.0
    vix_extreme_exit: float = 30.0
    garch_threshold_base: float = 0.80
    cvar_heat_multiplier: float = 1.5
    reentry_3pos_ic: float = 0.20
    reentry_3pos_trades: int = 20
    reentry_2pos_ic: float = 0.10
    reentry_2pos_trades: int = 10
    macro_stress_stale_tick_secs: int = 60
    drawdown_velocity_pct: float = 2.0
    drawdown_velocity_window_secs: int = 3600
    equity_snapshot_interval_secs: int = 60
    equity_snapshot_retention_secs: int = 7200
    spread_edge_ratio: float = 2.0
    scanner_score_min: float = 30.0
    kelly_fraction_floor: float = 0.005

    # [hardening.sizing]
    min_trade_gbp_sim: float = 20.0
    min_trade_gbp_live: float = 1500.0

    @staticmethod
    def from_toml(cfg: dict) -> "RiskConfig":
        """Build RiskConfig from parsed config.toml dict."""
        sig = cfg.get("signal", {})
        pos = cfg.get("position", {})
        tim = cfg.get("timing", {})
        rsk = cfg.get("risk", {})
        hrd = cfg.get("hardening", {})
        siz = hrd.get("sizing", {})

        # Parse entry_cutoff_london "HH:MM" -> seconds from midnight.
        cutoff_str = tim.get("entry_cutoff_london", "15:45")
        parts = cutoff_str.split(":")
        cutoff_secs = int(parts[0]) * 3600 + int(parts[1]) * 60

        rc = RiskConfig()
        # [position]
        rc.max_positions = int(pos.get("max_simultaneous_positions", rc.max_positions))
        rc.portfolio_heat_limit_pct = float(pos.get("portfolio_heat_limit_pct", rc.portfolio_heat_limit_pct))
        rc.sector_heat_cap_pct = float(pos.get("sector_heat_cap_pct", rc.sector_heat_cap_pct))
        rc.cash_buffer_pct = float(pos.get("cash_buffer_pct", rc.cash_buffer_pct))
        rc.isa_annual_limit_gbp = float(pos.get("isa_annual_limit_gbp", rc.isa_annual_limit_gbp))

        # [signal]
        rc.confidence_floor = float(sig.get("confidence_floor", rc.confidence_floor))
        rc.velocity_check_window_secs = int(sig.get("velocity_check_window_secs", rc.velocity_check_window_secs))
        rc.velocity_check_max_intents = int(sig.get("velocity_check_max_intents", rc.velocity_check_max_intents))

        # [timing]
        rc.stale_data_threshold_secs = int(tim.get("stale_data_threshold_secs", rc.stale_data_threshold_secs))
        rc.entry_cutoff_secs = cutoff_secs

        # [risk]
        rc.daily_drawdown_pct = float(rsk.get("daily_drawdown_pct", rc.daily_drawdown_pct))
        rc.weekly_drawdown_pct = float(rsk.get("weekly_drawdown_pct", rc.weekly_drawdown_pct))
        rc.peak_drawdown_halt_pct = float(rsk.get("peak_drawdown_halt_pct", rc.peak_drawdown_halt_pct))
        rc.equity_floor_pct = float(rsk.get("equity_floor_pct", rc.equity_floor_pct))
        rc.spread_veto_pct = float(rsk.get("spread_veto_pct", rc.spread_veto_pct))
        rc.consecutive_loss_halt = int(rsk.get("consecutive_loss_halt", rc.consecutive_loss_halt))
        rc.daily_trade_limit = int(rsk.get("max_daily_trades", rc.daily_trade_limit))
        rc.min_gross_edge_pct = float(rsk.get("min_gross_edge_pct", rc.min_gross_edge_pct))

        # [hardening]
        rc.system_velocity_max = int(hrd.get("system_velocity_max", rc.system_velocity_max))
        rc.kelly_ramp_target = int(hrd.get("kelly_ramp_target", rc.kelly_ramp_target))
        rc.kelly_ramp_clamp_min = float(hrd.get("kelly_ramp_clamp_min", rc.kelly_ramp_clamp_min))
        rc.kelly_ramp_clamp_max = float(hrd.get("kelly_ramp_clamp_max", rc.kelly_ramp_clamp_max))
        rc.vix_high_enter = float(hrd.get("vix_high_enter", rc.vix_high_enter))
        rc.vix_high_exit = float(hrd.get("vix_high_exit", rc.vix_high_exit))
        rc.vix_extreme_enter = float(hrd.get("vix_extreme_enter", rc.vix_extreme_enter))
        rc.vix_extreme_exit = float(hrd.get("vix_extreme_exit", rc.vix_extreme_exit))
        rc.garch_threshold_base = float(hrd.get("garch_threshold_base", rc.garch_threshold_base))
        rc.cvar_heat_multiplier = float(hrd.get("cvar_heat_multiplier", rc.cvar_heat_multiplier))
        rc.reentry_3pos_ic = float(hrd.get("reentry_3pos_ic", rc.reentry_3pos_ic))
        rc.reentry_3pos_trades = int(hrd.get("reentry_3pos_trades", rc.reentry_3pos_trades))
        rc.reentry_2pos_ic = float(hrd.get("reentry_2pos_ic", rc.reentry_2pos_ic))
        rc.reentry_2pos_trades = int(hrd.get("reentry_2pos_trades", rc.reentry_2pos_trades))
        rc.macro_stress_stale_tick_secs = int(hrd.get("macro_stress_stale_tick_secs", rc.macro_stress_stale_tick_secs))
        rc.drawdown_velocity_pct = float(hrd.get("drawdown_velocity_pct", rc.drawdown_velocity_pct))
        rc.drawdown_velocity_window_secs = int(hrd.get("drawdown_velocity_window_secs", rc.drawdown_velocity_window_secs))
        rc.equity_snapshot_interval_secs = int(hrd.get("equity_snapshot_interval_secs", rc.equity_snapshot_interval_secs))
        rc.equity_snapshot_retention_secs = int(hrd.get("equity_snapshot_retention_secs", rc.equity_snapshot_retention_secs))
        rc.spread_edge_ratio = float(hrd.get("spread_edge_ratio", rc.spread_edge_ratio))
        rc.scanner_score_min = float(hrd.get("scanner_score_min", rc.scanner_score_min))
        rc.kelly_fraction_floor = float(hrd.get("kelly_fraction_floor", rc.kelly_fraction_floor))

        # [hardening.sizing]
        rc.min_trade_gbp_sim = float(siz.get("min_trade_gbp_sim", rc.min_trade_gbp_sim))
        rc.min_trade_gbp_live = float(siz.get("min_trade_gbp_live", rc.min_trade_gbp_live))

        return rc


# ---------------------------------------------------------------------------
# Macro regime evaluator (mirrors cross_asset_macro.rs).
# ---------------------------------------------------------------------------

def evaluate_macro_regime(indicator: MacroIndicator) -> MacroRegimeSignal:
    """Classify macro regime from indicator snapshot.

    Thresholds (first match wins):
    - Crisis: VIX > 30 OR credit spread > 200 bps
    - Stress: VIX > 25 OR credit spread > 150 bps OR Fear & Greed < 25
    - Caution: VIX > 20 OR Fear & Greed < 40
    - Normal: everything else
    """
    if indicator.vix > 30.0 or indicator.credit_spread_bps > 200.0:
        return MacroRegimeSignal.Crisis
    if indicator.vix > 25.0 or indicator.credit_spread_bps > 150.0 or indicator.fear_greed < 25.0:
        return MacroRegimeSignal.Stress
    if indicator.vix > 20.0 or indicator.fear_greed < 40.0:
        return MacroRegimeSignal.Caution
    return MacroRegimeSignal.Normal


def is_macro_stale(indicator: MacroIndicator, now_ns: int, threshold_ns: int) -> bool:
    """True when the latest macro update is older than threshold."""
    return (now_ns - indicator.last_update_ns) > threshold_ns


# ---------------------------------------------------------------------------
# RiskArbiterPy — the synchronous 33-check risk gate.
# ---------------------------------------------------------------------------

class RiskArbiterPy:
    """Python mirror of Rust RiskArbiter. Evaluates all CHECKs in deterministic order.

    Designed for:
    - Post-signal filtering in production_backtest.py
    - Shadow-mode validation against Rust decisions
    - Offline what-if analysis
    """

    VELOCITY_WINDOW_5MIN_NS: int = 300_000_000_000

    def __init__(self, config: RiskConfig) -> None:
        self.config = config
        self.regime: RiskRegime = RiskRegime.Normal

        # Velocity tracking: deque of (ticker, timestamp_ns).
        self._velocity_log: deque[Tuple[str, int]] = deque()

        # Ouroboros-calibrated regime scaling multipliers.
        self.regime_scales: Dict[str, float] = {}

        # Ouroboros-calibrated per-ticker Kelly fraction caps.
        self.kelly_fractions: Dict[str, float] = {}

        # Simulation mode: relaxes cash buffer, portfolio heat, drawdown checks.
        self.simulation_mode: bool = False

        # When True AND simulation_mode=True, enforce live risk gates anyway.
        self.paper_uses_live_gates: bool = False

        # Ouroboros ticker blacklist: symbols with WR < 30% over 10+ trades.
        self.ticker_blacklist: Set[str] = set()

        # VIX hysteresis state (prevents flip-flop at boundaries).
        self.vix_high: bool = False
        self.vix_extreme: bool = False

        # Equity snapshots for drawdown velocity: list of (timestamp_ns, equity).
        self._equity_snapshots: List[Tuple[int, float]] = []

        # Inverse pair lookup: ticker -> inverse ticker (bidirectional).
        self._inverse_pairs: Dict[str, str] = {}

        # Sector map: ticker -> sector name (loaded from config.toml [sectors]).
        self._sector_map: Dict[str, str] = {}

        # Kelly ramp trades (set externally based on validated trade count).
        self.kelly_ramp_trades: int = 0

    # -------------------------------------------------------------------
    # Factory
    # -------------------------------------------------------------------

    @classmethod
    def from_config_toml(cls, path: str | Path | None = None) -> "RiskArbiterPy":
        """Load config.toml and build a fully-configured arbiter.

        Searches for config.toml in order:
        1. Explicit path argument
        2. AEGIS_CONFIG_DIR env var
        3. /app/config/config.toml (Docker)
        4. <project_root>/config/config.toml (local dev)
        """
        if path is not None:
            cfg_path = Path(path)
        else:
            env_dir = os.environ.get("AEGIS_CONFIG_DIR")
            if env_dir:
                cfg_path = Path(env_dir) / "config.toml"
            else:
                docker_path = Path("/app/config/config.toml")
                if docker_path.exists():
                    cfg_path = docker_path
                else:
                    project_root = Path(__file__).resolve().parents[2]
                    cfg_path = project_root / "config" / "config.toml"

        raw = _load_config(cfg_path)
        rc = RiskConfig.from_toml(raw)
        arbiter = cls(rc)

        # Load inverse pairs from config.
        pairs = raw.get("inverse_pairs", {}).get("pairs", [])
        for pair in pairs:
            if len(pair) == 2:
                arbiter._inverse_pairs[pair[0]] = pair[1]
                arbiter._inverse_pairs[pair[1]] = pair[0]

        # Load sector map from config.
        sectors = raw.get("sectors", {})
        for sector_name, tickers in sectors.items():
            if isinstance(tickers, list):
                for ticker in tickers:
                    arbiter._sector_map[ticker] = sector_name

        # Load static blacklist from config.
        blacklist_tickers = raw.get("blacklist", {}).get("tickers", [])
        arbiter.ticker_blacklist = set(blacklist_tickers)

        return arbiter

    # -------------------------------------------------------------------
    # Main evaluation entry point
    # -------------------------------------------------------------------

    def evaluate(
        self,
        ticker: str,
        side: Direction,
        confidence: float,
        kelly: float,
        portfolio: PortfolioState,
        ctx: EvalContext,
    ) -> Tuple[bool, List[str]]:
        """Evaluate an order intent against all 33 risk checks.

        Returns (approved, vetoes) where vetoes is a list of
        "CHECK_N: reason" strings. Empty vetoes = approved.

        This is the primary API. Mirrors Rust RiskArbiter::evaluate() exactly.
        """
        vetoes: List[str] = []
        ts = ctx.now_ns
        enforce_live_gates = not self.simulation_mode or self.paper_uses_live_gates

        # ── CHECK 1: ISA Safety — Short selling blocked ──
        if side == Direction.Short:
            self.regime = RiskRegime.Halt
            vetoes.append("CHECK_1: ISA short sell blocked (ISA accounts cannot short)")
            return False, vetoes

        # ── CHECK 2: Inverse Mutual Exclusion (H32) ──
        blocker = portfolio.inverse_blocker(ticker, self._inverse_pairs)
        if blocker is not None:
            vetoes.append(f"CHECK_2: inverse mutual exclusion — {blocker} already open")
            return False, vetoes

        # ── CHECK 5: Risk Regime — HALT/FLATTEN rejects all entries ──
        if self.regime >= RiskRegime.Flatten:
            vetoes.append(f"CHECK_5: regime={self.regime.name} — all entries blocked")
            return False, vetoes

        # ── CHECK 6: Max Positions (H34) ──
        # ALWAYS enforced, including simulation mode.
        if portfolio.total_position_count() >= self.config.max_positions:
            vetoes.append(
                f"CHECK_6: max positions reached "
                f"({portfolio.total_position_count()} >= {self.config.max_positions})"
            )
            return False, vetoes

        # ── CHECK 7: Data Staleness — > threshold → HALT ──
        if ctx.last_tick_age_secs > self.config.stale_data_threshold_secs:
            self.regime = RiskRegime.Halt
            vetoes.append(
                f"CHECK_7: stale data — tick age {ctx.last_tick_age_secs}s "
                f"> threshold {self.config.stale_data_threshold_secs}s"
            )
            return False, vetoes

        # ── CHECK 8: Broker Connected ──
        if not ctx.broker_connected:
            self.regime = RiskRegime.Halt
            vetoes.append("CHECK_8: broker disconnected")
            return False, vetoes

        # ── CHECK 9: WAL Available ──
        if not ctx.wal_available:
            self.regime = RiskRegime.Halt
            vetoes.append("CHECK_9: WAL unavailable")
            return False, vetoes

        # ── CHECK 10: Confidence Floor (leverage-aware, Sprint 5 T-07) ──
        # 3x ETP: floor / sqrt(3). 5x: floor / sqrt(5). 1x: unchanged.
        leverage_sqrt = math.sqrt(max(ctx.leverage_factor, 1))
        adjusted_floor = self.config.confidence_floor / leverage_sqrt
        if confidence < adjusted_floor:
            vetoes.append(
                f"CHECK_10: confidence {confidence:.1f} "
                f"< floor {adjusted_floor:.1f} "
                f"(base {self.config.confidence_floor}, leverage {ctx.leverage_factor}x)"
            )
            return False, vetoes

        # ── CHECK 11: Time-of-Day Cutoff (H35) ──
        if enforce_live_gates and ctx.time_secs >= self.config.entry_cutoff_secs:
            vetoes.append(
                f"CHECK_11: too late in session — "
                f"time {ctx.time_secs}s >= cutoff {self.config.entry_cutoff_secs}s"
            )
            return False, vetoes

        # CHECK 12: REMOVED — Auction period blocking was LSE-specific.
        # Spread veto (CHECK 13) provides natural protection during auctions.

        # ── CHECK 13: Spread Veto (H36) ──
        if ctx.bid > 0.0:
            spread_pct = (ctx.ask - ctx.bid) / ctx.bid * 100.0
            if spread_pct > self.config.spread_veto_pct:
                spread_bps = int(spread_pct * 100.0)
                vetoes.append(
                    f"CHECK_13: spread too wide — "
                    f"{spread_pct:.3f}% ({spread_bps} bps) > limit {self.config.spread_veto_pct}%"
                )
                return False, vetoes

        # ── CHECK 28: Daily Trade Limit (N0a) ──
        # ALWAYS enforced, including simulation mode.
        if portfolio.daily_trade_count >= self.config.daily_trade_limit:
            vetoes.append(
                f"CHECK_28: daily trade limit reached "
                f"({portfolio.daily_trade_count} >= {self.config.daily_trade_limit})"
            )
            return False, vetoes

        # ── CHECK 29: Minimum Gross Edge (N0d) ──
        if ctx.bid > 0.0 and self.config.min_gross_edge_pct > 0.0:
            spread_pct = (ctx.ask - ctx.bid) / ctx.bid * 100.0
            if spread_pct > self.config.min_gross_edge_pct * self.config.spread_edge_ratio:
                vetoes.append(
                    f"CHECK_29: gross edge too low — "
                    f"spread {spread_pct:.3f}% > "
                    f"edge threshold {self.config.min_gross_edge_pct * self.config.spread_edge_ratio:.3f}%"
                )
                return False, vetoes

        # ── CHECK 14: Cash Buffer (H31) ──
        if enforce_live_gates and portfolio.cash_buffer_pct() < self.config.cash_buffer_pct:
            vetoes.append(
                f"CHECK_14: cash buffer insufficient — "
                f"{portfolio.cash_buffer_pct():.1f}% < {self.config.cash_buffer_pct}%"
            )
            return False, vetoes

        # ── CHECK 15: Portfolio Heat ──
        if enforce_live_gates and portfolio.portfolio_heat_pct() >= self.config.portfolio_heat_limit_pct:
            vetoes.append(
                f"CHECK_15: portfolio heat exceeded — "
                f"{portfolio.portfolio_heat_pct():.1f}% >= {self.config.portfolio_heat_limit_pct}%"
            )
            return False, vetoes

        # ── CHECK 16: Sector Heat (H30) ──
        if enforce_live_gates:
            sector_heat = portfolio.sector_heat_pct(ticker, self._sector_map)
            if sector_heat >= self.config.sector_heat_cap_pct:
                sector = self._sector_map.get(ticker, "Unknown")
                vetoes.append(
                    f"CHECK_16: sector heat exceeded — "
                    f"sector {sector} at {sector_heat:.1f}% >= {self.config.sector_heat_cap_pct}%"
                )
                return False, vetoes

        # ── CHECK 17: ISA Annual Limit ──
        if enforce_live_gates and portfolio.isa_year_invested >= self.config.isa_annual_limit_gbp:
            vetoes.append(
                f"CHECK_17: ISA annual limit exceeded — "
                f"invested {portfolio.isa_year_invested:.0f} >= {self.config.isa_annual_limit_gbp:.0f}"
            )
            return False, vetoes

        # ── CHECK 18: Daily Drawdown → FLATTEN (H29) ──
        if enforce_live_gates and portfolio.daily_drawdown_pct() > self.config.daily_drawdown_pct:
            self.regime = RiskRegime.Flatten
            vetoes.append(
                f"CHECK_18: daily drawdown breached — "
                f"{portfolio.daily_drawdown_pct():.2f}% > {self.config.daily_drawdown_pct}%"
            )
            return False, vetoes

        # ── CHECK 30: Weekly Drawdown → FLATTEN (Sprint 10) ──
        if enforce_live_gates and portfolio.weekly_drawdown_pct() > self.config.weekly_drawdown_pct:
            self.regime = RiskRegime.Flatten
            vetoes.append(
                f"CHECK_30: weekly drawdown breached — "
                f"{portfolio.weekly_drawdown_pct():.2f}% > {self.config.weekly_drawdown_pct}%"
            )
            return False, vetoes

        # ── CHECK 31: Peak Drawdown from ATH → HALT (Sprint 10) ──
        if enforce_live_gates:
            peak_dd = portfolio.peak_drawdown_pct()
            if peak_dd > self.config.peak_drawdown_halt_pct:
                self.regime = RiskRegime.Halt
                vetoes.append(
                    f"CHECK_31: peak drawdown halt — "
                    f"{peak_dd:.2f}% > {self.config.peak_drawdown_halt_pct}%"
                )
                return False, vetoes

        # ── CHECK 32: Equity Floor (Sprint 10) ──
        if enforce_live_gates:
            floor = portfolio.initial_equity * self.config.equity_floor_pct / 100.0
            if portfolio.equity < floor:
                self.regime = RiskRegime.Halt
                vetoes.append(
                    f"CHECK_32: equity floor breached — "
                    f"equity {portfolio.equity:.2f} < floor {floor:.2f} "
                    f"({self.config.equity_floor_pct}% of {portfolio.initial_equity:.2f})"
                )
                return False, vetoes

        # ── CHECK 19: Per-Ticker Velocity (H37) ──
        self._prune_velocity(ts)
        per_ticker_recent = sum(
            1 for (t, _) in self._velocity_log if t == ticker
        )
        if per_ticker_recent >= self.config.velocity_check_max_intents:
            vetoes.append(
                f"CHECK_19: per-ticker velocity — "
                f"{per_ticker_recent} intents for {ticker} "
                f">= max {self.config.velocity_check_max_intents}"
            )
            return False, vetoes

        # ── CHECK 19b: System-wide velocity ──
        system_cutoff = max(0, ts - self.VELOCITY_WINDOW_5MIN_NS)
        system_recent = sum(
            1 for (_, t) in self._velocity_log if t >= system_cutoff
        )
        if system_recent >= self.config.system_velocity_max:
            vetoes.append(
                f"CHECK_19b: system velocity — "
                f"{system_recent} intents in 5min "
                f">= max {self.config.system_velocity_max}"
            )
            return False, vetoes

        # ── CHECK 20: Macro Regime Escalation (Phase 9) ──
        macro_veto = self._evaluate_macro_escalation(ctx)
        if macro_veto is not None:
            vetoes.append(macro_veto)
            return False, vetoes

        # ── CHECK 21: Consecutive Loss Breaker (H38) ──
        if portfolio.consecutive_stop_losses >= self.config.consecutive_loss_halt:
            self.regime = RiskRegime.Halt
            vetoes.append(
                f"CHECK_21: consecutive loss breaker — "
                f"{portfolio.consecutive_stop_losses} losses "
                f">= halt threshold {self.config.consecutive_loss_halt}"
            )
            return False, vetoes

        # ── CHECK 22: Duplicate Position — gated by momentum re-entry ──
        if ctx.ticker_position_count > 0:
            if ctx.ticker_locked:
                max_allowed = 1  # Locked tickers: no re-entry
            elif (ctx.ticker_ic >= self.config.reentry_3pos_ic
                  and ctx.ticker_trade_count >= self.config.reentry_3pos_trades):
                max_allowed = 3
            elif (ctx.ticker_ic >= self.config.reentry_2pos_ic
                  and ctx.ticker_trade_count >= self.config.reentry_2pos_trades):
                max_allowed = 2
            else:
                max_allowed = 1  # Default: single position only

            if ctx.ticker_position_count >= max_allowed:
                vetoes.append(
                    f"CHECK_22: duplicate position — "
                    f"{ctx.ticker_position_count} positions for {ticker} "
                    f">= max allowed {max_allowed} "
                    f"(IC={ctx.ticker_ic:.3f}, trades={ctx.ticker_trade_count}, locked={ctx.ticker_locked})"
                )
                return False, vetoes

        # ── CHECK 23: Ticker Halted ──
        if ctx.ticker_halted:
            vetoes.append(f"CHECK_23: ticker {ticker} is halted")
            return False, vetoes

        # ── CHECK 24: CVaR Heat — portfolio-level conditional value at risk ──
        cvar_heat = portfolio.cvar_heat_pct(ctx.volatilities)
        cvar_limit = self.config.portfolio_heat_limit_pct * self.config.cvar_heat_multiplier
        if cvar_heat > cvar_limit:
            vetoes.append(
                f"CHECK_24: CVaR heat exceeded — "
                f"{cvar_heat:.1f}% > limit {cvar_limit:.1f}% "
                f"({self.config.portfolio_heat_limit_pct} * {self.config.cvar_heat_multiplier})"
            )
            return False, vetoes

        # ── CHECK 25: GARCH forecast sigma — leverage-scaled ──
        garch_threshold = self.config.garch_threshold_base * math.sqrt(max(ctx.leverage_factor, 1))
        if ctx.garch_sigma > garch_threshold:
            vetoes.append(
                f"CHECK_25: GARCH vol too high — "
                f"sigma {ctx.garch_sigma:.4f} > threshold {garch_threshold:.4f} "
                f"(base {self.config.garch_threshold_base}, leverage {ctx.leverage_factor}x)"
            )
            return False, vetoes

        # ── CHECK 26: Scanner score below minimum ──
        if ctx.scanner_score > 0.0 and ctx.scanner_score < self.config.scanner_score_min:
            vetoes.append(
                f"CHECK_26: scanner score too low — "
                f"{ctx.scanner_score:.0f} < min {self.config.scanner_score_min:.0f}"
            )
            return False, vetoes

        # ── CHECK 27: Kelly fraction below floor ──
        # Ouroboros per-ticker Kelly cap overrides global max when available.
        effective_kelly = kelly
        cap = self.kelly_fractions.get(ticker)
        if cap is not None:
            effective_kelly = min(kelly, cap)
        if ctx.kelly_fraction_raw > 0.0 and ctx.kelly_fraction_raw < self.config.kelly_fraction_floor:
            vetoes.append(
                f"CHECK_27: Kelly below floor — "
                f"raw fraction {ctx.kelly_fraction_raw:.4f} "
                f"< floor {self.config.kelly_fraction_floor:.4f}"
            )
            return False, vetoes

        # ── All checks passed. Calculate adjusted size. ──

        # Kelly ramp: SC-13
        if self.config.kelly_ramp_target > 0:
            kelly_ramp = self.kelly_ramp_trades / self.config.kelly_ramp_target
        else:
            kelly_ramp = 1.0
        kelly_ramp = max(self.config.kelly_ramp_clamp_min,
                         min(self.config.kelly_ramp_clamp_max, kelly_ramp))
        ramped_kelly = effective_kelly * kelly_ramp
        size = ramped_kelly * portfolio.equity

        # Ouroboros-calibrated regime scaling.
        regime_name = self.regime.name
        if self.regime == RiskRegime.Reduce:
            default_scale = 0.5
        else:
            default_scale = 1.0
        regime_scale = self.regime_scales.get(regime_name, default_scale)
        adjusted_size = size * regime_scale

        # ── Minimum entry size gate (SC-05) ──
        # Suspended during Kelly ramp when validated_trades < kelly_ramp_target.
        minimum_entry = self.config.min_trade_gbp_live
        if self.simulation_mode:
            minimum_entry = self.config.min_trade_gbp_sim
        if self.kelly_ramp_trades >= self.config.kelly_ramp_target and adjusted_size < minimum_entry:
            vetoes.append(
                f"CHECK_SC05: below minimum entry size — "
                f"adjusted size {adjusted_size:.2f} < min {minimum_entry:.2f}"
            )
            return False, vetoes

        # Record approved intent for velocity tracking.
        self._velocity_log.append((ticker, ts))

        return True, vetoes

    # -------------------------------------------------------------------
    # Convenience: evaluate from dicts (for production_backtest.py).
    # -------------------------------------------------------------------

    def evaluate_signal(
        self,
        signal: Dict[str, Any],
        market_ctx: Dict[str, Any],
        portfolio: PortfolioState | None = None,
    ) -> Tuple[bool, List[str]]:
        """Evaluate a signal dict + market context dict.

        This is the high-level API for backtest integration. Extracts fields
        from the signal/context dicts and delegates to evaluate().

        Signal dict keys (from bridge.py):
            ticker, side ("long"/"short"), confidence, kelly_fraction,
            strategy, scanner_score, garch_sigma, leverage

        Market context dict keys:
            time_secs, last_tick_age_secs, bid, ask, broker_connected,
            wal_available, now_ns, vix, dxy, credit_spread_bps,
            fear_greed, macro_last_update_ns, leverage_factor,
            ticker_halted, ticker_ic, ticker_trade_count, ticker_locked,
            ticker_position_count, volatilities, evt_cvar,
            kalman_divergence, native_spread_bps, structural_score
        """
        if portfolio is None:
            portfolio = PortfolioState()

        # Parse side.
        side_str = str(signal.get("side", "long")).lower()
        side = Direction.Short if side_str == "short" else Direction.Long

        confidence = float(signal.get("confidence", 0.0))
        kelly_val = float(signal.get("kelly_fraction", 0.0))
        ticker_name = str(signal.get("ticker", signal.get("symbol", "")))
        leverage = int(signal.get("leverage", market_ctx.get("leverage_factor", 1)))

        # Build MacroIndicator.
        macro = MacroIndicator(
            vix=float(market_ctx.get("vix", 15.0)),
            dxy=float(market_ctx.get("dxy", 100.0)),
            credit_spread_bps=float(market_ctx.get("credit_spread_bps", 100.0)),
            fear_greed=float(market_ctx.get("fear_greed", 50.0)),
            last_update_ns=int(market_ctx.get("macro_last_update_ns", 0)),
        )

        # Build EvalContext.
        ctx = EvalContext(
            time_secs=int(market_ctx.get("time_secs", market_ctx.get("london_time_secs", 10 * 3600))),
            last_tick_age_secs=int(market_ctx.get("last_tick_age_secs", 0)),
            bid=float(market_ctx.get("bid", 0.0)),
            ask=float(market_ctx.get("ask", 0.0)),
            broker_connected=bool(market_ctx.get("broker_connected", True)),
            wal_available=bool(market_ctx.get("wal_available", True)),
            now_ns=int(market_ctx.get("now_ns", market_ctx.get("timestamp_ns", 0))),
            volatilities=dict(market_ctx.get("volatilities", {})),
            ticker_halted=bool(market_ctx.get("ticker_halted", False)),
            garch_sigma=float(signal.get("garch_sigma", market_ctx.get("garch_sigma", -1.0))),
            leverage_factor=leverage,
            scanner_score=float(signal.get("scanner_score", market_ctx.get("scanner_score", -1.0))),
            kelly_fraction_raw=kelly_val,
            macro_indicator=macro,
            macro_stale_threshold_ns=int(market_ctx.get("macro_stale_threshold_ns", 300_000_000_000)),
            ticker_ic=float(market_ctx.get("ticker_ic", 0.0)),
            ticker_trade_count=int(market_ctx.get("ticker_trade_count", 0)),
            ticker_locked=bool(market_ctx.get("ticker_locked", False)),
            ticker_position_count=int(market_ctx.get("ticker_position_count", 0)),
            evt_cvar=float(market_ctx.get("evt_cvar", 0.0)),
            kalman_divergence=float(market_ctx.get("kalman_divergence", 0.0)),
            native_spread_bps=float(market_ctx.get("native_spread_bps", 0.0)),
            structural_score=float(market_ctx.get("structural_score", 50.0)),
        )

        return self.evaluate(ticker_name, side, confidence, kelly_val, portfolio, ctx)

    # -------------------------------------------------------------------
    # Regime management (mirrors Rust methods).
    # -------------------------------------------------------------------

    def escalate(self, new_regime: RiskRegime) -> None:
        """Transition to a higher (more restrictive) regime."""
        if new_regime > self.regime:
            self.regime = new_regime

    def clear_reduce(self) -> None:
        """Clear REDUCE if conditions have been nominal for 5 minutes."""
        if self.regime == RiskRegime.Reduce:
            self.regime = RiskRegime.Normal

    def clear_flatten(self) -> None:
        """Clear FLATTEN after all positions closed + reconciliation clean."""
        if self.regime == RiskRegime.Flatten:
            self.regime = RiskRegime.Normal

    def manual_clear_halt(self) -> None:
        """Manual human approval to clear HALT."""
        if self.regime == RiskRegime.Halt:
            self.regime = RiskRegime.Normal

    # -------------------------------------------------------------------
    # Drawdown velocity tracking (mirrors Rust P1-2.16).
    # -------------------------------------------------------------------

    def record_equity_snapshot(self, now_ns: int, equity: float) -> None:
        """Record equity snapshot for drawdown velocity tracking."""
        interval_ns = self.config.equity_snapshot_interval_secs * 1_000_000_000
        if self._equity_snapshots:
            last_ts = self._equity_snapshots[-1][0]
            if now_ns < last_ts + interval_ns:
                return
        self._equity_snapshots.append((now_ns, equity))
        cutoff = now_ns - (self.config.equity_snapshot_retention_secs * 1_000_000_000)
        self._equity_snapshots = [
            (ts, eq) for ts, eq in self._equity_snapshots if ts >= cutoff
        ]

    def check_drawdown_velocity(self, now_ns: int, current_equity: float) -> bool:
        """Check drawdown velocity: if equity dropped >N% in window -> HALT."""
        window_ns = self.config.drawdown_velocity_window_secs * 1_000_000_000
        window_ago = max(0, now_ns - window_ns)
        for ts, eq in self._equity_snapshots:
            if ts >= window_ago and eq > 0.0:
                drawdown_pct = ((eq - current_equity) / eq) * 100.0
                if drawdown_pct > self.config.drawdown_velocity_pct:
                    self.regime = RiskRegime.Halt
                    return True
                break
        return False

    # -------------------------------------------------------------------
    # Internal helpers.
    # -------------------------------------------------------------------

    def _prune_velocity(self, now_ns: int) -> None:
        """Remove expired entries from velocity log (O(1) amortized)."""
        while self._velocity_log:
            _, ts = self._velocity_log[0]
            if (now_ns - ts) > 300_000_000_000:
                self._velocity_log.popleft()
            else:
                break
        # Hard ceiling: prevent unbounded growth.
        if len(self._velocity_log) > 50_000:
            self._velocity_log.popleft()

    def _evaluate_macro_escalation(self, ctx: EvalContext) -> Optional[str]:
        """CHECK 20: Macro Regime Escalation (Phase 9).

        Evaluates macro indicators and escalates regime if needed.
        VIX hysteresis deadband prevents flip-flopping at boundaries.
        Returns veto string if regime escalation blocks entry, None otherwise.
        """
        macro_signal = evaluate_macro_regime(ctx.macro_indicator)
        vix = ctx.macro_indicator.vix

        # VIX hysteresis deadband (P1-2.7).
        if vix >= self.config.vix_extreme_enter:
            self.vix_extreme = True
            self.vix_high = True
        elif vix < self.config.vix_extreme_exit:
            self.vix_extreme = False

        if vix >= self.config.vix_high_enter:
            self.vix_high = True
        elif vix < self.config.vix_high_exit:
            self.vix_high = False

        # Trigger A: VIX Crisis -> FLATTEN.
        if macro_signal == MacroRegimeSignal.Crisis or self.vix_extreme:
            self.regime = RiskRegime.Flatten
            return (
                f"CHECK_20a: macro crisis detected — "
                f"VIX={vix:.1f}, credit={ctx.macro_indicator.credit_spread_bps:.0f}bps"
            )

        # VIX high (with hysteresis) -> REDUCE. Don't return veto — allows entries at 0.5x.
        if self.vix_high and self.regime < RiskRegime.Reduce:
            self.regime = RiskRegime.Reduce

        # Trigger B: Macro Stress + Stale Ticks -> HALT.
        if (macro_signal == MacroRegimeSignal.Stress
                and ctx.last_tick_age_secs > self.config.macro_stress_stale_tick_secs):
            self.regime = RiskRegime.Halt
            return "CHECK_20b: macro stress with stale ticks"

        # Trigger D: Macro Data Stale + non-Normal -> REDUCE.
        if (is_macro_stale(ctx.macro_indicator, ctx.now_ns, ctx.macro_stale_threshold_ns)
                and macro_signal != MacroRegimeSignal.Normal):
            self.regime = RiskRegime.Reduce
            age_secs = (ctx.now_ns - ctx.macro_indicator.last_update_ns) // 1_000_000_000
            return (
                f"CHECK_20d: macro data stale — "
                f"age {age_secs}s, signal={macro_signal.name}"
            )

        return None


# ---------------------------------------------------------------------------
# Standalone testing / smoke test.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    # Try to load from project config.
    project_root = Path(__file__).resolve().parents[2]
    config_path = project_root / "config" / "config.toml"

    if not config_path.exists():
        print(f"ERROR: config.toml not found at {config_path}", file=sys.stderr)
        sys.exit(1)

    arbiter = RiskArbiterPy.from_config_toml(config_path)
    print(f"Loaded RiskArbiterPy with {len(arbiter._inverse_pairs)} inverse pairs, "
          f"{len(arbiter._sector_map)} sector mappings, "
          f"{len(arbiter.ticker_blacklist)} blacklisted tickers")
    print(f"Config: max_positions={arbiter.config.max_positions}, "
          f"confidence_floor={arbiter.config.confidence_floor}, "
          f"spread_veto_pct={arbiter.config.spread_veto_pct}, "
          f"daily_trade_limit={arbiter.config.daily_trade_limit}")

    # Smoke test: signal that should pass with relaxed paper config.
    portfolio = PortfolioState(equity=10000.0, initial_equity=10000.0, cash=8000.0)
    ctx = EvalContext(
        time_secs=10 * 3600,  # 10:00 London
        last_tick_age_secs=2,
        bid=100.0,
        ask=100.05,
        broker_connected=True,
        wal_available=True,
        now_ns=1_000_000_000_000,
        leverage_factor=3,
    )

    approved, vetoes = arbiter.evaluate(
        ticker="QQQ3.L",
        side=Direction.Long,
        confidence=70.0,
        kelly=0.05,
        portfolio=portfolio,
        ctx=ctx,
    )

    print(f"\nSmoke test: approved={approved}")
    if vetoes:
        for v in vetoes:
            print(f"  VETO: {v}")
    else:
        print("  No vetoes — signal approved")

    # Test a signal that should fail (short sell).
    approved2, vetoes2 = arbiter.evaluate(
        ticker="QQQ3.L",
        side=Direction.Short,
        confidence=90.0,
        kelly=0.05,
        portfolio=portfolio,
        ctx=ctx,
    )
    print(f"\nShort sell test: approved={approved2}")
    for v in vetoes2:
        print(f"  VETO: {v}")

    # Test dict-based API.
    signal_dict = {
        "ticker": "3LUS.L",
        "side": "long",
        "confidence": 80.0,
        "kelly_fraction": 0.04,
        "leverage": 3,
    }
    market_dict = {
        "time_secs": 10 * 3600,
        "last_tick_age_secs": 1,
        "bid": 50.0,
        "ask": 50.02,
        "broker_connected": True,
        "wal_available": True,
        "now_ns": 2_000_000_000_000,
        "vix": 18.0,
    }
    approved3, vetoes3 = arbiter.evaluate_signal(signal_dict, market_dict, portfolio)
    print(f"\nDict API test: approved={approved3}")
    if vetoes3:
        for v in vetoes3:
            print(f"  VETO: {v}")
    else:
        print("  No vetoes — signal approved")

    print("\nAll smoke tests completed.")
