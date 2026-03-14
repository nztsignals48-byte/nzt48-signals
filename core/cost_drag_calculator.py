"""
Cost Drag Calculator -- NZT-48 Institutional Risk Metric
Frazzini, Israel & Moskowitz (2015): Trading Costs of Asset Pricing Anomalies.
Korajczyk & Sadka (2004): momentum profits reduce 50-80% after trading costs.
Computes full-cycle cost: spread + market impact + volatility drag + mgmt fee.
"""

import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

# Per-ticker spread estimates (basis points)
_SPREAD_BPS = {
    "QQQ3.L": 15, "3LUS.L": 20, "3SEM.L": 25, "GPT3.L": 30,
    "NVD3.L": 20, "TSL3.L": 25, "TSM3.L": 25, "MU2.L": 30,
    "QQQS.L": 15, "3USS.L": 15, "QQQ5.L": 12, "SP5L.L": 12,
}

# Approximate average daily volumes (GBP)
_ADV_GBP = {
    "QQQ3.L": 5_000_000, "3LUS.L": 2_000_000, "3SEM.L": 500_000, "GPT3.L": 300_000,
    "NVD3.L": 1_000_000, "TSL3.L": 800_000, "TSM3.L": 400_000, "MU2.L": 200_000,
    "QQQS.L": 400_000, "3USS.L": 300_000, "QQQ5.L": 1_000_000, "SP5L.L": 500_000,
}

# Management fee (p.a.) for Leverage Shares ETPs
_MGMT_FEE_PA = 0.0075  # 0.75% p.a.

# Market impact constant (Bouchaud et al. 2009)
_IMPACT_LIQUID_K = 0.10   # Liquid ETPs
_IMPACT_THIN_K = 0.50     # Thin ETPs

# Capacity thresholds as fraction of ADV
_CAPACITY_AMBER_PCT = 0.005  # 0.5% ADV
_CAPACITY_RED_PCT = 0.02     # 2% ADV


class CostDragCalculator:
    """
    Full-cycle cost drag for each trade setup.

    Components (Frazzini, Israel & Moskowitz 2015):
    1. Bid-Ask Spread (round-trip)
    2. Market Impact: k x sqrt(trade_size / ADV)
    3. Volatility Drag (leveraged ETPs path-dependency): -0.5 x lev^2 x sigma^2 per day
    4. Management Fee: 0.75% p.a. = 0.003% per day, 0.006% round-trip (1 day)
    """

    def get_total_drag_bps(self, ticker: str, position_gbp: float,
                           hold_days: float = 1.0, daily_sigma: float = 0.015) -> dict:
        """
        Returns full cost breakdown in basis points.

        Args:
            ticker: LSE ETP ticker
            position_gbp: Trade size in GBP
            hold_days: Holding period in days
            daily_sigma: Daily volatility estimate (default 1.5%)
        """
        spread_bps = _SPREAD_BPS.get(ticker, 20)
        adv = _ADV_GBP.get(ticker, 500_000)

        # Market impact (Bouchaud et al. 2009: Y x sigma x sqrt(Q/V))
        is_thin = adv < 300_000
        k = _IMPACT_THIN_K if is_thin else _IMPACT_LIQUID_K
        q_over_v = position_gbp / adv if adv > 0 else 0
        impact_bps = k * daily_sigma * math.sqrt(q_over_v) * 10000 * 2  # round-trip

        # Volatility drag (leveraged ETP path-dependency)
        leverage = 5 if ticker in ("QQQ5.L", "SP5L.L") else 3
        vol_drag_daily = 0.5 * (leverage ** 2) * (daily_sigma ** 2)
        vol_drag_bps = vol_drag_daily * hold_days * 10000

        # Management fee (round-trip, per day held)
        mgmt_fee_bps = (_MGMT_FEE_PA / 252) * hold_days * 10000 * 2  # round-trip

        total_bps = spread_bps + impact_bps + vol_drag_bps + mgmt_fee_bps

        # Capacity check
        pct_adv = (position_gbp / adv) * 100 if adv > 0 else 999
        if pct_adv > _CAPACITY_RED_PCT * 100:
            capacity = "RED"
        elif pct_adv > _CAPACITY_AMBER_PCT * 100:
            capacity = "AMBER"
        else:
            capacity = "GREEN"

        return {
            "ticker": ticker,
            "spread_bps": round(spread_bps, 1),
            "impact_bps": round(impact_bps, 1),
            "vol_drag_bps": round(vol_drag_bps, 1),
            "mgmt_fee_bps": round(mgmt_fee_bps, 1),
            "total_bps": round(total_bps, 1),
            "capacity": capacity,
            "pct_adv": round(pct_adv, 3),
            "position_gbp": position_gbp,
        }

    def is_capacity_constrained(self, ticker: str, position_gbp: float) -> tuple:
        """True + reason if trade_size > 1% ADV."""
        drag = self.get_total_drag_bps(ticker, position_gbp)
        if drag["capacity"] in ("RED",):
            cap = drag["capacity"]
            pct = drag["pct_adv"]
            return True, f"{ticker} capacity={cap} ({pct:.2f}% ADV)"
        return False, "ok"

    def get_net_edge_after_costs(self, ticker: str, gross_r: float,
                                 position_gbp: float, stop_pct: float = 0.01) -> float:
        """
        Returns gross R minus cost drag expressed as R-multiples.
        stop_pct: stop distance as fraction of entry (default 1%)
        """
        drag = self.get_total_drag_bps(ticker, position_gbp)
        cost_in_r = (drag["total_bps"] / 10000) / stop_pct if stop_pct > 0 else 0
        return round(gross_r - cost_in_r, 4)

    def get_telegram_summary(self, tickers: list, position_gbp: float = 750) -> str:
        lines = [f"Cost Drag (GBP{position_gbp:.0f} position):"]
        for t in tickers:
            drag = self.get_total_drag_bps(t, position_gbp)
            cap = drag["capacity"]
            total = drag["total_bps"]
            pct = drag["pct_adv"]
            lines.append(
                f"  {t}: {total:.1f}bps total | "
                f"cap={cap} ({pct:.3f}% ADV)"
            )
        return chr(10).join(lines)
