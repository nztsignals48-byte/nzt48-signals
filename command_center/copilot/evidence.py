"""
command_center/copilot/evidence.py
===================================
Evidence collector for the NZT-48 Operator Copilot.

Resolves intent-specific artifact paths and returns structured evidence
dicts that handlers attach to their responses. Every evidence item includes
file existence checks so missing artifacts are surfaced gracefully.

Evidence dict schema:
    {
        "path":   str,       # relative path from project root
        "type":   str,       # artifact type tag (plays, drought, gate, etc.)
        "notes":  str,       # human-readable description or MISSING note
        "as_of":  str,       # ISO timestamp or file mtime
    }
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from command_center.copilot.intents import Intent

# Project root (nzt48-signals/)
_PROJECT_ROOT = Path(__file__).parent.parent.parent

# Standard artifact locations
_ARTIFACTS_DIR = _PROJECT_ROOT / "artifacts"
_DATA_DIR = _PROJECT_ROOT / "data"


def _today_str() -> str:
    """Today's date as YYYY-MM-DD for artifact directory lookup."""
    return date.today().isoformat()


def _file_mtime_iso(path: Path) -> str:
    """Return file modification time as ISO string, or empty if missing."""
    try:
        mtime = os.path.getmtime(path)
        return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    except (OSError, ValueError):
        return ""


def _check_artifact(rel_path: str, artifact_type: str, notes: str = "") -> dict:
    """Build an evidence dict for a single artifact file.

    Args:
        rel_path:      Path relative to project root.
        artifact_type: Tag like "plays", "drought", "gate_report", etc.
        notes:         Human-readable description.

    Returns:
        Evidence dict with existence check.
    """
    full_path = _PROJECT_ROOT / rel_path
    if full_path.exists():
        return {
            "path": rel_path,
            "type": artifact_type,
            "notes": notes or f"{artifact_type} artifact",
            "as_of": _file_mtime_iso(full_path),
        }
    else:
        return {
            "path": rel_path,
            "type": artifact_type,
            "notes": f"MISSING -- artifact not found: {rel_path}",
            "as_of": "",
        }


def _find_latest_session_dir() -> Optional[str]:
    """Find the latest session artifact directory for today.

    Looks in artifacts/<today>/ for session subdirectories and returns
    the most recently modified one. Falls back to yesterday if today
    has no artifacts yet.
    """
    for day_offset in (0, 1):
        try:
            from datetime import timedelta
            target_date = (date.today() - timedelta(days=day_offset)).isoformat()
            day_dir = _ARTIFACTS_DIR / target_date
            if not day_dir.is_dir():
                continue
            # Find all session subdirs, pick most recent by mtime
            subdirs = [d for d in day_dir.iterdir() if d.is_dir() and d.name != "universe"]
            if subdirs:
                latest = max(subdirs, key=lambda d: d.stat().st_mtime)
                return f"artifacts/{target_date}/{latest.name}"
        except Exception:
            continue
    return None


def collect_evidence(intent: Intent, params: dict, state: object = None) -> list[dict]:
    """Collect relevant artifact evidence for a given intent and parameters.

    Args:
        intent: The classified Intent.
        params: Extracted parameters (ticker, lane, etc.).
        state:  CommandCenterState object (may be None).

    Returns:
        List of evidence dicts, each with path, type, notes, as_of.
    """
    evidence: list[dict] = []
    session_dir = _find_latest_session_dir()

    if intent == Intent.SCAN_NOW:
        # Fresh scan — reference the plays and system_state artifacts
        if session_dir:
            evidence.append(_check_artifact(
                f"{session_dir}/plays.json", "plays",
                "Current plays from latest pipeline run",
            ))
            evidence.append(_check_artifact(
                f"{session_dir}/system_state.json", "system_state",
                "System state snapshot from latest run",
            ))
            evidence.append(_check_artifact(
                f"{session_dir}/drought.json", "drought",
                "Drought diagnostics (if no signals)",
            ))
            evidence.append(_check_artifact(
                f"{session_dir}/strategies.json", "strategies",
                "Active strategy routing from latest run",
            ))
        # Also reference signal log
        evidence.append(_check_artifact(
            "data/signal_log.jsonl", "signal_log",
            "Persistent signal log (all sessions)",
        ))

    elif intent == Intent.EXPLAIN_SIGNAL:
        ticker = params.get("ticker")
        if session_dir:
            evidence.append(_check_artifact(
                f"{session_dir}/plays.json", "plays",
                f"Play details for {ticker or 'unknown ticker'}",
            ))
            evidence.append(_check_artifact(
                f"{session_dir}/quality_report.json", "quality_report",
                "Quality report with score breakdowns",
            ))
            evidence.append(_check_artifact(
                f"{session_dir}/reliability.json", "reliability",
                "Data reliability assessment per ticker",
            ))

    elif intent == Intent.WHY_NOT_TICKER:
        ticker = params.get("ticker")
        if session_dir:
            evidence.append(_check_artifact(
                f"{session_dir}/plays.json", "plays",
                f"Gate report lookup for {ticker or 'unknown ticker'}",
            ))
            evidence.append(_check_artifact(
                f"{session_dir}/readiness.json", "readiness",
                "Universe readiness assessment",
            ))
            evidence.append(_check_artifact(
                f"{session_dir}/drought.json", "drought",
                "Drought / closest-miss diagnostics",
            ))

    elif intent == Intent.HEALTH_SUMMARY:
        if session_dir:
            evidence.append(_check_artifact(
                f"{session_dir}/system_state.json", "system_state",
                "Latest system state snapshot",
            ))
            evidence.append(_check_artifact(
                f"{session_dir}/reliability.json", "reliability",
                "Data reliability per ticker",
            ))
        evidence.append(_check_artifact(
            "data/session_status.json", "session_status",
            "PDF job PASS/FAIL status per session",
        ))

    elif intent == Intent.SHOW_TOP_TRADES:
        if session_dir:
            evidence.append(_check_artifact(
                f"{session_dir}/plays.json", "plays",
                "Ranked plays from latest run",
            ))
            evidence.append(_check_artifact(
                f"{session_dir}/risk_officer.json", "risk_officer",
                "Risk officer veto/approve decisions",
            ))
            evidence.append(_check_artifact(
                f"{session_dir}/strategies.json", "strategies",
                "Strategy routing and weights",
            ))

    elif intent == Intent.SHOW_CLOSEST_MISSES:
        if session_dir:
            evidence.append(_check_artifact(
                f"{session_dir}/drought.json", "drought",
                "Closest misses and blocker analysis",
            ))
            evidence.append(_check_artifact(
                f"{session_dir}/readiness.json", "readiness",
                "Universe readiness with gate outcomes",
            ))

    elif intent == Intent.WHAT_CHANGED:
        if session_dir:
            evidence.append(_check_artifact(
                f"{session_dir}/plays.json", "plays",
                "Current plays (to diff against previous)",
            ))
            evidence.append(_check_artifact(
                f"{session_dir}/system_state.json", "system_state",
                "Current system state",
            ))
        evidence.append(_check_artifact(
            "data/signal_log.jsonl", "signal_log",
            "Signal log for recent entries",
        ))

    elif intent == Intent.REGIME_STATUS:
        if session_dir:
            evidence.append(_check_artifact(
                f"{session_dir}/strategies.json", "strategies",
                "Strategy routing by regime",
            ))
            evidence.append(_check_artifact(
                f"{session_dir}/system_state.json", "system_state",
                "System state with regime tag",
            ))

    else:
        # UNKNOWN — include whatever we have
        if session_dir:
            evidence.append(_check_artifact(
                f"{session_dir}/system_state.json", "system_state",
                "General system state reference",
            ))

    return evidence
