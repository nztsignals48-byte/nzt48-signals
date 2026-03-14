"""
NZT-48 Learning Module 9: Parameter Self-Optimisation
Tracks signal outcomes in parameter bands and suggests optimal values.

Tracked parameters:
- ATR stop multiplier (1.2, 1.5, 1.8, 2.0)
- RVOL minimum per ticker
- Confidence floor
- ORB timeframe (5-min vs 15-min)
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

logger = logging.getLogger("nzt48.learning.params")


class ParameterBand:
    """Results for one parameter value band."""
    def __init__(self, param_name: str, band_value: str):
        self.param_name = param_name
        self.band_value = band_value
        self.trades: int = 0
        self.total_r: float = 0.0
        self.wins: int = 0

    @property
    def avg_r(self) -> float:
        return self.total_r / self.trades if self.trades > 0 else 0

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades > 0 else 0


class ParameterOptimizer:
    """Tracks trade outcomes per parameter value and suggests optimal settings."""

    # Parameter bands to track
    ATR_BANDS = ["1.0", "1.2", "1.5", "1.8", "2.0", "2.5"]
    RVOL_BANDS = ["1.0", "1.3", "1.5", "1.8", "2.0", "2.5"]
    CONFIDENCE_BANDS = ["55-60", "60-65", "65-70", "70-75", "75-80", "80-85", "85+"]
    ORB_BANDS = ["5min", "15min"]

    MIN_SAMPLES = 50

    def __init__(self):
        # param_name -> band_value -> ParameterBand
        self.bands: dict[str, dict[str, ParameterBand]] = defaultdict(dict)
        self._suggestions: list[dict] = []

        # Initialize bands
        for band in self.ATR_BANDS:
            self.bands["atr_stop_mult"][band] = ParameterBand("atr_stop_mult", band)
        for band in self.RVOL_BANDS:
            self.bands["rvol_min"][band] = ParameterBand("rvol_min", band)
        for band in self.CONFIDENCE_BANDS:
            self.bands["confidence_floor"][band] = ParameterBand("confidence_floor", band)
        for band in self.ORB_BANDS:
            self.bands["orb_timeframe"][band] = ParameterBand("orb_timeframe", band)

    def record_trade(self, trade_data: dict) -> None:
        """Record a trade with its parameter values.

        trade_data should include:
        - r_multiple: float
        - atr_stop_mult: float (approximate band)
        - rvol_at_entry: float
        - confidence: int
        - orb_timeframe: '5min' or '15min'
        """
        r = trade_data.get("r_multiple", 0)
        was_win = r > 0

        # ATR band
        atr = trade_data.get("atr_stop_mult", 1.5)
        atr_band = self._find_band(atr, [1.0, 1.2, 1.5, 1.8, 2.0, 2.5])
        if atr_band in self.bands["atr_stop_mult"]:
            b = self.bands["atr_stop_mult"][atr_band]
            b.trades += 1
            b.total_r += r
            if was_win: b.wins += 1

        # RVOL band
        rvol = trade_data.get("rvol_at_entry", 1.5)
        rvol_band = self._find_band(rvol, [1.0, 1.3, 1.5, 1.8, 2.0, 2.5])
        if rvol_band in self.bands["rvol_min"]:
            b = self.bands["rvol_min"][rvol_band]
            b.trades += 1
            b.total_r += r
            if was_win: b.wins += 1

        # Confidence band
        conf = trade_data.get("confidence", 70)
        conf_band = self._confidence_band(conf)
        if conf_band in self.bands["confidence_floor"]:
            b = self.bands["confidence_floor"][conf_band]
            b.trades += 1
            b.total_r += r
            if was_win: b.wins += 1

        # ORB timeframe
        orb = trade_data.get("orb_timeframe", "5min")
        if orb in self.bands["orb_timeframe"]:
            b = self.bands["orb_timeframe"][orb]
            b.trades += 1
            b.total_r += r
            if was_win: b.wins += 1

    @staticmethod
    def _find_band(value: float, thresholds: list[float]) -> str:
        """Find the closest band for a value."""
        closest = min(thresholds, key=lambda t: abs(t - value))
        return str(closest)

    @staticmethod
    def _confidence_band(conf: int) -> str:
        if conf >= 85: return "85+"
        if conf >= 80: return "80-85"
        if conf >= 75: return "75-80"
        if conf >= 70: return "70-75"
        if conf >= 65: return "65-70"
        if conf >= 60: return "60-65"
        return "55-60"

    def get_optimal_values(self) -> dict:
        """Get optimal parameter values based on trade data."""
        optimal = {}

        for param_name, bands in self.bands.items():
            best_band = None
            best_avg_r = -float("inf")

            for band_value, band in bands.items():
                if band.trades >= self.MIN_SAMPLES and band.avg_r > best_avg_r:
                    best_avg_r = band.avg_r
                    best_band = band_value

            if best_band is not None:
                optimal[param_name] = {
                    "optimal_value": best_band,
                    "avg_r": round(best_avg_r, 3),
                    "trades": bands[best_band].trades,
                    "win_rate": round(bands[best_band].win_rate * 100, 1),
                }

        return optimal

    def generate_suggestions(self) -> list[dict]:
        """Generate parameter optimization suggestions."""
        suggestions = []
        optimal = self.get_optimal_values()

        for param, data in optimal.items():
            suggestions.append({
                "parameter": param,
                "current": "default",
                "suggested": data["optimal_value"],
                "avg_r": data["avg_r"],
                "trades": data["trades"],
                "win_rate": data["win_rate"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        self._suggestions = suggestions
        return suggestions

    def get_band_report(self) -> dict:
        """Get full band analysis for all parameters."""
        report = {}
        for param_name, bands in self.bands.items():
            report[param_name] = [
                {
                    "band": band_value,
                    "trades": band.trades,
                    "avg_r": round(band.avg_r, 3),
                    "win_rate": round(band.win_rate * 100, 1),
                }
                for band_value, band in bands.items()
                if band.trades > 0
            ]
        return report

    def save_state(self, conn: sqlite3.Connection) -> None:
        """Persist parameter optimizer state to SQLite as a JSON blob."""
        conn.execute(
            """CREATE TABLE IF NOT EXISTS learning_state (
                module TEXT PRIMARY KEY,
                state_json TEXT,
                updated_at TEXT
            )"""
        )
        state = {}
        for param_name, bands in self.bands.items():
            state[param_name] = {
                bv: {"trades": b.trades, "total_r": b.total_r, "wins": b.wins}
                for bv, b in bands.items()
            }
        conn.execute(
            "INSERT OR REPLACE INTO learning_state (module, state_json, updated_at) VALUES (?, ?, ?)",
            ("param_optimizer", json.dumps(state), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        logger.info("Parameter optimizer state saved to DB")

    def load_state(self, conn: sqlite3.Connection) -> None:
        """Load parameter optimizer state from SQLite."""
        try:
            row = conn.execute(
                "SELECT state_json FROM learning_state WHERE module = ?",
                ("param_optimizer",),
            ).fetchone()
        except Exception:
            return
        if not row:
            return
        state = json.loads(row["state_json"] if isinstance(row, sqlite3.Row) else row[0])
        for param_name, bands_data in state.items():
            if param_name not in self.bands:
                continue
            for bv, data in bands_data.items():
                if bv in self.bands[param_name]:
                    b = self.bands[param_name][bv]
                    b.trades = data["trades"]
                    b.total_r = data["total_r"]
                    b.wins = data["wins"]
        logger.info("Parameter optimizer state loaded")
