"""AI Market Making & Liquidity Provision — Book 202.

Avellaneda-Stoikov market making framework for ISA-legal liquidity provision
on illiquid LSE leveraged ETPs. The core model computes optimal bid/ask quotes
by balancing spread revenue against inventory risk and adverse selection.

Key equations:
  Reservation price: r = mid - q * gamma * sigma^2 * (T - t)
  Optimal spread:    delta = gamma * sigma^2 * T + (2/gamma) * ln(1 + gamma/k)

Components:
  - ASConfig: Avellaneda-Stoikov model parameters
  - AvellanedaStoikov: Core quoting model
  - InventoryManager: Inventory skew and risk penalty
  - AdverseSelectionDetector: Detects informed-trader flow
  - MarketMakingSignal: Top-level signal generator combining all components

Data paths:
  - /app/data/market_maker_state.json — persisted inventory + quote history
  - /app/data/market_maker_fills.ndjson — fill log for adverse selection analysis

Bridge.py integration:
    try:
        from python_brain.execution.market_maker import (
            MarketMakingSignal, AvellanedaStoikov, InventoryManager,
            AdverseSelectionDetector, ASConfig,
        )
    except ImportError:
        pass

Usage:
    config = ASConfig(gamma=0.1, sigma=0.02, k=1.5, T=1.0, dt=5.0/28800)
    mm = MarketMakingSignal(config)
    result = mm.evaluate(mid=15.42, bid=15.40, ask=15.44,
                         inventory=2, vol=0.02, time_remaining=0.5)
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

log = logging.getLogger("market_maker")

__all__ = [
    "ASConfig",
    "AvellanedaStoikov",
    "InventoryManager",
    "AdverseSelectionDetector",
    "MarketMakingSignal",
]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR = Path("/app/data")
STATE_PATH = DATA_DIR / "market_maker_state.json"
FILLS_PATH = DATA_DIR / "market_maker_fills.ndjson"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class ASConfig:
    """Avellaneda-Stoikov model configuration.

    Attributes:
        gamma: Risk aversion parameter. Higher = wider spreads, less inventory risk.
               Typical range: 0.01 - 1.0. Default 0.1.
        sigma: Volatility of the instrument (annualised or session-normalised).
               For LSE 3x ETPs, typical intraday sigma ~ 0.02 - 0.05.
        k:     Order arrival intensity (Poisson rate). Higher k = more fills at
               given spread. Estimated from historical fill rates. Default 1.5.
        T:     Trading horizon in normalised time units (1.0 = full session).
        dt:    Time step size. For 5-second bars in 8-hour session: 5/28800.
    """
    gamma: float = 0.1
    sigma: float = 0.02
    k: float = 1.5
    T: float = 1.0
    dt: float = 5.0 / 28800.0


# ---------------------------------------------------------------------------
# Core Avellaneda-Stoikov Model
# ---------------------------------------------------------------------------
class AvellanedaStoikov:
    """Implements the Avellaneda-Stoikov optimal market making model.

    The model computes a reservation price (fair value adjusted for inventory)
    and an optimal spread around it. Quotes are then:
        bid = reservation_price - half_spread
        ask = reservation_price + half_spread
    """

    def __init__(self, config: ASConfig) -> None:
        """Initialise with model configuration.

        Args:
            config: ASConfig with gamma, sigma, k, T, dt parameters.
        """
        self._config = config
        self._quote_history: Deque[Dict[str, Any]] = deque(maxlen=5000)
        log.info(
            "AvellanedaStoikov initialised: gamma=%.4f sigma=%.4f k=%.2f T=%.2f",
            config.gamma, config.sigma, config.k, config.T,
        )

    @property
    def config(self) -> ASConfig:
        """Return current configuration."""
        return self._config

    def reservation_price(
        self,
        mid: float,
        q: int,
        sigma: float,
        gamma: float,
        T_remaining: float,
    ) -> float:
        """Compute the reservation price — the market maker's true fair value.

        The reservation price is the mid adjusted for inventory risk:
            r = mid - q * gamma * sigma^2 * T_remaining

        When long (q > 0), reservation price is BELOW mid (willing to sell cheaper).
        When short (q < 0), reservation price is ABOVE mid (willing to buy higher).

        Args:
            mid: Current mid price of the instrument.
            q: Current inventory position (positive = long, negative = short).
            sigma: Current volatility estimate.
            gamma: Risk aversion parameter.
            T_remaining: Time remaining in session (0.0 to 1.0).

        Returns:
            Reservation price as float.
        """
        if T_remaining <= 0.0:
            return mid
        r = mid - q * gamma * (sigma ** 2) * T_remaining
        return r

    def optimal_spread(
        self,
        sigma: float,
        gamma: float,
        k: float,
        T_remaining: float = 1.0,
    ) -> float:
        """Compute the optimal full spread (bid-ask distance).

        Optimal spread from the AS model:
            delta = gamma * sigma^2 * T + (2/gamma) * ln(1 + gamma/k)

        First term: compensation for inventory risk over remaining time.
        Second term: compensation for adverse selection (order arrival).

        Args:
            sigma: Volatility estimate.
            gamma: Risk aversion parameter.
            k: Order arrival intensity.
            T_remaining: Time remaining in session (0.0 to 1.0).

        Returns:
            Optimal full spread (distance from bid to ask) in price units.
        """
        if gamma <= 0.0:
            log.warning("gamma must be > 0, got %.6f; using 0.01", gamma)
            gamma = 0.01
        if k <= 0.0:
            log.warning("k must be > 0, got %.6f; using 0.1", k)
            k = 0.1

        inventory_term = gamma * (sigma ** 2) * max(T_remaining, 0.0)
        adverse_term = (2.0 / gamma) * math.log(1.0 + gamma / k)
        spread = inventory_term + adverse_term
        return spread

    def quote(
        self,
        mid: float,
        inventory: int,
        time_remaining: float,
        sigma_override: Optional[float] = None,
    ) -> Tuple[float, float]:
        """Generate optimal bid and ask quotes.

        Combines reservation price and optimal spread to produce actionable quotes.

        Args:
            mid: Current mid price.
            inventory: Current inventory position.
            time_remaining: Fraction of session remaining (0.0 to 1.0).
            sigma_override: Override volatility (uses config.sigma if None).

        Returns:
            Tuple of (bid_price, ask_price).
        """
        sigma = sigma_override if sigma_override is not None else self._config.sigma
        gamma = self._config.gamma
        k = self._config.k

        r = self.reservation_price(mid, inventory, sigma, gamma, time_remaining)
        full_spread = self.optimal_spread(sigma, gamma, k, time_remaining)
        half_spread = full_spread / 2.0

        bid = r - half_spread
        ask = r + half_spread

        # Ensure bid < ask and both positive
        bid = max(bid, mid * 0.95)
        ask = max(ask, bid + mid * 0.0001)

        record = {
            "ts": time.time(),
            "mid": mid,
            "reservation": r,
            "bid": bid,
            "ask": ask,
            "spread": ask - bid,
            "inventory": inventory,
            "time_remaining": time_remaining,
        }
        self._quote_history.append(record)

        log.debug(
            "Quote: mid=%.4f r=%.4f bid=%.4f ask=%.4f spread=%.4f inv=%d T=%.4f",
            mid, r, bid, ask, ask - bid, inventory, time_remaining,
        )
        return (bid, ask)

    def get_quote_history(self) -> List[Dict[str, Any]]:
        """Return recent quote history for analysis."""
        return list(self._quote_history)


# ---------------------------------------------------------------------------
# Inventory Management
# ---------------------------------------------------------------------------
class InventoryManager:
    """Manages inventory risk and computes skew adjustments.

    The market maker should skew quotes to reduce inventory:
      - Long inventory → lower ask (encourage selling) → skew < 0
      - Short inventory → raise bid (encourage buying) → skew > 0

    Also computes a quadratic risk penalty for position monitoring.
    """

    def __init__(
        self,
        max_inventory: int = 10,
        target: int = 0,
    ) -> None:
        """Initialise inventory manager.

        Args:
            max_inventory: Maximum absolute inventory before halting quotes.
            target: Target inventory level (usually 0 for market-neutral).
        """
        self._max_inventory = max(max_inventory, 1)
        self._target = target
        self._inventory_history: Deque[Tuple[float, int]] = deque(maxlen=2000)
        log.info(
            "InventoryManager: max=%d target=%d",
            self._max_inventory, self._target,
        )

    @property
    def max_inventory(self) -> int:
        """Return maximum allowed inventory."""
        return self._max_inventory

    @property
    def target(self) -> int:
        """Return target inventory level."""
        return self._target

    def should_skew(self, current_inventory: int) -> float:
        """Compute quote skew factor based on inventory deviation from target.

        Returns a skew factor in [-1.0, +1.0]:
          - Positive skew: raise bid / lower ask → encourage buying from us (reduce long)
          - Negative skew: lower bid / raise ask → encourage selling to us (reduce short)
          - Zero: balanced — no skew needed.

        The skew is linear in the ratio of deviation to max inventory.

        Args:
            current_inventory: Current inventory position.

        Returns:
            Skew factor in [-1.0, +1.0].
        """
        deviation = current_inventory - self._target
        skew = -float(deviation) / float(self._max_inventory)
        skew = max(-1.0, min(1.0, skew))

        self._inventory_history.append((time.time(), current_inventory))

        log.debug(
            "Inventory skew: inv=%d target=%d deviation=%d skew=%.4f",
            current_inventory, self._target, deviation, skew,
        )
        return skew

    def risk_penalty(self, inventory: int) -> float:
        """Compute quadratic risk penalty for current inventory.

        Penalty = (inventory / max_inventory)^2
        Used to reduce position size or widen spreads as inventory grows.

        At max_inventory, penalty = 1.0 (full penalty).
        At zero inventory, penalty = 0.0.

        Args:
            inventory: Current inventory position.

        Returns:
            Risk penalty in [0.0, 1.0+] (can exceed 1.0 if over max).
        """
        ratio = float(abs(inventory)) / float(self._max_inventory)
        penalty = ratio ** 2
        return penalty

    def is_at_limit(self, inventory: int) -> bool:
        """Check if inventory is at or beyond maximum limit.

        Args:
            inventory: Current inventory position.

        Returns:
            True if absolute inventory >= max_inventory.
        """
        return abs(inventory) >= self._max_inventory

    def side_allowed(self, inventory: int) -> Dict[str, bool]:
        """Determine which sides we can still quote on.

        If at +max, stop quoting bids (don't accumulate more long).
        If at -max, stop quoting asks (don't accumulate more short).

        Args:
            inventory: Current inventory position.

        Returns:
            Dict with 'bid_allowed' and 'ask_allowed' booleans.
        """
        return {
            "bid_allowed": inventory < self._max_inventory,
            "ask_allowed": inventory > -self._max_inventory,
        }

    def inventory_stats(self) -> Dict[str, Any]:
        """Compute inventory statistics from history.

        Returns:
            Dict with mean, std, max, min inventory over history window.
        """
        if not self._inventory_history:
            return {"mean": 0.0, "std": 0.0, "max": 0, "min": 0, "count": 0}

        inventories = [inv for _, inv in self._inventory_history]
        arr = np.array(inventories, dtype=np.float64)
        return {
            "mean": float(np.mean(arr)),
            "std": float(np.std(arr)),
            "max": int(np.max(arr)),
            "min": int(np.min(arr)),
            "count": len(inventories),
        }


# ---------------------------------------------------------------------------
# Adverse Selection Detection
# ---------------------------------------------------------------------------
class AdverseSelectionDetector:
    """Detects informed-trader flow (adverse selection).

    When fills consistently move against us immediately after execution,
    we are being picked off by informed traders. This detector measures
    the fraction of fills that result in adverse price movement within
    a short window.

    A high adverse selection ratio means we should widen spreads or
    temporarily pull quotes.
    """

    def __init__(self, lookback: int = 100) -> None:
        """Initialise adverse selection detector.

        Args:
            lookback: Number of recent fills to analyse.
        """
        self._lookback = lookback
        self._fills: Deque[Dict[str, Any]] = deque(maxlen=lookback)
        log.info("AdverseSelectionDetector: lookback=%d", lookback)

    def record_fill(
        self,
        fill_side: str,
        fill_price: float,
        mid_at_fill: float,
        mid_after: float,
        fill_time: Optional[float] = None,
    ) -> None:
        """Record a fill for adverse selection analysis.

        Args:
            fill_side: 'buy' or 'sell' — which side was filled.
            fill_price: Price at which the fill occurred.
            mid_at_fill: Mid price at time of fill.
            mid_after: Mid price a short time after fill (e.g., 5-30 seconds).
            fill_time: Timestamp of fill (uses current time if None).
        """
        if fill_time is None:
            fill_time = time.time()

        # Adverse = price moved against our position after fill
        if fill_side == "buy":
            # We bought. If mid dropped after, that's adverse.
            adverse = mid_after < mid_at_fill
            pnl_impact = mid_after - fill_price
        elif fill_side == "sell":
            # We sold. If mid rose after, that's adverse.
            adverse = mid_after > mid_at_fill
            pnl_impact = fill_price - mid_after
        else:
            log.warning("Unknown fill_side: %s", fill_side)
            return

        self._fills.append({
            "ts": fill_time,
            "side": fill_side,
            "fill_price": fill_price,
            "mid_at_fill": mid_at_fill,
            "mid_after": mid_after,
            "adverse": adverse,
            "pnl_impact": pnl_impact,
        })

    def detect(self, recent_fills: Optional[List[Dict[str, Any]]] = None) -> float:
        """Compute adverse selection ratio from recent fills.

        If recent_fills is provided, uses those directly.
        Otherwise uses internally recorded fills.

        Args:
            recent_fills: Optional list of fill dicts with 'adverse' key (bool).
                          If None, uses internal fill history.

        Returns:
            Fraction of fills that were adverse (0.0 to 1.0).
            Returns 0.0 if no fills recorded.
        """
        fills = recent_fills if recent_fills is not None else list(self._fills)
        if not fills:
            return 0.0

        n_adverse = sum(1 for f in fills if f.get("adverse", False))
        ratio = n_adverse / len(fills)

        log.debug(
            "Adverse selection: %d/%d fills adverse (%.2f%%)",
            n_adverse, len(fills), ratio * 100,
        )
        return ratio

    def should_widen(
        self,
        adverse_ratio: Optional[float] = None,
        threshold: float = 0.6,
    ) -> bool:
        """Determine if spreads should be widened due to adverse selection.

        Args:
            adverse_ratio: Pre-computed ratio. If None, computes from internal fills.
            threshold: Ratio above which widening is recommended. Default 0.6.

        Returns:
            True if adverse selection ratio exceeds threshold.
        """
        if adverse_ratio is None:
            adverse_ratio = self.detect()
        should = adverse_ratio > threshold
        if should:
            log.warning(
                "Adverse selection HIGH (%.2f > %.2f) — recommend widening spreads",
                adverse_ratio, threshold,
            )
        return should

    def adverse_pnl_impact(self) -> float:
        """Compute total P&L impact from adverse fills.

        Returns:
            Sum of P&L impacts from all recorded fills. Negative = losing money.
        """
        return sum(f.get("pnl_impact", 0.0) for f in self._fills)

    def fill_stats(self) -> Dict[str, Any]:
        """Return summary statistics of fill history.

        Returns:
            Dict with count, adverse_count, adverse_ratio, total_pnl.
        """
        fills = list(self._fills)
        if not fills:
            return {
                "count": 0,
                "adverse_count": 0,
                "adverse_ratio": 0.0,
                "total_pnl": 0.0,
            }

        n_adverse = sum(1 for f in fills if f.get("adverse", False))
        total_pnl = sum(f.get("pnl_impact", 0.0) for f in fills)
        return {
            "count": len(fills),
            "adverse_count": n_adverse,
            "adverse_ratio": n_adverse / len(fills),
            "total_pnl": total_pnl,
        }


# ---------------------------------------------------------------------------
# Top-Level Market Making Signal
# ---------------------------------------------------------------------------
class MarketMakingSignal:
    """Combines Avellaneda-Stoikov quoting, inventory management, and adverse
    selection detection into a single signal generator.

    Produces a quote recommendation dict with bid, ask, spread, confidence,
    inventory status, and whether to widen or pull quotes.
    """

    def __init__(
        self,
        config: Optional[ASConfig] = None,
        max_inventory: int = 10,
        adverse_lookback: int = 100,
        adverse_threshold: float = 0.6,
        min_spread_bps: float = 10.0,
        max_spread_bps: float = 200.0,
    ) -> None:
        """Initialise the market making signal generator.

        Args:
            config: ASConfig for the quoting model. Uses defaults if None.
            max_inventory: Maximum absolute inventory before halting.
            adverse_lookback: Number of fills for adverse selection window.
            adverse_threshold: Adverse ratio above which to widen.
            min_spread_bps: Minimum spread in basis points (floor).
            max_spread_bps: Maximum spread in basis points (ceiling).
        """
        self._config = config or ASConfig()
        self._model = AvellanedaStoikov(self._config)
        self._inventory_mgr = InventoryManager(
            max_inventory=max_inventory,
            target=0,
        )
        self._adverse_detector = AdverseSelectionDetector(
            lookback=adverse_lookback,
        )
        self._adverse_threshold = adverse_threshold
        self._min_spread_bps = min_spread_bps
        self._max_spread_bps = max_spread_bps
        self._eval_count: int = 0
        log.info(
            "MarketMakingSignal initialised: min_spread=%.1fbps max_spread=%.1fbps",
            min_spread_bps, max_spread_bps,
        )

    @property
    def model(self) -> AvellanedaStoikov:
        """Return the underlying AS model."""
        return self._model

    @property
    def inventory_manager(self) -> InventoryManager:
        """Return the inventory manager."""
        return self._inventory_mgr

    @property
    def adverse_detector(self) -> AdverseSelectionDetector:
        """Return the adverse selection detector."""
        return self._adverse_detector

    def evaluate(
        self,
        mid: float,
        bid: float,
        ask: float,
        inventory: int,
        vol: float,
        time_remaining: float,
    ) -> Dict[str, Any]:
        """Generate a complete market making quote recommendation.

        Combines:
          1. AS model for optimal reservation price + spread
          2. Inventory skew adjustment
          3. Adverse selection widening
          4. Spread floor/ceiling enforcement

        Args:
            mid: Current mid price.
            bid: Current best bid in the market.
            ask: Current best ask in the market.
            inventory: Current inventory position (shares held).
            vol: Current volatility estimate (annualised or session-normalised).
            time_remaining: Fraction of trading session remaining (0.0 to 1.0).

        Returns:
            Dict containing:
              - our_bid: Recommended bid price
              - our_ask: Recommended ask price
              - spread_bps: Spread in basis points
              - reservation_price: AS reservation price
              - skew: Inventory skew factor
              - risk_penalty: Quadratic inventory penalty
              - adverse_ratio: Adverse selection ratio
              - widen: Whether adverse selection recommends widening
              - sides_allowed: Which sides we can quote
              - confidence: Overall confidence in the quote (0-100)
              - action: 'QUOTE_BOTH', 'QUOTE_BID_ONLY', 'QUOTE_ASK_ONLY', 'PULL_QUOTES'
        """
        self._eval_count += 1

        if mid <= 0.0:
            log.error("Invalid mid price: %.6f", mid)
            return {"action": "PULL_QUOTES", "reason": "invalid_mid", "confidence": 0}

        if time_remaining <= 0.0:
            log.info("Session ended — pulling quotes")
            return {"action": "PULL_QUOTES", "reason": "session_ended", "confidence": 0}

        # 1. Core AS model
        our_bid, our_ask = self._model.quote(
            mid, inventory, time_remaining, sigma_override=vol,
        )
        reservation = self._model.reservation_price(
            mid, inventory, vol, self._config.gamma, time_remaining,
        )

        # 2. Inventory management
        skew = self._inventory_mgr.should_skew(inventory)
        risk_pen = self._inventory_mgr.risk_penalty(inventory)
        sides = self._inventory_mgr.side_allowed(inventory)

        # Apply skew: shift quotes in the direction that reduces inventory
        skew_adjustment = skew * mid * 0.001  # up to 10bps skew
        our_bid += skew_adjustment
        our_ask += skew_adjustment

        # 3. Adverse selection check
        adverse_ratio = self._adverse_detector.detect()
        widen = self._adverse_detector.should_widen(
            adverse_ratio, self._adverse_threshold,
        )

        # If adverse selection is high, widen spread by 50%
        if widen:
            current_spread = our_ask - our_bid
            extra_spread = current_spread * 0.25  # add 25% each side
            our_bid -= extra_spread
            our_ask += extra_spread
            log.info("Widening spread due to adverse selection: ratio=%.3f", adverse_ratio)

        # 4. Enforce spread bounds
        spread = our_ask - our_bid
        spread_bps = (spread / mid) * 10000.0

        if spread_bps < self._min_spread_bps:
            target_spread = mid * self._min_spread_bps / 10000.0
            half_adj = (target_spread - spread) / 2.0
            our_bid -= half_adj
            our_ask += half_adj
            spread_bps = self._min_spread_bps

        if spread_bps > self._max_spread_bps:
            target_spread = mid * self._max_spread_bps / 10000.0
            half_adj = (spread - target_spread) / 2.0
            our_bid += half_adj
            our_ask -= half_adj
            spread_bps = self._max_spread_bps

        # 5. Determine action
        if self._inventory_mgr.is_at_limit(inventory):
            if not sides["bid_allowed"] and not sides["ask_allowed"]:
                action = "PULL_QUOTES"
            elif not sides["bid_allowed"]:
                action = "QUOTE_ASK_ONLY"
            else:
                action = "QUOTE_BID_ONLY"
        else:
            action = "QUOTE_BOTH"

        # 6. Confidence: penalise for high inventory, adverse selection, end of session
        confidence = 80.0
        confidence -= risk_pen * 30.0          # up to -30 for max inventory
        confidence -= adverse_ratio * 20.0     # up to -20 for 100% adverse
        confidence -= (1.0 - time_remaining) * 10.0  # up to -10 at session end
        confidence = max(0.0, min(100.0, confidence))

        result = {
            "our_bid": round(our_bid, 6),
            "our_ask": round(our_ask, 6),
            "spread_bps": round(spread_bps, 2),
            "reservation_price": round(reservation, 6),
            "market_bid": bid,
            "market_ask": ask,
            "mid": mid,
            "skew": round(skew, 4),
            "risk_penalty": round(risk_pen, 4),
            "adverse_ratio": round(adverse_ratio, 4),
            "widen": widen,
            "sides_allowed": sides,
            "inventory": inventory,
            "time_remaining": round(time_remaining, 4),
            "confidence": round(confidence, 1),
            "action": action,
            "eval_count": self._eval_count,
        }

        log.info(
            "MM Signal #%d: action=%s bid=%.4f ask=%.4f spread=%.1fbps conf=%.1f inv=%d",
            self._eval_count, action, our_bid, our_ask, spread_bps,
            confidence, inventory,
        )
        return result

    def record_fill(
        self,
        fill_side: str,
        fill_price: float,
        mid_at_fill: float,
        mid_after: float,
    ) -> None:
        """Record a fill for adverse selection tracking.

        Args:
            fill_side: 'buy' or 'sell'.
            fill_price: Fill price.
            mid_at_fill: Mid at time of fill.
            mid_after: Mid shortly after fill.
        """
        self._adverse_detector.record_fill(
            fill_side, fill_price, mid_at_fill, mid_after,
        )

        # Also log to NDJSON
        try:
            fill_record = {
                "ts": time.time(),
                "side": fill_side,
                "fill_price": fill_price,
                "mid_at_fill": mid_at_fill,
                "mid_after": mid_after,
            }
            with open(FILLS_PATH, "a") as f:
                f.write(json.dumps(fill_record) + "\n")
        except OSError as exc:
            log.warning("Failed to write fill log: %s", exc)

    def save_state(self) -> None:
        """Persist current state to disk."""
        state = {
            "eval_count": self._eval_count,
            "fill_stats": self._adverse_detector.fill_stats(),
            "inventory_stats": self._inventory_mgr.inventory_stats(),
            "config": {
                "gamma": self._config.gamma,
                "sigma": self._config.sigma,
                "k": self._config.k,
                "T": self._config.T,
            },
        }
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(STATE_PATH, "w") as f:
                json.dump(state, f, indent=2)
            log.info("State saved to %s", STATE_PATH)
        except OSError as exc:
            log.error("Failed to save state: %s", exc)

    def summary(self) -> Dict[str, Any]:
        """Return summary of market making activity.

        Returns:
            Dict with eval_count, fill_stats, inventory_stats, config.
        """
        return {
            "eval_count": self._eval_count,
            "fill_stats": self._adverse_detector.fill_stats(),
            "inventory_stats": self._inventory_mgr.inventory_stats(),
            "adverse_threshold": self._adverse_threshold,
            "spread_bounds_bps": {
                "min": self._min_spread_bps,
                "max": self._max_spread_bps,
            },
        }
