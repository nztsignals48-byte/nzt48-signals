"""
I-06: Apex Scout Module — RVOL anomaly scanner for universe expansion.
I-07: Data Cost Control — tiered scanning frequencies.
Scans beyond core 12 ETPs to discover high-RVOL opportunities.
"""
import logging
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
from collections import deque
from enum import Enum

logger = logging.getLogger(__name__)


class ScanTier(Enum):
    CORE = "core"       # 60s scan interval
    PEER = "peer"       # 5 min scan interval
    FULL_SCAN = "full"  # 30 min scan interval


@dataclass
class ScoutSignal:
    ticker: str
    underlying: str
    tier: ScanTier
    rvol_zscore: float
    adr_pct: float
    regime: str
    timestamp: datetime
    stranger_penalty: float  # Bayesian penalty for untested tickers


@dataclass
class ScanConfig:
    core_interval_s: int = 60
    peer_interval_s: int = 300
    full_interval_s: int = 1800
    # Regime-adaptive RVOL Z-thresholds
    rvol_z_thresholds: Dict[str, float] = field(default_factory=lambda: {
        "TRENDING_UP_STRONG": 2.0,
        "TRENDING_UP_MOD": 2.0,
        "RANGE_BOUND": 3.0,
        "TRENDING_DOWN_MOD": 2.5,
        "RISK_OFF": 3.5,
        "SHOCK": 999.0,  # disabled
    })


class ApexScout:
    """Asynchronous RVOL anomaly scanner for universe expansion.

    Scans 3 tiers at different frequencies:
    - CORE: main 12 ISA ETPs every 60s
    - PEER: related sector ETPs every 5 min
    - FULL_SCAN: entire LSE leveraged ETP universe every 30 min

    All Scout signals carry Bayesian Stranger Penalty (C-01).
    """

    def __init__(self, config: Optional[ScanConfig] = None, underlying_map: Optional[dict] = None):
        self._config = config or ScanConfig()
        self._underlying_map = underlying_map or {}
        self._universe: Dict[ScanTier, Set[str]] = {
            ScanTier.CORE: set(),
            ScanTier.PEER: set(),
            ScanTier.FULL_SCAN: set(),
        }
        self._last_scan: Dict[ScanTier, datetime] = {}
        self._signals: deque = deque(maxlen=500)
        self._running = False

    def set_universe(self, tier: ScanTier, tickers: Set[str]) -> None:
        self._universe[tier] = tickers
        logger.info("ApexScout: %s tier set to %d tickers", tier.value, len(tickers))

    def _should_scan(self, tier: ScanTier) -> bool:
        last = self._last_scan.get(tier)
        if last is None:
            return True
        intervals = {
            ScanTier.CORE: self._config.core_interval_s,
            ScanTier.PEER: self._config.peer_interval_s,
            ScanTier.FULL_SCAN: self._config.full_interval_s,
        }
        return (datetime.utcnow() - last).total_seconds() >= intervals[tier]

    def _get_rvol_threshold(self, regime: str) -> float:
        return self._config.rvol_z_thresholds.get(regime, 3.0)

    async def scan_tier(self, tier: ScanTier, regime: str,
                        price_data: Dict[str, dict]) -> List[ScoutSignal]:
        """Scan a tier for RVOL anomalies."""
        if not self._should_scan(tier):
            return []

        self._last_scan[tier] = datetime.utcnow()
        threshold = self._get_rvol_threshold(regime)
        signals = []

        for ticker in self._universe.get(tier, set()):
            data = price_data.get(ticker)
            if not data:
                continue
            rvol_z = data.get("rvol_zscore", 0.0)
            if rvol_z >= threshold:
                underlying = self._underlying_map.get(ticker, ticker)
                # Stranger penalty: lower for CORE, higher for FULL_SCAN
                penalty = {ScanTier.CORE: 1.0, ScanTier.PEER: 0.7, ScanTier.FULL_SCAN: 0.4}.get(tier, 0.5)
                sig = ScoutSignal(
                    ticker=ticker, underlying=underlying, tier=tier,
                    rvol_zscore=rvol_z, adr_pct=data.get("adr_pct", 0.0),
                    regime=regime, timestamp=datetime.utcnow(),
                    stranger_penalty=penalty,
                )
                signals.append(sig)
                self._signals.append(sig)
                logger.info("SCOUT: %s %s RVOL_Z=%.2f (threshold=%.1f, tier=%s)",
                           ticker, underlying, rvol_z, threshold, tier.value)
        return signals

    async def run_scan_cycle(self, regime: str, price_data: Dict[str, dict]) -> List[ScoutSignal]:
        """Run all eligible tier scans in one cycle."""
        all_signals = []
        for tier in ScanTier:
            signals = await self.scan_tier(tier, regime, price_data)
            all_signals.extend(signals)
        return all_signals

    def refresh_full_universe(self, all_tickers: Set[str], core_tickers: Set[str]) -> None:
        """Sunday night: refresh FULL_SCAN universe, filter to candidates."""
        peer = set()  # TODO: identify related sector ETPs
        full = all_tickers - core_tickers - peer
        self._universe[ScanTier.CORE] = core_tickers
        self._universe[ScanTier.PEER] = peer
        self._universe[ScanTier.FULL_SCAN] = full
        logger.info("ApexScout: universe refreshed — CORE=%d, PEER=%d, FULL=%d",
                    len(core_tickers), len(peer), len(full))

    def get_recent_signals(self, limit: int = 20) -> List[ScoutSignal]:
        return list(self._signals)[-limit:]
