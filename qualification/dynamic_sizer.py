"""
NZT-48 Trading System — Dynamic Position Sizing Engine
Section 37/56: Multi-factor position sizing combining Kelly Criterion,
volatility scaling, regime awareness, streak detection, portfolio heat,
confidence scaling, time-of-day decay, correlation penalty, shock
recovery, and Wave 2 academic risk scalars (vol targeting, CVaR,
momentum crash prevention, ERC allocation).

This is the authoritative position sizer for the system. It replaces
single-factor Kelly with a composite approach that adapts to every
dimension of market context. The output risk percentage is always
capped by the constitutional 0.75% immutable limit.

Sizing Pipeline (12 factors, applied multiplicatively):
    base_risk = half_kelly(win_rate, avg_win_loss_ratio)
    * volatility_scalar              (ATR stop-distance)
    * vol_target_scalar              (Moreira & Muir 2017 — realized vol)
    * cvar_scalar                    (Rockafellar & Uryasev 2000 — tail risk)
    * regime_scalar                  (8-state regime multiplier)
    * momentum_crash_scalar          (Barroso & Santa-Clara 2015 — crash guard)
    * erc_scalar                     (Maillard et al. 2010 — ERC allocation)
    * streak_scalar                  (win/loss streak adjustment)
    * confidence_scalar              (signal confidence mapping)
    * time_of_day_scalar             (intraday alpha decay)
    * correlation_scalar             (portfolio overlap penalty)
    * shock_recovery_scalar          (post-SHOCK dampener)
    -> clamped by portfolio heat budget (6%)
    -> clamped by immutable 0.75% cap
    -> clamped by regime-specific risk cap
    -> shares reduced by liquidity haircut (ADV-based)
"""

from __future__ import annotations

import logging
import math
import sys
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

# Project imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Signal, Direction, RegimeState
import config as cfg
from uk_isa.isa_universe import UNDERLYING_MAP

logger = logging.getLogger("nzt48.dynamic_sizer")

# Eastern Time for time-of-day scaling
from core.clock import ET_TZ as _ET


# ---------------------------------------------------------------------------
# Constants & defaults
# ---------------------------------------------------------------------------

# Constitutional cap — cannot be exceeded under any circumstance
_IMMUTABLE_MAX_RISK_PCT = 0.0075  # 0.75%

# G-01 R-04: Total deployment cap — max fraction of equity in open positions
_MAX_TOTAL_DEPLOYMENT = 0.40  # 40% of equity

# G-03 R-07: Portfolio CVaR gate — block entries when tail risk exceeds threshold
_PORTFOLIO_CVAR_MAX = 0.03        # 3% of equity (CVaR_95 limit)
_PORTFOLIO_CVAR_LOOKBACK = 60     # Minimum 60 days rolling window
_PORTFOLIO_CVAR_CONFIDENCE = 0.95 # 95% confidence level

# Regime multipliers: how much of full risk each regime allows
# A-02: RISK_OFF is direction-dependent — see _compute_regime_scalar()
_REGIME_MULTIPLIERS: dict[RegimeState, float] = {
    RegimeState.TRENDING_UP_STRONG: 1.0,
    RegimeState.TRENDING_UP_MOD: 0.85,
    RegimeState.TRENDING_DOWN_STRONG: 1.0,
    RegimeState.TRENDING_DOWN_MOD: 0.85,
    RegimeState.RANGE_BOUND: 0.50,
    RegimeState.HIGH_VOLATILITY: 0.50,
    RegimeState.RISK_OFF: 0.0,   # A-02: 0.0 for LONG (default); INVERSE gets 0.50 via override
    RegimeState.SHOCK: 0.0,
}

# A-02: RISK_OFF multiplier for INVERSE/SHORT positions (hedging allowed at half size)
_RISK_OFF_INVERSE_MULTIPLIER = 0.50

# Regime-specific maximum risk-per-trade caps
# Tighter than _IMMUTABLE_MAX_RISK_PCT for adverse regimes
_REGIME_RISK_CAPS: dict[RegimeState, float] = {
    RegimeState.TRENDING_UP_STRONG: _IMMUTABLE_MAX_RISK_PCT,
    RegimeState.TRENDING_UP_MOD: _IMMUTABLE_MAX_RISK_PCT,
    RegimeState.TRENDING_DOWN_STRONG: _IMMUTABLE_MAX_RISK_PCT,
    RegimeState.TRENDING_DOWN_MOD: _IMMUTABLE_MAX_RISK_PCT,
    RegimeState.RANGE_BOUND: _IMMUTABLE_MAX_RISK_PCT,
    RegimeState.HIGH_VOLATILITY: 0.005,
    RegimeState.RISK_OFF: 0.0,     # A-02: 0.0 for LONG; INVERSE override in calculate_position_size
    RegimeState.SHOCK: 0.0,
}

# SHOCK_RECOVERY: after SHOCK regime ends, use 25% of normal sizing
# for this many sessions before returning to full sizing
_SHOCK_RECOVERY_SESSIONS = 3
_SHOCK_RECOVERY_MULTIPLIER = 0.25

# Time-of-day windows (ET) and their scaling factors — US market
# Edge is strongest at the open and decays through the day.
_TOD_WINDOWS: list[tuple[time, time, float, str]] = [
    (time(9, 30), time(10, 30), 1.0, "morning_momentum"),
    (time(10, 30), time(11, 30), 0.85, "trend_extension"),
    (time(11, 30), time(13, 30), 0.70, "midday_chop"),
    (time(13, 30), time(15, 0), 0.80, "afternoon_push"),
    (time(15, 0), time(16, 0), 0.50, "last_hour"),
]

# C-09: LSE time-of-day windows (UK local time) for .L tickers
# LSE trading day 08:00-16:30 UK has different intraday alpha profile
# than US markets. US overlap (14:30-16:00) is highest alpha window.
_LSE_TZ = ZoneInfo("Europe/London")
_TOD_WINDOWS_LSE: list[tuple[time, time, float, str]] = [
    (time(8, 0), time(9, 0), 0.80, "lse_auction_settling"),     # Auction settling — wider spreads
    (time(9, 0), time(11, 30), 1.0, "lse_prime_morning"),       # Prime LSE morning
    (time(11, 30), time(13, 0), 0.85, "lse_lunch_trough"),      # Lunch trough (B-03 aligned)
    (time(13, 0), time(14, 30), 0.90, "lse_pre_us"),            # Pre-US open — anticipatory
    (time(14, 30), time(16, 0), 1.20, "lse_us_overlap"),        # US overlap — highest alpha
    (time(16, 0), time(16, 30), 0.70, "lse_closing_auction"),   # Closing auction risk
]

# Confidence-to-multiplier mapping (linear interpolation)
_CONF_MIN = 60.0   # Below this, multiplier = 0.6x (minimum)
_CONF_MAX = 90.0   # Above this, multiplier = 1.2x (maximum)
_CONF_MULT_MIN = 0.6
_CONF_MULT_MAX = 1.2

# Streak thresholds
_STREAK_LOSS_THRESHOLD = 3   # Start reducing after this many consecutive losses
_STREAK_LOSS_REDUCTION = 0.25  # 25% reduction per additional loss beyond threshold
_STREAK_WIN_THRESHOLD = 3    # Start allowing increase after this many consecutive wins
_STREAK_WIN_BOOST = 0.10     # 10% increase per additional win beyond threshold
_STREAK_WIN_CAP = 1.3        # Max win streak multiplier

# Correlation penalty
_CORRELATION_THRESHOLD = 0.7  # Above this, penalty kicks in
_CORRELATION_PENALTY_FACTOR = 0.5  # Reduce size by 50% for highly correlated trades

# Volatility scaling reference
_VOL_REFERENCE_ATR_PCT = 0.015  # 1.5% ATR is "normal" volatility

# Wave 2 — Moreira & Muir (2017) Volatility Targeting
_VOL_TARGET_DEFAULT = 0.15        # 15% annualized target vol
_VOL_TARGET_FLOOR = 0.30          # Never below 30% from vol targeting
_VOL_TARGET_LOOKBACK = 21         # 21 trades rolling window

# Wave 2 — Rockafellar & Uryasev (2000) CVaR Scaling
_CVAR_LOOKBACK = 60               # 60 trades for CVaR calculation
_CVAR_PERCENTILE = 0.05           # 5% tail
_CVAR_THRESHOLD_R = 2.0           # Scale down when CVaR < -2.0R
_CVAR_FLOOR = 0.25                # Never below 25% from CVaR

# Wave 2 — Barroso & Santa-Clara (2015) Momentum Crash Prevention
_MOMENTUM_CRASH_VIX_THRESHOLD = 30.0
_MOMENTUM_CRASH_SCALAR = 0.60     # 40% reduction during crash conditions

# C-01: Bayesian Stranger Penalty (Beta-Binomial posterior)
# Untested tickers get reduced sizing. After 30 trades at 50% WR, kappa -> 1.0.
# Alpha=Beta=2: weakly informative prior centered at 50%.
_STRANGER_ALPHA = 2.0
_STRANGER_BETA = 2.0
_STRANGER_MATURITY_TRADES = 30  # Number of trades for full maturity (sqrt(n/30) = 1.0)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SizingResult:
    """Complete position sizing output with full audit trail."""
    risk_pct: float = 0.0
    risk_dollars: float = 0.0
    shares: int = 0
    scaling_factors: dict[str, float] = field(default_factory=dict)
    sizing_log: str = ""


@dataclass
class _TradeRecord:
    """Lightweight internal record for streak and stats tracking."""
    r_multiple: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# DynamicSizer
# ---------------------------------------------------------------------------

class DynamicSizer:
    """Multi-factor dynamic position sizing engine.

    Combines twelve independent scaling dimensions into a single risk
    percentage that respects constitutional limits and portfolio heat
    constraints. Every factor is logged for post-trade audit.

    Args:
        starting_equity: Initial account equity for fallback calculations.
        max_portfolio_heat: Maximum total open risk as fraction of equity.
            Default 6% (0.06) per system spec.
    """

    def __init__(
        self,
        starting_equity: float = 10_000.0,
        max_portfolio_heat: float = 0.06,
    ) -> None:
        self._lock = threading.Lock()

        # Configuration with YAML overrides where available
        sizer_cfg = cfg.get("dynamic_sizer", {}) or {}
        self._starting_equity = starting_equity
        self._max_portfolio_heat = sizer_cfg.get(
            "max_portfolio_heat", max_portfolio_heat,
        )
        self._immutable_cap = _IMMUTABLE_MAX_RISK_PCT
        self._max_position_pct = sizer_cfg.get("max_position_pct", 0.05)

        # G-01 R-04: Total deployment cap (40% of equity)
        self._max_deployment = sizer_cfg.get(
            "max_total_deployment", _MAX_TOTAL_DEPLOYMENT,
        )

        # G-03 R-07: Portfolio CVaR gate (Cornish-Fisher parametric)
        self._portfolio_cvar_max = sizer_cfg.get(
            "portfolio_cvar_max", _PORTFOLIO_CVAR_MAX,
        )
        # Rolling daily portfolio returns for CVaR calculation (loaded on startup)
        self._portfolio_daily_returns: deque[float] = deque(maxlen=252)

        # Kelly parameters
        kelly_cfg = sizer_cfg.get("kelly", {}) or {}
        self._kelly_window = kelly_cfg.get("rolling_window", 60)
        self._kelly_min_trades = kelly_cfg.get("min_trades", 20)
        self._kelly_default_risk = kelly_cfg.get("default_risk", 0.0075)  # 0.75%

        # Volatility scaling
        vol_cfg = sizer_cfg.get("volatility", {}) or {}
        self._vol_reference = vol_cfg.get("reference_atr_pct", _VOL_REFERENCE_ATR_PCT)
        self._vol_floor = vol_cfg.get("floor_multiplier", 0.3)
        self._vol_ceiling = vol_cfg.get("ceiling_multiplier", 1.5)

        # Trade history for Kelly and streak tracking
        self._trade_history: deque[_TradeRecord] = deque(maxlen=self._kelly_window)
        self._consecutive_results: list[float] = []  # Track current streak direction

        # Running stats (updated incrementally for performance)
        self._total_wins = 0
        self._total_losses = 0
        self._sum_win_r = 0.0
        self._sum_loss_r = 0.0

        # Adaptive Kelly: total lifetime trade count for estimation confidence
        self._total_trade_count: int = 0

        # SHOCK_RECOVERY state: counts sessions remaining at reduced sizing
        # after leaving SHOCK regime
        self._shock_recovery_remaining: int = 0
        self._last_regime: Optional[RegimeState] = None
        # 3-02: Track last date we decremented to avoid per-signal decrement
        self._shock_recovery_last_decremented: str = ""

        # Wave 2 — Moreira & Muir vol targeting
        self._vol_target: float = cfg.get(
            "v95_vol_target_annualized", _VOL_TARGET_DEFAULT,
        )

        # C-01: Per-ticker trade stats for Bayesian stranger penalty
        # Maps ticker -> {"n": total_trades, "wins": win_count}
        self._ticker_stats: dict[str, dict[str, int]] = defaultdict(
            lambda: {"n": 0, "wins": 0},
        )

        # Wave 2 — Barroso & Santa-Clara macro state (updated via update_macro())
        self._macro_vix: float = 0.0
        self._macro_spx_3m_return: float = 0.0

        # Wave 2 — Maillard et al. (2010) ERC portfolio optimizer reference
        self._erc_optimizer: object | None = None

        logger.info(
            "DynamicSizer initialized | equity=%.0f | heat_cap=%.1f%% | "
            "kelly_window=%d | vol_ref=%.3f | vol_target=%.2f",
            self._starting_equity,
            self._max_portfolio_heat * 100,
            self._kelly_window,
            self._vol_reference,
            self._vol_target,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calculate_position_size(
        self,
        signal: Signal,
        regime: RegimeState,
        equity: float,
        open_positions: list[Any],
        recent_trades: list[Any],
        current_time: Optional[datetime] = None,
        cb_size_multiplier: float = 1.0,
    ) -> dict:
        """Calculate the optimal position size for a new signal.

        Applies all eight scaling factors multiplicatively to the
        Kelly-derived base risk, then clamps by portfolio heat and
        constitutional limits.

        Args:
            signal: The candidate Signal object. Must have entry, stop,
                confidence, ticker, and direction populated.
            regime: Current RegimeState from the regime classifier.
            equity: Current total account equity in dollars.
            open_positions: List of currently open Position objects.
                Used for portfolio heat and correlation checks.
            recent_trades: List of recent Trade objects (for vol/stats
                if the internal history is empty).
            current_time: Override for current time. Defaults to now(ET).

        Returns:
            dict with keys: risk_pct, risk_dollars, shares,
            scaling_factors, sizing_log.
        """
        if current_time is None:
            current_time = datetime.now(_ET)
        elif current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=_ET)
        else:
            current_time = current_time.astimezone(_ET)

        # ----------------------------------------------------------
        # Finding 13: NaN / Inf input defense — reject garbage inputs
        # ----------------------------------------------------------
        _zero_result = {
            "risk_pct": 0.0,
            "risk_dollars": 0.0,
            "shares": 0,
            "scaling_factors": {},
            "sizing_log": "NaN/Inf/invalid input",
        }
        if math.isnan(signal.confidence) or math.isinf(signal.confidence):
            logger.warning("NaN/Inf defense: signal.confidence=%s", signal.confidence)
            return _zero_result
        if math.isnan(equity) or math.isinf(equity) or equity <= 0:
            logger.warning("NaN/Inf defense: equity=%s", equity)
            return _zero_result
        if math.isnan(signal.entry) or math.isinf(signal.entry) or signal.entry <= 0:
            logger.warning("NaN/Inf defense: signal.entry=%s", signal.entry)
            return _zero_result
        if math.isnan(signal.stop) or math.isinf(signal.stop):
            logger.warning("NaN/Inf defense: signal.stop=%s", signal.stop)
            return _zero_result

        log_parts: list[str] = []
        factors: dict[str, float] = {}

        # ----------------------------------------------------------
        # G-01 R-04: Total Deployment Cap — hard veto at 40% equity
        # ----------------------------------------------------------
        deployment_result = self._check_deployment_cap(
            signal, open_positions, equity,
        )
        if deployment_result is not None:
            return deployment_result

        # ----------------------------------------------------------
        # G-03 R-07: Portfolio CVaR Gate — block if tail risk > 3%
        # ----------------------------------------------------------
        cvar_veto = self._check_portfolio_cvar_gate(equity)
        if cvar_veto is not None:
            return cvar_veto

        # ----------------------------------------------------------
        # 1. Kelly Criterion base risk (half-Kelly)
        # ----------------------------------------------------------
        kelly_risk = self._compute_kelly()

        # Apply adaptive Kelly fraction based on estimation confidence
        adaptive_fraction = self.compute_adaptive_kelly_fraction(
            self._total_trade_count,
        )
        kelly_risk *= adaptive_fraction
        factors["kelly"] = kelly_risk
        factors["adaptive_kelly_fraction"] = adaptive_fraction
        log_parts.append(
            f"kelly_base={kelly_risk:.4f} (adaptive={adaptive_fraction:.3f}, "
            f"trades={self._total_trade_count})"
        )

        # ----------------------------------------------------------
        # 2. Volatility scaling (ATR-based)
        # ----------------------------------------------------------
        vol_scalar = self._compute_volatility_scalar(signal)
        factors["volatility"] = vol_scalar
        log_parts.append(f"vol_scalar={vol_scalar:.3f}")

        # ----------------------------------------------------------
        # 2b. Moreira & Muir (2017) vol targeting
        # ----------------------------------------------------------
        vol_target_scalar = self._compute_vol_target_scalar()
        factors["vol_target"] = vol_target_scalar
        if vol_target_scalar < 1.0:
            log_parts.append(f"vol_target={vol_target_scalar:.3f}")

        # ----------------------------------------------------------
        # 2c. Rockafellar & Uryasev (2000) CVaR scaling
        # ----------------------------------------------------------
        cvar_scalar = self._compute_cvar_scalar()
        factors["cvar"] = cvar_scalar
        if cvar_scalar < 1.0:
            log_parts.append(f"cvar_scalar={cvar_scalar:.3f}")

        # ----------------------------------------------------------
        # 3. Regime-based scaling (A-02: direction-aware for RISK_OFF)
        # ----------------------------------------------------------
        regime_scalar = self._compute_regime_scalar(regime, signal.direction)
        factors["regime"] = regime_scalar
        log_parts.append(f"regime_scalar={regime_scalar:.3f} ({regime.value})")

        # ----------------------------------------------------------
        # 3b. SHOCK_RECOVERY tracking
        # ----------------------------------------------------------
        with self._lock:
            if self._last_regime == RegimeState.SHOCK and regime != RegimeState.SHOCK:
                # Just exited SHOCK — enter recovery period
                self._shock_recovery_remaining = _SHOCK_RECOVERY_SESSIONS
                logger.warning(
                    "SHOCK_RECOVERY: entering %d-session recovery at %.0f%% sizing",
                    _SHOCK_RECOVERY_SESSIONS,
                    _SHOCK_RECOVERY_MULTIPLIER * 100,
                )
            self._last_regime = regime

        shock_recovery_scalar = 1.0
        if self._shock_recovery_remaining > 0 and regime != RegimeState.SHOCK:
            shock_recovery_scalar = _SHOCK_RECOVERY_MULTIPLIER
            factors["shock_recovery"] = shock_recovery_scalar
            log_parts.append(
                f"SHOCK_RECOVERY: {self._shock_recovery_remaining} sessions left, "
                f"scalar={shock_recovery_scalar:.2f}"
            )

        # ----------------------------------------------------------
        # 4. Streak adjustment
        # ----------------------------------------------------------
        streak_scalar = self._compute_streak_scalar()
        factors["streak"] = streak_scalar
        log_parts.append(f"streak_scalar={streak_scalar:.3f}")

        # ----------------------------------------------------------
        # 5. Confidence scaling
        # ----------------------------------------------------------
        conf_scalar = self._compute_confidence_scalar(signal.confidence)
        factors["confidence"] = conf_scalar
        log_parts.append(
            f"conf_scalar={conf_scalar:.3f} (conf={signal.confidence:.0f})"
        )

        # ----------------------------------------------------------
        # 6. Time-of-day scaling
        # ----------------------------------------------------------
        tod_scalar, tod_label = self._compute_tod_scalar(current_time, signal.ticker)
        factors["time_of_day"] = tod_scalar
        log_parts.append(f"tod_scalar={tod_scalar:.3f} ({tod_label})")

        # ----------------------------------------------------------
        # 7. Correlation penalty
        # ----------------------------------------------------------
        corr_scalar = self._compute_correlation_scalar(signal, open_positions)
        factors["correlation"] = corr_scalar
        log_parts.append(f"corr_scalar={corr_scalar:.3f}")

        # ----------------------------------------------------------
        # 7b. Barroso & Santa-Clara (2015) momentum crash prevention
        # ----------------------------------------------------------
        momentum_crash_scalar = self._compute_momentum_crash_scalar()
        factors["momentum_crash"] = momentum_crash_scalar
        if momentum_crash_scalar < 1.0:
            log_parts.append(
                f"MOMENTUM_CRASH: VIX={self._macro_vix:.1f} "
                f"SPX_3m={self._macro_spx_3m_return:.3f} "
                f"scalar={momentum_crash_scalar:.2f}"
            )

        # ----------------------------------------------------------
        # 7c. Maillard et al. (2010) ERC allocation scalar
        # ----------------------------------------------------------
        erc_scalar = self._compute_erc_scalar(signal.ticker)
        factors["erc"] = erc_scalar
        if erc_scalar < 1.0:
            log_parts.append(f"erc_scalar={erc_scalar:.3f}")

        # ----------------------------------------------------------
        # 7d. C-01: Bayesian stranger penalty (Beta-Binomial posterior)
        # ----------------------------------------------------------
        stranger_kappa = self._compute_stranger_penalty(signal.ticker)
        factors["stranger_kappa"] = stranger_kappa
        if stranger_kappa < 1.0:
            log_parts.append(
                f"STRANGER_PENALTY: kappa={stranger_kappa:.3f} "
                f"(n={self._ticker_stats[signal.ticker]['n']}, "
                f"wins={self._ticker_stats[signal.ticker]['wins']})"
            )

        # ----------------------------------------------------------
        # Composite risk calculation
        # ----------------------------------------------------------
        composite_risk = (
            kelly_risk
            * vol_scalar
            * vol_target_scalar
            * cvar_scalar
            * regime_scalar
            * momentum_crash_scalar
            * erc_scalar
            * streak_scalar
            * conf_scalar
            * tod_scalar
            * corr_scalar
            * shock_recovery_scalar
            * stranger_kappa
        )

        # ----------------------------------------------------------
        # Circuit breaker size multiplier (applied after all factors)
        # ----------------------------------------------------------
        if cb_size_multiplier != 1.0:
            composite_risk *= cb_size_multiplier
            factors["cb_size_multiplier"] = cb_size_multiplier
            log_parts.append(f"cb_multiplier={cb_size_multiplier:.3f}")

        # Floor: never go below zero
        composite_risk = max(0.0, composite_risk)

        log_parts.append(f"composite_pre_cap={composite_risk:.5f}")

        # ----------------------------------------------------------
        # G-06: Apply ETP financing drag offset for overnight holds
        # ----------------------------------------------------------
        # Check if this position is likely to cross overnight (after 14:00 UK)
        try:
            from zoneinfo import ZoneInfo
            _now_uk = datetime.now(ZoneInfo("Europe/London"))
        except ImportError:
            import pytz
            _now_uk = datetime.now(pytz.timezone("Europe/London"))
        _crosses_overnight = _now_uk.hour >= 14  # After 14:00 UK, likely overnight hold
        _is_inverse = signal.direction == Direction.SHORT
        if _crosses_overnight and composite_risk > 0:
            # 3-01: Compute expected return from R:R ratio and confidence
            # E[return] = WR * (risk * R:R) - (1 - WR) * risk
            # Use signal confidence as win-rate proxy (normalised to 0-1)
            _wr_proxy = min(1.0, max(0.0, signal.confidence / 100.0))
            _per_share_risk_pct = abs(signal.entry - signal.stop) / signal.entry if signal.entry > 0 else 0
            _r_ratio = getattr(signal, "reward_risk_ratio", None) or 2.0  # Default 2:1 R:R
            _expected_return = (
                _wr_proxy * composite_risk * _r_ratio
                - (1.0 - _wr_proxy) * composite_risk
            )
            _drag_adjusted = self._apply_financing_drag(
                _expected_return, _is_inverse, _crosses_overnight,
            )
            if _drag_adjusted <= 0:
                logger.info(
                    "G-06: %s financing drag (E[ret]=%.5f, drag_adj=%.5f) makes trade negative EV — vetoing",
                    signal.ticker, _expected_return, _drag_adjusted,
                )
                return {
                    "risk_pct": 0.0,
                    "risk_dollars": 0.0,
                    "shares": 0,
                    "scaling_factors": {k: round(v, 5) for k, v in factors.items()},
                    "sizing_log": f"G-06: financing drag negative EV (E[ret]={_expected_return:.5f})",
                }

        # ----------------------------------------------------------
        # 8. Portfolio heat check
        # ----------------------------------------------------------
        current_heat = self._compute_portfolio_heat(open_positions, equity)
        remaining_heat = max(0.0, self._max_portfolio_heat - current_heat)
        heat_capped_risk = min(composite_risk, remaining_heat)
        factors["portfolio_heat_used"] = current_heat
        factors["portfolio_heat_remaining"] = remaining_heat
        log_parts.append(
            f"heat={current_heat:.4f} remaining={remaining_heat:.4f}"
        )

        if heat_capped_risk < composite_risk:
            log_parts.append(
                f"HEAT_CAP: {composite_risk:.5f} -> {heat_capped_risk:.5f}"
            )

        # ----------------------------------------------------------
        # Constitutional cap (immutable 0.75%)
        # ----------------------------------------------------------
        final_risk = min(heat_capped_risk, self._immutable_cap)
        if final_risk < heat_capped_risk:
            log_parts.append(
                f"IMMUTABLE_CAP: {heat_capped_risk:.5f} -> {final_risk:.5f}"
            )

        # ----------------------------------------------------------
        # Regime-specific risk cap (tighter than constitutional for
        # RISK_OFF, HIGH_VOLATILITY, SHOCK)
        # A-02: RISK_OFF cap is 0.0 for LONG, 0.005 for INVERSE/SHORT
        # ----------------------------------------------------------
        regime_cap = _REGIME_RISK_CAPS.get(regime, self._immutable_cap)
        if regime == RegimeState.RISK_OFF and signal.direction == Direction.SHORT:
            regime_cap = 0.005  # Allow INVERSE positions at reduced cap
        if final_risk > regime_cap:
            log_parts.append(
                f"REGIME_CAP ({regime.value}): {final_risk:.5f} -> {regime_cap:.5f}"
            )
            final_risk = regime_cap

        factors["regime_risk_cap"] = regime_cap
        factors["final_risk_pct"] = final_risk

        # ----------------------------------------------------------
        # Convert to dollars and shares
        # ----------------------------------------------------------
        risk_dollars = equity * final_risk
        per_share_risk = abs(signal.entry - signal.stop)

        if per_share_risk <= 0 or signal.entry <= 0:
            shares = 0
            risk_dollars = 0.0
            log_parts.append("ZERO_RISK: entry/stop invalid, shares=0")
        else:
            shares = int(risk_dollars / per_share_risk)

            # Cap position value at max_position_pct of equity
            max_position_value = equity * self._max_position_pct
            if shares * signal.entry > max_position_value:
                shares = int(max_position_value / signal.entry)
                log_parts.append(
                    f"POS_CAP: shares capped to {shares} "
                    f"(max {self._max_position_pct:.0%} of equity)"
                )

            # Liquidity haircut: reduce size for illiquid names
            # Look up ADV from config overrides or use a default
            adv = cfg.get_ticker_override(
                signal.ticker, "avg_daily_volume", 0,
            )
            if adv and adv > 0 and shares > 0:
                position_value = shares * signal.entry
                liq_haircut = self.compute_liquidity_haircut(
                    position_value, float(adv), signal.entry,
                )
                if liq_haircut < 1.0:
                    pre_haircut_shares = shares
                    shares = max(1, int(shares * liq_haircut))
                    log_parts.append(
                        f"LIQ_HAIRCUT: {liq_haircut:.2f}x "
                        f"({pre_haircut_shares} -> {shares} shares)"
                    )
                factors["liquidity_haircut"] = liq_haircut
            else:
                factors["liquidity_haircut"] = 1.0

            # Recalculate actual risk based on final share count
            risk_dollars = shares * per_share_risk

        log_parts.append(
            f"FINAL: risk_pct={final_risk:.4f} "
            f"risk_$={risk_dollars:.2f} shares={shares}"
        )

        # ----------------------------------------------------------
        # Decrement SHOCK_RECOVERY counter (once per calendar day, not per signal)
        # ----------------------------------------------------------
        if self._shock_recovery_remaining > 0 and regime != RegimeState.SHOCK:
            today_str = current_time.strftime("%Y-%m-%d")
            with self._lock:
                if today_str != self._shock_recovery_last_decremented:
                    self._shock_recovery_last_decremented = today_str
                    self._shock_recovery_remaining -= 1
                    if self._shock_recovery_remaining == 0:
                        logger.info("SHOCK_RECOVERY: recovery period complete, resuming normal sizing")
                    else:
                        logger.info(
                            "SHOCK_RECOVERY: decremented for %s, %d sessions remaining",
                            today_str, self._shock_recovery_remaining,
                        )

        sizing_log = " | ".join(log_parts)
        logger.info("SIZING [%s %s]: %s", signal.ticker, signal.direction.value, sizing_log)

        return {
            "risk_pct": round(final_risk, 6),
            "risk_dollars": round(risk_dollars, 2),
            "shares": shares,
            "scaling_factors": {k: round(v, 5) for k, v in factors.items()},
            "sizing_log": sizing_log,
        }

    def update_from_trade(self, r_multiple: float, ticker: str = "") -> None:
        """Update internal statistics from a completed trade.

        Must be called after every trade closes to keep Kelly,
        streak detection, and rolling stats current.

        Args:
            r_multiple: The trade's R-multiple outcome. Positive for
                wins (e.g. +2.0R), negative for losses (e.g. -1.0R),
                zero for breakeven.
            ticker: The ticker symbol for per-ticker Bayesian stranger
                penalty tracking. If empty, per-ticker stats are not updated.
        """
        with self._lock:
            record = _TradeRecord(
                r_multiple=r_multiple,
                timestamp=datetime.now(timezone.utc),
            )
            self._trade_history.append(record)

            # Update running stats
            if r_multiple > 0:
                self._total_wins += 1
                self._sum_win_r += r_multiple
            elif r_multiple < 0:
                self._total_losses += 1
                self._sum_loss_r += abs(r_multiple)

            # Increment lifetime trade count for adaptive Kelly
            self._total_trade_count += 1

            # Update consecutive streak tracker
            self._update_streak(r_multiple)

            # C-01: Update per-ticker stats for Bayesian stranger penalty
            if ticker:
                stats = self._ticker_stats[ticker]
                stats["n"] += 1
                if r_multiple > 0:
                    stats["wins"] += 1

        logger.info(
            "TRADE_UPDATE: r=%.2f ticker=%s | wins=%d losses=%d | streak=%s",
            r_multiple,
            ticker or "?",
            self._total_wins,
            self._total_losses,
            self._describe_streak(),
        )

    def update_macro(self, vix: float, spx_3m_return: float) -> None:
        """Update macro state for momentum crash prevention.

        Called from main.py after CrossAssetMacro.update() to feed
        VIX level and SPX 3-month return into the Barroso & Santa-Clara
        (2015) momentum crash guard.

        Args:
            vix: Current VIX spot level (e.g. 18.5).
            spx_3m_return: S&P 500 3-month return as decimal (e.g. -0.05 = -5%).
        """
        with self._lock:
            self._macro_vix = vix
            self._macro_spx_3m_return = spx_3m_return

    def set_erc_optimizer(self, optimizer: object) -> None:
        """Attach the ERC portfolio optimizer for weight-based scaling.

        Called once from main.py after both DynamicSizer and
        ERCPortfolioOptimizer are instantiated. The optimizer's
        get_weight(ticker) method is called during sizing to obtain
        the ERC allocation weight.

        Args:
            optimizer: ERCPortfolioOptimizer instance (or None to disable).
        """
        with self._lock:
            self._erc_optimizer = optimizer
            logger.info(
                "ERC optimizer %s to DynamicSizer",
                "attached" if optimizer is not None else "detached",
            )

    def get_status(self) -> dict:
        """Get current sizer state for dashboard display.

        Returns:
            dict with Kelly stats, streak info, configuration,
            and readiness indicators.
        """
        # Compute Wave 2 scalars outside lock (they acquire lock internally)
        vt_scalar = self._compute_vol_target_scalar()
        cv_scalar = self._compute_cvar_scalar()
        mc_scalar = self._compute_momentum_crash_scalar()

        with self._lock:
            sample_size = len(self._trade_history)
            win_rate = self._win_rate()
            avg_win = self._avg_win_r()
            avg_loss = self._avg_loss_r()
            kelly_full = self._raw_kelly(win_rate, avg_win, avg_loss)
            kelly_half = kelly_full / 2.0
            current_risk = self._compute_kelly()

            streak_desc = self._describe_streak()

            return {
                "engine": "DynamicSizer",
                "sample_size": sample_size,
                "sufficient_data": sample_size >= self._kelly_min_trades,
                "win_rate_pct": round(win_rate * 100, 1),
                "avg_win_r": round(avg_win, 3),
                "avg_loss_r": round(avg_loss, 3),
                "full_kelly_pct": round(kelly_full * 100, 3),
                "half_kelly_pct": round(kelly_half * 100, 3),
                "current_base_risk_pct": round(current_risk * 100, 4),
                "immutable_cap_pct": round(self._immutable_cap * 100, 2),
                "max_portfolio_heat_pct": round(self._max_portfolio_heat * 100, 1),
                "max_position_pct": round(self._max_position_pct * 100, 1),
                "streak": streak_desc,
                "vol_reference_atr": self._vol_reference,
                "total_trades_tracked": self._total_wins + self._total_losses,
                "shock_recovery_sessions_remaining": self._shock_recovery_remaining,
                "last_regime": self._last_regime.value if self._last_regime else None,
                "regime_risk_caps": {k.value: v for k, v in _REGIME_RISK_CAPS.items()},
                # Wave 2 — advanced risk scalars
                "vol_target_scalar": round(vt_scalar, 4),
                "cvar_scalar": round(cv_scalar, 4),
                "momentum_crash_scalar": round(mc_scalar, 4),
                "macro_vix": round(self._macro_vix, 2),
                "macro_spx_3m_return": round(self._macro_spx_3m_return, 4),
                "vol_target_annualized": self._vol_target,
                "erc_optimizer_attached": self._erc_optimizer is not None,
                # C-01: Bayesian stranger penalty
                "tickers_tracked": len(self._ticker_stats),
                "ticker_stats_summary": {
                    t: {"n": s["n"], "wins": s["wins"]}
                    for t, s in sorted(
                        self._ticker_stats.items(),
                        key=lambda x: x[1]["n"],
                        reverse=True,
                    )[:10]  # Top 10 most traded tickers
                },
            }

    def load_history(
        self,
        r_multiples: list[float],
        tickers: list[str] | None = None,
    ) -> None:
        """Bulk-load historical trade results on startup.

        Replays trades in order to rebuild Kelly stats and streak
        state. Use this when initialising from the database.

        Args:
            r_multiples: Ordered list of historical R-multiples
                (oldest first).
            tickers: Optional parallel list of ticker symbols for
                each trade (same length as r_multiples). Used to
                hydrate per-ticker stats for the Bayesian stranger
                penalty (C-01). If None, per-ticker stats are not
                populated from history.
        """
        with self._lock:
            self._trade_history.clear()
            self._total_wins = 0
            self._total_losses = 0
            self._sum_win_r = 0.0
            self._sum_loss_r = 0.0
            self._consecutive_results.clear()
            self._ticker_stats.clear()

            for i, r in enumerate(r_multiples):
                self._trade_history.append(
                    _TradeRecord(r_multiple=r)
                )
                if r > 0:
                    self._total_wins += 1
                    self._sum_win_r += r
                elif r < 0:
                    self._total_losses += 1
                    self._sum_loss_r += abs(r)
                self._update_streak(r)

                # C-01: Hydrate per-ticker stats
                if tickers and i < len(tickers) and tickers[i]:
                    stats = self._ticker_stats[tickers[i]]
                    stats["n"] += 1
                    if r > 0:
                        stats["wins"] += 1

            # 1-08: Set total trade count so adaptive Kelly uses correct phase
            self._total_trade_count = len(r_multiples)

        logger.info(
            "Loaded %d historical trades | WR=%.1f%% | streak=%s | tickers_tracked=%d",
            len(r_multiples),
            self._win_rate() * 100,
            self._describe_streak(),
            len(self._ticker_stats),
        )

    # ------------------------------------------------------------------
    # Adaptive Kelly (Portfolio-Level) — Sprint 2 Feature #19
    # ------------------------------------------------------------------

    def set_trade_count(self, count: int) -> None:
        """Set the total lifetime trade count for adaptive Kelly scaling.

        This should be called on startup with the historical trade count
        from the database. It determines the estimation confidence level
        used to scale the Kelly fraction.

        Args:
            count: Total number of trades completed in system lifetime.
        """
        with self._lock:
            self._total_trade_count = max(0, count)
        logger.info(
            "Adaptive Kelly: trade count set to %d (phase=%s)",
            self._total_trade_count,
            "early" if count < 50 else ("growing" if count < 200 else "mature"),
        )

    @staticmethod
    def compute_adaptive_kelly_fraction(
        trade_count: int,
        base_fraction: float = 0.50,
    ) -> float:
        """Compute the adaptive Kelly fraction based on estimation confidence.

        Early in a system's life, Kelly estimates are unreliable due to
        small sample sizes. This method dynamically scales the Kelly
        fraction from conservative (quarter-Kelly) to the target
        (half-Kelly) as the trade count grows.

        Args:
            trade_count: Total number of completed trades.
            base_fraction: Target Kelly fraction for mature systems.
                Default 0.50 (half-Kelly).

        Returns:
            Adaptive Kelly multiplier between 0.25 and base_fraction.
        """
        try:
            if trade_count < 0:
                trade_count = 0

            # Early life (< 50 trades): quarter-Kelly due to high estimation error
            if trade_count < 50:
                return 0.25

            # Growing (50-200 trades): linear interpolation from 0.25 to base_fraction
            if trade_count <= 200:
                t = (trade_count - 50) / 150.0  # 0.0 at 50, 1.0 at 200
                return 0.25 + t * (base_fraction - 0.25)

            # Mature (> 200 trades): use base_fraction (half-Kelly)
            return base_fraction

        except Exception:
            logger.exception("Failed to compute adaptive Kelly fraction")
            return 0.25  # Conservative default on error

    @staticmethod
    def compute_portfolio_kelly(
        positions: list[dict],
        correlation_matrix: dict,
    ) -> float:
        """Compute portfolio-level Kelly fraction accounting for correlations.

        Uses a simplified Markowitz-Kelly blend to determine the optimal
        aggregate portfolio exposure. Positions that are highly correlated
        reduce the optimal total exposure.

        Formula: f* = (mu - rf) / sigma^2  (simplified for the portfolio)

        Where:
        - mu = portfolio expected return (weighted average of position returns)
        - rf = risk-free rate (assumed 0 for simplicity)
        - sigma^2 = portfolio variance accounting for correlations

        Args:
            positions: List of position dicts with keys:
                - ticker (str): Ticker symbol
                - size_pct (float): Position size as fraction of portfolio
                - expected_return (float): Expected return for this position
                - win_prob (float): Win probability (0-1)
            correlation_matrix: Dict of dicts mapping ticker -> ticker -> correlation.
                E.g. {"NVDA": {"AMD": 0.8, "SPY": 0.5}, ...}

        Returns:
            Optimal aggregate exposure as a fraction (0.0 to 1.0).
            Returns 0.0 on error or if no valid positions.
        """
        try:
            if not positions:
                return 0.0

            # Compute portfolio expected return (weighted by size)
            total_weight = sum(p.get("size_pct", 0) for p in positions)
            if total_weight <= 0:
                return 0.0

            # Normalise weights
            weights = [p.get("size_pct", 0) / total_weight for p in positions]
            expected_returns = [
                p.get("expected_return", 0) * p.get("win_prob", 0.5)
                for p in positions
            ]

            portfolio_mu = sum(w * r for w, r in zip(weights, expected_returns))

            # Compute portfolio variance with correlations
            # sigma_p^2 = sum_i sum_j w_i * w_j * sigma_i * sigma_j * rho_ij
            # Use expected return volatility as a proxy for sigma
            # (simplified: sigma_i ~ |expected_return_i| as risk measure)
            tickers = [p.get("ticker", f"pos_{i}") for i, p in enumerate(positions)]
            sigmas = [abs(p.get("expected_return", 0.01)) for p in positions]

            portfolio_variance = 0.0
            n = len(positions)

            for i in range(n):
                for j in range(n):
                    if i == j:
                        rho = 1.0
                    else:
                        # Look up correlation
                        rho = (
                            correlation_matrix
                            .get(tickers[i], {})
                            .get(tickers[j], None)
                        )
                        if rho is None:
                            rho = (
                                correlation_matrix
                                .get(tickers[j], {})
                                .get(tickers[i], 0.3)
                            )

                    portfolio_variance += (
                        weights[i] * weights[j]
                        * sigmas[i] * sigmas[j]
                        * rho
                    )

            if portfolio_variance <= 0:
                return 0.0

            # Kelly: f* = mu / sigma^2 (with rf = 0)
            kelly_fraction = portfolio_mu / portfolio_variance

            # Clamp to 0.0-1.0
            kelly_fraction = max(0.0, min(1.0, kelly_fraction))

            logger.debug(
                "PORTFOLIO_KELLY: mu=%.4f, var=%.6f, f*=%.4f, positions=%d",
                portfolio_mu, portfolio_variance, kelly_fraction, n,
            )

            return round(kelly_fraction, 4)

        except Exception:
            logger.exception("Failed to compute portfolio Kelly")
            return 0.0

    # ------------------------------------------------------------------
    # Private: G-01 R-04 Deployment Cap & G-03 R-07 CVaR Gate
    # ------------------------------------------------------------------

    def _check_deployment_cap(
        self,
        signal: Signal,
        open_positions: list[Any],
        equity: float,
    ) -> dict | None:
        """G-01/R-04: Check if total deployed capital exceeds 40% of equity.

        Returns a zero-size dict (veto) if the cap is breached, or None
        to allow the sizing pipeline to continue.
        """
        total_deployed = 0.0
        for pos in open_positions:
            notional = getattr(pos, "notional", None)
            if notional is None:
                # Fallback: shares * entry_price
                shares = getattr(pos, "shares", 0) or 0
                entry = getattr(pos, "entry_price", 0.0) or getattr(pos, "entry", 0.0) or 0.0
                notional = shares * entry
            total_deployed += notional

        cap = equity * self._max_deployment
        if total_deployed >= cap:
            logger.warning(
                "G-01 DEPLOYMENT CAP: deployed=%.0f >= cap=%.0f (%.0f%% equity) — vetoing %s",
                total_deployed, cap, self._max_deployment * 100, signal.ticker,
            )
            return {
                "risk_pct": 0.0,
                "risk_dollars": 0.0,
                "shares": 0,
                "scaling_factors": {"deployment_cap_veto": 1.0},
                "sizing_log": f"G-01: deployment {total_deployed:.0f} >= cap {cap:.0f}",
            }
        return None  # OK — continue sizing

    def _check_portfolio_cvar_gate(self, equity: float) -> dict | None:
        """G-03/R-07: Check if portfolio CVaR exceeds 3% of equity.

        Uses rolling daily portfolio returns to compute CVaR_95.
        Returns a zero-size dict (veto) if tail risk is excessive,
        or None to allow the sizing pipeline to continue.
        """
        import numpy as np

        with self._lock:
            if len(self._portfolio_daily_returns) < 20:
                return None  # Not enough data — pass through

            returns = np.array(list(self._portfolio_daily_returns))

        var_5 = np.percentile(returns, 5)
        tail = returns[returns <= var_5]
        cvar = tail.mean() if len(tail) > 0 else 0.0

        if abs(cvar) > self._portfolio_cvar_max:
            logger.warning(
                "G-03 CVaR GATE: CVaR=%.4f > %.1f%% threshold — vetoing new entries",
                cvar, self._portfolio_cvar_max * 100,
            )
            return {
                "risk_pct": 0.0,
                "risk_dollars": 0.0,
                "shares": 0,
                "scaling_factors": {"cvar_gate_veto": abs(cvar)},
                "sizing_log": f"G-03: portfolio CVaR {cvar:.4f} exceeds {self._portfolio_cvar_max:.2%}",
            }
        return None  # OK — continue sizing

    # ------------------------------------------------------------------
    # Private: Kelly Criterion
    # ------------------------------------------------------------------

    def _win_rate(self) -> float:
        """Compute win rate from trade history (decisive trades only)."""
        decisive = self._total_wins + self._total_losses
        if decisive == 0:
            return 0.0
        return self._total_wins / decisive

    def _avg_win_r(self) -> float:
        """Average R-multiple of winning trades."""
        if self._total_wins == 0:
            return 0.0
        return self._sum_win_r / self._total_wins

    def _avg_loss_r(self) -> float:
        """Average R-multiple of losing trades (returned as positive)."""
        if self._total_losses == 0:
            return 1.0  # Default assumption: 1R average loss
        return self._sum_loss_r / self._total_losses

    @staticmethod
    def _raw_kelly(win_rate: float, avg_win: float, avg_loss: float) -> float:
        """Compute raw Kelly fraction.

        Formula: K = W - [(1-W) / R]
        Where W = win rate, R = avg_win / avg_loss
        """
        if avg_loss == 0:
            return 0.0
        win_loss_ratio = avg_win / avg_loss
        if win_loss_ratio == 0:
            return 0.0
        return win_rate - ((1.0 - win_rate) / win_loss_ratio)

    def _compute_kelly(self) -> float:
        """Compute half-Kelly base risk percentage.

        If insufficient data (fewer than min_trades), returns the
        conservative default risk instead of an unreliable estimate.
        """
        sample_size = len(self._trade_history)

        if sample_size < self._kelly_min_trades:
            return self._kelly_default_risk

        win_rate = self._win_rate()
        avg_win = self._avg_win_r()
        avg_loss = self._avg_loss_r()

        full_kelly = self._raw_kelly(win_rate, avg_win, avg_loss)
        half_kelly = full_kelly / 2.0

        # If Kelly says don't trade, return zero
        if half_kelly <= 0:
            return 0.0

        # Cap at constitutional maximum
        return min(half_kelly, self._immutable_cap)

    # ------------------------------------------------------------------
    # Private: Volatility Scaling
    # ------------------------------------------------------------------

    def _compute_volatility_scalar(self, signal: Signal) -> float:
        """Scale inversely with realized volatility.

        When ATR% is higher than the reference level, positions are
        smaller. When volatility is low, positions can be larger
        (up to the ceiling).

        Uses the signal's atr_pct if available via the entry/stop
        spread as a proxy; otherwise falls back to a default of 1.0.
        """
        # Use stop distance as a volatility proxy (stop is typically 1-2 ATR)
        if signal.entry <= 0:
            return 1.0

        stop_distance_pct = abs(signal.entry - signal.stop) / signal.entry

        if stop_distance_pct <= 0:
            return 1.0

        # Inverse scaling: reference / actual
        # Higher vol (wider stop) = smaller scalar
        raw_scalar = self._vol_reference / stop_distance_pct

        # Clamp between floor and ceiling
        clamped = max(self._vol_floor, min(self._vol_ceiling, raw_scalar))
        return clamped

    # ------------------------------------------------------------------
    # Private: Moreira & Muir (2017) Volatility Targeting
    # ------------------------------------------------------------------

    def _compute_vol_target_scalar(self) -> float:
        """Scale position by min(1.0, target_vol / realized_vol_21d).

        Moreira & Muir (2017): when recent realized volatility exceeds
        the target, reduce position size proportionally. Uses rolling
        21-trade R-multiple standard deviation as a volatility proxy,
        annualized by sqrt(252).

        Returns 1.0 (pass-through) when:
        - Feature flag v95_vol_target_enabled is false
        - Fewer than 21 trades in history
        - Realized vol is at or below target
        """
        if not cfg.get("v95_vol_target_enabled", True):
            return 1.0

        with self._lock:
            n = len(self._trade_history)
            if n < _VOL_TARGET_LOOKBACK:
                return 1.0

            # Extract last 21 R-multiples
            recent = [
                t.r_multiple
                for t in list(self._trade_history)[-_VOL_TARGET_LOOKBACK:]
            ]

        # Realized vol: stdev of R-multiples, annualized
        mean_r = sum(recent) / len(recent)
        variance = sum((r - mean_r) ** 2 for r in recent) / (len(recent) - 1)
        daily_vol = math.sqrt(variance)
        annualized_vol = daily_vol * math.sqrt(252)

        if annualized_vol <= 0:
            return 1.0

        raw_scalar = min(1.0, self._vol_target / annualized_vol)
        return max(_VOL_TARGET_FLOOR, raw_scalar)

    # ------------------------------------------------------------------
    # Private: Rockafellar & Uryasev (2000) CVaR Scaling
    # ------------------------------------------------------------------

    def _compute_cvar_scalar(self) -> float:
        """Scale down when rolling 60-trade 5% CVaR breaches -2.0R.

        CVaR (Conditional Value-at-Risk / Expected Shortfall) measures
        the average loss in the worst 5% of trades. When it deteriorates
        beyond -2.0R, position size is scaled down proportionally:
            scalar = min(1.0, 2.0 / abs(cvar))

        Returns 1.0 (pass-through) when:
        - Feature flag v95_cvar_scaling_enabled is false
        - Fewer than 30 trades in history
        - CVaR is above -2.0R (healthy tail)
        """
        if not cfg.get("v95_cvar_scaling_enabled", True):
            return 1.0

        with self._lock:
            n = len(self._trade_history)
            if n < 30:
                return 1.0

            # Extract up to last 60 R-multiples
            lookback = min(n, _CVAR_LOOKBACK)
            r_values = [
                t.r_multiple for t in list(self._trade_history)[-lookback:]
            ]

        # Sort ascending (worst trades first)
        r_sorted = sorted(r_values)

        # Bottom 5% (minimum 3 trades)
        tail_count = max(3, int(len(r_sorted) * _CVAR_PERCENTILE))
        tail = r_sorted[:tail_count]

        # CVaR = mean of worst trades
        cvar = sum(tail) / len(tail)

        if cvar >= -_CVAR_THRESHOLD_R:
            return 1.0

        # Scale down proportionally
        raw_scalar = min(1.0, _CVAR_THRESHOLD_R / abs(cvar))
        return max(_CVAR_FLOOR, raw_scalar)

    # ------------------------------------------------------------------
    # Private: Barroso & Santa-Clara (2015) Momentum Crash Prevention
    # ------------------------------------------------------------------

    def _compute_momentum_crash_scalar(self) -> float:
        """Reduce sizing 40% when VIX > 30 AND SPX 3-month return < 0.

        Barroso & Santa-Clara (2015): momentum strategies are vulnerable
        to crash-reversal drawdowns when volatility spikes coincide with
        declining markets. This guard reduces exposure during those
        conditions to protect capital.

        Returns 1.0 (pass-through) when:
        - Feature flag v95_momentum_crash_guard_enabled is false
        - VIX <= 30
        - SPX 3-month return >= 0
        """
        if not cfg.get("v95_momentum_crash_guard_enabled", True):
            return 1.0

        if (
            self._macro_vix > _MOMENTUM_CRASH_VIX_THRESHOLD
            and self._macro_spx_3m_return < 0.0
        ):
            return _MOMENTUM_CRASH_SCALAR

        return 1.0

    def _compute_erc_scalar(self, ticker: str) -> float:
        """Apply ERC portfolio weight as a sizing scalar.

        Maillard, Roncalli & Teiletche (2010): Equal Risk Contribution
        weights redistribute capital so each asset contributes equally
        to total portfolio risk. The ERC weight for a ticker is converted
        into a multiplicative scalar relative to equal weight:

            scalar = min(1.0, erc_weight * N)

        Where N = number of assets in the ERC universe. This ensures:
        - Equal-weight baseline maps to scalar = 1.0 (no change)
        - Overweight assets are capped at 1.0 (V7.0 immutability)
        - Underweight assets (high correlation clusters) get scalar < 1.0

        Returns 1.0 (pass-through) when:
        - Feature flag v95_erc_allocation_enabled is false
        - No ERC optimizer is attached
        - Ticker has no ERC weight assigned
        """
        if not cfg.get("v95_erc_allocation_enabled", True):
            return 1.0

        optimizer = self._erc_optimizer
        if optimizer is None:
            return 1.0

        try:
            weights = optimizer.get_weights()
            if not weights or ticker not in weights:
                return 1.0

            n = len(weights)
            if n <= 1:
                return 1.0

            erc_w = weights[ticker]
            equal_w = 1.0 / n

            # Scalar relative to equal weight, capped at 1.0 (can only reduce)
            scalar = min(1.0, erc_w / equal_w) if equal_w > 0 else 1.0

            return max(0.10, scalar)  # Floor: never below 10% from ERC alone

        except Exception as e:
            logger.debug("ERC scalar failed for %s: %s", ticker, e)
            return 1.0

    # ------------------------------------------------------------------
    # Private: Bayesian Stranger Penalty (C-01)
    # ------------------------------------------------------------------

    def _compute_stranger_penalty(self, ticker: str) -> float:
        """Compute Bayesian stranger penalty for an untested ticker.

        Uses a Beta-Binomial posterior to penalise tickers with few
        historical trades. The formula:

            kappa = min(1, sqrt(n / 30)) * (alpha + wins) / (alpha + beta + n)

        Where alpha=beta=2 (weakly informative prior centered at 50%).

        A brand-new ticker (n=0) gets kappa = 2/(2+2+0) = 0.50 (half size).
        After 30 trades at 50% WR: sqrt(30/30) * (2+15)/(2+2+30) = 0.50.
        After 30 trades at 60% WR: 1.0 * (2+18)/(2+2+30) = 0.588.
        After 60 trades at 60% WR: 1.0 * (2+36)/(2+2+60) = 0.594.
        Practical effect: new tickers start at 50% size and ramp up.

        Returns:
            Scalar between ~0.5 and 1.0 to multiply against position size.
        """
        stats = self._ticker_stats.get(ticker)
        if stats is None:
            # Completely unknown ticker — prior only
            return (_STRANGER_ALPHA) / (_STRANGER_ALPHA + _STRANGER_BETA)

        n = stats["n"]
        wins = stats["wins"]

        # Maturity ramp: sqrt(n / maturity_threshold), capped at 1.0
        maturity = min(1.0, math.sqrt(n / _STRANGER_MATURITY_TRADES)) if n > 0 else 0.0

        # Beta-Binomial posterior mean: (alpha + wins) / (alpha + beta + n)
        posterior_mean = (_STRANGER_ALPHA + wins) / (_STRANGER_ALPHA + _STRANGER_BETA + n)

        # Finding 14: kappa asymptotes to 1.0 — a 50% WR ticker reaches 1.0 at maturity.
        # Scale posterior_mean relative to the prior (0.5) so that kappa = maturity
        # when WR = 50%, and kappa < maturity when WR < 50%.
        kappa = min(1.0, maturity * (posterior_mean / 0.5))

        # Floor: never go below the pure prior (n=0 case)
        prior_mean = _STRANGER_ALPHA / (_STRANGER_ALPHA + _STRANGER_BETA)
        kappa = max(prior_mean, kappa)

        # Ceiling: never exceed 1.0
        return min(1.0, kappa)

    # ------------------------------------------------------------------
    # Private: Regime Scaling
    # ------------------------------------------------------------------

    def _compute_regime_scalar(
        self, regime: RegimeState, direction: Direction = Direction.LONG,
    ) -> float:
        """Map the current regime to its risk multiplier.

        Trending regimes get full risk. Choppy/range-bound gets 0.5x.
        RISK_OFF = 0.0 for LONG, 0.50 for SHORT/INVERSE (A-02).
        Shock disables trading entirely (0x).
        """
        # A-02: RISK_OFF allows INVERSE/SHORT at reduced size for hedging
        if regime == RegimeState.RISK_OFF and direction == Direction.SHORT:
            return _RISK_OFF_INVERSE_MULTIPLIER
        return _REGIME_MULTIPLIERS.get(regime, 0.5)

    # ------------------------------------------------------------------
    # Private: Streak Adjustment
    # ------------------------------------------------------------------

    def _update_streak(self, r_multiple: float) -> None:
        """Update the consecutive results tracker.

        Maintains a list of same-direction results. Resets when
        the direction changes.
        """
        if r_multiple == 0:
            # Breakeven does not break or extend a streak
            return

        current_direction = 1 if r_multiple > 0 else -1

        if not self._consecutive_results:
            self._consecutive_results = [current_direction]
            return

        last_direction = 1 if self._consecutive_results[-1] > 0 else -1

        if current_direction == last_direction:
            self._consecutive_results.append(current_direction)
        else:
            # Direction changed: reset streak
            self._consecutive_results = [current_direction]

    def _compute_streak_scalar(self) -> float:
        """Compute position size adjustment based on win/loss streaks.

        After 3+ consecutive losses, reduce by 25% per additional loss.
        After 3+ consecutive wins, allow 10% increase per additional win
        (capped at 1.3x to avoid overconfidence).
        """
        if not self._consecutive_results:
            return 1.0

        streak_len = len(self._consecutive_results)
        is_winning = self._consecutive_results[-1] > 0

        if is_winning and streak_len >= _STREAK_WIN_THRESHOLD:
            extra = streak_len - _STREAK_WIN_THRESHOLD
            boost = 1.0 + (extra * _STREAK_WIN_BOOST)
            return min(boost, _STREAK_WIN_CAP)

        if not is_winning and streak_len >= _STREAK_LOSS_THRESHOLD:
            extra = streak_len - _STREAK_LOSS_THRESHOLD + 1  # Reduce AT the threshold, not beyond
            reduction = 1.0 - (extra * _STREAK_LOSS_REDUCTION)
            return max(0.10, reduction)  # Floor at 10% of normal size

        return 1.0

    def _describe_streak(self) -> str:
        """Human-readable description of the current streak."""
        if not self._consecutive_results:
            return "none"
        streak_len = len(self._consecutive_results)
        direction = "W" if self._consecutive_results[-1] > 0 else "L"
        return f"{streak_len}{direction}"

    # ------------------------------------------------------------------
    # Private: Confidence Scaling
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_confidence_scalar(confidence: float) -> float:
        """Linearly map signal confidence to a size multiplier.

        60 confidence -> 0.6x multiplier (minimum)
        90 confidence -> 1.2x multiplier (maximum)
        Below 60 -> 0.6x (floor)
        Above 90 -> 1.2x (ceiling)
        """
        if confidence <= _CONF_MIN:
            return _CONF_MULT_MIN
        if confidence >= _CONF_MAX:
            return _CONF_MULT_MAX

        # Linear interpolation
        t = (confidence - _CONF_MIN) / (_CONF_MAX - _CONF_MIN)
        return _CONF_MULT_MIN + t * (_CONF_MULT_MAX - _CONF_MULT_MIN)

    # ------------------------------------------------------------------
    # Private: Time-of-Day Scaling (C-09: LSE-aware)
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_tod_scalar(
        current_time: datetime, ticker: str = "",
    ) -> tuple[float, str]:
        """Determine position size scalar based on time of day.

        C-09: LSE tickers (.L suffix) use UK-time windows with LSE-specific
        intraday alpha profile. US tickers use ET windows.

        Market edge is strongest at the open and weakest at close.
        Outside market hours returns 0.5x (pre/post-market or swing).
        """
        # C-09: Route .L tickers through LSE windows (UK local time)
        if ticker.upper().endswith(".L"):
            uk_time = current_time.astimezone(_LSE_TZ)
            t = uk_time.time()

            for window_start, window_end, scalar, label in _TOD_WINDOWS_LSE:
                if window_start <= t < window_end:
                    return scalar, label

            # Outside LSE windows
            if t < time(8, 0):
                return 0.50, "lse_pre_market"
            return 0.50, "lse_after_hours"

        # US tickers — original ET-based windows
        t = current_time.time()

        for window_start, window_end, scalar, label in _TOD_WINDOWS:
            if window_start <= t < window_end:
                return scalar, label

        # Outside defined windows (pre-market, after-hours)
        if t < time(9, 30):
            return 0.50, "pre_market"
        return 0.50, "after_hours"

    # ------------------------------------------------------------------
    # Private: Correlation Penalty
    # ------------------------------------------------------------------

    def _compute_correlation_scalar(
        self,
        signal: Signal,
        open_positions: list[Any],
    ) -> float:
        """Reduce size if the new trade is highly correlated with open positions.

        Uses a simplified sector/ticker overlap heuristic since
        real-time correlation matrices are expensive. Two positions
        in the same ticker, or same-direction leveraged ETPs on the
        same underlying, are treated as perfectly correlated.

        For each highly correlated existing position, the penalty
        compounds: scalar = (penalty_factor) ^ n_correlated.
        """
        if not open_positions:
            return 1.0

        correlated_count = 0

        for pos in open_positions:
            # Same ticker = perfectly correlated
            pos_ticker = getattr(pos, "ticker", "")
            if pos_ticker and pos_ticker == signal.ticker:
                correlated_count += 1
                continue

            # Same direction in the same "family" of instruments
            # (e.g., TQQQ and QQQ, or SQQQ and QQQ)
            pos_direction = getattr(pos, "direction", None)
            if self._are_instruments_correlated(signal.ticker, pos_ticker):
                if pos_direction == signal.direction:
                    correlated_count += 1

        if correlated_count == 0:
            return 1.0

        # Compound penalty: each correlated position halves the scalar
        scalar = _CORRELATION_PENALTY_FACTOR ** correlated_count
        return max(0.10, scalar)  # Floor at 10%

    @staticmethod
    def _are_instruments_correlated(ticker_a: str, ticker_b: str) -> bool:
        """Heuristic check for instrument-level correlation.

        C-08: Uses UNDERLYING_MAP from isa_universe.py for ISA .L tickers,
        mapping them to their underlying (e.g. QQQ3.L -> NASDAQ, NVD3.L -> NVIDIA).
        Two tickers sharing the same underlying are correlated.

        Falls back to US-only family lookup for non-.L tickers.
        This is a fast lookup -- no API calls needed.
        """
        # C-08: Check UNDERLYING_MAP first (covers all ISA .L tickers)
        ul_a = UNDERLYING_MAP.get(ticker_a)
        ul_b = UNDERLYING_MAP.get(ticker_b)

        if ul_a and ul_b:
            # Both are ISA tickers with known underlyings
            return ul_a == ul_b

        if ul_a or ul_b:
            # One is an ISA ticker, one is not -- check if the non-ISA
            # ticker IS the underlying (e.g. NVD3.L underlying = "NVIDIA",
            # and the other ticker is "NVDA")
            # This handles mixed ISA + US positions if they ever co-exist
            return False

        # Fallback: US-only families for non-ISA tickers
        _FAMILIES: list[set[str]] = [
            {"QQQ", "TQQQ", "SQQQ", "QLD", "PSQ"},
            {"SPY", "SPXL", "SPXS", "SSO", "SH", "UPRO"},
            {"IWM", "TNA", "TZA"},
            {"AAPL"},
            {"MSFT"},
            {"NVDA", "SOXL", "SOXS", "SMH"},
            {"TSLA"},
            {"AMZN"},
            {"META"},
            {"GOOG", "GOOGL"},
        ]

        a_upper = ticker_a.upper()
        b_upper = ticker_b.upper()

        for family in _FAMILIES:
            if a_upper in family and b_upper in family:
                return True

        return False

    # ------------------------------------------------------------------
    # Private: Portfolio Heat
    # ------------------------------------------------------------------

    def _compute_portfolio_heat(
        self,
        open_positions: list[Any],
        equity: float,
    ) -> float:
        """Calculate total open risk as a fraction of equity.

        Sums risk_dollars across all open positions and divides by
        current equity to get the heat percentage.
        """
        if equity <= 0 or not open_positions:
            return 0.0

        total_risk = 0.0
        for pos in open_positions:
            risk = getattr(pos, "risk_dollars", 0.0)
            if risk and risk > 0:
                total_risk += risk

        return total_risk / equity

    # ------------------------------------------------------------------
    # Liquidity-Adjusted Position Sizing
    # ------------------------------------------------------------------

    @staticmethod
    def compute_liquidity_haircut(
        position_value: float,
        avg_daily_volume: float,
        avg_price: float,
    ) -> float:
        """Compute a liquidity haircut to reduce position size for illiquid names.

        Estimates the number of days required to liquidate a position at
        10% participation rate (a common institutional constraint to avoid
        excessive market impact). Larger positions relative to daily
        liquidity receive progressively steeper haircuts.

        Args:
            position_value: Dollar value of the intended position
                (shares * entry_price).
            avg_daily_volume: Average daily volume in shares over the
                recent period (e.g. 20-day ADV).
            avg_price: Average price per share (used to convert volume
                to dollar terms).

        Returns:
            Haircut multiplier between 0.40 and 1.0:
                1.00 — no haircut (liquidation <= 0.5 days)
                0.80 — 20% reduction (0.5 < liquidation <= 1.0 days)
                0.60 — 40% reduction (1.0 < liquidation <= 2.0 days)
                0.40 — 60% reduction (liquidation > 2.0 days)
        """
        if avg_daily_volume <= 0 or avg_price <= 0 or position_value <= 0:
            return 1.0

        # Dollar liquidity available per day at 10% participation
        daily_dollar_liquidity = avg_daily_volume * avg_price * 0.10

        if daily_dollar_liquidity <= 0:
            return 0.40  # Cannot liquidate — maximum haircut

        liquidation_days = position_value / daily_dollar_liquidity

        if liquidation_days > 2.0:
            return 0.40
        if liquidation_days > 1.0:
            return 0.60
        if liquidation_days > 0.5:
            return 0.80

        return 1.0

    # ------------------------------------------------------------------
    # G-06: ETP Financing Cost Offset
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_financing_drag(
        expected_return: float, is_inverse: bool, crosses_overnight: bool,
    ) -> float:
        """G-06: Subtract financing drag ONLY if hold crosses overnight boundary.

        Leveraged ETPs incur daily financing costs that erode returns on
        overnight holds. Long leveraged: -2 bps/day. Inverse: -4 bps/day.

        Args:
            expected_return: Pre-drag expected return as a decimal (e.g. 0.005).
            is_inverse: True for inverse/short ETPs (QQQS.L, 3USS.L).
            crosses_overnight: True if the position will be held past the
                overnight financing cutoff.

        Returns:
            Expected return with financing drag subtracted (unchanged if
            intraday-only hold).
        """
        if not crosses_overnight:
            return expected_return
        drag = 0.0004 if is_inverse else 0.0002  # 4 bps or 2 bps
        return expected_return - drag

    # ------------------------------------------------------------------
    # F-15: Correlation Brake Notional Risk Cap
    # ------------------------------------------------------------------

    def _get_position_notional(
        self, ticker: str, open_positions: list[Any] | None = None,
    ) -> float:
        """Return the current notional value of open positions for a ticker.

        Sums shares * entry_price for all open positions matching the
        given ticker symbol.

        Args:
            ticker: Ticker symbol to look up.
            open_positions: List of position objects with ticker, shares,
                and entry_price (or entry) attributes. If None, returns 0.0.

        Returns:
            Total notional value for the ticker, or 0.0 if not found.
        """
        if not open_positions:
            return 0.0

        total = 0.0
        for pos in open_positions:
            pos_ticker = getattr(pos, "ticker", "")
            if pos_ticker == ticker:
                shares = getattr(pos, "shares", 0) or 0
                price = getattr(pos, "entry_price", 0.0) or getattr(pos, "entry", 0.0) or 0.0
                total += shares * price
        return total

    def _check_cluster_notional_cap(
        self,
        cluster_tickers: list,
        new_notional: float,
        max_risk_per_trade: float,
    ) -> bool:
        """F-15: Combined notional of correlation cluster must not exceed
        1.5x max risk per trade.

        Prevents concentration risk where multiple correlated positions
        (e.g. QQQ3.L + NVD3.L + GPT3.L — all tech-heavy) accumulate
        excessive combined notional exposure.

        Args:
            cluster_tickers: List of tickers in the same correlation cluster.
            new_notional: Notional value of the proposed new position.
            max_risk_per_trade: Maximum risk per trade in dollar terms.

        Returns:
            True if the new position would BREACH the 1.5x cap (block entry).
            False if within limits (allow entry).
        """
        existing_notional = sum(
            self._get_position_notional(t) for t in cluster_tickers
        )
        return (existing_notional + new_notional) > (max_risk_per_trade * 1.5)


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def create_sizer(**kwargs: Any) -> DynamicSizer:
    """Factory function for creating a DynamicSizer with config overrides.

    Reads starting_equity and max_portfolio_heat from the YAML config
    if not explicitly provided.

    Args:
        **kwargs: Override any DynamicSizer __init__ parameter.

    Returns:
        Configured DynamicSizer instance.
    """
    defaults = {
        "starting_equity": cfg.get("dynamic_sizer.starting_equity", 10_000.0),
        "max_portfolio_heat": cfg.get("dynamic_sizer.max_portfolio_heat", 0.06),
    }
    defaults.update(kwargs)
    return DynamicSizer(**defaults)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Create a sizer with defaults
    sizer = DynamicSizer(starting_equity=25_000, max_portfolio_heat=0.06)

    # Simulate loading some trade history
    history = [
        1.5, -1.0, 2.0, -1.0, 1.2, -0.8, 1.8, -1.0,
        0.5, -1.0, 2.5, 1.0, -1.0, 1.5, -1.0, 1.0,
        -1.0, 2.0, 1.0, -1.0, 1.5, -0.5, 1.0, -1.0,
    ]
    sizer.load_history(history)

    # Create a mock signal
    test_signal = Signal(
        ticker="AAPL",
        direction=Direction.LONG,
        entry=185.00,
        stop=183.00,
        confidence=78,
    )

    # Calculate with various regimes
    from datetime import datetime
    test_time = datetime(2025, 3, 15, 10, 0, tzinfo=_ET)  # 10:00 AM ET

    for regime in [
        RegimeState.TRENDING_UP_STRONG,
        RegimeState.RANGE_BOUND,
        RegimeState.RISK_OFF,
        RegimeState.SHOCK,
    ]:
        result = sizer.calculate_position_size(
            signal=test_signal,
            regime=regime,
            equity=25_000,
            open_positions=[],
            recent_trades=[],
            current_time=test_time,
        )
        print(f"\n--- {regime.value} ---")
        print(f"  Risk %:  {result['risk_pct'] * 100:.3f}%")
        print(f"  Risk $:  ${result['risk_dollars']:.2f}")
        print(f"  Shares:  {result['shares']}")
        for k, v in result["scaling_factors"].items():
            print(f"  {k}: {v}")

    # Show dashboard status
    print("\n--- STATUS ---")
    status = sizer.get_status()
    for k, v in status.items():
        print(f"  {k}: {v}")
