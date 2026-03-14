"""
learning/signal_logger.py
==========================
Logs every signal emitted by the engine to data/signal_log.jsonl.
Called from signal_engine after EngineResult is assembled.
Fast, fire-and-forget (pure IO, no blocking calls).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from learning.schemas import SignalLogRecord, make_signal_id

logger = logging.getLogger("nzt48.learning.signal_logger")

_DATA       = Path(__file__).parent.parent / "data"
_SIGNAL_LOG = _DATA / "signal_log.jsonl"


class SignalLogger:
    """Appends SignalLogRecords to signal_log.jsonl."""

    def __init__(self):
        _DATA.mkdir(parents=True, exist_ok=True)
        self._seen_ids: set[str] = set()
        self._load_seen_ids()

    def _load_seen_ids(self) -> None:
        """Load all signal IDs from existing log into memory for O(1) dedup."""
        if not _SIGNAL_LOG.exists():
            return
        try:
            with open(_SIGNAL_LOG) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        sid = d.get("signal_id", "")
                        if sid:
                            self._seen_ids.add(sid)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.warning("Failed to load seen signal IDs: %s", e)

    def log_signal(self, play: Any, session: str, regime_tag: str,
                   regime_confidence: float = 0.0) -> str:
        """
        Log one signal. play is a PlayScore/dict with attributes:
        ticker, direction, strategy_tag, track, composite,
        entry, stop, target1, target2, net_rr (or rr_ratio),
        rvol, atr_pct, setup_type, sizing_hint, factor_group
        Returns signal_id.
        """
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()
        date_str = now.strftime("%Y-%m-%d")

        def g(attr, default=None):
            if isinstance(play, dict):
                return play.get(attr, default)
            return getattr(play, attr, default)

        ticker       = g("ticker", "UNKNOWN")
        direction    = g("direction", "LONG")
        strategy_tag = g("strategy_tag", "")
        track        = g("track", "INTRADAY_SWING")
        entry        = float(g("entry", 0.0) or 0.0)
        target1      = float(g("target1", 0.0) or 0.0)

        signal_id = make_signal_id(date_str, now_str, ticker, strategy_tag, track, entry)

        # Check for dup
        if self._already_logged(signal_id):
            return signal_id

        net_rr = g("net_rr", None) or g("rr_ratio", None) or g("rr", None) or 0.0

        # Detect liquidity bucket from rvol
        # Fix: treat None/0 as UNKNOWN with penalty, not NORMAL
        raw_rvol = g("rvol", None)
        if raw_rvol is None or raw_rvol == 0:
            rvol = 0.0
            liq = "UNKNOWN"
        else:
            rvol = float(raw_rvol)
            if rvol >= 2.0:
                liq = "HIGH"
            elif rvol >= 1.2:
                liq = "NORMAL"
            elif rvol >= 0.7:
                liq = "LOW"
            else:
                liq = "THIN"

        # AEGIS 0-06: Extract per-indicator scores from metadata
        metadata = g("metadata", None)
        indicator_scores = {}
        if isinstance(metadata, dict):
            indicator_scores = metadata.get("indicator_scores", {})
        # Fallback: check if indicator_scores is a direct attribute (e.g. from a dict play)
        if not indicator_scores:
            raw_scores = g("indicator_scores", None)
            if isinstance(raw_scores, dict):
                indicator_scores = raw_scores

        rec = SignalLogRecord(
            signal_id     = signal_id,
            ticker        = ticker,
            direction     = direction,
            strategy_tag  = strategy_tag,
            regime_tag    = regime_tag,
            regime_confidence = float(regime_confidence or 0.0),
            time_window   = g("time_window", "UNKNOWN") or "UNKNOWN",
            track         = track,
            session       = session,
            composite     = float(g("composite", 0.0) or 0.0),
            entry         = entry,
            stop          = float(g("stop", 0.0) or 0.0),
            target1       = target1,
            target2       = float(g("target2", target1) or target1),
            net_rr        = float(net_rr or 0.0),
            generated_at  = now_str,
            date_str      = date_str,
            rvol          = rvol,
            atr_pct       = float(g("atr_pct", 0.0) or 0.0),
            bb_width      = float(g("bb_width", 0.0) or 0.0),
            rsi           = float(g("rsi", 0.0) or 0.0),
            adx           = float(g("adx", 0.0) or 0.0),
            spread_bps    = float(g("spread_bps", 0.0) or 0.0),
            liquidity_bucket = liq,
            risk_officer_decision = g("risk_officer_decision", "APPROVE") or "APPROVE",
            sizing_hint   = g("sizing_hint", "M") or "M",
            indicator_scores = indicator_scores,
            outcome       = "PENDING",
        )

        try:
            with open(_SIGNAL_LOG, "a") as f:
                f.write(json.dumps(rec.to_dict()) + "\n")
                self._seen_ids.add(signal_id)
        except Exception as e:
            logger.error(f"Failed to log signal {signal_id}: {e}")

        return signal_id

    def log_plays(self, plays: list, session: str, regime_tag: str,
                  regime_confidence: float = 0.0) -> list[str]:
        """Log a list of plays. Returns list of signal_ids."""
        ids = []
        for play in plays:
            try:
                sid = self.log_signal(play, session, regime_tag, regime_confidence)
                ids.append(sid)
            except Exception as e:
                logger.error(f"Failed to log play: {e}")
        return ids

    def _already_logged(self, signal_id: str) -> bool:
        """O(1) check using in-memory set."""
        return signal_id in self._seen_ids

    def get_stats(self) -> dict:
        """Quick stats on the signal log."""
        total = pending = resolved = 0
        if not _SIGNAL_LOG.exists():
            return {"total": 0, "pending": 0, "resolved": 0}
        try:
            with open(_SIGNAL_LOG) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    total += 1
                    d = json.loads(line)
                    outcome = d.get("outcome", "PENDING")
                    if outcome == "PENDING":
                        pending += 1
                    elif outcome == "RESOLVED":
                        resolved += 1
        except Exception:
            pass
        return {"total": total, "pending": pending, "resolved": resolved}


# Module singleton
_logger_instance: SignalLogger | None = None

def get_signal_logger() -> SignalLogger:
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = SignalLogger()
    return _logger_instance
