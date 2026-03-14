"""
risk_officer/rules/vol_shock.py
================================
VETO if VIX > 35 AND atr_pct > 3.5%.
DOWNSIZE if VIX 25-35 AND atr_pct > 2.5%.
Fast-path: uses router_result.kill_switch.
"""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from risk_officer.officer import RiskDecision

NAME = "VOL_SHOCK"

_VIX_VETO_THRESHOLD     = 35.0
_VIX_DOWNSIZE_THRESHOLD = 25.0
_ATR_VETO_THRESHOLD     = 3.5
_ATR_DOWNSIZE_THRESHOLD = 2.5


class VolShockRule:
    NAME = "VOL_SHOCK"

    def check(self, card, router_result, features, context: dict) -> Optional["RiskDecision"]:
        from risk_officer.officer import RiskDecision, APPROVE, DOWNSIZE, VETO

        # Fast path: kill switch already set by router
        if router_result and getattr(router_result, "kill_switch", False):
            return RiskDecision(
                decision=VETO,
                reasons=["Kill switch active (SHOCK regime / VIX spike)"],
                original_sizing=card.sizing_hint,
                final_sizing="S",
                rules_checked=[self.NAME],
                risk_score=1.0,
            )

        vix = context.get("vix", 0.0) if context else 0.0
        atr_pct = getattr(card, "atr_pct", 0.0)

        if vix > _VIX_VETO_THRESHOLD and atr_pct > _ATR_VETO_THRESHOLD:
            return RiskDecision(
                decision=VETO,
                reasons=[f"VIX={vix:.1f} > {_VIX_VETO_THRESHOLD} AND ATR%={atr_pct:.2f}% > {_ATR_VETO_THRESHOLD}% — compound decay risk too high"],
                original_sizing=card.sizing_hint,
                final_sizing="S",
                rules_checked=[self.NAME],
                risk_score=0.95,
            )

        if vix > _VIX_DOWNSIZE_THRESHOLD and atr_pct > _ATR_DOWNSIZE_THRESHOLD:
            return RiskDecision(
                decision=DOWNSIZE,
                reasons=[f"VIX={vix:.1f} > {_VIX_DOWNSIZE_THRESHOLD} AND ATR%={atr_pct:.2f}% elevated — downsize to S"],
                original_sizing=card.sizing_hint,
                final_sizing="S",
                rules_checked=[self.NAME],
                risk_score=0.55,
            )

        # Check router overlay sizing_mode
        sizing_mode = getattr(router_result, "sizing_mode", "NORMAL") if router_result else "NORMAL"
        if sizing_mode == "DEFENSIVE":
            return RiskDecision(
                decision=DOWNSIZE,
                reasons=["Router sizing_mode=DEFENSIVE (VOL_TARGET overlay) — downsize to S"],
                original_sizing=card.sizing_hint,
                final_sizing="S",
                rules_checked=[self.NAME],
                risk_score=0.40,
            )
        if sizing_mode == "REDUCED" and card.sizing_hint == "L":
            return RiskDecision(
                decision=DOWNSIZE,
                reasons=["Router sizing_mode=REDUCED — cap L -> M"],
                original_sizing=card.sizing_hint,
                final_sizing="M",
                rules_checked=[self.NAME],
                risk_score=0.25,
            )

        return None
