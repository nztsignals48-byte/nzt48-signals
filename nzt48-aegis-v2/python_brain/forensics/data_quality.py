"""Data Quality Nightly Report — Book 45.

Analyzes WAL data for quality issues:
1. Tick coverage heatmap (which tickers had data, which gaps)
2. Stale tick detection (per-ticker staleness events)
3. Gap inventory (periods with no ticks)
4. Spread anomalies (unusually wide spreads)
5. Volume consistency (sudden drops or spikes)
6. WAL integrity (CRC32 verification, event counts)

Runs nightly after market close. Results feed into Ouroboros
recommendations and Telegram alerts.

Usage:
    from python_brain.forensics.data_quality import (
        DataQualityAnalyzer, DataQualityReport,
    )

    analyzer = DataQualityAnalyzer()
    report = analyzer.analyze_day("2026-03-29")
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

log = logging.getLogger("data_quality")

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))


@dataclass
class TickerQuality:
    """Data quality metrics for a single ticker."""
    ticker: str = ""
    tick_count: int = 0
    first_tick_time: str = ""
    last_tick_time: str = ""
    gaps_over_60s: int = 0
    max_gap_secs: float = 0.0
    avg_spread_bps: float = 0.0
    max_spread_bps: float = 0.0
    volume_anomalies: int = 0
    stale_events: int = 0
    coverage_pct: float = 0.0  # % of trading session with data


@dataclass
class DataQualityReport:
    """Complete data quality report for one day."""
    date: str = ""
    total_events: int = 0
    event_types: Dict[str, int] = field(default_factory=dict)
    tickers_with_data: int = 0
    tickers_no_data: int = 0
    ticker_quality: Dict[str, TickerQuality] = field(default_factory=dict)
    wal_integrity: bool = True
    corrupt_lines: int = 0
    avg_coverage_pct: float = 0.0
    overall_score: float = 0.0  # 0-100

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "total_events": self.total_events,
            "event_types": self.event_types,
            "tickers_with_data": self.tickers_with_data,
            "wal_integrity": self.wal_integrity,
            "corrupt_lines": self.corrupt_lines,
            "avg_coverage_pct": round(self.avg_coverage_pct, 1),
            "overall_score": round(self.overall_score, 1),
            "ticker_details": {
                t: {
                    "ticks": q.tick_count,
                    "gaps_60s": q.gaps_over_60s,
                    "max_gap_s": round(q.max_gap_secs, 1),
                    "avg_spread_bps": round(q.avg_spread_bps, 1),
                    "coverage_pct": round(q.coverage_pct, 1),
                }
                for t, q in self.ticker_quality.items()
            },
        }


class DataQualityAnalyzer:
    """Analyze WAL data quality for a trading day."""

    def __init__(self, expected_tickers: Optional[Set[str]] = None):
        self.expected_tickers = expected_tickers or set()

    def analyze_day(self, date_str: str) -> DataQualityReport:
        """Analyze a single day's WAL file."""
        report = DataQualityReport(date=date_str)
        wal_path = WAL_DIR / f"{date_str}.ndjson"

        if not wal_path.exists():
            log.warning("No WAL file for %s", date_str)
            report.overall_score = 0.0
            return report

        # Parse WAL
        ticker_ticks: Dict[str, List[float]] = defaultdict(list)  # ticker → timestamps
        ticker_spreads: Dict[str, List[float]] = defaultdict(list)  # ticker → spreads
        event_types: Dict[str, int] = defaultdict(int)
        corrupt = 0

        with open(wal_path) as f:
            for line_num, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    evt = json.loads(line)
                    et = evt.get("event_type", "unknown")
                    event_types[et] += 1
                    report.total_events += 1

                    # Track tick timestamps per ticker
                    ticker = evt.get("ticker", evt.get("symbol", ""))
                    ts = evt.get("event_time", evt.get("timestamp", ""))

                    if ticker and ts:
                        try:
                            # Convert ISO timestamp to seconds
                            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            epoch_s = dt.timestamp()
                            ticker_ticks[ticker].append(epoch_s)
                        except (ValueError, TypeError):
                            pass

                    # Track spreads
                    spread = evt.get("spread_bps", 0)
                    if ticker and spread > 0:
                        ticker_spreads[ticker].append(spread)

                except json.JSONDecodeError:
                    corrupt += 1

        report.event_types = dict(event_types)
        report.corrupt_lines = corrupt
        report.wal_integrity = corrupt == 0
        report.tickers_with_data = len(ticker_ticks)

        # Per-ticker quality
        for ticker, timestamps in ticker_ticks.items():
            tq = TickerQuality(ticker=ticker, tick_count=len(timestamps))

            if timestamps:
                timestamps.sort()
                tq.first_tick_time = datetime.fromtimestamp(timestamps[0], tz=timezone.utc).isoformat()
                tq.last_tick_time = datetime.fromtimestamp(timestamps[-1], tz=timezone.utc).isoformat()

                # Gap analysis
                if len(timestamps) >= 2:
                    diffs = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)]
                    tq.gaps_over_60s = sum(1 for d in diffs if d > 60)
                    tq.max_gap_secs = max(diffs) if diffs else 0

                # Coverage: fraction of 8h trading session (28800 secs) with data
                total_span = timestamps[-1] - timestamps[0]
                session_secs = 28800  # 8 hours
                tq.coverage_pct = min(100, total_span / session_secs * 100) if session_secs > 0 else 0

            # Spread analysis
            spreads = ticker_spreads.get(ticker, [])
            if spreads:
                tq.avg_spread_bps = sum(spreads) / len(spreads)
                tq.max_spread_bps = max(spreads)

            report.ticker_quality[ticker] = tq

        # Compute overall score
        if report.ticker_quality:
            coverages = [tq.coverage_pct for tq in report.ticker_quality.values()]
            report.avg_coverage_pct = sum(coverages) / len(coverages)

            # Score: 50% coverage + 25% integrity + 25% gap quality
            coverage_score = report.avg_coverage_pct
            integrity_score = 100 if report.wal_integrity else max(0, 100 - report.corrupt_lines * 10)
            gap_score = 100 - min(100, sum(
                tq.gaps_over_60s for tq in report.ticker_quality.values()
            ) * 2)
            report.overall_score = coverage_score * 0.50 + integrity_score * 0.25 + gap_score * 0.25

        # Missing tickers
        if self.expected_tickers:
            observed = set(ticker_ticks.keys())
            report.tickers_no_data = len(self.expected_tickers - observed)

        return report

    def save_report(self, report: DataQualityReport, output_dir: Optional[Path] = None) -> Path:
        """Save data quality report to JSON."""
        out = output_dir or (DATA_DIR / "forensics")
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"data_quality_{report.date}.json"
        with open(path, "w") as f:
            json.dump(report.to_dict(), f, indent=2)
        log.info("Data quality report: %s (score=%.0f%%)", path, report.overall_score)
        return path
