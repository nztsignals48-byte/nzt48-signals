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
