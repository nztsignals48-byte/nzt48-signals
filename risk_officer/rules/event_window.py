"""
risk_officer/rules/event_window.py
=====================================
Stub: always APPROVE until earnings_adapter is wired.
When adapter is active:
  VETO if earnings within 1 day AND fallback_step > 0 (weak signal near event).
  DOWNSIZE if earnings within 2 days.
"""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from risk_officer.officer import RiskDecision

NAME = "EVENT_WINDOW"

_VETO_DAYS_THRESHOLD     = 1
_DOWNSIZE_DAYS_THRESHOLD = 2


class EventWindowRule:
    NAME = "EVENT_WINDOW"

    def check(self, card, router_result, features, context: dict) -> Optional["RiskDecision"]:
        from risk_officer.officer import RiskDecision, APPROVE, DOWNSIZE, VETO

        # Try to get earnings event data
        try:
            from signal_engine.adapters.earnings_adapter import EarningsAdapter
            adapter = EarningsAdapter()
            if not adapter.is_available():
                # Stub mode — always approve, record adapter status
                return None

            event = adapter.get_upcoming(card.ticker, days_ahead=_DOWNSIZE_DAYS_THRESHOLD)
            if event is None:
                return None

            fallback_step = getattr(card, "fallback_step", 0)

            if event.days_until <= _VETO_DAYS_THRESHOLD and fallback_step > 0:
                return RiskDecision(
                    decision=VETO,
                    reasons=[
                        f"Earnings in {event.days_until}d for {card.ticker} — "
                        f"fallback signal near event window is too risky"
                    ],
                    original_sizing=card.sizing_hint,
                    final_sizing="S",
                    rules_checked=[self.NAME],
                    risk_score=0.80,
                )
            if event.days_until <= _DOWNSIZE_DAYS_THRESHOLD:
                return RiskDecision(
                    decision=DOWNSIZE,
                    reasons=[
                        f"Earnings in {event.days_until}d for {card.ticker} — "
                        f"reduce size before event window"
                    ],
                    original_sizing=card.sizing_hint,
                    final_sizing="S",
                    rules_checked=[self.NAME],
                    risk_score=0.45,
                )
        except Exception:
            pass

        return None
