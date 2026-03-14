"""
NZT-48 Core -- PDF QA Pre-Flight Gate (W6)
Validates all data before PDF render to prevent NaN crashes,
missing fields, and invalid regimes from reaching the PDF builder.
Feature flag: pdf_qa_gate in settings.yaml
"""

from __future__ import annotations

import math
import logging
from typing import Any

from core.regime_mapping import VALID_8_STATE, VALID_5_STATE

logger = logging.getLogger("nzt48.core.pdf_qa_gate")

# -- Valid regimes for PDF output (union of 5-state and 8-state) ---------------
# PDFs accept both taxonomies depending on which generator produced the data.
_ALL_VALID_REGIMES = VALID_8_STATE | VALID_5_STATE | {"NEUTRAL", "UNKNOWN", "--"}


# -- Helper: check for NaN/None/Inf ------------------------------------------

def _is_bad_numeric(value: Any) -> bool:
    """Return True if value is None, NaN, or Inf."""
    if value is None:
        return True
    if isinstance(value, float):
        return math.isnan(value) or math.isinf(value)
    return False


# -- PDF 1 (Momentum) validators ----------------------------------------------

_MOMENTUM_INDICATOR_KEYS = {
    "price", "rsi", "macd", "macd_sig", "macd_hist",
    "atr", "atr_pct", "bb_upper", "bb_mid", "bb_lower",
    "pct_b", "bb_width", "adx", "rvol",
    "ema9", "ema20", "ema50", "ema200",
    "chg_1d", "chg_5d", "vol_20d",
}

_MOMENTUM_SCORE_RANGE = (0.0, 100.0)


def validate_momentum_data(
    data: dict[str, dict],
    scores: dict[str, tuple[float, float]],
    regimes: dict[str, str],
    macro: dict[str, dict],
    data_health: dict,
) -> tuple[bool, list[str]]:
    """Validate all data before PDF 1 (Momentum) generation.

    Args:
        data:        ticker -> indicator dict
        scores:      ticker -> (long_score, short_score)
        regimes:     ticker -> regime string
        macro:       macro_ticker -> {price, chg1d, chg5d}
        data_health: {total, valid, status}

    Returns:
        (passed, issues) -- passed is True only if zero issues found.
    """
    issues: list[str] = []

    # 1. Must have at least one ticker of data
    if not data:
        issues.append("QA_MOMENTUM: no ticker data loaded (data dict is empty)")

    # 2. Per-ticker indicator validation
    for ticker, ind in data.items():
        # 2a. Required keys present
        missing = _MOMENTUM_INDICATOR_KEYS - set(ind.keys())
        if missing:
            issues.append(f"QA_MOMENTUM: {ticker} missing indicator keys: {sorted(missing)}")

        # 2b. No NaN/None/Inf in numeric fields
        for key in _MOMENTUM_INDICATOR_KEYS:
            val = ind.get(key)
            if _is_bad_numeric(val):
                issues.append(f"QA_MOMENTUM: {ticker}.{key} is {val!r} (NaN/None/Inf)")

        # 2c. Price must be positive (zero price = division-by-zero downstream)
        price = ind.get("price")
        if isinstance(price, (int, float)) and price <= 0:
            issues.append(f"QA_MOMENTUM: {ticker} price={price} (must be > 0)")

        # 2d. RSI should be 0-100
        rsi = ind.get("rsi")
        if isinstance(rsi, (int, float)) and (rsi < 0 or rsi > 100):
            issues.append(f"QA_MOMENTUM: {ticker} RSI={rsi} outside [0, 100]")

        # 2e. ADX should be 0-100
        adx = ind.get("adx")
        if isinstance(adx, (int, float)) and (adx < 0 or adx > 100):
            issues.append(f"QA_MOMENTUM: {ticker} ADX={adx} outside [0, 100]")

    # 3. Score validation
    for ticker, score_pair in scores.items():
        if not isinstance(score_pair, (tuple, list)) or len(score_pair) != 2:
            issues.append(f"QA_MOMENTUM: {ticker} score is not a (long, short) pair: {score_pair!r}")
            continue
        long_s, short_s = score_pair
        for label, val in [("long", long_s), ("short", short_s)]:
            if _is_bad_numeric(val):
                issues.append(f"QA_MOMENTUM: {ticker} {label}_score is {val!r}")
            elif not (_MOMENTUM_SCORE_RANGE[0] <= val <= _MOMENTUM_SCORE_RANGE[1]):
                issues.append(
                    f"QA_MOMENTUM: {ticker} {label}_score={val} outside "
                    f"[{_MOMENTUM_SCORE_RANGE[0]}, {_MOMENTUM_SCORE_RANGE[1]}]"
                )

    # 4. Regime validation
    for ticker, regime in regimes.items():
        if not regime or regime not in _ALL_VALID_REGIMES:
            issues.append(f"QA_MOMENTUM: {ticker} regime={regime!r} not in valid taxonomy")

    # 5. Data health check
    if data_health:
        status = data_health.get("status")
        if status == "FAIL":
            issues.append(f"QA_MOMENTUM: data_health status is FAIL (valid={data_health.get('valid')}/{data_health.get('total')})")

    if issues:
        logger.warning("PDF QA GATE (Momentum): %d issue(s) found", len(issues))
        for issue in issues:
            logger.warning("  %s", issue)
    else:
        logger.info("PDF QA GATE (Momentum): PASSED -- all checks clean")

    return len(issues) == 0, issues


# -- PDF 2 (Risk) validators --------------------------------------------------

_RISK_REQUIRED_KEYS = {
    "ticker", "leverage", "last_close", "atr_14", "atr_pct",
    "vol_decay_score", "liquidity_score", "regime", "risk_score",
    "rsi_14", "rvol",
}

_RISK_SCORE_FIELDS = {"vol_decay_score", "liquidity_score", "risk_score"}

_RISK_VALID_REGIMES = VALID_5_STATE | {"NEUTRAL", "UNKNOWN"}


def validate_risk_data(
    stats: dict[str, dict[str, Any]],
    vix_data: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Validate all data before PDF 2 (Risk) generation.

    Args:
        stats:    ticker -> risk metrics dict
        vix_data: VIX/SPX regime dict

    Returns:
        (passed, issues)
    """
    issues: list[str] = []

    # 1. Must have at least one ticker
    if not stats:
        issues.append("QA_RISK: no ticker stats loaded (stats dict is empty)")

    # 2. Per-ticker validation
    for ticker, row in stats.items():
        # 2a. Required keys present
        missing = _RISK_REQUIRED_KEYS - set(row.keys())
        if missing:
            issues.append(f"QA_RISK: {ticker} missing keys: {sorted(missing)}")

        # 2b. No NaN/None/Inf in numeric fields
        for key in _RISK_REQUIRED_KEYS - {"ticker", "regime"}:
            val = row.get(key)
            if _is_bad_numeric(val):
                issues.append(f"QA_RISK: {ticker}.{key} is {val!r} (NaN/None/Inf)")

        # 2c. Score fields must be in [0, 100]
        for score_key in _RISK_SCORE_FIELDS:
            val = row.get(score_key)
            if isinstance(val, (int, float)) and (val < 0 or val > 100):
                issues.append(f"QA_RISK: {ticker} {score_key}={val} outside [0, 100]")

        # 2d. last_close must not be zero (division-by-zero guard)
        last_close = row.get("last_close")
        if isinstance(last_close, (int, float)) and last_close == 0:
            # Zero close is acceptable only if data was unavailable (warnings will say NO DATA)
            warnings = row.get("warnings", [])
            if not any("NO DATA" in w for w in warnings):
                issues.append(f"QA_RISK: {ticker} last_close=0 (division-by-zero risk)")

        # 2e. Regime label valid
        regime = row.get("regime")
        if regime and regime not in _RISK_VALID_REGIMES:
            issues.append(f"QA_RISK: {ticker} regime={regime!r} not in valid 5-state taxonomy")

        # 2f. RSI within range
        rsi = row.get("rsi_14")
        if isinstance(rsi, (int, float)) and (rsi < 0 or rsi > 100):
            issues.append(f"QA_RISK: {ticker} rsi_14={rsi} outside [0, 100]")

    # 3. VIX data validation
    if not vix_data:
        issues.append("QA_RISK: vix_data is empty")
    else:
        vix_now = vix_data.get("vix_now")
        if _is_bad_numeric(vix_now):
            issues.append(f"QA_RISK: vix_now is {vix_now!r} (NaN/None/Inf)")
        elif isinstance(vix_now, (int, float)) and vix_now <= 0:
            issues.append(f"QA_RISK: vix_now={vix_now} (must be > 0)")

    if issues:
        logger.warning("PDF QA GATE (Risk): %d issue(s) found", len(issues))
        for issue in issues:
            logger.warning("  %s", issue)
    else:
        logger.info("PDF QA GATE (Risk): PASSED -- all checks clean")

    return len(issues) == 0, issues
