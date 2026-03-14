# R20: CLAUDE INDEPENDENT ADVERSARY AUDIT

**Date**: 2026-03-06
**Reviewer**: Claude Opus 4.6 (Independent Adversarial — 4 Personas)
**Document Under Review**: AEGIS_MASTER_PLAN_v13_FINAL.md (v13.17, 8,500+ lines)
**Scope**: Full adversarial destruction audit — find what 18 prior rounds missed
**Method**: 4-persona analysis + 100 adversarial questions + independent findings

---

## EXECUTIVE VERDICT

**The plan is architecturally sound but operationally undeployable.**

After 18 review rounds, 116 amendments, and 384 indexed files, the plan has been refined into an impressive theoretical document. But a devastating truth remains: **ZERO lines of the 116 amendments have been implemented in code.** The running system is still pre-v13 code with every P0 bug intact. The plan describes a system that does not exist.

**Grade: B+ (plan quality) / F (system readiness)**

---

# PART I: FOUR-PERSONA ADVERSARIAL AUDIT

---

## PERSONA 1: CHIEF QUANT OFFICER (CQO)

*"Show me the math. Then show me where the math breaks."*

### CQO-01: Kelly Derivation Contains a Survivorship Assumption (NEW FINDING)

The Kelly payoff resolution (GPT-29, corrected by GPT-101) derives blended average win ≈ +5.0% using the VT inline ladder rung probabilities. But these probabilities are **assumed**, not empirically measured:

- Rung 2 (+2%) reach probability: ~90% ← **assumed**
- Rung 3 (+4%) reach probability: ~65% ← **assumed**
- Rung 4 (+6%) reach probability: ~45% ← **assumed**

The entire Kelly derivation, and therefore the entire system's mathematical viability, rests on these conditional probabilities being approximately correct. **There is zero empirical data to validate them.** The system has never traded live. The paper trading data (which doesn't exist yet either) would need 200+ winning trades to get stable rung-conditional probabilities.

**CRITICAL**: If Rung 2 reach probability is 70% instead of 90%, blended average win drops to ~+3.8%, and Kelly at 50% WR drops to +0.09 — barely positive and below any reasonable implementation threshold.

**Verdict**: The Kelly math is internally consistent but built on a house of assumed probabilities. The system MUST NOT go live until Shadow Markout (A-7) provides empirical rung reach rates from 200+ paper trades.

**Amendment: CQO-01** — Add a HARD GATE to the Go-Live criteria: "Empirical Rung 2 reach probability ≥ 75% measured over ≥100 winning paper trades, with 95% confidence interval." If Rung 2 < 75%, recalculate Kelly and daily target expectations before proceeding.

### CQO-02: The 2% Daily Target Is Physically Unreachable on Most Days (NEW FINDING)

The plan's mandate says "compound at 2%+ daily." Let's check the physics:

- 2% target on a 3x ETP = +6% ETP move = +2% underlying move
- QQQ (Nasdaq 100) average daily range ≈ 1.3% (median, not mean)
- Only ~35% of trading days see QQQ move >2% intraday

This means **on 65% of trading days, the underlying physically cannot deliver a +2% move**, making the 6% ETP target unreachable regardless of signal quality. The system will sit flat on most days.

The scenario table (Section 0.5) acknowledges this with "55% WR" but doesn't explicitly model **the zero-signal days** where no setup qualifies. If the system trades ~3 days/week (not 5), the effective compounding rate drops by 40%.

**Verdict**: The 2% target is aspirational marketing, not operational reality. The plan should explicitly model expected trading frequency (trades/week) alongside WR and R-ratio.

**Amendment: CQO-02** — Add "Expected Trading Frequency" row to the scenario table: Conservative = 2.5 trades/week, Moderate = 3.5 trades/week, Aggressive = 4.5 trades/week. Recalculate annual projections using (1 + daily_return)^(trades_per_year) instead of ^252.

### CQO-03: Variance Drag Invalidates Multi-Hour Holds on 3x ETPs

The plan acknowledges variance drag (B-7 Kinetic Time Stop) but underestimates its impact on the profit ladder. For a 3x ETP:

```
E[r_L] ≈ 3 × r_u - 3(3-1)/2 × σ² = 3r_u - 3σ²
```

At σ_daily = 1.5%:
- Drag per hour ≈ 3 × (0.015²/6.5) ≈ 0.010% per hour
- Over 4 hours: ~0.04% lost to drag alone

This seems small, but it means the "hold for Rung 4+ tail capture" strategy is fighting continuous bleed. The trailing 67% that rides from Rung 2 to Rung 4+ is being eroded by variance drag during the multi-hour hold. The Kelly payoff calculation doesn't account for this drag reducing the effective rung returns.

**Verdict**: MINOR — drag at current equity levels is small relative to rung sizes. But it becomes material at 5x leverage where drag ≈ 10σ². The plan should note this scaling concern.

### CQO-04: Monte Carlo Simulation Uses Questionable Return Distribution

Section 0.5 cites "10,000 paths with 60% win rate, 2.5R reward ratio, 40bps spread cost, and daily variance drawn from empirical leveraged ETP return distributions." But:

1. What distribution? Normal? Student-t? Which degrees of freedom?
2. 3x ETP daily returns are NOT normally distributed — they exhibit significant negative skewness and excess kurtosis (fat left tails)
3. Using a symmetric distribution for Monte Carlo on leveraged ETPs will systematically underestimate ruin probability

The plan mentions "Monte Carlo distribution specification" as a v13.1 addition but I don't see the actual specification in the plan body.

**Verdict**: The Monte Carlo projections are unreliable without specifying the return distribution. Recommend using empirical historical returns (bootstrap) rather than parametric distributions.

---

## PERSONA 2: LEAD SYSTEMS ARCHITECT (LSA)

*"Show me the wiring diagram. Then show me the single points of failure."*

### LSA-01: yfinance Is a Single Point of Failure for the Entire System (KNOWN, INSUFFICIENTLY ADDRESSED)

The plan acknowledges yfinance data quality issues (GPT-39 staleness, GPT-60 lock contention) but doesn't address the fundamental architectural risk: **the entire trading system depends on a single, free, rate-limited, unofficial API that can change or break at any time**.

yfinance is a community-maintained wrapper around Yahoo Finance's undocumented internal API. It has:
- No SLA
- No guaranteed uptime
- Rate limiting that can change without notice
- Data quality issues (stale quotes, missing bars, wrong timestamps)
- Historical precedent of being broken by Yahoo backend changes

The VIX cascade (yfinance→CBOE→HMM→default 25) only addresses VIX. The price feed for all 12 ISA tickers has **no fallback**. If yfinance returns stale data for QQQ3.L, the system trades on stale prices with no detection beyond the 5-minute staleness check.

**Amendment: LSA-01** — Add a price feed health gate: Compare yfinance last_price against the Rung 1 trigger level. If `abs(yfinance_price - broker_indicative_price) > 1%`, enter DATA_SUSPECT mode (exits only, no entries). This requires a secondary price source for cross-validation — even a delayed free source like Google Finance would catch gross errors.

### LSA-02: t3.small EC2 Instance Is Inadequate for 300-500 Ticker Core Universe

The plan specifies scaling to 300-500 Core tickers scanned every 60 seconds. A t3.small has:
- 2 vCPUs (burstable)
- 2 GB RAM
- Limited CPU credits that deplete under sustained load

Running yfinance batch downloads for 500 tickers, computing Amihud/ASER/DSR filters, scoring with LightGBM+XGBoost, evaluating 33 gates, managing Redis state, running the learning engine, generating PDFs, and serving the dashboard API — all on 2 GB RAM — is a recipe for OOM kills.

The current system already runs 12 tickers. At 500 tickers (40x increase), memory and CPU requirements scale super-linearly due to correlation matrix computation (O(n²)).

**Verdict**: The infrastructure section needs a scaling plan. Phase 2 (universe expansion) should trigger an EC2 upgrade to t3.medium (4GB) minimum, ideally t3.large (8GB).

### LSA-03: Three Profit Ladders Still Exist (NOT FIXED)

GPT-107 identified three competing profit ladder implementations. The plan says "consolidate to ONE" but the code still has all three:
1. `core/chandelier_exit.py` — dead code (register() never called)
2. `execution/virtual_trader.py` lines 1703-1877 — actually fires
3. `qualification/profit_ladder.py` lines 221-300 — also fires via DB reconciliation

The plan recommends option (b) — designate VT inline as canonical. But this hasn't been done. Both #2 and #3 fire simultaneously, potentially creating conflicting exit signals.

**Verdict**: This is a stop-ship item already tracked (GPT-101/107). Reconfirm it remains unfixed.

### LSA-04: No Graceful Shutdown Procedure

The plan has extensive startup procedures (Startup Readiness Gate, §8B) but no graceful shutdown procedure. If Docker is stopped during market hours:
- Open positions are orphaned (no exit orders placed)
- Redis state may be inconsistent (write was in-flight)
- Circuit breaker state is preserved (good — GPT-90) but position state is not reconciled

**Amendment: LSA-04** — Add a SIGTERM handler that: (1) cancels all pending orders, (2) logs all open positions with current P&L, (3) writes final state to SQLite, (4) flushes Redis with BGSAVE. If shutdown occurs during market hours, flag all positions for manual review on restart.

### LSA-05: The Codebase Header Stat Is Wrong

Line 14 says "15,700+ LOC" but the actual codebase is 131,254 LOC across 298 files (per R12 audit). The plan's own Document Statistics say "131,254 LOC." This header stat is stale from v11.

**Amendment: LSA-05** — Update line 14 from "15,700+ LOC" to "131,254 LOC" to match actual codebase.

---

## PERSONA 3: CHIEF RISK OFFICER (CRO)

*"Show me how the system fails. Then show me it can survive."*

### CRO-01: The System Has Never Been Tested Under Adverse Conditions (CRITICAL)

The plan specifies exhaustive risk controls: 33 gates, 10-layer risk shell, constitutional circuit breakers, regime classifier, ML meta-model, drought state machine, flapping protection, etc.

**NONE of these have been tested against real market data.** The system has never:
- Traded during a VIX spike (>35)
- Traded during an LSE circuit breaker halt
- Handled a gap open exceeding 5% on a 3x ETP
- Experienced yfinance returning incorrect data
- Had its ML model make a wrong prediction under stress
- Processed a RISK_OFF→SHOCK transition
- Encountered a genuine cascade failure

The paper trading phase is supposed to test these scenarios, but **the 63-day paper period is almost certainly insufficient** to encounter a genuine VIX >45 SHOCK event. The last such event was March 2020 (6 years ago). The failure simulation drills (GAP-08) partially address this, but simulated failures are not the same as real failures.

**Verdict**: The plan should explicitly acknowledge that paper trading will NOT validate crisis behaviour. Add a dedicated stress testing phase using historical replay of March 2020, October 2023, and August 2024 market events.

### CRO-02: Commandment 4 Says -3% Daily Halt But Constitution Says L1=-1.5%, L2=-2.5%, L3=-4.0%

Section 6B (10 Commandments) says "Daily halt at -3%." But:
- L1 = -1.5% (reduce 50%)
- L2 = -2.5% (exit-only)
- L3 = -4.0% (flatten all)

Which is it? The -3% figure appears in multiple places (Commandments, D-1 gate) but doesn't match any Constitutional level. Is it a discipline gate (-3%) that's separate from the constitutional cascade? If so, what's its relationship to L2 (-2.5%)?

**Amendment: CRO-02** — Reconcile D-1 "Daily Loss Limit" threshold with Constitutional cascade. Options: (a) D-1 triggers at -2.5% aligning with L2, or (b) D-1 is a separate, softer discipline gate that fires between L1 and L2. Whatever the answer, document it explicitly. Currently the plan has FOUR different daily loss thresholds: -1.5% (L1), -2.5% (L2), -3% (D-1/Commandment 4), -4% (L3). Only 3 should exist.

### CRO-03: Weekly Loss Halt Has THREE Conflicting Values

- Constitution (GAP-05): -8.0% → HALT for week
- Plan (settings.yaml): -6.0% → WARNING
- D-1/Commandment: -5.0% → HALT

The plan's resolution says -6% is a "WARNING" within the -8% Constitutional hard limit. But D-1 says -5% weekly. That's three numbers for the same concept.

**Verdict**: This was supposedly resolved in v13.16 but three values still exist. The plan should have a single PROTECTED_PARAMETERS table with ONE value per parameter.

### CRO-04: Max Concurrent Positions Is Inconsistent

- Section 6B, Rule 8: "max concurrent positions" governed by portfolio-level risk
- Section 6B, Multi-Trade Rules: "BULL: 7, RANGE: 3, BEAR: 2"
- Constitution R4: "max 40% deployment" → at 10% per position (R3), max = 4 positions
- Table B: "Max Concurrent = 4"
- LIMITED LIVE (§9B): Max positions = 1

The BULL regime allows 7 concurrent positions, but R3 says 10% max per position and R4 says 40% total. 7 × 10% = 70% — this VIOLATES R4's 40% cap. The plan needs to reconcile max_positions with the R4 deployment cap.

**Amendment: CRO-04** — Max concurrent positions = floor(MAX_TOTAL_DEPLOYMENT / R3_per_position_cap) = floor(0.40 / 0.10) = 4. This should be the ONLY max_positions value. The regime-specific 7/3/2 limits in Multi-Trade Rules should be constrained to min(regime_limit, 4).

### CRO-05: No Kill Switch Specification

The plan mentions "kill switch" 15+ times but never specifies the actual implementation:
- What exactly does the kill switch DO? Flatten all? Halt new entries? Both?
- Who can trigger it? Operator? Automated? Both?
- How fast does it execute? Seconds? Minutes?
- What happens to open orders? Cancelled? Left alone?
- Is there a Dead Man's Switch (referenced once in Phase 0)?

Phase 0 lists "Dead Man's Switch (CloudWatch + Lambda flatten)" as a task but there's no architectural specification for it in the plan body.

**Amendment: CRO-05** — Add a Kill Switch specification section with: trigger methods (Telegram command, dashboard button, Docker stop, CloudWatch alarm), execution mechanics (cancel all orders → flatten all positions → HALT mode), latency target (<30 seconds end-to-end), and Dead Man's Switch heartbeat interval (5 minutes).

---

## PERSONA 4: ACADEMIC PEER REVIEWER (APR)

*"Show me the citations. Then show me where you misapplied them."*

### APR-01: Avellaneda-Stoikov (2008) Is Misapplied

The plan cites Avellaneda-Stoikov 2008 for the EV Gate (now renamed to "EV Admittance Gate" per GPT-44). A-S 2008 is a market-making model that computes optimal bid-ask quotes for an inventory-risk-averse market maker. It is NOT applicable to a price-taking momentum strategy.

GPT-44 already identified this and renamed the gate, but the underlying formula (`s_hat_L = s_mid + L * beta_OBI * OBI * sigma_1min * urgency(t)`) still uses the A-S reservation price framework. The system is a price-taker; it doesn't set quotes.

**Verdict**: The formula is harmless in practice (it adjusts limit order placement, which is reasonable), but the academic citation is incorrect. The plan should cite Almgren & Chriss (2001) for optimal execution instead.

### APR-02: Kelly Criterion Assumptions Are Violated

The Kelly criterion (f* = (pb - q) / b) assumes:
1. Binary outcomes (win/lose) — **VIOLATED**: the profit ladder creates continuous outcomes
2. Known p and b — **VIOLATED**: both are estimated from zero live data
3. Independent trials — **VIOLATED**: consecutive trades on correlated 3x ETPs are not independent
4. Infinite time horizon — **VIOLATED**: daily session constraints limit trade count
5. No transaction costs — **VIOLATED**: 40bps round-trip spread

The plan uses Half-Kelly to address some of these violations (Thorp 2006), which is standard practice. But the claim that "Kelly is strongly positive" should come with the caveat that these assumption violations reduce effective edge by an unknown amount.

**Verdict**: MINOR — Half-Kelly is the industry standard correction. But the plan should explicitly list these assumption violations and explain why Half-Kelly is sufficient mitigation.

### APR-03: Hamilton (1989) HMM Is Over-Specified for This Application

The plan claims "3 latent states mapped to 8 observable regimes." Hamilton's HMM framework estimates latent states from return data. The 8 observable regimes are constructed via rule-based overlays (VIX thresholds + trend indicators).

This is not a pure HMM — it's a hybrid model where the HMM's latent states are overridden by deterministic rules. The academic rigor of the HMM is undermined by the rule-based overrides, because the overrides can contradict the HMM's posterior probabilities.

**Verdict**: The hybrid approach is pragmatically sensible but shouldn't claim the full authority of Hamilton (1989). It's better described as "HMM-informed rule-based regime classification."

### APR-04: Ledoit-Wolf Shrinkage Estimator Requires More Observations Than Variables

Section 5.4 uses Ledoit-Wolf for the correlation matrix. L-W requires n > p (more observations than variables). With 12 tickers and a 60-day lookback using 30-minute returns, n ≈ 60 × 13 = 780 observations for p = 12 variables. This is fine.

But at Phase 2 expansion (300-500 tickers), p = 500 with n ≈ 780, violating n > p. The Ledoit-Wolf estimator degrades significantly when p approaches n.

**Verdict**: The plan should specify switching to a factor model (e.g., Fama-French 5-factor + momentum) for correlation estimation when ticker count exceeds 100.

---

# PART II: 100 ADVERSARIAL QUESTIONS WITH ANSWERS

## Category 1: Kelly Criterion & Position Sizing (15 questions)

**Q1: What happens to the Kelly fraction if win rate drops to 45%?**
A: At WR=45%, b=1.70: f* = (1.70×0.45 - 0.55)/1.70 = (0.765-0.55)/1.70 = +0.126. Half-Kelly = 0.063. Still positive but very thin. The system would survive but compound very slowly (~0.4%/day). The plan handles this via regime-conditional Kelly (RANGE_BOUND = 0.3 multiplier reduces f* further).

**Q2: Is the 0.75% per-trade risk cap binding or Kelly-binding at current equity?**
A: At £10K, 0.75% risk = £75 max loss per trade. With 3% stop on a 3x ETP, max position = £75/0.03 = £2,500 (25% of equity). The regime-Kelly at 0.6 multiplier gives f* ≈ 0.17 → position = £1,700. Kelly is more binding than the 0.75% cap. The cap only becomes binding when Kelly exceeds 0.75%/stop_pct — at 3% stop, that's when f* > 0.25, which requires WR > 63%.

**Q3: How does the Kelly fraction change across 5x vs 3x ETPs?**
A: 5x ETPs have wider stops (5% vs 3%) and higher drag. The effective b (avg_win/avg_loss) changes. At 5x, drag = 10σ², reducing effective wins. Kelly should be computed separately for each leverage class. The plan does this via regime-conditional Kelly but doesn't explicitly separate by leverage.

**Q4: What is the maximum drawdown the Kelly sizing permits?**
A: At 0.75% per trade, 4 concurrent positions, all hitting stops simultaneously: max single-event DD = 4 × 0.75% = 3.0%. This exactly hits the L1 circuit breaker threshold of -1.5% (which is daily P&L, not instantaneous). But if all 4 are 3x ETPs with 3% stops and all gap through stops: max DD = 4 × 0.75% × 1.5 (gap factor) ≈ 4.5% — between L2 and L3.

**Q5: The DynamicSizer has 8 factors. Which factors are actually implemented in code?**
A: Based on R15 forensic audit, the DynamicSizer code exists but has bugs: correlation families are US-only (GPT-105), ToD windows are US market hours only (GPT-106), SHOCK_RECOVERY counts signals not sessions (GPT-61). At least 3 of 8 factors are broken for ISA tickers.

**Q6: What happens when the stranger penalty kappa=0.25 is applied to a Kelly fraction of 0.17?**
A: Effective position = 0.25 × 0.17 = 0.0425 = 4.25% of equity. At £10K, that's a £425 position. At 3x leverage on a £25 ETP, that's 17 shares. Commission on 17 shares ≈ £1.50. Profit on a +2% move = £8.50. Net = £7.00. This is viable but tiny.

**Q7: Does the commission viability gate (GPT-42) actually prevent sub-economic trades?**
A: GPT-42 adds a minimum position floor. But the plan doesn't specify the floor value. If the floor is too low, the system could take £5 positions where commission exceeds expected profit.

**Q8: What is the expected number of trades needed to reach kappa=0.50?**
A: Using kappa formula: 0.50 = 0.25 + 0.75 × f_DSR × f_n. At DSR=2.0: f_DSR = 1-exp(-0.5×0.5) = 0.221. Solving: 0.50 = 0.25 + 0.75 × 0.221 × n/(n+50). 0.25/0.166 = n/(n+50). 1.506(n+50) = n. Unsolvable (negative n). This means at DSR=2.0, kappa NEVER reaches 0.50 regardless of trade count. The DSR must be >2.5 to achieve kappa=0.50 in finite trades. This is extremely conservative.

**Q9: How many simultaneous losing days before the weekly halt triggers?**
A: At -1.5% per losing day (L1), the -6% weekly warning triggers after 4 consecutive losing days. The -8% Constitutional halt triggers after ~5.3 losing days. Given S15 trades ~3 days/week, this means 1.5-2 full losing weeks to hit the Constitutional weekly halt.

**Q10: Is Half-Kelly (f*/2) the correct Kelly fraction for leveraged ETPs?**
A: Thorp (2006) recommends Half-Kelly for estimation error. But leveraged ETPs have additional risks (tracking error, liquidity, overnight gap) that are not captured by Kelly. A more appropriate fraction might be Quarter-Kelly (f*/4) during the early validation phase. The plan's regime multipliers (0.3-0.6) effectively implement 30-60% of full Kelly, which is reasonable.

**Q11: What is the minimum account size below which the system becomes non-viable?**
A: At the minimum Kelly position of £425 (Q6), commission of £1.50, and expected profit of £7.00, the system needs commission < 20% of expected profit. At £10K with 0.75% risk, minimum position ≈ £75. With £1.50 commission and 2% expected return, profit = £1.50, and commission = 100% of profit. Below ~£5K, the system becomes commission-dominated and non-viable.

**Q12: The plan assumes average loss = -3%. What if average loss is -4.5% (gap through stop)?**
A: At avg_loss = 4.5%, b = 5.10/4.50 = 1.13. At WR=55%: f* = (1.13×0.55-0.45)/1.13 = (0.622-0.45)/1.13 = 0.152. Half-Kelly = 0.076. Still positive but significantly reduced. The system survives but compounds ~40% slower.

**Q13: How correlated are the 12 ISA ETPs during a Nasdaq sell-off?**
A: QQQ3.L, NVD3.L, GPT3.L, 3SEM.L, TSL3.L, TSM3.L are ALL Nasdaq-correlated tech names. During a tech selloff, correlation ≈ 0.85-0.95. The correlation brake (ρ > 0.70 → max 1 position) should trigger, but GPT-105 found the correlation families are US-only — ISA .L tickers never match. This means the correlation brake is 100% bypassed for ISA tickers.

**Q14: What is the maximum number of consecutive losers before account ruin?**
A: At 0.75% per trade, account ruin (90% drawdown) requires: 0.9925^n = 0.10 → n = ln(0.10)/ln(0.9925) = -2.303/-0.00753 ≈ 306 consecutive losers. This is effectively impossible. The system survives even catastrophic performance.

**Q15: Does the plan account for ISA contribution limits?**
A: UK ISA annual contribution limit is £20,000 (2025/26). The plan starts with £10,000. If the system compounds to £100K, the ISA contribution limit is irrelevant (gains are tax-free, not contributions). But the plan should note that you can only ADD £20K per tax year, not that gains are limited.

## Category 2: Risk Controls & Circuit Breakers (15 questions)

**Q16: If L3 (-4%) flattens everything, what happens to positions that are currently in profit?**
A: L3 flatten-all is unconditional. A position at +8% gets closed alongside a position at -3%. This is correct — during a genuine crisis, profitable positions can reverse rapidly. But it means a single bad trade can force premature exit of a big winner.

**Q17: Can the circuit breaker be gamed by restarting Docker?**
A: GPT-90 addresses this — circuit breaker state persists to SQLite. A restart does NOT reset breakers. Good.

**Q18: What happens if the daily session boundary (06:00 UTC) falls during a market event?**
A: Circuit breakers reset at 06:00 UTC. If a SHOCK event starts at 05:55 UTC and is still active at 06:05 UTC, the breakers reset to GREEN at 06:00 despite the ongoing crisis. The plan doesn't address this edge case.

**Q19: The Emergency Flatten triggers at -5% portfolio DD / -15% position DD. Can both fire simultaneously?**
A: Yes. If one position is at -15% DD (possible on a 3x ETP with 5% gap), the position-level flatten fires. If total portfolio is also at -5%, the portfolio-level flatten fires. Both trigger the same action (flatten all), so there's no conflict — but the logging should distinguish which trigger fired first.

**Q20: What happens if yfinance returns stale data and the system never enters RISK_OFF?**
A: If yfinance returns yesterday's VIX of 20 during a live VIX spike to 50, the regime classifier stays in NORMAL, the system continues trading into a crash. The staleness controls (GPT-39, max 120s) should catch this, but only if the bar_timestamp is also stale. If yfinance returns old data with a current timestamp (which it sometimes does), the staleness check passes incorrectly.

**Q21: The SHOCK threshold is VIX > 45. What about a flash crash where VIX spikes to 42 and back to 25 in 10 minutes?**
A: VIX = 42 doesn't trigger SHOCK (threshold = 45). The 3-tick confirmation buffer means the system needs 3 consecutive readings above 35 to enter RISK_OFF. A 10-minute spike with 3-tick confirmation should trigger RISK_OFF by the 3rd minute. But if VIX drops back below 33 (hysteresis exit) before the 3rd tick, the system stays in NORMAL. This is correct behaviour — a 10-minute VIX spike that resolves is noise.

**Q22: What is the maximum time between a crisis event and the system's response?**
A: Scan interval = 60s for entries, 10s for exits (GPT-49). Maximum detection delay = 60s (if the event occurs immediately after a scan). 3-tick confirmation = 3 minutes. Total worst-case response time = 60s + 180s = 4 minutes from crisis event to portfolio action. During those 4 minutes, a 3x ETP could move 5-10%.

**Q23: The anti-cascade stop halts trading for 30 minutes after 3 stops in 15 minutes. What happens to the remaining position?**
A: The plan says "cancel all pending orders" but doesn't specify what happens to remaining open positions during the 30-minute cooldown. They should retain their existing stops (which may tighten due to CDaR breaker). Clarify this.

**Q24: Can the system hold BOTH long and short 3x ETPs simultaneously?**
A: Yes — the plan says "the engine holds long AND short positions simultaneously via leveraged and inverse ETPs." The correlation brake should prevent holding QQQ3.L (long Nasdaq 3x) and QQQS.L (short Nasdaq 3x) simultaneously, as they'd be negatively correlated. But the correlation brake is currently US-only (GPT-105), so this check is broken.

**Q25: What is the maximum loss from a single gap open event?**
A: If a 3x ETP gaps -20% at open (plausible on bad earnings), and the system holds max position (0.75% risk, which at 3% stop = 25% of equity): loss = 25% × 20% = 5% of equity. This breaches L3 (-4%) and triggers flatten-all. But the damage is already done — a single gap event can cause a 5% drawdown.

**Q26: The overnight size cap is 0.50% (GPT-33). But R5 says close ALL positions by 16:25. Which applies?**
A: GAP-14 resolves this: R5 is binding during paper and limited live phases. All positions closed by 16:25. The 0.50% overnight cap only applies after a future Constitutional Amendment allowing overnight holds.

**Q27: How does the system handle a broker connectivity failure during market hours?**
A: The Startup Readiness Gate handles this at boot. But mid-session connectivity loss is not explicitly addressed. If IBKR API goes down while positions are open, the system cannot place exit orders. The Dead Man's Switch (CloudWatch + Lambda) is supposed to handle this, but it's not implemented.

**Q28: What happens if Redis crashes and loses all position state?**
A: Redis state includes Chandelier rung levels, position tracking, and recovery multipliers. A Redis crash would lose in-memory state. The plan says Redis uses WAIT for synchronous persistence, but if Redis crashes before BGSAVE completes, data is lost. Mitigation: positions are also in SQLite trades table. But the rung level would be lost, potentially causing premature exits or missed partial sells.

**Q29: The correlation brake checks pairwise correlation. What about factor-level concentration?**
A: GPT-45 rewrites the correlation brake as a "factor exposure cap (Nasdaq beta)." But the implementation details are unclear. If the system holds QQQ3.L, NVD3.L, and GPT3.L, all three have Nasdaq beta > 0.9. The pairwise correlation check catches this (ρ > 0.70 for all pairs). But if the system holds QQQ3.L, TSL3.L, and MU2.L, the pairwise correlations might be 0.65 each (below threshold) while the portfolio's effective Nasdaq exposure is still dangerously concentrated.

**Q30: Is there a maximum loss percentage beyond which the system permanently shuts down?**
A: The monthly halt is -15% (GAP-05). But there's no permanent shutdown trigger. If the system loses 15% in month 1, recovers, then loses 15% in month 2, it halts again but doesn't permanently stop. At what point does the system admit defeat? The plan doesn't specify a "total lifetime drawdown" kill switch.

## Category 3: ML Meta-Model (12 questions)

**Q31: The ML model never auto-retrains (GPT-102). How stale is the current model?**
A: If should_retrain() never fires, the model was trained once during initial setup and has been stale ever since. In a year of trading, market conditions change significantly. A stale model is worse than no model — it provides false confidence.

**Q32: What features does the ML model use?**
A: The plan references "15 features" in the risk shell diagram but doesn't list them. R15 found `_REGIME_MAP` uses invalid strings, causing regime features to encode -1. The actual feature set is undocumented in the plan.

**Q33: How does the ML meta-model interact with the 33-gate gauntlet?**
A: The ML gate is one of the 33 gates. If ML says "reject" but all other 32 gates say "pass," the trade is rejected. The ML model has absolute veto power. This is correct but means a broken ML model (always-reject or always-pass) can silently cripple or endanger the system.

**Q34: What is the ML model's false positive rate?**
A: Unknown. The plan specifies AUC monitoring (flag if < 0.55) and precision/recall in walk-forward logs, but the current false positive rate has never been measured on live data.

**Q35: The SHAP stability filter saves post-SHAP features with pre-SHAP-trained model (GPT-59). What's the actual impact?**
A: If the model was trained on 15 features and SHAP pruning removes 3, the saved model expects 15 features but inference provides 12. This causes either: (a) dimension mismatch error (crash), or (b) silent fallback to a default prediction. Either way, the ML gate is non-functional.

**Q36: De Prado meta-labeling — is the implementation correct?**
A: GPT-103 found meta_label() uses invalid regime strings. The meta-labeling threshold for RISK_OFF (which should be strict at 0.85) falls through to the default 0.65 (permissive). This means the ML gate ALLOWS trades during RISK_OFF that it should reject.

**Q37: Can the ML model overfit to the paper trading period?**
A: Yes. If the model trains on 63 days of paper data, it has at most ~60 trade outcomes. Training LightGBM+XGBoost on 60 samples is guaranteed overfitting. The plan's N<500 fallback to LogReg (v13.1) addresses this, but it hasn't been implemented.

**Q38: What happens when the ML model disagrees with the regime classifier?**
A: The ML gate and regime gate are independent. If ML says "high confidence trade" but regime says RISK_OFF (Kelly=0.0), the trade is blocked by Kelly sizing (zero allocation). The gates are redundant in this direction, which is correct (defense-in-depth).

**Q39: Is there a model risk management framework?**
A: The plan references MODEL_RISK_MRM_SPEC.md (40K) in the archive. This is one of the predecessor documents. The plan doesn't integrate MRM principles into the main body.

**Q40: What is the ML model's expected degradation rate?**
A: Without auto-retraining, the model degrades as market conditions change. Typical ML model half-life in financial markets is 3-6 months. After 6 months, the model's predictions are essentially random. The CUSUM alpha reaper should detect this, but if the reaper also relies on stale model features, it too is compromised.

**Q41: The walk-forward validation (§5.2) uses 3 splits. Is this sufficient?**
A: Three splits provide only 3 out-of-sample AUC estimates. The variance of 3 samples is enormous. You cannot reliably estimate model quality from 3 data points. Standard practice is 5-10 splits minimum (Pardo 2008 recommends 6-12).

**Q42: How does the ML model handle regime transitions?**
A: The model is trained on a fixed feature set that includes a regime feature. If the regime feature is broken (GPT-58: always encodes -1), the model has no regime awareness. It treats SHOCK and TRENDING_UP identically.

## Category 4: Execution & Market Microstructure (13 questions)

**Q43: The signal queue has NO CONSUMER (GPT-12). So how do trades currently execute?**
A: Despite the dead queue, trades actually execute via direct VirtualTrader calls in the scan loop. The queue was an architectural improvement that was never completed. Trades work without the queue — they just don't have priority ordering or backpressure protection.

**Q44: What is the actual execution latency from signal generation to position opening?**
A: Signal → gauntlet evaluation → DynamicSizer → VirtualTrader.open_position(). In paper mode, this is nearly instant (<100ms). In live mode, this would add broker API latency (~500ms-2s for IBKR).

**Q45: How does the system handle partial fills on limit orders?**
A: The plan specifies limit orders for Phase 2+ but doesn't address partial fills. If a £7,500 limit order only fills £3,000, the system holds a smaller position than intended. The profit ladder rungs may not trigger correctly on undersized positions.

**Q46: The random entry delay (GPT-52) is 0-300s. Five minutes of delay on a momentum signal could miss the entire move.**
A: Correct. A 5-minute delay on a 3x ETP moving +2% in 10 minutes means entering at +1% instead of 0%, halving the profit potential. The delay should be adaptive: shorter in high RVOL (market moving fast), longer in low RVOL.

**Q47: LSE closing auction bypass (Phase 0) — what time exactly does the system stop entering?**
A: The plan says "16:20 UK." But LSE's continuous trading ends at 16:30, with a random close auction between 16:30-16:35. Stopping entries at 16:20 gives only 10 minutes for the entry to work before the close. This may be too late for most setups.

**Q48: What is the actual spread cost on ISA ETPs during midday (low volume)?**
A: The plan uses 40bps as the round-trip spread. But midday spreads on thinly traded ETPs (e.g., SP5L.L, MU2.L) can widen to 80-120bps. The 40bps figure is an average that understates midday costs.

**Q49: The exit loop runs every 10 seconds (GPT-49). Can a 3x ETP move enough in 10s to skip a rung?**
A: On CPI release day at 13:30 UK, a 3x ETP can move 2-3% in 10 seconds. The profit ladder evaluates at 10s intervals, so it's possible to jump from Rung 1 to Rung 3 in a single evaluation, skipping the Rung 2 partial sell entirely. The plan should specify that rung evaluation checks ALL rungs, not just the next one.

**Q50: How does TWAP execution (Phase 4) interact with the 60-second scan cycle?**
A: If a TWAP order takes 30 minutes to fill, the position is partially open during those 30 minutes. The risk management system needs to account for the partial position. The plan doesn't address in-flight orders.

**Q51: What happens if the LSE halts a ticker mid-session?**
A: If QQQ3.L is halted (this happens occasionally on leveraged ETPs), the system can't exit. The Chandelier trail can't fire because there are no price updates. The position is frozen until the halt lifts. The plan's Key C (execution venue compatibility) should catch this, but it only checks "volume > 0" — a halted ticker might still show yesterday's volume.

**Q52: The Stoikov urgency function assumes a 6.5-hour session (390 minutes). But LSE ETPs can be traded on other venues (BATS, Turquoise) with different hours. Is this accounted for?**
A: No. The urgency function is hardcoded to a 390-minute session. If the system ever trades on BATS Europe (which has different hours), the urgency calculation would be wrong.

**Q53: How does the system handle stock splits or reverse splits on ETPs?**
A: Not addressed. If QQQ3.L reverse-splits 10:1, the historical price data becomes discontinuous. The ATR, RVOL, ASER, and all technical indicators would produce garbage values until the lookback window clears.

**Q54: What is the maximum order size the ISA broker (IBKR/T212) will accept for leveraged ETPs?**
A: Not specified. Some brokers limit ISA order sizes for leveraged instruments. IBKR may reject orders above a certain notional value in ISA accounts. The plan should verify this with the broker.

**Q55: Anti-adversary random partial exit (GPT-53) randomizes 25-40% bank. Does the Kelly calculation account for variable bank percentage?**
A: The Kelly derivation assumes 33% bank (fixed). Randomizing to 25-40% changes the blended average win. At 25% bank, less is secured; at 40%, more is secured but less trails. The Kelly sensitivity analysis should be redone for the randomized range.

## Category 5: Regime Classification (10 questions)

**Q56: The HMM has 3 latent states. How are these mapped to 8 observable regimes?**
A: 3 latent states (RISK_ON, NEUTRAL, RISK_OFF) → 8 observable via rule-based overlays: TRENDING_UP_STRONG, TRENDING_UP_MOD, RANGE_BOUND, TRENDING_DOWN_MOD, TRENDING_DOWN_STRONG, HIGH_VOLATILITY, RISK_OFF, SHOCK. The mapping uses VIX thresholds + trend indicators + ADX readings. This is pragmatic but complex.

**Q57: What is the HMM's accuracy on out-of-sample data?**
A: Unknown. The HMM has never been backtested against labeled regimes. There's no ground truth for "correct" regime — it's a latent variable model. The plan should define success criteria for the HMM (e.g., "portfolio performance is monotonically better in higher-regime-multiplier states").

**Q58: Can the regime classifier be fooled by a slow crash (VIX rising 1 point per day for 20 days)?**
A: The VIX thresholds are absolute: >25 = HIGH_VOL, >35 = RISK_OFF. A slow VIX rise from 20 to 35 over 20 days would transition through HIGH_VOL (at VIX 25) and RISK_OFF (at VIX 35) correctly. The 3-tick confirmation buffer ensures each transition is deliberate.

**Q59: The drought state machine has a quality threshold decay. Can this create a death spiral?**
A: At DROUGHT_CRITICAL, quality decays by 2 pts/day from 65 to minimum 50. A quality-50 trade is by definition marginal. If the system takes marginal trades that lose, it stays in drawdown, which tightens the circuit breakers, which makes it harder to trade, which extends the drought. There's a potential feedback loop, but the absolute floor of 50 prevents infinite decay.

**Q60: What happens when the two regime classifiers (HMM and rule-based) disagree?**
A: The plan says "if both return the same regime for >24h, raise alert" (Stuck Detection, GPT-82). But it doesn't say what happens when they DISAGREE. Which one wins? The plan should specify a disambiguation rule.

**Q61: The proportional VIX deadband (GPT-46) is 15% of current VIX. At VIX=15, deadband = 2.25 points. Is this too wide?**
A: At VIX=15, the range for HIGH_VOL entry is 25 (entry) to 22.75 (exit). That's a 2.25-point hysteresis band. Given VIX daily moves average 1-2 points at low levels, a 2.25-point band means the system could stay in HIGH_VOL for 1-2 days after VIX drops below 25. This is slightly conservative but acceptable.

**Q62: Can a flash crash trigger SHOCK (VIX>45 AND delta>10) on stale data?**
A: GPT-10 adversarial test covers this: "VIX spike with stale timestamp → NO flatten." But the implementation doesn't exist yet (it's a plan-only acceptance test).

**Q63: What regime does the system assign during UK bank holidays when the LSE is closed?**
A: Not specified. If the system runs the scan cycle during a bank holiday, yfinance returns no data, the regime classifier has no input, and it falls back to... what? GPT-100 says the default fallback is fail-OPEN (vix=0.0, regime="NEUTRAL") which is wrong. This should be fail-CLOSED.

**Q64: How does regime classification work during the US-UK overlap (14:30-16:30 UK)?**
A: During overlap, both LSE and US markets are open. VIX is live (US), and LSE ETPs are actively traded. The regime classifier uses VIX (US-based) which may not reflect LSE-specific conditions. A US sell-off affects LSE ETPs immediately but UK-specific events (BOE rate decisions) may not move VIX.

**Q65: Is there a regime for "no data" (data feed outage)?**
A: No. The plan has READY/DEGRADED/HALTED for startup, but no regime state for mid-session data loss. The system should have a DATA_SUSPECT regime that triggers exit-only mode.

## Category 6: Infrastructure & Operations (15 questions)

**Q66: How long does the daily S3 backup take, and does it impact trading?**
A: The backup script copies SQLite + outcomes + Redis AOF. At current data sizes, this is <5 minutes. It runs at 03:00 UTC (outside market hours). No trading impact.

**Q67: What is the Docker container restart time?**
A: Docker restart = stop + start ≈ 10-30 seconds. During this time, no scanning, no exit management, no kill switch (except Dead Man's Switch). If a restart occurs during market hours, all positions are unmanaged for 30 seconds.

**Q68: What happens when the EC2 instance runs out of CPU credits (t3.small burstable)?**
A: At baseline performance (20% of a vCPU), the scan cycle may take longer than 60 seconds, causing overlap with the next cycle. APScheduler should handle this (skipping overlapping executions) but the exit loop (10s) would also slow down, increasing response time to price changes.

**Q69: Is there monitoring for scan cycle duration?**
A: The plan mentions scan_health.json but doesn't specifically track cycle_duration_ms. If the scan cycle consistently exceeds 60 seconds, the system falls behind real-time. This should be a P1 metric.

**Q70: What is the disaster recovery plan?**
A: Daily S3 backups preserve data. But the plan doesn't specify an RTO (Recovery Time Objective) or RPO (Recovery Point Objective). If the EC2 instance dies:
- RPO = last S3 backup (up to 24h of data loss)
- RTO = time to spin up new instance + restore = 30-60 minutes
Both are acceptable for paper trading but may not be for live trading.

**Q71: The plan uses SQLite. What happens at 100,000+ trades?**
A: SQLite performance degrades with concurrent writes. The plan mentions PostgreSQL migration in Phase 4. But during the 63-day paper phase, SQLite is fine (max ~200 trades).

**Q72: How is the Telegram bot authenticated?**
A: Not specified in the plan. Telegram bots use a token. If the token is exposed, anyone can send commands to the bot (including kill switch commands). The token should be in .env (not committed to git).

**Q73: What happens if the Docker host runs out of disk space?**
A: Startup Readiness Gate checks disk space (>20% free). But mid-session, logs and data can fill the disk. The plan should specify log rotation and old artifact cleanup.

**Q74: Is there a health check endpoint for the Docker container?**
A: The command_center/server.py likely has an API. Docker HEALTHCHECK should ping this endpoint. If the endpoint stops responding, Docker restarts the container. The plan doesn't specify the HEALTHCHECK configuration.

**Q75: How does the system handle daylight saving time (DST) transitions?**
A: `delivery/dst_anchor.py` exists (4.9K). But the plan doesn't detail how DST affects session boundaries. UK clocks change twice yearly; US clocks change on different dates. The US-UK overlap window shifts by 1 hour during the mismatch weeks.

**Q76: What happens to Redis data if the Docker volume is accidentally deleted?**
A: Redis data includes position state, rung levels, and recovery multipliers. Volume deletion = total state loss. Positions would need manual reconciliation against SQLite trade records. The plan should specify Redis data backup frequency (ideally every scan cycle via BGSAVE).

**Q77: The plan indexes 384 files. How many have unit tests?**
A: 18 test files exist in tests/. For 250+ Python code files, that's <7% test coverage by file count. Many critical modules (dynamic_sizer, regime_classifier, profit_ladder) may have no tests.

**Q78: Is there a staging environment for testing changes before production?**
A: Not specified. Changes appear to go directly to the production EC2 instance. A staging environment (even a second Docker Compose on localhost) would catch deployment issues.

**Q79: How are secrets managed (API keys, Telegram tokens)?**
A: .env files. But .env is in .gitignore — verified. The plan has SECURITY_AND_SECRETS_SPEC.md in the archive but doesn't integrate its recommendations into the main plan.

**Q80: What is the maximum concurrent API request rate to yfinance before throttling?**
A: yfinance rate limits are undocumented and change without notice. Empirically, ~2000 requests/day is safe. At 12 tickers × 60 scans/hour × 8.5 hours = 6,120 individual price requests/day. This exceeds the safe rate. The plan uses batch downloads (50 tickers per call) to mitigate, but the actual rate limit behavior is unpredictable.

## Category 7: Strategic & Business Risk (20 questions)

**Q81: What is the expected Sharpe ratio of this system?**
A: At 2%/day with ~2% daily volatility: daily SR ≈ 1.0. Annualized: 1.0 × √252 ≈ 15.9. This is unrealistically high. Even the best quant funds achieve annualized SR of 2-3. An SR of 15.9 would imply the system is the most profitable trading strategy ever devised. The plan should present more conservative SR expectations.

**Q82: Has anyone ever compounded at 2% daily for a sustained period?**
A: No documented case exists of any individual or institution compounding at 2% daily for more than a few weeks. The plan acknowledges this in the scenario table (showing lower effective returns) but the mandate still says "2%+ daily." This creates unrealistic expectations.

**Q83: What happens when the strategy's edge decays?**
A: CUSUM alpha reaper detects edge decay. But the plan doesn't specify what happens AFTER detection. Options: retrain ML, switch strategy weights, increase cash allocation, halt the system. The plan should specify a decision tree for edge decay response.

**Q84: Can market makers detect and front-run the system's patterns?**
A: At £10K equity, the system's orders are invisible to market makers. At £100K+, patterns (same ticker, same time, same direction daily) become detectable. GPT-52 adds random entry delay (0-300s) to mitigate. This is adequate for current scale.

**Q85: What is the system's capacity before it starts degrading returns?**
A: Section 7 (Liquidity Scaling) addresses this thoroughly. At £500K, market impact becomes measurable. At £1M, TWAP is mandatory. At £3M, the LSE ETP universe is too small. This is well-analyzed.

**Q86: Are there regulatory risks with automated trading inside an ISA?**
A: HMRC doesn't restrict trading frequency inside ISAs. But if HMRC determines the ISA is being used as a "business" rather than "personal savings," they could challenge the tax-free status. High-frequency trading in an ISA could attract attention. The plan should note this risk.

**Q87: What happens if one of the 12 ISA ETPs is delisted?**
A: Key C (execution venue compatibility) checks for delisting. The ticker would be quarantined. But if a core ETP (e.g., QQQ3.L) is delisted, the universe shrinks and opportunity decreases. The plan has no contingency for core ticker loss.

**Q88: What is the expected annual return after accounting for realistic trading frequency?**
A: At 3 trades/week, 48 weeks/year = 144 trades. At 55% WR and +5.1% avg win / -3% avg loss: Expected P&L per trade = 0.55 × 5.1 - 0.45 × 3 = 2.805 - 1.35 = +1.455%. But this is on the risked portion (0.75% of equity). Expected portfolio return per trade = 1.455% × 0.75% / 3% ≈ 0.36%. Over 144 trades: (1.0036)^144 ≈ 1.68 = +68%. £10K → £16,800. This is a realistic Year 1 estimate — not £1.48M, not £102K, but ~£17K.

**Q89: What is the tax implication if the ISA wrapper is voided?**
A: If HMRC voids the ISA (e.g., due to non-eligible instrument trading), ALL gains become taxable retroactively. At 20% CGT above the £3,000 annual allowance: on £6,800 gain, tax = (6,800 - 3,000) × 0.20 = £760. At higher gains, the tax bill grows proportionally.

**Q90: Does the plan account for ETP tracking error?**
A: Mentioned in Section 4.4 (variance drag) but not explicitly modeled. 3x ETPs typically have 0.1-0.3% daily tracking error versus their stated leverage. Over time, this compounds against the system.

**Q91: What happens if the plan achieves its targets and equity reaches £100K+?**
A: The plan has scaling tables (Section 7, Table C) up to £3M+. This is well-planned. But the transition from £10K to £100K involves psychological challenges (drawdowns measured in £000s, not £10s) that aren't addressed.

**Q92: Is there a backup strategy if S15 (the core strategy) stops working?**
A: 15 other strategies exist in the codebase, but all are dormant in V2 (ISA-only mode). If S15 fails, the system has no fallback. The plan should specify conditions for activating backup strategies.

**Q93: How does the system handle earnings releases on underlying stocks?**
A: The earnings calendar and earnings fade gate are implemented. But 3x ETPs don't have "earnings" — their underlyings do. If NVIDIA has earnings, NVD3.L will gap. The system needs to map ETP → underlying → earnings calendar. The plan addresses this via `_ISA_TO_UNDERLYING` mapping (which has phantom ticker issues per GPT-15).

**Q94: What is the opportunity cost of the capital locked in the ISA?**
A: £10K in a high-yield savings account earns ~4.5% annually (£450). The system must beat this risk-free alternative to justify the complexity and risk. Any outcome below 4.5% annual return means the system destroyed value.

**Q95: Can the system adapt to a new market regime (e.g., zero interest rate environment)?**
A: The ML meta-model should adapt via retraining, but it never auto-retrains (GPT-102). The regime classifier is based on VIX thresholds which are historically stable. The system would likely struggle in a very low-volatility environment where 3x ETPs barely move.

**Q96: What is the plan's bus factor?**
A: 1. The system is built and maintained by a single person. If that person is unavailable, the system runs unsupervised. The Dead Man's Switch (not implemented) partially addresses this, but there's no handover documentation or operational runbook for a replacement operator.

**Q97: How many hours per day does the system require human oversight?**
A: Per §8C checklists: Morning (30 min) + Midday (15 min) + Evening (15 min) = ~1 hour/day. Plus ad-hoc P0/P1 alerts. During paper trading, this is acceptable. During live trading with real money, the psychological overhead is much higher.

**Q98: What is the total cost of running the system (before any trading revenue)?**
A: EC2 t3.small ≈ $15/month. S3 backup ≈ $1/month. Telegram bot: free. yfinance: free. No data subscriptions in Phase 0-2. Total: ~$16/month = £13/month = £156/year. The system needs to make >£156/year to be economically viable. This is easily achievable.

**Q99: Is the 63-day paper trading period statistically sufficient?**
A: At 3 trades/week, 63 days ≈ 27 trading days ≈ 9 weeks ≈ 27 trades. Testing a binary hypothesis (WR ≥ 50%) with n=27 has very low statistical power. You need ~100 trades to distinguish 55% WR from 50% WR with 80% power. The 63 MTRL days should be interpreted as "63 trades minimum" not "63 calendar days."

**Q100: What is the single most likely failure mode for this system?**
A: **Silent model degradation.** The ML model never retrains (GPT-102). The regime classifier has broken features (GPT-58, GPT-103). The correlation brake is bypassed for ISA tickers (GPT-105). The system will appear to function correctly — scan cycles complete, logs look normal, no crashes — while the underlying decisions become progressively worse. The first sign of failure will be a drawdown that triggers L1/L2, at which point the damage is already done. The plan addresses this with CUSUM, weekly reports, and shadow tracking, but none of these are implemented in code.

---

# PART III: NEW AMENDMENTS (R20)

| # | ID | Title | Priority | Finding |
|---|---|---|---|---|
| 1 | CQO-01 | Kelly rung probability Go-Live gate | P0 | Add hard gate: empirical Rung 2 reach ≥ 75% over 100+ wins before live |
| 2 | CQO-02 | Trading frequency in scenario table | P1 | Model trades/week (not 252 days) in scenario projections |
| 3 | CRO-02 | D-1 daily loss vs Constitutional L1/L2 reconciliation | P0 | Four daily loss thresholds exist; reconcile to three |
| 4 | CRO-04 | Max positions vs R4 deployment cap | P0 | BULL=7 violates R4 40%. Cap at min(regime_limit, 4) |
| 5 | CRO-05 | Kill switch specification | P0 | Define kill switch architecture: triggers, latency, mechanics |
| 6 | LSA-01 | Price feed cross-validation | P1 | Add secondary price source to detect yfinance errors |
| 7 | LSA-04 | Graceful shutdown handler | P1 | Add SIGTERM handler for position preservation |
| 8 | LSA-05 | Stale LOC count in header | P2 | Update 15,700 → 131,254 LOC |
| 9 | APR-02 | Kelly assumption violations | P2 | Document 5 assumption violations and Half-Kelly justification |
| 10 | Q88 | Realistic Year 1 return estimate | P1 | Add trading-frequency-adjusted projection (~£17K, not £1.48M) |

---

# PART IV: FINAL ASSESSMENT

## What 18 Rounds Got Right
1. The risk architecture is genuinely institutional-grade (when implemented)
2. The Constitutional hierarchy is novel and powerful
3. The profit ladder mathematics are sound (given validated rung probabilities)
4. The operational procedures (checklists, drills, limited live) are comprehensive
5. The adversarial review process itself is unprecedented in rigour

## What 18 Rounds Missed
1. **The system has never traded** — all analysis is theoretical
2. **Kelly probabilities are assumed, not measured** — the math proves nothing without data
3. **D-1 vs L1/L2 daily loss threshold conflict** — 4 different values exist
4. **Max positions (7) violates R4 (40% cap)** — arithmetic contradiction
5. **No kill switch specification** despite 15+ references
6. **Trading frequency not modeled** — projections assume daily trading (unrealistic)
7. **Realistic Year 1 return is ~£17K, not £102K-£1.48M** — when trading frequency is accounted for
8. **yfinance has no fallback for price data** — single point of failure for the entire system
9. **No graceful shutdown procedure** — positions orphaned on Docker stop
10. **LOC count is wrong in the header** — minor but indicates stale metadata

## The Uncomfortable Truth

This plan has been reviewed by 3 AI models across 18 rounds with 116+ amendments. It is the most thoroughly reviewed trading system specification I've ever encountered. And yet: **not one line of code has been changed by any of these reviews.** The system running on EC2 right now is the same code that existed before R1.

The priority is not more reviews. The priority is the 10-fix sprint defined in the Architect's Ruling. Every additional review round that doesn't produce code changes is plan completion theater (GPT-16).

**Recommendation: STOP REVIEWING. START CODING.**

---

**Prepared by:** Claude Opus 4.6 (Independent Adversarial Auditor, R20)
**Date:** 2026-03-06
**Classification:** INTERNAL — NZT-48 Adversarial Review
