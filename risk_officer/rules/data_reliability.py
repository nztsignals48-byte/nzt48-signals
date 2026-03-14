"""
risk_officer/rules/data_reliability.py
========================================
VETO if data_reliability < 0.50.
DOWNSIZE if data_reliability < 0.70 (includes SHORT_WINDOW penalty).
Also checks: short_window=True with fallback_step > 2 -> DOWNSIZE.
"""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from risk_officer.officer import RiskDecision

NAME = "DATA_RELIABILITY"

_VETO_THRESHOLD     = 0.50
_DOWNSIZE_THRESHOLD = 0.70


class DataReliabilityRule:
    NAME = "DATA_RELIABILITY"

    def check(self, card, router_result, features, context: dict) -> Optional["RiskDecision"]:
        from risk_officer.officer import RiskDecision, APPROVE, DOWNSIZE, VETO

        reliability    = getattr(card, "data_reliability", 1.0)
        short_window   = getattr(card, "short_window", False)
        fallback_step  = getattr(card, "fallback_step", 0)
        bars_available = getattr(card, "bars_available", 14)

        reasons = []
        worst_decision = APPROVE
        risk_score = 0.0

        if reliability < _VETO_THRESHOLD:
            reasons.append(
                f"data_reliability={reliability:.2f} < {_VETO_THRESHOLD} — "
                f"indicator quality too low, signal fabrication risk"
            )
            worst_decision = VETO
            risk_score = max(risk_score, 0.88)
        elif reliability < _DOWNSIZE_THRESHOLD:
            reasons.append(
                f"data_reliability={reliability:.2f} < {_DOWNSIZE_THRESHOLD} — "
                f"reduced quality data, downsize"
            )
            worst_decision = DOWNSIZE
            risk_score = max(risk_score, 0.40)

        if short_window and fallback_step > 2:
            reasons.append(
                f"SHORT_WINDOW ({bars_available} bars) + fallback_step={fallback_step} — "
                f"double relaxation stacked, downsize"
            )
            if worst_decision != VETO:
                worst_decision = DOWNSIZE
            risk_score = max(risk_score, 0.50)

        if short_window and bars_available < 10:
            reasons.append(
                f"Only {bars_available} bars available — ATR/RSI may be unreliable"
            )
            if worst_decision == APPROVE:
                worst_decision = DOWNSIZE
            risk_score = max(risk_score, 0.35)

        if worst_decision == APPROVE:
            return None

        final_sizing = "S"
        return RiskDecision(
            decision=worst_decision,
            reasons=reasons,
            original_sizing=card.sizing_hint,
            final_sizing=final_sizing,
            rules_checked=[self.NAME],
            risk_score=risk_score,
        )
