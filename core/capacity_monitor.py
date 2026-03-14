"""
Capacity Constraint Monitor -- NZT-48 Institutional Risk Metric
Bouchaud, Farmer & Lillo (2009): market impact scales with sqrt(Q/ADV).
Zhu (2014): dark pool capacity constraints for thin instruments.
Pre-emptively flags when tickers become untradeable at scale.
"""

import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

# Average daily volumes (GBP) -- conservative estimates
_ADV_GBP = {
    "QQQ3.L": 5_000_000,
    "3LUS.L": 2_000_000,
    "3SEM.L": 500_000,
    "GPT3.L": 300_000,
    "NVD3.L": 1_000_000,
    "TSL3.L": 800_000,
    "TSM3.L": 400_000,
    "MU2.L": 200_000,
    "QQQS.L": 400_000,
    "3USS.L": 300_000,
    "QQQ5.L": 1_000_000,
    "SP5L.L": 500_000,
}

# Capacity tier thresholds (as fraction of ADV)
_TIER_GREEN = 0.005   # < 0.5% ADV = no impact
_TIER_AMBER = 0.02    # 0.5-2% ADV = small but measurable
# > 2% ADV = significant impact (RED)

# Market impact constant (Bouchaud et al. 2009)
_IMPACT_K = 0.14      # Empirical constant for European ETPs
_DAILY_SIGMA = 0.02   # 2% daily vol estimate for leveraged ETPs

# Portfolio scale breakpoints for projection
_SCALE_POINTS = [10_000, 50_000, 100_000, 500_000, 1_000_000]


class CapacityConstraintMonitor:
    """
    Tracks available capacity per ticker at current portfolio size.
    Pre-emptively flags capacity constraints before they hit.

    Bouchaud et al. (2009): Market impact = Y x sigma x sqrt(Q/V)
    Where: Y=0.14, sigma=daily vol, Q=order GBP, V=ADV GBP
    Impact doubles on round-trip.
    """

    def get_capacity_status(self, ticker: str, position_gbp: float) -> dict:
        """
        Returns capacity tier and impact estimate for given trade size.

        Returns: {tier, impact_est_bps, pct_adv, max_clean_size_gbp,
                  scale_limit_portfolio_gbp}
        """
        adv = _ADV_GBP.get(ticker, 500_000)
        pct_adv = position_gbp / adv if adv > 0 else 999

        # Market impact (round-trip)
        q_over_v = pct_adv
        impact_bps = (
            _IMPACT_K * _DAILY_SIGMA * math.sqrt(q_over_v) * 10000 * 2
        )

        # Tier classification
        if pct_adv > _TIER_AMBER:
            tier = "RED"
        elif pct_adv > _TIER_GREEN:
            tier = "AMBER"
        else:
            tier = "GREEN"

        # Max "clean" size = 0.5% of ADV
        max_clean_size = adv * _TIER_GREEN

        # At what portfolio size (0.75% risk) does this ticker hit AMBER?
        # position_gbp ~ portfolio x 0.0075 / typical_stop_pct (1%)
        # Amber threshold: position_gbp > ADV x 0.005
        # So: portfolio x 0.0075 / 0.01 > ADV x 0.005
        # portfolio > ADV x 0.005 / 0.75 = ADV x 0.00667
        scale_amber_portfolio = adv * 0.005 / 0.0075
        scale_red_portfolio = adv * _TIER_AMBER / 0.0075

        return {
            "ticker": ticker,
            "tier": tier,
            "impact_est_bps": round(impact_bps, 1),
            "pct_adv": round(pct_adv * 100, 4),
            "max_clean_size_gbp": round(max_clean_size, 0),
            "scale_amber_at_portfolio_gbp": round(scale_amber_portfolio, 0),
            "scale_red_at_portfolio_gbp": round(scale_red_portfolio, 0),
        }

    def should_skip_thin_etp(self, ticker: str, position_gbp: float) -> tuple:
        """True + reason if ticker is RED capacity tier."""
        status = self.get_capacity_status(ticker, position_gbp)
        if status["tier"] == "RED":
            pct = status["pct_adv"]
            max_clean = status["max_clean_size_gbp"]
            return True, (
                f"{ticker} CAPACITY_RED ({pct:.2f}% ADV, "
                f"max_clean=GBP{max_clean:.0f})"
            )
        return False, "ok"

    def get_capacity_dashboard(self, portfolio_gbp: float) -> list:
        """
        Returns capacity status for all ISA tickers at given portfolio size.
        position_gbp ~ portfolio x 0.75% risk / 1% stop ~ portfolio x 0.75
        """
        typical_position = portfolio_gbp * 0.0075  # 0.75% risk
        results = []
        for ticker in _ADV_GBP:
            status = self.get_capacity_status(ticker, typical_position)
            results.append(status)
        return sorted(results, key=lambda x: x["pct_adv"], reverse=True)

    def get_telegram_summary(self, portfolio_gbp: float = 10_000) -> str:
        dashboard = self.get_capacity_dashboard(portfolio_gbp)
        lines = [f"Capacity Monitor (GBP{portfolio_gbp:,.0f} portfolio):"]
        for status in dashboard:
            tier = status["tier"]
            ticker = status["ticker"]
            pct = status["pct_adv"]
            amber_at = status["scale_amber_at_portfolio_gbp"]
            lines.append(
                f"  {ticker}: {tier} "
                f"({pct:.3f}% ADV | "
                f"amber@GBP{amber_at:,.0f})"
            )
        return chr(10).join(lines)
