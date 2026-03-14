"""
risk_officer/rules/correlation.py
===================================
Factor overload guard.
If 3+ signals share the same factor_group, DOWNSIZE the 3rd+ entries.
Uses RouterResult.max_factor_cap as the threshold.
This rule is STATEFUL per evaluate() call (tracks factor_group counts).
"""
from __future__ import annotations
from collections import defaultdict
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from risk_officer.officer import RiskDecision

NAME = "CORRELATION"


class CorrelationRule:
    """
    Must be instantiated fresh per officer.evaluate() call
    because it holds per-run state (_factor_counts).
    """
    NAME = "CORRELATION"

    def __init__(self):
        self._factor_counts: dict[str, int] = defaultdict(int)

    def reset(self):
        self._factor_counts = defaultdict(int)

    def check(self, card, router_result, features, context: dict) -> Optional["RiskDecision"]:
        from risk_officer.officer import RiskDecision, APPROVE, DOWNSIZE, VETO

        factor_group = getattr(card, "factor_group", None) or ""
        max_cap = getattr(router_result, "max_factor_cap", 3) if router_result else 3

        if not factor_group:
            return None

        self._factor_counts[factor_group] += 1
        count = self._factor_counts[factor_group]

        if count > max_cap:
            return RiskDecision(
                decision=VETO,
                reasons=[
                    f"Factor overload: {count} signals in '{factor_group}' group — cap={max_cap}. "
                    f"Adding more concentration risk exceeds guardrail."
                ],
                original_sizing=card.sizing_hint,
                final_sizing="S",
                rules_checked=[self.NAME],
                risk_score=0.75,
            )
        if count == max_cap:
            return RiskDecision(
                decision=DOWNSIZE,
                reasons=[
                    f"Factor cap reached: {count}/{max_cap} signals in '{factor_group}' — downsize final entry"
                ],
                original_sizing=card.sizing_hint,
                final_sizing="S",
                rules_checked=[self.NAME],
                risk_score=0.45,
            )

        return None
