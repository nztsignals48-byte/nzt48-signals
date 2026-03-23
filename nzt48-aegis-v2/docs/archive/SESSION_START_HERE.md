# 🚀 SESSION: COMPLETE AEGIS V2 WIRING IN ONE GO

## YOU ARE HERE

**Status**: 556 tests passing ✅ (Phases 0-2 done)
**Goal**: 560+ tests passing (add Phases 3-6)
**Time to finish**: 4.5 hours continuous
**Next step**: Open `PHASE_3_TO_6_COMPLETE_IMPLEMENTATION.md` and execute

---

## WHAT YOU NEED TO READ (IN ORDER)

### 1️⃣ **YOU'RE HERE NOW** (this file)
Quick orientation + what to expect

### 2️⃣ **FINAL_COMPLETENESS_CHECKLIST.md**
Proof that you have everything + what's left to do

### 3️⃣ **PHASE_3_TO_6_COMPLETE_IMPLEMENTATION.md** ⭐ **START HERE WHEN CODING**
The actual step-by-step code implementation with exact line numbers

### 4️⃣ (Reference) **LAYMANS_SUMMARY_AFTER_WIRING.md**
What your robot will do in plain English (already done, reference only)

### 5️⃣ (Reference) **MEGA_WIRING_PLAN_TODAY_CORRECTED.md**
High-level overview (already done, reference only)

---

## QUICK FACTS

| Fact | Value |
|------|-------|
| **Current tests** | 556 passing ✅ |
| **Target tests** | 560+ passing (need 5 acceptance tests) |
| **Remaining phases** | Phases 3, 4, 5, 6 |
| **Remaining time** | 4.5 hours continuous |
| **Success criteria** | `cargo test --lib` → 560+ passed; 0 failed |
| **Then** | Deploy to EC2 (3.230.44.22) |

---

## PHASES BREAKDOWN

### Phase 3: HOTSCANNER SCORING (1h) — Easy
**What**: Wire HotScanner scores to send JSON to Python Brain
**Lines of code**: ~30 new lines in engine.rs
**Tests**: Already exist, just need code to wire them
**Complexity**: Low (mostly serialization)

### Phase 4: MODEBPLUS ENUM (1h) — Medium
**What**: Add SessionMode::ModeBPlus for 14:30-16:30 UTC US overlap
**Lines of code**: ~60 new lines in session_manager.rs + engine.rs
**Tests**: Will add 2 acceptance tests
**Complexity**: Medium (6 match statement updates)

### Phase 5: SUBSCRIPTIONMANAGER ROTATION (1.5h) — Easy
**What**: Wire mode transitions to trigger IBKR subscription swaps
**Lines of code**: ~40 new lines in engine.rs
**Tests**: Will add 1 acceptance test
**Complexity**: Low (mostly logging + calling existing stubs)

### Phase 6: ACCEPTANCE TESTS (1h) — Easy
**What**: Write 5 tests proving everything works
**Lines of code**: ~150 lines of test code
**Tests**: 5 new tests added to engine_tests.rs
**Complexity**: Low (copy-paste templates with minor tweaks)

---

## STEP-BY-STEP EXECUTION

```
NOW:  Read this file (2 min)
→     Read FINAL_COMPLETENESS_CHECKLIST.md (3 min)
→     Open PHASE_3_TO_6_COMPLETE_IMPLEMENTATION.md

Phase 3 START (20 min buffer):
  3.1: Queue apex_snapshot JSON (10 min)
  3.2: Verify HotScanner threshold (5 min)
  3.3: Verify Python Brain format (5 min)
  3.4: Verify serde_json import (5 min)
  TEST: cargo test --lib

Phase 4 START (80 min buffer):
  4.1: Add ModeBPlus enum (5 min)
  4.2: Add Display impl (5 min)
  4.3: Update compute_mode() (25 min) ← LONGEST PART
  4.4: Update should_freeze_entries() (10 min)
  4.5: Update should_trigger_carry() (10 min)
  4.6: Update entries_allowed() (10 min)
  TEST: cargo test --lib

Phase 5 START (110 min buffer):
  5.1: Verify apply_mode_subscription_rotation() (10 min)
  5.2: Wire rotate on transition (15 min)
  5.3: Verify rotate_tickers() exists (15 min)
  5.4: Log subscription state (10 min)
  TEST: cargo test --lib

Phase 6 START (185 min buffer):
  6.1: Test HotScanner Mode A (15 min)
  6.2: Test RotationScanner Mode B (15 min)
  6.3: Test 23:00 UTC boundary (10 min)
  6.4: Test ModeBPlus (10 min)
  6.5: Test reconcile halt (10 min)
  TEST: cargo test --lib → 560+ expected

Final validation (200 min buffer):
  cargo check
  cargo clippy -D warnings
  cargo test --lib
  Expect: test result: ok. 560+ passed; 0 failed ✅

THEN: Deploy to EC2
```

---

## IF YOU GET STUCK

### Compilation Error
→ Check line numbers match your file (file lengths may differ)
→ Use `grep -n "SessionMode" rust_core/src/session_manager.rs` to find exact locations

### Test Failure
→ Add `eprintln!()` statements to see what's happening
→ Run just that test: `cargo test test_name --lib -- --nocapture`

### Method Doesn't Exist
→ Add a stub: `pub fn foo(&mut self) { eprintln!("foo called"); }`
→ Full implementation deferred to Phase 8

### ModeBPlus Panic
→ Check it's in ALL match statements (not just new ones)
→ Search: `grep -n "ModeB\|Auction" rust_core/src/session_manager.rs`

---

## THE BIG PICTURE

You're building a **global 22-hour trading robot** that:

- ✅ Trades Asia (00:00-07:50 UTC) using HotScanner
- ✅ Trades Europe (08:00-14:30 UTC) using VanguardSniper + RotationScanner
- ✅ Trades US overlap (14:30-16:30 UTC) with 80 LSE + 20 US lines
- ✅ Holds carry (16:35-23:45 UTC) protecting overnight positions
- ✅ Learns nightly (23:45-00:45 UTC) via Ouroboros calibration
- ✅ Accesses 20,000+ tickers via 5-second SubscriptionManager rotation
- ✅ Has crash-proof data with fsync()
- ✅ Halts on bugs (reconciliation audit log)
- ✅ Accurate hedging (Hayashi-Yoshida correlation)

This session gets you **100% of the wiring done and tested**.

---

## SUCCESS CONDITION

When you're done, run this:

```bash
cd /Users/rr/nzt48-signals/nzt48-aegis-v2/rust_core
cargo test --lib 2>&1 | tail -10
```

You should see:

```
test result: ok. 560+ passed; 0 failed
```

Then deploy to EC2:

```bash
rsync -avz /Users/rr/nzt48-signals/nzt48-aegis-v2/ \
  ubuntu@3.230.44.22:/home/ubuntu/nzt48-aegis-v2/

ssh ubuntu@3.230.44.22 "cd /home/ubuntu/nzt48-aegis-v2 && \
  docker compose build && docker compose up -d"
```

---

## NOW GO

Open `PHASE_3_TO_6_COMPLETE_IMPLEMENTATION.md` and start with **Phase 3, Part 3.1**.

You have all the information you need.

**Estimate**: 4.5 hours to 560+ tests.

Let's ship it. 🚀
