"""Cross-Market Lead-Lag Strategy — Books 77, 122, 136.

Exploits the 10-120 second lag between US futures/equities and
LSE leveraged ETPs tracking the same underlying.

Signal: When ES/SPY/NQ moves significantly (>0.3% in 5 minutes),
the corresponding LSE ETP (3USL, QQQ3, NVD3, etc.) lags behind
by 10-120 seconds. Enter the lagging ETP in the direction of
the leader's move.

This edge exists because:
1. LSE ETPs have lower liquidity than US originals
2. Market makers reprice LSE products with a delay
3. The information transmission from US→LSE is not instantaneous

Requirements:
- Simultaneous US and LSE market data (US session overlap: 14:30-16:30 UTC)
- Sub-minute bar data for timing accuracy
- Cross-exchange tick timestamp alignment

Usage:
    from python_brain.strategies.lead_lag import (
        detect_lead_lag_signal, LEAD_LAG_PAIRS,
    )

    signal = detect_lead_lag_signal(
        leader_returns=es_5min_returns,
        follower_price=qqq3_current,
        follower_returns=qqq3_5min_returns,
        pair="ES→QQQ3",
    )
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("lead_lag")


# ---------------------------------------------------------------------------
# Lead-Lag Pairs (US leader → LSE follower)
# ---------------------------------------------------------------------------
LEAD_LAG_PAIRS: Dict[str, Dict[str, str]] = {
    # US index → LSE leveraged ETP
    "ES→3USL": {"leader": "ES", "follower": "3USL.L", "leverage": "3"},
    "ES→3USS": {"leader": "ES", "follower": "3USS.L", "leverage": "3"},
    "NQ→QQQ3": {"leader": "NQ", "follower": "QQQ3.L", "leverage": "3"},
    "NQ→QQQS": {"leader": "NQ", "follower": "QQQS.L", "leverage": "3"},
    # US single stock → LSE 3x ETP
    "NVDA→NVD3": {"leader": "NVDA", "follower": "NVD3.L", "leverage": "3"},
    "TSLA→TSL3": {"leader": "TSLA", "follower": "TSL3.L", "leverage": "3"},
    "AMZN→AML3": {"leader": "AMZN", "follower": "AML3.L", "leverage": "3"},
    "MSFT→MSF3": {"leader": "MSFT", "follower": "MSF3.L", "leverage": "3"},
    "GOOG→GOO3": {"leader": "GOOG", "follower": "GOO3.L", "leverage": "3"},
    "AAPL→AAP3": {"leader": "AAPL", "follower": "AAP3.L", "leverage": "3"},
    "AMD→AMD3": {"leader": "AMD", "follower": "AMD3.L", "leverage": "3"},
    "META→MET3": {"leader": "META", "follower": "MET3.L", "leverage": "3"},
}


@dataclass
class LeadLagSignal:
    """Signal from cross-market lead-lag detection."""
    pair: str
    leader_ticker: str
    follower_ticker: str
    direction: str  # "long" or "short"
    leader_move_pct: float  # Leader move magnitude
    follower_lag_pct: float  # How much follower has NOT moved yet
    confidence: int  # 0-100
    estimated_catch_up_pct: float  # Expected follower catch-up
    optimal_entry_delay_secs: int  # How long to wait after leader move
    strategy: str = "LeadLag"


def detect_lead_lag_signal(
    leader_returns: List[float],
    follower_returns: List[float],
    pair_name: str,
    leader_move_threshold: float = 0.003,  # 0.3% minimum leader move
    lag_threshold: float = 0.5,  # Follower must have moved < 50% of leader
    min_bars: int = 5,
) -> Optional[LeadLagSignal]:
    """Detect cross-market lead-lag opportunity.

    Args:
        leader_returns: Recent 1-min returns for leader (newest last)
        follower_returns: Recent 1-min returns for follower (newest last)
        pair_name: Key in LEAD_LAG_PAIRS
        leader_move_threshold: Min leader 5-bar return to trigger
        lag_threshold: Max follower/leader ratio (lower = bigger lag)
        min_bars: Minimum bars of data needed
    """
    if len(leader_returns) < min_bars or len(follower_returns) < min_bars:
        return None

    pair = LEAD_LAG_PAIRS.get(pair_name)
    if not pair:
        return None

    # Compute 5-bar cumulative returns
    leader_5bar = sum(leader_returns[-5:])
    follower_5bar = sum(follower_returns[-5:])

    # Leader must have moved significantly
    if abs(leader_5bar) < leader_move_threshold:
        return None

    # Check if follower is lagging
    if abs(leader_5bar) > 0:
        catch_up_ratio = follower_5bar / leader_5bar
    else:
        return None

    # Follower should have moved less than lag_threshold of leader
    if catch_up_ratio > lag_threshold:
        return None  # Follower already caught up

    # Direction: follow the leader
    direction = "long" if leader_5bar > 0 else "short"

    # For ISA: can only go long on regular ETPs
    leverage = int(pair.get("leverage", "3"))
    follower = pair["follower"]
    if direction == "short":
        # Need inverse ETP
        inverse_pair = pair_name.replace("→" + follower.replace(".L", ""), "→" + follower.replace(".L", "").replace("3", "S"))
        # For now, skip short signals (ISA constraint)
        return None

    # Confidence based on leader move magnitude and lag size
    base_conf = 55
    move_bonus = min(20, int(abs(leader_5bar) * 1000))  # +20 max for large moves
    lag_bonus = min(15, int((1 - catch_up_ratio) * 30))  # Bigger lag = more confidence
    confidence = min(90, base_conf + move_bonus + lag_bonus)

    # Expected catch-up
    expected_catch_up = abs(leader_5bar) * (1 - catch_up_ratio) * leverage

    return LeadLagSignal(
        pair=pair_name,
        leader_ticker=pair["leader"],
        follower_ticker=follower,
        direction=direction,
        leader_move_pct=round(leader_5bar * 100, 3),
        follower_lag_pct=round(catch_up_ratio * 100, 1),
        confidence=confidence,
        estimated_catch_up_pct=round(expected_catch_up * 100, 3),
        optimal_entry_delay_secs=30,  # Typical optimal delay
        strategy="LeadLag",
    )


# ---------------------------------------------------------------------------
# Granger Causality Test (rolling, for pair validation)
# ---------------------------------------------------------------------------
def granger_causality_f_stat(
    leader_returns: np.ndarray,
    follower_returns: np.ndarray,
    max_lag: int = 5,
) -> Tuple[float, int]:
    """Compute Granger causality F-statistic.

    Tests H0: leader does NOT Granger-cause follower.
    High F-stat (>3.84 for p<0.05) = leader causes follower.

    Returns: (f_stat, optimal_lag)
    """
    n = min(len(leader_returns), len(follower_returns))
    if n < max_lag + 10:
        return 0.0, 0

    best_f = 0.0
    best_lag = 1

    for lag in range(1, max_lag + 1):
        # Restricted model: follower_t = a0 + a1*follower_{t-1} + ... + eps
        # Unrestricted model: follower_t = a0 + a1*follower_{t-1} + b1*leader_{t-1} + ... + eps

        y = follower_returns[lag:]
        T = len(y)
        if T < 20:
            continue

        # Restricted: AR(lag) on follower only
        X_r = np.column_stack([follower_returns[lag - i - 1:T + lag - i - 1] for i in range(lag)])
        X_r = np.column_stack([np.ones(T), X_r])

        # Unrestricted: AR(lag) on follower + leader lags
        X_u = np.column_stack([
            X_r,
            *[leader_returns[lag - i - 1:T + lag - i - 1] for i in range(lag)],
        ])

        try:
            # OLS residuals
            beta_r = np.linalg.lstsq(X_r, y, rcond=None)[0]
            rss_r = np.sum((y - X_r @ beta_r) ** 2)

            beta_u = np.linalg.lstsq(X_u, y, rcond=None)[0]
            rss_u = np.sum((y - X_u @ beta_u) ** 2)

            # F-statistic
            q = lag  # Number of restrictions
            dof = T - X_u.shape[1]
            if dof <= 0 or rss_u <= 0:
                continue

            f_stat = ((rss_r - rss_u) / q) / (rss_u / dof)

            if f_stat > best_f:
                best_f = f_stat
                best_lag = lag
        except (np.linalg.LinAlgError, ValueError):
            continue

    return best_f, best_lag


# ---------------------------------------------------------------------------
# Transfer Entropy (Book 136 extension)
# ---------------------------------------------------------------------------

def _shannon_entropy(data: np.ndarray, bins: int = 10) -> float:
    """Compute Shannon entropy H(X) = -sum(p * log(p)) using histogram binning."""
    counts, _ = np.histogram(data, bins=bins)
    probs = counts / counts.sum()
    probs = probs[probs > 0]
    return float(-np.sum(probs * np.log(probs)))


def _joint_entropy(x: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    """Compute joint entropy H(X,Y) using 2D histogram."""
    counts, _, _ = np.histogram2d(x, y, bins=bins)
    probs = counts.flatten() / counts.sum()
    probs = probs[probs > 0]
    return float(-np.sum(probs * np.log(probs)))


def _conditional_entropy(x: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    """Compute conditional entropy H(X|Y) = H(X,Y) - H(Y)."""
    return _joint_entropy(x, y, bins) - _shannon_entropy(y, bins)


def compute_transfer_entropy(
    x: np.ndarray,
    y: np.ndarray,
    lag: int = 1,
    bins: int = 10,
) -> float:
    """Compute Transfer Entropy TE(X -> Y) via histogram-based estimation.

    TE(X→Y) = H(Y_future | Y_past) - H(Y_future | Y_past, X_past)

    Measures the information X provides about the future of Y beyond
    what Y's own past provides. Higher TE = stronger causal influence.

    Args:
        x: Source time series (leader)
        y: Target time series (follower)
        lag: Number of lag steps
        bins: Histogram bin count for entropy estimation

    Returns: Transfer entropy in nats (>0 means X→Y information flow)
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = min(len(x), len(y))
    if n < lag + 10:
        return 0.0

    # Align: y_future, y_past, x_past
    y_future = y[lag:n]
    y_past = y[:n - lag]
    x_past = x[:n - lag]

    # TE(X→Y) = H(Y_future | Y_past) - H(Y_future | Y_past, X_past)
    # = H(Y_future, Y_past) - H(Y_past) - [H(Y_future, Y_past, X_past) - H(Y_past, X_past)]
    h_yf_yp = _joint_entropy(y_future, y_past, bins)
    h_yp = _shannon_entropy(y_past, bins)

    # For the 3-variable terms, stack y_past and x_past as joint
    joint_past = y_past + x_past * 1000  # crude joint encoding via linear combo
    h_yf_yp_xp = _joint_entropy(y_future, joint_past, bins)
    h_yp_xp = _joint_entropy(y_past, x_past, bins)

    te = (h_yf_yp - h_yp) - (h_yf_yp_xp - h_yp_xp)
    return max(0.0, float(te))  # TE should be non-negative in theory


# ---------------------------------------------------------------------------
# Dynamic Time Warping (Book 122 extension)
# ---------------------------------------------------------------------------

def _dtw_matrix(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Compute the DTW cost matrix using full O(n*m) dynamic programming.

    Returns the accumulated cost matrix D where D[i,j] is the
    minimum alignment cost between x[:i+1] and y[:j+1].
    """
    n = len(x)
    m = len(y)
    D = np.full((n + 1, m + 1), np.inf)
    D[0, 0] = 0.0

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = (x[i - 1] - y[j - 1]) ** 2
            D[i, j] = cost + min(D[i - 1, j], D[i, j - 1], D[i - 1, j - 1])

    return D


def compute_dtw_distance(x: np.ndarray, y: np.ndarray) -> float:
    """Compute Dynamic Time Warping distance between two time series.

    Full numpy implementation (no external deps). O(n*m) complexity.
    For lead-lag detection: low DTW distance = similar shape regardless of timing.

    Args:
        x: First time series (e.g., leader returns)
        y: Second time series (e.g., follower returns)

    Returns: DTW distance (sqrt of accumulated squared cost). Lower = more similar.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)

    if len(x) == 0 or len(y) == 0:
        return float("inf")

    D = _dtw_matrix(x, y)
    return float(math.sqrt(D[len(x), len(y)]))


# ---------------------------------------------------------------------------
# Five-Channel Lead-Lag Aggregator (Book 136)
# ---------------------------------------------------------------------------

@dataclass
class ChannelSignal:
    """Signal from a single lead-lag channel."""
    channel: str
    leader_move_pct: float
    lag_ratio: float
    transfer_entropy: float
    dtw_distance: float
    strength: float  # 0-1 composite


class FiveChannelLeadLag:
    """Aggregate lead-lag signals across 5 information channels.

    Channels:
    1. US Futures (ES, NQ) — fastest movers
    2. US Cash (SPY, QQQ) — confirmation
    3. Options Flow — put/call ratio shifts, skew changes
    4. FX (DXY, GBP/USD) — currency impact on LSE ETPs
    5. VIX — vol regime shifts

    Each channel contributes a weighted vote to the aggregate signal.
    """

    CHANNEL_WEIGHTS = {
        "us_futures": 0.35,
        "us_cash": 0.25,
        "options_flow": 0.15,
        "fx": 0.15,
        "vix": 0.10,
    }

    def __init__(self, te_threshold: float = 0.05, dtw_threshold: float = 5.0):
        self.te_threshold = te_threshold
        self.dtw_threshold = dtw_threshold
        self._channel_signals: Dict[str, ChannelSignal] = {}

    def update_channel(
        self,
        channel: str,
        leader_returns: np.ndarray,
        follower_returns: np.ndarray,
        leader_move_pct: float = 0.0,
    ) -> Optional[ChannelSignal]:
        """Update a single channel with new data.

        Args:
            channel: One of the 5 channel names
            leader_returns: Recent leader return series
            follower_returns: Recent follower return series
            leader_move_pct: Recent leader cumulative move

        Returns: ChannelSignal if data is valid, else None
        """
        if channel not in self.CHANNEL_WEIGHTS:
            log.warning("Unknown channel: %s", channel)
            return None

        leader_returns = np.asarray(leader_returns, dtype=float)
        follower_returns = np.asarray(follower_returns, dtype=float)

        if len(leader_returns) < 10 or len(follower_returns) < 10:
            return None

        # Compute transfer entropy (leader → follower)
        te = compute_transfer_entropy(leader_returns, follower_returns, lag=1)

        # Compute DTW distance (shape similarity)
        dtw_dist = compute_dtw_distance(leader_returns[-30:], follower_returns[-30:])

        # Lag ratio: how much follower has caught up
        leader_cum = float(np.sum(leader_returns[-5:]))
        follower_cum = float(np.sum(follower_returns[-5:]))
        lag_ratio = follower_cum / leader_cum if abs(leader_cum) > 1e-8 else 1.0

        # Compute channel strength (0-1)
        te_score = min(1.0, te / max(self.te_threshold, 1e-8)) if te > 0 else 0.0
        dtw_score = max(0.0, 1.0 - dtw_dist / max(self.dtw_threshold, 1e-8))
        lag_score = max(0.0, 1.0 - abs(lag_ratio))  # Higher when follower hasn't caught up
        strength = 0.4 * te_score + 0.3 * lag_score + 0.3 * dtw_score

        sig = ChannelSignal(
            channel=channel,
            leader_move_pct=leader_move_pct,
            lag_ratio=round(lag_ratio, 4),
            transfer_entropy=round(te, 6),
            dtw_distance=round(dtw_dist, 4),
            strength=round(strength, 4),
        )
        self._channel_signals[channel] = sig
        return sig

    def aggregate(self) -> Tuple[float, str]:
        """Aggregate all channel signals into a single lead-lag score.

        Returns: (aggregate_strength, direction)
            aggregate_strength: 0-1 weighted composite
            direction: "long" if leaders are up, "short" if down, "flat" if mixed
        """
        if not self._channel_signals:
            return 0.0, "flat"

        weighted_strength = 0.0
        weighted_direction = 0.0
        total_weight = 0.0

        for channel, sig in self._channel_signals.items():
            w = self.CHANNEL_WEIGHTS.get(channel, 0.0)
            weighted_strength += w * sig.strength
            weighted_direction += w * sig.leader_move_pct
            total_weight += w

        if total_weight > 0:
            weighted_strength /= total_weight
            weighted_direction /= total_weight

        if weighted_direction > 0.001:
            direction = "long"
        elif weighted_direction < -0.001:
            direction = "short"
        else:
            direction = "flat"

        return round(weighted_strength, 4), direction

    @property
    def channel_summary(self) -> Dict[str, dict]:
        """Summary of all active channels."""
        return {
            ch: {
                "strength": sig.strength,
                "te": sig.transfer_entropy,
                "dtw": sig.dtw_distance,
                "lag_ratio": sig.lag_ratio,
            }
            for ch, sig in self._channel_signals.items()
        }
