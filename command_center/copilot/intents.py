"""
command_center/copilot/intents.py
==================================
Intent classification for the NZT-48 Operator Copilot.

Uses regex pattern matching (NOT LLM) to classify natural-language
queries into structured intents with extracted parameters.

Supported intents:
    SCAN_NOW          — trigger an on-demand pipeline preview scan
    EXPLAIN_SIGNAL    — deep-dive on a specific ticker's signal
    WHY_NOT_TICKER    — explain why a ticker was rejected / didn't qualify
    HEALTH_SUMMARY    — system data health and operational status
    SHOW_TOP_TRADES   — list current top-ranked plays
    SHOW_CLOSEST_MISSES — tickers that almost qualified
    WHAT_CHANGED      — diff since last tick / scan cycle
    REGIME_STATUS     — current market regime and strategy routing
    UNKNOWN           — fallback for unrecognised queries
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional


class Intent(Enum):
    SCAN_NOW = "scan_now"
    EXPLAIN_SIGNAL = "explain_signal"
    WHY_NOT_TICKER = "why_not_ticker"
    HEALTH_SUMMARY = "health_summary"
    SHOW_TOP_TRADES = "show_top_trades"
    SHOW_CLOSEST_MISSES = "show_closest_misses"
    WHAT_CHANGED = "what_changed"
    REGIME_STATUS = "regime_status"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Regex patterns — order matters: first match wins
# ---------------------------------------------------------------------------

_INTENT_PATTERNS: list[tuple[Intent, re.Pattern]] = [
    # SCAN_NOW — user wants a fresh scan
    (Intent.SCAN_NOW, re.compile(
        r"\b(scan\s*now|run\s*scan|trade\s*now|qualifying\s*signals?|"
        r"what\s*can\s*i\s*trade|find\s*trades?|fresh\s*scan|"
        r"scan\s*the\s*market|run\s*the\s*engine|pipeline\s*scan|"
        r"live\s*scan|quick\s*scan)\b",
        re.IGNORECASE,
    )),

    # EXPLAIN_SIGNAL — user wants breakdown of a specific ticker's signal
    (Intent.EXPLAIN_SIGNAL, re.compile(
        r"\b(explain|break\s*down|tell\s*me\s*about|detail|deep\s*dive|"
        r"analyse|analyze|walk\s*me\s*through|what\s*about)\b",
        re.IGNORECASE,
    )),

    # WHY_NOT_TICKER — user asking why something was rejected
    (Intent.WHY_NOT_TICKER, re.compile(
        r"\b(why\s*not|why\s*isn'?t|why\s*was\s*.*\s*rejected|"
        r"why\s*didn'?t\s*.*\s*qualify|why\s*no\s*signal|"
        r"why\s*excluded|why\s*blocked|what\s*failed|"
        r"why\s*did\s*.*\s*fail|what\s*blocked)\b",
        re.IGNORECASE,
    )),

    # HEALTH_SUMMARY — system status check
    (Intent.HEALTH_SUMMARY, re.compile(
        r"\b(health|degraded|system\s*status|data\s*health|"
        r"are\s*we\s*ok|system\s*check|ops\s*status|"
        r"operational|diagnostics|heartbeat|watchdog)\b",
        re.IGNORECASE,
    )),

    # SHOW_TOP_TRADES — current best plays
    (Intent.SHOW_TOP_TRADES, re.compile(
        r"\b(top\s*trades?|best\s*trades?|top\s*signals?|"
        r"core\s*trades?|show\s*trades?|current\s*plays?|"
        r"active\s*signals?|best\s*plays?|ranked\s*plays?|"
        r"what'?s?\s*qualifying)\b",
        re.IGNORECASE,
    )),

    # SHOW_CLOSEST_MISSES — near misses
    (Intent.SHOW_CLOSEST_MISSES, re.compile(
        r"\b(closest?\s*miss(?:es)?|almost\s*qualif(?:ied|ying)|"
        r"near\s*miss(?:es)?|close\s*calls?|almost\s*made\s*it|"
        r"narrowly\s*missed|edge\s*cases?)\b",
        re.IGNORECASE,
    )),

    # WHAT_CHANGED — diff / updates since last tick
    (Intent.WHAT_CHANGED, re.compile(
        r"\b(what\s*changed|what'?s?\s*new|since\s*last|"
        r"any\s*updates?|diff|delta|changelog|"
        r"what\s*happened|latest\s*changes?)\b",
        re.IGNORECASE,
    )),

    # REGIME_STATUS — market regime and routing
    (Intent.REGIME_STATUS, re.compile(
        r"\b(regime|market\s*state|bull\s*or\s*bear|"
        r"trend\s*state|current\s*trend|market\s*regime|"
        r"strategy\s*routing|which\s*strategies?|"
        r"market\s*mode|risk\s*off|risk\s*on)\b",
        re.IGNORECASE,
    )),
]


# ---------------------------------------------------------------------------
# Ticker extraction — uppercase + optional digits + optional .L suffix
# ---------------------------------------------------------------------------

_TICKER_RE = re.compile(r"\b([A-Z]{2,5}\d{0,2}(?:\.L)?)\b")

# Exclude common English words that look like tickers
_TICKER_BLACKLIST = {
    "CORE", "ALL", "THE", "AND", "FOR", "NOT", "WHY", "HOW",
    "NOW", "NEW", "TOP", "RUN", "SET", "LOW", "HIGH", "LONG",
    "SHORT", "BULL", "BEAR", "HALT", "SHOW", "FIND", "WHAT",
    "SCAN", "TELL", "BEST", "NEAR", "MISS", "DIFF", "MODE",
    "RISK", "DATA", "GATE", "PASS", "FAIL", "WARN", "AMBER",
    "GREEN", "RED", "OK", "OPS", "TRADE", "WATCH", "INTEL",
}


# ---------------------------------------------------------------------------
# Lane extraction
# ---------------------------------------------------------------------------

_LANE_RE = re.compile(r"\b(CORE|OPPORTUNITY|INTEL)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Track filter extraction
# ---------------------------------------------------------------------------

_TRACK_RE = re.compile(r"\b(scalp|swing|intraday)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Confidence filter extraction
# ---------------------------------------------------------------------------

_CONFIDENCE_RE = re.compile(
    r"\b(?:confidence\s*([ABC])|([ABC])\s*confidence|"
    r"high\s*confidence|only\s*strict)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_intent(query: str) -> tuple[Intent, dict]:
    """Parse a natural-language query into an Intent + extracted parameters.

    Returns:
        (Intent, params) where params may contain:
            - ticker:     str or None
            - lane:       "CORE" | "OPPORTUNITY" | "INTEL" | "ALL"
            - track:      "SCALP" | "SWING" | "INTRADAY" or None
            - confidence: "A" | "B" | "C" or None
    """
    params: dict = {}

    # --- Extract ticker ---
    ticker_match = _TICKER_RE.findall(query)
    ticker = None
    for candidate in ticker_match:
        if candidate.upper() not in _TICKER_BLACKLIST:
            ticker = candidate.upper()
            break
    params["ticker"] = ticker

    # --- Extract lane ---
    lane_match = _LANE_RE.search(query)
    params["lane"] = lane_match.group(1).upper() if lane_match else "ALL"

    # --- Extract track ---
    track_match = _TRACK_RE.search(query)
    params["track"] = track_match.group(1).upper() if track_match else None

    # --- Extract confidence ---
    conf_match = _CONFIDENCE_RE.search(query)
    if conf_match:
        # Group 1 = "confidence A", group 2 = "A confidence"
        grade = conf_match.group(1) or conf_match.group(2)
        if grade:
            params["confidence"] = grade.upper()
        elif "high" in conf_match.group(0).lower() or "strict" in conf_match.group(0).lower():
            params["confidence"] = "A"
        else:
            params["confidence"] = None
    else:
        params["confidence"] = None

    # --- Match intent ---
    for intent, pattern in _INTENT_PATTERNS:
        if pattern.search(query):
            # For EXPLAIN_SIGNAL and WHY_NOT_TICKER, require a ticker
            if intent == Intent.EXPLAIN_SIGNAL and ticker is None:
                continue  # Fall through to next match
            if intent == Intent.WHY_NOT_TICKER and ticker is None:
                continue
            return intent, params

    return Intent.UNKNOWN, params
