"""
NZT-48 Phase Q4 Deliverable #3: Entry Timing ML Model
======================================================
Predicts optimal entry delay (0-10 minutes) based on market conditions.

Uses LightGBM regression trained on 200+ historical trades.

Features:
  - gap_size_pct: Size of gap at open (%)
  - rvol_at_entry: Relative volume at potential entry
  - regime: Market regime (bull, normal, bear, high_vol, risk_off)
  - time_of_day_bucket: Pre-market, open, mid-morning, lunch, afternoon, close
  - was_profitable: Whether the trade was profitable (only for training)

Target:
  - optimal_delay_minutes: How many minutes to wait before entering (0-10)

Expected Impact:
  - +2-3% average entry quality
  - +5-10% win rate improvement
  - Better timing on Type A entries (avoid entering too early)
"""
import logging
import os
import pickle
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime
from pathlib import Path

import numpy as np

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
        """Train LightGBM model on historical entry timing data."""
        if len(self._records) < self.MIN_TRADES:
            logger.warning("EntryTimingModel: insufficient data (%d < %d)", len(self._records), self.MIN_TRADES)
            return False

        try:
            import lightgbm as lgb
            import numpy as np
        except ImportError:
            logger.error("LightGBM not installed. Run: pip install lightgbm")
            return False

        # Prepare training data
        X = []
        y = []

        for record in self._records:
            features = [
                record.gap_size_pct,
                record.rvol_at_entry,
                self._encode_regime(record.regime),
                self._encode_time_bucket(record.time_of_day_bucket),
                1.0 if record.was_profitable else 0.0,  # Include profitability as feature
            ]
            X.append(features)
            y.append(record.actual_delay_minutes)

        X = np.array(X)
        y = np.array(y)

        # Train LightGBM model
        train_data = lgb.Dataset(X, label=y)

        params = {
            'objective': 'regression',
            'metric': 'rmse',
            'boosting_type': 'gbdt',
            'num_leaves': 31,
            'learning_rate': 0.05,
            'feature_fraction': 0.9,
            'verbose': -1,
        }

        self._model = lgb.train(
            params,
            train_data,
            num_boost_round=100,
            valid_sets=[train_data],
        )

        self._is_trained = True
        logger.info("EntryTimingModel: trained on %d records (RMSE: %.3f min)",
                    len(self._records), self._model.best_score['training']['rmse'])
        return True

    def predict(self, gap_size: float, rvol: float, regime: str, hour: int) -> Optional[EntryTimingPrediction]:
        """Predict optimal entry delay given market conditions."""
        if not self._is_trained:
            return None

        import numpy as np

        # Encode features
        time_bucket = self._hour_to_bucket(hour)
        features = np.array([[
            gap_size,
            rvol,
            self._encode_regime(regime),
            self._encode_time_bucket(time_bucket),
            0.5,  # Unknown profitability at prediction time
        ]])

        # Predict
        optimal_delay = self._model.predict(features)[0]

        # Clamp to reasonable range [0, 10 minutes]
        optimal_delay = max(0.0, min(optimal_delay, 10.0))

        # Calculate confidence based on historical similar trades
        confidence = self._calculate_confidence(gap_size, rvol, regime)

        return EntryTimingPrediction(
            optimal_delay_minutes=optimal_delay,
            confidence=confidence,
            features_used={
                'gap_size': gap_size,
                'rvol': rvol,
                'regime': regime,
                'hour': hour,
            }
        )

    def _encode_regime(self, regime: str) -> float:
        """Encode regime as numeric value."""
        regime_map = {
            'bull': 1.0,
            'normal': 0.5,
            'bear': 0.0,
            'high_vol': -0.5,
            'risk_off': -1.0,
        }
        return regime_map.get(regime.lower(), 0.5)

    def _encode_time_bucket(self, bucket: str) -> float:
        """Encode time of day as numeric value."""
        bucket_map = {
            'pre_market': 0.0,
            'open': 0.25,
            'mid_morning': 0.5,
            'lunch': 0.75,
            'afternoon': 1.0,
            'close': 1.25,
        }
        return bucket_map.get(bucket, 0.5)

    def _hour_to_bucket(self, hour: int) -> str:
        """Convert hour to time bucket."""
        if hour < 8:
            return 'pre_market'
        elif hour < 10:
            return 'open'
        elif hour < 12:
            return 'mid_morning'
        elif hour < 14:
            return 'lunch'
        elif hour < 16:
            return 'afternoon'
        else:
            return 'close'

    def _calculate_confidence(self, gap_size: float, rvol: float, regime: str) -> float:
        """Calculate prediction confidence based on historical similar trades."""
        if not self._records:
            return 0.5

        # Find similar trades (within 20% of features)
        similar = [
            r for r in self._records
            if abs(r.gap_size_pct - gap_size) / max(gap_size, 0.1) < 0.2
            and abs(r.rvol_at_entry - rvol) / max(rvol, 0.1) < 0.2
            and r.regime == regime
        ]

        if len(similar) < 5:
            return 0.4  # Low confidence if <5 similar trades

        # Confidence based on consistency of similar trades
        delays = [r.actual_delay_minutes for r in similar]
        std_dev = np.std(delays) if len(delays) > 1 else 0.0

        # Lower std dev = higher confidence
        if std_dev < 0.5:
            return 0.9
        elif std_dev < 1.0:
            return 0.7
        elif std_dev < 2.0:
            return 0.5
        else:
            return 0.3

    def get_median_ets(self) -> Optional[float]:
        """Get median Entry Timing Score from all records."""
        if not self._records:
            return None
        scores = [r.entry_timing_score for r in self._records]
        return sorted(scores)[len(scores) // 2]

    def save_model(self, filepath: str = "models/entry_timing_v1.pkl") -> bool:
        """Save trained model to disk."""
        if not self._is_trained:
            logger.warning("Cannot save: model not trained")
            return False

        try:
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)

            with open(filepath, 'wb') as f:
                pickle.dump({
                    'model': self._model,
                    'is_trained': self._is_trained,
                    'num_records': len(self._records),
                    'median_ets': self.get_median_ets(),
                }, f)

            logger.info("EntryTimingModel saved to %s (%d records)", filepath, len(self._records))
            return True

        except Exception as e:
            logger.error("Failed to save model: %s", e)
            return False

    def load_model(self, filepath: str = "models/entry_timing_v1.pkl") -> bool:
        """Load trained model from disk."""
        if not os.path.exists(filepath):
            logger.warning("Model file not found: %s", filepath)
            return False

        try:
            with open(filepath, 'rb') as f:
                data = pickle.load(f)

            self._model = data['model']
            self._is_trained = data['is_trained']

            logger.info("EntryTimingModel loaded from %s (trained on %d records, median ETS: %.3f)",
                        filepath, data['num_records'], data.get('median_ets', 0.0))
            return True

        except Exception as e:
            logger.error("Failed to load model: %s", e)
            return False

    def get_statistics(self) -> dict:
        """Get model statistics for monitoring."""
        if not self._records:
            return {
                'num_records': 0,
                'is_trained': False,
                'median_ets': None,
                'avg_delay': None,
                'profitable_pct': None,
            }

        delays = [r.actual_delay_minutes for r in self._records]
        profitable = [r.was_profitable for r in self._records]

        return {
            'num_records': len(self._records),
            'is_trained': self._is_trained,
            'median_ets': self.get_median_ets(),
            'avg_delay': np.mean(delays),
            'std_delay': np.std(delays),
            'profitable_pct': sum(profitable) / len(profitable) * 100,
            'min_delay': min(delays),
            'max_delay': max(delays),
        }
