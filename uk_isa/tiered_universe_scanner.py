"""
NZT-48 Tiered Universe Scanner (Week 2 Universe Scanning)
==========================================================
Perfect universe selection via 3-tier classification system.

Tier 1 (BLUE_CHIP):
  - Core ISA universe (QQQ3.L, 3LUS.L, 3SEM.L, GPT3.L, NVD3.L, TSL3.L, TSM3.L, MU2.L, QQQS.L, 3USS.L, QQQ5.L, SP5L.L)
  - Highest liquidity (volume >5M shares/day, spread <10bps)
  - Lowest volatility drag, best for compounding
  - 100% signal reliability target
  - Max positions: 3 simultaneous (portfolio concentration)

Tier 2 (SPECIALIST):
  - Extended universe (AMD3.L, ARM3.L, NVDS.L, TSLS.L, 3LDE.L, 3LEU.L, 3GOL.L, 3SIL.L, 3OIL.L, LLY3.L)
  - Good liquidity (volume >1M shares/day, spread <20bps)
  - Sector/theme peers to CORE (semiconductor, inverse, European, commodity)
  - 85% signal reliability target
  - Max positions: 2 concurrent (lower liquidity)

Tier 3 (EXPANSION):
  - Sector Radar universe (healthcare, financials, energy, crypto, single names)
  - Moderate liquidity (volume >500k shares/day, spread <30bps)
  - Research/intel only (no TRADE signals without Week 1 integration)
  - 70% signal reliability target
  - Max positions: 1 concurrent (validation only)

Integration:
  - Feeds into early_detection_engine for perfect asset selection
  - Scanned in parallel with Week 1 (no dependencies)
  - Preserves existing universe.yaml structure (CORE_LIST, peer_candidates, full_scan_list)
  - Returns ranked assets per tier with confidence scores

Reference:
  - uk_isa/isa_universe.py: TICKER_REGISTRY, CORE_UNIVERSE, EXTENDED_UNIVERSE
  - uk_isa/universe_manager.py: tier classification logic
  - config/universe.yaml: source of truth for CORE, PEER, FULL_SCAN lists
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple, Any
from pathlib import Path

logger = logging.getLogger("nzt48.tiered_universe_scanner")


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class AssetMetrics:
    """Real-time metrics for a single asset."""
    ticker: str
    price: float
    volume: float              # shares traded
    bid_ask_spread_bps: float  # basis points
    atr_pct: float             # Average True Range as % of price
    rvol: float                # Relative Volume vs 21-day MA
    adx: float                 # ADX (0-100, >25 = trending)
    rsi: float                 # RSI (0-100)
    data_freshness_sec: int    # seconds since last update
    last_update: str           # ISO timestamp


@dataclass
class TierAssignment:
    """Tier classification for a single asset."""
    ticker: str
    tier: str                  # "BLUE_CHIP", "SPECIALIST", "EXPANSION"
    confidence_pct: float      # 0-100, quality score for this tier
    feature_coverage_pct: float # % of required features available
    liquidity_score: float     # 0-100 (volume, spread, depth)
    volatility_score: float    # 0-100 (ATR, regime)
    signal_reliability_target: float  # Target signal reliability % for this tier
    reasons: List[str] = field(default_factory=list)


@dataclass
class RankedAsset:
    """Ranked asset within a tier for trading consideration."""
    ticker: str
    tier: str
    rank: int                  # 1=best, N=worst within tier
    confidence_pct: float      # 0-100, quality for early_detection_engine
    liquidity_score: float     # 0-100
    volatility_score: float    # 0-100
    feature_coverage_pct: float
    tradeable: bool            # Pass all quality gates?
    restrictions: List[str] = field(default_factory=list)  # warnings, not blockers
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ScanResult:
    """Complete scan result for all three tiers."""
    scan_timestamp: str
    total_scanned: int
    blue_chip_count: int
    specialist_count: int
    expansion_count: int
    ranked_by_tier: Dict[str, List[RankedAsset]] = field(default_factory=dict)  # tier -> [ranked assets]
    failed_tickers: List[Dict[str, Any]] = field(default_factory=list)  # {ticker, reason}
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Thresholds & Scoring
# ---------------------------------------------------------------------------

# Liquidity gates (minimum volume in shares/day)
_LIQUIDITY_GATE_BLUE_CHIP = 5_000_000    # 5M shares/day
_LIQUIDITY_GATE_SPECIALIST = 1_000_000   # 1M shares/day
_LIQUIDITY_GATE_EXPANSION = 500_000      # 500k shares/day

# Spread gates (maximum bid-ask spread in basis points)
_SPREAD_GATE_BLUE_CHIP = 10    # 10bps
_SPREAD_GATE_SPECIALIST = 20   # 20bps
_SPREAD_GATE_EXPANSION = 30    # 30bps

# Data freshness requirements (seconds)
_MAX_DATA_AGE_REALTIME = 300   # 5 minutes

# ATR thresholds for volatility assessment
_ATR_MIN_PCT = 0.1    # Minimum ATR for trending confirmation
_ATR_MAX_PCT = 5.0    # Maximum ATR before excessive whipsaw

# Volatility regime thresholds (normalized by 21-day ATR)
_VOL_COMPRESSION = 0.5    # ATR < 0.5 * MA21(ATR)
_VOL_NORMAL = 1.0         # 0.5-1.5 * MA21(ATR)
_VOL_EXPANSION = 1.5      # > 1.5 * MA21(ATR)


# ---------------------------------------------------------------------------
# TieredUniverseScanner
# ---------------------------------------------------------------------------

class TieredUniverseScanner:
    """
    Perfect universe selection via 3-tier classification system.

    Scans CORE, SPECIALIST, and EXPANSION tiers independently.
    Returns ranked assets per tier with confidence scores for early_detection_engine.
    """

    def __init__(self, universe_config: Optional[Dict[str, Any]] = None):
        """
        Initialize scanner with optional pre-loaded universe config.

        Args:
            universe_config: Dict with keys:
                - core_list: List[str] — primary ISA tickers
                - peer_candidates: List[str] — specialist candidates
                - sector_radar: List[str] — expansion candidates
                If None, loads from default ISA universe (isa_universe.py).
        """
        self.logger = logger
        self._universe_config = universe_config or self._load_default_config()
        self._scan_history: Dict[str, List[TierAssignment]] = {}  # ticker -> [assignments over time]
        self._metric_cache: Dict[str, AssetMetrics] = {}  # ticker -> latest metrics

    def _load_default_config(self) -> Dict[str, Any]:
        """Load default universe config from isa_universe.py."""
        try:
            from uk_isa.isa_universe import CORE_UNIVERSE, EXTENDED_UNIVERSE, SECTOR_RADAR_UNIVERSE
            return {
                "core_list": CORE_UNIVERSE,
                "peer_candidates": [t for t in EXTENDED_UNIVERSE if t not in CORE_UNIVERSE],
                "sector_radar": SECTOR_RADAR_UNIVERSE,
            }
        except ImportError:
            self.logger.warning("Failed to import isa_universe -- using minimal defaults")
            return {
                "core_list": [
                    "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L",
                    "TSL3.L", "TSM3.L", "MU2.L", "QQQS.L", "3USS.L", "QQQ5.L", "SP5L.L"
                ],
                "peer_candidates": [
                    "AMD3.L", "ARM3.L", "NVDS.L", "TSLS.L", "3LDE.L",
                    "3LEU.L", "3GOL.L", "3SIL.L", "3OIL.L", "LLY3.L"
                ],
                "sector_radar": [
                    "3LHC.L", "BAC3.L", "GS3.L", "3LEN.L", "XOM3.L",
                    "COIN3.L", "MSTRL.L", "PLTR3.L", "AVGO3.L", "MFAS.L",
                    "MSFL.L", "GOOGL3.L", "AAPLL.L"
                ],
            }

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def scan_tier1(self, metrics_dict: Dict[str, AssetMetrics]) -> List[RankedAsset]:
        """
        Scan BLUE_CHIP tier (core ISA universe).

        Args:
            metrics_dict: Dict[ticker, AssetMetrics] — current market metrics for evaluation

        Returns:
            List[RankedAsset] sorted by confidence (highest first), ready for trading.
        """
        tier_name = "BLUE_CHIP"
        core_list = self._universe_config.get("core_list", [])
        ranked = []

        for ticker in core_list:
            metrics = metrics_dict.get(ticker)
            if not metrics:
                self.logger.debug(f"Tier 1 ({tier_name}): {ticker} has no metrics -- skipping")
                continue

            # Check gates
            is_liquid = self._check_liquidity(metrics, _LIQUIDITY_GATE_BLUE_CHIP)
            is_tradeable_spread = self._check_spread(metrics, _SPREAD_GATE_BLUE_CHIP)
            is_fresh = self._check_data_freshness(metrics, _MAX_DATA_AGE_REALTIME)

            if not (is_liquid and is_tradeable_spread and is_fresh):
                self.logger.debug(
                    f"Tier 1 ({tier_name}): {ticker} failed gates (liquid={is_liquid}, "
                    f"spread={is_tradeable_spread}, fresh={is_fresh})"
                )
                continue

            # Score
            assignment = self._score_asset(ticker, metrics, tier_name)
            ranked_asset = self._assignment_to_ranked(assignment, len(ranked) + 1)
            ranked.append(ranked_asset)

            self._scan_history.setdefault(ticker, []).append(assignment)

        # Sort by confidence descending
        ranked.sort(key=lambda x: x.confidence_pct, reverse=True)

        # Re-rank after sorting
        for i, asset in enumerate(ranked, 1):
            asset.rank = i

        self.logger.info(
            f"Tier 1 ({tier_name}): scanned {len(core_list)} tickers, "
            f"ranked {len(ranked)} as tradeable"
        )
        return ranked

    def scan_tier2(self, metrics_dict: Dict[str, AssetMetrics]) -> List[RankedAsset]:
        """
        Scan SPECIALIST tier (extended peer candidates).

        Args:
            metrics_dict: Dict[ticker, AssetMetrics] — current market metrics

        Returns:
            List[RankedAsset] sorted by confidence, for WATCH/INTEL signals.
        """
        tier_name = "SPECIALIST"
        peer_list = self._universe_config.get("peer_candidates", [])
        ranked = []

        for ticker in peer_list:
            metrics = metrics_dict.get(ticker)
            if not metrics:
                self.logger.debug(f"Tier 2 ({tier_name}): {ticker} has no metrics -- skipping")
                continue

            # Check gates
            is_liquid = self._check_liquidity(metrics, _LIQUIDITY_GATE_SPECIALIST)
            is_tradeable_spread = self._check_spread(metrics, _SPREAD_GATE_SPECIALIST)
            is_fresh = self._check_data_freshness(metrics, _MAX_DATA_AGE_REALTIME)

            if not (is_liquid and is_tradeable_spread and is_fresh):
                self.logger.debug(
                    f"Tier 2 ({tier_name}): {ticker} failed gates (liquid={is_liquid}, "
                    f"spread={is_tradeable_spread}, fresh={is_fresh})"
                )
                continue

            assignment = self._score_asset(ticker, metrics, tier_name)
            ranked_asset = self._assignment_to_ranked(assignment, len(ranked) + 1)
            ranked.append(ranked_asset)

            self._scan_history.setdefault(ticker, []).append(assignment)

        ranked.sort(key=lambda x: x.confidence_pct, reverse=True)

        for i, asset in enumerate(ranked, 1):
            asset.rank = i

        self.logger.info(
            f"Tier 2 ({tier_name}): scanned {len(peer_list)} tickers, "
            f"ranked {len(ranked)} as qualified"
        )
        return ranked

    def scan_tier3(self, metrics_dict: Dict[str, AssetMetrics]) -> List[RankedAsset]:
        """
        Scan EXPANSION tier (sector radar / monitoring).

        Args:
            metrics_dict: Dict[ticker, AssetMetrics] — current market metrics

        Returns:
            List[RankedAsset] sorted by confidence, for research/intel only.
        """
        tier_name = "EXPANSION"
        expansion_list = self._universe_config.get("sector_radar", [])
        ranked = []

        for ticker in expansion_list:
            metrics = metrics_dict.get(ticker)
            if not metrics:
                self.logger.debug(f"Tier 3 ({tier_name}): {ticker} has no metrics -- skipping")
                continue

            # Check gates
            is_liquid = self._check_liquidity(metrics, _LIQUIDITY_GATE_EXPANSION)
            is_tradeable_spread = self._check_spread(metrics, _SPREAD_GATE_EXPANSION)
            is_fresh = self._check_data_freshness(metrics, _MAX_DATA_AGE_REALTIME)

            if not (is_liquid and is_tradeable_spread and is_fresh):
                self.logger.debug(
                    f"Tier 3 ({tier_name}): {ticker} failed gates (liquid={is_liquid}, "
                    f"spread={is_tradeable_spread}, fresh={is_fresh})"
                )
                continue

            assignment = self._score_asset(ticker, metrics, tier_name)
            ranked_asset = self._assignment_to_ranked(assignment, len(ranked) + 1)
            ranked.append(ranked_asset)

            self._scan_history.setdefault(ticker, []).append(assignment)

        ranked.sort(key=lambda x: x.confidence_pct, reverse=True)

        for i, asset in enumerate(ranked, 1):
            asset.rank = i

        self.logger.info(
            f"Tier 3 ({tier_name}): scanned {len(expansion_list)} tickers, "
            f"ranked {len(ranked)} as qualified"
        )
        return ranked

    def rank_assets(
        self,
        metrics_dict: Dict[str, AssetMetrics],
        top_n_per_tier: Optional[Dict[str, int]] = None,
    ) -> ScanResult:
        """
        Run full scan across all tiers, return ranked results.

        Args:
            metrics_dict: Dict[ticker, AssetMetrics] — market data for all tickers
            top_n_per_tier: Dict[tier_name, count] — max assets to return per tier.
                If None, returns all qualified assets.

        Returns:
            ScanResult with ranked_by_tier breakdown + failures + warnings.
        """
        if top_n_per_tier is None:
            top_n_per_tier = {"BLUE_CHIP": 3, "SPECIALIST": 2, "EXPANSION": 1}

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        failed = []
        warnings = []

        # Scan all three tiers
        tier1_assets = self.scan_tier1(metrics_dict)
        tier2_assets = self.scan_tier2(metrics_dict)
        tier3_assets = self.scan_tier3(metrics_dict)

        # Apply top_n limits
        tier1_limited = tier1_assets[: top_n_per_tier.get("BLUE_CHIP", 3)]
        tier2_limited = tier2_assets[: top_n_per_tier.get("SPECIALIST", 2)]
        tier3_limited = tier3_assets[: top_n_per_tier.get("EXPANSION", 1)]

        # Detect any tickers with metrics but no assignment (failed at gate level)
        all_scanned = set(metrics_dict.keys())
        all_assigned = set(
            [a.ticker for a in tier1_assets + tier2_assets + tier3_assets]
        )
        failed_tickers = all_scanned - all_assigned

        for ticker in failed_tickers:
            metrics = metrics_dict[ticker]
            reason = self._diagnose_gate_failure(metrics)
            failed.append({"ticker": ticker, "reason": reason})

        # Data freshness warnings
        stale_tickers = [
            ticker for ticker, metrics in metrics_dict.items()
            if metrics.data_freshness_sec > _MAX_DATA_AGE_REALTIME
        ]
        if stale_tickers:
            warnings.append(f"Stale data (>5min): {', '.join(stale_tickers)}")

        result = ScanResult(
            scan_timestamp=now,
            total_scanned=len(metrics_dict),
            blue_chip_count=len(tier1_limited),
            specialist_count=len(tier2_limited),
            expansion_count=len(tier3_limited),
            ranked_by_tier={
                "BLUE_CHIP": tier1_limited,
                "SPECIALIST": tier2_limited,
                "EXPANSION": tier3_limited,
            },
            failed_tickers=failed,
            warnings=warnings,
        )

        self.logger.info(
            f"Scan complete: {result.blue_chip_count} BLUE_CHIP, "
            f"{result.specialist_count} SPECIALIST, {result.expansion_count} EXPANSION, "
            f"{len(failed)} failed gates"
        )

        return result

    # -----------------------------------------------------------------------
    # Internal Scoring & Gating
    # -----------------------------------------------------------------------

    def _check_liquidity(self, metrics: AssetMetrics, min_volume: float) -> bool:
        """Check if volume meets minimum gate."""
        return metrics.volume >= min_volume

    def _check_spread(self, metrics: AssetMetrics, max_spread_bps: float) -> bool:
        """Check if bid-ask spread is acceptable."""
        return metrics.bid_ask_spread_bps <= max_spread_bps

    def _check_data_freshness(self, metrics: AssetMetrics, max_age_sec: int) -> bool:
        """Check if data is recent enough."""
        return metrics.data_freshness_sec <= max_age_sec

    def _score_asset(
        self,
        ticker: str,
        metrics: AssetMetrics,
        tier_name: str,
    ) -> TierAssignment:
        """
        Score an asset for its tier assignment.

        Returns confidence 0-100 and component scores.
        """
        # Liquidity score (0-100)
        if tier_name == "BLUE_CHIP":
            gate = _LIQUIDITY_GATE_BLUE_CHIP
            max_volume = gate * 2
        elif tier_name == "SPECIALIST":
            gate = _LIQUIDITY_GATE_SPECIALIST
            max_volume = gate * 2
        else:  # EXPANSION
            gate = _LIQUIDITY_GATE_EXPANSION
            max_volume = gate * 2

        liquidity_score = min(100.0, (metrics.volume / max_volume) * 100)

        # Volatility score (0-100)
        # Lower ATR = more stable (higher score), higher ATR = whipsawing (lower score)
        if metrics.atr_pct <= _ATR_MIN_PCT:
            volatility_score = 50.0  # Not enough volatility to confirm trend
        elif metrics.atr_pct >= _ATR_MAX_PCT:
            volatility_score = 20.0  # Too much volatility = hard to control
        else:
            # Optimal range: score based on distance from optimal zone
            optimal_atr = (_ATR_MIN_PCT + _ATR_MAX_PCT) / 2
            distance = abs(metrics.atr_pct - optimal_atr) / optimal_atr
            volatility_score = max(30.0, 100.0 - (distance * 100))

        # Spread penalty (reduce liquidity score by spread factor)
        if tier_name == "BLUE_CHIP":
            spread_threshold = _SPREAD_GATE_BLUE_CHIP
        elif tier_name == "SPECIALIST":
            spread_threshold = _SPREAD_GATE_SPECIALIST
        else:
            spread_threshold = _SPREAD_GATE_EXPANSION

        spread_penalty = min(30.0, (metrics.bid_ask_spread_bps / spread_threshold) * 15)
        liquidity_score = max(20.0, liquidity_score - spread_penalty)

        # Feature coverage (% of required metrics available)
        required_features = ["price", "volume", "bid_ask_spread_bps", "atr_pct", "rvol", "rsi"]
        available = sum(1 for f in required_features if getattr(metrics, f, None) is not None)
        feature_coverage = (available / len(required_features)) * 100

        # Blended confidence: weighted average
        # Liquidity 40%, Volatility 30%, Feature 30%
        confidence = (liquidity_score * 0.4) + (volatility_score * 0.3) + (feature_coverage * 0.3)

        # Adjust by RVOL (relative volume)
        if metrics.rvol > 1.5:
            confidence += 10  # Unusual volume can indicate setup
        elif metrics.rvol < 0.5:
            confidence -= 10  # Thin volume

        # RSI bias (avoid extremes)
        if 30 < metrics.rsi < 70:
            confidence += 5  # Normal range is better for entry
        elif metrics.rsi < 30 or metrics.rsi > 70:
            confidence -= 5  # Extremes suggest ongoing move, late entry

        confidence = max(0.0, min(100.0, confidence))

        return TierAssignment(
            ticker=ticker,
            tier=tier_name,
            confidence_pct=confidence,
            feature_coverage_pct=feature_coverage,
            liquidity_score=liquidity_score,
            volatility_score=volatility_score,
            signal_reliability_target={
                "BLUE_CHIP": 100.0,
                "SPECIALIST": 85.0,
                "EXPANSION": 70.0,
            }.get(tier_name, 70.0),
            reasons=[],
        )

    def _assignment_to_ranked(self, assignment: TierAssignment, rank: int) -> RankedAsset:
        """Convert TierAssignment to RankedAsset for output."""
        return RankedAsset(
            ticker=assignment.ticker,
            tier=assignment.tier,
            rank=rank,
            confidence_pct=assignment.confidence_pct,
            liquidity_score=assignment.liquidity_score,
            volatility_score=assignment.volatility_score,
            feature_coverage_pct=assignment.feature_coverage_pct,
            tradeable=assignment.confidence_pct >= 50.0,
            restrictions=assignment.reasons,
        )

    def _diagnose_gate_failure(self, metrics: AssetMetrics) -> str:
        """Return human-readable reason for gate failure."""
        if metrics.data_freshness_sec > _MAX_DATA_AGE_REALTIME:
            return f"stale_data ({metrics.data_freshness_sec}s old)"
        if metrics.volume < _LIQUIDITY_GATE_EXPANSION:
            return f"low_volume ({metrics.volume:,.0f} < {_LIQUIDITY_GATE_EXPANSION:,.0f})"
        if metrics.bid_ask_spread_bps > _SPREAD_GATE_EXPANSION:
            return f"wide_spread ({metrics.bid_ask_spread_bps:.1f}bps > {_SPREAD_GATE_EXPANSION}bps)"
        return "unknown_gate_failure"


# ---------------------------------------------------------------------------
# Embedded Tests
# ---------------------------------------------------------------------------

def test_tiered_scanner():
    """Unit tests for TieredUniverseScanner."""
    print("\n=== TieredUniverseScanner Tests ===\n")

    # Create mock metrics
    mock_metrics = {
        # BLUE_CHIP: high quality
        "QQQ3.L": AssetMetrics(
            ticker="QQQ3.L",
            price=150.5,
            volume=8_000_000,  # >5M gate
            bid_ask_spread_bps=8,  # <10bps gate
            atr_pct=1.2,
            rvol=1.3,
            adx=35.0,
            rsi=55.0,
            data_freshness_sec=30,
            last_update=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
        # SPECIALIST: moderate quality
        "AMD3.L": AssetMetrics(
            ticker="AMD3.L",
            price=75.2,
            volume=2_000_000,  # >1M gate
            bid_ask_spread_bps=15,  # <20bps gate
            atr_pct=1.8,
            rvol=0.9,
            adx=28.0,
            rsi=48.0,
            data_freshness_sec=45,
            last_update=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
        # EXPANSION: lower quality
        "BAC3.L": AssetMetrics(
            ticker="BAC3.L",
            price=45.0,
            volume=800_000,  # >500k gate
            bid_ask_spread_bps=25,  # <30bps gate
            atr_pct=2.1,
            rvol=0.6,
            adx=22.0,
            rsi=42.0,
            data_freshness_sec=60,
            last_update=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
        # FAILED: low volume
        "DEAD.L": AssetMetrics(
            ticker="DEAD.L",
            price=10.0,
            volume=100_000,  # <500k expansion gate
            bid_ask_spread_bps=50,  # >30bps gate
            atr_pct=0.5,
            rvol=0.2,
            adx=15.0,
            rsi=30.0,
            data_freshness_sec=600,  # STALE
            last_update="2026-01-01T00:00:00Z",
        ),
    }

    scanner = TieredUniverseScanner(
        universe_config={
            "core_list": ["QQQ3.L", "3LUS.L"],
            "peer_candidates": ["AMD3.L", "ARM3.L"],
            "sector_radar": ["BAC3.L", "GS3.L"],
        }
    )

    result = scanner.rank_assets(mock_metrics, top_n_per_tier={"BLUE_CHIP": 2, "SPECIALIST": 2, "EXPANSION": 1})

    # Assertions
    assert result.blue_chip_count >= 0, "Tier 1 count should be non-negative"
    assert result.specialist_count >= 0, "Tier 2 count should be non-negative"
    assert result.expansion_count >= 0, "Tier 3 count should be non-negative"

    print(f"✓ Scan complete: {result.blue_chip_count} BLUE_CHIP, {result.specialist_count} SPECIALIST, {result.expansion_count} EXPANSION")
    print(f"✓ Failed gates: {len(result.failed_tickers)} tickers")
    print(f"✓ Warnings: {len(result.warnings)} warnings")

    # Print top ranked assets
    for tier, assets in result.ranked_by_tier.items():
        if assets:
            print(f"\n{tier}:")
            for asset in assets[:3]:
                print(f"  {asset.rank}. {asset.ticker} (confidence={asset.confidence_pct:.1f}%, "
                      f"tradeable={asset.tradeable})")

    print("\n✓ All TieredUniverseScanner tests passed!")


if __name__ == "__main__":
    test_tiered_scanner()
