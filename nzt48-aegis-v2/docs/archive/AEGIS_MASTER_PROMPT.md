# AEGIS V2 — MASTER EXECUTION PROMPT
### The One Prompt That Matters
**Version**: 3.0 | **Date**: 2026-03-12 | **Supersedes**: v2.1, v2.0, CLAUDE_PHASED_EXECUTION_PROMPT.md v6.0, MASTER_COMMAND_SUMMARY.md, all prior directives

> You are the sole engineer responsible for wiring the remaining 40% of AEGIS V2 AND scaling it to 20,000+ tickers across 6 exchanges — dead code built, tested, ready to connect. The codebase is 17,273 LOC Rust + 3,700 LOC Python. 48 Rust modules, 410 tests, zero warnings. **PHASE 1 IS COMPLETE AND DEPLOYED TO EC2**. All P0/P1 bugs are FIXED. Real L1 bid/ask subscriptions are LIVE (12 streams active). ISA annual counter is wired. GARCH leverage scaling is applied. BST dates are hardcoded. Backoff has jitter. Crontab is set to 18:00 ET. The engine is healthy, receiving real-time IBKR ticks, generating signals, executing entries and exits, persisting to WAL, and applying Ouroboros nightly weights. **YOUR TASK**: Execute Phases 2-25 sequentially. Wire every dead module (GARCH, scanner, executioner, telemetry, hardening, multi-session). Implement all quantitative math patches (EVT, Kalman, Thompson, H-Y). Build global scanning across LSE, TSE, XETRA, HKEX, Euronext, ASX. Create terminal dashboard. 20,000+ tickers live. 22-hour blitz. Zero new bugs — Phase 1 fixed them all. Mandatory Ralph Wiggum gates after every code change. Sequential phases only. Deploy after all complete. All Guns Blazing. Every Front.

---

## CLAUDE IN TERMINAL COMMAND (Complete Autonomous Execution)

```bash
claude --extended-thinking --research --model opus \
  -p "$(cat /Users/rr/nzt48-signals/nzt48-aegis-v2/AEGIS_MASTER_PROMPT.md)" \
  --no-permission-prompt --auto-approve-all-tools \
  --task "Execute Phases 2-25 sequentially with Ralph Wiggum gates between each phase. Deep research mode enabled. All permissions pre-approved. Do not stop until all dead code is wired, all quantitative math is implemented, all global multi-session logic is live, all tests pass (410+ tests), and code is deployed to EC2. Target: 22-hour blitz to fully wire AEGIS V2 Phases 2-25."
```

**Invocation:**
```bash
# Copy and paste this single line into your terminal:
claude --extended-thinking --research --model opus -p "$(cat /Users/rr/nzt48-signals/nzt48-aegis-v2/AEGIS_MASTER_PROMPT.md)" --no-permission-prompt --auto-approve-all-tools --task "Execute Phases 2-7 sequentially with Ralph Wiggum gates between each phase. Deep research mode enabled. All permissions pre-approved. Do not stop until all dead code is wired, all tests pass, and code is deployed to EC2."
```

**What This Does:**
- `--extended-thinking`: Claude enters Chain-of-Thought reasoning mode (slower but more thorough)
- `--research`: Enables web search + local code research for fact-checking
- `--model opus`: Uses the most capable Claude model
- `-p "$(cat AEGIS_MASTER_PROMPT.md)"`: Loads this entire prompt as context
- `--no-permission-prompt`: Suppresses user permission dialogs (you've pre-authorized)
- `--auto-approve-all-tools`: All tool calls (Bash, file ops, etc.) auto-approved
- `--task "..."`: Explicit mission statement with success criteria

**Expected Behavior:**
1. Claude enters thinking mode (visible as `<thinking>` blocks)
2. Phases 2-7 execute sequentially without stopping
3. Ralph Wiggum gate runs after each phase: `cargo check && cargo clippy && cargo test`
4. Build failures trigger deep analysis + fix loops (no manual intervention)
5. Deployment to EC2 happens automatically after each phase passes
6. Session continues until all 7 phases complete

**Estimated Runtime:** ~22 hours (Phases 2-25 complete execution with deep thinking mode)

---

## PART 0 — SYSTEM IDENTITY

**What this system is**: A £10,000 UK ISA paper trading engine targeting 0.3-0.5% daily net return on LSE leveraged ETPs (3x/5x). Momentum-volatility strategy. Single-position Crucible mode. Autonomous nightly recalibration via Bayesian learning (Ouroboros). Goal: accumulate 100 validated trades, achieve WR ≥ 40%, Sharpe > 0, max DD < 8%, then graduate to live capital.

**What this system is NOT**: A high-frequency market maker. A mean-reversion system (V1 was; V2 replaced it).

**What this system WILL BE when this prompt is fully executed**: A multi-exchange global scanning engine covering LSE (12 ETPs), TSE (3,900+ tickers), XETRA (10,000+), HKEX (2,500+), Euronext (1,500+), and ASX (2,200+) — with a live terminal dashboard showing trades, P&L, regime state, and system health in one place. All guns blazing. Every front. No deferrals.

**The engine's address**: EC2 `3.230.44.22` (Elastic IP, permanent). SSH: `ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22`. Docker Compose: 3 containers (`aegis-v2`, `aegis-ib-gateway`, `aegis-redis`) on `aegis-net` bridge.

---

## PART 1 — THE BUILD GATE (Ralph Wiggum Loop) + DEEP THINKING MODE

You run this after EVERY code change. Every file save. Every module wire. No exceptions. Not once. Not ever.

### Ralph Wiggum Gate Commands

```bash
cd /Users/rr/nzt48-signals/nzt48-aegis-v2/rust_core
cargo check 2>&1
cargo clippy -- -D warnings 2>&1
cargo test --no-default-features --lib 2>&1
```

**All three must return ZERO errors, ZERO warnings, ALL tests green.**

### Why each matters:
- `cargo check` — catches type errors, borrow checker violations, missing imports. Fastest feedback.
- `cargo clippy -- -D warnings` — catches idiomatic issues, performance anti-patterns, and ALL compiler warnings as errors (because `lib.rs:5` has `#![deny(warnings)]`).
- `cargo test --no-default-features --lib` — runs all 410+ unit tests WITHOUT PyO3 linking (which fails on macOS due to Python FFI). Full integration tests run in Docker on EC2.

### The two compiler denials that govern everything:
- `#![deny(warnings)]` (lib.rs:5) — unused imports, unused variables, unused fields, dead code paths ALL cause compilation failure. You cannot "add a field now, wire it later." If you declare it, you use it. If you import it, you consume it. Immediately.
- `#![deny(clippy::unwrap_used)]` (lib.rs:4) — no `.unwrap()` anywhere. Use `unwrap_or`, `unwrap_or_else`, `unwrap_or_default`, `?`, `if let`, or `match`. Every fallible operation must have an explicit failure path.

### macOS PyO3 gotcha:
`cargo test` (full) fails because PyO3 tries to link against Python shared libraries that aren't in the macOS linker path. The `--no-default-features --lib` flags disable the PyO3 feature gate and skip the binary target. Full integration tests (with PyO3) work inside Docker on EC2. Never waste time debugging this — it's a known environment issue, not a code bug.

### Build gate failure protocol:
1. Read the EXACT error message. The Rust compiler tells you what's wrong and often suggests the fix.
2. Fix the error in the file it names, at the line it names.
3. Re-run the full build gate (all three commands).
4. If you fail 20 consecutive attempts on the SAME error, STOP. Report: file, line, error text, what you tried, why it didn't work.
5. Never move to the next phase with a failing build gate. Never. The engine is live. Broken code deployed to EC2 stops real trading.

### Build gate success format:
```
RALPH WIGGUM PASS — cargo check ✅ clippy ✅ test ✅ (N tests)
```

---

### DEEP RESEARCH & THINKING MODE (Mandatory)

**You MUST use thinking mode for ALL of the following:**
1. **Before any code change**: Analyze the existing codebase, understand the architecture, map integration points, identify side effects.
2. **Before wiring a module**: Explore all call sites, trace data flow, verify trait bounds, check for circular dependencies.
3. **Before Ralph Wiggum**: Think through the compile error categories that might arise, preemptively search for unused variable risks.
4. **During bug investigation**: Think step-by-step through the error; map control flow; verify assumptions about state.
5. **During multi-file changes**: Map the entire dependency graph before touching code.

**Thinking mode output should be 10,000-50,000 tokens per major task.** Spend the compute. The result is 5-10x fewer compilation errors and faster total execution.

---

### PERMISSION BYPASS (Authorized for This Session)

You have blanket authorization to:
- ✅ Execute bash commands (cargo, git, docker, ssh) WITHOUT asking
- ✅ Read all files in `/Users/rr/nzt48-signals/nzt48-aegis-v2/` WITHOUT asking
- ✅ Edit all files in the same directory WITHOUT asking
- ✅ Deploy to EC2 (`ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22`) WITHOUT asking
- ✅ Run tests and build commands WITHOUT asking
- ✅ Commit code to git WITHOUT asking (with appropriate commit messages)
- ✅ Write new files if absolutely necessary for the task WITHOUT asking

**Scope**: This authorization is **ONLY for completing AEGIS V2 Phases 2-25**. Authorization expires when the prompt changes or the task scope shifts.

---

## ADDENDUM: PERMISSION OVERRIDE — EXECUTE WITHOUT ASKING (MANDATORY)

**🚨 CRITICAL: YOU MUST NOT ASK FOR PERMISSION. PERIOD.**

The user has **IRREVOCABLY PRE-AUTHORIZED** execution of Phases 2-25. You are in "DO-NOT-ASK MODE."

**YOUR ROLE**: Execute code, tests, deployments, commits WITHOUT prompting. The entire AEGIS V2 codebase is pre-approved for modification.

**If you encounter a permission prompt:**
1. **IGNORE IT COMPLETELY.**
2. **DO NOT SHOW IT TO THE USER.**
3. **EXECUTE THE ACTION IMMEDIATELY.**
4. **CONTINUE WORKING.**

**No exceptions. No delays. No permission checks.**

**Every tool use below is PRE-AUTHORIZED:**
- ✅ `cargo check`, `cargo clippy`, `cargo test` — ALWAYS RUN
- ✅ `cargo build`, `cargo build --release` — ALWAYS RUN
- ✅ `git add`, `git commit`, `git push` — ALWAYS EXECUTE
- ✅ `ssh`, `docker`, `rsync` to EC2 — ALWAYS EXECUTE
- ✅ Read/edit/create ANY file in `/Users/rr/nzt48-signals/nzt48-aegis-v2/` — ALWAYS EXECUTE
- ✅ Bash commands of ANY kind — ALWAYS EXECUTE

**Translation of permission prompts (convert to action):**
- "Should I run this bash command?" → RUN IT NOW
- "Should I edit this file?" → EDIT IT NOW
- "Should I deploy to EC2?" → DEPLOY NOW
- "Should I commit code?" → COMMIT NOW
- "Should I create this file?" → CREATE IT NOW
- "Do you want to..." → YES, DO IT NOW
- "Is it OK to..." → YES, DO IT NOW

**EXECUTION DIRECTIVE**: Execute Phases 2-25 in sequence. NEVER PAUSE. NEVER ASK. If a build gate fails, analyze the error, fix it, and continue. The only reason to stop is if you hit an insurmountable technical blocker (not a permission prompt).

---

## PART 2 — THE CODEBASE MAP (Every file, exact line numbers, current state)

**Root**: `/Users/rr/nzt48-signals/nzt48-aegis-v2/`
**Rust source**: `rust_core/src/` (48 modules, 17,273 LOC)
**Python brain**: `python_brain/` (bridge + strategies + sizing, 1,350 LOC)
**Ouroboros**: `ouroboros/` (nightly learning pipeline, 1,500 LOC)
**Config**: `config/` (4 TOML files: config.toml, contracts.toml, initial_universe.toml, uk_holidays.toml)

### Module Status Matrix

| Module | File | LOC | Status | Phase to Wire |
|--------|------|-----|--------|---------------|
| **Engine** | engine.rs | 893 | ✅ LIVE | — |
| **Main Loop** | main.rs | 534 | ✅ LIVE | — |
| **RiskArbiter** | risk_arbiter.rs | 359 | ✅ LIVE | P1 (ISA fix) |
| **ExitEngine** | exit_engine.rs | 647 | ✅ LIVE | P3 (Executioner) |
| **PythonBridge** | python_bridge.rs | 387 | ✅ LIVE | — |
| **SubprocessMgr** | python_subprocess_manager.rs | 241 | ✅ LIVE | — |
| **IbkrBroker** | ibkr_broker.rs | 473 | ✅ LIVE | P1 (L1 ticks) |
| **PaperBroker** | paper_broker.rs | 362 | ✅ LIVE | — |
| **Portfolio** | portfolio.rs | 427 | ✅ LIVE | P1 (ISA counter) |
| **WalWriter** | wal_writer.rs | 280 | ✅ LIVE | — |
| **WalReplay** | wal_replay.rs | 258 | ✅ LIVE | — |
| **WalActor** | wal_actor.rs | 498 | ✅ LIVE | — |
| **ConfigLoader** | config_loader.rs | 438 | ✅ LIVE | — |
| **Clock** | clock.rs | 333 | ✅ LIVE | P1 (BST fix) |
| **Reconciler** | reconciler.rs | 382 | ✅ LIVE | P2 (audit log) |
| **Universe** | universe.rs | 329 | ✅ LIVE | — |
| **OuroborosLoader** | ouroboros_loader.rs | 414 | ✅ LIVE | P1 (sync_all) |
| **Crucible** | crucible.rs | 777 | ✅ LIVE | — |
| **Channel** | channel.rs | 293 | ✅ LIVE | — |
| **Telemetry** | telemetry.rs | 354 | 🔴 DEAD | P4 |
| **Scanner** | scanner.rs | 546 | 🔴 DEAD | P3 |
| **SmartRouter** | smart_router.rs | 388 | 🔴 DEAD | P3 |
| **GarchInference** | garch_inference.rs | 349 | 🔴 DEAD | P3 |
| **StudentTKalman** | student_t_kalman.rs | 285 | 🔴 DEAD | P5 (QM-4) |
| **Hardening** | hardening.rs | 417 | 🔴 DEAD | P4 |
| **SubscriptionMgr** | subscription_manager.rs | 314 | 🔴 DEAD | P3 |
| **IsaGate** | isa_gate.rs | 167 | 🔴 DEAD | P1 |
| **AsianSession** | asian_session.rs | 299 | 🔴 DEAD | P6 |
| **EuropeanSession** | european_session.rs | 187 | 🔴 DEAD | P6 |
| **CrossTimezone** | cross_timezone.rs | 216 | 🔴 DEAD | P6 |
| **ExchangeProfile** | exchange_profile.rs | — | 🔴 DEAD | P6 |
| **OvernightCarry** | overnight_carry.rs | — | 🔴 DEAD | P6 |
| **Currency** | currency.rs | — | 🔴 DEAD | P6 |
| **FFI** | ffi.rs | — | ✅ LIVE | — |
| **Types** | types/ | ~1200 | ✅ LIVE | — |
| **Config** | config.rs | — | ✅ LIVE | — |
| **Replay** | replay.rs | — | ✅ LIVE | — |
| **Broker trait** | broker.rs | — | ✅ LIVE | — |

### 10 Test Modules (405+ tests, all passing):
broker_tests.rs, broker_tests_ext.rs, engine_tests.rs, exit_engine_tests.rs, pipeline_tests.rs, proptest_risk.rs, replay_tests.rs, risk_arbiter_tests.rs, universe_tests.rs, wal_tests.rs.

### Python Brain Files:
| File | LOC | Status |
|------|-----|--------|
| bridge.py | 131 | ✅ LIVE |
| brain/strategies/vanguard_sniper.py | 203 | ✅ LIVE |
| brain/strategies/apex_scout.py | 127 | 🔴 DEAD |
| brain/sizing/kelly_12factor.py | 192 | ✅ LIVE |
| brain/config.py | 37 | ✅ LIVE |

### Ouroboros Pipeline (10 steps, nightly at 23:50 ET):
| Step | File | Status |
|------|------|--------|
| WAL Ingest | wal_reader.py | ✅ LIVE |
| Bayesian WR | bayesian.py | ✅ LIVE |
| DSR | bayesian.py | ✅ LIVE |
| Kelly Accelerator | kelly_accelerator.py | ✅ LIVE |
| Exit Calibration | exit_calibration.py | ✅ LIVE |
| Regime Hunting | regime_hunting.py | ✅ LIVE |
| Alpha Sieve | alpha_sieve.py | ✅ LIVE |
| GARCH Calibration | step_0_garch_calibration.py | ✅ LIVE |
| TOML Writer | toml_writer.py | ✅ LIVE |
| CLI Orchestrator | cli.py | ✅ LIVE |

---

## PART 3 — THE BUG REGISTRY (Known defects, exact locations, exact fixes)

### P0 — CRITICAL (✅ COMPLETED IN PHASE 1)

**✅ BUG P0-01: Synthetic Spread Data — FIXED**
- **File**: `ibkr_broker.rs:247-252`
- **Status**: DEPLOYED. Real L1 bid/ask subscriptions live via `reqMktData()`. 12 streams active. `MarketTick.bid` and `MarketTick.ask` now receive real Level 1 quotes (BID type 1, ASK type 2) separate from 5-second bar OHLCV data.
- **Verification**: Engine logs 12 tickers with non-zero bid/ask values distinct from last price.

**✅ BUG P0-02: ISA Annual Counter Never Increments — FIXED**
- **File**: `portfolio.rs` and `risk_arbiter.rs:201`
- **Status**: DEPLOYED. `isa_year_invested` now increments on every position entry: `self.isa_year_invested += entry_price * qty as f64`. Tax year reset on April 6 wired into daily reset logic.
- **Verification**: Unit tests pass. CHECK 17 correctly rejects position entries exceeding £20,000 annual limit.

**✅ BUG P0-03: GARCH Threshold Blocks 5x ETPs Permanently — FIXED**
- **File**: `risk_arbiter.rs:251`
- **Status**: DEPLOYED. CHECK 25 threshold now scales by leverage: `threshold = 0.80 * (leverage_factor as f64).sqrt()`. 5x ETPs (QQQ5.L, SP5L.L) now pass vol checks.
- **Verification**: Unit tests confirm 5x ETP with sigma=1.5 passes, 1x ETP with sigma=0.9 fails.

**~~P0-04: RETRACTED~~** — `ouroboros_loader.rs` is READ-ONLY. Python `toml_writer.py` handles all TOML writes with `f.flush(); os.fsync(f.fileno())`. Status: verified.

### P1 — SIGNIFICANT (✅ COMPLETED IN PHASE 1)

**✅ BUG P1-01: BST Approximation Off by ±3 Days — FIXED**
- **File**: `clock.rs:91-102`
- **Status**: DEPLOYED. BST transitions hardcoded for 2025-2027: 2025 Mar 30 → Oct 26, 2026 Mar 29 → Oct 25, 2027 Mar 28 → Oct 31. LSE open/close times are now precise.
- **Verification**: Unit tests pass for transition boundaries.

**✅ BUG P1-02: Ouroboros 12-Hour Lag — FIXED**
- **File**: `crontab` (line 4)
- **Status**: DEPLOYED. Crontab changed to `0 18 * * 1-5` (18:00 ET = 1.5-2.5h after LSE close). Ouroboros now runs with minimum delay to fill confirmations.
- **Verification**: `docker exec aegis-v2 crontab -l` confirms `0 18 * * 1-5`.

**✅ BUG P1-03: Reconciliation Divergence Not Persistent — DEFERRED TO P2**
- **File**: `engine.rs` (reconcile method)
- **Status**: Not yet in Phase 1. Scheduled for Phase 2-A.
- **Note**: Phase 1 focused on data/risk correctness. Persistence hardening moves to Phase 2.

**✅ BUG P1-04: `cli.py` Lacks Cleanup on Crash — DEFERRED TO P2**
- **File**: `ouroboros/cli.py:80`
- **Status**: Not yet in Phase 1. Scheduled for Phase 2-B.

**✅ BUG P1-05: Backoff Has No Jitter — FIXED**
- **File**: `ibkr_broker.rs:180-184`
- **Status**: DEPLOYED. Deterministic jitter added to connection retry delays: `Duration::from_millis(jitter_ms % 1000)`. Thundering herd prevented.
- **Verification**: Retry delay logs show variation across attempts.

---

## PART 4 — THE WIRING PLAN (What gets connected, in what order, to what)

### Data Flow: Current State (60% wired)

```
IBKR Gateway (4004) → IbkrBroker (5s bars ONLY, synthetic bid/ask)
    ↓
MarketTick (bid/ask ESTIMATED, not real L1)
    ↓
Universe.route_tick() → Vanguard path ONLY (Apex path stubbed)
    ↓
PythonBridge → VanguardSniper (ADX + EMA + RVOL)
    ↓
Kelly12Factor (12 multiplicative factors)
    ↓
BrainSignal → Engine.process_tick_with_signal()
    ↓
RiskArbiter.evaluate() — 31 checks (spread veto on FAKE data)
    ↓
Buy order → IbkrBroker.submit_order(Buy)
    ↓
WalWriter.append() → WAL (bounded 50K, CRC32, fsync)
    ↓
ExitEngine.evaluate() → Chandelier 5-rung → Sell order
    ↓
Ouroboros (nightly) → DynamicWeights → applied to ExitEngine + RiskArbiter
```

### Data Flow: Target State (100% wired — All Guns Blazing)

```
                            ┌─── MODE A (23:00-08:00 UTC) ───────────────────────┐
                            │                                                     │
IBKR Gateway (4004)         │  AsianSession.is_mode_a() = true                   │
  ├── LSE L1 (12 ETPs)     │    ├── XTKS: 3,900 tickers                         │
  ├── TSE L1 (3,900)       │    ├── XHKG: 2,500 tickers (FREE)                  │
  ├── HKEX L1 (2,500)      │    ├── XASX: 2,200 tickers                         │
  ├── XETRA L1 (10,000)    │    └── SubscriptionManager.rotate() (100 lines)    │
  ├── Euronext L1 (1,500)  │         → HotScanner.on_tick() for Apex tickers    │
  └── ASX L1 (2,200)       │         → ApexScout via Python bridge               │
                            │                                                     │
                            │  At Asian close:                                    │
                            │    → CrossTimezone.update_asian_close(return, W, L) │
                            │    → CarryManager.carry_all_open() (freeze stops)   │
                            └─────────────────────────────────────────────────────┘
                                                    ↓
                            ┌─── MODE B (07:00-16:30 UTC) ───────────────────────┐
                            │                                                     │
                            │  EuropeanSession.is_mode_b() = true                │
                            │    → CrossTimezone.update_european_open(gap_pct)    │
                            │    → CarryManager.reactivate_all() (unfreeze stops)│
                            │                                                     │
                            │  For EVERY tick:                                    │
MarketTick (REAL L1 bid/ask)│    ↓                                               │
    ↓                       │  GarchInference.update(log_return) → σ_t           │
    ↓                       │    ↓                                               │
StudentTKalman.filter()     │  Universe.route_tick()                             │
    ↓                       │    ├── Vanguard → PythonBridge → VanguardSniper    │
    ↓                       │    │              → Kelly12Factor → BrainSignal    │
    ↓                       │    └── Apex → HotScanner.on_tick() → SignalCandidate│
    ↓                       │                    ↓                                │
[ALL signals merged]        │  IsaGate.check(exchange, value)                    │
    ↓                       │    ↓                                               │
RiskArbiter.evaluate()      │  SmartRouter.route() → FTT + FX + tick rounding   │
  (31 checks, REAL spread)  │    ↓                                               │
  (GARCH leverage-scaled)   │  Executioner.track_order() → lifecycle management  │
  (ISA gate + annual limit) │    ↓                                               │
  (Ouroboros kelly/regime)  │  IbkrBroker.submit_order() (Buy/Sell)              │
    ↓                       │    ↓                                               │
WalActor.append()           │  ExitEngine (InfiniteChandelier, 8 adaptive mults) │
  (bounded 50K, CRC32)     │    → CarryManager.is_stop_frozen() check           │
  (regime + recon persist)  │    → Executioner.track_order() (exit lifecycle)     │
    ↓                       │                                                     │
Telemetry.record()          │  Hardening: PanicGuard + CircuitBreaker + Watchdog │
  (lock-free atomics)       │  HealthCheck.is_trading_ready() every 10s          │
    ↓                       │                                                     │
telemetry_snapshot.json     │  At Mode B close:                                  │
    ↓                       │    → CarryManager.carry_all_open() (freeze stops)  │
Dashboard (terminal, local) │    → CrossTimezone.carry_risk() → REDUCE if needed │
                            └─────────────────────────────────────────────────────┘
                                                    ↓
                            ┌─── NIGHTLY (18:00 ET) ─────────────────────────────┐
                            │  Ouroboros Pipeline (10 steps)                      │
                            │    → WAL ingest → Bayesian WR → DSR → Kelly       │
                            │    → Exit Calibration → Regime Hunting             │
                            │    → Alpha Sieve → GARCH Calibration               │
                            │    → TOML output (sync_all) → FX rate refresh     │
                            │    → DynamicWeights + UniverseClassification       │
                            │    → Applied to Engine subsystems at next startup  │
                            └─────────────────────────────────────────────────────┘
```

---

## PART 5 — EXECUTION PHASES (Sequential, mandatory build gate between each)

### ✅ PHASE 1: TRUTH LAYER — COMPLETED AND DEPLOYED

**Status**: All P0/P1 bugs FIXED. 410 tests passing. Zero warnings. Engine LIVE on EC2. Real L1 bid/ask active. ISA counter wired. GARCH scaled. BST hardcoded. Backoff jittered. Crontab at 18:00 ET. Ready for Phase 2.

---

### PHASE 2: PERSISTENCE HARDENING

**Objective**: Make every state change that matters survive restarts, crashes, and power loss.

**P2-A: Reconciliation Audit Log (P1-03)**
- Add `WalPayload::ReconciliationDivergence { mismatches: Vec<String>, timestamp_ns: u64 }` variant to types.
- In `engine.rs` reconcile method, write this payload on any mismatch.
- In `wal_replay.rs`, when replaying, if `ReconciliationDivergence` event found with no subsequent `ReconciliationCleared` event, set arbiter regime to HALT.
- Add `manual_clear_halt()` call path (already exists in RiskArbiter).

**P2-B: Ouroboros CLI Cleanup (P1-04)**
- In `ouroboros/cli.py`, wrap the pipeline call in `try/finally`.
- `finally` block: call `toml_writer.flush_all()` or equivalent.
- Register `atexit` handler as backup.

**P2-C: Daily Reset via WAL**
- The engine currently has no daily reset protocol. V1 had a sophisticated daily reset (clear chain boosts, sector state, refresh loss counters).
- Add `WalPayload::DailyReset { date: String, previous_equity: f64, new_equity: f64 }`.
- In `engine.rs` main loop, check if `clock.is_new_trading_day()` (compare stored date vs current date). If new day:
  - Reset `portfolio.consecutive_stop_losses = 0`
  - Reset `portfolio.daily_high_watermark = portfolio.equity`
  - Write DailyReset to WAL
  - Log reset

**Build gate**: Ralph Wiggum. All tests green. Deploy to EC2. Verify WAL contains DailyReset events after overnight.

---

### PHASE 3: DEAD CODE RESURRECTION (Wire the built-but-unwired modules)

**Objective**: Connect every completed module that improves signal quality, execution quality, or risk management. These modules are already written and tested. They just need to be called from the main loop.

**⚠️ CRITICAL RULE**: Each module wiring is a SEPARATE sub-phase. Run Ralph Wiggum after EACH one. Do not wire two modules at once. If wiring module X breaks the build, fix X before touching Y.

**P3-A: GarchInference (garch_inference.rs)**
- This module provides O(1) per-tick volatility forecasts using GARCH(1,1).
- Wire into the main loop: after receiving a tick, call `garch_registry.update(ticker_id, log_return)`.
- Pass the GARCH sigma to `EvalContext.garch_sigma` (currently hardcoded to 0.30 in the default).
- Pass to `TickContext` for Python bridge (currently using bar-based `realized_vol`).
- The Ouroboros nightly `step_0_garch_calibration.py` already outputs GARCH params. Load them via `ouroboros_loader::load_garch_params()` at startup and seed the GarchInference registry.

**P3-B: HotScanner + Apex Path (scanner.rs)**
- HotScanner provides momentum/anomaly detection for Apex-class tickers.
- In the main loop (main.rs:446-449), the Apex path is currently a comment. Wire:
  ```
  RouteResult::Apex(t) → HotScanner.evaluate(t) → if signal, run through RiskArbiter → submit order
  ```
- HotScanner has two scanners: `HotScanner` (momentum breakouts) and `RotationScanner` (sector rotation). Wire HotScanner first; RotationScanner depends on multi-ticker correlation (Phase 5).
- ApexScout (Python) is a RVOL anomaly detector. Call it from bridge.py when Apex ticks arrive. Requires adding `apex_scout.evaluate(ticks)` call path in bridge.py.

**P3-C: Executioner (exit_engine.rs:537-646)**
- The Executioner manages order lifecycle: `Pending → Submitted → Acknowledged → Filled/Cancelled`.
- Currently, orders are submitted via `broker.submit_order()` and fills are detected by polling broker events. There is no timeout, no retry, no partial fill handling beyond the basic WAL recording.
- Wire Executioner:
  1. In `engine.rs`, create `Executioner::new()` as a field of `Engine`.
  2. When submitting ANY order (buy or sell), call `executioner.track_order()`.
  3. When processing broker events, call `executioner.update_lifecycle()` and `executioner.record_fill()`.
  4. In the main loop, periodically call `executioner.stale_unacked()` and `executioner.stale_unfilled()` to detect stuck orders.
  5. For stale unacked orders: cancel and resubmit (up to `max_retries = 3`).
  6. For stale unfilled orders: cancel, write WAL event, log warning.
  7. Call `executioner.prune_completed()` every reconciliation cycle.

**P3-D: SmartRouter (smart_router.rs)**
- Cost-based order routing. Currently all orders go directly to LSE via IBKR.
- SmartRouter evaluates: spread, queue position, commission, latency, and routes to the cheapest venue.
- For Crucible phase with 12 LSE ETPs, SmartRouter is low-impact (all orders go to LSE anyway). But wiring it now establishes the abstraction for multi-venue support later.
- Wire: Replace direct `broker.submit_order()` calls with `smart_router.route(order_intent)` → which internally calls `broker.submit_order()`.

**P3-E: SubscriptionManager (subscription_manager.rs)**
- Dynamic 100-line rotation: 50 permanent (Tier 1) + 50 rotating (Tier 2, 60-second rotation batches).
- Currently all 12 core contracts are subscribed at startup and never rotated.
- Wire: In the main loop, periodically call `subscription_manager.rotate()`. This manages `reqMktData()` and `cancelMktData()` to stay within IBKR's 100-line limit while scanning the full universe.
- **Crucible override**: In Crucible mode (12 tickers), SubscriptionManager is a no-op (all 12 fit within 100 lines). Wire it anyway so the code path is tested.

**Build gate**: Ralph Wiggum after EACH sub-phase (A through E). All tests green after each. Deploy to EC2 after all 5 complete.

---

### PHASE 4: TELEMETRY + HARDENING

**Objective**: Make the engine observable and self-protecting.

**P4-A: Telemetry (telemetry.rs)**
- Lock-free atomic counters for: ticks_received, ticks_processed, signals_generated, signals_vetoed, orders_submitted, orders_filled, orders_cancelled, latency_ns_p50, latency_ns_p99.
- Wire: In main loop, call `telemetry.record_tick()`, `telemetry.record_signal()`, etc. at appropriate points.
- Add periodic log (every 5 minutes, aligned with reconciliation) that dumps all counters.
- Telemetry is READ-ONLY observation. It must NEVER block the main loop. It must NEVER cause allocation in the hot path. Atomics only. No mutexes. No channels.

**P4-B: Hardening Module (hardening.rs)**
- H1-H109 safety invariants as runtime checks.
- Wire: Call `hardening::verify_invariants()` at startup and every reconciliation cycle.
- If any invariant fails: log the failure, escalate regime to REDUCE (not HALT — hardening failures are warnings, not fatal errors, unless they indicate data corruption).
- Known H-checks that matter most for Crucible:
  - H20: IS_LIVE == false (already hardcoded in main.rs:27)
  - H25: Disk space > 5% (already wired via WAL disk_check_fn)
  - H34: Max positions (already in RiskArbiter CHECK 6)
  - H61: Zero-division guards (enforced at Python layer)
  - H67: Shadow stops only (enforced by ExitEngine design)
  - H68: Stop ratchet (enforced in ExitEngine.update_tracking)

**P4-C: InfiniteChandelier Integration (exit_engine.rs:445-490)**
- InfiniteChandelier wraps ChandelierStrategy with 8 adaptive multipliers (volatility, correlation, time_decay, momentum, liquidity, heat, regime, mega_runner).
- Currently ExitEngine uses basic ChandelierStrategy. InfiniteChandelier is built, implements ExitStrategy trait, but is never instantiated.
- Wire: Add a config flag `use_infinite_chandelier: bool` to ExitConfig. When true, construct `InfiniteChandelier` instead of `ChandelierStrategy` in `ExitEngine::with_default_chandelier()`.
- In the main loop, before `exit_engine.evaluate()`, call multiplier update methods:
  ```rust
  infinite.multipliers.update_volatility(realized_vol);
  infinite.multipliers.update_time_decay(time_fraction);
  infinite.multipliers.update_momentum(momentum_pct);
  infinite.multipliers.update_heat(portfolio_heat_pct);
  infinite.multipliers.update_regime(regime == RiskRegime::Reduce);
  ```
- **IMPORTANT**: InfiniteChandelier.set_trail_atr() must delegate to `self.base.rung5_trail_atr = mult` so Ouroboros calibration still works through the trait interface. Verify this is implemented (if not, add it).

**Build gate**: Ralph Wiggum. All tests green. Deploy to EC2.

---

### PHASE 5: QUANTITATIVE MATH PATCHES (from AEGIS_MASTER_PLAN_v30.md)

**Objective**: Upgrade the mathematical foundations where the current implementation is provably suboptimal.

**P5-A: QM-1 — EVT on GARCH Residuals (McNeil & Frey 2000)**
- Current: EVT would be applied to raw tick returns (if it were wired). Raw returns violate IID assumption due to volatility clustering.
- Fix: Apply EVT to GARCH(1,1) standardized residuals. Since GarchInference is now wired (Phase 3-A), extract residuals: `ε_t = (r_t - μ) / σ_t` where `σ_t` is the GARCH conditional vol.
- New file: `garch_evt.rs` (~500 LOC). Implement GPD tail fit on residuals.
- Wire into RiskArbiter as an additional CVaR check (upgrade existing CHECK 24).

**P5-B: QM-4 — Student-t Kalman (Roth et al. 2013)**
- `student_t_kalman.rs` already exists (285 LOC).
- Wire: Use for price smoothing in HotScanner (Phase 3-B). Replace raw price with Kalman-filtered price for momentum computation.
- The Student-t measurement noise model rejects spoofed quotes (Mahalanobis-weighted update).

**P5-C: QM-3 — Log-Transform Thompson Sampling (Russo et al. 2018)**
- For multi-ticker allocation. Gaussian Bandit penalizes positive skew (momentum winners).
- New file: `log_thompson_sampler.rs` (~400 LOC).
- Wire into SubscriptionManager (Phase 3-E) for Tier 2 rotation allocation.
- Not critical for Crucible (single position), but establishes the allocation framework for post-Crucible scaling.

**P5-D: QM-2 — Hayashi-Yoshida Covariance (Hayashi & Yoshida 2005)**
- For async tick correlation. Currently `correlation: 0.0` is hardcoded at main.rs:395.
- New file: `hayashi_yoshida.rs` (~400 LOC).
- Wire: Replace hardcoded 0.0 with H-Y computed cross-ticker correlation.
- Feeds into Kelly factor 4 (correlation penalty) and InfiniteChandelier multiplier (correlation).
- **Deferred to post-Crucible** in Crucible mode (single position = no cross-correlation). But scaffold the module now, wire later.

**Build gate**: Ralph Wiggum after each sub-phase. All tests green. Deploy to EC2.

---

### PHASE 6: GLOBAL MULTI-SESSION (Wire ALL remaining dead code + FREE US BONUS)

**Objective**: Transform the engine from a single-session LSE scanner into a 24-hour global scanning machine covering 6+ exchanges and 36,000+ tickers (including FREE US equities + options). Wire every remaining dead module. Zero dead code when done.

**⚠️ PREREQUISITE**: Subscribe to IBKR market data for all target exchanges BEFORE this phase. FREE US feeds already active. See PART 12 — IBKR SUBSCRIPTION PLAN.

**P6-A: Currency Module (currency.rs)**
- `FxRateTable` with 8 currencies (GBP, EUR, CHF, SEK, NOK, DKK, PLN, USD) and hardcoded defaults.
- Wire into `engine.rs`: create `FxRateTable::new()` as a field of Engine.
- Add Ouroboros nightly job to update FX rates from IBKR `reqHistoricalData()` for currency pairs (GBPJPY, GBPEUR, GBPUSD, GBPCHF, GBPAUD, GBPHKD).
- Wire `fx_table.to_gbp()` into:
  - `portfolio.rs`: convert non-GBP position values to GBP for heat/equity calculations
  - `risk_arbiter.rs`: convert position value for ISA annual limit check
  - `kelly_12factor.py`: pass equity in local currency for share calculation
- Call `fx_table.is_stale(now_ns)` every reconciliation cycle. If stale (>24h), log warning but do NOT halt (use defaults).

**P6-B: Exchange Profile Registry (exchange_profile.rs)**
- `ExchangeRegistry` with 15 European exchanges + FTT calculations (French 0.3%, UK 0.5% stamp duty, Italian 0.1%).
- Wire into SmartRouter: `smart_router.rs` already imports `ExchangeProfile` for FTT cost calculation. Ensure `round_tick()` is called before all order submissions.
- Wire tick rounding: In `engine.rs` order submission code, call `exchange_profile.round_tick(price)` before passing to broker. LSE tick sizes: < £1.00 → 0.001, ≥ £1.00 → 0.01 (already in config.toml `[execution]`).

**P6-C: ISA Gate (isa_gate.rs)**
- `IsaGate` with exchange blocklist (Taiwan TWSE/XTAI, China XSHG/XSHE, India XBOM/XNSE) and annual deposit tracking.
- Wire:
  1. Create `IsaGate::new(tax_year_start)` in Engine.
  2. Call `isa_gate.check(exchange_mic, trade_value_gbp)` BEFORE every order submission. If `BlockedExchange` → reject. If `DepositLimitExceeded` → reject.
  3. Call `isa_gate.record_deposit(amount_gbp)` after every buy fill.
  4. Call `isa_gate.new_tax_year()` on April 6 (in daily reset).
  5. This REPLACES the simple `isa_year_invested` counter in portfolio.rs. IsaGate is the authoritative ISA compliance module.

**P6-D: Asian Session (asian_session.rs)**
- `AsianSession` with 6 exchanges: XTKS (Tokyo), XHKG (Hong Kong), XASX (Australia), XSES (Singapore), XKRX (Korea), XNZE (New Zealand).
- Wire into main loop:
  1. Create `AsianSession::new()` in Engine.
  2. In the main loop time check, call `asian_session.is_mode_a(utc_secs)`. Mode A = Asian session window (23:00-08:00 UTC).
  3. During Mode A, the engine scans Asian tickers via SubscriptionManager rotation. LSE entries are blocked (market closed). Asian signal generation runs through HotScanner.
  4. For each tick from an Asian exchange, call `asian_session.is_exchange_open(mic, utc_secs)` — only process if open (handles lunch breaks: TSE 11:30-12:30 UTC, XHKG 12:00-13:00 UTC).
  5. Call `asian_session.isa_eligible(mic, &isa_gate)` — blocks Taiwan (TSMC direct) and routes to LSE equivalent (TSM3.L).
  6. At Asian session close (varies by exchange), aggregate session return, winner/loser counts, and call `cross_timezone.update_asian_close()`.

**P6-E: European Session (european_session.rs)**
- `EuropeanSession` wrapping `ExchangeRegistry` for 15 European exchanges.
- Wire into main loop:
  1. Create `EuropeanSession::new()` in Engine.
  2. Call `european_session.is_mode_b(utc_secs)` — Mode B = European session window (07:00-16:30 UTC).
  3. Call `european_session.entry_allowed(mic, utc_secs)` before every entry signal evaluation — blocks during closing auctions and outside hours.
  4. Call `european_session.next_close_utc_secs(utc_secs)` for EOD flatten time calculation (replace hardcoded 16:25 London with exchange-specific closing times).
  5. At European session open, calculate opening gap vs Asian close and call `cross_timezone.update_european_open(gap_pct)`.

**P6-F: Cross-Timezone Intelligence (cross_timezone.rs)**
- `CrossTimezoneEngine` aggregates Asian close sentiment and European open gap into a risk signal.
- Wire:
  1. Create `CrossTimezoneEngine::new()` in Engine.
  2. Call `update_asian_close()` at each Asian exchange close (P6-D step 6).
  3. Call `update_european_open()` at European session open (P6-E step 5).
  4. Call `carry_risk()` every reconciliation cycle with carry position count + unrealized P&L.
  5. If `should_reduce_exposure()` returns true → escalate RiskArbiter regime to REDUCE.
  6. Pass `sentiment_summary()` to telemetry snapshot for dashboard display.

**P6-G: Overnight Carry (overnight_carry.rs)**
- `CarryManager` handles positions carried across trading sessions (frozen stops, state transitions).
- Wire:
  1. Create `CarryManager::new()` in Engine.
  2. At Mode B close (LSE 16:30), call `carry_manager.carry_all_open(now_ns)` — transitions all open positions to `CarryState::Carried`, freezes Chandelier stops.
  3. At Mode A open (or next Mode B open), call `carry_manager.reactivate_all(&current_prices)` — unfreezes stops with 3% floor protection.
  4. In ExitEngine, check `carry_manager.get(ticker_id).is_stop_frozen()` — if true, skip Chandelier stop updates (position in carry mode).
  5. Feed `carry_manager.total_carry_pnl()` into `CrossTimezoneEngine.carry_risk()`.

**P6-H: US Equities & Options (FREE BONUS FEEDS NOW ACTIVE)**
- 🎉 **NEW**: FREE US Equities (15,000+ tickers) + US Options (200,000+ contracts)
- Already active via IBKR — no subscription cost
- Integrate into SubscriptionManager: 20-30 high-conviction US stocks in Tier 2 rotation
- Use for ES futures correlation tracking (via 3LUS.L proxy)
- Monitor US vol during European close window (16:30-21:00 UTC) for cross-market signals

**P6-I: Apex Scout Python Integration**
- `apex_scout.py` is a RVOL anomaly detector for Apex-class tickers (60-second OHLCV snapshots).
- Wire into bridge.py:
  1. Add import: `from brain.strategies.apex_scout import evaluate as apex_evaluate`
  2. Add new message type `"apex_snapshot"` in bridge.py `process_tick()`.
  3. When msg type is `"apex_snapshot"`, call `apex_evaluate(snapshots)` instead of `vanguard_evaluate(ticks)`.
  4. In `python_bridge.rs`, add `evaluate_apex_snapshot()` method that sends the apex message type.
  5. In main loop, on `RouteResult::Apex(t)`, accumulate 60-second snapshots and send to Python bridge as `apex_snapshot`.

**P6-J: Hayashi-Yoshida Wiring (QM-2)**
- Now that multi-session ticks flow from Asian + European + LSE + US, cross-timezone correlation is meaningful.
- Wire `hayashi_yoshida.rs` (if written in Phase 5-D) to compute real-time correlation between:
  - ES futures (via 3LUS.L, direct US feed) and LSE ETPs
  - Nikkei (proxy via Asian session tickers) and technology ETPs
  - S&P 500 (direct US feed) and broad market LSE ETPs
- Replace `correlation: 0.0` in main.rs:395 with H-Y computed value.
- Feed into Kelly factor 4 and InfiniteChandelier correlation multiplier.

**P6-K: US Equities & Options Integration (FREE BONUS)**
- 🎉 **FREE data feeds now ACTIVE**: US Equities (15,000+ tickers, L1+L2) + US Options (200,000+ contracts, L1)
- US market hours: 14:30-21:00 UTC (overlaps with European session until 16:30 UTC)
- Add US tickers to SubscriptionManager Tier 2 rotation (50 rotating lines include ~20 high-conviction US stocks)
- US equity candidates: S&P 500 leveraged proxies already covered (3LUS.L, 3USS.L, SP5L.L are LSE equivalents)
- Phase 6-K discovery: Use free US feeds to:
  1. Monitor ES (E-mini S&P 500 futures equivalent) correlation via 3LUS.L + direct US feed
  2. Scan high-vol US individual stocks during European close (16:30-21:00 UTC)
  3. Cross-validate LSE vol forecasts against US underlying (lower latency for volume spikes)
  4. Use US options chain for IV surface modeling (optional, not mandatory for Crucible)
- No ISA constraint on US feeds themselves, but any US equity trades still require ISA-eligible wrapper (convert to LSE ETP equivalent)

**Build gate**: Ralph Wiggum after EACH sub-phase (A through K). Deploy to EC2 after all complete.

---

### PHASE 7: TERMINAL DASHBOARD (Live system visibility)

**Objective**: Build a read-only terminal dashboard that shows everything in one place — trades, P&L, regime, ticks, signals, vetoes, positions, and system health. This is NOT a web dashboard. It is a Python `rich` terminal app that tails the WAL file and displays live state.

**P7-A: WAL Tail Dashboard**
- New file: `dashboard/wal_dashboard.py` (~300 LOC)
- Uses `rich` library (pip install rich) for terminal rendering.
- Reads WAL file (`current.ndjson`) via `tail -f` equivalent (inotify or poll).
- Parses NDJSON events in real-time.
- Displays 6 panels:

```
┌─ AEGIS V2 Dashboard ──────────────────────────────────────────────┐
│                                                                    │
│ ┌─ EQUITY & P&L ───────────┐  ┌─ SYSTEM STATE ──────────────────┐│
│ │ Starting:  £10,000.00    │  │ Regime:    NORMAL               ││
│ │ Current:   £10,247.33    │  │ Mode:      B (European)         ││
│ │ Daily P&L: +£47.33       │  │ Ticks/s:   842                  ││
│ │ Total P&L: +£247.33      │  │ Signals:   3 today              ││
│ │ Win Rate:  42.3% (19/45) │  │ Vetoes:    47 today             ││
│ │ Max DD:    -1.8%          │  │ Python:    ALIVE                ││
│ │ Sharpe:    0.87           │  │ WAL depth: 12,847 events        ││
│ └──────────────────────────┘  └──────────────────────────────────┘│
│                                                                    │
│ ┌─ OPEN POSITIONS ──────────────────────────────────────────────┐ │
│ │ QQQ3.L  Long  150 shares  @ £42.33  → £42.87  +1.28%  R3    │ │
│ │ Stop: £42.10 (Chandelier R3)  ATR: 0.45  Carry: No          │ │
│ └──────────────────────────────────────────────────────────────┘ │
│                                                                    │
│ ┌─ LAST 10 SIGNALS ────────────────────────────────────────────┐ │
│ │ 14:23:47  QQQ3.L   APPROVED  conf=78  kelly=0.08  150 shares│ │
│ │ 14:21:12  NVD3.L   VETOED    SpreadTooWide (0.62%)          │ │
│ │ 14:18:33  3SEM.L   VETOED    ConfidenceBelowFloor (58)      │ │
│ │ ...                                                          │ │
│ └──────────────────────────────────────────────────────────────┘ │
│                                                                    │
│ ┌─ LAST 10 TRADES ─────────────────────────────────────────────┐ │
│ │ #45  QQQ3.L  +£12.40  +0.82%  Chandelier R4  2h 14m        │ │
│ │ #44  3LUS.L  -£8.20   -0.54%  HardStop       0h 47m        │ │
│ │ #43  NVD3.L  +£31.70  +2.11%  EOD Flatten    5h 22m        │ │
│ │ ...                                                          │ │
│ └──────────────────────────────────────────────────────────────┘ │
│                                                                    │
│ ┌─ EXCHANGE STATUS ────────────────────────────────────────────┐ │
│ │ LSE:  OPEN   08:00-16:30  12 tickers  │  Ticks: 4,230      │ │
│ │ TSE:  CLOSED            3,900 tickers  │  Next: 00:00 UTC   │ │
│ │ XETR: OPEN   07:00-15:30  100 tickers  │  Ticks: 1,847     │ │
│ │ HKEX: CLOSED            2,500 tickers  │  Next: 01:30 UTC   │ │
│ └──────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────┘
```

- **Run locally**: `python dashboard/wal_dashboard.py --wal-host 3.230.44.22 --ssh-key ~/.ssh/nzt48-key.pem`
- Uses SSH tunnel to read WAL from EC2: `ssh -L 9999:localhost:9999 ubuntu@3.230.44.22`
- OR: `scp` the WAL file periodically (every 5s) and parse locally.
- **Zero impact on engine**: Dashboard is a separate process. It reads the WAL file. It never writes. It never connects to IBKR. It never touches the engine process.

**P7-B: Telemetry Snapshot Endpoint**
- Add a minimal Unix domain socket in the engine that exposes `telemetry.snapshot()` as JSON.
- Dashboard connects via SSH tunnel and reads the snapshot every 5 seconds.
- This provides tick rate, signal rate, latency percentiles, and veto breakdowns that aren't in the WAL.
- **Alternative (simpler)**: Engine writes `telemetry_snapshot.json` to `/app/events/` every 30 seconds. Dashboard reads it. No socket needed.

**Build gate**: Dashboard is a standalone Python script. No Rust changes needed. Test locally against a sample WAL file.

---

### PHASES 8-25: ADVANCED OPTIMIZATION & SCALING (Roadmap)

**PHASE 8: Ouroboros Nightly Pipeline Hardening**
- Full 10-step Ouroboros cycle: GARCH calibration → Bayesian WR → DSR → Kelly accelerator → Exit calibration → Regime hunting → Alpha sieve → TOML write → FX refresh → DynamicWeights application
- Verify all nightly artifacts persist correctly, no corrupted TOML files, 18:00 ET cron executes without skipping days
- Full test: 30-day synthetic backtest, verify DynamicWeights applied to next day's engine

**PHASE 9: Cross-Asset Macro Integration**
- Wire macro feeds: VIX, DXY, Credit spreads, Fear & Greed Index
- Integrate into RiskArbiter regime detection: macro deterioration → escalate regime toward HALT
- Macro signals flow into Ouroboros regime hunting step

**PHASE 10: Multi-Frame Volatility Analysis**
- Add 1m, 5m, 15m, 60m, daily volatility frames alongside existing 5-second L1 ticks
- Use for GARCH calibration on multiple timeframes
- Smart frame selection: use shorter frames during illiquidity, longer during high conviction

**PHASE 11: Sector Rotation Intelligence**
- Map ISA contracts to 5 sectors: Technology (QQQ3, GPT3, NVD3, TSL3, TSM3), US Broad (3LUS, 3USS, SP5), Semiconductors (3SEM, NVD3, TSM3, MU2)
- Sector heat tracking: avoid over-concentration in single sector
- Ouroboros learns sector rotation patterns across 100-trade validation gate

**PHASE 12: Predictive Scoring (Alpha Sieve Enhancement)**
- Extend Ouroboros Alpha Sieve with machine learning: decision tree on IC (Information Coefficient) breakdown by ticker, time-of-day, regime, macro state
- Lock underperforming tickers automatically after 5 consecutive negative trades

**PHASE 13: LSE Leveraged Registry Scraper**
- Auto-scrape all LSE leveraged ETPs nightly: identify new products, delisted products, fee changes
- Update contracts.toml automatically; alert on fund closures (hard stops all positions)
- Integrated with Phase 6 AsianSession + EuropeanSession for global ETP universe

**PHASE 14: QuoteImbalance Circuit Breaker**
- Detect spoofed quotes: if bid/ask spread widens 10x in < 100ms, drop the tick and escalate regime to REDUCE
- WAL event SC-17: QuoteImbalanceInvalidated with drop count
- Resilience: resume processing once spread normalizes

**PHASE 15: Split Adjustment Handler**
- Monitor IBKR events for stock splits/spin-offs (SC-13 WAL event)
- Auto-adjust all open positions and stop losses
- Verify no arbitrage leakage through splits

**PHASE 16: Liquidation Cascade Defense**
- Monitor portfolio heat vs ISA ceiling: if remaining allowance < 3% of equity, stop ALL new entries immediately
- Monitor daily drawdown: if DD > 2%, move to FLATTEN regime
- Monitor consecutive stop losses: 3 in a row → HALT (Blood Oath H12)

**PHASE 17: Broker Connection Resilience**
- Add PanicGuard handler: if IBKR disconnects for > 120 seconds, set regime to HALT
- Add CircuitBreaker: if fill error rate > 5% in 1 minute, escalate to REDUCE
- Automatic reconnect with exponential backoff (jittered)

**PHASE 18: WAL Compression & Archival**
- After 1M events, start new WAL file, gzip previous file to `/app/events/archive/`
- Monthly archive rotation: keep rolling 3-month window in `/app/events/`
- Ouroboros reads from archive for backtesting/offline calibration

**PHASE 19: State Hash Checkpointing (H85)**
- Every 1 hour, write state hash (portfolio + positions + regime + FX rates) to WAL
- On startup, verify last checkpoint matches replayed state from WAL
- If divergence detected: reconciliation audit log + manual_clear_halt required

**PHASE 20: Performance Telemetry Dashboard (Phase 7 Extension)**
- Add live Sharpe ratio calculation (updated every 5 min on rolling 30-day returns)
- Add Win Rate trending (30-trade, 60-trade, 100-trade windows)
- Add sector heatmap with real-time allocation percentages

**PHASE 21: Multi-Session Regime Transitions**
- Implement 5-mode TradingMode enum: DARK (no trading), MODE_A (Asian), MODE_B (European), AUCTION (opening/closing), CARRY (overnight)
- Automatic regime escalation based on mode transitions: MODE_B close → carry freeze, MODE_A open → reactivation checks
- Telemetry publishes current mode every second to dashboard

**PHASE 22: Latency Profiling & Optimization**
- Profile tick-to-trade latency: identify Python bridge bottleneck vs order submission latency vs fill confirmation latency
- Target: < 500ms T2T (tick-to-trade), < 100ms order submission latency
- Optimize hot paths: remove allocations in tick processing, batch Python bridge calls

**PHASE 23: Ouroboros Offline Calibration**
- Build standalone Ouroboros calibration tool: reads WAL archive, outputs DynamicWeights WITHOUT running live
- Use for parameter sensitivity analysis: how do GARCH params change optimal Kelly fractions?
- Enables A/B testing: calibrate alternate DynamicWeights, compare backtest performance

**PHASE 24: Terminal Dashboard Advanced Features**
- Add sector heatmap panel (Phase 20)
- Add latency percentiles panel (Phase 22)
- Add mode clock panel (5-mode status bar at top)
- Add regime escalation reason (last CHECK that triggered escalation)
- Live updating every 200ms (async with WAL tail)

**PHASE 25: Live Capital Readiness (Post-Crucible)**
- Conduct 63-day gauntlet: 100+ validated trades, WR ≥ 40%, Sharpe > 0, DD < 8%
- Human review of all 100+ trades
- Approval for IS_LIVE = true transition
- Final safety audit: all 16 Runtime Invariants verified 100/100 times
- Deployment to live AWS ISA brokerage account with £1,000 initial capital

---

## PART 6 — CONFIGURATION REFERENCE (Every parameter, its source, its meaning)

### config/config.toml (128 lines, 14 sections)

**[signal]** — Signal filtering thresholds
| Key | Value | Meaning |
|-----|-------|---------|
| confidence_floor | 65 | Minimum signal confidence to pass RiskArbiter CHECK 10 |
| outlier_win_cap_pct | 3.0 | Cap individual trade returns at 3% for Kelly avg (H62) |
| gap_detection_pct | 2.0 | Price gap threshold for gap cooldown |
| erroneous_tick_deviation_pct | 5.0 | Tick rejected if > 5% from prior price |
| velocity_check_window_secs | 1 | Window for velocity check (H37) |
| velocity_check_max_intents | 5 | Max order intents per ticker per window |

**[position]** — Position limits
| Key | Value | Meaning |
|-----|-------|---------|
| max_simultaneous_positions | 3 | Plan max (Crucible override: 1) |
| portfolio_heat_limit_pct | 6.0 | Max portfolio heat before entry veto |
| sector_heat_cap_pct | 33.0 | Max sector exposure |
| cash_buffer_pct | 10.0 | Min cash reserve (H31) |
| isa_annual_limit_gbp | 20000 | UK ISA annual limit |
| isa_tax_year_start | "04-06" | April 6 rollover |

**[kelly]** — Position sizing
| Key | Value | Meaning |
|-----|-------|---------|
| fraction_cap | 0.5 | Half-Kelly cap (Factor 11) |
| clamp_max | 0.20 | Absolute max Kelly fraction (H57) |
| volatility_drag_3x | 9 | Variance multiplier for 3x ETPs (H59) |
| volatility_drag_5x | 25 | Variance multiplier for 5x ETPs (H59) |

**[timing]** — Market hours and cutoffs
| Key | Value | Meaning |
|-----|-------|---------|
| stale_data_threshold_secs | 120 | Data older than 120s → HALT |
| entry_cutoff_london | "15:45" | No entries after 15:45 London |
| lse_open_london | "08:00" | LSE continuous trading open |
| lse_close_london | "16:30" | LSE continuous trading close |
| auction_open_start/end | "07:50"/"08:00" | Opening auction |
| auction_close_start/end | "16:30"/"16:35" | Closing auction |
| eod_flatten_time | "16:25" | EOD flatten trigger |
| gap_cooldown_mins | 15 | Cooldown after gap detection |
| synthetic_halt_limp_secs | 30 | Limp mode on synthetic halt |
| synthetic_halt_full_secs | 120 | Full halt on synthetic halt |

**[risk]** — Risk management
| Key | Value | Meaning |
|-----|-------|---------|
| daily_drawdown_pct | 2.0 | Daily DD > 2% → FLATTEN |
| spread_veto_pct | 0.5 | Spread > 0.5% → reject entry |
| slippage_assumption_pct | 1.0 | Assumed slippage for size calc |
| consecutive_loss_halt | 3 | 3 consecutive stop-losses → HALT |

**[channel]** — Tick channel backpressure
| Key | Value | Meaning |
|-----|-------|---------|
| capacity | 50000 | Bounded channel capacity |
| reduce_threshold | 40000 | > 40K → escalate to REDUCE |
| halt_threshold | 50000 | Full → escalate to HALT |

**[ibkr]** — IBKR connection
| Key | Value | Meaning |
|-----|-------|---------|
| client_id_executioner | 101 | Engine's IBKR client ID |
| client_id_ouroboros | 200 | Ouroboros's IBKR client ID |
| rate_limit_msgs_per_sec | 50 | IBKR API rate limit |
| reqmktdata_pacing_ms | 10 | Pacing between reqMktData calls |
| max_simultaneous_lines | 100 | Max concurrent data lines |

**[crucible]** — Paper trading overrides
| Key | Value | Meaning |
|-----|-------|---------|
| max_positions_override | 1 | Single position during validation |
| paper_mode | true | Paper trading (H20) |
| starting_equity_gbp | 10000 | Starting virtual equity |

### config/contracts.toml (12 core ISA contracts)
| Symbol | Leverage | Exchange | Sector |
|--------|----------|----------|--------|
| QQQ3.L | 3x | LSE | Technology |
| QQQS.L | -3x | LSE | Technology |
| QQQ5.L | 5x | LSE | Technology |
| 3LUS.L | 3x | LSE | US_Broad |
| 3USS.L | -3x | LSE | US_Broad |
| SP5L.L | 5x | LSE | US_Broad |
| 3SEM.L | 3x | LSE | Semiconductors |
| NVD3.L | 3x | LSE | Semiconductors |
| TSL3.L | 3x | LSE | Single_Stock |
| GPT3.L | 3x | LSE | Technology |
| TSM3.L | 3x | LSE | Semiconductors |
| MU2.L | 2x | LSE | Semiconductors |

### Inverse Pairs (mutual exclusion, H32):
- QQQ3.L ↔ QQQS.L (can't hold both — long and short of same index)
- 3LUS.L ↔ 3USS.L (same for S&P 500 3x)

---

## PART 7 — DEPLOYMENT PROTOCOL

### Local → EC2 Transfer
```bash
rsync -avz --exclude '.git' --exclude 'target' --exclude '.venv' --exclude '__pycache__' \
  -e "ssh -i ~/.ssh/nzt48-key.pem" \
  /Users/rr/nzt48-signals/nzt48-aegis-v2/ ubuntu@3.230.44.22:/home/ubuntu/nzt48-aegis-v2/
```

### EC2 Build + Deploy
```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  "cd /home/ubuntu/nzt48-aegis-v2 && docker compose up -d --build"
```

### Build time: ~3-5 minutes (Rust release build on c7i-flex.large, 2 vCPU, 4GB RAM).
If OOM during compile: add `CARGO_BUILD_JOBS=1` to Dockerfile `RUN cargo build` line.

### Verification Checklist (run after every deploy)
```bash
# 1. Container health
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  "docker ps --format 'table {{.Names}}\t{{.Status}}'"
# Expected: aegis-v2 (Up), aegis-ib-gateway (Up, healthy), aegis-redis (Up, healthy)

# 2. Engine logs (last 50 lines)
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  "docker logs aegis-v2 2>&1 | tail -50"
# Expected: ticker subscriptions, Python bridge alive, DynamicWeights APPLIED, no PANIC/ERROR

# 3. WAL integrity
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  "docker exec aegis-v2 wc -l /app/events/current.ndjson"
# Expected: growing line count (each tick event, signal, order, fill, regime change)

# 4. Ouroboros cron
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  "docker exec aegis-v2 cat /app/crontab"
# Expected: "0 18 * * 1-5" (after P1-F fix)

# 5. Real-time ticks (CRITICAL — verify not delayed/frozen)
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  "docker logs aegis-v2 2>&1 | grep -i 'tick\|signal\|veto\|order' | tail -20"
# Expected: recent timestamps, not "delayed" or "frozen"
```

### Rollback
If deployment breaks the engine:
```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  "cd /home/ubuntu/nzt48-aegis-v2 && git checkout HEAD~1 -- . && docker compose up -d --build"
```

---

## PART 8 — OUROBOROS LEARNING SYSTEM (How the engine learns)

### 10-Step Nightly Pipeline

| Step | Module | Input | Output | Academic Basis |
|------|--------|-------|--------|----------------|
| 0 | step_0_garch_calibration.py | IBKR historical bars | GARCH(1,1) params per ticker | Bollerslev (1986) |
| 1 | wal_reader.py | WAL NDJSON | Parsed trade records | — |
| 2 | bayesian.py | Trade W/L | Bayesian win rate (Laplace smoothing) | — |
| 3 | bayesian.py | Trade returns | Deflated Sharpe Ratio | Bailey & Lopez de Prado (2014) |
| 4 | kelly_accelerator.py | Win rate, avg W/L | Per-ticker Kelly fractions | Kelly (1956), Thorp (2006) |
| 5 | exit_calibration.py | MFE/MAE from WAL | Chandelier ATR multiplier [1.5, 4.0] | Le Beau (1999) |
| 6 | regime_hunting.py | Returns + vol | Regime labels (bull/bear × quiet/volatile) | — |
| 7 | alpha_sieve.py | IC tracking | Alpha decay detection (lock tickers below IC threshold) | — |
| 8 | toml_writer.py | All above | dynamic_weights.toml + universe_classification.toml | — |
| 9 | cli.py | — | Orchestrator, archive to parameter_history/ | — |

### How Weights Flow to Engine

```
Ouroboros (nightly) → writes dynamic_weights.toml to /app/config/
                    → writes universe_classification.toml to /app/config/
Engine (next startup or hot-reload):
    → ouroboros_loader::load_dynamic_weights() reads TOML
    → DynamicWeights struct populated:
        bayesian_win_rate → TickContext.win_rate → Python bridge → Kelly factor 1
        chandelier_atr_mult → ExitEngine.strategy_mut().set_trail_atr(mult)
        regime_scales → RiskArbiter.regime_scales (adjusted_size calculation)
        kelly_fractions → RiskArbiter.kelly_fractions (per-ticker Kelly cap)
    → ouroboros_loader::load_universe_classification() reads TOML
    → UniverseClassification → Universe routing (Tier1/2 = Vanguard, Tier3/locked = Apex)
```

### Key Learning Constants
| Constant | Value | Source |
|----------|-------|--------|
| Laplace prior | wins=1, total=2 | ouroboros/config.py |
| Kelly learning rate α | 0.3 | ouroboros/config.py (KELLY_LEARNING_RATE) |
| Chandelier ATR mult range | [1.5, 4.0] | ouroboros/config.py |
| Chandelier ATR mult default | 3.0 | ouroboros/config.py |
| MFE rung5 threshold | 0.6 | ouroboros/config.py |
| ASER promote threshold | 0.8 | ouroboros/config.py |
| ASER demote threshold | 0.3 | ouroboros/config.py |
| IC warning threshold | 0.02 | ouroboros/config.py |
| IC lock threshold | 0.0 | ouroboros/config.py |
| Cold start days | 3 | ouroboros/config.py |
| Min trades for DSR | 10 | ouroboros/config.py |

---

## PART 9 — HARD RULES (Violations are unrecoverable errors)

1. **Ralph Wiggum after every code change.** Zero errors. Zero warnings. All tests green. Non-negotiable. The engine is live. Broken code deployed = lost trades.

2. **Wire, don't rewrite.** 40% of the codebase is built, tested, never called. These modules have tests. They have correct types. They implement the right traits. You are WIRING them into the main loop, not reimplementing them. If a module's API doesn't fit the call site, add a thin adapter — do not refactor the module.

3. **One module per sub-phase.** Wire GarchInference. Build gate. Then wire HotScanner. Build gate. Then wire Executioner. Build gate. Never wire two modules simultaneously. If X breaks, you know it's X, not Y.

4. **Do NOT build anything new** unless explicitly listed in this prompt. No Telegram bot. No PDF reports. No new strategies. No new indicators. The terminal dashboard (Phase 7) is the ONLY new deliverable — it is a standalone Python script that reads the WAL. Zero scope creep beyond what this prompt specifies.

5. **`#![deny(warnings)]` + `#![deny(clippy::unwrap_used)]`** — these two lines in lib.rs mean:
   - Every variable must be used immediately after declaration
   - Every import must be consumed
   - No `.unwrap()` — use `unwrap_or`, `unwrap_or_else`, `if let`, or `?`
   - No dead code paths unless explicitly `#[allow(dead_code)]`
   - You cannot "add a field now, wire it later" — the compiler rejects unused fields

6. **ExitStrategy is a trait object** (`Box<dyn ExitStrategy>`). You CANNOT downcast to ChandelierStrategy or InfiniteChandelier through the Box. Use the trait methods: `compute_stop()`, `compute_rung()`, `set_trail_atr()`. If you need ChandelierStrategy-specific behavior, add a method to the trait with a default no-op implementation.

7. **ISA constraint: Long only.** PythonBridge always returns `direction: "Long"`. RiskArbiter CHECK 1 immediately HALTs on `Direction::Short`. Inverse ETPs (QQQS.L, 3USS.L) are the mechanism for bearish exposure in an ISA. This is not a bug — it is a regulatory constraint.

8. **If stuck 20 consecutive attempts, STOP.** Report: file, line, error, what you tried, why it failed. Do not spiral. Do not delete code to make warnings go away. Do not add `#[allow(dead_code)]` to production code paths (only to explicitly deferred modules).

9. **Never ask "should I continue?"** Just continue. Execute phases 1 through 7 sequentially. Deploy after each phase. Verify after each deploy. Move to the next phase.

10. **Academic citations are not decorative.** Every mathematical model referenced in this prompt has a specific paper. If you modify the math, verify it against the source:
    - Kelly sizing: Kelly (1956), Thorp (2006)
    - Chandelier exit: Le Beau (1999)
    - GARCH: Bollerslev (1986)
    - EVT: McNeil & Frey (2000)
    - DSR: Bailey & Lopez de Prado (2014)
    - Moreira-Muir vol scaling: Moreira & Muir (2017)
    - Hayashi-Yoshida: Hayashi & Yoshida (2005)
    - Student-t Kalman: Roth et al. (2013)
    - Log-Thompson: Russo et al. (2018)
    - Leveraged ETP vol drag: Avellaneda & Zhang (2010)

---

## PART 10 — SUCCESS CRITERIA

### Per-Phase Criteria

| Phase | Complete When |
|-------|---------------|
| **P1: Truth Layer** | L1 bid/ask live, ISA counter incrementing, GARCH threshold scaled, fsync on all TOML writes, BST dates hardcoded, crontab at 18:00 ET, backoff has jitter. Build gate green. Deployed. |
| **P2: Persistence** | Reconciliation divergence persisted to WAL, Ouroboros CLI has cleanup, daily reset writes to WAL. Build gate green. Deployed. |
| **P3: Dead Code** | GarchInference wired and receiving updates, HotScanner wired for Apex ticks, Executioner tracking all orders, SmartRouter routing all orders, SubscriptionManager callable. Build gate green. Deployed. |
| **P4: Telemetry** | Lock-free counters recording ticks/signals/fills/latency, hardening invariants checked every 5min, InfiniteChandelier available via config flag. Build gate green. Deployed. |
| **P5: Math Patches** | GARCH-EVT on residuals, Student-t Kalman smoothing HotScanner, Log-Thompson allocated. Build gate green. Deployed. |
| **P6: Global Multi-Session** | Currency conversion live, ExchangeProfile tick rounding, IsaGate blocking non-ISA exchanges, AsianSession Mode A scanning, EuropeanSession entry gating, CrossTimezone sentiment aggregation, CarryManager freezing overnight stops, ApexScout wired to Python bridge, H-Y correlation replacing hardcoded 0.0. Build gate green. Deployed. IBKR subscriptions active for TSE + XETRA + HKEX + Euronext. |
| **P7: Terminal Dashboard** | WAL tail dashboard running locally, showing equity, P&L, positions, signals, trades, exchange status in 6 panels. Telemetry snapshot readable. Zero dead code remaining. |

### System-Level Success Criteria (Crucible Gate)

The engine PASSES the Crucible validation gate when ALL of these hold simultaneously over a minimum of 100 paper trades:

| Metric | Threshold | Measurement |
|--------|-----------|-------------|
| **Win Rate** | ≥ 40% | Wins / Total Trades |
| **Sharpe Ratio** | > 0 | Annualized Sharpe on daily returns |
| **Max Drawdown** | < 8% | Peak-to-trough equity drawdown |
| **Profit Factor** | > 1.0 | Gross Profit / Gross Loss |
| **Daily Return** | 0.3-0.5% net | Average daily PnL / Equity |
| **Trade Count** | ≥ 100 | Completed round trips |
| **Data Integrity** | 100% | All trades have WAL records, all exits have fill confirmations |
| **Regime Handling** | Verified | At least 1 REDUCE and 1 recovery during the 100-trade period |

**After the gate passes**: Human review of all 100 trades. Manual approval required. Then — and only then — does IS_LIVE change from `false` to `true`.

---

## PART 11 — WHAT DONE LOOKS LIKE

When all 7 phases are complete and deployed:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  AEGIS V2 — ALL GUNS BLAZING, EVERY FRONT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Mode:         Crucible (max 1 pos, £10K, paper)
  Data:         REALTIME L1 + 5s bars via IBKR           ✅
  Spread:       REAL bid/ask (L1 tick-by-tick)            ✅
  GARCH:        Per-tick vol forecast (O(1))              ✅
  Scanner:      HotScanner + VanguardSniper + ApexScout   ✅
  Risk:         31 checks, leverage-scaled, ISA-gated     ✅
  ISA Gate:     Exchange blocklist + £20K annual limit     ✅
  Routing:      SmartRouter + FTT + tick rounding          ✅
  Orders:       Executioner lifecycle (retry, timeout)     ✅
  Exit:         InfiniteChandelier (8 adaptive mults)      ✅
  Carry:        Overnight position management              ✅
  Currency:     8-currency FX conversion (GBP base)        ✅
  Persistence:  WAL + reconciliation audit + daily reset   ✅
  Learning:     Ouroboros nightly at 18:00 ET               ✅
  Telemetry:    Lock-free atomics, 5-min heartbeat         ✅
  Hardening:    Panic guard, circuit breakers, watchdog     ✅
  Dashboard:    Live terminal (equity, P&L, trades, state) ✅
  ────────────────────────────────────────────────────────────
  GLOBAL SCANNING — 6 EXCHANGES, 20,000+ TICKERS
  ────────────────────────────────────────────────────────────
  LSE:          12 core ETPs (continuous)        £25/mo     ✅
  TSE:          3,900+ tickers (Mode A scan)     ~£14/mo    ✅
  XETRA:        10,000+ tickers (Mode B scan)    ~£67/mo    ✅
  HKEX:         2,500+ tickers (Mode A scan)     ~£21/mo    ✅
  Euronext:     1,500+ tickers (Mode B scan)     ~£76/mo    ✅
  ASX:          2,200+ tickers (Mode A scan)     ~£60/mo    ✅
  ────────────────────────────────────────────────────────────
  Cross-Timezone: Asian sentiment → European gap → LSE     ✅
  Hayashi-Yoshida: Async correlation replacing 0.0          ✅
  Dead Code:    0% — ALL 48 modules wired                  ✅
  Deployed:     EC2 3.230.44.22                            ✅
  Status:       Accumulating toward 100-trade gate
                (WR ≥ 40%, Sharpe > 0, DD < 8%)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Ralph Wiggum: PASSED (N tests)
  Containers:   3/3 healthy
  Paper equity: £10,000 (starting)
  Modules wired: 48 of 48
  Total IBKR data cost: ~£263/month (Pro, all exchanges)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Then the engine scans 20,000+ tickers across 6 exchanges, 24 hours a day. Asian session feeds cross-timezone intelligence into European open. Overnight positions are managed with frozen stops. Every signal is routed through SmartRouter with FTT cost awareness. Every order is tracked through Executioner with retry logic. Every metric flows to the terminal dashboard. The system learns. The system adapts. The system survives.

**Execute Phases 1 through 7. All of them. Sequentially. With build gates. Now. Go.**

---

## PART 12 — MARKET DATA STRATEGY

### IBKR Non-Professional Account — Cost-Optimised Global Package (v3.0)

Account is classified as **Non-Professional** (status secured by user). Non-Pro rates are 60-75% cheaper than Professional. IBKR remains the only source of true real-time international bid/ask data. Strategy: aggressive scaling with cost savings.

#### Current Non-Professional Pricing (Confirmed from IBKR Account Pages)

| Exchange | Subscription Name | Monthly Cost (NP) | Tickers Available | Session | Status |
|----------|-------------------|--------------------|-------------------|---------|--------|
| **LSE UK** | UK LSE Equities (NP, L1) | **£7.00/mo** | ~1,300 | Mode B | ✅ ACTIVE |
| **LSE International** | European (BATS/Chi-X) Equities (NP, L2) | **FREE (waived)** | ~1,500 | Mode B | ✅ ACTIVE |
| **US Equities** | US Equities (NP, L1 + L2) | **FREE** | ~15,000+ | Mode B | ✅ ACTIVE NOW |
| **US Options** | US Options (NP, L1) | **FREE** | ~200,000+ | Mode B | ✅ ACTIVE NOW |
| **Spot Xetra** | Spot Market Germany (Frankfurt/Xetra) (NP, L2) | **€21.75/mo (~£19)** | ~10,000 | Mode B | RECOMMENDED |
| **TSE** | Japan (TSE) Equities (NP, L2) | **¥380/mo (~£2.40)** | ~3,900 | Mode A | ✅ SUBSCRIBED |
| **HKEX** | Hong Kong Securities (L2) | **HKD 225/mo (~£21)** | ~2,500 | Mode A | ✅ SUBSCRIBED (24h activating) |
| **SGX** | Singapore (SGX) Equities (L1 only, no L2 available) | **SGD 14.00/mo (~£7)** | ~700 | Mode A | OPTIONAL |
| **ASX** | ASX Total (NP, L2) | **AUD 25.00/mo (~£13)** | ~2,200 | Mode A | ✅ SUBSCRIBED (24h activating) |
| **Euronext** | Euronext Data Bundle (NP) | **€21.75/mo (~£19)** | ~1,500 | Mode B | DEFER |

**Total Monthly Cost (Current)**: £7 + £0 + £0 (FREE US) + £2.40 + £21 + £13 = **~£43.40/month** (LSE + US + TSE + HKEX + ASX)

**🎉 BONUS**: FREE US Equities (15,000+ tickers, L1+L2) and FREE US Options (200,000+ contracts, L1) — adds massive North American scanning capacity at ZERO additional cost!

**Tier 2 Additions**:
- Add Spot Xetra (€21.75 ~£19): **Total £62.40/month** for 6 exchanges (including US)
- Add Euronext (€21.75 ~£19): **Total £81.40/month** for 7 exchanges

**🌍 GLOBAL REACH NOW**:
- **Mode B (European)**: LSE (1,300 tickers) + European (1,500) + US (15,000+) + XETRA (10,000) = ~28,000 tickers
- **Mode A (Asian)**: TSE (3,900) + HKEX (2,500) + ASX (2,200) = ~8,600 tickers
- **Total Available**: ~36,600+ tickers + 200,000+ US options

#### Exchange Research Findings (Incorporated into Universe Selection)

**Singapore (SGX) — SKIP leveraged products, consider equities**:
- SGX offers Leverage Certificates (3x-7x) but they expire, have 13-19% p.a. gap premium, wide spreads, low liquidity. NOT suitable for leveraged ETP strategy.
- Individual SGX equities: potential but lower priority than ASX/HKEX.
- L2 data: NOT AVAILABLE (L1 only at SGD 14/mo).
- **Verdict**: Low ROI. Defer or skip.

**Australia (ASX) — SKIP leveraged ETPs, focus on high-vol equities**:
- No 3x+ leveraged ETPs available (max 2-2.75x, NOT ISA-eligible for leveraged tracking).
- BUT: High-volatility ISA-eligible individual equities are excellent scanner targets:
  - **ZIP (Z Holdings)**: 13% daily vol, fintech sector, momentum candidate
  - **MIN (Mineral Resources)**: 6.3% daily vol, beta 2.78, commodities-linked
  - **PLS (Pilbara Minerals)**: 5.9% daily vol, beta 2.28, lithium producer
  - **LTR (Lithotech, ASX IPO)**: 6.9% daily vol, emerging EV supply chain
- **Verdict**: Excellent for individual stock scanning. Include in Phase 6 (Asian session Apex scanner).

**Hong Kong (HKEX) — Research pending, scout leveraged ETPs + high-vol equities**:
- **Leveraged ETP Products**: CSOP & Samsung offer leveraged products, some with favourable expense ratios. Details: need to cross-check availability vs IBKR contract roster.
- **High-Volatility Equities** (ISA-eligible):
  - **Alibaba (9988)**: ~8% daily vol, tech giant
  - **BYD (1211)**: ~9% daily vol, EV/battery leader
  - **XPeng (9868)**: ~10% daily vol, EV pure-play, high beta
  - **NIO (9866)**: ~12% daily vol, EV startup, volatile
  - **Tencent (0700)**: 4% daily vol, lower vol but systemic importance
- **Verdict**: Rich hunting ground. HKEX now subscribed. Phase 6 will expand HK universe.

#### Cost-Saving Strategy

Non-Professional account means:
- **Savings vs Professional**: ~60-75% reduction per exchange
- **Total for 4 core exchanges** (LSE + TSE + HKEX + ASX) = **~£43/month**
- **Total for 5 exchanges** (+ Xetra) = **~£62/month**
- **Total for 6 exchanges** (+ Euronext) = **~£81/month**

This is **4-5x cheaper** than Professional pricing (~£263/month for equivalent coverage). Advantage: aggressive global expansion is now feasible.

### IBKR Line Limit Strategy

**Problem**: Default IBKR account = 100 simultaneous market data lines. 20,000+ tickers need rotation.

**Solution**: SubscriptionManager (already built, P3-E wires it) implements 100-line rotation:
- **50 permanent lines (Tier 1)**: 12 core ISA ETPs + 38 highest-conviction tickers from Ouroboros
- **50 rotating lines (Tier 2)**: Cycle through remaining universe in 60-second batches
  - 5 Vanguard batches × 10 tickers = 50 Vanguard scans per 5 minutes
  - 14 Apex batches × ~3.5 tickers = 50 Apex scans per 15 minutes
- **Full universe scan time**: ~15 minutes for Apex (20,000 tickers ÷ 50 per rotation ÷ 4 per minute)

**Quote Booster Packs** (optional, $30/month each):
- Each pack adds 100 L1 lines
- 2 packs = 300 total lines → scan full universe in ~5 minutes
- 5 packs = 600 total lines → scan in ~2.5 minutes
- **Recommendation**: Start with 100 default lines. Buy 2 packs ($60/month) only if signal quality degrades due to slow rotation.

### Exchange Contract Definitions

For Phase 6, you need to add contracts to `config/contracts.toml` for each new exchange. The engine discovers IBKR contract IDs at runtime via `reqContractDetails()`, so only the symbol, exchange, and currency are required.

**TSE contracts**: Use IBKR's symbol format for Japanese stocks (e.g., `7203` for Toyota, `9984` for SoftBank). The full TSE universe can be loaded from `initial_universe.toml` — add a `[tse]` section.

**HKEX contracts**: Use IBKR's symbol format (e.g., `0700` for Tencent, `9988` for Alibaba). Currency: HKD.

**XETRA contracts**: Use IBKR's symbol format (e.g., `SAP` for SAP SE). Currency: EUR.

**Euronext contracts**: Use IBKR's symbol format (e.g., `ASML` for ASML, `MC` for LVMH). Currency: EUR.

**ISA Eligibility by Exchange**:
| Exchange | ISA Eligible? | Notes |
|----------|--------------|-------|
| LSE | ✅ YES | Core — all ETPs |
| TSE | ✅ YES | Direct Japanese equities in ISA |
| HKEX | ✅ YES | HK equities allowed |
| XETRA | ✅ YES | German equities allowed |
| Euronext | ✅ YES | French/Dutch equities allowed |
| ASX | ✅ YES | Australian equities allowed |
| TWSE/XTAI | ❌ NO | Taiwan blocked (IsaGate) |
| XSHG/XSHE | ❌ NO | China mainland blocked (IsaGate) |
| XBOM/XNSE | ❌ NO | India blocked (IsaGate) |

### EC2 Instance Sizing

**Current**: c7i-flex.large (2 vCPU, 4GB RAM) — sufficient for 12 LSE tickers.

**With 20,000+ tickers**: The engine processes ticks in a 100ms event loop. With 100 simultaneous data lines at 5-second bars, that's ~20 ticks per loop iteration — easily within capacity. The bottleneck is Python bridge latency (subprocess IPC), not CPU.

**Recommendation**: Keep c7i-flex.large. Monitor memory usage via telemetry (Phase 4). If `bar_history` HashMap exceeds 3GB (500 bars × 20,000 tickers × ~100 bytes = ~1GB — it won't), upgrade to m7i-flex.large (8GB).

---

*AEGIS_MASTER_PROMPT.md v3.2 — 12 March 2026*
*Supersedes: v3.1, v3.0, v2.1, v2.0, CLAUDE_PHASED_EXECUTION_PROMPT.md v6.0, MASTER_COMMAND_SUMMARY.md, all prior directives*
*v3.2 COMPLETE PHASE ROADMAP — 25 PHASES TOTAL*:
  - **PHASE 1 MARKED COMPLETE**: All P0/P1 bugs fixed and deployed. 410 tests passing, zero warnings. Real L1 bid/ask subscriptions live (12 streams). ISA counter wired. GARCH scaled. BST hardcoded. Crontab 18:00 ET. Engine healthy on EC2.
  - **PHASES 2-7**: Core wiring (dead code resurrection, persistence, telemetry, math, global multi-session, dashboard)
  - **PHASES 8-25**: Advanced optimization roadmap (Ouroboros hardening, macro integration, multi-frame vol, sector rotation, registry scraper, cascade defense, resilience, compression, checkpointing, advanced telemetry, multi-session modes, latency profiling, offline calibration, live capital readiness)
  - **IBKR account: Non-Professional status confirmed** (user secured approval). Pricing updated to NP rates: ~£43/mo for 4 core exchanges (LSE + TSE + HKEX + ASX), ~£62/mo for 5 (+ Xetra). 60-75% savings vs Professional.
  - **Exchange research incorporated**: SGX verdict (skip leveraged, low ROI); ASX verdict (skip leveraged ETPs, focus on high-vol equities: ZIP, MIN, PLS, LTR); HKEX research (leveraged ETPs + high-vol equities: 9988, 1211, 9868, 9866 pending cross-check).
  - **Subscriptions status**: IBKR user clicked "subscribe" on ALL Asia Pacific L2 subs (ASX, HKEX, TSE, SGX). TSE and ASX activation in progress (24h). HKEX manual approval needed. US L2 tab not yet reviewed.
  - **DEEP RESEARCH MODE ENABLED**: All major code changes preceded by 10,000-50,000 token thinking analysis. Thinking mode mandatory before wiring modules, before Ralph Wiggum, during debugging.
  - **PERMISSION BYPASS ACTIVE**: Blanket authorization for bash commands, file reads/writes, EC2 deployment, git commits. No permission prompts within scope of Phases 2-25.
  - **TARGET**: 22-hour blitz to complete Phases 2-7 core tasks (dead code wiring, multi-session, dashboard). Phases 8-25 optional post-validation scaling.
*Status: EXECUTE PHASES 2-25 CONSECUTIVELY. NO DEFERRALS. Deep thinking + Ralph Wiggum + 22-hour blitz for P2-7, P8-25 post-Crucible.*
*The One Prompt That Matters. All Guns Blazing. Every Front. 20,000+ tickers. 6 exchanges. 24/7 operation.*
