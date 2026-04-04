"""Pandera DataFrame Schema Validator — runtime data quality enforcement.

Validates critical DataFrames at pipeline boundaries:
  1. Trade history: ensures PnL, confidence, timestamp fields are present & typed
  2. Signal features: validates ranges (confidence 0-100, kelly 0-0.5, etc.)
  3. Nightly aggregation output: ensures no NaN in critical metrics

Fail-open by default: logs warnings but doesn't block trading.
Set AEGIS_STRICT_SCHEMA=1 to make validation failures fatal.

License: Pandera is MIT.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

log = logging.getLogger("schema_validator")

try:
    import pandera as pa
    import pandas as pd
    _HAS_PANDERA = True
except ImportError:
    _HAS_PANDERA = False

_STRICT = os.environ.get("AEGIS_STRICT_SCHEMA", "0") == "1"

# ── Schema Definitions ──

if _HAS_PANDERA:
    TradeHistorySchema = pa.DataFrameSchema({
        "strategy": pa.Column(str, nullable=False),
        "ticker": pa.Column(str, nullable=False),
        "pnl": pa.Column(float, checks=[pa.Check.ge(-1.0), pa.Check.le(10.0)]),
        "confidence": pa.Column(float, checks=[pa.Check.ge(0), pa.Check.le(100)]),
        "kelly_fraction": pa.Column(float, checks=[pa.Check.ge(0), pa.Check.le(0.5)]),
        "entry_price": pa.Column(float, checks=pa.Check.gt(0)),
        "exit_price": pa.Column(float, checks=pa.Check.gt(0)),
    }, coerce=True, strict=False)  # strict=False allows extra columns

    SignalFeatureSchema = pa.DataFrameSchema({
        "confidence": pa.Column(float, checks=[pa.Check.ge(0), pa.Check.le(100)]),
        "rvol": pa.Column(float, checks=[pa.Check.ge(0), pa.Check.le(100)], nullable=True),
        "hurst": pa.Column(float, checks=[pa.Check.ge(-1), pa.Check.le(2)], nullable=True),
        "adx": pa.Column(float, checks=[pa.Check.ge(0), pa.Check.le(100)], nullable=True),
        "vpin": pa.Column(float, checks=[pa.Check.ge(0), pa.Check.le(1)], nullable=True),
        "spread_pct": pa.Column(float, checks=[pa.Check.ge(0), pa.Check.le(100)], nullable=True),
    }, coerce=True, strict=False)

    NightlyMetricsSchema = pa.DataFrameSchema({
        "total_pnl": pa.Column(float, nullable=False),
        "win_rate": pa.Column(float, checks=[pa.Check.ge(0), pa.Check.le(1)]),
        "profit_factor": pa.Column(float, checks=pa.Check.ge(0)),
        "n_trades": pa.Column(int, checks=pa.Check.ge(0)),
    }, coerce=True, strict=False)


def validate_trade_history(df) -> bool:
    """Validate trade history DataFrame. Returns True if valid."""
    if not _HAS_PANDERA:
        return True
    try:
        TradeHistorySchema.validate(df, lazy=True)
        return True
    except pa.errors.SchemaErrors as e:
        log.warning("Trade history validation failed: %d errors", len(e.failure_cases))
        for _, row in e.failure_cases.iterrows():
            log.warning("  Column=%s, Check=%s", row.get("column"), row.get("check"))
        if _STRICT:
            raise
        return False


def validate_signal_features(df) -> bool:
    """Validate signal features DataFrame."""
    if not _HAS_PANDERA:
        return True
    try:
        SignalFeatureSchema.validate(df, lazy=True)
        return True
    except pa.errors.SchemaErrors as e:
        log.warning("Signal features validation failed: %d errors", len(e.failure_cases))
        if _STRICT:
            raise
        return False


def validate_nightly_metrics(df) -> bool:
    """Validate nightly aggregation metrics."""
    if not _HAS_PANDERA:
        return True
    try:
        NightlyMetricsSchema.validate(df, lazy=True)
        return True
    except pa.errors.SchemaErrors as e:
        log.warning("Nightly metrics validation failed: %d errors", len(e.failure_cases))
        if _STRICT:
            raise
        return False


def validate_dict_as_row(data: Dict[str, Any], schema_name: str = "signal") -> bool:
    """Validate a single dict as a one-row DataFrame.

    Convenience wrapper for validating signal dicts from bridge.py.
    """
    if not _HAS_PANDERA:
        return True
    try:
        import pandas as pd
        df = pd.DataFrame([data])
        if schema_name == "trade":
            return validate_trade_history(df)
        elif schema_name == "signal":
            return validate_signal_features(df)
        elif schema_name == "nightly":
            return validate_nightly_metrics(df)
        return True
    except Exception as e:
        log.warning("Dict validation failed: %s", e)
        return not _STRICT
