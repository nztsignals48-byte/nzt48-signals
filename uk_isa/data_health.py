"""
NZT-48 V8.0 -- Data Health Gate
=================================
Institutional-grade OHLCV data validation for the UK ISA leveraged ETP universe.
Called by all PDF generators BEFORE building tables or charts.

Checks performed per ticker / bar set
--------------------------------------
1.  OHLC_PRESENT   -- open/high/low/close columns exist and have no NaN for
                      the rows being analysed
2.  VOLUME_NONZERO -- volume is present and > 0 for the target session row
3.  RANGE_VS_MOVE  -- if |move%| > 0.01 and range% == 0 -> STALE_BAR anomaly
4.  NAN_INF        -- no np.nan or np.inf in any OHLCV column
5.  OHLC_SANITY    -- high >= max(open, close), low <= min(open, close),
                      high >= low
6.  PRICE_SCALE    -- for .L tickers: if close[-1] > expected_max -> likely
                      priced in pence, return scale factor 0.01
7.  MIN_ROWS       -- at least 2 rows required (need prev_close for move%)
8.  VOLUME_PLAUS   -- reject suspiciously round volumes (exactly 1000, 10000)
                      that indicate placeholder / synthetic data

Result objects
--------------
DataHealthResult  -- per-ticker: status, exceptions, price_scale_factor,
                     rows_checked, corrected_df (price divided if pence)
DataHealthSummary -- batch: status, counts, per-ticker results, generated_at

Usage
-----
    from uk_isa.data_health import DataHealthGate, get_gate
    gate = get_gate()
    summary = gate.batch_validate(["QQQ3.L", "TSM3.L", "NVD3.L"])
    if summary.status != "PASS":
        for exc in summary.exceptions:
            logger.warning("DATA HEALTH: %s", exc)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from uk_isa.isa_universe import UNDERLYING_INDEX

logger = logging.getLogger("nzt48.data_health")

# ---------------------------------------------------------------------------
# Expected price ranges (GBP) for pence-vs-pounds sanity check
# Format: ticker -> (min_gbp, max_gbp)
# Tickers that normally trade outside this range are flagged as scale anomaly
# ---------------------------------------------------------------------------
_EXPECTED_PRICE_RANGE: dict[str, tuple[float, float]] = {
    "QQQ3.L":  (0.5,   200.0),
    "3LUS.L":  (1.0,   200.0),
    "QQQ5.L":  (0.5,   100.0),
    "SP5L.L":  (1.0,   150.0),
    "QQQS.L":  (0.5,    80.0),
    "3USS.L":  (0.5,    60.0),
    "3SEM.L":  (0.5,    80.0),
    "GPT3.L":  (0.5,    80.0),
    "NVD3.L":  (0.5,   100.0),
    "TSL3.L":  (0.1,    80.0),
    "TSM3.L":  (0.5,    60.0),
    "MU2.L":   (0.5,    50.0),
    "AMD3.L":  (0.5,   100.0),
    "ARM3.L":  (0.5,    80.0),
    "NVDS.L":  (0.5,    60.0),
    "TSLS.L":  (0.5,    60.0),
    "3LDE.L":  (1.0,   100.0),
    "3LEU.L":  (1.0,    80.0),
    "3GOL.L":  (1.0,   100.0),
    "3SIL.L":  (0.5,    50.0),
    "3OIL.L":  (0.5,    50.0),
    "3SSM.L":  (0.5,    80.0),
    "3SEN.L":  (0.5,    60.0),
    "3SDE.L":  (0.5,    60.0),
    "3SEU.L":  (0.5,    60.0),
    "3LEN.L":  (0.5,    60.0),
    "3LFI.L":  (0.5,    60.0),
    "3LHC.L":  (0.5,    60.0),
}

# Default range for unknown .L tickers
_DEFAULT_PRICE_RANGE = (0.01, 500.0)

# Status levels
_STATUS_PASS = "PASS"
_STATUS_WARN = "WARN"
_STATUS_FAIL = "FAIL"

# Minimum rows for meaningful analysis
_MIN_ROWS = 2

# Threshold for flagging suspiciously round volume (likely placeholder)
_ROUND_VOL_MULTIPLES = {1_000, 10_000, 100_000, 1_000_000}


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DataHealthResult:
    """Validation result for a single ticker."""
    ticker: str
    status: str                         # "PASS" | "WARN" | "FAIL"
    exceptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rows_checked: int = 0
    price_scale_factor: float = 1.0     # 1.0 = already in GBP, 0.01 = was in pence
    corrected_df: Optional[pd.DataFrame] = None
    close_last: float = 0.0
    volume_last: float = 0.0
    move_pct: float = 0.0
    range_pct: float = 0.0
    rvol: float = 1.0

    def is_valid(self) -> bool:
        """Return True if status is PASS or WARN (data usable)."""
        return self.status in (_STATUS_PASS, _STATUS_WARN)

    def summary_line(self) -> str:
        """One-line summary for PDF display."""
        if self.status == _STATUS_PASS:
            return f"{self.ticker}: PASS ({self.rows_checked} rows)"
        elif self.status == _STATUS_WARN:
            return f"{self.ticker}: WARN -- {'; '.join(self.warnings[:2])}"
        else:
            return f"{self.ticker}: FAIL -- {'; '.join(self.exceptions[:2])}"


@dataclass
class DataHealthSummary:
    """Batch validation result across a universe of tickers."""
    status: str                          # "PASS" | "WARN" | "FAIL"
    total: int = 0
    pass_count: int = 0
    warn_count: int = 0
    fail_count: int = 0
    results: dict[str, DataHealthResult] = field(default_factory=dict)
    exceptions: list[str] = field(default_factory=list)   # all FAIL reasons
    warnings: list[str] = field(default_factory=list)     # all WARN reasons
    generated_at: str = ""

    def badge_text(self) -> str:
        """Short badge string for PDF cover page."""
        if self.status == _STATUS_PASS:
            return f"DATA: PASS  ({self.pass_count}/{self.total} tickers OK)"
        elif self.status == _STATUS_WARN:
            return (f"DATA: WARN  ({self.pass_count} OK, "
                    f"{self.warn_count} WARN, {self.fail_count} FAIL "
                    f"of {self.total})")
        else:
            return (f"DATA: FAIL  ({self.fail_count}/{self.total} tickers "
                    f"failed validation)")

    def exception_lines(self) -> list[str]:
        """Return all exception strings across all failed tickers."""
        lines = []
        for ticker, result in self.results.items():
            if result.exceptions:
                for exc in result.exceptions:
                    lines.append(f"{ticker}: {exc}")
        return lines


# ---------------------------------------------------------------------------
# Main gate class
# ---------------------------------------------------------------------------

class DataHealthGate:
    """
    Validates OHLCV data quality for the UK ISA leveraged ETP universe.

    Designed to be called once per PDF generation run. Results are cached
    on the instance for the duration of the run.

    Thread-safe: stateless per validate() call; batch_validate() is not
    thread-safe due to instance-level cache.
    """

    def __init__(self) -> None:
        self._cache: dict[str, DataHealthResult] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self, ticker: str, df: Optional[pd.DataFrame]) -> DataHealthResult:
        """
        Validate OHLCV data for a single ticker.

        Parameters
        ----------
        ticker : str
            Yahoo Finance ticker symbol.
        df : pd.DataFrame or None
            Raw OHLCV dataframe from yfinance.  Columns must be lowercase
            (open, high, low, close, volume).  None is treated as FAIL.

        Returns
        -------
        DataHealthResult
            Always returns a result object, never raises.
        """
        if df is None or df.empty:
            return DataHealthResult(
                ticker=ticker,
                status=_STATUS_FAIL,
                exceptions=["No data returned from yfinance (None or empty DataFrame)"],
            )

        result = DataHealthResult(ticker=ticker, status=_STATUS_PASS)
        result.rows_checked = len(df)

        # Normalise column names
        df = self._normalise_columns(df)

        # Run all checks -- each appends to exceptions/warnings
        self._check_min_rows(df, result)
        if result.status == _STATUS_FAIL:
            return result  # can't proceed without minimum rows

        self._check_ohlc_present(df, result)
        self._check_nan_inf(df, result)
        self._check_volume(df, result)
        self._check_ohlc_sanity(df, result)
        self._check_range_vs_move(df, result)
        self._check_price_scale(ticker, df, result)

        # Compute convenience fields
        try:
            close = df["close"].values.astype(float)
            high  = df["high"].values.astype(float)
            low   = df["low"].values.astype(float)
            vol   = df["volume"].values.astype(float)
            result.close_last   = float(close[-1]) * result.price_scale_factor
            result.volume_last  = float(vol[-1])
            if len(close) >= 2 and close[-2] > 0:
                result.move_pct  = (close[-1] - close[-2]) / close[-2] * 100
                result.range_pct = (high[-1] - low[-1]) / close[-2] * 100
            avg_vol = float(np.mean(vol[:-1][-20:])) if len(vol) >= 21 else float(np.mean(vol))
            result.rvol = float(vol[-1] / avg_vol) if avg_vol > 0 else 1.0
        except Exception as e:
            result.warnings.append(f"Convenience field computation failed: {e}")

        # Determine final status
        if result.exceptions:
            result.status = _STATUS_FAIL
        elif result.warnings:
            result.status = _STATUS_WARN

        # If price scale was corrected, apply to df
        if result.price_scale_factor != 1.0:
            result.corrected_df = self._apply_price_scale(df, result.price_scale_factor)

        return result

    def batch_validate(
        self,
        tickers: list[str],
        period: str = "5d",
        use_cache: bool = True,
    ) -> DataHealthSummary:
        """
        Validate OHLCV data for a list of tickers.

        Fetches fresh daily bars via yfinance for each ticker.
        Results are cached on this instance -- call with use_cache=False to
        force a re-fetch.

        Parameters
        ----------
        tickers : list[str]
            List of Yahoo Finance ticker symbols.
        period : str
            yfinance period string (default "5d").
        use_cache : bool
            Whether to return cached results for tickers already validated.

        Returns
        -------
        DataHealthSummary
        """
        results: dict[str, DataHealthResult] = {}

        for ticker in tickers:
            if use_cache and ticker in self._cache:
                results[ticker] = self._cache[ticker]
                continue
            try:
                df = yf.download(ticker, period=period, interval="1d",
                                 auto_adjust=True, progress=False)
                if df is None or df.empty:
                    result = DataHealthResult(
                        ticker=ticker,
                        status=_STATUS_FAIL,
                        exceptions=["yfinance returned no data"],
                    )
                else:
                    # Normalise multi-index columns from yfinance
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = [c[0].lower() for c in df.columns]
                    else:
                        df.columns = [c.lower() if isinstance(c, str) else str(c).lower()
                                      for c in df.columns]
                    result = self.validate(ticker, df)
            except Exception as exc:
                logger.warning("DataHealthGate: fetch failed for %s: %s", ticker, exc)
                result = DataHealthResult(
                    ticker=ticker,
                    status=_STATUS_FAIL,
                    exceptions=[f"Fetch/parse exception: {exc}"],
                )
            self._cache[ticker] = result
            results[ticker] = result

        # Build summary
        pass_c  = sum(1 for r in results.values() if r.status == _STATUS_PASS)
        warn_c  = sum(1 for r in results.values() if r.status == _STATUS_WARN)
        fail_c  = sum(1 for r in results.values() if r.status == _STATUS_FAIL)
        total   = len(results)

        if fail_c > total * 0.3:
            summary_status = _STATUS_FAIL
        elif fail_c > 0 or warn_c > 3:
            summary_status = _STATUS_WARN
        else:
            summary_status = _STATUS_PASS

        all_exceptions: list[str] = []
        all_warnings: list[str] = []
        for r in results.values():
            all_exceptions.extend([f"{r.ticker}: {e}" for e in r.exceptions])
            all_warnings.extend([f"{r.ticker}: {w}" for w in r.warnings])

        return DataHealthSummary(
            status=summary_status,
            total=total,
            pass_count=pass_c,
            warn_count=warn_c,
            fail_count=fail_c,
            results=results,
            exceptions=all_exceptions,
            warnings=all_warnings,
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        )

    def clear_cache(self) -> None:
        """Clear the internal result cache."""
        self._cache.clear()

    # ------------------------------------------------------------------
    # Private: individual checks
    # ------------------------------------------------------------------

    def _check_min_rows(self, df: pd.DataFrame, result: DataHealthResult) -> None:
        if len(df) < _MIN_ROWS:
            result.exceptions.append(
                f"INSUFFICIENT_ROWS: got {len(df)}, need >= {_MIN_ROWS} "
                f"(requires prev_close for move% calculation)"
            )
            result.status = _STATUS_FAIL

    def _check_ohlc_present(self, df: pd.DataFrame, result: DataHealthResult) -> None:
        required = ["open", "high", "low", "close"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            result.exceptions.append(
                f"OHLC_MISSING: columns not found: {missing}"
            )
            return
        # Check last two rows for NaN in OHLC
        tail = df[required].tail(2)
        nan_mask = tail.isna()
        if nan_mask.values.any():
            bad_cols = [c for c in required if nan_mask[c].any()]
            result.warnings.append(
                f"OHLC_NAN: NaN in last 2 rows for {bad_cols}"
            )

    def _check_nan_inf(self, df: pd.DataFrame, result: DataHealthResult) -> None:
        for col in ["open", "high", "low", "close", "volume"]:
            if col not in df.columns:
                continue
            vals = df[col].values
            try:
                arr = np.array(vals, dtype=float)
            except (ValueError, TypeError):
                result.exceptions.append(f"TYPE_ERROR: cannot cast {col} to float")
                continue
            nan_count = int(np.sum(np.isnan(arr)))
            inf_count = int(np.sum(np.isinf(arr)))
            if nan_count > 0:
                result.warnings.append(
                    f"NAN_IN_{col.upper()}: {nan_count} NaN values"
                )
            if inf_count > 0:
                result.exceptions.append(
                    f"INF_IN_{col.upper()}: {inf_count} Inf values -- data feed error"
                )

    def _check_volume(self, df: pd.DataFrame, result: DataHealthResult) -> None:
        if "volume" not in df.columns:
            result.warnings.append("VOLUME_MISSING: volume column absent")
            return
        try:
            vol = float(df["volume"].iloc[-1])
        except (ValueError, TypeError):
            result.warnings.append("VOLUME_PARSE_ERROR: cannot read last volume")
            return
        if vol == 0:
            # Phase 11: check if underlying index is liquid (AP can create/redeem)
            underlying = UNDERLYING_INDEX.get(result.ticker)
            if underlying:
                try:
                    idx = yf.download(underlying, period="1d", interval="1d", progress=False)
                    if idx is not None and not idx.empty:
                        # Handle multi-index columns from yfinance
                        if isinstance(idx.columns, pd.MultiIndex):
                            idx.columns = [c[0] for c in idx.columns]
                        idx_vol = float(idx["Volume"].iloc[-1])
                        if idx_vol > 1_000_000:
                            result.warnings.append(
                                f"ZERO_ETP_VOL but {underlying} vol={idx_vol:,.0f} -- liquid via AP"
                            )
                            return  # Don't mark as unhealthy
                except Exception as e:
                    logger.debug(
                        "Underlying index check failed for %s -> %s: %s",
                        result.ticker, underlying, e,
                    )
            result.exceptions.append(
                "ZERO_VOLUME: volume is 0 for last bar -- bar may be incomplete, "
                "stale, or from a non-trading session"
            )
        elif math.isnan(vol):
            result.warnings.append("VOLUME_NAN: last bar volume is NaN")
        elif vol in _ROUND_VOL_MULTIPLES:
            result.warnings.append(
                f"ROUND_VOLUME: volume = {vol:.0f} exactly -- "
                "possible placeholder / synthetic data"
            )

    def _check_ohlc_sanity(self, df: pd.DataFrame, result: DataHealthResult) -> None:
        required = ["open", "high", "low", "close"]
        if not all(c in df.columns for c in required):
            return  # already flagged in _check_ohlc_present
        try:
            o = float(df["open"].iloc[-1])
            h = float(df["high"].iloc[-1])
            l = float(df["low"].iloc[-1])
            c = float(df["close"].iloc[-1])
        except (ValueError, TypeError):
            return

        if any(math.isnan(x) for x in [o, h, l, c]):
            return  # already flagged by NaN check

        # High must be >= close and open
        if h < c - 1e-6:
            result.warnings.append(
                f"OHLC_SANITY: high ({h:.4f}) < close ({c:.4f}) -- "
                "data feed anomaly"
            )
        if h < o - 1e-6:
            result.warnings.append(
                f"OHLC_SANITY: high ({h:.4f}) < open ({o:.4f})"
            )
        # Low must be <= close and open
        if l > c + 1e-6:
            result.warnings.append(
                f"OHLC_SANITY: low ({l:.4f}) > close ({c:.4f})"
            )
        if l > o + 1e-6:
            result.warnings.append(
                f"OHLC_SANITY: low ({l:.4f}) > open ({o:.4f})"
            )
        # High >= Low
        if h < l - 1e-6:
            result.exceptions.append(
                f"OHLC_INVERTED: high ({h:.4f}) < low ({l:.4f}) -- "
                "corrupted bar data"
            )

    def _check_range_vs_move(self, df: pd.DataFrame, result: DataHealthResult) -> None:
        """Flag stale/stuck bars where price moved but high-low range is zero."""
        required = ["high", "low", "close"]
        if not all(c in df.columns for c in required):
            return
        if len(df) < 2:
            return
        try:
            close = df["close"].values.astype(float)
            high  = df["high"].values.astype(float)
            low   = df["low"].values.astype(float)
            prev_c  = close[-2]
            move_pct   = abs((close[-1] - prev_c) / prev_c * 100) if prev_c != 0 else 0
            range_pct  = (high[-1] - low[-1]) / prev_c * 100 if prev_c != 0 else 0
        except (ValueError, TypeError, IndexError):
            return

        if move_pct > 0.05 and range_pct < 0.01:
            result.warnings.append(
                f"STALE_BAR: Move%={move_pct:.2f}% but Range%={range_pct:.4f}% "
                f"-- bar may be synthetic or have missing OHLC"
            )
        elif move_pct > 1.0 and range_pct < move_pct * 0.3:
            result.warnings.append(
                f"NARROW_RANGE: Move%={move_pct:.2f}% but Range% only "
                f"{range_pct:.2f}% ({range_pct/move_pct*100:.0f}% of move) "
                f"-- unusually narrow bar"
            )

    def _check_price_scale(
        self, ticker: str, df: pd.DataFrame, result: DataHealthResult
    ) -> None:
        """Detect if .L ticker data is in pence rather than pounds."""
        if not ticker.endswith(".L"):
            return
        if "close" not in df.columns:
            return
        try:
            close_last = float(df["close"].iloc[-1])
        except (ValueError, TypeError):
            return
        if math.isnan(close_last) or close_last <= 0:
            return

        lo, hi = _EXPECTED_PRICE_RANGE.get(ticker, _DEFAULT_PRICE_RANGE)

        if close_last > hi * 100:
            # Very likely in pence (100x too large even for pence-scale)
            result.exceptions.append(
                f"PRICE_SCALE_EXTREME: close={close_last:.2f} is >100x the "
                f"expected max ({hi:.1f} GBP) for {ticker} -- check data feed"
            )
        elif close_last > hi:
            # Possibly in pence (100x scale)
            result.warnings.append(
                f"PRICE_SCALE_WARN: close={close_last:.2f} exceeds expected "
                f"max ({hi:.1f} GBP) for {ticker} -- may be in pence (GBX)"
            )
            result.price_scale_factor = 0.01  # apply pence -> pounds correction
        elif close_last < lo and close_last > 0:
            result.warnings.append(
                f"PRICE_SCALE_LOW: close={close_last:.4f} is below expected "
                f"min ({lo:.2f} GBP) for {ticker} -- verify data"
            )

    # ------------------------------------------------------------------
    # Private: helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
        """Normalise column names to lowercase, handling MultiIndex."""
        if isinstance(df.columns, pd.MultiIndex):
            df = df.copy()
            df.columns = [c[0].lower() if isinstance(c[0], str) else str(c[0]).lower()
                          for c in df.columns]
        else:
            df = df.copy()
            df.columns = [c.lower() if isinstance(c, str) else str(c).lower()
                          for c in df.columns]
        return df

    @staticmethod
    def _apply_price_scale(df: pd.DataFrame, scale: float) -> pd.DataFrame:
        """Apply a price scale factor to OHLC columns (not volume)."""
        df = df.copy()
        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                df[col] = df[col] * scale
        return df


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_gate_singleton: Optional[DataHealthGate] = None


def get_gate() -> DataHealthGate:
    """Return the module-level DataHealthGate singleton (lazy init)."""
    global _gate_singleton
    if _gate_singleton is None:
        _gate_singleton = DataHealthGate()
    return _gate_singleton


# ---------------------------------------------------------------------------
# Convenience wrapper for one-shot validation
# ---------------------------------------------------------------------------

def validate_universe(
    tickers: list[str],
    period: str = "5d",
) -> DataHealthSummary:
    """
    Quick one-shot validation of a ticker universe.

    Creates a fresh gate (no cache), validates all tickers, returns summary.
    Use this when you want isolated validation without shared state.

    Parameters
    ----------
    tickers : list[str]
    period  : str

    Returns
    -------
    DataHealthSummary
    """
    gate = DataHealthGate()
    return gate.batch_validate(tickers, period=period, use_cache=False)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _cli_main() -> None:
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="NZT-48 DataHealthGate -- validate ISA universe data quality"
    )
    parser.add_argument(
        "tickers", nargs="*",
        default=["QQQ3.L", "TSM3.L", "NVD3.L", "TSL3.L", "3SEM.L"],
        help="Tickers to validate (default: sample set)",
    )
    parser.add_argument("--period", default="5d", help="yfinance period (default 5d)")
    args = parser.parse_args()

    summary = validate_universe(args.tickers, period=args.period)
    print(f"\n{summary.badge_text()}")
    print(f"Generated: {summary.generated_at}\n")
    for ticker, result in summary.results.items():
        print(f"  {result.summary_line()}")
    if summary.exceptions:
        print(f"\nExceptions ({len(summary.exceptions)}):")
        for exc in summary.exceptions:
            print(f"  [FAIL] {exc}")
    if summary.warnings:
        print(f"\nWarnings ({len(summary.warnings)}):")
        for w in summary.warnings[:10]:
            print(f"  [WARN] {w}")
    sys.exit(0 if summary.status == _STATUS_PASS else 1)


if __name__ == "__main__":
    _cli_main()
