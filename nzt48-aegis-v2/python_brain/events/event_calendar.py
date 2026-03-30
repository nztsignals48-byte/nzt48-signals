"""
Book 24: Event Sniper - Economic event calendar and proximity detection.

Tracks high-impact events (FOMC, CPI, NFP, earnings seasons) and provides
event-aware position sizing and timing recommendations.
"""

import os
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from enum import Enum


DATA_DIR = os.environ.get("AEGIS_DATA_DIR", "/app/data")


class EventType(Enum):
    """Types of market-moving events."""
    FOMC = "FOMC"
    CPI = "CPI"
    NFP = "NFP"
    PCE = "PCE"
    EARNINGS = "EARNINGS"
    GEOPOLITICAL = "GEOPOLITICAL"


class Impact(Enum):
    """Expected market impact level."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class ScheduledEvent:
    """A scheduled economic or market event."""
    name: str
    event_type: str  # EventType value
    date: str  # ISO format YYYY-MM-DD
    time_utc: str  # HH:MM or "UNKNOWN"
    impact: str  # Impact value
    expected_vol_mult: float  # Expected volatility multiplier
    pre_event_hours: float  # Hours before event to prepare
    post_event_hours: float  # Hours after event to wait

    def timestamp_utc(self) -> Optional[datetime]:
        """Convert to UTC datetime, returns None if time unknown."""
        if self.time_utc == "UNKNOWN":
            return None
        try:
            dt_str = f"{self.date} {self.time_utc}"
            return datetime.strptime(dt_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def is_near(self, timestamp_ns: int) -> bool:
        """Check if timestamp is near this event (within pre/post window)."""
        event_dt = self.timestamp_utc()
        if event_dt is None:
            # If time unknown, check if same day
            check_dt = datetime.fromtimestamp(timestamp_ns / 1e9, tz=timezone.utc)
            event_date = datetime.strptime(self.date, "%Y-%m-%d").date()
            return check_dt.date() == event_date

        check_dt = datetime.fromtimestamp(timestamp_ns / 1e9, tz=timezone.utc)
        pre_window = event_dt - timedelta(hours=self.pre_event_hours)
        post_window = event_dt + timedelta(hours=self.post_event_hours)

        return pre_window <= check_dt <= post_window


class EventCalendar:
    """Manages economic event calendar with proximity detection."""

    def __init__(self):
        self.events: List[ScheduledEvent] = []
        self._load_or_refresh()

    def _load_or_refresh(self):
        """Load calendar from disk or refresh if stale."""
        calendar_path = os.path.join(DATA_DIR, "event_calendar.json")

        if os.path.exists(calendar_path):
            try:
                with open(calendar_path, 'r') as f:
                    data = json.load(f)
                    self.events = [ScheduledEvent(**e) for e in data.get("events", [])]
                    # Check if calendar is stale (older than 7 days)
                    saved_date = datetime.fromisoformat(data.get("generated_at", "2000-01-01"))
                    if (datetime.now(timezone.utc) - saved_date).days > 7:
                        self.refresh()
            except Exception as e:
                print(f"[EventCalendar] Failed to load calendar: {e}, refreshing")
                self.refresh()
        else:
            self.refresh()

    def refresh(self):
        """Generate calendar from known schedules."""
        self.events = []
        now = datetime.now(timezone.utc)

        # Generate 30-day forward calendar
        for days_ahead in range(30):
            target_date = now + timedelta(days=days_ahead)

            # FOMC meetings (hardcoded 2025-2026)
            self._add_fomc_events(target_date)

            # CPI (2nd Wednesday of month, 8:30 AM ET = 13:30 UTC)
            self._add_cpi_events(target_date)

            # NFP (first Friday of month, 8:30 AM ET = 13:30 UTC)
            self._add_nfp_events(target_date)

            # PCE (last Friday of month, 8:30 AM ET = 13:30 UTC)
            self._add_pce_events(target_date)

            # Earnings seasons
            self._add_earnings_events(target_date)

        # Sort by date
        self.events.sort(key=lambda e: e.date)

        self.save()

    def _add_fomc_events(self, target_date: datetime):
        """Add FOMC meetings from hardcoded schedule."""
        fomc_dates_2025 = [
            "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
            "2025-07-30", "2025-09-17", "2025-11-05", "2025-12-17"
        ]
        fomc_dates_2026 = [
            "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
            "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16"
        ]

        all_fomc = fomc_dates_2025 + fomc_dates_2026
        date_str = target_date.strftime("%Y-%m-%d")

        if date_str in all_fomc:
            self.events.append(ScheduledEvent(
                name="FOMC Rate Decision",
                event_type=EventType.FOMC.value,
                date=date_str,
                time_utc="19:00",  # 2:00 PM ET = 19:00 UTC
                impact=Impact.HIGH.value,
                expected_vol_mult=1.5,
                pre_event_hours=4.0,
                post_event_hours=4.0
            ))

    def _add_cpi_events(self, target_date: datetime):
        """Add CPI releases (2nd Wednesday of month)."""
        # Check if this is the 2nd Wednesday
        if target_date.weekday() == 2:  # Wednesday
            # Count Wednesdays in month so far
            first_day = target_date.replace(day=1)
            wednesdays = 0
            for day in range(1, target_date.day + 1):
                check_date = first_day.replace(day=day)
                if check_date.weekday() == 2:
                    wednesdays += 1

            if wednesdays == 2:
                self.events.append(ScheduledEvent(
                    name="CPI Release",
                    event_type=EventType.CPI.value,
                    date=target_date.strftime("%Y-%m-%d"),
                    time_utc="13:30",  # 8:30 AM ET
                    impact=Impact.HIGH.value,
                    expected_vol_mult=1.8,
                    pre_event_hours=2.0,
                    post_event_hours=3.0
                ))

    def _add_nfp_events(self, target_date: datetime):
        """Add NFP releases (first Friday of month)."""
        if target_date.weekday() == 4:  # Friday
            # Check if this is first Friday
            first_day = target_date.replace(day=1)
            fridays = 0
            for day in range(1, target_date.day + 1):
                check_date = first_day.replace(day=day)
                if check_date.weekday() == 4:
                    fridays += 1

            if fridays == 1:
                self.events.append(ScheduledEvent(
                    name="Non-Farm Payrolls",
                    event_type=EventType.NFP.value,
                    date=target_date.strftime("%Y-%m-%d"),
                    time_utc="13:30",  # 8:30 AM ET
                    impact=Impact.HIGH.value,
                    expected_vol_mult=2.0,
                    pre_event_hours=2.0,
                    post_event_hours=4.0
                ))

    def _add_pce_events(self, target_date: datetime):
        """Add PCE releases (last Friday of month)."""
        if target_date.weekday() == 4:  # Friday
            # Check if this is last Friday of month
            next_week = target_date + timedelta(days=7)
            if next_week.month != target_date.month:
                self.events.append(ScheduledEvent(
                    name="PCE Inflation",
                    event_type=EventType.PCE.value,
                    date=target_date.strftime("%Y-%m-%d"),
                    time_utc="13:30",  # 8:30 AM ET
                    impact=Impact.MEDIUM.value,
                    expected_vol_mult=1.3,
                    pre_event_hours=2.0,
                    post_event_hours=2.0
                ))

    def _add_earnings_events(self, target_date: datetime):
        """Add earnings season markers."""
        month = target_date.month

        # Q1: Jan-Feb, Q2: Apr-May, Q3: Jul-Aug, Q4: Oct-Nov
        earnings_months = [1, 2, 4, 5, 7, 8, 10, 11]

        if month in earnings_months and target_date.day == 15:
            quarter = {1: "Q4", 2: "Q4", 4: "Q1", 5: "Q1",
                      7: "Q2", 8: "Q2", 10: "Q3", 11: "Q3"}[month]

            self.events.append(ScheduledEvent(
                name=f"{quarter} Earnings Season Peak",
                event_type=EventType.EARNINGS.value,
                date=target_date.strftime("%Y-%m-%d"),
                time_utc="UNKNOWN",
                impact=Impact.MEDIUM.value,
                expected_vol_mult=1.2,
                pre_event_hours=8.0,
                post_event_hours=8.0
            ))

    def save(self):
        """Save calendar to disk."""
        calendar_path = os.path.join(DATA_DIR, "event_calendar.json")
        os.makedirs(DATA_DIR, exist_ok=True)

        data = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "events": [asdict(e) for e in self.events]
        }

        with open(calendar_path, 'w') as f:
            json.dump(data, f, indent=2)

    def upcoming(self, days: int = 7) -> List[ScheduledEvent]:
        """Get events in next N days."""
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=days)

        upcoming_events = []
        for event in self.events:
            event_date = datetime.strptime(event.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if now <= event_date <= cutoff:
                upcoming_events.append(event)

        return upcoming_events

    def is_near_event(self, timestamp_ns: int) -> Optional[ScheduledEvent]:
        """Check if timestamp is near a HIGH-impact event."""
        for event in self.events:
            if event.impact == Impact.HIGH.value and event.is_near(timestamp_ns):
                return event
        return None

    def get_sizing_modifier(self, event: ScheduledEvent) -> float:
        """Get position size multiplier based on event impact."""
        impact_modifiers = {
            Impact.HIGH.value: 0.3,
            Impact.MEDIUM.value: 0.5,
            Impact.LOW.value: 0.7
        }
        return impact_modifiers.get(event.impact, 1.0)

    def get_min_confidence(self, event: ScheduledEvent) -> float:
        """Get minimum confidence threshold for event-based entries."""
        confidence_thresholds = {
            Impact.HIGH.value: 0.65,
            Impact.MEDIUM.value: 0.80,
            Impact.LOW.value: 0.50
        }
        return confidence_thresholds.get(event.impact, 0.70)


# Singleton instance for bridge.py
_event_calendar_singleton: Optional[EventCalendar] = None


def get_event_calendar() -> EventCalendar:
    """Get or create singleton EventCalendar instance."""
    global _event_calendar_singleton
    if _event_calendar_singleton is None:
        _event_calendar_singleton = EventCalendar()
    return _event_calendar_singleton


def run_event_refresh() -> Dict[str, Any]:
    """
    Refresh event calendar (called by nightly pipeline).

    Returns:
        Summary dict with event counts and upcoming events.
    """
    calendar = get_event_calendar()
    calendar.refresh()

    upcoming_7d = calendar.upcoming(days=7)
    high_impact = [e for e in upcoming_7d if e.impact == Impact.HIGH.value]

    summary = {
        "status": "success",
        "total_events": len(calendar.events),
        "upcoming_7d": len(upcoming_7d),
        "high_impact_7d": len(high_impact),
        "next_high_impact": high_impact[0].name if high_impact else None,
        "calendar_path": os.path.join(DATA_DIR, "event_calendar.json")
    }

    print(f"[EventCalendar] Refreshed: {summary['total_events']} events, "
          f"{summary['high_impact_7d']} HIGH impact in next 7 days")

    return summary


if __name__ == "__main__":
    print("Book 24: Event Sniper - Calendar Refresh")
    print("=" * 60)

    result = run_event_refresh()

    print(f"\nStatus: {result['status']}")
    print(f"Total events: {result['total_events']}")
    print(f"Upcoming (7d): {result['upcoming_7d']}")
    print(f"HIGH impact (7d): {result['high_impact_7d']}")
    print(f"Next HIGH impact: {result['next_high_impact']}")

    print("\n--- Upcoming Events (7 days) ---")
    calendar = get_event_calendar()
    for event in calendar.upcoming(days=7):
        print(f"  {event.date} {event.time_utc:>8} | {event.impact:>6} | {event.name}")

    print("\n--- Event Proximity Check ---")
    now_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
    near = calendar.is_near_event(now_ns)
    if near:
        print(f"  NEAR HIGH-IMPACT EVENT: {near.name} on {near.date}")
        print(f"  Sizing modifier: {calendar.get_sizing_modifier(near):.2f}x")
        print(f"  Min confidence: {calendar.get_min_confidence(near):.2f}")
    else:
        print("  No HIGH-impact events nearby")

    print(f"\n✓ Calendar saved to: {result['calendar_path']}")
