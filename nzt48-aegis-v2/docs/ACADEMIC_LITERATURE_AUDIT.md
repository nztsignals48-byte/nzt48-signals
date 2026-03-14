# AEGIS V2 Academic Literature Audit
## Principal Quantitative Research Report

**Date**: 2026-03-11
**Scope**: 10 foundational topics underpinning the AEGIS V2 intraday momentum/volatility trading system for LSE leveraged ETPs (3x and 5x) in a UK ISA
**Standard**: Institutional-grade, with exact citations and design implications

---

## 1. Intraday Momentum Persistence

### Key Finding
Intraday momentum is real, statistically significant, and economically exploitable. The first half-hour return (measured from previous close) predicts the last half-hour return with a predictive R-squared of ~1.6% -- matching or exceeding typical monthly-frequency predictability. The effect is stronger on high-volatility days, high-volume days, recession periods, and around macroeconomic announcements.

### Sources

**Gao, L., Han, Y., Li, S.Z., & Zhou, G. (2018).** "Market Intraday Momentum." *Journal of Financial Economics*, 129(2), 394-414.
- Uses S&P 500 ETF data 1993-2013. First half-hour return predicts last half-hour return.
- Effect holds across 10 other actively traded domestic and international ETFs.
- Consistent with Bogousslavsky (2016) infrequent portfolio rebalancing model and late-informed trading near close.

**Elaut, G., Frommel, M., & Lampaert, K. (2018).** "Intraday Momentum in FX Markets: Disentangling Informed Trading from Liquidity Provision." *Journal of Financial Markets*, 37(C), 35-51.
- Confirms intraday momentum in RUB-USD FX market, extending evidence beyond equities.

**Zhang, Y., Ma, F., & Liao, Y. (2020).** "Intraday Time Series Momentum: Global Evidence and Links to Market Characteristics." *Journal of Financial Markets*, 57.
- Documents intraday momentum globally; cross-section persistence remains statistically significant for up to 520 half-hour lags (~40 trading days).

### AEGIS V2 Design Implications
- **VALIDATES** the core premise of intraday momentum signals on leveraged ETPs.
- The first-30-minute and last-30-minute windows are the highest-signal periods. AEGIS V2 should concentrate signal generation and execution around these windows.
- Effect amplification on high-volatility days is directly relevant since AEGIS trades 3x/5x leveraged products which inherently exhibit amplified volatility.
- The 1.6% R-squared may seem small but is enormous by financial forecasting standards. Do not expect higher signal strength -- design the system to profit from weak but persistent edges via repeated application and position sizing.
- Intraday cross-section persistence decaying over ~40 days suggests signal recycling windows of approximately 2 months before regime reassessment.

---

## 2. Leveraged ETF/ETP Volatility Drag

### Key Finding
Leveraged ETFs suffer from variance drain (volatility drag) that compounds daily and is mathematically inevitable. The approximate daily drag formula is: **Drag = 0.5 * L^2 * sigma^2**, where L is leverage and sigma^2 is variance. For a 3x product, drag = 4.5 * sigma^2. For a 5x product, drag = 12.5 * sigma^2. This means 5x products lose nearly 3x MORE to volatility drag than 3x products, making holding periods critically important.

### Sources

**Avellaneda, M. & Zhang, S. (2010).** "Path-Dependence of Leveraged ETF Returns." *SIAM Journal on Financial Mathematics*, 1(1), 586-603.
- Derives exact Ito-formula-based relationship linking leveraged fund return to leverage multiple, index return, and realized variance.
- Tested empirically on 56 leveraged funds (44 double, 12 triple) with excellent agreement.
- Concludes leveraged ETFs may be unsuitable for buy-and-hold but can be used with dynamic rebalancing on ~weekly frequency.

**Cheng, M. & Madhavan, A. (2009).** "The Dynamics of Leveraged and Inverse Exchange-Traded Funds." *Journal of Investment Management*, 7(4), 43-62.
- Shows returns are path-dependent; daily re-leveraging exacerbates end-of-day volatility.
- Derives the equation now used in ProShares, Direxion, and GraniteShares prospectuses: realized leverage is a function of leverage, volatility, and underlying trend.

**Approximation Formula (continuous-time)**:
- 3x product: Leveraged Return approx = 3R - 4.5 * sigma^2
- 5x product: Leveraged Return approx = 5R - 12.5 * sigma^2
- At 20% annualized vol (sigma = 0.20): 3x daily drag approx = 4.5 * (0.20/sqrt(252))^2 = ~7.1 bps/day; 5x daily drag approx = 12.5 * same = ~19.8 bps/day

### AEGIS V2 Design Implications
- **CRITICAL**: The system MUST be intraday or very short-term. Holding 5x products overnight accumulates ~20 bps/day of drag in typical volatility, which devours any edge below ~0.3% daily.
- The 5x products (QQQ5.L, SP5L.L) should only be used when daily expected momentum exceeds the 12.5 * sigma^2 drag threshold. Otherwise, prefer 3x products.
- Implement a real-time "drag budget" calculator that computes expected drag for current realized volatility and aborts trades where expected drag exceeds expected alpha.
- Weekly rebalancing frequency (per Avellaneda) is too slow for AEGIS -- intraday entry/exit eliminates most variance drain but requires precise timing.

---

## 3. Kelly Criterion Under Estimation Error

### Key Finding
Full Kelly is extremely sensitive to parameter estimation errors. A 10% error in estimated edge can cause 50%+ overbetting. Half-Kelly provides approximately 75% of Full Kelly's growth rate with only 50% of the volatility (equivalently, 25% of the variance). This makes Half-Kelly the practical standard for any system where win rate, payoff ratio, or edge cannot be estimated with high precision.

### Sources

**Kelly, J.L. Jr. (1956).** "A New Interpretation of Information Rate." *Bell System Technical Journal*, 35(4), 917-926.
- Original derivation: maximum exponential growth rate of capital equals the rate of transmission of information over the channel.
- Optimal fraction f* = (bp - q) / b where b = odds, p = win probability, q = 1-p.

**MacLean, L.C., Thorp, E.O., & Ziemba, W.T. (2011).** "Good and Bad Properties of the Kelly Criterion." In: *The Kelly Capital Growth Investment Criterion: Theory and Practice*. World Scientific.
- Demonstrates Full Kelly's two fatal flaws: (1) large short-term drawdown risk, (2) extreme sensitivity to estimation errors.
- A 10% estimation error in expected return can cause 50%+ overbetting.
- Fractional Kelly (especially Half-Kelly) dramatically reduces both problems.

**MacLean, L.C., Ziemba, W.T., & Blazenko, G. (1992).** "Growth versus Security in Dynamic Investment Analysis." *Management Science*, 38(11), 1562-1585.
- Proves Half-Kelly yields ~75% of Full Kelly growth rate with ~50% of the volatility.
- The growth-variance trade-off is asymmetric: you give up only 25% growth but halve the risk.

### AEGIS V2 Design Implications
- **MANDATORY**: Use Half-Kelly (or less) for all position sizing. Full Kelly is mathematically optimal only with perfect parameter estimates, which AEGIS cannot have.
- With 52 paper trades showing 0% win rate on S15, the current edge estimate has massive estimation error. Until the 100-trade validation gate produces WR >= 40%, even Half-Kelly may be too aggressive.
- During the validation phase, use Quarter-Kelly (f*/4) to survive estimation error while gathering data to refine edge estimates.
- Implement a rolling Bayesian edge estimator that updates the Kelly fraction as trade history accumulates, converging toward Half-Kelly only after statistical significance is established.

---

## 4. GARCH-Family Forecasting: Intraday vs Daily

### Key Finding
GARCH(1,1) is remarkably hard to beat for daily volatility forecasting -- but this is a statement about daily horizons. For intraday forecasting at 5-second frequency, GARCH is the wrong tool. The appropriate approach is realized volatility estimation using intraday returns, which GARCH was originally compared against (not designed to replace). More sophisticated GARCH models only outperform GARCH(1,1) when the data exhibits leverage effects (equities) rather than symmetric volatility (FX).

### Sources

**Andersen, T.G. & Bollerslev, T. (1998).** "Answering the Skeptics: Yes, Standard Volatility Models Do Provide Accurate Forecasts." *International Economic Review*, 39(4), 885-905.
- GARCH(1,1) forecasts achieve R-squared of ~50% when benchmarked against realized volatility (sum of squared intraday returns) rather than daily squared returns.
- The apparent failure of GARCH was a measurement problem, not a model problem. Daily squared returns are a noisy proxy for true conditional variance.

**Hansen, P.R. & Lunde, A. (2005).** "A Forecast Comparison of Volatility Models: Does Anything Beat a GARCH(1,1)?" *Journal of Applied Econometrics*, 20(7), 873-889.
- Compares 330 GARCH-type models. For exchange rate data: nothing beats GARCH(1,1).
- For equity data (IBM): models with leverage effects do outperform GARCH(1,1).
- Key insight: model complexity only helps when the data has asymmetric volatility dynamics.

**Andersen, T.G., Bollerslev, T., Diebold, F.X., & Labys, P. (2003).** "Modeling and Forecasting Realized Volatility." *Econometrica*, 71(2), 579-625.
- Realized volatility from high-frequency returns provides far superior volatility estimates.
- The HAR (Heterogeneous Autoregressive) model of realized volatility is simpler and more effective than GARCH for intraday applications.

### AEGIS V2 Design Implications
- **DO NOT** fit GARCH to 5-second bars. GARCH is designed for daily frequency and assumes specific return distribution properties that break down at ultra-high frequency.
- Instead, compute realized volatility from 5-second returns using noise-robust estimators (see Topic 6).
- Use a HAR-RV (Heterogeneous Autoregressive Realized Volatility) model for volatility forecasting: RV_t = c + beta_d * RV_{t-1} + beta_w * RV_{t-5:t-1} + beta_m * RV_{t-22:t-1}. This captures daily, weekly, and monthly volatility components without GARCH's distributional assumptions.
- For regime classification, realized volatility thresholds are more appropriate than GARCH-estimated conditional variance.

---

## 5. CUSUM / Change Detection for Regime Switching

### Key Finding
CUSUM and HMM address different problems. CUSUM (Page, 1954) is a sequential, non-parametric change-point detector optimized for speed of detection with controlled false alarm rate. HMM (Hamilton, 1989) is a parametric regime-switching model that infers unobserved states from observed data. For real-time regime detection in AEGIS, CUSUM is the faster, lighter-weight choice for abrupt changes; HMM is better for persistent regime identification but introduces latency.

### Sources

**Page, E.S. (1954).** "Continuous Inspection Schemes." *Biometrika*, 41(1/2), 100-115.
- Introduces the CUSUM (Cumulative Sum) control chart for sequential change detection.
- Designed for industrial quality control but directly applicable to financial time series.
- Detects shifts in mean/variance with provably optimal detection delay for a given false alarm rate.

**Hamilton, J.D. (1989).** "A New Approach to the Economic Analysis of Nonstationary Time Series and the Business Cycle." *Econometrica*, 57(2), 357-384.
- Introduces the Markov-switching model where an unobserved state variable follows a Markov chain.
- Each regime has distinct parameters (mean, variance); transitions are probabilistic.
- Foundational for identifying bull/bear markets, volatility regimes, etc.

**Comparison Characteristics**:
- CUSUM: O(1) per observation, no distributional assumptions, immediate detection, but no forward-looking regime probability.
- HMM: O(N^2) per observation (N = number of states), provides regime probabilities, but suffers from look-ahead bias in offline estimation and detection lag in online (filtered) mode.
- HMM lag: research shows HMMs have difficulty with rapid identification of regime shifts after significant market events (lagged detection).

### AEGIS V2 Design Implications
- **USE BOTH**: Deploy CUSUM as the fast "trip wire" for abrupt changes (e.g., flash crash, sudden vol spike) with immediate position reduction. Deploy HMM as the slower "strategic" regime classifier for the current session (trending vs mean-reverting vs crisis).
- CUSUM threshold calibration is critical: too sensitive = frequent false alarms causing unnecessary trade exits; too insensitive = late detection of regime breaks.
- For AEGIS's 5-second bar frequency, CUSUM on rolling realized volatility is computationally trivial and should be the primary guard rail.
- HMM should operate on 5-minute aggregated data (not 5-second) to avoid fitting to microstructure noise.
- Consider a 3-state HMM: Low-Vol Trending, High-Vol Trending, and Crisis/Whipsaw. Only trade in the first two states.

---

## 6. Bid-Ask Bounce and Microstructure Noise at 5-Second Frequency

### Key Finding
At 5-second sampling frequency, microstructure noise dominates true price variation. The bid-ask bounce (Roll, 1984) induces negative first-order autocorrelation in returns, and the noise variance does NOT shrink with the sampling interval while true volatility does. This means naive realized volatility from 5-second returns will be heavily biased upward, and momentum signals computed from raw 5-second prices will contain substantial noise.

### Sources

**Roll, R. (1984).** "A Simple Implicit Measure of the Effective Bid-Ask Spread in an Efficient Market." *Journal of Finance*, 39(4), 1127-1139.
- The bid-ask spread induces negative serial covariance in returns: Spread = sqrt(2 * |cov|), where cov is the first-order serial covariance of price changes.
- In the Roll model, the noise term epsilon_t = (s/2) * Q_t where s is the spread and Q_t is a binomial order-flow indicator (+1 or -1 with equal probability).

**Harris, L. (2003).** *Trading and Exchanges: Market Microstructure for Practitioners*. Oxford University Press.
- Comprehensive practitioner reference: bid-ask spreads reflect trading costs; market microstructure distinguishes informed traders from noise/liquidity traders.
- The spread is the baseline cost of any round-trip trade -- critical for intraday systems.

**Ait-Sahalia, Y., Mykland, P.A., & Zhang, L. (2005).** "How Often to Sample a Continuous-Time Process in the Presence of Market Microstructure Noise." *Review of Financial Studies*, 18(2), 351-416.
- Derives optimal sampling frequency as a bias-variance trade-off: sampling too fast accumulates noise bias, sampling too slow loses information.
- Proposes Two-Scales Realized Volatility (TSRV) estimator that is consistent and asymptotically unbiased despite noise.

**Bandi, F.M. & Russell, J.R. (2008).** "Microstructure Noise, Realized Variance, and Optimal Sampling." *Review of Economic Studies*, 75(2), 339-369.
- Derives MSE-optimal sampling frequency balancing noise bias against estimation variance.
- The commonly cited "5-minute rule" (sampling at 5-minute intervals) is an empirical approximation of this optimal trade-off for liquid US equities.

### AEGIS V2 Design Implications
- **CRITICAL**: Do NOT compute momentum signals directly from raw 5-second mid-prices. The noise-to-signal ratio at this frequency is very high.
- Use 5-second bars for data ingestion but aggregate to 1-5 minute bars for signal computation. The academic consensus optimal sampling frequency for liquid assets is approximately 5 minutes; for less liquid LSE leveraged ETPs, it may be even longer (10-15 minutes).
- Implement a TSRV (Two-Scales Realized Volatility) estimator for volatility measurement: compute RV at both fast (5-second) and slow (5-minute) scales, then use the linear combination that cancels noise bias.
- The Roll estimator (Spread = sqrt(2|cov|)) should be computed in real-time as a transaction cost monitor. If the implied spread widens beyond a threshold, halt signal generation.
- For correlation calculations between instruments, the noise problem is even worse (see Topic 9 on Hayashi-Yoshida).

---

## 7. Execution Cost Realism for LSE Leveraged ETPs

### Key Finding
LSE leveraged ETPs are structurally less liquid than their underlying indices. Daily volumes of GBP 1-10M imply that even moderate orders (GBP 50K+) will experience meaningful market impact. Realistic all-in execution costs (spread + slippage + market impact) for these products are estimated at 10-30 basis points per side, depending on product, time of day, and market conditions. This is substantially higher than large-cap US equity execution costs (1-5 bps).

### Sources

**Almgren, R. & Chriss, N. (2001).** "Optimal Execution of Portfolio Transactions." *Journal of Risk*, 3(2), 5-40.
- Foundational model for optimal execution: total cost = permanent impact + temporary impact + volatility risk.
- Market impact follows a power law (often square-root) of trading rate: Impact approx = sigma * sqrt(V/ADV) where V = volume traded, ADV = average daily volume.
- For an order that is 1% of ADV in a product with 20% annualized vol, expected impact is approximately 20 bps.

**Leverage Shares / BNP Paribas Market Making**:
- BNP Paribas provides continuous bid/ask spreads on all Leverage Shares ETPs on LSE, ensuring minimum liquidity even in stressed conditions.
- However, the guaranteed spread is likely wider than organic order book depth for the most liquid products.

**Practical Estimation for AEGIS V2 Universe**:
- QQQ3.L (WisdomTree Nasdaq 3x): ~GBP 5-10M daily volume. Spread typically 10-20 bps. Market impact for GBP 10K order: negligible. For GBP 100K: ~5-10 bps additional.
- 5x products (QQQ5.L, SP5L.L): Lower volume (~GBP 1-5M daily). Spread typically 15-30 bps. Market impact for GBP 100K: ~10-20 bps additional.
- Each basis point of cost translates to L * 1 bps of PnL impact through leverage (3 bps for 3x, 5 bps for 5x products).

### AEGIS V2 Design Implications
- **BUDGET CONSERVATIVELY**: Assume 15 bps per side (30 bps round-trip) as the default execution cost for 3x products, and 25 bps per side (50 bps round-trip) for 5x products.
- With a GBP 10,000 ISA starting equity, order sizes will be small (GBP 1-5K) and market impact will be minimal. The dominant cost is the bid-ask spread.
- Implement a real-time spread monitor. If the observed spread exceeds 2x the trailing median, delay execution or reduce position size.
- Use limit orders, not market orders. The Almgren-Chriss framework justifies patient execution for non-urgent signals.
- At GBP 10K equity with 30 bps round-trip cost and daily turnover, execution costs alone consume ~0.3% per day if fully turning over the portfolio. This is the ENTIRE daily target (0.3-0.5%). Minimize turnover.
- Consider time-of-day effects: LSE leveraged ETPs are most liquid during the overlap with US pre-market and early US session (14:30-16:30 GMT).

---

## 8. Multiple Testing / DSR / Backtest Overfitting

### Key Finding
The Deflated Sharpe Ratio (DSR) framework proves that searching over N strategy variants inflates the maximum observed Sharpe ratio even when all variants are pure noise. For N = 100 independent trials, the expected maximum Sharpe ratio of noise is approximately 2.5 (annualized). For N = 1000, it exceeds 3.0. A newly proposed factor needs t-statistic > 3.0 (not the traditional 2.0) to clear the multiple testing hurdle.

### Sources

**Bailey, D.H. & Lopez de Prado, M. (2014).** "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality." *Journal of Portfolio Management*, 40(5), 94-107.
- DSR incorporates: number of independent experiments, variance of observed Sharpe ratios, sample length, skewness, and kurtosis of returns.
- Two sources of performance inflation: (1) non-normal returns (fat tails inflate apparent Sharpe), (2) selection bias under multiple testing.
- Without reporting the number of trials attempted, any backtest is statistically meaningless.

**Harvey, C.R., Liu, Y., & Zhu, H. (2016).** "...and the Cross-Section of Expected Returns." *Review of Financial Studies*, 29(1), 5-68.
- Documents that hundreds of factors have been "discovered" through data mining.
- A new factor needs t-statistic > 3.0 to be credible after adjusting for the multiple testing conducted across the entire literature.
- Most claimed research findings in financial economics are likely false.

**Lopez de Prado, M. (2018).** *Advances in Financial Machine Learning*. Wiley.
- Introduces Combinatorial Purged Cross-Validation (CPCV) as superior to walk-forward for controlling backtest overfitting.
- Probability of Backtest Overfitting (PBO) provides a direct estimate of how likely a strategy's IS performance is due to overfitting.
- Emphasizes that backtest overfitting is the single greatest threat to quantitative strategy development.

### AEGIS V2 Design Implications
- **EXISTENTIAL RISK**: AEGIS has gone through multiple plan versions (v15-v30+) with extensive parameter tuning. Every configuration variant tested counts toward the multiple testing penalty.
- Compute DSR for any strategy claiming significance. Record the TOTAL number of configurations tested (including discarded ones) and use this as input to DSR.
- The 100-Trade Validation Gate (WR >= 40%) is a good first filter, but it must be combined with DSR. A 40% win rate over 100 trades could easily be noise if hundreds of parameter combinations were tested to arrive at those parameters.
- Implement CPCV rather than simple walk-forward backtesting. Walk-forward testing has a known overfitting vulnerability that CPCV addresses.
- Consider the Harvey-Liu-Zhu threshold: if AEGIS's backtested Sharpe ratio is below 3.0 (annualized, after all costs), treat it with extreme skepticism.
- The 0% win rate over 52 paper trades is actually informative -- it strongly suggests the previous strategy configuration was worse than random. This is useful data for DSR calibration.

---

## 9. Asynchronous Covariance Estimation

### Key Finding
When two assets trade at different times (non-synchronous trading), the standard realized covariance estimator is biased toward zero (the "Epps effect"). The Hayashi-Yoshida estimator solves this by summing products of all time-overlapping returns between two assets, using all available data without requiring synchronization or interpolation.

### Sources

**Hayashi, T. & Yoshida, N. (2005).** "On Covariance Estimation of Non-Synchronously Observed Diffusion Processes." *Bernoulli*, 11(2), 359-379.
- Proposes an estimator free of any synchronization preprocessing: sums products of returns whose time intervals overlap.
- Converges in probability to the true quadratic covariation as observation frequency increases.
- Eliminates the downward bias of synchronized/interpolated estimators.

**Barndorff-Nielsen, O.E., Hansen, P.R., Lunde, A., & Shephard, N. (2011).** "Multivariate Realised Kernels: Consistent Positive Semi-Definite Estimators of the Covariation of Equity Prices in the Presence of Noise." *Journal of the Royal Statistical Society B*, 73(3), 373-411.
- Extends the problem to include microstructure noise: the Hayashi-Yoshida estimator is biased in the presence of noise.
- Proposes realized kernel estimators that are consistent, positive semi-definite, and robust to noise and non-synchronous trading simultaneously.

### AEGIS V2 Design Implications
- **NEEDED** for the correlation engine tracking 12 ISA instruments. These LSE leveraged ETPs trade asynchronously (different tick rates, stale quotes during low-volume periods).
- Implement Hayashi-Yoshida for pairwise covariance estimation between all 12 instruments. Standard synchronization (e.g., previous-tick interpolation) would bias correlations toward zero, causing the correlation engine to underestimate co-movement and mis-size hedges.
- For the sector rotation module: correlation breakdowns between instruments are a key signal. Using a biased estimator would delay detection of correlation regime changes.
- At 5-second sampling, the non-synchronicity problem is severe for less liquid ETPs (e.g., MU2.L, TSM3.L). Some instruments may not update for 30+ seconds during quiet periods.
- Consider the Barndorff-Nielsen et al. realized kernel approach if microstructure noise significantly affects covariance estimates.

---

## 10. Moreira-Muir Volatility Managed Portfolios

### Key Finding
Scaling portfolio exposure inversely with recent realized volatility (taking less risk when vol is high, more when vol is low) produces large, statistically significant alphas across the market, value, momentum, and other factors. For momentum specifically, volatility management virtually eliminates crash risk, nearly doubles the Sharpe ratio (from 0.53 to 0.97), and dramatically reduces tail risk (max drawdown from -96.7% to -45.2%).

### Sources

**Moreira, A. & Muir, T. (2017).** "Volatility-Managed Portfolios." *Journal of Finance*, 72(4), 1611-1644.
- Managed portfolios that scale inversely with volatility produce large alphas and increased Sharpe ratios across market, value, momentum, profitability, ROE, investment, and BAB factors, plus currency carry.
- Key insight: changes in volatility are NOT offset by proportional changes in expected returns. This violates a standard assumption and creates a free lunch from vol-timing.
- The strategy takes less risk in recessions, ruling out typical risk-based explanations.

**Barroso, P. & Santa-Clara, P. (2015).** "Momentum Has Its Moments." *Journal of Financial Economics*, 116(1), 111-120.
- Momentum risk is highly variable and predictable. Scaling by inverse of 6-month realized volatility:
  - Sharpe ratio: 0.53 --> 0.97 (nearly doubles)
  - Excess kurtosis: 18.24 --> 2.68
  - Skew: -2.47 --> -0.42
  - Min monthly return: -78.96% --> -28.40%
  - Max drawdown: -96.69% --> -45.20%
- The risk-managed momentum strategy has nearly symmetric return distribution -- the catastrophic left tail is virtually eliminated.

**Cederburg, S., O'Doherty, M., Wang, F., & Yan, X.S. (2020).** "On the Performance of Volatility-Managed Portfolios." *Journal of Financial Economics*, 138(1), 95-117.
- Challenges the universality of Moreira-Muir findings; shows benefits are concentrated in momentum and that statistical significance is sensitive to sample period and test methodology.

### AEGIS V2 Design Implications
- **STRONGEST SINGLE RECOMMENDATION**: Implement volatility scaling as a core position-sizing mechanism. This is the single highest-impact improvement available to AEGIS.
- Position size should be proportional to target_vol / realized_vol, where target_vol is a fixed parameter and realized_vol is estimated from recent intraday data.
- For leveraged ETPs, this is doubly important: the leverage itself amplifies volatility, and the drag formula (Topic 2) shows that high-vol periods have exponentially higher drag. Reducing position size when vol is high both reduces crash risk AND reduces volatility drag.
- Barroso & Santa-Clara's 6-month lookback is for monthly momentum. For intraday AEGIS, use a shorter lookback (5-20 days of realized vol).
- The Cederburg et al. caveat is important: vol-management works best for momentum (which is what AEGIS does). Do not assume it generalizes to other strategy types without testing.
- Combine vol-scaling with Half-Kelly (Topic 3): f_effective = f_halfkelly * (target_vol / realized_vol). This provides a doubly-robust position sizing framework.

---

## Summary: Priority Design Actions for AEGIS V2

| Priority | Action | Source | Impact |
|----------|--------|--------|--------|
| P0 | Implement volatility-scaled position sizing | Moreira & Muir 2017; Barroso & Santa-Clara 2015 | Doubles Sharpe, eliminates crash risk |
| P0 | Use Half-Kelly (or Quarter-Kelly during validation) | MacLean, Thorp & Ziemba 2011 | Prevents ruin under estimation error |
| P0 | Budget 15-25 bps/side execution costs | Almgren & Chriss 2001 | Prevents PnL erosion from unrealistic assumptions |
| P0 | Compute DSR; record ALL trials attempted | Bailey & Lopez de Prado 2014 | Prevents backtest overfitting self-deception |
| P1 | Aggregate 5-sec bars to 1-5 min for signals | Ait-Sahalia et al. 2005; Bandi & Russell 2008 | Eliminates microstructure noise contamination |
| P1 | Implement CUSUM fast trip-wire + HMM slow regime | Page 1954; Hamilton 1989 | Catches regime breaks at two timescales |
| P1 | Use HAR-RV instead of GARCH for vol forecasting | Andersen & Bollerslev 1998; Hansen & Lunde 2005 | Correct tool for intraday frequency |
| P1 | Implement drag budget (0.5 * L^2 * sigma^2) | Avellaneda & Zhang 2010 | Prevents trading when drag exceeds alpha |
| P2 | Implement Hayashi-Yoshida for cross-asset correlation | Hayashi & Yoshida 2005 | Unbiased correlation for non-synchronous LSE ETPs |
| P2 | Focus signals on first/last 30-min windows | Gao et al. 2018 | Concentrates on highest-signal periods |

---

## Complete Reference List

1. Ait-Sahalia, Y., Mykland, P.A., & Zhang, L. (2005). "How Often to Sample a Continuous-Time Process in the Presence of Market Microstructure Noise." *Review of Financial Studies*, 18(2), 351-416.
2. Almgren, R. & Chriss, N. (2001). "Optimal Execution of Portfolio Transactions." *Journal of Risk*, 3(2), 5-40.
3. Andersen, T.G. & Bollerslev, T. (1998). "Answering the Skeptics: Yes, Standard Volatility Models Do Provide Accurate Forecasts." *International Economic Review*, 39(4), 885-905.
4. Andersen, T.G., Bollerslev, T., Diebold, F.X., & Labys, P. (2003). "Modeling and Forecasting Realized Volatility." *Econometrica*, 71(2), 579-625.
5. Avellaneda, M. & Zhang, S. (2010). "Path-Dependence of Leveraged ETF Returns." *SIAM Journal on Financial Mathematics*, 1(1), 586-603.
6. Bailey, D.H. & Lopez de Prado, M. (2014). "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality." *Journal of Portfolio Management*, 40(5), 94-107.
7. Bandi, F.M. & Russell, J.R. (2008). "Microstructure Noise, Realized Variance, and Optimal Sampling." *Review of Economic Studies*, 75(2), 339-369.
8. Barndorff-Nielsen, O.E., Hansen, P.R., Lunde, A., & Shephard, N. (2011). "Multivariate Realised Kernels." *Journal of the Royal Statistical Society B*, 73(3), 373-411.
9. Barroso, P. & Santa-Clara, P. (2015). "Momentum Has Its Moments." *Journal of Financial Economics*, 116(1), 111-120.
10. Cederburg, S., O'Doherty, M., Wang, F., & Yan, X.S. (2020). "On the Performance of Volatility-Managed Portfolios." *Journal of Financial Economics*, 138(1), 95-117.
11. Cheng, M. & Madhavan, A. (2009). "The Dynamics of Leveraged and Inverse Exchange-Traded Funds." *Journal of Investment Management*, 7(4), 43-62.
12. Elaut, G., Frommel, M., & Lampaert, K. (2018). "Intraday Momentum in FX Markets." *Journal of Financial Markets*, 37(C), 35-51.
13. Gao, L., Han, Y., Li, S.Z., & Zhou, G. (2018). "Market Intraday Momentum." *Journal of Financial Economics*, 129(2), 394-414.
14. Hamilton, J.D. (1989). "A New Approach to the Economic Analysis of Nonstationary Time Series and the Business Cycle." *Econometrica*, 57(2), 357-384.
15. Hansen, P.R. & Lunde, A. (2005). "A Forecast Comparison of Volatility Models: Does Anything Beat a GARCH(1,1)?" *Journal of Applied Econometrics*, 20(7), 873-889.
16. Harris, L. (2003). *Trading and Exchanges: Market Microstructure for Practitioners*. Oxford University Press.
17. Harvey, C.R., Liu, Y., & Zhu, H. (2016). "...and the Cross-Section of Expected Returns." *Review of Financial Studies*, 29(1), 5-68.
18. Hayashi, T. & Yoshida, N. (2005). "On Covariance Estimation of Non-Synchronously Observed Diffusion Processes." *Bernoulli*, 11(2), 359-379.
19. Kelly, J.L. Jr. (1956). "A New Interpretation of Information Rate." *Bell System Technical Journal*, 35(4), 917-926.
20. Lopez de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley.
21. MacLean, L.C., Thorp, E.O., & Ziemba, W.T. (2011). "Good and Bad Properties of the Kelly Criterion." In: *The Kelly Capital Growth Investment Criterion*. World Scientific.
22. MacLean, L.C., Ziemba, W.T., & Blazenko, G. (1992). "Growth versus Security in Dynamic Investment Analysis." *Management Science*, 38(11), 1562-1585.
23. Moreira, A. & Muir, T. (2017). "Volatility-Managed Portfolios." *Journal of Finance*, 72(4), 1611-1644.
24. Page, E.S. (1954). "Continuous Inspection Schemes." *Biometrika*, 41(1/2), 100-115.
25. Roll, R. (1984). "A Simple Implicit Measure of the Effective Bid-Ask Spread in an Efficient Market." *Journal of Finance*, 39(4), 1127-1139.
26. Zhang, Y., Ma, F., & Liao, Y. (2020). "Intraday Time Series Momentum: Global Evidence and Links to Market Characteristics." *Journal of Financial Markets*, 57.
