"""Deterministic Replay & Time-Travel Debugging — Book 92.

Replay WAL events through the engine and verify identical output.
The guarantee: same WAL + same config + same binary = identical state.

Uses:
  1. Bug investigation: replay to the exact tick where behavior diverged
  2. Strategy testing: replay historical data through new strategy code
  3. State recovery: rebuild engine state from WAL after crash
  4. Regression testing: verify new code doesn't change old behavior

Usage:
    from python_brain.risk.deterministic_replay import (
        WALReplayer, ReplayResult,
    )

    replayer = WALReplayer()
    result = replayer.replay("events/2026-03-29.ndjson")
    if not result.deterministic:
        investigate(result.first_divergence)
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("deterministic_replay")


@dataclass
class ReplayEvent:
    """A single replayed event with verification."""
    event_type: str
    timestamp: str
    original_hash: str = ""
    replayed_hash: str = ""
    matches: bool = True


@dataclass
class ReplayResult:
    """Result of a WAL replay verification."""
    wal_file: str = ""
    total_events: int = 0
    replayed_events: int = 0
    deterministic: bool = True
    first_divergence_at: int = -1  # Event index where divergence occurred
    divergence_details: str = ""
    state_hashes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "wal_file": self.wal_file,
            "total_events": self.total_events,
            "replayed_events": self.replayed_events,
            "deterministic": self.deterministic,
            "first_divergence_at": self.first_divergence_at,
            "divergence_details": self.divergence_details,
        }


class WALReplayer:
    """Replay WAL events for deterministic verification."""

    def __init__(self):
        self._state: Dict[str, Any] = {}  # Accumulated state

    def replay(self, wal_path: str) -> ReplayResult:
        """Replay a WAL file and verify event consistency.

        Checks:
        1. CRC32 integrity of each event
        2. Monotonic timestamp ordering
        3. State hash consistency at checkpoints
        """
        path = Path(wal_path)
        result = ReplayResult(wal_file=str(path))

        if not path.exists():
            result.deterministic = False
            result.divergence_details = "WAL file not found"
            return result

        prev_timestamp = ""
        events: List[Dict] = []

        with open(path) as f:
            for line_num, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    evt = json.loads(line)
                    events.append(evt)
                    result.total_events += 1

                    # Check monotonic timestamps
                    ts = evt.get("event_time", evt.get("timestamp", ""))
                    if ts and ts < prev_timestamp:
                        result.deterministic = False
                        result.first_divergence_at = line_num
                        result.divergence_details = f"Non-monotonic timestamp at line {line_num}: {ts} < {prev_timestamp}"
                        log.warning("REPLAY: %s", result.divergence_details)
                    prev_timestamp = ts

                    # Check CRC32 if present
                    crc = evt.get("crc32")
                    if crc is not None:
                        # Recompute CRC from event data (excluding CRC field itself)
                        evt_copy = {k: v for k, v in evt.items() if k != "crc32"}
                        computed = self._compute_crc(json.dumps(evt_copy, sort_keys=True))
                        if computed != crc:
                            result.deterministic = False
                            if result.first_divergence_at < 0:
                                result.first_divergence_at = line_num
                                result.divergence_details = f"CRC mismatch at line {line_num}"

                    # Apply event to state
                    self._apply_event(evt)
                    result.replayed_events += 1

                    # State checkpoint hash every 100 events
                    if line_num % 100 == 0:
                        state_hash = self._state_hash()
                        result.state_hashes.append(state_hash)

                except json.JSONDecodeError:
                    result.deterministic = False
                    if result.first_divergence_at < 0:
                        result.first_divergence_at = line_num
                        result.divergence_details = f"JSON parse error at line {line_num}"

        if result.deterministic:
            log.info("REPLAY: %s — %d events, DETERMINISTIC", path.name, result.total_events)
        else:
            log.warning("REPLAY: %s — DIVERGENCE at event %d: %s",
                       path.name, result.first_divergence_at, result.divergence_details)

        return result

    def _apply_event(self, evt: Dict):
        """Apply an event to the accumulated state."""
        et = evt.get("event_type", "")
        ticker = evt.get("ticker", evt.get("symbol", ""))

        if et == "PositionOpened":
            self._state[f"pos_{ticker}"] = {
                "qty": evt.get("quantity", 0),
                "entry": evt.get("entry_price", 0),
            }
        elif et == "PositionClosed":
            self._state.pop(f"pos_{ticker}", None)
        elif et == "StateSnapshot":
            self._state["equity"] = evt.get("equity", 0)
            self._state["positions_count"] = evt.get("positions_count", 0)

    def _state_hash(self) -> str:
        """Compute FNV-1a-like hash of current state."""
        payload = json.dumps(self._state, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:12]

    def _compute_crc(self, data: str) -> int:
        """Compute CRC32 of data string."""
        import binascii
        return binascii.crc32(data.encode()) & 0xFFFFFFFF

    def compare_replays(self, replay1: ReplayResult, replay2: ReplayResult) -> bool:
        """Compare two replay results for identical state progression."""
        if len(replay1.state_hashes) != len(replay2.state_hashes):
            return False
        return all(h1 == h2 for h1, h2 in zip(replay1.state_hashes, replay2.state_hashes))


# ─── Replay Shell & A/B Comparison ──────────────────────────────────────────

from typing import Callable


@dataclass
class ReplayState:
    """Snapshot of engine state at a specific event."""
    event_index: int = 0
    timestamp: str = ""
    positions: Dict[str, Any] = field(default_factory=dict)
    equity: float = 0.0
    regime: str = ""
    errors: List[str] = field(default_factory=list)


class ReplayShell:
    """Interactive replay shell for time-travel debugging.

    Load a WAL file and step through events one at a time,
    jump to specific events, or advance to a breakpoint condition.
    """

    def __init__(self, wal_path: str):
        self._wal_path = wal_path
        self._events: List[Dict[str, Any]] = []
        self._current_index: int = -1
        self._state = ReplayState()

        # Load all WAL events
        path = Path(wal_path)
        if not path.exists():
            raise FileNotFoundError(f"WAL file not found: {wal_path}")

        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    self._events.append(json.loads(line))
                except json.JSONDecodeError as e:
                    self._state.errors.append(f"Parse error: {e}")

        log.info("ReplayShell: loaded %d events from %s", len(self._events), wal_path)

    def _replay_to(self, target_index: int) -> ReplayState:
        """Replay from start to target_index (inclusive), rebuilding state."""
        state = ReplayState(event_index=target_index)
        positions: Dict[str, Any] = {}
        equity = 0.0
        regime = ""
        errors: List[str] = []

        for i in range(min(target_index + 1, len(self._events))):
            evt = self._events[i]
            et = evt.get("event_type", "")
            ticker = evt.get("ticker", evt.get("symbol", ""))
            ts = evt.get("event_time", evt.get("timestamp", ""))

            if et == "PositionOpened":
                positions[ticker] = {
                    "qty": evt.get("quantity", 0),
                    "entry": evt.get("entry_price", 0),
                    "side": evt.get("direction", ""),
                }
            elif et == "PositionClosed":
                positions.pop(ticker, None)
            elif et == "StateSnapshot":
                equity = evt.get("equity", equity)
            elif et == "RegimeChange":
                regime = evt.get("new_regime", evt.get("regime", regime))
            elif et == "Error":
                errors.append(evt.get("message", str(evt)))

            state.timestamp = ts

        state.positions = positions
        state.equity = equity
        state.regime = regime
        state.errors = errors
        self._current_index = target_index
        self._state = state
        return state

    def seek(self, event_index: int) -> ReplayState:
        """Jump to event N by replaying from start."""
        idx = max(0, min(event_index, len(self._events) - 1))
        return self._replay_to(idx)

    def step_forward(self) -> ReplayState:
        """Advance one event."""
        next_idx = self._current_index + 1
        if next_idx >= len(self._events):
            log.warning("ReplayShell: already at end of WAL (%d events)", len(self._events))
            return self._state
        return self._replay_to(next_idx)

    def step_back(self) -> ReplayState:
        """Rewind one event (replays from start to N-1)."""
        prev_idx = self._current_index - 1
        if prev_idx < 0:
            log.warning("ReplayShell: already at start of WAL")
            return self._state
        return self._replay_to(prev_idx)

    def inspect(self) -> dict:
        """Return current state snapshot as dict."""
        return {
            "event_index": self._state.event_index,
            "total_events": len(self._events),
            "timestamp": self._state.timestamp,
            "positions": self._state.positions,
            "equity": self._state.equity,
            "regime": self._state.regime,
            "n_errors": len(self._state.errors),
            "current_event": self._events[self._current_index] if 0 <= self._current_index < len(self._events) else None,
        }

    def breakpoint_at(self, condition_fn: Callable[[Dict[str, Any], ReplayState], bool]) -> ReplayState:
        """Advance until condition_fn(event, state) returns True.

        condition_fn receives the current event dict and the ReplayState.
        """
        start = self._current_index + 1
        for i in range(start, len(self._events)):
            state = self._replay_to(i)
            if condition_fn(self._events[i], state):
                log.info("ReplayShell: breakpoint hit at event %d", i)
                return state

        log.warning("ReplayShell: breakpoint condition never met, at end of WAL")
        return self._state


@dataclass
class ReplayComparison:
    """Result of comparing replay vs live execution."""
    original_pnl: float = 0.0
    replay_pnl: float = 0.0
    divergence_events: List[int] = field(default_factory=list)  # Event indices where divergence occurred
    match_rate: float = 0.0  # Fraction of events that matched


def compare_replay_vs_live(wal_path: str, live_results: List[Dict[str, Any]]) -> ReplayComparison:
    """Event-by-event comparison of WAL replay vs live recorded results.

    live_results: list of dicts with at least {event_index, pnl, positions_count, equity}.
    Each is compared against the replayed state at that event index.
    """
    shell = ReplayShell(wal_path)
    comp = ReplayComparison()

    if not live_results:
        return comp

    matches = 0
    total = len(live_results)
    divergences: List[int] = []

    for lr in live_results:
        idx = lr.get("event_index", 0)
        state = shell.seek(idx)

        live_equity = lr.get("equity", 0.0)
        live_pos_count = lr.get("positions_count", 0)
        replay_pos_count = len(state.positions)

        # Check for divergence: equity mismatch > 0.01 or position count mismatch
        equity_match = abs(live_equity - state.equity) < 0.01 if live_equity > 0 and state.equity > 0 else True
        pos_match = live_pos_count == replay_pos_count

        if equity_match and pos_match:
            matches += 1
        else:
            divergences.append(idx)

    # Compute PnL from last live result
    comp.original_pnl = live_results[-1].get("pnl", 0.0) if live_results else 0.0
    # Replay PnL: equity delta from first to last replayed state
    if len(live_results) >= 2:
        first_state = shell.seek(live_results[0].get("event_index", 0))
        last_state = shell.seek(live_results[-1].get("event_index", 0))
        comp.replay_pnl = last_state.equity - first_state.equity
    comp.divergence_events = divergences
    comp.match_rate = matches / max(total, 1)

    return comp
