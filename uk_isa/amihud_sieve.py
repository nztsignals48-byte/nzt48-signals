"""
NZT-48 AEGIS Phase I -- Amihud Capacity Sieve (I-01)
=====================================================
Amihud (2002) illiquidity filter with leverage-adjusted market impact
estimation and time-of-day volume correction.

Prevents execution on ETP/tick combinations where estimated market
impact exceeds 50 bps (0.005) of the trade size.

Formula:
    ILLIQ_i = mean(|r_t| / GBPVolume_t) x L^1.5
    where GBPVolume_t = Shares_t x Close_t

Pass condition:
    (heat_size x ILLIQ_i) < 0.005   (< 50 bps market impact)

Time-of-day volume multipliers (UK session):
    09:00 - 10:00  1.6x  (opening auction + first hour)
    10:00 - 12:00  1.0x  (baseline)
    12:00 - 14:00  0.7x  (lunch lull)
    14:30 - 15:30  1.8x  (US open overlap)
    else           1.0x  (default)

Reference: Amihud, Y. (2002). "Illiquidity and stock returns:
    cross-section and time-series effects." Journal of Financial
    Markets, 5(1), 31-56.

Usage:
    from uk_isa.amihud_sieve import AmihudSieve

    sieve = AmihudSieve()
    if sieve.is_liquid("QQQ3.L", heat_size=500.0, leverage_factor=3):
        # safe to trade
        ...
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger("nzt48.amihud_sieve")

# ---------------------------------------------------------------------------
# Time-of-day volume adjustment multipliers (UK session hours, UTC)
# Higher multiplier = more volume available = lower impact
# ---------------------------------------------------------------------------
_TOD_VOLUME_MULTIPLIERS: list[tuple[int, int, float]] = [
    (9,  10, 1.6),   # Opening auction + first hour
    (10, 12, 1.0),   # Baseline mid-morning
    (12, 14, 0.7),   # Lunch lull (lowest liquidity)
    (14, 15, 1.8),   # US pre-open / overlap start
    (15, 16, 1.8),   # US open overlap (14:30-15:30 adjusted to hour boundary)
]
_DEFAULT_TOD_MULTIPLIER: float = 1.0

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
_IMPACT_THRESHOLD: float = 0.005       # 50 bps max market impact
_LOOKBACK_DAYS: int = 20               # trailing window for ILLIQ calc
_MIN_OBSERVATIONS: int = 10            # minimum valid trading days required
_DOWNLOAD_PERIOD: str = "2mo"          # yfinance period (extra buffer for holidays)


def _get_tod_multiplier(hour_utc: Optional[int] = None) -> float:
    """Return the time-of-day volume adjustment multiplier.

    Parameters
    ----------
    hour_utc : int or None
        Current hour in UTC (0-23). If None, uses current wall clock.

    Returns
    -------
    float
        Volume multiplier (>1 = higher liquidity, <1 = lower liquidity).
    """
    if hour_utc is None:
        hour_utc = datetime.now(timezone.utc).hour

    for start_h, end_h, mult in _TOD_VOLUME_MULTIPLIERS:
        if start_h <= hour_utc < end_h:
            return mult
    return _DEFAULT_TOD_MULTIPLIER


class AmihudSieve:
    """Amihud (2002) illiquidity filter with leverage and ToD adjustments.

    Maintains a cache of ILLIQ values per ticker to avoid redundant
    yfinance downloads within the same session.

    Parameters
    ----------
    impact_threshold : float
        Maximum acceptable market impact ratio (default 0.005 = 50 bps).
    lookback_days : int
        Number of trailing trading days for ILLIQ computation (default 20).

    Examples
    --------
    >>> sieve = AmihudSieve()
    >>> sieve.is_liquid("QQQ3.L", heat_size=500.0, leverage_factor=3)
    True
    """

    def __init__(
        self,
        impact_threshold: float = _IMPACT_THRESHOLD,
        lookback_days: int = _LOOKBACK_DAYS,
    ) -> None:
        self._impact_threshold = impact_threshold
        self._lookback_days = lookback_days
        self._cache: dict[str, float] = {}  # ticker -> raw ILLIQ (before leverage)

    def compute_illiq(
        self,
        ticker: str,
        leverage_factor: float = 1.0,
        hour_utc: Optional[int] = None,
    ) -> float:
        """Compute the Amihud ILLIQ ratio for *ticker* with adjustments.

        Formula:
            ILLIQ_raw = mean(|r_t| / GBPVolume_t) for trailing `lookback_days`
            ILLIQ_adj = ILLIQ_raw x L^1.5 / tod_multiplier

        Parameters
        ----------
        ticker : str
            Yahoo Finance ticker symbol (e.g. "QQQ3.L").
        leverage_factor : float
            Unsigned leverage multiplier (e.g. 3 for 3x ETP).
        hour_utc : int or None
            Current hour UTC for time-of-day adjustment. None = wall clock.

        Returns
        -------
        float
            Adjusted ILLIQ ratio. Lower = more liquid. Returns np.inf
            if data is insufficient.
        """
        raw_illiq = self._get_raw_illiq(ticker)
        if np.isinf(raw_illiq):
            return np.inf

        # Leverage adjustment: higher leverage = harder to trade without impact
        lev_adj = abs(leverage_factor) ** 1.5

        # Time-of-day adjustment: higher multiplier = more volume = divide ILLIQ
        tod_mult = _get_tod_multiplier(hour_utc)

        adjusted_illiq = raw_illiq * lev_adj / tod_mult
        return adjusted_illiq

    def is_liquid(
        self,
        ticker: str,
        heat_size: float,
        leverage_factor: float = 1.0,
        hour_utc: Optional[int] = None,
    ) -> bool:
        """Check if a trade of *heat_size* GBP is executable within impact limits.

        PASS condition:
            (heat_size x ILLIQ_adjusted) < impact_threshold

        Parameters
        ----------
        ticker : str
            Yahoo Finance ticker symbol.
        heat_size : float
            Planned trade size in GBP (e.g. 500.0).
        leverage_factor : float
            Unsigned leverage multiplier (e.g. 3 for 3x ETP).
        hour_utc : int or None
            Current hour UTC for time-of-day adjustment.

        Returns
        -------
        bool
            True if estimated market impact is below threshold (liquid enough).
        """
        illiq = self.compute_illiq(ticker, leverage_factor, hour_utc)

        if np.isinf(illiq):
            logger.warning(
                "AMIHUD_SIEVE FAIL: %s — insufficient data, ILLIQ=inf", ticker
            )
            return False

        estimated_impact = heat_size * illiq
        is_pass = estimated_impact < self._impact_threshold

        if not is_pass:
            logger.info(
                "AMIHUD_SIEVE FAIL: %s — impact=%.6f (%.1f bps) > threshold=%.4f "
                "(%.1f bps), heat=%.0f GBP, L=%.1f",
                ticker,
                estimated_impact,
                estimated_impact * 10_000,
                self._impact_threshold,
                self._impact_threshold * 10_000,
                heat_size,
                leverage_factor,
            )
        else:
            logger.debug(
                "AMIHUD_SIEVE PASS: %s — impact=%.6f (%.1f bps), heat=%.0f GBP",
                ticker,
                estimated_impact,
                estimated_impact * 10_000,
                heat_size,
            )

        return is_pass

    def get_impact_bps(
        self,
        ticker: str,
        heat_size: float,
        leverage_factor: float = 1.0,
        hour_utc: Optional[int] = None,
    ) -> float:
        """Return estimated market impact in basis points.

        Convenience method for diagnostics and reporting.

        Parameters
        ----------
        ticker : str
            Yahoo Finance ticker symbol.
        heat_size : float
            Planned trade size in GBP.
        leverage_factor : float
            Unsigned leverage multiplier.
        hour_utc : int or None
            Current hour UTC.

        Returns
        -------
        float
            Estimated impact in bps. Returns float('inf') if data unavailable.
        """
        illiq = self.compute_illiq(ticker, leverage_factor, hour_utc)
        if np.isinf(illiq):
            return float("inf")
        return heat_size * illiq * 10_000

    def clear_cache(self) -> None:
        """Clear the ILLIQ cache. Call after daily data refresh."""
        self._cache.clear()
        logger.debug("AMIHUD_SIEVE: cache cleared")

    def _get_raw_illiq(self, ticker: str) -> float:
        """Fetch or return cached raw ILLIQ for *ticker* (no leverage/ToD adj).

        Raw ILLIQ = mean(|r_t| / GBPVolume_t) over trailing `lookback_days`.

        Parameters
        ----------
        ticker : str
            Yahoo Finance ticker symbol.

        Returns
        -------
        float
            Raw ILLIQ ratio. Returns np.inf if data is insufficient.
        """
        if ticker in self._cache:
            return self._cache[ticker]

        raw_illiq = self._compute_raw_illiq_from_data(ticker)
        self._cache[ticker] = raw_illiq
        return raw_illiq

    def _compute_raw_illiq_from_data(self, ticker: str) -> float:
        """Download price/volume data and compute raw ILLIQ.

        ILLIQ_i = mean(|r_t| / GBPVolume_t) for trailing lookback_days

        GBPVolume_t = Shares_t x Close_t  (NOT raw share count)

        Parameters
        ----------
        ticker : str
            Yahoo Finance ticker symbol.

        Returns
        -------
        float
            Raw ILLIQ. Returns np.inf on failure or insufficient data.
        """
        try:
            data = yf.download(
                ticker,
                period=_DOWNLOAD_PERIOD,
                interval="1d",
                auto_adjust=True,
                progress=False,
            )
        except Exception as exc:
            logger.error("AMIHUD_SIEVE: yfinance download failed for %s: %s", ticker, exc)
            return np.inf

        if data is None or data.empty:
            logger.warning("AMIHUD_SIEVE: no data for %s", ticker)
            return np.inf

        # Flatten MultiIndex columns if present (yfinance quirk)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        if "Close" not in data.columns or "Volume" not in data.columns:
            logger.warning("AMIHUD_SIEVE: missing Close/Volume columns for %s", ticker)
            return np.inf

        # Take trailing lookback window
        df = data.tail(self._lookback_days + 1).copy()
        df = df.dropna(subset=["Close", "Volume"])

        if len(df) < _MIN_OBSERVATIONS + 1:
            logger.warning(
                "AMIHUD_SIEVE: insufficient data for %s — got %d rows, need %d",
                ticker, len(df), _MIN_OBSERVATIONS + 1,
            )
            return np.inf

        # Daily returns (absolute)
        close = df["Close"].values.flatten()
        volume = df["Volume"].values.flatten()

        returns = np.abs(np.diff(close) / close[:-1])
        # GBP Volume = shares traded x close price (aligns with returns)
        gbp_volume = volume[1:] * close[1:]

        # Filter out zero-volume days
        valid = gbp_volume > 0
        if valid.sum() < _MIN_OBSERVATIONS:
            logger.warning(
                "AMIHUD_SIEVE: too many zero-volume days for %s — %d valid of %d",
                ticker, int(valid.sum()), len(gbp_volume),
            )
            return np.inf

        illiq_daily = returns[valid] / gbp_volume[valid]
        raw_illiq = float(np.mean(illiq_daily))

        logger.debug(
            "AMIHUD_SIEVE: %s raw_ILLIQ=%.10f (n=%d days)",
            ticker, raw_illiq, int(valid.sum()),
        )
        return raw_illiq


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_sieve_instance: Optional[AmihudSieve] = None


def get_sieve() -> AmihudSieve:
    """Return the module-level singleton AmihudSieve."""
    global _sieve_instance
    if _sieve_instance is None:
        _sieve_instance = AmihudSieve()
    return _sieve_instance
