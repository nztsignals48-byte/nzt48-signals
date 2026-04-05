"""Fundamental Data Provider — IBKR reqFundamentalData full XML extraction.

Expands AEGIS V2's use of IBKR reqFundamentalData beyond just earnings dates.
IBKR's ReportSnapshot XML contains rich company fundamentals — all FREE with
existing market data subscriptions.

Extracted fields:
    - Market cap
    - P/E ratio (trailing and forward)
    - EPS (trailing and forward estimates)
    - Revenue and revenue growth
    - Dividend yield
    - Book value / P/B ratio
    - Debt/equity ratio
    - ROE / ROA
    - Free cash flow
    - Short interest / days to cover
    - Analyst ratings (buy/hold/sell counts, consensus target price)
    - Insider ownership %
    - Institutional ownership %

Caching:
    Fundamentals change slowly. Results cached for 24h on disk at
    /app/data/fundamental_cache.json. In-memory dict for hot-path lookups.

Usage:
    from python_brain.feeds.fundamental_data_provider import get_fundamentals, refresh_all_fundamentals
    data = get_fundamentals("AAPL")
    # Returns dict with all available fields, or empty dict if unavailable.

Quarantine rules:
    - Read-only: never places orders, never modifies live state
    - Uses IBKRDataProvider singleton (client_id=102)
    - All exceptions caught — never raises to caller
    - Thread-safe via module-level lock
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

log = logging.getLogger("fundamental_data")

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
_DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
_CACHE_FILE = _DATA_DIR / "fundamental_cache.json"
_SIGNALS_FILE = _DATA_DIR / "fundamental_signals.json"
_CACHE_TTL_SECS = 86400  # 24 hours

# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------
_cache: Dict[str, Dict[str, Any]] = {}
_cache_lock = threading.Lock()
_cache_loaded = False


def _load_disk_cache() -> Dict[str, Dict[str, Any]]:
    """Load fundamental cache from disk. Returns empty dict on failure."""
    global _cache, _cache_loaded
    if _cache_loaded:
        return _cache
    with _cache_lock:
        if _cache_loaded:
            return _cache
        if _CACHE_FILE.exists():
            try:
                with open(_CACHE_FILE) as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    _cache = raw
                    log.info("Loaded fundamental cache: %d symbols", len(_cache))
            except Exception as e:
                log.warning("Failed to load fundamental cache: %s", e)
        _cache_loaded = True
    return _cache


def _save_disk_cache(data: Dict[str, Dict[str, Any]]) -> None:
    """Persist fundamental cache to disk."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(_CACHE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.warning("Failed to save fundamental cache: %s", e)


# ---------------------------------------------------------------------------
# XML Parsing — ReportSnapshot full extraction
# ---------------------------------------------------------------------------

def _safe_float(text: Optional[str]) -> Optional[float]:
    """Parse string to float, returning None on failure."""
    if text is None:
        return None
    try:
        val = float(text.strip())
        if val != val:  # NaN check
            return None
        return val
    except (ValueError, TypeError):
        return None


def _safe_int(text: Optional[str]) -> Optional[int]:
    """Parse string to int, returning None on failure."""
    if text is None:
        return None
    try:
        return int(float(text.strip()))
    except (ValueError, TypeError):
        return None


def _parse_report_snapshot(xml_data: str, symbol: str) -> Dict[str, Any]:
    """Parse IBKR ReportSnapshot XML into a comprehensive fundamental dict.

    The ReportSnapshot XML structure (Thomson Reuters via IBKR) contains:
    - <CoGeneralInfo> — company name, sector, industry
    - <Ratio FieldName="XXX"> — financial ratios (P/E, P/B, ROE, etc.)
    - <ForecastData> — analyst estimates (EPS, revenue, target price)
    - <SharesOut> — shares outstanding, short interest
    - <Ownership> — insider/institutional ownership percentages

    All fields are optional — we extract what's available and skip missing data.
    """
    result: Dict[str, Any] = {
        "symbol": symbol,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as e:
        log.debug("XML parse failed for %s: %s", symbol, e)
        return result

    # ── Company General Info ──
    for info in root.iter("CoGeneralInfo"):
        result["company_name"] = info.get("CompanyName", "")
        result["sector"] = info.get("Sector", "")
        result["industry"] = info.get("IndustryGroup", "")
        break  # Only need first

    # Some XML variants use <TextInfo> or <CompanyInfo>
    for info in root.iter("CompanyInfo"):
        if not result.get("company_name"):
            result["company_name"] = info.get("CompanyName", info.get("IssuerName", ""))
        break

    # ── Financial Ratios ──
    # IBKR uses various FieldName codes. Map all known ones.
    _RATIO_MAP = {
        # Market cap and valuation
        "MKTCAP": "market_cap_m",           # Market cap in millions
        "APENORM": "pe_trailing",            # P/E trailing (normalized)
        "APEEXCLXOR": "pe_trailing_excl",    # P/E trailing excluding extraordinary items
        "AREVPS": "revenue_per_share",       # Revenue per share
        "APRICE2BK": "pb_ratio",             # Price to Book
        "APRICE2TANBK": "ptb_tangible",      # Price to Tangible Book
        "APRICE2REV": "ps_ratio",            # Price to Sales (Revenue)
        "APRICE2CF": "pcf_ratio",            # Price to Cash Flow
        "TTMPRFCFPS": "fcf_per_share",       # Free Cash Flow per share (TTM)

        # Profitability
        "AROESSION": "roe",                  # Return on Equity
        "ARESSION": "roe",                   # ROE (alternate code)
        "TTMROEPCT": "roe",                  # ROE TTM percent
        "AROESSION": "roe",
        "AROAPCT": "roa",                    # Return on Assets
        "TTMROAPCT": "roa",                  # ROA TTM percent
        "AGROSMGN": "gross_margin",          # Gross margin %
        "TTMGROSMGN": "gross_margin",
        "ANPMGNPCT": "net_margin",           # Net profit margin %
        "TTMNPMGN": "net_margin",

        # Earnings
        "AEPSXCLXOR": "eps_trailing",        # EPS excluding extraordinary items
        "AEPSNORM": "eps_normalized",        # Normalized EPS
        "TTMEPSXCLX": "eps_ttm",             # EPS TTM excl. extraordinary

        # Growth
        "AREVGRPCT": "revenue_growth_pct",   # Revenue growth %
        "AEPSCHNGYR": "eps_growth_pct",      # EPS change year-over-year %
        "REVCHNGYR": "revenue_growth_pct",   # Revenue change YoY
        "EPSCHNGYR": "eps_growth_pct",       # EPS change YoY

        # Dividend
        "ADIVYIELD": "dividend_yield",       # Dividend yield %
        "TTMDIVSHR": "dividend_per_share",   # Dividend per share TTM
        "DIVPAYOUTRATIO": "payout_ratio",    # Dividend payout ratio

        # Leverage / Balance Sheet
        "ADEBTC": "debt_to_capital",         # Debt / Total Capital
        "ATOTD2EQ": "debt_to_equity",        # Total Debt / Equity
        "ALTD2EQ": "lt_debt_to_equity",      # Long-term Debt / Equity
        "ACURRATIO": "current_ratio",        # Current Ratio
        "AQUESSION": "quick_ratio",          # Quick Ratio

        # Per Share
        "ABVPS": "book_value_per_share",     # Book value per share
        "ATANBVPS": "tangible_bv_per_share", # Tangible book value per share
        "AREVPS": "revenue_per_share",       # Revenue per share

        # Cash Flow
        "TTMFCF": "free_cash_flow_m",        # Free cash flow (millions) TTM
        "TTMCFSHR": "cf_per_share",          # Cash flow per share TTM

        # Short Interest
        "SHORTINT": "short_interest",         # Short interest (shares)
        "SHORTINTDT": "short_interest_date",  # Short interest date
        "SHORTRATIO": "days_to_cover",        # Days to cover (short ratio)

        # Enterprise Value
        "EV": "enterprise_value_m",           # Enterprise value (millions)
        "AEBITD": "ebitda_m",                 # EBITDA (millions)
        "AEVEBIDTA": "ev_to_ebitda",         # EV / EBITDA

        # Analyst
        "PTARGET": "target_price",            # Consensus target price
        "NUMREC": "analyst_count",            # Number of analyst recommendations
        "STMAVG": "analyst_consensus_score",  # Consensus recommendation score (1=Buy, 5=Sell)

        # Forward estimates
        "PEEXCLXOR": "pe_forward",           # Forward P/E
    }

    for ratio in root.iter("Ratio"):
        field_name = ratio.get("FieldName", "")
        mapped = _RATIO_MAP.get(field_name)
        if mapped and ratio.text:
            val = _safe_float(ratio.text)
            if val is not None:
                # Don't overwrite already-set values (first match wins)
                if mapped not in result:
                    result[mapped] = val

    # ── Forecast Data (Analyst Estimates) ──
    # Extract forward EPS and revenue estimates
    for forecast in root.iter("ForecastData"):
        for consensus in forecast.iter("ConsEstimate"):
            est_type = consensus.get("type", "")
            period_type = consensus.get("periodType", "")
            if period_type not in ("Annual", "A", "NTM"):
                continue

            mean_elem = consensus.find("Mean")
            high_elem = consensus.find("High")
            low_elem = consensus.find("Low")
            n_est_elem = consensus.find("NumOfEst")

            if est_type in ("EPS", "AEPS"):
                if mean_elem is not None and mean_elem.text:
                    result.setdefault("eps_forward", _safe_float(mean_elem.text))
                if high_elem is not None and high_elem.text:
                    result.setdefault("eps_forward_high", _safe_float(high_elem.text))
                if low_elem is not None and low_elem.text:
                    result.setdefault("eps_forward_low", _safe_float(low_elem.text))
                if n_est_elem is not None and n_est_elem.text:
                    result.setdefault("eps_num_estimates", _safe_int(n_est_elem.text))

            elif est_type in ("Revenue", "SREV"):
                if mean_elem is not None and mean_elem.text:
                    result.setdefault("revenue_forward_m", _safe_float(mean_elem.text))
                if n_est_elem is not None and n_est_elem.text:
                    result.setdefault("revenue_num_estimates", _safe_int(n_est_elem.text))

            elif est_type in ("TargetPrice", "PTARGET"):
                if mean_elem is not None and mean_elem.text:
                    result.setdefault("target_price", _safe_float(mean_elem.text))

    # ── Analyst Recommendations ──
    # Extract buy/hold/sell counts
    for rec in root.iter("Recommendation"):
        buy = rec.find("Buy")
        overweight = rec.find("Overweight")
        hold = rec.find("Hold")
        underweight = rec.find("Underweight")
        sell = rec.find("Sell")

        buy_count = (_safe_int(buy.text) if buy is not None else 0) or 0
        ow_count = (_safe_int(overweight.text) if overweight is not None else 0) or 0
        hold_count = (_safe_int(hold.text) if hold is not None else 0) or 0
        uw_count = (_safe_int(underweight.text) if underweight is not None else 0) or 0
        sell_count = (_safe_int(sell.text) if sell is not None else 0) or 0

        total = buy_count + ow_count + hold_count + uw_count + sell_count
        if total > 0:
            result["analyst_buy"] = buy_count + ow_count
            result["analyst_hold"] = hold_count
            result["analyst_sell"] = sell_count + uw_count
            result["analyst_total"] = total
            result["analyst_buy_pct"] = round((buy_count + ow_count) / total * 100, 1)
            result["analyst_sell_pct"] = round((sell_count + uw_count) / total * 100, 1)
        break  # Only need first recommendation block

    # ── Shares Outstanding & Short Interest ──
    for shares_out in root.iter("SharesOut"):
        val = _safe_float(shares_out.get("TotalFloat", shares_out.text))
        if val is not None and val > 0:
            result.setdefault("shares_outstanding_m", val)
        break

    # ── Ownership Data ──
    for ownership in root.iter("Ownership"):
        insider_pct = ownership.get("InsiderOwnership")
        inst_pct = ownership.get("InstitutionalOwnership")
        if insider_pct:
            result.setdefault("insider_ownership_pct", _safe_float(insider_pct))
        if inst_pct:
            result.setdefault("institutional_ownership_pct", _safe_float(inst_pct))
        break

    # Also look for ownership in Ratio fields (alternate XML structure)
    for ratio in root.iter("Ratio"):
        fn = ratio.get("FieldName", "")
        if fn == "AINSOWN" and ratio.text and "insider_ownership_pct" not in result:
            result["insider_ownership_pct"] = _safe_float(ratio.text)
        elif fn == "AINSTOWN" and ratio.text and "institutional_ownership_pct" not in result:
            result["institutional_ownership_pct"] = _safe_float(ratio.text)

    # ── Compute forward P/E if we have price and forward EPS ──
    if "pe_forward" not in result and result.get("eps_forward"):
        eps_fwd = result["eps_forward"]
        if eps_fwd and eps_fwd > 0:
            # We don't have price here, but the ReportSnapshot often includes it
            for ratio in root.iter("Ratio"):
                if ratio.get("FieldName") == "NPRICE" and ratio.text:
                    price = _safe_float(ratio.text)
                    if price and price > 0:
                        result["pe_forward"] = round(price / eps_fwd, 2)
                    break

    return result


def _parse_financial_summary(xml_data: str, symbol: str) -> Dict[str, Any]:
    """Parse IBKR ReportsFinSummary XML for revenue/EPS history."""
    result: Dict[str, Any] = {"symbol": symbol}
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        return result

    # Extract annual revenue and EPS history
    annual_revenue = []
    annual_eps = []

    for fy in root.iter("FYActual"):
        for period in fy.iter("FYPeriod"):
            period_type = period.get("periodType", "")
            fiscal_year = period.get("fYear", "")
            if period_type != "Annual":
                continue

            for item in period.iter("lineItem"):
                coa_code = item.get("coaCode", "")
                if coa_code == "SREV" and item.text:
                    val = _safe_float(item.text)
                    if val is not None:
                        annual_revenue.append({"year": fiscal_year, "revenue_m": val})
                elif coa_code in ("AEPS", "AEPSNORM") and item.text:
                    val = _safe_float(item.text)
                    if val is not None:
                        annual_eps.append({"year": fiscal_year, "eps": val})

    if len(annual_revenue) >= 2:
        # Compute revenue growth from last two years
        sorted_rev = sorted(annual_revenue, key=lambda x: x["year"], reverse=True)
        if sorted_rev[1]["revenue_m"] > 0:
            growth = ((sorted_rev[0]["revenue_m"] - sorted_rev[1]["revenue_m"])
                      / sorted_rev[1]["revenue_m"] * 100)
            result["revenue_growth_computed_pct"] = round(growth, 1)

    if len(annual_eps) >= 2:
        sorted_eps = sorted(annual_eps, key=lambda x: x["year"], reverse=True)
        if sorted_eps[1]["eps"] != 0:
            growth = ((sorted_eps[0]["eps"] - sorted_eps[1]["eps"])
                      / abs(sorted_eps[1]["eps"]) * 100)
            result["eps_growth_computed_pct"] = round(growth, 1)

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_fundamentals(symbol: str, force_refresh: bool = False) -> Dict[str, Any]:
    """Get fundamental data for a symbol.

    Returns cached data if fresh (< 24h). Fetches from IBKR if stale or missing.
    Thread-safe. Never raises — returns empty dict on failure.

    Args:
        symbol: Ticker in yfinance format (e.g. "AAPL", "QQQ3.L").
        force_refresh: If True, bypass cache and re-fetch from IBKR.

    Returns:
        Dict with fundamental fields. Empty dict if unavailable.
    """
    cache = _load_disk_cache()

    # Check cache freshness
    if not force_refresh and symbol in cache:
        cached = cache[symbol]
        fetched_at = cached.get("fetched_at", "")
        if fetched_at:
            try:
                fetch_time = datetime.fromisoformat(fetched_at)
                age_secs = (datetime.now(timezone.utc) - fetch_time).total_seconds()
                if age_secs < _CACHE_TTL_SECS:
                    return cached
            except (ValueError, TypeError):
                pass  # Invalid timestamp — treat as stale

    # Fetch from IBKR
    result = _fetch_from_ibkr(symbol)
    if result and len(result) > 2:  # More than just symbol + fetched_at
        with _cache_lock:
            _cache[symbol] = result
        return result

    # Return stale cache if IBKR fails
    if symbol in cache:
        log.debug("Returning stale cache for %s (IBKR unavailable)", symbol)
        return cache[symbol]

    return {}


def _fetch_from_ibkr(symbol: str) -> Dict[str, Any]:
    """Fetch fundamental data from IBKR. Returns dict or empty dict on failure."""
    try:
        from python_brain.ouroboros.ibkr_data_provider import get_provider
        provider = get_provider()
    except (ImportError, Exception) as e:
        log.debug("IBKR provider unavailable: %s", e)
        return {}

    result: Dict[str, Any] = {"symbol": symbol}

    # Primary: ReportSnapshot (has the richest data)
    try:
        if not provider._ensure_connected():
            log.debug("IBKR not connected for %s", symbol)
            return {}

        provider._rate_limit()
        contract = provider._make_contract(symbol)

        with provider._lock:
            ib = provider._ib

        if ib is None:
            return {}

        xml_data = ib.reqFundamentalData(contract, reportType="ReportSnapshot")
        if xml_data:
            result = _parse_report_snapshot(xml_data, symbol)
            log.debug("ReportSnapshot for %s: %d fields", symbol, len(result))

    except Exception as e:
        log.debug("ReportSnapshot failed for %s: %s", symbol, e)

    # Secondary: ReportsFinSummary (for revenue/EPS history and growth computation)
    try:
        if provider._ensure_connected():
            provider._rate_limit()
            contract = provider._make_contract(symbol)

            with provider._lock:
                ib = provider._ib

            if ib is not None:
                xml_fin = ib.reqFundamentalData(contract, reportType="ReportsFinSummary")
                if xml_fin:
                    fin_data = _parse_financial_summary(xml_fin, symbol)
                    # Merge computed growth if not already present from snapshot
                    if "revenue_growth_pct" not in result and "revenue_growth_computed_pct" in fin_data:
                        result["revenue_growth_pct"] = fin_data["revenue_growth_computed_pct"]
                    if "eps_growth_pct" not in result and "eps_growth_computed_pct" in fin_data:
                        result["eps_growth_pct"] = fin_data["eps_growth_computed_pct"]

    except Exception as e:
        log.debug("ReportsFinSummary failed for %s: %s", symbol, e)

    result["fetched_at"] = datetime.now(timezone.utc).isoformat()
    return result


def refresh_all_fundamentals(symbols: List[str], max_symbols: int = 100) -> Dict[str, Any]:
    """Refresh fundamental data for all given symbols. Returns summary stats.

    Called by nightly pipeline. Rate-limited (150ms between requests).
    Saves updated cache to disk + writes fundamental_signals.json for bridge.py.

    Args:
        symbols: List of tickers to refresh.
        max_symbols: Maximum symbols to fetch per run (IBKR rate limits).

    Returns:
        Summary dict with counts and timing.
    """
    start = time.monotonic()
    log.info("Fundamental refresh starting: %d symbols (max %d)", len(symbols), max_symbols)

    cache = _load_disk_cache()
    fetched = 0
    cached_used = 0
    failed = 0

    for sym in symbols[:max_symbols]:
        # Check if cache is still fresh
        if sym in cache:
            cached_entry = cache[sym]
            fetched_at = cached_entry.get("fetched_at", "")
            if fetched_at:
                try:
                    fetch_time = datetime.fromisoformat(fetched_at)
                    age_secs = (datetime.now(timezone.utc) - fetch_time).total_seconds()
                    if age_secs < _CACHE_TTL_SECS:
                        cached_used += 1
                        continue
                except (ValueError, TypeError):
                    pass

        # Fetch from IBKR
        result = _fetch_from_ibkr(sym)
        if result and len(result) > 2:
            with _cache_lock:
                _cache[sym] = result
            fetched += 1
        else:
            failed += 1

        time.sleep(0.15)  # IBKR pacing: ~6.7 req/s

    # Save cache to disk
    with _cache_lock:
        _save_disk_cache(_cache)

    # Generate fundamental_signals.json for bridge.py overlay
    signals = _generate_fundamental_signals(_cache)
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(_SIGNALS_FILE, "w") as f:
            json.dump(signals, f, indent=2)
        log.info("Saved fundamental_signals.json: %d tickers scored", len(signals.get("tickers", {})))
    except Exception as e:
        log.error("Failed to save fundamental_signals.json: %s", e)

    elapsed = time.monotonic() - start
    summary = {
        "total_symbols": len(symbols),
        "fetched": fetched,
        "cached_used": cached_used,
        "failed": failed,
        "signals_generated": len(signals.get("tickers", {})),
        "duration_secs": round(elapsed, 1),
    }
    log.info("Fundamental refresh complete: %s", summary)
    return summary


def _generate_fundamental_signals(cache: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Generate per-ticker fundamental signals from cached data.

    Produces the fundamental_signals.json consumed by bridge.py overlay.
    Format mirrors insider_signals.json pattern:
    {
        "generated_at": "...",
        "tickers": {
            "AAPL": {
                "confidence_delta": 5,
                "screens": ["value", "quality"],
                "pe_trailing": 28.5,
                ...
            }
        }
    }
    """
    now = datetime.now(timezone.utc)
    tickers: Dict[str, Dict[str, Any]] = {}

    for sym, data in cache.items():
        # Skip stale entries (> 48h — give 2x TTL for signals)
        fetched_at = data.get("fetched_at", "")
        if fetched_at:
            try:
                fetch_time = datetime.fromisoformat(fetched_at)
                if (now - fetch_time).total_seconds() > _CACHE_TTL_SECS * 2:
                    continue
            except (ValueError, TypeError):
                continue

        screens = []
        confidence_delta = 0
        details: Dict[str, Any] = {}

        # ── VALUE SCREEN: P/E < 15, P/B < 2, ROE > 15% ──
        pe = data.get("pe_trailing")
        pb = data.get("pb_ratio")
        roe = data.get("roe")
        if pe is not None and pe > 0 and pe < 15:
            if pb is not None and pb > 0 and pb < 2:
                if roe is not None and roe > 15:
                    screens.append("value")
                    confidence_delta += 5  # Strong value play
                    details["value_pe"] = round(pe, 1)
                    details["value_pb"] = round(pb, 2)
                    details["value_roe"] = round(roe, 1)

        # ── GROWTH SCREEN: Revenue growth > 20% + EPS growth positive ──
        rev_growth = data.get("revenue_growth_pct")
        eps_growth = data.get("eps_growth_pct")
        if rev_growth is not None and rev_growth > 20:
            if eps_growth is not None and eps_growth > 0:
                screens.append("growth")
                confidence_delta += 4  # Growth momentum
                details["growth_rev_pct"] = round(rev_growth, 1)
                details["growth_eps_pct"] = round(eps_growth, 1)

        # ── SHORT INTEREST SCREEN: Days to cover > 5 = short squeeze potential ──
        dtc = data.get("days_to_cover")
        if dtc is not None and dtc > 5:
            screens.append("short_squeeze")
            # Graduated boost: 5-8 days = +3, 8-12 = +5, 12+ = +7
            if dtc > 12:
                confidence_delta += 7
            elif dtc > 8:
                confidence_delta += 5
            else:
                confidence_delta += 3
            details["short_days_to_cover"] = round(dtc, 1)

        # ── ANALYST CONSENSUS SCREEN ──
        buy_pct = data.get("analyst_buy_pct")
        sell_pct = data.get("analyst_sell_pct")
        analyst_total = data.get("analyst_total", 0)
        if analyst_total and analyst_total >= 5:  # Minimum analyst coverage
            if buy_pct is not None and buy_pct > 80:
                screens.append("analyst_strong_buy")
                confidence_delta += 3
                details["analyst_buy_pct"] = buy_pct
            elif sell_pct is not None and sell_pct > 50:
                screens.append("analyst_bearish")
                confidence_delta -= 5
                details["analyst_sell_pct"] = sell_pct

        # ── TARGET PRICE SCREEN ──
        # If consensus target is >15% above current (we may not have current price
        # in the fundamentals data, but target_price alone is informative)
        target = data.get("target_price")
        if target is not None and target > 0:
            details["target_price"] = round(target, 2)

        # ── INSIDER OWNERSHIP SCREEN: >10% insider = alignment ──
        insider_own = data.get("insider_ownership_pct")
        if insider_own is not None and insider_own > 10:
            screens.append("insider_aligned")
            confidence_delta += 2
            details["insider_ownership_pct"] = round(insider_own, 1)

        # ── QUALITY SCREEN: Strong balance sheet ──
        de = data.get("debt_to_equity")
        cr = data.get("current_ratio")
        fcf = data.get("free_cash_flow_m") or data.get("fcf_per_share")
        if de is not None and de < 0.5 and cr is not None and cr > 1.5:
            if fcf is not None and fcf > 0:
                screens.append("quality")
                confidence_delta += 2
                details["quality_de"] = round(de, 2)
                details["quality_cr"] = round(cr, 2)

        # ── DANGER SCREENS (negative signals) ──
        # Extremely high P/E = speculative
        if pe is not None and pe > 100:
            screens.append("speculative_pe")
            confidence_delta -= 3
            details["speculative_pe"] = round(pe, 1)

        # Very high debt
        if de is not None and de > 3.0:
            screens.append("high_leverage")
            confidence_delta -= 3
            details["high_leverage_de"] = round(de, 2)

        # Negative EPS
        eps = data.get("eps_trailing") or data.get("eps_ttm") or data.get("eps_normalized")
        if eps is not None and eps < 0:
            screens.append("negative_earnings")
            confidence_delta -= 2
            details["negative_eps"] = round(eps, 2)

        # Only include tickers with actual signal
        if screens or confidence_delta != 0:
            # Clamp confidence delta to [-10, +10]
            confidence_delta = max(-10, min(10, confidence_delta))
            ticker_signal = {
                "confidence_delta": confidence_delta,
                "screens": screens,
                **details,
            }
            # Include key metrics for diagnostics even without screens
            for key in ("pe_trailing", "pb_ratio", "roe", "dividend_yield",
                        "debt_to_equity", "market_cap_m", "days_to_cover"):
                if key in data and data[key] is not None:
                    ticker_signal.setdefault(key, data[key])

            tickers[sym] = ticker_signal

    return {
        "generated_at": now.isoformat(),
        "type": "fundamental_signals",
        "tickers": tickers,
        "total_cached": len(cache),
        "total_scored": len(tickers),
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [FundamentalData] %(levelname)s %(message)s",
    )

    # Self-test: try to fetch AAPL fundamentals
    print("=" * 60)
    print("Fundamental Data Provider — Self Test")
    print("=" * 60)

    test_symbols = ["AAPL", "TSLA", "NVDA", "MSFT", "AMZN"]
    for sym in test_symbols:
        data = get_fundamentals(sym)
        if data and len(data) > 2:
            print(f"  {sym}: {len(data)} fields")
            for key in ("pe_trailing", "roe", "revenue_growth_pct", "days_to_cover",
                        "analyst_buy_pct", "insider_ownership_pct", "market_cap_m"):
                if key in data:
                    print(f"    {key}: {data[key]}")
        else:
            print(f"  {sym}: no data available")

    print("=" * 60)
    print("Self-test complete")
