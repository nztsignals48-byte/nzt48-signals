"""N10hh -- SETSqx Awareness for AEGIS V2.

SETSqx is a less-liquid LSE trading platform (vs the main SETS order book).
Some LSE leveraged ETPs may trade on SETSqx instead of SETS, which means:
  - No continuous order book (auction-based only)
  - Auctions at 08:00, 09:00, 11:00, 14:00, 16:35 London time
  - Wider spreads, lower liquidity
  - Market makers set indicative prices between auctions

This module:
  1. Discovers all LSEETF tickers from contracts.toml
  2. Maintains a known-SETSqx watchlist (manually curated)
  3. Returns risk assessments and next-auction times for SETSqx tickers
  4. Generates a formatted awareness report

QUARANTINE: Read-only analysis. No writes to WAL, config, or live state.

Usage:
    python3 -m python_brain.ouroboros.setsqx_awareness
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("ouroboros.setsqx_awareness")

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))

# ---------------------------------------------------------------------------
# SETSqx auction schedule (London time, UTC+0 winter / UTC+1 BST summer)
# These are fixed by LSE rules.
# ---------------------------------------------------------------------------
SETSQX_AUCTION_TIMES_UTC: List[time] = [
    time(8, 0),
    time(9, 0),
    time(11, 0),
    time(14, 0),
    time(16, 35),
]

# ---------------------------------------------------------------------------
# Known SETSqx tickers — manually maintained watchlist.
# As of 2026-03, all 49 LSEETF ETPs in contracts.toml trade on SETS.
# If any ticker migrates to SETSqx, add it here.
# ---------------------------------------------------------------------------
_KNOWN_SETSQX: Dict[str, str] = {
    # "EXAMPLE.L": "Moved to SETSqx on YYYY-MM-DD — reason",
}


# ---------------------------------------------------------------------------
# Read LSE tickers from contracts.toml
# ---------------------------------------------------------------------------
def _load_lse_tickers() -> List[Dict[str, str]]:
    """Parse contracts.toml and return LSEETF entries.

    Uses a minimal TOML reader (no external deps) — just extracts
    symbol/exchange/currency fields from [[contracts]] blocks.
    """
    toml_path = CONFIG_DIR / "contracts.toml"
    if not toml_path.exists():
        log.warning("contracts.toml not found at %s", toml_path)
        return []

    results: List[Dict[str, str]] = []
    current: Dict[str, str] = {}

    with open(toml_path, "r") as f:
        for raw_line in f:
            line = raw_line.strip()
            if line == "[[contracts]]":
                if current.get("exchange") == "LSEETF":
                    results.append(current)
                current = {}
                continue
            if "=" in line and not line.startswith("#"):
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"')
                if key in ("symbol", "exchange", "currency", "sector", "leverage"):
                    current[key] = val

        # Flush last block
        if current.get("exchange") == "LSEETF":
            results.append(current)

    return results


def _get_all_lse_symbols() -> List[str]:
    """Return sorted list of all LSEETF symbols from contracts.toml."""
    entries = _load_lse_tickers()
    return sorted(e["symbol"] for e in entries if "symbol" in e)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def is_setsqx_ticker(symbol: str) -> bool:
    """Return True if the symbol is known to trade on SETSqx."""
    return symbol in _KNOWN_SETSQX


def get_next_auction_time(symbol: str) -> Optional[datetime]:
    """Return the next SETSqx auction window for a SETSqx ticker.

    Returns None if the symbol is not a SETSqx ticker.
    Auction times are in UTC (does not adjust for BST — caller should
    be aware that LSE shifts to BST March-October).
    """
    if not is_setsqx_ticker(symbol):
        return None

    now = datetime.now(timezone.utc)
    today = now.date()

    for auction_t in SETSQX_AUCTION_TIMES_UTC:
        candidate = datetime.combine(today, auction_t, tzinfo=timezone.utc)
        if candidate > now:
            return candidate

    # All today's auctions have passed — next is 08:00 tomorrow
    tomorrow = today + timedelta(days=1)
    # Skip weekends
    while tomorrow.weekday() >= 5:  # 5=Sat, 6=Sun
        tomorrow += timedelta(days=1)
    return datetime.combine(tomorrow, SETSQX_AUCTION_TIMES_UTC[0], tzinfo=timezone.utc)


def check_setsqx_risk(symbol: str) -> Dict[str, Any]:
    """Return risk assessment for a symbol regarding SETSqx trading.

    Returns:
        Dict with keys:
            is_setsqx: bool
            liquidity_warning: str
            auction_only: bool
            recommended_order_type: str — "LIMIT" for SETSqx, "LMT" for SETS
            next_auction: Optional[str] — ISO timestamp of next auction
            note: str — human-readable context
    """
    if not is_setsqx_ticker(symbol):
        return {
            "is_setsqx": False,
            "liquidity_warning": "none",
            "auction_only": False,
            "recommended_order_type": "LMT",
            "next_auction": None,
            "note": f"{symbol} trades on SETS — continuous order book available",
        }

    next_auction = get_next_auction_time(symbol)
    reason = _KNOWN_SETSQX.get(symbol, "Unknown reason")

    return {
        "is_setsqx": True,
        "liquidity_warning": "HIGH — auction-only, no continuous book",
        "auction_only": True,
        "recommended_order_type": "LIMIT",
        "next_auction": next_auction.isoformat() if next_auction else None,
        "note": (
            f"{symbol} trades on SETSqx (auction-only). "
            f"Wider spreads expected. Use LIMIT orders and time entries "
            f"to auction windows. Reason: {reason}"
        ),
    }


def generate_setsqx_report() -> str:
    """Generate a formatted text report of SETSqx awareness status.

    Returns:
        Multi-line string suitable for logging or PDF inclusion.
    """
    now = datetime.now(timezone.utc)
    lse_symbols = _get_all_lse_symbols()
    setsqx_tickers = [s for s in lse_symbols if is_setsqx_ticker(s)]
    sets_tickers = [s for s in lse_symbols if not is_setsqx_ticker(s)]

    lines: List[str] = []
    lines.append("=" * 70)
    lines.append("AEGIS V2 -- SETSqx Awareness Report (N10hh)")
    lines.append(f"Generated: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append("=" * 70)
    lines.append("")

    # Summary
    lines.append(f"Total LSEETF contracts in universe: {len(lse_symbols)}")
    lines.append(f"  Trading on SETS (continuous book): {len(sets_tickers)}")
    lines.append(f"  Trading on SETSqx (auction-only):  {len(setsqx_tickers)}")
    lines.append("")

    # SETSqx detail
    if setsqx_tickers:
        lines.append("-" * 70)
        lines.append("SETSqx TICKERS (REQUIRE SPECIAL HANDLING)")
        lines.append("-" * 70)
        for sym in setsqx_tickers:
            risk = check_setsqx_risk(sym)
            reason = _KNOWN_SETSQX.get(sym, "")
            lines.append(f"  {sym:<12} | Next auction: {risk['next_auction'] or 'N/A'}")
            lines.append(f"  {'':12} | Reason: {reason}")
            lines.append(f"  {'':12} | Order type: {risk['recommended_order_type']}")
            lines.append("")
    else:
        lines.append("[OK] No LSEETF tickers currently on SETSqx.")
        lines.append("     All contracts trade on SETS with continuous order book.")
        lines.append("")

    # Auction schedule reference
    lines.append("-" * 70)
    lines.append("SETSqx AUCTION SCHEDULE (UTC, winter; +1h during BST)")
    lines.append("-" * 70)
    for i, auction_t in enumerate(SETSQX_AUCTION_TIMES_UTC, 1):
        label = {1: "Opening", 2: "Intra-day 1", 3: "Intra-day 2",
                 4: "Intra-day 3", 5: "Closing"}.get(i, f"Auction {i}")
        lines.append(f"  {label:<15} {auction_t.strftime('%H:%M')} UTC")
    lines.append("")

    # SETS tickers (compact list)
    lines.append("-" * 70)
    lines.append(f"SETS TICKERS ({len(sets_tickers)} — continuous order book, no action needed)")
    lines.append("-" * 70)
    # Print in rows of 6
    for i in range(0, len(sets_tickers), 6):
        chunk = sets_tickers[i:i + 6]
        lines.append("  " + "  ".join(f"{s:<12}" for s in chunk))
    lines.append("")

    # Advisory
    lines.append("-" * 70)
    lines.append("ADVISORY")
    lines.append("-" * 70)
    lines.append("  - If any ETP moves from SETS to SETSqx, add it to _KNOWN_SETSQX")
    lines.append("    in setsqx_awareness.py with the date and reason.")
    lines.append("  - SETSqx tickers should use LIMIT orders only (no market orders).")
    lines.append("  - Time entries to auction windows for best fill probability.")
    lines.append("  - Bridge.py should check is_setsqx_ticker() before order routing.")
    lines.append("  - Monitor LSE announcements for platform migration notices.")
    lines.append("")
    lines.append("=" * 70)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [SETSqx] %(levelname)s %(message)s",
    )
    print(generate_setsqx_report())
