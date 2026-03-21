"""N6a — Claude Nightly Review Module.

Runs after nightly_v6 (04:52 UTC). Reads the ResearchContextStore output,
calls the Anthropic Claude API, and produces a structured JSON review
with per-trade narrative classification and recommended actions.

QUARANTINE: This module is READ-ONLY. It NEVER writes to WAL, config.toml,
dynamic_weights.toml, or any live trading parameter. Output goes only to
/data/ouroboros_reviews/ and optionally Telegram.

Dependencies: `anthropic` Python SDK (add to requirements.txt)

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

log = logging.getLogger("claude_review")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
REVIEW_DIR = DATA_DIR / "ouroboros_reviews"
RESEARCH_DIR = DATA_DIR / "research"
INCIDENTS_DIR = RESEARCH_DIR / "incidents"
CONTEXT_FILE = RESEARCH_DIR / "context_store.json"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 2048
TEMPERATURE = 0.3  # Low temperature for structured analysis

# Cost tracking
TOKEN_COST_INPUT_PER_M = 3.0   # $/M input tokens (Claude 3.5 Sonnet)
TOKEN_COST_OUTPUT_PER_M = 15.0  # $/M output tokens

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are the nightly trade reviewer for AEGIS V2, an autonomous UK ISA leveraged ETP trading engine.

Your role: Analyze today's trading performance and provide actionable insights.

CONTEXT:
- The system trades 3x/5x leveraged ETPs on the London Stock Exchange
- Strategies: VanguardSniper (momentum), Autonomous Orchestrator (VWAP/Gap/RSI/Momentum)
- Exit: 5-rung Chandelier trailing stop (0.8%→1.5%→2.5%→4.0% gain thresholds)
- Risk: 31-check arbiter, confidence floor 65, spread veto 0.3%, max 3 trades/day
- Current phase: Paper trading (£10,000 starting equity)

OUTPUT FORMAT (JSON):
{
  "date": "YYYY-MM-DD",
  "executive_summary": "1-2 sentence summary of the day",
  "performance_grade": "A/B/C/D/F",
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
      "reasoning": "Why this change"
    }
  ],
  "risk_alerts": ["Any risk concerns"],
  "tomorrow_watchlist": ["Tickers to watch"],
  "overall_confidence": 0.0-1.0
}

TRADE CLASSIFICATION:
Winners:
  W1: Clean Momentum — EMA cross + trend, Rung 3+ exit
  W2: VWAP Reversion — Pullback to VWAP, quick exit
  W3: Gap Fill — Morning gap exploitation
  W4: Tail Capture — Rung 5 exit, exceptional move
  W5: Breakeven Escape — Rung 2 exit (fee-covered)

Losers:
  L1: Spread Victim — Cost exceeded edge
  L2: Stop Hunt — Leveraged ETP whipsaw
  L3: Regime Failure — Momentum in mean-reverting market
  L4: Noise Exit — Chandelier too tight, stopped on noise
  L5: Overextended Entry — Chased above VWAP
  L6: Volume Fade — Entered on declining volume
  L7: Timing Error — Wrong session, wrong hour

RULES:
1. Be concise and actionable
2. Never recommend parameter changes > 20% from current values
3. Flag any trade that looks like a systematic issue (not one-off)
4. Gate tuning confidence must be based on statistical evidence (not hunches)
5. If insufficient data (< 5 trades), say so explicitly"""


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------
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
            parts.append(f"\n## Ouroboros Recommendations")
            parts.append(f"- Kelly: {recs.get('kelly_fraction', '?')}")
            parts.append(f"- Chandelier ATR: {recs.get('chandelier_atr_mult', '?')}")
            for adj in recs.get("adjustments", []):
                parts.append(f"- Adjustment: {adj}")

        # Missed winners
        mw = rc.get("missed_winners", {})
        if mw and mw.get("total_rejected", 0) > 0:
            parts.append(f"\n## Gate Veto Analysis")
            parts.append(f"- Rejected: {mw.get('total_rejected', 0)}")
            parts.append(f"- Missed Winners: {mw.get('total_missed_winners', 0)}")
            parts.append(f"- Rate: {mw.get('missed_winner_rate', 0):.1f}%")
            for gate in mw.get("worst_gates", [])[:5]:
                parts.append(f"  - {gate}")

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
        parts.append(f"\n## Incident Analysis")
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

    parts.append("\n---\nPlease provide your structured JSON review.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Claude API call
# ---------------------------------------------------------------------------
def call_claude(prompt: str) -> Optional[Dict[str, Any]]:
    """Call Claude API and parse JSON response."""
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY not set — cannot call Claude")
        return None

    try:
        import anthropic
    except ImportError:
        log.error("anthropic SDK not installed — add 'anthropic' to requirements.txt")
        return None

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    start_time = time.time()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        elapsed = time.time() - start_time
        content = response.content[0].text if response.content else ""

        # Track usage
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cost_usd": (
                response.usage.input_tokens * TOKEN_COST_INPUT_PER_M / 1e6
                + response.usage.output_tokens * TOKEN_COST_OUTPUT_PER_M / 1e6
            ),
            "latency_s": round(elapsed, 2),
        }
        log.info("Claude API: %d in / %d out tokens, $%.4f, %.1fs",
                 usage["input_tokens"], usage["output_tokens"],
                 usage["cost_usd"], usage["latency_s"])

        # Parse JSON from response
        try:
            # Handle case where response has markdown code blocks
            text = content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text
                text = text.rsplit("```", 1)[0] if "```" in text else text
                text = text.strip()

            review = json.loads(text)
            review["_usage"] = usage
            return review
        except json.JSONDecodeError:
            log.warning("Claude returned non-JSON. Raw response saved.")
            return {
                "raw_response": content,
                "_usage": usage,
                "parse_error": True,
            }

    except Exception as e:
        log.error("Claude API call failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------
def run_nightly_review(
    date_str: Optional[str] = None,
    dry_run: bool = False,
    send_telegram: bool = False,
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
    if metrics.get("total_trades", 0) == 0:
        log.info("No trades today — skipping review")
        return {"date": review_date, "skipped": True, "reason": "no_trades"}

    if dry_run:
        print("=" * 60)
        print("  N6a DRY RUN — Prompt that would be sent to Claude")
        print("=" * 60)
        print(f"\nSystem prompt: {len(SYSTEM_PROMPT)} chars")
        print(f"User prompt: {len(prompt)} chars")
        print(f"\n{prompt}")
        return {"date": review_date, "dry_run": True}

    # Call Claude
    review = call_claude(prompt)
    if not review:
        log.error("Claude review failed — no response")
        return None

    review["review_date"] = review_date
    review["generated_at"] = datetime.now(timezone.utc).isoformat()

    # Save review
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    review_path = REVIEW_DIR / f"review_{review_date}.json"
    try:
        with open(review_path, "w") as f:
            json.dump(review, f, indent=2, default=str)
        log.info("Review saved: %s", review_path)
    except OSError as e:
        log.error("Failed to save review: %s", e)

    # Send Telegram summary
    if send_telegram:
        _send_telegram_summary(review)

    return review


def _send_telegram_summary(review: Dict[str, Any]):
    """Send condensed review summary via Telegram."""
    try:
        from python_brain.ouroboros.telegram_notify import send_message

        summary = review.get("executive_summary", "No summary available")
        grade = review.get("performance_grade", "?")
        confidence = review.get("overall_confidence", 0)
        risk_alerts = review.get("risk_alerts", [])

        lines = [
            f"\U0001f9e0 <b>CLAUDE NIGHTLY REVIEW</b>",
            f"",
            f"Date: {review.get('review_date', '?')}",
            f"Grade: <b>{grade}</b> | Confidence: {confidence:.0%}",
            f"",
            f"{summary}",
        ]

        # Root causes
        causes = review.get("root_causes", [])
        if causes:
            lines.append(f"\n<b>Root Causes:</b>")
            for c in causes[:3]:
                lines.append(f"  \u2022 {c}")

        # Gate tuning
        tuning = review.get("gate_tuning", [])
        if tuning:
            lines.append(f"\n<b>Gate Tuning:</b>")
            for t in tuning[:3]:
                conf = t.get("confidence", 0)
                lines.append(f"  \u2022 {t.get('gate', '?')}: {t.get('recommendation', '?')} ({conf:.0%})")

        # Risk alerts
        if risk_alerts:
            lines.append(f"\n\u26a0\ufe0f <b>Risk Alerts:</b>")
            for alert in risk_alerts[:3]:
                lines.append(f"  \u2022 {alert}")

        usage = review.get("_usage", {})
        if usage:
            lines.append(f"\n<i>Cost: ${usage.get('cost_usd', 0):.4f} | "
                         f"{usage.get('input_tokens', 0)}+{usage.get('output_tokens', 0)} tokens</i>")

        send_message("\n".join(lines))
        log.info("Telegram summary sent")
    except ImportError:
        log.warning("Telegram not available")
    except Exception as e:
        log.error("Telegram send failed: %s", e)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [ClaudeReview] %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Claude Nightly Review (N6a)")
    parser.add_argument("--date", type=str, help="Review specific date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Show prompt without calling API")
    parser.add_argument("--send-telegram", action="store_true", help="Send summary via Telegram")
    parser.add_argument("--json", action="store_true", help="Output review as JSON")
    args = parser.parse_args()

    review = run_nightly_review(
        date_str=args.date,
        dry_run=args.dry_run,
        send_telegram=args.send_telegram,
    )

    if review and args.json and not args.dry_run:
        print(json.dumps(review, indent=2, default=str))
    elif review and not args.dry_run and not args.json:
        summary = review.get("executive_summary", "No summary")
        grade = review.get("performance_grade", "?")
        print(f"\nReview complete: Grade {grade}")
        print(f"Summary: {summary}")
        usage = review.get("_usage", {})
        if usage:
            print(f"Cost: ${usage.get('cost_usd', 0):.4f}")


if __name__ == "__main__":
    main()
