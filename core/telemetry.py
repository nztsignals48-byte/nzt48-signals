"""
NZT-48 V9.5 Feature Telemetry Buffer
=====================================
Rich ML training data snapshot pipeline.

Captures per-trade feature snapshots at entry time and buffers them in Redis.
When a trade closes, the snapshot is retrieved and merged into the outcome record,
giving the ML meta-model 20+ features to learn from (vs. 5 previously).

Captured features:
  Microstructure: VPIN, OFI, micro-price, spread_bps, spread_momentum
  Sizing:         kelly_fraction, final_position_size, all 8 sizer scalars
  Hawkes:         intensity, baseline
  Regime:         label, confidence, VIX
  Temporal:       hour_of_day, day_of_week, time_window

Redis key scheme:
  nzt:telemetry:{signal_id}  — JSON feature snapshot (TTL 7 days)

References:
  Easley, Lopez de Prado & O'Hara (2012) — VPIN
  Cont, Kukanov & Stoikov (2014) — OFI
  Stoikov (2017) — Micro-price
  Hawkes (1971) — Self-exciting point process
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.state_manager import StateManager

logger = logging.getLogger("nzt48.telemetry")

# Redis key prefix and TTL
_TELEMETRY_PREFIX = "nzt:telemetry:"
_TELEMETRY_TTL = 7 * 24 * 3600  # 7 days


@dataclass
class FeatureSnapshot:
    """Complete feature snapshot at signal entry time.

    Every field defaults to 0.0/empty so missing data never blocks execution.
    """
    # ── Microstructure ─────────────────────────────────────────
    vpin: float = 0.0
    ofi: float = 0.0
    micro_price: float = 0.0
    bid_ask_spread_bps: float = 0.0
    spread_momentum: float = 0.0       # 5-bar spread change (Stoikov 2017)

    # ── Sizing ─────────────────────────────────────────────────
    kelly_fraction: float = 0.0
    final_position_size: int = 0

    # ── Dynamic sizer 8-factor scalars (qualification/dynamic_sizer.py) ──
    regime_scalar: float = 1.0
    rvol_scalar: float = 1.0
    confluence_scalar: float = 1.0
    time_scalar: float = 1.0
    correlation_scalar: float = 1.0
    regime_stability_scalar: float = 1.0
    earnings_scalar: float = 1.0
    leverage_scalar: float = 1.0

    # ── Hawkes self-excitation ─────────────────────────────────
    hawkes_intensity: float = 0.0
    hawkes_baseline: float = 0.0

    # ── Regime ─────────────────────────────────────────────────
    regime: str = ""
    regime_confidence: float = 0.0
    vix: float = 0.0

    # ── Temporal context ───────────────────────────────────────
    hour_of_day: int = 0
    day_of_week: int = 0
    time_window: str = ""

    def to_dict(self) -> dict:
        """Serialize to dict for Redis storage and outcomes merging."""
        return asdict(self)


class TelemetryBuffer:
    """Redis-backed feature snapshot buffer.

    Usage:
        # At trade entry:
        snapshot = telemetry.capture(signal_id, ticker, vpin=..., ofi=..., ...)

        # At trade close:
        entry_features = telemetry.retrieve(signal_id)
        if entry_features:
            outcome_record.update(entry_features)
    """

    def __init__(self, state_manager: StateManager) -> None:
        self._sm = state_manager

    async def capture(
        self,
        signal_id: str,
        ticker: str,
        *,
        # Microstructure
        vpin: float = 0.0,
        ofi: float = 0.0,
        micro_price: float = 0.0,
        bid_ask_spread_bps: float = 0.0,
        spread_momentum: float = 0.0,
        # Sizing
        kelly_fraction: float = 0.0,
        position_size: int = 0,
        # Sizer factors
        regime_scalar: float = 1.0,
        rvol_scalar: float = 1.0,
        confluence_scalar: float = 1.0,
        time_scalar: float = 1.0,
        correlation_scalar: float = 1.0,
        regime_stability_scalar: float = 1.0,
        earnings_scalar: float = 1.0,
        leverage_scalar: float = 1.0,
        # Hawkes
        hawkes_intensity: float = 0.0,
        hawkes_baseline: float = 0.0,
        # Regime
        regime: str = "",
        regime_confidence: float = 0.0,
        vix: float = 0.0,
        # Temporal
        hour_of_day: int = 0,
        day_of_week: int = 0,
        time_window: str = "",
    ) -> FeatureSnapshot:
        """Capture complete feature snapshot and buffer in Redis."""
        snapshot = FeatureSnapshot(
            vpin=vpin,
            ofi=ofi,
            micro_price=micro_price,
            bid_ask_spread_bps=bid_ask_spread_bps,
            spread_momentum=spread_momentum,
            kelly_fraction=kelly_fraction,
            final_position_size=position_size,
            regime_scalar=regime_scalar,
            rvol_scalar=rvol_scalar,
            confluence_scalar=confluence_scalar,
            time_scalar=time_scalar,
            correlation_scalar=correlation_scalar,
            regime_stability_scalar=regime_stability_scalar,
            earnings_scalar=earnings_scalar,
            leverage_scalar=leverage_scalar,
            hawkes_intensity=hawkes_intensity,
            hawkes_baseline=hawkes_baseline,
            regime=regime,
            regime_confidence=regime_confidence,
            vix=vix,
            hour_of_day=hour_of_day,
            day_of_week=day_of_week,
            time_window=time_window,
        )

        # Buffer in Redis with TTL
        await self._sm.set_json(
            f"{_TELEMETRY_PREFIX}{signal_id}",
            snapshot.to_dict(),
            ttl=_TELEMETRY_TTL,
        )

        logger.debug(
            "TELEMETRY captured: %s %s VPIN=%.4f OFI=%.2f Kelly=%.3f size=%d",
            signal_id, ticker, vpin, ofi, kelly_fraction, position_size,
        )

        return snapshot

    async def retrieve(self, signal_id: str) -> Optional[dict]:
        """Retrieve buffered snapshot from Redis for outcome enrichment."""
        return await self._sm.get_json(f"{_TELEMETRY_PREFIX}{signal_id}")
