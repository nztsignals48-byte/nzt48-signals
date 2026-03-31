"""Book 13: System Journal Generator.

Auto-generates daily journal entries from WAL events and nightly analysis.
Implements the compression pyramid:
  - Daily: full entry (13 fields from WAL)
  - Weekly: top 5 insights per week
  - (Monthly/quarterly compression delegated to Claude)

Output: /app/data/journal/journal.ndjson (append-only)

Wired into nightly pipeline Step 17.
"""

from __future__ import annotations

import glob
import json
import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

DATA_DIR = os.environ.get("AEGIS_DATA_DIR", "/app/data")
JOURNAL_DIR = os.path.join(DATA_DIR, "journal")
JOURNAL_FILE = os.path.join(JOURNAL_DIR, "journal.ndjson")
INSIGHTS_FILE = os.path.join(JOURNAL_DIR, "insights.json")
WAL_DIR = os.environ.get("AEGIS_WAL_DIR", "/app/events")


@dataclass
class DailyEntry:
    """A single day's journal entry."""
    date: str
    # Core metrics
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    net_pnl: float = 0.0
    gross_pnl: float = 0.0
    total_costs: float = 0.0
    # Signal stats
    signals_emitted: int = 0
    signals_vetoed: int = 0
    # Regime
    primary_regime: str = "UNKNOWN"
    regime_changes: int = 0
    # Strategy breakdown
    strategy_counts: Dict[str, int] = None
    # Top veto reasons
    veto_reasons: Dict[str, int] = None
    # Insights (auto-generated)
    insights: List[str] = None

    def __post_init__(self):
        if self.strategy_counts is None:
            self.strategy_counts = {}
        if self.veto_reasons is None:
            self.veto_reasons = {}
        if self.insights is None:
            self.insights = []

    def to_dict(self) -> Dict:
        return {
            "date": self.date,
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.wins / max(self.total_trades, 1) * 100, 1),
            "net_pnl": round(self.net_pnl, 2),
            "gross_pnl": round(self.gross_pnl, 2),
            "total_costs": round(self.total_costs, 2),
            "cost_drag_pct": round(
                self.total_costs / max(abs(self.gross_pnl), 0.01) * 100, 1
            ),
            "signals_emitted": self.signals_emitted,
            "signals_vetoed": self.signals_vetoed,
            "signal_rate_pct": round(
                self.signals_emitted / max(self.signals_emitted + self.signals_vetoed, 1) * 100, 1
            ),
            "primary_regime": self.primary_regime,
            "regime_changes": self.regime_changes,
            "strategy_counts": self.strategy_counts,
            "veto_reasons": dict(sorted(self.veto_reasons.items(), key=lambda x: -x[1])[:5]),
            "insights": self.insights,
        }


def _parse_wal_events(date_str: str) -> List[Dict]:
    """Load WAL events for a given date from the events directory."""
    events = []

    # WAL files may be named by date or be a single append-only file
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
                            # Filter to target date
                            ts = event.get("timestamp", event.get("ts", ""))
                            if isinstance(ts, str) and date_str in ts:
                                events.append(event)
                            elif isinstance(ts, (int, float)):
                                event_date = datetime.fromtimestamp(
                                    ts / 1e9 if ts > 1e12 else ts,
                                    tz=timezone.utc,
                                ).strftime("%Y-%m-%d")
                                if event_date == date_str:
                                    events.append(event)
                        except (json.JSONDecodeError, ValueError):
                            continue
            except (OSError, IOError):
                continue

    return events


def _generate_insights(entry: DailyEntry) -> List[str]:
    """Auto-generate insights from the day's metrics."""
    insights = []

    wr = entry.wins / max(entry.total_trades, 1)

    if entry.total_trades == 0:
        insights.append("No trades executed today — check signal generation or market conditions")
    elif wr > 0.6 and entry.total_trades >= 3:
        insights.append(f"Strong day: {wr:.0%} win rate across {entry.total_trades} trades")
    elif wr < 0.3 and entry.total_trades >= 3:
        insights.append(f"Weak day: {wr:.0%} win rate — review signal quality")

    if entry.total_costs > 0 and entry.gross_pnl > 0:
        drag = entry.total_costs / entry.gross_pnl
        if drag > 0.5:
            insights.append(f"Cost drag {drag:.0%} of gross P&L — trades may not be worthwhile")

    if entry.signals_vetoed > entry.signals_emitted * 3:
        insights.append(
            f"High veto rate: {entry.signals_vetoed} vetoed vs {entry.signals_emitted} emitted"
        )

    # Strategy concentration
    if entry.strategy_counts:
        total = sum(entry.strategy_counts.values())
        for strat, count in entry.strategy_counts.items():
            if total > 0 and count / total > 0.7:
                insights.append(f"Strategy concentration: {strat} = {count/total:.0%} of signals")

    if entry.regime_changes > 3:
        insights.append(f"Unstable regime: {entry.regime_changes} regime changes today")

    return insights[:5]  # Cap at 5 insights


def generate_daily_entry(date_str: Optional[str] = None) -> Dict:
    """Generate a daily journal entry from WAL events.

    Args:
        date_str: Date in YYYY-MM-DD format. Defaults to today.

    Returns:
        Journal entry as dict.
    """
    if date_str is None:
        date_str = time.strftime("%Y-%m-%d", time.gmtime())

    entry = DailyEntry(date=date_str)
    events = _parse_wal_events(date_str)

    strategy_counts = Counter()
    veto_reasons = Counter()
    regimes = []

    for event in events:
        etype = event.get("type", event.get("event_type", ""))

        if etype in ("fill", "trade", "exit"):
            entry.total_trades += 1
            pnl = event.get("pnl", event.get("realized_pnl", 0.0))
            if isinstance(pnl, (int, float)):
                entry.gross_pnl += abs(pnl)
                entry.net_pnl += pnl
                if pnl > 0:
                    entry.wins += 1
                else:
                    entry.losses += 1
            cost = event.get("commission", 0.0) + event.get("cost", 0.0)
            if isinstance(cost, (int, float)):
                entry.total_costs += cost
            strat = event.get("strategy", "unknown")
            strategy_counts[strat] += 1

        elif etype in ("signal", "signal_emitted"):
            entry.signals_emitted += 1
            strat = event.get("strategy", "unknown")
            strategy_counts[strat] += 1

        elif etype in ("veto", "check_failed", "signal_vetoed"):
            entry.signals_vetoed += 1
            reason = event.get("reason", event.get("check", "unknown"))
            veto_reasons[reason] += 1

        elif etype in ("regime_change", "regime"):
            entry.regime_changes += 1
            regime = event.get("regime", event.get("new_regime", ""))
            if regime:
                regimes.append(regime)

    entry.strategy_counts = dict(strategy_counts)
    entry.veto_reasons = dict(veto_reasons)

    # Primary regime = most common
    if regimes:
        entry.primary_regime = Counter(regimes).most_common(1)[0][0]

    # Also try loading nightly_output.json for supplementary data
    nightly_file = os.path.join(DATA_DIR, "nightly_output.json")
    if os.path.exists(nightly_file):
        try:
            with open(nightly_file) as f:
                nightly = json.load(f)
            if "regime" in nightly and not regimes:
                entry.primary_regime = nightly["regime"]
        except Exception:
            pass

    entry.insights = _generate_insights(entry)
    return entry.to_dict()


def append_to_journal(entry: Dict):
    """Append an entry to the journal NDJSON file."""
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    try:
        with open(JOURNAL_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def load_recent_entries(n: int = 7) -> List[Dict]:
    """Load the last N journal entries."""
    if not os.path.exists(JOURNAL_FILE):
        return []
    entries = []
    try:
        with open(JOURNAL_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except Exception:
        return []
    return entries[-n:]


def compress_weekly() -> Optional[Dict]:
    """Generate a weekly compression: top 5 insights from the past 7 days."""
    entries = load_recent_entries(7)
    if not entries:
        return None

    all_insights = []
    total_trades = 0
    total_pnl = 0.0
    total_costs = 0.0
    strategy_totals = Counter()

    for entry in entries:
        all_insights.extend(entry.get("insights", []))
        total_trades += entry.get("total_trades", 0)
        total_pnl += entry.get("net_pnl", 0.0)
        total_costs += entry.get("total_costs", 0.0)
        for strat, count in entry.get("strategy_counts", {}).items():
            strategy_totals[strat] += count

    # Deduplicate insights by similarity (simple prefix matching)
    unique_insights = []
    seen_prefixes = set()
    for insight in all_insights:
        prefix = insight[:30]
        if prefix not in seen_prefixes:
            seen_prefixes.add(prefix)
            unique_insights.append(insight)

    wins = sum(e.get("wins", 0) for e in entries)
    return {
        "type": "weekly_compression",
        "week_ending": entries[-1].get("date", ""),
        "days_traded": len(entries),
        "total_trades": total_trades,
        "net_pnl": round(total_pnl, 2),
        "total_costs": round(total_costs, 2),
        "win_rate": round(wins / max(total_trades, 1) * 100, 1),
        "top_strategy": strategy_totals.most_common(1)[0][0] if strategy_totals else "none",
        "top_insights": unique_insights[:5],
    }


def run_journal_generation() -> Dict:
    """Nightly runner: generate today's entry and append to journal."""
    entry = generate_daily_entry()
    append_to_journal(entry)

    # Weekly compression on Fridays
    dow = time.strftime("%u", time.gmtime())
    weekly = None
    if dow == "5":
        weekly = compress_weekly()
        if weekly:
            append_to_journal(weekly)

    return {
        "status": "ok",
        "date": entry.get("date", ""),
        "trades": entry.get("total_trades", 0),
        "pnl": entry.get("net_pnl", 0.0),
        "insights": len(entry.get("insights", [])),
        "weekly_generated": weekly is not None,
    }


if __name__ == "__main__":
    result = run_journal_generation()
    print(f"Journal: {result}")
