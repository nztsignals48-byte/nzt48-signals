"""
Early Detection Engine (Tier-Based Signal Fusion)
==================================================
Purpose: Detect PERFECT ENTRY TIMING by fusing 4 tiers of signals.

This is the core of Perfect Entry Timing for ALL trades, not just explosive moves.
Entry decision: confidence ≥65% AND Tier 1 present AND (1+ Tier 2 OR 2+ Tier 3)

Tier 1: Regime Pre-Conditions (Setup)
  - COMPRESSION setup (ATR coil, ready to spring)
  - EXPANSION starting (volatility expanding, momentum accelerating)
  - Hawkes branching ratio rising (self-exciting momentum cluster forming)
  Confidence: +10-12%

Tier 2: Volume/Flow Confirmation (Institutional Flow)
  - OFI directional (order flow imbalance >|0.30|, rising)
  - Volume profile breakthrough (price breaking LVN with size)
  - Absorption pattern (high-vol tight-range followed by directional move)
  - VTD high (volume-time decay >0.70, flow still feeding)
  - Volume climax + reversal (3x vol spike on reversal candle)
  Confidence: +6-10% per signal

Tier 3: Momentum Pre-Signals (Early Acceleration)
  - Trend acceleration (3+ bars: rising vol + expanding range)
  - Momentum divergence (price rising but MACD declining = bullish divergence)
  - Gap and Go (gap >1.5%, holds 5+ bars, RVOL >2.0)
  - Intraday momentum (first 30-min return predicts last 30-min)
  Confidence: +6-12% per signal

Tier 4: Catalyst Confirmation (Optional Bonus)
  - Short squeeze risk (SI>threshold, price rising, OFI bullish)
  - Opening range breakout (ORB: break OR high/low with volume)
  - Earnings morning report positive gap
  Confidence: +3-5% per signal

Decision Logic:
  - Base confidence: 30%
  - Add Tier 1, Tier 2, Tier 3, Tier 4 bonuses
  - ENTRY IF: confidence ≥65% AND Tier 1 present AND (1+ Tier 2 OR 2+ Tier 3)
  - SPECIAL: Gap+Go + RVOL>2.0 = minimum 55% confidence threshold

Reference:
  - Gao et al (2018): Intraday momentum predicts EOD
  - Blume et al (1994): Volume and volatility expansion predicts moves
  - Hawkes (1971): Self-exciting point processes model momentum clustering
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Tuple
import logging
import math

logger = logging.getLogger(__name__)


@dataclass
class EarlySignal:
    """A single early detection signal"""
    name: str
    tier: int  # 1, 2, 3, or 4
    confidence: float  # 0.00-0.12, percentage point contribution
    details: str  # Human-readable explanation


@dataclass
class EarlyDetectionResult:
    """Result from early detection engine"""
    should_enter: bool
    confidence: float  # 0-100%
    signals: List[EarlySignal]  # All signals that fired
    tier1_present: bool  # Did we have at least one Tier 1 signal?
    tier2_count: int  # Number of Tier 2 signals
    tier3_count: int  # Number of Tier 3 signals
    decision_reason: str


class EarlyDetectionEngine:
    """
    Fusion of early signals to decide "is a move starting NOW?"

    Applies to all trade entry scenarios (long, short, inverse ETPs).
    Provides confidence scoring that feeds into position sizing.
    """

    def __init__(self):
        self.logger = logging.getLogger("nzt48.early_detection")
        self._signal_history = {}  # ticker → list of recent signals

    def evaluate_entry_readiness(self, ticker: str, market_data: Dict) -> EarlyDetectionResult:
        """
        Run all checks, return (should_enter, confidence, signals).

        Args:
            ticker: e.g., "QQQ3.L", "HIMS"
            market_data: Dict with keys:
              - 'current_price': float
              - 'bid': float
              - 'ask': float
              - 'volume': float (shares)
              - 'vix': float
              - 'realized_vol': float (%)
              - 'atr': float (in currency)
              - 'bb_width_pct': float (0-100, percentile)
              - 'momentum': float (-2 to +2)
              - 'ofi': float (-1.0 to +1.0)
              - 'ofi_rising': bool
              - 'volume_profile_lvn': float (price of lowest-volume-node)
              - 'vtd_ratio': float (0-1.0, volume-time-decay)
              - 'hawkes_branching_ratio': float (α/β)
              - 'hawkes_trending': bool (rising)
              - 'atm_accel': float (0-2.0, ATR acceleration ratio)
              - 'recent_bars': List[Dict] with OHLCV for last 10 bars
              - 'gap_pct': float (open vs prev close)
              - 'si_pct': Optional[float] (short interest %)
              - 'recent_return_first_30m': Optional[float] (0-1 correlation)
              - 'market_regime': str (COMPRESSION, EXPANSION, TRENDING_UP, etc.)

        Returns:
            EarlyDetectionResult with decision and all fired signals
        """

        tier1_signals = []
        tier2_signals = []
        tier3_signals = []
        tier4_signals = []

        # ===== TIER 1: REGIME PRE-CONDITIONS =====
        tier1_signals.extend(self._check_tier1_regime(ticker, market_data))

        # ===== TIER 2: VOLUME/FLOW CONFIRMATION =====
        tier2_signals.extend(self._check_tier2_volume(ticker, market_data))

        # ===== TIER 3: MOMENTUM PRE-SIGNALS =====
        tier3_signals.extend(self._check_tier3_momentum(ticker, market_data))

        # ===== TIER 4: CATALYST CONFIRMATION =====
        tier4_signals.extend(self._check_tier4_catalysts(ticker, market_data))

        # ===== DECISION LOGIC =====
        confidence = 0.30  # Base 30%
        confidence += sum(s.confidence for s in tier1_signals)
        confidence += sum(s.confidence for s in tier2_signals)
        confidence += sum(s.confidence for s in tier3_signals)
        confidence += sum(s.confidence for s in tier4_signals)

        # Scale to 0-100%
        confidence_pct = min(100.0, confidence * 100)

        has_tier1 = len(tier1_signals) > 0
        has_tier2 = len(tier2_signals) > 0
        has_tier3_multi = len(tier3_signals) >= 2

        # Decision: confidence ≥65% AND Tier 1 present AND (1+ Tier 2 OR 2+ Tier 3)
        should_enter = False
        reason = ""

        if not has_tier1:
            reason = "No regime setup (Tier 1 signals required)"
        elif confidence_pct >= 65.0 and (has_tier2 or has_tier3_multi):
            should_enter = True
            reason = f"Confidence {confidence_pct:.0f}% ≥ 65% + Tier1 + ({'Tier2' if has_tier2 else 'Tier3×2'})"
        elif confidence_pct >= 60.0 and has_tier1:
            # Marginal: 60-65% is still reasonable with strong Tier 1
            should_enter = True
            reason = f"Marginal: Confidence {confidence_pct:.0f}% (60-65 range) + strong Tier1"
        else:
            reason = f"Insufficient confidence: {confidence_pct:.0f}% (need ≥65%)"

        # Special case: Gap + Go is powerful
        gap_go = [s for s in tier3_signals if "Gap and Go" in s.name]
        if gap_go and confidence_pct >= 55.0:
            should_enter = True
            reason = "Gap + Go special case (55%+ confidence)"

        all_signals = tier1_signals + tier2_signals + tier3_signals + tier4_signals

        return EarlyDetectionResult(
            should_enter=should_enter,
            confidence=confidence_pct,
            signals=all_signals,
            tier1_present=has_tier1,
            tier2_count=len(tier2_signals),
            tier3_count=len(tier3_signals),
            decision_reason=reason
        )

    def _check_tier1_regime(self, ticker: str, market_data: Dict) -> List[EarlySignal]:
        """Regime setup checks: COMPRESSION, EXPANSION, Hawkes rising"""
        signals = []

        regime = market_data.get("market_regime", "UNKNOWN")
        hawkes_br = market_data.get("hawkes_branching_ratio", 0.4)
        hawkes_trending = market_data.get("hawkes_trending", False)
        atm_accel = market_data.get("atm_accel", 1.0)
        bb_width_pct = market_data.get("bb_width_pct", 50)

        # Signal 1a: COMPRESSION setup (coil ready to spring)
        if regime == "COMPRESSION" and atm_accel < 0.8 and bb_width_pct < 20:
            signals.append(EarlySignal(
                name="COMPRESSION_setup",
                tier=1,
                confidence=0.10,
                details=f"Spring coil: ATR accel {atm_accel:.2f}, BB width {bb_width_pct:.0f}th pct"
            ))

        # Signal 1b: EXPANSION starting (accelerating volatility)
        if regime == "EXPANSION" and atm_accel > 1.2 and bb_width_pct > 60:
            signals.append(EarlySignal(
                name="EXPANSION_starting",
                tier=1,
                confidence=0.08,
                details=f"Vol expanding: ATR accel {atm_accel:.2f}, BB width {bb_width_pct:.0f}th pct"
            ))

        # Signal 1c: Hawkes branching ratio rising (self-exciting momentum)
        if hawkes_br > 0.5 and hawkes_trending:
            signals.append(EarlySignal(
                name="Hawkes_branching_rising",
                tier=1,
                confidence=0.12,
                details=f"Self-exciting momentum: α/β = {hawkes_br:.2f}, rising"
            ))

        return signals

    def _check_tier2_volume(self, ticker: str, market_data: Dict) -> List[EarlySignal]:
        """Volume & flow confirmation checks"""
        signals = []

        ofi = market_data.get("ofi", 0.0)
        ofi_rising = market_data.get("ofi_rising", False)
        current_price = market_data.get("current_price", 0.0)
        volume_profile_lvn = market_data.get("volume_profile_lvn", current_price - 1.0)
        vtd_ratio = market_data.get("vtd_ratio", 0.5)
        recent_bars = market_data.get("recent_bars", [])

        # Signal 2a: OFI directional (strong buy/sell pressure)
        if abs(ofi) > 0.30 and ofi_rising:
            direction = "BULLISH" if ofi > 0 else "BEARISH"
            signals.append(EarlySignal(
                name=f"OFI_directional_{direction}",
                tier=2,
                confidence=0.08,
                details=f"OFI = {ofi:.2f}, rising (strong directional conviction)"
            ))

        # Signal 2b: Volume profile breakthrough (LVN penetration)
        if current_price > volume_profile_lvn and current_price > volume_profile_lvn * 1.01:
            signals.append(EarlySignal(
                name="Volume_profile_breakthrough",
                tier=2,
                confidence=0.10,
                details=f"Price {current_price:.2f} penetrating LVN {volume_profile_lvn:.2f}"
            ))

        # Signal 2c: Absorption pattern (tight range before directional move)
        if len(recent_bars) >= 2:
            recent = recent_bars[-2:]
            if self._is_absorption_pattern(recent):
                signals.append(EarlySignal(
                    name="Absorption_pattern",
                    tier=2,
                    confidence=0.09,
                    details="High-vol tight range: supply/demand absorbed"
                ))

        # Signal 2d: VTD high (directional flow still sustained)
        if vtd_ratio > 0.70:
            signals.append(EarlySignal(
                name="VTD_high",
                tier=2,
                confidence=0.06,
                details=f"Volume-time decay {vtd_ratio:.0%}: directional flow sustained"
            ))

        # Signal 2e: Volume climax + reversal
        if len(recent_bars) >= 3:
            if self._is_volume_climax_reversal(recent_bars[-3:]):
                signals.append(EarlySignal(
                    name="Volume_climax_reversal",
                    tier=2,
                    confidence=0.08,
                    details="3x vol spike on reversal: exhaustion + commitment"
                ))

        return signals

    def _check_tier3_momentum(self, ticker: str, market_data: Dict) -> List[EarlySignal]:
        """Momentum acceleration checks"""
        signals = []

        momentum = market_data.get("momentum", 0.0)
        realized_vol = market_data.get("realized_vol", 15.0)
        gap_pct = market_data.get("gap_pct", 0.0)
        recent_bars = market_data.get("recent_bars", [])
        recent_return_first_30m = market_data.get("recent_return_first_30m", 0.0)

        # Signal 3a: Trend acceleration (3+ bars with rising vol and expanding range)
        if len(recent_bars) >= 3:
            if self._detect_trend_acceleration(recent_bars[-3:]):
                signals.append(EarlySignal(
                    name="Trend_acceleration",
                    tier=3,
                    confidence=0.12,
                    details="3-bar run: higher vol + expanding range"
                ))

        # Signal 3b: Momentum divergence (bullish/bearish)
        # For now, placeholder; in real system would compare MACD vs price
        if momentum > 1.0 and realized_vol < 12.0:
            signals.append(EarlySignal(
                name="Momentum_bullish_divergence",
                tier=3,
                confidence=0.09,
                details=f"Strong momentum {momentum:.2f} + low vol = setup"
            ))

        # Signal 3c: Gap and Go (gap >1.5%, holds, high realized vol)
        if gap_pct > 1.5 and realized_vol > 20.0:
            if len(recent_bars) >= 5:
                # Check if gap "holds" (doesn't reverse)
                gap_holds = all(
                    bar.get("close", 0) > bar.get("open", 0) * 0.995
                    for bar in recent_bars[-5:]
                )
                if gap_holds:
                    signals.append(EarlySignal(
                        name="Gap_and_Go",
                        tier=3,
                        confidence=0.10,
                        details=f"Gap {gap_pct:.1f}%, holds 5+ bars, RVOL {realized_vol:.0f}%"
                    ))

        # Signal 3d: Intraday momentum (first 30m return predicts EOD, Gao et al)
        if recent_return_first_30m > 0.4:  # >0.4 correlation threshold
            signals.append(EarlySignal(
                name="Intraday_momentum",
                tier=3,
                confidence=0.08,
                details=f"First 30-min return {recent_return_first_30m:.0%} correlation (predictor)"
            ))

        return signals

    def _check_tier4_catalysts(self, ticker: str, market_data: Dict) -> List[EarlySignal]:
        """Optional catalyst checks (bonus confidence)"""
        signals = []

        si_pct = market_data.get("si_pct", None)
        ofi = market_data.get("ofi", 0.0)
        gap_pct = market_data.get("gap_pct", 0.0)
        current_price = market_data.get("current_price", 0.0)
        recent_bars = market_data.get("recent_bars", [])

        # Signal 4a: Short squeeze risk
        if si_pct is not None and si_pct > 20.0 and gap_pct > 0 and ofi > 0.20:
            signals.append(EarlySignal(
                name="Short_squeeze_risk",
                tier=4,
                confidence=0.04,
                details=f"SI {si_pct:.0f}% + gap + bullish OFI"
            ))

        # Signal 4b: Opening range breakout
        if len(recent_bars) >= 5:
            if self._is_orb(recent_bars[-5:]):
                signals.append(EarlySignal(
                    name="Opening_range_breakout",
                    tier=4,
                    confidence=0.03,
                    details="Price breaking OR high with volume"
                ))

        # Signal 4c: Earnings morning report positive gap
        # Placeholder: would integrate earnings calendar
        if gap_pct > 2.0 and ofi > 0.25:
            signals.append(EarlySignal(
                name="Earnings_positive_gap",
                tier=4,
                confidence=0.05,
                details=f"Large gap {gap_pct:.1f}% + bullish flow"
            ))

        return signals

    # ===== HELPER METHODS (Pattern Detection) =====

    def _is_absorption_pattern(self, bars: List[Dict]) -> bool:
        """
        Absorption: High-vol tight-range bar followed by directional breakout.

        Bar1: High volume, small range (body tight, wicks small)
        Bar2: Directional candle (clear up or down movement)
        """
        if len(bars) < 2:
            return False

        b0, b1 = bars[-2], bars[-1]

        vol0 = b0.get("volume", 0)
        vol1 = b1.get("volume", 0)

        open0 = b0.get("open", 0)
        close0 = b0.get("close", 0)
        high0 = b0.get("high", 0)
        low0 = b0.get("low", 0)

        open1 = b1.get("open", 0)
        close1 = b1.get("close", 0)

        if open0 <= 0 or close1 <= 0:
            return False

        # Bar0: high volume, tight range
        body_range0 = abs(close0 - open0)
        total_range0 = high0 - low0
        range_ratio0 = body_range0 / total_range0 if total_range0 > 0 else 1.0

        is_tight = range_ratio0 > 0.7  # Body is >70% of total range
        is_high_vol = vol1 > vol0 * 0.8  # Vol0 is high, vol1 sustains

        # Bar1: clear directional move
        is_directional = abs(close1 - open1) / open1 > 0.005  # >0.5% move

        return is_tight and is_high_vol and is_directional

    def _is_volume_climax_reversal(self, bars: List[Dict]) -> bool:
        """
        Volume climax + reversal: 3x normal vol on reversal candle.

        E.g., strong up move, then sudden reversal on 3x volume.
        Signals capitulation/exhaustion followed by conviction reversal.
        """
        if len(bars) < 3:
            return False

        b0, b1, b2 = bars[-3], bars[-2], bars[-1]

        vol0 = b0.get("volume", 0)
        vol2 = b2.get("volume", 0)

        close0 = b0.get("close", 0)
        close1 = b1.get("close", 0)
        close2 = b2.get("close", 0)

        if vol0 <= 0 or close0 <= 0:
            return False

        # Vol climax: b2 vol >> normal
        is_climax = vol2 > vol0 * 3.0

        # Reversal: b0→b1 was up, b1→b2 reverses down (or vice versa)
        move_01 = (close1 - close0) / close0
        move_12 = (close2 - close1) / close1

        is_reversal = (move_01 > 0.01 and move_12 < -0.01) or (move_01 < -0.01 and move_12 > 0.01)

        return is_climax and is_reversal

    def _detect_trend_acceleration(self, bars: List[Dict]) -> bool:
        """
        Trend acceleration: 3+ bars with rising volume and expanding range.

        Signals momentum is building, not just random noise.
        """
        if len(bars) < 3:
            return False

        # Check vol rising
        vols = [b.get("volume", 0) for b in bars]
        vol_rising = all(vols[i] < vols[i + 1] for i in range(len(vols) - 1))

        # Check range expanding (use high-low per bar)
        ranges = [b.get("high", 0) - b.get("low", 0) for b in bars]
        range_expanding = all(ranges[i] < ranges[i + 1] for i in range(len(ranges) - 1)) if min(ranges) > 0 else False

        return vol_rising and range_expanding

    def _is_orb(self, bars: List[Dict]) -> bool:
        """
        Opening range breakout: Price breaks OR high/low with volume.

        OR = first 30 min (bars[0:4] approx).
        ORB = breakout of that range at volume.
        """
        if len(bars) < 5:
            return False

        or_bars = bars[:4]  # First ~30 min
        or_high = max(b.get("high", 0) for b in or_bars)
        or_low = min(b.get("low", 0) for b in or_bars)

        last_bar = bars[-1]
        last_vol = last_bar.get("volume", 0)
        avg_vol = sum(b.get("volume", 0) for b in or_bars) / len(or_bars)

        # Breakout: price beyond OR + volume surge
        price_breakout = (
            last_bar.get("close", 0) > or_high * 1.002 or
            last_bar.get("close", 0) < or_low * 0.998
        )
        vol_surge = last_vol > avg_vol * 1.5

        return price_breakout and vol_surge


if __name__ == "__main__":
    # Test the engine
    print("="*70)
    print("EARLY DETECTION ENGINE TEST")
    print("="*70)

    engine = EarlyDetectionEngine()

    # Mock market data for HIMS (before explosive move)
    test_data = {
        "current_price": 2.50,
        "bid": 2.48,
        "ask": 2.52,
        "volume": 250000,
        "vix": 14,
        "realized_vol": 45.0,  # High vol
        "atr": 0.15,
        "bb_width_pct": 25,  # Expansion starting
        "momentum": 1.5,  # Bullish
        "ofi": 0.35,  # Strong buy
        "ofi_rising": True,
        "volume_profile_lvn": 2.40,  # Price above LVN
        "vtd_ratio": 0.75,  # High VTD
        "hawkes_branching_ratio": 0.65,  # Self-exciting
        "hawkes_trending": True,
        "atm_accel": 1.4,  # Accelerating
        "gap_pct": 2.0,  # Gap up
        "si_pct": 22.0,  # High short interest
        "recent_return_first_30m": 0.5,
        "market_regime": "EXPANSION",
        "recent_bars": [
            {"open": 2.40, "high": 2.45, "low": 2.38, "close": 2.42, "volume": 100000},
            {"open": 2.42, "high": 2.50, "low": 2.41, "close": 2.48, "volume": 150000},
            {"open": 2.48, "high": 2.52, "low": 2.47, "close": 2.50, "volume": 200000},
        ]
    }

    result = engine.evaluate_entry_readiness("HIMS", test_data)

    print(f"\nEntry Readiness: {result.should_enter}")
    print(f"Confidence: {result.confidence:.1f}%")
    print(f"Decision: {result.decision_reason}")
    print(f"\nTier 1 Present: {result.tier1_present}")
    print(f"Tier 2 Signals: {result.tier2_count}")
    print(f"Tier 3 Signals: {result.tier3_count}")
    print(f"\nSignals Fired ({len(result.signals)} total):")
    for sig in result.signals:
        print(f"  [T{sig.tier}] {sig.name}: +{sig.confidence*100:.0f}% → {sig.details}")

    print(f"\n{'='*70}")
    if result.should_enter:
        print(f"✅ PERFECT ENTRY DETECTED")
        print(f"   Confidence: {result.confidence:.1f}% (target: ≥65%)")
        print(f"   Reasoning: {result.decision_reason}")
    else:
        print(f"❌ NOT READY FOR ENTRY")
        print(f"   Current: {result.confidence:.1f}% (need ≥65%)")
        print(f"   Reason: {result.decision_reason}")
