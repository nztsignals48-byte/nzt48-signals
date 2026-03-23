"""N6a -- Claude Nightly Review Module (Sprint S07).

Runs after nightly_v6 (04:52 UTC). Reads the ResearchContextStore output,
gate vetoes, missed winners, and trade data. Calls Claude via claude_helper
(CLI-based, $0/call via Max subscription) and produces a structured JSON
review with per-trade narrative classification and recommended actions.

QUARANTINE: This module is READ-ONLY. It NEVER writes to WAL, config.toml,
dynamic_weights.toml, or any live trading parameter. Output goes only to
/app/data/claude/reviews/ and optionally Telegram.

Usage:
    python3 -m python_brain.ouroboros.claude_review                     # Full nightly review
    python3 -m python_brain.ouroboros.claude_review --dry-run            # Show prompt, don't call API
    python3 -m python_brain.ouroboros.claude_review --send-telegram      # Send summary via Telegram
    python3 -m python_brain.ouroboros.claude_review --date 2026-03-20    # Review specific date
"""
from __future__ import annotations

import json
import logging
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
)

log = logging.getLogger("claude_review")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
REVIEW_DIR = DATA_DIR / "claude" / "reviews"
RESEARCH_DIR = DATA_DIR / "research"
INCIDENTS_DIR = RESEARCH_DIR / "incidents"
CONTEXT_FILE = RESEARCH_DIR / "context_store.json"
GATE_VETOES_FILE = DATA_DIR / "gate_vetoes.ndjson"
NIGHTLY_OUTPUT_FILE = DATA_DIR / "nightly_output.json"

# ---------------------------------------------------------------------------
# System prompt (with W1-W5 / L1-L7 from CLAUDE.md taxonomy)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are the nightly trade reviewer for AEGIS V2, an autonomous UK ISA leveraged ETP trading engine.

Your role: Analyze today's trading performance and provide actionable insights.

CONTEXT:
- The system trades 3x/5x leveraged ETPs across LSE, US, HK, and other exchanges.
- Strategies: TypeA-F (TypeA=dip recovery, TypeB=early runner, TypeC=exhaustion, TypeD=bounce, TypeE=capitulation, TypeF=OBV divergence), Orchestrator (VWAP/Gap/RSI/Momentum)
- Exit: 5-rung Chandelier trailing stop (0.8%->1.5%->2.5%->4.0% gain thresholds)
- Risk: 33-check arbiter, confidence floor, spread veto, max trades/day
- Current phase: Paper trading (GBP 10,000 starting equity)

OUTPUT FORMAT (pure JSON, no markdown wrapping):
{
  "date": "YYYY-MM-DD",
  "status": "complete",
  "executive_summary": "1-2 sentence summary of the day",
  "performance_grade": "A/B/C/D/F",
  "confidence": 0.0-1.0,
  "trade_narratives": [
    {
      "ticker": "QQQ3.L",
      "classification": "W1-W5 or L1-L7",
      "narrative": "What happened and why",
      "lesson": "What to learn from this trade"
    }
  ],
  "root_causes": ["Root cause 1", "Root cause 2"],
  "gate_tuning": [
    {
      "gate": "gate_name",
      "recommendation": "tighten/loosen/keep",
      "confidence": 0.0-1.0,
      "sample_size": 0,
      "reasoning": "Why this change"
    }
  ],
  "veto_analysis": {
    "total_vetoes": 0,
    "good_vetoes": 0,
    "bad_vetoes": 0,
    "worst_gates": ["gate1", "gate2"]
  },
  "risk_alerts": ["Any risk concerns"],
  "tomorrow_watchlist": ["Tickers to watch"],
  "overall_confidence": 0.0-1.0
}

TRADE CLASSIFICATION TAXONOMY (from CLAUDE.md):
Winners:
  W1: Clean Trend -- EMA cross + sustained trend, Rung 3+ exit
  W2: Grind -- Small, steady gains ground out over time
  W3: Rung Climber -- Progressive rung advancement through the ladder
  W4: VWAP Reclaim -- Pullback to VWAP, bounce and profit
  W5: Macro Surf -- Caught a macro-driven move (sector rotation, news)

Losers:
  L1: Spread Victim -- Spread cost exceeded the available edge
  L2: Stop Hunted -- Leveraged ETP whipsaw triggered Chandelier exit
  L3: Late Entry -- Entered after the move was exhausted
  L4: Macro Crush -- Adverse macro event killed the position
  L5: Regime Mismatch -- Momentum strategy in mean-reverting market
  L6: Fake Breakout -- Entered on false breakout, immediate reversal
  L7: Time Decay -- Position drifted sideways, exited on time-based stop

VETO CLASSIFICATION:
  GOOD_VETO: Gate correctly blocked a trade that would have lost
  BAD_VETO: Gate incorrectly blocked a trade that would have won
  AMBIGUOUS: Insufficient data to determine
  DATA_VETO: Blocked due to missing data (not a quality judgment)

RULES:
1. Be concise and actionable
2. Never recommend parameter changes > 20% from current values (10% for kelly_fraction)
3. Flag any trade that looks like a systematic issue (not one-off)
4. Gate tuning confidence must be based on statistical evidence (include sample_size)
5. If insufficient data (< 5 trades), say so explicitly
6. All recommendations must include sample_size and confidence
7. Classify confidence: HIGH (sample >= 50), MEDIUM (20-49), LOW (< 20), INSUFFICIENT (< 10)
8. NEVER recommend removing a risk CHECK entirely"""


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------
def _load_gate_vetoes(date_str: str, max_lines: int = 200) -> List[Dict[str, Any]]:
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
                    # Filter to requested date
                    ts = entry.get("timestamp", entry.get("ts", ""))
                    if isinstance(ts, str) and ts.startswith(date_str):
                        vetoes.append(entry)
                    elif isinstance(ts, (int, float)):
                        # Nanosecond timestamp
                        from datetime import datetime as dt, timezone as tz
                        evt_date = dt.fromtimestamp(ts / 1e9, tz=tz.utc).strftime("%Y-%m-%d")
                        if evt_date == date_str:
                            vetoes.append(entry)
                except json.JSONDecodeError:
                    continue
                if len(vetoes) >= max_lines:
                    break
    except OSError as e:
        log.warning("Failed to read gate vetoes: %s", e)
    return vetoes


def _load_nightly_output() -> Dict[str, Any]:
    """Load nightly_output.json (Ouroboros recommendations)."""
    if not NIGHTLY_OUTPUT_FILE.exists():
        return {}
    try:
        with open(NIGHTLY_OUTPUT_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to load nightly output: %s", e)
        return {}


def assemble_context(date_str: Optional[str] = None) -> Dict[str, Any]:
    """Assemble the review context from ResearchContextStore + incidents."""
    context: Dict[str, Any] = {
        "date": date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "research_context": {},
        "incident_pack": None,
        "anomaly_baselines": {},
    }

    # Load research context
    if CONTEXT_FILE.exists():
        try:
            with open(CONTEXT_FILE) as f:
                store = json.load(f)
            # Get most recent day's data
            days = store.get("days", {})
            if date_str and date_str in days:
                context["research_context"] = days[date_str]
            elif days:
                latest_date = sorted(days.keys())[-1]
                context["research_context"] = days[latest_date]
                context["date"] = latest_date

            context["open_concerns"] = store.get("open_concerns", [])

            # Build 7-day summary
            try:
                from python_brain.ouroboros.research_store import ResearchContextStore
                rcs = ResearchContextStore()
                rcs.load()
                context["claude_context"] = rcs.get_context_for_claude(7)
            except ImportError:
                pass

        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load research context: %s", e)

    # Load incident pack for today
    incident_path = INCIDENTS_DIR / f"incident_{context['date']}.json"
    if incident_path.exists():
        try:
            with open(incident_path) as f:
                context["incident_pack"] = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # Load anomaly baselines
    baselines_path = RESEARCH_DIR / "anomaly_baselines.json"
    if baselines_path.exists():
        try:
            with open(baselines_path) as f:
                data = json.load(f)
            context["anomaly_baselines"] = data.get("baselines", {})
        except (json.JSONDecodeError, OSError):
            pass

    # Load gate vetoes for this date
    context["gate_vetoes"] = _load_gate_vetoes(context["date"])

    # Load nightly output (Ouroboros recommendations)
    context["nightly_output"] = _load_nightly_output()

    return context


def build_prompt(context: Dict[str, Any]) -> str:
    """Build the user message prompt from assembled context."""
    parts = [f"# AEGIS V2 Nightly Review: {context['date']}\n"]

    # Research context
    rc = context.get("research_context", {})
    if rc:
        metrics = rc.get("metrics", {})
        parts.append("## Today's Metrics")
        parts.append(f"- Trades: {metrics.get('total_trades', 0)}")
        parts.append(f"- Wins: {metrics.get('wins', 0)}, Losses: {metrics.get('losses', 0)}")
        parts.append(f"- Win Rate: {metrics.get('win_rate', 0):.0%}")
        parts.append(f"- P&L: GBP {metrics.get('total_pnl', 0):.2f}")
        parts.append(f"- Profit Factor: {metrics.get('profit_factor', 0):.2f}")
        parts.append(f"- Avg Rung: {metrics.get('avg_rung', 0):.1f}")

        # Per-ticker breakdown
        per_ticker = metrics.get("per_ticker", {})
        if per_ticker:
            parts.append("\n## Per-Ticker Performance")
            for t, td in sorted(per_ticker.items()):
                parts.append(f"- {t}: trades={td.get('trades', 0)} WR={td.get('win_rate', 0):.0%} "
                             f"PnL={td.get('pnl', 0):.2f}")

        # Recommendations from nightly
        recs = rc.get("recommendations", {})
        if recs:
            parts.append("\n## Ouroboros Recommendations")
            parts.append(f"- Kelly: {recs.get('kelly_fraction', '?')}")
            parts.append(f"- Chandelier ATR: {recs.get('chandelier_atr_mult', '?')}")
            for adj in recs.get("adjustments", []):
                parts.append(f"- Adjustment: {adj}")

        # Missed winners
        mw = rc.get("missed_winners", {})
        if mw and mw.get("total_rejected", 0) > 0:
            parts.append("\n## Missed Winner Analysis (from research context)")
            parts.append(f"- Rejected: {mw.get('total_rejected', 0)}")
            parts.append(f"- Missed Winners: {mw.get('total_missed_winners', 0)}")
            parts.append(f"- Rate: {mw.get('missed_winner_rate', 0):.1f}%")
            for gate in mw.get("worst_gates", [])[:5]:
                parts.append(f"  - {gate}")

    # Gate vetoes (direct from ndjson)
    gate_vetoes = context.get("gate_vetoes", [])
    if gate_vetoes:
        parts.append(f"\n## Gate Vetoes Today ({len(gate_vetoes)} total)")
        # Summarize by gate name
        gate_counts: Dict[str, int] = {}
        for v in gate_vetoes:
            gate = v.get("gate", v.get("veto_reason", "unknown"))
            gate_counts[gate] = gate_counts.get(gate, 0) + 1
        for gate, count in sorted(gate_counts.items(), key=lambda x: -x[1])[:10]:
            parts.append(f"- {gate}: {count} vetoes")
        # Include a few example vetoes with indicator context
        for v in gate_vetoes[:5]:
            ticker = v.get("ticker", v.get("symbol", "?"))
            gate = v.get("gate", v.get("veto_reason", "?"))
            indicators = v.get("indicators", {})
            ind_str = ", ".join(f"{k}={v}" for k, v in list(indicators.items())[:4]) if indicators else "N/A"
            parts.append(f"  Example: {ticker} vetoed by {gate} (indicators: {ind_str})")

    # Nightly output (Ouroboros recommendations)
    nightly = context.get("nightly_output", {})
    if nightly:
        parts.append("\n## Ouroboros Nightly Output")
        parts.append(f"- Date: {nightly.get('date', '?')}")
        parts.append(f"- Kelly recommended: {nightly.get('kelly_fraction', '?')}")
        parts.append(f"- Chandelier ATR recommended: {nightly.get('chandelier_atr_mult', '?')}")
        recs_list = nightly.get("recommendations", [])
        if recs_list:
            parts.append("- Recommendations:")
            for r in recs_list[:10]:
                if isinstance(r, dict):
                    parts.append(f"  - {r.get('param', '?')}: {r.get('old', '?')} -> {r.get('new', '?')} "
                                 f"(reason: {r.get('reason', '?')})")
                else:
                    parts.append(f"  - {r}")

    # 7-day context
    cc = context.get("claude_context", {})
    if cc and cc.get("actual_days", 0) > 0:
        rp = cc.get("recent_performance", {})
        parts.append(f"\n## 7-Day Rolling")
        parts.append(f"- WR: {rp.get('win_rate_7d', 0):.1%}")
        parts.append(f"- PF: {rp.get('pf_7d', 0):.2f}")
        parts.append(f"- Total P&L: GBP {rp.get('total_pnl_7d', 0):.2f}")
        parts.append(f"- Trade Count: {rp.get('trade_count_7d', 0)}")

        gv = cc.get("gate_veto_trend", {})
        parts.append(f"- 7d Vetoes: {gv.get('total_vetoes_7d', 0)}")
        parts.append(f"- 7d Missed Winners: {gv.get('total_missed_winners_7d', 0)}")

    # Incident pack
    ip = context.get("incident_pack")
    if ip:
        parts.append("\n## Incident Analysis")
        parts.append(f"- Severity: {ip.get('severity', '?')}")
        parts.append(f"- Summary: {ip.get('summary', '?')}")
        for cause in ip.get("root_cause_candidates", []):
            parts.append(f"- Root Cause: {cause}")
        for action in ip.get("recommended_actions", []):
            parts.append(f"- Action: {action}")

    # Open concerns
    concerns = context.get("open_concerns", [])
    if concerns:
        parts.append(f"\n## Open Concerns ({len(concerns)})")
        for c in concerns[-5:]:
            parts.append(f"- [{c.get('date', '?')}] [{c.get('category', '?')}] {c.get('issue', '')}")

    parts.append("\n---\nPlease provide your structured JSON review. Output pure JSON, no markdown code blocks.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Claude CLI call (replaces old Anthropic SDK call)
# ---------------------------------------------------------------------------
def call_claude(prompt: str) -> Optional[Dict[str, Any]]:
    """Call Claude CLI via claude_helper and parse JSON response."""
    claude_md = load_claude_md()
    system_context = SYSTEM_PROMPT
    if claude_md:
        system_context = claude_md + "\n\n" + SYSTEM_PROMPT

    start_time = time.time()
    review = claude_query(prompt, system_context=system_context)
    elapsed = time.time() - start_time

    if review is None:
        log.error("Claude CLI returned no response")
        return None

    # Check if we got a parse error (raw text instead of JSON)
    if isinstance(review, dict) and review.get("parse_error"):
        log.warning("Claude returned non-JSON response")
        review["_latency_s"] = round(elapsed, 2)
        return review

    # Add metadata
    review["_latency_s"] = round(elapsed, 2)
    review["_cost_usd"] = 0.0  # Free via Max subscription
    log.info("Claude CLI review completed in %.1fs", elapsed)
    return review


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------
def run_nightly_review(
    date_str: Optional[str] = None,
    dry_run: bool = False,
    send_tg: bool = False,
) -> Optional[Dict[str, Any]]:
    """Execute the full nightly review workflow."""
    log.info("N6a: Starting nightly review for %s", date_str or "today")

    # Assemble context
    context = assemble_context(date_str)
    prompt = build_prompt(context)
    review_date = context["date"]

    # Check if we have any data
    rc = context.get("research_context", {})
    metrics = rc.get("metrics", {})
    if metrics.get("total_trades", 0) == 0 and not context.get("gate_vetoes"):
        log.info("No trades and no vetoes today -- skipping review")
        return {"date": review_date, "status": "skipped", "confidence": 0, "reason": "no_trades"}

    if dry_run:
        print("=" * 60)
        print("  N6a DRY RUN -- Prompt that would be sent to Claude")
        print("=" * 60)
        print(f"\nSystem prompt: {len(SYSTEM_PROMPT)} chars")
        print(f"User prompt: {len(prompt)} chars")
        print(f"\n{prompt}")
        return {"date": review_date, "status": "dry_run", "confidence": 0}

    # Call Claude
    review = call_claude(prompt)
    if not review:
        log.error("Claude review failed -- no response")
        return None

    review["review_date"] = review_date
    review["generated_at"] = datetime.now(timezone.utc).isoformat()

    # Save review to new path: /app/data/claude/reviews/
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    review_path = REVIEW_DIR / f"review_{review_date}.json"
    try:
        with open(review_path, "w") as f:
            json.dump(review, f, indent=2, default=str)
        log.info("Review saved: %s", review_path)
    except OSError as e:
        log.error("Failed to save review: %s", e)

    # Send Telegram summary
    if send_tg:
        _send_telegram_summary(review)

    return review


def _send_telegram_summary(review: Dict[str, Any]):
    """Send condensed review summary via Telegram."""
    summary = review.get("executive_summary", "No summary available")
    grade = review.get("performance_grade", "?")
    confidence = review.get("overall_confidence", review.get("confidence", 0))
    risk_alerts = review.get("risk_alerts", [])

    lines = [
        "<b>CLAUDE NIGHTLY REVIEW</b>",
        "",
        f"Date: {review.get('review_date', '?')}",
        f"Grade: <b>{grade}</b> | Confidence: {confidence:.0%}",
        "",
        f"{summary}",
    ]

    # Root causes
    causes = review.get("root_causes", [])
    if causes:
        lines.append("\n<b>Root Causes:</b>")
        for c in causes[:3]:
            lines.append(f"  - {c}")

    # Gate tuning
    tuning = review.get("gate_tuning", [])
    if tuning:
        lines.append("\n<b>Gate Tuning:</b>")
        for t in tuning[:3]:
            conf = t.get("confidence", 0)
            lines.append(f"  - {t.get('gate', '?')}: {t.get('recommendation', '?')} ({conf:.0%})")

    # Veto analysis
    veto = review.get("veto_analysis", {})
    if veto and veto.get("total_vetoes", 0) > 0:
        lines.append(f"\n<b>Vetoes:</b> {veto.get('total_vetoes', 0)} total, "
                     f"{veto.get('bad_vetoes', 0)} bad vetoes")

    # Risk alerts
    if risk_alerts:
        lines.append("\n<b>Risk Alerts:</b>")
        for alert in risk_alerts[:3]:
            lines.append(f"  - {alert}")

    lines.append(f"\n<i>Cost: $0.00 (Max sub) | Latency: {review.get('_latency_s', 0):.0f}s</i>")

    send_telegram("\n".join(lines))
    log.info("Telegram summary sent")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [ClaudeReview] %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Claude Nightly Review (N6a / Sprint S07)")
    parser.add_argument("--date", type=str, help="Review specific date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Show prompt without calling API")
    parser.add_argument("--send-telegram", action="store_true", help="Send summary via Telegram")
    parser.add_argument("--json", action="store_true", help="Output review as JSON")
    args = parser.parse_args()

    review = run_nightly_review(
        date_str=args.date,
        dry_run=args.dry_run,
        send_tg=args.send_telegram,
    )

    if review and args.json and not args.dry_run:
        print(json.dumps(review, indent=2, default=str))
    elif review and not args.dry_run and not args.json:
        summary = review.get("executive_summary", "No summary")
        grade = review.get("performance_grade", "?")
        print(f"\nReview complete: Grade {grade}")
        print(f"Summary: {summary}")


if __name__ == "__main__":
    main()
