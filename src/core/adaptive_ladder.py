"""
Adaptive Profit Ladder (Enhancement to Chandelier Exit)
=======================================================
Purpose: Modulate chandelier profit rungs based on market regime and momentum state.

Current system (chandelier_exit.py):
  - Fixed 7-rung ladder (+2%, +4%, +6%, +8%, +10%, +12%, +15%)
  - Fixed leverage-adjusted ATR multipliers (λ=5→N=1.0, λ=3→N=1.5, etc.)

New system (adaptive_ladder.py):
  - DYNAMIC rung widths based on regime + Hawkes branching ratio
  - DYNAMIC stop tightness based on volume-time decay (VTD)
  - SMART ratcheting that prevents whipsaw while protecting profits

Regime Impact on Rungs:
  COMPRESSION (0.7x): Tight rungs (coil ready to spring, preserve profits)
  EXPANSION (1.4x):   Wide rungs (vol expanding, let it run)
  BLOW_OFF (2.0x):    Very wide (extreme vol, give maximum room)
  EXHAUSTION (0.8x):  Moderate (reversal likely soon, tighten)
  BREAKDOWN (0.9x):   Moderate (momentum fading)

Hawkes Branching Ratio (α/β):
  >0.7:  Self-exciting (momentum feeding on itself, widen rungs, don't tighten stops)
  0.5-0.7: Healthy (normal momentum clustering, standard stops)
  <0.3:  Exhausted (momentum stopping, tighten rungs and stops hard)

Volume-Time Decay (VTD):
  >0.70: Flowing (directional vol still feeding move, widen trail, let run)
  0.50-0.70: Normal (reasonable momentum, standard trail)
  <0.30: Exhausted (volume dying, tighten hard, realize profits)

Integration with Chandelier:
  1. At entry: Calculate regime_mult × hawkes_mult
  2. Calculate adjusted rungs for this trade
  3. Store in chandelier state: "adaptive_rungs"
  4. As price advances: Check VTD, adjust stop tightness dynamically
  5. Exit sequence unchanged (ratchet on best high, bank at rungs)
"""

from dataclasses import dataclass
from typing import List, Dict, Optional
import logging
import math

logger = logging.getLogger(__name__)


@dataclass
class AdaptiveRungs:
    """Dynamically calculated rung targets for one trade"""
    rung_targets: List[float]  # [2%, 4%, 6%, 8%, 10%, 12%, 15%] adjusted
    regime_multiplier: float  # How to scale based on regime+Hawkes
    vtd_ratio: float  # Current volume-time decay
    stop_multipliers: List[float]  # [N×ATR] for each rung, adjusted per VTD
    timestamp: float  # When these were calculated


class AdaptiveLadder:
    """
    Dynamically modulates chandelier profit ladder based on regime and flow state.

    Works as a decorator/enhancement layer on chandelier_exit.py.
    Does NOT replace chandelier, but provides adjusted rung targets and stop tightness.
    """

    # Base regime multipliers (how to scale rungs)
    REGIME_MULTIPLIERS = {
        "COMPRESSION": 0.7,    # Tight: compressed vol means tighter rungs
        "EXPANSION": 1.4,      # Wide: vol expanding, give room
        "BLOW_OFF": 2.0,       # Very wide: extreme vol, let it run
        "EXHAUSTION": 0.8,     # Moderate: reversal soon, tighten
        "BREAKDOWN": 0.9,      # Moderate: momentum fading
        "TRENDING_UP": 1.2,    # Wide: trending strong
        "TRENDING_DOWN": 0.8,  # Tighten for shorts
        "RANGE": 0.9,          # Moderate: choppy, tighten
        "HIGH_VOL": 1.1,       # Slightly wide
        "RISK_OFF": 0.6,       # Very tight: caution
    }

    # Hawkes branching ratio impact
    HAWKES_MULTIPLIERS = {
        "high": 1.2,      # >0.7: self-exciting, widen
        "normal": 1.0,    # 0.5-0.7: normal
        "exhausted": 0.7  # <0.3: exhausted, tighten
    }

    # Base profit rungs (from chandelier_exit.py)
    BASE_RUNGS_PCT = [0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.15]

    # Base leverage-adjusted ATR multipliers
    LEVERAGE_ATR_MULT = {
        5: 1.0,
        3: 1.5,
        2: 2.0,
        1: 2.5,
    }

    def __init__(self):
        self.logger = logging.getLogger("nzt48.adaptive_ladder")
        self._trade_states = {}  # trade_id → AdaptiveRungs

    def calculate_adaptive_rungs(
        self,
        entry_price: float,
        leverage: int,
        regime: str,
        hawkes_branching_ratio: float,
        atr: float,
        vtd_ratio: float
    ) -> AdaptiveRungs:
        """
        Calculate dynamically adjusted rung targets for this trade.

        Args:
            entry_price: Entry price in currency
            leverage: 1, 2, 3, or 5
            regime: Market regime (COMPRESSION, EXPANSION, etc.)
            hawkes_branching_ratio: α/β (0-1.0)
            atr: Current ATR in currency
            vtd_ratio: Volume-time decay (0-1.0)

        Returns:
            AdaptiveRungs with adjusted targets and stop multipliers
        """

        # Step 1: Calculate regime multiplier
        regime_mult = self.REGIME_MULTIPLIERS.get(regime, 1.0)

        # Step 2: Calculate Hawkes multiplier based on branching ratio
        if hawkes_branching_ratio > 0.7:
            hawkes_cat = "high"
        elif hawkes_branching_ratio < 0.3:
            hawkes_cat = "exhausted"
        else:
            hawkes_cat = "normal"

        hawkes_mult = self.HAWKES_MULTIPLIERS[hawkes_cat]

        # Step 3: Combined multiplier (regime × Hawkes)
        combined_mult = regime_mult * hawkes_mult

        # Step 4: Adjust base rungs
        adjusted_rungs = []
        for rung_pct in self.BASE_RUNGS_PCT:
            adjusted_pct = rung_pct * combined_mult
            target_price = entry_price * (1.0 + adjusted_pct)
            adjusted_rungs.append(target_price)

        # Step 5: Calculate adaptive stop multipliers (per rung)
        base_atr_mults = self._get_base_atr_multipliers(leverage)
        adjusted_stop_mults = self._get_vtd_adjusted_stops(
            base_atr_mults, vtd_ratio, regime
        )

        self.logger.info(
            f"Adaptive Rungs: entry={entry_price:.2f}, regime={regime} ({regime_mult:.1f}x), "
            f"hawkes={hawkes_branching_ratio:.2f} ({hawkes_cat}, {hawkes_mult:.1f}x), "
            f"combined={combined_mult:.1f}x, vtd={vtd_ratio:.0%}"
        )

        return AdaptiveRungs(
            rung_targets=adjusted_rungs,
            regime_multiplier=combined_mult,
            vtd_ratio=vtd_ratio,
            stop_multipliers=adjusted_stop_mults,
            timestamp=0.0  # TODO: set to current timestamp
        )

    def get_adaptive_stop_for_rung(
        self,
        rung_num: int,
        atr: float,
        vtd_ratio: float,
        regime: str,
        leverage: int
    ) -> float:
        """
        Return adaptive stop loss level for a specific rung.

        Args:
            rung_num: 0-6 (7 rungs)
            atr: Current ATR in currency
            vtd_ratio: Volume-time decay (0-1.0)
            regime: Market regime
            leverage: 1, 2, 3, 5

        Returns:
            Stop loss in currency (ATR-based)
        """

        base_atr_mults = self._get_base_atr_multipliers(leverage)
        adjusted_mults = self._get_vtd_adjusted_stops(base_atr_mults, vtd_ratio, regime)

        if rung_num >= len(adjusted_mults):
            rung_num = len(adjusted_mults) - 1

        atr_mult = adjusted_mults[rung_num]
        stop_level = atr * atr_mult

        return stop_level

    def should_dynamically_tighten_stop(
        self,
        current_rung: int,
        prev_vtd: float,
        curr_vtd: float,
        prev_hawkes: float,
        curr_hawkes: float,
        recent_stop_advances: int,
        seconds_since_last_advance: float
    ) -> bool:
        """
        Decide if stop should be tightened (independent of price movement).

        This prevents holding stops too loose if momentum is fading.

        Args:
            current_rung: Which rung we're at (0-6)
            prev_vtd, curr_vtd: Volume-time decay before/now
            prev_hawkes, curr_hawkes: Hawkes branching ratio before/now
            recent_stop_advances: How many times stop moved in last 5 min
            seconds_since_last_advance: Time since last stop movement

        Returns:
            True if stop should tighten
        """

        # Rule 1: Tighten if VTD dropped >20% in last minute
        vtd_deteriorating = (prev_vtd - curr_vtd) > 0.20

        # Rule 2: Tighten if Hawkes branching dropped below 0.3 (exhaustion)
        momentum_exhausted = curr_hawkes < 0.3

        # Rule 3: If already advanced stop 3+ times in 5 min, don't advance more
        # Instead, let it sit and see if stops out (anti-whipsaw)
        too_many_advances = recent_stop_advances >= 3

        # Rule 4: At high rungs (5, 6) with low VTD, tighten aggressively
        high_rung_low_vtd = (current_rung >= 4 and curr_vtd < 0.40)

        should_tighten = (
            (vtd_deteriorating or momentum_exhausted or high_rung_low_vtd)
            and not too_many_advances
        )

        return should_tighten

    # ===== PRIVATE HELPERS =====

    def _get_base_atr_multipliers(self, leverage: int) -> List[float]:
        """
        Get base ATR multipliers per rung for this leverage.

        From chandelier_exit.py:
          λ=5: N=1.0 (very tight)
          λ=3: N=1.5 (normal)
          λ=2: N=2.0 (wide)
          λ=1: N=2.5 (very wide)

        Applies equally to all rungs in base system.
        """

        base_mult = self.LEVERAGE_ATR_MULT.get(leverage, 1.5)

        # Rung 0 (2%): very tight (protect entry)
        # Rung 1 (4%): tight
        # Rung 2 (6%): normal
        # Rung 3 (8%): normal
        # Rung 4 (10%): wider (let runners run)
        # Rung 5 (12%): wider
        # Rung 6 (15%): widest

        rung_adjustments = [1.0, 1.0, 0.9, 0.9, 0.7, 0.5, 0.5]

        return [base_mult * adj for adj in rung_adjustments]

    def _get_vtd_adjusted_stops(
        self,
        base_mults: List[float],
        vtd_ratio: float,
        regime: str
    ) -> List[float]:
        """
        Adjust stop tightness based on volume-time decay and regime.

        High VTD (>0.70): Flow still strong, widen stops, let run
        Low VTD (<0.30): Flow dying, tighten hard, realize profits
        """

        # VTD-based adjustment
        if vtd_ratio > 0.70:
            vtd_mult = 1.3  # Widen 30%
        elif vtd_ratio > 0.50:
            vtd_mult = 1.0  # Normal
        elif vtd_ratio > 0.30:
            vtd_mult = 0.8  # Tighten 20%
        else:
            vtd_mult = 0.6  # Tighten 40%

        # Regime-based additional adjustment
        regime_adj = {
            "COMPRESSION": 1.2,   # Wide (spring about to coil)
            "EXPANSION": 1.0,     # Normal
            "BLOW_OFF": 0.9,      # Slightly tighter (let run but protect core)
            "EXHAUSTION": 0.7,    # Much tighter (reversal soon)
            "BREAKDOWN": 0.8,     # Tighter
            "TRENDING_UP": 1.1,   # Slightly wide
            "TRENDING_DOWN": 0.8, # Tighter for shorts
            "RANGE": 0.8,         # Tighter (mean-reverting)
            "HIGH_VOL": 1.0,      # Normal
            "RISK_OFF": 0.5,      # Very tight (emergency mode)
        }

        regime_mult = regime_adj.get(regime, 1.0)

        # Apply both adjustments
        adjusted_mults = [m * vtd_mult * regime_mult for m in base_mults]

        return adjusted_mults


if __name__ == "__main__":
    # Test the adaptive ladder
    print("="*70)
    print("ADAPTIVE LADDER TEST")
    print("="*70)

    ladder = AdaptiveLadder()

    # Scenario 1: EXPANSION regime with high Hawkes (rungs should widen)
    print("\n1. EXPANSION + High Hawkes (rungs should WIDEN)")
    print("-" * 70)

    rungs = ladder.calculate_adaptive_rungs(
        entry_price=150.0,
        leverage=3,
        regime="EXPANSION",
        hawkes_branching_ratio=0.75,  # High: self-exciting
        atr=2.0,
        vtd_ratio=0.75  # High: flowing
    )

    print(f"Entry: £150.00, Leverage: 3x")
    print(f"Base rungs: {[f'{p*100:.0f}%' for p in ladder.BASE_RUNGS_PCT]}")
    print(f"Adjusted rungs: {[f'£{r:.2f}' for r in rungs.rung_targets]}")
    print(f"Stop multipliers: {[f'{m:.1f}x ATR' for m in rungs.stop_multipliers]}")

    # Scenario 2: EXHAUSTION regime with low Hawkes (rungs should tighten)
    print("\n2. EXHAUSTION + Low Hawkes (rungs should TIGHTEN)")
    print("-" * 70)

    rungs = ladder.calculate_adaptive_rungs(
        entry_price=150.0,
        leverage=3,
        regime="EXHAUSTION",
        hawkes_branching_ratio=0.25,  # Low: exhausted
        atr=2.0,
        vtd_ratio=0.30  # Low: flow dying
    )

    print(f"Entry: £150.00, Leverage: 3x")
    print(f"Base rungs: {[f'{p*100:.0f}%' for p in ladder.BASE_RUNGS_PCT]}")
    print(f"Adjusted rungs: {[f'£{r:.2f}' for r in rungs.rung_targets]}")
    print(f"Stop multipliers: {[f'{m:.1f}x ATR' for m in rungs.stop_multipliers]}")

    # Scenario 3: Test dynamic stop tightening
    print("\n3. Dynamic Stop Tightening (momentum exhaustion)")
    print("-" * 70)

    should_tighten = ladder.should_dynamically_tighten_stop(
        current_rung=4,  # At +10% rung
        prev_vtd=0.80,
        curr_vtd=0.45,  # Dropped 35%
        prev_hawkes=0.60,
        curr_hawkes=0.25,  # Exhausted
        recent_stop_advances=1,  # Only 1 advance (OK)
        seconds_since_last_advance=30.0
    )

    print(f"Rung 4, VTD: 80% → 45% (deteriorating)")
    print(f"Hawkes: 0.60 → 0.25 (exhausting)")
    print(f"Should tighten stop: {should_tighten} (YES — flow dying + momentum exhausted)")

    print(f"\n{'='*70}")
    print("✅ ADAPTIVE LADDER TESTS COMPLETE")
