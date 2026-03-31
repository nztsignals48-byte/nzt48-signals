"""Sprint S13: Claude Shadow Curation — Universe Ticker Selection.

Shadow mode universe curation. Claude picks top 100 primary + 50 booster
tickers from scanner results, Thompson top-K, Ouroboros scoreboard, and
session context. Compares Claude picks vs deterministic ticker_selector
output. Logs comparison — deterministic continues as active.

Constraint: open positions MUST remain in Tier 1 (never demoted mid-trade).

Usage: python3 -m python_brain.ouroboros.claude_curation
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PROJECT_ROOT / "python_brain"))
sys.path.insert(0, str(_PROJECT_ROOT))

from python_brain.ouroboros.claude_helper import (
    claude_query,
    build_context_string,
    load_context_files,
    send_telegram,
    MODEL_HAIKU,
    get_last_backend,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))
CURATION_DIR = DATA_DIR / "curation_comparison"

WATCHLIST_FILE = CONFIG_DIR / "active_watchlist.json"
THOMPSON_FILE = DATA_DIR / "thompson_top_k.json"
NIGHTLY_FILE = DATA_DIR / "nightly_output.json"
MEMORY_FILE = DATA_DIR / "persistent_memory.json"
CONTRACTS_FILE = CONFIG_DIR / "contracts.toml"

MAX_PRIMARY = 100
MAX_BOOSTER = 50

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Claude-Curation] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("claude_curation")


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------
def load_open_positions() -> Set[str]:
    """Load currently open position symbols from WAL (must stay Tier 1)."""
    open_positions: Set[str] = set()
    wal_path = WAL_DIR / "current.ndjson"
    if not wal_path.exists():
        return open_positions

    # Track opens and closes to find currently open
    opened: Dict[str, int] = {}  # symbol -> count of opens
    closed: Dict[str, int] = {}  # symbol -> count of closes

    try:
        with open(wal_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = event.get("payload", {})
                if "PositionOpened" in payload:
                    sym = payload["PositionOpened"].get("symbol", "")
                    if sym:
                        opened[sym] = opened.get(sym, 0) + 1
                elif "PositionClosed" in payload:
                    sym = payload["PositionClosed"].get("symbol", "")
                    if sym:
                        closed[sym] = closed.get(sym, 0) + 1
    except Exception as e:
        log.warning("Error reading WAL for open positions: %s", e)

    for sym, n_open in opened.items():
        n_close = closed.get(sym, 0)
        if n_open > n_close:
            open_positions.add(sym)

    return open_positions


def load_watchlist() -> Dict[str, Any]:
    """Load deterministic ticker_selector output (active_watchlist.json)."""
    if not WATCHLIST_FILE.exists():
        return {}
    try:
        with open(WATCHLIST_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log.warning("Failed to load watchlist: %s", e)
        return {}


def load_thompson_topk() -> List[Dict[str, Any]]:
    """Load Thompson sampling top-K tickers."""
    if not THOMPSON_FILE.exists():
        return []
    try:
        with open(THOMPSON_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else data.get("top_k", [])
    except (json.JSONDecodeError, IOError):
        return []


def load_scoreboard() -> List[Dict[str, Any]]:
    """Load Ouroboros ticker scoreboard from nightly output."""
    if not NIGHTLY_FILE.exists():
        return []
    try:
        with open(NIGHTLY_FILE) as f:
            data = json.load(f)
        sb = data.get("ticker_scoreboard", {})
        return sb.get("scoreboard", [])
    except (json.JSONDecodeError, IOError):
        return []


def load_all_contract_symbols() -> List[str]:
    """Load all available contract symbols from contracts.toml."""
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        if CONTRACTS_FILE.exists():
            with open(CONTRACTS_FILE, "rb") as f:
                data = tomllib.load(f)
            return [c["symbol"] for c in data.get("contracts", []) if c.get("symbol")]
    except Exception as e:
        log.warning("Failed to load contracts: %s", e)
    return []


# ---------------------------------------------------------------------------
# Claude curation query
# ---------------------------------------------------------------------------
def build_curation_prompt(
    all_symbols: List[str],
    watchlist: Dict[str, Any],
    thompson: List[Dict[str, Any]],
    scoreboard: List[Dict[str, Any]],
    open_positions: Set[str],
) -> str:
    """Build the prompt for Claude to curate the universe."""

    # Summarize inputs for Claude
    thompson_str = json.dumps(thompson[:30], indent=1) if thompson else "[]"
    scoreboard_top = [s for s in scoreboard if s.get("score", 0) >= 40][:30]
    scoreboard_str = json.dumps(scoreboard_top, indent=1) if scoreboard_top else "[]"

    watchlist_vanguard = [t.get("symbol", "") for t in watchlist.get("vanguard", [])]
    watchlist_warm = [t.get("symbol", "") for t in watchlist.get("warm", [])]

    open_list = sorted(open_positions)

    return f"""You are the AEGIS V2 universe curator. Analyze the following data and select tickers.

AVAILABLE UNIVERSE: {len(all_symbols)} total contracts across multiple exchanges.

THOMPSON TOP-K (Bayesian best-performing):
{thompson_str}

OUROBOROS SCOREBOARD (top tickers by composite score >= 40):
{scoreboard_str}

CURRENT DETERMINISTIC WATCHLIST:
  Vanguard (Tier 1): {json.dumps(watchlist_vanguard[:20])}
  Warm (Tier 2): {json.dumps(watchlist_warm[:20])}

OPEN POSITIONS (MUST remain in primary_tickers):
{json.dumps(open_list)}

CONSTRAINTS:
1. Select exactly {MAX_PRIMARY} primary tickers and {MAX_BOOSTER} booster tickers.
2. All open positions ({len(open_positions)} tickers) MUST be in primary_tickers.
3. Prefer tickers with: high Thompson score, high Ouroboros score, recent momentum.
4. Diversify across exchanges and sectors when possible.
5. Exclude any ticker with Ouroboros score < 20 (KILL classification).

Return JSON with this exact schema:
{{
  "date": "YYYY-MM-DD",
  "status": "ok",
  "confidence": "HIGH|MEDIUM|LOW",
  "primary_tickers": ["SYM1", "SYM2", ...],
  "booster_tickers": ["SYM1", "SYM2", ...],
  "rationale": "Brief explanation of selection logic",
  "additions_vs_deterministic": ["tickers Claude added that deterministic missed"],
  "removals_vs_deterministic": ["tickers Claude would remove that deterministic kept"]
}}"""


def compare_picks(
    claude_primary: List[str],
    claude_booster: List[str],
    deterministic_vanguard: List[str],
    deterministic_warm: List[str],
) -> Dict[str, Any]:
    """Compare Claude's picks against deterministic ticker_selector."""
    claude_set = set(claude_primary + claude_booster)
    det_set = set(deterministic_vanguard + deterministic_warm)

    claude_only = sorted(claude_set - det_set)
    det_only = sorted(det_set - claude_set)
    overlap = sorted(claude_set & det_set)

    overlap_pct = len(overlap) / max(len(claude_set | det_set), 1) * 100

    return {
        "claude_primary_count": len(claude_primary),
        "claude_booster_count": len(claude_booster),
        "deterministic_vanguard_count": len(deterministic_vanguard),
        "deterministic_warm_count": len(deterministic_warm),
        "overlap_count": len(overlap),
        "overlap_pct": round(overlap_pct, 1),
        "claude_only": claude_only,
        "deterministic_only": det_only,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_curation(send_tg: bool = False) -> int:
    """Execute shadow curation and log comparison."""
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d_%H%M")
    date_str = now.strftime("%Y-%m-%d")
    log.info("Claude curation starting for %s", timestamp)

    # Load inputs
    all_symbols = load_all_contract_symbols()
    if not all_symbols:
        log.error("No contract symbols loaded — aborting")
        return 1

    watchlist = load_watchlist()
    thompson = load_thompson_topk()
    scoreboard = load_scoreboard()
    open_positions = load_open_positions()

    log.info(
        "Inputs: %d contracts, %d thompson, %d scoreboard, %d open positions",
        len(all_symbols), len(thompson), len(scoreboard), len(open_positions),
    )

    # Build prompt and query Claude
    prompt = build_curation_prompt(
        all_symbols, watchlist, thompson, scoreboard, open_positions,
    )

    context = load_context_files([
        str(MEMORY_FILE),
        str(CONFIG_DIR / "dynamic_weights.toml"),
    ])
    system_ctx = build_context_string(context)

    result = claude_query(prompt, system_context=system_ctx, model=MODEL_HAIKU)
    if result is None:
        log.error("Claude query failed — no curation result")
        return 1

    # Validate response
    claude_primary = result.get("primary_tickers", [])
    claude_booster = result.get("booster_tickers", [])

    # Enforce open positions constraint
    for sym in open_positions:
        if sym not in claude_primary:
            claude_primary.append(sym)
            log.warning("Forced open position %s into primary_tickers", sym)

    # Compare against deterministic
    det_vanguard = [t.get("symbol", "") for t in watchlist.get("vanguard", [])]
    det_warm = [t.get("symbol", "") for t in watchlist.get("warm", [])]
    comparison = compare_picks(claude_primary, claude_booster, det_vanguard, det_warm)

    # Build output
    output = {
        "timestamp": now.isoformat(),
        "date": date_str,
        "status": result.get("status", "ok"),
        "confidence": result.get("confidence", "LOW"),
        "mode": "SHADOW",
        "claude_primary": claude_primary,
        "claude_booster": claude_booster,
        "rationale": result.get("rationale", ""),
        "comparison": comparison,
        "open_positions_enforced": sorted(open_positions),
    }

    # Write output
    CURATION_DIR.mkdir(parents=True, exist_ok=True)
    output_path = CURATION_DIR / f"{timestamp}.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    log.info("Curation comparison written: %s", output_path)

    log.info(
        "Shadow curation: %d primary, %d booster, %.1f%% overlap with deterministic",
        len(claude_primary), len(claude_booster), comparison["overlap_pct"],
    )

    if send_tg:
        msg = (
            f"<b>Claude Curation (SHADOW)</b>\n"
            f"Primary: {len(claude_primary)}, Booster: {len(claude_booster)}\n"
            f"Overlap with deterministic: {comparison['overlap_pct']:.1f}%\n"
            f"Claude-only: {len(comparison['claude_only'])}\n"
            f"Confidence: {result.get('confidence', 'LOW')}"
        )
        send_telegram(msg)

    return 0


def main():
    parser = argparse.ArgumentParser(description="Claude Shadow Curation (Sprint S13)")
    parser.add_argument("--send-telegram", action="store_true", help="Send summary via Telegram")
    args = parser.parse_args()

    try:
        sys.exit(run_curation(send_tg=args.send_telegram))
    except Exception as e:
        log.error("Claude curation crashed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
