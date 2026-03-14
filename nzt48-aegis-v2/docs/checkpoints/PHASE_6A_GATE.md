# PHASE 6A GATE — UNIVERSE: RUST DATA ROUTING
# Status: PENDING REVIEW

---

## Acceptance Criteria Checklist

- [x] `cargo check` passes with ZERO warnings
- [x] `cargo test` passes with ZERO failures (121/121)
- [x] Feed 1,000 synthetic tickers → Vanguard (300) gets continuous delivery
- [x] Feed 1,000 synthetic tickers → Apex (700) gets 60-second OHLCV snapshots
- [x] NO tick is routed to both Vanguard AND Apex paths simultaneously
- [x] Amihud illiquidity filter: inject illiquid ticker (ILLIQ > threshold) → filtered out
- [x] ASER filter: inject ticker with spread > 0.5% → filtered out
- [x] TickerId interning: "QQQ3.L" → TickerId(0), no String comparison in hot path (H01)
- [x] Crossbeam channel: bounded at 50,000 capacity
- [x] Oldest-tick dropping: fill channel to capacity → oldest dropped, newest preserved
- [x] Drop rate monitoring: >100 drops/sec → REDUCE escalation
- [x] Queue depth monitoring: 40,000 → REDUCE, 50,000 → HALT
- [x] Apex snapshot interval: 60s drift-resistant check (H18)
- [x] reqMktData pacing: 10ms spacing between requests (H42)
- [x] Synthetic halt detection: no ticks for 30s on specific ticker (H122)
- [x] Reverse split detection: >500% overnight price move → HALT ticker (H76)
- [x] Erroneous tick filter: >5% deviation from 1s EMA → filtered (H77)

## File Line Counts (≤400 limit)

```
316 rust_core/src/universe.rs
142 rust_core/src/channel.rs
385 rust_core/src/universe_tests.rs
 32 rust_core/src/lib.rs
```

## Test Output (121/121)

```
running 121 tests
[Phase 1: 15 type tests] ... ok
[Phase 2: 24 risk arbiter + portfolio tests] ... ok
[Phase 2: 2 proptest fuzz tests] ... ok
[Phase 3: 13 WAL tests] ... ok
[Phase 3: 2 WAL writer unit tests] ... ok
[Phase 4: 4 broker unit tests] ... ok
[Phase 4: 18 broker acceptance tests] ... ok
[Phase 4: 8 broker extended tests] ... ok
[Phase 5: 18 exit engine tests] ... ok
[Phase 6A: 17 universe + channel tests] ... ok

test result: ok. 121 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
```

## Python FFI Regression: 28/28 passed

## Clippy + Fmt: ZERO warnings, ZERO diffs

## Architecture Summary

### New Modules (Phase 6A)
- `universe.rs` (316 lines) — TickerIntern, UniverseClass, Universe routing + 5 data filters
- `channel.rs` (142 lines) — Crossbeam bounded channel with oldest-dropping + health monitoring
- `universe_tests.rs` (385 lines) — 17 acceptance tests

### Modified Modules
- `lib.rs` — Added `pub mod channel;`, `pub mod universe;`, `#[cfg(test)] mod universe_tests;`
- `Cargo.toml` — Added `crossbeam-channel = "0.5"` dependency

### Universe Design
- **TickerIntern**: String → TickerId(u32) hash map. Reverse lookup. Idempotent. Zero String ops in hot path.
- **UniverseClass**: Vanguard (continuous) or Apex (60s snapshots). Mutually exclusive.
- **route_tick()**: Hot-path function. Filters in order: halted → Amihud → ASER → synthetic halt → reverse split → erroneous tick → route by class.

### Data Filters
| Filter | Threshold | Action |
|--------|-----------|--------|
| Amihud Illiquidity | > 1.0 (configurable) | FilterReason::AmihudIlliquid |
| ASER (spread) | > 0.5% | FilterReason::AserSpreadTooWide |
| Erroneous Tick (H77) | > 5% from 1s EMA | FilterReason::ErroneousTick |
| Reverse Split (H76) | > 500% overnight move | FilterReason::ReverseSplit + halt |
| Synthetic Halt (H122) | No ticks for 30s | FilterReason::SyntheticHalt |

### Channel Design
- **Crossbeam bounded**: Configurable capacity (default 50,000)
- **Oldest-first dropping**: When full, drop oldest tick, accept newest
- **Health monitoring**: Drop rate per second + queue depth thresholds
- **Escalation**: 40k depth → REDUCE, 50k → HALT, >100 drops/sec → REDUCE
- **Batch receive**: recv_batch(max) for 200-tick batches

---

**PHASE 6A COMPLETE — AWAITING HUMAN REVIEW**
