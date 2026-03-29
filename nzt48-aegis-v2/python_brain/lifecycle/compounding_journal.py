"""Compounding Journal & Milestone Tracker — Book 218.

Track the compounding journey from £10K to targets.
Weekly metrics, monthly milestones, phase gates.

Milestones:
  Phase 1: £10K → £15K (paper validated, 1 strategy live)
  Phase 2: £15K → £25K (3 strategies live, Sharpe > 0.5)
  Phase 3: £25K → £50K (5 strategies live, add US equities)
  Phase 4: £50K → £100K (7 strategies, full ensemble)
  Phase 5: £100K+ (institutional hardening, acquisition-ready)

Usage:
    from python_brain.lifecycle.compounding_journal import (
        CompoundingJournal, JournalEntry,
    )

    journal = CompoundingJournal(initial_equity=10000)
    journal.record_week(equity=10150, trades=12, net_pnl=150)
    milestone = journal.check_milestone()
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("compounding_journal")


@dataclass
class JournalEntry:
    """Weekly journal entry."""
    week_number: int = 0
    date: str = ""
    equity: float = 0.0
    net_pnl_week: float = 0.0
    cumulative_pnl: float = 0.0
    trades: int = 0
    win_rate: float = 0.0
    sharpe: float = 0.0
    max_drawdown_pct: float = 0.0
    cost_drag_pct: float = 0.0
    strategies_active: int = 0
    regime: str = ""
    notes: str = ""


@dataclass
class Milestone:
    """A capital phase milestone."""
    phase: int
    target_equity: float
    requirements: Dict[str, Any]
    achieved: bool = False
    achieved_date: str = ""


MILESTONES = [
    Milestone(1, 15000, {"min_strategies": 1, "min_sharpe": 0.0}),
    Milestone(2, 25000, {"min_strategies": 3, "min_sharpe": 0.5}),
    Milestone(3, 50000, {"min_strategies": 5, "min_sharpe": 0.8}),
    Milestone(4, 100000, {"min_strategies": 7, "min_sharpe": 1.0}),
    Milestone(5, 500000, {"min_strategies": 7, "min_sharpe": 1.5}),
]


class CompoundingJournal:
    """Track compounding progress and phase transitions."""

    def __init__(self, initial_equity: float = 10000.0):
        self.initial_equity = initial_equity
        self._entries: List[JournalEntry] = []
        self._milestones = [Milestone(m.phase, m.target_equity, m.requirements) for m in MILESTONES]
        self._current_phase = 0

    @property
    def current_equity(self) -> float:
        if self._entries:
            return self._entries[-1].equity
        return self.initial_equity

    @property
    def total_return_pct(self) -> float:
        return (self.current_equity - self.initial_equity) / self.initial_equity * 100

    @property
    def weeks_running(self) -> int:
        return len(self._entries)

    def record_week(
        self,
        equity: float,
        trades: int,
        net_pnl: float,
        win_rate: float = 0.0,
        sharpe: float = 0.0,
        max_drawdown_pct: float = 0.0,
        cost_drag_pct: float = 0.0,
        strategies_active: int = 0,
        regime: str = "",
        notes: str = "",
    ):
        """Record a weekly journal entry."""
        entry = JournalEntry(
            week_number=len(self._entries) + 1,
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            equity=equity,
            net_pnl_week=net_pnl,
            cumulative_pnl=equity - self.initial_equity,
            trades=trades,
            win_rate=win_rate,
            sharpe=sharpe,
            max_drawdown_pct=max_drawdown_pct,
            cost_drag_pct=cost_drag_pct,
            strategies_active=strategies_active,
            regime=regime,
            notes=notes,
        )
        self._entries.append(entry)
        log.info(
            "JOURNAL: Week %d — equity=%.0f, PnL=%+.0f, WR=%.0f%%, Sharpe=%.2f, strats=%d",
            entry.week_number, equity, net_pnl, win_rate * 100, sharpe, strategies_active,
        )

    def check_milestone(self) -> Optional[Milestone]:
        """Check if current equity has reached the next milestone."""
        for m in self._milestones:
            if not m.achieved and self.current_equity >= m.target_equity:
                m.achieved = True
                m.achieved_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                self._current_phase = m.phase
                log.info(
                    "MILESTONE: Phase %d achieved! Equity=%.0f >= %.0f target",
                    m.phase, self.current_equity, m.target_equity,
                )
                return m
        return None

    def projected_time_to_next(self, weekly_return_pct: float = 0.5) -> Optional[int]:
        """Estimate weeks to next milestone at current growth rate."""
        for m in self._milestones:
            if not m.achieved:
                if weekly_return_pct <= 0:
                    return None
                gap = m.target_equity - self.current_equity
                if gap <= 0:
                    return 0
                weeks = gap / (self.current_equity * weekly_return_pct / 100)
                return max(1, int(weeks))
        return None  # All milestones achieved

    def save(self, output_path: Path):
        """Save journal to JSON."""
        data = {
            "initial_equity": self.initial_equity,
            "current_equity": self.current_equity,
            "total_return_pct": round(self.total_return_pct, 2),
            "weeks_running": self.weeks_running,
            "current_phase": self._current_phase,
            "milestones": [
                {"phase": m.phase, "target": m.target_equity,
                 "achieved": m.achieved, "date": m.achieved_date}
                for m in self._milestones
            ],
            "entries": [asdict(e) for e in self._entries[-52:]],  # Last year
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)

    def to_dict(self) -> dict:
        return {
            "equity": self.current_equity,
            "return_pct": round(self.total_return_pct, 2),
            "weeks": self.weeks_running,
            "phase": self._current_phase,
            "next_milestone": next(
                (m.target_equity for m in self._milestones if not m.achieved), None
            ),
        }
