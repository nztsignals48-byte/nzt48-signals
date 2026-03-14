# PHASE 4 GATE — BROKER INTERFACE + PAPER ADAPTER
# Status: PENDING REVIEW

---

## Acceptance Criteria Checklist

- [x] `cargo check` passes with ZERO warnings
- [x] `cargo test` passes with ZERO failures (86/86)
- [x] BrokerAdapter trait defined with: submit_order, cancel_order, request_positions, request_open_orders, heartbeat, is_connected
- [x] PaperBroker implements BrokerAdapter
- [x] Full lifecycle test: submit → ack → fill → position updated in PortfolioState
- [x] Partial fill test: order for 100 shares, fill 37, then 63. Final qty=100, price=VWAP
- [x] Duplicate submission: same order_id twice → second rejected
- [x] Heartbeat timeout: mock failure for 60s → HALT triggered
- [x] PaperBroker simulates configurable latency (50-200ms)
- [x] PaperBroker generates valid UUIDv7 exec_ids for fills
- [x] PaperBroker supports random partial fills (configurable)
- [x] Rate limiter: token bucket at 50 msgs/sec (H16)
- [x] Exponential backoff test: disconnect → reconnect attempts at 1s, 2s, 4s, 8s (H17)
- [x] Client ID isolation: Executioner=100, Ouroboros=200 (H41)
- [x] nextValidId persistence in WAL (H47)
- [x] PendingCancel state: cancel sent → wait for Cancelled ack (H54)
- [x] Phantom fill: cancel sent, fill arrives 50ms later → accept position (H55)

## File Line Counts (≤400 limit)

```
228 rust_core/src/broker.rs
223 rust_core/src/broker_tests.rs
227 rust_core/src/broker_tests_ext.rs
 82 rust_core/src/config.rs
 35 rust_core/src/ffi.rs
 25 rust_core/src/lib.rs
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

## Test Output (86/86)

```
running 86 tests
[Phase 1: 15 type tests] ... ok
[Phase 2: 24 risk arbiter + portfolio tests] ... ok
[Phase 2: 2 proptest fuzz tests] ... ok
[Phase 3: 13 WAL tests] ... ok
[Phase 3: 2 WAL writer unit tests] ... ok
[Phase 4: 4 broker unit tests] ... ok
[Phase 4: 18 broker acceptance tests] ... ok
[Phase 4: 8 broker extended tests] ... ok

test result: ok. 86 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
```

## Python FFI Regression: 28/28 passed

## Clippy + Fmt: ZERO warnings, ZERO diffs

## Architecture Summary

### New Modules (Phase 4)
- `broker.rs` (228 lines) — BrokerAdapter trait, BrokerError, BrokerEvent, TokenBucket rate limiter, BackoffState
- `paper_broker.rs` (397 lines) — PaperBroker implementing BrokerAdapter with configurable latency, partial fills, phantom fills
- `broker_tests.rs` (223 lines) — Tests 1-8: lifecycle, partial fill, duplicate, heartbeat, latency, UUIDv7, partial fills, rate limiter
- `broker_tests_ext.rs` (227 lines) — Tests 9-17: backoff, client ID, nextValidId, PendingCancel, phantom fill, disconnect, portfolio update, WAL event, split qty

### Modified Modules
- `types/wal.rs` — Added `NextValidId { id: u64 }` variant to WalPayload (H47)
- `wal_replay.rs` — Handle NextValidId in replay (no-op)
- `proptest_risk.rs` — Fixed assertion for negative kelly inputs
- `risk_arbiter_tests.rs` — Fixed clippy field_reassign_with_default

### BrokerAdapter Trait
```
submit_order(order_id, ticker_id, qty, limit_price) → Result<(), BrokerError>
cancel_order(order_id) → Result<(), BrokerError>
request_positions() → Result<Vec<BrokerPosition>, BrokerError>
request_open_orders() → Result<Vec<BrokerOpenOrder>, BrokerError>
heartbeat() → Result<(), BrokerError>
is_connected() → bool
drain_events() → Vec<BrokerEvent>
next_valid_id() → u64
client_id() → u32
```

### PaperBroker Features
- Configurable latency range (50-200ms default)
- Token bucket rate limiter at 50 msgs/sec
- Partial fill splitting (configurable chunks)
- Duplicate order rejection by order_id
- Heartbeat timeout detection (60s)
- PendingCancel → Cancelled state machine (H54)
- Phantom fill injection for race condition testing (H55)
- Client ID isolation (Executioner=100, Ouroboros=200)
- nextValidId tracking with persistence via WAL events

### Token Bucket Rate Limiter (H16)
- Capacity: 50 tokens (configurable)
- Refill rate: 50 tokens/second
- Nanosecond-precision time tracking
- Zero-allocation refill logic

### Exponential Backoff (H17)
- Base delay: 1000ms
- Exponential: 1s → 2s → 4s → 8s → 16s → ...
- Configurable max delay cap
- Reset on successful reconnection

---

**PHASE 4 COMPLETE — AWAITING HUMAN REVIEW**
