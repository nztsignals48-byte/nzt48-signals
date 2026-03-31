"""analytics/microstructure.py — Book 32: Market Microstructure Physics.

VPIN calculator, Kyle's lambda tracker, Lee-Ready trade classification,
spread decomposition, and microstructure entry quality scorer (0-100).

Integration: bridge.py CHECK 34 veto gate — micro_score < strategy_minimum → block.
"""

import math
import logging
from collections import deque
from dataclasses import dataclass, asdict
from typing import Dict, Optional, List

import numpy as np

try:
    from scipy.stats import norm as sp_norm
except ImportError:
    sp_norm = None

log = logging.getLogger(__name__)

# ── VPIN Calculator ─────────────────────────────────────────────────────

class VPINCalculator:
    """Volume-Synchronized Probability of Informed Trading.

    Buckets trades by volume (not time), computes order imbalance ratio.
    VPIN = SUM(|V_buy - V_sell|) / (n_buckets × bucket_size)

    Thresholds:
      < 0.25: BENIGN
      0.25-0.35: ELEVATED (potential entry zone for trends)
      0.35-0.50: DIRECTIONAL_TOXIC
      0.50-0.70: BILATERAL_TOXIC
      > 0.70: CRISIS
    """

    def __init__(self, bucket_size: float, n_buckets: int = 50,
                 sigma_window: int = 20):
        self.bucket_size = max(bucket_size, 1.0)
        self.n_buckets = n_buckets
        self.sigma_window = sigma_window

        self.current_bucket_buy = 0.0
        self.current_bucket_sell = 0.0
        self.current_bucket_vol = 0.0
        self.completed_buckets: List[tuple] = []
        self.price_changes: deque = deque(maxlen=sigma_window)
        self._last_vpin = float("nan")

    def update(self, price_open: float, price_close: float,
               volume: float) -> float:
        """Update with a new bar. Returns current VPIN or NaN if warming up."""
        if volume <= 0 or price_open <= 0:
            return self._last_vpin

        dp = price_close - price_open
        self.price_changes.append(dp)

        if len(self.price_changes) < 2:
            return float("nan")

        sigma = max(float(np.std(list(self.price_changes))), 1e-8)
        z = dp / sigma

        # Bulk Volume Classification
        if sp_norm is not None:
            v_buy = volume * sp_norm.cdf(z)
        else:
            # Fallback: simple sign-based classification
            v_buy = volume * (0.5 + 0.5 * np.tanh(z))
        v_sell = volume - v_buy

        remaining_buy = v_buy
        remaining_sell = v_sell
        remaining_vol = volume

        while remaining_vol > 1e-8:
            space = self.bucket_size - self.current_bucket_vol
            if remaining_vol >= space:
                frac = space / remaining_vol if remaining_vol > 0 else 0
                self.current_bucket_buy += remaining_buy * frac
                self.current_bucket_sell += remaining_sell * frac
                self.completed_buckets.append(
                    (self.current_bucket_buy, self.current_bucket_sell))
                remaining_buy *= (1.0 - frac)
                remaining_sell *= (1.0 - frac)
                remaining_vol -= space
                self.current_bucket_buy = 0.0
                self.current_bucket_sell = 0.0
                self.current_bucket_vol = 0.0
            else:
                self.current_bucket_buy += remaining_buy
                self.current_bucket_sell += remaining_sell
                self.current_bucket_vol += remaining_vol
                remaining_vol = 0

        if len(self.completed_buckets) < self.n_buckets:
            return float("nan")

        recent = self.completed_buckets[-self.n_buckets:]
        vpin = sum(abs(b - s) for b, s in recent) / (self.n_buckets * self.bucket_size)
        self._last_vpin = min(vpin, 1.0)
        return self._last_vpin

    @property
    def vpin(self) -> float:
        return self._last_vpin

    def classify_toxicity(self) -> str:
        """Classify current VPIN into toxicity regime."""
        v = self._last_vpin
        if math.isnan(v):
            return "UNKNOWN"
        if v < 0.25:
            return "BENIGN"
        elif v < 0.35:
            return "ELEVATED"
        elif v < 0.50:
            return "DIRECTIONAL_TOXIC"
        elif v < 0.70:
            return "BILATERAL_TOXIC"
        return "CRISIS"


# ── Kyle's Lambda Tracker ──────────────────────────────────────────────

class KyleLambdaTracker:
    """Kyle's lambda: price impact = λ × order_imbalance_volume.

    Lambda regimes:
      < 0.5× median: DEEP (low impact, easy to enter/exit)
      0.5-1.5× median: NORMAL
      1.5-3.0× median: THIN (high impact, careful sizing)
      > 3.0× median: DANGEROUS (avoid)
    """

    def __init__(self, window: int = 100, decay: float = 0.95):
        self.window = window
        self.decay = decay
        self._price_changes: deque = deque(maxlen=window)
        self._oib_values: deque = deque(maxlen=window)
        self._lambda_history: deque = deque(maxlen=window)
        self._current_lambda = 0.0

    def update(self, price_change: float, order_imbalance: float) -> float:
        """Update with a new observation. Returns current lambda estimate."""
        self._price_changes.append(price_change)
        self._oib_values.append(order_imbalance)

        if len(self._price_changes) < 10:
            return 0.0

        prices = np.array(self._price_changes)
        oib = np.array(self._oib_values)

        # Lambda = cov(dp, OIB) / var(OIB)
        var_oib = np.var(oib)
        if var_oib < 1e-12:
            return self._current_lambda

        cov = np.mean((prices - np.mean(prices)) * (oib - np.mean(oib)))
        new_lambda = abs(cov / var_oib)

        # Exponential smoothing
        self._current_lambda = self.decay * self._current_lambda + (1 - self.decay) * new_lambda
        self._lambda_history.append(self._current_lambda)
        return self._current_lambda

    @property
    def lambda_value(self) -> float:
        return self._current_lambda

    def classify_regime(self) -> str:
        """Classify current lambda into market depth regime."""
        if len(self._lambda_history) < 20:
            return "UNKNOWN"

        median = float(np.median(list(self._lambda_history)))
        if median < 1e-12:
            return "DEEP"

        ratio = self._current_lambda / median
        if ratio < 0.5:
            return "DEEP"
        elif ratio < 1.5:
            return "NORMAL"
        elif ratio < 3.0:
            return "THIN"
        return "DANGEROUS"

    @property
    def impact_bps(self) -> float:
        """Estimated price impact in basis points per unit volume."""
        return self._current_lambda * 10000


# ── Spread Decomposition ──────────────────────────────────────────────

@dataclass
class SpreadMetrics:
    """Glosten-Milgrom spread decomposition."""
    raw_spread_bps: float = 0.0
    avg_spread_bps: float = 0.0
    spread_ratio: float = 1.0  # current / average
    is_wide: bool = False      # > 1.5× average
    is_blowout: bool = False   # > 2.0× average

    def to_dict(self) -> Dict:
        return asdict(self)


class SpreadTracker:
    """Track bid-ask spread statistics."""

    def __init__(self, window: int = 100):
        self._spreads: deque = deque(maxlen=window)

    def update(self, bid: float, ask: float) -> SpreadMetrics:
        """Update with new quote. Returns current spread metrics."""
        if bid <= 0 or ask <= 0 or ask < bid:
            return SpreadMetrics()

        mid = (bid + ask) / 2.0
        spread_bps = (ask - bid) / mid * 10000

        self._spreads.append(spread_bps)

        if len(self._spreads) < 5:
            return SpreadMetrics(raw_spread_bps=spread_bps, avg_spread_bps=spread_bps)

        avg = float(np.mean(list(self._spreads)))
        ratio = spread_bps / avg if avg > 0 else 1.0

        return SpreadMetrics(
            raw_spread_bps=round(spread_bps, 2),
            avg_spread_bps=round(avg, 2),
            spread_ratio=round(ratio, 3),
            is_wide=ratio > 1.5,
            is_blowout=ratio > 2.0,
        )


# ── Ask-Side Ratio (ASR) ──────────────────────────────────────────────

class ASRTracker:
    """Ask-Side Ratio: fraction of volume at the ask (buyer-initiated).

    ASR > 0.65: strong bullish pressure
    ASR < 0.35: strong bearish pressure
    """

    def __init__(self, window: int = 200):
        self._buy_volume: deque = deque(maxlen=window)
        self._sell_volume: deque = deque(maxlen=window)

    def update(self, buy_volume: float, sell_volume: float) -> float:
        """Update with classified volume. Returns current ASR."""
        self._buy_volume.append(buy_volume)
        self._sell_volume.append(sell_volume)

        total_buy = sum(self._buy_volume)
        total_sell = sum(self._sell_volume)
        total = total_buy + total_sell

        if total < 1e-8:
            return 0.5

        return total_buy / total

    @property
    def asr(self) -> float:
        total_buy = sum(self._buy_volume)
        total = total_buy + sum(self._sell_volume)
        return total_buy / total if total > 1e-8 else 0.5


# ── Microstructure Entry Quality Scorer ────────────────────────────────

# Per-strategy minimum micro scores
STRATEGY_MINIMUMS = {
    "VanguardSniper": 50,
    "S_VanguardSniper": 50,
    "EarlyRunner": 50,
    "IBS_MeanReversion": 40,
    "OverboughtFade": 40,
    "DipRecovery": 40,
    "OBV_Divergence": 50,
    "S_ApexScout": 50,
    "S_AutonomousOrchestrator": 30,
}


@dataclass
class MicrostructureScore:
    """Microstructure entry quality score (0-100)."""
    vpin_score: float = 0.0          # 0-25
    flow_direction_score: float = 0.0  # 0-25
    spread_score: float = 0.0        # 0-25
    quote_dynamics_score: float = 0.0  # 0-25
    total: float = 0.0
    action: str = "BLOCK"  # ENTER_FULL, ENTER_REDUCED, ENTER_MINIMAL, BLOCK
    vpin_regime: str = "UNKNOWN"
    lambda_regime: str = "UNKNOWN"

    def to_dict(self) -> Dict:
        return asdict(self)


def compute_micro_score(vpin: float,
                        vpin_rising: bool,
                        asr: float,
                        signal_direction: int,  # +1 long, -1 short
                        spread_ratio: float,
                        spread_widening: bool,
                        lambda_regime: str,
                        vpin_regime: str) -> MicrostructureScore:
    """Compute microstructure entry quality score (0-100).

    4 dimensions, 25 points each:
      1. VPIN (informed flow detection)
      2. Flow direction (cumulative delta + ASR)
      3. Spread (transaction cost quality)
      4. Quote dynamics (market depth proxy)

    Action thresholds:
      >= 70: ENTER_FULL
      50-69: ENTER_REDUCED (60% size)
      30-49: ENTER_MINIMAL (30% size)
      < 30:  BLOCK
    """
    score = MicrostructureScore(vpin_regime=vpin_regime, lambda_regime=lambda_regime)

    # 1. VPIN CHECK (0-25)
    if not math.isnan(vpin):
        if 0.25 <= vpin <= 0.45:
            score.vpin_score = 20.0  # Sweet spot for directional entries
        elif vpin < 0.25:
            score.vpin_score = 10.0  # Low informed flow, uncertain
        elif vpin <= 0.50:
            score.vpin_score = 15.0  # Elevated but manageable
        else:
            score.vpin_score = 5.0   # Too toxic
        if vpin_rising and vpin > 0.25:
            score.vpin_score = min(25.0, score.vpin_score + 5.0)

    # 2. FLOW DIRECTION (0-25)
    if signal_direction > 0:  # Long signal
        if asr > 0.65:
            score.flow_direction_score = 25.0  # Strong buy pressure confirms
        elif asr > 0.55:
            score.flow_direction_score = 18.0
        elif asr > 0.45:
            score.flow_direction_score = 12.0  # Neutral
        else:
            score.flow_direction_score = 5.0   # Sell pressure contradicts
    elif signal_direction < 0:  # Short signal (ISA doesn't short, but for completeness)
        if asr < 0.35:
            score.flow_direction_score = 25.0
        elif asr < 0.45:
            score.flow_direction_score = 18.0
        else:
            score.flow_direction_score = 8.0
    else:
        score.flow_direction_score = 12.0  # No direction

    # 3. SPREAD CHECK (0-25)
    if spread_ratio <= 1.0:
        score.spread_score = 25.0  # Tighter than average
    elif spread_ratio <= 1.2:
        score.spread_score = 20.0
    elif spread_ratio <= 1.5:
        score.spread_score = 12.0
    elif spread_ratio <= 2.0:
        score.spread_score = 5.0   # Wide
    else:
        score.spread_score = 0.0   # Blowout — do NOT enter

    if spread_widening and spread_ratio > 1.2:
        score.spread_score = max(0, score.spread_score - 5.0)

    # 4. QUOTE DYNAMICS (0-25) — proxy via lambda regime
    lambda_scores = {"DEEP": 25.0, "NORMAL": 18.0, "THIN": 8.0,
                     "DANGEROUS": 2.0, "UNKNOWN": 12.0}
    score.quote_dynamics_score = lambda_scores.get(lambda_regime, 12.0)

    # Total
    score.total = (score.vpin_score + score.flow_direction_score
                   + score.spread_score + score.quote_dynamics_score)

    # Action
    if score.total >= 70:
        score.action = "ENTER_FULL"
    elif score.total >= 50:
        score.action = "ENTER_REDUCED"
    elif score.total >= 30:
        score.action = "ENTER_MINIMAL"
    else:
        score.action = "BLOCK"

    return score


# ── Aggregate Microstructure State ──────────────────────────────────────

class MicrostructureState:
    """Aggregates all microstructure components for a single instrument.

    Usage in bridge.py:
      state = get_micro_state(ticker_id)
      state.on_bar(open, close, volume, bid, ask)
      score = state.entry_score(signal_direction=+1, strategy="VanguardSniper")
    """

    def __init__(self, bucket_size: float = 1000.0):
        self.vpin_calc = VPINCalculator(bucket_size=bucket_size)
        self.lambda_tracker = KyleLambdaTracker()
        self.spread_tracker = SpreadTracker()
        self.asr_tracker = ASRTracker()
        self._prev_vpin = float("nan")

    def on_bar(self, price_open: float, price_close: float,
               volume: float, bid: float = 0, ask: float = 0) -> None:
        """Update all microstructure components with new bar data."""
        # VPIN
        self._prev_vpin = self.vpin_calc.vpin
        self.vpin_calc.update(price_open, price_close, volume)

        # Lambda
        dp = price_close - price_open
        # Approximate OIB from BVC
        if sp_norm is not None and volume > 0:
            sigma = max(np.std(list(self.vpin_calc.price_changes)) if self.vpin_calc.price_changes else 0.01, 1e-8)
            z = dp / sigma
            buy_vol = volume * sp_norm.cdf(z)
        else:
            buy_vol = volume * 0.5
        sell_vol = volume - buy_vol
        oib = buy_vol - sell_vol
        self.lambda_tracker.update(dp, oib)

        # ASR
        self.asr_tracker.update(buy_vol, sell_vol)

        # Spread
        if bid > 0 and ask > 0:
            self.spread_tracker.update(bid, ask)

    def entry_score(self, signal_direction: int = 1,
                    strategy: str = "") -> MicrostructureScore:
        """Compute entry quality score for current microstructure state."""
        vpin = self.vpin_calc.vpin
        vpin_rising = (not math.isnan(vpin) and not math.isnan(self._prev_vpin)
                       and vpin > self._prev_vpin)
        asr = self.asr_tracker.asr

        spreads = list(self.spread_tracker._spreads)
        if len(spreads) >= 2:
            spread_ratio = spreads[-1] / np.mean(spreads) if np.mean(spreads) > 0 else 1.0
            spread_widening = spreads[-1] > spreads[-2]
        else:
            spread_ratio = 1.0
            spread_widening = False

        return compute_micro_score(
            vpin=vpin,
            vpin_rising=vpin_rising,
            asr=asr,
            signal_direction=signal_direction,
            spread_ratio=spread_ratio,
            spread_widening=spread_widening,
            lambda_regime=self.lambda_tracker.classify_regime(),
            vpin_regime=self.vpin_calc.classify_toxicity(),
        )

    def should_block(self, strategy: str, signal_direction: int = 1) -> tuple:
        """Check if microstructure should block entry.

        Returns (should_block: bool, reason: str, micro_score: float).
        """
        score = self.entry_score(signal_direction, strategy)
        minimum = STRATEGY_MINIMUMS.get(strategy, 50)

        if score.total < minimum:
            return (True,
                    f"MICRO_QUALITY_LOW: {score.total:.0f} < {minimum} "
                    f"(vpin={score.vpin_regime}, lambda={score.lambda_regime})",
                    score.total)

        return (False, "", score.total)


# ── Module-level state cache ───────────────────────────────────────────

_micro_states: Dict[int, MicrostructureState] = {}


def get_micro_state(ticker_id: int,
                    bucket_size: float = 1000.0) -> MicrostructureState:
    """Get or create MicrostructureState for a ticker (singleton per ticker)."""
    if ticker_id not in _micro_states:
        _micro_states[ticker_id] = MicrostructureState(bucket_size=bucket_size)
    return _micro_states[ticker_id]
