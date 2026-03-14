# PHASE 5 GATE — SINGULAR CANONICAL EXIT ENGINE
# Status: PENDING REVIEW

---

## Acceptance Criteria Checklist

- [x] `cargo check` passes with ZERO warnings
- [x] `cargo test` passes with ZERO failures (104/104)
- [x] Exactly ONE exit engine (structural — all exits through ExitEngine::evaluate)
- [x] Exit priority ordering: HALT > HardStop > Chandelier > EOD > Signal
- [x] Same-tick collision: hard stop + Chandelier → hard stop wins (suppressed_count >= 1)
- [x] Same-tick collision: hard stop + EOD → hard stop wins (suppressed_count >= 1)
- [x] HALT override: ALL exits become MarketToLimit + IOC (H117, H69)
- [x] Chandelier 5-rung profit ladder (Le Beau 1999) with exact stop values verified
- [x] Stop ratchet: stop can NEVER decrease (H68)
- [x] EOD flatten at 16:25 London (59100s from midnight)
- [x] Shadow stops: internal Rust stops, NOT IBKR trailing stops (H67)
- [x] TIF rules: entry=DAY, emergency=IOC (H69)
- [x] MTL for emergency exits (H117)
- [x] highest_high persisted in PositionState for crash recovery (H70)
- [x] Price spike filter: detects artifact spikes vs real crashes (H71)
- [x] Commission in targets: ev_after_commission helper (H73)
- [x] ExitStrategy trait: hot-swappable exit math (H72) — custom FixedTrailStrategy test
- [x] Signal reversal exit fires correctly
- [x] Triple collision (HALT + hard stop + EOD + signal) → HALT wins, 2+ suppressed

## File Line Counts (≤400 limit)

```
302 rust_core/src/exit_engine.rs
322 rust_core/src/exit_engine_tests.rs
 28 rust_core/src/lib.rs
228 rust_core/src/broker.rs
223 rust_core/src/broker_tests.rs
227 rust_core/src/broker_tests_ext.rs
 82 rust_core/src/config.rs
 35 rust_core/src/ffi.rs
397 rust_core/src/paper_broker.rs
250 rust_core/src/portfolio.rs
118 rust_core/src/proptest_risk.rs
390 rust_core/src/risk_arbiter_tests.rs
254 rust_core/src/risk_arbiter.rs
244 rust_core/src/wal_replay.rs
368 rust_core/src/wal_tests.rs
205 rust_core/src/wal_writer.rs
400 rust_core/src/types/enums.rs
274 rust_core/src/types/execution.rs
 12 rust_core/src/types/mod.rs
245 rust_core/src/types/structs.rs
118 rust_core/src/types/wal.rs
```

## Test Output (104/104)

```
running 104 tests
[Phase 1: 15 type tests] ... ok
[Phase 2: 24 risk arbiter + portfolio tests] ... ok
[Phase 2: 2 proptest fuzz tests] ... ok
[Phase 3: 13 WAL tests] ... ok
[Phase 3: 2 WAL writer unit tests] ... ok
[Phase 4: 4 broker unit tests] ... ok
[Phase 4: 18 broker acceptance tests] ... ok
[Phase 4: 8 broker extended tests] ... ok
[Phase 5: 18 exit engine tests] ... ok

test result: ok. 104 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
```

## Python FFI Regression: 28/28 passed

## Clippy + Fmt: ZERO warnings, ZERO diffs

## Architecture Summary

### New Modules (Phase 5)
- `exit_engine.rs` (302 lines) — ExitStrategy trait, ChandelierStrategy, ExitEngine with priority collision resolution
- `exit_engine_tests.rs` (322 lines) — 18 acceptance tests covering all exit criteria

### Modified Modules
- `lib.rs` — Added `pub mod exit_engine;` and `#[cfg(test)] mod exit_engine_tests;`

### ExitEngine Design
- **Singular authority**: ONE ExitEngine evaluates ALL exit conditions per tick
- **Priority collision resolution**: All firing exits collected, sorted by priority, highest wins
- **Suppression tracking**: suppressed_count reports how many lower-priority exits were overridden
- **HALT override**: When HALT active, winner becomes MarketToLimit + IOC regardless

### Chandelier 5-Rung Profit Ladder (Le Beau 1999)
| Rung | Threshold (ATR from entry) | Stop |
|------|---------------------------|------|
| 0 | < 0.5 ATR | Initial stop (unchanged) |
| 1 | 0.5 ATR | Breakeven (entry price) |
| 2 | 1.0 ATR | Entry + 0.25 ATR |
| 3 | 1.5 ATR | Entry + 0.5 ATR |
| 4 | 2.0 ATR | Entry + 1.0 ATR |
| 5 | 3.0 ATR | Trail 1.5 ATR from high |

### ExitStrategy Trait (H72)
```rust
pub trait ExitStrategy: Send {
    fn compute_stop(&self, pos: &PositionState, high: f64, atr: f64) -> f64;
    fn compute_rung(&self, pos: &PositionState, high: f64, atr: f64) -> u8;
}
```
Hot-swappable: tested with custom FixedTrailStrategy (5% trailing from high).

### Key Invariants Verified
- H67: Shadow stops (internal Rust, not IBKR)
- H68: Stop ratchet (never decreases)
- H69: TIF rules (DAY normal, IOC emergency)
- H70: highest_high persisted for crash recovery
- H71: Price spike filter (artifact vs real crash)
- H72: ExitStrategy trait swappable
- H73: Commission in targets (ev_after_commission)
- H117: MTL for emergency exits

---

**PHASE 5 COMPLETE — AWAITING HUMAN REVIEW**
