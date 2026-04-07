"""12-Factor Kelly Position Sizing for AEGIS V2.

PURE FUNCTION. No side effects. No I/O. No state mutation. No threading (H07).
All constants from brain.config (H109). Zero-division guards (H61).
Fractional shares: always math.floor(), never round() (H64).

The 12 factors applied IN ORDER:
 1. Base Kelly from Bayesian Win Rate (H58)
 2. Volatility decay (3x: x9, 5x: x25) (H59)
 3. Moreira-Muir realized vol scaling
 4. Correlation penalty
 5. Drawdown scaling
 6. Amihud liquidity scaling
 7. Regime scaling
 8. Spread cost adjustment
 9. Session quality scaling (spread width + close proximity)
10. Confidence scaling
11. Half-Kelly cap (0.5)
12. Portfolio heat limit (6%)
"""

import math

import numpy as np

from brain.config import (
    KELLY_CLAMP_MAX,
    KELLY_FRACTION_CAP,
    OUTLIER_WIN_CAP_PCT,
    TRADING_DAYS_PER_YEAR,
    VOL_TARGET_ANNUAL_PCT,
)

# Bayesian prior strength for shrinkage (H58)
BAYESIAN_PRIOR_TRADES = 10


def bayesian_win_rate(wins, total, prior_wr=0.5, prior_strength=BAYESIAN_PRIOR_TRADES):
    """Bayesian shrinkage of win rate (H58).

    Laplace smoothing: W_adj = (W*N + prior_wr*prior_strength) / (N + prior_strength)
    Small sample → pulled toward 50%. Large sample → converges to observed.
    """
    safe_total = max(total, 0)
    return (wins + prior_wr * prior_strength) / max(safe_total + prior_strength, 1e-9)


def outlier_capped_avg_win(returns, cap_pct=OUTLIER_WIN_CAP_PCT):
    """Cap individual trade returns at cap_pct for Kelly avg payout (H62).

    Single trade at 5% → capped at 3% for Kelly average calculation.
    """
    if len(returns) == 0:
        return 0.0
    cap = cap_pct / 100.0
    capped = np.clip(returns, -np.inf, cap)
    return float(np.mean(capped))


def kelly_12factor(
    win_rate_raw,
    total_trades,
    avg_win,
    avg_loss,
    leverage_factor,
    realized_vol_annual,
    correlation_to_portfolio,
    current_drawdown_pct,
    amihud_illiq,
    regime,
    spread_pct,
    time_of_day_fraction,
    confidence,
    portfolio_heat_pct,
    equity,
    price,
):
    """Compute Kelly fraction with all 12 factors applied.

    Args:
        win_rate_raw: Raw observed win rate [0, 1].
        total_trades: Number of trades for Bayesian shrinkage.
        avg_win: Average winning trade return (before outlier cap).
        avg_loss: Average losing trade return (positive value, e.g. 0.02 = 2%).
        leverage_factor: ETP leverage (1, 2, 3, or 5).
        realized_vol_annual: Annualized realized volatility.
        correlation_to_portfolio: Correlation of this ticker to portfolio [-1, 1].
        current_drawdown_pct: Current daily drawdown [0, 100].
        amihud_illiq: Amihud illiquidity ratio (higher = less liquid).
        regime: Risk regime string: "normal", "reduce", "flatten", "halt".
        spread_pct: Current bid-ask spread in percent.
        time_of_day_fraction: Fraction of trading day elapsed [0, 1].
        confidence: Signal confidence [0, 100].
        portfolio_heat_pct: Current portfolio heat [0, 100].
        equity: Total portfolio equity in GBP.
        price: Current share price.

    Returns:
        dict with keys: kelly_fraction, shares, factors (dict of 12 factor values).
    """
    factors = {}

    # ── Factor 1: Base Kelly from Bayesian Win Rate (H58) ──
    wins = int(win_rate_raw * max(total_trades, 1))
    wr = bayesian_win_rate(wins, total_trades)
    safe_avg_loss = max(avg_loss, 1e-9)  # H61
    base_kelly = wr - (1.0 - wr) / max(avg_win / safe_avg_loss, 1e-9)
    base_kelly = max(base_kelly, 0.0)
    factors["f01_base_kelly"] = base_kelly

    # ── Factor 2: Volatility decay (H59) ──
    # 3x ETP: variance x9, 5x: variance x25
    vol_decay = 1.0 / max(leverage_factor ** 2, 1)
    factors["f02_vol_decay"] = vol_decay

    # ── Factor 3: Moreira-Muir realized vol scaling ──
    safe_vol = max(realized_vol_annual, 1e-9)  # H61
    mm_scale = (VOL_TARGET_ANNUAL_PCT / 100.0) / safe_vol
    mm_scale = float(np.clip(mm_scale, 0.01, 2.0))
    factors["f03_moreira_muir"] = mm_scale

    # ── Factor 4: Correlation penalty ──
    # Higher correlation to existing portfolio → reduce sizing
    corr_penalty = 1.0 - 0.5 * abs(correlation_to_portfolio)
    factors["f04_correlation"] = corr_penalty

    # ── Factor 5: Drawdown scaling ──
    # Reduce proportionally as drawdown increases
    dd_scale = max(1.0 - current_drawdown_pct / 4.0, 0.1)
    factors["f05_drawdown"] = dd_scale

    # ── Factor 6: Amihud liquidity scaling ──
    # Less liquid → smaller position
    liq_scale = 1.0 / max(1.0 + amihud_illiq, 1.0)
    factors["f06_amihud"] = liq_scale

    # ── Factor 7: Regime scaling ──
    regime_map = {"normal": 1.0, "reduce": 0.5, "flatten": 0.0, "halt": 0.0}
    regime_scale = regime_map.get(regime, 0.0)
    factors["f07_regime"] = regime_scale

    # ── Factor 8: Spread cost adjustment ──
    # Wider spread → reduce size (spread eats into edge)
    spread_scale = max(1.0 - spread_pct * 2.0, 0.1)
    factors["f08_spread"] = spread_scale

    # ── Factor 9: Session quality scaler (replaces linear time-of-day decay) ──
    # Late entries get smaller sizing due to LIQUIDITY conditions, not arbitrary time.
    # Uses spread percentile as primary quality signal.
    if spread_pct > 1.0:
        tod_scale = 0.3  # Very wide spread = poor liquidity = small size
    elif spread_pct > 0.5:
        tod_scale = 0.5  # Wide spread
    elif spread_pct > 0.2:
        tod_scale = 0.8  # Moderate spread
    else:
        tod_scale = 1.0  # Tight spread = full sizing
    # Time proximity to close: gentle reduction only in last 30 min
    if time_of_day_fraction > 0.92:  # Last ~30 min of session
        tod_scale *= 0.6  # Reduce for close proximity
    factors["f09_session_quality"] = tod_scale

    # ── Factor 10: Confidence scaling ──
    # Scale linearly by confidence [65, 100] → [0.65, 1.0]
    conf_scale = max(confidence / 100.0, 0.0)
    factors["f10_confidence"] = conf_scale

    # Apply all multiplicative factors
    kelly = base_kelly
    kelly *= vol_decay
    kelly *= mm_scale
    kelly *= corr_penalty
    kelly *= dd_scale
    kelly *= liq_scale
    kelly *= regime_scale
    kelly *= spread_scale
    kelly *= tod_scale
    kelly *= conf_scale

    # ── Factor 11: Half-Kelly cap (0.5) ──
    kelly = min(kelly, KELLY_FRACTION_CAP)
    factors["f11_half_kelly_cap"] = KELLY_FRACTION_CAP

    # ── Factor 12: Portfolio heat limit (6%) ──
    heat_remaining = max(6.0 - portfolio_heat_pct, 0.0) / 100.0
    kelly = min(kelly, heat_remaining)
    factors["f12_portfolio_heat"] = heat_remaining

    # Final clamp (H57): never exceed 0.20
    kelly = min(kelly, KELLY_CLAMP_MAX)
    kelly = max(kelly, 0.0)

    # Fractional shares: math.floor() only (H64)
    safe_price = max(price, 1e-9)  # H61
    shares = math.floor(kelly * equity / safe_price)
    shares = max(shares, 0)

    return {
        "kelly_fraction": kelly,
        "shares": shares,
        "factors": factors,
    }
