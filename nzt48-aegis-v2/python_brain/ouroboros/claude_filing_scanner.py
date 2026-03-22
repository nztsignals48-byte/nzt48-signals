"""Sprint S19: Claude SEC/RNS Filing Semantic Delta Scanner.

Reads top 20 tickers from active_watchlist.json. For each, checks if new
SEC 10-Q/8-K or LSE RNS filings exist (stub integration — actual download
deferred). Claude compares filing text diffs, focusing on Risk Factors and
Management Discussion. Flags material changes: new legal language, removed
guidance, going-concern mentions.

Shadow mode — 50 events before any auto-exclusion.

Usage: python3 -m python_brain.ouroboros.claude_filing_scanner
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))
WATCHLIST_FILE = CONFIG_DIR / "active_watchlist.json"
FILING_DIR = DATA_DIR / "claude" / "macro"
FILING_CACHE_DIR = DATA_DIR / "filing_cache"

# Shadow mode: accumulate N events before any auto-exclusion
SHADOW_THRESHOLD = 50
MAX_TICKERS = 20

# Filing types to scan
SEC_FILING_TYPES = ["10-Q", "10-K", "8-K", "6-K"]
RNS_CATEGORIES = ["Results", "Trading Update", "Director Dealings", "Regulatory"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Claude-FilingScanner] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("claude_filing_scanner")


# ---------------------------------------------------------------------------
# Watchlist loading
# ---------------------------------------------------------------------------
def load_top_tickers() -> List[Dict[str, Any]]:
    """Load top N tickers from active_watchlist.json."""
    if not WATCHLIST_FILE.exists():
        log.warning("Watchlist not found: %s", WATCHLIST_FILE)
        return []
    try:
        with open(WATCHLIST_FILE) as f:
            data = json.load(f)
        # Combine vanguard + warm, take top N
        tickers = []
        for t in data.get("vanguard", []):
            tickers.append(t)
        for t in data.get("warm", []):
            tickers.append(t)
        return tickers[:MAX_TICKERS]
    except (json.JSONDecodeError, IOError) as e:
        log.warning("Failed to load watchlist: %s", e)
        return []


# ---------------------------------------------------------------------------
# Filing stub — actual download integration deferred
# ---------------------------------------------------------------------------
def check_new_filings(symbol: str) -> List[Dict[str, Any]]:
    """Check for new SEC/RNS filings for a given symbol.

    STUB: Returns cached filing data if available, empty list otherwise.
    Actual SEC EDGAR / LSE RNS API integration is deferred to a future sprint.
    When implemented, this will:
    1. Query SEC EDGAR for 10-Q/8-K filings (US tickers)
    2. Query LSE RNS feed for regulatory news (UK tickers)
    3. Download and cache filing text
    4. Return list of new filings with text content
    """
    filings: List[Dict[str, Any]] = []

    # Check filing cache for pre-downloaded filings
    cache_dir = FILING_CACHE_DIR / symbol.replace(".", "_")
    if not cache_dir.exists():
        return filings

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for filing_path in sorted(cache_dir.glob("*.json")):
        try:
            with open(filing_path) as f:
                filing = json.load(f)
            # Only include filings from the last 7 days
            filing_date = filing.get("date", "")
            if filing_date >= (datetime.now(timezone.utc).strftime("%Y-%m-") + "01"):
                filings.append(filing)
        except (json.JSONDecodeError, IOError):
            continue

    return filings


def load_previous_filing(symbol: str, filing_type: str) -> Optional[str]:
    """Load the previous version of a filing for diff comparison.

    STUB: Returns cached previous filing text if available.
    """
    cache_dir = FILING_CACHE_DIR / symbol.replace(".", "_")
    prev_path = cache_dir / f"prev_{filing_type.replace('-', '_')}.txt"
    if prev_path.exists():
        try:
            return prev_path.read_text(errors="replace")[:30000]
        except IOError:
            pass
    return None


# ---------------------------------------------------------------------------
# Claude analysis
# ---------------------------------------------------------------------------
def build_filing_prompt(
    symbol: str,
    filings: List[Dict[str, Any]],
    previous_text: Optional[str],
) -> str:
    """Build prompt for Claude to analyze filing semantic deltas."""
    filings_str = json.dumps(filings, indent=2)
    prev_excerpt = previous_text[:5000] if previous_text else "(no previous filing available)"

    return f"""You are the AEGIS V2 filing analyst reviewing SEC/RNS filings for {symbol}.

NEW FILINGS:
{filings_str}

PREVIOUS FILING EXCERPT (for diff comparison):
{prev_excerpt}

Analyze the filings and identify MATERIAL CHANGES in these sections:
1. Risk Factors — new risks, removed risks, language changes
2. Management Discussion & Analysis — guidance changes, tone shifts
3. Going Concern — any mention or removal of going-concern language
4. Legal Proceedings — new lawsuits, settlements, regulatory actions
5. Revenue/Earnings — significant misses, restatements, accounting changes

SEVERITY CLASSIFICATION:
- CRITICAL: Going-concern mention, restatement, delisting risk, fraud allegation
- HIGH: Removed forward guidance, new material litigation, significant revenue miss
- MEDIUM: New risk factors, management changes, minor guidance revision
- LOW: Routine updates, cosmetic language changes
- NONE: No material changes detected

Return JSON:
{{
  "symbol": "{symbol}",
  "date": "YYYY-MM-DD",
  "status": "ok",
  "confidence": "HIGH|MEDIUM|LOW",
  "filings_reviewed": <int>,
  "material_changes": [
    {{
      "section": "<section name>",
      "change_type": "ADDED|REMOVED|MODIFIED",
      "severity": "CRITICAL|HIGH|MEDIUM|LOW|NONE",
      "summary": "<1-2 sentence description>",
      "key_phrases": ["<relevant phrases>"]
    }}
  ],
  "overall_severity": "CRITICAL|HIGH|MEDIUM|LOW|NONE",
  "recommendation": "EXCLUDE|WATCHLIST|MONITOR|NO_ACTION",
  "reasoning": "<1-2 sentence overall assessment>"
}}"""


# ---------------------------------------------------------------------------
# Shadow mode tracking
# ---------------------------------------------------------------------------
def load_shadow_counter() -> int:
    """Load the shadow mode event counter."""
    counter_path = FILING_DIR / "filing_shadow_counter.json"
    if counter_path.exists():
        try:
            with open(counter_path) as f:
                data = json.load(f)
            return data.get("events_processed", 0)
        except (json.JSONDecodeError, IOError):
            pass
    return 0


def save_shadow_counter(count: int) -> None:
    """Save the shadow mode event counter."""
    counter_path = FILING_DIR / "filing_shadow_counter.json"
    FILING_DIR.mkdir(parents=True, exist_ok=True)
    with open(counter_path, "w") as f:
        json.dump({
            "events_processed": count,
            "updated": datetime.now(timezone.utc).isoformat(),
            "shadow_threshold": SHADOW_THRESHOLD,
            "shadow_active": count < SHADOW_THRESHOLD,
        }, f, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_filing_scanner() -> int:
    """Execute SEC/RNS filing semantic delta scan."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    log.info("Filing scanner starting for %s", date_str)

    # Load top tickers
    tickers = load_top_tickers()
    if not tickers:
        log.info("No tickers in watchlist — nothing to scan")
        return 0

    log.info("Scanning filings for %d tickers", len(tickers))

    # Shadow mode counter
    shadow_count = load_shadow_counter()
    is_shadow = shadow_count < SHADOW_THRESHOLD
    if is_shadow:
        log.info("SHADOW MODE: %d/%d events processed (auto-exclusion disabled)", shadow_count, SHADOW_THRESHOLD)

    results: List[Dict[str, Any]] = []
    critical_alerts: List[Dict[str, Any]] = []

    for ticker_info in tickers:
        symbol = ticker_info.get("symbol", "") if isinstance(ticker_info, dict) else str(ticker_info)
        if not symbol:
            continue

        # Check for new filings
        filings = check_new_filings(symbol)
        if not filings:
            continue

        log.info("Found %d filings for %s", len(filings), symbol)

        # Load previous filing for diff
        filing_type = filings[0].get("type", "10-Q")
        previous_text = load_previous_filing(symbol, filing_type)

        # Query Claude
        prompt = build_filing_prompt(symbol, filings, previous_text)
        result = claude_query(prompt)

        if result is None:
            log.warning("Claude query failed for %s — skipping", symbol)
            continue

        result["mode"] = "SHADOW" if is_shadow else "ACTIVE"
        results.append(result)
        shadow_count += 1

        severity = result.get("overall_severity", "NONE")
        recommendation = result.get("recommendation", "NO_ACTION")

        log.info(
            "  %s: severity=%s, recommendation=%s, changes=%d",
            symbol, severity, recommendation,
            len(result.get("material_changes", [])),
        )

        if severity in ("CRITICAL", "HIGH"):
            critical_alerts.append(result)

    # Save shadow counter
    save_shadow_counter(shadow_count)

    # Write consolidated output
    FILING_DIR.mkdir(parents=True, exist_ok=True)
    output = {
        "date": date_str,
        "tickers_scanned": len(tickers),
        "filings_found": sum(1 for r in results if r.get("filings_reviewed", 0) > 0),
        "critical_count": len(critical_alerts),
        "shadow_mode": is_shadow,
        "shadow_events_processed": shadow_count,
        "results": results,
    }

    output_path = FILING_DIR / f"filing_delta_{date_str}.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    log.info("Filing scan results written: %s", output_path)

    # Telegram for critical alerts
    if critical_alerts:
        alert_lines = []
        for alert in critical_alerts:
            sym = alert.get("symbol", "?")
            sev = alert.get("overall_severity", "?")
            rec = alert.get("recommendation", "?")
            changes = alert.get("material_changes", [])
            top_change = changes[0].get("summary", "") if changes else ""
            alert_lines.append(f"  {sym}: {sev} — {rec}\n  {top_change}")

        msg = (
            f"<b>Filing Scanner Alert</b>\n"
            f"Critical/High filings: {len(critical_alerts)}\n"
            + "\n".join(alert_lines) + "\n"
            + ("Mode: SHADOW (no auto-exclusion)" if is_shadow else "Mode: ACTIVE")
        )
        send_telegram(msg)

    log.info(
        "Filing scan complete: %d tickers, %d with filings, %d critical",
        len(tickers), len(results), len(critical_alerts),
    )

    return 0


def main():
    parser = argparse.ArgumentParser(description="Claude SEC/RNS Filing Scanner (Sprint S19)")
    parser.parse_args()  # No custom args

    try:
        sys.exit(run_filing_scanner())
    except Exception as e:
        log.error("Claude filing scanner crashed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
