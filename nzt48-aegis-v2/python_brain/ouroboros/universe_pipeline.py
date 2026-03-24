"""Universe Pipeline — 5-phase orchestrator for AEGIS V2 universe management.

Builds a 5,000-ticker curated research universe, applies deterministic filters
to create an eligible universe, uses Gemini for bounded session-aware ranking,
applies deterministic final admission, and generates all tier artifacts.

Pipeline Phases:
  Phase 1: Build RESEARCH_5000 (top-quality 5,000 from 36K master)
  Phase 2: Apply deterministic filters → ELIGIBLE + PENDING_CONTRACT + EXCLUDED
  Phase 3: Session-aware ranking + Gemini triage
  Phase 4: Deterministic final admission → ACTIVE + SHADOW
  Phase 5: Generate artifacts + engine bridge (active_watchlist.json)

DOCTRINE:
  - Gemini ranks and triages; it does NOT decide final admission.
  - Deterministic filters have final authority on what gets streamed.
  - The Rust 33-CHECK RiskArbiter is the final authority on trades.
  - If Gemini fails, the system degrades to deterministic scoring.

Usage:
    python3 -m python_brain.ouroboros.universe_pipeline
    python3 -m python_brain.ouroboros.universe_pipeline --dry-run
    python3 -m python_brain.ouroboros.universe_pipeline --rebuild-research
    python3 -m python_brain.ouroboros.universe_pipeline --phase 1
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PROJECT_ROOT / "python_brain"))
sys.path.insert(0, str(_PROJECT_ROOT))

from python_brain.ouroboros.session_map import (
    SESSION_MAP, SESSION_ORDER, SessionDefinition,
    detect_current_session, get_next_session, session_map_to_dict,
    ticker_matches_session, normalize_exchange,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Universe-Pipeline] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("universe_pipeline")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
UNIVERSE_DIR = DATA_DIR / "universe"
MASTER_FILE = CONFIG_DIR / "isa_universe_master.json"
UNIVERSE_JSON = CONFIG_DIR / "universe.json"
CONTRACTS_FILE = CONFIG_DIR / "contracts.toml"
CONFIG_FILE = CONFIG_DIR / "config.toml"
WATCHLIST_FILE = CONFIG_DIR / "active_watchlist.json"
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))

# Artifact paths
RESEARCH_FILE = UNIVERSE_DIR / "research_universe_5000.json"
ELIGIBLE_FILE = UNIVERSE_DIR / "eligible_universe.json"
PENDING_FILE = UNIVERSE_DIR / "pending_contracts.json"
ACTIVE_FILE = UNIVERSE_DIR / "active_session_universe.json"
SHADOW_FILE = UNIVERSE_DIR / "shadow_universe.json"
EXCLUDED_FILE = UNIVERSE_DIR / "excluded_universe.json"
SESSION_STATE_FILE = UNIVERSE_DIR / "session_map.json"
RANKINGS_FILE = UNIVERSE_DIR / "gemini_rankings.json"
DECISION_LOG = UNIVERSE_DIR / "decision_log.ndjson"
SUMMARY_FILE = UNIVERSE_DIR / "session_universe_summary.json"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RESEARCH_TARGET = 5000
ENGINE_MAX_SLOTS = 100       # Rust engine hard cap (engine.rs line 2682)
SHADOW_TARGET = 200
MIN_VOLUME_ELIGIBLE = 100_000
MIN_PRICE_ELIGIBLE = 0.10

# Major index sources (high-quality signal for research scoring)
MAJOR_INDICES = {
    "S&P 500", "NASDAQ 100", "FTSE 100", "FTSE 250", "FTSE All-Share",
    "Russell 2000", "DAX 40", "CAC 40", "Nikkei 225",
    "Hang Seng", "Hang Seng Tech", "ASX 200", "Euro Stoxx 50",
    "Euro Stoxx 600", "TSX 60", "KOSPI 200", "SMI", "STI",
}

# Exchange quality multiplier for research scoring
EXCHANGE_QUALITY = {
    "NYSE": 10.0, "NASDAQ": 10.0, "AMEX": 8.0, "SMART": 10.0,
    "LSE": 9.0, "LSEETF": 7.0,
    "XETRA": 7.0, "EURONEXT": 7.0, "EURONEXT_PA": 7.0, "EURONEXT_AS": 7.0,
    "AEB": 7.0, "SIX": 6.0,
    "TSE": 8.0, "HKEX": 7.0, "SGX": 5.0, "KRX": 6.0,
    "ASX": 6.0, "TSX": 6.0, "NZX": 4.0,
}


# ---------------------------------------------------------------------------
# Utility: atomic JSON write (from universe_filters.py pattern)
# ---------------------------------------------------------------------------
def _atomic_write(path: Path, data: Any, indent: int = 2) -> None:
    """Write JSON atomically via tmp + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".tmp", prefix=path.stem + "_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=indent, default=str)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _log_decision(phase: str, symbol: str, action: str, reason: str = "",
                   extra: Optional[Dict] = None) -> Dict:
    """Create and return a decision log entry."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        "symbol": symbol,
        "action": action,
        "reason": reason,
    }
    if extra:
        entry.update(extra)
    return entry


# ---------------------------------------------------------------------------
# Data loaders (reuse existing patterns)
# ---------------------------------------------------------------------------

def _load_master() -> List[Dict[str, Any]]:
    """Load isa_universe_master.json tickers."""
    if not MASTER_FILE.exists():
        log.warning("Master file not found: %s", MASTER_FILE)
        return []
    try:
        with open(MASTER_FILE) as f:
            data = json.load(f)
        tickers = data.get("tickers", [])
        log.info("Loaded %d tickers from master universe", len(tickers))
        return tickers
    except (json.JSONDecodeError, IOError) as e:
        log.error("Failed to load master: %s", e)
        return []


def _load_universe_json() -> List[Dict[str, Any]]:
    """Load universe.json (curated index constituents)."""
    if not UNIVERSE_JSON.exists():
        return []
    try:
        with open(UNIVERSE_JSON) as f:
            data = json.load(f)
        result = []
        for exch_name, exch_data in data.get("exchanges", {}).items():
            for t in exch_data.get("tickers", []):
                sym = t.get("symbol", "")
                if not sym:
                    continue
                exchange = t.get("ibkr_exchange", exch_name)
                if exchange == "ISLAND":
                    exchange = "NASDAQ"
                # Map index to source
                raw_index = t.get("index", "")
                indices = [idx.strip() for idx in raw_index.split(",") if idx.strip()]
                source = ""
                _INDEX_MAP = {
                    "FTSE100": "FTSE 100", "FTSE250": "FTSE 250",
                    "SP500": "S&P 500", "NDX100": "NASDAQ 100",
                }
                for idx in indices:
                    mapped = _INDEX_MAP.get(idx, "")
                    if mapped:
                        source = mapped
                result.append({
                    "symbol": sym, "exchange": exchange,
                    "name": t.get("name", ""), "type": "stock",
                    "sector": t.get("sector", "Unknown"),
                    "currency": t.get("currency", "USD"),
                    "isa_eligible": True, "leveraged": False,
                    "inverse": False, "leverage_factor": 1,
                    "market_cap_usd": 0, "avg_daily_volume": 0,
                    "validated": True, "source": source,
                })
        log.info("Loaded %d tickers from universe.json", len(result))
        return result
    except Exception as e:
        log.warning("Failed to load universe.json: %s", e)
        return []


def _load_contract_symbols() -> Set[str]:
    """Load all contract symbols from contracts.toml."""
    if not CONTRACTS_FILE.exists():
        return set()
    try:
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib
            except ImportError:
                # Manual parse fallback
                symbols = set()
                with open(CONTRACTS_FILE) as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("symbol"):
                            parts = line.split("=", 1)
                            if len(parts) == 2:
                                sym = parts[1].strip().strip('"').strip("'")
                                if sym:
                                    symbols.add(sym)
                return symbols
        with open(CONTRACTS_FILE, "rb") as f:
            data = tomllib.load(f)
        return {c["symbol"] for c in data.get("contracts", []) if c.get("symbol")}
    except Exception as e:
        log.warning("Failed to load contracts.toml: %s", e)
        return set()


def _load_blacklist() -> Tuple[Set[str], Set[str]]:
    """Load blacklisted tickers and exchanges from config.toml."""
    bl_tickers: Set[str] = set()
    bl_exchanges: Set[str] = set()
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, "rb") as f:
                data = tomllib.load(f)
            bl = data.get("blacklist", {})
            bl_tickers = set(bl.get("tickers", []))
            bl_exchanges = set(bl.get("exchanges", []))
    except Exception as e:
        log.warning("Failed to load blacklist: %s", e)
    return bl_tickers, bl_exchanges


def _load_pipeline_config() -> Dict[str, Any]:
    """Load [universe_pipeline] config from config.toml."""
    defaults = {
        "research_universe_size": RESEARCH_TARGET,
        "eligible_min_volume": MIN_VOLUME_ELIGIBLE,
        "eligible_min_price": MIN_PRICE_ELIGIBLE,
        "streamed_slots": ENGINE_MAX_SLOTS,
        "shadow_slots": SHADOW_TARGET,
        "gemini_triage_enabled": True,
        "gemini_triage_timeout_secs": 90,
    }
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, "rb") as f:
                data = tomllib.load(f)
            cfg = data.get("universe_pipeline", {})
            defaults.update({k: v for k, v in cfg.items() if k in defaults})
    except Exception:
        pass
    return defaults


# ---------------------------------------------------------------------------
# PHASE 1: Build Research Universe (5,000)
# ---------------------------------------------------------------------------

def phase_1_research_universe(
    target: int = RESEARCH_TARGET,
    force_rebuild: bool = False,
) -> List[Dict[str, Any]]:
    """Build the curated 5,000-ticker research universe.

    Strategy: score every ticker from the master + universe.json sources,
    then take the top N by quality. Quality = index membership + validation
    + market cap + volume + exchange quality + leverage bonus.

    If a recent research file exists and force_rebuild is False, loads it.
    """
    log.info("=" * 60)
    log.info("PHASE 1: Build Research Universe (%d target)", target)
    log.info("=" * 60)

    # Check for recent research file (skip rebuild if <24h old)
    if not force_rebuild and RESEARCH_FILE.exists():
        try:
            with open(RESEARCH_FILE) as f:
                cached = json.load(f)
            gen_time = cached.get("generated", "")
            if gen_time:
                gen_dt = datetime.fromisoformat(gen_time)
                if gen_dt.tzinfo is None:
                    gen_dt = gen_dt.replace(tzinfo=timezone.utc)
                age_hours = (datetime.now(timezone.utc) - gen_dt).total_seconds() / 3600
                if age_hours < 24:
                    tickers = cached.get("tickers", [])
                    log.info("Using cached research universe (%d tickers, %.1fh old)",
                             len(tickers), age_hours)
                    return tickers
        except Exception:
            pass

    # Load all sources
    master_tickers = _load_master()
    universe_tickers = _load_universe_json()
    contract_symbols = _load_contract_symbols()

    # Merge and deduplicate (universe.json tickers override master for same symbol)
    by_symbol: Dict[str, Dict] = {}
    for t in master_tickers:
        if t.get("delisted"):
            continue
        sym = t.get("symbol", "")
        if sym:
            by_symbol[sym] = t

    for t in universe_tickers:
        sym = t.get("symbol", "")
        if sym:
            if sym in by_symbol:
                # Merge: keep validated/volume from master, add source from universe.json
                existing = by_symbol[sym]
                if t.get("source") and t["source"] in MAJOR_INDICES:
                    existing["source"] = t["source"]
                if t.get("name") and not existing.get("name"):
                    existing["name"] = t["name"]
                if t.get("validated"):
                    existing["validated"] = True
            else:
                by_symbol[sym] = t

    all_tickers = list(by_symbol.values())
    log.info("Merged %d unique tickers from master + universe.json", len(all_tickers))

    # Score each ticker for research quality
    for t in all_tickers:
        score = 0.0

        # Index membership (strong quality signal)
        source = t.get("source", "")
        if source in MAJOR_INDICES:
            score += 50.0

        # Validation status
        if t.get("validated"):
            score += 20.0

        # Has IBKR contract (can actually be traded)
        # Apply same suffix-strip logic as Phase 2: "0700.HK" → check "0700"
        _sym = t.get("symbol", "")
        _in_contracts = _sym in contract_symbols
        if not _in_contracts and "." in _sym and not _sym.endswith(".L"):
            _in_contracts = _sym.rsplit(".", 1)[0] in contract_symbols
        if _in_contracts:
            score += 25.0
            t["has_contract"] = True
        else:
            t["has_contract"] = False

        # Market cap (log10 scaled, max +30)
        mcap = t.get("market_cap_usd", 0)
        if mcap and mcap > 0:
            score += min(30.0, math.log10(max(mcap, 1)) * 3)

        # Average daily volume (log10 scaled, max +20)
        vol = t.get("avg_daily_volume", 0)
        if vol and vol > 0:
            score += min(20.0, math.log10(max(vol, 1)) * 2.5)

        # Leveraged ETP bonus (strategy edge from amplified volume)
        if t.get("leveraged"):
            score += 15.0

        # Exchange quality
        exch = t.get("exchange", "")
        score += EXCHANGE_QUALITY.get(exch, 3.0)

        t["research_score"] = round(score, 2)

    # Sort by score descending
    all_tickers.sort(key=lambda t: -t.get("research_score", 0))

    # Ensure exchange diversity: min 50 per exchange that has ≥50 qualifying
    research = _ensure_exchange_diversity(all_tickers, target)

    # Add session_availability based on exchange (with normalization)
    for t in research:
        exch = t.get("exchange", "")
        sessions = []
        for name, sdef in SESSION_MAP.items():
            if ticker_matches_session(exch, sdef):
                sessions.append(name)
        t["session_availability"] = sessions

    # Ensure metadata fields
    for t in research:
        t.setdefault("instrument_class", "leveraged_etp" if t.get("leveraged") else "equity")
        t.setdefault("country", _exchange_to_country(t.get("exchange", "")))
        t.setdefault("volatility_proxy", 0.0)
        t.setdefault("spread_proxy", 0.0)

    log.info("PHASE 1 complete: %d research tickers (from %d candidates)", len(research), len(all_tickers))

    # Save artifact
    artifact = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "version": 1,
        "total": len(research),
        "target": target,
        "sources": ["isa_universe_master", "universe_json"],
        "exchange_breakdown": _exchange_breakdown(research),
        "tickers": research,
    }
    _atomic_write(RESEARCH_FILE, artifact)
    log.info("Saved: %s", RESEARCH_FILE)

    return research


def _ensure_exchange_diversity(
    sorted_tickers: List[Dict], target: int, min_per_exchange: int = 50,
) -> List[Dict]:
    """Take top N tickers while ensuring exchange diversity.

    Each exchange with ≥ min_per_exchange qualifying tickers gets at least
    min_per_exchange slots. Remaining slots filled by top score.
    """
    # Count available per exchange
    exchange_pools: Dict[str, List[Dict]] = defaultdict(list)
    for t in sorted_tickers:
        exchange_pools[t.get("exchange", "Unknown")].append(t)

    # Reserve minimum per qualifying exchange
    reserved: List[Dict] = []
    reserved_syms: Set[str] = set()

    for exch, pool in exchange_pools.items():
        if len(pool) >= min_per_exchange:
            for t in pool[:min_per_exchange]:
                if t["symbol"] not in reserved_syms:
                    reserved.append(t)
                    reserved_syms.add(t["symbol"])

    # Fill remaining from top of sorted list
    remaining = target - len(reserved)
    for t in sorted_tickers:
        if remaining <= 0:
            break
        if t["symbol"] not in reserved_syms:
            reserved.append(t)
            reserved_syms.add(t["symbol"])
            remaining -= 1

    # Sort final result by score
    reserved.sort(key=lambda t: -t.get("research_score", 0))
    return reserved[:target]


def _exchange_breakdown(tickers: List[Dict]) -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    for t in tickers:
        counts[t.get("exchange", "Unknown")] += 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def _exchange_to_country(exchange: str) -> str:
    _MAP = {
        "NYSE": "US", "NASDAQ": "US", "AMEX": "US", "SMART": "US",
        "LSE": "UK", "LSEETF": "UK",
        "XETRA": "DE", "EURONEXT": "EU", "EURONEXT_PA": "FR",
        "EURONEXT_AS": "NL", "AEB": "NL", "SIX": "CH",
        "TSE": "JP", "HKEX": "HK", "SGX": "SG", "KRX": "KR",
        "ASX": "AU", "TSX": "CA", "NZX": "NZ",
    }
    return _MAP.get(exchange, "??")


# ---------------------------------------------------------------------------
# PHASE 2: Deterministic Filtering → Eligible Universe
# ---------------------------------------------------------------------------

def phase_2_eligible_universe(
    research: List[Dict[str, Any]],
) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """Apply deterministic hard filters to produce eligible, pending, excluded lists.

    Returns: (eligible, pending_contract, excluded)
    """
    log.info("=" * 60)
    log.info("PHASE 2: Deterministic Filtering → Eligible Universe")
    log.info("=" * 60)

    contract_symbols = _load_contract_symbols()
    bl_tickers, bl_exchanges = _load_blacklist()
    config = _load_pipeline_config()
    min_vol = config.get("eligible_min_volume", MIN_VOLUME_ELIGIBLE)
    min_price = config.get("eligible_min_price", MIN_PRICE_ELIGIBLE)

    eligible = []
    pending_contract = []
    excluded = []
    decisions = []

    for t in research:
        sym = t.get("symbol", "")
        exch = t.get("exchange", "")
        reason = ""

        # Hard filter: blacklisted ticker
        if sym in bl_tickers:
            reason = "blacklist_ticker"
        # Hard filter: blacklisted exchange
        elif exch in bl_exchanges:
            reason = "blacklist_exchange"
        # Hard filter: delisted
        elif t.get("delisted"):
            reason = "delisted"
        # Hard filter: volume too low (if known)
        elif t.get("avg_daily_volume", 0) > 0 and t["avg_daily_volume"] < min_vol:
            reason = f"volume_too_low ({t['avg_daily_volume']:,.0f} < {min_vol:,.0f})"
        # Hard filter: price too low (if known)
        elif t.get("last_price", 0) > 0 and t["last_price"] < min_price:
            reason = f"price_too_low ({t['last_price']:.2f} < {min_price})"

        if reason:
            t["exclusion_reason"] = reason
            t["tier"] = "EXCLUDED"
            excluded.append(t)
            decisions.append(_log_decision("2_eligible", sym, "EXCLUDED", reason))
            continue

        # Check contract availability
        # contracts.toml uses IBKR symbols (no yfinance suffixes):
        #   HKEX: "0700" not "0700.HK", TSE: "7203" not "7203.T",
        #   XETRA: "SAP" not "SAP.DE", ASX: "BHP" not "BHP.AX", etc.
        # Only LSE keeps the .L suffix in contracts.toml.
        has_contract = sym in contract_symbols
        if not has_contract and "." in sym and not sym.endswith(".L"):
            base_sym = sym.rsplit(".", 1)[0]
            has_contract = base_sym in contract_symbols
        t["has_contract"] = has_contract

        if has_contract:
            t["tier"] = "ELIGIBLE"
            eligible.append(t)
            decisions.append(_log_decision("2_eligible", sym, "ELIGIBLE"))
        else:
            t["tier"] = "PENDING_CONTRACT"
            pending_contract.append(t)
            decisions.append(_log_decision("2_eligible", sym, "PENDING_CONTRACT",
                                           "no contracts.toml entry"))

    log.info("PHASE 2 complete: %d eligible, %d pending_contract, %d excluded",
             len(eligible), len(pending_contract), len(excluded))

    # Save artifacts
    _atomic_write(ELIGIBLE_FILE, {
        "generated": datetime.now(timezone.utc).isoformat(),
        "total": len(eligible),
        "exchange_breakdown": _exchange_breakdown(eligible),
        "tickers": eligible,
    })
    _atomic_write(PENDING_FILE, {
        "generated": datetime.now(timezone.utc).isoformat(),
        "total": len(pending_contract),
        "exchange_breakdown": _exchange_breakdown(pending_contract),
        "tickers": pending_contract,
    })
    _atomic_write(EXCLUDED_FILE, {
        "generated": datetime.now(timezone.utc).isoformat(),
        "total": len(excluded),
        "reason_breakdown": _reason_breakdown(excluded),
        "tickers": excluded,
    })

    # Append decisions to log
    _append_decisions(decisions)

    log.info("Saved: %s, %s, %s", ELIGIBLE_FILE, PENDING_FILE, EXCLUDED_FILE)
    return eligible, pending_contract, excluded


def _reason_breakdown(excluded: List[Dict]) -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    for t in excluded:
        reason = t.get("exclusion_reason", "unknown")
        # Normalize reasons with variable data
        if reason.startswith("volume_too_low"):
            reason = "volume_too_low"
        elif reason.startswith("price_too_low"):
            reason = "price_too_low"
        counts[reason] += 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def _append_decisions(decisions: List[Dict]) -> None:
    """Append decision entries to the NDJSON log."""
    UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(DECISION_LOG, "a") as f:
            for d in decisions:
                f.write(json.dumps(d, default=str) + "\n")
    except OSError as e:
        log.warning("Failed to write decision log: %s", e)


# ---------------------------------------------------------------------------
# PHASE 3: Session-Aware Ranking + Gemini Triage
# ---------------------------------------------------------------------------

def phase_3_session_ranking(
    eligible: List[Dict[str, Any]],
    session: Optional[SessionDefinition] = None,
    market_data: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Rank eligible tickers for the current session.

    Steps:
    1. Detect current session
    2. Filter to session-eligible exchanges
    3. Try Gemini triage (if enabled)
    4. Fall back to deterministic scoring
    5. Merge rankings

    Returns: ranked list (highest rank first)
    """
    log.info("=" * 60)
    log.info("PHASE 3: Session-Aware Ranking + Gemini Triage")
    log.info("=" * 60)

    if session is None:
        session = detect_current_session()
    log.info("Current session: %s (%s-%s UTC)",
             session.name, session.start_utc, session.end_utc)

    # Filter to session-eligible exchanges (with normalization for NYSE→SMART etc.)
    session_tickers = [
        t for t in eligible
        if ticker_matches_session(t.get("exchange", ""), session)
    ]
    session_exchanges = set(session.eligible_exchanges) | set(session.shadow_exchanges)
    log.info("Session-eligible tickers: %d (from %d eligible, session exchanges=%s)",
             len(session_tickers), len(eligible), sorted(session_exchanges))

    # If session has no eligible exchanges (DARK window), use shadow
    if not session_tickers and session.shadow_exchanges:
        session_tickers = [
            t for t in eligible
            if any(ne in set(session.shadow_exchanges) for ne in normalize_exchange(t.get("exchange", "")))
        ]
        log.info("DARK window: using %d shadow tickers", len(session_tickers))

    # Try Gemini triage
    config = _load_pipeline_config()
    gemini_rankings = None
    gemini_source = "deterministic_fallback"

    if config.get("gemini_triage_enabled", True):
        try:
            from python_brain.ouroboros.gemini_scanner import triage_session_universe
            gemini_result = triage_session_universe(
                session=session,
                eligible_tickers=session_tickers,
                market_data=market_data,
                total_slots=config.get("streamed_slots", ENGINE_MAX_SLOTS),
            )
            if gemini_result and gemini_result.get("rankings"):
                gemini_rankings = gemini_result["rankings"]
                gemini_source = gemini_result.get("source", "gemini")
                log.info("Gemini triage returned %d rankings (source=%s)",
                         len(gemini_rankings), gemini_source)
        except ImportError:
            log.info("Gemini triage not available (triage_session_universe not found)")
        except Exception as e:
            log.warning("Gemini triage failed: %s — using deterministic fallback", e)

    # Deterministic scoring (always computed, used alone or merged with Gemini)
    scored = _deterministic_score(session_tickers, session)

    # Merge rankings if Gemini provided any
    if gemini_rankings and gemini_source != "deterministic_fallback":
        scored = _merge_rankings(scored, gemini_rankings)
        log.info("Merged Gemini + deterministic rankings")

    # Sort by final score
    scored.sort(key=lambda t: -t.get("final_score", 0))

    # Save rankings artifact
    _atomic_write(RANKINGS_FILE, {
        "generated": datetime.now(timezone.utc).isoformat(),
        "session": session.name,
        "source": gemini_source,
        "total_ranked": len(scored),
        "top_20": [
            {"symbol": t["symbol"], "exchange": t.get("exchange", ""),
             "final_score": round(t.get("final_score", 0), 4)}
            for t in scored[:20]
        ],
    })

    log.info("PHASE 3 complete: %d tickers ranked (source=%s)", len(scored), gemini_source)
    return scored


def _deterministic_score(
    tickers: List[Dict], session: SessionDefinition,
) -> List[Dict]:
    """Score tickers deterministically based on available metadata.

    Scoring formula:
    score = volume_score * 0.30 + research_score_norm * 0.25
            + leverage_score * 0.20 + exchange_session_fit * 0.15
            + momentum_proxy * 0.10
    """
    if not tickers:
        return []

    # Normalize research scores to 0-1
    max_rs = max(t.get("research_score", 0) for t in tickers) or 1.0

    slot_alloc = session.slot_allocation
    primary_exchanges = set(session.eligible_exchanges)

    for t in tickers:
        rs_norm = t.get("research_score", 0) / max_rs

        # Volume score (log scaled)
        vol = t.get("avg_daily_volume", 0)
        if vol and vol > 0:
            vol_score = min(1.0, math.log10(max(vol, 1)) / 8)  # 100M shares = 1.0
        else:
            vol_score = 0.3  # Unknown volume gets a middle score

        # Leverage score
        lev = t.get("leverage_factor", 1)
        if lev >= 5:
            lev_score = 1.0
        elif lev >= 3:
            lev_score = 0.8
        elif lev >= 2:
            lev_score = 0.6
        else:
            lev_score = 0.0

        # Exchange-session fit: primary exchanges get full score, shadow get 0.3
        # Uses normalization: NYSE/NASDAQ → SMART for session matching
        exch = t.get("exchange", "")
        normalized_exchanges = normalize_exchange(exch)
        is_primary = bool(set(normalized_exchanges) & primary_exchanges)
        is_shadow = bool(set(normalized_exchanges) & set(session.shadow_exchanges))

        if is_primary:
            session_fit = 1.0
            # Bonus for exchanges with more allocated slots
            for ne in normalized_exchanges:
                exch_slots = slot_alloc.get(ne, 0)
                if exch_slots > 30:
                    session_fit = 1.2  # Dominant exchange boost
                    break
        elif is_shadow:
            session_fit = 0.3
        else:
            session_fit = 0.1

        # Market cap proxy
        mcap = t.get("market_cap_usd", 0)
        if mcap and mcap > 0:
            mcap_score = min(1.0, math.log10(max(mcap, 1)) / 12)
        else:
            mcap_score = 0.2

        # Composite
        final = (
            vol_score * 0.30
            + rs_norm * 0.25
            + lev_score * 0.20
            + session_fit * 0.15
            + mcap_score * 0.10
        )
        t["final_score"] = round(final, 6)
        t["scoring_source"] = "deterministic"

    return tickers


def _merge_rankings(
    det_scored: List[Dict], gemini_rankings: List[Dict],
) -> List[Dict]:
    """Merge Gemini rankings with deterministic scores.

    Merge formula: 0.6 * gemini_rank_score + 0.4 * deterministic_score
    """
    # Build Gemini rank lookup: symbol → normalized rank score (1.0 = rank 1)
    gemini_by_sym = {}
    total_gemini = len(gemini_rankings) or 1
    for i, entry in enumerate(gemini_rankings):
        sym = entry.get("symbol", "")
        if sym:
            gemini_by_sym[sym] = 1.0 - (i / total_gemini)  # rank 1 = 1.0, last = 0.0

    for t in det_scored:
        sym = t.get("symbol", "")
        gemini_score = gemini_by_sym.get(sym, 0.0)
        det_score = t.get("final_score", 0.0)

        if gemini_score > 0:
            t["final_score"] = round(0.6 * gemini_score + 0.4 * det_score, 6)
            t["scoring_source"] = "gemini_merged"
        # else: keep deterministic score

    return det_scored


# ---------------------------------------------------------------------------
# PHASE 4: Deterministic Final Admission
# ---------------------------------------------------------------------------

def phase_4_final_admission(
    ranked: List[Dict[str, Any]],
    session: Optional[SessionDefinition] = None,
) -> Tuple[List[Dict], List[Dict]]:
    """Apply deterministic final admission: ACTIVE (streamed) + SHADOW.

    Hard gates:
    - ACTIVE: top N by score (N = engine max slots, default 100)
    - SHADOW: next M (default 200)
    - Open positions ALWAYS get an ACTIVE slot
    - Blacklisted tickers NEVER get ACTIVE
    """
    log.info("=" * 60)
    log.info("PHASE 4: Deterministic Final Admission")
    log.info("=" * 60)

    if session is None:
        session = detect_current_session()

    config = _load_pipeline_config()
    max_active = config.get("streamed_slots", ENGINE_MAX_SLOTS)
    max_shadow = config.get("shadow_slots", SHADOW_TARGET)
    bl_tickers, bl_exchanges = _load_blacklist()

    # Remove blacklisted from ranking
    ranked = [t for t in ranked if t.get("symbol", "") not in bl_tickers
              and t.get("exchange", "") not in bl_exchanges]

    # Respect leveraged_allowed per session
    if not session.leveraged_allowed:
        non_leveraged = [t for t in ranked if not t.get("leveraged")]
        leveraged_shadowed = [t for t in ranked if t.get("leveraged")]
        ranked = non_leveraged
        # Add leveraged to shadow pool later
    else:
        leveraged_shadowed = []

    # ACTIVE: top max_active
    active = ranked[:max_active]
    for t in active:
        t["tier"] = "ACTIVE"
        t["admission"] = "streamed"

    # SHADOW: next max_shadow
    shadow_pool = ranked[max_active:max_active + max_shadow]
    shadow_pool.extend(leveraged_shadowed[:max(0, max_shadow - len(shadow_pool))])
    for t in shadow_pool:
        t["tier"] = "SHADOW"
        t["admission"] = "shadow"

    log.info("PHASE 4 complete: %d ACTIVE, %d SHADOW (session=%s)",
             len(active), len(shadow_pool), session.name)

    # Save artifacts
    _atomic_write(ACTIVE_FILE, {
        "generated": datetime.now(timezone.utc).isoformat(),
        "session": session.name,
        "total": len(active),
        "exchange_breakdown": _exchange_breakdown(active),
        "tickers": active,
    })
    _atomic_write(SHADOW_FILE, {
        "generated": datetime.now(timezone.utc).isoformat(),
        "session": session.name,
        "total": len(shadow_pool),
        "exchange_breakdown": _exchange_breakdown(shadow_pool),
        "tickers": shadow_pool,
    })

    # Log decisions
    decisions = []
    for t in active:
        decisions.append(_log_decision("4_admission", t["symbol"], "ACTIVE",
                                        extra={"score": t.get("final_score", 0)}))
    for t in shadow_pool:
        decisions.append(_log_decision("4_admission", t["symbol"], "SHADOW",
                                        extra={"score": t.get("final_score", 0)}))
    _append_decisions(decisions)

    log.info("Saved: %s, %s", ACTIVE_FILE, SHADOW_FILE)
    return active, shadow_pool


# ---------------------------------------------------------------------------
# PHASE 5: Generate Artifacts + Engine Bridge
# ---------------------------------------------------------------------------

def phase_5_artifacts(
    active: List[Dict[str, Any]],
    shadow: List[Dict[str, Any]],
    session: Optional[SessionDefinition] = None,
    research_count: int = 0,
    eligible_count: int = 0,
    pending_count: int = 0,
    excluded_count: int = 0,
) -> None:
    """Write all output artifacts including the engine-compatible active_watchlist.json."""
    log.info("=" * 60)
    log.info("PHASE 5: Generate Artifacts + Engine Bridge")
    log.info("=" * 60)

    if session is None:
        session = detect_current_session()

    now = datetime.now(timezone.utc)

    # ── CRITICAL: Write engine-compatible active_watchlist.json ──
    # The Rust engine reads this file (engine.rs line 2613-2651).
    # Required fields per vanguard entry: symbol, exchange, currency
    # Optional: type, leveraged, leverage_factor, name, sector, composite_score
    vanguard = []
    for t in active:
        vanguard.append({
            "symbol": t.get("symbol", ""),
            "exchange": t.get("exchange", "SMART"),
            "currency": t.get("currency", "USD"),
            "type": t.get("instrument_class", t.get("type", "stock")),
            "leveraged": t.get("leveraged", False),
            "inverse": t.get("inverse", False),
            "leverage_factor": t.get("leverage_factor", 1),
            "name": t.get("name", ""),
            "sector": t.get("sector", "Unknown"),
            "composite_score": t.get("final_score", 0),
            "avg_daily_volume": t.get("avg_daily_volume", 0),
        })

    watchlist = {
        "generated": now.isoformat(),
        "pipeline_version": 1,
        "session": session.name,
        "total_scored": len(active) + len(shadow),
        "tickers": [t["symbol"] for t in vanguard],
        "tier_counts": {
            "active": len(active),
            "shadow": len(shadow),
        },
        "scoring_weights": {
            "note": "universe_pipeline deterministic + gemini_merged",
        },
        "vanguard": vanguard,
    }

    # Atomic write to the engine-read path
    _atomic_write(WATCHLIST_FILE, watchlist)
    log.info("Engine bridge: %s (%d vanguard tickers)", WATCHLIST_FILE, len(vanguard))

    # ── Session map state ──
    _atomic_write(SESSION_STATE_FILE, session_map_to_dict())

    # ── Summary ──
    summary = {
        "generated": now.isoformat(),
        "session": session.name,
        "session_utc_range": f"{session.start_utc}-{session.end_utc}",
        "research_universe_size": research_count,
        "eligible_universe_size": eligible_count,
        "pending_contract_size": pending_count,
        "excluded_size": excluded_count,
        "active_session_size": len(active),
        "shadow_size": len(shadow),
        "active_exchange_breakdown": _exchange_breakdown(active),
        "shadow_exchange_breakdown": _exchange_breakdown(shadow),
        "top_10_active": [
            {"symbol": t["symbol"], "exchange": t.get("exchange", ""),
             "score": round(t.get("final_score", 0), 4)}
            for t in active[:10]
        ],
    }
    _atomic_write(SUMMARY_FILE, summary)

    log.info("PHASE 5 complete. All artifacts written to %s", UNIVERSE_DIR)
    log.info("Summary: research=%d eligible=%d active=%d shadow=%d excluded=%d pending=%d",
             research_count, eligible_count, len(active), len(shadow),
             excluded_count, pending_count)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run_pipeline(
    dry_run: bool = False,
    rebuild_research: bool = False,
    phase_only: Optional[int] = None,
) -> int:
    """Execute the full 5-phase universe pipeline.

    Args:
        dry_run: If True, compute but don't write engine bridge (active_watchlist.json)
        rebuild_research: Force rebuild of the 5,000 research universe
        phase_only: Run only a specific phase (1-5)

    Returns: 0 on success, 1 on error
    """
    start = time.monotonic()
    now = datetime.now(timezone.utc)
    session = detect_current_session()

    log.info("=" * 70)
    log.info("AEGIS V2 Universe Pipeline")
    log.info("  Time: %s UTC", now.strftime("%Y-%m-%d %H:%M"))
    log.info("  Session: %s (%s-%s)", session.name, session.start_utc, session.end_utc)
    log.info("  Mode: %s", "dry-run" if dry_run else "live")
    if rebuild_research:
        log.info("  Force rebuild: research universe")
    if phase_only:
        log.info("  Phase only: %d", phase_only)
    log.info("=" * 70)

    UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        # Phase 1: Research Universe
        if phase_only is None or phase_only == 1:
            research = phase_1_research_universe(
                target=RESEARCH_TARGET,
                force_rebuild=rebuild_research,
            )
        else:
            # Load from cache
            research = _load_cached_research()

        if not research:
            log.error("PHASE 1 produced 0 research tickers — cannot continue")
            return 1

        # Phase 2: Eligible Universe
        if phase_only is None or phase_only == 2:
            eligible, pending, excluded = phase_2_eligible_universe(research)
        else:
            eligible = research  # Skip filtering
            pending = []
            excluded = []

        if not eligible:
            log.error("PHASE 2 produced 0 eligible tickers — cannot continue")
            return 1

        # Phase 3: Session Ranking
        if phase_only is None or phase_only == 3:
            ranked = phase_3_session_ranking(eligible, session=session)
        else:
            ranked = eligible

        if not ranked:
            log.warning("PHASE 3 produced 0 ranked tickers — using eligible as fallback")
            ranked = eligible

        # Phase 4: Final Admission
        if phase_only is None or phase_only == 4:
            active, shadow = phase_4_final_admission(ranked, session=session)
        else:
            active = ranked[:ENGINE_MAX_SLOTS]
            shadow = ranked[ENGINE_MAX_SLOTS:ENGINE_MAX_SLOTS + SHADOW_TARGET]

        # Phase 5: Artifacts
        if phase_only is None or phase_only == 5:
            if dry_run:
                log.info("DRY RUN: skipping engine bridge write")
                log.info("Would write %d active, %d shadow tickers", len(active), len(shadow))
            else:
                phase_5_artifacts(
                    active, shadow, session=session,
                    research_count=len(research),
                    eligible_count=len(eligible),
                    pending_count=len(pending),
                    excluded_count=len(excluded),
                )

    except Exception as e:
        log.error("Pipeline failed: %s", e, exc_info=True)
        return 1

    elapsed = time.monotonic() - start
    log.info("=" * 70)
    log.info("Universe Pipeline complete in %.1fs", elapsed)
    log.info("  Research: %d | Eligible: %d | Active: %d | Shadow: %d",
             len(research), len(eligible), len(active), len(shadow))
    log.info("  Pending contracts: %d | Excluded: %d",
             len(pending), len(excluded))
    log.info("=" * 70)

    return 0


def _load_cached_research() -> List[Dict]:
    """Load research universe from cached file."""
    if RESEARCH_FILE.exists():
        try:
            with open(RESEARCH_FILE) as f:
                data = json.load(f)
            return data.get("tickers", [])
        except Exception:
            pass
    return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="AEGIS V2 Universe Pipeline — 5-phase orchestrator")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute everything but don't write engine bridge")
    parser.add_argument("--rebuild-research", action="store_true",
                        help="Force rebuild of the 5,000 research universe")
    parser.add_argument("--phase", type=int, default=None, choices=[1, 2, 3, 4, 5],
                        help="Run only a specific phase")
    args = parser.parse_args()

    try:
        sys.exit(run_pipeline(
            dry_run=args.dry_run,
            rebuild_research=args.rebuild_research,
            phase_only=args.phase,
        ))
    except KeyboardInterrupt:
        log.info("Interrupted")
        sys.exit(130)


if __name__ == "__main__":
    main()
