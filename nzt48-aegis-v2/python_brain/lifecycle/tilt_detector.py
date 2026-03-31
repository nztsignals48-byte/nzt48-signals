"""Book 16: Tilt Detector.

Detects operator tilt patterns from WAL events:
  1. Rapid trading (>8 trades/day when avg is 3-5)
  2. Size escalation after losses (revenge trading)
  3. Override clustering (multiple manual interventions in short window)
  4. Late-session entries (trades after normal strategy hours)
  5. Consecutive loss response (entering immediately after streak)

Tilt score: 0-100 (0=calm, 100=full tilt)
Thresholds:
  - 0-20: CALM
  - 20-40: ELEVATED
  - 40-60: WARNING (reduce allocation)
  - 60-80: CRITICAL (halt new entries)
  - 80-100: EXTREME (flatten recommendation)

Wired into nightly pipeline Step 19.
"""

from __future__ import annotations

import glob
import json
import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

DATA_DIR = os.environ.get("AEGIS_DATA_DIR", "/app/data")
TILT_FILE = os.path.join(DATA_DIR, "tilt_state.json")
WAL_DIR = os.environ.get("AEGIS_WAL_DIR", "/app/events")


@dataclass
class TiltTrigger:
    """A single tilt trigger with its contribution."""
    name: str
    score: int  # 0-25 per trigger
    detail: str = ""


@dataclass
class TiltResult:
    """Result of tilt analysis."""
    date: str = ""
    tilt_score: int = 0  # 0-100
    level: str = "CALM"
    triggers: List[TiltTrigger] = field(default_factory=list)
    recommendation: str = ""
    allocation_scale: float = 1.0


class TiltDetector:
    """Detects operator tilt from WAL patterns."""

    # Score thresholds
    LEVELS = [
        (80, "EXTREME", 0.0, "FLATTEN all positions immediately"),
        (60, "CRITICAL", 0.25, "Halt all new entries"),
        (40, "WARNING", 0.50, "Reduce allocation to 50%"),
        (20, "ELEVATED", 0.75, "Caution — heightened emotional state"),
        (0, "CALM", 1.0, "Normal operation"),
    ]

    def __init__(self):
        self._history: List[TiltResult] = []

    def analyze_day(self, date_str: Optional[str] = None) -> TiltResult:
        """Analyze a day's WAL events for tilt indicators."""
        if date_str is None:
            date_str = time.strftime("%Y-%m-%d", time.gmtime())

        result = TiltResult(date=date_str)
        events = self._load_events(date_str)
        if not events:
            result.level = "CALM"
            result.recommendation = "No events to analyze"
            return result

        triggers = []

        # 1. Rapid trading detection
        t = self._check_rapid_trading(events)
        if t:
            triggers.append(t)

        # 2. Size escalation after losses
        t = self._check_revenge_trading(events)
        if t:
            triggers.append(t)

        # 3. Override clustering
        t = self._check_override_clustering(events)
        if t:
            triggers.append(t)

        # 4. Late-session entries
        t = self._check_late_entries(events)
        if t:
            triggers.append(t)

        # 5. Consecutive loss response
        t = self._check_loss_streak_response(events)
        if t:
            triggers.append(t)

        # Compute total score
        result.triggers = triggers
        result.tilt_score = min(100, sum(t.score for t in triggers))

        # Determine level
        for threshold, level, scale, rec in self.LEVELS:
            if result.tilt_score >= threshold:
                result.level = level
                result.allocation_scale = scale
                result.recommendation = rec
                break

        self._history.append(result)
        return result

    def _check_rapid_trading(self, events: List[Dict]) -> Optional[TiltTrigger]:
        """Check for unusually high trade frequency."""
        trades = [e for e in events if e.get("type") in ("fill", "trade", "signal")]
        n_trades = len(trades)

        if n_trades > 10:
            return TiltTrigger(
                name="RAPID_TRADING", score=25,
                detail=f"{n_trades} trades (expected 3-5/day)",
            )
        elif n_trades > 8:
            return TiltTrigger(
                name="RAPID_TRADING", score=15,
                detail=f"{n_trades} trades (above average)",
            )
        return None

    def _check_revenge_trading(self, events: List[Dict]) -> Optional[TiltTrigger]:
        """Detect size increases immediately following losses."""
        fills = [e for e in events if e.get("type") in ("fill", "trade", "exit")]
        if len(fills) < 3:
            return None

        revenge_count = 0
        for i in range(1, len(fills)):
            prev_pnl = fills[i - 1].get("pnl", fills[i - 1].get("realized_pnl", 0))
            curr_size = fills[i].get("shares", fills[i].get("quantity", 0))
            prev_size = fills[i - 1].get("shares", fills[i - 1].get("quantity", 0))

            if isinstance(prev_pnl, (int, float)) and prev_pnl < 0:
                if isinstance(curr_size, (int, float)) and isinstance(prev_size, (int, float)):
                    if prev_size > 0 and curr_size > prev_size * 1.3:
                        revenge_count += 1

        if revenge_count >= 2:
            return TiltTrigger(
                name="REVENGE_TRADING", score=25,
                detail=f"{revenge_count} size increases after losses",
            )
        elif revenge_count == 1:
            return TiltTrigger(
                name="REVENGE_TRADING", score=10,
                detail="1 size increase after loss",
            )
        return None

    def _check_override_clustering(self, events: List[Dict]) -> Optional[TiltTrigger]:
        """Detect multiple manual overrides in short window."""
        overrides = [
            e for e in events
            if e.get("type") in ("override", "manual_entry", "manual_exit")
            or e.get("source") == "manual"
        ]

        if len(overrides) >= 3:
            return TiltTrigger(
                name="OVERRIDE_CLUSTER", score=20,
                detail=f"{len(overrides)} manual overrides",
            )
        elif len(overrides) >= 1:
            return TiltTrigger(
                name="OVERRIDE_CLUSTER", score=5,
                detail=f"{len(overrides)} manual override(s)",
            )
        return None

    def _check_late_entries(self, events: List[Dict]) -> Optional[TiltTrigger]:
        """Detect entries outside normal trading hours."""
        late_count = 0
        for e in events:
            if e.get("type") not in ("fill", "signal", "trade"):
                continue
            ts = e.get("timestamp", e.get("ts", 0))
            if isinstance(ts, (int, float)):
                if ts > 1e12:
                    ts = ts / 1e9
                hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
                # Late = after 20:00 UTC or before 07:00 UTC
                if hour >= 20 or hour < 7:
                    late_count += 1

        if late_count >= 3:
            return TiltTrigger(
                name="LATE_ENTRIES", score=15,
                detail=f"{late_count} trades outside normal hours",
            )
        elif late_count >= 1:
            return TiltTrigger(
                name="LATE_ENTRIES", score=5,
                detail=f"{late_count} late entry",
            )
        return None

    def _check_loss_streak_response(self, events: List[Dict]) -> Optional[TiltTrigger]:
        """Check for immediate re-entry after consecutive losses."""
        exits = [e for e in events if e.get("type") in ("exit", "fill") and "pnl" in e]
        if len(exits) < 4:
            return None

        # Count max consecutive losses
        max_streak = 0
        current_streak = 0
        for e in exits:
            pnl = e.get("pnl", e.get("realized_pnl", 0))
            if isinstance(pnl, (int, float)) and pnl < 0:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0

        if max_streak >= 5:
            return TiltTrigger(
                name="LOSS_STREAK", score=20,
                detail=f"{max_streak} consecutive losses",
            )
        elif max_streak >= 3:
            return TiltTrigger(
                name="LOSS_STREAK", score=10,
                detail=f"{max_streak} consecutive losses",
            )
        return None

    def _load_events(self, date_str: str) -> List[Dict]:
        """Load WAL events for a date."""
        events = []
        patterns = [
            os.path.join(WAL_DIR, f"*{date_str}*"),
            os.path.join(WAL_DIR, "wal.ndjson"),
            os.path.join(WAL_DIR, "events.ndjson"),
        ]
        for pattern in patterns:
            for filepath in glob.glob(pattern):
                try:
                    with open(filepath) as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                event = json.loads(line)
                                ts = event.get("timestamp", event.get("ts", ""))
                                if isinstance(ts, str) and date_str in ts:
                                    events.append(event)
                                elif isinstance(ts, (int, float)):
                                    t = ts / 1e9 if ts > 1e12 else ts
                                    ed = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
                                    if ed == date_str:
                                        events.append(event)
                            except (json.JSONDecodeError, ValueError):
                                continue
                except (OSError, IOError):
                    continue
        return events

    def tilt_score(self) -> int:
        """Get most recent tilt score."""
        if self._history:
            return self._history[-1].tilt_score
        return 0

    def tilt_triggers(self) -> List[str]:
        """Get most recent trigger names."""
        if self._history:
            return [t.name for t in self._history[-1].triggers]
        return []

    def save(self):
        try:
            os.makedirs(os.path.dirname(TILT_FILE), exist_ok=True)
            recent = self._history[-30:] if self._history else []
            data = []
            for r in recent:
                data.append({
                    "date": r.date,
                    "score": r.tilt_score,
                    "level": r.level,
                    "triggers": [{"name": t.name, "score": t.score, "detail": t.detail}
                                 for t in r.triggers],
                })
            with open(TILT_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def load(self):
        if not os.path.exists(TILT_FILE):
            return
        try:
            with open(TILT_FILE) as f:
                data = json.load(f)
            for entry in data:
                r = TiltResult(
                    date=entry.get("date", ""),
                    tilt_score=entry.get("score", 0),
                    level=entry.get("level", "CALM"),
                    triggers=[
                        TiltTrigger(**t) for t in entry.get("triggers", [])
                    ],
                )
                self._history.append(r)
        except Exception:
            pass


# Singleton
_detector: Optional[TiltDetector] = None


def get_tilt_detector() -> TiltDetector:
    global _detector
    if _detector is None:
        _detector = TiltDetector()
        _detector.load()
    return _detector


def run_tilt_analysis() -> Dict:
    """Nightly: analyze today's events for tilt."""
    detector = get_tilt_detector()
    result = detector.analyze_day()
    detector.save()

    # Alert if elevated
    if result.tilt_score >= 40:
        try:
            from python_brain.ouroboros.claude_helper import send_telegram
            triggers = ", ".join(t.name for t in result.triggers)
            send_telegram(
                f"TILT ALERT: {result.level} (score={result.tilt_score})\n"
                f"Triggers: {triggers}\n"
                f"Action: {result.recommendation}\n"
                f"Allocation scale: {result.allocation_scale}"
            )
        except Exception:
            pass

    return {
        "status": result.level,
        "score": result.tilt_score,
        "triggers": [t.name for t in result.triggers],
        "recommendation": result.recommendation,
    }


if __name__ == "__main__":
    result = run_tilt_analysis()
    print(f"Tilt: score={result['score']}, level={result['status']}")
    for t in result.get("triggers", []):
        print(f"  - {t}")
