"""Dynamic Universe Orchestrator — Broker-grounded autonomous universe management.

IBKR is the sole source of instrument truth. LLMs (Gemini, Claude) only score/veto
a broker-grounded universe. Deterministic filters admit. Atomic publish. Rust ack
with hash verification. Full lifecycle with sunset state.

Architecture: Split by timeframe (Syndicate-hardened)
    HEAVY (Cold) Path — --bootstrap, --session, --full
        IBKR API calls, reqContractDetails, Gemini LLM, Claude LLM, SIGHUP
        Runs: bootstrap (once), session (every 2h), full (daily 22:00 UTC)
    LIGHT (Hot) Path — --prep-next
        Cached JSON artifacts ONLY. Zero IBKR. Zero LLM. No SIGHUP.
        Runs: every 15 min during active sessions

Phase 9 is a PURE JSON ROUTER — reads pre-computed artifacts, sums scores,
applies quotas. Does NOT calculate ADF tests, Kalman filters, GARCH models,
or any quantitative math.

Product rules:
    - ETPs only on LSE/LSEETF/XETRA/EURONEXT
    - STK on all 9 exchanges
    - LSEETF leveraged/inverse: min 15 reserved live_100 slots

Usage:
    python3 -m python_brain.ouroboros.dynamic_universe --bootstrap --dry-run
    python3 -m python_brain.ouroboros.dynamic_universe --session
    python3 -m python_brain.ouroboros.dynamic_universe --prep-next
    python3 -m python_brain.ouroboros.dynamic_universe --full
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ── Graceful imports (try/except per module) ─────────────────────────────────

try:
    from python_brain.ouroboros.universe_lifecycle import LifecycleManager
except ImportError:
    LifecycleManager = None  # type: ignore

try:
    from python_brain.ouroboros.universe_publish import UniversePublisher
except ImportError:
    UniversePublisher = None  # type: ignore

try:
    from python_brain.ouroboros.universe_snapshot_schema import UniverseSnapshot
except ImportError:
    UniverseSnapshot = None  # type: ignore

try:
    from python_brain.ouroboros.universe_reason_codes import (
        AdmitReason, RejectReason, DegradedReason, RotationReason,
    )
except ImportError:
    # Fallback string constants
    class AdmitReason:  # type: ignore
        PASSED_ALL_GATES = "admit:passed_all_gates"
        EMERGENCY_BASELINE = "admit:emergency_baseline"
    class RejectReason:  # type: ignore
        INVALID_SEC_TYPE = "reject:invalid_sec_type"
        EXCHANGE_NOT_ALLOWED = "reject:exchange_not_allowed"
        PRICE_BELOW_MIN = "reject:price_below_min"
        ADV_BELOW_MIN = "reject:adv_below_min"
    class DegradedReason:  # type: ignore
        IBKR_UNAVAILABLE = "degraded:ibkr_unavailable"
        GEMINI_UNAVAILABLE = "degraded:gemini_unavailable"
    class RotationReason:  # type: ignore
        HIGH_RANK_ADMISSION = "rotation:high_rank_admission"
        RANK_DECAYED = "rotation:rank_decayed"

log = logging.getLogger("dynamic_universe")

# ═��═════════════════════════════════════════════════════════════════════════════
# Paths
# ══��═════════════��════════════════════════════════��═════════════════════════════
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
UNIVERSE_DIR = DATA_DIR / "universe"

CONTRACTS_FILE = CONFIG_DIR / "contracts.toml"
WATCHLIST_FILE = CONFIG_DIR / "active_watchlist.json"
UNIVERSE_TOML_FILE = CONFIG_DIR / "initial_universe.toml"
ROTATION_PLAN_FILE = CONFIG_DIR / "live_rotation_plan.json"
SYMBOL_MAP_FILE = UNIVERSE_DIR / "symbol_map.json"
SNAPSHOT_FILE = UNIVERSE_DIR / "dynamic_universe_snapshot.json"
EMERGENCY_BASELINE = CONFIG_DIR / "emergency_universe_baseline.toml"
OPEN_POSITIONS_FILE = DATA_DIR / "open_positions.json"
SYMBOL_MAP_OVERRIDES = CONFIG_DIR / "symbol_map_overrides.json"
RADAR_CACHE_FILE = DATA_DIR / "radar_cache.json"
RADAR_UNIVERSE_FILE = CONFIG_DIR / "radar_universe.toml"

# ══���════════════════════════════════════════════════════════════════════════════
# Constants — Quotas, Anti-Thrash, Boost Caps
# ════════════════════════════════════════════════════���══════════════════════════
# Sizes
MAX_SHORTLIST = 250
MAX_LIVE = 100
CORE_200_SIZE = 200
TACTICAL_50_SIZE = 50

# Quotas (precedence order defined in _apply_quotas)
MIN_LSEETF_LEVERAGED = 15
MAX_SINGLE_SECTOR = 20
MAX_SINGLE_EXCHANGE = 40
MIN_NON_US = 20
MAX_PAIR_SLOTS_PCT = 20  # max % of live_100 for pair co-residency
MAX_PAIR_COUNT = 10

# Anti-thrash
MIN_RESIDENCY_MINUTES = 30
MAX_CHURN_PER_15MIN = 8
MAX_SHORTLIST_CHURN_PER_2H = 25
MIN_SCORE_ADVANTAGE = 0.15
COOLDOWN_MINUTES = 30
COOLDOWN_OVERRIDE_ADVANTAGE = 0.30

# Boost stack
TOTAL_BOOST_CAP = 0.40

# 9 supported exchanges
ALLOWED_EXCHANGES = {
    "NYSE", "NASDAQ", "LSE", "LSEETF", "XETRA", "EURONEXT",
    "TSE", "HKEX", "SGX",
}
US_EXCHANGES = {"NYSE", "NASDAQ", "SMART"}
ETP_EXCHANGES = {"LSE", "LSEETF", "XETRA", "EURONEXT"}  # ETPs only on these

# IBKR exchange → yfinance suffix mapping table
IBKR_TO_YF_SUFFIX = {
    "LSE": ".L", "LSEETF": ".L",
    "TSE": ".T",
    "HKEX": ".HK", "SEHK": ".HK",
    "KRX": ".KS",
    "XETRA": ".DE",
    "EURONEXT": ".PA",  # default; .AS for Amsterdam
    "SGX": ".SI",
    "ASX": ".AX",
    # US exchanges: no suffix
}

# ═════════════════════════════════════════════���═════════════════════════════════
# Boost Signal Definitions
# ═══════���═══════════════════════════════════════════════════════════════════════
BOOST_SIGNALS = [
    {"name": "sector",      "file": "scanner_results.json",     "max": 0.12, "freshness_s": 1800, "env": "BOOST_SECTOR"},
    {"name": "event",       "file": "event_calendar.json",      "max": 0.12, "freshness_s": 86400, "env": "BOOST_EVENT"},
    {"name": "insider",     "file": "insider_signals.json",     "max": 0.08, "freshness_s": 86400, "env": "BOOST_INSIDER"},
    {"name": "pairs",       "file": "pairs_cointegration.json", "max": 0.15, "freshness_s": 14400, "env": "BOOST_PAIRS"},
    {"name": "overnight",   "file": "overnight_gap.json",       "max": 0.07, "freshness_s": 3600, "env": "BOOST_OVERNIGHT"},
    {"name": "winner",      "file": "winner_persistence.json",  "max": 0.10, "freshness_s": 86400, "env": "BOOST_WINNER"},
    {"name": "vix",         "file": "vix_regime.json",          "max": 0.12, "freshness_s": 1800, "env": "BOOST_VIX"},
    {"name": "lead_lag",    "file": "lead_lag_pairs.json",      "max": 0.10, "freshness_s": 14400, "env": "BOOST_LEADLAG"},
    {"name": "social",      "file": "social_sentiment.json",    "max": 0.05, "freshness_s": 1800, "env": "BOOST_SOCIAL"},
    {"name": "thompson",    "file": "thompson_top_k.json",      "max": 0.10, "freshness_s": 86400, "env": "BOOST_THOMPSON"},
    # ── Session 35: Radar Daemon Boost Signals ──
    {"name": "radar_opra",      "file": "radar_opra_signals.json", "max": 0.15, "freshness_s": 300, "env": "BOOST_RADAR_OPRA"},
    {"name": "radar_iv_regime", "file": "radar_opra_signals.json", "max": 0.08, "freshness_s": 300, "env": "BOOST_RADAR_IV"},
    {"name": "radar_momentum",  "file": "radar_opra_signals.json", "max": 0.10, "freshness_s": 300, "env": "BOOST_RADAR_MOMENTUM"},
    {"name": "radar_liquidity", "file": "radar_opra_signals.json", "max": 0.20, "freshness_s": 300, "env": "BOOST_RADAR_LIQUIDITY"},
]


def _toml_escape(value: str) -> str:
    """Escape a string for safe TOML interpolation. Prevents injection via ticker symbols."""
    # Remove characters that could break TOML string syntax
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "").replace("\r", "")


# ═���═════════════════════���════════════════════════════════��══════════════════════
# Symbol Mapping
# ═══════════════════════════════════════════════���═══════════════════════════════

def _build_symbol_map(
    instruments: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Build IBKR ↔ yfinance bidirectional map.

    Priority: manual override > deterministic table > heuristic.
    Returns dict keyed by IBKR symbol with yf_symbol, confidence, mapping_status.
    """
    # Load manual overrides
    overrides: Dict[str, str] = {}
    if SYMBOL_MAP_OVERRIDES.exists():
        try:
            with open(SYMBOL_MAP_OVERRIDES) as f:
                overrides = json.load(f)
        except Exception:
            pass

    symbol_map: Dict[str, Dict[str, Any]] = {}
    for inst in instruments:
        sym = inst.get("symbol", "")
        exch = inst.get("exchange", "")
        if not sym:
            continue

        # Priority 1: manual override
        if sym in overrides:
            symbol_map[sym] = {
                "ibkr_symbol": sym,
                "yf_symbol": overrides[sym],
                "exchange": exch,
                "confidence": 1.0,
                "mapping_status": "override",
            }
            continue

        # Priority 2: deterministic table
        suffix = IBKR_TO_YF_SUFFIX.get(exch, "")
        if suffix:
            # LSE/LSEETF: keep .L suffix
            if exch in ("LSE", "LSEETF"):
                yf_sym = sym if sym.endswith(".L") else sym + ".L"
            else:
                yf_sym = sym + suffix
            symbol_map[sym] = {
                "ibkr_symbol": sym,
                "yf_symbol": yf_sym,
                "exchange": exch,
                "confidence": 1.0,
                "mapping_status": "table_hit",
            }
            continue

        # Priority 3: US exchanges (no suffix needed)
        if exch in US_EXCHANGES or exch in ("NYSE", "NASDAQ"):
            symbol_map[sym] = {
                "ibkr_symbol": sym,
                "yf_symbol": sym,
                "exchange": exch,
                "confidence": 1.0,
                "mapping_status": "table_hit",
            }
            continue

        # Priority 4: heuristic (low confidence)
        symbol_map[sym] = {
            "ibkr_symbol": sym,
            "yf_symbol": sym,
            "exchange": exch,
            "confidence": 0.7,
            "mapping_status": "heuristic",
        }

    return symbol_map


def _save_symbol_map(symbol_map: Dict[str, Dict[str, Any]]) -> bool:
    """Persist symbol map to disk."""
    UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        data = {
            "updated": datetime.now(timezone.utc).isoformat(),
            "count": len(symbol_map),
            "mappings": symbol_map,
        }
        with open(SYMBOL_MAP_FILE, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        log.warning("Failed to save symbol map: %s", e)
        return False


def _load_symbol_map() -> Dict[str, Dict[str, Any]]:
    """Load cached symbol map from disk."""
    if not SYMBOL_MAP_FILE.exists():
        return {}
    try:
        with open(SYMBOL_MAP_FILE) as f:
            data = json.load(f)
        return data.get("mappings", {})
    except Exception:
        return {}


# ═���══════════════════════════════════════════════════════════���══════════════════
# Deterministic Admissibility
# ═══════════════════════════════════════════════════════════════════════════════

def _apply_admissibility(
    candidates: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Apply hard deterministic gates. Returns (admitted, rejected) lists.

    No LLM involvement. Pure data filters.
    """
    admitted = []
    rejected = []

    for c in candidates:
        sym = c.get("symbol", "")
        exch = c.get("exchange", "")
        sec_type = c.get("sec_type", "STK")
        currency = c.get("currency", "")
        price = c.get("price", 0)
        adv = c.get("avg_daily_volume", 0)
        name = (c.get("name") or c.get("long_name") or "").upper()

        reason = None

        # Gate 1: Exchange must be in allowed set
        if exch and exch not in ALLOWED_EXCHANGES and exch != "SMART":
            reason = RejectReason.EXCHANGE_NOT_ALLOWED

        # Gate 2: ETP product type only on European exchanges
        elif sec_type not in ("STK", "ETF", "ETP"):
            reason = RejectReason.INVALID_SEC_TYPE

        # Gate 3: Price floor ($0.50 min)
        elif price > 0 and price < 0.50:
            reason = RejectReason.PRICE_BELOW_MIN

        # Gate 4: Volume floor (50K ADV min)
        elif adv > 0 and adv < 50000:
            reason = RejectReason.ADV_BELOW_MIN

        # Gate 5: Rights, warrants, preferred shares
        elif any(kw in name for kw in ("RIGHT", "WARRANT", "WTS", "PREFERRED", " PFD")):
            reason = "reject:product_structure"

        # Gate 6: Halted / delisted
        elif c.get("halted") or c.get("delisted"):
            reason = "reject:halted_or_delisted"

        if reason:
            c["reject_reason"] = reason
            rejected.append(c)
        else:
            c["admit_reason"] = AdmitReason.PASSED_ALL_GATES
            admitted.append(c)

    return admitted, rejected


# ═══════════════════════════��═══════════════════════════════════════════════════
# Boost Signal Loading (Pure JSON Router)
# ════════════════════════════════════════════��══════════════════════════════════

def _load_boost_signals() -> Dict[str, Dict[str, Any]]:
    """Load all boost signal artifacts. Per-signal try/except.

    Returns dict keyed by signal name → {symbols: {sym: score}, fresh: bool}.
    Missing artifact = 0 boost, log warning, continue.
    """
    now = time.time()
    boosts: Dict[str, Dict[str, Any]] = {}

    for sig in BOOST_SIGNALS:
        name = sig["name"]
        env_var = sig["env"]
        max_boost = sig["max"]
        freshness_s = sig["freshness_s"]
        filepath = DATA_DIR / sig["file"]

        # Check disable env
        if os.environ.get(env_var, "1") == "0":
            boosts[name] = {"symbols": {}, "fresh": False, "disabled": True, "max": max_boost}
            continue

        try:
            if not filepath.exists():
                log.debug("Boost artifact missing: %s", filepath)
                boosts[name] = {"symbols": {}, "fresh": False, "max": max_boost}
                continue

            mtime = filepath.stat().st_mtime
            fresh = (now - mtime) < freshness_s

            with open(filepath) as f:
                data = json.load(f)

            # Extract symbol→score mapping from artifact
            # Artifacts are expected to have a "scores" or "symbols" dict
            # or a list of {symbol, score} entries
            sym_scores: Dict[str, float] = {}
            if isinstance(data, dict):
                # Try common patterns
                if "scores" in data:
                    # Session 35: handle both positive boosts and negative penalties
                    for k, v in data["scores"].items():
                        fv = float(v)
                        if fv >= 0:
                            sym_scores[k] = min(fv, max_boost)
                        else:
                            sym_scores[k] = max(fv, -max_boost)  # cap penalty magnitude
                elif "symbols" in data:
                    for entry in data["symbols"]:
                        if isinstance(entry, dict) and "symbol" in entry:
                            sym_scores[entry["symbol"]] = min(float(entry.get("score", 0)), max_boost)
                elif "tickers" in data:
                    for entry in data["tickers"]:
                        if isinstance(entry, dict) and "symbol" in entry:
                            sym_scores[entry["symbol"]] = min(float(entry.get("score", 0)), max_boost)
                # Sector rotation: scanner_results.json has different format
                elif "scanners" in data:
                    for scanner_data in data["scanners"].values():
                        for result in scanner_data.get("results", []):
                            sym = result.get("symbol", "")
                            if sym:
                                # Volume-based boost normalized to max
                                sym_scores[sym] = max_boost * 0.8  # strong scanner signal

            # Social sentiment extra gating
            if name == "social" and fresh:
                gated = {}
                for sym, score in sym_scores.items():
                    # Only boost if source_count >= 3 (from artifact metadata)
                    sources = data.get("source_counts", {}).get(sym, 0)
                    if sources >= 3:
                        gated[sym] = score
                sym_scores = gated

            boosts[name] = {"symbols": sym_scores, "fresh": fresh, "max": max_boost}

        except Exception as e:
            log.warning("Boost signal '%s' load failed: %s", name, e)
            boosts[name] = {"symbols": {}, "fresh": False, "max": max_boost}

    return boosts


def _compute_boost_stack(
    symbol: str,
    boosts: Dict[str, Dict[str, Any]],
) -> Tuple[float, Dict[str, float]]:
    """Compute total boost for a symbol. Cap at TOTAL_BOOST_CAP.

    Returns (capped_total, components_dict).
    """
    components: Dict[str, float] = {}
    total = 0.0

    for name, sig_data in boosts.items():
        if sig_data.get("disabled"):
            continue
        score = sig_data["symbols"].get(symbol, 0.0)
        if score > 0:
            capped = min(score, sig_data["max"])
            components[name] = capped
            total += capped
        elif score < 0:
            # Session 35: Handle negative penalty signals (e.g., radar_liquidity)
            capped = max(score, -sig_data["max"])  # cap penalty magnitude
            components[name] = capped
            total += capped

    if total > TOTAL_BOOST_CAP:
        # Proportional scaling to cap
        scale = TOTAL_BOOST_CAP / total
        components = {k: v * scale for k, v in components.items()}
        total = TOTAL_BOOST_CAP

    return total, components


# ═══════════════════════════════════════════════════════════════════════════════
# Rotation Planning — Quota Precedence Ladder
# ═══════════════════════════════════════════════════════════════════════════════

def _load_open_positions() -> Set[str]:
    """Load open position symbols from Rust WAL / open_positions.json."""
    if not OPEN_POSITIONS_FILE.exists():
        return set()
    try:
        with open(OPEN_POSITIONS_FILE) as f:
            data = json.load(f)
        # Accept list of symbols or dict with positions
        if isinstance(data, list):
            return {p.get("symbol", "") for p in data if p.get("quantity", 0) != 0}
        elif isinstance(data, dict):
            positions = data.get("positions", [])
            return {p.get("symbol", "") for p in positions if p.get("quantity", 0) != 0}
    except Exception:
        return set()
    return set()


def _load_pairs_artifact() -> Dict[str, Dict[str, Any]]:
    """Load pairs cointegration artifact for co-residency enforcement."""
    filepath = DATA_DIR / "pairs_cointegration.json"
    if not filepath.exists():
        return {}
    try:
        with open(filepath) as f:
            data = json.load(f)
        pairs = {}
        for pair in data.get("pairs", []):
            leg_a = pair.get("leg_a", "")
            leg_b = pair.get("leg_b", "")
            if leg_a and leg_b:
                pairs[f"{leg_a}:{leg_b}"] = pair
        return pairs
    except Exception:
        return {}


def _load_previous_rotation() -> Dict[str, Any]:
    """Load previous rotation plan for anti-thrash checks."""
    if not ROTATION_PLAN_FILE.exists():
        return {}
    try:
        with open(ROTATION_PLAN_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _apply_rotation_planning(
    scored: List[Dict[str, Any]],
    lifecycle_mgr: Any,
    boosts: Dict[str, Dict[str, Any]],
    mode: str,
) -> Dict[str, Any]:
    """Phase 9: Pure JSON router. Reads pre-computed artifacts, applies quotas.

    Quota precedence ladder (deterministic, in order):
        1. Admissibility (already filtered)
        2. Sunset protection (cannot evict while position > 0)
        3. Active pair co-residency
        4. Reserved LSEETF leveraged slots (min 15)
        5. Max single exchange (40)
        6. Max single sector (20)
        7. Min non-US (20)
        8. Anti-thrash rules
        9. Score ordering (final_score descending)

    Returns rotation plan dict with score decomposition per symbol.
    """
    open_positions = _load_open_positions()
    pairs = _load_pairs_artifact()
    prev_plan = _load_previous_rotation()
    cached_symbol_map = _load_symbol_map()
    prev_live_syms = {t.get("symbol") for t in prev_plan.get("live_100", [])}
    prev_live_times = {
        t.get("symbol"): t.get("admitted_at", "")
        for t in prev_plan.get("live_100", [])
    }
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    # ── Step 1: Compute final scores with boost ──────────────────────────
    for c in scored:
        base_score = c.get("gemini_score", c.get("composite_score", 0.5))
        boost_total, boost_components = _compute_boost_stack(c["symbol"], boosts)
        c["base_score"] = base_score
        c["boost_components"] = boost_components
        c["raw_boost"] = sum(boost_components.values())
        c["capped_boost"] = boost_total
        c["final_score"] = base_score + boost_total
        c["quota_adjustments"] = []
        c["final_rank_reason"] = ""

    # Sort by final_score descending
    scored.sort(key=lambda x: x["final_score"], reverse=True)

    # ── Step 2: Sunset protection ────────────────────────────────────────
    # Sunset symbols with open positions MUST stay in live_100
    sunset_protected: List[Dict[str, Any]] = []
    if lifecycle_mgr:
        sunset_syms = lifecycle_mgr.get_all_in_state("sunset")
        for sym in sunset_syms:
            if sym in open_positions:
                # Find in scored list or create placeholder
                entry = next((c for c in scored if c["symbol"] == sym), None)
                if entry is None:
                    entry = {"symbol": sym, "base_score": 0, "boost_components": {},
                             "raw_boost": 0, "capped_boost": 0, "final_score": 0,
                             "exchange": "", "sector": "", "quota_adjustments": ["sunset_protected"]}
                entry["final_rank_reason"] = RotationReason.STRATEGY_PROTECTED
                entry["quota_adjustments"].append("sunset_protected")
                sunset_protected.append(entry)
            else:
                # Position is flat — transition sunset → inactive
                lifecycle_mgr.transition(sym, "inactive", "position_confirmed_flat")

    sunset_count = len(sunset_protected)
    available_slots = MAX_LIVE - sunset_count

    # ── Step 3: Pair co-residency ────────────────────────────────────────
    pair_locked: Set[str] = set()
    max_pair_slots = int(MAX_LIVE * MAX_PAIR_SLOTS_PCT / 100)
    pair_count = 0

    for pair_key, pair_data in pairs.items():
        if pair_count >= MAX_PAIR_COUNT:
            break
        if len(pair_locked) >= max_pair_slots:
            break

        leg_a = pair_data.get("leg_a", "")
        leg_b = pair_data.get("leg_b", "")
        adf_p = pair_data.get("adf_pvalue", 1.0)
        kalman_fresh = pair_data.get("kalman_updated_this_session", False)

        if adf_p < 0.10 and kalman_fresh:
            # Both legs must be in scored list
            a_in = any(c["symbol"] == leg_a for c in scored)
            b_in = any(c["symbol"] == leg_b for c in scored)
            if a_in and b_in:
                pair_locked.add(leg_a)
                pair_locked.add(leg_b)
                pair_count += 1

    # ── Step 4: Reserved LSEETF leveraged slots ──────────────────────────
    lseetf_lev = [c for c in scored if
                  c.get("exchange") in ("LSE", "LSEETF") and
                  (c.get("leverage", 1) > 1 or c.get("leveraged"))]
    lseetf_reserved_syms: Set[str] = set()
    for c in lseetf_lev[:MIN_LSEETF_LEVERAGED]:
        lseetf_reserved_syms.add(c["symbol"])
        c["quota_adjustments"].append("lseetf_reserved")

    # ── Step 5-7: Build live_100 with exchange/sector/non-US quotas ──────
    live_100: List[Dict[str, Any]] = list(sunset_protected)
    live_syms: Set[str] = {c["symbol"] for c in live_100}
    exch_counts: Dict[str, int] = {}
    sector_counts: Dict[str, int] = {}
    non_us_count = 0

    # Count sunset contributions
    for c in sunset_protected:
        e = c.get("exchange", "UNKNOWN")
        s = c.get("sector", "Unknown")
        exch_counts[e] = exch_counts.get(e, 0) + 1
        sector_counts[s] = sector_counts.get(s, 0) + 1
        if e not in US_EXCHANGES:
            non_us_count += 1

    # Add pair-locked symbols first (they need both legs)
    for c in scored:
        if len(live_100) >= MAX_LIVE:
            break
        sym = c["symbol"]
        if sym in live_syms:
            continue
        if sym in pair_locked:
            c["quota_adjustments"].append("pair_locked")
            c["final_rank_reason"] = c["final_rank_reason"] or RotationReason.HIGH_RANK_ADMISSION
            live_100.append(c)
            live_syms.add(sym)
            e = c.get("exchange", "UNKNOWN")
            s = c.get("sector", "Unknown")
            exch_counts[e] = exch_counts.get(e, 0) + 1
            sector_counts[s] = sector_counts.get(s, 0) + 1
            if e not in US_EXCHANGES:
                non_us_count += 1

    # Add LSEETF reserved slots
    for c in scored:
        if len(live_100) >= MAX_LIVE:
            break
        sym = c["symbol"]
        if sym in live_syms:
            continue
        if sym in lseetf_reserved_syms:
            c["final_rank_reason"] = c["final_rank_reason"] or RotationReason.HIGH_RANK_ADMISSION
            live_100.append(c)
            live_syms.add(sym)
            e = c.get("exchange", "UNKNOWN")
            s = c.get("sector", "Unknown")
            exch_counts[e] = exch_counts.get(e, 0) + 1
            sector_counts[s] = sector_counts.get(s, 0) + 1
            if e not in US_EXCHANGES:
                non_us_count += 1

    # Fill remaining by score, applying quotas
    churn_count = 0
    for c in scored:
        if len(live_100) >= MAX_LIVE:
            break
        sym = c["symbol"]
        if sym in live_syms:
            continue

        e = c.get("exchange", "UNKNOWN")
        s = c.get("sector", "Unknown")

        # Quota 5: max single exchange
        if exch_counts.get(e, 0) >= MAX_SINGLE_EXCHANGE:
            c["quota_adjustments"].append("exchange_capped")
            continue

        # Quota 6: max single sector
        if sector_counts.get(s, 0) >= MAX_SINGLE_SECTOR:
            c["quota_adjustments"].append("sector_capped")
            continue

        # Quota 8: anti-thrash — residency, cooldown, churn cap, score advantage
        if sym not in prev_live_syms:
            # New addition — check churn limit
            if churn_count >= MAX_CHURN_PER_15MIN:
                c["quota_adjustments"].append("churn_capped")
                continue

            # Check cooldown: recently evicted symbols cannot re-enter
            evicted_at = prev_plan.get("evicted_at", {}).get(sym, "")
            if evicted_at:
                try:
                    evict_time = datetime.fromisoformat(evicted_at.replace("Z", "+00:00"))
                    if hasattr(evict_time, "tzinfo") and evict_time.tzinfo is None:
                        evict_time = evict_time.replace(tzinfo=timezone.utc)
                    minutes_since = (now - evict_time).total_seconds() / 60
                    if minutes_since < COOLDOWN_MINUTES:
                        # Allow override if score advantage is very large
                        if c["final_score"] - 0.5 < COOLDOWN_OVERRIDE_ADVANTAGE:
                            c["quota_adjustments"].append("cooldown_active")
                            continue
                except (ValueError, TypeError):
                    pass

            # Check min score advantage over lowest evictable live member
            evictable = [x for x in live_100
                         if x["symbol"] not in pair_locked
                         and "sunset_protected" not in x.get("quota_adjustments", [])]
            # Enforce min residency: only consider evicting members past MIN_RESIDENCY
            evictable_scores = []
            for x in evictable:
                admitted = x.get("admitted_at", "")
                if admitted:
                    try:
                        adm_time = datetime.fromisoformat(admitted.replace("Z", "+00:00"))
                        if hasattr(adm_time, "tzinfo") and adm_time.tzinfo is None:
                            adm_time = adm_time.replace(tzinfo=timezone.utc)
                        if (now - adm_time).total_seconds() / 60 < MIN_RESIDENCY_MINUTES:
                            continue  # Protected by min residency
                    except (ValueError, TypeError):
                        pass
                evictable_scores.append(x.get("final_score", 0))

            if evictable_scores:
                lowest_score = min(evictable_scores)
                if c["final_score"] - lowest_score < MIN_SCORE_ADVANTAGE:
                    c["quota_adjustments"].append("insufficient_advantage")
                    continue

            churn_count += 1

        # Low-confidence symbol mapping: exclude from live_100
        mapping = cached_symbol_map.get(sym, {})
        if mapping.get("confidence", 1.0) < 0.8:
            c["quota_adjustments"].append("low_confidence_mapping")
            continue

        # Session 35: Airlock verification for radar-sourced new promotions
        if c.get("_source") == "radar" and sym not in prev_live_syms:
            if not self._airlock_verify(c):
                c["quota_adjustments"].append("airlock_rejected")
                continue

        c["final_rank_reason"] = c["final_rank_reason"] or RotationReason.HIGH_RANK_ADMISSION
        live_100.append(c)
        live_syms.add(sym)
        exch_counts[e] = exch_counts.get(e, 0) + 1
        sector_counts[s] = sector_counts.get(s, 0) + 1
        if e not in US_EXCHANGES:
            non_us_count += 1

    # Quota 7: min non-US — backfill if under quota
    if non_us_count < MIN_NON_US and len(live_100) >= MAX_LIVE:
        non_us_candidates = [c for c in scored
                             if c["symbol"] not in live_syms
                             and c.get("exchange", "") not in US_EXCHANGES]
        deficit = MIN_NON_US - non_us_count
        # Swap out lowest-scored US names for non-US
        us_live = sorted(
            [c for c in live_100 if c.get("exchange", "") in US_EXCHANGES
             and "sunset_protected" not in c.get("quota_adjustments", [])
             and c["symbol"] not in pair_locked],
            key=lambda x: x.get("final_score", 0),
        )
        for _ in range(min(deficit, len(non_us_candidates), len(us_live))):
            if not us_live or not non_us_candidates:
                break
            evicted = us_live.pop(0)
            replacement = non_us_candidates.pop(0)
            evicted["quota_adjustments"].append("non_us_swap_out")
            replacement["quota_adjustments"].append("non_us_swap_in")
            replacement["final_rank_reason"] = "rotation:non_us_quota"
            live_100.remove(evicted)
            live_syms.discard(evicted["symbol"])
            live_100.append(replacement)
            live_syms.add(replacement["symbol"])

    # ── Build shortlist_250 ──────────────────────────────────────────────
    shortlist: List[Dict[str, Any]] = list(live_100)
    short_syms = set(live_syms)
    for c in scored:
        if len(shortlist) >= MAX_SHORTLIST:
            break
        if c["symbol"] not in short_syms:
            shortlist.append(c)
            short_syms.add(c["symbol"])

    # ── Build rotation plan with score decomposition ─────────────────────
    def _entry(c: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "symbol": c.get("symbol", ""),
            "con_id": c.get("con_id", 0),
            "exchange": c.get("exchange", ""),
            "sector": c.get("sector", "Unknown"),
            "base_score": round(c.get("base_score", 0), 4),
            "boost_components": {k: round(v, 4) for k, v in c.get("boost_components", {}).items()},
            "raw_boost": round(c.get("raw_boost", 0), 4),
            "capped_boost": round(c.get("capped_boost", 0), 4),
            "final_score": round(c.get("final_score", 0), 4),
            "quota_adjustments": c.get("quota_adjustments", []),
            "final_rank_reason": c.get("final_rank_reason", ""),
            "admitted_at": c.get("admitted_at") or prev_live_times.get(c.get("symbol", ""), now_iso),
        }

    plan = {
        "generated": now_iso,
        "mode": mode,
        "live_100": [_entry(c) for c in live_100],
        "shortlist_250": [_entry(c) for c in shortlist],
        "metrics": {
            "live_count": len(live_100),
            "shortlist_count": len(shortlist),
            "sunset_protected": sunset_count,
            "pair_locked": len(pair_locked),
            "lseetf_reserved": len(lseetf_reserved_syms),
            "churn_this_run": churn_count,
            "exchange_distribution": dict(exch_counts),
            "non_us_count": non_us_count,
        },
    }

    return plan


# ═══════════════════════════���═══════════════════════════════════════════════════
# Orchestrator Class
# ════════════════���══════════════════════════════════════════════════════════════

class DynamicUniverseOrchestrator:
    """Orchestrates the dynamic universe pipeline.

    Heavy Path: --bootstrap, --session, --full (IBKR + LLM + SIGHUP)
    Light Path: --prep-next (cached JSON only, no API calls, no SIGHUP)
    """

    HEAVY_MODES = {"bootstrap", "session", "full"}
    LIGHT_MODES = {"prep_next"}

    def __init__(self):
        self._lifecycle: Optional[Any] = None
        self._publisher: Optional[Any] = None
        self._degraded_reasons: List[str] = []

    def run(self, mode: str, dry_run: bool = False) -> Dict[str, Any]:
        """Execute the universe pipeline.

        Args:
            mode: one of bootstrap, session, prep_next, full
            dry_run: if True, skip publish and SIGHUP

        Returns snapshot dict.
        """
        run_id = f"{mode}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"
        start = time.monotonic()
        log.info("=" * 70)
        log.info("Dynamic Universe — mode=%s dry_run=%s run_id=%s", mode, dry_run, run_id)
        log.info("=" * 70)

        # Init lifecycle
        if LifecycleManager:
            self._lifecycle = LifecycleManager()
            self._lifecycle.load()
        else:
            log.warning("LifecycleManager unavailable — lifecycle tracking disabled")

        # Init publisher
        if UniversePublisher:
            self._publisher = UniversePublisher()

        if mode in self.HEAVY_MODES:
            result = self._run_heavy(mode, dry_run, run_id)
        elif mode in self.LIGHT_MODES:
            result = self._run_light(dry_run, run_id)
        else:
            log.error("Unknown mode: %s", mode)
            return {"error": f"Unknown mode: {mode}"}

        elapsed = time.monotonic() - start
        result["duration_seconds"] = round(elapsed, 2)
        result["run_id"] = run_id

        # Save snapshot
        self._save_snapshot(result)

        log.info("Dynamic Universe complete in %.1fs — mode=%s", elapsed, mode)
        return result

    # ═══════════════════════════════════════════════════════════════════════
    # HEAVY (Cold) Path
    # ═══════════════════════════════════════════════════════════════════════

    def _run_heavy(self, mode: str, dry_run: bool, run_id: str) -> Dict[str, Any]:
        """Heavy path: IBKR discovery → admissibility → resolution → Gemini → Claude → rotation → publish."""
        snapshot: Dict[str, Any] = {
            "run_mode": mode,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "exchanges_scanned": [],
            "exchanges_failed": [],
            "degraded_reasons": [],
        }
        self._degraded_reasons = []

        # ── Phase 1: IBKR Discovery ──────────────────────────────────────
        log.info("Phase 1: IBKR Discovery")
        discovered = self._phase_discovery(mode)
        snapshot["raw_discoveries"] = len(discovered)
        if not discovered:
            log.warning("Phase 1: Zero discoveries — attempting emergency baseline")
            return self._emergency_baseline(snapshot, dry_run, run_id)

        # ── Phase 2: Normalize + Dedupe ──────────────────────────────────
        log.info("Phase 2: Normalize + Dedupe (%d raw)", len(discovered))
        normalized = self._phase_normalize(discovered)
        log.info("Phase 2: %d after dedupe", len(normalized))

        # ── Phase 3: Deterministic Admissibility ──���──────────────────────
        log.info("Phase 3: Admissibility gates")
        admitted, rejected = _apply_admissibility(normalized)
        snapshot["admissible_count"] = len(admitted)
        snapshot["rejected_count"] = len(rejected)
        reject_reasons: Dict[str, int] = {}
        for r in rejected:
            rc = r.get("reject_reason", "unknown")
            reject_reasons[rc] = reject_reasons.get(rc, 0) + 1
        snapshot["reject_reasons"] = reject_reasons
        log.info("Phase 3: %d admitted, %d rejected", len(admitted), len(rejected))

        # ── Phase 4: Contract Resolution ───────────────────────────���─────
        log.info("Phase 4: Contract Resolution (IBKR reqContractDetails)")
        resolved = self._phase_resolution(admitted)
        snapshot["resolved_count"] = len([c for c in resolved if c.get("con_id", 0) > 0])
        snapshot["resolution_failures"] = len([c for c in resolved if c.get("con_id", 0) == 0])
        log.info("Phase 4: %d resolved with con_id", snapshot["resolved_count"])

        # ── Phase 5: Symbol Mapping ──────────────────────────────────────
        log.info("Phase 5: Symbol Mapping (IBKR ↔ yfinance)")
        symbol_map = _build_symbol_map(resolved)
        _save_symbol_map(symbol_map)
        log.info("Phase 5: %d mappings built", len(symbol_map))

        # ── Phase 6: Gemini Ranking ────────────────────────────��─────────
        log.info("Phase 6: Gemini Ranking")
        ranked = self._phase_gemini_ranking(resolved)
        snapshot["gemini_degraded"] = any("gemini" in r for r in self._degraded_reasons)
        log.info("Phase 6: %d ranked", len(ranked))

        # ── Phase 7: Claude Vetting (optional) ────��──────────────────────
        if os.environ.get("UNIVERSE_CLAUDE_VETO", "0") == "1":
            log.info("Phase 7: Claude Vetting (enabled)")
            ranked = self._phase_claude_vetting(ranked)
        else:
            log.info("Phase 7: Claude Vetting (disabled)")
        snapshot["claude_degraded"] = any("claude" in r for r in self._degraded_reasons)

        # ── Phase 8: Lifecycle pre-update (discovery/resolution tracking) ──
        log.info("Phase 8: Lifecycle pre-update (discovery + resolution tracking)")
        # NOTE: Single update_from_run after Phase 9 to avoid mass evictions.
        # Here we only track newly discovered/resolved instruments.
        if self._lifecycle:
            for d in discovered:
                sym = d.get("symbol", "")
                if sym and self._lifecycle.get_state(sym) is None:
                    self._lifecycle.transition(sym, "discovered", "run_discovery",
                                               con_id=d.get("con_id", 0), exchange=d.get("exchange", ""))
            for r in resolved:
                sym = r.get("symbol", "")
                cid = r.get("con_id", 0)
                if sym and cid > 0 and self._lifecycle.get_state(sym) == "discovered":
                    self._lifecycle.transition(sym, "resolved", "contract_resolved",
                                               con_id=cid, exchange=r.get("exchange", ""))

        # ── Phase 9: Rotation Planning ────────���──────────────────────────
        log.info("Phase 9: Rotation Planning (pure JSON router)")
        boosts = _load_boost_signals()
        plan = _apply_rotation_planning(ranked, self._lifecycle, boosts, mode)
        snapshot["live_100_count"] = plan["metrics"]["live_count"]
        snapshot["shortlist_250_count"] = plan["metrics"]["shortlist_count"]
        snapshot["rotation_metrics"] = plan["metrics"]
        log.info("Phase 9: live_100=%d shortlist=%d sunset=%d",
                 plan["metrics"]["live_count"],
                 plan["metrics"]["shortlist_count"],
                 plan["metrics"]["sunset_protected"])

        # ── Phase 8b: Single lifecycle update with ALL data ──────────────
        if self._lifecycle:
            shortlist_dicts = [{"symbol": t["symbol"], "con_id": t.get("con_id", 0),
                                "exchange": t.get("exchange", "")} for t in plan["shortlist_250"]]
            live_dicts = [{"symbol": t["symbol"], "con_id": t.get("con_id", 0),
                           "exchange": t.get("exchange", "")} for t in plan["live_100"]]
            transitions = self._lifecycle.update_from_run(
                discovered=discovered,
                admissible=admitted,
                resolved=resolved,
                ranked=ranked,
                shortlisted=shortlist_dicts,
                live_100=live_dicts,
                run_id=run_id,
            )
            self._lifecycle.apply_quarantine_rules()
            if mode == "full":
                self._lifecycle.apply_retirement_rules()
            self._lifecycle.save()
            snapshot["lifecycle_changes"] = len(transitions)

        # ── Phase 10: Snapshot Build ─────────────────────────────────────
        log.info("Phase 10: Snapshot Build")
        snapshot["completed_at"] = datetime.now(timezone.utc).isoformat()
        snapshot["degraded_reasons"] = self._degraded_reasons

        # ── Phase 11: Atomic Publish + Ack ───────────────────────────────
        if dry_run:
            log.info("Phase 11: DRY RUN — skipping publish")
            snapshot["publish_decision"] = "dry_run"
        else:
            log.info("Phase 11: Atomic Publish")
            snapshot["publish_decision"] = self._phase_publish(plan, snapshot, run_id)

        # Save rotation plan (always, even on dry-run)
        self._save_rotation_plan(plan)

        # Session 35: Generate radar_universe.toml on --full runs
        if mode == "full":
            log.info("Generating radar_universe.toml for radar daemon")
            self._generate_radar_universe(admitted)

        # Telegram PM briefing hook (placeholder)
        self._pm_briefing_hook(plan, snapshot)

        return snapshot

    # ═══════════════════════════════════════════════════════════════════════
    # LIGHT (Hot) Path
    # ═══════════════════════════════════��═══════════════════════════════════

    def _run_light(self, dry_run: bool, run_id: str) -> Dict[str, Any]:
        """Light path: cached JSON only. Zero IBKR. Zero LLM. No SIGHUP.

        Only mutates: live_rotation_plan.json, lifecycle (sunset→inactive only),
        snapshot, lifecycle_log.
        """
        snapshot: Dict[str, Any] = {
            "run_mode": "prep_next",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "degraded_reasons": [],
        }

        # ── Step 1: Load cached shortlist ────────────────────────────────
        prev_plan = _load_previous_rotation()
        if not prev_plan:
            log.warning("Light path: No previous rotation plan — nothing to do")
            snapshot["publish_decision"] = "blocked"
            snapshot["completed_at"] = datetime.now(timezone.utc).isoformat()
            return snapshot

        prev_shortlist = prev_plan.get("shortlist_250", [])
        prev_live = prev_plan.get("live_100", [])
        log.info("Light path: loaded %d shortlist, %d live from cache",
                 len(prev_shortlist), len(prev_live))

        # ── Step 2: Load boost artifacts ─────────────────────────────────
        boosts = _load_boost_signals()

        # ── Step 3: Re-score tactical_50 ���────────────────────────────────
        # Re-score all shortlisted symbols with fresh boost data
        for c in prev_shortlist:
            base = c.get("base_score", 0.5)
            boost_total, boost_components = _compute_boost_stack(c["symbol"], boosts)
            c["base_score"] = base
            c["boost_components"] = boost_components
            c["raw_boost"] = sum(boost_components.values())
            c["capped_boost"] = boost_total
            c["final_score"] = base + boost_total

        # ── Step 4: Sunset → inactive for flat positions ─────────────────
        if self._lifecycle:
            open_pos = _load_open_positions()
            sunset_syms = self._lifecycle.get_all_in_state("sunset")
            for sym in sunset_syms:
                if sym not in open_pos:
                    self._lifecycle.transition(sym, "inactive", f"position_flat:{run_id}")
            # NOTE: Do NOT save here — Phase 9 may perform additional transitions.
            # Save after Phase 9 below.

        # ── Step 5: Update rotation plan if changes warrant ──────────────
        plan = _apply_rotation_planning(prev_shortlist, self._lifecycle, boosts, "prep_next")
        snapshot["live_100_count"] = plan["metrics"]["live_count"]
        snapshot["shortlist_250_count"] = plan["metrics"]["shortlist_count"]
        snapshot["rotation_metrics"] = plan["metrics"]

        # Save lifecycle AFTER Phase 9 (which may transition more sunset→inactive)
        if self._lifecycle:
            self._lifecycle.save()

        if not dry_run:
            self._save_rotation_plan(plan)
        # NO SIGHUP, NO contracts.toml mutation, NO watchlist mutation

        snapshot["publish_decision"] = "prep_next_updated"
        snapshot["completed_at"] = datetime.now(timezone.utc).isoformat()
        return snapshot

    # ═══════════════════════════════════════════════════════════════════════
    # Phase Implementations (Heavy Path)
    # ═══��═══════════════════════════════════════════════════════════════════

    def _phase_discovery(self, mode: str) -> List[Dict[str, Any]]:
        """Phase 1: Radar cache + IBKR discovery (Session 35 enhanced).

        Step A: Read /app/data/radar_cache.json (instant, 0 API calls)
        Step B: Run ibkr_scanner.run_ibkr_scan() (unchanged)
        Step C: Merge radar candidates + scanner candidates (deduplicate)
        """
        # ── Step A: Load radar cache (fail-closed: empty list on failure) ──
        radar_candidates = self._load_radar_cache()

        # ── Step B: IBKR scanner discovery (unchanged) ──
        scanner_candidates = []
        try:
            from python_brain.ouroboros.ibkr_scanner import run_ibkr_scan, load_master, MASTER_FILE
        except ImportError:
            log.warning("ibkr_scanner not available — using cached master")
            scanner_candidates = self._load_cached_master()
        else:
            try:
                exit_code = run_ibkr_scan()
                if exit_code != 0:
                    log.warning("ibkr_scanner returned exit code %d", exit_code)
                    self._degraded_reasons.append(DegradedReason.IBKR_UNAVAILABLE)

                master = load_master()
                if master and master.get("tickers"):
                    scanner_candidates = master["tickers"]
                else:
                    log.warning("Master file empty after scan")
                    self._degraded_reasons.append(DegradedReason.IBKR_UNAVAILABLE)
                    scanner_candidates = self._load_cached_master()
            except Exception as e:
                log.error("IBKR discovery failed: %s", e)
                self._degraded_reasons.append(DegradedReason.IBKR_UNAVAILABLE)
                scanner_candidates = self._load_cached_master()

        # ── Step C: Merge (deduplicate by symbol) ──
        if not radar_candidates:
            return scanner_candidates

        # Build lookup of scanner symbols for dedup
        scanner_syms = {c.get("symbol", "") for c in scanner_candidates}
        merged = list(scanner_candidates)

        radar_added = 0
        for rc in radar_candidates:
            sym = rc.get("symbol", "")
            if sym and sym not in scanner_syms:
                merged.append(rc)
                scanner_syms.add(sym)
                radar_added += 1

        if radar_added > 0:
            log.info("Phase 1C: Merged %d radar candidates (total: %d)", radar_added, len(merged))

        return merged

    def _load_radar_cache(self) -> List[Dict[str, Any]]:
        """Load radar cache. Returns empty list on any failure (fail-closed).

        Session 35: Reads /app/data/radar_cache.json written by radar_daemon.py.
        Only includes tickers with fresh timestamps (< 5 min old).
        """
        try:
            if not RADAR_CACHE_FILE.exists():
                log.info("RADAR: cache not found — scanner-only mode")
                return []

            age = time.time() - RADAR_CACHE_FILE.stat().st_mtime
            if age > 600:  # 10 minutes
                log.warning("RADAR: cache stale (%.0fs) — scanner-only mode", age)
                return []

            with open(RADAR_CACHE_FILE) as f:
                data = json.load(f)

            tickers = data.get("tickers", {})
            if not tickers:
                log.warning("RADAR: cache has empty tickers dict — scanner-only mode")
                return []

            candidates = []
            now = datetime.now(timezone.utc)
            for sym, info in tickers.items():
                ts = info.get("ts")
                if not ts:
                    continue
                try:
                    ticker_ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    ticker_age = (now - ticker_ts.replace(tzinfo=timezone.utc)).total_seconds()
                except Exception:
                    continue
                if ticker_age > 300:  # 5 min per-ticker staleness
                    continue

                # Build candidate dict compatible with scanner output format
                candidate = {
                    "symbol": sym,
                    "ibkr_exchange": info.get("exchange", "SMART"),
                    "exchange": info.get("exchange", "SMART"),
                    "sec_type": "STK",
                    "currency": "USD",
                    "name": sym,
                    "category": "",
                    "subcategory": "",
                    # Radar-specific scoring data
                    "_radar_score": info.get("directional_score", 0.0),
                    "_radar_rvol": info.get("rvol", 0.0),
                    "_radar_spread_bps": info.get("spread_bps", 999.0),
                    "_radar_fund_rec": info.get("fund_rec"),
                    "_source": "radar",
                }
                candidates.append(candidate)

            log.info("RADAR: loaded %d fresh candidates from cache", len(candidates))
            return candidates

        except Exception as e:
            log.error("RADAR: cache load failed (%s) — scanner-only mode", e)
            return []

    def _load_cached_master(self) -> List[Dict[str, Any]]:
        """Fallback: load cached master from disk."""
        master_path = CONFIG_DIR / "isa_universe_master.json"
        if not master_path.exists():
            return []
        try:
            with open(master_path) as f:
                data = json.load(f)
            return data.get("tickers", [])
        except Exception:
            return []

    def _phase_normalize(self, discovered: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Phase 2: Normalize + dedupe by con_id (if available) or symbol."""
        seen_con_ids: Set[int] = set()
        seen_symbols: Set[str] = set()
        unique = []

        for d in discovered:
            con_id = d.get("con_id", 0)
            sym = d.get("symbol", "")
            if not sym:
                continue

            # Dedupe by con_id if available
            if con_id > 0:
                if con_id in seen_con_ids:
                    continue
                seen_con_ids.add(con_id)
            else:
                if sym in seen_symbols:
                    continue

            seen_symbols.add(sym)
            unique.append(d)

        return unique

    def _phase_resolution(self, admitted: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Phase 4: Contract resolution via IBKR reqContractDetails."""
        try:
            from python_brain.ouroboros.contract_expander import validate_candidates_ibkr
        except ImportError:
            log.warning("validate_candidates_ibkr not available — skipping resolution")
            return admitted

        # Get IBKR provider
        provider = None
        try:
            from python_brain.ouroboros.ibkr_data_provider import get_provider
            provider = get_provider()
        except ImportError:
            log.warning("ibkr_data_provider not available — resolution skipped")
            return admitted
        except Exception as e:
            log.warning("Failed to get IBKR provider: %s", e)
            self._degraded_reasons.append(DegradedReason.IBKR_UNAVAILABLE)
            return admitted

        try:
            resolved = validate_candidates_ibkr(admitted, provider)
            # Record resolution failures in lifecycle
            if self._lifecycle:
                for c in resolved:
                    if c.get("con_id", 0) == 0:
                        self._lifecycle.record_resolution_failure(c.get("symbol", ""))
            return resolved
        except Exception as e:
            log.error("Contract resolution failed: %s", e)
            self._degraded_reasons.append(DegradedReason.RESOLUTION_BURST_FAILURE)
            return admitted

    def _phase_gemini_ranking(self, resolved: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Phase 6: Gemini LLM ranking."""
        try:
            from python_brain.ouroboros.gemini_scanner import scan_core_universe
        except ImportError:
            log.warning("gemini_scanner not available — using deterministic fallback")
            self._degraded_reasons.append(DegradedReason.GEMINI_UNAVAILABLE)
            return self._deterministic_rank(resolved)

        try:
            # scan_core_universe returns list of recommended tickers
            gemini_picks = scan_core_universe()
            if not gemini_picks:
                log.warning("Gemini returned empty — using deterministic fallback")
                self._degraded_reasons.append(DegradedReason.GEMINI_UNAVAILABLE)
                return self._deterministic_rank(resolved)

            # Merge Gemini scores into resolved list
            gemini_set = set(gemini_picks) if isinstance(gemini_picks, (list, set)) else set()
            for c in resolved:
                sym = c.get("symbol", "")
                if sym in gemini_set:
                    c["gemini_score"] = 0.8  # Gemini-picked = high score
                else:
                    c["gemini_score"] = c.get("composite_score", 0.3)

            # Sort by gemini_score descending
            resolved.sort(key=lambda x: x.get("gemini_score", 0), reverse=True)
            return resolved

        except Exception as e:
            log.error("Gemini ranking failed: %s", e)
            self._degraded_reasons.append(DegradedReason.GEMINI_UNAVAILABLE)
            return self._deterministic_rank(resolved)

    def _deterministic_rank(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Deterministic fallback ranking: volume × leverage × exchange priority."""
        for c in candidates:
            vol = c.get("avg_daily_volume", 0)
            lev = c.get("leverage_factor", 1) or 1
            exch = c.get("exchange", "")

            # Exchange priority: LSEETF > LSE > US > Others
            exch_weight = {"LSEETF": 1.3, "LSE": 1.2, "NYSE": 1.0, "NASDAQ": 1.0,
                           "XETRA": 0.9, "EURONEXT": 0.9, "TSE": 0.8, "HKEX": 0.8, "SGX": 0.7}
            import math
            score = (math.log10(max(vol, 1)) / 10) * (1 + 0.15 * lev) * exch_weight.get(exch, 0.5)
            c["gemini_score"] = min(score, 1.0)

        candidates.sort(key=lambda x: x.get("gemini_score", 0), reverse=True)
        return candidates

    def _phase_claude_vetting(self, ranked: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Phase 7: Optional Claude vetting (advisory only, hard 60s timeout)."""
        try:
            from python_brain.ouroboros.claude_helper import ask_claude
        except ImportError:
            log.warning("claude_helper not available — skipping vetting")
            return ranked

        # Only vet top 20 (save LLM tokens)
        to_vet = ranked[:20]
        symbols = [c.get("symbol", "") for c in to_vet]

        try:
            import signal as sig

            def _timeout_handler(signum, frame):
                raise TimeoutError("Claude vetting timeout")

            old_handler = sig.signal(sig.SIGALRM, _timeout_handler)
            sig.alarm(60)  # Hard 60s timeout

            try:
                prompt = (
                    f"Review these {len(symbols)} ticker selections for the live trading universe. "
                    f"Flag any that should be downranked or vetoed. "
                    f"Respond with JSON: {{symbol: recommendation}} where recommendation is "
                    f"'allow', 'downrank', or 'veto'. Symbols: {json.dumps(symbols)}"
                )
                response = ask_claude(prompt, tier="fast")
                if response:
                    try:
                        vetoes = json.loads(response) if isinstance(response, str) else response
                        for c in ranked:
                            sym = c.get("symbol", "")
                            rec = vetoes.get(sym, "allow") if isinstance(vetoes, dict) else "allow"
                            if rec == "downrank":
                                c["gemini_score"] = c.get("gemini_score", 0.5) * 0.7
                                c["claude_veto"] = "downrank"
                            elif rec == "veto":
                                c["gemini_score"] = 0  # Will be filtered by score
                                c["claude_veto"] = "veto"
                    except (json.JSONDecodeError, TypeError):
                        log.warning("Claude response not parseable — ignoring")
            finally:
                sig.alarm(0)
                sig.signal(sig.SIGALRM, old_handler)

        except TimeoutError:
            log.warning("Claude vetting timed out at 60s — continuing without")
            self._degraded_reasons.append(DegradedReason.CLAUDE_UNAVAILABLE)
        except Exception as e:
            log.warning("Claude vetting failed: %s — continuing without", e)
            self._degraded_reasons.append(DegradedReason.CLAUDE_UNAVAILABLE)

        return ranked

    def _phase_publish(self, plan: Dict[str, Any], snapshot: Dict[str, Any], run_id: str) -> str:
        """Phase 11: Atomic publish via UniversePublisher."""
        if not self._publisher:
            log.warning("UniversePublisher not available — skipping publish")
            return "blocked"

        # Check degraded mode: don't SIGHUP degraded baseline over valid live universe
        if self._degraded_reasons and CONTRACTS_FILE.exists():
            log.warning("Degraded mode: NOT publishing over valid live universe")
            snapshot["degraded_reasons"] = self._degraded_reasons
            return "degraded"

        # Build contracts.toml content from plan
        contracts_toml = self._build_contracts_toml(plan)
        watchlist_json = self._build_watchlist_json(plan)
        universe_toml = self._build_universe_toml(plan)

        try:
            success, reasons = self._publisher.publish(
                contracts_toml=contracts_toml,
                watchlist_json=watchlist_json,
                universe_toml=universe_toml,
                rotation_plan=plan,
                snapshot_data=snapshot,
                dry_run=False,
            )
            if success:
                return "published"
            else:
                log.error("Publish blocked: %s", reasons)
                return "blocked"
        except Exception as e:
            log.error("Publish failed: %s", e)
            return "blocked"

    # ══��════════════════════════════════════════════════════════════════════
    # Emergency Baseline
    # ════════════════════════════════════════════════��══════════════════════

    def _emergency_baseline(self, snapshot: Dict[str, Any], dry_run: bool, run_id: str) -> Dict[str, Any]:
        """Break-glass: load emergency baseline when all discovery fails."""
        log.warning("EMERGENCY BASELINE: All IBKR discovery failed")
        snapshot["emergency_baseline_active"] = True
        snapshot["degraded_reasons"] = [DegradedReason.IBKR_UNAVAILABLE]

        if not EMERGENCY_BASELINE.exists():
            log.error("Emergency baseline file not found: %s", EMERGENCY_BASELINE)
            snapshot["publish_decision"] = "blocked"
            snapshot["completed_at"] = datetime.now(timezone.utc).isoformat()
            return snapshot

        # Only SIGHUP baseline if no previous contracts exist (true cold-start)
        if CONTRACTS_FILE.exists() and not dry_run:
            log.warning("Degraded: NOT overwriting valid contracts.toml with emergency baseline")
            snapshot["publish_decision"] = "degraded"
        else:
            snapshot["publish_decision"] = "degraded"

        snapshot["completed_at"] = datetime.now(timezone.utc).isoformat()
        return snapshot

    # ═══════════════════════════════════════════════════════════════════════
    # Artifact Builders
    # ═══════════════════════════════════════════════════════════════════════

    def _build_contracts_toml(self, plan: Dict[str, Any]) -> str:
        """Build contracts.toml: keep existing entries, append new ones from plan."""
        existing = ""
        existing_symbols: Set[str] = set()
        if CONTRACTS_FILE.exists():
            try:
                existing = CONTRACTS_FILE.read_text()
                # Parse existing symbols to avoid duplicates
                for line in existing.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("symbol") and "=" in stripped:
                        val = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            existing_symbols.add(val)
            except Exception:
                pass

        if not existing:
            existing = "# AEGIS V2 — contracts.toml\n# Auto-generated by dynamic_universe.py\n"

        # Append new contracts from shortlist that aren't in existing
        new_entries = []
        for t in plan.get("shortlist_250", []):
            sym = t.get("symbol", "")
            if not sym or sym in existing_symbols:
                continue
            con_id = t.get("con_id", 0)
            exch = _toml_escape(t.get("exchange", "SMART"))
            cur = t.get("currency", "USD")
            sec = _toml_escape(t.get("sector", "Unknown"))
            lev = t.get("leverage", 1) or 1
            safe_sym = _toml_escape(sym)
            new_entries.append(
                f'\n[[contracts]]\nsymbol = "{safe_sym}"\n'
                f'con_id = {con_id}\nexchange = "{exch}"\n'
                f'currency = "{cur}"\nsector = "{sec}"\n'
                f'leverage = {lev}\n'
            )
            existing_symbols.add(sym)

        if new_entries:
            log.info("Appending %d new contracts to contracts.toml", len(new_entries))
            existing += "\n" + "".join(new_entries)

        return existing

    def _build_watchlist_json(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Build active_watchlist.json from rotation plan."""
        now = datetime.now(timezone.utc).isoformat()
        tickers = [t["symbol"] for t in plan.get("live_100", [])]
        vanguard = []
        for t in plan.get("live_100", []):
            vanguard.append({
                "symbol": t["symbol"],
                "exchange": t.get("exchange", ""),
                "currency": t.get("currency", "USD"),
                "composite_score": t.get("final_score", 0),
                "sector": t.get("sector", "Unknown"),
                "con_id": t.get("con_id", 0),
            })

        return {
            "generated": now,
            "source": "dynamic_universe",
            "total_scored": plan["metrics"]["shortlist_count"],
            "tickers": tickers,
            "tier_counts": {"active": len(tickers)},
            "vanguard": vanguard,
        }

    def _build_universe_toml(self, plan: Dict[str, Any]) -> str:
        """Build initial_universe.toml from rotation plan (bootstrap aid only)."""
        lines = [
            "# AEGIS V2 — initial_universe.toml",
            f"# Generated by dynamic_universe.py at {datetime.now(timezone.utc).isoformat()}",
            f"# {len(plan.get('shortlist_250', []))} symbols in shortlist",
            "",
            "[universe]",
        ]
        for t in plan.get("shortlist_250", []):
            sym = t.get("symbol", "")
            exch = t.get("exchange", "")
            if sym:
                lines.append(f'[[universe.instruments]]')
                lines.append(f'symbol = "{_toml_escape(sym)}"')
                lines.append(f'exchange = "{_toml_escape(exch)}"')
                lines.append("")

        return "\n".join(lines)

    # ═════════════════════════���═════════════════════════════════════════════
    # Persistence
    # ═══════════════════════════════════════════��═══════════════════════════

    def _save_rotation_plan(self, plan: Dict[str, Any]):
        """Save rotation plan atomically."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            import tempfile as tmp
            fd, tmppath = tmp.mkstemp(dir=CONFIG_DIR, suffix=".json")
            with os.fdopen(fd, "w") as f:
                json.dump(plan, f, indent=2)
            os.replace(tmppath, str(ROTATION_PLAN_FILE))
            log.info("Saved rotation plan: %d live, %d shortlist",
                     len(plan.get("live_100", [])), len(plan.get("shortlist_250", [])))
        except Exception as e:
            log.error("Failed to save rotation plan: %s", e)

    def _save_snapshot(self, snapshot: Dict[str, Any]):
        """Save snapshot atomically to disk."""
        UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            import tempfile as tmp
            fd, tmppath = tmp.mkstemp(dir=UNIVERSE_DIR, suffix=".json")
            with os.fdopen(fd, "w") as f:
                json.dump(snapshot, f, indent=2, default=str)
            os.replace(tmppath, str(SNAPSHOT_FILE))
        except Exception as e:
            log.warning("Failed to save snapshot: %s", e)

    def _airlock_verify(self, candidate: Dict[str, Any]) -> bool:
        """Airlock: verify radar-sourced candidate with live snapshot before promotion.

        Session 35: Uses ibkr_data_provider.get_live_snapshot() (client_id=102).
        Returns True if candidate passes all checks. Fail-closed: False on any error.

        Checks:
            1. Price delta < 1.5% (signal not stale)
            2. Spread < 100bps (instrument liquid)
        """
        sym = candidate.get("symbol", "")
        radar_price = candidate.get("_radar_score", 0)  # This is directional score, not price

        try:
            from python_brain.ouroboros.ibkr_data_provider import get_provider
            provider = get_provider()
            snap = provider.get_live_snapshot(sym)
            if snap is None:
                log.warning("AIRLOCK REJECT %s: snapshot failed", sym)
                return False

            # Price delta check (compare snapshot to radar cache last price if available)
            radar_last = candidate.get("last", 0)
            if radar_last > 0 and snap["last"] > 0:
                delta_pct = abs(snap["last"] - radar_last) / radar_last * 100
                if delta_pct > 1.5:
                    log.warning("AIRLOCK REJECT %s: price delta %.1f%% > 1.5%%", sym, delta_pct)
                    return False

            # Spread check
            if snap["ask"] > 0 and snap["bid"] > 0 and snap["last"] > 0:
                spread_bps = (snap["ask"] - snap["bid"]) / snap["last"] * 10000
                if spread_bps > 100:
                    log.warning("AIRLOCK REJECT %s: spread %.0f bps > 100 bps", sym, spread_bps)
                    return False

            log.info("AIRLOCK PASS %s: last=%.2f spread=%.1f bps",
                     sym, snap["last"], (snap["ask"] - snap["bid"]) / snap["last"] * 10000 if snap["last"] > 0 else 0)
            return True

        except ImportError:
            log.warning("AIRLOCK SKIP %s: ibkr_data_provider not available", sym)
            return True  # Allow if provider unavailable (don't block on import issues)
        except Exception as e:
            log.warning("AIRLOCK REJECT %s: exception (%s)", sym, e)
            return False

    def _generate_radar_universe(self, admitted: List[Dict[str, Any]]):
        """Generate config/radar_universe.toml for the radar daemon.

        Session 35: Called during --full path. Groups admitted tickers by exchange.
        Daemon auto-detects changes via mtime file-watch (no signal needed).
        """
        try:
            by_exchange: Dict[str, List[str]] = {}
            for ticker in admitted:
                sym = ticker.get("symbol", "")
                exch = ticker.get("exchange", ticker.get("ibkr_exchange", "SMART"))
                if sym and exch:
                    by_exchange.setdefault(exch, []).append(sym)

            lines = [
                "# AEGIS V2 — Radar Universe (auto-generated by dynamic_universe.py --full)",
                f"# Generated: {datetime.now(timezone.utc).isoformat()}",
                f"# Total: {sum(len(v) for v in by_exchange.values())} tickers across {len(by_exchange)} exchanges",
                "",
            ]

            for exch in sorted(by_exchange.keys()):
                syms = sorted(set(by_exchange[exch]))
                lines.append(f"[{exch}]")
                lines.append(f"tickers = [{', '.join(repr(s) for s in syms)}]")
                lines.append("")

            RADAR_UNIVERSE_FILE.parent.mkdir(parents=True, exist_ok=True)
            import tempfile as _tf
            fd, tmp = _tf.mkstemp(dir=str(RADAR_UNIVERSE_FILE.parent), suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                f.write("\n".join(lines))
            os.replace(tmp, str(RADAR_UNIVERSE_FILE))
            log.info("Generated radar_universe.toml: %d tickers across %d exchanges",
                     sum(len(v) for v in by_exchange.values()), len(by_exchange))
        except Exception as e:
            log.warning("Failed to generate radar_universe.toml: %s", e)

    def _pm_briefing_hook(self, plan: Dict[str, Any], snapshot: Dict[str, Any]):
        """Telegram PM briefing on --session completion.

        Sends top 5 conviction with boost decomposition, churn delta,
        sunset queue, and summary metrics to Telegram.
        Falls back to logging if Telegram unavailable.
        """
        live_100 = plan.get("live_100", [])
        if not live_100:
            return

        metrics = snapshot.get("rotation_metrics", {})
        mode = snapshot.get("run_mode", "unknown")
        decision = snapshot.get("publish_decision", "unknown")
        duration = snapshot.get("duration_seconds", 0)

        # Build message
        top5 = sorted(live_100, key=lambda x: x.get("final_score", 0), reverse=True)[:5]
        lines = [
            f"<b>AEGIS Universe — {mode.upper()}</b>",
            f"Publish: {decision} | Duration: {duration:.1f}s",
            f"Live: {metrics.get('live_count', 0)} | Shortlist: {metrics.get('shortlist_count', 0)}",
            f"Sunset: {metrics.get('sunset_protected', 0)} | Churn: {metrics.get('churn_this_run', 0)}",
            "",
            "<b>Top 5 Conviction:</b>",
        ]
        for i, t in enumerate(top5, 1):
            boosts = t.get("boost_components", {})
            boost_str = ", ".join(f"{k}={v:.2f}" for k, v in boosts.items()) if boosts else "none"
            lines.append(f"  {i}. <b>{t['symbol']}</b> ({t.get('exchange', '?')}) "
                         f"score={t.get('final_score', 0):.3f} [{boost_str}]")

        # Exchange distribution
        exch_dist = metrics.get("exchange_distribution", {})
        if exch_dist:
            dist_str = " | ".join(f"{k}:{v}" for k, v in sorted(exch_dist.items(), key=lambda x: -x[1]))
            lines.append(f"\nExchanges: {dist_str}")

        # Degraded warning
        degraded = snapshot.get("degraded_reasons", [])
        if degraded:
            lines.append(f"\n<b>DEGRADED:</b> {', '.join(degraded)}")

        msg = "\n".join(lines)

        # Log always
        for line in lines:
            log.info("PM: %s", line.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))

        # Send via Telegram (best-effort)
        try:
            from python_brain.ouroboros.telegram_notify import send_message
            result = send_message(msg, parse_mode="HTML", disable_notification=(mode == "prep_next"))
            if result.get("ok"):
                log.info("PM Briefing sent to Telegram")
            else:
                log.warning("PM Briefing Telegram send failed: %s", result.get("error", "unknown"))
        except ImportError:
            log.info("PM Briefing: telegram_notify not available (logged only)")
        except Exception as e:
            log.warning("PM Briefing Telegram error: %s", e)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Dynamic Universe Orchestrator — broker-grounded autonomous universe management"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--bootstrap", action="store_true", help="Cold start, all exchanges (Heavy Path)")
    group.add_argument("--session", action="store_true", help="Open exchanges, refresh core_200 every 2h (Heavy Path)")
    group.add_argument("--prep-next", action="store_true", help="Tactical_50 refresh every 15min (Light Path)")
    group.add_argument("--full", action="store_true", help="All exchanges, full diff + retirement daily 22:00 UTC (Heavy Path)")
    parser.add_argument("--dry-run", action="store_true", help="All phases, no publish, no SIGHUP")

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [DynUniverse] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.bootstrap:
        mode = "bootstrap"
    elif args.session:
        mode = "session"
    elif args.prep_next:
        mode = "prep_next"
    elif args.full:
        mode = "full"
    else:
        parser.error("Must specify one of --bootstrap, --session, --prep-next, --full")
        return

    orchestrator = DynamicUniverseOrchestrator()
    result = orchestrator.run(mode, dry_run=args.dry_run)

    # Exit code
    decision = result.get("publish_decision", "blocked")
    if decision in ("published", "dry_run", "prep_next_updated"):
        sys.exit(0)
    elif decision == "degraded":
        sys.exit(2)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
