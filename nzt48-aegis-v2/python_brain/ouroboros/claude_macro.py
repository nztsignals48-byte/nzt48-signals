"""Sprint S16: Claude Pre-Event Macro Intelligence.

Reads /app/config/macro_calendar.json for upcoming economic events.
30 minutes before FOMC/NFP/CPI/PMI/major earnings, Claude assesses:
expected impact, recommended blackout extension (max 60min).
FLATTEN recommendations require operator Telegram approval.

Usage: python3 -m python_brain.ouroboros.claude_macro
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
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
MACRO_CALENDAR = CONFIG_DIR / "macro_calendar.json"
MACRO_OUTPUT_DIR = DATA_DIR / "claude" / "macro"

# Look-ahead window: trigger assessment 30 minutes before event
LOOKAHEAD_MINUTES = 30
# Maximum blackout extension Claude can recommend
MAX_BLACKOUT_MINUTES = 60
# Event types that are high-impact by default
HIGH_IMPACT_TYPES = {"FOMC", "NFP", "CPI", "PMI", "GDP", "ECB", "BOE"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Claude-Macro] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("claude_macro")


# ---------------------------------------------------------------------------
# Calendar loading
# ---------------------------------------------------------------------------
def load_calendar() -> List[Dict[str, Any]]:
    """Load macro events from calendar JSON."""
    if not MACRO_CALENDAR.exists():
        log.warning("Macro calendar not found: %s", MACRO_CALENDAR)
        return []
    try:
        with open(MACRO_CALENDAR) as f:
            data = json.load(f)
        events = data.get("events", [])
        log.info("Loaded %d macro events from calendar", len(events))
        return events
    except (json.JSONDecodeError, IOError) as e:
        log.warning("Failed to load macro calendar: %s", e)
        return []


def find_upcoming_events(
    events: List[Dict[str, Any]],
    lookahead_minutes: int = LOOKAHEAD_MINUTES,
) -> List[Dict[str, Any]]:
    """Find events occurring within the lookahead window from now."""
    now = datetime.now(timezone.utc)
    window_start = now
    window_end = now + timedelta(minutes=lookahead_minutes + 5)  # slight buffer

    upcoming = []
    for event in events:
        try:
            date_str = event.get("date", "")
            time_str = event.get("time_utc", "")
            if not date_str or not time_str:
                continue
            event_dt = datetime.strptime(
                f"{date_str} {time_str}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=timezone.utc)

            # Check if event is within [now, now + lookahead + 5min]
            # We want events that are coming up in the next ~30 minutes
            time_until = (event_dt - now).total_seconds() / 60.0
            if -5 <= time_until <= lookahead_minutes + 5:
                event["event_datetime"] = event_dt.isoformat()
                event["minutes_until"] = round(time_until, 1)
                upcoming.append(event)
        except (ValueError, TypeError) as e:
            log.warning("Failed to parse event date: %s — %s", event, e)
            continue

    return upcoming


# ---------------------------------------------------------------------------
# Claude assessment
# ---------------------------------------------------------------------------
def build_macro_prompt(
    events: List[Dict[str, Any]],
    open_positions: List[Dict[str, Any]],
) -> str:
    """Build prompt for Claude macro event assessment."""
    events_str = json.dumps(events, indent=2)
    positions_str = json.dumps(open_positions, indent=2) if open_positions else "[]"

    return f"""You are the AEGIS V2 macro event analyst. The following economic events are imminent.

UPCOMING EVENTS:
{events_str}

CURRENT OPEN POSITIONS:
{positions_str}

Assess each event and provide recommendations:

1. Expected impact on our positions (leveraged ETPs on NASDAQ, S&P, gold, etc.)
2. Recommended blackout extension in minutes (0 to {MAX_BLACKOUT_MINUTES}, in 5-min increments)
3. Whether to FLATTEN any specific positions (requires operator Telegram approval)
4. Confidence in assessment

RULES:
- Blackout extension max is {MAX_BLACKOUT_MINUTES} minutes
- FLATTEN recommendations MUST set requires_operator_approval = true
- For FOMC/NFP/CPI: minimum 15 min blackout recommended unless consensus is very clear
- For earnings: only relevant if we hold the underlying or a correlated ETP
- Consider both first-order (direct) and second-order (correlation) effects

Return JSON:
{{
  "date": "YYYY-MM-DD",
  "status": "ok",
  "confidence": "HIGH|MEDIUM|LOW",
  "events_assessed": [
    {{
      "type": "<event type>",
      "description": "<event description>",
      "minutes_until": <float>,
      "expected_impact": "LOW|MEDIUM|HIGH|CRITICAL",
      "impact_direction": "BULLISH|BEARISH|NEUTRAL|UNCERTAIN",
      "blackout_extension_minutes": <int 0-60>,
      "flatten_tickers": ["<ticker>"],
      "reasoning": "<1-2 sentence analysis>"
    }}
  ],
  "overall_recommendation": "NORMAL|CAUTIOUS|DEFENSIVE|FLATTEN",
  "requires_operator_approval": true|false,
  "summary": "<1-2 sentence overall assessment>"
}}

REGIME FRAMEWORK (Campbell/Lo/MacKinlay, Bollerslev GARCH):
Do NOT predict directional price impact of economic data. Estimate Volatility Shock Expansion only.
1. Treat all Tier-1 events (FOMC, CPI, NFP, BOE) as volatility clustering triggers.
2. Output a Velocity Cap Modifier (e.g., 0.5x to halve trading speed) and a Kelly Fraction Modifier (e.g., 0.25x to reduce sizing).
3. Require system to wait for Volatility Crush (drop in VIX or asset-specific ATR) before returning Kelly to 1.0x.
4. Do NOT recommend time-based blackouts. Recommend mathematical constraint multipliers."""


def load_open_positions_summary() -> List[Dict[str, Any]]:
    """Load summary of currently open positions from WAL."""
    wal_path = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events")) / "current.ndjson"
    if not wal_path.exists():
        return []

    # Track net open positions
    opened: Dict[str, Dict[str, Any]] = {}
    closed_syms: Dict[str, int] = {}

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
                if "PositionOpened" in payload:
                    po = payload["PositionOpened"]
                    sym = po.get("symbol", "")
                    if sym:
                        opened[sym] = {
                            "symbol": sym,
                            "entry_price": po.get("entry_price", 0),
                            "qty": po.get("qty", 0),
                            "side": po.get("side", "Long"),
                        }
                elif "PositionClosed" in payload:
                    sym = payload["PositionClosed"].get("symbol", "")
                    if sym:
                        closed_syms[sym] = closed_syms.get(sym, 0) + 1
    except IOError:
        pass

    # Return positions that have more opens than closes
    positions = []
    for sym, info in opened.items():
        if closed_syms.get(sym, 0) < 1:  # simplified: at least one open not closed
            positions.append(info)

    return positions


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_macro() -> int:
    """Execute pre-event macro intelligence assessment."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    log.info("Macro intelligence starting for %s", date_str)

    # Load calendar
    all_events = load_calendar()
    if not all_events:
        log.info("No macro calendar events — nothing to assess")
        return 0

    # Find upcoming events
    upcoming = find_upcoming_events(all_events)
    if not upcoming:
        log.info("No macro events in the next %d minutes", LOOKAHEAD_MINUTES)
        return 0

    log.info("Found %d upcoming macro events:", len(upcoming))
    for evt in upcoming:
        log.info(
            "  %s: %s (in %.0f min)",
            evt.get("type", "?"), evt.get("description", "?"),
            evt.get("minutes_until", 0),
        )

    # Load open positions
    open_positions = load_open_positions_summary()
    log.info("Open positions: %d", len(open_positions))

    # Query Claude
    prompt = build_macro_prompt(upcoming, open_positions)
    context = load_context_files([
        str(CONFIG_DIR / "config.toml"),
        str(DATA_DIR / "persistent_memory.json"),
    ])
    system_ctx = build_context_string(context)

    result = claude_query(prompt, system_context=system_ctx)
    if result is None:
        log.error("Claude query failed — using conservative defaults")
        # Conservative fallback: recommend blackout for all high-impact events
        result = {
            "date": date_str,
            "status": "fallback",
            "confidence": "LOW",
            "events_assessed": [],
            "overall_recommendation": "CAUTIOUS",
            "requires_operator_approval": False,
            "summary": "Claude unavailable. Conservative blackout applied for upcoming events.",
        }
        for evt in upcoming:
            etype = evt.get("type", "")
            is_high = etype.upper() in HIGH_IMPACT_TYPES
            result["events_assessed"].append({
                "type": etype,
                "description": evt.get("description", ""),
                "minutes_until": evt.get("minutes_until", 0),
                "expected_impact": "HIGH" if is_high else "MEDIUM",
                "impact_direction": "UNCERTAIN",
                "blackout_extension_minutes": 30 if is_high else 15,
                "flatten_tickers": [],
                "reasoning": "Conservative default due to Claude unavailability.",
            })

    # Enforce constraints
    for assessed in result.get("events_assessed", []):
        # Cap blackout extension
        ext = assessed.get("blackout_extension_minutes", 0)
        assessed["blackout_extension_minutes"] = min(max(int(ext), 0), MAX_BLACKOUT_MINUTES)
        # FLATTEN requires approval
        if assessed.get("flatten_tickers"):
            result["requires_operator_approval"] = True

    # Write output
    MACRO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = MACRO_OUTPUT_DIR / f"macro_{date_str}.json"
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)
    log.info("Macro assessment written: %s", output_path)

    # Telegram notification
    overall = result.get("overall_recommendation", "NORMAL")
    needs_approval = result.get("requires_operator_approval", False)

    if overall != "NORMAL" or needs_approval:
        event_lines = []
        for assessed in result.get("events_assessed", []):
            event_lines.append(
                f"  {assessed.get('type', '?')}: "
                f"impact={assessed.get('expected_impact', '?')}, "
                f"blackout={assessed.get('blackout_extension_minutes', 0)}min"
            )
        flatten_tickers = []
        for assessed in result.get("events_assessed", []):
            flatten_tickers.extend(assessed.get("flatten_tickers", []))

        msg = f"<b>Macro Alert: {overall}</b>\n"
        msg += "\n".join(event_lines) + "\n"
        if flatten_tickers:
            msg += f"<b>FLATTEN requested: {', '.join(flatten_tickers)}</b>\n"
            msg += "Reply APPROVE to confirm flatten.\n"
        msg += f"Confidence: {result.get('confidence', 'LOW')}"
        send_telegram(msg)

    return 0


def main():
    parser = argparse.ArgumentParser(description="Claude Pre-Event Macro Intelligence (Sprint S16)")
    parser.parse_args()  # No custom args — runs on schedule

    try:
        sys.exit(run_macro())
    except Exception as e:
        log.error("Claude macro intelligence crashed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
