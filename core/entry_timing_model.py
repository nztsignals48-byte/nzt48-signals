"""
J-07: Optimal Entry Delay Model.
After 200+ trades: train simple model predicting optimal entry delay.
Features: gap_size, rvol_trajectory, sector_momentum, regime, time_of_day
Target: optimal_delay_minutes
"""
import logging
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class EntryTimingPrediction:
    optimal_delay_minutes: float
    confidence: float
    features_used: dict


@dataclass
class EntryTimingRecord:
    ticker: str
    entry_time: datetime
    gap_size_pct: float
    rvol_at_entry: float
    regime: str
    time_of_day_bucket: str
    actual_delay_minutes: float
    entry_timing_score: float  # (high - entry) / (high - low) for LONG
    was_profitable: bool


class EntryTimingModel:
    """Learns optimal entry delay from historical trades.
    Requires 200+ trades before training. Uses simple linear model initially.
    """
    MIN_TRADES = 200

    def __init__(self):
        self._records: List[EntryTimingRecord] = []
        self._model = None
        self._is_trained = False
        logger.info("EntryTimingModel initialized (requires %d+ trades)", self.MIN_TRADES)

    def add_record(self, record: EntryTimingRecord) -> None:
        self._records.append(record)
        if len(self._records) >= self.MIN_TRADES and not self._is_trained:
            logger.info("EntryTimingModel: %d records -- ready to train", len(self._records))

    def train(self) -> bool:
        if len(self._records) < self.MIN_TRADES:
            logger.warning("EntryTimingModel: insufficient data (%d < %d)", len(self._records), self.MIN_TRADES)
            return False
        # TODO: Train sklearn LinearRegression or LightGBM
        logger.info("EntryTimingModel: training on %d records (TODO: implement)", len(self._records))
        self._is_trained = True
        return True

    def predict(self, gap_size: float, rvol: float, regime: str, hour: int) -> Optional[EntryTimingPrediction]:
        if not self._is_trained:
            return None
        # TODO: Replace with actual model inference
        return EntryTimingPrediction(optimal_delay_minutes=2.0, confidence=0.5, features_used={})

    def get_median_ets(self) -> Optional[float]:
        if not self._records:
            return None
        scores = [r.entry_timing_score for r in self._records]
        return sorted(scores)[len(scores) // 2]
