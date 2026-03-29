"""Bayesian Signal Aggregation — Book 209.

Combines multiple signal sources (13 generators + LLMs) via Bayesian
posterior updating instead of naive "highest confidence wins."

Each source is an imperfect observer of the true market state.
We maintain a confusion matrix per source: P(signal | true_state).
Sources with better track records get more weight automatically.

Key advantage over current system:
- Current: best single signal wins (ignores all other information)
- Bayesian: all signals contribute, weighted by proven accuracy

Usage:
    from python_brain.aggregation.bayesian_aggregator import (
        BayesianAggregator, SignalSource,
    )

    agg = BayesianAggregator()
    agg.add_source("TypeF", accuracy=0.72, n_trades=100)
    agg.add_source("S2_Reversion", accuracy=0.45, n_trades=50)

    posterior = agg.aggregate([
        ("TypeF", "long", 0.75),
        ("S2_Reversion", "long", 0.65),
    ])
    # posterior.direction = "long", posterior.confidence = 0.82
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("bayesian_aggregator")


@dataclass
class SignalSource:
    """Track record for a signal source."""
    name: str
    # Confusion matrix components
    true_positive: int = 0   # Predicted long, market went up
    false_positive: int = 0  # Predicted long, market went down
    true_negative: int = 0   # Predicted no trade/short, market went down
    false_negative: int = 0  # Predicted no trade/short, market went up
    total_signals: int = 0
    # Health
    is_active: bool = True
    consecutive_wrong: int = 0
    suspend_threshold: int = 10  # Auto-suspend after 10 consecutive wrong

    @property
    def accuracy(self) -> float:
        total = self.true_positive + self.false_positive + self.true_negative + self.false_negative
        if total == 0:
            return 0.5  # Prior: assume 50% accuracy
        return (self.true_positive + self.true_negative) / total

    @property
    def precision(self) -> float:
        """P(correct | signal fired)"""
        denom = self.true_positive + self.false_positive
        return self.true_positive / denom if denom > 0 else 0.5

    @property
    def recall(self) -> float:
        """P(signal fired | should have traded)"""
        denom = self.true_positive + self.false_negative
        return self.true_positive / denom if denom > 0 else 0.5

    @property
    def likelihood_ratio_positive(self) -> float:
        """LR+ = sensitivity / (1 - specificity)."""
        sensitivity = self.recall
        specificity = self.true_negative / max(self.true_negative + self.false_positive, 1)
        if specificity >= 1.0:
            return 10.0  # Cap
        return sensitivity / (1 - specificity + 1e-10)

    def update(self, predicted_direction: str, actual_profitable: bool):
        """Update confusion matrix with new observation."""
        self.total_signals += 1

        if predicted_direction in ("long", "buy"):
            if actual_profitable:
                self.true_positive += 1
                self.consecutive_wrong = 0
            else:
                self.false_positive += 1
                self.consecutive_wrong += 1
        else:
            if actual_profitable:
                self.false_negative += 1
                self.consecutive_wrong += 1
            else:
                self.true_negative += 1
                self.consecutive_wrong = 0

        # Auto-suspend on consecutive failures
        if self.consecutive_wrong >= self.suspend_threshold:
            self.is_active = False
            log.warning(
                "SOURCE_SUSPENDED: %s after %d consecutive wrong signals (accuracy=%.1f%%)",
                self.name, self.consecutive_wrong, self.accuracy * 100,
            )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "accuracy": round(self.accuracy, 3),
            "precision": round(self.precision, 3),
            "total_signals": self.total_signals,
            "is_active": self.is_active,
            "consecutive_wrong": self.consecutive_wrong,
            "lr_positive": round(self.likelihood_ratio_positive, 2),
        }


@dataclass
class AggregatedSignal:
    """Result of Bayesian aggregation across multiple sources."""
    direction: str  # "long", "short", "no_trade"
    posterior_prob: float  # P(profitable | all signals)
    confidence: int  # 0-100 mapped from posterior
    n_sources_agree: int
    n_sources_total: int
    source_contributions: Dict[str, float] = field(default_factory=dict)


class BayesianAggregator:
    """Aggregate signals from multiple sources using Bayesian updating."""

    def __init__(self, base_rate: float = 0.52):
        """
        Args:
            base_rate: Prior P(profitable trade). Historical FTSE: ~52% up days.
        """
        self.base_rate = base_rate
        self._sources: Dict[str, SignalSource] = {}

    def add_source(
        self,
        name: str,
        accuracy: float = 0.5,
        n_trades: int = 0,
    ) -> SignalSource:
        """Register a signal source with initial accuracy estimate."""
        source = SignalSource(name=name)
        # Initialize confusion matrix from accuracy estimate
        if n_trades > 0 and accuracy > 0:
            tp = int(n_trades * accuracy * 0.5)
            fp = int(n_trades * (1 - accuracy) * 0.5)
            tn = int(n_trades * accuracy * 0.5)
            fn = int(n_trades * (1 - accuracy) * 0.5)
            source.true_positive = tp
            source.false_positive = fp
            source.true_negative = tn
            source.false_negative = fn
            source.total_signals = n_trades
        self._sources[name] = source
        return source

    def get_source(self, name: str) -> Optional[SignalSource]:
        return self._sources.get(name)

    def aggregate(
        self,
        signals: List[Tuple[str, str, float]],
    ) -> AggregatedSignal:
        """Aggregate multiple signals via Bayesian updating.

        Args:
            signals: List of (source_name, direction, raw_confidence)
                direction: "long" or "short"/"no_trade"
                raw_confidence: 0.0-1.0

        Returns: AggregatedSignal with posterior probability
        """
        if not signals:
            return AggregatedSignal("no_trade", self.base_rate, 0, 0, 0)

        # Start with prior odds
        prior_odds = self.base_rate / (1 - self.base_rate + 1e-10)

        # Count direction votes
        long_count = sum(1 for _, d, _ in signals if d in ("long", "buy"))
        total = len(signals)

        # Bayesian update: multiply prior odds by each source's likelihood ratio
        posterior_odds = prior_odds
        contributions: Dict[str, float] = {}

        for source_name, direction, raw_conf in signals:
            source = self._sources.get(source_name)
            if source is None or not source.is_active:
                continue

            # Use source's likelihood ratio
            lr = source.likelihood_ratio_positive
            if direction not in ("long", "buy"):
                lr = 1.0 / max(lr, 0.1)  # Inverse LR for bearish signals

            # Weight by raw confidence (higher confidence → stronger update)
            weighted_lr = 1.0 + (lr - 1.0) * min(raw_conf, 1.0)
            posterior_odds *= weighted_lr
            contributions[source_name] = round(weighted_lr, 3)

        # Convert odds back to probability
        posterior_prob = posterior_odds / (1 + posterior_odds)
        posterior_prob = max(0.0, min(1.0, posterior_prob))

        # Direction from posterior
        if posterior_prob > 0.55:
            direction = "long"
        elif posterior_prob < 0.45:
            direction = "short"
        else:
            direction = "no_trade"

        # Map posterior to confidence (0-100)
        # 0.55 → 55, 0.70 → 70, 0.85 → 85
        confidence = int(posterior_prob * 100)

        return AggregatedSignal(
            direction=direction,
            posterior_prob=round(posterior_prob, 4),
            confidence=confidence,
            n_sources_agree=long_count if direction == "long" else total - long_count,
            n_sources_total=total,
            source_contributions=contributions,
        )

    def update_source(self, name: str, predicted_direction: str, actual_profitable: bool):
        """Update a source's track record after trade outcome."""
        source = self._sources.get(name)
        if source:
            source.update(predicted_direction, actual_profitable)

    def reactivate_source(self, name: str):
        """Reactivate a suspended source (after manual review)."""
        source = self._sources.get(name)
        if source:
            source.is_active = True
            source.consecutive_wrong = 0
            log.info("SOURCE_REACTIVATED: %s", name)

    def get_all_sources(self) -> Dict[str, dict]:
        return {name: s.to_dict() for name, s in self._sources.items()}

    def to_dict(self) -> dict:
        return {
            "base_rate": self.base_rate,
            "n_sources": len(self._sources),
            "active_sources": sum(1 for s in self._sources.values() if s.is_active),
            "sources": self.get_all_sources(),
        }
