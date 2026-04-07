"""Regime-Scaled Risk Limits — Book 85.

Dynamic loss limits, position sizing, and cooldown periods based on market regime.
Four regimes: STEADY, INFLATION, WOI (Window of Instability), CRISIS.

Book 85 Specifications:
- Daily loss limits:  STEADY -3%, INFLATION -2.5%, WOI -2%, CRISIS -1.5%
- Weekly loss limits: STEADY -7%, INFLATION -5.5%, WOI -4%, CRISIS -2%
- Risk per trade:     STEADY 0.75%, INFLATION 0.60%, WOI 0.40%, CRISIS 0.20%
- Cooldown between:   STEADY 5min, INFLATION 10min, WOI 15min, CRISIS 30min

Regime detection: Integrated with nightly pipeline output (regime_detector.json).
Falls back to VIX/Hurst estimation if nightly pipeline unavailable.

Usage:
    from python_brain.risk.regime_risk_limits import get_regime_limits, RegimeLimits

    limits = get_regime_limits(vix=28, hurst=0.3)
    # limits.daily_loss_limit_pct, limits.risk_per_trade_pct, limits.cooldown_secs
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

log = logging.getLogger("regime_risk_limits")


@dataclass(frozen=True)
class RegimeLimits:
    """Regime-scaled risk limits per Book 85."""
    regime: str = "STEADY"                        # STEADY, INFLATION, WOI, CRISIS
    daily_loss_limit_pct: float = -3.0            # Drawdown circuit breaker per day
    weekly_loss_limit_pct: float = -7.0           # Drawdown circuit breaker per week
    risk_per_trade_pct: float = 0.75              # Max position size (% of equity)
    cooldown_secs: int = 300                      # Seconds between trades (5 min = 300)
    kelly_cap: float = 0.05                       # Legacy: Kelly fraction cap
    confidence_floor: int = 50                    # Legacy: Min signal confidence
    max_positions: int = 3                        # Legacy: Max open positions
    portfolio_heat_pct: float = 10.0              # Legacy: Max portfolio heat


def _load_max_positions_from_config() -> int:
    """Load max_positions from config.toml, default 5."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore
        except ImportError:
            return 5
    try:
        _cfg_path = Path(__file__).resolve().parents[2] / "config" / "config.toml"
        if not _cfg_path.exists():
            _cfg_path = Path("/app/config/config.toml")
        if _cfg_path.exists():
            with open(_cfg_path, "rb") as f:
                cfg = tomllib.load(f)
            return cfg.get("crucible", {}).get("max_positions_override",
                   cfg.get("risk", {}).get("max_positions", 5))
    except Exception:
        pass
    return 5


_BASE_MAX_POSITIONS = _load_max_positions_from_config()


# Book 85: Regime → Loss Limits + Risk Per Trade + Cooldown
import os as _os_rl

# Paper trading mode: reduce cooldowns to allow signal flow for data collection.
# Production cooldowns are preserved when AEGIS_SIM_MODE is not set.
_PAPER_MODE = _os_rl.environ.get("AEGIS_SIM_MODE", "0") == "1"
_CD_MULT = 0.1 if _PAPER_MODE else 1.0  # 10x shorter cooldowns in paper mode

REGIME_LIMITS: Dict[str, RegimeLimits] = {
    "STEADY": RegimeLimits(
        regime="STEADY",
        daily_loss_limit_pct=-3.0,
        weekly_loss_limit_pct=-7.0,
        risk_per_trade_pct=0.75,
        cooldown_secs=int(300 * _CD_MULT),     # 5 min prod, 30s paper
        kelly_cap=0.05,
        confidence_floor=50,
        max_positions=_BASE_MAX_POSITIONS,
        portfolio_heat_pct=10.0,
    ),
    "INFLATION": RegimeLimits(
        regime="INFLATION",
        daily_loss_limit_pct=-2.5,
        weekly_loss_limit_pct=-5.5,
        risk_per_trade_pct=0.60,
        cooldown_secs=int(600 * _CD_MULT),     # 10 min prod, 60s paper
        kelly_cap=0.035,
        confidence_floor=55 if not _PAPER_MODE else 45,
        max_positions=max(1, _BASE_MAX_POSITIONS * 2 // 3),
        portfolio_heat_pct=8.0,
    ),
    "WOI": RegimeLimits(
        regime="WOI",
        daily_loss_limit_pct=-2.0,
        weekly_loss_limit_pct=-4.0,
        risk_per_trade_pct=0.40,
        cooldown_secs=int(900 * _CD_MULT),     # 15 min prod, 90s paper
        kelly_cap=0.035,
        confidence_floor=60 if not _PAPER_MODE else 50,
        max_positions=max(1, _BASE_MAX_POSITIONS // 2),
        portfolio_heat_pct=7.0,
    ),
    "CRISIS": RegimeLimits(
        regime="CRISIS",
        daily_loss_limit_pct=-1.5,
        weekly_loss_limit_pct=-2.0,
        risk_per_trade_pct=0.20,
        cooldown_secs=int(1800 * _CD_MULT),    # 30 min prod, 180s paper
        kelly_cap=0.02,
        confidence_floor=75 if not _PAPER_MODE else 60,
        max_positions=1,
        portfolio_heat_pct=4.0,
    ),
}

# Cache nightly regime (avoid repeated file I/O)
_cached_regime: Optional[tuple] = None
_cached_regime_timestamp: float = 0.0
_regime_cache_ttl_secs: float = 60.0


def _load_nightly_regime() -> Optional[str]:
    """Try to read regime from nightly pipeline output (Book 85).

    Cache for 60 seconds to avoid repeated file I/O in tight signal loops.

    Returns regime string or None if unavailable.
    """
    global _cached_regime, _cached_regime_timestamp

    now = time.time()
    if _cached_regime is not None and (now - _cached_regime_timestamp) < _regime_cache_ttl_secs:
        return _cached_regime[0]

    try:
        regime_path = "/app/data/regime_detector.json"
        if os.path.exists(regime_path):
            with open(regime_path, "r") as f:
                data = json.load(f)
                regime = data.get("regime", "").upper()
                if regime in REGIME_LIMITS:
                    _cached_regime = (regime, now)
                    _cached_regime_timestamp = now
                    return regime
    except Exception as e:
        log.debug(f"Failed to load regime from pipeline: {e}")

    return None


def classify_regime(vix: float = 21.0, hurst: float = 0.5) -> str:
    """Detect regime from market indicators (VIX + Hurst).

    Book 85: Maps market conditions to 4-state regime (STEADY, INFLATION, WOI, CRISIS).

    Fallback when nightly pipeline unavailable. Uses VIX as primary signal,
    Hurst exponent as confirmation.

    Thresholds (Phase 1 alignment — widened STEADY band):
    - CRISIS: VIX >= 35
    - WOI: VIX >= 28 or (VIX >= 22 and extreme Hurst)
    - INFLATION: VIX >= 22 or (VIX < 22 and Hurst < 0.3)
    - STEADY: VIX < 22 and Hurst near 0.5 (random walk)
    """
    # Primary: VIX level
    if vix >= 35:
        return "CRISIS"
    elif vix >= 28:
        return "WOI"
    elif vix >= 22:
        # Elevated zone: Hurst helps distinguish WOI vs INFLATION
        if hurst < 0.2 or hurst > 0.7:  # Extreme Hurst = structural stress → WOI
            return "WOI"
        else:
            return "INFLATION"
    else:
        # VIX < 22: Hurst helps distinguish INFLATION vs STEADY
        if hurst < 0.3:  # Mean-reverting = mild uncertainty (INFLATION)
            return "INFLATION"
        else:
            return "STEADY"


def get_regime_limits(
    vix: float = 21.0,
    hurst: float = 0.5,
    override_regime: Optional[str] = None,
) -> RegimeLimits:
    """Get risk limits for current market conditions (Book 85).

    Priority:
    1. override_regime (if provided)
    2. Nightly pipeline regime_detector.json
    3. VIX/Hurst classification fallback

    Args:
        vix: Current VIX level (20 is neutral)
        hurst: Hurst exponent for this instrument (0.5 = random walk)
        override_regime: Force a specific regime (testing/admin override)

    Returns:
        RegimeLimits dataclass with all 4-regime parameters
    """
    regime = override_regime

    if not regime:
        # Try nightly pipeline first
        regime = _load_nightly_regime()

    if not regime:
        # Fallback to classification
        regime = classify_regime(vix, hurst)

    # Ensure valid regime (defensive)
    if regime not in REGIME_LIMITS:
        log.warning(f"Unknown regime '{regime}', falling back to STEADY")
        regime = "STEADY"

    limits = REGIME_LIMITS[regime]
    log.debug(f"Regime: {regime} (vix={vix:.1f}, hurst={hurst:.2f}) "
              f"→ daily_limit={limits.daily_loss_limit_pct}%, "
              f"risk_per_trade={limits.risk_per_trade_pct}%, "
              f"cooldown={limits.cooldown_secs}s")

    return limits


def interpolate_limits(
    current: RegimeLimits,
    target: RegimeLimits,
    progress: float,  # 0.0 = current, 1.0 = target
) -> RegimeLimits:
    """Smoothly transition between two risk limit sets (not used in Book 85).

    For regime transitions that span multiple nightly cycles.
    Typical transition: 2-5 days (progress increments 0.2-0.5 per day).

    Args:
        current: Starting regime limits
        target: Target regime limits
        progress: Interpolation factor [0.0, 1.0]

    Returns:
        Interpolated limits set
    """
    p = max(0.0, min(1.0, progress))
    q = 1.0 - p

    return RegimeLimits(
        regime=target.regime,  # Jump to target regime name
        daily_loss_limit_pct=current.daily_loss_limit_pct * q + target.daily_loss_limit_pct * p,
        weekly_loss_limit_pct=current.weekly_loss_limit_pct * q + target.weekly_loss_limit_pct * p,
        risk_per_trade_pct=current.risk_per_trade_pct * q + target.risk_per_trade_pct * p,
        cooldown_secs=int(current.cooldown_secs * q + target.cooldown_secs * p),
        kelly_cap=current.kelly_cap * q + target.kelly_cap * p,
        confidence_floor=int(current.confidence_floor * q + target.confidence_floor * p),
        max_positions=int(current.max_positions * q + target.max_positions * p),
        portfolio_heat_pct=current.portfolio_heat_pct * q + target.portfolio_heat_pct * p,
    )
