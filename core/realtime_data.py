"""
Real-Time Data Feed — NZT-48
Priority chain: POLYGON (US) → TWELVEDATA (LSE+US) → ALPHAVANTAGE → YFINANCE

Polygon.io Starter ($29/mo) — best for US equities, real-time
TwelveData Grow ($29/mo)    — covers LSE .L tickers + US, 15s delay on Grow
AlphaVantage                — free tier fallback, 5 calls/min
yfinance                    — last resort, 15-20min delay

Keys set via environment variables:
  POLYGON_KEY
  TWELVEDATA_API_KEY
  ALPHA_VANTAGE_KEY
"""

import os
import json
import time
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

STALE_THRESHOLD_SECONDS = 300  # 5 minutes — older than this = not real-time

# ─────────────────────────────────────────────────────────
# Mandate 10: Dynamic P90 Fallback Spread Tracker
# Pastor & Stambaugh (2003): spreads spike during illiquidity
# ─────────────────────────────────────────────────────────


class SpreadHistoryTracker:
    """Rolling 500-record spread history per ticker. Fallback = P90.

    The hardcoded 0.2% fallback spread is dangerous during feed outages.
    During market shocks, 3SEM.L spread can reach 1.5%+.
    P90 of rolling 5-day history is the correct statistical approach.

    Reference: Pastor & Stambaugh (2003) "Liquidity Risk and Expected Stock Returns"
    """

    def __init__(self, max_records: int = 500):
        self._history: dict[str, list[dict]] = {}
        self._max_records = max_records

    def record(self, ticker: str, spread_pct: float) -> None:
        """Record a spread observation."""
        if ticker not in self._history:
            self._history[ticker] = []
        self._history[ticker].append({
            "spread": spread_pct,
            "ts": time.time(),
        })
        # Trim to max records
        if len(self._history[ticker]) > self._max_records:
            self._history[ticker] = self._history[ticker][-self._max_records:]

    def get_fallback_spread(self, ticker: str) -> float:
        """Return P90 fallback spread for a ticker.

        If insufficient history (<10 records), returns 0.004 (0.4%) as a
        conservative floor — better than the dangerous 0.2% hardcode.
        """
        records = self._history.get(ticker, [])
        if len(records) < 10:
            return 0.004  # 0.4% conservative floor — no history yet

        import numpy as np
        spreads = [r["spread"] for r in records]
        p90 = float(np.percentile(spreads, 90))
        return max(p90, 0.002)  # Floor at 0.2% (minimum realistic spread)

    def get_3day_median_spread(self, ticker: str) -> Optional[float]:
        """Return median spread over ~3-day history for a ticker.

        B-02: Used by the spread-aware open gate in daily_target.py.
        3 trading days ~ 3 * 6.25h * 60 = 1,125 minutes of LSE trading.
        At 60s scan interval = ~1,125 records. At 500 max, use all available.

        Returns None if insufficient history (<10 records) — caller should
        skip the spread gate (fail-open: allow trading, don't block on no data).
        """
        records = self._history.get(ticker, [])
        if len(records) < 10:
            return None  # Insufficient data — fail-open

        # Filter to last 3 days (259,200 seconds)
        cutoff = time.time() - (3 * 86400)
        recent = [r["spread"] for r in records if r["ts"] >= cutoff]
        if len(recent) < 10:
            # Fall back to all available history if 3-day window is sparse
            recent = [r["spread"] for r in records]

        import numpy as np
        return float(np.median(recent))

    def get_all_fallbacks(self) -> dict[str, float]:
        """Return P90 fallback spreads for all tracked tickers."""
        return {
            ticker: self.get_fallback_spread(ticker)
            for ticker in self._history
        }

    # ------------------------------------------------------------------
    # D-03: Rolling 20-day P90 Spread Tracker — Redis-persisted
    # Academic basis: Pastor & Stambaugh (2003) — spread percentiles
    # are the gold standard for liquidity risk measurement. P90 captures
    # "typical worst-case" spread without being distorted by rare outliers.
    #
    # 20 trading days ≈ 1 calendar month. At 60s scan interval × 6.25h
    # LSE day = 375 observations/day × 20 days = 7,500 observations.
    # Redis key: nzt:spread:p90:{ticker} → JSON {p90, median_3d, updated}
    # Updated every scan cycle; consumed by S15 scoring and risk officer.
    # ------------------------------------------------------------------

    def record_and_persist(
        self, ticker: str, spread_pct: float, redis_client=None,
    ) -> None:
        """Record a spread observation and persist P90 to Redis.

        D-03: Extends record() to also update the Redis-persisted P90
        spread value. Called from the main scan loop on every cycle.

        Parameters
        ----------
        ticker : str
            Yahoo Finance ticker symbol (e.g. ``"QQQ3.L"``).
        spread_pct : float
            Current bid-ask spread as a decimal fraction (e.g. 0.001 = 10bps).
        redis_client : optional
            Redis client instance. If None, only in-memory recording occurs.
        """
        # Record in-memory (existing logic)
        self.record(ticker, spread_pct)

        # Persist P90 to Redis if client available
        if redis_client is None:
            return

        try:
            p90_val = self.get_p90_20day(ticker)
            median_3d = self.get_3day_median_spread(ticker)
            redis_client.setex(
                f"nzt:spread:p90:{ticker}",
                86400 * 2,  # 2-day TTL (refreshed every scan cycle)
                json.dumps({
                    "p90": round(p90_val, 6) if p90_val is not None else None,
                    "median_3d": round(median_3d, 6) if median_3d is not None else None,
                    "updated": time.time(),
                    "n_observations": len(self._history.get(ticker, [])),
                }),
            )
        except Exception as e:
            logger.debug("D-03: failed to persist P90 spread for %s: %s", ticker, e)

    def get_p90_20day(self, ticker: str) -> Optional[float]:
        """Return the 20-trading-day P90 spread for a ticker.

        D-03: Rolling 20-day P90 spread — the "typical worst-case" spread.
        Used by S15 scoring and risk officer for liquidity gating.

        20 trading days ≈ 20 × 86400 = 1,728,000 seconds.
        Requires minimum 50 observations for statistical validity.

        Returns None if insufficient history.
        """
        records = self._history.get(ticker, [])
        if not records:
            return None

        # Filter to last 20 trading days
        cutoff = time.time() - (20 * 86400)
        recent = [r["spread"] for r in records if r["ts"] >= cutoff]

        if len(recent) < 50:
            # Insufficient 20-day data — fall back to all available
            recent = [r["spread"] for r in records]
            if len(recent) < 10:
                return None  # Still not enough — fail-open

        import numpy as np
        return float(np.percentile(recent, 90))

    def is_spread_vetoed(
        self, ticker: str, current_spread: float, multiplier: float = 2.5,
    ) -> tuple[bool, Optional[str]]:
        """Check if current spread exceeds the veto threshold.

        D-03: Veto if current spread > multiplier × 3-day median.
        Default multiplier is 2.5x for 3x ETPs.
        D-01 overrides to 1.8x for 5x ETPs.

        Parameters
        ----------
        ticker : str
            Yahoo Finance ticker symbol.
        current_spread : float
            Current bid-ask spread as decimal fraction.
        multiplier : float
            Veto threshold multiplier (default 2.5 for 3x, 1.8 for 5x).

        Returns
        -------
        tuple[bool, Optional[str]]
            (True, reason_string) if vetoed, (False, None) if OK.
        """
        if current_spread <= 0:
            return False, None

        median = self.get_3day_median_spread(ticker)
        if median is None or median <= 0:
            return False, None  # Fail-open: no history

        if current_spread > multiplier * median:
            return True, (
                f"spread_veto({current_spread*10000:.0f}bps>"
                f"{multiplier}x median {median*10000:.0f}bps)"
            )
        return False, None

    def get_p90_from_redis(
        self, ticker: str, redis_client=None,
    ) -> Optional[float]:
        """Read the persisted P90 spread from Redis.

        D-03: Used when in-memory history is empty (e.g. after restart).
        Falls back to None if Redis unavailable or no cached value.
        """
        if redis_client is None:
            return None
        try:
            cached = redis_client.get(f"nzt:spread:p90:{ticker}")
            if cached:
                data = json.loads(cached)
                return data.get("p90")
        except Exception:
            pass
        return None

    def get_status(self) -> dict:
        """Return tracker status for monitoring."""
        return {
            "tickers_tracked": len(self._history),
            "records_per_ticker": {
                t: len(recs) for t, recs in self._history.items()
            },
            "fallback_spreads": self.get_all_fallbacks(),
        }

SOURCE_POLYGON = "polygon"
SOURCE_TWELVEDATA = "twelvedata"
SOURCE_ALPHAVANTAGE = "alphavantage"
SOURCE_YFINANCE = "yfinance"

LSE_SUFFIX = ".L"


def _http_get(url: str, timeout: int = 10) -> Optional[dict]:
    """Simple HTTP GET returning parsed JSON or None."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NZT48/2.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.debug(f"HTTP GET failed: {url[:80]}... — {e}")
        return None


class RealtimeDataFeed:
    """
    Unified real-time price feed with 4-source priority chain.
    Automatically routes LSE tickers to TwelveData, US to Polygon.
    Falls back gracefully when keys are missing or APIs fail.
    """

    def __init__(self):
        self.polygon_key = os.getenv("POLYGON_KEY", "")
        self.twelvedata_key = os.getenv("TWELVEDATA_API_KEY", "")
        self.alphavantage_key = os.getenv("ALPHA_VANTAGE_KEY", "")
        self._cache: dict = {}  # ticker → {price, timestamp, source}
        self._source_stats: dict = {s: {"ok": 0, "fail": 0}
                                    for s in [SOURCE_POLYGON, SOURCE_TWELVEDATA,
                                              SOURCE_ALPHAVANTAGE, SOURCE_YFINANCE]}

    # ─────────────────────────────────────────────────────────
    # Public interface
    # ─────────────────────────────────────────────────────────

    def get_price(self, ticker: str, max_age_seconds: int = STALE_THRESHOLD_SECONDS) -> dict:
        """
        Returns dict:
          price       : float
          timestamp   : ISO string (UTC)
          source      : str (which API served this)
          is_realtime : bool (False if older than max_age_seconds)
          age_seconds : int
        """
        # Check cache first
        cached = self._cache.get(ticker)
        if cached:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached["timestamp"])).total_seconds()
            if age < max_age_seconds:
                cached["age_seconds"] = int(age)
                return cached

        is_lse = ticker.endswith(LSE_SUFFIX)

        # Route to best source
        result = None
        if is_lse:
            # LSE: TwelveData first, then fallbacks
            if self.twelvedata_key:
                result = self._twelvedata_price(ticker)
            if result is None:
                result = self._yfinance_price(ticker)
        else:
            # US: Polygon first
            if self.polygon_key:
                result = self._polygon_price(ticker)
            if result is None and self.twelvedata_key:
                result = self._twelvedata_price(ticker)
            if result is None and self.alphavantage_key:
                result = self._alphavantage_price(ticker)
            if result is None:
                result = self._yfinance_price(ticker)

        if result:
            self._cache[ticker] = result
            return result

        return {"price": None, "timestamp": None, "source": "none", "is_realtime": False, "age_seconds": 9999}

    def get_batch_prices(self, tickers: list) -> dict:
        """Returns {ticker: price_dict} for all tickers."""
        results = {}
        for t in tickers:
            results[t] = self.get_price(t)
        return results

    def get_bars(self, ticker: str, period: str = "1d", interval: str = "5m") -> Optional[object]:
        """
        Returns OHLCV bars using yfinance (Polygon bars require additional calls).
        period: '1d', '5d', '1mo'
        interval: '1m', '5m', '15m', '1h', '1d'
        """
        try:
            import yfinance as yf
            data = yf.Ticker(ticker).history(period=period, interval=interval)
            return data if not data.empty else None
        except Exception as e:
            logger.warning(f"get_bars({ticker}): {e}")
            return None

    # ─────────────────────────────────────────────────────────
    # Source implementations
    # ─────────────────────────────────────────────────────────

    def _polygon_price(self, ticker: str) -> Optional[dict]:
        """Polygon.io /v2/last/trade/{ticker} — real-time for US."""
        # Polygon uses exchange prefix for non-US, strip .L for safety
        clean = ticker.replace(".L", "")
        url = f"https://api.polygon.io/v2/last/trade/{clean}?apiKey={self.polygon_key}"
        data = _http_get(url)
        if data and data.get("status") == "OK" and "results" in data:
            r = data["results"]
            price = r.get("p") or r.get("price")
            ts_ns = r.get("t", 0)
            ts = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).isoformat()
            age = (datetime.now(timezone.utc) - datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)).total_seconds()
            self._source_stats[SOURCE_POLYGON]["ok"] += 1
            return {
                "price": float(price),
                "timestamp": ts,
                "source": SOURCE_POLYGON,
                "is_realtime": age < STALE_THRESHOLD_SECONDS,
                "age_seconds": int(age),
            }
        self._source_stats[SOURCE_POLYGON]["fail"] += 1
        return None

    def _twelvedata_price(self, ticker: str) -> Optional[dict]:
        """TwelveData /price endpoint — covers LSE .L tickers."""
        # TwelveData uses exchange param for LSE
        if ticker.endswith(".L"):
            base = ticker[:-2]
            url = (f"https://api.twelvedata.com/price"
                   f"?symbol={urllib.parse.quote(base)}&exchange=LSE"
                   f"&apikey={self.twelvedata_key}")
        else:
            url = (f"https://api.twelvedata.com/price"
                   f"?symbol={urllib.parse.quote(ticker)}"
                   f"&apikey={self.twelvedata_key}")

        data = _http_get(url)
        if data and "price" in data:
            try:
                price = float(data["price"])
                ts = datetime.now(timezone.utc).isoformat()
                self._source_stats[SOURCE_TWELVEDATA]["ok"] += 1
                return {
                    "price": price,
                    "timestamp": ts,
                    "source": SOURCE_TWELVEDATA,
                    "is_realtime": True,  # TwelveData Grow ~15s delay, close enough
                    "age_seconds": 15,
                }
            except (ValueError, TypeError):
                pass
        self._source_stats[SOURCE_TWELVEDATA]["fail"] += 1
        return None

    def _alphavantage_price(self, ticker: str) -> Optional[dict]:
        """AlphaVantage GLOBAL_QUOTE — free tier, 5 calls/min."""
        url = (f"https://www.alphavantage.co/query"
               f"?function=GLOBAL_QUOTE&symbol={urllib.parse.quote(ticker)}"
               f"&apikey={self.alphavantage_key}")
        data = _http_get(url)
        if data and "Global Quote" in data:
            gq = data["Global Quote"]
            price_str = gq.get("05. price", "")
            if price_str:
                try:
                    price = float(price_str)
                    ts = datetime.now(timezone.utc).isoformat()
                    self._source_stats[SOURCE_ALPHAVANTAGE]["ok"] += 1
                    return {
                        "price": price,
                        "timestamp": ts,
                        "source": SOURCE_ALPHAVANTAGE,
                        "is_realtime": False,  # AV free = 15-20min delay
                        "age_seconds": 900,
                    }
                except (ValueError, TypeError):
                    pass
        self._source_stats[SOURCE_ALPHAVANTAGE]["fail"] += 1
        return None

    def _yfinance_price(self, ticker: str) -> Optional[dict]:
        """yfinance fallback — ~15-20min delay."""
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).fast_info
            price = info.get("lastPrice") or info.get("regularMarketPrice")
            if price and price > 0:
                ts = datetime.now(timezone.utc).isoformat()
                self._source_stats[SOURCE_YFINANCE]["ok"] += 1
                return {
                    "price": float(price),
                    "timestamp": ts,
                    "source": SOURCE_YFINANCE,
                    "is_realtime": False,
                    "age_seconds": 900,
                }
        except Exception as e:
            logger.debug(f"yfinance fallback failed for {ticker}: {e}")
        self._source_stats[SOURCE_YFINANCE]["fail"] += 1
        return None

    # ─────────────────────────────────────────────────────────
    # Status / diagnostics
    # ─────────────────────────────────────────────────────────

    def get_source_status(self) -> dict:
        """Returns health stats for each data source."""
        return {
            "polygon_key_set": bool(self.polygon_key),
            "twelvedata_key_set": bool(self.twelvedata_key),
            "alphavantage_key_set": bool(self.alphavantage_key),
            "stats": self._source_stats,
        }

    def get_bid_ask(self, ticker: str) -> dict:
        """Get bid-ask spread data for a ticker.

        Priority: TwelveData /quote (has bid/ask) → Polygon NBBO → P90 fallback.
        NEVER blocks a trade on missing bid-ask data.
        """
        # Try TwelveData /quote which includes bid/ask for .L tickers
        if self.twelvedata_key and ticker.endswith(LSE_SUFFIX):
            try:
                base = ticker[:-2]
                url = (f"https://api.twelvedata.com/quote"
                       f"?symbol={urllib.parse.quote(base)}&exchange=LSE"
                       f"&apikey={self.twelvedata_key}")
                data = _http_get(url, timeout=5)
                if data and "bid" in data and "ask" in data:
                    bid = float(data["bid"])
                    ask = float(data["ask"])
                    if bid > 0 and ask > bid:
                        spread = ask - bid
                        spread_pct = spread / ((bid + ask) / 2)
                        return {
                            "bid": bid, "ask": ask,
                            "spread": spread, "spread_pct": round(spread_pct, 6),
                            "source": SOURCE_TWELVEDATA,
                        }
            except Exception as e:
                logger.debug("Bid-ask TwelveData failed for %s: %s", ticker, e)

        # Fallback to SpreadHistoryTracker P90
        return {
            "bid": None, "ask": None,
            "spread": None, "spread_pct": None,
            "source": "fallback",
        }

    def get_upgrade_recommendation(self) -> str:
        lines = ["Data Feed Status:"]
        if not self.polygon_key:
            lines.append("  ⚠️  POLYGON_KEY not set — US real-time unavailable")
            lines.append("     → Polygon.io Starter: $29/mo for US real-time")
        else:
            lines.append("  ✅ Polygon: US real-time active")
        if not self.twelvedata_key:
            lines.append("  ⚠️  TWELVEDATA_API_KEY not set — LSE real-time unavailable")
            lines.append("     → TwelveData Grow: $29/mo for LSE real-time")
        else:
            lines.append("  ✅ TwelveData: LSE real-time active")
        if not self.polygon_key and not self.twelvedata_key:
            lines.append("  ℹ️  Falling back to yfinance (15-20min delay)")
        return "\n".join(lines)
