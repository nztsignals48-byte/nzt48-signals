"""Base strategy interface. Every MVP strategy implements this.

Dataset-contract rule: every StrategyView returned must carry enough in `features`
to populate the WAL SignalReceived schema in full. Missing fields => strategy blocked.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class StrategyContext:
    ticker: str
    timestamp_ns: int
    bars: Dict[str, list]
    indicators: Dict[str, float]
    quant: Dict[str, float]
    regime_probs: list
    intel: Dict[str, dict]
    portfolio: Dict[str, float]


@dataclass
class StrategyView:
    strategy: str
    ticker: str
    default_conviction: float
    edge_estimate_bps: float
    risk_bps: float
    features: Dict[str, float]
    required_intel: list[str]


class Strategy(ABC):
    name: str = "base"
    required_intel: list[str] = []
    exit_method: str = "ChandelierStop"

    @abstractmethod
    def evaluate(self, ctx: StrategyContext) -> Optional[StrategyView]: ...
