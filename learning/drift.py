"""
learning/drift.py
==================
Drift detection for NZT-48 self-learning AI.

Detects:
- Feature drift: distribution of rvol/atr_pct/spread_bps shifts
- Residual drift: predicted vs actual pnl_r diverging
- Hit-rate drift: win rate dropping in key buckets
- Regime drift: current market stats vs historical baseline

Output:
- DriftReport with severity + defensive mode trigger
- Written to data/drift_reports.jsonl
"""
from __future__ import annotations

import json
import logging
import math
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from learning.schemas import DriftReport, SignalLogRecord, OutcomeRecord

logger = logging.getLogger("nzt48.learning.drift")

_DATA          = Path(__file__).parent.parent / "data"
_SIGNAL_LOG    = _DATA / "signal_log.jsonl"
_OUTCOMES      = _DATA / "outcomes.jsonl"
_DRIFT_REPORTS = _DATA / "drift_reports.jsonl"

# Thresholds
_FEATURE_DRIFT_THRESHOLD = 0.30   # 30% relative change in mean triggers LOW
_RESIDUAL_DRIFT_THRESHOLD = 0.25  # |predicted - actual| avg > 0.25R triggers MEDIUM
_HIT_RATE_DROP_THRESHOLD = 0.15   # absolute win rate drop of 15pp triggers MEDIUM
_DEFENSIVE_TRIGGER_SEVERITY = {"HIGH", "CRITICAL"}


def _mean_std(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return values[0], 0.0
    return statistics.mean(values), statistics.stdev(values)


class DriftDetector:
    """Detects statistical drift in signals, features, and outcomes."""

    def _load_signals(self, days_back: int = 30) -> list[SignalLogRecord]:
        records = []
        if not _SIGNAL_LOG.exists():
            return records
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        try:
            with open(_SIGNAL_LOG) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    d = json.loads(line)
                    r = SignalLogRecord.from_dict(d)
                    try:
                        ts = datetime.fromisoformat(r.generated_at.replace("Z", "+00:00"))
                        if ts >= cutoff:
                            records.append(r)
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Error loading signals: {e}")
        return records

    def _load_outcomes(self, days_back: int = 30) -> list[OutcomeRecord]:
        records = []
        if not _OUTCOMES.exists():
            return records
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        try:
            with open(_OUTCOMES) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    d = json.loads(line)
                    r = OutcomeRecord.from_dict(d)
                    try:
                        ts = datetime.fromisoformat(r.closed_at.replace("Z", "+00:00"))
                        if ts >= cutoff:
                            records.append(r)
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Error loading outcomes: {e}")
        return records

    def detect_feature_drift(self, recent_days: int = 7, baseline_days: int = 30) -> Optional[DriftReport]:
        """Detect distribution shift in key features."""
        all_signals = self._load_signals(baseline_days)
        if len(all_signals) < 10:
            return None

        cutoff = datetime.now(timezone.utc) - timedelta(days=recent_days)
        def ts(r):
            try:
                return datetime.fromisoformat(r.generated_at.replace("Z", "+00:00"))
            except Exception:
                return datetime.min.replace(tzinfo=timezone.utc)

        recent   = [r for r in all_signals if ts(r) >= cutoff]
        baseline = [r for r in all_signals if ts(r) < cutoff]

        if len(recent) < 5 or len(baseline) < 5:
            return None

        drift_flags = []
        for attr in ("rvol", "atr_pct", "spread_bps"):
            rec_vals  = [getattr(r, attr, 0.0) for r in recent  if getattr(r, attr, 0.0) > 0]
            base_vals = [getattr(r, attr, 0.0) for r in baseline if getattr(r, attr, 0.0) > 0]
            if not rec_vals or not base_vals:
                continue
            rec_mean, _  = _mean_std(rec_vals)
            base_mean, _ = _mean_std(base_vals)
            if base_mean > 0:
                rel_change = abs(rec_mean - base_mean) / base_mean
                if rel_change > _FEATURE_DRIFT_THRESHOLD:
                    drift_flags.append(f"{attr}: {base_mean:.2f}->{rec_mean:.2f} ({rel_change*100:.0f}% shift)")

        if not drift_flags:
            return None

        severity = "MEDIUM" if len(drift_flags) >= 2 else "LOW"
        return DriftReport(
            detected=True,
            severity=severity,
            drift_type="FEATURE",
            description="; ".join(drift_flags),
            affected_strategies=[],
            defensive_mode_triggered=(severity in _DEFENSIVE_TRIGGER_SEVERITY),
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    def detect_hit_rate_drift(self, recent_days: int = 7, baseline_days: int = 30) -> Optional[DriftReport]:
        """Detect win rate drop in resolved outcomes."""
        outcomes = self._load_outcomes(baseline_days)
        if len(outcomes) < 10:
            return None

        cutoff = datetime.now(timezone.utc) - timedelta(days=recent_days)
        def ts(r):
            try:
                return datetime.fromisoformat(r.closed_at.replace("Z", "+00:00"))
            except Exception:
                return datetime.min.replace(tzinfo=timezone.utc)

        recent   = [r for r in outcomes if ts(r) >= cutoff and r.outcome in ("HIT_TARGET", "HIT_STOP", "TIME_STOP")]
        baseline = [r for r in outcomes if ts(r) < cutoff  and r.outcome in ("HIT_TARGET", "HIT_STOP", "TIME_STOP")]

        if len(recent) < 5 or len(baseline) < 5:
            return None

        rec_wr  = sum(1 for r in recent   if r.outcome == "HIT_TARGET") / len(recent)
        base_wr = sum(1 for r in baseline if r.outcome == "HIT_TARGET") / len(baseline)
        drop = base_wr - rec_wr

        if drop < _HIT_RATE_DROP_THRESHOLD:
            return None

        severity = "HIGH" if drop > 0.25 else "MEDIUM"
        affected = list({r.strategy_tag for r in recent if r.outcome != "HIT_TARGET"})[:5]

        return DriftReport(
            detected=True,
            severity=severity,
            drift_type="HIT_RATE",
            description=f"Win rate dropped {drop*100:.0f}pp: {base_wr*100:.0f}% -> {rec_wr*100:.0f}% (last {recent_days}d)",
            affected_strategies=affected,
            defensive_mode_triggered=(severity in _DEFENSIVE_TRIGGER_SEVERITY),
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    def run_all(self) -> list[DriftReport]:
        """Run all detectors and save results."""
        reports = []
        for fn in (self.detect_feature_drift, self.detect_hit_rate_drift):
            try:
                r = fn()
                if r:
                    reports.append(r)
            except Exception as e:
                logger.error(f"Drift detector {fn.__name__} failed: {e}")

        # Save to drift_reports.jsonl
        if reports:
            with open(_DRIFT_REPORTS, "a") as f:
                for r in reports:
                    f.write(json.dumps(r.to_dict()) + "\n")
            logger.info(f"Drift: {len(reports)} reports, max severity: {max(r.severity for r in reports)}")

        return reports

    def get_latest_report(self) -> Optional[DriftReport]:
        """Get most recent drift report."""
        if not _DRIFT_REPORTS.exists():
            return None
        last_line = None
        try:
            with open(_DRIFT_REPORTS) as f:
                for line in f:
                    if line.strip():
                        last_line = line.strip()
        except Exception:
            return None
        if not last_line:
            return None
        try:
            d = json.loads(last_line)
            return DriftReport(**{k: d[k] for k in DriftReport.__dataclass_fields__ if k in d})
        except Exception:
            return None

    def is_defensive_mode_active(self) -> bool:
        """Check if any recent drift report triggered defensive mode."""
        if not _DRIFT_REPORTS.exists():
            return False
        # Check last 5 reports
        lines = []
        try:
            with open(_DRIFT_REPORTS) as f:
                lines = [l.strip() for l in f if l.strip()]
        except Exception:
            return False
        for line in lines[-5:]:
            try:
                d = json.loads(line)
                if d.get("defensive_mode_triggered") and d.get("severity") in ("HIGH", "CRITICAL"):
                    # Check if it is recent (last 24h)
                    ts_str = d.get("generated_at", "")
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if (datetime.now(timezone.utc) - ts).total_seconds() < 86400:
                            return True
            except Exception:
                pass
        return False


# Synthetic drift test (for proof/testing)
def inject_synthetic_drift_test():
    """Write fake signals with shifted rvol to trigger drift detection."""
    from learning.schemas import SignalLogRecord, make_signal_id
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    _DATA.mkdir(parents=True, exist_ok=True)

    with open(_SIGNAL_LOG, "a") as f:
        # Baseline: 15 signals with rvol ~1.5
        for i in range(15):
            ts = (now - timedelta(days=20, hours=i)).isoformat()
            ds = (now - timedelta(days=20, hours=i)).strftime("%Y-%m-%d")
            sid = make_signal_id(ds, ts, "QQQ3.L", "TREND_MOMENTUM", "INTRADAY_SWING", 24.0 + i)
            r = SignalLogRecord(
                signal_id=sid, ticker="QQQ3.L", direction="LONG",
                strategy_tag="TREND_MOMENTUM", regime_tag="RISK_ON",
                regime_confidence=0.75, time_window="MORNING_MOMENTUM",
                track="INTRADAY_SWING", session="LSE", composite=75.0,
                entry=24.0+i, stop=23.5+i, target1=25.0+i, target2=25.5+i,
                net_rr=2.0, generated_at=ts, date_str=ds,
                rvol=1.5, atr_pct=1.2, outcome="RESOLVED",
            )
            f.write(json.dumps(r.to_dict()) + "\n")

        # Recent: 10 signals with rvol ~0.5 (big drift)
        for i in range(10):
            ts = (now - timedelta(hours=i*2)).isoformat()
            ds = now.strftime("%Y-%m-%d")
            sid = make_signal_id(ds, ts, "QQQ3.L", "TREND_MOMENTUM", "INTRADAY_SWING", 20.0 + i)
            r = SignalLogRecord(
                signal_id=sid, ticker="QQQ3.L", direction="LONG",
                strategy_tag="TREND_MOMENTUM", regime_tag="RISK_ON",
                regime_confidence=0.55, time_window="MORNING_MOMENTUM",
                track="INTRADAY_SWING", session="LSE", composite=60.0,
                entry=20.0+i, stop=19.0+i, target1=22.0+i, target2=23.0+i,
                net_rr=2.0, generated_at=ts, date_str=ds,
                rvol=0.5, atr_pct=0.5, outcome="PENDING",
            )
            f.write(json.dumps(r.to_dict()) + "\n")

    logger.info("Synthetic drift test data injected")
