"""Macro Event Backfill Layer — historical economic event calendar for veto-quality analysis.

Enables the nightly learning loop to classify trades as "macro_victim" vs "clean".
COLD PATH module — runs on cron via nightly_v6 or CLI, never touches hot path.
Static calendar only (stdlib), no external API calls.

Usage:
  python3 -m python_brain.ouroboros.macro_event_layer --generate-2026
  python3 -m python_brain.ouroboros.macro_event_layer --query 2026-03-20
"""
from __future__ import annotations

import argparse, json, logging, os
from dataclasses import asdict, dataclass
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("ouroboros.macro_events")

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
CACHE_FILE = DATA_DIR / "macro_events_cache.json"
STATIC_TPL = "macro_events_{year}.json"
HIGH_IMPACT_WINDOW_MIN = 30

# ── Data Model ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MacroEvent:
    """Single economic calendar event."""
    date: str                       # YYYY-MM-DD
    time_utc: str                   # HH:MM or "AllDay"
    event_name: str
    impact: str                     # "high" | "medium" | "low"
    country: str                    # "US" | "UK" | "EU"
    actual: Optional[float] = None
    forecast: Optional[float] = None
    previous: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]: return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> MacroEvent:
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})

    def timestamp_utc(self) -> Optional[datetime]:
        if self.time_utc == "AllDay":
            return None
        try:
            return datetime.strptime(f"{self.date} {self.time_utc}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

# ── Date helpers ──────────────────────────────────────────────────────────

def _nth_wd(year: int, month: int, wd: int, n: int) -> date:
    """nth occurrence of weekday (0=Mon..4=Fri) in month. n is 1-based."""
    first = date(year, month, 1)
    d = (wd - first.weekday()) % 7
    return first + timedelta(days=d, weeks=n - 1)

def _last_wd(year: int, month: int, wd: int) -> date:
    nxt = date(year + (month // 12), (month % 12) + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - wd) % 7)

def _first_bizday(year: int, month: int) -> date:
    d = date(year, month, 1)
    while d.weekday() >= 5: d += timedelta(days=1)
    return d

# ── Static calendar generators ────────────────────────────────────────────

def _monthly(year: int, name: str, impact: str, country: str, time: str,
             wd: int, nth: int, months: range = range(1, 13)) -> List[MacroEvent]:
    return [MacroEvent(_nth_wd(year, m, wd, nth).isoformat(), time, name, impact, country)
            for m in months]

def _monthly_last(year: int, name: str, impact: str, country: str, time: str,
                  wd: int, months: range = range(1, 13)) -> List[MacroEvent]:
    return [MacroEvent(_last_wd(year, m, wd).isoformat(), time, name, impact, country)
            for m in months]

def _specific_months(year: int, name: str, impact: str, country: str, time: str,
                     wd: int, nth: int, months: List[int]) -> List[MacroEvent]:
    return [MacroEvent(_nth_wd(year, m, wd, nth).isoformat(), time, name, impact, country)
            for m in months]

def generate_static_calendar(year: int) -> List[MacroEvent]:
    """Generate complete static calendar for a year. 113 events covering all major releases."""
    ev: List[MacroEvent] = []
    # FOMC — 8/yr, last Wed (capped at 28th) at 18:00 UTC
    for m in [1, 3, 5, 6, 7, 9, 11, 12]:
        w = _last_wd(year, m, 2)  # Wed=2
        if w.day > 28: w -= timedelta(weeks=1)
        ev.append(MacroEvent(w.isoformat(), "18:00", "FOMC Rate Decision", "high", "US"))
    # NFP — 1st Fri monthly, 12:30 UTC
    ev += _monthly(year, "US Non-Farm Payrolls", "high", "US", "12:30", 4, 1)
    # CPI — 2nd Wed monthly, 12:30 UTC
    ev += _monthly(year, "US CPI", "high", "US", "12:30", 2, 2)
    # PPI — 2nd Thu monthly, 12:30 UTC
    ev += _monthly(year, "US PPI", "medium", "US", "12:30", 3, 2)
    # Core PCE — last Fri monthly, 12:30 UTC
    ev += _monthly_last(year, "US Core PCE", "high", "US", "12:30", 4)
    # US GDP Advance — last Thu of Jan/Apr/Jul/Oct
    for m in [1, 4, 7, 10]:
        ev.append(MacroEvent(_last_wd(year, m, 3).isoformat(), "12:30", "US GDP (Advance)", "high", "US"))
    # ISM Manufacturing — 1st biz day monthly, 14:00 UTC
    for m in range(1, 13):
        ev.append(MacroEvent(_first_bizday(year, m).isoformat(), "14:00", "ISM Manufacturing PMI", "medium", "US"))
    # ECB — 3rd Thu, 8 months
    ev += _specific_months(year, "ECB Rate Decision", "high", "EU", "12:15", 3, 3,
                           [1, 3, 4, 6, 7, 9, 10, 12])
    # BoE — 1st Thu, 8 months
    ev += _specific_months(year, "BoE Rate Decision", "high", "UK", "12:00", 3, 1,
                           [2, 3, 5, 6, 8, 9, 11, 12])
    # UK GDP — 2nd Fri monthly (high for quarterly months)
    for m in range(1, 13):
        imp = "high" if m in (1, 4, 7, 10) else "medium"
        ev.append(MacroEvent(_nth_wd(year, m, 4, 2).isoformat(), "07:00", "UK GDP", imp, "UK"))
    # OpEx — 3rd Fri monthly (quad witch Mar/Jun/Sep/Dec)
    for m in range(1, 13):
        quad = m in (3, 6, 9, 12)
        ev.append(MacroEvent(_nth_wd(year, m, 4, 3).isoformat(), "AllDay",
                             "Quad Witching OpEx" if quad else "Monthly OpEx",
                             "high" if quad else "medium", "US"))
    # Jackson Hole — last Fri of Aug
    ev.append(MacroEvent(_last_wd(year, 8, 4).isoformat(), "14:00", "Jackson Hole Symposium", "high", "US"))
    ev.sort(key=lambda e: (e.date, e.time_utc))
    log.info("Generated %d static macro events for %d", len(ev), year)
    return ev

# ── EventCalendar ─────────────────────────────────────────────────────────

class EventCalendar:
    """Thread-safe macro event calendar. Loaded once, read-only after."""

    def __init__(self) -> None:
        self._events: List[MacroEvent] = []
        self._by_date: Dict[str, List[MacroEvent]] = {}

    @property
    def event_count(self) -> int: return len(self._events)

    def _index(self) -> None:
        self._by_date = {}
        for ev in self._events:
            self._by_date.setdefault(ev.date, []).append(ev)

    # ── Loading ───────────────────────────────────────────────────────────

    def load_events(self, start_date: str, end_date: str) -> List[MacroEvent]:
        """Load events for date range. Prefers saved static file, else generates."""
        sd, ed = date.fromisoformat(start_date), date.fromisoformat(end_date)
        all_ev: List[MacroEvent] = []
        for yr in range(sd.year, ed.year + 1):
            p = DATA_DIR / STATIC_TPL.format(year=yr)
            if p.exists():
                all_ev.extend(self._read_json(p))
            else:
                all_ev.extend(generate_static_calendar(yr))
        # Overlay cache (may have actual/forecast values filled in)
        if CACHE_FILE.exists():
            cached = self._read_json(CACHE_FILE)
            keys = {(e.date, e.event_name) for e in all_ev}
            for ce in cached:
                if (ce.date, ce.event_name) not in keys:
                    all_ev.append(ce)
        self._events = sorted([e for e in all_ev if start_date <= e.date <= end_date],
                              key=lambda e: (e.date, e.time_utc))
        self._index()
        log.info("EventCalendar: %d events for %s..%s", len(self._events), start_date, end_date)
        return self._events

    def get_events_for_date(self, dt: str) -> List[MacroEvent]:
        return list(self._by_date.get(dt, []))

    # ── High-impact window ────────────────────────────────────────────────

    def is_high_impact_window(self, timestamp_ns: int) -> bool:
        """True if within HIGH_IMPACT_WINDOW_MIN of any high-impact event."""
        ts = datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=timezone.utc)
        win = timedelta(minutes=HIGH_IMPACT_WINDOW_MIN)
        for ev in self._by_date.get(ts.strftime("%Y-%m-%d"), []):
            if ev.impact != "high": continue
            ev_ts = ev.timestamp_utc()
            if ev_ts is None: return True          # AllDay high = whole day flagged
            if abs(ts - ev_ts) <= win: return True
        return False

    # ── Integration hooks ─────────────────────────────────────────────────

    def get_upcoming_events(self, hours_ahead: int = 24) -> List[MacroEvent]:
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours_ahead)
        out = []
        for ev in self._events:
            ev_ts = ev.timestamp_utc()
            if ev_ts is None:
                if now.date() <= date.fromisoformat(ev.date) <= cutoff.date():
                    out.append(ev)
            elif now <= ev_ts <= cutoff:
                out.append(ev)
        return out

    def classify_trade_macro_context(self, entry_time_ns: int, exit_time_ns: int) -> Dict[str, Any]:
        """Classify trade macro context: "clean" | "macro_proximate" | "macro_victim"."""
        entry = datetime.fromtimestamp(entry_time_ns / 1_000_000_000, tz=timezone.utc)
        exit_ = datetime.fromtimestamp(exit_time_ns / 1_000_000_000, tz=timezone.utc)
        win = timedelta(minutes=HIGH_IMPACT_WINDOW_MIN)
        search_s = (entry - timedelta(days=1)).strftime("%Y-%m-%d")
        search_e = (exit_ + timedelta(days=1)).strftime("%Y-%m-%d")

        hits: List[MacroEvent] = []
        hi = 0
        for dt_str, devs in self._by_date.items():
            if not (search_s <= dt_str <= search_e): continue
            for ev in devs:
                ev_ts = ev.timestamp_utc()
                if ev_ts is None:
                    if entry.date() <= date.fromisoformat(ev.date) <= exit_.date():
                        hits.append(ev); hi += (ev.impact == "high")
                elif (entry - win) <= ev_ts <= (exit_ + win):
                    hits.append(ev); hi += (ev.impact == "high")

        cls_ = "clean"
        if hits:
            cls_ = "macro_proximate"
            tight = timedelta(minutes=15)
            for ev in hits:
                if ev.impact != "high": continue
                ev_ts = ev.timestamp_utc()
                if ev_ts is None or entry <= ev_ts <= exit_ or abs(entry - ev_ts) <= tight:
                    cls_ = "macro_victim"; break

        return {"events_during_trade": [e.to_dict() for e in hits],
                "high_impact_count": hi, "macro_classification": cls_}

    # ── Persistence ───────────────────────────────────────────────────────

    def save_cache(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps([e.to_dict() for e in self._events], indent=2))
        tmp.rename(CACHE_FILE)
        log.info("Saved %d events to %s", len(self._events), CACHE_FILE)

    def load_cache(self) -> bool:
        if not CACHE_FILE.exists(): return False
        self._events = self._read_json(CACHE_FILE)
        self._index()
        log.info("Loaded %d events from cache", len(self._events))
        return True

    @staticmethod
    def _read_json(path: Path) -> List[MacroEvent]:
        with open(path) as f: return [MacroEvent.from_dict(d) for d in json.load(f)]

    @staticmethod
    def save_static_calendar(year: int) -> Path:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        p = DATA_DIR / STATIC_TPL.format(year=year)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps([e.to_dict() for e in generate_static_calendar(year)], indent=2))
        tmp.rename(p)
        log.info("Saved static calendar to %s", p)
        return p

# ── CLI ───────────────────────────────────────────────────────────────────

def _fmt(ev: MacroEvent) -> str:
    tag = {"high": "!!!", "medium": " ! ", "low": "   "}.get(ev.impact, "   ")
    extra = "".join(f" {k}={v}" for k, v in [("A", ev.actual), ("F", ev.forecast)] if v is not None)
    return f"  [{tag}] {ev.date} {ev.time_utc:>6}  {ev.country:>2} {ev.event_name}{extra}"

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    pa = argparse.ArgumentParser(description="Macro Event Calendar")
    pa.add_argument("--generate", type=int, metavar="YEAR")
    pa.add_argument("--generate-2026", action="store_true")
    pa.add_argument("--query", type=str, metavar="DATE")
    pa.add_argument("--upcoming", type=int, nargs="?", const=24, metavar="HOURS")
    pa.add_argument("--stats", action="store_true")
    a = pa.parse_args()

    year = a.generate or (2026 if a.generate_2026 else None)
    if year:
        print(f"Generated: {EventCalendar.save_static_calendar(year)}")
        cal = EventCalendar(); cal.load_events(f"{year}-01-01", f"{year}-12-31")
        _stats(cal, year); return

    now = datetime.now(timezone.utc)
    cal = EventCalendar(); cal.load_events(f"{now.year}-01-01", f"{now.year}-12-31")

    if a.query:
        evs = cal.get_events_for_date(a.query)
        print(f"Events on {a.query}:" if evs else f"No events on {a.query}")
        for e in evs: print(_fmt(e)); return
    if a.upcoming is not None:
        evs = cal.get_upcoming_events(a.upcoming)
        print(f"Upcoming ({a.upcoming}h):" if evs else f"No events in next {a.upcoming}h")
        for e in evs: print(_fmt(e)); return
    if a.stats: _stats(cal, now.year); return
    # Default: today + 48h upcoming
    evs = cal.get_events_for_date(now.strftime("%Y-%m-%d"))
    print(f"Today ({now.strftime('%Y-%m-%d')}):")
    for e in evs: print(_fmt(e))
    if not evs: print("  (none)")
    up = cal.get_upcoming_events(48)
    if up: print("\nUpcoming (48h):"); [print(_fmt(e)) for e in up]

def _stats(cal: EventCalendar, year: int) -> None:
    hi = sum(1 for e in cal._events if e.impact == "high")
    md = sum(1 for e in cal._events if e.impact == "medium")
    print(f"\n=== Macro Event Calendar {year} === {cal.event_count} total ({hi} high, {md} medium)")
    cs: Dict[str, int] = {}; ns: Dict[str, int] = {}
    for e in cal._events: cs[e.country] = cs.get(e.country, 0) + 1; ns[e.event_name] = ns.get(e.event_name, 0) + 1
    print("By country: " + ", ".join(f"{c}:{n}" for c, n in sorted(cs.items(), key=lambda x: -x[1])))
    print("By type:")
    for name, n in sorted(ns.items(), key=lambda x: -x[1]): print(f"  {name}: {n}")

if __name__ == "__main__":
    main()
