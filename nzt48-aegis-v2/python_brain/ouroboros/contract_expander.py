"""Contract Expander — Autonomously grows contracts.toml from scored universe.

Runs after ticker_selector.py (every 15 min) and:
  1. Loads the scored watchlist (active_watchlist.json — all tiers)
  2. Loads the master universe (isa_universe_master.json — 36K+ tickers)
  3. Loads current contracts.toml
  4. Finds high-scoring tickers that LACK contract definitions
  5. Validates them via yfinance (must have recent price data)
  6. Appends new contract entries to contracts.toml
  7. Sends SIGHUP to the Rust engine so it hot-reloads the new contracts

The goal: the system autonomously discovers, validates, and registers new
tradeable instruments without human intervention. The ticker selector
discovers potential tickers from the 36K+ universe; this module bridges
the gap by creating IBKR contract definitions for the best ones.

Usage: python3 -m python_brain.ouroboros.contract_expander

Quarantine rules:
  - Only appends to contracts.toml (never overwrites/deletes existing entries)
  - Validates via yfinance before adding (no blind insertions)
  - Caps additions per run (MAX_NEW_PER_RUN) to prevent runaway growth
  - Logs every addition for audit trail
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from python_brain.ouroboros.ibkr_data_provider import get_provider as _get_ibkr_provider
    _HAS_IBKR = True
except ImportError:
    _HAS_IBKR = False

try:
    import yfinance as yf
    _HAS_YF = True
except ImportError:
    yf = None  # type: ignore
    _HAS_YF = False

if not _HAS_IBKR and not _HAS_YF:
    print("ERROR: Neither IBKR provider nor yfinance available", file=sys.stderr)
    sys.exit(1)

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        tomllib = None  # type: ignore

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
MASTER_FILE = CONFIG_DIR / "isa_universe_master.json"
WATCHLIST_FILE = CONFIG_DIR / "active_watchlist.json"
CONTRACTS_FILE = CONFIG_DIR / "contracts.toml"
REPORTS_DIR = DATA_DIR / "ouroboros_reports"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Contract-Expander] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("contract_expander")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Max new contracts to add per run (prevent runaway growth)
MAX_NEW_PER_RUN = 100  # Raised from 20 (universe expansion sprint)
# Max new contracts in --bulk mode (weekly seeding runs)
MAX_BULK_PER_RUN = 500
# Max total contracts in contracts.toml (raised from 500 for 5K research universe)
MAX_TOTAL_CONTRACTS = 5000
# Minimum composite score to consider a ticker for contract expansion
MIN_SCORE_THRESHOLD = 0.3
# yfinance batch size
YF_BATCH_SIZE = 20

# ---------------------------------------------------------------------------
# Exchange mapping: yfinance suffix → contracts.toml exchange + currency
# ---------------------------------------------------------------------------
YF_SUFFIX_TO_CONTRACT = {
    ".L":  ("LSEETF", "GBP"),
    ".T":  ("TSE",    "JPY"),
    ".HK": ("HKEX",   "HKD"),
    ".KS": ("KRX",    "KRW"),
    ".KQ": ("KRX",    "KRW"),
    ".DE": ("XETRA",  "EUR"),
    ".PA": ("EURONEXT", "EUR"),
    ".AS": ("EURONEXT", "EUR"),
    ".SI": ("SGX",    "SGD"),
    ".AX": ("ASX",    "AUD"),
}

# US exchanges (no suffix) — map internal exchange name → contracts.toml exchange
US_EXCHANGE_MAP = {
    "NYSE": "SMART",
    "NASDAQ": "SMART",
    "AMEX": "SMART",
}


def _yf_to_contract_symbol(yf_symbol: str) -> str:
    """Convert yfinance symbol to contracts.toml symbol format.

    LSE keeps .L suffix. All others strip the suffix.
    """
    if yf_symbol.endswith(".L"):
        return yf_symbol
    for suffix in YF_SUFFIX_TO_CONTRACT:
        if suffix == ".L":
            continue
        if yf_symbol.endswith(suffix):
            return yf_symbol[:-len(suffix)]
    return yf_symbol


def _yf_to_exchange_currency(yf_symbol: str, exchange: str = "") -> Tuple[str, str]:
    """Determine contracts.toml exchange and currency from yfinance symbol."""
    for suffix, (exch, cur) in YF_SUFFIX_TO_CONTRACT.items():
        if yf_symbol.endswith(suffix):
            return exch, cur
    # US stock (no suffix)
    if exchange in US_EXCHANGE_MAP:
        return US_EXCHANGE_MAP[exchange], "USD"
    return "SMART", "USD"


def _detect_leverage(ticker: Dict[str, Any]) -> int:
    """Detect leverage factor from ticker metadata."""
    lev = ticker.get("leverage_factor") or (3 if ticker.get("leveraged") else 1)
    if lev and lev > 1:
        return int(lev)
    # Try to detect from symbol name
    sym = ticker.get("symbol", "")
    name = ticker.get("name", "").upper()
    if sym.endswith(".L"):
        base = sym.replace(".L", "")
        if len(base) >= 2 and base[0].isdigit():
            return int(base[0])
    if "3X" in name or "TRIPLE" in name:
        return 3
    if "5X" in name:
        return 5
    if "2X" in name or "DOUBLE" in name:
        return 2
    return 1


# ---------------------------------------------------------------------------
# Load existing contracts
# ---------------------------------------------------------------------------

def load_existing_contracts() -> Set[str]:
    """Load all contract symbols from contracts.toml."""
    if not CONTRACTS_FILE.exists():
        return set()
    try:
        if tomllib is None:
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


def count_existing_contracts() -> int:
    """Count total contracts in contracts.toml."""
    return len(load_existing_contracts())


# ---------------------------------------------------------------------------
# Load candidates from watchlist + master universe
# ---------------------------------------------------------------------------

def load_candidates() -> List[Dict[str, Any]]:
    """Load scored tickers from watchlist that don't have contract definitions.

    Pulls from ALL tiers (vanguard + warm + apex) of active_watchlist.json.
    Also checks master universe for high-value tickers.
    Returns candidates sorted by composite_score descending.
    """
    existing = load_existing_contracts()
    candidates = []
    seen_symbols = set()

    # Source 1: active_watchlist.json (pre-scored, highest quality)
    if WATCHLIST_FILE.exists():
        try:
            with open(WATCHLIST_FILE) as f:
                watchlist = json.load(f)

            for tier_key in ("vanguard", "warm", "apex"):
                tier_list = watchlist.get(tier_key, [])
                for t in tier_list:
                    sym = t.get("symbol", "")
                    if not sym:
                        continue
                    contract_sym = _yf_to_contract_symbol(sym)
                    if contract_sym in existing:
                        continue
                    if sym in seen_symbols:
                        continue
                    seen_symbols.add(sym)
                    score = t.get("composite_score", 0)
                    if score >= MIN_SCORE_THRESHOLD:
                        candidates.append({
                            "symbol": sym,
                            "contract_symbol": contract_sym,
                            "composite_score": score,
                            "exchange": t.get("exchange", ""),
                            "name": t.get("name", ""),
                            "sector": t.get("sector", "Unknown"),
                            "leverage_factor": t.get("leverage_factor") or (3 if t.get("leveraged") else 1),
                            "source": f"watchlist_{tier_key}",
                        })
        except Exception as e:
            log.warning("Failed to load watchlist: %s", e)

    # Source 2: master universe (broader coverage, may find tickers not yet scored)
    if MASTER_FILE.exists():
        try:
            with open(MASTER_FILE) as f:
                master = json.load(f)

            for t in master.get("tickers", []):
                sym = t.get("symbol", "")
                if not sym or t.get("delisted"):
                    continue
                contract_sym = _yf_to_contract_symbol(sym)
                if contract_sym in existing or sym in seen_symbols:
                    continue
                # Only include validated tickers with good metadata
                if not t.get("validated"):
                    continue
                seen_symbols.add(sym)
                # Assign a base score for master universe tickers
                vol = t.get("avg_daily_volume", 0)
                lev = t.get("leverage_factor") or (3 if t.get("leveraged") else 1)
                # Simple scoring: leverage * log(volume)
                import math
                score = 0.1 + (0.3 if lev > 1 else 0) + min(0.3, math.log10(max(vol, 1)) / 10)
                if score >= MIN_SCORE_THRESHOLD:
                    candidates.append({
                        "symbol": sym,
                        "contract_symbol": contract_sym,
                        "composite_score": score,
                        "exchange": t.get("exchange", ""),
                        "name": t.get("name", ""),
                        "sector": t.get("sector", "Unknown"),
                        "leverage_factor": lev,
                        "source": "master_universe",
                    })
        except Exception as e:
            log.warning("Failed to load master universe: %s", e)

    # Source 3: pending_contracts.json (from universe_pipeline Phase 2)
    pending_path = DATA_DIR / "universe" / "pending_contracts.json"
    if pending_path.exists():
        try:
            with open(pending_path) as f:
                pending = json.load(f)
            for t in pending.get("tickers", []):
                sym = t.get("symbol", "")
                if not sym or sym in seen_symbols:
                    continue
                contract_sym = _yf_to_contract_symbol(sym)
                if contract_sym in existing:
                    continue
                seen_symbols.add(sym)
                score = t.get("research_score", 0) / 100.0  # Normalize to 0-1 range
                if score >= MIN_SCORE_THRESHOLD:
                    candidates.append({
                        "symbol": sym,
                        "contract_symbol": contract_sym,
                        "composite_score": score,
                        "exchange": t.get("exchange", ""),
                        "name": t.get("name", ""),
                        "sector": t.get("sector", "Unknown"),
                        "leverage_factor": t.get("leverage_factor") or (3 if t.get("leveraged") else 1),
                        "source": "pending_contracts",
                    })
            log.info("  Pending contracts source: %d candidates",
                     sum(1 for c in candidates if c["source"] == "pending_contracts"))
        except Exception as e:
            log.warning("Failed to load pending_contracts.json: %s", e)

    # Source 4: Discovery cache (from ticker_discovery.py nightly scan)
    # Auto-discovered new IPOs, LSEETF products, IBKR scanner finds
    discovery_path = DATA_DIR / "discovery_cache.json"
    if discovery_path.exists():
        try:
            with open(discovery_path) as f:
                discovery = json.load(f)
            for t in discovery.get("candidates", []):
                sym = t.get("symbol", "")
                if not sym or sym in seen_symbols:
                    continue
                contract_sym = _yf_to_contract_symbol(sym)
                if contract_sym in existing:
                    continue
                seen_symbols.add(sym)
                # Discovery candidates get a base score of 0.5
                # (they passed scanner/sweep filters but haven't been scored)
                candidates.append({
                    "symbol": sym,
                    "contract_symbol": contract_sym,
                    "composite_score": 0.5,
                    "exchange": t.get("exchange", ""),
                    "name": t.get("long_name", ""),
                    "sector": t.get("reason", "Discovery"),
                    "leverage_factor": 1,
                    "source": f"discovery_{t.get('source', 'unknown')}",
                    "con_id": t.get("con_id", 0),  # May already have con_id from IBKR scanner
                })
            log.info("  Discovery cache source: %d candidates",
                     sum(1 for c in candidates if c["source"].startswith("discovery_")))
        except Exception as e:
            log.warning("Failed to load discovery_cache.json: %s", e)

    # Sort by score descending
    candidates.sort(key=lambda c: c["composite_score"], reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# Validate candidates via yfinance
# ---------------------------------------------------------------------------

def validate_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validate candidates have recent price data.

    Tries IBKR data provider first, falls back to yfinance.
    Returns only those with valid price data.
    """
    if not candidates:
        return []

    validated = []
    remaining = list(candidates)

    # Try IBKR first (primary)
    if _HAS_IBKR:
        try:
            provider = _get_ibkr_provider()
            ibkr_validated = set()
            for c in remaining:
                sym = c["symbol"]
                try:
                    df = provider.get_price_data(sym, days=5, bar_size="1 day")
                    if df is not None and not df.empty:
                        validated.append(c)
                        ibkr_validated.add(sym)
                except Exception as e:
                    log.debug("IBKR validation failed for %s: %s", sym, e)
                time.sleep(0.07)  # IBKR pacing
            remaining = [c for c in remaining if c["symbol"] not in ibkr_validated]
            if ibkr_validated:
                log.info("IBKR validated %d candidates", len(ibkr_validated))
        except Exception as e:
            log.warning("IBKR provider failed during validation: %s", e)

    # Fallback: validate remaining via yfinance
    if remaining and _HAS_YF:
        symbols = [c["symbol"] for c in remaining]
        for i in range(0, len(symbols), YF_BATCH_SIZE):
            batch_syms = symbols[i:i + YF_BATCH_SIZE]
            batch_str = " ".join(batch_syms)

            try:
                data = yf.download(
                    batch_str,
                    period="5d",
                    interval="1d",
                    progress=False,
                    threads=True,
                    ignore_tz=True,
                )

                if data.empty:
                    continue

                for sym in batch_syms:
                    try:
                        if len(batch_syms) == 1:
                            has_data = not data["Close"].dropna().empty
                        else:
                            has_data = (
                                sym in data["Close"].columns
                                and not data["Close"][sym].dropna().empty
                            )

                        if has_data:
                            cand = next((c for c in remaining if c["symbol"] == sym), None)
                            if cand:
                                validated.append(cand)
                    except (KeyError, TypeError):
                        continue

            except Exception as e:
                log.warning("yfinance batch validation failed: %s", e)

            time.sleep(0.3)

    return validated


# ---------------------------------------------------------------------------
# Validate candidates via IBKR reqContractDetails
# ---------------------------------------------------------------------------

def validate_candidates_ibkr(
    candidates: List[Dict[str, Any]],
    provider: Any = None,
    max_batch: int = 50,
) -> List[Dict[str, Any]]:
    """Validate candidates via IBKR reqContractDetails. Returns enriched dicts with real con_id.

    Uses ibkr_data_provider.get_contract_details() for each candidate.
    Rate-limited to avoid IBKR Error 321 pacing violations.
    On failure per symbol: logs warning, sets con_id=0, continues.

    Args:
        candidates: list of candidate dicts (must have "symbol" or "contract_symbol")
        provider: ibkr_data_provider instance (must have get_contract_details method)
        max_batch: max candidates to resolve per call (IBKR pacing safety)

    Returns:
        Same candidates list with enriched IBKR metadata (con_id, sec_type, etc.)
    """
    if not candidates or provider is None:
        return candidates

    enriched = []
    batch = candidates[:max_batch]
    resolved = 0
    failed = 0

    for c in batch:
        sym = c.get("contract_symbol") or c.get("symbol", "")
        exch = c.get("exchange", "")
        if not sym:
            enriched.append(c)
            continue

        try:
            details = provider.get_contract_details(sym, exch)
            if details and details.get("con_id"):
                c["con_id"] = details["con_id"]
                c["sec_type"] = details.get("sec_type", "STK")
                c["primary_exchange"] = details.get("primary_exchange", exch)
                c["currency"] = details.get("currency", c.get("currency", "USD"))
                c["category"] = details.get("category", "")
                c["subcategory"] = details.get("subcategory", "")
                c["long_name"] = details.get("long_name", "")
                resolved += 1
            else:
                c["con_id"] = 0
                failed += 1
        except Exception as e:
            log.warning("IBKR validation failed for %s: %s", sym, e)
            c["con_id"] = 0
            failed += 1

        enriched.append(c)
        time.sleep(0.1)  # IBKR API pacing — mandatory

    # Append any remaining candidates beyond batch limit (unresolved)
    for c in candidates[max_batch:]:
        c.setdefault("con_id", 0)
        enriched.append(c)

    log.info("IBKR validation: %d resolved, %d failed, %d skipped (over batch limit)",
             resolved, failed, max(0, len(candidates) - max_batch))
    return enriched


# ---------------------------------------------------------------------------
# Append new contracts to contracts.toml
# ---------------------------------------------------------------------------

def append_contracts(new_contracts: List[Dict[str, Any]]) -> int:
    """Append new contract entries to contracts.toml.

    Returns the number of contracts successfully appended.
    """
    if not new_contracts:
        return 0

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "",
        f"# ═══════════════════════════════════════════════════════════════════════════",
        f"# AUTO-EXPANDED by contract_expander.py on {today}",
        f"# {len(new_contracts)} new contracts discovered from scored universe",
        f"# ═══════════════════════════════════════════════════════════════════════════",
        "",
    ]

    for c in new_contracts:
        sym = c["contract_symbol"]
        exch, cur = _yf_to_exchange_currency(c["symbol"], c.get("exchange", ""))
        lev = _detect_leverage(c)
        sector = c.get("sector", "Unknown")

        # Sanitize strings to prevent TOML injection
        def _esc(v: str) -> str:
            return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "").replace("\r", "")

        lines.append("[[contracts]]")
        lines.append(f'symbol = "{_esc(sym)}"')
        lines.append(f'con_id = {int(c.get("con_id", 0))}')
        lines.append(f'exchange = "{_esc(exch)}"')
        lines.append('sec_type = "STK"')
        lines.append(f'currency = "{_esc(cur)}"')
        lines.append(f"leverage = {int(lev)}")
        lines.append(f'sector = "{_esc(sector)}"')
        lines.append('inverse_of = ""')
        lines.append("")

    try:
        with open(CONTRACTS_FILE, "a") as f:
            f.write("\n".join(lines))
        log.info("Appended %d new contracts to contracts.toml", len(new_contracts))
        return len(new_contracts)
    except Exception as e:
        log.error("Failed to append to contracts.toml: %s", e)
        return 0


# ---------------------------------------------------------------------------
# Signal engine to hot-reload contracts
# ---------------------------------------------------------------------------

def signal_engine_reload() -> bool:
    """Send SIGHUP to the Rust engine process to trigger contract hot-reload.

    Inside Docker, the engine is PID 1 (or close to it).
    We find it by looking for the 'aegis' binary.
    """
    try:
        # Find engine PID (the Rust binary)
        result = subprocess.run(
            ["pgrep", "-f", "aegis"],
            capture_output=True, text=True, timeout=5,
        )
        pids = result.stdout.strip().split("\n")
        pids = [p.strip() for p in pids if p.strip()]

        if not pids:
            log.warning("Could not find engine process to signal")
            return False

        for pid in pids:
            try:
                os.kill(int(pid), signal.SIGHUP)
                log.info("Sent SIGHUP to engine PID %s", pid)
                return True
            except (OSError, ValueError) as e:
                log.warning("Failed to signal PID %s: %s", pid, e)

        return False
    except Exception as e:
        log.warning("Engine signal failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Main expansion logic
# ---------------------------------------------------------------------------

def run_expansion(bulk: bool = False) -> int:
    """Execute one round of contract expansion.

    Args:
        bulk: If True, use MAX_BULK_PER_RUN limit (for weekly seeding).

    Returns the number of new contracts added.
    """
    start = time.monotonic()
    mode_str = "BULK" if bulk else "standard"
    log.info("=" * 60)
    log.info("Contract Expander (%s) — %s", mode_str,
             datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    log.info("=" * 60)

    # Check current contract count
    current_count = count_existing_contracts()
    if current_count >= MAX_TOTAL_CONTRACTS:
        log.info("contracts.toml already at %d/%d max — skipping expansion",
                 current_count, MAX_TOTAL_CONTRACTS)
        return 0

    room = MAX_TOTAL_CONTRACTS - current_count
    per_run_limit = MAX_BULK_PER_RUN if bulk else MAX_NEW_PER_RUN
    batch_limit = min(per_run_limit, room)
    log.info("Current contracts: %d | Room for %d more (batch limit: %d)",
             current_count, room, batch_limit)

    # Step 1: Find candidates
    candidates = load_candidates()
    log.info("Step 1: Found %d candidate tickers without contract definitions", len(candidates))

    if not candidates:
        log.info("No expansion candidates found. Universe fully covered.")
        elapsed = time.monotonic() - start
        log.info("Completed in %.1fs", elapsed)
        return 0

    # Step 2: Take top N candidates
    top_candidates = candidates[:batch_limit * 2]  # Fetch extra in case some fail validation
    log.info("Step 2: Validating top %d candidates via yfinance...", len(top_candidates))

    # Step 3: Validate via yfinance
    validated = validate_candidates(top_candidates)
    log.info("Step 3: %d candidates validated (have recent price data)", len(validated))

    if not validated:
        log.info("No candidates passed validation.")
        elapsed = time.monotonic() - start
        log.info("Completed in %.1fs", elapsed)
        return 0

    # Step 4: Resolve con_ids via IBKR reqContractDetails
    to_add = validated[:batch_limit]
    if _HAS_IBKR:
        try:
            provider = _get_ibkr_provider()
            log.info("Step 4: Resolving %d candidates via IBKR reqContractDetails...", len(to_add))
            to_add = validate_candidates_ibkr(to_add, provider, max_batch=batch_limit)
            # Filter out any that failed IBKR resolution (con_id=0)
            before = len(to_add)
            to_add = [c for c in to_add if c.get("con_id", 0) > 0]
            log.info("Step 4: %d/%d contracts have valid IBKR con_id", len(to_add), before)
        except Exception as e:
            log.warning("Step 4: IBKR resolution failed: %s — skipping unresolved", e)
            to_add = [c for c in to_add if c.get("con_id", 0) > 0]
    else:
        log.warning("Step 4: IBKR provider unavailable — cannot resolve con_ids, skipping")
        to_add = []

    if not to_add:
        log.info("No candidates with valid IBKR con_id. Skipping append.")
        elapsed = time.monotonic() - start
        log.info("Completed in %.1fs", elapsed)
        return 0

    log.info("Step 4b: Adding %d new contracts (of %d validated)", len(to_add), len(validated))
    for c in to_add:
        log.info("  + %s (%s) con_id=%d score=%.3f source=%s",
                 c["contract_symbol"], c.get("exchange", "?"),
                 c.get("con_id", 0), c["composite_score"], c["source"])

    # Step 5: Append to contracts.toml
    added = append_contracts(to_add)

    # Step 6: Signal engine to hot-reload
    if added > 0:
        log.info("Step 6: Signaling engine to hot-reload contracts...")
        signaled = signal_engine_reload()
        if not signaled:
            log.warning("Engine signal failed — new contracts will load on next restart")

    # Step 7: Save expansion report
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"contract_expansion_{datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M')}.json"
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "candidates_found": len(candidates),
        "validated": len(validated),
        "added": added,
        "new_total": current_count + added,
        "contracts": [
            {
                "symbol": c["contract_symbol"],
                "yf_symbol": c["symbol"],
                "score": c["composite_score"],
                "exchange": c.get("exchange", ""),
                "source": c["source"],
            }
            for c in to_add[:added]
        ],
    }
    try:
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)
    except Exception:
        pass

    elapsed = time.monotonic() - start
    log.info("=" * 60)
    log.info("Contract Expander complete in %.1fs", elapsed)
    log.info("  Added: %d | New total: %d/%d", added, current_count + added, MAX_TOTAL_CONTRACTS)
    log.info("=" * 60)

    return added


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Contract Expander — grow contracts.toml")
    parser.add_argument("--bulk", action="store_true",
                        help=f"Bulk mode: add up to {MAX_BULK_PER_RUN} contracts (weekly seeding)")
    args = parser.parse_args()

    try:
        added = run_expansion(bulk=args.bulk)
        sys.exit(0 if added >= 0 else 1)
    except KeyboardInterrupt:
        log.info("Interrupted")
        sys.exit(130)
    except Exception as e:
        log.error("Contract expansion failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
