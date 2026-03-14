# RiskGate: Adaptive Risk Management / Trade Gating System
## Deep Research Report — March 2026

---

## Table of Contents
1. [Adaptive Veto Thresholds](#1-adaptive-veto-thresholds)
2. [Portfolio-Level Risk Management](#2-portfolio-level-risk-management)
3. [Cross-Exposure Management](#3-cross-exposure-management)
4. [Regime-Dependent Risk Limits](#4-regime-dependent-risk-limits)
5. [ISA-Specific Constraints](#5-isa-specific-constraints)
6. [Adaptive Position Sizing](#6-adaptive-position-sizing)
7. [Real-Time Risk Monitoring](#7-real-time-risk-monitoring)
8. [Feedback Loop & Self-Tuning](#8-feedback-loop--self-tuning)

---

## 1. Adaptive Veto Thresholds

### 1.1 Spread Veto — Adaptive Per-Ticker, Per-Exchange, Per-Time-of-Day

**The Problem**: A fixed spread threshold (e.g., "reject if spread > 0.5%") is wrong because:
- 3x leveraged LSE ETPs (QQQ3.L) have structurally wider spreads than SPY
- Spreads follow a U-shape intraday: wide at open, narrow mid-session, wide at close (McInish & Wood 1992, Brock & Kleidon 1992)
- Spreads widen dramatically during volatility spikes

**Academic Foundation**:
- Chan, Chung & Johnson (1995): Documented the U-shaped intraday spread pattern
- Corwin & Schultz (2012): Estimating bid-ask spreads from daily high-low prices
- Avellaneda & Stoikov (2008): Optimal spread = f(volatility, inventory, time-to-close)

**Adaptive Formula**:

```
adaptive_spread_max(ticker, time) = baseline_spread(ticker)
                                     * time_of_day_multiplier(time)
                                     * volatility_multiplier(current_vol / historical_vol)
                                     * regime_multiplier(regime)
```

Where:

```
baseline_spread(ticker) = EWMA of observed spreads for ticker over N days
                        = lambda * baseline_prev + (1-lambda) * observed_spread
                        (lambda = 0.94 per RiskMetrics)

time_of_day_multiplier(t):
    if t in [open, open+30min]:  return 1.5   # Wider spreads at open
    if t in [close-30min, close]: return 1.3   # Wider near close
    else:                         return 1.0   # Normal mid-session

volatility_multiplier = max(1.0, current_ATR_5min / median_ATR_5min_20d)
    # Scales up threshold when current volatility exceeds normal

regime_multiplier:
    NORMAL:   1.0
    CAUTION:  1.3
    CRISIS:   1.8   # Accept wider spreads in crisis (or you'll never trade)
```

**Recalibration Trigger**: Re-estimate `baseline_spread` daily at market close using the last 20 trading days of tick data.

**Veto Logic**:
```python
def spread_veto(ticker, current_spread, time_now):
    max_spread = adaptive_spread_max(ticker, time_now)
    if current_spread > max_spread:
        return VETO(reason=f"Spread {current_spread:.4f} > adaptive max {max_spread:.4f}")
    # Also compute spread cost as % of expected profit
    spread_cost_ratio = current_spread / expected_profit_per_trade
    if spread_cost_ratio > 0.30:  # spread eats >30% of expected profit
        return VETO(reason=f"Spread cost ratio {spread_cost_ratio:.1%} too high")
    return PASS
```

### 1.2 Volume Veto — Adaptive Based on Order Size and Ticker's Normal Volume

**The Problem**: A fixed minimum volume (e.g., "reject if volume < 10,000 shares") ignores:
- Your order's market impact (10,000 shares of QQQ3.L vs. 10,000 shares of SPY are wildly different)
- Volume varies by time-of-day (same U-shape as spreads)
- Volume drops on certain days (half-days, holidays)

**Academic Foundation**:
- Almgren & Chriss (2001): Optimal execution framework — market impact proportional to order_size / ADV
- Kyle (1985): Lambda (price impact coefficient) = sigma / sqrt(volume)

**Adaptive Formula**:

```
min_volume_ratio(ticker) = order_size / participation_rate_max
    where participation_rate_max = 0.02  (never be >2% of volume in any period)

# ADV-relative threshold
volume_veto_threshold(ticker, period) = max(
    absolute_minimum,                          # e.g., 1000 shares
    order_notional / (adv_20d * 0.02),        # 2% participation cap
    order_notional / (period_volume * 0.05)    # 5% of current period volume
)
```

**Market Impact Estimate** (Almgren & Chriss):
```
impact_bps = eta * sigma * sqrt(order_size / ADV)
    where eta = 0.1 to 0.5 (calibrated per ticker)

if impact_bps > max_acceptable_impact:  # e.g., 5 bps
    VETO("Market impact too high")
```

**Recalibration**: Update ADV (Average Daily Volume) with 20-day EWMA daily. Recalibrate `eta` monthly using realized fill quality vs. arrival price.

### 1.3 Correlation Veto — Adaptive Based on Regime

**The Problem**: In crisis, correlations spike to 1.0 (the "correlation crisis" phenomenon). A static correlation limit of 0.6 will either be too loose in calm markets or block everything in stress.

**Academic Foundation**:
- Engle (2002): Dynamic Conditional Correlation (DCC-GARCH)
- Longin & Solnik (2001): Correlations increase in bear markets, not just during extreme moves
- Forbes & Rigobon (2002): Contagion vs. interdependence — testing for correlation breakdowns

**Adaptive Formula (DCC-GARCH)**:

```
# Step 1: Univariate GARCH for each asset
sigma_i(t)^2 = omega + alpha * r_i(t-1)^2 + beta * sigma_i(t-1)^2

# Step 2: Standardize returns
z_i(t) = r_i(t) / sigma_i(t)

# Step 3: Dynamic correlation
Q(t) = (1 - a - b) * Q_bar + a * z(t-1) * z(t-1)' + b * Q(t-1)
R(t) = diag(Q(t))^(-1/2) * Q(t) * diag(Q(t))^(-1/2)

where:
    Q_bar = unconditional correlation matrix of standardized residuals
    a = 0.01-0.05 (news coefficient)
    b = 0.90-0.97 (persistence coefficient)
```

**Regime-Adaptive Correlation Threshold**:
```
max_pairwise_correlation(regime):
    BULL/NORMAL:  0.70   # Allow reasonably correlated positions
    CAUTION:      0.55   # Tighten when trouble brewing
    BEAR:         0.40   # Much tighter — correlations surge in bear
    CRISIS:       0.30   # Extremely tight — everything correlates

max_portfolio_avg_correlation(regime):
    NORMAL:  0.45
    CAUTION: 0.35
    BEAR:    0.25
    CRISIS:  0.20
```

**Recalibration**: DCC-GARCH re-estimated daily using last 252 trading days. Regime transitions trigger immediate threshold adjustment.

### 1.4 Position Concentration — Adaptive Based on Conviction & Regime

**The Problem**: A flat "max 20% in one position" ignores conviction level and market conditions.

**Academic Foundation**:
- De Prado (2018): Meta-labeling for conviction-adjusted sizing
- Thorp (2006): Kelly criterion for optimal concentration

**Adaptive Formula**:

```
max_position_pct(ticker, conviction, regime):
    base_max = 0.20  # 20% base maximum

    conviction_multiplier:
        LOW (0-0.3):     0.5   # Max 10%
        MEDIUM (0.3-0.6): 1.0  # Max 20%
        HIGH (0.6-0.8):   1.25 # Max 25%
        EXTREME (0.8-1.0): 1.5 # Max 30%

    regime_multiplier:
        NORMAL:  1.0
        CAUTION: 0.75  # Max becomes 15% at medium conviction
        BEAR:    0.50  # Max becomes 10% at medium conviction
        CRISIS:  0.25  # Max becomes 5% at medium conviction

    # Hard cap regardless of conviction/regime
    ABSOLUTE_MAX = 0.35  # Never more than 35% in one position

    return min(base_max * conviction_multiplier * regime_multiplier, ABSOLUTE_MAX)
```

**How Institutions Set This**:
- Bridgewater: ~2% per position in Pure Alpha (extreme diversification)
- Renaissance: Highly diversified, rarely >5% in any single name
- Prop desks: Typically 10-25% max per position, scaled by conviction
- For a 12-instrument ISA universe: 20-35% is reasonable (limited universe)

### 1.5 Sector Concentration — Adaptive in Trending Sector Markets

**Academic Foundation**:
- Vardharaj & Fabozzi (2007): ~75% of 5-year return variance explained by sector allocation
- IMF WP/16/158: Herfindahl-Hirschman Index for concentration measurement

**HHI-Based Measurement**:
```
HHI = sum(w_i^2) for all positions i in sector
    where w_i = weight of position i relative to total sector exposure

Effective_N_in_sector = 1 / HHI  # Number of effective positions
```

**Adaptive Sector Limits**:
```
max_sector_pct(sector, regime, sector_momentum):
    base_max = 0.40  # 40% base maximum per sector

    # If sector is in strong uptrend, allow more concentration
    if sector_momentum_zscore > 2.0:
        trend_bonus = 0.10  # Allow up to 50%
    elif sector_momentum_zscore > 1.0:
        trend_bonus = 0.05  # Allow up to 45%
    else:
        trend_bonus = 0.00

    regime_multiplier:
        NORMAL:  1.0
        CAUTION: 0.85
        BEAR:    0.70
        CRISIS:  0.50

    ABSOLUTE_SECTOR_MAX = 0.60  # Never >60% in one sector

    return min((base_max + trend_bonus) * regime_multiplier, ABSOLUTE_SECTOR_MAX)
```

**ISA-Specific Note**: With only 12 instruments all in leveraged tech/index ETPs, sector concentration is inherently high. The system should measure UNDERLYING sector exposure (e.g., QQQ3.L and NVD3.L both have heavy tech exposure). Consider a look-through approach to underlying holdings.

---

## 2. Portfolio-Level Risk Management

### 2.1 Maximum Simultaneous Positions — Adaptive

**Should This Adapt?** YES. Key factors:
- In low-correlation markets: more positions = more diversification benefit
- In high-correlation markets: more positions = more concentrated risk (false diversification)
- In low-volatility: can carry more positions (lower total portfolio vol)

**Adaptive Formula**:
```
max_positions(regime, avg_correlation, portfolio_vol):
    base_max = N  # Total universe size (12 for ISA)

    # Correlation adjustment
    effective_positions = base_max / (1 + (base_max - 1) * avg_correlation)
    # When avg_corr=0: effective=12, when avg_corr=1: effective=1

    # Regime cap
    regime_cap:
        NORMAL:  min(10, base_max)
        CAUTION: min(7, base_max)
        BEAR:    min(5, base_max)
        CRISIS:  min(3, base_max)

    # Volatility adjustment
    vol_ratio = portfolio_vol / target_portfolio_vol
    if vol_ratio > 1.5:
        vol_penalty = int((vol_ratio - 1.0) * 3)  # reduce by ~1-2 positions
    else:
        vol_penalty = 0

    return max(1, min(effective_positions, regime_cap) - vol_penalty)
```

### 2.2 Maximum Daily Drawdown — Institutional Approach

**How Institutions Set Daily Drawdown Limits**:
- Prop trading desks: Typically 2-5% daily drawdown limit (static or trailing)
- Hedge funds: 1-3% daily limit, with tiered escalation
- Market makers: 1-2% hard daily limit

**Adaptive Daily Drawdown**:
```
max_daily_drawdown(regime, recent_performance):
    base_limit = 0.02  # 2% of portfolio value

    # Regime scaling
    regime_factor:
        NORMAL:  1.0    # 2% limit
        CAUTION: 0.75   # 1.5% limit
        BEAR:    0.50   # 1% limit
        CRISIS:  0.25   # 0.5% limit

    # Performance scaling (tighter after losses, looser after wins)
    if trailing_5d_return < -0.03:  # Lost >3% in last 5 days
        perf_factor = 0.50  # Halve the limit
    elif trailing_5d_return < -0.01:
        perf_factor = 0.75
    elif trailing_5d_return > 0.02:
        perf_factor = 1.25  # Slightly more room after winning streak
    else:
        perf_factor = 1.0

    ABSOLUTE_DAILY_MAX = 0.03  # Never more than 3%
    ABSOLUTE_DAILY_MIN = 0.003 # Never less than 0.3% (too restrictive)

    limit = base_limit * regime_factor * perf_factor
    return clip(limit, ABSOLUTE_DAILY_MIN, ABSOLUTE_DAILY_MAX)
```

**Tiered Escalation (Institutional Pattern)**:
```
Daily Drawdown Tiers:
    0% to 50% of limit:  NORMAL — continue trading
    50% to 75% of limit: REDUCE — cut all position sizes by 50%, no new positions
    75% to 90% of limit: FLATTEN — close all positions, no new trades
    90% to 100% of limit: HALT — system fully stops, requires manual restart

Example with 2% limit ($200 on $10,000):
    $0-$100 loss:   NORMAL
    $100-$150 loss:  REDUCE
    $150-$180 loss:  FLATTEN
    $180-$200 loss:  HALT
```

### 2.3 VaR (Value at Risk) for Intraday Portfolios

**Academic Foundation**:
- RiskMetrics (J.P. Morgan, 1996): EWMA-based VaR
- Giot & Laurent (2003): CGARCH-EVT-Copula for intraday VaR

**Parametric VaR (EWMA-based, real-time)**:
```
# Single-asset VaR
VaR_alpha(position_i) = -position_value * z_alpha * sigma_i(t)
    where z_alpha = quantile of standard normal (z_0.99 = 2.326)
    sigma_i(t) = EWMA volatility with lambda=0.94

# Portfolio VaR
VaR_portfolio = -sqrt(w' * Sigma * w) * z_alpha * portfolio_value
    where:
        w = vector of position weights
        Sigma = EWMA covariance matrix
        Sigma(t) = lambda * Sigma(t-1) + (1-lambda) * r(t-1) * r(t-1)'

# For intraday, scale by sqrt(holding_period / trading_day)
VaR_intraday = VaR_daily * sqrt(holding_hours / 6.5)
```

**VaR Veto**:
```python
def var_veto(proposed_trade, portfolio):
    current_var = compute_portfolio_var(portfolio)
    hypothetical_var = compute_portfolio_var(portfolio + proposed_trade)
    marginal_var = hypothetical_var - current_var

    if hypothetical_var > max_portfolio_var:
        return VETO(f"Portfolio VaR {hypothetical_var:.2%} exceeds limit {max_portfolio_var:.2%}")
    if marginal_var > max_marginal_var:
        return VETO(f"Marginal VaR {marginal_var:.2%} too high")
    return PASS
```

### 2.4 Expected Shortfall (CVaR) — Superior to VaR for Tail Risk

**Why CVaR > VaR**:
- VaR only says "we won't lose more than X with 99% confidence" — says nothing about the 1% tail
- CVaR = average loss GIVEN that VaR is breached (Artzner et al., 1999)
- CVaR is a COHERENT risk measure (subadditive, monotone, positive homogeneous, translation invariant)
- VaR is NOT coherent — fails subadditivity (diversification can increase VaR!)
- Basel III/IV now requires Expected Shortfall for internal models (97.5% confidence)

**Formula**:
```
ES_alpha(X) = -E[X | X <= -VaR_alpha(X)]
            = -(1/alpha) * integral_0^alpha VaR_gamma(X) d_gamma

# Practical computation (historical simulation):
# 1. Sort daily returns from worst to best
# 2. Take the worst alpha% of returns
# 3. Average them
# That's your CVaR

# For 99% CVaR with 252 observations:
# Take the worst 2-3 returns, average them
```

**Parametric CVaR (Gaussian approximation)**:
```
CVaR_alpha = -mu + sigma * phi(z_alpha) / alpha
    where:
        mu = expected return
        sigma = EWMA volatility
        phi(z) = standard normal PDF at quantile z
        alpha = tail probability (0.01 for 99% CVaR)

# For normal distribution at 99%:
CVaR_99 = mu - sigma * phi(2.326) / 0.01
        = mu - sigma * 0.02665 / 0.01
        = mu - sigma * 2.665
# Note: CVaR_99_normal ~ 2.665 * sigma  vs  VaR_99_normal ~ 2.326 * sigma
# CVaR is ~15% more conservative than VaR under normality
# Under fat tails, the difference is MUCH larger
```

**Implementation for Intraday**:
```python
def compute_cvar_realtime(returns_5min, alpha=0.01):
    """Compute CVaR from 5-minute return series"""
    n = len(returns_5min)
    sorted_returns = np.sort(returns_5min)
    cutoff = int(np.ceil(n * alpha))
    if cutoff == 0:
        cutoff = 1
    cvar = -np.mean(sorted_returns[:cutoff])
    return cvar

def cvar_veto(proposed_trade, portfolio, max_cvar):
    hypothetical_cvar = compute_portfolio_cvar(portfolio + proposed_trade)
    if hypothetical_cvar > max_cvar:
        return VETO(f"Portfolio CVaR {hypothetical_cvar:.2%} exceeds limit {max_cvar:.2%}")
    return PASS
```

### 2.5 Real-Time Portfolio Risk from Tick Data

**EWMA Covariance (RiskMetrics approach, lambda=0.94)**:
```
# Initialize with sample covariance
Sigma_0 = sample_covariance(first_60_returns)

# Update every 5-second bar:
for each new return vector r(t):
    Sigma(t) = lambda * Sigma(t-1) + (1 - lambda) * r(t) * r(t)'

portfolio_vol(t) = sqrt(w' * Sigma(t) * w)
portfolio_var(t) = z_alpha * portfolio_vol(t) * portfolio_value
```

**Optimal Lambda** (Guermat & Harris, 2001):
```
# lambda = 0.94 (RiskMetrics daily default)
# For 5-second bars: lambda should be higher (more data points per day)
# Approximate: lambda_5s = 1 - (1-0.94) * (daily_bars / intraday_bars)
# With ~4680 5-second bars per day vs 1 daily bar:
# lambda_5s = 1 - 0.06/4680 = 0.999987
# In practice, use lambda_5s ~ 0.999 to 0.9999
```

### 2.6 Kelly Criterion for Portfolio-Level Sizing

**Single-Asset Kelly**:
```
f* = (mu - r) / sigma^2
    where:
        f* = optimal fraction of capital to bet
        mu = expected return
        r = risk-free rate
        sigma = volatility of returns
```

**Multi-Asset Kelly** (Thorp, 2006; Bell & Cover, 1980):
```
# For a portfolio of N correlated assets:
f* = Sigma^(-1) * (mu - r)
    where:
        f* = N-dimensional vector of optimal fractions
        Sigma = N x N covariance matrix
        mu = N-dimensional expected return vector
        r = risk-free rate (scalar)

# This is equivalent to the tangency portfolio (maximum Sharpe ratio)!
# Kelly portfolio = Tangency portfolio (under log-utility)
```

**Fractional Kelly — What Fraction and Should It Adapt?**:
```
fractional_kelly(regime, estimation_uncertainty):
    # Base: Use half-Kelly (most common in practice)
    base_fraction = 0.50

    # Regime adjustment
    regime_factor:
        NORMAL:  1.0    # Half Kelly
        CAUTION: 0.75   # 0.375 Kelly
        BEAR:    0.50   # Quarter Kelly
        CRISIS:  0.25   # Eighth Kelly

    # Estimation uncertainty adjustment
    # More uncertain parameters => more conservative
    if parameter_confidence < 0.5:
        uncertainty_factor = 0.50
    elif parameter_confidence < 0.75:
        uncertainty_factor = 0.75
    else:
        uncertainty_factor = 1.0

    return base_fraction * regime_factor * uncertainty_factor
```

**Why Fractional Kelly**:
- Full Kelly has ~50% drawdown probability before doubling (too aggressive)
- Half Kelly: ~75% of full Kelly growth rate but ~50% the variance
- Quarter Kelly: ~94% risk reduction vs. full Kelly, ~56% of growth rate
- Professional quants typically use 10-25% of full Kelly (De Prado, Thorp)

---

## 3. Cross-Exposure Management

### 3.1 ETP to Underlying Cross-Exposure Blocking

**The Problem**: Holding QQQ3.L (3x Nasdaq 100) and NVD3.L (3x NVIDIA) creates hidden concentration because NVIDIA is a top holding in Nasdaq 100. Similarly, 3LUS.L (3x S&P 500) and QQQ3.L overlap heavily.

**Look-Through Exposure Calculation**:
```python
def compute_look_through_exposure(positions, etp_holdings_map):
    """
    positions: dict of {ticker: weight}  e.g., {"QQQ3.L": 0.25, "NVD3.L": 0.15}
    etp_holdings_map: dict of {ticker: {underlying: weight}}
    """
    underlying_exposure = defaultdict(float)

    for ticker, pos_weight in positions.items():
        leverage = etp_leverage[ticker]  # e.g., 3
        for underlying, holding_weight in etp_holdings_map[ticker].items():
            # Effective exposure = position_weight * leverage * holding_weight
            underlying_exposure[underlying] += pos_weight * leverage * holding_weight

    return underlying_exposure

# Example:
# QQQ3.L weight=25%, leverage=3x, AAPL=8% of Nasdaq => AAPL exposure = 0.25*3*0.08 = 6%
# NVD3.L weight=15%, leverage=3x, NVDA=100%          => NVDA exposure = 0.15*3*1.0  = 45%
# If NVDA is also 5% of Nasdaq via QQQ3.L:           => extra NVDA    = 0.25*3*0.05 = 3.75%
# Total NVDA exposure = 48.75% — VERY concentrated
```

**Cross-Exposure Veto**:
```python
def cross_exposure_veto(proposed_trade, portfolio):
    hypothetical = portfolio + proposed_trade
    look_through = compute_look_through_exposure(hypothetical)

    for underlying, exposure in look_through.items():
        if exposure > MAX_SINGLE_UNDERLYING_EXPOSURE:  # e.g., 40%
            return VETO(f"Underlying {underlying} exposure {exposure:.1%} too high")

    # Check sector-level look-through
    sector_exposure = aggregate_by_sector(look_through)
    for sector, exposure in sector_exposure.items():
        if exposure > MAX_SECTOR_EXPOSURE:  # e.g., 60%
            return VETO(f"Sector {sector} exposure {exposure:.1%} too high")

    return PASS
```

### 3.2 Cross-Asset Correlation Monitoring in Real-Time

**DCC-GARCH Implementation** (Engle, 2002):
```
# Real-time correlation matrix update (simplified)
# Every 5-second bar:

for each asset pair (i, j):
    # Update EWMA correlation
    rho_ij(t) = lambda_corr * rho_ij(t-1) + (1-lambda_corr) * z_i(t) * z_j(t)
    # where z_i = standardized return = r_i / sigma_i

    lambda_corr = 0.97  # Higher persistence for correlations

# Detect correlation spike:
if rho_ij(t) > rho_ij_95th_percentile:
    emit_alert("CORRELATION_SPIKE", i, j, rho_ij(t))

# Portfolio-level metric:
avg_pairwise_correlation = mean(rho_ij for all pairs)
if avg_pairwise_correlation > regime_threshold:
    trigger_regime_change(CAUTION or BEAR)
```

### 3.3 Detecting Hidden Correlations During Stress

**Methods**:

1. **Copula-based tail dependence** (Joe, 1997):
```
# Tail dependence coefficient (lower tail)
lambda_L = lim_{u->0} P(U1 <= u | U2 <= u)
# Measures probability of joint extreme losses
# If lambda_L >> 0 when historically ~0, hidden correlation is emerging
```

2. **Rolling window correlation vs. full-sample** (Forbes & Rigobon, 2002):
```
# Compare short-window (20 bars) vs long-window (252 bars) correlation
corr_short = rolling_corr(20_bars)
corr_long  = rolling_corr(252_bars)
delta_corr = corr_short - corr_long

if delta_corr > 0.3:  # Correlation has surged 0.3+ above baseline
    emit_alert("HIDDEN_CORRELATION_EMERGING", assets, delta_corr)
```

3. **Principal Component Analysis (PCA) monitoring**:
```
# Track explained variance of PC1
# In normal markets: PC1 explains ~30-40% of variance
# In crisis: PC1 explains >60% (everything moves together)
pc1_variance_ratio = pca.explained_variance_ratio_[0]
if pc1_variance_ratio > 0.50:
    emit_alert("CORRELATION_CONVERGENCE", pc1_variance_ratio)
```

---

## 4. Regime-Dependent Risk Limits

### 4.1 Regime Detection

**Multi-Signal Regime Classification**:
```python
def detect_regime(vix, credit_spread, market_breadth, recent_drawdown):
    """
    Inputs:
        vix: current VIX level
        credit_spread: ICE BofA US HY OAS (bps)
        market_breadth: % of S&P 500 above 200-day MA
        recent_drawdown: max drawdown over trailing 20 days
    """
    score = 0

    # VIX component (0-4 points)
    if vix < 15:    score += 0  # Calm
    elif vix < 20:  score += 1  # Slightly elevated
    elif vix < 25:  score += 2  # Elevated
    elif vix < 35:  score += 3  # High
    else:           score += 4  # Crisis

    # Credit spread component (0-4 points)
    if credit_spread < 350:    score += 0  # Normal
    elif credit_spread < 450:  score += 1  # Widening
    elif credit_spread < 600:  score += 2  # Stressed
    elif credit_spread < 800:  score += 3  # Distressed
    else:                      score += 4  # Crisis

    # Market breadth component (0-4 points)
    if market_breadth > 0.70:  score += 0  # Healthy
    elif market_breadth > 0.50: score += 1  # Weakening
    elif market_breadth > 0.30: score += 2  # Poor
    elif market_breadth > 0.15: score += 3  # Very poor
    else:                       score += 4  # Capitulation

    # Recent drawdown component (0-4 points)
    if recent_drawdown > -0.02:  score += 0  # Normal
    elif recent_drawdown > -0.05: score += 1
    elif recent_drawdown > -0.10: score += 2
    elif recent_drawdown > -0.15: score += 3
    else:                         score += 4  # Severe drawdown

    # Classification (0-16 scale)
    if score <= 3:   return NORMAL   # All-clear
    elif score <= 6: return CAUTION  # Be careful
    elif score <= 10: return BEAR    # Defensive mode
    else:            return CRISIS   # Capital preservation only
```

**HMM-Based Regime Detection** (Hamilton, 1989):
```
# Hidden Markov Model with 3 states
# State 1 (Bull): mu=+0.05%/day, sigma=0.8%/day
# State 2 (Normal): mu=+0.02%/day, sigma=1.2%/day
# State 3 (Bear/Crisis): mu=-0.10%/day, sigma=2.5%/day

# Use hmmlearn or pomegranate library
from hmmlearn import hmm
model = hmm.GaussianHMM(n_components=3, covariance_type="full")
model.fit(returns_matrix)

# Get filtered probability of each regime for today
regime_probs = model.predict_proba(today_features)
current_regime = np.argmax(regime_probs[-1])
```

### 4.2 VIX-Based Risk Scaling

**Academic Foundation**: Whaley (2000, 2009): VIX as fear gauge; Banerjee et al. (2007): VIX regime-switching

```
vix_risk_scalar(vix):
    """Scale all risk limits by this factor"""
    if vix < 12:    return 1.10  # Complacency premium — slightly generous
    elif vix < 16:  return 1.00  # Normal
    elif vix < 20:  return 0.85  # Elevated
    elif vix < 25:  return 0.70  # High
    elif vix < 30:  return 0.55  # Very high
    elif vix < 40:  return 0.40  # Crisis
    else:           return 0.25  # Extreme crisis (2008, Mar 2020)

    # Apply to all risk limits:
    # max_position_size *= vix_risk_scalar
    # max_daily_drawdown *= vix_risk_scalar (makes limit tighter)
    # max_simultaneous_positions *= vix_risk_scalar
```

### 4.3 Credit Spread-Based Risk Scaling

**Academic Foundation**: Gilchrist & Zakrajsek (2012): Credit spreads predict recessions; Boyarchenko & Elias (2024): Global credit cycle

```
credit_risk_scalar(hy_oas_bps):
    """ICE BofA US High Yield OAS"""
    if hy_oas_bps < 300:    return 1.05   # Tight spreads, benign
    elif hy_oas_bps < 400:  return 1.00   # Normal
    elif hy_oas_bps < 500:  return 0.85   # Widening
    elif hy_oas_bps < 650:  return 0.65   # Stressed
    elif hy_oas_bps < 800:  return 0.45   # Distressed
    else:                   return 0.25   # Crisis (2008: 2000+ bps)
```

### 4.4 Market Breadth-Based Position Limits

**Academic Foundation**: McClellan Oscillator (McClellan & McClellan, 1969); Advance-Decline Line studies

```
breadth_position_scalar(pct_above_200ma):
    """% of index constituents above 200-day moving average"""
    if pct_above_200ma > 0.75:  return 1.10   # Strong breadth
    elif pct_above_200ma > 0.60: return 1.00   # Healthy
    elif pct_above_200ma > 0.45: return 0.85   # Deteriorating
    elif pct_above_200ma > 0.30: return 0.65   # Weak
    elif pct_above_200ma > 0.15: return 0.45   # Very weak
    else:                        return 0.25   # Breadth collapse
```

### 4.5 Dynamic Risk Budgeting

**Academic Foundation**: Bruder & Roncalli (2012): Equal Risk Contribution; Jurczenko (2015): Risk-Based and Factor Investing

**Composite Risk Budget**:
```
total_risk_budget(regime):
    """What fraction of max-risk capacity to deploy"""
    NORMAL:  0.80 - 1.00  # Deploy 80-100% of risk budget
    CAUTION: 0.50 - 0.70  # Deploy 50-70%
    BEAR:    0.25 - 0.40  # Deploy 25-40%
    CRISIS:  0.05 - 0.15  # Deploy 5-15% (mostly cash)

# Per-position risk allocation:
risk_budget_per_position = total_risk_budget / max_positions * conviction_weight
```

---

## 5. ISA-Specific Constraints

### 5.1 Annual Contribution Limit Enforcement (GBP 20,000)

```python
class ISAContributionTracker:
    def __init__(self, tax_year_start, annual_limit=20000):
        self.tax_year_start = tax_year_start  # April 6
        self.annual_limit = annual_limit
        self.contributions = []  # (date, amount_gbp)

    def total_contributed(self):
        return sum(amount for _, amount in self.contributions)

    def remaining_allowance(self):
        return max(0, self.annual_limit - self.total_contributed())

    def can_contribute(self, amount_gbp):
        return amount_gbp <= self.remaining_allowance()

    def contribution_veto(self, proposed_buy_amount_gbp):
        if not self.can_contribute(proposed_buy_amount_gbp):
            return VETO(
                f"ISA contribution {proposed_buy_amount_gbp:.2f} would exceed "
                f"remaining allowance {self.remaining_allowance():.2f}"
            )
        return PASS

    def days_until_reset(self):
        """Days until next April 6"""
        today = date.today()
        next_reset = date(today.year, 4, 6)
        if today >= next_reset:
            next_reset = date(today.year + 1, 4, 6)
        return (next_reset - today).days
```

**Important**: Only CONTRIBUTIONS count, not withdrawals or gains. Selling does NOT free up allowance. Track cumulative buys (cost basis), not portfolio value.

### 5.2 No Margin / No Leverage (Positions Must Be Fully Funded)

```python
def fully_funded_veto(proposed_trade, cash_available):
    """ISA: cannot use margin. Must have cash to cover full purchase."""
    trade_cost = proposed_trade.quantity * proposed_trade.price
    commission = estimate_commission(proposed_trade)
    total_cost = trade_cost + commission

    if total_cost > cash_available:
        return VETO(
            f"Insufficient cash: need {total_cost:.2f}, have {cash_available:.2f}. "
            f"ISA does not allow margin trading."
        )
    return PASS

def no_short_selling_veto(proposed_trade):
    """ISA: cannot short sell"""
    if proposed_trade.side == SELL and proposed_trade.quantity > current_holding:
        return VETO("Short selling not allowed in ISA")
    return PASS
```

**Note on Leveraged ETPs**: 3x ETPs like QQQ3.L are ALLOWED in ISAs because the leverage is embedded in the product, not margin. The ISA holder buys shares outright; the ETP provider manages the leverage internally.

### 5.3 FX Exposure Limits Within ISA

**The Problem**: All 12 ISA instruments are LSE-listed but denominated in GBP (settled in GBP). However, the UNDERLYING exposure is primarily USD (Nasdaq, S&P 500) and some USD/other currencies via semiconductor companies. FX risk is real but INDIRECT.

```python
def fx_exposure_tracker(positions, etp_fx_exposure_map):
    """
    Track effective FX exposure through ETP look-through
    """
    fx_exposure = defaultdict(float)

    for ticker, weight in positions.items():
        leverage = etp_leverage[ticker]
        for currency, fx_pct in etp_fx_exposure_map[ticker].items():
            fx_exposure[currency] += weight * leverage * fx_pct

    return fx_exposure

# Example:
# QQQ3.L: 100% USD underlying, 3x leverage, 25% weight => 75% USD exposure
# Result: total USD exposure might be 200%+ due to leverage amplification

def fx_veto(proposed_trade, portfolio, max_fx_exposure_pct=3.0):
    """
    Veto if adding this trade pushes any single FX exposure too high.
    For leveraged ETPs, effective FX exposure can exceed 100%.
    max_fx_exposure_pct: e.g., 3.0 = 300% of portfolio in one currency
    """
    hypothetical = portfolio + proposed_trade
    fx_exposure = fx_exposure_tracker(hypothetical)

    for currency, exposure in fx_exposure.items():
        if exposure > max_fx_exposure_pct:
            return VETO(f"FX exposure to {currency}: {exposure:.0%} exceeds {max_fx_exposure_pct:.0%}")
    return PASS
```

### 5.4 Real-Time Contribution Tracking

```python
class ISARealTimeTracker:
    def __init__(self):
        self.redis_key = "isa:contributions:current_year"

    def record_buy(self, ticker, quantity, price_gbp, timestamp):
        contribution = quantity * price_gbp
        # Store in Redis for real-time access
        redis.rpush(self.redis_key, json.dumps({
            "timestamp": timestamp.isoformat(),
            "ticker": ticker,
            "quantity": quantity,
            "price_gbp": price_gbp,
            "contribution_gbp": contribution
        }))
        redis.incrbyfloat("isa:total_contributed", contribution)

    def get_remaining(self):
        total = float(redis.get("isa:total_contributed") or 0)
        return max(0, 20000.0 - total)

    def tax_year_reset(self):
        """Called on April 6 each year"""
        # Archive previous year
        redis.rename(self.redis_key, f"isa:contributions:{prev_year}")
        redis.set("isa:total_contributed", 0)
```

---

## 6. Adaptive Position Sizing

### 6.1 Kelly Criterion with Adaptive Parameters

**Full Kelly**:
```
f* = (p * b - q) / b    [discrete: win probability p, payout ratio b, q = 1-p]
f* = (mu - r) / sigma^2  [continuous: excess return mu-r, volatility sigma]
```

**Adaptive Parameters**:
```python
def adaptive_kelly(ticker, lookback=100):
    """
    Kelly with parameters estimated from recent trades
    """
    recent_trades = get_recent_trades(ticker, lookback)

    # Estimate win rate and payoff ratio from recent data
    wins = [t for t in recent_trades if t.pnl > 0]
    losses = [t for t in recent_trades if t.pnl <= 0]

    p = len(wins) / len(recent_trades)
    avg_win = np.mean([t.pnl for t in wins]) if wins else 0
    avg_loss = abs(np.mean([t.pnl for t in losses])) if losses else 1
    b = avg_win / avg_loss  # Payoff ratio

    kelly_fraction = (p * b - (1 - p)) / b

    # Bayesian shrinkage toward prior (reduce estimation error)
    # Prior: 50% win rate, 1:1 payoff
    n = len(recent_trades)
    prior_weight = 20 / (n + 20)  # More data => less prior influence
    kelly_fraction = (1 - prior_weight) * kelly_fraction + prior_weight * 0.0

    return max(0, kelly_fraction)  # Never negative (never bet against yourself)
```

### 6.2 Fractional Kelly — Optimal Fraction and Adaptation

**What Fraction?**
```
Fraction    Growth Rate (% of full Kelly)    Drawdown Reduction
1.00x       100%                              0% (baseline)
0.75x       94%                               ~44%
0.50x       75%                               ~75%
0.25x       44%                               ~94%
0.10x       19%                               ~99%
```

**Adaptive Fraction**:
```python
def adaptive_kelly_fraction(regime, estimation_quality, recent_performance):
    """
    Determines what fraction of full Kelly to use
    """
    # Base fractions by regime
    regime_fractions = {
        "NORMAL":  0.40,  # 40% Kelly in normal markets
        "CAUTION": 0.25,  # 25% Kelly when cautious
        "BEAR":    0.15,  # 15% Kelly in bear
        "CRISIS":  0.05,  # 5% Kelly in crisis
    }
    base = regime_fractions[regime]

    # Estimation quality adjustment
    # n_trades: number of trades used to estimate parameters
    if n_trades < 30:
        quality_factor = 0.50  # Very uncertain estimates
    elif n_trades < 100:
        quality_factor = 0.75
    elif n_trades < 500:
        quality_factor = 0.90
    else:
        quality_factor = 1.00

    # Recent performance adjustment
    if recent_sharpe < 0:  # Losing money
        perf_factor = 0.50
    elif recent_sharpe < 0.5:
        perf_factor = 0.75
    else:
        perf_factor = 1.00

    fraction = base * quality_factor * perf_factor

    # Floor and ceiling
    return clip(fraction, 0.02, 0.50)
```

### 6.3 Volatility-Based Sizing (ATR-Adjusted)

**Academic Foundation**: Van Tharp (2006): Percent Volatility Model; Wilder (1978): ATR

```python
def atr_position_size(ticker, portfolio_value, risk_pct=0.01, atr_multiplier=2.0):
    """
    Size position so that an atr_multiplier * ATR move = risk_pct of portfolio

    Parameters:
        risk_pct: fraction of portfolio to risk per trade (e.g., 0.01 = 1%)
        atr_multiplier: how many ATRs for stop-loss (2.0 typical intraday)
    """
    atr = compute_atr(ticker, period=14)  # 14-period ATR
    price = current_price(ticker)

    dollar_risk = portfolio_value * risk_pct
    stop_distance = atr * atr_multiplier

    shares = int(dollar_risk / stop_distance)
    position_value = shares * price
    position_pct = position_value / portfolio_value

    return PositionSize(
        shares=shares,
        position_value=position_value,
        position_pct=position_pct,
        stop_loss=price - stop_distance,
        risk_amount=dollar_risk
    )
```

**Adaptive ATR Multiplier**:
```python
def adaptive_atr_multiplier(regime, time_of_day, ticker_vol_percentile):
    """
    Wider stops in volatile conditions (higher multiplier = fewer shares)
    """
    base_multiplier = 2.0

    # Regime adjustment
    regime_adj = {"NORMAL": 1.0, "CAUTION": 1.25, "BEAR": 1.5, "CRISIS": 2.0}

    # Time of day (wider stops near open/close due to higher vol)
    if minutes_since_open < 30 or minutes_to_close < 30:
        time_adj = 1.3
    else:
        time_adj = 1.0

    # Ticker-specific: if this ticker is more volatile than usual
    if ticker_vol_percentile > 0.90:  # In top 10% of its own vol history
        vol_adj = 1.3
    elif ticker_vol_percentile > 0.75:
        vol_adj = 1.15
    else:
        vol_adj = 1.0

    return base_multiplier * regime_adj[regime] * time_adj * vol_adj
```

### 6.4 Conviction-Based Sizing

**De Prado's Meta-Labeling Approach**:
```python
def meta_label_position_size(primary_signal, meta_model, features, max_position_pct):
    """
    primary_signal: direction from primary model (+1 or -1)
    meta_model: trained classifier predicting P(profitable | signal)
    """
    # Get probability that this trade will be profitable
    prob_profitable = meta_model.predict_proba(features)[0, 1]

    # Sigmoid Optimal Position Sizing (SOPS)
    # Maps probability to position size non-linearly
    if prob_profitable < 0.50:
        return 0  # Don't trade if less than coin-flip

    # Scale from 0 at p=0.5 to max at p=1.0
    conviction = (prob_profitable - 0.50) / 0.50  # 0 to 1

    # Non-linear scaling (sigmoid)
    # More conservative at edges, aggressive in middle
    position_pct = max_position_pct * (2 / (1 + np.exp(-3 * conviction)) - 1)

    return position_pct
```

**Combined Sizing (Kelly + ATR + Conviction + Regime)**:
```python
def final_position_size(ticker, portfolio, signal, meta_prob, regime):
    """
    The MASTER position sizing function combining all approaches
    """
    # 1. Kelly-optimal (theoretical maximum)
    kelly_size = adaptive_kelly(ticker) * adaptive_kelly_fraction(regime)

    # 2. ATR-based (risk-normalized)
    atr_size = atr_position_size(ticker, portfolio.value).position_pct

    # 3. Conviction-based (meta-label probability)
    conviction_size = meta_label_position_size(signal, meta_model, features, 0.30)

    # 4. Take the MINIMUM of Kelly and ATR (safety first)
    risk_bounded_size = min(kelly_size, atr_size)

    # 5. Scale by conviction
    final_size = risk_bounded_size * (conviction_size / 0.30)

    # 6. Apply regime cap
    regime_max = max_position_pct(ticker, conviction=meta_prob, regime=regime)
    final_size = min(final_size, regime_max)

    # 7. Apply absolute portfolio cap
    final_size = min(final_size, 0.35)  # Never > 35%

    # 8. Apply ISA cash constraint
    max_affordable = portfolio.cash / portfolio.value
    final_size = min(final_size, max_affordable)

    return final_size
```

---

## 7. Real-Time Risk Monitoring

### 7.1 Detecting Risk Building Across the Portfolio

**Multi-Metric Dashboard**:
```python
class RealTimeRiskMonitor:
    def __init__(self):
        self.risk_metrics = {}

    def update(self, portfolio, market_data):
        # Core metrics (update every 5 seconds)
        self.risk_metrics = {
            "portfolio_var_99": compute_portfolio_var(portfolio, 0.01),
            "portfolio_cvar_99": compute_portfolio_cvar(portfolio, 0.01),
            "portfolio_vol_annualized": portfolio_vol * sqrt(252),
            "daily_pnl": portfolio.value - portfolio.start_of_day_value,
            "daily_drawdown": portfolio.max_value_today - portfolio.value,
            "avg_pairwise_correlation": compute_avg_correlation(portfolio),
            "hhi_concentration": compute_hhi(portfolio),
            "effective_positions": 1.0 / compute_hhi(portfolio),
            "max_position_pct": max(w for w in portfolio.weights.values()),
            "total_leverage": sum(w * lev for w, lev in zip(weights, leverages)),
            "vix_level": market_data.vix,
            "regime": detect_regime(market_data),
            "pc1_variance_ratio": compute_pc1_ratio(portfolio),
        }

        # Risk score (0-100, higher = more dangerous)
        self.risk_score = self.compute_composite_risk_score()

    def compute_composite_risk_score(self):
        m = self.risk_metrics
        score = 0

        # VaR relative to limit
        score += 20 * min(1.0, m["portfolio_var_99"] / MAX_VAR)

        # Drawdown relative to limit
        score += 25 * min(1.0, m["daily_drawdown"] / MAX_DAILY_DRAWDOWN)

        # Correlation convergence
        score += 15 * min(1.0, m["avg_pairwise_correlation"] / 0.80)

        # Concentration
        score += 15 * min(1.0, m["max_position_pct"] / 0.40)

        # VIX level
        score += 15 * min(1.0, m["vix_level"] / 40.0)

        # PC1 dominance (correlation crisis)
        score += 10 * min(1.0, m["pc1_variance_ratio"] / 0.60)

        return min(100, score)
```

### 7.2 Drawdown Alerts and Circuit Breakers

**4-Tier System (Institutional Standard)**:
```python
class DrawdownCircuitBreaker:
    TIERS = {
        "GREEN":  (0.00, 0.50),  # 0-50% of daily limit used
        "YELLOW": (0.50, 0.75),  # 50-75%: reduce exposure
        "ORANGE": (0.75, 0.90),  # 75-90%: flatten all
        "RED":    (0.90, 1.00),  # 90-100%: HALT
    }

    def __init__(self, max_daily_drawdown):
        self.max_dd = max_daily_drawdown
        self.current_tier = "GREEN"

    def check(self, current_drawdown):
        ratio = abs(current_drawdown) / self.max_dd

        if ratio >= 0.90:
            return self.trigger_halt()
        elif ratio >= 0.75:
            return self.trigger_flatten()
        elif ratio >= 0.50:
            return self.trigger_reduce()
        else:
            return self.continue_normal()

    def trigger_reduce(self):
        """YELLOW: Cut all position sizes by 50%, no new positions"""
        return Action(
            tier="YELLOW",
            actions=["halve_all_position_sizes", "block_new_entries"],
            message="Drawdown at 50-75% of limit. Reducing exposure."
        )

    def trigger_flatten(self):
        """ORANGE: Close all positions immediately"""
        return Action(
            tier="ORANGE",
            actions=["close_all_positions", "block_all_trading"],
            message="Drawdown at 75-90% of limit. Flattening portfolio."
        )

    def trigger_halt(self):
        """RED: Full system halt, requires manual restart"""
        return Action(
            tier="RED",
            actions=["close_all_positions", "halt_system", "notify_operator"],
            message="CRITICAL: Drawdown at 90%+ of limit. SYSTEM HALTED."
        )
```

### 7.3 Correlation Spike Detection

```python
class CorrelationMonitor:
    def __init__(self, lookback=1260, spike_threshold_zscore=2.5):
        self.lookback = lookback  # 5 years of daily data
        self.spike_threshold = spike_threshold_zscore

    def check_for_spikes(self, correlation_matrix):
        """
        Detect when current correlations are abnormally high
        compared to historical distribution
        """
        # Flatten upper triangle of correlation matrix
        n = correlation_matrix.shape[0]
        current_corrs = []
        for i in range(n):
            for j in range(i+1, n):
                current_corrs.append(correlation_matrix[i, j])

        avg_corr = np.mean(current_corrs)

        # Compare to historical distribution
        historical_avg_corr = self.get_historical_avg_correlations()
        z_score = (avg_corr - np.mean(historical_avg_corr)) / np.std(historical_avg_corr)

        if z_score > self.spike_threshold:
            return CorrelationAlert(
                level="CRITICAL",
                z_score=z_score,
                avg_correlation=avg_corr,
                message=f"Correlation spike: avg={avg_corr:.2f}, z={z_score:.1f}"
            )
        elif z_score > self.spike_threshold * 0.6:
            return CorrelationAlert(
                level="WARNING",
                z_score=z_score,
                avg_correlation=avg_corr,
                message=f"Correlation elevated: avg={avg_corr:.2f}, z={z_score:.1f}"
            )
        return None
```

### 7.4 State Machine: NORMAL -> REDUCE -> FLATTEN -> HALT

```python
class RiskStateMachine:
    """
    State transitions based on composite risk score
    """
    TRANSITIONS = {
        "NORMAL":  {"REDUCE": 60, "FLATTEN": 80, "HALT": 95},
        "REDUCE":  {"NORMAL": 40, "FLATTEN": 75, "HALT": 90},
        "FLATTEN": {"REDUCE": 55, "NORMAL": 35, "HALT": 85},
        "HALT":    {}  # Manual restart only
    }

    # Hysteresis: require score to drop further before upgrading state
    # This prevents oscillation between states
    HYSTERESIS = 10

    def __init__(self):
        self.state = "NORMAL"
        self.state_history = []
        self.state_since = datetime.now()

    def evaluate(self, risk_score):
        new_state = self.state
        thresholds = self.TRANSITIONS[self.state]

        for target_state, threshold in sorted(thresholds.items(),
                                                key=lambda x: x[1], reverse=True):
            # Worsening: cross threshold
            if risk_score >= threshold and self.is_worse(target_state):
                new_state = target_state
                break
            # Improving: cross threshold - hysteresis
            elif risk_score < threshold - self.HYSTERESIS and self.is_better(target_state):
                new_state = target_state
                break

        if new_state != self.state:
            self.transition(new_state, risk_score)

    def is_worse(self, target):
        order = ["NORMAL", "REDUCE", "FLATTEN", "HALT"]
        return order.index(target) > order.index(self.state)

    def is_better(self, target):
        order = ["NORMAL", "REDUCE", "FLATTEN", "HALT"]
        return order.index(target) < order.index(self.state)
```

---

## 8. Feedback Loop & Self-Tuning

### 8.1 Measuring if RiskGate is Too Conservative or Too Loose

**Key Metrics**:
```python
class RiskGatePerformanceTracker:
    def __init__(self):
        self.veto_log = []  # All vetoed trades
        self.passed_log = []  # All passed trades

    def log_veto(self, trade, veto_reason, outcome=None):
        self.veto_log.append({
            "timestamp": datetime.now(),
            "trade": trade,
            "reason": veto_reason,
            "hypothetical_outcome": outcome  # Track what WOULD have happened
        })

    def log_pass(self, trade, outcome):
        self.passed_log.append({
            "timestamp": datetime.now(),
            "trade": trade,
            "actual_outcome": outcome
        })

    def compute_metrics(self):
        """
        Core feedback metrics
        """
        # 1. FALSE POSITIVE RATE (Type I Error)
        # Trades vetoed that WOULD have been profitable
        vetoed_profitable = sum(
            1 for v in self.veto_log
            if v["hypothetical_outcome"] and v["hypothetical_outcome"].pnl > 0
        )
        total_vetoed = len(self.veto_log)
        false_positive_rate = vetoed_profitable / total_vetoed if total_vetoed > 0 else 0

        # 2. FALSE NEGATIVE RATE (Type II Error)
        # Trades passed that WERE unprofitable
        passed_unprofitable = sum(
            1 for p in self.passed_log
            if p["actual_outcome"].pnl < 0
        )
        total_passed = len(self.passed_log)
        false_negative_rate = passed_unprofitable / total_passed if total_passed > 0 else 0

        # 3. OPPORTUNITY COST
        # PnL lost by vetoing trades that would have been profitable
        opportunity_cost = sum(
            v["hypothetical_outcome"].pnl
            for v in self.veto_log
            if v["hypothetical_outcome"] and v["hypothetical_outcome"].pnl > 0
        )

        # 4. PROTECTION VALUE
        # PnL saved by vetoing trades that would have been unprofitable
        protection_value = abs(sum(
            v["hypothetical_outcome"].pnl
            for v in self.veto_log
            if v["hypothetical_outcome"] and v["hypothetical_outcome"].pnl < 0
        ))

        # 5. NET VALUE OF RISK GATE
        net_value = protection_value - opportunity_cost

        # 6. VETO RATIO
        veto_ratio = total_vetoed / (total_vetoed + total_passed)
        # Healthy range: 15-40%. <10% = too loose, >50% = too tight

        return {
            "false_positive_rate": false_positive_rate,  # Target: <30%
            "false_negative_rate": false_negative_rate,  # Target: <50%
            "opportunity_cost": opportunity_cost,
            "protection_value": protection_value,
            "net_value": net_value,  # Should be positive
            "veto_ratio": veto_ratio,  # Target: 15-40%
        }
```

### 8.2 Tuning Veto Thresholds Based on Historical Trade Outcomes

**Bayesian Threshold Optimization**:
```python
class AdaptiveThresholdTuner:
    """
    For each veto check, maintains a threshold and adapts it
    based on realized outcomes.
    """
    def __init__(self, veto_name, initial_threshold, learning_rate=0.01):
        self.veto_name = veto_name
        self.threshold = initial_threshold
        self.lr = learning_rate
        self.history = []

    def update(self, feature_value, was_vetoed, hypothetical_pnl):
        """
        After each trade decision, update the threshold

        feature_value: the value that was compared to threshold (e.g., spread)
        was_vetoed: True if trade was blocked
        hypothetical_pnl: what the trade earned (or would have earned if vetoed)
        """
        self.history.append({
            "feature_value": feature_value,
            "vetoed": was_vetoed,
            "pnl": hypothetical_pnl
        })

        if len(self.history) < 50:
            return  # Not enough data yet

        # Optimal threshold = value that maximizes E[PnL]
        # by blocking trades with negative E[PnL] and passing positive E[PnL]

        # Grid search over threshold candidates
        values = [h["feature_value"] for h in self.history]
        candidates = np.percentile(values, np.arange(5, 95, 5))

        best_threshold = self.threshold
        best_expected_pnl = -np.inf

        for candidate in candidates:
            # Simulate: what if threshold had been this value?
            total_pnl = 0
            for h in self.history:
                would_veto = h["feature_value"] > candidate
                if not would_veto:  # Trade passes
                    total_pnl += h["pnl"]
                # If vetoed, PnL = 0 (no trade)

            if total_pnl > best_expected_pnl:
                best_expected_pnl = total_pnl
                best_threshold = candidate

        # Smooth update toward optimal
        self.threshold = (1 - self.lr) * self.threshold + self.lr * best_threshold

    def get_threshold(self):
        return self.threshold
```

### 8.3 Per-Veto Value Assessment

```python
class VetoValueAnalyzer:
    """
    Measures the value contributed by each individual veto check
    """
    def analyze_veto(self, veto_name, veto_log, passed_log):
        """
        For each veto type, compute:
        1. How often does it fire?
        2. When it fires, how often is it correct? (saves from loss)
        3. When it fires, how much does it save?
        4. What is the false positive cost?
        """
        vetoed_by_this = [v for v in veto_log if v["reason"].startswith(veto_name)]

        fires_count = len(vetoed_by_this)

        correct_vetoes = sum(
            1 for v in vetoed_by_this
            if v["hypothetical_outcome"] and v["hypothetical_outcome"].pnl < 0
        )
        incorrect_vetoes = sum(
            1 for v in vetoed_by_this
            if v["hypothetical_outcome"] and v["hypothetical_outcome"].pnl > 0
        )

        precision = correct_vetoes / fires_count if fires_count > 0 else 0

        # Value = losses prevented - profits missed
        losses_prevented = abs(sum(
            v["hypothetical_outcome"].pnl
            for v in vetoed_by_this
            if v["hypothetical_outcome"] and v["hypothetical_outcome"].pnl < 0
        ))
        profits_missed = sum(
            v["hypothetical_outcome"].pnl
            for v in vetoed_by_this
            if v["hypothetical_outcome"] and v["hypothetical_outcome"].pnl > 0
        )

        net_value = losses_prevented - profits_missed

        return VetoReport(
            name=veto_name,
            fires_per_100_trades=fires_count / len(veto_log + passed_log) * 100,
            precision=precision,         # Target: >60% (correct >60% of time)
            losses_prevented=losses_prevented,
            profits_missed=profits_missed,
            net_value=net_value,          # Must be positive
            recommendation=self.recommend(precision, net_value)
        )

    def recommend(self, precision, net_value):
        if net_value < 0 and precision < 0.50:
            return "CONSIDER_REMOVING — veto costs more than it saves"
        elif net_value < 0 and precision >= 0.50:
            return "LOOSEN_THRESHOLD — veto is directionally right but too aggressive"
        elif net_value > 0 and precision < 0.60:
            return "TIGHTEN_THRESHOLD — veto saves money but fires on too many good trades"
        else:
            return "KEEP — veto is working well"
```

### 8.4 Complete Veto Check List (25+ Checks)

```python
class RiskGate:
    """
    Complete list of all veto checks with adaptive thresholds.
    Each returns PASS or VETO with reason.
    """

    # === MARKET MICROSTRUCTURE ===
    # 1. Spread veto (adaptive per ticker/time/regime)
    # 2. Volume veto (adaptive per order size and ADV)
    # 3. Market impact veto (Almgren-Chriss estimate)
    # 4. Price staleness veto (reject if last trade >N seconds ago)
    # 5. Order book imbalance veto (reject if heavy selling pressure)

    # === POSITION-LEVEL ===
    # 6. Position concentration veto (adaptive per conviction/regime)
    # 7. Stop-loss distance veto (reject if ATR-based stop too wide)
    # 8. Expected profit veto (reject if expected PnL < min threshold)
    # 9. Risk-reward ratio veto (reject if R:R < 1.5:1)
    # 10. Conviction minimum veto (reject if meta-label prob < 0.55)

    # === PORTFOLIO-LEVEL ===
    # 11. Max positions veto (adaptive per regime/correlation)
    # 12. Daily drawdown veto (tiered: reduce/flatten/halt)
    # 13. Portfolio VaR veto (marginal VaR check)
    # 14. Portfolio CVaR veto (marginal ES check)
    # 15. Pairwise correlation veto (DCC-GARCH adaptive)
    # 16. Average portfolio correlation veto
    # 17. HHI concentration veto
    # 18. Sector concentration veto (adaptive + look-through)

    # === CROSS-EXPOSURE ===
    # 19. ETP underlying overlap veto (look-through)
    # 20. FX exposure veto
    # 21. PC1 dominance veto (correlation crisis)

    # === REGIME/MACRO ===
    # 22. Regime-based risk budget veto (deploy too much risk for regime)
    # 23. VIX circuit breaker (reject all new longs if VIX > threshold)
    # 24. Credit spread circuit breaker
    # 25. Market breadth circuit breaker

    # === ISA-SPECIFIC ===
    # 26. Contribution limit veto (GBP 20k annual)
    # 27. Fully funded veto (no margin)
    # 28. Short-selling veto (not allowed in ISA)

    # === META ===
    # 29. Cooldown veto (min time between trades in same ticker)
    # 30. Max daily trades veto (prevent overtrading)
    # 31. End-of-day veto (no new positions in last 15 min)

    def evaluate(self, proposed_trade, portfolio, market_data):
        """
        Run ALL veto checks. Return first VETO or PASS.
        Veto checks are ordered: cheapest/fastest first.
        """
        checks = [
            # Cheapest checks first (no computation needed)
            self.isa_contribution_veto,
            self.fully_funded_veto,
            self.short_selling_veto,
            self.end_of_day_veto,
            self.cooldown_veto,
            self.max_daily_trades_veto,

            # Regime/macro checks (use cached values)
            self.regime_risk_budget_veto,
            self.vix_circuit_breaker,
            self.credit_spread_circuit_breaker,
            self.market_breadth_circuit_breaker,

            # Market microstructure (need current market data)
            self.spread_veto,
            self.volume_veto,
            self.market_impact_veto,
            self.price_staleness_veto,
            self.order_book_imbalance_veto,

            # Position-level (need signal quality)
            self.conviction_minimum_veto,
            self.expected_profit_veto,
            self.risk_reward_veto,
            self.stop_loss_distance_veto,
            self.position_concentration_veto,

            # Portfolio-level (most expensive computations)
            self.max_positions_veto,
            self.daily_drawdown_veto,
            self.portfolio_var_veto,
            self.portfolio_cvar_veto,
            self.pairwise_correlation_veto,
            self.avg_correlation_veto,
            self.hhi_concentration_veto,
            self.sector_concentration_veto,

            # Cross-exposure (requires look-through)
            self.etp_underlying_overlap_veto,
            self.fx_exposure_veto,
            self.pc1_dominance_veto,
        ]

        for check in checks:
            result = check(proposed_trade, portfolio, market_data)
            if result.is_veto:
                self.feedback_tracker.log_veto(proposed_trade, result.reason)
                return result

        self.feedback_tracker.log_pass(proposed_trade)
        return PASS
```

---

## Key Academic References

| # | Citation | Relevance |
|---|----------|-----------|
| 1 | Engle, R. (2002). "Dynamic Conditional Correlation" J. Business & Economic Statistics | DCC-GARCH for adaptive correlation monitoring |
| 2 | Artzner, P. et al. (1999). "Coherent Measures of Risk" Mathematical Finance | CVaR > VaR (coherent risk measure theory) |
| 3 | Almgren, R. & Chriss, N. (2001). "Optimal Execution of Portfolio Transactions" J. Risk | Market impact modeling for volume veto |
| 4 | Avellaneda, M. & Stoikov, S. (2008). "High-Frequency Trading in a Limit Order Book" | Optimal spread modeling |
| 5 | De Prado, M. (2018). "Advances in Financial Machine Learning" Wiley | Meta-labeling, conviction sizing, triple barrier |
| 6 | Hamilton, J. (1989). "A New Approach to the Economic Analysis of Nonstationary Time Series" Econometrica | Markov regime-switching models |
| 7 | Thorp, E. (2006). "The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market" | Kelly for portfolio sizing |
| 8 | Kelly, J.L. (1956). "A New Interpretation of Information Rate" Bell System Technical J. | Original Kelly criterion |
| 9 | Wilder, J.W. (1978). "New Concepts in Technical Trading Systems" | ATR for volatility measurement |
| 10 | Van Tharp (2006). "Trade Your Way to Financial Freedom" | Percent volatility position sizing |
| 11 | Bruder, B. & Roncalli, T. (2012). "Managing Risk Exposures Using the Risk Budgeting Approach" | Equal risk contribution, risk budgeting |
| 12 | Longin, F. & Solnik, B. (2001). "Extreme Correlation of International Equity Markets" J. Finance | Correlations spike in bear markets |
| 13 | Forbes, K. & Rigobon, R. (2002). "No Contagion, Only Interdependence" J. Finance | Testing for correlation breakdowns |
| 14 | Gilchrist, S. & Zakrajsek, E. (2012). "Credit Spreads and Business Cycle Fluctuations" AER | Credit spreads predict recessions |
| 15 | Whaley, R. (2000, 2009). "The Investor Fear Gauge" J. Portfolio Management | VIX as regime indicator |
| 16 | McInish, T. & Wood, R. (1992). "An Analysis of Intraday Patterns in Bid/Ask Spreads" J. Finance | Intraday spread U-shape |
| 17 | Kyle, A. (1985). "Continuous Auctions and Insider Trading" Econometrica | Price impact lambda |
| 18 | Corwin, S. & Schultz, P. (2012). "A Simple Way to Estimate Bid-Ask Spreads" J. Finance | Spread estimation from OHLC |
| 19 | Guermat, C. & Harris, R. (2001). "Robust Conditional Variance Estimation and VaR" J. Risk | Optimal EWMA lambda |
| 20 | Vardharaj, R. & Fabozzi, F. (2007). "Sector, Style, Region: Explaining Stock Allocation Performance" FAJ | Sector allocation drives returns |

---

## Implementation Priority for NZT-48 / AEGIS

### Phase 1 (Must-Have for MVP)
1. Spread veto (adaptive per ticker + time-of-day)
2. Volume veto (participation rate cap)
3. Position concentration veto (regime-scaled)
4. Daily drawdown circuit breaker (4-tier)
5. ISA contribution limit tracker
6. Fully funded / no-short vetoes
7. Regime detection (VIX + breadth composite)
8. Max simultaneous positions (regime-adaptive)

### Phase 2 (Before Live Trading)
9. Portfolio VaR (EWMA parametric)
10. Portfolio CVaR
11. DCC correlation monitoring
12. Sector concentration (look-through)
13. ETP underlying overlap detection
14. ATR-based position sizing
15. Fractional Kelly (adaptive)
16. Feedback loop (track vetoed trade outcomes)

### Phase 3 (Optimization)
17. Meta-labeling conviction sizing
18. Market impact estimation (Almgren-Chriss)
19. HMM regime detection
20. PC1 dominance monitoring
21. Per-veto value analysis
22. Adaptive threshold tuning
23. Bayesian parameter estimation for Kelly
24. Copula-based tail dependence monitoring
25. FX exposure tracking
