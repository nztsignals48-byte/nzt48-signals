"""N6b -- Claude Operator Briefing (Sprint S11-S12).

Generates morning (07:45 UTC) or evening (21:30 UTC) operator briefings using
Claude CLI via claude_helper ($0/call via Max subscription).

Morning briefing: yesterday's grade, overnight changes, attention items,
today's regime, challenger output, approval log.

Evening briefing: day's P&L by exchange, factor breakdown, gate vetoes,
top 5 tickers for tomorrow.

Sent to operator via Telegram. Also saved as text files.

QUARANTINE: Read-only. Never writes to WAL, config, or live trading parameters.
Output goes only to /app/data/claude/briefings/ and optionally Telegram.

Usage:
    python3 -m python_brain.ouroboros.claude_briefing                     # Morning briefing
    python3 -m python_brain.ouroboros.claude_briefing --evening            # Evening briefing
    python3 -m python_brain.ouroboros.claude_briefing --send-telegram      # Send via Telegram
    python3 -m python_brain.ouroboros.claude_briefing --dry-run            # Show prompt only
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from python_brain.ouroboros.claude_helper import (
    claude_query,
    load_context_files,
    send_telegram,
    build_context_string,
    load_claude_md,
)

log = logging.getLogger("claude_briefing")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
REVIEW_DIR = DATA_DIR / "claude" / "reviews"
BRIEFING_DIR = DATA_DIR / "claude" / "briefings"
CHALLENGE_DIR = DATA_DIR / "claude" / "challenges"
APPROVAL_LOG = DATA_DIR / "claude" / "approval_log.ndjson"
RESEARCH_DIR = DATA_DIR / "research"
MONITOR_FILE = DATA_DIR / "monitor_status.json"
GATE_VETOES_FILE = DATA_DIR / "gate_vetoes.ndjson"
NIGHTLY_OUTPUT_FILE = DATA_DIR / "nightly_output.json"
TELEMETRY_FILE = Path(os.environ.get("AEGIS_WAL_DIR", "/app/events")) / "telemetry_snapshot.json"
REPORTS_DIR = DATA_DIR / "ouroboros_reports"

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------
MORNING_SYSTEM_PROMPT = """You are the morning briefing generator for AEGIS V2, an autonomous UK ISA leveraged ETP trading engine.

Generate a concise, human-readable morning briefing for the operator.
The operator reads this on their phone before market open.

STYLE:
- Conversational but professional
- Lead with the most important info (problems first, then opportunities)
- Use bullet points for quick scanning
- Include specific numbers (not vague "improved")
- End with 1-2 actionable items for the operator

FORMAT (plain text with HTML tags for Telegram):
<b>AEGIS MORNING BRIEFING -- {date}</b>

<b>Key Takeaway:</b>
One sentence summary.

<b>Yesterday's Performance:</b>
- [metrics]

<b>Overnight Changes:</b>
- [parameter changes applied by approval gate]
- [challenger verdicts]

<b>Attention Needed:</b>
- [issues, if any]

<b>Today's Setup:</b>
- [regime, market conditions]
- [tickers to watch]

<b>Operator Actions:</b>
1. [action item]
2. [action item]

Keep it under 500 words. The operator should be able to read it in 60 seconds."""

EVENING_SYSTEM_PROMPT = """You are the evening briefing generator for AEGIS V2, an autonomous UK ISA leveraged ETP trading engine.

Generate a concise evening wrap-up for the operator.

STYLE:
- Brief, results-focused
- P&L numbers front and center
- Highlight what worked and what didn't
- End with tomorrow's top 5 watchlist

FORMAT (plain text with HTML tags for Telegram):
<b>AEGIS EVENING BRIEFING -- {date}</b>

<b>Day Summary:</b>
- Total P&L: GBP X.XX
- Trades: X (W/L)
- Win Rate: X%

<b>P&L by Exchange:</b>
- LSE: GBP X.XX (N trades)
- US: GBP X.XX (N trades)

<b>Factor Breakdown:</b>
- Best factor: [what drove wins]
- Worst factor: [what drove losses]

<b>Gate Vetoes:</b>
- X total vetoes (Y bad vetoes)
- Worst gate: [gate name]

<b>Tomorrow's Top 5:</b>
1. TICKER -- reason
2. TICKER -- reason

Keep it under 400 words."""


# ---------------------------------------------------------------------------
# Context assembly -- Morning
# ---------------------------------------------------------------------------
def _load_latest_review() -> Optional[Dict[str, Any]]:
    """Load the most recent Claude review."""
    if not REVIEW_DIR.exists():
        # Also check old path
        old_dir = DATA_DIR / "ouroboros_reviews"
        if old_dir.exists():
            review_files = sorted(old_dir.glob("review_*.json"))
            if review_files:
                try:
                    with open(review_files[-1]) as f:
                        return json.load(f)
                except (json.JSONDecodeError, OSError):
                    pass
        return None

    review_files = sorted(REVIEW_DIR.glob("review_*.json"))
    if not review_files:
        return None
    try:
        with open(review_files[-1]) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _load_latest_challenger() -> Optional[Dict[str, Any]]:
    """Load the most recent challenger output."""
    if not CHALLENGE_DIR.exists():
        return None
    challenge_files = sorted(CHALLENGE_DIR.glob("challenge_*.json"))
    if not challenge_files:
        return None
    try:
        with open(challenge_files[-1]) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _load_recent_approval_log(max_entries: int = 20) -> List[Dict[str, Any]]:
    """Load recent entries from approval_log.ndjson."""
    if not APPROVAL_LOG.exists():
        return []
    entries: List[Dict[str, Any]] = []
    try:
        with open(APPROVAL_LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return entries[-max_entries:]


def _load_gate_vetoes_today(date_str: str) -> List[Dict[str, Any]]:
    """Load today's gate vetoes from gate_vetoes.ndjson."""
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
                    ts = entry.get("timestamp", entry.get("ts", ""))
                    if isinstance(ts, str) and ts.startswith(date_str):
                        vetoes.append(entry)
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return vetoes


def _load_latest_metrics() -> Optional[Dict[str, Any]]:
    """Load the most recent daily metrics."""
    if not REPORTS_DIR.exists():
        return None
    json_files = sorted(REPORTS_DIR.glob("*_metrics.json"))
    if not json_files:
        return None
    try:
        with open(json_files[-1]) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def assemble_morning_context() -> Dict[str, Any]:
    """Assemble context for the morning briefing."""
    context: Dict[str, Any] = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "mode": "morning",
        "nightly_review": _load_latest_review(),
        "challenger": _load_latest_challenger(),
        "approval_log": _load_recent_approval_log(),
        "system_health": None,
        "telemetry": None,
        "7day_context": None,
    }

    # Load system health
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


def assemble_evening_context() -> Dict[str, Any]:
    """Assemble context for the evening briefing."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    context: Dict[str, Any] = {
        "date": date_str,
        "mode": "evening",
        "today_metrics": _load_latest_metrics(),
        "gate_vetoes": _load_gate_vetoes_today(date_str),
        "telemetry": None,
        "nightly_output": None,
    }

    # Load telemetry
    if TELEMETRY_FILE.exists():
        try:
            with open(TELEMETRY_FILE) as f:
                context["telemetry"] = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # Load nightly output (for tomorrow's recommendations)
    if NIGHTLY_OUTPUT_FILE.exists():
        try:
            with open(NIGHTLY_OUTPUT_FILE) as f:
                context["nightly_output"] = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    return context


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------
def build_morning_prompt(context: Dict[str, Any]) -> str:
    """Build morning briefing prompt from assembled context."""
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

        # Veto analysis
        veto = review.get("veto_analysis", {})
        if veto and veto.get("total_vetoes", 0) > 0:
            parts.append(f"Vetoes: {veto.get('total_vetoes', 0)} total, "
                         f"{veto.get('bad_vetoes', 0)} bad")
    else:
        parts.append("## No nightly review available (no trades yesterday or review failed)")

    # Challenger output
    challenger = context.get("challenger")
    if challenger and challenger.get("status") == "complete":
        parts.append("\n## Challenger Verdicts")
        for c in challenger.get("challenges", [])[:5]:
            param = c.get("param", "?")
            verdict = c.get("verdict", "?")
            parts.append(f"- {param}: {verdict} ({c.get('reasoning', '')[:60]})")

        sys_alerts = challenger.get("system_alerts", [])
        if sys_alerts:
            parts.append("System alerts: " + "; ".join(sys_alerts[:3]))

    # Approval log
    approvals = context.get("approval_log", [])
    if approvals:
        # Filter to recent (last 24 hours)
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        recent = [a for a in approvals if a.get("timestamp", "") >= cutoff]
        if recent:
            parts.append(f"\n## Overnight Approval Gate ({len(recent)} decisions)")
            for a in recent[:5]:
                action = a.get("action", "?")
                param = a.get("param", "?")
                reason = a.get("reason", "")[:60]
                parts.append(f"- {action} {param}: {reason}")

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
        parts.append("\n## Engine Telemetry")
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


def build_evening_prompt(context: Dict[str, Any]) -> str:
    """Build evening briefing prompt from assembled context."""
    parts = [f"Generate the AEGIS evening briefing for {context['date']}.\n"]

    # Today's metrics
    metrics = context.get("today_metrics")
    if metrics:
        parts.append("## Today's Performance")
        parts.append(f"- Total Trades: {metrics.get('total_trades', 0)}")
        parts.append(f"- Wins: {metrics.get('wins', 0)}, Losses: {metrics.get('losses', 0)}")
        parts.append(f"- Win Rate: {metrics.get('win_rate', 0):.0%}")
        parts.append(f"- P&L: GBP {metrics.get('total_pnl', 0):.2f}")
        parts.append(f"- Profit Factor: {metrics.get('profit_factor', 0):.2f}")
        parts.append(f"- Avg Rung: {metrics.get('avg_rung', 0):.1f}")

        # Per-exchange breakdown
        per_exchange = metrics.get("per_exchange", {})
        if per_exchange:
            parts.append("\n## P&L by Exchange")
            for exch, exch_data in sorted(per_exchange.items()):
                parts.append(f"- {exch}: GBP {exch_data.get('pnl', 0):.2f} "
                             f"({exch_data.get('trades', 0)} trades, "
                             f"WR {exch_data.get('win_rate', 0):.0%})")

        # Per-ticker breakdown
        per_ticker = metrics.get("per_ticker", {})
        if per_ticker:
            parts.append("\n## Per-Ticker Performance")
            for t, td in sorted(per_ticker.items(), key=lambda x: -abs(x[1].get("pnl", 0)))[:10]:
                parts.append(f"- {t}: PnL={td.get('pnl', 0):.2f} "
                             f"trades={td.get('trades', 0)} WR={td.get('win_rate', 0):.0%}")

        # Factor breakdown (if available)
        factors = metrics.get("factor_breakdown", {})
        if factors:
            parts.append("\n## Factor Breakdown")
            for factor, fdata in sorted(factors.items(), key=lambda x: -abs(x[1].get("contribution", 0))):
                parts.append(f"- {factor}: contribution={fdata.get('contribution', 0):.2f}")
    else:
        parts.append("## No trading data available today")

    # Gate vetoes
    vetoes = context.get("gate_vetoes", [])
    if vetoes:
        parts.append(f"\n## Gate Vetoes ({len(vetoes)} total)")
        gate_counts: Dict[str, int] = {}
        for v in vetoes:
            gate = v.get("gate", v.get("veto_reason", "unknown"))
            gate_counts[gate] = gate_counts.get(gate, 0) + 1
        for gate, count in sorted(gate_counts.items(), key=lambda x: -x[1])[:5]:
            parts.append(f"- {gate}: {count} vetoes")

    # Telemetry
    telem = context.get("telemetry")
    if telem:
        parts.append(f"\n## Engine State")
        parts.append(f"Regime: {telem.get('regime', '?')}")
        parts.append(f"Equity: {telem.get('equity', '?')}")

    # Nightly output (tomorrow's recommendations)
    nightly = context.get("nightly_output")
    if nightly:
        recs = nightly.get("recommendations", [])
        if recs:
            parts.append("\n## Tomorrow's Ouroboros Recommendations")
            for r in recs[:5]:
                if isinstance(r, dict):
                    parts.append(f"- {r.get('param', '?')}: {r.get('old', '?')} -> {r.get('new', '?')}")
                else:
                    parts.append(f"- {r}")

    parts.append("\n---\nGenerate the evening briefing. Use the HTML format specified in your system prompt.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Claude CLI call (replaces old Anthropic SDK call)
# ---------------------------------------------------------------------------
def call_claude(prompt: str, is_evening: bool = False) -> Optional[str]:
    """Call Claude CLI and return text response."""
    claude_md = load_claude_md()
    system_prompt = EVENING_SYSTEM_PROMPT if is_evening else MORNING_SYSTEM_PROMPT
    system_context = system_prompt
    if claude_md:
        system_context = claude_md + "\n\n" + system_prompt

    start_time = time.time()

    # For briefings we want text output, not JSON -- but claude_query returns dict.
    # We'll ask for a text-wrapped JSON with a "briefing_text" field.
    enhanced_prompt = (
        prompt + "\n\nIMPORTANT: Return your response as JSON with a single key "
        "\"briefing_text\" containing the full briefing HTML string. "
        "Example: {\"briefing_text\": \"<b>AEGIS MORNING BRIEFING...</b>\"}"
    )

    result = claude_query(enhanced_prompt, system_context=system_context)
    elapsed = time.time() - start_time

    if result is None:
        log.error("Claude CLI returned no response")
        return None

    log.info("Claude CLI briefing completed in %.1fs (cost: $0.00)", elapsed)

    # Extract briefing text from Claude response
    if isinstance(result, dict):
        # Direct briefing_text key (ideal case)
        text = result.get("briefing_text", "")
        if text:
            return text

        # Claude helper returns {"text": "...", "raw": True} when response isn't pure JSON
        raw_text = result.get("text", "")
        if raw_text:
            # Strategy 1: Find embedded JSON with briefing_text key
            import re
            match = re.search(r'\{[^{]*"briefing_text"\s*:', raw_text)
            if match:
                json_start = match.start()
                # Try progressively larger substrings to find valid JSON
                embedded = raw_text[json_start:]
                # Find matching closing brace by counting braces
                depth = 0
                end_pos = 0
                for i, ch in enumerate(embedded):
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            end_pos = i + 1
                            break
                if end_pos > 0:
                    try:
                        parsed = json.loads(embedded[:end_pos])
                        if isinstance(parsed, dict) and "briefing_text" in parsed:
                            briefing = parsed["briefing_text"]
                            # Unescape any double-escaped newlines
                            briefing = briefing.replace("\\n", "\n")
                            return briefing
                    except json.JSONDecodeError:
                        pass

            # Strategy 2: Find HTML content starting with <b> tag
            if "<b>" in raw_text:
                html_start = raw_text.find("<b>")
                # Take everything from first <b> tag
                extracted = raw_text[html_start:]
                # Remove any trailing JSON/metadata after the briefing
                # Look for patterns like "}", "stop_reason", etc at the end
                for end_marker in ['"}', "stop_reason", '"type"']:
                    marker_pos = extracted.rfind(end_marker)
                    if marker_pos > 0 and marker_pos > len(extracted) * 0.8:
                        extracted = extracted[:marker_pos].rstrip().rstrip('"').rstrip(',').rstrip()
                return extracted

            # Strategy 3: Return raw text as-is
            return raw_text

        # Fallback: structured response without briefing_text
        if "executive_summary" in result or "summary" in result:
            return json.dumps(result, indent=2)

        log.warning("Claude response missing briefing_text key -- using raw output")
        return str(result)

    return None


# ---------------------------------------------------------------------------
# Fallback briefing (no Claude CLI available)
# ---------------------------------------------------------------------------
def generate_fallback_briefing(context: Dict[str, Any], is_evening: bool = False) -> str:
    """Generate a simple briefing without Claude (when CLI not available)."""
    date_str = context["date"]

    if is_evening:
        lines = [f"<b>AEGIS EVENING BRIEFING -- {date_str}</b>", ""]
        metrics = context.get("today_metrics")
        if metrics and metrics.get("total_trades", 0) > 0:
            pnl = metrics.get("total_pnl", 0)
            trades = metrics.get("total_trades", 0)
            wins = metrics.get("wins", 0)
            wr = wins / trades if trades > 0 else 0
            lines.append(f"<b>Day Summary:</b>")
            lines.append(f"  P&L: GBP {pnl:+.2f}")
            lines.append(f"  Trades: {trades} ({wins}W/{trades - wins}L)")
            lines.append(f"  WR: {wr:.0%}")
        else:
            lines.append("<b>No trades today.</b>")

        vetoes = context.get("gate_vetoes", [])
        if vetoes:
            lines.append(f"\n<b>Vetoes:</b> {len(vetoes)} total")
    else:
        lines = [f"<b>AEGIS MORNING BRIEFING -- {date_str}</b>", ""]

        # System health
        health = context.get("system_health")
        if health:
            status = health.get("overall_status", "unknown")
            lines.append(f"<b>System:</b> {status.upper()}")
            for check in health.get("checks", []):
                if check.get("status") in ("critical", "warning"):
                    lines.append(f"  {check['name']}: {check.get('detail', '?')}")

        # Nightly review
        review = context.get("nightly_review")
        if review and not review.get("skipped"):
            grade = review.get("performance_grade", "?")
            lines.append(f"\n<b>Yesterday:</b> Grade {grade}")
            summary = review.get("executive_summary", "")
            if summary:
                lines.append(f"  {summary}")
        else:
            lines.append("\n<b>Yesterday:</b> No trades or review unavailable")

        # Challenger summary
        challenger = context.get("challenger")
        if challenger and challenger.get("challenges"):
            verdicts = {}
            for c in challenger["challenges"]:
                v = c.get("verdict", "?")
                verdicts[v] = verdicts.get(v, 0) + 1
            verdict_str = ", ".join(f"{v}:{n}" for v, n in sorted(verdicts.items()))
            lines.append(f"\n<b>Challenger:</b> {verdict_str}")

        # Approval log
        approvals = context.get("approval_log", [])
        applied = [a for a in approvals if a.get("action") == "APPLIED"]
        if applied:
            lines.append(f"\n<b>Overnight Approvals:</b> {len(applied)} applied")

        # 7-day
        ctx7d = context.get("7day_context")
        if ctx7d and ctx7d.get("actual_days", 0) > 0:
            rp = ctx7d.get("recent_performance", {})
            lines.append(f"\n<b>7-Day:</b> WR {rp.get('win_rate_7d', 0):.0%}, "
                         f"PF {rp.get('pf_7d', 0):.1f}, "
                         f"PnL GBP {rp.get('total_pnl_7d', 0):+.0f}")

        # Telemetry
        telem = context.get("telemetry")
        if telem:
            lines.append(f"\n<b>Engine:</b> regime={telem.get('regime', '?')}, "
                         f"positions={telem.get('positions', '?')}, "
                         f"equity={telem.get('equity', '?')}")

    lines.append(f"\n<i>Generated {datetime.now(timezone.utc).strftime('%H:%M UTC')} (fallback, no Claude)</i>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------
def run_briefing(
    dry_run: bool = False,
    send_tg: bool = False,
    is_evening: bool = False,
) -> Optional[str]:
    """Generate and distribute operator briefing."""
    mode = "evening" if is_evening else "morning"
    log.info("S11/S12: Generating %s briefing", mode)

    if is_evening:
        context = assemble_evening_context()
        prompt = build_evening_prompt(context)
    else:
        context = assemble_morning_context()
        prompt = build_morning_prompt(context)

    if dry_run:
        print("=" * 60)
        print(f"  N6b DRY RUN -- {mode.capitalize()} Briefing Prompt")
        print("=" * 60)
        print(prompt)
        return None

    # Try Claude first, fall back to template
    briefing = call_claude(prompt, is_evening=is_evening)

    if not briefing:
        log.info("Using fallback briefing (Claude CLI unavailable)")
        briefing = generate_fallback_briefing(context, is_evening=is_evening)

    # Save briefing
    BRIEFING_DIR.mkdir(parents=True, exist_ok=True)
    date_str = context["date"]
    suffix = "evening" if is_evening else "morning"
    briefing_path = BRIEFING_DIR / f"briefing_{date_str}_{suffix}.txt"
    try:
        with open(briefing_path, "w") as f:
            f.write(briefing)
        log.info("Briefing saved: %s", briefing_path)
    except OSError as e:
        log.error("Failed to save briefing: %s", e)

    # Send via Telegram
    if send_tg:
        import re

        def _sanitize_telegram_html(text: str) -> str:
            """Escape < and > that aren't valid Telegram HTML tags.

            Telegram only supports: <b>, <i>, <u>, <s>, <code>, <pre>, <a>.
            Everything else with < > must be escaped.
            """
            allowed_tags = r'</?(?:b|i|u|s|code|pre|a(?:\s[^>]*)?)>'
            parts = []
            last_end = 0
            for m in re.finditer(allowed_tags, text):
                # Escape any < > between last match and this one
                between = text[last_end:m.start()]
                between = between.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                parts.append(between)
                parts.append(m.group())  # Keep valid tag as-is
                last_end = m.end()
            # Escape remaining text after last tag
            remaining = text[last_end:]
            remaining = remaining.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            parts.append(remaining)
            return "".join(parts)

        sanitized = _sanitize_telegram_html(briefing)
        success = send_telegram(sanitized, parse_mode="HTML")
        if not success:
            log.info("HTML parse failed, retrying as plain text")
            plain = re.sub(r'<[^>]+>', '', briefing)
            success = send_telegram(plain, parse_mode=None)
        if success:
            log.info("Briefing sent via Telegram")
        else:
            log.warning("Telegram send failed (both HTML and plain)")

    return briefing


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [Briefing] %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Claude Operator Briefing (Sprint S11/S12)")
    parser.add_argument("--send-telegram", action="store_true", help="Send via Telegram")
    parser.add_argument("--dry-run", action="store_true", help="Show prompt only")
    parser.add_argument("--evening", action="store_true", help="Generate evening briefing instead of morning")
    args = parser.parse_args()

    briefing = run_briefing(
        dry_run=args.dry_run,
        send_tg=args.send_telegram,
        is_evening=args.evening,
    )

    if briefing and not args.dry_run:
        print(briefing)


if __name__ == "__main__":
    main()
