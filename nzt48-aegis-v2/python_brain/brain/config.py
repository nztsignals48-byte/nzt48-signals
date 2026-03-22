"""Shared constants for all Python brain modules (H109: no magic numbers).

All thresholds reference the canonical rules in config.toml.
Code MUST use these constants, never literal values.
Updated 2026-03-22: Aligned with Sprint 5 fixes and config.toml canonical values.
"""

# Signal filtering — MUST match config.toml [signal] confidence_floor
CONFIDENCE_FLOOR = 65  # Minimum confidence to emit signal (Sprint 5: was 45, now matches config.toml)
OUTLIER_WIN_CAP_PCT = 3.0  # Cap single-trade return for Kelly avg

# Momentum (Vanguard Sniper)
ADX_PERIOD = 14  # ADX lookback period
EMA_FAST_PERIOD = 20  # Fast EMA for trend confirmation
VOLUME_BREAKOUT_MULT = 1.5  # Volume must exceed 1.5x rolling mean
MOMENTUM_LOOKBACK = 15  # Rolling window for momentum scoring

# RVOL — MUST match bridge.py Sprint 5 T-05 fix
RVOL_THRESHOLD = 1.0  # RVOL threshold for strong signal (was 2.0, lowered Sprint 5)
RVOL_THRESHOLD_LOW = 0.7  # RVOL threshold for any signal (Sprint 5 T-05)
RVOL_LOOKBACK = 20  # Rolling window for average volume
SNAPSHOT_INTERVAL_SECS = 60  # Apex snapshot interval

# ADX — MUST match bridge.py Sprint 5 T-04 fix
ADX_MIN_TREND = 12  # Minimum ADX for any entry (was 15, lowered Sprint 5 T-04)
ADX_STRONG_TREND = 20  # Strong trend (was 25)
ADX_VERY_STRONG = 30  # Very strong trend (was 35)

# Volatility (Moreira-Muir)
VOL_TARGET_ANNUAL_PCT = 15.0  # Annual portfolio volatility target
VOL_ROLLING_WINDOW = 10  # Rolling window for realized vol
TRADING_DAYS_PER_YEAR = 252  # LSE trading days

# Kelly sizing — MUST match config.toml [kelly]
KELLY_FRACTION_CAP = 0.5  # Half-Kelly cap
KELLY_CLAMP_MAX = 0.20  # Maximum Kelly output (H57)

# Risk — MUST match config.toml [risk] spread_veto_pct
SPREAD_VETO_PCT = 0.3  # Spread > 0.3% → veto (was 0.5, tightened per quant audit)

# Logging callback type hint
LOG_LEVEL_DEBUG = "DEBUG"
LOG_LEVEL_WARNING = "WARNING"
