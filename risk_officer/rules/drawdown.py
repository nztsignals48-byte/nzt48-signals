"""
risk_officer/rules/drawdown.py
================================
Consecutive loss guardrail.
DOWNSIZE all to S if consecutive_losses >= 3.
VETO all if consecutive_losses >= 5.
Context key: "consecutive_losses" (int, default 0).
Also checks "daily_loss_pct" context key: VETO if daily_loss > 3%.
"""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from risk_officer.officer import RiskDecision

NAME = "DRAWDOWN"

# A-13: These thresholds are aligned with circuit_breakers.py, which is the
# sole authority for consecutive loss session halts. This rule provides a
# complementary VETO/DOWNSIZE signal to the risk officer pipeline.
_CONSEC_DOWNSIZE_THRESHOLD = 3
_CONSEC_VETO_THRESHOLD     = 5   # Matches circuit_breakers._CONSEC_LOSS_TIER_3
_DAILY_LOSS_VETO_PCT       = 3.0
_DAILY_LOSS_DOWNSIZE_PCT   = 1.5


class DrawdownRule:
    NAME = "DRAWDOWN"

    def check(self, card, router_result, features, context: dict) -> Optional["RiskDecision"]:
        from risk_officer.officer import RiskDecision, APPROVE, DOWNSIZE, VETO

        if not context:
            return None

        consecutive_losses = context.get("consecutive_losses", 0)
        daily_loss_pct     = context.get("daily_loss_pct", 0.0)

        reasons = []
        worst_decision = APPROVE
        risk_score = 0.0

        if consecutive_losses >= _CONSEC_VETO_THRESHOLD:
            reasons.append(
                f"Consecutive losses={consecutive_losses} >= {_CONSEC_VETO_THRESHOLD} "
                f"— drawdown guardrail active, no new signals"
            )
            worst_decision = VETO
            risk_score = max(risk_score, 0.92)
        elif consecutive_losses >= _CONSEC_DOWNSIZE_THRESHOLD:
            reasons.append(
                f"Consecutive losses={consecutive_losses} >= {_CONSEC_DOWNSIZE_THRESHOLD} "
                f"— reduce size until equity recovers"
            )
            worst_decision = DOWNSIZE
            risk_score = max(risk_score, 0.55)

        if daily_loss_pct > _DAILY_LOSS_VETO_PCT:
            reasons.append(
                f"Daily loss={daily_loss_pct:.2f}% > {_DAILY_LOSS_VETO_PCT}% "
                f"— daily loss limit hit, stop trading"
            )
            worst_decision = VETO
            risk_score = max(risk_score, 0.95)
        elif daily_loss_pct > _DAILY_LOSS_DOWNSIZE_PCT:
            reasons.append(
                f"Daily loss={daily_loss_pct:.2f}% > {_DAILY_LOSS_DOWNSIZE_PCT}% "
                f"— intraday drawdown elevated, downsize"
            )
            if worst_decision != VETO:
                worst_decision = DOWNSIZE
            risk_score = max(risk_score, 0.50)

        if worst_decision == APPROVE:
            return None

        final_sizing = "S" if worst_decision in (VETO, DOWNSIZE) else card.sizing_hint
        return RiskDecision(
            decision=worst_decision,
            reasons=reasons,
            original_sizing=card.sizing_hint,
            final_sizing=final_sizing,
            rules_checked=[self.NAME],
            risk_score=risk_score,
        )
