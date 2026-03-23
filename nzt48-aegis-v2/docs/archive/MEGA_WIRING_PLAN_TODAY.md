# MEGA WIRING PLAN — v30 COMPLETE IMPLEMENTATION TODAY

**Status**: APPROVED FOR IMMEDIATE EXECUTION
**Date**: 2026-03-13
**Deadline**: End of today
**Expected Hours**: 8-12 hours continuous

---

## PHASE 0 — CRITICAL BLOCKERS (7.5 hours)

Must fix BEFORE Phase 8 or system has silent data corruption + audit trail loss.

### 0.1 — fs::write() sync_all() in ouroboros_loader.rs (30 min)

**File**: `rust_core/src/ouroboros_loader.rs` (lines 198, 222, 262)

**Problem**: `std::fs::write()` doesn't call `fsync()`. On power loss between write() and fsync(), TOML files corrupt silently.

**Fix Pattern**:
```rust
use std::fs::File;
use std::io::Write;

fn write_with_sync(path: &Path, content: &str) -> Result<()> {
    let mut f = File::create(path)?;
    f.write_all(content.as_bytes())?;
    f.sync_all()?;  // CRITICAL — ensure data on disk
    Ok(())
}
```

**Gate**: `grep sync_all rust_core/src/ouroboros_loader.rs` returns 3+ lines

---

### 0.2 — Reconciliation Audit Log in engine.rs (2 hours)

**File**: `rust_core/src/engine.rs` (reconcile method + new struct)

**Problem**: When reconciliation diverges, system auto-recovers without audit trail. Violates Blood Oath guarantee.

**Fix**:
1. Add new struct in types.rs:
```rust
pub struct ReconcileAuditLog {
    pub persistent_mismatch: Option<Instant>,
    pub last_mismatches: Vec<(u64, String)>,  // timestamp_ns, reason
}
```

2. In engine.rs:
```rust
if reconcile_result.has_divergence {
    self.reconcile_log.persistent_mismatch = Some(now);
    self.reconcile_log.last_mismatches.push((self.now_ns, format!("{:?}", mismatch)));

    // LOCK: require manual clear before trading resumes
    self.arbiter.regime = Regime::HALT;
}
```

3. Add manual unlock method:
```rust
pub fn manual_clear_reconcile_halt(&mut self) -> Result<()> {
    self.reconcile_log.persistent_mismatch = None;
    self.arbiter.regime = Regime::NORMAL;
    Ok(())
}
```

**Gate**: `grep ReconcileAuditLog rust_core/src/types.rs` + `grep manual_clear rust_core/src/engine.rs`

---

### 0.3 — Hayashi-Yoshida Async Correlation (4 hours)

**File**: New file `rust_core/src/hayashi_yoshida.rs` (~400 lines)

**Problem**: Pearson ρ on async ticks (ES 100ms, LSE 5s) biases toward zero. HY covariance works on overlapping intervals without sync.

**Implementation** (academic source: Hayashi & Yoshida 2005):

```rust
pub fn hayashi_yoshida_covariance(
    ticks_a: Vec<(u64, f64)>,  // timestamps, prices
    ticks_b: Vec<(u64, f64)>,
) -> f64 {
    // Bucket both to 5-second intervals
    let bucket_ns = 5_000_000_000u64;
    let buckets_a = bucket_ticks(&ticks_a, bucket_ns);
    let buckets_b = bucket_ticks(&ticks_b, bucket_ns);

    // Compute returns within overlapping buckets
    let mut hy_cov = 0.0;
    let mut count = 0;

    for (bucket_a, ret_a) in &buckets_a {
        for (bucket_b, ret_b) in &buckets_b {
            // Overlapping interval check
            if bucket_a == bucket_b {
                hy_cov += ret_a * ret_b;
                count += 1;
            }
        }
    }

    if count > 0 {
        hy_cov / count as f64
    } else {
        0.0
    }
}
```

**Unit test**:
```rust
#[test]
fn test_hy_vs_pearson_on_async() {
    // Generate ES (100ms) and LSE (5s) ticks
    // Verify H-Y ≥ Pearson on real async data
}
```

**Gate**: `grep hayashi_yoshida rust_core/src/lib.rs` + test passes

---

### 0.4 — cli.py atexit Cleanup ✅ DONE

Already verified. No changes needed.

---

## PHASE 1 — 5-MODE CLOCK FIX (1 hour)

**Critical bug** from AEGIS_SELF_ANALYSIS_TRIAGE.md [INFRA-01]:

Mode boundary at 23:00 UTC wrapping midnight is WRONG.

**File**: `rust_core/src/clock.rs` (line 51 approximately)

**Current (WRONG)**:
```rust
t if !(MODE_A_START..DARK_START).contains(&t) => TradingMode::Dark,
```
This makes 23:00-01:00 UTC = DARK (wrong, should be ModeA)

**Fix**:
```rust
// ModeA wraps midnight: 23:00 UTC (82800) - 08:00 UTC (28800)
if london_time_secs >= 82800 || london_time_secs < 28800 {
    return TradingMode::ModeA;
}
```

**Verification**:
- 23:00 UTC (82800) → ModeA ✓
- 00:00 UTC (0) → ModeA ✓
- 01:00 UTC (3600) → ModeA ✓
- 08:00 UTC (28800) → Auction ✓

---

## PHASE 2 — ROTATION SCANNER WIRING (2 hours)

**Currently**: RotationScanner module exists (scanner.rs) but is completely unwired — no field in Engine, never instantiated, never called.

**Target**: Wire RotationScanner to fire during MODE B (08:00-16:30 UTC) on Apex tickers.

### 2.1 — Add field to Engine struct

**File**: `rust_core/src/engine.rs` (around line 334)

```rust
pub struct Engine {
    // ... existing fields ...

    /// P13: RotationScanner for sector rotation during MODE B
    pub rotation_scanner: RotationScanner,
}
```

### 2.2 — Initialize in Engine::new()

```rust
rotation_scanner: RotationScanner::new(0.005, 10),  // 0.5% threshold, top 10
```

### 2.3 — Wire to MODE B tick processing

**File**: `rust_core/src/engine.rs` (process_tick method, around line 750)

```rust
// MODE B: Route Apex tickers to RotationScanner (60s snapshots)
if matches!(self.current_mode, TradingMode::ModeB)
    && self.universe.is_apex(tid)
    && self.should_emit_snapshot(tid) {

    // Feed 60s OHLCV snapshot to RotationScanner
    let close = tick.last;
    self.rotation_scanner.on_snapshot(tid, close);

    // Every 5 tickers or 60s elapsed, recompute sectors
    if self.apex_snapshots_buffered >= 5 || elapsed_since_last_recompute > 60s {
        self.rotation_scanner.recompute_sectors();
        let candidates = self.rotation_scanner.rotation_candidates(self.now_ns);

        for candidate in candidates {
            eprintln!(
                "ROTATION_SIGNAL: ticker={}, score={:.1}, relative_strength={:.2}%",
                candidate.ticker_id.0, candidate.score,
                candidate.direction_bias * 100.0
            );

            // Buffer for Python Brain evaluation (same as HotScanner)
            self.apex_snapshots.entry(tid).or_insert_with(Vec::new)
                .push((self.now_ns, tick.clone()));
        }
    }
}
```

### 2.4 — Register sectors for each Apex ticker

**File**: `rust_core/src/engine.rs` (startup, after Universe loads)

```rust
// Map tickers to sectors (from universe_classification.toml or hardcoded)
for (ticker_id, state) in &self.universe.tickers {
    if state.classification == UniverseClass::Apex {
        let sector = self.sector_map.get(&ticker_id).copied().unwrap_or("Technology");
        self.rotation_scanner.register_ticker(*ticker_id, sector);
    }
}
```

**Gate**:
- `grep "pub rotation_scanner" rust_core/src/engine.rs` ✓
- `grep "rotation_scanner.on_snapshot" rust_core/src/engine.rs` ✓
- `grep "rotation_candidates" rust_core/src/engine.rs` ✓

---

## PHASE 3 — HOTSCANNER SCORING FIX (1 hour)

**Currently**: HotScanner is wired BUT its scores are never used — buffering happens but scoring doesn't drive signals.

**File**: `rust_core/src/engine.rs` (process_apex_tick method, line 1419)

**Current issue**: `if let Some(candidate) = self.process_apex_tick()` checks score threshold, but only buffers. Never sends to Python Brain.

**Fix**:
```rust
// MODE A: HotScanner scores Apex tickers
if matches!(self.current_mode, TradingMode::ModeA) && self.universe.is_apex(tid) {
    if let Some(candidate) = self.process_apex_tick(&tick) {
        // Score passed threshold (30) → buffer for 60s snapshot evaluation
        eprintln!(
            "HOT_SCORE: ticker={}, score={:.1}, confidence={:.2}",
            tid.0, candidate.score, candidate.direction_bias
        );

        // CRITICAL: Only send to Python if score > 70 (high conviction)
        if candidate.score > 70.0 {
            // Send apex_snapshot message to Python Brain
            let msg = serde_json::json!({
                "type": "apex_snapshot",
                "ticker_id": tid.0,
                "snapshots": [{
                    "open": tick.last,
                    "high": tick.last,
                    "low": tick.last,
                    "close": tick.last,
                    "volume": tick.volume,
                }],
            });
            self.python_bridge.send_json(msg)?;
        }
    }
}
```

**Gate**: `grep "score > 70" rust_core/src/engine.rs` ✓

---

## PHASE 4 — SESSION MANAGER BUG: MODEBPLUS (1 hour)

**Issue**: session_manager.rs has ModeA/ModeB/Auction/Carry/Dark, but v17 plan shows MODE B+ (14:30-16:30 with 20 US lines).

**Fix**: Add MODE B+ to SessionMode enum

**File**: `rust_core/src/session_manager.rs`

```rust
pub enum SessionMode {
    Dark,
    ModeA,      // 00:00-07:50 (Asian)
    ModeB,      // 08:00-14:30 (European only)
    ModeBPlus,  // 14:30-16:30 (80 LSE + 20 US)  ← NEW
    Auction,
    Carry,
}

pub fn compute_mode(london_time_secs: u32, has_open_positions: bool) -> SessionMode {
    // Asian session: 00:00-07:50 London
    if london_time_secs < 7 * 3600 + 50 * 60 {
        if has_open_positions {
            return SessionMode::Carry;
        }
        return SessionMode::ModeA;
    }

    // LSE opening auction: 07:50-08:00.
    if london_time_secs < 8 * 3600 {
        return SessionMode::Auction;
    }

    // European continuous trading: 08:00-14:30.
    if london_time_secs < 14 * 3600 + 30 * 60 {
        return SessionMode::ModeB;
    }

    // MODE B+: 14:30-16:30 (US overlap, 80 LSE + 20 US lines)
    if london_time_secs < 16 * 3600 + 30 * 60 {
        return SessionMode::ModeBPlus;
    }

    // LSE closing auction: 16:30-16:35.
    if london_time_secs < 16 * 3600 + 35 * 60 {
        return SessionMode::Auction;
    }

    // Post-close carry: 16:35-23:45
    if london_time_secs < 23 * 3600 + 45 * 60 {
        if has_open_positions {
            return SessionMode::Carry;
        }
        return SessionMode::Dark;
    }

    // Ouroboros maintenance window: 23:45-00:00.
    SessionMode::Dark
}

pub fn entries_allowed(&self) -> bool {
    matches!(self.current_mode, SessionMode::ModeB | SessionMode::ModeBPlus)
}

pub fn entries_frozen(&self) -> bool {
    matches!(
        self.current_mode,
        SessionMode::Auction | SessionMode::ModeC | SessionMode::Dark
    )
}
```

**Gate**: `grep "ModeBPlus" rust_core/src/session_manager.rs` ✓

---

## PHASE 5 — SUBSCRIPTIONMANAGER WIRING (1.5 hours)

**File**: `rust_core/src/subscription_manager.rs`

**Current**: Exists but is completely dead code — `rotate_tickers()` is never called.

**Wire into engine reconcile method** to rotate subscriptions at mode boundaries:

```rust
// engine.rs reconcile method (around line 1700)
if let Some(transition) = self.session_manager.update(london_time_secs, self.has_open_positions(), now_ns) {
    eprintln!("MODE_TRANSITION: {} → {}", transition.from, transition.to);

    // Rotate IBKR subscriptions at mode boundaries
    match transition.to {
        SessionMode::ModeA => {
            // Cancel all European lines, subscribe to TSE/HKEX/ASX (91 lines)
            self.subscription_manager.rotate_mode(
                &[(12, "LSE"), (14, "XETRA"), (6, "Euronext")],  // Cancel
                &[(20, "TSE"), (20, "HKEX"), (20, "ASX"), (15, "NZX")],  // Subscribe
            )?;
        },
        SessionMode::ModeB => {
            // Cancel all Asian lines, subscribe to European (32 lines)
            self.subscription_manager.rotate_mode(
                &[(20, "TSE"), (20, "HKEX"), (20, "ASX"), (15, "NZX")],  // Cancel
                &[(12, "LSE"), (14, "XETRA"), (6, "Euronext")],  // Subscribe
            )?;
        },
        SessionMode::ModeBPlus => {
            // ADD 20 US lines without cancelling LSE (80 LSE + 20 US = 100 max)
            self.subscription_manager.add_us_lines(20)?;
        },
        SessionMode::Auction => {
            // No subscription changes, just block entries
        },
        SessionMode::Carry => {
            // Keep carry positions' underlyings subscribed, cancel scanning
        },
        SessionMode::Dark => {
            // Cancel all market data, only Ouroboros analytics
        },
    }
}
```

**Gate**: `grep "rotate_mode\|add_us_lines" rust_core/src/engine.rs` ✓

---

## PHASE 6 — ACCEPTANCE TESTS (1 hour)

Add tests to verify all wiring:

**File**: `rust_core/src/engine_tests.rs`

```rust
#[test]
fn test_mode_a_hotscanner_fires_on_apex() {
    // Set current_mode = ModeA, send tick to Apex ticker
    // Verify HotScanner.on_tick() is called
    // Verify score > 70 triggers Python message
}

#[test]
fn test_mode_b_rotation_scanner_fires_on_apex() {
    // Set current_mode = ModeB, register sector, send 60s snapshot
    // Verify RotationScanner.on_snapshot() is called
    // Verify candidates are ranked by score
}

#[test]
fn test_mode_transition_23_00_utc_wrapping() {
    // london_time_secs = 82800 (23:00 UTC) → ModeA ✓
    // london_time_secs = 0 (00:00 UTC) → ModeA ✓
    // london_time_secs = 3600 (01:00 UTC) → ModeA ✓
    // london_time_secs = 28800 (08:00 UTC) → Auction ✓
}

#[test]
fn test_mode_b_plus_subscription_rotation() {
    // At 14:30 UTC: transition ModeB → ModeBPlus
    // Verify add_us_lines(20) is called
    // Verify total lines ≤ 100
}

#[test]
fn test_reconcile_audit_log_halts_on_divergence() {
    // Inject reconciliation divergence
    // Verify regime locked at HALT
    // Verify manual_clear_reconcile_halt() required to resume
}
```

**Gate**: All 5 tests pass with `cargo test --lib`

---

## BUILD GATES (Every phase)

```bash
cd /Users/rr/nzt48-signals/nzt48-aegis-v2/rust_core
cargo check 2>&1 | grep -E "error|warning"  # Must be ZERO
cargo clippy -- -D warnings 2>&1 | grep -E "error|warning"  # Must be ZERO
cargo test --lib --no-default-features 2>&1 | tail -5  # Must show "test result: ok"
```

**Gate Success**: `test result: ok. 556+ passed; 0 failed`

---

## SUMMARY: WHAT THE SYSTEM WILL BE (LAYMAN'S TERMS)

After this mega-wiring plan completes today, your trading robot will be:

### **Before (Current, 556 tests)**
- ✅ Receives real-time market data from IBKR (5-second bars)
- ✅ Runs VanguardSniper (momentum-following strategy) 24/5
- ✅ Executes buy/sell orders with Kelly position sizing
- ✅ Persists trades to audit log (WAL)
- ✅ Runs nightly learning (Ouroboros) to calibrate Kelly
- ❌ **But**: Only trades 12 LSE ETPs during LSE hours (8h/day)
- ❌ **And**: HotScanner exists but scores never fire
- ❌ **And**: RotationScanner exists but is completely unused
- ❌ **And**: Doesn't trade US markets or Asian markets
- ❌ **And**: Has silent data corruption risks on crash
- ❌ **And**: Can't recover from reconciliation bugs without manual intervention

### **After (Today's Wiring, 560+ tests)**
- ✅ Still does everything above
- ✅ **Now also**: Trades 22 hours/day across 6 exchanges:
  - **00:00-07:50 UTC** (ModeA): Scans TSE/HKEX/ASX/NZX (92 tickers) with HotScanner
  - **08:00-14:30 UTC** (ModeB): Scans LSE/XETRA/Euronext (32 tickers) with VanguardSniper + RotationScanner
  - **14:30-16:30 UTC** (ModeBPlus): Adds 20 US NYSE/NASDAQ tickers (overlaps with LSE)
  - **16:35-23:45 UTC** (Carry): Holds overnight positions, freezes stops at session close to prevent gap hunts
  - **21:00-23:00 UTC** (Dark): Ouroboros nightly calibration

- ✅ **HotScanner now fires**: Detects volatility-momentum breakouts in Asian markets, sends high-conviction signals (score > 70) to Python Brain for ApexScout refinement

- ✅ **RotationScanner now fires**: Detects sector leadership changes in European markets, ranks sectors by relative strength, emits highest-conviction rotation signals during ModeB

- ✅ **Data integrity guaranteed**: All TOML writes now fsync to disk, won't corrupt on power loss

- ✅ **Audit trail locked**: Reconciliation divergences lock trading, require manual approval to unlock (no silent recovery)

- ✅ **Correlation math fixed**: Async tick timing (ES 100ms vs LSE 5s) no longer biases correlation calculations

- ✅ **No more mode boundary bugs**: 23:00 UTC wrapping midnight now correctly enters ModeA (Japan markets accessible 00:00-06:00 UTC)

### **What This Means Concretely**

Instead of:
> "Robot trades £10k starting capital on 12 LSE leveraged ETPs, 8 hours/day, hoping for 0.3% daily"

You'll have:
> "Robot trades £10k across 92 different assets on 6 exchanges, 22 hours/day, using 2 different strategies (momentum + sector rotation), with ironclad data integrity and audit trails. Targets 0.5-1% daily on compounding, with hard stops if anything breaks."

The robot will make **3-5x more trading opportunities** (22 hours vs 8 hours), on **8x more tickers** (92 vs 12), with **2 independent signal sources** (HotScanner for volatility + RotationScanner for sectors) instead of just 1.

**Risk**: System is more complex now (more places to break). But today's wiring adds safeguards (reconciliation locks, data fsync, mode boundaries) so it fails safely if something goes wrong.

---

## TIMELINE

- **Phase 0 (Blockers)**: 7.5 hours
- **Phase 1 (Mode clock)**: 1 hour
- **Phase 2 (RotationScanner)**: 2 hours
- **Phase 3 (HotScanner scoring)**: 1 hour
- **Phase 4 (ModeBPlus)**: 1 hour
- **Phase 5 (Subscription rotation)**: 1.5 hours
- **Phase 6 (Tests)**: 1 hour

**Total: 14.5 hours**

Start: 2026-03-13 16:00 UTC
Target end: 2026-03-14 06:30 UTC (overnight blitz)

---

**Status**: READY FOR IMMEDIATE EXECUTION
**Next**: Execute Phase 0.1 (fs::write sync_all)
