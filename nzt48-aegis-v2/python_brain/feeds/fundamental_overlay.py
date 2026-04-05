"""Fundamental Overlay — Strategy-aware confidence adjustments from IBKR fundamentals.

Reads /app/data/fundamental_signals.json (produced nightly by fundamental_data_provider.py)
and applies per-ticker confidence deltas to live signals in bridge.py's _apply_adjustments.

Overlay logic:
    1. VALUE SCREEN (P/E < 15, P/B < 2, ROE > 15%):
       → Boost mean-reversion strategies (+5 confidence)
       → These are fundamentally cheap stocks where pullbacks are buying opportunities

    2. GROWTH SCREEN (Revenue growth > 20%, EPS growth positive):
       → Boost momentum strategies (+4 confidence)
       → Growth stocks have momentum persistence

    3. SHORT INTEREST (Days to cover > 5):
       → Boost HighFlyer and momentum strategies (+3 to +7 confidence)
       → Short squeeze potential creates explosive upside moves

    4. ANALYST CONSENSUS:
       → >80% buy ratings → +3 confidence (all strategies)
       → >50% sell ratings → -5 confidence (all strategies)

    5. INSIDER OWNERSHIP (>10%):
       → +2 confidence (all strategies)
       → Management alignment with shareholders

    6. QUALITY SCREEN (low debt, high current ratio, positive FCF):
       → +2 confidence for mean-reversion
       → Low-risk, high-quality companies where pullbacks are buying opportunities

    7. DANGER SCREENS (P/E > 100, debt/equity > 3, negative EPS):
       → Confidence penalty (-2 to -3)

Pattern: Follows the same file-based overlay approach as insider_signals.json,
congressional_signals.json, etc. in bridge.py _apply_adjustments.

Usage in bridge.py:
    from python_brain.feeds.fundamental_overlay import apply_fundamental_overlay
    apply_fundamental_overlay(all_signals, ticker_symbol)
"""

from __future__ import annotations

import json
import logging
import os
import time
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("fundamental_overlay")

_DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
_SIGNALS_FILE = _DATA_DIR / "fundamental_signals.json"

# ---------------------------------------------------------------------------
# Cached signal data (loaded once, refreshed every 5 min)
# ---------------------------------------------------------------------------
_fund_data: Optional[Dict[str, Any]] = None
_fund_data_lock = threading.Lock()
_fund_data_load_time: float = 0.0
_RELOAD_INTERVAL = 300  # 5 minutes


# Strategy categories for targeted overlays
_MOMENTUM_STRATEGIES = frozenset({
    "Momentum", "VolExpansion", "S1_Microstructure", "S3_MacroTrend",
    "HighFlyer", "ORB", "GapMomentum", "FOmcDrift",
})

_MR_STRATEGIES = frozenset({
    "IBS_MeanReversion", "S2_Reversion", "PairsReversion", "CointPairs",
    "NAVArbitrage",
})

_SQUEEZE_STRATEGIES = frozenset({
    "HighFlyer", "Momentum", "VolExpansion", "GapMomentum",
})


def _load_fund_signals() -> Dict[str, Any]:
    """Load fundamental signals from disk. Cached for 5 min."""
    global _fund_data, _fund_data_load_time
    now = time.monotonic()

    # Fast path: data is fresh
    if _fund_data is not None and (now - _fund_data_load_time) < _RELOAD_INTERVAL:
        return _fund_data

    with _fund_data_lock:
        # Double-check after acquiring lock
        if _fund_data is not None and (now - _fund_data_load_time) < _RELOAD_INTERVAL:
            return _fund_data

        if not _SIGNALS_FILE.exists():
            _fund_data = {}
            _fund_data_load_time = now
            return _fund_data

        try:
            file_age = time.time() - os.path.getmtime(_SIGNALS_FILE)
            if file_age > 172800:  # > 48h old — stale
                log.debug("Fundamental signals file is stale (%.0fh old)", file_age / 3600)
                _fund_data = {}
                _fund_data_load_time = now
                return _fund_data

            with open(_SIGNALS_FILE) as f:
                data = json.load(f)
            _fund_data = data if isinstance(data, dict) else {}
            _fund_data_load_time = now
            return _fund_data

        except Exception as e:
            log.debug("Failed to load fundamental signals: %s", e)
            _fund_data = {}
            _fund_data_load_time = now
            return _fund_data


def apply_fundamental_overlay(
    all_signals: List[Dict[str, Any]],
    ticker_symbol: str,
) -> None:
    """Apply fundamental-based confidence adjustments to signals in-place.

    This is the main entry point called from bridge.py _apply_adjustments.
    Modifies signals in-place (same pattern as insider/congressional overlays).

    Args:
        all_signals: List of signal dicts (modified in-place).
        ticker_symbol: The ticker symbol to look up fundamentals for.
    """
    if not all_signals or not ticker_symbol:
        return

    data = _load_fund_signals()
    tickers = data.get("tickers", {})
    info = tickers.get(ticker_symbol)

    if not info or not isinstance(info, dict):
        return

    screens = set(info.get("screens", []))
    base_delta = info.get("confidence_delta", 0)

    if base_delta == 0 and not screens:
        return

    for sig in all_signals:
        strategy = sig.get("strategy", "")
        delta = 0

        # ── VALUE SCREEN: Boost mean-reversion on cheap quality stocks ──
        if "value" in screens and strategy in _MR_STRATEGIES:
            delta += 5
            sig["fundamental_value"] = True
            if "value_pe" in info:
                sig["fund_pe"] = info["value_pe"]

        # ── GROWTH SCREEN: Boost momentum on growth stocks ──
        if "growth" in screens and strategy in _MOMENTUM_STRATEGIES:
            delta += 4
            sig["fundamental_growth"] = True
            if "growth_rev_pct" in info:
                sig["fund_rev_growth"] = info["growth_rev_pct"]

        # ── SHORT SQUEEZE: Boost squeeze-eligible strategies ──
        if "short_squeeze" in screens and strategy in _SQUEEZE_STRATEGIES:
            dtc = info.get("short_days_to_cover", 0)
            if dtc > 12:
                delta += 7
            elif dtc > 8:
                delta += 5
            else:
                delta += 3
            sig["fundamental_short_squeeze"] = True
            sig["fund_days_to_cover"] = dtc

        # ── ANALYST CONSENSUS: Apply to all strategies ──
        if "analyst_strong_buy" in screens:
            delta += 3
            sig["fundamental_analyst_buy"] = True
            if "analyst_buy_pct" in info:
                sig["fund_analyst_buy_pct"] = info["analyst_buy_pct"]
        elif "analyst_bearish" in screens:
            delta -= 5
            sig["fundamental_analyst_sell"] = True
            if "analyst_sell_pct" in info:
                sig["fund_analyst_sell_pct"] = info["analyst_sell_pct"]

        # ── INSIDER OWNERSHIP: Alignment signal for all strategies ──
        if "insider_aligned" in screens:
            delta += 2
            sig["fundamental_insider_aligned"] = True
            if "insider_ownership_pct" in info:
                sig["fund_insider_own_pct"] = info["insider_ownership_pct"]

        # ── QUALITY: Boost mean-reversion on low-risk companies ──
        if "quality" in screens and strategy in _MR_STRATEGIES:
            delta += 2
            sig["fundamental_quality"] = True

        # ── DANGER SCREENS: Penalties for all strategies ──
        if "speculative_pe" in screens:
            delta -= 3
            sig["fundamental_speculative"] = True
        if "high_leverage" in screens:
            delta -= 3
            sig["fundamental_high_leverage"] = True
        if "negative_earnings" in screens:
            delta -= 2
            sig["fundamental_negative_earnings"] = True

        # Apply clamped delta
        if delta != 0:
            delta = max(-10, min(10, delta))
            sig["confidence"] = max(0, min(100, sig["confidence"] + delta))
            sig["fundamental_delta"] = delta
            sig["fundamental_screens"] = list(screens)


class FundamentalOverlay:
    """Object-oriented wrapper around the fundamental overlay functions."""

    def apply(self, all_signals: List[Dict[str, Any]], ticker_symbol: str) -> None:
        """Apply fundamental-based confidence adjustments to signals in-place."""
        apply_fundamental_overlay(all_signals, ticker_symbol)

    def get_info(self, ticker_symbol: str) -> Optional[Dict[str, Any]]:
        """Get fundamental signal info for a single ticker."""
        return get_fundamental_info(ticker_symbol)


def get_fundamental_info(ticker_symbol: str) -> Optional[Dict[str, Any]]:
    """Get fundamental signal info for a single ticker. For diagnostics."""
    data = _load_fund_signals()
    tickers = data.get("tickers", {})
    return tickers.get(ticker_symbol)
