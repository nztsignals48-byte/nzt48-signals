"""
risk_officer/rules/liquidity.py
================================
VETO if rvol < 0.15 (no liquidity to get out cleanly).
DOWNSIZE if rvol 0.40-0.60.
Checks spread_proxy_bps if available in execution_plan.
"""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from risk_officer.officer import RiskDecision

NAME = "LIQUIDITY"

_RVOL_VETO_THRESHOLD     = 0.15
_RVOL_DOWNSIZE_THRESHOLD = 0.60
_SPREAD_VETO_BPS         = 30.0
_SPREAD_DOWNSIZE_BPS     = 22.0


class LiquidityRule:
    NAME = "LIQUIDITY"

    def check(self, card, router_result, features, context: dict) -> Optional["RiskDecision"]:
        from risk_officer.officer import RiskDecision, APPROVE, DOWNSIZE, VETO

        rvol = getattr(card, "rvol", None)
        execution_plan = getattr(card, "execution_plan", {}) or {}
        spread_bps = execution_plan.get("spread_proxy_bps", 10.0)

        reasons = []
        worst_decision = APPROVE
        risk_score = 0.0

        # RVOL checks
        if rvol is not None:
            if rvol < _RVOL_VETO_THRESHOLD:
                reasons.append(f"RVOL={rvol:.2f}x < {_RVOL_VETO_THRESHOLD} — insufficient liquidity to exit cleanly")
                worst_decision = VETO
                risk_score = max(risk_score, 0.90)
            elif rvol < _RVOL_DOWNSIZE_THRESHOLD:
                reasons.append(f"RVOL={rvol:.2f}x < {_RVOL_DOWNSIZE_THRESHOLD} — low liquidity, downsize")
                worst_decision = DOWNSIZE
                risk_score = max(risk_score, 0.45)

        # Spread checks
        if spread_bps > _SPREAD_VETO_BPS:
            reasons.append(f"Spread proxy={spread_bps:.1f}bps > {_SPREAD_VETO_BPS}bps — transaction cost destroys edge")
            worst_decision = VETO
            risk_score = max(risk_score, 0.85)
        elif spread_bps > _SPREAD_DOWNSIZE_BPS:
            reasons.append(f"Spread proxy={spread_bps:.1f}bps > {_SPREAD_DOWNSIZE_BPS}bps — wide spread, downsize")
            if worst_decision != VETO:
                worst_decision = DOWNSIZE
            risk_score = max(risk_score, 0.40)

        if worst_decision == APPROVE:
            return None

        final_sizing = "S" if worst_decision == VETO else (
            "S" if card.sizing_hint == "L" else card.sizing_hint
        )
        return RiskDecision(
            decision=worst_decision,
            reasons=reasons,
            original_sizing=card.sizing_hint,
            final_sizing=final_sizing,
            rules_checked=[self.NAME],
            risk_score=risk_score,
        )
