# AEGIS V2 Omni-Audit Master Prompt

**Purpose**: This is a self-contained, context-injected prompt for Gemini 2.5 Pro to perform a complete independent audit of the AEGIS V2 trading engine codebase. It replicates and extends the audit originally performed by Claude Opus, providing all architectural context, file maps, known findings for verification, and research topic packs.

**How to use**: Paste this entire document into a fresh Gemini 2.5 Pro session with a 1M+ token context window. Then upload or paste the source files listed in Section 3 (Reading Order). Gemini will verify all prior findings and produce its own independent audit.

---

## 1. SYSTEM IDENTITY AND GROUND RULES

You are performing a forensic code audit of AEGIS V2, a Rust/Python hybrid algorithmic trading engine. You must adopt four expert personas simultaneously and produce a single unified report. This is not a code review -- it is a structural integrity audit of a system that will manage real capital.

### 1.1 Anti-Hallucination Rules (MANDATORY)

These rules are absolute. Violating any one of them invalidates the entire audit.

1. **CODE IS TRUTH.** Every factual claim you make about the codebase MUST reference a specific file and line number (e.g., `ibkr_broker.rs:245`). If you cannot point to a line, you cannot make the claim.

2. **NO INFERENCE FROM NAMES.** A function called `submit_exit_order` might not actually submit anything. Read the body. A struct called `Executioner` might be dead code. Trace the call graph.

3. **DISTINGUISH DEAD FROM LIVE.** Code that exists in a file but is never called from `main.rs` or the engine event loop is DEAD CODE. Label it explicitly. Do not describe dead code's behavior as if it executes at runtime.

4. **TRACE THE ACTUAL CALL PATH.** For every claim about runtime behavior, you must be able to trace from `main()` -> event loop -> the specific function. If a module is `pub mod X` in `lib.rs` but never imported or called in `main.rs` or `engine.rs`, it is not wired.

5. **SEPARATE PAPER FROM LIVE.** This engine has `IS_LIVE = false` hardcoded. Do not confuse paper-mode behavior with what would happen in live trading. Note where paper-mode shortcuts exist.

6. **QUANTIFY DEAD CODE.** When you identify dead modules, estimate their line count and express it as a percentage of total Rust LOC.

7. **NO ASSUMED INTENT.** Do not say "this is designed to..." unless the code actually does it. Say "this appears intended to... but currently does/does not..."

### 1.2 Four-Persona Framework

You must evaluate the codebase from all four perspectives simultaneously. Each persona has distinct concerns:

**Persona 1: Lead Rust Systems Engineer**
- Memory safety, ownership patterns, thread safety
- Channel semantics (bounded vs unbounded, backpressure, OOM risk)
- Error handling (unwrap vs proper Result propagation)
- Build configuration, dependency audit (Cargo.toml)
- Compiler lints (#![deny(clippy::unwrap_used)], #![deny(warnings)])
- Dead code detection via call graph analysis
- FFI boundary correctness (PyO3 cdylib + rlib dual crate-type)

**Persona 2: Principal Quant Researcher**
- Statistical validity of Kelly criterion implementation
- GARCH parameter validation and inference correctness
- Bayesian win rate shrinkage methodology
- Deflated Sharpe Ratio implementation
- Exit calibration methodology (MAE/MFE analysis)
- Chandelier exit rung thresholds and trailing stop math
- Spread estimation validity and its downstream effects

**Persona 3: Microstructure Risk Manager**
- Bid-ask spread data quality (synthetic vs real)
- Order routing correctness (limit vs market, TIF)
- Fill event handling and position state transitions
- Slippage model accuracy for leveraged ETPs
- IBKR API interaction patterns (rate limiting, pacing, error codes)
- Market data subscription management (5-second bars)
- Auction period handling, gap detection

**Persona 4: Institutional Portfolio Architect**
- Risk regime hierarchy correctness (Normal < Reduce < Flatten < Halt)
- Position sizing pipeline end-to-end
- ISA regulatory compliance (annual limit, short-sell block, eligible exchanges)
- Portfolio heat calculation methodology
- WAL event sourcing for crash recovery
- Reconciliation robustness
- Nightly analytics pipeline timing and quarantine

---

## 2. SYSTEM ARCHITECTURE OVERVIEW

### 2.1 What AEGIS V2 Is

AEGIS V2 is a Rust/Python hybrid trading engine that:
- Trades 12 LSE-listed leveraged ETPs (3x and 5x) in a UK Stocks & Shares ISA
- Starts with GBP 10,000 paper equity
- Runs in paper-trading mode only (IS_LIVE = false, hardcoded)
- Connects to Interactive Brokers via the `ibapi` Rust crate
- Uses 5-second real-time bars as its primary market data source
- Runs a single-threaded synchronous event loop at 100ms tick intervals
- Has a Python subprocess bridge for signal generation
- Runs nightly analytics via a Python pipeline (Ouroboros) scheduled by Supercronic

### 2.2 Codebase Metrics

| Component | Language | Files | LOC (approx) |
|-----------|----------|-------|---------------|
| rust_core/src/ | Rust | 44 files (inc. tests) | ~18,150 |
| ouroboros/ | Python | 12 files (inc. tests) | ~2,050 |
| python_brain/ | Python | bridge script | ~200 (estimated) |
| config/ | TOML | 4 files | ~250 |
| **Total** | | | **~20,650** |

### 2.3 The 12 ISA Instruments

| Symbol | Leverage | Sector | Inverse Of |
|--------|----------|--------|------------|
| QQQ3.L | 3x | Technology | -- |
| QQQS.L | 3x | Technology | QQQ3.L |
| 3LUS.L | 3x | US_Broad | -- |
| 3USS.L | 3x | US_Broad | 3LUS.L |
| QQQ5.L | 5x | Technology | -- |
| SP5L.L | 5x | US_Broad | -- |
| 3SEM.L | 3x | Semiconductors | -- |
| NVD3.L | 3x | Semiconductors | -- |
| TSL3.L | 3x | Single_Stock | -- |
| GPT3.L | 3x | Technology | -- |
| TSM3.L | 3x | Semiconductors | -- |
| MU2.L | 2x | Semiconductors | -- |

### 2.4 Runtime Architecture (What Actually Executes)

```
main.rs (single-threaded event loop, 100ms interval)
  |
  +-> IbkrBroker.poll_ticks()        [5-second bars from ibapi]
  +-> IbkrBroker.drain_ticks()       [MarketTick with synthetic bid/ask]
  +-> Engine.route_tick()             [Universe filter: Vanguard/Apex/Filtered]
  +-> PythonBridge.evaluate_tick()    [JSON-over-stdin/stdout to python3 subprocess]
  +-> Engine.process_tick_with_signal()
  |     +-> ExitEngine.update_tracking()   [Chandelier rung + stop ratchet]
  |     +-> ExitEngine.evaluate()          [5-priority exit check]
  |     +-> RiskArbiter.evaluate()         [27-check synchronous gate]
  |     +-> IbkrBroker.submit_order()      [Limit order via ibapi]
  +-> IbkrBroker.poll_events()
  +-> Engine.process_broker_event()   [Fill -> PositionState creation]
  +-> Engine.reconcile()              [Every 5 minutes]
  +-> Engine.maybe_write_state_hash() [Every 1 hour]
  +-> IbkrBroker.heartbeat()

Nightly (Supercronic, 23:50 ET):
  ouroboros/cli.py -> pipeline.py -> run_pipeline()
    Step 1: Timing guard (refuse during LSE hours)
    Step 2: Cold start check (first 3 days -> defaults)
    Step 3: Ingest WAL (read finished day's journal)
    Step 4: Bayesian win rate + DSR
    Step 5: Kelly Accelerator (EWA blend with prior)
    Step 6: Exit Calibration (MAE/MFE -> Chandelier multiplier)
    Step 7: Regime Hunting
    Step 8: Alpha Sieve (universe reclassification)
    Step 9: Write dynamic_weights.toml + universe_classification.toml
    Step 10: Archive to parameter_history/
```

### 2.5 What Does NOT Execute (Dead Code Candidates)

The following modules are defined in `lib.rs` as `pub mod` but are NOT called from `main.rs` or from within the engine event loop. Verify each one:

| Module | File | LOC | Status (to verify) |
|--------|------|-----|--------------------|
| `scanner` (HotScanner, RotationScanner) | scanner.rs | 546 | Not called from main.rs or engine.rs |
| `smart_router` (SmartRouter) | smart_router.rs | 388 | Not called from main.rs or engine.rs |
| `exit_engine::InfiniteChandelier` | exit_engine.rs:337-478 | ~142 | Defined but engine uses `ChandelierStrategy::default()` |
| `exit_engine::Executioner` | exit_engine.rs:482-635 | ~154 | Defined but never instantiated in engine |
| `wal_actor` (WalActor, WalHandle) | wal_actor.rs | 497 | Not used; main.rs uses WalWriter directly |
| `asian_session` | asian_session.rs | 299 | Not called from main.rs |
| `european_session` | european_session.rs | 187 | Not called from main.rs |
| `cross_timezone` | cross_timezone.rs | 216 | Not called from main.rs |
| `ffi` | ffi.rs | (check) | PyO3 module registration; verify if built/used |
| `student_t_kalman` | student_t_kalman.rs | 285 | Not called from main.rs |
| `overnight_carry` | overnight_carry.rs | 255 | Not called from main.rs |

**Your task**: Verify each module above. Trace whether ANY code path from `main()` reaches it. Calculate total dead LOC and express as percentage.

---

## 3. FILE READING ORDER

Read files in this exact order. This ordering builds understanding bottom-up: types first, then infrastructure, then the engine, then the nightly pipeline.

### Tier 1: Data Contracts and Types (read first)
1. `rust_core/src/types/mod.rs` -- module re-exports
2. `rust_core/src/types/enums.rs` -- 2 newtypes (TickerId, OrderId) + 10 enums (Direction, RiskRegime, VetoReason, ExitReason, ExitPriority, OrderState, etc.)
3. `rust_core/src/types/structs.rs` -- MarketTick, OrderIntent, RiskDecision, PositionState, ExitSignal
4. `rust_core/src/types/wal.rs` -- WalEvent, WalPayload (12 variants)
5. `rust_core/src/types/execution.rs` -- execution-related types

### Tier 2: Configuration
6. `config/config.toml` -- ALL configurable parameters (~128 lines)
7. `config/contracts.toml` -- 12 ISA contract definitions with leverage/sector/inverse
8. `config/initial_universe.toml` -- universe classification seed
9. `config/uk_holidays.toml` -- UK bank holidays for clock
10. `rust_core/src/config.rs` -- Rust config structs
11. `rust_core/src/config_loader.rs` -- TOML parsing and EngineConfig construction

### Tier 3: Infrastructure Modules
12. `rust_core/src/broker.rs` -- BrokerAdapter trait definition
13. `rust_core/src/ibkr_broker.rs` -- **CRITICAL**: Real IBKR adapter. Lines 240-262 = synthetic spread. Lines 304-424 = BrokerAdapter impl.
14. `rust_core/src/paper_broker.rs` -- PaperBroker for testing
15. `rust_core/src/wal_writer.rs` -- Direct file WAL writer (what main.rs actually uses)
16. `rust_core/src/wal_actor.rs` -- Channel-based WAL actor (NOT used in main.rs -- verify)
17. `rust_core/src/wal_replay.rs` -- WAL replay for crash recovery
18. `rust_core/src/clock.rs` -- London timezone clock, TradingMode 5-state machine
19. `rust_core/src/channel.rs` -- TickChannel with backpressure monitoring
20. `rust_core/src/telemetry.rs` -- Metrics and logging
21. `rust_core/src/subscription_manager.rs` -- IBKR market data subscription rotation

### Tier 4: Core Engine
22. `rust_core/src/portfolio.rs` -- PortfolioState (positions, cash, heat, CVaR, VWAP cost basis)
23. `rust_core/src/risk_arbiter.rs` -- **CRITICAL**: 27-check synchronous risk gate. 4-state regime hierarchy.
24. `rust_core/src/exit_engine.rs` -- **CRITICAL**: Chandelier 5-rung exit + InfiniteChandelier (dead?) + Executioner (dead?)
25. `rust_core/src/engine.rs` -- **CRITICAL**: Engine struct, 8-step startup, process_tick_with_signal(), reconcile(), shutdown()
26. `rust_core/src/main.rs` -- **CRITICAL**: The actual binary entrypoint. Event loop. What is wired and what is not.

### Tier 5: Signal Generation
27. `rust_core/src/python_bridge.rs` -- Subprocess IPC to Python brain (JSON over stdin/stdout)
28. `rust_core/src/scanner.rs` -- HotScanner + RotationScanner (verify: dead code?)
29. `rust_core/src/garch_inference.rs` -- GARCH(1,1) O(1) inference from nightly-fitted params
30. `rust_core/src/universe.rs` -- Universe filter (Vanguard/Apex/Filtered routing)

### Tier 6: Supporting Modules (verify dead/alive)
31. `rust_core/src/smart_router.rs` -- ETP-first routing (verify: dead code?)
32. `rust_core/src/isa_gate.rs` -- HMRC ISA eligibility enforcement
33. `rust_core/src/reconciler.rs` -- Position reconciliation with broker
34. `rust_core/src/ouroboros_loader.rs` -- Loads nightly TOML artifacts into engine
35. `rust_core/src/hardening.rs` -- Runtime invariant checks
36. `rust_core/src/student_t_kalman.rs` -- Student-t Kalman filter (verify: dead code?)
37. `rust_core/src/overnight_carry.rs` -- Overnight position carry cost (verify: dead code?)
38. `rust_core/src/currency.rs` -- FX rate table
39. `rust_core/src/exchange_profile.rs` -- Exchange metadata (fees, hours, tick sizes)
40. `rust_core/src/asian_session.rs` -- Asian session handling (verify: dead code?)
41. `rust_core/src/european_session.rs` -- European session handling (verify: dead code?)
42. `rust_core/src/cross_timezone.rs` -- Cross-timezone arbitrage (verify: dead code?)

### Tier 7: Crucible Validation
43. `rust_core/src/crucible.rs` -- 7-suite verification harness (TradeGate, etc.)

### Tier 8: Python Ouroboros Pipeline
44. `ouroboros/config.py` -- All constants (Kelly rates, thresholds, timing)
45. `ouroboros/pipeline.py` -- 10-step nightly orchestrator
46. `ouroboros/wal_reader.py` -- WAL ingestion (DayJournal, ClosedTrade)
47. `ouroboros/bayesian.py` -- Bayesian win rate + DSR
48. `ouroboros/kelly_accelerator.py` -- Kelly fraction recalibration
49. `ouroboros/exit_calibration.py` -- Chandelier multiplier tuning via MAE/MFE
50. `ouroboros/regime_hunting.py` -- Regime classification
51. `ouroboros/alpha_sieve.py` -- Universe reclassification
52. `ouroboros/toml_writer.py` -- TOML output generation + archival

### Tier 9: Tests (read selectively for coverage assessment)
53. `rust_core/src/risk_arbiter_tests.rs`
54. `rust_core/src/exit_engine_tests.rs`
55. `rust_core/src/engine_tests.rs`
56. `rust_core/src/broker_tests.rs`
57. `rust_core/src/wal_tests.rs`
58. `rust_core/src/pipeline_tests.rs`
59. `rust_core/src/universe_tests.rs`
60. `rust_core/src/replay_tests.rs`
61. `rust_core/src/proptest_risk.rs`
62. `ouroboros/tests/test_ouroboros.py`

### Tier 10: Build and Deployment
63. `rust_core/Cargo.toml` -- Dependencies and build config
64. `Cargo.toml` -- Workspace root
65. `Dockerfile` -- Multi-stage build (Python 3.12 + Rust + Supercronic)

---

## 4. KNOWN FINDINGS TO VERIFY

The following findings were identified in the initial audit. Your task is to VERIFY each one independently. For each finding:

1. Confirm or deny the finding with specific file:line evidence
2. If confirmed, assess whether severity rating is correct
3. If denied, explain what the auditor got wrong
4. Add any nuance or additional context

### 4.1 P0 Findings (Critical -- Must Fix Before Live Trading)

**P0-1: Synthetic spread data from 5-second bar high-low**
- **Claim**: `ibkr_broker.rs` lines 244-249 synthesize bid/ask from bar close +/- 10% of (high-low) range. This is not real bid-ask data. The spread used in risk checks (SpreadTooWide veto in risk_arbiter.rs) and in the Python bridge context is fabricated.
- **Verify**: Read ibkr_broker.rs:240-262. Confirm the formula. Trace how `tick.bid` and `tick.ask` flow into: (a) RiskArbiter's spread check, (b) PythonBridge's TickContext.spread_pct, (c) ExitEngine's price spike filter.
- **Downstream impact**: If spread is always artificially narrow, the spread veto (config: 0.5% threshold) may never fire when it should, allowing entries during illiquid periods.

**P0-2: ~40% of Rust code is dead (not wired)**
- **Claim**: HotScanner, RotationScanner, InfiniteChandelier, Executioner, SmartRouter, ApexScout, WalActor, AsianSession, EuropeanSession, CrossTimezone, StudentTKalman, OvernightCarry are defined but never called from main.rs or the engine event loop.
- **Verify**: For EACH module listed, grep for any import or function call from main.rs, engine.rs, or any other module that IS called from main.rs. Compute total dead LOC / total Rust LOC.

**P0-3: No real exit management -- ExitEngine evaluates but does not submit orders**
- **Claim**: In engine.rs, `process_tick_with_signal()`, when ExitEngine.evaluate() returns Some(ExitResult), the engine writes WAL events (ExitSignal, PositionClosed) and removes the position from portfolio state -- but never actually submits a sell order to the broker. The position disappears from internal tracking but the shares remain at IBKR.
- **Verify**: Read engine.rs lines ~448-483. Confirm that after `exit_result` fires, there is no call to `self.broker.submit_order()` for the exit. Compare with the entry path (lines ~575-581) where `submit_order` IS called.

**P0-4: WAL actor uses unbounded crossbeam channel (OOM risk)**
- **Claim**: `wal_actor.rs:89` creates an `unbounded()` channel. Under sustained burst (e.g., 10k ticks/sec with WAL writes per tick), if the writer thread falls behind, the channel grows without bound and can cause OOM.
- **Verify**: Read wal_actor.rs:89. Note that main.rs does NOT use WalActor (it uses WalWriter directly). So this is a dead-code OOM risk -- it only matters if WalActor is ever enabled. Adjust severity accordingly.

**P0-5: Bridge always returns direction="Long" (correct but undocumented)**
- **Claim**: The Python bridge subprocess always returns "Long" direction for ISA instruments (since shorting is prohibited in ISA). This is correct behavior but is enforced implicitly in the Python brain rather than explicitly in the Rust risk gate.
- **Verify**: Read python_bridge.rs lines 267-273 (response parsing). Read risk_arbiter.rs lines 88-92 (CHECK 1: ISA short-sell block). Confirm that the Rust side DOES have an explicit Short->HALT+REJECT check, making the Python-side long-only behavior redundant (defense in depth). Adjust severity: this is likely P1 documentation issue, not P0.

### 4.2 P1 Findings (Important -- Should Fix Before Extended Paper Trading)

**P1-1: Ouroboros runs 12-13 hours after LSE close**
- **Claim**: Ouroboros runs at 23:50 ET via Supercronic. LSE closes at 16:30 London (11:30 ET during EST, 10:30 ET during EDT). The nightly pipeline runs 12-13 hours after market close, meaning parameter updates are applied the next morning with stale-by-half-a-day data.
- **Verify**: Read ouroboros/config.py lines 14-19 (ANALYTICS_RUN_ET = "23:50"). Read Dockerfile line 40 (crontab reference). Confirm the timing gap.

**P1-2: Kelly learning rate 0.3 means 70% inertia**
- **Claim**: In kelly_accelerator.py, the EWA blend uses `KELLY_LEARNING_RATE = 0.3`. This means `new = 0.3 * today's_optimal + 0.7 * yesterday's_value`. After a regime change, it takes ~7 days (ln(0.05)/ln(0.7) = 8.4) to adapt 95% toward the new optimum.
- **Verify**: Read ouroboros/config.py:31 and kelly_accelerator.py:92-97. Confirm the formula and compute the adaptation time constant.

**P1-3: Exit calibration uses fixed +/-0.2 steps**
- **Claim**: In exit_calibration.py, the Chandelier multiplier adjusts by a fixed +0.2 (loosen) or -0.2 (tighten) per night. This is crude -- a 7% threshold difference causes the same 0.2 step as a 90% threshold difference.
- **Verify**: Read exit_calibration.py:77-84. Confirm the adjustment is always exactly +/-0.2 regardless of the magnitude of rung5_rate or early_stop_rate.

**P1-4: No fill events written to WAL from broker**
- **Claim**: The IbkrBroker does not actively poll for fill events from IBKR. In poll_events() (ibkr_broker.rs:271-282), the only check is whether the client is still connected. There is no mechanism to receive ExecutionDetails or OrderStatus callbacks from IBKR. Fill events only appear if manually injected via inject_fill().
- **Verify**: Read ibkr_broker.rs:270-282. Confirm there is no reqExecutions, no order status callback processing. Then read engine.rs:593-597 -- fill events ARE processed if they appear in drain_events(), but the question is whether real fills from IBKR ever get into the events queue during live operation.

**P1-5: ISA annual limit check exists but counter never increments**
- **Claim**: risk_arbiter.rs CHECK 17 (line ~195) checks `portfolio.isa_year_invested >= config.isa_annual_limit_gbp`. But `isa_year_invested` in PortfolioState is initialized to 0.0 and is never incremented anywhere in the codebase when orders fill.
- **Verify**: Read portfolio.rs -- search for any code that increments `isa_year_invested`. Read engine.rs process_broker_event (Fill handling, lines ~644-682) -- confirm no ISA counter update. Also check isa_gate.rs for record_deposit() usage.

---

## 5. INDEPENDENT AUDIT REQUIREMENTS

Beyond verifying the known findings, you must perform your own independent analysis. Produce findings in the following categories:

### 5.1 Architecture & Design
- Is the single-threaded event loop appropriate for 12 instruments at 5-second bars?
- Is the Python subprocess bridge (JSON over stdin/stdout) a latency bottleneck?
- Is the 100ms loop interval appropriate given 5-second bar data?
- Evaluate the WAL event sourcing design for crash recovery completeness.

### 5.2 Risk Management
- Are all 27 risk checks in RiskArbiter correctly ordered? (Check for short-circuit optimization: cheapest checks first?)
- Is the 4-state regime hierarchy (Normal < Reduce < Flatten < Halt) correctly enforced? Can the system ever get stuck in HALT?
- Is the Kelly position sizing pipeline sound end-to-end? (Python computes, Rust arbiter adjusts, broker executes)
- Does the daily drawdown check use the correct denominator (high-water mark, not starting equity)?

### 5.3 Data Integrity
- Trace the MarketTick lifecycle from IBKR bar to Python bridge to risk check to order. Where can data corruption occur?
- Is the WalWriter fsync policy adequate? (Check for torn writes, partial lines)
- How does the system handle NaN/Inf propagation? (Check validate_f64 usage)

### 5.4 Operational Readiness
- What happens on IBKR disconnect during an open position?
- What happens on Python bridge crash mid-trade?
- What happens on disk full (WAL cannot write)?
- Is the Docker healthcheck (pgrep aegis) sufficient?

### 5.5 ISA Compliance
- Verify the short-sell block is truly air-tight (Rust + Python + IBKR API all agree)
- Verify the annual deposit limit enforcement path
- Verify exchange eligibility (all 12 instruments are on LSE)

---

## 6. RESEARCH TOPIC PACKS

For each finding, cross-reference against these four knowledge domains. Cite specific papers, docs, or standards where applicable.

### 6.1 Academic & Quantitative Research

| Topic | Key References |
|-------|---------------|
| Kelly Criterion for leveraged ETPs | Kelly (1956), Thorp (2006) "The Kelly Criterion in Blackjack Sports Betting and the Stock Market", MacLean/Thorp/Ziemba (2011) |
| Half-Kelly and fractional Kelly | Thorp (2006): half-Kelly reduces variance by 75% at cost of 25% growth rate |
| GARCH(1,1) for leveraged ETP vol | Bollerslev (1986), Engle (1982). Note: leveraged ETPs have vol drag = leverage^2 * base_vol / 2 |
| Bayesian win rate estimation | Laplace smoothing. Beta-Binomial conjugate prior. Note: config uses prior=(1,2) which is Jeffreys prior |
| Deflated Sharpe Ratio | Bailey & Lopez de Prado (2014) "The Deflated Sharpe Ratio". Adjusts for multiple testing |
| Chandelier Exit | Le Beau (1999). Originally: 3x ATR from highest high. AEGIS uses 5-rung ladder variant |
| MAE/MFE analysis | Sweeney (1993) Maximum Adverse Excursion. Used for stop placement optimization |
| Wilder's ATR | Wilder (1978) "New Concepts in Technical Trading Systems". Smoothed true range |
| Yang-Zhang volatility | Yang & Zhang (2000) "Drift-Independent Volatility Estimation". Handles overnight gaps |
| Amihud illiquidity | Amihud (2002) "Illiquidity and stock returns". ILLIQ = E[|r|/volume] |
| CVaR (Expected Shortfall) | Rockafellar & Uryasev (2000). Coherent risk measure unlike VaR |
| Moreira-Muir vol targeting | Moreira & Muir (2017) "Volatility-Managed Portfolios" |

### 6.2 Broker & Exchange Documentation

| Topic | Reference |
|-------|-----------|
| IBKR 5-second real-time bars | IBKR API docs: reqRealTimeBars returns OHLCV every 5s. NO bid/ask in bars. |
| IBKR rate limits | 50 messages/sec API rate limit. Historical data: 60 requests per 10 min. |
| IBKR Error 1100/1102 | 1100 = connectivity lost. 1102 = restored (market data and account may be out of sync). Requires reconciliation. |
| IBKR Error 321 | Pacing violation for historical data requests |
| IBKR Error 326 | Cannot connect (duplicate client_id). Requires client_id rotation. |
| IBKR Paper Trading | Paper account fills are simulated. Order behavior may differ from live (no partial fills on paper, instant fills, etc.) |
| LSE trading hours | Pre-open: 07:50-08:00. Continuous: 08:00-16:30. Closing auction: 16:30-16:35. All London local time. |
| LSE tick sizes | Under GBP 1: 0.001 increments. Over GBP 1: 0.01 increments. |
| UK ISA rules (HMRC) | Annual deposit limit GBP 20,000 (2025/26). No short selling. Only eligible exchanges (LSE qualifies). Tax year: 6 April - 5 April. |
| Leveraged ETP structure | Daily-reset leveraged products. Compounding drag (vol drag) over multi-day holds. 3x Nasdaq ETP has ~9% annual vol drag at 20% base vol. |

### 6.3 Competitor & Industry Reference

| Topic | Reference |
|-------|-----------|
| Two Sigma risk framework | Multi-layer risk: pre-trade, real-time, post-trade. AEGIS has pre-trade (RiskArbiter) but incomplete real-time (no fill polling) and no post-trade (Ouroboros is delayed) |
| Jane Street position management | Fill-driven state machine. Every fill triggers immediate position update + risk recheck. AEGIS does this in process_broker_event but lacks reliable fill delivery. |
| Citadel reconciliation | Continuous reconciliation. AEGIS does 5-minute periodic. Industry standard for systematic: 1-minute or event-driven. |
| AQR Kelly implementation | Typically fractional Kelly (1/4 to 1/2). AEGIS uses half-Kelly (0.5 cap) with additional Ouroboros learning rate blending. |
| Bridgewater risk parity | Portfolio heat concept (risk budget per position). AEGIS heat = sum((entry-stop)*qty/equity). |

### 6.4 Systems & Engineering

| Topic | Reference |
|-------|-----------|
| crossbeam unbounded channel | Can grow without limit. OOM risk under sustained producer-faster-than-consumer scenarios. Bounded channels provide backpressure. |
| fsync reliability | fsync guarantees data reaches disk. But power loss between write and fsync can lose data. AEGIS batches syncs (configurable interval). |
| NDJSON format | Newline-delimited JSON. Robust to partial writes (last line may be truncated, all prior lines intact). Good choice for WAL. |
| UUIDv7 | Time-ordered UUIDs. Sortable by creation time. Used for WAL event_id and order_id. Correct choice for event sourcing. |
| Docker healthcheck | `pgrep aegis` only checks process existence, not liveness. A hung process (deadlock, infinite loop) would pass this check. |
| Supercronic | Non-syslog cron for containers. Suitable for Ouroboros scheduling. Does not handle missed runs (if container was down at 23:50, run is lost). |
| PyO3 cdylib + rlib | Dual crate-type allows both Python extension module (cdylib for maturin) and Rust library (rlib for aegis binary). Standard pattern. |
| ibapi crate | Rust IBKR client. sync feature = blocking client. Subscription model: iterator-based with try_next() for non-blocking poll. |

---

## 7. OUTPUT FORMAT

Produce your audit report in the following structure:

### Section A: Executive Summary
- 2-3 paragraph overview of system readiness
- Count of P0 / P1 / P2 findings (yours + verified)
- Overall assessment: GO / NO-GO for extended paper trading

### Section B: Known Findings Verification
For each of the 10 known findings (P0-1 through P1-5):
```
### [ID]: [Title]
**Status**: CONFIRMED / DENIED / MODIFIED
**Evidence**: [file:line references]
**Severity**: P0 / P1 / P2 (may differ from original assessment)
**Analysis**: [2-5 sentences explaining your verification]
```

### Section C: New Independent Findings
For each new finding you discover:
```
### [NEW-XX]: [Title]
**Severity**: P0 / P1 / P2
**Persona**: [Which persona identified this]
**Evidence**: [file:line references]
**Impact**: [What could go wrong]
**Recommendation**: [How to fix]
```

Severity definitions:
- **P0**: System cannot safely trade live capital. Must fix before any real money deployment.
- **P1**: System will underperform or have operational gaps. Should fix before extended paper trading.
- **P2**: Code quality, documentation, or design improvement. Fix when convenient.

### Section D: Dead Code Census
```
| Module | File | LOC | Called From | Verdict |
|--------|------|-----|-------------|---------|
| ... | ... | ... | [None / main.rs / engine.rs] | DEAD / ALIVE |
```
Total dead LOC: X / Y = Z%

### Section E: Risk Gate Audit
Walk through all 27 RiskArbiter checks. For each:
- Is the check order correct?
- Is the threshold sourced from config.toml (not hardcoded)?
- Are there any checks that can never fire given current config values?
- Are there missing checks that should exist?

### Section F: Data Flow Trace
Trace a single MarketTick from IBKR bar reception through to order submission. Document every transformation, every copy, every decision point. Flag any point where data quality degrades.

### Section G: Recommendations Priority Matrix
Rank all findings (verified + new) by:
1. Impact (what breaks if unfixed)
2. Effort (lines of code to fix)
3. Urgency (blocks paper trading / blocks live trading / nice to have)

---

## 8. FINAL INSTRUCTIONS

1. Read every file listed in Section 3 before writing any findings.
2. Do not skim. The bugs are in the details (missing function calls, unbounded channels, counters that never increment).
3. For every claim, provide file:line evidence. No exceptions.
4. If you are uncertain about a finding, say so and explain what additional information would resolve the uncertainty.
5. Prioritize findings that affect capital safety over code quality issues.
6. Remember: this system will eventually manage real money. Treat the audit accordingly.

---

*This prompt was generated on 2026-03-11 for AEGIS V2 codebase at approximately 20,650 LOC (18,150 Rust + 2,050 Python + 450 config/build).*

---

## GEMINI CROSS-AUDIT RESULTS (2026-03-11)

The Gemini Institutional Syndicate executed this prompt against the codebase dump and produced
5 deliverables. The results were triaged against actual source code by the Claude primary audit.

### Verified Findings (Added to Master War File)
1. **P0-06: DelayedFrozen market data** — `ibkr_broker.rs:123` calls `switch_market_data_type(DelayedFrozen)` on connect. CONFIRMED.
2. **P0-07: Directionless broker (buy-only)** — `broker.rs:76-82` has no side param; `ibkr_broker.rs:330` always calls `.buy()`. CONFIRMED.
3. **P0-08: TickerId(0) hardcode** — `main.rs:386` passes `TickerId(0)` for all broker events. CONFIRMED.

### Rejected Hallucinations
Gemini fabricated ~12 findings referencing modules and architectures that don't exist:
- Tokio (not in Cargo.toml — engine is synchronous std::thread)
- PyO3 GIL deadlock (Python is subprocess, not in-process)
- RwLock write-starvation (zero RwLock/Mutex in codebase)
- VWAP/TWAP order slicing (single limit orders, no slicing)
- Kelly formula error (formula is correct: p - q/b)
- EVT, CUSUM, LSTM, Thompson Bandit, DCC-GARCH, Polygon (none exist in code)

### Lessons for Future Audits
1. Always verify module existence in Cargo.toml before claiming dependency issues
2. Verify architectural claims (async/sync, subprocess/FFI) by reading main.rs
3. Read the actual Kelly formula code before claiming math errors
4. Do not assume plan-document features exist in the runtime binary
