"""Volume Analytics — microstructure indicators for the Python Brain.

Provides:
- calculate_rvol: Relative volume vs 20-bar MA
- calculate_vpin: Volume-Synchronized Probability of Informed Trading
- classify_volume_bvc: Bulk Volume Classification (BVC) for VPIN input
- detect_sweep: Sweep-to-fill detection (large aggressive orders)
- spread_explosion_rate: Rate of bid-ask spread widening
- volume_divergence: Price up but volume down (bearish signal)
"""

from __future__ import annotations
import math
from typing import List, Tuple


def _norm_cdf(x: float) -> float:
    """Abramowitz & Stegun approximation of the standard normal CDF.

    Avoids scipy dependency. Max error ~1.5e-7 which is more than adequate
    for BVC volume classification.
    """
    t = 1.0 / (1.0 + 0.2316419 * abs(x))
    d = 0.3989422804014327  # 1 / sqrt(2 * pi)
    p = d * math.exp(-x * x / 2.0) * (
        t * (0.319381530
             + t * (-0.356563782
                    + t * (1.781477937
                           + t * (-1.821255978
                                  + t * 1.330274429))))
    )
    return 1.0 - p if x > 0 else p


def classify_volume_bvc(
    closes: List[float],
    volumes: List[float],
) -> Tuple[List[float], List[float]]:
    """Bulk Volume Classification (Easley, Lopez de Prado, O'Hara 2012).

    Classifies each bar's volume as buy or sell based on price change.
    Uses the simplified BVC: V_buy = V * CDF(z) where z = (close - prev) / sigma.

    Returns (buy_volumes, sell_volumes) lists of the same length as *closes*.
    """
    buy_vols: List[float] = []
    sell_vols: List[float] = []

    # Compute rolling standard deviation of returns for z-score normalisation
    returns: List[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            returns.append((closes[i] - closes[i - 1]) / closes[i - 1])

    # Need >10 returns for a meaningful sigma; fall back to 1% if not enough data
    if len(returns) > 10:
        mean_r = sum(returns) / len(returns)
        var_r = sum((r - mean_r) ** 2 for r in returns) / len(returns)
        sigma = math.sqrt(var_r) if var_r > 0 else 0.01
    else:
        sigma = 0.01
    sigma = max(sigma, 1e-9)

    for i in range(len(closes)):
        v = volumes[i] if i < len(volumes) else 0.0
        if i == 0 or closes[i - 1] == 0:
            # First bar or zero prev close — split 50/50
            buy_vols.append(v * 0.5)
            sell_vols.append(v * 0.5)
            continue

        z = (closes[i] - closes[i - 1]) / (closes[i - 1] * sigma)
        buy_pct = _norm_cdf(z)
        buy_vols.append(v * buy_pct)
        sell_vols.append(v * (1.0 - buy_pct))

    return buy_vols, sell_vols


def calculate_rvol(volumes: List[float], window: int = 20) -> float:
    """Relative volume: current volume divided by trailing *window*-bar MA.

    RVOL > 1.8 = elevated (entry confirmation).
    RVOL > 3.5 = extreme (jump-diffusion territory).

    Returns 0.0 if insufficient data or zero MA.
    """
    if len(volumes) < window + 1:
        return 0.0
    ma = sum(volumes[-(window + 1):-1]) / window
    if ma <= 0.0:
        return 0.0
    return volumes[-1] / ma


def calculate_vpin(
    buy_volume: List[float],
    sell_volume: List[float],
    n_buckets: int = 50,
) -> float:
    """Volume-Synchronized Probability of Informed Trading (Easley et al. 2012).

    VPIN = mean(|V_buy - V_sell|) / mean(V_buy + V_sell) over *n_buckets*.
    Returns value in [0.0, 1.0].  Higher = more informed flow.

    Returns 0.0 if inputs are mismatched or too short.
    """
    n = min(len(buy_volume), len(sell_volume))
    if n < n_buckets or n_buckets <= 0:
        return 0.0

    # Use the most recent n_buckets entries
    bv = buy_volume[-n_buckets:]
    sv = sell_volume[-n_buckets:]

    abs_diff_sum = 0.0
    total_vol_sum = 0.0
    for b, s in zip(bv, sv):
        abs_diff_sum += abs(b - s)
        total_vol_sum += b + s

    if total_vol_sum <= 0.0:
        return 0.0
    return abs_diff_sum / total_vol_sum


def detect_sweep(
    prices: List[float],
    volumes: List[float],
    threshold: float = 2.0,
) -> bool:
    """Sweep-to-fill detection: large aggressive order eating through the book.

    A sweep is detected when the latest bar has:
    - Volume > *threshold* x 20-bar volume MA (aggressive size), AND
    - Price moved > 0.3% in one bar (eating through levels).

    Returns True if sweep signature detected.
    """
    if len(prices) < 21 or len(volumes) < 21:
        return False

    vol_ma = sum(volumes[-21:-1]) / 20
    if vol_ma <= 0.0:
        return False

    rvol = volumes[-1] / vol_ma
    price_move_pct = abs(prices[-1] - prices[-2]) / prices[-2] * 100.0 if prices[-2] > 0 else 0.0

    return rvol > threshold and price_move_pct > 0.3


def spread_explosion_rate(spreads: List[float], window: int = 10) -> float:
    """Rate of bid-ask spread widening over trailing *window* bars.

    Returns the ratio of current spread to the *window*-bar average spread.
    Value > 2.0 = spread explosion (likely adverse selection).
    Returns 0.0 if insufficient data.
    """
    if len(spreads) < window + 1:
        return 0.0

    avg_spread = sum(spreads[-(window + 1):-1]) / window
    if avg_spread <= 0.0:
        return 0.0
    return spreads[-1] / avg_spread


def volume_divergence(
    prices: List[float],
    volumes: List[float],
    window: int = 10,
) -> bool:
    """Price-volume divergence: price rising but volume declining.

    Looks at the most recent *window* bars.  If prices trend up (last > first)
    and volumes trend down (regression slope < 0), divergence is confirmed.
    This is a bearish signal useful for Type C (overbought fade) entries.

    Returns True if divergence detected.
    """
    if len(prices) < window or len(volumes) < window:
        return False

    p = prices[-window:]
    v = volumes[-window:]

    # Price direction: simple first vs last
    price_up = p[-1] > p[0]
    if not price_up:
        return False

    # Volume direction: simple linear regression slope
    n = float(window)
    x_mean = (n - 1.0) / 2.0
    v_mean = sum(v) / n

    cov = 0.0
    var_x = 0.0
    for i, vi in enumerate(v):
        dx = i - x_mean
        cov += dx * (vi - v_mean)
        var_x += dx * dx

    if var_x <= 0.0:
        return False

    slope = cov / var_x
    return slope < 0.0
