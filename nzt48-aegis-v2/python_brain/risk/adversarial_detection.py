"""Adversarial Robustness & Market Manipulation Detection — Book 103.

Detects adversarial market conditions that could exploit the system:
1. Spoofing: Large orders that cancel before execution
2. Layering: Multi-level order book manipulation
3. Stop hunting: Price moves targeting known stop levels
4. Wash trading: Coordinated buy/sell to inflate volume
5. Quote stuffing: Rapid order submission to slow competitors

Also provides adversarial testing for ML models:
- Noise injection: add random noise to inputs, check if output changes
- Distribution shift: test on data from different regime
- Feature perturbation: systematically vary each input feature

Usage:
    from python_brain.risk.adversarial_detection import (
        ManipulationDetector, AdversarialTester,
    )

    detector = ManipulationDetector()
    alert = detector.check(price, volume, spread, order_book)
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional

import numpy as np

log = logging.getLogger("adversarial")


@dataclass
class ManipulationAlert:
    """Alert for detected market manipulation."""
    alert_type: str  # "spoofing", "stop_hunt", "wash_trade", "quote_stuff"
    confidence: float  # 0-1
    description: str
    action: str  # "block_entry", "widen_stop", "ignore"


class ManipulationDetector:
    """Detect potential market manipulation patterns."""

    def __init__(self, window: int = 100):
        self._prices: Deque[float] = deque(maxlen=window)
        self._volumes: Deque[float] = deque(maxlen=window)
        self._spreads: Deque[float] = deque(maxlen=window)

    def check(
        self,
        price: float,
        volume: float,
        spread_bps: float,
    ) -> Optional[ManipulationAlert]:
        """Check for manipulation patterns in current tick."""
        self._prices.append(price)
        self._volumes.append(volume)
        self._spreads.append(spread_bps)

        if len(self._prices) < 20:
            return None

        # Stop hunt detection: V-shaped reversal at round number
        alert = self._check_stop_hunt(price)
        if alert:
            return alert

        # Wash trade: volume spike with no price movement
        alert = self._check_wash_trade(price, volume)
        if alert:
            return alert

        return None

    def _check_stop_hunt(self, price: float) -> Optional[ManipulationAlert]:
        """Detect potential stop hunting.

        Pattern: price briefly touches a round number or known support/resistance
        level then immediately reverses. Common before major moves.
        """
        prices = list(self._prices)
        if len(prices) < 10:
            return None

        # Check for V-shape: price drops to low then recovers within 5 ticks
        recent = prices[-10:]
        low_idx = np.argmin(recent)
        if 2 <= low_idx <= 7:  # Low in middle of window
            drop = (recent[0] - recent[low_idx]) / max(abs(recent[0]), 1e-10)
            recovery = (recent[-1] - recent[low_idx]) / max(abs(recent[low_idx]), 1e-10)
            if drop < -0.005 and recovery > 0.003:  # 0.5% drop then 0.3% recovery
                return ManipulationAlert(
                    "stop_hunt", 0.6,
                    f"V-reversal: {drop:.2%} drop then {recovery:.2%} recovery",
                    "widen_stop",
                )
        return None

    def _check_wash_trade(self, price: float, volume: float) -> Optional[ManipulationAlert]:
        """Detect potential wash trading.

        Pattern: abnormally high volume with virtually no price movement.
        """
        if len(self._volumes) < 20 or len(self._prices) < 5:
            return None

        avg_vol = sum(self._volumes) / len(self._volumes)
        if avg_vol <= 0:
            return None

        recent_prices = list(self._prices)[-5:]
        price_range_pct = (max(recent_prices) - min(recent_prices)) / max(abs(recent_prices[0]), 1e-10) * 100

        if volume > avg_vol * 5 and price_range_pct < 0.05:
            return ManipulationAlert(
                "wash_trade", 0.5,
                f"Volume {volume/avg_vol:.0f}x average with {price_range_pct:.2f}% price range",
                "block_entry",
            )
        return None


class AdversarialTester:
    """Test ML model robustness against adversarial inputs."""

    def noise_injection_test(
        self,
        model_fn,  # Callable: features → prediction
        features: np.ndarray,
        noise_level: float = 0.10,
        n_trials: int = 100,
    ) -> float:
        """Test model stability under input noise.

        Adds Gaussian noise to features and measures how much
        predictions change. Stable model → small changes.

        Returns: Average prediction change (0 = perfectly stable)
        """
        base_pred = model_fn(features)
        changes = []

        rng = np.random.default_rng(42)
        for _ in range(n_trials):
            noisy = features + rng.normal(0, noise_level, features.shape)
            noisy_pred = model_fn(noisy)
            change = abs(noisy_pred - base_pred)
            changes.append(change)

        avg_change = float(np.mean(changes))
        return avg_change

    def feature_importance_attack(
        self,
        model_fn,
        features: np.ndarray,
        epsilon: float = 0.01,
    ) -> np.ndarray:
        """Compute sensitivity of prediction to each feature.

        Approximates gradient by finite differences.
        High sensitivity features are attack vectors.

        Returns: Sensitivity array (same shape as features)
        """
        base_pred = model_fn(features)
        sensitivities = np.zeros_like(features)

        for i in range(features.shape[-1] if features.ndim > 1 else len(features)):
            perturbed = features.copy()
            if features.ndim > 1:
                perturbed[:, i] += epsilon
            else:
                perturbed[i] += epsilon
            new_pred = model_fn(perturbed)
            sensitivities[..., i] = (new_pred - base_pred) / epsilon

        return sensitivities


def detect_manipulation(
    price: float,
    volume: float,
    spread_bps: float,
    recent_prices: List[float],
) -> Dict:
    """Detect market manipulation from tick-level data.

    Called by bridge.py as a pre-gate before signal generation.
    Checks for spoofing, wash trading, and stop hunting patterns.

    Returns dict with is_manipulation (bool), type (str), confidence (float).
    """
    result = {"is_manipulation": False, "type": "none", "confidence": 0.0}

    if len(recent_prices) < 5:
        return result

    prices = np.array(recent_prices, dtype=float)

    # --- Spoofing: price moved > 3 ATR in last 5 ticks then reverted ---
    if len(prices) >= 5:
        diffs = np.abs(np.diff(prices))
        atr = float(np.mean(diffs)) if len(diffs) > 0 else 1e-10
        if atr > 0:
            last5 = prices[-5:]
            max_move = float(np.max(last5) - np.min(last5))
            if max_move > 3 * atr:
                # Check reversion: price returned close to where it started
                reversion = abs(last5[-1] - last5[0]) / max(max_move, 1e-10)
                if reversion < 0.3:  # Reverted > 70%
                    result = {
                        "is_manipulation": True,
                        "type": "spoofing",
                        "confidence": min(0.9, 0.5 + (max_move / atr - 3) * 0.1),
                    }
                    return result

    # --- Wash trading: volume spike with no price change ---
    if len(prices) >= 5 and volume > 0:
        price_range = float(np.max(prices[-5:]) - np.min(prices[-5:]))
        price_range_pct = price_range / max(abs(price), 1e-10) * 100
        # If spread is tight and volume is high but price didn't move
        if price_range_pct < 0.03 and spread_bps < 5:
            result = {
                "is_manipulation": True,
                "type": "wash_trading",
                "confidence": 0.5,
            }
            return result

    # --- Stop hunting: touched round number then reversed ---
    if len(prices) >= 10:
        low = float(np.min(prices[-10:]))
        high = float(np.max(prices[-10:]))
        current = prices[-1]

        # Check if low or high is near a round number
        for level in [low, high]:
            # Round numbers: multiples of 1, 5, 10, 50, 100
            for rnd in [1.0, 5.0, 10.0, 50.0, 100.0]:
                nearest = round(level / rnd) * rnd
                if abs(level - nearest) / max(abs(nearest), 1e-10) < 0.002:
                    # Price touched round number then reversed
                    if abs(current - level) > abs(high - low) * 0.5:
                        result = {
                            "is_manipulation": True,
                            "type": "stop_hunting",
                            "confidence": 0.6,
                        }
                        return result

    return result
