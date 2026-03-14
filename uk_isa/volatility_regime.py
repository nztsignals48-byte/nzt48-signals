"""
NZT-48 V8.0 — Volatility Regime Classifier
===========================================
Classifies each LSE leveraged instrument into one of 5 regimes daily:

  COMPRESSION   — low vol, coiling, breakout imminent
  EXPANSION     — vol expanding, trend accelerating
  BLOW_OFF      — extreme vol spike, climactic move
  EXHAUSTION    — vol decelerating after expansion, possible reversal
  BREAKDOWN     — structural vol collapse / liquidity event

Uses:
  - ATR acceleration (current ATR vs 20-day average ATR)
  - Volatility percentile shift (vs 1Y rolling)
  - Bollinger Band width expansion
  - Cross-asset volatility comparison (vs VIX)

Output: RegimeClassification with 5 probability scores + dominant regime
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional
import sys

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.safe_math import safe_divide

logger = logging.getLogger("nzt48.vol_regime")


class VolRegime(str, Enum):
    COMPRESSION = "COMPRESSION"
    EXPANSION = "EXPANSION"
    BLOW_OFF = "BLOW_OFF"
    EXHAUSTION = "EXHAUSTION"
    BREAKDOWN = "BREAKDOWN"
    UNKNOWN = "UNKNOWN"


@dataclass
class RegimeClassification:
    ticker: str
    regime: VolRegime
    compression_prob: float    # 0-1
    expansion_prob: float
    blow_off_prob: float
    exhaustion_prob: float
    breakdown_prob: float
    atr_current: float
    atr_20d_avg: float
    atr_acceleration: float    # current / 20d avg ratio
    bb_width_current: float
    bb_width_percentile: float  # 0-100
    vol_percentile: float       # current vol rank vs 1Y
    vix_ratio: float            # ticker vol / VIX (normalised)
    computed_at: str = ""

    @property
    def regime_score(self) -> float:
        """Dominant regime probability."""
        scores = {
            VolRegime.COMPRESSION: self.compression_prob,
            VolRegime.EXPANSION: self.expansion_prob,
            VolRegime.BLOW_OFF: self.blow_off_prob,
            VolRegime.EXHAUSTION: self.exhaustion_prob,
            VolRegime.BREAKDOWN: self.breakdown_prob,
        }
        return scores.get(self.regime, 0.0)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "regime": self.regime.value,
            "compression_prob": round(self.compression_prob, 3),
            "expansion_prob": round(self.expansion_prob, 3),
            "blow_off_prob": round(self.blow_off_prob, 3),
            "exhaustion_prob": round(self.exhaustion_prob, 3),
            "breakdown_prob": round(self.breakdown_prob, 3),
            "atr_current": round(self.atr_current, 4),
            "atr_20d_avg": round(self.atr_20d_avg, 4),
            "atr_acceleration": round(self.atr_acceleration, 3),
            "bb_width_current": round(self.bb_width_current, 4),
            "bb_width_percentile": round(self.bb_width_percentile, 1),
            "vol_percentile": round(self.vol_percentile, 1),
            "vix_ratio": round(self.vix_ratio, 3),
            "computed_at": self.computed_at,
        }


class VolatilityRegimeClassifier:
    """
    Classifies each instrument's current volatility regime.

    Usage:
        clf = VolatilityRegimeClassifier(db_path="data/nzt48.db")
        results = clf.classify_all(["QQQ3.L", "NVD3.L", "QQQS.L"])
        rc = clf.get_regime("QQQ3.L")
    """

    _BB_PERIOD = 20
    _BB_STD = 2.0
    _ATR_PERIOD = 14
    _ATR_AVG_PERIOD = 20

    def __init__(self, db_path: str = "data/nzt48.db") -> None:
        self._db_path = db_path
        self._vix_ann_vol: float = 0.20  # fallback
        self._init_db()

    def _init_db(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vol_regimes (
                    ticker TEXT PRIMARY KEY,
                    regime TEXT,
                    compression_prob REAL,
                    expansion_prob REAL,
                    blow_off_prob REAL,
                    exhaustion_prob REAL,
                    breakdown_prob REAL,
                    atr_current REAL,
                    atr_20d_avg REAL,
                    atr_acceleration REAL,
                    bb_width_current REAL,
                    bb_width_percentile REAL,
                    vol_percentile REAL,
                    vix_ratio REAL,
                    computed_at TEXT
                )
            """)
            conn.commit()

    # ── Main classify method ──────────────────────────────────────────────────

    def classify_all(self, tickers: list[str]) -> dict[str, RegimeClassification]:
        """Download data for all tickers and classify each."""
        if not tickers:
            return {}

        # Fetch VIX for cross-asset comparison
        self._fetch_vix()

        # Fetch 1Y daily data
        all_tickers = tickers + ["^VIX"]
        try:
            raw = yf.download(
                all_tickers, period="400d", interval="1d",
                group_by="ticker", auto_adjust=True, progress=False, threads=True,
            )
        except Exception as exc:
            logger.error("Download failed: %s", exc)
            return {}

        results: dict[str, RegimeClassification] = {}
        now = datetime.now(timezone.utc).isoformat()

        for ticker in tickers:
            try:
                if len(all_tickers) == 1:
                    df = raw
                elif ticker in raw.columns.get_level_values(0):
                    df = raw[ticker]
                else:
                    continue

                if df is None or df.empty:
                    continue
                df = df.dropna(subset=["Close"])
                if len(df) < 30:
                    continue

                rc = self._classify_ticker(ticker, df, now)
                if rc:
                    results[ticker] = rc
                    self._persist(rc)

            except Exception as exc:
                logger.debug("Regime error %s: %s", ticker, exc)

        logger.info("VolRegime: classified %d tickers", len(results))
        return results

    def _fetch_vix(self) -> None:
        try:
            vix = yf.download("^VIX", period="30d", interval="1d", progress=False, auto_adjust=True)
            if not vix.empty and "Close" in vix.columns:
                vix_level = float(vix["Close"].iloc[-1])
                # VIX is annualised implied vol in % terms — convert to daily
                self._vix_ann_vol = vix_level / 100.0
        except Exception:
            pass

    def _compute_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df["High"] if "High" in df.columns else df["Close"]
        low = df["Low"] if "Low" in df.columns else df["Close"]
        close = df["Close"]
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        return tr.rolling(period).mean()

    def _classify_ticker(
        self, ticker: str, df: pd.DataFrame, now: str
    ) -> Optional[RegimeClassification]:
        closes = df["Close"].astype(float)
        if len(closes) < 30:
            return None

        # ATR
        atr = self._compute_atr(df, self._ATR_PERIOD)
        atr_current = float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else 0.0
        atr_20d_vals = atr.tail(self._ATR_AVG_PERIOD + 1).iloc[:-1]
        atr_20d_avg = float(atr_20d_vals.mean()) if not atr_20d_vals.empty else atr_current
        atr_acceleration = (atr_current / atr_20d_avg) if atr_20d_avg > 1e-9 else 1.0

        # Bollinger Band width
        sma = closes.rolling(self._BB_PERIOD).mean()
        std = closes.rolling(self._BB_PERIOD).std()
        bb_upper = sma + self._BB_STD * std
        bb_lower = sma - self._BB_STD * std
        bb_width = (bb_upper - bb_lower) / sma.replace(0, np.nan)
        bb_width = bb_width.fillna(0.0)
        bb_width_current = float(bb_width.iloc[-1]) if not pd.isna(bb_width.iloc[-1]) else 0.0

        # BB width percentile vs 1Y
        bb_hist = bb_width.dropna()
        if len(bb_hist) > 0:
            bb_pct = float((bb_hist <= bb_width_current).mean() * 100)
        else:
            bb_pct = 50.0

        # Daily returns volatility
        rets = closes.pct_change().dropna()
        rolling_vol = rets.rolling(20).std()
        current_vol = float(rolling_vol.iloc[-1]) if not pd.isna(rolling_vol.iloc[-1]) else float(rets.std())
        vol_hist = rolling_vol.dropna()
        vol_pct = float((vol_hist <= current_vol).mean() * 100) if not vol_hist.empty else 50.0

        # VIX ratio
        ann_vol = current_vol * np.sqrt(252)
        vix_ratio = (ann_vol / self._vix_ann_vol) if self._vix_ann_vol > 0 else 1.0

        # Previous ATR acceleration (2 bars ago) for exhaustion detection
        atr_prev = float(atr.iloc[-3]) if len(atr) >= 3 and not pd.isna(atr.iloc[-3]) else atr_current
        atr_decel = atr_current < atr_prev * 0.9  # decelerating

        # ── Regime probability scoring ────────────────────────────────────────
        scores = {
            VolRegime.COMPRESSION: 0.0,
            VolRegime.EXPANSION: 0.0,
            VolRegime.BLOW_OFF: 0.0,
            VolRegime.EXHAUSTION: 0.0,
            VolRegime.BREAKDOWN: 0.0,
        }

        # COMPRESSION: low vol, BB width at historical lows, ATR below average
        comp_score = 0.0
        if vol_pct < 25: comp_score += 0.4
        if bb_pct < 20: comp_score += 0.3
        if atr_acceleration < 0.85: comp_score += 0.2
        if atr_acceleration < 0.70: comp_score += 0.1
        scores[VolRegime.COMPRESSION] = min(comp_score, 1.0)

        # EXPANSION: vol rising, ATR accelerating, BB width expanding
        exp_score = 0.0
        if atr_acceleration > 1.2: exp_score += 0.35
        if atr_acceleration > 1.5: exp_score += 0.2
        if bb_pct > 60: exp_score += 0.25
        if vol_pct > 60: exp_score += 0.2
        scores[VolRegime.EXPANSION] = min(exp_score, 1.0)

        # BLOW_OFF: extreme vol, very high percentiles, large ATR
        blow_score = 0.0
        if vol_pct > 90: blow_score += 0.45
        if bb_pct > 90: blow_score += 0.30
        if atr_acceleration > 2.0: blow_score += 0.25
        if vix_ratio > 3.0: blow_score += 0.15
        scores[VolRegime.BLOW_OFF] = min(blow_score, 1.0)

        # EXHAUSTION: vol was high, now decelerating
        exh_score = 0.0
        if vol_pct > 50 and atr_decel: exh_score += 0.5
        if bb_pct > 60 and atr_decel: exh_score += 0.3
        if atr_acceleration < 1.0 and vol_pct > 60: exh_score += 0.2
        scores[VolRegime.EXHAUSTION] = min(exh_score, 1.0)

        # BREAKDOWN: vol collapsing suddenly from high levels
        brkdn_score = 0.0
        if atr_acceleration < 0.5 and vol_pct > 40: brkdn_score += 0.5
        if bb_pct < 15 and vol_pct > 50: brkdn_score += 0.3
        if vix_ratio < 0.3: brkdn_score += 0.2
        scores[VolRegime.BREAKDOWN] = min(brkdn_score, 1.0)

        # Normalise so they sum to 1
        total = sum(scores.values())
        if total > 0:
            for k in scores:
                scores[k] /= total
        else:
            scores[VolRegime.COMPRESSION] = 1.0

        dominant = max(scores, key=lambda k: scores[k])

        return RegimeClassification(
            ticker=ticker,
            regime=dominant,
            compression_prob=scores[VolRegime.COMPRESSION],
            expansion_prob=scores[VolRegime.EXPANSION],
            blow_off_prob=scores[VolRegime.BLOW_OFF],
            exhaustion_prob=scores[VolRegime.EXHAUSTION],
            breakdown_prob=scores[VolRegime.BREAKDOWN],
            atr_current=atr_current,
            atr_20d_avg=atr_20d_avg,
            atr_acceleration=atr_acceleration,
            bb_width_current=bb_width_current,
            bb_width_percentile=bb_pct,
            vol_percentile=vol_pct,
            vix_ratio=vix_ratio,
            computed_at=now,
        )

    def _persist(self, rc: RegimeClassification) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                INSERT INTO vol_regimes (
                    ticker, regime, compression_prob, expansion_prob, blow_off_prob,
                    exhaustion_prob, breakdown_prob, atr_current, atr_20d_avg,
                    atr_acceleration, bb_width_current, bb_width_percentile,
                    vol_percentile, vix_ratio, computed_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(ticker) DO UPDATE SET
                    regime=excluded.regime,
                    compression_prob=excluded.compression_prob,
                    expansion_prob=excluded.expansion_prob,
                    blow_off_prob=excluded.blow_off_prob,
                    exhaustion_prob=excluded.exhaustion_prob,
                    breakdown_prob=excluded.breakdown_prob,
                    atr_current=excluded.atr_current,
                    atr_20d_avg=excluded.atr_20d_avg,
                    atr_acceleration=excluded.atr_acceleration,
                    bb_width_current=excluded.bb_width_current,
                    bb_width_percentile=excluded.bb_width_percentile,
                    vol_percentile=excluded.vol_percentile,
                    vix_ratio=excluded.vix_ratio,
                    computed_at=excluded.computed_at
            """, (
                rc.ticker, rc.regime.value,
                rc.compression_prob, rc.expansion_prob, rc.blow_off_prob,
                rc.exhaustion_prob, rc.breakdown_prob,
                rc.atr_current, rc.atr_20d_avg, rc.atr_acceleration,
                rc.bb_width_current, rc.bb_width_percentile,
                rc.vol_percentile, rc.vix_ratio, rc.computed_at,
            ))
            conn.commit()

    # ── Query ─────────────────────────────────────────────────────────────────

    def get_regime(self, ticker: str) -> Optional[RegimeClassification]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM vol_regimes WHERE ticker=?", (ticker,)).fetchone()
            if not row:
                return None
            return RegimeClassification(
                ticker=row["ticker"],
                regime=VolRegime(row["regime"]) if row["regime"] else VolRegime.UNKNOWN,
                compression_prob=row["compression_prob"] or 0,
                expansion_prob=row["expansion_prob"] or 0,
                blow_off_prob=row["blow_off_prob"] or 0,
                exhaustion_prob=row["exhaustion_prob"] or 0,
                breakdown_prob=row["breakdown_prob"] or 0,
                atr_current=row["atr_current"] or 0,
                atr_20d_avg=row["atr_20d_avg"] or 0,
                atr_acceleration=row["atr_acceleration"] or 1,
                bb_width_current=row["bb_width_current"] or 0,
                bb_width_percentile=row["bb_width_percentile"] or 50,
                vol_percentile=row["vol_percentile"] or 50,
                vix_ratio=row["vix_ratio"] or 1,
                computed_at=row["computed_at"] or "",
            )

    def get_by_regime(self, regime: VolRegime) -> list[str]:
        """Return tickers currently in a given regime."""
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT ticker FROM vol_regimes WHERE regime=?", (regime.value,)
            ).fetchall()
            return [r[0] for r in rows]

    def get_expansion_candidates(self) -> list[str]:
        """Tickers in COMPRESSION or transitioning to EXPANSION — breakout watch."""
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute("""
                SELECT ticker FROM vol_regimes
                WHERE regime IN ('COMPRESSION','EXPANSION')
                  AND compression_prob + expansion_prob > 0.5
                ORDER BY expansion_prob DESC
            """).fetchall()
            return [r[0] for r in rows]
