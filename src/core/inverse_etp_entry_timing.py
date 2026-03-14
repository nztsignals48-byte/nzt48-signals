"""
Inverse ETP Entry Timing (Shorts / Inverse ETPs)
================================================
Purpose: Special entry timing logic for short positions and inverse ETPs (3USS.L, etc).

Problem: Shorting is harder than going long (psychological, squeeze risk, etc.)
Solution: Use MIRROR LOGIC from early detection engine, but trigger on BEARISH signals

Key difference from long entries:
  - Long: Enter when BULLISH signals fire (OFI>+0.30, gap up, Hawkes rising)
  - Short: Enter when BEARISH signals fire (OFI<-0.30, gap down, Hawkes rising on downside)

Bearish Signals (Mirror of Bullish):

Tier 1 (Regime):
  ✓ COMPRESSION + price at top of range → ready to spring down
  ✓ EXPANSION with bearish momentum → vol accelerating down
  ✓ Hawkes branching rising on DOWNSIDE → self-exciting down moves

Tier 2 (Volume/Flow):
  ✓ OFI strongly negative (< -0.30, rising in magnitude)
  ✓ Volume profile breakdown (price breaking LVN to downside)
  ✓ Climax volume on reversal DOWN (exhaustion + selling conviction)
  ✓ VTD high (downside flow sustained)

Tier 3 (Momentum):
  ✓ Trend acceleration DOWN (3 bars: higher vol, expanding down range)
  ✓ Bearish divergence (price declining but MACD rising = weakness)
  ✓ Gap DOWN and hold (gap >1.5% down, holds, RVOL high)
  ✓ Intraday momentum DOWN predicts EOD down

Tier 4 (Catalyst):
  ✓ Short covering squeeze risk becomes SHORT SQUEEZE (price falling fast)
  ✓ Opening range breakdown (break OR low with volume)
  ✓ Earnings negative surprise

Perfect inverse ETP entry = right BEFORE downtrend starts, not after -5% already hit.
"""

from dataclasses import dataclass
from typing import Optional, List, Dict
import logging

logger = logging.getLogger(__name__)


@dataclass
class InverseEntryResult:
    """Result from inverse ETP entry timing evaluation"""
    should_short: bool
    confidence: float  # 0-100%
    signals_fired: List[str]
    reason: str


class InverseETPEntryTiming:
    """
    Mirrors early detection logic but for SHORT/INVERSE positions.

    Detects early signs of DOWNTREND start, not downside that already happened.
    """

    def __init__(self):
        self.logger = logging.getLogger("nzt48.inverse_entry")

    def is_perfect_short_entry(
        self,
        ticker: str,
        market_data: Dict
    ) -> InverseEntryResult:
        """
        Detect perfect entry for short positions / inverse ETPs.

        Args:
            ticker: e.g., "3USS.L" (inverse) or "AAPL" (short)
            market_data: Same structure as early_detection_engine but now
                        we look for BEARISH signals

        Returns:
            InverseEntryResult with decision and signals
        """

        signals = []
        confidence = 0.30  # Base

        # ===== TIER 1: REGIME PRE-CONDITIONS (BEARISH) =====
        t1_signals, t1_conf = self._check_tier1_bearish(ticker, market_data)
        signals.extend(t1_signals)
        confidence += t1_conf

        # ===== TIER 2: VOLUME/FLOW (BEARISH) =====
        t2_signals, t2_conf = self._check_tier2_bearish(ticker, market_data)
        signals.extend(t2_signals)
        confidence += t2_conf

        # ===== TIER 3: MOMENTUM (BEARISH) =====
        t3_signals, t3_conf = self._check_tier3_bearish(ticker, market_data)
        signals.extend(t3_signals)
        confidence += t3_conf

        # ===== TIER 4: CATALYST (BEARISH) =====
        t4_signals, t4_conf = self._check_tier4_bearish(ticker, market_data)
        signals.extend(t4_signals)
        confidence += t4_conf

        # Scale to 0-100%
        confidence_pct = min(100.0, confidence * 100)

        # Decision logic: same as longs but on bearish signals
        has_tier1 = any(s.startswith("[T1]") for s in signals)
        tier2_count = sum(1 for s in signals if s.startswith("[T2]"))
        tier3_count = sum(1 for s in signals if s.startswith("[T3]"))

        should_short = False
        reason = ""

        if not has_tier1:
            reason = "No bearish regime setup (Tier 1 required)"
        elif confidence_pct >= 65.0 and (tier2_count >= 1 or tier3_count >= 2):
            should_short = True
            reason = f"Confidence {confidence_pct:.0f}% ≥ 65% + Tier1 bearish + ({'T2' if tier2_count else 'T3×2'})"
        elif confidence_pct >= 60.0 and has_tier1:
            should_short = True
            reason = f"Marginal: Confidence {confidence_pct:.0f}% (60-65) + bearish Tier1"
        else:
            reason = f"Insufficient confidence: {confidence_pct:.0f}% (need ≥65%)"

        # Special case: Gap down + hold
        gap_down = any("Gap_down" in s for s in signals)
        if gap_down and confidence_pct >= 55.0:
            should_short = True
            reason = "Gap down + hold special case (55%+ confidence)"

        return InverseEntryResult(
            should_short=should_short,
            confidence=confidence_pct,
            signals_fired=signals,
            reason=reason
        )

    def _check_tier1_bearish(self, ticker: str, market_data: Dict) -> tuple:
        """Bearish Tier 1: regime setup for shorts"""
        signals = []
        confidence = 0.0

        regime = market_data.get("market_regime", "UNKNOWN")
        hawkes_br = market_data.get("hawkes_branching_ratio", 0.4)
        hawkes_trending = market_data.get("hawkes_trending", False)
        momentum = market_data.get("momentum", 0.0)
        atm_accel = market_data.get("atm_accel", 1.0)
        bb_width_pct = market_data.get("bb_width_pct", 50)

        # Signal 1a: COMPRESSION at top (ready to spring down)
        current_price = market_data.get("current_price", 0.0)
        bb_upper = market_data.get("bb_upper", current_price + 1.0)

        if regime == "COMPRESSION" and current_price > (bb_upper * 0.95):
            signals.append("[T1] COMPRESSION_at_resistance")
            confidence += 0.10

        # Signal 1b: EXPANSION with bearish momentum
        if regime == "EXPANSION" and momentum < -0.5 and atm_accel > 1.2:
            signals.append("[T1] EXPANSION_downside")
            confidence += 0.08

        # Signal 1c: Hawkes on downside
        if hawkes_br > 0.5 and hawkes_trending and momentum < -0.3:
            signals.append("[T1] Hawkes_downside_momentum")
            confidence += 0.12

        return signals, confidence

    def _check_tier2_bearish(self, ticker: str, market_data: Dict) -> tuple:
        """Bearish Tier 2: volume/flow confirmation"""
        signals = []
        confidence = 0.0

        ofi = market_data.get("ofi", 0.0)
        ofi_rising = market_data.get("ofi_rising", False)
        current_price = market_data.get("current_price", 0.0)
        volume_profile_hvn = market_data.get("volume_profile_hvn", current_price + 1.0)
        vtd_ratio = market_data.get("vtd_ratio", 0.5)
        recent_bars = market_data.get("recent_bars", [])

        # Signal 2a: OFI strongly negative (sell pressure)
        if ofi < -0.30 and ofi_rising:  # Rising in magnitude (more negative)
            signals.append("[T2] OFI_bearish_strong")
            confidence += 0.08

        # Signal 2b: Volume profile breakdown (price breaks HVN downside)
        if current_price < volume_profile_hvn and current_price < volume_profile_hvn * 0.99:
            signals.append("[T2] Volume_breakdown_down")
            confidence += 0.10

        # Signal 2c: Volume climax on reversal DOWN
        if len(recent_bars) >= 3:
            if self._is_climax_down(recent_bars[-3:]):
                signals.append("[T2] Climax_volume_downside")
                confidence += 0.08

        # Signal 2d: VTD high on downside (selling sustained)
        if vtd_ratio > 0.70 and market_data.get("momentum", 0.0) < -0.3:
            signals.append("[T2] VTD_downside_flow")
            confidence += 0.06

        return signals, confidence

    def _check_tier3_bearish(self, ticker: str, market_data: Dict) -> tuple:
        """Bearish Tier 3: momentum acceleration down"""
        signals = []
        confidence = 0.0

        momentum = market_data.get("momentum", 0.0)
        realized_vol = market_data.get("realized_vol", 15.0)
        gap_pct = market_data.get("gap_pct", 0.0)
        recent_bars = market_data.get("recent_bars", [])

        # Signal 3a: Trend acceleration DOWN
        if len(recent_bars) >= 3:
            if self._detect_downtrend_acceleration(recent_bars[-3:]):
                signals.append("[T3] Downtrend_acceleration")
                confidence += 0.12

        # Signal 3b: Bearish divergence
        if momentum < -1.0 and realized_vol < 12.0:
            signals.append("[T3] Bearish_divergence")
            confidence += 0.09

        # Signal 3c: Gap DOWN and hold
        if gap_pct < -1.5 and realized_vol > 20.0:
            if len(recent_bars) >= 5:
                gap_holds_down = all(
                    bar.get("close", 0) < bar.get("open", 0) * 1.005
                    for bar in recent_bars[-5:]
                )
                if gap_holds_down:
                    signals.append("[T3] Gap_down_and_hold")
                    confidence += 0.10

        # Signal 3d: Intraday momentum DOWN
        intraday_return = market_data.get("recent_return_first_30m", 0.0)
        if intraday_return < -0.3:  # Negative correlation (predicts EOD down)
            signals.append("[T3] Intraday_momentum_down")
            confidence += 0.08

        return signals, confidence

    def _check_tier4_bearish(self, ticker: str, market_data: Dict) -> tuple:
        """Bearish Tier 4: catalysts for shorts"""
        signals = []
        confidence = 0.0

        gap_pct = market_data.get("gap_pct", 0.0)
        recent_bars = market_data.get("recent_bars", [])

        # Signal 4a: Opening range breakdown (break OR low with volume)
        if len(recent_bars) >= 5:
            if self._is_orb_down(recent_bars[-5:]):
                signals.append("[T4] Opening_range_breakdown")
                confidence += 0.03

        # Signal 4b: Earnings negative surprise gap down
        if gap_pct < -2.0:
            signals.append("[T4] Earnings_negative_gap")
            confidence += 0.05

        return signals, confidence

    # ===== HELPER METHODS (Bearish Pattern Detection) =====

    def _is_climax_down(self, bars: List[Dict]) -> bool:
        """Climax volume on downside reversal"""
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

        # Vol climax
        is_climax = vol2 > vol0 * 3.0

        # Reversal DOWN: b0→b1 was down, b1→b2 reverses... no wait, we want climax on DOWN
        # So b2 should be strong down candle with volume
        move_12 = (close2 - close1) / close1
        is_downside_move = move_12 < -0.01

        return is_climax and is_downside_move

    def _detect_downtrend_acceleration(self, bars: List[Dict]) -> bool:
        """Downtrend acceleration: 3 bars with rising vol and expanding down range"""
        if len(bars) < 3:
            return False

        vols = [b.get("volume", 0) for b in bars]
        vol_rising = all(vols[i] < vols[i + 1] for i in range(len(vols) - 1))

        # Range expanding downside (high-low spread increasing)
        ranges = [b.get("high", 0) - b.get("low", 0) for b in bars]
        range_expanding = all(ranges[i] < ranges[i + 1] for i in range(len(ranges) - 1)) if min(ranges) > 0 else False

        # Check that recent candles are DOWN biased
        closes = [b.get("close", 0) for b in bars]
        down_biased = all(closes[i] > closes[i + 1] for i in range(len(closes) - 1))

        return vol_rising and range_expanding and down_biased

    def _is_orb_down(self, bars: List[Dict]) -> bool:
        """Opening range breakdown downside"""
        if len(bars) < 5:
            return False

        or_bars = bars[:4]
        or_high = max(b.get("high", 0) for b in or_bars)
        or_low = min(b.get("low", 0) for b in or_bars)

        last_bar = bars[-1]
        last_vol = last_bar.get("volume", 0)
        avg_vol = sum(b.get("volume", 0) for b in or_bars) / len(or_bars)

        # Breakdown: price below OR with volume
        price_breakdown = last_bar.get("close", 0) < or_low * 0.998
        vol_surge = last_vol > avg_vol * 1.5

        return price_breakdown and vol_surge


if __name__ == "__main__":
    print("="*70)
    print("INVERSE ETP ENTRY TIMING TEST")
    print("="*70)

    inv_timing = InverseETPEntryTiming()

    # Test case: 3USS.L (inverse ETF, tracks -1x SPY)
    # Perfect short entry: SPY gap down, negative OFI, bearish divergence
    test_data = {
        "current_price": 45.50,  # Lower than yesterday
        "bid": 45.40,
        "ask": 45.60,
        "volume": 200000,
        "vix": 18,
        "realized_vol": 22.0,  # Elevated
        "atr": 1.5,
        "bb_width_pct": 75,  # Expansion
        "momentum": -1.5,  # Bearish
        "ofi": -0.35,  # Strong sell
        "ofi_rising": True,  # Rising (more negative)
        "volume_profile_hvn": 46.50,  # Price breaking HVN down
        "vtd_ratio": 0.65,  # Downside flow
        "hawkes_branching_ratio": 0.60,  # Self-exciting
        "hawkes_trending": True,
        "atm_accel": 1.3,  # Accelerating
        "gap_pct": -2.5,  # Gap down
        "bb_upper": 47.0,
        "recent_return_first_30m": -0.4,  # Negative early return
        "market_regime": "EXPANSION",
        "recent_bars": [
            {"open": 47.0, "high": 47.2, "low": 46.8, "close": 46.9, "volume": 150000},
            {"open": 46.9, "high": 46.5, "low": 46.0, "close": 46.1, "volume": 180000},
            {"open": 46.1, "high": 46.2, "low": 45.3, "close": 45.5, "volume": 250000},
        ]
    }

    result = inv_timing.is_perfect_short_entry("3USS.L", test_data)

    print(f"\nShort Entry: {result.should_short}")
    print(f"Confidence: {result.confidence:.1f}%")
    print(f"Reason: {result.reason}")
    print(f"\nSignals Fired ({len(result.signals_fired)} total):")
    for sig in result.signals_fired:
        print(f"  {sig}")

    print(f"\n{'='*70}")
    if result.should_short:
        print(f"✅ PERFECT SHORT ENTRY DETECTED (3USS.L)")
        print(f"   Confidence: {result.confidence:.1f}%")
        print(f"   This would be ideal time to SHORT SPY / LONG 3USS.L")
    else:
        print(f"❌ NOT READY FOR SHORT ENTRY")

