# AEGIS V2 — Position Sizing Methodology

**Audit Date:** 2026-04-04

---

## 12-Factor Kelly Sizing (Live System)

**File:** `python_brain/brain/sizing/kelly_12factor.py` (192 lines)

The live system uses a multiplicative 12-factor Kelly framework. Each factor scales the base Kelly fraction:

| Factor | Input | Effect | Source |
|--------|-------|--------|--------|
| 1. Base Kelly | Bayesian win_rate | f* = (p*b - q) / b | dynamic_weights.toml |
| 2. Volatility Decay | Leverage (3x=9x vol, 5x=25x vol) | Divides by leverage^2 | contracts.toml |
| 3. Moreira-Muir Vol Scaling | Realized vol vs target vol | Scale = target / realized | Book 10, 80 |
| 4. Correlation Penalty | EWMA correlation matrix | Reduce if corr > 0.6 | Book 41 |
| 5. Drawdown Scaling | Current drawdown % | Quadratic reduction | Book 42 |
| 6. Amihud Liquidity | Amihud illiquidity ratio | Reduce for illiquid | Book 49 |
| 7. Regime Scaling | Regime from dynamic_weights | bear_volatile = 0.50x | dynamic_weights.toml |
| 8. Spread Cost | Actual spread vs edge | Reduce if spread eats edge | CHECK 29 |
| 9. Time-of-Day | Hour weight from backtest | Multiply by hour_weight | config.toml |
| 10. Confidence | Signal confidence score | Linear 0-1 scaling | bridge.py |
| 11. Half-Kelly Cap | Cap at 0.50 of full Kelly | Max 50% Kelly | Standard |
| 12. Portfolio Heat | Current heat % | Hard cap at 6% heat | config.toml |

**Final clamp:** max 0.20 (20% Kelly). Shares = `math.floor()` only.

## Rolling Kelly Estimator (Bayesian)

**File:** `python_brain/sizing/rolling_kelly.py` (327 lines)

Three rolling windows with Bayesian prior:

| Window | Period | Weight | Purpose |
|--------|--------|--------|---------|
| Short | 60 days | 0.4 | Recent performance |
| Medium | 120 days | 0.35 | Seasonal patterns |
| Long | 250 days | 0.25 | Full-cycle |

**Bayesian prior:** Returns 1.0 for <10 observations to avoid penalizing fresh sources.

## 4-Stage Drawdown Staging

**File:** `python_brain/sizing/rolling_kelly.py`, class `DrawdownStager`

| Stage | Drawdown Threshold | Kelly Multiplier | Max Heat |
|-------|-------------------|-----------------|----------|
| STEADY | 0 - 5% | 1.00x | 10% |
| CAUTION | 5 - 10% | 0.75x | 7.5% |
| REDUCE | 10 - 15% | 0.50x | 5% |
| FLATTEN | > 15% | 0.00x (halt) | 0% |

## Backtest Sizing

The backtest uses simplified sizing:
- **Confidence-based:** Each entry type has a fixed base confidence
- **Equity curve:** 10% Kelly fraction for equity compounding
- **No 12-factor adjustment** (no real-time vol, correlation, liquidity data)

This is a **known limitation** — the backtest P&L curve does not reflect the sizing adjustments that would occur in live trading.

## Additional Sizing Modules

| Module | File | Book | Purpose |
|--------|------|------|---------|
| Vol Targeting | sizing/vol_targeting.py | 10, 80, 118 | Target vol / realized vol |
| Meta Allocator | sizing/meta_allocator.py | 131 | Darwinian capital reallocation across strategies |
| Capital Phasing | sizing/capital_phasing.py | 179 | Graduated deployment for new strategies |
| Leverage Selector | sizing/leverage_selector.py | 27 | Optimal leverage (1x/3x/5x) per regime |
| Compounding Velocity | sizing/compounding_velocity.py | 26 | Tracks and adjusts compounding rate |

## ISA Constraints on Sizing

| Constraint | Value | Enforcement |
|------------|-------|-------------|
| Annual deposit limit | £20,000 | CHECK 17 (ISA Annual Limit) |
| Max positions | 3 | CHECK 6 (live config) |
| Cash buffer | 25% | CHECK 14 |
| Max risk per trade | 0.50% | config.live.toml |
| Min trade size (live) | £1,500 | SC-05 |
| Long-only | Enforced | CHECK 1 (ISA Short-Sell Block) |
