"""
J-06: Weekly Gate Rejection Audit.
If a gate rejects >30% of signals AND >50% of rejected signals
would have been profitable, flag for threshold adjustment.
"""
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class GateRejection:
    timestamp: datetime
    ticker: str
    gate_name: str
    reason: str
    signal_confidence: float
    would_have_been_profitable: Optional[bool] = None  # filled in later
    eod_return_pct: Optional[float] = None


@dataclass
class GateAuditResult:
    gate_name: str
    total_signals: int
    rejections: int
    rejection_rate: float
    profitable_rejections: int
    profitable_rejection_rate: float
    needs_review: bool
    recommendation: str


class GateAuditor:
    """Tracks gate rejections and audits whether gates are too tight.

    Weekly audit: if gate rejects >30% AND >50% profitable -> flag.
    """

    REJECTION_THRESHOLD = 0.30
    PROFITABLE_THRESHOLD = 0.50

    def __init__(self):
        self._rejections: List[GateRejection] = []
        self._total_signals_by_gate: Dict[str, int] = defaultdict(int)
        self._audit_results: List[GateAuditResult] = []

    def record_pass(self, gate_name: str) -> None:
        self._total_signals_by_gate[gate_name] += 1

    def record_rejection(self, gate_name: str, ticker: str, reason: str,
                        confidence: float) -> None:
        self._total_signals_by_gate[gate_name] += 1
        self._rejections.append(GateRejection(
            timestamp=datetime.utcnow(), ticker=ticker,
            gate_name=gate_name, reason=reason,
            signal_confidence=confidence,
        ))

    def backfill_outcomes(self, ticker: str, date: datetime,
                         eod_return_pct: float) -> int:
        """Backfill whether rejected signals would have been profitable."""
        count = 0
        for r in self._rejections:
            if (r.ticker == ticker and r.timestamp.date() == date.date()
                    and r.would_have_been_profitable is None):
                r.eod_return_pct = eod_return_pct
                r.would_have_been_profitable = eod_return_pct > 0
                count += 1
        return count

    def run_audit(self, lookback_days: int = 7) -> List[GateAuditResult]:
        cutoff = datetime.utcnow() - timedelta(days=lookback_days)
        recent = [r for r in self._rejections if r.timestamp >= cutoff]

        gates = set(r.gate_name for r in recent)
        results = []

        for gate in gates:
            gate_rejections = [r for r in recent if r.gate_name == gate]
            total = self._total_signals_by_gate.get(gate, len(gate_rejections))
            rejection_rate = len(gate_rejections) / total if total > 0 else 0

            with_outcomes = [r for r in gate_rejections
                           if r.would_have_been_profitable is not None]
            profitable = [r for r in with_outcomes if r.would_have_been_profitable]
            profitable_rate = len(profitable) / len(with_outcomes) if with_outcomes else 0

            needs_review = (rejection_rate > self.REJECTION_THRESHOLD
                          and profitable_rate > self.PROFITABLE_THRESHOLD)

            if needs_review:
                rec = f"REVIEW: {gate} rejects {rejection_rate:.0%} of signals, " \
                      f"{profitable_rate:.0%} would have been profitable"
                logger.warning(rec)
            else:
                rec = "OK"

            result = GateAuditResult(
                gate_name=gate, total_signals=total,
                rejections=len(gate_rejections), rejection_rate=rejection_rate,
                profitable_rejections=len(profitable),
                profitable_rejection_rate=profitable_rate,
                needs_review=needs_review, recommendation=rec,
            )
            results.append(result)

        self._audit_results = results
        return results

    def get_summary(self) -> str:
        if not self._audit_results:
            return "No audit results available. Run run_audit() first."
        lines = ["Gate Rejection Audit:"]
        for r in sorted(self._audit_results, key=lambda x: x.rejection_rate, reverse=True):
            flag = " REVIEW" if r.needs_review else ""
            lines.append(f"  {r.gate_name}: {r.rejection_rate:.0%} rejected, "
                        f"{r.profitable_rejection_rate:.0%} profitable{flag}")
        return "\n".join(lines)
