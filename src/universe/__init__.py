"""
Universe Perfection System
==========================
3-tier classification and optimization for asset selection.

Modules:
  - tiered_universe_scanner.py: 3-tier universe scanning (BLUE_CHIP/SPECIALIST/EXPANSION)
  - perfect_asset_optimizer.py: Quality-based asset whitelisting
  - orchestrator_integration.py: Integration with main orchestrator loop

Core Features:
  - BLUE_CHIP tier: 60s scan, 60% confidence, 5M+ volume, <10bps spread
  - SPECIALIST tier: 90s scan, 65% confidence, 1M+ volume, <20bps spread
  - EXPANSION tier: 180s scan, 70% confidence, 500k+ volume, <30bps spread
  - PerfectAssetOptimizer filters candidates via:
    - Tradeability (volume, spread, freshness, delisted)
    - Signal quality (accuracy >60%, reliability >75%)
    - Data quality (>90% complete, <5min stale)
    - Regime stability (no EXTREME volatility)

Integration with AEGIS V2:
  - Input: Market data from data feeds
  - Output: ranked assets per tier → early_detection_engine → position_sizer
  - Parallel threads with main trading loop (no dependencies)
  - All decisions logged to database asset_health table
"""

from .tiered_universe_scanner import (
    TieredUniverseScanner,
    AssetMetrics,
    RankedAsset,
    ScanResult,
)
from .perfect_asset_optimizer import (
    PerfectAssetOptimizer,
    AssetWhitelistEntry,
    OptimizationResult,
)

__all__ = [
    "TieredUniverseScanner",
    "PerfectAssetOptimizer",
    "AssetMetrics",
    "RankedAsset",
    "ScanResult",
    "AssetWhitelistEntry",
    "OptimizationResult",
]
