"""Multi-Source Data Aggregator — unified pipeline for all configured APIs.

Aggregates data from ALL available market data APIs into a single
enrichment layer consumed by bridge.py, nightly pipeline, and regime detection.

Configured APIs (from .env):
  - TwelveData (TWELVEDATA_API_KEY)
  - FMP / Financial Modeling Prep (FMP_KEY)
  - Finnhub (FINNHUB_API_KEY)
  - NewsAPI (NEWSAPI_API_KEY)
  - Polygon.io (POLYGON_KEY)
  - Alpha Vantage (ALPHA_VANTAGE_KEY)
  - FRED (FRED_API_KEY) — handled by fred_provider.py
  - Benzinga (BENZINGA_API_KEY)
  - EODHD (EODHD_API_KEY)

Design: graceful degradation — each source fails independently.
Data written to config/enrichment_data.json for Rust/Python consumption.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger("multi_source_aggregator")

# API timeout for all HTTP calls
_TIMEOUT = 10  # seconds


def _get_key(env_var: str) -> Optional[str]:
    """Get API key from environment, return None if not set."""
    key = os.environ.get(env_var, "")
    return key if key else None


# ── TwelveData ──

def fetch_twelvedata_quote(symbols: List[str]) -> Dict[str, Any]:
    """Fetch real-time quotes from TwelveData."""
    key = _get_key("TWELVEDATA_API_KEY")
    if not key:
        return {}

    results = {}
    try:
        # TwelveData supports batch quotes
        sym_str = ",".join(symbols[:50])  # Max 50 per request
        resp = requests.get(
            "https://api.twelvedata.com/quote",
            params={"symbol": sym_str, "apikey": key},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            # Handle rate limit response inside 200
            if isinstance(data, dict) and data.get("code") == 429:
                log.warning("TwelveData: rate limited (429)")
            # Handle single vs batch response
            elif isinstance(data, dict) and "symbol" in data:
                data = {data["symbol"]: data}
                for sym, quote in data.items():
                    if isinstance(quote, dict) and "close" in quote:
                        results[sym] = {
                            "price": float(quote.get("close", 0)),
                            "change_pct": float(quote.get("percent_change", 0)),
                            "volume": int(quote.get("volume", 0)),
                            "source": "twelvedata",
                        }
            elif isinstance(data, dict):
                for sym, quote in data.items():
                    if isinstance(quote, dict) and "close" in quote:
                        results[sym] = {
                            "price": float(quote.get("close", 0)),
                            "change_pct": float(quote.get("percent_change", 0)),
                            "volume": int(quote.get("volume", 0)),
                            "source": "twelvedata",
                        }
        elif resp.status_code == 429:
            log.warning("TwelveData: rate limited (HTTP 429)")
        log.info("TwelveData: %d quotes fetched", len(results))
    except Exception as e:
        log.warning("TwelveData failed: %s", str(e)[:100])

    return results


def fetch_twelvedata_technical(symbol: str, indicator: str = "rsi", interval: str = "1day") -> Optional[Dict]:
    """Fetch technical indicator from TwelveData."""
    key = _get_key("TWELVEDATA_API_KEY")
    if not key:
        return None

    try:
        resp = requests.get(
            f"https://api.twelvedata.com/{indicator}",
            params={"symbol": symbol, "interval": interval, "apikey": key, "outputsize": 30},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        log.warning("TwelveData technical failed: %s", str(e)[:80])
    return None


# ── FMP (Financial Modeling Prep) ──

def fetch_fmp_financials(symbols: List[str]) -> Dict[str, Any]:
    """Fetch company financials and key metrics from FMP."""
    key = _get_key("FMP_KEY")
    if not key:
        return {}

    results = {}
    for symbol in symbols[:15]:  # Rate limit
        try:
            # Try v3 key-metrics endpoint first, fall back to profile
            resp = requests.get(
                f"https://financialmodelingprep.com/api/v3/key-metrics-ttm/{symbol}",
                params={"apikey": key},
                timeout=_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data and isinstance(data, list):
                    metrics = data[0]
                    results[symbol] = {
                        "pe_ratio": metrics.get("peRatioTTM"),
                        "pb_ratio": metrics.get("pbRatioTTM"),
                        "roe": metrics.get("roeTTM"),
                        "debt_to_equity": metrics.get("debtToEquityTTM"),
                        "dividend_yield": metrics.get("dividendYieldTTM"),
                        "market_cap": metrics.get("marketCapTTM"),
                        "source": "fmp",
                    }
            elif resp.status_code in (403, 401):
                # FMP legacy endpoint deprecated — try profile endpoint
                resp2 = requests.get(
                    f"https://financialmodelingprep.com/api/v3/profile/{symbol}",
                    params={"apikey": key},
                    timeout=_TIMEOUT,
                )
                if resp2.status_code == 200:
                    data2 = resp2.json()
                    if data2 and isinstance(data2, list):
                        p = data2[0]
                        results[symbol] = {
                            "pe_ratio": p.get("pe"),
                            "market_cap": p.get("mktCap"),
                            "beta": p.get("beta"),
                            "price": p.get("price"),
                            "source": "fmp",
                        }
                elif resp2.status_code in (403, 401):
                    log.warning("FMP: API key rejected (both endpoints). Skipping.")
                    break  # Don't waste calls if key is invalid
        except Exception as e:
            log.debug("FMP %s failed: %s", symbol, str(e)[:80])
        time.sleep(0.25)  # Rate limit

    log.info("FMP: %d company financials fetched", len(results))
    return results


def fetch_fmp_earnings_calendar(days_ahead: int = 14) -> List[Dict[str, Any]]:
    """Fetch upcoming earnings dates from FMP."""
    key = _get_key("FMP_KEY")
    if not key:
        return []

    try:
        from_date = date.today().isoformat()
        to_date = (date.today() + timedelta(days=days_ahead)).isoformat()
        resp = requests.get(
            "https://financialmodelingprep.com/api/v3/earning_calendar",
            params={"from": from_date, "to": to_date, "apikey": key},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                log.info("FMP: %d upcoming earnings events", len(data))
                return [
                    {
                        "ticker": e.get("symbol"),
                        "date": e.get("date"),
                        "eps_estimate": e.get("epsEstimated"),
                        "revenue_estimate": e.get("revenueEstimated"),
                        "event_type": "EARNINGS",
                        "source": "fmp",
                    }
                    for e in data if e.get("symbol")
                ]
        elif resp.status_code in (403, 401):
            log.warning("FMP earnings calendar: API rejected (legacy endpoint)")
    except Exception as e:
        log.warning("FMP earnings calendar failed: %s", str(e)[:100])

    return []


# ── Finnhub ──

def fetch_finnhub_news(symbols: List[str], days_back: int = 3) -> Dict[str, List[Dict]]:
    """Fetch company news from Finnhub."""
    key = _get_key("FINNHUB_API_KEY")
    if not key:
        return {}

    results = {}
    from_date = (date.today() - timedelta(days=days_back)).isoformat()
    to_date = date.today().isoformat()

    for symbol in symbols[:20]:
        try:
            resp = requests.get(
                "https://finnhub.io/api/v1/company-news",
                params={"symbol": symbol, "from": from_date, "to": to_date, "token": key},
                timeout=_TIMEOUT,
            )
            if resp.status_code == 200:
                news = resp.json()
                if news:
                    results[symbol] = [
                        {
                            "headline": n.get("headline", ""),
                            "summary": n.get("summary", "")[:200],
                            "datetime": n.get("datetime"),
                            "source": n.get("source", "finnhub"),
                        }
                        for n in news[:5]  # Top 5 per ticker
                    ]
        except Exception:
            pass
        time.sleep(0.1)

    log.info("Finnhub: %d tickers with news", len(results))
    return results


def fetch_finnhub_recommendation(symbol: str) -> Optional[Dict]:
    """Fetch analyst recommendations from Finnhub."""
    key = _get_key("FINNHUB_API_KEY")
    if not key:
        return None

    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/stock/recommendation",
            params={"symbol": symbol, "token": key},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data:
                latest = data[0]
                return {
                    "buy": latest.get("buy", 0),
                    "hold": latest.get("hold", 0),
                    "sell": latest.get("sell", 0),
                    "strong_buy": latest.get("strongBuy", 0),
                    "strong_sell": latest.get("strongSell", 0),
                    "period": latest.get("period"),
                }
    except Exception:
        pass
    return None


# ── NewsAPI ──

def fetch_newsapi_headlines(query: str = "stock market") -> List[Dict[str, Any]]:
    """Fetch top news headlines from NewsAPI."""
    key = _get_key("NEWSAPI_API_KEY")
    if not key:
        return []

    try:
        resp = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q": query,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": 20,
                "apiKey": key,
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            articles = data.get("articles") or []
            log.info("NewsAPI: %d articles for '%s'", len(articles), query)
            return [
                {
                    "title": a.get("title") or "",
                    "description": (a.get("description") or "")[:200],
                    "published_at": a.get("publishedAt"),
                    "source": (a.get("source") or {}).get("name", "newsapi"),
                    "url": a.get("url") or "",
                }
                for a in articles
            ]
    except Exception as e:
        log.warning("NewsAPI failed: %s", str(e)[:100])

    return []


# ── Polygon.io ──

def fetch_polygon_snapshot(symbols: List[str]) -> Dict[str, Any]:
    """Fetch market snapshots from Polygon.io."""
    key = _get_key("POLYGON_KEY")
    if not key:
        return {}

    results = {}
    try:
        # Polygon v3 snapshot endpoint
        resp = requests.get(
            "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers",
            params={"apiKey": key},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            for ticker_data in data.get("tickers", []):
                sym = ticker_data.get("ticker", "")
                if sym in symbols:
                    day = ticker_data.get("day", {})
                    results[sym] = {
                        "price": float(day.get("c", 0)),
                        "open": float(day.get("o", 0)),
                        "high": float(day.get("h", 0)),
                        "low": float(day.get("l", 0)),
                        "volume": int(day.get("v", 0)),
                        "change_pct": float(ticker_data.get("todaysChangePerc", 0)),
                        "source": "polygon",
                    }
        log.info("Polygon: %d snapshots", len(results))
    except Exception as e:
        log.warning("Polygon failed: %s", str(e)[:100])

    return results


# ── Alpha Vantage ──

def fetch_alphavantage_overview(symbol: str) -> Optional[Dict]:
    """Fetch company overview from Alpha Vantage."""
    key = _get_key("ALPHA_VANTAGE_KEY")
    if not key:
        return None

    try:
        resp = requests.get(
            "https://www.alphavantage.co/query",
            params={"function": "OVERVIEW", "symbol": symbol, "apikey": key},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            if "Symbol" in data:
                return {
                    "pe_ratio": float(data.get("PERatio", 0) or 0),
                    "eps": float(data.get("EPS", 0) or 0),
                    "beta": float(data.get("Beta", 0) or 0),
                    "market_cap": data.get("MarketCapitalization"),
                    "52w_high": float(data.get("52WeekHigh", 0) or 0),
                    "52w_low": float(data.get("52WeekLow", 0) or 0),
                    "source": "alphavantage",
                }
    except Exception:
        pass
    return None


# ── Benzinga ──

def fetch_benzinga_news(symbols: List[str]) -> List[Dict[str, Any]]:
    """Fetch news from Benzinga."""
    key = _get_key("BENZINGA_API_KEY")
    if not key:
        return []

    try:
        resp = requests.get(
            "https://api.benzinga.com/api/v2/news",
            params={"token": key, "tickers": ",".join(symbols[:10]), "pageSize": 10},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            return [
                {
                    "title": a.get("title", ""),
                    "body": a.get("body", "")[:200],
                    "created": a.get("created"),
                    "tickers": a.get("stocks", []),
                    "source": "benzinga",
                }
                for a in data
            ]
    except Exception as e:
        log.warning("Benzinga failed: %s", str(e)[:80])
    return []


# ── EODHD ──

def fetch_eodhd_fundamentals(symbol: str) -> Optional[Dict]:
    """Fetch fundamentals from EODHD."""
    key = _get_key("EODHD_API_KEY")
    if not key:
        return None

    try:
        resp = requests.get(
            f"https://eodhd.com/api/fundamentals/{symbol}.US",
            params={"api_token": key, "fmt": "json"},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            highlights = data.get("Highlights", {})
            return {
                "pe_ratio": highlights.get("PERatio"),
                "eps": highlights.get("EarningsShare"),
                "market_cap": highlights.get("MarketCapitalization"),
                "dividend_yield": highlights.get("DividendYield"),
                "source": "eodhd",
            }
    except Exception:
        pass
    return None


# ── Unified Aggregation Pipeline ──

def run_full_enrichment(
    symbols: Optional[List[str]] = None,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the full multi-source enrichment pipeline.

    Aggregates data from ALL configured APIs and writes a unified
    enrichment_data.json consumed by bridge.py and nightly pipeline.

    Args:
        symbols: List of ticker symbols to enrich. Defaults to AEGIS universe.
        output_path: Where to write JSON output.
    """
    if output_path is None:
        output_path = os.environ.get("AEGIS_CONFIG_DIR", "/app/config") + "/enrichment_data.json"

    if symbols is None:
        # Load from contracts.toml — limit to US symbols for API enrichment
        try:
            import tomli as tomllib
        except ImportError:
            try:
                import tomllib
            except ImportError:
                symbols = []
        if symbols is None:
            config_dir = os.environ.get("AEGIS_CONFIG_DIR", "/app/config")
            contracts_path = os.path.join(config_dir, "contracts.toml")
            try:
                with open(contracts_path, "rb") as f:
                    data = tomllib.load(f)
                all_syms = [c["symbol"] for c in data.get("contracts", []) if c.get("symbol")]
                # Use only US equities (no suffix) and limit to 50 for API rate limits
                us_syms = [s for s in all_syms if "." not in s][:50]
                symbols = us_syms if us_syms else all_syms[:50]
            except Exception:
                symbols = []

    log.info("Running multi-source enrichment for %d symbols...", len(symbols))

    result: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources_available": [],
        "sources_failed": [],
        "quotes": {},
        "financials": {},
        "news": {},
        "earnings_calendar": [],
        "market_headlines": [],
        "analyst_recommendations": {},
        "sentiment": {},
    }

    # ── Quotes (TwelveData → Polygon fallback) ──
    quotes = fetch_twelvedata_quote(symbols)
    if quotes:
        result["quotes"].update(quotes)
        result["sources_available"].append("twelvedata")
    else:
        result["sources_failed"].append("twelvedata")

    polygon_quotes = fetch_polygon_snapshot(symbols)
    if polygon_quotes:
        # Merge: Polygon fills gaps not covered by TwelveData
        for sym, data in polygon_quotes.items():
            if sym not in result["quotes"]:
                result["quotes"][sym] = data
        result["sources_available"].append("polygon")
    else:
        result["sources_failed"].append("polygon")

    # ── Financials (FMP primary, Alpha Vantage fallback) ──
    us_symbols = [s for s in symbols if not any(s.endswith(x) for x in [".L", ".T", ".HK", ".SI"])]
    financials = fetch_fmp_financials(us_symbols[:15])
    if financials:
        result["financials"].update(financials)
        result["sources_available"].append("fmp")
    else:
        result["sources_failed"].append("fmp")

    # ── Earnings Calendar (FMP) ──
    earnings = fetch_fmp_earnings_calendar(days_ahead=14)
    if earnings:
        result["earnings_calendar"] = earnings
        log.info("Earnings calendar: %d upcoming events", len(earnings))

    # ── News (Finnhub + NewsAPI + Benzinga) ──
    finnhub_news = fetch_finnhub_news(us_symbols[:10])
    if finnhub_news:
        result["news"].update(finnhub_news)
        result["sources_available"].append("finnhub")
    else:
        result["sources_failed"].append("finnhub")

    headlines = fetch_newsapi_headlines("stock market economy inflation fed")
    if headlines:
        result["market_headlines"] = headlines
        result["sources_available"].append("newsapi")
    else:
        result["sources_failed"].append("newsapi")

    benzinga_news = fetch_benzinga_news(us_symbols[:10])
    if benzinga_news:
        result["news"]["_benzinga_general"] = benzinga_news
        result["sources_available"].append("benzinga")

    # ── Analyst Recommendations (Finnhub) ──
    for sym in us_symbols[:10]:
        rec = fetch_finnhub_recommendation(sym)
        if rec:
            result["analyst_recommendations"][sym] = rec
        time.sleep(0.1)

    # ── Write output ──
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    log.info("Enrichment complete: %d sources OK, %d failed → %s",
             len(result["sources_available"]), len(result["sources_failed"]), output_path)

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [Aggregator] %(levelname)s %(message)s")
    result = run_full_enrichment(symbols=["AAPL", "MSFT", "NVDA", "SPY"])
    print(f"\nSources available: {result['sources_available']}")
    print(f"Sources failed: {result['sources_failed']}")
    print(f"Quotes: {len(result['quotes'])}")
    print(f"Financials: {len(result['financials'])}")
    print(f"Earnings events: {len(result['earnings_calendar'])}")
    print(f"Headlines: {len(result['market_headlines'])}")
