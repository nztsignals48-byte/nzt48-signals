# DOCUMENT TRIAGE: WHAT TO USE vs ARCHIVE
**Date**: March 13, 2026 | **Action**: Organize documentation for execution | **Timeline**: Complete by March 14

---

## 🎯 QUICK ACTION

### This Week (Before Week 1 Starts)
1. Create directory: `docs/archive/this-session-march-13/`
2. Move the 7 files listed under "ARCHIVE NOW" section (below)
3. Keep all files in "USE FOR EXECUTION" section (below)
4. You're done. Ready to start bootstrap.

**Total time**: 5 minutes

---

## ✅ USE FOR EXECUTION (Keep in active directories)

### In `docs/` Directory (Core Architecture)
These are the canonical sources for execution:

```
docs/
├── AEGIS_CODEX.md ........................... 🔴 CRITICAL (read Part 2 & Part 3)
├── 00_CANONICAL_RULES.md ................... Reference (type definitions)
├── 01_DATA_CONTRACTS.md .................... Reference (vendor contracts)
├── 02_STATE_MACHINE.md ..................... Reference (state machine)
├── 03_ACCEPTANCE_TESTS.md .................. Reference (test format)
├── PHASE_11_DIRECT_EQUITY_SPEC.md ......... Phase 8-10 specification
├── PHASE_12_EUROPEAN_EQUITY_SPEC.md ....... Phase 11-13 specification
├── PHASE_13_ASIA_PACIFIC_SPEC.md .......... Phase 11-13 specification
└── checkpoints/
    ├── PHASE_0_GATE.md
    ├── PHASE_1_GATE.md
    ├── PHASE_2_GATE.md
    ├── PHASE_3_GATE.md
    ├── PHASE_4_GATE.md
    ├── PHASE_5_GATE.md
    ├── PHASE_6_GATE.md
    ├── PHASE_7_GATE.md
    ├── PHASE_8_GATE.md
    └── PHASE_9_GATE.md
```

**Action**: Leave these where they are. Don't move or delete.

**Why**: These define your execution path, acceptance criteria, and architecture.

### In Root Directory (Week 1 Execution Guides)
These are created this session and guide Week 1:

```
/Users/rr/nzt48-signals/nzt48-aegis-v2/
├── EXECUTION_MANIFEST.md .................. 🟢 READ FIRST (tells you the plan)
├── WEEK_1_VERIFICATION_CHECKLIST.md ....... 🟢 READ SECOND (validation guide)
└── SESSION_COMPLETION_AND_HANDOFF.md ...... 🟢 READ THIRD (this context)
```

**Action**: Leave these in root directory for easy access during Week 1.

**Why**: You'll reference these daily during bootstrap and refactoring phases.

---

## ❌ ARCHIVE NOW (Move to `docs/archive/this-session-march-13/`)

### Layman's Guides (For Investor/Auditor Presentation Later)
Move these **after Week 1 bootstrap starts successfully**:

```
LAYMANS_GUIDE_WHAT_AEGIS_DOES.md
├── Use: After implementation (investor presentations)
├── Why: Explains AEGIS in plain English
└── Archive reason: Not needed for Week 1 coding

LAYMANS_GUIDE_BUSINESS.md
├── Use: After implementation (investment thesis)
├── Why: Money projections, ROI, risk
└── Archive reason: Not needed for Week 1 coding

LAYMANS_GUIDE_COMPLIANCE.md
├── Use: After implementation (regulator presentations)
├── Why: Legal, compliance, audit trail
└── Archive reason: Not needed for Week 1 coding

READING_GUIDE.md
├── Use: After implementation (navigation for audiences)
├── Why: Explains which docs to read for different roles
└── Archive reason: Not needed for Week 1 coding

START_HERE.md
├── Use: After implementation (quick start for all)
├── Why: Executive summary
└── Archive reason: Not needed for Week 1 coding

FINAL_STATUS_DELIVERY.md
├── Use: After implementation (status reporting)
├── Why: Test counts, deliverables, current state
└── Archive reason: Will update after Week 1

SESSION_COMPLETION_SUMMARY.md
├── Use: After implementation (audit trail)
├── Why: What changed this session
└── Archive reason: Superseded by SESSION_COMPLETION_AND_HANDOFF.md
```

### Reconciliation Documents (Already Analyzed, Keep for Reference)
Move these **after Week 1 completes**:

```
AMENDMENT_7_DAY_SESSION_REVIEW.md
├── Use: Historical reference (how divergence was discovered)
├── Why: Documents why two architectures existed
└── Archive reason: Not needed for Week 1 execution

COMPLETE_7_DAY_SESSION_ANALYSIS.md
├── Use: Historical reference (comprehensive analysis)
├── Why: Full day-by-day decision timeline
└── Archive reason: Not needed for Week 1 execution

FINAL_SESSION_RECONCILIATION.md
├── Use: Historical reference (complete overview)
├── Why: Phase gates, fixes, full timeline documented
└── Archive reason: Not needed for Week 1 execution
```

---

## 📋 EXACT COMMAND TO ARCHIVE (Copy & Paste)

### Step 1: Create Archive Directory
```bash
mkdir -p /Users/rr/nzt48-signals/nzt48-aegis-v2/docs/archive/this-session-march-13
```

### Step 2: Move Layman's Guides (Do This After Week 1 Bootstrap Starts)
```bash
cd /Users/rr/nzt48-signals/nzt48-aegis-v2

# Move layman's guides
mv LAYMANS_GUIDE_WHAT_AEGIS_DOES.md docs/archive/this-session-march-13/
mv LAYMANS_GUIDE_BUSINESS.md docs/archive/this-session-march-13/
mv LAYMANS_GUIDE_COMPLIANCE.md docs/archive/this-session-march-13/
mv READING_GUIDE.md docs/archive/this-session-march-13/
mv START_HERE.md docs/archive/this-session-march-13/
mv FINAL_STATUS_DELIVERY.md docs/archive/this-session-march-13/
mv SESSION_COMPLETION_SUMMARY.md docs/archive/this-session-march-13/
```

### Step 3: Move Reconciliation Documents (Do This After Week 1 Completes)
```bash
cd /Users/rr/nzt48-signals/nzt48-aegis-v2

# Move reconciliation docs
mv AMENDMENT_7_DAY_SESSION_REVIEW.md docs/archive/this-session-march-13/
mv COMPLETE_7_DAY_SESSION_ANALYSIS.md docs/archive/this-session-march-13/
mv FINAL_SESSION_RECONCILIATION.md docs/archive/this-session-march-13/
```

---

## 🗂️ FINAL DIRECTORY STRUCTURE (After Archival)

```
/Users/rr/nzt48-signals/nzt48-aegis-v2/
├── docs/
│   ├── AEGIS_CODEX.md ........................ ✅ KEEP (canonical source)
│   ├── 00_CANONICAL_RULES.md ................. ✅ KEEP
│   ├── 01_DATA_CONTRACTS.md .................. ✅ KEEP
│   ├── 02_STATE_MACHINE.md ................... ✅ KEEP
│   ├── 03_ACCEPTANCE_TESTS.md ................ ✅ KEEP
│   ├── PHASE_11_DIRECT_EQUITY_SPEC.md ........ ✅ KEEP
│   ├── PHASE_12_EUROPEAN_EQUITY_SPEC.md ...... ✅ KEEP
│   ├── PHASE_13_ASIA_PACIFIC_SPEC.md ......... ✅ KEEP
│   ├── checkpoints/ .......................... ✅ KEEP (phase gates)
│   └── archive/
│       └── this-session-march-13/
│           ├── LAYMANS_GUIDE_WHAT_AEGIS_DOES.md
│           ├── LAYMANS_GUIDE_BUSINESS.md
│           ├── LAYMANS_GUIDE_COMPLIANCE.md
│           ├── READING_GUIDE.md
│           ├── START_HERE.md
│           ├── FINAL_STATUS_DELIVERY.md
│           ├── SESSION_COMPLETION_SUMMARY.md
│           ├── AMENDMENT_7_DAY_SESSION_REVIEW.md
│           ├── COMPLETE_7_DAY_SESSION_ANALYSIS.md
│           └── FINAL_SESSION_RECONCILIATION.md
├── EXECUTION_MANIFEST.md ....................... ✅ KEEP (Week 1 guide)
├── WEEK_1_VERIFICATION_CHECKLIST.md ............ ✅ KEEP (validation)
├── SESSION_COMPLETION_AND_HANDOFF.md ........... ✅ KEEP (this context)
└── DOCUMENT_TRIAGE_ACTION_ITEMS.md ............. ✅ KEEP (this file)
```

---

## 📚 READING ORDER FOR WEEK 1

### Before Bootstrap Starts (Friday or Monday)
1. **EXECUTION_MANIFEST.md** (15 min) — Understand your 15-week plan
2. **AEGIS_CODEX.md Part 2** (30 min) — Bootstrap protocol specification
3. **WEEK_1_VERIFICATION_CHECKLIST.md** (20 min) — Validation checklist
4. **Verify**: 588 tests pass (`cargo test --lib`)

### During Bootstrap (Day 1-2)
- Reference **AEGIS_CODEX.md Part 2** for each task
- Use **WEEK_1_VERIFICATION_CHECKLIST.md** to verify each step
- Monitor for 429 errors (indicates parallelism issue)

### During Refactoring (Day 3-5)
- Read **AEGIS_CODEX.md Part 3** for each RM mandate
- Use **WEEK_1_VERIFICATION_CHECKLIST.md** to verify each implementation
- Run `cargo test --lib` after each RM (must stay at 588 passing)

### After Week 1 Complete
- Run full verification: `pytest rust_core/tests/ -v && pytest python_brain/ -v`
- Archive layman's guides to `docs/archive/this-session-march-13/`
- Prepare for Week 2-5 (read PHASE_11_DIRECT_EQUITY_SPEC.md)

---

## 🚫 DOCUMENTS TO IGNORE

These files are in the root directory but should NOT be used for Week 1:

- `AEGIS_INVESTMENT_PROPOSAL.md` (old analysis)
- `AEGIS_MASTER_PROMPT.md` (old prompt)
- `AEGIS_V2_COMPLETE_IMPLEMENTATION_GUIDE.md` (old 10,010-line guide)
- `AEGIS_V2_COMPLETE_VISION.md` (old vision)
- `AEGIS_V2_REVISED_LAYMANS_GUIDE.md` (old guide)
- `AEGIS_V2_COMPLETE_BLUEPRINT.md` (old blueprint)
- `AEGIS_V2_STATUS_SUMMARY.md` (old status)
- `AEGIS_V2_TERMINAL_DIRECTIVE.md` (old directive)
- `COMPLETE_MASTER_PLAN*.md` (v1, v1000H, expanded, lean)
- `COMPLETE_PHASES_1_TO_25_PLAN.md` (old plan)
- `PHASES_0_TO_25_COMPLETE_DETAILED_PLAN.md` (old plan)
- `PHASE_3_TO_6_COMPLETE_IMPLEMENTATION.md` (old implementation)
- `PHASE_*_ANALYSIS.md`, `PHASE_*_ISSUES.md`, `PHASE_*_SUMMARY.md` (old analysis)
- `README_*.md` (old readmes)
- `*_PROMPT.md` files (old prompts)
- `*_AUDIT.md` files (old audits)

**Why**: These are old versions. They've been superseded by AEGIS_CODEX.md. Using them will cause confusion.

**Action**: Leave them where they are (don't delete). They're harmless. Just don't read them during Week 1.

---

## ✅ SIGN-OFF CHECKLIST

Before starting Week 1, confirm:

- [ ] Created directory: `docs/archive/this-session-march-13/`
- [ ] Read EXECUTION_MANIFEST.md (understand the 15-week plan)
- [ ] Read AEGIS_CODEX.md Part 2 (bootstrap specification)
- [ ] Read WEEK_1_VERIFICATION_CHECKLIST.md (validation checklist)
- [ ] Verified 588 tests pass: `cargo test --lib`
- [ ] Polygon API key tested and working
- [ ] Bootstrap data directories exist and are writable
- [ ] YFinance connectivity verified (can fetch LSE data)
- [ ] Ready to start Task 1 (dividend bootstrap)

---

## 🎯 FINAL POINT

**The three files you need for Week 1 are**:
1. **EXECUTION_MANIFEST.md** (in root)
2. **WEEK_1_VERIFICATION_CHECKLIST.md** (in root)
3. **AEGIS_CODEX.md** (in docs/)

Everything else is either reference material or historical documentation.

**Keep it simple. Follow the plan. Execute the code. Pass the tests.**

---

**Created**: March 13, 2026
**Status**: Ready to action
**Timeline**: Archive after Week 1 starts successfully (March 14 or 17)
**Next step**: Begin bootstrap protocol
