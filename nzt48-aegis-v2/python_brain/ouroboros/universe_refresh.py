"""Universe Refresh — Daily incremental validation for 36K+ tickers.

Runs daily at 06:00 UTC (before any market open) and:
  1. Loads existing isa_universe_master.json
  2. Validates a rotating subset of 500 tickers per day
     (covers full 36K universe in ~72 days)
  3. Prioritises recently added + problematic tickers
  4. Detects new leveraged ETPs from LSE via pattern matching
  5. Updates the master file with changes
  6. Generates a diff report
  7. Updates initial_universe.toml if new leveraged ETPs discovered

With 36K+ tickers, full validation takes ~72 days on a rotating schedule.
Each daily run stays under 10 minutes by validating exactly 500 tickers.

Usage: python3 -m python_brain.ouroboros.universe_refresh

Quarantine rules:
  - NEVER modifies live WAL or trading config
  - Only updates universe metadata files
  - Network failures are retried gracefully
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections import defaultdict
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
    import tomli
except ImportError:
    try:
        import tomllib as tomli
    except ImportError:
        print("ERROR: tomli not installed", file=sys.stderr)
        sys.exit(1)

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
MASTER_FILE = CONFIG_DIR / "isa_universe_master.json"
TOML_FILE = CONFIG_DIR / "initial_universe.toml"
REPORTS_DIR = DATA_DIR / "ouroboros_reports"
PROGRESS_FILE = DATA_DIR / "universe_cache" / "validation_progress.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Universe-Refresh] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("universe_refresh")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Daily validation batch: 500 tickers/day = ~72 days for 36K universe
DAILY_VALIDATION_BATCH = 500
# Priority slots: first N of the 500 are reserved for priority tickers
PRIORITY_SLOTS = 100
# Tickers that fail validation this many consecutive times are marked delisted
DELIST_THRESHOLD = 5
# Max runtime guard (minutes) — abort if exceeding this
MAX_RUNTIME_MINUTES = 10
# Batch size for yfinance calls
YF_BATCH_SIZE = 20


# ---------------------------------------------------------------------------
# Load / Save master file
# ---------------------------------------------------------------------------

def load_master() -> Optional[Dict[str, Any]]:
    """Load the master universe file."""
    if not MASTER_FILE.exists():
        log.error("Master file not found: %s — run full_universe_builder.py first", MASTER_FILE)
        return None
    try:
        with open(MASTER_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log.error("Failed to load master file: %s", e)
        return None


def save_master(master: Dict[str, Any]) -> None:
    """Save the updated master file."""
    master["last_updated"] = datetime.now(timezone.utc).isoformat()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(MASTER_FILE, "w") as f:
        json.dump(master, f, indent=2, default=str)
    log.info("Master file saved: %s (%d tickers)", MASTER_FILE, master["total_tickers"])


# ---------------------------------------------------------------------------
# Validation progress tracking
# ---------------------------------------------------------------------------

def load_progress() -> Dict[str, Any]:
    """Load validation progress (tracks where we are in the rotation)."""
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"last_offset": 0, "last_date": "", "total_validated_lifetime": 0}


def save_progress(progress: Dict[str, Any]) -> None:
    """Save validation progress."""
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


# ---------------------------------------------------------------------------
# Smart batch selection — prioritises recently added + problematic tickers
# ---------------------------------------------------------------------------

def select_validation_batch(
    tickers: List[Dict[str, Any]],
    progress: Dict[str, Any],
    batch_size: int = DAILY_VALIDATION_BATCH,
    priority_slots: int = PRIORITY_SLOTS,
) -> Tuple[List[Dict[str, Any]], int]:
    """Select the daily validation batch with smart prioritisation.

    Returns: (batch, new_offset)

    Strategy:
      1. First PRIORITY_SLOTS go to high-priority tickers:
         - Never validated (new additions)
         - Recently had failures (consecutive_failures > 0)
         - Not validated in 60+ days
      2. Remaining slots filled by rotating through the full universe.
    """
    active = [t for t in tickers if not t.get("delisted")]
    now = datetime.now(timezone.utc)

    # Identify priority tickers
    never_validated = []
    recently_failed = []
    stale = []

    for t in active:
        if not t.get("last_validated"):
            never_validated.append(t)
        elif t.get("consecutive_failures", 0) > 0:
            recently_failed.append(t)
        else:
            # Check staleness
            try:
                last_val = datetime.fromisoformat(t["last_validated"])
                if hasattr(last_val, 'tzinfo') and last_val.tzinfo is None:
                    last_val = last_val.replace(tzinfo=timezone.utc)
                age_days = (now - last_val).days
                if age_days > 60:
                    stale.append(t)
            except (ValueError, TypeError):
                stale.append(t)

    # Build priority batch
    priority_batch = []
    # 1. Never validated first (new tickers from full_universe_builder)
    priority_batch.extend(never_validated[:priority_slots])
    remaining_priority = priority_slots - len(priority_batch)
    # 2. Then recently failed
    if remaining_priority > 0:
        priority_batch.extend(recently_failed[:remaining_priority])
        remaining_priority = priority_slots - len(priority_batch)
    # 3. Then stale
    if remaining_priority > 0:
        priority_batch.extend(stale[:remaining_priority])

    priority_syms = {t["symbol"] for t in priority_batch}
    log.info("  Priority batch: %d never-validated, %d failed, %d stale => %d total",
             min(len(never_validated), priority_slots),
             min(len(recently_failed), max(0, priority_slots - len(never_validated))),
             len(priority_batch) - min(len(never_validated), priority_slots) -
             min(len(recently_failed), max(0, priority_slots - len(never_validated))),
             len(priority_batch))

    # Fill remaining slots with rotating walk through the universe
    rotation_slots = batch_size - len(priority_batch)
    rotation_batch = []

    # Filter out priority tickers from rotation pool
    rotation_pool = [t for t in active if t["symbol"] not in priority_syms]
    total_pool = len(rotation_pool)

    if total_pool > 0 and rotation_slots > 0:
        offset = progress.get("last_offset", 0) % total_pool
        end = offset + rotation_slots

        if end <= total_pool:
            rotation_batch = rotation_pool[offset:end]
        else:
            # Wrap around
            rotation_batch = rotation_pool[offset:] + rotation_pool[:end - total_pool]

        new_offset = end % total_pool
    else:
        new_offset = 0

    batch = priority_batch + rotation_batch
    log.info("  Rotation batch: %d tickers (offset %d -> %d of %d)",
             len(rotation_batch),
             progress.get("last_offset", 0),
             new_offset,
             total_pool)

    return batch, new_offset


# ---------------------------------------------------------------------------
# Validation via yfinance (fast batch mode)
# ---------------------------------------------------------------------------

def validate_subset(tickers_to_check: List[Dict[str, Any]]) -> Dict[str, str]:
    """Validate a subset of tickers. Returns {symbol: status}.

    status: "valid", "no_data", "error"

    Tries IBKR data provider first, falls back to yfinance.
    A ticker is "valid" if any source returns price data.
    """
    results = {}
    symbols = [t["symbol"] for t in tickers_to_check]
    start_time = time.monotonic()

    # Try IBKR first (primary)
    remaining = list(symbols)
    if _HAS_IBKR:
        try:
            provider = _get_ibkr_provider()
            ibkr_checked = []
            for sym in symbols:
                elapsed_min = (time.monotonic() - start_time) / 60
                if elapsed_min > MAX_RUNTIME_MINUTES * 0.8:
                    break
                try:
                    df = provider.get_price_data(sym, days=5, bar_size="1 day")
                    if df is not None and not df.empty:
                        results[sym] = "valid"
                    else:
                        results[sym] = "no_data"
                    ibkr_checked.append(sym)
                except Exception:
                    pass  # Let yfinance try
                time.sleep(0.07)
            remaining = [s for s in symbols if s not in results]
            if ibkr_checked:
                log.info("IBKR validated %d/%d tickers", len(ibkr_checked), len(symbols))
        except Exception as e:
            log.warning("IBKR provider failed: %s", e)

    # Fallback to yfinance for remaining
    if remaining and _HAS_YF:
        batch_size = YF_BATCH_SIZE
        for i in range(0, len(remaining), batch_size):
            elapsed_min = (time.monotonic() - start_time) / 60
            if elapsed_min > MAX_RUNTIME_MINUTES * 0.8:
                log.warning("Approaching runtime limit (%.1f min), stopping validation at batch %d",
                            elapsed_min, i)
                for sym in remaining[i:]:
                    results[sym] = "error"
                break

            batch = remaining[i:i + batch_size]
            batch_str = " ".join(batch)

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
                    for sym in batch:
                        results[sym] = "no_data"
                elif len(batch) == 1:
                    sym = batch[0]
                    if not data["Close"].dropna().empty:
                        results[sym] = "valid"
                    else:
                        results[sym] = "no_data"
                else:
                    for sym in batch:
                        try:
                            if sym in data["Close"].columns and not data["Close"][sym].dropna().empty:
                                results[sym] = "valid"
                            else:
                                results[sym] = "no_data"
                        except (KeyError, TypeError):
                            results[sym] = "no_data"

            except Exception as e:
                log.warning("Batch validation failed: %s", e)
                for sym in batch:
                    results[sym] = "error"

            time.sleep(0.3)

    return results


# ---------------------------------------------------------------------------
# Delisting detection
# ---------------------------------------------------------------------------

def update_delist_counters(
    tickers: List[Dict[str, Any]],
    validation_results: Dict[str, str],
) -> Tuple[List[str], List[str]]:
    """Update consecutive failure counters and detect delistings.

    Returns: (newly_delisted, revalidated)
    """
    newly_delisted = []
    revalidated = []

    for t in tickers:
        sym = t["symbol"]
        if sym not in validation_results:
            continue

        status = validation_results[sym]
        fail_count = t.get("consecutive_failures", 0)

        if status == "valid":
            if fail_count > 0:
                revalidated.append(sym)
            t["consecutive_failures"] = 0
            t["validated"] = True
            t["last_validated"] = datetime.now(timezone.utc).isoformat()
        elif status == "no_data":
            t["consecutive_failures"] = fail_count + 1
            if t["consecutive_failures"] >= DELIST_THRESHOLD:
                newly_delisted.append(sym)
                t["delisted"] = True
                t["delisted_date"] = datetime.now(timezone.utc).isoformat()
        # "error" status doesn't increment counter (transient network issues)

    return newly_delisted, revalidated


# ---------------------------------------------------------------------------
# New ticker detection
# ---------------------------------------------------------------------------

def check_for_new_leveraged_etps(existing_symbols: Set[str]) -> List[Dict[str, Any]]:
    """Check for new LSE leveraged ETPs not in the universe yet.

    Uses a broader pattern scan than the initial discovery.
    Limited to a small set per day to keep runtime low.
    """
    new_tickers = []

    # Generate candidate symbols not yet in universe
    codes_to_try = [
        "AP", "MS", "AM", "MT", "GO", "NF", "BA", "CO", "NV", "TS",
        "UK", "EU", "DE", "OI", "GD", "SV", "DI", "NI", "PF", "UB",
        "PL", "AI", "SQ", "PE", "RO", "BP", "RD", "HS", "AZ", "IO",
        "AB", "SP", "FT", "MA", "AD", "SO", "CR", "VI", "AR", "SE",
        "PA", "SA", "MU", "IN", "CP", "BI", "NE", "SH", "DA", "EL",
    ]

    candidates = []
    for code in codes_to_try:
        for prefix in ["3L", "3S", "2L", "2S", "5L", "5S"]:
            sym = f"{prefix}{code}.L"
            if sym not in existing_symbols:
                candidates.append(sym)

    if not candidates:
        return new_tickers

    # Only check a small subset per day to keep runtime under 10 minutes
    # Rotate through candidates using day-of-year
    day_of_year = datetime.now(timezone.utc).timetuple().tm_yday
    candidates_per_day = 60  # ~60 candidates = ~3 yfinance batches = ~10 seconds
    start_idx = (day_of_year * candidates_per_day) % max(len(candidates), 1)
    daily_candidates = candidates[start_idx:start_idx + candidates_per_day]

    if not daily_candidates:
        return new_tickers

    log.info("Checking %d new leveraged ETP candidates (of %d total unknown)...",
             len(daily_candidates), len(candidates))

    # Fast validation: try IBKR first, fallback to yfinance
    ibkr_found = set()
    if _HAS_IBKR:
        try:
            provider = _get_ibkr_provider()
            for sym in daily_candidates:
                try:
                    df = provider.get_price_data(sym, days=5, bar_size="1 day")
                    if df is not None and not df.empty:
                        leverage = int(sym[0])
                        is_inverse = sym[1] == "S"
                        last_price = float(df["close"].dropna().iloc[-1])
                        avg_vol = float(df["volume"].dropna().mean()) if "volume" in df.columns else 0

                        new_tickers.append({
                            "symbol": sym,
                            "exchange": "LSE",
                            "name": "",
                            "type": "leveraged_etp",
                            "sector": "Unknown",
                            "industry": "Unknown",
                            "currency": "GBP",
                            "isa_eligible": True,
                            "leveraged": True,
                            "inverse": is_inverse,
                            "leverage_factor": leverage,
                            "last_price": last_price,
                            "avg_daily_volume": int(avg_vol),
                            "validated": True,
                            "last_validated": datetime.now(timezone.utc).isoformat(),
                            "consecutive_failures": 0,
                            "source": "ibkr_etp_scan",
                        })
                        ibkr_found.add(sym)
                except Exception:
                    pass
                time.sleep(0.07)
        except Exception:
            pass

    # Fallback: yfinance for remaining candidates
    yf_candidates = [s for s in daily_candidates if s not in ibkr_found]
    if yf_candidates and _HAS_YF:
        batch_str = " ".join(yf_candidates)
        try:
            data = yf.download(batch_str, period="5d", interval="1d",
                               progress=False, threads=True, ignore_tz=True)
            if not data.empty and len(yf_candidates) > 1:
                for sym in yf_candidates:
                    try:
                        if sym in data["Close"].columns and not data["Close"][sym].dropna().empty:
                            leverage = int(sym[0])
                            is_inverse = sym[1] == "S"
                            last_price = data["Close"][sym].dropna().iloc[-1]
                            avg_vol = data["Volume"][sym].dropna().mean() if sym in data["Volume"].columns else 0

                            new_tickers.append({
                                "symbol": sym,
                                "exchange": "LSE",
                                "name": "",
                                "type": "leveraged_etp",
                                "sector": "Unknown",
                                "industry": "Unknown",
                                "currency": "GBP",
                                "isa_eligible": True,
                                "leveraged": True,
                                "inverse": is_inverse,
                                "leverage_factor": leverage,
                                "market_cap_usd": 0,
                                "avg_daily_volume": int(avg_vol) if avg_vol else 0,
                                "validated": True,
                                "last_validated": datetime.now(timezone.utc).isoformat(),
                                "source": "daily_scan",
                            })
                            log.info("  NEW ETP discovered: %s (price=%.2f)", sym, last_price)
                    except (KeyError, TypeError, IndexError):
                        continue
        except Exception as e:
            log.warning("ETP discovery batch failed: %s", e)

    return new_tickers


# ---------------------------------------------------------------------------
# TOML update
# ---------------------------------------------------------------------------

def update_initial_universe_toml(new_etps: List[Dict[str, Any]]) -> None:
    """Append newly discovered leveraged ETPs to initial_universe.toml."""
    if not new_etps:
        return

    if not TOML_FILE.exists():
        log.warning("initial_universe.toml not found, skipping TOML update")
        return

    with open(TOML_FILE, "r") as f:
        content = f.read()

    additions = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    additions.append(f"\n# ============================================================================")
    additions.append(f"# AUTO-DISCOVERED on {today} by universe_refresh.py")
    additions.append(f"# ============================================================================\n")

    for etp in new_etps:
        sym = etp["symbol"]
        leverage = etp.get("leverage_factor") or (3 if etp.get("leveraged") else 1)
        name = etp.get("name", "")
        sector = etp.get("sector", "Unknown")
        inverse = etp.get("inverse", False)

        inverse_of = ""
        if inverse:
            base = sym.replace(".L", "")
            if base[1] == "S":
                inverse_of = base[0] + "L" + base[2:] + ".L"

        additions.append(f"[[tickers]]")
        additions.append(f'symbol = "{sym}"')
        additions.append(f'leverage = {leverage}')
        additions.append(f'underlying = "{name}"')
        additions.append(f'sector = "{sector}"')
        additions.append(f'inverse_of = "{inverse_of}"')
        additions.append("")

    with open(TOML_FILE, "a") as f:
        f.write("\n".join(additions))

    log.info("Updated initial_universe.toml with %d new ETPs", len(new_etps))


# ---------------------------------------------------------------------------
# Diff report
# ---------------------------------------------------------------------------

def generate_diff_report(
    added: List[Dict[str, Any]],
    removed: List[str],
    revalidated: List[str],
    validation_count: int,
    total_tickers: int,
    never_validated_remaining: int,
    days_to_full_coverage: float,
) -> Path:
    """Generate a diff report for today's changes."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = REPORTS_DIR / f"universe_changes_{today}.json"

    report = {
        "date": today,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_tickers": total_tickers,
            "added": len(added),
            "removed": len(removed),
            "revalidated": len(revalidated),
            "validated_today": validation_count,
            "never_validated_remaining": never_validated_remaining,
            "estimated_days_to_full_coverage": round(days_to_full_coverage, 1),
        },
        "added": [{"symbol": t["symbol"], "exchange": t.get("exchange", ""), "name": t.get("name", "")} for t in added],
        "removed": removed,
        "revalidated": revalidated,
    }

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    log.info("Diff report saved: %s", report_path)
    log.info("  Added: %d | Removed: %d | Revalidated: %d | Validated: %d",
             len(added), len(removed), len(revalidated), validation_count)
    log.info("  Never-validated remaining: %d | Est. days to full coverage: %.1f",
             never_validated_remaining, days_to_full_coverage)

    return report_path


# ---------------------------------------------------------------------------
# Exchange summary rebuild
# ---------------------------------------------------------------------------

def rebuild_exchange_summary(tickers: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Rebuild the exchanges summary from the ticker list."""
    exchanges: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "tickers": []})
    for t in tickers:
        if t.get("delisted"):
            continue
        exch = t.get("exchange", "Unknown")
        exchanges[exch]["count"] += 1
        exchanges[exch]["tickers"].append(t["symbol"])
    return dict(sorted(exchanges.items(), key=lambda x: -x[1]["count"]))


# ---------------------------------------------------------------------------
# Main refresh loop
# ---------------------------------------------------------------------------

def run_refresh() -> int:
    """Execute the daily universe refresh with 500-ticker rotating validation."""
    start = time.monotonic()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.info("=" * 60)
    log.info("Universe Refresh (36K) — %s", today)
    log.info("=" * 60)

    # Step 1: Load master file
    master = load_master()
    if master is None:
        log.error("Cannot proceed without master file. Run full_universe_builder.py first.")
        return 1

    tickers = master.get("tickers", [])
    active_tickers = [t for t in tickers if not t.get("delisted")]
    log.info("Step 1: Loaded %d tickers (%d active, %d delisted)",
             len(tickers), len(active_tickers), len(tickers) - len(active_tickers))

    # Step 2: Load progress and select validation batch
    progress = load_progress()
    batch, new_offset = select_validation_batch(active_tickers, progress)
    log.info("Step 2: Selected %d tickers for validation", len(batch))

    # Step 3: Validate the batch
    log.info("Step 3: Validating %d tickers...", len(batch))
    validation_results = validate_subset(batch)
    valid_count = sum(1 for v in validation_results.values() if v == "valid")
    no_data_count = sum(1 for v in validation_results.values() if v == "no_data")
    error_count = sum(1 for v in validation_results.values() if v == "error")
    log.info("  Results: %d valid, %d no_data, %d errors",
             valid_count, no_data_count, error_count)

    # Step 4: Update delist counters
    newly_delisted, revalidated = update_delist_counters(tickers, validation_results)
    if newly_delisted:
        log.warning("Step 4: %d tickers marked as delisted: %s",
                    len(newly_delisted), newly_delisted[:20])
    if revalidated:
        log.info("Step 4: %d tickers revalidated: %s",
                 len(revalidated), revalidated[:20])

    # Step 5: Check for new leveraged ETPs (small daily subset)
    existing_symbols = {t["symbol"] for t in tickers}
    new_etps = check_for_new_leveraged_etps(existing_symbols)
    if new_etps:
        tickers.extend(new_etps)
        log.info("Step 5: Discovered %d new leveraged ETPs", len(new_etps))
    else:
        log.info("Step 5: No new leveraged ETPs found today")

    # Step 6: Rebuild counts and save
    active_count = sum(1 for t in tickers if not t.get("delisted"))
    validated_count = sum(1 for t in tickers if t.get("validated") and not t.get("delisted"))
    never_validated = sum(1 for t in tickers if not t.get("validated") and not t.get("delisted") and not t.get("last_validated"))

    master["tickers"] = tickers
    master["total_tickers"] = active_count
    master["exchanges"] = rebuild_exchange_summary(tickers)
    master["validated_count"] = validated_count
    save_master(master)

    # Step 7: Update progress
    progress["last_offset"] = new_offset
    progress["last_date"] = today
    progress["total_validated_lifetime"] = progress.get("total_validated_lifetime", 0) + len(validation_results)
    save_progress(progress)

    # Step 8: Update TOML if new ETPs found
    if new_etps:
        update_initial_universe_toml(new_etps)

    # Step 9: Generate diff report
    days_to_coverage = never_validated / max(DAILY_VALIDATION_BATCH - PRIORITY_SLOTS, 1) if never_validated > 0 else 0
    generate_diff_report(
        added=new_etps,
        removed=newly_delisted,
        revalidated=revalidated,
        validation_count=len(validation_results),
        total_tickers=active_count,
        never_validated_remaining=never_validated,
        days_to_full_coverage=days_to_coverage,
    )

    elapsed = time.monotonic() - start
    log.info("=" * 60)
    log.info("Universe Refresh complete in %.1fs (%.1f min)", elapsed, elapsed / 60)
    log.info("  Active tickers: %d", active_count)
    log.info("  Validated: %d (%.1f%%)", validated_count,
             validated_count / max(active_count, 1) * 100)
    log.info("  Never validated: %d (est. %d days to cover)",
             never_validated, int(days_to_coverage))
    log.info("  Added: %d | Removed: %d | Validated today: %d",
             len(new_etps), len(newly_delisted), len(validation_results))
    log.info("=" * 60)

    return 0


def main():
    try:
        sys.exit(run_refresh())
    except KeyboardInterrupt:
        log.info("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        log.error("Universe refresh failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
