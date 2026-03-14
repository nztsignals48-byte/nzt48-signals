# R19 ADVERSARIAL AUDIT PROMPTS

**Purpose**: Two independent adversarial audits of AEGIS Master Plan v13.16 + codebase. Copy-paste each prompt into the respective AI. Attach the master plan PDF/MD and any relevant code files.

---

## FILES TO ATTACH TO BOTH PROMPTS

1. `AEGIS_MASTER_PLAN_v13_FINAL.md` (the plan — 8,089 lines)
2. `R15_COMPREHENSIVE_AUDIT.md` (deepest code audit — 16 amendments)
3. `R17_QUALITY_VERDICT.md` (kill-or-keep verdicts on every predecessor addition)
4. `PREDECESSOR_WISDOM_TRACKER.md` (205 items cross-referenced)
5. Key code files (attach as many as the context window allows, in priority order):
   - `main.py` (orchestrator, ~7700 lines)
   - `qualification/dynamic_sizer.py` (1,486 lines)
   - `core/ml_meta_model.py` (773 lines)
   - `feeds/regime_classifier.py` (484 lines)
   - `execution/virtual_trader.py` (~2000 lines)
   - `qualification/circuit_breakers.py` (~350 lines)
   - `qualification/risk_sizer.py` (~400 lines)
   - `core/trading_discipline.py` (437 lines)
   - `core/chandelier_exit.py` (~200 lines)
   - `qualification/profit_ladder.py` (~300 lines)
   - `core/cross_asset_macro.py` (~300 lines)
   - `config/settings.yaml` (1,082 lines)

---

## PROMPT 1: GEMINI 2.5 PRO (R19-G)

```
You are performing a ROUND 19 FORENSIC ADVERSARIAL REVIEW of the AEGIS Alpha-Omega Master Plan v13.16 and its live codebase (131,254 LOC across 298 Python files). This plan has survived 18 review rounds and 116+ amendments across 3 AI models. Round 18 resolved 15 contradictions and codified the Architect's 10-fix priority sprint. Round 19 is YOUR independent audit.

YOU HAVE ONE JOB: Find what 18 rounds missed.

CONTEXT:
- System: Automated leveraged ETP trading engine targeting 2% daily compounding in a UK ISA (£10K → £1.48M/year theoretical)
- Architecture: EC2 t3.small, Docker Compose (engine + API + Redis + Dashboard), APScheduler 60s scan loop
- Universe: 12 LSE-listed leveraged ETPs (QQQ3.L, 3LUS.L, 3SEM.L, GPT3.L, NVD3.L, TSL3.L, TSM3.L, MU2.L, QQQS.L, 3USS.L, QQQ5.L, SP5L.L)
- Data: yfinance (60s polling, no Level 2, no real-time feed)
- Strategy: S15 "2% Daily Target" — score all 12 tickers by "2% reachability", take the BEST one per day
- Kelly: Blended avg win ≈ +5.0% (from VT inline 6-rung profit ladder), Kelly = 0.28 at 55% WR, 0.75% per-trade risk cap (immutable)
- Risk: 10-layer Risk Shell, 33-gate gauntlet, Constitutional circuit breakers (L1=-1.5%, L2=-2.5%, L3=-4.0%), weekly -8%, monthly -15%
- ML: LightGBM+XGBoost ensemble with SHAP stability filtering, De Prado meta-labeling
- Current status: 80 unfixed items (20 P0, 18 P1, 12 P2 code bugs + 19 plan-only + 11 plan gaps). 10-fix priority sprint about to begin.

KNOWN CRITICAL BUGS (the "5 Silent Killers" — DO NOT re-report these, they are already scheduled for fixing):
1. GPT-111: SessionProtection at +1.5% prevents 2% target (353x terminal wealth difference)
2. GPT-104: List mutation during iteration skips signals
3. GPT-102: should_retrain() TypeError — ML never auto-retrains
4. GPT-55: asyncio.QueueFull vs queue.Full exception mismatch
5. GPT-105: ISA correlation families empty — correlation brake bypassed

NON-NEGOTIABLE RULES:
1. NO THEORETICAL PRAISE. You are conducting a hostile audit.
2. EVERY finding must cite specific numbers, parameter values, or line references from the plan or code.
3. Show the exact mathematical derivation for any calibration error you find.
4. Do NOT re-report known bugs listed above. Find NEW ones.
5. If a rule has no enforcing code, mark it "PAPER ONLY" and treat as a fatal flaw.
6. Assume Phase A/B data feeds only (no Level 2). Do NOT propose L2-dependent fixes.

ADOPT 4 PERSONAS SIMULTANEOUSLY:
- PERSONA 1 — Chief Quant (30y, ran $2B pod): Focus on compounding precision errors, variance drag, degrees-of-freedom inflation
- PERSONA 2 — Lead Systems Architect (exchange-grade HFT infrastructure): Focus on race conditions, deadlocks, data freshness, single-writer invariants
- PERSONA 3 — Chief Risk Officer (former market maker, leveraged ETP specialist): Focus on correlated failures, gap risk, cascading halts, bad fills
- PERSONA 4 — Academic Reviewer (published on leveraged ETP decay): Focus on model-use boundaries, statistical power, epistemological limits

EXECUTE THESE 7 INSTITUTIONAL PROCEDURES:

PROCEDURE 1 — MODEL RISK MANAGEMENT (MRM) ASSESSMENT:
Catalogue every model (S15 scoring, DynamicSizer, Kelly, HMM regime classifier, CUSUM alpha reaper, Kinetic Time-Stop, ML meta-model, Bayesian Stranger). For each: inputs, outputs, assumptions, limitations, materiality tier. Define Observable Pass/Fail metrics for the first 63 paper trading days.

PROCEDURE 2 — INDEPENDENT VALUATION VERIFICATION (IVV):
The plan claims +2%/day. Decompose this into: alpha, beta exposure (NASDAQ 3x), variance drag (L²σ²/2), spread cost (40bps round-trip on 3x ETPs), slippage, and commission. What is the REALISTIC expected daily return after all frictions? Map the ±0.15% uncertainty of 60-second yfinance polling to P&L variance.

PROCEDURE 3 — STRESS TESTING & SCENARIO ANALYSIS:
Determine survival (DD < Constitutional limits) and specific risk control activations for:
* COVID Crash (2020-03-09 to 2020-03-23): NASDAQ -30% in 2 weeks
* Volmageddon (2018-02-05): VIX from 17 to 50 intraday
* Flash Crash (2015-08-24): S&P -5% in minutes, circuit breakers triggered
* Orderly Bear (2022-01-24 to 2022-03-14): NASDAQ -20% over 7 weeks
* yfinance dark for 4 hours during a -5% underlying move
* LSE halts all leveraged ETPs for 2 hours
* Redis crashes and flushes all state (Chandelier, circuit breakers, regime)
* REVERSE STRESS TEST: The exact smallest sequence of events that causes >15% drawdown

PROCEDURE 4 — OPERATIONAL RISK ASSESSMENT:
Map Blast Radius and Recovery Time for: EC2 instance death, yfinance API failure, Docker OOM killer, Redis data loss, operator absence (48 hours). Define 10 Key Risk Indicators (KRIs) with warning/critical thresholds.

PROCEDURE 5 — BEST EXECUTION & ADVERSARY REVIEW:
How would Jane Street / Optiver detect this specific 1-trade/day order flow on LSE leveraged ETPs? Define the exact footprint AEGIS leaves. What countermeasures prevent systematic spread-widening?

PROCEDURE 6 — PRE-TRADE RISK LIMIT FRAMEWORK:
Construct a prime-broker-grade limit schedule: max position % equity, max daily loss, max VaR, max leverage. Mathematical derivation for every limit.

PROCEDURE 7 — COUNTERPARTY & FUNDING LIQUIDITY RISK:
Calculate the "Death Zone" equity threshold where minimum broker commissions mathematically destroy Kelly EV. Map liquidity evaporation of LSE ETPs during stress events.

THEN REVIEW ALL 16 PLAN SECTIONS:
For each, provide: (A) Logical/Precision Errors, (B) State Machine Deadlocks, (C) Fixes with concrete code-ready solutions.

1. KELLY PAYOFF RESOLUTION (GPT-29/101 — blended avg win +5.0%, ladder vs flat payoff)
2. RISK STATE MACHINE (HALT > FLATTEN > REDUCE > NORMAL, GPT-30)
3. GAP & AUCTION RISK CONTROLS (GPT-33)
4. SIGNAL STALENESS CONTROLS (GPT-39)
5. DEAD CODE AUDIT (ChandelierExit, signal queue consumer, R-10 Anti-Cascade)
6. EMERGENCY FLATTEN (-5% portfolio / -15% position, GPT-32/40)
7. SETUPFINGERPRINT DIMENSIONALITY (GPT-34)
8. PROFIT LADDER (VT inline 6-rung ETP ladder — the ACTUAL firing ladder)
9. DYNAMICSIZER 8-FACTOR KELLY (half-Kelly base, regime multipliers 0.0-0.6)
10. BAYESIAN STRANGER PENALTY (kappa shrinkage, n_0=50, lambda=0.5)
11. 8-INDICATOR S15 CONSENSUS
12. GO-LIVE GATE (12 criteria + 27 stop-ship items + failure drills)
13. NIGHTLY ACTIVATION + BASE-RATE GATE (walk-forward selection, beta-binomial posterior)
14. EV GATE + GAUNTLET (positive-EV-after-friction, 33-gate pipeline)
15. REGIME CLASSIFIER + VIX HYSTERESIS (3 HMM latent → 8 observable, VIX 25/35/45)
16. DRAWDOWN CASCADE + CIRCUIT BREAKERS (L1/L2/L3 intraday + accumulated cascade)

THEN ANSWER ALL 100 QUESTIONS (provided below).

FINAL DELIVERABLES (as distinct sections):
A) "STATE MACHINE DEADLOCK REPORT" — every race condition and contradiction in the command tree
B) "PRECISION ERROR BUDGET" — total expected annual return deviation from targets
C) "ADVERSARY EXPLOITATION PLAYBOOK" — how a market maker extracts your alpha
D) "WHAT WOULD YOU DO DIFFERENTLY?" — if you had to rebuild this system from scratch, what architecture would you choose?
E) "R19-G AMENDMENT PACK" — exact text changes to the plan for each accepted fix, tagged GPT-117+

ANSWER ALL 100 QUESTIONS BELOW:

[100 QUESTIONS SECTION FOLLOWS AT THE END OF THIS DOCUMENT]
```

---

## PROMPT 2: CHATGPT (R19-C)

```
You are performing a ROUND 19 ADVERSARIAL REVIEW of the AEGIS Alpha-Omega Master Plan v13.16 and its live Python codebase. This plan has survived 18 review rounds, 116+ amendments, and audits by Claude Opus 4.6, Gemini 2.5 Pro, and ChatGPT across 3 months. Round 18 resolved 15 contradictions and codified a 10-fix priority sprint. Round 19 is YOUR fresh audit.

YOUR MISSION: Attack IMPLEMENTATION and "COMMAND TREE FIREPOWER." At every decision node, can the system actually enforce the rule with the data + timing it has?

SYSTEM OVERVIEW:
- Automated leveraged ETP trading engine: 2% daily compounding, UK ISA, £10K starting equity
- 12 LSE-listed leveraged ETPs (QQQ3.L, 3LUS.L, 3SEM.L, GPT3.L, NVD3.L, TSL3.L, TSM3.L, MU2.L, QQQS.L, 3USS.L, QQQ5.L, SP5L.L)
- EC2 t3.small, Docker Compose, 60s APScheduler scan loop, yfinance data (no L2)
- S15 strategy: score all tickers by "2% reachability", take BEST per day
- Kelly = 0.28 at 55% WR after VT profit ladder (blended avg win +5.0%), 0.75% per-trade risk cap
- 10-layer Risk Shell, 33-gate gauntlet, Constitutional circuit breakers (L1=-1.5%, L2=-2.5%, L3=-4.0%)
- ML: LightGBM+XGBoost with De Prado meta-labeling, SHAP stability
- 80 unfixed items: 20 P0 + 18 P1 + 12 P2 code bugs + 19 plan-only + 11 plan gaps
- 10-fix priority sprint about to execute

KNOWN BUGS (already scheduled — DO NOT re-report):
GPT-111 (SessionProtection +1.5%), GPT-104 (list mutation), GPT-102 (should_retrain TypeError), GPT-55 (asyncio.QueueFull), GPT-105 (ISA correlation families)

MANDATORY METHOD — Adopt 4 personas simultaneously:
* PERSONA A: Chief Quant (30y, $2B+ pod) — EV after costs, variance drag, compounding precision
* PERSONA B: Lead Systems Architect (exchange-grade) — race conditions, latency, single-writer invariants, data freshness
* PERSONA C: Chief Risk Officer (leveraged ETP / market-maker mindset) — gap risk, bad fills, cascading halts, correlated failures
* PERSONA D: Model Risk / Academic Reviewer — model-use boundaries, statistical power, p-hacking, epistemological limits

OUTPUT REQUIREMENTS (STRICT):

A) COMMAND TREE FIREPOWER AUDIT (P0):
Create a table of every major decision node (data freshness gate, signal scoring, gauntlet gates, queue operations, sizing, execution, exit management, risk overrides, audit logging). For each node:
* Rule stated in plan (exact quote + section reference)
* Actual enforcing code path (file + function + line numbers) — or "PAPER ONLY" if no code
* Data dependency + update frequency
* Timing dependency (entry cadence 60s vs exit cadence)
* Failure mode if dependency breaks
* Severity (P0/P1/P2)

B) PRECISION-ERROR HUNT (P0):
Find at least 10 NEW "precision traps" (not the 5 Silent Killers already known) where a small mismatch compounds into material drawdown. For each:
* Why it's a trap
* How it manifests in production logs
* Minimal patch (≤50 LOC)
* Test to prove it's fixed

C) RISK STATE MACHINE INVARIANTS (P0):
Verify the RiskStateMachine precedence HALT > FLATTEN > REDUCE > NORMAL is enforced by a SINGLE arbiter. If multiple modules can directly flatten/halve/stop, propose a single-writer "risk arbiter" interface with exact refactor steps.

D) QUEUE + STALENESS (P0):
Prove the signal queue cannot silently drop P0 signals. Verify "market-age staleness" is enforced (now - last_bar_timestamp), not just "time since enqueue." Propose:
* signal_market_age_seconds gate
* max_signal_age_seconds gate
* Metrics: dropped_stale_count, p95 enqueue→execute latency
* Fail-closed behavior when data stale

E) EXIT SYSTEM (P0):
Audit exit priority ordering. Confirm that when multiple exits are true on same tick, the system deterministically selects highest-priority exit and logs "exit_also_true" for the rest. Verify exit cadence. If exit checks run at entry cadence only (60s), propose decoupling:
* Entry loop: 60s
* Exit loop: 10s reading cached prices (no network I/O)

F) COST REALISM (P0):
Verify there is a minimum viable trade size gate: expected_gross_pnl >= (spread + comms + slippage_buffer). Quantify how many trades would be vetoed at £10K and how this affects sample collection during 63-day paper phase.

G) DELIVERABLES:
1. "R19 Findings" list with P0/P1/P2 tags (P0 must include immediate patch guidance)
2. "v13.17 Amendment Pack" — exact text changes to the plan for each fix, tagged GPT-117+
3. "Patch Plan" — ordered list of code changes with file paths + function names + tests
4. "Stop-Ship Criteria Update" — additions to the 27-item checklist if warranted

CONSTRAINTS:
* Assume Phase A/B data feeds only (no Level 2). Do NOT propose L2-dependent fixes as P0/P1.
* Be ruthless: if a feature cannot be enforced with current data/cadence, downgrade to telemetry-only.
* No vague advice. Every fix must have concrete implementation steps and at least one test.
* Do NOT re-report the 5 known Silent Killers. Find NEW issues.

THEN ANSWER ALL 100 QUESTIONS BELOW:

[100 QUESTIONS SECTION FOLLOWS AT THE END OF THIS DOCUMENT]
```

---

## 100 ADVERSARIAL QUESTIONS (APPEND TO BOTH PROMPTS)

```
ANSWER ALL 100 QUESTIONS. Be hyper-specific, quantitative, and brutal. No hand-waving. Every answer must reference specific plan sections, code files, or mathematical derivations.

### KELLY & POSITION SIZING (Q1-Q15)

Q1. The plan claims Kelly = 0.28 at 55% WR with blended avg win +5.0%. Derive this from scratch. Show the full calculation including the profit ladder rung probabilities. What WR makes Kelly go negative?

Q2. The 0.75% per-trade risk cap is "immutable." If Kelly optimal is 0.28 (28% of equity), and the cap limits risk to 0.75%, what is the ACTUAL Kelly fraction being deployed? What is the growth rate sacrifice?

Q3. Half-Kelly is stated as the base, but "actual fractions are quarter-Kelly (25%) for 3x ETPs and fifth-Kelly (20%) for 5x ETPs." Show the mathematical derivation for why these specific fractions are optimal for leveraged products. Cite the leverage-adjusted Kelly formula.

Q4. The DynamicSizer has 8 multiplicative factors. If ALL 8 hit their minimum simultaneously (e.g., 0.0 × 0.0 × 0.5 × ... ), what is the resulting position size? Is this handled correctly, or does it produce a nonsensical micro-position?

Q5. The plan says "0.75% risk requires 133 consecutive losers for ruin." Derive this number. What assumptions does it make about stop-loss placement and position sizing?

Q6. At £10,000 equity with 0.75% risk = £75 max risk per trade, and ATR stop of 1.5x on a 3x ETP with typical ATR of 3%, what is the maximum position size in shares? Is this above or below broker minimums?

Q7. The Kelly payoff resolution (GPT-29/101) uses "blended average win ≈ +5.0%." This assumes the profit ladder fires correctly. If the profit ladder fails to bank at Rung 1 (+2%) and the trade reverses to stop-loss, what is the actual payoff? How does this change the Kelly calculation?

Q8. Variance drag for a 3x leveraged ETP is L²σ²/2. At σ_daily = 1.5%, this is (9)(0.000225)/2 = 0.10125% per day. Over 252 days, this compounds to -22.5% drag. How does the plan account for this systematic headwind?

Q9. The Regime-Kelly multipliers range from 0.0 to 0.6. TRENDING_UP_STRONG gets 0.6, not 1.0. Why not 1.0? What is the theoretical justification for capping at 0.6?

Q10. The plan specifies "minimum 30 trades per regime for stable f* estimation." At 1 trade/day across 8 regimes, how many trading days are needed to have 30 trades in EVERY regime? Is this achievable in the 63-day paper period?

Q11. SHOCK_RECOVERY counts signals not sessions (GPT-61). After the fix (decrement by date), what happens if a SHOCK event occurs on Friday afternoon? Does the 3-session recovery span the weekend, or does it expire?

Q12. The signal queue has zero consumers (GPT-12). If this is fixed by adding a consumer, what is the maximum acceptable latency between signal generation and execution? How does this interact with the 120s staleness gate?

Q13. The Bayesian Stranger Penalty uses n_0 = 50 and lambda = 0.5. Derive the kappa value at n = 10, n = 30, n = 50, and n = 100 trades. At what n does the penalty become negligible (<5%)?

Q14. The commission viability gate requires expected_gross_pnl >= 2 × (commission + spread_cost). At £10K equity, 0.75% risk, 40bps spread on a £500 position: what is the minimum R:R required for this gate to pass?

Q15. If the system compounds at 1.5% daily (not 2%) due to all frictions, what is terminal wealth after 252 days? After 504 days (2 years)?

### RISK MANAGEMENT (Q16-Q35)

Q16. The Constitutional cascade has L1=-1.5%, L2=-2.5%, L3=-4.0%. At 3x leverage, a -0.5% underlying move creates a -1.5% ETP move. How many "normal" underlying moves trigger L1? Is L1 too sensitive?

Q17. Emergency Flatten triggers at -5% portfolio DD. With 1 position at 10% of equity and 3x leverage, how much must the underlying fall to trigger this? Is -5% ever reachable with 1 position?

Q18. Circuit breaker state is not persisted to disk (GPT-90). After a Docker restart, all L1/L2/L3 state is lost. The system could restart and immediately resume trading after an L3 halt. What is the blast radius of this failure?

Q19. The Risk State Machine has 4 states: SYSTEM_HALTED > EMERGENCY_FLATTEN > REDUCE > NORMAL. But the code has no single arbiter (GPT-50). How many distinct code paths can call flatten_position()? List them all.

Q20. Weekly -8% halt and monthly -15% halt have no code implementation. During the 63-day paper phase, what is the probability of hitting -8% weekly given the daily loss limits? Show the math.

Q21. The correlation brake (R-06) uses pairwise correlation. But 8 of 12 ISA tickers are NASDAQ-3x/5x products. What is the effective portfolio correlation when holding QQQ3.L + NVD3.L + 3SEM.L simultaneously?

Q22. The CDaR breaker uses Historical Simulation VaR (GPT-43) on 252-day rolling returns. During the first 63 days of paper trading, there are only 63 data points. Is this statistically sufficient for a 95th percentile tail estimate?

Q23. The anti-adversary measures (GPT-52/53) specify random entry delay (0-300s) and randomized partial exit (25-40%). Neither is implemented. If a market maker detects the 1-trade/day pattern, what is the maximum spread-widening they could extract?

Q24. The Dead Man's Switch monitors EC2 health from an external Lambda. But this Lambda needs to be DEPLOYED and TESTED. Is there a test for it? What happens if the Lambda itself fails?

Q25. Post-recovery ramp-up (GPT-81) specifies 0.25x size for 30-60 minutes after crisis. But at 1 trade/day, the system might not trade during the ramp period at all. Is this ramp meaningful for the S15 strategy?

Q26. The drought state machine (GPT-89) decays quality threshold from 65 to 50 over extended dry periods. At threshold 50, what is the expected win rate? Does Kelly remain positive?

Q27. The plan says "no overnight holds" for all leveraged ETPs during paper/limited live (GAP-14/R5). The code only enforces overnight_kill for 5x products. 3x products can be held overnight in normal VIX. Is this a violation of the Constitutional R5?

Q28. The Kinetic Time-Stop (B-7) uses T_max = MaxDrag / (σ² × L²). At L=3, σ_daily=1.5%, MaxDrag=0.5%, what is T_max in minutes? Is this shorter than the 60s scan loop?

Q29. The plan specifies 10 Key Risk Indicators (KRIs). List all 10 with warning and critical thresholds. Are any of them currently monitored in code?

Q30. What happens if yfinance returns stale data (same bar for 10 minutes) but the timestamps look fresh? How does the system detect "data that looks fresh but isn't"?

Q31. The iCVaR gate (Layer 5) blocks if incremental CVaR > 0.5% equity. With 1 position at 10% of equity and 3x leverage, what underlying volatility triggers this gate? Is it calibrated correctly?

Q32. CUSUM alpha reaper (Layer 9) has threshold 3.0σ. How many consecutive losing trades trigger CUSUM? What is the false positive rate at this threshold?

Q33. The 3% portfolio heat cap (Layer 10) limits total risk across all positions. With 0.75% per trade and 4 positions (max in BULL regime), total risk = 3.0% — exactly at the cap. Is there any headroom?

Q34. The plan mentions "regime-stratified CV" for ML validation. How many folds are used? With 8 regimes and potentially <30 trades per regime, is cross-validation even statistically meaningful?

Q35. The factor exposure cap (GPT-45, correlation brake rewrite) limits "Nasdaq beta <= 1.5x." But ALL 12 ISA tickers are leveraged NASDAQ/S&P products. Is the Nasdaq beta of the entire portfolio always > 1.5x by construction?

### ML & SIGNAL QUALITY (Q36-Q50)

Q36. The ML meta-model uses LightGBM + XGBoost ensemble. What is the training set size after 63 days at 1 trade/day? Is 63 samples sufficient for a meaningful ensemble?

Q37. Feature leakage (GPT-104 from R12): "confidence" is both an input to the ML model AND an output used for gating. If the model learns to predict "confidence," it's predicting its own input. How is this circularity broken?

Q38. The SHAP stability filter prunes features where rank drift > 5 positions. After pruning, the model is retrained on fewer features. But the SHAP values were computed on the ORIGINAL feature set. Is there a SHAP → retrain → SHAP feedback loop?

Q39. Meta-labeling (De Prado) gates signals with a binary classifier. What is the minimum precision required for the meta-label gate to improve overall strategy Sharpe? Derive from the base strategy's false positive rate.

Q40. The ML model retrains weekly (Sundays). But should_retrain() is broken (GPT-102). Even after fixing, what is the cost of weekly retraining on a 63-sample dataset? Is there enough data for meaningful walk-forward validation?

Q41. The _REGIME_MAP (GPT-58) always encodes -1 for regime. This means the ML model has been training with a dead feature for its entire history. After fixing the map, will retraining on historical data produce a meaningful model, or is all historical training data contaminated?

Q42. The 8-indicator S15 consensus requires how many indicators to agree before generating a signal? What happens when exactly 4/8 agree (tied vote)?

Q43. The Nightly Activation Set (B-10, GPT-24) uses walk-forward strategy selection. With 16 strategies and 12 tickers, the combinatorial space is 192. At 1 trade/day for 63 days, how many strategy-ticker combinations will have ZERO observations?

Q44. The Base-Rate Gate (B-11, GPT-25) uses beta-binomial posterior gating. With n < 20 observations for a setup fingerprint, the posterior is dominated by the prior. What prior is used? Is it informative or diffuse?

Q45. SetupFingerprint (GPT-34) starts at 3 dimensions and grows to 5 as data accumulates. At 3 dimensions with 63 trades, how many unique fingerprints exist? What is the average N per fingerprint?

Q46. The plan says "reject all signals where meta-model confidence < 0.50." But meta_label() checks invalid regime strings (GPT-103), so RISK_OFF gets permissive 0.65 instead of 0.85. After fixing, how many signals would be vetoed that currently pass?

Q47. Walk-forward CV with purged combinatorial splits: how many independent test folds can be constructed from 63 daily observations with a 5-day embargo? Is this enough for reliable out-of-sample estimates?

Q48. The SHAP clustering mechanism groups similar features. If 3 of 15 features are highly correlated (e.g., RSI_14, RSI_21, RSI_60), does SHAP correctly attribute importance, or does it split importance across the cluster?

Q49. The plan specifies "Bonferroni correction for Scout discoveries." With 192 strategy-ticker combinations tested, the corrected significance level is 0.05/192 = 0.00026. How many trades are needed per combination to detect a Sharpe of 2.0 at this significance level?

Q50. The meta-model's feature set includes "confidence" as an input. If the meta-model learns to gate based on confidence alone (ignoring all other features), what is the effective behavior? Is this equivalent to just raising the confidence threshold?

### EXECUTION & TIMING (Q51-Q65)

Q51. The system uses yfinance with 60s polling. What is the maximum price staleness at the moment of order execution? If the price moved 1% in the last 59 seconds, how much slippage is expected?

Q52. LSE market hours are 08:00-16:30 UK. The 5-min opening exclusion (R12) blocks entries until 08:05. The time-decay close starts at 16:00. What is the effective trading window? How many 60s scan cycles fit in this window?

Q53. The entry velocity gate "Move or Die" (B-8, GPT-22) detects failed impulse after entry. At 60s scan cadence, what is the minimum price move detectable? Is this sufficient for 3x ETPs with typical tick sizes?

Q54. The exit loop is NOT decoupled from the entry loop (GPT-49). Both run at 60s. The Kinetic Time-Stop can produce T_max < 60s. What percentage of exits are MISSED because the check happens after the deadline?

Q55. The VirtualTrader holds a threading.RLock during yfinance calls (GPT-60). With 3 concurrent positions, each requiring a yfinance call, the lock is held for 15-60 seconds. What other operations are blocked during this time?

Q56. Order execution uses market orders (at £10K). What is the expected fill price deviation from the yfinance "last price" on LSE leveraged ETPs? Is there a NBBO check?

Q57. The profit ladder banks 33% at each rung. After banking at Rung 1 (+2%), the remaining 67% trails. If the price reverses from +2.5% to the stop (-3%), what is the net P&L? Show the full calculation.

Q58. The Chandelier Exit (5-rung) is dead code (GPT-101). The VT inline ladder (6-rung) fires instead. Are there any other "dead exit" mechanisms that the system evaluates but never acts on?

Q59. The time-decay close at 16:00-16:25 UK uses "linear urgency ramp." What is the mathematical formula? At 16:20, what percentage of the position is force-closed?

Q60. If two ISA tickers both generate qualifying S15 signals in the same scan cycle, the system takes "the BEST one." What is the scoring function? Is it a total ordering (no ties possible)?

Q61. The anti-adversary random delay (GPT-52, 0-300s uniform) is not implemented. At 1 trade/day always executed within the first few minutes of the session, how many days before a market maker identifies the pattern?

Q62. The plan mentions "LIMIT orders only" for LIMITED LIVE. But S15 generates signals based on yfinance "last price." If a limit order is placed at the last price, what is the fill probability on LSE ETPs with 40bps spreads?

Q63. WHALE MODE activates when RVOL > 1.5 at the current profit ladder rung. What does WHALE MODE do differently? Is there code for it, or is it plan-only?

Q64. The overnight_kill for 5x products triggers at 16:15 UK. But LSE closing auction runs from 16:30-16:35. Should the kill happen BEFORE or AFTER the auction? What is the optimal execution timing?

Q65. The profit ladder has different RVOL thresholds (1.2 in profit_ladder.py vs 1.5 in virtual_trader.py). Which one governs? Can both fire on the same position simultaneously?

### REGIME & MACRO (Q66-Q80)

Q66. The HMM has 3 latent states mapped to 8 observable regimes. What is the mapping function? Is it deterministic or probabilistic? Can two HMM states map to the same observable regime?

Q67. The VIX hysteresis (GPT-46, 15% proportional deadband) is not implemented. At VIX = 25.0, the regime boundary is HIGH_VOL. Without hysteresis, how many regime changes occur per day when VIX oscillates between 24.5 and 25.5?

Q68. The cross-asset macro uses CRYPTO Fear & Greed (GPT-65/110), not equity F&G. After fixing to equity F&G, what data source should be used? Is there a free API for equity-specific sentiment?

Q69. The regime classifier has 8 states. At 1 trade/day, how many days are needed to observe ALL 8 regimes at least once? What if SHOCK occurs only once per year?

Q70. The flapping protection (GPT-80) triggers after 3 regime changes in 10 minutes. But with 60s scan cadence, a maximum of 10 scans occur in 10 minutes. Is 3 changes in 10 scans a realistic trigger, or too sensitive?

Q71. The drought state machine decays quality from 65 to 50 over ~12 days. At quality threshold 50, what types of signals pass that would be rejected at 65? Are these low-quality signals profitable?

Q72. The HMM confirmation lag is "3 days" (Table F, F-7). But GPT-70 says the code counts 3 hourly cache intervals (3 hours, not 3 days). Which is correct? What is the optimal lag for a system trading daily?

Q73. The regime transition table (§6D) lists 5 key transitions. RISK_OFF → NORMAL resumes at 0.25x for 30 minutes. But at 1 trade/day, the system might not trade during those 30 minutes. Is the ramp-up meaningful?

Q74. VIX thresholds: plan says SHOCK > 45 (code), but earlier text said > 40. At VIX 42, is the system in RISK_OFF (flatten all) or SHOCK (emergency flatten)? What is the practical difference?

Q75. The regime-stuck detection (GPT-82) alerts after 24h unchanged. During a weekend, the regime is always "stuck" for 60+ hours. Does the stuck detector distinguish market hours from non-market hours?

Q76. The correlation brake (R-06) uses pairwise correlation on a 60-day rolling window. With 12 ISA tickers that are ALL leveraged NASDAQ/S&P products, is pairwise correlation ever BELOW 0.70? If not, is the brake permanently triggered?

Q77. CUSUM alpha reaper detects strategy decay. At 1 trade/day, CUSUM accumulates slowly. How many consecutive losers trigger CUSUM at threshold 3.0? What if losses are small (-0.3%) rather than full stops (-3%)?

Q78. The plan mentions "ex-ante CDaR simulation" before each trade. With Historical Simulation VaR on 252-day returns, this requires a Monte Carlo simulation. What is the computational cost per signal evaluation? Can it complete within 60s?

Q79. The cross-asset macro monitors VIX, DXY, credit spreads, F&G, and HMM regime. If VIX says RISK_ON but credit spreads say RISK_OFF, which prevails? Is there a voting mechanism or strict hierarchy?

Q80. The plan references "Epps effect fix" for correlation calculations. What is the Epps effect? How does it affect correlation estimates for leveraged ETPs sampled at 60s intervals?

### INFRASTRUCTURE & OPERATIONS (Q81-Q95)

Q81. EC2 t3.small has 2 vCPUs and 2GB RAM. With Docker running engine + API + Redis + Dashboard, what is the peak memory usage? Is there an OOM kill risk?

Q82. Redis is configured with password "nzt48redis" and internal Docker networking only. If the Redis container restarts, is the AOF (Append-Only File) configured for persistence? What data survives a restart?

Q83. The backup script runs daily to S3. If the system crashes at 14:00 and the last backup was at 02:00, how many hours of trading data are lost?

Q84. There is no Elastic IP. The EC2 instance IP changes on stop/start. How does the dashboard (.env.production CORS) handle this? Is there automatic IP detection?

Q85. The deploy script (`deploy_to_ec2.sh`) — does it perform a health check after deployment? Can it roll back to a known-good state if the new version crashes?

Q86. The startup readiness gate (GPT-78) specifies 8 pre-flight checks. How long does each check take? Can all 8 complete before market open at 08:00 UK if the system boots at 07:55?

Q87. Docker logs are rotated by Docker's default driver. What is the max log size per container? Can critical error messages be lost due to rotation?

Q88. The system runs 24/7 with APScheduler on 60s intervals. Over 252 trading days, that's 362,880 scan cycles. What is the expected number of yfinance API failures? Does the system handle rate limiting?

Q89. The plan_proof_check.sh CI gate (GPT-02) blocks deployment if critical modules are missing. Is this script currently in the CI pipeline? What CI system is used?

Q90. The evidence preservation protocol (GPT-84) requires snapshotting Redis, logs, health, and positions BEFORE taking corrective action. Is there a script for this, or is it manual?

Q91. Telegram alerts are used for P0/P1 notifications. What is the maximum message delivery latency? Can Telegram rate-limiting delay critical alerts?

Q92. The system uses SQLite for trade storage. Under concurrent write pressure from the engine + API, can SQLite handle the load? Is WAL mode enabled?

Q93. The dashboard runs on Next.js at port 3001. Is it authenticated? Can anyone with the EC2 IP access the dashboard and view trading data?

Q94. The nightly PDF report generation — how long does it take? Does it interfere with the trading engine's scan cycle?

Q95. If the operator is absent for 48 hours and the system hits L3 FLATTEN ALL, what happens? Does the system stay halted, or can it auto-recover?

### STRATEGIC & PHILOSOPHICAL (Q96-Q100)

Q96. The plan targets (1.02)^252 = £1.48M from £10K. This is a 14,757% annual return. Name 3 funds in history that have achieved >1,000% annual returns for more than 1 year. What makes AEGIS different?

Q97. The plan says "the market owes us nothing." If the system goes 30 consecutive days without a qualifying trade (drought), what is the psychological impact on the operator? How does the plan prevent manual override?

Q98. The ISA universe has 12 tickers, all heavily correlated to NASDAQ/S&P. If there is a secular bear market in US tech (like 2000-2002, -78% NASDAQ), what is the system's expected performance over 2 years? Can it survive?

Q99. The plan has been reviewed 18 times by 3 AI models. Is there a risk of "review fatigue" where each round adds complexity without improving the system? At what point does plan complexity become a risk factor?

Q100. If you were allocating $1M of your own money to this system, what single change would you demand before funding? Why?
```

---

## INSTRUCTIONS FOR USE

1. **Gemini**: Go to https://gemini.google.com or use the API with Gemini 2.5 Pro. Paste the PROMPT 1 text, then paste the 100 QUESTIONS section at the end. Attach the plan file and as many code files as the context window allows.

2. **ChatGPT**: Go to https://chat.openai.com or use GPT-4o/o1. Paste the PROMPT 2 text, then paste the 100 QUESTIONS section at the end. Attach the same files.

3. **After receiving responses**: Bring both responses back to Claude for R20 triage — cross-validation of findings, deduplication, and amendment of the master plan.
