"""
F-03: Inverse ETP List -- Single Source of Truth
=================================================

Pure-data module with ZERO imports from other project modules.
This prevents circular imports (SA-06) while giving every module
in the codebase a single canonical place to check "is this ticker
an inverse ETP?" and "what is its long counterpart?".

Usage
-----
    from config.universe_constants import INVERSE_ETPS, INVERSE_ETPS_SET

    if ticker in INVERSE_ETPS_SET:
        long_counterpart = INVERSE_ETPS[ticker]

Maintenance
-----------
When adding or removing an inverse ETP, edit ONLY this dict.
Every other module imports from here -- no other file should
hard-code inverse ticker lists.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# INVERSE_ETPS: inverse ticker -> long counterpart
#
# This is the ONLY place inverse ETP membership is defined.
# Key   = inverse/short ETP ticker
# Value = corresponding long ETP ticker (same underlying)
# ---------------------------------------------------------------------------

INVERSE_ETPS: dict[str, str] = {
    # --- Core inverse pairs (CORE tier) ---
    "QQQS.L": "QQQ3.L",   # Nasdaq 100 -3x  (Leverage Shares)
    "3USS.L": "3LUS.L",    # S&P 500 -3x     (WisdomTree)

    # --- Extended inverse pairs ---
    "NVDS.L": "NVD3.L",    # NVIDIA -3x      (Leverage Shares)
    "TSLS.L": "TSL3.L",    # Tesla -3x       (Leverage Shares)

    # --- Sector / single-stock inverse ---
    "SC3S.L": "3SEM.L",    # Semiconductors -3x (WisdomTree)
    "GPTS.L": "GPT3.L",    # AI Index -3x       (Leverage Shares)
    "3SNV.L": "NVD3.L",    # NVIDIA -3x         (GraniteShares variant)
    "3STS.L": "TSL3.L",    # Tesla -3x          (GraniteShares variant)
    "TSMS.L": "TSM3.L",    # TSMC -3x           (Leverage Shares)
    "MUS.L":  "MU2.L",     # Micron -1x         (Leverage Shares)

    # --- 5x inverse (intraday only, mandatory overnight kill) ---
    "SQQQ.L": "QQQ5.L",    # Nasdaq 100 -5x  (Leverage Shares)
    "SPYS.L": "SP5L.L",    # S&P 500 -5x     (Leverage Shares)
}

# ---------------------------------------------------------------------------
# Derived constants (frozen -- never mutate at runtime)
# ---------------------------------------------------------------------------

INVERSE_ETPS_SET: frozenset[str] = frozenset(INVERSE_ETPS.keys())
"""All inverse ETP tickers as a frozenset for O(1) membership checks."""

# Reverse map: long ticker -> list of its inverse counterparts
# (a long ETP may have multiple inverse variants, e.g. NVD3.L -> [NVDS.L, 3SNV.L])
LONG_TO_INVERSE: dict[str, list[str]] = {}
for _inv, _long in INVERSE_ETPS.items():
    LONG_TO_INVERSE.setdefault(_long, []).append(_inv)
del _inv, _long  # clean up module namespace
