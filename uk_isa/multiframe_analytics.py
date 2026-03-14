"""
NZT-48 V8.0 — Multi-Timeframe Analytics Engine
===============================================
For every LSE leveraged instrument, computes and stores:

Timeframes: 1 Month, 3 Months, 6 Months, 1 Year

Per timeframe:
  - CAGR (annualised)
  - Annualised volatility
  - Sharpe ratio (risk-free rate = 4.5% UK gilt proxy)
  - Max drawdown
  - Trend slope (linear regression on log prices)
  - Momentum persistence score (% of positive periods)
  - Volatility percentile rank (vs trailing 1Y)

Storage: SQLite table `multiframe_analytics`
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import sys

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.multiframe")

# UK gilt proxy risk-free rate (annualised)
_RISK_FREE_RATE_ANNUAL = 0.045
# Trading days per year
_TRADING_DAYS = 252

_TIMEFRAMES = {
    "1M": 21,
    "3M": 63,
    "6M": 126,
    "1Y": 252,
}


@dataclass
class TimeframeStats:
    ticker: str
    timeframe: str          # "1M", "3M", "6M", "1Y"
    cagr: float             # annualised return
    ann_vol: float          # annualised volatility
    sharpe: float           # Sharpe ratio
    max_drawdown: float     # maximum peak-to-trough (negative)
    trend_slope: float      # log-price linear regression slope (annualised)
    momentum_persistence: float  # % of periods with positive returns (0-1)
    vol_percentile_rank: float   # current vol vs 1Y rolling (0-100)
    start_price: float
    end_price: float
    total_return: float     # raw % return over period
    computed_at: str = ""

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "timeframe": self.timeframe,
            "cagr": round(self.cagr, 4),
            "ann_vol": round(self.ann_vol, 4),
            "sharpe": round(self.sharpe, 3),
            "max_drawdown": round(self.max_drawdown, 4),
            "trend_slope": round(self.trend_slope, 6),
            "momentum_persistence": round(self.momentum_persistence, 3),
            "vol_percentile_rank": round(self.vol_percentile_rank, 1),
            "total_return": round(self.total_return, 4),
            "computed_at": self.computed_at,
        }


class MultiframeAnalytics:
    """
    Computes multi-timeframe analytics for a list of tickers.

    Usage:
        engine = MultiframeAnalytics(db_path="data/nzt48.db")
        results = engine.compute(["QQQ3.L", "NVD3.L", "QQQS.L"])
        stats = engine.get_stats("QQQ3.L", "3M")
    """

    def __init__(self, db_path: str = "data/nzt48.db") -> None:
        self._db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS multiframe_analytics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    cagr REAL,
                    ann_vol REAL,
                    sharpe REAL,
                    max_drawdown REAL,
                    trend_slope REAL,
                    momentum_persistence REAL,
                    vol_percentile_rank REAL,
                    start_price REAL,
                    end_price REAL,
                    total_return REAL,
                    computed_at TEXT,
                    UNIQUE(ticker, timeframe)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mfa_ticker ON multiframe_analytics(ticker)"
            )
            conn.commit()

    # ── Computation ───────────────────────────────────────────────────────────

    def compute(self, tickers: list[str]) -> dict[str, dict[str, TimeframeStats]]:
        """
        Download 400 days of daily data for all tickers, compute all timeframes.
        Returns {ticker: {timeframe: TimeframeStats}}
        """
        if not tickers:
            return {}

        logger.info("MultiframeAnalytics: computing for %d tickers", len(tickers))
        try:
            raw = yf.download(
                tickers, period="400d", interval="1d",
                group_by="ticker", auto_adjust=True, progress=False, threads=True,
            )
        except Exception as exc:
            logger.error("yfinance download failed: %s", exc)
            return {}

        results: dict[str, dict[str, TimeframeStats]] = {}
        now = datetime.now(timezone.utc).isoformat()

        for ticker in tickers:
            try:
                if len(tickers) == 1:
                    df = raw
                elif ticker in raw.columns.get_level_values(0):
                    df = raw[ticker]
                else:
                    continue

                if df is None or df.empty:
                    continue
                closes = df["Close"].dropna()
                if len(closes) < 22:
                    continue

                results[ticker] = {}
                # 1Y data for vol percentile baseline
                full_returns = closes.pct_change().dropna()

                for tf_name, tf_days in _TIMEFRAMES.items():
                    if len(closes) < tf_days:
                        continue
                    tf_closes = closes.tail(tf_days)
                    stats = self._compute_stats(ticker, tf_name, tf_days, tf_closes, full_returns, now)
                    if stats:
                        results[ticker][tf_name] = stats
                        self._persist(stats)

            except Exception as exc:
                logger.debug("Error computing %s: %s", ticker, exc)

        logger.info("MultiframeAnalytics: computed %d tickers", len(results))
        return results

    def _compute_stats(
        self,
        ticker: str,
        tf_name: str,
        tf_days: int,
        closes: pd.Series,
        full_returns: pd.Series,
        now: str,
    ) -> Optional[TimeframeStats]:
        try:
            if len(closes) < 2:
                return None

            start_p = float(closes.iloc[0])
            end_p = float(closes.iloc[-1])
            n_days = len(closes)

            # Total return
            total_ret = (end_p - start_p) / start_p if start_p else 0.0

            # CAGR — annualised
            years = n_days / _TRADING_DAYS
            cagr = (((end_p / start_p) ** (1.0 / years)) - 1.0) if start_p > 0 and years > 0 else 0.0

            # Daily returns
            rets = closes.pct_change().dropna()
            if rets.empty:
                return None

            # Annualised volatility
            ann_vol = float(rets.std() * np.sqrt(_TRADING_DAYS))

            # Sharpe ratio
            ann_excess = cagr - _RISK_FREE_RATE_ANNUAL
            sharpe = (ann_excess / ann_vol) if ann_vol > 1e-9 else 0.0

            # Max drawdown
            cum = (1 + rets).cumprod()
            rolling_max = cum.cummax()
            drawdown = (cum - rolling_max) / rolling_max
            max_dd = float(drawdown.min())

            # Trend slope — linear regression on log prices
            log_prices = np.log(closes.values.astype(float))
            x = np.arange(len(log_prices))
            coeffs = np.polyfit(x, log_prices, 1)
            slope_daily = coeffs[0]
            slope_annual = slope_daily * _TRADING_DAYS  # annualised

            # Momentum persistence — % of daily returns > 0
            persistence = float((rets > 0).mean())

            # Volatility percentile rank — current period vol vs full 1Y rolling
            current_vol = float(rets.std())
            if len(full_returns) >= 20:
                roll_std = full_returns.rolling(20).std().dropna()
                if not roll_std.empty:
                    rank = float((roll_std <= current_vol).mean() * 100)
                else:
                    rank = 50.0
            else:
                rank = 50.0

            return TimeframeStats(
                ticker=ticker,
                timeframe=tf_name,
                cagr=cagr,
                ann_vol=ann_vol,
                sharpe=sharpe,
                max_drawdown=max_dd,
                trend_slope=slope_annual,
                momentum_persistence=persistence,
                vol_percentile_rank=rank,
                start_price=start_p,
                end_price=end_p,
                total_return=total_ret,
                computed_at=now,
            )
        except Exception as exc:
            logger.debug("Stats computation error %s %s: %s", ticker, tf_name, exc)
            return None

    def _persist(self, stats: TimeframeStats) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                INSERT INTO multiframe_analytics (
                    ticker, timeframe, cagr, ann_vol, sharpe, max_drawdown,
                    trend_slope, momentum_persistence, vol_percentile_rank,
                    start_price, end_price, total_return, computed_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(ticker, timeframe) DO UPDATE SET
                    cagr=excluded.cagr,
                    ann_vol=excluded.ann_vol,
                    sharpe=excluded.sharpe,
                    max_drawdown=excluded.max_drawdown,
                    trend_slope=excluded.trend_slope,
                    momentum_persistence=excluded.momentum_persistence,
                    vol_percentile_rank=excluded.vol_percentile_rank,
                    start_price=excluded.start_price,
                    end_price=excluded.end_price,
                    total_return=excluded.total_return,
                    computed_at=excluded.computed_at
            """, (
                stats.ticker, stats.timeframe, stats.cagr, stats.ann_vol,
                stats.sharpe, stats.max_drawdown, stats.trend_slope,
                stats.momentum_persistence, stats.vol_percentile_rank,
                stats.start_price, stats.end_price, stats.total_return,
                stats.computed_at,
            ))
            conn.commit()

    # ── Query ─────────────────────────────────────────────────────────────────

    def get_stats(self, ticker: str, timeframe: str) -> Optional[TimeframeStats]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM multiframe_analytics WHERE ticker=? AND timeframe=?",
                (ticker, timeframe),
            ).fetchone()
            if not row:
                return None
            return TimeframeStats(
                ticker=row["ticker"], timeframe=row["timeframe"],
                cagr=row["cagr"] or 0, ann_vol=row["ann_vol"] or 0,
                sharpe=row["sharpe"] or 0, max_drawdown=row["max_drawdown"] or 0,
                trend_slope=row["trend_slope"] or 0,
                momentum_persistence=row["momentum_persistence"] or 0,
                vol_percentile_rank=row["vol_percentile_rank"] or 50,
                start_price=row["start_price"] or 0, end_price=row["end_price"] or 0,
                total_return=row["total_return"] or 0, computed_at=row["computed_at"] or "",
            )

    def get_all_stats(self, ticker: str) -> dict[str, TimeframeStats]:
        result = {}
        for tf in _TIMEFRAMES:
            s = self.get_stats(ticker, tf)
            if s:
                result[tf] = s
        return result

    def get_top_performers(self, timeframe: str, n: int = 10, bias: Optional[str] = None) -> list[TimeframeStats]:
        """Return top N tickers ranked by CAGR for a given timeframe."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM multiframe_analytics WHERE timeframe=? ORDER BY cagr DESC LIMIT ?",
                (timeframe, n * 3),  # fetch extra to allow filtering
            ).fetchall()
        stats = []
        for row in rows:
            s = TimeframeStats(
                ticker=row["ticker"], timeframe=row["timeframe"],
                cagr=row["cagr"] or 0, ann_vol=row["ann_vol"] or 0,
                sharpe=row["sharpe"] or 0, max_drawdown=row["max_drawdown"] or 0,
                trend_slope=row["trend_slope"] or 0,
                momentum_persistence=row["momentum_persistence"] or 0,
                vol_percentile_rank=row["vol_percentile_rank"] or 50,
                start_price=row["start_price"] or 0, end_price=row["end_price"] or 0,
                total_return=row["total_return"] or 0, computed_at=row["computed_at"] or "",
            )
            stats.append(s)
        return stats[:n]

    def get_high_sharpe(self, timeframe: str = "3M", min_sharpe: float = 1.0) -> list[TimeframeStats]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM multiframe_analytics WHERE timeframe=? AND sharpe >= ? ORDER BY sharpe DESC",
                (timeframe, min_sharpe),
            ).fetchall()
        return [
            TimeframeStats(
                ticker=r["ticker"], timeframe=r["timeframe"],
                cagr=r["cagr"] or 0, ann_vol=r["ann_vol"] or 0,
                sharpe=r["sharpe"] or 0, max_drawdown=r["max_drawdown"] or 0,
                trend_slope=r["trend_slope"] or 0,
                momentum_persistence=r["momentum_persistence"] or 0,
                vol_percentile_rank=r["vol_percentile_rank"] or 50,
                start_price=r["start_price"] or 0, end_price=r["end_price"] or 0,
                total_return=r["total_return"] or 0, computed_at=r["computed_at"] or "",
            ) for r in rows
        ]
