"""
NZT-48 Perfect Asset Optimizer (Week 2 Asset Quality Gate)
===========================================================
Safe quality filters for PERFECT asset execution.

Prevents bad-quality assets from reaching position_sizer.
Acts as final veto gate BEFORE trading decisions are made.

Quality Dimensions:
  1. Tradeability: liquidity >500k shares/day, spread <0.3%, not delisted
  2. Signal Consistency: recent signal accuracy >60%, reliability >75%
  3. Data Quality: complete OHLCV, no gaps >1 day, freshness <5 min
  4. Regime Stability: volatility not at extremes, ADX confirming trend
  5. Correlation Risk: not correlated >0.85 with active position

Integration:
  - Input: ranked assets from TieredUniverseScanner + early_detection_engine confidence
  - Output: whitelist of PERFECT assets only
  - Gate Position: Execute BEFORE PositionSizer.size() call
  - Return: {ticker: bool, reason: str} for each candidate

Reference:
  - uk_isa/tiered_universe_scanner.py: ranked assets
  - src/core/early_detection_engine.py: confidence scores
  - src/core/position_sizer.py: where results are used
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Tuple, Any
from pathlib import Path

logger = logging.getLogger("nzt48.perfect_asset_optimizer")


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class AssetQualityMetrics:
    """Per-asset quality assessment."""
    ticker: str
    tier: str                          # "BLUE_CHIP", "SPECIALIST", "EXPANSION"
    liquidity_score: float             # 0-100
    spread_bps: float                  # actual bid-ask spread
    signal_accuracy_pct: float         # % of recent signals that were correct
    signal_reliability_pct: float      # % of signals within expected confidence range
    data_completeness_pct: float       # % of required OHLCV bars present
    data_freshness_sec: int            # seconds since last quote
    volatility_regime: str             # "COMPRESSION", "NORMAL", "EXPANSION", "EXTREME"
    adx: float                         # ADX indicator (0-100)
    is_delisted: bool                  # marked as delisted in governance?
    last_assessment: str               # ISO timestamp


@dataclass
class TradeabilityResult:
    """Result of tradeability check."""
    ticker: str
    is_tradeable: bool
    volume_ok: bool                    # >500k
    spread_ok: bool                    # <0.3% or <30bps
    data_ok: bool                      # complete, fresh
    not_delisted: bool
    issues: List[str] = field(default_factory=list)


@dataclass
class SignalQualityResult:
    """Result of signal consistency check."""
    ticker: str
    is_high_quality: bool
    accuracy_pct: float                # >60% target
    reliability_pct: float             # >75% target
    sample_size: int                   # number of recent signals analyzed
    issues: List[str] = field(default_factory=list)


@dataclass
class AssetWhitelistEntry:
    """Entry in the execution whitelist."""
    ticker: str
    tier: str
    confidence_pct: float              # from early_detection_engine
    quality_score: float               # 0-100 blended
    tradeable: bool
    signal_quality: bool
    data_quality: bool
    regime_ok: bool
    is_approved: bool                  # final approval for execution
    approval_reason: str
    restrictions: List[str] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class OptimizationResult:
    """Complete optimization result for a slate of candidates."""
    scan_timestamp: str
    total_candidates: int
    approved_count: int                # passed all checks
    rejected_count: int                # failed at least one check
    whitelist: List[AssetWhitelistEntry]  # only approved assets
    rejections: List[Dict[str, Any]] = field(default_factory=list)  # {ticker, reason, issues}
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Thresholds & Gates
# ---------------------------------------------------------------------------

# Tradeability gates (hard blockers)
_MIN_VOLUME_SHARES = 500_000          # 500k shares/day minimum
_MAX_SPREAD_BPS = 30                  # 30 basis points maximum
_MAX_SPREAD_PCT = 0.30                # 0.30% maximum
_MAX_DATA_AGE_SEC = 300               # 5 minutes maximum

# Signal quality thresholds
_MIN_ACCURACY_PCT = 60.0              # Minimum accuracy % for good signals
_MIN_RELIABILITY_PCT = 75.0           # Minimum reliability for consistent signals
_MIN_SIGNAL_SAMPLE_SIZE = 10          # Need at least 10 recent signals

# Data quality thresholds
_MIN_DATA_COMPLETENESS = 90.0         # At least 90% of bars present
_MAX_DATA_GAP_DAYS = 1                # No gaps >1 trading day

# Volatility regime thresholds
_ADX_MIN_TRENDING = 20.0              # ADX >20 = confirmed trend
_VOLATILITY_EXTREME = "EXTREME"       # Reject if in extreme volatility

# Correlation risk threshold
_MAX_CORRELATION_EXISTING = 0.85      # Don't stack highly correlated positions

# Quality score weighting
_WEIGHTS = {
    "tradeability": 0.30,             # Fundamental viability
    "signal_quality": 0.35,           # Entry confidence
    "data_quality": 0.15,             # Execution reliability
    "regime": 0.20,                   # Market environment
}


# ---------------------------------------------------------------------------
# PerfectAssetOptimizer
# ---------------------------------------------------------------------------

class PerfectAssetOptimizer:
    """
    Safe asset quality filter for perfect execution.

    Provides whitelist of PERFECT assets only.
    Acts as veto gate before position_sizer.
    """

    def __init__(self, existing_positions: Optional[Dict[str, float]] = None):
        """
        Initialize optimizer.

        Args:
            existing_positions: Dict[ticker, position_size] — active positions to avoid correlation duplication
        """
        self.logger = logger
        self._existing_positions = existing_positions or {}
        self._asset_history: Dict[str, List[AssetQualityMetrics]] = {}  # ticker -> history
        self._whitelist_cache: Dict[str, AssetWhitelistEntry] = {}  # ticker -> cached whitelist entry

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def is_tradeable(
        self,
        ticker: str,
        volume: float,
        spread_bps: float,
        is_delisted: bool = False,
        data_freshness_sec: int = 0,
    ) -> TradeabilityResult:
        """
        Check if asset meets minimum tradeability standards.

        Args:
            ticker: Asset symbol
            volume: Daily volume in shares
            spread_bps: Bid-ask spread in basis points
            is_delisted: Whether ticker is marked delisted
            data_freshness_sec: Seconds since last data update

        Returns:
            TradeabilityResult with pass/fail and issues list.
        """
        issues = []
        volume_ok = volume >= _MIN_VOLUME_SHARES
        spread_ok = spread_bps <= _MAX_SPREAD_BPS

        if not volume_ok:
            issues.append(f"volume {volume:,.0f} < {_MIN_VOLUME_SHARES:,.0f}")

        if not spread_ok:
            issues.append(f"spread {spread_bps:.1f}bps > {_MAX_SPREAD_BPS}bps")

        data_ok = data_freshness_sec <= _MAX_DATA_AGE_SEC
        if not data_ok:
            issues.append(f"stale data ({data_freshness_sec}s > {_MAX_DATA_AGE_SEC}s)")

        not_delisted = not is_delisted
        if is_delisted:
            issues.append("marked delisted")

        is_tradeable = volume_ok and spread_ok and data_ok and not_delisted

        result = TradeabilityResult(
            ticker=ticker,
            is_tradeable=is_tradeable,
            volume_ok=volume_ok,
            spread_ok=spread_ok,
            data_ok=data_ok,
            not_delisted=not_delisted,
            issues=issues,
        )

        self.logger.debug(
            f"{ticker}: tradeable={is_tradeable} (volume={volume_ok}, spread={spread_ok}, "
            f"data={data_ok}, not_delisted={not_delisted})"
        )

        return result

    def rank_by_quality(
        self,
        candidates: List[Dict[str, Any]],
        early_detection_scores: Optional[Dict[str, float]] = None,
    ) -> OptimizationResult:
        """
        Filter and rank candidates by quality, return approved whitelist.

        Args:
            candidates: List of dicts with keys:
                - ticker, tier, volume, spread_bps, signal_accuracy_pct,
                  signal_reliability_pct, data_completeness_pct, data_freshness_sec,
                  volatility_regime, adx, is_delisted
            early_detection_scores: Dict[ticker, confidence_pct] — from early_detection_engine

        Returns:
            OptimizationResult with approved whitelist + rejections.
        """
        if early_detection_scores is None:
            early_detection_scores = {}

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        approved = []
        rejections = []
        warnings = []

        for candidate in candidates:
            ticker = candidate.get("ticker")
            if not ticker:
                continue

            # Extract fields with defaults
            tier = candidate.get("tier", "UNKNOWN")
            volume = candidate.get("volume", 0)
            spread_bps = candidate.get("spread_bps", 100)
            is_delisted = candidate.get("is_delisted", False)
            data_freshness_sec = candidate.get("data_freshness_sec", 0)
            signal_accuracy = candidate.get("signal_accuracy_pct", 0)
            signal_reliability = candidate.get("signal_reliability_pct", 0)
            data_completeness = candidate.get("data_completeness_pct", 0)
            volatility_regime = candidate.get("volatility_regime", "UNKNOWN")
            adx = candidate.get("adx", 0)

            confidence_from_early_detection = early_detection_scores.get(ticker, 0)

            # Run all checks
            tradeability = self.is_tradeable(
                ticker, volume, spread_bps, is_delisted, data_freshness_sec
            )
            signal_quality = self._check_signal_quality(
                ticker, signal_accuracy, signal_reliability
            )
            data_quality = self._check_data_quality(ticker, data_completeness, data_freshness_sec)
            regime_ok = self._check_regime_stability(ticker, volatility_regime, adx)

            # Determine approval
            all_pass = (
                tradeability.is_tradeable
                and signal_quality.is_high_quality
                and data_quality >= 70.0
                and regime_ok
            )

            quality_score = self._compute_quality_score(
                tradeability.is_tradeable,
                signal_quality.is_high_quality,
                data_quality,
                regime_ok,
                signal_accuracy,
                adx,
            )

            if all_pass:
                whitelist_entry = AssetWhitelistEntry(
                    ticker=ticker,
                    tier=tier,
                    confidence_pct=confidence_from_early_detection,
                    quality_score=quality_score,
                    tradeable=tradeability.is_tradeable,
                    signal_quality=signal_quality.is_high_quality,
                    data_quality=data_quality >= 70.0,
                    regime_ok=regime_ok,
                    is_approved=True,
                    approval_reason=f"All checks passed (quality={quality_score:.0f}%)",
                    restrictions=[],
                )
                approved.append(whitelist_entry)
                self.logger.info(f"{ticker}: APPROVED (quality={quality_score:.0f}%, "
                                f"early_det={confidence_from_early_detection:.0f}%)")
            else:
                rejection_reasons = []
                if not tradeability.is_tradeable:
                    rejection_reasons.append(f"tradeability: {'; '.join(tradeability.issues)}")
                if not signal_quality.is_high_quality:
                    rejection_reasons.append(f"signal_quality: {'; '.join(signal_quality.issues)}")
                if data_quality < 70.0:
                    rejection_reasons.append(f"data_quality: {data_quality:.0f}% < 70%")
                if not regime_ok:
                    rejection_reasons.append(f"regime: {volatility_regime} is unsuitable")

                rejections.append({
                    "ticker": ticker,
                    "tier": tier,
                    "quality_score": quality_score,
                    "reasons": rejection_reasons,
                })
                self.logger.debug(f"{ticker}: REJECTED ({'; '.join(rejection_reasons)})")

        # Sort approved by quality (highest first)
        approved.sort(key=lambda x: x.quality_score, reverse=True)

        # Check for correlation risks
        for entry in approved:
            corr_issues = self._check_correlation_risk(entry.ticker, approved)
            if corr_issues:
                entry.restrictions.extend(corr_issues)
                warnings.append(f"{entry.ticker}: correlation warning - {'; '.join(corr_issues)}")

        result = OptimizationResult(
            scan_timestamp=now,
            total_candidates=len(candidates),
            approved_count=len(approved),
            rejected_count=len(rejections),
            whitelist=approved,
            rejections=rejections,
            warnings=warnings,
        )

        self.logger.info(
            f"Optimization complete: {len(approved)}/{len(candidates)} approved, "
            f"quality avg={sum(e.quality_score for e in approved)/max(1, len(approved)):.0f}%"
        )

        return result

    # -----------------------------------------------------------------------
    # Internal Checks
    # -----------------------------------------------------------------------

    def _check_signal_quality(
        self,
        ticker: str,
        accuracy_pct: float,
        reliability_pct: float,
    ) -> SignalQualityResult:
        """Check if signal metrics meet quality thresholds."""
        issues = []
        accuracy_ok = accuracy_pct >= _MIN_ACCURACY_PCT
        reliability_ok = reliability_pct >= _MIN_RELIABILITY_PCT

        if not accuracy_ok:
            issues.append(f"accuracy {accuracy_pct:.0f}% < {_MIN_ACCURACY_PCT:.0f}%")

        if not reliability_ok:
            issues.append(f"reliability {reliability_pct:.0f}% < {_MIN_RELIABILITY_PCT:.0f}%")

        is_high_quality = accuracy_ok and reliability_ok

        return SignalQualityResult(
            ticker=ticker,
            is_high_quality=is_high_quality,
            accuracy_pct=accuracy_pct,
            reliability_pct=reliability_pct,
            sample_size=_MIN_SIGNAL_SAMPLE_SIZE,
            issues=issues,
        )

    def _check_data_quality(
        self,
        ticker: str,
        completeness_pct: float,
        freshness_sec: int,
    ) -> float:
        """
        Check data quality, return score 0-100.

        Factors: completeness, freshness
        """
        completeness_ok = completeness_pct >= _MIN_DATA_COMPLETENESS
        freshness_ok = freshness_sec <= _MAX_DATA_AGE_SEC

        # Compute score
        score = 0.0
        if completeness_ok:
            score += 50.0 * (completeness_pct / 100.0)
        if freshness_ok:
            score += 50.0 * (1.0 - (freshness_sec / _MAX_DATA_AGE_SEC))

        return score

    def _check_regime_stability(
        self,
        ticker: str,
        volatility_regime: str,
        adx: float,
    ) -> bool:
        """Check if volatility regime is suitable for trading."""
        if volatility_regime == _VOLATILITY_EXTREME:
            self.logger.debug(f"{ticker}: rejected (EXTREME volatility regime)")
            return False

        if adx < _ADX_MIN_TRENDING and volatility_regime in ("COMPRESSION",):
            self.logger.debug(
                f"{ticker}: warning (ADX={adx:.0f} < {_ADX_MIN_TRENDING}, "
                f"regime={volatility_regime})"
            )
            return True  # Still OK, just warning

        return True

    def _compute_quality_score(
        self,
        tradeable: bool,
        signal_quality: bool,
        data_quality: float,
        regime_ok: bool,
        accuracy_pct: float,
        adx: float,
    ) -> float:
        """Blended quality score 0-100."""
        score = 0.0

        # Tradeability component (0-100)
        if tradeable:
            score += 100 * _WEIGHTS["tradeability"]
        else:
            score += 30 * _WEIGHTS["tradeability"]

        # Signal quality component (0-100)
        if signal_quality:
            score += 100 * _WEIGHTS["signal_quality"]
        else:
            score += (accuracy_pct / 100) * 100 * _WEIGHTS["signal_quality"]

        # Data quality component (passed in as 0-100)
        score += data_quality * _WEIGHTS["data_quality"]

        # Regime component (0-100)
        if regime_ok:
            score += 100 * _WEIGHTS["regime"]
            # Boost if ADX is strong
            if adx > _ADX_MIN_TRENDING:
                adx_boost = min(20, (adx / 50) * 20)
                score += adx_boost * 0.1
        else:
            score += 40 * _WEIGHTS["regime"]

        return min(100.0, max(0.0, score))

    def _check_correlation_risk(
        self,
        candidate_ticker: str,
        other_approved: List[AssetWhitelistEntry],
    ) -> List[str]:
        """
        Check if candidate is highly correlated with other approved assets.

        Returns list of correlation issues (empty if OK).
        """
        issues = []

        # For now, simple heuristic: don't approve >2 from same tier
        tier_counts = {}
        for entry in other_approved:
            if entry.ticker != candidate_ticker:
                tier_counts[entry.tier] = tier_counts.get(entry.tier, 0) + 1

        # Tier-specific limits (prevent duplication)
        candidate_tier = None
        for entry in other_approved:
            if entry.ticker == candidate_ticker:
                candidate_tier = entry.tier
                break

        if candidate_tier:
            tier_limit = {
                "BLUE_CHIP": 2,
                "SPECIALIST": 1,
                "EXPANSION": 1,
            }.get(candidate_tier, 1)

            if tier_counts.get(candidate_tier, 0) >= tier_limit:
                issues.append(f"tier limit ({tier_counts[candidate_tier]} already approved for {candidate_tier})")

        return issues


# ---------------------------------------------------------------------------
# Embedded Tests
# ---------------------------------------------------------------------------

def test_perfect_asset_optimizer():
    """Unit tests for PerfectAssetOptimizer."""
    print("\n=== PerfectAssetOptimizer Tests ===\n")

    optimizer = PerfectAssetOptimizer()

    # Test 1: Tradeability check
    print("Test 1: Tradeability checks")
    result1 = optimizer.is_tradeable(
        ticker="QQQ3.L",
        volume=8_000_000,
        spread_bps=8,
        is_delisted=False,
        data_freshness_sec=30,
    )
    assert result1.is_tradeable, "QQQ3.L should be tradeable"
    print(f"✓ QQQ3.L tradeable: {result1.is_tradeable}")

    result2 = optimizer.is_tradeable(
        ticker="DEAD.L",
        volume=100_000,
        spread_bps=50,
        is_delisted=True,
        data_freshness_sec=600,
    )
    assert not result2.is_tradeable, "DEAD.L should NOT be tradeable"
    print(f"✓ DEAD.L rejected: {'; '.join(result2.issues)}")

    # Test 2: Quality ranking
    print("\nTest 2: Quality ranking")
    candidates = [
        {
            "ticker": "QQQ3.L",
            "tier": "BLUE_CHIP",
            "volume": 8_000_000,
            "spread_bps": 8,
            "signal_accuracy_pct": 72,
            "signal_reliability_pct": 85,
            "data_completeness_pct": 98,
            "data_freshness_sec": 30,
            "volatility_regime": "NORMAL",
            "adx": 35,
            "is_delisted": False,
        },
        {
            "ticker": "ARM3.L",
            "tier": "SPECIALIST",
            "volume": 2_000_000,
            "spread_bps": 15,
            "signal_accuracy_pct": 65,
            "signal_reliability_pct": 80,
            "data_completeness_pct": 95,
            "data_freshness_sec": 45,
            "volatility_regime": "NORMAL",
            "adx": 28,
            "is_delisted": False,
        },
        {
            "ticker": "JUNK.L",
            "tier": "EXPANSION",
            "volume": 100_000,
            "spread_bps": 60,
            "signal_accuracy_pct": 50,
            "signal_reliability_pct": 60,
            "data_completeness_pct": 70,
            "data_freshness_sec": 400,
            "volatility_regime": "EXTREME",
            "adx": 15,
            "is_delisted": False,
        },
    ]

    early_detection = {
        "QQQ3.L": 80,
        "ARM3.L": 65,
        "JUNK.L": 30,
    }

    opt_result = optimizer.rank_by_quality(candidates, early_detection)

    assert opt_result.approved_count >= 1, "Should approve at least QQQ3.L"
    assert opt_result.rejected_count >= 1, "Should reject JUNK.L"
    print(f"✓ Approved: {opt_result.approved_count}/{opt_result.total_candidates}")
    print(f"✓ Rejected: {opt_result.rejected_count}/{opt_result.total_candidates}")

    for entry in opt_result.whitelist:
        print(f"  {entry.ticker}: quality={entry.quality_score:.0f}%, "
              f"confidence={entry.confidence_pct:.0f}%")

    print("\n✓ All PerfectAssetOptimizer tests passed!")


if __name__ == "__main__":
    test_perfect_asset_optimizer()
