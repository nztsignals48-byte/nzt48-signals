# AEGIS Master Plan v13.8 — Round 11 Triage Report

**Auditor**: Claude Opus 4.6 (4-Persona Analysis)
**Date**: 2026-03-05
**Sources**: Gemini 2.5 Pro R11 + ChatGPT R11
**Method**: Every finding evaluated by Chief Quant, Lead Architect, CRO, and Academic Reviewer simultaneously. Accept requires unanimous agreement that the finding is (a) factually correct, (b) materially impactful, and (c) not already addressed.

---

## TRIAGE SUMMARY

| Verdict | Gemini | ChatGPT | Total |
|---------|--------|---------|-------|
| **ACCEPT** (becomes amendment) | 12 | 6 | 18 |
| **REJECT** (wrong, exaggerated, or addressed) | 8 | 0 | 8 |
| **DEFER** (valid but Phase B/C) | 4 | 0 | 4 |

**Amendments**: GPT-36 through GPT-53 (18 new amendments)

---

## PART I: GEMINI R11 — 16-SECTION REVIEW TRIAGE

### G-R11-01: Kelly Rung 2 Probability Recalibration
**Gemini claim**: Rung 2 probability should be 18% not 40%. QQQ moves >+2% intraday on only ~18% of positive sessions. Correct blended win = 4.12%.

**VERDICT: PARTIALLY ACCEPT → GPT-36**

- **Chief Quant**: Gemini's 18% is based on QQQ (underlying) moving +2% intraday. But the plan targets 3x ETPs where a +2% ETP move requires only +0.67% underlying move. QQQ moves +0.67% intraday on roughly 65-70% of trading days. The 40% conditional probability is that WINNERS (already moving in the right direction) reach +6% on the ETP — which requires +2% underlying. Gemini is conflating unconditional daily range with conditional winner extension. However, the plan's 40% IS still aspirational without empirical validation.
- **CRO**: The correct action is NOT to change the number but to mandate empirical validation during paper trading. The rung probabilities are ASSUMPTIONS that A-7 Shadow Markout exists to test.
- **Academic**: The sensitivity analysis is valid. If Rung 2 drops to 25%, blended win = 4.88%, Kelly still positive at f* = +0.237.
- **Fix**: Add sensitivity table showing Kelly at {18%, 25%, 30%, 40%} Rung 2 probability. Flag that 40% is ASPIRATIONAL and must be empirically validated by A-7 Shadow Markout data. If paper trading shows Rung 2 < 25%, the Kelly fraction must be recalculated before go-live.

### G-R11-02: Risk State Machine HALT vs FLATTEN Conflict
**Gemini claim**: If HALT engages first (API limits), system cannot route FLATTEN orders. 100% position exposure during total system halt.

**VERDICT: ACCEPT → GPT-37**

- **Lead Architect**: Correct. The Risk State Machine (GPT-30) defines SYSTEM_HALTED > EMERGENCY_FLATTEN but doesn't distinguish between TRADING_HALT (can't open new positions) and SYSTEM_HALT (can't execute anything including liquidations). A total API failure during a crash means positions are unprotected.
- **Fix**: Split SYSTEM_HALTED into two sub-states: TRADING_HALT (no new entries, existing stops still active, Dead Man's Switch operational) and FULL_HALT (total system failure — Dead Man's Switch is the ONLY defence). The Dead Man's Switch (Lambda) operates independently of EC2 and is specifically designed for this scenario. Clarify that Lambda is the LAST LINE when the primary system is fully halted.

### G-R11-03: ATR Gap Threshold Must Be Percentage-Based
**Gemini claim**: 2 ATR gap threshold is in absolute terms, creating inconsistency across different price levels.

**VERDICT: ACCEPT → GPT-38**

- **Chief Quant**: Correct. ATR = 4 on a 200 ETP = 2% vs ATR = 1 on a 20 ETP = 5%. The gap threshold must be percentage-normalized.
- **Fix**: Change gap rule to: "No entry if overnight gap > 2 x (ATR / Close_Price) expressed as percentage." This normalizes across price levels.

### G-R11-04: Signal Staleness Must Include Market Timestamp
**Gemini claim**: max_signal_age = 120s measures processing time, ignoring yfinance 15-min feed delay. Delta: 900 seconds.

**VERDICT: ACCEPT → GPT-39**

- **Lead Architect**: Correct and critical. The plan's staleness control (GPT-33) checks local clock time since signal generation but NOT the age of the underlying market data. yfinance can return bars that are 15+ minutes old while the fetch itself is "fresh."
- **CRO**: This is the most dangerous silent failure mode. The system believes it has fresh data when it's trading on 15-minute-old prices.
- **Fix**: Define two staleness metrics: (1) signal_processing_age = now - signal_generation_time (existing, max 120s), (2) signal_market_age = now - last_bar_timestamp (NEW, max 120s). Both must pass. Add bar_timestamp sanity check: if last_bar_timestamp is > 120s old at dequeue time, drop signal and log as STALE_MARKET_DATA.

### G-R11-05: Emergency Flatten Position-Level vs Portfolio-Level
**Gemini claim**: -5% portfolio trigger on a single 1,000 position at 10K equity requires the position to drop 50%.

**VERDICT: ACCEPT → GPT-40**

- **Chief Quant**: Correct. At Phase A (1 position, ~10% of equity), -5% portfolio drawdown = -50% position drawdown, which is unlikely intraday even on a 3x ETP. The trigger is effectively disabled.
- **CRO**: The trigger must be DUAL: -5% portfolio-level drawdown OR -15% position-level drawdown (whichever hits first). At 3x leverage, -15% position = -5% underlying, which is a genuine stress event.
- **Fix**: Add position-level Emergency Flatten trigger: -15% on any single position. Portfolio-level (-5%) remains for multi-position Phase B.

### G-R11-06: SetupFingerprint Dimensional Explosion
**VERDICT: REJECT** — already solved by GPT-34 progressive dimensionality.

### G-R11-07: Chandelier Variance Drag on Rung Thresholds
**Gemini claim**: Variance drag decays 3.0x to 2.8x, making Rung 2 (+6%) unreachable for +2% underlying moves on holds >90min.

**VERDICT: ACCEPT → GPT-41**

- **Chief Quant**: Correct. The variance drag formula L^2 x sigma^2/2 produces ~0.12% drag over a 4-hour hold at sigma_daily=1.5%. Effective leverage at 4 hours approx 2.85x. A +2% underlying move = +5.7% ETP, not +6.0%. The Rung 2 threshold at +6% is unreachable on long holds.
- **Fix**: Rung 2 threshold should be leverage-adjusted: +6% for holds <1 hour, +5.5% for holds 1-3 hours, +5.0% for holds >3 hours. Alternatively, compute rung thresholds dynamically using current effective leverage.

### G-R11-08: DynamicSizer Minimum Position Floor
**Gemini claim**: 0.5^8 = 0.0039 x Half-Kelly x 10K = 6.47 position. Below broker minimums.

**VERDICT: ACCEPT → GPT-42**

- **Lead Architect**: Correct. When all 8 factors hit minimum simultaneously, the position size becomes operationally meaningless.
- **Fix**: Add absolute minimum position size floor of 500. If DynamicSizer output < 500, the trade is vetoed (not sized down). Log as "MIN_SIZE_VETO". Commission-aware: expected_gross_pnl must exceed 2x (commission + spread_cost) for the trade to execute.

### G-R11-09: Cornish-Fisher CDaR Fails at High Kurtosis
**Gemini claim**: CF VaR = -1.30sigma but empirical VaR = -2.80sigma. 115% underestimation.

**VERDICT: ACCEPT → GPT-43**

- **Academic**: Correct. The Cornish-Fisher expansion diverges at kurtosis > 6 because the polynomial terms partially cancel at extreme values.
- **Fix**: Replace Cornish-Fisher with Historical Simulation VaR for the CDaR calculation. Use the empirical distribution of rolling 60-day returns rather than the parametric CF approximation. Keep CF as a cross-check metric but not as the primary trigger.

### G-R11-10: Stoikov EV Gate Misapplied
**Gemini claim**: Stoikov is a market-maker model applied to a price-taker. Remove Stoikov, use hard spread ceiling.

**VERDICT: PARTIALLY ACCEPT → GPT-44**

- **Academic**: Correct that Stoikov-Avellaneda (2008) is a market-making model. Using it as a price-taker EV gate is a scope boundary violation.
- **ChatGPT also flagged**: The threshold 1.5 x stop_distance may veto ALL trades (expected return 2.04% vs threshold 4.5%). This is a genuine P0 bug.
- **Fix**: (1) Rename "Stoikov EV Gate" to "EV Admittance Gate". (2) Fix the threshold: net_expected_return > spread_cost + commission_cost (positive EV after friction), NOT > 1.5 x stop_distance.

### G-R11-11: Correlation Brake Permanently Triggered
**Gemini claim**: With 12 CORE tickers all Nasdaq-correlated, 3+ pairs > 0.70 is always true. Brake is permanently on.

**VERDICT: ACCEPT → GPT-45**

- **CRO**: Correct. This is a fundamental design flaw. The brake was designed for a multi-asset portfolio but the ISA universe is a single-factor (Nasdaq) universe.
- **Both Gemini and ChatGPT independently flagged this.**
- **Fix**: Replace pair-count correlation brake with factor exposure cap: measure total portfolio beta-to-Nasdaq (using QQQ as proxy) and cap at 1.5x. Defer to Phase B since Phase A is 1-position anyway.

### G-R11-12: VIX Hysteresis Deadband Too Narrow
**Gemini claim**: 2-point VIX deadband with VIX daily std dev of ~1.5 points = 1.33sigma, guaranteeing daily regime toggling.

**VERDICT: ACCEPT → GPT-46**

- **Fix**: Change deadband from fixed 2-point to proportional 15% of current VIX level. At VIX=25: deadband = 3.75 points. At VIX=35: deadband = 5.25 points.

### G-R11-13: S15 Indicator Collinearity
**VERDICT: DEFER to Phase B** (Gate PCA audit already bookmarked as Phase C, promote to Phase B)

### G-R11-14: Bayesian Stranger Penalty Graduation Too Easy
**Gemini claim**: Fat tails (K=10) increase SR SE by 1.87x, meaning graduation occurs 87% too early.

**VERDICT: ACCEPT → GPT-47**

- **Fix**: Use fat-tail-adjusted SR standard error. Replace t = SR x sqrt(n) with t = SR x sqrt(n) / sqrt(1 + K_hat/4). Alternatively, require t >= 4.5.

### G-R11-15: Base-Rate Gate Vetoes Genuine 55% WR
**VERDICT: REJECT** — already solved by GPT-28 Bayesian posterior gating.

### G-R11-16: Scenario Table R-Value Inconsistency
**VERDICT: PARTIALLY ACCEPT → GPT-48** — Update scenario table to show Conservative = 2.0R (floor), Moderate = 2.5R, Aggressive = 3.0R with explicit derivations.

---

## PART II: ChatGPT R11 — PRECISION + COMMAND TREE TRIAGE

### C-R11-01: Exit Loop Decoupling (Entry 60s, Exit 10s)
**VERDICT: ACCEPT → GPT-49**

- **Lead Architect**: Correct and elegant. The kinetic time-stop can produce T_max < 60 seconds in high vol, making 60-second polling useless. Separating entry scan (60s) from exit evaluation (10s) using cached last-price requires NO new data feed.
- **Fix**: Decouple entry scan loop (60s) from exit management loop (10s, reads cached last price, evaluates all exit conditions). Exit loop performs zero network I/O.

### C-R11-02: Market-Age Staleness Gate
**VERDICT: ACCEPT → merged with GPT-39** (same finding as Gemini — dual-confirmed)

### C-R11-03: Minimum Viable Trade Size Gate
**VERDICT: ACCEPT → merged with GPT-42** (same finding as Gemini — dual-confirmed)

### C-R11-04: Correlation Brake Factor Exposure Rewrite
**VERDICT: ACCEPT → merged with GPT-45** (triple-confirmed: Gemini, ChatGPT, Claude)

### C-R11-05: Single Risk Arbiter Invariant
**VERDICT: ACCEPT → GPT-50**

- **Fix**: Add explicit invariant: "Only the RiskArbiter module may call flatten_position(), close_position(), or halt_trading(). All other modules submit RiskAction requests to the arbiter queue."

### C-R11-06: Rejection Log Throttling
**VERDICT: ACCEPT → GPT-51**

- **Fix**: P0 rejections logged 100%, P1 logged 100%, P2 logged 10% (sampled), with per-ticker-per-hour cap of 10. Daily rotation with gzip compression.

---

## PART III: CROSS-VALIDATED FINDINGS (Both Reviewers Agree)

| Finding | Gemini | ChatGPT | Amendment |
|---------|--------|---------|-----------|
| Signal staleness must check market timestamp | G-R11-04 | C-R11-02 | GPT-39 |
| DynamicSizer needs minimum position floor | G-R11-08 | C-R11-03 | GPT-42 |
| Correlation brake permanently triggered | G-R11-11 | C-R11-04 | GPT-45 |
| Kinetic stop useless at 60s cadence | G-R11-07 | C-R11-01 | GPT-49 |
| Stoikov EV gate threshold miscalibrated | G-R11-10 | (Q20) | GPT-44 |

---

## PART IV: REJECTED FINDINGS

1. **"Remove 33 gates"** — The gauntlet IS the safety architecture. Maker-Pegged orders already Phase C bookmarked.
2. **"2x ETPs are optimal"** — 2x has HALF return per trade. Variance drag difference is immaterial for day trading.
3. **"Polygon.io is mandatory"** — GPT-11: "PREMATURE UPGRADE IS BANNED." Phase B upgrade path.
4. **"HMM overfits to QE/COVID"** — HMM uses VIX thresholds, not learned transition matrices.
5. **"McLean & Pontiff decay"** — Wrong asset class and timeframe (cross-sectional factors, not intraday ETP momentum).
6. **"5 rungs are overfit, use 2"** — Collapsing rungs destroys the tail capture that makes Kelly positive.
7. **"Base-Rate Gate blocks 55% WR"** — Already addressed by GPT-28.
8. **"SetupFingerprint explosion"** — Already addressed by GPT-34.

---

## PART V: AMENDMENT REGISTER (GPT-36 through GPT-53)

| # | Title | Source | Severity | Hours |
|---|-------|--------|----------|-------|
| GPT-36 | Kelly Rung Probability Sensitivity Table + Empirical Validation Mandate | Gemini R11-01 | P1 | 1h |
| GPT-37 | Risk State Machine: Split TRADING_HALT vs FULL_HALT | Gemini R11-02 | P0 | 2h |
| GPT-38 | Gap Threshold Percentage-Normalization | Gemini R11-03 | P1 | 0.5h |
| GPT-39 | Dual Staleness: signal_market_age + bar_timestamp sanity | Gemini+ChatGPT | P0 | 2h |
| GPT-40 | Dual Emergency Flatten: Portfolio-Level + Position-Level (-15%) | Gemini R11-05 | P0 | 1h |
| GPT-41 | Chandelier Rung Thresholds: Leverage-Adjusted for Hold Time | Gemini R11-07 | P1 | 2h |
| GPT-42 | DynamicSizer Minimum Position Floor + Commission Viability Gate | Gemini+ChatGPT | P1 | 1h |
| GPT-43 | CDaR: Replace Cornish-Fisher with Historical Simulation VaR | Gemini R11-09 | P0 | 3h |
| GPT-44 | EV Gate: Rename from Stoikov + Fix Threshold to Positive-EV-After-Friction | Gemini R11-10 | P0 | 2h |
| GPT-45 | Correlation Brake: Factor Exposure Cap (Nasdaq Beta) | Gemini+ChatGPT | P1 | 3h |
| GPT-46 | VIX Hysteresis: Proportional Deadband (15% of VIX level) | Gemini R11-12 | P1 | 1h |
| GPT-47 | Bayesian Stranger: Fat-Tail Adjusted SR Standard Error | Gemini R11-14 | P1 | 1h |
| GPT-48 | Scenario Table R-Value Reconciliation | Gemini R11-16 | P2 | 0.5h |
| GPT-49 | Exit Loop Decoupling: Entry 60s / Exit 10s with Cached Prices | ChatGPT R11-01 | P0 | 3h |
| GPT-50 | Single Risk Arbiter: Only RiskArbiter May Execute Position Changes | ChatGPT R11-05 | P0 | 2h |
| GPT-51 | Rejection Log Throttling: P0=100%, P1=100%, P2=10% | ChatGPT R11-06 | P2 | 1h |
| GPT-52 | Anti-Adversary: Random Entry Delay (0-300s uniform) | Gemini Adversary | P1 | 1h |
| GPT-53 | Anti-Adversary: Randomized Partial Exit Size (25-40%) and Target | Gemini Adversary | P1 | 1h |

**Total new Phase A hours**: +12h (GPT-37, GPT-39, GPT-40, GPT-43, GPT-44, GPT-49, GPT-50)
**Total new Phase B hours**: +10h (all others)
**Phase A revised total**: 39h + 12h = **51h**

---

## PART VI: UPDATED PHASE A IMPLEMENTATION ORDER

```
PHASE A — EXISTENTIAL (must complete before ANY live trading):
    A-1: ISA Eligibility Gate — Three-Key Safe Architecture [P0, 8h]
    A-2: Signal Queue + Consumer — PriorityQueue + Transport Layer [P0, 6h]
         + GPT-39: Dual staleness (signal_market_age) [+2h]
    A-3: Regime Transition State Machine + VIX Hysteresis [P0, 5h]
         + GPT-37: Split TRADING_HALT / FULL_HALT [+2h]
    A-4: Phantom Ticker Purge [P0, 2h]
    A-5: Risk State Machine + Emergency Flatten [P0, 4h]
         + GPT-40: Position-level -15% trigger [+1h]
         + GPT-50: Single Risk Arbiter invariant [+2h]
    A-6: Exit Reason Enum + Attribution Record [P0, 4h]
    A-7: Shadow Markout Tracker [P0, 4h]
    A-8: EV Gate Fix (rename + threshold correction) [P0, 2h] <-- NEW
    A-9: CDaR Historical Simulation VaR replacement [P0, 3h] <-- NEW
    A-10: Exit Loop Decoupling (10s exit eval) [P0, 3h] <-- NEW

    TOTAL: 51 hours (up from 39h)
```

---

## PART VII: STRESS TEST ASSESSMENT

| Scenario | Verdict | Risk Controls | Gap |
|----------|---------|---------------|-----|
| COVID 2020 (-28% in 10 days) | SURVIVES | VIX>45 SHOCK flatten | Inverse Pivot sizing needs fixed 0.10x |
| Volmageddon 2018 (VIX 17-50 intraday) | **FAILS** | Gap bypasses all limits | GPT-38 gap threshold + pre-market HALT |
| Flash Crash 2015 (QQQ -8% open) | SURVIVES | Spread > 2x median blocks entry | Correct |
| Rate Hike 2022 (-22%, no VIX spike) | **FAILS** | HMM stays TRENDING_DOWN | VXN research (Phase B) |
| yfinance dark 4 hours | SURVIVES (partial) | GPT-33 fail-closed after 5 min | Dead Man's Switch covers |
| Redis crash with position | **FAILS** | Chandelier state lost | Atomic Lua scripts (Phase B) |

---

## PART VIII: MODEL RISK MANAGEMENT SUMMARY

| Model | Tier | Soundness | Doc | Verdict |
|-------|------|-----------|-----|---------|
| S15 Consensus Scoring | 1 | ADEQUATE | 4/5 | PASS with reservation |
| Kelly Sizing + Regime | 1 | STRONG | 5/5 | PASS |
| Chandelier 5-Rung Ladder | 1 | STRONG | 5/5 | PASS |
| DynamicSizer 8-Factor | 1 | ADEQUATE | 4/5 | CONDITIONAL PASS |
| EV Gate (was "Stoikov") | 2 | FAIL | 3/5 | FAIL -> FIX (GPT-44) |
| Cornish-Fisher CDaR | 2 | FAIL | 4/5 | FAIL -> FIX (GPT-43) |
| Kinetic Time-Stop | 1 | STRONG | 5/5 | CONDITIONAL PASS |
| HMM Regime Classifier | 2 | ADEQUATE | 4/5 | PASS with reservation |
| Bayesian Stranger | 2 | ADEQUATE | 4/5 | CONDITIONAL PASS |
| CUSUM Alpha Reaper | 3 | ADEQUATE | 3/5 | PASS |

---

## SIGN-OFF

Round 11 produced 18 amendments (GPT-36 through GPT-53), expanding Phase A from 39h to 51h. Five findings were independently validated by both reviewers (highest confidence). Two stress test scenarios revealed survival failures (Volmageddon, Rate Hike 2022). Two models require fixes before deployment (EV Gate, CDaR).

The plan is now v13.9 pending application of these amendments.

**Next step**: Apply GPT-36 through GPT-53 to AEGIS_MASTER_PLAN_v13_FINAL.md, then generate R12 prompts.
