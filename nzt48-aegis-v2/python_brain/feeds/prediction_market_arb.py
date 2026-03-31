"""Prediction Market Intelligence & Probability Arbitrage — Book 194.

Detects mispricings and arbitrage opportunities across prediction
markets. When P(A) + P(not A) != 1.0, there is a guaranteed profit
opportunity. Cross-market arbitrage occurs when the same event is
priced differently on two platforms.

Also provides calibrated probability aggregation using log-odds pooling,
which is theoretically optimal for combining independent forecasts.

Key features:
  - Detect complement mispricings: P(yes) + P(no) != 1.0
  - Cross-market arbitrage detection (same event, different prices)
  - Log-odds probability aggregation from multiple sources
  - Calibration testing (Brier score, reliability diagram data)

Note: AEGIS uses these signals for intelligence only (ISA cannot
trade prediction markets directly). The probabilities feed into
macro regime estimation and confidence adjustment.

State persisted to /app/data/prediction_markets/arb/.

Usage:
    from python_brain.feeds.prediction_market_arb import (
        ArbitrageDetector, ProbabilityAggregator, MarketProbability,
    )
    detector = ArbitrageDetector()
    mispricings = detector.detect_mispricing(events)
    cross_arb = detector.cross_market_arb(market1, market2)

    agg = ProbabilityAggregator()
    combined = agg.aggregate([0.70, 0.65, 0.80], [1.0, 1.0, 0.5])
    calibration = agg.calibrate(historical_probs, outcomes)
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("prediction_market_arb")

__all__ = [
    "MarketProbability",
    "ArbitrageDetector",
    "ProbabilityAggregator",
]

# ── Constants ──────────────────────────────────────────────────────────

STATE_DIR = Path("/app/data/prediction_markets/arb")
MISPRICING_THRESHOLD = 0.02    # 2% deviation from P + (1-P) = 1
CROSS_MARKET_THRESHOLD = 0.05  # 5% price difference for same event
PROB_FLOOR = 0.001             # Prevent log(0) in log-odds
PROB_CEIL = 0.999


# ── Data Classes ──────────────────────────────────────────────────────

@dataclass
class MarketProbability:
    """A probability quote from a prediction market.

    Attributes:
        market_id: Unique market identifier.
        question: The market question.
        prob_yes: Probability of YES outcome.
        prob_no: Probability of NO outcome.
        volume: Total trading volume in USD.
        spread: Bid-ask spread.
        source: Platform identifier.
        timestamp: ISO timestamp.
    """
    market_id: str
    question: str
    prob_yes: float
    prob_no: float
    volume: float = 0.0
    spread: float = 0.0
    source: str = "polymarket"
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    @property
    def overround(self) -> float:
        """Overround (vigorish): P(yes) + P(no) - 1.0.

        Positive = market maker edge.
        Negative = arbitrage opportunity.
        """
        return self.prob_yes + self.prob_no - 1.0

    @property
    def is_mispriced(self) -> bool:
        """Check if the market has a mispricing."""
        return abs(self.overround) > MISPRICING_THRESHOLD

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            "market_id": self.market_id,
            "question": self.question,
            "prob_yes": self.prob_yes,
            "prob_no": self.prob_no,
            "volume": self.volume,
            "spread": self.spread,
            "source": self.source,
            "overround": round(self.overround, 4),
            "is_mispriced": self.is_mispriced,
        }


# ── Arbitrage Detector ────────────────────────────────────────────────

class ArbitrageDetector:
    """Detects mispricings and cross-market arbitrage in prediction markets.

    Two types of arbitrage:
      1. Complement mispricing: P(A) + P(not A) != 1.0 on same platform
      2. Cross-market: Same event priced differently across platforms
    """

    def __init__(self):
        """Initialize arbitrage detector."""
        self._detected: List[Dict] = []
        log.info("ArbitrageDetector initialized")

    def detect_mispricing(self, events: List[MarketProbability]) -> List[Dict[str, Any]]:
        """Find complement mispricings: P(yes) + P(no) != 1.0.

        In an efficient market, buying YES and NO contracts should
        cost exactly $1. Deviations indicate arbitrage.

        Args:
            events: List of MarketProbability from a single platform.

        Returns:
            List of mispricing opportunities.
        """
        mispricings: List[Dict[str, Any]] = []

        for event in events:
            overround = event.overround

            if abs(overround) > MISPRICING_THRESHOLD:
                opportunity_type = "underpriced" if overround < 0 else "overpriced"

                # Expected profit from buying both sides (if underpriced)
                # or selling both sides (if overpriced)
                if overround < 0:
                    # Buy YES + NO for total < $1, guaranteed $1 payout
                    profit_pct = abs(overround) * 100
                else:
                    # Both sides sum > $1 — market maker has edge
                    profit_pct = 0.0

                entry = {
                    "market_id": event.market_id,
                    "question": event.question,
                    "prob_yes": round(event.prob_yes, 4),
                    "prob_no": round(event.prob_no, 4),
                    "overround": round(overround, 4),
                    "type": opportunity_type,
                    "profit_pct": round(profit_pct, 2),
                    "volume": event.volume,
                    "source": event.source,
                    "spread": event.spread,
                    "net_of_spread": round(profit_pct - event.spread * 100, 2),
                }
                mispricings.append(entry)

                log.info("Mispricing detected: %s — overround=%.3f%%, "
                         "type=%s, volume=%.0f",
                         event.market_id, overround * 100,
                         opportunity_type, event.volume)

        self._detected.extend(mispricings)
        return mispricings

    def cross_market_arb(self, market1: MarketProbability,
                         market2: MarketProbability) -> Optional[Dict[str, Any]]:
        """Detect cross-market arbitrage for the same event.

        If the same event is priced at P1 on market A and P2 on market B,
        and |P1 - P2| > threshold, there may be an arbitrage opportunity.

        Args:
            market1: Probability quote from first platform.
            market2: Probability quote from second platform.

        Returns:
            Arbitrage opportunity dict, or None if no opportunity.
        """
        # Compare YES probabilities
        yes_diff = abs(market1.prob_yes - market2.prob_yes)

        if yes_diff < CROSS_MARKET_THRESHOLD:
            return None

        # Determine direction: buy low, sell high
        if market1.prob_yes < market2.prob_yes:
            buy_market = market1
            sell_market = market2
        else:
            buy_market = market2
            sell_market = market1

        # Gross profit
        gross_profit_pct = yes_diff * 100

        # Net of spreads
        total_spread = buy_market.spread + sell_market.spread
        net_profit_pct = gross_profit_pct - total_spread * 100

        if net_profit_pct <= 0:
            return None

        arb = {
            "event_question": market1.question,
            "buy": {
                "market": buy_market.source,
                "market_id": buy_market.market_id,
                "prob_yes": round(buy_market.prob_yes, 4),
            },
            "sell": {
                "market": sell_market.source,
                "market_id": sell_market.market_id,
                "prob_yes": round(sell_market.prob_yes, 4),
            },
            "gross_profit_pct": round(gross_profit_pct, 2),
            "spread_cost_pct": round(total_spread * 100, 2),
            "net_profit_pct": round(net_profit_pct, 2),
            "min_volume": min(market1.volume, market2.volume),
        }

        log.info("Cross-market arb: %s — buy@%.2f (%s) sell@%.2f (%s), "
                 "net=%.2f%%",
                 market1.question,
                 buy_market.prob_yes, buy_market.source,
                 sell_market.prob_yes, sell_market.source,
                 net_profit_pct)

        self._detected.append(arb)
        return arb

    @property
    def detected_opportunities(self) -> List[Dict]:
        """All detected opportunities."""
        return list(self._detected)

    def clear(self) -> None:
        """Clear detected opportunities."""
        self._detected = []


# ── Probability Aggregator ────────────────────────────────────────────

class ProbabilityAggregator:
    """Aggregates probability estimates from multiple sources.

    Uses log-odds pooling (optimal for combining independent forecasts):
      log_odds(combined) = sum(w_i * log_odds(p_i))

    This gives more weight to extreme probabilities (near 0 or 1)
    and is theoretically justified when sources are independent.
    """

    def __init__(self):
        """Initialize probability aggregator."""
        self._calibration_history: List[Tuple[float, float]] = []
        log.info("ProbabilityAggregator initialized")

    def aggregate(self, sources: List[float],
                  weights: Optional[List[float]] = None) -> float:
        """Aggregate probabilities via log-odds pooling.

        Args:
            sources: List of probability estimates from different sources.
            weights: Optional weight for each source. Equal weights if None.

        Returns:
            Aggregated probability [0, 1].
        """
        if not sources:
            return 0.5

        n = len(sources)
        if weights is None:
            weights = [1.0 / n] * n
        else:
            total_w = sum(weights)
            if total_w > 0:
                weights = [w / total_w for w in weights]
            else:
                weights = [1.0 / n] * n

        # Clamp probabilities to prevent log(0)
        clamped = [float(np.clip(p, PROB_FLOOR, PROB_CEIL)) for p in sources]

        # Log-odds pooling
        log_odds_sum = 0.0
        for p, w in zip(clamped, weights):
            log_odds = math.log(p / (1.0 - p))
            log_odds_sum += w * log_odds

        # Convert back to probability
        combined = 1.0 / (1.0 + math.exp(-log_odds_sum))
        combined = float(np.clip(combined, PROB_FLOOR, PROB_CEIL))

        log.debug("Aggregated %d sources: %s -> %.4f",
                  n, [f"{p:.2f}" for p in sources], combined)
        return combined

    def calibrate(self, historical_probs: List[float],
                  outcomes: List[float]) -> Dict[str, Any]:
        """Evaluate calibration of probability forecasts.

        Computes Brier score and calibration curve data.

        Brier score: mean( (p_i - o_i)^2 ) — lower is better.
        Perfect calibration: Brier = 0.0
        Coin flip: Brier = 0.25

        Args:
            historical_probs: Predicted probabilities.
            outcomes: Binary outcomes (0 or 1).

        Returns:
            Calibration report dict.
        """
        if not historical_probs or not outcomes:
            return {"status": "no_data"}

        n = min(len(historical_probs), len(outcomes))
        probs = np.array(historical_probs[:n], dtype=np.float64)
        outs = np.array(outcomes[:n], dtype=np.float64)

        # Brier score
        brier = float(np.mean((probs - outs) ** 2))

        # Calibration curve: bin probabilities and compare to actual frequency
        n_bins = 10
        bin_edges = np.linspace(0, 1, n_bins + 1)
        calibration_bins: List[Dict[str, Any]] = []

        for i in range(n_bins):
            lo = bin_edges[i]
            hi = bin_edges[i + 1]
            mask = (probs >= lo) & (probs < hi) if i < n_bins - 1 else (probs >= lo) & (probs <= hi)
            bin_n = int(np.sum(mask))

            if bin_n > 0:
                mean_pred = float(np.mean(probs[mask]))
                mean_actual = float(np.mean(outs[mask]))
                calibration_bins.append({
                    "bin": f"{lo:.1f}-{hi:.1f}",
                    "n": bin_n,
                    "mean_predicted": round(mean_pred, 3),
                    "mean_actual": round(mean_actual, 3),
                    "gap": round(abs(mean_pred - mean_actual), 3),
                })

        # Expected Calibration Error (ECE)
        ece = 0.0
        for b in calibration_bins:
            ece += b["n"] / n * b["gap"]

        # Log-loss
        eps = 1e-10
        clamped_p = np.clip(probs, eps, 1 - eps)
        log_loss = -float(np.mean(
            outs * np.log(clamped_p) + (1 - outs) * np.log(1 - clamped_p)
        ))

        return {
            "brier_score": round(brier, 4),
            "log_loss": round(log_loss, 4),
            "ece": round(ece, 4),
            "n_samples": n,
            "calibration_bins": calibration_bins,
            "interpretation": self._interpret_brier(brier),
        }

    @staticmethod
    def _interpret_brier(brier: float) -> str:
        """Interpret Brier score quality."""
        if brier < 0.10:
            return "excellent"
        elif brier < 0.20:
            return "good"
        elif brier < 0.25:
            return "fair (near coin-flip)"
        else:
            return "poor (worse than coin-flip)"

    def save(self, path: str = "/app/data/prediction_markets/arb/calibration.json") -> None:
        """Save calibration history."""
        save_path = Path(path)
        try:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "history": self._calibration_history,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            with open(str(save_path), "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.error("Failed to save calibration: %s", e)

    def load(self, path: str = "/app/data/prediction_markets/arb/calibration.json") -> None:
        """Load calibration history."""
        try:
            with open(path, "r") as f:
                data = json.load(f)
            self._calibration_history = data.get("history", [])
            log.info("Loaded %d calibration samples", len(self._calibration_history))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.error("Failed to load calibration: %s", e)
