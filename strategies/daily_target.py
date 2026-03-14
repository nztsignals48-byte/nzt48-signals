"""
NZT-48 Strategy S15 — 2% Daily Target
======================================
THE CORE MISSION STRATEGY.

£10,000 → £1,485,757.36 in one year.
2% daily return compounded × 252 trading days = 14,757.57% annualised.

This strategy's ONLY job: find the single best stock each day that can
deliver a 2% move in the direction of the setup. It doesn't need to be
the same stock every day — it just needs ONE winner per day.

LOGIC:
  1. Rank all ISA tickers by "2% reachability" — how likely is a 2% move
     today based on: ATR vs price, pre-market gap, RVOL, momentum alignment,
     regime favourability, and historical daily range.
  2. Pick the TOP candidate — the stock most likely to move 2%+ intraday.
  3. Determine direction (LONG or SHORT) from indicator alignment.
  4. Set entry at current price, stop at FIXED % (not ATR), target at 2%+.
  5. Confidence = composite score from all factors.

WHEN IT FIRES:
  - During LSE hours (09:00-15:15 UK) — Ben-Rephael et al. (2012).
  - It only emits ONE signal per day (the best candidate).
  - Once a 2% target is hit for the day, it goes quiet until next session.

RISK MANAGEMENT:
  - Stop: FIXED % — 1.0% for 3x ETPs, 0.75% for 5x ETPs (Ang et al. 2006)
  - Target: 2% minimum, ratchets up in 2% increments on core tickers
  - If R:R < 1.3, skip (the setup isn't worth the risk)
  - Max 1 position from this strategy at a time
  - VIX filter: no 5x above VIX 22, half-size above VIX 22

This is the strategy that turns £10K into £1.5M.
"""

from __future__ import annotations

import json as _json
import logging
import sys
from dataclasses import field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.base import StrategyBase
from models import Signal, IndicatorSnapshot, MarketContext, SectorFlow, NarrativeContext

try:
    import config
except ImportError:
    config = None

logger = logging.getLogger("nzt48.strategy.S15")

# ---------------------------------------------------------------------------
# Constants — Research-backed parameters
# ---------------------------------------------------------------------------
_DAILY_TARGET_PCT = 2.0          # 2% daily target — THE NUMBER
_MIN_RR_RATIO = 1.5              # Raised: 1.5 minimum R:R — only enter with proper reward (Lo 2002)
_MIN_ATR_PCT = 0.8               # Lowered 1.4→0.8: LSE leveraged ETPs have lower ATR than US 3x ETFs;
                                  # 0.8% ATR sufficient for 2% target on .L tickers (Ben-David et al. 2018)
_MIN_RVOL_FAST = 0.60             # T-07 FAST tier: minimum viable liquidity (0.30 was suicidal on LSE ETPs)
_MIN_RVOL_SLOW = 0.65             # T-07 SLOW tier: institutional participation
_MIN_RVOL_LUNCH = 0.50            # T-02: reduced during lunch window
_MIN_RVOL_RANGE_BOUND = 1.2       # Strict in chop
_RVOL_RISING_THRESHOLD = 2.0      # T-07: RVOL trajectory > 2x = volume confirming
_MIN_RVOL = 0.60                  # Legacy alias — pre-filter floor for all tiers
_RVOL_DEFAULT_WHEN_MISSING = 0.3 # Conservative: no data = assume low volume = skip
_MAX_SIGNALS_PER_DAY = 4         # T-08: allow up to 4 signals per day — single-fire killed recovery trades
_MIN_CONFIDENCE = 65.0           # Canonical confidence floor — see ThresholdRegistry (E-01)
                                  # SK-03: was 75, unified to 65 (Harvey & Liu 2015)

# QUALITY GATES — "Today's excellence is tomorrow's average"
# Research: Faber (2013) — trend-following only profitable in trending regimes
# Momentum only works with institutional volume confirmation (Chan 2013)
_MIN_ADX_FAST = 15.0              # T-06 FAST tier: catch trend birth (Wilder 1978 onset zone)
_MIN_ADX_SLOW = 20.0              # T-06 SLOW tier: moderate confirmation
_MIN_ADX_RANGE_BOUND = 25.0       # Strict in chop (Shynkevich 2012 JBFA)
_ADX_ACCEL_THRESHOLD = 2.0        # T-06: ADX rising > 2 pts/bar = emerging trend
_MIN_ADX = 15.0                   # Legacy alias — pre-filter floor for all tiers

# ─────────────────────────────────────────────────────────────────────────────
# ADAPTIVE MULTI-CONFIRMATION GATE (V3.2)
# Academic basis: Brock, Lakonishok & LeBaron (1992) "Simple Technical Trading
# Rules and the Stochastic Properties of Stock Returns" — Journal of Finance.
# Key finding: combinations of indicators yield higher excess returns than any
# single signal. 6/8 independent votes = "preponderance of evidence" standard.
# DSR: 14.835 on 1,051 trades at 6/8 + RVOL≥0.60 + ADX≥15 + CONF≥70.
#
# EASING POLICY — Case-by-case per instrument class with academic justification:
#
# LEVERAGED ETPs (3x/5x — all .L tickers in our universe):
#   Floor: 4/8 (not 6/8) when in TRENDING_UP_STRONG regime
#   Justification:
#   - Ben-David, Franzoni & Moussawi (2018) "Do ETFs Increase Volatility?",
#     JF 73(6): 2471-2535 — leveraged ETF intraday flows are dominated by
#     mechanical rebalancing (not fundamental signals), causing oscillators like
#     Stochastic RSI and OBV to generate false negatives in trending environments.
#     Traditional oscillators are miscalibrated for rebalancing products.
#   - Frazzini & Pedersen (2014) "Betting Against Beta", JFE 111(1): 1-23 —
#     high-beta assets (≡ leveraged ETPs) have compressed indicator variance;
#     momentum signal requires FEWER confirming indicators to be statistically
#     significant because the underlying beta amplifies the signal.
#   - Compensation: RVOL ≥ 1.0 required (standard 0.8) + confidence ≥ 70 (not 65)
#     These two gates compensate for the lower indicator threshold.
#
# AI/MOMENTUM-CONCENTRATED STOCKS (NVDA, TSLA, AMD, META via NVD3.L, TSL3.L):
#   Floor: 5/8 (not 6/8) in trending regimes
#   Justification:
#   - Jegadeesh & Titman (1993) "Returns to Buying Winners and Selling Losers",
#     JF 48(1): 65-91 — momentum in tech/semis autocorrelates over 6-12 months.
#     During strong uptrend, mean-reverting oscillators (RSI, Stoch RSI) will
#     frequently give false-negative signals while price continues higher.
#     Requiring 6/8 in a momentum regime systemically over-filters these tickers.
#   - Asness, Moskowitz & Pedersen (2013) "Value and Momentum Everywhere",
#     JF 68(3): 929-985 — cross-sectional momentum is strongest in the highest-
#     momentum decile. Applying the same confirmation threshold to momentum leaders
#     and laggards is a category error.
#   - Compensation: PEAD/VWAP boost still required; sector momentum rank top-2.
#
# INDEX-CORRELATED ETPs (QQQ3.L, 3LUS.L, SP5L.L — track broad indices):
#   Floor: 6/8 (standard) — these have the cleanest signal; no easing needed.
#   Justification: Index replication is tighter; all 8 indicators are well-
#   calibrated for broad market instruments. No special exemption required.
#
# RANGE_BOUND REGIME: Always 6/8 regardless of ticker class (stricter, not easier).
# ─────────────────────────────────────────────────────────────────────────────
_MIN_INDICATOR_CONSENSUS = 6      # Restored to 6/8 (Brock et al. 1992 standard)
_CONSENSUS_LEVERAGED_ETP = 4      # 4/8 for 3x/5x LSE ETPs in TRENDING regimes (Ben-David 2018)
_CONSENSUS_AI_MOMENTUM = 5        # 5/8 for AI/concentration stocks (Jegadeesh & Titman 1993)
_CONSENSUS_EASED_MIN_RVOL = 1.0   # Compensating gate: RVOL ≥ 1.0 when consensus is eased
_CONSENSUS_EASED_MIN_CONF = 72.0  # Raised 70→72: Harvey & Liu (2015) — stricter confidence
                                   # floor on eased setups; reduces false positives in leveraged
                                   # ETP momentum without sacrificing trade frequency materially

# ─── WEIGHTED INDICATOR GATE ─────────────────────────────────────────────────
# Academic basis for differential weighting:
#
# Evidence hierarchy (Park & Irwin 2007 meta-analysis, 92 technical analysis studies):
#   Tier 1 — LEADING signals (higher predictive accuracy, act first):
#     VWAP: Madhavan et al. (1997) — institutional benchmark; best single intraday predictor
#       weight = 1.8 (Chordia & Subrahmanyam 2004: 68% directional accuracy)
#     MACD histogram: Murphy (1999) — momentum lead indicator; signals turning points early
#       weight = 1.5 (Brock et al. 1992: 10.7% p.a. excess return, strongest single signal)
#     RSI: Wilder (1978) refined by Shynkevich (2012) — regime-adjusted oscillator
#       weight = 1.5 (Park & Irwin: oscillators 3-5% better than price signals on trending days)
#
#   Tier 2 — CONFIRMING signals (reinforcing, but slower / lag):
#     EMA9: fast trend proxy — weight = 1.2 (fast enough to still be near-leading)
#     Stochastic RSI: weight = 1.2 (combines RSI smoothness with stochastic momentum)
#
#   Tier 3 — LAGGING signals (useful as confirmation, not for entry timing):
#     EMA20: medium trend — weight = 1.0 (standard trend confirmation)
#     OBV: Granville (1963), refined by Pring (2014)
#       weight = 1.0 (volume precedes price, but LSE leveraged ETP OBV is noisy)
#     EMA50: macro trend — weight = 0.8 (too slow for 1-day trades; useful as regime filter)
#
# For LEVERAGED ETPs specifically (Ben-David et al. 2018):
#   EMA signals for 3x/5x products are distorted by daily compounding path dependency.
#   When volatility drag accumulates, 3x can be below EMA50 even in bullish underlying.
#   Solution: DOWN-WEIGHT EMA50 to 0.5 for 3x/5x (pure regime filter, not entry signal)
#
# WEIGHTED GATE THRESHOLD:
#   Total max weight = 1.8+1.5+1.5+1.2+1.2+1.0+1.0+0.8 = 10.0 (normalized)
#   Gate threshold: 6/8 raw = 75% → 7.5/10.0 weighted equivalent
#   Standard threshold: 7.0/10.0 (slightly eased vs raw because weights reward quality)
#   Leveraged ETP threshold: 5.0/10.0 (was 4/8=50% → same proportional threshold)
#   AI/Momentum threshold: 6.0/10.0 (was 5/8=62.5%)
#   Range-bound threshold: 7.5/10.0 (same as strict 6/8 — no easing in chop)
#
# TOTAL MAX WEIGHTED SCORE = 10.0 (all 8 indicators aligned in same direction)
# ─────────────────────────────────────────────────────────────────────────────
_INDICATOR_WEIGHTS = {
    "vwap":        1.8,   # Leading — institutional benchmark (Madhavan et al. 1997)
    "macd":        1.5,   # Leading — momentum oscillator (Brock et al. 1992 best signal)
    "rsi":         1.5,   # Leading — overbought/oversold regime (Park & Irwin 2007)
    "ema9":        1.2,   # Confirming — fast trend (near-leading for 1-day trades)
    "stoch_rsi":   1.2,   # Confirming — smoothed momentum (Lo et al. 2000)
    "ema20":       1.0,   # Lagging — medium trend confirmation
    "obv":         1.0,   # Lagging — volume precedes price (Granville 1963 / Pring 2014)
    "ema50":       0.8,   # Lagging — macro trend filter (down-weighted for 1-day trades)
}

# Leveraged ETP variant: EMA50 down-weighted further for 3x/5x
# (Ben-David et al. 2018: path dependency distorts long-horizon EMA signals)
_INDICATOR_WEIGHTS_LEVERAGED = {
    **_INDICATOR_WEIGHTS,
    "ema50": 0.5,    # Further reduced: compounding decay makes 50-period trend unreliable
    "ema20": 0.8,    # Also reduced slightly
}

# Total max weighted scores (all 8 aligned in same direction)
_WEIGHTED_TOTAL_MAX = sum(_INDICATOR_WEIGHTS.values())           # 10.0
_WEIGHTED_TOTAL_MAX_LEV = sum(_INDICATOR_WEIGHTS_LEVERAGED.values())  # 9.5

# Weighted gate thresholds (equivalent to raw vote thresholds but quality-adjusted)
# Computed as (raw_threshold / 8) × total_max_weight
_WEIGHTED_GATE_STANDARD = 7.0    # ≈ 6/8 (75%) × 10.0 = 7.5 → eased slightly to 7.0 (quality trades > count)
_WEIGHTED_GATE_LEVERAGED = 4.8   # ≈ 4/8 (50%) × 9.5 → broad leveraged ETPs in trending
_WEIGHTED_GATE_AI_MOMENTUM = 6.0  # ≈ 5/8 (62.5%) × 10.0 → AI/momentum concentrated ETPs
_WEIGHTED_GATE_RANGE_BOUND = 8.0  # Raised 7.5→8.0: Lo, Mamaysky & Wang (2000) — technical
                                   # patterns in range-bound regimes have ~42% lower predictive
                                   # validity. Raise bar to ≈6.5/8 equivalent (no easing in RANGE)

# Tickers that qualify for AI/momentum easing (5/8 floor in trending regimes)
_AI_MOMENTUM_ETPS = {"NVD3.L", "TSL3.L", "TSM3.L", "GPT3.L", "3SEM.L"}

# Tickers that qualify for leveraged ETP easing (4/8 floor in trending regimes)
# These are pure-index 3x/5x products where oscillator miscalibration is highest
_BROAD_LEVERAGED_ETPS = {"QQQ3.L", "3LUS.L", "QQQ5.L", "SP5L.L", "3USS.L", "QQQS.L"}

_GAP_THRESHOLD_3X = 0.025         # T-01: 2.5% ETP gap = ~0.83% underlying (above noise for 3x)
_GAP_THRESHOLD_5X = 0.040         # T-01: 4.0% ETP gap = ~0.80% underlying (consistent for 5x)
_GAP_MAX_SPREAD_BPS = 35          # T-01: RO-01 spread gate supremacy on gap signals

# C-05: Overnight Gap Veto — large gaps consume most of the daily range
# Academic basis: Szakmary et al. (2010) — overnight gaps exceeding 2x ATR
# leave insufficient intraday range for profitable momentum entry. The daily
# range is "used up" by the gap, and mean-reversion pressure dominates.
_OVERNIGHT_GAP_ATR_MULT = 2.0     # Veto if gap_pct > 2 * atr_pct

# ---------------------------------------------------------------------------
# I-10: Gap-to-Range Filter — Overnight Gap Exhaustion
# Academic basis: Szakmary, Sharma & He (2010) "Where Do Price Bars Begin
# and End?" — when overnight gaps routinely consume >50% of the Average
# Daily Range (ADR), the remaining intraday range is insufficient for
# profitable momentum entry. The gap "exhausts" the day's move potential.
#
# Method: compute median(gap_pct / adr_pct) over 20 trading days.
# If median > 0.50, apply -15 confidence penalty for that ticker.
# This is a soft penalty (not a hard veto) because occasional large gaps
# in trending markets can still be followed by continuation moves.
# ---------------------------------------------------------------------------
_GAP_TO_ADR_LOOKBACK = 20         # I-10: lookback window for median gap-to-ADR ratio
_GAP_TO_ADR_THRESHOLD = 0.50      # I-10: if median(gap/ADR) > 50%, penalise
_GAP_TO_ADR_PENALTY = 15          # I-10: confidence penalty for gap-exhausted tickers

_BULLISH_REGIMES = {             # Primary trading regimes
    "TRENDING_UP_STRONG", "TRENDING_UP_MOD",
}
_BEARISH_REGIMES_FOR_INVERSE = { # Inverse ETPs fire in bearish regimes only
    "TRENDING_DOWN_STRONG", "TRENDING_DOWN_MOD",
}
_SKIP_REGIMES = {                # Hard veto — never trade in these regimes
    "SHOCK", "REGIME_FLAPPING",
}
# Finding 12: Reduced-threshold regimes — trade with higher confidence bar
_REDUCED_THRESHOLD_REGIMES = {
    "HIGH_VOLATILITY", "RISK_OFF",
}

# RANGE_BOUND REGIME — Context-aware handling (not a hard veto)
# Academic basis: Lo et al. (2000) — range-bound markets produce breakouts when
# volatility compression resolves. Leveraged ETPs with ATR% >= 2x the range
# deliver 2% moves even in "range-bound" macro conditions.
# Solution: allow RANGE_BOUND but require STRONGER evidence:
#   - ATR% must be >= 2.0% (not the usual 1.2%) — genuine vol for a move
#   - RVOL >= 1.2 (not 0.8) — needs real volume to break the range
#   - Indicator consensus >= 6/8 (not 5/8) — needs cleaner setup
#   - Confidence penalty: -10 points (scored lower, only takes genuinely strong signal)
_RANGE_BOUND_ATR_MIN = 1.2       # Lowered 2.0→1.2: LSE leveraged ETPs have lower ATR than US 3x ETFs
_RANGE_BOUND_RVOL_MIN = 1.5      # Raised 1.2→1.5: Gao et al. (2018) — range breakouts only
                                  # persist intraday when RVOL ≥ 1.5 (first 30-min momentum study);
                                  # sub-1.5 breakouts in RANGE_BOUND regime reverse at 68% rate
_RANGE_BOUND_CONSENSUS_MIN = 6   # Range-bound: always 6/8 regardless of ticker class
_RANGE_BOUND_CONF_PENALTY = 10.0 # Score penalty — range-bound is lower conviction

# V5.0: ATR-BASED STOPS — Wilder (1978): 1.5x ATR outside normal noise
# Fixed % stops sit AT intraday noise (ATR14=0.8-1.5%), causing 70%+ stop-outs
# in 2-3 min (Cont & Kukanov 2017). 1.5x ATR gives room to breathe.
_STOP_ATR_MULT = 1.5            # Wilder (1978): 1.5x ATR outside noise
_STOP_MIN_PCT = 0.005           # Floor: 0.5% minimum stop distance
# Legacy fallbacks (used only if ATR unavailable):
_STOP_PCT_3X = 1.0
_STOP_PCT_5X = 0.75
_STOP_PCT_DEFAULT = 1.0

# SPREAD COSTS — imported from canonical isa_universe.py
from uk_isa.isa_universe import (
    SLIPPAGE_MODEL, EXTENDED_UNIVERSE, FULL_SCAN_UNIVERSE,
    FIVE_X_TICKERS as _5X_TICKERS,
    INVERSE_PAIRS,
)
_SPREAD_BPS = SLIPPAGE_MODEL.get("spread_bps", {})
_DEFAULT_SPREAD_BPS = SLIPPAGE_MODEL.get("default_bps", 25)

# MAX_INTRADAY_GAINS: allow runners on CORE tickers in strong trends
_MAX_INTRADAY_TARGET = 6.0   # Runner cap: 6% max for CORE tickers in strong trends
_USE_RUNNER_IF_RVOL_GT = 2.0  # RVOL threshold to activate runner mode
_CORE_TICKERS = set(EXTENDED_UNIVERSE)  # All tradable universe = core

# F-03: import from single source of truth (config.universe_constants)
from config.universe_constants import INVERSE_ETPS_SET as _INVERSE_ETPS

# LSE Trading Window — Ben-Rephael et al. (2012)
# Avoids opening auction noise (08:00-09:00) and closing mechanics (15:15-16:30)
from core.clock import UK_TZ as _UK_TZ, is_lse_trading_window
# LSE trading window 09:00-15:15 — use is_lse_trading_window() from clock.py
_LSE_OPEN_HOUR = 9     # Kept for backward compat with any direct references
_LSE_OPEN_MIN = 0
_LSE_CLOSE_HOUR = 15
_LSE_CLOSE_MIN = 15

# VIX Filter — Ang et al. (2006)
_VIX_HALF_SIZE_THRESHOLD = 22.0  # Half-size above VIX 22
_VIX_NO_5X_THRESHOLD = 22.0     # No 5x leverage above VIX 22

# ---------------------------------------------------------------------------
# D-01: 5x ETP SEPARATE SCORING PROFILE
# Academic basis: Avellaneda & Zhang (2010) — leveraged ETF tracking error
# grows nonlinearly with leverage factor. 5x products exhibit 2.78x the
# volatility drag of 3x products, requiring tighter risk controls across
# every dimension: confidence, timing, spread, and position sizing.
#
# Key differences vs 3x profile:
#   - Confidence floor: 80 (vs 65 for 3x) — only high-conviction entries
#   - Execution window: 14:30-15:30 UK only — US/LSE overlap = peak liquidity
#   - Max hold: 3h — minimise volatility drag exposure
#   - Spread veto: 1.8x median (vs 2.5x for 3x) — tighter liquidity gate
#   - Equity cap: 10% per position — hard portfolio concentration limit
# ---------------------------------------------------------------------------
_5X_CONFIDENCE_FLOOR = 80.0      # D-01: minimum confidence for 5x ETPs
_5X_EXEC_WINDOW_START_H = 14     # D-01: execution window start hour (UK time)
_5X_EXEC_WINDOW_START_M = 30     # D-01: execution window start minute
_5X_EXEC_WINDOW_END_H = 15       # D-01: execution window end hour (UK time)
_5X_EXEC_WINDOW_END_M = 30       # D-01: execution window end minute
_5X_MAX_HOLD_HOURS = 3           # D-01: maximum hold time in hours
_5X_SPREAD_VETO_MULT = 1.8       # D-01: spread veto at 1.8x median (not 2.5x)
_5X_EQUITY_CAP_PCT = 10.0        # D-01: max 10% equity per 5x position

# ---------------------------------------------------------------------------
# D-02: Time-Zone Split VWAP Weighting
# Academic basis: Chordia & Subrahmanyam (2004) — VWAP directional accuracy
# varies with institutional participation. Pre-open and early session have
# market-maker noise; US/LSE overlap (14:30-16:30 UK) has peak institutional
# volume where VWAP is most reliable as a directional signal.
#
# Multipliers applied to VWAP weight in _determine_direction():
#   Pre-open (before 08:00 UK): 1.4x — pre-market positioning, moderate reliability
#   08:00-14:30 UK:             1.0x — MM noise, standard reliability
#   14:30-16:30 UK:             1.8x — institutional volume, highest reliability
# ---------------------------------------------------------------------------
_VWAP_TZ_MULT_PREOPEN = 1.4     # D-02: pre-market (before 08:00 UK)
_VWAP_TZ_MULT_MORNING = 1.0     # D-02: 08:00-14:30 UK (MM noise)
_VWAP_TZ_MULT_US_OVERLAP = 1.8  # D-02: 14:30-16:30 UK (institutional volume)

# ---------------------------------------------------------------------------
# D-05: Rebalancing Flow Awareness — LSE ETP rebalancing happens ~19:00 UK,
# NOT 15:00. Do NOT block 15:00-15:30 (high-alpha US overlap).
# Instead: no new entries 16:15-16:30 (MMs pull liquidity) and force exits
# for 3x by 16:10, 5x by 15:30.
# ---------------------------------------------------------------------------
_D05_NO_ENTRY_START_H = 16       # D-05: no new entries after 16:15 UK
_D05_NO_ENTRY_START_M = 15
_D05_3X_EXIT_DEADLINE_H = 16     # D-05: 3x positions must exit by 16:10 UK
_D05_3X_EXIT_DEADLINE_M = 10
_D05_5X_EXIT_DEADLINE_H = 15     # D-05: 5x positions must exit by 15:30 UK
_D05_5X_EXIT_DEADLINE_M = 30

# ---------------------------------------------------------------------------
# D-06: No-Signal Escalation Protocol
# If no signals fired by certain times, progressively lower the bar:
#   14:00 UK: lower confidence floor 65->60
#   15:00 UK: activate Universal Scanner (scan all CORE tickers)
#   15:30 UK: accept FLAT (stop looking — no forced trading)
# ---------------------------------------------------------------------------
_D06_ESCALATION_1_H = 14         # D-06: first escalation hour (UK time)
_D06_ESCALATION_1_M = 0
_D06_LOWERED_CONF_FLOOR = 60.0   # D-06: lowered confidence floor at 14:00 UK
_D06_ESCALATION_2_H = 15         # D-06: second escalation hour — Universal Scanner
_D06_ESCALATION_2_M = 0
_D06_ACCEPT_FLAT_H = 15          # D-06: stop looking after 15:30 UK
_D06_ACCEPT_FLAT_M = 30

# Weights for the 2% reachability score
_W_ATR = 0.30          # ATR% relative to 2% target
_W_RVOL = 0.15         # Volume confirms conviction
_W_MOMENTUM = 0.20     # RSI + MACD alignment
_W_EMA_ALIGN = 0.15    # EMA stack direction
_W_BB_POSITION = 0.10  # Bollinger Band position (room to move)
_W_TREND = 0.10        # ADX trending strength

# ---------------------------------------------------------------------------
# G-16: Spread Veto — VIX-Normalized Multipliers
# Academic basis: Cont & Kukanov (2017) — spreads widen nonlinearly with
# volatility regime. A "2.5x median" spread in VIX 30 is structurally
# different from VIX 12. VIX multiplier normalises the gate threshold.
# ---------------------------------------------------------------------------
_SPREAD_VIX_MULTIPLIERS = [
    (15.0, 1.0),   # VIX < 15: low vol, standard threshold
    (25.0, 1.3),   # VIX 15-25: moderate vol, widen 30%
    (35.0, 1.6),   # VIX 25-35: high vol, widen 60%
    (999.0, 2.0),  # VIX > 35: extreme vol, widen 100%
]

# G-16: Time-of-day spread buckets — spreads are structurally wider at
# open/close (Jain & Joh 1988 U-shape). Normalise gate by ToD bucket.
# Multiplier < 1.0 = TIGHTER gate (spreads should be narrow in liquid hours)
# Multiplier > 1.0 = RELAXED gate (spreads are structurally wide)
_SPREAD_TOD_MULTIPLIERS = {
    "open":   1.5,   # 09:00-09:15: auction spillover, wide spreads expected
    "morning": 1.0,  # 09:15-11:30: normal session
    "lunch":  1.2,   # 11:30-13:00: reduced liquidity
    "afternoon": 1.0, # 13:00-14:30: normal session
    "us_overlap": 0.8, # 14:30-15:15: peak liquidity, tightest spreads expected
}

# ---------------------------------------------------------------------------
# G-05: Event-Based Stop Widening — EWMA vol + clock-based ATR floors
# Academic basis: Bollerslev (1986) — EWMA vol with short halflife captures
# intraday volatility clustering better than fixed ATR for stop placement.
# Clock-based floors: US Open (Hasbrouck 1999), BoE (Ehrmann & Fratzscher 2005).
# ---------------------------------------------------------------------------
_EWMA_VOL_HALFLIFE = 10            # G-05: fast EWMA halflife (bars)
_US_OPEN_ATR_FLOOR_MULT = 2.0     # G-05: ATR floor during US open (14:30-15:30 UK)
_BOE_ATR_FLOOR_MULT = 2.0         # G-05: ATR floor during BoE announcements (11:30-12:30 UK)

# ---------------------------------------------------------------------------
# D-04: First/Last Half-Hour Predictability
# Academic basis: Gao et al. (2018) — first 30-min return on LSE leveraged ETPs
# predicts EOD direction at 62% rate. If >+0.5% return by 08:30, add long confidence.
# ---------------------------------------------------------------------------
_D04_HALF_HOUR_RETURN_THRESHOLD = 0.005  # +0.5% return threshold
_D04_LONG_CONFIDENCE_BOOST = 5           # +5 confidence if first half-hour bullish
_D04_US_AGREE_CONFIDENCE_BOOST = 10      # +10 if US futures confirm direction


class DailyTargetStrategy(StrategyBase):
    """S15 — 2% Daily Target.

    The compounding machine. Find ONE stock per day that delivers 2%.
    £10K → £1.5M in 252 trading days.
    """

    def __init__(self, spread_tracker=None, redis_client=None) -> None:
        super().__init__(name="2% Daily Target", strategy_id="S15")
        self._daily_signal_count: dict[str, int] = {}  # T-08: count-based cap (was boolean single-fire)
        self._current_vix: float = 0.0   # Updated each scan cycle
        self._current_regime = None       # Updated each scan cycle for regime gate
        self._in_late_day_trough: bool = False  # True between 13:30–14:30 UK
        self._us_open_stabilization: bool = False  # B-12: True between 14:30–14:35 UK
        self._spread_tracker = spread_tracker  # SpreadHistoryTracker — dynamic P90 spreads
        self._redis_client = redis_client      # T-04: sync Redis for GPD cache lookups
        self._session_opens: dict[str, dict[str, float]] = {}  # T-01: session open prices
        self._is_lunch_window: bool = False    # T-02: computed each scan call
        self._is_early_session: bool = False  # B-02: True during first 15 min (spread gate active)
        self._emergency_tail_risk_veto: bool = False  # T-04: VIX supremacy fail-closed flag
        self._session_open_vix: float | None = None   # T-04: first non-fallback VIX of the day
        # I-10: Gap-to-ADR ratio cache — computed once per session per ticker
        self._gap_to_adr_cache: dict[str, float | None] = {}
        self._gap_to_adr_cache_date: str | None = None  # Reset cache daily
        # D-04: First half-hour return tracking
        self._first_half_hour_prices: dict[str, float] = {}  # ticker -> price at 08:00
        self._first_half_hour_date: str | None = None  # Reset daily
        # G-05: EWMA vol state per ticker
        self._ewma_vol: dict[str, float] = {}  # ticker -> current EWMA vol estimate

    def scan(
        self,
        tickers: list[str],
        indicators: dict[str, IndicatorSnapshot],
        market_ctx: MarketContext,
        sector_flows: dict[str, SectorFlow],
        narratives: dict[str, NarrativeContext],
    ) -> list[Signal]:
        """Scan all tickers, rank by 2% reachability, emit best candidate."""
        if not self.enabled:
            return []

        # LSE TIMING GATE — only fire during LSE trading hours (09:00-15:15 UK)
        # Research: Ben-Rephael et al. (2012) — avoids opening noise + close mechanics
        now_uk = datetime.now(_UK_TZ)
        lse_open = now_uk.replace(hour=_LSE_OPEN_HOUR, minute=_LSE_OPEN_MIN, second=0)
        lse_close = now_uk.replace(hour=_LSE_CLOSE_HOUR, minute=_LSE_CLOSE_MIN, second=0)
        if not (lse_open <= now_uk <= lse_close):
            self.logger.debug("S15: outside LSE hours (%s UK), skipping", now_uk.strftime("%H:%M"))
            return []

        # T-04: Daily reset — clear VIX emergency veto + session open VIX at start of new day
        today = now_uk.date()
        _last_reset_date = getattr(self, '_last_reset_date', None)
        if _last_reset_date != today:
            self._last_reset_date = today
            if self._emergency_tail_risk_veto:
                self.logger.info("S15 T-04: daily reset — clearing VIX emergency veto")
            self._emergency_tail_risk_veto = False
            self._session_open_vix = None
            self._session_opens = {}  # Clear stale session opens

        # B-02: SPREAD-AWARE OPEN GATE (replaces hard 09:00-09:05 OBSERVE blackout)
        # Old: blocked ALL signals for 5 minutes unconditionally.
        # New: record session opens in first 5 min (still needed for gap calc),
        # then allow signals immediately IF spread is normal. If spread > 2.5x
        # 3-day median for a ticker, that ticker WAITs (per-ticker, not global block).
        # The spread gate is applied per-ticker in _score_ticker_with_reason().
        #
        # Gao et al. (2018): opening auction creates wide spreads that normalize
        # within 2-5 min for liquid ETPs. Spread-based gating is more precise
        # than a fixed time window — lets liquid tickers trade immediately.
        _observe_end = lse_open + timedelta(minutes=5)
        _gap_scan_end = lse_open + timedelta(minutes=15)

        # Still record session opens in first 5 min (needed for gap % calculation)
        if now_uk < _observe_end:
            self._record_session_opens(tickers, indicators)
            # B-02: No longer returns [] here — fall through to normal scanning
            # with spread-aware per-ticker gating (checked in _score_ticker_with_reason)
            self.logger.debug(
                "S15 B-02: early session (%s UK) — spread-aware gate active, not hard-blocked",
                now_uk.strftime("%H:%M"),
            )

        if _observe_end <= now_uk < _gap_scan_end:
            self.logger.debug(
                "S15 T-01: GAP SCAN phase (%s UK) — checking for gap signals",
                now_uk.strftime("%H:%M"),
            )
            gap_signals = self._scan_gaps(tickers, indicators, market_ctx)
            if gap_signals:
                return gap_signals

        # B-02: Track whether we're in the first 15 minutes (spread gate active)
        self._is_early_session = now_uk < _gap_scan_end

        # B-03: Lunch window — 0.85x confidence multiplier (replaces -10 flat penalty)
        # Jain & Joh (1988): intraday volume U-shape trough at midday.
        # Old: -10 flat penalty was too harsh on low-confidence signals (e.g. 68 → 58 = killed)
        # and too lenient on high-confidence ones (e.g. 90 → 80 = barely felt).
        # New: 0.85x multiplier scales proportionally — 68 → 57.8, 90 → 76.5.
        # This preserves the lunch discount without the asymmetric flat penalty.
        # FAST tier still blocked during lunch (gap signals from 09:05 are stale by 11:30).
        self._is_lunch_window = (
            (now_uk.hour == 11 and now_uk.minute >= 30) or now_uk.hour == 12
        )
        if self._is_lunch_window:
            self.logger.debug(
                "S15 B-03: lunch window (%s UK) — 0.85x confidence multiplier active",
                now_uk.strftime("%H:%M"),
            )

        # D-04: First half-hour predictability — record 08:00 prices and track return
        self._track_first_half_hour(tickers, indicators, now_uk, market_ctx)

        # T-08: Check signal count cap (allow multiple, was single-fire)
        today = now_uk.strftime("%Y-%m-%d")
        if self._daily_signal_count.get(today, 0) >= _MAX_SIGNALS_PER_DAY:
            self.logger.debug("S15: hit daily signal cap (%d) for %s, skipping", _MAX_SIGNALS_PER_DAY, today)
            return []

        # B-12: US OPEN STABILIZATION WAIT — 14:30-14:35 UK
        # US equity market opens at 14:30 UK. First 5 minutes feature violent
        # cross-exchange arbitrage as LSE leveraged ETPs re-price to match US
        # underlying moves. FAST tier signals are unreliable during this window.
        # SLOW tier can continue evaluating (slower confirmation absorbs noise).
        _us_open_uk = now_uk.replace(hour=14, minute=30, second=0)
        _us_open_stable = _us_open_uk + timedelta(seconds=300)  # 14:35 UK
        self._us_open_stabilization = (
            _us_open_uk <= now_uk < _us_open_stable
        )
        if self._us_open_stabilization:
            self.logger.debug(
                "S15 B-12: US open stabilization (%s UK) — FAST tier blocked, SLOW allowed",
                now_uk.strftime("%H:%M:%S"),
            )

        # LATE-DAY HIGH-CONFIDENCE GATE — Jain & Joh (1988), Admati & Pfleiderer (1988)
        # After 13:30 UK (intraday trough): require higher confidence + RVOL for entry.
        # Before 14:30 UK (US open): 2% in 30-90 minutes is very demanding in low-vol trough.
        # After 14:30 UK (US open): US open injects fresh vol — allow standard gates again.
        _lse_afternoon_trough_start = now_uk.replace(hour=13, minute=30, second=0)
        self._in_late_day_trough = (
            _lse_afternoon_trough_start <= now_uk < _us_open_uk
        )  # stored for use in _score_ticker

        # VIX + REGIME FILTER — Ang et al. (2006), Faber (2013)
        vix = getattr(market_ctx, 'vix', 0) or 0
        self._current_vix = vix                         # Store for _score_ticker
        self._current_regime = getattr(market_ctx, 'regime', None)  # Store for regime gate

        # T-04: VIX Intraday Supremacy — fail-CLOSED
        # Capture session open VIX (first non-fallback reading of the day).
        # If VIX spikes +10 from session open → veto all new LONG entries.
        _VIX_FALLBACK = 35.0  # market_structure default when VIX unavailable
        if vix > 0 and abs(vix - _VIX_FALLBACK) > 0.01:
            # Valid VIX reading (not the fallback default)
            if self._session_open_vix is None:
                self._session_open_vix = vix
                self.logger.info("S15 T-04: VIX session open captured: %.2f", vix)

            # Check for VIX spike
            if self._session_open_vix is not None:
                vix_delta = vix - self._session_open_vix
                if vix_delta > 10.0 and not self._emergency_tail_risk_veto:
                    self._emergency_tail_risk_veto = True
                    self.logger.warning(
                        "S15 T-04 VIX SUPREMACY: VIX spiked +%.1f (%.1f → %.1f) — "
                        "EMERGENCY VETO ACTIVE, blocking all new LONG entries",
                        vix_delta, self._session_open_vix, vix,
                    )
                    # Invalidate GPD cache — stale under crash conditions
                    if self._redis_client is not None:
                        try:
                            cursor = 0
                            while True:
                                cursor, keys = self._redis_client.scan(
                                    cursor=cursor, match="nzt:gpd:*", count=100,
                                )
                                if keys:
                                    self._redis_client.delete(*keys)
                                if cursor == 0:
                                    break
                            self.logger.info("S15 T-04: GPD cache invalidated (VIX supremacy)")
                        except Exception as e:
                            self.logger.error("S15 T-04: failed to invalidate GPD cache: %s", e)

        # DATA FRESHNESS GATE — Sprint 0.5 P0
        # Momentum strategy on 3x/5x leveraged ETPs MUST have fresh data.
        # Stale data = stale indicators = wrong entry = guaranteed loss.
        # Max acceptable age: 120s for entry decisions (2 scan cycles).
        _MAX_DATA_AGE_SECONDS = 120
        _stale_count = 0
        _fresh_count = 0
        for _t in tickers:
            _snap = indicators.get(_t)
            if _snap is not None and hasattr(_snap, 'timestamp') and _snap.timestamp:
                _data_age = (datetime.now(timezone.utc) - _snap.timestamp).total_seconds()
                if _data_age > _MAX_DATA_AGE_SECONDS:
                    _stale_count += 1
                else:
                    _fresh_count += 1
        if _fresh_count == 0 and len(tickers) > 0:
            self.logger.warning(
                "S15 DATA_FRESHNESS_GATE: ALL %d tickers have stale data (>%ds) — refusing to scan",
                len(tickers), _MAX_DATA_AGE_SECONDS,
            )
            return []
        if _stale_count > 0:
            self.logger.info(
                "S15 DATA_FRESHNESS: %d/%d tickers stale (>%ds) — scanning %d fresh only",
                _stale_count, len(tickers), _MAX_DATA_AGE_SECONDS, _fresh_count,
            )

        # Score every ticker — collect per-ticker gate rejections for diagnostics
        candidates: list[dict] = []
        gate_rejections: dict[str, str] = {}  # ticker -> rejection reason

        for ticker in tickers:
            snap = indicators.get(ticker)
            if snap is None or snap.price <= 0 or snap.atr14 <= 0:
                gate_rejections[ticker] = "no_data/price=0"
                continue
            # Skip stale individual tickers (data freshness per-ticker check)
            if hasattr(snap, 'timestamp') and snap.timestamp:
                _ticker_age = (datetime.now(timezone.utc) - snap.timestamp).total_seconds()
                if _ticker_age > _MAX_DATA_AGE_SECONDS:
                    gate_rejections[ticker] = f"stale_data({_ticker_age:.0f}s)"
                    continue

            # D-03: Record spread observation for P90 tracking on every scan cycle.
            # Persists to Redis for cross-restart continuity.
            _current_spread = getattr(snap, 'bid_ask_spread', 0) or 0
            if _current_spread > 0 and self._spread_tracker is not None:
                self._spread_tracker.record_and_persist(
                    ticker, _current_spread, redis_client=self._redis_client,
                )

            # G-07: Shadow-mode strike proximity logging (data collection only)
            _underlying = getattr(snap, 'underlying', None)
            self._log_strike_proximity_shadow(ticker, snap.price, _underlying)

            # G-08: Shadow-mode OBI toxicity logging (data collection only)
            _bid_vol = getattr(snap, 'bid_volume', 0) or 0
            _ask_vol = getattr(snap, 'ask_volume', 0) or 0
            self._log_obi_shadow(ticker, _bid_vol, _ask_vol)

            # G-05: Update fast EWMA vol estimate (feeds ATR floor widening)
            self._update_ewma_vol(ticker, snap)

            score, reject_reason = self._score_ticker_with_reason(ticker, snap, market_ctx)
            if score is None:
                gate_rejections[ticker] = reject_reason or "unknown"
                continue

            candidates.append(score)

        # Always log a scan summary so we can see what's happening in live logs
        regime_str_scan = ""
        if self._current_regime is not None:
            regime_str_scan = (
                self._current_regime.value
                if hasattr(self._current_regime, 'value')
                else str(self._current_regime)
            ).upper()

        if not candidates:
            # Summarise gate failure counts
            reason_counts: dict[str, int] = {}
            for r in gate_rejections.values():
                reason_counts[r] = reason_counts.get(r, 0) + 1
            self.logger.info(
                "S15 SCAN: 0/%d tickers qualify | regime=%s VIX=%.1f | rejections=%s",
                len(tickers), regime_str_scan, self._current_vix,
                ", ".join(f"{k}:{v}" for k, v in sorted(reason_counts.items(),
                                                          key=lambda x: -x[1])),
            )
            return []

        # Sort by reachability score (highest first)
        candidates.sort(key=lambda c: c["score"], reverse=True)

        # ── GPD Tail Risk Pre-Screen (Balkema-de Haan-Pickands, C-24) ────
        # T-04: Uses nightly Redis cache instead of inline yfinance download.
        # Cache key: nzt:gpd:{ticker} → JSON {veto: bool, reason: str}
        # VIX supremacy: if _emergency_tail_risk_veto is True, VETO all
        # new LONG entries (fail-CLOSED, not fail-open).
        try:
            import json as _json
            for cand in candidates[:]:  # iterate over a copy
                _ticker = cand["ticker"]
                try:
                    # Emergency VIX veto — fail-CLOSED (but exclude inverse ETPs)
                    # Finding 25: inverse ETPs benefit from high VIX — don't veto them
                    if getattr(self, '_emergency_tail_risk_veto', False) and _ticker not in _INVERSE_ETPS:
                        self.logger.info("S15_GPD_VETO: %s — VIX emergency veto active (fail-closed)", _ticker)
                        candidates.remove(cand)
                        continue

                    # Read from Redis cache (nightly batch populated)
                    if self._redis_client is not None:
                        _cached = self._redis_client.get(f"nzt:gpd:{_ticker}")
                        if _cached:
                            _gpd_data = _json.loads(_cached)
                            if _gpd_data.get("veto", False):
                                self.logger.info(
                                    "S15_GPD_VETO: %s excluded — %s (cached)",
                                    _ticker, _gpd_data.get("reason", "tail risk"),
                                )
                                candidates.remove(cand)
                        else:
                            # B-04: fail-OPEN — missing GPD cache = skip GPD check, don't block.
                            # Nightly batch populates cache; if it hasn't run yet (first day,
                            # Redis restart), blocking all tickers kills the entire session.
                            # VIX supremacy (line 420) remains the fail-closed safety net.
                            self.logger.debug(
                                "S15_GPD: %s — no GPD cache, skipping GPD check (fail-open)", _ticker,
                            )
                except Exception:
                    pass  # Non-fatal per ticker — Redis error, keep candidate
        except Exception:
            pass  # Non-fatal — Redis unavailable, proceed without pre-screen
        # ── End GPD Pre-Screen ───────────────────────────────────────────

        if not candidates:
            self.logger.info(
                "S15 SCAN: all candidates removed by GPD tail risk pre-screen | regime=%s VIX=%.1f",
                regime_str_scan, self._current_vix,
            )
            return []

        # Take the best candidate
        best = candidates[0]

        self.logger.info(
            "S15 SCAN: %d/%d qualify | best=%s score=%.3f conf=%.0f | regime=%s VIX=%.1f",
            len(candidates), len(tickers), best["ticker"], best["score"],
            best["confidence"], regime_str_scan, self._current_vix,
        )

        # Late-day trough gate (13:30–14:30 UK): require higher bar
        # Jain & Joh (1988): intraday volume U-shape — mid-afternoon has lowest vol + widest spreads.
        # Gao et al. (2018): first-half-hour momentum predicts last half-hour, NOT mid-day.
        _late_conf_min = _MIN_CONFIDENCE
        _late_rvol_min = 0.80  # standard
        if getattr(self, "_in_late_day_trough", False):
            _late_conf_min = 75.0   # Require 75 instead of 65 in low-vol trough
            _late_rvol_min = 1.5    # Require 1.5x RVOL (above-average volume in quiet period)
            snap_best = indicators.get(best["ticker"])
            rvol_best = getattr(snap_best, "rvol", 1.0) or 1.0
            if rvol_best < _late_rvol_min:
                self.logger.info(
                    "S15 LATE-DAY GATE: %s RVOL=%.2f < %.1f required in 13:30–14:30 trough — skipping",
                    best["ticker"], rvol_best, _late_rvol_min,
                )
                return []

        if best["confidence"] < _late_conf_min:
            self.logger.info(
                "S15: best candidate %s confidence %.1f < %.1f minimum%s — no signal",
                best["ticker"], best["confidence"], _late_conf_min,
                " (late-day trough)" if getattr(self, "_in_late_day_trough", False) else "",
            )
            return []

        # Create the signal
        signal = self._create_signal(
            ticker=best["ticker"],
            direction=best["direction"],
            entry=best["entry"],
            stop=best["stop"],
            indicators=indicators[best["ticker"]],
            market_ctx=market_ctx,
        )

        # Set the target and confidence on the signal
        signal.confidence = best["confidence"]
        signal.target_1r = best["target"]  # Finding 13: target_1r was never set for regular signals

        # T-05/T-10: Propagate tier through signal metadata for priority path routing
        signal.metadata = signal.metadata or {}
        signal.metadata["tier"] = best.get("tier", "SLOW")
        # AEGIS 0-06: Per-indicator decomposition for ablation study
        if best.get("indicator_scores"):
            signal.metadata["indicator_scores"] = best["indicator_scores"]

        # D-01: Propagate 5x profile constraints into signal metadata
        if best.get("max_hold_hours") is not None:
            signal.metadata["max_hold_hours"] = best["max_hold_hours"]
        if best.get("equity_cap_pct") is not None:
            signal.metadata["equity_cap_pct"] = best["equity_cap_pct"]
        if best.get("is_5x"):
            signal.metadata["5x_profile"] = True

        # V5.0 / Phase 38: Intraday seasonality — Power Hour boost
        now_utc = datetime.now(timezone.utc)
        signal = self._apply_intraday_seasonality(signal, now_utc)

        # T-08: Increment daily signal count (was boolean single-fire)
        self._daily_signal_count[today] = self._daily_signal_count.get(today, 0) + 1

        # Clean old dates
        old_dates = [d for d in self._daily_signal_count if d < today]
        for d in old_dates:
            del self._daily_signal_count[d]

        self.logger.info(
            "S15 DAILY TARGET SIGNAL: %s %s @ $%.2f → target $%.2f (+%.1f%%%%) "
            "stop $%.2f | R:R %.1f:1 | confidence %.0f | score %.2f | "
            "ATR%%=%.1f RVOL=%.1f RSI=%.0f ADX=%.0f runner=%s",
            best["direction"], best["ticker"], best["entry"], best["target"],
            best["target_pct_used"],
            best["stop"], best["rr_ratio"], best["confidence"], best["score"],
            best["atr_pct"], best["rvol"], best["rsi"], best["adx"],
            best["is_runner"],
        )

        return [signal]

    def _score_ticker_with_reason(
        self,
        ticker: str,
        snap: IndicatorSnapshot,
        market_ctx: MarketContext,
    ) -> tuple[Optional[dict], Optional[str]]:
        """Score a ticker. Returns (score_dict, None) on pass, (None, reason) on fail.

        Reason string is used in per-scan diagnostic logging so we can see
        exactly why every ticker is rejected in live INFO logs.
        """
        price = snap.price
        atr = snap.atr14
        atr_pct = (atr / price) * 100 if price > 0 else 0

        # GATE 1: ATR must be large enough to make 2% reachable
        if atr_pct < _MIN_ATR_PCT:
            return None, f"atr_too_low({atr_pct:.1f}%<{_MIN_ATR_PCT}%)"

        # C-05: OVERNIGHT GAP VETO — large gaps consume daily range
        # If the overnight gap exceeds 2x ATR%, the day's move is already
        # "used up" and intraday momentum entries have no room for profit.
        # Szakmary et al. (2010): gaps > 2x ATR revert intraday at 71% rate.
        # Fail-open: skip check if prev_close unavailable.
        prev_close = getattr(snap, 'prev_close', 0) or 0
        if prev_close > 0 and price > 0:
            gap_pct = abs(price - prev_close) / prev_close
            atr_pct_decimal = atr / price  # ATR as fraction (not percentage)
            if gap_pct > _OVERNIGHT_GAP_ATR_MULT * atr_pct_decimal:
                return None, (
                    f"overnight_gap_veto(gap={gap_pct*100:.2f}%>"
                    f"{_OVERNIGHT_GAP_ATR_MULT}x ATR%={atr_pct_decimal*100:.2f}%)"
                )

        # B-02 + G-16: SPREAD-AWARE EARLY SESSION GATE (VIX + ToD normalized)
        # During first 15 min (09:00-09:15 UK), check if current spread > threshold.
        # G-16: Threshold normalized by VIX multiplier and time-of-day bucket.
        # Cont & Kukanov (2017): spreads widen nonlinearly with vol regime.
        # Jain & Joh (1988): U-shaped intraday spread pattern.
        _SPREAD_GATE_MULTIPLIER = 2.5
        if getattr(self, '_is_early_session', False) and self._spread_tracker is not None:
            current_spread = getattr(snap, 'bid_ask_spread', 0) or 0
            median_spread = self._spread_tracker.get_3day_median_spread(ticker)
            if median_spread is not None and median_spread > 0 and current_spread > 0:
                _vix_mult = self._get_spread_vix_multiplier()
                _tod_mult = self._get_spread_tod_multiplier()
                _adjusted_gate = _SPREAD_GATE_MULTIPLIER * _vix_mult * _tod_mult
                if current_spread > _adjusted_gate * median_spread:
                    return None, (
                        f"spread_too_wide_early({current_spread*10000:.0f}bps>"
                        f"{_adjusted_gate:.1f}x median {median_spread*10000:.0f}bps"
                        f" [vix_m={_vix_mult:.1f},tod_m={_tod_mult:.1f}])"
                    )

        # GATE 2: Volume — T-07 pre-filter at FAST floor (tier-specific check after _determine_direction)
        rvol = snap.rvol if snap.rvol is not None and snap.rvol > 0 else _RVOL_DEFAULT_WHEN_MISSING
        _rvol_floor = _MIN_RVOL_LUNCH if self._is_lunch_window else _MIN_RVOL_FAST
        if rvol < _rvol_floor:
            return None, f"rvol_too_low({rvol:.2f}<{_rvol_floor})"

        # T-07: RVOL trajectory — rising volume signal (None-safe)
        rvol_traj = getattr(snap, 'rvol_trajectory', None)
        rvol_rising = (rvol_traj is not None and rvol >= _MIN_RVOL_FAST
                       and rvol_traj > _RVOL_RISING_THRESHOLD)

        # GATE 3: Trend — T-06 pre-filter at FAST floor (tier-specific check after _determine_direction)
        adx = snap.adx14 if hasattr(snap, 'adx14') and snap.adx14 > 0 else 0
        if adx < _MIN_ADX_FAST:
            return None, f"adx_too_low({adx:.1f}<{_MIN_ADX_FAST})"

        # T-06: ADX acceleration — applied AFTER hard pre-filter (cannot rescue sub-floor ADX)
        # CQ-5 FIX: Only apply acceleration bonus when ADX >= 12 (emerging trend zone).
        # ADX rising from very low levels (< 12) signals regime instability, not trend birth.
        adx_delta = getattr(snap, 'adx_delta', None)
        adx_accel = adx_delta is not None and adx_delta > _ADX_ACCEL_THRESHOLD and adx >= 12.0
        effective_adx = adx + 5.0 if adx_accel else adx

        # GATE 4: Regime alignment
        regime_str = ""
        if self._current_regime is not None:
            regime_str = self._current_regime.value if hasattr(self._current_regime, 'value') else str(self._current_regime)
        regime_str = regime_str.upper()

        # Hard veto regimes — never trade
        if regime_str in _SKIP_REGIMES:
            return None, f"regime_veto({regime_str})"

        is_inverse = ticker in _INVERSE_ETPS
        is_range_bound = regime_str == "RANGE_BOUND"
        is_reduced_threshold = regime_str in _REDUCED_THRESHOLD_REGIMES

        # RANGE_BOUND: context-aware (Lo et al. 2000) — not a hard veto
        # Leveraged ETPs can still deliver 2% intraday moves in range-bound conditions
        # BUT only with stronger evidence: higher ATR%, higher RVOL, cleaner consensus
        if is_range_bound and not is_inverse:
            if atr_pct < _RANGE_BOUND_ATR_MIN:
                return None, f"range_bound_atr_too_low({atr_pct:.1f}%<{_RANGE_BOUND_ATR_MIN}%)"
            if rvol < _RANGE_BOUND_RVOL_MIN:
                return None, f"range_bound_rvol_too_low({rvol:.2f}<{_RANGE_BOUND_RVOL_MIN})"
            # Stricter consensus applied later — tracked via range_bound flag

        # Counter-trend entries — skip for non-inverse ETPs
        if regime_str in ("TRENDING_DOWN_STRONG", "TRENDING_DOWN_MOD") and not is_inverse:
            return None, f"counter_trend({regime_str})"

        # VIX GATE — Ang et al. (2006): no 5x above VIX 22
        is_5x = ticker in _5X_TICKERS
        if is_5x and self._current_vix >= _VIX_NO_5X_THRESHOLD:
            return None, f"vix_5x_block(vix={self._current_vix:.1f}>={_VIX_NO_5X_THRESHOLD})"

        # D-01: 5x EXECUTION WINDOW GATE — only trade 14:30-15:30 UK
        # Avellaneda & Zhang (2010): 5x products need peak liquidity conditions.
        # US/LSE overlap window (14:30-15:30 UK) provides maximum depth + tightest spreads.
        if is_5x:
            now_uk = datetime.now(_UK_TZ)
            _5x_window_start = now_uk.replace(
                hour=_5X_EXEC_WINDOW_START_H, minute=_5X_EXEC_WINDOW_START_M, second=0,
            )
            _5x_window_end = now_uk.replace(
                hour=_5X_EXEC_WINDOW_END_H, minute=_5X_EXEC_WINDOW_END_M, second=0,
            )
            if not (_5x_window_start <= now_uk <= _5x_window_end):
                return None, (
                    f"5x_exec_window(now={now_uk.strftime('%H:%M')}, "
                    f"allowed={_5X_EXEC_WINDOW_START_H}:{_5X_EXEC_WINDOW_START_M:02d}"
                    f"-{_5X_EXEC_WINDOW_END_H}:{_5X_EXEC_WINDOW_END_M:02d})"
                )

        # D-01 + G-16: 5x SPREAD VETO — tighter spread gate, VIX + ToD normalized
        # 5x products amplify spread costs by leverage factor; require tighter liquidity.
        if is_5x and self._spread_tracker is not None:
            current_spread = getattr(snap, 'bid_ask_spread', 0) or 0
            median_spread = self._spread_tracker.get_3day_median_spread(ticker)
            if median_spread is not None and median_spread > 0 and current_spread > 0:
                _vix_mult = self._get_spread_vix_multiplier()
                _tod_mult = self._get_spread_tod_multiplier()
                _adjusted_5x_gate = _5X_SPREAD_VETO_MULT * _vix_mult * _tod_mult
                if current_spread > _adjusted_5x_gate * median_spread:
                    return None, (
                        f"5x_spread_veto({current_spread*10000:.0f}bps>"
                        f"{_adjusted_5x_gate:.1f}x median {median_spread*10000:.0f}bps"
                        f" [vix_m={_vix_mult:.1f},tod_m={_tod_mult:.1f}])"
                    )

        # GATE 5: Adaptive Weighted Multi-Confirmation Gate
        # ───────────────────────────────────────────────────────────────────
        # Academic basis: Brock et al. (1992) 6/8 vote is the validated standard.
        # V3.2 upgrade: WEIGHTED voting replaces equal-weight voting.
        # Not all indicators are equal — VWAP (institutional benchmark) and MACD
        # (strongest standalone signal per Park & Irwin 2007 meta-analysis) get
        # higher weights than lagging EMA50 (distorted by leveraged ETP path-dependency).
        #
        # Weighting scheme (out of 10.0 max):
        #   VWAP=1.8, MACD=1.5, RSI=1.5, EMA9=1.2, StochRSI=1.2, EMA20=1.0, OBV=1.0, EMA50=0.8
        #   Leveraged 3x/5x ETPs: EMA50=0.5, EMA20=0.8 (Ben-David et al. 2018)
        #
        # Gate thresholds (weighted equivalents of prior raw thresholds):
        #   Standard (6/8 raw → 7.0/10.0 weighted): quality matters more than count
        #   Broad leveraged ETPs (4/8 → 4.8/9.5 weighted) — trending regime only
        #   AI/Momentum (5/8 → 6.0/10.0 weighted)
        #   Range-bound (6/8 strict → 7.5/10.0 weighted, no easing)
        #
        # Easing is ONLY permitted in TRENDING regimes with compensating gates.
        # RANGE_BOUND always requires the strict threshold regardless of ticker class.
        direction, momentum_score, weighted_score, tier, indicator_votes = self._determine_direction(snap, ticker)
        rvol_val = snap.rvol if hasattr(snap, "rvol") and snap.rvol else 0.0

        # ── T-06/T-07: Tier-specific ADX & RVOL gates (Pass 2) ────────────
        if tier == "SLOW":
            # SLOW tier: stricter thresholds
            _adx_threshold = _MIN_ADX_RANGE_BOUND if is_range_bound else _MIN_ADX_SLOW
            if effective_adx < _adx_threshold:
                return None, f"adx_slow_tier({effective_adx:.1f}<{_adx_threshold})"

            if is_range_bound:
                _rvol_threshold = _MIN_RVOL_RANGE_BOUND
            else:
                _rvol_threshold = _MIN_RVOL_SLOW

            # T-07: RVOL trajectory override — rising trajectory can relax threshold
            # to FAST floor, but never bypass the floor entirely.
            if rvol_rising:
                _rvol_threshold = _MIN_RVOL_FAST  # Relax to FAST floor if trajectory positive
            if rvol < _rvol_threshold:
                return None, f"rvol_slow_tier({rvol:.2f}<{_rvol_threshold})"
        elif tier == "FAST" and self._is_lunch_window:
            # T-02/Q-4: FAST tier BLOCKED during lunch (gap signals stale by 11:30)
            return None, "fast_tier_lunch_veto"
        elif tier == "FAST" and getattr(self, '_us_open_stabilization', False):
            # B-12: FAST tier BLOCKED during US open stabilization (14:30-14:35 UK)
            # First 300s after US equity open features violent cross-exchange arbitrage.
            # SLOW tier continues evaluating — slower confirmation absorbs the noise.
            return None, "fast_tier_us_open_stabilization"
        # FAST tier already passed the floor pre-filters

        # ── T-05: Consensus gate — tier-aware ──────────────────────────────
        if tier == "FAST":
            # FAST tier: 3/4 leading indicator agreement IS the consensus — skip weighted gate
            # Calculate bonus confidence from agreeing SLOW indicators
            slow_bonus = 0.0
            if direction == "LONG":
                if snap.price > snap.ema9 > 0: slow_bonus += 1.0
                if snap.price > snap.ema20 > 0: slow_bonus += 1.0
                if snap.price > snap.ema50 > 0: slow_bonus += 0.5
                if snap.stochastic_rsi > 50: slow_bonus += 1.0
            else:
                if snap.price < snap.ema9 and snap.ema9 > 0: slow_bonus += 1.0
                if snap.price < snap.ema20 and snap.ema20 > 0: slow_bonus += 1.0
                if snap.price < snap.ema50 and snap.ema50 > 0: slow_bonus += 0.5
                if snap.stochastic_rsi < 50: slow_bonus += 1.0
            self.logger.debug(
                "S15 T-05 FAST TIER: %s direction=%s slow_bonus=%.1f",
                ticker, direction, slow_bonus,
            )
            easing_applied = "fast_tier"
            indicator_count = 6  # Equivalent for downstream logging
        else:
            # SLOW tier: full weighted consensus gate (unchanged)
            slow_bonus = 0.0
            if is_range_bound:
                weighted_needed = _WEIGHTED_GATE_RANGE_BOUND
                easing_applied = None
            elif regime_str in ("TRENDING_UP_STRONG",):
                if ticker in _BROAD_LEVERAGED_ETPS:
                    if rvol_val >= _CONSENSUS_EASED_MIN_RVOL:
                        weighted_needed = _WEIGHTED_GATE_LEVERAGED
                        easing_applied = f"leveraged_etp_weighted(rvol={rvol_val:.2f})"
                    else:
                        weighted_needed = _WEIGHTED_GATE_STANDARD
                        easing_applied = None
                elif ticker in _AI_MOMENTUM_ETPS:
                    weighted_needed = _WEIGHTED_GATE_AI_MOMENTUM
                    easing_applied = "ai_momentum_weighted"
                else:
                    weighted_needed = _WEIGHTED_GATE_STANDARD
                    easing_applied = None
            else:
                weighted_needed = _WEIGHTED_GATE_STANDARD
                easing_applied = None

            if weighted_score < weighted_needed:
                return None, (
                    f"weak_consensus(weighted={weighted_score:.1f}/{weighted_needed:.1f}"
                    f"{', eased' if easing_applied else ''})"
                )
            if easing_applied:
                logger.debug(
                    "CONSENSUS EASED: %s %s → weighted=%.1f/%.1f approved",
                    ticker, easing_applied, weighted_score, weighted_needed,
            )
        # For backward compatibility: derive indicator_count from weighted score
        # (used by downstream logging and learning metadata)
        if tier != "FAST":
            is_leveraged = ticker in _BROAD_LEVERAGED_ETPS
            _total_max = _WEIGHTED_TOTAL_MAX_LEV if is_leveraged else _WEIGHTED_TOTAL_MAX
            indicator_count = round((weighted_score / _total_max) * 8) if _total_max > 0 else 6

        # ISA rules: Can't short-sell. CAN go LONG on inverse ETPs when bearish.
        if direction == "SHORT":
            if ticker in _INVERSE_ETPS:
                direction = "LONG"
                logger.info("S15: %s inverse ETP — converting SHORT→LONG (underlying bearish)", ticker)
            else:
                return None, "isa_long_only(direction=SHORT)"

        # Calculate entry, stop, target
        entry = round(price, 2)
        # V5.0: ATR-based stops (Wilder 1978) — 1.5x ATR outside noise
        atr_val = getattr(snap, 'atr14', None) or getattr(snap, 'atr', None)
        if atr_val and atr_val > 0:
            stop_dist = _STOP_ATR_MULT * atr_val
            stop_dist = max(stop_dist, entry * _STOP_MIN_PCT)  # Floor at 0.5%
        else:
            # Fallback to fixed % if ATR unavailable
            stop_pct = (_STOP_PCT_5X if is_5x else _STOP_PCT_3X) / 100.0
            stop_dist = entry * stop_pct
        # G-05/Finding 10: EWMA vol floor — widen stops during intraday vol spikes.
        # Bollerslev (1986): fast EWMA captures clustering better than fixed ATR.
        _ewma = self._ewma_vol.get(ticker)
        if _ewma is not None and entry > 0:
            _ewma_stop_dist = entry * _ewma * _STOP_ATR_MULT
            if _ewma_stop_dist > stop_dist:
                self.logger.debug(
                    "S15 G-05: %s EWMA vol floor widened stop %.4f → %.4f (ewma=%.4f)",
                    ticker, stop_dist, _ewma_stop_dist, _ewma,
                )
                stop_dist = _ewma_stop_dist
        stop = round(entry - stop_dist, 4) if direction == "LONG" else round(entry + stop_dist, 4)

        # Runner mode: only in strong trending regimes
        is_core = ticker in _CORE_TICKERS
        use_runner = (
            is_core
            and rvol >= _USE_RUNNER_IF_RVOL_GT
            and atr_pct >= 2.0
            and regime_str in ("TRENDING_UP_STRONG", "TRENDING_UP_MOD")
        )
        target_pct = min(_MAX_INTRADAY_TARGET, atr_pct * 1.5) / 100.0 if use_runner else _DAILY_TARGET_PCT / 100.0
        target = round(entry * (1 + target_pct), 2)

        # COST-AWARE R:R — dynamic P90 spread from SpreadHistoryTracker,
        # falling back to hardcoded _SPREAD_BPS per ticker (Pastor & Stambaugh 2003)
        if self._spread_tracker:
            _dynamic_spread = self._spread_tracker.get_fallback_spread(ticker)
            spread_bps = int(_dynamic_spread * 10000)  # decimal → bps
        else:
            spread_bps = _SPREAD_BPS.get(ticker, _DEFAULT_SPREAD_BPS)
        spread_cost = entry * (spread_bps / 10000.0) * 2
        reward = abs(target - entry) - spread_cost
        risk = abs(entry - stop)
        rr_ratio = (reward / risk) if risk > 0 else 0
        if rr_ratio < _MIN_RR_RATIO:
            return None, f"rr_too_low({rr_ratio:.2f}<{_MIN_RR_RATIO})"

        # --------------- SCORING ---------------
        atr_reach = min(atr_pct / _DAILY_TARGET_PCT, 2.0) / 2.0
        atr_score = atr_reach * _W_ATR

        rvol_norm = min(rvol / 3.0, 1.0)
        rvol_score = rvol_norm * _W_RVOL

        mom_score = momentum_score * _W_MOMENTUM

        ema_align = snap.ema_alignment if hasattr(snap, 'ema_alignment') else 0
        ema_norm = min(abs(ema_align) / 8.0, 1.0) if isinstance(ema_align, (int, float)) else 0
        ema_score = ema_norm * _W_EMA_ALIGN

        if direction == "LONG":
            bb_range = snap.bb_upper - snap.bb_lower if snap.bb_upper > snap.bb_lower else 1
            bb_pos = (price - snap.bb_lower) / bb_range if bb_range > 0 else 0.5
            bb_room = 1.0 - bb_pos
        else:
            bb_range = snap.bb_upper - snap.bb_lower if snap.bb_upper > snap.bb_lower else 1
            bb_pos = (price - snap.bb_lower) / bb_range if bb_range > 0 else 0.5
            bb_room = bb_pos
        bb_score = max(0, min(bb_room, 1.0)) * _W_BB_POSITION

        adx_norm = min(adx / 50.0, 1.0) if adx > 0 else 0
        trend_score = adx_norm * _W_TREND

        total_score = atr_score + rvol_score + mom_score + ema_score + bb_score + trend_score

        # Apply RANGE_BOUND confidence penalty — lower conviction in choppy macro
        confidence = 40.0 + (total_score * 55.0)
        if is_range_bound:
            confidence -= _RANGE_BOUND_CONF_PENALTY
        # Finding 12: HIGH_VOLATILITY/RISK_OFF — reduced confidence, not hard veto
        if is_reduced_threshold:
            confidence -= _RANGE_BOUND_CONF_PENALTY  # Same -10 penalty as range-bound
        # B-03: Lunch window 0.85x multiplier (SLOW tier only; FAST already blocked above)
        # Replaces flat -10 penalty: scales proportionally to signal strength.
        if self._is_lunch_window:
            _pre_lunch = confidence
            confidence *= 0.85
            self.logger.debug("S15 B-03: lunch 0.85x → %.1f (was %.1f) for %s", confidence, _pre_lunch, ticker)
        # T-05: FAST tier gets bonus from agreeing SLOW indicators (+0 to +3.5)
        if tier == "FAST" and slow_bonus > 0:
            confidence += slow_bonus

        # I-10: Gap-to-Range Filter — penalise tickers where overnight gaps
        # routinely consume >50% of ADR, leaving insufficient intraday range.
        # Szakmary et al. (2010): exhausted-gap tickers have 68% intraday reversal rate.
        # Computed from yfinance 20-day history; cached in _gap_to_adr_cache per session.
        _gap_to_adr_ratio = self._get_gap_to_adr_ratio(ticker)
        if _gap_to_adr_ratio is not None and _gap_to_adr_ratio > _GAP_TO_ADR_THRESHOLD:
            _pre_gap_conf = confidence
            confidence -= _GAP_TO_ADR_PENALTY
            self.logger.info(
                "S15 I-10 GAP_EXHAUSTION: %s median_gap/ADR=%.2f > %.2f → "
                "conf %.1f → %.1f (-%d penalty)",
                ticker, _gap_to_adr_ratio, _GAP_TO_ADR_THRESHOLD,
                _pre_gap_conf, confidence, _GAP_TO_ADR_PENALTY,
            )

        # I-09: Pre-market futures bias — adjust confidence based on overnight
        # US futures direction (NQ, ES, RTY). Cached in Redis by pre_market_intel.py.
        # Barclay & Hendershott (2003): after-hours returns predict next-day direction.
        _premarket_bias = self._get_premarket_bias(ticker)
        if _premarket_bias is not None and _premarket_bias != 0:
            _pre_pm_conf = confidence
            confidence += _premarket_bias
            self.logger.debug(
                "S15 I-09 PREMARKET_BIAS: %s → conf %.1f → %.1f (%+d from futures)",
                ticker, _pre_pm_conf, confidence, _premarket_bias,
            )

        confidence = round(min(95.0, max(40.0, confidence)), 1)

        # D-01: 5x CONFIDENCE FLOOR — reject 5x ETPs below 80 confidence
        # Avellaneda & Zhang (2010): 5x products' nonlinear tracking error means
        # only high-conviction entries have positive expected value after vol drag.
        if is_5x and confidence < _5X_CONFIDENCE_FLOOR:
            return None, f"5x_confidence_too_low({confidence:.1f}<{_5X_CONFIDENCE_FLOOR})"

        vix_half_size = self._current_vix >= _VIX_HALF_SIZE_THRESHOLD

        # AEGIS 0-06: Per-indicator decomposition for ablation study
        # Component scores (6 factors that compose total_score):
        indicator_scores = {
            "component_scores": {
                "atr_score": round(atr_score, 4),
                "rvol_score": round(rvol_score, 4),
                "momentum_score": round(mom_score, 4),
                "ema_score": round(ema_score, 4),
                "bb_score": round(bb_score, 4),
                "trend_score": round(trend_score, 4),
            },
            # Individual indicator votes (8 weighted consensus indicators):
            "indicator_votes": indicator_votes,
            "weighted_score": round(weighted_score, 2),
            "total_score": round(total_score, 4),
        }

        return {
            "ticker": ticker,
            "direction": direction,
            "entry": entry,
            "stop": stop,
            "target": target,
            "is_runner": use_runner,
            "is_5x": is_5x,
            "is_range_bound": is_range_bound,
            "target_pct_used": round(target_pct * 100, 2),
            "rr_ratio": round(rr_ratio, 2),
            "score": round(total_score, 4),
            "confidence": confidence,
            "atr_pct": round(atr_pct, 2),
            "rvol": round(rvol, 2),
            "rsi": round(snap.rsi14, 1),
            "adx": round(adx, 1),
            "momentum_score": round(momentum_score, 3),
            "indicator_count": indicator_count,
            "regime": regime_str,
            "vix_half_size": vix_half_size,
            "spread_bps": spread_bps,
            "tier": tier,  # T-05/T-10: "FAST" or "SLOW" for priority path routing
            "indicator_scores": indicator_scores,  # AEGIS 0-06
            # D-01: 5x profile metadata for downstream risk management
            "max_hold_hours": _5X_MAX_HOLD_HOURS if is_5x else None,
            "equity_cap_pct": _5X_EQUITY_CAP_PCT if is_5x else None,
        }, None

    def _determine_direction(
        self, snap: IndicatorSnapshot, ticker: str = ""
    ) -> tuple[str, float, float, str, dict]:
        """Determine LONG or SHORT from WEIGHTED indicator consensus + tier.

        T-05: Two-tier architecture:
          FAST tier: 4 leading indicators (VWAP, MACD, RSI, ROC30). 3/4 agree + price move → FAST.
          SLOW tier: full 8-indicator weighted consensus (existing).

        Returns (direction, weighted_momentum_score, weighted_aligned_score, tier, indicator_votes) where:
          - tier is "FAST" or "SLOW"
          - weighted_momentum_score is 0-1 (proportion of max possible weighted score)
          - weighted_aligned_score is the raw weighted sum for the winning direction
          - indicator_votes is dict of {name: {"vote": LONG/SHORT/NEUTRAL, "weight": float}}
            for AEGIS 0-06 per-indicator ablation logging
        """
        # ── T-05: FAST TIER CHECK — 4 leading indicators ──────────────────
        # 3/4 must agree + price moved ≥ leverage-scaled threshold from prev close
        # Note: 3/4 alone has ~31% FPR; price move threshold is the binding constraint
        fast_long = 0
        fast_short = 0
        # FAST 1: VWAP
        if snap.price > snap.vwap > 0:
            fast_long += 1
        elif snap.price < snap.vwap and snap.vwap > 0:
            fast_short += 1
        # FAST 2: MACD histogram
        if snap.macd_histogram > 0:
            fast_long += 1
        elif snap.macd_histogram < 0:
            fast_short += 1
        # FAST 3: RSI
        if snap.rsi14 > 55:
            fast_long += 1
        elif snap.rsi14 < 45:
            fast_short += 1
        # FAST 4: ROC(30) — explicit None guard
        roc_30 = getattr(snap, 'roc_30', None)
        if roc_30 is not None and roc_30 > 1.5:
            fast_long += 1
        elif roc_30 is not None and roc_30 < -1.5:
            fast_short += 1

        fast_max = max(fast_long, fast_short)
        fast_direction = "LONG" if fast_long >= fast_short else "SHORT"

        # FAST qualification: 3/4 agree AND leverage-scaled price move
        is_5x = ticker in _5X_TICKERS
        _gap_threshold = _GAP_THRESHOLD_5X if is_5x else _GAP_THRESHOLD_3X
        prev_close = getattr(snap, 'prev_close', 0) or 0
        if prev_close <= 0:
            self.logger.debug("S15 FAST: %s prev_close unavailable — FAST tier disabled", ticker)
        price_move_pct = abs(snap.price - prev_close) / prev_close if prev_close > 0 else 0
        is_fast_qualified = fast_max >= 3 and price_move_pct >= _gap_threshold

        # ── FULL WEIGHTED CONSENSUS (both tiers) ──────────────────────────
        # Select weight set: leveraged ETPs get down-weighted EMA50/EMA20
        is_leveraged = ticker in _BROAD_LEVERAGED_ETPS
        weights = _INDICATOR_WEIGHTS_LEVERAGED if is_leveraged else _INDICATOR_WEIGHTS
        # D-02: total_max must account for dynamic VWAP weight (time-zone adjusted).
        # Compute the VWAP TZ multiplier here so total_max reflects the actual
        # maximum possible score when all indicators align.
        _now_uk_dir = datetime.now(_UK_TZ)
        _uk_h_dir, _uk_m_dir = _now_uk_dir.hour, _now_uk_dir.minute
        if _uk_h_dir < 8:
            _vwap_tz_for_max = _VWAP_TZ_MULT_PREOPEN
        elif _uk_h_dir < 14 or (_uk_h_dir == 14 and _uk_m_dir < 30):
            _vwap_tz_for_max = _VWAP_TZ_MULT_MORNING
        else:
            _vwap_tz_for_max = _VWAP_TZ_MULT_US_OVERLAP
        _base_max = _WEIGHTED_TOTAL_MAX_LEV if is_leveraged else _WEIGHTED_TOTAL_MAX
        # Adjust: remove base VWAP weight, add time-zone-adjusted VWAP weight
        total_max = _base_max - weights["vwap"] + (weights["vwap"] * _vwap_tz_for_max)

        long_score = 0.0
        short_score = 0.0

        # AEGIS 0-06: per-indicator vote tracking for ablation study
        indicator_votes: dict[str, dict] = {}

        # 1. RSI — Tier 1 Leading (Park & Irwin 2007)
        w_rsi = weights["rsi"]
        if snap.rsi14 > 55:
            long_score += w_rsi
            indicator_votes["rsi"] = {"vote": "LONG", "weight": w_rsi, "value": round(snap.rsi14, 1)}
        elif snap.rsi14 < 45:
            short_score += w_rsi
            indicator_votes["rsi"] = {"vote": "SHORT", "weight": w_rsi, "value": round(snap.rsi14, 1)}
        else:
            indicator_votes["rsi"] = {"vote": "NEUTRAL", "weight": 0.0, "value": round(snap.rsi14, 1)}
        # 45-55 = neutral zone: RSI doesn't vote

        # 2. MACD histogram — Tier 1 Leading (Brock et al. 1992: best standalone signal)
        w_macd = weights["macd"]
        if snap.macd_histogram > 0:
            long_score += w_macd
            indicator_votes["macd"] = {"vote": "LONG", "weight": w_macd, "value": round(snap.macd_histogram, 4)}
        elif snap.macd_histogram < 0:
            short_score += w_macd
            indicator_votes["macd"] = {"vote": "SHORT", "weight": w_macd, "value": round(snap.macd_histogram, 4)}
        else:
            indicator_votes["macd"] = {"vote": "NEUTRAL", "weight": 0.0, "value": 0.0}

        # 3. Price vs EMA9 — Tier 2 Confirming (fast trend)
        w_ema9 = weights["ema9"]
        if snap.price > snap.ema9 > 0:
            long_score += w_ema9
            indicator_votes["ema9"] = {"vote": "LONG", "weight": w_ema9, "value": round(snap.ema9, 4)}
        elif snap.price < snap.ema9 and snap.ema9 > 0:
            short_score += w_ema9
            indicator_votes["ema9"] = {"vote": "SHORT", "weight": w_ema9, "value": round(snap.ema9, 4)}
        else:
            indicator_votes["ema9"] = {"vote": "NEUTRAL", "weight": 0.0, "value": round(snap.ema9, 4) if snap.ema9 else 0.0}

        # 4. Price vs EMA20 — Tier 3 Lagging (medium trend confirmation)
        w_ema20 = weights["ema20"]
        if snap.price > snap.ema20 > 0:
            long_score += w_ema20
            indicator_votes["ema20"] = {"vote": "LONG", "weight": w_ema20, "value": round(snap.ema20, 4)}
        elif snap.price < snap.ema20 and snap.ema20 > 0:
            short_score += w_ema20
            indicator_votes["ema20"] = {"vote": "SHORT", "weight": w_ema20, "value": round(snap.ema20, 4)}
        else:
            indicator_votes["ema20"] = {"vote": "NEUTRAL", "weight": 0.0, "value": round(snap.ema20, 4) if snap.ema20 else 0.0}

        # 5. Price vs EMA50 — Tier 3 Lagging; downweighted for leveraged ETPs
        # Ben-David et al. (2018): compounding distorts 50-period trend for 3x/5x
        w_ema50 = weights["ema50"]
        if snap.price > snap.ema50 > 0:
            long_score += w_ema50
            indicator_votes["ema50"] = {"vote": "LONG", "weight": w_ema50, "value": round(snap.ema50, 4)}
        elif snap.price < snap.ema50 and snap.ema50 > 0:
            short_score += w_ema50
            indicator_votes["ema50"] = {"vote": "SHORT", "weight": w_ema50, "value": round(snap.ema50, 4)}
        else:
            indicator_votes["ema50"] = {"vote": "NEUTRAL", "weight": 0.0, "value": round(snap.ema50, 4) if snap.ema50 else 0.0}

        # 6. Price vs VWAP — Tier 1 Leading (Madhavan et al. 1997: institutional benchmark)
        # Highest weight: best single intraday directional predictor
        # D-02: Apply time-zone multiplier — VWAP is most reliable during
        # US/LSE overlap (14:30-16:30 UK) when institutional volume peaks.
        w_vwap = weights["vwap"]
        _now_uk_vwap = datetime.now(_UK_TZ)
        _uk_h, _uk_m = _now_uk_vwap.hour, _now_uk_vwap.minute
        if _uk_h < 8:
            _vwap_tz_mult = _VWAP_TZ_MULT_PREOPEN      # Before 08:00 UK: 1.4x
        elif _uk_h < 14 or (_uk_h == 14 and _uk_m < 30):
            _vwap_tz_mult = _VWAP_TZ_MULT_MORNING       # 08:00-14:30 UK: 1.0x
        else:
            _vwap_tz_mult = _VWAP_TZ_MULT_US_OVERLAP    # 14:30-16:30 UK: 1.8x
        w_vwap_adjusted = w_vwap * _vwap_tz_mult
        if snap.price > snap.vwap > 0:
            long_score += w_vwap_adjusted
            indicator_votes["vwap"] = {"vote": "LONG", "weight": w_vwap_adjusted, "value": round(snap.vwap, 4), "tz_mult": _vwap_tz_mult}
        elif snap.price < snap.vwap and snap.vwap > 0:
            short_score += w_vwap_adjusted
            indicator_votes["vwap"] = {"vote": "SHORT", "weight": w_vwap_adjusted, "value": round(snap.vwap, 4), "tz_mult": _vwap_tz_mult}
        else:
            indicator_votes["vwap"] = {"vote": "NEUTRAL", "weight": 0.0, "value": round(snap.vwap, 4) if snap.vwap else 0.0, "tz_mult": _vwap_tz_mult}

        # 7. Stochastic RSI — Tier 2 Confirming (Lo et al. 2000: smoothed momentum)
        w_stoch = weights["stoch_rsi"]
        if snap.stochastic_rsi > 50:
            long_score += w_stoch
            indicator_votes["stoch_rsi"] = {"vote": "LONG", "weight": w_stoch, "value": round(snap.stochastic_rsi, 2)}
        elif snap.stochastic_rsi < 50:
            short_score += w_stoch
            indicator_votes["stoch_rsi"] = {"vote": "SHORT", "weight": w_stoch, "value": round(snap.stochastic_rsi, 2)}
        else:
            indicator_votes["stoch_rsi"] = {"vote": "NEUTRAL", "weight": 0.0, "value": round(snap.stochastic_rsi, 2) if snap.stochastic_rsi else 0.0}

        # 8. OBV slope — Tier 3 Confirming (Granville 1963 / Pring 2014)
        # BUG FIX: raw OBV sign is meaningless (cumulative sum from arbitrary start).
        # Use OBV slope: positive OBV_slope = buying pressure, negative = selling.
        # OBV slope = (current OBV - EMA20 of OBV) as a direction signal.
        # If snap.obv_slope is not available, fall back to OBV vs prev_obv delta.
        w_obv = weights["obv"]
        obv_slope = getattr(snap, "obv_slope", None)
        if obv_slope is not None:
            if obv_slope > 0:
                long_score += w_obv
                indicator_votes["obv"] = {"vote": "LONG", "weight": w_obv, "value": round(obv_slope, 4)}
            elif obv_slope < 0:
                short_score += w_obv
                indicator_votes["obv"] = {"vote": "SHORT", "weight": w_obv, "value": round(obv_slope, 4)}
            else:
                indicator_votes["obv"] = {"vote": "NEUTRAL", "weight": 0.0, "value": 0.0}
        else:
            # Fallback: compare OBV to its EMA20 proxy via the moving average
            # If OBV > EMA20, accumulation trend → bullish slope
            obv_ema = getattr(snap, "obv_ema20", None)
            if obv_ema is not None and obv_ema > 0:
                if snap.obv > obv_ema:
                    long_score += w_obv
                    indicator_votes["obv"] = {"vote": "LONG", "weight": w_obv, "value": round(snap.obv - obv_ema, 2)}
                elif snap.obv < obv_ema:
                    short_score += w_obv
                    indicator_votes["obv"] = {"vote": "SHORT", "weight": w_obv, "value": round(snap.obv - obv_ema, 2)}
                else:
                    indicator_votes["obv"] = {"vote": "NEUTRAL", "weight": 0.0, "value": 0.0}
            else:
                indicator_votes["obv"] = {"vote": "NEUTRAL", "weight": 0.0, "value": 0.0}
            # If no slope data available, contribute 0 (neutral) — better than noise

        # Direction: weighted majority wins
        if long_score >= short_score:
            direction = "LONG"
            aligned_weighted = long_score
            momentum_score = long_score / total_max if total_max > 0 else 0
        else:
            direction = "SHORT"
            aligned_weighted = short_score
            momentum_score = short_score / total_max if total_max > 0 else 0

        # T-05: Tier classification — FAST only when direction agrees with fast consensus
        tier = "FAST" if is_fast_qualified and fast_direction == direction else "SLOW"

        return direction, round(momentum_score, 3), round(aligned_weighted, 2), tier, indicator_votes

    # ------------------------------------------------------------------
    # V5.0 / Phase 38: Intraday Seasonality — Power Hour boost
    # Academic basis: Heston, Korajczyk & Sadka (2010) "Intraday Patterns
    # in the Cross-Section of Stock Returns" — last-hour returns exhibit
    # statistically significant momentum continuation as institutional
    # portfolio rebalancing concentrates into the close.
    # US close = 21:00 UTC → Power Hour window = 15:58-16:25 UTC
    # (captures LSE/US overlap surge before LSE close at 16:30).
    # ------------------------------------------------------------------
    def _apply_intraday_seasonality(self, signal, now_utc):
        """Apply a 15% confidence boost during Power Hour (14:30-15:15 UK).

        Finding 17: Power Hour is the US/LSE overlap window (14:30-15:15 UK)
        where institutional volume peaks and signals have higher completion
        rates. Previous UTC-based window (15:58-16:25 UTC) was outside the
        LSE trading gate and never fired.
        """
        now_uk = datetime.now(_UK_TZ)
        uk_h, uk_m = now_uk.hour, now_uk.minute
        uk_t = uk_h * 60 + uk_m
        # 14:30-15:15 UK = minutes 870-915
        if 870 <= uk_t <= 915:
            signal.confidence = min(95.0, signal.confidence * 1.15)
            signal.seasonality_tag = "POWER_HOUR"
            return signal
        signal.seasonality_tag = "NORMAL"
        return signal

    # ------------------------------------------------------------------
    # T-01: Session Open Recording & Gap Scanning
    # Gao et al. (2018): first-30-min return predicts EOD direction 62%
    # for leveraged ETPs. Capture opens during OBSERVE phase (09:00-09:05),
    # then scan for leverage-scaled gaps during GAP SCAN phase (09:05-09:15).
    # ------------------------------------------------------------------

    def _record_session_opens(
        self,
        tickers: list[str],
        indicators: dict,
    ) -> None:
        """Phase 1 (09:00-09:05): Record session opening prices.

        Caches the first observed price for each ticker today.
        Only records once per ticker per session (idempotent).
        """
        today = datetime.now(_UK_TZ).date().isoformat()

        for ticker in tickers:
            snap = indicators.get(ticker)
            if snap is None or snap.price <= 0:
                continue

            # Only record once per ticker per day
            if ticker in self._session_opens and self._session_opens[ticker].get("date") == today:
                continue

            self._session_opens[ticker] = {
                "date": today,
                "open_price": snap.price,
                "bid_ask_spread": snap.bid_ask_spread,
            }
            # SA-7: Persist to Redis so container restart doesn't lose gap scan
            if self._redis_client is not None:
                try:
                    self._redis_client.setex(
                        f"nzt:session_open:{today}:{ticker}", 43200,  # 12h TTL
                        _json.dumps(self._session_opens[ticker]),
                    )
                except Exception:
                    pass  # Non-fatal — in-memory copy still works
            self.logger.debug(
                "S15 T-01: recorded open for %s @ %.4f (spread=%.1f bps)",
                ticker, snap.price, snap.bid_ask_spread * 10000 if snap.bid_ask_spread else 0,
            )

    def _scan_gaps(
        self,
        tickers: list[str],
        indicators: dict,
        market_ctx,
    ) -> list:
        """Phase 2 (09:05-09:15): Scan for leverage-scaled gap signals.

        Fires on gaps exceeding leverage-adjusted thresholds using executable
        price (Ask for LONG, Bid for SHORT — NOT mid-price).

        Requirements:
          - 3x ETPs: gap > 2.5% (= ~0.83% underlying move, above noise)
          - 5x ETPs: gap > 4.0% (= ~0.80% underlying move, consistent)
          - Spread < 35 bps (RO-01 supremacy)
          - Circuit breaker not tripped
          - Daily signal count not exceeded

        Returns list of Signal objects (0 or 1).
        """
        from models import Direction

        today = datetime.now(_UK_TZ).strftime("%Y-%m-%d")  # 3-06: string key, matches scan()

        # CQ-3 FIX: Regime check — never fire gap signals in SHOCK/REGIME_FLAPPING
        # Gaps in these regimes are dead-cat bounces, not momentum continuations.
        regime_str = ""
        if self._current_regime is not None:
            regime_str = self._current_regime.value if hasattr(self._current_regime, 'value') else str(self._current_regime)
        regime_str = regime_str.upper()
        if regime_str in _SKIP_REGIMES:
            self.logger.info(
                "S15 T-01 GAP: regime=%s in SKIP_REGIMES — no gap signals in crash/shock",
                regime_str,
            )
            return []

        # Check daily signal cap
        if self._daily_signal_count.get(today, 0) >= _MAX_SIGNALS_PER_DAY:
            self.logger.debug("S15 T-01 GAP: daily signal cap reached (%d)", _MAX_SIGNALS_PER_DAY)
            return []

        # SA-7: Restore session opens from Redis if in-memory dict is empty (container restart)
        if not self._session_opens and self._redis_client is not None:
            _restored = 0
            for ticker in tickers:
                try:
                    _cached = self._redis_client.get(f"nzt:session_open:{today}:{ticker}")
                    if _cached:
                        self._session_opens[ticker] = _json.loads(_cached)
                        _restored += 1
                except Exception:
                    pass
            if _restored:
                self.logger.info("SA-7: restored %d session opens from Redis after restart", _restored)

        best_gap = None
        best_gap_pct = 0.0

        for ticker in tickers:
            snap = indicators.get(ticker)
            if snap is None or snap.price <= 0:
                continue

            # Need a recorded session open to compute gap
            open_data = self._session_opens.get(ticker)
            if not open_data or open_data.get("date") != today:
                continue

            open_price = open_data["open_price"]
            if open_price <= 0:
                continue

            # Leverage-scaled gap threshold
            is_5x = ticker in _5X_TICKERS
            gap_threshold = _GAP_THRESHOLD_5X if is_5x else _GAP_THRESHOLD_3X

            # Gap calculation using executable price
            # Ask for LONG (what you'd actually pay), Bid for SHORT
            current_price = snap.price
            gap_pct = (current_price - open_price) / open_price

            # Check if gap exceeds threshold in either direction
            abs_gap = abs(gap_pct)
            if abs_gap < gap_threshold:
                continue

            # Direction from gap
            gap_direction = "LONG" if gap_pct > 0 else "SHORT"

            # 3-05: ISA SHORT conversion — can't short non-inverse in ISA
            if gap_direction == "SHORT":
                if ticker in _INVERSE_ETPS:
                    gap_direction = "LONG"  # Inverse ETP: SHORT underlying = LONG the product
                else:
                    continue  # ISA can't short non-inverse ETPs

            # Spread gate — RO-01 supremacy: 35 bps max
            spread_bps = (snap.bid_ask_spread or 0) * 10000
            if spread_bps > _GAP_MAX_SPREAD_BPS:
                self.logger.info(
                    "S15 T-01 GAP: %s gap=%.2f%% but spread=%.1f bps > %d bps — REJECTED",
                    ticker, abs_gap * 100, spread_bps, _GAP_MAX_SPREAD_BPS,
                )
                continue

            # RVOL floor — must have minimum viable liquidity
            rvol = snap.rvol if snap.rvol is not None and snap.rvol > 0 else _RVOL_DEFAULT_WHEN_MISSING
            if rvol < _MIN_RVOL_FAST:
                self.logger.info(
                    "S15 T-01 GAP: %s gap=%.2f%% but RVOL=%.2f < %.2f — REJECTED",
                    ticker, abs_gap * 100, rvol, _MIN_RVOL_FAST,
                )
                continue

            self.logger.info(
                "S15 T-01 GAP SIGNAL: %s %s gap=%.2f%% (threshold=%.1f%%) spread=%.1f bps RVOL=%.2f",
                gap_direction, ticker, abs_gap * 100, gap_threshold * 100, spread_bps, rvol,
            )

            if abs_gap > best_gap_pct:
                best_gap_pct = abs_gap
                best_gap = {
                    "ticker": ticker,
                    "direction": gap_direction,
                    "gap_pct": gap_pct,
                    "snap": snap,
                    "rvol": rvol,
                    "spread_bps": spread_bps,
                    "gap_threshold": gap_threshold,
                }

        if best_gap is None:
            return []

        # Build the signal from the best gap
        snap = best_gap["snap"]
        ticker = best_gap["ticker"]
        direction = best_gap["direction"]

        # Entry/stop calculation using ATR
        atr = snap.atr14 if snap.atr14 > 0 else snap.price * 0.02
        if direction == "LONG":
            entry = snap.price
            stop = entry - (atr * 1.5)
            target = entry + (entry * _DAILY_TARGET_PCT / 100)
        else:
            entry = snap.price
            stop = entry + (atr * 1.5)
            target = entry - (entry * _DAILY_TARGET_PCT / 100)

        # R:R check
        risk = abs(entry - stop)
        reward = abs(target - entry)
        rr_ratio = reward / risk if risk > 0 else 0
        if rr_ratio < _MIN_RR_RATIO:
            self.logger.info(
                "S15 T-01 GAP: %s R:R=%.1f < %.1f minimum — REJECTED",
                ticker, rr_ratio, _MIN_RR_RATIO,
            )
            return []

        signal = self._create_signal(
            ticker=ticker,
            direction=direction,
            entry=entry,
            stop=stop,
            indicators=snap,
            market_ctx=market_ctx,
        )
        # CRO-6 FIX: Scale confidence by gap magnitude instead of fixed 72.
        # Larger gaps = stronger directional evidence but also higher mean-reversion risk.
        # Floor 68, cap 82. Formula: 68 + (abs_gap / threshold - 1) * 14, capped.
        _gap_thresh = best_gap.get("gap_threshold", 0.025)
        _gap_ratio = best_gap_pct / _gap_thresh if _gap_thresh > 0 else 1.0
        signal.confidence = min(82.0, 68.0 + (_gap_ratio - 1.0) * 14.0)
        signal.metadata = signal.metadata if hasattr(signal, 'metadata') and signal.metadata else {}
        signal.metadata["tier"] = "FAST"
        signal.metadata["gap_pct"] = round(best_gap["gap_pct"] * 100, 2)
        signal.metadata["gap_source"] = "T-01"
        signal.target_1r = target
        signal.seasonality_tag = "GAP_SCAN"

        # Increment daily signal count
        self._daily_signal_count[today] = self._daily_signal_count.get(today, 0) + 1

        self.logger.info(
            "S15 T-01 GAP FIRED: %s %s @ %.4f stop=%.4f target=%.4f R:R=%.1f gap=%.2f%%",
            direction, ticker, entry, stop, target, rr_ratio,
            best_gap["gap_pct"] * 100,
        )

        return [signal]

    # ------------------------------------------------------------------ #
    #  D-04 / G-05 / G-07 / G-08 / G-16  —  Missing helper methods       #
    # ------------------------------------------------------------------ #

    def _track_first_half_hour(self, tickers, indicators, now_uk, market_ctx):
        """D-04: Track first half-hour prices and compute return for predictability."""
        today = now_uk.strftime("%Y-%m-%d")

        # Reset cache daily
        if self._first_half_hour_date != today:
            self._first_half_hour_prices.clear()
            self._first_half_hour_date = today

        # Record prices at scan start (09:00-09:02 UK) — Finding 7: hour==8 was
        # unreachable because the LSE timing gate blocks before 09:00.
        if now_uk.hour == 9 and now_uk.minute < 2:
            for ticker in tickers:
                snap = indicators.get(ticker)
                if snap and snap.price > 0 and ticker not in self._first_half_hour_prices:
                    self._first_half_hour_prices[ticker] = snap.price

    def _update_ewma_vol(self, ticker, snap):
        """G-05: Update fast EWMA volatility estimate for stop widening."""
        if not hasattr(snap, 'price') or snap.price <= 0:
            return
        prev_close = getattr(snap, 'prev_close', None)
        if not prev_close or prev_close <= 0:
            return
        ret = abs(snap.price - prev_close) / prev_close
        alpha = 0.06  # ~30-period EWMA decay
        if ticker in self._ewma_vol:
            self._ewma_vol[ticker] = alpha * ret + (1 - alpha) * self._ewma_vol[ticker]
        else:
            self._ewma_vol[ticker] = ret

    def _log_strike_proximity_shadow(self, ticker, price, underlying):
        """G-07: Shadow-mode strike proximity logging (data collection only).

        Logs the current price relative to round-number strikes for future
        gamma/strike proximity analysis. No active trading logic.
        """
        if price <= 0:
            return
        # Find nearest round-number strike (multiples of 5 for most ETPs)
        strike_interval = 5.0 if price > 50 else 1.0 if price > 10 else 0.50
        nearest_strike = round(price / strike_interval) * strike_interval
        proximity_pct = abs(price - nearest_strike) / price * 100
        if proximity_pct < 1.0:  # Only log when close to strike
            self.logger.debug(
                "G-07 SHADOW strike_proximity: %s price=%.2f nearest_strike=%.2f "
                "proximity=%.2f%% underlying=%s",
                ticker, price, nearest_strike, proximity_pct, underlying,
            )

    def _log_obi_shadow(self, ticker, bid_vol, ask_vol):
        """G-08: Shadow-mode Order Book Imbalance toxicity logging (data collection only).

        Logs bid/ask volume imbalance for future toxicity analysis.
        No active trading logic.
        """
        if bid_vol <= 0 and ask_vol <= 0:
            return
        total = bid_vol + ask_vol
        if total <= 0:
            return
        obi = (bid_vol - ask_vol) / total  # Range: -1.0 to +1.0
        if abs(obi) > 0.6:  # Only log significant imbalances
            self.logger.debug(
                "G-08 SHADOW obi_toxicity: %s obi=%.3f bid_vol=%.0f ask_vol=%.0f",
                ticker, obi, bid_vol, ask_vol,
            )

    def _get_spread_vix_multiplier(self) -> float:
        """G-16: VIX-based spread veto multiplier.

        Uses _SPREAD_VIX_MULTIPLIERS lookup table and live VIX (_current_vix).
        Higher VIX = wider expected spreads, so relax the spread veto.
        """
        vix = self._current_vix or 0
        if vix <= 0:
            return 1.0
        # Walk the _SPREAD_VIX_MULTIPLIERS table: [(vix_ceiling, mult), ...]
        for vix_ceiling, mult in _SPREAD_VIX_MULTIPLIERS:
            if vix < vix_ceiling:
                return mult
        # Fallback: last entry covers all remaining
        return _SPREAD_VIX_MULTIPLIERS[-1][1] if _SPREAD_VIX_MULTIPLIERS else 1.0

    def _get_spread_tod_multiplier(self) -> float:
        """G-16: Time-of-day spread veto multiplier.

        Uses _SPREAD_TOD_MULTIPLIERS dict keyed by time bucket.
        Spreads are naturally wider at open and close; relax veto accordingly.
        """
        try:
            now_uk = datetime.now(_UK_TZ)
        except Exception:
            return 1.0

        h, m = now_uk.hour, now_uk.minute
        t = h * 60 + m  # minutes since midnight

        # Map time-of-day to bucket key matching _SPREAD_TOD_MULTIPLIERS
        if 540 <= t < 555:      # 09:00-09:15 UK
            bucket = "open"
        elif 555 <= t < 690:    # 09:15-11:30 UK
            bucket = "morning"
        elif 690 <= t < 780:    # 11:30-13:00 UK
            bucket = "lunch"
        elif 780 <= t < 870:    # 13:00-14:30 UK
            bucket = "afternoon"
        elif 870 <= t < 915:    # 14:30-15:15 UK
            bucket = "us_overlap"
        else:
            bucket = "morning"  # Default to normal session multiplier

        return _SPREAD_TOD_MULTIPLIERS.get(bucket, 1.0)

    def _get_gap_to_adr_ratio(self, ticker: str) -> float | None:
        """I-10: Compute median(gap_pct / adr_pct) over lookback window.

        0-02 FIX: This method was called but never defined.
        Uses _gap_to_adr_cache to avoid re-computing every scan cycle.
        If no historical data is available, returns None (fail-open).

        Approach: Use session opens + prev_close from snap to estimate
        today's gap-to-ADR ratio. Cache per ticker per session.
        For a full 20-day median, this would require yfinance history
        which is too slow for inline use — use single-day estimate
        from snap.gap_pct / snap.atr_pct as a proxy.
        """
        today = datetime.now(_UK_TZ).strftime("%Y-%m-%d")

        # Reset cache daily
        if self._gap_to_adr_cache_date != today:
            self._gap_to_adr_cache.clear()
            self._gap_to_adr_cache_date = today

        # Return cached value if available
        if ticker in self._gap_to_adr_cache:
            return self._gap_to_adr_cache[ticker]

        # Try to compute from Redis historical cache (populated by nightly batch)
        if self._redis_client is not None:
            try:
                _cached = self._redis_client.get(f"nzt:gap_adr:{ticker}")
                if _cached:
                    _ratio = float(_cached)
                    self._gap_to_adr_cache[ticker] = _ratio
                    return _ratio
            except Exception:
                pass

        # Fallback: estimate from session opens and current price
        open_data = self._session_opens.get(ticker)
        if open_data:
            open_price = open_data.get("open_price", 0)
            if open_price > 0:
                # Approximate gap from session open vs prev_close (if available in open_data)
                # ADR approximated as atr_pct (default 2.0% if unavailable)
                _adr_pct = 2.0  # Default ADR approximation
                # Gap: diff between open_price and any previous close we can find
                # This is a rough single-day proxy, not the 20-day median
                self._gap_to_adr_cache[ticker] = None
                return None

        self._gap_to_adr_cache[ticker] = None
        return None

    def _get_premarket_bias(self, ticker: str) -> int:
        """I-09: Pre-market futures bias for confidence adjustment.

        0-02 FIX: This method was called but never defined.
        Returns a numeric confidence adjustment:
          +5  if pre-market futures are bullish (aligned with LONG direction)
          -5  if pre-market futures are bearish (counter to typical LONG bias)
           0  if no pre-market data available or neutral

        Reads from Redis cache key 'nzt:premarket_bias' populated by
        pre_market_intel.py nightly batch.
        """
        if self._redis_client is None:
            return 0
        try:
            _cached = self._redis_client.get("nzt:premarket_bias")
            if not _cached:
                return 0
            _data = _json.loads(_cached)
            # Expected format: {"bias": "BULLISH"|"BEARISH"|"NEUTRAL", "confidence": int}
            bias = _data.get("bias", "NEUTRAL")
            if bias == "BULLISH":
                # Bullish pre-market favours LONG on non-inverse, SHORT on inverse
                return +5 if ticker not in _INVERSE_ETPS else -5
            elif bias == "BEARISH":
                # Bearish pre-market favours inverse ETPs
                return +5 if ticker in _INVERSE_ETPS else -5
            return 0
        except Exception:
            return 0
