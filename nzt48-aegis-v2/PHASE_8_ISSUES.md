# PHASE 8 ISSUES & REMEDIATION TRACKER

**Last Updated**: 2026-03-13
**Status**: Analysis Complete, Ready for Fixes

---

## CRITICAL ISSUES (Blockers)

### ISSUE #1: Crontab Timing Broken
**Severity**: 🔴 CRITICAL (P0)  
**Component**: `crontab` (line 4)  
**Status**: OPEN  
**Discovery**: Code review identified timing mismatch  

**Problem**:
- Current: `0 18 * * 1-5` (18:00 UTC = 18:00 London time)
- LSE open hours: 08:00-16:30 London time
- Ouroboros timing guard refuses to run during LSE hours
- Result: Pipeline **never executes** nightly

**Impact**:
- Ouroboros TOML files never updated
- Engine always uses stale weights
- Phase 8 acceptance test cannot pass

**Fix**:
```bash
# Change line 4 in crontab from:
0 18 * * 1-5 cd /app && ...

# To:
50 3 * * 1-5 cd /app && ...

# (03:50 UTC = 23:50 ET, after LSE close at 16:30 GMT)
```

**Effort**: 5 minutes  
**Verification**: Run manual Ouroboros at 23:50 ET, confirm success  
**Owner**: To be assigned  

---

### ISSUE #2: Downstream Weight Appliance Not Verified
**Severity**: 🔴 CRITICAL (P1)  
**Component**: `engine.rs`, `risk_arbiter.rs`, `smart_router.rs`, `exit_engine.rs`  
**Status**: OPEN  
**Discovery**: Code review found weights are loaded but appliance unknown  

**Problem**:
- Ouroboros computes weights ✅
- Engine loads weights from TOML ✅
- Engine **applies** weights to trading decisions ❓ UNKNOWN

**Impact**:
- Weights might be computed but ignored
- Trading might not adapt to market conditions
- Phase 8 acceptance test cannot verify effectiveness

**Unknowns**:
1. `bayesian_win_rate` → used where?
2. `chandelier_atr_mult` → used in exit_engine.rs?
3. `kelly_fractions` → used for position sizing?
4. `regime_scales` → multiplier for regime-aware sizing?
5. `universe_tiers` → used in routing filter?

**Fix Plan**:
```bash
# 1. Search for each weight in engine code
grep -r "bayesian_win_rate" /Users/rr/nzt48-signals/nzt48-aegis-v2/rust_core/src/
grep -r "chandelier_atr_mult" /Users/rr/nzt48-signals/nzt48-aegis-v2/rust_core/src/
grep -r "kelly_fractions" /Users/rr/nzt48-signals/nzt48-aegis-v2/rust_core/src/
grep -r "regime_scales" /Users/rr/nzt48-signals/nzt48-aegis-v2/rust_core/src/

# 2. Trace each usage to decision point
# 3. Confirm weights affect order routing/sizing
# 4. Document in PHASE_8_INTEGRATION_REPORT.md
```

**Effort**: 2 hours  
**Verification**: Spot-check logs, confirm order sizing varies with weights  
**Owner**: To be assigned  
**Blocker For**: Phase 8 sign-off (maybe — depends on current usage)

---

## HIGH PRIORITY ISSUES

### ISSUE #3: Epoch Reference Hard-Coded
**Severity**: 🟡 HIGH (P1)  
**Component**: `crontab` (line 4)  
**Status**: OPEN  
**Discovery**: Code review found hard-coded epoch  

**Problem**:
- Crontab calculates `--day-count` from epoch `1741478400` (2026-03-11 00:00 UTC)
- After 2026-06-11, this reference will be 92+ days stale
- Cold-start detection (first 3 days) becomes unreliable
- Manual Ouroboros runs will have incorrect day-count

**Impact**:
- Low impact for first 3 months (within cold-start period)
- Medium impact after June 2026 (day_count > 3 expected)
- Could affect future debugging/recovery scenarios

**Fix**:
```bash
# Change from:
--day-count $(( ($(date +%s) - 1741478400) / 86400 ))

# To:
--day-count $(( ($(date +%s) - 1735689600) / 86400 ))
# (1735689600 = 2025-01-01 00:00 UTC, good for next 5+ years)

# Or implement dynamic reference (better):
# --day-count $(( ($(date +%s) - $(cat /app/config/EPOCH_START)) / 86400 ))
```

**Effort**: 10 minutes  
**Verification**: Verify day-count calculation at various dates  
**Owner**: To be assigned  

---

### ISSUE #4: 30-Day Synthetic Backtest Not Implemented
**Severity**: 🟡 HIGH (P2)  
**Component**: `ouroboros/tests/`  
**Status**: OPEN  
**Discovery**: Phase 8 specification requires acceptance test  

**Problem**:
- Phase 8 spec calls for "30-day synthetic backtest validation"
- Current unit tests don't cover 30-day nightly update cycle
- No verification that TOML files persist across multiple nights
- No verification that engine loads weights each morning

**Impact**:
- Cannot confirm Phase 8 is production-ready
- No baseline for future performance regressions
- Missing acceptance gate before Phase 9

**Fix Plan**:
1. Create `ouroboros/tests/test_ouroboros_30day.py` (150+ lines)
2. Simulate 30 days of synthetic trading:
   - Generate random 10-50 trades per day
   - Run Ouroboros nightly (skip 3 cold-start days)
   - Verify TOML validity
   - Load weights each morning (simulate engine boot)
   - Check for file corruption
3. Acceptance criteria:
   - All 27 analytics days succeed (0 failures)
   - TOML files 100% valid
   - No crashes or exceptions
   - Parameter drift is realistic (<5% per day)
   - Archive completeness (27 files)

**Effort**: 4-5 hours  
**Verification**: Run backtest, all assertions pass  
**Owner**: To be assigned  

---

### ISSUE #5: GARCH Calibration Documentation Missing
**Severity**: 🟡 HIGH (P3)  
**Component**: `ouroboros/step_0_garch_calibration.py`  
**Status**: OPEN  
**Discovery**: File exists but is not part of pipeline  

**Problem**:
- `step_0_garch_calibration.py` (50+ lines) exists in repo
- Not called by `cli.py` → not part of nightly pipeline
- Pipeline comments describe "10 steps" but GARCH is step 0 (confusing)
- New developers might think it's broken or missing a call

**Impact**:
- Confusion about what Ouroboros actually runs
- Potential future misuse (someone adds import, breaks design)
- Misleading comments in pipeline.py

**Fix Options**:

**Option A: Document as standalone utility**
- Add comment to `step_0_garch_calibration.py`:
  ```python
  """STANDALONE UTILITY — NOT part of nightly Ouroboros pipeline.
  
  GARCH parameters are now computed at engine startup via:
  - Engine loads 60-day historical data from yfinance
  - Fits GARCH(1,1) model at boot
  - Caches in GarchRegistry
  - Updates incrementally per tick (no nightly recalc needed)
  
  This file is kept for testing/reference only.
  To use: python -m ouroboros.step_0_garch_calibration --tickers QQQ3.L TSL3.L ...
  """
  ```

**Option B: Delete the file**
- Remove `step_0_garch_calibration.py` entirely
- All GARCH logic now lives in `engine.rs::GarchRegistry`
- Cleaner codebase

**Recommendation**: **Option A** (keep for testing, document clearly)

**Effort**: 10 minutes  
**Verification**: Ensure no one imports it  
**Owner**: To be assigned  

---

## MEDIUM PRIORITY ISSUES

### ISSUE #6: No Monitoring/Alerting for Ouroboros Failures
**Severity**: 🟠 MEDIUM (P2)  
**Component**: Operational (no code change needed)  
**Status**: OPEN  
**Discovery**: Phase 8 hardening requires observability  

**Problem**:
- Ouroboros runs nightly but no one watches the output
- If cron job fails silently, no one notices for days
- TOML files might not be updated but engine still boots fine (uses old weights)
- No alerting if corrupted TOML detected

**Impact**:
- Production issue could go undetected for weeks
- Weights could be stale without anyone knowing
- Can't debug historical issues without logs

**Fix Plan**:
1. Enable stderr logging: `tee -a /app/events/ouroboros.log` (already in crontab ✅)
2. Add structured logging:
   ```json
   {
     "timestamp": "2026-03-13T23:50:00Z",
     "status": "success",
     "bayesian_wr": 0.623,
     "chandelier_mult": 3.12,
     "tiers_updated": 12,
     "archive_path": "/app/config/parameter_history/ouroboros_2026-03-13.json"
   }
   ```
3. Set up log rotation (prevent disk fill)
4. **Optional**: Add Slack/email alert on failure

**Effort**: 2-4 hours (basic logging) + ongoing (alert setup)  
**Verification**: Confirm logs are readable and rotated  
**Owner**: DevOps  

---

### ISSUE #7: TOML Corruption Recovery Not Tested
**Severity**: 🟠 MEDIUM (P3)  
**Component**: `ouroboros_loader.rs`, `toml_writer.py`  
**Status**: OPEN  
**Discovery**: No test for corrupted TOML recovery  

**Problem**:
- If `dynamic_weights.toml` is partially written (e.g., missing closing bracket)
- Engine boot loads it, fails, falls back to defaults ✅ (safe)
- But if Ouroboros next night reads this corrupt file (for state recovery)
  - This would fail too
  - Future code might try to load yesterday's state

**Impact**:
- Low impact now (no state recovery from TOML)
- Could be high impact in future (if we add state persistence)
- Good to have as "defense in depth"

**Fix Plan**:
1. Add pre-flight check in `cli.py`:
   ```python
   def validate_toml_files(config_dir):
       for toml_file in ['dynamic_weights.toml', 'universe_classification.toml']:
           path = config_dir / toml_file
           if not path.exists():
               continue
           try:
               with open(path) as f:
                   toml.load(f)
           except Exception as e:
               print(f"WARNING: {path} is corrupted ({e})")
               print(f"Moving to dead-letter: {path}.bak")
               path.rename(f"{path}.bak")
   ```
2. Call before `run_pipeline()`

**Effort**: 1-2 hours  
**Verification**: Manually corrupt TOML, confirm recovery  
**Owner**: To be assigned  
**Blocker For**: Nothing urgent (P3)

---

## LOW PRIORITY ISSUES

### ISSUE #8: Crontab Comment Disagrees with Code
**Severity**: 🟢 LOW (P3)  
**Component**: `crontab` (comment line 1-2)  
**Status**: OPEN  
**Discovery**: Documentation mismatch  

**Problem**:
- Comment says: "18:00 ET every weekday"
- Code says: `0 18 * * 1-5` (18:00 UTC, not ET)
- These are **different times**

**Impact**:
- Confuses operators
- Makes debugging harder
- Once ISSUE #1 is fixed, comment will be wrong anyway

**Fix**:
```bash
# Change comment from:
# 18:00 ET every weekday (Mon-Fri) — gives 1.5h buffer after LSE close

# To:
# 23:50 ET every weekday (Mon-Fri) — runs after LSE close (16:30 GMT)
# Note: 23:50 ET = 04:50 UTC (EST) or 03:50 UTC (EDT)
```

**Effort**: 2 minutes  
**Verification**: Visual inspection  
**Owner**: To be assigned  

---

### ISSUE #9: Manual Ouroboros Execution Runbook Missing
**Severity**: 🟢 LOW (P3)  
**Component**: Documentation  
**Status**: OPEN  
**Discovery**: No guide for running Ouroboros manually  

**Problem**:
- If you need to debug or re-run Ouroboros manually, how do you do it?
- No documented steps or examples
- New team members won't know how to execute

**Impact**:
- Makes debugging harder
- Slows onboarding
- No easy way to re-run analysis if needed

**Fix**: Create `PHASE_8_RUNBOOK.md` with sections:
- Manual Ouroboros execution
- Interpreting output
- Checking TOML validity
- Engine boot verification
- Troubleshooting common errors

**Effort**: 1 hour  
**Verification**: Run through runbook, follow all steps  
**Owner**: Documentation  

---

### ISSUE #10: No Load Test for High-Trade Volume
**Severity**: 🟢 LOW (P3)  
**Component**: Testing  
**Status**: OPEN  
**Discovery**: Ouroboros not tested with 1000+ trades/day  

**Problem**:
- Current tests use 10-50 trades per test
- Real trading might generate 500+ trades/day
- No verification that pipeline handles volume
- Could have O(N²) algorithm hidden in alpha_sieve.py

**Impact**:
- Nightly job could timeout on high-volume days
- Pipeline latency unknown
- Memory footprint unknown

**Fix Plan**:
1. Create benchmark test: `test_ouroboros_perf.py`
2. Test with 500, 1000, 5000 trades
3. Measure runtime + memory
4. Accept if < 60 seconds, < 500MB

**Effort**: 2-3 hours  
**Verification**: Run benchmark, inspect results  
**Owner**: To be assigned  
**Blocker For**: Nothing (P3)

---

## ISSUE SUMMARY TABLE

| ID | Issue | Severity | Type | Owner | Est. Time | Blocker? |
|----|----|----------|------|-------|-----------|----------|
| #1 | Crontab timing broken | 🔴 P0 | Code | TBD | 5 min | **YES** |
| #2 | Weight appliance unknown | 🔴 P1 | Analysis | TBD | 2 hrs | **MAYBE** |
| #3 | Epoch hard-coded | 🟡 P1 | Code | TBD | 10 min | No |
| #4 | 30-day backtest missing | 🟡 P2 | Test | TBD | 4 hrs | **YES** |
| #5 | GARCH docs missing | 🟡 P3 | Docs | TBD | 10 min | No |
| #6 | No monitoring/alerts | 🟠 P2 | Ops | DevOps | 2-4 hrs | No |
| #7 | TOML recovery untested | 🟠 P3 | Test | TBD | 1-2 hrs | No |
| #8 | Crontab comment wrong | 🟢 P3 | Docs | TBD | 2 min | No |
| #9 | Runbook missing | 🟢 P3 | Docs | TBD | 1 hr | No |
| #10 | No load test | 🟢 P3 | Test | TBD | 2-3 hrs | No |

---

## REMEDIATION TIMELINE

### Week 1 (Before Phase 9)

- [ ] **ISSUE #1**: Fix crontab timing (5 min)
- [ ] **ISSUE #2**: Verify weight appliance (2 hrs)
- [ ] **ISSUE #3**: Fix epoch reference (10 min)
- [ ] **ISSUE #4**: Implement 30-day backtest (4 hrs)
- [ ] **ISSUE #5**: Document GARCH (10 min)
- [ ] **ISSUE #8**: Fix crontab comment (2 min)

**Total effort**: ~6.5 hours + 4 hours backtest run = ~10.5 hours

### Week 2+ (Follow-up)

- [ ] **ISSUE #6**: Set up monitoring/alerting (2-4 hrs)
- [ ] **ISSUE #7**: Add TOML recovery test (1-2 hrs)
- [ ] **ISSUE #9**: Write runbook (1 hr)
- [ ] **ISSUE #10**: Add load test (2-3 hrs)

---

## PHASE 8 ACCEPTANCE CRITERIA

Phase 8 is **COMPLETE** when:

- [x] All 10 pipeline modules exist and are tested
- [x] Atomicity/fsync implementation is correct
- [x] DynamicWeights loader has safe fallback
- [x] Engine loads weights safely at boot
- [ ] **ISSUE #1 FIXED**: Crontab timing correct
- [ ] **ISSUE #2 RESOLVED**: Weight appliance verified
- [ ] **ISSUE #4 FIXED**: 30-day backtest passes
- [ ] Documentation updated (ISSUE #5, #8, #9)

---

**Prepared By**: Code Review Agent  
**Date**: 2026-03-13  
**Next Review**: After issues are fixed (ETA 2026-03-20)
