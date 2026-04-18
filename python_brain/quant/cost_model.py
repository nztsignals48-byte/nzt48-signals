"""
IBKR-Calibrated Cost Model

Computes net edge after:
- Commission (IBKR flat + tiered)
- Spread cost (half-spread at entry, half-spread at exit = full)
- Temporary market impact (Almgren square-root formula)
- Permanent market impact (10% of temporary)
- UK PTM levy (if UK trade)

Returns {net_edge_bps, components} for ACCEPT/REJECT decision.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class CostBreakdown:
    commission_bps: float
    spread_bps: float
    impact_bps: float
    permanent_bps: float
    ptm_bps: float
    total_bps: float
    net_edge_bps: float


class CostModel:
    """Cost model calibrated to IBKR ISA + paper account fees."""

    def __init__(
        self,
        commission_min_usd: float = 1.0,
        commission_per_share_usd: float = 0.005,
        commission_pct_max: float = 0.01,
        ptm_levy_bps: float = 50.0,  # UK only
        default_spread_bps: float = 5.0,
        min_edge_bps: float = 2.0,
    ):
        self.commission_min = commission_min_usd
        self.commission_per_share = commission_per_share_usd
        self.commission_pct_max = commission_pct_max
        self.ptm_levy_bps = ptm_levy_bps
        self.default_spread_bps = default_spread_bps
        self.min_edge_bps = min_edge_bps

    def commission_bps(self, shares: int, fill_price: float) -> float:
        """IBKR tiered commission converted to bps."""
        if shares <= 0 or fill_price <= 0:
            return 0.0
        notional = shares * fill_price
        commission_usd = max(
            self.commission_min,
            min(shares * self.commission_per_share, notional * self.commission_pct_max),
        )
        return (commission_usd / notional) * 10000

    def impact_bps(self, shares: int, adv_shares: float, vol_bps: float) -> float:
        """Almgren square-root temporary impact."""
        if adv_shares <= 0 or shares <= 0:
            return 0.0
        participation = min(abs(shares) / adv_shares, 1.0)
        return 0.314 * (vol_bps / 10000) * math.sqrt(participation) * 10000

    def net_edge_bps(
        self,
        gross_edge_bps: float,
        shares: int,
        fill_price: float,
        adv_shares: float = 1_000_000,
        spread_bps: float = None,
        vol_bps: float = 100.0,
        is_uk: bool = False,
    ) -> dict:
        """
        Compute net edge = gross_edge - all costs.

        Returns dict with component breakdown + net_edge_bps.
        """
        if spread_bps is None:
            spread_bps = self.default_spread_bps

        comm = self.commission_bps(shares, fill_price)
        spread_cost = spread_bps  # full round-trip spread
        temp_impact = self.impact_bps(shares, adv_shares, vol_bps)
        perm_impact = 0.1 * temp_impact  # 10% of temporary
        ptm = self.ptm_levy_bps if is_uk else 0.0

        total_cost = comm + spread_cost + temp_impact + perm_impact + ptm
        net = gross_edge_bps - total_cost

        return {
            "gross_edge_bps": gross_edge_bps,
            "commission_bps": round(comm, 2),
            "spread_bps": round(spread_cost, 2),
            "impact_bps": round(temp_impact, 2),
            "permanent_bps": round(perm_impact, 2),
            "ptm_bps": round(ptm, 2),
            "total_cost_bps": round(total_cost, 2),
            "net_edge_bps": round(net, 2),
            "passes_min_edge": net >= self.min_edge_bps,
        }

    def should_trade(
        self,
        gross_edge_bps: float,
        shares: int,
        fill_price: float,
        adv_shares: float = 1_000_000,
        **kwargs,
    ) -> tuple[bool, dict]:
        """Binary accept/reject decision."""
        result = self.net_edge_bps(
            gross_edge_bps, shares, fill_price, adv_shares, **kwargs
        )
        return result["passes_min_edge"], result


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        cm = CostModel()
        # Good trade: 50 bps edge, 1000 shares at $100
        r = cm.net_edge_bps(50, 1000, 100, adv_shares=1_000_000)
        print(f"Good trade: gross={r['gross_edge_bps']}, costs={r['total_cost_bps']}, net={r['net_edge_bps']}")

        # Bad trade: 2 bps edge, same size
        r2 = cm.net_edge_bps(2, 1000, 100, adv_shares=1_000_000)
        print(f"Bad trade: gross={r2['gross_edge_bps']}, costs={r2['total_cost_bps']}, net={r2['net_edge_bps']}")
        print(f"Passes? {r['passes_min_edge']} vs {r2['passes_min_edge']}")
        print("OK")
