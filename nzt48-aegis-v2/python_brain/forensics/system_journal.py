"""System Journal & Institutional Memory — Book 13.

Compresses institutional knowledge into a persistent, queryable store.
Every Claude session, every operator decision, every lesson learned
is recorded so future sessions start with full context.

Memory types:
  LESSON:    Something learned from trading (e.g., "TypeF fails on FOMC days")
  DECISION:  An operator or Claude decision with rationale
  INCIDENT:  Something that went wrong and how it was resolved
  PATTERN:   A recurring pattern observed in data
  PARAMETER: Why a parameter was set to its current value

Usage:
    from python_brain.forensics.system_journal import SystemJournal

    journal = SystemJournal()
    journal.add_lesson("TypeF underperforms during high-VIX regimes", source="nightly_review")
    relevant = journal.query("TypeF VIX")
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("system_journal")

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))


@dataclass
class JournalEntry:
    """A single knowledge entry in the system journal."""
    entry_type: str  # LESSON, DECISION, INCIDENT, PATTERN, PARAMETER
    content: str
    source: str = ""  # Who/what created this entry
    tags: List[str] = field(default_factory=list)
    timestamp: str = ""
    confidence: float = 0.5  # How confident we are this knowledge is still valid
    expiry_days: int = 0     # 0 = never expires


class SystemJournal:
    """Persistent institutional memory for AEGIS V2."""

    def __init__(self, journal_path: Optional[Path] = None):
        self._path = journal_path or (DATA_DIR / "system_journal.json")
        self._entries: List[JournalEntry] = []
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                with open(self._path) as f:
                    data = json.load(f)
                self._entries = [JournalEntry(**e) for e in data.get("entries", [])]
            except (json.JSONDecodeError, TypeError):
                self._entries = []

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {"entries": [asdict(e) for e in self._entries], "count": len(self._entries)}
        with open(self._path, "w") as f:
            json.dump(data, f, indent=2)

    def add_lesson(self, content: str, source: str = "", tags: Optional[List[str]] = None):
        self._add("LESSON", content, source, tags)

    def add_decision(self, content: str, source: str = "", tags: Optional[List[str]] = None):
        self._add("DECISION", content, source, tags)

    def add_incident(self, content: str, source: str = "", tags: Optional[List[str]] = None):
        self._add("INCIDENT", content, source, tags)

    def add_pattern(self, content: str, source: str = "", tags: Optional[List[str]] = None):
        self._add("PATTERN", content, source, tags)

    def _add(self, entry_type: str, content: str, source: str, tags: Optional[List[str]]):
        entry = JournalEntry(
            entry_type=entry_type,
            content=content,
            source=source,
            tags=tags or [],
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._entries.append(entry)
        self._save()
        log.info("JOURNAL: [%s] %s (source=%s)", entry_type, content[:80], source)

    def query(self, keyword: str, entry_type: Optional[str] = None) -> List[JournalEntry]:
        """Search journal entries by keyword and optional type."""
        results = []
        kw_lower = keyword.lower()
        for e in self._entries:
            if entry_type and e.entry_type != entry_type:
                continue
            if kw_lower in e.content.lower() or any(kw_lower in t.lower() for t in e.tags):
                results.append(e)
        return results

    def recent(self, n: int = 10) -> List[JournalEntry]:
        return self._entries[-n:]

    @property
    def count(self) -> int:
        return len(self._entries)

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for e in self._entries:
            counts[e.entry_type] = counts.get(e.entry_type, 0) + 1
        return counts
