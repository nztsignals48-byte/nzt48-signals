"""Book 188: Pre-Trade Risk Architecture.

Five independent risk checks that run AFTER signal generation and existing
quality gates, but BEFORE the signal is sent to Rust for execution. Each
check can independently block or reduce a signal.

The checks are:
  1. Position-level risk: notional value < 5% of account equity
  2. Correlation risk: new position too correlated with existing (>0.8)
  3. Sector concentration: no more than 40% in any sector
  4. Intraday P&L guard: daily P&L < -2% reduces size by 50%
  5. Order-to-trade ratio: >100 orders with <10% fill rate pauses 5 min

Usage (bridge.py — after existing gates, before signal output):
    from python_brain.risk.pre_trade_checks import pre_trade_risk_check
    passed, reason = pre_trade_risk_check(signal, portfolio, market)
    if not passed:
        log_gate_veto(ticker_id, "pre_trade_188", ...)
        return no_signal

Portfolio dict expected shape:
    {
        "equity": float,             # Account equity (e.g. 10000.0)
        "daily_pnl": float,          # Today's realised + unrealised P&L
        "daily_pnl_pct": float,      # daily_pnl / equity (e.g. -0.015 = -1.5%)
        "open_positions": [           # List of open position dicts
            {
                "symbol": str,
                "sector": str,        # "TECH", "FINANCE", etc. (or "" if unknown)
                "notional": float,    # Current notional value
                "returns": [float],   # Recent per-bar returns (for correlation)
            },
            ...
        ],
        "orders_today": int,         # Total orders placed today
        "fills_today": int,          # Total fills today
        "last_order_ts": float,      # Timestamp of last order (epoch seconds)
    }

Market dict expected shape:
    {
        "timestamp": float,          # Current epoch seconds
    }
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("pre_trade_checks")

# ---------------------------------------------------------------------------
# Constants (Book 188, Chapter 4: Calibrated thresholds)
# ---------------------------------------------------------------------------

# Check 1: Max notional as fraction of equity
MAX_POSITION_NOTIONAL_PCT = 0.05  # 5%

# Check 2: Max correlation with any existing position
MAX_CORRELATION_THRESHOLD = 0.80

# Check 3: Max sector concentration
MAX_SECTOR_CONCENTRATION = 0.40  # 40%

# Check 4: Intraday P&L guard
INTRADAY_PNL_GUARD_PCT = -0.02  # -2% triggers size reduction
INTRADAY_SIZE_REDUCTION = 0.50  # Reduce size by 50%

# Check 5: Order-to-trade ratio
OTR_ORDER_THRESHOLD = 100  # Must have at least this many orders
OTR_FILL_RATE_THRESHOLD = 0.10  # Below 10% fill rate = pause
OTR_PAUSE_SECONDS = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Correlation computation (pure stdlib)
# ---------------------------------------------------------------------------

def _pearson_correlation(x: List[float], y: List[float]) -> float:
    """Compute Pearson correlation coefficient between two series.

    Returns 0.0 if insufficient data or zero variance.
    """
    n = min(len(x), len(y))
    if n < 5:
        return 0.0

    # Use the last n observations from each
    x = x[-n:]
    y = y[-n:]

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    cov = 0.0
    var_x = 0.0
    var_y = 0.0

    for i in range(n):
        dx = x[i] - mean_x
        dy = y[i] - mean_y
        cov += dx * dy
        var_x += dx * dx
        var_y += dy * dy

    if var_x < 1e-12 or var_y < 1e-12:
        return 0.0

    return cov / (var_x ** 0.5 * var_y ** 0.5)


# ---------------------------------------------------------------------------
# Sector classification (simple heuristic for ETPs)
# ---------------------------------------------------------------------------

# Symbol prefix → sector mapping for common AEGIS instruments
_PREFIX_SECTOR_MAP = {
    # UK / European ETPs
    "QQQ": "TECH", "3QQ": "TECH", "5QQ": "TECH",
    "SPY": "US_BROAD", "3US": "US_BROAD", "5US": "US_BROAD",
    "IWM": "US_SMALL",
    "EWJ": "JAPAN", "TSE": "JAPAN",
    "EWH": "HONG_KONG", "HSI": "HONG_KONG",
    "NV3": "SEMIS", "3NV": "SEMIS",
    "3AM": "TECH", "3AP": "TECH", "3MS": "TECH",
    "3TS": "TECH", "3SM": "SEMIS",
    "3SN": "SEMIS", "3SA": "TECH",
    "SUK": "UK_BROAD", "ISF": "UK_BROAD", "VUKE": "UK_BROAD",
    "3EM": "EMERGING",
    "GLD": "COMMODITIES", "SLV": "COMMODITIES",
    "USO": "ENERGY", "XLE": "ENERGY",
    "XLF": "FINANCE", "XLV": "HEALTH",
}


def _infer_sector(symbol: str) -> str:
    """Infer sector from symbol name using prefix matching.

    Returns sector string or "UNKNOWN".
    """
    if not symbol:
        return "UNKNOWN"
    sym_upper = symbol.upper().rstrip(".L")  # Strip LSE suffix

    # Try exact prefix match (3-char, then 2-char)
    for prefix_len in (3, 2):
        prefix = sym_upper[:prefix_len]
        if prefix in _PREFIX_SECTOR_MAP:
            return _PREFIX_SECTOR_MAP[prefix]

    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_position_notional(
    signal: Dict[str, Any],
    portfolio: Dict[str, Any],
) -> Tuple[bool, str]:
    """Check 1: Position-level risk — notional < 5% of equity.

    If the signal's proposed notional exceeds the limit, it is blocked.
    """
    equity = portfolio.get("equity", 0.0)
    if equity <= 0:
        return True, ""  # Can't check without equity — fail-open

    price = signal.get("price", signal.get("last", 0.0))
    shares = signal.get("shares", 0)
    notional = price * shares

    if notional <= 0:
        return True, ""

    pct = notional / equity
    if pct > MAX_POSITION_NOTIONAL_PCT:
        reason = (
            f"CHECK_188_1_NOTIONAL: {notional:.2f} = {pct:.1%} of equity "
            f"({equity:.2f}) exceeds {MAX_POSITION_NOTIONAL_PCT:.0%} limit"
        )
        return False, reason

    return True, ""


def _check_correlation_risk(
    signal: Dict[str, Any],
    portfolio: Dict[str, Any],
) -> Tuple[bool, str]:
    """Check 2: Correlation risk — new position correlation with existing > 0.8 -> block.

    Uses per-bar return series from open positions and the signal's ticker.
    """
    open_positions = portfolio.get("open_positions", [])
    if not open_positions:
        return True, ""

    new_returns = signal.get("returns", [])
    if not new_returns or len(new_returns) < 10:
        return True, ""  # Insufficient data — fail-open

    new_symbol = signal.get("symbol", "")

    for pos in open_positions:
        pos_returns = pos.get("returns", [])
        if not pos_returns or len(pos_returns) < 10:
            continue

        corr = _pearson_correlation(new_returns, pos_returns)
        if abs(corr) > MAX_CORRELATION_THRESHOLD:
            pos_sym = pos.get("symbol", "?")
            reason = (
                f"CHECK_188_2_CORRELATION: {new_symbol} corr={corr:.3f} with "
                f"{pos_sym} exceeds {MAX_CORRELATION_THRESHOLD} threshold"
            )
            return False, reason

    return True, ""


def _check_sector_concentration(
    signal: Dict[str, Any],
    portfolio: Dict[str, Any],
) -> Tuple[bool, str]:
    """Check 3: Sector concentration — no more than 40% in any sector.

    Counts open positions by sector. If adding the new signal would push
    any sector above the concentration limit, it is blocked.
    """
    open_positions = portfolio.get("open_positions", [])
    if not open_positions:
        return True, ""

    # Count positions by sector
    sector_counts: Dict[str, int] = {}
    total = len(open_positions)

    for pos in open_positions:
        sector = pos.get("sector", "") or _infer_sector(pos.get("symbol", ""))
        sector_counts[sector] = sector_counts.get(sector, 0) + 1

    # Determine sector of new signal
    new_sector = signal.get("sector", "") or _infer_sector(signal.get("symbol", ""))

    # Check if adding one more to this sector exceeds the limit
    current_in_sector = sector_counts.get(new_sector, 0)
    new_total = total + 1
    new_concentration = (current_in_sector + 1) / new_total

    if new_concentration > MAX_SECTOR_CONCENTRATION:
        reason = (
            f"CHECK_188_3_SECTOR: {new_sector} would be "
            f"{current_in_sector + 1}/{new_total} = {new_concentration:.0%} "
            f"(limit {MAX_SECTOR_CONCENTRATION:.0%})"
        )
        return False, reason

    return True, ""


def _check_intraday_pnl_guard(
    signal: Dict[str, Any],
    portfolio: Dict[str, Any],
) -> Tuple[bool, str]:
    """Check 4: Intraday P&L guard — if daily P&L < -2%, reduce size by 50%.

    This check does NOT block the signal. Instead, it modifies it in place
    (halving shares and kelly_fraction) and returns True with a reason string
    indicating the size reduction was applied.
    """
    daily_pnl_pct = portfolio.get("daily_pnl_pct", 0.0)

    if daily_pnl_pct < INTRADAY_PNL_GUARD_PCT:
        # Apply size reduction IN PLACE
        old_shares = signal.get("shares", 0)
        old_kelly = signal.get("kelly_fraction", 0.0)

        new_shares = max(1, int(old_shares * INTRADAY_SIZE_REDUCTION))
        new_kelly = old_kelly * INTRADAY_SIZE_REDUCTION

        signal["shares"] = new_shares
        signal["kelly_fraction"] = new_kelly
        signal["pnl_guard_applied"] = True
        signal["pnl_guard_reduction"] = INTRADAY_SIZE_REDUCTION

        reason = (
            f"CHECK_188_4_PNL_GUARD: daily P&L {daily_pnl_pct:.2%} < "
            f"{INTRADAY_PNL_GUARD_PCT:.0%} — size reduced {INTRADAY_SIZE_REDUCTION:.0%} "
            f"(shares {old_shares}->{new_shares}, kelly {old_kelly:.3f}->{new_kelly:.3f})"
        )
        log.info(reason)
        # Return True (not blocked) but with reason for logging
        return True, reason

    return True, ""


def _check_order_to_trade_ratio(
    signal: Dict[str, Any],
    portfolio: Dict[str, Any],
    market: Dict[str, Any],
) -> Tuple[bool, str]:
    """Check 5: Order-to-trade ratio — pause if >100 orders with <10% fill rate.

    If the fill rate is too low, it suggests market conditions are adverse
    (wide spreads, thin books, or algo churn). Pauses new orders for 5 min.
    """
    orders_today = portfolio.get("orders_today", 0)
    fills_today = portfolio.get("fills_today", 0)

    if orders_today < OTR_ORDER_THRESHOLD:
        return True, ""  # Not enough orders to trigger

    fill_rate = fills_today / orders_today if orders_today > 0 else 0.0

    if fill_rate < OTR_FILL_RATE_THRESHOLD:
        # Check if we're still in the pause window
        last_order_ts = portfolio.get("last_order_ts", 0.0)
        current_ts = market.get("timestamp", time.time())
        elapsed = current_ts - last_order_ts

        if elapsed < OTR_PAUSE_SECONDS:
            remaining = OTR_PAUSE_SECONDS - elapsed
            reason = (
                f"CHECK_188_5_OTR: {orders_today} orders, {fills_today} fills "
                f"({fill_rate:.1%} fill rate < {OTR_FILL_RATE_THRESHOLD:.0%}) — "
                f"paused, {remaining:.0f}s remaining"
            )
            return False, reason

    return True, ""


# ---------------------------------------------------------------------------
# Main pre-trade risk check (aggregates all 5 checks)
# ---------------------------------------------------------------------------

def pre_trade_risk_check(
    signal: Dict[str, Any],
    portfolio: Dict[str, Any],
    market: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Run all 5 pre-trade risk checks on a signal.

    Checks run in sequence. The first hard block (checks 1,2,3,5) stops
    processing and returns (False, reason). Check 4 (P&L guard) only
    reduces size but does not block.

    Args:
        signal: Signal dict from bridge.py. Must have:
            - "price" or "last" (float)
            - "shares" (int)
            - "symbol" (str, optional)
            - "kelly_fraction" (float, optional)
            - "returns" (list[float], optional — for correlation check)
        portfolio: Portfolio state dict (see module docstring for shape).
        market: Market state dict with "timestamp" key.

    Returns:
        (passed, reason) tuple.
        passed=True means all checks passed (signal may have been size-reduced by Check 4).
        passed=False means signal should be blocked, reason explains which check failed.
    """
    if market is None:
        market = {"timestamp": time.time()}

    # Check 1: Position-level notional risk
    passed, reason = _check_position_notional(signal, portfolio)
    if not passed:
        log.info("PRE_TRADE_BLOCK: %s", reason)
        return False, reason

    # Check 2: Correlation risk
    passed, reason = _check_correlation_risk(signal, portfolio)
    if not passed:
        log.info("PRE_TRADE_BLOCK: %s", reason)
        return False, reason

    # Check 3: Sector concentration
    passed, reason = _check_sector_concentration(signal, portfolio)
    if not passed:
        log.info("PRE_TRADE_BLOCK: %s", reason)
        return False, reason

    # Check 4: Intraday P&L guard (modifies signal, does not block)
    _passed, pnl_reason = _check_intraday_pnl_guard(signal, portfolio)
    # pnl_reason is logged inside the check if triggered

    # Check 5: Order-to-trade ratio
    passed, reason = _check_order_to_trade_ratio(signal, portfolio, market)
    if not passed:
        log.info("PRE_TRADE_BLOCK: %s", reason)
        return False, reason

    # All checks passed (Check 4 may have reduced size)
    if pnl_reason:
        return True, pnl_reason  # Signal passed but was size-reduced
    return True, ""
