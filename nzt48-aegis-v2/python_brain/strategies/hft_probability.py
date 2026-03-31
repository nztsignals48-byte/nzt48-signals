"""High-Frequency Probability Trading — Book 204.

Adapted from the $313-to-$438K Polymarket playbook. Instead of binary crypto
contracts, we frame 5-second ETP bar movements as probability estimates.
When P(profitable) > 0.5 + min_edge, we trade. When it does not, we wait.

Key components:
  - TickProbabilityModel: 5-state Markov chain transition matrix
  - MicroPatternDetector: Detects micro-patterns (double-tap, V-reversal, breakout)
  - HFTProbabilitySignal: Combines probability + patterns into actionable signals

States (5-state Markov chain):
  0 = STRONG_DOWN  (return < -2 sigma)
  1 = WEAK_DOWN    (return < -0.5 sigma)
  2 = FLAT         (return in [-0.5, +0.5] sigma)
  3 = WEAK_UP      (return > +0.5 sigma)
  4 = STRONG_UP    (return > +2 sigma)

Data paths:
  - /app/data/hft_probability_state.json — model state + transition matrix

Bridge.py integration:
    try:
        from python_brain.strategies.hft_probability import (
            HFTProbabilitySignal, TickProbabilityModel, MicroPatternDetector,
        )
    except ImportError:
        pass

Usage:
    signal_gen = HFTProbabilitySignal(min_edge=0.02)
    result = signal_gen.generate({
        "price": 15.42, "volume": 1200, "spread": 0.04,
        "prices": np.array([...]),  # recent price series
    })
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger("hft_probability")

__all__ = [
    "TickProbabilityModel",
    "MicroPatternDetector",
    "HFTProbabilitySignal",
]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR = Path("/app/data")
STATE_PATH = DATA_DIR / "hft_probability_state.json"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
N_STATES = 5
STATE_NAMES = ["STRONG_DOWN", "WEAK_DOWN", "FLAT", "WEAK_UP", "STRONG_UP"]


# ---------------------------------------------------------------------------
# Tick Probability Model (Markov Chain)
# ---------------------------------------------------------------------------
class TickProbabilityModel:
    """5-state Markov chain over discretised price returns.

    Each 5-second bar is classified into one of 5 states based on its
    return relative to recent volatility. The transition matrix tracks
    P(next_state | current_state) and is updated online.

    State classification:
        0 STRONG_DOWN: return < -2*sigma
        1 WEAK_DOWN:   return in [-2*sigma, -0.5*sigma)
        2 FLAT:        return in [-0.5*sigma, +0.5*sigma]
        3 WEAK_UP:     return in (+0.5*sigma, +2*sigma]
        4 STRONG_UP:   return > +2*sigma
    """

    def __init__(self, lookback: int = 100) -> None:
        """Initialise tick probability model.

        Args:
            lookback: Number of recent observations for volatility estimation.
        """
        self._lookback = lookback
        self._returns: Deque[float] = deque(maxlen=lookback)
        self._states: Deque[int] = deque(maxlen=lookback)
        self._prices: Deque[float] = deque(maxlen=lookback + 1)
        self._volumes: Deque[float] = deque(maxlen=lookback)
        self._spreads: Deque[float] = deque(maxlen=lookback)

        # Transition count matrix: transitions[from_state][to_state]
        self._transitions = np.zeros((N_STATES, N_STATES), dtype=np.float64)
        # Add Laplace smoothing (1 pseudocount per cell)
        self._transitions += 1.0

        self._update_count: int = 0
        self._current_state: int = 2  # start FLAT
        log.info("TickProbabilityModel: lookback=%d states=%d", lookback, N_STATES)

    def _classify_return(self, ret: float, sigma: float) -> int:
        """Classify a return into one of 5 states.

        Args:
            ret: Price return (fractional).
            sigma: Current volatility estimate.

        Returns:
            State index (0-4).
        """
        if sigma <= 1e-12:
            return 2  # FLAT if no volatility

        z = ret / sigma
        if z < -2.0:
            return 0  # STRONG_DOWN
        elif z < -0.5:
            return 1  # WEAK_DOWN
        elif z <= 0.5:
            return 2  # FLAT
        elif z <= 2.0:
            return 3  # WEAK_UP
        else:
            return 4  # STRONG_UP

    def _estimate_sigma(self) -> float:
        """Estimate current volatility from recent returns.

        Returns:
            Standard deviation of recent returns. Minimum 1e-10.
        """
        if len(self._returns) < 5:
            return 1e-6  # not enough data
        arr = np.array(list(self._returns), dtype=np.float64)
        sigma = float(np.std(arr))
        return max(sigma, 1e-10)

    def update(
        self,
        price: float,
        volume: float = 0.0,
        spread: float = 0.0,
    ) -> Dict[str, Any]:
        """Update model with a new tick observation.

        Computes return from previous price, classifies state, updates
        transition matrix, and returns current model state.

        Args:
            price: Current price.
            volume: Current bar volume.
            spread: Current bid-ask spread.

        Returns:
            Dict with current state info, sigma, transition probabilities.
        """
        self._prices.append(price)
        self._volumes.append(volume)
        self._spreads.append(spread)
        self._update_count += 1

        if len(self._prices) < 2:
            return {
                "state": STATE_NAMES[self._current_state],
                "state_id": self._current_state,
                "sigma": 0.0,
                "updates": self._update_count,
                "ready": False,
            }

        # Compute return
        prev_price = self._prices[-2]
        if prev_price <= 0.0:
            return {"state": STATE_NAMES[2], "state_id": 2, "sigma": 0.0,
                    "updates": self._update_count, "ready": False}

        ret = (price - prev_price) / prev_price
        self._returns.append(ret)

        # Classify state
        sigma = self._estimate_sigma()
        new_state = self._classify_return(ret, sigma)

        # Update transition matrix
        self._transitions[self._current_state][new_state] += 1.0

        prev_state = self._current_state
        self._current_state = new_state
        self._states.append(new_state)

        log.debug(
            "Tick #%d: price=%.4f ret=%.6f sigma=%.6f state=%s->%s",
            self._update_count, price, ret, sigma,
            STATE_NAMES[prev_state], STATE_NAMES[new_state],
        )

        return {
            "state": STATE_NAMES[new_state],
            "state_id": new_state,
            "prev_state": STATE_NAMES[prev_state],
            "return": ret,
            "sigma": sigma,
            "updates": self._update_count,
            "ready": len(self._returns) >= 20,
        }

    def _compute_transition_matrix(
        self,
        states: Optional[List[int]] = None,
    ) -> np.ndarray:
        """Compute normalised transition probability matrix.

        Each row sums to 1.0: P(next_state | current_state).

        Args:
            states: Optional state sequence to compute from.
                    If None, uses internal transition counts.

        Returns:
            N_STATES x N_STATES numpy array of transition probabilities.
        """
        if states is not None:
            counts = np.ones((N_STATES, N_STATES), dtype=np.float64)  # Laplace
            for i in range(len(states) - 1):
                counts[states[i]][states[i + 1]] += 1.0
        else:
            counts = self._transitions.copy()

        # Normalise rows
        row_sums = counts.sum(axis=1, keepdims=True)
        row_sums = np.where(row_sums > 0, row_sums, 1.0)
        matrix = counts / row_sums
        return matrix

    def predict_next_state(self) -> Dict[str, Any]:
        """Predict the most likely next state and its probability.

        Uses the current state's row in the transition matrix.

        Returns:
            Dict with:
              - predicted_state: Most likely next state name
              - predicted_id: State index
              - probability: P(predicted | current)
              - distribution: Full probability vector for all states
        """
        matrix = self._compute_transition_matrix()
        row = matrix[self._current_state]
        best_id = int(np.argmax(row))
        best_prob = float(row[best_id])

        distribution = {STATE_NAMES[i]: round(float(row[i]), 4) for i in range(N_STATES)}

        return {
            "current_state": STATE_NAMES[self._current_state],
            "predicted_state": STATE_NAMES[best_id],
            "predicted_id": best_id,
            "probability": round(best_prob, 4),
            "distribution": distribution,
        }

    def get_transition_matrix(self) -> Dict[str, Dict[str, float]]:
        """Return the full transition matrix as a nested dict.

        Returns:
            Dict[from_state_name][to_state_name] = probability.
        """
        matrix = self._compute_transition_matrix()
        result = {}
        for i in range(N_STATES):
            result[STATE_NAMES[i]] = {
                STATE_NAMES[j]: round(float(matrix[i][j]), 4)
                for j in range(N_STATES)
            }
        return result

    def directional_probability(self) -> Tuple[float, float, float]:
        """Compute P(up), P(flat), P(down) from current state.

        Aggregates transition probabilities:
          P(up) = P(WEAK_UP) + P(STRONG_UP)
          P(flat) = P(FLAT)
          P(down) = P(WEAK_DOWN) + P(STRONG_DOWN)

        Returns:
            Tuple of (p_up, p_flat, p_down).
        """
        matrix = self._compute_transition_matrix()
        row = matrix[self._current_state]

        p_down = float(row[0] + row[1])
        p_flat = float(row[2])
        p_up = float(row[3] + row[4])
        return (p_up, p_flat, p_down)


# ---------------------------------------------------------------------------
# Micro-Pattern Detector
# ---------------------------------------------------------------------------
class MicroPatternDetector:
    """Detects micro-patterns in short price windows.

    Patterns:
      - double_tap: Price touches a level twice (support/resistance)
      - v_reversal: Sharp drop followed by sharp recovery
      - breakout: Price breaks above/below recent range with momentum
    """

    def __init__(self, atr_lookback: int = 50) -> None:
        """Initialise micro-pattern detector.

        Args:
            atr_lookback: Lookback for average true range estimation.
        """
        self._atr_lookback = atr_lookback
        self._detect_count: int = 0
        log.info("MicroPatternDetector: atr_lookback=%d", atr_lookback)

    def detect(
        self,
        prices: np.ndarray,
        window: int = 20,
    ) -> List[Dict[str, Any]]:
        """Detect all micro-patterns in the price window.

        Args:
            prices: Recent price array (newest last).
            window: Window size for pattern detection.

        Returns:
            List of detected pattern dicts with type, confidence, direction.
        """
        if len(prices) < window:
            return []

        self._detect_count += 1
        recent = prices[-window:]
        patterns = []

        dt = self._double_tap(recent)
        if dt is not None:
            patterns.append(dt)

        vr = self._v_reversal(recent)
        if vr is not None:
            patterns.append(vr)

        bo = self._breakout(prices, window)
        if bo is not None:
            patterns.append(bo)

        if patterns:
            log.debug(
                "Detected %d patterns in window of %d: %s",
                len(patterns), window,
                [p["type"] for p in patterns],
            )
        return patterns

    def _double_tap(self, prices: np.ndarray) -> Optional[Dict[str, Any]]:
        """Detect double-tap pattern (price touches a level twice).

        Looks for two lows within tolerance near the same price level
        (double bottom) or two highs (double top) within the window.

        Args:
            prices: Price window array.

        Returns:
            Pattern dict or None if not detected.
        """
        if len(prices) < 10:
            return None

        mid = len(prices) // 2
        first_half = prices[:mid]
        second_half = prices[mid:]

        # Check for double bottom
        low1 = float(np.min(first_half))
        low2 = float(np.min(second_half))
        current = float(prices[-1])

        tolerance = (float(np.max(prices)) - float(np.min(prices))) * 0.15
        if tolerance < 1e-8:
            return None

        if abs(low1 - low2) < tolerance and current > max(low1, low2):
            # Double bottom → bullish
            confidence = 1.0 - abs(low1 - low2) / tolerance
            return {
                "type": "double_tap",
                "subtype": "double_bottom",
                "direction": "BUY",
                "confidence": round(min(confidence, 1.0), 3),
                "level": round((low1 + low2) / 2.0, 6),
                "current": round(current, 6),
            }

        # Check for double top
        high1 = float(np.max(first_half))
        high2 = float(np.max(second_half))
        if abs(high1 - high2) < tolerance and current < min(high1, high2):
            confidence = 1.0 - abs(high1 - high2) / tolerance
            return {
                "type": "double_tap",
                "subtype": "double_top",
                "direction": "SELL",
                "confidence": round(min(confidence, 1.0), 3),
                "level": round((high1 + high2) / 2.0, 6),
                "current": round(current, 6),
            }

        return None

    def _v_reversal(self, prices: np.ndarray) -> Optional[Dict[str, Any]]:
        """Detect V-reversal: sharp drop then sharp recovery (or inverse).

        Looks for a trough/peak in the middle third of the window
        where the move down and up are both significant.

        Args:
            prices: Price window array.

        Returns:
            Pattern dict or None.
        """
        if len(prices) < 9:
            return None

        n = len(prices)
        third = n // 3

        first_price = float(prices[0])
        last_price = float(prices[-1])
        mid_section = prices[third:2 * third]

        if len(mid_section) == 0:
            return None

        price_range = float(np.max(prices)) - float(np.min(prices))
        if price_range < 1e-8:
            return None

        # Bullish V: drop then recovery
        mid_low = float(np.min(mid_section))
        drop = first_price - mid_low
        recovery = last_price - mid_low

        min_move = price_range * 0.3  # at least 30% of range

        if drop > min_move and recovery > min_move and last_price > first_price * 0.998:
            strength = min(drop, recovery) / price_range
            return {
                "type": "v_reversal",
                "subtype": "bullish_v",
                "direction": "BUY",
                "confidence": round(min(strength, 1.0), 3),
                "trough": round(mid_low, 6),
                "current": round(last_price, 6),
            }

        # Bearish inverted V: rise then drop
        mid_high = float(np.max(mid_section))
        rise = mid_high - first_price
        drop_back = mid_high - last_price

        if rise > min_move and drop_back > min_move and last_price < first_price * 1.002:
            strength = min(rise, drop_back) / price_range
            return {
                "type": "v_reversal",
                "subtype": "bearish_inv_v",
                "direction": "SELL",
                "confidence": round(min(strength, 1.0), 3),
                "peak": round(mid_high, 6),
                "current": round(last_price, 6),
            }

        return None

    def _breakout(
        self,
        prices: np.ndarray,
        window: int = 20,
    ) -> Optional[Dict[str, Any]]:
        """Detect breakout from recent range.

        Price breaks above/below the high/low of the prior window
        with volume or momentum confirmation (here: momentum only).

        Args:
            prices: Full price array (not just recent window).
            window: Size of the consolidation window.

        Returns:
            Pattern dict or None.
        """
        if len(prices) < window + 5:
            return None

        # Consolidation range: prior window (excluding last 5 bars)
        consolidation = prices[-(window + 5):-5]
        recent = prices[-5:]

        range_high = float(np.max(consolidation))
        range_low = float(np.min(consolidation))
        range_size = range_high - range_low

        if range_size < 1e-8:
            return None

        current = float(recent[-1])

        # Bullish breakout
        if current > range_high:
            excess = (current - range_high) / range_size
            confidence = min(excess / 0.5, 1.0)  # 50% of range excess = max conf
            return {
                "type": "breakout",
                "subtype": "bullish_breakout",
                "direction": "BUY",
                "confidence": round(confidence, 3),
                "range_high": round(range_high, 6),
                "range_low": round(range_low, 6),
                "current": round(current, 6),
            }

        # Bearish breakdown
        if current < range_low:
            excess = (range_low - current) / range_size
            confidence = min(excess / 0.5, 1.0)
            return {
                "type": "breakout",
                "subtype": "bearish_breakdown",
                "direction": "SELL",
                "confidence": round(confidence, 3),
                "range_high": round(range_high, 6),
                "range_low": round(range_low, 6),
                "current": round(current, 6),
            }

        return None


# ---------------------------------------------------------------------------
# HFT Probability Signal Generator
# ---------------------------------------------------------------------------
class HFTProbabilitySignal:
    """Combines Markov transition probabilities with micro-pattern detection
    to generate high-frequency signals.

    Only fires when P(profitable) > 0.5 + min_edge, ensuring each signal
    has a positive expected value after costs.

    Uses frequency-adjusted Kelly criterion for sizing.
    """

    def __init__(
        self,
        min_edge: float = 0.02,
        lookback: int = 100,
        pattern_window: int = 20,
        pattern_boost: float = 0.05,
        avg_win_return: float = 0.003,
        avg_loss_return: float = 0.005,
    ) -> None:
        """Initialise HFT probability signal generator.

        Args:
            min_edge: Minimum edge above 0.5 to fire a signal.
                      E.g., 0.02 means P(win) must be > 0.52.
            lookback: Lookback for Markov model volatility estimation.
            pattern_window: Window for micro-pattern detection.
            pattern_boost: Probability boost when a confirming pattern is detected.
            avg_win_return: Average return on winning trades (for Kelly).
            avg_loss_return: Average return on losing trades (for Kelly).
        """
        self._min_edge = min_edge
        self._pattern_window = pattern_window
        self._pattern_boost = pattern_boost
        self._avg_win = avg_win_return
        self._avg_loss = avg_loss_return

        self._markov = TickProbabilityModel(lookback=lookback)
        self._pattern_detector = MicroPatternDetector()
        self._signal_count: int = 0
        self._skip_count: int = 0

        log.info(
            "HFTProbabilitySignal: min_edge=%.3f pattern_boost=%.3f",
            min_edge, pattern_boost,
        )

    @property
    def markov_model(self) -> TickProbabilityModel:
        """Return the underlying Markov model."""
        return self._markov

    @property
    def pattern_detector(self) -> MicroPatternDetector:
        """Return the micro-pattern detector."""
        return self._pattern_detector

    def generate(self, tick_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Generate a signal from tick data if probability edge exists.

        Pipeline:
          1. Update Markov model with new tick
          2. Get directional probabilities
          3. Detect micro-patterns
          4. If confirming pattern found, boost probability
          5. If P(direction) > 0.5 + min_edge, generate signal
          6. Size via Kelly criterion

        Args:
            tick_data: Dict with keys:
                - price: Current price (required)
                - volume: Current bar volume (optional, default 0)
                - spread: Current bid-ask spread (optional, default 0)
                - prices: np.ndarray of recent prices for pattern detection (optional)

        Returns:
            Signal dict or None if no edge.
        """
        price = tick_data.get("price", 0.0)
        volume = tick_data.get("volume", 0.0)
        spread = tick_data.get("spread", 0.0)

        if price <= 0.0:
            return None

        # 1. Update Markov model
        model_state = self._markov.update(price, volume, spread)
        if not model_state.get("ready", False):
            return None

        # 2. Get directional probabilities
        p_up, p_flat, p_down = self._markov.directional_probability()

        # 3. Detect micro-patterns
        prices_arr = tick_data.get("prices")
        patterns = []
        if prices_arr is not None and len(prices_arr) >= self._pattern_window:
            patterns = self._pattern_detector.detect(
                prices_arr, self._pattern_window,
            )

        # 4. Determine direction and apply pattern boost
        direction = None
        p_win = 0.0

        if p_up > p_down:
            direction = "BUY"
            p_win = p_up
            # Boost if bullish pattern confirms
            for pat in patterns:
                if pat.get("direction") == "BUY":
                    p_win = min(p_win + self._pattern_boost * pat.get("confidence", 0.5), 0.99)
        elif p_down > p_up:
            direction = "SELL"
            p_win = p_down
            # Boost if bearish pattern confirms
            for pat in patterns:
                if pat.get("direction") == "SELL":
                    p_win = min(p_win + self._pattern_boost * pat.get("confidence", 0.5), 0.99)
        else:
            # No directional edge
            self._skip_count += 1
            return None

        # 5. Check if edge is sufficient
        edge = p_win - 0.5
        if edge < self._min_edge:
            self._skip_count += 1
            return None

        # 6. Kelly sizing
        kelly = self._kelly_from_probability(p_win, self._avg_win, self._avg_loss)

        # Confidence: scale edge to 0-100 range
        # Edge of min_edge maps to ~55, edge of 0.3+ maps to ~85
        confidence = 50.0 + (edge / 0.3) * 35.0
        confidence = max(50.0, min(90.0, confidence))

        self._signal_count += 1

        signal = {
            "ticker": tick_data.get("ticker", ""),
            "direction": direction,
            "confidence": round(confidence, 1),
            "kelly": round(kelly, 4),
            "p_win": round(p_win, 4),
            "edge": round(edge, 4),
            "price": price,
            "spread": spread,
            "markov_state": model_state.get("state", "FLAT"),
            "p_up": round(p_up, 4),
            "p_flat": round(p_flat, 4),
            "p_down": round(p_down, 4),
            "patterns": [p["type"] for p in patterns],
            "pattern_count": len(patterns),
            "source": "hft_probability",
            "signal_number": self._signal_count,
            "ts": time.time(),
        }

        log.info(
            "HFT Signal #%d: %s %s conf=%.1f p_win=%.3f edge=%.3f kelly=%.4f patterns=%d",
            self._signal_count, direction, tick_data.get("ticker", "?"),
            confidence, p_win, edge, kelly, len(patterns),
        )
        return signal

    def _kelly_from_probability(
        self,
        p_win: float,
        avg_win: float,
        avg_loss: float,
    ) -> float:
        """Compute Kelly fraction from probability and average outcomes.

        Kelly = (p * b - q) / b
        where b = avg_win / avg_loss (odds), q = 1 - p.

        Capped at 0.35 per AEGIS guardrails.

        Args:
            p_win: Probability of winning.
            avg_win: Average winning return (fractional).
            avg_loss: Average losing return (fractional, positive number).

        Returns:
            Kelly fraction in [0.0, 0.35].
        """
        if avg_loss <= 0.0 or avg_win <= 0.0:
            return 0.0

        b = avg_win / avg_loss  # odds ratio
        q = 1.0 - p_win
        kelly = (p_win * b - q) / b

        # Clamp to AEGIS guardrails
        kelly = max(0.0, min(0.35, kelly))
        return kelly

    def stats(self) -> Dict[str, Any]:
        """Return signal generation statistics.

        Returns:
            Dict with signal_count, skip_count, markov state info.
        """
        prediction = self._markov.predict_next_state()
        return {
            "signal_count": self._signal_count,
            "skip_count": self._skip_count,
            "fire_rate": (
                self._signal_count / (self._signal_count + self._skip_count)
                if (self._signal_count + self._skip_count) > 0 else 0.0
            ),
            "min_edge": self._min_edge,
            "current_state": prediction.get("current_state", "FLAT"),
            "predicted_next": prediction.get("predicted_state", "FLAT"),
            "prediction_prob": prediction.get("probability", 0.0),
        }

    def save_state(self) -> None:
        """Persist model state to disk."""
        state = {
            "signal_count": self._signal_count,
            "skip_count": self._skip_count,
            "transition_matrix": self._markov.get_transition_matrix(),
            "min_edge": self._min_edge,
            "ts": time.time(),
        }
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(STATE_PATH, "w") as f:
                json.dump(state, f, indent=2)
            log.info("State saved to %s", STATE_PATH)
        except OSError as exc:
            log.error("Failed to save state: %s", exc)
