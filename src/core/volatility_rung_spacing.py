"""
Volatility-Aware Rung Spacing
=============================
Purpose: Adjust rung spacing based on current volatility environment.

The adaptive_ladder.py handles regime+Hawkes impact on RUNG WIDTHS.
This module handles intraday volatility environment impact.

High vol environment (e.g., after earnings, during crisis):
  → Rungs space WIDER (capture more of expected move before stopping out)
  → Reason: Expected move is 3-5%, rungs need more room to let winners run
  → RVOL >30% → space rungs 1.5x wider than normal

Low vol environment (e.g., summer doldrums, stable range):
  → Rungs space TIGHTER (mean revert faster, take profits faster)
  → Reason: Expected move is 1-2%, rungs too wide means exiting on noise
  → RVOL <10% → space rungs 0.8x normal (30% tighter)

Bollinger Band width also matters:
  Compression (BB width <20th pct): Volume about to explode, widen rungs 1.3x
  Expansion (BB width >80th pct): Vol already high, slightly tighter 0.9x

Integration:
  Apply AFTER regime/Hawkes multiply in adaptive_ladder.py
  Formula: final_rung = base_rung × regime_mult × hawkes_mult × vol_spacing_mult
"""

from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class VolatilitySpacing:
    """Volatility-aware rung spacing adjustment"""
    multiplier: float  # Multiplier to apply (0.8-1.8)
    rvol: float  # Realized volatility (%)
    bb_width_pct: float  # Bollinger Band width percentile (0-100)
    reason: str  # Human-readable explanation


class VolatilityRungSpacing:
    """
    Adjusts rung spacing based on intraday volatility patterns.

    Key insight: Wider rungs in high vol = capture more expected move.
                 Tighter rungs in low vol = don't get shaken out.
    """

    def __init__(self):
        self.logger = logging.getLogger("nzt48.vol_rung_spacing")

    def calculate_optimal_rung_spacing(
        self,
        ticker: str,
        realized_vol: float,
        bb_width_pct: float,
        atr_accel: float = 1.0
    ) -> VolatilitySpacing:
        """
        Calculate rung spacing multiplier based on current vol conditions.

        Args:
            ticker: Stock/ETP ticker
            realized_vol: Realized volatility (%, e.g., 15.0 for 15%)
            bb_width_pct: Bollinger Band width percentile (0-100)
            atr_accel: ATR acceleration ratio (0-2.0, 1.0=normal)

        Returns:
            VolatilitySpacing with multiplier and explanation
        """

        # Step 1: RVOL-based adjustment
        # Normal vol: 15%
        # Low vol: <10% → tighter (0.8x)
        # High vol: 20-30% → wider (1.2-1.5x)
        # Extreme vol: >30% → very wide (1.8x)

        if realized_vol < 8:
            rvol_mult = 0.75  # Very tight
            rvol_cat = "ultra-low"
        elif realized_vol < 10:
            rvol_mult = 0.80
            rvol_cat = "low"
        elif realized_vol < 12:
            rvol_mult = 0.85
            rvol_cat = "below-normal"
        elif realized_vol < 18:
            rvol_mult = 1.0  # Normal
            rvol_cat = "normal"
        elif realized_vol < 25:
            rvol_mult = 1.2
            rvol_cat = "elevated"
        elif realized_vol < 35:
            rvol_mult = 1.5
            rvol_cat = "high"
        else:
            rvol_mult = 1.8
            rvol_cat = "extreme"

        # Step 2: Bollinger Band width-based adjustment
        # BB width = (upper - lower) / SMA(20)
        # Low percentile (<20): compression, rungs should widen (expect breakout)
        # High percentile (>80): expansion, slightly tighten (already moving)

        if bb_width_pct < 15:
            bb_mult = 1.4  # Coil very tight, breakout imminent, widen rungs
            bb_cat = "super-compression"
        elif bb_width_pct < 20:
            bb_mult = 1.3
            bb_cat = "compression"
        elif bb_width_pct < 50:
            bb_mult = 1.0  # Normal
            bb_cat = "normal"
        elif bb_width_pct < 80:
            bb_mult = 0.95
            bb_cat = "expansion"
        else:
            bb_mult = 0.85  # Already expanded, tighten slightly
            bb_cat = "high-expansion"

        # Step 3: ATR acceleration adjustment
        # If ATR accelerating rapidly (atr_accel >1.2), volatility is increasing
        # Should widen rungs to capture extended move

        if atr_accel > 1.3:
            accel_mult = 1.15
            accel_cat = "accelerating"
        elif atr_accel > 1.1:
            accel_mult = 1.05
            accel_cat = "moderately-accelerating"
        elif atr_accel < 0.85:
            accel_mult = 0.9
            accel_cat = "decelerating"
        else:
            accel_mult = 1.0
            accel_cat = "steady"

        # Step 4: Combine all factors
        final_mult = rvol_mult * bb_mult * accel_mult

        # Cap at reasonable bounds
        final_mult = max(0.7, min(1.8, final_mult))

        reason = (
            f"RVOL {realized_vol:.0f}% ({rvol_cat}, {rvol_mult:.2f}x) × "
            f"BB {bb_width_pct:.0f}th pct ({bb_cat}, {bb_mult:.2f}x) × "
            f"ATR accel {atr_accel:.2f} ({accel_cat}, {accel_mult:.2f}x) = {final_mult:.2f}x"
        )

        self.logger.info(f"{ticker}: {reason}")

        return VolatilitySpacing(
            multiplier=final_mult,
            rvol=realized_vol,
            bb_width_pct=bb_width_pct,
            reason=reason
        )

    def apply_spacing_to_rungs(
        self,
        base_rungs: list,
        entry_price: float,
        spacing_mult: float
    ) -> list:
        """
        Apply spacing multiplier to existing rung targets.

        Args:
            base_rungs: List of rung prices (e.g., [152.0, 154.0, 156.0, ...])
            entry_price: Entry price
            spacing_mult: Multiplier to apply (0.7-1.8)

        Returns:
            Adjusted rung targets
        """

        adjusted = []
        for rung_price in base_rungs:
            # Calculate percent above entry
            rung_pct = (rung_price - entry_price) / entry_price

            # Adjust by spacing multiplier
            adjusted_pct = rung_pct * spacing_mult

            # Convert back to price
            adjusted_price = entry_price * (1 + adjusted_pct)
            adjusted.append(adjusted_price)

        return adjusted


if __name__ == "__main__":
    print("="*70)
    print("VOLATILITY RUNG SPACING TEST")
    print("="*70)

    spacing = VolatilityRungSpacing()

    # Test case 1: Low vol environment (summer doldrums)
    print("\n1. LOW VOLATILITY (summer, quiet market)")
    print("-" * 70)

    result1 = spacing.calculate_optimal_rung_spacing(
        ticker="QQQ3.L",
        realized_vol=8.5,  # Low
        bb_width_pct=10,   # Compressed
        atr_accel=0.8      # Decelerating
    )

    print(f"Spacing multiplier: {result1.multiplier:.2f}x (TIGHTEN rungs)")
    print(f"Reason: {result1.reason}")

    base = [152, 154, 156, 158, 160, 162, 165]
    adjusted = spacing.apply_spacing_to_rungs(base, 150, result1.multiplier)
    print(f"\nBase rungs:     {base}")
    print(f"Adjusted rungs: {[f'{p:.0f}' for p in adjusted]}")

    # Test case 2: High vol environment (earnings, crisis)
    print("\n2. HIGH VOLATILITY (earnings season, crisis)")
    print("-" * 70)

    result2 = spacing.calculate_optimal_rung_spacing(
        ticker="QQQ3.L",
        realized_vol=35.0,  # Extreme
        bb_width_pct=95,    # Expanded
        atr_accel=1.5       # Accelerating
    )

    print(f"Spacing multiplier: {result2.multiplier:.2f}x (WIDEN rungs)")
    print(f"Reason: {result2.reason}")

    adjusted = spacing.apply_spacing_to_rungs(base, 150, result2.multiplier)
    print(f"\nBase rungs:     {base}")
    print(f"Adjusted rungs: {[f'{p:.0f}' for p in adjusted]}")

    # Test case 3: Compression before breakout
    print("\n3. COMPRESSION (coil before breakout)")
    print("-" * 70)

    result3 = spacing.calculate_optimal_rung_spacing(
        ticker="QQQ3.L",
        realized_vol=9.0,   # Low
        bb_width_pct=8,     # Super-compressed
        atr_accel=0.9       # Steady
    )

    print(f"Spacing multiplier: {result3.multiplier:.2f}x (WIDEN for breakout room)")
    print(f"Reason: {result3.reason}")

    print(f"\n{'='*70}")
    print("✅ VOLATILITY RUNG SPACING TESTS COMPLETE")
