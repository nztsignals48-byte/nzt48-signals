"""Sprint S14: Claude Weekly Gate Calibration — Rejected Signal Review.

Reads gate_vetoes.ndjson for the entire week, aggregates per-gate stats,
pre-calculates bad veto rate per gate (Python math), then asks Claude to
interpret and recommend: TIGHTEN / LOOSEN / KEEP / NEEDS_DATA.

Usage: python3 -m python_brain.ouroboros.claude_rejected_review [--send-telegram]
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
GATE_VETOES_PATH = DATA_DIR / "gate_vetoes.ndjson"
GATE_VETOES_ARCHIVE = DATA_DIR / "gate_vetoes_archive"
REVIEW_DIR = DATA_DIR / "claude" / "rejected_reviews"

# Minimum samples for Claude to recommend gate changes
MIN_VETOES_FOR_RECOMMENDATION = 10
# Minimum confidence for gate adjustment
CONFIDENCE_THRESHOLD = 50  # from CLAUDE.md: 50 for gate tuning

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Claude-RejectedReview] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("claude_rejected_review")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def get_week_label() -> str:
    """Get ISO week label like 2026-W12."""
    now = datetime.now(timezone.utc)
    return f"{now.year}-W{now.isocalendar()[1]:02d}"


def load_week_vetoes() -> List[Dict[str, Any]]:
    """Load gate vetoes from current file + archives for the past 7 days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    vetoes: List[Dict[str, Any]] = []

    # Collect all veto files: current + archived
    veto_files: List[Path] = []
    if GATE_VETOES_PATH.exists():
        veto_files.append(GATE_VETOES_PATH)
    if GATE_VETOES_ARCHIVE.exists():
        for f in sorted(GATE_VETOES_ARCHIVE.glob("*.ndjson")):
            veto_files.append(f)

    for veto_file in veto_files:
        try:
            with open(veto_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Filter by timestamp — only last 7 days
                    ts_str = event.get("timestamp", "")
                    if ts_str:
                        try:
                            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                            if ts < cutoff:
                                continue
                        except (ValueError, TypeError):
                            pass  # Include if timestamp unparseable
                    vetoes.append(event)
        except IOError as e:
            log.warning("Error reading %s: %s", veto_file, e)

    log.info("Loaded %d vetoes from past 7 days across %d files", len(vetoes), len(veto_files))
    return vetoes


# ---------------------------------------------------------------------------
# Per-gate aggregation (Python pre-calculation)
# ---------------------------------------------------------------------------
def aggregate_per_gate(vetoes: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Aggregate veto statistics per gate.

    Returns dict of gate_name -> {
        total_vetoes, symbols, avg_confidence, indicator_values,
        bad_veto_count, good_veto_count, ambiguous_count, bad_veto_rate
    }
    """
    gates: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "total_vetoes": 0,
        "symbols": set(),
        "confidences": [],
        "classifications": defaultdict(int),
        "indicator_snapshots": [],
    })

    for veto in vetoes:
        gate = veto.get("gate", veto.get("gate_name", "unknown"))
        g = gates[gate]
        g["total_vetoes"] += 1
        sym = veto.get("symbol", veto.get("ticker", ""))
        if sym:
            g["symbols"].add(sym)
        conf = veto.get("confidence", 0.0)
        if conf > 0:
            g["confidences"].append(conf)

        # Classification (from missed_winner_detector or inline)
        cls = veto.get("classification", "UNKNOWN")
        g["classifications"][cls] += 1

        # Collect indicator snapshot for context
        snapshot = {}
        for key in ("adx", "rvol", "hurst", "spread_pct", "vol_slope", "atr"):
            if key in veto:
                snapshot[key] = veto[key]
        if snapshot:
            g["indicator_snapshots"].append(snapshot)

    # Compute summary stats
    result: Dict[str, Dict[str, Any]] = {}
    for gate, g in gates.items():
        total = g["total_vetoes"]
        bad = g["classifications"].get("BAD_VETO", 0)
        good = g["classifications"].get("GOOD_VETO", 0)
        ambiguous = g["classifications"].get("AMBIGUOUS", 0) + g["classifications"].get("UNKNOWN", 0)

        # Average indicator values
        avg_indicators: Dict[str, float] = {}
        if g["indicator_snapshots"]:
            all_keys = set()
            for snap in g["indicator_snapshots"]:
                all_keys.update(snap.keys())
            for key in all_keys:
                vals = [s[key] for s in g["indicator_snapshots"] if key in s]
                if vals:
                    avg_indicators[key] = round(sum(vals) / len(vals), 4)

        result[gate] = {
            "total_vetoes": total,
            "symbols": sorted(g["symbols"])[:20],
            "symbol_count": len(g["symbols"]),
            "avg_confidence": round(sum(g["confidences"]) / max(len(g["confidences"]), 1), 2),
            "bad_veto_count": bad,
            "good_veto_count": good,
            "ambiguous_count": ambiguous,
            "bad_veto_rate": round(bad / max(total, 1) * 100, 1),
            "good_veto_rate": round(good / max(total, 1) * 100, 1),
            "avg_indicators": avg_indicators,
        }

    return result


# ---------------------------------------------------------------------------
# Claude review
# ---------------------------------------------------------------------------
def build_review_prompt(
    gate_stats: Dict[str, Dict[str, Any]],
    week_label: str,
) -> str:
    """Build prompt for Claude to review gate calibration."""
    stats_str = json.dumps(gate_stats, indent=2)

    return f"""You are the AEGIS V2 gate calibration reviewer for week {week_label}.

Below are per-gate statistics from this week's vetoed signals. Each gate has:
- total_vetoes: how many signals this gate blocked
- bad_veto_rate: percentage of vetoes that turned out to be missed winners (BAD_VETO)
- good_veto_rate: percentage of vetoes that correctly prevented losses (GOOD_VETO)
- avg_indicators: average indicator values at time of veto
- symbol_count: how many unique tickers were affected

GATE STATISTICS:
{stats_str}

CALIBRATION RULES:
- bad_veto_rate > 30% with N >= {MIN_VETOES_FOR_RECOMMENDATION} → recommend LOOSEN (gate is too tight)
- bad_veto_rate < 10% with N >= {MIN_VETOES_FOR_RECOMMENDATION} → recommend TIGHTEN (gate could be stricter)
- bad_veto_rate 10-30% → recommend KEEP (gate is calibrated well)
- N < {MIN_VETOES_FOR_RECOMMENDATION} → recommend NEEDS_DATA (insufficient samples)
- NEVER recommend removing a gate entirely

For each gate, provide a recommendation with:
- action: TIGHTEN / LOOSEN / KEEP / NEEDS_DATA
- threshold_adjustment: suggested threshold change (e.g., "+5" for ADX, "-0.2" for RVOL)
- reasoning: 1-sentence explanation

Return JSON:
{{
  "date": "{week_label}",
  "status": "ok",
  "confidence": "HIGH|MEDIUM|LOW",
  "sample_size": <total vetoes this week>,
  "gate_recommendations": {{
    "gate_name": {{
      "action": "TIGHTEN|LOOSEN|KEEP|NEEDS_DATA",
      "bad_veto_rate": <float>,
      "sample_size": <int>,
      "threshold_adjustment": "<description>",
      "reasoning": "<explanation>"
    }}
  }},
  "summary": "<1-2 sentence overall assessment>"
}}"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_review(send_tg: bool = False) -> int:
    """Execute weekly gate calibration review."""
    week_label = get_week_label()
    log.info("Weekly gate calibration review starting for %s", week_label)

    # Load vetoes
    vetoes = load_week_vetoes()
    if not vetoes:
        log.info("No vetoes found for the past week — nothing to review")
        return 0

    # Aggregate per gate
    gate_stats = aggregate_per_gate(vetoes)
    log.info("Aggregated stats for %d gates:", len(gate_stats))
    for gate, stats in sorted(gate_stats.items(), key=lambda x: -x[1]["total_vetoes"]):
        log.info(
            "  %s: %d vetoes, bad=%.1f%%, good=%.1f%%, %d symbols",
            gate, stats["total_vetoes"], stats["bad_veto_rate"],
            stats["good_veto_rate"], stats["symbol_count"],
        )

    # Query Claude
    prompt = build_review_prompt(gate_stats, week_label)
    context = load_context_files([
        str(CONFIG_DIR / "config.toml"),
        str(CONFIG_DIR / "dynamic_weights.toml"),
    ])
    system_ctx = build_context_string(context)

    result = claude_query(prompt, system_context=system_ctx)
    if result is None:
        log.error("Claude query failed — no review result")
        return 1

    # Enrich with pre-calculated stats
    result["pre_calculated_gate_stats"] = gate_stats
    result["total_vetoes_analyzed"] = len(vetoes)

    # Write output
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    output_path = REVIEW_DIR / f"review_{week_label}.json"
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)
    log.info("Review written: %s", output_path)

    # Log recommendations
    recs = result.get("gate_recommendations", {})
    for gate, rec in recs.items():
        action = rec.get("action", "UNKNOWN")
        reasoning = rec.get("reasoning", "")
        log.info("  %s -> %s: %s", gate, action, reasoning)

    # Telegram
    if send_tg:
        loosen_gates = [g for g, r in recs.items() if r.get("action") == "LOOSEN"]
        tighten_gates = [g for g, r in recs.items() if r.get("action") == "TIGHTEN"]
        msg = (
            f"<b>Gate Calibration Review {week_label}</b>\n"
            f"Vetoes analyzed: {len(vetoes)}\n"
            f"Gates reviewed: {len(recs)}\n"
            f"LOOSEN: {', '.join(loosen_gates) or 'none'}\n"
            f"TIGHTEN: {', '.join(tighten_gates) or 'none'}\n"
            f"Confidence: {result.get('confidence', 'LOW')}"
        )
        send_telegram(msg)

    return 0


def main():
    parser = argparse.ArgumentParser(description="Claude Weekly Gate Calibration (Sprint S14)")
    parser.add_argument("--send-telegram", action="store_true", help="Send summary via Telegram")
    args = parser.parse_args()

    try:
        sys.exit(run_review(send_tg=args.send_telegram))
    except Exception as e:
        log.error("Claude rejected review crashed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
