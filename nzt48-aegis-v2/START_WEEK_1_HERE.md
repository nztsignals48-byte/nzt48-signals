# 🚀 START WEEK 1 HERE
**Your guide to beginning execution on March 14 (or March 17)**

---

## ⚡ WHAT TO DO RIGHT NOW

### 1. You Have 4 New Documents Ready (Created Today)
These guide your next 15 weeks. They're in the root directory:

| Document | Time | Purpose |
|----------|------|---------|
| **EXECUTION_MANIFEST.md** | 15 min | 🔴 **READ FIRST** — Your complete 15-week plan |
| **WEEK_1_VERIFICATION_CHECKLIST.md** | 20 min | 🟡 **READ SECOND** — How to validate Week 1 |
| **SESSION_COMPLETION_AND_HANDOFF.md** | 15 min | 🟢 **READ THIRD** — Complete context of this session |
| **DOCUMENT_TRIAGE_ACTION_ITEMS.md** | 5 min | 📋 Which files to use vs archive |

**Total reading time**: ~50 minutes

### 2. You Need AEGIS_CODEX.md (Canonical Source)
Located in `docs/` directory:

- **Part 2**: Bootstrap Protocol (read before starting Tasks 1-3)
- **Part 3**: Week 1 Refactoring (read before implementing RM-1 through RM-5)

---

## 📅 YOUR WEEK 1 TIMELINE (Choose a Start Date)

### Option A: Start Friday, March 14
- **Fri, Mar 14**: Tasks 1-3 (bootstrap, 75 min total)
- **Mon, Mar 17**: RM-1 through RM-5 (refactoring)
- **Fri, Mar 21**: Week 1 gate (verify all complete)
- **Mon, Mar 24**: Start Weeks 2-5 (Phase 8-10)

### Option B: Start Monday, March 17
- **Mon, Mar 17**: Tasks 1-3 (bootstrap, 75 min total)
- **Tue-Thu, Mar 18-20**: RM-1 through RM-5 (refactoring)
- **Fri, Mar 21**: Week 1 gate (verify all complete)
- **Mon, Mar 24**: Start Weeks 2-5 (Phase 8-10)

**Either way, you'll be ready for Weeks 2-5 by March 24.**

---

## 📖 READING ORDER (Do This First)

### Step 1 (15 minutes)
Read **EXECUTION_MANIFEST.md** → Tells you the complete 15-week plan

### Step 2 (20 minutes)
Read **WEEK_1_VERIFICATION_CHECKLIST.md** → Tells you how to validate Week 1

### Step 3 (30 minutes)
Read **AEGIS_CODEX.md Part 2** (docs/ directory) → Bootstrap protocol spec

### Step 4 (10 minutes)
Run: `cargo test --lib` → Verify 588 tests pass

**Total**: ~75 minutes

---

## 🎯 WEEK 1 EXECUTION (What You'll Do)

### Days 1-2: Bootstrap (75 minutes total)

**Task 1** (37.5 min): Dividend calendar bootstrap via Polygon
- ~150 API calls with strict 15-second delays
- Reference: AEGIS_CODEX.md Part 2
- Validation: WEEK_1_VERIFICATION_CHECKLIST.md

**Task 2** (37.5 min): Stock splits bootstrap via Polygon
- ~150 API calls with strict 15-second delays
- Reference: AEGIS_CODEX.md Part 2
- Validation: WEEK_1_VERIFICATION_CHECKLIST.md

**Task 3** (3.3 min): YFinance LSE fetch
- Load all 12 LSE funds (GPT3.L, 3LUS.L, etc.)
- Reference: AEGIS_CODEX.md Part 2
- Validation: WEEK_1_VERIFICATION_CHECKLIST.md

### Days 3-5: Refactoring (5 mandates)

**RM-1**: GARCH daily fit (~4-6 hours)
- Attach to nightly Ouroboros job
- Reference: AEGIS_CODEX.md Part 3
- Validation: WEEK_1_VERIFICATION_CHECKLIST.md

**RM-2**: WAL dedicated thread (~3-4 hours)
- Spawn at startup
- Reference: AEGIS_CODEX.md Part 3
- Validation: WEEK_1_VERIFICATION_CHECKLIST.md

**RM-3**: PyO3 native FFI (~8-10 hours)
- Rewrite TradingModule integration
- Reference: AEGIS_CODEX.md Part 3
- Validation: WEEK_1_VERIFICATION_CHECKLIST.md

**RM-4**: Dynamic Huber delta (~6-8 hours)
- Parameterize exit engine
- Reference: AEGIS_CODEX.md Part 3
- Validation: WEEK_1_VERIFICATION_CHECKLIST.md

**RM-5**: Exponential backoff (~4-5 hours)
- Retry logic for API calls
- Reference: AEGIS_CODEX.md Part 3
- Validation: WEEK_1_VERIFICATION_CHECKLIST.md

### Friday: Week 1 Gate

**Verify**:
- ✅ All 5 RM mandates implemented
- ✅ 588/588 tests passing
- ✅ All 4 critical fixes verified
- ✅ Code committed to git

**Result**: Ready for Weeks 2-5 (Phase 8-10 direct equity trading)

---

## 🔑 KEY FACTS TO REMEMBER

### 1. The Bootstrap Takes Exactly 75 Minutes
- Task 1: 37.5 min (not 3-5 min)
- Task 2: 37.5 min
- Task 3: 3.3 min
- **Total**: 75 min ± 5 min variance

**Why so long?** Polygon has a 4 calls/minute limit (15-second delay between calls). With 150 calls, that's 37.5 minutes.

### 2. You CANNOT Parallelize the Bootstrap
- Polygon returns 429 (Too Many Requests) if you parallelize
- Must use strict sequential with 15-second delays only
- See AEGIS_CODEX.md Part 2, lines 48-170

### 3. AEGIS_CODEX.md is Canonical
- All decisions are locked in AEGIS_CODEX.md
- Execute it exactly as written
- Don't improvise or use the 25-phase theoretical plan
- Don't use the layman's guides for Week 1 (those are for later)

### 4. You Have 588 Tests Passing
- Phases 0-2: Complete
- Phases 3-6: Written (10 tests)
- Phase 24: Written (22 tests)
- DQN + Hawkes + Quantum Apex: 22 new tests
- **All must remain passing** during Week 1 refactoring

### 5. Four Critical Fixes Are Mandatory
These must be in the code before Week 1 ends:
1. **Polygon pagination**: 150 calls × 15-sec delays = 37.5 min (not 3-5 min)
2. **Stock splits bootstrap**: Parallel 150 calls (same pattern)
3. **YFinance throttling**: 0.5-1.5s jitter, 2-worker sequential
4. **Corporate action mutability**: Nightly validation check

All documented in AEGIS_CODEX.md Part 2.

---

## ❌ WHAT NOT TO DO

❌ **Don't follow the 25-phase multi-exchange plan** (theoretical only)
❌ **Don't parallelize the bootstrap** (Polygon will ban you)
❌ **Don't skip any RM mandate** (all 5 are mandatory)
❌ **Don't move forward if tests fail** (fix the regression first)
❌ **Don't use the layman's guides for Week 1** (those are for investor presentations)
❌ **Don't improvise or "optimize"** (execute CODEX exactly)

---

## ✅ YOUR SUCCESS CHECKLIST

### Before Week 1 Starts
- [ ] Read EXECUTION_MANIFEST.md
- [ ] Read WEEK_1_VERIFICATION_CHECKLIST.md
- [ ] Read AEGIS_CODEX.md Part 2
- [ ] Verify `cargo test --lib` = 588 passing
- [ ] Test Polygon API key (works, no 429 errors)
- [ ] Test yfinance connectivity (can fetch LSE data)

### During Week 1 (Bootstrap Days)
- [ ] Task 1: Dividend bootstrap (37.5 min)
- [ ] Task 2: Splits bootstrap (37.5 min)
- [ ] Task 3: YFinance LSE fetch (3.3 min)
- [ ] All tasks complete with zero 429 errors
- [ ] All data cached correctly

### During Week 1 (Refactoring Days)
- [ ] RM-1: GARCH daily fit implemented
- [ ] RM-2: WAL dedicated thread implemented
- [ ] RM-3: PyO3 native FFI implemented
- [ ] RM-4: Dynamic Huber delta implemented
- [ ] RM-5: Exponential backoff implemented
- [ ] 588 tests still passing after each RM

### Week 1 Complete
- [ ] All 5 RM mandates in code
- [ ] 588/588 tests passing
- [ ] All 4 critical fixes verified
- [ ] Code committed to git
- [ ] Ready for Weeks 2-5

---

## 📞 WHERE TO FIND ANSWERS

**Question**: What's my 15-week plan?
**Answer**: EXECUTION_MANIFEST.md

**Question**: How do I bootstrap?
**Answer**: AEGIS_CODEX.md Part 2

**Question**: How do I implement RM-1 through RM-5?
**Answer**: AEGIS_CODEX.md Part 3

**Question**: How do I verify Week 1 is complete?
**Answer**: WEEK_1_VERIFICATION_CHECKLIST.md

**Question**: What are the 4 critical fixes?
**Answer**: AEGIS_CODEX.md Part 2, lines 28-35 and throughout Part 2

**Question**: When does the system go live?
**Answer**: EXECUTION_MANIFEST.md — Week 11-15 (June 2026) with £10k

**Question**: What if something breaks?
**Answer**: WEEK_1_VERIFICATION_CHECKLIST.md has debugging steps for each task

---

## 🏁 YOU ARE READY

Everything you need is documented. The plan is clear. The code is ready (588 tests passing).

**All that's left is to execute.**

---

## 🚀 NEXT STEP

**Choose your start date** (Friday March 14 or Monday March 17):

### If Friday, March 14
1. Read EXECUTION_MANIFEST.md (today)
2. Read AEGIS_CODEX.md Part 2 (today)
3. Verify 588 tests pass (today)
4. **Tomorrow morning**: Start Task 1 (dividend bootstrap)

### If Monday, March 17
1. Read EXECUTION_MANIFEST.md (Friday or weekend)
2. Read AEGIS_CODEX.md Part 2 (Friday or weekend)
3. Verify 588 tests pass (Friday or weekend)
4. **Monday morning**: Start Task 1 (dividend bootstrap)

---

**Created**: March 13, 2026
**Status**: Your execution begins
**Timeline**: 15 weeks to live capital (Late June 2026)
**Architecture**: Option D+ (IBKR-primary, zero-cost, locked)

**Let's go. 🎯**
