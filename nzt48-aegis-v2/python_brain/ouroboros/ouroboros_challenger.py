"""Sprint S08 -- Ouroboros Parameter Challenger.

Reads Ouroboros nightly recommendations (nightly_output.json), pre-computes
sample size and basic statistics in Python, then calls Claude to challenge
each recommendation. Claude returns one of five verdicts:
  APPLY       -- Sample >= 30, statistically sound, within bounds
  TEST_ONLY   -- Sample 10-29, promising but needs more data
  REJECT      -- Sample < 10, conflicts with doctrine, or statistically weak
  NEEDS_DATA  -- Insufficient information to judge
  OPERATOR_ATTENTION -- WR < 30% or PF < 1.0 or other alarm condition

Output: /app/data/claude/challenges/challenge_YYYY-MM-DD.json

QUARANTINE: Read-only. Never writes to WAL, config, or live trading parameters.
Output goes ONLY to /app/data/claude/challenges/ and optionally Telegram.

Usage:
    python3 -m python_brain.ouroboros.ouroboros_challenger
    python3 -m python_brain.ouroboros.ouroboros_challenger --dry-run
    python3 -m python_brain.ouroboros.ouroboros_challenger --send-telegram
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from python_brain.ouroboros.claude_helper import (
    claude_query,
    load_context_files,
    send_telegram,
    build_context_string,
    load_claude_md,
    MODEL_OPUS,
    get_last_backend,
)

log = logging.getLogger("ouroboros_challenger")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", "/app/config"))
NIGHTLY_OUTPUT_FILE = DATA_DIR / "nightly_output.json"
REPORTS_DIR = DATA_DIR / "ouroboros_reports"
CHALLENGE_DIR = DATA_DIR / "claude" / "challenges"
DYNAMIC_WEIGHTS_FILE = CONFIG_DIR / "dynamic_weights.toml"

# Hard bounds (from CLAUDE.md guardrails)
PARAM_BOUNDS = {
    "kelly_fraction": {"min": 0.10, "max": 0.35, "max_change_pct": 10.0},
    "chandelier_atr_mult": {"min": 1.5, "max": 5.0, "max_change_pct": 20.0},
    "confidence_floor": {"min": 50, "max": 85, "max_change_pct": 20.0},
    "spread_veto_pct": {"min": 0.10, "max": 0.80, "max_change_pct": 20.0},
    "system_velocity_max": {"min": 5, "max": 20, "max_change_pct": 20.0},
}

# ---------------------------------------------------------------------------
# System prompt for challenger
# ---------------------------------------------------------------------------
CHALLENGER_SYSTEM_PROMPT = """You are the Ouroboros Parameter Challenger for AEGIS V2, an autonomous UK ISA leveraged ETP trading engine.

Your role: Challenge each parameter recommendation from the Ouroboros nightly learning loop.
You receive pre-computed statistics and must decide whether each recommendation is sound.

DECISION FRAMEWORK:
- APPLY: Sample size >= 30, statistically significant, within parameter bounds, direction makes sense
- TEST_ONLY: Sample size 10-29, promising but needs more data before production use
- REJECT: Sample size < 10, conflicts with risk doctrine, statistically weak, or violates bounds
- NEEDS_DATA: Insufficient information to make a judgment
- OPERATOR_ATTENTION: System-level alarm -- WR < 30% or PF < 1.0 or parameter drift > 50%

ANALYSIS RULES:
1. Check sample size FIRST -- reject anything with < 10 trades outright
2. Check if the recommendation direction makes statistical sense (does the data support it?)
3. Check if the magnitude is reasonable (max 20% change, max 10% for kelly_fraction)
4. Check for conflicting signals (e.g., raising Kelly while WR is declining)
5. Be conservative: when in doubt, TEST_ONLY rather than APPLY
6. NEVER recommend removing a risk CHECK entirely

OUTPUT FORMAT (pure JSON, no markdown wrapping):
{
  "date": "YYYY-MM-DD",
  "status": "complete",
  "confidence": 0.0-1.0,
  "challenges": [
    {
      "param": "parameter_name",
      "current_value": 0.0,
      "proposed_value": 0.0,
      "verdict": "APPLY/TEST_ONLY/REJECT/NEEDS_DATA/OPERATOR_ATTENTION",
      "reasoning": "Why this verdict",
      "sample_size": 0,
      "stat_significance": "HIGH/MEDIUM/LOW/INSUFFICIENT",
      "risk_direction": "increasing/decreasing/neutral"
    }
  ],
  "system_alerts": ["Any system-level concerns"],
  "overall_assessment": "Brief summary of Ouroboros health"
}

QUANTITATIVE FRAMEWORK (Lopez de Prado, Advances in Financial Machine Learning):
- Apply the Deflated Sharpe Ratio and Multiple Hypothesis Testing problem.
- If Ouroboros recommends a parameter change based on fewer than 100 trades, REJECT due to insufficient statistical power.
- Apply Bonferroni Correction: if multiple parameter shifts were tested, divide the required success threshold by the number of tests.
- Never accept a parameter change that improves Win Rate if it significantly degrades Profit Factor or increases Max Consecutive Losses.
- Default to REJECT. You must be convinced beyond 95% confidence interval that the edge is structural, not a random walk artifact."""


# ---------------------------------------------------------------------------
# Statistics helpers (pre-computed in Python, sent to Claude)
# ---------------------------------------------------------------------------
def _compute_basic_stats(values: List[float]) -> Dict[str, Any]:
    """Compute mean, std, min, max, median for a list of values."""
    if not values:
        return {"n": 0, "mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0, "median": 0.0}
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / max(n - 1, 1)
    std = math.sqrt(variance) if variance > 0 else 0.0
    sorted_vals = sorted(values)
    if n % 2 == 0:
        median = (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0
    else:
        median = sorted_vals[n // 2]
    return {
        "n": n,
        "mean": round(mean, 6),
        "std": round(std, 6),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
        "median": round(median, 6),
    }


def _load_historical_pnls(days: int = 30) -> List[float]:
    """Load daily PnL values from ouroboros_reports for rolling stats."""
    pnls: List[float] = []
    if not REPORTS_DIR.exists():
        return pnls
    json_files = sorted(REPORTS_DIR.glob("*_metrics.json"))
    for jf in json_files[-days:]:
        try:
            with open(jf) as f:
                data = json.load(f)
            pnl = data.get("total_pnl", 0.0)
            if data.get("total_trades", 0) > 0:
                pnls.append(pnl)
        except (json.JSONDecodeError, OSError):
            continue
    return pnls


def _load_historical_metrics(days: int = 30) -> List[Dict[str, Any]]:
    """Load historical metric records for trend analysis."""
    history: List[Dict[str, Any]] = []
    if not REPORTS_DIR.exists():
        return history
    json_files = sorted(REPORTS_DIR.glob("*_metrics.json"))
    for jf in json_files[-days:]:
        try:
            with open(jf) as f:
                history.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue
    return history


def _load_current_dynamic_weights() -> Dict[str, Any]:
    """Load current dynamic_weights.toml as a flat dict for comparison."""
    if not DYNAMIC_WEIGHTS_FILE.exists():
        return {}
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with open(DYNAMIC_WEIGHTS_FILE, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        log.warning("Failed to load dynamic_weights.toml: %s", e)
        return {}


# ---------------------------------------------------------------------------
# Pre-compute recommendation context
# ---------------------------------------------------------------------------
def _precompute_recommendation_stats(
    nightly: Dict[str, Any],
    history: List[Dict[str, Any]],
    current_weights: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """For each recommendation in nightly output, compute stats context."""
    enriched: List[Dict[str, Any]] = []
    recommendations = nightly.get("recommendations", [])

    # Also treat top-level params as implicit recommendations
    for param_key in ["kelly_fraction", "chandelier_atr_mult"]:
        proposed = nightly.get(param_key)
        if proposed is not None:
            # Find current value
            current = None
            if param_key == "kelly_fraction":
                current = current_weights.get("kelly_fractions", {}).get("t1")
            elif param_key == "chandelier_atr_mult":
                current = current_weights.get("exit", {}).get("chandelier_atr_mult")
            if current is not None and current != proposed:
                # Check if already in recommendations list
                already = any(
                    (r.get("param") == param_key if isinstance(r, dict) else False)
                    for r in recommendations
                )
                if not already:
                    recommendations.append({
                        "param": param_key,
                        "old": current,
                        "new": proposed,
                        "reason": f"Ouroboros nightly computed {param_key}={proposed}",
                    })

    # Compute rolling stats from history
    total_trades_history = sum(h.get("total_trades", 0) for h in history)
    win_rates = [h.get("win_rate", 0) for h in history if h.get("total_trades", 0) > 0]
    pnls = [h.get("total_pnl", 0) for h in history if h.get("total_trades", 0) > 0]
    pnl_stats = _compute_basic_stats(pnls)
    wr_stats = _compute_basic_stats(win_rates)

    # Compute profit factor from history
    gross_wins = sum(h.get("total_pnl", 0) for h in history if h.get("total_pnl", 0) > 0)
    gross_losses = abs(sum(h.get("total_pnl", 0) for h in history if h.get("total_pnl", 0) < 0))
    pf = gross_wins / gross_losses if gross_losses > 0 else 999.0

    for rec in recommendations:
        if isinstance(rec, str):
            # String-form recommendation (not parameterized)
            enriched.append({
                "param": "note",
                "description": rec,
                "current_value": None,
                "proposed_value": None,
                "sample_size": total_trades_history,
                "stats": {
                    "pnl": pnl_stats,
                    "win_rate": wr_stats,
                    "profit_factor": round(pf, 2),
                    "total_trades": total_trades_history,
                },
            })
            continue

        param = rec.get("param", "unknown")
        old_val = rec.get("old")
        new_val = rec.get("new")
        reason = rec.get("reason", "")

        # Look up bounds
        bounds = PARAM_BOUNDS.get(param, {})
        within_bounds = True
        if bounds and new_val is not None:
            try:
                nv = float(new_val)
                within_bounds = bounds.get("min", -9999) <= nv <= bounds.get("max", 9999)
            except (TypeError, ValueError):
                within_bounds = True  # Can't check non-numeric

        # Compute change magnitude
        change_pct = 0.0
        if old_val is not None and new_val is not None:
            try:
                ov = float(old_val)
                nv = float(new_val)
                if ov != 0:
                    change_pct = abs(nv - ov) / abs(ov) * 100.0
            except (TypeError, ValueError):
                pass

        # Determine risk direction
        risk_direction = "neutral"
        if param == "kelly_fraction" and new_val is not None and old_val is not None:
            risk_direction = "increasing" if float(new_val) > float(old_val) else "decreasing"
        elif param == "chandelier_atr_mult" and new_val is not None and old_val is not None:
            # Tighter Chandelier = more risk (stops closer)
            risk_direction = "increasing" if float(new_val) < float(old_val) else "decreasing"
        elif param == "confidence_floor" and new_val is not None and old_val is not None:
            # Lower floor = more trades = more risk
            risk_direction = "increasing" if float(new_val) < float(old_val) else "decreasing"

        enriched.append({
            "param": param,
            "current_value": old_val,
            "proposed_value": new_val,
            "change_pct": round(change_pct, 2),
            "reason": reason,
            "within_bounds": within_bounds,
            "bounds": bounds,
            "risk_direction": risk_direction,
            "sample_size": total_trades_history,
            "stats": {
                "pnl": pnl_stats,
                "win_rate": wr_stats,
                "profit_factor": round(pf, 2),
                "total_trades": total_trades_history,
            },
        })

    return enriched


# ---------------------------------------------------------------------------
# Build prompt for Claude
# ---------------------------------------------------------------------------
def _build_challenge_prompt(
    date_str: str,
    nightly: Dict[str, Any],
    enriched_recs: List[Dict[str, Any]],
    current_weights: Dict[str, Any],
) -> str:
    """Build the challenger prompt with pre-computed statistics."""
    parts = [f"# Ouroboros Parameter Challenge: {date_str}\n"]

    # Current system state
    bayesian = current_weights.get("bayesian", {})
    parts.append("## Current System State")
    parts.append(f"- Bayesian WR: {bayesian.get('win_rate', 0):.2%}")
    parts.append(f"- Trade Count: {bayesian.get('trade_count', 0)}")
    parts.append(f"- Sharpe Ratio: {bayesian.get('sharpe_ratio', 0):.2f}")
    parts.append(f"- DSR: {bayesian.get('dsr', 0):.4f} (significant: {bayesian.get('dsr_significant', False)})")

    kelly_section = current_weights.get("kelly_fractions", {})
    parts.append(f"- Kelly T1: {kelly_section.get('t1', 0):.4f}")

    exit_section = current_weights.get("exit", {})
    parts.append(f"- Chandelier ATR: {exit_section.get('chandelier_atr_mult', 0):.2f}")

    signal_section = current_weights.get("signal", {})
    parts.append(f"- Confidence Floor: {signal_section.get('confidence_floor', 65)}")

    # Enriched recommendations
    if not enriched_recs:
        parts.append("\n## No recommendations to challenge")
        parts.append("Ouroboros produced no parameter changes. Report system health only.")
    else:
        parts.append(f"\n## Recommendations to Challenge ({len(enriched_recs)})")
        for i, rec in enumerate(enriched_recs, 1):
            parts.append(f"\n### Recommendation {i}: {rec['param']}")
            parts.append(f"- Current: {rec.get('current_value', '?')}")
            parts.append(f"- Proposed: {rec.get('proposed_value', '?')}")
            parts.append(f"- Change: {rec.get('change_pct', 0):.1f}%")
            parts.append(f"- Within bounds: {rec.get('within_bounds', '?')}")
            parts.append(f"- Risk direction: {rec.get('risk_direction', '?')}")
            parts.append(f"- Reason: {rec.get('reason', 'N/A')}")

            stats = rec.get("stats", {})
            parts.append(f"- Sample size: {stats.get('total_trades', 0)} trades over rolling window")
            pnl = stats.get("pnl", {})
            parts.append(f"- PnL stats: mean={pnl.get('mean', 0):.2f}, std={pnl.get('std', 0):.2f}, "
                         f"median={pnl.get('median', 0):.2f}")
            wr = stats.get("win_rate", {})
            parts.append(f"- WR stats: mean={wr.get('mean', 0):.2%}, std={wr.get('std', 0):.2%}")
            parts.append(f"- Profit Factor: {stats.get('profit_factor', 0):.2f}")

            if rec.get("bounds"):
                b = rec["bounds"]
                parts.append(f"- Hard bounds: [{b.get('min', '?')}, {b.get('max', '?')}], "
                             f"max change: {b.get('max_change_pct', '?')}%/cycle")

    parts.append("\n---\nChallenge each recommendation. Output pure JSON, no markdown code blocks.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------
def run_challenger(
    dry_run: bool = False,
    send_tg: bool = False,
) -> Optional[Dict[str, Any]]:
    """Execute the Ouroboros challenger workflow."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.info("S08: Starting Ouroboros challenger for %s", date_str)

    # Load nightly output
    if not NIGHTLY_OUTPUT_FILE.exists():
        log.warning("No nightly_output.json found -- nothing to challenge")
        return {"date": date_str, "status": "skipped", "confidence": 0, "reason": "no_nightly_output"}

    try:
        with open(NIGHTLY_OUTPUT_FILE) as f:
            nightly = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error("Failed to load nightly output: %s", e)
        return None

    # Load supporting data
    history = _load_historical_metrics(days=30)
    current_weights = _load_current_dynamic_weights()

    # Pre-compute statistics for each recommendation
    enriched_recs = _precompute_recommendation_stats(nightly, history, current_weights)
    log.info("Pre-computed stats for %d recommendations", len(enriched_recs))

    # Build prompt
    prompt = _build_challenge_prompt(date_str, nightly, enriched_recs, current_weights)

    if dry_run:
        print("=" * 60)
        print("  S08 DRY RUN -- Challenger prompt")
        print("=" * 60)
        print(f"\nSystem prompt: {len(CHALLENGER_SYSTEM_PROMPT)} chars")
        print(f"User prompt: {len(prompt)} chars")
        print(f"\n{prompt}")
        return {"date": date_str, "status": "dry_run", "confidence": 0}

    # Call Claude
    claude_md = load_claude_md()
    system_ctx = CHALLENGER_SYSTEM_PROMPT
    if claude_md:
        system_ctx = claude_md + "\n\n" + CHALLENGER_SYSTEM_PROMPT

    start_time = time.time()
    result = claude_query(prompt, system_context=system_ctx, model=MODEL_OPUS)
    elapsed = time.time() - start_time

    if result is None:
        log.error("Claude challenger returned no response")
        return None

    result["_latency_s"] = round(elapsed, 2)
    result["_cost_usd"] = 0.0
    result["generated_at"] = datetime.now(timezone.utc).isoformat()

    # Ensure date and status fields
    result.setdefault("date", date_str)
    result.setdefault("status", "complete")
    result.setdefault("confidence", 0.5)

    # Save challenge output
    CHALLENGE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = CHALLENGE_DIR / f"challenge_{date_str}.json"
    try:
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2, default=str)
        log.info("Challenge saved: %s", output_path)
    except OSError as e:
        log.error("Failed to save challenge: %s", e)

    # Send Telegram
    if send_tg:
        _send_telegram_summary(result)

    return result


def _send_telegram_summary(result: Dict[str, Any]):
    """Send challenger summary via Telegram."""
    date_str = result.get("date", "?")
    challenges = result.get("challenges", [])
    alerts = result.get("system_alerts", [])

    lines = [
        "<b>OUROBOROS CHALLENGER</b>",
        f"Date: {date_str}",
        "",
    ]

    # Summarize verdicts
    verdict_counts: Dict[str, int] = {}
    for c in challenges:
        v = c.get("verdict", "UNKNOWN")
        verdict_counts[v] = verdict_counts.get(v, 0) + 1

    for verdict, count in sorted(verdict_counts.items()):
        lines.append(f"  {verdict}: {count}")

    # Detail each challenge
    for c in challenges[:5]:
        param = c.get("param", "?")
        verdict = c.get("verdict", "?")
        reasoning = c.get("reasoning", "")[:100]
        lines.append(f"\n<b>{param}</b>: {verdict}")
        if reasoning:
            lines.append(f"  {reasoning}")

    # System alerts
    if alerts:
        lines.append("\n<b>Alerts:</b>")
        for a in alerts[:3]:
            lines.append(f"  - {a}")

    lines.append(f"\n<i>Latency: {result.get('_latency_s', 0):.0f}s</i>")

    send_telegram("\n".join(lines))
    log.info("Telegram challenger summary sent")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [Challenger] %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Ouroboros Parameter Challenger (Sprint S08)")
    parser.add_argument("--dry-run", action="store_true", help="Show prompt without calling Claude")
    parser.add_argument("--send-telegram", action="store_true", help="Send summary via Telegram")
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    args = parser.parse_args()

    result = run_challenger(
        dry_run=args.dry_run,
        send_tg=args.send_telegram,
    )

    if result and args.json and not args.dry_run:
        print(json.dumps(result, indent=2, default=str))
    elif result and not args.dry_run and not args.json:
        challenges = result.get("challenges", [])
        print(f"\nChallenger complete: {len(challenges)} recommendations challenged")
        for c in challenges:
            print(f"  {c.get('param', '?')}: {c.get('verdict', '?')}")


if __name__ == "__main__":
    main()
