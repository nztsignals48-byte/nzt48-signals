"""
data_hub/normalization/corporate_actions.py
=============================================
Corporate action adjustments for historical bars.
"""
from __future__ import annotations
import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nzt48.data_hub.corp_actions")

_CACHE_PATH = Path(__file__).parent.parent.parent / "data" / "corporate_actions.json"


def load_corporate_actions() -> dict:
    """Load cached corporate actions. Returns {} if file missing."""
    try:
        if _CACHE_PATH.exists():
            return json.loads(_CACHE_PATH.read_text())
    except Exception as exc:
        logger.debug("[CORP_ACTIONS] load failed: %s", exc)
    return {}


def get_actions_for_ticker(ticker: str) -> list[dict]:
    """Return list of corporate action dicts for a ticker."""
    return load_corporate_actions().get(ticker, [])


def adjust_close_for_splits(
    close: float,
    ticker: str,
    bar_date: date,
) -> float:
    """
    Adjust a price for any splits occurring after bar_date.
    Very conservative: only applies if action is in local cache.
    """
    actions = get_actions_for_ticker(ticker)
    for action in actions:
        if action.get("action_type") != "SPLIT":
            continue
        try:
            act_date = date.fromisoformat(action["action_date"])
            if act_date > bar_date:
                ratio = float(action.get("ratio", 1.0))
                if ratio > 0:
                    close = close / ratio
        except Exception:
            pass
    return close


def save_corporate_actions(data: dict) -> None:
    """Save corporate actions cache."""
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        import tempfile, os
        fd, tmp = tempfile.mkstemp(dir=_CACHE_PATH.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2, default=str))
        Path(tmp).replace(_CACHE_PATH)
    except Exception as exc:
        logger.warning("[CORP_ACTIONS] save failed: %s", exc)
