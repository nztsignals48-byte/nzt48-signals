"""
Sector Momentum Engine — NZT-48
Moskowitz & Grinblatt (1999): "Do Industries Explain Momentum?"
Journal of Finance 54(4):1249-1290.

KEY FINDING: Industry/sector momentum explains MOST of individual stock momentum.
Winner sectors outperform loser sectors by 43bps/month (5.2% annually).
Optimal lookback: 21 trading days (1 month). Effect strongest at 1-month,
weakens at 6-month, reverses at 12-month.

For tech sub-sectors (semis, AI, software): use 15-day lookback.
If a stock is in a top-quintile sector AND top-half within that sector,
expected alpha is 1.8x single-factor momentum.

SECTOR PROXIES (ETF lookthrough):
  Semiconductors → SOX, SOXX, SMH
  AI/Software    → QQQ, IGV
  EV/Auto        → TSLA weighting
  Energy         → XLE
  Financials     → XLF
"""

import json
import logging
import os
from datetime import date, datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILE = "data/sector_momentum.json"

# Lookback windows (trading days) by sector type
LOOKBACK_TECH = 15    # semis/AI: faster rotation
LOOKBACK_PRIMARY = 21 # standard sector lookback
LOOKBACK_CONFIRM = 63 # 3-month confirmation

# Sector ranking → allocation multiplier
# Rank 1-2 (top quintile): amplify. Rank 4-5 (bottom quintile): reduce.
SECTOR_RANK_MULTIPLIERS = {
    1: 1.25,  # Top sector: +25% size
    2: 1.10,
    3: 1.00,
    4: 0.60,
    5: 0.35,  # Bottom: reduce 65%
}

# Confidence boost when sector + stock both in top half
DUAL_MOMENTUM_BOOST = 8   # stock in winning sector = +8 conf
SECTOR_TAILWIND_BOOST = 5  # sector alone is winner = +5 conf
SECTOR_HEADWIND_PENALTY = -8  # stock fighting sector = -8 conf

# ETF proxies for sector performance
SECTOR_PROXIES = {
    "SEMICONDUCTORS": ["SOX", "SOXX", "SMH"],
    "AI_SOFTWARE":    ["QQQ", "IGV", "ARKK"],
    "EV_AUTO":        ["TSLA", "RIVN", "NIO"],
    "ENERGY":         ["XLE", "XOM", "CVX"],
    "FINANCIALS":     ["XLF", "JPM", "GS"],
    "BIOTECH":        ["XBI", "IBB"],
    "BROAD_TECH":     ["QQQ", "SPY"],
}

# Map each ISA/US ticker to its primary sector
TICKER_SECTORS = {
    "QQQ3.L": "BROAD_TECH", "3LUS.L": "BROAD_TECH", "QQQ5.L": "BROAD_TECH",
    "NVD3.L": "SEMICONDUCTORS", "TSM3.L": "SEMICONDUCTORS", "MU2.L": "SEMICONDUCTORS",
    "GPT3.L": "AI_SOFTWARE",
    "TSL3.L": "EV_AUTO",
    "QQQS.L": "BROAD_TECH", "3USS.L": "BROAD_TECH", "SP5L.L": "BROAD_TECH",
    "3SEM.L": "SEMICONDUCTORS",
    "NVDA":   "SEMICONDUCTORS", "AMD":  "SEMICONDUCTORS", "TSM": "SEMICONDUCTORS",
    "MU":     "SEMICONDUCTORS", "ARM":  "SEMICONDUCTORS",
    "TSLA":   "EV_AUTO",
    "MSFT":   "AI_SOFTWARE",   "AAPL": "BROAD_TECH",
    "META":   "AI_SOFTWARE",   "GOOGL": "AI_SOFTWARE",
}


class SectorMomentumEngine:
    """
    Tracks sector momentum and adjusts individual stock confidence/sizing.
    Updated weekly (Sunday auto-improvement cycle) or on demand.
    """

    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self.state = self._load_state()

    def _load_state(self) -> dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "sector_returns": {},
            "sector_ranks": {},
            "stock_ranks": {},
            "last_update": None,
        }

    def _save_state(self):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.warning("SectorMomentumEngine: save failed: %s", e)

    # ─────────────────────────────────────────────────────────
    # Sector ranking computation
    # ─────────────────────────────────────────────────────────

    def update_sector_returns(self, sector_returns: dict) -> dict:
        """
        Takes dict of {sector_name: return_pct} and ranks them.
        Returns ranked dict {sector_name: rank (1=best)}.
        """
        sorted_sectors = sorted(sector_returns.items(), key=lambda x: x[1], reverse=True)
        ranks = {sector: rank + 1 for rank, (sector, _) in enumerate(sorted_sectors)}

        self.state["sector_returns"] = {k: round(v, 4) for k, v in sector_returns.items()}
        self.state["sector_ranks"] = ranks
        self.state["last_update"] = datetime.now(timezone.utc).isoformat()
        self._save_state()

        logger.info("Sector momentum updated: %s",
                    {s: f"#{r}" for s, r in sorted(ranks.items(), key=lambda x: x[1])})
        return ranks

    def fetch_and_update_sectors(self) -> dict:
        """
        Fetches 21-day returns for all sector proxies via yfinance and updates ranking.
        Returns sector ranks dict.
        """
        try:
            import yfinance as yf
            sector_returns = {}

            for sector, proxies in SECTOR_PROXIES.items():
                # Use first available proxy
                for proxy in proxies:
                    try:
                        data = yf.Ticker(proxy).history(period="25d")
                        if len(data) >= 15:
                            ret = (data["Close"].iloc[-1] - data["Close"].iloc[-LOOKBACK_PRIMARY]) / \
                                  data["Close"].iloc[-LOOKBACK_PRIMARY] * 100
                            sector_returns[sector] = round(ret, 3)
                            break
                    except Exception:
                        continue

            if sector_returns:
                return self.update_sector_returns(sector_returns)
            else:
                logger.warning("SectorMomentumEngine: no sector data fetched")
                return {}

        except ImportError:
            logger.warning("SectorMomentumEngine: yfinance not available")
            return {}

    # ─────────────────────────────────────────────────────────
    # Signal queries
    # ─────────────────────────────────────────────────────────

    def get_sector_rank(self, ticker: str) -> Optional[int]:
        """Returns 1-5 rank for the sector containing this ticker. None if unknown."""
        sector = TICKER_SECTORS.get(ticker)
        if not sector:
            return None
        return self.state["sector_ranks"].get(sector)

    def get_sector(self, ticker: str) -> Optional[str]:
        """Returns sector name for ticker."""
        return TICKER_SECTORS.get(ticker)

    def get_confidence_adjustment(self, ticker: str) -> int:
        """
        Moskowitz & Grinblatt (1999):
        - Top-quintile sector (rank 1-2): +5 to +8 confidence
        - Bottom-quintile sector (rank 4-5): -8 confidence (fighting sector momentum)
        """
        rank = self.get_sector_rank(ticker)
        if rank is None:
            return 0

        if rank <= 2:
            return SECTOR_TAILWIND_BOOST if rank == 2 else DUAL_MOMENTUM_BOOST
        if rank >= 4:
            return SECTOR_HEADWIND_PENALTY
        return 0

    def get_size_multiplier(self, ticker: str) -> float:
        """Returns position size multiplier based on sector rank."""
        rank = self.get_sector_rank(ticker)
        if rank is None:
            return 1.0
        return SECTOR_RANK_MULTIPLIERS.get(rank, 1.0)

    def is_sector_tailwind(self, ticker: str) -> bool:
        """True if ticker's sector is in the top two ranked sectors."""
        rank = self.get_sector_rank(ticker)
        return rank is not None and rank <= 2

    def is_sector_headwind(self, ticker: str) -> bool:
        """True if ticker's sector is in the bottom two ranked sectors."""
        rank = self.get_sector_rank(ticker)
        return rank is not None and rank >= 4

    # ─────────────────────────────────────────────────────────
    # Telegram summary
    # ─────────────────────────────────────────────────────────

    def get_telegram_summary(self) -> str:
        ranks = self.state.get("sector_ranks", {})
        returns = self.state.get("sector_returns", {})
        if not ranks:
            return "📊 Sector Momentum: not yet computed (run Sunday cycle)"

        sorted_sectors = sorted(ranks.items(), key=lambda x: x[1])
        lines = ["📊 Sector Momentum Ranking (21-day, Moskowitz & Grinblatt 1999):"]
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣"]
        for sector, rank in sorted_sectors:
            ret = returns.get(sector, 0)
            medal = medals[rank - 1] if rank - 1 < len(medals) else "  "
            adj = self.get_confidence_adjustment(sector)
            adj_str = f" ({adj:+d} conf)" if adj != 0 else ""
            lines.append(f"  {medal} #{rank} {sector}: {ret:+.2f}%{adj_str}")

        last = self.state.get("last_update", "")
        if last:
            lines.append(f"\n  Updated: {last[:16]} UTC")
        return "\n".join(lines)
