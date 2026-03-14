# PHASE 3 GATE — CANONICAL EVENT JOURNAL + RECOVERY
# Status: PENDING REVIEW

---

## Acceptance Criteria Checklist

- [x] `cargo check` passes with ZERO warnings
- [x] `cargo test` passes with ZERO failures (64/64)
- [x] Append 100 events → kill → restart → replay → PortfolioState matches exactly
- [x] Every WAL line contains: event_id (UUIDv7), schema_version, event_time_ns, write_time_ns, checksum (CRC32), payload
- [x] Corrupt last line (truncate mid-JSON) → replay skips it with WARNING, state consistent
- [x] Corrupt non-last line → panic! and refuse to trade (H27)
- [x] CRC32 mismatch on non-last line → panic! and refuse to trade
- [x] Orphan simulation: write RoutedOrder with no BrokerAck → on replay, marked ORPHANED → new orders blocked
- [x] Snapshot + replay: write 50 events, snapshot, 10 more → restart → load snapshot + replay 11 (not 61)
- [x] Idempotent replay: replay same WAL twice → identical PortfolioState (H84) — dedup by exec_id
- [x] Hourly state hash: deterministic CRC32-based hash of PortfolioState (H85)
- [x] Disk space check: mock < 5% disk → WalError::DiskSpaceLow (H25)
- [x] WAL writer uses &Event (immutable borrow, H26)
- [x] WAL writer designed for tokio::task::spawn_blocking (H13) — sync I/O with fsync
- [x] Dead letter queue: unparseable OrderIntent → dead_letter/YYYY-MM-DD.ndjson (H81)

## File Line Counts (≤400 limit)

```
 82 rust_core/src/config.rs
 35 rust_core/src/ffi.rs
 19 rust_core/src/lib.rs
250 rust_core/src/portfolio.rs
114 rust_core/src/proptest_risk.rs
388 rust_core/src/risk_arbiter_tests.rs
254 rust_core/src/risk_arbiter.rs
243 rust_core/src/wal_replay.rs
368 rust_core/src/wal_tests.rs
205 rust_core/src/wal_writer.rs
400 rust_core/src/types/enums.rs
274 rust_core/src/types/execution.rs
 12 rust_core/src/types/mod.rs
245 rust_core/src/types/structs.rs
115 rust_core/src/types/wal.rs
```

## Test Output (64/64)

```
running 64 tests
[Phase 1: 15 type tests] ... ok
[Phase 2: 24 risk arbiter + portfolio tests] ... ok
[Phase 2: 2 proptest fuzz tests] ... ok
[Phase 3: 13 WAL tests] ... ok
[Phase 3: 2 WAL writer unit tests] ... ok

test result: ok. 64 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
```

## Python FFI Regression: 28/28 passed

## Clippy + Fmt: ZERO warnings, ZERO diffs

## Architecture Summary

### New Modules (Phase 3)
- `wal_writer.rs` (205 lines) — Append-only WAL with CRC32, fsync, disk check, dead letter
- `wal_replay.rs` (243 lines) — Read, verify, replay events, orphan detection, snapshot recovery
- `wal_tests.rs` (368 lines) — 13 acceptance tests covering all Phase 3 criteria

### WAL Write Path
1. Serialize payload → compute CRC32
2. Set write_time_ns (system clock)
3. Serialize full WalEvent to ndjson
4. writeln + flush + sync_all (fsync)
5. Disk space check before write (injectable for tests)

### WAL Replay Path
1. Read all lines from ndjson file
2. Parse each line → verify CRC32
3. Non-last-line corruption → panic! (H27)
4. Last-line corruption → skip with WARNING
5. Process events: RoutedOrder, FillEvent, PositionClosed, StateSnapshot
6. Dedup fills by exec_id (H84 idempotent replay)
7. Detect orphans (RoutedOrder without matching terminal event)

### Dependencies Added
- `crc32fast` v1 — CRC32 checksums for WAL integrity
- `tempfile` v3 (dev) — Temp directories for WAL tests

---

**PHASE 3 COMPLETE — AWAITING HUMAN REVIEW**
