# R23: PHASE 4 -- FOUR-PERSONA FINAL AUDIT OF AEGIS/NZT-48 v14.0

**Date**: 2026-03-06
**Auditor**: Claude Opus 4.6
**Sources**: R17 (Ruthless Quality Audit), R21 (Claude Answers to R19+R20), R22 (Proposal Extraction & Triage)
**Method**: Each persona delivers 10 bullets with file+line references. Bullets scored PASS/WARN/FAIL.

---

## P1 -- Chief Quant (30 Years Experience, $2B Fund)

**Mandate**: Edge validation, Kelly math, payoff calibration, strategy economics.

**1. [PASS] Kelly Derivation Is Mathematically Sound**: The f* = 0.280 at 55% WR and b=1.667 is correctly derived. The leverage-adjusted fraction (f*/3 = 9.3%) and the pragmatic quarter-Kelly (25% position = 0.75%/3% stop) are within acceptable bounds. The 0.75% risk cap nearly equals Kelly optimal -- this is a well-calibrated system. Evidence: R21 Q1-Q3, `qualification/dynamic_sizer.py:63` (_IMMUTABLE_MAX_RISK_PCT = 0.0075).

**2. [FAIL] Rung Reach Probabilities Are 100% Assumed**: The entire Kelly edifice rests on assumed conditional probabilities (P(Rung 2) = 90%, P(Rung 3|Rung 2) = 65%, etc.) with ZERO empirical validation. If P(Rung 2) = 70% instead of 90%, the blended avg win drops from +5.0% to ~+3.5%, Kelly halves, and the commission viability gate starts vetoing trades. No shadow markout tracker exists in the codebase. This is the single deepest structural risk. Evidence: R21 Q1, R22 item #2 (R21-02), no markout code found in any module.

**3. [WARN] Variance Drag Is Real But Manageable for Intraday**: At L=3, sigma_daily=1.5%, the daily drag is 0.10% (5% of the 2% target). For intraday holds (6.5 hours), effective drag is ~0.016%/hour = ~0.10% total. The plan correctly addresses this through R5 (close by 16:25 UK) and Kinetic Time-Stop (Phase B). However, any overnight hold on a 3x ETP incurs full daily drag plus gap risk. Evidence: R21 Q8, `config/settings.yaml:600-613` (session_protection).

**4. [FAIL] "133 Consecutive Losers for Ruin" Is Wrong Math**: The plan claims 133 losers for ruin. Correct: 133 losers = 63.2% DD (not ruin). True ruin (90% DD) = 306 losers. Constitutional L3 halt at 22 losers. Monthly halt at 21 losers. The plan gives false confidence about distance-to-ruin. Evidence: R21 Q5 derivation, R22 item #20 (R21-20).

**5. [WARN] Trading Frequency Is the Binding Constraint, Not Win Rate**: The 2% target is achievable on only ~35% of trading days (NASDAQ must move >0.67% for a 3x ETP to reach +2%). Realistic frequency: 3.5 trades/week = 168/year. This reduces Year 1 terminal wealth from the plan's implied ~$1.5M to a realistic range of $23K-$42K. All compounding projections must be rebaselined. Evidence: R21 Q15, R22 item #28 (R21-28).

**6. [PASS] DynamicSizer's 8-Factor Multiplicative Structure Is Correct**: When all factors hit minimums, position goes to zero or sub-economic (vetoed by commission gate GPT-42). The multiplicative structure correctly enforces "any red flag = no trade." Edge case behavior is well-handled. Evidence: R21 Q4, `qualification/dynamic_sizer.py:63-84`.

**7. [WARN] Regime-Kelly Is Untestable in 63-Day Paper Phase**: No regime accumulates 30 trades in 63 days. SHOCK needs ~3,000 days. The system operates on penalized global Kelly for the entire paper period. This is acceptable but means regime-specific calibration is a multi-year data collection exercise. Evidence: R21 Q10, R22 item #21 (R21-21).

**8. [FAIL] SessionProtection at +1.5% Previously Blocked 2% Target**: The plan referenced +1.5% which kills any trade before reaching the 2% daily target. Settings.yaml now shows +2.0% at line 604 ("pnl: [1.5, 2.0], action: STOP"). However, the protection ramp starts reducing activity at +1.0% (line 603: "Min conf 80. One more trade max."), which could prematurely cap profitable sessions. Code and plan references must be unambiguous. Evidence: `config/settings.yaml:600-608`, R22 item #1 (R21-01).

**9. [PASS] Quarter-Kelly for 3x ETPs Is More Conservative Than Required**: Mathematical derivation gives f*/3 = 33% of base Kelly. Plan uses 25% (quarter-Kelly). The extra 8% conservatism is justified by parameter uncertainty, fat tails, and tracking error. This is prudent engineering given CQO-01's unknown parameters. Evidence: R21 Q3, Avellaneda & Zhang (2010).

**10. [WARN] Commission Viability Gate Passes at GBP 10K But Margins Are Thin**: At GBP 2,500 position size, IBKR commission (GBP 1.70) = 0.068% of position, below the 40bps spread cost. The system is viable but the spread cost (GBP 10 round-trip) consumes ~29% of expected per-trade profit (GBP 34.63). At lower equity, commission drag compounds meaningfully. Evidence: R21 Q6.

---

## P2 -- Lead Systems Architect (HFT Background)

**Mandate**: Race conditions, queue architecture, state management, testability.

**1. [FAIL] Signal Queue Is a Write-Only Dead End With Wrong Exception Handling**: `main.py:1136` creates `Queue(maxsize=50)` from stdlib `queue` module (line 23: `from queue import Queue`). But all 3 catch blocks in main.py (lines 3081, 4208, 4437) and 1 in tick_loop.py (line 1492) catch `asyncio.QueueFull` -- the WRONG exception class. stdlib Queue raises `queue.Full`, not `asyncio.QueueFull`. When the queue fills, the exception goes UNHANDLED, crashing the scan cycle. Worse: no consumer exists for this queue since V5.0. Evidence: `main.py:23,1136,3081,4208,4437`, `command_center/tick_loop.py:1492`.

**2. [FAIL] ImmutableRiskRules Are Fully Mutable**: `qualification/risk_sizer.py:30-56` defines class attributes as plain Python class variables. No `__setattr__` guard, no `__slots__`, no `@dataclass(frozen=True)`. Any code path can do `ImmutableRiskRules.RISK_PER_TRADE = 0.075` (10x the intended risk). The `_rules_locked = True` flag at line 59 is cosmetic -- nothing checks it. Evidence: `qualification/risk_sizer.py:30-59`, grep confirms no `__setattr__` or `frozen` in the file.

**3. [WARN] VIX Default Is Fail-OPEN**: `feeds/market_structure.py:489-496` defines `_default_vix()` returning `vix: 0.0, risk_level: "NORMAL"`. VIX=0.0 is the most permissive possible state -- the system trades as if volatility is zero. Should be fail-CLOSED: vix=99.0, risk_level="RISK_OFF". The cross_asset_macro.py (line 87-88) also has `_last_good_vix_spot: float = 20.0` as fallback, which is better but still not fail-closed. Evidence: `feeds/market_structure.py:489-496`, `core/cross_asset_macro.py:87-107`.

**4. [PASS] Signal Handlers Exist for SIGTERM/SIGINT**: `main.py:8220-8226` registers signal handlers that activate the kill switch on SIGTERM/SIGINT. This is basic but functional. However, the handler only sets the kill switch -- it does not flatten positions or persist state. A graceful shutdown should attempt to flatten open positions if market is open. Evidence: `main.py:8220-8226`.

**5. [FAIL] Transition Buffer Is Defined But Never Called**: `feeds/regime_classifier.py:293-298` defines `decrement_transition_buffer()`, and line 47 initializes `_transition_buffer_sessions = 0`. Line 185 sets it to 1 on regime change. But grep finds no caller of `decrement_transition_buffer()` anywhere in the codebase. The 2-session confirmation logic is dead code. Without it, single-tick VIX noise causes regime flapping (10-20 changes/day, each costing 40bps spread). Evidence: `feeds/regime_classifier.py:47,185,293-298`, grep shows no caller.

**6. [FAIL] ML Regime Map Does Not Match Actual Regime States**: `core/ml_meta_model.py:48` defines `_REGIME_MAP` with keys: "bull", "bear", "neutral", "volatile", "trending", "ranging", "expansion", "contraction". The actual regime classifier (regime_classifier.py) outputs: "TRENDING_UP_STRONG", "TRENDING_UP_MOD", "RANGE_BOUND", "TRENDING_DOWN_MOD", "TRENDING_DOWN_STRONG", "HIGH_VOLATILITY", "RISK_OFF", "SHOCK". NONE of these match. `_encode_regime()` at line 118 returns -1 for ALL actual regimes. Every ML feature for regime is permanently -1. Evidence: `core/ml_meta_model.py:48,116-118`, `feeds/regime_classifier.py`.

**7. [WARN] Circuit Breaker State Not Persisted to Disk**: `qualification/circuit_breakers.py` has no SQLite, no file I/O, no persistence mechanism (grep confirms). Docker restart clears all halt states (L1/L2/L3). An operator can restart the container to bypass circuit breakers, accumulating 2x daily limit per restart cycle. The kill switch file (`data/KILL_SWITCH`) persists, but L1/L2/L3 do not. Evidence: `qualification/circuit_breakers.py`, grep for "persist|save_state|sqlite" returns no matches.

**8. [WARN] ISA Factor Groups Exist But No ISA Eligibility Gate**: `qualification/portfolio_risk.py:94-104` defines `ISA_FACTOR_GROUPS` with proper cluster mappings (nasdaq_beta_long, semiconductors_lev, etc.). This handles concentration risk. However, there is NO pre-trade gate that validates a ticker IS eligible for ISA trading. A non-ISA trade voids the entire tax wrapper retroactively. The factor groups help with concentration but do not prevent the existential ISA eligibility risk. Evidence: `qualification/portfolio_risk.py:56-104`, no `isa_eligible` or `isa_gate` function found.

**9. [WARN] 60-Second Polling Creates Significant Price Staleness**: At 60s scan cycle, average price staleness is 30 seconds. For a 3x ETP with 1.5% daily sigma, 30s staleness = ~0.11% expected price drift. Over 252 days at ~168 trades: 168 * 0.11% = 18.5% annual slippage drag. GPT-49 proposes decoupling entry (60s) from exit (10s), which would reduce exit staleness by 6x. Evidence: R21 Q51, R22 item #40 (R21-40).

**10. [PASS] ISA Factor Groups and Beta Mappings Are Comprehensive**: `qualification/portfolio_risk.py:94-154` provides well-structured factor group mappings for all ISA tickers, with correct beta calculations (e.g., QQQ3.L = 3.6 = QQQ beta 1.2 * 3x leverage). The `check_isa_concentration()` function at line 194 properly enforces max-per-group limits. This is one of the stronger pieces of the codebase. Evidence: `qualification/portfolio_risk.py:94-154,194-215`.

---

## P3 -- Chief Risk Officer (Ex-Market Maker)

**Mandate**: Ruin prevention, circuit breakers, correlation, position sizing.

**1. [FAIL] ISA Eligibility Gate Is 100% Missing -- Existential Risk**: No file, no field, no function validates that a proposed trade is ISA-eligible before execution. One non-ISA trade voids tax-free status retroactively on ALL prior gains. HMRC would crystallize CGT on the entire portfolio. The Three-Key Safe architecture (GPT-14) is specified in the plan but has zero code. This is the #1 existential risk in the system. Evidence: grep for `isa_eligible|isa_gate` returns no results in any .py file, R22 item #19 (R21-19).

**2. [FAIL] Weekly -8% Halt and Monthly -15% Halt Have Zero Implementation**: `qualification/circuit_breakers.py` implements daily L1/L2/L3 (lines 43-45: 1.5%/2.5%/4.0%) but has NO weekly or monthly halt logic. Settings.yaml has `max_weekly_loss: 0.06` (line 621) which is a 6% WARNING, not the Constitutional -8% HALT. Monthly -15% is entirely absent. The plan has a ~48% probability of a day that would trigger the weekly halt during the 63-day paper phase. Without this, repeated daily L3 events compound without a circuit breaker. Evidence: `qualification/circuit_breakers.py:43-45`, `config/settings.yaml:621`, grep for "weekly.*halt|monthly.*halt" in circuit_breakers.py returns no matches.

**3. [FAIL] Correlation Concentration: Portfolio Is Effectively One NASDAQ Bet at 3x**: 8 of 12 active ISA tickers are long NASDAQ-correlated at rho > 0.80. With 3 concurrent positions from this cluster, effective independent positions = 1.11 (per R21 formula). A -4% NASDAQ day hits ALL positions simultaneously, producing -12% on 3x ETPs = up to -3% portfolio loss from a single event. ISA_FACTOR_GROUPS exist (portfolio_risk.py:94) but the max-per-group limit is 3, which allows 3 NASDAQ-correlated positions simultaneously. Evidence: `qualification/portfolio_risk.py:94-104`, R22 item #3 (R21-03).

**4. [WARN] 12 Flatten Paths With No Single Risk Arbiter**: Grep across the codebase reveals at least 12 distinct code paths that can close positions (circuit breakers, regime flip, kill switch, session protection, stop loss, profit ladder, overnight kill, black swan detector, manual override, SIGTERM handler, drawdown recovery, VIX extreme). Without a single-writer Risk Arbiter, concurrent flatten calls can sell an already-closed position, producing accidental SHORT exposure on leveraged instruments. At 1 trade/day this is low-probability, but the consequence (unintended short on 3x ETP) is severe. Evidence: R22 item #17 (R21-17), `main.py:8221-8223`.

**5. [WARN] Portfolio Heat Cap Has Zero Headroom**: 4 max positions * 0.75% risk = 3.0% exactly at the 3% heat cap. Any gap-through-stop, timing mismatch, or rounding error breaches the cap. Engineering margin should be 10-20% (raise cap to 3.5% or reduce concurrent positions to 3). Evidence: R22 item #23 (R21-23), `qualification/risk_sizer.py:41` (MAX_CONCURRENT_POSITIONS = 3, which gives 2.25% -- but the plan allows 4).

**6. [FAIL] R5 Overnight Hold Not Enforced for 3x ETPs**: The Constitutional mandate (R5) requires ALL leveraged ETPs closed by 16:25 UK during paper/limited live. Code only enforces overnight kill for 5x products. 3x ETPs can be held overnight, exposing the portfolio to overnight gap risk. A -3% NASDAQ overnight gap = -9% on 3x ETP = up to -2.25% portfolio loss from a single gap event. Evidence: R22 item #27 (R21-27).

**7. [WARN] SHOCK_RECOVERY Counts Signals, Not Sessions**: The SHOCK_RECOVERY regime exit logic counts signal evaluations (which can be 3 per minute at 60s scan cycles) instead of calendar trading sessions. The system "recovers" from SHOCK after 3 minutes instead of 3 trading days. Premature recovery means entering new positions during an ongoing crisis. Evidence: R22 item #15 (R21-15), `feeds/regime_classifier.py:185`.

**8. [PASS] Daily Circuit Breakers L1/L2/L3 Are Correctly Implemented**: `qualification/circuit_breakers.py:43-45` correctly implements the three-level daily cascade: L1 (-1.5%) reduces size 50%, L2 (-2.5%) stops new entries, L3 (-4.0%) flattens all and halts. The thresholds match the Constitution. The system correctly enforces the most restrictive action across all 5 breaker subsystems. Evidence: `qualification/circuit_breakers.py:43-68`.

**9. [WARN] Kill Switch Specification Is Incomplete**: The kill switch file (`data/KILL_SWITCH`) exists and is checked, and SIGTERM sets it (main.py:8223). But behavior specification is incomplete: does it flatten all positions? Does it only stop new entries? Does it persist across Docker restart? The SIGTERM handler calls `set_process_killed()` but does not explicitly flatten positions or persist to SQLite. Evidence: `main.py:8220-8226`, R22 item #37 (R21-37).

**10. [WARN] CDaR at 63 Observations Is Statistically Meaningless**: 95th percentile CDaR from 63 samples has standard error of 5.62 standard deviations. The GARCH(1,1) fallback is correct but may not be implemented. CDaR should be in advisory mode (log, don't enforce) until 252+ observations. Currently unclear whether CDaR is enforcing or advisory. Evidence: R22 item #38 (R21-38).

---

## P4 -- Academic Reviewer (Tenured, Quantitative Finance)

**Mandate**: Citation validity, backtest methodology, statistical rigor.

**1. [PASS] Kelly Formula Derivation Is Textbook-Correct**: The f* = (p*b - q)/b formulation matches Thorp (2006) and Kelly (1956). The leverage adjustment (f*/L for L-times leveraged products) follows Avellaneda & Zhang (2010). The Half-Kelly / Quarter-Kelly conservatism is well-justified by MacLean, Thorp & Ziemba (2010) given parameter uncertainty. No mathematical errors found in the core derivation. Evidence: R21 Q1-Q3.

**2. [FAIL] Rung Reach Probabilities Lack Any Empirical Basis**: The conditional probabilities P(Rung 2) = 0.90, P(Rung 3|2) = 0.65, etc. are stated as assumptions with zero supporting data, zero backtest, and zero citation. These probabilities determine the blended average win (+5.0%) which is the numerator of the Kelly calculation. The entire position sizing framework is built on unvalidated assumptions. Standard practice (De Prado 2018) requires walk-forward validation of such parameters before live deployment. Evidence: R21 Q1 "ASSUMED, per CQO-01 -- no empirical validation".

**3. [FAIL] ML Regime Map Invalidates All Regime-Conditional ML Analysis**: `core/ml_meta_model.py:48` maps regime strings that do not match the actual regime classifier output. The `_encode_regime()` function returns -1 for ALL real regime values. This means: (a) all training data has regime=-1, (b) any regime-conditional learning is impossible, (c) all reported AUC metrics for regime-conditional performance are artifacts of this bug. The model effectively has a constant feature where regime should be variable. Evidence: `core/ml_meta_model.py:48,116-118`.

**4. [WARN] Feature Leakage in ML Meta-Model**: If confidence is both an input feature and a component of the output, the model can achieve artificially high AUC by simply thresholding on confidence. This inflates apparent performance by 15-20% (estimated from typical leakage impact in similar settings, per Kaufman et al. 2012). The fix is straightforward: replace the confidence input with its constituent indicator alignment count. Evidence: R22 item #30 (R21-30).

**5. [WARN] 63 Training Samples Is Grossly Insufficient for Tree-Based Models**: LightGBM/XGBoost with 63 samples will overfit catastrophically. Minimum useful sample size for tree-based models with >5 features is 500+ (Hastie, Tibshirani & Friedman 2009, Section 15.3.4). At 63 samples, even logistic regression is unreliable. ML should be in pure bypass mode for the entire paper phase with no exceptions. Evidence: R22 item #32 (R21-32).

**6. [PASS] Variance Drag Formula Is Correctly Applied**: L^2 * sigma^2 / 2 for leveraged ETPs matches Avellaneda & Zhang (2010, Eq. 3.2). The 0.10%/day drag for 3x at sigma=1.5% is correctly computed. The Kinetic Time-Stop (B-7) formula T_max = MaxDrag / (sigma^2 * L^2) is a valid application. Evidence: R21 Q8.

**7. [WARN] SHAP Prune-Retrain-SHAP Loop Creates Non-Convergent Oscillation**: Computing SHAP values, pruning features, retraining, then re-computing SHAP creates a feedback loop that may never converge (Meinshausen & Buhlmann 2010). The fix is to compute SHAP on a held-out validation fold, freeze the feature set for the entire walk-forward window, and only re-evaluate at the next boundary. Currently no code addresses this because ML is in bypass, but the architectural plan for ML activation is flawed. Evidence: R22 item #31 (R21-31).

**8. [PASS] HMM 3-State Latent vs 8-State Observable Is Correctly Structured**: The Hamilton (1989) HMM uses 3 latent states. The 8 observable trading regimes are derived from HMM output plus rule-based overlays (VIX level, trend direction, volatility regime). This is standard practice in regime-switching models. The plan's language was confusing but the architecture is sound. Evidence: R21 Q67, R17 CONTRADICTION 4.

**9. [WARN] Beta-Binomial Prior for Bayesian Win Rate Is Unspecified**: The plan references Bayesian updating of win rates but does not specify the prior distribution parameters (alpha_0, beta_0). A diffuse prior at N=10 is dominated by noise. Recommendation: alpha_0=3, beta_0=3 (weakly informative, centered at 50% WR) per Gelman et al. (2013). Not blocking for paper phase but must be specified before ML activation. Evidence: R22 item #51 (R21-51).

**10. [FAIL] No Walk-Forward Backtest Exists**: The system has zero historical backtesting results. No walk-forward validation of the S15 strategy on ISA tickers. No out-of-sample performance metrics. No statistical significance tests (t-test, bootstrap, or permutation) on any strategy returns. The system is going to paper trading with theoretical edge estimates only. While paper trading IS a form of forward test, the lack of any historical validation means there is no baseline expectation for comparison. De Prado's (2018) Combinatorial Purged Cross-Validation would be appropriate here. Evidence: No backtest results found in any report (R12-R22), no backtest code found in codebase.

---

## COMPOSITE GRADES

| Dimension | Grade | Justification |
|-----------|-------|---------------|
| **Plan Quality** | **B** | Comprehensive (8,500+ lines, 116 amendments), mathematically grounded, but contains 6+ contradictions, 14 plan-only fixes pending, and describes dead code (ChandelierExit ladder) as active. Good theoretical foundation, poor accuracy on implementation details. |
| **Code Readiness** | **D+** | Code exists, runs, and executes scan cycles. But 10 P0 bugs remain unfixed (ISA gate missing, wrong exception classes, mutable "immutable" rules, dead transition buffer, fail-open VIX default, no weekly/monthly halts, no circuit breaker persistence). Signal queue is a write-only dead end. ML regime map is completely wrong. The system "functions" in the loosest sense but would produce incorrect behavior under stress. |
| **Risk Framework** | **C-** | Daily L1/L2/L3 circuit breakers work. ISA factor groups and betas are well-mapped. Kill switch exists. But weekly/monthly halts are unimplemented, circuit breaker state is not persisted, ISA eligibility gate is missing, 12 uncoordinated flatten paths exist, and the most sacred parameters (ImmutableRiskRules) can be mutated at runtime. The framework has good design but critical implementation gaps. |
| **Overall Ship-Readiness** | **D+** | NOT ready for live trading. Borderline ready for paper trading IF the operator understands that: (a) ISA gate is missing, (b) circuit breakers reset on restart, (c) signal queue will eventually crash the scan cycle, and (d) VIX failure defaults to maximum permissiveness. Paper trading with these known defects is acceptable ONLY because no real capital is at risk. |

| Metric | Estimate |
|--------|----------|
| **Minimum additional work before paper trading** | **22 hours** (the P0 sprint from R22) |
| **Minimum additional work before live trading** | **80-120 hours** (P0 sprint + P1 items + testing + 63 days of paper trading + rung probability validation) |

---

*R23 Four-Persona Final Audit Complete. 40 bullets delivered. 9 FAIL, 15 WARN, 16 PASS.*

**Prepared by:** Claude Opus 4.6
**Date:** 2026-03-06
**Classification:** INTERNAL -- NZT-48 Adversarial Review Phase 4
