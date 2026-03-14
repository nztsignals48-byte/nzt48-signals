"""Phase 6: Volatility Scaler (Moreira-Muir Dynamic Leverage)"""
import numpy as np
from dataclasses import dataclass

@dataclass
class VolResult:
    vol_scalar: float
    regime: str

class VolScaler:
    def __init__(self, target_vol=15.0):
        self.target_vol = target_vol

    def scale(self, realized_vol: float, regime: str) -> VolResult:
        """Scale leverage inversely to realized vol"""
        scalar = self.target_vol / realized_vol if realized_vol > 0 else 1.0
        scalar = np.clip(scalar, 0.5, 1.5)

        if regime == "HIGH_VOL":
            scalar = min(scalar, 1.0)
        elif regime == "RISK_OFF":
            scalar = min(scalar, 0.5)

        return VolResult(vol_scalar=scalar, regime=regime)

if __name__ == "__main__":
    scaler = VolScaler()
    r1 = scaler.scale(10, "TRENDING_UP")
    r2 = scaler.scale(25, "HIGH_VOL")
    print(f"✓ Vol scalar quiet: {r1.vol_scalar:.2f}x")
    print(f"✓ Vol scalar high: {r2.vol_scalar:.2f}x")
    print("✅ Phase 6 (Vol Scaler) complete")
