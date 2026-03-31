"""Book 23 Bridge Integration — Add entry confidence to signals.

This module integrates the LightGBM entry classifier into bridge.py's signal generation.
For each signal:
1. Extract 48 features from current market state
2. Predict confidence score [0.0-1.0]
3. Filter signals where confidence < 0.6
4. Add entry_classifier_confidence field to signal output
5. Track classifier performance vs actual signal PnL

Integration in bridge.py:
    from brain.ml.entry_classifier_bridge import EntryClassifierGate

    classifier_gate = EntryClassifierGate(model_path="/app/data/entry_classifier/model.txt")

    # Before sending signal to Rust:
    signal = vanguard_evaluate(...)
    if signal:
        confidence, is_approved = classifier_gate.gate_signal(signal, bar_history[ticker_id])
        if is_approved:
            signal['entry_classifier_confidence'] = confidence
            # Send to Rust
        else:
            # Veto this signal
            pass
"""

import json
import logging
import os
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from entry_classifier_book23 import (
    FeatureEngineer,
    EntryClassifierTrainer,
)

logger = logging.getLogger(__name__)


class EntryClassifierGate:
    """Real-time entry classifier gate for bridge.py."""

    def __init__(self, model_path: str = "/app/data/entry_classifier/model.txt",
                 confidence_threshold: float = 0.6):
        """
        Args:
            model_path: Path to saved LightGBM model
            confidence_threshold: Minimum confidence to approve signal (0.6 default)
        """
        self.model_path = model_path
        self.confidence_threshold = confidence_threshold
        self.trainer = None

        # Load model if available
        if os.path.exists(model_path):
            try:
                self.trainer = EntryClassifierTrainer.load(model_path)
                logger.info(f"Loaded entry classifier from {model_path}")
            except Exception as e:
                logger.warning(f"Failed to load entry classifier: {e}. Using fallback (always 0.5).")
                self.trainer = None
        else:
            logger.warning(f"Entry classifier model not found at {model_path}. Using fallback.")

        # Track performance: (signal_id -> {entry_price, entry_time, classifier_confidence})
        self.pending_entries = {}

        # Performance log
        self.performance_log = deque(maxlen=1000)
        self.perf_log_path = model_path.replace('.txt', '_perf.ndjson')

    def gate_signal(self, signal: Dict, bar_history: deque) -> Tuple[float, bool]:
        """
        Score and gate a signal based on entry classifier.

        Args:
            signal: Signal dict from bridge.py (has direction, price, etc.)
            bar_history: Deque of OHLCV bars for this ticker

        Returns:
            (confidence, is_approved)
            - confidence: [0.0-1.0] confidence score from classifier
            - is_approved: True if confidence > threshold
        """
        if self.trainer is None:
            # Fallback: use 0.5 confidence, approve all
            return 0.5, True

        try:
            # Convert bar_history to DataFrame
            if len(bar_history) < 50:
                logger.warning(f"Not enough bars ({len(bar_history)}) to classify")
                return 0.5, True

            ohlcv_list = list(bar_history)
            df = pd.DataFrame(ohlcv_list)

            # Engineer features
            feature_engineer = FeatureEngineer(df)
            X = feature_engineer.compute_all_features()

            # Use last row's features
            features = X.iloc[-1]

            # Predict confidence
            X_single = X.iloc[[-1]]
            confidence = self.trainer.predict(X_single)[0]

            # Determine approval
            is_approved = confidence > self.confidence_threshold

            # Log
            logger.debug(f"Signal: {signal.get('strategy')} on {signal.get('instrument')} "
                        f"-> confidence={confidence:.3f}, approved={is_approved}")

            # Track for later performance evaluation
            signal_id = signal.get('signal_id', f"sig_{datetime.now().timestamp()}")
            self.pending_entries[signal_id] = {
                'entry_price': signal.get('price', 0),
                'entry_time': datetime.now(),
                'classifier_confidence': confidence,
                'strategy': signal.get('strategy', 'unknown'),
                'direction': signal.get('direction', 'Long'),
            }

            return confidence, is_approved

        except Exception as e:
            logger.error(f"Error in entry classifier gate: {e}")
            return 0.5, True  # Fallback: approve with neutral confidence

    def track_exit(self, signal_id: str, exit_price: float, exit_time: Optional[datetime] = None):
        """
        Track signal exit for performance evaluation.

        Args:
            signal_id: Signal identifier
            exit_price: Exit price
            exit_time: Exit timestamp (default: now)
        """
        if signal_id not in self.pending_entries:
            return

        entry = self.pending_entries.pop(signal_id)
        exit_time = exit_time or datetime.now()

        # Calculate PnL
        entry_price = entry['entry_price']
        if entry['direction'] == 'Long':
            pnl = (exit_price - entry_price) / entry_price
        else:
            pnl = (entry_price - exit_price) / entry_price

        # Log performance
        perf_event = {
            'signal_id': signal_id,
            'strategy': entry['strategy'],
            'direction': entry['direction'],
            'classifier_confidence': entry['classifier_confidence'],
            'entry_price': entry_price,
            'exit_price': exit_price,
            'pnl': pnl,
            'entry_time': entry['entry_time'].isoformat(),
            'exit_time': exit_time.isoformat(),
            'duration_seconds': (exit_time - entry['entry_time']).total_seconds(),
            'timestamp': datetime.now().isoformat(),
        }

        self.performance_log.append(perf_event)

        # Append to file
        try:
            with open(self.perf_log_path, 'a') as f:
                f.write(json.dumps(perf_event) + '\n')
        except Exception as e:
            logger.error(f"Failed to write perf log: {e}")

    def get_performance_stats(self) -> Dict:
        """
        Get classifier performance statistics.

        Returns:
            Dict with metrics: win_rate, avg_pnl, precision, recall, etc.
        """
        if not self.performance_log:
            return {}

        perf_list = list(self.performance_log)
        pnls = [p['pnl'] for p in perf_list]
        confidences = [p['classifier_confidence'] for p in perf_list]

        # Classify wins/losses
        n_trades = len(perf_list)
        n_wins = sum(1 for p in pnl if p > 0 for p in pnls)
        n_losses = n_trades - n_wins

        # By confidence level
        high_conf_trades = [p for p in perf_list if p['classifier_confidence'] > 0.75]
        high_conf_wins = sum(1 for p in high_conf_trades if p['pnl'] > 0)
        high_conf_wr = high_conf_wins / len(high_conf_trades) if high_conf_trades else 0

        return {
            'n_trades': n_trades,
            'n_wins': n_wins,
            'n_losses': n_losses,
            'win_rate': n_wins / n_trades if n_trades > 0 else 0,
            'avg_pnl': float(np.mean(pnls)) if pnls else 0,
            'std_pnl': float(np.std(pnls)) if pnls else 0,
            'avg_confidence': float(np.mean(confidences)) if confidences else 0,
            'high_confidence_win_rate': high_conf_wr,
            'high_confidence_trades': len(high_conf_trades),
        }

    def report_performance(self) -> str:
        """Generate performance report."""
        stats = self.get_performance_stats()

        if not stats:
            return "No performance data yet."

        report = f"""
Entry Classifier Performance Report
====================================
Total Trades: {stats['n_trades']}
Wins: {stats['n_wins']}
Losses: {stats['n_losses']}
Win Rate: {stats['win_rate']:.2%}
Avg PnL: {stats['avg_pnl']:.4f}
Std PnL: {stats['std_pnl']:.4f}
Avg Confidence: {stats['avg_confidence']:.3f}
High-Conf (>0.75) Win Rate: {stats['high_confidence_win_rate']:.2%} ({stats['high_confidence_trades']} trades)
"""
        return report


class ClassifierPerformanceMonitor:
    """Monitor classifier performance for quarterly retraining decisions."""

    def __init__(self, model_path: str):
        self.model_path = model_path
        self.last_retrain = datetime.now()
        self.perf_log_path = model_path.replace('.txt', '_perf.ndjson')

    def should_retrain(self) -> bool:
        """Check if model should be retrained."""
        # Retrain quarterly
        age = datetime.now() - self.last_retrain
        if age > timedelta(days=90):
            return True

        # Or if concept drift detected
        try:
            stats = self._compute_performance_stats()
            if stats.get('win_rate', 0) < 0.45:  # Below expected ~55%
                logger.warning("Concept drift detected: win_rate < 0.45")
                return True
        except Exception as e:
            logger.error(f"Error checking performance: {e}")

        return False

    def _compute_performance_stats(self) -> Dict:
        """Load perf log and compute stats."""
        if not os.path.exists(self.perf_log_path):
            return {}

        pnls = []
        with open(self.perf_log_path, 'r') as f:
            for line in f:
                try:
                    event = json.loads(line)
                    pnls.append(event['pnl'])
                except Exception:
                    continue

        if not pnls:
            return {}

        # Last 30 days of trades
        recent_pnls = pnls[-500:] if len(pnls) > 500 else pnls
        n_wins = sum(1 for p in recent_pnls if p > 0)

        return {
            'n_trades': len(recent_pnls),
            'win_rate': n_wins / len(recent_pnls) if recent_pnls else 0,
            'avg_pnl': float(np.mean(recent_pnls)) if recent_pnls else 0,
        }

    def report_readiness(self) -> str:
        """Report on retraining readiness."""
        age = datetime.now() - self.last_retrain
        stats = self._compute_performance_stats()

        report = f"""
Classifier Retraining Readiness
================================
Model Age: {age.days} days
Should Retrain: {self.should_retrain()}

Performance (Last 500 Trades):
  Trades: {stats.get('n_trades', 0)}
  Win Rate: {stats.get('win_rate', 0):.2%}
  Avg PnL: {stats.get('avg_pnl', 0):.4f}
"""
        return report


if __name__ == '__main__':
    # Example usage
    gate = EntryClassifierGate()
    print("Entry Classifier Bridge Module loaded")
