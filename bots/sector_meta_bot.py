"""
NZT-48 Sector Rotation Meta-Bot
Phase 2: Monitors sector ETF flows and adjusts bot/strategy weighting.

Tracks 6 sector ETFs from the config:
- XLE (Energy), XLU (Utilities/AI power), XLI (Industrials/data centres)
- XLF (Financials/rates), GDX (Gold miners/fear), KWEB (China tech)

Functions:
1. Rank sectors by 20-day relative strength vs SPY
2. Identify sector inflows/outflows (money flow direction)
3. Boost confidence for tickers in top-2 sectors
4. Reduce confidence for tickers in bottom-2 sectors
5. Generate sector rotation signals (S7 strategy support)
6. Weekly rebalancing recommendation
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import (
    BotInstance, MarketContext, SectorFlow,
)
import config as cfg


# Map tickers to their sector ETFs
TICKER_TO_SECTOR_ETF = {
    "NVDA": "SMH", "AMD": "SMH", "MU": "SMH", "SNDK": "SMH",
    "AVGO": "SMH", "MRVL": "SMH", "ARM": "SMH", "TSM": "SMH",
    "ASML": "SMH", "SMCI": "SMH", "VRT": "XLI", "TSLA": "XLY",
}

# The sector ETFs we monitor for rotation (includes SMH and XLY used in TICKER_TO_SECTOR_ETF)
SECTOR_ETFS = ["XLE", "XLU", "XLI", "XLF", "GDX", "KWEB", "SMH", "XLY"]


class SectorRanking:
    """A sector's current ranking and flow data."""
    def __init__(self, etf: str):
        self.etf = etf
        self.rs_vs_spy_20d: float = 0.0  # 20-day relative strength vs SPY
        self.rs_vs_spy_5d: float = 0.0   # 5-day RS (short-term momentum)
        self.money_flow: str = "neutral"  # "inflow" / "outflow" / "neutral"
        self.rank: int = 0                # 1 = strongest, 6 = weakest
        self.recommendation: str = ""     # "overweight" / "neutral" / "underweight"


class SectorRotationMetaBot:
    """Meta-bot that monitors sector flows and adjusts strategy weighting.

    Runs weekly (and intraday for flow checks).
    Does NOT generate signals directly — instead modifies confidence
    scores and provides sector context to other strategies.
    """

    def __init__(self):
        self.logger = logging.getLogger("nzt48.sector_rotation")
        self.instance = BotInstance.SECTOR_ROTATION

        # Current rankings
        self.rankings: dict[str, SectorRanking] = {
            etf: SectorRanking(etf) for etf in SECTOR_ETFS
        }
        self.last_update: Optional[datetime] = None
        self._weekly_rotation_signal: Optional[dict] = None

    def update_rankings(
        self,
        sector_data: dict[str, dict],
        spy_return_20d: float = 0.0,
        spy_return_5d: float = 0.0,
    ) -> None:
        """Update sector rankings from price data.

        Args:
            sector_data: Dict of ETF -> {
                'return_20d': float,
                'return_5d': float,
                'volume_avg_ratio': float,  # current vs 20d avg
            }
            spy_return_20d: SPY 20-day return for RS calculation
            spy_return_5d: SPY 5-day return
        """
        for etf, data in sector_data.items():
            if etf not in self.rankings:
                continue

            ranking = self.rankings[etf]
            ranking.rs_vs_spy_20d = data.get("return_20d", 0) - spy_return_20d
            ranking.rs_vs_spy_5d = data.get("return_5d", 0) - spy_return_5d

            # Determine money flow from volume
            vol_ratio = data.get("volume_avg_ratio", 1.0)
            if ranking.rs_vs_spy_5d > 0.5 and vol_ratio > 1.2:
                ranking.money_flow = "inflow"
            elif ranking.rs_vs_spy_5d < -0.5 and vol_ratio > 1.2:
                ranking.money_flow = "outflow"
            else:
                ranking.money_flow = "neutral"

        # Sort by 20-day RS and assign ranks
        sorted_sectors = sorted(
            self.rankings.values(),
            key=lambda r: r.rs_vs_spy_20d,
            reverse=True,
        )
        for i, sector in enumerate(sorted_sectors):
            sector.rank = i + 1
            if sector.rank <= 2:
                sector.recommendation = "overweight"
            elif sector.rank >= 5:
                sector.recommendation = "underweight"
            else:
                sector.recommendation = "neutral"

        self.last_update = datetime.now(timezone.utc)
        self.logger.info(
            "Sector rankings updated: Top=%s(%+.1f%%) Bottom=%s(%+.1f%%)",
            sorted_sectors[0].etf, sorted_sectors[0].rs_vs_spy_20d * 100,
            sorted_sectors[-1].etf, sorted_sectors[-1].rs_vs_spy_20d * 100,
        )

    def get_ticker_sector_adjustment(self, ticker: str) -> int:
        """Get confidence adjustment for a ticker based on its sector ranking.

        Returns:
            Confidence adjustment: +5 to +10 for top sectors, -5 to -10 for bottom
        """
        sector_etf = TICKER_TO_SECTOR_ETF.get(ticker)
        if not sector_etf:
            return 0

        # Find the ranking for the ticker's sector
        # Most of our tickers map to SMH (semiconductors)
        # Use QQQ tech sector performance as proxy if SMH not in rankings
        ranking = self.rankings.get(sector_etf)
        if not ranking:
            return 0

        if ranking.rank <= 1:
            return 10  # Top sector: +10 confidence
        elif ranking.rank <= 2:
            return 5   # Second: +5
        elif ranking.rank >= 6:
            return -10  # Worst sector: -10
        elif ranking.rank >= 5:
            return -5   # Second worst: -5
        return 0

    def get_sector_flow(self, ticker: str) -> SectorFlow:
        """Build a SectorFlow object for a ticker."""
        sector_etf = TICKER_TO_SECTOR_ETF.get(ticker, "")
        ranking = self.rankings.get(sector_etf)

        if not ranking:
            return SectorFlow(
                timestamp=datetime.now(timezone.utc),
                ticker=ticker,
            )

        return SectorFlow(
            timestamp=datetime.now(timezone.utc),
            ticker=ticker,
            sector=sector_etf,
            rs_vs_spy=ranking.rs_vs_spy_20d,
            sector_etf_rs=ranking.rs_vs_spy_20d,
            money_flow_direction=ranking.money_flow,
            sector_rank=ranking.rank,
        )

    def generate_rotation_signal(self, market_ctx: MarketContext) -> Optional[dict]:
        """Generate weekly sector rotation recommendation.

        Returns a dict with:
        - overweight_sectors: list of ETFs to favor
        - underweight_sectors: list to avoid
        - rotation_detected: bool (has leadership changed?)
        - recommendation: str
        """
        if not self.rankings:
            return None

        sorted_rankings = sorted(
            self.rankings.values(),
            key=lambda r: r.rs_vs_spy_20d,
            reverse=True,
        )

        overweight = [r.etf for r in sorted_rankings if r.recommendation == "overweight"]
        underweight = [r.etf for r in sorted_rankings if r.recommendation == "underweight"]

        # Detect rotation: top sector has negative 5d RS (losing momentum)
        rotation_detected = False
        if sorted_rankings[0].rs_vs_spy_5d < 0:
            rotation_detected = True

        signal = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "overweight_sectors": overweight,
            "underweight_sectors": underweight,
            "rotation_detected": rotation_detected,
            "rankings": [
                {
                    "etf": r.etf,
                    "rank": r.rank,
                    "rs_20d": round(r.rs_vs_spy_20d * 100, 2),
                    "rs_5d": round(r.rs_vs_spy_5d * 100, 2),
                    "flow": r.money_flow,
                }
                for r in sorted_rankings
            ],
        }

        self._weekly_rotation_signal = signal
        return signal

    def should_avoid_sector(self, ticker: str) -> bool:
        """Check if a ticker's sector is in the underweight/avoid zone."""
        adjustment = self.get_ticker_sector_adjustment(ticker)
        return adjustment <= -5

    def get_status(self) -> dict:
        """Get sector rotation status."""
        sorted_rankings = sorted(
            self.rankings.values(),
            key=lambda r: r.rs_vs_spy_20d,
            reverse=True,
        )
        return {
            "instance": self.instance.value,
            "last_update": self.last_update.isoformat() if self.last_update else "Never",
            "rankings": [
                f"{r.etf}: rank={r.rank} RS={r.rs_vs_spy_20d*100:+.1f}% flow={r.money_flow}"
                for r in sorted_rankings
            ],
            "rotation_detected": (
                self._weekly_rotation_signal.get("rotation_detected", False)
                if self._weekly_rotation_signal else False
            ),
        }
