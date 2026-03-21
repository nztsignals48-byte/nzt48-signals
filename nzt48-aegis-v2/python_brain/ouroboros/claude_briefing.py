"""N6b — Claude Operator Morning Briefing.

Runs at 07:45 UTC Mon-Fri (before European session open). Generates a
human-readable morning briefing using N6a review output + system telemetry.

Sent to operator via Telegram. Also saved as markdown + optional PDF.

QUARANTINE: Read-only. Never writes to WAL, config, or live trading parameters.

Usage:
    python3 -m python_brain.ouroboros.claude_briefing                     # Generate briefing
    python3 -m python_brain.ouroboros.claude_briefing --send-telegram     # Send via Telegram
    python3 -m python_brain.ouroboros.claude_briefing --dry-run           # Show prompt only
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("claude_briefing")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
REVIEW_DIR = DATA_DIR / "ouroboros_reviews"
BRIEFING_DIR = DATA_DIR / "morning_briefings"
RESEARCH_DIR = DATA_DIR / "research"
MONITOR_FILE = DATA_DIR / "monitor_status.json"
TELEMETRY_FILE = Path(os.environ.get("AEGIS_WAL_DIR", "/app/events")) / "telemetry_snapshot.json"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 1500
TEMPERATURE = 0.4

SYSTEM_PROMPT = """You are the morning briefing generator for AEGIS V2, an autonomous UK ISA leveraged ETP trading engine.

Generate a concise, human-readable morning briefing for the operator.
The operator reads this on their phone before market open.

STYLE:
- Conversational but professional
- Lead with the most important info (problems first, then opportunities)
- Use bullet points for quick scanning
- Include specific numbers (not vague "improved")
- End with 1-2 actionable items for the operator

FORMAT (plain text with HTML tags for Telegram):
<b>AEGIS MORNING BRIEFING — {date}</b>

<b>🔑 Key Takeaway:</b>
One sentence summary.

<b>📊 Yesterday's Performance:</b>
- [metrics]

<b>⚠️ Attention Needed:</b>
- [issues, if any]

<b>🎯 Today's Setup:</b>
- [tickers to watch]
- [market conditions]

<b>📋 Operator Actions:</b>
1. [action item]
2. [action item]

Keep it under 500 words. The operator should be able to read it in 60 seconds."""


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------
def assemble_briefing_context() -> Dict[str, Any]:
    """Assemble context for the morning briefing."""
    context: Dict[str, Any] = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "nightly_review": None,
        "system_health": None,
        "telemetry": None,
        "7day_context": None,
    }

    # Load last night's Claude review (N6a)
    yesterday = (datetime.now(timezone.utc).date()).isoformat()
    review_path = REVIEW_DIR / f"review_{yesterday}.json"
    if not review_path.exists():
        # Try the day before
        from datetime import timedelta
        prev_day = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
        review_path = REVIEW_DIR / f"review_{prev_day}.json"

    if review_path.exists():
        try:
            with open(review_path) as f:
                context["nightly_review"] = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # Load system health (N10b)
    if MONITOR_FILE.exists():
        try:
            with open(MONITOR_FILE) as f:
                context["system_health"] = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # Load telemetry
    if TELEMETRY_FILE.exists():
        try:
            with open(TELEMETRY_FILE) as f:
                context["telemetry"] = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # Load 7-day research context
    context_file = RESEARCH_DIR / "context_store.json"
    if context_file.exists():
        try:
            from python_brain.ouroboros.research_store import ResearchContextStore
            rcs = ResearchContextStore()
            rcs.load()
            context["7day_context"] = rcs.get_context_for_claude(7)
        except (ImportError, Exception) as e:
            log.warning("Could not load 7-day context: %s", e)

    return context


def build_prompt(context: Dict[str, Any]) -> str:
    """Build user prompt from assembled context."""
    parts = [f"Generate the AEGIS morning briefing for {context['date']}.\n"]

    # Nightly review
    review = context.get("nightly_review")
    if review and not review.get("skipped"):
        parts.append("## Last Night's Review")
        parts.append(f"Grade: {review.get('performance_grade', '?')}")
        parts.append(f"Summary: {review.get('executive_summary', 'N/A')}")

        causes = review.get("root_causes", [])
        if causes:
            parts.append("Root causes: " + "; ".join(causes[:3]))

        alerts = review.get("risk_alerts", [])
        if alerts:
            parts.append("Risk alerts: " + "; ".join(alerts[:3]))

        watchlist = review.get("tomorrow_watchlist", [])
        if watchlist:
            parts.append(f"Watchlist: {', '.join(watchlist[:5])}")
    else:
        parts.append("## No nightly review available (no trades yesterday or review failed)")

    # System health
    health = context.get("system_health")
    if health:
        parts.append(f"\n## System Health: {health.get('overall_status', '?').upper()}")
        for check in health.get("checks", []):
            if check.get("status") in ("critical", "warning"):
                parts.append(f"- {check['name']}: {check.get('detail', '?')}")

    # Telemetry
    telem = context.get("telemetry")
    if telem:
        parts.append(f"\n## Engine Telemetry")
        parts.append(f"Regime: {telem.get('regime', '?')}")
        parts.append(f"Positions: {telem.get('positions', '?')}")
        parts.append(f"Equity: {telem.get('equity', '?')}")

    # 7-day context
    ctx7d = context.get("7day_context")
    if ctx7d and ctx7d.get("actual_days", 0) > 0:
        rp = ctx7d.get("recent_performance", {})
        parts.append(f"\n## 7-Day Rolling")
        parts.append(f"WR: {rp.get('win_rate_7d', 0):.1%}, PF: {rp.get('pf_7d', 0):.2f}")
        parts.append(f"P&L: GBP {rp.get('total_pnl_7d', 0):.2f}")
        parts.append(f"Trades: {rp.get('trade_count_7d', 0)}")

        concerns = ctx7d.get("open_concerns", [])
        if concerns:
            parts.append(f"Concerns: {len(concerns)} open")

    parts.append("\n---\nGenerate the morning briefing. Use the HTML format specified in your system prompt.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Claude API call
# ---------------------------------------------------------------------------
def call_claude(prompt: str) -> Optional[str]:
    """Call Claude API and return text response."""
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY not set")
        return None

    try:
        import anthropic
    except ImportError:
        log.error("anthropic SDK not installed")
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
        cost = (
            response.usage.input_tokens * 3.0 / 1e6
            + response.usage.output_tokens * 15.0 / 1e6
        )
        log.info("Claude API: %d/%d tokens, $%.4f, %.1fs",
                 response.usage.input_tokens, response.usage.output_tokens,
                 cost, elapsed)
        return content

    except Exception as e:
        log.error("Claude API failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Fallback briefing (no Claude API)
# ---------------------------------------------------------------------------
def generate_fallback_briefing(context: Dict[str, Any]) -> str:
    """Generate a simple briefing without Claude (when API key not set)."""
    lines = [
        f"\U0001f305 <b>AEGIS MORNING BRIEFING — {context['date']}</b>",
        "",
    ]

    # System health
    health = context.get("system_health")
    if health:
        status = health.get("overall_status", "unknown")
        icon = "\u2705" if status == "healthy" else "\U0001f534"
        lines.append(f"{icon} <b>System:</b> {status.upper()}")
        for check in health.get("checks", []):
            if check.get("status") in ("critical", "warning"):
                lines.append(f"  \u26a0\ufe0f {check['name']}: {check.get('detail', '?')}")

    # Nightly review
    review = context.get("nightly_review")
    if review and not review.get("skipped"):
        grade = review.get("performance_grade", "?")
        lines.append(f"\n\U0001f4ca <b>Yesterday:</b> Grade {grade}")
        summary = review.get("executive_summary", "")
        if summary:
            lines.append(f"  {summary}")
    else:
        lines.append(f"\n\U0001f4ca <b>Yesterday:</b> No trades or review unavailable")

    # 7-day
    ctx7d = context.get("7day_context")
    if ctx7d and ctx7d.get("actual_days", 0) > 0:
        rp = ctx7d.get("recent_performance", {})
        lines.append(f"\n\U0001f4c8 <b>7-Day:</b> WR {rp.get('win_rate_7d', 0):.0%}, "
                     f"PF {rp.get('pf_7d', 0):.1f}, "
                     f"PnL GBP {rp.get('total_pnl_7d', 0):+.0f}")

    # Telemetry
    telem = context.get("telemetry")
    if telem:
        lines.append(f"\n\U0001f916 <b>Engine:</b> regime={telem.get('regime', '?')}, "
                     f"positions={telem.get('positions', '?')}, "
                     f"equity={telem.get('equity', '?')}")

    lines.append(f"\n<i>Generated {datetime.now(timezone.utc).strftime('%H:%M UTC')}</i>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------
def run_morning_briefing(
    dry_run: bool = False,
    send_telegram: bool = False,
) -> Optional[str]:
    """Generate and distribute morning briefing."""
    log.info("N6b: Generating morning briefing")

    context = assemble_briefing_context()
    prompt = build_prompt(context)

    if dry_run:
        print("=" * 60)
        print("  N6b DRY RUN — Prompt for Claude")
        print("=" * 60)
        print(prompt)
        return None

    # Try Claude first, fall back to template
    briefing = None
    if ANTHROPIC_API_KEY:
        briefing = call_claude(prompt)

    if not briefing:
        log.info("Using fallback briefing (no Claude API)")
        briefing = generate_fallback_briefing(context)

    # Save briefing
    BRIEFING_DIR.mkdir(parents=True, exist_ok=True)
    date_str = context["date"]
    briefing_path = BRIEFING_DIR / f"briefing_{date_str}.txt"
    try:
        with open(briefing_path, "w") as f:
            f.write(briefing)
        log.info("Briefing saved: %s", briefing_path)
    except OSError as e:
        log.error("Failed to save briefing: %s", e)

    # Send via Telegram
    if send_telegram:
        try:
            from python_brain.ouroboros.telegram_notify import send_message
            send_message(briefing)
            log.info("Briefing sent via Telegram")
        except ImportError:
            log.warning("Telegram not available")
        except Exception as e:
            log.error("Telegram send failed: %s", e)

    return briefing


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [Briefing] %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Claude Morning Briefing (N6b)")
    parser.add_argument("--send-telegram", action="store_true", help="Send via Telegram")
    parser.add_argument("--dry-run", action="store_true", help="Show prompt only")
    args = parser.parse_args()

    briefing = run_morning_briefing(
        dry_run=args.dry_run,
        send_telegram=args.send_telegram,
    )

    if briefing and not args.dry_run:
        print(briefing)


if __name__ == "__main__":
    main()
