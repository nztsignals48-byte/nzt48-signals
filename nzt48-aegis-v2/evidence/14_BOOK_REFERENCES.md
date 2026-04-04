# AEGIS V2 — Academic & Book References

**Audit Date:** 2026-04-04

This document maps every strategy, model, and technique in AEGIS V2 to its source material.

---

## Entry Type References

| Entry Type | Source | Key Concept |
|------------|--------|-------------|
| TypeA (DipRecovery) | Standard RSI oversold + volume spike pattern | Buy oversold with confirmation |
| TypeB (EarlyRunner) | Momentum factor literature | Rising relative volume = early institutional accumulation |
| TypeE (IBSMeanReversion) | Connors & Alvarez, "Short Term Trading Strategies That Work" (2008) | Internal Bar Strength mean reversion |
| TypeF (OBVDivergence) | Granville, "New Key to Stock Market Profits" (1963) | On-Balance Volume divergence signals accumulation |

## Strategy Module References

| Strategy | Book # | Source | Implementation |
|----------|--------|--------|----------------|
| Vol Compression | 22 | Bollinger, "Bollinger on Bollinger Bands" | Keltner squeeze breakout detection |
| FOMC Drift | 24 | Lucca & Moench, "The Pre-FOMC Announcement Drift" (2015) | Pre/post-FOMC event drift capture |
| Overnight Carry | 40 | Cliff, Cooper & Gulen (2008) | Overnight gap reversal pattern |
| Calendar Anomalies | 171 | Lakonishok & Smidt (1988), various | DOW, TOM, pre-holiday, options expiry |
| NAV Arbitrage | 132 | Petajisto, "Inefficiencies in the Pricing of ETFs" (2017) | ETP premium/discount vs NAV |
| Rebalancing Flow | 36 | Khandani & Lo, "What Happened to the Quants?" (2007) | ETP provider daily rebalancing prediction |
| FOMC Pre-Drift | 5 | Lucca & Moench (2015) | T-1 to T+5 FOMC positioning |

## Sizing References

| Module | Book # | Source | Implementation |
|--------|--------|--------|----------------|
| Kelly Criterion | 10 | Kelly, "A New Interpretation of Information Rate" (1956) | Fractional Kelly (0.25x) with clamps |
| Rolling Kelly | 10 | Kelly (1956) + Bayesian updating | 60/120/250-day rolling windows |
| Vol Targeting | 80, 118 | Moreira & Muir, "Volatility-Managed Portfolios" (2017) | Target vol / realized vol scaling |
| Student-t Kelly | 118 | Fat-tailed return distributions | Heavy-tail correction for 3x ETPs |
| Meta Allocator | 131 | Darwinian capital allocation literature | Flow capital from losers to winners |
| Capital Phasing | 179 | Graduated deployment | New strategy ramp-up schedule |

## Risk References

| Module | Book # | Source | Implementation |
|--------|--------|--------|----------------|
| Conditional Hedging | 42 | Bayesian posterior P(drawdown) | Graduated hedge response curve |
| Correlation Tracking | 41 | RiskMetrics (1996) | EWMA correlation matrix, lambda=0.94 |
| Drawdown Recovery | 42 | Drawdown-based position sizing | 5-phase: NORMAL/MONITORING/RECOVERY/CRITICAL/HALTED |
| SPRT Quarantine | 47 | Wald, "Sequential Analysis" (1947) | Edge-death detection via SPRT |
| Regime Risk Limits | 85 | Regime-dependent constraints | Per-regime position limits and Kelly caps |
| Adversarial Detection | 103 | Market manipulation literature | Spoofing/wash trading pattern detection |
| Safety Boundaries | 190 | Hard safety limits | Inviolable constraints |

## ML References

| Module | Book # | Source | Implementation |
|--------|--------|--------|----------------|
| EMAT Attention | 102 | Exponential Moving Average Transform | Multi-aspect temporal attention (numpy) |
| Conformal Signals | 144 | Vovk et al., "Algorithmic Learning in a Random World" (2005) | Distribution-free prediction intervals |
| Swarm Predictor | 151 | Multi-agent simulation literature | Wealth-weighted agent consensus |
| Temporal Attention | 157 | Attention mechanism literature | Temporal attention for time-series |
| Mamba/S4 | 161 | Gu et al., "Efficiently Modeling Long Sequences with S4" (2022) | Structured State Space (numpy) |
| HighFlyer | 166 | Retail flow + multi-factor | Combined signal generator |
| TDA Crash Detector | 127 | Topological Data Analysis literature | Persistent homology for crash detection |
| Path Signatures | 128 | Lyons et al., rough path theory | Path signature features |
| Reservoir Computing | 129 | Jaeger, "The Echo State Approach" (2001) | Echo State Networks for regime detection |
| GNN Market Structure | 96 | Kipf & Welling, "Semi-Supervised Classification with GCN" (2017) | Graph convolution + attention (numpy) |
| Gaussian Process | 114 | Rasmussen & Williams, "GP for ML" (2006) | RBF/Matern kernel uncertainty |
| Constrained PPO | 213 | Schulman et al., "PPO" (2017) | RL timing agent (stdlib + numpy) |
| LightGBM Classifier | 23 | Ke et al., "LightGBM" (2017) | 48-feature ONNX entry filter |

## Indicator References

| Indicator | Source | Implementation |
|-----------|--------|----------------|
| Hurst Exponent | Hurst, "Long Term Storage Capacity of Reservoirs" (1951) | Rescaled Range (R/S) analysis |
| VPIN | Easley, Lopez de Prado & O'Hara, "VPIN" (2012) | Volume-synchronized probability of informed trading |
| ADX | Wilder, "New Concepts in Technical Trading Systems" (1978) | Average Directional Index |
| VWAP | Standard institutional benchmark | Volume-Weighted Average Price with bands |
| GARCH(1,1) | Bollerslev, "Generalized ARCH" (1986) | Conditional variance, O(1) inference |
| EVT/CVaR | Extreme Value Theory (McNeil & Frey, 2000) | Tail risk via GARCH residuals |
| Hayashi-Yoshida | Hayashi & Yoshida (2005) | Asynchronous tick covariance |
| Amihud Illiquidity | Amihud, "Illiquidity and Stock Returns" (2002) | Price impact ratio |

## Validation References

| Method | Source | Implementation |
|--------|--------|----------------|
| Deflated Sharpe Ratio | Bailey & Lopez de Prado (2014) | Multiple testing correction |
| PBO (CSCV) | Bailey et al., "Probability of Backtest Overfitting" (2017) | Combinatorial cross-validation |
| MinBTL | Minimum Backtest Length formula | Sufficient data check |
