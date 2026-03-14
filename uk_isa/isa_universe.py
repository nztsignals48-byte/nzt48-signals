"""
NZT-48 V8.0 -- ISA Universe Registry (SINGLE SOURCE OF TRUTH)
===============================================================
Canonical registry for ALL ISA ticker metadata.  Every other module
(strategies, cost models, risk officers, PDF reports) MUST import from
here.  Never define _ISA_UNIVERSE, ticker lists, leverage maps, or
spread tables locally.

Resolves V8.0 audit contradictions:
  C-06  Phantom tickers in settings.yaml  -- WARNING below
  C-07  Hardcoded slippage                -- delegated to execution/cost_model.py
  C-08  Missing leverage guards           -- get_leverage_factor() + per-ticker field
  C-09  Overnight kill logic              -- overnight_kill + must_close_by_session_end()
  C-10  Universe mismatch                 -- FROZEN_TICKERS frozenset

Exports:
  - TICKER_REGISTRY:       dict[str, TickerEntry]  -- per-ticker metadata
  - CORE_UNIVERSE:         list[str]   -- 12 active T212 ISA tickers
  - EXTENDED_UNIVERSE:     list[str]   -- 22 tradable tickers (core + research)
  - SECTOR_RADAR_UNIVERSE: list[str]   -- monitoring-only tickers
  - FULL_SCAN_UNIVERSE:    list[str]   -- tradable + monitoring
  - INTEL_UNIVERSE:        list[str]   -- context instruments (not tradable)
  - FROZEN_TICKERS:        frozenset   -- immutable runtime copy of CORE_UNIVERSE
  - ACTIVE_TICKERS:        frozenset   -- alias for FROZEN_TICKERS (S15 uses this)
  - ISA_FACTOR_GROUPS      dict
  - EXPECTED_PRICE_RANGES  dict
  - LEVERAGE_MAP           dict
  - CORRELATION_GROUPS     dict
  - UNDERLYING_INDEX       dict
  - INVERSE_PAIRS          dict
  - FIVE_X_TICKERS         set
  - TICKER_NAMES           dict
  - SESSION_CONFIG         dict
  - METRIC_DEFINITIONS     dict
  - T212_FX_FEE_BPS        float
  - T212_COMMISSION_BPS    float
  - T212_USD_DENOMINATED   set

Functions:
  - get_leverage_factor(ticker) -> int            (C-08)
  - must_close_by_session_end(ticker) -> bool     (C-09)
  - get_factor_group(ticker) -> str
  - get_net_return(gross_pct, ticker) -> float
  - is_short(ticker) -> bool
  - get_leverage(ticker) -> float
  - get_abs_leverage(ticker) -> float

Import this module everywhere instead of defining _ISA_UNIVERSE locally.

WARNING  ---------------------------------------------------------------
  settings.yaml contains a ``bot_a_universe`` section with phantom
  tickers that do NOT exist in this registry (e.g. SOXS.L, SOXL.L,
  MAG7.L, MG3S.L, MAGS.L, 3MG7.L, TECS.L, 3MSF.L, 3AMZ.L, 3GOL.L
  used as "Alphabet", 3USL.L, AMDS.L, AVG3.L, 3CRM.L, SPXL.L,
  QQS5.L, SP5S.L, etc.).

  Those YAML entries are DEAD.  They are NOT loaded, NOT scanned, and
  NOT tradable.  This file (uk_isa/isa_universe.py) is the SINGLE
  SOURCE OF TRUTH.  Any ticker not defined in TICKER_REGISTRY below
  does not exist in the NZT-48 universe.

  If you need to add a new ticker, add it to TICKER_REGISTRY first,
  then update every downstream dict (LEVERAGE_MAP, EXPECTED_PRICE_RANGES,
  TICKER_NAMES, etc.).  NEVER add tickers to settings.yaml alone.
  -----------------------------------------------------------------------
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# C-08 / C-09: Per-ticker metadata dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TickerEntry:
    """Immutable metadata for a single ISA-eligible ETP.

    Attributes
    ----------
    ticker : str
        Yahoo Finance ticker symbol (e.g. ``"QQQ3.L"``).
    name : str
        Human-readable display name for PDF reports.
    leverage_factor : int
        Unsigned nominal leverage multiplier (2, 3, or 5).
        Sign is determined by ``direction``.
    direction : str
        ``"LONG"`` or ``"SHORT"`` (inverse ETP).
    overnight_kill : bool
        If ``True``, position MUST be closed before session end.
        Set ``True`` for all 5x ETPs (extreme overnight gap risk).
    tier : str
        ``"CORE"`` -- active T212 ISA (S15 scans these).
        ``"EXTENDED"`` -- research universe, tradable.
        ``"SECTOR_RADAR"`` -- monitoring only (WATCH max).
    provider : str
        ETP issuer (Leverage Shares, WisdomTree, GraniteShares).
    underlying : str
        Underlying index or stock that the ETP tracks.
    stamp_duty_exempt : bool
        I-05: ``True`` if the product is exempt from UK stamp duty.
        All LSE-listed ETPs/ETCs are generally exempt as they are
        exchange-traded products (not ordinary shares).
        Any ETP with uncertain status MUST be excluded until verified.
    """

    ticker: str
    name: str
    leverage_factor: int
    direction: str           # "LONG" or "SHORT"
    overnight_kill: bool     # C-09: True => mandatory session-end close
    tier: str                # "CORE", "EXTENDED", "SECTOR_RADAR"
    provider: str
    underlying: str
    stamp_duty_exempt: bool  # I-05: Verified stamp duty exemption status


# ---------------------------------------------------------------------------
# TICKER REGISTRY -- the authoritative per-ticker metadata table
# ---------------------------------------------------------------------------

TICKER_REGISTRY: dict[str, TickerEntry] = {e.ticker: e for e in [
    # ===== CORE UNIVERSE (12) -- active T212 ISA tickers (S15 scans these) =====
    # I-05: stamp_duty_exempt=True — Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("QQQ3.L",  "Nasdaq 100 3x Long",      3, "LONG",  False, "CORE", "Leverage Shares", "Nasdaq 100",          True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("3LUS.L",  "S&P 500 3x Long",         3, "LONG",  False, "CORE", "WisdomTree",      "S&P 500",             True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("3SEM.L",  "Semiconductors 3x Long",   3, "LONG",  False, "CORE", "WisdomTree",      "PHLX Semiconductor",  True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("GPT3.L",  "AI / GPT 3x Long",        3, "LONG",  False, "CORE", "Leverage Shares", "Solactive US AI",     True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("NVD3.L",  "NVIDIA 3x Long",           3, "LONG",  False, "CORE", "Leverage Shares", "NVIDIA",              True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("TSL3.L",  "Tesla 3x Long",            3, "LONG",  False, "CORE", "Leverage Shares", "Tesla",               True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("TSM3.L",  "TSMC 3x Long",             3, "LONG",  False, "CORE", "Leverage Shares", "TSMC",                True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("MU2.L",   "Micron 2x Long",           2, "LONG",  False, "CORE", "Leverage Shares", "Micron Technology",   True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("QQQS.L",  "Nasdaq 100 3x Short",      3, "SHORT", False, "CORE", "Leverage Shares", "Nasdaq 100",          True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("3USS.L",  "S&P 500 3x Short",         3, "SHORT", False, "CORE", "WisdomTree",      "S&P 500",             True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("QQQ5.L",  "Nasdaq 100 5x Long",       5, "LONG",  True,  "CORE", "Leverage Shares", "Nasdaq 100",          True),  # C-09: overnight_kill=True; Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("SP5L.L",  "S&P 500 5x Long",          5, "LONG",  True,  "CORE", "Leverage Shares", "S&P 500",             True),  # C-09: overnight_kill=True; Verified: LSE-listed ETP, stamp duty exempt

    # ===== EXTENDED UNIVERSE (10 extra) -- research universe, tradable =====
    TickerEntry("AMD3.L",  "AMD 3x Long",              3, "LONG",  False, "EXTENDED", "Leverage Shares", "AMD",              True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("ARM3.L",  "ARM 3x Long",              3, "LONG",  False, "EXTENDED", "Leverage Shares", "ARM Holdings",     True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("NVDS.L",  "NVIDIA 3x Short",          3, "SHORT", False, "EXTENDED", "Leverage Shares", "NVIDIA",            True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("TSLS.L",  "Tesla 3x Short",           3, "SHORT", False, "EXTENDED", "Leverage Shares", "Tesla",             True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("3LDE.L",  "DAX 3x Long",              3, "LONG",  False, "EXTENDED", "WisdomTree",      "DAX",              True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("3LEU.L",  "Euro Stoxx 50 3x Long",    3, "LONG",  False, "EXTENDED", "WisdomTree",      "Euro Stoxx 50",    True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("3GOL.L",  "Gold 3x Long",             3, "LONG",  False, "EXTENDED", "WisdomTree",      "Gold",             True),  # Verified: LSE-listed ETC, stamp duty exempt
    TickerEntry("3SIL.L",  "Silver 3x Long",           3, "LONG",  False, "EXTENDED", "WisdomTree",      "Silver",           True),  # Verified: LSE-listed ETC, stamp duty exempt
    TickerEntry("3OIL.L",  "Oil 3x Long",              3, "LONG",  False, "EXTENDED", "WisdomTree",      "Crude Oil",        True),  # Verified: LSE-listed ETC, stamp duty exempt
    TickerEntry("LLY3.L",  "Eli Lilly 3x Long",        3, "LONG",  False, "EXTENDED", "Leverage Shares", "Eli Lilly",        True),  # Verified: LSE-listed ETP, stamp duty exempt

    # ===== SECTOR RADAR (13) -- monitoring only, never TRADE lane =====
    TickerEntry("3LHC.L",  "Healthcare 3x Long",       3, "LONG",  False, "SECTOR_RADAR", "WisdomTree",      "Healthcare",      True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("BAC3.L",  "Bank of America 3x Long",  3, "LONG",  False, "SECTOR_RADAR", "Leverage Shares", "Bank of America",  True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("GS3.L",   "Goldman Sachs 3x Long",    3, "LONG",  False, "SECTOR_RADAR", "Leverage Shares", "Goldman Sachs",    True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("3LEN.L",  "Energy 3x Long",           3, "LONG",  False, "SECTOR_RADAR", "WisdomTree",      "Energy",           True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("XOM3.L",  "ExxonMobil 3x Long",       3, "LONG",  False, "SECTOR_RADAR", "Leverage Shares", "ExxonMobil",       True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("COIN3.L", "Coinbase 3x Long",         3, "LONG",  False, "SECTOR_RADAR", "Leverage Shares", "Coinbase",         True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("MSTRL.L", "MicroStrategy 3x Long",    3, "LONG",  False, "SECTOR_RADAR", "Leverage Shares", "MicroStrategy",    True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("PLTR3.L", "Palantir 3x Long",         3, "LONG",  False, "SECTOR_RADAR", "Leverage Shares", "Palantir",         True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("AVGO3.L", "Broadcom 3x Long",         3, "LONG",  False, "SECTOR_RADAR", "Leverage Shares", "Broadcom",         True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("MFAS.L",  "Meta 3x Long",             3, "LONG",  False, "SECTOR_RADAR", "Leverage Shares", "Meta Platforms",   True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("MSFL.L",  "Microsoft 3x Long",        3, "LONG",  False, "SECTOR_RADAR", "Leverage Shares", "Microsoft",        True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("GOOGL3.L","Alphabet 3x Long",         3, "LONG",  False, "SECTOR_RADAR", "Leverage Shares", "Alphabet",         True),  # Verified: LSE-listed ETP, stamp duty exempt
    TickerEntry("AAPLL.L", "Apple 3x Long",            3, "LONG",  False, "SECTOR_RADAR", "Leverage Shares", "Apple",            True),  # Verified: LSE-listed ETP, stamp duty exempt
]}


# ---------------------------------------------------------------------------
# DERIVED UNIVERSE LISTS -- computed from TICKER_REGISTRY (never edit directly)
# ---------------------------------------------------------------------------

CORE_UNIVERSE: list[str] = [
    t for t, e in TICKER_REGISTRY.items() if e.tier == "CORE"
]

EXTENDED_UNIVERSE: list[str] = [
    t for t, e in TICKER_REGISTRY.items() if e.tier in ("CORE", "EXTENDED")
]

# Convenience alias
ALL_UNIVERSE: list[str] = list(dict.fromkeys(EXTENDED_UNIVERSE))

SECTOR_RADAR_UNIVERSE: list[str] = [
    t for t, e in TICKER_REGISTRY.items() if e.tier == "SECTOR_RADAR"
]

# Full scan universe: EXTENDED (tradable) + SECTOR_RADAR (monitoring)
# Deduplicated, preserving insertion order.
FULL_SCAN_UNIVERSE: list[str] = list(dict.fromkeys(
    EXTENDED_UNIVERSE + SECTOR_RADAR_UNIVERSE
))

# ---------------------------------------------------------------------------
# C-10: FROZEN_TICKERS -- immutable runtime copy of CORE_UNIVERSE
# S15 daily_target.py uses this.  Cannot be modified at runtime.
# ---------------------------------------------------------------------------

FROZEN_TICKERS: frozenset[str] = frozenset(CORE_UNIVERSE)

# Alias that S15 and other strategies can import directly.
# Identical to FROZEN_TICKERS.  The name makes intent explicit at call sites.
ACTIVE_TICKERS: frozenset[str] = FROZEN_TICKERS


# ---------------------------------------------------------------------------
# INTEL UNIVERSE -- context instruments for monitoring, NOT tradable in ISA
# These generate Intel Cards and WATCH-INTEL signals only.
# Never allowed to appear as TRADE signals or mix into core play list.
# ---------------------------------------------------------------------------

INTEL_UNIVERSE: list[str] = [
    # US benchmark ETFs (context for leveraged ETP moves)
    "QQQ",      # Nasdaq 100 ETF (unleveraged reference)
    "SPY",      # S&P 500 ETF
    "SMH",      # VanEck Semiconductor ETF
    "SOXX",     # iShares Semiconductor ETF
    # Volatility
    "^VIX",     # CBOE VIX
    # Bonds / rates
    "TLT",      # 20+ Year Treasury Bond ETF
    # FX / Commodities
    "GLD",      # Gold ETF
    "USO",      # Oil ETF
    "DX-Y.NYB", # US Dollar Index
    # Key single names (underlying for leveraged ETPs)
    "NVDA",     # NVIDIA (NVD3.L underlying)
    "TSLA",     # Tesla (TSL3.L underlying)
    "TSM",      # TSMC (TSM3.L underlying)
    "MU",       # Micron (MU2.L underlying)
    "AMD",      # AMD (AMD3.L underlying)
]

# ---------------------------------------------------------------------------
# ISA FACTOR GROUPS
# ---------------------------------------------------------------------------

ISA_FACTOR_GROUPS: dict[str, list[str]] = {
    "nasdaq_beta_long":    ["QQQ3.L", "QQQ5.L", "3LUS.L", "SP5L.L"],
    "nasdaq_beta_short":   ["QQQS.L", "3USS.L"],
    "semiconductors":      ["3SEM.L", "TSM3.L", "MU2.L", "AMD3.L", "AVGO3.L"],
    "ev_tech":             ["TSL3.L", "TSLS.L"],
    "ai_gpt":              ["GPT3.L", "NVD3.L", "ARM3.L", "PLTR3.L", "MFAS.L", "MSFL.L", "GOOGL3.L"],
    "eu_broad":            ["3LDE.L", "3LEU.L"],
    "commodities":         ["3GOL.L", "3SIL.L", "3OIL.L"],
    "healthcare":          ["3LHC.L", "LLY3.L"],
    "financials":          ["BAC3.L", "GS3.L"],
    "energy":              ["3LEN.L", "XOM3.L"],
    "crypto_tech":         ["COIN3.L", "MSTRL.L"],
    "mega_tech":           ["AAPLL.L", "MSFL.L", "GOOGL3.L", "MFAS.L"],
    "single_stock_long":   ["NVD3.L", "AMD3.L", "ARM3.L", "TSM3.L", "MU2.L", "GPT3.L", "LLY3.L"],
    "single_stock_short":  ["NVDS.L", "TSLS.L"],
}

# ---------------------------------------------------------------------------
# EXPECTED PRICE RANGES (GBP, actual trading price)
# Used by DataHealthGate to detect pence-vs-pounds mis-scaling.
# Format: ticker -> (min_gbp, max_gbp)
# ---------------------------------------------------------------------------

EXPECTED_PRICE_RANGES: dict[str, tuple[float, float]] = {
    "QQQ3.L":  (1.0,   200.0),
    "QQQ5.L":  (0.5,   100.0),
    "SP5L.L":  (1.0,   150.0),
    "3LUS.L":  (1.0,   200.0),
    "QQQS.L":  (0.5,    80.0),
    "3USS.L":  (0.5,    60.0),
    "3SEM.L":  (0.5,    50.0),
    "GPT3.L":  (0.5,    80.0),
    "NVD3.L":  (0.5,   100.0),
    "TSL3.L":  (0.1,    80.0),
    "TSM3.L":  (0.5,    60.0),
    "MU2.L":   (0.5,    50.0),
    "AMD3.L":  (0.5,   100.0),
    "ARM3.L":  (0.5,    80.0),
    "NVDS.L":  (0.5,    60.0),
    "TSLS.L":  (0.5,    60.0),
    "3LDE.L":  (1.0,   100.0),
    "3LEU.L":  (1.0,    80.0),
    "3GOL.L":  (1.0,   100.0),
    "3SIL.L":  (0.5,    50.0),
    "3OIL.L":  (0.5,    50.0),
    "LLY3.L":  (1.0,   100.0),
    # --- SECTOR RADAR ---
    "3LHC.L":  (0.5,    80.0),
    "BAC3.L":  (0.5,    80.0),
    "GS3.L":   (0.5,    80.0),
    "3LEN.L":  (0.5,    80.0),
    "XOM3.L":  (0.5,    80.0),
    "COIN3.L": (0.5,   100.0),
    "MSTRL.L": (0.5,   100.0),
    "PLTR3.L": (0.5,    80.0),
    "AVGO3.L": (0.5,   100.0),
    "MFAS.L":  (0.5,   100.0),
    "MSFL.L":  (0.5,   100.0),
    "GOOGL3.L":(0.5,   100.0),
    "AAPLL.L": (0.5,   100.0),
}

# ---------------------------------------------------------------------------
# LEVERAGE MAP -- nominal leverage multiplier per ticker
# Positive = long, Negative = short/inverse
# Derived from TICKER_REGISTRY but kept as a flat dict for backwards compat.
# ---------------------------------------------------------------------------

LEVERAGE_MAP: dict[str, float] = {
    t: float(e.leverage_factor) * (1.0 if e.direction == "LONG" else -1.0)
    for t, e in TICKER_REGISTRY.items()
}

# ---------------------------------------------------------------------------
# T212 FX FEE
# All .L instruments settle in GBP on T212 ISA -- NO FX conversion fee.
# The underlying exposure is USD-denominated but T212 handles the conversion
# as part of the ETP structure itself (no user-facing FX charge on .L).
# FX fee only applies if trading USD-denominated ETPs directly (not .L).
# ---------------------------------------------------------------------------

T212_FX_FEE_BPS: float = 0.0    # 0% -- .L tickers on T212 ISA are GBP-settled
T212_COMMISSION_BPS: float = 0.0 # 0% commission on T212 ISA (all .L tickers)

# Tickers that incur 0.15% FX fee on T212 (USD-denominated, non-.L products)
# Currently none in our ISA universe -- all are .L tickers
T212_USD_DENOMINATED: set[str] = set()  # empty -- update if adding non-.L ETPs

# ---------------------------------------------------------------------------
# SESSION CONFIG
# ---------------------------------------------------------------------------

SESSION_CONFIG: dict[str, str] = {
    "timezone":          "Europe/London",
    "lse_open":          "08:00",
    "lse_close":         "16:30",
    "us_open":           "14:30",
    "us_close":          "21:00",
    "overlap_start":     "14:30",
    "overlap_end":       "16:30",
    "bar_resolution":    "1d",
    "data_vendor":       "yfinance",
    "universe_version":  "2026-Q1",
    "risk_free_rate":    "0.045",
    "trading_days_pa":   "252",
}

# ---------------------------------------------------------------------------
# METRIC DEFINITIONS -- formula strings used in PDF footers / documentation
# ---------------------------------------------------------------------------

METRIC_DEFINITIONS: dict[str, str] = {
    "Move_pct":
        "(close_t - close_t-1) / close_t-1 * 100  [close-to-close %]",
    "Range_pct":
        "(high_t - low_t) / close_t-1 * 100  [full session H-L range as % of prior close]",
    "RVOL":
        "volume_t / mean(volume[t-21:t-1])  [relative volume vs 21-day trailing avg]",
    "MFE_from_open":
        "(high_t - open_t) / open_t * 100  [LONG: max favourable excursion from open]",
    "MAE_from_open":
        "(open_t - low_t) / open_t * 100   [LONG: max adverse excursion from open]",
    "Two_pct_flag":
        "Range_pct >= 2.0  [boolean: intraday range covers the 2% daily target]",
    "CAGR":
        "((close_end / close_start) ** (252 / n_days) - 1) * 100  [annualised return %]",
    "Ann_Vol":
        "std(daily_log_returns) * sqrt(252) * 100  [annualised volatility %]",
    "Sharpe":
        "(CAGR - risk_free_rate) / Ann_Vol  [annualised Sharpe, rf=4.5% UK gilt]",
    "Max_Drawdown":
        "(rolling_max - price) / rolling_max * 100  [peak-to-trough drawdown %]",
    "Momentum_Score":
        "weighted sum of 1M/3M/6M CAGR ranks  [0-100, higher = stronger momentum]",
    "Vol_Regime":
        "ATR_5 / ATR_21 classified: Expansion / Compression / Blow-off / Exhaustion",
    "Sector_Accel":
        "3M_CAGR - 6M_CAGR  [positive = sector accelerating vs prior half-year]",
}

# ---------------------------------------------------------------------------
# C-07: SLIPPAGE / SPREAD MODEL
# ---------------------------------------------------------------------------
# These are STATIC FALLBACK estimates only.  The authoritative cost model
# lives in ``execution/cost_model.py`` which implements:
#   - Live bid-ask spread tracking (EWMA via SpreadTracker)
#   - Almgren & Chriss (2001) market impact
#   - Perold (1988) implementation shortfall
#   - Spread gate (PASS / WATCH / VETO)
#
# The static spread_bps values below are used ONLY when:
#   1. execution/cost_model.py has no live spread observation for a ticker
#   2. The simple get_net_return() helper is called (e.g. PDF reports)
#
# For production trading decisions, always prefer:
#   from execution.cost_model import get_spread_bps, round_trip_cost_bps
#
# These values were calibrated from historical T212 spread observations
# (2025-Q4 to 2026-Q1) and should be refreshed quarterly.
# ---------------------------------------------------------------------------

SLIPPAGE_MODEL: dict = {
    "default_bps": 5,
    "spread_watch_threshold_bps": 22,
    "spread_veto_threshold_bps": 32,
    "spread_bps": {
        "QQQ3.L":  8,
        "QQQ5.L":  10,
        "SP5L.L":  8,
        "3LUS.L":  8,
        "QQQS.L":  10,
        "3USS.L":  10,
        "3SEM.L":  12,
        "GPT3.L":  12,
        "NVD3.L":  12,
        "TSL3.L":  15,
        "TSM3.L":  10,
        "MU2.L":   10,
        "AMD3.L":  12,
        "ARM3.L":  12,
        "NVDS.L":  15,
        "TSLS.L":  15,
        "3LDE.L":  12,
        "3LEU.L":  12,
        "3GOL.L":  15,
        "3SIL.L":  15,
        "3OIL.L":  15,
        "LLY3.L":  15,
        # --- SECTOR RADAR ---
        "3LHC.L":  20,
        "BAC3.L":  20,
        "GS3.L":   20,
        "3LEN.L":  18,
        "XOM3.L":  20,
        "COIN3.L": 25,
        "MSTRL.L": 25,
        "PLTR3.L": 18,
        "AVGO3.L": 15,
        "MFAS.L":  18,
        "MSFL.L":  15,
        "GOOGL3.L":18,
        "AAPLL.L": 15,
    },
}


# ---------------------------------------------------------------------------
# TICKER NAMES -- human-readable display names for PDF reports
# Derived from TICKER_REGISTRY for backwards compatibility.
# ---------------------------------------------------------------------------

TICKER_NAMES: dict[str, str] = {
    t: e.name for t, e in TICKER_REGISTRY.items()
}


# ---------------------------------------------------------------------------
# UNDERLYING INDEX MAP -- for liquidity gating + RVOL checks
# Maps each ETP to its underlying tradable asset (Yahoo Finance symbol).
# ---------------------------------------------------------------------------

UNDERLYING_INDEX: dict[str, str] = {
    # Nasdaq-linked
    "QQQ3.L": "^IXIC",  "QQQS.L": "^IXIC",  "QQQ5.L": "^IXIC",
    # S&P 500-linked
    "3LUS.L": "^GSPC",  "3USS.L": "^GSPC",  "SP5L.L": "^GSPC",
    # Semiconductors
    "3SEM.L": "^SOX",   "AMD3.L": "AMD",    "AVGO3.L": "AVGO",
    # Single-stock
    "NVD3.L": "NVDA",   "NVDS.L": "NVDA",
    "TSL3.L": "TSLA",   "TSLS.L": "TSLA",
    "TSM3.L": "TSM",    "MU2.L":  "MU",
    "ARM3.L": "ARM",    "GPT3.L": "MSFT",
    "LLY3.L": "LLY",
    # Europe
    "3LDE.L": "^GDAXI", "3LEU.L": "^STOXX50E",
    # Commodities
    "3OIL.L": "CL=F",   "3GOL.L": "GC=F",   "3SIL.L": "SI=F",
    # Sector Radar — single-stock
    "3LHC.L":  "XLV",     # Healthcare sector ETF (no single underlying)
    "BAC3.L":  "BAC",     "GS3.L":    "GS",
    "3LEN.L":  "XLE",     # Energy sector ETF (no single underlying)
    "XOM3.L":  "XOM",     "COIN3.L":  "COIN",
    "MSTRL.L": "MSTR",    "PLTR3.L":  "PLTR",
    "MFAS.L":  "META",    "MSFL.L":   "MSFT",
    "GOOGL3.L":"GOOGL",   "AAPLL.L":  "AAPL",
}

# ---------------------------------------------------------------------------
# INVERSE PAIR MAP -- for delta-neutral flash crash hedging
# Maps long ETP -> corresponding short ETP
# F-03: derived from single source of truth (config.universe_constants)
# ---------------------------------------------------------------------------

from config.universe_constants import LONG_TO_INVERSE as _long_to_inv

# INVERSE_PAIRS: long -> first inverse counterpart (backwards-compatible dict)
INVERSE_PAIRS: dict[str, str] = {
    long_t: inv_list[0] for long_t, inv_list in _long_to_inv.items()
}

# ---------------------------------------------------------------------------
# 5x PRODUCTS -- intraday only, mandatory overnight kill (C-09)
# Derived from TICKER_REGISTRY.overnight_kill for backwards compatibility.
# ---------------------------------------------------------------------------

FIVE_X_TICKERS: set[str] = {
    t for t, e in TICKER_REGISTRY.items() if e.overnight_kill
}

# ---------------------------------------------------------------------------
# CORRELATION GROUPS -- for PCA-based portfolio concentration veto
# Max 2 positions per group. Static fallback; dynamic PCA replaces in Week 2.
# ---------------------------------------------------------------------------

CORRELATION_GROUPS: dict[str, list[str]] = {
    "NASDAQ":      ["QQQ3.L", "QQQS.L", "QQQ5.L", "GPT3.L", "ARM3.L"],
    "SP500":       ["3LUS.L", "3USS.L", "SP5L.L"],
    "SEMIS":       ["3SEM.L", "NVD3.L", "AMD3.L", "TSM3.L", "AVGO3.L"],
    "TSLA":        ["TSL3.L", "TSLS.L"],
    "COMMODITIES": ["3OIL.L", "3GOL.L", "3SIL.L"],
    "EUROPE":      ["3LDE.L", "3LEU.L"],
}

# ---------------------------------------------------------------------------
# T-13: UNDERLYING MAP -- maps each ETP to its underlying for duplicate blocking
# Prevents QQQ3.L + QQQ5.L (both NASDAQ, different leverage) from co-existing
# ---------------------------------------------------------------------------

UNDERLYING_MAP: dict[str, str] = {
    # Core universe — LONG
    "QQQ3.L": "NASDAQ",    "QQQ5.L": "NASDAQ",
    "3LUS.L": "SP500",     "SP5L.L": "SP500",
    "3SEM.L": "SEMICON",   "NVD3.L": "NVIDIA",
    "TSL3.L": "TESLA",     "TSM3.L": "TSMC",
    "MU2.L":  "MICRON",    "GPT3.L": "AI_BASKET",
    # Core universe — SHORT (same underlying blocks both directions)
    "QQQS.L": "NASDAQ",    "3USS.L": "SP500",
    # Extended universe
    "AMD3.L": "AMD",       "ARM3.L": "ARM",
    "NVDS.L": "NVIDIA",    "TSLS.L": "TESLA",
    "AVGO3.L": "AVGO",     "LLY3.L": "ELI_LILLY",
    "3LDE.L": "DAX",       "3LEU.L": "EUROSTOXX",
    "3GOL.L": "GOLD",      "3SIL.L": "SILVER",
    "3OIL.L": "CRUDE_OIL",
}

# ---------------------------------------------------------------------------
# SECTOR PROXY MAP -- for sector dispersion check
# Maps ETP -> US sector ETF for directional alignment confirmation
# ---------------------------------------------------------------------------

SECTOR_PROXY: dict[str, str] = {
    "NVD3.L": "SMH",  "AMD3.L": "SMH",  "3SEM.L": "SMH",
    "TSM3.L": "SMH",  "AVGO3.L": "SMH",
    "GPT3.L": "XLK",  "ARM3.L": "XLK",
    "TSL3.L": "XLY",  "3OIL.L": "XLE",
    "3GOL.L": "GLD",  "3SIL.L": "SLV",
}


# ═══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

# ---------------------------------------------------------------------------
# C-08: Leverage guard -- get_leverage_factor()
# ---------------------------------------------------------------------------

def get_leverage_factor(ticker: str) -> int:
    """Return the unsigned leverage multiplier for *ticker* (2, 3, or 5).

    Parameters
    ----------
    ticker : str
        Yahoo Finance ticker symbol (e.g. ``"QQQ3.L"``).

    Returns
    -------
    int
        The nominal leverage factor (always positive).

    Raises
    ------
    ValueError
        If *ticker* is not in ``TICKER_REGISTRY``.

    Examples
    --------
    >>> get_leverage_factor("QQQ5.L")
    5
    >>> get_leverage_factor("QQQS.L")
    3
    """
    entry = TICKER_REGISTRY.get(ticker)
    if entry is None:
        raise ValueError(
            f"Unknown ticker '{ticker}' -- not in TICKER_REGISTRY.  "
            f"Valid tickers: {sorted(TICKER_REGISTRY.keys())}"
        )
    return entry.leverage_factor


# ---------------------------------------------------------------------------
# C-09: Overnight kill logic -- must_close_by_session_end()
# ---------------------------------------------------------------------------

def must_close_by_session_end(ticker: str) -> bool:
    """Return ``True`` if *ticker* must be closed before session end.

    5x ETPs carry extreme overnight gap risk and must never be held
    overnight.  This function checks the ``overnight_kill`` flag in
    ``TICKER_REGISTRY``.

    Parameters
    ----------
    ticker : str
        Yahoo Finance ticker symbol (e.g. ``"QQQ5.L"``).

    Returns
    -------
    bool
        ``True`` if the position must be liquidated before LSE close
        (16:30 UTC).  ``False`` if overnight holding is permitted.

    Raises
    ------
    ValueError
        If *ticker* is not in ``TICKER_REGISTRY``.

    Examples
    --------
    >>> must_close_by_session_end("QQQ5.L")
    True
    >>> must_close_by_session_end("QQQ3.L")
    False
    """
    entry = TICKER_REGISTRY.get(ticker)
    if entry is None:
        raise ValueError(
            f"Unknown ticker '{ticker}' -- not in TICKER_REGISTRY.  "
            f"Cannot determine overnight kill status."
        )
    return entry.overnight_kill


# ---------------------------------------------------------------------------
# Factor group lookup
# ---------------------------------------------------------------------------

def get_factor_group(ticker: str) -> str:
    """Return the primary ``ISA_FACTOR_GROUPS`` key for *ticker*.

    If a ticker appears in multiple groups the first match (in dict
    insertion order) is returned.  Returns ``"unknown"`` if the ticker
    is not found in any group.

    Parameters
    ----------
    ticker : str
        Yahoo Finance ticker symbol.

    Returns
    -------
    str
        Factor group name (e.g. ``"nasdaq_beta_long"``) or ``"unknown"``.
    """
    for group, members in ISA_FACTOR_GROUPS.items():
        if ticker in members:
            return group
    return "unknown"


# ---------------------------------------------------------------------------
# Net return after simple trading costs (PDF reports / quick estimates)
# ---------------------------------------------------------------------------

def get_net_return(gross_pct: float, ticker: str) -> float:
    """Adjust a gross percentage return for round-trip trading costs.

    This is a SIMPLIFIED cost model using static spread estimates.
    For production trading, use ``execution.cost_model.round_trip_cost_bps``
    which incorporates live spread tracking and market impact.

    Costs applied:
      1. Bid-ask spread: 2 x half-spread_bps (entry + exit)
      2. T212 FX conversion fee: 2 x fee_bps if ticker in T212_USD_DENOMINATED

    Parameters
    ----------
    gross_pct : float
        Raw percentage return before costs (e.g. 2.5 for a 2.5% move).
    ticker : str
        Yahoo Finance ticker symbol used to look up spread.

    Returns
    -------
    float
        Net return in percentage points after subtracting estimated costs.
    """
    spread_map: dict[str, int] = SLIPPAGE_MODEL["spread_bps"]
    half_spread_bps: int = spread_map.get(ticker, SLIPPAGE_MODEL["default_bps"])

    # Round-trip spread cost (entry half-spread + exit half-spread)
    spread_cost_pct: float = 2 * half_spread_bps / 100.0

    # FX cost: 0% for .L tickers on T212 ISA (GBP-settled)
    # 0.15% per leg only if ticker in T212_USD_DENOMINATED (currently empty)
    fx_cost_pct: float = (2 * T212_FX_FEE_BPS / 100.0
                          if ticker in T212_USD_DENOMINATED else 0.0)

    total_cost_pct: float = spread_cost_pct + fx_cost_pct
    return gross_pct - total_cost_pct


# ---------------------------------------------------------------------------
# Leverage helpers (backwards-compatible)
# ---------------------------------------------------------------------------

def is_short(ticker: str) -> bool:
    """Return ``True`` if the ticker is an inverse/short ETP."""
    return LEVERAGE_MAP.get(ticker, 1.0) < 0


def get_leverage(ticker: str, default: float = 1.0) -> float:
    """Return the signed leverage multiplier for *ticker*.

    Parameters
    ----------
    ticker : str
        Yahoo Finance ticker symbol.
    default : float
        Fallback value if *ticker* is not in ``LEVERAGE_MAP``.

    Returns
    -------
    float
        Signed leverage (negative for inverse ETPs).
    """
    return LEVERAGE_MAP.get(ticker, default)


def get_abs_leverage(ticker: str, default: float = 3.0) -> float:
    """Return the absolute leverage factor for *ticker* (always positive).

    Used by kelly_sizer and cost_model for risk scaling.

    Parameters
    ----------
    ticker : str
        Yahoo Finance ticker symbol.
    default : float
        Fallback value if *ticker* is not in ``LEVERAGE_MAP``.

    Returns
    -------
    float
        Absolute leverage factor (e.g. 3.0 for both long and short 3x).
    """
    return abs(LEVERAGE_MAP.get(ticker, default))


def get_underlying_yahoo_ticker(etp_ticker: str) -> Optional[str]:
    """Return the Yahoo Finance symbol for the underlying asset of an ETP.

    Used by the indicator engine (AEGIS 0-03) to compute RSI, EMA, MACD,
    and ADX on the underlying instrument instead of the leveraged ETP,
    avoiding vol-drag-induced RSI compression and false EMA downtrends.

    Parameters
    ----------
    etp_ticker : str
        Leveraged ETP ticker (e.g. ``"QQQ3.L"``).

    Returns
    -------
    Optional[str]
        Yahoo Finance symbol of the underlying (e.g. ``"^IXIC"`` for
        QQQ3.L, ``"NVDA"`` for NVD3.L).  Returns ``None`` if no
        mapping exists.

    Examples
    --------
    >>> get_underlying_yahoo_ticker("QQQ3.L")
    '^IXIC'
    >>> get_underlying_yahoo_ticker("NVD3.L")
    'NVDA'
    >>> get_underlying_yahoo_ticker("UNKNOWN") is None
    True
    """
    return UNDERLYING_INDEX.get(etp_ticker)
