"""
NZT-48 V8.0 — Sector Rotation Radar
======================================
Tracks capital flows across sectors and detects leadership rotation.

Ranks sectors by:
  - Momentum acceleration (rate of change of momentum)
  - Volatility expansion (ATR acceleration)
  - Capital inflow velocity (RVOL surge across sector instruments)
  - Relative strength vs benchmark

Detects early leadership shifts:
  AI/Tech → Energy → Materials → Defense → Other

System automatically adapts when capital rotates between themes.

Output:
  - SectorRanking: real-time sector rankings
  - LeadershipTransition: alert when rotation is detected
  - RotationMap: visual ranking of all sectors

Storage: SQLite for daily snapshots and transition history.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import sys

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.sector_rotation")

_DB_PATH = Path(__file__).parent.parent / "data" / "nzt48.db"

# ─────────────────────────────────────────────────────────────────────────────
# Sector universe: LSE leveraged ETPs grouped by sector
# ─────────────────────────────────────────────────────────────────────────────

SECTOR_MAP: dict[str, list[str]] = {
    "AI_TECH":       ["GPT3.L", "PLTR3.L", "MFAS.L", "MSFL.L", "GOOGL3.L", "AAPLL.L"],
    "SEMICONDUCTORS": ["3SEM.L", "NVD3.L", "TSM3.L", "MU2.L", "AMD3.L", "AVGO3.L", "ARM3.L"],
    "BROAD_US_LONG": ["QQQ3.L", "3LUS.L", "QQQ5.L", "SP5L.L"],
    "BROAD_US_SHORT":["QQQS.L", "3USS.L"],
    "EV_TECH":       ["TSL3.L"],
    "FINANCIALS":    ["BAC3.L", "GS3.L"],
    "ENERGY":        ["3LEN.L", "XOM3.L", "3OIL.L"],
    "HEALTHCARE":    ["3LHC.L", "LLY3.L"],
    "EU_MARKETS":    ["3LDE.L", "3LEU.L"],
    "COMMODITIES":   ["3GOL.L", "3SIL.L"],
    "CRYPTO_TECH":   ["COIN3.L", "MSTRL.L"],
}

# Underlying US sector ETFs for relative strength comparison
SECTOR_ETFS: dict[str, str] = {
    "AI_TECH":        "QQQ",
    "SEMICONDUCTORS": "SOXX",
    "BROAD_US_LONG":  "SPY",
    "EV_TECH":        "XLY",
    "FINANCIALS":     "XLF",
    "ENERGY":         "XLE",
    "HEALTHCARE":     "XLV",
    "EU_MARKETS":     "EWG",
    "COMMODITIES":    "GLD",
    "CRYPTO_TECH":    "MSTR",
}

# Benchmark
BENCHMARK = "^GSPC"  # S&P 500


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SectorRanking:
    sector: str
    rank: int                           # 1 = strongest
    momentum_score: float               # 0–100
    momentum_acceleration: float        # rate of change of momentum
    volatility_expansion_score: float   # ATR expansion vs baseline
    capital_inflow_score: float         # RVOL surge across sector
    relative_strength_vs_benchmark: float  # sector RS vs SPX
    composite_score: float              # weighted final score
    leadership_status: str              # "LEADER", "RISING", "NEUTRAL", "FADING", "LAGGARD"
    instruments: list[str]             # tickers in this sector
    best_instrument: str               # highest composite in sector
    best_instrument_score: float
    trend_direction: str               # "UP", "DOWN", "SIDEWAYS"
    rotation_signal: str               # "INFLOW", "OUTFLOW", "NEUTRAL"


@dataclass
class LeadershipTransition:
    detected_at: str
    old_leader: str
    new_leader: str
    confidence: float                   # 0–1
    signal_type: str                    # "ROTATION", "BROAD_RISK_OFF", "SECTOR_MELT_UP"
    instruments_to_watch: list[str]
    actionable_insight: str


@dataclass
class RotationSnapshot:
    generated_at: str
    rankings: list[SectorRanking]       # sorted by composite score
    current_leader: str
    transition_alert: Optional[LeadershipTransition]
    market_risk_mode: str               # "RISK_ON", "RISK_OFF", "NEUTRAL"
    macro_regime: str                   # "EXPANSION", "CONTRACTION", "TRANSITION"
    key_insight: str


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────

class SectorRotationRadar:
    """
    Real-time sector rotation radar for LSE leveraged ETPs.

    Scores all sectors on momentum, volatility expansion, capital inflows,
    and relative strength. Detects leadership rotation before it's obvious.

    This is the forward-looking engine — it identifies the NEXT leadership
    sector before the crowd does.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or _DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._cache: dict[str, pd.DataFrame] = {}
        self._cache_ts: Optional[datetime] = None

    # ── Database ──────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sector_rankings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_date TEXT NOT NULL,
                    sector TEXT NOT NULL,
                    rank INTEGER,
                    momentum_score REAL,
                    momentum_acceleration REAL,
                    volatility_expansion_score REAL,
                    capital_inflow_score REAL,
                    relative_strength REAL,
                    composite_score REAL,
                    leadership_status TEXT,
                    best_instrument TEXT,
                    trend_direction TEXT,
                    rotation_signal TEXT,
                    UNIQUE(snapshot_date, sector)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS leadership_transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    detected_at TEXT NOT NULL,
                    old_leader TEXT,
                    new_leader TEXT,
                    confidence REAL,
                    signal_type TEXT,
                    instruments TEXT,
                    actionable_insight TEXT
                )
            """)
            conn.commit()

    # ── Data fetching ─────────────────────────────────────────────────────────

    def _fetch(
        self, ticker: str, period: str = "6mo"
    ) -> Optional[pd.DataFrame]:
        if ticker in self._cache:
            return self._cache[ticker]
        try:
            df = yf.download(ticker, period=period, interval="1d",
                             auto_adjust=True, progress=False)
            if df is None or df.empty or len(df) < 20:
                return None
            df.columns = [c.lower() if isinstance(c, str) else c[0].lower()
                          for c in df.columns]
            self._cache[ticker] = df
            return df
        except Exception:
            return None

    def _fetch_sector_composite(
        self, tickers: list[str]
    ) -> Optional[pd.Series]:
        """
        Build equal-weighted composite return series for a sector.
        """
        returns: list[pd.Series] = []
        for t in tickers:
            df = self._fetch(t)
            if df is not None and len(df) >= 20:
                ret = df["close"].pct_change().dropna()
                returns.append(ret)

        if not returns:
            return None

        # Align on common index
        combined = pd.concat(returns, axis=1).dropna(how="all")
        if combined.empty:
            return None
        return combined.mean(axis=1)

    # ── Scoring functions ─────────────────────────────────────────────────────

    def _score_momentum(self, composite: pd.Series) -> tuple[float, float]:
        """
        Momentum score (0-100) and momentum acceleration.
        Uses 1M, 3M, 6M weighted ROC.
        Acceleration = current 1M momentum vs 3M momentum.
        """
        vals = composite.values
        n = len(vals)

        def roc(p: int) -> float:
            if n >= p + 1:
                return float((vals[-1] - vals[-(p+1)]) / vals[-(p+1)] * 100)
            return 0.0

        roc_20 = roc(20)   # 1 month
        roc_60 = roc(60)   # 3 months
        roc_120 = roc(120) # 6 months

        # Weighted: shorter TF matters more for momentum
        raw_momentum = roc_20 * 0.5 + roc_60 * 0.3 + roc_120 * 0.2

        # Normalise to 0-100 (assume ±20% is extreme)
        momentum_score = float(max(0, min(100, (raw_momentum + 20) / 40 * 100)))

        # Acceleration: is short-term momentum improving vs medium-term?
        acceleration = roc_20 - (roc_60 / 3)  # annualised comparison

        return momentum_score, acceleration

    def _score_volatility_expansion(self, composite: pd.Series) -> float:
        """
        Volatility expansion score (0-100).
        Measures ATR acceleration: recent 5d vol vs 20d baseline.
        Higher = more expanding volatility (momentum fuel).
        """
        vals = composite.values
        n = len(vals)
        if n < 21:
            return 50.0

        # Daily ranges as volatility proxy
        daily_range = np.abs(np.diff(vals))
        if len(daily_range) < 20:
            return 50.0

        recent_vol = float(np.std(daily_range[-5:]))
        baseline_vol = float(np.std(daily_range[-20:]))

        if baseline_vol == 0:
            return 50.0

        ratio = recent_vol / baseline_vol
        # Normalise: 1.0 = neutral, 2.0 = double vol = 100 score
        score = float(min(100, (ratio - 0.5) / 1.5 * 100))
        return max(0.0, score)

    def _score_capital_inflow(self, tickers: list[str]) -> float:
        """
        Capital inflow velocity score (0-100).
        Measures RVOL surge across sector instruments.
        High RVOL across multiple sector instruments = capital rotation IN.
        """
        rvol_scores: list[float] = []
        for ticker in tickers:
            df = self._fetch(ticker)
            if df is None or "volume" not in df.columns:
                continue
            vol = df["volume"].values
            if len(vol) < 21:
                continue
            avg_vol = float(np.mean(vol[-20:]))
            if avg_vol > 0:
                rvol = float(vol[-1] / avg_vol)
                rvol_scores.append(rvol)

        if not rvol_scores:
            return 50.0

        avg_rvol = float(np.mean(rvol_scores))
        # RVOL 1.0 = neutral, 2.0+ = strong inflow
        score = float(min(100, (avg_rvol - 0.5) / 1.5 * 100))
        return max(0.0, score)

    def _score_relative_strength(
        self, composite: Optional[pd.Series], benchmark: Optional[pd.DataFrame]
    ) -> float:
        """
        Relative strength vs S&P 500 benchmark (0-100).
        RS > 50 = outperforming benchmark.
        """
        if composite is None or benchmark is None:
            return 50.0

        bench_close = benchmark["close"].values
        comp_vals = composite.values

        periods_to_check = [5, 20, 60]
        rs_votes = 0

        for p in periods_to_check:
            if len(comp_vals) >= p + 1 and len(bench_close) >= p + 1:
                sector_ret = (comp_vals[-1] - comp_vals[-(p+1)]) / comp_vals[-(p+1)] if comp_vals[-(p+1)] > 0 else 0
                bench_ret  = (bench_close[-1] - bench_close[-(p+1)]) / bench_close[-(p+1)] if bench_close[-(p+1)] > 0 else 0
                if sector_ret > bench_ret:
                    rs_votes += 1

        return float(rs_votes / len(periods_to_check) * 100)

    # ── Leadership status classification ─────────────────────────────────────

    def _classify_leadership(
        self, composite_score: float, prev_rank: Optional[int], curr_rank: int
    ) -> str:
        if composite_score >= 70:
            return "LEADER"
        elif composite_score >= 55:
            return "RISING" if (prev_rank is None or curr_rank < prev_rank) else "NEUTRAL"
        elif composite_score >= 40:
            return "NEUTRAL"
        elif composite_score >= 25:
            return "FADING"
        else:
            return "LAGGARD"

    def _classify_rotation_signal(
        self, inflow_score: float, momentum_accel: float
    ) -> str:
        if inflow_score > 60 and momentum_accel > 1:
            return "INFLOW"
        elif inflow_score < 35 or momentum_accel < -2:
            return "OUTFLOW"
        return "NEUTRAL"

    def _best_instrument_in_sector(
        self, tickers: list[str]
    ) -> tuple[str, float]:
        """Find the individual instrument with highest recent momentum."""
        best_ticker = tickers[0] if tickers else "N/A"
        best_score = 0.0

        for ticker in tickers:
            df = self._fetch(ticker)
            if df is None or len(df) < 21:
                continue
            close = df["close"].values
            roc5 = (close[-1] - close[-6]) / close[-6] * 100 if close[-6] > 0 else 0
            roc20 = (close[-1] - close[-21]) / close[-21] * 100 if close[-21] > 0 else 0
            score = roc5 * 0.6 + roc20 * 0.4
            if score > best_score:
                best_score = score
                best_ticker = ticker

        return best_ticker, best_score

    # ── Rotation detection ────────────────────────────────────────────────────

    def _detect_rotation(
        self, current_rankings: list[SectorRanking]
    ) -> Optional[LeadershipTransition]:
        """
        Compare current rankings to yesterday's stored rankings.
        Flag leadership rotation if top sector changed.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        try:
            with sqlite3.connect(self.db_path) as conn:
                yesterday_rows = conn.execute("""
                    SELECT sector, rank, composite_score FROM sector_rankings
                    WHERE snapshot_date < ? ORDER BY snapshot_date DESC LIMIT 20
                """, (today,)).fetchall()
        except Exception:
            return None

        if not yesterday_rows:
            return None

        # Get yesterday's leader
        prev_rankings = {row[0]: row[1] for row in yesterday_rows}
        prev_leader = min(prev_rankings, key=prev_rankings.get) if prev_rankings else None

        # Current leader
        if not current_rankings:
            return None
        curr_leader = current_rankings[0].sector

        if prev_leader and prev_leader != curr_leader:
            # Rotation detected
            new_instruments = current_rankings[0].instruments[:3]
            confidence = (current_rankings[0].composite_score -
                          (current_rankings[1].composite_score if len(current_rankings) > 1 else 0)) / 100

            return LeadershipTransition(
                detected_at=today,
                old_leader=prev_leader,
                new_leader=curr_leader,
                confidence=min(1.0, max(0.0, confidence)),
                signal_type="ROTATION",
                instruments_to_watch=new_instruments,
                actionable_insight=(
                    f"Capital rotating from {prev_leader.replace('_', ' ')} "
                    f"→ {curr_leader.replace('_', ' ')}. "
                    f"Watch {', '.join(new_instruments[:2])} for entries."
                ),
            )

        return None

    # ── Market risk mode ──────────────────────────────────────────────────────

    def _classify_market_regime(self, rankings: list[SectorRanking]) -> tuple[str, str]:
        """
        Determine if market is risk-on, risk-off, or neutral.
        Also classify macro regime.
        """
        if not rankings:
            return "NEUTRAL", "UNKNOWN"

        # Risk-on: Tech/AI/Semis leading
        risk_on_sectors = {"AI_TECH", "SEMICONDUCTORS", "BROAD_US_LONG", "CRYPTO_TECH"}
        risk_off_sectors = {"BROAD_US_SHORT", "COMMODITIES"}

        top3 = {r.sector for r in rankings[:3]}
        risk_on_count = len(top3 & risk_on_sectors)
        risk_off_count = len(top3 & risk_off_sectors)

        if risk_on_count >= 2:
            market_mode = "RISK_ON"
        elif risk_off_count >= 1:
            market_mode = "RISK_OFF"
        else:
            market_mode = "NEUTRAL"

        # Macro regime: expansion if top sectors accelerating
        avg_acceleration = float(np.mean([r.momentum_acceleration for r in rankings[:3]]))
        if avg_acceleration > 2:
            macro = "EXPANSION"
        elif avg_acceleration < -2:
            macro = "CONTRACTION"
        else:
            macro = "TRANSITION"

        return market_mode, macro

    # ── Main analysis ─────────────────────────────────────────────────────────

    def scan(self) -> RotationSnapshot:
        """
        Full sector rotation scan.
        Scores all sectors, detects rotation, returns snapshot.
        """
        # Reset cache for fresh data
        self._cache.clear()

        benchmark_df = self._fetch(BENCHMARK, period="6mo")
        rankings: list[SectorRanking] = []

        for sector, tickers in SECTOR_MAP.items():
            try:
                composite = self._fetch_sector_composite(tickers)
                if composite is None:
                    continue

                mom_score, mom_accel = self._score_momentum(composite)
                vol_exp_score = self._score_volatility_expansion(composite)
                inflow_score = self._score_capital_inflow(tickers)
                rs_score = self._score_relative_strength(composite, benchmark_df)

                # Composite: momentum dominates, rotation speed matters
                composite_score = (
                    mom_score         * 0.35 +
                    mom_accel         * 2.0  +   # acceleration adds bonus
                    vol_exp_score     * 0.20 +
                    inflow_score      * 0.25 +
                    rs_score          * 0.20
                )
                composite_score = float(max(0, min(100, composite_score)))

                # Trend direction
                vals = composite.values
                if len(vals) >= 20:
                    roc20 = (vals[-1] - vals[-21]) / vals[-21] * 100 if vals[-21] > 0 else 0
                    trend = "UP" if roc20 > 2 else ("DOWN" if roc20 < -2 else "SIDEWAYS")
                else:
                    trend = "SIDEWAYS"

                best_ticker, best_score = self._best_instrument_in_sector(tickers)
                rotation_sig = self._classify_rotation_signal(inflow_score, mom_accel)

                rankings.append(SectorRanking(
                    sector=sector,
                    rank=0,  # assigned below
                    momentum_score=round(mom_score, 1),
                    momentum_acceleration=round(mom_accel, 2),
                    volatility_expansion_score=round(vol_exp_score, 1),
                    capital_inflow_score=round(inflow_score, 1),
                    relative_strength_vs_benchmark=round(rs_score, 1),
                    composite_score=round(composite_score, 1),
                    leadership_status="NEUTRAL",  # assigned below
                    instruments=tickers,
                    best_instrument=best_ticker,
                    best_instrument_score=round(best_score, 1),
                    trend_direction=trend,
                    rotation_signal=rotation_sig,
                ))

            except Exception as ex:
                logger.warning("Sector scan failed for %s: %s", sector, ex)

        # Sort and assign ranks
        rankings.sort(key=lambda r: r.composite_score, reverse=True)
        for i, r in enumerate(rankings):
            r.rank = i + 1
            r.leadership_status = self._classify_leadership(r.composite_score, None, r.rank)

        # Detect rotation
        transition = self._detect_rotation(rankings)

        # Market regime
        market_mode, macro = self._classify_market_regime(rankings)

        current_leader = rankings[0].sector if rankings else "UNKNOWN"

        # Key insight
        if transition:
            key_insight = transition.actionable_insight
        elif rankings:
            top = rankings[0]
            key_insight = (
                f"Leading sector: {top.sector.replace('_', ' ')} "
                f"(score {top.composite_score:.0f}, "
                f"accel {top.momentum_acceleration:+.1f}). "
                f"Best instrument: {top.best_instrument}. "
                f"Market mode: {market_mode}."
            )
        else:
            key_insight = "Insufficient data for sector analysis."

        # Save to DB
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._save_rankings(today, rankings)
        if transition:
            self._save_transition(transition)

        return RotationSnapshot(
            generated_at=datetime.now(timezone.utc).isoformat(),
            rankings=rankings,
            current_leader=current_leader,
            transition_alert=transition,
            market_risk_mode=market_mode,
            macro_regime=macro,
            key_insight=key_insight,
        )

    def _save_rankings(self, date: str, rankings: list[SectorRanking]) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                for r in rankings:
                    conn.execute("""
                        INSERT OR REPLACE INTO sector_rankings
                        (snapshot_date, sector, rank, momentum_score,
                         momentum_acceleration, volatility_expansion_score,
                         capital_inflow_score, relative_strength, composite_score,
                         leadership_status, best_instrument, trend_direction,
                         rotation_signal)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        date, r.sector, r.rank, r.momentum_score,
                        r.momentum_acceleration, r.volatility_expansion_score,
                        r.capital_inflow_score, r.relative_strength_vs_benchmark,
                        r.composite_score, r.leadership_status,
                        r.best_instrument, r.trend_direction, r.rotation_signal,
                    ))
                conn.commit()
        except Exception as ex:
            logger.warning("Save rankings failed: %s", ex)

    def _save_transition(self, t: LeadershipTransition) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO leadership_transitions
                    (detected_at, old_leader, new_leader, confidence,
                     signal_type, instruments, actionable_insight)
                    VALUES (?,?,?,?,?,?,?)
                """, (
                    t.detected_at, t.old_leader, t.new_leader,
                    t.confidence, t.signal_type,
                    ",".join(t.instruments_to_watch),
                    t.actionable_insight,
                ))
                conn.commit()
        except Exception as ex:
            logger.warning("Save transition failed: %s", ex)

    def get_historical_rankings(self, days: int = 7) -> pd.DataFrame:
        """Retrieve historical sector rankings for trend analysis."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute("""
                    SELECT snapshot_date, sector, rank, composite_score,
                           leadership_status, momentum_acceleration
                    FROM sector_rankings
                    ORDER BY snapshot_date DESC, rank ASC
                    LIMIT ?
                """, (days * len(SECTOR_MAP),)).fetchall()
            return pd.DataFrame(rows, columns=[
                "date", "sector", "rank", "score", "status", "acceleration"
            ])
        except Exception:
            return pd.DataFrame()

    def get_recent_transitions(self, limit: int = 5) -> list[dict]:
        """Get recent leadership transitions."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute("""
                    SELECT detected_at, old_leader, new_leader, confidence,
                           signal_type, actionable_insight
                    FROM leadership_transitions
                    ORDER BY detected_at DESC
                    LIMIT ?
                """, (limit,)).fetchall()
            return [
                {
                    "date": r[0], "from": r[1], "to": r[2],
                    "confidence": r[3], "type": r[4], "insight": r[5],
                }
                for r in rows
            ]
        except Exception:
            return []


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────────────────────

_RADAR: Optional[SectorRotationRadar] = None


def get_radar() -> SectorRotationRadar:
    global _RADAR
    if _RADAR is None:
        _RADAR = SectorRotationRadar()
    return _RADAR
