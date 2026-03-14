# PHASE 0 GATE — SPEC LOCK
# Date: 2026-03-08
# Status: READY FOR REVIEW

---

## 1. DIRECTORY TREE

```
nzt48-aegis-v2/
├── config/
│   ├── config.toml
│   ├── initial_universe.toml
│   ├── parameter_history/
│   └── uk_holidays.toml
├── dead_letter/
├── docs/
│   ├── 00_CANONICAL_RULES.md
│   ├── 01_DATA_CONTRACTS.md
│   ├── 02_STATE_MACHINE.md
│   ├── 03_ACCEPTANCE_TESTS.md
│   ├── checkpoints/
│   ├── REBUILD_MANIFEST.md
│   └── SYSTEM_STRUCTURE.md
├── events/
├── EXECUTION_STATE.md
├── ouroboros/
│   └── tests/
├── python_brain/
│   └── brain/
│       ├── sizing/
│       ├── strategies/
│       └── tests/
├── rust_core/
│   ├── src/
│   └── tests/
├── rust-toolchain.toml
├── scripts/
└── .claudeignore
```

---

## 2. FILE EXISTENCE + LINE COUNTS

```
$ wc -l docs/*.md
     265 docs/00_CANONICAL_RULES.md
     450 docs/01_DATA_CONTRACTS.md
     457 docs/02_STATE_MACHINE.md
     356 docs/03_ACCEPTANCE_TESTS.md
     139 docs/REBUILD_MANIFEST.md
     564 docs/SYSTEM_STRUCTURE.md
    2231 total
```

All 6 spec files exist and are non-empty.

---

## 3. CANONICAL RULES VERIFICATION

**00_CANONICAL_RULES.md** contains 12 rule sections:
1. Signal Filtering (5 rules: confidence floor, outlier win cap, gap detection, erroneous tick, price spike filter)
2. Position Limits (5 rules: max positions, portfolio heat, sector heat, cash buffer, ISA annual limit)
3. ISA Safety Invariants (3 rules: no short selling, no margin, inverse mutual exclusion)
4. Risk Arbiter State Transitions (4 states: HALT, FLATTEN, REDUCE, NORMAL with triggers and recovery)
5. Entry Timing Rules (4 rules: time cutoff, auction avoidance, spread veto, velocity check)
6. Position Sizing / Kelly (6 rules: fraction cap, clamp, vol drag, Bayesian shrinkage, drawdown scaling, slippage)
7. Order Execution Rules (8 rules: marketable limit, MTL exits, fractional shares, tick size, TIF, stop trigger, OUTSIDE_RTH)
8. Data Integrity Rules (8 rules: stale threshold, tick dropping, queue depth, backpressure, clock source, synthetic halt, alpha decay, reverse split)
9. WAL Integrity Rules (8 rules: WAL is God, schema version, event IDs, dual timestamps, checksum, disk space, corruption, immutable borrows)
10. Reconciliation Rules (5 rules: interval, mismatch action, orphan resolution, commission tracking, FIFO accounting)
11. Reject-to-HALT Escalation (5 rules: reject-to-HALT, consecutive loss, pacing violation, error 1100, error 1102)
12. Constants Registry (48 constants in valid TOML)

**Total canonical rules: 55. Total constants: 48.**
All rules have exact numeric thresholds and specific enforcement actions.

---

## 4. DATA CONTRACTS VERIFICATION

**01_DATA_CONTRACTS.md** defines:

### Newtypes (2):
- TickerId(u32) — interned ticker ID
- OrderId(uuid::Uuid) — UUIDv7 event/order ID

### Enums (10):
1. Direction { Long, Short }
2. StrategyId { VanguardSniper, ApexScout }
3. VetoReason { 23 variants with typed payloads }
4. RiskRegime { Normal=0, Reduce=1, Flatten=2, Halt=3 }
5. BrokerAckStatus { Accepted, Rejected, PendingCancel, Cancelled }
6. ExitReason { 7 variants }
7. ExitPriority { SignalReversal=1 through HaltFlatten=5 }
8. ExitOrderType { MarketSell, MarketToLimit, LimitAtStop }
9. OrderState { 15 variants matching state machine }
10. WalEventType { 9 variants }

### Core Structs (7):
1. MarketTick — 7 fields (timestamp_ns, bid, ask, last, volume, ticker_id, recv_timestamp_ns)
2. OrderIntent — 6 fields (ticker_id, side, confidence, strategy, kelly_fraction, features)
3. RiskDecision — 5 fields (approved, adjusted_size, reason, regime, decision_timestamp_ns)
4. FillEvent — 8 fields (order_id, ticker_id, filled_qty, remaining_qty, price, exec_id, timestamp_ns, commission)
5. PositionState — 12 fields (ticker_id, qty, avg_entry, unrealized_pnl, realized_pnl, highest_high, stop_price, trailing_rung, entry_timestamp_ns, total_commission, state, origin_order_id)
6. BrokerAck — 5 fields (order_id, status, ibkr_order_id, timestamp_ns, message)
7. ExitSignal — 6 fields (ticker_id, reason, priority, order_type, position_order_id, limit_price)

### WAL Types (2):
- WalEvent (envelope with event_id, schema_version, timestamps, checksum, payload)
- WalPayload (9 variants: RoutedOrder, BrokerAck, FillEvent, ExitSignal, PositionClosed, RiskStateChange, OrphanResolved, StateSnapshot, SystemReady)

**Total: 21 defined types (7 structs + 2 newtypes + 10 enums + 2 WAL types).**
All fields listed with exact Rust types. All #[pyclass] derive macros specified.

---

## 5. STATE MACHINE VERIFICATION

**02_STATE_MACHINE.md** maps:

### Order Lifecycle States (15):
1. IntentGenerated
2. RiskChecked
3. Rejected (terminal)
4. WalWritten
5. Submitted
6. BrokerRejected (terminal)
7. Acknowledged
8. Orphaned
9. PartiallyFilled
10. Filled
11. ExitRegistered
12. ExitTriggered
13. ExitOrderSubmitted
14. ExitFilled
15. PositionClosed (terminal)

### Includes:
- Full ASCII state diagram with all transitions
- State descriptions with entry/exit conditions
- Orphan Recovery Protocol (4 resolution cases: filled, open→cancel, never_received, already_cancelled)
- Phantom Fill Handling (H55)
- Partial Fill Accumulation with VWAP math
- Startup Recovery Sequence (8 steps)
- Exit Priority Collision Matrix (all 10 pair combinations)
- RiskArbiter State Machine (4 states with transition rules)

---

## 6. ACCEPTANCE TESTS VERIFICATION

**03_ACCEPTANCE_TESTS.md** defines tests for ALL phases:
- Phase 0: SPEC LOCK (15 criteria)
- Phase 1: EXECUTIONER SKELETON + FFI (15 criteria)
- Phase 2: EXECUTIONER RISK VAULT (20 criteria)
- Phase 3: CANONICAL EVENT JOURNAL + RECOVERY (14 criteria)
- Phase 4: BROKER INTERFACE + PAPER ADAPTER (16 criteria)
- Phase 5: SINGULAR CANONICAL EXIT ENGINE (16 criteria)
- Phase 6A: UNIVERSE RUST DATA ROUTING (16 criteria)
- Phase 6B: QUANTUM BRAIN PYTHON STRATEGIES (14 criteria)
- Phase 6C: KELLY SIZING + FFI WIRING (14 criteria)
- Phase 7: REPLAY HARNESS + PERFECT WIRING (15 criteria)
- Phase 8: PAPER ENGINE BOOTSTRAP (20 criteria)
- Phase 9: OUROBOROS NIGHTLY ANALYTICS (17 criteria)
- CROSS-PHASE INVARIANTS (11 criteria)

**All 10 phases + cross-phase invariants covered.**

---

## 7. CITATION ARTIFACT CHECK

```
$ grep -rn '\[cite_start\]' docs/ | grep -v 'ZERO citation'
(no output — zero real citation artifacts)
```

---

## 8. TOML VALIDITY

```
$ python3 -c "import toml; [toml.load(f) for f in [...]]"
config/config.toml: VALID TOML
config/uk_holidays.toml: VALID TOML
config/initial_universe.toml: VALID TOML
```

Constants registry in 00_CANONICAL_RULES.md contains valid TOML block (48 constants across 12 sections).

---

## 9. ADDITIONAL FILES CREATED

| File | Purpose | Lines |
|------|---------|-------|
| .claudeignore | Excludes target/, data/, events/, etc. (H89) | 7 |
| rust-toolchain.toml | Locks Rust stable + rustfmt + clippy (H92) | 3 |
| config/config.toml | All 48 constants + crucible overrides | 128 |
| config/uk_holidays.toml | 2026-2027 UK bank holidays | 27 |
| config/initial_universe.toml | 40+ core LSE leveraged ETPs | 220 |
| EXECUTION_STATE.md | Phase tracking for context recovery | 15 |
| docs/REBUILD_MANIFEST.md | Progress tracking for all 10 phases | 139 |
| docs/SYSTEM_STRUCTURE.md | Complete system architecture | 564 |

---

## 10. KNOWN RISKS

1. **Initial universe is incomplete**: Only ~40 tickers manually curated. Phase 8 bootstrap will use reqContractDetails to discover the remaining ~960 LSE leveraged ETPs.
2. **UK holiday dates**: 2026-2027 dates are calculated but should be verified against gov.uk when available.
3. **Rust toolchain**: Using `stable` channel rather than pinning exact version (e.g., `1.77.0`). Will pin exact version in Phase 1 when we confirm the version that builds with PyO3.

---

## PHASE 0 ACCEPTANCE CRITERIA STATUS

- [x] `docs/00_CANONICAL_RULES.md` exists and contains >= 23 named rules with exact thresholds (55 rules)
- [x] `docs/01_DATA_CONTRACTS.md` exists and defines ALL 7 core structs with every field and Rust type
- [x] `docs/01_DATA_CONTRACTS.md` defines ALL 10 enums
- [x] `docs/02_STATE_MACHINE.md` exists and maps ALL 15 order lifecycle states with transitions
- [x] `docs/02_STATE_MACHINE.md` includes orphan recovery protocol with 4 resolution cases
- [x] `docs/02_STATE_MACHINE.md` includes phantom fill handling (H55)
- [x] `docs/02_STATE_MACHINE.md` includes partial fill accumulation with VWAP math
- [x] `docs/02_STATE_MACHINE.md` includes startup recovery sequence (8 steps)
- [x] `docs/02_STATE_MACHINE.md` includes exit priority collision matrix
- [x] `docs/02_STATE_MACHINE.md` includes RiskArbiter state machine with transition rules
- [x] `docs/03_ACCEPTANCE_TESTS.md` exists and defines tests for ALL phases (0-9)
- [x] Directory structure shows correct layout
- [x] All spec files are non-empty (2,231 lines total across 6 docs)
- [x] ZERO citation artifacts in any file
- [x] Constants registry contains valid TOML
