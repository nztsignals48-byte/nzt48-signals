# PHASE Q1 Quick Start — Step-by-Step Execution

## MASTER CHECKLIST (Copy this to your task tracker)

```
PHASE Q1 EXECUTION PLAN (Week 1-4, ~63 hours)

WEEK 1: TIMING DEFECTS (T-01 to T-08)
  [ ] T-01: Remove 30-min blackout — 3 hours
  [ ] T-02: Fix lunch dead zone — 2 hours
  [ ] T-03: Event-driven scanning — 8 hours
  [ ] T-04: GPD batch caching — 4 hours
  [ ] T-05: FAST tier 3/4 — 6 hours (verify already active)
  [ ] T-06: ADX by regime — 1 hour (verify already active)
  [ ] T-07: RVOL by regime — 2 hours (verify already active)
  [ ] T-08: Multi-signal (remove dual throttles) — 1 hour
  [ ] Integration test week 1 — 3 hours
  
WEEK 2: SILENT KILLERS (SK-01 to SK-04)
  [ ] SK-01: Equity denominator sync — 1.5 hours
  [ ] SK-02: Zombie halt date filter — 1 hour
  [ ] SK-03: Confidence floor unification — 0.5 hours
  [ ] SK-04: Remove +1.5% session halt — 1 hour
  [ ] Integration test week 2 — 3 hours
  
WEEK 3: REGULATORY (R21-19, R21-16, R21-13/14, R21-10)
  [ ] R21-19: ISA eligibility gate — 8 hours
  [ ] R21-16: Circuit breaker persistence — 3 hours
  [ ] R21-13/14: VIX hysteresis — 4 hours
  [ ] R21-10: Weekly/monthly halts — 2 hours
  [ ] Integration test week 3 — 2 hours
  
WEEK 4: VALIDATION & DEPLOYMENT
  [ ] Deploy to paper trading — 2 hours
  [ ] Run 100+ paper trades — ongoing
  [ ] Validate 100-Trade Gate (all 4 must pass) — 2 hours
  
TOTAL: 63 hours
```

---

## IMMEDIATE ACTIONS (TODAY)

### 1. Verify Current Code Structure
```bash
cd /Users/rr/nzt48-signals
git status

# Should be clean or show only documentation changes
# If not, stash work first: git stash
```

### 2. Read the Full Plan
```bash
cat MERGED_MASTER_PLAN_v1.0.md | head -200  # Read executive summary
cat PHASE_Q1_IMPLEMENTATION_PLAN.md | head -300  # Read part 1
```

### 3. Create Phase Q1 Branch
```bash
git checkout -b phase-q1-timing-fixes
git push -u origin phase-q1-timing-fixes

# All work for Q1 happens on this branch
# Merge to main only after full week validation
```

### 4. Copy Test Template
```bash
cp PHASE_Q1_IMPLEMENTATION_PLAN.md tests/test_phase_q1_reference.md
# Reference while implementing
```

---

## WEEK 1: TIMING DEFECTS (Priority order)

### Step 1.1: T-08 (SIMPLEST — Start here to build confidence)

**Time estimate:** 1 hour
**Impact:** +0.15% daily (highest impact)

```bash
# Edit strategies/daily_target.py
# Find: _MAX_SIGNALS_PER_DAY = 3
# Verify it's set to 3 or 4

# Find and DELETE these patterns:
grep -n "_daily_signal_fired" strategies/daily_target.py
grep -n "_session_protection" strategies/daily_target.py
grep -n "_session_pnl" bots/kelly_sizer.py

# (Should find 3-5 references total)

# In daily_target.py:
#   - Remove the _daily_signal_fired dict initialization
#   - Remove checks like "if self._daily_signal_fired.get(ticker)"
#   - Keep _MAX_SIGNALS_PER_DAY = 4

# In bots/kelly_sizer.py:
#   - Remove the +1.5% session protection check
#   - Keep only the +2.0% daily ceiling

# Test:
pytest tests/test_phase_q1_implementation.py::TestTimingDefects::test_T08_multi_signal_concurrent_positions -v

# Commit:
git add -A && git commit -m "T-08: Remove single-fire, enable 4 concurrent positions"
```

### Step 1.2: T-01 (Timing, simple logic)

**Time estimate:** 3 hours
**Impact:** +0.05% daily

```bash
# Edit strategies/daily_target.py
# Find: def _time_window_check(self, ...)

# Add helper method _gap_setup_stable()
# Modify the 09:00-09:30 blackout logic to 5-min observe window

# See PHASE_Q1_IMPLEMENTATION_PLAN.md for exact code

# Test:
pytest tests/test_phase_q1_implementation.py::TestTimingDefects::test_T01_gap_stabilization_after_open -v

# Commit:
git add -A && git commit -m "T-01: Replace 30-min blackout with 5-min observation window"
```

### Step 1.3: T-02 (Lunch window, logic modification)

**Time estimate:** 2 hours
**Impact:** +0.02% daily

```bash
# Edit strategies/daily_target.py
# Find: lunch window check around line 366

# Modify to disable oscillators only, keep momentum
# Add _lunch_window flag

# See PHASE_Q1_IMPLEMENTATION_PLAN.md for exact code

# Test:
pytest tests/test_phase_q1_implementation.py::TestTimingDefects::test_T02_lunch_window_oscillator_veto -v

# Commit:
git add -A && git commit -m "T-02: Fix lunch dead zone, allow momentum signals only"
```

### Step 1.4: T-04 (GPD batch, NEW file + modifications)

**Time estimate:** 4 hours
**Impact:** +0.04% daily

```bash
# Create new file: core/tail_loss_monitor_batch.py
# (or add to existing core/tail_loss_monitor.py)

# Add methods:
#   - nightly_batch_gpd_calculation()
#   - get_gpd_tail() (fast lookup)

# Modify strategies/daily_target.py:
#   - Call self._tail_monitor.get_gpd_tail(ticker) instead of calculating

# Integrate with scheduled_jobs.py to run nightly

# See PHASE_Q1_IMPLEMENTATION_PLAN.md for exact code

# Test:
pytest tests/test_phase_q1_implementation.py::TestTimingDefects::test_T04_gpd_batch_caching -v

# Commit:
git add -A && git commit -m "T-04: Move GPD calculation to nightly batch, cache results"
```

### Step 1.5: T-03 (Event-driven, NEW file + main.py changes)

**Time estimate:** 8 hours
**Impact:** +0.08% daily

This is the most complex. Break into sub-steps:

```bash
# 1. Create new file: core/event_anomaly_detector.py
#    - EventAnomalyDetector class
#    - watch_for_vol_spikes() async method
#    - Callback registration

# 2. Modify main.py:
#    - Create anomaly_detector instance
#    - Register immediate_scan_ticker callback
#    - Run both heartbeat_60s_scan() and anomaly detection in parallel

# 3. Create new functions in main.py:
#    - immediate_scan_ticker(event)
#    - heartbeat_60s_scan() (extract existing polling to separate task)

# See PHASE_Q1_IMPLEMENTATION_PLAN.md for exact code

# Test:
pytest tests/test_phase_q1_implementation.py::TestTimingDefects::test_T03_event_driven_anomaly_detection -v

# Commit:
git add -A && git commit -m "T-03: Add event-driven anomaly detection for vol spikes"
```

### Step 1.6: T-05, T-06, T-07 (Regime-based gates — VERIFY existing)

**Time estimate:** 9 hours
**Impact:** +0.13% combined

These should already be partially implemented. Task is to VERIFY they're active:

```bash
# Check if these exist in strategies/daily_target.py:
grep "_get_indicator_gate\|_get_adx_gate\|_get_rvol_gate" strategies/daily_target.py

# If they exist and look complete, just add comments + tests
# If missing or incomplete, implement as per PHASE_Q1_IMPLEMENTATION_PLAN.md

# For each, test:
pytest tests/test_phase_q1_implementation.py::TestTimingDefects::test_T05_fast_tier_activation -v
pytest tests/test_phase_q1_implementation.py::TestTimingDefects::test_T06_adx_regime_adaptation -v
pytest tests/test_phase_q1_implementation.py::TestTimingDefects::test_T07_rvol_regime_adaptation -v

# Commit:
git add -A && git commit -m "T-05/T-06/T-07: Verify and activate regime-based indicator gates"
```

### Step 1.7: Integration Test Week 1

**Time estimate:** 3 hours

```bash
# Run all week 1 tests together:
pytest tests/test_phase_q1_implementation.py::TestTimingDefects -v

# Create small integration test:
#   - Scan 10 tickers
#   - Verify no blackouts between 09:05-12:30
#   - Verify lunch oscillator veto
#   - Verify event anomaly fires on vol spike
#   - Verify GPD is fast (<1ms)

# Commit:
git add -A && git commit -m "Week 1: Integration tests passing, all timing defects fixed"

# Ready for week 2
```

---

## WEEK 2: SILENT KILLERS + REGULATORY (Priority order)

### Step 2.1: SK-03 (SIMPLEST — Start here)

**Time estimate:** 0.5 hours
**Impact:** Fixes parameter drift bugs

```bash
# Update core/threshold_registry.py:
#   - Define CONFIDENCE_FLOOR = 65 as single source of truth

# Update all files that reference confidence:
grep -r "_MIN_CONFIDENCE\|CONFIDENCE_FLOOR" --include="*.py" | cut -d: -f1 | sort -u

# For each file, change to:
#   from core.threshold_registry import ThresholdRegistry as TR
#   ... use TR.CONFIDENCE_FLOOR ...

# Test:
pytest tests/test_phase_q1_implementation.py::TestSilentKillers::test_SK03_confidence_floor_consistency -v

# Commit:
git add -A && git commit -m "SK-03: Unify confidence floor to 65 (single source of truth)"
```

### Step 2.2: SK-04 (Remove dual throttles)

**Time estimate:** 1 hour
**Impact:** Simplifies throttling logic

```bash
# In bots/kelly_sizer.py:
#   - Find and remove +1.5% session protection check
#   - Keep only +2.0% daily ceiling

# Verify no _session_pnl tracking
grep -n "_session_pnl\|session_protection" bots/kelly_sizer.py

# Test:
pytest tests/test_phase_q1_implementation.py::TestSilentKillers::test_SK04_single_throttle_system -v

# Commit:
git add -A && git commit -m "SK-04: Remove +1.5% session halt, consolidate to single +2.0% ceiling"
```

### Step 2.3: SK-01 (Equity denominator sync)

**Time estimate:** 1.5 hours
**Impact:** Fixes phantom halts on profitable systems

```bash
# Edit core/risk_state_machine.py (or similar):
#   - Add reset_daily(current_equity) method
#   - Sync _session_opening_equity daily
#   - Use today's equity as denominator, not frozen starting equity

# Find all references to _starting_equity and verify correct usage:
grep -n "_starting_equity\|_session_opening_equity" core/risk_state_machine.py

# Test:
pytest tests/test_phase_q1_implementation.py::TestSilentKillers::test_SK01_equity_denominator_sync -v

# Commit:
git add -A && git commit -m "SK-01: Sync equity denominator daily to prevent phantom halts"
```

### Step 2.4: SK-02 (Zombie halt date filter)

**Time estimate:** 1 hour
**Impact:** Prevents infinite halts

```bash
# Find all consecutive loss queries:
grep -n "SELECT COUNT.* FROM trades WHERE outcome" --include="*.py" -r

# For each, add date filter:
#   WHERE outcome='LOSS' AND DATE(time_entered)=?

# Should be 3 queries total. Fix all 3:
#   1. database.py:1008
#   2. core/db_writer.py:420
#   3. core/risk_state_machine.py:188

# Test:
pytest tests/test_phase_q1_implementation.py::TestSilentKillers::test_SK02_zombie_halt_date_filter -v

# Commit:
git add -A && git commit -m "SK-02: Add date filters to consecutive loss queries"
```

### Step 2.5: R21-19 (ISA eligibility gate — NEW file)

**Time estimate:** 8 hours
**Impact:** Prevents ISA-void trades (regulatory critical)

```bash
# Create new file: uk_isa/isa_eligibility.py
# Add class ISAEligibilityGate with:
#   - is_eligible(ticker) -> bool
#   - check_universe_eligibility(tickers) -> dict

# Integrate into main.py at startup:
#   isa_gate = ISAEligibilityGate(universe_metadata)
#   ineligible = isa_gate.check_universe_eligibility(EXTENDED_UNIVERSE)

# Add check in S15 scan method:
#   if not isa_gate.is_eligible(ticker):
#       return None

# See PHASE_Q1_IMPLEMENTATION_PLAN.md for exact code

# Test:
pytest tests/test_phase_q1_implementation.py::TestRegulatoryFixes::test_R21_19_isa_eligibility_gate -v

# Commit:
git add -A && git commit -m "R21-19: Add ISA eligibility fast-reject gate"
```

### Step 2.6: Integration Test Week 2

**Time estimate:** 3 hours

```bash
# Run all week 2 tests:
pytest tests/test_phase_q1_implementation.py::TestSilentKillers -v
pytest tests/test_phase_q1_implementation.py::TestRegulatoryFixes::test_R21_19_isa_eligibility_gate -v

# Integration test:
#   - Verify SK fixes don't cause regressions
#   - Verify ISA gate rejects ineligible assets
#   - Verify equity denominator syncs correctly
#   - Verify consecutive loss query uses date filter

# Commit:
git add -A && git commit -m "Week 2: Integration tests passing, silent killers + ISA gate fixed"
```

---

## WEEK 3: REMAINING REGULATORY (R21-16, R21-13/14, R21-10)

### Step 3.1: R21-16 (Circuit breaker persistence)

**Time estimate:** 3 hours
**Impact:** Halts survive Docker restarts

```bash
# Edit core/risk_state_machine.py:
#   - Add CircuitBreakerPersistence class
#   - Use Lua atomicity for set/get
#   - Recover state on startup

# Integrate in main.py:
#   cb.persistence = CircuitBreakerPersistence(redis_client)
#   recovered = cb.persistence.get_halt_state()

# Test:
pytest tests/test_phase_q1_implementation.py::TestRegulatoryFixes::test_R21_16_circuit_breaker_persistence -v

# Commit:
git add -A && git commit -m "R21-16: Persist circuit breaker state to Redis with Lua atomicity"
```

### Step 3.2: R21-13/14 (VIX hysteresis)

**Time estimate:** 4 hours
**Impact:** Prevents VIX threshold flapping

```bash
# Create new file: core/vix_hysteresis_gate.py
# Add class VIXHysteresisGate with:
#   - get_state(current_vix) -> dict (with hysteresis logic)
#   - Deadband ±1.25 points (5% of midpoint)

# Integrate in main.py:
#   vix_gate = VIXHysteresisGate(redis_client)
#   vix_state = vix_gate.get_state(current_vix)

# Modify kelly_sizer.py to use vix_state for position sizing

# Test:
pytest tests/test_phase_q1_implementation.py::TestRegulatoryFixes::test_R21_13_vix_hysteresis -v

# Commit:
git add -A && git commit -m "R21-13/14: Add VIX hysteresis gate with 5% deadband"
```

### Step 3.3: R21-10 (Weekly/monthly halts)

**Time estimate:** 2 hours
**Impact:** Additional safety layers

```bash
# Edit core/risk_state_machine.py:
#   - Add check_weekly_loss(current_equity) -> bool
#   - Add check_monthly_loss(current_equity) -> bool
#   - Add reset_weekly(current_equity)
#   - Add reset_monthly(current_equity)

# Integrate in main.py:
#   circuit_breaker.reset_weekly(equity)
#   circuit_breaker.reset_monthly(equity)
#   if circuit_breaker.check_weekly_loss(equity):
#       halt_system()

# Test:
pytest tests/test_phase_q1_implementation.py::TestRegulatoryFixes::test_R21_10_weekly_monthly_halts -v

# Commit:
git add -A && git commit -m "R21-10: Add weekly (-6%) and monthly (-15%) halt thresholds"
```

### Step 3.4: Other P0 Fixes (R21-06, R21-42, R21-04)

**Time estimate:** 2 hours
**Impact:** Bug fixes

```bash
# R21-06: Fix queue.Full exception
grep -r "queue.Full\|asyncio.QueueFull" --include="*.py"
# Fix with: except (queue.Full, asyncio.QueueFull):

# R21-42: VIX fail-closed (default 99.0 not 0.0)
grep -n "return vix" core/cross_asset_macro.py
# Change default exception return to 99.0

# R21-04: Fix list mutation during iteration
grep -r "for.*in.*:.*.remove" --include="*.py"
# Change to list comprehension instead of mutation

# Commit:
git add -A && git commit -m "R21: Fix queue exception handling, VIX defaults, list mutations"
```

### Step 3.5: Integration Test Week 3

**Time estimate:** 2 hours

```bash
# Run all regulatory tests:
pytest tests/test_phase_q1_implementation.py::TestRegulatoryFixes -v

# Integration test:
#   - Verify circuit breaker persists after restart
#   - Verify VIX doesn't flap within deadband
#   - Verify weekly/monthly halts trigger correctly
#   - Verify ISA gate, all fixes integrated

# Commit:
git add -A && git commit -m "Week 3: Integration tests passing, all regulatory fixes complete"
```

---

## WEEK 4: VALIDATION & DEPLOYMENT

### Step 4.1: Full System Integration Test

**Time estimate:** 2 hours

```bash
# Run entire test suite for Phase Q1:
pytest tests/test_phase_q1_implementation.py -v

# Should see:
#   - 25+ tests
#   - All passing
#   - No warnings
```

### Step 4.2: Deploy to Paper Trading

**Time estimate:** 2 hours

```bash
# Merge branch to main:
git checkout main
git merge phase-q1-timing-fixes --no-ff
git push origin main

# Deploy to paper trading environment:
cd /path/to/deployment
git pull origin main
docker-compose down
docker-compose up -d

# Verify running:
docker-compose logs nzt48 --tail 20
```

### Step 4.3: Run 100-Trade Validation Gate

**Time estimate:** Ongoing (5 trading days)

```bash
# Run 5 trading days (Mon-Fri) collecting 100+ trades
# Monitor against 4 criteria:

# 1. Win Rate ≥ 40%
# 2. Average Entry < 1 minute into move
# 3. Profit Factor > 1.3x
# 4. Consecutive Losses < 3

# Check dashboard/logs daily
docker-compose logs nzt48 | grep "Signal\|Entry\|Win Rate"
```

### Step 4.4: Decision

```
If ALL 4 gates pass:
  ✓ Proceed to Phase Q2 (KRONOS integration)

If ANY gate fails:
  ✗ Stop implementation
  ✗ Diagnose root cause
  ✗ Iterate on fixes
  ✗ Retest on new 100-trade cycle
```

---

## GIT WORKFLOW

```bash
# All work on phase-q1 branch:
git checkout -b phase-q1-timing-fixes

# Commit after each 1-2 hour unit:
git add -A
git commit -m "T-01: Remove 30-min blackout"

# After each day, push:
git push -u origin phase-q1-timing-fixes

# After each week passes tests, merge:
git checkout main
git merge phase-q1-timing-fixes --no-ff -m "Week 1: Timing defects complete"
git push origin main

# Continue on main until Phase Q1 complete
git checkout -b phase-q1-week2-silent-killers
# ... repeat ...
```

---

## TESTING CHECKLIST

After each fix:
```bash
# Run new test
pytest tests/test_phase_q1_implementation.py::TestTimingDefects::test_T01_... -v

# Run all tests that week
pytest tests/test_phase_q1_implementation.py::TestTimingDefects -v

# Run integration test
pytest tests/integration_test_complete_system.py -v

# If all pass, commit
```

---

## EXPECTED BOTTLENECKS

1. **T-03 (Event-driven)** — Most complex, 8 hours
   - Solution: Break into 3 sub-steps (detector → main.py → testing)

2. **R21-19 (ISA gate)** — Requires universe metadata review, 8 hours
   - Solution: Reference uk_isa/isa_universe.py for asset class defs

3. **Testing async code** — Fixture setup for AsyncMock, 3 hours
   - Solution: Use pytest-asyncio plugin, copy patterns from existing tests

---

## SUCCESS CRITERIA

**Week 1:** 8 timing defects fixed + integrated, 10 tests passing
**Week 2:** 4 silent killers + 1 regulatory fix, 15 tests passing
**Week 3:** 4 regulatory fixes complete, 20 tests passing
**Week 4:** 100-trade gate validated, all 4 metrics pass

**Final:** Ready to deploy to Phase 1 live trading (25% sizing)

---

## NEXT IMMEDIATE ACTION

```bash
cd /Users/rr/nzt48-signals
git checkout -b phase-q1-timing-fixes
git push -u origin phase-q1-timing-fixes

# Start with T-08 (1 hour, highest impact, simplest code)
# Then T-01, T-02, T-04, T-03 (in that order by complexity)
```

**Ready?** Let's begin. Starting timer. 🏁
