"""Phase 10: Execution Quality (Slippage Modeling)"""
from dataclasses import dataclass

@dataclass
class ExecQualityResult:
    expected_slippage_bps: float
    actual_slippage_bps: float
    within_tolerance: bool

class ExecutionQuality:
    SLIPPAGE_LIMITS = {
        "LSE": 25, "NASDAQ": 20, "EURONEXT": 15,
        "ASX": 20, "JPX": 35
    }

    def model_slippage(self, market: str, vol_regime: str, order_size_pct: float) -> float:
        """Model expected slippage in basis points"""
        base = self.SLIPPAGE_LIMITS.get(market, 30)

        vol_multiplier = 1.5 if vol_regime == "HIGH_VOL" else (2.0 if vol_regime == "RISK_OFF" else 1.0)
        size_multiplier = 1.0 + (order_size_pct * 2)  # +2% slippage per 1% of volume

        return base * vol_multiplier * size_multiplier

    def validate(
        self,
        market: str,
        expected_slippage_bps: float,
        actual_fill_price: float,
        mid_price: float
    ) -> ExecQualityResult:
        """Validate actual vs expected slippage"""
        actual = abs(actual_fill_price - mid_price) / mid_price * 10000

        return ExecQualityResult(
            expected_slippage_bps=expected_slippage_bps,
            actual_slippage_bps=actual,
            within_tolerance=actual <= expected_slippage_bps * 1.5  # Allow 50% variance
        )

if __name__ == "__main__":
    exec_q = ExecutionQuality()
    exp = exec_q.model_slippage("LSE", "TRENDING_UP", 0.1)
    val = exec_q.validate("LSE", exp, 100.02, 100.0)
    print(f"✓ Expected slippage: {exp:.1f} bps, actual: {val.actual_slippage_bps:.1f} bps")
    print("✅ Phase 10 (Execution Quality) complete")
