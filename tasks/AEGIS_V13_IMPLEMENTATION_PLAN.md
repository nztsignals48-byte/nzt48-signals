# AEGIS v13 → CODE: Implementation Plan v3.1 (COMPLETE)

**Date:** 2026-03-08 (v3.1 — v3.0 + Gemini Institutional Syndicate 4-persona review integrated)
**Baseline:** Current codebase (Sprints 0-4 complete, 52 paper trades, 0% verified WR)
**Source:** AEGIS_MASTER_PLAN_v13_FINAL.pdf (153 pages) + v15/v16.2 merged plan
**Goal:** Validate whether intraday momentum on LSE leveraged ETPs has a tradeable edge, then build around it

---

## ADVERSARIAL REVIEW SUMMARY

This plan was stress-tested by 5 independent adversarial personas:

| Persona | Critical | High | Medium | Low |
|---------|----------|------|--------|-----|
| Risk Manager | 6 | 7 | 7 | 4 |
| Quant Researcher | 4 | 5 | 6 | 0 |
| Systems Architect | 2 | 5 | 7 | 1 |
| Devil's Advocate | 3 fatal | 7 serious | 2 minor | 0 |
| Gemini 4-Persona v1 | 3 | 5 | 4 | 3 |
| Gemini Syndicate v2 | 2 | 2 | 3 | 0 |
| Gemini Final v3 | 2 | 1 | 1 | 0 |
| **Gemini Institutional Syndicate v4** | **4** | **6** | **10** | **0** |

**Key findings v3.0 → v3.1 (Gemini Institutional Syndicate v4 — 40 findings, 4 personas):**
21. **Lunch RVOL must be time-of-day normalized** — 1.3 threshold on all-hours denominator is mathematically improbable during structural trough. CQ-01 ACCEPTED.
22. **Portfolio CVaR 20-day sample = 1 data point at 95th percentile** — use Parametric CVaR (Cornish-Fisher) instead of historical simulation. CQ-02 ACCEPTED.
23. **Amihud denominator must be GBP Volume, not raw shares** — price differences across ETPs break raw share metric. CQ-03 ACCEPTED.
24. **ASER units mismatch (ADR in £, spread in bps)** — standardize both to percentage. CQ-04 ACCEPTED.
25. **Signal queue MUST use async consumer, not inline** — inline processing blocks 60s scan loop. SA-02 ACCEPTED.
26. **ib_insync requires single event loop** — dual loop causes deadlocks. SA-03 ACCEPTED (K-19 rewritten).
27. **IMAGE_PARITY via Docker ARG, not git inside container** — avoids .git bloat. SA-05 ACCEPTED.
28. **Inverse ETP dict in pure-data config module** — avoids circular imports. SA-06 ACCEPTED.
29. **Redis LRU evicts critical state** — separate Redis DBs for state vs telemetry. SA-07 ACCEPTED.
30. **Startup gate must skip feed freshness outside market hours** — avoids 03:00 deadlock. SA-10 ACCEPTED.
31. **Session-end: aggressive LIMIT then MOC auction, not raw MARKET** — MM pull liquidity pre-auction. CRO-01 ACCEPTED.
32. **Cluster correlation must cap notional risk, not just position count** — 2 NASDAQ positions = 2× directional exposure. CRO-04 ACCEPTED.
33. **Regime flapping: REDUCE 50%, don't hold blindly on 3x leverage** — extreme uncertainty. CRO-06 ACCEPTED.
34. **Kill switch: require human confirmation, never auto-clear P0 safety** — CRO-08 ACCEPTED.
35. **Inverse ETP hedge during spread blowout pays 100bps twice** — hold cash instead. CRO-09 ACCEPTED (K-08 rewritten).
36. **Drought State Machine KILLED** — decaying quality = forced trading, violates edge premise. AR-01 ACCEPTED.
37. **MFE shadow markout is look-ahead bias** — calibrate rungs to ex-ante ATR only. AR-02 ACCEPTED (F-16 rewritten).
38. **MicrostructureCalibrator: use Implementation Shortfall, not IC** — IC breaks on HF data. AR-04 ACCEPTED.
39. **24/7 scanning must halt indicators, not just skip scan** — stale bars crush morning volatility metrics. AR-05 ACCEPTED.
40. **SQLite in-memory queue: process crash = data loss** — use Redis LPUSH/BRPOP as durable broker. SA-01 ACCEPTED (H-09 rewritten).

**Rejected/modified findings (with rationale):**
- CQ-06 (post-recovery ramp): PARTIALLY ACCEPTED — reduced timer to 15 min (was 30) but kept ramp (whipsaw evidence outweighs alpha opportunity at 3x leverage).
- CQ-07 (SFV currency EMA): NOTED — Phase Q2+ item, will apply 1-min EMA when built.
- CQ-08 (RLS for OFI): NOTED — Phase Q3-Q4, will use RLS when built.
- CQ-10 (DSR higher moments): NOTED — practical impact minimal with t-stat >= 3.0 requirement.
- SA-08 (ProcessPool IPC): NOTED — will pass lightweight dicts, not DataFrames.
- SA-09 (PyO3 GIL release): NOTED — Phase Q3-Q4, will use allow_threads.
- CQ-05 (nightly → monthly genetic opt): PARTIALLY ACCEPTED — changed to weekly with 10% drift cap.
- CQ-09 (spread VIX function): ACCEPTED — added VIX multiplier to spread normalization.
- SA-04 (IB Gateway Docker restart): ACCEPTED — added container restart after 3 failed reconnects.
- CRO-02 (overnight kill P0 alert): ACCEPTED — added SNS/Twilio fallback at 16:31.
- CRO-03 (arbiter SPOF): ACCEPTED — EMERGENCY_FLATTEN bypasses queue, executes synchronously.
- CRO-05 (£10k halt -12% → -8%): ACCEPTED — tightened.
- CRO-07 (bracket order drift): ACCEPTED — software updates broker stop on rung advance.
- CRO-10 (cancel resting on reconnect): ACCEPTED — first action = cancel all resting.
- AR-03 (fractional diff rolling d): NOTED — Phase Q3-Q4, will use rolling recalibration.
- AR-06 (GARCH vs clock-based widening): PARTIALLY ACCEPTED — keep clock rules as fallback, add fast EWMA vol component.
- AR-07 (financing drag overnight only): ACCEPTED — only apply if expected hold crosses overnight.
- AR-08 (gamma/strike: no -10, need data): ACCEPTED — changed to shadow-mode data collection.
- AR-09 (spoof detection L3 needed): NOTED — Phase Q2+, acknowledged L2 limitations.
- AR-10 (Hawkes needs ITCH): NOTED — Phase Q3-Q4, acknowledged data requirements.

**Key consensus findings from Synthesis:**
- **DSR graduation metric debate**: CQO wants non-normal adjustments, AR notes Sharpe fails on skewed distributions. RESOLUTION: Keep DSR t-stat >= 3.0 as primary gate (robust regardless of distribution). Add Probabilistic Sortino Ratio as secondary confirmation. Both must pass.
- **MOC order protocol**: SA notes IBKR syntax, CRO notes auction mechanics. RESOLUTION: Aggressive LIMIT pegged to Ask/Bid at 16:25, explicit MOC auction order at 16:28 if unfilled.

**Key findings v2.1 → v2.2 (Gemini v3 Final):**
17. **EMA50 slope polluted by overnight gap** — 45/50 bars from yesterday on 1-min chart. Replaced with Opening Drift (price > session open print).
18. **Schedule Paradox 2.0** — E-02 (Redis State Journal) still in Phase E but Validation Gate runs Week 3. Moved to Phase A.
19. **Signal copy must cover nested lists** — `copy.copy()` + explicit list copy for `patterns_detected` and `qualification_log`.
20. **Validation Gate minimum raised to 33%** — 25% WR has negative EV at 1.5:1 R:R. Breakeven = 33%.

**Key consensus findings that changed the plan (v2.0 → v2.1):**
10. **FAST tier multicollinearity** — MACD, RSI, ROC all derive from closing price. Replace ROC with spread_ratio for orthogonal signal.
11. **Schedule Paradox** — 200-Trade Gate can't run before TradeAttribution (E-04) and ScopedQuery (E-03). Moved to Phase 0.
12. **D-05 Rebalancing window is WRONG** — LSE ETP rebalancing happens ~19:00 UK, not 15:00-15:30. Blocking 15:00-15:30 kills prime US momentum.
13. **US Open stabilization restored** — F-10 (300s wait at 14:30 UK) was wrongly killed in v2.0. Restored as B-12.
14. **Anti-cascade consolidated** — A-09/A-10 merged into single portfolio-level breaker (correlated universe makes per-ticker redundant).
15. **Signal copy protection** — Shallow `list(signals)` doesn't protect against object mutation. Need `copy.copy(sig)` in qualification.
16. **Redis write locking required** — concurrent scans need `asyncio.Lock` or `SETNX` around state persistence.

**Previous v2.0 findings (1-9):**
1. **The "0% WR" may be a data pipeline bug** — 98.5% of outcomes.jsonl has NULL P&L. Must verify before assuming strategy failure.
2. **ML model is completely broken** — regime always = -1, confidence is leaked, 43.7% of training data is fabricated backfill. DISABLE ML until 200+ real trades.
3. **Indicators computed on ETP price, not underlying** — RSI/EMA/MACD on 3x prices are compressed by vol drag. Move C-13 to Phase 0.
4. **Safety systems must be fixed BEFORE opening signal generation** — v1 plan opened throttles first (Phase A), then fixed safety (Phase B). REVERSED in v2.
5. **9 items KILLED** — CDaR, iCVaR, PEAD, Chain Reaction, Crash Monetisation, CUSUM, Multi-TF, Adaptive Kelly, Sector Rotation. All add complexity without proven value at this stage.
6. **100-trade validation gate is statistically weak** — raised to 200-trade gate with tighter criteria.
7. **Event-driven scanning (A-08) DEFERRED** — GIL contention + scan overlap risk. Too dangerous for Phase A.
8. **B-05 (SHORT P&L sign) REMOVED** — verified correct in code. Gemini false positive.
9. **RVOL already at 0.60 in code** — plan's proposed 0.30 was already rejected by developers ("suicidal on LSE ETPs").

---

## EXECUTIVE SUMMARY v2

**Restructured to 5 phases + 1 validation phase. 56 items. ~100h estimated (×2 realistic = ~200h).**

The fundamental question: **Does momentum in LSE leveraged ETPs at the intraday horizon exist as a tradeable anomaly, net of costs?**

**Phase structure (REORDERED — safety FIRST, then throttle-opening):**
- **Phase 0 (DATA INTEGRITY + TELEMETRY):** 10 items, ~14h. Verify data. Fix indicators. Build telemetry FIRST.
- **Phase A (SAFETY SYSTEMS):** 14 items, ~22h. Fix ALL safety gates + state journal before opening throttles.
- **Phase B (SILENT KILLERS + TIMING):** 12 items, ~16h. Remove blockages that prevent signal execution.
- **Phase C (CORE EDGE):** 12 items, ~26h. Features that directly improve win rate.
- **Phase D (ETP INTELLIGENCE):** 8 items, ~16h. Leveraged ETP-specific rules.
- **Phase E (ROBUSTNESS):** 3 items, ~5h. Blood Oath #1 + hardening.
- **VALIDATION GATE:** 200 trades, WR >= 40%, PF > 1.2. Then decide on Phase F.

**KILLED from v1 (saves ~24h + massive complexity reduction):**
- C-07: CDaR Circuit Breaker — institutional risk metric, irrelevant at £10K
- C-08: iCVaR Portfolio Veto — portfolio-level risk for a max-3-position system
- D-06: PEAD Power-Law Decay — multi-week anomaly misapplied to day trades, coefficient 6x too high
- D-07: Chain Reaction Confidence Boost — requires reliable P&L data that doesn't exist
- D-12: Inverse Pivot Crash Monetisation — zero crash trades to calibrate, AEGIS v15 already defers
- E-10: CUSUM Feature Drift Detection — over-engineering for pre-validation stage
- F-01: Multi-Timeframe Confirmation — only after system proves it wins on one TF
- F-02: Adaptive Kelly Warmup — need 200+ trades before Kelly matters
- F-09: Sector Rotation Integration — one-bet portfolio makes rotation meaningless

---

## PHASE 0 — DATA INTEGRITY + TELEMETRY (10 items, ~14h)

**Nothing else is trustworthy until the data pipeline is verified AND telemetry is in place.** The Devil's Advocate found that 98.5% of outcomes.jsonl has NULL P&L. The Quant found 43.7% is fabricated backfill (SIG-BF prefix). The SRA identified that the 200-Trade Validation Gate CANNOT run without TradeAttribution and ScopedQuery — these must be built BEFORE any validation, not after.

### 0-01: Verify Outcomes Data Pipeline (2h)
**File:** `data/outcomes.jsonl` + `execution/virtual_trader.py` + `main.py`
**Problem:** 2,327 records in outcomes.jsonl but 98.5% have NULL P&L with no `resolution_method`. The "0% WR" claim may be a data pipeline bug (signals labeled but never executed), not a strategy bug.
**Fix:** Trace the full pipeline: signal → qualification → execution → close → outcome write. Determine whether S15's "0% WR" means (a) trades executed and lost, or (b) trades never executed. This changes everything.
**Review Source:** Devil's Advocate Finding #1

### 0-02: Purge Fabricated Training Data (1h)
**File:** `data/outcomes.jsonl`
**Problem:** 1,016 records (43.7%) have SIG-BF (backfill) prefix — synthetically generated, not from actual trades. 1,276 records are from US tickers (IONQ, WOLF, AMC) with different microstructure than ISA ETPs. Feature population is catastrophically sparse (ADX, RSI, VIX all zero).
**Fix:** Delete ALL SIG-BF records. Delete ALL non-ISA-universe records. Archive original to `data/outcomes_raw_backup.jsonl`. The ML model cannot train on fabricated distributions.
**Review Source:** Quant Finding #1

### 0-03: Compute Indicators on Underlying, Not ETP (3h)
**File:** `feeds/indicators.py` + `strategies/daily_target.py`
**Problem:** RSI, EMA, MACD, ADX computed on 3x/5x leveraged ETP price. Vol drag causes RSI compression, false downtrends in EMA stack, and OBV reflects MM hedging not informed flow. All oscillator thresholds are miscalibrated.
**Fix:** Compute RSI, EMA stack, MACD, ADX on underlying (QQQ for QQQ3.L, NVDA for NVD3.L via UNDERLYING_MAP from isa_universe.py). Keep VWAP and spread on ETP itself (reflects actual execution quality). Re-calibrate ALL thresholds after switch.
**Review Source:** Quant Finding #6 (moved from v1's C-13)

### 0-04: Signal List Mutation + Copy Protection Fix (0.5h)
**File:** `main.py:1929, 2067-2102, 2706-2860`
**Problem:** (a) List mutated during iteration — skips ~50% of signals. (b) Signal objects are mutated in-place (confidence, shares, risk_dollars modified at lines 2706-2860) while referenced by multiple lists simultaneously. Signal dataclass contains nested mutable objects: `patterns_detected: list[str]` and `qualification_log: list[str]`.
**Fix:** (a) Iterate over `list(signals)` copy for list-level safety. (b) Use `copy.copy(sig)` + explicitly copy nested lists: `sig.patterns_detected = list(sig.patterns_detected)` and `sig.qualification_log = list(sig.qualification_log)`. This is faster than `copy.deepcopy()` while covering the actual mutation vectors. (`ConfidenceBreakdown` is a nested dataclass with only float fields — immutable, no copy needed.)
**Review Source:** Systems Architect Finding #6, Risk Manager MED-06, Gemini v2 PSE, Gemini v3 PSE (nested list protection)

### 0-05: Disable ML Meta-Model Until 200+ Real Trades (0.5h)
**File:** `core/ml_meta_model.py` + `main.py`
**Problem:** ML regime encoding broken (always -1), confidence is leaked as input feature, 43.7% training data is fabricated. Model is actively harmful — producing confident predictions from garbage.
**Fix:** Set `_ML_ENABLED = False` in meta_model. Skip ML gate in qualification pipeline. Re-enable only after: (a) 200+ genuine trades with populated features, (b) regime map fixed, (c) feature leakage removed, (d) walk-forward validation passes.
**Review Source:** Quant Findings #1, #3; Devil's Advocate Finding #2

### 0-06: Signal Decomposition — Per-Indicator Logging (1h)
**File:** `learning/signal_logger.py`
**Problem:** Cannot determine which indicators contribute alpha without per-indicator breakdown.
**Fix:** Log {vwap_score, rvol_score, rsi_score, adx_score, ema_score, macro_score, tail_score, spread_score} per signal. This enables ablation study to determine which indicators matter BEFORE adjusting thresholds.
**Review Source:** Quant Finding #5 (moved from v1's C-10 — must come before threshold changes)

### 0-07: QueueFull Exception Mismatch (0.25h)
**File:** `main.py:3081,4208,4437`
**Problem:** Catches `asyncio.QueueFull` but uses stdlib `queue.Queue`.
**Fix:** `except queue.Full`.

### 0-08: Exception Logging Severity Upgrade (1h)
**File:** `main.py` (throughout)
**Problem:** Safety-critical exceptions logged at DEBUG (invisible at default INFO). StateManager kill switch, Ulysses Lock, Redis health, Portfolio Heat all swallowed silently.
**Fix:** Upgrade all safety-subsystem exception handlers from `logger.debug()` to `logger.warning()` or `logger.error()`. Add degradation counter: 3 consecutive failures in any safety subsystem → CRITICAL + halt trading.
**Review Source:** Systems Architect Finding #11

### 0-09: ScopedQuery Builder — Blood Oath #3 (2h)
**File:** NEW: `core/scoped_query.py` + `database.py`
**Problem:** Unscoped DB queries match 5-year-old data → permanent deadlock (SK-02 prevention). Must be in place BEFORE 200-Trade Gate so validation data is correctly scoped.
**Fix:** Automated `WHERE date >= session_start`. All DB queries go through ScopedQuery.
**Review Source:** SRA Schedule Paradox (moved from Phase E to Phase 0)

### 0-10: TradeAttribution Record — Blood Oath #4 (2h)
**File:** Enhancement to `learning/trade_autopsy.py`
**Problem:** Cannot judge the 200-Trade Gate without knowing WHY trades won or lost. MFE/MAE, exit ablation, and shadow markout are prerequisites for validation, not post-validation luxuries.
**Fix:** Structured record: MFE/MAE, exit-also-true ablation, shadow markout verdicts. Schema-compatible with existing outcomes.jsonl.
**Review Source:** SRA Schedule Paradox (moved from Phase E to Phase 0)

---

## PHASE A — SAFETY SYSTEMS (14 items, ~22h)

**Fix ALL safety gates BEFORE opening signal generation.** v1's ordering was wrong: it opened throttles (blackout removal, threshold lowering) before fixing the safety net. Reversed in v2.

### A-01: ISA Eligibility Pre-Trade Gate (2h)
**File:** NEW: `uk_isa/isa_eligibility.py` + integration in main.py qualification
**Problem:** No ISA eligibility check. One non-ISA trade voids the entire tax wrapper (HMRC).
**Fix:** Validate every ticker against ISA universe whitelist before entry. Hard veto — no override.
**Review Source:** Risk Manager HIGH-07, Gemini (moved from v1's B-16 to Phase A Day 1)

### A-02: RISK_OFF Multiplier = 0.0 for LONG (0.5h)
**File:** `qualification/dynamic_sizer.py:73-74`, `feeds/regime_classifier.py:246`
**Problem:** RISK_OFF = 0.25, spec says 0.0. System trades at 25% size in RISK_OFF regime.
**Fix:** RISK_OFF = 0.0 for LONG positions. RISK_OFF = 0.50 for INVERSE positions (per D-12 if implemented later). Guard divide-by-zero downstream.
**Review Source:** Risk Manager CRIT-03, Quant Finding #15

### A-03: Paper Mode Risk Parity (0.5h)
**File:** `qualification/circuit_breakers.py:653-676`
**Problem:** Paper mode disables consecutive loss session halt (Tier 3: 7+ losses only gets 50% reduction). Paper results are meaningless for live validation if risk rules differ.
**Fix:** Remove paper mode relaxation entirely. Paper = live risk rules.
**Review Source:** Risk Manager CRIT-04

### A-04: ImmutableRiskRules — Actually Make Immutable (0.5h)
**File:** `qualification/risk_sizer.py:30-59`
**Problem:** `_rules_locked = True` flag checked by nothing. Any code can silently mutate.
**Fix:** `@dataclass(frozen=True)` or `__setattr__` guard that raises `AttributeError` post-init.
**Review Source:** Risk Manager CRIT-05

### A-05: VIX/Regime Fail-Closed (0.5h)
**File:** `feeds/market_structure.py:489-496`
**Problem:** VIX fetch failure = fail-open. System trades freely when VIX data unavailable.
**Fix:** Default to VIX=99 (SHOCK regime) on fetch failure. 30-minute fix, massive risk reduction.
**Review Source:** Risk Manager HIGH-05

### A-06: Circuit Breaker State Persistence to Redis (4h)
**File:** `qualification/circuit_breakers.py`
**Problem:** All CB state RAM-only. Docker restart bypasses ALL halts. IBC daily restart at 04:45 UK clears state. Currently ZERO Redis operations and ZERO locking in circuit_breakers.py (verified).
**Fix:** Persist to Redis: halt status, consecutive losses, cooldowns, daily results. Use session-scoped TTL (not fixed 24h — must span weekends: `max(86400, seconds_until_next_market_open + 7200)`). Hydrate on startup. Use `appendfsync always` for trading state keys. **CRITICAL: Wrap all `_persist_state()` calls in `asyncio.Lock` or Redis `SETNX` to prevent write-clobbering from concurrent scan threads.**
**Review Source:** Risk Manager CRIT-02, Systems Architect Finding #3, Gemini v2 SRA (Redis locking)

### A-07: Weekly/Monthly Circuit Breakers (2h)
**File:** `qualification/circuit_breakers.py`
**Problem:** Only daily L1/L2/L3. No weekly (-8%) or monthly (-15%). Five consecutive L3 days = 20% equity loss with no emergency brake.
**Fix:** Weekly halt at -8%, monthly halt at -15%. Enforced in `check_all()` with `force_close_all=True`. Persist to Redis.
**Review Source:** Risk Manager CRIT-01 (moved from v1's C-06 to Phase A)

### A-08: Unify Kill Switches (2h)
**Files:** `execution/virtual_trader.py:55-56`, `qualification/circuit_breakers.py:44`
**Problem:** Two uncoordinated kill switches: VT (_DAILY_PNL_KILL = -£200 = 2%) and CB (L3 RED = 4%). VT only counts closed trades (misses unrealized), CB uses total. Open position can be down £350 with neither triggering.
**Fix:** Single authority: circuit breaker is canonical, uses TOTAL P&L (realized + unrealized). Remove VT's independent kill switch. Scale thresholds with equity (not hardcoded £200).
**Review Source:** Risk Manager CRIT-06

### A-09: Anti-Cascade Stop Logic — Portfolio-Wide (2h)
**File:** `qualification/circuit_breakers.py`
**Problem:** No rapid stop-out detector. 3 correlated positions can stop out in 2 minutes with only a 15-min cooldown. In a 12-instrument correlated portfolio (all US tech/semi leveraged ETPs), per-ticker and portfolio-wide cascades are the SAME event. Having both creates constant halts.
**Fix:** Single portfolio-level cascade breaker: 3 stop-outs in 15 min across ANY tickers → halt ALL entries for 30 min. Persist to Redis. (Per-ticker cascade is redundant in correlated universe — consolidated per Gemini v2 SRA recommendation.)
**Review Source:** Risk Manager HIGH-01, Gemini v2 SRA (consolidated A-09/A-10)

### A-11: Overnight ETP Protection — UK Time for 3x (1h)
**File:** `execution/virtual_trader.py:1406-1432`
**Problem:** Overnight protection uses ET (Eastern Time) for 3x ETPs. LSE closes at 16:30 UK. During DST, 15:55 ET = 20:55 UK — 4.5 hours after LSE close. 3x ETPs held through close with no protection.
**Fix:** Use UK time for ALL LSE-listed ETP overnight protection. Close 3x by 16:15 UK, 5x by 15:30 UK.
**Review Source:** Risk Manager HIGH-02

### A-12: 5x ETP Hard Kill Scheduler (1h)
**File:** `main.py` scheduler + `execution/virtual_trader.py`
**Problem:** 5x overnight kill check only runs in position update loop. If scan loop stalls during GPD calculation, 16:15-16:30 UK window passes without check. 3% overnight gap on 5x = 15% loss.
**Fix:** Dedicated `add_job` CronTrigger at 15:30 UK sharp for 5x hard kill. Separate from scan loop. Cannot be delayed by other processing.
**Review Source:** Risk Manager MED-04

### A-13: Consecutive Loss Threshold Unification (0.5h)
**Files:** `qualification/risk_sizer.py:48,152-154`, `qualification/circuit_breakers.py:60-62`
**Problem:** ImmutableRiskRules says halt at 5 losses. CircuitBreakers says halt at 7 losses. Two systems disagree.
**Fix:** Single authority: circuit breaker. Remove duplicate loss-streak logic from ImmutableRiskRules. Pick 5 as the threshold (more conservative).
**Review Source:** Risk Manager HIGH-04

### A-14: Scan Loop Overlap Prevention (1h)
**File:** `main.py:5023-5093`
**Problem:** CronTrigger scans don't have `max_instances=1`. At 08:00 UK, both 60s continuous scan AND "LSE Open" CronTrigger call `run_scan()` concurrently, mutating shared state.
**Fix:** Add `max_instances=1`, `coalesce=True`, `misfire_grace_time=60` to ALL CronTrigger scan jobs. Add `asyncio.Lock` in scan coordinator.
**Review Source:** Systems Architect Findings #2, #12

### A-15: Write-Ahead Redis State Journal — Blood Oath #2 (3h)
**File:** Enhancement across `circuit_breakers.py`, `chandelier_exit.py`, `main.py`
**Problem:** If Docker reboots during the 3-4 week Validation Gate, circuit breaker state is lost. CB Redis persistence (A-06) stores state, but doesn't guarantee write-before-operation ordering. Journal must exist BEFORE validation begins.
**Fix:** Journal entry BEFORE operation. All critical state survives restart. Use `appendfsync always` for state keys. Weekend-aware TTLs. All writes wrapped in `asyncio.Lock` (correct for AsyncIOScheduler which runs coroutines, not threads). If any CB writes happen inside `run_in_executor()`, use Redis `SETNX` as fallback.
**Review Source:** Gemini v3 SRA Schedule Paradox 2.0 (moved from Phase E to Phase A)

---

## PHASE B — SILENT KILLERS + TIMING (10 items, ~14h)

**Now that safety is fixed, remove the blockages preventing signal execution.**

### B-01: THE DUAL THROTTLE PARADOX (SK-04 + T-08) (1.5h)
**Files:** `strategies/daily_target.py:348,497` + `qualification/risk_sizer.py:362,370`
**Problem:** +1.5% tier caps gross at 1.1% net max. `_daily_signal_fired` gives ONE shot per day.
**Fix:** Remove +1.5% tier (keep +2.0%). Remove single-fire limit — allow multiple entries (subject to position limits).
**AEGIS Ref:** SK-04, T-08

### B-02: Remove First-30-Min Blackout (T-01) (0.5h)
**File:** `strategies/daily_target.py:324-333`
**Problem:** Blocks ALL signals 08:00-08:30 UK.
**Fix:** Replace with spread-aware gate: if spread > 2.5× 3-day median → WAIT (not BLOCK). Use median, not absolute bps.
**Review Source:** Gemini valid finding (2.5× median replaces 35bps hard-cap)

### B-03: Remove Lunch Dead Zone Blackout (T-02) (0.5h)
**File:** `strategies/daily_target.py:335-344`
**Fix:** Replace with 0.85× confidence multiplier during 11:30-13:00.

### B-04: Move GPD Tail Risk to Nightly Batch (T-04) (1h)
**File:** `strategies/daily_target.py:414-435`
**Fix:** Move GPD fit to nightly batch. Cache in Redis. Frees scan loop for faster execution.

### B-05: THE EQUITY DENOMINATOR PHANTOM (SK-01) (1h)
**File:** `qualification/circuit_breakers.py:298,387`
**Problem:** `_starting_equity` frozen at init.
**Fix:** Update at daily reset from broker/VT equity. Assert freshness — if reset fails, block all trading until confirmed.
**Review Source:** Risk Manager HIGH-03

### B-06: THE ZOMBIE HALT (SK-02) (2h)
**File:** `main.py:1176-1184`, `database.py:1008-1022`
**Fix:** `WHERE date >= session_start_date` on ALL loss-streak queries. Build ScopedQuery enforcer.

### B-07: THE CONFIDENCE CEILING (SK-03) (1h)
**File:** `strategies/daily_target.py:71`, `qualification/risk_sizer.py:45`
**Fix:** Unify to 65 everywhere. Create ThresholdRegistry(frozen=True).

### B-08: Reweight FAST Tier Indicators (T-05) (2h)
**File:** `strategies/daily_target.py:127-202`
**Problem:** FAST path requires same 8 indicators as SLOW. MACD, RSI, and ROC are all mathematical derivatives of closing price — when price gaps up, all three fire simultaneously. This is multicollinearity, not consensus. Additionally, EMA50 slope on 1-min bars at 09:05 UK uses 45/50 bars from yesterday — overnight gap pollutes the slope, creating false "strong trend" signals.
**Fix:** FAST tier: 3/4 orthogonal indicators: (1) RSI on underlying (momentum oscillator), (2) VWAP (volume-weighted price), (3) spread_ratio (current spread / 5-min median — microstructure quality), (4) **Opening Drift** (current price > session open print at 08:00 UK — purely intraday, zero overnight contamination). Drop MACD, ROC, StochRSI, EMA9/EMA20, and EMA50 slope from FAST tier.
**Review Source:** Quant Finding #5, Gemini v2 CQO (multicollinearity), Gemini v3 CQO (EMA50 overnight gap pollution)

### B-09: Adjust ADX Thresholds (T-06) (0.5h)
**File:** `strategies/daily_target.py:77-79`
**Fix:** FAST: ADX >= 15. SLOW: ADX >= 20. Already at these values in code — verify and document.
**NOTE:** Code already has ADX at 15/20. Verify no regression.

### B-10: Verify RVOL Thresholds (T-07) (0.25h)
**File:** `strategies/daily_target.py`
**Fix:** Code already has RVOL at 0.60 FAST / 0.65 SLOW. v1 plan's 0.30 was already rejected in code comments ("0.30 was suicidal on LSE ETPs"). Verify current values are correct. Do NOT lower further.
**Review Source:** Gemini triage (already fixed in code)

### B-11: FAST Path Gate Optimization (T-10) (2h)
**File:** `main.py:1823-2850`
**Fix:** `fast_qualify()` with 7 critical gates: ISA eligible, spread OK, CB not halted, position limit, correlation, size >0, ticker not halted. Skip ML (disabled anyway), full SHAP, portfolio heat for FAST.

### B-12: US Open Stabilization Wait (0.5h)
**File:** `strategies/daily_target.py`
**Problem:** US equity market opens at 14:30 UK. First 5 minutes feature violent cross-exchange arbitrage and auction imbalance clearing. Current code has a "Late-Day Trough" gate (13:30-14:30 UK) but NO explicit 14:30-14:35 stabilization period. If FAST tier fires at 14:30:01, it buys pure auction noise on LSE derivatives.
**Fix:** Mandatory 300-second wait (14:30-14:35 UK) before allowing any FAST tier signals. SLOW tier can continue evaluating.
**Review Source:** Gemini v2 EMS (restored from v1's F-10, wrongly killed in v2.0)

---

## PHASE C — CORE EDGE (12 items, ~26h)

**Features that directly improve win rate. Deploy after Phase B, then begin 200-Trade Gate.**

### C-01: Bayesian Stranger Penalty (3h)
**File:** `qualification/dynamic_sizer.py`
**Problem:** Untested tickers get full sizing.
**Fix:** Use Beta-Binomial posterior: `kappa(n, wins) = min(1, sqrt(n/30)) × (alpha + wins) / (alpha + beta + n)` with alpha=beta=2 (weakly informative prior centered at 50%). Shrink toward population mean win rate, not toward zero.
**Review Source:** Quant Finding #7 (improved formula from v1)

### C-02: Three Profit Ladders → One (2h)
**File:** `core/chandelier_exit.py` + `execution/virtual_trader.py` + `qualification/risk_sizer.py`
**Fix:** Chandelier Exit as sole authority. Delete inline ladders. Wire `register()` at position open, `update()` in scan loop.

### C-03: Chandelier Exit Wiring (2h)
**File:** `main.py` + `execution/virtual_trader.py`
**Problem:** 344 lines of dead code — never called.
**Fix:** Wire register at position open, update in scan loop.

### C-04: 33/67 Profit Banking (3h)
**File:** `core/chandelier_exit.py` + `execution/virtual_trader.py`
**Fix:** Rung 2 (+4%) = bank 15%, Rung 3 (+6%) = bank 33%, Rung 4 (+8%) = bank 50%.

### C-05: Overnight Gap Veto (>2 ATR) (2h)
**File:** `execution/virtual_trader.py`
**Fix:** Veto entry if overnight gap > 2× (ATR/Close%).

### C-06: Regime Confirmation Buffer (1h)
**File:** `core/cross_asset_macro.py`
**Problem:** HMM regime applied immediately with no confirmation. HMM refits on 63 data points (overfit).
**Fix:** 3-tick confirmation before regime transition. Refit HMM weekly (not every 30 min). Consider replacing with simpler threshold classifier (VIX level + 50d MA slope).
**Review Source:** Quant Finding #10

### C-07: Transition Buffer + VIX Hysteresis (2h)
**File:** `feeds/regime_classifier.py:293`, `qualification/circuit_breakers.py:476`
**Fix:** 3-tick confirmation + proportional VIX deadband (15% of level). Require VIX to fall below trigger-10% to re-arm.

### C-08: ISA Correlation Families — Use UNDERLYING_MAP (2h)
**File:** `qualification/dynamic_sizer.py:1302-1313`
**Problem:** US-only families (QQQ, SPY, NVDA). ISA .L tickers never match.
**Fix:** Use `UNDERLYING_MAP` from isa_universe.py. Use Spearman rank correlation (not Pearson — fat tails).
**Review Source:** Quant Finding #13

### C-09: Time-of-Day Scaling — LSE Windows (2h)
**File:** `qualification/dynamic_sizer.py:97-103`
**Problem:** ET-only windows. LSE morning = 0.5× penalty.
**Fix:** Add LSE-specific windows. Detect from ticker suffix `.L`.

### C-10: Regime-Dependent Directional Filtering (2h)
**File:** `strategies/daily_target.py`
**Fix:** TRENDING_UP_STRONG → LONG only. TRENDING_DOWN_STRONG → INVERSE only. SHOCK → NO TRADING. Use confidence penalty (-15 points), NOT hard veto, for moderate regimes. Data shows winning longs can occur in bearish regimes.
**Review Source:** Quant Finding #14 (softened from v1's hard veto)

### C-11: Atomic Mutual Exclusion — No Long + Inverse (1h)
**File:** `execution/virtual_trader.py`
**Fix:** If counterpart (QQQ3.L/QQQS.L) held → VETO.

### C-12: Priority Signal Queue (2h)
**File:** `main.py:1136`
**Fix:** heapq priority queue sorted by -composite_score.

---

## PHASE D — ETP INTELLIGENCE (8 items, ~16h)

**Leveraged ETP-specific rules from AEGIS §2.** Deploy after 50+ trades show positive expectancy.

### D-01: 5x ETP Separate Scoring Profile (2h)
**File:** `strategies/daily_target.py`
**Fix:** 5x profile: confidence floor 80, execution window 14:30-15:30 UK only, max hold 3h, spread veto at 1.8× median, 10% equity hard cap.

### D-02: Time-Zone Split VWAP Weighting (2h)
**File:** `strategies/daily_target.py`
**Fix:** 08:00-14:30 UK: 1.0× (MM noise). 14:30-16:30 UK: 1.8× (institutional). Pre-open: 1.4×.

### D-03: P90 Spread Tracker (1.5h)
**File:** `strategies/daily_target.py`
**Fix:** Track rolling 20-day P90 spread per ETP. Veto if current > 2.5× 3-day median.

### D-04: First-Half / Last-Half Hour Predictability (2h)
**File:** `strategies/daily_target.py`
**Fix:** Track 08:00-08:30 return. If > +0.5% → +5 long confidence. US open agrees → +10.

### D-05: Rebalancing Flow Awareness (2h)
**File:** `strategies/daily_target.py` + `execution/virtual_trader.py`
**Problem:** v2.0 blocked entries 15:00-15:30 UK for "rebalancing flow." This is WRONG — LSE ETP rebalancing happens ~19:00 UK (verified in `rebalance_flow.py:17`), not 15:00. The 15:00-15:30 UK window is prime US morning momentum. Only fear the LSE closing auction (16:20-16:30 UK).
**Fix:** (1) NO new entries 16:15-16:30 UK (MMs begin pulling liquidity at 16:15, not 16:20). (2) Exit open 3x positions by 16:10 UK to avoid degraded continuous-trading spreads. (3) 5x must exit before 15:30 UK (per A-12). (4) Do NOT block 15:00-15:30 — this is high-alpha US overlap.
**Review Source:** Gemini v2 EMS (corrected timing), Gemini v3 EMS (16:15 liquidity pull)

### D-06: No-Signal Escalation Protocol (2h)
**File:** `strategies/daily_target.py`
**Fix:** 14:00 UK: lower floor 65→60. 15:00 UK: activate Universal Scanner. 15:30 UK: accept FLAT.

### D-07: ETP Factsheet Registry (1.5h)
**File:** NEW: `uk_isa/etp_factsheet_registry.json`
**Fix:** Registry with ISIN, provider, underlying ticker, leverage factor, rebalancing freq, fee. Refresh quarterly.

### D-08: Vol-Managed Sizing (3h)
**File:** `qualification/dynamic_sizer.py`
**Problem:** Position size doesn't scale inversely by vol.
**Fix:** `weight = (target_vol / realised_vol_5d_intraday) × base_weight`. Use instrument-appropriate target_vol: 0.60 for 3x, 1.0 for 5x (NOT generic 15% equity target — that would reduce ALL positions to <10%). Use 5-day intraday vol (5-min bars, not daily close-to-close). NEVER scale UP (asymmetric).
**Review Source:** Quant Finding #8 (fixed vol target from v1)

---

## PHASE E — ROBUSTNESS (3 items, ~5h)

**Blood Oath #1 + hardening. (Blood Oath #2 moved to Phase A as A-15. Blood Oath #3 and #4 moved to Phase 0.)**

### E-01: ThresholdRegistry (frozen=True) — Blood Oath #1 (3h)
**File:** NEW: `core/threshold_registry.py`
**Fix:** Single frozen registry from YAML. All modules read from registry. Pydantic validation at boot. Fail hard on invalid config.
**NOTE:** Also validates settings.yaml on load (addresses config validation gap).
**Review Source:** Systems Architect Finding #14

### E-02: SHOCK_RECOVERY + Stale Data Counter (1h)
**File:** `qualification/dynamic_sizer.py`, `feeds/indicators.py`
**Fix:** Recovery counter decrements per session (not per signal). Stale data: track tick-change counter, alert if >5 min unchanged during market hours.

### E-03: IB Gateway Graceful Degradation (1h)
**File:** `main.py` feeds
**Problem:** IB Gateway daily restart at 04:45 UK leaves system blind for 30-60s. Monday 2FA = unknown downtime.
**Fix:** Detect IB Gateway unavailability (port 4004 refused). Switch to yfinance fallback. Log CRITICAL. Pause trading while broker disconnected.
**Review Source:** Systems Architect Finding #7

---

## DEFERRED TO Q2+ (NOT IN THIS PLAN)

Items from v1 that were killed or deferred:

| Item | Reason | Defer Until |
|------|--------|-------------|
| CDaR Circuit Breaker (v1 C-07) | Institutional metric, irrelevant at £10K | Never (kill) |
| iCVaR Portfolio Veto (v1 C-08) | Portfolio-level risk for 3-position system | Never (kill) |
| PEAD Power-Law Decay (v1 D-06) | Multi-week anomaly, coefficient 6x too high, yfinance data unreliable | Q2+ if single-stock edge proven |
| Chain Reaction Boost (v1 D-07) | Requires reliable P&L data that doesn't exist | Q2+ after 200+ trades |
| Inverse Pivot Crash Monetisation (v1 D-12) | Zero crash trades, AEGIS v15 already defers (RK-02) | Q2+ (keep dormant) |
| CUSUM Drift Detection (v1 E-10) | Over-engineering for pre-validation stage | Q2+ after ML re-enabled |
| Multi-TF Confirmation (v1 F-01) | Prove single TF first | Q2+ |
| Adaptive Kelly Warmup (v1 F-02) | Need 200+ trades before Kelly matters | Q2+ |
| Sector Rotation (v1 F-09) | One-bet portfolio makes rotation meaningless | Q2+ |
| Event-Driven Scanning (v1 A-08) | GIL contention + scan overlap risk | Q2+ after main.py refactor |
| Apex Scout (§3) | Build after core edge validated | Q2+ (~40h) |
| ML Auto-Retrain (v1 B-02) | ML disabled until data quality fixed | Q2+ after 200+ trades |
| Walk-Forward CV (v1 C-03) | ML disabled | Q2+ after ML re-enabled |
| Class Weight Balancing (v1 C-11) | ML disabled | Q2+ after ML re-enabled |
| ML Feature Leakage Fix (v1 B-14) | ML disabled (fix before re-enable) | Q2+ before ML re-enable |
| ML Regime Encoding Fix (v1 B-01) | ML disabled (fix before re-enable) | Q2+ before ML re-enable |
| main.py Refactor | God Object, 7700+ lines | Q2+ (before adding new features) |

---

## IMPLEMENTATION ORDER (Critical Path — v2.1 Revised)

**Key v2.1 change:** ScopedQuery + TradeAttribution moved to Phase 0 per SRA Schedule Paradox. 200-Trade Gate inserted AFTER Phase B (raw S15 baseline) before building edge features.

```
Week 1: Phase 0 — Data Integrity + Telemetry (14h)
  Day 1: 0-01 (verify data pipeline), 0-02 (purge backfill), 0-04 (signal copy fix)       [3h]
  Day 2: 0-03 (indicators on underlying — biggest change), 0-05 (disable ML)                [3.5h]
  Day 3: 0-06 (signal decomp), 0-07 (queue fix), 0-08 (exception logging)                   [2.25h]
  Day 4: 0-09 (ScopedQuery), 0-10 (TradeAttribution)                                        [4h]
  → Verify: outcomes.jsonl has real P&L. Indicators on underlying. Attribution captures MFE/MAE.

Week 2-3: Phase A — Safety Systems (22h)
  Day 1: A-01 (ISA gate), A-02 (RISK_OFF), A-03 (paper parity), A-04 (immutable), A-05 (VIX)  [4h]
  Day 2: A-06 (CB Redis persistence + locking)                                                   [4h]
  Day 3: A-07 (weekly/monthly CB), A-08 (unify kill switches), A-13 (loss thresholds)            [4.5h]
  Day 4: A-09 (anti-cascade portfolio), A-11 (overnight UK time), A-12 (5x kill), A-14 (scan)    [5h]
  Day 5: A-15 (Write-Ahead Redis State Journal — must exist before Validation Gate)               [3h]
  → Deploy to EC2. Restart Docker, confirm halts persist across restart. Verify journal writes.

Week 3: Phase B — Silent Killers + Timing (16h)
  Day 1: B-01 (dual throttle), B-02 (blackout), B-03 (lunch), B-09 (ADX), B-10 (RVOL)  [3.25h]
  Day 2: B-04 (GPD batch), B-05 (equity phantom), B-07 (confidence ceiling)              [3h]
  Day 3: B-06 (zombie halt), B-08 (FAST reweight — orthogonal indicators)                [4h]
  Day 4: B-11 (FAST gate), B-12 (US open 300s wait)                                      [2.5h]
  → Deploy. Verify scan loop fires signals during market hours.

  ╔════════════════════════════════════════════════════════════╗
  ║  PHASE V: 200-TRADE VALIDATION GATE (BASELINE)           ║
  ║  Run raw S15 engine with safety + timing fixes only.      ║
  ║  Measure WR, PF, Sharpe on clean data with attribution.   ║
  ║  This establishes the BASELINE before adding edge.        ║
  ║  If WR < 33% → strategy has negative EV. STOP.           ║
  ║    (At 1.5:1 R:R, breakeven = 40%. Below 33% = bleeding) ║
  ║  If WR 33-40% → proceed to Phase C (edge features).      ║
  ║  If WR > 40% → proceed to Phase C with confidence.       ║
  ╚════════════════════════════════════════════════════════════╝

Week 4-5: Phase C — Core Edge (26h) [ONLY IF BASELINE WR > 33%]
  Day 1: C-01 (Bayesian sizing), C-08 (correlation families), C-09 (ToD windows)   [6h]
  Day 2: C-02, C-03 (profit ladder unification + wiring)                             [4h]
  Day 3: C-04, C-05 (profit banking + gap veto)                                      [5h]
  Day 4: C-06, C-07 (regime fixes + VIX hysteresis)                                  [4h]
  Day 5: C-10, C-11, C-12 (directional filter + exclusion + priority queue)           [5h]

Week 5-6: Phase D — ETP Intelligence (16h)
  Day 1: D-01, D-03, D-07 (5x scoring + spread + registry, 5h)
  Day 2: D-02, D-04, D-06 (VWAP weighting + predictability + escalation, 6h)
  Day 3: D-05, D-08 (rebalancing awareness + vol-managed sizing, 5h)

Week 6: Phase E — Robustness (5h)
  Day 1: E-01 (ThresholdRegistry, 3h)
  Day 2: E-02, E-03 (recovery + IB degradation, 2h)
  → Deploy. System fully robust. Continue 200-Trade Gate to completion.
```

---

## 200-TRADE VALIDATION GATE

**Replaces v1's 100-trade gate (statistically too weak).**

At 200 trades with 40% WR, standard error = sqrt(0.4 × 0.6 / 200) = 0.035. 95% CI = [33%, 47%]. This provides meaningful signal.

**Pass criteria:**
- WR >= 40% (z-score >= 2.86 vs 50% null)
- Profit Factor > 1.2
- Sharpe > 1.0 (annualized)
- Max daily drawdown < -4%
- No circuit breaker state leaks across restarts
- At least 10 unique tickers traded

**If PASS:** Proceed to Phase F optimization (deferred items from v1).
**If FAIL at WR 33-40%:** Root cause analysis with trade attribution data (0-10). Likely indicator calibration issue. Proceed to Phase C edge features.
**If FAIL at WR < 33%:** Negative EV at any realistic R:R ratio. Fundamental strategy review required. Consider whether intraday momentum on leveraged ETPs is a viable edge.

---

## 4 BLOOD OATH GUARANTEES (Structural)

| # | Guarantee | Phase | Status |
|---|-----------|-------|--------|
| 1 | ThresholdRegistry(frozen=True) | E-01 | Planned |
| 2 | Write-Ahead Redis State Journal | A-15 | Planned (moved to Phase A) |
| 3 | ScopedQuery Builder | 0-09 | Planned (moved to Phase 0) |
| 4 | TradeAttribution Record | 0-10 | Planned (moved to Phase 0) |

---

## 12 CORE RUNTIME INVARIANTS (Must pass after Phase E)

| # | Name | Check |
|---|------|-------|
| 1 | IMAGE_PARITY | env.IMAGE_DIGEST == git.HEAD_SHA |
| 2 | ISA_FAIL_CLOSED | ticker.is_isa_eligible == True |
| 3 | VIX_FAIL_CLOSED | vix != 0 AND age < 300s |
| 4 | DRAWDOWN_CASCADE | daily_pnl > L3_threshold |
| 5 | POSITION_LIMIT | open_positions <= MAX_CONCURRENT |
| 6 | OVERNIGHT_FLAT | positions == 0 at 16:25 GMT |
| 7 | EQUITY_FRESH | equity matches broker ±0.1% |
| 8 | CONFIDENCE_FLOOR | signal.confidence >= 65 |
| 9 | IMMUTABLE_RISK | __setattr__ raises post-init |
| 10 | HALT_PERSISTENCE | Redis halt survives restart |
| 11 | LOSS_STREAK_SCOPED | query WHERE date >= session_start |
| 12 | DATA_FEED_ALIVE | last_tick_age < MAX_STALE AND tick_count > MIN |

---

## SUCCESS CRITERIA

Phase 0 → **Data is trustworthy** (real P&L, clean training data, correct indicators)
Phase A → System is **safe** (all safety gates active, survive restarts)
Phase B → System can **fire signals** (timing blockages removed)
Phase C → System has **edge** (proper sizing, profit banking, regime awareness)
Phase D → System exploits **ETP mechanics** (rebalancing, vol-scaling, 5x rules)
Phase E → System is **robust** (Blood Oath guarantees, state persistence)

**Ultimate gate:** 200 paper trades with WR >= 40%, PF > 1.2, Sharpe > 1.0.
Only then consider real capital.

---

## ITEM COUNT SUMMARY

| Phase | Items | Hours | Focus |
|-------|-------|-------|-------|
| 0: Data Integrity + Telemetry | 10 | 14h | Verify data, fix indicators, build telemetry |
| A: Safety Systems | 14 | 22h | Fix ALL safety + state journal before Validation Gate |
| B: Silent Killers + Timing | 12 | 16h | Remove signal blockages + US open wait |
| V: VALIDATION GATE | — | ~3-4 weeks | 200-trade baseline (must hit WR > 33%) |
| C: Core Edge | 12 | 26h | Win rate features (only if WR > 33%) |
| D: ETP Intelligence | 8 | 16h | Leveraged ETP mechanics |
| E: Robustness | 3 | 5h | Blood Oath #1 + hardening |
| **TOTAL** | **59** | **~99h** | **(×2 realistic = ~200h)** |
| **KILLED** | **10** | **-27h** | CDaR, iCVaR, PEAD, etc. + per-ticker cascade |
| **DEFERRED** | **8** | **-18h** | To Q2+ after validation |

---

## APPENDIX: ADVERSARIAL REVIEW CROSS-REFERENCE

Every item traces back to at least one adversarial finding:

| Reviewer | Findings Incorporated | Findings Rejected |
|----------|----------------------|-------------------|
| Risk Manager | CRIT-01 through CRIT-06, HIGH-01 through HIGH-07, MED-01 through MED-07 | None (all valid) |
| Quant Researcher | Findings 1,3,5,6,7,8,10,12,13,14,15,16 | F2 (2% target — already addressed in MEMORY.md as fantasy), F9 (PEAD killed), F11 (Kelly deferred) |
| Systems Architect | Findings 2,3,6,7,11,12,14 | F1 (main.py refactor — deferred to Q2+, too much churn now), F4 (SQLite migration — deferred), F5 (OOM — monitor, don't preemptively migrate) |
| Devil's Advocate | Findings 1,2,3,6,7,9 | F8 (academic citations — citations are documentation, not claims of validity), F11 (Blood Oath naming — nomenclature doesn't affect function) |
| Gemini v1 4-Persona | RVOL already 0.60, spread 2.5× median, US open wait, ISA gate Phase A, kill CDaR/iCVaR, defer event-driven | B-05 SHORT P&L sign (false positive — code is correct) |
| Gemini v2 Syndicate | CQO multicollinearity (B-08 fixed), SRA schedule paradox (E-03/E-04→Phase 0), EMS rebalancing timing (D-05 rewritten), EMS US open wait restored (B-12), SRA Redis locking (A-06 updated), PSE copy protection (0-04 upgraded), SRA cascade consolidation (A-09/A-10 merged) | CQO regime-adaptive floor (redundant with RISK_OFF=0.0), EMS portfolio heat in FAST (position limits sufficient), PSE ScopedQuery schema risk (no schema change) |
| Gemini v3 Final | CQO EMA50 gap pollution (B-08 → Opening Drift), SRA Schedule Paradox 2.0 (E-02→A-15), PSE nested list copy (0-04 upgraded), CQO validation gate 25%→33%, EMS closing auction 16:15 (D-05 updated) | SRA asyncio.Lock wrong (PARTIALLY: AsyncIOScheduler uses coroutines, not threads — asyncio.Lock IS correct; added SETNX fallback note for run_in_executor cases) |

---

# v3.0 ADDENDUM — ALL OUTSTANDING AEGIS ITEMS

**Everything below is NEW in v3.0.** These are ALL items from the full AEGIS v16.2 spec (98 stop-ship + all section items) that were NOT already covered by Phases 0-E above. Organized from most critical to most aspirational.

**Structure:**
- **Phase F** — Quick Wins & Bug Fixes (items under 1h each, massive risk reduction)
- **Phase G** — Risk Architecture Completion (15-control defence matrix items missing from v2.2)
- **Phase H** — Operational Infrastructure (startup gate, monitoring, deployment)
- **Phase I** — Universe Expansion + Discovery (Amihud, ASER, DSR, Apex Scout)
- **Phase J** — ML Rehabilitation (fixes needed before ML can be re-enabled)
- **Phase K** — Execution Physics + Microstructure (Phase Q2 items)
- **Phase L** — Quantum Apex End-State (Phase Q3-Q4, Rust FFI, DQN, Neural Hawkes)

**Gate logic:** F and G are PRE-validation (do alongside Phases 0-E). H through I are POST-validation (after 200-Trade Gate passes). J is after 500+ trades. K-L are Phase Q2-Q4.

---

## PHASE F — QUICK WINS & BUG FIXES (18 items, ~12h)

**Tiny fixes with outsized risk reduction. Most are under 30 minutes. Do these alongside Phases 0-A.**

### F-01: Fix settings.yaml Timezone US/Eastern → Europe/London (0.1h)
**File:** `config/settings.yaml:9`
**Problem:** Primary timezone set to `US/Eastern`. System trades LSE UK hours. Any module reading primary timezone is off by 5 hours.
**Fix:** Change line 9 to `timezone: Europe/London`.
**AEGIS Ref:** AB-02 (P1-44)

### F-02: Signal Queue Consumer — Async Consumer Coroutine (1h)
**File:** `main.py:23,3081,4208,4437`
**Problem:** `Queue(maxsize=50)` with `put_nowait()` but NO consumer exists. Queue is write-only. After 50 signals, `queue.Full` exception fires (already fixed in 0-07 to catch correct exception class). But signals are silently discarded regardless.
**Fix:** Add async consumer coroutine with priority sorting (Option A). DO NOT process signals inline — inline processing blocks the 60s scan loop for 1.5s per signal (SA-02: 4 signals = 6s desync of APScheduler tick sequence). Consumer coroutine runs independently, processes signals as they arrive.
**AEGIS Ref:** F-01 fatal flaw, R21-07 (P1-3)
**v3.1 Fix:** SA-02 — inline processing rejected as it desynchronizes APScheduler.

### F-03: Inverse ETP List — Single Source of Truth (1h)
**File:** `main.py:4571-4574`, `config/__init__.py:153-158`, `uk_isa/isa_universe.py:478-481`, `main.py:2173-2180`, `main.py:5963-5966`, `main.py:6013-6015`
**Problem:** 6 unsynchronized inverse ETP lists scattered across 4 files. Adding/removing an inverse ETP requires editing 6 locations.
**Fix:** Create single `INVERSE_ETPS` dict in a pure-data module `config/universe_constants.py` with ZERO dependencies. All 6 scattered references import from it. Do NOT put in `uk_isa/isa_universe.py` — if main.py imports from there and isa_universe.py imports types from main.py, circular import crash at boot (SA-06).
**AEGIS Ref:** F-04 fatal flaw
**v3.1 Fix:** SA-06 — isolated to pure-data config module to prevent circular imports.

### F-04: Kill Switch — Human Confirmation Required to Clear (0.5h)
**File:** Redis key `nzt:kill`, `main.py` scheduler, `delivery/telegram_notifier.py`
**Problem:** Kill switch can get stuck permanently. No clear logic exists.
**Fix:** At 06:00 UK daily: check if equity has recovered above L2 threshold. If yes AND no active positions, send Telegram webhook with "KILL SWITCH ELIGIBLE FOR CLEAR — reply /confirm to resume trading". REQUIRE human confirmation via Telegram reply to clear. NEVER auto-clear a P0 safety mechanism — machines must not auto-resume from catastrophic halts (CRO-08).
**AEGIS Ref:** F-05 fatal flaw
**v3.1 Fix:** CRO-08 — auto-clear replaced with human confirmation requirement.

### F-05: ML should_retrain() Signature Fix (0.1h)
**File:** `core/ml_meta_model.py:537`, `main.py:5605`
**Problem:** `should_retrain(self, last_trained_at: datetime)` requires positional arg. Caller passes ZERO args → `TypeError`, silently caught. ML NEVER auto-retrains.
**Fix:** Remove `last_trained_at` parameter, use `self._last_trained_at` internally.
**AEGIS Ref:** M-03 (P0-3 in stop-ship)

### F-06: Lunch RVOL — Time-of-Day Normalized (0.5h)
**File:** `settings.yaml`, `feeds/indicators.py`
**Problem:** Lunch RVOL at 1.7 filters 95% of setups during 11:30-13:00 UK. Simply lowering to 1.3 on an all-hours denominator is mathematically improbable during the structural lunch trough unless a macro shock occurs (CQ-01).
**Fix:** RVOL denominator must be time-of-day normalized. Compare current volume strictly against the 20-day historical mean for that specific 5-minute time bucket. Threshold remains 1.3× but against the time-normalized baseline. This makes 1.3× achievable during lunch without requiring a shock.
**AEGIS Ref:** F-09 medium flaw
**v3.1 Fix:** CQ-01 — time-of-day normalized RVOL denominator instead of flat threshold reduction.

### F-07: VIX Cache TTL 30min → 5min (0.25h)
**File:** `core/cross_asset_macro.py`
**Problem:** 30-minute VIX cache. During a crash, system trades on 30-min-old VIX.
**Fix:** 5-min TTL for VIX. Keep 30-min for DXY, credit, Fear&Greed (slow-moving).
**AEGIS Ref:** F-12 medium flaw

### F-08: Daily Loss Limit Contradiction — Remove Duplicate (0.5h)
**File:** `qualification/risk_sizer.py` (ImmutableRiskRules.MAX_DAILY_LOSS=0.03)
**Problem:** ImmutableRiskRules has MAX_DAILY_LOSS=3%, but L1/L2/L3 cascade is the real governor. Two overlapping systems = undefined behavior.
**Fix:** Remove MAX_DAILY_LOSS from ImmutableRiskRules. L1/L2/L3 cascade is sole daily-loss governor.
**AEGIS Ref:** AB-01 (P1-43)

### F-09: Overnight Kill ALL ETPs + P0 Fallback Alert (0.5h)
**File:** `settings.yaml`, `execution/virtual_trader.py`, `delivery/telegram_notifier.py`
**Problem:** Only 5x ETPs have enforced overnight kill. Ruin math assumes 0.75% max loss, but overnight gap on 3x = 5-15% portfolio loss.
**Fix:** `overnight_kill=True` for ALL leveraged ETPs during paper and limited live phases. Additionally: if `open_positions > 0` at 16:31 UK (i.e., exit order failed to fill), trigger automated P0 alert via Telegram + SNS phone call to operator (CRO-02). Position left overnight on 3x leverage is a catastrophic scenario requiring immediate human intervention.
**AEGIS Ref:** AB-05 / R21-27 (P1-13), elevated to critical per Abyss Analysis
**v3.1 Fix:** CRO-02 — added P0 fallback alert (SNS phone call) if positions survive 16:31.

### F-10: Exit Loop Decoupling — 10s Price Check for Open Positions (1h)
**File:** `main.py` scan loop, `execution/virtual_trader.py`
**Problem:** Stop-loss monitoring only runs in the 60s scan loop. If scan stalls during GPD/indicator computation, stops are unmonitored.
**Fix:** Dedicated 10s coroutine that checks prices for tickers with open positions only. Separate from full-universe scan.
**AEGIS Ref:** R-05 (stop detection, P1-17), R21-40 (P1-17)

### F-11: Single Risk Arbiter for 12 Flatten Paths (2h)
**File:** `main.py`, `qualification/circuit_breakers.py`, `execution/virtual_trader.py`
**Problem:** 12 different code paths can trigger position flatten. No coordination — two flatten commands can fire simultaneously causing double-sell or race conditions.
**Fix:** Single `RiskArbiter` class. All flatten requests go through it. Precedence: SYSTEM_HALTED > EMERGENCY_FLATTEN > REDUCE > NORMAL. CRITICAL: EMERGENCY_FLATTEN priority MUST bypass the queue entirely and execute synchronously in an isolated `try/except` block (CRO-03). This prevents the arbiter from becoming a single point of failure for emergency exits. Non-emergency flatten goes through the standard queue with one-at-a-time processing.
**AEGIS Ref:** R-09 Risk State Machine, R21-17 (P1-8)
**v3.1 Fix:** CRO-03 — EMERGENCY_FLATTEN bypasses queue to prevent arbiter SPOF.

### F-12: Portfolio Heat Cap 3.0% → 3.5% (0.1h)
**File:** `qualification/dynamic_sizer.py` or `settings.yaml`
**Problem:** Portfolio heat cap at 3.0% too tight. With 4 concurrent positions at 0.75% risk each = 3.0% exactly, no headroom for spread costs.
**Fix:** Raise to 3.5%.
**AEGIS Ref:** R21-23 (P1-10)

### F-13: Max Concurrent Positions — Reconcile 3 vs 4 (0.1h)
**File:** `settings.yaml:622` (currently 3), plan says 4
**Problem:** Code says max 3 concurrent, plan says 4. Must reconcile.
**Fix:** Set to 4 (per R-04: 4 × 10% = 40% total deployment cap).
**AEGIS Ref:** R21-34 (P1-16), R-04

### F-14: Stale Data Tick-Change Counter (1h)
**File:** `feeds/indicators.py` or `core/invariant_enforcer.py`
**Problem:** No detection of stuck data feeds. A frozen feed looks like a flat market.
**Fix:** Track tick-change counter per ticker. If >5 min unchanged during market hours AND volume >0 historically, alert DEGRADED. If >50% of universe stale >5 min, HALT.
**AEGIS Ref:** R21-24 (P1-11), RI-03 (P1-49), Runtime Invariant #12

### F-15: Correlation Brake — Notional Risk Cap Per Cluster (1.5h)
**File:** `qualification/dynamic_sizer.py`, `main.py:2441-2474`
**Problem:** Current pairwise correlation check doesn't group instruments. QQQ3.L + NVD3.L + 3SEM.L + GPT3.L all >0.85 correlated to NASDAQ but system doesn't see them as one cluster.
**Fix:** Group instruments by correlation cluster (UNDERLYING_MAP). Max 2 positions per cluster, BUT combined notional risk of the cluster MUST NOT exceed `MAX_RISK_PER_TRADE × 1.5` (CRO-04). Two NASDAQ 3x positions = 2× directional exposure — position count alone doesn't cap risk. Use rolling 20-day pairwise correlation >0.70 for cluster detection.
**AEGIS Ref:** R-03, R21-22 (P1-9), F-03 fatal flaw
**v3.1 Fix:** CRO-04 — cluster limit caps notional risk, not just position count.

### F-16: Rung Calibration — Ex-Ante ATR Only (1h)
**File:** `learning/trade_autopsy.py`, `core/chandelier_exit.py`
**Problem:** Profit ladder rung placement is theoretical. No data on how often price reaches each rung. Original plan proposed MFE-based calibration — but MFE is only known after trade concludes. Optimizing rungs to fit historical MFE is pure look-ahead bias and guarantees out-of-sample failure (AR-02).
**Fix:** Calibrate rungs strictly to ex-ante rolling ATR multiples (e.g., rung 1 = 1.0× ATR, rung 2 = 1.5× ATR, etc.). During paper phase, LOG MFE data for observational validation only — never feed MFE back into rung parameters. Rung placement changes require manual review + 50-trade validation.
**AEGIS Ref:** R21-02 (P1-1)
**v3.1 Fix:** AR-02 — MFE calibration replaced with ex-ante ATR. MFE logged for observation only.

### F-17: ETP Profit Ladder SHORT P&L Sign Fix (0.5h)
**File:** `execution/virtual_trader.py` (ETPProfitLadder)
**Problem:** SHORT/inverse P&L calculation may have sign issue in the ETPProfitLadder class.
**Fix:** Verify and fix P&L sign for inverse positions. Test with QQQS.L (inverse).
**AEGIS Ref:** R21-10 (P1-5)

### F-18: 24/7 Scanning — Halt Scan Loop AND Indicator Ingestion (1h)
**File:** `main.py:5276`, `feeds/indicators.py`
**Problem:** System scans 24/7 including weekends and overnight. Wastes compute and produces spurious signals on stale data. CRITICAL: if scan() simply skips but indicators (MACD, EMA, RSI) continue ingesting zero-volume flat-price bars overnight, volatility metrics are artificially crushed by the time the morning open arrives (AR-05).
**Fix:** The scan() loop must physically halt outside 06:00-22:00 UK on trading days. Additionally, all indicator computation must PAUSE ingestion during closed hours — do not feed overnight bars into rolling calculations. Indicators resume from the last live bar when market reopens. Allow 10s exit-monitoring coroutine to continue for position safety.
**AEGIS Ref:** F-08 medium flaw
**v3.1 Fix:** AR-05 — indicators must pause ingestion, not just skip scanning.

---

## PHASE G — RISK ARCHITECTURE COMPLETION (16 items, ~28h)

**Complete the 15-control defence matrix from AEGIS §6. Items not already in Phases 0-E.**

### G-01: R-04 Total Deployment Cap — 40% Equity (1h)
**File:** `qualification/dynamic_sizer.py`
**Problem:** No enforcement of max 40% equity deployed across all open positions.
**Fix:** Before any new position, check: `sum(open_position_notionals) + new_notional <= 0.40 × current_equity`. Hard veto if exceeded.
**AEGIS Ref:** R-04

### G-02: R-06 Drawdown Recovery Cascade — AUM-Tiered (3h)
**File:** `qualification/circuit_breakers.py`
**Problem:** No portfolio-level drawdown cascade. Only daily L1/L2/L3 exists. No multi-day drawdown protection.
**Fix:** Implement AUM-tiered drawdown cascade: at 10K-100K: Yellow -2%, Orange -4%, Red -6%, Critical -8%, Halt -8%. -12% halt was too loose for £10K — losing 12% means 6 days of max drawdowns without intervention (CRO-05). Persist to Redis. Scale thresholds as equity grows (tighten at low equity, relax above £100K).
**AEGIS Ref:** R-06
**v3.1 Fix:** CRO-05 — £10k tier halt tightened from -12% to -8%.

### G-03: R-07 Portfolio CVaR Gate — Parametric Cornish-Fisher (3h)
**File:** `qualification/dynamic_sizer.py`
**Problem:** No portfolio-level CVaR check. Individual position risk is capped but total tail risk isn't.
**Fix:** Block new entries if portfolio CVaR_95 > 3% of equity. Use Parametric CVaR with Cornish-Fisher expansion (accounts for skew and kurtosis) on a minimum 60-day rolling window. DO NOT use historical simulation on 20-day returns — 20 days means the 95th percentile is literally the single worst day, and a single outlier will freeze the system for a month (CQ-02).
**AEGIS Ref:** R-07 (note: iCVaR was killed in v2.0 — portfolio CVaR gate was not)
**v3.1 Fix:** CQ-02 — Parametric CVaR (Cornish-Fisher) replaces 20-day historical simulation.

### G-04: R-09 Risk State Machine — Formal Implementation (2h)
**File:** NEW: `core/risk_state_machine.py`
**Problem:** No formal risk state machine. Multiple subsystems can issue contradictory risk actions simultaneously.
**Fix:** Formal state machine: NORMAL → REDUCE → EXIT_ONLY → EMERGENCY_FLATTEN → SYSTEM_HALTED. Precedence enforced. Single executor processes one risk action at a time.
**AEGIS Ref:** R-09

### G-05: R-13 Event-Based Stop Widening + Fast EWMA Vol (3h)
**File:** `strategies/daily_target.py`, `core/chandelier_exit.py`
**Problem:** No event-based stop adjustment. US open, BoE rate decisions, UK data releases all cause volatility spikes that hit normal stops.
**Fix:** TWO-LAYER approach: (1) Fast EWMA volatility component (halflife=10 bars) feeds into ATR calculation — this naturally expands stops during macro events if calibrated correctly (AR-06). (2) Clock-based rules as FALLBACK safety net: US Open 14:30-15:30 UK: ATR floor 2.0x. BoE Rate Decision days 11:30-12:30 UK: ATR floor 2.0x. UK Data Release mornings (GDP, CPI) 06:30-08:00 UK: ATR floor 2.0x. FTSE Quarterly Rebalance days: halt new entries 15:30-16:35 UK. The EWMA component handles most cases; clock rules catch edge cases where EWMA hasn't reacted yet.
**AEGIS Ref:** R-13
**v3.1 Fix:** AR-06 — added fast EWMA vol component alongside clock-based fallback rules.

### G-06: R-14 ETP Financing Cost Offset — Overnight Only (1h)
**File:** `qualification/dynamic_sizer.py` or `strategies/daily_target.py`
**Problem:** No accounting for financing drag. Long leveraged ETPs lose ~2 bps/day, inverse lose ~4 bps/day.
**Fix:** Subtract financing drag from expected return in R:R calculation ONLY if expected hold time crosses the overnight boundary (AR-07). Financing is applied discretely overnight, not continuously intraday. For same-day trades (the vast majority), no adjustment needed. For overnight holds (emergency only): -2 bps for long, -4 bps for inverse.
**AEGIS Ref:** R-14
**v3.1 Fix:** AR-07 — financing penalty only applied if hold crosses overnight boundary.

### G-07: R-15 Gamma/Strike Proximity Risk — Shadow Mode (1h)
**File:** `strategies/daily_target.py`
**Problem:** When underlying is near a major options strike, MM gamma hedging causes unpredictable whipsaws.
**Fix:** SHADOW MODE only during Phase Q1: log proximity to major options strikes and correlate with trade outcomes. There is no empirical literature supporting a static -10 confidence penalty — the impact depends on Vanna/Charm exposure which varies (AR-08). Collect data first, then parameterize in Phase Q2 if evidence supports it.
**AEGIS Ref:** R-15
**v3.1 Fix:** AR-08 — arbitrary -10 penalty replaced with shadow-mode data collection.

### G-08: R-12 OBI Toxicity Wait Gate — Shadow Mode (1h)
**File:** `strategies/daily_target.py`
**Problem:** No order book imbalance awareness. System buys into toxic flow.
**Fix:** Shadow mode only (Phase Q1): log OBI calculation for each signal but don't use it to veto. Creates validation dataset for Phase Q2 when L2 data is available.
**AEGIS Ref:** R-12, U-02 (shadow mode)

### G-09: Regime Flapping Protection (2h)
**File:** `feeds/regime_classifier.py`
**Problem:** No protection against rapid regime oscillation. 3+ regime changes in 10 minutes = undefined behavior.
**Fix:** If 3+ regime changes in 10 minutes → enter REGIME_FLAPPING state. REDUCE existing positions by 50% (CRO-06 — do NOT hold blindly on 3x leverage during extreme uncertainty). No new entries. Auto-clear after 30 min of stable regime with 0.50× ramp-up.
**AEGIS Ref:** §6D Regime Flapping Protection
**v3.1 Fix:** CRO-06 — "hold positions" replaced with "reduce 50%" during flapping.

### G-10: Post-Recovery Ramp-Up Sizing (1h)
**File:** `qualification/dynamic_sizer.py`, `feeds/regime_classifier.py`
**Problem:** After regime recovery (RISK_OFF → NORMAL), system immediately trades at full size. Risk of whipsaw.
**Fix:** RISK_OFF → NORMAL: 0.50× for 15 min (CQ-06 — reduced from 30 min as regime transitions can be high-alpha, but still ramp to avoid whipsaw on 3x leverage). SHOCK → NORMAL: 0.25× for 30 min. REGIME_FLAPPING → NORMAL: 0.50× for 15 min.
**AEGIS Ref:** §6D Post-Recovery Ramp-Up
**v3.1 Fix:** CQ-06 — ramp timers shortened but kept (whipsaw on 3x outweighs missed alpha).

### G-11: Regime Stuck Detection (0.5h)
**File:** `feeds/regime_classifier.py`
**Problem:** If regime is stuck on same state for >24h of market time, something is broken (data feed, classifier bug).
**Fix:** Track regime duration. If same regime >24h market time → P1 alert for manual review.
**AEGIS Ref:** §6D Regime Stuck Detection

### ~~G-12: Drought State Machine~~ — KILLED (v3.1)
**KILLED by AR-01.** Decaying quality thresholds because no signals have fired violates the foundational premise of algorithmic edge. If there are no signals, the market is offering no edge. Lowering standards = forced trading = negative EV. Time elapsed does not increase expected value.
**Replacement:** G-13 (Drought-Regime Contradiction Detection) serves as the diagnostic tool — it alerts when drought + trending regime indicates a system bug (data feed or gates too tight), rather than lowering standards to force trades.

### G-13: Drought-Regime Contradiction Detection (1h)
**File:** `core/drought_monitor.py`
**Problem:** TRENDING + drought = something is wrong (data feed or gates too tight). No detection mechanism.
**Fix:** Cross-check drought state vs regime. TRENDING + drought → P1 alert "data feed or gate issue". EXPANSION + drought → "gates too tight". RANGE + drought → expected. SHOCK + no drought → "counter bug".
**AEGIS Ref:** §6D Drought-Regime Contradiction Detection

### G-14: Session-End Exit Protocol — Aggressive LIMIT then MOC Auction (1.5h)
**File:** `execution/virtual_trader.py`
**Problem:** EOD exit may use limit orders which may not fill. Position left overnight on 3x ETP = catastrophic.
**Fix:** DO NOT send raw MARKET orders at 16:25 — MMs pull liquidity before the 16:30 auction, causing extreme slippage (CRO-01). Instead: (1) At 16:25: submit aggressive LIMIT orders pegged to Ask (for sells) or Bid (for buys), refreshing price every 10s. (2) At 16:28: if still unfilled, submit explicit MOC (Market-On-Close) order to participate in the official uncrossing auction. (3) At 16:31: if ANY position still open, trigger P0 incident + SNS phone call to operator (ties into F-09 fallback).
**AEGIS Ref:** R-05 session-end exit protocol
**v3.1 Fix:** CRO-01 — raw MARKET replaced with aggressive LIMIT → MOC auction protocol.

### G-15: Broker-Side Bracket Orders + Dynamic Updates (2.5h)
**File:** `execution/virtual_trader.py`, `execution/ibkr_gateway.py`
**Problem:** If entire system crashes, no protection exists. Stops are software-only.
**Fix:** On position open, place OCA (One-Cancels-All) bracket order at broker: stop-loss at 1.5× ATR, take-profit at rung 3 (+4%). These survive total system failure. CRITICAL: software must continuously update the broker-side bracket order every time the Chandelier rung advances (CRO-07). ATR changes over time — a static broker stop becomes dangerously wide compared to the software Chandelier stop if not updated.
**AEGIS Ref:** R21-25 (P1-12)
**v3.1 Fix:** CRO-07 — broker bracket dynamically updated on each rung advance.

### G-16: Spread Veto — Time-of-Day + VIX Normalised (1h)
**File:** `strategies/daily_target.py`
**Problem:** Current spread veto uses absolute or raw median. Doesn't account for time-of-day spread patterns OR VIX regime.
**Fix:** Normalise spread comparison by time-of-day bucket (AM open wide, midday narrow, PM overlap). Apply VIX multiplier: VIX < 15 → 1.0×, VIX 15-25 → 1.3×, VIX 25-35 → 1.6×, VIX > 35 → 2.0× (CQ-09 — spread distribution is not stationary, MM behavior changes dynamically with VIX). Veto if current > 2.5× time-and-VIX-normalised median.
**AEGIS Ref:** R-11
**v3.1 Fix:** CQ-09 — added VIX multiplier to spread normalization.

---

## PHASE H — OPERATIONAL INFRASTRUCTURE (14 items, ~32h)

**Startup gates, monitoring, alerts, deployment hardening. Build after core system works.**

### H-01: Startup Readiness Gate — 8 Pre-Flight Checks (3h)
**File:** NEW: `core/startup_gate.py`, integrate in `main.py`
**Problem:** System starts trading immediately regardless of subsystem health.
**Fix:** 8 checks before any trading: (1) DB connectivity, (2) Redis + Chandelier state loaded, (3) Data feed fresh <5 min — BUT bypass freshness check outside market hours 06:00-22:00 UK (SA-10: container rebooting at 03:00 would deadlock on legitimately dead feeds), (4) Kill switch OFF (requires human confirmation per F-04), (5) Circuit breaker GREEN/YELLOW, (6) Disk >20% free, (7) Memory >500MB free, (8) Time sync <5s drift. Three-tier output: READY / DEGRADED / HALTED.
**AEGIS Ref:** §8B Startup Readiness Gate
**v3.1 Fix:** SA-10 — feed freshness bypassed outside market hours.

### H-02: InvariantEnforcer Module (6h)
**File:** NEW: `core/invariant_enforcer.py`
**Problem:** 12 runtime invariants defined but no centralized enforcement.
**Fix:** Run ALL 12 invariants at boot (fail = `sys.exit(1)`). Run invariants 2-12 every 60s during market hours. On ANY failure: trigger L3 flatten + alert + halt. Expose `/api/invariants` endpoint.
**AEGIS Ref:** RI-02 (P1-48), §2H.2

### H-03: IMAGE_PARITY Deploy Gate (2h)
**File:** `main.py` (global init), `Dockerfile`
**Problem:** Stale container deploys. Code updated but Docker image not rebuilt.
**Fix:** Inject git SHA as Docker build ARG (`ARG GIT_SHA` in Dockerfile, `docker build --build-arg GIT_SHA=$(git rev-parse HEAD)`) and write to a flat file `/app/.git_sha` inside the image (SA-05 — do NOT copy .git folder into container, it bloats the image and exposes source control history). At boot, verify `cat /app/.git_sha == expected_sha`. On mismatch: `sys.exit(1)` with log `BOOT_PARITY_MISMATCH`. Must be FIRST check before any trading logic.
**AEGIS Ref:** RI-01 (P0-37), Runtime Invariant #1
**v3.1 Fix:** SA-05 — SHA injected via Docker ARG, not runtime git command.

### H-04: Tiered Telegram Notification Architecture (4h)
**File:** NEW: `delivery/telegram_notifier.py` (extend existing)
**Problem:** No structured alert tiers. Critical alerts mixed with routine notifications.
**Fix:** P0: Instant + SOUND (drawdown >L2, crash, cascade halt). P1: Instant, silent (trade fill, stop hit, regime change). P2: 30-min batch (signal generated, graduation). P3: 2× daily digest (ML health, macro summary). Correlation escalation: 3+ P1 in 15 min → auto-escalate to P0.
**AEGIS Ref:** §8 Notification Architecture

### H-05: Notification Fallback Defence-in-Depth (2h)
**File:** `delivery/telegram_notifier.py`
**Problem:** If Telegram delivery fails, alerts are silently lost.
**Fix:** P0 alerts: Telegram AND email (AWS SES). If Telegram fails, auto-escalate to SMS (AWS SNS) within 30s. P1 burst protection: >5 P1 in 60s → consolidate into single summary. Log all delivery failures as P1 incidents.
**AEGIS Ref:** §8 Notification Fallback

### H-06: Broker Failure Protocol (2h)
**File:** `execution/virtual_trader.py`, `execution/ibkr_gateway.py`
**Problem:** No defined behavior when broker connection fails mid-session.
**Fix:** No ack within 30s → retry with exponential backoff. No ack within 60s → DEGRADED mode (no new entries, monitor only). Open positions rely on broker-side bracket orders (G-15). CRITICAL: on connection recovery, FIRST ACTION must be to cancel all resting orders before resuming state evaluation (CRO-10 — resting limit orders may have executed while blind, causing position state divergence). Log all connectivity failures with timestamp and portfolio state.
**AEGIS Ref:** §8 Broker Failure Protocol
**v3.1 Fix:** CRO-10 — cancel all resting orders as first reconnection action.

### H-07: IB Gateway Reconnection Loop — GQ-01 (2h)
**File:** `data_hub/sources/ibkr_source.py`
**Problem:** When IB Gateway disconnects (daily restart, Sunday re-auth), no active reconnection. System silently degrades to yfinance.
**Fix:** Background reconnection loop: when `IS_AVAILABLE==False`, attempt `ib.connectAsync()` every 5s for up to 10 min. Log each attempt. If reconnect succeeds, re-subscribe to market data. If 3 consecutive connection attempts fail AND the IB Gateway container appears hung (SA-04), issue Docker socket command to restart the ib-gateway container (`docker restart ib-gateway`). If 10 min total elapse without reconnect, send Telegram alert, remain on yfinance fallback, set DEGRADED state.
**AEGIS Ref:** GQ-01 (P1-57)
**v3.1 Fix:** SA-04 — container restart after 3 failed reconnects.

### H-08: Monday Pre-Market Go-NoGo — GQ-02 (1h)
**File:** `main.py` scheduler
**Problem:** Monday morning requires 2FA approval for IB Gateway. If missed, system trades on stale yfinance data.
**Fix:** At 07:50 UK every trading day: if `not ib.isConnected()`, fire Telegram alert "IBKR DISCONNECTED — 2FA REQUIRED". If still not connected by 08:00 UK, set `nzt:halt_reason=IBKR_DISCONNECTED` in Redis and HALT all trading. No yfinance gap trading.
**AEGIS Ref:** GQ-02 (P1-58)

### H-09: SQLite Durable Write Queue via Redis (4h)
**File:** NEW: `core/db_writer.py` or extend `database.py`
**Problem:** With multiple simultaneous trades, concurrent SQLite writes cause `database is locked` errors.
**Fix:** DO NOT use `asyncio.Queue` as the write broker — if the Python process crashes (OOM, segfault), all pending TRADE and EMERGENCY writes sitting in RAM are permanently lost (SA-01). Instead: use Redis Lists (LPUSH/BRPOP) as a durable write queue. ALL writes serialized to Redis first (survives process crash), then a dedicated writer coroutine BRPOPs and writes to SQLite sequentially. Priority levels encoded in key names: `nzt:dbq:emergency`, `nzt:dbq:trade`, `nzt:dbq:telemetry` — writer polls emergency first. WAL mode enabled. Queue depth monitoring: >50 pending → P1 alert. This provides crash-durable write ordering without requiring PostgreSQL migration yet.
**AEGIS Ref:** I-07B
**v3.1 Fix:** SA-01 — in-memory asyncio.Queue replaced with Redis Lists for crash durability.

### H-10: CloudWatch Monitoring (4h)
**File:** EC2 setup, `main.py` metrics emission
**Problem:** No external monitoring. System can silently die.
**Fix:** CloudWatch agent on EC2. Metrics: CPU, memory, disk, scan loop health, positions open, daily P&L. Alarms: CPU >80% for 5 min, memory >90%, scan loop missed 3 consecutive cycles, no heartbeat for 10 min.
**AEGIS Ref:** I-06

### H-11: Daily Operational Checklists — Automated (1h)
**File:** `main.py` scheduler, `delivery/telegram_notifier.py`
**Problem:** Morning/midday/evening checks are manual.
**Fix:** Automated Telegram summaries: Morning (07:45 UK): container health, overnight errors, data feed status, startup gate result. Midday (12:00 UK): open positions P&L, scan_health.json, CB status, drought state. Evening (17:00 UK): daily P&L, alerts, backup status, tomorrow's calendar.
**AEGIS Ref:** §8C Daily Operational Procedures

### H-12: S3 Backup Automation (0.5h)
**File:** `scripts/backup_to_s3.sh`, EC2 cron
**Problem:** S3 backup exists but may not be scheduled as cron.
**Fix:** Verify cron job runs daily at 22:00 UK. Backs up SQLite DB, outcomes.jsonl, Redis AOF. Verify restoration works.
**AEGIS Ref:** I-02

### H-13: Redis Memory Policy — Separate Databases (0.5h)
**File:** Redis config, `docker-compose.yml`, all Redis key references
**Problem:** Redis could OOM during telemetry flood, killing position state.
**Fix:** DO NOT use `allkeys-lru` with NOEJECT tags — Redis LRU ignores NOEJECT and evicts based on access time only. Critical CB state will be evicted if telemetry floods the cache (SA-07). Instead: use two separate Redis databases: DB 0 for critical state (positions, Chandelier, circuit breakers, kill switch) with `noeviction` policy. DB 1 for telemetry/metrics with `allkeys-lru`. Update all Redis key references to use appropriate DB.
**AEGIS Ref:** Strengthened Defense #12
**v3.1 Fix:** SA-07 — separate Redis DBs replace unreliable NOEJECT tagging.

### H-14: Weekly Performance Report Generator (2h)
**File:** NEW: `delivery/weekly_report.py`
**Problem:** No automated performance tracking.
**Fix:** Every Sunday 20:00 UK: compute weekly WR, PF, Sharpe, max drawdown, trades by ticker, trades by regime, gate rejection audit. Send via Telegram. Store in `data/weekly_reports/`.
**AEGIS Ref:** §8C, Phase 3 intelligence

---

## PHASE I — UNIVERSE EXPANSION + DISCOVERY (10 items, ~44h)

**Expand from 12 hardcoded ETPs to 60+ with quality filters. Build Apex Scout.**

### I-01: Amihud Capacity Sieve (4h)
**File:** NEW: `uk_isa/amihud_sieve.py`
**Problem:** No market impact check. Large positions on illiquid ETPs cause excessive slippage.
**Fix:** `ILLIQ_i = mean(|r_t| / GBPVolume_t) × L^1.5` for trailing 20 days where `GBPVolume_t = Shares_t × Close_t` (CQ-03 — denominator MUST be GBP volume, not raw share count. Comparing price returns to raw shares across ETPs with vastly different absolute prices (£5 vs £500) breaks the metric). PASS if `(heat_size × ILLIQ_i) < 0.005` (<50 bps market impact). Time-of-day volume adjustment: 09:00-10:00 UK 1.6×, 10:00-12:00 1.0×, 12:00-14:00 0.7×, 14:30-15:30 1.8×.
**AEGIS Ref:** §1 Amihud Capacity Sieve, §11.4
**v3.1 Fix:** CQ-03 — raw share volume replaced with GBP volume in Amihud denominator.

### I-02: ASER Filter in LSE Registry (2h)
**File:** `uk_isa/lse_registry.py`
**Problem:** No ADR-to-Spread Efficiency Ratio filter. Some ETPs have wide spreads relative to their daily range — cost-prohibitive.
**Fix:** Extend registry with ASER column. ASER = `(ADR% / Spread%)` where both are expressed as percentages of close price (CQ-04 — dividing absolute £ ADR by bps spread yields nonsense units). Specifically: `ADR% = ADR / Close × 100`, `Spread% = median_spread_bps / 100`. Require ASER > 10 for CORE tier inclusion. ETPs below threshold relegated to intelligence-only.
**AEGIS Ref:** §1 ASER Filter
**v3.1 Fix:** CQ-04 — standardized both ADR and spread to percentage units.

### I-03: DSR Graduation Gate + PSR Confirmation (3h)
**File:** NEW: `uk_isa/dsr_gate.py` or extend `qualification/dynamic_sizer.py`
**Problem:** No statistical significance test for ticker-level edge. Bayesian Stranger Penalty reduces size but never fully graduates.
**Fix:** Bailey & Lopez de Prado (2014) DSR: t-stat >= 3.0 required for full Kelly graduation. Additionally, Probabilistic Sortino Ratio (PSR) must confirm edge with asymmetric return adjustment (Synthesis consensus — DSR/Sharpe assumes normal returns, leveraged momentum has massive positive skew and heavy tails). BOTH gates must pass: DSR t-stat >= 3.0 AND PSR > 0.95. Below threshold: Bayesian penalty applies (0.25× to 1.0× scaling).
**AEGIS Ref:** §1 DSR Graduation Gate, §11.1
**v3.1 Fix:** Synthesis — added PSR as secondary confirmation for non-normal return distributions.

### I-04: LSE Registry — Real Web Scrape for New Listings (3h)
**File:** `uk_isa/lse_registry.py`
**Problem:** 46-product `_SEED_CATALOG` is hardcoded (lines 44-102). No new listing discovery. `new_listings` counter always 0.
**Fix:** Add actual LSE web scrape for leveraged/inverse ETPs. Run daily at 06:00 UK. New listings get Amihud + ASER filter before entering CORE tier. Alert on new listings discovered.
**AEGIS Ref:** §1 current state ("Needs: ASER column, actual LSE scrape for new listing detection")

### I-05: Stamp Duty Verification Registry (1h)
**File:** `uk_isa/isa_universe.py` (TICKER_REGISTRY)
**Problem:** No stamp duty verification. Some ETPs might not be stamp-duty-exempt.
**Fix:** Add `stamp_duty_exempt: bool` to TICKER_REGISTRY metadata. Any ETP with uncertain status excluded until verified. All current CORE ETPs are exchange-traded products (generally exempt) — verify and document.
**AEGIS Ref:** §1 Regulatory Risk, §0.1 Stamp Duty Verification

### I-06: Apex Scout Module (12h)
**File:** NEW: `strategies/apex_scout.py`
**Problem:** No discovery mechanism for RVOL anomalies outside the core 12 ETPs.
**Fix:** Asynchronous scanner: PEER tier every 5 min, FULL_SCAN tier every 30 min. Regime-adaptive RVOL Z-threshold (TRENDING=2.0, RANGE=3.0, RISK_OFF=3.5, SHOCK=disabled). All Scout signals carry Bayesian Stranger Penalty. LSE Priority Mapping: when Scout detects anomaly on underlying (e.g., NVIDIA), check `lse_mapper.get_etp_equivalent()` → NVD3.L.
**AEGIS Ref:** §3 The Apex Scout (entire section)

### I-07: Apex Scout Data Cost Control (2h)
**File:** `strategies/apex_scout.py`
**Problem:** Scanning 500 tickers every 30 min is expensive.
**Fix:** Sunday night: refresh FULL_SCAN universe, compute 20-day RVOL + ADR, filter to top candidates. Daily 06:00 UK: quick delta refresh. During market hours: IBKR real-time quotes for CORE (60s), PEER (5 min), FULL_SCAN (30 min). yfinance batch download as fallback only.
**AEGIS Ref:** §3 Data Cost Control

### I-08: Full Universe Scanning — 60+ ETP CORE Tier (4h)
**File:** `main.py`, `uk_isa/universe_manager.py`
**Problem:** S15 scans only 12 hardcoded ISA ETPs. Universe manager exists but isn't wired into S15.
**Fix:** Wire `universe_manager.get_tier("CORE")` into S15's scan loop. S15 scans entire CORE tier (60+ ETPs) every cycle. Secondary strategies can fire alongside S15.
**AEGIS Ref:** §2 E-02 Full Universe Scanning

### I-09: Pre-Market Intelligence Scan — T-09 (3h)
**File:** `strategies/daily_target.py` or new module
**Problem:** No pre-market intelligence. System is blind to overnight futures moves.
**Fix:** At 07:30 UK: scan overnight US futures (NQ, ES, RTY) for significant moves (>0.5%). If strong directional move, pre-load confidence bias for related ETPs. Feed into T-01 gap scan readiness.
**AEGIS Ref:** T-09 (P1-19)

### I-10: Gap-to-Range Filter for Overnight Gaps (1h)
**File:** `strategies/daily_target.py`
**Problem:** O2C velocity ranking ignores overnight gaps. A ticker with 80% of its ADR consumed by overnight gap has no intraday range left.
**Fix:** If median overnight gap > 50% of ADR, penalise -15 confidence. Prevents buying into exhausted gap moves.
**AEGIS Ref:** A-04 plan flaw

---

## PHASE J — ML REHABILITATION (8 items, ~20h)

**Everything needed to safely re-enable ML after 500+ trades. DO NOT START until N > 500.**

### J-01: ML Feature Leakage Fix (2h)
**File:** `core/ml_meta_model.py:74`
**Problem:** "confidence" at index 4 in `feature_cols` creates circular feedback: confidence → model input → ml_prob → blended output → next cycle's confidence input.
**Fix:** Remove "confidence" from feature_cols. Add replacement features: `raw_indicator_count`, `spread_bps`, `time_since_regime_change_hours`. Update `_extract_row()` and indicator pipeline.
**AEGIS Ref:** R21-30 (P1-14), M-01

### J-02: ML Regime Map Fix (0.5h)
**File:** `core/ml_meta_model.py:48`
**Problem:** `_REGIME_MAP` has keys `{"bull": 0, "bear": 1, ...}` but `RegimeState` enum has `TRENDING_UP_STRONG`, etc. Every regime encodes as -1.
**Fix:** Replace keys with actual RegimeState values: `{"trending_up_strong": 0, "trending_up_mod": 1, ...}`.
**AEGIS Ref:** R21-11 (P1-6), M-02

### J-03: Walk-Forward Validation with Purge/Embargo (6h)
**File:** `core/ml_meta_model.py:287-288`
**Problem:** `StratifiedKFold(n_splits=5, shuffle=True)` — `shuffle=True` on time-series is worst-case temporal leakage.
**Fix:** Replace with expanding-window walk-forward with 5-day purge + 5-day embargo. CPCV (Purged Combinatorial Cross-Validation) per de Prado 2018. max_depth=2 on all LightGBM trees.
**AEGIS Ref:** AR-03 (P0-22), M-04, RK-03 (P1-52)

### J-04: ML Bypass Enforcement (1h)
**File:** `core/ml_meta_model.py`, `main.py`
**Problem:** ML bypass during paper not formally enforced. Silent `try/except` means ML failures are invisible.
**Fix:** N < 200: ML DISABLED entirely. N < 500: Pure LogReg fallback (5 PCA features). N >= 500 AND DSR > 1.0: full ML ensemble active. Hard invariant — checked at boot.
**AEGIS Ref:** R21-32 (P1-15), M-05

### J-05: Entry Timing Feedback Loop — M-06 (3h)
**File:** `learning/trade_autopsy.py`
**Problem:** No measurement of whether timing fixes actually worked.
**Fix:** For every trade, compute Entry Timing Score: `(daily_high - entry) / (daily_high - daily_low)` for LONG. Target: < 0.50. 100-trade gate: if median ETS >= 0.50, timing fixes have NOT worked. Log Missed Alpha: for every ticker moving >2% NOT traded, record which gate rejected it and EOD performance.
**AEGIS Ref:** M-06

### J-06: Weekly Gate Rejection Audit (2h)
**File:** `learning/gate_auditor.py` (new) or extend `learning/trade_autopsy.py`
**Problem:** No way to know if gates are too tight or too loose.
**Fix:** If a gate rejects >30% of signals AND >50% of rejected signals would have been profitable, flag for threshold adjustment. Weekly automated audit report.
**AEGIS Ref:** M-06 (Weekly Gate Rejection Audit)

### J-07: Optimal Entry Delay Model — After 200+ Trades (3h)
**File:** NEW: `core/entry_timing_model.py`
**Problem:** Fixed entry timing. No learning from past entry quality.
**Fix:** After 200+ trades: train simple model (gap_size, rvol_trajectory, sector_momentum, regime, time_of_day → optimal_delay_minutes). Use to adjust FAST/SLOW tier entry timing.
**AEGIS Ref:** M-06 (Optimal Entry Delay), T-11 (P1-20)

### J-08: Parameter Drift Monitor — Constitutional Bounds (2h)
**File:** `core/invariant_enforcer.py` or new module
**Problem:** No detection if ML-adjusted parameters drift beyond Constitutional bounds.
**Fix:** Parameter drift limit: +/-15% from baseline. If any parameter drifts beyond, enter DEFENSIVE mode: reduced sizing, P1 alert, mandatory review. Track drift continuously.
**AEGIS Ref:** §5B Constitutional Bounds on ML

---

## PHASE K — EXECUTION PHYSICS + MICROSTRUCTURE (20 items, ~150h)

**Phase Q2. ONLY after Phase Q1 validates signal edge (200-Trade Gate + 63-Day Gauntlet passed).**

### K-01: SyntheticBroker — Local Matching Engine (12h)
**File:** NEW: `testing/synthetic_broker.py`
**Fix:** LSE ETP matching engine with FIFO queue priority, adverse selection (35% default), partial fills. Ghost-Maker connects via same interface as real broker API.
**AEGIS Ref:** CR-01 (P0-24)

### K-02: AsyncioHeartbeat — GIL Freeze Detector (6h)
**File:** NEW: `core/asyncio_heartbeat.py`
**Fix:** 10ms heartbeat callback. If lag >50ms, trip brain circuit breaker. Track p50/p95/p99 latency.
**AEGIS Ref:** CR-02 (P0-25)

### K-03: ReconciliationAuditor — Full Module (8h)
**File:** NEW: `core/reconciliation_auditor.py`
**Fix:** Three-way reconciliation (broker vs Redis vs SQLite) every 5 min. On ANY mismatch: write kill switch to Redis BEFORE SIGKILL, issue MOC orders via broker API, kill engine. Restart guard checks kill switch at boot.
**AEGIS Ref:** CR-03 (P0-26)

### K-04: MicrostructureCalibrator — Walk-Forward IS Optimization (10h)
**File:** NEW: `core/microstructure_calibrator.py`
**Fix:** Walk-forward calibration of Tachyon SG window + Hawkes decay using Implementation Shortfall (slippage vs arrival price). DO NOT use Information Coefficient — IC (Spearman rank correlation) breaks down in high-frequency data due to bid-ask bounce and zero-return bars (AR-04). 20-day training, 5-day test, 2-day purge. Regime-conditioned.
**AEGIS Ref:** CR-04 (P0-27)
**v3.1 Fix:** AR-04 — IC replaced with Implementation Shortfall for HF calibration.

### K-05: PostgreSQL Migration (12h)
**File:** `database.py` → PostgreSQL, RDS or local
**Fix:** Replace SQLite single-writer with PostgreSQL WAL mode. Concurrent R/W for Brain, Auditor, Telemetry. `synchronous_commit=on` for TRADE writes, `=off` for TELEMETRY.
**AEGIS Ref:** GA-02 (P0-29)

### K-06: ProcessPoolExecutor Brain Isolation (8h)
**File:** `main.py`, new process architecture
**Fix:** Brain runs in separate OS process (eliminates GIL contention entirely). Communication via `multiprocessing.Queue` or ZeroMQ IPC. AsyncioHeartbeat becomes backup safety net.
**AEGIS Ref:** GA-03 (P0-30)

### K-07: Commission Audit + Capital Critical Mass Gate (2h)
**File:** `qualification/risk_sizer.py`, `config/settings.yaml`
**Fix:** Verify IBKR Tiered pricing (0.05%, £1.00 min). Enforce MAX_CONCURRENT=1 until equity > £25k.
**AEGIS Ref:** GA-04 (P0-31)

### K-08: Spread-Expansion Circuit Breaker (3h)
**File:** `core/reconciliation_auditor.py`, `execution/virtual_trader.py`
**Fix:** Forbid Market Orders when spread >50 bps. Use passive Limit pegged to Mid-Price. If spread >100 bps: HOLD CASH (CRO-09 — do NOT hedge via inverse ETPs during spread blowouts; if QQQ3.L spreads blow out, QQQS.L spreads will also blow out, paying 100 bps twice). Only hedge via underlying futures (NQ=F) if available and spread is normal there. If spread >200 bps: HALT state, no new orders.
**AEGIS Ref:** GA-05 (P0-32)
**v3.1 Fix:** CRO-09 — inverse ETP hedging during spread blowout removed (pays blown spreads twice).

### K-09: Token Bucket API Rate Limiter (2h)
**File:** NEW: `execution/rate_limiter.py`
**Fix:** 50 req/s, regen 10/s. >80% consumed: Ghost-Maker timeout 800ms → 3000ms. >90%: only emergency flatten. 100%: HALT + cancel all resting. Reserve 20% for emergency flatten.
**AEGIS Ref:** GA-06 (P1-33)

### K-10: Single-Writer Actor Model for Broker API (3h)
**File:** NEW: `execution/execution_dispatcher.py`
**Fix:** Only ONE coroutine talks to broker API. Priority queue: P0=EMERGENCY_FLATTEN, P1=TOXICITY_CANCEL, P2=HAWKES_EXIT, P3=TACHYON_ENTRY. TICKER_LOCKED state prevents conflicting commands.
**AEGIS Ref:** GA-07 (P1-34)

### K-11: SFV Arbitrage Engine (6h)
**File:** NEW: `core/sfv_engine.py`
**Fix:** Compute real-time fair value: `SFV = NQ_futures × leverage × (GBP/USD) - swap_accrual`. Fire IOC when SFV diverges from LSE Ask by >2 ticks.
**AEGIS Ref:** GA-08 (P1-35)

### K-12: Micro-Price OBI Calculation (3h)
**File:** NEW: extend `feeds/indicators.py`
**Fix:** Volume-weighted mid-price. If 10k on Bid, 100 on Ask, true price is near Ask. Requires L2 data.
**AEGIS Ref:** GA-09 (P1-36)

### K-13: TCP_NODELAY + TCP_QUICKACK on Broker Sockets (1h)
**File:** `execution/ibkr_gateway.py`, `data_hub/sources/ibkr_source.py`
**Fix:** Set socket options explicitly on all IBKR connections. Eliminates 10-40ms Nagle's algorithm latency.
**AEGIS Ref:** GA-10 (P1-37)

### K-14: Spoof Detection Radar (3h)
**File:** NEW: `core/spoof_detector.py`
**Fix:** Track order cancellation rates. If order >5× avg book size appears and disappears <500ms: tag as "SPOOFED", halt execution 3s.
**AEGIS Ref:** GA-11 (P1-38)

### K-15: Variance Drag Kill-Switch (1h)
**File:** `qualification/risk_sizer.py`
**Fix:** If 5d ATR < 20d ATR (sideways market), ban 3x/5x ETP trading. Cash or intelligence only.
**AEGIS Ref:** GA-12 (P1-39)

### K-16: GBP/USD Flash Scrub for SFV (0.5h)
**File:** `core/sfv_engine.py`
**Fix:** If cable moves >0.25% in 60s, disable SFV arbitrage entirely. Currency noise, not genuine edge.
**AEGIS Ref:** GA-13 (P1-40)

### K-17: Weekly Genetic Optimization (8h)
**File:** NEW: `scripts/weekly_optimize.py` (AWS Lambda or EC2 cron)
**Fix:** Every Sunday 22:00 UK: download week's tick data, run walk-forward genetic optimization on indicator params, push to Redis. Engine loads pre-optimized weights Monday 08:00. DO NOT run nightly — nightly optimization guarantees parameter overfitting to the most recent noise regime (CQ-05). Enforce max parameter drift of 10% per epoch to prevent regime-whipsaw overfitting.
**AEGIS Ref:** GA-14 (P1-41)
**v3.1 Fix:** CQ-05 — nightly → weekly cadence + 10% drift cap to prevent overfitting.

### K-18: 5 Chaos Drills (20h)
**File:** `testing/chaos_drills/`
**Fix:** CD-01: Pandas Fat Finger (inject 200ms GIL block). CD-02: Toxic Tsunami (90% adverse selection). CD-03: Phantom Fill (dropped fill message). CD-04: Adverse Selection Sniper (30bps adverse per fill). CD-05: Redis Lobotomy (kill Redis mid-trade). ALL must pass 3× consecutive before 63-Day Gauntlet.
**AEGIS Ref:** CR-05 through CR-09 (P1-28 to P1-32)

### K-19: ib_insync Full Async Mode — Single Event Loop (4h)
**File:** `execution/ibkr_gateway.py`, `data_hub/sources/ibkr_source.py`
**Fix:** Replace all `ib.sleep()` with `await asyncio.sleep()`. Use a SINGLE event loop (SA-03 — ib_insync is strictly designed for single-loop execution; attempting separate event loops for data vs execution causes cross-loop deadlocks). Use separate client_ids (10 for data, 2 for execution) on the SAME loop. Use `ib.reqMktData()` asynchronously. Assert single loop in every callback.
**AEGIS Ref:** AB-03 (P1-45)
**v3.1 Fix:** SA-03 — dual event loop replaced with single loop (ib_insync requirement).

### K-20: Bare Metal / Dedicated Host Migration (8h)
**File:** EC2 infrastructure
**Fix:** Migrate from shared tenancy to c7i-flex.large dedicated (or c7g.medium in eu-west-2 London). Eliminates CPU steal-time, hypervisor jitter.
**AEGIS Ref:** GA-15 (P1-42)

---

## PHASE L — QUANTUM APEX END-STATE (9 items, ~1,204h)

**Phase Q3-Q4. The theoretical end-state. ONLY after ALL prior phases pass AND equity > £50k. Most of this is R&D that may never be built.**

### L-01: Rust FFI Execution Muscle — PyO3 (280h)
**Fix:** Rewrite ExecutionMuscle in Rust. PyO3 FFI bridge. <10μs signal-to-wire. GIL-free order lifecycle.
**AEGIS Ref:** M-01 (v16.0)

### L-02: DQN Ghost-Maker — Deep Q-Network Execution Agent (180h)
**Fix:** 21 discrete actions (bid-5 to ask+5, cancel, market cross). Reward = -implementation shortfall. Replaces static peg logic.
**AEGIS Ref:** M-02 (v16.0)

### L-03: Neural Hawkes Exit Engine — LSTM (160h)
**Fix:** LSTM models event intensity λ(t) for 4 event types. Replaces fixed Chandelier rungs. Exit thresholds: P_exit > 0.85 → IMMEDIATE, >0.60 → TIGHTEN_STOP, >0.40 → TIGHTEN_TRAIL.
**AEGIS Ref:** M-03 (v16.0)

### L-04: Cross-Impact OFI Signal Generator (120h)
**Fix:** Order Flow Imbalance from NQ=F, ES=F, DX=F predicts LSE ETP movement before MM reprices. 50-500ms information gap. Rolling 5-day OLS with Ledoit-Wolf shrinkage.
**AEGIS Ref:** M-04 (v16.0)

### L-05: LMAX Lock-Free Ring Buffer IPC (80h)
**Fix:** POSIX shared memory SPSC. 65,536 slots × 64 bytes = 4MB on `/dev/shm`. Lamport (1983) protocol. <200ns transit.
**AEGIS Ref:** M-05 (v16.0)

### L-06: Runtime Invariants 13-16 for Quantum Modules (8h)
**Fix:** QA-01: RUST_FFI_HEARTBEAT (500μs). QA-02: DQN_ACTION_BOUND (no exploration). QA-03: FIX_DROP_COPY_RECONCILE (tick-level). QA-04: FRACDIFF_STATIONARITY_GATE (ADF p<0.05).
**AEGIS Ref:** QA-01 through QA-04 (P1-53 to P1-56)

### L-07: End-State Infrastructure (384h)
**Fix:** Bare metal c7g.metal (64 cores, 128GB). DPDK kernel bypass (<3μs). IEEE 1588 PTP (<1μs clock). TimescaleDB replacing SQLite. systemd replacing Docker. CPU core pinning.
**AEGIS Ref:** §2J.4 v16.0 End-State Infrastructure

### L-08: Fractional Differentiation on ML Features (20h)
**Fix:** Per-feature walk-forward d-selection over [0.10, 0.90]. ADF test + correlation preservation. Typical d ~ 0.35-0.55. Recalibrated nightly.
**AEGIS Ref:** §2J.2 (v16.0), QA-04

### L-09: CI/CD Pipeline — GitHub Actions (12h)
**Fix:** Automated: lint → test → build → deploy to EC2. Pre-commit hooks for invariant tests. Blue-green deployment with rollback.
**AEGIS Ref:** I-09

---

## LIMITED LIVE TRANSITION PROTOCOL

**After 63-Day Paper Gauntlet passes. Three-stage transition to real capital.**

| Parameter | Limited Live | Expanded Limited | Full Live |
|-----------|-------------|-----------------|-----------|
| Max capital | £1,000 | £3,000 | £10,000 |
| Max positions | 1 | 2 | 4 |
| Strategy | S15 only | S15 only | All |
| Order type | LIMIT only | LIMIT only | Market (10K), Limit (50K+) |
| Human confirmation | Yes (every trade) | Yes (every trade) | Fully automated |
| Duration | Min 2 weeks (10 MTRL days) | Min 2 weeks (10 MTRL days) | Ongoing |

**Expanded Limited Live** tests multi-position mechanics. The 1→4 position jump is a phase-transition risk.
**Human Confirmation**: Log ALL signals — confirmed and rejected. Track hypothetical P&L of rejected signals. If rejected signals profitable >50% of the time, human is degrading performance.

---

## GO-LIVE GATE (Full Criteria)

| Criterion | Threshold |
|-----------|-----------|
| DSR + PSR | DSR t-stat >= 3.0 (HLZ 2016) AND PSR > 0.95 (v3.1) |
| Win Rate (S15) | >= 40% on 200+ trades |
| Profit Factor | > 1.2 |
| Sharpe (annualised) | > 1.0 |
| Max Daily Drawdown | < -4% |
| Max Portfolio Drawdown | < -8% at £10K tier (v3.1, was -12%) |
| System Uptime | > 99.5% over 30 days |
| P0 Fixes | All verified |
| CDaR_95 | Never > 5% |
| Paper Duration | 63 MTRL days minimum |
| Dropped P0 Signals | 0 |
| ISA Compliance | 100% (0 non-ISA trades) |
| False Flatten Events | 0 |
| Market Data Feed | Real-time IBKR (NOT yfinance) |
| Entry Timing Score | Median < 0.50 across 100+ trades |
| Signal-to-Order Latency | FAST < 500ms, SLOW < 3s |
| Gate Rejection FP Rate | < 30% of rejected signals profitable |
| SQLite Write Queue | 0 lock errors in 30 days |
| Regime Coverage | >= 5 days HIGH_VOL + >= 2 days RISK_OFF |
| Stamp Duty Verification | All CORE ETPs verified exempt |
| Operator Override Rate | < 20% rejection during Limited Live |
| Alpha Decay Monitor | Monthly Sharpe decline < 0.3 over 6 months |
| CB State Persistence | Halts survive Docker restart (verified) |
| At Least 10 Unique Tickers | Traded during validation period |

---

## LIQUIDITY SCALING FRAMEWORK

**From AEGIS §7 — Kyle's Lambda market impact model.**

```
Delta_P ~ lambda × sqrt(Q / V_daily)
lambda = 0.02 (conservative for leveraged ETPs)
```

| Equity | Heat (3%) | Impact | Verdict |
|--------|-----------|--------|---------|
| £10K | £300 | <0.1 bps | SAFE — market orders OK |
| £50K | £1,500 | ~0.7 bps | SAFE — market orders OK |
| £100K | £3,000 | ~0.9 bps | SAFE — limit orders preferred |
| £500K | £15,000 | ~2.1 bps | CAUTION — TWAP/VWAP mandatory |
| £1M | £30,000 | ~2.9 bps | CAUTION — expand universe |
| £3M+ | £90,000 | ~5.0 bps | DANGER — migrate to futures |

---

## v3.1 COMPLETE ITEM COUNT SUMMARY

| Phase | Items | Hours | Focus | When |
|-------|-------|-------|-------|------|
| 0: Data Integrity + Telemetry | 10 | 14h | Verify data, fix indicators, build telemetry | Week 1 |
| A: Safety Systems | 14 | 22h | Fix ALL safety + state journal | Weeks 2-3 |
| B: Silent Killers + Timing | 12 | 16h | Remove signal blockages | Week 3 |
| V: VALIDATION GATE | — | ~3-4 weeks | 200-trade baseline (WR > 33%) | Weeks 4-7 |
| C: Core Edge | 12 | 26h | Win rate features | Weeks 8-9 |
| D: ETP Intelligence | 8 | 16h | Leveraged ETP mechanics | Weeks 9-10 |
| E: Robustness | 3 | 5h | Blood Oath #1 + hardening | Week 10 |
| **F: Quick Wins & Bug Fixes** | **18** | **~14h** | **Tiny fixes, huge risk reduction** | **Alongside 0-A** |
| **G: Risk Architecture** | **15** | **~27h** | **15-control defence matrix (G-12 KILLED)** | **Alongside C-E** |
| **H: Operational Infrastructure** | **14** | **~33h** | **Startup gate, monitoring, alerts** | **Post-validation** |
| **I: Universe Expansion** | **10** | **44h** | **Amihud, ASER, DSR, Apex Scout** | **Post-validation** |
| **J: ML Rehabilitation** | **8** | **20h** | **Fix ML before re-enabling** | **After 500+ trades** |
| **K: Execution Physics** | **20** | **~148h** | **Phase Q2 microstructure** | **After 63-day gauntlet** |
| **L: Quantum Apex** | **9** | **1,204h** | **Phase Q3-Q4 end-state** | **After equity > £50k** |
| **TOTAL** | **153** | **~1,589h** | | |
| **Phase Q1 (actionable now)** | **92** | **~236h** | **0-E + F + G** | **First ~3 months** |
| **Phase Q2+ (after validation)** | **61** | **~1,353h** | **H-L** | **Months 4+** |
| **KILLED (v2.0)** | **10** | — | CDaR, iCVaR, PEAD, etc. | Never |
| **KILLED (v3.1)** | **1** | — | G-12 Drought State Machine (forced trading) | Never |

---

## v3.1 ADVERSARIAL REVIEW KILL LIST

Items removed by Gemini Institutional Syndicate v4 review:

| Item | Reason | Persona |
|------|--------|---------|
| G-12: Drought State Machine | Decaying quality thresholds = forced trading. Time elapsed does not increase EV. | AR-01 |
| F-04: Kill Switch AUTO-clear | Replaced with human confirmation. Machines must never auto-resume from P0 halt. | CRO-08 |
| F-16: MFE rung calibration | Look-ahead bias. MFE only known post-trade. Replaced with ex-ante ATR. | AR-02 |
| K-08: Inverse ETP hedge on spread blowout | Pays blown-out spreads twice. Replaced with cash/futures. | CRO-09 |

Note: F-04 and F-16 were REWRITTEN (not fully killed) — functionality replaced with safer alternatives. K-08 was REWRITTEN. Only G-12 was fully killed.

## v3.1 SINGLE POINTS OF FAILURE (SPOFs) — RESOLVED

| SPOF | Original Risk | Resolution |
|------|--------------|------------|
| In-Memory SQLite Queue (H-09) | Process crash = lost writes | Redis LPUSH/BRPOP durable broker (SA-01) |
| Risk Arbiter Choke Point (F-11) | Arbiter bug = all exits blocked | EMERGENCY_FLATTEN bypasses queue (CRO-03) |
| Redis LRU Eviction (H-13) | Telemetry flood evicts CB state | Separate Redis DBs: DB 0 state, DB 1 telemetry (SA-07) |
