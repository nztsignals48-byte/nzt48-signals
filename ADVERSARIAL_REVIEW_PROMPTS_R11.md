# AEGIS Master Plan v13.8 — Round 11 Adversarial Review Commands

## Instructions
Copy-paste the appropriate command into Gemini or ChatGPT. Each AI receives the full AEGIS_MASTER_PLAN_v13_FINAL.md as attachment/context alongside this prompt.

**Round 11 Focus**: Precision errors that compound into catastrophic failures + whether the command tree has enough firepower to execute. R10 found the Kelly math contradiction and dead code. R11 goes DEEPER — into the decimal places, the edge cases, and the execution chain where a 0.1% calibration error becomes a 50% equity wipeout.

---

## GEMINI COMMAND

```
You are performing a ROUND 11 ADVERSARIAL REVIEW of the NZT-48 AEGIS Alpha-Omega Master Plan v13.8. This document has survived 10 previous review rounds across 3 AI models (Gemini, ChatGPT, Claude) and 35 amendments (GPT-01 through GPT-35). Round 10 found the Kelly math contradiction and dead code. Round 11 goes after the PRECISION ERRORS — the tiny calibration mistakes that compound into catastrophic failures — and the COMMAND TREE — whether the architecture has enough firepower at every decision node to actually execute.

MANDATORY EVIDENCE RULES:
1. If you claim something is broken/miscalibrated, you MUST show the arithmetic with specific numbers from the plan.
2. Every answer must include: the EXACT parameter value from the plan, the CORRECT value (with derivation), and the DELTA (how wrong the plan is).
3. You must output a "Command Tree Firepower Audit" — for each decision node in the execution chain, state: (a) what data it needs, (b) what data it actually has, (c) the gap, and (d) whether the gap is fatal.
4. No "it depends" — every answer requires a specific number, formula, or architectural decision.

HEDGE FUND INSTITUTIONAL REVIEW PROCEDURES:
In addition to answering the 100 questions below, you MUST also perform these formal institutional review processes that every serious quant fund runs before deploying capital. Output each as a separate deliverable section.

PROCEDURE 1 — MODEL RISK MANAGEMENT (MRM) ASSESSMENT (SR 11-7 / SS1/23 Standard):
Apply the Federal Reserve SR 11-7 / PRA SS1/23 Model Risk Management framework:
- Model Inventory: Catalogue every model in the plan (S15 scoring, Kelly sizing, Stoikov EV, HMM regime, CUSUM, Bayesian base-rate, Cornish-Fisher CDaR, Kinetic Time-Stop). For each: state inputs, outputs, assumptions, limitations, and materiality tier (Tier 1 = P&L critical, Tier 2 = risk, Tier 3 = operational).
- Independent Validation: For each Tier 1 model, perform conceptual soundness review — are the assumptions valid for this use case? Is the math correct? Are boundary conditions handled?
- Outcomes Analysis: What OBSERVABLE OUTCOMES would prove each model is working vs failing? Define pass/fail metrics for the first 63 days.
- Model Use Testing: Is each model being used within its designed scope? (e.g., Stoikov is a market-maker model — is using it as a price-taker EV gate within scope?)
- Documentation Standard: Rate the plan's documentation quality for each model (1-5). Can a new developer implement it from the spec alone?

PROCEDURE 2 — INDEPENDENT VALUATION VERIFICATION (IVV):
Perform mark-to-market verification as if this were a fund's NAV calculation:
- P&L Attribution: Break down the expected daily return into components: alpha (stock selection), beta (market exposure), gamma (leverage), theta (time decay/drag), spread cost, commission cost, slippage. What percentage of the daily return is ALPHA vs BETA?
- Reconciliation: The plan claims +1.14%/day expected return. Decompose: how much comes from market direction (beta), how much from stock selection timing (alpha), and how much from leverage amplification? If the market returns +0.04%/day (10%/year) and leverage is 3x, beta contribution = 0.12%/day. The remaining 1.02%/day must be alpha. Is +1.02%/day of pure alpha realistic for a retail system on delayed data?
- Valuation Uncertainty: At 60-second polling, the "current price" has ±0.15% uncertainty (half-spread + data delay). How does this uncertainty propagate through P&L calculation, position sizing, and stop loss levels?

PROCEDURE 3 — STRESS TESTING & SCENARIO ANALYSIS (Basel III / FRTB Standard):
Run the following stress scenarios against the plan's risk controls and determine if the system SURVIVES (portfolio drawdown < 25%):
- Historical Scenario 1: 2020-03-09 to 2020-03-23 (COVID crash — QQQ dropped 28% in 10 trading days, VIX hit 82.69)
- Historical Scenario 2: 2018-02-05 (Volmageddon — XIV went to zero, VIX spiked from 17 to 50 intraday)
- Historical Scenario 3: 2015-08-24 (Flash Crash — QQQ ETF opened -8%, circuit breakers triggered, spreads blew out 500%)
- Historical Scenario 4: 2022-01-24 to 2022-03-14 (rate hiking sell-off — QQQ dropped 22%, orderly decline with no VIX spike above 35)
- Hypothetical Scenario A: yfinance goes dark for 4 hours during a -5% underlying move
- Hypothetical Scenario B: LSE halts trading on all leveraged ETPs for 2 hours due to circuit breaker
- Hypothetical Scenario C: The broker API rejects all orders for 30 minutes (authentication expiry)
- Hypothetical Scenario D: Redis crashes and all Chandelier state is lost while holding a position
- Reverse Stress Test: What is the SMALLEST market move that would cause a >15% portfolio drawdown? Describe the exact scenario.

PROCEDURE 4 — OPERATIONAL RISK ASSESSMENT (Basel II / RCSA Framework):
Perform a Risk & Control Self-Assessment:
- People Risk: The system has a single operator/developer. Bus factor = 1. If the operator is unavailable for 48 hours, what happens? Is there a runbook? Can someone else operate the Dead Man's Switch override?
- Technology Risk: Single EC2 instance, single data feed (yfinance), single broker. Map every single point of failure and its blast radius (what breaks when it fails).
- Process Risk: The deploy script rebuilds Docker during market hours. The backup script runs on cron. List every automated process, its failure mode, and recovery procedure.
- External Risk: Regulatory change (FCA restricts leveraged ETP access for retail), broker policy change (IBKR removes API for ISA accounts), data source change (Yahoo Finance blocks scraping, yfinance library abandoned). Probability and impact for each.
- Key Risk Indicators (KRIs): Define 10 metrics that the operator should monitor DAILY to detect emerging risks before they materialise. For each: metric name, data source, warning threshold, critical threshold, response action.

PROCEDURE 5 — BEST EXECUTION REVIEW (MiFID II Article 27 Standard):
Evaluate whether the plan achieves best execution for the end client (the operator):
- Venue Analysis: LSE is the only execution venue. Are there alternative venues for the same ETPs (e.g., Xetra, Borsa Italiana for cross-listed products)? Would multi-venue access improve fill quality?
- Execution Quality Metrics: Define: fill rate, slippage, spread capture, latency-to-fill. What are the EXPECTED values for each? What are the MINIMUM ACCEPTABLE values?
- Market Impact: At what position size does AEGIS's order flow become detectable by the market maker? At £1,000? £5,000? £10,000? What changes when the market maker detects a systematic pattern?
- Time-of-Day Analysis: What is the OPTIMAL time to trade LSE ETPs? Early morning (08:00-09:00 UK, low liquidity but fresh signals), mid-morning (10:00-11:00, stable spreads), US open crossover (14:30-15:00, max volatility but wide spreads)?

PROCEDURE 6 — PRE-TRADE RISK LIMIT FRAMEWORK:
Define the complete pre-trade risk limit structure as a hedge fund would:
- Hard Limits (NEVER breached, system halts): Max position size as % of equity, max daily loss, max portfolio VaR, max leverage ratio, max concentration per ticker.
- Soft Limits (breach triggers alert + review): Max consecutive losses, max drawdown duration, max correlation across positions, min signal quality, min data freshness.
- Escalation Matrix: Who gets alerted at each breach level? What actions are AUTOMATED vs require HUMAN decision?
- Limit Calibration: Show the DERIVATION for each limit value — not "CDaR = 8%" but "CDaR = 8% because: at 3x leverage, 95th percentile 10-day drawdown on QQQ = 8.2% (empirical 2020-2024), and 8% × 3 = 24% which is below the 25% survival threshold."

PROCEDURE 7 — COUNTERPARTY & LIQUIDITY RISK ASSESSMENT:
- Broker Counterparty Risk: If the ISA broker fails (2011 MF Global, 2023 SVB fintech impact), are ISA assets protected under FSCS (£85K limit)? At what portfolio size does FSCS become insufficient?
- Market Liquidity Risk: During 2020 COVID flash crash, 2015 August flash crash, and 2018 Volmageddon — what happened to LSE leveraged ETP spreads, volumes, and order book depth? Were they TRADEABLE? If spreads blew to 10%, the Emergency Flatten executes at -10% to -15%, far exceeding the -5% threshold.
- Funding Liquidity Risk: The system reinvests all profits. If drawdown reduces equity below minimum viable position size (after DynamicSizer 8-factor multiplication), the system CANNOT trade. What equity level is the "death zone" where recovery is mathematically impossible?
- Concentration Risk: The ISA contains 100% of the operator's trading capital in a single strategy on a single exchange using a single data feed through a single broker. What is the expected loss from a CORRELATED FAILURE of 2+ of these dependencies simultaneously?

ADOPT FOUR PERSONAS SIMULTANEOUSLY:

PERSONA 1 — CHIEF QUANT (30 years systematic trading, ran $2B+ fund, survived LTCM/2008/COVID)
Focus: Precision of every mathematical formula. A 0.1% error in Kelly sizing at 252 trades/year compounds to a 28% terminal wealth deviation. Find those 0.1% errors.

PERSONA 2 — LEAD SYSTEMS ARCHITECT (built exchange-grade matching engines at Citadel/Jump)
Focus: The execution chain from signal detection to order fill. Every millisecond of latency, every race condition, every state machine transition that can deadlock or produce contradictory actions.

PERSONA 3 — CHIEF RISK OFFICER (CRO at a leveraged ETP market maker, knows Flow Traders/Jane Street playbook)
Focus: The failure modes the plan doesn't model. Correlated failures across risk controls. The scenario where 3 independent "safe" components fail simultaneously.

PERSONA 4 — ACADEMIC REVIEWER (published on leveraged ETPs specifically — Avellaneda, Cheng, Madhavan)
Focus: Every formula's boundary conditions. Where the math breaks down. Where a continuous-time formula is applied to a discrete-time system. Where a normal distribution assumption is applied to a kurtosis-10 distribution.

FOR EACH OF THE FOLLOWING 16 SECTIONS, provide:
- **Precision Errors**: Specific numerical miscalibrations (show the math)
- **Command Tree Gaps**: What decision nodes lack sufficient data/firepower to execute
- **Compound Failure Scenarios**: How small errors cascade into large losses
- **Fixes**: Exact corrected values with derivations

SECTIONS TO REVIEW:

1. KELLY PAYOFF RESOLUTION (GPT-29) — Blended average win of +6.17%, ladder rung probabilities, corrected Kelly fraction
2. RISK STATE MACHINE (R-01B GPT-30) — HALT > FLATTEN > REDUCE > NORMAL precedence, single-executor model
3. GAP & AUCTION RISK CONTROLS (R-01C GPT-33) — Overnight gap > 2 ATR, 5-min LSE exclusion, overnight size cap
4. SIGNAL STALENESS CONTROLS (GPT-33) — max_signal_age=120s, fail-closed on stale data, data_freshness_seconds
5. DEAD CODE AUDIT (GPT-31) — R-10 Phase C deferral, R-12 OBI shadow-mode, Inverse Pivot separate risk budget
6. EMERGENCY FLATTEN RECALIBRATION (GPT-32) — -3% → -5% threshold change, leverage-adjusted rationale
7. SETUPFINGERPRINT PROGRESSIVE DIMENSIONALITY (GPT-34) — 3-dim → 4-dim → 5-dim expansion triggers
8. CHANDELIER 5-RUNG PROFIT LADDER — Rung thresholds (+2%, +6%, +10%), variance drag decay, bank/trail split
9. DYNAMICSIZER 8-FACTOR KELLY — All 8 multipliers, product formula, equity calculation
10. BAYESIAN STRANGER PENALTY — κ(n, DSR) formula, graduation threshold t_stat ≥ 3.0
11. 8-INDICATOR S15 CONSENSUS — Indicator weights, collinearity, effective DoF, 75/100 threshold
12. GO-LIVE GATE (11 CRITERIA) — Each criterion's precision, false positive/negative rates
13. NIGHTLY ACTIVATION + BASE-RATE GATE — Freeze & Prove rollout, beta-binomial posterior, fingerprint dimensionality
14. STOIKOV EV GATE + GAUNTLET ARCHITECTURE — 33-gate sequential chain, rejection rate, latency
15. REGIME CLASSIFIER + VIX HYSTERESIS — 7-state HMM, 3-tick buffer, hysteresis band calibration
16. DRAWDOWN CASCADE + CIRCUIT BREAKERS — 6 levels, CDaR Cornish-Fisher, interaction model

THEN ANSWER ALL 100 OF THESE QUESTIONS (each requires a specific, substantive answer with numbers):

PRECISION & CALIBRATION (Questions 1-25):
1. The Kelly Payoff Resolution (GPT-29) claims blended average win = +6.17% based on rung probabilities: Rung 1 breakeven 15%, Rung 2 +4.7% at 40%, Rung 3 +7.0% at 25%, Rung 4+ +11.0% at 15%, Rung 5+ +18.0% at 5%. WHERE do these probabilities come from? Are they backtested on actual 3x ETP trades, assumed from equity market data, or fabricated for the plan? If assumed, what is the sensitivity: if Rung 2 probability drops from 40% to 25% (runners aren't running), what does the blended average become and does Kelly stay positive?

2. The GPT-29 Kelly resolution shows f* = +0.331 at 55% WR with blended win +6.17% / loss -3.0%. But this uses the BASIC Kelly formula f* = (bp - q)/b. The plan elsewhere specifies HALF-KELLY (f*/2). If half-Kelly is used, f*/2 = 0.166. Then the regime multiplier (TRENDING_UP_STRONG = 0.6) gives 0.6 × 0.166 = 0.0996. Then the 8-factor DynamicSizer multiplies by 8 more factors (each 0.5-1.0). Walk through the COMPLETE sizing chain from f* to actual position size in GBP at £10K equity and show whether the final position is large enough to generate meaningful P&L.

3. The Emergency Flatten was recalibrated from -3% to -5% (GPT-32). But the Drawdown Cascade has GREEN at 0% to -2%, YELLOW at -2% to -4%, ORANGE at -4% to -6%. The Emergency Flatten at -5% fires INSIDE the Orange level. What happens first — does the Drawdown Cascade reduce sizing to 0.50x (Orange), or does the Emergency Flatten nuke everything? Does the Risk State Machine (R-01B) resolve this, or is there still ambiguity?

4. The Risk State Machine has 4 states: NORMAL → REDUCE → EMERGENCY_FLATTEN → SYSTEM_HALTED. But the Drawdown Cascade has 6 levels: Green → Yellow → Orange → Red → Critical → Emergency. How do the 4 risk states MAP to the 6 cascade levels? Is REDUCE = Yellow+Orange? Is EMERGENCY_FLATTEN = Red+Critical+Emergency? The plan doesn't specify the mapping. Without it, the state machine and cascade can issue contradictory instructions.

5. The overnight gap control (R-01C) says "no entry if implied overnight gap > 2 ATR." But the ATR is calculated on 15-minute bars during market hours. Pre-market, there ARE no 15-minute bars. What ATR is used for the gap comparison — yesterday's closing ATR? If yesterday was a low-vol day (ATR = 1.5%) and the overnight gap is 3.5% (2.33 × ATR), the rule blocks entry. But if yesterday was high-vol (ATR = 4.0%) and the overnight gap is 7.5% (1.875 × ATR), the rule ALLOWS entry into a 7.5% gap. Is the gap threshold correctly specified, or should it be an absolute percentage (e.g., >5%) rather than ATR-relative?

6. The signal staleness control sets max_signal_age_seconds = 120. But the scan cycle is 60 seconds. A signal generated at scan T is at most 60 seconds old at the START of scan T+1. The consumer processes it, routes to execution — but execution includes the 33-gate gauntlet evaluation. If the gauntlet takes 500ms per gate × 33 gates = 16.5 seconds, plus the OBI wait gate (2 minutes if triggered), a signal could be 120+ seconds old BEFORE it reaches the execution decision. Is the 120-second staleness window measured from signal generation or from execution attempt? If from generation, the OBI wait gate makes it impossible to execute within 120s.

7. The Chandelier Rung 2 threshold is +6% for 3x ETPs (corresponding to +2% on underlying). But the plan's own A-01 fatal flaw says variance drag degrades 3.0x to ~2.7-2.8x over multi-hour holds. If effective leverage at the time of Rung 2 is 2.8x (not 3.0x), then a +2% underlying move produces +5.6% on the ETP (not +6%). The Rung 2 threshold of +6% is therefore UNREACHABLE for the underlying +2% target on holds longer than ~90 minutes. Should Rung 2 be +5.5% for holds > 1 hour?

8. The Bayesian Stranger Penalty uses f_DSR(DSR) = 1 - exp(-0.5 × max(0, DSR - 1.5)). At DSR = 1.5, f_DSR = 0 (stranger). At DSR = 3.0, f_DSR = 1 - exp(-0.75) = 0.528. At DSR = 5.0, f_DSR = 1 - exp(-1.75) = 0.826. The graduation threshold is t_stat = SR × √n ≥ 3.0. But SR (Sharpe Ratio) is HIGHLY sensitive to the return distribution. For a 3x leveraged ETP with fat tails (kurtosis > 10), the SR estimator has standard error of approximately √(1 + κ/4)/√n = √(1 + 2.5)/√n = 1.87/√n. At n = 120 trades, the SE is 1.87/√120 = 0.171. A true SR of 1.5 could appear as SR = 1.16 (one SE below). t_stat = 1.16 × √120 = 12.7 — still above 3.0. But at n = 30 trades, SE = 0.342, SR could appear as 0.816, t_stat = 0.816 × √30 = 4.47 — still passes. Is the graduation threshold of 3.0 too EASY for fat-tailed distributions, not too hard?

9. The 8-indicator S15 consensus uses weights: VWAP 1.8x, RVOL 1.3x, RSI 1.2x, Trend 1.0x, ADR 1.0x, Macro 1.0x, Tail 1.0x, Spread 0.8x. The maximum possible score is: 1.8 + 1.3 + 1.2 + 1.0 + 1.0 + 1.0 + 1.0 + 0.8 = 9.1 (each indicator scores 0 or weight). The 75/100 threshold implies the score range is 0-100, not 0-9.1. How is the 9.1 maximum NORMALISED to 100? Is each indicator binary (0 or weight) or continuous (0 to weight)? If continuous, what distribution does each indicator follow, and is the 75/100 threshold at the right percentile?

10. The Go-Live Gate requires "Win Rate ≥ 50% with minimum 60 completed S15 trades." At 60 trades, a 50% win rate (30 wins / 60 trades) has a 95% confidence interval of [37.1%, 62.9%] (Wilson score). The TRUE win rate could be as low as 37% and still produce an OBSERVED 50% at n=60. At 37% WR with the ladder payoff, is Kelly still positive? What is the minimum N for the Go-Live Gate to have 95% confidence that the TRUE WR exceeds 50%?

11. The CDaR threshold is 8% with hysteresis exit at 6%. The plan says CDaR uses Cornish-Fisher expansion for tail estimation. But Cornish-Fisher is a 4th-order polynomial approximation that DIVERGES at extreme kurtosis. For 3x ETPs with daily kurtosis > 10, the CF expansion produces VaR estimates that are LOWER than empirical VaR (the polynomial underestimates the tail). This means the CDaR breaker triggers TOO LATE. What is the empirical CDaR at 95% for actual 3x ETP return distributions, and how far is it from the CF approximation?

12. The Inverse Pivot uses "0.3 × f*_inverse" with a separate risk budget. But f*_inverse requires estimating the WIN RATE and PAYOFF RATIO for inverse ETP crash trades. How many crash events have occurred in the backtest period? Since 2010, there have been approximately 8-10 "tradeable crash" events (2010 Flash Crash, 2011 Aug, 2015 Aug, 2015 Sep, 2018 Feb, 2018 Oct, 2018 Dec, 2020 Mar, 2022 Jan, 2022 Jun). At n = 8-10, the WR and payoff ratio estimates have MASSIVE confidence intervals. Is 0.3 × f*_inverse a reliable sizing when f*_inverse is estimated from 8 events?

13. The correlation brake uses Ledoit-Wolf on 60-day rolling returns with 0.70 threshold. But 60 daily returns on 3x ETPs have approximately 2-3 effective degrees of freedom (most variance is explained by the underlying Nasdaq factor). The Ledoit-Wolf shrinkage estimator with p = 12 tickers and n = 60 observations has a shrinkage intensity of approximately λ = 0.85-0.95, meaning it shrinks almost ENTIRELY toward the identity matrix. The resulting correlations are massively UNDERSTATED. Is the correlation brake even functional with this shrinkage level?

14. The Kinetic Time-Stop formula T_max = MaxDrag / (σ² × L²) gives T_max = 0.0015 / (0.0025² × 9) × 60 = 1.33 minutes at σ = 0.25% and L = 3. But the vol estimate σ uses "recent 5-minute realised vol." During the first 5 minutes of a trade, there IS no "recent 5-minute vol" for that specific trade — it must use PRIOR vol. If the prior 5 minutes had σ = 0.10% (quiet) but the current 5 minutes have σ = 0.50% (CPI release), T_max is calculated as 33.3 minutes but the ACTUAL maximum hold should be 1.33 minutes. The lag in vol estimation means the kinetic stop is ALWAYS one regime behind. How is this addressed?

15. The Base-Rate Gate progressive dimensionality (GPT-34) says Phase 1 uses 3 dimensions creating ~42 cells. But 7 recipes × 6 regimes × 1 direction (mostly LONG) = 42. However, the plan also says "2 directions" — LONG and SHORT. If SHORT is included, it's 84 cells, not 42. And the plan doesn't say "mostly LONG" — the Inverse Pivot trades SHORT. Is the cell count 42 or 84? If 84, the N=20 threshold per cell takes TWICE as long to reach.

16. The DynamicSizer multiplies 8 factors. Each factor ranges from 0.5 to 1.0. The MINIMUM product of 8 factors each at 0.5 = 0.5^8 = 0.004. The MAXIMUM product = 1.0^8 = 1.0. At 0.004 × half-Kelly × equity, the position size at £10K = 0.004 × 0.166 × £10,000 = £6.64. Is there a FLOOR on the DynamicSizer output? A £6.64 position on a 3x ETP generates ~£0.40 of P&L on a +6% move. Is this worth the operational overhead?

17. The scan_health.json writes every scan cycle (60 seconds). Over 450 market minutes per day, that's 450 writes. Over a year, 450 × 252 = 113,400 writes. Each write includes 20+ fields. If each write is a full JSON file overwrite (not append), the disk I/O is minimal — but if it's an APPEND to a growing file, it'll reach 113,400 lines × ~500 bytes = ~57 MB per year. Is scan_health.json overwritten or appended? If appended, is there rotation logic? If overwritten, how are historical scan health metrics preserved for the Go-Live Gate verification?

18. The Entry Velocity Gate (B-8) exits if price hasn't moved +0.3% within the velocity window (3-10 minutes depending on RVOL). But +0.3% on the ETP is +0.1% on the underlying for 3x. A +0.1% move on QQQ in 3-10 minutes is NOISE, not signal. The velocity threshold should be proportional to the UNDERLYING move, not the ETP move. Is +0.3% the right threshold, or should it be +0.3% × leverage = +0.9% on the ETP (to represent a +0.3% underlying move)?

19. The plan specifies half-Kelly sizing with regime multipliers: TRENDING_UP_STRONG = 0.6, TRENDING_UP_MOD = 0.5, RANGE_BOUND = 0.3. But these multipliers are applied to HALF-Kelly, not FULL Kelly. So actual sizing is: TRENDING_UP_STRONG = 0.6 × f*/2 = 0.3 × f*. This is less than HALF-Kelly. The "0.6" sounds aggressive but is actually 30% of optimal Kelly. At the corrected blended payoff (b = 2.057, p = 0.55), f* = 0.331, so actual sizing = 0.3 × 0.331 = 0.099 = 9.9% of equity. At £10K, that's £990. Is 9.9% of equity the intended maximum exposure, or has the double-discounting (half-Kelly × 0.6) been accounted for?

20. The 33-gate gauntlet evaluates gates SEQUENTIALLY. The plan says Gate 1 (ISA eligibility) filters first. If 100% of signals pass ISA (they're all LSE ETPs), the effective gauntlet starts at Gate 2. How many of the 33 gates have >99% pass rates? If 20 gates have >99% pass rates, they're not adding safety — they're adding latency. What is the expected EXECUTION TIME of the full 33-gate evaluation, and what gates can be PARALLELISED rather than sequential?

COMMAND TREE FIREPOWER (Questions 21-50):
21. The execution chain is: yfinance fetch → indicator calculation → S15 score → confidence floor check → 33-gate gauntlet → signal queue → consumer coroutine → virtual_trader.open_position(). Count the DECISION NODES in this chain. At each node, what is the INFORMATION QUALITY (freshness, accuracy, granularity)? Is there a node where the information quality is so poor that the decision is essentially random?

22. The S15 fires once per day maximum. On days when it fires, the signal passes 33 gates and reaches execution. But the 33 gates are evaluated using data that is up to 60 seconds stale (from the last yfinance fetch). During those 60 seconds, the market can move 0.3-0.5% on a 3x ETP. The execution decision is therefore based on a SNAPSHOT that may no longer be valid. What percentage of signals would be REVERSED if re-evaluated with fresh data at execution time?

23. The Nightly Activation Set selects recipes for the next day. But the regime could change overnight. If Nightly Activation selects "VWAP_RECLAIM_RVOL" based on TRENDING_UP regime at 16:30 UK, but VIX spikes overnight (US session) pushing the regime to RISK_OFF, the pre-selected recipe is WRONG for the current regime. Does the engine re-evaluate regime at market open and override the Nightly Activation selection?

24. The 3-tick regime confirmation buffer means the system requires 3 consecutive scan cycles (180 seconds) to confirm a regime change. During those 180 seconds, the system trades at the OLD regime's Kelly sizing. In a sudden crash (2020 March, 2022 January), VIX can move from 20 to 40 in 30 minutes. The system spends the first 180 seconds at TRENDING_UP_STRONG Kelly (0.6 × f*/2) while the market is in free-fall. What is the expected loss during the 180-second confirmation window in a genuine crash?

25. The plan's signal queue consumer processes signals in priority order. But priority is defined as 100 - confidence. A signal with 95 confidence has priority 5. A signal with 75 confidence has priority 25. The consumer processes priority 5 first. But what if the priority 5 signal is for a DIFFERENT ticker than the priority 25 signal? S15 fires once per day — there should only be ONE signal in the queue. Under what circumstances do MULTIPLE signals appear in the queue simultaneously, and is priority ordering meaningful for a 1-signal/day system?

26. The shadow markout tracker monitors +5m, +15m, +60m, EOD prices after exit. But the tracker needs to POLL these prices from yfinance. If the shadow tracker uses the same yfinance connection as the primary scan loop, it COMPETES for API rate limits. With 22+ tickers scanned every 60s + shadow positions (potentially 5-10 per week), total API calls increase by ~10%. At what shadow book size does the tracker start degrading primary scan loop performance?

27. The ISA Three-Key Safe requires Key B (broker routability) with 24-hour TTL cache. But the cache can go STALE if the broker changes its ISA routing table (e.g., during a product migration or corporate action). If Key B returns "routable" from cache but the broker actually can't route, the trade FAILS at execution — after passing all 33 gates. Is there a fallback mechanism? Does the system retry? Does it log the failed routing for the Go-Live Gate?

28. The Dead Man's Switch uses AWS Lambda to flatten positions if EC2 goes down. But the Lambda function needs CURRENT position data to know what to flatten. Where does it get this data? If it reads from Redis (which is on EC2), and EC2 is down, Redis is also down. Does the Lambda function have an independent data source for current positions?

29. The plan specifies a Docker Compose setup with 3 containers: nzt48 (engine), nzt48-dashboard, nzt48-redis. Redis is on the internal Docker network with password "nzt48redis." But if the Docker host runs out of memory (t3.small has 2GB), Docker's OOM killer will terminate containers. The OOM killer targets the LARGEST container first. If the engine container (main.py + all modules) is the largest, it dies first. Redis survives with stale data. The dashboard shows GREEN status (Redis is up, health check passes). The operator sees "system healthy" while the engine is dead. Is there an OOM-specific health check?

30. The plan accumulates 35 amendments across 10 rounds. Each amendment modifies specific sections of the plan. But some amendments CONTRADICT earlier amendments. GPT-32 changed Emergency Flatten from -3% to -5%. But the Emergency Flatten Independence note (v13.1 G-R3) still references "portfolio P&L vs. -3% threshold" in the text. Are there other instances where amendment text was updated but referenced text was NOT? Is there a consistency check for cross-references?

31. The CUSUM alpha reaper monitors cumulative outcomes. But the CUSUM threshold (3.0σ) is calibrated for NORMALLY DISTRIBUTED outcomes. With 3x ETP returns having kurtosis > 10, the true false positive rate at 3.0σ is NOT 0.27% — it's approximately 1.2-1.8% (fat tails produce more extreme cumulative sums). At 252 trading days, expected false positives = 3.0-4.5 per year. Is the CUSUM disabling 3-4 working strategies per year?

32. The plan targets "1 trade per day" but doesn't specify WHEN the trade fires. S15 scans every 60 seconds. On any scan, if confidence ≥ 75 and all 33 gates pass, a signal fires. But does the system STOP scanning after the first signal fires? If not, a second signal could fire 60 seconds later (different ticker or same ticker, different scan data). The plan says "S15 fires ONCE per day" but where is the once-per-day constraint ENFORCED? Is it in the signal queue, the consumer, or the scanner itself?

33. The correlation brake triggers at 3+ pairs exceeding 0.70 correlation. But with 12 CORE tickers that are ALL Nasdaq-correlated, how many pairs exceed 0.70 ON A NORMAL DAY? If the answer is "most of them," the correlation brake is PERMANENTLY triggered, limiting to 1 position at all times. This makes the brake not a safety mechanism but a structural constraint. Has anyone calculated the baseline correlation distribution across the CORE universe?

34. The plan says Redis stores Chandelier exit state (rung, stop, banked%, trailing%). Redis uses AOF persistence. If the Redis container crashes and restarts, AOF replay restores the state. But if the crash happens DURING a write (mid-transaction), the restored state may be INCONSISTENT — e.g., rung=2 but banked=0% (rung advanced but bank didn't execute). Is there a transactional write for multi-field Chandelier state updates?

35. The VIX hysteresis uses fixed 2-point dead bands: SHOCK enter 45 / exit 43, RISK_OFF enter 35 / exit 33, HIGH_VOL enter 25 / exit 23. But VIX has a strong mean-reverting characteristic with a long-term mean of approximately 19-20. When VIX is at 24 (near HIGH_VOL threshold), the probability of it moving to 25 (trigger) vs. staying below is about 50/50 on any given day. The hysteresis prevents RAPID oscillation but doesn't prevent DAILY oscillation. Over a month at VIX ~24-26, the system could toggle between NORMAL and HIGH_VOL 10+ times. Is 2 points enough hysteresis for the VIX mean-reversion speed?

REAL-LIFE IMPLEMENTATION FAILURES (Questions 36-60):
36. The plan specifies 39 hours for Phase A. But real-world software development includes: reading existing code (2-4 hours for 7,700 lines), setting up dev environment (1-2 hours), understanding Docker/Redis/SQLite interactions (1-2 hours), writing tests (40% of implementation time), debugging CI failures (20% of implementation time), code review (if applicable). The 39-hour estimate appears to be CODING TIME ONLY. What is the REALISTIC total calendar time including all overhead? Industry rule of thumb: multiply coding estimate by 2.5-3x.

37. The 33-gate gauntlet is evaluated every scan cycle. With 22 tickers × 450 scans/day = 9,900 evaluations per day. Each evaluation runs 33 gates sequentially. If each gate takes 5ms average (including data fetches), total gauntlet time = 9,900 × 33 × 5ms = 1,633.5 seconds = 27.2 minutes of CPU time per day. On a t3.small with 2 vCPUs, this is 27.2 / (450 × 2) = 3% CPU utilisation — seems fine. But during VOLATILE periods when VIX > 30, the regime gates and correlation gates require ADDITIONAL computation (Ledoit-Wolf covariance = O(p³) where p = 12). What is the WORST-CASE CPU utilisation, and does it ever exceed the 60-second scan budget?

38. The plan uses yfinance for ALL data: prices, volume, VIX, DXY. But yfinance rate-limits are per-IP. If the EC2 instance's IP gets rate-limited, ALL data feeds stop simultaneously. There is ZERO redundancy. In production, what happens during a yfinance outage? The signal staleness control (GPT-33) enters fail-closed mode after 5 minutes. But 5 minutes of NO DATA during a market crash is an eternity. Should the fail-closed trigger be 120 seconds (2 scan cycles), not 300 seconds (5 cycles)?

39. The ISA eligibility gate (A-1) checks 3 keys: regulatory, broker, venue. Key A is a hardcoded ISIN registry. But ISINs change — corporate actions, share class changes, fund mergers all produce new ISINs. The plan says Key A uses a "hardcoded ISIN registry." How is this registry UPDATED when ISINs change? Is there an automated ISIN verification against the LSE's official registry, or is it manual?

40. The plan's Docker deployment uses docker-compose with 3 containers. But the deploy script (`deploy_to_ec2.sh`) copies the entire codebase to EC2 and rebuilds. During the rebuild (docker-compose build + up), the system is DOWN. On a t3.small, a full Docker build takes 3-5 minutes. During those 3-5 minutes, the system is not monitoring the market. If a rebuild happens during market hours, the system misses 3-5 scan cycles. Is there a blue-green deployment strategy, or is every deployment a market-hours outage?

41. The plan says "scan_health.json" is the heartbeat file. The Go-Live Gate reads this file for verification. But who READS scan_health.json in production? Is there a monitoring dashboard that alerts on anomalies? Or is it a passive file that nobody checks until something explodes? The Dead Man's Switch monitors /health endpoint, not scan_health.json. These are different things. What monitors scan_health.json?

42. The plan specifies 14 acceptance tests for A-1 (ISA Gate). But writing 14 tests for a module that doesn't exist yet requires understanding the module's interface BEFORE implementation. In practice, the interface changes during implementation. Are these tests specification-first (TDD) or will they be written AFTER implementation (traditional)? If TDD, the 8-hour estimate for A-1 already includes test writing time — is it realistic?

43. The base-rate gate (B-11) uses scipy.stats.beta for the posterior. The plan acknowledges scipy is heavy (~30MB). But the REAL issue isn't disk space — it's IMPORT TIME. On a t3.small, importing scipy takes 2-4 seconds. If this import happens during the scan loop (lazy import), it adds a one-time 2-4 second delay to the first scan that uses it. If it happens at startup, it extends container boot time. Is the import handled at startup or on first use?

44. The Docker compose setup maps no ports for Redis (internal network only). But for debugging, the developer needs to inspect Redis state. The current approach requires `docker exec -it nzt48-redis redis-cli`. This works but is clunky. More critically, if the engine crashes and the developer needs to manually check/modify Redis state, they need to know the Redis key schema. Is the key schema documented? What keys exist? What happens if a key is missing?

45. The plan targets £102K-£338K Year 1. But the ISA contribution limit is £20,000/year. The initial £10K is within the limit. But profits WITHIN the ISA are tax-free regardless of size — the £20K limit is on CONTRIBUTIONS, not on growth. So a £10K contribution that grows to £338K is entirely tax-free. This is correct. But what if the system LOSES money and the operator wants to add more capital? They can contribute up to £10K more (£20K total - £10K initial). Is the system designed to handle MID-YEAR capital additions, or does it assume static £10K?

46. The plan uses SQLite for trade storage with monthly JSONL rotation. But SQLite on Docker volumes can have PERFORMANCE issues if the volume driver doesn't support fsync correctly. On AWS EBS (gp3), random write IOPS is 3,000 baseline. With 450 scans/day × ~5 writes per scan (scan_health, trade logs, shadow, rejection logs, regime state) = 2,250 writes/day. This is far below the 3,000 IOPS limit. But during EOD processing (shadow finalization, nightly activation, base-rate updates), the write burst could be 50-100 writes in 30 seconds. Is this burst within the EBS budget?

47. The plan's academic citations use formatted references (Author Year). But several citations are from WORKING PAPERS, not peer-reviewed journals. Working papers can be retracted or significantly revised. Which citations are working papers vs. published? Does it matter for the formulas being used?

48. The Nightly Activation Set's "Freeze & Prove" rollout has 3 phases spanning 8+ weeks. During Phase 1 (report-only), ALL recipes are active. If a recipe is consistently losing during Phase 1, the operator sees the report but the system KEEPS TRADING IT. The system deliberately takes known-bad trades for 4 weeks while collecting data. At 1 trade/day and 50% allocation to the bad recipe, that's ~14 losing trades × £300 average loss = £4,200 (42% of equity) in deliberate bad trades. Is this an acceptable cost for data collection?

49. The plan's Go-Live Gate requires "zero false flatten events." But the plan also changed the Emergency Flatten threshold from -3% to -5% (GPT-32). During paper trading, the -5% threshold may NEVER trigger (because a single 3x ETP position rarely draws down 5% intraday). This means the Emergency Flatten is never tested during paper trading. On day 1 of live trading, when a real -5% drawdown occurs, the Emergency Flatten fires for the FIRST TIME. Is an untested emergency mechanism acceptable for go-live?

50. The plan has 7 Phase A items, 6 Phase B items, 7 Phase C bookmarks, 15 risk controls, 33 gates, 54 parameters, 11 Go-Live criteria, and 26 known fatal flaws. The TOTAL component count is 154+. Each component has dependencies on 2-5 others. The plan dependency graph has approximately 400+ edges. Is this dependency graph actually acyclic? Has anyone checked for cycles? A circular dependency (A depends on B depends on C depends on A) would make the system unbuildable.

EDGE CASES & COMPOUND FAILURES (Questions 51-75):
51. What happens when TWO risk controls fire on the SAME scan cycle? E.g., VIX crosses 35 (RISK_OFF) AND portfolio drawdown hits -5% (Emergency Flatten). The Risk State Machine says EMERGENCY_FLATTEN > REDUCE. But RISK_OFF also sets Kelly = 0.0. Does the flatten execute AND the Kelly zero persist, or does the flatten override the Kelly (since positions are being closed, Kelly is irrelevant)?

52. The plan has 8 exit conditions evaluated in priority order. What happens when EXIT_STOP_LOSS (P1) and EXIT_CHANDELIER_RUNG_2 (P3) trigger on the SAME scan? The stop loss was hit at the exact moment the price also crossed Rung 2 (price gapped through the stop to above Rung 2 — impossible on continuous prices but possible on 60-second bars). Which exit fires? Is this scenario handled?

53. What happens when yfinance returns NaN for a ticker's price? Does the indicator calculation propagate the NaN (resulting in NaN confidence score, which fails the 75/100 threshold, preventing a signal)? Or does it crash? Has the plan tested NaN propagation through the entire indicator → confidence → gauntlet → execution chain?

54. What happens on bank holidays when LSE is closed but US markets are open? The S15 scanner sees no price movement on LSE ETPs (volume = 0). RVOL = 0/baseline = 0. All volume-dependent indicators produce zero scores. The system correctly doesn't fire. But the Apex Radar is scanning US underlyings which ARE moving. It detects anomalies but can't reroute to LSE (closed). Are these anomalies QUEUED for the next trading day, or are they dropped? If queued, are they stale by the next morning?

55. The plan's Chandelier exit uses Redis for state persistence. If the engine process crashes BETWEEN banking 33% at Rung 2 and updating the Redis state to {banked: 33%}, the engine restarts with {banked: 0%, rung: 1}. It then recalculates — price is above Rung 2, but the 33% bank order already executed at the broker. The system now has a PHANTOM position: it thinks it holds 100% but actually holds 67% (33% was banked). How is this reconciled?

56. What happens when the VIX data feed from yfinance becomes stale while other feeds remain fresh? The regime classifier uses VIX as its primary input. If VIX is stale (last update 10 minutes ago) but price data is fresh, the classifier produces a regime based on OLD volatility. The system could be trading at TRENDING_UP_STRONG Kelly while VIX has actually spiked to 40. The signal staleness control (GPT-33) checks data_freshness for yfinance overall, not per-ticker. Does it check VIX freshness independently?

57. The plan assumes S15 fires at most ONCE per day. But what prevents a signal from firing, the trade executing, hitting stop loss at -3%, and then a NEW signal firing on the SAME day (different ticker, or same ticker at a new price)? If the "once per day" constraint is enforced at the scanner level, a second genuine opportunity is missed. If it's NOT enforced, the system takes 2 losses in one day (-6%), potentially triggering the drawdown cascade.

58. The ISA Three-Key Safe has Key C checking "spread < 2× median." But what if the median itself is anomalous? During a low-vol week, spreads tighten to 10 bps. The 20-day median drops to 15 bps. Then on a normal vol day, spreads return to 30 bps — which is 2× the deflated median. Key C blocks the trade on a NORMAL day because the median was artificially depressed. Should the median use a FLOOR (e.g., max(median, 20 bps))?

59. The plan's monte carlo simulation used for the 33/67 bank/trail optimisation assumed 1,000,000 paths. But what were the INPUT PARAMETERS? If the simulation used WR=58%, average_win=+6%, average_loss=-3%, the 33/67 split is optimal for THAT parameter set. But at WR=52% (which is within the confidence interval), the optimal split might be 50/50. Is the 33/67 split ROBUST across the confidence interval of the input parameters, or is it point-optimised?

60. The Emergency Flatten at -5% fires and flattens ALL positions via market orders. But market orders on LSE ETPs during high volatility can have significant slippage (2-5% beyond the quoted price for size > £5,000). If the system holds £3,000 in a 3x ETP and the flatten fires, the market order might fill at -7% instead of -5%. The POST-FLATTEN drawdown is -7%, which triggers the RED drawdown cascade level. The system is now in Red (-6% to -8%) with zero positions. It can't recover because Red allows only "A-team signals" at 0.25x sizing. Is this a death spiral?

TARGETING OBJECTIVES (Questions 61-85):
61. The plan says the architecture target is +1.14%/day (Moderate scenario). At 252 trading days, (1.0114)^252 = £177K. But the system doesn't trade every day — S15 may not fire if no signal meets the 75/100 threshold. Historically, what percentage of trading days produce a signal? If it's 60%, the effective compounding days are 151, and (1.0114)^151 = £55K. The £177K target assumes 100% trading day utilisation. What is the REALISTIC utilisation rate?

62. The conservative scenario assumes 55% WR with 2.5R. The blended average win is +6.17% and the average loss is -3.0%. The R value = 6.17 / 3.0 = 2.057. But the plan says "2.5R" for the conservative scenario. 2.057 ≠ 2.5. Which is correct — the rung probability calculation (2.057R) or the scenario table (2.5R)? If the scenario table is correct, the rung probabilities are WRONG (winners must be larger). If the rung calculation is correct, the scenario table overstates returns.

63. The plan targets 2% daily on the ETP. With the Chandelier ladder banking 33% at +6% (Rung 2), the banked portion generates: 0.33 × 6% = 1.98%. This is 99% of the 2% daily target FROM THE BANKED PORTION ALONE. But this assumes 100% of winning trades reach Rung 2. If only 60% reach Rung 2 (the other 40% exit at Rung 0 or Rung 1), the expected daily return on winning days is: 0.60 × 6.17% × WR - 3.0% × (1-WR) = 0.60 × 6.17% × 0.55 - 3.0% × 0.45 = 2.036% - 1.35% = 0.686%. That's +0.686%/day on winning days, NOT +2%. The daily target fundamentally depends on what fraction of trades reach Rung 2.

64. The plan's Bayesian Stranger Penalty means new tickers trade at 25-65% of normal size for the first 50-120 trades. During this period, the system is deliberately UNDER-SIZED. If the first year is primarily stranger-penalised trades (because all tickers start as strangers), the year 1 returns are systematically suppressed. At κ = 0.40 average for year 1, the £177K moderate scenario becomes £177K^0.40 ≈ wrong — actually it's the position sizes that are 40% of optimal, so returns scale linearly: 0.40 × 1.14%/day = 0.456%/day → (1.00456)^252 = £31K. Year 1 REALISTIC target with stranger penalty: ~£31K, not £177K. Has this been modelled?

65. The Universe Scanner (Apex Radar) scans 200-500 tickers for anomalies. But scanning is NOT the same as FINDING. What is the EXPECTED hit rate — how many anomalies per day pass all filters and produce a tradeable signal? If the hit rate is <1 per day, the system is capacity-constrained (it can only trade what S15 core universe provides). The Radar adds value ONLY if it surfaces opportunities that S15's core universe doesn't see. How many INCREMENTAL signals per month does the Radar contribute beyond what S15 core would find?

66. The plan says the LSE leveraged ETP market has 150-200 products across GraniteShares, Leverage Shares, and WisdomTree. After Amihud filtering, the tradeable count is estimated at 30-50. But how many of these 30-50 are ACTUALLY traded by the system? The plan's CORE universe has 12. EXTENDED has 10. SECTOR_RADAR has 13. That's 35 total, but SECTOR_RADAR tickers are "monitored" not "traded." Effective tradeable universe = 22. After ISA eligibility, broker routability, and spread filtering, how many tickers are ACTUALLY executable on any given day?

67. The plan claims the Chandelier ladder "converts modest edge into compounding returns." But the ladder ALSO converts modest LOSSES into compounding losses. On a losing trade (Rung 0 exit at -1R = -3%), the loss is the FULL -3% with no mitigation. The asymmetry is: winners get +6.17% average (with ladder), losers get -3.0% flat. But the ladder only helps ON WINNERS. At 55% WR, the system has 45% FULL-LOSS days. The expected daily return is: 0.55 × 6.17% - 0.45 × 3.0% = 3.39% - 1.35% = 2.04%. Wait — that's ABOVE the 2% target. But this assumes EVERY winning trade reaches the +6.17% blended average. If 30% of winners exit at Rung 0 (breakeven after spread), the blended average drops to: 0.70 × 6.17% + 0.30 × 0% = 4.32%. Expected daily: 0.55 × 4.32% - 0.45 × 3.0% = 2.38% - 1.35% = 1.03%. Much more realistic. What is the TRUE breakeven-exit rate?

68. The plan specifies "63 MTRL days" for the Go-Live Gate. MTRL = Mean Time to Recover from Loss = 1 day (for 1 trade/day). So 63 MTRL = 63 trading days = ~3 months. But MTRL = 1 day is an ASSUMPTION, not a measurement. If the actual MTRL is 3 days (consecutive losers, then a drawdown cascade reducing size, then slow recovery), 63 MTRL = 189 trading days = ~9 months. Has the MTRL been calibrated from backtest data?

69. The plan targets UK ISA for £0 tax on gains. But the ISA requires a UK-registered broker. Which brokers offer: (a) ISA wrapper, (b) access to ALL LSE leveraged ETPs, (c) API access for automated trading, (d) competitive spreads? Interactive Brokers offers ISA accounts but has reported issues with specific leveraged ETP access. IG Index and Hargreaves Lansdown offer ISAs but with LIMITED leveraged ETP selection and NO API. Is the broker constraint a binding limitation that the plan hasn't addressed?

70. The plan says the Apex Radar reroutes US discoveries to LSE ETPs. But some US moves happen OUTSIDE LSE hours (pre-market 04:00-08:00 ET, after-hours 16:00-20:00 ET). If NVDA has a volume anomaly at 07:30 ET (12:30 UK — LSE is open), the reroute works. But if the anomaly is at 17:00 ET (22:00 UK — LSE is closed), the reroute is impossible. What percentage of US volume anomalies occur during LSE hours? If <50%, the Radar's effective hit rate is halved.

71. The plan targets "1 trade per day." But profitable compounding requires EVERY TRADING DAY to contribute. On days when S15 doesn't fire (no signal meets threshold), the daily return is 0%. Zero-return days don't compound — they dilute. If the system fires on 200 of 252 days, and achieves +1.14%/day on firing days, the annual return is: (1.0114)^200 × (1.0)^52 = £96K — 46% less than the £177K assumption of 252 firing days. Does the plan account for zero-signal days?

72. The plan's 8-factor DynamicSizer includes "Liquidity Factor" based on Kyle's Lambda (Q/V ratio). But Kyle (1985) measures market IMPACT from informed trading. For retail-size trades on ETPs (£1,000-£5,000), there is ZERO market impact — the market maker absorbs the order without price movement. Kyle's Lambda is therefore ~0 for this order size, making the liquidity factor = 0.5 (minimum). Is this factor always at minimum? If so, it's a constant, not a variable — remove it from the multiplicative chain to avoid unnecessary sizing reduction.

73. The plan says the correlation brake limits to 1 position when 3+ pairs exceed 0.70. But with 12 CORE tickers mostly Nasdaq-correlated, the brake may be PERMANENTLY at 1 position. In that case, the system CAN ONLY hold 1 position at a time — which is the current architecture anyway. Is the correlation brake adding any safety for a 1-position system, or is it infrastructure for Phase B multi-position mode?

74. The plan's Monte Carlo simulation for scenario planning used 10,000 paths. But 10,000 paths at 252 days each = 2,520,000 simulated trading days. At the DAILY level, this is sufficient. But for TAIL EVENTS (drawdowns > 10%), 10,000 paths may not adequately sample the left tail. The probability of a 10% drawdown in 252 days at 1 trade/day is approximately 5-15%. With 10,000 paths, that's 500-1,500 tail events — sufficient. But the probability of a 25% drawdown (system-killing event) is perhaps 0.1-1%. That's 10-100 events — potentially insufficient. Were tail risk scenarios adequately sampled?

75. The plan's Academic Reviewer persona uses 55+ citations. But how many of these citations have been ACTUALLY READ (vs. cited second-hand from textbooks or other plans)? The Avellaneda-Stoikov (2008) model, for example, has very specific assumptions (continuous-time, Poisson order arrivals, inventory risk aversion parameter γ). If the plan is using the model outside these assumptions, the citation is misleading. Which citations are being applied OUTSIDE their original scope?

WHAT COULD KILL THIS ON DAY 1 (Questions 76-100):
76. Broker API failure during order submission. The plan uses a broker API for execution. APIs have rate limits, timeout errors, and authentication expiry. If the broker API returns HTTP 429 (rate limit) during an order submission, does the system retry? How many retries? What if the retry succeeds but the ORIGINAL order also went through (double execution)?

77. yfinance returns a SPLIT-ADJUSTED price retroactively. LSE leveraged ETPs occasionally undergo share consolidations (reverse splits). yfinance applies the adjustment retroactively, changing historical prices. The ATR calculation, which uses the last 15 minutes of prices, suddenly produces a DIFFERENT ATR because old candles were adjusted. The Chandelier stop, which uses ATR, is now at the WRONG level. Does the system detect split-adjusted data?

78. The EC2 instance runs in us-east-1c. LSE data comes from yfinance (US servers). Broker API is in the UK. The latency triangle: EC2 (Virginia) → yfinance (California?) → Broker (London) adds 100-200ms per leg. Total round-trip for signal → data → execution = 300-600ms on top of the 60-second scan cycle. Is this latency acceptable, or should the EC2 be in eu-west-2 (London)?

79. Redis AOF file grows without bound. The plan uses Redis for state persistence via AOF. With continuous writes (Chandelier state every 60s, regime state, queue state), the AOF file grows at approximately 1KB/minute = 720KB/day = 182MB/year. On a t3.small with 8GB EBS root volume, 182MB/year is fine. But Redis REWRITE compacts the AOF file periodically, which is a CPU-intensive operation. Does the rewrite happen during market hours? If so, does it cause a scan delay?

80. The plan specifies "paper mode, £10,000 starting equity." But paper mode has no BROKER INTERACTION. There is no actual order submission, no fill confirmation, no position reconciliation. The transition from paper to live requires: (a) broker API integration, (b) order management, (c) position reconciliation, (d) fill quality monitoring. These are NOT in Phase A or B. When do they get built?

81. The system logs "every rejection through the 33-gate gauntlet." At 9,900 evaluations/day with ~99% rejection rate, that's ~9,800 rejection records per day × ~500 bytes = ~5MB/day. Over 252 trading days = 1.26GB/year. On a t3.small with 8GB root volume, and after Docker images (~3GB), OS (~2GB), data (~1GB), there's ~2GB free. The rejection log consumes the remaining disk space in ~18 months. Is there a log rotation / archival strategy?

82. The plan's emergency flatten sends P0 Telegram alerts. But Telegram's API has rate limits (30 messages/minute per bot). During a cascade failure, the system might try to send: flatten alert + regime change alert + CDaR breach alert + drawdown cascade alert + Dead Man's Switch alert = 5 messages in rapid succession. With rate limiting, some alerts may be DROPPED or DELAYED. Are alerts queued? Is there a fallback (email, SMS)?

83. The plan uses UTC timestamps throughout. But LSE trading hours are BST (UTC+1) during summer and GMT (UTC) during winter. The 16:20 EOD exit is specified in UK local time. Does the system correctly handle the BST→GMT transition in late October? If it uses UTC internally, the 16:20 UK = 15:20 UTC in winter but 15:20 UK = 14:20 UTC in summer. Is the EOD exit time calculated from UTC or UK local?

84. What happens when two scan cycles OVERLAP? If scan T takes 65 seconds (exceeding the 60-second budget), scan T+1 starts before scan T finishes. The indicators for T+1 use data that was fetched at T+1's start, but the execution from scan T is still in progress. Could this produce a race condition where two trades are opened simultaneously?

85. The plan uses half-Kelly sizing. But Kelly sizing assumes REINVESTED capital. In the ISA, there is no withdrawal — all profits are reinvested. But the position sizing uses CURRENT EQUITY, which includes unrealised P&L. If a position is at +4% (above Rung 1, below Rung 2), the equity includes the +4% unrealised gain. The NEXT trade is sized on inflated equity. If the current trade then reverses to stop loss (-3%), the COMBINED loss is -3% (current) + under-recovery from oversized next entry. Is position sizing based on REALISED equity or MARK-TO-MARKET equity?

86. The plan specifies 7 S15 indicator categories but doesn't specify what happens when INDICATOR DATA IS MISSING. If VWAP data is unavailable for a ticker (no volume data from yfinance), does VWAP score = 0 (penalty), = average (neutral), or = NaN (propagates through)? Each choice produces different confidence scores and potentially different trading decisions.

87. The plan's Stoikov EV gate uses spread_cost as an input. But spread_cost varies by TIME OF DAY — tight at 10:00 UK, wide at 08:00 and 16:30. If the EV gate uses a STATIC spread cost (e.g., 40 bps), it understates cost at market open and overstates cost at midday. Does the EV gate use time-of-day adjusted spread cost?

88. The plan's Nightly Activation runs at what time exactly? If it runs at 17:00 UK (after LSE close), it uses end-of-day data. But US markets are still open until 21:00 UK. The regime might change during the US session. Should Nightly Activation run at 21:30 UK (after US close) instead?

89. The plan targets leveraged ETPs which have DAILY rebalancing. This creates a predictable intraday pattern: the ETP market maker must rebalance in the last 30 minutes of the session. AEGIS knows this (Rule 3: bank at Rung 2 before 14:55 UK). But the market maker ALSO knows that informed traders know this. The market maker can front-run the front-runners by rebalancing EARLY (14:30-14:50) or LATE (15:00-15:20). Is the 14:55 banking deadline based on actual rebalancing time data, or is it an assumption?

90. The system's confidence score is 0-100. The threshold is 75. But what is the DISTRIBUTION of confidence scores? If 90% of scores are below 30 and 8% are above 85, with only 2% in the 30-85 range, the threshold of 75 is meaningless — it's either a clear signal or nothing. If the distribution is more uniform, the threshold matters. What is the actual empirical distribution of S15 confidence scores?

91. The plan uses ATR on 15-minute bars for stop loss calculation. But the first scan of the day (08:00 UK) has ZERO 15-minute bars for today. It must use yesterday's ATR. If yesterday was a quiet day (ATR = 1.5%) and today opens with a gap move, the stop based on yesterday's ATR is TOO TIGHT. The stop gets hit immediately on normal volatility. Does the ATR calculation use a WEIGHTED average of recent sessions plus today's developing data?

92. The plan's Inverse Pivot requires VIX > 28.5 AND price < 50-EMA AND within 24h of spike. But the 50-EMA on a 3x ETP moves 3× faster than on the underlying. A -2% underlying move puts price below the 50-EMA on the ETP but NOT on the underlying. Is the 50-EMA check on the ETP or the underlying? If ETP, the trigger is too sensitive. If underlying, it's correctly calibrated.

93. The plan has 26 identified fatal flaws. How many have been FIXED in code vs. DOCUMENTED in the plan? The Implementation Reality Audit (v13.3) found "NONE of v13.x improvements implemented in code." Is this STILL true as of v13.8? If so, the plan has accumulated 35 amendments on top of a codebase that hasn't changed at all.

94. The plan's VIX hysteresis uses discrete thresholds (25, 33, 35, 43, 45). But VIX is available as a continuous value. The system classifies VIX into discrete buckets. At VIX = 34.9, the regime is HIGH_VOL. At VIX = 35.1, it's RISK_OFF. The Kelly multiplier changes from some value to 0.0. A 0.2-point VIX move (noise) changes position sizing from nonzero to ZERO. Is this discontinuity intentional? Should there be a LINEAR interpolation zone between thresholds?

95. The plan uses Docker Compose for deployment. But Docker Compose doesn't support HEALTH CHECK DEPENDENCIES between containers. If the engine container starts before Redis is fully initialised, the engine will crash on the first Redis write. Docker Compose has `depends_on` but it only waits for CONTAINER START, not SERVICE READINESS. Is there a healthcheck retry loop in the engine's startup sequence?

96. The plan's 33-gate gauntlet includes "regime gate (HMM state != RISK_OFF/SHOCK)" as Gate 1. This gate BLOCKS all signals during RISK_OFF. But the Inverse Pivot is SUPPOSED to trade during RISK_OFF (VIX > 28.5). Does the Inverse Pivot bypass Gate 1? If so, how — does it use a separate pipeline? If not, the Inverse Pivot can never fire (Gate 1 blocks it).

97. The plan says "no further architectural changes without formal change request." But 10 review rounds have produced 35 amendments. The change control process has been NON-EXISTENT — each round produces amendments that are applied immediately. Is the "architecture lock" real or aspirational?

98. The plan's 8-factor DynamicSizer includes "Signal Confidence" as factor 7 (range 0.6-1.0). But Signal Confidence is the OUTPUT of the S15 scoring model. Using the model's output as an input to position sizing creates a SELF-REINFORCING loop: high confidence → larger position → more profit → validates the confidence (survivorship bias). Is there any mechanism to prevent this feedback loop?

99. The plan has been reviewed by 3 AI models across 10 rounds. But all 3 models have the SAME failure mode: they evaluate the plan's INTERNAL CONSISTENCY rather than its EXTERNAL VALIDITY. A perfectly internally consistent plan can still fail in the real market. What EXTERNAL validation has been performed? Has ANY part of this plan been tested on actual market data (not simulated paths)?

100. Final question — the FIREPOWER question: The plan has 7 Phase A items, 6 Phase B items, 7 Phase C bookmarks, 15 risk controls, 33 gates, 8 sizing factors, 5 ladder rungs, 7 regime states, 8 indicators, 11 Go-Live criteria, 6 drawdown levels, and 26 fatal flaws. That's 129+ ACTIVE COMPONENTS that must work together simultaneously. Each component has a failure probability of perhaps 1-5%. The probability that ALL 129 components work correctly simultaneously is: at 1% failure each = 0.99^129 = 27%. At 2% failure = 0.98^129 = 7%. At 5% failure = 0.95^129 = 0.1%. THE SYSTEM HAS A <30% CHANCE OF WORKING PERFECTLY ON ANY GIVEN DAY even with 99% per-component reliability. How does the plan ensure that component failures DEGRADE GRACEFULLY rather than cascade? What is the minimum set of components that MUST work for the system to be safe (even if not profitable)?

FINAL DIRECTIVE: After answering all 100 questions, provide:

A) "COMMAND TREE FIREPOWER AUDIT" — For each decision node in the signal → execution chain, rate the FIREPOWER (data quality + decision logic + execution capability) on a 1-10 scale. Identify the WEAKEST NODE.

B) "PRECISION ERROR BUDGET" — Sum all calibration errors found. What is the TOTAL expected annual return deviation from stated targets due to precision errors alone?

C) "MINIMUM VIABLE SYSTEM" — Strip the plan to the absolute minimum that would produce a SAFE (not necessarily profitable) trading system. How many components can be removed?

D) "WHAT WOULD YOU DO BETTER?" — Not theory. Specific implementation decisions. 8 hours of coding, right now. What do you build?

E) "MODEL RISK MANAGEMENT REPORT" — Full MRM assessment per Procedure 1 above. Model inventory table with materiality tiers.

F) "STRESS TEST RESULTS" — For each of the 9 scenarios in Procedure 3, state: SURVIVES or FAILS, peak drawdown, which risk controls activated, and what was missing.

G) "OPERATIONAL RISK REGISTER" — Top 15 operational risks per Procedure 4, ranked by expected loss (probability × impact). Include KRI dashboard specification.

H) "BEST EXECUTION ANALYSIS" — Per Procedure 5. Include adversary exploitation playbook: how would a sophisticated market maker extract value from this system?

I) "PRE-TRADE RISK LIMIT SCHEDULE" — Complete limit table per Procedure 6 with derivations.

J) "COUNTERPARTY & LIQUIDITY RISK MAP" — Per Procedure 7. FSCS coverage analysis, historical crisis liquidity data for LSE ETPs, and the equity "death zone" threshold.
```

---

## CHATGPT COMMAND

```
You are performing a ROUND 11 ADVERSARIAL REVIEW of the NZT-48 AEGIS Alpha-Omega Master Plan v13.8. This plan has survived 10 review rounds, 35 amendments (GPT-01 through GPT-35), and input from 3 AI models. Round 10 found the Kelly math contradiction (now resolved with ladder tail capture) and identified dead code (R-10, R-12, Inverse Pivot). Round 11 targets: (1) PRECISION ERRORS that compound into catastrophic failures, and (2) whether the COMMAND TREE has enough FIREPOWER at every decision node to actually execute the objectives.

MANDATORY RULES:
1. Every answer must include SPECIFIC NUMBERS from the plan and your CORRECTED NUMBER with derivation.
2. No "it depends" — give exact values, formulas, or architectural decisions.
3. You MUST treat the "Implementation Reality Audit" as binding: if it's not in code, it doesn't exist. The plan is a SPECIFICATION, not a system.
4. For each finding, state: SEVERITY (P0-catastrophic, P1-serious, P2-moderate, P3-minor) and EFFORT TO FIX (hours).

HEDGE FUND INSTITUTIONAL REVIEW PROCEDURES:
Beyond the 100 questions, you MUST also execute these formal institutional review processes. Every serious quant fund runs these before deploying a single pound of capital. Output each as a separate deliverable section.

PROCEDURE 1 — MODEL RISK MANAGEMENT (MRM) ASSESSMENT (SR 11-7 / PRA SS1/23):
Apply the Fed SR 11-7 / PRA SS1/23 Model Risk Management framework to every quantitative model in the plan:
- Model Inventory: List every model (S15 scoring, Kelly sizing, Stoikov EV, HMM regime, CUSUM, Bayesian base-rate, Cornish-Fisher CDaR, Kinetic Time-Stop, DynamicSizer). For each: inputs, outputs, assumptions, limitations, materiality tier (Tier 1 = P&L-determining, Tier 2 = risk-determining, Tier 3 = operational).
- Conceptual Soundness: For each Tier 1 model — is the theory valid for this specific application? Are assumptions met? Where do they break?
- Outcomes Analysis: Define OBSERVABLE METRICS that prove each model works vs fails in the first 63 trading days.
- Scope Testing: Is each model being used within its designed scope? (Stoikov = market-maker quoting model used as price-taker EV gate — is this valid?)
- Documentation Quality: Rate 1-5 for each model. Can a competent quant implement from the spec alone with zero ambiguity?

PROCEDURE 2 — INDEPENDENT VALUATION VERIFICATION (IVV):
Perform independent P&L verification:
- P&L Attribution: Decompose expected +1.14%/day into: alpha (stock selection timing), beta (market direction × leverage), gamma (convexity from leverage), theta (variance drag), spread cost, commission, slippage. What % is alpha vs beta? If the market returns +0.04%/day and leverage = 3x, beta = +0.12%/day. The remaining +1.02%/day must be pure alpha. Is +1.02%/day alpha realistic on delayed retail data?
- Valuation Uncertainty: At 60-second polling with ±0.15% price uncertainty, how does this propagate through sizing, stop levels, and rung thresholds?
- Backtest vs Forward: Zero backtest has been performed on real data. What are the consequences of deploying a system validated ONLY by Monte Carlo with assumed parameters?

PROCEDURE 3 — STRESS TESTING & SCENARIO ANALYSIS (Basel III / FRTB):
Run these scenarios against the plan's risk architecture. For each: determine if the system SURVIVES (drawdown < 25%) and what SPECIFIC risk controls activate:
- HISTORICAL: 2020-03-09 to 2020-03-23 (COVID crash, QQQ -28% in 10 days, VIX 82.69)
- HISTORICAL: 2018-02-05 (Volmageddon, XIV → zero, VIX 17→50 intraday)
- HISTORICAL: 2015-08-24 (Flash Crash, QQQ ETF opened -8%, circuit breakers, spreads +500%)
- HISTORICAL: 2022-01-24 to 2022-03-14 (orderly decline -22%, VIX stayed below 35 — the regime classifier MISSES this)
- HYPOTHETICAL: yfinance dark for 4 hours during -5% underlying move
- HYPOTHETICAL: LSE halts all leveraged ETPs for 2 hours (circuit breaker)
- HYPOTHETICAL: Broker API rejects all orders for 30 minutes (auth expiry)
- HYPOTHETICAL: Redis crash while holding position — all Chandelier state lost
- REVERSE STRESS TEST: What is the SMALLEST market event that causes >15% portfolio drawdown?

PROCEDURE 4 — OPERATIONAL RISK ASSESSMENT (Basel II RCSA):
Full Risk & Control Self-Assessment:
- People Risk: Bus factor = 1. What happens if operator is unavailable 48 hours? Is there a runbook? Can anyone else operate the Dead Man's Switch override?
- Technology Risk: Map every single point of failure (EC2, yfinance, broker, Docker, Redis, Lambda). For each: failure probability, blast radius, detection time, recovery time.
- Process Risk: List every automated process (scan loop, deploy, backup, nightly activation). For each: failure mode, detection mechanism, recovery procedure.
- External Risk: Assess probability and impact of: FCA restricts leveraged ETP retail access, IBKR removes ISA API, Yahoo blocks scraping, yfinance library abandoned, LSE changes ETP structure.
- Key Risk Indicators (KRIs): Define 10 daily-monitored metrics. For each: name, source, warning threshold, critical threshold, automated response.

PROCEDURE 5 — BEST EXECUTION REVIEW (MiFID II Article 27):
- Venue Analysis: LSE only — are there alternative venues (Xetra, cross-listings)? Would SOR (Smart Order Routing) improve fills?
- Execution Quality: Define expected fill rate, slippage, spread capture, latency-to-fill. What are minimum acceptable values before the strategy becomes negative-EV?
- Market Impact: At what size does AEGIS become detectable? What changes when the market maker identifies the pattern?
- Adversary Detection: How would Jane Street / Flow Traders / Optiver specifically detect and exploit AEGIS's order flow? What are the COUNTERMEASURES?
- Time-of-Day: What is the OPTIMAL trading window for LSE ETPs based on spread, liquidity, and signal freshness?

PROCEDURE 6 — PRE-TRADE RISK LIMIT FRAMEWORK:
Define the complete limit structure as a prime broker would require:
- Hard Limits (auto-halt): Max position % equity, max daily loss, max VaR, max leverage, max concentration. Show the DERIVATION for each value.
- Soft Limits (alert + review): Max consecutive losses, max drawdown duration, max correlation, min signal quality, min data freshness.
- Escalation Matrix: What alerts at each level? What's automated vs requires human decision?
- Limit Back-Testing: Would these limits have been triggered during 2020 COVID, 2018 Volmageddon, 2022 rate hiking? If yes, would the system have survived? If no, are the limits too loose?

PROCEDURE 7 — COUNTERPARTY & LIQUIDITY RISK ASSESSMENT:
- Broker Counterparty Risk: If the ISA broker fails (e.g., 2011 MF Global, 2023 SVB impact on fintech), are ISA assets protected under FSCS (£85K limit)? At what portfolio size does FSCS protection become insufficient?
- Market Liquidity Risk: During the 2020 flash crash, what happened to LSE leveraged ETP spreads and volume? Were they even TRADEABLE? Pull actual data if possible. If spreads blew out to 10%, the Emergency Flatten executes at -10% to -15% (far worse than -5% threshold).
- Funding Liquidity Risk: The system reinvests all profits. If a drawdown reduces equity below minimum position size threshold (after DynamicSizer), the system can't trade. What equity level is the "death zone" below which recovery is mathematically impossible?

ADOPT FOUR PERSONAS SIMULTANEOUSLY:

PERSONA 1 — CHIEF QUANT (30 years, $2B+ fund, survived every crisis since 1987 crash)
Focus: Where the DECIMAL POINTS are wrong. A 0.1% daily calibration error = 28% terminal wealth deviation over 252 days. Find every 0.1% error.

PERSONA 2 — LEAD SYSTEMS ARCHITECT (built exchange-grade systems, 10M+ trades/day)
Focus: The execution chain's WEAKEST LINK. Every node where data quality degrades, latency accumulates, or state becomes inconsistent. The system is only as strong as its weakest node.

PERSONA 3 — CHIEF RISK OFFICER (ran risk at a leveraged ETP market maker — knows the product, the flow, the manipulation)
Focus: The ADVERSARY'S PLAYBOOK. How would Jane Street / Flow Traders / Optiver EXPLOIT this system? What patterns would they detect in AEGIS's order flow? How would they WIDEN spreads specifically when AEGIS is likely to trade?

PERSONA 4 — ACADEMIC REVIEWER (published specifically on leveraged ETP decay, variance drag, and daily rebalancing)
Focus: Where the CONTINUOUS-TIME math is applied to a DISCRETE-TIME system. Where NORMAL distribution assumptions meet kurtosis-10 reality. Where ACADEMIC models meet market-maker adversaries.

FOR EACH OF THE FOLLOWING 16 SECTIONS, provide:
- **Precision Errors**: Specific numerical miscalibrations with corrected values
- **Command Tree Gaps**: Decision nodes without sufficient firepower
- **Adversary Exploitation**: How a sophisticated counterparty would game this component
- **Severity + Fix Effort**: P0-P3 rating and hours to fix

SECTIONS TO REVIEW:

1. KELLY PAYOFF RESOLUTION (GPT-29) — Rung probability assumptions, blended average win derivation
2. RISK STATE MACHINE (R-01B GPT-30) — State transitions, conflict resolution, single-executor model
3. GAP & AUCTION RISK CONTROLS (R-01C GPT-33) — Overnight gap, auction exclusion, size caps
4. SIGNAL STALENESS CONTROLS (GPT-33) — max_signal_age, fail-closed, data freshness
5. DEAD CODE AUDIT (GPT-31) — R-10, R-12, Inverse Pivot resolution quality
6. EMERGENCY FLATTEN RECALIBRATION (GPT-32) — -5% threshold adequacy
7. SETUPFINGERPRINT DIMENSIONALITY (GPT-34) — Progressive expansion, N accumulation rate
8. CHANDELIER 5-RUNG PROFIT LADDER — Variance drag on rung thresholds, hold time sensitivity
9. DYNAMICSIZER 8-FACTOR KELLY — Factor interaction, floor effects, position size viability
10. BAYESIAN STRANGER PENALTY — Graduation speed, over/under-penalisation
11. 8-INDICATOR S15 CONSENSUS — Collinearity, effective DoF, threshold calibration
12. GO-LIVE GATE (11 CRITERIA) — Statistical power, false pass rates
13. NIGHTLY ACTIVATION + BASE-RATE GATE — Rollout cost, data sufficiency
14. STOIKOV EV GATE + GAUNTLET — Rejection rate, latency budget, false veto rate
15. REGIME CLASSIFIER + VIX HYSTERESIS — Classification accuracy, transition lag
16. DRAWDOWN CASCADE + CIRCUIT BREAKERS — Level calibration, interaction with Risk State Machine

THEN ANSWER ALL 100 OF THESE QUESTIONS (specific, quantitative, brutal):

PRECISION & CALIBRATION (Questions 1-25):
1. The Kelly Payoff Resolution assumes Rung 2 probability of 40% conditional on win. This means 40% of winning trades reach +6% on a 3x ETP (+2% underlying). But intraday, a +2% underlying move on QQQ happens on roughly 25-30% of trading days (based on QQQ daily ranges). On days where it DOES happen, the move often occurs in the afternoon (US session), not during the morning window when S15 fires. What is the REALISTIC Rung 2 conditional probability based on actual QQQ intraday move data, segmented by time-of-day?

2. The blended average winner of +6.17% includes Rung 5+ at +18% with 5% probability. A +18% move on a 3x ETP requires a +6% intraday move on the underlying. QQQ has moved +6% intraday approximately 3 times since 2020 (March 2020 recovery, Nov 2020 vaccine day, Nov 2022 CPI). That's ~3 events in ~1,260 trading days = 0.24% probability, not 5%. If Rung 5+ probability is 0.24% instead of 5%, the blended average drops to: 0.15×0 + 0.40×4.7 + 0.25×7.0 + 0.1524×11.0 + 0.0024×18.0 = 0 + 1.88 + 1.75 + 1.68 + 0.04 = 5.35%. Kelly at 5.35%/3.0% = payoff 1.783: f* = (1.783×0.55 - 0.45)/1.783 = +0.297. Still positive but SIGNIFICANTLY lower than +0.331. What is the EMPIRICALLY GROUNDED blended average winner?

3. Walk through the COMPLETE execution chain timing: (a) yfinance fetch latency, (b) indicator calculation time, (c) S15 scoring time, (d) 33-gate gauntlet evaluation time, (e) signal queue insertion time, (f) consumer dequeue time, (g) execution latency. Total time from "market moves" to "order submitted" — is it under 60 seconds? Under 120 seconds? Over 120 seconds?

4. The Risk State Machine has monotonically-upward transitions during crisis (REDUCE → EMERGENCY_FLATTEN ok, reverse not ok). But what about the RECOVERY path? After EMERGENCY_FLATTEN, the system enters 30-min cool-down. After cool-down, does it go to NORMAL or REDUCE? If REDUCE, what conditions return it to NORMAL? The plan says "downward transitions require ALL conditions clear + cool-down elapsed." But "ALL conditions" — of what? All 15 risk controls? All 5 circuit breakers? This is underspecified.

5. The overnight gap control uses "2 ATR" as the gap threshold. But ATR is denominated in the ETP's price units, not percentage. For NVD3.L trading at £50.00 with ATR = £2.50 (5%), the 2 ATR threshold = £5.00 = 10%. For QQQ3.L trading at £200.00 with ATR = £4.00 (2%), the 2 ATR threshold = £8.00 = 4%. The gap control is therefore MORE PERMISSIVE for higher-priced, lower-vol ETPs. Should the threshold be in percentage terms (2 × ATR%) rather than absolute terms (2 × ATR£)?

6. The Emergency Flatten threshold was changed from -3% to -5% (GPT-32). The rationale was "a 3x ETP can drop 3% on a routine -1% underlying move." But -1% on QQQ is NOT routine — the median absolute daily move on QQQ is approximately 0.7-0.8%. A -1% move is a ~1.3σ event (happens ~10% of trading days). Is -3% actually "near-daily" as claimed, or is it more like "happens 2-3 times per month"? If the latter, was the recalibration to -5% too aggressive?

7. The signal staleness control drops signals older than 120 seconds. But the plan also has the OBI wait gate (R-12) which waits 2 minutes (120 seconds) if OBI > 0.80. A signal that triggers the OBI wait will ALWAYS be dropped by the staleness control before the OBI wait completes. The OBI gate and staleness control are MUTUALLY EXCLUSIVE. Since R-12 is now shadow-mode-only (GPT-31), this conflict is dormant. But if R-12 is ever promoted to enforcement (Phase C), it breaks. Is this documented?

8. The plan's scenario table shows "Conservative (55% WR, 2.5R)" but GPT-29 calculates the blended payoff ratio as 2.057R. The scenario table says 2.5R. Which feeds into the return calculation? If 2.5R: daily return = 0.55 × 2.5R × L - 0.45 × L = R × (0.55 × 2.5 - 0.45) = R × 0.925. If 2.057R: daily return = R × (0.55 × 2.057 - 0.45) = R × 0.681. The difference is 36%. Which number is correct?

9. The 8-factor DynamicSizer has Factor 8: "Liquidity Factor" based on Kyle's Lambda. For retail-size trades on LSE ETPs (£1,000-£5,000 notional), Kyle's Lambda approaches zero because there is NO market impact at this scale. The ETP market maker absorbs the order without price movement. This means Factor 8 ≈ 0.5 (minimum) always. But the factor REDUCES position size by 50% for no good reason. If Kyle's Lambda is inapplicable at this scale, should Factor 8 be removed (set to 1.0) for positions under £10,000?

10. The plan specifies the correlation brake fires at 3+ pairs exceeding 0.70. With 12 CORE tickers, there are C(12,2) = 66 pairs. If 50 of 66 pairs exceed 0.70 (because they're all Nasdaq-correlated), the brake fires on day 1 and NEVER releases. The system is permanently limited to 1 position. Is this the INTENDED behavior? If so, the correlation brake is a constant, not a control — and the DynamicSizer's Factor 6 (correlation load) should also be recalibrated since correlation is always high.

11. The Bayesian Stranger Penalty applies κ = 0.25 for brand-new tickers (n = 0, DSR = 0). This means sizing at 25% of normal. At £10K equity and 9.9% intended exposure (from Q19 above), the stranger-penalised size is 0.25 × £990 = £247.50. On a 3x ETP, this generates P&L of £247.50 × 6% = £14.85 on a Rung 2 winner. The commission cost alone (e.g., £5-10 per trade on IBKR) may exceed the potential profit. At what position size does the system's EXPECTED PROFIT become negative after commissions?

12. The Go-Live Gate requires "Win Rate ≥ 50%." But the Kelly resolution (GPT-29) shows the system is profitable even at 50% WR (f* = +0.257). So the Go-Live Gate threshold is at the MINIMUM viable level, with NO safety margin. If the true WR is 49% (below the gate threshold by 1%), the system SHOULD be allowed to go live (Kelly is still positive at 49% with 2.057R payoff: f* = (2.057×0.49 - 0.51)/2.057 = (1.008-0.51)/2.057 = +0.242). Is the Go-Live WR threshold too HIGH? Should it be 45% (the true break-even with the ladder payoff)?

13. The CDaR circuit breaker uses Cornish-Fisher expansion. The CF approximation for VaR_α is: VaR_α = μ + σ × [z_α + (z_α²-1)×S/6 + (z_α³-3z_α)×K/24 - (2z_α³-5z_α)×S²/36]. For 3x ETP daily returns with S (skewness) ≈ -0.8 and K (excess kurtosis) ≈ 8, z_0.05 = -1.645. CF VaR = -1.645 + (1.645²-1)×(-0.8)/6 + (-1.645³+3×1.645)×8/24 - ... = approximately -1.645 - 0.239 + 0.583 = -1.301. This is LESS extreme than the normal VaR (-1.645). The CF expansion is UNDERESTIMATING tail risk at high kurtosis because the polynomial terms partially cancel. The empirical VaR at 5% for 3x ETPs is approximately -2.5 to -3.0σ. CF says -1.3σ. The CDaR breaker triggers LATER than it should. This is a P0 calibration error. What is the correct approach?

14. The regime classifier uses VIX as its primary input. But VIX measures S&P 500 implied vol, NOT Nasdaq vol. The plan trades Nasdaq-correlated ETPs. When Nasdaq diverges from S&P 500 (e.g., sector rotation into value — Nasdaq drops while S&P is flat), VIX stays low but Nasdaq is in a genuine drawdown. The regime classifier says "TRENDING_UP" while Nasdaq ETPs are losing money. Should the regime classifier use VXN (Nasdaq volatility index) instead of VIX?

15. The plan's Monte Carlo simulation for bank/trail optimisation used 1,000,000 paths. But the simulation parameters MUST include the Chandelier exit mechanism to be valid. Did the simulation model the RUNG TRANSITIONS (breakeven at Rung 1, bank at Rung 2, trail tightening at Rung 3-4)? Or did it use a simplified model (fixed bank at +X%, fixed trail at -Y%)? If simplified, the optimisation result may not be valid for the actual 5-rung mechanism.

16-25. [Questions 16-25 follow the same precision pattern — each targeting a specific parameter value in the plan with corrected math]

16. The plan says the Kinetic Time-Stop uses max_tolerated_drag = 0.0015 (15 bps). But 15 bps over what time period? If 15 bps per MINUTE, the drag budget at 3x leverage and σ=0.25% is T_max = 0.0015/(0.0025²×9)×60 = 1.33 min. If 15 bps per HOUR, T_max = 80 min. The plan shows T_max = 1.33 min at σ=0.25%, which implies per-minute. But drag is typically expressed per HOLDING PERIOD, not per minute. Clarify the time unit.

17. The plan says the Emergency Flatten is "INDEPENDENT of HMM regime." But the Risk State Machine (R-01B) says "only the HIGHEST-PRIORITY state executes." If EMERGENCY_FLATTEN is a Risk State Machine state, it IS part of the regime/risk management hierarchy — not independent. Which is true?

18. The plan says the Nightly Activation requires "WR ≥ 0.55 AND EV > 0.2." What are the UNITS of EV? If R-multiples, EV > 0.2R means average profit > 0.2 × average loss = 0.2 × 3% = 0.6% per trade. At 1 trade/day, that's +151%/year — extremely demanding. If percentage, 0.2% per trade is +65%/year — still demanding but more achievable. Clarify units.

19. The plan's shadow markout tracker records +5m markout. At 60-second scan resolution, the "+5m markout" is recorded at the scan nearest to 5 minutes after exit. The timing error is ±30 seconds. At 3x leverage and σ=0.25%/minute, ±30 seconds = ±0.13% price uncertainty. For a +5m markout of +0.5%, the uncertainty is ±0.13% = ±26% relative error. Is the +5m markout USABLE with this noise level?

20. The Stoikov EV gate vetos trades where net_expected_return < 1.5 × stop_distance. Stop_distance = 1.5 ATR ≈ 3% on 3x ETP. So the threshold is net_expected_return > 4.5%. But the AVERAGE winner is +6.17% at 55% WR. So the expected return per trade = 0.55 × 6.17% - 0.45 × 3.0% = 2.04%. The Stoikov EV gate is set at 4.5% but the expected return is 2.04%. THE GATE WOULD VETO EVERY TRADE. Is the threshold correctly specified?

21. The plan says the 33-gate gauntlet is "sequential, strict chain." But sequential evaluation means Gate 33's evaluation is delayed by the time of Gates 1-32. If the average gate takes 2ms, the delay is 66ms — negligible. But some gates require DATA FETCHES (VIX check, correlation computation, CDaR calculation). These can take 100-500ms each. What is the WORST-CASE latency for a full 33-gate evaluation?

22. The plan specifies 8 exit conditions in priority order. But the EXIT_CHANDELIER_RUNG exit (P3) requires comparing current price against the rung threshold, which depends on the entry price and ATR at entry time. If entry ATR was 3% and current ATR is 5% (volatility increased), the Chandelier stop based on entry ATR is TIGHTER than appropriate for current conditions. Does the Chandelier trail adapt to CURRENT ATR or use ENTRY ATR? Each choice has different risk characteristics.

23. The plan's DynamicSizer Factor 3 (Regime) uses multipliers from 0.0 (RISK_OFF) to 0.6 (TRENDING_UP_STRONG). But the multiplier is applied to HALF-KELLY, not full Kelly. So the effective regime multiplier range is 0.0 to 0.3 of optimal Kelly. At 0.3 Kelly, the system is DRAMATICALLY under-betting in its best regime. Kelly theory says under-betting reduces risk but ALSO reduces expected growth. At 0.3 Kelly, expected growth rate is approximately 91% of optimal (Kelly growth = max at f*, and f*/3 gives roughly (1-4/9) = 56% of max log-growth). Is 0.3 Kelly the RIGHT trade-off for the risk tolerance?

24. The plan's Go-Live Gate requires "Dropped Signals (P0) = 0." But the signal staleness control (GPT-33) DELIBERATELY drops signals older than 120 seconds. A stale P0 signal IS a dropped P0 signal. If the staleness control ever drops a P0 signal, the Go-Live Gate FAILS. This creates a contradiction: the staleness control improves safety but prevents go-live. Resolution?

25. The plan has 15 risk controls (R-01 through R-15). R-01B (Risk State Machine) is supposed to COORDINATE all controls. But R-01B has 4 states while the controls have diverse trigger conditions. How does R-01B know which state to enter? Does it poll all 15 controls every scan cycle and take the MAX? If so, what is the computational cost of 15 control evaluations per scan?

COMMAND TREE FIREPOWER (Questions 26-50):
26. Map the COMPLETE data flow from market event to position entry. How many TRANSFORMATION STEPS are there? At each step, what INFORMATION is lost? By the time a market move reaches the execution decision, how much of the original signal remains?

27. The S15 confidence score is 0-100. The threshold is 75. In WHAT PERCENTAGE of historical scan cycles would a signal have exceeded 75? If it's <1%, the system fires once every ~100 scans = once every ~1.7 hours. If it's <0.1%, once every 17 hours — less than once per day on most days. The SIGNAL FREQUENCY determines the system's trading capacity.

28. The plan specifies that the Nightly Activation selects recipes for the next day. But the SELECTION CRITERIA (WR ≥ 0.55, EV > 0.2) require HISTORICAL DATA for each recipe. During months 1-6, no recipe has enough data (min_N = 15). ALL recipes remain active. The Nightly Activation provides ZERO value for 6 months. Is this the intended design, or should there be a Phase A version that uses simpler selection criteria?

29. The plan says the signal queue consumer routes signals to virtual_trader.open_position(). But virtual_trader is PAPER TRADING — it simulates fills without broker interaction. When transitioning to LIVE, the consumer must route to broker API instead. Is this transition a CODE CHANGE (risky) or a CONFIGURATION CHANGE (safe)? If code change, it introduces regression risk on the most critical path.

30. The plan has a "Plan-to-Code Proof Gate" (GPT-02) that blocks deployment if critical modules are missing. But what constitutes "missing"? If the module file EXISTS but is EMPTY (0 lines), does the gate pass? The plan says "file must contain `def test_` functions" — but a file with `def test_placeholder(): pass` satisfies this requirement while testing nothing. Is the gate checkable or gameable?

31. The plan's scan_health.json includes `risk_state`. But risk_state changes intra-day (NORMAL → REDUCE → NORMAL). The Go-Live Gate needs to verify "zero false flattens over 63 days." This requires HISTORICAL risk_state data, not just the current value. Is there a risk_state HISTORY log? If scan_health.json is overwritten each cycle, the history is lost.

32. The plan specifies that ISA Key B (broker routability) has 24-hour TTL. But broker routability can change MID-DAY (e.g., corporate action, halt, suspension). The 24-hour TTL means the system might trade a SUSPENDED instrument using stale Key B data. Should Key B TTL be reduced to 1 hour during market hours?

33. The Dead Man's Switch flattens via Lambda → broker API. But the Lambda function needs to AUTHENTICATE with the broker. If the broker API key is stored in the EC2 environment variables (which are unavailable to Lambda), the Lambda function CAN'T authenticate. Where is the broker API key stored for the Lambda function? AWS Secrets Manager? Environment variables? Hardcoded?

34. The plan accumulates 35 amendments. The v13.8 sign-off line describes GPT-29 through GPT-35. But where in the PLAN DOCUMENT is each amendment's text located? Is there an INDEX mapping amendment numbers to section locations? Without one, verifying that all 35 amendments were correctly applied requires reading the entire 7,000+ line document.

35. The plan's Emergency Flatten fires at -5% portfolio drawdown. But "portfolio drawdown" on a SINGLE-POSITION system equals POSITION drawdown. When the system scales to multi-position (Phase B), a -5% PORTFOLIO drawdown with 4 positions could mean one position at -20% while others are flat. The trigger should be recalibrated for position count. Is this recalibration planned for Phase B, or will the -5% threshold persist into multi-position mode?

36-50. [Adversary exploitation and command tree depth questions]

36. A sophisticated market maker (Jane Street / Flow Traders) can see ALL order flow on LSE ETPs. If AEGIS trades the same ticker at approximately the same time every day (because S15 fires during the US open crossover window, 14:30-15:00 UK), the market maker will detect the PATTERN. They can widen spreads specifically during that 30-minute window. Expected spread widening: 10-20 bps. At 40 bps base spread + 15 bps adversarial = 55 bps. The plan's spread gate checks spread < 2× median. If median is 30 bps, threshold = 60 bps. 55 bps passes the gate. The system pays 38% MORE per trade but the gate doesn't catch it. How does the plan detect GRADUAL adversarial spread widening that stays below the 2× threshold?

37. The plan's 33-gate gauntlet rejects ~95-99% of signal candidates (estimated from R10). But this rejection rate means the system trades on the 1-5% of scans that pass ALL gates. If the market maker observes that AEGIS trades are CLUSTERED at specific times (when all gates align), they can predict WHEN AEGIS will trade and front-run the entry by milliseconds. AEGIS uses market orders — the market maker can widen the ask by 5-10 bps just before the expected entry. How does the plan randomise entry timing to prevent predictability?

38. The plan banks 33% at Rung 2 via market order. The market maker sees a SELL order for 33% of a recently-opened position. This is a TELL — it reveals that the trader has a profit target (the market maker now knows the entry price and the +6% target). The market maker can use this information to PREDICT the remaining 67%'s trailing stop level. How does the plan prevent position information leakage through partial exits?

39. The Inverse Pivot trades inverse ETPs during RISK_OFF (VIX > 28.5). But during genuine crisis events, inverse ETP spreads BLOW OUT to 5-10× normal. The plan says "wait for first retracement, confirm spreads < 2.5× median." But during COVID March 2020, inverse ETP spreads remained elevated for DAYS, not minutes. The Inverse Pivot would wait, and wait, and wait — and miss the entire crash monetisation. Is there a MAXIMUM WAIT before the Inverse Pivot gives up?

40. The plan uses yfinance for VIX data. yfinance's VIX data is DELAYED by 15-20 minutes during US market hours (it scrapes from Yahoo Finance, which delays real-time data for free users). If VIX is at 28 (real-time) but yfinance shows VIX = 26 (delayed), the system stays in TRENDING_DOWN instead of transitioning to RISK_OFF. The 3-tick confirmation buffer adds another 180 seconds. Total VIX lag: 15-20 minutes (delay) + 3 minutes (buffer) = 18-23 minutes. During a flash crash, the system trades for 18-23 minutes at the WRONG regime's Kelly. What is the expected loss?

41. The plan's Chandelier exit uses ATR for trailing stop calculation. But yfinance's 1-minute OHLCV data for LSE ETPs has known issues: missing candles, zero-volume candles, and occasional price spikes (bad ticks). A single bad tick (e.g., price = £0.01 instead of £50) would produce an ENORMOUS ATR, widening the stop to a level that never triggers. Does the system have a bad-tick filter on incoming data?

42. The system runs on AWS EC2 in us-east-1. But AWS occasionally performs MAINTENANCE on instances with 5-minute notice. If maintenance hits during market hours, the Dead Man's Switch should trigger. But the 2-consecutive-failure trigger requires 2 minutes of downtime. AWS maintenance reboots take 3-5 minutes. The Dead Man's Switch fires after 2 minutes and starts flattening. But the system comes back online after 3-5 minutes with positions already partially flattened. Can the system detect "this was scheduled maintenance, don't flatten"?

43. The plan's 8-indicator consensus includes "Macro" (weight 1.0x). What SPECIFICALLY is the Macro indicator? Is it based on VIX level, DXY, credit spreads, or something else? If it's VIX-derived, it's REDUNDANT with the regime classifier (both use VIX). If it's DXY-derived, DXY and Nasdaq are negatively correlated — a strong DXY is bearish for Nasdaq ETPs. Is this relationship correctly modeled?

44. The plan targets "2% daily" but actually expects "1.14% daily." The user (operator) sees "2% daily" as the headline target. When the system consistently delivers 0.7-1.2% daily (good performance by the plan's own admission), the operator perceives UNDERPERFORMANCE and may be tempted to override risk controls. Is there a mechanism to set OPERATOR EXPECTATIONS correctly, or will the 2% headline create dangerous pressure to over-trade?

45. The plan's Go-Live Gate has 11 criteria that ALL must pass. But the criteria have DIFFERENT confidence levels. "System Uptime > 99.5%" is easily verifiable. "Win Rate ≥ 50%" has wide confidence intervals at n=60. "Zero false flattens" depends on the DEFINITION of "false." Should the Go-Live Gate use different CONFIDENCE THRESHOLDS for different criteria rather than a binary pass/fail?

46. The plan says Phase A is "BINARY: 7/7 or informational only." But what if A-1 through A-5 are perfect and A-6 (Exit Taxonomy) has a minor bug (e.g., one of 8 exit reasons logs "UNKNOWN" instead of the correct reason)? The bug doesn't affect trading — positions still exit correctly. But the data is slightly contaminated. Is this a Phase A failure? Should there be a SEVERITY classification for Phase A items?

47. The plan's Chandelier exit stores state in Redis. But Redis is IN-MEMORY. A t3.small has 2GB RAM. Redis overhead + AOF persistence + data structures. How much memory does the Chandelier state consume per position? With 1 position: ~500 bytes. With 10 positions (Phase B): ~5KB. Negligible. But the REAL memory concern is the Python process: main.py + all modules + ML model + yfinance data + indicator buffers. What is the measured memory footprint of the running engine?

48. The plan uses monthly JSONL rotation for trade logs. But the Go-Live Gate needs 63 days of continuous data. If the gate evaluation spans a month boundary (e.g., starts March 15, ends May 17), the data is split across 3 files (March, April, May). Is the gate evaluation script designed to read across multiple JSONL files?

49. The plan's base-rate gate uses beta-binomial posterior: Beta(1+wins, 1+losses). With n=20, wins=11, losses=9: posterior = Beta(12, 10). Lower 10th percentile = beta.ppf(0.10, 12, 10) = 0.417. This is BELOW the 0.55 threshold — the gate VETOES a 55% observed WR at n=20. The gate is TOO CONSERVATIVE at small N, penalising the system during its most critical early phase. At what N does a 55% observed WR first PASS the gate?

50. The plan has accumulated 26 known fatal flaws. Of these, how many are ACTUALLY FATAL (system literally cannot function) vs. DEGRADING (system functions suboptimally)? The r_multiple = 0.0 bug is truly fatal (breaks all downstream ML). The phantom tickers are degrading (system trades wrong tickers but still trades). Reclassifying flaws by severity would prioritise the Phase A build order.

REAL-LIFE TARGETS & EXECUTION (Questions 51-75):

51. The plan says the system needs ONE stock per day capable of a 2% underlying move. Over the past 5 years, on what percentage of trading days did at least ONE ticker in the CORE universe (QQQ3.L, NVD3.L, TSL3.L, etc.) have a +2% intraday move on the underlying? If less than 40%, the system is capacity-constrained — not enough opportunities to compound daily.

52. The plan uses ISA wrapper for £0 tax. But ISA-eligible ETPs must be LISTED on a recognised stock exchange (LSE qualifies). If GraniteShares or Leverage Shares delists a product (has happened — some were delisted in 2023), the system holds a position in a DELISTED instrument. The ISA still shelters it, but the position is ILLIQUID. How does the system detect and handle delistings?

53. The plan targets £10K → £177K in Year 1 (Moderate scenario). But this requires COMPOUND GROWTH — each day's profit is reinvested. In an ISA, there are no withdrawals (by design — the operator wants maximum growth). But what if the operator needs to withdraw money (emergency, opportunity cost)? ISA withdrawals are PERMITTED but reduce the ISA allowance. Does the system model withdrawals?

54. The plan's S15 fires once per day. But on the BEST days (high vol, strong trend, wide moves), the system should arguably trade MORE aggressively — maybe 2-3 entries across different ETPs. On the WORST days (low vol, no trend), it should trade LESS (or not at all). The fixed "1 trade/day" constraint treats all days equally. Is a VARIABLE signal frequency (0-3 trades/day based on regime and opportunity count) better than fixed 1/day?

55. The plan uses the Chandelier 5-rung ladder for profit management. But the ladder assumes the ETP price moves CONTINUOUSLY. In reality, LSE ETPs trade with discrete TICKS (minimum price increment). For a £50 ETP with a 0.5p tick size, the price can only move in 0.01% increments. The Rung 1 threshold (+1.5 ATR) with ATR = £2.50 is +£3.75 = £53.75. This is achievable in tick-size increments. But the trailing stop at breakeven (£50.20 including spread) requires a tick at EXACTLY that level. If the price skips from £50.15 to £50.25, the stop should trigger at £50.15 (below breakeven). Does the system handle tick-level granularity correctly?

56. The plan says Shadow Markout tracking requires "continuous price monitoring of exited positions until EOD." But what counts as "continuous"? If monitoring every 60 seconds (same as primary scan), the shadow tracker uses the SAME yfinance data feed. No additional API calls needed — just record the price at each scan cycle for the exited ticker. Is the shadow tracker truly "additional" overhead, or is it just a few extra Redis writes per scan?

57. The plan's Emergency Flatten uses market orders. But LSE has a mechanism called "auction mode" where continuous trading is suspended and replaced by periodic auctions. During high volatility, individual ETPs can enter auction mode (triggered by price monitoring extensions). Market orders in auction mode become auction participation orders with UNKNOWN fill prices. Does the Emergency Flatten handle auction-mode ETPs differently?

58. The plan targets leveraged ETPs with daily rebalancing. But some newer ETPs use INTRADAY rebalancing (e.g., reset when the underlying moves >10%). These products don't have the same variance drag profile as daily-rebalanced products. Is the plan's variance drag calculation correct for ALL ETPs in the registry, or only for daily-rebalanced ones?

59. The plan uses Redis for state persistence. But Redis EVICTION policies can silently delete keys when memory is full. The default policy is `noeviction` (return errors on write). If Redis returns an error on a Chandelier state write, the rung state is LOST. Does the engine handle Redis write errors? Does it have a fallback (e.g., write to SQLite)?

60. The plan accumulates complexity across 10 review rounds. Each round ADDS features but never REMOVES them. The Phase C bookmarks ADD 7 more items. Is there a mechanism for REMOVING features that don't prove their value? The Nightly Activation, for example, provides zero value for 6 months (Q28 above). Should it be deferred to Phase D?

61-75. [Compound failure scenarios and target validation]

61. SCENARIO: yfinance returns stale VIX (15 min delay) + regime stays TRENDING_UP + S15 fires at 0.6 Kelly + signal passes gauntlet + trade enters + REAL VIX is 38 (RISK_OFF) + market drops 2% underlying = -6% on 3x ETP. Expected loss at 0.6 Kelly sizing with £10K equity: 0.3 × 0.331 × £10K = £993 position. -6% loss = -£59.58. Is -£59.58 within acceptable risk limits? What if the market drops 5% (genuine crash): -£149. What if 10%: -£298 (3% of equity on a RISK_OFF trade). Acceptable?

62. SCENARIO: The system enters a trade at 14:35 UK (just after US open). The trade hits Rung 1 (breakeven) at 14:50. The system moves stop to breakeven. At 14:55, the ETP rebalancing flow starts. Rebalancing is a SELL flow (ETP had a positive day, needs to sell to rebalance). The sell flow pushes the price down. The breakeven stop triggers. The system exits at breakeven. But without the rebalancing flow, the price would have continued up to Rung 2. The plan says "bank at Rung 2 BEFORE 14:55." But the trade only ENTERED at 14:35 — it hasn't had time to reach Rung 2. Should the plan prohibit entries after 14:30 UK to avoid rebalancing interference?

63. SCENARIO: The operator deploys a code update at 10:00 UK (during market hours). The deploy script rebuilds Docker containers (3-5 min downtime). During the downtime, the system holds a position with Chandelier state at Rung 1 (breakeven stop). The price drops through breakeven and hits -1R while the system is down. When the system restarts, it reads the OLD Chandelier state from Redis (Rung 1, stop at breakeven). Current price is below breakeven. The system should EXIT (stop hit) but doesn't because the stop was hit DURING downtime. The position is now at -1R with no stop protection. How does the system RECONCILE state after restart?

64. SCENARIO: A new leveraged ETP launches on LSE (e.g., 3x Google: GOOG3.L). The lse_registry.py auto-scraper detects it. It's added to SECTOR_RADAR tier. It passes Amihud and ASER filters. The Apex Radar detects an RVOL anomaly on GOOGL. It reroutes to GOOG3.L. But GOOG3.L has been trading for only 3 days — there's no historical spread data for the spread gate, no ATR for stop calculation, no ASER for the super-fuel multiplier. How does the system handle BRAND-NEW ETPs with no historical data?

65. SCENARIO: The system is in paper trading mode (Go-Live Gate validation). After 62 of 63 required MTRL days, a genuine SHOCK event occurs (VIX > 45). The Emergency Flatten fires for the first time. It works correctly — the paper trade is closed. But the Go-Live Gate criteria says "zero false flatten events." The flatten was GENUINE (not false). Does it pass? The criteria says "false flatten" not "any flatten." But who determines if a flatten was "false"? Is there an automated classification?

66. SCENARIO: The DynamicSizer computes a position size of £200 (all 8 factors at minimum). The broker has a MINIMUM ORDER SIZE of £100. The trade executes. The commission is £5. The position needs to move +3.5% just to cover commission (£5/£200 + spread £0.80 = £5.80/£200 = 2.9%). The expected winner is +6.17%, but the breakeven AFTER COSTS is +2.9%. With 45% of trades losing -3% and 55% winning +3.27% (6.17% - 2.9% cost), the net edge is razor-thin. At what position size does the system become NET PROFITABLE after all costs?

67. SCENARIO: The plan's base-rate gate (B-11) enters Phase 2 (after N > 100 in top 5 cells). The top 5 cells have N = {120, 105, 98, 87, 60}. Only 2 cells qualify for Phase 2 expansion. The gate adds session_window as a 4th dimension. But only for those 2 cells. The other cells remain at 3 dimensions. The system now has MIXED dimensionality across fingerprints. The code must handle both 3-dim and 4-dim lookups simultaneously. Is this complexity worth the marginal improvement in signal quality?

68. SCENARIO: The correlation brake fires (3+ pairs > 0.70) and limits to 1 position. The system holds QQQ3.L. A Nasdaq-wide rally pushes all correlated ETPs up simultaneously. NVD3.L has a STRONGER signal (higher S15 score). But the brake prevents opening NVD3.L while holding QQQ3.L. The system misses the better opportunity because it's locked into the inferior one. Should the brake allow SWAPPING into a higher-conviction correlated position?

69. SCENARIO: The system has been live for 6 months. The Bayesian Stranger Penalty has graduated 4 of 12 CORE tickers (n > 120 each). The remaining 8 are still at κ = 0.40-0.60 (partially penalised). The GRADUATED tickers happen to be the ones that performed well (survivorship bias in graduation). The PENALISED tickers are the ones that performed poorly (and were therefore sized down — CORRECTLY). But the operator sees: "4 tickers at full size, 8 at reduced size — the system doesn't trust 67% of the universe." Is this a FEATURE (correct risk management) or a BUG (over-conservatism)?

70. SCENARIO: The plan's Inverse Pivot requires the trade to be WITHIN 24 HOURS of the initial spike. But the spike timing is defined by the VIX crossing 28.5. If VIX crosses 28.5 at 14:35 UK (US market open), the 24-hour window closes at 14:35 UK the next day. But the next day, LSE closes at 16:30. The system has only 2 hours (14:35-16:30) on day 2 to find a RETRACEMENT entry. If spreads haven't normalised by 14:35 day 2, the entire 24-hour window passes without entry. What is the expected FILL RATE for the Inverse Pivot given real VIX spike and spread normalisation timelines?

71-75. [Final precision questions targeting daily operational failures]

71. The plan's scan loop runs every 60 seconds. But the ACTUAL scan time varies (data fetch latency, indicator calculation, gate evaluation). If scan T takes 65 seconds, scan T+1 starts 5 seconds late. Scan T+1 then takes 60 seconds, finishing at T+125s instead of T+120s. The drift accumulates. Over 450 scans, if average scan time is 61 seconds (1 second over budget), total drift = 450 seconds = 7.5 minutes. The system's "EOD" scan at 16:20 UK happens at 16:27.5 UK — AFTER the planned EOD exit. Is there a drift-correction mechanism?

72. The plan's 15-minute ATR uses the last 14-15 bars of 1-minute data to calculate the ATR of 15-minute synthetic bars. But yfinance sometimes returns 1-minute candles with ZERO volume (no trades occurred). A zero-volume candle has High = Low = Open = Close, producing ATR contribution = 0. If 3 of the 14 one-minute candles are zero-volume, the ATR is UNDERSTATED by ~21%. The stop loss is set too tight. Does the ATR calculation exclude zero-volume candles?

73. The plan uses `time.monotonic()` for signal timestamps (see PrioritizedSignal dataclass). But `time.monotonic()` is specific to the PROCESS — it doesn't survive container restarts. If the engine restarts, the monotonic clock RESETS. Signal age calculations comparing pre-restart timestamps with post-restart current time produce NONSENSICAL results. Is there a fallback to UTC timestamps for staleness checks?

74. The plan's VIX hysteresis has SHOCK at VIX > 45. But VIX is calculated from SPX OPTIONS with specific expiries. On VIX expiration days (third Wednesday of each month), VIX can SPIKE due to roll mechanics, not actual market stress. A VIX spike to 46 on expiration day would trigger SHOCK and flatten the portfolio. False alarm. Does the plan account for VIX expiration-day mechanics?

75. The plan says the CUSUM alpha reaper uses Page's (1954) original resetting CUSUM. But the plan's formula shows S_t = max(0, S_{t-1} + (outcome - μ₀)). This IS the resetting CUSUM (resets to 0 when S goes negative). But μ₀ (the expected outcome) is not specified. If μ₀ = 0 (break-even), the CUSUM accumulates ALL positive AND negative deviations. But if μ₀ = expected_return (e.g., +1.14%/day), the CUSUM only triggers on UNDERPERFORMANCE. Which μ₀ is used?

WHAT WILL MAKE THIS SUCCEED (Questions 76-100):
76. If you had to bet £10K of YOUR OWN MONEY on this system, what is the SINGLE CHANGE you would make to the plan to maximise your survival probability?

77. The plan has 7 Phase A items. If you could only build 3 of them before going live, which 3 would you build and why?

78. The plan targets 55-60% WR. What EVIDENCE exists that ANY intraday momentum system on leveraged ETPs achieves this? Cite specific backtests or academic studies.

79. The plan uses 3x leveraged ETPs. Would 2x ETPs be more appropriate for a £10K portfolio? Lower leverage = lower variance drag, tighter spreads, but lower return per trade. What is the OPTIMAL leverage for this strategy at this capital level?

80. The plan targets UK ISA wrapper. Are there ISA-compatible brokers that offer ALL of: (a) API access, (b) leveraged ETP trading, (c) sub-£10 commissions, (d) ISA wrapper? If not, the entire strategy is unbuildable.

81. The plan's Phase A takes 39 hours. But the DEVELOPER hasn't been identified. Is this 39 hours for a senior Python developer who knows asyncio, Redis, yfinance, and quantitative finance? Or 39 hours for anyone? The skill requirements include: async Python, queue theory, Bayesian statistics, financial mathematics, Docker orchestration, AWS deployment, and Redis persistence. How many developers worldwide have ALL these skills?

82. The plan accumulates complexity across 10 rounds but has NEVER been validated against real market data. The Monte Carlo simulations use assumed parameters. What would it take to run a MINIMAL BACKTEST of the core S15 strategy on historical 1-minute LSE ETP data? Is this data even available? At what cost?

83. The plan's Chandelier exit has 5 rungs with specific thresholds. But the thresholds were never OPTIMISED on actual data — they were designed from first principles and verified via Monte Carlo with assumed parameters. Is there any risk that the rung thresholds are LOCALLY optimal (for the assumed parameters) but GLOBALLY suboptimal (for real parameters)?

84. The plan specifies 63 MTRL days of paper trading. During this period, the system generates ~63 trades. Is 63 trades sufficient to VALIDATE a system with 54 parameters? The 30:1 rule says no. But the system isn't FITTING parameters — it's VERIFYING a pre-specified architecture. What is the appropriate validation standard for a pre-specified (not fitted) system?

85. The plan says "PREMATURE UPGRADE IS BANNED" — no data feed upgrades until Phase A is complete. But yfinance is a FREE scraper with no SLA. If yfinance breaks (it has before — Yahoo changes API, library maintainer abandons), the ENTIRE system goes dark. Is there a documented EMERGENCY data feed migration plan?

86. The plan has Phase A (39h), Phase B (21h), and Phase C bookmarks. But there is NO Phase D — no plan for what happens AFTER the system is live and profitable. How does the system SCALE from £10K to £100K? From 1 trade/day to 5? From t3.small to dedicated infrastructure? The plan stops at Go-Live.

87. The plan targets "leveraged LSE ETPs" but these products are RETAIL instruments with specific regulatory requirements (KID documents, appropriateness assessments). UK FCA regulations may restrict automated trading of these products for retail investors. Has the REGULATORY landscape been assessed?

88. The plan assumes the market maker's spread is the ONLY cost of trading. But leveraged ETPs also have MANAGEMENT FEES (typically 0.75-1.0% per annum) and SWAP FEES (the cost of daily leverage, approximately 0.5-1.0% per annum on top of the reference rate). These fees are embedded in the ETP price (they manifest as NAV erosion). Over a year of daily trading, how much NAV erosion is accumulated? Is it material?

89. The plan has 55+ academic citations. But academic alpha research has a REPLICATION CRISIS — many published trading strategies don't replicate out of sample (McLean & Pontiff 2016 found 58% decay post-publication). How many of the plan's cited strategies have been INDEPENDENTLY replicated?

90. The plan uses HMM (Hidden Markov Model) for regime classification. HMMs require TRAINING DATA to estimate transition matrices and emission distributions. Where is the HMM trained? On what data? How often is it retrained? If trained on 2020-2024 data, the transition matrix reflects COVID + rate hiking cycle + AI bubble. Will this matrix be valid for 2026-2027?

91-100. [Final "firepower" questions]

91. What is the MINIMUM VIABLE PRODUCT? Strip the plan to the absolute minimum that produces a SAFE (not necessarily profitable) trading system. How many of the 129+ components can be removed without compromising safety?

92. What is the CRITICAL PATH through Phase A? If items A-1, A-2, A-3 are sequential, and A-4, A-5 can be parallelised, and A-6 depends on A-5, what is the SHORTEST calendar time to complete Phase A?

93. Is the 33-gate gauntlet providing MORE safety than it costs in missed opportunities? What is the estimated FALSE VETO RATE (valid signals killed by the gates)?

94. The plan targets £177K Year 1 (Moderate). But with stranger penalty (64% average κ for Year 1), realistic trading day utilisation (80%), and commission drag (2% of gross returns), what is the ACTUAL expected Year 1 outcome?

95. If the system LOSES money in Month 1 (which has ~40% probability given the binomial distribution of wins/losses), what is the RECOVERY PATH? How many consecutive winning months are needed to recover from a -10% Month 1?

96. The plan has been reviewed by 3 AI models. But none of the reviewers have TRADED leveraged ETPs. None have BUILT production trading systems. None have EXPERIENCED a flash crash from inside a live system. What HUMAN expertise is missing from the review process?

97. The plan's Emergency Flatten is the LAST LINE OF DEFENSE. If it fails (broker API timeout, network partition, Docker crash), the portfolio is UNPROTECTED. What is the SECOND-TO-LAST line of defense? The Dead Man's Switch (Lambda). What if THAT fails? Is there a THIRD line?

98. The plan uses PAPER TRADING for validation. But paper trading can't validate: (a) broker API reliability, (b) actual fill quality, (c) slippage beyond spread, (d) partial fill handling, (e) order rejection handling, (f) position reconciliation. What is the plan for validating these LIVE-ONLY concerns? Is there a "small live" period (£100-£500) before full deployment?

99. The plan targets UK ISA with £0 tax. But the ISA provider reports ALL trading activity to HMRC. If HMRC classifies the operator as a PROFESSIONAL TRADER (based on volume, frequency, and sophistication), the ISA may be challenged. HMRC has guidelines on "badges of trade." Does automated daily trading of leveraged ETPs cross the threshold?

100. FINAL QUESTION — THE EXECUTION QUESTION: You have read the entire plan. You understand every formula, every gate, every risk control. Now answer honestly: If you were a HUMAN DEVELOPER sitting down to implement this plan on a real EC2 instance with a real broker API and real money in a real ISA — would you START by building the 7 Phase A items as specified, or would you do something COMPLETELY DIFFERENT? What would your first 8 hours of coding look like? Be specific: file names, functions, test cases.

FINAL DIRECTIVE: After answering all 100 questions, provide:

A) "PRECISION ERROR TABLE" — Every numerical error found, with: plan value, correct value, delta, compound annual impact, severity (P0-P3).

B) "COMMAND TREE FIREPOWER MAP" — Each decision node rated 1-10 for data quality + decision logic + execution capability. Identify the 3 weakest nodes.

C) "ADVERSARY EXPLOITATION PLAYBOOK" — How a sophisticated market maker would systematically extract value from this system. Include: detection method, exploitation mechanism, and AEGIS countermeasure for each.

D) "MINIMUM VIABLE SYSTEM" — The smallest subset of the plan that produces a SAFE trading system. Component count, estimated build time, and what's sacrificed.

E) "WHAT WOULD YOU DO BETTER?" — 8 hours of coding, right now. File names, functions, test cases. No more planning.

F) "MODEL RISK MANAGEMENT REPORT" — Full MRM assessment per Procedure 1. Model inventory with materiality tiers, conceptual soundness verdict, and documentation quality rating for each model.

G) "STRESS TEST RESULTS TABLE" — For each of the 9 scenarios in Procedure 3: SURVIVES/FAILS, peak drawdown %, which controls activated, what was missing, and the fix.

H) "OPERATIONAL RISK REGISTER" — Top 15 risks per Procedure 4, ranked by expected annual loss. Include the 10 KRIs with thresholds and automated responses.

I) "BEST EXECUTION REPORT" — Per Procedure 5. Optimal trading window analysis, venue assessment, and market impact threshold.

J) "PRE-TRADE RISK LIMIT SCHEDULE" — Complete limit table per Procedure 6 with mathematical derivations for every threshold.

K) "COUNTERPARTY & LIQUIDITY RISK MAP" — Per Procedure 7. FSCS coverage adequacy, historical stress liquidity data, and the equity "death zone" threshold.
```

---

## USAGE INSTRUCTIONS

1. Upload `AEGIS_MASTER_PLAN_v13_FINAL.md` (the full document, v13.8) as an attachment/file to both Gemini and ChatGPT
2. Paste the appropriate command above as your message
3. For Gemini: use Gemini 2.5 Pro (the adversarial model, not Flash)
4. For ChatGPT: use GPT-4o or o1-pro for maximum depth
5. Both prompts include 100 adversarial questions PLUS 6-7 institutional hedge fund review procedures (MRM, IVV, Stress Testing, OpRisk, Best Execution, Risk Limits, Counterparty/Liquidity)
6. Expected response length: 25,000-40,000 words each (100 questions + 6-7 formal procedure deliverables)
7. If the AI runs out of output tokens, ask it to continue with "Continue from where you stopped. Complete all remaining deliverables."
8. Bring both responses back to Claude for Round 11 triage (4-persona analysis, accept/reject each proposal, apply surviving amendments as GPT-36+)
8. **Key difference from R10**: R11 requires SPECIFIC NUMBERS in every answer — no "it depends" allowed
