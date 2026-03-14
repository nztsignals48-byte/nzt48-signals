"""
risk_officer/officer.py
========================
Post-router, pre-artifact governance layer.

Calling convention (in engine.py after router enrichment, before artifact write):

    officer = RiskOfficer()
    evaluated = officer.evaluate(cards, router_result, features_map, context)
    for card, decision in evaluated:
        card.risk_officer_decision = decision.decision
        card.risk_officer_reasons  = decision.reasons
        card.sizing_hint           = decision.final_sizing
        card.risk_adjustment_factor = decision.risk_score

RiskOfficer decisions:
  APPROVE  — signal passes all rules as-is
  DOWNSIZE — signal admitted but sizing reduced to S (or M if was L)
  VETO     — signal blocked; will be excluded from top_plays
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("nzt48.risk_officer")

# Decision constants
APPROVE  = "APPROVE"
DOWNSIZE = "DOWNSIZE"
VETO     = "VETO"


@dataclass
class RiskDecision:
    decision:         str            # APPROVE / DOWNSIZE / VETO
    reasons:          list[str]
    original_sizing:  str            # S / M / L from SignalCard.sizing_hint
    final_sizing:     str            # S / M / L after officer ruling
    rules_checked:    list[str]      # names of rules that fired
    risk_score:       float = 0.0   # 0-1 aggregate risk severity (0=safe, 1=reject)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RiskOfficerReport:
    """One report per session run — attached to artifact and surfaced in War Room."""
    session:        str
    generated_at:   str
    decisions:      list[dict]  = field(default_factory=list)
    veto_count:     int         = 0
    downsize_count: int         = 0
    approve_count:  int         = 0

    def to_dict(self) -> dict:
        return asdict(self)


class RiskOfficer:
    """
    Post-router governance layer.
    Instantiate once per engine.run() call.
    All rules are loaded fresh each call to avoid state leakage.
    """

    def __init__(self) -> None:
        from risk_officer.rules.vol_shock        import VolShockRule
        from risk_officer.rules.liquidity        import LiquidityRule
        from risk_officer.rules.correlation      import CorrelationRule
        from risk_officer.rules.drawdown         import DrawdownRule
        from risk_officer.rules.event_window     import EventWindowRule
        from risk_officer.rules.data_reliability import DataReliabilityRule

        self._stateless_rules = [
            VolShockRule(),
            LiquidityRule(),
            DrawdownRule(),
            EventWindowRule(),
            DataReliabilityRule(),
        ]
        # CorrelationRule is stateful — tracks factor_group counts per evaluate() call
        self._correlation_rule = CorrelationRule()

    def evaluate(
        self,
        cards:         list,          # list[SignalCard]
        router_result: object,        # RouterResult (or None)
        features_map:  dict,          # ticker -> TickerFeatures (or {})
        context:       dict = None,   # {"vix": float, "consecutive_losses": int, ...}
    ) -> list[tuple]:
        """
        Returns list[(card, RiskDecision)] — one entry per input card.
        Cards vetoed are still returned (caller decides whether to exclude them).
        """
        ctx = context or {}
        self._correlation_rule.reset()
        results = []

        for card in cards:
            features = features_map.get(getattr(card, "ticker", ""), None)
            decision = self._apply_rules(card, router_result, features, ctx)
            results.append((card, decision))

        n_approve  = sum(1 for _, d in results if d.decision == APPROVE)
        n_downsize = sum(1 for _, d in results if d.decision == DOWNSIZE)
        n_veto     = sum(1 for _, d in results if d.decision == VETO)
        logger.info(
            "[RISK_OFFICER] evaluate: total=%d approve=%d downsize=%d veto=%d",
            len(cards), n_approve, n_downsize, n_veto,
        )
        return results

    def _apply_rules(self, card, router_result, features, context: dict) -> RiskDecision:
        """
        Run each rule in order; aggregate using worst-wins logic.
        VETO beats DOWNSIZE beats APPROVE.
        """
        worst = RiskDecision(
            decision=APPROVE,
            reasons=[],
            original_sizing=getattr(card, "sizing_hint", "M"),
            final_sizing=getattr(card, "sizing_hint", "M"),
            rules_checked=[],
            risk_score=0.0,
        )

        all_rules = self._stateless_rules + [self._correlation_rule]

        for rule in all_rules:
            try:
                result = rule.check(card, router_result, features, context)
                if result is None:
                    worst.rules_checked.append(rule.NAME + ":pass")
                    continue
                worst.rules_checked.append(rule.NAME + ":" + result.decision)
                worst.reasons.extend(result.reasons)
                worst.risk_score = max(worst.risk_score, result.risk_score)

                # Worst-wins escalation
                if result.decision == VETO:
                    worst.decision = VETO
                    worst.final_sizing = "S"
                elif result.decision == DOWNSIZE and worst.decision != VETO:
                    worst.decision = DOWNSIZE
                    # Downsize: L->M->S
                    _sizing_map = {"L": "M", "M": "S", "S": "S"}
                    current = worst.final_sizing
                    worst.final_sizing = _sizing_map.get(result.final_sizing, "S")

            except Exception as exc:
                logger.debug("[RISK_OFFICER] rule %s failed (non-fatal): %s", rule.NAME, exc)
                worst.rules_checked.append(rule.NAME + ":error")

        return worst

    def build_report(
        self,
        session: str,
        evaluated: list[tuple],
    ) -> RiskOfficerReport:
        """Build a summary RiskOfficerReport from evaluate() results."""
        decisions = []
        veto_count = downsize_count = approve_count = 0
        for card, dec in evaluated:
            decisions.append({
                "ticker":           getattr(card, "ticker", "?"),
                "direction":        getattr(card, "direction", "?"),
                "decision":         dec.decision,
                "reasons":          dec.reasons,
                "original_sizing":  dec.original_sizing,
                "final_sizing":     dec.final_sizing,
                "risk_score":       round(dec.risk_score, 3),
                "rules_checked":    dec.rules_checked,
            })
            if dec.decision == APPROVE:
                approve_count += 1
            elif dec.decision == DOWNSIZE:
                downsize_count += 1
            else:
                veto_count += 1

        return RiskOfficerReport(
            session=session,
            generated_at=datetime.now(timezone.utc).isoformat(),
            decisions=decisions,
            veto_count=veto_count,
            downsize_count=downsize_count,
            approve_count=approve_count,
        )
