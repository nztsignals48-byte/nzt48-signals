"""
NZT-48 Trading System -- ThresholdRegistry (AEGIS E-01)
=======================================================
Blood Oath #1: Single frozen registry for ALL trading thresholds.

Every hardcoded threshold in the codebase MUST read from this registry.
No module may define its own thresholds. The registry is frozen at boot
time -- any attempt to mutate it raises ``FrozenInstanceError``.

Canonical sources reconciled at creation time:
  - config/settings.yaml           (immutable_rules, qualification, confidence, etc.)
  - strategies/daily_target.py     (signal thresholds: ADX, RVOL, confidence, RR)
  - qualification/risk_sizer.py    (constitutional risk rules)
  - qualification/circuit_breakers.py  (drawdown, VIX, correlation, loss streaks)
  - signal_engine/unified_risk_gate.py (portfolio heat budget)
  - execution/cost_model.py        (spread veto/watch)

Usage::

    from core.threshold_registry import THRESHOLDS

    if signal.confidence < THRESHOLDS.confidence_floor:
        reject(signal)

    # Or load from YAML overrides at boot:
    from core.threshold_registry import load_thresholds
    reg = load_thresholds("config/settings.yaml")

YAML override keys live under the top-level ``thresholds:`` section in
settings.yaml. Any key not present in YAML keeps its dataclass default.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("nzt48.threshold_registry")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

class ThresholdValidationError(Exception):
    """Raised when threshold values fail sanity checks at boot."""


def _validate_range(
    name: str, value: float, lo: float, hi: float, errors: list[str],
) -> None:
    """Append an error message if *value* is outside [lo, hi]."""
    if not (lo <= value <= hi):
        errors.append(
            f"{name}={value} outside valid range [{lo}, {hi}]"
        )


def _validate_positive(name: str, value: float, errors: list[str]) -> None:
    if value <= 0:
        errors.append(f"{name}={value} must be > 0")


def _validate_non_negative(name: str, value: float, errors: list[str]) -> None:
    if value < 0:
        errors.append(f"{name}={value} must be >= 0")


# ---------------------------------------------------------------------------
# ThresholdRegistry -- frozen dataclass (Blood Oath #1)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ThresholdRegistry:
    """Single frozen registry for ALL trading thresholds.

    All modules MUST read from this registry. No hardcoded thresholds
    anywhere else in the codebase. ``frozen=True`` makes every attribute
    immutable after construction -- assignment raises ``FrozenInstanceError``.

    Categories:
      1. Signal quality gates       (confidence, ADX, RVOL, consensus)
      2. Risk management            (per-trade risk, drawdown, heat, positions)
      3. ETP allocation limits      (3x/5x single + total caps, hold days)
      4. Circuit breaker levels     (drawdown L1-L3, VIX, correlation, loss streaks)
      5. Spread & cost gates        (spread veto, watch, slippage)
      6. Timing                     (stabilization windows)
      7. Black swan detection       (SPY extreme move, flash crash)
    """

    # ===================================================================
    # 1. SIGNAL QUALITY GATES
    # ===================================================================

    # -- Confidence --
    confidence_floor: int = 65             # SK-03: system-wide minimum (settings.yaml says 60, risk_sizer says 65 -- 65 wins)
    confidence_orb_vwap_floor: int = 65    # ORB/VWAP setups
    confidence_bear_bot_floor: int = 80    # Bear bot requires high conviction
    confidence_max: int = 100              # Cap

    # -- ADX (trend strength) -- Wilder (1978)
    adx_floor_fast: int = 15              # T-06 FAST tier: catch trend birth
    adx_floor_slow: int = 20             # T-06 SLOW tier: moderate confirmation
    adx_floor_range_bound: int = 25      # Strict in chop (Shynkevich 2012 JBFA)
    adx_accel_threshold: float = 2.0     # T-06: ADX rising > 2 pts/bar = emerging trend
    adx_accel_min_level: float = 12.0    # CQ-5: only apply accel bonus when ADX >= 12

    # -- RVOL (relative volume) --
    rvol_floor_fast: float = 0.60         # T-07 FAST tier minimum viable liquidity
    rvol_floor_slow: float = 0.65         # T-07 SLOW tier institutional participation
    rvol_floor_lunch: float = 0.50        # T-02: reduced during lunch window
    rvol_floor_range_bound: float = 1.2   # Strict in chop (was 1.5, reconciled to settings.yaml)
    rvol_rising_threshold: float = 2.0    # T-07: trajectory > 2x = volume confirming
    rvol_default_when_missing: float = 0.3 # Conservative: no data = assume low volume

    # -- ATR (average true range) -- minimum for 2% reachability
    atr_pct_floor: float = 0.8            # Lowered for LSE leveraged ETPs (Ben-David 2018)

    # -- Indicator consensus -- Brock et al. (1992)
    consensus_standard: int = 6            # 6/8 for standard tickers
    consensus_leveraged_etp: int = 4       # 4/8 for 3x/5x LSE ETPs in TRENDING
    consensus_ai_momentum: int = 5         # 5/8 for AI/concentration stocks
    consensus_eased_min_rvol: float = 1.0  # Compensating gate when consensus eased
    consensus_eased_min_conf: float = 72.0 # Harvey & Liu (2015): stricter on eased setups

    # -- Weighted consensus gate thresholds --
    weighted_gate_standard: float = 7.0    # ~6/8 equivalent (quality-adjusted)
    weighted_gate_leveraged: float = 4.8   # ~4/8 for broad leveraged ETPs in trending
    weighted_gate_ai_momentum: float = 6.0 # ~5/8 for AI/momentum concentrated ETPs
    weighted_gate_range_bound: float = 8.0 # Strict in RANGE_BOUND (Lo et al. 2000)

    # -- R:R (reward-to-risk ratio) -- Lo (2002)
    min_rr_ratio: float = 1.5             # Minimum acceptable R:R

    # -- Daily target --
    daily_target_pct: float = 2.0          # 2% daily target -- THE NUMBER
    max_intraday_target_pct: float = 6.0   # Runner cap for core tickers in strong trends
    max_signals_per_day: int = 3           # T-08: allow up to 3 signals/day

    # -- RANGE_BOUND overrides --
    range_bound_atr_min: float = 1.2       # LSE leveraged ETPs lower ATR
    range_bound_rvol_min: float = 1.5      # Gao et al. (2018): breakouts only persist RVOL >= 1.5
    range_bound_consensus_min: int = 6     # Always 6/8 in chop
    range_bound_conf_penalty: float = 10.0 # Score penalty for range-bound regime

    # -- Gap thresholds (T-01) --
    gap_threshold_3x: float = 0.025        # 2.5% ETP gap = ~0.83% underlying
    gap_threshold_5x: float = 0.040        # 4.0% ETP gap = ~0.80% underlying
    gap_max_spread_bps: int = 35           # RO-01 spread gate on gap signals

    # ===================================================================
    # 2. RISK MANAGEMENT (CONSTITUTIONAL -- Section 43)
    # ===================================================================

    risk_per_trade: float = 0.0075         # 0.75% of equity -- IMMUTABLE
    max_weekly_loss: float = 0.06          # 6%
    max_concurrent_positions: int = 3      # Default (BULL-BOT: 5, BEAR-BOT: 2)
    max_same_sub_industry: int = 2         # No 3 GPU stocks at once
    max_trades_per_day: int = 4            # Per bot (hard cap)
    max_trades_per_week: int = 10          # Per bot
    min_confidence_constitutional: int = 65 # SK-03: unified floor
    consecutive_losses_3_cooldown_min: int = 15  # Minutes
    consecutive_losses_3_min_conf: int = 75      # Minimum conf after 3 losses
    regime_flip_exit: bool = True          # Wrong regime = EXIT immediately

    # -- Portfolio heat --
    portfolio_heat_cap: float = 0.035      # 3.5% of equity (F-12: raised from 3.0%)
    portfolio_heat_budget_pct: float = 6.0 # Total risk budget (unified_risk_gate.py)
    max_factor_group_positions: int = 2    # Factor group concentration limit
    max_per_ticker: int = 1                # Only 1 position per ticker across all pathways

    # ===================================================================
    # 3. ETP ALLOCATION LIMITS
    # ===================================================================

    etp_3x_max_allocation: float = 0.30    # 30% of equity
    etp_3x_max_single: float = 0.15       # 15%
    etp_3x_max_hold_days: int = 5         # Decay limit
    etp_5x_max_allocation: float = 0.15    # 15%
    etp_5x_max_single: float = 0.05       # 5%
    etp_5x_max_hold_days: int = 3         # Volatility decay

    # ===================================================================
    # 4. CIRCUIT BREAKER LEVELS (circuit_breakers.py -- sole authority)
    # ===================================================================

    # -- Drawdown (daily intraday loss as fraction of equity) --
    drawdown_l1_yellow: float = 0.015      # 1.5% -- reduce sizes 50%
    drawdown_l2_orange: float = 0.025      # 2.5% -- stop new entries
    drawdown_l3_red: float = 0.04          # 4.0% -- close everything, halt

    # -- Drawdown recovery protocol (Section 60) --
    drawdown_recovery_yellow: float = 0.03 # 3% cumulative peak-to-trough
    drawdown_recovery_orange: float = 0.05 # 5%
    drawdown_recovery_red: float = 0.08    # 8%
    drawdown_recovery_critical: float = 0.10 # 10%
    drawdown_recovery_emergency: float = 0.12 # 12%

    # -- VIX --
    vix_spike_pct: float = 0.25           # 25% intraday jump
    vix_spike_pause_sec: int = 1800       # 30 minutes
    vix_high_abs: int = 35                # Reduce all sizes 50%
    vix_extreme_abs: int = 45             # Emergency close leveraged ETPs
    vix_half_size_threshold: float = 22.0 # S15: half-size above VIX 22
    vix_no_5x_threshold: float = 22.0     # S15: no 5x leverage above VIX 22
    vix_intraday_spike_veto: float = 10.0 # T-04: VIX +10 from session open = LONG veto

    # -- Correlation --
    correlation_threshold: float = 0.80    # Avg cross-position correlation
    direction_concentration: float = 0.80  # > 80% same direction = alert
    max_correlated_positions: int = 2      # From settings.yaml
    sector_concentration_max: float = 0.60 # Max sector weight
    beta_exposure_max: float = 2.5         # Max portfolio beta

    # -- Consecutive losses (circuit_breakers.py is sole authority -- A-13) --
    consec_loss_tier_1: int = 3            # 15 min cooldown
    consec_loss_tier_2: int = 5            # 30 min cooldown + 50% size
    consec_loss_tier_3: int = 5            # Halt for rest of session
    cooldown_tier_1_sec: int = 900         # 15 minutes
    cooldown_tier_2_sec: int = 1800        # 30 minutes

    # ===================================================================
    # 5. SPREAD & COST GATES
    # ===================================================================

    spread_watch_threshold_bps: float = 22.0   # WATCH tier
    spread_veto_threshold_bps: float = 32.0    # VETO tier
    spread_veto_multiplier: float = 2.5        # x 3-day median
    slippage_bps_per_side: float = 5.0         # Market impact per side
    platform_fee_bps: float = 2.0              # Brokerage/platform fee per side
    default_spread_bps: float = 25.0           # Fallback if no ticker-specific data

    # ===================================================================
    # 6. TIMING
    # ===================================================================

    us_open_stabilization_sec: int = 300       # 5 minutes after US open
    data_freshness_max_sec: int = 120          # Max acceptable data age
    lse_open_hour: int = 9                     # LSE trading window start
    lse_open_min: int = 0
    lse_close_hour: int = 15                   # LSE trading window end
    lse_close_min: int = 15

    # ===================================================================
    # 7. BLACK SWAN DETECTION
    # ===================================================================

    spy_15m_extreme_pct: float = 0.02          # 2% in 15 minutes
    flash_crash_volume_mult: int = 5           # 5x normal volume
    flash_crash_price_drop: float = 0.03       # 3%+ price drop

    # ===================================================================
    # 8. STOP-LOSS PARAMETERS
    # ===================================================================

    stop_atr_mult: float = 1.5                 # Wilder (1978): 1.5x ATR outside noise
    stop_min_pct: float = 0.005                # Floor: 0.5% minimum stop distance
    stop_pct_3x_fallback: float = 1.0          # Legacy fallback if ATR unavailable
    stop_pct_5x_fallback: float = 0.75         # Legacy fallback if ATR unavailable

    # ===================================================================
    # 9. RUNNER MODE
    # ===================================================================

    runner_rvol_threshold: float = 2.0         # RVOL threshold to activate runner mode
    runner_atr_pct_min: float = 2.0            # Min ATR% for runner mode

    # ===================================================================
    # VALIDATION
    # ===================================================================

    def __post_init__(self) -> None:
        """Validate all thresholds at construction time.

        Raises ThresholdValidationError and calls sys.exit(1) if any
        threshold is outside its valid range. This is a hard fail --
        the system MUST NOT start with invalid thresholds.
        """
        errors: list[str] = []

        # -- Signal quality gates --
        _validate_range("confidence_floor", self.confidence_floor, 1, 100, errors)
        _validate_range("confidence_bear_bot_floor", self.confidence_bear_bot_floor, 1, 100, errors)
        _validate_range("adx_floor_fast", self.adx_floor_fast, 0, 100, errors)
        _validate_range("adx_floor_slow", self.adx_floor_slow, 0, 100, errors)
        _validate_range("rvol_floor_fast", self.rvol_floor_fast, 0, 50, errors)
        _validate_range("rvol_floor_slow", self.rvol_floor_slow, 0, 50, errors)
        _validate_positive("atr_pct_floor", self.atr_pct_floor, errors)
        _validate_positive("min_rr_ratio", self.min_rr_ratio, errors)
        _validate_positive("daily_target_pct", self.daily_target_pct, errors)
        _validate_range("max_signals_per_day", self.max_signals_per_day, 1, 20, errors)

        # -- Consensus gates --
        _validate_range("consensus_standard", self.consensus_standard, 1, 8, errors)
        _validate_range("consensus_leveraged_etp", self.consensus_leveraged_etp, 1, 8, errors)
        _validate_range("consensus_ai_momentum", self.consensus_ai_momentum, 1, 8, errors)

        # -- Risk management --
        _validate_range("risk_per_trade", self.risk_per_trade, 0.0001, 0.05, errors)
        _validate_range("max_weekly_loss", self.max_weekly_loss, 0.01, 0.20, errors)
        _validate_range("max_concurrent_positions", self.max_concurrent_positions, 1, 20, errors)
        _validate_range("max_trades_per_day", self.max_trades_per_day, 1, 50, errors)
        _validate_range("portfolio_heat_cap", self.portfolio_heat_cap, 0.005, 0.20, errors)

        # -- ETP limits --
        _validate_range("etp_3x_max_allocation", self.etp_3x_max_allocation, 0.01, 1.0, errors)
        _validate_range("etp_3x_max_single", self.etp_3x_max_single, 0.01, 0.50, errors)
        _validate_range("etp_5x_max_allocation", self.etp_5x_max_allocation, 0.01, 1.0, errors)
        _validate_range("etp_5x_max_single", self.etp_5x_max_single, 0.01, 0.50, errors)
        _validate_range("etp_3x_max_hold_days", self.etp_3x_max_hold_days, 1, 30, errors)
        _validate_range("etp_5x_max_hold_days", self.etp_5x_max_hold_days, 1, 30, errors)

        # -- Circuit breakers --
        _validate_positive("drawdown_l1_yellow", self.drawdown_l1_yellow, errors)
        _validate_positive("drawdown_l2_orange", self.drawdown_l2_orange, errors)
        _validate_positive("drawdown_l3_red", self.drawdown_l3_red, errors)
        # Ensure L1 < L2 < L3 (escalating severity)
        if self.drawdown_l1_yellow >= self.drawdown_l2_orange:
            errors.append(
                f"drawdown_l1_yellow ({self.drawdown_l1_yellow}) must be < "
                f"drawdown_l2_orange ({self.drawdown_l2_orange})"
            )
        if self.drawdown_l2_orange >= self.drawdown_l3_red:
            errors.append(
                f"drawdown_l2_orange ({self.drawdown_l2_orange}) must be < "
                f"drawdown_l3_red ({self.drawdown_l3_red})"
            )

        # Ensure recovery levels escalate
        recovery_levels = [
            ("drawdown_recovery_yellow", self.drawdown_recovery_yellow),
            ("drawdown_recovery_orange", self.drawdown_recovery_orange),
            ("drawdown_recovery_red", self.drawdown_recovery_red),
            ("drawdown_recovery_critical", self.drawdown_recovery_critical),
            ("drawdown_recovery_emergency", self.drawdown_recovery_emergency),
        ]
        for i in range(len(recovery_levels) - 1):
            name_a, val_a = recovery_levels[i]
            name_b, val_b = recovery_levels[i + 1]
            if val_a >= val_b:
                errors.append(f"{name_a} ({val_a}) must be < {name_b} ({val_b})")

        # -- VIX thresholds --
        _validate_positive("vix_high_abs", self.vix_high_abs, errors)
        _validate_positive("vix_extreme_abs", self.vix_extreme_abs, errors)
        if self.vix_high_abs >= self.vix_extreme_abs:
            errors.append(
                f"vix_high_abs ({self.vix_high_abs}) must be < "
                f"vix_extreme_abs ({self.vix_extreme_abs})"
            )

        # -- Consecutive losses --
        _validate_range("consec_loss_tier_1", self.consec_loss_tier_1, 1, 20, errors)
        _validate_range("consec_loss_tier_3", self.consec_loss_tier_3, 1, 20, errors)

        # -- Spread gates --
        _validate_positive("spread_watch_threshold_bps", self.spread_watch_threshold_bps, errors)
        _validate_positive("spread_veto_threshold_bps", self.spread_veto_threshold_bps, errors)
        if self.spread_watch_threshold_bps >= self.spread_veto_threshold_bps:
            errors.append(
                f"spread_watch_threshold_bps ({self.spread_watch_threshold_bps}) "
                f"must be < spread_veto_threshold_bps ({self.spread_veto_threshold_bps})"
            )

        # -- Timing --
        _validate_non_negative("us_open_stabilization_sec", self.us_open_stabilization_sec, errors)
        _validate_positive("data_freshness_max_sec", self.data_freshness_max_sec, errors)

        # -- Black swan --
        _validate_positive("spy_15m_extreme_pct", self.spy_15m_extreme_pct, errors)
        _validate_positive("flash_crash_price_drop", self.flash_crash_price_drop, errors)

        # -- Stop-loss --
        _validate_positive("stop_atr_mult", self.stop_atr_mult, errors)
        _validate_positive("stop_min_pct", self.stop_min_pct, errors)

        # ---- HARD FAIL ----
        if errors:
            msg = (
                f"ThresholdRegistry VALIDATION FAILED ({len(errors)} errors):\n"
                + "\n".join(f"  - {e}" for e in errors)
            )
            logger.critical(msg)
            print(msg, file=sys.stderr)
            sys.exit(1)

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ThresholdRegistry":
        """Load threshold overrides from settings.yaml.

        Reads the ``thresholds:`` top-level key from the YAML file.
        Any key present overrides the dataclass default. Keys not
        present keep their defaults. Unknown keys are logged as
        warnings and ignored.

        Returns a new frozen ``ThresholdRegistry`` instance.

        Raises:
            FileNotFoundError: if the YAML file does not exist.
            ThresholdValidationError: (via __post_init__) if values
                are invalid (and sys.exit(1)).
        """
        import yaml

        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"ThresholdRegistry: YAML not found: {path}")

        with open(path, "r") as f:
            raw = yaml.safe_load(f) or {}

        overrides = raw.get("thresholds", {})
        if not isinstance(overrides, dict):
            logger.warning(
                "ThresholdRegistry: 'thresholds' key in %s is not a dict, "
                "using all defaults",
                path,
            )
            overrides = {}

        # Filter to only known fields
        valid_fields = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for key, value in overrides.items():
            if key in valid_fields:
                kwargs[key] = value
            else:
                logger.warning(
                    "ThresholdRegistry: unknown key '%s' in %s — ignored",
                    key, path,
                )

        instance = cls(**kwargs)
        logger.info(
            "ThresholdRegistry loaded from %s | %d overrides applied | "
            "risk_per_trade=%.4f confidence_floor=%d",
            path, len(kwargs), instance.risk_per_trade, instance.confidence_floor,
        )
        return instance

    @classmethod
    def from_config_module(cls) -> "ThresholdRegistry":
        """Load threshold overrides from the config module (settings.yaml via config.get()).

        This reads relevant values from the already-loaded config module
        and maps them to ThresholdRegistry fields. This is the preferred
        boot-time factory when the config module is already initialised.

        Falls back to defaults if config is unavailable.
        """
        try:
            import config as cfg
        except ImportError:
            logger.warning(
                "ThresholdRegistry: config module not available, using defaults"
            )
            return cls()

        overrides: dict[str, Any] = {}

        # Map settings.yaml paths to ThresholdRegistry fields
        _mapping: list[tuple[str, str]] = [
            # Signal quality
            ("confidence.floor", "confidence_floor"),
            ("confidence.orb_vwap_floor", "confidence_orb_vwap_floor"),
            ("confidence.bear_bot_floor", "confidence_bear_bot_floor"),
            ("confidence.max_score", "confidence_max"),

            # Risk management (immutable rules)
            ("immutable_rules.risk_per_trade", "risk_per_trade"),
            ("immutable_rules.max_weekly_loss", "max_weekly_loss"),
            ("immutable_rules.max_concurrent_positions", "max_concurrent_positions"),
            ("immutable_rules.max_same_sub_industry", "max_same_sub_industry"),
            ("immutable_rules.max_trades_per_day", "max_trades_per_day"),
            ("immutable_rules.max_trades_per_week", "max_trades_per_week"),
            ("immutable_rules.min_confidence", "min_confidence_constitutional"),
            ("immutable_rules.consecutive_losses_3_cooldown", "consecutive_losses_3_cooldown_min"),
            ("immutable_rules.regime_flip_exit", "regime_flip_exit"),

            # ETP limits
            ("immutable_rules.etp_3x_max_allocation", "etp_3x_max_allocation"),
            ("immutable_rules.etp_3x_max_single", "etp_3x_max_single"),
            ("immutable_rules.etp_5x_max_allocation", "etp_5x_max_allocation"),
            ("immutable_rules.etp_5x_max_single", "etp_5x_max_single"),
            ("immutable_rules.etp_5x_max_hold_days", "etp_5x_max_hold_days"),
            ("immutable_rules.etp_3x_max_hold_days", "etp_3x_max_hold_days"),

            # Correlation
            ("correlation.portfolio_heat_max", "portfolio_heat_cap"),
            ("correlation.max_correlated_positions", "max_correlated_positions"),
            ("correlation.correlation_threshold", "correlation_threshold"),
            ("correlation.sector_concentration_max", "sector_concentration_max"),
            ("correlation.beta_exposure_max", "beta_exposure_max"),
            ("correlation.direction_concentration_warn", "direction_concentration"),

            # Qualification
            ("qualification.stage2_no_trade.vix_threshold", "vix_high_abs"),
            ("qualification.stage2_no_trade.adx_threshold", "adx_floor_fast"),
            ("qualification.stage6_risk_sizer.risk_per_trade", "risk_per_trade"),
        ]

        for yaml_key, field_name in _mapping:
            val = cfg.get(yaml_key)
            if val is not None:
                overrides[field_name] = val

        instance = cls(**overrides)
        logger.info(
            "ThresholdRegistry loaded from config module | %d overrides | "
            "risk_per_trade=%.4f confidence_floor=%d",
            len(overrides), instance.risk_per_trade, instance.confidence_floor,
        )
        return instance

    # ------------------------------------------------------------------
    # Convenience: summary for logging / dashboards
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return a dict of all thresholds for logging/dashboards."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def diff(self, other: "ThresholdRegistry") -> dict[str, tuple[Any, Any]]:
        """Return fields that differ between two registries.

        Useful for auditing YAML overrides vs defaults.

        Returns:
            dict mapping field_name -> (self_value, other_value)
        """
        diffs: dict[str, tuple[Any, Any]] = {}
        for f in fields(self):
            v_self = getattr(self, f.name)
            v_other = getattr(other, f.name)
            if v_self != v_other:
                diffs[f.name] = (v_self, v_other)
        return diffs


# ---------------------------------------------------------------------------
# Module-level singleton -- populated at boot
# ---------------------------------------------------------------------------

_REGISTRY: Optional[ThresholdRegistry] = None


def load_thresholds(
    yaml_path: str | Path | None = None,
    use_config_module: bool = True,
) -> ThresholdRegistry:
    """Initialise and return the module-level ThresholdRegistry singleton.

    Call this once at boot time. Subsequent calls return the cached instance.

    Args:
        yaml_path: Path to settings.yaml. If provided, loads overrides
                   from the ``thresholds:`` section. Takes precedence over
                   ``use_config_module``.
        use_config_module: If True (default) and ``yaml_path`` is None,
                          loads overrides from the config module.

    Returns:
        The frozen ThresholdRegistry singleton.
    """
    global _REGISTRY
    if _REGISTRY is not None:
        return _REGISTRY

    if yaml_path is not None:
        _REGISTRY = ThresholdRegistry.from_yaml(yaml_path)
    elif use_config_module:
        _REGISTRY = ThresholdRegistry.from_config_module()
    else:
        _REGISTRY = ThresholdRegistry()
        logger.info("ThresholdRegistry: using all defaults (no YAML, no config module)")

    # Log diff vs pure defaults for auditability
    defaults = ThresholdRegistry.__new__(ThresholdRegistry)
    # Manually init defaults without validation to compare
    try:
        default_instance = ThresholdRegistry()
        diffs = _REGISTRY.diff(default_instance)
        if diffs:
            logger.info(
                "ThresholdRegistry: %d fields differ from defaults: %s",
                len(diffs),
                ", ".join(f"{k}: {v[1]}->{v[0]}" for k, v in sorted(diffs.items())),
            )
    except SystemExit:
        pass  # defaults should always validate; if not, something is very wrong

    return _REGISTRY


def get_thresholds() -> ThresholdRegistry:
    """Return the current ThresholdRegistry singleton.

    If not yet initialised, creates one from the config module.
    Callers should prefer ``load_thresholds()`` at boot and
    ``get_thresholds()`` for runtime access.
    """
    if _REGISTRY is None:
        return load_thresholds()
    return _REGISTRY


# Convenience alias for ``from core.threshold_registry import THRESHOLDS``
# This is lazily evaluated on first access via module-level __getattr__.


def __getattr__(name: str) -> Any:
    if name == "THRESHOLDS":
        return get_thresholds()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
