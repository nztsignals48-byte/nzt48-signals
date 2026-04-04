"""Crypto Fear & Greed Index — macro risk-on/risk-off overlay.

Uses the Alternative.me Crypto Fear & Greed Index as a proxy for
global risk appetite. When crypto is in "Extreme Greed" (>80), risk
assets tend to be overbought. When in "Extreme Fear" (<20), risk-off
sentiment suggests caution for equity longs.

Signal overlay:
  - Extreme Greed (>80): -2 confidence on momentum longs (overbought market)
  - Greed (60-80): neutral
  - Neutral (40-60): neutral
  - Fear (20-40): +2 confidence on mean-reversion longs (contrarian)
  - Extreme Fear (<20): +3 confidence on deep value (max contrarian)

Data source: Alternative.me API (free, no key required, 1 req/sec).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("crypto_fear_greed")

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

_DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
_OUTPUT_PATH = _DATA_DIR / "crypto_fear_greed.json"
_API_URL = "https://api.alternative.me/fng/?limit=7"


def fetch_fear_greed() -> Optional[Dict[str, Any]]:
    """Fetch current Crypto Fear & Greed Index.

    Returns:
        Dict with value (0-100), classification, timestamp, or None.
    """
    if not _HAS_REQUESTS:
        return None

    try:
        resp = requests.get(_API_URL, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        entries = data.get("data", [])
        if not entries:
            return None

        latest = entries[0]
        value = int(latest.get("value", 50))
        classification = latest.get("value_classification", "Neutral")

        # 7-day average for smoothing
        if len(entries) >= 7:
            avg_7d = sum(int(e.get("value", 50)) for e in entries[:7]) / 7
        else:
            avg_7d = float(value)

        return {
            "value": value,
            "classification": classification,
            "avg_7d": round(avg_7d, 1),
            "timestamp": latest.get("timestamp", ""),
            "history": [{"value": int(e.get("value", 50)),
                         "date": e.get("timestamp", "")} for e in entries[:7]],
        }

    except Exception as e:
        log.warning("Crypto Fear/Greed fetch failed: %s", str(e)[:80])
        return None


def compute_macro_overlay(fg_data: Dict[str, Any]) -> Dict[str, Any]:
    """Compute macro overlay signal from Fear & Greed data.

    Returns:
        Dict with confidence_delta, regime_hint, description.
    """
    value = fg_data.get("value", 50)
    avg_7d = fg_data.get("avg_7d", 50.0)

    # Use 7-day average for stability
    effective = avg_7d

    delta = 0
    regime_hint = "neutral"
    desc = "neutral"

    if effective > 80:
        delta = -2
        regime_hint = "overbought"
        desc = "extreme_greed"
    elif effective > 60:
        delta = 0
        regime_hint = "risk_on"
        desc = "greed"
    elif effective > 40:
        delta = 0
        regime_hint = "neutral"
        desc = "neutral"
    elif effective > 20:
        delta = 2
        regime_hint = "contrarian_buy"
        desc = "fear"
    else:
        delta = 3
        regime_hint = "deep_value"
        desc = "extreme_fear"

    return {
        "confidence_delta": delta,
        "regime_hint": regime_hint,
        "description": desc,
        "value": value,
        "avg_7d": avg_7d,
    }


def run_fear_greed_scan(output_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Full pipeline: fetch + compute Fear/Greed overlay. Nightly step."""
    fg_data = fetch_fear_greed()
    if not fg_data:
        return None

    overlay = compute_macro_overlay(fg_data)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "raw_data": fg_data,
        "overlay": overlay,
    }

    if output_path is None:
        output_path = str(_OUTPUT_PATH)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    log.info("Crypto Fear/Greed: value=%d, 7d_avg=%.1f, overlay=%s (delta=%+d)",
             fg_data["value"], fg_data["avg_7d"],
             overlay["description"], overlay["confidence_delta"])

    return result
