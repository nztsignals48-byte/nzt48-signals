"""Earnings Boost Producer — Per-symbol earnings proximity scores for Dynamic Universe.

Generates data/event_calendar.json with symbol→score format that Phase 9
boost loader can consume directly. Scores represent urgency of upcoming
earnings announcements.

Scoring logic:
    T-5 to T-2 days before earnings:  +0.06 (position for pre-earnings drift)
    T-1 day before earnings:           +0.12 (max boost — vol expansion imminent)
    T+0 earnings day:                  -0.12 (negative boost — avoid earnings lottery)
    T+1 day after earnings:            +0.08 (post-earnings drift capture)
    No upcoming earnings:              0.00  (no boost)

Data sources (priority order):
    1. IBKR reqFundamentalData (if available) — most reliable
    2. yfinance earnings calendar — free, covers US + major international
    3. Cached earnings_dates.json from nightly pipeline — fallback

Usage:
    python3 -m python_brain.feeds.earnings_boost_producer

Produces:
    data/event_calendar.json — {"scores": {"AAPL": 0.12, "TSLA": -0.12, ...}, ...}
    data/earnings_dates.json — {"AAPL": "2026-04-28", ...} (raw dates cache)
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

log = logging.getLogger("earnings_boost")

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
EARNINGS_DATES_FILE = DATA_DIR / "earnings_dates.json"
EVENT_CALENDAR_FILE = DATA_DIR / "event_calendar.json"
CONTRACTS_FILE = CONFIG_DIR / "contracts.toml"
MASTER_FILE = CONFIG_DIR / "isa_universe_master.json"

# ETP → underlying mapping (leveraged ETPs inherit underlying's earnings dates)
# Session 35 FIX: Corrected 3LAM.L/3SAM.L from AMZN→AMD, GPT3.L from MSFT→NVDA.
# Single source of truth: config/equity_fund_map.toml
# See also: bridge.py:1108 (must stay in sync)
_ETP_UNDERLYING_MAP = {
    "QQQ3.L": "QQQ", "QQQS.L": "QQQ", "QQQ5.L": "QQQ",
    "3LUS.L": "SPY", "3USS.L": "SPY", "5SPY.L": "SPY",
    "NVD3.L": "NVDA", "3LNV.L": "NVDA", "3SNV.L": "NVDA",
    "TSL3.L": "TSLA", "3LTS.L": "TSLA", "3STS.L": "TSLA",
    "GPT3.L": "NVDA",  # FIX: was MSFT, confirmed NVDA (AI/Tech basket, NVDA-heavy)
    "TSM3.L": "TSM",
    "AMD3.L": "AMD", "3LAM.L": "AMD", "3SAM.L": "AMD",  # FIX: was AMZN, confirmed AMD
    "AMZ3.L": "AMZN", "3LAZ.L": "AMZN",
    "APL3.L": "AAPL", "3LAP.L": "AAPL", "3SAP.L": "AAPL",
    "MSF3.L": "MSFT", "3LMS.L": "MSFT", "3SMS.L": "MSFT",
    "GOO3.L": "GOOGL", "3LGO.L": "GOOGL",
    "MET3.L": "META", "3LME.L": "META",
    "MU2.L": "MU",
    "3LNF.L": "NFLX",
    "3SEM.L": "SMH",  # Semiconductor index
}


def _load_universe_symbols() -> Set[str]:
    """Load all symbols from contracts.toml and master universe."""
    symbols: Set[str] = set()

    # From contracts.toml
    try:
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore
            except ImportError:
                tomllib = None  # type: ignore

        if tomllib and CONTRACTS_FILE.exists():
            with open(CONTRACTS_FILE, "rb") as f:
                data = tomllib.load(f)
            for c in data.get("contracts", []):
                sym = c.get("symbol", "")
                if sym:
                    symbols.add(sym)
    except Exception as e:
        log.warning("Failed to load contracts.toml: %s", e)

    # Add known underlyings from ETP map
    symbols.update(_ETP_UNDERLYING_MAP.values())

    return symbols


def _fetch_earnings_yfinance(symbols: List[str]) -> Dict[str, str]:
    """Fetch next earnings dates via yfinance. Returns {symbol: "YYYY-MM-DD"}."""
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not available — skipping earnings fetch")
        return {}

    dates: Dict[str, str] = {}
    batch_size = 20
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        for sym in batch:
            try:
                ticker = yf.Ticker(sym)
                cal = ticker.calendar
                if cal is not None and not cal.empty:
                    if "Earnings Date" in cal.index:
                        earn_dates = cal.loc["Earnings Date"]
                        if hasattr(earn_dates, 'iloc') and len(earn_dates) > 0:
                            next_date = str(earn_dates.iloc[0])[:10]
                            dates[sym] = next_date
                        elif hasattr(earn_dates, 'strftime'):
                            dates[sym] = earn_dates.strftime("%Y-%m-%d")
                elif hasattr(ticker, 'earnings_dates') and ticker.earnings_dates is not None:
                    ed = ticker.earnings_dates
                    if len(ed) > 0:
                        next_date = str(ed.index[0])[:10]
                        dates[sym] = next_date
            except Exception as e:
                log.debug("yfinance earnings fetch failed for %s: %s", sym, e)
            time.sleep(0.1)  # Rate limit

        if i + batch_size < len(symbols):
            time.sleep(1.0)  # Batch sleep

    return dates


def _fetch_earnings_ibkr(symbols: List[str]) -> Dict[str, str]:
    """Fetch earnings dates via IBKR reqFundamentalData. Returns {symbol: "YYYY-MM-DD"}."""
    try:
        from python_brain.ouroboros.ibkr_data_provider import get_provider
        provider = get_provider()
        if provider is None:
            return {}
    except (ImportError, Exception) as e:
        log.debug("IBKR provider not available: %s", e)
        return {}

    dates: Dict[str, str] = {}
    for sym in symbols[:50]:  # Rate limit: 50 max
        try:
            result = provider.get_fundamental_data(sym, report_type="CalendarReport")
            if result and "earnings_date" in result:
                dates[sym] = result["earnings_date"]
        except Exception as e:
            log.debug("IBKR fundamental fetch failed for %s: %s", sym, e)
        time.sleep(0.15)  # IBKR pacing

    return dates


def _load_cached_earnings() -> Dict[str, str]:
    """Load cached earnings dates from disk."""
    if not EARNINGS_DATES_FILE.exists():
        return {}
    try:
        with open(EARNINGS_DATES_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_earnings_dates(dates: Dict[str, str]):
    """Save earnings dates cache to disk."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(EARNINGS_DATES_FILE, "w") as f:
            json.dump(dates, f, indent=2)
    except Exception as e:
        log.warning("Failed to save earnings dates: %s", e)


def _compute_scores(
    earnings_dates: Dict[str, str],
    universe_symbols: Set[str],
) -> Dict[str, float]:
    """Compute per-symbol earnings proximity scores.

    Returns {symbol: score} for all symbols in universe.
    Score > 0 means "boost this ticker" (pre/post earnings drift).
    Score < 0 means "penalize this ticker" (earnings day lottery avoidance).
    """
    now = datetime.now(timezone.utc).date()
    scores: Dict[str, float] = {}

    # Build reverse ETP map: underlying → [etps]
    underlying_to_etps: Dict[str, List[str]] = {}
    for etp, underlying in _ETP_UNDERLYING_MAP.items():
        underlying_to_etps.setdefault(underlying, []).append(etp)

    for sym, date_str in earnings_dates.items():
        try:
            earn_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue

        days_until = (earn_date - now).days

        # Scoring based on proximity to earnings
        score = 0.0
        if days_until > 5 or days_until < -2:
            continue  # Too far away — no boost
        elif 2 <= days_until <= 5:
            score = 0.06  # Pre-earnings positioning window
        elif days_until == 1:
            score = 0.12  # T-1: max boost (vol expansion imminent)
        elif days_until == 0:
            score = -0.12  # T+0: AVOID (earnings lottery)
        elif days_until == -1:
            score = 0.08  # T+1: post-earnings drift capture
        elif days_until == -2:
            score = 0.04  # T+2: fading drift

        if abs(score) > 0.001:
            # Apply to the underlying itself
            if sym in universe_symbols:
                scores[sym] = score

            # Propagate to all leveraged ETPs tracking this underlying
            for etp in underlying_to_etps.get(sym, []):
                if etp in universe_symbols:
                    # ETPs get stronger signal (leverage amplifies earnings moves)
                    etp_score = score * 1.5 if score > 0 else score * 2.0
                    scores[etp] = max(min(etp_score, 0.12), -0.12)

    return scores


def _build_event_boost_artifact(
    earnings_scores: Dict[str, float],
    macro_events: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build the event_calendar.json in boost-compatible format.

    Merges earnings proximity scores with macro event data.
    Phase 9 boost loader reads the "scores" key directly.
    """
    now = datetime.now(timezone.utc)

    # Start with earnings scores
    combined_scores = dict(earnings_scores)

    # Add macro event tickers (if events carry affected_tickers)
    for event in macro_events:
        affected = event.get("affected_tickers", [])
        impact = event.get("impact", "MEDIUM")
        for sym in affected:
            macro_boost = {"HIGH": 0.10, "MEDIUM": 0.06, "LOW": 0.03}.get(impact, 0.04)
            # Don't override earnings penalty with macro boost
            existing = combined_scores.get(sym, 0.0)
            if existing >= 0:
                combined_scores[sym] = max(existing, macro_boost)

    return {
        "generated_at": now.isoformat(),
        "type": "earnings_event_boost",
        "scores": combined_scores,
        "earnings_count": len(earnings_scores),
        "macro_count": len(macro_events),
        "symbols_boosted": len([s for s in combined_scores.values() if s > 0]),
        "symbols_penalized": len([s for s in combined_scores.values() if s < 0]),
    }


def run_earnings_boost() -> Dict[str, Any]:
    """Main entry point. Fetches earnings dates, computes scores, saves artifacts."""
    start = time.monotonic()
    log.info("Earnings boost producer starting")

    # Load universe
    universe = _load_universe_symbols()
    log.info("Universe: %d symbols", len(universe))

    # Determine which symbols need earnings lookups
    # Focus on US underlyings (earnings dates matter most for US stocks)
    us_underlyings = sorted(set(_ETP_UNDERLYING_MAP.values()))
    # Also include top US equities from universe (non-.L symbols)
    us_equities = sorted([s for s in universe if not any(s.endswith(sfx) for sfx in
                          [".L", ".T", ".HK", ".KS", ".DE", ".PA", ".AS", ".SI", ".AX"])])[:100]
    lookup_symbols = sorted(set(us_underlyings + us_equities))

    # Fetch earnings dates (priority: IBKR → yfinance → cache)
    cached = _load_cached_earnings()
    ibkr_dates = _fetch_earnings_ibkr(lookup_symbols[:30])  # IBKR for top 30
    yf_dates = _fetch_earnings_yfinance(
        [s for s in lookup_symbols if s not in ibkr_dates][:70]
    )

    # Merge: IBKR > yfinance > cache
    merged = dict(cached)
    merged.update(yf_dates)
    merged.update(ibkr_dates)

    # Save raw dates cache
    _save_earnings_dates(merged)
    log.info("Earnings dates: %d total (%d IBKR, %d yfinance, %d cached)",
             len(merged), len(ibkr_dates), len(yf_dates), len(cached))

    # Compute scores
    scores = _compute_scores(merged, universe)
    log.info("Earnings scores: %d symbols (%d boosted, %d penalized)",
             len(scores),
             len([s for s in scores.values() if s > 0]),
             len([s for s in scores.values() if s < 0]))

    # Load existing macro events for merging
    macro_events = []
    try:
        from python_brain.events.event_calendar import EventCalendar
        cal = EventCalendar()
        upcoming = cal.upcoming(days=7)
        macro_events = [{"name": e.name, "event_type": e.event_type,
                         "impact": e.impact, "affected_tickers": []}
                        for e in upcoming]
    except Exception as e:
        log.debug("Event calendar not available: %s", e)

    # Build and save boost artifact
    artifact = _build_event_boost_artifact(scores, macro_events)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(EVENT_CALENDAR_FILE, "w") as f:
            json.dump(artifact, f, indent=2)
        log.info("Saved event_calendar.json: %d scores", len(artifact["scores"]))
    except Exception as e:
        log.error("Failed to save event_calendar.json: %s", e)

    elapsed = time.monotonic() - start
    log.info("Earnings boost producer complete in %.1fs", elapsed)

    return {
        "total_dates": len(merged),
        "ibkr_dates": len(ibkr_dates),
        "yf_dates": len(yf_dates),
        "scores_produced": len(scores),
        "symbols_boosted": artifact["symbols_boosted"],
        "symbols_penalized": artifact["symbols_penalized"],
        "duration_secs": round(elapsed, 1),
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [EarningsBoost] %(levelname)s %(message)s",
    )
    result = run_earnings_boost()
    print(json.dumps(result, indent=2))
