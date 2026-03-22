"""Sprint S15: Claude Anomaly Assessment — Event-Triggered Market Analysis.

Accepts anomaly data via command-line JSON or reads from a trigger file.
Triggers: spread >3x normal, volume >5x, price gap >2%, VIX spike >3pts/30min.
Claude assesses: severity, likely cause, recommended action.
ADVISORY ONLY — engine makes final decision.

Usage:
  python3 -m python_brain.ouroboros.claude_anomaly --data '{"type":"spread_spike","ticker":"QQQ3.L","value":0.45,"normal":0.12}'
  python3 -m python_brain.ouroboros.claude_anomaly --trigger-file /app/data/anomaly_trigger.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

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
ANOMALY_DIR = DATA_DIR / "claude" / "anomalies"
TRIGGER_FILE = DATA_DIR / "anomaly_trigger.json"

# Anomaly thresholds (for pre-classification before Claude)
THRESHOLDS = {
    "spread_spike": 3.0,       # spread > 3x normal
    "volume_spike": 5.0,       # volume > 5x normal
    "price_gap": 2.0,          # price gap > 2%
    "vix_spike": 3.0,          # VIX change > 3pts in 30min
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Claude-Anomaly] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("claude_anomaly")


# ---------------------------------------------------------------------------
# Pre-classification
# ---------------------------------------------------------------------------
def pre_classify_severity(anomaly: Dict[str, Any]) -> str:
    """Pre-classify anomaly severity based on magnitude before Claude review."""
    atype = anomaly.get("type", "unknown")
    value = anomaly.get("value", 0.0)
    normal = anomaly.get("normal", 1.0)

    if normal <= 0:
        normal = 1.0
    ratio = value / normal

    if atype == "spread_spike":
        if ratio > 10:
            return "CRITICAL"
        elif ratio > 5:
            return "HIGH"
        elif ratio > 3:
            return "MEDIUM"
        return "LOW"
    elif atype == "volume_spike":
        if ratio > 20:
            return "CRITICAL"
        elif ratio > 10:
            return "HIGH"
        elif ratio > 5:
            return "MEDIUM"
        return "LOW"
    elif atype == "price_gap":
        pct = abs(value)
        if pct > 5:
            return "CRITICAL"
        elif pct > 3:
            return "HIGH"
        elif pct > 2:
            return "MEDIUM"
        return "LOW"
    elif atype == "vix_spike":
        pts = abs(value)
        if pts > 8:
            return "CRITICAL"
        elif pts > 5:
            return "HIGH"
        elif pts > 3:
            return "MEDIUM"
        return "LOW"
    return "MEDIUM"


# ---------------------------------------------------------------------------
# Claude assessment
# ---------------------------------------------------------------------------
def build_anomaly_prompt(anomaly: Dict[str, Any], pre_severity: str) -> str:
    """Build prompt for Claude anomaly assessment."""
    anomaly_str = json.dumps(anomaly, indent=2)

    return f"""You are the AEGIS V2 market anomaly analyst. An anomaly has been detected.

ANOMALY DATA:
{anomaly_str}

PRE-CLASSIFIED SEVERITY: {pre_severity}

ANOMALY TYPES AND THRESHOLDS:
- spread_spike: spread exceeds {THRESHOLDS['spread_spike']}x normal baseline
- volume_spike: volume exceeds {THRESHOLDS['volume_spike']}x normal baseline
- price_gap: price gap exceeds {THRESHOLDS['price_gap']}%
- vix_spike: VIX change exceeds {THRESHOLDS['vix_spike']} points in 30 minutes

Assess this anomaly and provide:
1. Confirm or adjust severity: LOW / MEDIUM / HIGH / CRITICAL
2. Most likely cause (market-wide event, single-stock issue, data error, etc.)
3. Recommended action for the engine (this is ADVISORY ONLY — engine decides)

Possible recommended actions:
- MONITOR: Continue trading, watch closely
- WIDEN_STOPS: Increase Chandelier ATR multiplier temporarily
- REDUCE_SIZE: Cut position sizing by 50%
- PAUSE_ENTRIES: Stop new entries for this ticker/exchange
- PAUSE_ALL: Stop all new entries (market-wide event)
- FLATTEN_TICKER: Close positions in affected ticker (requires operator approval)
- NO_ACTION: Anomaly is within acceptable range or likely data error

Return JSON:
{{
  "date": "YYYY-MM-DD",
  "status": "ok",
  "confidence": "HIGH|MEDIUM|LOW",
  "anomaly_type": "<type>",
  "ticker": "<affected ticker or 'MARKET_WIDE'>",
  "severity": "LOW|MEDIUM|HIGH|CRITICAL",
  "likely_cause": "<1-sentence explanation>",
  "recommended_action": "<action>",
  "reasoning": "<2-3 sentence analysis>",
  "duration_minutes": <suggested monitoring duration>,
  "requires_operator_approval": true|false
}}"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_anomaly(anomaly_data: Optional[Dict[str, Any]] = None) -> int:
    """Execute anomaly assessment."""
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d_%H%M%S")

    # Load anomaly data from argument or trigger file
    if anomaly_data is None:
        if TRIGGER_FILE.exists():
            try:
                with open(TRIGGER_FILE) as f:
                    anomaly_data = json.load(f)
                log.info("Loaded anomaly from trigger file: %s", TRIGGER_FILE)
            except (json.JSONDecodeError, IOError) as e:
                log.error("Failed to load trigger file: %s", e)
                return 1
        else:
            log.error("No anomaly data provided and no trigger file found")
            return 1

    log.info(
        "Anomaly assessment: type=%s, ticker=%s, value=%s",
        anomaly_data.get("type", "unknown"),
        anomaly_data.get("ticker", "unknown"),
        anomaly_data.get("value", "N/A"),
    )

    # Pre-classify severity
    pre_severity = pre_classify_severity(anomaly_data)
    log.info("Pre-classified severity: %s", pre_severity)

    # Build prompt and query Claude
    prompt = build_anomaly_prompt(anomaly_data, pre_severity)
    context = load_context_files([
        str(CONFIG_DIR / "config.toml"),
        str(DATA_DIR / "persistent_memory.json"),
    ])
    system_ctx = build_context_string(context)

    result = claude_query(prompt, system_context=system_ctx)
    if result is None:
        log.error("Claude query failed — falling back to pre-classification")
        result = {
            "date": now.strftime("%Y-%m-%d"),
            "status": "fallback",
            "confidence": "LOW",
            "anomaly_type": anomaly_data.get("type", "unknown"),
            "ticker": anomaly_data.get("ticker", "unknown"),
            "severity": pre_severity,
            "likely_cause": "Claude query failed — using pre-classification only",
            "recommended_action": "MONITOR" if pre_severity in ("LOW", "MEDIUM") else "PAUSE_ENTRIES",
            "reasoning": "Automated fallback based on threshold pre-classification.",
            "duration_minutes": 30,
            "requires_operator_approval": pre_severity == "CRITICAL",
        }

    # Enrich with input data and metadata
    result["input_anomaly"] = anomaly_data
    result["pre_classified_severity"] = pre_severity
    result["assessment_timestamp"] = now.isoformat()
    result["mode"] = "ADVISORY"

    # Write output
    ANOMALY_DIR.mkdir(parents=True, exist_ok=True)
    output_path = ANOMALY_DIR / f"anomaly_{timestamp}.json"
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)
    log.info("Anomaly assessment written: %s", output_path)

    severity = result.get("severity", pre_severity)
    action = result.get("recommended_action", "MONITOR")
    log.info("Assessment: severity=%s, action=%s", severity, action)

    # Send Telegram for HIGH/CRITICAL
    if severity in ("HIGH", "CRITICAL"):
        ticker = result.get("ticker", "unknown")
        cause = result.get("likely_cause", "unknown")
        msg = (
            f"<b>ANOMALY {severity}</b>\n"
            f"Type: {anomaly_data.get('type', 'unknown')}\n"
            f"Ticker: {ticker}\n"
            f"Action: {action}\n"
            f"Cause: {cause}\n"
            f"Mode: ADVISORY ONLY"
        )
        send_telegram(msg)

    return 0


def main():
    parser = argparse.ArgumentParser(description="Claude Anomaly Assessment (Sprint S15)")
    parser.add_argument("--data", type=str, help="Anomaly data as JSON string")
    parser.add_argument("--trigger-file", type=str, help="Path to anomaly trigger JSON file")
    args = parser.parse_args()

    anomaly_data = None

    if args.data:
        try:
            anomaly_data = json.loads(args.data)
        except json.JSONDecodeError as e:
            log.error("Invalid JSON in --data: %s", e)
            sys.exit(1)
    elif args.trigger_file:
        trigger_path = Path(args.trigger_file)
        if trigger_path.exists():
            try:
                with open(trigger_path) as f:
                    anomaly_data = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                log.error("Failed to load trigger file %s: %s", args.trigger_file, e)
                sys.exit(1)
        else:
            log.error("Trigger file not found: %s", args.trigger_file)
            sys.exit(1)

    try:
        sys.exit(run_anomaly(anomaly_data))
    except Exception as e:
        log.error("Claude anomaly assessment crashed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
