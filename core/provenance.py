"""
NZT-48 Trading System -- Provenance & Freshness Tracking (W3)
===============================================================
Ensures every data field in the system carries provider/as_of/TTL metadata
and that stale data is detected before it reaches downstream consumers.

Key problem solved: stale VIX from close being used in premarket regime
classification. Every data field now has an enforceable TTL.

Feature flag: provenance_tracking in settings.yaml (feature_flags section).
When disabled, all checks are no-ops that return "fresh".

Components:
    TTL_DEFAULTS        Per data-type TTL configuration (seconds).
    FreshnessChecker    Evaluates whether a ProvenanceEnvelope is stale.
    ProvenanceRegistry  Thread-safe registry of all active envelopes,
                        with bulk staleness scanning.

Usage:
    from core.provenance import FreshnessChecker, ProvenanceRegistry
    registry = ProvenanceRegistry()
    registry.register("vix", value=18.5, provider="yfinance")
    report = registry.check_all()
    # report = {"total": 1, "fresh": 1, "stale": 0, "stale_fields": []}

    # Or check a single field:
    envelope = registry.get("vix")
    if not FreshnessChecker.is_fresh(envelope):
        logger.warning("VIX data is stale!")
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

from core.schemas import ProvenanceEnvelope

logger = logging.getLogger("nzt48.core.provenance")


# ---------------------------------------------------------------------------
# TTL Defaults (seconds) -- per data type
# ---------------------------------------------------------------------------
# These are the maximum ages before data is considered stale.
# Keyed by logical field name prefix. Exact matches take priority,
# then prefix matches, then the global default.

TTL_DEFAULTS: dict[str, int] = {
    # Market indicators
    "vix":              300,    # 5 min -- VIX moves fast intraday
    "regime":           600,    # 10 min -- regime is smoothed, less volatile
    "spx_trend":        300,    # 5 min

    # Price data
    "price":            60,     # 1 min -- real-time price quotes
    "bars":             120,    # 2 min -- OHLCV bar data
    "volume":           60,     # 1 min

    # Technical indicators (derived from price)
    "rsi":              120,    # 2 min
    "macd":             120,    # 2 min
    "ema":              120,    # 2 min
    "atr":              300,    # 5 min -- ATR is slow-moving
    "adx":              300,    # 5 min
    "rvol":             120,    # 2 min
    "vwap":             60,     # 1 min

    # Structural / slow-changing
    "data_health":      600,    # 10 min
    "sector_rotation":  1800,   # 30 min
    "correlation":      1800,   # 30 min
    "holdings":         3600,   # 1 hour

    # Scores and signals
    "score":            300,    # 5 min
    "signal":           300,    # 5 min
    "exit_score":       120,    # 2 min -- exit urgency needs to be fresh

    # System meta
    "scan_health":      120,    # 2 min
    "system_state":     300,    # 5 min
}

# Fallback TTL if no match is found in TTL_DEFAULTS
_DEFAULT_TTL: int = 300  # 5 min


def get_ttl_for_field(field_name: str) -> int:
    """Look up the TTL for a given field name.

    Matching order:
        1. Exact match on full field_name (e.g. "vix")
        2. Prefix match on first component (e.g. "price.QQQ3.L" -> "price")
        3. Global default (_DEFAULT_TTL)

    Args:
        field_name: The logical field name.

    Returns:
        TTL in seconds.
    """
    # Exact match
    if field_name in TTL_DEFAULTS:
        return TTL_DEFAULTS[field_name]

    # Prefix match (first dot-separated component)
    prefix = field_name.split(".")[0] if "." in field_name else ""
    if prefix and prefix in TTL_DEFAULTS:
        return TTL_DEFAULTS[prefix]

    return _DEFAULT_TTL


# ---------------------------------------------------------------------------
# FreshnessChecker -- stateless utility
# ---------------------------------------------------------------------------

class FreshnessChecker:
    """Stateless utility to evaluate freshness of ProvenanceEnvelopes.

    All methods are classmethods / staticmethods so no instantiation needed.
    When the feature flag is off, all checks return True (fresh).
    """

    _enabled: bool = False  # Set by ProvenanceRegistry or caller

    @classmethod
    def enable(cls) -> None:
        cls._enabled = True

    @classmethod
    def disable(cls) -> None:
        cls._enabled = False

    @classmethod
    def is_enabled(cls) -> bool:
        return cls._enabled

    @classmethod
    def is_fresh(cls, envelope: ProvenanceEnvelope, now_epoch: float = 0.0) -> bool:
        """Check if a single envelope's data is within its TTL.

        When provenance tracking is disabled, always returns True.

        Args:
            envelope:   The ProvenanceEnvelope to check.
            now_epoch:  Current time as unix epoch. If 0, uses time.time().

        Returns:
            True if fresh (or tracking disabled), False if stale.
        """
        if not cls._enabled:
            return True
        return envelope.is_fresh(now_epoch)

    @classmethod
    def check_staleness(
        cls,
        envelope: ProvenanceEnvelope,
        now_epoch: float = 0.0,
    ) -> dict:
        """Return detailed staleness info for a single envelope.

        Returns:
            dict with keys: field_name, provider, age_seconds, ttl_seconds,
                            is_fresh, stale_by_seconds (0 if fresh).
        """
        if now_epoch <= 0.0:
            now_epoch = time.time()

        age = envelope.age_seconds(now_epoch)
        fresh = cls.is_fresh(envelope, now_epoch)
        stale_by = max(0.0, age - envelope.ttl_seconds) if not fresh else 0.0

        return {
            "field_name": envelope.field_name,
            "provider": envelope.provider,
            "age_seconds": round(age, 1),
            "ttl_seconds": envelope.ttl_seconds,
            "is_fresh": fresh,
            "stale_by_seconds": round(stale_by, 1),
        }


# ---------------------------------------------------------------------------
# ProvenanceRegistry -- thread-safe store of all active envelopes
# ---------------------------------------------------------------------------

class ProvenanceRegistry:
    """Thread-safe registry of ProvenanceEnvelopes for all active data fields.

    Central place where producers register their data with provenance,
    and consumers can check bulk freshness before using the data.

    Usage:
        registry = ProvenanceRegistry()
        registry.register("vix", value=18.5, provider="yfinance")
        registry.register("price.QQQ3.L", value=42.10, provider="yfinance")

        report = registry.check_all()
        # {"total": 2, "fresh": 2, "stale": 0, "stale_fields": []}

        stale = registry.get_stale_fields()
        # [] if all fresh, or list of field names
    """

    def __init__(self, enabled: bool = False) -> None:
        """
        Args:
            enabled: Whether provenance tracking is active.
                     When False, register() still stores envelopes but
                     all freshness checks return True.
        """
        self._lock = threading.Lock()
        self._envelopes: dict[str, ProvenanceEnvelope] = {}
        self._enabled = enabled

        if enabled:
            FreshnessChecker.enable()
            logger.info("ProvenanceRegistry initialised (ENABLED)")
        else:
            FreshnessChecker.disable()
            logger.info("ProvenanceRegistry initialised (DISABLED -- feature flag off)")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """Toggle provenance tracking at runtime (hot-reload safe)."""
        with self._lock:
            self._enabled = enabled
            if enabled:
                FreshnessChecker.enable()
            else:
                FreshnessChecker.disable()
            logger.info("Provenance tracking %s", "ENABLED" if enabled else "DISABLED")

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        field_name: str,
        *,
        value: Any = None,
        provider: str = "unknown",
        as_of_epoch: float = 0.0,
        ttl_seconds: Optional[int] = None,
    ) -> ProvenanceEnvelope:
        """Register or update a data field with provenance metadata.

        Args:
            field_name:     Logical name (e.g. "vix", "price.QQQ3.L").
            value:          The actual data value.
            provider:       Data source identifier.
            as_of_epoch:    When the data was fetched (epoch seconds).
                            If 0, uses current time.
            ttl_seconds:    Override TTL. If None, looked up from TTL_DEFAULTS.

        Returns:
            The created/updated ProvenanceEnvelope.
        """
        now = time.time()
        if as_of_epoch <= 0.0:
            as_of_epoch = now

        if ttl_seconds is None:
            ttl_seconds = get_ttl_for_field(field_name)

        as_of_iso = datetime.fromtimestamp(as_of_epoch, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )

        envelope = ProvenanceEnvelope(
            field_name=field_name,
            provider=provider,
            as_of=as_of_iso,
            as_of_epoch=as_of_epoch,
            ttl_seconds=ttl_seconds,
            value=value,
            stale=not self._is_fresh_internal(as_of_epoch, ttl_seconds, now),
        )

        with self._lock:
            self._envelopes[field_name] = envelope

        logger.debug(
            "Registered provenance: %s (provider=%s, ttl=%ds, age=%.1fs)",
            field_name, provider, ttl_seconds, now - as_of_epoch,
        )
        return envelope

    def _is_fresh_internal(self, as_of_epoch: float, ttl_seconds: int, now: float) -> bool:
        """Internal freshness check (no feature-flag gating)."""
        return (now - as_of_epoch) < ttl_seconds

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(self, field_name: str) -> Optional[ProvenanceEnvelope]:
        """Get the envelope for a specific field."""
        with self._lock:
            return self._envelopes.get(field_name)

    def get_value(self, field_name: str, default: Any = None) -> Any:
        """Get just the value from an envelope, with optional default.

        If provenance tracking is enabled and the data is stale,
        returns the default instead (fail-closed).
        """
        with self._lock:
            env = self._envelopes.get(field_name)
            if env is None:
                return default
            if self._enabled and not env.is_fresh():
                logger.warning(
                    "Stale data blocked for '%s' (age=%.1fs, ttl=%ds)",
                    field_name, env.age_seconds(), env.ttl_seconds,
                )
                return default
            return env.value

    def get_all(self) -> dict[str, ProvenanceEnvelope]:
        """Return a copy of all registered envelopes."""
        with self._lock:
            return dict(self._envelopes)

    # ------------------------------------------------------------------
    # Bulk freshness checks
    # ------------------------------------------------------------------

    def check_all(self, now_epoch: float = 0.0) -> dict:
        """Run freshness checks on all registered envelopes.

        Returns:
            dict with keys:
                total (int):            Number of registered fields.
                fresh (int):            Number of fresh fields.
                stale (int):            Number of stale fields.
                stale_fields (list):    List of dicts with staleness details.
                all_fresh (bool):       True if everything is fresh.
                enabled (bool):         Whether tracking is active.
        """
        if now_epoch <= 0.0:
            now_epoch = time.time()

        with self._lock:
            envelopes = list(self._envelopes.values())

        total = len(envelopes)
        stale_details = []

        for env in envelopes:
            info = FreshnessChecker.check_staleness(env, now_epoch)
            if not info["is_fresh"] and self._enabled:
                stale_details.append(info)
                # Update the envelope's stale flag
                env.stale = True
            else:
                env.stale = False

        fresh_count = total - len(stale_details)

        if stale_details:
            logger.warning(
                "Provenance check: %d/%d fields stale: %s",
                len(stale_details),
                total,
                [d["field_name"] for d in stale_details],
            )

        return {
            "total": total,
            "fresh": fresh_count,
            "stale": len(stale_details),
            "stale_fields": stale_details,
            "all_fresh": len(stale_details) == 0,
            "enabled": self._enabled,
        }

    def get_stale_fields(self, now_epoch: float = 0.0) -> list[str]:
        """Return list of field names that are currently stale.

        When provenance tracking is disabled, always returns [].
        """
        if not self._enabled:
            return []

        report = self.check_all(now_epoch)
        return [d["field_name"] for d in report["stale_fields"]]

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Remove all registered envelopes."""
        with self._lock:
            self._envelopes.clear()
        logger.debug("ProvenanceRegistry cleared")

    def remove(self, field_name: str) -> None:
        """Remove a single field's envelope."""
        with self._lock:
            self._envelopes.pop(field_name, None)

    def summary(self) -> dict:
        """Return a compact summary suitable for logging or dashboard display."""
        report = self.check_all()
        return {
            "provenance_enabled": self._enabled,
            "fields_tracked": report["total"],
            "fields_fresh": report["fresh"],
            "fields_stale": report["stale"],
            "stale_names": [d["field_name"] for d in report["stale_fields"]],
        }


# ---------------------------------------------------------------------------
# Module-level singleton (lazy init)
# ---------------------------------------------------------------------------

_registry: Optional[ProvenanceRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> ProvenanceRegistry:
    """Get or create the module-level ProvenanceRegistry singleton.

    Reads the feature flag from settings.yaml on first call.
    Thread-safe.
    """
    global _registry
    if _registry is not None:
        return _registry

    with _registry_lock:
        if _registry is not None:
            return _registry

        # Read feature flag
        enabled = False
        try:
            from config import get
            enabled = bool(get("feature_flags.provenance_tracking", False))
        except Exception as e:
            logger.warning("Could not read provenance_tracking feature flag: %s", e)

        _registry = ProvenanceRegistry(enabled=enabled)
        return _registry
