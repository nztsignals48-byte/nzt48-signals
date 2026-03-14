"""
signal_engine/state_machine.py
================================
Signal lifecycle state machine.

States:
    CANDIDATE   — ticker scored, all gates passed (or relaxed), not yet emitted
    QUALIFIED   — passed final portfolio-level checks; ready to emit
    SIGNAL      — emitted to Signal Tape + stored in DB
    ORDER_INTENT — (future) forwarded to execution layer
    EXPIRED     — stale (exceeded session window or stop already hit)
    INVALIDATED — post-emit data health failure or corporate action

Transitions:
    CANDIDATE   → QUALIFIED   : portfolio risk check passes
    CANDIDATE   → INVALIDATED : data health re-check fails after scoring
    QUALIFIED   → SIGNAL      : emitted to tape
    SIGNAL      → ORDER_INTENT: (optional) broker routing
    SIGNAL      → EXPIRED     : session closes without fill
    SIGNAL      → INVALIDATED : vendor disagreement > tolerance
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from signal_engine.scoring import PlayScore


class SignalState(str, Enum):
    CANDIDATE    = "CANDIDATE"
    QUALIFIED    = "QUALIFIED"
    SIGNAL       = "SIGNAL"
    ORDER_INTENT = "ORDER_INTENT"
    EXPIRED      = "EXPIRED"
    INVALIDATED  = "INVALIDATED"


@dataclass
class SignalRecord:
    """Full lifecycle record for one signal."""
    id:            str         = field(default_factory=lambda: str(uuid.uuid4())[:8].upper())
    ticker:        str         = ""
    direction:     str         = "LONG"
    state:         SignalState = SignalState.CANDIDATE
    play_score:    Optional[PlayScore] = None
    mode:          str         = "STRICT"       # STRICT | FALLBACK_STEP1..4
    session:       str         = ""             # PRE_LSE | PRE_NYSE | EOD
    created_at:    datetime    = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at:    datetime    = field(default_factory=lambda: datetime.now(timezone.utc))
    emitted_at:    Optional[datetime] = None
    expired_at:    Optional[datetime] = None
    state_history: list[tuple[str, str]] = field(default_factory=list)  # [(state, reason)]

    # Trade levels (mirrored from PlayScore for quick access)
    entry:   float = 0.0
    stop:    float = 0.0
    target1: float = 0.0
    target2: float = 0.0
    rr:      float = 0.0
    stars:   int   = 1
    label:   str   = ""

    def transition(self, new_state: SignalState, reason: str = "") -> None:
        old = self.state.value
        self.state = new_state
        self.updated_at = datetime.now(timezone.utc)
        self.state_history.append((old, new_state.value, reason))

        if new_state == SignalState.SIGNAL:
            self.emitted_at = self.updated_at
        elif new_state == SignalState.EXPIRED:
            self.expired_at = self.updated_at

    @property
    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.created_at).total_seconds()

    def to_tape_line(self) -> str:
        ts  = self.emitted_at or self.created_at
        ps  = self.play_score
        score_str = f"{ps.composite:.0f}/100" if ps else "N/A"
        return (
            f"[{ts.strftime('%H:%M:%S')}] {self.id} | {self.ticker} {self.direction} | "
            f"{self.stars_str} {score_str} | {self.label} | "
            f"entry={self.entry:.2f} stop={self.stop:.2f} T1={self.target1:.2f} "
            f"R:R={self.rr:.1f} | {self.state.value}"
        )

    @property
    def stars_str(self) -> str:
        n = self.stars
        return "★" * n + "☆" * (5 - n)

    @classmethod
    def from_play_score(cls, ps: PlayScore, session: str) -> "SignalRecord":
        rec = cls(
            ticker=ps.ticker,
            direction=ps.direction,
            state=SignalState.CANDIDATE,
            play_score=ps,
            mode=ps.label.split(" ")[0] if ps.label else "STRICT",
            session=session,
            entry=ps.entry,
            stop=ps.stop,
            target1=ps.target1,
            target2=ps.target2,
            rr=ps.rr_ratio,
            stars=ps.stars,
            label=ps.label,
        )
        return rec


class SignalTape:
    """In-memory ordered tape of emitted signals, with diff support."""

    def __init__(self, max_records: int = 500) -> None:
        self._records:   list[SignalRecord] = []
        self._by_id:     dict[str, SignalRecord] = {}
        self._max        = max_records
        self._prev_ids:  set[str] = set()

    def emit(self, record: SignalRecord) -> None:
        record.transition(SignalState.SIGNAL, reason="emitted to tape")
        self._records.append(record)
        self._by_id[record.id] = record
        # Trim oldest
        if len(self._records) > self._max:
            old = self._records.pop(0)
            self._by_id.pop(old.id, None)

    def get_recent(self, n: int = 20) -> list[SignalRecord]:
        return list(reversed(self._records[-n:]))

    def diff_since_last_tick(self) -> list[SignalRecord]:
        """Return records emitted since the last call to this method."""
        current_ids = {r.id for r in self._records}
        new_ids     = current_ids - self._prev_ids
        self._prev_ids = current_ids
        return [r for r in self._records if r.id in new_ids]

    def expire_stale(self, max_age_seconds: float = 3600) -> int:
        """Mark old SIGNAL records as EXPIRED. Returns count expired."""
        count = 0
        for r in self._records:
            if r.state == SignalState.SIGNAL and r.age_seconds > max_age_seconds:
                r.transition(SignalState.EXPIRED, reason="session window closed")
                count += 1
        return count

    def to_lines(self, n: int = 30) -> list[str]:
        return [r.to_tape_line() for r in self.get_recent(n)]
