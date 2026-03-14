"""Shared constants for all Python brain modules (H109: no magic numbers).

All thresholds reference the canonical rules in docs/00_CANONICAL_RULES.md.
Code MUST use these constants, never literal values.
"""

# Signal filtering
CONFIDENCE_FLOOR = 65  # Minimum confidence to emit OrderIntent
OUTLIER_WIN_CAP_PCT = 3.0  # Cap single-trade return for Kelly avg

# Momentum (Vanguard Sniper)
ADX_PERIOD = 14  # ADX lookback period
EMA_FAST_PERIOD = 20  # Fast EMA for trend confirmation
VOLUME_BREAKOUT_MULT = 2.0  # Volume must exceed 2x rolling mean
MOMENTUM_LOOKBACK = 20  # Rolling window for momentum scoring

# RVOL (Apex Scout)
RVOL_THRESHOLD = 2.0  # Relative volume threshold for anomaly
RVOL_LOOKBACK = 20  # Rolling window for average volume
SNAPSHOT_INTERVAL_SECS = 60  # Apex snapshot interval

# Volatility (Moreira-Muir)
VOL_TARGET_ANNUAL_PCT = 15.0  # Annual portfolio volatility target
VOL_ROLLING_WINDOW = 10  # Rolling window for realized vol
TRADING_DAYS_PER_YEAR = 252  # LSE trading days

# Kelly sizing
KELLY_FRACTION_CAP = 0.5  # Half-Kelly cap
KELLY_CLAMP_MAX = 0.20  # Maximum Kelly output (H57)

# Risk
SPREAD_VETO_PCT = 0.5  # Spread > 0.5% → veto

# Logging callback type hint
LOG_LEVEL_DEBUG = "DEBUG"
LOG_LEVEL_WARNING = "WARNING"
