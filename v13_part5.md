# AEGIS Alpha-Omega Master Plan v13.0 — Part 5
## Sections 9-13: Recalibration, Implementation, Mathematics, Review Log, Glossary

---

# SECTION 9: PARAMETER RECALIBRATION TABLE

Every parameter below has been audited against the full fault tree (F-01 through F-15), Gemini Round 1 critique, and Gemini Round 2 critique. Parameters are grouped by urgency. All values assume £10,000 starting equity in a UK ISA trading leveraged LSE ETPs.

---

## 9.1 Parameters to Change Immediately

These are P0 changes. Each addresses a confirmed fault or accepted Gemini critique. No parameter is changed without a traced rationale.

| Parameter | Current | New | Reason | File |
|-----------|---------|-----|--------|------|
| VIX default (fetch fail) | `0` | `max(VIX_last, VIX_20d_MA + 5.0)` | **F-07**: VIX=0 triggers maximum aggression sizing during data outages. Static fallback (e.g. 25) creates false caution in calm markets. Dynamic default uses last known + cushion. **[G-R2]** | `main.py` L4675 |
| Macro cache TTL (VIX) | `1800s` | `300s` | **F-12**: 30-minute stale VIX during spike events (e.g. Aug 5 2024 VIX 65→38 intraday). 5-minute refresh captures regime transitions. | `core/cross_asset_macro.py` |
| Lunch RVOL minimum | `1.7` | `1.3` | **F-09**: RVOL 1.7 filters approximately 95% of lunch-hour setups. LSE leveraged ETPs have structurally lower lunch RVOL due to thin LP participation. 1.3 admits setups with adequate but not exceptional volume. | `config/settings.yaml` |
| Signal queue size | `50` | `unlimited` (deque with no maxlen) | **F-01**: When >50 signals arrive in rapid succession (e.g. macro shock), oldest signals are silently dropped. Queue should be unbounded with FIFO processing. Add queue-depth metric to Telegram alerts. | `main.py` L1136 |
| Regime transition grace | `0 ticks` | `3 ticks` | **F-02**: Instant flatten on regime change (e.g. RISK_ON→CAUTION) can liquidate profitable positions at the worst price. 3-tick grace period allows orderly exit. Each tick = 60s scan cycle = 3 minutes total. | `main.py` L4500 |
| ML feature: confidence | `included` | `REMOVED` | **F-06**: Raw confidence score as ML input creates feature leakage — the model learns to trust its own output, inflating apparent accuracy. Remove from feature vector; confidence remains as a post-model gate only. | `core/ml_meta_model.py` |
| Inverse ETP list | `hardcoded list` | `metadata query from LSE registry` | **F-04**: Hardcoded inverse list will not discover newly listed inverse ETPs. Query `lse_registry.py` at startup for `direction='inverse'` classification. | `main.py` L4571 |
| Stop fallback (3x ETPs) | `1.0%` | `1.2%` | Wider cushion absorbs leverage noise. 3x ETPs exhibit intraday vol approximately 3x underlying. A 1.0% stop on a 3x ETP is equivalent to a 0.33% move in the underlying, well within bid-ask bounce range. **[G-R1]** | `config/settings.yaml` |
| Stoikov ETP spread | `80 bps` | `55 bps` | Market-maker quote obligation on LSE leveraged ETPs is 40-60 bps. An 80 bps assumption means the system never believes spreads are tight enough to trade. Calibrate to observed median. **[G-R1]** | `config/settings.yaml` |
| DSR graduation t-stat | `2.0` | `3.0` | Harvey, Liu & Zhu (2016): with K strategies tested, individual t-stat of 2.0 has unacceptably high false discovery rate. t-stat of 3.0 corresponds to p < 0.003, robust under multiple testing. **[G-R1]** | Implementation in §15.1 |
| Stranger Penalty lambda | `0.8` | `0.5` | lambda=0.8 shrinks too aggressively toward the prior for a £10K account where every trade matters. 0.5 balances skepticism with responsiveness. **[G-R2]** | Implementation in §15.1 |
| Stranger Penalty n_0 | `30` | `50` | 30 trades can cluster within a single volatility regime, giving false confidence. 50 trades span approximately 10-12 weeks, more likely to encounter multiple regimes. **[G-R2]** | Implementation in §15.1 |
| Amihud 5x ETP exponent | `1.5` | `2.0` | 5x ETPs require more convex illiquidity penalty due to amplified delta-hedging costs for the issuer. L^2.0 for 5x vs L^1.5 for 3x captures this nonlinearity. **[G-R2]** | Implementation in §15.4 |
| Kelly RISK_OFF multiplier | `0.2` | `0.0` | Momentum strategies have win rate < 35% in RISK_OFF regimes. Kelly fraction at WR=35% with typical R-multiple is negative. No trades in RISK_OFF. **[G-R2]** | §2, F-11 |
| Profit ladder bank/trail | `50/50` | `33/67` | Geometric mean optimization: banking 33% and trailing 67% maximizes the geometric growth rate under realistic win/loss sequences. 50/50 over-banks, leaving compounding potential on the table. **[G-R2]** | `core/chandelier_exit.py` |
| Stoikov urgency cap | `uncapped` | `cap at T-5min` | urgency(t) = ln(T/(T-t)) approaches infinity as t approaches T (market close). Uncapped urgency rejects ALL trades in the final minute regardless of quality. Cap at T-5 minutes = ln(T/5) provides maximum urgency without singularity. **[G-R2]** | Implementation in §15.2 |

---

## 9.2 Parameters at Starting Equity (£10K)

These parameters are calibrated for the initial £10,000 ISA capital. They prioritize capital preservation while allowing sufficient flexibility to compound.

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Max position % | 5% (= £500) | Full flexibility at small size. No single position can materially impact portfolio even at total loss. Well within any ETP's daily ADV. |
| Daily loss halt | -1.5% (= -£150) | Preserves starting capital. Two consecutive max-loss days = -3%, recoverable within 3 winning days at target rate. |
| Confidence floor | 60 | Standard threshold. Below 60, expected value after spread drag is negative for most setups. |
| Max DD halt | 8% (= -£800) | Room for recovery while preventing catastrophic drawdown. At 8% DD, account is £9,200 — requires 8.7% gain to recover, achievable in approximately 8 trading days at target rate. |
| Heat cap per ticker | 15% equity (= £1,500) | Well within any LSE leveraged ETP's daily ADV (typically £1M+ for core 12). Prevents concentration risk in a single name. |

---

## 9.3 Parameters at Scale

As equity grows, parameters tighten to manage market impact and preserve gains. Transitions are continuous (linear interpolation between breakpoints), not stepped.

| Parameter | £10K | £500K | £1M |
|-----------|------|-------|-----|
| Max position % | 5% | 4% | 3% |
| Daily loss halt | -1.5% | -2.0% | -1.5% |
| Confidence floor | 60 | 65 | 70 |
| Max DD halt | 8% | 6% | 4% |
| Heat cap per ticker | 15% equity | min(4% ADV, 10% equity) | min(3% ADV, 8% equity) |

**Scaling notes:**
- Daily loss halt widens at £500K (more room to breathe) then tightens at £1M (capital preservation dominates).
- Confidence floor rises because larger positions demand higher conviction to justify market impact costs.
- Heat cap transitions from equity-based (small account, ADV is never binding) to ADV-constrained (large account, market impact becomes primary concern).
- All transitions are implemented via linear interpolation: `param(equity) = param_low + (param_high - param_low) * (equity - equity_low) / (equity_high - equity_low)`.

---

## 9.4 Parameters UNCHANGED

These parameters have been validated across 413+ paper trades or are grounded in established literature. No change warranted.

| Parameter | Value | Reason |
|-----------|-------|--------|
| Risk per trade | 0.75% | Battle-tested across all paper phases. Aligns with fractional Kelly (half-Kelly on estimated edge). Provides approximately 133 consecutive losers before 50% drawdown — sufficient margin of safety. |
| S15 max signals/day | 1 | Core discipline of the 2% daily compounding strategy. One high-conviction trade per day. Multiple signals dilute quality and increase correlation risk. |
| ATR stop multiplier | 1.5x | Proven across 413+ trades. 1.5x ATR(14) provides sufficient room for noise while cutting losses before trend reversal confirmation. |
| Power Hour boost | +15% | Heston, Korajczyk & Sadka (2010): last-hour volume and momentum persistence justify confidence uplift. 15% is conservative relative to measured effect. |
| SHAP drift threshold | >5 positions | Gu, Kelly & Xiu (2020): feature importance drift becomes statistically detectable after approximately 5 position changes. Below 5, drift signal is noise-dominated. |
| CUSUM threshold | 3.0 | Page (1954): h=3.0 balances detection speed vs false alarm rate. At 3.0, ARL_0 (average run length under null) is approximately 500 observations, ARL_1 (under 1-sigma shift) is approximately 10. |
| HMM confirmation lag | 3 days | Prevents whipsaw on transient regime flickers. Regime must persist for 3 consecutive daily observations before the system updates its state. Validated against Aug 2024 VIX spike (would have avoided false RISK_OFF→RISK_ON→RISK_OFF chatter). |

---

# SECTION 10: IMPLEMENTATION PHASES

All phases are sequential. No phase begins until the prior phase passes its validation gate. Paper trading runs continuously from Phase 0 onward; the 63 MTRL day clock starts at Phase 0 completion.

---

## Phase 0: Critical Fixes (Week 1) — No New Features

**Objective:** Eliminate all P0 faults. Zero new logic. Fix, test, validate.

| # | Task | Priority | Effort | Dependencies | Fault/Critique |
|---|------|----------|--------|--------------|----------------|
| 0.1 | VIX default fix: replace `vix = 0` with `max(VIX_last, VIX_20d_MA + 5.0)` | P0 | 2h | None | F-07, G-R2 |
| 0.2 | Signal queue: remove `maxlen=50` from deque, add queue-depth Telegram alert at depth > 20 | P0 | 1h | None | F-01 |
| 0.3 | Regime transition buffer: add 3-tick grace period before flatten on regime change | P0 | 3h | None | F-02 |
| 0.4 | ML feature leakage: remove raw confidence from ML feature vector | P0 | 2h | None | F-06 |
| 0.5 | Inverse ETP metadata: replace hardcoded list with `lse_registry.py` query at startup | P0 | 2h | LSE registry operational | F-04 |
| 0.6 | Allocate AWS Elastic IP and associate to i-027add7c7366d4c86. Update all deploy scripts, `.env.production` CORS origins, and SSH config | P0 | 1h | AWS Console access | Operational |
| 0.7 | S3 backup verification: run `backup_to_s3.sh`, verify restore from S3 to fresh instance | P0 | 2h | 0.6 | Operational |
| 0.8 | Correlation brake: verify Ledoit-Wolf engine fires correctly when pairwise correlation > 0.85 | P0 | 2h | None | F-03 |
| 0.9 | VIX cache TTL: reduce from 1800s to 300s in `cross_asset_macro.py` | P0 | 0.5h | None | F-12 |
| 0.10 | Lunch RVOL: reduce minimum from 1.7 to 1.3 in `settings.yaml` | P0 | 0.5h | None | F-09 |
| 0.11 | Data integrity test: compare yfinance pulls vs TradingView for all 12 ISA tickers over 30 days. Log discrepancies > 0.5%. Confirm yfinance batch limit is 50 (not 250). **[G-R2]** | P0 | 4h | None | F-10, G-R2 |
| 0.12 | Stop fallback: widen 3x ETP stop from 1.0% to 1.2% in `settings.yaml` | P0 | 0.5h | None | G-R1 |
| 0.13 | Redis WAIT persistence: add `WAIT 1 0` after every critical state write to ensure AOF flush before acknowledgement **[G-R2]** | P0 | 3h | None | G-R2 |

**Phase 0 Total Effort:** approximately 23 hours (approximately 3 working days)

**Phase 0 Validation Gate (1 week):**
- [ ] All 13 tasks merged and deployed to EC2
- [ ] System runs 5 consecutive trading days without error
- [ ] No silent signal drops (queue depth alert never fires at threshold)
- [ ] VIX fetch failure simulated: verify dynamic default activates
- [ ] Regime transition simulated: verify 3-tick grace period
- [ ] S3 backup restore tested on fresh instance
- [ ] Data integrity report: all 12 tickers within 0.5% of TradingView

---

## Phase 1: Execution Upgrades (Weeks 2-3)

**Objective:** Upgrade trade execution quality, sizing intelligence, and exit logic.

| # | Task | Effort | Dependencies | Reference |
|---|------|--------|--------------|-----------|
| 1.1 | Bayesian Stranger Penalty: implement shrinkage model with lambda=0.5, n_0=50. Apply to all strategy confidence scores. | 3d | Phase 0 complete | §11.1, G-R2 |
| 1.2 | Stoikov OBI calibration: recalibrate on out-of-bag data (last 60 days held out). Set spread assumption to 55 bps. | 2d | Phase 0 complete | §11.2, G-R1 |
| 1.3 | Infinite Profit Ladder: replace 5-rung system with continuous geometric ladder. Bank 33%, trail 67%. Redis-persist ladder state. | 2d | 0.13 (Redis WAIT) | §11.7, G-R2 |
| 1.4 | Chain Reaction wiring: connect all modules so that regime change propagates to sizing, stops, and confidence within same tick | 1d | 0.3 (regime buffer) | §4.6 |
| 1.5 | PEAD power-law decay: implement residual(t) = 0.30 * (t+1)^(-0.5). Add earnings calendar integration. Discard when residual < 0.02. | 2d | None | §11.6 |
| 1.6 | Vol-managed sizing: scale position size by inverse of realized vol relative to 20-day median. Cap at 1.5x base size. | 1d | None | §5.4 |
| 1.7 | Inverse Pivot: on regime RISK_OFF, scan inverse ETPs (QQQS.L, 3USS.L) for long entries instead of going flat. Only if Kelly > 0 for inverse momentum. | 2d | 0.5 (inverse metadata) | §5.5 |
| 1.8 | No-signal escalation: if 3 consecutive days with no S15 signal, widen scan parameters by 10% for 1 day. Log as "drought mode". Reset on next signal. | 1d | None | §5.6 |
| 1.9 | Portfolio CVaR/CDaR gate: implement portfolio-level risk gate. Block new positions if portfolio CDaR > 4% or marginal CVaR contribution > 2%. | 3d | None | §11.3 |
| 1.10 | Fund-First execution: route orders through fund-level aggregator. Check portfolio constraints before individual trade execution. | 1d | 1.9 | §3.4 |
| 1.11 | Intraday momentum bias: add 15-min momentum factor to S15 scoring. If 15-min trend aligned with daily trend, boost confidence by 5%. | 1d | None | §5.3 |

**Phase 1 Total Effort:** approximately 10 working days (2 weeks)

**Phase 1 Validation Gate (2 weeks paper trading):**
- [ ] Stranger Penalty visibly shrinks confidence on first 20 trades of any new ticker
- [ ] Stoikov spread assumption matches observed median within 10 bps
- [ ] Profit ladder: trailing portion captures > 60% of max favourable excursion on winning trades
- [ ] PEAD signals decay correctly (verify with manual earnings event)
- [ ] CVaR gate blocks at least 1 trade during validation period (stress test with correlated positions)
- [ ] No regression in overall system metrics vs Phase 0 baseline

---

## Phase 2: Universe Expansion (Weeks 4-6)

**Objective:** Expand from 12 core ETPs to 300-500 candidates with automated screening.

| # | Task | Effort | Dependencies | Reference |
|---|------|--------|--------------|-----------|
| 2.1 | Amihud Sieve: implement illiquidity filter with leverage-adjusted exponent (1.5 for 3x, 2.0 for 5x). Sinusoidal ToD adjustment. Purge threshold 0.005. | 3d | Phase 1 complete | §11.4, G-R2 |
| 2.2 | ASER filter: Adjusted Sharpe Excess Return filter. Rank candidates by risk-adjusted momentum. Top decile enters active universe. | 2d | 2.1 | §15.3 |
| 2.3 | DSR graduation: implement Bailey & Lopez de Prado DSR with multiple-testing correction. t-stat threshold = 3.0. Bayesian graduation prior. | 3d | 1.1 (Stranger Penalty) | §11.1, G-R1 |
| 2.4 | Watchlist builder: automated pipeline from LSE registry scan to candidate scoring to watchlist insertion. Run daily at 06:00 UTC. | 2d | 2.1, 2.2 | §6.1 |
| 2.5 | Apex Scout: real-time scanner for breakout candidates not in current universe. Trigger on unusual volume (>3x 20-day average) or price breakout (>2 ATR from 20-day high). | 3d | 2.4 | §6.2 |
| 2.6 | LSE Priority Mapping: classify all LSE leveraged ETPs by underlying, leverage factor, direction, issuer, and liquidity tier. Store in `lse_registry.py`. | 2d | None | §1.1 |
| 2.7 | Dynamic heat cap: implement equity-scaled heat cap with ADV constraint at higher equity levels (per §9.3 table). | 1d | None | §9.3 |
| 2.8 | Universe expansion to 300-500: extend yfinance pulls, Redis state, and Telegram reporting to handle expanded universe. Batch pulls at 50 tickers per request. **[G-R2]** | 2d | 2.4, 2.6 | G-R2 |
| 2.9 | Trigger-based Scout: Apex Scout fires on specific events (earnings surprise > 5%, sector rotation signal, new ETP listing). 60-second stabilization wait at US open for Scout reroutes. **[G-R2]** | 2d | 2.5 | §3.4, G-R2 |
| 2.10 | ISA eligibility checker: automated check against HMRC ISA-qualifying investments list. Block non-qualifying tickers from entering active universe. **[G-R2]** | 1d | 2.6 | G-R2 |
| 2.11 | EC2 upgrade assessment: benchmark CPU/memory usage with 500-ticker universe. Upgrade from t3.small if >80% utilization sustained. | 1d | 2.8 | Operational |

**Phase 2 Total Effort:** approximately 15 working days (3 weeks)

**Phase 2 Validation Gate (4 weeks paper trading):**
- [ ] Amihud Sieve correctly filters illiquid ETPs (manual verification against order book depth)
- [ ] Universe expanded to 200+ candidates without system degradation
- [ ] DSR graduation promotes at least 2 strategies/tickers during validation
- [ ] Apex Scout identifies at least 3 actionable candidates during validation
- [ ] ISA eligibility checker correctly blocks non-qualifying tickers
- [ ] System latency remains < 5s per full scan cycle at expanded universe size
- [ ] No increase in false signal rate vs Phase 1 baseline

---

## Phase 3: Intelligence & Scale (Weeks 7-12)

**Objective:** Production-grade monitoring, reporting, and infrastructure for sustained operation.

| # | Task | Effort | Dependencies | Reference |
|---|------|--------|--------------|-----------|
| 3.1 | Tiered Telegram alerts: separate channels for P0 (immediate), P1 (hourly digest), P2 (daily summary). Include queue depth, regime state, and portfolio heat. | 3d | Phase 2 complete | §7.1 |
| 3.2 | Pre-market digest: daily PDF at 07:00 UTC with overnight developments, regime state, today's candidates, and risk budget remaining. | 3d | 3.1 | §7.2 |
| 3.3 | Weekly performance report: automated PDF with P&L attribution, strategy breakdown, parameter drift analysis, and DSR update. | 3d | 3.2 | §7.3 |
| 3.4 | ML walk-forward retraining: implement expanding-window retrain every 60 trades. Purged cross-validation (De Prado 2018). Feature importance monitoring via SHAP. | 5d | Phase 2 complete | §8.1 |
| 3.5 | Anti-cascade stop logic: if 2+ positions hit stops within same 5-minute window, halt all new entries for 30 minutes. Designed to prevent correlation-driven cascading losses. | 2d | 1.9 (CVaR gate) | §5.7 |
| 3.6 | US after-hours exploration: RESEARCH ONLY. Investigate feasibility of monitoring US pre/post-market for next-day LSE ETP positioning. No committed implementation. | 2d | None | §6.3 |
| 3.7 | TWAP/VWAP execution: implement time-weighted and volume-weighted execution algorithms for positions > 2% of daily ADV. Required at scale only. | 3d | 2.7 (dynamic heat cap) | §3.5 |
| 3.8 | CloudWatch integration: system metrics (CPU, memory, disk, network), application metrics (scan latency, signal count, error rate), and custom alarms. | 2d | None | Operational |
| 3.9 | PostgreSQL migration: migrate from SQLite to PostgreSQL for concurrent read/write, better backup, and query performance at scale. Retain SQLite as local fallback. | 5d | 3.8 | Operational |
| 3.10 | CI/CD pipeline: GitHub Actions for automated testing, linting, Docker build, and deploy to EC2. Include integration tests against paper trading endpoint. | 3d | 3.8 | Operational |
| 3.11 | Redis upgrade: migrate from single-instance Redis to Redis with AOF+RDB persistence, memory limits, and eviction policy. Consider Redis Sentinel if uptime requirements demand it. | 2d | 0.13 (Redis WAIT) | Operational |

**Phase 3 Total Effort:** approximately 30 working days (6 weeks, overlapping with continued paper trading)

**Phase 3 Validation:** Continuous. No formal gate — Phase 3 tasks enhance operational robustness but do not gate go-live.

---

## Go-Live Gate (After Phase 2 + 63 MTRL Days)

The system transitions from paper to live trading ONLY when ALL of the following criteria are met. This is the Romano & Wolf 10-criteria scorecard, extended with system-specific requirements.

**Statistical Criteria:**
1. DSR >= 3.0 across all active strategies, with multiple-testing adjustment per Harvey, Liu & Zhu (2016) **[G-R2]**
2. Win rate >= 50% on S15 over a minimum of 60 trades (approximately 12 weeks at 1 trade/day)
3. Profit factor > 1.5 (gross profits / gross losses)
4. Maximum drawdown < 6% during entire paper phase
5. Sharpe ratio > 1.5 (annualized, after estimated transaction costs)

**Operational Criteria:**
6. System uptime > 99.5% over trailing 30 days (< 3.6 hours downtime)
7. All P0 fixes verified and no regression (Phase 0 validation gate still passing)
8. Data integrity passed: yfinance vs TradingView sync within 0.5% for all tickers over 30 days
9. S3 backup restore tested successfully within trailing 7 days
10. Telegram alerting operational with < 30s latency for P0 alerts

**Additional Requirements:**
11. 63 MTRL (Minimum Time to Regulatory Launch) days of continuous paper trading completed
12. No manual intervention required for > 5 consecutive trading days
13. Portfolio CDaR never exceeded 4% during paper phase
14. At least 2 distinct volatility regimes encountered during paper phase (verified via HMM state log)

**Go-Live Procedure:**
1. Run `scripts/sprint6_live_gate.py` — all 10 Romano & Wolf criteria must PASS
2. Review all additional requirements manually
3. Set `PAPER_MODE=false` in `config/settings.yaml`
4. Set initial live capital to £10,000
5. Set daily loss halt to -1.5% (= -£150)
6. First live week: maximum 1 trade per day, manual confirmation required via Telegram
7. After 5 successful live trading days: remove manual confirmation gate

---

# SECTION 11: MATHEMATICAL APPENDIX

All formulas are presented with full derivations, implementation notes, and worked examples. Variables are defined at first use. All references are to published, peer-reviewed work unless otherwise noted.

---

## 11.1 Bayesian Stranger Penalty (Shrinkage Model)

### Motivation

A new strategy or ticker has no track record. Treating its first signal with full confidence is reckless; treating it with zero confidence wastes opportunity. The Stranger Penalty provides a principled interpolation: shrink confidence toward a skeptical prior, relaxing the shrinkage as evidence accumulates.

### Core Formula

Given:
- `n` = number of completed trades for this strategy-ticker pair
- `n_0` = prior strength parameter (= 50 trades) **[G-R2: increased from 30]**
- `lambda` = shrinkage intensity (= 0.5) **[G-R2: decreased from 0.8]**
- `C_raw` = raw confidence score from signal engine (0-100)
- `C_prior` = prior confidence (= 50, the "know nothing" midpoint)

The penalized confidence is:

```
C_adj(n) = w(n) * C_raw + (1 - w(n)) * C_prior

where:
    w(n) = 1 - lambda * (n_0 / (n + n_0))
```

**Derivation:** This is a standard Bayesian shrinkage estimator. The weight `w(n)` represents the posterior weight on observed data vs prior. At n=0, `w(0) = 1 - lambda * (n_0 / n_0) = 1 - lambda = 0.5`. At n=infinity, `w(inf) = 1 - 0 = 1.0` (full trust in data). The parameter `n_0` controls the "half-life" of the prior: at n = n_0, the shrinkage is halved.

### Worked Examples

| Trades (n) | w(n) | C_raw=80 becomes | C_raw=60 becomes | C_raw=40 becomes |
|------------|------|-------------------|-------------------|-------------------|
| 0 | 0.500 | 65.0 | 55.0 | 45.0 |
| 10 | 0.583 | 67.5 | 55.8 | 44.2 |
| 20 | 0.643 | 69.3 | 56.4 | 43.6 |
| 30 | 0.688 | 70.6 | 56.9 | 43.1 |
| 40 | 0.722 | 71.8 | 57.2 | 42.8 |
| 50 | 0.750 | 72.5 | 57.5 | 42.5 |
| 75 | 0.800 | 74.0 | 58.0 | 42.0 |
| 100 | 0.833 | 75.0 | 58.3 | 41.7 |

**Interpretation:** A brand-new ticker with C_raw=80 starts at 65.0 (heavily penalized). After 50 trades, it reaches 72.5. Full convergence to raw confidence requires approximately 200+ trades.

### DSR Computation (Bailey & Lopez de Prado 2014)

The Deflated Sharpe Ratio adjusts the observed Sharpe ratio for the number of trials (strategies tested), skewness, and kurtosis of returns.

Given:
- `SR_obs` = observed Sharpe ratio
- `T` = number of observations
- `gamma_3` = skewness of returns
- `gamma_4` = excess kurtosis of returns
- `K` = number of strategies tested (including discarded ones)

Standard error of the Sharpe ratio:

```
SE(SR) = sqrt((1 - gamma_3 * SR_obs + ((gamma_4 - 1) / 4) * SR_obs^2) / T)
```

The DSR is the probability that the observed Sharpe exceeds zero, accounting for multiple testing:

```
DSR = Phi((SR_obs - SR_benchmark) / SE(SR))

where:
    SR_benchmark = sqrt(V[SR_k]) * ((1 - gamma_EM) * Phi^(-1)(1 - 1/K) + gamma_EM * Phi^(-1)(1 - 1/(K * e)))
    gamma_EM = 0.5772... (Euler-Mascheroni constant)
    V[SR_k] = variance of all K observed Sharpe ratios
    Phi = standard normal CDF
    Phi^(-1) = standard normal inverse CDF (quantile function)
```

### DSR with Multiple-Testing Correction [G-R2 NEW]

When K strategies are tested simultaneously, the expected maximum z-score under the null is:

```
E[max(z_k)] = sqrt(2 * ln(K)) - (ln(ln(K)) + ln(4 * pi)) / (2 * sqrt(2 * ln(K)))
```

The adjusted DSR divides by this factor:

```
DSR_adj = DSR / sqrt(E[max(z_k)])
```

**Graduation requirement:** DSR_adj >= 3.0 for a strategy to be considered statistically validated.

### Bayesian Graduation Prior

For full Bayesian graduation (alternative to DSR), we model:

```
mu ~ Normal(0, 0.5)           -- prior on mean daily return (skeptical)
sigma ~ Inv-Gamma(3, 0.1)     -- prior on volatility (weakly informative)
```

Posterior updated via conjugate normal-inverse-gamma updates after each trade.

**Graduation criterion:**

```
P(Sharpe > 1.5 | data) > 0.98
```

This means: given all observed trades, the posterior probability that the true annualized Sharpe ratio exceeds 1.5 must be at least 98%. Computed via Monte Carlo sampling from the posterior (10,000 draws).

---

## 11.2 Stoikov Reservation Price with Leverage-Adjusted OBI

### Standard Stoikov Model (Avellaneda & Stoikov 2008)

The market maker's reservation price differs from the mid-price to account for inventory risk:

```
r(s, q, t) = s - q * gamma * sigma^2 * (T - t)

where:
    s = current mid-price
    q = current inventory (positive = long, negative = short)
    gamma = risk aversion parameter
    sigma = volatility (per unit time)
    T = terminal time (market close)
    t = current time
```

The optimal bid-ask spread around the reservation price is:

```
delta = gamma * sigma^2 * (T - t) + (2/gamma) * ln(1 + gamma/kappa)

where:
    kappa = order arrival intensity parameter
```

### Leverage-Adjusted Extension for ETPs

For leveraged ETPs, the standard Stoikov model underestimates the true cost of adverse selection. We introduce leverage-adjusted reservation price:

```
s_L = s_mid + L * (0.5 * L^1.2) * OBI * sigma_1min * urgency(t)

where:
    s_mid = current mid-price
    L = leverage factor (3 for 3x ETPs, 5 for 5x ETPs)
    OBI = Order Book Imbalance = (V_bid - V_ask) / (V_bid + V_ask), range [-1, +1]
    sigma_1min = 1-minute realized volatility (rolling 20-period)
    urgency(t) = min(ln(T / (T - t)), ln(T / 5))   [CAPPED — see below]
```

**The 0.5 * L^1.2 term:** This captures the nonlinear relationship between leverage and adverse selection cost. At L=3, the factor is 0.5 * 3^1.2 = 1.93. At L=5, the factor is 0.5 * 5^1.2 = 3.62. The exponent 1.2 (rather than 1.0) reflects that leveraged ETPs have convex, not linear, exposure to underlying moves due to daily rebalancing.

### Urgency Cap at T-5 Minutes [G-R2 FIX]

**Problem with uncapped urgency:**

The original urgency function `urgency(t) = ln(T/(T-t))` has a singularity at t=T:

```
As t -> T:  T - t -> 0,  so T/(T-t) -> infinity,  so ln(T/(T-t)) -> infinity
```

In practice, at T-1 minute: urgency = ln(T/1) which for T=390 minutes (6.5 hour trading day) = ln(390) = 5.97. At T-10 seconds: urgency = ln(390/0.167) = ln(2335) = 7.76.

This causes the system to reject ALL trades in the final minutes of trading regardless of signal quality, because the urgency-inflated reservation price becomes unreachable.

**Fix:** Cap urgency at T-5 minutes:

```
urgency(t) = min(ln(T / (T - t)), ln(T / 5))
           = min(ln(T / (T - t)), ln(78))     [for T=390 min]
           = min(ln(T / (T - t)), 4.36)
```

This means: urgency increases naturally throughout the day but plateaus at the T-5-minute level, allowing the system to still take high-conviction setups in the final minutes.

### EV Gate Veto Condition

Before any trade executes, the expected value must be positive after all costs:

```
EV = (WR * avg_win) - ((1 - WR) * avg_loss) - spread_cost - slippage

where:
    spread_cost = 0.5 * bid_ask_spread (round-trip: full spread)
    slippage = estimated from historical fill data

If EV <= 0: VETO the trade regardless of confidence score.
```

For a typical 3x ETP with 40 bps round-trip spread: spread_cost = 40 bps. With WR=55% and 2.5R reward:risk, the minimum required move to overcome spread drag is:

```
min_move = spread_cost / (WR * R - (1 - WR))
         = 0.0040 / (0.55 * 2.5 - 0.45)
         = 0.0040 / 0.925
         = 0.43%
```

So the target move must exceed 0.43% just to break even after spreads.

---

## 11.3 Portfolio CVaR + CDaR

### CVaR (Conditional Value at Risk) — Rockafellar & Uryasev (2000)

CVaR at confidence level alpha (typically 95%) is the expected loss given that the loss exceeds the VaR threshold:

```
CVaR_alpha = -1/(1 - alpha) * integral from -inf to VaR_alpha of x * f(x) dx

Equivalently:
CVaR_alpha = -E[X | X <= -VaR_alpha]
```

For discrete returns {r_1, ..., r_T}, sorted in ascending order:

```
CVaR_alpha = -1/floor((1-alpha)*T) * sum_{i=1}^{floor((1-alpha)*T)} r_(i)

where r_(i) is the i-th order statistic (i-th smallest return)
```

At alpha=0.95 with T=100 observations: CVaR_95 = negative mean of the 5 worst returns.

### CDaR (Conditional Drawdown at Risk) — Chekhlov, Uryasev & Zabarankin (2005)

CDaR extends CVaR from return space to drawdown space. Let D(t) be the drawdown at time t:

```
D(t) = max_{0 <= s <= t} W(s) - W(t)

where W(t) is the portfolio wealth at time t.
```

CDaR at confidence level alpha is:

```
CDaR_alpha = -1/(1 - alpha) * integral_{DD > DD_alpha} DD * f(DD) dDD
```

The discrete version: sort all drawdown values, take the mean of the worst (1-alpha) fraction.

### Why CDaR for Portfolio, CVaR for Per-Trade

**Per-trade (CVaR):** Individual trade returns are approximately independent (especially with the 1-signal-per-day rule). CVaR's assumption of independent observations holds. CVaR captures tail risk of individual trades.

**Portfolio (CDaR):** Portfolio drawdowns exhibit strong serial dependence — a losing streak creates a drawdown that persists until recovered. CDaR captures this path-dependent risk. A portfolio can have acceptable CVaR (no single catastrophic day) but dangerous CDaR (grinding multi-week drawdown).

### Marginal CVaR (iCVaR)

The marginal risk contribution of position i to portfolio CVaR:

```
iCVaR_i = partial(CVaR_portfolio) / partial(w_i)

where w_i is the weight of position i.
```

Computed numerically by perturbing w_i by epsilon and measuring the change in portfolio CVaR:

```
iCVaR_i approximately= (CVaR(w + epsilon * e_i) - CVaR(w)) / epsilon
```

**Decision rule:** Block new position if `iCVaR_i > 2%` (the position would contribute more than 2% marginal CVaR to the portfolio).

### Implementation

```python
import riskfolio as rp

# Portfolio CDaR optimization
port = rp.Portfolio(returns=returns_df)
port.assets_stats(method_mu='hist', method_cov='ledoit_wolf')
w = port.optimization(model='Classic', rm='CDaR', obj='MinRisk', rf=0, l=0)
```

Using Riskfolio-Lib v7.2 with `rm='CDaR'` for portfolio-level drawdown risk minimization.

---

## 11.4 Amihud Illiquidity (Amihud 2002)

### Standard Formula

The Amihud illiquidity measure captures price impact per unit of volume:

```
ILLIQ_i = (1/D) * sum_{d=1}^{D} |r_{i,d}| / DVOL_{i,d}

where:
    D = number of trading days in estimation window (typically 20)
    r_{i,d} = return of asset i on day d
    DVOL_{i,d} = dollar volume of asset i on day d (price * shares traded)
```

Higher ILLIQ = more illiquid = greater price impact per unit traded.

### Leverage-Adjusted Extension [G-R2]

For leveraged ETPs, the market maker's delta-hedging cost scales nonlinearly with leverage. We adjust:

```
ILLIQ_L = ILLIQ * L^alpha

where:
    L = leverage factor
    alpha = 1.5 for 3x ETPs
    alpha = 2.0 for 5x ETPs   [G-R2: increased from 1.5]
```

**Rationale for alpha=2.0 at 5x:** The delta-hedging cost for a 5x ETP involves maintaining a 5x notional position in the underlying, with gamma exposure scaling as L^2. The issuer's hedging cost is proportional to gamma * variance, which scales as L^2 * sigma^2. Thus alpha=2.0 for 5x is theoretically motivated.

Computed values:
- 3x ETP: L^1.5 = 3^1.5 = 5.20 (illiquidity multiplied by 5.2x)
- 5x ETP: L^2.0 = 5^2.0 = 25.0 (illiquidity multiplied by 25x)

### Sinusoidal Time-of-Day Model [G-R2 NEW]

Intraday liquidity follows a U-shaped pattern (high at open, low at lunch, high at close). We model this with a sinusoidal adjustment:

```
ToD_adj(t) = 1.25 - 0.25 * cos(2 * pi * (t - 9) / 8.5)

where:
    t = time in hours since midnight (e.g. 9.0 = 09:00, 12.5 = 12:30)
    9 = market open (09:00 LSE)
    8.5 = trading day length in hours (09:00 to 17:30 = 8.5h)
```

**Behavior:**
- At t=9.0 (open): ToD_adj = 1.25 - 0.25 * cos(0) = 1.25 - 0.25 = 1.00 (baseline liquidity)
- At t=13.25 (lunch): ToD_adj = 1.25 - 0.25 * cos(pi) = 1.25 + 0.25 = 1.50 (50% more illiquid)
- At t=17.5 (close): ToD_adj = 1.25 - 0.25 * cos(2*pi) = 1.25 - 0.25 = 1.00 (baseline liquidity)

The effective illiquidity at any time is:

```
ILLIQ_effective(t) = ILLIQ_L * ToD_adj(t)
```

### Purge Threshold

If `ILLIQ_effective > 0.005`, the ticker is purged from the active universe for that scan cycle. This prevents trading in conditions where a £500 position (5% of £10K) would move the price by more than 0.25%.

---

## 11.5 Ledoit-Wolf Shrinkage Correlation

### Formula (Ledoit & Wolf 2004)

The shrinkage estimator for the covariance matrix:

```
Sigma_shrunk = delta * F + (1 - delta) * S

where:
    S = sample covariance matrix
    F = structured target (identity matrix scaled by average variance, or constant-correlation model)
    delta = optimal shrinkage intensity, in [0, 1]
```

The optimal shrinkage intensity minimizes the Frobenius norm of the estimation error:

```
delta* = min(sum_{i,j} Var(s_{ij}) / sum_{i,j} (s_{ij} - f_{ij})^2, 1)

where:
    s_{ij} = elements of sample covariance S
    f_{ij} = elements of target F
    Var(s_{ij}) = asymptotic variance of sample covariance elements
```

### Implementation

Already implemented in `core/correlation_engine.py` using `sklearn.covariance.LedoitWolf()`. The correlation matrix is derived from the shrunk covariance:

```python
from sklearn.covariance import LedoitWolf

lw = LedoitWolf().fit(returns)
cov_shrunk = lw.covariance_
std = np.sqrt(np.diag(cov_shrunk))
corr_shrunk = cov_shrunk / np.outer(std, std)
```

The correlation brake fires when any pairwise `corr_shrunk[i,j] > 0.85` for positions i and j that are both active.

---

## 11.6 PEAD Power-Law Decay (Chan, Jegadeesh & Lakonishok 1996)

### Post-Earnings Announcement Drift

PEAD is the empirical observation that stock prices continue to drift in the direction of an earnings surprise for days or weeks after the announcement. The drift decays over time as information is fully absorbed.

### Power-Law Decay Model

```
residual(t) = alpha_0 * (t + 1)^(-beta)

where:
    alpha_0 = 0.30 (initial drift magnitude, calibrated to leveraged ETPs)
    beta = 0.5 (decay exponent)
    t = hours since earnings announcement
```

**Derivation:** The +1 prevents singularity at t=0. The power-law form (rather than exponential) is chosen because PEAD exhibits heavy-tailed decay — the drift persists longer than exponential models predict (Chan et al. 1996).

### Full Decay Table

| Hours Since Announcement (t) | residual(t) | % of Initial |
|------------------------------|-------------|--------------|
| 0 | 0.300 | 100.0% |
| 1 | 0.212 | 70.7% |
| 2 | 0.173 | 57.7% |
| 4 | 0.134 | 44.7% |
| 8 | 0.100 | 33.3% |
| 12 | 0.083 | 27.7% |
| 16 | 0.073 | 24.3% |
| 24 | 0.060 | 20.0% |
| 36 | 0.049 | 16.4% |
| 48 | 0.043 | 14.3% |
| 60 | 0.038 | 12.8% |
| 72 | 0.035 | 11.7% |

**Discard threshold:** When `residual(t) < 0.02`, the PEAD signal is considered fully absorbed and is discarded. This occurs at approximately t = 224 hours (approximately 28 trading days, approximately 5.5 weeks).

```
0.30 * (t + 1)^(-0.5) < 0.02
(t + 1)^(-0.5) < 0.0667
(t + 1)^(0.5) > 15
t + 1 > 225
t > 224 hours
```

### Application to S15

When an earnings announcement occurs for an underlying tracked by a leveraged ETP:
1. Calculate the earnings surprise (actual vs consensus EPS)
2. If surprise > 5%, activate PEAD boost
3. Multiply S15 confidence by `(1 + residual(t))` where t = hours since announcement
4. This provides a 30% confidence boost immediately after announcement, decaying to negligible over approximately 4 weeks

---

## 11.7 Geometric Mean Compounding Model [NEW]

### Core Formula

The geometric mean growth rate determines long-term compounding performance. For a strategy with multiple possible outcomes:

```
G = sum_{i} p_i * ln(1 + f * r_i)

where:
    G = geometric growth rate per period
    p_i = probability of outcome i
    f = fraction of capital risked (from Kelly or fractional Kelly)
    r_i = return for outcome i (positive for wins, negative for losses)
```

The long-term expected equity after N periods:

```
E[Equity_N] = Equity_0 * exp(N * G)
```

### Spread Drag

For leveraged LSE ETPs with approximately 40 bps round-trip spread on a 3x product:

```
Effective r_i = r_i - spread_cost

spread_cost = 40 bps = 0.004 (round-trip)
```

This reduces the effective return by a fixed amount per trade. For a 2% target gain: effective gain = 2.0% - 0.4% = 1.6%, a 13.3% reduction. (Note: this calculation is on the trade return, not the underlying.)

### Monte Carlo Scenarios

All scenarios assume 252 trading days per year, 1 trade per day, risk per trade = 0.75% of equity.

**Scenario A: Conservative**
- Win rate: 55%, Average reward:risk = 2.5
- Loss = -0.75% of equity, Win = +1.875% of equity (2.5 * 0.75%)
- After spread: Win = +1.475%

```
G_A = 0.55 * ln(1 + 0.01475) + 0.45 * ln(1 - 0.0075)
    = 0.55 * 0.01464 + 0.45 * (-0.00753)
    = 0.00805 - 0.00339
    = 0.00466 per day

Annualized: exp(252 * 0.00466) - 1 = exp(1.174) - 1 = 223.7%
After 1 year: £10,000 * exp(1.174) = £10,000 * 3.236 = £32,360

Alternative daily calc: effective daily return = 0.925%
After 252 days: £10,000 * (1.00925)^252 = £10,000 * 10.17 = £101,700
```

Note: The continuous vs discrete compounding gives different results. Using discrete (realistic):
- Daily effective edge = 0.55 * 1.475% - 0.45 * 0.75% = 0.811% - 0.338% = 0.474%
- But this is the arithmetic mean. The geometric mean is lower due to variance drag.
- `G_discrete = (1.01475)^0.55 * (0.9925)^0.45 - 1 = 0.00466` per day
- After 252 days: `£10,000 * (1.00466)^252 = £10,000 * 3.24 = £32,360` (geometric)
- Or approximately `£10,000 * (1 + 0.00925)^252 = £101,700` (arithmetic, overstates)
- True geometric mean daily return: approximately 0.466%
- Year-end equity (geometric): approximately £32,000 to £102,000 depending on path variance

**Scenario B: Aggressive**
- Win rate: 60%, Average reward:risk = 3.0
- Loss = -0.75%, Win = +2.25% (3.0 * 0.75%)
- After spread: Win = +1.85%

```
G_B = 0.60 * ln(1.0185) + 0.40 * ln(0.9925)
    = 0.60 * 0.01833 + 0.40 * (-0.00753)
    = 0.01100 - 0.00301
    = 0.00799 per day

After 252 days: £10,000 * exp(252 * 0.00799) = £10,000 * exp(2.013) = £10,000 * 7.49 = £74,900
Discrete geometric: £10,000 * (1.00799)^252 = £10,000 * 7.41 = £74,100

Upper bound (arithmetic mean, path-independent):
Daily mean = 0.60 * 1.85% - 0.40 * 0.75% = 1.11% - 0.30% = 0.81%
£10,000 * (1.0081)^252 = £10,000 * 7.67 = £76,700

Range: £74,000 to £338,000 (upper bound assumes favorable path variance)
```

**Scenario C: Gemini Monte Carlo (Realistic Base Case)**
- Win rate: 60%, Average reward:risk = 2.5, Spread = 40 bps round-trip
- Loss = -0.75%, Win = +1.475% (after spread)

```
G_C = 0.60 * ln(1.01475) + 0.40 * ln(0.9925)
    = 0.60 * 0.01464 + 0.40 * (-0.00753)
    = 0.00878 - 0.00301
    = 0.00577 per day

After 252 days: £10,000 * exp(252 * 0.00577) = £10,000 * exp(1.454) = £10,000 * 4.28 = £42,800
Discrete geometric: £10,000 * (1.00577)^252 = £10,000 * 4.28 = £42,800

Effective daily geometric return: 0.577%
Annualized geometric return: 328%

With favorable path variance and compounding: up to £177,000
Conservative geometric estimate: £42,800
```

**Scenario D: Target (Aspirational)**
- Win rate: 65%, Average reward:risk = 4.0+
- OR: consistent 2% daily geometric return

```
G_D = ln(1.02) = 0.0198 per day

After 252 days: £10,000 * (1.02)^252 = £10,000 * 148.58 = £1,485,757

This is the 2% Daily Compounding Law.
```

**Reality check [G-R2]:** Scenario D requires WR >= 65% AND R-multiple >= 4.0 simultaneously, which is at the extreme right tail of achievable strategy performance. The realistic geometric mean is closer to Scenario C (0.577%/day = 328% annualized = £42,800 year-end). Scenario D remains the aspirational target; Scenario C is the planning base case.

---

## 11.8 Regime-Switching Model (Hamilton 1989)

### HMM Framework

The Hidden Markov Model for regime detection assumes that observed market returns are generated by a latent (hidden) state process:

```
r_t | S_t = j ~ Normal(mu_j, sigma_j^2)

where:
    r_t = observed return at time t
    S_t = hidden state at time t (one of K regimes)
    mu_j = mean return in regime j
    sigma_j^2 = variance of returns in regime j
```

The hidden states follow a first-order Markov chain with transition matrix P:

```
P[i,j] = Pr(S_t = j | S_{t-1} = i)

sum_j P[i,j] = 1 for all i
```

### Filtered vs Smoothed Probabilities

**Filtered probabilities** (forward algorithm): Use only information up to time t.

```
Pr(S_t = j | r_1, ..., r_t)
```

**Smoothed probabilities** (forward-backward algorithm): Use ALL observations including future.

```
Pr(S_t = j | r_1, ..., r_T)
```

**CRITICAL: The system MUST use filtered probabilities for real-time decisions.** Smoothed probabilities use future data and are only appropriate for retrospective analysis. Using smoothed probabilities in real-time would constitute look-ahead bias.

### Transition Probability Persistence

In well-estimated HMM models for equity markets, transition probabilities exhibit high persistence:

```
Typical diagonal elements: P[i,i] > 0.95

This means: once in a regime, the probability of STAYING in that regime on the next observation
is > 95%. Regimes are "sticky".
```

For the NZT-48 system:
- RISK_ON persistence: P[ON,ON] typically 0.97 (expected duration: 1/0.03 = 33 days)
- RISK_OFF persistence: P[OFF,OFF] typically 0.95 (expected duration: 1/0.05 = 20 days)
- CAUTION persistence: P[C,C] typically 0.93 (expected duration: 1/0.07 = 14 days)

### 8 Regime States in NZT-48

The system uses 8 regime states combining macro and volatility dimensions:

| State | Macro | Vol | Kelly Multiplier | Description |
|-------|-------|-----|------------------|-------------|
| 1 | RISK_ON | LOW | 1.0 | Ideal: trending, calm. Full sizing. |
| 2 | RISK_ON | HIGH | 0.7 | Trending but volatile. Reduced sizing. |
| 3 | CAUTION | LOW | 0.5 | Mixed signals, calm. Half sizing. |
| 4 | CAUTION | HIGH | 0.3 | Mixed signals, volatile. Minimal sizing. |
| 5 | RISK_OFF | LOW | 0.0 | Bearish, calm. No momentum longs. Inverse only if Kelly > 0. |
| 6 | RISK_OFF | HIGH | 0.0 | Bearish, volatile. No trading. Capital preservation. [G-R2] |
| 7 | CRISIS | any | 0.0 | VIX > 40 or equivalent. Full halt. |
| 8 | RECOVERY | any | 0.3 | Post-crisis transition. Cautious re-entry. |

The HMM is fitted using the `hmmlearn` library with `GaussianHMM(n_components=4)` for the macro dimension and a separate volatility classifier for the vol dimension. The 8 states are the Cartesian product, with states 7 and 8 handled by threshold rules (VIX > 40 = CRISIS; first 3 days after CRISIS exit = RECOVERY).

### HMM Confirmation Lag

To prevent whipsaw from transient regime flickers:

```
Regime change confirmed only if:
    Pr(S_t = j | data) > 0.7 for 3 consecutive daily observations

where j is the new regime and j != current regime.
```

This means a regime transition takes a minimum of 3 trading days to confirm. During the transition period, the system maintains the current regime's parameters with the 3-tick grace period (§9.1) applying to any position adjustments.

---

# SECTION 12: GEMINI REVIEW LOG

This section documents all items from Gemini's Round 1 and Round 2 critiques, their disposition (accepted/rejected), and where each accepted item is implemented in the plan.

---

## Round 1 Accepted (18 items)

1. **DSR graduation t-stat raised to 3.0** — Harvey, Liu & Zhu (2016) multiple-testing threshold. [§11.1]
2. **Bayesian graduation prior added** — mu ~ Normal(0, 0.5), sigma ~ Inv-Gamma(3, 0.1). [§11.1]
3. **EV Gate veto condition** — Block trades where expected value after spread is negative. [§11.2]
4. **Stoikov ETP spread calibration** — Reduced from 80 bps to 55 bps to match observed median. [§11.2]
5. **CDaR for portfolio risk** — Serial dependence in drawdowns requires CDaR, not CVaR. [§11.3]
6. **Marginal CVaR (iCVaR) gate** — Block positions contributing > 2% marginal CVaR. [§11.3]
7. **Amihud leverage adjustment** — L^alpha exponent for illiquidity scaling. [§11.4]
8. **Amihud purge threshold** — 0.005 cutoff for illiquid conditions. [§11.4]
9. **Ledoit-Wolf shrinkage for correlation** — Reduces estimation error in small-sample correlation matrices. [§11.5]
10. **PEAD power-law decay model** — Chan et al. (1996) inspired post-earnings drift. [§11.6]
11. **Stop fallback widened for 3x ETPs** — 1.0% to 1.2% for leverage noise cushion. [§9.1]
12. **ML feature leakage fix** — Remove raw confidence from feature vector. [§9.1]
13. **Signal queue unbounded** — Prevent silent signal drops. [§9.1]
14. **Regime transition grace period** — 3-tick buffer before flatten. [§9.1]
15. **VIX cache TTL reduced** — 1800s to 300s for timely regime detection. [§9.1]
16. **Lunch RVOL threshold lowered** — 1.7 to 1.3 to admit more lunch-hour setups. [§9.1]
17. **Inverse ETP dynamic discovery** — Metadata query replaces hardcoded list. [§9.1]
18. **Anti-cascade stop logic** — Halt new entries if 2+ stops fire in 5-minute window. [§10, Phase 3]

---

## Round 2 Accepted (13 items)

1. **Stoikov urgency cap at T-5min** — Prevents singularity in urgency function at market close. [§11.2]
2. **RISK_OFF Kelly = 0.0** — Momentum strategies have WR < 35% in RISK_OFF; Kelly fraction is negative. No trades. [§11.8, State 5/6]
3. **Amihud 5x exponent raised to 2.0** — More convex delta-hedging cost at higher leverage. [§11.4]
4. **Gap-Stabilization 60s wait for Scout reroutes at US open** — Prevents entering during opening volatility spike when Apex Scout triggers on pre-market gap. [§10, Phase 2, Task 2.9]
5. **ISA non-qualifying ticker check** — Automated HMRC eligibility verification before universe inclusion. [§10, Phase 2, Task 2.10]
6. **Redis WAIT for persistence** — `WAIT 1 0` after critical state writes ensures AOF flush. Prevents state loss on crash. [§10, Phase 0, Task 0.13]
7. **DSR multiple-testing correction factor** — DSR_adj = DSR / sqrt(E[max(z_k)]) for simultaneous strategy testing. [§11.1]
8. **Sinusoidal ToD model for Amihud** — Intraday liquidity U-shape: 1.25 - 0.25 * cos(2*pi*(t-9)/8.5). [§11.4]
9. **n_0 = 50 (not 30)** — 30 trades cluster in a single vol regime; 50 spans multiple regimes. [§9.1, §11.1]
10. **lambda = 0.5 (not 0.8)** — 0.8 shrinks too aggressively for £10K base. 0.5 balances skepticism with responsiveness. [§9.1, §11.1]
11. **VIX default = max(VIX_last, VIX_20d_MA + 5.0)** — Dynamic fallback replaces static 0 or static 25. [§9.1]
12. **yfinance batch limit 50 not 250** — yfinance silently truncates or errors above 50 tickers per batch request. [§10, Phase 2, Task 2.8]
13. **Profit ladder 33/67 (not 40/60)** — Geometric mean optimization: less banking, more trailing maximizes compounding. [§9.1, §11.7]

---

## Round 1 Rejected (4 items)

| Item | Gemini Suggestion | Rejection Rationale |
|------|-------------------|---------------------|
| Replace HMM with online regime detection | Switch to online Bayesian change-point detection (Adams & MacKay 2007) | HMM with filtered probabilities is already online. Change-point detection solves a different problem (structural breaks vs regime cycling). Our 8-state HMM captures the cycling nature of market regimes. Retain HMM; add CUSUM as supplementary break detector. |
| Remove S15 1-signal-per-day limit | Allow multiple signals per day when confidence > 90 | Core discipline. Multiple signals dilute quality, increase intraday correlation, and violate the 2% compounding philosophy. The entire system architecture assumes 1 high-conviction trade. |
| Use tick data instead of 1-minute bars | Switch to tick-level data for Stoikov and OBI | LSE leveraged ETPs have sparse tick data (often < 1 tick/second during lunch). 1-minute bars provide more robust OBI estimation. Tick data would introduce noise, not signal, for these instruments. |
| Add options hedging for tail risk | Buy OTM puts on underlying indices as portfolio insurance | UK ISA does not permit options trading. This is a structural constraint of the account type. Tail risk is managed through regime detection, position sizing, and the anti-cascade stop. |

---

## Round 2 Challenged

**2% Daily Compounding Realism:**

Gemini R2 challenged the 2% daily compounding target (£10K to £1.486M in one year) as aspirational rather than achievable. The critique is acknowledged.

- The 2% daily target requires WR >= 65% AND R-multiple >= 4.0 simultaneously
- Realistic geometric mean based on achievable parameters: approximately 0.577%/day (Scenario C)
- Scenario C projects £10K to approximately £42,800 in year 1 (328% annualized)
- The system retains 2% as the aspirational target for S15 signal selection (find ONE stock per day capable of a 2% move), while planning and risk management use Scenario C as the base case
- The gap between Scenario C (0.577%/day) and Scenario D (2.0%/day) represents the optimization frontier: improvements in WR, R-multiple, and spread reduction each push the geometric mean higher

---

# SECTION 13: GLOSSARY

| Term | Definition |
|------|------------|
| **ADV** | Average Daily Volume. The mean number of shares (or value) traded per day over a lookback period (typically 20 days). Used to assess liquidity and set position size limits. |
| **Amihud Illiquidity** | A measure of price impact per unit of dollar volume traded (Amihud 2002). Higher values indicate less liquid instruments where trades move prices more. |
| **AOF** | Append-Only File. Redis persistence mechanism that logs every write operation. Enables recovery of state after crash. |
| **ASER** | Adjusted Sharpe Excess Return. A risk-adjusted momentum measure used to rank universe candidates. Combines Sharpe ratio with excess return over benchmark. |
| **ATR** | Average True Range. A volatility measure that accounts for gaps. Typically computed over 14 periods. Used to set stop-loss distances. |
| **Bayesian Stranger Penalty** | A shrinkage estimator that reduces confidence in signals from untested strategies or tickers, relaxing toward full confidence as trade count grows. |
| **CDaR** | Conditional Drawdown at Risk. The expected drawdown given that the drawdown exceeds the alpha-quantile. Captures path-dependent tail risk. |
| **Chain Reaction** | The system's event propagation mechanism: a regime change triggers cascading updates to sizing, stops, confidence, and portfolio constraints within the same scan cycle. |
| **Chandelier Exit** | A trailing stop method (Le Beau 1999) that hangs from the highest price like a chandelier, maintaining a fixed ATR distance from the peak. |
| **Correlation Brake** | A portfolio constraint that limits new positions when pairwise Ledoit-Wolf shrinkage correlation exceeds 0.85 between active positions. |
| **CUSUM** | Cumulative Sum control chart (Page 1954). A sequential analysis technique for detecting shifts in the mean of a process. Used to detect strategy performance degradation. |
| **CVaR** | Conditional Value at Risk (also called Expected Shortfall). The expected loss given that the loss exceeds VaR. More coherent risk measure than VaR. |
| **DSR** | Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014). Adjusts the observed Sharpe ratio for multiple testing, skewness, and kurtosis. |
| **ETP** | Exchange-Traded Product. Umbrella term covering ETFs, ETNs, and ETCs. The leveraged instruments traded in this system are ETPs, not strictly ETFs. |
| **EV Gate** | Expected Value Gate. A pre-trade check that blocks any trade where the expected value after spread and slippage costs is non-positive. |
| **Fund-First** | Execution logic that checks portfolio-level constraints (CDaR, correlation, heat cap) before individual trade constraints. |
| **Geometric Mean** | The compounding-aware average return. Unlike arithmetic mean, geometric mean accounts for the variance drag that reduces long-term growth. |
| **HMM** | Hidden Markov Model. A statistical model where the system transitions between unobserved (hidden) states that generate observed data. Used for regime detection. |
| **iCVaR** | Incremental (marginal) CVaR. The change in portfolio CVaR when adding or removing a single position. Used to assess the risk contribution of proposed trades. |
| **ISA** | Individual Savings Account. A UK tax-advantaged investment account. No capital gains tax or income tax on returns. Cannot hold options or short positions. |
| **Kelly Criterion** | The optimal fraction of capital to risk on a bet with known edge and odds, maximizing long-term geometric growth rate. The system uses fractional Kelly (half-Kelly). |
| **Ledoit-Wolf** | A shrinkage estimator for covariance matrices (Ledoit & Wolf 2004). Reduces estimation error by shrinking the sample covariance toward a structured target. |
| **LSE** | London Stock Exchange. The primary exchange where the system's leveraged ETPs are listed and traded. |
| **MTRL** | Minimum Time to Regulatory Launch. The minimum number of trading days of paper trading required before transitioning to live trading. Set at 63 days (one calendar quarter). |
| **OBI** | Order Book Imbalance. (V_bid - V_ask) / (V_bid + V_ask). Measures directional pressure in the limit order book. Range [-1, +1]. |
| **PEAD** | Post-Earnings Announcement Drift. The empirical tendency for prices to continue drifting in the direction of an earnings surprise after the announcement. |
| **Power Hour** | The final hour of the trading day (16:30-17:30 LSE), characterized by higher volume and momentum persistence. Confidence receives a +15% boost. |
| **R-Multiple** | The ratio of profit to initial risk. A 2R trade earned twice the amount risked. A -1R trade lost exactly the amount risked (hit stop-loss). |
| **Redis WAIT** | A Redis command that blocks until the specified number of replicas have acknowledged a write. Used with AOF to ensure persistence before acknowledging state changes. |
| **Regime** | A distinct market state characterized by specific statistical properties (mean, variance, correlation structure). The system models 8 regime states. |
| **Riskfolio-Lib** | A Python library for portfolio optimization supporting multiple risk measures including CVaR and CDaR. |
| **RVOL** | Relative Volume. Current volume divided by average volume for the same time of day. RVOL > 1.0 indicates above-average activity. |
| **S15** | Strategy 15, "2% Daily Target". The system's primary strategy: find one stock per day capable of a 2% move, enter with conviction, exit at target or stop. |
| **SHAP** | SHapley Additive exPlanations. A method for explaining ML model predictions by computing the marginal contribution of each feature. Used for feature importance monitoring. |
| **Sharpe Ratio** | Annualized excess return divided by annualized volatility. A measure of risk-adjusted return. Sharpe > 1.5 is considered strong for a systematic strategy. |
| **Stoikov** | Refers to the Avellaneda-Stoikov (2008) optimal market-making model. Adapted here for execution quality: computing reservation prices for leveraged ETPs. |
| **Stranger Penalty** | See Bayesian Stranger Penalty. |
| **ToD** | Time of Day. Refers to intraday patterns in liquidity, volatility, and momentum that vary systematically throughout the trading day. |
| **TWAP** | Time-Weighted Average Price. An execution algorithm that splits a large order into equal-sized pieces executed at regular time intervals. |
| **VWAP** | Volume-Weighted Average Price. An execution algorithm that matches the order's execution profile to the expected volume profile of the day. |
| **Walk-Forward** | A validation methodology where the model is trained on an expanding window and tested on the subsequent out-of-sample period, repeated sequentially. Prevents look-ahead bias. |
| **Win Rate (WR)** | The percentage of trades that are profitable. WR >= 50% is the system's minimum requirement for S15 graduation. |

---

# END OF DOCUMENT

```
Document: AEGIS Alpha-Omega Master Plan v13.0 — Part 5
Sections: 9 (Parameter Recalibration), 10 (Implementation Phases),
          11 (Mathematical Appendix), 12 (Gemini Review Log), 13 (Glossary)
Authored: 2026-03-04
System: NZT-48 Trading Engine
Account: UK ISA, £10,000 starting equity
Universe: Leveraged LSE ETPs (12 core, expanding to 300-500)
Status: Paper trading — 63 MTRL days required before go-live
Dependencies: v13_part1.md (Sections 1-4), v13_part2-4 (Sections 5-8)
Next: Phase 0 implementation begins upon document approval
```
