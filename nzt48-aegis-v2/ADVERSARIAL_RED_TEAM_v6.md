# AEGIS V2 — ADVERSARIAL RED-TEAM REVIEW v6.0
# Phase 11: Five-Persona Hostile Audit
**Generated:** 2026-03-20 | **Review Period:** Post-N0 Survival Stack Deployment
**Codebase:** 30,137 Rust LOC + 20,175 Python LOC = 50,312 total
**Trade History:** 20 trades (statistically insignificant)
**Prior Red-Team Items:** 371 points processed (103 + 268 from v5.1)
**Evidence Standard:** Code/config references, quantitative reasoning, or cited research

---

## Persona 1: Market Microstructure Expert

*"I trade leveraged ETPs for a living. I know exactly how market makers extract value from retail flow. Let me show you where this engine bleeds money."*

---

### RT-1-01 | CRITICAL | Spread Model Uses Static 0.3% Veto But LSE 3x ETP Spreads Are Time-Variant

**Finding:** The spread veto gate is a single static threshold (`spread_veto_pct = 0.3` in config.toml:72). Real LSE leveraged ETP spreads vary dramatically: 0.08-0.15% during liquid mid-session, 0.5-2.0% during open auction, 1-5% in the last 15 minutes, and 3-10% during low-volume periods. A single threshold either blocks too many liquid opportunities (false negatives) or permits entries during illiquid regimes (false positives).

**Evidence:** `config.toml:72` sets `spread_veto_pct = 0.3`. `risk_arbiter.rs:195` checks `if spread_pct > self.config.spread_veto_pct`. No time-of-day adjustment, no ticker-specific spread profiles, no regime-aware spread thresholds.

**Risk:** At 0.3%, the engine enters trades during moderately illiquid periods where real fill quality is degraded. Conversely, it may reject perfectly clean signals during tight-spread periods if a transient quote widens momentarily. Market makers on LSE leveraged ETPs routinely widen spreads by 3-5x in the last 30 minutes of trading, precisely when momentum signals may fire on closing moves.

**Recommendation:** Implement time-of-day spread multiplier (e.g., 08:00-08:15 = 2x threshold, 15:45-16:30 = 3x threshold, mid-session = 1x). Track per-ticker median spread and use 1.5x median as dynamic veto. Log all spread-veto events for calibration.

**Status:** OPEN

---

### RT-1-02 | CRITICAL | No Fill Quality Model: Paper Fills Are Perfectly At-Mid, Live Will Slip

**Finding:** The engine uses `slippage_assumption_pct = 0.5` (config.toml:73) as a static assumption but does not model actual fill slippage. In paper mode, IBKR fills at or near the limit price. In live trading on LSE leveraged ETPs with 3x leverage, the actual slippage depends on: (a) order size relative to displayed depth, (b) adverse selection from informed flow, (c) quote fading by market makers who detect algorithmic patterns, and (d) latency from EC2 us-east-1 to LSE matching engine (~80ms).

**Evidence:** `python_bridge.rs:206-219` sends ticks to Python, receives signals, but no slippage model exists between signal generation and order submission. `exit_engine.rs:51` uses `round_trip_fee_pct: 0.003` (0.3%) which is reasonable for commission but ignores market impact. WAL `FillEvent` records `spread_at_fill_pct` but this is not fed back into slippage estimation.

**Risk:** Paper trading WR and PF will be systematically inflated relative to live performance. A 79% paper WR at 20 trades may become 50-60% live after accounting for adverse fills. Every basis point of unmodeled slippage on a 3x leveraged ETP costs 3x on the underlying move.

**Recommendation:** Build a slippage model using WAL FillEvent data: `actual_slip = fill_price - mid_at_signal`. Accumulate per-ticker slippage distributions. Gate entries where expected slippage exceeds expected edge. This is the single most important bridge between paper and live performance.

**Status:** OPEN

---

### RT-1-03 | HIGH | Auction Period Handling Blocks Entries But Not Exits During Price Discovery

**Finding:** `clock.rs:128-131` defines auction periods (07:50-08:00 open, 16:30-16:35 close) and the risk arbiter blocks new entries during auctions. However, the auction close period overlaps with LSE closing auction, where indicative prices can shift 1-3% from the last continuous trade. Positions held through the closing auction face uncontrolled price exposure during the uncrossing.

**Evidence:** `clock.rs:AUCTION_CLOSE_START = 16:30, AUCTION_CLOSE_END = 16:35`. The EOD flatten phase 3 (`eod_flatten_phase3 = "16:25"`) submits emergency MTL orders 5 minutes before close. If the MTL doesn't fill by 16:30, the position enters the closing auction with no price control.

**Risk:** A 3x leveraged ETP in the closing auction with a large order imbalance could see 1-2% adverse price movement. At 3x leverage, this is 3-6% on the underlying. On a position-value basis, this is a potential 60-180bps loss per unfilled EOD position.

**Recommendation:** Phase 3 emergency MTL at 16:25 should be a marketable limit (mid + 5 ticks) rather than a true MTL to ensure fill before auction. Add monitoring for unfilled positions entering closing auction. Consider a Phase 2.5 at 16:20 with aggressive limit pricing.

**Status:** OPEN

---

### RT-1-04 | HIGH | Quote Imbalance Invalidation Exists But Threshold Is Uncalibrated

**Finding:** WAL type `QuoteImbalanceInvalidated` (wal.rs:202-207) records instances where quote imbalance caused signal suspension, but the invalidation threshold is not visible in config.toml. Without calibration data on LSE leveraged ETP order book dynamics, this gate may be either too aggressive (blocking valid signals) or too permissive (allowing entries into toxic flow).

**Evidence:** `wal.rs:202-207` defines the event type with `dropped_count` and `resumed_at_ts`. No corresponding threshold in config.toml. No empirical data on what constitutes a "meaningful" quote imbalance for 3x ETPs vs plain equities.

**Risk:** Quote imbalance is fundamentally different for leveraged ETPs because the ETP market maker delta-hedges against the underlying index. An imbalance in the ETP order book may simply reflect lagged hedging, not toxic flow. False invalidation reduces the opportunity set; missed invalidation allows adverse-selection entries.

**Recommendation:** Add configurable `quote_imbalance_ratio_veto` to config.toml. Start at 3:1 (bid:ask or ask:bid ratio). Log all imbalance events with subsequent 5-minute price moves for calibration. Treat leveraged ETP imbalances differently from equity imbalances.

**Status:** OPEN

---

### RT-1-05 | HIGH | VWAP Extension Gate at 1.5% Is Too Generous for 3x Leveraged ETPs

**Finding:** The VWAP extension gate (referenced in IMPLEMENTATION_MASTER_PLAN.md:200, `price 0.3% above VWAP < 1.5%`) permits entries when the current price is up to 1.5% above intraday VWAP. For a 3x leveraged ETP, a 1.5% extension above VWAP represents a 0.5% extension on the underlying, which is actually reasonable for the underlying but creates 4.5% implied move on the leveraged instrument. The mean-reversion pressure at this extension level is significant.

**Evidence:** Bridge.py signal generation checks VWAP extension < 1.5% per the master plan trace. No leverage-adjusted VWAP threshold exists. The Chandelier Rung 2 breakeven lock is at +0.8% (exit_engine.rs:64), so entering at +1.5% VWAP extension means the instrument needs to rally another 0.8% from an already extended level.

**Risk:** Entering a 3x ETP at 1.5% above VWAP means the instrument has already captured most of the intraday momentum. The probability of reaching Rung 2 (+0.8% from entry) drops significantly when the entry itself is already extended. This creates a population of NOISE_EXIT and SPREAD_VICTIM trades.

**Recommendation:** Set leverage-adjusted VWAP extension gate: `vwap_extension_max = 1.5% / leverage_factor`. For 3x ETPs, the effective gate becomes 0.5%. For 5x ETPs, 0.3%. This aligns the entry quality across leverage tiers.

**Status:** OPEN

---

### RT-1-06 | MEDIUM | Spread-to-Range Ratio Not Tracked: Spread May Consume Entire Daily Range

**Finding:** The system tracks absolute spread (bid-ask in percent) but does not track spread relative to the daily range. For an LSE leveraged ETP with a 0.3% spread and a 0.5% daily range, the spread consumes 60% of the tradeable range, making profitable round-trips near-impossible. This metric is critical for selectivity.

**Evidence:** config.toml:72 `spread_veto_pct = 0.3`. No spread-to-range ratio calculation in bridge.py or risk_arbiter.rs. The min_gross_edge_pct = 0.15 (config.toml:81) partially addresses this but does not account for the relationship between spread and ATR/range.

**Risk:** On low-volatility days, the spread may consume 40-80% of the available range, making every trade a de facto spread victim regardless of signal quality. The current gates do not detect this condition.

**Recommendation:** Add `spread_to_atr_ratio_max = 0.25` gate. If spread > 25% of 5-min ATR, reject the signal. This dynamically adapts to volatility regime changes.

**Status:** OPEN

---

### RT-1-07 | MEDIUM | No Market Impact Model for Position Sizes Relative to Average Daily Volume

**Finding:** The system sizes positions using Kelly criterion and confidence scaling but does not check whether the intended position size represents a significant fraction of the instrument's average daily volume (ADV). For thinly-traded LSE leveraged ETPs, a single order may represent 5-20% of ADV, causing market impact and signaling to other participants.

**Evidence:** `position_sizer.rs` calculates Kelly sizing without ADV check. contracts.toml has 49 LSE ETPs but no ADV data. Some smaller ETPs (e.g., 3LBA.L, MU2.L) may have daily volumes under 100,000 shares.

**Risk:** A market order for 10% of ADV in a thinly-traded 3x ETP will move the price against the entry by 20-50bps. The exit order will face similar impact. Total market impact could exceed the Chandelier Rung 2 breakeven threshold.

**Recommendation:** Add ADV data to contracts.toml or fetch via IBKR historical data. Gate: reject entries where `order_size > max_adv_fraction * ADV_20d`. Start at `max_adv_fraction = 0.02` (2% of ADV).

**Status:** OPEN

---

### RT-1-08 | MEDIUM | Price Spike Filter at 5% Deviation May Miss Leveraged ETP Intraday Moves

**Finding:** `config.toml:10` sets `erroneous_tick_deviation_pct = 5.0` to detect bad ticks. However, 3x leveraged ETPs routinely move 3-5% intraday on normal trading days (S&P 500 1-2% move = 3-6% on 3x ETP). During volatile sessions, 5-8% intraday moves are not erroneous. A 5% filter may suppress genuine high-volatility signals or, worse, allow truly erroneous ticks below the threshold.

**Evidence:** `config.toml:10 erroneous_tick_deviation_pct = 5.0`. No leverage-adjusted tick filter. The VIX-tiered response (config.toml:86-91) handles portfolio-level risk but not tick-level filtering.

**Risk:** During a VIX spike event, a 3x ETP may move 5% in 10 minutes legitimately. The erroneous tick filter would suppress these ticks, blinding the engine to genuine price action during the most critical market moments.

**Recommendation:** Set leverage-adjusted erroneous tick filter: `erroneous_tick_deviation_pct = 5.0 * leverage_factor / 3.0`. For 3x ETPs, effective threshold = 5%. For 5x ETPs, ~8.3%. For 1x equities, ~1.7%. Consider a rolling EWMA deviation model instead of a static threshold.

**Status:** OPEN

---

## Persona 2: Quantitative Strategist

*"I have published papers on momentum factor decay, volatility targeting, and Kelly criterion misapplication. This system has interesting architecture but dangerous quantitative assumptions."*

---

### RT-2-01 | CRITICAL | VanguardSniper Has Zero Backtest: Expected Value Is Unknown

**Finding:** The primary signal generation strategy (VanguardSniper) has never been backtested against historical data. All 20 trades were generated live in paper mode with no counterfactual analysis. The PROOF_REGISTER classifies this as SP-01 ("SPECULATIVE: Zero backtest"). Operating a compounding machine on an unvalidated strategy is quantitative malpractice.

**Evidence:** PROOF_REGISTER.md SP-01: "VanguardSniper has positive expectancy — Momentum + volume + ADX + Hurst — Zero backtest." EXECUTION_BACKLOG.md N7a: "Top-100 ticker backfill" (3 days, not started). V3: "VanguardSniper backtest — N7a complete — N7a" (deferred to 100+ trades).

**Risk:** The strategy may have negative expected value after costs. The 79% WR on 20 trades has a 95% CI of [55%, 94%] (binomial). At the lower bound, the strategy is a marginal coin-flip. At 55% WR with 1:1 reward-risk ratio and 0.3% round-trip costs, the net expectancy is approximately -0.05% per trade. Three trades per day = -0.15% daily = -30% annual drain.

**Recommendation:** Immediate priority: execute backtest using N7a historical data backfill. Even a crude 12-month backtest on QQQ3.L with the current gate configuration will reveal whether the strategy has structural edge. Halt all paper trading interpretation until backtest confirms positive expectancy.

**Status:** OPEN

---

### RT-2-02 | CRITICAL | Kelly Sizing With Leverage Drag: volatility_drag_3x = 9 Is Unsourced

**Finding:** `config.toml:29` sets `volatility_drag_3x = 9` and `volatility_drag_5x = 25`. These constants represent the leverage drag multiplier (variance drain on leveraged returns) but the values appear to be theoretical approximations. For a 3x ETP, the daily variance drain is `leverage^2 * sigma^2 / 2`. With 20% annualized vol on the underlying, this is `9 * 0.04 / 2 = 18%` annual drag. However, the actual drag depends on path-dependency and rebalancing frequency, which varies by ETP issuer.

**Evidence:** `config.toml:29-30`. `position_sizer.rs` uses these in Kelly calculation (line 59). No citation for the specific values. The `volatility_drag_3x = 9` is mathematically correct for leverage^2 but does not account for the actual rebalancing methodology (daily vs intraday), financing costs, or swap spreads.

**Risk:** If the Kelly calculator under-estimates leverage drag, position sizes will be too large. If it over-estimates, the opportunity set shrinks. For a 3x ETP with 30% annualized vol (typical for tech-heavy leveraged products), the actual annual drag is `9 * 0.09 / 2 = 40.5%`. This means the ETP loses 40.5% of its value annually from variance drain alone, independent of direction. Kelly must account for this as a structural headwind.

**Recommendation:** Validate drag constants empirically by comparing 3x ETP total return vs 3x underlying return over 1-year periods. Adjust Kelly fraction by subtracting realized drag from expected return. Consider a daily drag estimate: `daily_drag = leverage^2 * daily_vol^2 / 2` and deduct from per-trade expected return.

**Status:** OPEN

---

### RT-2-03 | HIGH | Hurst Exponent on 200 5-Second Bars (~17 Minutes): Statistically Unreliable

**Finding:** The Hurst exponent is computed on 200 bars of 5-second data (approximately 17 minutes of market activity). Academic literature (Mandelbrot & Wallis 1969, Lo 1991) establishes that Hurst exponent estimation requires minimum 2,000-5,000 observations for reliable estimates, with R/S analysis particularly sensitive to short-sample bias. At n=200, the standard error of the Hurst estimate is approximately 0.15, meaning a true Hurst of 0.50 (random walk) could register anywhere from 0.35 to 0.65.

**Evidence:** IMPLEMENTATION_MASTER_PLAN.md:199 shows `Hurst=0.58 (trending)` computed from 200 ticks. Bridge.py gate: `hurst > 0.50`. With standard error of ~0.15, a H=0.58 estimate does not significantly differ from H=0.50 (random walk) at any conventional significance level.

**Risk:** The Hurst gate may be doing nothing useful: passing random signals and blocking random signals with roughly equal probability. Worse, on 3x leveraged ETPs, the auto-correlation structure of the ETP may differ from the underlying due to daily rebalancing, making the Hurst estimate of the ETP systematically biased.

**Recommendation:** Either increase the Hurst computation window to 1,000+ bars (using historical data fill from Redis persistence) or replace with a more robust regime detector. Alternative: use ADX alone as the trend filter (more stable on short samples) and demote Hurst to a secondary confidence modifier.

**Status:** OPEN

---

### RT-2-04 | HIGH | ADX Threshold Not Leverage-Adjusted: 3x ETP ADX Reads Differently

**Finding:** ADX(14) is computed on the ETP price series, not the underlying. A 3x leveraged ETP will show systematically higher ADX values than the underlying because the leverage amplifies directional moves. An ADX of 25 on the underlying may show as ADX 35-45 on the 3x ETP. The ADX gate in bridge.py does not adjust for this leverage amplification.

**Evidence:** IMPLEMENTATION_MASTER_PLAN.md:199: `ADX(14)=32`. The standard Wilder ADX interpretation (>25 = trending) was calibrated on equities and commodities, not leveraged derivatives. No leverage adjustment in the signal generation code.

**Risk:** The ADX gate may be systematically too permissive for leveraged ETPs, allowing entries during what is actually a weak trend on the underlying. This creates false momentum signals that are really just leverage-amplified noise.

**Recommendation:** Compute ADX on the underlying index (or implied underlying from ETP price / leverage factor) rather than on the ETP price directly. Alternatively, raise ADX thresholds proportionally: `adx_threshold = base_threshold * sqrt(leverage_factor)`. For 3x: 25 * 1.73 = 43.

**Status:** OPEN

---

### RT-2-05 | HIGH | Multi-Timeframe Gate Eliminates 40%+ of Signals With No Counterfactual

**Finding:** The multi-timeframe (MTF) alignment gate suppresses approximately 40% of signals (IMPLEMENTATION_MASTER_PLAN.md:97, PROOF_REGISTER LK-04). However, there is no analysis of whether the suppressed signals would have been winners or losers. If the MTF gate randomly eliminates 40% of both winners and losers, it reduces trade count (increasing cost per trade due to fixed infrastructure costs) without improving net expectancy.

**Evidence:** PROOF_REGISTER.md LK-04: "Gate vetoes prevent bad trades — 40%+ rejection rate — No missed-winner analysis." EXECUTION_BACKLOG.md N2c: "MissedWinnerCandidate WAL event" (1 day, not started). WAL type `MissedWinnerCandidate` exists in wal.rs:273-287 but is not yet being written.

**Risk:** The MTF gate may be a net negative: reducing the opportunity set without improving hit rate. At 3 trades/day maximum, losing 40% of signals to a potentially useless gate could mean the engine takes 1.8 high-quality signals per day instead of 3, leaving money on the table.

**Recommendation:** Implement N2c (MissedWinnerCandidate) immediately and track for 50+ rejections. Compare hypothetical PnL of rejected signals vs taken signals. If the gate does not improve WR by at least 10 percentage points, consider relaxing or removing it.

**Status:** OPEN

---

### RT-2-06 | HIGH | Trade Taxonomy Classifier Has Hardcoded Thresholds Without Empirical Basis

**Finding:** `trade_taxonomy.py` uses hardcoded thresholds for trade classification: MAE < 20% of MFE for CLEAN_TREND (line 119), MAE > 50% of MFE for GRIND_WINNER (line 124), hold_mins < 25 for SPIKE_WINNER (line 115), loss < 2x spread for SPREAD_VICTIM (line 131). These thresholds are reasonable heuristics but have no empirical calibration from actual trade data.

**Evidence:** `trade_taxonomy.py:105-148`. All thresholds are magic numbers. With only 20 trades, no calibration is possible. The classifier was designed before sufficient data existed to validate it.

**Risk:** Misclassification leads to incorrect Ouroboros learning signals. If a THESIS_FAILURE is misclassified as a NOISE_EXIT, the learning system applies the wrong corrective action (tighten stops vs change entry criteria). Garbage classification in, garbage learning out.

**Recommendation:** Accept this as a known limitation during the first 100 trades. After 100 trades, perform manual classification of all trades and compare against automated classification. Adjust thresholds based on actual distributions. Add a MANUAL_OVERRIDE field for operator corrections.

**Status:** OPEN

---

### RT-2-07 | MEDIUM | Alpha Decay Detection Relies on Ouroboros With n=20: No Statistical Power

**Finding:** The Ouroboros learning system is designed to detect alpha decay (strategies losing edge over time) and adjust weights via dynamic_weights.toml. With only 20 trades, the system has zero statistical power to detect decay. A strategy could degrade from 70% WR to 40% WR and the change would not be statistically significant until n > 80 (chi-squared test at p=0.05).

**Evidence:** PROOF_REGISTER.md LK-01: "Ouroboros improves performance — n=20 too small." Ouroboros nightly loop runs daily but cannot meaningfully learn from insufficient data.

**Risk:** The system may be over-fitting to noise in the first 20 trades, amplifying spurious patterns. Ouroboros confidence adjustments based on n=20 are essentially random perturbations to the strategy configuration.

**Recommendation:** Implement a minimum-sample gate in Ouroboros: do not adjust strategy weights until n >= 50 per strategy. Before that threshold, use static default weights. Log proposed adjustments without applying them for post-hoc analysis.

**Status:** OPEN

---

### RT-2-08 | MEDIUM | Structural Tradability Score Not Yet Implemented: Selectivity Gap

**Finding:** EXECUTION_BACKLOG N3a defines a "Structural tradability score" as a P1 item. This score would combine spread, volume, volatility, and regime into a single tradability metric. Without it, the engine relies on individual gates that can pass a signal which is individually acceptable on each gate but collectively marginal (e.g., spread at 0.29%, ADX at 26, Hurst at 0.51 — each barely passing).

**Evidence:** EXECUTION_BACKLOG.md N3a: "Structural tradability score — 1 day — N1b — Bridge" (P1, not started).

**Risk:** The conjunction of marginally-passing gates produces low-quality entries. A composite score would reject signals that pass each gate by a small margin but are collectively below threshold.

**Recommendation:** Implement N3a as a weighted geometric mean of normalized gate scores. Any individual gate below 60th percentile pulls the composite below threshold, even if all gates technically pass.

**Status:** OPEN

---

## Persona 3: Fund Manager (Risk & Returns)

*"I manage a $500M fund. I know what 0.3-0.5% daily returns actually requires. Let me tell you why this system's return expectations need a reality check."*

---

### RT-3-01 | CRITICAL | PAPER VALIDATION Overrides Are Ticking Time Bombs: 15 Positions at 10K

**Finding:** Six PAPER VALIDATION overrides in config.toml allow behavior that would be catastrophic in live trading: `max_simultaneous_positions = 15` (should be 3), `portfolio_heat_limit_pct = 50.0` (should be 10.0), `sector_heat_cap_pct = 80.0` (should be 33.0), `cash_buffer_pct = 5.0` (should be 25.0), and `max_positions_override = 15` (should be 3). While config.live.toml exists, the overlay mechanism is NOT implemented in code.

**Evidence:** config.toml lines 18-22, 181. config.live.toml exists with correct values but main.rs:52-65 only checks for file existence, does not load or overlay values. The IS_LIVE flag blocks live trading, but the paper trading data is generated under unrealistic position/risk parameters, making the paper results non-transferable to live.

**Risk:** Paper trading at 15 positions with 50% heat at 10K equity means average position is 667 GBP. At 3x leverage, each position controls 2,000 GBP notional. 15 positions = 30,000 GBP notional on 10,000 GBP equity = 3x portfolio leverage before ETP leverage = 9x effective leverage. Any correlated drawdown will be catastrophic. More importantly, the paper trading statistics (WR, PF, DD) generated under these conditions are INVALID for predicting live performance at 3 positions.

**Recommendation:** Immediately reduce paper validation to match live parameters. The purpose of paper trading is to validate performance under LIVE conditions, not to collect data under impossible conditions. Set paper max_positions = 3, heat = 10%, sector_heat = 33%, cash_buffer = 25%. The Ouroboros learning data collected under 15-position mode is contaminated and should be discarded when live parameters are applied.

**Status:** OPEN

---

### RT-3-02 | CRITICAL | 0.3-0.5% Daily Target Requires Exceptional Performance Not Seen In Any Retail System

**Finding:** The stated target of 0.3-0.5% daily net return (IMPLEMENTATION_MASTER_PLAN.md:91, PROOF_REGISTER SP-03: "30-50% annual return achievable") requires a combination of win rate, payoff ratio, and trade frequency that has not been demonstrated by any retail algorithmic trading system operating on leveraged ETPs. Renaissance Technologies achieves ~66% annual but with billions in capital, proprietary data feeds, co-located infrastructure, and hundreds of PhDs.

**Evidence:** IMPLEMENTATION_MASTER_PLAN.md Phase 1: "0.3-0.5% daily net — realistic, world-class." At 0.3% daily compounding over 252 trading days: (1.003)^252 = 2.13x = 113% annual. At 0.5% daily: (1.005)^252 = 3.51x = 251% annual. The system operates from EC2 us-east-1 (~80ms to LSE), uses free IBKR market data (no Level 2 depth), and has a 10K starting equity.

**Risk:** Unrealistic return targets lead to dangerous parameter tuning: widening gates, increasing position sizes, and accepting marginal signals to hit the daily target. This is the textbook path to blowup. The daily_target.py (S15 strategy) was already identified as having 0% win rate across 52 trades in the original V1 system.

**Recommendation:** Reset return expectations to 0.05-0.15% daily net (12-45% annual), which is ambitious but achievable for a systematic momentum strategy on leveraged ETPs. Remove any daily target logic. The system should maximize risk-adjusted returns per trade, not chase a daily PnL number.

**Status:** OPEN

---

### RT-3-03 | HIGH | Maximum Drawdown Scenario: Correlated 3x ETP Crash Can Wipe 50%+ in One Day

**Finding:** The `daily_drawdown_pct = 4.0` and `peak_drawdown_halt_pct = 15.0` guardrails exist but may be insufficient during a flash crash or correlated sell-off. With 3 positions (live config) in correlated 3x tech ETPs (QQQ3.L, NVD3.L, GPT3.L), a 3% drop in NASDAQ = 9% drop in each 3x ETP. Three positions at max size (3.3K each) losing 9% = 2,970 GBP loss on 10,000 equity = 29.7% drawdown. The 4% daily halt would trigger after the first position (4% * 10K = 400 GBP) but the HALT may not prevent fill of already-submitted orders for positions 2 and 3.

**Evidence:** config.toml:65-68: `daily_drawdown_pct = 4.0, peak_drawdown_halt_pct = 15.0, equity_floor_pct = 70.0`. Sectors config (config.toml:147-148) groups QQQ3.L, NVD3.L, GPT3.L in Technology and Semiconductors — different sectors despite high correlation. `max_correlated_positions = 3` (config.toml:70) but correlation is not dynamically computed.

**Risk:** The sector heat cap treats Technology and Semiconductors as separate sectors, allowing 33% heat in each. In reality, QQQ3.L and NVD3.L have >0.95 correlation. A "diversified" portfolio of QQQ3.L + NVD3.L + TSM3.L is actually a single concentrated bet on US tech/semis. The `max_correlated_positions = 3` check requires real-time correlation computation, which is listed but not verified as operational.

**Recommendation:** Merge Technology and Semiconductors into a single "Tech/Semis" sector for heat cap purposes. Implement real-time 20-day rolling correlation using ticker return data. Any pair with correlation > 0.80 should count as a single position for heat cap purposes. Reduce max_correlated_positions to 2.

**Status:** OPEN

---

### RT-3-04 | HIGH | Position Sizing at 10K Equity: Minimum Viable Position May Exceed Kelly Recommendation

**Finding:** At 10,000 GBP equity with 3 max positions and 25% cash buffer (live config), the deployable capital is 7,500 GBP across 3 positions = 2,500 GBP per position. For a 3x ETP priced at 50-200 GBP per share, the minimum lot size is 1 share. If Kelly recommends 2% of equity (200 GBP) but minimum position is 1 share at 150 GBP, the actual position is 150/10000 = 1.5% of equity — acceptable. But if Kelly recommends 0.5% (50 GBP) and minimum share price is 150 GBP, the system must either skip the trade or take a position 3x larger than Kelly recommends.

**Evidence:** `position_sizer.rs:296-299`: shares = `account_equity * max_pct * kelly_fraction / price`. At Kelly 0.02, equity 10K, and price 150: shares = 10000 * 0.20 * 0.02 / 150 = 0.27 shares. Rounds to 0 (skip) or 1 (3x over-Kelly). config.toml:28: `kelly.clamp_max = 0.20`.

**Risk:** The quantization error on small accounts forces binary decisions: either skip (missing good trades) or take oversized positions (exceeding Kelly risk). This is a fundamental problem for any sub-50K account trading instruments priced above 50 GBP/share.

**Recommendation:** Add a `min_position_shares` floor (default 1) and a `max_kelly_overshoot_pct` threshold (e.g., 200%). If the minimum position exceeds Kelly by more than 200%, skip the trade. Log these skips for analysis. Consider fractional share brokers for live deployment (IBKR does not support fractional shares on LSE ETPs).

**Status:** OPEN

---

### RT-3-05 | HIGH | ISA 20K Annual Limit Creates Capacity Ceiling That Conflicts With Compounding

**Finding:** The ISA annual contribution limit is 20,000 GBP (config.toml:23). Starting at 10,000 GBP with a 50% annual return target, the account reaches 15,000 GBP by year-end. In year 2, the contribution is another 20,000 GBP, bringing the account to 35,000 GBP. By year 3 at 50% annual, the account is at 52,500 GBP. But the ISA limit means you can only add 20,000/year, not compound externally. At account sizes above 100K, the leverage impact on LSE 3x ETPs becomes material (order sizes start moving prices). The system does not model this capacity constraint.

**Evidence:** config.toml:23-24: `isa_annual_limit_gbp = 20000, isa_tax_year_start = "04-06"`. No capacity modeling anywhere in the codebase.

**Risk:** The system's entire value proposition is tax-free compounding within the ISA wrapper. But the compounding math assumes unlimited capacity. In practice, the system's strategy may stop working at 50-100K due to market impact on thinly-traded LSE ETPs. The ceiling is lower than the compounding curve suggests.

**Recommendation:** Model capacity constraints: compute max_position_size as min(Kelly, ADV * 2%). Estimate at what account size the system can no longer deploy capital efficiently. If the capacity ceiling is below 50K, the long-term compounding thesis is weaker than assumed.

**Status:** OPEN

---

### RT-3-06 | HIGH | Chandelier Rung Thresholds Optimized Theoretically, Not Empirically

**Finding:** The Chandelier rung thresholds were restructured in an audit (exit_engine.rs:58-64) from [0, 2%, 4%, 6%, 8%] to [0, 0.8%, 1.5%, 2.5%, 4.0%] based on theoretical reasoning about compounding frequency. The auditor's quote: "Rung spacing too wide. A system that wins 60% at +1.2% compounds faster than one that wins 50% at +2.0%." This is mathematically true but the TIGHTER rungs also mean tighter trailing stops, which increases the probability of being stopped out by noise before reaching higher rungs.

**Evidence:** `exit_engine.rs:57-73`. PROOF_REGISTER LK-02: "Chandelier rungs capture compounding — No empirical validation." EXECUTION_BACKLOG V4: "Chandelier rung threshold optimization — 100+ trades" (deferred).

**Risk:** The tighter rungs may increase Rung 2 attainment (breakeven lock) but reduce Rung 3+ attainment (profit capture). If the average winner only reaches Rung 2-3, the average win is 0.8-1.5%, which after 0.3% round-trip costs leaves 0.5-1.2% net. At 60% WR, the net expectancy is 0.60 * 0.85% - 0.40 * 1.5% = 0.51% - 0.60% = -0.09% per trade. The tighter rungs may have moved the system from positive to negative expectancy.

**Recommendation:** Run the rung optimization analysis as soon as 50 trades are available. Compare: (a) current tight rungs, (b) original wide rungs, (c) intermediate rungs. Optimize for maximum net expectancy, not maximum Rung 2 attainment.

**Status:** OPEN

---

### RT-3-07 | MEDIUM | Cost Drag on 10K Account: Fixed Costs Consume Disproportionate Returns

**Finding:** Fixed costs include: IBKR minimum monthly activity fee (0 for IBKR Lite, 10 USD for Pro), market data subscriptions (0 with basic bundle), and platform fees. Variable costs include: commission (0.05% typical on LSE), stamp duty (0% for ETPs), spread cost (0.1-0.3%). At 3 trades/day with 0.3% round-trip cost, annual cost = 3 * 0.003 * avg_position * 252 = 2,268 * avg_position_fraction. On 10K equity with 2.5K average position: annual cost = 3 * 2500 * 0.003 * 252 = 5,670 GBP = 56.7% of starting equity.

**Evidence:** config.toml:77 `max_daily_trades = 3`. config.toml:72 `spread_veto_pct = 0.3`. exit_engine.rs:73 `round_trip_fee_pct: 0.003`.

**Risk:** Annual trading costs of 56.7% on starting equity means the strategy must generate 56.7% gross returns just to break even. This is an extraordinarily high hurdle rate. Even at 2 trades/day, the cost is 37.8% of equity.

**Recommendation:** Reduce max_daily_trades to 2 in the near term. Focus on trade selectivity rather than frequency. Track cost-per-trade in daily reports. Consider the trade-off: is the marginal 3rd trade per day generating enough alpha to cover its 0.3% round-trip cost?

**Status:** OPEN

---

### RT-3-08 | MEDIUM | Risk of Ruin Calculation: Monte Carlo Not Performed

**Finding:** No risk-of-ruin (RoR) calculation exists anywhere in the codebase. For a small account (10K) trading leveraged instruments with a trailing stop system, the standard RoR formula is: `RoR = ((1-edge)/(1+edge))^(capital_units)`. Without knowing the true edge (unvalidated strategy), the RoR cannot be computed. But even with the stated assumptions (60% WR, 1.2:1 payoff), the RoR at 2% risk per trade with 5 consecutive loss halt is approximately 0.8^5 * recovery_probability.

**Evidence:** No Monte Carlo simulation in the codebase. No RoR calculation. The equity_floor_pct = 70.0 (config.toml:68) acts as a hard floor but is checked reactively, not proactively.

**Risk:** Without RoR analysis, the operator does not know the probability of hitting the 70% equity floor within any given time period. If the true WR is 50% (lower bound of CI), the probability of hitting 5 consecutive losses is 3.1% per 5-trade window, occurring approximately once every 32 trading days.

**Recommendation:** Build a Monte Carlo simulator using current parameters (WR, avg_win, avg_loss, max_positions, costs). Run 10,000 paths over 252 days. Report: median return, 5th percentile return, probability of hitting 70% floor, probability of hitting 50% floor. Use this to set appropriate position sizing.

**Status:** OPEN

---

## Persona 4: Risk & Governance Officer

*"I enforce regulatory compliance, operational risk controls, and governance frameworks. This system has architectural maturity but critical governance gaps."*

---

### RT-4-01 | CRITICAL | IS_LIVE Flag Governance: config.live.toml Overlay Not Implemented In Code

**Finding:** The IS_LIVE flag is hardcoded to false in main.rs:29 with an exit(1) guard. config.live.toml exists with production-safe values. However, the actual overlay mechanism (load config.toml, then overlay config.live.toml when IS_LIVE=true) is NOT implemented. main.rs:52-65 only checks for file existence. When the time comes to go live, a developer must manually implement the overlay, test it, and deploy — under pressure to start trading. This is the exact moment when mistakes happen.

**Evidence:** `main.rs:29: const IS_LIVE: bool = false`. `main.rs:52-65`: only checks `live_config_path.exists()`, does not load or parse the file. config.live.toml has different key names than config.toml (e.g., `daily_trade_limit` vs `max_daily_trades`, `min_edge_bps` vs `min_gross_edge_pct`). These naming inconsistencies will cause silent config failures.

**Risk:** Key name mismatches between config.toml and config.live.toml mean the overlay will silently fail to override critical parameters. The developer may flip IS_LIVE=true believing the live config is active, but the engine runs with paper validation parameters (15 positions, 50% heat) on real money.

**Recommendation:** (1) Standardize key names between config.toml and config.live.toml. (2) Implement and TEST the overlay mechanism now, during paper trading. (3) Add a startup assertion that validates all PAPER VALIDATION parameters are overridden when IS_LIVE=true. (4) The overlay test should be part of the CI pipeline, run on every commit.

**Status:** OPEN

---

### RT-4-02 | CRITICAL | API Keys Hardcoded in Shell Scripts: Polygon Key Visible in Plaintext

**Finding:** THE_MASTER_COMMAND.sh contains the Polygon API key in plaintext on line 15 (`POLYGON_API_KEY="[REDACTED - see .env]"`) and EXECUTE_FULL_PLAN.sh line 40 repeats the same key. AEGIS_COMPLETE_EXECUTION.sh also contains the key. While PR-12 confirms `.env` is never committed to git, the API keys are hardcoded directly in committed shell scripts.

**Evidence:** `THE_MASTER_COMMAND.sh:15`, `AEGIS_COMPLETE_EXECUTION.sh:40`, `EXECUTE_FULL_PLAN.sh:28`. These are committed to git (they are tracked in the repository). The Polygon API key provides access to market data APIs and could be used to consume API credits or exfiltrate data.

**Risk:** Anyone with read access to the git repository can extract the Polygon API key. If the repository is public or shared, the key is compromised. The key grants access to Polygon.io's API, which could be used for unauthorized data access or credit consumption.

**Recommendation:** (1) Immediately rotate the Polygon API key. (2) Remove all hardcoded keys from shell scripts. (3) Use environment variables exclusively, loaded from `.env` files that are gitignored. (4) Add a pre-commit hook that scans for API key patterns (`[A-Za-z0-9]{32}` adjacent to `API_KEY`). (5) Audit git history for any other committed secrets.

**Status:** OPEN

---

### RT-4-03 | HIGH | WAL Crash Recovery: Replay After Partial FillEvent Write Is Undefined

**Finding:** The WAL writer uses fsync + CRC32 for integrity (PR-04). However, the crash recovery scenario where the engine crashes BETWEEN writing a FillEvent and updating the portfolio state is not explicitly tested. The WalEvent has CRC32 on the payload, but if the process crashes after the WAL write succeeds but before the in-memory portfolio updates, the replayed state may include a fill that was never reflected in position tracking.

**Evidence:** `wal.rs:14-28` WalEvent structure with CRC32. `wal_replay.rs:207` replays FillEvent but destructures `spread_at_fill_pct: _` (ignores spread). The replay logic must reconstruct the exact portfolio state from the WAL event sequence, but the FillEvent replay path was not fully auditable from the first 100 lines.

**Risk:** After a crash, the replayed portfolio may show a position that was never properly initialized (entry_price, qty, stop_price not set correctly). The reconciliation check (every 5 min) would catch this IF the broker reports the position, but there's a window between replay and first reconciliation where the engine operates on corrupted state.

**Recommendation:** Add an integration test: write a FillEvent to WAL, crash (kill process), replay, and verify portfolio state matches expected. Test both partial-fill and full-fill scenarios. Ensure reconciliation runs IMMEDIATELY after WAL replay, not after 5 minutes.

**Status:** OPEN

---

### RT-4-04 | HIGH | Python Bridge Single Point of Failure: No Health Check, No Timeout Enforcement

**Finding:** The Python bridge (`python_bridge.rs:164-202`) spawns a subprocess and communicates via stdin/stdout JSON. There is a `consecutive_errors` counter (line 173) for crash detection, but no explicit health check endpoint, no periodic heartbeat, and no hard timeout on individual tick evaluation. If Python enters an infinite loop or deadlock, the Rust engine will block indefinitely on `reader.read_line()`.

**Evidence:** `python_bridge.rs:164-202`. The subprocess has no timeout on read operations. `consecutive_errors` tracks errors but relies on the Python process actually responding with an error message. A frozen Python process produces no response at all.

**Risk:** A Python deadlock (e.g., in numpy, pandas, or any C extension) will freeze the entire engine. No new signals will be generated, no exits will be processed, and open positions will be unmanaged. The reconciliation loop may not detect this because it checks broker state, not bridge health.

**Recommendation:** (1) Add a 500ms timeout on `reader.read_line()` using `std::time::timeout` or non-blocking I/O. (2) If the bridge fails to respond within timeout, log a CRITICAL alert, kill the subprocess, and respawn. (3) Add a `/health` JSON command sent every 30 seconds to verify Python is responsive. (4) During bridge-down periods, the exit engine must still function (it's Rust-native).

**Status:** OPEN

---

### RT-4-05 | HIGH | Redis Dependency: Single Instance, No Persistence Verification

**Finding:** Redis is used for state caching (bar history, Chandelier rung state) but runs as a single Docker container (`aegis-redis`) with no replication, no persistence verification, and password `nzt48redis` (from memory context). If Redis crashes or loses data, the engine loses bar history (16-min warmup required) and any Redis-persisted state.

**Evidence:** docker-compose.yml references aegis-redis container. Redis password is documented in project memory. No Redis persistence configuration (AOF/RDB) visible in the files reviewed. No Redis health check in the engine startup sequence.

**Risk:** Redis data loss during market hours means: (a) 16-minute warmup blackout with no new signals, (b) loss of any Redis-persisted state (if bar history persistence N5b is implemented), (c) potential inconsistency between WAL state and Redis state. Combined with the Python bridge dependency, a Redis crash during volatile market conditions could leave positions unmanaged.

**Recommendation:** (1) Configure Redis with AOF persistence (appendonly yes, appendfsync everysec). (2) Add Redis health check to engine startup. (3) Implement Redis reconnection logic with exponential backoff. (4) The engine must degrade gracefully when Redis is unavailable: continue operating with WAL state only, accept the 16-min warmup, and log the degradation.

**Status:** OPEN

---

### RT-4-06 | HIGH | Reconciliation Divergence: HALT Triggered But No Automated Recovery Path

**Finding:** The reconciler (reconciler.rs) compares broker positions vs local portfolio every 5 minutes. On mismatch, it triggers a CRITICAL log and the ReconcileAuditLog enforces a 24-hour lock period requiring `manual_clear_halt`. This is appropriately conservative, but there is no automated notification to the operator. A HALT during market hours with no operator awareness means positions are locked but the market continues moving.

**Evidence:** `reconciler.rs:44-66`: ReconcileAuditLog with 24h LOCK_PERIOD_NS. Manual clear required. No Telegram/email alert on reconciliation failure. The wal_watcher provides Telegram notifications for WAL events but reconciliation HALT is a state change, not necessarily a WAL event.

**Risk:** If reconciliation fails at 10:00 and the operator checks at 16:00, six hours of unmanaged exposure have passed. The HALT prevents new entries but existing positions are still subject to market risk with no exit processing.

**Recommendation:** (1) Write a ReconciliationDivergence WAL event (already defined in wal.rs:219-223) on every mismatch. (2) Ensure wal_watcher sends IMMEDIATE Telegram alert for this event type. (3) Add an SMS/phone call escalation if no operator response within 30 minutes. (4) Consider auto-flatten after 2 hours of uncleared HALT as a last resort.

**Status:** OPEN

---

### RT-4-07 | MEDIUM | Audit Trail Completeness: No Log Rotation, No Retention Policy

**Finding:** WAL events are written to `events/YYYY-MM-DD.ndjson` (daily files). Gate vetoes go to `gate_vetoes.ndjson`. There is no log rotation policy, no retention limit, and no archival process documented. On the EC2 instance with 19GB disk, WAL files will eventually consume available space.

**Evidence:** WAL files are ndjson format, one per day. EC2 has 19GB total. Docker builds consume ~5GB. With WAL events + gate vetoes + Python logs, disk consumption could reach critical levels within months.

**Risk:** Disk full condition causes: WAL writer failure (fsync fails), engine HALT (WAL unavailable = fail-closed), and potential data corruption if partial writes occur during disk-full. The engine would enter an unrecoverable state.

**Recommendation:** (1) Implement WAL archival: compress daily files older than 30 days to .gz. (2) Add disk space monitoring to supercronic crontab. (3) Alert at 80% disk usage. (4) Archive to S3 daily (scripts/backup_to_s3.sh exists but verify it runs and covers WAL files).

**Status:** OPEN

---

### RT-4-08 | MEDIUM | No Config Change Audit Trail: Dynamic Weights Changes Are Untracked

**Finding:** The config_writer generates dynamic_weights.toml based on Ouroboros nightly analysis. The engine hot-reloads via SIGHUP. But there is no audit trail of what changed between dynamic_weights versions. If Ouroboros makes a bad adjustment that causes losses, there is no rollback mechanism and no record of the previous configuration.

**Evidence:** EXECUTION_BACKLOG N7b: "Config diff rollback ledger — 1 day — Governance" (P1, not started). config_writer.py runs at 04:51 UTC via crontab. dynamic_weights.toml is overwritten in-place.

**Risk:** A bad Ouroboros adjustment (e.g., removing a critical gate or over-weighting a failing ticker) persists until the next nightly run. If the bad config causes losses during the trading day, the operator has no way to quickly identify and revert the specific change.

**Recommendation:** (1) Before writing new dynamic_weights.toml, copy the current file to dynamic_weights.YYYY-MM-DD.bak. (2) Log a diff of old vs new to a dedicated config_changes.log. (3) Implement a one-command rollback: `python config_writer.py --rollback` that restores the previous day's config.

**Status:** OPEN

---

### RT-4-09 | MEDIUM | Bridge SIGHUP Not Implemented: Engine Reloads But Python Does Not

**Finding:** The engine reloads dynamic_weights.toml via SIGHUP, but the Python bridge subprocess does NOT reload. This means the engine has updated strategy weights but Python continues generating signals with stale configuration. The desynchronization persists until the next engine restart.

**Evidence:** IMPLEMENTATION_MASTER_PLAN.md:173: "Bridge SIGHUP reload: NO." EXECUTION_BACKLOG N5c: "Bridge SIGHUP hot-reload — 1 day — Bridge" (P0 BUILD NOW, not started).

**Risk:** Stale Python configuration means the engine's risk parameters and Python's signal generation parameters diverge. The engine may reject signals that Python generates under old rules, or accept signals that Python would have suppressed under new rules. The effective strategy is a chimera of two different configurations.

**Recommendation:** Implement N5c immediately. On SIGHUP, the engine should send a `{"cmd": "reload"}` message to Python via stdin. Python reads the new dynamic_weights.toml and acknowledges. Until this is implemented, note in operations docs that dynamic_weights changes require engine restart, not just SIGHUP.

**Status:** OPEN

---

## Persona 5: Macro Economist / External Shock Analyst

*"I have lived through the GFC, Flash Crash, COVID crash, and every VIX spike since 2008. Leveraged ETPs in an ISA during a market crisis is a scenario I know intimately."*

---

### RT-5-01 | CRITICAL | 3x-5x Leveraged ETPs During Market Crashes: -50% to -90% Possible In Single Day

**Finding:** During the COVID crash (March 2020), the S&P 500 fell 12% in a single day (March 16). A 3x leveraged S&P ETP would have declined approximately 36% in that session. QQQ3.L (3x NASDAQ) would have seen similar moves. During the Flash Crash (May 6, 2010), some ETPs temporarily lost 60-90% of value within minutes before recovering. The system's `daily_drawdown_pct = 4.0` and `equity_floor_pct = 70.0` would trigger HALT, but by the time the HALT is detected (5-minute reconciliation cycle), the damage may already be catastrophic.

**Evidence:** config.toml:65: `daily_drawdown_pct = 4.0`. At 3 positions in 3x ETPs, a 12% underlying drop = 36% ETP drop. Even at 3.3K position size (1/3 of deployed capital): 3.3K * 0.36 = 1,188 GBP loss per position. Three positions = 3,564 GBP = 35.6% of 10K equity. The 4% daily halt would trigger at 400 GBP loss, but the engine processes ticks every 100ms. A flash crash that gaps through the stop will not trigger the Chandelier exit at the stop price; it will fill at the crash price.

**Risk:** Gap risk + leverage = potential account destruction. The VIX-tiered response (config.toml:86-91) suspends 3x longs at VIX > 35, but VIX can spike from 15 to 35 within hours, after positions are already established. The system has no pre-market VIX check to prevent entries on days when VIX futures indicate elevated risk.

**Recommendation:** (1) Add pre-market VIX check: if VIX futures > 25 at 07:30 London, reduce max positions to 1 and tighten all stops by 50%. (2) Implement gap detection on market open: if any held ETP gaps down > 5% from prior close, immediately submit market sell orders (do not wait for Chandelier stop to trigger). (3) Add overnight position limit: no more than 1 position held overnight in crisis regime.

**Status:** OPEN

---

### RT-5-02 | CRITICAL | Currency Risk: USD-Denominated ETPs in GBP ISA With No FX Hedge

**Finding:** Most LSE leveraged ETPs trade in USD on LSEETF (project memory: "Only 3LUS.L and 5SPY.L are GBP. All others need currency=USD"). The ISA account is denominated in GBP. Every trade in a USD-denominated ETP has implicit GBP/USD currency exposure. A 5% GBP strengthening against USD wipes out 5% of portfolio value in GBP terms, independent of ETP performance.

**Evidence:** Project memory: "Most LSE leveraged ETPs trade in USD on LSEETF." contracts.toml: 49 LSE ETPs, most with currency="USD". No FX hedge logic anywhere in the codebase. portfolio.rs grep for "currency|Currency|GBP|USD" returns no matches — the portfolio tracks positions without currency conversion.

**Risk:** The system's PnL is calculated in the ETP's trading currency (USD for most instruments) but the account and ISA limit are in GBP. A trade that is profitable in USD terms may be a loss in GBP terms. Over a year, GBP/USD can move 10-15%, which is larger than the system's expected annual return in some scenarios. The lack of currency awareness means the WAL PositionClosed `final_pnl` field may be in the wrong currency.

**Recommendation:** (1) Add GBP/USD rate tracking to the engine (subscribe to GBP.USD= IBKR market data). (2) Convert all PnL calculations to GBP for consistency with the ISA account. (3) Consider FX risk as a component of the risk budget: if GBP is strengthening, reduce USD-denominated positions. (4) At minimum, log the GBP/USD rate at entry and exit in the WAL for accurate PnL attribution.

**Status:** OPEN

---

### RT-5-03 | HIGH | Correlation Clustering: All 49 LSE Leveraged ETPs Are Correlated To US Tech

**Finding:** The 49 LSE leveraged ETPs in contracts.toml are overwhelmingly concentrated in US technology and broad market indices: QQQ3, NVD3, GPT3, 3LUS, TSL3, AMD3, TSM3, MU2, 3SEM, etc. Even the "diversified" instruments (3LUS = 3x S&P 500, SP5L = 5x S&P 500) have >0.85 correlation with NASDAQ during stress events. The sector classification in config.toml treats these as different sectors, but in a crisis, they all go down together.

**Evidence:** config.toml:146-155 defines sectors: Technology, Semiconductors, US_Broad, Single_Stock. In the March 2020 crash, NASDAQ and S&P 500 had 0.97 correlation. NVD3 (3x NVIDIA) and QQQ3 (3x NASDAQ) had >0.90 correlation. TSL3 (3x Tesla) and QQQ3 had >0.85 correlation. The sector heat caps treat these as independent, allowing simultaneous positions.

**Risk:** The portfolio's effective diversification during stress events is near-zero. A "diversified" portfolio of QQQ3.L + 3LUS.L + NVD3.L is essentially a single 9x leveraged bet on US technology. The max_correlated_positions = 3 guard requires real-time correlation computation that may not be calibrated for crisis-regime correlations.

**Recommendation:** (1) Implement stress-correlation matrices: use crisis-period correlations (not normal-period) for position limits. (2) Hard cap: no more than 2 positions in long-leveraged US-correlated instruments simultaneously. (3) Require at least one inverse position (QQQS.L, 3USS.L) when holding 2+ long positions. (4) Add a "portfolio beta" check: if portfolio beta to NASDAQ > 5x, reject new entries.

**Status:** OPEN

---

### RT-5-04 | HIGH | UK Regulatory Risk: FCA May Restrict Leveraged ETPs in ISAs

**Finding:** The UK Financial Conduct Authority (FCA) has historically restricted retail access to complex products. In 2020, the FCA banned the sale of crypto-derivatives to retail investors. Leveraged ETPs in ISAs represent a similar regulatory target. If the FCA restricts or bans leveraged ETPs in ISAs, the entire strategy becomes non-viable. HMRC could also change ISA rules to exclude leveraged products.

**Evidence:** No regulatory monitoring in the codebase. The ISA tax advantage (0% CGT) is described as the system's "structural edge" (IMPLEMENTATION_MASTER_PLAN.md:107). If this edge is removed by regulation, the system must generate sufficient returns to overcome CGT (20% for higher-rate taxpayers on gains above the annual exempt amount).

**Risk:** Regulatory change could: (a) force liquidation of all leveraged ETP positions within the ISA, (b) create tax complications for gains realized before the rule change, (c) eliminate the 0% CGT advantage that makes the strategy viable on a risk-adjusted basis.

**Recommendation:** (1) Monitor FCA consultation papers and policy statements quarterly. (2) Build a contingency plan: if leveraged ETPs are restricted, which non-leveraged instruments can the system trade? (3) Estimate post-tax returns assuming CGT applies. If post-tax returns are negative, the strategy is not viable outside the ISA wrapper.

**Status:** OPEN

---

### RT-5-05 | HIGH | Liquidity Evaporation During VIX Spikes: ETPs May Become Untradeable

**Finding:** During VIX spikes above 40, LSE leveraged ETP liquidity historically drops by 80-95%. Market makers widen spreads to 2-5% and may withdraw entirely. The system's EOD flatten logic assumes it can exit positions, but during a liquidity crisis, exit orders may not fill at any reasonable price. IBKR may also restrict trading in leveraged products during extreme volatility.

**Evidence:** VIX-tiered response in config.toml:86-91 handles portfolio-level risk but does not model instrument-level liquidity. The EOD flatten Phase 3 uses MTL (Market-to-Limit) orders, which in illiquid conditions may fill at prices 3-5% below the last trade.

**Risk:** Trapped positions during a liquidity crisis. If the system holds 3 long-leveraged positions and the market crashes, exit orders may not fill. The positions could lose 20-40% before liquidity returns. IBKR has historically auto-liquidated positions in leveraged products during extreme moves, potentially at the worst possible prices.

**Recommendation:** (1) Track real-time quoted depth for each position. If bid depth < 2x position size, escalate to FLATTEN immediately (do not wait for scheduled exit). (2) During VIX > 30, switch all exit orders to IOC (Immediate-or-Cancel) market orders to ensure fills. (3) Consider a "liquidity reserve" rule: never hold positions larger than 10% of average 5-minute volume.

**Status:** OPEN

---

### RT-5-06 | HIGH | Gap Risk: Overnight Positions in 3x ETPs Face Unhedgeable Gap Exposure

**Finding:** The system allows overnight positions (config.toml:69: `overnight_exposure_cap_pct = 50.0`). For 3x leveraged ETPs, overnight gaps are amplified. If the US market drops 3% after LSE close, the 3x ETP will open approximately 9% lower. The Chandelier stop at Rung 1 (entry - 1.5x ATR) provides approximately 2-3% stop, but a 9% gap blows through this stop completely.

**Evidence:** config.toml:69: `overnight_exposure_cap_pct = 50.0`. At max overnight exposure with 10K equity, 5,000 GBP could be in overnight positions. A 9% gap = 450 GBP loss = 4.5% of equity from a single overnight gap. With 3 positions: 1,350 GBP = 13.5% of equity.

**Risk:** The Chandelier trailing stop is meaningless during market gaps. The position opens below the stop price and the exit fill occurs at the gap-down price, not the stop price. The system's "maximum" loss per trade (stop width) is a fiction during overnight gaps.

**Recommendation:** (1) For paper validation, set overnight_exposure_cap_pct to 25% (matching live cash_buffer logic). (2) Add a "gap risk budget": overnight positions must have combined notional such that a 10% gap in all positions does not breach the daily drawdown limit. (3) For 3x ETPs, apply a 3x multiplier to gap risk calculations. (4) Consider closing all positions by EOD as the default, with overnight holds only for positions above Rung 3.

**Status:** OPEN

---

### RT-5-07 | MEDIUM | Interest Rate Impact on Leveraged ETP Costs: Rising Rates Increase Drag

**Finding:** Leveraged ETPs incur daily financing costs proportional to the leverage factor and the prevailing interest rate. At 5% risk-free rate and 3x leverage, the daily financing cost is approximately `(3-1) * 5% / 365 = 0.027%` per day = 10% annual. This cost is embedded in the ETP NAV and reduces returns. The system does not track or account for this structural cost drag.

**Evidence:** No financing cost tracking in config.toml or bridge.py. The `volatility_drag_3x = 9` in config.toml:29 captures variance drain but not financing costs. These are additive headwinds: variance drain + financing cost = total structural drag.

**Risk:** At current rates (~4.5% GBP, ~5% USD), the annual financing cost for a 3x ETP is approximately 9-10%. Combined with variance drain (18% at 20% vol) and trading costs (30-57% from RT-3-07), the total annual drag could exceed 60%. The strategy must generate >60% gross annual returns to be profitable.

**Recommendation:** (1) Add financing cost to the Kelly expectancy calculation: deduct daily financing from expected per-trade return. (2) Track cumulative financing drag in the daily report. (3) Monitor interest rate trends: rising rates increase financing costs and may make the strategy non-viable. (4) Consider holding periods: financing cost is per-day, so shorter hold times reduce this drag.

**Status:** OPEN

---

### RT-5-08 | MEDIUM | Flash Crash Exposure: No Circuit Breaker At The Engine Level

**Finding:** The trade taxonomy includes FLASH_CRASH classification (trade_taxonomy.py:105-106: MAE > 3% in < 5 minutes) but this is post-hoc analysis. The engine has no real-time flash crash detector that would suspend trading during a market-wide anomaly. The synthetic halt logic (config.toml:49-50: `synthetic_halt_limp_secs = 30, synthetic_halt_full_secs = 120`) handles per-ticker staleness, not rapid price declines.

**Evidence:** trade_taxonomy.py:105-106 classifies FLASH_CRASH after the fact. No real-time flash crash detection in engine.rs or risk_arbiter.rs. The reconciliation check (every 5 min) is too slow to react to a flash crash that occurs in seconds.

**Risk:** During a flash crash, the engine may: (a) generate exit signals at prices far below stops (hitting the market at the worst moment), (b) attempt new entries as the price appears to trigger momentum signals on the down move, (c) submit multiple orders that overwhelm the broker's rate limiter. The 2010 Flash Crash saw prices recover within 20 minutes; selling during the crash and buying back after would have been the worst possible outcome.

**Recommendation:** (1) Add a real-time circuit breaker: if any position's unrealized loss exceeds 5% in under 2 minutes, HALT all new entries for 15 minutes. (2) During HALT, do NOT exit positions (the crash may reverse). (3) Resume normal operation after 15 minutes if prices have stabilized. (4) Log the event as a FLASH_CRASH_HALT WAL event for post-analysis.

**Status:** OPEN

---

## Summary

### Finding Counts by Severity

| Severity | Persona 1 | Persona 2 | Persona 3 | Persona 4 | Persona 5 | Total |
|----------|-----------|-----------|-----------|-----------|-----------|-------|
| CRITICAL | 2 | 2 | 2 | 2 | 2 | **10** |
| HIGH | 3 | 3 | 4 | 4 | 4 | **18** |
| MEDIUM | 3 | 3 | 2 | 3 | 2 | **13** |
| **Total** | **8** | **8** | **8** | **9** | **8** | **41** |

### Top 5 Most Critical Findings

1. **RT-3-01** (Fund Manager, CRITICAL): PAPER VALIDATION overrides (15 positions, 50% heat) generate contaminated data that is non-transferable to live trading. All Ouroboros learning under these conditions is suspect.

2. **RT-2-01** (Quant, CRITICAL): VanguardSniper has zero backtest. The strategy's expected value after costs is completely unknown. Operating a compounding machine on an unvalidated strategy is quantitative malpractice.

3. **RT-5-02** (Macro, CRITICAL): USD-denominated ETPs in a GBP ISA with no FX tracking or hedging. Portfolio PnL is not converted to account currency. 10-15% annual FX moves dwarf expected strategy returns.

4. **RT-4-01** (Governance, CRITICAL): config.live.toml overlay mechanism is not implemented in code and has key name mismatches. The paper-to-live transition is a manual, error-prone process with catastrophic failure modes.

5. **RT-1-01** (Microstructure, CRITICAL): Static 0.3% spread veto ignores time-of-day variation, ticker-specific profiles, and regime-dependent liquidity. This is the primary source of SPREAD_VICTIM trades.

### Recommended Immediate Actions (Next 5 Build Days)

| Priority | Action | Estimated Days | Finding |
|----------|--------|---------------|---------|
| 1 | Reduce paper config to match live parameters (3 positions, 10% heat) | 0.5 | RT-3-01 |
| 2 | Fix config.live.toml key name mismatches and implement overlay | 1 | RT-4-01 |
| 3 | Add GBP/USD tracking and currency-aware PnL | 1 | RT-5-02 |
| 4 | Rotate Polygon API key, remove from shell scripts | 0.5 | RT-4-02 |
| 5 | Implement MissedWinnerCandidate tracking (N2c) | 1 | RT-2-05 |
| 6 | Add Python bridge timeout (500ms) and health check | 1 | RT-4-04 |

### Go/No-Go Assessment for Paper Trading Continuation

**VERDICT: CONDITIONAL GO — paper trading may continue with mandatory mitigations.**

**Rationale:**
- The system architecture is sound (A grade: WAL, risk arbiter, Chandelier, reconciliation).
- The economics are unvalidated (D+ grade: no backtest, no cost-aware learning, contaminated paper data).
- The IS_LIVE=false hardcode with exit(1) guard provides absolute protection against live deployment.
- Paper trading generates the data needed to resolve most CRITICAL findings.

**Mandatory Conditions for Continued Paper Trading:**
1. IMMEDIATELY reduce paper config to live-equivalent parameters (RT-3-01). Data collected under 15-position/50%-heat conditions is scientifically useless for predicting live performance.
2. Fix config.live.toml key name mismatches within 48 hours (RT-4-01). This is a ticking time bomb.
3. Rotate the compromised Polygon API key within 24 hours (RT-4-02).
4. Add GBP/USD rate logging within 1 week (RT-5-02). Every trade without currency tracking is a data quality gap.

**Mandatory Conditions Before Any Live Trading:**
1. 100+ trades with live-equivalent paper parameters (not the contaminated 20-trade dataset).
2. Backtest validation of VanguardSniper (RT-2-01).
3. Currency-aware PnL system operational and validated.
4. config.live.toml overlay tested and verified.
5. Monte Carlo risk-of-ruin analysis completed (RT-3-08).
6. All 10 CRITICAL findings either MITIGATED or ACCEPTED with documented risk acknowledgment.

---

*This review was conducted under adversarial assumptions. Each persona was instructed to find genuine weaknesses, not validate existing design decisions. The findings represent the minimum standard expected by institutional risk governance frameworks (Basel III operational risk, MiFID II best execution, FCA SYSC 7).*

**Review Board Sign-off Required:** CTO, CRO, CIO, Head of Quant, Red-Team Lead.
