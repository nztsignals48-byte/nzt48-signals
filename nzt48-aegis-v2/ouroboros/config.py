"""Ouroboros constants — no magic numbers (H109).

All thresholds reference docs/00_CANONICAL_RULES.md.
"""

# Identity
CLIENT_ID = 200  # Ouroboros client ID (H41), Executioner = 100

# Timing — Ouroboros runs ONLY outside LSE hours
LSE_OPEN_SECS = 8 * 3600       # 08:00 London
LSE_CLOSE_SECS = 16 * 3600 + 30 * 60  # 16:30 London

# Nightly timeline (ET → converted to London at entrypoint)
GATEWAY_RESTART_ET = "23:45"
UNIVERSE_RECLASS_ET = "23:46"
ANALYTICS_RUN_ET = "23:50"
STATE_SNAPSHOT_ET = "00:00"
GATEWAY_ONLINE_ET = "00:15"
CLOCK_RESYNC_ET = "00:16"

# Bayesian win rate
LAPLACE_PRIOR_WINS = 1    # Laplace smoothing: prior wins
LAPLACE_PRIOR_TOTAL = 2   # Laplace smoothing: prior total
MIN_TRADES_FOR_DSR = 10   # Minimum trades before DSR is meaningful
BENCHMARK_SHARPE = 0.0    # SR₀ for DSR (null hypothesis: no skill)

# Kelly Accelerator
KELLY_FLOOR = 0.02        # Minimum Kelly fraction (never zero)
KELLY_CEILING = 0.20      # Maximum Kelly fraction (H57)
KELLY_HALF_CAP = 0.5      # Half-Kelly cap
KELLY_LEARNING_RATE = 0.3 # Blend new evidence with prior (EWA alpha)

# Exit Calibration
CHANDELIER_ATR_MULT_MIN = 1.5   # Tightest allowed multiplier
CHANDELIER_ATR_MULT_MAX = 4.0   # Loosest allowed multiplier
CHANDELIER_ATR_MULT_DEFAULT = 3.0  # Starting multiplier
MFE_RUNG5_THRESHOLD = 0.6       # >60% trades hit Rung 5 → loosen

# Alpha Decay (IC tracking)
IC_LOOKBACK_DAYS = 20     # Rolling window for IC
IC_WARNING_THRESHOLD = 0.02   # IC < 0.02 → warning
IC_LOCK_THRESHOLD = 0.0       # IC ≤ 0 → lock ticker from Vanguard

# Universe Reclassification (ASER)
ASER_PROMOTE_THRESHOLD = 0.8   # ASER > 0.8 → promote to Tier 1
ASER_DEMOTE_THRESHOLD = 0.3    # ASER < 0.3 → demote to Tier 3
SPREAD_WIDEN_THRESHOLD = 0.5   # Spread > 0.5% → demote (H60)
COLD_START_DAYS = 3            # Days before Ouroboros has enough data

# Regime labels
REGIME_LABELS = ["bull_quiet", "bull_volatile", "bear_quiet", "bear_volatile"]

# Output file names
DYNAMIC_WEIGHTS_FILE = "dynamic_weights.toml"
UNIVERSE_CLASS_FILE = "universe_classification.toml"
PARAMETER_HISTORY_DIR = "parameter_history"

# Schema
SCHEMA_VERSION = 1
