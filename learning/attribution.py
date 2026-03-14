"""
learning/attribution.py
========================
Win-rate / expectancy attribution tables.
Slices performance by: strategy_tag, regime_tag, time_window, track, liquidity_bucket.
Returns calibration-ready empty structures when no data exists.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nzt48.learning.attribution")

_STORE_PATH = Path(__file__).parent.parent / "data" / "trade_store.json"


@dataclass
class OutcomeRecord:
    """One signal + outcome record in the TradeStore."""
    signal_id:     str
    ticker:        str
    direction:     str
    strategy_tag:  str
    regime_tag:    str
    time_window:   str
    track:         str
    composite:     float
    entry:         float
    stop:          float
    target1:       float
    net_rr:        float
    generated_at:  str

    # Outcome fields (filled in post-trade)
    outcome:       str   = "PENDING"  # HIT_TARGET | HIT_STOP | TIME_STOP | SCRATCH | PENDING
    exit_price:    float = 0.0
    pnl_r:         float = 0.0        # P&L in R multiples
    mfe_pct:       float = 0.0        # max favourable excursion %
    mae_pct:       float = 0.0        # max adverse excursion %
    slippage_bps:  float = 0.0
    closed_at:     str   = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AttributionSlice:
    """One row in an attribution table."""
    dimension:     str
    value:         str
    n_signals:     int    = 0
    n_outcomes:    int    = 0
    win_rate:      float  = 0.0
    avg_rr:        float  = 0.0
    expectancy:    float  = 0.0     # win_rate * avg_rr - (1-win_rate) * 1.0
    calibration_status: str = "NEEDS_DATA"   # NEEDS_DATA | CALIBRATION_READY | ACTIONABLE

    def to_dict(self) -> dict:
        return asdict(self)


class AttributionEngine:
    """Compute attribution tables from TradeStore. Returns empty tables if no data."""

    def load_records(self) -> list[OutcomeRecord]:
        """Load all records from trade_store.json."""
        try:
            if _STORE_PATH.exists():
                raw = json.loads(_STORE_PATH.read_text())
                records = []
                for r in raw.get("records", []):
                    records.append(OutcomeRecord(**{k: r[k] for k in OutcomeRecord.__dataclass_fields__ if k in r}))
                return records
        except Exception as exc:
            logger.debug("[ATTRIBUTION] load failed: %s", exc)
        return []

    def save_record(self, record: OutcomeRecord) -> None:
        """Append one OutcomeRecord to trade_store.json."""
        try:
            _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            existing = {}
            if _STORE_PATH.exists():
                existing = json.loads(_STORE_PATH.read_text())
            records = existing.get("records", [])
            records.append(record.to_dict())
            existing["records"] = records
            existing["updated_at"] = datetime.now(timezone.utc).isoformat()
            import tempfile, os
            fd, tmp = tempfile.mkstemp(dir=_STORE_PATH.parent, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps(existing, indent=2, default=str))
                f.flush(); os.fsync(f.fileno())
            Path(tmp).replace(_STORE_PATH)
        except Exception as exc:
            logger.warning("[ATTRIBUTION] save failed: %s", exc)

    def compute_attribution(self, dimension: str) -> list[AttributionSlice]:
        """
        Compute attribution table sliced by dimension.
        Returns calibration-ready empty rows if no data.
        """
        records = self.load_records()
        completed = [r for r in records if r.outcome not in ("PENDING", "")]

        buckets: dict[str, list[OutcomeRecord]] = defaultdict(list)
        for r in records:
            val = getattr(r, dimension, "unknown")
            buckets[str(val)].append(r)

        slices = []
        for val, recs in sorted(buckets.items()):
            completed_recs = [r for r in recs if r.outcome not in ("PENDING", "")]
            n = len(completed_recs)
            if n == 0:
                slices.append(AttributionSlice(
                    dimension=dimension, value=val,
                    n_signals=len(recs), n_outcomes=0,
                    calibration_status="NEEDS_DATA",
                ))
                continue
            wins = [r for r in completed_recs if r.outcome == "HIT_TARGET"]
            win_rate = len(wins) / n
            avg_rr   = sum(r.net_rr for r in completed_recs) / n if n else 0.0
            expectancy = win_rate * avg_rr - (1 - win_rate) * 1.0
            status = "ACTIONABLE" if n >= 30 else ("CALIBRATION_READY" if n >= 10 else "NEEDS_DATA")
            slices.append(AttributionSlice(
                dimension=dimension, value=val,
                n_signals=len(recs), n_outcomes=n,
                win_rate=round(win_rate, 3),
                avg_rr=round(avg_rr, 3),
                expectancy=round(expectancy, 3),
                calibration_status=status,
            ))

        if not slices:
            return [AttributionSlice(
                dimension=dimension, value="(no_data)",
                n_signals=0, n_outcomes=0,
                calibration_status="NEEDS_DATA",
            )]
        return slices

    def get_summary(self) -> dict:
        """Get overall attribution summary for the API."""
        records = self.load_records()
        n_total = len(records)
        n_completed = len([r for r in records if r.outcome not in ("PENDING", "")])
        return {
            "n_total_signals":    n_total,
            "n_outcomes_recorded": n_completed,
            "n_pending":          n_total - n_completed,
            "calibration_status": "ACTIONABLE" if n_completed >= 30 else (
                "CALIBRATION_READY" if n_completed >= 10 else "NEEDS_DATA"
            ),
            "strategy_attribution": [s.to_dict() for s in self.compute_attribution("strategy_tag")],
            "regime_attribution":   [s.to_dict() for s in self.compute_attribution("regime_tag")],
            "track_attribution":    [s.to_dict() for s in self.compute_attribution("track")],
            "generated_at":         datetime.now(timezone.utc).isoformat(),
        }
