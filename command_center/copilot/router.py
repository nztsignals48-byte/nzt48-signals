"""
command_center/copilot/router.py
=================================
Main entry point for the NZT-48 Operator Copilot.

Routes natural-language queries to the appropriate intent handler
and provides audit logging for all queries and suggested actions.

Usage:
    from command_center.copilot import CopilotRouter

    copilot = CopilotRouter()
    result = copilot.query("scan now")
    print(result["answer"])

SAFETY: This module is READ-ONLY. It CANNOT place orders. All suggested
actions are advisory only.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from command_center.copilot.intents import Intent, parse_intent
from command_center.copilot.throttling import ScanThrottle
from command_center.copilot.handlers import (
    handle_scan_now,
    handle_explain_signal,
    handle_why_not_ticker,
    handle_health_summary,
    handle_show_top_trades,
    handle_show_closest_misses,
    handle_what_changed,
    handle_regime_status,
    handle_unknown,
)

logger = logging.getLogger("nzt48.copilot.router")

# Project root for audit log placement
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"


# ---------------------------------------------------------------------------
# Audit Logger
# ---------------------------------------------------------------------------

class CopilotAuditLogger:
    """Thread-safe audit logger for copilot queries and actions.

    Writes to:
        data/copilot_queries.jsonl  — every query with intent, result summary
        data/copilot_actions.jsonl  — every suggested action

    Each line is a self-contained JSON object for easy streaming analysis.
    """

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self._data_dir = data_dir or _DATA_DIR
        self._lock = threading.Lock()
        self._queries_path = self._data_dir / "copilot_queries.jsonl"
        self._actions_path = self._data_dir / "copilot_actions.jsonl"

        # Ensure data directory exists
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("Cannot create data dir for audit log: %s", exc)

    def log_query(
        self,
        query: str,
        intent: Intent,
        lane: str,
        result: dict,
        duration_ms: int,
    ) -> None:
        """Append a query log entry to copilot_queries.jsonl.

        Args:
            query:       The raw user query.
            intent:      Classified Intent enum.
            lane:        Lane filter (CORE/OPPORTUNITY/INTEL/ALL).
            result:      Full handler result dict.
            duration_ms: Handler execution time in milliseconds.
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "query_id": str(uuid.uuid4())[:8].upper(),
            "query": query,
            "intent": intent.value,
            "lane": lane,
            "run_id": result.get("as_of", ""),
            "as_of": result.get("as_of", ""),
            "system_state": result.get("system_state", ""),
            "regime": result.get("regime", ""),
            "confidence": result.get("confidence", ""),
            "response_length": len(result.get("answer", "")),
            "actions_count": len(result.get("actions", [])),
            "warnings_count": len(result.get("warnings", [])),
            "duration_ms": duration_ms,
        }
        self._append_jsonl(self._queries_path, entry)

    def log_actions(
        self,
        actions: list[dict],
        intent: Intent,
        lane: str,
    ) -> None:
        """Append action log entries to copilot_actions.jsonl.

        Args:
            actions: List of suggested action dicts from the handler.
            intent:  The intent that produced these actions.
            lane:    Lane filter.
        """
        ts = datetime.now(timezone.utc).isoformat()
        batch_id = str(uuid.uuid4())[:8].upper()

        for i, action in enumerate(actions):
            entry = {
                "timestamp": ts,
                "batch_id": batch_id,
                "action_index": i,
                "intent": intent.value,
                "lane": lane,
                "action_type": action.get("type", "UNKNOWN"),
                "ticker": action.get("ticker", ""),
                "direction": action.get("direction", ""),
                "entry": action.get("entry", ""),
                "stop": action.get("stop", ""),
                "target": action.get("target", ""),
                "note": action.get("note", ""),
            }
            self._append_jsonl(self._actions_path, entry)

    def _append_jsonl(self, path: Path, entry: dict) -> None:
        """Thread-safe append of a JSON line to a file."""
        try:
            line = json.dumps(entry, default=str) + "\n"
            with self._lock:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line)
        except Exception as exc:
            logger.warning("Failed to write audit log to %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Copilot Router
# ---------------------------------------------------------------------------

class CopilotRouter:
    """Main entry point for the NZT-48 Operator Copilot.

    Routes natural-language queries through intent classification to
    deterministic handlers. Provides rate limiting for on-demand scans
    and audit logging for all interactions.

    This router is READ-ONLY and CANNOT place orders.

    Example:
        copilot = CopilotRouter()
        result = copilot.query("what can I trade right now?")
        print(result["answer"])
        # result["actions"] contains advisory suggestions only
    """

    def __init__(
        self,
        scan_cooldown_seconds: float = 60.0,
        data_dir: Optional[Path] = None,
    ) -> None:
        """Initialise the copilot router.

        Args:
            scan_cooldown_seconds: Minimum interval between on-demand scans.
            data_dir:              Override directory for audit log files.
        """
        self.throttle = ScanThrottle(cooldown_seconds=scan_cooldown_seconds)
        self.audit_logger = CopilotAuditLogger(data_dir=data_dir)

        # Handler dispatch map
        self._handler_map = {
            Intent.SCAN_NOW: handle_scan_now,
            Intent.EXPLAIN_SIGNAL: handle_explain_signal,
            Intent.WHY_NOT_TICKER: handle_why_not_ticker,
            Intent.HEALTH_SUMMARY: handle_health_summary,
            Intent.SHOW_TOP_TRADES: handle_show_top_trades,
            Intent.SHOW_CLOSEST_MISSES: handle_show_closest_misses,
            Intent.WHAT_CHANGED: handle_what_changed,
            Intent.REGIME_STATUS: handle_regime_status,
            Intent.UNKNOWN: handle_unknown,
        }

        logger.info(
            "CopilotRouter initialised (scan_cooldown=%ss, intents=%d)",
            scan_cooldown_seconds,
            len(self._handler_map),
        )

    def query(
        self,
        query: str,
        lane: str = "ALL",
        max_results: int = 10,
    ) -> dict:
        """Route a natural-language query to the appropriate handler.

        Args:
            query:       Free-text query from the operator.
            lane:        Lane filter: "ALL", "CORE", "OPPORTUNITY", "INTEL".
            max_results: Maximum number of plays/misses to return.

        Returns:
            Handler result dict with keys: answer, actions, evidence,
            warnings, as_of, system_state, regime, confidence.
        """
        start = time.monotonic()

        # Parse intent
        intent, params = parse_intent(query)
        params["lane"] = lane
        params["max_results"] = max_results
        params["throttle"] = self.throttle

        logger.info(
            "Copilot query: intent=%s, ticker=%s, lane=%s, query='%s'",
            intent.value,
            params.get("ticker"),
            lane,
            query[:80],
        )

        # Dispatch to handler
        handler = self._handler_map.get(intent, handle_unknown)
        try:
            result = handler(params)
        except Exception as exc:
            logger.exception("Handler %s failed: %s", intent.value, exc)
            result = {
                "answer": f"Internal error in handler '{intent.value}': {exc}",
                "actions": [],
                "evidence": [],
                "warnings": [f"Handler exception: {exc}"],
                "as_of": datetime.now(timezone.utc).isoformat(),
                "system_state": "DEGRADED",
                "regime": "UNKNOWN",
                "confidence": "C",
            }

        # Compute duration
        duration_ms = int((time.monotonic() - start) * 1000)

        # Audit log (fire-and-forget, never fail the query)
        try:
            self.audit_logger.log_query(query, intent, lane, result, duration_ms)
            if result.get("actions"):
                self.audit_logger.log_actions(result["actions"], intent, lane)
        except Exception as exc:
            logger.warning("Audit logging failed: %s", exc)

        # Attach metadata
        result["_meta"] = {
            "intent": intent.value,
            "query": query,
            "lane": lane,
            "duration_ms": duration_ms,
            "handler": handler.__name__,
        }

        return result

    @property
    def available_intents(self) -> list[str]:
        """List of available intent names for help / discovery."""
        return [intent.value for intent in self._handler_map.keys()]

    def reset_throttle(self) -> None:
        """Reset the scan throttle. Useful for testing or admin override."""
        self.throttle.reset()
        logger.info("Scan throttle reset by operator")
