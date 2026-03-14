# AEGIS V2 — REBUILD MANIFEST
# Tracks progress across all 10 build phases.

---

## Phase 0 — SPEC LOCK
- **Status:** IN PROGRESS
- **Gate:** docs/checkpoints/PHASE_0_GATE.md
- **Deliverables:**
  - [x] Directory structure created
  - [ ] docs/00_CANONICAL_RULES.md (55 rules, 48 constants)
  - [ ] docs/01_DATA_CONTRACTS.md (7 structs, 2 newtypes, 10 enums, 2 WAL types)
  - [ ] docs/02_STATE_MACHINE.md (15 states, orphan recovery, phantom fills)
  - [ ] docs/03_ACCEPTANCE_TESTS.md (tests for all 10 phases)
  - [ ] docs/SYSTEM_STRUCTURE.md (complete architecture)
  - [ ] config/config.toml (static configuration)
  - [ ] config/initial_universe.toml (1,000 tickers)
  - [ ] config/uk_holidays.toml (2026-2027)
  - [ ] .claudeignore
  - [ ] rust-toolchain.toml

## Phase 1 — EXECUTIONER SKELETON + FFI
- **Status:** NOT STARTED
- **Gate:** docs/checkpoints/PHASE_1_GATE.md
- **Deliverables:**
  - [ ] Cargo.toml workspace root
  - [ ] rust_core crate with all #[pyclass] types
  - [ ] PyO3 FFI bridge
  - [ ] Python can import Rust types
  - [ ] Round-trip tests for all 7 struct types
  - [ ] NaN sanitization tests
  - [ ] maturin develop builds successfully
  - [ ] docs/BLIND_SPOTS.md

## Phase 2 — EXECUTIONER RISK VAULT
- **Status:** NOT STARTED
- **Gate:** docs/checkpoints/PHASE_2_GATE.md
- **Deliverables:**
  - [ ] PortfolioState implementation
  - [ ] RiskArbiter 4-state hierarchy
  - [ ] ISA safety invariants
  - [ ] All 22 risk checks
  - [ ] proptest fuzzing

## Phase 3 — CANONICAL EVENT JOURNAL + RECOVERY
- **Status:** NOT STARTED
- **Gate:** docs/checkpoints/PHASE_3_GATE.md
- **Deliverables:**
  - [ ] WAL writer (ndjson, CRC32, UUIDv7)
  - [ ] Crash recovery via WAL replay
  - [ ] Snapshot + event pattern
  - [ ] Orphan detection on replay
  - [ ] Corruption handling

## Phase 4 — BROKER INTERFACE + PAPER ADAPTER
- **Status:** NOT STARTED
- **Gate:** docs/checkpoints/PHASE_4_GATE.md
- **Deliverables:**
  - [ ] BrokerAdapter async trait
  - [ ] PaperBroker implementation
  - [ ] Full order lifecycle
  - [ ] Partial fill handling
  - [ ] Phantom fill handling
  - [ ] Rate limiter

## Phase 5 — SINGULAR CANONICAL EXIT ENGINE
- **Status:** NOT STARTED
- **Gate:** docs/checkpoints/PHASE_5_GATE.md
- **Deliverables:**
  - [ ] Exit Engine with priority hierarchy
  - [ ] Chandelier 5-rung profit ladder
  - [ ] Same-tick collision resolution
  - [ ] HALT override
  - [ ] Phased EOD flatten
  - [ ] ExitStrategy trait

## Phase 6A — UNIVERSE: RUST DATA ROUTING
- **Status:** NOT STARTED
- **Gate:** docs/checkpoints/PHASE_6A_GATE.md
- **Deliverables:**
  - [ ] 1,000-ticker data routing
  - [ ] Dynamic rotation manager (100 free lines)
  - [ ] Vanguard/Apex classification
  - [ ] Amihud + ASER filters
  - [ ] Crossbeam channel + monitoring

## Phase 6B — QUANTUM BRAIN: PYTHON STRATEGIES
- **Status:** NOT STARTED
- **Gate:** docs/checkpoints/PHASE_6B_GATE.md
- **Deliverables:**
  - [ ] Vanguard Sniper (momentum, pure function)
  - [ ] Apex Scout (RVOL anomaly, pure function)
  - [ ] Moreira-Muir volatility scaling
  - [ ] Yang-Zhang estimator
  - [ ] Pure function verification

## Phase 6C — KELLY SIZING + FFI WIRING
- **Status:** NOT STARTED
- **Gate:** docs/checkpoints/PHASE_6C_GATE.md
- **Deliverables:**
  - [ ] 13-factor Kelly sizing
  - [ ] Full pipeline end-to-end wiring
  - [ ] GIL isolation verified
  - [ ] Batch FFI verified
  - [ ] Backpressure monitoring

## Phase 7 — REPLAY HARNESS + PERFECT WIRING
- **Status:** NOT STARTED
- **Gate:** docs/checkpoints/PHASE_7_GATE.md
- **Deliverables:**
  - [ ] Synthetic tick data generator (1M+ ticks)
  - [ ] Day replay at 10x speed
  - [ ] Zero disconnected signal paths
  - [ ] Deterministic replay
  - [ ] Memory stability test

## Phase 8 — PAPER ENGINE BOOTSTRAP
- **Status:** NOT STARTED
- **Gate:** docs/checkpoints/PHASE_8_GATE.md
- **Deliverables:**
  - [ ] IB Gateway connection (port 4002)
  - [ ] Dynamic rotation manager live
  - [ ] Position reconciliation (5-min + fill-triggered)
  - [ ] 8-step startup sequence
  - [ ] Restart recovery test
  - [ ] systemd service file

## Phase 9 — OUROBOROS NIGHTLY ANALYTICS
- **Status:** NOT STARTED
- **Gate:** docs/checkpoints/PHASE_9_GATE.md
- **Deliverables:**
  - [ ] 10-step nightly pipeline
  - [ ] Bayesian win rate + DSR
  - [ ] Kelly Accelerator
  - [ ] Exit Ladder Calibration
  - [ ] Alpha Decay detection (IC tracking)
  - [ ] Universe reclassification
  - [ ] Walk-forward validation
  - [ ] Cold Start Protocol
