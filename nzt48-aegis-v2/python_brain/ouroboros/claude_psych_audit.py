"""Sprint S18: Claude Operator Psychological Audit.

Reads WAL events for operator interventions (kill, pause, resume, manual
override actions). Compares what the operator did vs what the engine would
have done (counterfactual analysis). Claude analyzes intervention patterns:
panic sells, premature kills, revenge trades, etc.

Usage:
  python3 -m python_brain.ouroboros.claude_psych_audit [--send-telegram]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List

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
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))
REVIEW_DIR = DATA_DIR / "claude" / "reviews"

# WAL event types that indicate operator intervention
INTERVENTION_EVENTS = {
    "OperatorKill",
    "OperatorPause",
    "OperatorResume",
    "ManualClose",
    "ManualFlatten",
    "OperatorOverride",
    "SystemHalt",
    "SystemResume",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Claude-PsychAudit] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("claude_psych_audit")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def get_week_label() -> str:
    """Get ISO week label like 2026-W12."""
    now = datetime.now(timezone.utc)
    return f"{now.year}-W{now.isocalendar()[1]:02d}"


def load_week_wal_events() -> List[Dict[str, Any]]:
    """Load all WAL events from the past 7 days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    cutoff_ns = int(cutoff.timestamp() * 1e9)
    events: List[Dict[str, Any]] = []

    # Collect WAL files
    wal_files: List[Path] = []
    current = WAL_DIR / "current.ndjson"
    if current.exists():
        wal_files.append(current)
    archive_dir = WAL_DIR / "archive"
    if archive_dir.exists():
        for f in sorted(archive_dir.glob("*.ndjson")):
            wal_files.append(f)

    for wal_file in wal_files:
        try:
            with open(wal_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    ts_ns = event.get("event_time_ns", event.get("ts", 0))
                    if ts_ns >= cutoff_ns:
                        events.append(event)
        except IOError as e:
            log.warning("Error reading %s: %s", wal_file, e)

    log.info("Loaded %d WAL events from past 7 days", len(events))
    return events


def extract_interventions(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract operator intervention events from WAL."""
    interventions: List[Dict[str, Any]] = []

    for event in events:
        payload = event.get("payload", {})
        for event_type in INTERVENTION_EVENTS:
            if event_type in payload:
                ts_ns = event.get("event_time_ns", event.get("ts", 0))
                try:
                    dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
                    ts_str = dt.isoformat()
                except (OSError, ValueError):
                    ts_str = str(ts_ns)

                intervention = {
                    "type": event_type,
                    "timestamp": ts_str,
                    "timestamp_ns": ts_ns,
                    "details": payload[event_type],
                }
                interventions.append(intervention)
                break

    interventions.sort(key=lambda x: x["timestamp_ns"])
    return interventions


def extract_trades_around_interventions(
    events: List[Dict[str, Any]],
    interventions: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Extract PositionClosed events near intervention times for counterfactual."""
    WINDOW_NS = 30 * 60 * 1_000_000_000  # 30 minutes

    nearby_trades: List[Dict[str, Any]] = []
    intervention_times = [i["timestamp_ns"] for i in interventions]

    for event in events:
        payload = event.get("payload", {})
        if "PositionClosed" not in payload:
            continue

        pc = payload["PositionClosed"]
        exit_ns = pc.get("exit_time_ns", event.get("event_time_ns", 0))

        # Check if this trade closed near any intervention
        for int_time in intervention_times:
            if abs(exit_ns - int_time) < WINDOW_NS:
                nearby_trades.append({
                    "symbol": pc.get("symbol", ""),
                    "entry_price": pc.get("entry_price", 0),
                    "exit_price": pc.get("exit_price", 0),
                    "final_pnl": pc.get("final_pnl", 0),
                    "highest_rung": pc.get("highest_rung", 0),
                    "mae": pc.get("mae", 0),
                    "mfe": pc.get("mfe", 0),
                    "exit_reason": pc.get("exit_reason", "unknown"),
                    "exit_time_ns": exit_ns,
                    "near_intervention_ns": int_time,
                })
                break

    return nearby_trades


def build_intervention_summary(
    interventions: List[Dict[str, Any]],
    nearby_trades: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Pre-calculate intervention pattern statistics."""
    type_counts: Dict[str, int] = defaultdict(int)
    by_hour: Dict[int, int] = defaultdict(int)
    by_day: Dict[str, int] = defaultdict(int)

    for intervention in interventions:
        type_counts[intervention["type"]] += 1
        try:
            dt = datetime.fromisoformat(intervention["timestamp"])
            by_hour[dt.hour] += 1
            by_day[dt.strftime("%A")] += 1
        except (ValueError, TypeError):
            pass

    # Counterfactual: how did trades near interventions perform?
    intervention_trades_pnl = sum(t["final_pnl"] for t in nearby_trades)
    intervention_trades_count = len(nearby_trades)
    manual_exits = [t for t in nearby_trades if "manual" in t.get("exit_reason", "").lower()
                    or "operator" in t.get("exit_reason", "").lower()]
    premature_kills = [t for t in manual_exits if t["mfe"] > abs(t["final_pnl"]) * 2]

    return {
        "total_interventions": len(interventions),
        "type_counts": dict(type_counts),
        "peak_hour": max(by_hour, key=by_hour.get) if by_hour else None,
        "peak_day": max(by_day, key=by_day.get) if by_day else None,
        "by_hour": dict(by_hour),
        "by_day": dict(by_day),
        "nearby_trades_count": intervention_trades_count,
        "nearby_trades_pnl": round(intervention_trades_pnl, 2),
        "manual_exits_count": len(manual_exits),
        "premature_kill_count": len(premature_kills),
    }


# ---------------------------------------------------------------------------
# Claude analysis
# ---------------------------------------------------------------------------
def build_psych_prompt(
    interventions: List[Dict[str, Any]],
    nearby_trades: List[Dict[str, Any]],
    summary: Dict[str, Any],
    week_label: str,
) -> str:
    """Build prompt for Claude psychological analysis."""
    interventions_str = json.dumps(interventions[:50], indent=2)
    trades_str = json.dumps(nearby_trades[:30], indent=2)
    summary_str = json.dumps(summary, indent=2)

    return f"""You are a trading psychology analyst reviewing operator behavior for week {week_label}.

INTERVENTION EVENTS (operator actions that override the engine):
{interventions_str}

TRADES NEAR INTERVENTIONS (closed within 30min of operator action):
{trades_str}

PRE-CALCULATED SUMMARY:
{summary_str}

Analyze the operator's intervention patterns and identify:

1. PANIC INDICATORS: Kills/pauses during drawdowns that would have recovered
2. PREMATURE EXITS: Manual closes where MFE >> |final_pnl| (left money on the table)
3. REVENGE PATTERNS: Rapid resume after pause, or increased activity after losses
4. TIME PATTERNS: Do interventions cluster at specific times (market open, lunch, close)?
5. COUNTERFACTUAL: What would PnL have been if operator had NOT intervened?
6. POSITIVE PATTERNS: Any interventions that were genuinely correct (e.g., pre-news flatten)

Classify operator behavior:
- DISCIPLINED: Interventions are rare, justified, and improve outcomes
- REACTIVE: Interventions are emotionally driven but not catastrophic
- DESTRUCTIVE: Interventions consistently worsen outcomes
- INSUFFICIENT_DATA: Not enough interventions to assess (<5)

Return JSON:
{{
  "date": "{week_label}",
  "status": "ok",
  "confidence": "HIGH|MEDIUM|LOW",
  "sample_size": {len(interventions)},
  "operator_classification": "DISCIPLINED|REACTIVE|DESTRUCTIVE|INSUFFICIENT_DATA",
  "patterns_detected": [
    {{
      "pattern": "<pattern name>",
      "severity": "LOW|MEDIUM|HIGH",
      "evidence": "<specific examples>",
      "recommendation": "<what to change>"
    }}
  ],
  "counterfactual_pnl_estimate": <float>,
  "intervention_cost_estimate": <float>,
  "positive_interventions": <int>,
  "summary": "<2-3 sentence assessment with specific advice>"
}}"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_psych_audit(send_tg: bool = False) -> int:
    """Execute weekly operator psychological audit."""
    week_label = get_week_label()
    log.info("Operator psychological audit starting for %s", week_label)

    # Load WAL events
    events = load_week_wal_events()
    if not events:
        log.info("No WAL events found for the past week")
        return 0

    # Extract interventions
    interventions = extract_interventions(events)
    log.info("Found %d operator interventions this week", len(interventions))

    if not interventions:
        log.info("No operator interventions this week — nothing to audit")
        return 0

    # Extract nearby trades for counterfactual
    nearby_trades = extract_trades_around_interventions(events, interventions)
    log.info("Found %d trades near intervention times", len(nearby_trades))

    # Pre-calculate summary
    summary = build_intervention_summary(interventions, nearby_trades)
    log.info(
        "Intervention summary: %d total, peak_hour=%s, manual_exits=%d, premature_kills=%d",
        summary["total_interventions"],
        summary["peak_hour"],
        summary["manual_exits_count"],
        summary["premature_kill_count"],
    )

    # Query Claude
    prompt = build_psych_prompt(interventions, nearby_trades, summary, week_label)
    context = load_context_files([
        str(DATA_DIR / "persistent_memory.json"),
    ])
    system_ctx = build_context_string(context)

    result = claude_query(prompt, system_context=system_ctx)
    if result is None:
        log.error("Claude query failed — no audit result")
        return 1

    # Enrich with pre-calculated data
    result["pre_calculated_summary"] = summary
    result["interventions_analyzed"] = len(interventions)

    # Write output
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    output_path = REVIEW_DIR / f"psych_audit_{week_label}.json"
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)
    log.info("Psychological audit written: %s", output_path)

    # Log findings
    classification = result.get("operator_classification", "UNKNOWN")
    patterns = result.get("patterns_detected", [])
    log.info("Operator classification: %s", classification)
    for pattern in patterns:
        log.info(
            "  Pattern: %s (severity=%s) — %s",
            pattern.get("pattern", "?"),
            pattern.get("severity", "?"),
            pattern.get("recommendation", ""),
        )

    # Telegram
    if send_tg:
        cost = result.get("intervention_cost_estimate", 0)
        msg = (
            f"<b>Psych Audit {week_label}</b>\n"
            f"Classification: {classification}\n"
            f"Interventions: {len(interventions)}\n"
            f"Patterns: {len(patterns)}\n"
            f"Est. intervention cost: GBP {cost:+.2f}\n"
            f"{result.get('summary', '')}"
        )
        send_telegram(msg)

    return 0


def main():
    parser = argparse.ArgumentParser(description="Claude Operator Psych Audit (Sprint S18)")
    parser.add_argument("--send-telegram", action="store_true", help="Send summary via Telegram")
    args = parser.parse_args()

    try:
        sys.exit(run_psych_audit(send_tg=args.send_telegram))
    except Exception as e:
        log.error("Claude psych audit crashed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
