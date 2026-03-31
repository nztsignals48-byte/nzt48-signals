"""Volume-Synchronized PIN (VPIN) for Flow Toxicity Detection — Book 162.

VPIN estimates the probability of informed trading using volume-synchronized
sampling. Unlike traditional PIN estimation (maximum likelihood on daily
data), VPIN provides real-time toxicity estimates by bucketing trades into
equal-volume bars and measuring order flow imbalance.

Key insight: Toxic flow (large informed trades) precedes adverse price
moves. High VPIN signals that market makers face elevated adverse selection
risk, making it a leading indicator for:
  - Flash crash risk
  - Spread widening
  - Liquidity dry-ups
  - Signal confidence adjustment

Method:
  1. Classify each trade as buy/sell using the tick rule
  2. Accumulate trades into equal-volume buckets
  3. Compute |V_buy - V_sell| / V_total for each bucket
  4. Average over n rolling buckets = VPIN

VPIN > 0.7 indicates highly toxic flow (rare but dangerous).
VPIN > 0.5 indicates elevated toxicity.
VPIN < 0.3 indicates normal market-making conditions.

State persisted to /app/data/vpin/.

Usage:
    from python_brain.analytics.vpin_calculator import (
        VPINCalculator, VPINConfig, VolumeBucket,
    )
    config = VPINConfig(bucket_size=50, n_buckets=50)
    calc = VPINCalculator(config)
    for price, volume in trades:
        vpin = calc.update(price, volume)
        if vpin and calc.is_toxic(vpin):
            signal_confidence *= 0.5  # Reduce confidence in toxic flow
"""

from __future__ import annotations

import json
import logging
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("vpin_calculator")

__all__ = [
    "VPINConfig",
    "VolumeBucket",
    "VPINCalculator",
]

# ── Constants ──────────────────────────────────────────────────────────

STATE_DIR = Path("/app/data/vpin")
DEFAULT_TOXIC_THRESHOLD = 0.70
ELEVATED_THRESHOLD = 0.50


# ── Config ─────────────────────────────────────────────────────────────

@dataclass
class VPINConfig:
    """Configuration for VPIN calculator.

    Attributes:
        bucket_size: Volume per bucket (in shares/units).
        n_buckets: Number of buckets for rolling VPIN average.
        sigma_window: Lookback for volume sigma estimation.
        toxicity_threshold: VPIN level considered toxic.
        elevated_threshold: VPIN level considered elevated.
    """
    bucket_size: int = 50
    n_buckets: int = 50
    sigma_window: int = 20
    toxicity_threshold: float = DEFAULT_TOXIC_THRESHOLD
    elevated_threshold: float = ELEVATED_THRESHOLD


# ── Volume Bucket ─────────────────────────────────────────────────────

class VolumeBucket:
    """Accumulates trades into a fixed-volume bucket.

    Each bucket fills up with trades until the total volume reaches
    bucket_size. Buy and sell volumes are tracked separately.
    """

    def __init__(self, bucket_size: int):
        """Initialize volume bucket.

        Args:
            bucket_size: Target volume for the bucket.
        """
        self.bucket_size = bucket_size
        self._buy_vol: float = 0.0
        self._sell_vol: float = 0.0
        self._total_vol: float = 0.0
        self._n_trades: int = 0
        self._prices: List[float] = []

    def add_trade(self, price: float, volume: float,
                  side: str) -> bool:
        """Add a trade to the bucket.

        If the trade would overflow the bucket, only the portion
        needed to fill it is added (remainder handled by caller).

        Args:
            price: Trade price.
            volume: Trade volume (always positive).
            side: 'buy' or 'sell'.

        Returns:
            True when the bucket is full (reached bucket_size).
        """
        volume = abs(volume)
        remaining_capacity = self.bucket_size - self._total_vol

        if remaining_capacity <= 0:
            return True

        # How much of this trade fills the bucket
        fill_amount = min(volume, remaining_capacity)

        if side == "buy":
            self._buy_vol += fill_amount
        else:
            self._sell_vol += fill_amount

        self._total_vol += fill_amount
        self._n_trades += 1
        self._prices.append(price)

        return self._total_vol >= self.bucket_size

    def buy_volume(self) -> float:
        """Total buy volume in this bucket."""
        return self._buy_vol

    def sell_volume(self) -> float:
        """Total sell volume in this bucket."""
        return self._sell_vol

    @property
    def total_volume(self) -> float:
        """Total accumulated volume."""
        return self._total_vol

    @property
    def imbalance(self) -> float:
        """Order flow imbalance = |V_buy - V_sell| / V_total."""
        if self._total_vol < 1e-10:
            return 0.0
        return abs(self._buy_vol - self._sell_vol) / self._total_vol

    @property
    def is_full(self) -> bool:
        """Whether the bucket has reached target volume."""
        return self._total_vol >= self.bucket_size

    @property
    def n_trades(self) -> int:
        """Number of trades in this bucket."""
        return self._n_trades

    @property
    def vwap(self) -> float:
        """Volume-weighted average price (approximation)."""
        if not self._prices:
            return 0.0
        return float(np.mean(self._prices))

    def reset(self) -> None:
        """Reset the bucket for reuse."""
        self._buy_vol = 0.0
        self._sell_vol = 0.0
        self._total_vol = 0.0
        self._n_trades = 0
        self._prices = []

    def to_dict(self) -> Dict[str, Any]:
        """Serialize bucket state."""
        return {
            "buy_volume": round(self._buy_vol, 2),
            "sell_volume": round(self._sell_vol, 2),
            "total_volume": round(self._total_vol, 2),
            "imbalance": round(self.imbalance, 4),
            "n_trades": self._n_trades,
        }


# ── VPIN Calculator ───────────────────────────────────────────────────

class VPINCalculator:
    """Volume-Synchronized Probability of Informed Trading calculator.

    Processes trade-by-trade data, classifies trades using the tick rule,
    accumulates into volume buckets, and computes rolling VPIN.
    """

    def __init__(self, config: Optional[VPINConfig] = None):
        """Initialize VPIN calculator.

        Args:
            config: VPINConfig. Uses defaults if None.
        """
        self._config = config or VPINConfig()
        self._current_bucket = VolumeBucket(self._config.bucket_size)
        self._completed_buckets: deque = deque(maxlen=self._config.n_buckets)
        self._prev_price: Optional[float] = None
        self._current_vpin: Optional[float] = None
        self._n_trades: int = 0
        self._n_buckets_completed: int = 0

        # Historical VPIN values for percentile computation
        self._vpin_history: deque = deque(maxlen=self._config.sigma_window * self._config.n_buckets)

        # Volume statistics
        self._daily_volumes: deque = deque(maxlen=self._config.sigma_window)

        log.info("VPINCalculator: bucket_size=%d, n_buckets=%d, "
                 "sigma_window=%d",
                 self._config.bucket_size, self._config.n_buckets,
                 self._config.sigma_window)

    def update(self, price: float, volume: float) -> Optional[float]:
        """Process a new trade and return VPIN if a new bucket completed.

        Args:
            price: Trade price.
            volume: Trade volume.

        Returns:
            Current VPIN value if a new bucket just completed, else None.
        """
        if volume <= 0:
            return None

        self._n_trades += 1

        # Classify trade direction using tick rule
        side = self._classify_trade(price, self._prev_price)
        self._prev_price = price

        # Handle volume that may span multiple buckets
        remaining_volume = abs(volume)

        while remaining_volume > 0:
            capacity = self._config.bucket_size - self._current_bucket.total_volume
            trade_vol = min(remaining_volume, capacity)

            is_full = self._current_bucket.add_trade(price, trade_vol, side)
            remaining_volume -= trade_vol

            if is_full:
                # Store completed bucket
                self._completed_buckets.append(self._current_bucket)
                self._n_buckets_completed += 1

                # Compute VPIN if we have enough buckets
                if len(self._completed_buckets) >= self._config.n_buckets:
                    self._current_vpin = self._compute_vpin(
                        list(self._completed_buckets)
                    )
                    self._vpin_history.append(self._current_vpin)

                    if self._current_vpin > self._config.toxicity_threshold:
                        log.warning("TOXIC FLOW detected: VPIN=%.4f (threshold=%.2f)",
                                    self._current_vpin, self._config.toxicity_threshold)

                    # Start new bucket
                    self._current_bucket = VolumeBucket(self._config.bucket_size)
                    return self._current_vpin
                else:
                    self._current_bucket = VolumeBucket(self._config.bucket_size)

        return None

    def _classify_trade(self, price: float,
                        prev_price: Optional[float]) -> str:
        """Classify trade as buy or sell using the tick rule.

        Tick rule: uptick = buy-initiated, downtick = sell-initiated.
        Zero-tick inherits the previous classification.

        Args:
            price: Current trade price.
            prev_price: Previous trade price.

        Returns:
            'buy' or 'sell'.
        """
        if prev_price is None:
            return "buy"  # Default for first trade

        if price > prev_price:
            return "buy"   # Uptick
        elif price < prev_price:
            return "sell"  # Downtick
        else:
            return "buy"   # Zero-tick defaults to buy (convention)

    def _compute_vpin(self, buckets: List[VolumeBucket]) -> float:
        """Compute VPIN from completed buckets.

        VPIN = mean(|V_buy_i - V_sell_i| / V_total_i) for i in buckets

        Args:
            buckets: List of completed VolumeBucket objects.

        Returns:
            VPIN value in [0, 1].
        """
        if not buckets:
            return 0.0

        n = min(len(buckets), self._config.n_buckets)
        recent = buckets[-n:]

        imbalances = []
        for bucket in recent:
            total = bucket.total_volume
            if total > 0:
                imb = abs(bucket.buy_volume() - bucket.sell_volume()) / total
                imbalances.append(imb)

        if not imbalances:
            return 0.0

        return float(np.mean(imbalances))

    def is_toxic(self, vpin: float,
                 threshold: Optional[float] = None) -> bool:
        """Check if the current VPIN indicates toxic flow.

        Args:
            vpin: VPIN value to check.
            threshold: Override threshold. Uses config if None.

        Returns:
            True if flow is considered toxic.
        """
        t = threshold if threshold is not None else self._config.toxicity_threshold
        return vpin >= t

    def is_elevated(self, vpin: Optional[float] = None) -> bool:
        """Check if VPIN is elevated (above normal but not toxic).

        Args:
            vpin: VPIN value. Uses current if None.

        Returns:
            True if flow toxicity is elevated.
        """
        v = vpin if vpin is not None else self._current_vpin
        if v is None:
            return False
        return v >= self._config.elevated_threshold

    def get_percentile(self, vpin: Optional[float] = None) -> float:
        """Get the historical percentile rank of a VPIN value.

        A VPIN at the 95th percentile means it's higher than 95%
        of historically observed values.

        Args:
            vpin: VPIN value. Uses current if None.

        Returns:
            Percentile [0, 100].
        """
        v = vpin if vpin is not None else self._current_vpin
        if v is None or len(self._vpin_history) < 5:
            return 50.0

        history = np.array(self._vpin_history)
        percentile = float(np.sum(history <= v) / len(history) * 100)
        return round(percentile, 1)

    @property
    def current_vpin(self) -> Optional[float]:
        """Most recently computed VPIN value."""
        return self._current_vpin

    @property
    def summary(self) -> Dict[str, Any]:
        """Calculator summary."""
        result: Dict[str, Any] = {
            "n_trades_processed": self._n_trades,
            "n_buckets_completed": self._n_buckets_completed,
            "current_vpin": round(self._current_vpin, 4) if self._current_vpin else None,
            "is_toxic": self.is_toxic(self._current_vpin) if self._current_vpin else False,
            "is_elevated": self.is_elevated(),
            "percentile": self.get_percentile(),
            "config": {
                "bucket_size": self._config.bucket_size,
                "n_buckets": self._config.n_buckets,
                "toxicity_threshold": self._config.toxicity_threshold,
            },
        }
        if len(self._vpin_history) > 0:
            hist = np.array(self._vpin_history)
            result["vpin_stats"] = {
                "mean": round(float(np.mean(hist)), 4),
                "std": round(float(np.std(hist)), 4),
                "min": round(float(np.min(hist)), 4),
                "max": round(float(np.max(hist)), 4),
                "p95": round(float(np.percentile(hist, 95)), 4),
                "n_samples": len(hist),
            }
        result["current_bucket"] = {
            "fill_pct": round(
                self._current_bucket.total_volume / max(self._config.bucket_size, 1) * 100, 1
            ),
            "n_trades": self._current_bucket.n_trades,
        }
        return result

    def save(self, path: str = "/app/data/vpin/state.json") -> None:
        """Persist calculator state."""
        save_path = Path(path)
        try:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "n_trades": self._n_trades,
                "n_buckets_completed": self._n_buckets_completed,
                "current_vpin": self._current_vpin,
                "prev_price": self._prev_price,
                "vpin_history": list(self._vpin_history),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            with open(str(save_path), "w") as f:
                json.dump(state, f, indent=2)
            log.info("VPIN state saved to %s", path)
        except Exception as e:
            log.error("Failed to save VPIN state: %s", e)

    def load(self, path: str = "/app/data/vpin/state.json") -> None:
        """Load calculator state."""
        try:
            with open(path, "r") as f:
                state = json.load(f)
            self._n_trades = state.get("n_trades", 0)
            self._n_buckets_completed = state.get("n_buckets_completed", 0)
            self._current_vpin = state.get("current_vpin")
            self._prev_price = state.get("prev_price")
            for v in state.get("vpin_history", []):
                self._vpin_history.append(v)
            log.info("VPIN state loaded: %d trades, VPIN=%s",
                     self._n_trades,
                     f"{self._current_vpin:.4f}" if self._current_vpin else "N/A")
        except FileNotFoundError:
            log.info("No saved VPIN state at %s", path)
        except Exception as e:
            log.error("Failed to load VPIN state: %s", e)
