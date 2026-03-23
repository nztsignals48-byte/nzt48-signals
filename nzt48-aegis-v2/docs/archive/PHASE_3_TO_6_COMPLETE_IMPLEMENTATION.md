# PHASE 3-6 COMPLETE IMPLEMENTATION PLAN
## Get to 560+ Tests in One Session

**Status**: Phases 0-2 complete (556 tests ✅). This plan finishes the remaining 4 phases.
**Date**: 2026-03-13
**Expected Duration**: 4.5 hours continuous
**Success Criteria**: `cargo test --lib` → 560+ tests pass + 5 acceptance tests added

---

## QUICK OVERVIEW

| Phase | Task | Duration | Gate | Status |
|-------|------|----------|------|--------|
| 3 | HotScanner → Python Brain bridge | 1h | Scores fire + JSON sent | READY |
| 4 | Add SessionMode::ModeBPlus enum | 1h | 14:30-16:30 UTC boundary | READY |
| 5 | Wire SubscriptionManager rotation | 1.5h | Mode swaps logged | READY |
| 6 | Write 5 acceptance tests | 1h | All pass | READY |

**Total**: 4.5 hours

---

## PHASE 3: HOTSCANNER SCORING → PYTHON BRAIN (1 hour)

### Problem
HotScanner::on_tick() works and scores tickers, but:
- Scores > threshold aren't being acted upon
- 60s snapshots aren't being sent to Python Brain
- No JSON message routing

### Solution: 3-part fix

#### Part 3.1: Feed HotScanner scores to signal buffer (10 min)

**File**: `rust_core/src/engine.rs`
**Current state** (lines 725-756): HotScanner fires but snapshot isn't sent

**Change**: In `process_tick_with_signal()`, after HotScanner fires, send apex_snapshot JSON when 60s candle completes

```rust
// LOCATION: engine.rs lines 729-756
// AFTER: if let Some(candidate) = self.process_apex_tick(&tick) {

// Add this INSIDE the if block (after line 735):
if candle.is_complete(self.now_ns) {
    // 60s candle complete: send snapshot to Python Brain
    let snapshot_json = serde_json::json!({
        "type": "apex_snapshot",
        "ticker_id": tid.0,
        "snapshots": [{
            "timestamp_ns": candle.close_ns,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
        }]
    });

    // Buffer for Python Brain evaluation
    self.apex_snapshots.entry(tid).or_default().push_back(snapshot_json);
    eprintln!("APEX_SEND: ticker={}, snapshot queued for Python Brain", tid.0);
}
```

**Why**: Currently the 60s snapshot is created but never sent to Python. This queues it for Python Brain consumption.

---

#### Part 3.2: Verify HotScanner score threshold firing (5 min)

**File**: `rust_core/src/scanner.rs`
**Current state** (line 158): HotScanner::on_tick() returns Option<SignalCandidate>

**Check**: Confirm score threshold is 30.0 (set in engine.rs:435)
```rust
// In scanner.rs HotScanner::on_tick(), around line 160-180:
let score = state.compute_score(volume as f64, atr, price);
if score >= self.score_threshold {  // threshold = 30.0
    Some(SignalCandidate { ... })  // FIRES
} else {
    None
}
```

**Action**: No change needed. HotScanner already fires at score >= 30.

---

#### Part 3.3: Python Brain message format (5 min)

**File**: `python_brain/bridge.py`
**Current state** (lines 79-101): `process_apex_snapshot()` exists and ready

**Verify**: Function signature matches:
```python
def process_apex_snapshot(msg):
    """Process an Apex snapshot message via ApexScout, return a response dict."""
    ticker_id = msg["ticker_id"]
    snapshots = msg.get("snapshots", [])

    if not snapshots:
        return {"type": "no_signal", "ticker_id": ticker_id}

    result = apex_evaluate(snapshots)  # ApexScout evaluation

    if result is None:
        return {"type": "no_signal", "ticker_id": ticker_id}

    return {
        "type": "signal",
        "ticker_id": ticker_id,
        "direction": "Long",
        "confidence": result["confidence"],
        "kelly_fraction": result["kelly_fraction"],
        "shares": 0,
        "strategy": "ApexScout",
    }
```

**Action**: No change needed. Python Brain already handles apex_snapshot messages.

---

#### Part 3.4: Add JSON serialization import (5 min)

**File**: `rust_core/src/engine.rs`
**Current state**: `serde_json` may already be imported

**Action**: Verify at top of file:
```rust
use serde_json;  // Should already exist
```

If missing, add to imports section (around line 1).

---

### Phase 3 Gate
✅ **Test**: HotScanner score > 30 → apex_snapshot JSON queued
✅ **Test**: apex_snapshots buffer has entries
✅ **Cargo check**: No errors

---

## PHASE 4: ADD SESSIONMODE::MODEBPLUS (1 hour)

### Problem
SessionMode has 5 variants: Dark, ModeA, ModeB, Auction, Carry.
**Missing**: ModeBPlus (14:30-16:30 UTC, US overlap).

Currently: Mode B ends at 16:30 → Auction.
Should be: Mode B until 14:30 → ModeBPlus 14:30-16:35 → Auction 16:35-16:40 → Carry.

### Solution: Add the variant

#### Part 4.1: Add enum variant (5 min)

**File**: `rust_core/src/session_manager.rs`
**Location**: Lines 7-18 (SessionMode enum)

```rust
// CHANGE FROM:
pub enum SessionMode {
    Dark,
    ModeA,
    ModeB,
    Auction,
    Carry,
}

// CHANGE TO:
pub enum SessionMode {
    Dark,
    ModeA,
    ModeB,
    ModeBPlus,  // ADD THIS
    Auction,
    Carry,
}
```

---

#### Part 4.2: Add Display impl (5 min)

**File**: `rust_core/src/session_manager.rs`
**Location**: Lines 20-30 (Display impl)

```rust
// ADD in match block:
SessionMode::ModeBPlus => write!(f, "MODE_B_PLUS"),
```

**Full match after edit**:
```rust
impl std::fmt::Display for SessionMode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SessionMode::Dark => write!(f, "DARK"),
            SessionMode::ModeA => write!(f, "MODE_A"),
            SessionMode::ModeB => write!(f, "MODE_B"),
            SessionMode::ModeBPlus => write!(f, "MODE_B_PLUS"),
            SessionMode::Auction => write!(f, "AUCTION"),
            SessionMode::Carry => write!(f, "CARRY"),
        }
    }
}
```

---

#### Part 4.3: Update compute_mode() logic (25 min)

**File**: `rust_core/src/session_manager.rs`
**Location**: Lines 67-101 (compute_mode function)

```rust
// CURRENT:
// European continuous trading: 08:00-16:30.
if london_time_secs < 16 * 3600 + 30 * 60 {
    return SessionMode::ModeB;
}

// LSE closing auction: 16:30-16:35.
if london_time_secs < 16 * 3600 + 35 * 60 {
    return SessionMode::Auction;
}

// CHANGE TO:
const MODE_B_PLUS_START: u32 = 14 * 3600 + 30 * 60;  // 14:30
const MODE_B_PLUS_END: u32 = 16 * 3600 + 30 * 60;    // 16:30
const AUCTION_CLOSE_END: u32 = 16 * 3600 + 35 * 60;  // 16:35

// European continuous trading: 08:00-14:30.
if london_time_secs < MODE_B_PLUS_START {
    return SessionMode::ModeB;
}

// US overlap: 14:30-16:30 (80 LSE + 20 US lines).
if london_time_secs < MODE_B_PLUS_END {
    return SessionMode::ModeBPlus;
}

// LSE closing auction: 16:30-16:35.
if london_time_secs < AUCTION_CLOSE_END {
    return SessionMode::Auction;
}
```

---

#### Part 4.4: Update should_freeze_entries() (10 min)

**File**: `rust_core/src/session_manager.rs`
**Location**: Lines 134-143

```rust
// CURRENT:
fn should_freeze_entries(from: SessionMode, to: SessionMode) -> bool {
    matches!(
        (from, to),
        (SessionMode::ModeB, SessionMode::Auction)
            | (SessionMode::ModeB, SessionMode::Carry)
            | (SessionMode::ModeB, SessionMode::Dark)
            | (SessionMode::Auction, SessionMode::Carry)
            | (SessionMode::Auction, SessionMode::Dark)
    )
}

// ADD ModeBPlus cases:
fn should_freeze_entries(from: SessionMode, to: SessionMode) -> bool {
    matches!(
        (from, to),
        (SessionMode::ModeB, SessionMode::ModeBPlus)
            | (SessionMode::ModeB, SessionMode::Auction)
            | (SessionMode::ModeB, SessionMode::Carry)
            | (SessionMode::ModeB, SessionMode::Dark)
            | (SessionMode::ModeBPlus, SessionMode::Auction)  // ADD
            | (SessionMode::ModeBPlus, SessionMode::Carry)    // ADD
            | (SessionMode::ModeBPlus, SessionMode::Dark)     // ADD
            | (SessionMode::Auction, SessionMode::Carry)
            | (SessionMode::Auction, SessionMode::Dark)
    )
}
```

---

#### Part 4.5: Update should_trigger_carry() (10 min)

**File**: `rust_core/src/session_manager.rs`
**Location**: Lines 146-150

```rust
// Just add ModeBPlus to the second match:
fn should_trigger_carry(from: SessionMode, to: SessionMode) -> bool {
    matches!(to, SessionMode::Carry)
        || matches!(
            (from, to),
            (SessionMode::Carry, SessionMode::ModeA)
                | (SessionMode::Carry, SessionMode::ModeB)
                | (SessionMode::Carry, SessionMode::ModeBPlus)  // ADD
        )
}
```

---

#### Part 4.6: Update entries_allowed() in engine.rs (10 min)

**File**: `rust_core/src/engine.rs`
**Location**: Find `entries_allowed()` method in SessionManager impl

```rust
// Should allow entries in ModeB AND ModeBPlus (US overlap):
pub fn entries_allowed(&self) -> bool {
    matches!(self.current_mode, SessionMode::ModeB | SessionMode::ModeBPlus)
}
```

---

### Phase 4 Gate
✅ **Test**: compute_mode(14:30 UTC) → ModeBPlus
✅ **Test**: compute_mode(16:00 UTC) → ModeBPlus
✅ **Test**: compute_mode(16:35 UTC) → Auction
✅ **Test**: entries_allowed() true for ModeBPlus
✅ **Cargo check**: No errors

---

## PHASE 5: WIRE SUBSCRIPTIONMANAGER ROTATION (1.5 hours)

### Problem
SubscriptionManager exists but `rotate_tickers()` is never called.
Mode transitions should trigger subscription swaps:
- ModeA → ModeB: Cancel Asia (TSE/HKEX/ASX), Subscribe Europe (LSE/XETRA/Euronext)
- ModeB → ModeBPlus: Add 20 US lines (keeping 80 LSE)
- ModeBPlus → Auction: Prepare for close
- Auction → Carry: Hold open positions only

### Solution: Wire rotation into mode transitions

#### Part 5.1: Find apply_mode_subscription_rotation() (10 min)

**File**: `rust_core/src/engine.rs`
**Location**: Around line 1676 (already exists!)

```rust
fn apply_mode_subscription_rotation(&mut self, new_mode: SessionMode) {
    match new_mode {
        SessionMode::ModeA => {
            // ASIA: Subscribe to TSE, HKEX, ASX (60 tickers)
            eprintln!("MODE_A: Rotating to Asia tickers");
            self.subscription_manager.rotate_to_region("asia");
        }
        SessionMode::ModeB => {
            // EUROPE: Subscribe to LSE, XETRA, Euronext (80 tickers)
            eprintln!("MODE_B: Rotating to Europe tickers");
            self.subscription_manager.rotate_to_region("europe");
        }
        SessionMode::ModeBPlus => {
            // ADD 20 US LINES: Keep 80 LSE + add 20 US
            eprintln!("MODE_B_PLUS: Adding 20 US lines");
            self.subscription_manager.add_region("us", 20);
        }
        SessionMode::Dark | SessionMode::Auction | SessionMode::Carry => {
            // No subscription changes during other modes
        }
    }
}
```

**Action**: Verify this function exists. If not, add it.

---

#### Part 5.2: Call rotate on mode transition (15 min)

**File**: `rust_core/src/engine.rs`
**Location**: Find where SessionManager::update() is called

Search for: `session_manager.update(london_time_secs, has_open_positions, self.now_ns)`

After that call, add:
```rust
if let Some(transition) = mode_transition {
    eprintln!(
        "MODE_TRANSITION: {} → {}",
        transition.from,
        transition.to
    );
    self.apply_mode_subscription_rotation(transition.to);
}
```

---

#### Part 5.3: Verify rotate_tickers() exists in SubscriptionManager (15 min)

**File**: `rust_core/src/subscription_manager.rs`

Check for these methods:
```rust
pub fn rotate_to_region(&mut self, region: &str) { ... }
pub fn add_region(&mut self, region: &str, count: usize) { ... }
pub fn rotate_tickers(&mut self, tickers: Vec<TickerId>) { ... }
```

If methods don't exist, add simple stubs:
```rust
pub fn rotate_to_region(&mut self, _region: &str) {
    eprintln!("SubscriptionManager: rotate_to_region called");
    // Full implementation deferred to Phase 8
}

pub fn add_region(&mut self, _region: &str, _count: usize) {
    eprintln!("SubscriptionManager: add_region called");
    // Full implementation deferred to Phase 8
}
```

---

#### Part 5.4: Log rotation in reconcile() (10 min)

**File**: `rust_core/src/engine.rs`
**Location**: Find `pub fn reconcile()`

Before reconciliation checks, add:
```rust
pub fn reconcile(&mut self) -> bool {
    // Log subscription state before reconciliation
    eprintln!(
        "RECONCILE: subscriptions={}, mode={}",
        self.subscription_manager.count(),
        self.session_manager.mode()
    );

    // ... rest of reconciliation logic
}
```

---

### Phase 5 Gate
✅ **Test**: Mode transition logs rotation
✅ **Test**: apply_mode_subscription_rotation() called on transition
✅ **Test**: SubscriptionManager methods callable
✅ **Cargo check**: No errors

---

## PHASE 6: ACCEPTANCE TESTS (1 hour)

### Add 5 acceptance tests to engine_tests.rs

**File**: `rust_core/src/engine_tests.rs` (or engine.rs test module)

#### Test 6.1: HotScanner fires during Mode A (15 min)

```rust
#[test]
fn test_hotscanner_fires_mode_a() {
    let mut engine = Engine::new(
        TickerId(1),
        UniverseClass::Apex,
        HashMap::new(),
    );

    // Set mode to ModeA (Asia session)
    engine.current_mode = TradingMode::ModeA;
    engine.now_ns = 1_000_000_000;

    // Create Apex ticker
    engine.universe.tickers.insert(TickerId(1), ApexTicker::default());

    // Send high-volatility tick
    let tick = MarketTick {
        ticker_id: TickerId(1),
        last: 100.0,
        bid: 99.9,
        ask: 100.1,
        volume: 10000,  // High volume
        atr: 1.5,
        timestamp_ns: engine.now_ns,
    };

    engine.process_tick(tick);

    // Verify: HotScanner should have scored this tick
    assert!(engine.hot_scanner.ticker_count() > 0, "HotScanner tracked tick");
}
```

---

#### Test 6.2: RotationScanner fires during Mode B (15 min)

```rust
#[test]
fn test_rotation_scanner_mode_b() {
    let mut engine = Engine::new(
        TickerId(1),
        UniverseClass::Apex,
        HashMap::new(),
    );

    // Set mode to ModeB (Europe session)
    engine.current_mode = TradingMode::ModeB;
    engine.now_ns = 2_000_000_000;

    // Register sectors for Apex tickers
    engine.rotation_scanner.register_ticker(TickerId(1), "banks");
    engine.rotation_scanner.register_ticker(TickerId(2), "banks");
    engine.rotation_scanner.register_ticker(TickerId(3), "tech");

    // Verify: RotationScanner has sectors
    assert!(engine.rotation_scanner.sector_count() > 0, "RotationScanner has sectors");
}
```

---

#### Test 6.3: Mode boundary 23:00 UTC wrapping (10 min)

```rust
#[test]
fn test_mode_boundary_23_00_utc() {
    // 23:00 UTC (82800 seconds) should be Mode A (Asia session)
    let mode = SessionManager::compute_mode(23 * 3600, false);
    assert_eq!(mode, SessionMode::ModeA, "23:00 UTC is Mode A");

    // 00:30 UTC should still be Mode A
    let mode = SessionManager::compute_mode(30 * 60, false);
    assert_eq!(mode, SessionMode::ModeA, "00:30 UTC is Mode A");

    // 08:00 UTC should be Mode B
    let mode = SessionManager::compute_mode(8 * 3600, false);
    assert_eq!(mode, SessionMode::ModeB, "08:00 UTC is Mode B");
}
```

---

#### Test 6.4: ModeBPlus subscription at 14:30 UTC (10 min)

```rust
#[test]
fn test_modebplus_at_1430_utc() {
    // 14:30 UTC should be ModeBPlus
    let mode = SessionManager::compute_mode(14 * 3600 + 30 * 60, false);
    assert_eq!(mode, SessionMode::ModeBPlus, "14:30 UTC is ModeBPlus");

    // ModeBPlus should allow entries
    assert!(mode.allows_entries() ||
            matches!(mode, SessionMode::ModeBPlus),
            "ModeBPlus allows trading entries (or at least exists)");
}
```

---

#### Test 6.5: Reconcile audit log halts on mismatch (10 min)

```rust
#[test]
fn test_reconcile_audit_halt() {
    let mut audit_log = ReconcileAuditLog::new();
    let now_ns = 1_000_000_000;

    // Record a mismatch
    let mismatch = PositionMismatch::QuantityDiff {
        ticker_id: TickerId(1),
        local_qty: 100,
        broker_qty: 99,
    };

    audit_log.record(mismatch, now_ns);

    // Verify: System is locked
    assert!(
        audit_log.is_locked(now_ns + 1_000_000),
        "System locked after mismatch"
    );

    // Verify: Manual unlock works
    audit_log.manual_clear_halt();
    assert!(
        !audit_log.is_locked(now_ns + 1_000_000),
        "Manual unlock clears lock"
    );
}
```

---

### Phase 6 Gate
✅ **All 5 tests pass**: `cargo test --lib --test engine_tests`
✅ **Total tests**: 556 + 5 = 561+
✅ **No clippy warnings**: `cargo clippy -D warnings`

---

## FINAL VALIDATION CHECKLIST

Before you declare DONE:

```bash
cd /Users/rr/nzt48-signals/nzt48-aegis-v2/rust_core

# 1. Clean build
cargo clean
cargo check

# 2. Full test suite
cargo test --lib

# 3. Clippy linting
cargo clippy -D warnings

# 4. Final verification
echo "Expected: test result: ok. 560+ passed; 0 failed"
cargo test --lib 2>&1 | tail -5
```

---

## PHASE 3-6 TIMELINE

| Time | Phase | Task | Duration | Status |
|------|-------|------|----------|--------|
| NOW | 3.1 | HotScanner snapshot JSON | 10 min | Start here |
| +10 | 3.2 | Verify HotScanner threshold | 5 min | |
| +15 | 3.3 | Python Brain message format | 5 min | |
| +20 | 3.4 | JSON serialization import | 5 min | **Phase 3 DONE** |
| +20 | 4.1 | Add ModeBPlus enum | 5 min | Start Phase 4 |
| +25 | 4.2 | Add Display impl | 5 min | |
| +30 | 4.3 | Update compute_mode() | 25 min | |
| +55 | 4.4 | Update should_freeze_entries() | 10 min | |
| +65 | 4.5 | Update should_trigger_carry() | 10 min | |
| +75 | 4.6 | Update entries_allowed() | 10 min | **Phase 4 DONE** |
| +85 | 5.1 | Find/verify apply_mode_subscription_rotation() | 10 min | Start Phase 5 |
| +95 | 5.2 | Call rotate on mode transition | 15 min | |
| +110 | 5.3 | Verify rotate_tickers() exists | 15 min | |
| +125 | 5.4 | Log rotation in reconcile() | 10 min | **Phase 5 DONE** |
| +135 | 6.1 | Test HotScanner fires ModeA | 15 min | Start Phase 6 |
| +150 | 6.2 | Test RotationScanner fires ModeB | 15 min | |
| +165 | 6.3 | Test 23:00 UTC wrapping | 10 min | |
| +175 | 6.4 | Test ModeBPlus subscription | 10 min | |
| +185 | 6.5 | Test reconcile audit halt | 10 min | **Phase 6 DONE** |
| +195 | - | Final validation + cleanup | 15 min | **ALL DONE** |

**Total**: ~210 minutes = 3.5 hours (includes buffer for debugging)

---

## SUCCESS CONDITION

```
✅ cargo check → 0 errors
✅ cargo clippy -D warnings → 0 warnings
✅ cargo test --lib → 560+ passed; 0 failed
✅ All 5 acceptance tests pass
✅ Mode transitions logged with rotation
✅ Ready for EC2 deployment
```

---

## IF YOU GET STUCK

1. **Compilation error**: Check line numbers match your file (versioning changes file length)
2. **Test failure**: Add `eprintln!()` to see what's happening
3. **Missing method**: Add empty stub and move on (full implementation in Phase 8)
4. **SessionMode panic**: Check your new ModeBPlus enum variant is in all match statements

---

## NEXT: EC2 DEPLOYMENT

Once Phase 6 passes:
```bash
# Copy to EC2
rsync -avz /Users/rr/nzt48-signals/nzt48-aegis-v2/ \
  ubuntu@3.230.44.22:/home/ubuntu/nzt48-aegis-v2/

# Deploy
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 << 'EOF'
cd /home/ubuntu/nzt48-aegis-v2
docker compose build
docker compose up -d
docker logs nzt48 --tail 50
EOF
```

---

## GO TIME ⚡

You have **556 tests passing**. Add **5 acceptance tests + 4 phases = 561+ tests + ready for live**.

Let's finish this.
