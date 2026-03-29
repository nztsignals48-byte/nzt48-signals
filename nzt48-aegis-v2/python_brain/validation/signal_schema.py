"""Book 207 — NormalizedSignal Schema Validation.

Every signal from bridge.py must pass through this schema before being sent
to the Rust engine. This prevents malformed data (NaN, missing fields, wrong
types) from reaching the risk arbiter.

Design:
- Pure dataclass, no dependencies outside stdlib
- validate() raises ValueError on bad data (bridge.py catches → no_signal)
- from_dict() factory for converting raw signal dicts

Fields:
  signal_id:      Unique identifier (auto-generated if missing)
  source:         Strategy name (e.g. "S2_Reversion", "VanguardSniper")
  instrument:     Ticker symbol (e.g. "VUSA.L")
  ticker_id:      Integer ticker ID from Rust
  direction:      "Long" or "Short"
  confidence:     Integer 0-100
  kelly_fraction: Float 0.001-0.35
  shares:         Positive integer
  price:          Current price (positive float)
  expected_return: Optional estimated return (from strategy)
  risk_score:     Optional 0-100 risk assessment
  timestamp_ns:   Nanosecond-precision Unix timestamp
  expiry_ns:      Signal expiry timestamp (optional, default: +5 min)

Usage:
    from python_brain.validation.signal_schema import NormalizedSignal

    sig = NormalizedSignal.from_dict(raw_signal)
    sig.validate()  # Raises ValueError on invalid data
    return sig.to_dict()  # Clean dict for Rust
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class NormalizedSignal:
    """Validated signal ready for Rust engine consumption."""

    # Required fields
    signal_id: str = ""
    source: str = ""
    instrument: str = ""
    ticker_id: int = 0
    direction: str = ""
    confidence: int = 0
    kelly_fraction: float = 0.0
    shares: int = 0
    price: float = 0.0

    # Optional enrichment
    expected_return: Optional[float] = None
    risk_score: Optional[int] = None
    timestamp_ns: int = 0
    expiry_ns: int = 0

    # Passthrough fields (bridge.py adds many extra fields for Rust)
    _extra: Dict[str, Any] = field(default_factory=dict, repr=False)

    # Validation bounds
    _MIN_CONFIDENCE: int = field(default=0, init=False, repr=False)
    _MAX_CONFIDENCE: int = field(default=100, init=False, repr=False)
    _MIN_KELLY: float = field(default=0.0, init=False, repr=False)
    _MAX_KELLY: float = field(default=0.35, init=False, repr=False)
    _VALID_DIRECTIONS: tuple = field(default=("Long", "Short"), init=False, repr=False)
    _DEFAULT_EXPIRY_NS: int = field(default=5 * 60 * 1_000_000_000, init=False, repr=False)  # 5 min

    def validate(self) -> None:
        """Validate all fields. Raises ValueError on invalid data."""
        errors = []

        # Direction
        if self.direction not in self._VALID_DIRECTIONS:
            errors.append(f"direction={self.direction!r} not in {self._VALID_DIRECTIONS}")

        # Confidence
        if not isinstance(self.confidence, int):
            try:
                self.confidence = int(self.confidence)
            except (TypeError, ValueError):
                errors.append(f"confidence={self.confidence!r} not convertible to int")
        if self.confidence < self._MIN_CONFIDENCE or self.confidence > self._MAX_CONFIDENCE:
            errors.append(f"confidence={self.confidence} outside [{self._MIN_CONFIDENCE}, {self._MAX_CONFIDENCE}]")

        # Kelly
        if math.isnan(self.kelly_fraction) or math.isinf(self.kelly_fraction):
            errors.append(f"kelly_fraction={self.kelly_fraction} is NaN/Inf")
        elif self.kelly_fraction < self._MIN_KELLY or self.kelly_fraction > self._MAX_KELLY:
            errors.append(f"kelly_fraction={self.kelly_fraction} outside [{self._MIN_KELLY}, {self._MAX_KELLY}]")

        # Shares (0 allowed for Apex signals where Rust does sizing)
        if self.shares < 0:
            errors.append(f"shares={self.shares} must be >= 0")

        # Price
        if self.price <= 0 or math.isnan(self.price) or math.isinf(self.price):
            errors.append(f"price={self.price} must be positive finite number")

        # Source
        if not self.source:
            errors.append("source (strategy name) is empty")

        # Ticker ID
        if self.ticker_id < 0:
            errors.append(f"ticker_id={self.ticker_id} must be >= 0")

        if errors:
            raise ValueError(f"NormalizedSignal validation failed: {'; '.join(errors)}")

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> NormalizedSignal:
        """Create from a raw signal dict (as produced by bridge.py).

        Maps bridge.py field names to schema fields, preserving extras.
        """
        now_ns = int(time.time() * 1_000_000_000)

        # Map known fields
        known_keys = {
            "signal_id", "source", "instrument", "ticker_id", "direction",
            "confidence", "kelly_fraction", "shares", "price",
            "expected_return", "risk_score", "timestamp_ns", "expiry_ns",
        }

        # Defensive: NaN floats → safe defaults before int() conversion
        def _safe_float(v, default=0.0):
            try:
                f = float(v)
                return default if (math.isnan(f) or math.isinf(f)) else f
            except (TypeError, ValueError):
                return default

        def _safe_int(v, default=0):
            try:
                f = float(v)
                return default if (math.isnan(f) or math.isinf(f)) else int(f)
            except (TypeError, ValueError):
                return default

        sig = cls(
            signal_id=str(d.get("signal_id", f"sig_{d.get('ticker_id', 0)}_{now_ns}")),
            source=d.get("strategy", d.get("source", "")),
            instrument=d.get("symbol", d.get("instrument", "")),
            ticker_id=_safe_int(d.get("ticker_id", 0)),
            direction=d.get("direction", ""),
            confidence=_safe_int(d.get("confidence", 0)),
            kelly_fraction=_safe_float(d.get("kelly_fraction", 0)),
            shares=_safe_int(d.get("shares", 0)),
            price=_safe_float(d.get("price", d.get("last", 0))),
            expected_return=d.get("expected_return"),
            risk_score=d.get("risk_score"),
            timestamp_ns=_safe_int(d.get("timestamp_ns", d.get("timestamp", now_ns))),
            expiry_ns=_safe_int(d.get("expiry_ns", 0)),
        )

        # Default expiry: 5 min from now
        if sig.expiry_ns == 0:
            sig.expiry_ns = sig.timestamp_ns + sig._DEFAULT_EXPIRY_NS

        # Collect all extra fields for passthrough to Rust
        # Exclude keys we explicitly handle in to_dict() to avoid double-writes
        _handled = known_keys | {"type", "strategy", "symbol", "last", "timestamp"}
        for k, v in d.items():
            if k not in _handled:
                sig._extra[k] = v

        return sig

    def to_dict(self) -> Dict[str, Any]:
        """Export as dict for JSON serialization to Rust.

        Merges validated schema fields with passthrough extras.
        Preserves the "type": "signal" and original field names Rust expects.
        """
        out = {
            "type": "signal",
            "signal_id": self.signal_id,
            "ticker_id": self.ticker_id,
            "direction": self.direction,
            "confidence": self.confidence,
            "kelly_fraction": self.kelly_fraction,
            "shares": self.shares,
            "strategy": self.source,
            "price": self.price,
            "timestamp_ns": self.timestamp_ns,
            "expiry_ns": self.expiry_ns,
        }
        if self.instrument:
            out["symbol"] = self.instrument
        if self.expected_return is not None:
            out["expected_return"] = self.expected_return
        if self.risk_score is not None:
            out["risk_score"] = self.risk_score

        # Merge passthrough fields (bridge.py extras like rsi, ibs, vpin, etc.)
        # Sanitize NaN/Inf floats → None (invalid JSON otherwise)
        for k, v in self._extra.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                out[k] = None
            else:
                out[k] = v
        return out
