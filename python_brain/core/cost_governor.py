"""Cost governor. Hard-cap $15/day LLM spend, warn at $12.

Phase 6 fills real token counting + daily reset at session close.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict

@dataclass
class CostGovernor:
    daily_cap_usd: float = 15.0
    warning_usd: float = 12.0
    phase: str = "haiku_only"   # haiku_only | ensemble | full
    today: date = field(default_factory=date.today)
    spend_usd: float = 0.0
    per_agent_usd: Dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_defaults(cls) -> "CostGovernor":
        return cls()

    def reset_if_new_day(self) -> None:
        today = date.today()
        if today != self.today:
            self.today = today
            self.spend_usd = 0.0
            self.per_agent_usd = {}

    def can_spend(self, agent: str, amount_usd: float) -> bool:
        self.reset_if_new_day()
        return (self.spend_usd + amount_usd) <= self.daily_cap_usd

    def record(self, agent: str, amount_usd: float) -> None:
        self.spend_usd += amount_usd
        self.per_agent_usd[agent] = self.per_agent_usd.get(agent, 0.0) + amount_usd

    def is_warning(self) -> bool:
        return self.spend_usd >= self.warning_usd

    def is_capped(self) -> bool:
        return self.spend_usd >= self.daily_cap_usd
