"""
learning/edge_ledger.py
========================
Computes Edge Ledger: per-bucket win rate, expectancy, confidence.
Bucket key: strategy_tag x regime_tag x track x time_window x liquidity_bucket

Writes:
- data/edge_ledger.json
- data/edge_weekly_delta.json

Uses Wilson interval for win-rate confidence bounds.
"""
from __future__ import annotations

import json
import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from learning.schemas import EdgeBucketKey, EdgeLedgerRecord, OutcomeRecord

logger = logging.getLogger("nzt48.learning.edge_ledger")

_DATA          = Path(__file__).parent.parent / "data"
_OUTCOMES      = _DATA / "outcomes.jsonl"
_EDGE_LEDGER   = _DATA / "edge_ledger.json"
_WEEKLY_DELTA  = _DATA / "edge_weekly_delta.json"

MIN_SAMPLE_ACTIONABLE     = 20
MIN_SAMPLE_CALIBRATE      = 10


def _wilson_ci(n_successes: int, n_total: int, z: float = 1.645) -> tuple[float, float]:
    """Wilson score interval for binomial proportion. z=1.645 for 90% CI."""
    if n_total == 0:
        return 0.0, 1.0
    p = n_successes / n_total
    denom = 1 + z**2 / n_total
    centre = (p + z**2 / (2 * n_total)) / denom
    spread = (z * math.sqrt(p * (1 - p) / n_total + z**2 / (4 * n_total**2))) / denom
    return max(0.0, centre - spread), min(1.0, centre + spread)


def _confidence_score(n: int, stability: float) -> float:
    """0-1 confidence: grows with sample size, penalised by instability."""
    size_factor = min(1.0, math.log1p(n) / math.log1p(100))
    return round(size_factor * stability, 3)


class EdgeLedger:
    """Computes and caches edge statistics per bucket."""

    def load_outcomes(self, days_back: Optional[int] = None) -> list[OutcomeRecord]:
        records = []
        if not _OUTCOMES.exists():
            return records
        cutoff = None
        if days_back is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        try:
            with open(_OUTCOMES) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    d = json.loads(line)
                    r = OutcomeRecord.from_dict(d)
                    if r.outcome not in ("HIT_TARGET", "HIT_STOP", "TIME_STOP"):
                        continue
                    if cutoff:
                        try:
                            ts = datetime.fromisoformat(r.closed_at.replace("Z", "+00:00"))
                            if ts < cutoff:
                                continue
                        except Exception:
                            pass
                    records.append(r)
        except Exception as e:
            logger.error(f"Error loading outcomes: {e}")
        return records

    def compute(self, records: Optional[list[OutcomeRecord]] = None) -> dict[str, EdgeLedgerRecord]:
        if records is None:
            records = self.load_outcomes()

        buckets: dict[str, list] = defaultdict(list)
        for r in records:
            key = EdgeBucketKey(
                strategy_tag     = r.strategy_tag,
                regime_tag       = r.regime_tag,
                track            = r.track,
                time_window      = r.time_window,
                liquidity_bucket = "NORMAL",
            ).to_str()
            buckets[key].append(r)

        result: dict[str, EdgeLedgerRecord] = {}
        now_str = datetime.now(timezone.utc).isoformat()

        for key, bucket_records in buckets.items():
            wins       = sum(1 for r in bucket_records if r.outcome == "HIT_TARGET")
            n          = len(bucket_records)
            win_rate   = wins / n if n > 0 else 0.0
            lo, hi     = _wilson_ci(wins, n)
            rr_vals    = [r.pnl_r_net for r in bucket_records if r.outcome in ("HIT_TARGET", "HIT_STOP", "TIME_STOP")]
            avg_rr_net = sum(rr_vals) / len(rr_vals) if rr_vals else 0.0
            avg_rr_gross = sum(r.pnl_r_gross for r in bucket_records) / n if n > 0 else 0.0
            avg_dur    = sum(r.duration_minutes for r in bucket_records) / n if n > 0 else 0.0
            expectancy = win_rate * avg_rr_net - (1 - win_rate) * 1.0

            # Max loss streak
            streak = cur_streak = 0
            for r in sorted(bucket_records, key=lambda x: x.closed_at):
                if r.outcome != "HIT_TARGET":
                    cur_streak += 1
                    streak = max(streak, cur_streak)
                else:
                    cur_streak = 0

            # Stability proxy: compare first/second half win rates
            if n >= 6:
                mid = n // 2
                first_wr  = sum(1 for r in bucket_records[:mid] if r.outcome == "HIT_TARGET") / mid
                second_wr = sum(1 for r in bucket_records[mid:] if r.outcome == "HIT_TARGET") / (n - mid)
                stability = max(0.0, 1.0 - abs(first_wr - second_wr) * 2)
            else:
                stability = 0.5

            status = (
                "ACTIONABLE"        if n >= MIN_SAMPLE_ACTIONABLE and expectancy > 0 else
                "CALIBRATION_READY" if n >= MIN_SAMPLE_CALIBRATE else
                "NEEDS_DATA"
            )

            result[key] = EdgeLedgerRecord(
                key              = key,
                trades_count     = n,
                win_rate         = round(win_rate, 4),
                win_rate_low     = round(lo, 4),
                win_rate_high    = round(hi, 4),
                avg_rr_gross     = round(avg_rr_gross, 4),
                avg_rr_net       = round(avg_rr_net, 4),
                avg_duration_min = round(avg_dur, 1),
                max_loss_streak  = streak,
                expectancy_net   = round(expectancy, 4),
                confidence_score = _confidence_score(n, stability),
                last_updated     = now_str,
                status           = status,
            )

        return result

    def save(self, ledger: dict[str, EdgeLedgerRecord]) -> None:
        out = {k: v.to_dict() for k, v in ledger.items()}
        _EDGE_LEDGER.write_text(json.dumps(out, indent=2))
        logger.info(f"Edge ledger saved: {len(out)} buckets")

    def load(self) -> dict[str, EdgeLedgerRecord]:
        if not _EDGE_LEDGER.exists():
            return {}
        try:
            raw = json.loads(_EDGE_LEDGER.read_text())
            return {k: EdgeLedgerRecord(**v) for k, v in raw.items()}
        except Exception:
            return {}

    def rebuild(self) -> dict:
        """Full rebuild of edge ledger. Returns summary."""
        records = self.load_outcomes()
        ledger  = self.compute(records)
        self.save(ledger)
        self._save_weekly_delta(records)
        return {
            "buckets": len(ledger),
            "total_outcomes": len(records),
            "actionable": sum(1 for v in ledger.values() if v.status == "ACTIONABLE"),
        }

    def _save_weekly_delta(self, all_records: list[OutcomeRecord]) -> None:
        """Compare last 7 days vs prior 7 days per bucket."""
        now = datetime.now(timezone.utc)
        cutoff_7  = now - timedelta(days=7)
        cutoff_14 = now - timedelta(days=14)

        def ts(r):
            try:
                return datetime.fromisoformat(r.closed_at.replace("Z", "+00:00"))
            except Exception:
                return now

        last_7  = [r for r in all_records if ts(r) >= cutoff_7]
        prior_7 = [r for r in all_records if cutoff_14 <= ts(r) < cutoff_7]

        ledger_last  = self.compute(last_7)
        ledger_prior = self.compute(prior_7)

        delta = {}
        for key, curr in ledger_last.items():
            prior = ledger_prior.get(key)
            if prior:
                delta[key] = {
                    "win_rate_delta":   round(curr.win_rate - prior.win_rate, 4),
                    "expectancy_delta": round(curr.expectancy_net - prior.expectancy_net, 4),
                    "trend": (
                        "IMPROVING" if curr.expectancy_net > prior.expectancy_net else
                        "DECAYING"  if curr.expectancy_net < prior.expectancy_net - 0.05 else
                        "STABLE"
                    ),
                    "current_n":  curr.trades_count,
                    "prior_n":    prior.trades_count,
                }
            else:
                delta[key] = {
                    "win_rate_delta": 0.0,
                    "expectancy_delta": 0.0,
                    "trend": "NEW",
                    "current_n": curr.trades_count,
                    "prior_n": 0,
                }

        _WEEKLY_DELTA.write_text(json.dumps(delta, indent=2))


# Module singleton
_ledger_instance: EdgeLedger | None = None

def get_edge_ledger() -> EdgeLedger:
    global _ledger_instance
    if _ledger_instance is None:
        _ledger_instance = EdgeLedger()
    return _ledger_instance
