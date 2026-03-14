"""Phase 9: Position Sizer (Leverage Prioritization)"""
import sys
sys.path.insert(0, '/Users/rr/nzt48-signals')

from dataclasses import dataclass
from src.core.perfect_entry_filter import PerfectEntryFilter

@dataclass
class PositionResult:
    size: float
    leverage: float
    approved: bool

class PositionSizer:
    def __init__(self, kelly_size: float, vol_scalar: float):
        self.kelly_size = kelly_size
        self.vol_scalar = vol_scalar
        # WEEK 1: Perfect entry timing filter
        self.entry_filter = PerfectEntryFilter()

    def size(
        self,
        confidence: float,
        regime: str,
        asset_type: str,
        daily_gain_pct: float,
        equity: float,
        direction: str = "BUY"
    ) -> PositionResult:
        """Calculate position size with leverage prioritization"""
        base_size = self.kelly_size * self.vol_scalar

        # Leverage prioritization for LSE 3x/5x
        if asset_type == "LSE" and regime == "TRENDING_UP":
            if confidence > 7.5:
                leverage = 5.0
                size_boost = 1.5
            elif confidence > 6.5:
                leverage = 3.0
                size_boost = 1.2
            else:
                leverage = 1.0
                size_boost = 1.0
        else:
            leverage = 1.0
            size_boost = 1.0

        # Ralph Wiggum check: don't chase (if already +10% today, don't add)
        if confidence > 8.5 and daily_gain_pct > 10:
            return PositionResult(0, 0, False)  # Block trade

        final_size = base_size * size_boost * leverage

        # WEEK 1: Apply perfect entry filter to final size
        # This adjusts position size based on early detection confidence (0-100%)
        actual_size = self.entry_filter.apply_to_position_size(
            kelly_position_size=final_size,
            confidence_pct=confidence,
            direction=direction
        )

        approved = actual_size <= equity * 0.5  # Don't exceed 50% of equity in 1 trade

        return PositionResult(size=actual_size, leverage=leverage, approved=approved)

if __name__ == "__main__":
    sizer = PositionSizer(kelly_size=275, vol_scalar=1.5)
    r = sizer.size(7.8, "TRENDING_UP", "LSE", 0, 10000)
    print(f"✓ Position size: £{r.size:.0f}, leverage: {r.leverage:.1f}x, approved: {r.approved}")
    print("✅ Phase 9 (Position Sizer) complete")
