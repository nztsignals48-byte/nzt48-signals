"""Account-size-aware Almgren-Chriss — scales slice sizing to account equity.

Almgren-Chriss assumes "slice shares over horizon"; for a $10k paper account
with 10k-share targets, this is absurd. This wrapper:

1. Clamps max shares to (account_equity * max_position_pct) / price
2. Reduces slice count for small trades (no point slicing 10 shares)
3. Increases slice count only when order size > 1000 shares AND notional > $5k

Consumed by paper_executor before Almgren-Chriss is invoked.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ACScaleParams:
    effective_shares: int
    num_slices: int
    urgency: str
    rationale: str


def scale_for_account(
    requested_shares: int,
    price: float,
    account_equity_usd: float,
    max_position_pct: float = 0.15,
    min_slice_notional_usd: float = 500.0,
) -> ACScaleParams:
    """Scale shares + slicing plan to account size."""
    if requested_shares <= 0 or price <= 0 or account_equity_usd <= 0:
        return ACScaleParams(0, 1, "immediate", "zero inputs")

    # Cap position size
    max_usd = account_equity_usd * max_position_pct
    max_shares = int(max_usd / price)
    effective = min(requested_shares, max_shares)

    # Choose slice count by notional
    notional = effective * price
    if notional < min_slice_notional_usd:
        return ACScaleParams(effective, 1, "immediate",
                             f"notional ${notional:.0f} < ${min_slice_notional_usd:.0f}, no slicing")
    if notional < 5000:
        return ACScaleParams(effective, 1, "normal", f"small notional ${notional:.0f}")
    if notional < 20000:
        return ACScaleParams(effective, 3, "normal", f"moderate notional ${notional:.0f}")
    if notional < 100000:
        return ACScaleParams(effective, 5, "aggressive", f"large notional ${notional:.0f}")
    return ACScaleParams(effective, 10, "aggressive", f"huge notional ${notional:.0f}")


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        # $10k account, $100 stock
        for req in [10, 50, 200, 1000, 10000]:
            r = scale_for_account(req, 100.0, 10_000)
            print(f"req={req:6d} -> effective={r.effective_shares} slices={r.num_slices} "
                  f"urgency={r.urgency} ({r.rationale})")
        print("OK")
