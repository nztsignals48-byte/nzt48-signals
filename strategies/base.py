"""Base class for all NZT-48 strategies."""
from __future__ import annotations
import logging
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from models import Signal, IndicatorSnapshot, MarketContext, SectorFlow, NarrativeContext

class StrategyBase(ABC):
    """Base class for all 14 NZT-48 strategies.
    Each strategy is a self-contained module with a scan() method."""

    def __init__(self, name: str, strategy_id: str):
        self.name = name
        self.strategy_id = strategy_id
        self.logger = logging.getLogger(f"nzt48.strategy.{strategy_id}")
        self.enabled = True

    @abstractmethod
    def scan(
        self,
        tickers: list[str],
        indicators: dict[str, IndicatorSnapshot],
        market_ctx: MarketContext,
        sector_flows: dict[str, SectorFlow],
        narratives: dict[str, NarrativeContext],
    ) -> list[Signal]:
        """Scan for trade signals across all tickers.
        Returns list of Signal objects that pass this strategy's filters."""
        ...

    def _create_signal(self, ticker: str, direction: str, entry: float,
                       stop: float, indicators: IndicatorSnapshot,
                       market_ctx: MarketContext) -> Signal:
        """Helper to create a properly populated Signal object."""
        from models import Direction, Bot, SignalStatus, Strategy
        import uuid
        signal = Signal(
            id=str(uuid.uuid4())[:12],
            ticker=ticker,
            direction=Direction.LONG if direction == "LONG" else Direction.SHORT,
            strategy=self.strategy_id,
            entry=entry,
            stop=stop,
            regime=market_ctx.regime,
            gex_regime=market_ctx.gex_regime,
            rvol=indicators.rvol,
            time_window=market_ctx.time_window,
            patterns_detected=indicators.patterns_detected,
            internals_composite=market_ctx.internals_composite,
        )
        return signal
