# WEEK 1 VERIFICATION CHECKLIST
**Timeline**: March 14-20, 2026 | **Status**: Ready to execute | **Success**: All boxes checked = proceed to Week 2

---

## PHASE: BOOTSTRAP PROTOCOL (Mon-Tue, 75 minutes)

### Task 1: Dividend Calendar Bootstrap
**Estimated time**: 37.5 minutes
**Start time**: _____ (Day 1, Morning)
**Actual time**: _____

#### Pre-Execution Checklist
- [ ] Polygon API key verified and accessible
- [ ] Rate limit set to 4 calls/min (15-second delays)
- [ ] Cache directory exists: `/var/nzt48/data/dividends/`
- [ ] No parallel workers enabled (sequential only)
- [ ] Test ping to Polygon API succeeds (no 429 errors)

#### Execution Checklist
- [ ] Task 1 begins
- [ ] First API call succeeds (verify response code 200)
- [ ] Rate limiting engaged (15-second delay between calls)
- [ ] Monitor: API calls count increments
- [ ] Monitor: No 429 (Too Many Requests) errors
- [ ] Monitor: Dividend data accumulates in cache
- [ ] ~150 API calls completed (1-5 min per call × 150 = 37.5 min)
- [ ] Task 1 complete
- [ ] Cache file created: `dividends_cache.json`
- [ ] Sample verify: Check 3-5 dividend records look correct

#### Post-Execution Verification
- [ ] Dividend cache loaded into memory (verify log message)
- [ ] Cache contains 5+ years of history (2020-2025)
- [ ] No API errors in logs
- [ ] Total runtime = 37.5 ± 5 minutes (allow ±5 min variance)
- [ ] Proceed to Task 2? **YES** / **STOP**

---

### Task 2: Stock Splits Bootstrap
**Estimated time**: 37.5 minutes
**Start time**: _____ (Day 1, After Task 1)
**Actual time**: _____

#### Pre-Execution Checklist
- [ ] Task 1 completed successfully (dividend cache verified)
- [ ] Rate limit still set to 4 calls/min
- [ ] Cache directory exists: `/var/nzt48/data/splits/`
- [ ] No parallel workers enabled
- [ ] Log file cleared or rotated

#### Execution Checklist
- [ ] Task 2 begins
- [ ] First API call succeeds (Polygon splits endpoint)
- [ ] Rate limiting engaged (15-second delay between calls)
- [ ] Monitor: API calls count increments
- [ ] Monitor: No 429 errors
- [ ] Monitor: Splits data accumulates in cache
- [ ] ~150 API calls completed (37.5 minutes)
- [ ] Task 2 complete
- [ ] Cache file created: `splits_cache.json`
- [ ] Sample verify: Check 5+ splits look correct (verify adjustment factors)

#### Post-Execution Verification
- [ ] Splits cache loaded into memory (verify log message)
- [ ] Cache contains 5+ years of history (2020-2025)
- [ ] Adjustment factors are correct (e.g., 2:1 split = 0.5 factor)
- [ ] No API errors in logs
- [ ] Total runtime = 37.5 ± 5 minutes
- [ ] Proceed to Task 3? **YES** / **STOP**

---

### Task 3: YFinance LSE Fetch
**Estimated time**: 3.3 minutes
**Start time**: _____ (Day 1, After Task 2)
**Actual time**: _____

#### Pre-Execution Checklist
- [ ] Tasks 1-2 completed successfully
- [ ] YFinance configured for LSE tickers (.L suffix)
- [ ] 12 LSE funds identified: GPT3.L, 3LUS.L, 3SEM.L, TSL3.L, etc.
- [ ] Throttling set to 0.5-1.5s jitter between calls
- [ ] 2-worker sequential mode enabled

#### Execution Checklist
- [ ] Task 3 begins
- [ ] First yfinance call succeeds (1 LSE ticker)
- [ ] Wait observed (0.5-1.5 seconds)
- [ ] No IP ban detected (verify no 403/429 responses)
- [ ] Historical data loaded for all 12 tickers
- [ ] Recent prices match expected ranges
- [ ] Total API calls = 12 (one per fund)
- [ ] Task 3 complete

#### Post-Execution Verification
- [ ] All 12 LSE funds loaded in memory
- [ ] Price data is current (within 1 hour)
- [ ] No missing data fields
- [ ] No yfinance errors in logs
- [ ] Total runtime = 3.3 ± 1 minutes
- [ ] Bootstrap complete? **YES** / **STOP**

---

## PHASE: WEEK 1 REFACTORING (Wed-Fri, 3 days)

### RM-1: GARCH Daily Fit
**Estimated time**: 4-6 hours
**Start time**: _____ (Wed Morning)
**Actual time**: _____

#### Pre-Implementation Checklist
- [ ] Bootstrap protocol completed and verified
- [ ] All 588 tests still passing
- [ ] GARCH specification read (AEGIS_CODEX.md Part 3)
- [ ] File location identified: `core/garch_volatility.py`
- [ ] Dependencies installed: `arch` package available

#### Implementation Checklist
- [ ] Code written: Daily GARCH fit logic
- [ ] Integration point: Nightly Ouroboros job (before daily targets)
- [ ] Parameters set: 252-day rolling window, student-t distribution
- [ ] Test case added: 20-day synthetic data, verify fit converges
- [ ] Manual test passed: Real LSE data, fit completes in <2 min
- [ ] No syntax errors (pytest passes)
- [ ] RM-1 code review: Comments clear, variable names descriptive

#### Post-Implementation Verification
- [ ] RM-1 code merged into main branch
- [ ] Run `pytest rust_core/tests/ -v` → 588 tests pass
- [ ] Run `pytest python_brain/ -v` → GARCH tests pass
- [ ] Check logs: GARCH fits run nightly without errors
- [ ] Proceed to RM-2? **YES** / **STOP**

---

### RM-2: WAL Dedicated Thread
**Estimated time**: 3-4 hours
**Start time**: _____ (Wed Afternoon)
**Actual time**: _____

#### Pre-Implementation Checklist
- [ ] RM-1 complete and tests passing
- [ ] WAL (Write-Ahead Log) specification read
- [ ] File location identified: `core/wal_manager.py`
- [ ] Thread safety requirements understood
- [ ] Redis connection available (nzt48-redis running)

#### Implementation Checklist
- [ ] Code written: Dedicated thread for WAL writes
- [ ] Thread spawned at startup (main.py initialization)
- [ ] Queue implemented (thread-safe, 1000-item buffer)
- [ ] Writes are non-blocking to trading loop
- [ ] Graceful shutdown implemented (flush before exit)
- [ ] Test case added: 100 simulated trades, verify all logged
- [ ] Manual test passed: Real trades logged without latency impact
- [ ] No race conditions (review locking strategy)

#### Post-Implementation Verification
- [ ] RM-2 code merged
- [ ] Run full test suite: 588 tests pass
- [ ] Check logs: WAL thread starts at boot
- [ ] Monitor: No "write queue overflow" errors
- [ ] Latency check: Trade latency unchanged from before RM-2
- [ ] Proceed to RM-3? **YES** / **STOP**

---

### RM-3: PyO3 Native FFI
**Estimated time**: 8-10 hours
**Start time**: _____ (Thu Morning)
**Actual time**: _____

#### Pre-Implementation Checklist
- [ ] RM-1 and RM-2 complete and tests passing
- [ ] PyO3 + pyo3-asyncio specification read
- [ ] File locations identified:
  - [ ] `rust_core/src/trading_module_ffi.rs` (Rust side)
  - [ ] `python_brain/pyo3_bridge.py` (Python side)
- [ ] GIL (Global Interpreter Lock) strategy understood
- [ ] C++ quantum_apex integration working (verify with `cargo test`)

#### Implementation Checklist
- [ ] Rust FFI interface written (PyO3 bindings)
- [ ] Python wrapper created (pyo3-asyncio for async safety)
- [ ] TradingModule integration rewritten (use native FFI instead of IPC)
- [ ] Test case: 50 calls to native module, verify results match
- [ ] Manual test: 1000 real signals, latency < 5ms per call
- [ ] GIL release verified (profile with py-spy if available)
- [ ] Error handling: Rust panics become Python exceptions
- [ ] Documentation added: FFI contract explanation

#### Post-Implementation Verification
- [ ] RM-3 code merged
- [ ] Run full test suite: 588 tests pass
- [ ] Run `cargo test --lib` → All Rust tests pass
- [ ] Run `pytest python_brain/ -v` → All Python tests pass
- [ ] Check: TradingModule calls use native FFI (no fallback to IPC)
- [ ] Latency check: Signals processed in <10ms (batch of 20)
- [ ] Proceed to RM-4? **YES** / **STOP**

---

### RM-4: Dynamic Huber Delta
**Estimated time**: 6-8 hours
**Start time**: _____ (Thu Afternoon)
**Actual time**: _____

#### Pre-Implementation Checklist
- [ ] RM-1, RM-2, RM-3 complete and tests passing
- [ ] Huber delta specification read (robust loss function)
- [ ] File location identified: `core/exit_engine.py`
- [ ] Backtesting framework available for comparison

#### Implementation Checklist
- [ ] Code written: Dynamic delta parameterization
- [ ] Delta adjusted based on: Recent volatility, recent P&L, market regime
- [ ] Parameter ranges: 0.5 (stable) to 10.0 (volatile)
- [ ] Test case: 3 volatility regimes, verify delta adjusts correctly
- [ ] Backtesting: Compare fixed delta vs dynamic delta (last 30 days)
- [ ] Expected result: Dynamic should slightly improve Sharpe or reduce DD
- [ ] Regression test: Same exit criteria as before (no algorithmic change)
- [ ] Documentation: Explain delta adjustment formula

#### Post-Implementation Verification
- [ ] RM-4 code merged
- [ ] Run full test suite: 588 tests pass
- [ ] Backtest results show delta adjusting dynamically (verify logs)
- [ ] Exit behavior unchanged if delta becomes neutral (regression test)
- [ ] Manual check: 50 paper trades, delta varies reasonably
- [ ] Proceed to RM-5? **YES** / **STOP**

---

### RM-5: Exponential Backoff
**Estimated time**: 4-5 hours
**Start time**: _____ (Fri Morning)
**Actual time**: _____

#### Pre-Implementation Checklist
- [ ] RM-1 through RM-4 complete and tests passing
- [ ] Exponential backoff specification read
- [ ] File location identified: `core/api_retry_manager.py`
- [ ] Retry scenarios understood (network, rate limit, timeout)

#### Implementation Checklist
- [ ] Code written: Exponential backoff with jitter
- [ ] Retry logic: 1s, 2s, 4s, 8s, 16s... + random jitter
- [ ] Max retries: 5 (total wait = 31 seconds max)
- [ ] Applied to: Polygon API, yfinance, IB Gateway, Redis
- [ ] Test case: Mock API failures, verify backoff timing
- [ ] Jitter test: Verify 10 retries don't cluster (min spread = 50%)
- [ ] Timeout test: Verify doesn't exceed max total wait (60 sec)
- [ ] Graceful degradation: Falls back to cached data if all retries fail

#### Post-Implementation Verification
- [ ] RM-5 code merged
- [ ] Run full test suite: 588 tests pass
- [ ] Test with intentional API failure: Verify backoff works
- [ ] Monitor: No repeated 429 errors (backoff prevents hammering)
- [ ] Check: Fallback to cache when API unavailable
- [ ] Proceed to Week 1 Gate? **YES** / **STOP**

---

## WEEK 1 GATE (Fri End of Week)

### All RM Mandates Complete?
- [ ] RM-1: GARCH daily fit — **COMPLETE**
- [ ] RM-2: WAL dedicated thread — **COMPLETE**
- [ ] RM-3: PyO3 native FFI — **COMPLETE**
- [ ] RM-4: Dynamic Huber delta — **COMPLETE**
- [ ] RM-5: Exponential backoff — **COMPLETE**

### All Tests Passing?
- [ ] Run: `cargo test --lib --release` → **588/588 PASS**
- [ ] Run: `pytest rust_core/tests/ -v` → **ALL PASS**
- [ ] Run: `pytest python_brain/ -v` → **ALL PASS**
- [ ] No warnings in build output
- [ ] No deprecation warnings in tests

### Critical Fixes Verified?
- [ ] Fix 1: Polygon pagination rate limit enforced (15 sec/call) — **VERIFIED**
- [ ] Fix 2: Splits bootstrap data cached and loaded — **VERIFIED**
- [ ] Fix 3: YFinance throttling (0.5-1.5s jitter) — **VERIFIED**
- [ ] Fix 4: Corporate action mutability check running nightly — **VERIFIED**

### Code Quality Checks
- [ ] No uncommitted changes (all code committed)
- [ ] No merge conflicts
- [ ] No TODOs left in critical files
- [ ] Code review checklist passed (self or peer)

### Documentation Updated?
- [ ] AEGIS_CODEX.md Part 2-3 sections match implementation
- [ ] RM-1 through RM-5 documented with code examples
- [ ] Known issues (if any) documented
- [ ] Next steps (Week 2, Phases 8-10) are clear

---

## WEEK 1 SUCCESS CRITERIA

### ✅ PASS (Proceed to Week 2)
All of the following must be true:
1. Bootstrap protocol completed (Tasks 1-3, 75 min total)
2. All 5 RM mandates implemented in code
3. 588/588 tests passing (no regressions)
4. All 4 critical fixes verified and working
5. Nightly Ouroboros runs without errors
6. Code committed to git with clear messages

### ❌ FAIL (Stop and Debug)
If any of the following occurs:
- Test count drops below 588 (regression)
- RM implementation incomplete (partial code)
- Critical fix not verified (assumption over testing)
- Bootstrap task takes >45 min per task (indicates issue)
- Any 429 rate limit errors during bootstrap (indicates parallelism issue)

### 🎯 NEXT STEP (Week 2 Begins)
If all checks pass:
1. **Next phase**: Weeks 2-5 (Phase 8-10 Direct Equity Trading)
2. **Specification**: Read `PHASE_11_DIRECT_EQUITY_SPEC.md`
3. **Execution**: Begin paper trading with 100+ trade target
4. **Gate criteria**: 100+ trades, WR ≥ 45%, max DD < 8%

---

## SIGN-OFF

**Week 1 Execution:** _____________ (Date completed)
**Executed by:** _____________ (Your name/ID)
**All checks passed:** YES / NO
**Proceed to Week 2:** YES / NO

**Comments/Issues**:
_______________________________________________________________
_______________________________________________________________
_______________________________________________________________

**Approved to proceed:** _____________ (Your signature/confirmation)

---

**Template version**: 1.0
**Created**: March 13, 2026
**Status**: Ready for Week 1 execution (March 14-20, 2026)
