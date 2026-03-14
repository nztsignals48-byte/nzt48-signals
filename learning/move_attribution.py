"""
NZT-48 Learning Module 3: Move Attribution Engine
Every time a ticker moves >1.5%, auto-analyse WHY.
Builds ticker personality profiles.
"""
from __future__ import annotations
import json
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.learning.attribution")


class MoveAttribution:
    """Analyses why tickers moved and builds personality profiles."""

    def __init__(self):
        self.move_threshold = 0.015  # 1.5%
        # ticker -> list of {date, move_pct, driver, ...}
        self._history: dict[str, list[dict]] = defaultdict(list)
        # ticker -> {driver: count} personality profile
        self.profiles: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    def check_move(
        self,
        ticker: str,
        current_price: float,
        prev_close: float,
        earnings_today: bool = False,
        news_headlines: list[str] = None,
        sector_etf_move: float = 0.0,
        gex_squeeze: bool = False,
        macro_event: bool = False,
        indicators: dict = None,
    ) -> Optional[dict]:
        """Check if a ticker moved >1.5% and attribute the cause."""
        if prev_close <= 0:
            return None

        move_pct = (current_price - prev_close) / prev_close
        if abs(move_pct) < self.move_threshold:
            return None

        direction = "UP" if move_pct > 0 else "DOWN"

        # Attribution logic — check drivers in priority order
        primary_driver = "UNKNOWN"
        secondary_driver = ""
        explanation = ""

        if earnings_today:
            primary_driver = "EARNINGS"
            explanation = f"Earnings day move {move_pct*100:+.1f}%"
        elif news_headlines:
            primary_driver = "NEWS"
            explanation = news_headlines[0] if news_headlines else ""
        elif abs(sector_etf_move) > 0.01:
            primary_driver = "SECTOR_ROTATION"
            explanation = f"Sector ETF moved {sector_etf_move*100:+.1f}%"
        elif gex_squeeze:
            primary_driver = "GEX_SQUEEZE"
            explanation = "Gamma exposure squeeze detected"
        elif macro_event:
            primary_driver = "MACRO"
            explanation = "FOMC/CPI/NFP event day"
        elif indicators:
            # Check for technical breakout
            price = current_price
            if indicators.get("or_high_5m") and price > indicators["or_high_5m"]:
                primary_driver = "TECHNICAL_BREAKOUT"
                explanation = "Opening range breakout"
            elif indicators.get("bb_upper") and price > indicators["bb_upper"]:
                primary_driver = "TECHNICAL_BREAKOUT"
                explanation = "Bollinger band breakout"
            else:
                primary_driver = "TECHNICAL_BREAKOUT"
                explanation = "Indicator-driven move"

        # Record
        record = {
            "ticker": ticker,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "move_pct": round(move_pct * 100, 2),
            "direction": direction,
            "primary_driver": primary_driver,
            "secondary_driver": secondary_driver,
            "explanation": explanation,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self._history[ticker].append(record)
        self.profiles[ticker][primary_driver] += 1

        logger.info(
            "ATTRIBUTION: %s %s %.1f%% → %s (%s)",
            ticker, direction, abs(move_pct) * 100, primary_driver, explanation,
        )

        return record

    def get_ticker_personality(self, ticker: str) -> dict:
        """Get a ticker's personality profile: % breakdown of move drivers."""
        counts = self.profiles.get(ticker, {})
        total = sum(counts.values())
        if total == 0:
            return {}

        return {
            driver: round(count / total * 100, 1)
            for driver, count in sorted(counts.items(), key=lambda x: x[1], reverse=True)
        }

    def get_confidence_boost(self, ticker: str, signal_driver: str) -> int:
        """Boost confidence if signal aligns with ticker's primary driver."""
        personality = self.get_ticker_personality(ticker)
        if not personality:
            return 0

        primary = max(personality, key=personality.get) if personality else ""
        if signal_driver == primary and personality.get(primary, 0) > 30:
            return 5  # +5 confidence if aligned with primary driver
        return 0

    def get_all_profiles(self) -> dict:
        """Get all ticker personality profiles."""
        return {
            ticker: self.get_ticker_personality(ticker)
            for ticker in self.profiles
        }

    def save_state(self, conn: sqlite3.Connection) -> None:
        """Persist move attributions to the move_attributions table."""
        for ticker, records in self._history.items():
            for rec in records:
                conn.execute(
                    """INSERT OR IGNORE INTO move_attributions
                       (ticker, date, move_pct, direction, primary_driver,
                        secondary_driver, explanation, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (rec["ticker"], rec["date"], rec["move_pct"],
                     rec["direction"], rec["primary_driver"],
                     rec.get("secondary_driver", ""), rec.get("explanation", ""),
                     rec.get("timestamp", datetime.now(timezone.utc).isoformat())),
                )
        conn.commit()
        logger.info("Move attribution state saved to DB")

    def load_state(self, conn: sqlite3.Connection) -> None:
        """Load move attributions from the move_attributions table."""
        rows = conn.execute(
            "SELECT * FROM move_attributions ORDER BY created_at DESC LIMIT 1000"
        ).fetchall()
        for row in rows:
            ticker = row["ticker"]
            record = {
                "ticker": ticker,
                "date": row["date"],
                "move_pct": row["move_pct"],
                "direction": row["direction"],
                "primary_driver": row["primary_driver"],
                "secondary_driver": row["secondary_driver"] or "",
                "explanation": row["explanation"] or "",
                "timestamp": row["created_at"],
            }
            self._history[ticker].append(record)
            self.profiles[ticker][row["primary_driver"]] += 1
        logger.info("Move attribution state loaded: %d records", len(rows))
