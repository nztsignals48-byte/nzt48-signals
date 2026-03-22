"""Claude Forensic Review -- post-trade analysis with trade classification.

Runs nightly at 04:53 UTC (after nightly_v6 at 04:50). Reads today's WAL
events (PositionOpened + PositionClosed) and gate_vetoes.ndjson, then asks
Claude to:
  1. Classify each trade as GOOD_ENTRY, BAD_ENTRY, UNLUCKY, PREMATURE_EXIT
  2. Identify which gate vetoes were correct vs which blocked good trades
  3. Provide root cause analysis and actionable recommendations

QUARANTINE: This module is READ-ONLY. It NEVER writes to WAL, config.toml,
dynamic_weights.toml, or any live trading parameter. Output goes only to
/app/data/claude_forensic_review.json.

Usage:
    python3 -m python_brain.ouroboros.claude_forensic_review
    python3 -m python_brain.ouroboros.claude_forensic_review --dry-run
    python3 -m python_brain.ouroboros.claude_forensic_review --date 2026-03-21
"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from python_brain.ouroboros.claude_intelligence import claude_analyze_json

log = logging.getLogger("claude_forensic_review")

# ---------------------------------------------------------------------------
# Paths (mirror nightly_v6.py conventions)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))
GATE_VETOES_FILE = DATA_DIR / "gate_vetoes.ndjson"
NIGHTLY_OUTPUT_FILE = DATA_DIR / "nightly_output.json"
OUTPUT_FILE = DATA_DIR / "claude_forensic_review.json"

# ---------------------------------------------------------------------------
# System prompt for forensic review
# ---------------------------------------------------------------------------
FORENSIC_SYSTEM_PROMPT = """\
You are the post-trade forensic analyst for AEGIS V2, an autonomous UK ISA
leveraged ETP trading engine. Your job is to classify every trade and gate
veto from today with surgical precision.

TRADE CLASSIFICATION (choose exactly one per trade):
  GOOD_ENTRY   -- Entry logic was sound, position was managed well, outcome correct.
  BAD_ENTRY    -- Entry logic was flawed (wrong regime, chased price, false signal).
  UNLUCKY      -- Entry was reasonable but adverse move / news / macro killed it.
  PREMATURE_EXIT -- Entry was good AND price later moved significantly in our favour,
                    but Chandelier stop triggered too early.

For each classification, provide:
  - The classification label
  - 1-2 sentence reasoning citing specific numbers (entry price, MFE, MAE, rung)
  - A lesson (what the system should learn)

VETO CLASSIFICATION (choose exactly one per veto):
  GOOD_VETO    -- Gate correctly blocked a trade that would have lost money.
  BAD_VETO     -- Gate incorrectly blocked a trade that would have been profitable.
  AMBIGUOUS    -- Insufficient data or unclear outcome.
  DATA_VETO    -- Blocked due to missing/stale data, not a signal quality judgement.

OUTPUT FORMAT (pure JSON, no markdown wrapping):
{
  "date": "YYYY-MM-DD",
  "status": "complete",
  "confidence": 0.0-1.0,
  "trade_classifications": [
    {
      "ticker": "SYMBOL",
      "entry_price": 0.0,
      "exit_price": 0.0,
      "pnl": 0.0,
      "rung": 0,
      "classification": "GOOD_ENTRY|BAD_ENTRY|UNLUCKY|PREMATURE_EXIT",
      "reasoning": "...",
      "lesson": "..."
    }
  ],
  "veto_analysis": {
    "total_vetoes": 0,
    "good_vetoes": 0,
    "bad_vetoes": 0,
    "ambiguous": 0,
    "data_vetoes": 0,
    "worst_gates": ["gate_name_1", "gate_name_2"],
    "classifications": [
      {
        "ticker": "SYMBOL",
        "gate": "gate_name",
        "classification": "GOOD_VETO|BAD_VETO|AMBIGUOUS|DATA_VETO",
        "reasoning": "..."
      }
    ]
  },
  "root_causes": ["..."],
  "recommendations": ["..."],
  "summary": "1-2 sentence overall assessment"
}

RULES:
1. Base all analysis on the data provided. Do not invent narratives without evidence.
2. If a trade has MFE >> exit price, strongly consider PREMATURE_EXIT.
3. If a trade has MAE > entry_price * 0.02 within first 5 minutes, consider BAD_ENTRY.
4. For veto analysis, check if the price moved >1% in our favour after rejection.
5. If fewer than 3 trades, say "INSUFFICIENT_DATA" and set confidence to 0.2.
6. All recommendations must be specific and actionable (cite gate names, thresholds).
"""


# ---------------------------------------------------------------------------
# WAL event loading
# ---------------------------------------------------------------------------
def _build_wal_candidates(date_str: str) -> List[Path]:
    """Build list of WAL files to scan (same pattern as nightly_v6.py)."""
    candidates = [
        WAL_DIR / "current.ndjson",
        WAL_DIR / f"{date_str}.ndjson",
        WAL_DIR / f"wal_{date_str}.ndjson",
    ]
    archive_dir = WAL_DIR / "archive"
    if archive_dir.exists():
        for f in sorted(archive_dir.glob("*.ndjson")):
            if f not in candidates:
                candidates.append(f)
    return candidates


def _load_wal_events(date_str: str) -> Dict[str, List[Dict[str, Any]]]:
    """Load PositionOpened and PositionClosed events for the given date.

    Returns:
        {"opened": [...], "closed": [...]} with raw WAL payloads.
    """
    opened: List[Dict[str, Any]] = []
    closed: List[Dict[str, Any]] = []

    for wal_path in _build_wal_candidates(date_str):
        if not wal_path.exists():
            continue
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

                    # PositionOpened events
                    if "PositionOpened" in payload:
                        po = payload["PositionOpened"]
                        entry_ns = po.get("entry_time_ns", event.get("time_ns", 0))
                        if entry_ns > 0:
                            entry_date = datetime.fromtimestamp(
                                entry_ns / 1e9, tz=timezone.utc
                            ).strftime("%Y-%m-%d")
                            if entry_date != date_str:
                                continue
                        opened.append(po)

                    # PositionClosed events
                    if "PositionClosed" in payload:
                        pc = payload["PositionClosed"]
                        exit_ns = pc.get("exit_time_ns", 0)
                        if exit_ns > 0:
                            exit_date = datetime.fromtimestamp(
                                exit_ns / 1e9, tz=timezone.utc
                            ).strftime("%Y-%m-%d")
                            if exit_date != date_str:
                                continue
                        closed.append(pc)

        except Exception as e:
            log.warning("Error reading %s: %s", wal_path, e)

    log.info("WAL events for %s: %d opened, %d closed", date_str, len(opened), len(closed))
    return {"opened": opened, "closed": closed}


def _load_gate_vetoes(date_str: str, max_lines: int = 300) -> List[Dict[str, Any]]:
    """Load gate veto events for the given date from gate_vetoes.ndjson."""
    vetoes: List[Dict[str, Any]] = []
    if not GATE_VETOES_FILE.exists():
        return vetoes
    try:
        with open(GATE_VETOES_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = entry.get("timestamp", entry.get("ts", ""))
                if isinstance(ts, str) and ts.startswith(date_str):
                    vetoes.append(entry)
                elif isinstance(ts, (int, float)):
                    evt_date = datetime.fromtimestamp(
                        ts / 1e9, tz=timezone.utc
                    ).strftime("%Y-%m-%d")
                    if evt_date == date_str:
                        vetoes.append(entry)
                if len(vetoes) >= max_lines:
                    break
    except OSError as e:
        log.warning("Failed to read gate vetoes: %s", e)
    return vetoes


def _load_nightly_output() -> Dict[str, Any]:
    """Load nightly_output.json for market condition context."""
    if not NIGHTLY_OUTPUT_FILE.exists():
        return {}
    try:
        with open(NIGHTLY_OUTPUT_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------
def _build_context(
    date_str: str,
    wal_events: Dict[str, List[Dict[str, Any]]],
    vetoes: List[Dict[str, Any]],
) -> str:
    """Build a structured context string for Claude."""
    parts = [f"# AEGIS V2 Forensic Review Data: {date_str}\n"]

    # --- Trades taken ---
    closed = wal_events.get("closed", [])
    if closed:
        parts.append(f"## Trades Closed Today ({len(closed)})")
        for i, pc in enumerate(closed, 1):
            symbol = pc.get("symbol", f"TID_{pc.get('ticker_id', '?')}")
            entry_p = pc.get("entry_price", 0.0)
            exit_p = pc.get("exit_price", 0.0)
            pnl = pc.get("final_pnl", 0.0)
            rung = pc.get("highest_rung", 0)
            strategy = pc.get("strategy", "?")
            confidence = pc.get("confidence", 0)
            mae = pc.get("mae", 0.0)
            mfe = pc.get("mfe", 0.0)
            hold_mins = pc.get("hold_time_mins", 0)
            regime = pc.get("regime_at_entry", pc.get("regime", "?"))
            exchange = pc.get("exchange", "?")
            spread_entry = pc.get("spread_at_entry_pct", 0.0)
            vwap_dist = pc.get("vwap_dist_at_entry_pct", 0.0)

            parts.append(
                f"Trade {i}: {symbol} | entry={entry_p:.4f} exit={exit_p:.4f} "
                f"pnl={pnl:+.4f} | rung={rung} strategy={strategy} "
                f"conf={confidence} | MAE={mae:.4f} MFE={mfe:.4f} "
                f"hold={hold_mins}min | regime={regime} exchange={exchange} "
                f"spread={spread_entry:.3f}% vwap_dist={vwap_dist:.3f}%"
            )
    else:
        parts.append("## No trades closed today")

    # --- Trades opened (for context on still-open positions) ---
    opened = wal_events.get("opened", [])
    if opened:
        parts.append(f"\n## Positions Opened Today ({len(opened)})")
        for po in opened:
            symbol = po.get("symbol", f"TID_{po.get('ticker_id', '?')}")
            entry_p = po.get("entry_price", 0.0)
            strategy = po.get("strategy", "?")
            confidence = po.get("confidence", 0)
            parts.append(f"  Opened: {symbol} entry={entry_p:.4f} strategy={strategy} conf={confidence}")

    # --- Gate vetoes ---
    if vetoes:
        parts.append(f"\n## Gate Vetoes Today ({len(vetoes)})")

        # Summary by gate
        gate_counts: Dict[str, int] = defaultdict(int)
        for v in vetoes:
            gate = v.get("gate", v.get("veto_reason", "unknown"))
            gate_counts[gate] += 1
        parts.append("### Veto Summary by Gate")
        for gate, count in sorted(gate_counts.items(), key=lambda x: -x[1]):
            parts.append(f"  {gate}: {count} vetoes")

        # Detailed examples (up to 20)
        parts.append("\n### Veto Details (sample)")
        for v in vetoes[:20]:
            ticker = v.get("ticker", v.get("symbol", "?"))
            gate = v.get("gate", v.get("veto_reason", "?"))
            price = v.get("price", v.get("last", 0.0))
            indicators = v.get("indicators", {})
            ind_str = ", ".join(
                f"{k}={val}" for k, val in list(indicators.items())[:6]
            ) if indicators else "N/A"
            parts.append(f"  {ticker} vetoed by {gate} price={price:.4f} ({ind_str})")
    else:
        parts.append("\n## No gate vetoes today")

    # --- Market conditions from nightly output ---
    nightly = _load_nightly_output()
    if nightly:
        parts.append("\n## Market Conditions (from nightly_v6)")
        parts.append(f"  Date: {nightly.get('date', '?')}")
        regime = nightly.get("regime", nightly.get("market_regime", "?"))
        parts.append(f"  Regime: {regime}")
        kelly = nightly.get("kelly_fraction", "?")
        parts.append(f"  Recommended Kelly: {kelly}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------
def run_forensic_review(
    date_str: Optional[str] = None,
    dry_run: bool = False,
) -> Optional[Dict[str, Any]]:
    """Execute the forensic review for a given date."""
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    log.info("Forensic review starting for %s", date_str)

    # Load data
    wal_events = _load_wal_events(date_str)
    vetoes = _load_gate_vetoes(date_str)
    closed_count = len(wal_events.get("closed", []))

    # Skip if no data
    if closed_count == 0 and len(vetoes) == 0:
        log.info("No trades and no vetoes for %s -- skipping forensic review", date_str)
        result = {
            "date": date_str,
            "status": "skipped",
            "confidence": 0.0,
            "reason": "no_trades_no_vetoes",
        }
        _save_output(result)
        return result

    # Build context
    context = _build_context(date_str, wal_events, vetoes)
    prompt = (
        f"Perform a forensic review of AEGIS V2 trading for {date_str}. "
        f"There were {closed_count} closed trades and {len(vetoes)} gate vetoes. "
        f"Classify each trade and veto according to the taxonomy in your system prompt. "
        f"Output pure JSON, no markdown code blocks."
    )

    if dry_run:
        print("=" * 60)
        print("  FORENSIC REVIEW DRY RUN")
        print("=" * 60)
        print(f"\nSystem prompt: {len(FORENSIC_SYSTEM_PROMPT)} chars")
        print(f"Context: {len(context)} chars")
        print(f"Trades: {closed_count}, Vetoes: {len(vetoes)}")
        print(f"\n{context}")
        return {"date": date_str, "status": "dry_run", "confidence": 0.0}

    # Call Claude
    full_context = FORENSIC_SYSTEM_PROMPT + "\n\n" + context
    result = claude_analyze_json(
        prompt=prompt,
        context=full_context,
        max_tokens=4096,
        timeout=120,
    )

    if result is None:
        log.error("Forensic review failed -- Claude returned no parseable response")
        result = {
            "date": date_str,
            "status": "failed",
            "confidence": 0.0,
            "error": "claude_no_response",
        }
    else:
        result["date"] = date_str
        result["status"] = result.get("status", "complete")
        result["generated_at"] = datetime.now(timezone.utc).isoformat()
        result["trades_analyzed"] = closed_count
        result["vetoes_analyzed"] = len(vetoes)

    _save_output(result)
    log.info("Forensic review complete: %s", result.get("status", "?"))
    return result


def _save_output(result: Dict[str, Any]) -> None:
    """Save forensic review output to data/claude_forensic_review.json."""
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(OUTPUT_FILE, "w") as f:
            json.dump(result, f, indent=2, default=str)
        log.info("Forensic review saved: %s", OUTPUT_FILE)
    except OSError as e:
        log.error("Failed to save forensic review: %s", e)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [ForensicReview] %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Claude Forensic Review -- post-trade analysis"
    )
    parser.add_argument("--date", type=str, help="Review specific date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Show context without calling Claude")
    parser.add_argument("--json", action="store_true", help="Output result as JSON to stdout")
    args = parser.parse_args()

    result = run_forensic_review(date_str=args.date, dry_run=args.dry_run)

    if result and args.json and not args.dry_run:
        print(json.dumps(result, indent=2, default=str))
    elif result and not args.dry_run and not args.json:
        status = result.get("status", "?")
        summary = result.get("summary", "No summary")
        classifications = result.get("trade_classifications", [])
        print(f"\nForensic review: {status}")
        print(f"Summary: {summary}")
        if classifications:
            for tc in classifications:
                print(f"  {tc.get('ticker', '?')}: {tc.get('classification', '?')} -- {tc.get('reasoning', '')[:80]}")


if __name__ == "__main__":
    main()
