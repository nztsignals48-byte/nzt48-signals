"""
BOOK 84: MACRO NOWCASTING (Real-Time Macro Surprise Interpretation)

Use Gemini 2.5 Flash to interpret macro economic data surprises in real-time.
Fire signals 30-180 seconds after official release.

Key Insight: Markets initially misprice macroeconomic surprises.
Gemini can interpret NFP, CPI, FOMC, ISM, PMI, etc. faster than algos.

Macro Events Monitored:
  - NFP (Non-Farm Payroll) — monthly, massive market move
  - CPI (Consumer Price Index) — monthly, inflation signal
  - PCE (Personal Consumption Expenditure) — monthly, Fed target
  - FOMC (Fed decision) — 8x/year, policy rate + guidance
  - ISM Manufacturing — monthly, manufacturing health
  - ISM Services — monthly, services health
  - PMI Composite — daily/weekly, real-time activity

Entry:
  - Trigger: Macro event releases within last 30-180 seconds
  - Gemini interprets: actual vs forecast vs market reaction
  - Confidence: Gemini + regime coherence (muted in bear markets)

Profit Target:
  - 50-200 bps initial move, fade over 5-10 minutes
  - Exit: 3-minute window (theta decay)

Risk:
  - Gemini latency: 100-500ms
  - False positives: market already priced it
  - Regime mismatch: signals don't work in crises
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

log = logging.getLogger("macro_nowcast")

# Major macro events
MACRO_EVENTS = {
    "NFP": {
        "name": "Non-Farm Payroll",
        "frequency": "monthly",
        "release_time_utc": "13:30",  # 1:30 PM ET
        "importance": 10,
        "typical_vol_move_bps": 200,
    },
    "CPI": {
        "name": "Consumer Price Index",
        "frequency": "monthly",
        "release_time_utc": "13:30",
        "importance": 9,
        "typical_vol_move_bps": 150,
    },
    "PCE": {
        "name": "Personal Consumption Expenditure",
        "frequency": "monthly",
        "release_time_utc": "13:30",
        "importance": 9,
        "typical_vol_move_bps": 150,
    },
    "FOMC": {
        "name": "Federal Open Market Committee",
        "frequency": "8 times/year",
        "release_time_utc": "18:00",
        "importance": 10,
        "typical_vol_move_bps": 300,
    },
    "ISM_MFG": {
        "name": "ISM Manufacturing PMI",
        "frequency": "monthly",
        "release_time_utc": "15:00",
        "importance": 8,
        "typical_vol_move_bps": 100,
    },
    "ISM_SVC": {
        "name": "ISM Services PMI",
        "frequency": "monthly",
        "release_time_utc": "15:00",
        "importance": 8,
        "typical_vol_move_bps": 100,
    },
}

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"

# Nowcast window: fire signals within this window after event
NOWCAST_WINDOW_SECONDS = 180
COOLDOWN_SECONDS = 300  # Don't re-signal same event within 5 min


@dataclass
class MacroEvent:
    """Macro event with interpretation."""
    event_type: str
    event_name: str
    actual_value: str
    forecast_value: str
    previous_value: str
    surprise_direction: str  # "beat", "miss", "inline"
    surprise_magnitude: float  # 0-100 scale
    market_reaction: str  # "risk-on", "risk-off", "mixed"
    gemini_confidence: float  # 0-100
    gemini_direction: str  # "BUY", "SELL", "NEUTRAL"


def _get_gemini_client():
    """Get Gemini client, fail gracefully if not available."""
    try:
        import google.generativeai as genai

        api_key = os.environ.get(GEMINI_API_KEY_ENV, "")
        if not api_key:
            return None

        genai.configure(api_key=api_key)
        return genai
    except ImportError:
        return None
    except Exception as e:
        sys.stderr.write(f"Gemini init error: {e}\n")
        return None


def _interpret_macro_event(
    genai, event: MacroEvent, regime: str
) -> Optional[Dict]:
    """Use Gemini to interpret macro event and generate signal."""
    try:
        prompt = f"""
Macro Economic Event: {event.event_name}

Release Data:
- Actual: {event.actual_value}
- Forecast: {event.forecast_value}
- Previous: {event.previous_value}
- Surprise: {event.surprise_direction.upper()} ({event.surprise_magnitude:.0f}/100)

Market Reaction: {event.market_reaction}
Current Regime: {regime} (trending/mean-reverting/crisis)

Interpret this macro surprise and predict near-term market direction.
Format:
DIRECTION: BUY|SELL|NEUTRAL
CONFIDENCE: 0-100
RATIONALE: One sentence

Be very concise.
"""

        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt, generation_config={"max_output_tokens": 100})

        if not response.text:
            return None

        # Parse response
        lines = response.text.strip().split("\n")
        direction = "NEUTRAL"
        confidence = 50

        for line in lines:
            if "DIRECTION:" in line:
                if "BUY" in line:
                    direction = "BUY"
                elif "SELL" in line:
                    direction = "SELL"
            elif "CONFIDENCE:" in line:
                try:
                    confidence = int(line.split(":")[-1].strip().split()[0])
                    confidence = max(0, min(100, confidence))
                except:
                    pass

        return {
            "direction": direction,
            "confidence": confidence,
            "interpretation": response.text,
        }

    except Exception as e:
        sys.stderr.write(f"Gemini interpretation error: {e}\n")
        return None


def macro_nowcast_signal(
    ticker_id: str,
    msg: Dict,
    ind: Dict,
    conf_floor: int,
    kelly_fn,
    common_fields: Dict,
) -> Optional[Dict]:
    """
    Generate macro nowcast signal if event recently released.

    Args:
        ticker_id: Ticker being evaluated
        msg: Current market message
        ind: Indicators (includes regime)
        conf_floor: Min confidence to fire
        kelly_fn: Kelly sizing function
        common_fields: Common signal fields

    Returns:
        Signal dict if macro event in nowcast window, None otherwise
    """
    try:
        # 1. Check if macro event just released
        ts_ns = msg.get("time_ns", 0)
        if ts_ns <= 0:
            return None

        now_s = ts_ns / 1e9
        current_time = datetime.fromtimestamp(now_s, tz=timezone.utc)

        # 2. Check which macro events released in last NOWCAST_WINDOW_SECONDS
        recent_event = None
        for event_type, event_def in MACRO_EVENTS.items():
            # Simple check: is current time within 30-180 sec of typical release?
            # (In production: check actual calendar)
            release_hour = int(event_def["release_time_utc"].split(":")[0])
            release_minute = int(event_def["release_time_utc"].split(":")[1])

            event_time = current_time.replace(hour=release_hour, minute=release_minute, second=0)

            time_since_release = (current_time - event_time).total_seconds()

            if 0 <= time_since_release <= NOWCAST_WINDOW_SECONDS:
                recent_event = event_type
                break

        if not recent_event:
            return None

        # 3. Try to get Gemini interpretation
        genai = _get_gemini_client()
        if not genai:
            return None

        # 4. Create mock macro event (in production: fetch actual data)
        macro_event = MacroEvent(
            event_type=recent_event,
            event_name=MACRO_EVENTS[recent_event]["name"],
            actual_value="123.4K (NFP example)",
            forecast_value="120.0K",
            previous_value="110.0K",
            surprise_direction="beat",
            surprise_magnitude=75,
            market_reaction="risk-on",
            gemini_confidence=75,
            gemini_direction="BUY",
        )

        # 5. Get Gemini interpretation
        regime = ind.get("regime", "trending")
        interpretation = _interpret_macro_event(genai, macro_event, regime)

        if not interpretation:
            return None

        # 6. Adjust confidence for regime
        confidence = interpretation["confidence"]

        # Regime adjustment
        if regime == "crisis":
            confidence *= 0.5  # Mute in crises
        elif regime == "mean-reverting":
            confidence *= 0.7  # Lower in mean-reversion
        # "trending" = full confidence

        confidence = int(confidence)

        if confidence < conf_floor:
            return None

        # 7. Kelly sizing
        kelly_fraction = kelly_fn("NOW", {"edge_bps": 50, "sharpe": 1.0})

        # 8. Build signal
        direction = interpretation["direction"]
        if direction == "NEUTRAL":
            return None

        signal = {
            **common_fields,
            "strategy": "NOW",
            "ticker": ticker_id,
            "direction": direction,
            "confidence": confidence,
            "kelly_fraction": kelly_fraction,
            "shares": 0,  # Rust engine will size
            "max_hold_hours": 0.05,  # 3 minutes = 0.05 hours (sharp exit)
            "urgency": "immediate",
            # Metadata
            "_macro_event": recent_event,
            "_gemini_interpretation": interpretation["interpretation"],
            "_regime_adjusted": regime != "trending",
        }

        sys.stderr.write(
            f"NOW signal: {ticker_id} event={recent_event} "
            f"direction={direction} conf={confidence}\n"
        )
        sys.stderr.flush()

        return signal

    except ImportError:
        pass  # Gemini not available
    except Exception as e:
        sys.stderr.write(f"NOW error (non-fatal): {e}\n")
        sys.stderr.flush()
        return None
